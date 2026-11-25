#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
商品库模块 · 淘宝商品数据落地 MySQL（推送计划的"推什么"素材库）

表 products（taobao_funnel 库）：
    id            自增主键
    category_id   行为日志类目编号（关联 category_mapping）
    category_name 类目中文名（即搜索词）
    title         商品标题
    price         价格（元，解析失败为 NULL）
    sales         销量/付款人数原文（如 "2万+人付款"）
    shop          店铺名
    url           商品链接（md5 哈希唯一，防重复入库）
    source        来源标识（默认 taobao_search）
    crawled_at    抓取时间

数据来源：浏览器会话逐类目抓取淘宝搜索第一页（约 40 个商品/类目），
以 JSON 记录列表交给 insert_products() 入库；页面端支持导入同格式 JSON。
小批量学习用途，遵守网站 robots 与服务条款，不含验证码绕过逻辑。

直接运行自检：python src/product_catalog.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DDL_PRODUCTS = """
CREATE TABLE IF NOT EXISTS products (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    category_id   BIGINT            NULL,
    category_name VARCHAR(64)       NOT NULL,
    title         VARCHAR(512)      NOT NULL,
    price         DECIMAL(12, 2)    NULL,
    sales         VARCHAR(64)       NULL,
    shop          VARCHAR(256)      NULL,
    url           VARCHAR(1024)     NOT NULL,
    url_hash      CHAR(32)          NOT NULL,
    source        VARCHAR(32)       NOT NULL DEFAULT 'taobao_search',
    crawled_at    DATETIME          NOT NULL,
    UNIQUE KEY uk_url_hash (url_hash),
    KEY idx_category (category_name),
    KEY idx_cat_id (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def ensure_products_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(DDL_PRODUCTS))


# ---------------------------------------------------------------- 解析工具
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_price(raw) -> float | None:
    """从 "¥228.00" / "228" / 228.0 等取值中解析价格数字。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)
    m = _PRICE_RE.search(str(raw).replace(",", ""))
    return round(float(m.group(1)), 2) if m else None


def url_hash(url: str) -> str:
    return hashlib.md5(str(url).strip().encode("utf-8")).hexdigest()


def normalize_row(row: dict, category_name: str,
                  category_id=None, source: str = "taobao_search") -> dict | None:
    """把一条抓取记录规整为 products 行；缺标题或链接的丢弃。"""
    title = str(row.get("title") or row.get("标题") or "").strip()
    url = str(row.get("url") or row.get("链接") or row.get("detail_url") or "").strip()
    if not title or not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    return {
        "category_id": int(category_id) if category_id not in (None, "") else None,
        "category_name": category_name,
        "title": title[:500],
        "price": parse_price(row.get("price", row.get("价格"))),
        "sales": str(row.get("sales", row.get("销量", row.get("sales_text", "")))
                     or "")[:60],
        "shop": str(row.get("shop", row.get("店铺", row.get("shop_name", "")))
                    or "")[:250],
        "url": url[:1000],
        "url_hash": url_hash(url),
        "source": source,
        "crawled_at": datetime.now(),
    }


# ---------------------------------------------------------------- 写入
def insert_products(engine: Engine, rows: list[dict], category_name: str,
                    category_id=None, source: str = "taobao_search") -> dict:
    """
    批量入库（按 url 哈希去重，已存在的跳过）。
    rows 为浏览器抓取的原始 JSON 记录列表。
    返回 {received, valid, inserted, skipped}。
    """
    ensure_products_table(engine)
    norm, seen = [], set()
    for r in rows:
        item = normalize_row(r, category_name, category_id, source)
        if item and item["url_hash"] not in seen:
            seen.add(item["url_hash"])
            norm.append(item)
    if not norm:
        return {"received": len(rows), "valid": 0, "inserted": 0, "skipped": 0}

    ins = text("""
        INSERT INTO products
            (category_id, category_name, title, price, sales, shop,
             url, url_hash, source, crawled_at)
        VALUES (:category_id, :category_name, :title, :price, :sales, :shop,
                :url, :url_hash, :source, :crawled_at)
    """)
    inserted = skipped = 0
    with engine.begin() as conn:
        for item in norm:
            try:
                conn.execute(ins, item)
                inserted += 1
            except Exception:                                     # noqa: BLE001
                # 唯一键冲突 = 同链接商品已在库，视为跳过
                skipped += 1
    return {"received": len(rows), "valid": len(norm),
            "inserted": inserted, "skipped": skipped}


def import_json_file(engine: Engine, path_or_text, category_name: str,
                     category_id=None, source: str = "taobao_search") -> dict:
    """导入 JSON 文件路径或 JSON 字符串（列表或 {data|items|list: [...]}）。"""
    if isinstance(path_or_text, (str, bytes)) and "\n" not in str(path_or_text) \
            and str(path_or_text).strip().endswith(".json"):
        with open(path_or_text, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.loads(path_or_text)
    if isinstance(payload, dict):
        for k in ("data", "items", "list", "products"):
            if isinstance(payload.get(k), list):
                payload = payload[k]
                break
    if not isinstance(payload, list):
        raise ValueError("JSON 结构不符合预期：需要记录列表或含 data/items/list 的对象")
    return insert_products(engine, payload, category_name, category_id, source)


# ---------------------------------------------------------------- 查询
def catalog_stats(engine: Engine) -> dict:
    """商品库概况：总量、类目数、各来源最近抓取时间。"""
    with engine.connect() as conn:
        n, cats = conn.execute(text(
            "SELECT COUNT(*), COUNT(DISTINCT category_name) FROM products"
        )).fetchone()
        latest = conn.execute(text(
            "SELECT MAX(crawled_at) FROM products")).scalar()
    return {"商品总数": int(n or 0), "类目数": int(cats or 0),
            "最近抓取": pd.Timestamp(latest) if latest else None}


def list_by_category(engine: Engine, category_name: str | None = None,
                     keyword: str | None = None, order_by: str = "price",
                     limit: int = 500) -> pd.DataFrame:
    """商品查询：可按类目过滤、按关键词搜标题、按价格/销量排序。"""
    where, params = ["1=1"], {}
    if category_name:
        where.append("category_name = :cat")
        params["cat"] = category_name
    if keyword:
        where.append("(title LIKE :kw OR shop LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    order = {"price": "price ASC", "price_desc": "price DESC",
             "sales": "sales DESC", "new": "crawled_at DESC"}.get(order_by,
                                                                  "price ASC")
    sql = f"""SELECT id, category_name AS 类目, title AS 商品标题, price AS 价格,
                     sales AS 销量, shop AS 店铺, url AS 链接, crawled_at AS 抓取时间
              FROM products WHERE {' AND '.join(where)}
              ORDER BY {order} LIMIT :lim"""
    params["lim"] = int(limit)
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def category_summary(engine: Engine) -> pd.DataFrame:
    """每类目商品数与价格区间（商品库页类目选择的数据源）。"""
    with engine.connect() as conn:
        return pd.read_sql(text("""
            SELECT category_name AS 类目, COUNT(*) AS 商品数,
                   MIN(price) AS 最低价, MAX(price) AS 最高价,
                   ROUND(AVG(price), 2) AS 均价, MAX(crawled_at) AS 最近抓取
            FROM products GROUP BY category_name ORDER BY 商品数 DESC
        """), conn)


def category_options(engine: Engine) -> pd.DataFrame:
    """类目下拉选项：category_mapping 全量，按行为日志量降序（无日志则按编号）。"""
    sql = """
        SELECT c.category_id, c.category_name,
               (SELECT COUNT(*) FROM user_behavior b
                WHERE b.category_id = c.category_id) AS behavior_n
        FROM category_mapping c
        ORDER BY behavior_n DESC, c.category_id
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)


def delete_category(engine: Engine, category_name: str) -> int:
    """删除某类目全部商品（重新爬取前清库用）。返回删除行数。"""
    with engine.begin() as conn:
        res = conn.execute(text("DELETE FROM products WHERE category_name = :c"),
                           {"c": category_name})
        return res.rowcount


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    from db import get_engine
    eng = get_engine()
    ensure_products_table(eng)
    print("products 表就绪")
    print(catalog_stats(eng))
    demo = [{"title": "示例连衣裙 2026 新款", "price": "¥129.00",
             "sales": "1万+人付款", "shop": "示例旗舰店",
             "url": "https://item.taobao.com/item.htm?id=demo123"}]
    print("试写入:", insert_products(eng, demo, "自检测试"))
    print(list_by_category(eng, "自检测试"))
    print("清理:", delete_category(eng, "自检测试"), "行")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
