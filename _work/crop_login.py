# -*- coding: utf-8 -*-
"""裁剪登录页/注册页截图四周空白，提升报告中的可读性。"""
from pathlib import Path

import numpy as np
from PIL import Image

out_dir = Path("_work/media")
for name, pad in (("img_03", 40), ("img_04", 40)):
    im = Image.open(out_dir / f"{name}.png").convert("RGB")
    a = np.asarray(im)
    corner = a[0, 0].astype(int)
    diff = np.abs(a.astype(int) - corner).sum(axis=2)
    ys, xs = np.where(diff > 24)
    x0, x1 = max(0, xs.min() - pad), min(a.shape[1], xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(a.shape[0], ys.max() + pad)
    cropped = im.crop((x0, y0, x1, y1))
    out = out_dir / f"{name}_crop.png"
    cropped.save(out)
    print(out, cropped.size)
