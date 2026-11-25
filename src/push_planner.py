#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
推送计划模块 · 人群包 × 商品库 → 可执行任务单（含效果回流预留接口）

表 push_tasks（taobao_funnel 库）：
    id             自增主键
    task_name      任务名称
    user_type      目标用户类型（五类分型之一）
    user_count     人群包人数（快照）
    category_id    推荐类目编号
    category_name  推荐类目中文名
    product_id     关联 products.id（商品库素材，可空）
    product_title  商品标题快照（可空 = 仅品类级推送）
    hook           投放钩子（限时折扣 / 降价提醒 / 会员专享 …）
    channel        渠道（短信 / App推送 / 信息流 …）
    suggest_time   建议发送时段说明
    status         状态：draft → pending → sent（/ done）
    created_at     创建时间
    sent_at        实际发送时间（置为 sent 时填写）
    ---- 效果回流（预留列，本期不回填，接口先行）----
    opened         打开/曝光数     INT NULL
    clicked        点击数         INT NULL
    buyed          成交数         INT NULL
    feedback_at    效果数据回填时间 DATETIME NULL

直接运行自检：python src/push_planner.py
"""

from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DDL_PUSH_TASKS = """
CREATE TABLE IF NOT EXISTS push_tasks (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_name      VARCHAR(128)   NOT NULL,
    user_type      VARCHAR(64)    NOT NULL,
    user_count     INT            NOT NULL DEFAULT 0,
    category_id    BIGINT         NULL,
    category_name  VARCHAR(64)    NULL,
    product_id     BIGINT         NULL,
    product_title  VARCHAR(512)   NULL,
    hook           VARCHAR(128)   NULL,
    channel        VARCHAR(32)    NULL,
    suggest_time   VARCHAR(128)   NULL,
    status         VARCHAR(16)    NOT NULL DEFAULT 'draft',
    created_at     DATETIME       NOT NULL,
    sent_at        DATETIME       NULL,
    opened         INT            NULL,
    clicked        INT            NULL,
    buyed          INT            NULL,
    feedback_at    DATETIME       NULL,
    KEY idx_status (status),
    KEY idx_user_type (user_type),
    CONSTRAINT fk_push_product FOREIGN KEY (product_id)
        REFERENCES products (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def ensure_push_tasks_table(engine: Engine) -> None:
    # push_tasks 通过外键引用 products，确保商品表先存在
    from product_catalog import ensure_products_table
    ensure_products_table(engine)
    with engine.begin() as conn:
        conn.execute(text(DDL_PUSH_TASKS))


# ---------------------------------------------------------------- 任务单
def create_task(engine: Engine, task_name: str, user_type: str,
                user_count: int, category_name: str | None = None,
                category_id=None, product_id=None, product_title: str | None = None,
                hook: str | None = None, channel: str = "App推送",
                suggest_time: str | None = None) -> int:
    """新建推送任务（status=draft），返回任务 id。"""
    ensure_push_tasks_table(engine)
    with engine.begin() as conn:
        res = conn.execute(text("""
            INSERT INTO push_tasks
                (task_name, user_type, user_count, category_id, category_name,
                 product_id, product_title, hook, channel, suggest_time,
                 status, created_at)
            VALUES (:name, :utype, :ucount, :cid, :cname, :pid, :ptitle,
                    :hook, :channel, :stime, 'draft', :now)
        """), {"name": task_name, "utype": user_type, "ucount": int(user_count),
               "cid": category_id, "cname": category_name, "pid": product_id,
               "ptitle": product_title[:500] if product_title else None,
               "hook": hook, "channel": channel, "stime": suggest_time,
               "now": datetime.now()})
        return res.lastrowid


def list_tasks(engine: Engine, status: str | None = None,
               limit: int = 200) -> pd.DataFrame:
    """任务单列表（新任务在前）。"""
    ensure_push_tasks_table(engine)
    where = "WHERE status = :st" if status else ""
    params = {"st": status} if status else {}
    sql = f"""
        SELECT id AS 任务ID, task_name AS 任务名称, user_type AS 目标人群,
               user_count AS 人群人数, category_name AS 推荐类目,
               product_title AS 推送商品, hook AS 投放钩子, channel AS 渠道,
               suggest_time AS 建议发送时段,
               CASE status WHEN 'draft' THEN '草稿' WHEN 'pending' THEN '待发送'
                    WHEN 'sent' THEN '已发送' ELSE status END AS 状态,
               created_at AS 创建时间, sent_at AS 发送时间,
               opened AS 打开数, clicked AS 点击数, buyed AS 成交数
        FROM push_tasks {where} ORDER BY id DESC LIMIT {int(limit)}
    """
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def set_task_status(engine: Engine, task_id: int, status: str) -> bool:
    """更新任务状态；置为 sent 时自动记录发送时间。"""
    sent_at = datetime.now() if status == "sent" else None
    with engine.begin() as conn:
        res = conn.execute(text(
            "UPDATE push_tasks SET status = :s, "
            "sent_at = COALESCE(:sent, sent_at) WHERE id = :id"),
            {"s": status, "sent": sent_at, "id": int(task_id)})
        return res.rowcount > 0


def delete_task(engine: Engine, task_id: int) -> bool:
    with engine.begin() as conn:
        return conn.execute(text("DELETE FROM push_tasks WHERE id = :id"),
                            {"id": int(task_id)}).rowcount > 0


# ---------------------------------------------------------------- 效果回流接口（预留）
def update_feedback(engine: Engine, task_id: int,
                    opened: int | None = None, clicked: int | None = None,
                    buyed: int | None = None) -> bool:
    """
    效果回流：回填 打开/点击/成交 三项指标并记录回填时间。
    本期留接口不产数据；后续接入发送平台回调后调本函数落库。
    """
    with engine.begin() as conn:
        res = conn.execute(text("""
            UPDATE push_tasks
            SET opened  = COALESCE(:o, opened),
                clicked = COALESCE(:c, clicked),
                buyed   = COALESCE(:b, buyed),
                feedback_at = :now
            WHERE id = :id
        """), {"o": opened, "c": clicked, "b": buyed,
               "now": datetime.now(), "id": int(task_id)})
        return res.rowcount > 0


def feedback_summary(engine: Engine) -> pd.DataFrame:
    """已回流任务的效果汇总（点击率/转化率），无回流数据时为空表。"""
    sql = """
        SELECT task_name AS 任务名称, user_type AS 目标人群,
               opened AS 打开数, clicked AS 点击数, buyed AS 成交数,
               ROUND(clicked / NULLIF(opened, 0) * 100, 2) AS 点击率pct,
               ROUND(buyed / NULLIF(clicked, 0) * 100, 2) AS 点击成交率pct
        FROM push_tasks
        WHERE feedback_at IS NOT NULL ORDER BY feedback_at DESC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)
    return df.rename(columns={"点击率pct": "点击率%", "点击成交率pct": "点击→成交%"})


# ---------------------------------------------------------------- 建议发送时机
SUGGEST_TIME_BY_TYPE = {
    "加购囤积犹豫型": "工作日 20:00–22:00（晚间下单高峰，限时券倒计时压力最强）",
    "品类专一高转化型": "10:00–11:00 或 15:00–16:00（碎片浏览期，复购提醒打扰度低）",
    "收藏待决型": "12:00–13:00（午休比价期，降价提醒最易触发回访）",
    "跨类探索低转化型": "周末 14:00–17:00（闲逛时段，组合推荐+新人券）",
    "浏览游荡/低活跃型": "全天信息流低成本投放，不定向时段",
    "其他常规用户": "跟随大盘购买峰值时段常规推送",
}


def suggest_time(user_type: str, buy_peak_hour: int | None = None) -> str:
    """按人群类型给建议发送时段；有大盘购买峰值数据则补充说明。"""
    base = SUGGEST_TIME_BY_TYPE.get(user_type, "跟随大盘活跃时段推送")
    if buy_peak_hour is not None:
        base += f"（当前数据集购买峰值 {buy_peak_hour:02d} 时，可微调至峰值前 1 小时）"
    return base


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    from product_catalog import ensure_products_table
    from db import get_engine
    eng = get_engine()
    ensure_products_table(eng)
    ensure_push_tasks_table(eng)
    tid = create_task(eng, "自检任务", "加购囤积犹豫型", 1234,
                      category_name="连衣裙", hook="限时折扣",
                      suggest_time=suggest_time("加购囤积犹豫型"))
    print("新建任务:", tid)
    print(list_tasks(eng, "draft"))
    print("回流测试:", update_feedback(eng, tid, opened=100, clicked=10, buyed=2))
    print(feedback_summary(eng))
    print("清理:", delete_task(eng, tid))
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
