#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
高价值用户评估器（纯 Pandas，可作用于任意同格式行为日志）

为什么独立于数据库的 user_profile.is_high_value？
    库内版 is_high_value = has_buy=1 AND rfm>=阈值，判定本身用了购买信息：
    1) 对已有用户做的是"事后描述"而非"事前识别"；
    2) 无法外推到尚无购买记录的新数据集；
    3) rfm_score 的 50% 权重来自购买频次分位，属标签泄漏。
本模块改用"不含购买信息"的行为特征打分，让"高价值组购买转化率更高"
成为可验证的命题（见 validate_value_method.py），并能直接评估新数据集。

评分体系（value_score 0~100，全部为全体用户内的相对分位加权）
    意向强度  intent_cnt = fav_cnt + cart_cnt        权重 40%  （收藏/加购是最接近成交的漏斗中层）
    活跃强度  events = 全部行为次数                  权重 25%
    活跃广度  active_days = 有行为的自然日数         权重 20%
    需求聚焦  top_cat_share = 最活跃类目行为占比     权重 15%  （专注单一类目=需求明确）

    is_high_value = value_score >= 阈值（默认 80，约对应前 10% 分位）

输入格式（与原始日志一致）
    user_id, item_id, category_id, behavior_type(pv/fav/cart/buy), dt

用法
    from value_scorer import evaluate_users
    user_df, summary = evaluate_users(behavior_df)
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VALID_BEHAVIORS = ["pv", "fav", "cart", "buy"]

# 特征权重与中文说明（前端展示与单用户判定共用同一套配置）
FEATURES = [
    ("intent_cnt", 0.40, "意向强度：收藏+加购次数"),
    ("events", 0.25, "活跃强度：全部行为次数"),
    ("active_days", 0.20, "活跃广度：有行为的自然日数"),
    ("top_cat_share", 0.15, "需求聚焦：最活跃类目行为占比"),
]
DEFAULT_THRESHOLD = 80.0


def _check_columns(df: pd.DataFrame) -> None:
    need = {"user_id", "item_id", "category_id", "behavior_type", "dt"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"缺少必需列：{', '.join(sorted(missing))}；"
                         f"需要格式：user_id, item_id, category_id, behavior_type, dt")


def build_features(behavior_df: pd.DataFrame) -> pd.DataFrame:
    """从行为日志计算用户级特征（不含任何购买信息）。"""
    _check_columns(behavior_df)
    d = behavior_df.copy()
    d["behavior_type"] = d["behavior_type"].astype(str).str.strip().str.lower()
    d = d[d["behavior_type"].isin(VALID_BEHAVIORS)]
    if d.empty:
        raise ValueError("数据中没有有效的行为类型（pv/fav/cart/buy）")
    d["dt"] = pd.to_datetime(d["dt"], errors="coerce")
    d = d.dropna(subset=["dt"])
    # 只保留自然日（与数据库口径一致，不做时刻级会话切分——新数据集不必有 session_id）
    d["day"] = d["dt"].dt.normalize()

    g = d.groupby("user_id")
    feat = g.agg(
        events=("behavior_type", "size"),
        fav_cnt=("behavior_type", lambda s: int((s == "fav").sum())),
        cart_cnt=("behavior_type", lambda s: int((s == "cart").sum())),
        active_days=("day", "nunique"),
    ).reset_index()

    # 类目聚焦度：用户最活跃类目的行为占比（排除类目缺失的行为）
    d_cat = d.dropna(subset=["category_id"])
    top_share = (d_cat.groupby(["user_id", "category_id"]).size()
                 .groupby("user_id").max())
    total = d_cat.groupby("user_id").size()
    feat["top_cat_share"] = feat["user_id"].map(
        (top_share / total).fillna(0.0)).round(4)
    feat["intent_cnt"] = feat["fav_cnt"] + feat["cart_cnt"]
    return feat


def score_users(feat_df: pd.DataFrame,
                threshold: float = DEFAULT_THRESHOLD) -> pd.DataFrame:
    """
    给特征表打分并判定高价值。
    分位口径与数据库版一致：平均秩 pct（并列取平均），保证分布稳健。
    """
    out = feat_df.copy()
    score = np.zeros(len(out))
    for col, w, _label in FEATURES:
        score += w * out[col].rank(method="average", pct=True).to_numpy()
    out["value_score"] = np.round(score * 100, 1)
    out["is_high_value"] = (out["value_score"] >= threshold).astype(int)
    return out.sort_values("value_score", ascending=False).reset_index(drop=True)


def evaluate_users(behavior_df: pd.DataFrame,
                   threshold: float = DEFAULT_THRESHOLD
                   ) -> tuple[pd.DataFrame, dict]:
    """
    一站式评估：行为日志 -> 用户评分表 + 汇总（含分组购买率对比）。

    返回
    ----
    user_df : 评分表（user_id / 特征 / value_score / is_high_value / has_buy）
    summary : dict —— 总用户数、高价值人数与占比、高/低组购买率与提升倍数
    """
    feat = build_features(behavior_df)
    user_df = score_users(feat, threshold)

    # 购买率（仅用于验证与汇报，不参与打分）
    buy_users = set(behavior_df.loc[behavior_df["behavior_type"] == "buy",
                                    "user_id"].unique())
    user_df["has_buy"] = user_df["user_id"].isin(buy_users).astype(int)

    hv = user_df[user_df["is_high_value"] == 1]
    rest = user_df[user_df["is_high_value"] == 0]
    rate_hv = hv["has_buy"].mean() * 100 if len(hv) else 0.0
    rate_rest = rest["has_buy"].mean() * 100 if len(rest) else 0.0
    lift = rate_hv / rate_rest if rate_rest > 0 else float("inf")

    summary = {
        "总用户数": int(len(user_df)),
        "高价值用户数": int(len(hv)),
        "高价值占比%": round(len(hv) / len(user_df) * 100, 2) if len(user_df) else 0.0,
        "高价值组购买率%": round(float(rate_hv), 2),
        "其他用户购买率%": round(float(rate_rest), 2),
        "购买率提升倍数": round(float(lift), 2) if np.isfinite(lift) else None,
        "判定阈值": threshold,
    }
    return user_df, summary


def score_single_user(fav_cart_cnt: float, events: float,
                      active_days: float, top_cat_share: float,
                      reference: pd.DataFrame,
                      threshold: float = DEFAULT_THRESHOLD) -> dict:
    """
    单用户判定：给定四个特征取值，参照既有用户分布（reference=特征表）
    计算分位得分并判定是否高价值。
    """
    one = pd.DataFrame({
        "intent_cnt": [fav_cart_cnt],
        "events": [events],
        "active_days": [active_days],
        "top_cat_share": [top_cat_share],
    })
    score = 0.0
    for (col, w, _label), v in zip(FEATURES, one.iloc[0]):
        # 分位 = 参照群体中小于等于该值的比例（并列取平均近似：用 rank 法）
        col_vals = reference[col].to_numpy()
        pct = (np.sum(col_vals < v) + 0.5 * np.sum(col_vals == v)) / len(col_vals)
        score += w * pct
    score *= 100
    return {
        "value_score": round(score, 1),
        "is_high_value": bool(score >= threshold),
        "threshold": threshold,
    }


if __name__ == "__main__":
    # 自检：构造 200 个模拟用户的行为日志跑通全流程
    rng = np.random.default_rng(42)
    rows = []
    for uid in range(200):
        intensity = rng.gamma(1.2, 8) + 1
        for _ in range(int(intensity)):
            b = rng.choice(["pv", "fav", "cart", "buy"],
                           p=[0.70, 0.06, 0.12, 0.12])
            rows.append((uid, rng.integers(1, 5000), rng.integers(1, 500),
                         b, f"2017-11-{rng.integers(25, 31):02d}"))
    demo = pd.DataFrame(rows, columns=["user_id", "item_id", "category_id",
                                       "behavior_type", "dt"])
    udf, summ = evaluate_users(demo)
    print(summ)
