#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dataset_loader 单元测试：列识别、行为归一化、漏斗聚合、分群画像。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dataset_loader as dl


def _demo_df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for uid in range(200):
        n = int(rng.integers(3, 20))
        kind = rng.random()
        for _ in range(n):
            if kind < 0.3:
                b = rng.choice(["pv", "fav", "cart", "buy"], p=[.5, .1, .15, .25])
            else:
                b = rng.choice(["pv", "fav", "cart", "buy"], p=[.85, .05, .08, .02])
            rows.append((uid, rng.integers(1, 300), rng.integers(1, 20), b,
                         f"2017-11-{rng.integers(25, 31):02d} "
                         f"{rng.integers(0, 24):02d}:00:00"))
    return pd.DataFrame(rows, columns=["user_id", "item_id", "category_id",
                                       "behavior_type", "dt"]).assign(
        dt=lambda d: pd.to_datetime(d["dt"]))


def test_detect_columns_aliases():
    df = pd.DataFrame(columns=["uid", "itemid", "cid", "action", "time"])
    found = dl.detect_columns(df)
    assert found["user_id"] == "uid" and found["behavior_type"] == "action"
    assert found["dt"] == "time" and found["category_id"] == "cid"


def test_detect_columns_chinese():
    df = pd.DataFrame(columns=["用户ID", "商品ID", "类目ID", "行为类型", "时间"])
    found = dl.detect_columns(df)
    assert set(found) == set(dl.STANDARD_COLS)


def test_normalize_behavior_codes():
    s = pd.Series([1, 2, 3, 4, "pv", "收藏", "加购", "buy", "未知"])
    out = dl.normalize_behavior(s)
    assert list(out[:4]) == ["pv", "fav", "cart", "buy"]
    assert list(out[4:8]) == ["pv", "fav", "cart", "buy"]
    assert pd.isna(out.iloc[8])


def test_load_uploaded_csv_string():
    demo = _demo_df().head(50)
    df = dl.load_uploaded(demo.rename(
        columns={"user_id": "uid", "item_id": "item", "category_id": "cid",
                 "behavior_type": "behavior", "dt": "time"}).to_csv(index=False))
    assert list(df.columns) == dl.STANDARD_COLS
    assert set(df["behavior_type"]) <= {"pv", "fav", "cart", "buy"}
    assert pd.api.types.is_datetime64_any_dtype(df["dt"])


def test_load_uploaded_missing_column():
    with pytest.raises(dl.DatasetError):
        dl.load_uploaded("a,b\n1,2\n")


def test_load_uploaded_bad_behavior():
    with pytest.raises(dl.DatasetError):
        dl.load_uploaded("uid,behavior,time\n1,跳舞,2017-11-25\n")


def test_funnel_stats():
    df = _demo_df()
    stats = dl.funnel_stats(df)
    assert stats["总用户数"] == 200
    assert stats["事件量漏斗"]["浏览"] == int((df["behavior_type"] == "pv").sum())
    # 漏斗口径单调不减：浏览人数 >= 收藏人数 …
    u = stats["用户量漏斗"]
    assert u["浏览"] >= u["收藏"] >= 0 and u["购买"] <= u["浏览"]
    assert 0 <= stats["整体购买转化率%"] <= 100


def test_hourly_stats_keys():
    df = _demo_df()
    hourly = dl.hourly_stats(df)
    assert set(hourly) == set(range(24))
    assert all(set(v) == {"pv", "fav", "cart", "buy"} for v in hourly.values())


def test_fingerprint_stable():
    df = _demo_df()
    assert dl.fingerprint(df) == dl.fingerprint(df.copy())
    assert dl.fingerprint(df) != dl.fingerprint(df.head(30))


def test_segment_users():
    df = _demo_df()
    scored, profiles = dl.segment_users(df, k=3)
    assert len(profiles) == 3
    assert sum(p["用户数"] for p in profiles) == 200
    assert {"群编号", "用户数", "购买率%", "人均行为数"} <= set(profiles[0])
    assert scored["cluster"].nunique() == 3
