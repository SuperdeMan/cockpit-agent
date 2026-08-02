"""intent 对、agent_id 猜错 → 归位到唯一拥有者，而不是静默丢步。

出处：意图落域对抗测试首轮 §2 簇 F。实测「调高音量」planner 给的计划是对的
（`volume.inc`），只是把它派给了 `edge-media`——而 `volume.*` 属 `edge-vehicle`。
能力集校验按「intent ∈ 该 agent 能力集」判定不通过，于是**整步被静默丢掉**，
计划退化成 `chitchat.talk`。

判据：**丢一步和幻觉一步对用户是同一件事**——都没做成。但归位不能变成瞎猜，所以
只在 intent 于全清单里**唯一归属**时才归位；归属有歧义（≥2 家）或压根不存在时仍然丢。
`capability_hallucination_rate: 0%` 这条保证一分不减。
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


def _plan(raw: str, agents):
    async def mock_llm(messages):
        return raw

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build("调高音量", WorkingSet(catalog=agents),
                                     PlanContext(session_id="test")))


def _step(agent_id: str, intent: str) -> str:
    return ('{"steps":[{"id":"s1","agent_id":"%s","intent":"%s",'
            '"slots":{},"depends_on":[],"slot_refs":{}}]}' % (agent_id, intent))


def test_unique_owner_gets_the_misattributed_step():
    agents = [MockAgent("edge-vehicle", ["volume.inc", "hvac.set"]),
              MockAgent("edge-media", ["media.play"])]
    plan = _plan(_step("edge-media", "volume.inc"), agents)
    assert [(s.agent_id, s.intent) for s in plan.steps] == [("edge-vehicle", "volume.inc")]


def test_rehomed_step_carries_the_new_owners_endpoint():
    """归位不是只改个字符串——endpoint/部署位都必须换成真正拥有者的，否则调不通。"""
    agents = [MockAgent("edge-vehicle", ["volume.inc"]),
              MockAgent("edge-media", ["media.play"])]
    owner = next(a for a in agents if a.manifest.agent_id == "edge-vehicle")
    plan = _plan(_step("edge-media", "volume.inc"), agents)
    assert plan.steps[0].endpoint == owner.endpoint


def test_ambiguous_owner_is_still_dropped():
    """两家都声称拥有这个 intent → 不猜，仍按原样丢步。"""
    agents = [MockAgent("a", ["shop.order"]), MockAgent("b", ["shop.order"]),
              MockAgent("c", ["chitchat.talk"])]
    plan = _plan(_step("c", "shop.order"), agents)
    assert plan.steps == [] or all(s.intent != "shop.order" for s in plan.steps)


def test_unknown_intent_is_still_dropped():
    """能力集校验挡幻觉的作用不许被削弱：没有任何 agent 拥有它就是没有。"""
    agents = [MockAgent("edge-vehicle", ["volume.inc"])]
    plan = _plan(_step("edge-vehicle", "teleport.now"), agents)
    assert plan.steps == [] or all(s.intent != "teleport.now" for s in plan.steps)


def test_correctly_attributed_step_is_untouched():
    agents = [MockAgent("edge-vehicle", ["volume.inc"]),
              MockAgent("edge-media", ["media.play"])]
    plan = _plan(_step("edge-vehicle", "volume.inc"), agents)
    assert [(s.agent_id, s.intent) for s in plan.steps] == [("edge-vehicle", "volume.inc")]
