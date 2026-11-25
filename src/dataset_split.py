#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基于淘宝用户行为日志的大数据分析与转化漏斗系统
================================================================
数据集拆分器：把原始数据集按「用户」切成 N 份全覆盖子数据集

拆分原则（辐射整个原数据集）
---------------------------
* 以 **用户** 为最小拆分单位：每个用户的所有行为记录完整落在同一个
  子数据集里 —— 保证单用户漏斗/分群分析不被割裂；
* 10 份子数据集的 **并集 = 原始数据集全集**（无重叠、无遗漏）；
* 用户按 user_id 排序后轮转分配（user 序号 % N），确定性可复现，
  各份用户数与行为量基本均衡。

数据来源：MySQL user_behavior（清洗后的全量行为日志）。
输出：datasets/split_01.csv … split_10.csv（标准五列，可直接上传看板分析）。

用法：
    python src/dataset_split.py            # 默认 10 份
    python src/dataset_split.py --parts 8  # 自定义份数
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from db import get_engine  # noqa: E402

OUT_DIR = ROOT / "datasets"
CHUNK_ROWS = 500_000
STANDARD_COLS = ["user_id", "item_id", "category_id", "behavior_type", "dt"]


def assign_user_groups(engine, parts: int) -> dict:
    """所有用户按 user_id 排序后轮转分配到 parts 组，返回 {user_id: 组号}。"""
    users = pd.read_sql("SELECT DISTINCT user_id FROM user_behavior ORDER BY user_id",
                        engine)["user_id"]
    groups = (users.rank(method="first").astype(int) - 1) % parts
    return dict(zip(users.astype(str), groups))


def split(parts: int = 10, out_dir: Path = OUT_DIR) -> list[dict]:
    """按用户把 user_behavior 全表拆成 parts 份 CSV，返回各份摘要。"""
    engine = get_engine()
    group_of = assign_user_groups(engine, parts)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 追加写 10 个分片文件；先清掉旧文件保证幂等
    paths = [out_dir / f"split_{i + 1:02d}.csv" for i in range(parts)]
    for p in paths:
        if p.exists():
            p.unlink()

    stats = [{"rows": 0, "users": set()} for _ in range(parts)]
    chunks = pd.read_sql(
        "SELECT user_id, item_id, category_id, behavior_type, dt "
        "FROM user_behavior ORDER BY user_id, dt", engine, chunksize=CHUNK_ROWS)
    for chunk in chunks:
        key = chunk["user_id"].astype(str)
        chunk["__g"] = key.map(group_of)
        for g in range(parts):
            sub = chunk.loc[chunk["__g"] == g, STANDARD_COLS]
            if sub.empty:
                continue
            sub.to_csv(paths[g], mode="a", index=False,
                       header=not stats[g]["rows"])
            stats[g]["rows"] += len(sub)
            stats[g]["users"].update(sub["user_id"].astype(str))

    total_rows = sum(s["rows"] for s in stats)
    summary = []
    for i, (p, s) in enumerate(zip(paths, stats)):
        summary.append({"文件": p.name, "行为数": s["rows"],
                        "用户数": len(s["users"]),
                        "占比%": round(s["rows"] / total_rows * 100, 2)})
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="按用户把原始数据集拆成 N 份全覆盖子数据集")
    ap.add_argument("--parts", type=int, default=10, help="拆分份数（默认 10）")
    args = ap.parse_args()
    print(f"开始拆分：{args.parts} 份，输出目录 {OUT_DIR}")
    rows = split(args.parts)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    total = sum(r["行为数"] for r in rows)
    users = sum(r["用户数"] for r in rows)
    print(f"合计：{total:,} 条行为（应等于全表行数）、{users:,} 名用户（不重叠）")


if __name__ == "__main__":
    main()
