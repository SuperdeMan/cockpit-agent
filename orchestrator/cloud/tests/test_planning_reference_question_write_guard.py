"""问规范 / 注意事项 / 条件的问句不执行声明为写的云侧步（评审三轮追加批 L，2026-09-24；设计 §15）。

「开长途前要注意些什么」真栈两次摸到改状态的云侧写：`fc1f5dde`（批 J 修前基线）一趟 `scene.activate`「长途驾驶」（没有同名场景才没执行——
匹配上会直接下发非危险动作），`62414223` 基线一趟 `scene.create`「长途自驾模式，共 11 个动作…座椅放平到105度」挂出确认卡。问句闸只拦
端侧写与需确认的步。扩到**全部**问句会误伤：collector 426 轮声明写步里被判成问句的 13 轮全是正当的（条件指令、嵌入式疑问、「去惠州怎么
充电」→ 充电规划、「介绍一下…」→ 深度调研）；只扩「规范 / 注意事项 / 条件」这一类（`is_reference_question`），历史 27 轮里命中 2、误伤 0。
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


def _write(agent, *intents):
    for cap in agent.manifest.capabilities:
        if cap.intent in intents:
            cap.effect = "write"
    return agent


def _agents():
    return [
        MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
        _write(MockAgent("scene-orchestrator", ["scene.activate", "scene.create"]), "scene.activate", "scene.create"),
        _write(MockAgent("charging-planner", ["charging.plan"]), "charging.plan"),
        _write(MockAgent("navigation", ["navigation.navigate_to"]), "navigation.navigate_to"),
        _write(MockAgent("deep-research", ["research.run"]), "research.run"),
        MockAgent("info", ["info.weather"]),
    ]


def _build(text, *pairs):
    catalog = _assemble_capability_catalog(_agents())
    wire = json.dumps({"addressed": True, "complexity": "simple", "steps": [
        {"id": f"s{i}", "capability_ref": catalog.pair_to_ref[pair], "slots": {}, "depends_on": [], "slot_refs": {}}
        for i, pair in enumerate(pairs, 1)]}, ensure_ascii=False)

    async def mock_llm(messages):
        return wire

    async def mock_resolve(query, top_k=1):
        return []

    return asyncio.run(PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve).build(
        text, WorkingSet(catalog=_agents()), PlanContext(session_id="t")))


@pytest.mark.parametrize("text, pair", [
    ("开长途前要注意些什么", ("scene-orchestrator", "scene.activate")),   # `fc1f5dde` 基线
    ("开长途前要注意些什么", ("scene-orchestrator", "scene.create")),     # `62414223` 基线
    ("去惠州要注意些什么", ("charging-planner", "charging.plan")),
])
def test_a_reference_question_does_not_run_a_declared_write(text, pair):
    plan = _build(text, pair)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert (plan.plan_mode or "").endswith("_question_write_blocked"), plan.plan_mode


def test_reads_in_a_reference_question_are_kept():
    plan = _build("开长途前要注意些什么", ("info", "info.weather"), ("scene-orchestrator", "scene.activate"))
    assert [s.intent for s in plan.steps] == ["info.weather"], plan.steps


@pytest.mark.parametrize("text, pair", [
    ("去惠州怎么充电", ("charging-planner", "charging.plan")),                       # collector ×4
    ("介绍一下北京有哪些著名的历史建筑", ("deep-research", "research.run")),           # collector ×1
    ("如果深圳今天不下雪，就导航去深圳湾公园", ("navigation", "navigation.navigate_to")),  # collector ×4
    ("导航去公司，看看路上什么情况", ("navigation", "navigation.navigate_to")),
])
def test_other_questions_keep_their_declared_writes(text, pair):
    """扩到全部问句会误伤的那几类：它们只受原来的问句闸管（端侧写 / 需确认）。"""
    plan = _build(text, pair)
    assert [s.intent for s in plan.steps] == [pair[1]], plan.steps


@pytest.mark.parametrize("text", [
    "露营模式是什么意思？", "什么是露营模式", "露营模式是干嘛的",
    "请问自动泊车是什么意思", "座椅加热是什么",
])
def test_definition_only_question_cannot_activate_a_cloud_write(text):
    # CA2-01 真栈 V207: planner's goal said "解释", but scene.activate reached NEED_CONFIRM.
    plan = _build(text, ("scene-orchestrator", "scene.activate"))
    assert [s.intent for s in plan.steps] == ["chitchat.talk"]


@pytest.mark.parametrize("text,pair", [
    ("深入调研什么是固态电池", ("deep-research", "research.run")),
    ("请介绍什么是固态电池", ("deep-research", "research.run")),
    ("帮我创建一个名叫什么是幸福的场景", ("scene-orchestrator", "scene.create")),
    ("开启露营模式", ("scene-orchestrator", "scene.activate")),
])
def test_explicit_work_is_not_a_definition_only_question(text, pair):
    plan = _build(text, pair)
    assert [s.intent for s in plan.steps] == [pair[1]]
