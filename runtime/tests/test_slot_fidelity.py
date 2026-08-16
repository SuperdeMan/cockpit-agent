"""槽值保真（Q12）：回填判据的正反两向。

⚠ **反向对照占一半以上是刻意的**。这条机制唯一的危险是**回填错**——那等于系统
替用户改了他说的话，比不回填糟得多。所以每条「该补」都配一条形态相近的「不许补」。
"""
from __future__ import annotations

from runtime.slot_fidelity import (restore_dropped_qualifiers,
                                   restore_time_qualifiers)

I008 = "明天下午四点提醒我开会，三点半再提醒我一次"


# ── 该补的 ────────────────────────────────────────────────────────────
def test_inherits_day_and_segment_from_the_earlier_phrase():
    """I-008 本体：第二个时刻把「明天下午」丢了，原话里明摆着。"""
    value, reason = restore_time_qualifiers(I008, "三点半")
    assert value == "明天下午三点半"
    assert reason == "time_qualifier:+明天下午"


def test_restores_only_the_dimension_that_was_dropped():
    """**逐维补**，不是「整值裸时刻才补」——真栈实测 planner 产的是「明天三点半」。

    首版只认裸时刻，于是这个**日留下、段位丢了**的形态原样放行，落成次日 03:30
    ——把 I-008 的原始症状（03:30 + speech 说下午）在修复之后又复现了一次。
    这是「防御要防到真正会被拿去用的那个值」的时间版。
    """
    assert restore_time_qualifiers(I008, "明天三点半") == ("明天下午三点半",
                                                           "time_qualifier:+下午")
    assert restore_time_qualifiers(I008, "下午三点半") == ("明天下午三点半",
                                                           "time_qualifier:+明天")


def test_takes_the_qualifier_glued_to_the_value_first():
    """紧邻优先：值前面直接贴着限定词时用它，不去更远处继承。"""
    raw = "明天下午四点开会，后天三点半再提醒我"
    assert restore_time_qualifiers(raw, "三点半")[0] == "后天三点半"


def test_segment_only():
    assert restore_time_qualifiers("下午四点开会，五点再叫我", "五点")[0] == "下午五点"


def test_day_only():
    assert restore_time_qualifiers("明天八点开会，十点提醒我", "十点")[0] == "明天十点"


def test_hhmm_value_is_also_covered():
    raw = "明天下午16:00开会，15:30再提醒我"
    assert restore_time_qualifiers(raw, "15:30")[0] == "明天下午15:30"


# ── 不许补的（反向对照）──────────────────────────────────────────────
def test_value_that_already_has_a_qualifier_is_untouched():
    assert restore_time_qualifiers(I008, "明天下午四点") == ("明天下午四点", "")


def test_adverbial_without_a_clock_is_not_a_qualifier():
    """「早上跑完步」里的「早上」修饰的是跑步，不是某个时刻。

    这是 E2 那条「**话里提到了人**不等于**这条记忆是关于那个人的**」的时间版：
    段位词出现过 ≠ 它管着这个时刻。
    """
    raw = "早上跑完步，提醒我三点半吃药"
    assert restore_time_qualifiers(raw, "三点半") == ("三点半", "")


def test_unparsed_date_qualifier_makes_it_stand_down():
    """值前面贴着本模块不解析的日期词（周三/15号）→ 一律不动。

    继承会给出「明天下午」，而原话写的是「周三」——**回填错比不回填糟**。
    """
    assert restore_time_qualifiers("明天下午四点开会，周三三点半再提醒", "三点半") \
        == ("三点半", "")
    assert restore_time_qualifiers("明天下午四点开会，15号三点半再提醒", "三点半") \
        == ("三点半", "")


def test_value_absent_from_raw_is_untouched():
    """planner 把「三点半」改写成「15:30」时够不着原话 —— 诚实不动。"""
    assert restore_time_qualifiers(I008, "15:30") == ("15:30", "")


def test_ambiguous_multiple_occurrences_are_untouched():
    raw = "三点半提醒我吃药，下午三点半也提醒一次"
    assert restore_time_qualifiers(raw, "三点半") == ("三点半", "")


def test_no_earlier_phrase_means_no_backfill():
    assert restore_time_qualifiers("三点半提醒我开会", "三点半") == ("三点半", "")


def test_non_clock_values_are_never_touched():
    for v in ("开会", "深圳欢乐海岸", "少冰", "", "尽快"):
        assert restore_time_qualifiers(I008, v) == (v, "")


def test_empty_raw_is_safe():
    assert restore_time_qualifiers("", "三点半") == ("三点半", "")


# ── 批量入口 ──────────────────────────────────────────────────────────
def test_slots_entry_reports_only_what_changed():
    slots = {"title": "开会", "time_text": "三点半"}
    changed = restore_dropped_qualifiers(I008, slots)
    assert set(changed) == {"time_text"}
    assert changed["time_text"][0] == "明天下午三点半"


def test_skip_protects_server_resolved_slots():
    """服务端权威解析出来的值不许被原话覆盖——那会取消 provenance。"""
    slots = {"time_text": "三点半"}
    assert restore_dropped_qualifiers(I008, slots, skip={"time_text"}) == {}


def test_non_string_slot_values_are_ignored():
    assert restore_dropped_qualifiers(I008, {"n": 3, "ok": True}) == {}
