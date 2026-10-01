"""契约测试夹具：不起 gRPC server，直接驱动 Agent.handle 做黄金用例断言。

用法（在 agents/<name>/tests/ 中）::

    import pytest
    from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
    from agents.navigation.src.agent import NavigationAgent

    @pytest.mark.asyncio
    async def test_search_poi():
        agent = NavigationAgent()
        res = await run_handle(agent, "navigation.search_poi",
                               slots={"keyword": "充电站"}, raw_text="附近的充电站")
        assert res.status == "ok"
        assert any(a["type"] == "navigate" for a in res.actions) or res.speech

    def test_manifest():
        assert assert_manifest_consistent(NavigationAgent()) is True
"""
from __future__ import annotations
import copy
from unittest.mock import AsyncMock
from typing import AsyncIterator

from runtime import operation as op

from .base import Context, IntentView
from .result import AgentResult


def make_context(session_id: str = "test-sess", user_id: str = "u1",
                 vehicle_id: str = "v1", context_values: dict | None = None,
                 history: list | None = None,
                 granted_permissions=("profile.read", "location.read", "vehicle.read.state", "camera.frame")) -> Context:
    mem = AsyncMock()
    mem.get_context.return_value = context_values or {}
    mem.get_session.return_value = history or []
    mem.recall.return_value = []  # 默认无语义记忆；用 ctx.recall 的 Agent 测试可覆盖

    async def _recall_read(user_id, query="", **kw):
        """三态版跟着 `recall` 走（批 5 W17）：测试只需改 `mem.recall.return_value` /
        `side_effect`；抛异常 ⇒ `unavailable`，与生产 `MemoryClient.recall_read` 同契约。"""
        from runtime import memory_read
        try:
            items = list(await mem.recall(user_id, query, **kw) or [])
        except Exception:
            return [], memory_read.UNAVAILABLE
        return items, memory_read.read_state(items)

    mem.recall_read.side_effect = _recall_read
    # Positive business fixtures carry explicit read grants. Security tests pass
    # an empty set or construct the real Context/servicer directly.
    ctx = Context(session_id, user_id, vehicle_id, mem,
                  meta={"granted_scopes": ",".join(granted_permissions)})
    import json, time
    aliases = {"vehicle.battery": "battery", "vehicle.speed": "speed_kmh", "vehicle.gear": "gear"}
    values = {aliases[k]: v for k, v in (context_values or {}).items() if k in aliases}
    if values:
        ctx._projection_meta = {"vehicle_observation": json.dumps({
            "version": 2, "vehicle_id": vehicle_id, "state": values,
            "signals": {k: {"quality": "good", "freshness": "bounded",
                            "expires_at_ms": int(time.time() * 1000) + 60000,
                            "source_kind": "simulated"} for k in values}})}
    return ctx


async def run_handle(agent, intent_name: str, slots: dict | None = None,
                     raw_text: str = "", confidence: float = 0.9,
                     ctx: Context | None = None, meta: dict | None = None) -> AgentResult:
    iv = IntentView(intent_name, slots or {}, raw_text, confidence)
    return await agent.handle(iv, ctx or make_context(), meta or {})


async def run_handle_stream(agent, intent_name: str, slots: dict | None = None,
                            raw_text: str = "", confidence: float = 0.9,
                            ctx: Context | None = None,
                            meta: dict | None = None) -> list[tuple[str, object]]:
    """运行 handle_stream 并收集所有事件。返回 [(kind, payload), ...]。"""
    iv = IntentView(intent_name, slots or {}, raw_text, confidence)
    events = []
    async for kind, payload in agent.handle_stream(iv, ctx or make_context(), meta or {}):
        events.append((kind, payload))
    return events


def assert_manifest_consistent(agent) -> bool:
    """校验 Agent manifest 一致性：agent_id 存在、有 capabilities、category 合法。"""
    m = agent.manifest
    assert m.agent_id, "manifest.agent_id is empty"
    assert m.version, f"{m.agent_id}: manifest.version is empty"
    assert m.category in ("core", "ecosystem"), f"{m.agent_id}: invalid category {m.category}"
    assert m.trust_level in ("system", "first_party", "third_party"), \
        f"{m.agent_id}: invalid trust_level {m.trust_level}"
    assert m.deployment in ("edge", "cloud"), f"{m.agent_id}: invalid deployment {m.deployment}"
    assert len(m.capabilities) > 0, f"{m.agent_id}: no capabilities declared"
    for cap in m.capabilities:
        assert cap.intent, f"{m.agent_id}: capability has empty intent"
        assert "." in cap.intent, f"{m.agent_id}: intent '{cap.intent}' not in domain.action format"
    return True


def assert_result_valid(res: AgentResult, expected_status: str = None):
    """校验 AgentResult 结构合法性。"""
    assert res.speech, "speech is empty"
    if expected_status:
        assert res.status == expected_status, f"status={res.status}, expected={expected_status}"
    for a in res.actions:
        assert "type" in a, f"action missing 'type': {a}"


# ── CA2-08 操作准入的内存孪生（各 Agent 测试共用）──────────────────────────
# 逐条镜像 `ledger.TaskLedger.operation_*` 的 SQL 条件；`test/probe_operation_admission_sql.py`
# 用真 PostgreSQL 跑同一组场景，是它不走样的证据。只有 kind=operation 的方法，没有任务 API。

def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


class MemoryOperationLedger:
    """In-memory twin of the SQL; every write re-checks the same conditions."""

    def __init__(self, ready=True):
        self.ready = ready
        self.rows: dict[str, dict] = {}
        self.fail_settle = False

    async def operations_ready(self):
        return self.ready

    def _check(self):
        if not self.ready:
            raise _store_error("operation_admission_unavailable")

    async def operation_insert(self, *, operation_id, user_id, session_id, agent_id, trace_id, envelope):
        self._check()
        if operation_id in self.rows:
            return False
        now = _now_ms()
        self.rows[operation_id] = {"status": op.ACCEPTED, "user_id": user_id, "agent_id": agent_id,
                                   "session_id": session_id, "trace_id": trace_id,
                                   "envelope": copy.deepcopy(envelope), "touched_ms": now,
                                   "result_ref": {}}
        return True

    async def operation_get(self, operation_id):
        self._check()
        row = self.rows.get(operation_id)
        if row is None:
            return None
        return {"status": row["status"], "user_id": row["user_id"], "agent_id": row["agent_id"],
                "envelope": copy.deepcopy(row["envelope"]), "touched_ms": row["touched_ms"],
                "now_ms": _now_ms()}

    async def operation_claim(self, operation_id, *, observed_binding, envelope, session_id, trace_id):
        self._check()
        row = self.rows.get(operation_id)
        env = row["envelope"] if row else {}
        if (row is None or row["status"] != op.ACCEPTED or env.get("phase") != op.PHASE_AWAITING
                or env.get("binding_sha256") != observed_binding
                or not isinstance(env.get("expires_at_ms"), int) or env["expires_at_ms"] <= _now_ms()):
            return False
        row.update(envelope=copy.deepcopy(envelope), session_id=session_id, trace_id=trace_id,
                   touched_ms=_now_ms())
        return True

    async def operation_settle(self, operation_id, *, binding, status, phase, result_ref, await_ttl_ms=0):
        self._check()
        if self.fail_settle:
            raise _store_error("operation_settle_failed")
        row = self.rows.get(operation_id)
        if row is None or row["envelope"].get("binding_sha256") != binding:
            return False
        if not (row["status"] == op.ORPHANED or (row["status"] == op.ACCEPTED
                                                 and row["envelope"].get("phase") == op.PHASE_EXECUTING)):
            return False
        row["status"] = status
        row["envelope"]["phase"] = phase
        if await_ttl_ms > 0:
            row["envelope"]["expires_at_ms"] = _now_ms() + int(await_ttl_ms)
        row["result_ref"] = copy.deepcopy(result_ref)
        return True

    async def operation_mark_stale(self, operation_id, *, binding, stale_s, result_ref):
        self._check()
        row = self.rows.get(operation_id)
        if (row is None or row["status"] != op.ACCEPTED or row["envelope"].get("phase") != op.PHASE_EXECUTING
                or row["envelope"].get("binding_sha256") != binding
                or row["touched_ms"] > _now_ms() - int(stale_s * 1000)):
            return False
        row.update(status=op.ORPHANED, result_ref=copy.deepcopy(result_ref))
        return True

    async def operation_mark_expired(self, operation_id, *, binding, result_ref):
        self._check()
        row = self.rows.get(operation_id)
        if (row is None or row["status"] != op.ACCEPTED or row["envelope"].get("phase") != op.PHASE_AWAITING
                or row["envelope"].get("binding_sha256") != binding
                or int(row["envelope"].get("expires_at_ms") or 0) > _now_ms()):
            return False
        row.update(status=op.CANCELLED, result_ref=copy.deepcopy(result_ref))
        return True


def _store_error(code: str):
    from .ledger import OperationStoreError
    return OperationStoreError(code)
