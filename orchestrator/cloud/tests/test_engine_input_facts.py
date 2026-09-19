"""规划轮提前结束时，输入侧事实照样登记进焦点（QA T47 收口，2026-09-19）。

C1-B 的判据是「登记挂在输入上，不挂在路由上」，可登记住在 `extract_focus` 里，而它只在
`update_focus` 被调到时才跑——技术失败终态（F09）、授权缺失、澄清、取消未命中、「没听清」
这几条出口都在它之前 `return`。真栈（release `0d414816`）：「检查过了，机油灯已经灭了，恢复正常了」
那一轮 planner 技术失败 ⇒ 解除陈述没跑到焦点 ⇒ 下一句仍答「未解除的机油灯」。
反方向同样成立：告警句若恰好落在这几条出口上，会话里就**不知道**灯亮过。

⚠ 这些用例替被测系统只提供了一个前提——planner 怎么结束这一轮（`build` 被换成脚本）；
「输入侧事实该不该登记」这条被测判据没有被注入，仍由 `extract_focus` 自己算。
"""
from __future__ import annotations

import asyncio

from orchestrator.cloud.context import _render_focus
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus

from .test_engine_confirm import _make_engine, _req, _run


def _seed_alert(engine, session_id="sess-1", user_id="u1"):
    """先让会话里挂上一条 critical（走正常抽取路径，不借被测机制）。"""
    plan = Plan(steps=[Step(id="s0", agent_id="manual-rag", intent="manual.query", slots={})],
                raw_text="红色机油灯亮了怎么办")
    asyncio.run(engine.context.update_focus(
        session_id, plan, [StepResult(step_id="s0", status=StepStatus.OK, data={})],
        user_id=user_id))
    focus = asyncio.run(engine.context._load_focus(session_id, user_id))
    assert focus.safety_alert.get("level") == "critical"


def _script_planner(engine, factory):
    async def build(text, working_set, ctx, **kwargs):
        return factory(text)
    engine.planner.build = build


def test_resolution_on_a_technical_failure_turn_still_clears_the_alert():
    engine, _spy, _session = _make_engine()
    _seed_alert(engine)
    _script_planner(engine, lambda text: Plan(
        steps=[Step(id="s1", agent_id="chitchat", intent="chitchat.talk", slots={"text": text})],
        raw_text=text, technical_failure=True))

    events = _run(engine, _req("检查过了，机油灯已经灭了，恢复正常了"))

    assert "没能把您的请求拆成" in (events[-1].get("speech") or ""), "前提：这一轮走的是技术失败终态"
    focus = asyncio.run(engine.context._load_focus("sess-1", "u1"))
    assert not (focus and focus.safety_alert), "技术失败出口把解除陈述吞掉了——焦点里还挂着机油灯"
    assert "安全告警" not in _render_focus(focus)


def test_alert_on_a_no_plan_turn_is_still_registered():
    """反方向：告警句落到「没听清」出口，会话里也必须知道灯亮过。"""
    engine, _spy, _session = _make_engine()
    _script_planner(engine, lambda text: Plan(steps=[], raw_text=text))

    events = _run(engine, _req("水温灯亮了"))

    assert "没听清" in (events[-1].get("speech") or ""), "前提：这一轮走的是 no_plan 出口"
    focus = asyncio.run(engine.context._load_focus("sess-1", "u1"))
    assert focus is not None and focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "水温灯"


def test_resolution_on_a_clarify_turn_still_clears_the_alert(monkeypatch):
    engine, _spy, _session = _make_engine()
    monkeypatch.setenv("CLARIFY_ENABLED", "1")
    _seed_alert(engine)
    _script_planner(engine, lambda text: Plan(
        steps=[], raw_text=text,
        clarify={"question": "你想让我做什么？", "options": []}))

    events = _run(engine, _req("机油灯灭了"))

    final = events[-1]
    assert final.get("ui_card", {}).get("type") == "intent_choice" or "没听清" in (final.get("speech") or ""), \
        "前提：这一轮在规划后提前结束（澄清或没听清）"
    focus = asyncio.run(engine.context._load_focus("sess-1", "u1"))
    assert not (focus and focus.safety_alert)


def test_an_ordinary_early_exit_leaves_the_focus_untouched():
    """误伤对照：没有任何输入侧事实的「没听清」轮，不许改写焦点（旧告警照旧在）。"""
    engine, _spy, _session = _make_engine()
    _seed_alert(engine)
    _script_planner(engine, lambda text: Plan(steps=[], raw_text=text))

    _run(engine, _req("呃那个"))

    focus = asyncio.run(engine.context._load_focus("sess-1", "u1"))
    assert focus.safety_alert.get("level") == "critical"
    assert focus.safety_alert.get("signal") == "机油灯"
