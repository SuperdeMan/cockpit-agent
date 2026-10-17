"""「这一步的结果有没有后续步接着用」随 meta 下发（`runtime.slots.RESULT_CONSUMED_META`，2026-10-17，核心旅程 EN09）。

声明方据此决定要不要零播报改派：充电找站在「找站 → 引用第一个改线」这份两步计划里要是也改派改线，就加了两个站。
"""
from __future__ import annotations

import asyncio

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, PlanContext, Step
from runtime.slots import RESULT_CONSUMED_META


class _Resp:
    def __init__(self):
        self.status, self.speech, self.follow_up = 0, "好的。", ""
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, {"items": [{"name": "国网充电站"}]}, []


def test_consumed_steps_are_found_from_dependencies_refs_and_placeholders():
    steps = [Step(id="s1", agent_id="charging", intent="charging.find"),
             Step(id="s2", agent_id="navigation", intent="navigation.reroute",
                  slot_refs={"add_waypoint": "s1.data.items.0.name"}),
             Step(id="s3", agent_id="nearby", intent="nearby.search"),
             Step(id="s4", agent_id="navigation", intent="navigation.navigate_to",
                  slots={"destination": "${s3.data.items.0.name}"}),
             Step(id="s5", agent_id="info", intent="info.weather"),
             Step(id="s6", agent_id="chitchat", intent="chitchat.talk", depends_on=["s5"]),
             Step(id="s7", agent_id="info", intent="info.search", slots={"query": "s9.data.x 不是本计划的步"})]
    assert DagExecutor._consumed_step_ids(steps) == {"s1", "s3", "s5"}


def test_run_stamps_the_signal_and_clears_a_stale_one():
    calls = []

    async def call_agent(endpoint, intent, slots, ctx, meta):
        calls.append((intent, dict(meta or {})))
        return _Resp()

    find = Step(id="s1", agent_id="charging", intent="charging.find", meta={RESULT_CONSUMED_META: "true"})
    plan = Plan(steps=[find])
    asyncio.run(_drain(DagExecutor(call_agent_fn=call_agent).run(plan, PlanContext(raw_text="那顺路加个充电站"))))
    assert RESULT_CONSUMED_META not in calls[-1][1], "单步：没有后续步，旧值不许留着"

    calls.clear()
    find = Step(id="s1", agent_id="charging", intent="charging.find")
    reroute = Step(id="s2", agent_id="navigation", intent="navigation.reroute",
                   depends_on=["s1"], slot_refs={"add_waypoint": "s1.data.items.0.name"})
    asyncio.run(_drain(DagExecutor(call_agent_fn=call_agent).run(
        Plan(steps=[find, reroute]), PlanContext(raw_text="那顺路加个充电站"))))
    metas = dict(calls)
    assert metas["charging.find"].get(RESULT_CONSUMED_META) == "true"
    assert RESULT_CONSUMED_META not in metas["navigation.reroute"]


async def _drain(gen):
    return [item async for item in gen]
