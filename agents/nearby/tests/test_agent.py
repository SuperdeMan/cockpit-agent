"""nearby（周边发现）契约测试。"""
import asyncio
from types import SimpleNamespace

from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
from agents.nearby.src.agent import NearbyAgent

# 2026-08-13 起「附近发现」类检索**要求有搜索中心**（位置缺席不再全国检索冒充附近，
# demo-mkemhn 北京店三连）。发现类用例统一带车辆位置；位置缺席的行为有专门用例。
_LOC = {"current_lat": "22.54", "current_lng": "114.05"}


def test_manifest_consistent():
    assert assert_manifest_consistent(NearbyAgent()) is True


def test_manifest_separates_discovery_from_selected_place_detail():
    caps = {cap.intent: cap for cap in NearbyAgent().manifest.capabilities}
    search = caps["nearby.search"].description
    detail = caps["nearby.detail"].description

    assert "category" in search and "keyword" in search
    assert "已选中" in detail or "已引用" in detail
    assert "附近是否有" in detail and "nearby.search" in detail


def test_missing_food_slot_is_semantically_recovered_from_raw_text():
    intent = SimpleNamespace(slots={}, raw_text="看看附近有什么吃的")
    recovered = NearbyAgent._resolve_category(intent)

    assert NearbyAgent._build_keyword(recovered, "", "", "") == \
        NearbyAgent._build_keyword("餐饮", "", "", "")


def test_missing_restroom_slot_never_falls_back_to_food():
    for raw_text in ("附近有洗手间吗", "帮我找个卫生间", "最近的厕所在哪"):
        intent = SimpleNamespace(slots={}, raw_text=raw_text)
        recovered = NearbyAgent._resolve_category(intent)

        assert NearbyAgent._build_keyword(recovered, "", "", "") == "公共厕所"


def test_coarse_category_still_uses_restroom_semantics_from_raw_text():
    for category in ("公共设施", "生活服务"):
        intent = SimpleNamespace(
            slots={"category": category}, raw_text="附近有洗手间吗")
        recovered = NearbyAgent._resolve_category(intent)

        assert NearbyAgent._build_keyword(recovered, "", "", "") == "公共厕所"


def test_named_brand_in_keyword_is_not_widened_into_its_category():
    """用户点名的品牌不得被类目词吞掉（2026-08-12 demo-2goetq / trace e6a82c1d 实证）。

    planner 那轮只填了 keyword=「瑞幸咖啡」没填 brand，非餐饮类目分支用「咖啡厅」
    覆盖了它 → 高德返回一半非瑞幸门店，而同 plan 的 luckin.order 直接取 items.0
    当下单门店。品牌丢失在这里是**静默**的，只有看话术「找到 10 家咖啡厅」才发现。
    """
    # 槽值取自那一轮 planner 的真实输出（llm_raw: slots={"keyword": "瑞幸咖啡"}）
    for raw_text, kw_slot, expected in (
            ("帮我在附近的瑞幸咖啡店点一杯美式，要冰的。", "瑞幸咖啡", "瑞幸咖啡"),
            ("附近有特斯拉充电桩吗", "特斯拉充电桩", "特斯拉充电桩"),
            ("附近的瑞幸咖啡店有什么可以点单的", "瑞幸咖啡", "瑞幸咖啡"),
    ):
        intent = SimpleNamespace(
            slots={"keyword": kw_slot}, raw_text=raw_text)
        recovered = NearbyAgent._resolve_category(intent)

        assert NearbyAgent._build_keyword(
            recovered, "", "", kw_slot) == expected


def test_category_alias_and_leftover_sentence_still_use_the_clean_category_word():
    """反向护栏：别名、残句都仍归一到干净类目词，别把上面那条修成「原样透传」。

    末两条是**刻意保留的漏认**：planner 把整句灌进 keyword 时，品牌救不回来，
    但绝不能把整句交给高德——那比丢品牌更糟。
    """
    for kw_slot, expected in (
            ("帮我查一查人均百元的停车场", "停车场"),   # 剥壳剥不掉价位，仍是残句
            ("附近的充电桩", "充电站"),                 # 别名 → 规范类目词
            ("找个洗手间", "公共厕所"),
            ("在瑞幸咖啡店点一杯美式", "咖啡厅"),       # 整句：漏认，退回类目
            ("100元以内的停车场", "停车场"),
    ):
        intent = SimpleNamespace(slots={"keyword": kw_slot}, raw_text=kw_slot)
        recovered = NearbyAgent._resolve_category(intent)

        assert NearbyAgent._build_keyword(
            recovered, "", "", kw_slot) == expected


def test_unknown_explicit_category_never_silently_becomes_food():
    intent = SimpleNamespace(
        slots={"category": "公共设施"}, raw_text="帮我找公共服务设施")
    recovered = NearbyAgent._resolve_category(intent)

    assert recovered == "公共设施"
    assert NearbyAgent._build_keyword(recovered, "", "", "") == "公共设施"


def test_search_returns_place_list_card():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"cuisine": "川菜"}, raw_text="附近的川菜馆", meta=_LOC))
    assert res.status == "ok"
    assert res.ui_card["type"] == "place_list"
    assert res.ui_card["items"]                       # 有结果
    assert "lat" in res.data["items"][0]              # 结构化结果供「第N个」handoff


def test_search_incorporates_recalled_taste_preference():
    """餐饮搜索前 ctx.recall 取学到的口味偏好并体现在话术（精确读取走 predicate_prefix）。"""
    agent = NearbyAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户不吃辣", "scope": "profile.taste",
         "predicate": "taste.spicy", "confidence": 0.9}]
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={"cuisine": "川菜"}, raw_text="找家川菜馆",
                                 ctx=ctx, meta=_LOC))
    assert res.status == "ok"
    assert "不吃辣" in res.speech                       # 召回偏好进了话术
    assert ctx._memory.recall.call_args.kwargs.get("predicate_prefix") == "taste."  # 精确读取


def test_search_uses_session_location_when_user_did_not_name_an_area():
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kwargs):
        seen["keyword"] = keyword
        seen.update(kwargs)
        return []

    agent.place.search = search
    asyncio.run(run_handle(
        agent, "nearby.search", slots={"cuisine": "川菜"}, raw_text="附近川菜",
        meta={"current_lat": "39.92", "current_lng": "116.41"}))
    near = seen["near"]
    assert near is not None
    assert abs(near.lat - 39.92) < 1e-6 and abs(near.lng - 116.41) < 1e-6


def test_search_non_food_category_no_taste():
    """多类目：附近的酒店 → place_list；非餐饮不注入口味画像。"""
    agent = NearbyAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户不吃辣", "predicate": "taste.spicy"}]
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={"category": "酒店"}, raw_text="附近有什么酒店",
                                 ctx=ctx, meta=_LOC))
    assert res.status == "ok"
    assert res.ui_card["type"] == "place_list"
    assert "不吃辣" not in res.speech                    # 非餐饮不带口味


def test_detail_returns_place_detail_card():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.detail",
        slots={"name": "蜀香源"}, raw_text="蜀香源怎么样"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "place_detail"
    assert res.ui_card.get("tel") or res.ui_card.get("open_today")  # 富字段


def test_detail_missing_target_asks():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.detail", slots={}, raw_text="看看详情"))
    assert res.status == "need_slot"


def test_order_requires_confirm():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.order",
        slots={"name": "蜀香源川菜馆", "datetime": "今晚19:00", "party_size": "2"},
        raw_text="在这家订今晚7点两位"))
    assert res.status == "need_confirm"
    assert any(a["require_confirm"] for a in res.actions)


def test_order_missing_target_asks():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.order", slots={}, raw_text="点单"))
    assert res.status == "need_slot"


def test_order_confirmed_is_honest_not_fake_booking():
    """预留桩：确认后诚实告知未接入、给电话+导航兜底，不假装『已订好』。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.order",
        slots={"name": "蜀香源川菜馆"},
        raw_text="确认", meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert "接入中" in res.speech
    assert "订好" not in res.speech and "已预订" not in res.speech


def _capture_search():
    """返回 (agent, seen)：monkeypatch place.search 捕获透传给 provider 的参数。"""
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kw):
        seen["keyword"] = keyword
        seen.update(kw)
        return []

    agent.place.search = search
    return agent, seen


def test_search_facility_keyword_stripped_from_whole_sentence():
    """route_hint 把整句灌进 keyword（停车场）：agent 剥壳成干净类目词 + 认出类目。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"keyword": "附近的停车场"}, raw_text="附近的停车场",
                           meta=_LOC))
    assert seen["keyword"] == "停车场"
    assert seen["category"] == "停车"


def test_search_facility_charging_keyword():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"keyword": "附近的充电站"}, raw_text="附近哪里有充电站",
                           meta=_LOC))
    assert seen["keyword"] == "充电站"


def test_search_price_parsed_from_raw_text_when_planner_missed_slot():
    """价位兜底：planner 没填 price_max，agent 从原话『一百以内』解析出 100。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"cuisine": "火锅"}, raw_text="人均一百以内的火锅",
                           meta=_LOC))
    assert seen["price_max"] == 100.0


def test_search_sort_parsed_from_raw_text():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"cuisine": "火锅"}, raw_text="附近评分高的火锅",
                           meta=_LOC))
    assert seen["sort"] == "rating"


def test_search_facility_keyword_strips_query_verbs():
    """『帮我查一查附近的停车场』→ 关键词剥成『停车场』（修『为您找到1家查查停车场』）。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"keyword": "帮我查一查附近的停车场"},
                           raw_text="帮我查一查附近的停车场", meta=_LOC))
    assert seen["keyword"] == "停车场"


def test_search_price_band_from_left_right():
    """『人均一百左右』→ 区间 [约60,约140]（下限剔掉太便宜的 18/30，修『左右只当上限』）。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={}, raw_text="附近人均一百左右的餐厅", meta=_LOC))
    assert seen["price_min"] == 60.0 and seen["price_max"] == 140.0


def test_search_price_within_is_upper_bound_only():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={}, raw_text="人均一百以内的火锅", meta=_LOC))
    assert seen["price_min"] == 0.0 and seen["price_max"] == 100.0


def test_search_open_now_parsed_from_raw_text():
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"cuisine": "火锅"}, raw_text="附近现在营业的火锅",
                           meta=_LOC))
    assert seen["open_now"] is True


def test_search_raw_price_band_overrides_llm_price_max_slot():
    """LLM 把『一百』填进 price_max 槽，但原话是『左右』→ 用原话区间(带下限)，不被纯上限盖过。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"price_max": "100"}, raw_text="附近人均一百左右的餐厅",
                           meta=_LOC))
    assert seen["price_min"] == 60.0 and seen["price_max"] == 140.0


# ── 室内组合推荐（badcase 三连 f53d/c0d1/4799：雨天「去哪玩」）────────────────

def test_indoor_category_not_degraded_to_scenic_spot():
    """『室内景点』含『景点』子串——类目扫描必须归室内组，不能被『景点』抢走
    （badcase 4799fb1：planner 明确要室内，搜出去的却是户外公园+沙滩）。"""
    from agents.nearby.src.agent import NearbyAgent, _CATEGORY_KEYWORD, _INDOOR_SENTINEL

    class _I:
        slots = {"category": "室内景点"}
        raw_text = "你确定下雨天还推荐我去公园吗"
    assert _CATEGORY_KEYWORD[NearbyAgent._resolve_category(_I)] == _INDOOR_SENTINEL


def test_indoor_search_fans_out_and_mixes_types():
    """室内组扇出：商场/电影院/博物馆各自检索、交错合并，类型都露脸。"""
    from agents.nearby.src.providers.base import Place

    agent = NearbyAgent()
    keywords = []

    class _P:
        async def search(self, keyword, **kw):
            keywords.append(keyword)
            return [Place(id=f"{keyword}-{i}", name=f"{keyword}{i}号",
                          address="x", lat=22.5, lng=113.9, rating=4.5)
                    for i in (1, 2)]

    agent.place = _P()
    res = asyncio.run(run_handle(
        agent, "nearby.search",
        slots={"category": "室内", "weather_context": "雨"},
        raw_text="这样的天气适合去哪玩啊", meta=_LOC))
    assert res.status == "ok"
    assert keywords == ["商场", "电影院", "博物馆"]          # 全类目扇出（串行）
    top3 = [it["name"] for it in res.data["items"][:3]]
    assert top3 == ["商场1号", "电影院1号", "博物馆1号"]      # 交错合并：类型多样性


def test_indoor_search_speech_acknowledges_rain():
    """话术必须承接天气前提——badcase 根源之一是回答与『下雨』语境完全脱节。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"category": "室内", "weather_context": "中雨"},
        raw_text="这样的天气适合去哪玩啊", meta=_LOC))
    assert res.status == "ok"
    assert "雨天不太适合户外" in res.speech
    assert "室内" in res.speech
    assert res.ui_card["type"] == "place_list" and res.ui_card["items"]


def test_indoor_search_partial_provider_failure_still_answers():
    """扇出某类失败（高德 QPS 超限）→ 其余类目继续，不整轮失败。"""
    from agents.nearby.src.providers.base import Place
    from agents._sdk.http import ProviderError

    agent = NearbyAgent()

    class _P:
        async def search(self, keyword, **kw):
            if keyword == "电影院":
                raise ProviderError("CUQPS_HAS_EXCEEDED_THE_LIMIT")
            return [Place(id=f"{keyword}-1", name=f"{keyword}1号",
                          address="x", lat=22.5, lng=113.9, rating=4.4)]

    agent.place = _P()
    res = asyncio.run(run_handle(
        agent, "nearby.search", slots={"category": "室内"}, raw_text="附近室内玩的",
        meta=_LOC))
    assert res.status == "ok"
    assert "商场1号" in res.speech and "博物馆1号" in res.speech


def test_outdoor_search_with_good_weather_context_leads_positively():
    """好天气 + 户外类目：话术带上『天气不错』的承接（planner 按 guide 填 weather_context）。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"category": "景点", "weather_context": "晴"}, raw_text="今天去哪玩好",
        meta=_LOC))
    assert res.status == "ok"
    assert res.speech.startswith("天气不错")


def test_mall_category_searches_mall_not_food():
    """『附近有什么商场』不再退化成默认餐饮/美食检索（同族潜伏缺陷）。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"category": "商场"}, raw_text="附近有什么商场",
                           meta=_LOC))
    assert seen["keyword"] == "商场"


def test_parking_in_mall_still_parking():
    """类目优先级回归：『商场停车场』仍归停车（设施类目在室内组之前）。"""
    agent, seen = _capture_search()
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={}, raw_text="找个商场停车场", meta=_LOC))
    assert seen["keyword"] == "停车场"
    assert seen["category"] == "停车"


def test_discovery_search_without_any_center_degrades_honestly():
    """位置缺席的品牌/品类发现（demo-mkemhn 59b34983/44943f00）：不做全国关键字
    检索冒充「附近」——高德无位置时默认北京热门 POI，「离得最近的瑞幸」报出
    什刹海店，用户说「不是北京哦」也无从纠正，因为系统不知道自己少了位置。"""
    agent = NearbyAgent()
    called = []

    async def search(keyword, **kw):
        called.append(keyword)
        return []

    agent.place.search = search
    res = asyncio.run(run_handle(
        agent, "nearby.search",
        slots={"brand": "瑞幸", "keyword": "瑞幸咖啡"},
        raw_text="帮我看看我附近最近的瑞幸咖啡的菜单"))

    assert res.status == "ok"
    assert called == []                      # 压根不该打 provider
    assert "位置" in res.speech
    assert "附近" not in (res.speech.split("。")[0].split("没法")[0])  # 不冒充就近
    assert res.data["center"] == "none"
    assert res.data["items"] == []
    assert res.ui_card is None


def test_named_store_lookup_still_works_without_center():
    """指名门店（候选卡按钮/用户点名）是**名字查找**，不依赖位置——放行按名检索，
    但话术说「按名称找到」，不说「附近/为您找到」的就近暗示。"""
    from agents.nearby.src.providers.base import Place

    agent = NearbyAgent()

    async def search(keyword, near=None, **kw):
        assert near is None
        return [Place(id="p1", name="瑞幸咖啡(深铁金融科技大厦店)",
                      address="深大地铁站D口旁", lat=22.54, lng=113.95)]

    agent.place.search = search
    res = asyncio.run(run_handle(
        agent, "nearby.search",
        slots={"keyword": "瑞幸咖啡 深铁金融科技大厦店"},
        raw_text="选择瑞幸门店：深铁金融科技大厦店"))

    assert res.status == "ok"
    assert "按名称找到" in res.speech
    assert "附近" not in res.speech
    assert res.data["center"] == "none"
    assert res.data["items"][0]["name"] == "瑞幸咖啡(深铁金融科技大厦店)"


def test_named_poi_query_judgement():
    from agents.nearby.src.agent import _named_poi_query

    for named in ("瑞幸咖啡(深铁金融科技大厦店)", "luckin coffee 瑞幸咖啡（前海店）",
                  "瑞幸咖啡 深铁金融科技大厦店", "麦当劳 碧海君庭餐厅"):
        assert _named_poi_query(named), named
    for generic in ("瑞幸咖啡", "咖啡店", "便利店", "停车场", "奶茶店", ""):
        assert not _named_poi_query(generic), generic


def test_indoor_search_without_center_degrades_honestly():
    agent = NearbyAgent()
    called = []

    async def search(keyword, **kw):
        called.append(keyword)
        return []

    agent.place.search = search
    res = asyncio.run(run_handle(
        agent, "nearby.search", slots={"category": "室内"}, raw_text="附近室内玩的"))

    assert res.status == "ok"
    assert called == []
    assert "位置" in res.speech
    assert res.data["center"] == "none"


def test_focus_location_name_resolved_near_current_not_nationwide():
    """R3（旅程 B1-3）：location 槽是地名（焦点指代「那附近」）→ 先按当前坐标偏置
    搜该名解析成坐标（含名字包含校验），不交给全国歧义的 geocode（真栈解析到
    呼和浩特万象天地）。类目搜索收到的是解析后的**坐标**中心。"""
    import asyncio
    from agents.nearby.src.agent import NearbyAgent
    from agents.nearby.src.providers.base import Place
    from agents._sdk.testing import run_handle

    agent = NearbyAgent()
    calls = []

    class _Spy:
        async def search(self, keyword, near=None, meta=None, **kw):
            calls.append((keyword, near))
            if keyword == "万象天地":
                assert near is not None and abs(near.lat - 22.541) < 1e-3  # 按当前坐标偏置
                return [Place(id="wxtd", name="华润城万象天地1期",
                              address="南山区大冲三路", lat=22.535, lng=113.954)]
            return [Place(id="p1", name="某停车场", address="南山区",
                          lat=22.536, lng=113.955)]

    agent.place = _Spy()
    res = asyncio.run(run_handle(
        agent, "nearby.search",
        slots={"category": "停车场", "location": "万象天地"},
        raw_text="那附近有停车场吗",
        meta={"current_lat": "22.5410", "current_lng": "114.0579"}))

    assert res.status == "ok"
    # 第二次调用（类目搜索）中心=解析出的万象天地坐标，而非当前 GPS/地名 geocode
    cat_near = calls[-1][1]
    assert cat_near is not None and abs(cat_near.lat - 22.535) < 1e-3
