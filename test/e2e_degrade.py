"""E2E 降级矩阵：架构 §3.3 四行的确定性刻画（characterize，非修复）。

对应 docs/reviews/2026-07-02-repo-audit-and-roadmap.md T3.5（背景 G4）。本脚本只断言
「今天代码实际产出什么」，不改 orchestrator/cloud 核心业务逻辑一行（planning/context/
aggregator/progress/route_hints/executor/dispatch 均零改动）。

已知的 3 处不完美，本卡明确按用户决定不修、只刻画现状（详见
docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md）：
1) executor.py::_to_result 丢 resp.error.message/.code，aggregator._ERROR_FRIENDLY 对
   非超时的快速失败（如 agent_unreachable）是死代码——Row 3 因此断言可观测的 span
   status 而非聚合器话术原文（该话术目前是通用「抱歉，处理失败。」）。
2) gateway/cloud/main.go 的 mid-stream planner 崩溃分支不回任何消息给端侧（silent 挂
   起）——Row 2 特意在发请求前就停 cloud-planner，走的是另一条干净失败分支，不触碰这
   个坑。
3) llm-gateway CompleteStream 无备用模型重试——不在本卡范围内。

前置：`make up` 起全栈；docker / docker compose CLI 可用。依赖：pip install websockets。
用法：python test/e2e_degrade.py
      python test/e2e_degrade.py --case agent_down      # 只跑某一行，可重复传

**必须放在 nightly-e2e.yml / run_e2e.{sh,ps1} 清单最后**：四个用例都会临时让某个云侧
服务不可用，即便 try/finally 严格恢复，也不应该冒险排在其它 e2e 脚本前面。

顺序（小→大爆炸半径 / 快→慢探测，四行严格顺序执行，不并发）：
  1. 单 Agent 故障（trip-planner-agent stop/start，同容器不换 IP）
  2. LLM 超时（llm-gateway 注入 mock 延迟，换 env 须 --force-recreate，唯一换 IP 的一行）
  3. 云 Planner 故障（cloud-planner stop/start，同容器不换 IP）
  4. 断网（pause/unpause cloud-gateway——真正的黑洞而非 stop 的即时拒绝，最慢探测，放最后）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

# 只借 e2e_central_hub_assertions 的 trace/span 引擎（Row 3 需要——精确断言可观测的
# step.agent:<id> span status，而非易随文案漂移的聚合器话术）。其余请求走本文件自己
# 的 ask()：_send() 为了兼容多 final 的场景，收到第一条 final 后还会再等 10s 收尾，
# 不适合本脚本"即时秒回/限时超时"类的计时断言。
sys.path.insert(0, str(Path(__file__).parent))
from e2e_central_hub_assertions import _trace_id, _wait_trace, _span_status, _nodes, _send  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", "compose.yaml"]

URL = "ws://localhost:8090/ws"
ASK_TIMEOUT = 60
RECOVER_DEADLINE = 120        # 换 IP 的一行（llm-gateway --force-recreate）用；对齐 e2e_resilience.py 同名常量
SHORT_RECOVER_DEADLINE = 60   # 同容器不换 IP 的三行（stop/start、pause/unpause）用，恢复应快得多
POLL_GAP = 5


def _compose_env(overrides: dict[str, str] | None = None) -> dict:
    env = os.environ.copy()
    if overrides:
        env.update(overrides)
    return env


def _run(args: list[str], env: dict | None = None) -> None:
    subprocess.run(COMPOSE + args, cwd=ROOT, check=True, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def ask(text: str, session: str) -> dict | None:
    """发一条请求，收到第一条 final/error 立即返回（逐字复用 e2e_resilience.py 同名
    函数的模式——该项目已接受的 e2e 脚本间小工具重复惯例，见 D19）。

    ping_interval=None：断网/超时类用例的降级路径可能要等 10s+ 才产出应用层话术，
    websockets 库默认的底层 ping/pong 保活会在等到话术前先把连接判死——本脚本首次真
    实跑时就踩过这个坑（network_outage 用例被 1011 keepalive ping timeout 提前掐断）。
    照抄 e2e_central_hub_assertions.py::_send() 已有的同款修复（"模拟浏览器"注释）。"""
    try:
        async with websockets.connect(URL, open_timeout=10, ping_interval=None) as ws:
            await ws.send(json.dumps({"text": text, "session_id": session}, ensure_ascii=False))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=ASK_TIMEOUT)
                msg = json.loads(raw)
                if msg.get("type") in ("final", "error"):
                    return msg
    except Exception as e:
        print(f"    (ask 失败: {type(e).__name__}: {str(e)[:60]})")
        return None


def _speech_of(msg: dict | None) -> str:
    if not msg:
        return ""
    return (msg.get("speech") or msg.get("message") or "").strip()


# 四行各自的确切降级话术——用于恢复轮询判定「已经不再是任何一种已知降级态」。
_DEGRADED_PHRASES = (
    "网络不太好", "云端处理异常", "处理超时了", "处理失败",
    "暂时不可用", "请稍后重试", "请稍后再试", "没能理解",
)


def _is_healthy_reply(msg: dict | None) -> bool:
    if not msg or msg.get("type") != "final":
        return False
    speech = _speech_of(msg)
    return bool(speech) and not any(p in speech for p in _DEGRADED_PHRASES)


async def _poll_until(check, deadline: float, gap: float = POLL_GAP) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < deadline:
        if await check():
            return True
        await asyncio.sleep(gap)
    return False


async def _quick_recovered(text: str) -> bool:
    msg = await ask(text, f"degrade-recover-{uuid.uuid4().hex[:6]}")
    return _is_healthy_reply(msg)


# ════════════════════════════════════════════════════════════════════════
# Row 3：单 Agent 故障
# ════════════════════════════════════════════════════════════════════════
async def case_agent_down() -> bool:
    print("\n[降级 1/4] 单 Agent 故障：trip-planner-agent 停机")
    service = "trip-planner-agent"          # docker compose 服务名
    agent_node = "step.agent:trip-planner"  # manifest agent_id（≠ 服务名！）
    text = "周末去杭州两天带老人不要太累"     # 与 e2e_trip.py 同款，route_hints 确定性命中 trip.plan
    try:
        try:
            print(f"  → docker compose stop {service}")
            _run(["stop", service])

            trace_id = _trace_id()
            finals = await _send(text, f"degrade-agent-down-{uuid.uuid4().hex[:6]}", trace_id)
            spans = _wait_trace(trace_id, [agent_node])
            status = _span_status(spans, agent_node)
            completed = bool(finals)   # DAG 不炸：单步 FAILED 仍应给出收尾 final/error

            print(f"  {'✓' if status == 'err' else '✗'} span {agent_node} status={status!r}"
                  f"（期望 err；spans={_nodes(spans)!r}）")
            print(f"  {'✓' if completed else '✗'} 整轮仍收到 final/error（DAG 未被单步失败拖垮）")
            return status == "err" and completed
        except Exception as e:
            print(f"  ✗ 用例执行异常: {type(e).__name__}: {e}")
            return False
    finally:
        try:
            print(f"  → docker compose start {service}（恢复）")
            _run(["start", service])
            recovered = await _agent_recovered(text, agent_node)
            print("  ✓ 已确认 trip-planner-agent 恢复" if recovered
                  else "  ✗ 恢复未确认，需人工检查 docker compose ps！")
        except Exception as e:
            print(f"  ✗ 恢复步骤异常: {type(e).__name__}: {e}——需人工检查！")


async def _agent_recovered(text: str, agent_node: str) -> bool:
    """stop/start 同容器不换 IP，进程重启本身数秒内完成；但本恢复探针复用的是完整
    trip.plan 请求——真实 LLM+真实高德 POI 时，完整生成行程可能要 30-60s+（跟"有没有
    恢复"无关，是行程规划这个操作本来就慢；nightly 走 mock 时 trip-planner 自身的
    `_fallback_skeleton` 兜底生成器应该快得多）。放宽到 10+20+30+40=100s 退避重试，
    容纳本机真实 key 场景下的正常慢速；即便仍未在此窗口内确认，也只是 finally 块的
    诊断信息，不影响用例本身的通过/失败判定。"""
    for wait_s in (10, 20, 30, 40):
        await asyncio.sleep(wait_s)
        trace_id = _trace_id()
        finals = await _send(text, f"degrade-agent-recover-{uuid.uuid4().hex[:6]}", trace_id)
        spans = _wait_trace(trace_id, [agent_node])
        if _span_status(spans, agent_node) == "ok" and finals:
            return True
    return False


# ════════════════════════════════════════════════════════════════════════
# Row 4：LLM 超时
# ════════════════════════════════════════════════════════════════════════
# 原设计假定 chitchat 走 executor.py 的 asyncio.wait_for(latency_budget_ms=2500) 包裹，
# 大延迟会命中 aggregator._ERROR_FRIENDLY["step_timeout"]，断言固定话术「抱歉，处理超时
# 了，请稍后再试。」。真实跑（本地手工验证，见 docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md
# §6）发现不成立：chitchat 走 engine.py 的 D0 单步流式直通，不受该包裹管辖——8000ms 延迟
# 下请求仍在 ~16s（≈规划 2 次重试各记一次延迟）内正常收到完整回复，从未挂起或降级；换
# 唯一有 route_hints（mock 下路由确定）的 trip-planner 试过 45000ms 延迟，其"heavy"任务
# 时间预算（为容纳"思考"被放宽）比预期大得多，200s 耐心等待仍未收尾，作为 e2e 用例不实际。
# 故本行改为断言一个更朴实但同样真实的性质：**LLM 变慢时系统仍保持响应、给出连贯回复，
# 不会挂起或崩溃**——顺带验证 LLM_MOCK_DELAY_MS 钩子本身真的在生效（用耗时下限印证延迟被
# 应用，而不是网络抖动侥幸通过）。
LLM_MOCK_DELAY_MS = "3000"   # 小延迟即可：目的是证明"变慢不致命"，不需要撑到踩中任何超时阈值


async def case_llm_timeout() -> bool:
    print("\n[降级 2/4] LLM 超时：llm-gateway 注入 mock 延迟（断言仍优雅响应，非精确超时话术）")
    text = "讲个笑话"
    try:
        try:
            print(f"  → 用 LLM_MOCK_DELAY_MS={LLM_MOCK_DELAY_MS} 重建 llm-gateway（换 IP，四行里唯一一行）…")
            _run(["up", "-d", "--force-recreate", "--no-deps", "llm-gateway"],
                 env=_compose_env({"LLM_MOCK_DELAY_MS": LLM_MOCK_DELAY_MS}))
            await asyncio.sleep(8)   # 容器启动 + 下游 channel 重连的基础窗口

            t0 = time.monotonic()
            msg = await ask(text, f"degrade-llm-timeout-{uuid.uuid4().hex[:6]}")
            elapsed = time.monotonic() - t0
            speech = _speech_of(msg)
            # 延迟确实生效（耗时明显高于正常秒回）+ 仍然给出连贯、非崩溃的回复（不是 error
            # 类型、不是空话术）——不断言具体哪句话，措辞可能因走哪条代码路径而不同。
            delay_engaged = elapsed >= float(LLM_MOCK_DELAY_MS) / 1000.0 * 0.5
            responded_ok = msg is not None and msg.get("type") == "final" and bool(speech)
            print(f"  {'✓' if delay_engaged else '✗'} 耗时 {elapsed:.1f}s（延迟钩子应可观测生效）")
            print(f"  {'✓' if responded_ok else '✗'} 仍收到连贯 final 回复：{speech[:50]!r}")
            return delay_engaged and responded_ok
        except Exception as e:
            print(f"  ✗ 用例执行异常: {type(e).__name__}: {e}")
            return False
    finally:
        try:
            print("  → 恢复 llm-gateway（去掉延迟 env，recreate 换回默认）…")
            _run(["up", "-d", "--force-recreate", "--no-deps", "llm-gateway"])
            # 换 IP 场景，给足 120s（对齐 e2e_resilience.py 同类场景的 RECOVER_DEADLINE）；
            # 即便轮询期间偶发触发 chitchat 熔断，30s recovery_timeout 后半开探测也能在
            # 此预算内收敛，见落地文档「方案」一节的分析。
            recovered = await _poll_until(lambda: _quick_recovered(text), deadline=RECOVER_DEADLINE)
            print("  ✓ 已确认 llm-gateway 恢复" if recovered else "  ✗ 恢复轮询超时，需人工检查！")
        except Exception as e:
            print(f"  ✗ 恢复步骤异常: {type(e).__name__}: {e}——需人工检查！")


# ════════════════════════════════════════════════════════════════════════
# Row 2：云 Planner 故障
# ════════════════════════════════════════════════════════════════════════
async def case_planner_down() -> bool:
    print("\n[降级 3/4] 云 Planner 故障：cloud-planner 停机")
    text = "讲个笑话"
    target = "云端处理异常，请稍后重试。"
    try:
        try:
            print("  → docker compose stop cloud-planner")
            _run(["stop", "cloud-planner"])

            msg = await ask(text, f"degrade-planner-down-{uuid.uuid4().hex[:6]}")
            speech = _speech_of(msg)
            ok = target in speech
            print(f"  {'✓' if ok else '✗'} speech={speech!r}")
            return ok
        except Exception as e:
            print(f"  ✗ 用例执行异常: {type(e).__name__}: {e}")
            return False
    finally:
        try:
            print("  → docker compose start cloud-planner（恢复）")
            _run(["start", "cloud-planner"])
            recovered = await _poll_until(lambda: _quick_recovered(text), deadline=SHORT_RECOVER_DEADLINE)
            print("  ✓ 已确认 cloud-planner 恢复" if recovered else "  ✗ 恢复轮询超时，需人工检查！")
        except Exception as e:
            print(f"  ✗ 恢复步骤异常: {type(e).__name__}: {e}——需人工检查！")


# ════════════════════════════════════════════════════════════════════════
# Row 1：断网
# ════════════════════════════════════════════════════════════════════════
async def case_network_outage() -> bool:
    print("\n[降级 4/4] 断网：cloud-gateway 暂停（真正的黑洞，非 stop 的即时拒绝）")
    local_text = "打开空调26度"     # 与 e2e_ws.py / PoC 验收清单同款车控探针
    cloud_text = "讲个笑话"
    target = "网络不太好，复杂请求暂时无法处理，不过车内控制依然可以正常使用。"
    try:
        try:
            print("  → docker compose pause cloud-gateway")
            _run(["pause", "cloud-gateway"])

            t0 = time.monotonic()
            local_msg = await ask(local_text, f"degrade-net-local-{uuid.uuid4().hex[:6]}")
            local_elapsed = time.monotonic() - t0
            local_ok = _is_healthy_reply(local_msg) and local_elapsed < 5.0
            print(f"  {'✓' if local_ok else '✗'} 车控本地秒回：{local_elapsed:.1f}s "
                  f"speech={_speech_of(local_msg)[:40]!r}")

            cloud_msg = await ask(cloud_text, f"degrade-net-cloud-{uuid.uuid4().hex[:6]}")
            cloud_speech = _speech_of(cloud_msg)
            cloud_ok = target in cloud_speech
            print(f"  {'✓' if cloud_ok else '✗'} 云端请求降级话术：{cloud_speech!r}")

            return local_ok and cloud_ok
        except Exception as e:
            print(f"  ✗ 用例执行异常: {type(e).__name__}: {e}")
            return False
    finally:
        try:
            print("  → docker compose unpause cloud-gateway（恢复）")
            _run(["unpause", "cloud-gateway"])
            # 已知缺口（本次真实跑发现，非本卡改动引入）：edge-orchestrator 的持久 channel
            # 在 cloud-gateway pause→unpause（冻结再解冻，同 IP）后不会像 e2e_resilience.py
            # 测的"recreate 换 IP"场景那样可靠自愈——实测日志反复 "Missed too many pongs,
            # forcing reconnect" 后仍 "cloud channel not connected"，需要重启 edge-orchestrator
            # 才能恢复。详见 docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md §6。不修这个
            # 缺口（跟研究阶段发现的 3 处一视同仁），但本脚本的恢复步骤不能依赖不可靠的自愈
            # ——显式重启 edge-orchestrator 兜底，确保测试结束时栈真的恢复干净。
            print("  → 顺带重启 edge-orchestrator（pause/unpause 后已知不会自愈的兜底）")
            _run(["restart", "edge-orchestrator"])
            recovered = await _poll_until(lambda: _quick_recovered(cloud_text), deadline=SHORT_RECOVER_DEADLINE)
            print("  ✓ 已确认 cloud-gateway 恢复" if recovered else "  ✗ 恢复轮询超时，需人工检查！")
        except Exception as e:
            print(f"  ✗ 恢复步骤异常: {type(e).__name__}: {e}——需人工检查！")


_CASES = [
    ("agent_down", case_agent_down),
    ("llm_timeout", case_llm_timeout),
    ("planner_down", case_planner_down),
    ("network_outage", case_network_outage),
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", default=[],
                        help="只跑某几行（可重复）：agent_down/llm_timeout/planner_down/network_outage")
    args = parser.parse_args()

    cases = _CASES
    if args.case:
        selected = set(args.case)
        cases = [(n, f) for n, f in _CASES if n in selected]
        if not cases:
            print(f"未知 --case，可选：{[n for n, _ in _CASES]}")
            return 2

    print("=== E2E 降级矩阵测试（架构 §3.3 四行）===")
    results = []
    for name, fn in cases:      # 严格顺序执行，不并发（部分故障爆炸半径重叠）
        ok = await fn()
        results.append((name, ok))

    print("\n=== 结果 ===")
    for name, ok in results:
        print(f"  {'✓ PASS' if ok else '✗ FAIL'}  {name}")
    failed = [n for n, ok in results if not ok]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
