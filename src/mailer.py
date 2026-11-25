#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
邮件通知：注册审核通过后给申请者发邮件（smtplib，SSL 直连）

SMTP 配置从 .streamlit/secrets.toml 读取（app 层传入，本模块不依赖
Streamlit），未配置时调用方静默降级——审核操作照常执行，仅提示未发信。

secrets.toml 示例（QQ 邮箱）：
    [smtp]
    host = "smtp.qq.com"
    port = 465
    user = "your@qq.com"
    password = "授权码（非登录密码）"
"""

from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText


def send_mail(cfg: dict | None, to: str, subject: str, body: str) -> tuple[bool, str]:
    """发送纯文本邮件。返回 (是否成功, 失败原因)。"""
    if not cfg:
        return False, "未配置 SMTP"
    for key in ("host", "user", "password"):
        if not cfg.get(key):
            return False, f"SMTP 配置缺少 {key}"
    sender = cfg.get("sender", cfg["user"])
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = sender
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL(str(cfg["host"]), int(cfg.get("port", 465)),
                              timeout=15) as smtp:
            smtp.login(cfg["user"], cfg["password"])
            smtp.sendmail(sender, [to], msg.as_string())
    except Exception as exc:                                    # noqa: BLE001
        return False, f"{exc.__class__.__name__}"
    return True, ""


def approval_subject(username: str) -> str:
    return f"【淘宝用户行为分析系统】注册审核通过"


def approval_body(username: str) -> str:
    return (
        f"{username}，你好：\n\n"
        "你的注册申请已通过管理员审核，账号已创建成功，"
        "现在可以登录「基于淘宝用户行为日志的大数据分析与转化漏斗系统」。\n\n"
        "如非本人操作，请忽略本邮件。\n"
        "——系统自动通知"
    )
