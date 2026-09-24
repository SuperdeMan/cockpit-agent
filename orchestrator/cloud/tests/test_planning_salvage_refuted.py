"""抢救计划里的写操作被工具通道的「受话、零步」推翻，就不再回落到它（评审三轮追加批 N，N-3，2026-09-24；设计 §17）。

真栈 `f14d1e43` RS28 第 2 趟（trace `d94de400`）：「推荐三部适合全家看的电影」第一轮掉出工具通道，文本里编出 `luckin.order`
（奶咖 / 大杯 / 标准糖）+ `navigation.navigate_to`；第二轮走工具通道如实交了「受话、零步」。循环后的「重试失败就回落到抢救计划」
把这个合法结论当成了重试失败——导航真的发了出去。collector 121 次回落里只有这一次是「求信息的请求 + 重试说零步 + 抢救计划带写」；
「改成7点半前到就行」「只要有堵车就提醒我」的抢救计划是对的，照旧回落。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _SUBMIT_PLAN_NAME, _assemble_capability_catalog

from tests.test_planning import MockAgent


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")


def _write(agent, *intents):
    for cap in agent.manifest.capabilities:
        if cap.intent in intents:
            cap.effect = "write"
    return agent


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
            _write(MockAgent("navigation", ["navigation.navigate_to", "navigation.reroute"]),
                   "navigation.navigate_to", "navigation.reroute"),
            MockAgent("info", ["info.search"])]


def _salvage_text(*pairs):
    catalog = _assemble_capability_catalog(_agents())
    return json.dumps({"addressed": True, "steps": [
        {"id": f"s{i}", "capability_ref": catalog.pair_to_ref[pair], "slots": {}, "depends_on": [], "slot_refs": {}}
        for i, pair in enumerate(pairs, 1)]}, ensure_ascii=False)


_NO_ACTION_CALL = ("", [{"id": "c2", "name": _SUBMIT_PLAN_NAME, "arguments": {"addressed": True, "steps": []}}])


def _build(text, salvage, retry=_NO_ACTION_CALL):
    replies = [(salvage, None), retry]           # 第 1 轮掉档（只有文本），第 2 轮回到工具通道

    async def llm_tools(messages, tools):
        content, calls = replies.pop(0)
        return content, calls

    async def llm(messages):
        return "这不是 JSON"

    async def resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=llm, registry_fn=resolve, llm_tool_fn=llm_tools)
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()), PlanContext(session_id="t")))


def test_a_salvaged_write_refuted_by_a_no_action_retry_is_not_executed():
    plan = _build("推荐三部适合全家看的电影", _salvage_text(("navigation", "navigation.navigate_to")))
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False
    assert plan.plan_mode.endswith("_no_action_info"), plan.plan_mode


def test_a_correction_keeps_its_salvaged_plan():
    """「改成7点半前到就行」不是求信息的请求：抢救计划是对的（重试的零步反而错），照旧回落。"""
    plan = _build("改成7点半前到就行", _salvage_text(("navigation", "navigation.reroute")))
    assert [s.intent for s in plan.steps] == ["navigation.reroute"], plan.steps
    assert plan.plan_mode == "toolcall_salvage_kept"


def test_a_read_only_salvage_is_still_kept_for_an_information_request():
    plan = _build("推荐三部适合全家看的电影", _salvage_text(("info", "info.search")))
    assert [s.intent for s in plan.steps] == ["info.search"], plan.steps
    assert plan.plan_mode == "toolcall_salvage_kept"


def test_a_retry_that_fails_outright_still_falls_back_to_the_salvage():
    """重试轮是真失败（没交出任何东西）而不是「零步」：回落规则不变。"""
    plan = _build("推荐三部适合全家看的电影", _salvage_text(("navigation", "navigation.navigate_to")),
                  retry=("", None))
    assert [s.intent for s in plan.steps] == ["navigation.navigate_to"], plan.steps
    assert plan.plan_mode == "toolcall_salvage_kept"
