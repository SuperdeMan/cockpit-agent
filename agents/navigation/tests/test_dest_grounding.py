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


# ── 校园族（person-pickup 卡，2026-08-20）：与「虹桥机场」逐字同构 ──────────
#
# POI 名字/类目取自 2026-08-20 真高德取证（云端 navigation-agent 容器直连）：
#   「南山实验小学」near=当前位置 → 五条全是深圳的学校（鼎太/深湾/海滨/南头…）
#   「南山实验小学」near=None     → **只有**「济南市南山实验小学」1579km
# ⇒ 「接孩子接到济南」不是高德乱返回，是**我们自己那条去偏置全国重搜**捞上来的。

SZ = {"current_lat": "22.5410", "current_lng": "113.9412"}   # 深圳南山（QA 探针同款）

_SZ_SCHOOLS = [
    POI(id="n1", name="深圳市南山实验教育集团鼎太小学",
        category="科教文化服务;学校;小学", lat=22.5361, lng=113.9285),
    POI(id="n2", name="深圳市海滨实验小学",
        category="科教文化服务;学校;小学", lat=22.5290, lng=113.9330),
]
_JINAN_SCHOOL = POI(id="w1", name="济南市南山实验小学",
                    category="科教文化服务;科教文化场所;科教文化场所",
                    lat=36.6512, lng=117.1201)


def test_school_anchor_parsing():
    anchor = NavigationAgent._category_anchor
    assert anchor("南山实验小学") == ("小学", ("学校", "小学"))
    assert anchor("南山外国语学校") == ("学校", ("学校",))
    assert anchor("小学") is None            # 纯类目词维持就近距离序
    assert anchor("学校") is None


def test_school_stem_match_beats_the_nationwide_namesake():
    """真栈红例：「南山实验小学」→ 济南（1579km）。

    正主是 `results[0]`，与官方名隔着「教育集团」不构成连续包含 ⇒ 严格校验够不着；
    主干「南山实验」+ 类目「小学」双匹配够得着。**必须零额外 API**——
    走到去偏置全国重搜就已经错了。
    """
    poi = _RecordingPoi(near_results=_SZ_SCHOOLS, wide_results=[_JINAN_SCHOOL])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "南山实验小学"},
        raw_text="带我去接孩子放学", meta=SZ))
    assert res.status == "ok"
    assert _nav_dest(res) == "深圳市南山实验教育集团鼎太小学"
    assert ("南山实验小学", False) not in poi.calls    # 没做全国重搜


def test_nationwide_namesake_is_rejected_when_the_rescue_runs():
    """第二道：近侧全是借名 POI ⇒ 真跑去偏置全国重搜，而济南那条也过不了类目复核
    （它的高德类目是「科教文化场所」，不含「学校/小学」）⇒ **不许被当成正主**。

    修前这一步正是 1579km 的来源：严格包含放行、类目无人复核。
    ⚠ 原话刻意**不写成接送句**：那会触发 person-pickup 兜底、把读数变成教学问，
    验的就不是接地这一段了（首版就这么写的，测试当场按住）。
    """
    borrowed = POI(id="n1", name="南山实验小学家长接送临时停车区",
                   category="交通设施服务;停车场;路边停车场",
                   lat=22.5361, lng=113.9285)
    poi = _RecordingPoi(near_results=[borrowed], wide_results=[_JINAN_SCHOOL])
    agent = NavigationAgent()
    agent.poi = poi
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "南山实验小学"},
        raw_text="导航去南山实验小学", meta=SZ))
    assert ("南山实验小学", False) in poi.calls    # 重搜确实跑了……
    assert "济南" not in _nav_dest(res)            # ……只是没被采信


# ── 名字对不上的弱匹配不许兜底到另一座城（评审四轮真栈顺带发现，2026-09-25）────────────
#
# `bda5af71` 真栈 CL1 / RS33：深圳定位下「云岚国际中心」近侧搜索、全国重搜都只捞回北京的
# 「云岚之境美容美体中心」（39.858012, 116.444415，约 1950 km 直线）；名字校验不过、地标解析也解不出
# ⇒ 兜底照样当目的地，出发去全程约 2400 km、46 小时的路线。POI 取自那一轮的 navigate payload。

_BEIJING_SALON = POI(id="b1", name="云岚之境美容美体中心", address="北京城区鑫源国际1号楼603",
                     category="生活服务;美容美发店;美容美发店", lat=39.858012, lng=116.444415)


class _NoLandmarkLlm:
    """地标解析给不出候选（真栈那一轮同样解不出）。"""

    async def complete(self, *args, **kwargs):
        return "[]"


def _agent_with(poi):
    agent = NavigationAgent()
    agent.poi = poi
    agent.llm = _NoLandmarkLlm()
    return agent


def test_an_unverified_match_in_another_city_is_asked_about_not_driven_to():
    poi = _RecordingPoi(near_results=[_BEIJING_SALON], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.navigate_to", slots={"destination": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta=SZ))
    assert not [a for a in res.actions if a["type"] == "navigate"], res.actions
    assert res.status == "need_slot", (res.status, res.speech)
    assert "云岚之境" not in res.speech
    assert "请补充城市" in (res.follow_up or "")


def test_an_unverified_local_match_still_navigates_under_its_real_name():
    """对照（修前行为不变）：本地半径内的弱匹配照旧当目的地，话术报出实际名让用户纠正。"""
    local = POI(id="l1", name="云岚美容(南山店)", category="生活服务;美容美发店;美容美发店",
                lat=22.5361, lng=113.9285)
    poi = _RecordingPoi(near_results=[local], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.navigate_to", slots={"destination": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta=SZ))
    assert res.status == "ok", (res.status, res.speech)
    assert _nav_dest(res) == "云岚美容(南山店)"


def test_a_verified_destination_in_another_city_still_navigates():
    """对照：名字对得上的长途照常跨城导航（PU8「导航去上海外滩」同形）。"""
    tower = POI(id="t1", name="东方明珠广播电视塔", category="风景名胜;风景名胜;国家级景点",
                lat=31.2397, lng=121.4998)
    poi = _RecordingPoi(near_results=[tower], wide_results=[tower])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.navigate_to", slots={"destination": "东方明珠"},
        raw_text="导航去东方明珠", meta=SZ))
    assert res.status == "ok", (res.status, res.speech)
    assert _nav_dest(res) == "东方明珠广播电视塔"


def test_an_unverified_landmark_description_match_in_another_city_is_not_driven_to():
    """地标描述那一路的兜底（地标候选验证不出来 ⇒ 退回原话直搜）同一道闸。"""
    poi = _RecordingPoi(near_results=[_BEIJING_SALON], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.navigate_to", slots={"destination": "像云一样的大楼"},
        raw_text="导航去像云一样的大楼", meta=SZ))
    assert not [a for a in res.actions if a["type"] == "navigate"], res.actions
    assert res.status == "need_slot", (res.status, res.speech)


def test_without_a_location_the_fallback_is_unchanged():
    """边界：没有定位就判不了「在不在本地」——照旧报出实际名（修前行为）。"""
    poi = _RecordingPoi(near_results=[_BEIJING_SALON], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.navigate_to", slots={"destination": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta={}))
    assert res.status == "ok", (res.status, res.speech)
    assert _nav_dest(res) == "云岚之境美容美体中心"


class _SearchPoi(_RecordingPoi):
    """`search_poi` 走 `poi.search(keyword, near=…, rating_min=…)`：同一份近侧 / 全国结果。"""


def test_search_poi_with_a_navigation_phrase_does_not_drive_to_an_unverified_far_match():
    """同一个北京问题的第二个入口：规划把「导航去X」落成 `search_poi` 时，带导航词的原话会自动导到第一个结果。"""
    poi = _SearchPoi(near_results=[_BEIJING_SALON], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"keyword": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta=SZ))
    assert not [a for a in res.actions if a["type"] == "navigate"], res.actions
    assert "请补充城市" in (res.follow_up or ""), res.follow_up


def test_search_poi_with_nothing_found_says_so():
    """`bda5af71` RS36 第 2 趟逐字：「为您找到 0 个云岚国际中心，推荐前三个：。需要导航过去吗？」+「可以说『导航去第一个』」。"""
    poi = _SearchPoi(near_results=[], wide_results=[])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"keyword": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta=SZ))
    assert "0 个" not in res.speech and "推荐前三个" not in res.speech, res.speech
    assert "云岚国际中心" in res.speech
    assert "第一个" not in (res.follow_up or "")
    assert "请补充城市" in (res.follow_up or ""), res.follow_up
    assert not res.actions


def test_search_poi_with_a_navigation_phrase_still_drives_to_a_verified_match():
    """对照：名字对得上（哪怕在另一座城）照旧直接导航。"""
    tower = POI(id="t1", name="东方明珠广播电视塔", category="风景名胜;风景名胜;国家级景点",
                lat=31.2397, lng=121.4998)
    poi = _SearchPoi(near_results=[tower], wide_results=[tower])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"keyword": "东方明珠"},
        raw_text="导航去东方明珠", meta=SZ))
    assert _nav_dest(res) == "东方明珠广播电视塔"


def test_search_poi_without_a_navigation_phrase_still_lists_what_it_found():
    """对照：只是搜（原话不带导航词）⇒ 照旧把找到的列出来，由用户挑。"""
    poi = _SearchPoi(near_results=[_BEIJING_SALON], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"keyword": "云岚国际中心"},
        raw_text="搜一下云岚国际中心", meta=SZ))
    assert not res.actions
    assert "云岚之境美容美体中心" in res.speech


# ── 地标解析那一路的宽松匹配（`2f8f92be` 真栈 RS36 第 2 / 3 趟）──────────────────────────
#
# 名字校验 / 全国重搜都没过之后走地标解析：模型把「云岚国际中心」原样当候选返回，候选检索的第一条是北京那家，
# `name_matches` 的「2 字公共子串」（云岚 / 中心）判它通过 ⇒ 被当成**已验证**的目的地，本地半径那道闸根本没机会看。

class _LandmarkLlm:
    def __init__(self, candidates):
        self._raw = __import__("json").dumps(candidates, ensure_ascii=False)

    async def complete(self, *args, **kwargs):
        return self._raw


def _agent_with_landmarks(poi, candidates):
    agent = NavigationAgent()
    agent.poi = poi
    agent.llm = _LandmarkLlm(candidates)
    return agent


def test_a_loose_landmark_match_in_another_city_is_not_driven_to():
    poi = _RecordingPoi(near_results=[_BEIJING_SALON], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["云岚国际中心"]), "navigation.navigate_to",
        slots={"destination": "云岚国际中心"}, raw_text="导航去云岚国际中心", meta=SZ))
    assert not [a for a in res.actions if a["type"] == "navigate"], res.actions
    assert res.status == "need_slot", (res.status, res.speech)


def test_a_loose_landmark_match_nearby_is_still_taken():
    """对照：俗称 → 官方名（「华润春笋大厦」→「中国华润大厦」只有宽松匹配）在本地照旧采信。"""
    tower = POI(id="c1", name="中国华润大厦", category="商务住宅;楼宇;商务写字楼", lat=22.5144, lng=113.9442)
    poi = _RecordingPoi(near_results=[POI(id="n1", name="华润万家(科技园店)", lat=22.54, lng=113.95)],
                        wide_results=[tower])
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["华润春笋大厦"]), "navigation.navigate_to",
        slots={"destination": "华润春笋大厦"}, raw_text="导航去华润春笋大厦", meta=SZ))
    assert _nav_dest(res) == "中国华润大厦"


def test_a_strict_landmark_match_in_another_city_still_navigates():
    """对照：地标描述解析成官方名、检索结果与它严格包含 ⇒ 跨城照常导航（「北京的大裤衩」从深圳出发）。"""
    cctv = POI(id="b2", name="中央电视台总部大楼", category="商务住宅;楼宇;商务写字楼", lat=39.9151, lng=116.4632)
    poi = _RecordingPoi(near_results=[], wide_results=[cctv])
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["中央电视台总部大楼"]), "navigation.navigate_to",
        slots={"destination": "北京的大裤衩"}, raw_text="导航去北京的大裤衩", meta=SZ))
    assert _nav_dest(res) == "中央电视台总部大楼"


class _KeywordPoi:
    """按关键词返回：原关键词搜不到、地标候选搜得到（`search_poi` 的候选检索与原检索都带 near）。"""

    def __init__(self, by_keyword):
        self.by_keyword = by_keyword

    async def search(self, keyword, near=None, **kw):
        return list(self.by_keyword.get(keyword, []))


def test_search_poi_does_not_auto_navigate_to_a_loose_landmark_match_in_another_city():
    """修前 `search_poi` 把「地标候选解析过」一律当已验证：候选与结果只有宽松匹配、又在北京 ⇒ 照样自动导过去。"""
    poi = _KeywordPoi({"云岚国际中心(北京)": [_BEIJING_SALON]})
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["云岚国际中心(北京)"]), "navigation.search_poi",
        slots={"keyword": "云岚国际中心"}, raw_text="导航去云岚国际中心", meta=SZ))
    assert not [a for a in res.actions if a["type"] == "navigate"], res.actions
    assert "请补充城市" in (res.follow_up or ""), res.follow_up


# ── 原话不是地标描述时，地标解析给的候选必须和原话沾边（`a7e664f3` 真栈 CT4 第 3 趟）──────────────
#
# 「附近的咖啡店」之后的「导航到第二家」被规划成 `search_poi {keyword: 第二家}`：搜不到 ⇒ 原话交给地标解析，模型从它提示里的示例
# （「苏州的大秋裤 → 东方之门」）猜了一个名胜；检索结果与这个候选严格包含，于是被当成已验证，出发去 1478 km 外的苏州。
# 地标解析本来是给「像船的建筑」这类描述用的；原话只是名字没对上时，候选若和原话一个字都不沾，它就是猜出来的。

_SUZHOU_GATE = POI(id="s1", name="东方之门", address="星融街与星港街辅路交叉口西南140米",
                   category="风景名胜;风景名胜相关;旅游景点", lat=31.3190, lng=120.6655)


def test_search_poi_does_not_drive_to_a_landmark_guessed_out_of_nothing():
    poi = _KeywordPoi({"东方之门": [_SUZHOU_GATE]})
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["东方之门"]), "navigation.search_poi",
        slots={"keyword": "第二家"}, raw_text="导航到第二家", meta=SZ))
    assert not [a for a in res.actions if a["type"] == "navigate"], res.actions


def test_navigate_to_does_not_drive_to_a_landmark_guessed_out_of_nothing():
    poi = _KeywordPoi({"东方之门": [_SUZHOU_GATE]})
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["东方之门"]), "navigation.navigate_to",
        slots={"destination": "第二家"}, raw_text="导航到第二家", meta=SZ))
    assert not [a for a in res.actions if a["type"] == "navigate"], res.actions
    assert res.status == "need_slot", (res.status, res.speech)


def test_a_visual_description_still_crosses_cities():
    """对照：原话就是地标描述（「像秋裤一样的大楼」）⇒ 那正是地标解析的用途，严格匹配照常跨城。"""
    poi = _KeywordPoi({"东方之门": [_SUZHOU_GATE]})
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["东方之门"]), "navigation.navigate_to",
        slots={"destination": "苏州那个像秋裤一样的大楼"}, raw_text="导航去苏州那个像秋裤一样的大楼", meta=SZ))
    assert _nav_dest(res) == "东方之门"


def test_a_guessed_candidate_that_shares_the_users_words_still_crosses_cities():
    """对照：名字没对上、地标解析给出的候选和原话沾边（「上海虹桥火车站」→「上海虹桥站」）⇒ 照常跨城。"""
    station = POI(id="h1", name="上海虹桥站", category="交通设施服务;火车站;火车站", lat=31.1944, lng=121.3200)
    poi = _KeywordPoi({"上海虹桥站": [station]})
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["上海虹桥站"]), "navigation.navigate_to",
        slots={"destination": "上海虹桥火车站"}, raw_text="导航去上海虹桥火车站", meta=SZ))
    assert _nav_dest(res) == "上海虹桥站"


def test_a_guess_after_an_unverified_nearby_result_does_not_cross_cities():
    """中间那个入口：近侧有结果但名字没对上 ⇒ 全国重搜也没对上 ⇒ 地标猜测。猜出来的东方之门不采信，退回本地弱匹配（照旧报实际名）。"""
    shop = POI(id="l2", name="美宜佳(华富洋大厦店)", category="购物服务;便利店;便利店", lat=22.5401, lng=113.9420)
    poi = _KeywordPoi({"第二家": [shop], "东方之门": [_SUZHOU_GATE]})
    res = asyncio.run(run_handle(
        _agent_with_landmarks(poi, ["东方之门"]), "navigation.navigate_to",
        slots={"destination": "第二家"}, raw_text="导航到第二家", meta=SZ))
    assert "东方之门" not in [a["payload"].get("destination") for a in res.actions if a["type"] == "navigate"]


# ── 「没找到」要接得住用户的下一句（`f535c654` 真栈 RS39 2/3）──────────────────────────────────────
#
# 「导航去云岚国际中心」被规划成 `search_poi` 时，批 E 的「没找到」只回一句话：话术说「请补充城市…我再为您定位」，却没留挂起——
# 用户接着说「深圳湾公园」成了一句全新的裸地名，被澄清成「你希望我怎么处理深圳湾公园？」。`navigate_to` 那条「没找到」一直是补槽追问，
# 所以导航语境下的「没找到」改派给它（挂起落在 navigate_to 上，补上的地名续接它、直接导航）。

_ESCALATE_TO_NAVIGATE = {"intent": "navigation.navigate_to", "slots": {"destination": "云岚国际中心"},
                         "reason": "search_not_found"}


def test_search_poi_with_a_navigation_phrase_that_finds_nothing_hands_over_to_navigate_to():
    poi = _SearchPoi(near_results=[], wide_results=[])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"keyword": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta=SZ))
    assert (res.data or {}).get("_escalate") == _ESCALATE_TO_NAVIGATE, res.data
    # 话术照留：不消费改派的路径（T2 循环）上仍是一句实话
    assert res.speech == "没找到「云岚国际中心」。" and "请补充城市" in (res.follow_up or "")
    assert not res.actions


def test_search_poi_refusing_a_far_guess_also_hands_over_to_navigate_to():
    poi = _SearchPoi(near_results=[_BEIJING_SALON], wide_results=[_BEIJING_SALON])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"keyword": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta=SZ))
    assert (res.data or {}).get("_escalate") == _ESCALATE_TO_NAVIGATE, res.data
    assert not res.actions


def test_search_poi_carrying_navigate_tos_destination_slot_searches_that_place():
    """真栈 `dabc2e2d` RS39 1/6：规划交出 `navigation.search_poi {destination: 云岚国际中心}`（自家 navigate_to 的槽）——
    修前没有 keyword ⇒「您想找什么类型的地点呢？」，而且那一问不声明缺哪个槽，答「深圳湾公园」写不进去、原样再问。"""
    poi = _SearchPoi(near_results=[], wide_results=[])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"destination": "云岚国际中心"},
        raw_text="导航去云岚国际中心", meta=SZ))
    assert (res.data or {}).get("_escalate") == _ESCALATE_TO_NAVIGATE, (res.status, res.speech, res.data)


def test_search_poi_without_any_place_asks_and_declares_the_keyword_slot():
    poi = _SearchPoi(near_results=[], wide_results=[])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={}, raw_text="帮我搜个地方", meta=SZ))
    assert res.status == "need_slot" and res.missing_slots == ["keyword"], (res.status, res.missing_slots)


def test_a_plain_search_that_finds_nothing_just_says_so():
    """对照：只是搜（不带导航词）⇒ 说没找到，不改派。"""
    poi = _SearchPoi(near_results=[], wide_results=[])
    res = asyncio.run(run_handle(
        _agent_with(poi), "navigation.search_poi", slots={"keyword": "云岚国际中心"},
        raw_text="搜一下云岚国际中心", meta=SZ))
    assert "_escalate" not in (res.data or {})
    assert "云岚国际中心" in res.speech
