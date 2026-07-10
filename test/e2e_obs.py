"""端到端验证：badcase 排查观测链路（obs.turn / obs.llm / obs.log / SQLite 持久化）。

覆盖设计文档 §11 P0/P1/P2 验收锚点（docs/design/2026-07-10-dashboard-badcase-observability-redesign.md）：
1. HMI 同款 WS 请求（带自生成 trace_id + session）→ collector 会话/轮次可查（本地快路径 + 云端路径）
2. 轮次详情含 span 链路；云端轮含 cloud.planning 门控 plan 内容 + LLM 调用记录（obs.llm）
3. 带 trace 的日志经 obs.log 进 collector（P1，按 trace 关联）
4. badcase 标记 / 检索 / 导出 JSON（P2）
5. 重启 collector 后数据仍在（SQLite named volume 持久化）

前置：`make up`（或 docker compose up -d --build）起全栈后运行。依赖：pip install websockets httpx
用法：python test/e2e_obs.py
"""
import asyncio
import json
import subprocess
import sys
import time
import uuid

try:                                   # Windows 控制台默认 GBK，强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
    import httpx
except ImportError:
    print("请先：pip install websockets httpx")
    sys.exit(1)

WS_URL = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
TIMEOUT = 90
SESSION = f"e2e-obs-{int(time.time())}"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, note: str = ""):
    RESULTS.append((name, ok, note))
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  ({note})" if note else ""))


async def ask(text: str, trace_id: str) -> dict:
    """HMI 同款请求：自带 trace_id 随 meta 上行。"""
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "text": text, "session_id": SESSION, "is_confirmation": False,
            "meta": {"trace_id": trace_id},
        }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") in ("final", "error"):
                print(f"  [{text}] -> {msg.get('speech', msg.get('message', ''))[:60]}")
                return msg


async def wait_for(fn, timeout=15, interval=0.5):
    """轮询直到 fn() 返回真值或超时；返回最后一次结果。"""
    deadline = time.monotonic() + timeout
    result = None
    while time.monotonic() < deadline:
        try:
            result = await fn()
        except Exception:
            result = None
        if result:
            return result
        await asyncio.sleep(interval)
    return result


async def main() -> int:
    trace_local = uuid.uuid4().hex[:16]
    trace_cloud = uuid.uuid4().hex[:16]

    async with httpx.AsyncClient(base_url=COLLECTOR, timeout=10) as api:
        health = (await api.get("/healthz")).json()
        check("collector 在线（NATS 已连）", bool(health.get("nats")), str(health))

        # ── 1. 发两轮：本地快路径 + 云端路径 ──
        print("\n[1] 发送两轮请求（local + cloud）")
        await ask("打开空调26度", trace_local)
        await ask("今天深圳天气怎么样", trace_cloud)

        # ── 2. 会话/轮次落库 ──
        print("\n[2] 会话/轮次（obs.turn → SQLite）")

        async def _session_row():
            rows = (await api.get("/api/sessions", params={"q": SESSION})).json()
            return next((r for r in rows if r["session_id"] == SESSION), None)

        session_row = await wait_for(_session_row)
        check("会话出现在 /api/sessions", session_row is not None)
        if session_row:
            check("会话轮数 = 2", session_row["turns"] == 2, f"turns={session_row['turns']}")

        turns = (await api.get(f"/api/sessions/{SESSION}/turns")).json()
        by_trace = {t["trace_id"]: t for t in turns}
        t_local, t_cloud = by_trace.get(trace_local), by_trace.get(trace_cloud)
        check("本地轮 path=local 且有话术",
              bool(t_local and t_local["path"] == "local" and t_local["speech"]),
              f"path={t_local and t_local['path']}")
        check("云端轮 path=cloud 且 user_text 完整",
              bool(t_cloud and t_cloud["path"] == "cloud"
                   and t_cloud["user_text"] == "今天深圳天气怎么样"))

        # ── 3. 轮次详情：span 链路 + plan 内容 + LLM 调用 ──
        print("\n[3] 轮次详情（span / plan / obs.llm）")
        detail_local = (await api.get(f"/api/turns/{trace_local}")).json()
        nodes_local = [s["node"] for s in detail_local.get("spans", [])]
        check("本地轮 spans 含 route.local + val.execute",
              "route.local" in nodes_local and "val.execute" in nodes_local,
              str(nodes_local))

        detail_cloud = (await api.get(f"/api/turns/{trace_cloud}")).json()
        nodes_cloud = [s["node"] for s in detail_cloud.get("spans", [])]
        planning = next((s for s in detail_cloud.get("spans", [])
                         if s["node"] == "cloud.planning"), None)
        check("云端轮 spans 含 cloud.planning", planning is not None, str(nodes_cloud))
        if planning:
            check("planning span 带门控 plan 内容", bool(planning["attrs"].get("plan")),
                  str(planning["attrs"].get("plan"))[:60])
        llm_calls = detail_cloud.get("llm_calls", [])
        check("云端轮有 LLM 调用记录（obs.llm）", len(llm_calls) >= 1,
              f"{len(llm_calls)} 次, caller={[c['caller'] for c in llm_calls][:3]}")

        # ── 4. 日志按 trace 关联（obs.log；INFO 带 trace 也发） ──
        print("\n[4] 日志贯通（obs.log）")

        async def _trace_logs():
            rows = (await api.get("/api/logs", params={"trace_id": trace_cloud})).json()
            return rows or None

        logs = await wait_for(_trace_logs, timeout=10)
        check("云端轮有按 trace 关联的日志", bool(logs),
              f"{len(logs or [])} 条, 首条={logs[0]['service'] if logs else '-'}")

        # ── 5. badcase 标记 / 检索 / 导出 ──
        print("\n[5] badcase 工作流")
        marked = (await api.post(f"/api/turns/{trace_cloud}/badcase",
                                 json={"badcase": True, "note": "e2e 标记"})).json()
        check("标记 badcase", marked.get("ok") is True)
        flagged = (await api.get("/api/search", params={"badcase": 1, "q": trace_cloud})).json()
        check("badcase 检索命中", any(t["trace_id"] == trace_cloud for t in flagged))
        exported = (await api.get(f"/api/export/{trace_cloud}")).json()
        check("导出 JSON 结构完整",
              bool(exported.get("turn") and "spans" in exported and "llm_calls" in exported))

        # 文本检索（HMI 复制 trace 前缀直达）
        hits = (await api.get("/api/search", params={"q": "深圳天气"})).json()
        check("按原话文本检索命中", any(t["trace_id"] == trace_cloud for t in hits))

    # ── 6. 重启 collector：SQLite 持久化 ──
    print("\n[6] 重启 collector（持久化验证）")
    subprocess.run(["docker", "restart", "car-agent-observability-collector-1"],
                   check=True, capture_output=True)
    await asyncio.sleep(4)
    async with httpx.AsyncClient(base_url=COLLECTOR, timeout=10) as api:
        async def _after_restart():
            try:
                rows = (await api.get("/api/sessions", params={"q": SESSION})).json()
                return next((r for r in rows if r["session_id"] == SESSION), None)
            except Exception:
                return None

        survived = await wait_for(_after_restart, timeout=30, interval=1)
        check("重启后会话数据仍在", survived is not None)
        if survived:
            detail = (await api.get(f"/api/turns/{trace_cloud}")).json()
            check("重启后轮次详情完整（含 badcase 标记）",
                  bool(detail.get("turn") and detail["turn"]["badcase"] == 1))

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'='*52}\n结果：{len(RESULTS)-len(failed)}/{len(RESULTS)} 通过")
    for name, ok, note in failed:
        print(f"  ❌ {name} {note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
