#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PNG 取证探针（纯 stdlib；本机无 PIL / ffmpeg，真机读数全靠它）。

为什么在仓库里：UX v2 B1/B2 前两批的取证脚本只活在 scratchpad，且**都没有做反滤波**——
把 IDAT 解压后的字节直接当像素读，只有 `filter type == 0` 的扫描线才碰巧对。症状很好认却
一直被归因成别的：顶栏采集点恒读 `(0,0,0)`（以为坐标错了）、「有打断按钮 vs 无按钮」读出
1316 vs 1217（以为区域选大了）；带 defilter 之后同一判据变成 2158 vs 0、采集点读出
`(104,209,191)` 的青色（B2 计划 §6.2 坑⑨）。

判据面分两类，接手时别搞混：
  · **颜色 / 亮度类**读数必须经本工具（`region` / `px` / `rows`）；
  · **逐字节差异类**读数（同一区域两帧比）不受反滤波影响——同一 filter 作用在两帧上，内容
    相同则字节相同——但本工具的 `diff` 比的是**解码后的像素**，比原来更严，可直接沿用。

用法（`adb exec-out screencap -p -d <displayId> > x.png` 拉图；**不要经 PowerShell 的 `>`**，会损坏）::

    python png_probe.py info   a.png
    python png_probe.py px     a.png X Y
    python png_probe.py region a.png X0 Y0 X1 Y1 [--bright 128]
    python png_probe.py rows   a.png X0 Y0 X1 Y1        # 逐行亮度剖面（找 2dp 细线用）
    python png_probe.py diff   a.png b.png [X0 Y0 X1 Y1]
    python png_probe.py selftest                         # 通道自检：五种 filter 各来一遍

矩形是半开区间 `[x0, x1) × [y0, y1)`，坐标是**原图像素**（不是 dp）。加 `--json` 输出机器可读。

通道自检有两层，取读数前都要做：`selftest` 证明**解码器**没错；判据本身还要拿一张肉眼确认过的
图跑一遍同一条判据（B2 §6.2 坑④：「没看到 X」先证明观测通道开着）。
"""

from __future__ import annotations

import json
import struct
import sys
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}  # 灰 / RGB / 灰+A / RGBA（3=调色板，screencap 不产，未支持）


def _chunks(data: bytes):
    i = 8
    while i + 8 <= len(data):
        (ln,) = struct.unpack(">I", data[i : i + 4])
        typ = data[i + 4 : i + 8]
        yield typ, data[i + 8 : i + 8 + ln]
        i += 12 + ln


def decode(path: str, y_from: int = 0, y_to: int | None = None):
    """解 PNG → (w, h, nch, rows)；rows 是 dict{y: bytes}，只含 [y_from, y_to) 那几行。

    只解需要的行：filter 0/1 的扫描线不依赖上一行，所以从 y_from 往回找最近的一条「链起点」
    开始解即可（整屏 screencap 有 180 万像素，全解要几十秒）。
    """
    raw_file = open(path, "rb").read()
    if raw_file[:8] != PNG_SIG:
        raise SystemExit(f"{path}: 不是 PNG（前 8 字节 {raw_file[:8]!r}）")
    w = h = bd = ct = None
    interlace = 0
    idat = bytearray()
    for typ, body in _chunks(raw_file):
        if typ == b"IHDR":
            w, h, bd, ct, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body[:13])
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
    if w is None:
        raise SystemExit(f"{path}: 没有 IHDR")
    if bd != 8:
        raise SystemExit(f"{path}: 只支持 8-bit（本图 {bd}-bit）")
    if interlace:
        raise SystemExit(f"{path}: 隔行 PNG 未支持")
    if ct not in CHANNELS:
        raise SystemExit(f"{path}: 未支持的颜色类型 {ct}（3=调色板未支持）")
    nch = CHANNELS[ct]
    stride = w * nch
    raw = zlib.decompress(bytes(idat))
    if len(raw) < h * (stride + 1):
        raise SystemExit(f"{path}: IDAT 长度不足（{len(raw)} < {h * (stride + 1)}）——图可能被截断或损坏")

    y_to = h if y_to is None else min(y_to, h)
    y_from = max(0, min(y_from, y_to))
    # 往回找最近的链起点：filter 0(None) / 1(Sub) 不看上一行
    start = 0
    for y in range(y_from, -1, -1):
        if raw[y * (stride + 1)] in (0, 1):
            start = y
            break

    rows: dict[int, bytes] = {}
    prev = bytearray(stride)
    for y in range(start, y_to):
        pos = y * (stride + 1)
        f = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        if f == 1:  # Sub
            for x in range(nch, stride):
                line[x] = (line[x] + line[x - nch]) & 255
        elif f == 2:  # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:  # Average
            for x in range(nch):
                line[x] = (line[x] + (prev[x] >> 1)) & 255
            for x in range(nch, stride):
                line[x] = (line[x] + ((line[x - nch] + prev[x]) >> 1)) & 255
        elif f == 4:  # Paeth
            for x in range(nch):
                line[x] = (line[x] + prev[x]) & 255
            for x in range(nch, stride):
                a = line[x - nch]
                b = prev[x]
                c = prev[x - nch]
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        elif f != 0:
            raise SystemExit(f"{path}: 第 {y} 行 filter type {f} 不合法")
        if y >= y_from:
            rows[y] = bytes(line)
        prev = line
    return w, h, nch, rows


def _rgb(row: bytes, x: int, nch: int):
    o = x * nch
    if nch >= 3:
        return row[o], row[o + 1], row[o + 2]
    v = row[o]
    return v, v, v


def _clamp_rect(w: int, h: int, x0: int, y0: int, x1: int, y1: int):
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(x1, w), min(y1, h)
    if x0 >= x1 or y0 >= y1:
        raise SystemExit(f"空矩形：[{x0},{x1}) × [{y0},{y1}) 在 {w}×{h} 的图上没有像素")
    return x0, y0, x1, y1


def cmd_info(path: str, as_json: bool):
    w, h, nch, _ = decode(path, 0, 1)
    out = {"path": path, "width": w, "height": h, "channels": nch}
    print(json.dumps(out) if as_json else f"{path}: {w}×{h}, {nch} 通道")


def cmd_px(path: str, x: int, y: int, as_json: bool):
    w, h, nch, rows = decode(path, y, y + 1)
    if not (0 <= x < w and y in rows):
        raise SystemExit(f"({x},{y}) 不在 {w}×{h} 内")
    r, g, b = _rgb(rows[y], x, nch)
    print(json.dumps({"x": x, "y": y, "rgb": [r, g, b]}) if as_json else f"({x},{y}) = ({r},{g},{b})")


def cmd_region(path: str, x0: int, y0: int, x1: int, y1: int, bright: int, as_json: bool):
    w, h, nch, rows = decode(path, y0, y1)
    x0, y0, x1, y1 = _clamp_rect(w, h, x0, y0, x1, y1)
    sr = sg = sb = 0
    n = nbright = 0
    for y in range(y0, y1):
        row = rows[y]
        for x in range(x0, x1):
            r, g, b = _rgb(row, x, nch)
            sr += r
            sg += g
            sb += b
            n += 1
            if max(r, g, b) >= bright:
                nbright += 1
    avg = [round(sr / n, 1), round(sg / n, 1), round(sb / n, 1)]
    out = {
        "path": path,
        "rect": [x0, y0, x1, y1],
        "pixels": n,
        "avg_rgb": avg,
        "bright_threshold": bright,
        "bright_pixels": nbright,
        "bright_ratio": round(nbright / n, 4),
    }
    if as_json:
        print(json.dumps(out))
    else:
        print(f"{path} [{x0},{y0})-[{x1},{y1}) {n} px")
        print(f"  平均 RGB = ({avg[0]}, {avg[1]}, {avg[2]})")
        print(f"  亮像素(max(r,g,b)>={bright}) = {nbright} / {n} = {nbright / n:.2%}")


def cmd_rows(path: str, x0: int, y0: int, x1: int, y1: int, as_json: bool):
    """逐行剖面：找 2dp 细线（顶缘极光那类）时，别猜行号——把剖面打出来看哪一行亮。"""
    w, h, nch, rows = decode(path, y0, y1)
    x0, y0, x1, y1 = _clamp_rect(w, h, x0, y0, x1, y1)
    prof = []
    for y in range(y0, y1):
        row = rows[y]
        sr = sg = sb = 0
        for x in range(x0, x1):
            r, g, b = _rgb(row, x, nch)
            sr += r
            sg += g
            sb += b
        n = x1 - x0
        r, g, b = sr / n, sg / n, sb / n
        prof.append({"y": y, "rgb": [round(r, 1), round(g, 1), round(b, 1)], "luma": round(0.299 * r + 0.587 * g + 0.114 * b, 1)})
    if as_json:
        print(json.dumps({"path": path, "x": [x0, x1], "rows": prof}))
    else:
        print(f"{path} x[{x0},{x1}) 逐行剖面：")
        for e in prof:
            print(f"  y={e['y']:5d}  RGB=({e['rgb'][0]:6.1f},{e['rgb'][1]:6.1f},{e['rgb'][2]:6.1f})  luma={e['luma']:6.1f}")


def cmd_diff(a: str, b: str, rect, as_json: bool):
    wa, ha, na, _ = decode(a, 0, 1)
    wb, hb, nb, _ = decode(b, 0, 1)
    if (wa, ha) != (wb, hb):
        raise SystemExit(f"尺寸不同：{a} {wa}×{ha} vs {b} {wb}×{hb}")
    x0, y0, x1, y1 = rect if rect else (0, 0, wa, ha)
    x0, y0, x1, y1 = _clamp_rect(wa, ha, x0, y0, x1, y1)
    ra = decode(a, y0, y1)[3]
    rb = decode(b, y0, y1)[3]
    diff_px = 0
    diff_bytes = 0
    total_px = 0
    maxdev = 0
    for y in range(y0, y1):
        rowa, rowb = ra[y], rb[y]
        for x in range(x0, x1):
            oa, ob = x * na, x * nb
            pa = rowa[oa : oa + na]
            pb = rowb[ob : ob + nb]
            total_px += 1
            if pa != pb:
                diff_px += 1
                for u, v in zip(pa, pb):
                    if u != v:
                        diff_bytes += 1
                        maxdev = max(maxdev, abs(u - v))
    out = {
        "a": a,
        "b": b,
        "rect": [x0, y0, x1, y1],
        "pixels": total_px,
        "diff_pixels": diff_px,
        "diff_ratio": round(diff_px / total_px, 4),
        "diff_bytes": diff_bytes,
        "max_channel_dev": maxdev,
    }
    if as_json:
        print(json.dumps(out))
    else:
        print(f"{a} vs {b}  [{x0},{y0})-[{x1},{y1}) {total_px} px")
        print(f"  不同像素 = {diff_px} / {total_px} = {diff_px / total_px:.2%}（不同字节 {diff_bytes}，单通道最大偏差 {maxdev}）")


def _encode(w: int, h: int, px: list[list[tuple[int, int, int]]], filt: int) -> bytes:
    """自检用的最小编码器：整图用同一种 filter type，好让 selftest 逐种走一遍。"""
    stride = w * 3
    raw = bytearray()
    prev = bytearray(stride)
    for y in range(h):
        line = bytearray()
        for x in range(w):
            line += bytes(px[y][x])
        enc = bytearray(stride)
        for i in range(stride):
            a = line[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if filt == 0:
                pr = 0
            elif filt == 1:
                pr = a
            elif filt == 2:
                pr = b
            elif filt == 3:
                pr = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            enc[i] = (line[i] - pr) & 255
        raw += bytes([filt]) + enc
        prev = line

    def chunk(typ: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + typ + body + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return PNG_SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")


def cmd_selftest():
    """通道自检：造一张已知内容的图，五种 filter 各编一次，解回来必须逐像素相同。

    这是「变异测试的第一条断言是变异真的发生了」的同款：读数之前先证明**仪器**是对的。
    不做这一步，一个只在 filter=0 上正确的解码器会安静地返回看似合理的数（前两批就是这样）。
    """
    import os
    import tempfile

    w, h = 23, 17
    px = [[((x * 11 + y * 7) % 256, (x * 3 + 40) % 256, (255 - y * 13) % 256) for x in range(w)] for y in range(h)]
    ok = True
    tmp = tempfile.mkdtemp(prefix="png_probe_selftest_")
    for filt in range(5):
        path = os.path.join(tmp, f"f{filt}.png")
        open(path, "wb").write(_encode(w, h, px, filt))
        gw, gh, nch, rows = decode(path)
        bad = None
        if (gw, gh, nch) != (w, h, 3):
            bad = f"头不符：{gw}×{gh}×{nch}"
        else:
            for y in range(h):
                for x in range(w):
                    if _rgb(rows[y], x, nch) != px[y][x]:
                        bad = f"({x},{y}) 解出 {_rgb(rows[y], x, nch)}，应为 {px[y][x]}"
                        break
                if bad:
                    break
        print(f"  filter {filt} ({['None', 'Sub', 'Up', 'Average', 'Paeth'][filt]:7s}) : {'PASS' if not bad else 'FAIL — ' + bad}")
        ok = ok and not bad
    # 部分解码（只要中间几行）必须与全解一致——线上读数都走这条路径
    path = os.path.join(tmp, "f4.png")
    part = decode(path, 9, 12)[3]
    full = decode(path)[3]
    same = all(part[y] == full[y] for y in range(9, 12))
    print(f"  部分解码 rows[9,12) 与全解一致          : {'PASS' if same else 'FAIL'}")
    ok = ok and same
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    bright = 128
    if "--bright" in argv:
        i = argv.index("--bright")
        bright = int(argv[i + 1])
        del argv[i : i + 2]
    cmd = argv[1]
    try:
        if cmd == "info":
            cmd_info(argv[2], as_json)
        elif cmd == "px":
            cmd_px(argv[2], int(argv[3]), int(argv[4]), as_json)
        elif cmd == "region":
            cmd_region(argv[2], *(int(v) for v in argv[3:7]), bright=bright, as_json=as_json)
        elif cmd == "rows":
            cmd_rows(argv[2], *(int(v) for v in argv[3:7]), as_json=as_json)
        elif cmd == "diff":
            rect = tuple(int(v) for v in argv[4:8]) if len(argv) >= 8 else None
            cmd_diff(argv[2], argv[3], rect, as_json)
        elif cmd == "selftest":
            return cmd_selftest()
        else:
            print(__doc__)
            return 2
    except IndexError:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
