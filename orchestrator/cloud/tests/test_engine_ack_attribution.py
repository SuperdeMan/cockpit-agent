"""普通应答 / 事务确认分开（评审四轮 R4-01，2026-09-24）。

修前「好的 / 嗯 / 可以 / 行」与「确认」共用一个短路入口：
  · 没有挂起时，规划器看见历史之前就答「当前没有待确认的操作」——助手刚问「还要继续讲吗」，续讲的机会被截走；
  · **有一条待确认时它就是那条的授权，与中间隔了几轮无关**——挂着「打开后备箱」、插话听了个笑话、助手问「还要再听一个吗」，
    用户答「好的」⇒ 后备箱被打开（真栈 `ecbeed28` RS34 3/3）。
判据：纯应答回答的是**最近那一问**。它只在最新一条挂起是待确认、且提出它的那一轮就是最近一轮时才授权；否则按插话保留挂起、
交规划去接最近那一问。「确认」这类事务词不受影响（R2：插话之后回头说「确认」照旧找回挂起；没有挂起时照旧诚实报过期）。
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
from runtime import memory_read


class _Cap:
    def __init__(self, intent, description, *, require_confirm=False, response_only=False):
        self.intent, self.slots, self.description = intent, [], description
        self.require_confirm, self.response_only = require_confirm, response_only


def _agents():
    vehicle = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="vehicle", trust_level="first_party", latency_budget_ms=2000, requires_permissions=[],
        capabilities=[_Cap("trunk.open", "打开后备箱", require_confirm=True)]), endpoint="stub:50070")
    chitchat = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000, requires_permissions=[],
        capabilities=[_Cap("chitchat.talk", "闲聊", response_only=True)]), endpoint="stub:50071")
    return [vehicle, chitchat]


_REFS = {"chitchat.talk": "cap_0001", "trunk.open": "cap_0002"}


class _Resp:
    def __init__(self, status=0, speech="", actions=None):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions = actions or []
        self.ui_card, self.data, self.missing_slots = None, None, []


class _Spy:
    """带**真历史**的替身：`append_turn` 落进内存，`get_session_read` 按 exchange 读回（同生产客户端的形状）。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.plans: list[str] = []
        self.history: list[dict] = []
        self.history_state = memory_read.FOUND

    def confirmed(self, intent):
        return sum(1 for i, m in self.calls if i == intent and m.get("confirmed") == "true")

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        if intent == "chitchat.talk":
            return _Resp(speech="有只企鹅走进了冰淇淋店……还要再听一个吗？")
        if (meta or {}).get("confirmed") == "true":
            return _Resp(speech="已打开后备箱。", actions=[SimpleNamespace(
                type="vehicle.control", payload={"command": intent}, require_confirm=False)])
        return _Resp(status=1, speech="这项操作可能影响车辆安全，请确认是否继续。")

    async def llm(self, messages, **kwargs):
        if "任务编排器" not in messages[0]["content"]:
            return "好的。"
        said = messages[-1]["content"].rsplit("用户说: ", 1)[-1].strip()
        self.plans.append(said)
        intent = "trunk.open" if "后备箱" in said else "chitchat.talk"
        slots = {} if intent == "trunk.open" else {"text": said}
        return json.dumps({"goal": said, "steps": [{"id": "s1", "capability_ref": _REFS[intent],
                                                     "slots": slots, "depends_on": [], "slot_refs": {}}]},
                          ensure_ascii=False)

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()

    async def list_agents(self):
        return _agents()

    async def append_turn(self, session_id, role, text, user_id="", vehicle_id="", occupant_id="",
                          e2e_memory_capability="", turn_id="", exchange_id="", actions=None, sources=None):
        self.history.append({"role": role, "text": text, "exchange_id": exchange_id,
                             "actions": list(actions or [])})

    async def get_session_read(self, session_id, last_n=6, *, user_id="", occupant_id=""):
        if self.history_state == memory_read.UNAVAILABLE:
            return [], memory_read.UNAVAILABLE
        return list(self.history[-last_n:]), (memory_read.FOUND if self.history else memory_read.NONE)


def _make():
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                           executor=DagExecutor(call_agent_fn=spy.call_agent),
                           aggregator=Aggregator(llm_fn=spy.llm), session=session)
    return engine, spy, session


_SEQ = iter(range(10_000))


def _req(text, *, meta=None, is_confirmation=False, operation_id=""):
    return SimpleNamespace(text=text, session_id="sess-1", request_id=f"x-{next(_SEQ)}", meta=meta or {},
                           is_confirmation=is_confirmation, operation_id=operation_id,
                           context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def _alive(session, op):
    return asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op)) is not None


def _suspend_trunk(engine):
    final = _run(engine, _req("打开后备箱"))[-1]
    assert final.get("need_confirm") and final.get("operation_id")
    return final["operation_id"]


# ── 危险形态：插话之后的「好的」不授权旧的确认 ────────────────────────────────────────────

@pytest.mark.parametrize("ack", ["好的", "嗯", "可以", "行", "好啊"])
def test_an_ack_after_an_interjection_does_not_confirm_the_older_pending(ack):
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    _run(engine, _req("先给我讲个笑话"))                      # 插话：助手最后问「还要再听一个吗？」
    final = _run(engine, _req(ack))[-1]
    assert spy.confirmed("trunk.open") == 0, ack
    assert _alive(session, op), "挂起按插话保留（R2）"
    assert op not in (final.get("closed_operation_ids") or [])
    assert "没有待确认" not in final["speech"]
    assert spy.plans[-1] == ack, "交给规划去接最近那一问"


def test_an_explicit_confirm_after_an_interjection_still_finds_the_pending():
    """R2 不变：插话之后回头说「确认」照旧执行——事务词冲着的就是那笔事务。"""
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    _run(engine, _req("先给我讲个笑话"))
    final = _run(engine, _req("确认"))[-1]
    assert spy.confirmed("trunk.open") == 1
    assert op in final["closed_operation_ids"]


def test_an_edge_local_turn_in_between_is_also_an_interjection():
    """插的是端侧本地轮次（没上云，历史可能还没落）：端侧签发的上一轮本地 exchange 就是证据。"""
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    final = _run(engine, _req("好的", meta={"_edge_previous_local_exchange": "edge-local-1"}))[-1]
    assert spy.confirmed("trunk.open") == 0
    assert _alive(session, op)


# ── 正常对照：确认卡就是最近那一问 ─────────────────────────────────────────────────

@pytest.mark.parametrize("ack", ["好的", "嗯", "可以", "好的好的"])
def test_an_ack_right_after_the_confirm_card_still_confirms(ack):
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    final = _run(engine, _req(ack))[-1]
    assert spy.confirmed("trunk.open") == 1, ack
    assert op in final["closed_operation_ids"]


def test_an_ack_cannot_be_proven_current_when_history_is_unreadable():
    """读不到历史 ⇒ 证明不了它是最近那一问 ⇒ 纯应答不授权（fail-safe）；显式「确认」照旧可用。"""
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    spy.history_state = memory_read.UNAVAILABLE
    _run(engine, _req("好的"))
    assert spy.confirmed("trunk.open") == 0 and _alive(session, op)
    _run(engine, _req("确认"))
    assert spy.confirmed("trunk.open") == 1


def test_an_old_record_without_its_prompt_exchange_is_not_authorised_by_an_ack():
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    state = asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op))
    state.prompt_exchange_id = ""
    asyncio.run(session.save("sess-1", state))
    _run(engine, _req("好的"))
    assert spy.confirmed("trunk.open") == 0


def test_the_pending_records_the_exchange_that_asked():
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    state = asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op))
    assert state.prompt_exchange_id and state.prompt_exchange_id == spy.history[-1]["exchange_id"]


# ── 没有挂起：普通应答交规划，事务词照旧报过期 ─────────────────────────────────────────────

@pytest.mark.parametrize("ack", ["好的", "嗯", "可以"])
def test_an_ack_without_any_pending_goes_to_the_planner(ack):
    engine, spy, _ = _make()
    _run(engine, _req("给我讲个故事的开头"))
    final = _run(engine, _req(ack))[-1]
    assert "没有待确认" not in final["speech"], ack
    assert spy.plans[-1] == ack


@pytest.mark.parametrize("word", ["确认", "好的，确认吧", "下单"])
def test_a_transaction_word_without_any_pending_is_still_honestly_expired(word):
    engine, spy, _ = _make()
    final = _run(engine, _req(word))[-1]
    assert "没有待确认" in final["speech"], word
    assert spy.plans == []


def test_intercept_rule_separates_acks_from_transaction_words():
    assert PlannerEngine._intercepts_as_confirm("确认") is True
    assert PlannerEngine._intercepts_as_confirm("取消") is True
    for ack in ("好的", "嗯", "可以", "行啊", "ok"):
        assert PlannerEngine._intercepts_as_confirm(ack) is False, ack
