# -*- coding: utf-8 -*-
"""临时脚本：按文档顺序导出终版报告中的图片及其显示尺寸。"""
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

ROOT = Path(__file__).resolve().parent
src = ROOT.parent / "实训报告_基于淘宝用户行为日志的大数据分析与转化漏斗系统_终版.docx"
out_dir = ROOT / "media"
out_dir.mkdir(exist_ok=True)

doc = Document(str(src))
# rel id -> image part
rels = {rid: rel for rid, rel in doc.part.rels.items() if "image" in rel.reltype}

order = 1
body = doc.element.body
for child in body.iterchildren():
    if child.tag != qn("w:p"):
        continue
    p = Paragraph(child, doc)
    drawings = child.findall(".//" + qn("w:drawing"))
    for d in drawings:
        blip = d.find(".//" + qn("a:blip"))
        ext = d.find(".//" + qn("wp:extent"))
        if blip is None:
            continue
        rid = blip.get(qn("r:embed"))
        rel = rels.get(rid)
        if rel is None:
            continue
        part = rel.target_part
        ext_name = Path(part.partname).suffix or ".png"
        fn = out_dir / f"img_{order:02d}{ext_name}"
        fn.write_bytes(part.blob)
        cx = int(ext.get("cx")) if ext is not None else 0
        cy = int(ext.get("cy")) if ext is not None else 0
        print(f"{order:02d} {rid} {part.partname} {len(part.blob)}B display={cx/914400*2.54:.1f}x{cy/914400*2.54:.1f}cm -> {fn.name}")
        order += 1
