#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
用户五类规则分型（可直接产出投放人群包：推什么类目 + 用什么钩子）

输入只有五列：user_id / item_id / category_id / behavior_type(pv,fav,cart,buy) / dt
分型为互斥规则、按顺序判定（每名用户只落入一类），口径透明可复现：

  0) 浏览游荡/低活跃型   总行为 < 10 次；或浏览占比 ≥ 80%、意向(fav+cart) ≤ 5、
                         无购买且类目分散（浏览类目 ≥ 5 或 top类目占比 < 50%）
                         → 大众爆款/高频低价引流品，低成本信息流触达
  1) 加购囤积犹豫型      加购 ≥ 75分位(≥3) 且 加购→购买率 < 20% 且存在「加购未购」类目
                         → 其加购最多但未购买类目的限时折扣券/满减/降价提醒（ROI 最高，优先投）
  2) 收藏待决型          收藏 ≥ 75分位(≥3) 且 收藏→购买率 < 20%
                         → 收藏类目的相似款对比/降价提醒/库存提醒
                         （人数极少时可并入上一型合并为「高意向未购型」）
  3) 品类专一高转化型    有购买且集中在 ≤2 个类目或常购类目占比 ≥ 60%，
                         且转化不低（加购→购买率 ≥ 20% 或 购买占行为 ≥ 5%）
                         → 同品类新品、升级款、复购套装、品类内关联品；会员专享/复购提醒
  4) 跨类探索低转化型    浏览类目 ≥ 5，且无购买或购买类目熵 ≥ 0.7（分散）
                         → 关联品类组合、爆款引流品、低决策门槛商品；猜你喜欢/新人首单优惠
  5) 其他常规用户        以上均不满足的剩余用户（不强行归类，避免污染画像）
                         → 按大盘热门类目常规推荐

每名用户输出「推荐类目ID/中文名」（加购型=加购最多未购类目、收藏型=收藏最多类目、
品类专一=常购类目、探索型=浏览最多类目、其余=大盘热门类目）+ 投放钩子 + 优先级。
优先级：加购囤积 > 品类专一 > 收藏待决 > 跨类探索 > 浏览游荡 > 其他。

用法
    from user_classifier import classify_users
    user_df, summary = classify_users(behavior_df, name_map=中文名映射)
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LOW_EVENTS = 10          # 总行为次数低于该值 → 低活跃
CART2BUY_MAX = 0.2       # 加购→购买率低于此视为"犹豫"
FAV2BUY_MAX = 0.2        # 收藏→购买率低于此视为"待决"
ENTROPY_HIGH = 0.7       # 购买类目归一化熵高于此视为"分散"
MIN_QTY = 3              # 加购/收藏分位数的绝对下限（避免分位过低失真）

# 类型元数据：名称 → (优先级, 判定口径, 推什么, 投放钩子)
TYPE_META = {
    "加购囤积犹豫型": ("P0", "加购次数多、加购未购类目多、加购→购买率低",
                       "其加购最多但未购买类目的限时折扣券/满减/降价提醒",
                       "限时优惠、库存紧张提示"),
    "品类专一高转化型": ("P1", "购买集中在 1~2 个类目（类目熵低），转化意愿明确",
                       "同品类新品、升级款、复购套装、品类内关联品",
                       "会员专享、复购提醒"),
    "收藏待决型": ("P2", "收藏行为突出、收藏→购买率低、购买次数少",
                   "收藏类目的相似款对比、降价提醒、库存提醒",
                   "降价通知、限时保留"),
    "跨类探索低转化型": ("P3", "浏览类目多、购买类目分散且各类目购买占比低",
                       "关联品类组合、爆款引流品、低决策门槛商品",
                       "猜你喜欢式推荐、新人/首单优惠"),
    "浏览游荡/低活跃型": ("P4", "浏览量大但收藏/加购/购买都低且类目分散，或总行为极少",
                       "大众爆款、高频低价引流品",
                       "低频低成本信息流触达，不建议发大额券"),
    "其他常规用户": ("P5", "不满足以上任一口径的剩余用户（避免强行归类污染画像）",
                   "按大盘热门类目做常规个性化推荐",
                   "常规推荐位触达"),
}
# 页面/导出时的展示顺序（按投放优先级）
TYPE_ORDER = list(TYPE_META)


def _entropy_by_user(cnt: pd.DataFrame) -> pd.Series:
    """购买类目归一化熵（0=全部集中在一个类目，1=均匀分散）。"""
    if cnt.empty:
        return pd.Series(dtype=float)
    tot = cnt.groupby("user_id")["n"].transform("sum")
    p = cnt["n"] / tot

    def _h(x: pd.Series) -> float:
        if len(x) <= 1:
            return 0.0
        return float(-(x * np.log(x)).sum() / np.log(len(x)))

    return p.groupby(cnt["user_id"]).apply(_h)


def _top_cat(sub: pd.DataFrame) -> pd.Series:
    """每名用户行为量最大的类目（并列时取行为更晚出现的）。"""
    if sub.empty:
        return pd.Series(dtype="float64")
    c = sub.groupby(["user_id", "category_id"]).size().reset_index(name="n")
    return (c.sort_values("n", ascending=False)
             .drop_duplicates("user_id").set_index("user_id")["category_id"])


def _cat_label(cid, name_map: dict | None) -> str:
    if pd.isna(cid):
        return "未知类目"
    return (name_map or {}).get(str(int(cid)), f"类目{int(cid)}")


def classify_users(df: pd.DataFrame,
                   name_map: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """
    规则五类分型。返回 (用户级分型表, 分型汇总)。

    user_df 列：用户ID / 用户类型 / 子类 / 行为总数 / 浏览次数 / 收藏次数 /
    加购次数 / 购买次数 / 活跃天数 / 价值评分 / 是否高价值 /
    推荐类目ID / 推荐类目名称 / 建议动作 / 投放钩子 / 推送优先级
    summary：每类 {人数, 占比%, 优先级, 判定口径, 推什么, 投放钩子, 推荐类目Top5}
    """
    from value_scorer import DEFAULT_THRESHOLD, build_features, score_users

    feat = build_features(df)                       # events/fav_cnt/cart_cnt/active_days/top_cat_share/intent_cnt
    by = df.groupby(["user_id", "behavior_type"]).size().unstack(fill_value=0)
    for c in ("pv", "fav", "cart", "buy"):
        if c not in by.columns:
            by[c] = 0
    feat = feat.merge(by[["pv", "fav", "cart", "buy"]].reset_index(),
                      on="user_id", how="left")
    feat[["pv", "fav", "cart", "buy"]] = \
        feat[["pv", "fav", "cart", "buy"]].fillna(0).astype(int)

    # ---- 类目级特征
    d = df.dropna(subset=["category_id"])
    buy_rows = d[d["behavior_type"] == "buy"]
    buy_cnt = buy_rows.groupby(["user_id", "category_id"]).size().reset_index(name="n")
    bought_n = (buy_rows.groupby("user_id")["category_id"].nunique()
                .reindex(feat["user_id"]).fillna(0).to_numpy())
    buy_entropy = _entropy_by_user(buy_cnt).reindex(feat["user_id"]).to_numpy()

    # 常购类目与占比
    if buy_cnt.empty:
        top_buy = pd.Series(dtype="float64")
        top_buy_share = pd.Series(0.0, index=feat["user_id"].to_numpy())
    else:
        top_buy = _top_cat(buy_rows)
        tot = buy_cnt.groupby("user_id")["n"].transform("sum")
        top_share = (buy_cnt.assign(p=buy_cnt["n"] / tot)
                     .sort_values("p", ascending=False)
                     .drop_duplicates("user_id").set_index("user_id")["p"])
        top_buy_share = top_share.reindex(feat["user_id"]).fillna(0.0).to_numpy()

    fav_top = _top_cat(d[d["behavior_type"] == "fav"])
    pv_top = _top_cat(d[d["behavior_type"] == "pv"])

    # 加购最多但未购买的类目（加购囤积型的核心推荐类目）
    cart_rows = d[d["behavior_type"] == "cart"]
    if cart_rows.empty or buy_rows.empty:
        cnb_top = pd.Series(dtype="float64")
    else:
        cart_cnt = (cart_rows.groupby(["user_id", "category_id"]).size()
                    .reset_index(name="n"))
        bought_pairs = (buy_rows[["user_id", "category_id"]].drop_duplicates()
                        .assign(_b=1))
        m = cart_cnt.merge(bought_pairs, on=["user_id", "category_id"], how="left")
        cnb_top = (m[m["_b"].isna()].sort_values("n", ascending=False)
                   .drop_duplicates("user_id").set_index("user_id")["category_id"])

    # 大盘热门类目（低活跃/其他用户的兜底推荐）
    hot_cat = d.groupby("category_id").size().idxmax() if not d.empty else np.nan

    # 浏览过的类目数
    pv_rows = d[d["behavior_type"] == "pv"]
    feat["browsed_cats"] = (pv_rows.groupby("user_id")["category_id"].nunique()
                            .reindex(feat["user_id"]).fillna(0).to_numpy())

    # ---- 比率特征
    feat["pv_share"] = feat["pv"] / feat["events"]
    feat["cart2buy"] = np.minimum(
        np.where(feat["cart"] > 0, feat["buy"] / feat["cart"], 0.0), 1.0)
    feat["fav2buy"] = np.minimum(
        np.where(feat["fav"] > 0, feat["buy"] / feat["fav"], 0.0), 1.0)
    intent = feat["fav"] + feat["cart"]

    q75_cart = max(float(feat["cart"].quantile(0.75)), MIN_QTY)
    q75_fav = max(float(feat["fav"].quantile(0.75)), MIN_QTY)
    cnb_map = cnb_top.reindex(feat["user_id"])

    # ---- 互斥规则（按判定顺序）
    is_low = feat["events"].to_numpy() < LOW_EVENTS
    wander = ((~is_low) & (feat["pv_share"] >= 0.8) & (intent <= 5)
              & (feat["buy"] == 0)
              & ((feat["browsed_cats"] >= 5) | (feat["top_cat_share"] < 0.5))
              ).to_numpy()
    hoard = ((~is_low) & (~wander) & (feat["cart"] >= q75_cart)
             & (feat["cart2buy"] < CART2BUY_MAX) & cnb_map.notna().to_numpy()
             ).to_numpy()
    favp = ((~is_low) & (~wander) & (~hoard) & (feat["fav"] >= q75_fav)
            & (feat["fav2buy"] < FAV2BUY_MAX)).to_numpy()
    focus = ((~is_low) & (~wander) & (~hoard) & (~favp) & (feat["buy"] >= 1)
             & ((bought_n <= 2) | (top_buy_share >= 0.6))
             & ((feat["cart2buy"] >= CART2BUY_MAX)
                | (feat["buy"] / feat["events"] >= 0.05))).to_numpy()
    explore = ((~is_low) & (~wander) & (~hoard) & (~favp) & (~focus)
               & (feat["browsed_cats"] >= 5)
               & ((feat["buy"] == 0)
                  | (np.nan_to_num(buy_entropy, nan=1.0) >= ENTROPY_HIGH))
               ).to_numpy()

    t = np.full(len(feat), "其他常规用户", dtype=object)
    sub = np.full(len(feat), "—", dtype=object)
    t[is_low] = "浏览游荡/低活跃型"; sub[is_low] = "低活跃"
    t[wander] = "浏览游荡/低活跃型"; sub[wander] = "浏览游荡"
    t[hoard] = "加购囤积犹豫型"
    t[favp] = "收藏待决型"
    t[focus] = "品类专一高转化型"
    t[explore] = "跨类探索低转化型"
    feat["用户类型"] = t
    feat["子类"] = sub

    # ---- 推荐类目（每类各有来源；np.where 全量赋值避免掩码长度对齐问题）
    rec = np.full(len(feat), float(hot_cat) if pd.notna(hot_cat) else np.nan)
    rec = np.where(t == "加购囤积犹豫型", cnb_map.to_numpy(), rec)
    rec = np.where(t == "收藏待决型",
                   fav_top.reindex(feat["user_id"]).to_numpy(), rec)
    rec = np.where(t == "品类专一高转化型",
                   top_buy.reindex(feat["user_id"]).to_numpy(), rec)
    rec = np.where(t == "跨类探索低转化型",
                   pv_top.reindex(feat["user_id"]).to_numpy(), rec)
    feat["推荐类目ID"] = pd.Series(rec, index=feat.index).astype("Int64")
    feat["推荐类目名称"] = feat["推荐类目ID"].map(lambda c: _cat_label(c, name_map))

    # ---- 价值评分（保留作参考列，不参与分型）
    scored = score_users(feat, DEFAULT_THRESHOLD)

    meta = TYPE_META
    scored["建议动作"] = scored["用户类型"].map(lambda x: meta[x][2])
    scored["投放钩子"] = scored["用户类型"].map(lambda x: meta[x][3])
    scored["推送优先级"] = scored["用户类型"].map(lambda x: meta[x][0])

    out = scored.rename(columns={
        "user_id": "用户ID", "events": "行为总数", "pv": "浏览次数",
        "fav": "收藏次数", "cart": "加购次数", "buy": "购买次数",
        "active_days": "活跃天数", "value_score": "价值评分",
        "is_high_value": "是否高价值"})[
        ["用户ID", "用户类型", "子类", "行为总数", "浏览次数", "收藏次数",
         "加购次数", "购买次数", "活跃天数", "价值评分", "是否高价值",
         "推荐类目ID", "推荐类目名称", "建议动作", "投放钩子", "推送优先级"]]
    out = out.sort_values(["推送优先级", "价值评分"],
                          ascending=[True, False]).reset_index(drop=True)

    total = len(out)
    summary = {}
    for name in TYPE_ORDER:
        sub_df = out[out["用户类型"] == name]
        summary[name] = {
            "人数": int(len(sub_df)),
            "占比%": round(len(sub_df) / total * 100, 2) if total else 0.0,
            "优先级": meta[name][0],
            "判定口径": meta[name][1],
            "推什么": meta[name][2],
            "投放钩子": meta[name][3],
            "推荐类目Top5": (sub_df["推荐类目名称"].value_counts().head(5).to_dict()
                             if len(sub_df) else {}),
        }
    return out, summary


if __name__ == "__main__":
    # 自检：构造覆盖各类型的模拟日志跑通全流程
    rng = np.random.default_rng(7)
    rows = []
    for uid in range(400):
        kind = uid % 5
        for _ in range(rng.integers(30, 120)):
            if kind == 0:      # 加购囤积：多加购不买
                b = rng.choice(["pv", "fav", "cart", "buy"], p=[.5, .1, .38, .02])
            elif kind == 1:    # 品类专一：集中买一个类目
                b = rng.choice(["pv", "fav", "cart", "buy"], p=[.5, .1, .15, .25])
            elif kind == 2:    # 收藏待决
                b = rng.choice(["pv", "fav", "cart", "buy"], p=[.45, .45, .05, .05])
            elif kind == 3:    # 跨类探索
                b = rng.choice(["pv", "fav", "cart", "buy"], p=[.85, .05, .08, .02])
            else:              # 低活跃/游荡
                b = "pv"
            cat = 100 + (0 if kind == 1 else rng.integers(0, 30))
            rows.append((uid, rng.integers(1, 9999), cat, b,
                         f"2017-11-{rng.integers(25, 31):02d}"))
    demo = pd.DataFrame(rows, columns=["user_id", "item_id", "category_id",
                                       "behavior_type", "dt"])
    udf, summ = classify_users(demo, name_map={"100": "连衣裙"})
    for k, v in summ.items():
        print(k, v["人数"], f"{v['占比%']}%", v["优先级"], v["推荐类目Top5"])
