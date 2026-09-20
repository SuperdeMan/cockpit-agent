"""批 5 W16/W17：胶囊（`WorkingSet`）上的两格读态 `history_state` / `memory_state`。

此前 `_history` / `_recall` 各自 `except Exception: return []`——「读到了、是空的」与「根本没读到」
在胶囊上是同一个值。现在读侧三态（`runtime.memory_read`）随装配落在胶囊上、随 `context_stats`
进 `cloud.planning` span。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from orchestrator.cloud.context import ContextManager
from runtime import memory_read as mr


def _agent(agent_id):
    caps = [SimpleNamespace(intent=f"{agent_id}.x", slots=[], description=agent_id)]
    manifest = SimpleNamespace(agent_id=agent_id, capabilities=caps,
                               kind="agent", deployment="cloud")
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50000")


def _ctx(user_id="u1"):
    return SimpleNamespace(session_id="sess", user_id=user_id, occupant_id="primary")


class _ReadClients:
    """生产形态：带三态读接口。"""
    def __init__(self, history=([], mr.NONE), memories=([], mr.NONE)):
        self._history = history
        self._memories = memories

    async def list_agents(self):
        return [_agent("a")]

    async def get_session_read(self, session_id, last_n, *, user_id="", occupant_id=""):
        return self._history

    async def recall_read(self, user_id, query="", **kw):
        return self._memories


class _LegacyClients:
    """旧形态 / 测试替身：只有两参 `get_session` 与 `recall`，状态由装配层推断。"""
    def __init__(self, history=None, memories=None, raise_history=False, raise_recall=False):
        self._history = history or []
        self._memories = memories or []
        self._raise_history = raise_history
        self._raise_recall = raise_recall

    async def list_agents(self):
        return [_agent("a")]

    async def get_session(self, session_id, last_n):
        if self._raise_history:
            raise RuntimeError("memory down")
        return list(self._history)

    async def recall(self, user_id, query="", **kw):
        if self._raise_recall:
            raise RuntimeError("memory down")
        return list(self._memories)


def _assemble(clients, *, mem_on=True):
    return asyncio.run(ContextManager(clients).assemble("hi", _ctx(), mem_on=mem_on))


def test_read_interface_states_land_on_the_capsule():
    ws = _assemble(_ReadClients(
        history=([{"role": "user", "text": "x"}], mr.FOUND),
        memories=([], mr.UNAVAILABLE)))
    assert ws.history_state == mr.FOUND
    assert ws.memory_state == mr.UNAVAILABLE
    assert ws.history == [{"role": "user", "text": "x"}]
    assert ws.memories == []


def test_legacy_clients_infer_state_from_result_and_exception():
    ws = _assemble(_LegacyClients(history=[{"role": "user", "text": "x"}], memories=[]))
    assert ws.history_state == mr.FOUND
    assert ws.memory_state == mr.NONE
    ws = _assemble(_LegacyClients(raise_history=True, raise_recall=True))
    assert ws.history_state == mr.UNAVAILABLE
    assert ws.memory_state == mr.UNAVAILABLE
    assert ws.history == [] and ws.memories == []      # 降级语义不变：绝不阻塞规划


def test_memory_off_is_off_not_none_not_unavailable():
    """用户关了记忆：没有去读。它既不是故障也不是「用户没说过」。"""
    ws = _assemble(_ReadClients(), mem_on=False)
    assert ws.history_state == mr.OFF
    assert ws.memory_state == mr.OFF


def test_no_user_id_recall_is_off():
    clients = _ReadClients(memories=([{"text": "x"}], mr.FOUND))
    ws = asyncio.run(ContextManager(clients).assemble("hi", _ctx(user_id="")))
    assert ws.memory_state == mr.OFF
    assert ws.memories == []


def test_states_are_in_context_stats():
    ws = _assemble(_ReadClients(history=([], mr.UNAVAILABLE), memories=([], mr.NONE)))
    ws.render_context()
    assert ws.context_stats["history_state"] == mr.UNAVAILABLE
    assert ws.context_stats["memory_state"] == mr.NONE


def test_default_capsule_states_are_none():
    """没装配过的胶囊（确认 / 补槽续接轮 `working_set=None` 之外的直接构造）缺省是 none：
    旧构造方零改动。"""
    from orchestrator.cloud.context import WorkingSet
    ws = WorkingSet()
    assert ws.history_state == mr.NONE and ws.memory_state == mr.NONE


def test_a_raising_read_interface_is_unavailable_too():
    """生产客户端的 `*_read` 自己不抛；万一抛了（替身 / 旧版本），装配层同样记 unavailable。"""
    class _Raising(_ReadClients):
        async def get_session_read(self, *a, **k):
            raise RuntimeError("boom")

        async def recall_read(self, *a, **k):
            raise RuntimeError("boom")

    ws = _assemble(_Raising())
    assert ws.history_state == mr.UNAVAILABLE and ws.memory_state == mr.UNAVAILABLE
    assert ws.history == [] and ws.memories == []


def test_planning_span_attrs_carry_read_states_and_window():
    """胶囊上的读态与视窗随 `cloud.planning` span 出去（W05「按实际请求记录」的载体）。"""
    from orchestrator.cloud.engine import _context_stats_attrs
    from orchestrator.cloud.context import WorkingSet
    ws = WorkingSet(history=[{"role": "user", "text": "x"}, {"role": "assistant", "text": "y"}],
                    history_state=mr.UNAVAILABLE, memory_state=mr.OFF, history_exchanges=4)
    ws.render_context()
    attrs = _context_stats_attrs(ws)
    assert attrs["history_state"] == mr.UNAVAILABLE
    assert attrs["memory_state"] == mr.OFF
    assert attrs["history_exchanges"] == 4
    assert attrs["history_pairs_kept"] == 1
    ws.context_stats["memory_state"] = "not-a-state"      # 值域外的不出 span
    assert "memory_state" not in _context_stats_attrs(ws)
