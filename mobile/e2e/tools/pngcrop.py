"""纯 stdlib PNG 裁剪：解 IDAT + 五种 filter 反滤波 → 取矩形 → 重新编码为 RGB8 PNG。

用途：5 人小样本要的画廊截图必须裁掉「标题行（primary=xxx）」与「判据行（transport=... capture=...）」——
那两行直接把答案写在图上，不裁就等于把答案发给受试者。
"""
import struct, zlib, sys, os


def _decode(path):
    raw = open(path, "rb").read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG: " + path
    pos, idat, w = 8, [], None
    while pos < len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        typ = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, color = struct.unpack(">IIBB", data[:10])
            assert depth == 8, "only 8-bit supported"
            ch = {0: 1, 2: 3, 4: 2, 6: 4}[color]
        elif typ == b"IDAT":
            idat.append(data)
        elif typ == b"IEND":
            break
        pos += 12 + ln
    buf = zlib.decompress(b"".join(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = buf[p]; p += 1
        line = bytearray(buf[p:p + stride]); p += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, out


def crop(src, dst, x0, y0, x1, y1):
    w, h, ch, px = _decode(src)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    cw, chh = x1 - x0, y1 - y0
    assert cw > 0 and chh > 0, "empty crop"
    rows = []
    for y in range(y0, y1):
        base = y * w * ch
        row = bytearray(b"\x00")
        for x in range(x0, x1):
            i = base + x * ch
            row += bytes(px[i:i + 3]) if ch >= 3 else bytes([px[i]] * 3)
        rows.append(bytes(row))
    comp = zlib.compress(b"".join(rows), 9)

    def chunk(typ, data):
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    with open(dst, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", cw, chh, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", comp))
        f.write(chunk(b"IEND", b""))
    return cw, chh


if __name__ == "__main__":
    a = sys.argv[1:]
    cw, chh = crop(a[0], a[1], *[int(v) for v in a[2:6]])
    print("%s -> %s  %dx%d  %d bytes" % (os.path.basename(a[0]), os.path.basename(a[1]), cw, chh, os.path.getsize(a[1])))
