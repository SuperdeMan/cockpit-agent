"""Android 端 UI/内存性能取数装置（AR09 / A09-2）。

只用 adb 读**设备自己持有的事实**，不改任何设置、不发业务请求、不采集音视频：

  · 帧：`dumpsys gfxinfo <pkg>` 的 janky frames 与 50/90/95/99 分位（先 reset 再跑，读的才是本场景）
  · 内存：`dumpsys meminfo <pkg>` 的 TOTAL PSS
  · 冷启动：`am start -W` 的 TotalTime / WaitTime
  · 上下文：活跃屏的刷新率、density、window size、电量与温度、省电模式、前台 Activity

三条纪律写进实现，不靠使用者记得：

1. **刷新率必须随读数一起出**。60Hz 与 120Hz 的帧预算差一倍，把两者的 janky 比例并排放是
   在比两把不同的尺子。装置直接把活跃屏的 fps 与由它算出的帧预算写进每条记录。
2. **没读到就是 None，不是 0**。gfxinfo 在某些 OEM 上给不出分位数，那时该字段留空，
   不许填 0 —— 0 会被读成「非常快」。
3. **一次运行 = 一个场景 = 一个 JSON**，里面带包身份与设备指纹。换包、换设备、换协议都要另存，
   不许把两次的读数并进一张表。

用法：
    python scripts/probe_ui_perf.py --scenario p0 --label chat-idle-handsfree-off --seconds 180
    python scripts/probe_ui_perf.py --scenario p3 --cycles 30
    python scripts/probe_ui_perf.py --scenario p5 --repeats 10

输出默认落 `%LOCALAPPDATA%\\car-agent\\artifacts\\ar09-ui-perf\\`（仓库外）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PKG = "com.xiaozhou.companion"
ROUTES = {
    "chat": "xiaozhou:///",
    "settings": "xiaozhou:///settings",
    "vehicle": "xiaozhou:///vehicle",
    "map": "xiaozhou:///map",
}


def adb_path() -> str:
    home = os.environ.get("ANDROID_HOME")
    if home:
        candidate = Path(home) / "platform-tools" / "adb.exe"
        if candidate.exists():
            return str(candidate)
    return "adb"


class Device:
    def __init__(self, serial: str) -> None:
        self.serial = serial
        self.adb = adb_path()

    def sh(self, command: str, timeout: int = 60) -> str:
        result = subprocess.run(
            [self.adb, "-s", self.serial, "shell", command],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        return result.stdout or ""


def resolve_test_device() -> Device:
    """角色只认厂商，与 scripts/mobile_device.ps1 同一约定：OPPO = test。"""
    adb = adb_path()
    listing = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=60).stdout
    serials = [line.split()[0] for line in listing.splitlines()[1:] if line.strip().endswith("device")]
    for serial in serials:
        maker = subprocess.run(
            [adb, "-s", serial, "shell", "getprop", "ro.product.manufacturer"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        if maker.upper() == "OPPO":
            return Device(serial)
    raise SystemExit("no OPPO (role=test) device attached")


def active_display_facts(dev: Device) -> dict:
    """活跃屏的刷新率与尺寸。折叠屏上**不能**拿第一块屏顶替——合盖时它 state OFF。"""
    dump = dev.sh("dumpsys display")
    fps: float | None = None
    # 只取还亮着的那块屏的 mActiveSfDisplayMode
    for block in dump.split("DisplayDeviceInfo{"):
        if "state ON" not in block and "committedState ON" not in block:
            continue
        match = re.search(r"renderFrameRate\s*[= ]\s*([0-9.]+)", block)
        if match:
            fps = float(match.group(1))
            break
    if fps is None:
        match = re.search(r"mActiveSfDisplayMode=DisplayMode\{[^}]*refreshRate=([0-9.]+)", dump)
        fps = float(match.group(1)) if match else None
    size = dev.sh("wm size").strip()
    density = dev.sh("wm density").strip()
    return {
        "active_refresh_hz": fps,
        "frame_budget_ms": round(1000.0 / fps, 3) if fps else None,
        "wm_size": size,
        "wm_density": density,
    }


def context(dev: Device) -> dict:
    battery = dev.sh("dumpsys battery")
    pkg = dev.sh(f"dumpsys package {PKG} | grep -E 'versionName|lastUpdateTime'")
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "serial": dev.serial,
        "model": dev.sh("getprop ro.product.model").strip(),
        "android": dev.sh("getprop ro.build.version.release").strip(),
        "battery_level": _int(re.search(r"level:\s*(\d+)", battery)),
        "battery_temp_decic": _int(re.search(r"temperature:\s*(\d+)", battery)),
        "powersave": "powerSaveMode=true" in battery or "  powersave: true" in battery,
        "package_lines": [line.strip() for line in pkg.splitlines() if line.strip()],
        **active_display_facts(dev),
    }


def _int(match: re.Match[str] | None) -> int | None:
    return int(match.group(1)) if match else None


def _float(match: re.Match[str] | None) -> float | None:
    return float(match.group(1)) if match else None


def reset_gfx(dev: Device) -> None:
    dev.sh(f"dumpsys gfxinfo {PKG} reset")


def read_gfx(dev: Device) -> dict:
    """gfxinfo。**读不到的字段留 None**——某些 OEM 不给分位数，填 0 会被读成「非常快」。"""
    text = dev.sh(f"dumpsys gfxinfo {PKG}")
    total = _int(re.search(r"Total frames rendered:\s*(\d+)", text))
    janky = re.search(r"Janky frames:\s*(\d+)\s*\(([0-9.]+)%\)", text)
    # 一帧都没渲时**分位数没有意义**：dumpsys 仍会打印上一段的残值/直方图桶，
    # 照抄下来会让「这一屏空闲时零渲染」看着像「p95 4950ms」。零帧就把分位置空。
    if not total:
        return {
            "total_frames": total,
            "janky_frames": 0 if total == 0 else None,
            "janky_pct": 0.0 if total == 0 else None,
            "p50_ms": None, "p90_ms": None, "p95_ms": None, "p99_ms": None,
            "missed_vsync": _int(re.search(r"Number Missed Vsync:\s*(\d+)", text)),
            "slow_ui_thread": _int(re.search(r"Number Slow UI thread:\s*(\d+)", text)),
            "slow_draw": _int(re.search(r"Number Slow Draw:\s*(\d+)", text)),
            "high_input_latency": _int(re.search(r"Number High input latency:\s*(\d+)", text)),
        }
    return {
        "total_frames": total,
        "janky_frames": int(janky.group(1)) if janky else None,
        "janky_pct": float(janky.group(2)) if janky else None,
        "p50_ms": _float(re.search(r"50th percentile:\s*(\d+)ms", text)),
        "p90_ms": _float(re.search(r"90th percentile:\s*(\d+)ms", text)),
        "p95_ms": _float(re.search(r"95th percentile:\s*(\d+)ms", text)),
        "p99_ms": _float(re.search(r"99th percentile:\s*(\d+)ms", text)),
        "missed_vsync": _int(re.search(r"Number Missed Vsync:\s*(\d+)", text)),
        "slow_ui_thread": _int(re.search(r"Number Slow UI thread:\s*(\d+)", text)),
        "slow_draw": _int(re.search(r"Number Slow Draw:\s*(\d+)", text)),
        "high_input_latency": _int(re.search(r"Number High input latency:\s*(\d+)", text)),
    }


def read_pss_kb(dev: Device) -> int | None:
    return read_mem(dev).get("total_pss_kb")


def read_mem(dev: Device) -> dict:
    """PSS 与它的分档。只报 TOTAL 说不出「涨的是哪一块」——Graphics 涨和 Dalvik 涨是两种病。"""
    text = dev.sh(f"dumpsys meminfo {PKG}")
    return {
        "total_pss_kb": _int(re.search(r"TOTAL PSS:\s*(\d+)", text)),
        "native_heap_kb": _int(re.search(r"Native Heap\s+(\d+)", text)),
        "dalvik_heap_kb": _int(re.search(r"Dalvik Heap\s+(\d+)", text)),
        "graphics_kb": _int(re.search(r"Graphics:\s*(\d+)", text)),
        "code_kb": _int(re.search(r"Code:\s*(\d+)", text)),
        "unknown_kb": _int(re.search(r"Unknown\s+(\d+)", text)),
    }


def open_route(dev: Device, name: str) -> None:
    dev.sh(f'am start -a android.intent.action.VIEW -d "{ROUTES[name]}"')


def scenario_p0(dev: Device, args: argparse.Namespace) -> dict:
    """空闲负载：停在一个路由上不动，读帧与内存。**不发业务、不开麦**。"""
    open_route(dev, args.route)
    time.sleep(4)
    reset_gfx(dev)
    pss_start = read_pss_kb(dev)
    time.sleep(args.seconds)
    return {
        "route": args.route,
        "idle_seconds": args.seconds,
        "pss_start_kb": pss_start,
        "pss_end_kb": read_pss_kb(dev),
        "mem_end": read_mem(dev),
        "gfx": read_gfx(dev),
    }


def scenario_p3(dev: Device, args: argparse.Namespace) -> dict:
    """路由循环：对话→设置→车辆→地图→对话，重复 N 次，看订阅释放与内存增长。"""
    order = [r.strip() for r in args.routes.split(",") if r.strip()]
    for name in order:
        if name not in ROUTES:
            raise SystemExit(f"unknown route: {name}")
    if args.cold_baseline:
        # 从冷进程起算，两臂的基线才可比（暖基线会把上一臂的残留算进来）
        dev.sh(f"am force-stop {PKG}")
        time.sleep(3)
    open_route(dev, "chat")
    time.sleep(6)
    reset_gfx(dev)
    mem_warm = read_mem(dev)
    pss_warm = mem_warm.get("total_pss_kb")
    for _ in range(args.cycles):
        for name in order:
            open_route(dev, name)
            time.sleep(args.dwell)
    open_route(dev, "chat")
    time.sleep(2)
    gfx = read_gfx(dev)
    pss_after = read_pss_kb(dev)
    time.sleep(args.settle)
    return {
        "routes": order,
        "cold_baseline": bool(args.cold_baseline),
        "cycles": args.cycles,
        "dwell_s": args.dwell,
        "settle_s": args.settle,
        "pss_warm_kb": pss_warm,
        "pss_after_kb": pss_after,
        "pss_settled_kb": read_pss_kb(dev),
        "mem_warm": mem_warm,
        "mem_settled": read_mem(dev),
        "gfx": gfx,
    }


def scenario_p5(dev: Device, args: argparse.Namespace) -> dict:
    """冷启动：force-stop 之后 `am start -W`，取设备自己报的 TotalTime/WaitTime。"""
    samples: list[dict] = []
    for _ in range(args.repeats):
        dev.sh(f"am force-stop {PKG}")
        time.sleep(2)
        out = dev.sh(f'am start -W -a android.intent.action.VIEW -d "{ROUTES["chat"]}"', timeout=90)
        samples.append({
            "this_time_ms": _int(re.search(r"ThisTime:\s*(\d+)", out)),
            "total_time_ms": _int(re.search(r"TotalTime:\s*(\d+)", out)),
            "wait_time_ms": _int(re.search(r"WaitTime:\s*(\d+)", out)),
        })
        time.sleep(3)
    values = [s["total_time_ms"] for s in samples if s["total_time_ms"] is not None]
    values.sort()
    return {
        "repeats": args.repeats,
        "samples": samples,
        "measured": len(values),
        # nearest-rank，与 core/obs/latencyStats.ts 同一口径
        "p50_ms": values[max(0, -(-len(values) // 2) - 1)] if values else None,
        "p95_ms": values[max(0, -(-(len(values) * 95) // 100) - 1)] if values else None,
        "min_ms": values[0] if values else None,
        "max_ms": values[-1] if values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=("p0", "p3", "p5"))
    parser.add_argument("--label", default="")
    parser.add_argument("--route", default="chat", choices=tuple(ROUTES))
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--cycles", type=int, default=30)
    parser.add_argument("--dwell", type=float, default=1.2)
    parser.add_argument("--settle", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--routes", default="chat,settings,vehicle,map")
    parser.add_argument("--cold-baseline", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    dev = resolve_test_device()
    payload = {
        "schema_version": 1,
        "protocol": "ar09-probe-v1",
        "scenario": args.scenario,
        "label": args.label,
        "context_before": context(dev),
    }
    runner = {"p0": scenario_p0, "p3": scenario_p3, "p5": scenario_p5}[args.scenario]
    payload["result"] = runner(dev, args)
    payload["context_after"] = context(dev)

    out_dir = Path(args.out) if args.out else Path(
        os.environ.get("LOCALAPPDATA", "."), "car-agent", "artifacts", "ar09-ui-perf"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{args.scenario}-{args.label or args.route}-{stamp}.json"
    target = out_dir / name
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(target), **payload["result"]}, ensure_ascii=False)[:1400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
