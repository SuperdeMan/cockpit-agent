"""整句型能力在同一份计划里最多一步（QA 余项 SL1，2026-08-29）。

## 真栈来历

长会话 `e15ac1e` family persona，SL1 那一句
「明天下午四点提醒我参加代号992535的评审会，三点半再提醒我一次」：
trace 的 `span_nodes` 里出现**两个 `step.agent:reminder`**，
卡片是两个 `card_group`、**4 张 `reminder_card`、4 个不同 id**。
`_create_batch` 读的是 `intent.raw_text`（不读槽），所以每一步都把整组建了一遍。

**代价不止「多几条记录」**：4 条一模一样的条目让按标题取消当场撞歧义
（真栈原样：「有 4 条都能对上，要取消哪条？」），这组提醒**既清不掉、
又一直参与序数参照系**。当年那条「同轮不收编」的裁定算的是「多几条记录」，
**没算「从此取消不掉」**。

## 为什么既有的两道防抖挡不住（逐条核过，不是推测）

- `executor._exec_step` 的副作用指纹是 `(intent, 归一化 slots)`，而 planner
  给这两步**发明了不同的槽名**——9 次专项取样里出现过 7 种
  （`title1/time1_text`、`time_text_1/2`、`item_1/item_2`…）⇒ 指纹不同、不命中。
- `reminder._cross_turn_duplicate` 明写着「一句话被规划成两步是设计内的，
  **同轮不收编**」（契约 §9.35 / fix plan C10-E / history 三处）。

⇒ 本机制**不推翻那条裁定**：它管的不是「两步做了同一件事」，
而是「**这个能力按自己的声明就只该有一步**」。声明在 capability
（`whole_utterance`），编排通用消费——同 `heavy` / `require_confirm` /
`slot_shapes` 的分工，编排核心不出现任何 agent_id/intent 字面量。
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
from orchestrator.cloud.planning import PlanBuilder, _capability_pairs

from tests.test_planning import MockAgent


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    return [MockAgent("reminder", ["reminder.create_batch", "reminder.create"],
                      whole_utterance=("reminder.create_batch",)),
            MockAgent("chitchat", ["chitchat.talk"])]


def _reply(*steps) -> str:
    """steps: (intent, slots) —— 槽名故意各不相同，复刻真栈那 7 种发明。"""
    pairs = _capability_pairs(_agents())
    refs = {name: f"cap_{i:04d}" for i, (_a, name) in enumerate(pairs, 1)}
    return json.dumps({"addressed": True, "steps": [
        {"id": f"s{i}", "capability_ref": refs[intent], "slots": slots,
         "depends_on": [], "slot_refs": {}}
        for i, (intent, slots) in enumerate(steps, 1)]}, ensure_ascii=False)


def _build(reply: str, text="明天下午四点提醒我参加代号992535的评审会，三点半再提醒我一次"):
    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()),
                                     PlanContext(session_id="t")))


def test_two_whole_utterance_steps_collapse_to_one():
    """真栈那份计划的形状：同一个整句型能力两步、**槽名不同**（所以指纹防抖不命中）。"""
    plan = _build(_reply(
        ("reminder.create_batch", {"title1": "参加代号992535的评审会",
                                   "time1_text": "明天下午四点"}),
        ("reminder.create_batch", {"item_2": "参加代号992535的评审会",
                                   "time_text_2": "三点半"})))

    assert [s.intent for s in plan.steps] == ["reminder.create_batch"], (
        "整句型能力被排了两步——每一步都会把整组再建一遍")
    assert plan.steps[0].id == "s1", "保留的应当是第一步"
    assert plan.steps[0].whole_utterance is True


def test_a_capability_that_did_not_declare_it_keeps_both_steps():
    """误伤对照：**没声明的能力逐字维持原行为**。

    这条机制不许悄悄给全域加一条「同 intent 只许一步」的规则——
    「提醒我三点半和四点各一次」真被规划成两个 `reminder.create` 时，
    那两步各建一条，是对的。
    """
    plan = _build(_reply(
        ("reminder.create", {"title": "评审会", "time_text": "明天下午四点"}),
        ("reminder.create", {"title": "评审会", "time_text": "明天三点半"})))

    assert [s.intent for s in plan.steps] == [
        "reminder.create", "reminder.create"]


def test_a_single_whole_utterance_step_is_untouched():
    """对照：正常的一步一个字不改。"""
    plan = _build(_reply(
        ("reminder.create_batch", {"title": "评审会"})))

    assert [s.intent for s in plan.steps] == ["reminder.create_batch"]


def test_whole_utterance_does_not_collapse_across_different_intents():
    """对照：整句型只对**同一个 intent** 收敛——它说的是「这个能力只该有一步」，
    不是「这份计划只该有一步」。"""
    plan = _build(_reply(
        ("reminder.create_batch", {"title": "评审会"}),
        ("chitchat.talk", {"text": "顺便讲个笑话"})))

    assert [s.intent for s in plan.steps] == [
        "reminder.create_batch", "chitchat.talk"]
