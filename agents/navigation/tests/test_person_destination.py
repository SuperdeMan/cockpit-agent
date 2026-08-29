"""人称目的地解析契约测试（M2 记忆图谱 P1-b）。

「去接孩子放学」是母提案 §1.2-E2 的 Eva 例子，也是关系边**唯一非做不可**的消费面。
两条硬要求：
1. 用户已经给了具体地点时**绝不改写**（「孩子学校旁边的星巴克」要去星巴克）；
2. 不知道人在哪时**诚实追问**，绝不用相似度猜——导航到错学校比问一句更糟。
"""
import asyncio

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent, _person_destination


# ── 触发判据（纯函数）────────────────────────────────────────────────────

@pytest.mark.parametrize("dest,expect", [
    ("孩子", "孩子"),
    ("接孩子", "孩子"),
    ("孩子的学校", "孩子"),
    ("女儿学校", "女儿"),
    ("去接女儿放学", "女儿"),
    ("导航去接孩子放学", "孩子"),
    ("老婆单位", "老婆"),
    # 口语里「接我妈」比「接妈妈」常见——真栈首验漏掉：filler 词表没有「我」，
    # 剥完「妈」剩个「我」被当成实质内容，整条链路静默不触发。
    ("接我妈", "妈"),
    ("我妈", "妈"),
    ("送我爸", "爸"),
])
def test_person_only_destinations_trigger(dest, expect):
    assert _person_destination(dest) == expect


@pytest.mark.parametrize("dest", [
    "孩子学校旁边的星巴克",     # 用户已给具体地点 → 改写它是帮倒忙
    "大妈家门口的超市",         # 裸「妈」不误伤：剥掉后剩「大超市」= 有实质内容
    "女儿喜欢的迪士尼乐园",
    "XX小学",
    "星巴克",
    "",
    "   ",
])
def test_specific_destinations_do_not_trigger(dest):
    assert _person_destination(dest) == ""


# ── handler 行为 ─────────────────────────────────────────────────────────

def _ctx_with(hit):
    ctx = make_context()

    async def _resolve(person_word):
        return hit
    ctx.resolve_person_place = _resolve
    return ctx


def test_resolves_to_place_and_navigates():
    """孩子 → family(小雨) → place_of(XX小学) → 按 XX小学 继续既有导航链路。"""
    agent = NavigationAgent()
    seen = {}

    async def _fake_correct(dest, raw, meta):
        return dest
    agent._correct_planner_landmark = _fake_correct

    async def _fake_search(*a, **k):
        seen["dest"] = a[0] if a else k.get("keyword")
        return []
    agent.poi.search = _fake_search

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "接孩子"},
        raw_text="去接孩子放学",
        ctx=_ctx_with({"person": "小雨", "place": "XX小学", "object_ref": ""})))
    # 解析后的地点进入了既有搜索链路（不再拿「接孩子」去搜 POI）
    assert seen.get("dest") == "XX小学" or "XX小学" in str(res.speech)


def test_planner_collapsed_school_slot_still_resolves_person_from_raw_text():
    """Planner 把「去接孩子放学」压成 destination=学校时，原话仍是人称解析真相源。"""
    agent = NavigationAgent()
    seen = {}

    async def _fake_correct(dest, raw, meta):
        return dest
    agent._correct_planner_landmark = _fake_correct

    async def _fake_search(*args, **kwargs):
        seen["dest"] = args[0] if args else kwargs.get("keyword")
        return []
    agent.poi.search = _fake_search

    res = asyncio.run(run_handle(
        agent,
        "navigation.navigate_to",
        slots={"destination": "学校"},
        raw_text="导航去接孩子放学",
        ctx=_ctx_with({"person": "小雨", "place": "阳光小学", "object_ref": ""}),
    ))

    assert seen.get("dest") == "阳光小学" or "阳光小学" in str(res.speech)


def test_unknown_person_asks_honestly():
    """**不猜**：不知道孩子是谁/在哪 → NEED_SLOT 追问，并告诉用户怎么教会它。"""
    agent = NavigationAgent()

    async def _fake_correct(dest, raw, meta):
        return dest
    agent._correct_planner_landmark = _fake_correct

    async def _must_not_search(*a, **k):
        raise AssertionError("人称未解析时不应拿人称词去搜 POI")
    agent.poi.search = _must_not_search

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "接孩子"},
        raw_text="去接孩子放学", ctx=_ctx_with(None)))
    assert res.status == "need_slot"
    assert "孩子" in res.speech and "在哪" in res.speech
    assert "上学" in res.follow_up            # 告诉用户怎么补这条关系


def test_bare_kinship_word_reads_naturally():
    """裸称谓要转成自然说法——「我还不知道**妈**平时在哪」读着别扭（真栈实测）。"""
    from agents.navigation.src.agent import _person_display
    assert _person_display("妈") == "妈妈"
    assert _person_display("爸") == "爸爸"
    assert _person_display("娃") == "孩子"
    assert _person_display("女儿") == "女儿"      # 本来就自然的不动


def test_specific_destination_never_hits_relation_lookup():
    """给了具体地点就不该查关系图（省一跳，也防误改写）。"""
    agent = NavigationAgent()

    async def _fake_correct(dest, raw, meta):
        return dest
    agent._correct_planner_landmark = _fake_correct

    async def _fake_search(*a, **k):
        return []
    agent.poi.search = _fake_search

    ctx = make_context()

    async def _must_not_resolve(person_word):
        raise AssertionError("具体地点不应触发关系边解析")
    ctx.resolve_person_place = _must_not_resolve

    asyncio.run(run_handle(agent, "navigation.navigate_to",
                           slots={"destination": "孩子学校旁边的星巴克"},
                           raw_text="导航去孩子学校旁边的星巴克", ctx=ctx))


def test_memory_unavailable_degrades_to_ask():
    """memory 挂了 → resolve 返回 None → 走诚实追问，不炸也不猜。"""
    agent = NavigationAgent()

    async def _fake_correct(dest, raw, meta):
        return dest
    agent._correct_planner_landmark = _fake_correct

    ctx = make_context()

    async def _boom(person_word):
        raise RuntimeError("memory down")
    ctx.resolve_person_place = _boom

    with pytest.raises(RuntimeError):
        # Context.resolve_person_place 自身吞异常；这里直接注入抛错的替身，
        # 验证的是「Agent 没有额外吞掉真异常」——SDK 层的 best-effort 由 base.py 保证
        asyncio.run(run_handle(agent, "navigation.navigate_to",
                               slots={"destination": "接孩子"},
                               raw_text="去接孩子", ctx=ctx))


# ── P1（EVA 遗留卡）：途经点三级解析 ─────────────────────────────────────

from agents.navigation.src.providers.base import POI as _POI


class _WpPoi:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    async def search(self, keyword, near=None, limit=5, meta=None, **kw):
        self.calls.append(keyword)
        return self.results.get(keyword, [])

    async def get_route(self, *a, **kw):
        return {"distance_km": 5.0, "duration_min": 10}


def _dest_poi():
    return _POI(id="d", name="公司大厦", address="addr", lat=22.5, lng=113.9)


def test_waypoint_person_resolves_via_relation_graph():
    """「途经孩子的学校」：人称词走关系图谱→地点名→POI 取坐标，不搜「孩子的学校」。"""
    agent = NavigationAgent()
    poi = _WpPoi({"南山实验小学": [
        _POI(id="s", name="南山实验小学", address="南山", lat=22.54, lng=113.93)]})
    agent.poi = poi
    ctx = _ctx_with({"person": "小雨", "place": "南山实验小学"})

    res = asyncio.run(agent._navigate_via_waypoint(
        _dest_poi(), "公司大厦", "孩子的学校", [], {}, ctx=ctx))

    assert res.status == "ok"
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and [w["name"] for w in nav[0]["payload"]["waypoints"]] == ["南山实验小学"]
    assert "孩子的学校" not in poi.calls           # 没拿人称描述去搜随机学校


def test_waypoint_person_unknown_asks_to_teach():
    agent = NavigationAgent()
    agent.poi = _WpPoi()
    ctx = _ctx_with(None)

    res = asyncio.run(agent._navigate_via_waypoint(
        _dest_poi(), "公司大厦", "孩子的学校", [], {}, ctx=ctx))

    assert res.status == "need_slot"
    assert "还不知道" in res.speech
    assert not res.actions


def test_waypoint_place_alias_uses_profile():
    """「途经学校」：常用地点别名走画像坐标，不做 POI 搜索。"""
    agent = NavigationAgent()
    poi = _WpPoi()
    agent.poi = poi
    ctx = make_context(context_values={"profile.places": {
        "school": {"name": "市实验小学", "address": "a", "lat": 22.55, "lng": 113.92}}})

    res = asyncio.run(agent._navigate_via_waypoint(
        _dest_poi(), "公司大厦", "学校", [], {}, ctx=ctx))

    assert res.status == "ok"
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert [w["name"] for w in nav[0]["payload"]["waypoints"]] == ["市实验小学"]
    assert poi.calls == []                          # 画像直取，零搜索


def test_waypoint_place_alias_unset_guides_setup():
    agent = NavigationAgent()
    agent.poi = _WpPoi()
    ctx = make_context()

    res = asyncio.run(agent._navigate_via_waypoint(
        _dest_poi(), "公司大厦", "学校", [], {}, ctx=ctx))

    assert res.status == "need_slot"
    assert "还没有设置「学校」" in res.speech


# ── P4 守卫：家人位置陈述不许写本人常用地点 ──

def test_set_place_person_statement_never_writes_profile():
    """「我老婆平时在深圳湾万象城上班」被 planner 映射成 set_place(公司=万象城)，
    把用户自己的公司改写成了家人位置（2026-08-15 真栈恶性实测）——含人称词只口头
    记下，绝不写画像。"""
    agent = NavigationAgent()
    saved = {}
    ctx = make_context()

    async def save_profile(key, value):
        saved[key] = value
    ctx.save_profile = save_profile

    res = asyncio.run(run_handle(
        agent, "navigation.set_place",
        slots={"place": "公司", "address": "深圳湾万象城"},
        raw_text="我老婆平时在深圳湾万象城上班", ctx=ctx))

    assert res.status == "ok"
    assert "记在TA名下" in res.speech or "记下了" in res.speech
    assert saved == {}                       # 画像零写入


def test_set_place_own_home_with_mawan_road_not_blocked():
    """「把家设成妈湾路8号」——单字「妈」在地名里不误伤（守卫只认长词+代词组合）。"""
    agent = NavigationAgent()

    async def search(keyword, **kw):
        from agents.navigation.src.providers.base import POI as _P
        return [_P(id="x", name="妈湾路8号", address="南山区", lat=22.5, lng=113.9)]
    agent.poi.search = search
    saved = {}
    ctx = make_context()

    async def save_profile(key, value):
        saved[key] = value
    ctx.save_profile = save_profile

    res = asyncio.run(run_handle(
        agent, "navigation.set_place",
        slots={"place": "家", "address": "妈湾路8号"},
        raw_text="把家设成妈湾路8号", ctx=ctx))

    assert res.status == "ok"
    assert "places" in saved                 # 本人常用地点照常可设


# ── person-pickup 卡（2026-08-20）：复合句里的接送人称 ─────────────────────
#
# 存量缺陷，两个 provider 同时红 ⇒ 系统缺口不是模型方差（卡 §1）。
# `_person_destination` 的「剥完人称还剩不剩实质内容」判据对**槽值**是对的，
# 套到**整句原话**上天然失效——复合请求必然有剩余内容。
# 修法**不动那条判据**（卡 §5），改成在「这个目的地我接不着」之后再回退人称。

from agents.navigation.src.agent import _pickup_person


@pytest.mark.parametrize("raw,expect", [
    ("接爸妈去吃饭。", "爸妈"),
    ("带我去接孩子放学，顺便帮我找一家麦当劳，5点我要到学校。", "孩子"),
    ("先去接我妈，再找家川菜馆。", "妈"),
    ("送孩子上学，路过买个早餐", "孩子"),
    ("接一下老婆然后去吃饭", "老婆"),
    ("接女儿放学，路上买杯咖啡。", "女儿"),
])
def test_pickup_pattern_survives_composite_sentences(raw, expect):
    assert _pickup_person(raw) == expect


@pytest.mark.parametrize("raw", [
    "接下来去哪吃饭",          # 「接」后面不是人称词
    "导航去机场接机",
    "我接受这个方案",
    "去万象城买东西",
    "",
])
def test_pickup_pattern_does_not_overreach(raw):
    assert _pickup_person(raw) == ""


def test_dual_appellation_is_named_as_a_pair():
    """「接爸妈」问的是两个人——教学问不能说成「你爸爸」（卡 §4.2）。"""
    from agents.navigation.src.agent import _person_display
    assert _pickup_person("接爸妈去吃饭。") == "爸妈"
    assert _person_display("爸妈") == "爸妈"
    assert _person_display("父母") == "爸妈"


def _agent_with_search(results_by_query):
    agent = NavigationAgent()

    async def _fake_correct(dest, raw, meta):
        return dest
    agent._correct_planner_landmark = _fake_correct
    calls = []

    async def _fake_search(keyword, near=None, **kw):
        calls.append(keyword)
        return list(results_by_query.get(keyword, []))
    agent.poi.search = _fake_search
    return agent, calls


def test_composite_pickup_without_place_asks_instead_of_quoting_the_sentence():
    """一#5 原样：「接爸妈去吃饭」接不到地点 ⇒ 教学问，**不是**把整句当地名回读。

    修前话术：「暂时无法确定「接爸妈去吃饭」对应的具体地点。」
    """
    agent, _ = _agent_with_search({})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "接爸妈去吃饭"},
        raw_text="接爸妈去吃饭。", ctx=_ctx_with(None)))
    assert res.status == "need_slot"
    assert "爸妈" in res.speech and "在哪" in res.speech
    assert "上班" in res.follow_up
    assert "暂时无法确定" not in res.speech


def test_composite_pickup_with_place_navigates_there():
    """接不到地点但记忆里有那个人的常去地 ⇒ 用它继续既有导航链路。"""
    school = _POI(id="s1", name="深圳市南山实验教育集团鼎太小学",
                  category="科教文化服务;学校;小学", lat=22.53, lng=113.93)
    agent, calls = _agent_with_search({"深圳市南山实验小学": [school]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "接孩子放学"},
        raw_text="带我去接孩子放学，顺便帮我找一家麦当劳。",
        ctx=_ctx_with({"person": "小雨", "place": "深圳市南山实验小学",
                       "object_ref": ""}),
        meta={"current_lat": "22.5410", "current_lng": "113.9412"}))
    assert res.status == "ok"
    assert "鼎太小学" in res.speech
    assert "深圳市南山实验小学" in calls          # 用的是记忆里的全名


def test_pickup_fallback_never_fires_when_the_destination_grounds():
    """**反向对照（卡 §4.3）**：给了具体地点的复合句不得被改写成那个人的常去地。"""
    mall = _POI(id="m1", name="深圳湾万象城", category="购物服务;商场;购物中心",
                lat=22.51, lng=113.93)
    agent, _ = _agent_with_search({"万象城": [mall]})
    ctx = make_context()

    async def _must_not_resolve(person_word):
        raise AssertionError("目的地接得着时不该回退人称")
    ctx.resolve_person_place = _must_not_resolve

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "万象城"},
        raw_text="接孩子后去万象城。", ctx=ctx,
        meta={"current_lat": "22.5410", "current_lng": "113.9412"}))
    assert res.status == "ok"
    assert "万象城" in res.speech


def test_non_pickup_sentence_keeps_the_old_fallback():
    """反向对照：不是接送句 ⇒ 「暂时无法确定」那条兜底一个字不变。"""
    agent, _ = _agent_with_search({})
    ctx = make_context()

    async def _must_not_resolve(person_word):
        raise AssertionError("非接送句不该查关系图")
    ctx.resolve_person_place = _must_not_resolve

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "某个不存在的地方"},
        raw_text="导航去某个不存在的地方", ctx=ctx))
    assert res.status == "need_slot"
    assert "暂时无法确定" in res.speech


def test_pickup_beats_an_unset_place_alias():
    """PU6 真栈稳定占 2/5：planner 把「接女儿放学」压成 `destination=学校`，
    别名分支抢在人称之前命中 ⇒ 答「您还没有设置「学校」的位置」，
    而库里那个人的地点明明在。**接不着就回退人称**，这里的「接不着」是「没设过」。"""
    school = _POI(id="s1", name="深圳市南山实验教育集团明远学校",
                  category="科教文化服务;学校;中学", lat=22.53, lng=113.93)
    agent, calls = _agent_with_search({"深圳市南山实验小学": [school]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "学校"},
        raw_text="接女儿放学，路上买杯咖啡。",
        ctx=_ctx_with({"person": "小雨", "place": "深圳市南山实验小学",
                       "object_ref": ""}),
        meta={"current_lat": "22.5410", "current_lng": "113.9412"}))
    assert res.status == "ok"
    assert "明远学校" in res.speech
    assert "您还没有设置" not in res.speech


def test_plain_place_alias_still_asks_to_be_set():
    """**反向对照**：原话没有接送人称 ⇒ 「导航去学校」照旧走常用地点设置引导，
    一个字不动（别把这条通用教学链改坏）。"""
    agent, _ = _agent_with_search({})
    ctx = make_context()

    async def _must_not_resolve(person_word):
        raise AssertionError("原话无接送人称时不该查关系图")
    ctx.resolve_person_place = _must_not_resolve

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "学校"},
        raw_text="导航去学校", ctx=ctx))
    assert res.status == "need_slot"
    assert "您还没有设置「学校」的位置" in res.speech


def test_pickup_does_not_override_a_configured_place():
    """**反向对照之二**：常用地点已设置 ⇒ 用它，不许被人称解析顶掉
    （用户明确配过「学校」就是把它当地址簿用）。"""
    agent, _ = _agent_with_search({})

    async def _fake_correct(dest, raw, meta):
        return dest
    agent._correct_planner_landmark = _fake_correct
    ctx = _ctx_with({"person": "小雨", "place": "深圳市南山实验小学", "object_ref": ""})

    async def _get_place(_ctx, key):
        return {"name": "阳光小学", "address": "深圳市南山区某路 1 号",
                "lat": 22.54, "lng": 113.94}
    agent._get_place = _get_place

    # ⚠ 原话必须是**复合句**：短句「接女儿放学」会命中上面那道槽值判据、
    # 根本走不到别名分支，验的就不是这一段了（首版就这么写的，测试当场按住）。
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "学校"},
        raw_text="接女儿放学，路上买杯咖啡。", ctx=ctx,
        meta={"current_lat": "22.5410", "current_lng": "113.9412"}))
    assert "阳光小学" in res.speech


# ── 接送句的就近合理性（卡 §3-B 的收窄版）─────────────────────────────────

_SZ_META = {"current_lat": "22.5410", "current_lng": "113.9412"}


def test_pickup_to_another_province_asks_instead_of_driving():
    """真栈 PU5 七次取样仍有一次导到 1582km 外的济南同名校（来源至今没查清）。
    **来源查不清不等于不能防**：这条只问「接人接到这么远合理吗」。"""
    far = _POI(id="j1", name="济南市南山实验小学",
               category="科教文化服务;科教文化场所;科教文化场所",
               lat=36.5109, lng=117.0261)
    agent, _ = _agent_with_search({"南山实验小学": [far]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "南山实验小学"},
        raw_text="带我去接孩子放学，顺便帮我找一家麦当劳。",
        ctx=_ctx_with(None), meta=_SZ_META))
    assert res.status == "need_slot"
    assert "济南市南山实验小学" in res.speech and "公里" in res.speech
    assert not [a for a in res.actions if a["type"] == "navigate"]


def test_pickup_nearby_is_untouched():
    """反向对照：接人接到本地 ⇒ 一个字不受影响，照常导航。"""
    near = _POI(id="s1", name="深圳市南山实验教育集团明远学校",
                category="科教文化服务;学校;中学", lat=22.529, lng=113.9289)
    agent, _ = _agent_with_search({"南山实验小学": [near]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "南山实验小学"},
        raw_text="带我去接孩子放学。", ctx=_ctx_with(None), meta=_SZ_META))
    assert res.status == "ok" and "明远学校" in res.speech


def test_long_haul_without_pickup_is_untouched():
    """反向对照之二（卡 §4.4）：真实长途原话没有接送人称 ⇒ 这道闸根本不该被执行到。

    ——「什么情况下我这道闸不会被执行到」和「它判得对不对」一样重要（§4.3）。
    """
    bund = _POI(id="b1", name="外滩", category="风景名胜;风景名胜;国家级景点",
                lat=31.2335, lng=121.4921)
    agent, _ = _agent_with_search({"上海外滩": [bund], "外滩": [bund]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "上海外滩"},
        raw_text="导航去上海外滩。", ctx=_ctx_with(None), meta=_SZ_META))
    assert res.status == "ok" and "外滩" in res.speech


def test_pickup_guard_is_skipped_without_a_current_position():
    """没有定位就判不出「多远」——**认不出就不判**，不回落成拒绝。"""
    far = _POI(id="j1", name="济南市南山实验小学",
               category="科教文化服务;科教文化场所;科教文化场所",
               lat=36.5109, lng=117.0261)
    agent, _ = _agent_with_search({"南山实验小学": [far]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "南山实验小学"},
        raw_text="带我去接孩子放学。", ctx=_ctx_with(None), meta={}))
    assert res.status == "ok"


# ── PU6：长会话后段 planner 继承上一次接送的目的地（2026-08-30）────────────

_HOTEL = _POI(id="h1", name="深圳湾万象城桔子水晶酒店",
              category="住宿服务;宾馆酒店", lat=22.5165, lng=113.9463)
_SCHOOL = _POI(id="s9", name="深圳市南山实验教育集团明远学校",
               category="科教文化服务;学校;中学", lat=22.529, lng=113.9289)


def test_pickup_destination_inherited_from_an_earlier_turn_loses_to_the_person():
    """真栈长会话 `538335f` family：**同一条会话里同一句话，前段对、后段错**。

    T9「接女儿放学，路上买杯咖啡。」→ 明远学校（对）；
    T54 同一句 → **深圳湾万象城桔子水晶酒店**（T3/T48「去接老婆」的地点）。
    干净会话 `--repeat 3` 全对 ⇒ **跨轮污染**，不是解析能力问题。

    上面两档都够不着：`_person_destination(dest)` 看到的是一个真地名，
    `_person_destination(raw_text)` 对复合句天然失效（agent.py :76 注释）。
    """
    agent, _ = _agent_with_search({"深圳市南山实验教育集团明远学校": [_SCHOOL],
                                   "深圳湾万象城桔子水晶酒店": [_HOTEL]})
    ctx = _ctx_with({"person": "小雨", "place": "深圳市南山实验教育集团明远学校",
                     "object_ref": ""})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳湾万象城桔子水晶酒店"},
        raw_text="接女儿放学，路上买杯咖啡。", ctx=ctx, meta=_SZ_META))

    assert res.status == "ok"
    assert "明远学校" in res.speech, res.speech
    assert "酒店" not in res.speech


def test_a_destination_the_user_actually_said_is_never_rewritten():
    """误伤对照 ①（卡 §4.3 的原始铁律）：**给了具体地点的接送句不许被改写。**

    「接孩子后去万象城」里的「万象城」明明白白在原话里 ⇒ 一个字不动。
    没有这条护栏，本修法会把这一整类句子改写成那个人的常去地。
    """
    wanted = _POI(id="w1", name="深圳湾万象城", category="购物服务;商场",
                  lat=22.5165, lng=113.9463)
    agent, _ = _agent_with_search({"万象城": [wanted], "深圳湾万象城": [wanted]})
    ctx = _ctx_with({"person": "小雨", "place": "深圳市南山实验教育集团明远学校",
                     "object_ref": ""})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "万象城"},
        raw_text="接孩子后去万象城。", ctx=ctx, meta=_SZ_META))

    assert res.status == "ok" and "万象城" in res.speech


def test_an_inferred_but_correct_destination_is_kept_when_we_know_nothing_better():
    """误伤对照 ②：planner 为接送句填一个**具体校名**是它的正常职责，
    那个名字同样不在原话里。**「不在原话里」只说明它是推断的，不说明它是错的**
    ——查不到这个人的地点时一个字不动（这条第一版没写，当场撞红三条既有用例）。
    """
    agent, _ = _agent_with_search({"南山实验小学": [_SCHOOL]})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "南山实验小学"},
        raw_text="带我去接孩子放学。", ctx=_ctx_with(None), meta=_SZ_META))

    assert res.status == "ok" and "明远学校" in res.speech


def test_a_sentence_without_a_pickup_person_never_reaches_this_guard():
    """误伤对照 ③：原话没有接送人称 ⇒ 这道闸根本不该被执行到
    （「什么情况下它不会被执行到」和「它判得对不对」一样重要）。"""
    agent, _ = _agent_with_search({"深圳湾万象城桔子水晶酒店": [_HOTEL]})
    ctx = _ctx_with({"person": "小雨", "place": "深圳市南山实验教育集团明远学校",
                     "object_ref": ""})
    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to",
        slots={"destination": "深圳湾万象城桔子水晶酒店"},
        raw_text="导航去桔子水晶酒店。", ctx=ctx, meta=_SZ_META))

    assert res.status == "ok" and "酒店" in res.speech
