"""通用 escalate 机制契约测试（2026-07-12 mode-routing 设计 P1-2）。

协议：Agent 在 `AgentResult.data["_escalate"]={"intent","slots","reason"}` 声明改派，
engine 有界消费（每轮最多 1 跳）：目标步经 `PlanBuilder._validated_steps` 装配成单步
mini-plan 走 executor（heavy/latency_budget/权限自动带出）。固化的契约：
  a) D0 流式 final 带 _escalate（零播报）→ 改派执行，final=escalated 结果
  b) escalated 结果再带 _escalate → 不再跳（单跳预算，结构性防环）
  c) intent 无承接 Agent → 忽略 + 诚实兜底话术
  d) executor 多步计划：仅声明步的结果被替换，其余步保留进聚合
  e) escalated NEED_CONFIRM → F1 挂起语义一致
  f) 已流式播报（streamed=True）→ 忽略 escalate，不二次回答
  g) heavy escalated 步发过程区 execute 事件 + meta thinking=on
全部进程内 stub（装置风格同 test_engine_stream.py），不依赖 gRPC。
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

_SINGLE_PLAN = json.dumps({"steps": [
    {"id": "s1", "agent_id": "chitchat", "intent": "chitchat.talk", "slots": {}, "depends_on": []},
]})
_TWO_STEP_PLAN = json.dumps({"steps": [
    {"id": "s1", "agent_id": "chitchat", "intent": "chitchat.talk", "slots": {}, "depends_on": []},
    {"id": "s2", "agent_id": "chitchat", "intent": "chitchat.talk", "slots": {}, "depends_on": ["s1"]},
]})

_ESC = {"_escalate": {"intent": "info.search",
                      "slots": {"query": "昨晚欧冠决赛结果"},
                      "reason": "needs_realtime"}}


class _Cap:
    def __init__(self, intent, heavy=False):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = heavy
        self.examples = []


def _agents():
    chitchat = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("chitchat.talk")], route_hints=[],
    ), endpoint="stub:50062")
    info = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="info", trust_level="first_party", latency_budget_ms=50000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("info.search", heavy=True)], route_hints=[],
    ), endpoint="stub:50063")
    return [chitchat, info]


class _Resp:
    def __init__(self, status=0, speech="", follow_up="", data=None):
        self.status, self.speech, self.follow_up = status, speech, follow_up
        self.actions, self.ui_card, self.missing_slots = [], None, []
        self.data = data


class _EscSpy:
    """clients stub：D0 流式脚本 + 按 intent/次序可编程的 unary 响应。"""

    def __init__(self, plan_json=_SINGLE_PLAN, script=None, unary_seq=None):
        self.plan_json = plan_json
        self.script = script or []
        self.unary_seq = list(unary_seq or [])   # [(intent_expected_or_None, _Resp)] 顺序出队
        self.stream_calls: list[str] = []
        self.unary_calls: list[tuple[str, dict, dict]] = []

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        self.stream_calls.append(intent)
        for item in self.script:
            yield item

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


def _req(text, session_id="sess-esc", is_confirmation=False):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def test_d0_escalate_redirects_to_search_with_process_region():
    """契约 a+g：零播报 chitchat 声明改派 → executor 跑 info.search（带 heavy 预算/thinking/
    过程区），final=搜索结果话术。"""
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=dict(_ESC)))],
        unary_seq=[_Resp(speech="昨晚决赛皇马夺冠。")])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    final = events[-1]
    assert final["kind"] == "final"
    assert final["speech"] == "昨晚决赛皇马夺冠。"
    # 改派步走 unary executor（绝不裸 call_agent 的 10s 默认超时——预算经 _validated_steps 带出）
    assert [c[0] for c in spy.unary_calls] == ["info.search"]
    assert spy.unary_calls[0][1].get("query") == "昨晚欧冠决赛结果"
    assert spy.unary_calls[0][2].get("thinking") == "on"       # heavy → 动态开思考
    progress = [e for e in events if e["kind"] == "progress"]
    assert any(e["phase"] == "execute" and e["status"] == "running" for e in progress)
    assert any(e["phase"] == "execute" and e["status"] == "done" for e in progress)


def test_escalated_result_cannot_escalate_again():
    """契约 b：escalated 结果再带 _escalate → 不再跳（单跳预算），且保留键被剥离。"""
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=dict(_ESC)))],
        unary_seq=[_Resp(speech="搜到了。", data={"_escalate": {
            "intent": "info.search", "slots": {"query": "再来一跳"}}})])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    assert [c[0] for c in spy.unary_calls] == ["info.search"]  # 只有一跳
    assert events[-1]["speech"] == "搜到了。"


def test_escalate_invalid_intent_ignored_with_honest_fallback():
    """契约 c：目标 intent 无承接 Agent → 忽略改派，诚实兜底话术（不空转、不崩）。"""
    spy = _EscSpy(script=[("final", _Resp(speech="", data={"_escalate": {
        "intent": "nope.intent", "slots": {}}}))])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    assert not spy.unary_calls                                  # 没有乱派
    assert "联网查询" in events[-1]["speech"]


def test_executor_path_replaces_only_escalating_step():
    """契约 d：多步计划里第一步声明改派 → 该步结果被 escalated 结果替换，第二步保留聚合。"""
    spy = _EscSpy(
        plan_json=_TWO_STEP_PLAN,
        unary_seq=[
            _Resp(speech="", data=dict(_ESC)),      # s1 chitchat：声明改派
            _Resp(speech="第二步话术。"),             # s2 chitchat：正常
            _Resp(speech="搜索结果话术。"),            # esc1 info.search
        ])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("先查个东西再聊一句"))

    assert [c[0] for c in spy.unary_calls] == [
        "chitchat.talk", "chitchat.talk", "info.search"]
    assert events[-1]["kind"] == "final"
    assert events[-1]["speech"]                                  # 聚合产出非空


def test_escalated_need_confirm_suspends_like_f1():
    """契约 e：escalated 步 NEED_CONFIRM → 走既有挂起语义（session 保存 mini-plan）。"""
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=dict(_ESC)))],
        unary_seq=[_Resp(status=1, speech="需要确认后才能继续，确认吗？")])
    engine, session = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    final = events[-1]
    assert final["need_confirm"] is True
    state = asyncio.run(session.load("sess-esc"))
    assert state is not None and state.phase == "wait_confirm"
    assert state.pending_step_id == "esc1"


def test_streamed_reply_ignores_escalate():
    """契约 f：已流式播报过增量 → 忽略 escalate（不二次回答），final 用原话术。"""
    spy = _EscSpy(script=[
        ("speech", "早上"),
        ("final", _Resp(speech="早上好呀。", data=dict(_ESC)))])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("早"))

    assert not spy.unary_calls                                   # 未改派
    assert events[-1]["speech"] == "早上好呀。"
