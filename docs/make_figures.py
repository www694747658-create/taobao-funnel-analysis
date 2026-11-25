#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成开题报告所需的三张配图。

重要约束：正文单元格可用宽度只有 13.14 cm，图片按 12.5 cm 插入。
如果画布太宽，缩放后图内文字会小到看不清（实测 1060px 宽时只剩 4.7pt）。
因此统一按 **700 px** 画布设计，缩放后字号约 7.5 pt，正好可读。

    fig1_modules.png  功能模块结构图
    fig2_flow.png     系统执行流程图
    fig3_tables.png   数据库表关系图
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OUT = Path(__file__).resolve().parent
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

WHITE = (255, 255, 255)
INK = (29, 29, 31)
SOFT = (108, 108, 113)
FAINT = (140, 140, 145)
LINE = (198, 198, 203)
ACCENT = (0, 113, 227)
BG_SOFT = (240, 246, 253)
BG_GREEN = (240, 250, 243)
BG_WARM = (252, 247, 240)

W = 700          # 统一画布宽度


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_PATH, size)


def center_text(d, xy, s, f, fill=INK):
    x0, y0, x1, y1 = xy
    bb = d.textbbox((0, 0), s, font=f)
    d.text(((x0 + x1 - (bb[2] - bb[0])) / 2 - bb[0],
            (y0 + y1 - (bb[3] - bb[1])) / 2 - bb[1]), s, font=f, fill=fill)


def rbox(d, xy, radius=10, fill=WHITE, outline=LINE, width=2):
    d.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def arrow_down(d, x, y, size=6, color=SOFT):
    d.polygon([(x, y), (x - size, y - size), (x + size, y - size)], fill=color)


def arrow_left(d, x, y, size=6, color=ACCENT):
    d.polygon([(x, y), (x + size, y - size), (x + size, y + size)], fill=color)


# ---------------------------------------------------------------- 图 1
def fig_modules() -> Path:
    """纵向树：顶层系统 -> 四个模块（每个模块内两列列出子功能）。"""
    modules = [
        ("数据准备模块", ["分块读取与内存控制", "脏时间戳识别与剔除",
                          "窗口内活跃用户抽样", "完整轨迹保留与清洗"]),
        ("数据存储与口径模块", ["行为明细表 user_behavior", "会话汇总宽表 session_summary",
                                "元数据表 dataset_meta", "覆盖索引与漏斗视图"]),
        ("漏斗分析模块", ["会话切分（30 分钟）", "三层漏斗指标计算",
                          "严格时序 / 宽松口径", "抽样偏差度量"]),
        ("可视化展示模块", ["转化漏斗图", "分层流失分析",
                            "口径对比与行为分布", "逐日趋势与数据血缘"]),
    ]
    f_root = font(17, bold=True)
    f_mod = font(16, bold=True)
    f_item = font(15)

    root = (110, 18, 590, 68)
    bus_y = 96
    box_top, box_h, box_gap = 122, 100, 16
    H = box_top + len(modules) * box_h + (len(modules) - 1) * box_gap + 22

    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    rbox(d, root, radius=10, fill=BG_SOFT, outline=(142, 176, 220))
    center_text(d, root, "淘宝用户行为日志转化漏斗分析系统", f_root)

    # 顶层 -> 主干
    d.line((W / 2, root[3], W / 2, bus_y), fill=SOFT, width=2)
    d.line((58, bus_y, W / 2, bus_y), fill=SOFT, width=2)

    last_center = box_top + (len(modules) - 1) * (box_h + box_gap) + box_h / 2
    d.line((58, bus_y, 58, last_center), fill=SOFT, width=2)

    for i, (name, items) in enumerate(modules):
        y0 = box_top + i * (box_h + box_gap)
        box = (92, y0, W - 24, y0 + box_h)
        rbox(d, box, radius=10, fill=WHITE, outline=LINE)
        d.line((58, y0 + box_h / 2, 92, y0 + box_h / 2), fill=SOFT, width=2)

        d.text((110, y0 + 12), name, font=f_mod, fill=INK)
        d.line((110, y0 + 38, W - 42, y0 + 38), fill=(232, 232, 236), width=1)

        col_x = (110, 396)
        for j, it in enumerate(items):
            cx = col_x[j // 2]
            cy = y0 + 50 + (j % 2) * 22
            d.ellipse((cx, cy + 5, cx + 5, cy + 10), fill=(168, 168, 173))
            d.text((cx + 12, cy), it, font=f_item, fill=SOFT)

    p = OUT / "fig1_modules.png"
    img.save(p)
    return p


# ---------------------------------------------------------------- 图 2
def fig_flow() -> Path:
    steps = [
        ("原始数据文件 CSV（100,150,807 行 / 3.4 GB）", False),
        ("分块扫描时间戳，识别数据主区间", False),
        ("剔除 44,269 条脏时间戳记录", False),
        ("取首个自然日窗口：活跃用户 706,641 人", False),
        ("按用户均匀抽样 2.459%，得 17,374 人", True),
        ("保留这些用户的完整行为轨迹", False),
        ("清洗并按时间排序：1,999,922 行", False),
        ("会话切分：间隔 > 30 分钟即断开会话", True),
        ("写入 MySQL：明细表 + 会话汇总宽表", False),
        ("漏斗视图计算：会话内严格时序口径", False),
        ("Streamlit + Plotly 看板展示与导出", True),
    ]
    box_h, gap, top = 40, 16, 22
    H = top + len(steps) * (box_h + gap) - gap + 22
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    f = font(16)

    for i, (txt, hl) in enumerate(steps):
        y0 = top + i * (box_h + gap)
        xy = (36, y0, W - 36, y0 + box_h)
        if hl:
            rbox(d, xy, radius=10, fill=BG_SOFT, outline=(142, 176, 220))
        else:
            rbox(d, xy, radius=10, fill=WHITE, outline=LINE)
        center_text(d, xy, txt, f)
        if i < len(steps) - 1:
            cx = W / 2
            d.line((cx, y0 + box_h, cx, y0 + box_h + gap - 5), fill=SOFT, width=2)
            arrow_down(d, cx, y0 + box_h + gap)

    p = OUT / "fig2_flow.png"
    img.save(p)
    return p


# ---------------------------------------------------------------- 图 3
def fig_tables() -> Path:
    H = 430
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    f_name = font(15, bold=True)
    f_col = font(14)
    f_note = font(12)

    # 左：行为明细表
    ub = (36, 34, 300, 340)
    rbox(d, ub, radius=10)
    d.rectangle((36, 34, 300, 74), fill=BG_SOFT)
    d.line((36, 74, 300, 74), fill=LINE, width=2)
    center_text(d, (36, 34, 300, 74), "user_behavior　明细表", f_name)
    cols = ["id　BIGINT　主键", "user_id　INT　用户", "item_id　INT　商品",
            "category_id　INT　类目", "behavior_type　ENUM", "ts　INT　时间戳",
            "dt　DATETIME　北京时间", "session_id　BIGINT　会话"]
    y = 88
    for c in cols:
        d.text((52, y), c, font=f_col, fill=SOFT)
        y += 30

    # 右上：会话汇总宽表
    ss = (400, 34, 664, 306)
    rbox(d, ss, radius=10)
    d.rectangle((400, 34, 664, 74), fill=BG_GREEN)
    d.line((400, 74, 664, 74), fill=LINE, width=2)
    center_text(d, (400, 34, 664, 74), "session_summary　汇总表", f_name)
    cols2 = ["session_id　BIGINT　主键", "user_id　INT　所属用户",
             "t_start / t_end　起止", "events　会话内行为数",
             "has_pv / has_mid / has_buy", "t_pv / t_mid / t_buy　最早",
             "ord_mid / ord_buy　按序"]
    y = 88
    for c in cols2:
        d.text((416, y), c, font=f_col, fill=SOFT)
        y += 30

    # 右下：元数据表
    dm = (400, 322, 664, 396)
    rbox(d, dm, radius=10)
    d.rectangle((400, 322, 664, 356), fill=BG_WARM)
    d.line((400, 356, 664, 356), fill=LINE, width=2)
    center_text(d, (400, 322, 664, 356), "dataset_meta　血缘表", f_name)
    d.text((416, 366), "k　参数名　／　v　参数值", font=f_col, fill=SOFT)

    # 关系连线：两框间距留 100px，标注放在空白处，不会压到字段文字
    d.line((400, 160, 300, 160), fill=ACCENT, width=2)
    arrow_left(d, 300, 160)
    d.text((331, 134), "1 : N", font=f_name, fill=ACCENT)

    p = OUT / "fig3_tables.png"
    img.save(p)
    return p


if __name__ == "__main__":
    for fn in (fig_modules, fig_flow, fig_tables):
        path = fn()
        im = Image.open(path)
        # 按 12.5 cm 插入时的等效字号
        pt = 15 / im.size[0] * 12.5 * 28.35
        print(f"已生成 {path.name}  {im.size[0]}x{im.size[1]}  "
              f"12.5cm 宽时 13px 文字 ≈ {pt:.1f} pt")
