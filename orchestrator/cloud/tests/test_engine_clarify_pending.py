"""澄清续接（评审 2026-09-19 §5.3 / W10）：澄清进挂起表，点选是**选择结果**不是一段自然语言。

此前 `plan.clarify` 只是一张卡：客户端把 `send_text` 当新话轮回发（带 `clarify_resume=1`），
planner 从零猜；服务端不知道自己问过什么、用户选了什么。W10 起：

- 澄清轮落一条 `phase=wait_clarify` 挂起（`operation_id` / question / options / 截止）；
- 下一轮按 `operation_id`、裸序数、选项 label、`send_text` 原文（老客户端）解出**哪一项**；
- 选项带 planner 预解析的 `step`（`capability_ref` + slots 经 catalog 校验）时**零 LLM 直接执行**，
  没带才回退到「send_text 重新规划」；
- 同一个问题没有进展才止损：换了问题的第二次澄清允许。
全部进程内 stub。
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

_CLARIFY = {"addressed": True, "steps": [], "clarify": {
    "question": "您是想看天气还是导航过去？",
    "options": [
        {"label": "看天气", "send_text": "查华润大厦的天气",
         "capability_ref": "cap_0001", "slots": {"city": "深圳"}},
        {"label": "导航过去", "send_text": "导航去华润大厦"},
    ]}}
_CLARIFY_SAME = {"addressed": True, "steps": [], "clarify": {
    "question": "你希望我怎么处理华润大厦？",
    "options": [{"label": "看天气", "send_text": "查华润大厦的天气"},
                {"label": "导航过去", "send_text": "导航去华润大厦"}]}}
_CLARIFY_OTHER = {"addressed": True, "steps": [], "clarify": {
    "question": "要今天的还是明天的天气？",
    "options": [{"label": "今天", "send_text": "查华润大厦今天的天气"},
                {"label": "明天", "send_text": "查华润大厦明天的天气"}]}}
_WEATHER_PLAN = {"addressed": True, "steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {"city": "深圳"},
     "depends_on": [], "slot_refs": {}}]}


class _Cap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description, self.examples = intent, slots, intent, []
        self.heavy = False


def _agent():
    manifest = SimpleNamespace(
        agent_id="info", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[], kind="agent", deployment="cloud",
        route_hints=[], context_scopes=[], capabilities=[_Cap("info.weather", ["city"])])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50070")


class _Resp:
    def __init__(self, status=0, speech=""):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, None, []


class _Spy:
    """planner 按**用户原话**给计划：裸「华润大厦」→ 澄清；其余 → 天气单步。"""

    def __init__(self, replies: dict | None = None):
        self.plan_calls: list[str] = []
        self.agent_calls: list[tuple[str, dict]] = []
        self.replies = replies or {}

    async def llm(self, messages, **kwargs):
        if "任务编排器" not in messages[0]["content"]:
            return "今天晴，25度。"
        user = messages[-1]["content"]
        said = user.rsplit("用户说: ", 1)[-1].strip()
        self.plan_calls.append(said)
        for key, plan in self.replies.items():
            if key in said:
                return json.dumps(plan, ensure_ascii=False)
        return json.dumps(_CLARIFY if said == "华润大厦" else _WEATHER_PLAN, ensure_ascii=False)

    async def resolve(self, query="", intent="", top_k=1):
        return [_agent()]

    async def list_agents(self):
        return [_agent()]

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None, **kw):
        self.agent_calls.append((intent, dict(slots or {})))
        return _Resp(speech="今天晴，25度。")

    async def append_turn(self, *a, **k):
        pass


def _make_engine(replies=None):
    spy = _Spy(replies)
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session)
    return engine, spy, session


def _req(text, *, meta=None, operation_id=""):
    return SimpleNamespace(
        text=text, session_id="s1", request_id="r1", is_confirmation=False,
        operation_id=operation_id, meta=meta or {},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


@pytest.fixture(autouse=True)
def _clarify_on(monkeypatch):
    monkeypatch.setenv("CLARIFY_ENABLED", "on")


# ── 澄清轮：落挂起、卡带寻址键 ─────────────────────────────────────────────

def test_a_clarify_turn_suspends_with_an_operation_id_and_a_structured_card():
    engine, spy, session = _make_engine()
    final = _run(engine, _req("华润大厦"))[-1]
    assert final["speech"] == "您是想看天气还是导航过去？"
    card = final["ui_card"]
    assert card["type"] == "intent_choice" and final["operation_id"].startswith("op-")
    assert card["operation_id"] == final["operation_id"]
    assert card["expires_at_ms"] > 0
    assert [(o["index"], o["label"]) for o in card["options"]] == [(1, "看天气"), (2, "导航过去")]
    # 内部字段（预解析的 step）不进卡
    assert all("step" not in o and "capability_ref" not in o for o in card["options"])
    state = asyncio.run(session.load("s1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_clarify"
    assert state.clarify["question"] == "您是想看天气还是导航过去？"
    assert state.clarify["options"][0]["step"]["intent"] == "info.weather"   # 预解析了
    assert "step" not in state.clarify["options"][1]                        # 没给 ref 的没有
    assert not spy.agent_calls


# ── 选择：四条通道解出同一项 ─────────────────────────────────────────────

@pytest.mark.parametrize("say, op", [
    ("第一个", ""), ("看天气", ""), ("查华润大厦的天气", ""),   # 裸序数 / label / 老客户端回发 send_text
    ("1", "op"),                                              # 新客户端：寻址键 + 序号
])
def test_choosing_a_pre_resolved_option_executes_without_another_planning_call(say, op):
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    plans_before = len(spy.plan_calls)
    final = _run(engine, _req(say, operation_id=first["operation_id"] if op else ""))[-1]
    assert len(spy.plan_calls) == plans_before, "预解析的选项不许再过一次规划"
    assert spy.agent_calls == [("info.weather", {"city": "深圳"})]
    assert "晴" in final["speech"]
    assert first["operation_id"] in final["closed_operation_ids"]
    assert asyncio.run(session.load("s1", owner_user_id="u1")) is None


def test_choosing_an_option_without_a_ref_replans_its_send_text_as_a_resume_turn():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    final = _run(engine, _req("第二个"))[-1]
    assert spy.plan_calls[-1] == "导航去华润大厦"        # 用的是选项的完整指令，不是「第二个」
    assert first["operation_id"] in final["closed_operation_ids"]
    assert asyncio.run(session.load("s1", owner_user_id="u1")) is None


def test_an_old_client_echoing_send_text_with_clarify_resume_is_matched_deterministically():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    final = _run(engine, _req("导航去华润大厦", meta={"clarify_resume": "1"}))[-1]
    assert first["operation_id"] in final["closed_operation_ids"]


def test_a_choice_addressed_by_operation_id_wins_over_the_newest_pending():
    """两条挂起并存（先澄清、后确认）：带寻址键的「1」打给澄清那条。"""
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    asyncio.run(session.save("s1", SessionState(
        phase="wait_confirm", owner_user_id="u1", operation_id="op-confirm",
        pending_step_id="s9", pending_plan={"goal": "解锁车门", "steps": []})))
    final = _run(engine, _req("1", operation_id=first["operation_id"]))[-1]
    assert spy.agent_calls == [("info.weather", {"city": "深圳"})]
    left = [s.operation_id for s in asyncio.run(session.load_all("s1", owner_user_id="u1"))]
    assert left == ["op-confirm"]


# ── 换题：挂起保留，新话正常规划 ───────────────────────────────────────────

def test_an_unrelated_sentence_keeps_the_clarify_pending_and_plans_normally():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    final = _run(engine, _req("今天深圳天气怎么样"))[-1]
    assert spy.plan_calls[-1] == "今天深圳天气怎么样"
    assert first["operation_id"] not in (final.get("closed_operation_ids") or [])
    assert first["operation_id"] in (final.get("held_operation_ids") or [])
    assert "选择" in (final.get("follow_up") or "")
    state = asyncio.run(session.load("s1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_clarify"


def test_cancel_closes_the_clarify_pending():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    final = _run(engine, _req("算了"))[-1]
    assert first["operation_id"] in final["closed_operation_ids"]
    assert asyncio.run(session.load("s1", owner_user_id="u1")) is None


def test_an_out_of_range_ordinal_is_not_a_choice():
    engine, spy, session = _make_engine()
    _run(engine, _req("华润大厦"))
    _run(engine, _req("第五个"))
    assert not spy.agent_calls
    state = asyncio.run(session.load("s1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_clarify"


# ── 止损：同一个问题不再问；换了问题可以再问 ───────────────────────────────

def test_the_same_question_again_after_a_choice_is_not_asked_a_second_time():
    engine, spy, session = _make_engine({"导航去华润大厦": _CLARIFY_SAME})
    _run(engine, _req("华润大厦"))
    final = _run(engine, _req("第二个"))[-1]
    assert (final.get("ui_card") or {}).get("type") != "intent_choice"
    assert asyncio.run(session.load("s1", owner_user_id="u1")) is None


def test_a_different_question_after_a_choice_is_asked():
    """缺日期、补完日期后缺门店是两个问题——止损限制的是「同一个问题没有进展」。"""
    engine, spy, session = _make_engine({"导航去华润大厦": _CLARIFY_OTHER})
    _run(engine, _req("华润大厦"))
    final = _run(engine, _req("第二个"))[-1]
    assert (final.get("ui_card") or {}).get("type") == "intent_choice"
    assert final["speech"] == "要今天的还是明天的天气？"
    state = asyncio.run(session.load("s1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_clarify"
    assert state.clarify["question"] == "要今天的还是明天的天气？"


def test_pending_state_outlet_names_a_clarify_as_waiting_for_a_choice():
    engine, spy, session = _make_engine()
    _run(engine, _req("华润大厦"))
    final = _run(engine, _req("现在还有待确认的操作吗"))[-1]
    assert "等你选择" in final["speech"]
