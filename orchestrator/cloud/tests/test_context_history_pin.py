"""批 5 W19：请求级历史视窗 pin `meta.planner_history_exchanges`（单变量 A/B 的入口）。

此前视窗只有部署缺省 `PLANNER_HISTORY_EXCHANGES`（compose 不透传 ⇒ 生产恒 2），比较 2 对 vs 4 对
要两次发布。pin 只放宽视窗不放宽预算；不进 `prefs`（Agent 看不见）；越界 / 非法一律缺省。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from orchestrator.cloud.context import (
    ContextManager, WorkingSet, _HISTORY_EXCHANGES, _HISTORY_PIN_MAX, build_context,
    pinned_history_exchanges_from_meta)
from runtime import memory_read as mr


def _agent(agent_id):
    caps = [SimpleNamespace(intent=f"{agent_id}.x", slots=[], description=agent_id)]
    manifest = SimpleNamespace(agent_id=agent_id, capabilities=caps,
                               kind="agent", deployment="cloud")
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50000")


def _history(pairs: int) -> list[dict]:
    out = []
    for i in range(pairs):
        out.append({"role": "user", "text": f"问{i}"})
        out.append({"role": "assistant", "text": f"答{i}"})
    return out


class _Clients:
    def __init__(self, history):
        self._history = history
        self.last_n_seen = None

    async def list_agents(self):
        return [_agent("a")]

    async def get_session_read(self, session_id, last_n, *, user_id="", occupant_id=""):
        self.last_n_seen = last_n
        return list(self._history[-last_n:]), mr.FOUND

    async def recall_read(self, user_id, query="", **kw):
        return [], mr.NONE


def _req(meta):
    return SimpleNamespace(request_id="r", session_id="s", meta=meta,
                           context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def test_meta_pin_is_parsed_only_within_range():
    assert pinned_history_exchanges_from_meta({"planner_history_exchanges": "4"}) == 4
    assert pinned_history_exchanges_from_meta({"planner_history_exchanges": "1"}) == 1
    assert pinned_history_exchanges_from_meta({"planner_history_exchanges": str(_HISTORY_PIN_MAX)}) == _HISTORY_PIN_MAX
    for bad in ("0", "7", "99", "-1", "abc", "", "4.0", None):
        assert pinned_history_exchanges_from_meta({"planner_history_exchanges": bad}) == 0, bad
    assert pinned_history_exchanges_from_meta({}) == 0


def test_pin_lands_on_the_context_but_not_in_prefs():
    ctx = build_context(_req({"planner_history_exchanges": "4", "answer_length": "short"}))
    assert ctx.history_exchanges == 4
    assert "planner_history_exchanges" not in ctx.prefs
    assert build_context(_req({})).history_exchanges == 0


def test_pinned_window_widens_the_fetch_and_the_render():
    clients = _Clients(_history(6))
    ctx = build_context(_req({"planner_history_exchanges": "4"}))
    ws = asyncio.run(ContextManager(clients).assemble("hi", ctx))
    assert clients.last_n_seen == 2 * 4 + 2
    ws.render_context()
    assert ws.context_stats["history_exchanges"] == 4
    assert ws.context_stats["history_pairs_kept"] == 4
    assert "问2" in ws.render_context() and "问1" not in ws.render_context()


def test_default_window_is_unchanged_without_a_pin():
    clients = _Clients(_history(6))
    ctx = build_context(_req({}))
    ws = asyncio.run(ContextManager(clients).assemble("hi", ctx))
    assert clients.last_n_seen == 2 * _HISTORY_EXCHANGES + 2
    ws.render_context()
    assert ws.context_stats["history_exchanges"] == _HISTORY_EXCHANGES
    assert ws.context_stats["history_pairs_kept"] == _HISTORY_EXCHANGES


def test_budget_is_still_the_hard_cap():
    """pin 放宽视窗不放宽预算：4 对装不下就照 W02 的规矩整对丢。"""
    long_pairs = [{"role": r, "text": ("很长的一句话" * 40)} for _ in range(4) for r in ("user", "assistant")]
    ws = WorkingSet(history=long_pairs, history_exchanges=4)
    ws.render_context()
    assert ws.context_stats["history_pairs_kept"] < 4
    assert ws.context_stats["ctx_chars"] <= 1400
