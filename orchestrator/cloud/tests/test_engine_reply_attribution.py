"""回复归属：不带寻址键的序数只归**最近那个提示**（评审四轮 R4-02，2026-09-24）。

修前 `_clarify_target` 不带 `operation_id` 时从全部挂起里逆序找最新一条 wait_clarify，不管它是不是最近那个提示，
engine 又在补槽分支与候选装配**之前**消费这个选择：
  · 旧澄清 + 新补槽（等门店 / 商品选项）+「2」⇒ 选中旧澄清的第 2 项，关掉旧澄清、原话改写成那一项的 send_text；
  · 旧澄清 + 之后刚列过一份新候选 +「第二个」⇒ 同样被旧澄清吃掉（真栈 `ecbeed28` RS33 三趟都答成「云岚国际中心…」）。
这不是数字识别失败，是**回复归属错了**。判据：wait_clarify 只在它是最新一条挂起、且焦点里没有比它更新的候选列表时接序数；
选项 label / send_text 原文是用户点名了那个问题的选项，照接；带寻址键（点旧卡）照接。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import SessionState
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore

_CLARIFY = {"addressed": True, "steps": [], "clarify": {
    "question": "您是想看天气还是导航过去？",
    "options": [{"label": "看天气", "send_text": "查华润大厦的天气"},
                {"label": "导航过去", "send_text": "导航去华润大厦"}]}}
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
    def __init__(self):
        self.plan_calls: list[str] = []
        self.agent_calls: list[tuple[str, dict]] = []

    async def llm(self, messages, **kwargs):
        if "任务编排器" not in messages[0]["content"]:
            return "今天晴，25度。"
        said = messages[-1]["content"].rsplit("用户说: ", 1)[-1].strip()
        self.plan_calls.append(said)
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


def _make_engine():
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm), session=session)
    return engine, spy, session


def _req(text, *, operation_id=""):
    return SimpleNamespace(
        text=text, session_id="s1", request_id=f"r-{time.monotonic_ns()}", is_confirmation=False,
        operation_id=operation_id, meta={}, context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def _pending_ids(session):
    return [s.operation_id for s in asyncio.run(session.load_all("s1", owner_user_id="u1"))]


def _list_shown(session, ts):
    """焦点里放一份候选列表（等价于那一轮 nearby.search 之后 `update_focus` 落的那份）。"""
    asyncio.run(session.save_focus("s1", {"candidate_sets": [{
        "source_intent": "nearby.search", "agent_id": "nearby", "purpose": "list", "ts": ts,
        "is_fallback": False, "label": "", "revision": 1, "query_signature": "q",
        "items": [{"name": "咖啡A"}, {"name": "咖啡B"}, {"name": "咖啡C"}]}]}, owner_user_id="u1"))


def _slot_pending(session):
    asyncio.run(session.save("s1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-slot", pending_step_id="s1",
        missing_slots=["city"],
        pending_plan={"goal": "查天气", "raw_text": "查一下天气", "safety_origin_text": "查一下天气",
                      "steps": [{"id": "s1", "agent_id": "info", "intent": "info.weather",
                                 "slots": {}, "depends_on": [], "slot_refs": {}}]})))


@pytest.fixture(autouse=True)
def _clarify_on(monkeypatch):
    monkeypatch.setenv("CLARIFY_ENABLED", "on")


# ── 反例：旧澄清不抢 ─────────────────────────────────────────────────────────

def test_an_older_clarify_does_not_take_a_number_meant_for_the_newer_slot_question():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    _slot_pending(session)
    final = _run(engine, _req("2"))[-1]
    assert spy.agent_calls == [("info.weather", {"city": "2"})], "「2」是新补槽问题的答案"
    assert first["operation_id"] in _pending_ids(session), "旧澄清留着（R2：插话不清挂起）"
    assert first["operation_id"] not in (final.get("closed_operation_ids") or [])
    assert "导航去华润大厦" not in spy.plan_calls


def test_an_older_clarify_does_not_take_an_ordinal_meant_for_a_newer_list():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    _list_shown(session, ts=time.time() + 5)
    final = _run(engine, _req("第二个"))[-1]
    assert spy.plan_calls[-1] == "第二个", "交给规划（带候选）去接，不改写成旧选项的 send_text"
    assert first["operation_id"] in _pending_ids(session)
    assert first["operation_id"] not in (final.get("closed_operation_ids") or [])


# ── 正常对照：澄清就是最近的提示 / 点名 / 寻址 ──────────────────────────────────────

def test_the_newest_clarify_still_takes_the_ordinal():
    engine, spy, session = _make_engine()
    _list_shown(session, ts=time.time() - 60)          # 列表更早，澄清更新
    first = _run(engine, _req("华润大厦"))[-1]
    final = _run(engine, _req("第二个"))[-1]
    assert spy.plan_calls[-1] == "导航去华润大厦"
    assert first["operation_id"] in final["closed_operation_ids"]


def test_naming_an_option_of_the_older_question_still_resolves_it():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    _slot_pending(session)
    final = _run(engine, _req("导航过去"))[-1]
    assert spy.plan_calls[-1] == "导航去华润大厦"
    assert first["operation_id"] in final["closed_operation_ids"]
    assert "op-slot" in _pending_ids(session), "新补槽那条不受影响"


def test_an_addressed_choice_on_the_old_card_still_resolves_it():
    engine, spy, session = _make_engine()
    first = _run(engine, _req("华润大厦"))[-1]
    _list_shown(session, ts=time.time() + 5)
    final = _run(engine, _req("2", operation_id=first["operation_id"]))[-1]
    assert spy.plan_calls[-1] == "导航去华润大厦"
    assert first["operation_id"] in final["closed_operation_ids"]


@pytest.mark.parametrize("text,positional", [
    ("第二个", True), ("2", True), ("选第一个吧", True), ("二号方案", True),
    ("导航过去", False), ("导航去华润大厦", False), ("第二天", False)])
def test_positional_reply_shapes(text, positional):
    assert PlannerEngine._clarify_reply_is_positional(text) is positional
