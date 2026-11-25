#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
看板登录认证与账号管理：MySQL 用户表 + PBKDF2 密码哈希 + 角色权限

安全设计
--------
* 密码绝不存明文：pbkdf2_hmac(SHA-256, 20 万次迭代) + 每用户独立随机盐，
  存储格式 `salt_hex$hash_hex`；
* 全部用绑定变量传参，规避 SQL 注入；
* 用户名规则 2~32 位字母/数字/下划线/中文，密码至少 6 位，邮箱格式校验。

角色与状态（用户工作流）
------------------------
* role：admin（最高权限，唯一内置账号）/ user（普通用户）；
* status：pending（注册申请待审核）→ active（可登录）⇄ frozen（冻结，禁止登录）；
* 注册不直接建号成功，而是进入 pending，由管理员在「账号管理」页审核；
* 管理员账号启动时自动播种（若不存在），且每次启动强制恢复 active，
  防止误冻结导致系统失去管理入口。

供 app.py 调用：
    ensure_users_table(engine)            # 启动时建表/迁移/播种（幂等）
    register(engine, u, p, email)         # 注册 → pending 申请
    verify_login(engine, u, p)            # 登录校验 → (是否通过, 原因)
    get_role(engine, u)                   # 查角色
    list_users(engine)                    # 全量账号表（管理员页用）
    set_status(engine, u, status)         # 审核 / 冻结 / 解冻
    reject_user(engine, u)                # 拒绝并删除申请
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets

import pandas as pd

# 内置管理员账号（启动时自动播种）。真实密码不要写进代码：
# 用环境变量 TAOBAO_ADMIN_USERNAME / TAOBAO_ADMIN_PASSWORD 覆盖，
# 公开仓库里只保留本地演示用的默认值。
ADMIN_USERNAME = os.getenv("TAOBAO_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("TAOBAO_ADMIN_PASSWORD", "admin@123456")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD_LEN = 6

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_FROZEN = "frozen"

SQL_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    username      VARCHAR(32)  NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    email         VARCHAR(120) NOT NULL DEFAULT '',
    role          VARCHAR(16)  NOT NULL DEFAULT 'user',
    status        VARCHAR(16)  NOT NULL DEFAULT 'pending',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 旧表结构迁移：依次补齐 email / role / status 列
SQL_MIGRATE_COLS = (
    ("email", "ALTER TABLE users ADD COLUMN email VARCHAR(120) "
              "NOT NULL DEFAULT ''"),
    ("role", "ALTER TABLE users ADD COLUMN role VARCHAR(16) "
             "NOT NULL DEFAULT 'user'"),
    ("status", "ALTER TABLE users ADD COLUMN status VARCHAR(16) "
               "NOT NULL DEFAULT 'active'"),
)


def _hash(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, 200_000)
    return salt.hex() + "$" + digest.hex()


def ensure_users_table(engine) -> None:
    """建表（幂等）+ 旧表补列 + 旧账号激活 + 播种/保护管理员账号。"""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text(SQL_ENSURE_TABLE))
        try:
            cols = {r[0] for r in conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'"))}
            for col, ddl in SQL_MIGRATE_COLS:
                if col not in cols:
                    conn.execute(text(ddl))
        except Exception:                                       # noqa: BLE001
            pass  # 非 MySQL 后端（如单测用 sqlite）跳过迁移
        # 历史遗留空状态一律视为已激活
        conn.execute(text(
            "UPDATE users SET status = 'active' "
            "WHERE status IS NULL OR status = ''"))
        exists = conn.execute(text(
            "SELECT 1 FROM users WHERE username = :u"),
            {"u": ADMIN_USERNAME}).first()
        if not exists:
            conn.execute(text(
                "INSERT INTO users (username, password_hash, role, status) "
                "VALUES (:u, :p, 'admin', :s)"),
                {"u": ADMIN_USERNAME, "p": _hash(ADMIN_PASSWORD,
                                                 secrets.token_bytes(16)),
                 "s": STATUS_ACTIVE})
        else:
            # 管理员账号自我保护：始终保持 admin + active
            conn.execute(text(
                "UPDATE users SET role = 'admin', status = :s "
                "WHERE username = :u"),
                {"u": ADMIN_USERNAME, "s": STATUS_ACTIVE})


def register(engine, username: str, password: str,
             email: str = "") -> tuple[bool, str]:
    """注册申请：校验通过后以 pending 状态入库，等待管理员审核。"""
    from sqlalchemy import text
    username = username.strip()
    email = email.strip()
    if not USERNAME_RE.match(username):
        return False, "用户名需 2~32 位字母、数字、下划线或中文"
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"密码至少 {MIN_PASSWORD_LEN} 位"
    if not EMAIL_RE.match(email):
        return False, "请填写有效的邮箱地址（用于接收审核结果通知）"
    salt = secrets.token_bytes(16)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (username, password_hash, email, role, "
                "status) VALUES (:u, :p, :e, 'user', :s)"),
                {"u": username, "p": _hash(password, salt), "e": email,
                 "s": STATUS_PENDING})
    except Exception as exc:
        if ("Duplicate entry" in str(exc)
                or exc.__class__.__name__ == "IntegrityError"):
            return False, "该用户名已被注册"
        return False, f"注册失败：{exc.__class__.__name__}"
    return True, "注册申请已提交，管理员审核通过后会将结果发送至你的邮箱"


def verify_login(engine, username: str, password: str) -> tuple[bool, str]:
    """校验用户名密码与账号状态。返回 (是否通过, 失败原因)。"""
    from sqlalchemy import text
    row = None
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT password_hash, status FROM users WHERE username = :u"),
            {"u": (username or "").strip()}).first()
    if row is None:
        return False, ""
    password_hash, status = row[0], row[1]
    try:
        salt_hex, expected_hex = password_hash.split("$", 1)
        expected = bytes.fromhex(expected_hex)
    except ValueError:
        return False, ""
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), 200_000)
    if not secrets.compare_digest(actual, expected):
        return False, ""
    if status == STATUS_FROZEN:
        return False, "该账号已被管理员冻结，暂无法登录"
    if status == STATUS_PENDING:
        return False, "注册申请尚待管理员审核，通过后会将结果发送至你的邮箱"
    return True, ""


def get_role(engine, username: str) -> str:
    """查询账号角色：admin / user；账号不存在时返回 user。"""
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT role FROM users WHERE username = :u"),
            {"u": (username or "").strip()}).first()
    return (row[0] if row else "user") or "user"


def list_users(engine) -> pd.DataFrame:
    """全量账号表（管理员页用）：待审核 → 冻结 → 正常，同级内按时间倒序。"""
    from sqlalchemy import text
    with engine.connect() as conn:
        df = pd.read_sql(text(
            "SELECT username, email, role, status, created_at FROM users"), conn)
    # FIELD() 为 MySQL 专用，排序在 pandas 内完成以兼容任意后端
    return df.sort_values(
        ["status", "created_at"], ascending=[True, False],
        key=lambda s: s.map({"pending": 0, "frozen": 1, "active": 2})
        if s.name == "status" else s).reset_index(drop=True)


def set_status(engine, username: str, status: str) -> tuple[bool, str]:
    """审核通过 / 冻结 / 解冻。管理员账号不允许被冻结。"""
    from sqlalchemy import text
    username = (username or "").strip()
    if status not in (STATUS_ACTIVE, STATUS_FROZEN):
        return False, "非法的状态值"
    if username == ADMIN_USERNAME and status != STATUS_ACTIVE:
        return False, "管理员账号不允许被冻结"
    with engine.begin() as conn:
        res = conn.execute(text(
            "UPDATE users SET status = :s WHERE username = :u"),
            {"s": status, "u": username})
    if res.rowcount == 0:
        return False, "账号不存在"
    return True, {"active": "已激活", "frozen": "已冻结"}[status]


def reject_user(engine, username: str) -> tuple[bool, str]:
    """拒绝注册申请：直接删除该 pending 记录。管理员账号不可删。"""
    from sqlalchemy import text
    username = (username or "").strip()
    if username == ADMIN_USERNAME:
        return False, "管理员账号不可删除"
    with engine.begin() as conn:
        res = conn.execute(text(
            "DELETE FROM users WHERE username = :u AND role <> 'admin'"),
            {"u": username})
    if res.rowcount == 0:
        return False, "账号不存在或不可删除"
    return True, "已拒绝并删除该申请"


def get_user_email(engine, username: str) -> str:
    """查询账号登记的邮箱。"""
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT email FROM users WHERE username = :u"),
            {"u": (username or "").strip()}).first()
    return (row[0] if row else "") or ""
