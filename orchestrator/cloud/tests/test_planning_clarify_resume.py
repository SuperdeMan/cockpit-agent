"""澄清卡的选项被点了之后，不许回答「我没听清你要做什么」（QA I-031）。

真栈现象：问「没开定位为什么还有距离」→ 系统出澄清卡 → 用户点「解释定位原理」
→ 按钮把整句回发（带 `clarify_resume=1`）→ 后端答「抱歉，我没听清您想让我做什么」。
**用户被自己刚点的按钮顶回了起点。**

根因不在 send_text 写得好不好：resume 轮模型**又出了一张澄清卡**，而 engine 在这一轮
不许连问两次、会把它丢掉——丢完就只剩空计划，于是落到「没听清」那句兜底话术。
判据是**形态**不是措辞：这一轮是用户在回应系统自己提的问题，空计划唯一说得通的
出口是交给兜底 Agent 答一句。

⚠ 覆盖面写清楚：本修法挂在 planner 侧。engine 那句「没听清」在别的路径上仍可达
（例如 `_fallback` 语义 top-1 分数不足），那是**正确的诚实降级**，不在本卡范围内。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder

from tests.test_planning import MockAgent

# resume 轮模型又出一张澄清卡——这是真实形态。纯空 steps 那种早被 `_no_action` 接住。
_CLARIFY_AGAIN = ('{"addressed":true,"steps":[],"clarify":{"question":"你是想问哪一种？",'
                  '"options":[{"label":"定位原理","send_text":"解释定位原理"},'
                  '{"label":"距离怎么算","send_text":"解释距离是怎么算出来的"}]}}')
_EMPTY = '{"addressed":true,"steps":[]}'


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"],
                      response_only=("chitchat.talk",)),
            MockAgent("navigation", ["navigation.navigate_to"])]


def _build(prefs: dict, text: str = "解释定位原理", reply: str = _CLARIFY_AGAIN):
    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    ctx = PlanContext(session_id="test", prefs=dict(prefs))
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()), ctx))


def test_clarify_resume_empty_plan_falls_back_to_talk():
    plan = _build({"clarify_resume": "1"})
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], (
        "点了系统自己给的选项，却仍然是空计划 —— engine 会答「没听清」")
    assert plan.plan_mode.endswith("_clarify_resume_talk"), plan.plan_mode


def test_clarify_resume_bare_empty_plan_also_answers():
    """第二种形态：模型什么都不给。`_no_action` 会先接住，两条路的用户可见行为相同，
    所以这里断言**结果对**，不强求走哪条分支。"""
    plan = _build({"clarify_resume": "1"}, reply=_EMPTY)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"]


def test_normal_turn_clarify_is_not_swallowed():
    """反向对照：普通轮出澄清卡是**正确行为**（路由歧义就该问）。

    把它也改写成 chitchat 等于把澄清能力关掉——那是方向相反、同样严重的错。
    """
    plan = _build({})
    assert not plan.steps and plan.clarify is not None, "普通轮的澄清卡被吃掉了"
    assert not (plan.plan_mode or "").endswith("_clarify_resume_talk")


# ── W10（2026-09-20）：选项预解析与「同一个问题」判据 ─────────────────────────

def test_parse_clarify_keeps_a_well_formed_ref_and_slots_only():
    from orchestrator.cloud.planning import PlanBuilder
    parsed = PlanBuilder._parse_clarify({
        "question": "怎么处理？",
        "options": [
            {"label": "看天气", "send_text": "查天气", "capability_ref": "cap_0001",
             "slots": {"city": "深圳", "bad": {"x": 1}, "flag": True}},
            {"label": "导航", "send_text": "导航过去", "capability_ref": 7},
        ]})
    assert parsed["options"][0]["capability_ref"] == "cap_0001"
    assert parsed["options"][0]["slots"] == {"city": "深圳"}         # 非标量值 / 布尔丢掉
    assert "capability_ref" not in parsed["options"][1]


def test_resolve_clarify_options_attaches_a_validated_step_and_strips_the_raw_keys():
    from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog
    catalog = _assemble_capability_catalog(_agents() + [MockAgent("info", ["info.weather"])])
    ref = next(r for r, pair in catalog.ref_to_pair.items() if pair[1] == "info.weather")
    clarify = {"question": "怎么处理？", "options": [
        {"label": "看天气", "send_text": "查天气", "capability_ref": ref, "slots": {"city": "深圳"}},
        {"label": "别的", "send_text": "别的", "capability_ref": "cap_9999"},
        {"label": "裸", "send_text": "裸"},
    ]}
    out = PlanBuilder.resolve_clarify_options(clarify, catalog)
    first, unknown, bare = out["options"]
    assert first["step"]["intent"] == "info.weather" and first["step"]["slots"] == {"city": "深圳"}
    assert first["step"]["agent_id"] and first["step"]["endpoint"]
    assert "capability_ref" not in first and "slots" not in first
    assert "step" not in unknown and "capability_ref" not in unknown   # 不在本请求映射里 ⇒ 不猜
    assert "step" not in bare
    assert out["question"] == "怎么处理？"


def test_clarify_is_progress_judges_same_question_by_text_or_by_option_labels():
    from orchestrator.cloud.planning import clarify_is_progress
    probe = {"question": "你希望我怎么处理华润大厦？", "labels": ["看天气", "导航过去"]}
    same_text = {"question": "你希望我怎么处理华润大厦", "options": [
        {"label": "查询", "send_text": "x"}, {"label": "别的", "send_text": "y"}]}
    same_labels = {"question": "要对华润大厦做什么", "options": [
        {"label": "导航过去", "send_text": "x"}, {"label": "看天气", "send_text": "y"}]}
    other = {"question": "要今天的还是明天的？", "options": [
        {"label": "今天", "send_text": "x"}, {"label": "明天", "send_text": "y"}]}
    assert clarify_is_progress(probe, same_text) is False
    assert clarify_is_progress(probe, same_labels) is False
    assert clarify_is_progress(probe, other) is True
    assert clarify_is_progress({}, same_text) is True       # 没有上一问 ⇒ 交回调用方

