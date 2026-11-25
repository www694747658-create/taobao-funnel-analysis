#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据库层集成测试：连接、表清单、核心表结构与业务不变量。

库 taobao_funnel 五张表：
    user_behavior（原始行为日志）/ users（登录账号）/ category_mapping（类目映射）
    products（商品库）/ push_tasks（推送任务单）
"""

from __future__ import annotations

from sqlalchemy import text

import pytest


def test_ping(engine):
    from db import ping
    info = ping(engine)
    assert "MySQL" in info and "taobao_funnel" in info


def test_table_whitelist(engine):
    """只允许五张核心表存在（旧架构派生表/视图必须已清除）。"""
    from db import table_names
    tables = set(table_names(engine))
    expected = {"user_behavior", "users", "category_mapping",
                "products", "push_tasks"}
    assert tables == expected, \
        f"库中存在意外对象：{tables - expected}"


# ---------------------------------------------------------------- user_behavior
def test_user_behavior_overview(engine):
    """全量行为日志规模不变量（拆分脚本据此校验并集=全集）。"""
    with engine.connect() as c:
        n, users = c.execute(text(
            "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM user_behavior")).fetchone()
    assert n == 4_993_579
    assert users == 49_400


def test_user_behavior_no_null_keys(engine):
    with engine.connect() as c:
        n = c.execute(text(
            "SELECT COUNT(*) FROM user_behavior "
            "WHERE user_id IS NULL OR behavior_type IS NULL OR dt IS NULL")).scalar()
    assert n == 0


def test_user_behavior_values_normalized(engine):
    with engine.connect() as c:
        vals = {r[0] for r in c.execute(
            text("SELECT DISTINCT behavior_type FROM user_behavior")).fetchall()}
    assert vals <= {"pv", "fav", "cart", "buy"}


# ---------------------------------------------------------------- users
def test_users_table_has_hash_column(engine):
    """登录表必须用哈希存密码（不允许明文列）。"""
    with engine.connect() as c:
        cols = {r[0] for r in c.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA='taobao_funnel' AND TABLE_NAME='users'")).fetchall()}
    assert any("hash" in col.lower() or "password" in col.lower() for col in cols)
    assert "password_plain" not in {c.lower() for c in cols}


# ---------------------------------------------------------------- category_mapping
def test_category_mapping_structure(engine):
    with engine.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM category_mapping")).scalar()
        pk = c.execute(text(
            "SELECT COUNT(DISTINCT CONSTRAINT_NAME) FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA='taobao_funnel' AND TABLE_NAME='category_mapping' "
            "AND CONSTRAINT_NAME='PRIMARY'")).scalar()
        ids = c.execute(text("SELECT COUNT(DISTINCT category_id) "
                             "FROM category_mapping")).scalar()
    assert n > 0, "category_mapping 表为空：请先运行 src/build_category_map.py"
    assert pk == 1, "category_mapping 缺少主键"
    assert ids == n, "存在重复的类目编号"


def test_category_mapping_covers_dataset(engine):
    """映射表应覆盖数据集中的全部类目（DeepSeek 推测命名）。"""
    with engine.connect() as c:
        total = c.execute(text(
            "SELECT COUNT(DISTINCT category_id) FROM user_behavior")).scalar()
        mapped = c.execute(text("SELECT COUNT(*) FROM category_mapping")).scalar()
    assert mapped >= total, f"映射 {mapped} 条 < 数据集类目数 {total}"


def test_category_mapping_names_nonempty(engine):
    with engine.connect() as c:
        n = c.execute(text(
            "SELECT COUNT(*) FROM category_mapping "
            "WHERE category_name IS NULL OR TRIM(category_name)=''")).scalar()
    assert n == 0
