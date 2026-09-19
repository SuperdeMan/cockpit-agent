"""对话行为标签 + 任务帧（评审 2026-09-19 §3.1–3.2 / W06 + W07）。

W06：planner 可额外输出顶层 `acts`（prompt-only、fail-open：`correct` / `resume` /
`new_goal` / `ask_explanation`），缺省 = 今天的行为。W07：焦点里多一格 `active_task`
（task_id / intent / slots / revision / outcome / ts），每个任务性步骤执行后落账、跨轮接力。
两者在 engine 里合成唯一的消费面：**「改口」精确修改对象**——`acts` 含 `correct` 且本轮
单步与活动任务同 intent ⇒ 缺的槽从活动任务继承（新值优先），revision+1、task_id 不变；
新任务（无标签 / 换 intent / 活动任务过期）一个槽都不继承。
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore


def _nav_plan(slots: dict, acts: list | None = None) -> dict:
    wire = {"addressed": True, "complexity": "simple", "goal": "导航", "steps": [
        {"id": "s1", "capability_ref": "cap_0002", "slots": slots,
         "depends_on": [], "slot_refs": {}}]}
    if acts is not None:
        wire["acts"] = acts
    return wire


class _Cap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description, self.examples = intent, slots, intent, []
        self.heavy = False


def _agent():
    manifest = SimpleNamespace(
        agent_id="navigation", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[], kind="agent", deployment="cloud",
        route_hints=[], context_scopes=[],
        capabilities=[_Cap("navigation.navigate_to", ["destination", "arrive_by"])])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50061")


def _info_agent():
    manifest = SimpleNamespace(
        agent_id="info", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[], kind="agent", deployment="cloud",
        route_hints=[], context_scopes=[], capabilities=[_Cap("info.weather", ["city"])])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50070")


class _Action:
    def __init__(self, kind):
        self.type, self.payload, self.require_confirm = kind, None, False


class _Resp:
    def __init__(self, speech="", actions=None):
        self.status, self.speech, self.follow_up = 0, speech, ""
        self.actions, self.ui_card, self.data, self.missing_slots = list(actions or []), None, None, []


class _Spy:
    def __init__(self, replies: dict):
        self.replies = replies
        self.agent_calls: list[tuple[str, dict]] = []
        self.plan_calls: list[str] = []

    async def llm(self, messages, **kwargs):
        if "任务编排器" not in messages[0]["content"]:
            return "好的。"
        said = messages[-1]["content"].rsplit("用户说: ", 1)[-1].strip()
        self.plan_calls.append(said)
        for key, wire in self.replies.items():
            if key in said:
                return json.dumps(wire, ensure_ascii=False)
        return json.dumps({"addressed": True, "steps": []})

    async def resolve(self, query="", intent="", top_k=1):
        return [_info_agent(), _agent()]

    async def list_agents(self):
        return [_info_agent(), _agent()]

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None, **kw):
        self.agent_calls.append((intent, dict(slots or {})))
        if intent == "info.weather":
            return _Resp(speech="今天晴。")                       # 纯查询：零动作
        return _Resp(speech="已开始导航。", actions=[_Action("navigate")])

    async def append_turn(self, *a, **k):
        pass


def _make(replies):
    spy = _Spy(replies)
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session)
    return engine, spy, session


def _req(text):
    return SimpleNamespace(
        text=text, session_id="s1", request_id="r1", is_confirmation=False,
        operation_id="", meta={},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, text):
    async def collect():
        return [e async for e in engine.run(_req(text))]
    return asyncio.run(collect())


def _focus(session):
    return asyncio.run(session.load_focus("s1", owner_user_id="u1")) or {}


_WEATHER = {"addressed": True, "steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {"city": "深圳"},
     "depends_on": [], "slot_refs": {}}]}
_REPLIES = {
    "深圳湾公园": _nav_plan({"destination": "深圳湾公园", "arrive_by": "19:00"}),
    "天气": _WEATHER,
    "改成7点半": _nav_plan({"arrive_by": "19:30"}, acts=["correct"]),
    "去机场": _nav_plan({"destination": "机场"}),
    "没标签只改时间": _nav_plan({"arrive_by": "20:00"}),
}


# ── W07：任务帧落账与接力 ─────────────────────────────────────────────

def test_an_executed_task_step_writes_the_active_task_frame():
    engine, spy, session = _make(_REPLIES)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    task = _focus(session)["active_task"]
    assert task["intent"] == "navigation.navigate_to"
    assert task["slots"] == {"destination": "深圳湾公园", "arrive_by": "19:00"}
    assert task["revision"] == 1 and task["outcome"] == "completed"
    assert task["task_id"].startswith("task-") and task["ts"] > 0


def test_the_task_frame_survives_a_turn_that_is_not_a_task():
    engine, spy, session = _make(_REPLIES)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    first = _focus(session)["active_task"]
    _run(engine, "随便聊聊")                       # 空计划轮
    assert _focus(session)["active_task"]["task_id"] == first["task_id"]


def test_a_read_in_between_does_not_displace_the_write_task():
    """「导航去公园 → 查个天气 → 改成7点半」：改的是导航。写任务只被写任务顶掉。"""
    engine, spy, session = _make(_REPLIES)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    first = _focus(session)["active_task"]
    _run(engine, "深圳天气怎么样")
    assert spy.agent_calls[-1][0] == "info.weather"
    task = _focus(session)["active_task"]
    assert task["task_id"] == first["task_id"] and task["kind"] == "write"
    _run(engine, "改成7点半")
    assert spy.agent_calls[-1] == (
        "navigation.navigate_to", {"destination": "深圳湾公园", "arrive_by": "19:30"})


def test_a_read_becomes_the_task_when_no_write_task_is_live():
    engine, spy, session = _make(_REPLIES)
    _run(engine, "深圳天气怎么样")
    task = _focus(session)["active_task"]
    assert task["intent"] == "info.weather" and task["kind"] == "read"


# ── W06 × W07：改口精确修改对象 ────────────────────────────────────────

def test_a_correction_inherits_the_missing_slots_and_bumps_the_revision():
    engine, spy, session = _make(_REPLIES)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    first = _focus(session)["active_task"]
    _run(engine, "改成7点半")
    assert spy.agent_calls[-1] == (
        "navigation.navigate_to", {"destination": "深圳湾公园", "arrive_by": "19:30"})
    task = _focus(session)["active_task"]
    assert task["task_id"] == first["task_id"] and task["revision"] == 2
    assert task["slots"]["arrive_by"] == "19:30"


def test_a_new_goal_inherits_nothing_and_starts_a_new_task():
    engine, spy, session = _make(_REPLIES)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    first = _focus(session)["active_task"]
    _run(engine, "去机场")
    assert spy.agent_calls[-1] == ("navigation.navigate_to", {"destination": "机场"})
    task = _focus(session)["active_task"]
    assert task["task_id"] != first["task_id"] and task["revision"] == 1


def test_without_the_correct_label_slots_are_not_inherited():
    """标签是闸：模型没说这是改口，系统不替它猜（误继承一个陈旧的目的地比漏继承更危险）。"""
    engine, spy, session = _make(_REPLIES)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    _run(engine, "没标签只改时间")
    assert spy.agent_calls[-1] == ("navigation.navigate_to", {"arrive_by": "20:00"})


def test_a_stale_task_is_not_patched():
    engine, spy, session = _make(_REPLIES)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    saved = _focus(session)
    saved["active_task"]["ts"] = time.time() - 4000
    asyncio.run(session.save_focus("s1", saved, owner_user_id="u1"))
    _run(engine, "改成7点半")
    assert spy.agent_calls[-1] == ("navigation.navigate_to", {"arrive_by": "19:30"})


def test_a_correction_for_another_intent_does_not_touch_the_task():
    replies = dict(_REPLIES)
    replies["改成7点半"] = {"addressed": True, "steps": [
        {"id": "s1", "capability_ref": "cap_0002", "slots": {"arrive_by": "19:30"},
         "depends_on": [], "slot_refs": {}}], "acts": ["correct"]}
    engine, spy, session = _make(replies)
    _run(engine, "导航去深圳湾公园，晚上7点前到")
    saved = _focus(session)
    saved["active_task"]["intent"] = "trip.modify"       # 活动任务是别的事
    asyncio.run(session.save_focus("s1", saved, owner_user_id="u1"))
    _run(engine, "改成7点半")
    assert spy.agent_calls[-1] == ("navigation.navigate_to", {"arrive_by": "19:30"})


def test_the_focus_block_tells_the_planner_about_the_active_task():
    from orchestrator.cloud.context import Focus, _render_focus
    block = _render_focus(Focus(active_task={
        "task_id": "task-1", "intent": "navigation.navigate_to", "agent_id": "navigation",
        "slots": {"destination": "深圳湾公园", "arrive_by": "19:00"}, "revision": 2,
        "outcome": "completed", "ts": time.time(), "goal": "导航去深圳湾公园"}))
    assert "当前任务=navigation.navigate_to" in block and "第2版" in block
    assert "destination=深圳湾公园" in block


# ── W06 解析与 prompt 门控 ───────────────────────────────────────────────

def test_parse_acts_keeps_only_the_closed_vocabulary_in_order():
    from orchestrator.cloud.planning import _parse_acts
    assert _parse_acts(["correct", "CORRECT", "resume", "bogus", 3]) == ["correct", "resume"]
    assert _parse_acts("correct") == ["correct"]
    assert _parse_acts(None) == [] and _parse_acts({"a": 1}) == []


def test_acts_section_is_prompt_only_and_switchable(monkeypatch):
    from orchestrator.cloud import planning
    monkeypatch.delenv("PLANNER_ACTS", raising=False)
    assert "对话行为标注" in planning._planner_system()
    monkeypatch.setenv("PLANNER_ACTS", "off")
    assert "对话行为标注" not in planning._planner_system()
    # 不进 submit_plan schema（schema 可见性诱发多填，同 emotion / clarify 的裁决）
    catalog = planning._assemble_capability_catalog([_agent()])
    schema = json.dumps(planning._submit_plan_tools(catalog), ensure_ascii=False)
    assert '"acts"' not in schema


def test_a_stale_frame_is_dropped_at_load():
    from orchestrator.cloud.context import ContextManager
    session = SessionStore(redis_url="")
    cm = ContextManager(SimpleNamespace(), session)
    asyncio.run(session.save_focus("s1", {
        "active_task": {"task_id": "task-old", "intent": "navigation.navigate_to",
                        "slots": {"destination": "x"}, "revision": 1, "outcome": "completed",
                        "ts": time.time() - 4000, "goal": "g"},
        "focus_ts": time.time()}, owner_user_id="u1"))
    loaded = asyncio.run(cm._load_focus("s1", "u1"))
    assert loaded is not None and loaded.active_task == {}

