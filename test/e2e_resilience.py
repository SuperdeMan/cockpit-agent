"""端到端韧性验证：依赖服务重建（换 IP）后系统自愈，无需重启依赖方。

直接对应实测痛点「断连 / 无响应」与历史坑「单服务 recreate 换 IP 需重启依赖方」。
加固前：各 gRPC channel 无 keepalive，依赖重建换 IP 后缓存的 channel 钉死旧地址，
空闲不重连 → 后续请求永久无响应，必须重启依赖方才恢复。
加固后：全链路 keepalive，连接断开/假死在一个周期内被探测并重连重解析 DNS → 自愈。

前置：`make up` 起全栈。依赖：pip install websockets；需 docker compose 可用。
用法：python test/e2e_resilience.py
      python test/e2e_resilience.py --service chitchat   # 只测某依赖

验收点：
- 基线：云端请求能拿到 final（speech 非空、非错误）。
- 重建 cloud-planner（--force-recreate，换 IP）后，不重启任何依赖方，
  云端请求在 RECOVER_DEADLINE 内自愈恢复（验证 Go 网关→planner keepalive）。
- 重建 chitchat agent（换 IP）后同样自愈（验证 Python aio_channel keepalive）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", "compose.yaml"]
ASK_TIMEOUT = 60          # 单次请求等待 final 的上限（秒）
RECOVER_DEADLINE = 120    # 重建后自愈的容忍上限（秒）
POLL_GAP = 5              # 自愈轮询间隔（秒）


async def ask(text: str, session: str) -> dict | None:
    """发一条请求并等到 final/error；连接失败或超时返回 None（供轮询继续）。"""
    try:
        async with websockets.connect(URL, open_timeout=10) as ws:
            await ws.send(json.dumps({"text": text, "session_id": session}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=ASK_TIMEOUT)
                msg = json.loads(raw)
                if msg.get("type") in ("final", "error"):
                    return msg
    except Exception as e:
        print(f"    (ask 失败，将重试: {type(e).__name__}: {str(e)[:60]})")
        return None


# 网关/编排器在依赖不可达时的兜底话术——基线/自愈判定须排除它们，否则误判成功。
_ERROR_SPEECH = ("云端处理异常", "出错了", "响应超时", "暂时不可用", "调用失败", "稍后重试")


def _ok(msg: dict | None) -> bool:
    """final 且 speech 非空、且非错误兜底话术 = 正常应答。"""
    if not msg or msg.get("type") != "final":
        return False
    speech = (msg.get("speech") or "").strip()
    return bool(speech) and not any(e in speech for e in _ERROR_SPEECH)


def recreate(service: str) -> None:
    """强制重建单个服务（换 IP），不动其依赖。"""
    print(f"  → 重建 {service}（--force-recreate --no-deps，换 IP）…")
    subprocess.run(COMPOSE + ["up", "-d", "--force-recreate", "--no-deps", service],
                   cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def probe_until_ok(text: str, session_prefix: str, deadline: float) -> float | None:
    """轮询请求直到拿到正常应答。返回耗时秒数；超 deadline 返回 None。"""
    start = time.monotonic()
    n = 0
    while time.monotonic() - start < deadline:
        n += 1
        msg = await ask(text, f"{session_prefix}-{n}")
        if _ok(msg):
            return time.monotonic() - start
        await asyncio.sleep(POLL_GAP)
    return None


async def case_recover_after_recreate(service: str, probe_text: str) -> bool:
    print(f"\n[韧性] 重建 {service} 后自愈（不重启依赖方）")
    # 1) 基线（容忍冷启动/上一用例重建后的短暂瞬时，最多 25s 内拿到真实应答）
    base = await probe_until_ok(probe_text, f"resil-{service}-base", 25)
    if base is None:
        print("  ✗ 基线请求未通过，无法继续（stack 未就绪？）")
        return False
    print("  ✓ 基线正常")

    # 2) 重建依赖（换 IP）
    recreate(service)

    # 3) 不重启网关/依赖方，轮询直到自愈
    took = await probe_until_ok(probe_text, f"resil-{service}-recover", RECOVER_DEADLINE)
    if took is None:
        print(f"  ✗ {RECOVER_DEADLINE}s 内未自愈——疑似 channel 钉死旧 IP（keepalive 失效）")
        return False
    print(f"  ✓ 约 {took:.0f}s 自愈恢复，无需重启依赖方")
    return True


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="", help="只测某依赖（cloud-planner / chitchat）")
    args = parser.parse_args()

    # 探测语：用云端兜底闲聊，确保走完整端云链路（HMI→edge→cloud→planner→agent→llm）。
    targets = [
        ("cloud-planner", "讲个笑话"),
        ("chitchat-agent", "讲个笑话"),
    ]
    if args.service:
        targets = [t for t in targets if t[0] == args.service]
        if not targets:
            print(f"未知 service: {args.service}")
            return 2

    print("=== E2E 韧性测试（依赖重建自愈）===")
    results = []
    for service, probe in targets:
        ok = await case_recover_after_recreate(service, probe)
        results.append((service, ok))

    print("\n=== 结果 ===")
    for service, ok in results:
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}  {service}")
    failed = [s for s, ok in results if not ok]
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(asyncio.run(main()))
