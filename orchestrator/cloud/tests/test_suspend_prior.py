"""挂起前缀契约测试（旅程 A1-4 遗留卡：`_suspend` 携带前序已完成步结论）。

场景：多步/adaptive 计划里前序步已出结论（如天气），后续步挂起（NEED_SLOT/NEED_CONFIRM）
时聚合器不会跑、复杂任务又不逐步流出话术、挂起 final 还会整体替换 HMI 气泡——不带简报
用户就会被"凭空追问"（查到雨才建提醒，却没听到有雨）。固化的契约：
  a) executor 多步：挂起 final 前缀本轮已完成 OK 步简报（首句/安全计数口径）
  b) 挂起步自身不进前缀（trip/scene 确认话术本就是完整叙述——防双重播报）
  c) 确认/补槽续接：种子结果（上轮已播报）不再前缀
  d) T2 loop：跨迭代把前轮结论带进挂起；已流式播报的结果不复读
  e) 短回执（「好的」类）不值一播，过滤
装置风格同 test_engine_escalate.py，全进程内 stub。
"""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.loop import LoopController
from orchestrator.cloud.models import (
    Plan, PlanContext, ReplanDecision, Step, StepResult, StepStatus,
)
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.session import SessionStore

_TWO_STEP_PLAN = json.dumps({"steps": [
    {"id": "s1", "agent_id": "chitchat", "intent": "chitchat.talk",
     "slots": {}, "depends_on": []},
    {"id": "s2", "agent_id": "chitchat", "intent": "chitchat.talk",
     "slots": {}, "depends_on": ["s1"]},
]})
_SINGLE_PLAN = json.dumps({"steps": [
    {"id": "s1", "agent_id": "chitchat", "intent": "chitchat.talk",
     "slots": {}, "depends_on": []},
]})

_WEATHER = "明天深圳有小雨，最高29℃，记得带伞"
_ASK_TIME = "好的，带伞。什么时候提醒你？"


class _Cap:
    def __init__(self, intent):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = False
        self.examples = []


def _agents():
    return [SimpleNamespace(manifest=SimpleNamespace(
        agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("chitchat.talk")], route_hints=[],
    ), endpoint="stub:50062")]


class _Resp:
    def __init__(self, status=0, speech="", follow_up="", data=None,
                 missing_slots=None):
        self.status, self.speech, self.follow_up = status, speech, follow_up
        self.actions, self.ui_card = [], None
        self.missing_slots = missing_slots or []
        self.data = data


class _Spy:
    """clients stub：D0 流式脚本（默认空→回退 executor）+ unary 顺序出队。"""

    def __init__(self, plan_json=_TWO_STEP_PLAN, unary_seq=None):
        self.plan_json = plan_json
        self.unary_seq = list(unary_seq or [])
        self.unary_calls: list[tuple[str, dict, dict]] = []

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        return
        yield  # pragma: no cover — 空流：D0 无事件 → 安全回退 executor

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append((intent, dict(slots or {}), dict(meta or {})))
        if self.unary_seq:
            return self.unary_seq.pop(0)
        return _Resp(speech=f"（{intent} 兜底）")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return self.plan_json
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()[:1]

    async def list_agents(self):
        return _agents()


def _make_engine(spy):
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
    )
    return engine, session


def _req(text, session_id="sess-prior", is_confirmation=False):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


# ── engine executor 路径 ──────────────────────────────────────


def test_multi_step_suspend_prefixes_prior_conclusion():
    """契约 a：天气步 OK + 提醒步 NEED_SLOT → 挂起 final 先播天气结论再追问。"""
    spy = _Spy(unary_seq=[
        _Resp(speech=_WEATHER),
        _Resp(status=2, speech=_ASK_TIME, missing_slots=["time_text"]),
    ])
    engine, session = _make_engine(spy)
    events = _run(engine, _req("查明天天气，下雨就提醒我带伞"))

    final = events[-1]
    assert final["kind"] == "final"
    assert final["speech"].startswith("明天深圳有小雨")
    assert "什么时候提醒你" in final["speech"]
    state = asyncio.run(session.load("sess-prior"))
    assert state is not None and state.phase == "wait_slot"
    # 持久化的挂起结果保持原话术——前缀只出现在本轮 final，不进续接种子
    assert state.completed_results["s2"]["speech"] == _ASK_TIME


def test_single_step_confirm_speech_unchanged():
    """契约 b：单步 NEED_CONFIRM（trip/scene 形态）无前序 → 话术原样，不受前缀影响。"""
    spy = _Spy(plan_json=_SINGLE_PLAN, unary_seq=[
        _Resp(status=1, speech="将创建「下班模式」：空调24度。确认保存吗？"),
    ])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("创建一个下班模式"))

    final = events[-1]
    assert final["need_confirm"] is True
    assert final["speech"] == "将创建「下班模式」：空调24度。确认保存吗？"


def test_resume_does_not_rebroadcast_seed():
    """契约 c：补槽续接再挂起时，上轮种子（天气）不再前缀——防跨轮双重播报。"""
    spy = _Spy(unary_seq=[
        _Resp(speech=_WEATHER),
        _Resp(status=2, speech=_ASK_TIME, missing_slots=["time_text"]),
        _Resp(status=2, speech="还是没听懂，几点提醒？", missing_slots=["time_text"]),
    ])
    engine, _ = _make_engine(spy)
    _run(engine, _req("查明天天气，下雨就提醒我带伞"))
    events = _run(engine, _req("那个啥"))    # 补槽续接轮（非换话题）→ 挂起步重跑仍缺槽

    final = events[-1]
    assert final["kind"] == "final"
    assert final["speech"] == "还是没听懂，几点提醒？"
    assert "明天深圳" not in final["speech"]


def test_prior_brief_filters_trivial_and_joins():
    """契约 e：「好的」类短回执过滤；多条简报「；」相接，md 剥净。"""
    ok_media = StepResult("m1", StepStatus.OK, speech="好的")
    ok_weather = StepResult("w1", StepStatus.OK, speech="**明天有雨**，最高29℃。记得带伞。")
    ok_poi = StepResult("p1", StepStatus.OK, speech="",
                        ui_card={"type": "poi_list", "items": [{}, {}]})
    pending = StepResult("r1", StepStatus.NEED_SLOT, speech="什么时候提醒你？")
    brief = PlannerEngine._prior_brief(
        [ok_media, ok_weather, ok_poi, pending], pending)
    assert brief == "明天有雨，最高29℃；已找到 2 个地点。"


# ── T2 loop 路径 ─────────────────────────────────────────────


class _Planner:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def replan(self, goal, observations, agents, ctx,
                     granted_permissions=None, working_set=None):
        return self.decisions.pop(0)


class _Executor:
    def __init__(self, results_by_step):
        self.results_by_step = results_by_step

    async def run(self, plan, ctx, done=None):
        for step in plan.steps:
            yield self.results_by_step[step.id]


class _Aggregator:
    async def compose(self, text, results, **kwargs):
        return {"speech": "best effort", "actions": [], "cards": []}


def _collect(controller, **kwargs):
    async def run():
        return [event async for event in controller.run(**kwargs)]
    return asyncio.run(run())


def test_loop_suspend_carries_prior_iteration_conclusion():
    """契约 d：迭代1 天气 OK（unary）→ replan 补提醒步 NEED_SLOT → prior 带天气结果。"""
    weather = StepResult("s1", StepStatus.OK, speech=_WEATHER)
    planner = _Planner([ReplanDecision(done=False, steps=[
        Step(id="r1", agent_id="reminder", intent="reminder.create"),
    ])])
    executor = _Executor({
        "s1": weather,
        "r1": StepResult("r1", StepStatus.NEED_SLOT, speech=_ASK_TIME,
                         missing_slots=["time_text"]),
    })
    captured = {}

    async def suspend(step_result, results, plan, ctx, prior=None):
        captured["prior"] = list(prior or [])
        return {"kind": "final", "speech": step_result.speech}

    controller = LoopController(planner, executor, _Aggregator(), suspend,
                                max_iters=2, budget_ms=5000)
    _collect(
        controller,
        goal="查天气，下雨就提醒",
        initial_plan=Plan(steps=[Step(id="s1", agent_id="info")],
                          complexity="adaptive"),
        agents=[],
        ctx=PlanContext(),
        user_text="查明天天气，下雨就提醒我带伞",
    )

    assert captured["prior"] == [weather]


def test_loop_suspend_excludes_streamed_and_seed_results():
    """契约 c+d：已流式播报的迭代结果与续接种子都不进 prior——不复读。"""
    seed = StepResult("z1", StepStatus.OK, speech="上轮已播报过的结论")
    calls = {"n": 0}

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        from cockpit.agent.v1 import agent_pb2
        calls["n"] += 1
        if calls["n"] == 1:
            yield ("speech", _WEATHER)
            yield ("final", agent_pb2.ExecuteResponse(status=0, speech=_WEATHER))
        else:
            yield ("final", agent_pb2.ExecuteResponse(
                status=2, speech=_ASK_TIME))

    planner = _Planner([ReplanDecision(done=False, steps=[
        Step(id="r1", agent_id="reminder", intent="reminder.create",
             kind="agent", deployment="cloud", latency_budget_ms=5000),
    ])])
    captured = {}

    async def suspend(step_result, results, plan, ctx, prior=None):
        captured["prior"] = list(prior or [])
        return {"kind": "final", "speech": step_result.speech}

    controller = LoopController(planner, _Executor({}), _Aggregator(), suspend,
                                max_iters=2, budget_ms=5000, stream_fn=stream_fn)
    _collect(
        controller,
        goal="查天气，下雨就提醒",
        initial_plan=Plan(
            steps=[Step(id="s1", agent_id="info", kind="agent",
                        deployment="cloud", intent="info.weather",
                        latency_budget_ms=5000)],
            complexity="adaptive"),
        agents=[],
        ctx=PlanContext(),
        user_text="查明天天气，下雨就提醒我带伞",
        seed_results=[seed],
    )

    assert captured["prior"] == []   # 天气已流出（spoken）、种子已播报——都不复读
