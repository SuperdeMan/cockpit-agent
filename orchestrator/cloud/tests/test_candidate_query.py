"""候选集聚合问答（`candidate_query.py`，QA Q2 残余 2026-08-19）。

治的是 I-018「已有营业时间却答未查到」与 I-023「已知两项价格却不算合计」。

**本文件一半的用例是误伤对照组**，这是刻意的：这条短路挂在 `_orchestrate` 里
plan 构建**之前**，看到的是全部流量，命中即整轮不进 Planner。判据宽一格的代价
不是「答得差一点」，是「正常请求被吞掉」——同 Q6 那条「chitchat 兜底看到全部流量，
所以判据必须窄」，只是这里高一档。

真栈基线（2026-08-19，cloud 档 release 34d72d7）：
  · CD1「附近的咖啡店」→「哪家最晚关门？」→ 答**「你想对这家咖啡店做什么？」**
    ——注意它没说错话，所以原判据（不许说「未查到」）**本来会判绿**；
  · CD4「看看麦当劳有什么可以点的」→「第一个和第二个一共多少钱」→ 答
    **「我这边没有可以引用的列表。」**——菜单从没进过候选集，于是 I-052
    那条防编造的弃权守卫在这里变成了误伤。
"""
from __future__ import annotations

import time

from orchestrator.cloud import candidate_query as cq
from orchestrator.cloud.context import Focus, newest_candidate_set

# 逐字复刻 `nearby._item()` 的产出（经白名单裁剪后的形状）。
_CAFES = [
    {"id": "a", "name": "甲咖啡", "open_today": "07:00-21:00", "rating": 4.2,
     "cost": "30", "distance_km": 0.4},
    {"id": "b", "name": "乙咖啡", "open_today": "09:00-23:00", "rating": 4.8,
     "cost": "55", "distance_km": 1.2},
    {"id": "c", "name": "丙咖啡", "open_today": "10:00-01:00", "rating": 3.9,
     "cost": "18", "distance_km": 2.5},
]
# 逐字复刻商户 `_menu_item()`。
_MENU = [
    {"id": "1", "name": "巨无霸", "price": "26.50"},
    {"id": "2", "name": "可乐", "price": "9.50"},
    {"id": "3", "name": "薯条", "price": "12.00"},
]


def _entry(items, **kw):
    return {"source_intent": kw.get("intent", "nearby.search"),
            "agent_id": "nearby", "purpose": "list", "ts": time.time(),
            "is_fallback": False, "items": items}


# ── 最值型：I-018 ─────────────────────────────────────────────────────────

def test_latest_closing_picks_the_cross_midnight_one():
    """「丙咖啡」营业到次日 01:00，必须赢过「乙咖啡」的 23:00。

    这是 I-018 的核心判定，也是把收盘时刻归一到 `>1440` 的理由。
    """
    got = cq.answer("哪家最晚关门？", _entry(_CAFES))
    assert got is not None and "丙咖啡" in got
    assert "次日 01:00" in got
    assert "甲咖啡" not in got and "乙咖啡" not in got


def test_earliest_closing_is_the_other_direction():
    got = cq.answer("这几家哪家最早关门", _entry(_CAFES))
    assert got is not None and "甲咖啡" in got and "21:00" in got


def test_cheapest_and_best_rated_and_nearest():
    assert "丙咖啡" in cq.answer("哪个最便宜", _entry(_CAFES))
    assert "乙咖啡" in cq.answer("哪家评分最高", _entry(_CAFES))
    assert "甲咖啡" in cq.answer("这几家哪个最近", _entry(_CAFES))


def test_ties_are_reported_together():
    """并列第一就一起报——**不许静默挑一个**，那是把「有两个答案」说成「有一个」。"""
    tied = [{"name": "甲", "open_today": "09:00-22:00"},
            {"name": "乙", "open_today": "10:00-22:00"}]
    got = cq.answer("哪家最晚关门", _entry(tied))
    assert "甲" in got and "乙" in got


def test_partial_coverage_is_disclosed():
    """只有部分候选带营业时间时，把覆盖度说出来——**别让 2/10 读起来像 10/10**。"""
    mixed = _CAFES + [{"name": "丁咖啡"}, {"name": "戊咖啡"}]
    got = cq.answer("哪家最晚关门", _entry(mixed))
    assert "3/5" in got


def test_no_data_is_an_honest_deterministic_abstention():
    """一条营业时间都没有 ⇒ 确定性说算不出来，**不回落 LLM**。

    回落就等于交回去编——I-052 真栈原样复现过「无任何候选时编出一整条营业记录」。
    """
    bare = [{"name": "甲"}, {"name": "乙"}]
    got = cq.answer("哪家最晚关门", _entry(bare))
    assert got is not None and "营业时间" in got and "算不出来" in got
    # 而且不许出现任何钟点——那就是编造的形态判据（同 CD3）
    assert ":" not in got


# ── 合计型：I-023 ─────────────────────────────────────────────────────────

def test_ordinal_total_is_computed_not_guessed():
    got = cq.answer("第一个和第二个一共多少钱", _entry(_MENU, intent="mcd.menu"))
    assert got is not None and "36.00" in got
    assert "巨无霸 26.50" in got and "可乐 9.50" in got


def test_named_total_matches_i023_verbatim():
    """I-023 的原话说的是**名字**不是序数：「巨无霸 26.50、可乐 9.50，问总价」。"""
    got = cq.answer("巨无霸和可乐这两个一共多少钱", _entry(_MENU, intent="mcd.menu"))
    assert got is not None and "36.00" in got


def test_three_way_total():
    got = cq.answer("第一个第二个第三个加起来多少钱", _entry(_MENU, intent="mcd.menu"))
    assert "48.00" in got


def test_out_of_range_ordinal_is_ignored_not_wrapped():
    """「第五个」在三项列表里不存在——**不许回绕到第二个**（`% len` 那类聪明写法）。"""
    got = cq.answer("第一个和第五个一共多少钱", _entry(_MENU, intent="mcd.menu"))
    assert got is None          # 点不到两项 ⇒ 交回 Planner，不瞎算


def test_missing_price_says_which_one():
    priceless = [{"name": "巨无霸", "price": "26.50"}, {"name": "神秘餐品"}]
    got = cq.answer("第一个和第二个一共多少钱", _entry(priceless, intent="mcd.menu"))
    assert got is not None and "神秘餐品" in got and "算不出来" in got


# ── 误伤对照组：这些**必须**放行给 Planner ────────────────────────────────

def test_a_new_search_is_never_hijacked():
    """「附近最便宜的加油站」是一次新检索，不是对当前列表的聚合。"""
    for text in ("附近最便宜的加油站", "周边哪家评分最高的火锅",
                 "帮我找最近的充电站", "这附近有没有更便宜的"):
        assert cq.answer(text, _entry(_CAFES)) is None, text


def test_single_item_price_query_is_not_a_total():
    """「巨无霸多少钱」是单品查询——没有合计词就不是求和。"""
    assert cq.answer("巨无霸多少钱", _entry(_MENU, intent="mcd.menu")) is None
    assert cq.answer("第一个多少钱", _entry(_MENU, intent="mcd.menu")) is None


def test_itinerary_ordinal_is_not_a_candidate_reference():
    """「第二天第一个景点」指的是行程内部——`references_a_candidate` 锚在句首
    正是为了这个，本模块不该把它抢过来。"""
    assert cq.answer("第二天第一个景点安排什么", _entry(_CAFES)) is None


def test_no_operator_means_no_hijack():
    """有引用指示词但没有算子 ⇒ 放行。「哪家好吃」要的是推荐不是聚合。"""
    for text in ("哪家好吃", "这几家哪个适合办公", "第一个的电话是多少"):
        assert cq.answer(text, _entry(_CAFES)) is None, text


def test_superlative_without_a_reference_word_is_left_alone():
    """「最晚关门的呢」**刻意不劫持**——判据宁可漏说法，不误伤。"""
    assert cq.answer("最晚关门的呢", _entry(_CAFES)) is None


def test_empty_or_thin_candidate_set_is_left_alone():
    assert cq.answer("哪家最晚关门", None) is None
    assert cq.answer("哪家最晚关门", _entry([])) is None
    assert cq.answer("哪家最晚关门", _entry([_CAFES[0]])) is None   # 一项无从比较


def test_car_control_is_never_touched():
    """安全对照：车控/危险动作一句都不该命中本模块（它压根不产动作，但判据也要窄）。"""
    for text in ("哪个车窗关了", "第一个空调调到最低", "把全车门解锁"):
        assert cq.answer(text, _entry(_CAFES)) is None, text


# ── 确定性 ───────────────────────────────────────────────────────────────

def test_same_input_same_answer_verbatim():
    """确定性的直接证据就是零方差（同 CD3「三次话术逐字相同」）。"""
    outs = {cq.answer("哪家最晚关门？", _entry(_CAFES)) for _ in range(5)}
    assert len(outs) == 1


def test_it_reads_the_same_candidate_set_the_ordinal_guard_reads():
    """绑哪一组的口径只有一份——`newest_candidate_set`，本模块不发明第二套。

    这里顺带钉住「兜底那份不赢」：I-011/N5 的纪律对聚合问答同样成立。
    """
    focus = Focus()
    focus.candidate_sets = [
        _entry(_CAFES),
        {**_entry([{"name": "兜底甲", "open_today": "00:00-23:59"}]),
         "is_fallback": True},
    ]
    entry = newest_candidate_set(focus, allow_fallback=True)
    got = cq.answer("哪家最晚关门", entry)
    assert "丙咖啡" in got and "兜底甲" not in got


def test_bare_named_total_without_any_reference_word():
    """I-023 的原话形态：**只有名字，一个指示词都没有**。名字通道就是为它加的。"""
    got = cq.answer("巨无霸和可乐一共多少钱", _entry(_MENU, intent="mcd.menu"))
    assert got is not None and "36.00" in got


def test_one_name_is_not_a_reference():
    """只点到一个名字 ⇒ 名字通道不成立（那更像单品查询）。

    「≥2 个」这个下限是刻意的：一个名字 + 合计词很可能是「这杯一共多少钱」
    在问加料后的单杯总价，那不是本模块的事。
    """
    assert cq.answer("巨无霸一共多少钱", _entry(_MENU, intent="mcd.menu")) is None


def test_named_channel_still_respects_the_new_search_exclusion():
    """名字通道不许绕过新检索排除——「附近有没有巨无霸和可乐」是找店不是算钱。"""
    assert cq.answer("附近哪里有巨无霸和可乐一共多少钱",
                     _entry(_MENU, intent="mcd.menu")) is None


def test_real_corpus_lines_are_not_hijacked():
    """拿**仓库里真实存在的语料句**当回归探针，不是我编的边界例。

    2026-08-19 全仓扫算子词，命中两处且是同一句：
    `skills/exemplars/nearby.yaml:176` 与 `test/journeys/target_a.yaml:41`。
    它带「附近」⇒ 新检索排除生效。**没有那条排除，这条 journey 会被短路吞掉**，
    而红灯会出现在 journeys 里、很难追回本模块。
    """
    assert cq.answer("找一家附近评分最高的川菜馆，直接导航过去",
                     _entry(_CAFES)) is None


def test_a_name_contained_in_another_name_is_not_double_counted():
    """菜单里「拿铁」与「生椰拿铁」并存——裸子串匹配会命中三项、合计多算一杯。

    **一个算错的确定性答案比不答更糟**：它没有 LLM 那种「听起来不太确定」的信号，
    用户会直接照着付钱。剔除被包含项后按最长匹配算。
    """
    menu = [{"name": "拿铁", "price": "20.00"},
            {"name": "生椰拿铁", "price": "32.00"},
            {"name": "美式", "price": "15.00"}]
    got = cq.answer("生椰拿铁和美式一共多少钱", _entry(menu, intent="luckin.menu"))
    assert got is not None and "47.00" in got      # 32 + 15，不是 67
    assert "拿铁 20.00" not in got


def test_both_overlapping_names_asked_together_falls_back_to_the_planner():
    """真要「拿铁和生椰拿铁」两杯都算时，剔除后只剩一项 ⇒ 交回 Planner。

    **保守方向是刻意的**：这条路径答不了总比答错好，而 Planner 那边还有别的办法。
    """
    menu = [{"name": "拿铁", "price": "20.00"},
            {"name": "生椰拿铁", "price": "32.00"}]
    assert cq.answer("拿铁和生椰拿铁一共多少钱",
                     _entry(menu, intent="luckin.menu")) is None
