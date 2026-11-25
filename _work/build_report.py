# -*- coding: utf-8 -*-
"""按模板封面与学术论文格式生成实习报告 docx。"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content import BLOCKS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "_work" / "template.docx"
OUT = ROOT / "实训报告_基于淘宝用户行为日志的大数据分析与转化漏斗系统_最终版_封面修订.docx"

CN_SONG = "宋体"
CN_HEI = "黑体"
CN_KAI = "楷体_GB2312"
EN = "Times New Roman"

TEXT_WIDTH_CM = 16.5  # 21 - 2.5 - 2.0


# ---------------------------------------------------------------- 基础工具
def set_run(run, *, cn=CN_SONG, en=EN, size=12, bold=False, italic=False,
            color=None, underline=False):
    run.font.name = en
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.underline = underline
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), en)
    rFonts.set(qn("w:hAnsi"), en)
    rFonts.set(qn("w:eastAsia"), cn)
    rFonts.set(qn("w:cs"), en)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def fmt_para(p, *, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line=20.0, before=0.0,
             after=0.0, first_indent=0.0, left_indent=0.0, keep_next=False,
             line_rule=WD_LINE_SPACING.EXACTLY):
    pf = p.paragraph_format
    p.alignment = align
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    if line_rule is None:
        pf.line_spacing = None
    else:
        pf.line_spacing = Pt(line)
        pf.line_spacing_rule = line_rule
    pf.first_line_indent = Pt(first_indent)
    pf.left_indent = Pt(left_indent)
    pf.keep_with_next = keep_next
    pf.widow_control = True
    return p


def add_heading(text, level):
    p = doc.add_paragraph()
    if level == 1:
        fmt_para(p, align=WD_ALIGN_PARAGRAPH.CENTER, line=20, before=10, after=6,
                 keep_next=True)
        run = p.add_run(text)
        set_run(run, cn=CN_HEI, size=14, bold=True)
    elif level == 2:
        fmt_para(p, align=WD_ALIGN_PARAGRAPH.LEFT, line=20, before=7, after=3,
                 keep_next=True)
        run = p.add_run(text)
        set_run(run, cn=CN_SONG, size=12, bold=True)
    elif level == 3:
        fmt_para(p, align=WD_ALIGN_PARAGRAPH.LEFT, line=20, before=4, after=1,
                 keep_next=True)
        run = p.add_run(text)
        set_run(run, cn=CN_SONG, size=12, bold=True)
    else:
        fmt_para(p, align=WD_ALIGN_PARAGRAPH.LEFT, line=20, before=2, after=0,
                 keep_next=True)
        run = p.add_run(text)
        set_run(run, cn=CN_SONG, size=12, bold=True)


def add_body(text):
    p = doc.add_paragraph()
    fmt_para(p, align=WD_ALIGN_PARAGRAPH.JUSTIFY, line=20, before=0, after=0,
             first_indent=24)
    run = p.add_run(text)
    set_run(run, cn=CN_SONG, size=12)
    return p


def add_caption_above(text):
    p = doc.add_paragraph()
    fmt_para(p, align=WD_ALIGN_PARAGRAPH.CENTER, line=16, before=8, after=2,
             keep_next=True)
    run = p.add_run(text)
    set_run(run, cn=CN_HEI, size=10.5)
    return p


def add_caption_below(text):
    p = doc.add_paragraph()
    fmt_para(p, align=WD_ALIGN_PARAGRAPH.CENTER, line=12, before=0, after=0)
    run = p.add_run(text)
    set_run(run, cn=CN_SONG, size=10.5)
    return p


def _set_cell_borders_zero(tcPr):
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tcPr.append(borders)


def add_three_line_table(spec):
    headers = spec["headers"]
    rows = spec["rows"]
    widths = spec["widths"]
    aligns = spec.get("aligns", ["center"] * len(headers))
    font_size = spec.get("font", 10.5)

    add_caption_above(spec["caption"])

    table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    tbl = table._tbl
    tblPr = tbl.tblPr
    # 固定宽度
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:w"), str(int(sum(widths) * 567)))
    tblW.set(qn("w:type"), "dxa")
    tblPr.append(tblW)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    # 单元格边距
    mar = OxmlElement("w:tblCellMar")
    for edge, w in (("top", 20), ("left", 80), ("bottom", 20), ("right", 80)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(w))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.append(mar)
    # 三线边框
    old = tblPr.find(qn("w:tblBorders"))
    if old is not None:
        tblPr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        if edge in ("top", "bottom"):
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "12")
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), "000000")
        else:
            el.set(qn("w:val"), "nil")
        borders.append(el)
    tblPr.append(borders)

    # 网格列宽
    grid = tbl.tblGrid
    for i, w in enumerate(widths):
        col = grid.findall(qn("w:gridCol"))[i]
        col.set(qn("w:w"), str(int(w * 567)))

    for r_idx in range(len(rows) + 1):
        row = table.rows[r_idx]
        trPr = row._tr.get_or_add_trPr()
        cant = OxmlElement("w:cantSplit")
        trPr.append(cant)
        if r_idx == 0:
            hdr = OxmlElement("w:tblHeader")
            trPr.append(hdr)
        for c_idx in range(len(headers)):
            cell = row.cells[c_idx]
            cell.width = Cm(widths[c_idx])
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            tcPr = cell._tc.get_or_add_tcPr()
            tcW = OxmlElement("w:tcW")
            tcW.set(qn("w:w"), str(int(widths[c_idx] * 567)))
            tcW.set(qn("w:type"), "dxa")
            tcPr.append(tcW)
            _set_cell_borders_zero(tcPr)
            if r_idx == 0:
                # 表头下边线 0.75pt
                tcB = OxmlElement("w:tcBorders")
                bottom = OxmlElement("w:bottom")
                bottom.set(qn("w:val"), "single")
                bottom.set(qn("w:sz"), "6")
                bottom.set(qn("w:space"), "0")
                bottom.set(qn("w:color"), "000000")
                tcB.append(bottom)
                tcPr.append(tcB)
            text = headers[c_idx] if r_idx == 0 else rows[r_idx - 1][c_idx]
            p = cell.paragraphs[0]
            fmt_para(p, align={"left": WD_ALIGN_PARAGRAPH.LEFT,
                               "center": WD_ALIGN_PARAGRAPH.CENTER,
                               "right": WD_ALIGN_PARAGRAPH.RIGHT}[aligns[c_idx]],
                     line=14, before=0, after=0, first_indent=0,
                     line_rule=WD_LINE_SPACING.EXACTLY)
            run = p.add_run(str(text))
            set_run(run, cn=CN_HEI if r_idx == 0 else CN_SONG,
                    size=font_size, bold=False)
    # 表后留一点空
    if len(rows) <= 11:
        for row in table.rows[:-1]:
            for cell in row.cells:
                for p in cell.paragraphs:
                    p.paragraph_format.keep_with_next = True
    spacer = doc.add_paragraph()
    fmt_para(spacer, align=WD_ALIGN_PARAGRAPH.LEFT, line=6, before=0, after=0,
             line_rule=WD_LINE_SPACING.EXACTLY)


def add_figure(spec):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    pf.first_line_indent = Pt(0)
    pf.keep_with_next = True
    run = p.add_run()
    run.add_picture(str(ROOT / spec["path"]), width=Cm(spec["width"]))
    add_caption_below(spec["caption"])


def add_placeholder(spec):
    p = doc.add_paragraph()
    fmt_para(p, align=WD_ALIGN_PARAGRAPH.CENTER, line=20, before=8, after=2,
             keep_next=True)
    # 浅灰虚线边框，便于识别为占位
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "dashed")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "4")
        el.set(qn("w:color"), "BFBFBF")
        pBdr.append(el)
    pPr.append(pBdr)
    run = p.add_run("【待补图】" + spec["hint"])
    set_run(run, cn=CN_SONG, size=10.5, italic=True, color="808080")
    add_caption_below(spec["caption"])


def add_code(lines):
    for line in lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.0
        pf.first_line_indent = Pt(0)
        pf.left_indent = Pt(24)
        run = p.add_run(line)
        set_run(run, cn=CN_SONG, en="Consolas", size=10)


def add_refs(items):
    for i, item in enumerate(items, 1):
        p = doc.add_paragraph()
        fmt_para(p, align=WD_ALIGN_PARAGRAPH.LEFT, line=18, before=0, after=0,
                 first_indent=0, left_indent=0)
        run = p.add_run(f"[{i}] {item}")
        set_run(run, cn=CN_SONG, size=10.5)


def add_signature(text):
    p = doc.add_paragraph()
    fmt_para(p, align=WD_ALIGN_PARAGRAPH.RIGHT, line=20, before=12, after=0)
    run = p.add_run(text)
    set_run(run, cn=CN_SONG, size=12)


# ---------------------------------------------------------------- 打开模板、清掉正文模板表
doc = Document(str(TEMPLATE))
body = doc.element.body

# 找到“实习起止时间”段落，删除其后的所有内容（保留 sectPr）
cover_end = None
for p in doc.paragraphs:
    if p.text.startswith("实习起止时间"):
        cover_end = p
        break
assert cover_end is not None, "模板封面缺少实习起止时间"
el = cover_end._p.getnext()
while el is not None:
    nxt = el.getnext()
    if el.tag != qn("w:sectPr"):
        body.remove(el)
    el = nxt

# ---------------------------------------------------------------- 封面字段：无边框表格 + 单元格下框线
def _tbl_borders_none(table):
    tblPr = table._tbl.tblPr
    old = tblPr.find(qn("w:tblBorders"))
    if old is not None:
        tblPr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tblPr.append(borders)


def _cell_bottom_border(cell, sz=6):
    tcPr = cell._tc.get_or_add_tcPr()
    tcB = OxmlElement("w:tcBorders")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(sz))
    bottom.set(qn("w:space"), "0")
    bottom.set(qn("w:color"), "000000")
    tcB.append(bottom)
    tcPr.append(tcB)


def _cover_table(rows, widths, row_height_cm, margin_lr=80):
    table = doc.add_table(rows=len(rows), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _tbl_borders_none(table)
    tblPr = table._tbl.tblPr
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:w"), str(int(sum(widths) * 567)))
    tblW.set(qn("w:type"), "dxa")
    tblPr.append(tblW)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    mar = OxmlElement("w:tblCellMar")
    for edge, w in (("top", 0), ("left", margin_lr), ("bottom", 0), ("right", margin_lr)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(w))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.append(mar)
    for i, w in enumerate(widths):
        table.columns[i].width = Cm(w)
        table._tbl.tblGrid.findall(qn("w:gridCol"))[i].set(qn("w:w"), str(int(w * 567)))
    for r_idx, (label, value) in enumerate(rows):
        row = table.rows[r_idx]
        row.height = Cm(row_height_cm)
        trPr = row._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:cantSplit"))
        for c_idx, w in enumerate(widths):
            cell = row.cells[c_idx]
            cell.width = Cm(w)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            tcPr = cell._tc.get_or_add_tcPr()
            tcW = OxmlElement("w:tcW")
            tcW.set(qn("w:w"), str(int(w * 567)))
            tcW.set(qn("w:type"), "dxa")
            tcPr.append(tcW)
            p = cell.paragraphs[0]
            p.alignment = (WD_ALIGN_PARAGRAPH.RIGHT if c_idx == 0
                           else WD_ALIGN_PARAGRAPH.LEFT)
            pf = p.paragraph_format
            pf.space_before = Pt(0)
            pf.space_after = Pt(0)
            pf.line_spacing = 1.0
            pf.first_line_indent = Pt(0)
            pf.left_indent = Pt(0)
            pf.right_indent = Pt(0)
            run = p.add_run(label if c_idx == 0 else value)
            set_run(run, cn=CN_SONG, size=16)
            if c_idx == 1:
                _cell_bottom_border(cell)
    return table


# 删除模板中的字段段落，改为无边框表格；下划线由单元格下框线生成，长度统一
field_keys = ("实 习 题 目", "课 程 名 称", "院（系、部、中心）",
              "专       业", "班       级", "学       号", "姓       名")
field_paras = [p for p in doc.paragraphs if p.text.strip().startswith(field_keys)]
assert len(field_paras) >= 7, "模板封面字段段落不足"
remove_set = {p._p for p in field_paras}
el = field_paras[0]._p
while el is not None and el in remove_set:
    el = el.getnext()
anchor_next = el
for p in field_paras:
    p._element.getparent().remove(p._element)

title_rows = [("实习题目", " 基于淘宝用户行为日志的大数据分析与转化漏斗系统")]
field_rows = [
    ("课程名称", "企业技术实习B"),
    ("院（系、部、中心）", "××××学院"),
    ("专业", "数据科学与大数据技术"),
    ("班级", "大数据"),
    ("学号", "202200000"),
    ("姓名", "铁蛋"),
]
t_title = _cover_table(title_rows, [2.8, 13.7], 1.30, margin_lr=0)
t_fields = _cover_table(field_rows, [5.6, 10.9], 1.12)
gap = doc.add_paragraph()
gap.paragraph_format.space_before = Pt(0)
gap.paragraph_format.space_after = Pt(0)
gap.paragraph_format.line_spacing = Pt(2)
gap.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
if anchor_next is not None:
    anchor_next.addprevious(t_title._tbl)
    anchor_next.addprevious(gap._p)
    anchor_next.addprevious(t_fields._tbl)
else:
    body.append(t_title._tbl)

# 压缩字段表与日期之间的空行，保证封面含起止时间仍为一页
date_para = next(p for p in doc.paragraphs if p.text.startswith("实习起止时间"))
empties = []
el = t_fields._tbl.getnext()
while el is not None and el is not date_para._p:
    if el.tag == qn("w:p"):
        from docx.text.paragraph import Paragraph
        if not Paragraph(el, doc).text.strip():
            empties.append(el)
    el = el.getnext()
for e in (empties[:-1] if len(empties) > 1 else []):
    body.remove(e)

# ---------------------------------------------------------------- 正文分节
section = doc.add_section(WD_SECTION.NEW_PAGE)
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.left_margin = Cm(2.5)
section.right_margin = Cm(2.0)
section.top_margin = Cm(2.5)
section.bottom_margin = Cm(2.5)
section.header_distance = Cm(1.5)
section.footer_distance = Cm(1.5)

# 页码从 1 开始
sectPr = section._sectPr
pgNum = OxmlElement("w:pgNumType")
pgNum.set(qn("w:start"), "1")
pgMar = sectPr.find(qn("w:pgMar"))
if pgMar is not None:
    pgMar.addnext(pgNum)
else:
    sectPr.append(pgNum)

# 页脚页码：居中
footer = section.footer
footer.is_linked_to_previous = False
fp = footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
fp.paragraph_format.space_before = Pt(0)
fp.paragraph_format.space_after = Pt(0)
run = fp.add_run()
set_run(run, cn=CN_SONG, size=9)
fld_begin = OxmlElement("w:fldChar")
fld_begin.set(qn("w:fldCharType"), "begin")
instr = OxmlElement("w:instrText")
instr.set(qn("xml:space"), "preserve")
instr.text = " PAGE "
fld_sep = OxmlElement("w:fldChar")
fld_sep.set(qn("w:fldCharType"), "separate")
txt = OxmlElement("w:t")
txt.text = "1"
fld_end = OxmlElement("w:fldChar")
fld_end.set(qn("w:fldCharType"), "end")
for el in (fld_begin, instr, fld_sep, txt, fld_end):
    run._r.append(el)

# 封面节页脚保持为空
cover_footer = doc.sections[0].footer
cover_footer.is_linked_to_previous = False

# ---------------------------------------------------------------- 渲染正文
for block in BLOCKS:
    kind = block[0]
    if kind == "h1":
        add_heading(block[1], 1)
    elif kind == "h2":
        add_heading(block[1], 2)
    elif kind == "h3":
        add_heading(block[1], 3)
    elif kind == "h4":
        add_heading(block[1], 4)
    elif kind == "p":
        add_body(block[1])
    elif kind == "table":
        add_three_line_table(block[1])
    elif kind == "fig":
        add_figure(block[1])
    elif kind == "placeholder":
        add_placeholder(block[1])
    elif kind == "code":
        add_code(block[1])
    elif kind == "refs":
        add_refs(block[1])
    elif kind == "signature":
        add_signature(block[1])
    else:
        raise ValueError(f"未知块类型: {kind}")

# 文档属性
doc.core_properties.title = "基于淘宝用户行为日志的大数据分析与转化漏斗系统"
doc.core_properties.author = "铁蛋"
doc.core_properties.subject = "企业技术实习B 实习报告"
doc.core_properties.comments = ""

doc.save(str(OUT))
print("saved:", OUT)
