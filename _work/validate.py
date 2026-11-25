# -*- coding: utf-8 -*-
"""报告格式自检：页面、字体、行距、表格边框、编号、引用与禁用语。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "实训报告_基于淘宝用户行为日志的大数据分析与转化漏斗系统_最终版.docx"

doc = Document(str(DOC))
issues = []
info = []

# 页面设置
sec = doc.sections[0]
info.append(f"sections={len(doc.sections)} cover_page={sec.page_width.cm:.1f}x{sec.page_height.cm:.1f}")
body = doc.sections[-1]
info.append(f"body_margins L{body.left_margin.cm:.2f} R{body.right_margin.cm:.2f} "
            f"T{body.top_margin.cm:.2f} B{body.bottom_margin.cm:.2f}")

# 统计
paras = doc.paragraphs
texts = [p.text for p in paras]
info.append(f"paragraphs={len(paras)} tables={len(doc.tables)} images={len(doc.inline_shapes)}")
info.append(f"chars={sum(len(t) for t in texts)}")

# 标题检查
h1 = [t for t in texts if re.match(r"^[一二三四]、", t)]
h2 = [t for t in texts if re.match(r"^[1-6]\s|^[1-6]、", t)]
h3 = [t for t in texts if re.match(r"^[1-6]\.\d\s", t)]
h4 = [t for t in texts if re.match(r"^[1-6]\.\d\.\d\s", t)]
info.append(f"h1={h1}")
info.append(f"h2_count={len(h2)} h3_count={len(h3)} h4_count={len(h4)}")

# 图/表题
fig_caps = [t for t in texts if re.match(r"^图\d+\.\d+　", t)]
tab_caps = [t for t in texts if re.match(r"^表\d+-\d+　", t)]
info.append(f"figure_captions={len(fig_caps)} table_captions={len(tab_caps)}")
info.append("fig_caps=" + ", ".join(c.split("　")[0] for c in fig_caps))
info.append("tab_caps=" + ", ".join(c.split("　")[0] for c in tab_caps))

# 图表编号连续性
fig_nums = [tuple(map(int, re.match(r"图(\d+)\.(\d+)", c).groups())) for c in fig_caps]
tab_nums = [tuple(map(int, re.match(r"表(\d+)-(\d+)", c).groups())) for c in tab_caps]
for nums, label in ((fig_nums, "图"), (tab_nums, "表")):
    by_chap = {}
    for ch, no in nums:
        by_chap.setdefault(ch, []).append(no)
    for ch, seq in by_chap.items():
        if seq != list(range(1, len(seq) + 1)):
            issues.append(f"{label}编号不连续 chapter{ch}: {seq}")

# 正文引用与参考文献
refs_start = next(i for i, t in enumerate(texts) if t == "6 参考文献")
refs = [t for t in texts[refs_start + 1:] if re.match(r"^\[\d+\]", t)]
ref_nums = set()
for r in refs:
    ref_nums.add(int(re.match(r"^\[(\d+)\]", r).group(1)))
info.append(f"references={len(refs)} nums={sorted(ref_nums)}")
citations = set()
for t in texts[:refs_start]:
    for m in re.finditer(r"\[(\d+)(?:-(\d+))?\]", t):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        citations.update(range(a, b + 1))
info.append(f"citations={sorted(citations)}")
if ref_nums - citations:
    issues.append(f"文献未被正文引用: {sorted(ref_nums - citations)}")
if citations - ref_nums:
    issues.append(f"正文引用无对应文献: {sorted(citations - ref_nums)}")

# 禁用表达
full_text = "\n".join(texts)
for word in ["!", "！", "?", "？", "😀", "赋能", "闭环生态", "抓手", "助力",
             "值得注意的是", "综上所述", "总而言之", "不难发现", "非常", "超级", "简直"]:
    if word in full_text:
        cnt = full_text.count(word)
        issues.append(f"出现禁用表达 {word!r} x{cnt}")

# 段落格式抽查
def run_font(r):
    rPr = r._element.rPr
    ea = ""
    if rPr is not None and rPr.rFonts is not None:
        ea = rPr.rFonts.get(qn("w:eastAsia")) or ""
    return ea, (r.font.size.pt if r.font.size else None), bool(r.font.bold)

samples = {}
for p in paras:
    t = p.text.strip()
    if not t:
        continue
    if t in ("一、实习目的", "二、实习内容", "1 引言", "1.1 研究背景与意义"):
        samples[t] = [(run_font(r), r.text[:12]) for r in p.runs]
    if t.startswith("巩固大数据相关理论"):
        pf = p.paragraph_format
        samples["BODY"] = {
            "first_indent_pt": pf.first_line_indent.pt if pf.first_line_indent else None,
            "line_spacing": pf.line_spacing.pt if hasattr(pf.line_spacing, "pt") else pf.line_spacing,
            "rule": str(pf.line_spacing_rule),
            "align": str(p.alignment),
            "run": [run_font(r) for r in p.runs],
        }
info.append("format_samples=" + repr(samples))
if "BODY" in samples:
    b = samples["BODY"]
    if b["first_indent_pt"] != 24:
        issues.append(f"正文首行缩进异常: {b['first_indent_pt']}")
    if b["line_spacing"] != 20:
        issues.append(f"正文行距异常: {b['line_spacing']}")
    if b["run"][0][0] != "宋体" or b["run"][0][1] != 12:
        issues.append(f"正文字体异常: {b['run']}")

# 表格三线检查
for i, table in enumerate(doc.tables):
    tbl = table._tbl
    tblPr = tbl.tblPr
    borders = tblPr.find(qn("w:tblBorders"))
    if borders is None:
        issues.append(f"表{i}缺少 tblBorders")
        continue
    vals = {b.tag.split("}")[-1]: b.get(qn("w:val")) for b in borders}
    if vals.get("insideV") != "nil" or vals.get("insideH") != "nil":
        issues.append(f"表{i}存在内部框线: {vals}")
    if vals.get("left") != "nil" or vals.get("right") != "nil":
        issues.append(f"表{i}存在左右框线: {vals}")
    # 表头是否有下边线
    first_row = table.rows[0]
    has_header_bottom = False
    for cell in first_row.cells:
        tcPr = cell._tc.tcPr
        if tcPr is not None:
            tcB = tcPr.find(qn("w:tcBorders"))
            if tcB is not None and tcB.find(qn("w:bottom")) is not None:
                has_header_bottom = True
    if not has_header_bottom:
        issues.append(f"表{i}表头缺少下边线")

# 图片尺寸
sizes = []
for sh in doc.inline_shapes:
    sizes.append((round(sh.width.cm, 1), round(sh.height.cm, 1)))
info.append(f"image_sizes={sizes}")

print("\n".join(info))
print("\n=== ISSUES ===")
if issues:
    print("\n".join(f"- {x}" for x in issues))
else:
    print("none")
