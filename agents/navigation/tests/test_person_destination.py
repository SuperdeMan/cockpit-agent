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
