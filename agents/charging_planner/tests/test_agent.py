"""charging-planner Agent 契约测试。

覆盖三种路径：NEED_SLOT / OK / NEED_CONFIRM。
验证 Provider 降级、协作降级、车控只产 action。
"""
import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from agents._sdk.testing import make_context, run_handle, assert_result_valid
from agents.charging_planner.src.agent import ChargingPlannerAgent


def test_find_returns_list():
    """charging.find → OK + ui_card.charging_list"""
    ctx = make_context(context_values={"vehicle.battery": "72%", "vehicle.location": "科技园"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.find",
        slots={"prefer": "快充"}, raw_text="找个充电站", ctx=ctx))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "charging_list"
    assert len(res.ui_card["items"]) > 0


def test_find_near_destination_resolves_landmark_and_emits_waypoint():
    """charging.find 带视觉地标目的地 → 先解析成官方名再按目的地搜，出途经点契约+charging_route 卡。

    支撑「导航去[地标] + 在附近找充电桩」：地标"像笋的建筑"经共享解析器→"中国华润大厦"（高德可检索），
    聚合器据 data.waypoint 把站并入导航 navigate 动作。
    """
    from agents.charging_planner.src.providers.base import ChargingStation
    agent = ChargingPlannerAgent()
    seen = {}

    async def fake_find(location, charger_type="", meta=None):
        seen["address"] = location.address
        return [ChargingStation(id="c1", name="逸安启超级充电站", address="深圳湾万象城",
                                lat=22.516, lng=113.9473, distance_km=0.2,
                                available=3, total=8)]

    async def fake_llm(messages, **kwargs):
        return '["中国华润大厦"]'

    agent.charging.find_nearby = fake_find
    agent.llm.complete = fake_llm
    ctx = make_context(context_values={"vehicle.battery": "60%"})
    res = asyncio.run(run_handle(
        agent, "charging.find", slots={"destination": "深圳外形像笋一样的建筑物"},
        raw_text="导航去深圳外形像笋的建筑物，附近找个充电桩", ctx=ctx))

    assert res.status == "ok"
    assert seen["address"] == "中国华润大厦"               # 地标解析为官方名再搜（非原描述/当前位置）
    assert res.ui_card["type"] == "charging_route"
    assert res.ui_card["destination"] == "中国华润大厦"
    assert res.ui_card["stops"][0]["name"] == "逸安启超级充电站"
    wp = res.data["waypoint"]
    assert wp["name"] == "逸安启超级充电站" and wp["lat"] == 22.516 and wp["lng"] == 113.9473
    assert "途经充电站" in res.speech


def test_find_without_destination_unchanged():
    """charging.find 无 destination → 仍按当前位置搜、出 charging_list（行为不变）。"""
    ctx = make_context(context_values={"vehicle.battery": "72%", "vehicle.location": "科技园"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.find",
        slots={"prefer": "快充"}, raw_text="找个充电站", ctx=ctx))
    assert res.status == "ok"
    assert res.ui_card["type"] == "charging_list"
    assert "waypoint" not in (res.data or {})


def test_plan_is_advisory_no_confirm_no_navigate():
    """charging.plan 改信息建议：OK、不二次确认、不发导航动作（导航交给导航步），
    消除多意图『导航+充电』里的双确认/双 navigate。"""
    ctx = make_context(context_values={"vehicle.battery": "45%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.plan",
        slots={"destination": "杭州"}, raw_text="去杭州怎么充电", ctx=ctx))
    assert res.status == "ok"
    assert not any(a.get("require_confirm") for a in res.actions)   # 不再二次确认
    assert not any(a["type"] == "navigate" for a in res.actions)    # 不再发导航动作


def test_amap_charging_plan_waypoints_along_route():
    """充电规划 = 出发地 → 沿途途经充电点 → 目的地：途经点取自路线坐标（非目的地附近）。"""
    from agents.charging_planner.src.providers.amap import AmapChargingProvider
    from agents.navigation.src.providers.base import POI

    p = AmapChargingProvider(key="test-key")

    async def fake_route(o, d, meta=None, with_polyline=False):
        return {"distance_km": 870.0, "duration_min": 600.0, "steps": [],
                "points": [{"lng": 114.0, "lat": 23.0, "cum_km": 250.0},
                           {"lng": 116.0, "lat": 24.0, "cum_km": 600.0},
                           {"lng": 118.1, "lat": 24.46, "cum_km": 870.0}]}

    seen_near = []

    async def fake_search(keyword, near=None, **kw):
        seen_near.append((near.lat, near.lng))
        return [POI(id="s", name=f"沿途充电站@{near.cum_km if hasattr(near,'cum_km') else near.lat}",
                    address="高速服务区", rating=4.5)]

    p._poi.get_route = fake_route
    p._poi.search = fake_search
    plan = asyncio.run(p.plan_route(
        "厦门火车站", soc="50%", meta={"current_lat": "22.5", "current_lng": "113.8"}))
    assert "870" in plan.summary                       # 真实全程里程
    assert "公里处" in plan.summary                     # 途经点带"约N公里处"位置
    assert plan.stops and "at_km" in plan.stops[0]      # 途经点带里程
    assert len(seen_near) >= 1                          # 在沿途坐标搜站（不是目的地附近）


def test_amap_charging_plan_direct_when_range_enough():
    """续航足够 → 直达、无途经点。"""
    from agents.charging_planner.src.providers.amap import AmapChargingProvider

    p = AmapChargingProvider(key="test-key")

    async def fake_route(o, d, meta=None, with_polyline=False):
        return {"distance_km": 120.0, "duration_min": 90.0, "steps": [],
                "points": [{"lng": 114.0, "lat": 23.0, "cum_km": 120.0}]}

    p._poi.get_route = fake_route
    plan = asyncio.run(p.plan_route(
        "近郊", soc="80%", meta={"current_lat": "22.5", "current_lng": "113.8"}))
    assert "足够直达" in plan.summary and plan.stops == []


def test_plan_emits_charging_route_card_with_waypoints():
    """charging.plan 出 charging_route 卡（出发地→途经点→目的地），含里程与途经充电站。"""
    from agents.charging_planner.src.providers.base import ChargingPlan
    agent = ChargingPlannerAgent()

    async def fake_plan(destination, soc="", meta=None):
        return ChargingPlan(
            summary="前往X，全程约613公里，途中补电1次：约212公里处·南网充电站",
            stops=[{"name": "南网充电站", "address": "服务区", "at_km": 212, "charge_to": "80%"}],
            total_duration_min=382, distance_km=613.1)

    agent.charging.plan_route = fake_plan
    ctx = make_context(context_values={"vehicle.battery": "50%"})
    res = asyncio.run(run_handle(
        agent, "charging.plan", slots={"destination": "X"}, raw_text="规划充电", ctx=ctx))
    assert res.ui_card and res.ui_card["type"] == "charging_route"
    assert res.ui_card["destination"] == "X"
    assert res.ui_card["distance_km"] == 613.1
    assert res.ui_card["stops"][0]["at_km"] == 212


def test_is_vague_destination_heuristic():
    """行政区划级目的地（市/省/区/县…）判为过泛；带具体 POI 后缀的不算。"""
    f = ChargingPlannerAgent._is_vague_destination
    assert f("甘肃省兰州市") is True
    assert f("兰州市") is True
    assert f("朝阳区") is True
    assert f("云霄县") is True
    assert f("兰州西站") is False
    assert f("人民广场") is False
    assert f("解放路123号") is False
    assert f("") is False


def test_plan_confirms_vague_destination():
    """目的地过泛（兰州市）→ 先 NEED_SLOT 二次确认具体地点，不直接规划/不编路线。"""
    ctx = make_context(context_values={"vehicle.battery": "50%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.plan",
        slots={"destination": "甘肃省兰州市"}, raw_text="去兰州市规划充电", ctx=ctx))
    assert res.status == "need_slot"
    assert "destination" in res.missing_slots
    assert "兰州" in res.speech and res.ui_card is None


def test_plan_specific_destination_not_blocked():
    """具体地点（带 POI 后缀）不触发二次确认，直接进入规划（mock → advisory OK）。"""
    ctx = make_context(context_values={"vehicle.battery": "50%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.plan",
        slots={"destination": "兰州西站"}, raw_text="去兰州西站规划充电", ctx=ctx))
    assert res.status == "ok"


def test_plan_vague_destination_offers_amap_candidates():
    """泛地点 + 有高德候选 → 出 dest_choice 候选卡（供『第N个』/说名称回填），不直接规划。"""
    agent = ChargingPlannerAgent()

    async def fake_suggest(query, meta=None):
        return [{"id": "0", "name": "兰州市", "address": ""},          # 行政区划自身，应被过滤
                {"id": "1", "name": "兰州站", "address": "城关区"},
                {"id": "2", "name": "兰州西站", "address": "七里河区"}]

    agent.charging.suggest_destinations = fake_suggest
    ctx = make_context(context_values={"vehicle.battery": "50%"})
    res = asyncio.run(run_handle(
        agent, "charging.plan", slots={"destination": "甘肃省兰州市"},
        raw_text="去兰州市规划充电", ctx=ctx))
    assert res.status == "need_slot" and "destination" in res.missing_slots
    assert res.ui_card and res.ui_card["type"] == "poi_list"
    assert res.ui_card.get("purpose") == "dest_choice"
    assert [i["name"] for i in res.ui_card["items"]] == ["兰州站", "兰州西站"]
    assert "兰州站" in res.speech


def test_amap_suggest_destinations_from_poi_search():
    """高德候选 = 用核心地名（去省/市后缀）搜 POI，返回真实候选地点。"""
    from agents.charging_planner.src.providers.amap import AmapChargingProvider
    from agents.navigation.src.providers.base import POI

    p = AmapChargingProvider(key="test-key")
    seen = []

    async def fake_search(keyword, near=None, **kw):
        seen.append(keyword)
        return [POI(id="1", name="兰州站", address="城关区"),
                POI(id="2", name="兰州西站", address="七里河区")]

    p._poi.search = fake_search
    out = asyncio.run(p.suggest_destinations("甘肃省兰州市"))
    assert [c["name"] for c in out] == ["兰州站", "兰州西站"]
    assert seen and "兰州" in seen[0] and "省" not in seen[0]   # 用核心地名搜


def test_amap_charging_plan_requires_location():
    """无定位 → 诚实说明需要当前位置，不编造路线/站点。"""
    from agents.charging_planner.src.providers.amap import AmapChargingProvider
    p = AmapChargingProvider(key="test-key")
    plan = asyncio.run(p.plan_route("厦门火车站", soc="50%", meta={}))
    assert "定位" in plan.summary or "当前位置" in plan.summary
    assert plan.stops == []


def test_plan_does_not_fabricate_specific_stations():
    """规划诚实：mock 无真实数据时不编造具体服务区名/总时长（旧 bug：嘉兴/145分钟）。"""
    ctx = make_context(context_values={"vehicle.battery": "45%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.plan",
        slots={"destination": "厦门火车站"}, raw_text="去厦门怎么充电", ctx=ctx))
    assert "嘉兴" not in res.speech and "杭州东" not in res.speech
    assert "分钟" not in res.speech            # 不报无法计算的精确总时长
    assert "厦门火车站" in res.speech


def test_plan_needs_destination():
    """charging.plan 无目的地 → NEED_SLOT"""
    ctx = make_context(context_values={"vehicle.battery": "45%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.plan",
        slots={}, raw_text="帮我规划充电", ctx=ctx))
    assert res.status == "need_slot"
    assert "destination" in res.missing_slots


def test_status_returns_battery():
    """charging.status → OK + battery data"""
    ctx = make_context(context_values={"vehicle.battery": "72%"})
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.status",
        slots={}, raw_text="现在电量多少", ctx=ctx))
    assert res.status == "ok"
    assert "72%" in res.speech


def test_find_provider_outage_degrades_honestly_no_mock():
    """M0a-2（架构 §9.5 铁律③）：真实源运行期失败 → 诚实降级 OK 话术，
    绝不改供 mock 假充电站（假站可能被用户导航过去）；不出假列表卡。"""
    from agents._sdk.http import ProviderError
    agent = ChargingPlannerAgent()
    async def _fail(*a, **kw):
        raise ProviderError("provider down")
    agent.charging.find_nearby = _fail
    ctx = make_context(context_values={"vehicle.battery": "72%", "vehicle.location": "科技园"})
    res = asyncio.run(run_handle(
        agent, "charging.find",
        slots={}, raw_text="找个充电站", ctx=ctx))
    assert res.status == "ok"          # R9 契约：诚实降级用 OK（FAILED 话术会被聚合器吞）
    assert res.ui_card is None         # 没有假列表
    assert "暂时" in res.speech


def test_find_near_destination_outage_degrades_honestly_no_mock():
    """按目的地搜充电站真实源失败 → 诚实说拿不到（到达后再找），不喂 mock 站、无途经点契约。"""
    from agents._sdk.http import ProviderError
    agent = ChargingPlannerAgent()
    async def _fail(*a, **kw):
        raise ProviderError("amap 5xx")
    agent.charging.find_nearby = _fail
    ctx = make_context(context_values={"vehicle.battery": "60%"})
    res = asyncio.run(run_handle(
        agent, "charging.find", slots={"destination": "兰州西站"},
        raw_text="导航去兰州西站，附近找个充电桩", ctx=ctx))
    assert res.status == "ok"
    assert res.ui_card is None
    assert "暂时" in res.speech and "兰州西站" in res.speech
    assert not (res.data or {}).get("waypoint")       # 不产假途经点


def test_plan_provider_outage_degrades_honestly_no_mock():
    """charging.plan 真实源失败 → 诚实降级 OK 话术，不出 mock 假路线卡。"""
    from agents._sdk.http import ProviderError
    agent = ChargingPlannerAgent()
    async def _fail(*a, **kw):
        raise ProviderError("amap route 5xx")
    agent.charging.plan_route = _fail
    ctx = make_context(context_values={"vehicle.battery": "50%"})
    res = asyncio.run(run_handle(
        agent, "charging.plan", slots={"destination": "兰州西站"},
        raw_text="去兰州西站规划充电", ctx=ctx))
    assert res.status == "ok"
    assert res.ui_card is None
    assert "暂时" in res.speech


def test_no_runtime_mock_fallback_field():
    """mock 兜底 provider 字段已随 §9.5 铁律③整改移除——结构上不可能运行期回退 mock。"""
    assert not hasattr(ChargingPlannerAgent(), "_fallback")


def test_unsupported_intent():
    """不支持的意图 → FAILED"""
    ctx = make_context()
    res = asyncio.run(run_handle(
        ChargingPlannerAgent(), "charging.unknown",
        slots={}, raw_text="xxx", ctx=ctx))
    assert res.status == "failed"


def test_resolve_soc_prefers_meta_battery_over_memory():
    """充电规划优先用边端注入的真实电量(meta.vehicle_battery)，不用 memory 默认/陈旧值。"""
    agent = ChargingPlannerAgent()
    ctx = make_context(context_values={"vehicle.battery": "50%"})  # memory 旧/默认
    soc = asyncio.run(agent._resolve_soc(ctx, {"vehicle_battery": "72"}))
    assert soc == "72"                                              # 取边端真实电量
    soc2 = asyncio.run(agent._resolve_soc(ctx, {}))
    assert soc2 == "50%"                                           # 无 meta 时回退 memory


def test_find_bare_city_destination_asks_choice():
    """R1（旅程 B2-3/B5-2）：「去惠州的路上找充电站」——裸城市名（无 市 后缀）经
    geocode level 判定为过泛 → dest_choice 澄清，不吃就近关键词匹配（0.3km「惠州出口」）。"""
    import asyncio
    from agents._sdk.testing import run_handle
    agent = ChargingPlannerAgent()

    class _PoiStub:
        async def geocode_level(self, address, meta=None):
            return ("市", "114.416,23.111") if address == "惠州" else ("", "")

    class _ChargingStub:
        _poi = _PoiStub()

        async def suggest_destinations(self, dest, meta=None):
            return [{"id": "1", "name": "惠州西湖", "address": "惠城区"},
                    {"id": "2", "name": "惠州站", "address": "惠城区"}]

    agent.charging = _ChargingStub()
    res = asyncio.run(run_handle(
        agent, "charging.find", slots={"destination": "惠州"},
        raw_text="去惠州的路上帮我找个充电站"))
    assert res.status == "need_slot"
    assert res.ui_card and res.ui_card.get("purpose") == "dest_choice"
    assert "惠州西湖" in (res.speech or "")


def test_find_specific_destination_not_blocked_by_level_probe():
    """具体地点（探测返回兴趣点级）不受 R1 城市门影响，照常按目的地搜途经点。"""
    import asyncio
    from agents._sdk.testing import run_handle
    from agents.charging_planner.src.providers.base import ChargingStation
    agent = ChargingPlannerAgent()

    class _PoiStub:
        async def geocode_level(self, address, meta=None):
            return "兴趣点", "113.9,22.5"

    stations = [ChargingStation(id="c1", name="真充电站", lat=22.5, lng=113.9,
                                distance_km=0.5, available=0, total=0)]

    class _ChargingStub:
        _poi = _PoiStub()

        async def find_nearby(self, near, charger_type="", meta=None):
            return stations

    agent.charging = _ChargingStub()
    res = asyncio.run(run_handle(
        agent, "charging.find", slots={"destination": "会展中心"},
        raw_text="去会展中心的路上找个充电站"))
    assert res.status == "ok"
    assert "真充电站" in (res.speech or "")


def test_dest_ordinal_resolved_from_choices():
    """B2-3 尾巴：destination=「第一个」按上一轮 dest_choice 候选回填真名（消费即清）。"""
    import asyncio
    from agents._sdk.testing import make_context, run_handle
    from agents._sdk.shared_state import CHARGING_DEST_CHOICES
    from agents.charging_planner.src.providers.base import ChargingStation

    agent = ChargingPlannerAgent()
    seen = {}

    class _PoiStub:
        async def geocode_level(self, address, meta=None):
            return "兴趣点", "114.4,23.1"

    class _ChargingStub:
        _poi = _PoiStub()

        async def find_nearby(self, near, charger_type="", meta=None):
            seen["near_addr"] = getattr(near, "address", "")
            return [ChargingStation(id="c1", name="惠州站充电站", lat=23.1, lng=114.4,
                                    distance_km=0.4, available=0, total=0)]

    agent.charging = _ChargingStub()
    # make_context 的 shared_state 是 AsyncMock 不真存——钉进内存 dict（scene KV 同款）
    kv = {}
    ctx = make_context()

    async def _save(key, value):
        kv[key] = value
        return True

    async def _load(key):
        return kv.get(key)

    ctx.save_shared_state = _save
    ctx.load_shared_state = _load
    # 预置上一轮澄清写入的候选
    kv[CHARGING_DEST_CHOICES] = {"items": [{"name": "惠州站", "address": "惠城区"},
                                           {"name": "惠州西湖", "address": "惠城区"}]}
    res = asyncio.run(run_handle(
        agent, "charging.find", slots={"destination": "第一个"},
        raw_text="第一个", ctx=ctx))
    assert res.status == "ok"
    assert seen.get("near_addr") == "惠州站"            # 序号真回填成候选名（不许蒙混）
    assert kv.get(CHARGING_DEST_CHOICES) == {}          # 消费即清
