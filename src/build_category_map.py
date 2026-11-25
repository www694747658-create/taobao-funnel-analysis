#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
商品类目映射表构建器：DeepSeek 为每个类目编号推测中文名

背景：原始数据集（UserBehavior）为脱敏数据，category_id 只是一串编号，
数据集中不存在任何真实类目名。本脚本为每个类目提取行为画像
（浏览/收藏/加购/购买量、购买率、覆盖用户数等），交给 DeepSeek
推测该类目最可能的商品品类，并给出中文名称 —— 全部标注 is_speculative=1
（推测命名），后续可人工修正，修正后 is_speculative 置 0。

产出：
* MySQL 表 category_mapping(category_id PK, category_name, hint,
  is_speculative, updated_at)
* datasets/category_mapping.csv（同一份映射的 CSV 导出）

支持断点续跑：已命名的类目自动跳过，重跑只补缺失项。
用法：python src/build_category_map.py [--batch 100] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_engine  # noqa: E402
from llm_client import LLMError, llm_ready, chat_json  # noqa: E402

OUT_CSV = ROOT / "datasets" / "category_mapping.csv"
BATCH = 100

DDL = """
CREATE TABLE IF NOT EXISTS category_mapping (
    category_id    BIGINT       PRIMARY KEY,
    category_name  VARCHAR(64)  NOT NULL,
    hint           VARCHAR(255) NOT NULL DEFAULT '',
    is_speculative TINYINT      NOT NULL DEFAULT 1,
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4
"""

SCHEMA = {"categories": [
    {"id": "与输入的类目ID一致（字符串）",
     "name": "2~6字中文品类名，如 连衣裙/手机/猫粮",
     "hint": "25字内：为什么这么判断（引用购买率等特征）"},
]}

SYSTEM = ("你是电商数据分析专家。输入是一些商品类目的行为统计（类目ID已脱敏）。"
          "请根据每个类目的行为模式（浏览/收藏/加购/购买量、购买率、覆盖用户数）"
          "推测它最可能属于什么商品品类，给出简洁的中文品类名。"
          "只输出合法 JSON，结构为：" + str(SCHEMA))


def fetch_stats(engine) -> pd.DataFrame:
    """全表类目行为画像：一行一个类目。"""
    return pd.read_sql("""
        SELECT category_id,
               COUNT(*) AS total,
               SUM(behavior_type='pv')  AS pv,
               SUM(behavior_type='fav') AS fav,
               SUM(behavior_type='cart') AS cart,
               SUM(behavior_type='buy') AS buy,
               COUNT(DISTINCT user_id)  AS users
        FROM user_behavior
        GROUP BY category_id
        ORDER BY total DESC
    """, engine)


def ensure_table(engine) -> None:
    with engine.begin() as c:
        c.execute(text(DDL))


def named_ids(engine) -> set[str]:
    with engine.connect() as c:
        rows = c.execute(text("SELECT category_id FROM category_mapping")).fetchall()
    return {str(r[0]) for r in rows}


def upsert(engine, items: list[dict]) -> int:
    n = 0
    with engine.begin() as c:
        for it in items:
            try:
                cid = int(str(it.get("id", "")).strip())
                name = str(it.get("name", "")).strip()[:64]
                hint = str(it.get("hint", "")).strip()[:255]
            except (TypeError, ValueError):
                continue
            if not cid or not name:
                continue
            c.execute(text("""
                INSERT INTO category_mapping (category_id, category_name, hint, is_speculative)
                VALUES (:cid, :name, :hint, 1)
                ON DUPLICATE KEY UPDATE
                    category_name=:name, hint=:hint, is_speculative=1
            """), {"cid": cid, "name": name, "hint": hint})
            n += 1
    return n


def name_batch(rows: list[dict]) -> list[dict]:
    """一次 DeepSeek 调用为一批类目命名。"""
    user = ("以下是商品类目的行为统计 JSON（类目ID已脱敏）。请为每个类目推测中文品类名。\n\n"
            + str(rows))
    data = chat_json([{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": user}], max_tokens=4000)
    items = data.get("categories", data if isinstance(data, list) else [])
    return items if isinstance(items, list) else []


def build(batch_size: int = BATCH, limit: int | None = None) -> None:
    ok, hint = llm_ready()
    if not ok:
        print("未配置 DeepSeek key：", hint)
        sys.exit(1)

    engine = get_engine()
    ensure_table(engine)
    stats = fetch_stats(engine)
    done = named_ids(engine)
    todo = stats[~stats["category_id"].astype(str).isin(done)]
    if limit:
        todo = todo.head(limit)
    print(f"类目总数 {len(stats)}，已命名 {len(done)}，待命名 {len(todo)}")

    records = todo.to_dict("records")
    total_named = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        payload = [{
            "id": str(r["category_id"]), "total": int(r["total"]),
            "pv": int(r["pv"]), "fav": int(r["fav"]),
            "cart": int(r["cart"]), "buy": int(r["buy"]),
            "buy_rate%": round(int(r["buy"]) / max(1, int(r["total"])) * 100, 2),
            "users": int(r["users"]),
        } for r in batch]
        # 单批失败重试两次，仍失败则跳过该批（续跑可补）
        for attempt in range(3):
            try:
                items = name_batch(payload)
                n = upsert(engine, items)
                total_named += n
                print(f"[{i + len(batch)}/{len(records)}] 本批命名 {n} 个")
                break
            except LLMError as e:                                   # noqa: PERF203
                print(f"  批次 {i}~{i + len(batch)} 失败（第 {attempt + 1} 次）：{e}")
                if attempt == 2:
                    print("  跳过该批，稍后可重跑补齐")
                else:
                    time.sleep(5)
        time.sleep(1)

    # 导出 CSV
    with engine.connect() as c:
        df = pd.read_sql("SELECT category_id, category_name, hint, is_speculative "
                         "FROM category_mapping ORDER BY category_id", c)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"完成：共命名 {total_named} 个本轮新增；映射表 {len(df)} 条 → {OUT_CSV}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="DeepSeek 为类目编号推测中文名并建映射表")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 个未命名类目")
    args = ap.parse_args()
    build(args.batch, args.limit)
