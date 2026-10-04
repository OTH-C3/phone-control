#!/usr/bin/env python3
"""截图像素分析：定位横线、选项框、状态色块等【位置】。

注意：这个脚本【不读内容】，只告诉你东西在【哪】。
要读内容用 probe.py（uiautomator dump），那是首选。
只有 dump 拿不到内容时，才用这个 + 截图视觉读取。

用法:
  pixels.py shot.png                 整体概览：内容行段 + 连续色块
  pixels.py shot.png --rows          只输出"有内容的横向行段"（找选项/输入框）
  pixels.py shot.png --dots          只输出"状态色块"（找对错/选中/进度标记）
  pixels.py shot.png --crop x0 y0 x1 y1   只分析某个区域里的行

依赖:Pillow
  python3 -m pip install --user Pillow
"""
import argparse
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("缺 Pillow：python3 -m pip install --user Pillow")


def ink_ratio(row, thr=200):
    """一行里"非白像素"的比例。深底色页面把 thr 调高。"""
    tot = len(row)
    if not tot:
        return 0.0
    dark = sum(1 for px in row if sum(px[:3]) < thr * 3)
    return dark / tot


def content_rows(img, step=8, min_ratio=0.01):
    """把图按 step 行采样，找出连续的有内容行段。

    min_ratio 是"这一行有多少比例的像素算有内容"，文字行一般 2%~15%。
    别把它设成 0.5（半行全黑）——那就永远只命中深色大色块。
    """
    w, h = img.size
    px = img.load()
    flags = []
    for y in range(0, h, step):
        row = [px[x, y] for x in range(0, w, step)]
        flags.append(ink_ratio(row) > min_ratio)
    runs, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if (i - start) * step >= step:  # 至少一步高
                runs.append((start * step, i * step))
            start = None
    if start is not None:
        runs.append((start * step, len(flags) * step))
    return runs


def color_blocks(img, step=6, minpx=30, tol=40):
    """找图里的蓝/红/绿连续色块，返回中心点（找状态标记、选中态用）。"""
    w, h = img.size
    px = img.load()
    targets = {
        "blue": (33, 150, 243),
        "red": (244, 67, 54),
        "green": (76, 175, 80),
        "teal": (0, 150, 136),   # 常见"选中/正确"青绿
    }
    pts = {k: [] for k in targets}
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y][:3]
            for k, (tr, tg, tb) in targets.items():
                if abs(r - tr) < tol and abs(g - tg) < tol and abs(b - tb) < tol:
                    pts[k].append((x, y))
                    break
    out = []
    for k, arr in pts.items():
        if len(arr) < minpx:
            continue
        # 简单聚类：容差内并一簇
        clusters = []
        for (x, y) in arr:
            for c in clusters:
                if abs(c["cy"] - y) < 60 * step and abs(c["cx"] - x) < 70 * step:
                    c["n"] += 1
                    c["sx"] += x
                    c["sy"] += y
                    c["cx"] = c["sx"] // c["n"]
                    c["cy"] = c["sy"] // c["n"]
                    c["maxy"] = max(c["maxy"], y)
                    c["miny"] = min(c["miny"], y)
                    break
            else:
                clusters.append({"n": 1, "sx": x, "sy": y, "cx": x, "cy": y,
                                 "miny": y, "maxy": y})
        for c in clusters:
            if c["n"] >= minpx:
                out.append((k, c["cx"], c["cy"], c["n"]))
    return sorted(out, key=lambda t: (t[2], t[1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shot")
    ap.add_argument("--rows", action="store_true")
    ap.add_argument("--dots", action="store_true")
    args = ap.parse_args()

    img = Image.open(args.shot).convert("RGB")
    print("尺寸: %dx%d" % img.size)

    if args.rows:
        runs = content_rows(img)
        print("内容行段 %d 段:" % len(runs))
        for i, (y0, y1) in enumerate(runs):
            print("  #%-2d  y %4d ~ %-4d  高 %d" % (i, y0, y1, y1 - y0))
        return

    if args.dots:
        blocks = color_blocks(img)
        print("色块 %d 个:" % len(blocks))
        for kind, x, y, n in blocks:
            print("  %-6s @ (%d,%d)  像素点 %d" % (kind, x, y, n))
        return

    runs = content_rows(img)
    print("内容行段 %d 段:" % len(runs))
    for i, (y0, y1) in enumerate(runs):
        print("  #%-2d  y %4d ~ %-4d  高 %d" % (i, y0, y1, y1 - y0))
    blocks = color_blocks(img)
    print("色块 %d 个:" % len(blocks))
    for kind, x, y, n in blocks:
        print("  %-6s @ (%d,%d)  像素点 %d" % (kind, x, y, n))


if __name__ == "__main__":
    main()
