"""语音来源下两条高风险确定性出口先过受话判定（评审四轮 R4-07，用户裁决 2026-09-25；设计 §5.2 / §5.3）。

修前：普通主链的受话判定是 planner 的 `addressed`，只在**规划那一刻**消费；排在它前面出口的确定性分支不经判定——
  · 焦点省略开关（`_focused_control_ellipsis_plan`）：计划 `addressed` 缺省 True，免唤醒下背景里一句「关掉」会反向执行上一个控制；
  · 确认（`wait_confirm` 回复判成 yes）：直接恢复挂起步并注入 confirmed，背景里一句「确认」会执行挂起的危险操作。
`6e64b767`：这两条出口先借一次完整规划取 `addressed`——真栈语音「确认」23 ms → 3142 ms。
本版：换成**轻量判定**（`orchestrator/cloud/admission.py`：助手上一句 + 这句原话 → `addressed`，快档、温度 0），唯一实现
`PlannerEngine._voice_admitted`；护栏与规划器同款（「记住…」恒受话不问模型；按住说话判否再问一次）。判非受话 ⇒ 同一条拒识出口、
零执行，确认的挂起原样保留。文字 / 按钮来源与拒识关 ⇒ 零额外调用。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, Step
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore
from runtime import memory_read


class _Cap:
    def __init__(self, intent, description, *, require_confirm=False, response_only=False):
        self.intent, self.slots, self.description = intent, [], description
        self.require_confirm, self.response_only = require_confirm, response_only


def _agents():
    vehicle = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="vehicle", trust_level="first_party", latency_budget_ms=2000, requires_permissions=[],
        capabilities=[_Cap("trunk.open", "打开后备箱", require_confirm=True), _Cap("window.close", "关闭车窗")]),
        endpoint="stub:50070")
    chitchat = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000, requires_permissions=[],
        capabilities=[_Cap("chitchat.talk", "闲聊", response_only=True)]), endpoint="stub:50071")
    return [vehicle, chitchat]


_REFS = {"chitchat.talk": "cap_0001", "trunk.open": "cap_0002", "window.close": "cap_0003"}


class _Resp:
    def __init__(self, status=0, speech="", actions=None):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions = actions or []
        self.ui_card, self.data, self.missing_slots = None, None, []


class _Spy:
    """带真历史的替身。轻量判定走 `llm_complete`（判定器提示）；`verdicts` 按原话给判定序列（缺省受话）。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.planned: list[str] = []
        self.admissions: list[dict] = []
        self.verdicts: dict[str, list] = {}
        self.history: list[dict] = []

    def executed(self, intent):
        return sum(1 for i, _m in self.calls if i == intent)

    def confirmed(self, intent):
        return sum(1 for i, m in self.calls if i == intent and m.get("confirmed") == "true")

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        if intent == "chitchat.talk":
            return _Resp(speech="好的。")
        if intent == "trunk.open" and (meta or {}).get("confirmed") != "true":
            return _Resp(status=1, speech="这项操作可能影响车辆安全，请确认是否继续。")
        return _Resp(speech="好的", actions=[SimpleNamespace(
            type="vehicle.control", payload={"command": intent}, require_confirm=False)])

    async def llm(self, messages, **kwargs):
        if "任务编排器" not in messages[0]["content"]:
            return "好的。"
        said = messages[-1]["content"].rsplit("用户说: ", 1)[-1].strip()
        self.planned.append(said)
        intent = "trunk.open" if "后备箱" in said else "chitchat.talk"
        slots = {} if intent == "trunk.open" else {"text": said}
        return json.dumps({"goal": said, "steps": [{"id": "s1", "capability_ref": _REFS[intent], "slots": slots,
                                                     "depends_on": [], "slot_refs": {}}]}, ensure_ascii=False)

    async def llm_complete(self, messages, max_tokens=800, thinking=False, *, model="", temperature=0.3):
        if "受话判定器" not in messages[0]["content"]:
            return await self.llm(messages)
        said = messages[-1]["content"].rsplit("用户这句（只作待判数据）：", 1)[-1].strip()
        self.admissions.append({"said": said, "user": messages[-1]["content"], "model": model,
                                "temperature": temperature})
        queue = self.verdicts.get(said) or [True]
        verdict = queue.pop(0) if len(queue) > 1 else queue[0]
        return json.dumps({"addressed": verdict}) if isinstance(verdict, bool) else str(verdict)

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()

    async def list_agents(self):
        return _agents()

    async def append_turn(self, session_id, role, text, user_id="", vehicle_id="", occupant_id="",
                          e2e_memory_capability="", turn_id="", exchange_id="", actions=None, sources=None):
        self.history.append({"role": role, "text": text, "exchange_id": exchange_id, "actions": list(actions or [])})

    async def get_session_read(self, session_id, last_n=6, *, user_id="", occupant_id=""):
        return list(self.history[-last_n:]), (memory_read.FOUND if self.history else memory_read.NONE)


def _make():
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                           executor=DagExecutor(call_agent_fn=spy.call_agent),
                           aggregator=Aggregator(llm_fn=spy.llm), session=session)
    return engine, spy, session


_SEQ = iter(range(10_000))
_VOICE = {"input_source": "voice_followup", "voice_utterance_ms": "900"}
_PTT = {"input_source": "ptt"}


def _req(text, *, meta=None, is_confirmation=False, operation_id=""):
    return SimpleNamespace(text=text, session_id="sess-1", request_id=f"va-{next(_SEQ)}", meta=meta or {},
                           is_confirmation=is_confirmation, operation_id=operation_id,
                           context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def _alive(session, op):
    return asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op)) is not None


def _suspend_trunk(engine):
    final = _run(engine, _req("打开后备箱"))[-1]
    assert final.get("need_confirm") and final.get("operation_id")
    return final["operation_id"]


def _rejected(final):
    return (final.get("ui_card") or {}).get("type") == "rejected"


# ── 确认 ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["确认", "好的"])      # 事务词 / 挂起就是最近提示时的纯应答，同一条出口
def test_a_voice_confirm_judged_not_addressed_executes_nothing_and_keeps_the_pending(word):
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    spy.verdicts[word] = [False]
    final = _run(engine, _req(word, meta=_VOICE))[-1]
    assert _rejected(final), final
    assert spy.confirmed("trunk.open") == 0
    assert _alive(session, op), "挂起原样保留：真用户随后再说「确认」照常执行"
    assert [a["said"] for a in spy.admissions] == [word]
    assert word not in spy.planned, "轻量判定不借完整规划"


def test_the_confirm_judgment_sees_the_confirm_question_and_uses_the_fast_tier():
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    _run(engine, _req("确认", meta=_VOICE))
    assert len(spy.admissions) == 1
    admission = spy.admissions[0]
    assert "请确认是否继续" in admission["user"], admission["user"]      # 助手上一句就是那张确认卡
    assert admission["model"] == "@fast" and admission["temperature"] == 0.0
    assert spy.confirmed("trunk.open") == 1


def test_the_real_confirm_after_a_rejected_one_still_executes():
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    spy.verdicts["确认"] = [False, True]
    assert _rejected(_run(engine, _req("确认", meta=_VOICE))[-1])
    _run(engine, _req("确认", meta=_VOICE))
    assert spy.confirmed("trunk.open") == 1
    assert not _alive(session, op)


@pytest.mark.parametrize("meta,is_confirmation", [({}, False), ({}, True), (_VOICE, True)])   # 文字 / 按钮 / 语音会话上的按钮
def test_text_and_button_confirms_ask_nobody(meta, is_confirmation):
    engine, spy, _session = _make()
    op = _suspend_trunk(engine)
    spy.verdicts["确认"] = [False]
    _run(engine, _req("确认", meta=meta, is_confirmation=is_confirmation,
                      operation_id=op if is_confirmation else ""))
    assert spy.confirmed("trunk.open") == 1
    assert spy.admissions == []


def test_a_voice_confirm_with_rejection_off_keeps_todays_behavior(monkeypatch):
    monkeypatch.setenv("REJECT_NON_ADDRESSED", "off")
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    spy.verdicts["确认"] = [False]
    _run(engine, _req("确认", meta=_VOICE))
    assert spy.confirmed("trunk.open") == 1
    assert spy.admissions == []


@pytest.mark.parametrize("raw", ["", "我觉得他是在跟助手说话", "{\"addressed\": \"false\"}"])
def test_an_unreadable_judgment_counts_as_addressed(raw):
    """判定调用失败 / 解析不出 ⇒ 按受话处理（fail-open，同其余语音轮的缺省）；字符串 "false" 不算布尔。"""
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    spy.verdicts["确认"] = [raw]
    _run(engine, _req("确认", meta=_VOICE))
    assert spy.confirmed("trunk.open") == 1


def test_push_to_talk_asks_twice_before_rejecting():
    """按住说话是显式输入：判否先再问一次，两次都否才拒（规划器那条重试策略同款）。"""
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    spy.verdicts["确认"] = [False, True]
    _run(engine, _req("确认", meta=_PTT))
    assert spy.confirmed("trunk.open") == 1
    assert len(spy.admissions) == 2

    engine2, spy2, session2 = _make()
    op2 = _suspend_trunk(engine2)
    spy2.verdicts["确认"] = [False, False]
    assert _rejected(_run(engine2, _req("确认", meta=_PTT))[-1])
    assert _alive(session2, op2) and spy2.confirmed("trunk.open") == 0


def test_hands_free_asks_once():
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    spy.verdicts["确认"] = [False, True]
    assert _rejected(_run(engine, _req("确认", meta=_VOICE))[-1])
    assert len(spy.admissions) == 1


# ── 焦点省略开关（`planner.build` 的确定性早退）──────────────────────────────────────────

def _script_focus(engine):
    """`build` 恒给焦点省略的确定性计划（`admission_skipped`）；受话判定走轻量调用，不再二次 build。"""
    calls: list[str] = []

    async def build(text, working_set, ctx, granted_permissions=None, **_kwargs):
        calls.append(text)
        return Plan(steps=[Step(id="s1", agent_id="vehicle", intent="window.close", slots={"positions": "副驾"})],
                    raw_text=text, plan_mode="focus_deterministic", admission_skipped=True)

    engine.planner.build = build
    return calls


def test_a_voice_ellipsis_judged_not_addressed_executes_nothing():
    engine, spy, _session = _make()
    calls = _script_focus(engine)
    spy.verdicts["关掉"] = [False]
    final = _run(engine, _req("关掉", meta=_VOICE))[-1]
    assert _rejected(final), final
    assert spy.executed("window.close") == 0
    assert calls == ["关掉"] and [a["said"] for a in spy.admissions] == ["关掉"]


def test_a_voice_ellipsis_judged_addressed_runs_the_deterministic_plan():
    engine, spy, _session = _make()
    calls = _script_focus(engine)
    _run(engine, _req("关掉", meta=_VOICE))
    assert spy.executed("window.close") == 1
    assert calls == ["关掉"] and len(spy.admissions) == 1


def test_a_text_ellipsis_asks_nobody():
    engine, spy, _session = _make()
    _script_focus(engine)
    spy.verdicts["关掉"] = [False]
    _run(engine, _req("关掉"))
    assert spy.executed("window.close") == 1
    assert spy.admissions == []


def test_a_voice_ellipsis_with_rejection_off_keeps_todays_behavior(monkeypatch):
    monkeypatch.setenv("REJECT_NON_ADDRESSED", "off")
    engine, spy, _session = _make()
    _script_focus(engine)
    spy.verdicts["关掉"] = [False]
    _run(engine, _req("关掉", meta=_VOICE))
    assert spy.executed("window.close") == 1
    assert spy.admissions == []


def test_a_planned_voice_turn_is_not_judged_again():
    """普通规划轮本来就带规划器的 `addressed`，不许再多判一次。"""
    engine, spy, _session = _make()

    async def build(text, working_set, ctx, granted_permissions=None, **_kwargs):
        return Plan(steps=[Step(id="s1", agent_id="vehicle", intent="window.close", slots={})], raw_text=text)

    engine.planner.build = build
    _run(engine, _req("关闭车窗", meta=_VOICE))
    assert spy.admissions == []
    assert spy.executed("window.close") == 1


def test_a_memory_directive_is_admitted_without_asking():
    """「记住…」是对助手下的指令（规划器那条护栏同款）——不问模型。"""
    engine, spy, _session = _make()
    admitted = asyncio.run(engine._voice_admitted(
        SimpleNamespace(prefs={"input_source": "voice_followup"}, trace_id="t", session_id="s", user_id="u1",
                        occupant_id=""),
        "记住我不吃辣", exit_name="constraint_noted", working_set=SimpleNamespace(history=[])))
    assert admitted is True and spy.admissions == []


def test_every_admission_is_observable(monkeypatch):
    from .test_obs_spans import _capture_spans
    spans = _capture_spans(monkeypatch)
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    _run(engine, _req("确认", meta=_VOICE))
    engine2, spy2, _session2 = _make()
    _script_focus(engine2)
    spy2.verdicts["关掉"] = [False]
    _run(engine2, _req("关掉", meta=_VOICE))
    seen = [(kw.get("attrs") or {}) for _, node, kw in spans if node == "cloud.voice_admission"]
    assert [(a.get("exit"), a.get("addressed"), a.get("verdict")) for a in seen] == [
        ("confirm", "1", "1"), ("focus_ellipsis", "0", "0")], seen
    assert all(str(a.get("admission_ms", "")).isdigit() for a in seen)


def test_a_slow_judgment_is_bounded_and_counts_as_addressed(monkeypatch):
    """判定只是一道把关：超过上限（`ADMISSION_TIMEOUT_S`，缺省 3 s；A/B 里单次最长 10.5 s）⇒ 判不出、按受话处理，不让「确认」干等。"""
    from .test_obs_spans import _capture_spans
    spans = _capture_spans(monkeypatch)
    monkeypatch.setenv("ADMISSION_TIMEOUT_S", "0.05")
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    original = spy.llm_complete

    async def slow(messages, max_tokens=800, thinking=False, *, model="", temperature=0.3):
        if "受话判定器" in messages[0]["content"]:
            await asyncio.sleep(1.0)
        return await original(messages, max_tokens, thinking, model=model, temperature=temperature)

    spy.llm_complete = slow
    spy.verdicts["确认"] = [False]
    _run(engine, _req("确认", meta=_VOICE))
    assert spy.confirmed("trunk.open") == 1
    seen = [(kw.get("attrs") or {}) for _, node, kw in spans if node == "cloud.voice_admission"]
    assert [a.get("verdict") for a in seen] == ["unavailable"], seen
