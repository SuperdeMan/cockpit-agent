"""C6-A/B：分句级 route_hint 与焦点让路（2026-08-28，QA P1-03）。

背景：接送 hint 原本只锚**整句形态**，注释里当时明写「带后续目的地的复合句均不命中」
——那是刻意把复合句留给 LLM。真栈把这笔账收了：T47「接爸妈去吃饭」→ 川菜列表零接人、
T53「接孩子后去万象城」→ 商场列表、T55「先去接我妈，再找家川菜馆」→ 被三轮前的
「万象城」焦点劫持成「哪个城市的万象城？」；而同形态的 T48（换成儿子）却走对了。

这里验两件事：① 分句档的匹配面与去重判据；② 命中分句档时粘性地点焦点让路。
全部用真实的 `agents/navigation/manifest.yaml`——**hint 是声明，测声明本身才作数**。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.cloud.models import Plan, Step
from orchestrator.cloud.route_hints import RouteHintEngine


def _nav_manifest():
    from agents._sdk.manifest import load_manifest
    return load_manifest("agents/navigation/manifest.yaml")


@pytest.fixture(scope="module")
def agent_map():
    manifest = _nav_manifest()
    return {"navigation": SimpleNamespace(manifest=manifest,
                                          endpoint="stub:50051")}


def _engine():
    """validate_steps 替身：把 raw dict 原样变成 Step（本测只关心 hint 判定）。"""
    def validate(raw_steps, _agent_map):
        return [Step(id=r["id"], agent_id=r["agent_id"], intent=r["intent"],
                     slots=dict(r["slots"]), depends_on=[], slot_refs={})
                for r in raw_steps]
    return RouteHintEngine(validate)


def _apply(text, steps, agent_map):
    plan = Plan(steps=list(steps))
    _engine().apply(plan, text, agent_map)
    return plan


def _nav_slots(plan):
    return [s.slots for s in plan.steps if s.intent == "navigation.navigate_to"]


# ── ① 分句档命中面：「接X + 任意后续」都要保住接人那一半 ──────────────

@pytest.mark.parametrize("text,person", [
    ("接爸妈去吃饭", "爸妈"),                       # T47
    ("接孩子后去万象城", "孩子"),                    # T53
    ("先去接我妈，再找家川菜馆", "妈"),               # T55
    ("接孩子放学，顺便找麦当劳，5点到校", "孩子"),      # T50
])
def test_pickup_clause_is_appended_even_inside_a_compound_sentence(
        text, person, agent_map):
    plan = _apply(text, [Step(id="s1", agent_id="nearby", intent="nearby.search",
                              slots={"cuisine": "川菜"})], agent_map)
    assert {"destination": person} in _nav_slots(plan)
    # append 不是接管：LLM 原来那一半原样留着
    assert any(s.intent == "nearby.search" for s in plan.steps)


def test_the_old_whole_sentence_hint_still_wins_where_it_already_matched(agent_map):
    """对照：既有的「接送+顺路词+停靠类目」整句 replace 档（priority=127）优先级更高，
    行为逐字不变——它一次给出**两个**槽，比分句档补出来的更完整。
    ⚠ 这条是写测试时撞出来的：首版把「送孩子上学，路上买杯咖啡」也放进上面那组，
    读数是 `{destination:孩子, stop_category:咖啡}`——**期望写窄了，红的是尺子。**"""
    plan = _apply("送孩子上学，路上买杯咖啡",
                  [Step(id="s1", agent_id="nearby", intent="nearby.search",
                        slots={"cuisine": "川菜"})], agent_map)
    assert _nav_slots(plan) == [{"destination": "孩子", "stop_category": "咖啡"}]


@pytest.mark.parametrize("text", [
    "不是去接孩子，直接回家",        # 正向前缀闭集天然落空
    "别去接孩子了",                 # guard
    "提醒我接孩子放学",             # guard：这是建提醒不是导航
    "接下来去哪",                   # 无亲属词
    "接机",
    "我接受这个方案",
])
def test_negative_and_lookalike_clauses_are_not_appended(text, agent_map):
    plan = _apply(text, [Step(id="s1", agent_id="chitchat", intent="chitchat.talk",
                              slots={})], agent_map)
    assert _nav_slots(plan) == []


# ── ② 去重判据：clause 档按**值**判，不是按 intent 判 ────────────────

def test_an_unrelated_navigate_step_does_not_swallow_the_pickup_half(agent_map):
    """T53 的要害：计划里 `navigate_to(destination=万象城)` 确实在，但它回答的是
    **另一半**诉求。按 intent 去重会把接人那一半再丢一次。"""
    plan = _apply("接孩子后去万象城",
                  [Step(id="s1", agent_id="navigation",
                        intent="navigation.navigate_to",
                        slots={"destination": "万象城"})], agent_map)
    assert {"destination": "孩子"} in _nav_slots(plan)
    assert {"destination": "万象城"} in _nav_slots(plan)


def test_the_pickup_half_is_not_appended_twice(agent_map):
    """LLM 已经把接人那半规划对了 ⇒ 值级判据认出来，不重复补步。"""
    plan = _apply("接孩子后去万象城",
                  [Step(id="s1", agent_id="navigation",
                        intent="navigation.navigate_to",
                        slots={"destination": "孩子"})], agent_map)
    assert _nav_slots(plan) == [{"destination": "孩子"}]


def test_a_bare_pickup_sentence_still_goes_through_the_replace_hint(agent_map):
    """裸接送句由 priority=128 的整句 replace 档接住并**命中即停**，
    分句档轮不到——所以不会出现两步接同一个人。"""
    plan = _apply("去接我爸", [Step(id="s1", agent_id="chitchat",
                                   intent="chitchat.talk", slots={})], agent_map)
    assert _nav_slots(plan) == [{"destination": "爸"}]
    assert all(s.intent != "chitchat.talk" for s in plan.steps)   # replace 语义


# ── ③ guard 仍对整句求值（放宽锚定 ≠ 放宽守卫）─────────────────────────

def test_guard_is_still_evaluated_on_the_whole_utterance(agent_map):
    """clause 档只放宽了 `pattern` 的锚定范围，**没有放宽 `guard`**。

    ⚠ 这条是写实现时先做反了、跑全量撞红三条既有负向锁才纠回来的：首版把 guard
    也收进分句（想让「接孩子，别忘了充电」不被另一句的「别」误杀），代价是
    「接女儿放学，路上买杯咖啡，然后播放音乐」这类**另一个域的诉求**失去守卫。
    **两件事只做一件**：误伤一条正向句，比放开一整面守卫便宜。
    """
    plan = _apply("接孩子放学，别忘了充电",
                  [Step(id="s1", agent_id="charging", intent="charging.find",
                        slots={})], agent_map)
    assert _nav_slots(plan) == []


# ── ④ C6-B 焦点让路 ──────────────────────────────────────────────────

def test_clause_scope_match_is_visible_to_the_caller(agent_map):
    assert _engine().matches_clause_scope("先去接我妈，再找家川菜馆", agent_map)
    assert not _engine().matches_clause_scope("找家川菜馆", agent_map)


def test_sticky_place_focus_yields_to_a_pickup_turn():
    """T55 的劫持链：三轮前的「万象城」「上海」还挂在焦点里。
    让路只挡**粘性地点/候选**，安全告警与车控对象焦点原样保留。"""
    from orchestrator.cloud.context import Focus, WorkingSet
    focus = Focus(last_intent="nearby.search", obj="window",
                  last_poi="万象城", last_destination="万象城",
                  last_city="上海", last_choices=["川菜1", "川菜2"])
    ws = WorkingSet(focus=focus)
    assert "万象城" in ws.render_context()
    ws.suppress_sticky_places = True
    rendered = ws.render_context()
    assert "万象城" not in rendered and "上海" not in rendered
    assert "川菜1" not in rendered
    assert "上一轮意图=nearby.search" in rendered      # 让路名单之外的原样保留
    assert "对象=window" in rendered
