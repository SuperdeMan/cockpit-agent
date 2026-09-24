"""只回答的计划不被升级成多批（评审三轮追加批 J，J-2，2026-09-24；设计 §13）。

`_preserve_conditional_replan_contract` 看见「如果 / 要是 / 假如 / 若」就把 simple 计划升成 adaptive——它本是给「模型规划了条件计划的
观察步、却漏了 adaptive 标记」补回来的。「如果你能去旅行，你想去哪里」模型只给了一个 `chitchat.talk`，被升级进 T2 ⇒ 深度回答流式超时、
unary 重跑再超时、再规划 ⇒ 13.5 s 后念「前面那个小问题出了点状况」。collector 里 40 次升级有 2 次全由只回答步组成（另一次是广告词里
「宛若」的「若」），其余都有观察步。条件计划的前半段是观察；只回答的步（manifest `response_only`）产不出可供决策的观察。
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
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    return [
        MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
        MockAgent("manual-rag", ["manual.query"], response_only=("manual.query",)),
        MockAgent("info", ["info.weather"]),
        MockAgent("reminder", ["reminder.create"]),
    ]


def _build(text, pairs, complexity="simple"):
    catalog = _assemble_capability_catalog(_agents())
    wire = json.dumps({"addressed": True, "complexity": complexity, "steps": [
        {"id": f"s{i}", "capability_ref": catalog.pair_to_ref[pair], "slots": {},
         "depends_on": [], "slot_refs": {}} for i, pair in enumerate(pairs, 1)]}, ensure_ascii=False)

    async def mock_llm(messages):
        return wire

    async def mock_resolve(query, top_k=1):
        return []

    return asyncio.run(PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve).build(
        text, WorkingSet(catalog=_agents()), PlanContext(session_id="t")))


@pytest.mark.parametrize("text, pairs", [
    ("如果你能去旅行，你想去哪里", [("chitchat", "chitchat.talk")]),        # §7 原句（修前 13.5 s + 失败话术）
    ("要是双闪一直开着会怎么样", [("manual-rag", "manual.query")]),
    ("融雪凝脂质地，宛若逆转八年纹路", [("chitchat", "chitchat.talk")]),     # collector 里「宛若」的「若」
])
def test_an_answer_only_plan_is_not_promoted_to_adaptive(text, pairs):
    plan = _build(text, pairs)
    assert plan.complexity == "simple", plan.complexity


def test_a_conditional_plan_with_an_observation_step_is_still_promoted():
    plan = _build("明天要是下雨就提醒我带伞", [("info", "info.weather")])
    assert plan.complexity == "adaptive"


def test_a_model_declared_adaptive_plan_is_left_alone():
    plan = _build("如果你能去旅行，你想去哪里", [("chitchat", "chitchat.talk")], complexity="adaptive")
    assert plan.complexity == "adaptive"


def test_a_conditional_plan_mixing_observation_and_answer_steps_is_still_promoted():
    """「全是」只回答步才不升级：有一个观察步，条件计划的前半段就在。"""
    plan = _build("明天要是下雨就提醒我带伞", [("info", "info.weather"), ("chitchat", "chitchat.talk")])
    assert plan.complexity == "adaptive"
