# Observability Dashboard Implementation Plan - P2/P3

> 历史 TDD 实施明细：端云链路、Agent 健康/指标、控制台闭环与验收。
> 当前状态与分卷导航见 [`2026-06-15-observability-dashboard.md`](2026-06-15-observability-dashboard.md)。
> 文内预期测试数保留实施时原文；当前验证基线为 360 passed, 2 skipped。

## Phase 2 — 请求链路可视化（需求 3）

### Task 9: edge trace_id 贯穿（前端生成 → edge → 透传云端）

**Files:**
- Modify: `orchestrator/edge/server.py`（新增 `_ensure_trace_id`；`Handle` 入口调用）
- Test: `orchestrator/edge/tests/test_trace_propagation.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/edge/tests/test_trace_propagation.py
from server import _ensure_trace_id


class _Req:
    def __init__(self, meta):
        self.meta = meta


def test_preserves_frontend_trace_id():
    r = _Req({"trace_id": "front-123"})
    assert _ensure_trace_id(r) == "front-123"
    assert r.meta["trace_id"] == "front-123"   # 保留前端 id，供云端复用


def test_generates_trace_id_when_absent():
    r = _Req({})
    tid = _ensure_trace_id(r)
    assert tid and r.meta["trace_id"] == tid   # 写回 meta，上云带上
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest orchestrator/edge/tests/test_trace_propagation.py -v --import-mode=importlib`
Expected: FAIL（`ImportError: cannot import name '_ensure_trace_id'`）

- [ ] **Step 3: 在 `orchestrator/edge/server.py` 加 helper 并在 `Handle` 调用**

文件顶部 import 区加：

```python
from observability.tracing import new_trace_id, set_trace_id
```

模块级（`_HIGH = ...` 之后）加：

```python
def _ensure_trace_id(request) -> str:
    """前端在 meta.trace_id 放了就复用，否则生成；写回 request.meta 以便透传云端。"""
    tid = request.meta.get("trace_id") if request.meta else ""
    if not tid:
        tid = new_trace_id()
    request.meta["trace_id"] = tid
    set_trace_id(tid)
    return tid
```

在 `Handle` 方法体最开头（`meta = dict(request.meta) ...` 之前）加：

```python
        trace_id = _ensure_trace_id(request)
        self._change_source.set("T0")
```

（`self._change_source` 已在 Task 7 注入；`trace_id` 供本 task 后续 span 使用。）

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest orchestrator/edge/tests/test_trace_propagation.py -v --import-mode=importlib`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add orchestrator/edge/server.py orchestrator/edge/tests/test_trace_propagation.py
git commit -m "feat(obs): edge trace_id 贯穿（前端生成→透传云端）"
```

---

### Task 10: edge 链路 span（route.* / val.execute）

**Files:**
- Modify: `orchestrator/edge/server.py`（`Handle` 本地快路径 B、慢路径分支加 span）
- Test: `orchestrator/edge/tests/test_edge_spans.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/edge/tests/test_edge_spans.py
import asyncio
from server import EdgeOrchestratorServicer
from cockpit.orchestrator.v1 import orchestrator_pb2


def test_local_path_emits_route_and_val_spans(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    svc = EdgeOrchestratorServicer()
    nodes = []

    async def fake_span(trace_id, node, **kw):
        nodes.append(node)
    svc.obs.emit_span = fake_span

    req = orchestrator_pb2.HandleRequest(
        text="打开空调26度", session_id="t", request_id="r")

    async def run():
        async for _ in svc.Handle(req, None):
            pass
    asyncio.run(run())

    assert any("route.local" in n for n in nodes)
    assert any("val.execute" in n for n in nodes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest orchestrator/edge/tests/test_edge_spans.py -v --import-mode=importlib`
Expected: FAIL（无 route.local/val.execute span）

- [ ] **Step 3: 在 `Handle` 本地快路径 B 加 span**

在快路径 B（`if intent and intent["confidence"] >= _HIGH and is_local(intent["name"]):` 块内）、`self.val.execute(...)` 成功产出 `speech` 之后、`yield ... FinalResult(speech=speech)` 之前，加：

```python
                await self.obs.emit_span(trace_id, "route.local",
                                         attrs={"intent": intent["name"],
                                                "confidence": intent["confidence"]})
                await self.obs.emit_span(trace_id, "val.execute", status="ok",
                                         attrs={"intent": intent["name"]})
```

在慢路径（`logger.info("CLOUD route: %s", request.text)` 之后）加：

```python
        await self.obs.emit_span(trace_id, "route.cloud",
                                 attrs={"text": request.text[:40]})
```

> 多意图/混合路径（A/A2）同理可加 `route.multi`/`route.mixed`，本 task 至少覆盖 local 与 cloud 两条主路径（验收需要）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest orchestrator/edge/tests/test_edge_spans.py -v --import-mode=importlib`
Expected: PASS（1 passed）

- [ ] **Step 5: 回归端侧**

Run: `python -m pytest orchestrator/edge/tests/ test/smoke_edge.py -q --import-mode=importlib`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add orchestrator/edge/server.py orchestrator/edge/tests/test_edge_spans.py
git commit -m "feat(obs): edge route/val.execute 链路 span"
```

---

### Task 11: cloud 链路 span（planning / step / t2.iter / aggregate）

**Files:**
- Modify: `observability/events.py`（加 `get_emitter`）
- Modify: `orchestrator/cloud/engine.py`（`_build_context` 填 trace_id；`run` 入口 set；planning/aggregate span）
- Modify: `orchestrator/cloud/dispatch.py`（每 step span）
- Modify: `orchestrator/cloud/loop.py`（t2.iter span）
- Test: `orchestrator/cloud/tests/test_obs_spans.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/cloud/tests/test_obs_spans.py
import asyncio
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.models import Step, PlanContext
from cockpit.agent.v1 import agent_pb2


def test_build_context_reads_trace_id():
    eng = PlannerEngine(clients=None, planner=None, executor=None,
                        aggregator=None, session=None, perms=None, loop=object())

    class Req:
        request_id = "r"; session_id = "s"; is_confirmation = False
        meta = {"trace_id": "front-7"}; context = None

    ctx = eng._build_context(Req())
    assert ctx.trace_id == "front-7"


def test_dispatch_emits_step_span(monkeypatch):
    from observability import events

    spans = []

    class FakeEmitter:
        async def emit_span(self, trace_id, node, **kw):
            spans.append(node)
        async def emit_metric(self, *a, **k):
            pass

    monkeypatch.setattr(events, "get_emitter", lambda service="cloud": FakeEmitter())

    async def fake_cloud(endpoint, intent, slots, ctx, meta):
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK, speech="ok")

    d = UnifiedDispatcher(cloud_call=fake_cloud, edge_call=None)
    step = Step(id="s1", agent_id="navigation",
                intent="navigation.search_poi", endpoint="x",
                kind="agent", deployment="cloud")
    ctx = PlanContext(request_id="r", session_id="s", trace_id="t",
                      granted_permissions=["navigation"])
    asyncio.run(d.dispatch(step, ctx))
    assert any(n == "step.agent:navigation" for n in spans)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest orchestrator/cloud/tests/test_obs_spans.py -v --import-mode=importlib`
Expected: FAIL（`AttributeError: module 'observability.events' has no attribute 'get_emitter'` / trace_id 为空）

- [ ] **Step 3a: `observability/events.py` 加进程级默认 emitter**

文件末尾加：

```python
_default_emitter: EventEmitter | None = None


def get_emitter(service: str = "cloud") -> EventEmitter:
    """进程级单例 emitter（首次调用确定 service 名）。"""
    global _default_emitter
    if _default_emitter is None:
        _default_emitter = EventEmitter(service)
    return _default_emitter
```

- [ ] **Step 3b: `orchestrator/cloud/engine.py` 接线**

import 区加：

```python
from observability.events import get_emitter
from observability.tracing import set_trace_id
```

`_build_context` 的 `return PlanContext(...)` 加上 `trace_id`：

```python
        return PlanContext(
            request_id=getattr(request, "request_id", ""),
            session_id=getattr(request, "session_id", ""),
            user_id=getattr(request.context, "user_id", "") if hasattr(request, "context") and request.context else "",
            vehicle_id=getattr(request.context, "vehicle_id", "") if hasattr(request, "context") and request.context else "",
            is_confirmation=getattr(request, "is_confirmation", False),
            granted_permissions=granted,
            trace_id=meta.get("trace_id", ""),
            prefs=prefs,
        )
```

在 `run()` 方法体开头（`ctx = self._build_context(request)` 之后）加：

```python
        set_trace_id(ctx.trace_id)
```

在 `_orchestrate` 中规划完成后（`plan = await self.planner.build(...)` 且 `if not plan.steps:` 检查之后）加：

```python
            await get_emitter("cloud").emit_span(
                ctx.trace_id, "cloud.planning",
                attrs={"complexity": plan.complexity, "goal": plan.goal,
                       "steps": len(plan.steps)})
```

在 `_orchestrate` 末尾聚合处（`final = await self.aggregator.compose(...)` 之后、`yield {"kind": "final", **final}` 之前的那处）加：

```python
        await get_emitter("cloud").emit_span(ctx.trace_id, "aggregate")
```

- [ ] **Step 3c: `orchestrator/cloud/dispatch.py` 每 step span**

import 区加 `from observability.events import get_emitter`。在 `UnifiedDispatcher` 内加：

```python
    @staticmethod
    def _step_node(step) -> str:
        if step.kind == "tool":
            return f"step.tool:{step.intent}"
        if step.deployment == "edge":
            return f"step.edge:{step.intent}"
        return f"step.agent:{step.agent_id}"

    async def _emit_step(self, step, ctx, ok: bool, elapsed: float):
        try:
            await get_emitter("cloud").emit_span(
                getattr(ctx, "trace_id", ""), self._step_node(step),
                status="ok" if ok else "err", duration_ms=elapsed,
                attrs={"intent": step.intent, "agent_id": step.agent_id,
                       "kind": step.kind, "deployment": step.deployment})
        except Exception:
            pass
```

在三条执行路径每个 `metrics.record_agent_call(...)` 之后、对应 `return resp` / `return _failure(...)` / `raise` 之前插入一行（`ok` 用与 `record_agent_call` 相同的成功判定；异常路径用 `False`），例如 tool 成功路径：

```python
                ok = resp.status == agent_pb2.ExecuteResponse.OK
                await self._emit_step(step, ctx, ok, elapsed)
                return resp
```

edge 与 cloud 成功路径同样在 `return resp` 前加这两行；三个 `except` 路径在 `metrics.record_agent_call(..., False)` 后加 `await self._emit_step(step, ctx, False, elapsed)`。

- [ ] **Step 3d: `orchestrator/cloud/loop.py` t2.iter span**

import 区加 `from observability.events import get_emitter`。在 `run()` 的 `while True:` 循环里、执行完一批（`current = None` 重置之前、即每轮批次执行后）加：

```python
            await get_emitter("cloud").emit_span(
                ctx.trace_id, "t2.iter",
                attrs={"replans": replans, "results": len(results)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest orchestrator/cloud/tests/test_obs_spans.py -v --import-mode=importlib`
Expected: PASS（2 passed）

- [ ] **Step 5: 回归云端**

Run: `python -m pytest orchestrator/cloud/ -q --import-mode=importlib`
Expected: 全绿（span 为旁路 emit，不改编排逻辑）

- [ ] **Step 6: Commit**

```bash
git add observability/events.py orchestrator/cloud/engine.py orchestrator/cloud/dispatch.py orchestrator/cloud/loop.py orchestrator/cloud/tests/test_obs_spans.py
git commit -m "feat(obs): cloud planning/step/t2/aggregate 链路 span"
```

---

### Task 12: dashboard 请求链路时间线

**Files:**
- Create: `dashboard/src/components/TracePanel.tsx`
- Modify: `dashboard/src/App.tsx`（维护 traces + 渲染 TracePanel）
- Test: `dashboard/src/components/TracePanel.test.tsx`

- [ ] **Step 1: Write the component**

```tsx
// dashboard/src/components/TracePanel.tsx
import type { Trace, Span } from '../types'

// node 前缀 → 语义颜色类（实际配色交 frontend-design）
function nodeClass(node: string): string {
  if (node.startsWith('route.local') || node.startsWith('step.edge')) return 'n-edge'
  if (node.startsWith('val')) return 'n-val'
  if (node.startsWith('cloud.planning')) return 'n-llm'
  if (node.startsWith('step.tool')) return 'n-tool'
  if (node.startsWith('route.cloud') || node.startsWith('step.agent') || node.startsWith('aggregate') || node.startsWith('t2')) return 'n-cloud'
  if (node.includes('suspend') || node.includes('wait')) return 'n-wait'
  return 'n-default'
}

function SpanRow({ s }: { s: Span }) {
  return (
    <div className={'node ' + nodeClass(s.node)} data-node={s.node}>
      <span className="nname">{s.node}</span>
      {s.attrs?.intent && <span className="meta">{String(s.attrs.intent)}</span>}
      {s.duration_ms > 0 && <span className="ms">{s.duration_ms}ms</span>}
      <span className={'st st-' + s.status}>{s.status}</span>
    </div>
  )
}

export function TracePanel({ traces }: { traces: Trace[] }) {
  return (
    <section className="panel trace-panel">
      <h2>请求链路</h2>
      {traces.length === 0 && <p className="empty">发一条指令看链路…</p>}
      {traces.map((t) => (
        <div key={t.trace_id} className="trace" data-trace={t.trace_id}>
          <div className="trace-id">trace #{t.trace_id.slice(0, 8)}</div>
          <div className="tl">{t.spans.map((s) => <SpanRow key={s.span_id} s={s} />)}</div>
        </div>
      ))}
    </section>
  )
}
```

- [ ] **Step 2: 在 App.tsx 维护 traces 并渲染**

在 `App.tsx` 的 import 加 `import { TracePanel } from './components/TracePanel'` 和类型 `import type { VehicleState as VS, Trace, Span } from './types'`。组件内加 traces 状态与 span 处理：

```tsx
  const [traces, setTraces] = useState<Trace[]>([])

  const addSpan = (sp: Span) => {
    setTraces((prev) => {
      const i = prev.findIndex((t) => t.trace_id === sp.trace_id)
      if (i >= 0) {
        const copy = prev.slice()
        copy[i] = { ...copy[i], spans: [...copy[i].spans, sp] }
        return copy
      }
      return [{ trace_id: sp.trace_id, spans: [sp] }, ...prev].slice(0, 30)
    })
  }
```

在 `connectObs({...})` 里补两个回调：

```tsx
      onSnapshot: (s) => { setVehicle(s.vehicle_state || {}); setTraces(s.traces || []) },
      onSpan: addSpan,
```

并在 `<main className="grid">` 内、`<VehicleState .../>` 旁加 `<TracePanel traces={traces} />`。

- [ ] **Step 3: Write the failing test**

```tsx
// dashboard/src/components/TracePanel.test.tsx
import { render, screen } from '@testing-library/react'
import { TracePanel } from './TracePanel'

test('renders spans of a trace in order', () => {
  const trace = {
    trace_id: 'abcd1234ef', spans: [
      { trace_id: 'abcd1234ef', span_id: '1', ts: 1, service: 'edge', node: 'route.local', status: 'ok', duration_ms: 0, attrs: { intent: 'hvac.set' } },
      { trace_id: 'abcd1234ef', span_id: '2', ts: 2, service: 'edge', node: 'val.execute', status: 'ok', duration_ms: 8, attrs: {} },
    ],
  }
  render(<TracePanel traces={[trace]} />)
  expect(screen.getByText('route.local')).toBeTruthy()
  expect(screen.getByText('val.execute')).toBeTruthy()
  expect(document.querySelector('[data-node="val.execute"]')!.className).toContain('n-val')
})
```

- [ ] **Step 4: Run test + build**

Run: `cd dashboard && npm test && npm run build`
Expected: vitest 2 passed（含 Task 8 的 VehicleState 测试）；构建成功

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/TracePanel.tsx dashboard/src/components/TracePanel.test.tsx dashboard/src/App.tsx
git commit -m "feat(dashboard): 请求链路时间线"
```

---

## Phase 3 — Agent 运行态 + 车辆动态 + 对照实验（需求 2、4）

### Task 13: registry 健康周期上报

**Files:**
- Modify: `registry/store.py`（加 `all()`）
- Modify: `registry/main.py`（加 `emit_all_health` + 周期 task）
- Test: `registry/tests/test_health_emit.py`

- [ ] **Step 1: Write the failing test**

```python
# registry/tests/test_health_emit.py
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # registry 目录入 path

from store import Store
from main import emit_all_health


class _M:
    agent_id = "navigation"
    deployment = "cloud"
    kind = "agent"
    requires_permissions: list = []
    capabilities: list = []


def test_emit_all_health_sends_each_agent():
    s = Store()
    s.register(_M(), "navigation:50061")
    sent = []

    class E:
        async def emit_health(self, **kw):
            sent.append(kw)

    asyncio.run(emit_all_health(s, E()))
    assert sent and sent[0]["agent_id"] == "navigation"
    assert sent[0]["healthy"] is True and sent[0]["deployment"] == "cloud"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest registry/tests/test_health_emit.py -v --import-mode=importlib`
Expected: FAIL（`ImportError: cannot import name 'emit_all_health'` / `Store` 无 `all`）

- [ ] **Step 3a: `registry/store.py` 加 `all()`**

在 `Store` 类末尾（`list` 方法后）加：

```python
    def all(self):
        """返回全部记录（含不健康的），供可观测监控用。"""
        return list(self._agents.values())
```

- [ ] **Step 3b: `registry/main.py` 加 emit + 周期 task**

```python
"""Agent Registry 启动入口。"""
import asyncio
import os

import grpc
from cockpit.registry.v1 import registry_pb2_grpc

from server import RegistryServicer
from observability.events import EventEmitter


async def emit_all_health(store, emitter):
    """把当前所有 agent 的健康态 emit 出去（best-effort）。"""
    for rec in store.all():
        m = rec.manifest
        await emitter.emit_health(
            agent_id=m.agent_id, healthy=rec.healthy,
            fail_count=rec.fail_count, last_seen=rec.last_seen,
            deployment=getattr(m, "deployment", ""),
            kind=getattr(m, "kind", ""))


async def _health_loop(store, emitter, interval=5):
    while True:
        await emit_all_health(store, emitter)
        await asyncio.sleep(interval)


async def serve():
    port = int(os.getenv("REGISTRY_PORT", "50051"))
    server = grpc.aio.server()
    servicer = RegistryServicer()
    registry_pb2_grpc.add_RegistryServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    emitter = EventEmitter("registry")
    asyncio.create_task(_health_loop(servicer.store, emitter))
    print(f"[registry] serving on :{port}", flush=True)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
```

> `observability/` 在 registry 容器内可达：`registry/Dockerfile` 需 `COPY observability/`（见 Task 17 同款处理）。本 task 仅改源码与测试。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest registry/tests/test_health_emit.py -v --import-mode=importlib`
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add registry/store.py registry/main.py registry/tests/test_health_emit.py
git commit -m "feat(obs): registry 健康周期上报"
```

---

### Task 14: cloud agent 指标上报

**Files:**
- Modify: `observability/metrics.py`（加 `agent_snapshot`）
- Modify: `orchestrator/cloud/dispatch.py`（`_emit_step` 加 metric emit）
- Test: `observability/tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# observability/tests/test_metrics.py
from observability.metrics import MetricsCollector


def test_agent_snapshot_aggregates():
    m = MetricsCollector()
    m.record_agent_call("navigation", 100, True)
    m.record_agent_call("navigation", 200, True)
    m.record_agent_call("navigation", 300, False)
    snap = m.agent_snapshot("navigation")
    assert snap["count"] == 3
    assert snap["avg_ms"] == 200.0
    assert snap["error_rate"] == round(1 / 3, 3)


def test_agent_snapshot_missing_returns_none():
    assert MetricsCollector().agent_snapshot("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest observability/tests/test_metrics.py -v --import-mode=importlib`
Expected: FAIL（`AttributeError: 'MetricsCollector' object has no attribute 'agent_snapshot'`）

- [ ] **Step 3a: `observability/metrics.py` 加 `agent_snapshot`**

在 `MetricsCollector` 的 `snapshot` 方法之后加：

```python
    def agent_snapshot(self, agent_id: str) -> dict | None:
        """单个 agent 的累积指标（供 emit_metric）。无记录返回 None。"""
        m = self._agent.get(agent_id)
        if not m:
            return None
        return {"count": m.count, "avg_ms": round(m.avg_ms, 1),
                "error_rate": round(m.error_rate, 3)}
```

- [ ] **Step 3b: `orchestrator/cloud/dispatch.py` 的 `_emit_step` 加 metric**

把 Task 11 写的 `_emit_step` 替换为（在 span 之后顺带 emit 该 agent 累积指标）：

```python
    async def _emit_step(self, step, ctx, ok: bool, elapsed: float):
        try:
            emitter = get_emitter("cloud")
            await emitter.emit_span(
                getattr(ctx, "trace_id", ""), self._step_node(step),
                status="ok" if ok else "err", duration_ms=elapsed,
                attrs={"intent": step.intent, "agent_id": step.agent_id,
                       "kind": step.kind, "deployment": step.deployment})
            snap = metrics.agent_snapshot(step.agent_id)
            if snap:
                await emitter.emit_metric(step.agent_id, **snap)
        except Exception:
            pass
```

（`metrics` 已在 `dispatch.py` 顶部 import。）

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest observability/tests/test_metrics.py orchestrator/cloud/tests/test_obs_spans.py -v --import-mode=importlib`
Expected: PASS（4 passed：2 metrics + 2 spans，dispatch 回归不破）

- [ ] **Step 5: Commit**

```bash
git add observability/metrics.py orchestrator/cloud/dispatch.py observability/tests/test_metrics.py
git commit -m "feat(obs): cloud agent 指标上报"
```

---

### Task 15: debug 车辆动态闭环（collector → NATS → edge → VAL）

**Files:**
- Modify: `orchestrator/edge/server.py`（`EdgeOrchestratorServicer` 加 `apply_debug` + `_DEBUG_KEYS`）
- Modify: `orchestrator/edge/main.py`（订阅 `obs.debug.vehicle.set`）
- Test: `orchestrator/edge/tests/test_debug_control.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/edge/tests/test_debug_control.py
from server import EdgeOrchestratorServicer


def test_apply_debug_allows_environment_key(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    svc = EdgeOrchestratorServicer()
    assert svc.apply_debug("speed_kmh", 130) is True
    assert svc.val.state["speed_kmh"] == 130


def test_apply_debug_rejects_vehicle_control_key(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    svc = EdgeOrchestratorServicer()
    assert svc.apply_debug("hvac_on", True) is False
    assert svc.val.state["hvac_on"] is False   # 车控字段不可经 debug 写（纵深防御）
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest orchestrator/edge/tests/test_debug_control.py -v --import-mode=importlib`
Expected: FAIL（`AttributeError: ... has no attribute 'apply_debug'`）

- [ ] **Step 3a: `orchestrator/edge/server.py` 加 `apply_debug`**

在 `EdgeOrchestratorServicer` 类体内（`drain_state` 附近）加：

```python
    _DEBUG_KEYS = {"speed_kmh", "battery", "gear", "location"}

    def apply_debug(self, key: str, value) -> bool:
        """debug 通道执行：仅放行环境量（纵深防御，守 debug 只设环境量红线）。"""
        if key not in self._DEBUG_KEYS:
            return False
        self._change_source.set("debug")
        self.val.set_env(key, value)
        return True
```

- [ ] **Step 3b: `orchestrator/edge/main.py` 订阅 debug topic**

在 `serve()` 中（`await servicer.emit_snapshot()` 之后）加订阅协程：

```python
    async def _subscribe_debug():
        url = os.getenv("NATS_URL", "")
        if not url:
            return
        try:
            import json
            import nats
            nc = await nats.connect(url, max_reconnect_attempts=-1)
        except Exception as exc:
            print(f"[edge-orchestrator] debug subscribe skipped: {exc}", flush=True)
            return

        async def _cb(msg):
            try:
                d = json.loads(msg.data.decode())
                servicer.apply_debug(d.get("key"), d.get("value"))
            except Exception:
                pass

        await nc.subscribe("obs.debug.vehicle.set", cb=_cb)

    asyncio.create_task(_subscribe_debug())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest orchestrator/edge/tests/test_debug_control.py -v --import-mode=importlib`
Expected: PASS（2 passed）

- [ ] **Step 5: 回归端侧**

Run: `python -m pytest orchestrator/edge/tests/ test/smoke_edge.py -q --import-mode=importlib`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add orchestrator/edge/server.py orchestrator/edge/main.py orchestrator/edge/tests/test_debug_control.py
git commit -m "feat(obs): debug 车辆动态闭环（NATS→edge→VAL，环境量白名单）"
```

---

### Task 16: dashboard Agent 区 + 车辆动态 + 命令栏（对照实验）

**Files:**
- Create: `dashboard/src/components/AgentList.tsx`、`dashboard/src/components/Dynamics.tsx`、`dashboard/src/components/CommandBar.tsx`
- Modify: `dashboard/src/App.tsx`（agents 状态 + 渲染四区）
- Test: `dashboard/src/components/Dynamics.test.tsx`、`dashboard/src/components/CommandBar.test.tsx`

- [ ] **Step 1: 三个组件**

```tsx
// dashboard/src/components/AgentList.tsx
import type { AgentInfo } from '../types'

export function AgentList({ agents }: { agents: Record<string, AgentInfo> }) {
  const ids = Object.keys(agents)
  return (
    <section className="panel agents">
      <h2>Agent 运行状态</h2>
      {ids.length === 0 && <p className="empty">等待 agent 上报…</p>}
      {ids.map((id) => {
        const a = agents[id]
        return (
          <div key={id} className={'arow' + (a.healthy === false ? ' down' : '')} data-agent={id}>
            <span className="anm">{id}</span>
            {a.kind && <span className="kind">{a.kind}</span>}
            <span className="ah">{a.healthy === false ? '离线' : '健康'}</span>
            <span className="am">
              {a.count != null && <b>{a.count} 调用</b>}
              {a.avg_ms != null && <b>{a.avg_ms}ms</b>}
              {a.error_rate != null && <b>{Math.round(a.error_rate * 100)}%</b>}
              {a.fail_count ? <b>fail×{a.fail_count}</b> : null}
            </span>
          </div>
        )
      })}
    </section>
  )
}
```

```tsx
// dashboard/src/components/Dynamics.tsx
import { setVehicleEnv } from '../api'
import type { VehicleState } from '../types'

export function Dynamics({ state }: { state: VehicleState }) {
  const speed = Number(state.speed_kmh ?? 0)
  const battery = Number(state.battery ?? 0)
  return (
    <section className="panel dynamics">
      <h2>车辆动态</h2>
      <label className="drow">车速 {speed} km/h
        <input type="range" min={0} max={180} value={speed}
               onChange={(e) => setVehicleEnv('speed_kmh', Number(e.target.value))} />
      </label>
      <label className="drow">电量 {battery}%
        <input type="range" min={0} max={100} value={battery}
               onChange={(e) => setVehicleEnv('battery', Number(e.target.value))} />
      </label>
      <p className="safety">车速 &gt; 120 时，VAL 会拦截「开窗」等指令——拖动复现</p>
    </section>
  )
}
```

```tsx
// dashboard/src/components/CommandBar.tsx
import { useState } from 'react'

const EDGE = (import.meta.env.VITE_EDGE_GATEWAY_URL as string) || 'http://localhost:8090'
const WS_URL = EDGE.replace(/^http/, 'ws') + '/ws'

export function genTraceId(): string {
  const raw = (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
    ? crypto.randomUUID() : Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2)
  return raw.replace(/-/g, '').slice(0, 16)
}

export function CommandBar({ onTrace }: { onTrace?: (tid: string) => void }) {
  const [text, setText] = useState('空调调到26度')
  const send = () => {
    const tid = genTraceId()
    onTrace?.(tid)
    const ws = new WebSocket(WS_URL)
    ws.onopen = () => ws.send(JSON.stringify({
      text, session_id: 'dashboard', is_confirmation: false, meta: { trace_id: tid },
    }))
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data)
      if (m.type === 'final') ws.close()  // 收到终态即关闭本次连接
    }
    ws.onerror = () => ws.close()
  }
  return (
    <div className="cmd">
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder="发一条指令做对照…" />
      <button onClick={send}>发送</button>
    </div>
  )
}
```

- [ ] **Step 2: App.tsx 接 agents 与四区**

在 `App.tsx` 加 import：

```tsx
import { AgentList } from './components/AgentList'
import { Dynamics } from './components/Dynamics'
import { CommandBar } from './components/CommandBar'
import type { AgentInfo } from './types'
```

组件内加 agents 状态与回调：

```tsx
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
  const mergeAgent = (id: string, patch: Partial<AgentInfo>) =>
    setAgents((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }))
```

在 `connectObs({...})` 里补：

```tsx
      onSnapshot: (s) => { setVehicle(s.vehicle_state || {}); setTraces(s.traces || []); setAgents(s.agents || {}) },
      onHealth: (ev) => mergeAgent(ev.agent_id, { healthy: ev.healthy, fail_count: ev.fail_count, last_seen: ev.last_seen, deployment: ev.deployment, kind: ev.kind }),
      onMetric: (ev) => mergeAgent(ev.agent_id, { count: ev.count, avg_ms: ev.avg_ms, error_rate: ev.error_rate }),
```

把 `<main className="grid">` 内容改为四区 + 命令栏：

```tsx
      <main className="grid">
        <div className="col-left">
          <CommandBar onTrace={() => {}} />
          <TracePanel traces={traces} />
        </div>
        <div className="col-right">
          <VehicleState state={vehicle} changed={changed} />
          <Dynamics state={vehicle} />
          <AgentList agents={agents} />
        </div>
      </main>
```

- [ ] **Step 3: Write the failing tests**

```tsx
// dashboard/src/components/Dynamics.test.tsx
import { render, screen } from '@testing-library/react'
import { Dynamics } from './Dynamics'

test('renders speed and battery from state', () => {
  render(<Dynamics state={{ speed_kmh: 60, battery: 72 }} />)
  expect(screen.getByText(/车速 60 km\/h/)).toBeTruthy()
  expect(screen.getByText(/电量 72%/)).toBeTruthy()
})
```

```tsx
// dashboard/src/components/CommandBar.test.tsx
import { genTraceId } from './CommandBar'

test('genTraceId returns 16 hex chars', () => {
  const t = genTraceId()
  expect(t).toMatch(/^[0-9a-f]{16}$/)
})
```

- [ ] **Step 4: Run tests + build**

Run: `cd dashboard && npm test && npm run build`
Expected: vitest 全 passed（VehicleState + TracePanel + Dynamics + CommandBar）；构建成功

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/AgentList.tsx dashboard/src/components/Dynamics.tsx dashboard/src/components/CommandBar.tsx dashboard/src/components/Dynamics.test.tsx dashboard/src/components/CommandBar.test.tsx dashboard/src/App.tsx
git commit -m "feat(dashboard): Agent 区 + 车辆动态 + 对照实验命令栏"
```

---

### Task 17: dashboard 容器化 + compose 注册 + 全栈验收

**Files:**
- Create: `dashboard/Dockerfile`
- Modify: `deploy/docker-compose.yaml`、`registry/Dockerfile`、`orchestrator/edge/Dockerfile`、`orchestrator/cloud/Dockerfile`（确保各容器含 `observability/`）

- [ ] **Step 1: `dashboard/Dockerfile`**

```dockerfile
# dashboard/Dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . ./
EXPOSE 5174
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

- [ ] **Step 2: edge / registry Dockerfile 补 observability（cloud 已含，无需改）**

`orchestrator/cloud/Dockerfile` 已 `COPY observability /app/observability` 且 `PYTHONPATH=/app:/app/gen/python`——**不改**。

`orchestrator/edge/Dockerfile`：在 `COPY orchestrator/edge /app/orchestrator/edge` 之后加一行，并把 `ENV PYTHONPATH=/app/gen/python` 整行替换为带 `/app`：

```dockerfile
COPY observability /app/observability
ENV PYTHONPATH=/app:/app/gen/python
```

`registry/Dockerfile`：在 `COPY registry /app/registry` 之后加一行，并把 `ENV PYTHONPATH=/app/gen/python` 整行替换为带 `/app`：

```dockerfile
COPY observability /app/observability
ENV PYTHONPATH=/app:/app/gen/python
```

> 为什么必须：edge/registry 此前不依赖 `observability`，本计划新增的埋点让它们 `import observability.events`，容器内需 `observability/` 在镜像且 `/app` 在 `PYTHONPATH`。本地 `pytest` 不受影响（root `conftest.py` 已把项目根入 path）。

- [ ] **Step 3: `deploy/docker-compose.yaml` 注册 dashboard**

在 `hmi` 服务之后加：

```yaml
  dashboard:
    build: { context: .., dockerfile: dashboard/Dockerfile }
    environment:
      VITE_COLLECTOR_URL: http://localhost:8092
      VITE_EDGE_GATEWAY_URL: http://localhost:8090
    ports: ["5174:5174"]
    depends_on: [observability-collector, edge-gateway]
```

- [ ] **Step 4: 全量回归（守"不破坏现状"不变量）**

Run: `python -m pytest --import-mode=importlib -q`
Expected: 现有 325 passed 之上**净增本计划新增用例**全部 PASS，2 skipped 不变（无回归）。

Run: `python test/smoke_edge.py`
Expected: 13 passed, 0 failed

- [ ] **Step 5: 全栈验收（对照设计文档 §8，需 docker）**

```bash
cp .env.example deploy/.env   # 若尚未
docker compose -f deploy/docker-compose.yaml up -d --build
```
手动核对：
1. 打开 `http://localhost:5174`，collector 徽章「已连」。
2. 命令栏发"空调调到26度" → 右侧空调卡片即时 `刚变` 且左侧链路出现 `route.local → val.execute`，同一 `trace_id`。
3. 拖车速到 130 → 发"打开车窗" → 链路 `val.execute` 状态为安全门控拒绝、车窗状态不变。
4. Agent 区显示各 agent 健康/调用/时延；停掉一个 agent 容器 → 该 agent 转「离线」。
5. 停掉 nats 容器 → 主链路（HMI 发指令）仍正常，仪表盘徽章转红、退化为快照。

- [ ] **Step 6: Commit**

```bash
git add dashboard/Dockerfile deploy/docker-compose.yaml registry/Dockerfile orchestrator/edge/Dockerfile
git commit -m "build(obs): dashboard 容器化 + compose 注册 + 全栈验收"
```

---

## 全量验收对照（设计文档 §8）

| 验收项 | 覆盖任务 |
|---|---|
| 状态对照（发指令→卡片 diff + 链路同 diff） | Task 6/7/8 + 10 + 16 |
| 链路完整（云端复杂意图全链路串联） | Task 9/10/11/12 |
| Agent 态（健康/调用/时延/离线摘除） | Task 13/14/16 |
| 车辆动态（车速>120 触发安全门控拒绝） | Task 6/15/16 |
| 安全（debug 写车控被拒；发指令经 VAL） | Task 4/15（双层白名单）+ Task 16（复用 edge-gateway） |
| 不破坏（325 passed 全绿；NATS 停主链路不变） | Task 1/7（best-effort）+ Task 17 回归 |

## 回归基线命令

```bash
python -m pytest --import-mode=importlib -q     # 后端全量
python test/smoke_edge.py                        # 端侧 13/13
cd dashboard && npm test && npm run build        # 前端
```

