#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
展示层 · 可视化看板（Streamlit · 上传驱动 + DeepSeek AI 分析）

页面结构（st.navigation，普通用户三页 + 管理员四页）：
    上传数据集          手动上传 CSV（多选上限 10 份）→ 列自动识别/映射 → 校验预览
    AI 漏斗分析         本地算漏斗/时段/类目聚合 → DeepSeek 生成动态诊断报告
    AI 分群与投放       五类互斥分型 → DeepSeek 生成定向投放策略
    账号管理            仅管理员（admin 角色）：注册审核 / 冻结解冻 / 邮件通知

动态性设计：
    * 一切分析围绕**本次上传的数据集**即时计算，不依赖固定数据库；
    * 只把聚合统计（漏斗比例、分群画像、时段分布）发给大模型，
      不发原始日志行 —— token 成本低、不暴露明细数据；
    * AI 结果按数据指纹缓存：同一数据集不重复调用 API；
    * 无文件时可一键加载示例数据（MySQL 抽样）演示全流程。

登录门保留：MySQL 用户表 + PBKDF2 哈希（src/auth.py）。
密钥：环境变量 DEEPSEEK_API_KEY 或 .streamlit/secrets.toml（勿提交仓库）。

启动：
    streamlit run app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import ai_analyst as ai  # noqa: E402
import dataset_loader as dl  # noqa: E402
import mailer  # noqa: E402
import product_catalog as pc  # noqa: E402
import push_planner as pp  # noqa: E402
import user_classifier as uc  # noqa: E402
from auth import (ADMIN_USERNAME, ensure_users_table, get_role,  # noqa: E402
                  get_user_email, list_users, register, reject_user,
                  set_status, verify_login)
from db import get_engine  # noqa: E402
from llm_client import LLMError, llm_ready  # noqa: E402

st.set_page_config(page_title="淘宝用户行为分析", layout="wide")

# 版式硬性要求（用户指定）：内容限宽 1160px 居中。
# 视觉规范参照 unslop（github.com/heydamir/unslop，CC BY 4.0）：
# 中性纸灰表面 + 发丝线边框 + 单一淘宝橙 accent + 等宽数字，图表即主视觉。
st.markdown(
    "<style>"
    ":root{--paper:#FAFAF9;--paper2:#F5F5F4;--ink:#1C1917;--ink2:#57534E;"
    "--ink3:#78716C;--hair:rgba(28,27,23,.08);--accent:#C2410C;--accent-h:#9A3412;}"
    "[data-testid='stAppViewContainer']{background:var(--paper);}"
    "[data-testid='stHeader']{background:transparent;}"
    "body,.stApp,button,input,select,textarea{font-family:-apple-system,'Segoe UI',"
    "'PingFang SC','Microsoft YaHei',sans-serif;color:var(--ink);}"
    ".block-container{max-width:1160px;padding:1.5rem 1.5rem 4rem;}"
    "/* 侧边栏：中性浅灰 + 右侧发丝线 */"
    "section[data-testid='stSidebar']{background:var(--paper2);"
    "border-right:1px solid var(--hair);}"
    "/* 标题层级：只用字重 600 与墨色，不用粗黑大标题 */"
    "h1,h2,h3,.stMarkdown h1,.stMarkdown h2,.stMarkdown h3{font-weight:600;"
    "color:var(--ink);letter-spacing:-0.01em;}"
    "p,st.caption,[data-testid='stCaptionContainer']{color:var(--ink2);}"
    "/* KPI：等宽数字，关键值用墨色，说明用次级灰 */"
    "[data-testid='stMetric']{border:none;}"
    "[data-testid='stMetricValue']{font-variant-numeric:tabular-nums;"
    "font-weight:600;color:var(--ink);}"
    "[data-testid='stMetricLabel']{color:var(--ink2);font-weight:500;}"
    "[data-testid='stMetricDelta']{font-variant-numeric:tabular-nums;}"
    "/* 卡片/展开框：统一发丝线 + 圆角，无阴影无发光 */"
    "[data-testid='stVerticalBlockBorderWrapper']"
    "{border:1px solid var(--hair)!important;border-radius:12px;"
    "box-shadow:none!important;background:var(--paper);}"
    "[data-testid='stExpander']{border:1px solid var(--hair);border-radius:12px;}"
    "[data-testid='stExpanderDetails']{border:none;}"
    "/* 按钮：主按钮唯一 accent（淘宝橙），其余安静描边 */"
    "[data-testid='baseButton-primary']{background:var(--accent);"
    "border:1px solid var(--accent);color:#fff;box-shadow:none;}"
    "[data-testid='baseButton-primary']:hover{background:var(--accent-h);"
    "border-color:var(--accent-h);color:#fff;}"
    "[data-testid='baseButton-secondary'],[data-testid='baseButton-default']"
    "{border:1px solid var(--hair);color:var(--ink);background:var(--paper);"
    "box-shadow:none;}"
    "[data-testid='baseButton-secondary']:hover,"
    "[data-testid='baseButton-default']:hover{border-color:var(--ink3);}"
    "/* 分隔线与单选/表格 */"
    "hr{border-color:var(--hair);}"
    "[data-testid='stRadio'] label p{color:var(--ink);}"
    "[data-testid='stDataFrame']{border:1px solid var(--hair);border-radius:12px;}"
    "</style>",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------- 渲染小工具
def kpi_row(items: list[dict]) -> None:
    """一行 KPI 卡片：label 大数字 + 小字说明，同构卡片等宽排布。"""
    for col, item in zip(st.columns(len(items), border=True), items):
        with col:
            st.metric(item["label"], item["value"])
            if item.get("note"):
                st.caption(item["note"])


def chart_card(fig, caption: str, alt: str) -> None:
    """图表卡片：边框容器 + 图表 + 居中图注。"""
    with st.container(border=True):
        st.plotly_chart(fig, alt=alt, theme=None, config={"locale": "zh-CN"},
                        use_container_width=True)
        if caption:
            st.caption(caption)


def insight(text: str, icon: str = "insights") -> None:
    """结论提示卡：带图标的边框容器。"""
    with st.container(border=True):
        st.markdown(f":material/{icon}: {text}")


# ---------------------------------------------------------------- 会话状态
st.session_state.setdefault("auth_user", None)
st.session_state.setdefault("auth_role", "user")      # admin / user
st.session_state.setdefault("dataset", None)          # 标准五列 DataFrame
st.session_state.setdefault("dataset_name", "")
st.session_state.setdefault("ai_reports", {})         # {数据指纹: 漏斗AI报告}
st.session_state.setdefault("ai_report_time", {})     # {数据指纹: 报告生成时间}
st.session_state.setdefault("ai_types", {})           # {数据指纹: 分型投放方案列表}


def _store_dataset(df: pd.DataFrame, name: str) -> None:
    st.session_state["dataset"] = df
    st.session_state["dataset_name"] = name
    st.toast(f"数据集「{name}」加载成功：{len(df):,} 条行为记录",
             icon=":material/check_circle:")


# ---------------------------------------------------------------- 登录门
def _render_login_page() -> None:
    """登录 / 注册页。中性纸灰背景，标题即项目名，无多余装饰。"""
    st.markdown(
        "<style>"
        "[data-testid='stAppViewContainer']{background:#FAFAF9 !important;}"
        "[data-testid='stHeader']{background:#FAFAF9 !important;}"
        "</style>",
        unsafe_allow_html=True,
    )
    try:
        _eng = get_engine()
        ensure_users_table(_eng)
    except Exception as e:                                        # noqa: BLE001
        st.error("数据库连接失败，登录页需要 MySQL 可用："
                 f"{type(e).__name__}", icon=":material/error:")
        st.stop()

    st.write("")
    st.write("")
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        st.markdown(
            "<h1 style='text-align:center;font-size:27px;line-height:1.5;"
            "margin-bottom:2px'>淘宝用户漏斗分析</h1>",
            unsafe_allow_html=True,
        )
        st.write("")
        tab_login, tab_reg = st.tabs(["登录", "注册"])

        with tab_login:
            u = st.text_input("用户名", key="login_user")
            p = st.text_input("密码", type="password", key="login_pw")
            if st.button("登 录", key="login_btn", width="stretch", type="primary"):
                if not u or not p:
                    st.warning("请输入用户名和密码", icon=":material/warning:")
                else:
                    ok, reason = verify_login(_eng, u, p)
                    if ok:
                        st.session_state["auth_user"] = u.strip()
                        st.session_state["auth_role"] = get_role(_eng, u.strip())
                        st.session_state["just_logged_in"] = True
                        st.rerun()
                    else:
                        st.error(reason or "用户名或密码错误",
                                 icon=":material/error:")

        with tab_reg:
            ru = st.text_input("用户名", key="reg_user",
                               help="2~32 位字母、数字、下划线或中文")
            re_ = st.text_input("邮箱", key="reg_email",
                                help="用于接收注册审核结果通知")
            rp = st.text_input("设置密码", type="password", key="reg_pw",
                               help="至少 6 位")
            rp2 = st.text_input("确认密码", type="password", key="reg_pw2")
            if st.button("提交注册申请", key="reg_btn", width="stretch"):
                if not ru or not re_ or not rp or not rp2:
                    st.warning("请填写完整（用户名、邮箱、密码）",
                               icon=":material/warning:")
                elif rp != rp2:
                    st.error("两次输入的密码不一致", icon=":material/error:")
                else:
                    ok, msg = register(_eng, ru, rp, re_)
                    if ok:
                        st.success(msg, icon=":material/mark_email_read:")
                        st.caption("审核通过后即可使用该账号登录；"
                                   "期间无需重复提交申请。")
                    else:
                        st.error(msg, icon=":material/error:")

        st.caption("账号说明：注册需管理员审核，通过后系统发送邮件通知；"
                   "账号仅用于看板访问控制，密码经 PBKDF2 哈希存储，不保存明文。")
    st.stop()


if not st.session_state["auth_user"]:
    _render_login_page()

# 登录 / 注册成功后的首帧：注入全屏遮罩，盖住 Streamlit rerun 过程中
# 旧登录页与看板内容短暂同屏的混合帧；看板就绪后由 CSS 动画自动淡出。
if st.session_state.pop("just_logged_in", False):
    st.markdown(
        "<style>@keyframes bootFadeOut{to{opacity:0;visibility:hidden;}}</style>"
        "<div style='position:fixed;inset:0;z-index:9999;background:#FAFAF9;"
        "display:flex;flex-direction:column;align-items:center;justify-content:center;"
        "animation:bootFadeOut .6s ease 2.6s forwards;'>"
        "<div style='font-size:20px;font-weight:600;color:#1C1917;'>"
        "基于淘宝用户行为日志的大数据分析与转化漏斗系统</div>"
        "<div style='margin-top:12px;font-size:14px;color:#78716C;'>"
        "正在加载看板，请稍候…</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- 侧边栏
with st.sidebar:
    _role = st.session_state.get("auth_role", "user")
    st.caption(f":material/person: 当前用户：**{st.session_state['auth_user']}**"
               + ("（管理员）" if _role == "admin" else ""))
    if st.button("退出登录", key="logout_btn", icon=":material/logout:",
                 width="stretch"):
        st.session_state["auth_user"] = None
        st.rerun()
    st.divider()
    st.header(":material/smart_toy: AI 引擎")
    ready, hint = llm_ready()
    st.markdown(f":material/{'check_circle' if ready else 'error'}: {hint}")
    st.divider()
    ds = st.session_state.get("dataset")
    if ds is not None:
        st.header(":material/dataset: 当前数据集")
        st.caption(f"**{st.session_state['dataset_name']}**\n\n"
                   f"{len(ds):,} 条行为 · {ds['user_id'].nunique():,} 名用户 · "
                   f"{ds['dt'].dt.date.nunique()} 天")
    else:
        st.header(":material/dataset: 当前数据集")
        st.caption("尚未上传，请先到「上传数据集」页加载数据。")


# ---------------------------------------------------------------- 计算缓存
@st.cache_data(show_spinner=False)
def cached_agg(df: pd.DataFrame):
    """漏斗/时段/头部类目聚合（按 DataFrame 内容自动缓存）。
    类目附带中文品类名（MySQL category_mapping 映射表，DeepSeek 推测命名）。"""
    return (dl.funnel_stats(df), dl.hourly_stats(df),
            dl.top_categories(df, name_map=dl.load_category_mapping()))


@st.cache_data(show_spinner=False)
def cached_classify(df: pd.DataFrame):
    """用户五类规则分型（推荐类目附中文品类名）。"""
    return uc.classify_users(df, dl.load_category_mapping())


_page_upload: st.Page | None = None  # 「上传数据集」Page 对象，供 st.page_link 跳转


def _need_dataset() -> bool:
    if st.session_state.get("dataset") is None:
        st.warning("尚未加载数据集：请先到「上传数据集」页上传 CSV 或加载示例数据。",
                   icon=":material/upload_file:")
        if _page_upload is not None:
            st.page_link(_page_upload, label="前往上传数据集",
                         icon=":material/upload:")
        return True
    return False


# ================================================================ 页面 1：上传数据集
def page_upload():
    """上传数据集：支持一次多选 CSV（上限 10 份），合并后统一分析。"""
    st.title(":material/upload_file: 上传数据集")
    st.caption("上传要分析的用户行为日志 CSV，**可同时选择多个文件（最多 10 份）**，"
               "系统自动合并为一个完整数据集，分型名单即为全量用户。"
               "列名自动识别（支持常见别名）与行为取值归一化"
               "（pv/fav/cart/buy、数字编码 1/2/3/4、中文均可）。")

    up = st.file_uploader(
        "选择 CSV 文件（可多选）", type=["csv"], key="uploader",
        accept_multiple_files=True,
        help="必需列：用户ID、行为类型、时间；商品ID、类目ID 可选（有则分析更细）。"
             "最多 10 份，超出请分批分析。")
    if up:
        if len(up) > 10:
            st.error(f"一次最多上传 10 份数据集，当前选择了 {len(up)} 份，"
                     "请删减后重试。", icon=":material/error:")
        else:
            parsed, errors = [], []
            with st.spinner(f"正在解析 {len(up)} 份数据集…"):
                for f in up:
                    try:
                        parsed.append((f.name, dl.load_uploaded(f)))
                    except dl.DatasetError as e:
                        errors.append((f.name, str(e)))
            for name, msg in errors:
                st.error(f"「{name}」解析失败：{msg}", icon=":material/error:")
            if parsed:
                sig = tuple((n, len(d)) for n, d in parsed)
                if st.session_state.get("dataset_sig") != sig:
                    combined = pd.concat([d for _, d in parsed],
                                         ignore_index=True)
                    combined.attrs["dropped_rows"] = sum(
                        d.attrs.get("dropped_rows", 0) for _, d in parsed)
                    _store_dataset(combined,
                                   f"合并 {len(parsed)} 份数据集"
                                   if len(parsed) > 1 else parsed[0][0])
                    st.session_state["dataset_sig"] = sig
                if len(parsed) > 1:
                    parts = pd.DataFrame(
                        [{"数据集": n, "有效行为记录": f"{len(d):,}",
                          "用户数": f"{d['user_id'].nunique():,}"}
                         for n, d in parsed])
                    st.dataframe(parts, alt="各分片解析结果一览",
                                 use_container_width=True, hide_index=True)
                    st.caption(f"已合并为 {len(parsed)} 份 · 共 "
                               f"{sum(len(d) for _, d in parsed):,} 条有效记录，"
                               "后续漏斗分析与用户分型均基于合并后的全量数据。")

    st.write("")
    if st.button("没有数据？加载示例数据（MySQL 抽样）",
                 icon=":material/database:", width="stretch"):
        try:
            with st.spinner("从示例库抽样…"):
                _store_dataset(dl.load_sample_from_db(), "示例数据 user_behavior")
        except Exception as e:                                    # noqa: BLE001
            st.error(f"示例数据加载失败：{type(e).__name__}", icon=":material/error:")

    ds = st.session_state.get("dataset")
    if ds is None:
        st.info("上传 CSV 或加载示例数据后，这里将展示数据预览与基础指标。",
                icon=":material/info:")
        return

    st.divider()
    st.subheader(f"数据预览：{st.session_state['dataset_name']}")
    dropped = ds.attrs.get("dropped_rows", 0)
    if dropped:
        st.caption(f"已自动剔除 {dropped:,} 条无效记录（缺失用户/时间或行为值无法识别）。")
    n_users = int(ds["user_id"].nunique())
    kpi_row([
        {"label": "行为记录", "value": f"{len(ds):,}"},
        {"label": "用户数", "value": f"{n_users:,}"},
        {"label": "覆盖天数", "value": str(ds["dt"].dt.date.nunique())},
        {"label": "涉及类目", "value": f"{int(ds['category_id'].nunique()):,}"},
    ])
    st.dataframe(ds.head(30), alt="数据预览：前 30 行标准五列",
                 use_container_width=True, hide_index=True)

    stats, _hourly, _cats = cached_agg(ds)
    st.subheader("基础漏斗指标")
    kpi_row([
        {"label": "整体购买转化率", "value": f"{stats['整体购买转化率%']}%",
         "note": "购买用户 ÷ 全部用户"},
        {"label": "浏览→收藏", "value": f"{stats['浏览→收藏转化率%']}%"},
        {"label": "收藏→加购", "value": f"{stats['收藏→加购转化率%']}%"},
        {"label": "加购→购买", "value": f"{stats['加购→购买转化率%']}%"},
    ])
    insight("数据已就绪。前往「AI 漏斗分析」查看诊断报告，"
            "「AI 分群与投放」生成每类用户的定向推送方案。",
            icon="check_circle")


# ================================================================ 页面 2：AI 漏斗分析
def page_funnel_ai():
    """AI 漏斗分析：本地聚合 + DeepSeek 动态诊断报告。"""
    st.title(":material/query_stats: AI 漏斗分析")
    if _need_dataset():
        return
    ds = st.session_state["dataset"]
    fp = dl.fingerprint(ds)
    stats, hourly, cats = cached_agg(ds)

    st.caption(f"数据集：**{st.session_state['dataset_name']}** · "
               f"{stats['日期范围'][0]} ~ {stats['日期范围'][1]} · "
               "漏斗支持「用户数」与「行为次数」两种口径切换。")

    # ---- 漏斗总览
    st.subheader("漏斗总览")
    unit_mode = st.radio(
        "统计口径", ["用户数（人）", "行为次数（次）"], horizontal=True,
        help="用户数口径：每阶段统计出现过该行为的独立用户数（漏斗分析常用）；"
             "行为次数口径：每阶段统计该类行为发生的总次数。")
    by_events = unit_mode.startswith("行为次数")
    u = stats["事件量漏斗"] if by_events else stats["用户量漏斗"]
    unit = "次" if by_events else "人"
    kpi_row([
        {"label": f"{s}{'次数' if by_events else '用户数'}",
         "value": f"{u[s]:,} {unit}", "note": note}
        for s, note in zip(dl.STAGE_LABELS,
                           ["进入浏览", "标记喜欢", "放入购物车", "付款成交"])
    ])
    conv = [u[dl.STAGE_LABELS[i + 1]] / u[dl.STAGE_LABELS[i]] * 100
            if u[dl.STAGE_LABELS[i]] else 0 for i in range(3)]
    kpi_row([
        {"label": "浏览→收藏", "value": f"{conv[0]:.2f}%"},
        {"label": "收藏→加购", "value": f"{conv[1]:.2f}%"},
        {"label": "加购→购买", "value": f"{conv[2]:.2f}%"},
        {"label": "整体购买转化", "value": f"{stats['整体购买转化率%']}%"},
    ])

    fig_funnel = go.Figure(go.Funnel(
        y=dl.STAGE_LABELS, x=[u[s] for s in dl.STAGE_LABELS],
        texttemplate="%{value:,} " + unit + "（占首层 %{percentInitial}）",
        textposition="inside",
        marker={"color": ["#4E79A7", "#F28E2B", "#E15759", "#59A14F"]}))
    fig_funnel.update_layout(margin=dict(l=60, r=40, t=30, b=40), height=360,
                             yaxis_automargin=True)
    chart_card(fig_funnel,
               f"四阶段{unit_mode[:-3]}漏斗：层内标注{unit}数与相对首层的转化比例。",
               f"用户漏斗图：浏览、收藏、加购、购买（{unit}）")

    # ---- 时段与趋势
    hd = pd.DataFrame([{"时段": f"{h:02d}时", **v} for h, v in hourly.items()])
    fig_hour = px.line(hd, x="时段", y=["pv", "fav", "cart", "buy"])
    fig_hour.for_each_trace(lambda t: t.update(name={"pv": "浏览", "fav": "收藏",
                                                    "cart": "加购", "buy": "购买"}[t.name]))
    fig_hour.update_layout(margin=dict(l=70, r=40, t=30, b=50), height=360,
                           legend_title_text="行为类型", yaxis_title="行为次数",
                           yaxis_automargin=True)
    fig_hour.update_traces(hovertemplate="%{x}<br>%{y:,} 次<extra></extra>")
    chart_card(fig_hour, "24 小时行为分布：浏览与购买峰值时段是推送时机的重要依据。",
               "分时段行为量折线图")

    dd = ds.assign(day=ds["dt"].dt.date.astype(str))
    trend = dd.groupby(["day", "behavior_type"]).size().reset_index(name="次数")
    name_map = {"pv": "浏览", "fav": "收藏", "cart": "加购", "buy": "购买"}
    trend["行为"] = trend["behavior_type"].map(name_map)
    fig_trend = px.line(trend, x="day", y="次数", color="行为", markers=True)
    fig_trend.update_layout(margin=dict(l=70, r=40, t=30, b=50), height=360,
                            xaxis_title="日期", yaxis_automargin=True)
    chart_card(fig_trend, "每日行为趋势：观察活跃度变化与促销窗口。",
               "每日行为趋势折线图")

    if cats:
        cd = pd.DataFrame(cats)
        xcol = "类目名称" if "类目名称" in cd.columns else "类目ID"
        fig_cat = px.bar(cd, x=xcol, y="行为总量", color="购买率%",
                         hover_data=["类目ID", "购买次数"])
        fig_cat.update_layout(
            margin=dict(l=70, r=80, t=30, b=110), height=420,
            xaxis_tickangle=-30, xaxis_automargin=True, yaxis_automargin=True)
        chart_card(fig_cat, f"头部 {len(cats)} 个类目的行为量与购买率。",
                   "头部类目行为量柱状图")

    # ---- AI 诊断报告
    st.divider()
    st.subheader("AI 诊断报告（DeepSeek）")
    ready, hint = llm_ready()
    if not ready:
        st.warning(hint, icon=":material/key_off:")
        return
    report = st.session_state["ai_reports"].get(fp)
    if report is None:
        insight("点击下方按钮，系统会把上面算好的漏斗比例、时段分布与头部类目"
                "**聚合统计**交给 DeepSeek，生成贴合本数据集的动态诊断。",
                icon="auto_awesome")
        if st.button("运行 AI 漏斗分析", type="primary",
                     icon=":material/auto_awesome:", width="stretch"):
            try:
                with st.spinner("DeepSeek 正在分析漏斗数据…（约 20~60 秒）"):
                    report = ai.analyze_funnel(stats, cats, hourly)
                st.session_state["ai_reports"][fp] = report
                st.session_state["ai_report_time"][fp] = \
                    pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
                st.rerun()
            except LLMError as e:
                st.error(f"AI 调用失败：{e}", icon=":material/error:")
        return
    # 正式报告文档样式：边框容器 + 抬头信息 + 正文
    with st.container(border=True):
        gen_time = st.session_state.get("ai_report_time", {}).get(fp)
        meta = f"数据集：{st.session_state['dataset_name']} · {stats.get('总用户数', 0):,} 名用户"
        if gen_time:
            meta += f" · 生成时间 {gen_time}"
        st.caption(meta)
        st.markdown(report)
    st.download_button("下载报告（Markdown）",
                       data=report.encode("utf-8"),
                       file_name="AI漏斗分析报告.md", mime="text/markdown",
                       icon=":material/download:", width="stretch")
    st.caption("报告基于当前数据集生成（已缓存，切换数据集后自动重新分析）。")


# ================================================================ 页面 3：AI 分群与投放
def page_strategy_ai():
    """用户五类分型与定向投放：本地规则分型（人群包可直接执行）+ DeepSeek 具体化。"""
    st.title(":material/campaign: AI 分群与投放策略")
    if _need_dataset():
        return
    ds = st.session_state["dataset"]
    fp = dl.fingerprint(ds)
    stats, _hourly, _cats = cached_agg(ds)

    try:
        udf, tsummary = cached_classify(ds)
    except Exception as e:                                        # noqa: BLE001
        st.error(f"用户分型计算失败：{type(e).__name__}: {e}", icon=":material/error:")
        return

    # ---- 分型总览表（AI 方案生成后追加解读列）
    st.subheader("用户分型总览")
    prof_df = pd.DataFrame([
        {"用户类型": k, **{kk: v[kk] for kk in ("人数", "占比%", "优先级",
                                                "判定口径", "推什么", "投放钩子")}}
        for k, v in tsummary.items()])
    ai_list = st.session_state["ai_types"].get(fp)
    if ai_list:
        read_map = {s["类型"]: s["特征解读"] for s in ai_list}
        prof_df.insert(2, "AI 解读", prof_df["用户类型"].map(read_map))
    st.dataframe(prof_df, alt="用户分型总览表：各类型人数、判定口径与投放钩子",
                 use_container_width=True, hide_index=True)

    # ---- 分型名单（人群包，可直接下载）
    st.subheader("分型名单（人群包）")
    type_icons = {"加购囤积犹豫型": "shopping_cart",
                  "品类专一高转化型": "workspace_premium",
                  "收藏待决型": "bookmarks",
                  "跨类探索低转化型": "travel_explore",
                  "浏览游荡/低活跃型": "visibility",
                  "其他常规用户": "person"}
    for name in uc.TYPE_ORDER:
        sub = udf[udf["用户类型"] == name]
        info = tsummary[name]
        if sub.empty:
            st.caption(f"{name}：当前数据集没有符合口径的用户。")
            continue
        with st.expander(f"{name} · {len(sub):,} 人 · 优先级 {info['优先级']}"
                         " — 预览与下载",
                         icon=f":material/{type_icons[name]}:"):
            st.caption(f"判定口径：{info['判定口径']}。\n\n推什么：{info['推什么']}。"
                       f"\n\n投放钩子：{info['投放钩子']}。")
            cats_top = sub["推荐类目名称"].value_counts().head(8)
            if not cats_top.empty:
                fig_t = px.bar(x=cats_top.index, y=cats_top.values,
                               labels={"x": "推荐类目（中文）", "y": "用户数"})
                fig_t.update_layout(margin=dict(l=70, r=60, t=10, b=10),
                                    height=300, xaxis_tickangle=-30,
                                    xaxis_automargin=True, yaxis_automargin=True)
                st.plotly_chart(fig_t, alt=f"{name}推荐类目分布柱状图",
                                theme=None, use_container_width=True)
            st.dataframe(sub.head(20), alt=f"{name}名单预览（前20行）",
                         use_container_width=True, hide_index=True)
            st.download_button(f"下载「{name}」完整名单 CSV",
                               data=sub.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"人群包_{name}.csv", mime="text/csv",
                               icon=":material/download:", width="stretch",
                               key=f"dl_type_{name}")
    st.download_button("下载全部用户分型名单 CSV",
                       data=udf.to_csv(index=False).encode("utf-8-sig"),
                       file_name="用户五类分型完整名单.csv", mime="text/csv",
                       icon=":material/download:", width="stretch")

    # ---- AI 投放方案
    st.divider()
    st.subheader("AI 投放方案细化（DeepSeek）")
    ready, hint = llm_ready()
    if not ready:
        st.warning(hint, icon=":material/key_off:")
        return
    if ai_list is None:
        insight("点击下方按钮，把各类型的人数、购买率与**推荐类目 Top5**（中文品类名）"
                "交给 DeepSeek，产出具体到品类的投放方案（推什么款、怎么投、何时推）。",
                icon="auto_awesome")
        if st.button("生成 AI 投放方案", type="primary",
                     icon=":material/auto_awesome:", width="stretch"):
            try:
                with st.spinner("DeepSeek 正在为各类型设计具体投放方案…"):
                    _s, _h, cats_all = cached_agg(ds)
                    type_profiles = []
                    for name in uc.TYPE_ORDER:
                        sub = udf[udf["用户类型"] == name]
                        info = tsummary[name]
                        type_profiles.append({
                            "类型": name, "人数": info["人数"],
                            "占比%": info["占比%"], "投放优先级": info["优先级"],
                            "投放钩子": info["投放钩子"],
                            "推荐类目Top5": info["推荐类目Top5"],
                            "购买率%": round((sub["购买次数"] > 0).mean() * 100, 2)
                            if len(sub) else 0.0,
                            "人均行为数": round(sub["行为总数"].mean(), 1)
                            if len(sub) else 0.0,
                        })
                    ai_list = ai.analyze_types(
                        type_profiles, stats,
                        extra={"头部类目（含中文品类名）": cats_all})
                st.session_state["ai_types"][fp] = ai_list
                st.rerun()
            except LLMError as e:
                st.error(f"AI 调用失败：{e}", icon=":material/error:")
            except ValueError as e:
                st.error(f"AI 返回格式异常：{e}", icon=":material/error:")
        return

    for seg in ai_list:
        with st.container(border=True):
            st.markdown(f"#### {seg['类型']}")
            st.markdown(seg["特征解读"])
            c1, c2, c3 = st.columns(3, border=True)
            c1.markdown(f"**推荐商品方向**\n\n{seg['推荐商品方向']}")
            c2.markdown(f"**推送策略**\n\n{seg['推送策略']}")
            c3.markdown(f"**推送时机**\n\n{seg['推送时机']}")
    st.caption("方案基于当前数据集生成（已缓存，切换数据集后自动重新分析）。"
               "推荐类目中文名由 DeepSeek 推测命名，可在 MySQL category_mapping "
               "表人工修正后重新导出人群包。")


# ================================================================ 页面 4：商品库
def page_catalog():
    """商品库：淘宝商品素材库（爬取入库 → 浏览 / 搜索 / 导出 / 清理）。"""
    st.title(":material/storefront: 商品库")
    st.caption("推送计划的「推什么」素材库：按行为量 Top 类目的中文名到淘宝搜索"
               "抓取第一页商品（标题 / 价格 / 销量 / 店铺 / 链接）落库。"
               "抓取由分析会话完成或在此导入 JSON，仅作小批量学习用途。")

    eng = get_engine()
    try:
        pc.ensure_products_table(eng)
    except Exception as e:                                        # noqa: BLE001
        st.error(f"数据库连接失败：{type(e).__name__}", icon=":material/error:")
        return
    stats = pc.catalog_stats(eng)
    kpi_row([
        {"label": "商品总数", "value": f"{stats['商品总数']:,}"},
        {"label": "已覆盖类目", "value": str(stats["类目数"])},
        {"label": "最近抓取", "value": stats["最近抓取"].strftime("%m-%d %H:%M")
         if stats["最近抓取"] is not None else "—"},
    ])

    # ---- 类目概览
    cat_sum = pc.category_summary(eng)
    if not cat_sum.empty:
        st.subheader("类目概览")
        st.dataframe(cat_sum, alt="各类目商品数与价格区间一览",
                     use_container_width=True, hide_index=True)

    # ---- 导入抓取数据（JSON）
    st.divider()
    st.subheader("导入抓取数据")
    st.caption("JSON 格式：记录列表，或含 `data/items/list` 键的对象；"
               "每条记录需有 `title` 与 `url`，可选 `price` / `sales` / `shop`。")
    opts = pc.category_options(eng)
    if opts.empty:
        st.warning("category_mapping 表为空，无法选择类目。", icon=":material/warning:")
        return
    opt_labels = {f"{r.category_name}（{r.category_id}）": (r.category_id, r.category_name)
                  for r in opts.itertuples()}
    sel_label = st.selectbox("归属类目（即搜索词）", list(opt_labels),
                             key="import_cat")
    sel_id, sel_name = opt_labels[sel_label]
    up_json = st.file_uploader("上传 JSON 文件", type=["json"], key="cat_json")
    paste_json = st.text_area("或直接粘贴 JSON", height=120, key="cat_paste",
                              placeholder='[{"title":"…","price":"¥129","sales":"2万+人付款","shop":"…","url":"https://…"}]')
    payload = None
    if up_json is not None:
        try:
            payload = up_json.read().decode("utf-8")
        except Exception as e:                                    # noqa: BLE001
            st.error(f"读取文件失败：{e}", icon=":material/error:")
    if payload is None and paste_json.strip():
        payload = paste_json
    if payload and st.button("导入入库", type="primary",
                             icon=":material/save:", key="import_btn"):
        try:
            res = pc.import_json_file(eng, payload, sel_name, sel_id)
            st.success(f"导入完成：收到 {res['received']} 条，有效 {res['valid']} 条，"
                       f"新入库 {res['inserted']} 条，重复跳过 {res['skipped']} 条。",
                       icon=":material/check_circle:")
            st.rerun()
        except (ValueError, json.JSONDecodeError) as e:
            st.error(f"JSON 解析失败：{e}", icon=":material/error:")

    # ---- 浏览与搜索
    st.divider()
    st.subheader("浏览与搜索")
    c1, c2, c3 = st.columns([1.4, 1.6, 1.2])
    with c1:
        cats = ["全部类目"] + (cat_sum["类目"].tolist() if not cat_sum.empty else [])
        f_cat = st.selectbox("类目", cats, key="browse_cat")
    with c2:
        f_kw = st.text_input("标题 / 店铺关键词", key="browse_kw",
                             placeholder="如：连衣裙 修身")
    with c3:
        f_order = st.radio("排序", ["价格升", "价格降", "销量", "最新抓取"],
                           horizontal=True, key="browse_order")
    order_map = {"价格升": "price", "价格降": "price_desc", "销量": "sales",
                 "最新抓取": "new"}
    df_items = pc.list_by_category(
        eng, None if f_cat == "全部类目" else f_cat,
        keyword=f_kw.strip() or None, order_by=order_map[f_order])
    if df_items.empty:
        st.caption("没有符合条件的商品：先在上方导入抓取数据，或更换筛选条件。")
    else:
        st.dataframe(
            df_items,
            column_config={
                "链接": st.column_config.LinkColumn("链接", display_text="打开商品"),
                "价格": st.column_config.NumberColumn("价格", format="%.2f"),
            },
            alt="商品库列表", use_container_width=True, hide_index=True,
            height=460)
        st.download_button("导出当前结果 CSV",
                           data=df_items.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"商品库_{f_cat}.csv", mime="text/csv",
                           icon=":material/download:", width="stretch",
                           key="dl_catalog")
        st.caption(f"共 {len(df_items):,} 条（列表最多展示 500 条，导出为全量）。")

    # ---- 清理
    st.divider()
    with st.expander("清理类目数据", icon=":material/delete:"):
        st.caption("重新抓取某类目前，可先清空该类目在商品库中的旧数据。")
        del_cat = st.selectbox("选择类目", cat_sum["类目"].tolist(),
                               key="del_cat") if not cat_sum.empty else None
        confirm = st.checkbox("我确认删除该类目全部商品", key="del_confirm")
        if del_cat and confirm and st.button("删除", type="primary",
                                             icon=":material/delete_forever:",
                                             key="del_btn"):
            n = pc.delete_category(eng, del_cat)
            st.toast(f"已删除「{del_cat}」{n} 条商品", icon=":material/check:")
            st.rerun()


# ================================================================ 页面 5：推送计划
def page_push_plan():
    """推送计划：人群包 × 商品库 → 任务单 + 建议发送时机 + 效果回流预留。"""
    st.title(":material/send: 推送计划")
    st.caption("把「AI 分群与投放」的人群包落成可执行任务单：每类人群推什么类目、"
               "推哪款商品、用什么钩子、何时发送。效果回流（打开/点击/成交）"
               "已预留接口，本期不产数据，后续接入发送平台即可回填。")

    eng = get_engine()
    try:
        pp.ensure_push_tasks_table(eng)
    except Exception as e:                                        # noqa: BLE001
        st.error(f"数据库连接失败：{type(e).__name__}", icon=":material/error:")
        return

    # ---- 任务单总览
    tasks = pp.list_tasks(eng)
    kpi_row([
        {"label": "任务总数", "value": f"{len(tasks):,}"},
        {"label": "待发送",
         "value": f"{int((tasks['状态'] == '待发送').sum()):,}"},
        {"label": "已发送",
         "value": f"{int((tasks['状态'] == '已发送').sum()):,}"},
        {"label": "覆盖人群",
         "value": f"{int(tasks['人群人数'].sum()):,}" if not tasks.empty else "0"},
    ])

    # ---- 新建任务（需要数据集）
    st.subheader("新建推送任务")
    if st.session_state.get("dataset") is None:
        st.caption("创建任务需要先在「上传数据集」页加载数据（用于用户分型）。")
        if _page_upload is not None:
            st.page_link(_page_upload, label="前往上传数据集",
                         icon=":material/upload:")
        return

    ds = st.session_state["dataset"]
    udf, tsummary = cached_classify(ds)
    _stats, _hourly, _cats = cached_agg(ds)
    hourly = _hourly
    buy_peak = max(hourly, key=lambda h: hourly[h].get("buy", 0)) \
        if hourly and sum(v.get("buy", 0) for v in hourly.values()) > 0 else None

    present = [t for t in uc.TYPE_ORDER if int(tsummary[t]["人数"]) > 0]
    if not present:
        st.caption("当前数据集没有可投放的人群。")
        return
    t_sel = st.radio("目标人群（按投放优先级排列）", present, horizontal=True,
                     key="push_type",
                     format_func=lambda t: f"{t} · {tsummary[t]['人数']:,} 人 · "
                                           f"优先级 {tsummary[t]['优先级']}")
    sub = udf[udf["用户类型"] == t_sel]
    hook = uc.TYPE_META[t_sel][3]
    st.caption(f"判定口径：{tsummary[t_sel]['判定口径']}。\n\n"
               f"推什么：{tsummary[t_sel]['推什么']}。\n\n"
               f"投放钩子：**{hook}**。")
    st_time = pp.suggest_time(t_sel, buy_peak)

    cat_counts = sub["推荐类目名称"].value_counts()
    if cat_counts.empty:
        st.warning("该人群没有推荐类目，无法创建任务。", icon=":material/warning:")
        return
    c1, c2 = st.columns(2)
    with c1:
        c_sel = st.selectbox("推荐类目（按人群集中度）",
                             [f"{k}（{v:,} 人）" for k, v in cat_counts.items()],
                             key="push_cat")
        c_name = c_sel.split("（")[0]
        cat_id_row = opts_row = None
        try:
            _o = pc.category_options(eng)
            hit = _o[_o["category_name"] == c_name]
            cat_id_row = int(hit.iloc[0]["category_id"]) if not hit.empty else None
        except Exception:                                         # noqa: BLE001
            cat_id_row = None
    with c2:
        prods = pc.list_by_category(eng, c_name, limit=200)
        prod_opts = ["仅品类级推送（不指定具体商品）"] + (
            [f"{r.商品标题}｜¥{r.价格:.0f}" for r in prods.itertuples()]
            if not prods.empty else [])
        p_sel = st.selectbox("推送商品（来自商品库）", prod_opts, key="push_prod")
    p_title = None
    p_id = None
    if not p_sel.startswith("仅品类级") and "｜¥" in p_sel:
        p_title = p_sel.split("｜¥")[0]
        p_id = int(prods.iloc[prod_opts.index(p_sel) - 1]["id"])
    ch_sel = st.radio("渠道", ["App推送", "短信", "信息流", "邮件"], horizontal=True,
                      key="push_channel")
    st.caption(f"建议发送时段：**{st_time}**")

    with st.container(border=True):
        st.markdown("**推送文案预览**")
        demo_title = p_title or f"{c_name}人气好货"
        st.info(f"【{hook}】为你挑选的「{c_name}」心动好物已备好：{demo_title}，"
                "限时优惠等你来抢 →", icon=":material/notifications_active:")

    name_default = f"{t_sel}·{c_name}"
    t_name = st.text_input("任务名称", value=name_default, key="push_name")
    if st.button("创建任务（草稿）", type="primary",
                 icon=":material/add_task:", width="stretch",
                 key="create_task_btn"):
        tid = pp.create_task(eng, t_name or name_default, t_sel,
                             len(sub), category_name=c_name,
                             category_id=cat_id_row, product_id=p_id,
                             product_title=p_title, hook=hook,
                             channel=ch_sel, suggest_time=st_time)
        st.toast(f"任务 #{tid} 已创建（草稿）", icon=":material/check_circle:")
        st.rerun()

    # ---- 任务列表与状态
    st.divider()
    st.subheader("任务单")
    tasks = pp.list_tasks(eng)
    if tasks.empty:
        st.caption("暂无任务，先在上方创建。")
    else:
        st.dataframe(tasks, alt="推送任务单列表",
                     use_container_width=True, hide_index=True, height=300)
        st.download_button("导出任务单 CSV",
                           data=tasks.to_csv(index=False).encode("utf-8-sig"),
                           file_name="推送任务单.csv", mime="text/csv",
                           icon=":material/download:", width="stretch",
                           key="dl_tasks")
        ops_ids = tasks["任务ID"].tolist()
        op_sel = st.selectbox("选择任务",
                              [f"#{i} {tasks.iloc[k]['任务名称']}"
                               for k, i in enumerate(ops_ids)],
                              key="task_op_sel")
        op_id = int(op_sel.split(" ")[0][1:])
        op_status = tasks.iloc[ops_ids.index(op_id)]["状态"]
        b1, b2, b3 = st.columns(3)
        if op_status != "已发送" and b1.button("标记为已发送", key="task_sent_btn"):
            pp.set_task_status(eng, op_id, "sent")
            st.toast(f"任务 #{op_id} 已标记发送", icon=":material/check:")
            st.rerun()
        if op_status == "草稿" and b2.button("转入待发送", key="task_pending_btn"):
            pp.set_task_status(eng, op_id, "pending")
            st.toast(f"任务 #{op_id} 已转入待发送", icon=":material/check:")
            st.rerun()
        if b3.button("删除任务", key="task_del_btn"):
            pp.delete_task(eng, op_id)
            st.toast(f"任务 #{op_id} 已删除", icon=":material/delete:")
            st.rerun()

    # ---- 效果回流（预留接口）
    st.divider()
    st.subheader("效果回流（接口预留）")
    st.caption("发送平台的打开 / 点击 / 成交数据可在此回填（本期为预留接口，"
               "后续接入回调后自动落库，用于计算点击率与成交转化）。")
    fb = pp.feedback_summary(eng)
    if not fb.empty:
        st.dataframe(fb, alt="已回流任务效果汇总", use_container_width=True,
                     hide_index=True)
    sent = tasks[tasks["状态"] == "已发送"] if not tasks.empty else tasks
    if not sent.empty:
        with st.expander("手动回填效果数据", icon=":material/insights:"):
            s_ids = sent["任务ID"].tolist()
            s_sel = st.selectbox("选择已发送任务",
                                 [f"#{i} {sent.iloc[k]['任务名称']}"
                                  for k, i in enumerate(s_ids)],
                                 key="fb_task_sel")
            s_id = int(s_sel.split(" ")[0][1:])
            f1, f2, f3 = st.columns(3)
            o = f1.number_input("打开数", min_value=0, step=1, key="fb_open")
            c = f2.number_input("点击数", min_value=0, step=1, key="fb_click")
            b = f3.number_input("成交数", min_value=0, step=1, key="fb_buy")
            if st.button("回填", type="primary", key="fb_submit",
                         icon=":material/cloud_upload:"):
                pp.update_feedback(eng, s_id, int(o), int(c), int(b))
                st.toast(f"任务 #{s_id} 效果数据已回填", icon=":material/check:")
                st.rerun()


# ---------------------------------------------------------------- 账号管理（仅管理员）
def _smtp_cfg() -> dict | None:
    """读取 SMTP 配置（.streamlit/secrets.toml 的 [smtp] 段），未配置返回 None。"""
    try:
        cfg = dict(st.secrets.get("smtp", {}))
        return cfg or None
    except Exception:                                           # noqa: BLE001
        return None


def page_admin():
    """账号管理：注册审核、冻结/解冻、邮件通知（仅管理员角色可见）。"""
    st.title(":material/manage_accounts: 账号管理")
    st.caption("审核注册申请、冻结或恢复普通用户账号。审核通过后系统会向"
               "申请者登记的邮箱发送通知（需在 `.streamlit/secrets.toml` "
               "配置 `[smtp]` 段）。")
    eng = get_engine()
    ensure_users_table(eng)
    users = list_users(eng)

    n_pending = int((users["status"] == "pending").sum())
    n_frozen = int((users["status"] == "frozen").sum())
    kpi_row([
        {"label": "账号总数", "value": f"{len(users):,}"},
        {"label": "待审核", "value": f"{n_pending:,}"},
        {"label": "已冻结", "value": f"{n_frozen:,}"},
    ])

    st.subheader("注册审核")
    pending = users[users["status"] == "pending"]
    if pending.empty:
        st.caption("暂无待审核的注册申请。")
    for _, r in pending.iterrows():
        c1, c2, c3, c4 = st.columns([1.2, 2.2, 1, 1])
        c1.markdown(f"**{r['username']}**")
        c2.caption(r["email"] or "未登记邮箱")
        if c3.button("通过", key=f"ap_{r['username']}", type="primary",
                     width="stretch"):
            ok, msg = set_status(eng, r["username"], "active")
            if ok:
                email = r["email"] or get_user_email(eng, r["username"])
                sent, err = mailer.send_mail(
                    _smtp_cfg(), email, mailer.approval_subject(r["username"]),
                    mailer.approval_body(r["username"])) if email else \
                    (False, "未登记邮箱")
                if sent:
                    st.toast(f"已通过 {r['username']} 的申请，通知邮件已发送",
                             icon=":material/mail:")
                else:
                    st.warning(f"已通过 {r['username']} 的申请，"
                               f"但邮件未发送：{err}",
                               icon=":material/error:")
                st.rerun()
            else:
                st.error(msg, icon=":material/error:")
        if c4.button("拒绝", key=f"rj_{r['username']}", width="stretch"):
            ok, msg = reject_user(eng, r["username"])
            if ok:
                st.toast(msg, icon=":material/person_remove:")
                st.rerun()
            else:
                st.error(msg, icon=":material/error:")

    st.subheader("全部账号")
    st.dataframe(
        users.assign(role=users["role"].map({"admin": "管理员", "user": "普通用户"}),
                     status=users["status"].map(
                         {"active": "正常", "pending": "待审核",
                          "frozen": "已冻结"}))
        .rename(columns={"username": "用户名", "email": "邮箱", "role": "角色",
                         "status": "状态", "created_at": "创建时间"}),
        alt="全部账号一览表", use_container_width=True, hide_index=True)

    st.subheader("冻结 / 恢复")
    normal = users[(users["role"] != "admin")
                   & (users["status"].isin(["active", "frozen"]))]
    if normal.empty:
        st.caption("暂无可操作的普通用户账号（待审核申请请在上方审核）。")
    else:
        opts = [f"{r['username']}（{'已冻结' if r['status'] == 'frozen' else '正常'}）"
                for _, r in normal.iterrows()]
        sel = st.selectbox("选择账号", opts, key="freeze_sel",
                           label_visibility="collapsed")
        target = normal.iloc[opts.index(sel)]
        frozen = target["status"] == "frozen"
        if st.button("恢复登录" if frozen else "冻结账号",
                     key="freeze_btn",
                     type="primary" if frozen else "secondary"):
            ok, msg = set_status(eng, target["username"],
                                 "active" if frozen else "frozen")
            if ok:
                st.toast(f"{target['username']} {msg}", icon=":material/check:")
                st.rerun()
            else:
                st.error(msg, icon=":material/error:")
    st.caption("冻结后的账号无法登录，登录时会提示“账号已被管理员冻结”。"
               "管理员账号受系统保护，不可冻结或删除。")


# ---------------------------------------------------------------- 页面导航
_page_upload = st.Page(page_upload, title="上传数据集",
                       icon=":material/upload_file:", default=True)
_pages = [
    _page_upload,
    st.Page(page_funnel_ai, title="AI 漏斗分析", icon=":material/query_stats:"),
    st.Page(page_strategy_ai, title="AI 分群与投放", icon=":material/campaign:"),
    st.Page(page_push_plan, title="推送计划", icon=":material/send:"),
    st.Page(page_catalog, title="商品库", icon=":material/storefront:"),
]
if st.session_state.get("auth_role") == "admin":
    _pages.append(st.Page(page_admin, title="账号管理",
                          icon=":material/manage_accounts:"))
pg = st.navigation(_pages)
pg.run()
