#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
上传数据集 · 解析 / 校验 / 聚合（展示层与 AI 分析共用）

职责
----
* 接收用户上传的 CSV，自动识别并映射到标准五列：
  user_id / item_id / category_id / behavior_type / dt
* 行为取值归一化（pv/fav/cart/buy，兼容数字编码 1/2/3/4 与中文）
* 校验失败抛 DatasetError，页面据此给出可读的中文提示
* 聚合出漏斗、时段与用户分群画像（只传聚合结果给大模型）

本模块不依赖 Streamlit；`load_sample_from_db` 供「示例数据」按钮使用。
"""

from __future__ import annotations

import hashlib
import io
import re
import sys

import numpy as np
import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STANDARD_COLS = ["user_id", "item_id", "category_id", "behavior_type", "dt"]
VALID_BEHAVIORS = ["pv", "fav", "cart", "buy"]

# 标准列名 ← 常见候选名（匹配时不区分大小写/下划线）
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "user_id": ("userid", "uid", "用户id", "买家id"),
    "item_id": ("itemid", "item", "goodsid", "productid", "商品id"),
    "category_id": ("categoryid", "cid", "cateid", "cat", "类目id", "商品类目id"),
    "behavior_type": ("behaviortype", "behavior", "action", "eventtype", "行为类型"),
    "dt": ("timestamp", "time", "datetime", "date", "日期", "时间", "时间戳"),
}

# 行为取值归一化映射（兼容字符串与数字编码）
BEHAVIOR_MAP = {
    "pv": "pv", "click": "pv", "view": "pv", "浏览": "pv", "1": "pv",
    "fav": "fav", "favorite": "fav", "collect": "fav", "收藏": "fav", "2": "fav",
    "cart": "cart", "addcart": "cart", "加购": "cart", "购物车": "cart", "3": "cart",
    "buy": "buy", "purchase": "buy", "购买": "buy", "成交": "buy", "4": "buy",
}

STAGE_LABELS = ["浏览", "收藏", "加购", "购买"]


class DatasetError(ValueError):
    """数据集解析/校验失败，message 面向最终用户。"""


def _norm_col(name: str) -> str:
    return re.sub(r"[_\s]+", "", str(name)).lower()


def detect_columns(df: pd.DataFrame) -> dict[str, str]:
    """
    自动识别标准列。返回 {标准列名: 实际列名}；识别不到的键不在结果里。
    """
    norm = {_norm_col(c): c for c in df.columns}
    found: dict[str, str] = {}
    for std, aliases in COLUMN_ALIASES.items():
        for alias in (std, *aliases):
            if alias in norm:
                found[std] = norm[alias]
                break
    return found


def normalize_behavior(series: pd.Series) -> pd.Series:
    """行为取值归一化为 pv/fav/cart/buy，无法识别的置为 NaN。"""
    s = series.astype(str).str.strip().str.lower()
    mapped = s.map(BEHAVIOR_MAP)
    # 数字编码（int 列）：1浏览 2收藏 3加购 4购买（UserBehavior 原始约定）
    numeric_mask = pd.to_numeric(series, errors="coerce").notna()
    if numeric_mask.any():
        enc = pd.to_numeric(series[numeric_mask], errors="coerce") \
            .astype("Int64").astype(str).map(BEHAVIOR_MAP)
        mapped.loc[numeric_mask] = enc
    return mapped


def load_uploaded(file) -> pd.DataFrame:
    """
    解析上传的 CSV → 标准五列 DataFrame。
    兼容 utf-8 / gbk 编码；识别不到必需列或行为值时抛 DatasetError。
    """
    if isinstance(file, (bytes, bytearray)):
        file = io.BytesIO(file)
    elif isinstance(file, str):
        file = io.StringIO(file)
    try:
        df = pd.read_csv(file)
    except UnicodeDecodeError:
        file.seek(0)
        df = pd.read_csv(file, encoding="gbk")
    except Exception as exc:                                        # noqa: BLE001
        raise DatasetError(f"CSV 解析失败：{exc}") from exc

    df.columns = [str(c).strip() for c in df.columns]
    if df.empty:
        raise DatasetError("上传的文件没有数据行")

    found = detect_columns(df)
    missing = [c for c in ("user_id", "behavior_type", "dt") if c not in found]
    if missing:
        raise DatasetError(
            "未能识别必需列：" + "、".join(missing)
            + f"。\n当前列名：{', '.join(df.columns)}"
            + "。\n需要包含 用户ID、行为类型、时间 列（列名支持常见别名，如 uid/行为类型/timestamp）。")

    ts = df[found["dt"]]
    out = pd.DataFrame({
        "user_id": df[found["user_id"]],
        "behavior_type": normalize_behavior(df[found["behavior_type"]]),
        "dt": pd.to_datetime(ts, errors="coerce",
                             unit="s" if pd.api.types.is_numeric_dtype(ts) else None),
        "item_id": df[found["item_id"]] if "item_id" in found else np.nan,
        "category_id": df[found["category_id"]] if "category_id" in found else np.nan,
    })
    before = len(out)
    out = out.dropna(subset=["user_id", "behavior_type", "dt"])
    if out.empty:
        raise DatasetError("没有可用的行为记录：请检查行为类型取值（应含浏览/收藏/加购/购买）"
                           "与时间列格式（如 2017-11-25 或 Unix 时间戳）")
    out.attrs["dropped_rows"] = before - len(out)
    out["behavior_type"] = out["behavior_type"].astype(str)
    for col in ("user_id", "item_id", "category_id"):
        if out[col].notna().any():
            num = pd.to_numeric(out[col], errors="coerce")
            out[col] = num.astype("Int64").fillna(out[col])
    return out[STANDARD_COLS].reset_index(drop=True)


def fingerprint(df: pd.DataFrame) -> str:
    """数据指纹：同一数据集的 AI 结果可复用缓存。"""
    h = hashlib.md5()
    h.update(f"{len(df)}|{list(df.columns)}".encode())
    h.update(df.head(200).astype(str).to_csv(index=False).encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- 聚合统计
def _rate(part: int, whole: int) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def funnel_stats(df: pd.DataFrame) -> dict:
    """
    四阶段漏斗（浏览→收藏→加购→购买）：
    返回事件量与用户量两种口径的人数/次数、层间转化率与整体转化率。
    """
    total_users = df["user_id"].nunique()
    ev = df["behavior_type"].value_counts().to_dict()
    users_by = {b: df.loc[df["behavior_type"] == b, "user_id"].nunique()
                for b in VALID_BEHAVIORS}
    stages_users = [users_by[b] for b in VALID_BEHAVIORS]
    buyers = users_by["buy"]
    return {
        "总用户数": total_users,
        "总事件数": int(len(df)),
        "日期范围": [str(df["dt"].min().date()), str(df["dt"].max().date())],
        "事件量漏斗": {lbl: int(ev.get(b, 0))
                       for lbl, b in zip(STAGE_LABELS, VALID_BEHAVIORS)},
        "用户量漏斗": dict(zip(STAGE_LABELS, stages_users)),
        "浏览→收藏转化率%": _rate(stages_users[1], stages_users[0]),
        "收藏→加购转化率%": _rate(stages_users[2], stages_users[1]),
        "加购→购买转化率%": _rate(stages_users[3], stages_users[2]),
        "整体购买转化率%": _rate(buyers, total_users),
        "浏览→购买整体转化率%": _rate(buyers, stages_users[0]),
    }


def hourly_stats(df: pd.DataFrame) -> dict[int, dict[str, int]]:
    """分时段行为量（0-23 时 × 四类行为），供 AI 判断推送时机。"""
    d = df.assign(hour=df["dt"].dt.hour)
    by = {}
    for b in VALID_BEHAVIORS:
        by[b] = d[d["behavior_type"] == b].groupby("hour").size()
    return {h: {b: int(by[b].get(h, 0)) for b in VALID_BEHAVIORS}
            for h in range(24)}


def load_category_mapping() -> dict[str, str]:
    """类目编号→中文品类名（MySQL category_mapping 表）；不可用时返回空 dict。

    原始数据集类目 ID 已脱敏，真实名称不存在 —— 映射表由 DeepSeek 依据
    行为画像推测命名（src/build_category_map.py 生成），可人工修正。
    """
    try:
        from db import get_engine
        with get_engine().connect() as c:
            df = pd.read_sql("SELECT category_id, category_name FROM category_mapping", c)
        return {str(r["category_id"]): str(r["category_name"])
                for _, r in df.iterrows()}
    except Exception:                                               # noqa: BLE001
        return {}


def top_categories(df: pd.DataFrame, n: int = 10,
                   name_map: dict[str, str] | None = None) -> list[dict]:
    """头部类目：行为量与购买率（供 AI 识别引流/成交类目并给出推送建议）。
    提供类目映射表时附带中文类目名称字段。"""
    d = df.dropna(subset=["category_id"])
    if d.empty:
        return []
    g = d.groupby("category_id")["behavior_type"].agg(
        pv=lambda s: int((s == "pv").sum()), buy=lambda s: int((s == "buy").sum()),
        total="size").sort_values("total", ascending=False).head(n)
    out = []
    for i, r in g.iterrows():
        item = {"类目ID": str(i), "行为总量": int(r["total"]),
                "购买次数": int(r["buy"]),
                "购买率%": _rate(int(r["buy"]), int(r["total"]))}
        if name_map:
            item["类目名称"] = name_map.get(str(i), f"类目{i}")
        out.append(item)
    return out


def segment_users(df: pd.DataFrame, k: int = 4) -> tuple[pd.DataFrame, list[dict]]:
    """
    用户分群：行为特征 + 高价值评分（不含购买信息的分位加权），再 K-Means 聚成 k 层。
    返回 (用户级评分表, 群体画像摘要) —— 画像摘要交由大模型解读命名与定策略。
    """
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    from value_scorer import DEFAULT_THRESHOLD, build_features, score_users

    feat = build_features(df)
    buy_users = set(df.loc[df["behavior_type"] == "buy", "user_id"].unique())
    feat["has_buy"] = feat["user_id"].isin(buy_users).astype(int)
    scored = score_users(feat, DEFAULT_THRESHOLD)

    feats = ["events", "fav_cnt", "cart_cnt", "active_days"]
    X = StandardScaler().fit_transform(scored[feats].to_numpy(dtype=float))
    km = KMeans(n_clusters=min(k, max(2, len(scored) // 50)), random_state=42,
                n_init="auto")
    scored["cluster"] = km.fit_predict(X)

    g = scored.groupby("cluster").agg(
        users=("user_id", "size"),
        buy_rate=("has_buy", "mean"),
        events=("events", "mean"),
        fav_cnt=("fav_cnt", "mean"),
        cart_cnt=("cart_cnt", "mean"),
        active_days=("active_days", "mean"),
        value_score=("value_score", "mean"),
    ).sort_values("buy_rate", ascending=False)
    profiles = []
    for cid, r in g.iterrows():
        profiles.append({
            "群编号": int(cid), "用户数": int(r["users"]),
            "购买率%": round(r["buy_rate"] * 100, 2),
            "人均行为数": round(r["events"], 1),
            "人均收藏": round(r["fav_cnt"], 1), "人均加购": round(r["cart_cnt"], 1),
            "人均活跃天数": round(r["active_days"], 1),
            "平均价值评分": round(r["value_score"], 1),
        })
    return scored, profiles


def load_sample_from_db(limit: int = 300_000) -> pd.DataFrame:
    """示例数据：从 MySQL user_behavior 抽样，供无文件时演示全流程。"""
    from db import get_engine
    eng = get_engine()
    df = pd.read_sql(
        "SELECT user_id, item_id, category_id, behavior_type, dt "
        f"FROM user_behavior ORDER BY dt LIMIT {int(limit)}", eng)
    df["behavior_type"] = df["behavior_type"].str.strip().str.lower()
    df["dt"] = pd.to_datetime(df["dt"])
    return df[STANDARD_COLS]


if __name__ == "__main__":
    demo = pd.DataFrame({
        "uid": [1, 1, 2, 2, 3],
        "item": [10, 10, 11, 12, 13],
        "cid": [100, 100, 101, 101, 102],
        "action": ["pv", "buy", "pv", "cart", "fav"],
        "time": pd.to_datetime(["2017-11-25 09:00:00", "2017-11-25 09:05:00",
                                "2017-11-25 10:00:00", "2017-11-25 10:10:00",
                                "2017-11-25 11:00:00"]),
    })
    d = load_uploaded(demo.to_csv(index=False))
    print(d)
    print(funnel_stats(d))
    print(detect_columns(demo))
