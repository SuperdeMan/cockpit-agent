"""PlannerEngine 对话上下文测试（task 2：对话记忆 + 指代消解的数据来源）。

覆盖：本轮按 用户→助手 顺序写入对话记忆；规划时注入此前历史到 planner prompt；
memory_enabled=false 时整轮不读写记忆。进程内 stub，不依赖 gRPC。
"""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.session import SessionStore
from security.permission import PermissionEngine

_PLAN_JSON = json.dumps({"steps": [
    {"id": "s1", "agent_id": "demo", "intent": "demo.do", "slots": {}, "depends_on": []},
]})


class _Cap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description = intent, slots, intent


def _agent():
    manifest = SimpleNamespace(
        agent_id="demo", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[], capabilities=[_Cap("demo.do", [])])
    return SimpleNamespace(manifest=manifest, endpoint="stub:1")


class _Resp:
    def __init__(self, status=0, speech="已为您打开空调。"):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, None, []


class _CtxSpy:
    def __init__(self, history=None):
        self._history = history or []
        self.appended: list[tuple[str, str]] = []
        self.session_reads = 0
        self.planner_prompts: list[str] = []

    # 单步计划会先尝试流式；spy 不支持 → 抛错 → engine 回退 unary（覆盖回退路径）
    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        raise RuntimeError("no stream")
        yield  # pragma: no cover

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        return _Resp()

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            self.planner_prompts.append(messages[1]["content"])
            return _PLAN_JSON
        return "（聚合）"

    async def resolve(self, query="", intent="", top_k=1):
        return [_agent()]

    async def list_agents(self):
        return [_agent()]

    async def append_turn(self, session_id, role, text):
        self.appended.append((role, text))

    async def get_session(self, session_id, last_n=6):
        self.session_reads += 1
        return self._history


def _make_engine(spy):
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""),
        perms=PermissionEngine(),
    )
    return engine


def _req(text, session_id="sess-c", is_confirmation=False, meta=None):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation, meta=meta or {},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def test_records_user_then_assistant_turn():
    spy = _CtxSpy()
    engine = _make_engine(spy)
    _run(engine, _req("打开空调"))
    # 本轮结束后按顺序落库
    assert spy.appended == [("user", "打开空调"), ("assistant", "已为您打开空调。")]


def test_history_injected_into_planner_prompt():
    spy = _CtxSpy(history=[
        {"role": "user", "text": "把副驾空调调到26度"},
        {"role": "assistant", "text": "好的，副驾空调已设到26度。"},
    ])
    engine = _make_engine(spy)
    _run(engine, _req("再调高一点"))
    assert spy.session_reads == 1
    prompt = spy.planner_prompts[0]
    assert "最近对话" in prompt          # 注入了上下文块
    assert "副驾" in prompt              # 指代消解所需的前文在 prompt 里
    assert "再调高一点" in prompt        # 当前话术也在


def test_memory_disabled_skips_read_and_write():
    spy = _CtxSpy(history=[{"role": "user", "text": "之前说过的"}])
    engine = _make_engine(spy)
    _run(engine, _req("打开空调", meta={"memory_enabled": "false"}))
    assert spy.appended == []            # 不写
    assert spy.session_reads == 0        # 不读
    assert "最近对话" not in spy.planner_prompts[0]
