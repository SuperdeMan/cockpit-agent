"""目标尺寸读实（B4-11 / 方案 §6「目标 ≥56dp」）——从 `maestro hierarchy` 的 JSON 里读指定
testID 的 bounds，按 density 换算成 dp，逐个断言 ≥ 阈值。

⚠ **这不是 Accessibility Scanner 的读数**（那个装 APK 要授权，见 T13 步骤 13）。两者分开记：
   Scanner 量的是无障碍树上的可点区域，这里量的是**视觉 bounds**——靠 hitSlop 达标的元素
   （presence-capsule 视觉 26dp）在这里必然 FAIL，那是**读法的限制**不是缺陷，单列说明。

**两种输入都吃**（按文件内容自动分辨）：
  · `maestro hierarchy` 的 JSON；
  · `adb shell uiautomator dump` 的 XML —— B4-13 实测**只有这条路走得通**：
    Maestro 的 driver 在本机装不上（`INSTALL_FAILED_USER_RESTRICTED`，与锁屏无关，
    需设备侧放行），而 uiautomator 把 RN 的 testID 原样映射成 `resource-id`。
    ⚠ uiautomator 要窗口**真的 idle** 才吐完整树：对话页默认只吐到 `ComposeView` 一层
    （3.7KB、零 RN 节点），把「减少动效（强制）」打开之后同一屏就是完整树（B4 §6.2 补取轮⑨）。

用法（Windows 侧一律 PowerShell 发 adb / maestro，CLAUDE.md §6.1）：
  adb shell uiautomator dump /sdcard/ui.xml; adb pull /sdcard/ui.xml
  adb shell wm density        # 取 Physical/Override density
  python target_probe.py ui.xml --density 480 --min 56 composer-orb composer-send

退出码：0 全过 / 1 有不达标 / 2 找不到某个 id（**演员没上场**——先核那一屏真的在，
「没看到 X」的第一种形态，B1 第 4 批坑）。
"""

import json
import re
import sys


def walk(node, out):
    """两种形状都试：{attributes:{resource-id,bounds}, children:[…]} 与扁平 {resource-id,bounds,…}。
    第一次实跑先 `head -40 h.json` 看一眼键名；对不上就改这里，**不要改判据**。"""
    if isinstance(node, dict):
        attrs = node.get('attributes', node)
        if isinstance(attrs, dict):
            rid = attrs.get('resource-id') or attrs.get('resourceId') or ''
            b = attrs.get('bounds')
            if rid and b:
                out.setdefault(str(rid).split('/')[-1], []).append(b)
        for v in node.values():
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)


def dp_of(bounds, density):
    m = re.findall(r'-?\d+', str(bounds))
    if len(m) < 4:
        return 0.0, 0.0
    x1, y1, x2, y2 = (int(v) for v in m[:4])
    k = density / 160.0
    return (x2 - x1) / k, (y2 - y1) / k


def main(argv):
    if len(argv) < 5 or '--density' not in argv:
        print(__doc__)
        return 2
    path = argv[1]
    density = int(argv[argv.index('--density') + 1])
    min_dp = float(argv[argv.index('--min') + 1]) if '--min' in argv else 56.0
    ids = [a for a in argv[2:] if not a.startswith('--') and not a.replace('.', '', 1).isdigit()]
    with open(path, encoding='utf-8') as f:
        raw = f.read()
    found = {}
    if raw.lstrip().startswith('<'):
        # uiautomator XML：resource-id 与 bounds 是同一个 <node> 上的两个属性，
        # 属性顺序不保证，所以先切出每个 node 再各自取，不用「一条正则串起来」
        for node in re.findall(r'<node\b[^>]*>', raw):
            rid = re.search(r'resource-id="([^"]*)"', node)
            b = re.search(r'bounds="([^"]*)"', node)
            if rid and b and rid.group(1):
                found.setdefault(rid.group(1).split('/')[-1], []).append(b.group(1))
    else:
        walk(json.loads(raw), found)
    # 观测通道自检：一个 id 都没抓到 ⇒ 是 walk 认错了键名，不是「全都没上场」
    if not found:
        print('自检失败：整棵树一个 resource-id+bounds 都没抓到 —— 先看 h.json 的键名，改 walk()')
        return 2
    rc = 0
    for tid in ids:
        if tid not in found:
            print('%s: NOT FOUND（演员没上场）' % tid)
            rc = 2 if rc == 0 else rc
            continue
        for b in found[tid]:
            w, h = dp_of(b, density)
            ok = min(w, h) >= min_dp
            print('%s: %.1f×%.1fdp %s (%s)' % (tid, w, h, 'PASS' if ok else 'FAIL', b))
            if not ok:
                rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv))
