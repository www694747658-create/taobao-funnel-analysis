#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pytest 公共夹具：路径注入、引擎夹具、收集配置。

分层测试策略：
    test_dataset_loader.py  解析/聚合纯函数测试（不需要数据库）
    test_db_layer.py        数据库层集成测试（连接 / 表结构 / 业务不变量）
    test_auth.py            登录注册测试
    test_app.py             整页渲染集成测试（Streamlit AppTest）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def engine():
    from db import get_engine
    return get_engine()
