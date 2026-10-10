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
        user = messages[-1]["content"]
        # 两份判据：挂起续接 / 唤醒轮用的轻量判定，与续问窗那一份（2026-10-10）
        judge = "continuation" if "麦克风收到的这句（只作待判数据）：" in user else "light"
        said = user.rsplit("（只作待判数据）：", 1)[-1].strip()
        self.admissions.append({"said": said, "user": user, "model": model, "judge": judge,
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
_WAKE = {"input_source": "voice_wake", "voice_utterance_ms": "900"}
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
    final = _run(engine, _req("关掉", meta=_WAKE))[-1]
    assert _rejected(final), final
    assert spy.executed("window.close") == 0
    assert calls == ["关掉"] and [a["said"] for a in spy.admissions] == ["关掉"]


def test_a_voice_ellipsis_judged_addressed_runs_the_deterministic_plan():
    engine, spy, _session = _make()
    calls = _script_focus(engine)
    _run(engine, _req("关掉", meta=_WAKE))
    assert spy.executed("window.close") == 1
    assert calls == ["关掉"] and len(spy.admissions) == 1


def test_a_followup_ellipsis_is_gated_by_the_continuation_judgment_alone():
    """续问窗里的「关掉」：续问窗那条判定判否（两次）⇒ 拒；判受话 ⇒ 执行，不再走第二道轻量判定。"""
    engine, spy, _session = _make()
    _script_focus(engine)
    spy.verdicts["关掉"] = [False]
    assert _rejected(_run(engine, _req("关掉", meta=_VOICE))[-1])
    assert spy.executed("window.close") == 0
    assert [(a["said"], a["judge"]) for a in spy.admissions] == [("关掉", "continuation")] * 2

    engine2, spy2, _session2 = _make()
    _script_focus(engine2)
    _run(engine2, _req("关掉", meta=_VOICE))
    assert spy2.executed("window.close") == 1
    assert [a["judge"] for a in spy2.admissions] == ["continuation"]


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
    """唤醒词那一轮的普通规划本来就带规划器的 `addressed`，不许再多判一次；续问窗来的只多一次续问窗判定，不叠轻量判定。"""
    engine, spy, _session = _make()

    async def build(text, working_set, ctx, granted_permissions=None, **_kwargs):
        return Plan(steps=[Step(id="s1", agent_id="vehicle", intent="window.close", slots={})], raw_text=text)

    engine.planner.build = build
    _run(engine, _req("关闭车窗", meta=_WAKE))
    assert spy.admissions == []
    assert spy.executed("window.close") == 1

    engine2, spy2, _session2 = _make()
    engine2.planner.build = build
    _run(engine2, _req("关闭车窗", meta=_VOICE))
    assert [a["judge"] for a in spy2.admissions] == ["continuation"]
    assert spy2.executed("window.close") == 1


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
    _run(engine2, _req("关掉", meta=_WAKE))
    engine3, spy3, _session3 = _make()
    _script_focus(engine3)
    spy3.verdicts["关掉"] = [False]
    _run(engine3, _req("关掉", meta=_VOICE))
    seen = [(kw.get("attrs") or {}) for _, node, kw in spans if node == "cloud.voice_admission"]
    assert [(a.get("exit"), a.get("addressed"), a.get("verdict")) for a in seen] == [
        ("confirm", "1", "1"), ("focus_ellipsis", "0", "0"), ("continuation", "0", "0")], seen
    assert seen[-1].get("votes") == "0,0"
    assert all(str(a.get("admission_ms", "")).isdigit() for a in seen)
    rejected = [(kw.get("attrs") or {}) for _, node, kw in spans if node == "rejected"]
    assert [a.get("judge") for a in rejected] == [None, "continuation"], rejected


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


# ── 澄清选择 / 补槽（评审四轮 R4-07 第二步，设计 §5.4）──────────────────────────────────────
#
# 两条都是「一句短回复就执行动作」：背景里一句「第一个」会执行澄清卡的第一项，补槽窗口里别人说的一句话会被当成槽值去执行。
# 取消刻意不接：它只丢挂起不执行，误拒一句真「取消」反而把危险操作的挂起留下来。

from orchestrator.cloud.models import SessionState  # noqa: E402

_CLOSE_STEP = {"id": "s1", "agent_id": "vehicle", "intent": "window.close", "slots": {},
               "depends_on": [], "slot_refs": {}}


def _seed_slot(session):
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-slot", pending_step_id="s1",
        missing_slots=["positions"], completed_results={},
        pending_plan={"steps": [dict(_CLOSE_STEP)], "raw_text": "关闭车窗", "goal": "关闭车窗",
                      "safety_origin_text": "关闭车窗"})))


def _seed_clarify(session):
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_clarify", owner_user_id="u1", operation_id="op-clar", pending_step_id="",
        missing_slots=[], completed_results={},
        pending_plan={"steps": [], "raw_text": "车窗", "goal": "车窗", "safety_origin_text": "车窗"},
        clarify={"question": "车窗要打开还是关上？",
                 "options": [{"label": "关上", "send_text": "关闭车窗", "step": dict(_CLOSE_STEP)},
                             {"label": "打开", "send_text": "打开车窗"}]})))


def _pending_ids(session):
    return [s.operation_id for s in asyncio.run(session.load_all("sess-1", owner_user_id="u1"))]


def test_a_voice_slot_answer_judged_not_addressed_executes_nothing_and_keeps_the_question():
    engine, spy, session = _make()
    _seed_slot(session)
    spy.verdicts["副驾"] = [False]
    final = _run(engine, _req("副驾", meta=_VOICE))[-1]
    assert _rejected(final), final
    assert spy.executed("window.close") == 0
    assert _pending_ids(session) == ["op-slot"], "补槽挂起原样保留，也不算一次没接上的重问"
    state = asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id="op-slot"))
    assert int(getattr(state, "slot_retry", 0) or 0) == 0


def test_a_voice_slot_answer_judged_addressed_fills_and_executes():
    engine, spy, session = _make()
    _seed_slot(session)
    _run(engine, _req("副驾", meta=_VOICE))
    assert spy.executed("window.close") == 1
    assert [a["said"] for a in spy.admissions] == ["副驾"]


def test_a_text_slot_answer_asks_nobody():
    engine, spy, session = _make()
    _seed_slot(session)
    spy.verdicts["副驾"] = [False]
    _run(engine, _req("副驾"))
    assert spy.executed("window.close") == 1
    assert spy.admissions == []


def test_a_voice_clarify_choice_judged_not_addressed_executes_nothing_and_keeps_the_card():
    engine, spy, session = _make()
    _seed_clarify(session)
    spy.verdicts["第一个"] = [False]
    final = _run(engine, _req("第一个", meta=_VOICE))[-1]
    assert _rejected(final), final
    assert spy.executed("window.close") == 0
    assert _pending_ids(session) == ["op-clar"]


def test_a_voice_clarify_choice_judged_addressed_runs_the_option():
    engine, spy, session = _make()
    _seed_clarify(session)
    _run(engine, _req("第一个", meta=_VOICE))
    assert spy.executed("window.close") == 1
    assert [a["said"] for a in spy.admissions] == ["第一个"]


def test_a_text_or_tapped_clarify_choice_asks_nobody():
    engine, spy, session = _make()
    _seed_clarify(session)
    spy.verdicts["关闭车窗"] = [False]
    _run(engine, _req("关闭车窗", operation_id="op-clar", meta={"clarify_resume": "1"}))
    assert spy.executed("window.close") == 1
    assert spy.admissions == []


# ── 续问窗（唤醒后连续对话）的受话判定（2026-10-10，设计 docs/design/2026-10-10-handsfree-followup-rejection.md §4.1）──
#
# 续问窗里没喊唤醒词就收进来的话（`voice_followup` / `voice_bargein`）与规划并行判一次：判否再问一次、两次都否就拒，
# 判否先回来时不等规划；判不出回落规划器的 `addressed`。唤醒词那一轮、按住说话、文字不受影响；挂起续接仍用轻量判定。

_BARGEIN = {"input_source": "voice_bargein", "voice_utterance_ms": "900"}


def _planner_says(engine, *, addressed=True, intent="window.close"):
    async def build(text, working_set, ctx, granted_permissions=None, **_kwargs):
        steps = [Step(id="s1", agent_id="vehicle", intent=intent, slots={})] if addressed else []
        return Plan(steps=steps, raw_text=text, addressed=addressed)
    engine.planner.build = build


def test_a_followup_utterance_judged_not_addressed_twice_runs_nothing_and_leaves_no_history():
    engine, spy, _session = _make()
    _planner_says(engine)
    spy.verdicts["我们晚上吃火锅怎么样"] = [False]
    final = _run(engine, _req("我们晚上吃火锅怎么样", meta=_VOICE))[-1]
    assert _rejected(final), final
    assert spy.calls == [] and spy.history == []
    assert [(a["judge"], a["model"], a["temperature"]) for a in spy.admissions] == [("continuation", "@fast", 0.0)] * 2


def test_a_single_flip_is_settled_by_the_second_ask():
    engine, spy, _session = _make()
    _planner_says(engine)
    spy.verdicts["把车窗关上"] = [False, True]
    final = _run(engine, _req("把车窗关上", meta=_VOICE))[-1]
    assert not _rejected(final)
    assert spy.executed("window.close") == 1 and len(spy.admissions) == 2


@pytest.mark.parametrize("raw", ["", "这句话我拿不准", "{\"addressed\": \"false\"}"])
def test_an_unreadable_continuation_judgment_falls_back_to_the_planner(raw):
    for planner_addressed in (True, False):
        engine, spy, _session = _make()
        _planner_says(engine, addressed=planner_addressed)
        spy.verdicts["把车窗关上"] = [raw]
        final = _run(engine, _req("把车窗关上", meta=_VOICE))[-1]
        assert _rejected(final) is (not planner_addressed)
        assert spy.executed("window.close") == (1 if planner_addressed else 0)


def test_an_early_rejection_does_not_wait_for_the_planner():
    """判否先回来 ⇒ 取消在飞的规划，立刻拒识（规划可能要好几秒，用户这边是一段静默）。"""
    engine, spy, _session = _make()
    cancelled = []

    async def build(text, working_set, ctx, granted_permissions=None, **_kwargs):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(text)
            raise
        raise AssertionError("规划不该跑完")

    engine.planner.build = build
    spy.verdicts["哈哈哈对对对"] = [False]

    async def go():
        return await asyncio.wait_for(_collect(engine, _req("哈哈哈对对对", meta=_VOICE)), timeout=5)

    final = asyncio.run(go())[-1]
    assert _rejected(final) and cancelled == ["哈哈哈对对对"]


async def _collect(engine, req):
    return [e async for e in engine.run(req)]


def test_bargein_is_a_continuation_and_wake_or_push_to_talk_is_not():
    for meta, judged in ((_BARGEIN, True), (_WAKE, False), (_PTT, False), ({}, False)):
        engine, spy, _session = _make()
        _planner_says(engine)
        spy.verdicts["把车窗关上"] = [False]
        final = _run(engine, _req("把车窗关上", meta=meta))[-1]
        assert _rejected(final) is judged, meta
        assert ([a["judge"] for a in spy.admissions] == ["continuation"] * 2) is judged


def test_the_continuation_judgment_reads_the_name_and_the_previous_answer():
    engine, spy, _session = _make()
    _run(engine, _req("讲个笑话"))                       # 文字轮：助手上一句是「好的。」
    _planner_says(engine)
    _run(engine, _req("把车窗关上", meta={**_VOICE, "assistant_name": "小航"}))
    user = spy.admissions[-1]["user"]
    assert "你的名字：小航" in user and "你上一句：好的。" in user, user


def test_rejection_off_or_a_memory_directive_asks_nobody(monkeypatch):
    engine, spy, _session = _make()
    _planner_says(engine)
    asyncio.run(engine._continuation_admitted(
        SimpleNamespace(prefs={"input_source": "voice_followup"}, trace_id="t", session_id="s", user_id="u1",
                        occupant_id=""),
        "记住我不吃辣", SimpleNamespace(history=[])))
    assert spy.admissions == []
    monkeypatch.setenv("REJECT_NON_ADDRESSED", "off")
    spy.verdicts["把车窗关上"] = [False]
    _run(engine, _req("把车窗关上", meta=_VOICE))
    assert spy.admissions == [] and spy.executed("window.close") == 1


def test_a_pending_answer_in_the_followup_window_keeps_the_light_judgment():
    """助手刚问了问题（补槽 / 确认 / 澄清），这一句大概率就是答案：仍走原来那份宽松判定，不走续问窗那份。"""
    engine, spy, session = _make()
    _seed_slot(session)
    _run(engine, _req("副驾", meta=_VOICE))
    assert spy.executed("window.close") == 1
    assert [a["judge"] for a in spy.admissions] == ["light"]


def test_a_late_rejection_still_rejects_after_the_plan_is_ready():
    """规划先好（确定性计划零 LLM）、判定后到且判否 ⇒ 仍拒，计划一步都不执行。"""
    engine, spy, _session = _make()
    _planner_says(engine)
    original = spy.llm_complete

    async def slow(messages, max_tokens=800, thinking=False, *, model="", temperature=0.3):
        if "受话判定器" in messages[0]["content"]:
            await asyncio.sleep(0.05)
        return await original(messages, max_tokens, thinking, model=model, temperature=temperature)

    spy.llm_complete = slow
    spy.verdicts["把车窗关上"] = [False]
    assert _rejected(_run(engine, _req("把车窗关上", meta=_VOICE))[-1])
    assert spy.executed("window.close") == 0
