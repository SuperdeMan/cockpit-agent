# Observability Dashboard Implementation Plan - P0/P1

> 历史 TDD 实施明细：基础设施、collector、VAL 观测和 Dashboard 骨架。
> 当前状态与分卷导航见 [`2026-06-15-observability-dashboard.md`](2026-06-15-observability-dashboard.md)。
> 文内预期测试数保留实施时原文；当前验证基线为 360 passed, 2 skipped。

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

