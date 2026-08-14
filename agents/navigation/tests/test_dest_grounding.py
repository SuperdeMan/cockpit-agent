"""R1 二期：目的地接地「就近包含误伤」家族回归（接地卡 2026-08-14）。

真栈红例：「虹桥机场」→如家酒店停车场、「外滩」→星空艺术馆(百货8层)、
「滴水湖」→雅悦酒店、「千岛湖」→上海的千岛湖鱼头馆、「西湖」→台湾苗栗西湖乡
（行政级分支被高德多义 geocode 带偏）。共因：包含式名字校验的隐含假设
「名字包含 ⇒ 是本体」被借名 POI 证伪，且本体根本不在 near 候选集里。
修法：类目锚词复核（机场/湖/滩）+ 候选集内双匹配 + wide 双匹配 + 区县级就近合理性。
POI 名字/类目均取自 2026-08-14 真高德取证（scratchpad grounding_probe）。
"""
import asyncio

from agents._sdk.testing import run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

SH = {"current_lat": "31.2317", "current_lng": "121.4692"}  # 上海人民广场（EVA 探针同款）


def _nav_dest(res):
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav, f"no navigate action: status={res.status} speech={res.speech}"
    return nav[0]["payload"]["destination"]


class _RecordingPoi:
    """按 near 有无返回不同候选集，记录调用序（near=True 周边距离序 / False 全国序）。"""

    def __init__(self, near_results, wide_results):
        self.near_results = near_results
        self.wide_results = wide_results
        self.calls = []

    async def search(self, keyword, near=None, **kw):
        self.calls.append((keyword, near is not None))
        return self.near_results if near is not None else self.wide_results


def test_category_anchor_parsing():
    """锚词判定：以锚词结尾且严格长于才触发；纯类目词（「机场」=就近语义）不触发。"""
    anchor = NavigationAgent._category_anchor
    assert anchor("虹桥机场") == ("机场", ("机场",))
    assert anchor("滴水湖") == ("湖", ("风景名胜", "自然地名", "热点地名"))
    assert anchor("外滩") == ("滩", ("风景名胜", "自然地名", "热点地名"))
    assert anchor("机场") is None          # 纯类目词维持就近距离序
    assert anchor("瑞幸咖啡") is None      # 无锚词零影响
    assert anchor("滴水湖雅悦酒店") is None  # 用户点名酒店不触发湖锚


def test_stem_match_bridges_official_name():
    """主干级：「虹桥机场」与「上海虹桥国际机场」隔着「国际」不构成连续包含——
    专名主干（虹桥）+ 类目（机场）双匹配把官方名接上；类目失配的沾边名仍拒。"""
    g = NavigationAgent._grounds_to
    airport = POI(id="a", name="上海虹桥国际机场", category="交通设施服务;机场相关;飞机场")
    assert g("虹桥机场", airport, "机场", ("机场",)) is True
    hub = POI(id="b", name="虹桥智谷党群服务站",
              category="政府机构及社会团体;政府及社会团体相关;政府及社会团体相关")
    assert g("虹桥机场", hub, "机场", ("机场",)) is False  # 名字沾边、类目失配
    other = POI(id="c", name="北京首都国际机场", category="交通设施服务;机场相关;飞机场")
    assert g("虹桥机场", other, "机场", ("机场",)) is False  # 类目对、主干不匹配


def test_borrowed_name_poi_rejected_wide_rescue():
    """「虹桥机场」：near 候选全是借名（停车场/加油站，名字包含放行是老 bug）且集内
    无本体 → 类目失配走去偏置重搜，wide 里选到机场本体。"""
    poi = _RecordingPoi(
        near_results=[
            POI(id="n1", name="如家快捷酒店上海虹桥机场世贸会展中心店地面停车场",
                category="交通设施服务;停车场;公共停车场", lat=31.19, lng=121.35),
            POI(id="n2", name="中国航油虹桥机场第2加油站",
                category="汽车服务;加油站;加油站", lat=31.20, lng=121.34),
        ],
        wide_results=[
            POI(id="w1", name="上海虹桥国际机场",
                category="交通设施服务;机场相关;飞机场", lat=31.1979, lng=121.3363),
        ])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "虹桥机场"},
        raw_text="导航去虹桥机场", meta=SH))
    assert res.status == "ok"
    assert _nav_dest(res) == "上海虹桥国际机场"  # 官方名，不是借名停车场
    assert "上海虹桥国际机场" in res.speech
    assert "如家" not in res.speech
    assert ("虹桥机场", False) in poi.calls  # 确实做了去偏置重搜


def test_lake_dual_match_inside_candidates_zero_api():
    """「东湖」：#2 东湖绿地名字+类目双匹配 → 集内重排，不做全国重搜
    （全国序 top1 是 500km 外的武汉东湖，就近合理性反而更差）。"""
    poi = _RecordingPoi(
        near_results=[
            POI(id="n1", name="东湖公寓", category="商务住宅;住宅区;住宅小区",
                lat=31.21, lng=121.44),
            POI(id="n2", name="东湖绿地", category="风景名胜;公园广场;公园",
                lat=31.21, lng=121.45),
        ],
        wide_results=[
            POI(id="w1", name="东湖生态旅游风景区",
                category="风景名胜;风景名胜;国家级景点", lat=30.55, lng=114.41),
        ])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "东湖"},
        raw_text="导航去东湖", meta=SH))
    assert res.status == "ok"
    assert "东湖绿地" in res.speech
    assert ("东湖", False) not in poi.calls  # 集内命中，零额外 API


def test_wide_picks_dual_match_not_top1():
    """「滴水湖」：wide 全国序 top1 是同名地铁站（名字匹配、类目失配）——锚词在场时
    按双匹配扫列表选湖本体，不被同名亲戚卡住。"""
    poi = _RecordingPoi(
        near_results=[
            POI(id="n1", name="滴水湖雅悦酒店(上海老芦公路店)",
                category="住宿服务;住宿服务相关;住宿服务相关", lat=30.90, lng=121.93),
        ],
        wide_results=[
            POI(id="w1", name="滴水湖(地铁站)",
                category="交通设施服务;地铁站;地铁站", lat=30.91, lng=121.94),
            POI(id="w2", name="滴水湖",
                category="风景名胜;风景名胜;风景名胜", lat=30.9080, lng=121.9420),
        ])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "滴水湖"},
        raw_text="带我去滴水湖", meta=SH))
    assert res.status == "ok"
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert abs(nav[0]["payload"]["lat"] - 30.9080) < 1e-6  # 湖本体，不是地铁站
    assert "雅悦酒店" not in res.speech


def test_no_anchor_top1_accepted_unchanged():
    """守卫：「瑞幸咖啡」无锚词——就近门店 top1 直接接受，零重搜（用户要的就是最近店）。"""
    poi = _RecordingPoi(
        near_results=[
            POI(id="n1", name="瑞幸咖啡(仙乐斯广场店)",
                category="餐饮服务;咖啡厅;咖啡厅", lat=31.2318, lng=121.4690),
        ],
        wide_results=[
            POI(id="w1", name="瑞幸咖啡", category="餐饮服务;咖啡厅;咖啡厅",
                lat=39.9, lng=116.4),  # 全国序会给外地店——绝不能碰
        ])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "瑞幸咖啡"},
        raw_text="导航去瑞幸咖啡", meta=SH))
    assert res.status == "ok"
    assert "仙乐斯" in res.speech
    assert ("瑞幸咖啡", False) not in poi.calls


def test_anchor_top1_pass_keeps_distance_order():
    """守卫：锚词命中且 top1 类目正确（真在湖边）→ 保持距离序，零重搜。"""
    poi = _RecordingPoi(
        near_results=[
            POI(id="n1", name="金鸡湖景区", category="风景名胜;风景名胜;国家级景点",
                lat=31.31, lng=120.71),
        ],
        wide_results=[])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "金鸡湖"},
        raw_text="导航去金鸡湖", meta=SH))
    assert res.status == "ok"
    assert "金鸡湖景区" in res.speech
    assert ("金鸡湖", False) not in poi.calls


class _AdminPoi(_RecordingPoi):
    def __init__(self, level, loc, near_results=None, wide_results=None):
        super().__init__(near_results or [], wide_results or [])
        self._level, self._loc = level, loc

    async def geocode_level(self, address, meta=None):
        return self._level, self._loc


def test_admin_county_far_falls_through_to_search():
    """「西湖」：高德 geocode 给出台湾苗栗西湖乡（区县级、700km 外）——区县级多义
    不可信 → fall through 关键词搜索，湖锚校验接到杭州西湖（真栈取证实测形态）。"""
    poi = _AdminPoi(
        "区县", "120.759424,24.557418",
        near_results=[
            POI(id="n1", name="西湖公寓", category="商务住宅;住宅区;住宅小区",
                lat=31.25, lng=121.45),
            POI(id="n2", name="西湖龙井(中山北路店)", category="购物服务;专卖店;专营店",
                lat=31.25, lng=121.44),
        ],
        wide_results=[
            POI(id="w1", name="杭州西湖风景名胜区",
                category="风景名胜;风景名胜;国家级景点", lat=30.2430, lng=120.1500),
        ])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "西湖"},
        raw_text="导航去西湖", meta=SH))
    assert res.status == "ok"
    assert "杭州西湖" in res.speech
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert abs(nav[0]["payload"]["lat"] - 30.2430) < 1e-6  # 不是台湾苗栗 24.56


def test_admin_county_nearby_still_trusted():
    """守卫：区县级但确实在本地（≤150km）→ 行政中心直达维持（「导航去嘉定」形态）。"""
    poi = _AdminPoi("区县", "121.2655,31.3747")  # 嘉定区，距人民广场约 20km
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "嘉定"},
        raw_text="导航去嘉定", meta=SH))
    assert res.status == "ok"
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and abs(nav[0]["payload"]["lat"] - 31.3747) < 1e-6
    assert not poi.calls  # 行政直达，没做关键词搜索


def test_admin_city_level_unconditional_cross_city():
    """守卫：市级唯一性好，跨城导航合法（上海说「导航去佛山」1200km 照样直达）。"""
    poi = _AdminPoi("市", "113.121586,23.021351")
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "佛山"},
        raw_text="导航去佛山", meta=SH))
    assert res.status == "ok"
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and abs(nav[0]["payload"]["lat"] - 23.021351) < 1e-6


def test_admin_county_no_location_falls_through():
    """区县级 + 无定位：没有就近合理性可判 → 宁走全国关键词序（top1 杭州西湖），
    不信 geocode 的多义唯一解（导去台湾比给弱候选糟得多）。"""
    poi = _AdminPoi(
        "区县", "120.759424,24.557418",
        wide_results=[
            POI(id="w1", name="杭州西湖风景名胜区",
                category="风景名胜;风景名胜;国家级景点", lat=30.2430, lng=120.1500),
        ])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "西湖"},
        raw_text="导航去西湖", meta={}))
    assert res.status == "ok"
    assert "杭州西湖" in res.speech
