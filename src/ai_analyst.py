#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
AI 分析师：把本地算好的聚合指标交给 DeepSeek，生成贴合转化漏斗主题的
动态分析（漏斗诊断报告 + 用户分群投放策略）

设计要点
--------
* 只把**聚合统计**（漏斗比例、分群画像、时段分布）发给模型，
  不发原始日志行 —— token 成本低，且不暴露明细数据；
* 输出结构固定：漏斗报告为 Markdown，分群策略为 JSON（方便页面渲染卡片）；
* 调用失败抛 llm_client.LLMError，由页面负责提示与重试。
"""

from __future__ import annotations

import json
import sys

from llm_client import chat, chat_json

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ANALYST_SYSTEM = (
    "你是电商数据分析团队的资深分析师，为业务负责人撰写正式的数据分析报告。"
    "写作规范：\n"
    "1) 每个结论都必须引用输入数据中的具体数值（比例用百分数，保留一至两位小数）；\n"
    "2) 语言客观、克制、陈述式。禁止：感叹号、反问句、emoji、口语词"
    "（非常/超级/简直/ remarkable 类修饰）、空洞套话（值得注意的是/综上/总而言之/"
    "不难发现/赋能/抓手/助力）、对仗排比式的口号句；\n"
    "3) 判断须给出数据依据；建议须给出具体动作与预期指标变化，不写正确的废话；\n"
    "4) 结构采用正式报告体：章节用『一、二、三』编号，段落简洁，多用短句与数据；\n"
    "5) 全文用简体中文，Markdown 输出。"
)


def analyze_funnel(stats: dict, top_cats: list[dict], hourly: dict[int, dict],
                   max_tokens: int = 4096) -> str:
    """
    漏斗诊断报告：输入 dataset_loader.funnel_stats / top_categories /
    hourly_stats 的聚合结果，返回 Markdown 报告。
    """
    peak_pv = max(hourly, key=lambda h: hourly[h]["pv"])
    peak_buy = max(hourly, key=lambda h: hourly[h]["buy"])
    payload = {
        "漏斗与转化率": stats,
        "头部类目": top_cats,
        "时段行为量(24h)": hourly,
        "浏览峰值时段": f"{peak_pv}时", "购买峰值时段": f"{peak_buy}时",
    }
    user = (
        "请依据以下聚合统计，撰写一份正式的《用户行为转化漏斗分析报告》。"
        "结构要求（Markdown）：\n"
        "# 一行报告标题（不超过 18 字，不带副标题）\n"
        "一、数据概况 —— 样本量、时间范围、四类行为量、整体购买转化率；\n"
        "二、漏斗分层诊断 —— 浏览→收藏、收藏→加购、加购→购买三段逐一给出："
        "该层转化率数值、依据行业常规基准判断该层是否为瓶颈、流失原因分析；\n"
        "三、时段与类目发现 —— 峰值时段与头部类目的数据发现 2~4 条，每条一行；\n"
        "四、结论与建议 —— 按优先级列出 4~6 条行动项，每条注明：针对的漏斗层级、"
        "具体做法、预期改善的指标。\n\n"
        "只输出报告正文，不要输出『好的，以下是报告』之类的开场语。\n\n数据：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    return chat(
        [{"role": "system", "content": ANALYST_SYSTEM},
         {"role": "user", "content": user}],
        temperature=0.4, max_tokens=max_tokens,
    )


def analyze_segments(profiles: list[dict], overall: dict,
                     extra: dict | None = None,
                     max_tokens: int = 4096) -> list[dict]:
    """
    分群投放策略：输入 K-Means 分群画像（dataset_loader.segment_users 返回的
    profiles），让 DeepSeek 判断每类用户是什么类型、该怎么投放。
    extra 可传入定向推送名单概况（dataset_loader.targeted_push_lists 的 summary，
    含高价值用户偏爱类目 Top10），使「推荐商品方向」具体到品类名。
    返回列表（每群一项），字段：群编号/类型名/特征解读/推荐商品方向/
    推送策略/推送时机/优先级。
    """
    schema = {
        "segments": [{
            "群编号": "与输入一致",
            "类型名": "5字以内，如 冲动购买型",
            "特征解读": "80字内，引用数据解释这类用户的行为",
            "推荐商品方向": "具体到商品品类，必须引用输入中给出的中文类目名称，30字内",
            "推送策略": "具体怎么投（渠道/文案/优惠方式），60字内",
            "推送时机": "结合活跃时段给出",
            "优先级": "高/中/低",
        }],
    }
    user = (
        "以下是某电商平台的用户分群画像（K-Means 聚类结果 + 不含购买信息的价值评分，"
        "购买率仅用于验证）。请判断每群用户属于什么类型，并给出定向投放方案。\n"
        f"输出 JSON，结构为：{json.dumps(schema, ensure_ascii=False)}\n"
        "要求：\n"
        "1. 类型名贴合行为特征；策略要可落地；整体购买转化率"
        f"{overall.get('整体购买转化率%')}%，以此判断各群相对大盘的高低；\n"
        "2. 「推荐商品方向」必须具体到商品品类：直接引用「头部类目名称」"
        "或「高价值用户偏爱类目Top10」中出现的中文品类名（如具体的服饰/家居/数码等"
        "品类名），并给出该品类下值得主推的商品形态（如款型、价位段），"
        "禁止输出『日用品』『热门商品』这类泛泛说法；\n"
        "3. 若输入含定向名单概况，请把三类名单（浏览多购买少→打折实用商品、"
        "收藏多购买少→收藏商品打折提醒、高价值用户→按偏爱类目推新款）的差异化"
        "策略融入对应人群的推送策略中。\n\n分群画像：\n"
        + json.dumps(profiles, ensure_ascii=False)
    )
    if extra:
        user += "\n\n补充上下文（定向名单与偏爱类目）：\n" \
                + json.dumps(extra, ensure_ascii=False)
    data = chat_json(
        [{"role": "system", "content": ANALYST_SYSTEM},
         {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )
    segments = data.get("segments", data if isinstance(data, list) else [])
    if not isinstance(segments, list) or not segments:
        raise ValueError("AI 返回的分群结果为空")
    # 保证群编号可对回聚类结果
    out = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        # 群编号统一为 int（模型有时返回字符串），对不回时按顺序兜底
        try:
            seg["群编号"] = int(seg["群编号"])
        except (TypeError, ValueError):
            seg["群编号"] = i
        for k in ("类型名", "特征解读", "推荐商品方向", "推送策略", "推送时机", "优先级"):
            seg.setdefault(k, "—")
        out.append(seg)
    return out


def analyze_types(type_profiles: list[dict], overall: dict,
                  extra: dict | None = None,
                  max_tokens: int = 4096) -> list[dict]:
    """
    五类规则分型的投放方案细化：输入 user_classifier.classify_users 的
    分型概况（人数/优先级/推荐类目Top5/购买率），DeepSeek 为每一类给出
    具体到品类的投放方案（推什么款、怎么投、何时推）。
    返回列表（每类型一项），字段：类型/特征解读/推荐商品方向/推送策略/推送时机。
    """
    schema = {
        "types": [{
            "类型": "与输入一致",
            "特征解读": "60字内，引用数据解释这类用户的行为",
            "推荐商品方向": "具体到品类名与款型/价位段（必须引用输入给出的中文类目名），40字内",
            "推送策略": "具体怎么投（渠道/文案/优惠方式/钩子），60字内",
            "推送时机": "结合人群特征给出",
        }],
    }
    user = (
        "以下是某电商平台用户的规则分型结果（五类画像+兜底，互斥口径，"
        "每名用户已标注推荐类目）。请为每一类输出投放方案。\n"
        f"输出 JSON，结构为：{json.dumps(schema, ensure_ascii=False)}\n"
        "要求：\n"
        "1. 「推荐商品方向」必须具体到商品品类：直接引用该类型「推荐类目Top5」"
        "或「头部类目」中的中文品类名，并给出值得主推的款型与价位段，"
        "禁止『日用品』『热门商品』这类泛泛说法；\n"
        "2. 推送策略要贴合每类既定的投放钩子（加购囤积→限时折扣/库存紧张、"
        "品类专一→会员专享/复购提醒、收藏待决→降价通知/相似款对比、"
        "跨类探索→猜你喜欢/新人首单、浏览游荡→低成本信息流爆款引流）；\n"
        f"3. 整体购买转化率{overall.get('整体购买转化率%')}%，据此把握各类型的"
        "转化空间与投入力度。\n\n分型概况：\n"
        + json.dumps(type_profiles, ensure_ascii=False)
    )
    if extra:
        user += "\n\n补充上下文（头部类目等）：\n" + json.dumps(extra, ensure_ascii=False)
    data = chat_json(
        [{"role": "system", "content": ANALYST_SYSTEM},
         {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )
    types = data.get("types", data if isinstance(data, list) else [])
    if not isinstance(types, list) or not types:
        raise ValueError("AI 返回的分型结果为空")
    valid = {p["类型"] for p in type_profiles}
    out = []
    for seg in types:
        if isinstance(seg, dict) and seg.get("类型") in valid:
            for k in ("特征解读", "推荐商品方向", "推送策略", "推送时机"):
                seg.setdefault(k, "—")
            out.append(seg)
    if not out:
        raise ValueError("AI 返回的类型名与输入不匹配")
    return out
