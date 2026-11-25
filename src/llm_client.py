#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
DeepSeek 大模型客户端（展示层 AI 分析共用）

密钥查找顺序（避免把 key 硬编码进仓库）：
    1. 环境变量 DEEPSEEK_API_KEY
    2. 项目根 .streamlit/secrets.toml 的 DEEPSEEK_API_KEY

用法
----
    from llm_client import chat, chat_json, llm_ready
    text = chat([{"role": "user", "content": "用一句话解释转化漏斗"}])
    data = chat_json([...])   # 强制 JSON 输出并解析为 dict

本模块不依赖 Streamlit / 数据库，报告脚本与测试可直接复用。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT_S = 120
MAX_RETRIES = 2


class LLMError(RuntimeError):
    """DeepSeek 调用失败（网络/鉴权/限流/格式解析）。"""


def _secrets_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"


def get_api_key() -> str:
    """按 优先环境变量 → secrets.toml 的顺序取 DeepSeek API key。"""
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        data = tomllib.loads(_secrets_path().read_text(encoding="utf-8"))
        return str(data.get("DEEPSEEK_API_KEY", "")).strip()
    except Exception:
        return ""


def llm_ready() -> tuple[bool, str]:
    """返回 (密钥是否可用, 提示语)，供页面给出友好引导。"""
    if get_api_key():
        return True, "DeepSeek 已就绪"
    return False, ("未找到 DeepSeek API key：请设置环境变量 DEEPSEEK_API_KEY，"
                   "或在 .streamlit/secrets.toml 中填写")


def chat(messages: list[dict], model: str = DEFAULT_MODEL,
         temperature: float = 1.0, max_tokens: int = 4096,
         response_format: dict | None = None) -> str:
    """
    调用 DeepSeek chat 接口，返回助手回复文本。
    网络抖动自动重试（指数退避），限流 429 亦重试。
    """
    key = get_api_key()
    if not key:
        raise LLMError("未配置 DeepSeek API key（环境变量 DEEPSEEK_API_KEY 或 secrets.toml）")

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    import time
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt:
            time.sleep(min(2 ** attempt, 8))
        try:
            resp = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            last_err = LLMError(f"网络请求失败：{exc.__class__.__name__}: {exc}")
            continue
        if resp.status_code == 200:
            try:
                return resp.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"响应格式异常：{exc}") from exc
        if resp.status_code in (429, 500, 502, 503):
            last_err = LLMError(f"服务暂时不可用（HTTP {resp.status_code}）：{resp.text[:200]}")
            continue
        # 4xx 其余（鉴权失败、参数错误等）不重试
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:200])
        except ValueError:
            detail = resp.text[:200]
        raise LLMError(f"DeepSeek 返回 HTTP {resp.status_code}：{detail}")
    raise LLMError(f"重试 {MAX_RETRIES} 次后仍失败：{last_err}")


def _extract_json(text: str) -> str:
    """从回复中提取 JSON 主体（容忍 ```json 围栏与前后说明文字）。"""
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise LLMError("回复中未找到 JSON 内容")
    return text[start:]


def chat_json(messages: list[dict], model: str = DEFAULT_MODEL,
              temperature: float = 0.3, max_tokens: int = 4096) -> dict | list:
    """要求模型输出 JSON 并解析；解析失败自动降级为一次修正请求。"""
    msgs = list(messages) + [{"role": "system",
                              "content": "只输出合法 JSON，不要任何多余文字或代码围栏。"}]
    text = chat(msgs, model=model, temperature=temperature,
                max_tokens=max_tokens, response_format={"type": "json_object"})
    try:
        return json.loads(_extract_json(text))
    except (json.JSONDecodeError, LLMError):
        fix = [{"role": "user",
                "content": "下面的内容不是合法 JSON，请修正为合法 JSON 后只输出 JSON：\n" + text[:4000]}]
        return json.loads(_extract_json(chat(fix, model=model, temperature=0.0)))


if __name__ == "__main__":
    ready, hint = llm_ready()
    print(hint)
    if ready:
        print(chat([{"role": "user", "content": "用一句话说明电商转化漏斗的核心作用。"}]))
