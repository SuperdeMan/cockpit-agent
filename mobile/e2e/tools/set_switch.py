"""设置页开关：按 testID 设值并**回读**（AR06 / A06-2，AR09 取数前置）。

为什么不用 Maestro：冷启动落到 `/settings` 之后，Maestro 的 `waitForAppToSettle` 会卡在
`viewHierarchy` 的 gRPC 上直到 DEADLINE_EXCEEDED（2026-09-10 实测两次，同一形态在
`/capture-status` 上不复现）。而 uiautomator 在设置页是通的（对话页才因为常驻动画拿不到 idle）。

为什么按 **resource-id** 而不是「标签之后第一枚开关」：多行说明会把开关挤出文字带，
按位置配对会点中下一行那枚，**而且回读读到的也是那枚错开关的新值**——看着完全像生效了。
`settings-switch-<settingKey>` 由 SwitchRow 必填参数生成，diagnosticRoutes 有唯一性单测。

判据是**回读**，不是「点过了」。点完读不到目标值就以非零退出。

用法：
    python mobile/e2e/tools/set_switch.py reduceMotionForce true
    python mobile/e2e/tools/set_switch.py reduceTransparency false
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

PKG = "com.xiaozhou.companion"
MAX_SCROLLS = 14


def adb_bin() -> str:
    home = os.environ.get("ANDROID_HOME")
    if home:
        candidate = os.path.join(home, "platform-tools", "adb.exe")
        if os.path.exists(candidate):
            return candidate
    return "adb"


def pick_device() -> str:
    adb = adb_bin()
    listing = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=60).stdout
    for line in listing.splitlines()[1:]:
        if not line.strip().endswith("device"):
            continue
        serial = line.split()[0]
        maker = subprocess.run(
            [adb, "-s", serial, "shell", "getprop", "ro.product.manufacturer"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        if maker.upper() == "OPPO":
            return serial
    raise SystemExit("no OPPO (role=test) device attached")


def sh(serial: str, command: str, timeout: int = 90) -> str:
    return subprocess.run(
        [adb_bin(), "-s", serial, "shell", command],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    ).stdout or ""


def dump_tree(serial: str) -> ET.Element | None:
    sh(serial, "rm -f /sdcard/uiswitch.xml")
    sh(serial, "uiautomator dump /sdcard/uiswitch.xml")
    raw = subprocess.run(
        [adb_bin(), "-s", serial, "exec-out", "cat", "/sdcard/uiswitch.xml"],
        capture_output=True, timeout=90,
    ).stdout
    if not raw or b"<hierarchy" not in raw:
        return None
    try:
        return ET.fromstring(raw.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return None


def find_switch(root: ET.Element, key: str) -> ET.Element | None:
    wanted = f"settings-switch-{key}"
    for node in root.iter("node"):
        if node.get("resource-id", "").endswith(wanted):
            return node
    return None


def center(node: ET.Element) -> tuple[int, int]:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if not match:
        raise SystemExit("switch bounds unreadable")
    x1, y1, x2, y2 = (int(v) for v in match.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in {"true", "false"}:
        print(__doc__)
        return 2
    key, want = sys.argv[1], sys.argv[2] == "true"
    serial = pick_device()

    sh(serial, f"am force-stop {PKG}")
    time.sleep(2)
    sh(serial, 'am start -a android.intent.action.VIEW -d "xiaozhou:///settings"')
    time.sleep(6)

    node = None
    for _ in range(MAX_SCROLLS):
        tree = dump_tree(serial)
        if tree is not None:
            node = find_switch(tree, key)
            if node is not None:
                break
        sh(serial, "input swipe 500 1500 500 700 260")
        time.sleep(0.9)
    if node is None:
        print(f"NOT_FOUND settings-switch-{key}")
        return 1

    before = node.get("checked") == "true"
    print(f"before={before} want={want}")
    if before != want:
        x, y = center(node)
        sh(serial, f"input tap {x} {y}")
        time.sleep(1.2)

    # 回读：设置生效的判据是开关自己的新值
    tree = dump_tree(serial)
    node = find_switch(tree, key) if tree is not None else None
    if node is None:
        print("READBACK_LOST")
        return 1
    after = node.get("checked") == "true"
    print(f"after={after}")
    if after != want:
        print("READBACK_MISMATCH")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
