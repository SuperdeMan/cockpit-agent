"""批 5 W17：记忆 / 历史**读不到**时的确定性出口（挂点契约，跑 `engine.run`）。

| 出口 | 条件 | 结果 |
|---|---|---|
| 记忆问句（新） | `is_memory_recall_question` ∧ 胶囊 `memory_state=unavailable` | 「记忆服务这会儿连不上…」，零 Agent 零 LLM，kind `memory_unavailable` |
| 执行史（既有） | 问「刚才执行了什么」∧ `history_state=unavailable` | 「查不到记录」而不是「没有执行记录」 |
| 记忆问句、读得到 | `memory_state ∈ {found, none}` | 照旧进 Planner（chitchat 带召回作答） |

同 `test_engine_session_facts` 的理由：判据是纯函数，「那条路径会不会走到它」不自动成立。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore
from runtime import memory_read as mr

_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
]})


class _Cap:
    def __init__(self, intent):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = False
        self.examples = []
        self.response_only = True


def _agents():
    chat = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="chitchat", trust_level="internal", latency_budget_ms=15000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("chitchat.talk")], route_hints=[],
    ), endpoint="stub:50060")
    return [chat]


class _Resp:
    def __init__(self, status=0, speech="", data=None, ui_card=None):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.missing_slots = [], ui_card, []
        self.data = data


class _Spy:
    def __init__(self, *, history_state=mr.NONE, memory_state=mr.NONE, turns=None):
        self.unary_calls: list[str] = []
        self.llm_calls = 0
        self.history_state = history_state
        self.memory_state = memory_state
        self.turns = list(turns or [])
        self.outcomes: list[str] = []

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append(intent)
        return _Resp(speech="（chitchat 兜底）")

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append(intent)
        yield ("final", _Resp(speech="（chitchat 兜底）"))

    async def llm(self, messages, **kwargs):
        self.llm_calls += 1
        if "任务编排器" in messages[0]["content"]:
            return _PLAN
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()

    async def list_agents(self):
        return _agents()

    async def append_turn(self, *a, **k):
        return None

    async def get_session_read(self, session_id, last_n=6, *, user_id="", occupant_id=""):
        return list(self.turns), self.history_state

    async def recall_read(self, user_id, query="", **kw):
        return [], self.memory_state


def _engine(spy):
    return PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""),
    )


def _req(text, session_id):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id=f"r-{text[:6]}",
        is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, text, session_id):
    async def collect():
        return [e async for e in engine.run(_req(text, session_id))]
    return asyncio.run(collect())


def _final(events):
    return [e for e in events if e.get("kind") == "final"][-1]


@pytest.fixture
def outcome_spy(monkeypatch):
    """截 `_emit_outcome` 拿 kind（内部键在 `run()` 里已被剥掉）。"""
    seen: list[str] = []
    original = PlannerEngine._emit_outcome

    async def spy(ctx, kind, event, actions):
        seen.append(kind)
        await original(ctx, kind, event, actions)

    monkeypatch.setattr(PlannerEngine, "_emit_outcome", staticmethod(spy))
    return seen


# ── 记忆问句 × 读不到 ────────────────────────────────────────────────────────

def test_memory_question_with_memory_unavailable_is_answered_honestly(outcome_spy):
    spy = _Spy(memory_state=mr.UNAVAILABLE)
    final = _final(_run(_engine(spy), "你还记得我不吃辣吗", "s-mem-1"))
    assert final["speech"] == mr.MEMORY_UNAVAILABLE_SPEECH
    assert final.get("actions", []) == []
    assert spy.unary_calls == [], "读不到时不该把问题交给 chitchat 去编"
    assert spy.llm_calls == 0
    assert outcome_spy[-1] == "memory_unavailable"


@pytest.mark.parametrize("state", [mr.FOUND, mr.NONE, mr.OFF])
def test_memory_question_reaches_the_planner_when_memory_is_readable(state):
    """**误伤对照**：读得到（哪怕是空的）就照旧进 Planner——「没有」也是一个可以如实答的事实。"""
    spy = _Spy(memory_state=state)
    _run(_engine(spy), "你还记得我不吃辣吗", "s-mem-2")
    assert spy.unary_calls, "读得到时被短路吞掉了"


def test_non_memory_question_ignores_memory_state():
    spy = _Spy(memory_state=mr.UNAVAILABLE)
    _run(_engine(spy), "讲个笑话", "s-mem-3")
    assert spy.unary_calls, "记忆不可用不该影响普通轮"


# ── 执行史 × 历史读不到 ─────────────────────────────────────────────────────

def test_execution_audit_with_history_unavailable_says_cannot_read_not_none(outcome_spy):
    spy = _Spy(history_state=mr.UNAVAILABLE)
    final = _final(_run(_engine(spy), "刚才实际执行了什么", "s-hist-1"))
    assert final["speech"] == mr.HISTORY_UNAVAILABLE_SPEECH
    assert "没有" not in final["speech"]
    assert spy.unary_calls == [] and spy.llm_calls == 0
    assert outcome_spy[-1] == "fact_answered"


def test_execution_audit_with_empty_readable_history_still_says_no_record():
    """对照：读到了、账是空的 ⇒ 照旧如实说没有记录（既有行为一个字不变）。"""
    spy = _Spy(history_state=mr.NONE)
    final = _final(_run(_engine(spy), "刚才实际执行了什么", "s-hist-2"))
    assert final["speech"] != mr.HISTORY_UNAVAILABLE_SPEECH
    assert spy.unary_calls == []


# ── 批 5 W18-b：「我今天说过不吃辣吗」读的是焦点里的会话约束 ───────────────────

def test_constraint_recall_question_is_answered_from_the_focus(outcome_spy):
    spy = _Spy()
    engine = _engine(spy)
    first = _final(_run(engine, "我不吃辣，也不想排长队", "s-cr-1"))
    assert "不吃辣" in first["speech"]                  # W13 的确定性致谢（登记成功）
    calls_before, llm_before = len(spy.unary_calls), spy.llm_calls
    final = _final(_run(engine, "我今天说过不吃辣吗", "s-cr-1"))
    assert final["speech"] == "您这次说过：不吃辣、不想排队。找地方的时候我按这个来。"
    assert len(spy.unary_calls) == calls_before and spy.llm_calls == llm_before
    assert outcome_spy[-1] == "fact_answered"


def test_constraint_question_never_registers_a_constraint():
    """此前「你还记得我不吃辣吗」会被当成纯陈述登记成 `no_spicy=True`——一句问话改写了事实。"""
    spy = _Spy()
    engine = _engine(spy)
    _run(engine, "你还记得我不吃辣吗", "s-cr-2")
    focus = asyncio.run(engine.context._load_focus("s-cr-2", "u1"))
    assert not (getattr(focus, "session_constraints", None) or {})


def test_constraint_question_without_a_session_constraint_reaches_the_planner():
    """有账才劫持：这次会话里没说过 ⇒ 交回规划（长期记忆里有没有由 chitchat 带召回去答）。"""
    spy = _Spy()
    _run(_engine(spy), "我今天说过不吃辣吗", "s-cr-3")
    assert spy.unary_calls, "空账被短路吞掉了"
