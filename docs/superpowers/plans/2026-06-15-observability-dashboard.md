# 座舱 Agent 可观测仪表盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 car-agent 补一层可观测层并新增独立仪表盘前端，实时看车辆状态（含变更 diff）、车辆动态、请求链路走向、各 Agent 运行态，并支持"发指令→看链路→看状态变化"的对照实验。

**Architecture:** 复用架构 §8 规划但未接通的 NATS 事件总线作为汇聚通道；各服务在关键节点 `fire-and-forget` 发事件；新增 `observability-collector`（FastAPI）订阅聚合并对前端暴露 REST 快照 + WebSocket 增量；新增 `dashboard`（React）消费 collector 并复用现有 edge-gateway 入口发指令。**不改任何 `.proto`**。

**Tech Stack:** Python 3.11 + FastAPI + nats-py（后端）；React + TypeScript + Vite（前端）；NATS（事件总线，已在 compose）；pytest（后端测试）；vitest/tsc（前端测试）。

**设计真相源：** `docs/design/2026-06-15-observability-dashboard.md`（本计划严格据此展开）。

---

## 不变量（每个 task 都不得违反）

1. **车控只经 VAL**：仪表盘发指令走与 HMI 相同的 edge-gateway 入口；debug 只设环境量（speed_kmh/battery/gear/location），绝不写车控输出状态。
2. **埋点 best-effort**：所有 `emit_*` fire-and-forget、失败静默、不阻塞主链路、NATS 不可用不破坏离线。
3. **不改 proto**：可观测事件与 registry 健康都走 NATS JSON。
4. **不破坏现状**：每个后端 task 完成后 `python -m pytest --import-mode=importlib` 保持现有 325 passed 全绿。

---

## 文件结构（创建/修改一览）

**新增：**
| 文件 | 职责 |
|---|---|
| `observability/events.py` | `EventEmitter`：emit_span/state/metric/health，懒连 NATS，best-effort |
| `observability/tests/test_events.py` | events 单测 |
| `observability/collector/__init__.py` | 包标记 |
| `observability/collector/store.py` | `CollectorStore`：车辆状态镜像 / 链路环形缓冲 / agent 聚合 |
| `observability/collector/server.py` | FastAPI app：REST + WS + NATS 订阅 + debug 转发 |
| `observability/collector/main.py` | 启动入口（uvicorn） |
| `observability/collector/requirements.txt` | fastapi/uvicorn/nats-py/httpx |
| `observability/collector/Dockerfile` | 容器 |
| `observability/collector/tests/test_store.py` | store 单测 |
| `observability/collector/tests/test_server.py` | REST/debug 单测（TestClient） |
| `dashboard/` | React+TS Vite 应用（脚手架 + api client + 四区组件） |

**修改：**
| 文件 | 改动 |
|---|---|
| `orchestrator/edge/val.py` | `__init__(on_change=None)` + state 写入处回调 |
| `orchestrator/edge/server.py` | trace_id 贯穿 + route/val span emit |
| `orchestrator/edge/main.py` | 注入 publisher（队列→后台 task）+ 订阅 debug topic + 启动 snapshot |
| `orchestrator/cloud/engine.py` | planning/aggregate span |
| `orchestrator/cloud/dispatch.py` | 每 step span + metric emit |
| `orchestrator/cloud/loop.py` | t2.iter span |
| `registry/store.py` + `registry/main.py` | 健康 emit |
| `orchestrator/edge/requirements.txt`、`orchestrator/cloud/requirements.txt`、`registry/requirements.txt` | 加 `nats-py` |
| `deploy/docker-compose.yaml` | 加 `observability-collector` + `dashboard` 服务 |

> 前端组件的**逻辑与数据绑定**在本计划中给全（complete code）；**视觉样式**用语义 className 占位，最终由 `frontend-design` skill 在执行阶段统一打磨（深空座舱风格，呼应 `hmi/`）。这是设计阶段已确认的分工。

---

## Phase 0 — 可观测地基（无行为变化，collector 能起、能订阅、能推空数据）

### Task 1: EventEmitter（可观测出口）

**Files:**
- Create: `observability/events.py`
- Test: `observability/tests/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
# observability/tests/test_events.py
import asyncio
import json
from observability.events import EventEmitter


def test_emit_is_noop_without_nats_url():
    """未配 NATS_URL → 整体禁用，emit 不抛、不连接。"""
    em = EventEmitter("edge", nats_url="")
    asyncio.run(em.emit_span("t1", "fast_intent"))  # 不抛即通过
    assert em._disabled is True


def test_emit_does_not_raise_when_unreachable():
    """NATS 连不上 → 静默降级，绝不影响主链路。"""
    em = EventEmitter("edge", nats_url="nats://127.0.0.1:1")  # 无人监听
    asyncio.run(em.emit_state([{"key": "hvac_temp", "old": 24, "new": 26}], "T0"))
    assert em._disabled is True  # 连接失败后标记禁用，不反复重试


def test_emit_publishes_payload_when_connected(monkeypatch):
    """连上时 emit_span 应 publish 到 obs.span，payload 含 service/ts/node。"""
    em = EventEmitter("cloud", nats_url="nats://x")
    sent = []

    class FakeNC:
        async def publish(self, subject, data):
            sent.append((subject, data))

    async def fake_conn():
        return FakeNC()

    monkeypatch.setattr(em, "_conn", fake_conn)
    asyncio.run(em.emit_span("trace-9", "step.agent:navigation",
                             status="ok", duration_ms=340,
                             attrs={"intent": "navigation.search_poi"}))
    assert sent and sent[0][0] == "obs.span"
    body = json.loads(sent[0][1])
    assert body["trace_id"] == "trace-9"
    assert body["node"] == "step.agent:navigation"
    assert body["service"] == "cloud"
    assert body["attrs"]["intent"] == "navigation.search_poi"
    assert "ts" in body and "span_id" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest observability/tests/test_events.py -v --import-mode=importlib`
Expected: FAIL（`ModuleNotFoundError: observability.events`）

- [ ] **Step 3: Write minimal implementation**

```python
# observability/events.py
"""可观测事件出口：把状态变更 / 链路 span / 指标 fire-and-forget 发到 NATS。

设计不变量（违反即 bug）：
- best-effort：任何 emit 失败静默、不抛、不阻塞主链路。
- NATS 不可用（未配 NATS_URL / 连不上）整体降级为 no-op，不破坏离线快路径。
"""
from __future__ import annotations
import os
import json
import time
import uuid
import asyncio
import logging

logger = logging.getLogger("obs.events")


def _now_ms() -> int:
    return int(time.time() * 1000)


class EventEmitter:
    def __init__(self, service: str, nats_url: str | None = None):
        self.service = service
        self.nats_url = nats_url if nats_url is not None else os.getenv("NATS_URL", "")
        self._nc = None
        self._lock = asyncio.Lock()
        self._disabled = not self.nats_url  # 无 URL 直接禁用

    async def _conn(self):
        if self._disabled:
            return None
        if self._nc is not None:
            return self._nc
        async with self._lock:
            if self._nc is not None or self._disabled:
                return self._nc
            try:
                import nats
                self._nc = await nats.connect(
                    self.nats_url, connect_timeout=2,
                    max_reconnect_attempts=3, allow_reconnect=True)
                logger.info("obs events connected to NATS (service=%s)", self.service)
            except Exception as e:
                self._disabled = True  # 连不上彻底降级，避免反复重试刷日志
                logger.debug("NATS unavailable, obs disabled: %s", e)
        return self._nc

    async def _emit(self, subject: str, payload: dict):
        try:
            nc = await self._conn()
            if nc is None:
                return
            payload.setdefault("ts", _now_ms())
            payload.setdefault("service", self.service)
            await nc.publish(subject, json.dumps(payload, ensure_ascii=False).encode())
        except Exception as e:  # best-effort：绝不影响主链路
            logger.debug("emit %s failed: %s", subject, e)

    async def emit_span(self, trace_id, node, status="ok", duration_ms=0,
                        attrs=None, parent_id="", span_id=""):
        await self._emit("obs.span", {
            "trace_id": trace_id, "span_id": span_id or uuid.uuid4().hex[:12],
            "parent_id": parent_id, "node": node, "status": status,
            "duration_ms": round(duration_ms, 1), "attrs": attrs or {},
        })

    async def emit_state(self, changes, source, trace_id=""):
        await self._emit("vehicle.state.changed", {
            "trace_id": trace_id, "source": source, "changes": changes,
        })

    async def emit_metric(self, agent_id, count, avg_ms, error_rate, **extra):
        await self._emit("obs.metric", {
            "agent_id": agent_id, "count": count, "avg_ms": avg_ms,
            "error_rate": error_rate, **extra,
        })

    async def emit_health(self, agent_id, healthy, fail_count, last_seen,
                          deployment="", kind=""):
        await self._emit("obs.agent.health", {
            "agent_id": agent_id, "healthy": healthy, "fail_count": fail_count,
            "last_seen": last_seen, "deployment": deployment, "kind": kind,
        })

    async def close(self):
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest observability/tests/test_events.py -v --import-mode=importlib`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add observability/events.py observability/tests/test_events.py
git commit -m "feat(obs): EventEmitter best-effort NATS 可观测出口"
```

---

### Task 2: 引入 nats-py 依赖

**Files:**
- Modify: `orchestrator/edge/requirements.txt`、`orchestrator/cloud/requirements.txt`、`registry/requirements.txt`

- [ ] **Step 1: 在三个 requirements.txt 末尾各加一行**

`orchestrator/edge/requirements.txt`（在 `PyYAML>=6.0` 后追加）：
```
nats-py>=2.6.0
```

`orchestrator/cloud/requirements.txt`（在 `protobuf==7.35.0` 后追加）：
```
nats-py>=2.6.0
```

`registry/requirements.txt`（在 `protobuf==7.35.0` 后追加）：
```
nats-py>=2.6.0
```

- [ ] **Step 2: 本地装上（供后续 task 运行用）**

Run: `pip install "nats-py>=2.6.0"`
Expected: 安装成功（`Successfully installed nats-py-...`）

- [ ] **Step 3: Commit**

```bash
git add orchestrator/edge/requirements.txt orchestrator/cloud/requirements.txt registry/requirements.txt
git commit -m "build(obs): 各服务加 nats-py 依赖"
```

---

### Task 3: CollectorStore（内存聚合）

**Files:**
- Create: `observability/collector/__init__.py`、`observability/collector/store.py`
- Test: `observability/collector/tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# observability/collector/tests/test_store.py
from observability.collector.store import CollectorStore


def test_apply_state_builds_mirror():
    s = CollectorStore()
    s.apply_state({"source": "T0", "changes": [
        {"key": "hvac_temp", "old": 24, "new": 26},
        {"key": "hvac_on", "old": False, "new": True},
    ]})
    assert s.vehicle_state["hvac_temp"] == 26
    assert s.vehicle_state["hvac_on"] is True


def test_apply_span_groups_by_trace():
    s = CollectorStore()
    s.apply_span({"trace_id": "t1", "node": "fast_intent", "ts": 1})
    s.apply_span({"trace_id": "t1", "node": "val.execute", "ts": 2})
    assert len(s.traces["t1"]["spans"]) == 2
    assert s.traces["t1"]["spans"][1]["node"] == "val.execute"


def test_traces_ring_buffer_evicts_oldest():
    s = CollectorStore(max_traces=2)
    for i in range(3):
        s.apply_span({"trace_id": f"t{i}", "node": "x", "ts": i})
    assert "t0" not in s.traces  # 最旧被淘汰
    assert set(s.traces.keys()) == {"t1", "t2"}


def test_apply_health_and_metric_merge():
    s = CollectorStore()
    s.apply_health({"agent_id": "navigation", "healthy": True,
                    "fail_count": 0, "last_seen": 1.0})
    s.apply_metric({"agent_id": "navigation", "count": 12,
                    "avg_ms": 230.0, "error_rate": 0.0})
    a = s.agents["navigation"]
    assert a["healthy"] is True and a["count"] == 12 and a["avg_ms"] == 230.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest observability/collector/tests/test_store.py -v --import-mode=importlib`
Expected: FAIL（`ModuleNotFoundError: observability.collector.store`）

- [ ] **Step 3: Write minimal implementation**

```python
# observability/collector/__init__.py
```
（空文件，仅作包标记）

```python
# observability/collector/store.py
"""collector 内存聚合：车辆状态镜像 / 链路环形缓冲 / agent 运行态。

单进程内存即可（PoC）。trace 用环形缓冲控制数量，旧的淘汰。
"""
from __future__ import annotations
from collections import OrderedDict


class CollectorStore:
    def __init__(self, max_traces: int = 200):
        self.vehicle_state: dict = {}                 # key -> 当前值
        self.traces: "OrderedDict[str, dict]" = OrderedDict()  # trace_id -> {spans, ...}
        self.agents: dict[str, dict] = {}             # agent_id -> 健康+指标
        self._max_traces = max_traces

    def apply_state(self, ev: dict):
        for ch in ev.get("changes", []):
            self.vehicle_state[ch["key"]] = ch["new"]

    def apply_span(self, ev: dict):
        tid = ev.get("trace_id") or "unknown"
        tr = self.traces.get(tid)
        if tr is None:
            tr = {"trace_id": tid, "spans": [], "started": ev.get("ts")}
            self.traces[tid] = tr
            while len(self.traces) > self._max_traces:
                self.traces.popitem(last=False)       # 淘汰最旧
        tr["spans"].append(ev)
        tr["updated"] = ev.get("ts")
        self.traces.move_to_end(tid)

    def apply_metric(self, ev: dict):
        a = self.agents.setdefault(ev["agent_id"], {})
        for k in ("count", "avg_ms", "error_rate",
                  "route_hits", "degrade", "llm_tokens"):
            if k in ev:
                a[k] = ev[k]

    def apply_health(self, ev: dict):
        a = self.agents.setdefault(ev["agent_id"], {})
        for k in ("healthy", "fail_count", "last_seen", "deployment", "kind"):
            if k in ev:
                a[k] = ev[k]

    def snapshot_traces(self, limit: int = 50) -> list:
        items = list(self.traces.values())[-limit:]
        return list(reversed(items))                  # 最新在前
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest observability/collector/tests/test_store.py -v --import-mode=importlib`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add observability/collector/__init__.py observability/collector/store.py observability/collector/tests/test_store.py
git commit -m "feat(obs): CollectorStore 内存聚合（状态/链路/agent）"
```

---

### Task 4: collector FastAPI（REST + WS + NATS 订阅 + debug 转发）

**Files:**
- Create: `observability/collector/server.py`、`observability/collector/main.py`
- Test: `observability/collector/tests/test_server.py`

- [ ] **Step 1: Write the failing test**

```python
# observability/collector/tests/test_server.py
from fastapi.testclient import TestClient
from observability.collector.server import create_app


def _client():
    app = create_app()  # nc 默认 None，不连真实 NATS
    return TestClient(app)


def test_vehicle_state_reflects_store():
    c = _client()
    c.app.state.store.apply_state({"source": "T0",
        "changes": [{"key": "hvac_temp", "old": 24, "new": 26}]})
    r = c.get("/api/vehicle/state")
    assert r.status_code == 200 and r.json()["hvac_temp"] == 26


def test_debug_rejects_non_whitelisted_key():
    c = _client()
    r = c.post("/api/debug/vehicle", json={"key": "hvac_temp", "value": 30})
    assert r.json()["ok"] is False  # 车控字段禁止经 debug 写


def test_debug_allows_environment_key():
    c = _client()
    r = c.post("/api/debug/vehicle", json={"key": "speed_kmh", "value": 130})
    assert r.json()["ok"] is True and r.json()["value"] == 130


def test_agents_endpoint():
    c = _client()
    c.app.state.store.apply_health({"agent_id": "navigation",
        "healthy": True, "fail_count": 0, "last_seen": 1.0})
    r = c.get("/api/agents")
    assert r.status_code == 200 and "navigation" in r.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest observability/collector/tests/test_server.py -v --import-mode=importlib`
Expected: FAIL（`ModuleNotFoundError: observability.collector.server`）

- [ ] **Step 3: Write minimal implementation**

```python
# observability/collector/server.py
"""collector FastAPI：订阅 NATS 可观测事件，REST 快照 + WS 实时增量 + debug 转发。"""
from __future__ import annotations
import os
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .store import CollectorStore

logger = logging.getLogger("obs.collector")

SUBJECTS = ["vehicle.state.changed", "obs.span", "obs.metric", "obs.agent.health"]
DEBUG_ON = os.getenv("DEBUG_VEHICLE_CONTROL", "true").lower() == "true"
DEBUG_KEYS = {"speed_kmh", "battery", "gear", "location"}


class Hub:
    """WS 广播：维护连接集合，向所有前端推增量。"""
    def __init__(self):
        self.clients: set = set()

    async def join(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def leave(self, ws):
        self.clients.discard(ws)

    async def broadcast(self, msg: dict):
        text = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)


def create_app(store: CollectorStore | None = None, hub: Hub | None = None) -> FastAPI:
    app = FastAPI(title="cockpit-observability-collector")
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])
    app.state.store = store or CollectorStore()
    app.state.hub = hub or Hub()
    app.state.nc = None

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "nats": app.state.nc is not None}

    @app.get("/api/vehicle/state")
    async def vehicle_state():
        return app.state.store.vehicle_state

    @app.get("/api/traces")
    async def traces(limit: int = 50):
        return app.state.store.snapshot_traces(limit)

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str):
        return app.state.store.traces.get(trace_id) or {"error": "not found"}

    @app.get("/api/agents")
    async def agents():
        return app.state.store.agents

    @app.post("/api/debug/vehicle")
    async def debug_vehicle(body: dict):
        if not DEBUG_ON:
            return {"ok": False, "error": "debug disabled"}
        key, value = body.get("key"), body.get("value")
        if key not in DEBUG_KEYS:          # 安全：只放行环境量，禁车控旁路
            return {"ok": False, "error": f"key not allowed: {key}"}
        nc = app.state.nc
        if nc is not None:
            await nc.publish("obs.debug.vehicle.set",
                             json.dumps({"key": key, "value": value}).encode())
        return {"ok": True, "key": key, "value": value}

    @app.websocket("/stream")
    async def stream(ws: WebSocket):
        hub = app.state.hub
        await hub.join(ws)
        try:
            await ws.send_text(json.dumps({
                "type": "snapshot",
                "vehicle_state": app.state.store.vehicle_state,
                "agents": app.state.store.agents,
                "traces": app.state.store.snapshot_traces(30),
            }, ensure_ascii=False))
            while True:
                await ws.receive_text()    # 前端只接收；收消息仅用于保活
        except WebSocketDisconnect:
            hub.leave(ws)
        except Exception:
            hub.leave(ws)

    return app


async def ingest_loop(app: FastAPI):
    """订阅 NATS，落 store + 广播前端。NATS 不可用则降级（REST 仍可用）。"""
    url = os.getenv("NATS_URL", "")
    if not url:
        logger.warning("NATS_URL unset; collector runs without live stream")
        return
    try:
        import nats
        nc = await nats.connect(url, max_reconnect_attempts=-1)
    except Exception as e:
        logger.warning("collector NATS connect failed: %s", e)
        return
    app.state.nc = nc
    store, hub = app.state.store, app.state.hub

    async def handler(msg):
        try:
            ev = json.loads(msg.data.decode())
        except Exception:
            return
        subj = msg.subject
        if subj == "vehicle.state.changed":
            store.apply_state(ev)
            await hub.broadcast({"type": "state_change", **ev})
        elif subj == "obs.span":
            store.apply_span(ev)
            await hub.broadcast({"type": "span", **ev})
        elif subj == "obs.metric":
            store.apply_metric(ev)
            await hub.broadcast({"type": "metric", **ev})
        elif subj == "obs.agent.health":
            store.apply_health(ev)
            await hub.broadcast({"type": "health", **ev})

    for s in SUBJECTS:
        await nc.subscribe(s, cb=handler)
    logger.info("collector subscribed: %s", SUBJECTS)
```

```python
# observability/collector/main.py
"""collector 启动入口。"""
import os
import asyncio
import logging

import uvicorn

from observability.collector.server import create_app, ingest_loop

logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "info").upper(), logging.INFO))

app = create_app()


@app.on_event("startup")
async def _startup():
    asyncio.create_task(ingest_loop(app))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.getenv("OBS_COLLECTOR_PORT", "8092")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest observability/collector/tests/test_server.py -v --import-mode=importlib`
Expected: PASS（4 passed）。若报缺包：`pip install fastapi uvicorn httpx`

- [ ] **Step 5: Commit**

```bash
git add observability/collector/server.py observability/collector/main.py observability/collector/tests/test_server.py
git commit -m "feat(obs): collector FastAPI（REST/WS/NATS 订阅/debug 转发）"
```

---

### Task 5: collector 打包 + compose 注册

**Files:**
- Create: `observability/collector/requirements.txt`、`observability/collector/Dockerfile`
- Modify: `deploy/docker-compose.yaml`

- [ ] **Step 1: 写 requirements.txt**

```
# observability/collector/requirements.txt
fastapi>=0.110
uvicorn>=0.29
nats-py>=2.6.0
httpx>=0.27
```

- [ ] **Step 2: 写 Dockerfile**

```dockerfile
# observability/collector/Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY observability/collector/requirements.txt ./req.txt
RUN pip install --no-cache-dir -r req.txt
COPY observability/ ./observability/
ENV PYTHONPATH=/app
EXPOSE 8092
CMD ["python", "-m", "observability.collector.main"]
```

- [ ] **Step 3: 在 `deploy/docker-compose.yaml` 的 `# ── 接入层 ──` 之前加服务**

```yaml
  observability-collector:
    build: { context: .., dockerfile: observability/collector/Dockerfile }
    environment:
      NATS_URL: nats://nats:4222
      OBS_COLLECTOR_PORT: "8092"
      DEBUG_VEHICLE_CONTROL: ${DEBUG_VEHICLE_CONTROL:-true}
      LOG_LEVEL: ${LOG_LEVEL:-info}
    ports: ["8092:8092"]
    depends_on: [nats]
```

- [ ] **Step 4: 验证 compose 配置合法**

Run: `docker compose -f deploy/docker-compose.yaml config -q`
Expected: 无输出（配置合法）。无 docker 环境则跳过，标注待全栈验证。

- [ ] **Step 5: Commit**

```bash
git add observability/collector/requirements.txt observability/collector/Dockerfile deploy/docker-compose.yaml
git commit -m "build(obs): collector 容器化 + compose 注册（端口 8092）"
```

---

## Phase 1 — 车辆状态可视化（需求 1）

### Task 6: VAL 状态变更出口（on_change）

**Files:**
- Modify: `orchestrator/edge/val.py`（`VAL.__init__` 第 25-38 行区域；新增 `execute` 包装与 `set_env`）
- Test: `orchestrator/edge/tests/test_val_onchange.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/edge/tests/test_val_onchange.py
from val import VAL


def test_on_change_reports_diff_batch():
    captured = []
    v = VAL(on_change=lambda chs: captured.append(chs))
    v.execute({"domain": "car_control", "intent": "hvac.set",
               "data": {"object": "aircon", "operate": "set", "value": 26}})
    assert captured, "执行后应回调一次"
    keys = {c["key"] for c in captured[0]}
    assert "hvac_temp" in keys and "hvac_on" in keys
    temp = next(c for c in captured[0] if c["key"] == "hvac_temp")
    assert temp["new"] == 26


def test_no_state_change_no_callback():
    captured = []
    v = VAL(on_change=lambda chs: captured.append(chs))
    v.execute("media.next")  # 切歌不写 state
    assert captured == []


def test_set_env_triggers_callback():
    captured = []
    v = VAL(on_change=lambda chs: captured.append(chs))
    v.set_env("speed_kmh", 130)
    assert captured and captured[0][0]["key"] == "speed_kmh"
    assert captured[0][0]["new"] == 130 and v.state["speed_kmh"] == 130


def test_battery_location_defaults_present():
    v = VAL()
    assert v.state["battery"] == 72 and v.state["location"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest orchestrator/edge/tests/test_val_onchange.py -v --import-mode=importlib`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument 'on_change'`）

- [ ] **Step 3: Modify `VAL.__init__` 与 `execute`**

把 `orchestrator/edge/val.py` 的 `__init__` 签名与 state 初始化改为（新增 `on_change` 参数、`battery`/`location` 默认、保存回调）：

```python
    def __init__(self, knowledge_dir: str | None = None, vehicle_model: str | None = None,
                 on_change=None):
        self.state = {
            "hvac_on": False, "hvac_temp": 24,
            "window": "closed", "media": "stopped", "speed_kmh": 60,
            "gear": "P", "battery": 72, "location": None,
        }
        self._on_change = on_change           # on_change(changes: list[{key,old,new}])
        self.vehicle_model = vehicle_model
        self.commands: dict = {}
        self.entities: dict = {}
        self.responses: dict = {}

        if knowledge_dir is None:
            knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge")
        self._load_knowledge(knowledge_dir)
```

把现有 `def execute(self, cmd, args=None, answer_length="short")` 整体替换为"包装 + 内部分发 + diff 通知"三段：

```python
    def execute(self, cmd: Any, args: dict | None = None, answer_length: str = "short") -> tuple[bool, str]:
        """统一入口。执行前后对 state 做 diff，变更经 on_change 回调（单一出口，零遗漏）。"""
        before = dict(self.state)
        ok, speech = self._run(cmd, args, answer_length)
        self._notify(before)
        return ok, speech

    def _run(self, cmd: Any, args: dict | None, answer_length: str) -> tuple[bool, str]:
        self._answer_length = answer_length
        if isinstance(cmd, str):
            return self._legacy_execute(cmd, args or {})
        if isinstance(cmd, dict):
            return self._structured_execute(cmd)
        return False, "暂不支持该控制指令"

    def _notify(self, before: dict):
        if not self._on_change:
            return
        changes = [{"key": k, "old": before.get(k), "new": v}
                   for k, v in self.state.items() if before.get(k) != v]
        if changes:
            try:
                self._on_change(changes)
            except Exception:
                pass  # 可观测回调绝不影响主链路

    def set_env(self, key: str, value) -> None:
        """debug 专用：仅设传感器环境量（车速/电量/挡位/位置），触发变更回调。"""
        old = self.state.get(key)
        if old != value:
            self.state[key] = value
            self._notify({key: old})
```

> 注：原文件中 `execute` 的旧实现（`self._answer_length = ...` 那段 if/isinstance）逻辑已移入 `_run`，删除旧的 `execute` 方法体避免重复定义。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest orchestrator/edge/tests/test_val_onchange.py -v --import-mode=importlib`
Expected: PASS（4 passed）

- [ ] **Step 5: 回归 VAL/端侧既有测试**

Run: `python -m pytest orchestrator/edge/tests/ test/smoke_edge.py -q --import-mode=importlib`
Expected: 全绿（execute 行为不变，仅增加 diff 通知）

- [ ] **Step 6: Commit**

```bash
git add orchestrator/edge/val.py orchestrator/edge/tests/test_val_onchange.py
git commit -m "feat(obs): VAL execute diff 出口 + set_env 环境量入口"
```

---

### Task 7: edge 接线（emitter + 队列 + 后台 publish + 启动 snapshot + source 标记）

**Files:**
- Modify: `observability/events.py`（加 `change_source` contextvar）
- Modify: `orchestrator/edge/server.py`（`EdgeOrchestratorServicer.__init__` 第 82-88 行；新增 `drain_state`/`emit_snapshot`；本地路径打 source 标记）
- Modify: `orchestrator/edge/edge_call.py`（`EdgeCallExecutor.execute` 入口打 `edge_call` 标记）
- Modify: `orchestrator/edge/main.py`（建 servicer 实例、起后台 task、发 snapshot）
- Test: `orchestrator/edge/tests/test_obs_state.py`

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/edge/tests/test_obs_state.py
import asyncio
from server import EdgeOrchestratorServicer


def test_local_execute_enqueues_and_emits_state(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")  # emitter 禁用，纯测队列→emit 桥
    svc = EdgeOrchestratorServicer()

    sent = []
    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source, trace_id))
    svc.obs.emit_state = fake_emit

    # 触发本地车控（写 state）
    svc.val.execute({"domain": "car_control", "intent": "hvac.set",
                     "data": {"object": "aircon", "operate": "set", "value": 26}})
    assert not svc._state_q.empty(), "state 变更应入队"

    async def drain_once():
        changes, src, trace = await svc._state_q.get()
        await svc.obs.emit_state(changes, source=src, trace_id=trace)
    asyncio.run(asyncio.wait_for(drain_once(), timeout=1))

    assert sent and any(c["key"] == "hvac_temp" for c in sent[0][0])


def test_emit_snapshot_sends_full_state(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    svc = EdgeOrchestratorServicer()
    sent = []
    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source))
    svc.obs.emit_state = fake_emit
    asyncio.run(svc.emit_snapshot())
    assert sent and sent[0][1] == "snapshot"
    keys = {c["key"] for c in sent[0][0]}
    assert {"hvac_temp", "speed_kmh", "battery"} <= keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest orchestrator/edge/tests/test_obs_state.py -v --import-mode=importlib`
Expected: FAIL（`AttributeError: 'EdgeOrchestratorServicer' object has no attribute 'obs'`）

- [ ] **Step 3a: `observability/events.py` 顶部加 contextvar**

在 `logger = logging.getLogger("obs.events")` 之后加：

```python
import contextvars

# 当前 state 变更来源（T0 本地快路径 / edge_call 云调度 / debug 手动）。
# server/edge_call/debug handler 在驱动 VAL 前 set，on_change 桥读取。
change_source: contextvars.ContextVar[str] = contextvars.ContextVar(
    "change_source", default="vehicle")
```

- [ ] **Step 3b: `orchestrator/edge/server.py` `__init__` 接线**

把 `EdgeOrchestratorServicer.__init__`（当前第 82-88 行）替换为：

```python
    def __init__(self):
        import asyncio as _asyncio
        from observability.events import EventEmitter, change_source
        from observability.tracing import get_trace_id
        self.obs = EventEmitter("edge")
        self._state_q: _asyncio.Queue = _asyncio.Queue()
        self._change_source = change_source
        self._get_trace_id = get_trace_id

        def _on_change(changes):
            try:
                self._state_q.put_nowait(
                    (changes, self._change_source.get(), self._get_trace_id()))
            except Exception:
                pass  # 队列异常绝不影响车控主链路

        self.val = VAL(on_change=_on_change)
        self.cloud = CloudClient(edge_call_executor=EdgeCallExecutor(self.val))
        self.cloud_connected = False
        self.memory = _MemoryClient()
        self._bg: set[asyncio.Task] = set()

    async def drain_state(self):
        """后台消费 state 变更队列，best-effort emit 到 NATS。"""
        while True:
            changes, src, trace = await self._state_q.get()
            await self.obs.emit_state(changes, source=src, trace_id=trace)

    async def emit_snapshot(self):
        """启动时发一份全量车辆状态，供 collector 建立初始镜像。"""
        changes = [{"key": k, "old": None, "new": v}
                   for k, v in self.val.state.items()]
        await self.obs.emit_state(changes, source="snapshot")
```

在 `Handle` 内**本地 VAL 执行前**（快路径 A/A2/B 三处调用 `self.val.execute(...)` 之前的最近位置）打来源标记。最小改法：在 `Handle` 方法体最开头加一行：

```python
        self._change_source.set("T0")   # 本路径默认本地来源；edge_call 分支自行覆盖
```

- [ ] **Step 3c: `orchestrator/edge/edge_call.py` 打来源标记**

在 `EdgeCallExecutor.execute` 方法体开头（`intent_name = call.intent.name` 之前）加：

```python
        from observability.events import change_source
        change_source.set("edge_call")
```

- [ ] **Step 3d: `orchestrator/edge/main.py` 起后台 task + snapshot**

把 `serve()` 中的 servicer 注册段替换为：

```python
    servicer = EdgeOrchestratorServicer()
    orchestrator_pb2_grpc.add_EdgeOrchestratorServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    asyncio.create_task(servicer.drain_state())   # 后台 state 出口
    await servicer.emit_snapshot()                # 启动全量快照
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest orchestrator/edge/tests/test_obs_state.py -v --import-mode=importlib`
Expected: PASS（2 passed）

- [ ] **Step 5: 回归端侧**

Run: `python -m pytest orchestrator/edge/tests/ test/smoke_edge.py -q --import-mode=importlib`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add observability/events.py orchestrator/edge/server.py orchestrator/edge/edge_call.py orchestrator/edge/main.py orchestrator/edge/tests/test_obs_state.py
git commit -m "feat(obs): edge 车辆状态变更出口（队列→NATS）+ 启动快照"
```

---

### Task 8: dashboard 脚手架 + collector client + 车辆状态区

**Files:**
- Create: `dashboard/package.json`、`dashboard/vite.config.ts`、`dashboard/tsconfig.json`、`dashboard/index.html`、`dashboard/src/main.tsx`、`dashboard/src/types.ts`、`dashboard/src/api.ts`、`dashboard/src/App.tsx`、`dashboard/src/components/VehicleState.tsx`、`dashboard/src/styles.css`
- Test: `dashboard/src/components/VehicleState.test.tsx`

> 样式用语义 className 占位，最终交 frontend-design；本 task 只保证逻辑与数据绑定正确、可构建。

- [ ] **Step 1: 脚手架配置文件**

```json
// dashboard/package.json
{
  "name": "cockpit-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --port 5174",
    "build": "tsc -b && vite build",
    "test": "vitest run"
  },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": {
    "@testing-library/react": "^16.0.0",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.3",
    "vite": "^5.4.0",
    "vitest": "^2.0.5"
  }
}
```

```ts
// dashboard/vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', globals: true },
})
```

```json
// dashboard/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2020", "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"], "module": "ESNext",
    "skipLibCheck": true, "moduleResolution": "bundler",
    "resolveJsonModule": true, "isolatedModules": true, "noEmit": true,
    "jsx": "react-jsx", "strict": true, "types": ["vitest/globals", "node"]
  },
  "include": ["src"]
}
```

```html
<!-- dashboard/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
  <head><meta charset="UTF-8" /><title>座舱 Agent 可观测台</title></head>
  <body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body>
</html>
```

```tsx
// dashboard/src/main.tsx
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
createRoot(document.getElementById('root')!).render(<App />)
```

```css
/* dashboard/src/styles.css — 占位，最终视觉交 frontend-design */
:root { color-scheme: dark; }
body { margin: 0; font-family: system-ui, "Microsoft YaHei", sans-serif; background: #070b16; color: #e8edf8; }
.vcard.changed { outline: 1px solid #2dd4bf; }
```

- [ ] **Step 2: 类型 + collector client**

```ts
// dashboard/src/types.ts
export type Span = {
  trace_id: string; span_id: string; parent_id?: string; ts: number
  service: string; node: string; status: string; duration_ms: number
  attrs: Record<string, any>
}
export type Trace = { trace_id: string; spans: Span[]; started?: number; updated?: number }
export type VehicleState = Record<string, any>
export type AgentInfo = {
  healthy?: boolean; fail_count?: number; last_seen?: number
  count?: number; avg_ms?: number; error_rate?: number
  deployment?: string; kind?: string
}
```

```ts
// dashboard/src/api.ts
import type { Span, Trace, VehicleState, AgentInfo } from './types'

const BASE = (import.meta.env.VITE_COLLECTOR_URL as string) || 'http://localhost:8092'
const WS_URL = BASE.replace(/^http/, 'ws') + '/stream'

export type ObsHandlers = {
  onSnapshot?: (s: { vehicle_state: VehicleState; agents: Record<string, AgentInfo>; traces: Trace[] }) => void
  onStateChange?: (ev: { changes: { key: string; old: any; new: any }[]; source: string; trace_id?: string }) => void
  onSpan?: (ev: Span) => void
  onMetric?: (ev: any) => void
  onHealth?: (ev: any) => void
  onConn?: (connected: boolean) => void
}

export function connectObs(h: ObsHandlers): () => void {
  let ws: WebSocket | null = null, closed = false, retry: any
  const open = () => {
    ws = new WebSocket(WS_URL)
    ws.onopen = () => h.onConn?.(true)
    ws.onclose = () => { h.onConn?.(false); if (!closed) retry = setTimeout(open, 1500) }
    ws.onerror = () => ws?.close()
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data)
      if (m.type === 'snapshot') h.onSnapshot?.(m)
      else if (m.type === 'state_change') h.onStateChange?.(m)
      else if (m.type === 'span') h.onSpan?.(m)
      else if (m.type === 'metric') h.onMetric?.(m)
      else if (m.type === 'health') h.onHealth?.(m)
    }
  }
  open()
  return () => { closed = true; if (retry) clearTimeout(retry); ws?.close() }
}

export async function setVehicleEnv(key: string, value: any): Promise<void> {
  await fetch(BASE + '/api/debug/vehicle', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ key, value }),
  })
}
```

- [ ] **Step 3: 车辆状态组件 + App 骨架**

```tsx
// dashboard/src/components/VehicleState.tsx
import type { VehicleState as VS } from '../types'

// state key → 展示标签（缺省回退 key 本身）
const LABELS: Record<string, string> = {
  hvac_on: '空调', hvac_temp: '空调温度', window: '车窗', media: '媒体',
  ambient_light: '氛围灯', ambient_light_color: '氛围灯颜色', sunroof: '天窗',
  door_lock: '车门锁', volume: '音量', seat_heating: '座椅加热',
}

function fmt(v: any): string {
  if (v === true) return '开'; if (v === false) return '关'
  if (v === null || v === undefined) return '—'
  return String(v)
}

export function VehicleState({ state, changed }: { state: VS; changed: Set<string> }) {
  const keys = Object.keys(state).filter((k) => !['speed_kmh', 'battery', 'gear', 'location'].includes(k))
  return (
    <section className="panel vehicle-state">
      <h2>车辆状态</h2>
      <div className="vgrid">
        {keys.map((k) => (
          <div key={k} className={'vcard' + (changed.has(k) ? ' changed' : '')} data-key={k}>
            <div className="nm">{LABELS[k] || k}</div>
            <div className="vv">{fmt(state[k])}</div>
            {changed.has(k) && <div className="chg">刚变</div>}
          </div>
        ))}
      </div>
    </section>
  )
}
```

```tsx
// dashboard/src/App.tsx
import { useEffect, useRef, useState } from 'react'
import { connectObs } from './api'
import { VehicleState } from './components/VehicleState'
import type { VehicleState as VS } from './types'

export default function App() {
  const [connected, setConnected] = useState(false)
  const [vehicle, setVehicle] = useState<VS>({})
  const [changed, setChanged] = useState<Set<string>>(new Set())
  const timers = useRef<Record<string, any>>({})

  const flash = (keys: string[]) => {
    setChanged((prev) => { const n = new Set(prev); keys.forEach((k) => n.add(k)); return n })
    keys.forEach((k) => {
      clearTimeout(timers.current[k])
      timers.current[k] = setTimeout(() => {
        setChanged((prev) => { const n = new Set(prev); n.delete(k); return n })
      }, 2500)
    })
  }

  useEffect(() => {
    return connectObs({
      onConn: setConnected,
      onSnapshot: (s) => setVehicle(s.vehicle_state || {}),
      onStateChange: (ev) => {
        setVehicle((prev) => {
          const next = { ...prev }
          ev.changes.forEach((c) => { next[c.key] = c.new })
          return next
        })
        flash(ev.changes.map((c) => c.key))
      },
    })
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <h1>座舱 Agent 可观测台</h1>
        <span className={'badge' + (connected ? '' : ' off')}>collector {connected ? '已连' : '断开'}</span>
      </header>
      <main className="grid">
        <VehicleState state={vehicle} changed={changed} />
      </main>
    </div>
  )
}
```

- [ ] **Step 4: Write the failing test**

```tsx
// dashboard/src/components/VehicleState.test.tsx
import { render, screen } from '@testing-library/react'
import { VehicleState } from './VehicleState'

test('renders state values and highlights changed keys', () => {
  render(<VehicleState state={{ hvac_temp: 26, window: 'closed' }} changed={new Set(['hvac_temp'])} />)
  expect(screen.getByText('26')).toBeTruthy()
  const card = document.querySelector('[data-key="hvac_temp"]')!
  expect(card.className).toContain('changed')
})
```

- [ ] **Step 5: Install + run test + build**

Run:
```bash
cd dashboard && npm install && npm test && npm run build
```
Expected: vitest 1 passed；`vite build` 成功产出 `dist/`

- [ ] **Step 6: Commit**

```bash
git add dashboard/
git commit -m "feat(dashboard): 脚手架 + collector client + 车辆状态区"
```

---

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

