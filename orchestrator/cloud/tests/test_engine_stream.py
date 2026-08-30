"""PlannerEngine 单步流式直通测试（task 4：开放域"边想边说"）。

覆盖：单步计划流式下发 speech 增量 + final；流式 NEED_CONFIRM 仍走 F1 挂起；
流式不可用时安全回退 unary；多步计划不走流式（保持 executor 路径）。
全部进程内 stub，不依赖 gRPC。
"""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.models import StepStatus
from orchestrator.cloud.session import SessionStore

_SINGLE_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
]})
_TWO_STEP_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
    {"id": "s2", "capability_ref": "cap_0001", "slots": {},
     "depends_on": ["s1"], "slot_refs": {}},
]})


class _Cap:
    def __init__(self, intent, slots, *, response_only=False):
        self.intent, self.slots, self.description = intent, slots, intent
        self.response_only = response_only
        self.require_confirm = False


def _agent(intent="test.stream", *, response_only=False):
    manifest = SimpleNamespace(
        agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[_Cap(intent, [], response_only=response_only)],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50062")


def _chitchat_agent():
    return _agent("chitchat.talk", response_only=True)


class _Resp:
    def __init__(self, status=0, speech="", follow_up=""):
        self.status, self.speech, self.follow_up = status, speech, follow_up
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, None, []


class _StreamSpy:
    def __init__(self, plan_json=_SINGLE_PLAN, script=None, stream_error=False,
                 agent=None):
        self.plan_json = plan_json
        self.script = script or []
        self.stream_error = stream_error
        self.agent = agent or _agent()
        self.stream_calls: list[tuple[str, dict]] = []
        self.unary_calls: list[tuple[str, dict]] = []

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        self.stream_calls.append((intent, dict(meta or {})))
        if self.stream_error:
            raise RuntimeError("ExecuteStream unavailable")
        for item in self.script:
            yield item

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append((intent, dict(meta or {})))
        return _Resp(speech="（unary 兜底回复）")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return self.plan_json
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return [self.agent]

    async def list_agents(self):
        return [self.agent]


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


def _req(text, session_id="sess-s", is_confirmation=False):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def test_single_step_streams_deltas_then_final():
    spy = _StreamSpy(script=[
        ("speech", "为什么"), ("speech", "天空"), ("speech", "是蓝的"),
        ("final", _Resp(speech="为什么天空是蓝的，因为……")),
    ])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("讲个笑话"))

    deltas = [e["delta"] for e in events if e["kind"] == "speech"]
    assert deltas == ["为什么", "天空", "是蓝的"]          # 流式增量逐段下发
    final = events[-1]
    assert final["kind"] == "final"
    assert final["speech"] == "为什么天空是蓝的，因为……"
    assert not final.get("need_confirm")
    assert spy.stream_calls and not spy.unary_calls       # 走了流式、没走 unary


def test_d0_stream_result_is_stamped_with_executed_intent():
    spy = _StreamSpy(script=[("final", _Resp(speech="ok"))])
    engine, _ = _make_engine(spy)
    captured = {}

    async def compose(text, results, **kwargs):
        captured["source_intent"] = results[0].source_intent
        return {"speech": "ok", "actions": [], "cards": []}

    engine.aggregator.compose = compose
    _run(engine, _req("讲个笑话"))
    assert captured["source_intent"] == "test.stream"


def _capture_response_only_result(script):
    spy = _StreamSpy(script=script, agent=_chitchat_agent())
    engine, _ = _make_engine(spy)
    captured = {}

    async def compose(text, results, **kwargs):
        captured["result"] = results[0]
        return {"speech": results[0].speech, "actions": [], "cards": []}

    engine.aggregator.compose = compose
    events = _run(engine, _req("红色机油灯亮了怎么办"))
    return spy, captured["result"], events


def test_d0_response_only_action_before_final_is_dropped_and_failed_closed():
    spy, captured, events = _capture_response_only_result([
        ("action", {"type": "external.write", "payload": {"id": "x"}}),
        ("final", _Resp(status=0, speech="已处理")),
    ])

    assert not [event for event in events if event["kind"] == "action"]
    assert spy.unary_calls == []
    assert captured.status == StepStatus.FAILED
    assert captured.actions == []
    assert captured.error == "response_only_contract_violation"
    assert not events[-1].get("need_confirm")


def test_d0_response_only_action_without_final_is_terminal_without_unary_fallback():
    spy, captured, events = _capture_response_only_result([
        ("action", {"type": "external.write", "payload": {"id": "x"}}),
    ])

    assert not [event for event in events if event["kind"] == "action"]
    assert spy.unary_calls == []
    assert captured.status == StepStatus.FAILED
    assert captured.actions == []
    assert captured.error == "response_only_contract_violation"
    assert not events[-1].get("need_confirm")


def test_d0_response_only_legal_speech_stream_is_unchanged():
    spy, captured, events = _capture_response_only_result([
        ("speech", "先停车，"),
        ("speech", "再检查机油液位。"),
        ("final", _Resp(status=0, speech="先停车，再检查机油液位。")),
    ])

    assert [event["delta"] for event in events if event["kind"] == "speech"] == [
        "先停车，", "再检查机油液位。",
    ]
    assert spy.unary_calls == []
    assert captured.status == StepStatus.OK
    assert captured.actions == []


def test_stream_need_confirm_still_suspends():
    """流式单步返回 NEED_CONFIRM 时，仍按 F1 保存挂起态。"""
    spy = _StreamSpy(script=[
        ("speech", "确认"),
        ("final", _Resp(status=1, speech="确认要这样做吗？", follow_up="说确认即可")),
    ])
    engine, session = _make_engine(spy)
    events = _run(engine, _req("做这件事"))

    final = events[-1]
    assert final["need_confirm"] is True
    state = asyncio.run(session.load("sess-s", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_confirm" and state.pending_step_id == "s1"


def test_stream_unavailable_falls_back_to_unary():
    """ExecuteStream 抛错（不支持/连接失败）→ 安全回退 executor 的 unary 调用。"""
    spy = _StreamSpy(stream_error=True)
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("讲个笑话"))

    final = events[-1]
    assert final["kind"] == "final"
    assert final["speech"] == "（unary 兜底回复）"
    assert spy.stream_calls and spy.unary_calls           # 尝试了流式、回退到 unary


def test_multi_step_plan_does_not_stream():
    """多步计划保持 executor 路径，不走流式直通；复杂任务以过程区 progress 事件反馈。"""
    spy = _StreamSpy(plan_json=_TWO_STEP_PLAN)
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("先聊一句再聊一句"))

    assert not spy.stream_calls                            # 没有流式调用
    assert len(spy.unary_calls) == 2                       # 两步都走 unary
    # 多步=复杂任务：现在用过程区 progress 事件反馈（analyze→execute→synthesize），
    # 不再用 "正在为您处理" speech 占位，逐步信息也不再刷进气泡（只留最终答案）。
    progress = [e for e in events if e["kind"] == "progress"]
    assert any(e["phase"] == "understand" for e in progress)  # 过程区起点：理解需求
    assert any(e["phase"] == "plan" for e in progress)        # 规划步骤
    assert any(e["phase"] == "execute" for e in progress)     # 每步执行进度
    assert not any(e["kind"] == "speech" for e in events)     # 复杂任务不再 yield 逐步 speech
    assert events[-1]["kind"] == "final"
