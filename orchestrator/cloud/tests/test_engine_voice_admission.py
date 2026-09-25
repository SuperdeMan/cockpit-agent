"""语音来源下两条高风险确定性出口先过受话判定（评审四轮 R4-07 第一步，用户裁决 2026-09-25；设计 §5.2）。

修前：普通主链的受话判定是 planner 的 `addressed`，只在**规划那一刻**消费；排在它前面出口的确定性分支不经判定——
  · 焦点省略开关（`_focused_control_ellipsis_plan`）：计划 `addressed` 缺省 True，免唤醒下背景里一句「关掉」会反向执行上一个控制；
  · 确认（`wait_confirm` 回复判成 yes）：直接恢复挂起步并注入 confirmed，背景里一句「确认」会执行挂起的危险操作。
本步：语音来源 + 拒识开时，这两条出口先跑一次 planner 只取 `addressed`（与规划轮、纯偏好陈述同一条判定，唯一实现
`PlannerEngine._voice_admitted`）；判非受话 ⇒ 同一条拒识出口、零执行，确认的挂起原样保留。文字 / 按钮来源与拒识关 ⇒ 零额外调用。
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
    """带真历史的替身；`not_addressed` 里的原话，规划器判「不是对助手说的」。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.planned: list[str] = []
        self.not_addressed: set[str] = set()
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
        if said in self.not_addressed:
            return json.dumps({"addressed": False, "steps": []})
        intent = "trunk.open" if "后备箱" in said else "chitchat.talk"
        slots = {} if intent == "trunk.open" else {"text": said}
        return json.dumps({"goal": said, "steps": [{"id": "s1", "capability_ref": _REFS[intent], "slots": slots,
                                                     "depends_on": [], "slot_refs": {}}]}, ensure_ascii=False)

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
    spy.not_addressed.add(word)
    final = _run(engine, _req(word, meta=_VOICE))[-1]
    assert _rejected(final), final
    assert spy.confirmed("trunk.open") == 0
    assert _alive(session, op), "挂起原样保留：真用户随后再说「确认」照常执行"
    assert word in spy.planned, "判非受话之前确实问过规划器"


def test_the_real_confirm_after_a_rejected_one_still_executes():
    engine, spy, session = _make()
    op = _suspend_trunk(engine)
    spy.not_addressed.add("确认")
    assert _rejected(_run(engine, _req("确认", meta=_VOICE))[-1])
    spy.not_addressed.clear()
    _run(engine, _req("确认", meta=_VOICE))
    assert spy.confirmed("trunk.open") == 1
    assert not _alive(session, op)


def test_a_voice_confirm_judged_addressed_executes():
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    _run(engine, _req("确认", meta=_VOICE))
    assert spy.confirmed("trunk.open") == 1
    assert "确认" in spy.planned


@pytest.mark.parametrize("meta,is_confirmation", [({}, False), ({}, True)])   # 文字「确认」/ 按钮确认
def test_text_and_button_confirms_ask_nobody(meta, is_confirmation):
    engine, spy, _session = _make()
    op = _suspend_trunk(engine)
    spy.not_addressed.add("确认")                         # 即使规划器会判非受话，文字 / 按钮来源也不问它
    _run(engine, _req("确认", meta=meta, is_confirmation=is_confirmation,
                      operation_id=op if is_confirmation else ""))
    assert spy.confirmed("trunk.open") == 1
    assert "确认" not in spy.planned


def test_a_voice_confirm_with_rejection_off_keeps_todays_behavior(monkeypatch):
    monkeypatch.setenv("REJECT_NON_ADDRESSED", "off")
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    spy.not_addressed.add("确认")
    _run(engine, _req("确认", meta=_VOICE))
    assert spy.confirmed("trunk.open") == 1
    assert "确认" not in spy.planned


# ── 焦点省略开关（`planner.build` 的确定性早退）──────────────────────────────────────────

def _script_focus(engine, *, addressed: bool):
    """第一次 build = 焦点省略的确定性计划（`admission_skipped`）；`focus_shortcut=False` 那次 = 受话判定。"""
    calls: list[bool] = []

    async def build(text, working_set, ctx, granted_permissions=None, focus_shortcut=True):
        calls.append(focus_shortcut)
        if focus_shortcut:
            return Plan(steps=[Step(id="s1", agent_id="vehicle", intent="window.close",
                                    slots={"positions": "副驾"})],
                        raw_text=text, plan_mode="focus_deterministic", admission_skipped=True)
        return Plan(steps=[], raw_text=text, addressed=addressed)

    engine.planner.build = build
    return calls


def test_a_voice_ellipsis_judged_not_addressed_executes_nothing():
    engine, spy, _session = _make()
    calls = _script_focus(engine, addressed=False)
    final = _run(engine, _req("关掉", meta=_VOICE))[-1]
    assert _rejected(final), final
    assert spy.executed("window.close") == 0
    assert calls == [True, False]


def test_a_voice_ellipsis_judged_addressed_runs_the_deterministic_plan():
    engine, spy, _session = _make()
    calls = _script_focus(engine, addressed=True)
    _run(engine, _req("关掉", meta=_VOICE))
    assert spy.executed("window.close") == 1
    assert calls == [True, False]


def test_a_text_ellipsis_asks_nobody():
    engine, spy, _session = _make()
    calls = _script_focus(engine, addressed=False)
    _run(engine, _req("关掉"))
    assert spy.executed("window.close") == 1
    assert calls == [True]


def test_a_voice_ellipsis_with_rejection_off_keeps_todays_behavior(monkeypatch):
    monkeypatch.setenv("REJECT_NON_ADDRESSED", "off")
    engine, spy, _session = _make()
    calls = _script_focus(engine, addressed=False)
    _run(engine, _req("关掉", meta=_VOICE))
    assert spy.executed("window.close") == 1
    assert calls == [True]


def test_a_planned_voice_turn_is_not_judged_twice():
    """普通规划轮本来就带 `addressed`，不许再多跑一次。"""
    engine, spy, _session = _make()
    calls: list[bool] = []

    async def build(text, working_set, ctx, granted_permissions=None, focus_shortcut=True):
        calls.append(focus_shortcut)
        return Plan(steps=[Step(id="s1", agent_id="vehicle", intent="window.close", slots={})], raw_text=text)

    engine.planner.build = build
    _run(engine, _req("关闭车窗", meta=_VOICE))
    assert calls == [True]


def test_a_button_confirm_on_a_voice_session_asks_nobody():
    """全局确认条是一次点击（`is_confirmation`）——哪怕这一轮带着语音来源的 meta 也不问。"""
    engine, spy, _session = _make()
    op = _suspend_trunk(engine)
    spy.not_addressed.add("确认")
    _run(engine, _req("确认", meta=_VOICE, is_confirmation=True, operation_id=op))
    assert spy.confirmed("trunk.open") == 1
    assert "确认" not in spy.planned


def test_every_admission_is_observable(monkeypatch):
    from .test_obs_spans import _capture_spans
    spans = _capture_spans(monkeypatch)
    engine, spy, _session = _make()
    _suspend_trunk(engine)
    _run(engine, _req("确认", meta=_VOICE))
    engine2, _spy2, _session2 = _make()
    _script_focus(engine2, addressed=False)
    _run(engine2, _req("关掉", meta=_VOICE))
    seen = [(kw.get("attrs") or {}) for _, node, kw in spans if node == "cloud.voice_admission"]
    assert [(a.get("exit"), a.get("addressed")) for a in seen] == [("confirm", "1"), ("focus_ellipsis", "0")], seen
