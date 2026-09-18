"""真机「文字上屏节奏」取证（2026-09-18，设计 docs/design/2026-09-18-android-stream-text-pacing.md §2 / §5）。

只用 adb，不用 Maestro；OPPO PEUM00（role=test）实测边界：
  · ColorOS 的 `screenrecord` 直接段错误（rc=139）、`settings put global animator_duration_scale` 被 WRITE_SECURE_SETTINGS 挡，
    所以视觉节奏用 **`dumpsys gfxinfo <pkg> framestats`** 轮询 + 按 IntendedVsync 去重——前提是 App 内「减少动效」强制开
    （`set_switch.py reduceMotionForce true`，光球 / 光标 / 思考点全静帧），这样每一帧都是内容变化；跑完记得改回 false。
  · 中文输入：长按历史里的用户气泡（App 长按 = 复制正文）→ 点输入框 → KEYCODE_PASTE（279）；回读 `composer-input` 的 text 判粘贴落没落。
  · `uiautomator dump` 在对话页偶发拿不到 idle，重试即可；Maestro driver 与 uiautomator 互斥，dump 前先 force-stop dev.mobile.maestro。
  · RN 新架构 JS 线程叫 `mqt_v_js`，同名有十来个（worklet 等 runtime），只认 TIME+ 非零那条。

用法（设备按 mobile_device.ps1 的 test 角色，或传 --serial）：
    python mobile/e2e/tools/stream_cadence_probe.py ui                       # 打印可见节点（text / resource-id / bounds）
    python mobile/e2e/tools/stream_cadence_probe.py prep "给我讲一个很长的故事。"   # 从历史复制这句、粘进输入框并回读
    python mobile/e2e/tools/stream_cadence_probe.py frames baseline 32       # 点发送，采 32s framestats + top -H，打印帧间隔与 JS 线程占用
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

PKG = "com.xiaozhou.companion"


def _adb_bin() -> str:
    home = os.environ.get("ANDROID_HOME")
    if home:
        cand = os.path.join(home, "platform-tools", "adb.exe")
        if os.path.exists(cand):
            return cand
    return "adb"


def _pick_device() -> str:
    out = subprocess.run([_adb_bin(), "devices"], capture_output=True, text=True, timeout=60).stdout
    for line in out.splitlines()[1:]:
        if not line.strip().endswith("device"):
            continue
        serial = line.split()[0]
        maker = subprocess.run([_adb_bin(), "-s", serial, "shell", "getprop", "ro.product.manufacturer"],
                               capture_output=True, text=True, timeout=30).stdout.strip()
        if maker.upper() == "OPPO":
            return serial
    raise SystemExit("no OPPO (role=test) device attached")


class Device:
    def __init__(self, serial: str):
        self.serial = serial
        self.tmp = tempfile.mkdtemp(prefix="stream-cadence-")

    def adb(self, *args: str, check: bool = True, timeout: int = 90) -> str:
        r = subprocess.run([_adb_bin(), "-s", self.serial, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args)} failed: {(r.stderr or '').strip()[:200]}")
        return r.stdout or ""

    def shell(self, *args: str, **kw) -> str:
        return self.adb("shell", *args, **kw)

    def dump(self) -> ET.Element:
        self.shell("am", "force-stop", "dev.mobile.maestro", check=False)
        last = ""
        for _ in range(4):
            r = subprocess.run([_adb_bin(), "-s", self.serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            last = (r.stdout or "") + (r.stderr or "")
            if "dumped" in last:
                break
            time.sleep(1.5)
        else:
            raise RuntimeError("uiautomator dump failed: " + last.strip()[:200])
        local = os.path.join(self.tmp, "ui.xml")
        self.adb("pull", "/sdcard/ui.xml", local)
        return ET.parse(local).getroot()

    def pid(self) -> str:
        return self.shell("pidof", PKG, check=False).strip()

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def long_press(self, x: int, y: int, ms: int = 900) -> None:
        self.shell("input", "swipe", str(x), str(y), str(x), str(y), str(ms))


def _center(node: ET.Element) -> tuple[int, int]:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds") or "")
    if not m:
        raise RuntimeError("node without bounds")
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _find_text(root: ET.Element, needle: str) -> ET.Element | None:
    return next((n for n in root.iter("node") if needle in (n.get("text") or "")), None)


def _find_id(root: ET.Element, rid: str) -> ET.Element | None:
    return next((n for n in root.iter("node") if (n.get("resource-id") or "") == rid), None)


def cmd_ui(dev: Device) -> None:
    for n in dev.dump().iter("node"):
        t, rid, cd = n.get("text") or "", n.get("resource-id") or "", n.get("content-desc") or ""
        if t.strip() or rid or cd.strip():
            print(f"{n.get('bounds')} rid={rid!r} text={t[:44]!r} desc={cd[:30]!r}")


def cmd_prep(dev: Device, text: str) -> None:
    if not dev.pid():
        dev.shell("am", "start", "-a", "android.intent.action.VIEW", "-d", "xiaozhou:///", check=False)
        time.sleep(7)
    root = dev.dump()
    node = _find_text(root, text)
    for _ in range(6):
        if node is not None:
            break
        dev.shell("input", "swipe", "540", "700", "540", "1500", "400")   # 露出更早的记录
        time.sleep(1.2)
        root = dev.dump()
        node = _find_text(root, text)
    if node is None:
        raise SystemExit("user bubble not found in history")
    x, y = _center(node)
    dev.long_press(x, y)                       # App：长按 = 复制正文
    time.sleep(1.0)
    root = dev.dump()
    comp = _find_id(root, "composer-input")
    if comp is None:
        raise SystemExit("composer-input not found")
    cx, cy = _center(comp)
    dev.tap(cx, cy)
    time.sleep(0.8)
    dev.shell("input", "keyevent", "279")     # KEYCODE_PASTE
    time.sleep(1.0)
    comp = _find_id(dev.dump(), "composer-input")
    got = (comp.get("text") or "") if comp is not None else ""
    print("composer text:", repr(got))
    if got.strip() != text.strip():
        raise SystemExit("paste did not land")


def _parse_framestats(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    in_table, cols = False, None
    for line in text.splitlines():
        if line.startswith("---PROFILEDATA---"):
            in_table, cols = not in_table, None
            continue
        if not in_table:
            continue
        parts = line.strip().split(",")
        if cols is None:
            cols = parts
            continue
        if len(parts) < len(cols) or not parts[0].isdigit():
            continue
        row = dict(zip(cols, parts))
        try:
            out.append((int(row["IntendedVsync"]), int(row["FrameCompleted"])))
        except (KeyError, ValueError):
            continue
    return out


def cmd_frames(dev: Device, label: str, seconds: int) -> None:
    send = _find_id(dev.dump(), "composer-send")
    if send is None:
        raise SystemExit("composer-send not found (is there text in the composer?)")
    sx, sy = _center(send)
    pid = dev.pid()
    if not pid:
        raise SystemExit("app not running")
    top_path = os.path.join(dev.tmp, f"top_{label}.txt")
    with open(top_path, "w", encoding="utf-8") as topf:
        top = subprocess.Popen([_adb_bin(), "-s", dev.serial, "shell", "top", "-H", "-b", "-d", "1", "-n", str(seconds), "-p", pid],
                               stdout=topf, stderr=subprocess.DEVNULL)
        dev.shell("dumpsys", "gfxinfo", PKG, "reset", check=False)
        time.sleep(1.0)
        frames: dict[int, int] = {}
        t0 = time.time()
        dev.tap(sx, sy)
        print(f"[{label}] send tapped; polling framestats for {seconds}s …")
        while time.time() - t0 < seconds:
            for iv, fc in _parse_framestats(dev.shell("dumpsys", "gfxinfo", PKG, "framestats", check=False)):
                frames[iv] = fc
            time.sleep(0.35)
        top.wait(timeout=seconds + 30)
    ts = sorted(frames.values())
    gaps = [(b - a) / 1e6 for a, b in zip(ts, ts[1:])]
    if gaps:
        g = sorted(gaps)
        print(f"[{label}] frames={len(ts)} span={(ts[-1] - ts[0]) / 1e9:.2f}s gap p50={g[len(g) // 2]:.0f}ms "
              f"p90={g[int(len(g) * 0.9)]:.0f}ms max={g[-1]:.0f}ms gaps>25ms={sum(1 for x in gaps if x > 25)}")
        print(f"[{label}] inter-frame gaps ms: {' '.join(f'{x:.0f}' for x in gaps[:400])}")
    js: list[float] = []
    for line in open(top_path, encoding="utf-8", errors="replace"):
        c = line.split()
        if len(c) >= 12 and c[0].isdigit() and c[11] == "mqt_v_js" and c[10] != "0:00.00":
            try:
                js.append(float(c[8]))
            except ValueError:
                pass
    print(f"[{label}] main JS thread %CPU per second: {js}")
    print(f"[{label}] raw top log: {top_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", default="")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ui")
    p = sub.add_parser("prep")
    p.add_argument("text")
    f = sub.add_parser("frames")
    f.add_argument("label")
    f.add_argument("seconds", type=int, nargs="?", default=30)
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    dev = Device(a.serial or _pick_device())
    if a.cmd == "ui":
        cmd_ui(dev)
    elif a.cmd == "prep":
        cmd_prep(dev, a.text)
    else:
        cmd_frames(dev, a.label, a.seconds)


if __name__ == "__main__":
    main()
