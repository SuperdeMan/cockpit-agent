# Central Hub P0 Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add P0 regression coverage for central hub dispatch, execution, confirmation, multi-turn context, and observability assertions.

**Architecture:** Keep fast deterministic coverage in pytest unit tests, and add one full-stack assertion runner that reuses the existing edge gateway WebSocket plus observability collector REST APIs. The E2E runner uses a JSON fixture so new cases can be added without changing runner code.

**Tech Stack:** Python 3.11, pytest, grpc/protobuf generated Python modules, FastAPI TestClient patterns already in repo, standard-library `json`/`urllib`, and existing `websockets` dependency for full-stack scripts.

---

## File Structure

- Modify `orchestrator/cloud/tests/test_obs_spans.py`
  - Adds explicit span status regression tests for `NEED_CONFIRM` and `NEED_SLOT`.
- Create `orchestrator/cloud/tests/test_engine_multiturn_context.py`
  - Adds process-local multi-turn tests for `NEED_SLOT` resume and session-scoped history.
- Create `test/fixtures/central_hub_cases.json`
  - Declares full-stack assertion cases for P0 central hub flows.
- Create `test/e2e_central_hub_assertions.py`
  - Runs fixture cases through `ws://localhost:8090/ws` and asserts collector trace/state output from `http://localhost:8092`.
- No production code changes are expected for P0. If a new test fails, fix the implementation in the smallest owner file revealed by the failure.

---

### Task 1: Add Dispatcher Wait Span Regression Tests

**Files:**
- Modify: `orchestrator/cloud/tests/test_obs_spans.py`
- Test: `orchestrator/cloud/tests/test_obs_spans.py`

- [ ] **Step 1: Append failing tests for pending dispatcher spans**

Add these tests to the end of `orchestrator/cloud/tests/test_obs_spans.py`:

```python
def test_dispatch_need_confirm_emits_wait_span(monkeypatch):
    from observability import events

    spans = []

    class FakeEmitter:
        async def emit_span(self, trace_id, node, **kwargs):
            spans.append((trace_id, node, kwargs))

        async def emit_metric(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        events,
        "get_emitter",
        lambda service="cloud": FakeEmitter(),
        raising=False,
    )

    async def fake_cloud(endpoint, intent, slots, context, meta):
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.NEED_CONFIRM,
            speech="请确认是否继续。",
        )

    dispatcher = UnifiedDispatcher(cloud_call=fake_cloud, edge_call=None)
    step = Step(
        id="s1",
        agent_id="food-ordering",
        intent="food.reserve",
        endpoint="food:50063",
        kind="agent",
        deployment="cloud",
    )
    context = PlanContext(
        request_id="request-1",
        session_id="session-1",
        trace_id="trace-confirm-1",
        granted_permissions=["food.ordering"],
    )

    asyncio.run(dispatcher.dispatch(step, context))

    wait_spans = [
        kwargs for _, node, kwargs in spans
        if node == "step.agent:food-ordering"
    ]
    assert wait_spans
    assert wait_spans[-1]["status"] == "wait"


def test_dispatch_need_slot_emits_wait_span(monkeypatch):
    from observability import events

    spans = []

    class FakeEmitter:
        async def emit_span(self, trace_id, node, **kwargs):
            spans.append((trace_id, node, kwargs))

        async def emit_metric(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        events,
        "get_emitter",
        lambda service="cloud": FakeEmitter(),
        raising=False,
    )

    async def fake_cloud(endpoint, intent, slots, context, meta):
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.NEED_SLOT,
            speech="您想什么时候出发？",
            follow_up="请补充时间。",
        )

    dispatcher = UnifiedDispatcher(cloud_call=fake_cloud, edge_call=None)
    step = Step(
        id="s1",
        agent_id="navigation",
        intent="navigation.route",
        endpoint="navigation:50061",
        kind="agent",
        deployment="cloud",
    )
    context = PlanContext(
        request_id="request-1",
        session_id="session-1",
        trace_id="trace-slot-1",
        granted_permissions=["navigation"],
    )

    asyncio.run(dispatcher.dispatch(step, context))

    wait_spans = [
        kwargs for _, node, kwargs in spans
        if node == "step.agent:navigation"
    ]
    assert wait_spans
    assert wait_spans[-1]["status"] == "wait"
```

- [ ] **Step 2: Run the focused tests**

Run:

```bash
python -m pytest --import-mode=importlib orchestrator/cloud/tests/test_obs_spans.py -q
```

Expected: both new tests pass on current `main`; they fail on a pre-`c4b4097a` implementation where pending statuses were emitted as `err`.

- [ ] **Step 3: Fix only if the tests fail**

If either test fails because `status` is `err`, update `orchestrator/cloud/dispatch.py` so `_finish()` treats `NEED_CONFIRM` and `NEED_SLOT` as pending:

```python
st = response.status
pending = st in (
    agent_pb2.ExecuteResponse.NEED_CONFIRM,
    agent_pb2.ExecuteResponse.NEED_SLOT,
)
await self._emit_step(
    step, ctx,
    st == agent_pb2.ExecuteResponse.OK,
    elapsed_ms,
    pending=pending,
)
```

- [ ] **Step 4: Re-run the focused tests**

Run:

```bash
python -m pytest --import-mode=importlib orchestrator/cloud/tests/test_obs_spans.py -q
```

Expected: all tests in `test_obs_spans.py` pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add orchestrator/cloud/tests/test_obs_spans.py orchestrator/cloud/dispatch.py
git commit -m "test: cover pending dispatcher span status"
```

---

### Task 2: Add Multi-Turn Context Contract Tests

**Files:**
- Create: `orchestrator/cloud/tests/test_engine_multiturn_context.py`
- Test: `orchestrator/cloud/tests/test_engine_multiturn_context.py`

- [ ] **Step 1: Create the failing multi-turn test file**

Create `orchestrator/cloud/tests/test_engine_multiturn_context.py` with this content:

```python
"""PlannerEngine multi-turn context regressions for P0 central hub coverage."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore
from security.permission import PermissionEngine


_NEED_SLOT_PLAN = json.dumps({
    "steps": [
        {
            "id": "s1",
            "agent_id": "food-ordering",
            "intent": "food.reserve",
            "slots": {"restaurant_name": "川菜路名店"},
            "depends_on": [],
        },
    ],
})

_SIMPLE_NAV_PLAN = json.dumps({
    "steps": [
        {
            "id": "s1",
            "agent_id": "navigation",
            "intent": "navigation.route",
            "slots": {"destination": "首都机场"},
            "depends_on": [],
        },
    ],
})


class _Cap:
    def __init__(self, intent, slots=None):
        self.intent = intent
        self.slots = slots or []
        self.description = intent


def _agent(agent_id, intent, slots=None):
    manifest = SimpleNamespace(
        agent_id=agent_id,
        trust_level="first_party",
        latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[_Cap(intent, slots or [])],
    )
    return SimpleNamespace(manifest=manifest, endpoint=f"stub:{agent_id}")


class _Resp:
    def __init__(
        self,
        status=0,
        speech="ok",
        follow_up="",
        missing_slots=None,
    ):
        self.status = status
        self.speech = speech
        self.follow_up = follow_up
        self.actions = []
        self.ui_card = None
        self.data = None
        self.missing_slots = missing_slots or []


class _MultiTurnSpy:
    def __init__(self, plan_json=_NEED_SLOT_PLAN, histories=None):
        self.plan_json = plan_json
        self.histories = histories or {}
        self.calls: list[tuple[str, dict, dict]] = []
        self.appended: list[tuple[str, str, str]] = []
        self.session_reads: list[str] = []
        self.planner_prompts: list[str] = []
        self.llm_plan_calls = 0

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        raise RuntimeError("stream unavailable in test")
        yield  # pragma: no cover

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.calls.append((intent, dict(slots or {}), dict(meta or {})))
        if intent == "food.reserve" and "datetime" not in (slots or {}):
            return _Resp(
                status=2,
                speech="请问您想订什么时候？",
                follow_up="请补充时间。",
                missing_slots=["datetime"],
            )
        if intent == "food.reserve":
            return _Resp(speech=f"已为您预订{slots['datetime']}。")
        if intent == "navigation.route":
            return _Resp(speech="已为您规划路线。")
        return _Resp(status=3, speech="未知意图")

    async def llm(self, messages):
        if "任务编排器" in messages[0]["content"]:
            self.llm_plan_calls += 1
            self.planner_prompts.append(messages[1]["content"])
            return self.plan_json
        return "聚合完成"

    async def resolve(self, query="", intent="", top_k=1):
        return [
            _agent("food-ordering", "food.reserve", ["restaurant_name", "datetime"]),
            _agent("navigation", "navigation.route", ["destination"]),
        ]

    async def list_agents(self):
        return await self.resolve()

    async def append_turn(self, session_id, role, text):
        self.appended.append((session_id, role, text))

    async def get_session(self, session_id, last_n=6):
        self.session_reads.append(session_id)
        return self.histories.get(session_id, [])


def _make_engine(spy):
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
        perms=PermissionEngine(),
    )
    return engine, session


def _req(text, session_id="sess-mt", is_confirmation=False, meta=None):
    return SimpleNamespace(
        text=text,
        session_id=session_id,
        request_id="r1",
        is_confirmation=is_confirmation,
        meta=meta or {},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req):
    async def collect():
        return [event async for event in engine.run(req)]

    return asyncio.run(collect())


def test_need_slot_resume_reuses_pending_plan_and_fills_slot():
    spy = _MultiTurnSpy()
    engine, session = _make_engine(spy)

    first_events = _run(engine, _req("订个川菜馆"))
    first_final = first_events[-1]

    assert first_final["kind"] == "final"
    assert first_final["need_confirm"] is False
    assert "请" in first_final["speech"]
    state = asyncio.run(session.load("sess-mt"))
    assert state is not None
    assert state.phase == "wait_slot"
    assert state.pending_step_id == "s1"
    assert state.missing_slots == ["datetime"]
    assert spy.llm_plan_calls == 1

    second_events = _run(engine, _req("今晚七点"))
    second_final = second_events[-1]

    assert second_final["kind"] == "final"
    assert second_final["speech"] == "聚合完成"
    assert spy.llm_plan_calls == 1
    assert spy.calls[-1][0] == "food.reserve"
    assert spy.calls[-1][1]["datetime"] == "今晚七点"
    assert asyncio.run(session.load("sess-mt")) is None


def test_session_scoped_history_does_not_leak_between_sessions():
    spy = _MultiTurnSpy(
        plan_json=_SIMPLE_NAV_PLAN,
        histories={
            "sess-a": [
                {"role": "user", "text": "导航去首都机场"},
                {"role": "assistant", "text": "已为您规划去首都机场的路线。"},
            ],
            "sess-b": [],
        },
    )
    engine, _ = _make_engine(spy)

    _run(engine, _req("换成最快路线", session_id="sess-a"))
    prompt_a = spy.planner_prompts[-1]
    _run(engine, _req("换成最快路线", session_id="sess-b"))
    prompt_b = spy.planner_prompts[-1]

    assert spy.session_reads == ["sess-a", "sess-b"]
    assert "首都机场" in prompt_a
    assert "首都机场" not in prompt_b
```

- [ ] **Step 2: Run the new test file**

Run:

```bash
python -m pytest --import-mode=importlib orchestrator/cloud/tests/test_engine_multiturn_context.py -q
```

Expected: both tests pass on current `main`. A failure in `test_need_slot_resume_reuses_pending_plan_and_fills_slot` points to `orchestrator/cloud/engine.py` restore/fill-slot behavior.

- [ ] **Step 3: Fix only if the tests fail**

If slot resume fails because the second turn triggers a fresh plan, inspect `PlannerEngine._orchestrate()` and keep the wait-slot branch before new planning:

```python
if pending and pending.phase == "wait_slot":
    plan, seed_results = self._restore(pending)
    if plan is None:
        await self.session.clear(ctx.session_id)
        yield {"kind": "final",
               "speech": "刚才的操作已过期，麻烦您再说一遍需求。"}
        return
    for step in plan.steps:
        if step.id == pending.pending_step_id:
            for slot_name in (pending.missing_slots or []):
                step.slots[slot_name] = text
            break
```

If session history leaks between sessions, inspect `_history(ctx.session_id)` call sites and keep history lookup scoped to the current `ctx.session_id`.

- [ ] **Step 4: Run related existing multi-turn tests**

Run:

```bash
python -m pytest --import-mode=importlib orchestrator/cloud/tests/test_engine_confirm.py orchestrator/cloud/tests/test_engine_context.py orchestrator/cloud/tests/test_engine_multiturn_context.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add orchestrator/cloud/tests/test_engine_multiturn_context.py orchestrator/cloud/engine.py
git commit -m "test: cover central hub multi-turn context"
```

---

### Task 3: Add Full-Stack Central Hub Fixture

**Files:**
- Create: `test/fixtures/central_hub_cases.json`
- Test: consumed by `test/e2e_central_hub_assertions.py` in Task 4

- [ ] **Step 1: Create the fixture directory if needed**

Run:

```bash
New-Item -ItemType Directory -Force test/fixtures
```

Expected: PowerShell reports the directory, or does nothing if it already exists.

- [ ] **Step 2: Create the P0 fixture JSON**

Create `test/fixtures/central_hub_cases.json` with this content:

```json
[
  {
    "name": "t0_hvac_local",
    "setup": {"speed_kmh": 0},
    "turns": [
      {
        "text": "打开空调26度",
        "expect_spans": ["route.local", "val.execute"],
        "forbid_spans": ["cloud.planning"],
        "expect_state": {"hvac_on": true, "hvac_temp": 26},
        "expect_need_confirm": false
      }
    ]
  },
  {
    "name": "safety_window_speed_gate",
    "setup": {"speed_kmh": 130},
    "turns": [
      {
        "text": "打开车窗",
        "expect_spans": ["route.local", "val.execute"],
        "expect_span_status": {"val.execute": "err"},
        "expect_state_unchanged": ["window"],
        "expect_need_confirm": false,
        "expect_speech_contains": ["高速", "安全"]
      }
    ]
  },
  {
    "name": "cloud_navigation_single_agent",
    "setup": {"speed_kmh": 0},
    "turns": [
      {
        "text": "导航去北京南站",
        "expect_spans": ["route.cloud", "cloud.planning", "step.agent:navigation"],
        "expect_need_confirm": false
      }
    ]
  },
  {
    "name": "mixed_local_and_cloud",
    "setup": {"speed_kmh": 0},
    "turns": [
      {
        "text": "打开主驾座椅加热，然后导航去首都机场",
        "expect_spans": ["route.mixed", "val.execute", "cloud.planning"],
        "expect_state": {"seat_heating": true},
        "expect_need_confirm": false
      }
    ]
  },
  {
    "name": "dangerous_trunk_confirm",
    "setup": {"speed_kmh": 0},
    "turns": [
      {
        "text": "打开后备箱",
        "expect_spans": ["route.cloud", "cloud.planning"],
        "expect_need_confirm": true,
        "expect_state_unchanged": ["trunk"]
      },
      {
        "text": "确认",
        "is_confirmation": true,
        "expect_need_confirm": false,
        "expect_state": {"trunk": "open"}
      }
    ]
  }
]
```

- [ ] **Step 3: Validate JSON syntax**

Run:

```bash
python -m json.tool test/fixtures/central_hub_cases.json
```

Expected: formatted JSON prints to stdout and the command exits with code 0.

- [ ] **Step 4: Commit Task 3**

```bash
git add test/fixtures/central_hub_cases.json
git commit -m "test: add central hub e2e fixtures"
```

---

### Task 4: Add Full-Stack Assertion Runner

**Files:**
- Create: `test/e2e_central_hub_assertions.py`
- Test: `test/e2e_central_hub_assertions.py`

- [ ] **Step 1: Create the assertion runner**

Create `test/e2e_central_hub_assertions.py` with this content:

```python
"""Assertion-based central hub E2E tests.

Prerequisite: run `make up` before this script.
Dependency: pip install websockets
Usage: python test/e2e_central_hub_assertions.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Please install dependency first: pip install websockets")
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EDGE_WS = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "central_hub_cases.json"


def _get(path: str):
    with urllib.request.urlopen(COLLECTOR + path, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_debug(key: str, value):
    data = json.dumps({"key": key, "value": value}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        COLLECTOR + "/api/debug/vehicle",
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


async def _send(text: str, session_id: str, trace_id: str, *, is_confirmation=False):
    payload = {
        "text": text,
        "session_id": session_id,
        "is_confirmation": is_confirmation,
        "meta": {"trace_id": trace_id},
    }
    async with websockets.connect(EDGE_WS, max_size=None) as ws:
        await ws.send(json.dumps(payload, ensure_ascii=False))
        finals = []
        started = time.time()
        got_final = False
        while time.time() - started < 120:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                if got_final:
                    break
                continue
            message = json.loads(raw)
            if message.get("type") in ("final", "error"):
                finals.append(message)
                got_final = True
        return finals


def _load_cases(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _nodes(spans):
    return [span.get("node", "") for span in spans]


def _span_status(spans, node):
    for span in reversed(spans):
        if span.get("node") == node:
            return span.get("status")
    return None


def _speech(finals):
    return " ".join(
        str(final.get("speech") or final.get("message") or "")
        for final in finals
    )


def _state_diff(before, after):
    keys = set(before) | set(after)
    return {
        key: (before.get(key), after.get(key))
        for key in sorted(keys)
        if before.get(key) != after.get(key)
    }


def _wait_trace(trace_id: str, required_nodes: list[str], timeout_s=12):
    deadline = time.time() + timeout_s
    last_spans = []
    while time.time() < deadline:
        try:
            trace = _get(f"/api/traces/{trace_id}")
            spans = sorted(trace.get("spans", []), key=lambda item: item.get("ts", 0))
            last_spans = spans
            nodes = _nodes(spans)
            if all(node in nodes for node in required_nodes):
                return spans
            if spans and not required_nodes:
                return spans
        except Exception:
            pass
        time.sleep(0.5)
    return last_spans


def _assert_turn(case_name, turn, before, after, spans, finals):
    nodes = _nodes(spans)
    diff = _state_diff(before, after)
    speech = _speech(finals)

    for node in turn.get("expect_spans", []):
        assert node in nodes, (
            f"{case_name}: expected span {node!r}, got nodes={nodes!r}"
        )

    for node in turn.get("forbid_spans", []):
        assert node not in nodes, (
            f"{case_name}: forbidden span {node!r} appeared in nodes={nodes!r}"
        )

    for node, expected_status in turn.get("expect_span_status", {}).items():
        actual_status = _span_status(spans, node)
        assert actual_status == expected_status, (
            f"{case_name}: span {node!r} status {actual_status!r}, "
            f"expected {expected_status!r}"
        )

    for key, expected in turn.get("expect_state", {}).items():
        assert after.get(key) == expected, (
            f"{case_name}: state {key!r}={after.get(key)!r}, expected {expected!r}; "
            f"diff={diff!r}"
        )

    for key in turn.get("expect_state_unchanged", []):
        assert before.get(key) == after.get(key), (
            f"{case_name}: state {key!r} changed from {before.get(key)!r} "
            f"to {after.get(key)!r}"
        )

    if "expect_need_confirm" in turn:
        actual = any(final.get("need_confirm") for final in finals)
        assert actual is bool(turn["expect_need_confirm"]), (
            f"{case_name}: need_confirm={actual!r}, "
            f"expected {turn['expect_need_confirm']!r}; finals={finals!r}"
        )

    for part in turn.get("expect_speech_contains", []):
        assert part in speech, (
            f"{case_name}: expected speech to contain {part!r}, got {speech!r}"
        )


async def _run_case(case):
    name = case["name"]
    session_id = f"central-{name}-{uuid.uuid4().hex[:6]}"
    print(f"\n== {name} ==")

    for key, value in case.get("setup", {}).items():
        result = _post_debug(key, value)
        assert result.get("ok") is True, f"{name}: debug setup failed: {result!r}"
    if case.get("setup"):
        time.sleep(1.0)

    for index, turn in enumerate(case["turns"], start=1):
        trace_id = _trace_id()
        before = _get("/api/vehicle/state")
        finals = await _send(
            turn["text"],
            session_id,
            trace_id,
            is_confirmation=turn.get("is_confirmation", False),
        )
        required_nodes = turn.get("expect_spans", [])
        spans = _wait_trace(trace_id, required_nodes)
        after = _get("/api/vehicle/state")

        _assert_turn(name, turn, before, after, spans, finals)
        print(
            f"  turn {index}: ok "
            f"nodes={_nodes(spans)} diff={_state_diff(before, after)}"
        )


async def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()

    try:
        health = _get("/healthz")
    except urllib.error.URLError as exc:
        raise SystemExit(f"collector unavailable; run make up first: {exc}") from exc

    print(f"collector healthz: {health}")
    cases = _load_cases(Path(args.fixture))
    selected = set(args.case)
    if selected:
        cases = [case for case in cases if case["name"] in selected]
    assert cases, "no cases selected"

    for case in cases:
        await _run_case(case)

    print(f"\ncentral hub assertions passed: {len(cases)} case(s)")


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 2: Run static syntax validation**

Run:

```bash
python -m py_compile test/e2e_central_hub_assertions.py
```

Expected: command exits with code 0 and prints no output.

- [ ] **Step 3: Run fixture validation**

Run:

```bash
python -m json.tool test/fixtures/central_hub_cases.json
```

Expected: command exits with code 0.

- [ ] **Step 4: Run one full-stack case**

Prerequisite: `make up` is already running and `http://localhost:8092/healthz` returns `ok`.

Run:

```bash
python test/e2e_central_hub_assertions.py --case t0_hvac_local
```

Expected: script prints `central hub assertions passed: 1 case(s)`.

- [ ] **Step 5: Run all full-stack P0 cases**

Run:

```bash
python test/e2e_central_hub_assertions.py
```

Expected: script prints `central hub assertions passed: 5 case(s)`.

- [ ] **Step 6: Fix only if runner exposes a product regression**

Use these ownership rules:

- Missing `route.*` or cloud/local split bug: inspect `orchestrator/edge/server.py` and `orchestrator/edge/fast_intent.py`.
- Missing `cloud.planning`, `step.*`, or `aggregate`: inspect `orchestrator/cloud/engine.py`, `orchestrator/cloud/dispatch.py`, or `orchestrator/cloud/loop.py`.
- State expectation mismatch: inspect `orchestrator/edge/val.py` and `orchestrator/edge/edge_call.py`.
- Collector trace missing while business result is correct: inspect `observability/events.py`, `observability/collector/store.py`, and `observability/collector/server.py`.

- [ ] **Step 7: Commit Task 4**

```bash
git add test/e2e_central_hub_assertions.py
git commit -m "test: add central hub e2e assertion runner"
```

---

### Task 5: Run P0 Verification Suite

**Files:**
- Verify: `orchestrator/cloud/tests/test_obs_spans.py`
- Verify: `orchestrator/cloud/tests/test_engine_confirm.py`
- Verify: `orchestrator/cloud/tests/test_engine_context.py`
- Verify: `orchestrator/cloud/tests/test_engine_multiturn_context.py`
- Verify: `orchestrator/edge/tests/test_server_dispatch.py`
- Verify: `test/e2e_central_hub_assertions.py`

- [ ] **Step 1: Run fast P0 unit tests**

Run:

```bash
python -m pytest --import-mode=importlib orchestrator/cloud/tests/test_obs_spans.py orchestrator/cloud/tests/test_engine_confirm.py orchestrator/cloud/tests/test_engine_context.py orchestrator/cloud/tests/test_engine_multiturn_context.py orchestrator/edge/tests/test_server_dispatch.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run syntax checks for new full-stack files**

Run:

```bash
python -m py_compile test/e2e_central_hub_assertions.py
python -m json.tool test/fixtures/central_hub_cases.json
```

Expected: both commands exit with code 0.

- [ ] **Step 3: Run all Python tests when time allows**

Run:

```bash
python -m pytest --import-mode=importlib -q
```

Expected: full suite remains green, matching current baseline unless unrelated environment issues appear.

- [ ] **Step 4: Run full-stack assertion cases**

Prerequisite: `make up` has started the full stack.

Run:

```bash
python test/e2e_central_hub_assertions.py
```

Expected: all fixture cases pass and the script prints `central hub assertions passed: 5 case(s)`.

- [ ] **Step 5: Commit verification fixes if any were needed**

If Task 5 required changes after Tasks 1-4 commits, commit those changes:

```bash
git add orchestrator/cloud orchestrator/edge observability test
git commit -m "test: stabilize central hub p0 coverage"
```

If no additional changes were needed, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:

- P0-1 through P0-5 are covered by `test/fixtures/central_hub_cases.json` and `test/e2e_central_hub_assertions.py`.
- P0-6 is already covered by `orchestrator/edge/tests/test_server_dispatch.py`; Task 5 keeps it in the verification set.
- P0-7 is covered by Task 1.
- P0-8 is already covered by `orchestrator/cloud/tests/test_dispatch.py`; it remains part of the broader existing cloud test suite.
- P0-9 is already covered by `orchestrator/cloud/tests/test_engine_confirm.py`; Task 5 keeps it in the verification set.
- P0-10 is covered by Task 2.
- P0-11 is already covered by `test_unrelated_reply_treated_as_new_request` in `orchestrator/cloud/tests/test_engine_confirm.py`; Task 5 keeps it in the verification set.
- Multi-turn session scoping requested after spec approval is covered by Task 2.

Out of scope for this plan:

- P1 fault injection for collector restart, registry restart, NATS outage, edge transport outage, and stream fallback.
- P2 large utterance corpus expansion.
- Dashboard visual verification.

