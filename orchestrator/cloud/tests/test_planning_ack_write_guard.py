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


def _agents():
    return [
        MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
        _write(MockAgent("reminder", ["reminder.cancel", "reminder.list"]), "reminder.cancel"),
        _write(MockAgent("deep-research", ["research.run"]), "research.run"),
        _write(MockAgent("navigation", ["navigation.cancel"]), "navigation.cancel"),
        MockAgent("edge-vehicle", ["warning_light.close", "hvac.inc", "hvac.on"],
                  kind="edge_fast", deployment="edge"),
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
