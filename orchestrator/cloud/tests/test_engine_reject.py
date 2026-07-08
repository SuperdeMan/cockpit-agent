"""R4.4 engine 拒识短路 + 落库跳过单测（T2）。

覆盖 D6-1：hands-free 语音源 + addressed=false → rejected 卡短路、speech 空、_rejected 已剥离、
append_turn 未调用；REJECT_NON_ADDRESSED=off 一键回今天；显式输入（无 input_source）永不拒识；
正常受话轮行为=今天（落库）；确认续接轮不进新规划分支（拒识代码不可达）。
全部进程内 stub，不依赖 gRPC/真 LLM。
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
from orchestrator.cloud.models import SessionState

_WEATHER_PLAN = json.dumps({"steps": [
    {"id": "s1", "agent_id": "info", "intent": "info.weather", "slots": {}, "depends_on": []}]})
_REJECT_PLAN = json.dumps({"addressed": False, "steps": []})
_CLARIFY_PLAN = json.dumps({"addressed": True, "clarify": {
    "question": "您是想看详情还是导航过去？",
    "options": [{"label": "看详情", "send_text": "看华润大厦的详情"},
                {"label": "导航过去", "send_text": "导航去华润大厦"}]}})


class _Cap:
    def __init__(self, intent):
        self.intent, self.slots, self.description, self.examples = intent, [], intent, []
        self.heavy = False


def _agent():
    manifest = SimpleNamespace(
        agent_id="info", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[], kind="agent", deployment="cloud",
        route_hints=[], context_scopes=[], capabilities=[_Cap("info.weather")])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50070")


class _Resp:
    def __init__(self, status=0, speech=""):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, None, []


class _Spy:
    def __init__(self, plan_json):
        self._plan_json = plan_json
        self.append_turns: list[tuple] = []
        self.agent_calls = 0

    async def llm(self, messages, **kwargs):
        return self._plan_json if "任务编排器" in messages[0]["content"] else "今天晴，25度。"

    async def resolve(self, query="", intent="", top_k=1):
        return [_agent()]

    async def list_agents(self):
        return [_agent()]

    async def call_agent_stream(self, endpoint, intent, slots, ctx, meta=None, **kw):
        self.agent_calls += 1
        yield "final", _Resp(speech="今天晴，25度。")

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None, **kw):
        self.agent_calls += 1
        return _Resp(speech="今天晴，25度。")

    async def append_turn(self, session_id, role, text, user_id="", vehicle_id=""):
        self.append_turns.append((role, text))


def _make_engine(plan_json):
    spy = _Spy(plan_json)
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session)
    return engine, spy, session


def _req(text, *, session_id="s1", is_confirmation=False, meta=None):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation, meta=meta or {},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


_VOICE = {"input_source": "voice_followup", "voice_utterance_ms": "1200"}


def test_rejects_non_addressed_voice_source():
    engine, spy, _ = _make_engine(_REJECT_PLAN)
    events = _run(engine, _req("他昨天跟我说那个项目黄了", meta=_VOICE))
    final = events[-1]
    assert final["kind"] == "final"
    assert final["ui_card"] == {"type": "rejected", "reason": "not_addressed"}
    assert final["speech"] == ""
    assert "_rejected" not in final          # 内部键已在 run() 剥离，消费端看不到
    assert spy.append_turns == []            # 拒识轮不落库
    assert spy.agent_calls == 0              # 未触达任何 Agent


def test_reject_disabled_env_falls_through(monkeypatch):
    monkeypatch.setenv("REJECT_NON_ADDRESSED", "off")
    engine, spy, _ = _make_engine(_REJECT_PLAN)
    final = _run(engine, _req("他昨天跟我说那个项目黄了", meta=_VOICE))[-1]
    # 不拒 → 走空计划「抱歉」话术，正常落库（一键回今天）
    assert "抱歉" in final["speech"]
    assert not final.get("ui_card")
    assert len(spy.append_turns) == 2        # user + assistant 都落库


def test_no_input_source_never_rejects():
    """显式输入（push-to-talk/文本/候选选择）无 input_source → 即便 addressed=false 也不拒。"""
    engine, spy, _ = _make_engine(_REJECT_PLAN)
    final = _run(engine, _req("他昨天跟我说那个项目黄了", meta={}))[-1]
    assert "抱歉" in final["speech"]
    assert not final.get("ui_card")
    assert len(spy.append_turns) == 2


def test_addressed_voice_executes_normally():
    """voice 源 + addressed=true + 正常 steps → 行为与今天一致（执行 + 落库）。"""
    engine, spy, _ = _make_engine(_WEATHER_PLAN)
    final = _run(engine, _req("今天天气怎么样", meta={"input_source": "voice_wake"}))[-1]
    assert final["speech"]                   # 有正常应答
    assert not (final.get("ui_card") or {}).get("type") == "rejected"
    assert spy.agent_calls >= 1
    assert len(spy.append_turns) == 2


def test_confirm_continuation_bypasses_reject():
    """确认续接轮走 pending 分支、不进新规划——拒识代码不可达（回归护栏）。"""
    engine, spy, session = _make_engine(_REJECT_PLAN)
    asyncio.run(session.save("s1", SessionState(
        phase="wait_confirm", pending_step_id="s1",
        pending_plan={"steps": [{"id": "s1", "agent_id": "info", "intent": "info.weather",
                                 "slots": {}, "depends_on": []}]})))
    final = _run(engine, _req("取消", is_confirmation=True, meta=_VOICE))[-1]
    assert "取消" in final["speech"]         # 走确认分支
    assert not (final.get("ui_card") or {}).get("type") == "rejected"
    assert asyncio.run(session.load("s1")) is None


# ── P1 澄清短路（D6-3）───────────────────────────────────────────────────────

def test_clarify_shows_card_when_enabled(monkeypatch):
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    engine, spy, session = _make_engine(_CLARIFY_PLAN)
    final = _run(engine, _req("华润大厦"))[-1]
    assert final["speech"] == "您是想看详情还是导航过去？"
    assert (final.get("ui_card") or {}).get("type") == "intent_choice"
    assert len((final["ui_card"] or {}).get("options") or []) == 2
    assert asyncio.run(session.load("s1")) is None    # 零会话状态：不挂 session
    assert spy.agent_calls == 0                        # 未执行任何 Agent


def test_clarify_ignored_when_disabled():
    """CLARIFY_ENABLED 默认 off → clarify 被忽略，回空计划话术（回归保护）。"""
    engine, _, _ = _make_engine(_CLARIFY_PLAN)
    final = _run(engine, _req("华润大厦"))[-1]
    assert "抱歉" in final["speech"]
    assert not (final.get("ui_card") or {}).get("type") == "intent_choice"


def test_clarify_suppressed_on_resume(monkeypatch):
    """clarify_resume=1 的轮次丢弃 clarify（深度=1，防问个不停）→ 空计划话术。"""
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    engine, _, _ = _make_engine(_CLARIFY_PLAN)
    final = _run(engine, _req("华润大厦", meta={"clarify_resume": "1"}))[-1]
    assert "抱歉" in final["speech"]
    assert not (final.get("ui_card") or {}).get("type") == "intent_choice"
