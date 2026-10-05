"""规划器输出只少最外层对象的右花括号时补上（2026-10-05 固定语料 V207）。

`8ffc8bff` 基线第 1 遍：「露营模式是什么意思？」第一轮在文本里交了「受话、零步」，第二轮交了正确的 `manual.query` 计划，
只少最后一个 `}`（`…"露营模式是什么意思"}}]` 就结束了）。解析失败被当成「从未规划」，交给闲聊凭常识编了一段露营模式。
只补对象、不补数组：steps 数组是模型自己闭合的，说明它写完了步骤；数组没闭合可能是被长度截断、还有步骤没写。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog, _close_dangling_objects

from tests.test_planning import MockAgent


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")


_V207 = '{"addressed":true,"steps":[{"id":"s1","capability_ref":"cap_0107","slots":{"question":"露营模式是什么意思"}}]'


@pytest.mark.parametrize("raw,closed", [
    (_V207, _V207 + "}"),
    ('```json\n{"addressed":true,"steps":[]\n```', '{"addressed":true,"steps":[]}'),     # 代码块围栏
    ('{"addressed":true,"goal":"说\\"你好\\"","steps":[]', '{"addressed":true,"goal":"说\\"你好\\"","steps":[]}'),
    ('{"addressed":true,"plan":{"steps":[]', '{"addressed":true,"plan":{"steps":[]}}'),
])
def test_only_missing_closing_braces_are_added(raw, closed):
    assert _close_dangling_objects(raw) == closed


@pytest.mark.parametrize("raw", [
    '{"addressed":true,"steps":[{"id":"s1","capability_ref":"cap_1","slots":{}}',   # 数组没闭合：可能还有步骤
    '{"addressed":true,"steps":[{"id":"s1","capability_ref":"cap_',                 # 停在字符串里
    '{"addressed":true,"steps":[]}',                                                 # 本来就完整
    '{"addressed":true,"steps":[]} 多余的字',                                        # 最外层已闭合，后面是别的问题
    '{"addressed":true,"steps":[]} {"steps":[]',                                     # 闭合后又开了一个对象
    '{"addressed":true,"goal":"到了}',                                               # 停在字符串里，恰好以 } 结尾
    "这不是 JSON",
])
def test_other_broken_shapes_are_not_repaired(raw):
    assert _close_dangling_objects(raw) is None


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
            MockAgent("manual", ["manual.query"])]


def test_a_plan_missing_its_last_brace_is_planned_not_handed_to_chitchat():
    catalog = _assemble_capability_catalog(_agents())
    ref = catalog.pair_to_ref[("manual", "manual.query")]
    truncated = json.dumps({"addressed": True, "steps": [
        {"id": "s1", "capability_ref": ref, "slots": {"question": "露营模式是什么意思"}}]}, ensure_ascii=False)[:-1]
    replies = ['{"addressed":true,"steps":[]}', truncated]          # 第 1 轮受话零步，第 2 轮少最后一个 }

    async def llm_tools(messages, tools):
        return replies.pop(0), None                                 # 两轮都没走工具调用，只在文本里交

    async def llm(messages):
        return replies.pop(0)

    async def resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=llm, registry_fn=resolve, llm_tool_fn=llm_tools)
    plan = asyncio.run(builder.build("露营模式是什么意思？", WorkingSet(catalog=_agents()), PlanContext(session_id="t")))
    assert [s.intent for s in plan.steps] == ["manual.query"], (plan.plan_mode, plan.steps)
