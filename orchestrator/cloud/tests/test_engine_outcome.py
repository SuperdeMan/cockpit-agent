"""终态账本（评审 W13，2026-09-20）：engine 每条 final 出口都声明 kind，`run()` 唯一出口发
`cloud.outcome` span 并把内部键剥掉；两条 F09 家族的新出口（纯偏好陈述 / 裸对象想澄清却没卡）。

⚠ 用例替被测系统提供的前提只有「planner 怎么结束这一轮」（`build` 换成脚本）；
终态 kind 由 engine 自己按出口 / 结果集算，没有被注入。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus
from runtime.outcome import OUTCOME_KINDS

from .test_engine_confirm import _make_engine, _req, _run
from .test_obs_spans import _capture_spans

_ENGINE_DIR = Path(__file__).resolve().parents[1]


def _outcomes(spans) -> list[dict]:
    return [kw.get("attrs") or {} for _, node, kw in spans if node == "cloud.outcome"]


def _script_planner(engine, factory):
    async def build(text, working_set, ctx, **kwargs):
        return factory(text)
    engine.planner.build = build


def _voice_req(text: str, source: str = "voice_wake"):
    req = _req(text)
    req.meta = {"input_source": source}
    return req


# ── 源码级：engine / loop 声明的每个 kind 都在词表里 ────────────────────────

def test_every_declared_outcome_kind_is_in_the_vocabulary():
    declared = set()
    for name in ("engine.py", "loop.py"):
        src = (_ENGINE_DIR / name).read_text(encoding="utf-8")
        declared.update(re.findall(r'"_outcome":\s*"([a-z_]+)"', src))
        declared.update(re.findall(r'_outcome\(\s*"([a-z_]+)"', src))
    assert declared, "engine 没有声明任何终态——账本没接上"
    unknown = declared - OUTCOME_KINDS
    assert not unknown, f"engine 声明了词表外的终态 kind：{sorted(unknown)}"


# ── 执行类：按结果集算 ─────────────────────────────────────────────────────

def test_confirm_flow_records_pending_confirm_then_completed(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine, _spy, _session = _make_engine()

    first = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]
    assert first["need_confirm"] is True
    assert "_outcome" not in first, "内部键必须在 run() 出口剥掉"
    kinds = [o.get("kind") for o in _outcomes(spans)]
    assert kinds == ["pending_confirm"], kinds

    second = _run(engine, _req("确认", is_confirmation=True))[-1]
    assert not second.get("need_confirm")
    kinds = [o.get("kind") for o in _outcomes(spans)]
    assert kinds == ["pending_confirm", "completed"], kinds
    last = _outcomes(spans)[-1]
    assert last.get("category") == "progress"
    assert last.get("actions") == 0


def test_no_pending_bare_confirm_is_a_session_control_outcome(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine, _spy, _session = _make_engine()

    final = _run(engine, _req("确认"))[-1]

    assert "没有待确认" in final["speech"]
    assert [o.get("kind") for o in _outcomes(spans)] == ["no_pending"]
    assert _outcomes(spans)[0].get("category") == "session_control"


def test_no_plan_and_planner_failure_are_separate_kinds(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine, _spy, _session = _make_engine()
    _script_planner(engine, lambda text: Plan(steps=[], raw_text=text))
    _run(engine, _req("呃那个"))
    _script_planner(engine, lambda text: Plan(
        steps=[Step(id="s1", agent_id="chitchat", intent="chitchat.talk", slots={"text": text})],
        raw_text=text, technical_failure=True))
    _run(engine, _req("把那个弄一下"))

    kinds = [o.get("kind") for o in _outcomes(spans)]
    assert kinds == ["no_plan", "planner_failure"], kinds


def test_voice_not_addressed_is_recorded_and_the_final_is_silent(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine, _spy, _session = _make_engine()
    _script_planner(engine, lambda text: Plan(steps=[], raw_text=text, addressed=False))

    final = _run(engine, _voice_req("妈你到哪了"))[-1]

    assert final.get("speech") == "" and (final.get("ui_card") or {}).get("type") == "rejected"
    assert [o.get("kind") for o in _outcomes(spans)] == ["not_addressed"]


def test_partial_when_one_step_fails_in_a_multi_step_plan(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine, spy, _session = _make_engine()

    async def call_agent(endpoint, intent, slots, ctx, meta):
        if intent == "nearby.search":
            return SimpleNamespace(status=0, speech="找到 3 家。", follow_up="", actions=[],
                                   ui_card=None, data={"items": [{"name": "川菜·名店1"}]},
                                   missing_slots=[])
        return SimpleNamespace(status=3, speech="订位服务暂时不可用", follow_up="", actions=[],
                               ui_card=None, data={}, missing_slots=[])
    engine.executor._dispatcher._call = call_agent

    final = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]

    assert not final.get("need_confirm")
    assert "订位服务暂时不可用" in final["speech"] or "找到" in final["speech"]
    assert [o.get("kind") for o in _outcomes(spans)] == ["partial"]


# ── W13 F09-a：纯偏好陈述 ───────────────────────────────────────────────────

def test_pure_constraint_statement_is_acknowledged_without_the_planner(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine, spy, _session = _make_engine()
    calls = {"build": 0}

    async def build(text, working_set, ctx, **kwargs):
        calls["build"] += 1
        return Plan(steps=[], raw_text=text)
    engine.planner.build = build

    final = _run(engine, _req("我不吃辣，也不想排长队"))[-1]

    assert calls["build"] == 0, "纯偏好陈述不该进 planner"
    assert "不吃辣" in final["speech"] and "不想排队" in final["speech"]
    assert final.get("actions") == []
    assert [o.get("kind") for o in _outcomes(spans)] == ["constraint_noted"]
    focus = asyncio.run(engine.context._load_focus("sess-1", "u1"))
    assert focus is not None and focus.session_constraints == {"no_spicy": True, "no_queue": True}


def test_constraint_plus_request_still_plans(monkeypatch):
    """误伤对照：「我不吃辣，帮我找家餐厅」第二个分句不是约束 ⇒ 照常规划。"""
    engine, _spy, _session = _make_engine()
    seen = {}

    async def build(text, working_set, ctx, **kwargs):
        seen["text"] = text
        return Plan(steps=[], raw_text=text)
    engine.planner.build = build

    _run(engine, _req("我不吃辣，帮我找家餐厅"))

    assert seen.get("text") == "我不吃辣，帮我找家餐厅"


def test_constraint_waiver_is_acknowledged_as_a_waiver(monkeypatch):
    engine, _spy, _session = _make_engine()
    _run(engine, _req("我不想排队"))

    final = _run(engine, _req("排不排队都行"))[-1]

    assert "排不排队都行" in final["speech"]
    focus = asyncio.run(engine.context._load_focus("sess-1", "u1"))
    assert focus is not None and "no_queue" not in focus.session_constraints


def test_constraint_statement_with_memory_off_goes_to_the_planner(monkeypatch):
    """记忆关掉时焦点不落盘——「记下了」会是一句假话，退回正常规划。"""
    engine, _spy, _session = _make_engine()
    calls = {"build": 0}

    async def build(text, working_set, ctx, **kwargs):
        calls["build"] += 1
        return Plan(steps=[], raw_text=text)
    engine.planner.build = build
    req = _req("我不吃辣")
    req.meta = {"memory_enabled": "false"}

    _run(engine, req)

    assert calls["build"] == 1


# ── W13 F09-b：裸对象想澄清却没交出卡 ───────────────────────────────────────

def test_planner_that_wanted_to_clarify_but_failed_yields_an_honest_ask(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine, _spy, _session = _make_engine()
    _script_planner(engine, lambda text: Plan(
        steps=[Step(id="s1", agent_id="chitchat", intent="chitchat.talk", slots={"text": text})],
        raw_text=text, technical_failure=True, clarify_wanted=True))

    final = _run(engine, _req("云岚国际中心"))[-1]

    assert "云岚国际中心" in final["speech"] and "没听清要拿它做什么" in final["speech"]
    assert "没能把您的请求拆成" not in final["speech"]
    assert not final.get("issues"), "这不是技术失败，不出 retry issue"
    assert [o.get("kind") for o in _outcomes(spans)] == ["unresolved_object"]


def test_plain_technical_failure_keeps_the_retry_issue(monkeypatch):
    engine, _spy, _session = _make_engine()
    _script_planner(engine, lambda text: Plan(
        steps=[Step(id="s1", agent_id="chitchat", intent="chitchat.talk", slots={"text": text})],
        raw_text=text, technical_failure=True))

    final = _run(engine, _req("云岚国际中心"))[-1]

    assert "没能把您的请求拆成" in final["speech"]
    assert final.get("issues") and final["issues"][0]["code"] == "planner.technical_failure"


# ── W14：谈话步 + 零动作 + 声称执行 ⇒ 剥掉声称句 ────────────────────────────

def _talk_engine(speech: str, *, response_only: bool):
    engine, spy, _session = _make_engine()

    async def call_agent(endpoint, intent, slots, ctx, meta):
        return SimpleNamespace(status=0, speech=speech, follow_up="", actions=[],
                               ui_card=None, data={}, missing_slots=[])
    engine.executor._dispatcher._call = call_agent
    _script_planner(engine, lambda text: Plan(
        steps=[Step(id="s1", agent_id="chitchat", intent="chitchat.talk",
                    slots={"text": text}, response_only=response_only)],
        raw_text=text))
    return engine


def _claims(spans) -> list[dict]:
    return [kw.get("attrs") or {} for _, node, kw in spans if node == "cloud.execution_claim"]


def test_response_only_turn_claiming_an_execution_is_stripped(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine = _talk_engine(
        "好的，已为您避开此路段。已为您重新规划路线：当前位置 → A → B，全程约7.4公里。路上注意安全。",
        response_only=True)

    final = _run(engine, _req("但是，这里面来。哎妈。"))[-1]

    assert final["speech"] == "路上注意安全。"
    assert final.get("actions") == []
    assert _claims(spans) == [{"family": "done", "intercepted": "true"}]
    assert [o.get("kind") for o in _outcomes(spans)] == ["completed"]
    assert _outcomes(spans)[0].get("answer_only") == "1"


def test_response_only_turn_that_is_nothing_but_a_claim_gets_the_honest_line(monkeypatch):
    _capture_spans(monkeypatch)
    engine = _talk_engine("可以，已为您执行。", response_only=True)

    final = _run(engine, _req("可以吗"))[-1]

    assert "没有执行任何操作" in final["speech"]


def test_a_task_step_claiming_completion_is_only_observed(monkeypatch):
    """信息类能力的「已为您规划…」可能是真的：非 response_only 的步照旧只观测。"""
    spans = _capture_spans(monkeypatch)
    engine = _talk_engine("已为您规划好 3 天行程。", response_only=False)

    final = _run(engine, _req("规划三天行程"))[-1]

    assert final["speech"] == "已为您规划好 3 天行程。"
    assert _claims(spans) == [{"family": "done"}]
    assert _outcomes(spans)[0].get("answer_only") == "0"


def test_response_only_plain_answer_is_untouched(monkeypatch):
    spans = _capture_spans(monkeypatch)
    engine = _talk_engine("今天深圳小雨，记得带伞。", response_only=True)

    final = _run(engine, _req("今天要带伞吗"))[-1]

    assert final["speech"] == "今天深圳小雨，记得带伞。"
    assert _claims(spans) == []
