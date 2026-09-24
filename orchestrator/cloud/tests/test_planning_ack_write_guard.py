"""纯应答不许长出写步（追加批 I，2026-09-24；设计 §12）。

`a4bb73bf` RS21 第 3 趟：「可以，已为您执行」**第一轮**就被规划成 `reminder.cancel {index:"2"}`（没经过任何重试），碰巧没有第 2 条提醒。
历史上同一句还被规划成 `reminder.cancel {title:"第二个"}`、`research.run`。批 G 的 G-3 只盖「被重试催出来的」那一版，这一次它不在场。
这句话 = 一个应答词 + 一句助手口吻的执行声称，没有任何请求；上一轮助手也没有提议。判据：原话是纯应答（`runtime.affirmation`）+ 计划里有
写步（端侧写 / 需确认 / 声明 `effect: write`）+ 最近一条助手话不是提问 ⇒ 整份计划换兜底谈话。「好的」接在助手的提议之后照常执行。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog

from tests.test_planning import MockAgent


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    """范例检索缺省 hybrid 会打 Embed（网络）；单测离线确定。"""
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _write(agent, *intents):
    for cap in agent.manifest.capabilities:
        if cap.intent in intents:
            cap.effect = "write"
    return agent


#: 端侧能力在 Registry 里的描述（`orchestrator/edge/capabilities.py::_describe` 生成的就是这种）——评审四轮 R4-01 起
#: 「好的」接受哪一步要看提议点没点名它，点名的依据正是这份描述（空描述的步没法被证明是提议的那一项）。
_EDGE_DESCRIPTIONS = {"warning_light.close": "关闭警示灯", "hvac.inc": "调高空调温度", "hvac.on": "打开空调",
                      "window.open": "打开车窗", "window.close": "关闭车窗",
                      # 云侧写步的真实描述是一大段写给规划器的用法说明，点名只看功能主干（第一个标点之前）
                      "navigation.cancel": "结束/取消**当前正在进行的这次导航**（「取消导航」「别导了」「不去了」 在有活动路线时）"}


def _described(agent):
    for cap in agent.manifest.capabilities:
        cap.description = _EDGE_DESCRIPTIONS.get(cap.intent, cap.description)
    return agent


def _agents():
    return [
        MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
        _write(MockAgent("reminder", ["reminder.cancel", "reminder.list"]), "reminder.cancel"),
        _write(MockAgent("deep-research", ["research.run"]), "research.run"),
        _described(_write(MockAgent("navigation", ["navigation.cancel"]), "navigation.cancel")),
        _described(MockAgent("edge-vehicle", ["warning_light.close", "hvac.inc", "hvac.on",
                                              "window.open", "window.close"],
                             kind="edge_fast", deployment="edge")),
        MockAgent("nearby", ["nearby.search", "nearby.order"], require_confirm=("nearby.order",)),
    ]


def _wire(*steps):
    """steps: (agent_id, intent, slots)。capability_ref 从真实装配的 catalog 取。"""
    catalog = _assemble_capability_catalog(_agents())
    return json.dumps({"addressed": True, "complexity": "simple", "steps": [
        {"id": f"s{i}", "capability_ref": catalog.pair_to_ref[(agent_id, intent)], "slots": slots,
         "depends_on": [], "slot_refs": {}}
        for i, (agent_id, intent, slots) in enumerate(steps, 1)]}, ensure_ascii=False)


def _build(reply, text, history=None):
    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    working_set = WorkingSet(catalog=_agents(), history=list(history or []))
    return asyncio.run(builder.build(text, working_set, PlanContext(session_id="t")))


def _intents(plan):
    return [step.intent for step in plan.steps]


# ── 纯应答 + 写步 + 上一轮没有提议 ⇒ 不执行 ────────────────────────────────────────────────

@pytest.mark.parametrize("steps", [
    [("reminder", "reminder.cancel", {"index": "2"})],                  # 真栈 `a4bb73bf` RS21 第 3 趟逐字
    [("reminder", "reminder.cancel", {"title": "第二个"})],              # 历史 2026-09-22
    [("deep-research", "research.run", {"topic": "过去一周重大科技新闻综述"})],   # 历史 2026-09-23
    [("edge-vehicle", "warning_light.close", {})],                      # 同形的端侧写（批 G 那次是被催出来的）
])
def test_an_acknowledgment_does_not_grow_a_write(steps):
    plan = _build(_wire(*steps), "可以，已为您执行")
    assert _intents(plan) == ["chitchat.talk"], _intents(plan)
    assert "_ack_write_blocked" in plan.plan_mode, plan.plan_mode
    assert plan.technical_failure is False


def test_the_whole_plan_goes_not_only_the_write():
    plan = _build(_wire(("nearby", "nearby.search", {"keyword": "咖啡"}),
                        ("reminder", "reminder.cancel", {"index": "2"})), "好的")
    assert _intents(plan) == ["chitchat.talk"], _intents(plan)


def test_a_bare_ok_with_no_offer_before_it_does_not_grow_a_write():
    history = [{"role": "user", "text": "今天天气怎么样"}, {"role": "assistant", "text": "今天晴，26 度。"}]
    plan = _build(_wire(("edge-vehicle", "hvac.inc", {})), "好的", history)
    assert _intents(plan) == ["chitchat.talk"], _intents(plan)


# ── 对照：照常执行 ───────────────────────────────────────────────────────────────────────────

def test_ok_after_the_assistant_offered_still_executes():
    """「好的」接在助手的提议之后：请求在上一轮的提议里，照常执行。"""
    history = [{"role": "user", "text": "有点冷"},
               {"role": "assistant", "text": "要不要我帮你把空调调高一点？"}]
    plan = _build(_wire(("edge-vehicle", "hvac.inc", {})), "好的", history)
    assert _intents(plan) == ["hvac.inc"], _intents(plan)
    assert "_ack_write_blocked" not in plan.plan_mode


@pytest.mark.parametrize("text, steps", [
    ("好的，打开空调", [("edge-vehicle", "hvac.on", {})]),
    ("取消导航", [("navigation", "navigation.cancel", {})]),
    ("就这家", [("nearby", "nearby.order", {"name": "甲咖啡"})]),
])
def test_utterances_that_carry_a_request_still_plan_writes(text, steps):
    plan = _build(_wire(*steps), text)
    assert _intents(plan) == [intent for _, intent, _ in steps], _intents(plan)
    assert "_ack_write_blocked" not in plan.plan_mode


def test_a_read_from_an_acknowledgment_is_not_touched():
    """只读步不是安全问题，本闸不管。"""
    plan = _build(_wire(("reminder", "reminder.list", {})), "可以，已为您执行")
    assert _intents(plan) == ["reminder.list"], _intents(plan)


# ── 评审四轮 R4-01：「好的」只接受**提议点名的那一项** ─────────────────────────────────────────────
# 修前判据是「最近一条助手话里有没有问号」：问号可能来自引用或知识问句，也不说明提议的是哪一步——纯应答可以长出任何写步。

def _said(text):
    return [{"role": "user", "text": "有点冷"}, {"role": "assistant", "text": text}]


def test_an_offer_accepts_only_the_step_it_named():
    plan = _build(_wire(("edge-vehicle", "hvac.inc", {}), ("edge-vehicle", "window.open", {})),
                  "好的", _said("要不要我帮你把空调调高一点？"))
    assert _intents(plan) == ["hvac.inc"], _intents(plan)
    assert "_ack_write_trimmed" in plan.plan_mode


def test_an_offer_of_something_else_does_not_authorise_this_write():
    plan = _build(_wire(("edge-vehicle", "window.open", {})), "好的", _said("要不要我帮你把空调调高一点？"))
    assert _intents(plan) == ["chitchat.talk"], _intents(plan)
    assert "_ack_write_blocked" in plan.plan_mode


def test_an_offer_to_close_does_not_authorise_opening_it():
    plan = _build(_wire(("edge-vehicle", "window.open", {})), "好的", _said("要不要我帮你把车窗关上？"))
    assert _intents(plan) == ["chitchat.talk"], _intents(plan)


@pytest.mark.parametrize("said", [
    "您刚才问的是「要不要打开车窗？」。车窗可以在中控屏上控制。",   # 引号里的问号不是提议
    "你知道为什么空调会有异味吗？",                                 # 知识问句不是提议
    "要不要我帮你打开空调？另外，今天气温二十六度。",                # 提议不在最后一句：接受的是最后那一句
    "空调已经调高了一点。",                                          # 根本没有提问
])
def test_a_question_mark_alone_is_not_an_offer(said):
    plan = _build(_wire(("edge-vehicle", "hvac.on", {})), "好的", _said(said))
    assert _intents(plan) == ["chitchat.talk"], (said, _intents(plan))


def test_a_cloud_write_named_by_the_head_of_its_description_is_accepted():
    """云侧写步的描述是一大段用法说明，点名只看功能主干（「结束/取消当前正在进行的这次导航」）。"""
    plan = _build(_wire(("navigation", "navigation.cancel", {})),
                  "好的", _said("前方拥堵严重，需要我帮你把现在的导航取消吗？"))
    assert _intents(plan) == ["navigation.cancel"], _intents(plan)
