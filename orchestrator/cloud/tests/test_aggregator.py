"""Aggregator 卡片择优：多意图下优先展示充能路线卡（途经点），而非首个卡。"""
import asyncio

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.models import StepResult, StepStatus


async def _fake_llm(messages):
    return "已为您规划好路线并安排了途中补电。"


def test_prefers_charging_route_card_over_first_card():
    agg = Aggregator(_fake_llm)
    nav = StepResult(step_id="s1", status=StepStatus.OK, speech="已规划路线",
                     ui_card={"type": "poi_list", "items": [{"name": "东车村委会"}]})
    charge = StepResult(step_id="s2", status=StepStatus.OK, speech="途中补电1次",
                        ui_card={"type": "charging_route", "destination": "东车村",
                                 "stops": [{"name": "南网充电站", "at_km": 212}]})
    out = asyncio.run(agg.compose("导航去东车村并规划途中充电", [nav, charge]))
    # 即便导航卡在前，也应展示充能路线卡（途经点对"规划充电"更相关）
    assert out["ui_card"]["type"] == "charging_route"
    assert out["ui_card"]["stops"][0]["at_km"] == 212


def test_single_card_unchanged():
    agg = Aggregator(_fake_llm)
    nav = StepResult(step_id="s1", status=StepStatus.OK, speech="已规划路线",
                     ui_card={"type": "poi_list", "items": []})
    out = asyncio.run(agg.compose("导航去东车村", [nav]))
    assert out["ui_card"]["type"] == "poi_list"
