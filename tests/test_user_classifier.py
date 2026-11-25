#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""user_classifier 五类规则分型：口径互斥、推荐类目中文标注。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from user_classifier import TYPE_ORDER, classify_users  # noqa: E402


def _make_df() -> pd.DataFrame:
    rows = []
    # 用户1：品类专一高转化（集中买类目100，转化率高）
    for i in range(40):
        rows.append((1, 1000 + i, 100, "pv", "2017-11-25"))
    for i in range(10):
        rows.append((1, 1000 + i, 100, "cart", "2017-11-25"))
    for i in range(8):
        rows.append((1, 1000 + i, 100, "buy", "2017-11-26"))
    # 用户2：加购囤积犹豫（多加购两个类目，零购买）
    for i in range(20):
        rows.append((2, 2000 + i, 200, "pv", "2017-11-25"))
    for i in range(10):
        rows.append((2, 3000 + i, 200, "cart", "2017-11-25"))
    for i in range(5):
        rows.append((2, 4000 + i, 201, "cart", "2017-11-25"))
    # 用户3：低活跃（总行为 < 10）
    rows.append((3, 5001, 300, "pv", "2017-11-25"))
    rows.append((3, 5002, 301, "pv", "2017-11-26"))
    return pd.DataFrame(rows, columns=["user_id", "item_id", "category_id",
                                       "behavior_type", "dt"])


def test_classify_users_mutually_exclusive_and_complete():
    udf, summ = classify_users(_make_df())
    assert set(summ.keys()) == set(TYPE_ORDER)
    assert udf["用户类型"].isin(TYPE_ORDER).all()
    assert udf["用户ID"].nunique() == 3          # 互斥：每用户恰一行
    assert summ["品类专一高转化型"]["人数"] == 1
    assert summ["加购囤积犹豫型"]["人数"] == 1
    assert summ["浏览游荡/低活跃型"]["人数"] == 1


def test_classify_users_recommended_category_chinese():
    udf, _ = classify_users(_make_df(), name_map={"100": "连衣裙"})
    focus = udf[udf["用户类型"] == "品类专一高转化型"]
    assert (focus["推荐类目名称"] == "连衣裙").all()
    hoard = udf[udf["用户类型"] == "加购囤积犹豫型"]
    # 推荐类目 = 加购最多但未购买的类目（200 加购 10 次 > 201 的 5 次）
    assert (hoard["推荐类目ID"] == 200).all()
    low = udf[udf["用户类型"] == "浏览游荡/低活跃型"]
    assert (low["子类"] == "低活跃").all()
    # 导出列完整（人群包可直接对接推送系统）
    for col in ("推荐类目名称", "建议动作", "投放钩子", "推送优先级"):
        assert col in udf.columns
