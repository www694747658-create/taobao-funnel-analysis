#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""账号体系单元测试：注册待审核 / 冻结 / 解冻 / 管理员保护（sqlite 内存库）。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from auth import (ADMIN_USERNAME, get_role, get_user_email, list_users,
                  register, reject_user, set_status, verify_login)

CREATE = """
CREATE TABLE users (
    username      VARCHAR(32) PRIMARY KEY,
    password_hash VARCHAR(200) NOT NULL,
    email         VARCHAR(120) NOT NULL DEFAULT '',
    role          VARCHAR(16)  NOT NULL DEFAULT 'user',
    status        VARCHAR(16)  NOT NULL DEFAULT 'pending',
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture()
def eng():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(CREATE))
    return engine


def _register_ok(eng, u="newbie", p="secret123", e="n@example.com"):
    ok, msg = register(eng, u, p, e)
    assert ok, msg
    return u


def test_register_goes_pending(eng):
    """注册成功后账号处于待审核状态，未审核不可登录。"""
    u = _register_ok(eng)
    df = list_users(eng)
    row = df[df["username"] == u].iloc[0]
    assert row["status"] == "pending" and row["role"] == "user"
    ok, reason = verify_login(eng, u, "secret123")
    assert not ok and "审核" in reason


def test_register_validation(eng):
    """邮箱格式 / 密码长度 / 重名校验。"""
    assert not register(eng, "a1", "secret123", "bad-email")[0]
    assert not register(eng, "a1", "123", "a@b.com")[0]
    _register_ok(eng, "dup")
    ok, msg = register(eng, "dup", "secret123", "x@example.com")
    assert not ok and "已被注册" in msg


def test_approve_then_login(eng):
    """审核通过后可正常登录，角色为普通用户。"""
    u = _register_ok(eng)
    ok, msg = set_status(eng, u, "active")
    assert ok, msg
    ok, reason = verify_login(eng, u, "secret123")
    assert ok and not reason
    assert get_role(eng, u) == "user"
    assert get_user_email(eng, u) == "n@example.com"


def test_freeze_blocks_login(eng):
    """冻结后禁止登录并给出冻结提示，解冻后恢复。"""
    u = _register_ok(eng)
    set_status(eng, u, "active")
    assert set_status(eng, u, "frozen")[0]
    ok, reason = verify_login(eng, u, "secret123")
    assert not ok and "冻结" in reason
    assert set_status(eng, u, "active")[0]
    assert verify_login(eng, u, "secret123")[0]


def test_admin_protected(eng):
    """管理员账号不可冻结 / 不可删除；不存在时 get_role 兜底 user。"""
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (username, password_hash, role, status) "
            "VALUES (:u, 'x$y', 'admin', 'active')"), {"u": ADMIN_USERNAME})
    ok, msg = set_status(eng, ADMIN_USERNAME, "frozen")
    assert not ok and "管理员" in msg
    ok, msg = reject_user(eng, ADMIN_USERNAME)
    assert not ok and "管理员" in msg
    assert get_role(eng, "ghost") == "user"


def test_reject_removes_pending(eng):
    """拒绝申请后记录被删除，登录返回凭据错误（无账号）。"""
    u = _register_ok(eng)
    ok, msg = reject_user(eng, u)
    assert ok, msg
    ok, reason = verify_login(eng, u, "secret123")
    assert not ok and not reason  # 账号不存在：不泄露原因
