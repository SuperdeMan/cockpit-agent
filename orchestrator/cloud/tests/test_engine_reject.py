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
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}}]})
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


def test_android_ptt_rejects_non_addressed_but_keeps_real_request():
    for response, rejected in [(_REJECT_PLAN, True), (_WEATHER_PLAN, False)]:
        engine, spy, _ = _make_engine(response)
        final = _run(engine, _req("他昨天跟我说那个项目黄了" if rejected else "今天天气怎么样",
                                 meta={"input_source": "ptt"}))[-1]
        assert ((final.get("ui_card") or {}).get("type") == "rejected") == rejected
        assert len(spy.append_turns) == (0 if rejected else 2)
        if rejected:
            assert final["speech"] == ""
            assert spy.agent_calls == 0


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
        phase="wait_confirm", owner_user_id="u1", pending_step_id="s1",
        pending_plan={"steps": [{"id": "s1", "agent_id": "info", "intent": "info.weather",
                                 "slots": {}, "depends_on": []}]})))
    final = _run(engine, _req("取消", is_confirmation=True, meta=_VOICE))[-1]
    assert "取消" in final["speech"]         # 走确认分支
    assert not (final.get("ui_card") or {}).get("type") == "rejected"
    assert asyncio.run(session.load("s1", owner_user_id="u1")) is None


# ── P1 澄清短路（D6-3）───────────────────────────────────────────────────────

def test_clarify_shows_card_when_enabled(monkeypatch):
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    engine, spy, session = _make_engine(_CLARIFY_PLAN)
    final = _run(engine, _req("华润大厦"))[-1]
    assert final["speech"] == "您是想看详情还是导航过去？"
    assert (final.get("ui_card") or {}).get("type") == "intent_choice"
    assert len((final["ui_card"] or {}).get("options") or []) == 2
    # W10（2026-09-20）：澄清**进挂起表**（phase=wait_clarify，带寻址键），下一轮的
    # 「第一个」由服务端解成选择结果；此前这里断言「零会话状态」——那正是评审 §5.3
    # 点名的缺口（系统不知道自己问过什么）。契约细节在 test_engine_clarify_pending.py。
    state = asyncio.run(session.load("s1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_clarify"
    assert final["operation_id"] == state.operation_id
    assert spy.agent_calls == 0                        # 未执行任何 Agent


def test_clarify_ignored_when_disabled(monkeypatch):
    """CLARIFY_ENABLED=off → clarify 被忽略，回空计划话术（回归保护）。

    2026-08-03：本测试原来**靠代码兜底缺省是 off** 来表达「关掉」，而部署缺省一直是 on
    （`.env.example` / compose 自 2026-07-08 起就是 `${CLARIFY_ENABLED:-on}`）。
    兜底缺省对齐到 on 之后它就红了——**红得对**：它测的是「关掉时的行为」，
    那就该把开关显式关掉，而不是赌某个缺省值恰好是 off。
    """
    monkeypatch.setenv("CLARIFY_ENABLED", "off")
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


# ── 评审二轮 R3（2026-09-22）：纯偏好短路只消费已受话的输入 ─────────────────
#
# 「我不吃辣」这样的纯偏好陈述有一条确定性出口（W13 F09-a：登记 + 致谢、零 LLM）。它排在
# planner 之前，于是语音来源的这句话**跳过了受话判定**：乘客对别人说的一句「我不吃辣」被登记
# 进焦点、被应答、还落进普通历史。语音来源先走既有的受话判定（planner `addressed`），
# 判非受话 ⇒ 与其他非受话轮同一条拒识出口；文字 / 按钮来源没有受话问题，照旧零 LLM。

_ADDRESSED_EMPTY_PLAN = json.dumps({"addressed": True, "steps": []})


def _focus_constraints(engine, session_id="s1"):
    focus = asyncio.run(engine.context._load_focus(session_id, "u1"))
    return dict(getattr(focus, "session_constraints", None) or {}) if focus else {}


def test_voice_pure_preference_judged_not_addressed_is_rejected_and_not_recorded():
    engine, spy, _ = _make_engine(_REJECT_PLAN)
    final = _run(engine, _req("我不吃辣", meta=_VOICE))[-1]
    assert final["ui_card"] == {"type": "rejected", "reason": "not_addressed"}
    assert final["speech"] == ""
    assert spy.append_turns == []                       # 不落普通历史
    assert _focus_constraints(engine) == {}             # 不登记会话约束
    assert spy.agent_calls == 0


def test_voice_pure_preference_judged_addressed_takes_the_deterministic_ack():
    engine, spy, _ = _make_engine(_ADDRESSED_EMPTY_PLAN)
    final = _run(engine, _req("我不吃辣", meta=_VOICE))[-1]
    assert "不吃辣" in final["speech"]
    assert final.get("actions") == []
    assert _focus_constraints(engine) == {"no_spicy": True}
    assert len(spy.append_turns) == 2
    assert spy.agent_calls == 0


def test_voice_pure_preference_takes_the_ack_even_when_the_planner_only_fails_technically():
    """保留短路诞生的理由：受话了但 planner 交不出合法计划（技术失败 / 规划成一次搜索）
    ⇒ 仍走确定性致谢，不是一句报错。"""
    engine, spy, _ = _make_engine("this is not json at all")
    final = _run(engine, _req("我不吃辣", meta={"input_source": "ptt"}))[-1]
    assert "不吃辣" in final["speech"]
    assert _focus_constraints(engine) == {"no_spicy": True}


def test_text_pure_preference_keeps_the_zero_llm_shortcut():
    engine, spy, _ = _make_engine(_REJECT_PLAN)      # 即使 planner 会判非受话，文字源也不问它
    planned = {"n": 0}
    orig = spy.llm

    async def llm(messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            planned["n"] += 1
        return await orig(messages, **kwargs)
    engine.planner._llm = llm
    final = _run(engine, _req("我不吃辣", meta={}))[-1]
    assert "不吃辣" in final["speech"]
    assert planned["n"] == 0
    assert _focus_constraints(engine) == {"no_spicy": True}


def test_voice_pure_preference_with_reject_disabled_keeps_the_shortcut(monkeypatch):
    monkeypatch.setenv("REJECT_NON_ADDRESSED", "off")
    engine, spy, _ = _make_engine(_REJECT_PLAN)
    final = _run(engine, _req("我不吃辣", meta=_VOICE))[-1]
    assert "不吃辣" in final["speech"]
    assert _focus_constraints(engine) == {"no_spicy": True}
