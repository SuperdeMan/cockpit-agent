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


def test_merges_charging_waypoint_into_navigate_action():
    """导航 navigate 动作 + 充电步 data.waypoint → 途经点并入 navigate.payload.waypoints。"""
    agg = Aggregator(_fake_llm)
    nav = StepResult(
        step_id="s1", status=StepStatus.OK, speech="已规划路线",
        actions=[{"type": "navigate",
                  "payload": {"destination": "华润春笋大厦", "lat": 22.5, "lng": 113.94},
                  "require_confirm": False}])
    charge = StepResult(
        step_id="s2", status=StepStatus.OK, speech="已加入途经充电站",
        ui_card={"type": "charging_route", "destination": "华润春笋大厦",
                 "stops": [{"name": "特来电·春笋站"}]},
        data={"waypoint": {"name": "特来电·春笋站", "lat": 22.4998, "lng": 113.9385,
                           "address": "深圳湾"}})
    out = asyncio.run(agg.compose("导航去华润春笋大厦，附近找个充电桩", [nav, charge]))
    navs = [a for a in out["actions"] if a["type"] == "navigate"]
    assert len(navs) == 1
    wps = navs[0]["payload"]["waypoints"]
    assert wps[0]["name"] == "特来电·春笋站" and wps[0]["lat"] == 22.4998
    assert out["ui_card"]["type"] == "charging_route"   # 仍优先展示充能路线卡


def test_prefers_waypoint_choice_card_over_other_cards():
    """多意图『导航+找餐厅』若 planner 误拆出 food，导航的 waypoint_choice 真实候选卡应胜出。"""
    agg = Aggregator(_fake_llm)
    nav = StepResult(step_id="s1", status=StepStatus.OK, speech="规划路线",
                     ui_card={"type": "poi_list", "purpose": "waypoint_choice",
                              "destination": "中国华润大厦", "items": [{"name": "餐厅A"}]})
    food = StepResult(step_id="s2", status=StepStatus.OK, speech="需要订位吗",
                      ui_card={"type": "restaurant_list", "items": [{"name": "美食·名店1"}]})
    out = asyncio.run(agg.compose("导航去X在附近找餐厅", [nav, food]))
    assert out["ui_card"]["purpose"] == "waypoint_choice"


def test_dedupes_duplicate_navigate_actions():
    """同目的地的重复 navigate 动作只保留一个（防御多意图重复导航）。"""
    agg = Aggregator(_fake_llm)
    dup = {"type": "navigate",
           "payload": {"destination": "V东滨店", "lat": 22.499, "lng": 113.938},
           "require_confirm": False}
    a = StepResult(step_id="s1", status=StepStatus.OK, speech="路线A", actions=[dict(dup)])
    b = StepResult(step_id="s2", status=StepStatus.OK, speech="路线B", actions=[dict(dup)])
    out = asyncio.run(agg.compose("导航去V东滨店", [a, b]))
    navs = [x for x in out["actions"] if x["type"] == "navigate"]
    assert len(navs) == 1
