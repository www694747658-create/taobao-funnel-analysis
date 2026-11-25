#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""整页渲染集成测试：Streamlit AppTest 无头运行 app.py 全链路。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _demo_df() -> pd.DataFrame:
    import numpy as np
    rng = np.random.default_rng(7)
    rows = []
    for uid in range(40):
        for _ in range(int(rng.integers(3, 15))):
            b = rng.choice(["pv", "fav", "cart", "buy"], p=[.7, .07, .1, .13])
            rows.append((uid, rng.integers(1, 200), rng.integers(1, 15), b,
                         f"2017-11-{rng.integers(25, 31):02d} "
                         f"{rng.integers(0, 24):02d}:00:00"))
    return pd.DataFrame(rows, columns=["user_id", "item_id", "category_id",
                                       "behavior_type", "dt"]).assign(
        dt=lambda d: pd.to_datetime(d["dt"]))


def test_login_gate():
    """未登录时只渲染登录页：出现登录/注册标签，不渲染数据图表。"""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, \
        f"登录页运行期异常: {[(e.type, e.message) for e in at.exception]}"
    tabs = [t.label for t in at.tabs if isinstance(t.label, str)]
    assert any("登录" in t for t in tabs) and any("注册" in t for t in tabs), \
        f"登录页缺少登录/注册标签: {tabs}"
    assert len(at.get("plotly_chart")) == 0, "未登录不应渲染数据图表"


@pytest.fixture(scope="module")
def app_test():
    """先跑登录页，再模拟已登录 + 已注入示例数据集，渲染完整看板。"""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    at.session_state["auth_user"] = "tester"
    at.session_state["dataset"] = _demo_df()
    at.session_state["dataset_name"] = "测试数据集.csv"
    at.run()
    return at


def test_no_exception(app_test):
    assert not app_test.exception, \
        f"页面运行期异常: {[(e.type, e.message) for e in app_test.exception]}"


def test_upload_page_renders(app_test):
    """默认页（上传数据集）在已注入数据时渲染预览与基础漏斗指标。"""
    labels = [m.label for m in app_test.metric]
    assert "整体购买转化率" in labels, f"未渲染基础漏斗指标: {labels[:8]}"
    assert "行为记录" in labels, "未渲染数据规模 KPI"
    assert len(app_test.get("dataframe")) >= 1, "未渲染数据预览表格"


def test_need_dataset_blocks_ai_pages():
    """未加载数据集时，AI 页面给出引导提示而不报异常。"""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    at.session_state["auth_user"] = "tester"
    at.run()
    assert not at.exception
