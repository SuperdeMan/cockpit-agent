"""T2（adaptive / reactive）收口与 E 路径同一份：清续接挂起、写焦点、补挂起软提醒。

批 8 ①（2026-09-22 continuity 第一趟 T63/T69 实录）：「接孩子后去万象城」走了四轮 T2、发出两次
`navigate`，六轮后「取消导航」答「当前没有正在进行的导航」——navigation 每一步都声明了
`_route_session`，而 T2 的两条出口（adaptive / reactive）在 `loop.run` 之后直接 `return`，
`update_focus` / `_settle_session` / `_append_pending_hint` 都在它们之后。E 路径与流式路径
都做的三件事，T2 一件没做：候选批、活动路线、任务帧、Agent 声明的安全告警在 T2 轮全部丢失，
确认续接进 T2 的那条挂起也不会被关掉。「抽取改对了，而调用方在它之前就返回了」——同一形态第四例。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from google.protobuf import struct_pb2
from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, ReplanDecision, SessionState, Step
from orchestrator.cloud.session import SessionStore


_ROUTE = {"destination": "深圳万象城", "lat": 22.53923, "lng": 114.11068,
          "waypoints": [{"name": "鼎太小学", "lat": 22.522059, "lng": 113.912866}],
          "strategy": "", "arrive_by_ts": 0}


class _Planner:
    """`build` 返回脚本计划；`replan` 按脚本给决策（缺省一轮就 done）。"""

    def __init__(self, plan, decisions=None):
        self.plan = plan
        self.decisions = list(decisions or [ReplanDecision(done=True)])
        self.replans = 0

    async def build(self, *_args, **_kwargs):
        return self.plan

    async def replan(self, *_args, **_kwargs):
        self.replans += 1
        return self.decisions.pop(0) if self.decisions else ReplanDecision(done=True)


class _Clients:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls: list[tuple[str, dict]] = []

    async def list_agents(self):
        return []

    async def resolve(self, query="", intent="", top_k=1):
        return []

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        return self.responses[intent]

    async def call_agent_stream(self, *args, **kwargs):
        # 零输出 ⇒ T2 按 B5 §4 回退 unary（`allow_unary_fallback`），结果仍经 executor
        if False:
            yield None


async def _aggregate(_messages, **_kwargs):
    return "done"


def _response(speech, data=None, status=agent_pb2.ExecuteResponse.OK, actions=()):
    payload = struct_pb2.Struct()
    payload.update(data or {})
    resp = agent_pb2.ExecuteResponse(status=status, speech=speech, data=payload)
    for name in actions:
        resp.actions.add(type=name)
    return resp


def _request(text="接孩子后去万象城", *, is_confirmation=False, operation_id=""):
    return SimpleNamespace(
        text=text, session_id="sess-t2", request_id="r-t2", is_confirmation=is_confirmation,
        operation_id=operation_id, meta={},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _nav_step(step_id="s1", **overrides):
    fields = dict(id=step_id, agent_id="navigation", endpoint="nav:1",
                  intent="navigation.navigate_to", slots={"destination": "万象城"})
    fields.update(overrides)
    return Step(**fields)


def _engine(plan, responses, *, decisions=None):
    clients = _Clients(responses)
    planner = _Planner(plan, decisions)
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=clients, planner=planner,
        executor=DagExecutor(call_agent_fn=clients.call_agent),
        aggregator=Aggregator(_aggregate), session=session,
    )
    return engine, clients, session, planner


def _run(engine, request):
    async def collect():
        return [event async for event in engine.run(request)]
    return asyncio.run(collect())


def _focus(session):
    return asyncio.run(session.load_focus("sess-t2", owner_user_id="u1")) or {}


def test_adaptive_turn_writes_the_route_session_into_focus():
    """activity：T2 完成轮的 `_route_session` 必须落 `focus.active_route`——下一句「取消导航」靠它。"""
    plan = Plan(steps=[_nav_step()], complexity="adaptive", goal="接孩子后去万象城",
                raw_text="接孩子后去万象城")
    engine, clients, session, planner = _engine(
        plan, {"navigation.navigate_to": _response(
            "已规划到深圳万象城", {"destination": "深圳万象城", "_route_session": _ROUTE},
            actions=("navigate",))})

    events = _run(engine, _request())

    assert events[-1]["kind"] == "final" and not events[-1].get("need_confirm")
    assert planner.replans == 1                      # 真的走了 T2 而不是 E 路径
    focus = _focus(session)
    assert focus.get("active_route", {}).get("destination") == "深圳万象城",         f"T2 完成轮没有写活动路线：{focus}"


def test_adaptive_turn_focus_covers_replanned_steps_too():
    """再规划批的步不在初计划里——候选批 / 任务帧按 plan.steps 抽，漏了它们就等于漏了半个 T2。"""
    plan = Plan(steps=[Step(id="s1", agent_id="info", endpoint="info:1", intent="info.weather",
                            slots={"city": "深圳"})],
                complexity="adaptive", goal="看天气再找餐厅", raw_text="看看天气，再找家餐厅")
    replanned = Step(id="s2", agent_id="nearby", endpoint="nearby:1", intent="nearby.search",
                     slots={"keyword": "餐厅"})
    engine, clients, session, planner = _engine(
        plan,
        {"info.weather": _response("深圳晴", {"city": "深圳"}),
         "nearby.search": _response("找到两家", {"items": [
             {"name": "南店1", "lat": 22.5, "lng": 113.9, "rating": 4.5},
             {"name": "南店2", "lat": 22.5, "lng": 113.9, "rating": 4.2}]})},
        decisions=[ReplanDecision(done=False, steps=[replanned]), ReplanDecision(done=True)])

    _run(engine, _request("看看天气，再找家餐厅"))

    focus = _focus(session)
    sets = focus.get("candidate_sets") or []
    assert [s.get("source_intent") for s in sets] == ["nearby.search"], focus
    assert [i.get("name") for i in sets[0]["items"]] == ["南店1", "南店2"]
    task = focus.get("active_task") or {}
    assert task.get("intent") == "nearby.search" and task.get("outcome") == "completed", task


def test_adaptive_confirm_resume_clears_the_consumed_pending_and_reports_it():
    """确认续接进 T2：本轮续接上的那条挂起要关掉（与 E 路径 `_settle_session` 同一件事），
    并经 final 的 `closed_operation_ids` 告诉客户端撤确认条；此前它会在挂起表里躺到 TTL。"""
    plan = Plan(steps=[_nav_step(require_confirm=True)], complexity="adaptive",
                goal="导航去万象城", raw_text="导航去万象城", safety_origin_text="导航去万象城")
    engine, clients, session, planner = _engine(
        plan, {"navigation.navigate_to": _response(
            "出发", {"destination": "深圳万象城", "_route_session": _ROUTE}, actions=("navigate",))})
    asyncio.run(session.save("sess-t2", SessionState(
        phase="wait_confirm", owner_user_id="u1", operation_id="op-t2-confirm",
        pending_step_id="s1", pending_plan=PlannerEngine._serialize_plan(plan))))

    events = _run(engine, _request("确认", is_confirmation=True, operation_id="op-t2-confirm"))

    final = events[-1]
    assert final["kind"] == "final" and not final.get("need_confirm")
    assert clients.calls and clients.calls[-1][1].get("confirmed") == "true"
    assert asyncio.run(session.load("sess-t2", owner_user_id="u1")) is None,         "确认续接完成后那条挂起仍在表里"
    assert "op-t2-confirm" in (final.get("closed_operation_ids") or [])


def test_adaptive_interjection_final_carries_the_held_pending_hint():
    """插话轮进 T2：挂起保留（R2），final 要像 E 路径一样带软提醒与 `held_operation_ids`。"""
    plan = Plan(steps=[Step(id="s1", agent_id="info", endpoint="info:1", intent="info.weather",
                            slots={"city": "深圳"})],
                complexity="adaptive", goal="查天气", raw_text="深圳天气怎么样")
    engine, clients, session, planner = _engine(
        plan, {"info.weather": _response("深圳晴", {"city": "深圳"})})
    held = Plan(steps=[_nav_step(require_confirm=True)], raw_text="导航去万象城")
    asyncio.run(session.save("sess-t2", SessionState(
        phase="wait_confirm", owner_user_id="u1", operation_id="op-held",
        pending_step_id="s1", pending_plan=PlannerEngine._serialize_plan(held))))

    events = _run(engine, _request("深圳天气怎么样"))

    final = events[-1]
    assert final.get("held_operation_ids") == ["op-held"], final
    assert "确认" in str(final.get("follow_up") or ""), final
    state = asyncio.run(session.load("sess-t2", owner_user_id="u1"))
    assert state is not None and state.operation_id == "op-held"      # 挂起没被插话轮清掉


def test_adaptive_suspend_neither_writes_focus_nor_touches_the_new_pending():
    """T2 里挂起的那一步没做成：它自己带的路线会话不落焦点（挂起轮只登记已执行的事实），新挂起也不能被收口误清。"""
    plan = Plan(steps=[_nav_step(require_confirm=True)], complexity="adaptive",
                goal="导航去万象城", raw_text="导航去万象城", safety_origin_text="导航去万象城")
    engine, clients, session, planner = _engine(
        plan, {"navigation.navigate_to": _response(
            "要开始导航吗？", {"destination": "深圳万象城", "_route_session": _ROUTE},
            status=agent_pb2.ExecuteResponse.NEED_CONFIRM)})

    events = _run(engine, _request("导航去万象城"))

    final = events[-1]
    assert final.get("need_confirm") is True
    assert not _focus(session).get("active_route")
    state = asyncio.run(session.load("sess-t2", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_confirm"
    assert planner.replans == 0


class _GoalRecordingPlanner(_Planner):
    def __init__(self, plan, decisions=None):
        super().__init__(plan, decisions)
        self.goals: list[str] = []

    async def replan(self, goal, *args, **kwargs):
        self.goals.append(goal)
        return await super().replan(goal, *args, **kwargs)


def test_a_slot_answer_that_replaces_the_destination_is_not_chased_again_by_the_loop():
    """评审四轮（`4438ea6b` 真栈 RS39）：adaptive 计划的再规划批里 navigate_to 找不到「云岚国际中心」⇒ 追问挂起；用户答「深圳湾公园」。
    修前：续接导航之后循环按**模型写的旧 goal** 再规划一次，又去搜云岚国际中心、再挂一条追问，话术「…没找到「云岚国际中心」…」，
    导航动作却已经发出去了。修后：goal 跟着用户改、追旧值的步不跑、路线会话登记、不留新挂起。"""
    batch = ReplanDecision(done=False, steps=[_nav_step("t1-r1", slots={"destination": "云岚国际中心"})]).to_plan(
        "解析\"云岚国际中心\"为可导航的具体地点后启动导航", safety_origin_text="导航去云岚国际中心")
    batch.raw_text = "导航去云岚国际中心"
    stale = [Step(id="r1", agent_id="navigation", endpoint="nav:1", intent="navigation.search_poi",
                  slots={"keyword": "云岚国际中心"}),
             _nav_step("r2", slots={"destination": "云岚国际中心"}, depends_on=["r1"])]
    clients = _Clients({"navigation.navigate_to": _response(
        "为您导航到深圳湾公园", {"destination": "深圳湾公园",
                                "_route_session": {**_ROUTE, "destination": "深圳湾公园"}}, actions=("navigate",)),
        "navigation.search_poi": _response("没找到「云岚国际中心」。")})
    planner = _GoalRecordingPlanner(batch, [ReplanDecision(done=False, steps=stale), ReplanDecision(done=True)])
    session = SessionStore(redis_url="")
    engine = PlannerEngine(clients=clients, planner=planner,
                           executor=DagExecutor(call_agent_fn=clients.call_agent),
                           aggregator=Aggregator(_aggregate), session=session)
    asyncio.run(session.save("sess-t2", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-slot", pending_step_id="t1-r1",
        missing_slots=["destination"], pending_plan=PlannerEngine._serialize_plan(batch))))

    events = _run(engine, _request("深圳湾公园"))

    final = events[-1]
    assert final["kind"] == "final" and not final.get("need_confirm"), final
    assert [intent for intent, _meta in clients.calls] == ["navigation.navigate_to"], clients.calls
    assert planner.goals and "深圳湾公园" in planner.goals[0] and "云岚国际中心" not in planner.goals[0], planner.goals
    assert "云岚国际中心" not in str(final.get("speech") or ""), final
    assert _focus(session).get("active_route", {}).get("destination") == "深圳湾公园"
    assert asyncio.run(session.load("sess-t2", owner_user_id="u1")) is None, "不该再留一条追问旧地点的挂起"


def test_the_replan_batch_marker_survives_the_pending_round_trip():
    """`replan_batch` 随挂起持久化（`_serialize_plan` → `_restore`）；旧记录没有这一键 ⇒ False（修前行为）。"""
    batch = ReplanDecision(done=False, steps=[_nav_step("t1-r1")]).to_plan("导航")
    record = PlannerEngine._serialize_plan(batch)
    restored, _ = PlannerEngine._restore(None, SessionState(phase="wait_slot", pending_step_id="t1-r1", pending_plan=record),
                                         inject_confirmed=False)
    assert restored.replan_batch is True
    legacy = dict(record)
    legacy.pop("replan_batch")
    restored, _ = PlannerEngine._restore(None, SessionState(phase="wait_slot", pending_step_id="t1-r1", pending_plan=legacy),
                                         inject_confirmed=False)
    assert restored.replan_batch is False


def test_reactive_upgrade_turn_writes_focus_from_seed_and_loop_results():
    """第二条 T2 出口（simple 计划执行后 `_needs_replan` 升级）：种子步（初计划）与再规划步的产物都要落焦点。"""
    plan = Plan(steps=[Step(id="s1", agent_id="nearby", endpoint="nearby:1", intent="nearby.search",
                            slots={"keyword": "充电站"})],
                goal="找充电站再导航", raw_text="找个充电站，然后导航过去")
    replanned = _nav_step("r1", slots={"destination": "南店1"})
    engine, clients, session, planner = _engine(
        plan,
        {"nearby.search": _response("找到两家", {"replan": True, "items": [
             {"name": "南店1", "lat": 22.5, "lng": 113.9, "rating": 4.5},
             {"name": "南店2", "lat": 22.5, "lng": 113.9, "rating": 4.2}]}),
         "navigation.navigate_to": _response(
             "出发", {"destination": "南店1", "_route_session": {**_ROUTE, "destination": "南店1"}},
             actions=("navigate",))},
        decisions=[ReplanDecision(done=False, steps=[replanned]), ReplanDecision(done=True)])

    events = _run(engine, _request("找个充电站，然后导航过去"))

    assert events[-1]["kind"] == "final" and planner.replans == 2
    focus = _focus(session)
    assert [s.get("source_intent") for s in focus.get("candidate_sets") or []] == ["nearby.search"], focus
    assert focus.get("active_route", {}).get("destination") == "南店1", focus
