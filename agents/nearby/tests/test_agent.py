"""nearby（周边发现）契约测试。"""
import asyncio
from types import SimpleNamespace

from runtime.clock import epoch_at
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


def test_search_declares_the_group_label_users_will_name_it_by():
    """候选组标签（`_candidate_label`，I-030）= 卡上那个 `keyword`（品牌优先）。

    没有它，「先搜川菜、再搜咖啡」之后一句「川菜那份里最贵的」会绑到咖啡那组
    ——**确定性地答出另一份列表里的真店真价格**。断言两处相等而不是字面量：
    用户是照卡上看到的那个词点名的。
    """
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"cuisine": "川菜"}, raw_text="附近的川菜馆", meta=_LOC))
    assert res.data["_candidate_label"] == res.ui_card["keyword"]
    assert len(res.data["_candidate_label"]) >= 2      # 1 字标签会被编排当未声明

    # 品牌优先：用户点名的品牌就是他下一轮会用来指代这一组的词
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"brand": "瑞幸咖啡"}, raw_text="附近的瑞幸咖啡", meta=_LOC))
    assert res.data["_candidate_label"] == "瑞幸咖啡"


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


def test_search_deictic_uses_focus_destination_center():
    """B1-3 确定性化：「那附近有停车场」→ 检索中心用焦点目的地坐标（engine 按
    location scope 注入的 focus_destination_*），不是当前 GPS。此前靠 planner LLM
    看焦点 prompt 填 location 槽——软路径方差，7/25 journeys 绿、8/14 两跑皆红。"""
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kwargs):
        seen.update(kwargs)
        return []

    agent.place.search = search
    asyncio.run(run_handle(
        agent, "nearby.search", slots={"keyword": "停车场"},
        raw_text="那附近有停车场吗",
        meta={"current_lat": "22.5410", "current_lng": "114.0579",       # 福田
              "focus_destination": "华润城万象天地1期",
              "focus_destination_lat": "22.5405", "focus_destination_lng": "113.9412"}))
    near = seen["near"]
    assert near is not None
    assert abs(near.lng - 113.9412) < 1e-6, "中心应是焦点目的地（南山），不是当前 GPS"


def test_search_plain_nearby_not_hijacked_by_focus_destination():
    """守卫：普通「附近」无指代词 → 仍按当前 GPS，不被上次导航目的地劫持。"""
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kwargs):
        seen.update(kwargs)
        return []

    agent.place.search = search
    asyncio.run(run_handle(
        agent, "nearby.search", slots={"cuisine": "川菜"},
        raw_text="附近有什么好吃的川菜",
        meta={"current_lat": "22.5410", "current_lng": "114.0579",
              "focus_destination": "华润城万象天地1期",
              "focus_destination_lat": "22.5405", "focus_destination_lng": "113.9412"}))
    near = seen["near"]
    assert abs(near.lat - 22.5410) < 1e-6 and abs(near.lng - 114.0579) < 1e-6


def test_search_deictic_without_focus_falls_back_to_gps():
    """指代词在场但无焦点坐标（首轮就说「那附近」）→ 回落 GPS，不抛错。"""
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kwargs):
        seen.update(kwargs)
        return []

    agent.place.search = search
    asyncio.run(run_handle(
        agent, "nearby.search", slots={"keyword": "停车场"},
        raw_text="那附近有停车场吗",
        meta={"current_lat": "22.5410", "current_lng": "114.0579"}))
    near = seen["near"]
    assert abs(near.lat - 22.5410) < 1e-6


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


# ─── EVA 二轮 G6：口味偏好的确定性消费（检索前生效，不再是话术装饰）───

def test_taste_biases_generic_food_search_keyword():
    """泛餐饮发现（没点菜系/品牌）→ 记忆里的喜好菜系直接偏置检索词（消费方证据）。"""
    agent = NearbyAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户喜欢粤菜", "scope": "profile.taste",
         "predicate": "taste.cuisine", "polarity": "like", "confidence": 0.9}]
    seen = {}

    async def search(keyword, **kwargs):
        seen["keyword"] = keyword
        return []

    agent.place.search = search
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"category": "餐饮"}, raw_text="晚上找地方吃饭",
                           ctx=ctx, meta=_LOC))
    assert seen["keyword"] == "粤菜"          # 偏好进了检索词，不是只进话术


def test_taste_bias_never_overrides_explicit_cuisine():
    """用户点名川菜时，记忆喜好不得覆盖（用户说的永远优先于记忆）。"""
    agent = NearbyAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户喜欢粤菜", "predicate": "taste.cuisine", "polarity": "like",
         "scope": "profile.taste"}]
    seen = {}

    async def search(keyword, **kwargs):
        seen["keyword"] = keyword
        return []

    agent.place.search = search
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"cuisine": "川菜"}, raw_text="找家川菜馆",
                           ctx=ctx, meta=_LOC))
    assert seen["keyword"] == "川菜"


def test_taste_negative_feedback_demotes_named_store():
    """「这家太咸」店名级负反馈 → 该店软降权后移（不删除）；话术如实报「已排后」。"""
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户觉得老灶火锅太咸", "predicate": "taste.dislike",
         "polarity": "dislike", "scope": "profile.taste"}]

    async def search(keyword, **kwargs):
        return [Place(id="a", name="老灶火锅(总店)", category="餐饮", rating=4.5),
                Place(id="b", name="初色日料", category="餐饮", rating=4.2)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={"cuisine": "日料"}, raw_text="附近吃点什么",
                                 ctx=ctx, meta=_LOC))
    assert [i["name"] for i in res.data["items"]] == ["初色日料", "老灶火锅(总店)"]
    assert "已排后" in res.speech


def test_taste_recall_includes_named_family_subject():
    """「和老婆吃饭」→ 额外按 subject=老婆 召回她的口味（G6 subject 消费方证据）。"""
    agent = NearbyAgent()
    ctx = make_context()
    calls = []

    async def recall(user_id, query="", **kwargs):
        calls.append(kwargs)
        if kwargs.get("subject") == "老婆":
            return [{"text": "老婆喜欢粤菜", "subject": "老婆", "polarity": "like",
                     "predicate": "taste.cuisine", "scope": "profile.taste"}]
        return []

    ctx._memory.recall = recall
    seen = {}

    async def search(keyword, **kwargs):
        seen["keyword"] = keyword
        return []

    agent.place.search = search
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={"category": "餐饮"}, raw_text="晚上找地方和老婆吃饭",
                           ctx=ctx, meta=_LOC))
    assert any(k.get("subject") == "老婆" for k in calls)
    assert seen["keyword"] == "粤菜"          # 老婆的偏好真实进了检索


# ─── EVA 二轮批 E（G5）：语义类目扩展 / 无信号诚实追问 / 氛围软重排 ───

def test_zoo_semantic_category_no_food_fallback():
    """「带孩子看看动物」→ 动物园检索（此前零覆盖、兜底错搜「美食」）。"""
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kwargs):
        seen["keyword"] = keyword
        return []

    agent.place.search = search
    asyncio.run(run_handle(agent, "nearby.search",
                           slots={}, raw_text="带孩子去附近能看看动物的地方",
                           meta=_LOC))
    assert seen["keyword"] == "动物园"


def test_unknown_category_without_food_hint_asks_honestly():
    """全无类目命中且无饮食信号 → 诚实追问（错误结果比失败更糟），不落「美食」。"""
    agent = NearbyAgent()
    called = {"n": 0}

    async def search(keyword, **kwargs):
        called["n"] += 1
        return []

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={}, raw_text="找个能待着的地方", meta=_LOC))
    assert res.status == "need_slot" and "哪一类" in res.speech
    assert called["n"] == 0                    # 没拿「美食」冒充


def test_ambience_word_reranks_by_tags_and_rating_honestly():
    """「安静点的地方喝咖啡」→ 环境类标签+评分软重排，话术如实说没有安静度数据。"""
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()

    async def search(keyword, **kwargs):
        return [Place(id="a", name="闹市咖啡", category="咖啡厅", rating=4.8,
                      tags="商务宴请,网红店"),
                Place(id="b", name="庭院咖啡", category="咖啡厅", rating=4.2,
                      tags="环境好,安静")]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={}, raw_text="最近有点累，找个安静点的地方喝咖啡",
                                 meta=_LOC))
    assert [i["name"] for i in res.data["items"]] == ["庭院咖啡", "闹市咖啡"]
    assert "安静度数据" in res.speech          # 诚实说数据边界，不假装有安静度


# ── P5（EVA 遗留卡）：咖啡类目的口味消费面 ──

def test_coffee_category_consumes_store_level_dislike():
    """「这家咖啡太酸」的店铺级差评此前对咖啡搜索结构性失效——口味门禁只认正餐
    类目（_FOOD_CATS 无咖啡），带店名条目入了库结果集也纹丝不动。修后：咖啡类目
    走 _TASTE_CATS 消费面，店名头匹配降权生效；菜系偏置仍只限正餐（粤菜偏好
    不得偏置咖啡检索词）。"""
    from agents.nearby.src.providers.base import Place as _Place
    agent = NearbyAgent()
    seen = {}

    async def search(keyword, **kw):
        seen["keyword"] = keyword
        return [
            _Place(id="a", name="三立方(南山创维店)", rating=4.0, lat=22.5, lng=113.9),
            _Place(id="b", name="瑞幸咖啡(创维店)", rating=4.5, lat=22.5, lng=113.9),
        ]

    agent.place.search = search
    ctx = make_context()

    async def recall(query, **kw):
        return [
            {"text": "用户喜欢吃粤菜", "polarity": "like", "predicate": "taste.cuisine"},
            {"text": "用户不喜欢三立方的咖啡（太酸）", "polarity": "dislike",
             "predicate": "taste.coffee"},
        ]
    ctx.recall = recall

    res = asyncio.run(run_handle(
        agent, "nearby.search", slots={"keyword": "咖啡"},
        raw_text="帮我找杯咖啡喝", ctx=ctx,
        meta={"current_lat": "22.54", "current_lng": "113.93"}))

    assert res.status == "ok"
    names = [i["name"] for i in res.data["items"]]
    assert names[0] == "瑞幸咖啡(创维店)"          # 差评店降权后移
    assert names[-1] == "三立方(南山创维店)"
    assert "不合口味的已排后" in res.speech
    assert seen["keyword"] != "粤菜"               # 菜系偏置未污染咖啡检索


# ── E5（EVA 余项⑤）口味召回两路并集 ─────────────────────────────
def _fake_recall(rows):
    """复刻 pg_store._score 的过滤语义：scope 与 predicate_prefix 是 **AND**。"""
    async def recall(query, *, scopes=None, kinds=None, top_k=5, predicate_prefix="",
                     min_score=0.0, min_confidence=0.0, max_age_days=0, subject=""):
        out = []
        for r in rows:
            if scopes and r.get("scope") not in scopes:
                continue
            if predicate_prefix and not str(r.get("predicate") or "").startswith(
                    predicate_prefix):
                continue
            if subject and str(r.get("subject") or "") != subject:
                continue
            out.append(r)
        return out[:top_k]
    return recall


def test_taste_recall_issues_both_scope_and_predicate_paths():
    """并集召回必须真的发两路——只发一路就是今天这个 bug 的形状。"""
    agent = NearbyAgent()
    ctx = make_context()
    seen = []

    async def recall(query, **kw):
        seen.append((tuple(kw.get("scopes") or ()), kw.get("predicate_prefix") or ""))
        return []
    ctx.recall = recall

    asyncio.run(agent._recall_taste(ctx))
    assert (("profile.taste",), "") in seen        # scope 路（谓词不限）
    assert ((), "taste.") in seen                  # 谓词路（scope 不限）


def test_store_dislike_without_taste_prefix_is_recalled_and_demoted():
    """真栈存量形态：`place.avoid` 的 scope 是 profile.taste 但谓词没有 taste. 前缀。

    旧的单路召回（scope **且** 谓词前缀）返回空 → 差评存了三条却一条都用不上；
    并集召回把它取回来，店铺级降权照常生效。
    """
    from agents.nearby.src.providers.base import Place
    rows = [{"text": "以后不要推荐三立方(南山创维店)，觉得咖啡太酸",
             "predicate": "place.avoid", "scope": "profile.taste",
             "polarity": "dislike"}]
    recall = _fake_recall(rows)
    # 先钉根因：老口径（两条件同时给）确实召不回
    assert asyncio.run(recall("口味偏好", scopes=["profile.taste"],
                              predicate_prefix="taste.")) == []

    agent = NearbyAgent()
    ctx = make_context()
    ctx.recall = recall

    async def search(keyword, **kw):
        return [Place(id="a", name="三立方(南山创维店)", rating=4.6),
                Place(id="b", name="瑞幸咖啡(创维店)", rating=4.2)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"keyword": "咖啡"},
                                 raw_text="帮我找杯咖啡喝", ctx=ctx, meta=_LOC))
    assert [i["name"] for i in res.data["items"]] == ["瑞幸咖啡(创维店)", "三立方(南山创维店)"]
    assert "已排后" in res.speech


def test_taste_recall_survives_scope_drift():
    """反向：scope 漂成 profile.habit、谓词却是 taste.* 的行，由谓词路兜住。"""
    from agents.nearby.src.providers.base import Place
    rows = [{"text": "用户不喜欢老灶火锅（太咸）", "predicate": "taste.dislike_place",
             "scope": "profile.habit", "polarity": "dislike"}]
    agent = NearbyAgent()
    ctx = make_context()
    ctx.recall = _fake_recall(rows)

    async def search(keyword, **kw):
        return [Place(id="a", name="老灶火锅(总店)", rating=4.5),
                Place(id="b", name="初色日料", rating=4.2)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"cuisine": "日料"},
                                 raw_text="附近吃点什么", ctx=ctx, meta=_LOC))
    assert [i["name"] for i in res.data["items"]] == ["初色日料", "老灶火锅(总店)"]


def test_taste_recall_subject_partition_still_holds():
    """并集不得打破 subject 定向：点名家人时只取该家人的条目（G6 分区纪律）。

    ⚠ 反向不成立且**不是本卡要改的**：subject 为空是「不过滤」而非「只取本人」
    （`pg_store._score` 的 subject 过滤只在非空时生效），所以泛召回本来就会带上
    家人条目——这是既有语义，此处只钉「定向那一路仍然干净」。
    """
    rows = [{"text": "老婆喜欢粤菜", "predicate": "taste.cuisine",
             "scope": "profile.taste", "subject": "老婆", "polarity": "like"},
            {"text": "用户喜欢川菜", "predicate": "taste.cuisine",
             "scope": "profile.taste", "polarity": "like"}]
    agent = NearbyAgent()
    ctx = make_context()
    ctx.recall = _fake_recall(rows)

    hers = asyncio.run(agent._recall_taste(ctx, subject="老婆"))
    assert hers and all(r["subject"] == "老婆" for r in hers)   # 定向路只有她的
    assert len(hers) == 2                                       # 两路各命中一次（去重在 _taste_profile）


# ── E1（G1 余项）事件时刻 → 用餐窗反推 ─────────────────────────
_EVENT_NOW = epoch_at(2026, 8, 14, 14, 0)   # 周五 14:00（业务时区，非宿主本地时）


def _agent_at(now_ts=_EVENT_NOW, places=None):
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()
    agent._now_ts = lambda: now_ts
    rows = places if places is not None else [
        Place(id="a", name="小南国", category="餐饮", rating=4.6),
        Place(id="b", name="蜀香源", category="餐饮", rating=4.3)]

    async def search(keyword, **kw):
        return list(rows)
    agent.place.search = search
    return agent


def test_event_time_reverse_infers_the_dining_window():
    """「晚上7点的电影，先找个地方吃饭」→ 反推入座/离席窗，并把路上预留**说出来**。"""
    agent = _agent_at()
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="晚上7点的电影，先找个地方吃饭",
                                 ctx=make_context(), meta=_LOC))
    assert "19:00的电影" in res.speech
    assert "17:30入座" in res.speech and "18:30前吃完" in res.speech
    assert "预留30分钟路上时间" in res.speech          # 假设必须念出来
    w = res.data["dining_window"]
    assert w["dwell_min"] == 60 and w["buffer_min"] == 30 and w["tight"] is False


def test_no_event_word_means_no_window():
    """普通找吃的不带窗口（非回归）：没有事件就没有可反推的东西。"""
    agent = _agent_at()
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="附近有什么好吃的", ctx=make_context(), meta=_LOC))
    assert "dining_window" not in res.data and "入座" not in res.speech


def test_window_filters_places_closed_at_the_seating_time():
    """按入座时刻筛营业中——这一条不是近似，是真实的 opentime_today 数据。"""
    from agents.nearby.src.providers.base import Place
    agent = _agent_at(places=[
        Place(id="a", name="夜宵摊", category="餐饮", rating=4.8, open_today="21:00-03:00"),
        Place(id="b", name="小南国", category="餐饮", rating=4.2, open_today="11:00-22:00"),
        Place(id="c", name="不明营业", category="餐饮", rating=4.1)])   # 未知 → 保留
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="晚上7点的电影，先吃个饭",
                                 ctx=make_context(), meta=_LOC))
    names = [i["name"] for i in res.data["items"]]
    assert "夜宵摊" not in names                     # 17:30 明确不营业 → 剔
    assert "小南国" in names and "不明营业" in names  # 营业中 / 未知都留


def test_all_closed_at_seating_time_is_told_not_hidden():
    """全被剔时不硬凑——保留原列表并如实说那个点大多不营业。"""
    from agents.nearby.src.providers.base import Place
    agent = _agent_at(places=[
        Place(id="a", name="夜宵摊", category="餐饮", open_today="21:00-03:00"),
        Place(id="b", name="宵夜铺", category="餐饮", open_today="22:00-04:00")])
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="晚上7点的电影，先吃个饭",
                                 ctx=make_context(), meta=_LOC))
    assert "大多不营业" in res.speech
    assert len(res.data["items"]) == 2


def test_tight_window_says_it_is_too_late():
    """18:40 才问「7点的电影先吃饭」→ 说来不及，不把窗口压缩成能凑上的数。"""
    late = epoch_at(2026, 8, 14, 18, 40)
    agent = _agent_at(now_ts=late)
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="晚上7点的电影，先吃个饭",
                                 ctx=make_context(), meta=_LOC))
    assert "时间不太够" in res.speech and res.data["dining_window"]["tight"] is True
    assert "入座" not in res.speech


# ── E2（G5 余项）无障碍 / 停车便利 / 不排队 ──────────────────────
def _agent_with_parking(main_places, lots_by_name):
    """主检索返回餐厅、「停车场」检索按坐标返回各自的停车场（探测桩）。"""
    agent = NearbyAgent()

    async def search(keyword, **kw):
        if keyword == "停车场":
            near = kw.get("near")
            key = (round(near.lat, 4), round(near.lng, 4)) if near else None
            return list(lots_by_name.get(key, []))
        return list(main_places)

    agent.place.search = search
    return agent


def test_explicit_accessibility_request_reranks_by_parking_and_says_it_is_an_approximation():
    """「腿脚不便」→ 按周边停车场密度近似重排，并**说明这是近似**（没有台阶数据）。"""
    from agents.nearby.src.providers.base import Place
    far = Place(id="a", name="无停车小馆", category="餐饮", rating=4.8,
                lat=22.500, lng=113.900)
    near = Place(id="b", name="商场里的粤菜", category="餐饮", rating=4.1,
                 lat=22.600, lng=113.950)
    agent = _agent_with_parking([far, near], {
        (22.5, 113.9): [],
        (22.6, 113.95): [Place(id="p1", name="P1", distance_km=0.08),
                         Place(id="p2", name="P2", distance_km=0.22),
                         Place(id="p3", name="太远", distance_km=1.4)],
    })
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="找个吃饭的地方，老人腿脚不便",
                                 ctx=make_context(), meta=_LOC))
    assert [i["name"] for i in res.data["items"]] == ["商场里的粤菜", "无停车小馆"]
    assert "没有无障碍/台阶数据" in res.speech and "停车便利度" in res.speech
    stats = {s["name"]: s for s in res.data["access"]["parking"]}
    assert stats["商场里的粤菜"]["count"] == 2 and stats["商场里的粤菜"]["nearest_km"] == 0.08
    assert stats["无停车小馆"]["count"] == 0


def test_accessibility_triggered_by_memory_when_user_only_names_family():
    """「带爸妈去吃饭」不带任何无障碍词 → 读画像里的行动不便事实再触发（记忆消费面）。"""
    from agents.nearby.src.providers.base import Place
    agent = _agent_with_parking(
        [Place(id="a", name="甲餐厅", category="餐饮", lat=22.5, lng=113.9)],
        {(22.5, 113.9): [Place(id="p", name="P", distance_km=0.1)]})
    ctx = make_context()
    ctx.recall = _fake_recall([
        {"text": "用户的父母腿脚不太方便", "predicate": "person.parent",
         "scope": "profile.person"}])
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="带爸妈去吃个饭", ctx=ctx, meta=_LOC))
    assert "记得您提到过家人行动不太方便" in res.speech
    assert res.data["access"]["parking"][0]["count"] == 1


def test_accessibility_not_triggered_without_person_or_explicit_words():
    """守卫：普通「附近吃什么」不翻记忆、不做停车探测（探测有代价，别每轮都打）。"""
    from agents.nearby.src.providers.base import Place
    hits = []

    agent = NearbyAgent()

    async def search(keyword, **kw):
        hits.append(keyword)
        return [Place(id="a", name="甲餐厅", category="餐饮", lat=22.5, lng=113.9)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="附近有什么好吃的", ctx=make_context(), meta=_LOC))
    assert "停车场" not in hits and "access" not in res.data


def test_parking_probe_failure_does_not_break_the_search():
    """探测失败（provider 挂/限流）→ 主结果照出，只是没有那句排序说明。"""
    from agents.nearby.src.providers.base import Place
    from agents._sdk.http import ProviderError
    agent = NearbyAgent()

    async def search(keyword, **kw):
        if keyword == "停车场":
            raise ProviderError("CUQPS_HAS_EXCEEDED_THE_LIMIT")
        return [Place(id="a", name="甲餐厅", category="餐饮", lat=22.5, lng=113.9)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="找个停车方便的餐厅", ctx=make_context(), meta=_LOC))
    assert res.status == "ok" and res.data["items"]
    assert "这条我按不上" in res.speech          # 诚实：近似也没算成


def test_parking_probe_is_bounded_to_top_k():
    """探测面有界（K=4）：候选再多也只打 4 次，重排面不超出探测面。"""
    from agents.nearby.src.providers.base import Place
    probed = []
    agent = NearbyAgent()
    rows = [Place(id=str(i), name=f"店{i}", category="餐饮", lat=22.5 + i / 1000,
                  lng=113.9) for i in range(9)]

    async def search(keyword, **kw):
        if keyword == "停车场":
            probed.append(kw.get("near"))
            return []
        return list(rows)

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="找个好停车的地方吃饭",
                                 ctx=make_context(), meta=_LOC))
    assert len(probed) == 4
    assert [i["name"] for i in res.data["items"]] == [f"店{i}" for i in range(9)]


def test_no_queue_preference_is_answered_honestly_not_faked():
    """「不排队」没有数据源——如实说按不上，不拿评分/人气冒充（六#3 语料另半边）。"""
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()

    async def search(keyword, **kw):
        return [Place(id="a", name="甲餐厅", category="餐饮", rating=4.5)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"cuisine": "粤菜"},
                                 raw_text="找个不排队的粤菜馆", ctx=make_context(), meta=_LOC))
    assert "没有实时排队数据" in res.speech


def test_no_queue_preference_from_memory_also_surfaces():
    """「老婆不喜欢排队」存在画像里 → 和老婆吃饭时也如实说一句（存了就要用上）。"""
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()
    ctx = make_context()
    ctx.recall = _fake_recall([
        {"text": "老婆不喜欢排队", "predicate": "taste.no_queue",
         "scope": "profile.taste", "subject": "老婆"}])

    async def search(keyword, **kw):
        return [Place(id="a", name="甲餐厅", category="餐饮", rating=4.5)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="晚上和老婆找地方吃饭", ctx=ctx, meta=_LOC))
    assert "没有实时排队数据" in res.speech


def test_mobility_memory_is_not_applied_to_an_unrelated_person():
    """真栈抓修：「和老婆吃饭」不得命中「父母腿脚不便」那条记忆。

    「话里提到了人」不等于「这条记忆是关于那个人的」——套上去就是假个性化
    （系统声称考虑了一件根本不适用的事），与 §5① 同族只是换了维度。
    """
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()
    ctx = make_context()
    ctx.recall = _fake_recall([
        {"text": "用户的父母腿脚不太方便", "predicate": "person.parent",
         "scope": "profile.person"}])

    async def search(keyword, **kw):
        return [Place(id="a", name="甲餐厅", category="餐饮", lat=22.5, lng=113.9)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="晚上和老婆找个地方吃饭", ctx=ctx, meta=_LOC))
    assert "行动不太方便" not in res.speech and "access" not in res.data
    # 对照：同一条记忆在「带爸妈吃饭」时必须仍然生效（别把守卫修成一律不触发）
    res2 = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                  raw_text="带爸妈去吃个饭", ctx=ctx, meta=_LOC))
    assert "行动不太方便" in res2.speech


def test_mobility_memory_with_explicit_subject_only_applies_to_that_subject():
    """带 subject 的记忆按 subject 判定：老婆的行动不便只在提到老婆时生效。"""
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()
    ctx = make_context()
    ctx.recall = _fake_recall([
        {"text": "最近走路不太方便", "predicate": "person.spouse",
         "scope": "profile.person", "subject": "老婆"}])

    async def search(keyword, **kw):
        return [Place(id="a", name="甲餐厅", category="餐饮", lat=22.5, lng=113.9)]

    agent.place.search = search
    hit = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="和老婆去吃饭", ctx=ctx, meta=_LOC))
    miss = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                  raw_text="带爸妈去吃饭", ctx=ctx, meta=_LOC))
    assert "行动不太方便" in hit.speech
    assert "行动不太方便" not in miss.speech


def test_food_category_alias_in_keyword_slot_is_not_used_as_a_query_word():
    """planner 把「吃饭」填进 keyword 时不能拿它当检索词（真栈实测话术念成
    「为您找到 10 家吃饭」）；具体词仍原样。"""
    for kw_slot, expected in (("吃饭", "美食"), ("餐厅", "美食"),
                              ("火锅", "火锅"), ("川菜", "川菜")):
        assert NearbyAgent._build_keyword("餐饮", "", "", kw_slot) == expected


# ── EVA 复验批：当轮忌口 / 停车说法 / 时段词（2026-08-15 双档真栈抓出）──
def test_current_turn_no_spicy_overrides_remembered_cuisine():
    """「不要太辣」必须压过记忆里的川菜偏好——两个 provider 都推了川菜（系统缺口）。

    记忆是背景、这句话是前景；冲突时前景赢，而且要说出来。
    """
    from agents.nearby.src.providers.base import Place
    agent = NearbyAgent()
    ctx = make_context()
    ctx.recall = _fake_recall([
        {"text": "用户喜欢川菜和四川火锅", "predicate": "taste.cuisine",
         "scope": "profile.taste", "polarity": "like"}])
    seen = {}

    async def search(keyword, **kw):
        seen["keyword"] = keyword
        return [Place(id="a", name="老灶火锅", category="餐饮", rating=4.6),
                Place(id="b", name="淮扬人家", category="餐饮", rating=4.2)]

    agent.place.search = search
    res = asyncio.run(run_handle(agent, "nearby.search", slots={"category": "餐饮"},
                                 raw_text="晚上7点电影，先吃饭，不要太辣",
                                 ctx=ctx, meta=_LOC))
    assert seen["keyword"] != "川菜"                      # 不拿爱吃的辣菜系去偏置
    assert "不要辣" in res.speech or "不按平时爱吃" in res.speech
    assert [i["name"] for i in res.data["items"]][0] == "淮扬人家"   # 重辣的排后


def test_no_spicy_phrasings_are_recognized():
    """忌辣说法覆盖面（首版只认「不…吃/沾辣」，「不要太辣」直接漏）。"""
    agent = NearbyAgent()
    for raw in ("不要太辣", "不太能吃辣", "别太辣", "少辣一点", "我不吃辣",
                "口味清淡点", "怕辣"):
        assert agent._NO_SPICY_RE.search(raw), raw
    for raw in ("要辣一点", "特别辣的那种", "麻辣香锅"):
        assert not agent._NO_SPICY_RE.search(raw), raw


def test_parking_phrasing_allows_inserted_words():
    """「停车最好方便一点」这类插入语形态要触发属性维（真栈实测漏掉）。"""
    from agents.nearby.src.agent import _ACCESS_RE
    for raw in ("停车最好方便一点", "停车方便点的", "找个好停车的地方",
                "停车位好找的", "方便停车吗"):
        assert _ACCESS_RE.search(raw), raw
    assert not _ACCESS_RE.search("附近有停车场吗")        # 找停车场本身不是无障碍诉求


def test_meal_time_words_are_not_used_as_query_words():
    """「晚饭」是时段词不是检索词（真栈实测搜出一串赛百味、话术念「10 家晚饭」）。"""
    for kw_slot, expected in (("晚饭", "美食"), ("晚餐", "美食"), ("夜宵", "美食"),
                              ("早饭", "早餐店"), ("火锅", "火锅")):
        cat = NearbyAgent._resolve_category(
            SimpleNamespace(slots={"keyword": kw_slot}, raw_text=kw_slot))
        assert NearbyAgent._build_keyword(cat, "", "", kw_slot) == expected, kw_slot


def test_constraint_words_are_never_used_as_query_words():
    """planner 把**约束词**填进 keyword 时不得拿去检索（2026-08-15 双档真栈两个恶例）：
    「不辣」搜出一串「辣可可·现炒黄牛肉」、「适合带老人」搜出家政公司。"""
    for kw_slot in ("不辣", "适合带老人", "安静", "停车方便", "环境好", "便宜点"):
        assert NearbyAgent._build_keyword("餐饮", "", "", kw_slot) == "美食", kw_slot


def test_real_dish_words_still_pass_through():
    """反向对照：菜系/菜品词照旧原样进检索（别把守卫修成一律退回类目）。"""
    for kw_slot in ("火锅", "川菜", "日料", "潮汕牛肉", "烤鱼", "轻食", "brunch"):
        assert NearbyAgent._build_keyword("餐饮", "", "", kw_slot) == kw_slot, kw_slot


# ── Q2/N5：兜底候选必须自报家门（保留键 `_fallback`）────────────────────────
# I-011 的真根因不是「失败的重搜清空了候选」——那次重搜**根本没失败**：泛化兜底
# 搜出 10 家「美食」，于是它**合法地**覆盖了上一份川菜候选，第三轮「刚才列表里的
# 第二家」拿到的是兜底那份的第二家。要修的是「兜底不得顶替用户点名的那份」，
# 而编排看不出一次检索是不是兜底——**只有产生方知道搜的和他说的是不是一回事**。

def test_discarded_user_term_declares_a_fallback():
    """用户点了一个具体词、检索词却退回干净类目词 ⇒ 这一份是兜底。

    CD2 的原句形态：「附近有没有卖锟斤拷的店」——planner 把那个词填进 keyword，
    `_build_keyword` 剥完认不出，落回「美食」。搜出来的 10 家和用户说的没关系。
    """
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"category": "餐饮", "keyword": "锟斤拷"},
        raw_text="附近有没有卖锟斤拷的店", meta=_LOC))
    assert res.status == "ok"
    assert res.data.get("_fallback") is True


def test_generic_food_request_is_not_a_fallback():
    """对照：用户本来就问得泛（「附近有什么好吃的」，没点任何具体词）——
    搜「美食」是**照他说的做**，不是猜。标成兜底会让这一份永远排在序数解析之后。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"category": "餐饮"}, raw_text="附近有什么好吃的", meta=_LOC))
    assert res.status == "ok"
    assert "_fallback" not in res.data


def test_named_cuisine_is_not_a_fallback():
    """对照：用户点了菜系 ⇒ 这就是他要的那一份，不许被标成兜底
    （标错方向的代价是**真候选被当成兜底忽略**，比漏标更贵）。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"cuisine": "川菜"}, raw_text="附近的川菜馆", meta=_LOC))
    assert res.status == "ok"
    assert "_fallback" not in res.data


def test_named_brand_is_not_a_fallback():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"brand": "瑞幸"}, raw_text="附近的瑞幸", meta=_LOC))
    assert res.status == "ok"
    assert "_fallback" not in res.data


def test_explicit_facility_category_is_not_a_fallback():
    """「附近的停车场」检索词就该是干净类目词——它是用户**点名**的类目，不是我猜的。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"category": "停车场"}, raw_text="附近的停车场", meta=_LOC))
    assert res.status == "ok"
    assert "_fallback" not in res.data


def test_guessed_category_declares_a_fallback_even_without_a_discarded_term():
    """第二个信号：planner 一个具体词都不填、只给 category=餐饮 时，
    「用户给了词我们丢了」这条够不着——真栈 3 轮里有一轮正是这个形态，
    兜底没被标出来、序数当场绑到它上面。类目是不是**猜**的必须单独判。"""
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"category": "餐饮"}, raw_text="附近有没有卖锟斤拷的店", meta=_LOC))
    assert res.status == "ok"
    assert res.data.get("_fallback") is True
