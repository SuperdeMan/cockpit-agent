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
