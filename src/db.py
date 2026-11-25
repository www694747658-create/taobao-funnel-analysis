#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
数据访问模块（SQLAlchemy）· 数据库重构后的精简版

库 taobao_funnel 现包含三张表（旧架构的派生表/视图已全部移除）：
    user_behavior     原始行为日志全量（user_id/item_id/category_id/
                      behavior_type/ts/dt/session_id）
    users             看板登录账号表（PBKDF2 哈希，src/auth.py）
    category_mapping  类目编号→中文品类名映射（DeepSeek 推测命名，
                      src/build_category_map.py 生成，可人工修正）

本模块只保留连接层职责：配置、引擎、通用查询、连通性自检。
聚合分析全部在 dataset_loader / dataset_split / build_category_map 内完成。

直接运行做连通性自检：python src/db.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

try:  # Python 3.11+；旧版本自动降级为“只读环境变量”
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

# 让中文输出在管道 / 终端下都不乱码
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _local_secret(section: str, key: str) -> str:
    """读取本地 .streamlit/secrets.toml（已在 .gitignore 中排除）的配置项。"""
    if tomllib is None:
        return ""
    path = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"
    try:
        with path.open("rb") as fh:
            return str(tomllib.load(fh).get(section, {}).get(key, "") or "")
    except (OSError, ValueError):
        return ""


def _env_or_secret(env_key: str, section: str, secret_key: str,
                   default: str = "") -> str:
    """配置读取顺序：环境变量 → 本地 secrets.toml → 默认值。"""
    return os.getenv(env_key) or _local_secret(section, secret_key) or default


# ---------------------------------------------------------------- 配置与连接
@dataclass
class DBConfig:
    """数据库连接配置。密码等敏感项优先取环境变量 / 本地 secrets.toml。"""
    host: str = field(default_factory=lambda: os.getenv("TAOBAO_DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("TAOBAO_DB_PORT", "3306")))
    user: str = field(default_factory=lambda: _env_or_secret(
        "TAOBAO_DB_USER", "mysql", "user", "root"))
    password: str = field(default_factory=lambda: _env_or_secret(
        "TAOBAO_DB_PASSWORD", "mysql", "password"))
    database: str = field(default_factory=lambda: os.getenv("TAOBAO_DB_NAME", "taobao_funnel"))
    charset: str = "utf8mb4"

    @property
    def url(self) -> str:
        # quote_plus 处理密码里的 @ # / 等特殊字符，避免连接串被截断
        return (f"mysql+pymysql://{quote_plus(self.user)}:{quote_plus(self.password)}"
                f"@{self.host}:{self.port}/{self.database}?charset={self.charset}")

    def safe_repr(self) -> str:
        return f"{self.user}@{self.host}:{self.port}/{self.database}"


def get_engine(cfg: DBConfig | None = None) -> Engine:
    """
    创建 SQLAlchemy 引擎。

    pool_pre_ping=True：取连接前先探活，避免 MySQL 8 小时空闲断连后报
    "MySQL server has gone away"（Streamlit 长时间挂着很常见）。
    """
    cfg = cfg or DBConfig()
    return create_engine(cfg.url, pool_pre_ping=True, pool_recycle=3600, future=True)


def query_df(sql: str, engine: Engine, params: dict | None = None) -> pd.DataFrame:
    """执行 SELECT 并返回 DataFrame。SQL 用绑定变量传参，不做字符串拼接。"""
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def ping(engine: Engine) -> str:
    """连通性自检，返回 MySQL 版本与当前库名。"""
    row = query_df("SELECT VERSION() AS v, DATABASE() AS db", engine).iloc[0]
    return f"MySQL {row['v']} / 当前库 {row['db']}"


def table_names(engine: Engine) -> list[str]:
    """当前库的全部表名（不含视图）。"""
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SHOW TABLES")).fetchall()]


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    cfg = DBConfig()
    print("=" * 70)
    print("数据访问模块自检")
    print("=" * 70)
    print(f"连接 : {cfg.safe_repr()}")
    try:
        engine = get_engine(cfg)
        print(f"状态 : {ping(engine)}")
    except Exception as e:                                    # noqa: BLE001
        print(f"连接失败：{type(e).__name__}: {e}")
        print("请检查 MySQL 是否启动、库是否存在、账号密码是否正确。")
        return 2

    tables = table_names(engine)
    print(f"数据表 : {', '.join(sorted(tables))}")
    for t in ("user_behavior", "users", "category_mapping"):
        if t in tables:
            n = query_df(f"SELECT COUNT(*) AS n FROM {t}", engine).iloc[0]["n"]
            print(f"  {t:<18} {n:,} 行")
    print("\n连接正常，可以启动 Streamlit：streamlit run app.py")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
