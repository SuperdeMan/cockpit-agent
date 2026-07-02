"""Aggregator 卡片择优：多意图下优先展示充能路线卡（途经点），而非首个卡。"""
import asyncio

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.models import StepResult, StepStatus


async def _fake_llm(messages, **kwargs):
    return "已为您规划好路线并安排了途中补电。"


def test_prefers_charging_route_card_over_first_card():
    agg = Aggregator(_fake_llm)
    nav = StepResult(step_id="s1", status=StepStatus.OK, speech="已规划路线",
                     ui_card={"type": "poi_list", "items": [{"name": "东车村委会"}]})
    charge = StepResult(step_id="s2", status=StepStatus.OK, speech="途中补电1次",
                        ui_card={"type": "charging_route", "display_priority": 0,
                                 "destination": "东车村",
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
        ui_card={"type": "charging_route", "display_priority": 0, "destination": "华润春笋大厦",
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
                              "display_priority": 1,
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


def test_multiple_info_cards_grouped_for_same_screen():
    """股票卡+新闻卡（纯信息、无交互）→ 合成 card_group 同屏并存，不再丢其一。"""
    agg = Aggregator(_fake_llm)
    stock = StepResult(step_id="s1", status=StepStatus.OK, speech="英伟达208.65",
                       ui_card={"type": "stock_quote", "symbol": "NVDA"})
    news = StepResult(step_id="s2", status=StepStatus.OK, speech="新闻",
                      ui_card={"type": "news_brief", "items": []})
    out = asyncio.run(agg.compose("查英伟达股价和新闻", [stock, news]))
    assert out["ui_card"]["type"] == "card_group"
    types = [c["type"] for c in out["ui_card"]["items"]]
    assert "stock_quote" in types and "news_brief" in types


def test_interactive_card_shown_alone_not_grouped():
    """有交互卡（充电路线/候选选择）时单独展示，不与信息卡混排（避免干扰选择）。"""
    agg = Aggregator(_fake_llm)
    stock = StepResult(step_id="s1", status=StepStatus.OK, speech="x",
                       ui_card={"type": "stock_quote"})
    charge = StepResult(step_id="s2", status=StepStatus.OK, speech="y",
                        ui_card={"type": "charging_route", "display_priority": 0, "stops": []})
    out = asyncio.run(agg.compose("q", [stock, charge]))
    assert out["ui_card"]["type"] == "charging_route"


def test_aggregate_honors_user_count_format_request():
    """用户要求『三条结论』→ 聚合提示词带上该意图，system 指示按分点输出（不再压成一段）。"""
    captured = {}

    async def _capture_llm(messages, **kwargs):
        captured["system"], captured["user"] = messages[0]["content"], messages[1]["content"]
        return "1. 结论一 2. 结论二 3. 结论三"

    agg = Aggregator(_capture_llm)
    r1 = StepResult(step_id="s1", status=StepStatus.OK, speech="英伟达新闻……")
    r2 = StepResult(step_id="s2", status=StepStatus.OK, speech="股价208.65美元")
    out = asyncio.run(agg.compose("查英伟达消息股价，对智能座舱影响给我三条结论", [r1, r2]))

    assert "三条结论" in captured["user"]                       # 用户原话进了 prompt
    assert ("分点" in captured["system"]) or ("条数" in captured["system"])  # system 指示分点
    assert "1." in out["speech"]
