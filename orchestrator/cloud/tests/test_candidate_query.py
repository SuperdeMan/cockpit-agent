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

import pytest

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
    """「巨无霸多少钱」是单品查询——没有合计词就不是求和。

    ⚠ **本条改过一次，留痕（2026-08-19，序数取值通道上线）。**
    原文还有一行 `assert cq.answer("第一个多少钱", ...) is None`。
    那一行断言的**不是这条测试的主张**——它主张的是「没有合计词就不是求和」，
    而 `第一个多少钱 → None` 只是「当时只有最值/合计两种算子」的**副产物**，
    被顺手写进了同一条断言里。第三种算子（序数取值）上线后它当场变红。

    > **判据：一条断言里混进了它没打算主张的东西，就会在别处正确演进时假红。**
    > 这与「不为某个模型的问题改案例集」不冲突——那条针对**被测对象做不到**，
    > 这里是**被测对象新增了一个正确能力**，而断言的字面表述过期了。
    > 拆开之后两半各自更强：这里守「不是求和」，
    > `test_ordinal_pick_answers_from_the_card_not_the_model` 守「是取值」。
    """
    assert cq.answer("巨无霸多少钱", _entry(_MENU, intent="mcd.menu")) is None
    # 「第一个多少钱」现在有答案，但**必须不是合计**。
    got = cq.answer("第一个多少钱", _entry(_MENU, intent="mcd.menu"))
    assert got is not None and "一共" not in got and "巨无霸" in got


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


# ── 序数取值型：两条既有守卫之间的缝（2026-08-19，Q10 复跑掀开）───────────
#
# 真栈原话：菜单卡就在上一轮，「麦当劳的第七个多少钱」落到 chitchat，答
# **「第七个是脆汁鸡腿堡，10.90 元」**——第 7 项其实是柠檬脆脆麦旋风 16.00 元，
# **商品名和价格都是编的**。它同时躲开了两条守卫：最值/合计那条只认聚合算子，
# I-052 那条只在**零候选**时触发，而这里是**有候选的单项查询**。

_TEN = [{"name": f"第{i}款", "price": f"{i}.50"} for i in range(1, 11)]


def test_ordinal_pick_answers_from_the_card_not_the_model():
    got = cq.answer("麦当劳的第七个多少钱", _entry(_TEN, intent="mcd.menu"))
    assert got is not None and "第7款" in got and "7.5" in got


@pytest.mark.parametrize("text,expect", [
    ("第一个几点关门", "营业到"),
    ("第 2 个评分多少", "评分"),
    ("第三个多远", "公里"),
])
def test_every_dimension_has_an_ordinal_pick_form(text, expect):
    items = [{"name": "甲", "open_today": "10:00-22:00", "rating": 4.5,
              "distance_km": 0.8},
             {"name": "乙", "open_today": "09:00-21:00", "rating": 4.1,
              "distance_km": 1.4},
             {"name": "丙", "open_today": "08:00-20:00", "rating": 3.9,
              "distance_km": 2.6}]
    got = cq.answer(text, _entry(items))
    assert got is not None and expect in got, text


def test_an_ordinal_followed_by_a_real_noun_is_not_hijacked():
    """**这是本条唯一的收窄手段**：序数与维度问句必须紧邻。

    「第二天第一个景点多少钱」问的是行程内部的第一个景点，不是上一份候选列表。
    分开匹（「句里有序数」+「句里有多少钱」）会把行程、菜谱、清单里的任何序数
    都吞掉，而这条短路误伤的代价是**整轮不进 Planner**。
    同 `context._CANDIDATE_REFERENCE_RE` 头上那段的判据来源。
    """
    assert cq.answer("第二天第一个景点多少钱", _entry(_TEN)) is None
    assert cq.answer("行程里第一个城市的酒店多少钱", _entry(_TEN)) is None


def test_a_new_search_still_wins_over_the_ordinal_pick():
    """「附近第七个多少钱」是新检索，三段判据的第三段仍然管着它。"""
    assert cq.answer("附近第七个多少钱", _entry(_TEN)) is None


def test_out_of_range_says_what_the_system_actually_kept():
    """越界要**诚实说系统记得多少**，不是说「列表只有 N 项」——那是假话。

    候选集裁到 10 项而卡片渲染 20 项，用户看得见第 15 项。说「这份列表只有 10 项」
    等于用一句确定的话说错一件事，比不答更糟。
    """
    got = cq.answer("第十五个多少钱", _entry(_TEN, intent="mcd.menu"))
    assert got is not None
    assert "只跟到第 10 项" in got and "第 15 项" in got
    assert "列表只有" not in got


def test_missing_dimension_is_an_honest_refusal_not_a_fallback():
    got = cq.answer("第一个多少钱",
                    _entry([{"name": "甲"}, {"name": "乙"}]))
    assert got is not None and "甲" in got and "没带价格" in got


def test_a_single_item_candidate_set_still_answers_an_ordinal_pick():
    """只读菜单命中单品后候选集只剩一项——「第一个多少钱」那时照样有答案。

    最值/合计仍要求 ≥2 项（一项没有「哪家最…」可言），序数取值没有这条限制。
    """
    one = [{"name": "柠檬脆脆麦旋风", "price": "16.00"}]
    assert cq.answer("第一个多少钱", _entry(one, intent="mcd.menu")) is not None
    assert cq.answer("哪个最便宜", _entry(one, intent="mcd.menu")) is None


def test_the_total_operator_still_wins_when_both_could_match():
    """「第一个和第二个一共多少钱」是合计不是取值——序数后面是「和」，不紧邻。"""
    got = cq.answer("第一个和第二个一共多少钱", _entry(_TEN, intent="mcd.menu"))
    assert got is not None and "一共" in got


# ── I-030 跨组：点名了两组，就不该由其中一组独自回答 ──────────────────────
#
# 真栈取证把卡上的定性改了一档：卡写「跨组比较做不了」（答非所问），而实测是
# **跨组会给出一个算错的确定性答案**——「麦当劳的第二个多少钱」绑到瑞幸那组，
# 零方差地答「「生椰拿铁」16 元」。名字与价格都真实存在，所以比编造更难被发现。
#
# ⚠ 本节**依然一半是误伤对照**，理由与本文件开头那段同源，而且更强：跨组的错
# 会把两家的东西比成一家的，而话术里两个名字都在，看起来毫无异常。

_G_MCD = {"source_intent": "mcd.menu", "agent_id": "mcp-bridge", "purpose": "list",
          "ts": time.time() - 60, "is_fallback": False, "label": "麦当劳",
          "items": [{"name": "巨无霸", "price": "26.50"},
                    {"name": "麦辣鸡腿堡", "price": "19.50"}]}
_G_LUCKIN = {"source_intent": "luckin.menu", "agent_id": "mcp-bridge",
             "purpose": "list", "ts": time.time(), "is_fallback": False,
             "label": "瑞幸",
             "items": [{"name": "美式", "price": "15.00"},
                       {"name": "生椰拿铁", "price": "16.00"}]}
_TWO = [_G_MCD, _G_LUCKIN]


def test_naming_one_group_answers_from_that_group_not_the_newest():
    """I-030 的核心读数：同一句话，修前答瑞幸的第二个、修后答麦当劳的第二个。"""
    got = cq.answer("麦当劳的第二个多少钱", _G_MCD, [_G_MCD])
    assert got is not None and "麦辣鸡腿堡" in got and "生椰拿铁" not in got


def test_cross_group_comparison_states_both_sides_before_the_verdict():
    """跨组结论只有一个词（「更贵」），用户没法核对它是不是拿对了组
    ——所以两边的数都要念出来。**拿错组恰恰是这条通道要修的病。**"""
    got = cq.answer("麦当劳的第二个和瑞幸的第二个哪个贵", _G_LUCKIN, _TWO)
    assert got is not None
    assert "麦当劳的「麦辣鸡腿堡」" in got and "瑞幸的「生椰拿铁」" in got
    assert got.endswith("「麦辣鸡腿堡」更贵。")


def test_cross_group_total_keeps_the_arithmetic_checkable():
    got = cq.answer("麦当劳的第二个和瑞幸的第二个一共多少钱", _G_LUCKIN, _TWO)
    assert got is not None and " + " in got and "一共 35.50 元" in got


def test_cross_group_ordinals_do_not_leak_across_groups():
    """两个「第二个」必须各归各组。不切段而在整句里找序数，就会把它们都塞给
    同一组——那正是这条通道要修的错，只是换了个地方发生。"""
    got = cq.answer("麦当劳的第一个和瑞幸的第二个一共多少钱", _G_LUCKIN, _TWO)
    assert got is not None and "巨无霸" in got and "生椰拿铁" in got
    assert "一共 42.50 元" in got


def test_cross_group_names_work_too_not_only_ordinals():
    got = cq.answer("麦当劳的巨无霸和瑞幸的美式哪个便宜", _G_LUCKIN, _TWO)
    assert got is not None and got.endswith("「美式」更便宜。")


def test_a_tie_is_reported_as_a_tie_not_as_a_winner():
    """相等时点名一个「更贵」，是用一句确定的话说错一件事。"""
    same = dict(_G_LUCKIN, items=[{"name": "美式", "price": "26.50"}])
    got = cq.answer("麦当劳的第一个和瑞幸的第一个哪个贵", same, [_G_MCD, same])
    assert got is not None and got.endswith("两边价格一样。")


def test_cross_group_missing_dimension_is_an_honest_refusal():
    """菜单项没有营业时间——诚实说比不了，**不回落 LLM**（回落就是交回去编）。"""
    got = cq.answer("麦当劳的第一个和瑞幸的第一个哪个关门更晚", _G_LUCKIN, _TWO)
    assert got is not None and "没带营业时间" in got and "比不了" in got


def test_a_group_that_cannot_be_pinned_to_exactly_one_item_aborts_the_whole_answer():
    """任一组点不到**恰好一项**就整句放弃——同 `candidate_ref` 那条
    「命中多项一律不动」。跨组的错比单组贵。"""
    assert cq.answer("麦当劳和瑞幸的第二个哪个贵", _G_LUCKIN, _TWO) is None


def test_an_out_of_range_ordinal_is_answered_honestly_not_handed_back_to_the_llm():
    """越界与「点不到」是两件事：「第九个」是**明确的**引用，只是我们跟不到
    那么远 ⇒ 诚实说系统记得多少（同单组 `_ordinal_pick_answer` 那条判据）。

    ⚠ 说的是「我这边只跟到第 N 项」不是「这份列表只有 N 项」——候选集裁到 10 项
    而卡片渲染 20 项，后者是用一句确定的话说错一件事。
    """
    got = cq.answer("麦当劳的第九个和瑞幸的第二个哪个贵", _G_LUCKIN, _TWO)
    assert got is not None and "只跟到第 2 项" in got and "第 9 项" in got
    assert "列表只有" not in got


def test_the_overflow_answer_still_requires_an_operator():
    """**误伤对照**：算子闸排在解析之前。没有「哪个更…／一共」的句子不该被
    越界话术接管——那是把误伤面往回放宽。"""
    assert cq.answer("麦当劳的第九个和瑞幸的第二个", _G_LUCKIN, _TWO) is None


def test_the_comparative_table_does_not_widen_the_single_group_gate():
    """**误伤对照**：比较级只在点名了 ≥2 组时求值。单组句子命中这张表也拿不到
    答案，照常进 Planner——本表要求的条件比现状更严，不是又放宽一道口子。"""
    assert cq.answer("哪个更贵", _entry(_MENU, intent="mcd.menu")) is None
    assert cq.answer("哪个更贵", _G_MCD, [_G_MCD]) is None


def test_a_new_search_still_wins_over_cross_group():
    """**误伤对照**：「附近还有别的麦当劳和瑞幸吗，哪个便宜」是一次新检索。"""
    assert cq.answer("附近还有别的麦当劳和瑞幸吗，哪个便宜",
                     _G_LUCKIN, _TWO) is None


def test_an_unnamed_sentence_keeps_the_old_single_group_behaviour():
    """**误伤对照**：没点名任何组时，逐字还是修改之前那条路。"""
    got = cq.answer("第二个多少钱", _G_LUCKIN, [])
    assert got is not None and "生椰拿铁" in got


# ── 重列型：C4-C（2026-08-28，MiniMax QA 修复批第 2 批）──────────────────
#
# 真栈 merchant T19/T20 连着两次说「重新列出刚才可以选择的项目」，前三种算子
# 一条都不认（它们全要求「算子 + 维度」），于是整句进 Planner **重搜了一遍**：
# 两次搜回不同城市、不同门店的列表，第二次 LLM 还凭空把检索地点定到青岛平度。
# **用户要的是「刚才那份」，系统手里就有那份，却给了他一份新的。**
#
# ⚠ 本节同样带误伤对照：重列的判据只有一段（算子词），比其余三种都松，
# 所以「附近再列一遍」这类新检索必须仍然放行。

def test_relist_replays_the_ledger_verbatim_instead_of_researching():
    got = cq.answer("重新列出刚才可以选择的项目", _entry(_MENU, intent="mcd.menu"))
    assert got is not None
    for index, item in enumerate(_MENU, start=1):
        assert f"{index}. {item['name']}" in got, got


def test_relist_carries_the_price_the_ledger_actually_kept():
    """序号 + 名字 + 价格——**台账里有的那几键**，一个字都不补。"""
    got = cq.answer("再列一遍", _entry(_MENU, intent="mcd.menu"))
    assert "26.5 元" in got and "9.5 元" in got


def test_relist_says_where_the_buttons_are():
    """候选台账里没有 `send_text`（白名单刻意不含交互载体）⇒ 文字清单回不到
    那张卡的按钮。不说清就等于让用户以为按钮没了。"""
    got = cq.answer("重新列出刚才的选项", _entry(_MENU, intent="mcd.menu"))
    assert "按钮" in got and "刚才那张卡" in got


def test_relist_without_price_still_lists_names():
    """没有价格的候选（nearby 那类）照样能重列——**缺的维度不补，不是不答**。"""
    got = cq.answer("再列一遍", _entry([{"name": "甲咖啡"}, {"name": "乙咖啡"}]))
    assert got is not None and "1. 甲咖啡" in got and "2. 乙咖啡" in got


def test_relist_is_verbatim_stable():
    """确定性的直接证据是零方差。"""
    entry = _entry(_MENU, intent="mcd.menu")
    assert cq.answer("重新列出刚才可以选择的项目", entry) == \
        cq.answer("重新列出刚才可以选择的项目", entry)


@pytest.mark.parametrize("text", [
    "附近再列一遍咖啡店",          # 新检索指示词在场
    "帮我搜一下再列出来",          # 同上
    "换一批重新列出",              # 同上
])
def test_a_new_search_still_wins_over_relist(text):
    """**误伤对照**：带新检索指示词的句子照常进 Planner。"""
    assert cq.answer(text, _entry(_MENU, intent="mcd.menu")) is None


@pytest.mark.parametrize("text", [
    "第二个多少钱",                # 序数取值，不是重列
    "哪家最晚关门",                # 最值
    "把第一个加入购物车",          # 指令
    "刚才那家店叫什么",            # 追问单项
])
def test_ordinary_follow_ups_are_not_swallowed_by_relist(text):
    """**误伤对照**：重列词表不许把其它说法一起吃掉。"""
    got = cq.answer(text, _entry(_MENU, intent="mcd.menu"))
    assert got is None or "按钮还在刚才那张卡上" not in got


def test_relist_with_no_candidates_hands_back_to_the_planner():
    """零候选时**不劫持**——这条短路的存在理由是「手里有那份却给了新的」，
    手里没有那份时它没有立场说话（弃权那条由 I-052 守卫按序数指代管）。"""
    assert cq.answer("重新列出刚才可以选择的项目", _entry([])) is None


def test_relist_answers_from_the_named_group_not_the_newest():
    """组指代对重列同样成立：点名了麦当劳就重列麦当劳那份。"""
    got = cq.answer("重新列出麦当劳刚才可以选择的项目", _G_MCD, [_G_MCD])
    assert got is not None and "巨无霸" in got and "生椰拿铁" not in got
