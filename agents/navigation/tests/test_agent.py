"""navigation 契约测试（黄金用例）。不起 gRPC server，直接驱动 handle。"""
import asyncio
from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI


def test_nearby_search_uses_session_location_coordinates():
    agent = NavigationAgent()
    seen = {}

    async def search(keyword, near=None, **kwargs):
        seen["near"] = near
        return [POI(id="poi-1", name="附近咖啡", lat=39.93, lng=116.42)]

    agent.poi.search = search
    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "咖啡"}, raw_text="附近咖啡",
        ctx=make_context(), meta={"current_lat": "39.92", "current_lng": "116.41"}))

    assert res.status == "ok"
    assert seen["near"].lat == 39.92 and seen["near"].lng == 116.41

class _ScriptedPoiProvider:
    def __init__(self, responses=None, default=None):
        self.responses = responses or {}
        self.default = [] if default is None else default
        self.queries = []

    async def search(self, keyword, **kwargs):
        self.queries.append(keyword)
        return self.responses.get(keyword, self.default)


def _poi(name: str) -> POI:
    return POI(id="landmark-1", name=name, address="深圳市南山区", lat=22.50, lng=113.94)


def _async_return(value):
    async def fake_complete(*args, **kwargs):
        return value
    return fake_complete


def test_search_poi_returns_card():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.search_poi",
        slots={"keyword": "充电站"}, raw_text="附近的充电站"))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card["type"] == "poi_list"
    assert len(res.ui_card["items"]) >= 1


def test_search_poi_missing_keyword_asks():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.search_poi", slots={}, raw_text="找个地方"))
    assert res.status == "need_slot"


def test_navigate_to_emits_action():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to",
        slots={"destination": "首都机场"}, raw_text="导航去首都机场"))
    assert res.status == "ok"
    assert any(a["type"] == "navigate" for a in res.actions)


def test_navigate_to_attaches_granted_current_location_as_origin():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to",
        slots={"destination": "\u9996\u90fd\u673a\u573a"}, raw_text="\u5bfc\u822a\u53bb\u9996\u90fd\u673a\u573a",
        meta={"current_lat": "39.92", "current_lng": "116.41"}))

    payload = res.actions[0]["payload"]
    assert payload["origin_lat"] == 39.92
    assert payload["origin_lng"] == 116.41


def test_navigate_to_missing_dest_asks():
    res = asyncio.run(run_handle(
        NavigationAgent(), "navigation.navigate_to", slots={}, raw_text="导航"))
    assert res.status == "need_slot"


def test_navigate_to_resolves_and_validates_visual_landmark():
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "深圳笋一样的建筑物": [],
        "华润大厦": [_poi("华润大厦")],
    })
    agent.llm.complete = _async_return('["华润大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳笋一样的建筑物"}, raw_text="去深圳笋一样的建筑物"))

    assert agent.poi.queries == ["深圳笋一样的建筑物", "华润大厦"]
    assert res.actions[0]["payload"]["destination"] == "华润大厦"


def test_navigate_to_reasks_when_no_landmark_candidate_is_validated():
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[])
    agent.llm.complete = _async_return('["不存在的地标"]')

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "某个像飞船的建筑"}, raw_text="导航到某个像飞船的建筑"))

    assert res.status == "need_slot"
    assert res.actions == []


def test_search_poi_resolves_visual_landmark_from_raw_text_and_navigates():
    """Planner 可能错误抽出普通关键词，导航 Agent 仍应使用原话解析地标。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "笋岗": [],
        "华润大厦": [_poi("华润大厦")],
    })
    agent.llm.complete = _async_return('["华润大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "笋岗"},
        raw_text="去深圳笋一样的建筑物"))

    assert agent.poi.queries == ["笋岗", "华润大厦"]
    assert res.actions[0]["type"] == "navigate"
    assert res.actions[0]["payload"]["destination"] == "华润大厦"


def test_search_poi_prefers_validated_landmark_over_misparsed_keyword_result():
    """视觉地标描述不能被 Planner 抽出的同名普通 POI 抢占。"""
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({
        "笋岗": [_poi("笋岗地铁站")],
        "中国华润大厦": [_poi("中国华润大厦")],
    })
    agent.llm.complete = _async_return('["中国华润大厦"]')

    res = asyncio.run(run_handle(
        agent, "navigation.search_poi", slots={"keyword": "笋岗"},
        raw_text="去深圳笋一样的建筑物"))

    assert agent.poi.queries == ["笋岗", "中国华润大厦"]
    assert res.actions[0]["payload"]["destination"] == "中国华润大厦"


def test_visual_landmark_detection_does_not_promote_ordinary_navigation():
    assert NavigationAgent._is_visual_landmark_description("导航到上海船型的建筑物")
    assert not NavigationAgent._is_visual_landmark_description("去深圳万象城")


def test_landmark_resolution_passes_original_utterance_to_model():
    """视觉比喻的细节不能被拼接提示词改写后丢失。"""
    agent = NavigationAgent()
    seen = {}

    async def fake_complete(messages, **kwargs):
        seen["messages"] = messages
        return '["中国华润大厦"]'

    agent.llm.complete = fake_complete
    raw = "去深圳笋一样的建筑物"

    candidates = asyncio.run(agent._landmark_candidates(raw))

    assert candidates == ["中国华润大厦"]
    assert seen["messages"][-1] == {"role": "user", "content": raw}
