"""开关现状问句的查询步必须被原话点名（核心旅程 K12，2026-10-12）。

加了「查询香氛」能力之后，目录里只有这一个开关现状查询；「充电口现在开着吗」被规划器拿它顶替，答成「香氛关着（模拟车读数）」——
问东答西。点不出名的端侧只读查询步去掉，空了交未确认的兜底答一句；「电量还剩多少」这类不是开关现状问句的查询不受影响。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import Plan, PlanContext, Step
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_planning import MockAgent  # noqa: E402  复用真实装配路径的 mock agent
from test_question_write_guard import _response  # noqa: E402  同一个未确认兜底


def _edge(intent: str, description: str, *, step_id: str = "s1") -> Step:
    return Step(id=step_id, agent_id="edge-vehicle", intent=intent, deployment="edge", kind="edge_fast",
                capability_description=description)


def _guard(text: str, *steps: Step, agents=None) -> Plan:
    plan = Plan(steps=list(steps), complexity="simple", raw_text=text)
    return PlanBuilder(llm_fn=None, registry_fn=None)._apply_switch_state_query_guard(plan, text, agents)


def test_a_query_for_another_object_is_dropped_and_falls_back():
    fallback = _response(MockAgent("chitchat", ["chitchat.answer"]), "chitchat.answer")
    plan = _guard("充电口现在开着吗", _edge("fragrance.query", "查询香氛"), agents=[fallback])
    assert [s.intent for s in plan.steps] == ["chitchat.answer"]
    assert plan.plan_mode.endswith("_state_query_unnamed")


def test_without_a_fallback_the_plan_is_left_empty_rather_than_answering_for_another_object():
    plan = _guard("充电口现在开着吗", _edge("fragrance.query", "查询香氛"))
    assert plan.steps == []


def test_the_object_the_user_named_is_kept():
    for text in ("香氛现在开着吗", "香氛开了没"):
        plan = _guard(text, _edge("fragrance.query", "查询香氛"))
        assert [s.intent for s in plan.steps] == ["fragrance.query"], text


def test_queries_that_are_not_switch_state_questions_are_untouched():
    # 「还剩多少电」「轮胎气压正常吗」与能力描述对不上字面（点名 0 分）——护栏要是对所有查询都生效，正常查询会被误删
    for text, intent, description in (("电量还剩多少", "battery.query", "查询电量"),
                                      ("还剩多少电", "battery.query", "查询电量"),
                                      ("轮胎气压正常吗", "tire_pressure.query", "查询胎压")):
        plan = _guard(text, _edge(intent, description))
        assert [s.intent for s in plan.steps] == [intent], text


def test_only_unnamed_edge_reads_go_cloud_steps_and_writes_stay():
    cloud = Step(id="s2", agent_id="manual", intent="manual.query", capability_description="查询车辆手册")
    write = _edge("charging_port.open", "打开充电口", step_id="s3")
    plan = _guard("充电口现在开着吗", _edge("fragrance.query", "查询香氛"), cloud, write)
    assert [s.intent for s in plan.steps] == ["manual.query", "charging_port.open"]


@pytest.fixture(autouse=True)
def _offline_exemplars(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _build(text: str):
    """跑真正的 `build()`：模型照旧把开关现状问句派给香氛查询，看出口的护栏拦不拦得住。"""
    edge = MockAgent("edge-vehicle", ["fragrance.query"], kind="edge_fast", deployment="edge")
    edge.manifest.capabilities[0].description = "查询香氛"
    agents = [edge, MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",))]
    ref = _assemble_capability_catalog(agents).pair_to_ref[("edge-vehicle", "fragrance.query")]
    wire = '{"steps":[{"id":"s1","capability_ref":"%s","slots":{},"depends_on":[],"slot_refs":{}}]}' % ref

    async def mock_llm(messages):
        return wire

    async def mock_resolve(query, top_k=1):
        return []

    return asyncio.run(PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve).build(
        text, WorkingSet(catalog=agents), PlanContext(session_id="t")))


def test_build_does_not_answer_the_charge_port_with_the_fragrance_state():
    assert "fragrance.query" not in [s.intent for s in _build("充电口现在开着吗").steps]


def test_build_keeps_the_fragrance_query_for_the_fragrance():
    assert [s.intent for s in _build("香氛现在开着吗").steps] == ["fragrance.query"]
