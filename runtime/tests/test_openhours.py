"""营业时间窗口解析（`runtime/openhours.py`，QA Q2 残余 2026-08-19）。

这份解析 2026-08-19 从 `agents/nearby/src/providers/base.py` 收敛来——云侧编排要用
同一份算「哪家最晚关门」，而它的镜像不 COPY `agents/`。所以本文件有两个职责：

1. **行为锁金标**：`is_open_now` 的每一条判定必须与收敛前逐字同判。金标是手写的
   输入→期望表，**刻意不内联一份旧实现来对跑**——那样两边同源，断言会变恒绿
   （Q13 那批的教训：收敛后 parity 断言恒绿，真正长期有效的是行为锁金标）。
2. `closing_minute` 的新语义，尤其是**未知返回 None 而不是 0**。
"""
from __future__ import annotations

import pytest

from runtime.openhours import (DAY_MINUTES, closing_minute, format_minute,
                              is_open_now, parse_ranges)


# ── is_open_now 行为锁：收敛前后必须逐字同判 ──────────────────────────────

@pytest.mark.parametrize("open_today,now_min,expect", [
    ("09:00-22:00", 10 * 60, True),
    ("09:00-22:00", 8 * 60, False),
    ("09:00-22:00", 22 * 60, True),           # 收盘那一分钟算开着（闭区间，收敛前同判）
    ("09:00-22:00", 22 * 60 + 1, False),
    ("10:00-14:00 17:00-22:00", 15 * 60, False),   # 午休段
    ("10:00-14:00 17:00-22:00", 18 * 60, True),
    ("17:00-02:00", 23 * 60, True),           # 跨零点·当日侧
    ("17:00-02:00", 1 * 60, True),            # 跨零点·凌晨侧
    ("17:00-02:00", 10 * 60, False),
    ("24小时", 3 * 60, True),
    ("全天营业", 3 * 60, True),
    ("00:00-24:00", 3 * 60, True),
    ("周一至周日 10:00-22:00", 11 * 60, True),
    ("", 11 * 60, None),                      # 空 = 未知，**不是不营业**
    ("营业中", 11 * 60, None),                # 判不出范围 = 未知
])
def test_is_open_now_behaviour_is_locked(open_today, now_min, expect):
    assert is_open_now(open_today, now_min) is expect


def test_degenerate_equal_range_matches_the_pre_convergence_branch():
    """`10:00-10:00` 在收敛前走的是 `end <= start` 那条跨零点分支（几乎全天 True）。

    收敛后由 `parse_ranges` 的同一条件产出 `end += DAY_MINUTES`——**等价迁移**。
    这条钉住它，免得有人「顺手把退化写法改成 False」而不知道那是行为变更。
    """
    assert is_open_now("10:00-10:00", 3 * 60) is True
    assert is_open_now("10:00-10:00", 23 * 60) is True


# ── closing_minute：新语义 ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("09:00-22:00", 22 * 60),
    ("10:00-14:00 17:00-23:30", 23 * 60 + 30),     # 多段取最晚那一段
    ("17:00-02:00", 26 * 60),                       # 跨零点 > 1440，可直接比大小
    ("24小时", DAY_MINUTES),
    ("周一至周日 10:00-21:00", 21 * 60),
    ("", None),
    ("旺季营业", None),
])
def test_closing_minute(raw, expect):
    assert closing_minute(raw) == expect


def test_unknown_is_none_not_zero():
    """**缺失值不许伪装成一个合法的极端值。**

    如果未知返回 0，一家「营业时间未知」的店会赢下「哪家最早关门」——
    同 `last_places_ts=0` 那条口径（0 按过期处理而不是「1970 年取回的」）。
    """
    assert closing_minute(None) is None
    assert closing_minute("") is None
    assert closing_minute("", None, "  ") is None


def test_candidates_are_tried_in_authority_order():
    """调用方按权威性排参数：今日实况优于一周概述。"""
    assert closing_minute("10:00-20:00", "周一至周日 10:00-23:00") == 20 * 60
    # 今日缺失时才回退到一周概述——这正是 `open_week` 存在的理由
    assert closing_minute("", "周一至周日 10:00-23:00") == 23 * 60


def test_cross_midnight_sorts_later_than_late_evening():
    """「营业到凌晨 2 点」必须比「营业到 23 点」更晚——这是 I-018 的判定核心。"""
    assert closing_minute("17:00-02:00") > closing_minute("10:00-23:00")


# ── parse_ranges / format_minute ─────────────────────────────────────────

def test_parse_ranges_normalises_cross_midnight():
    assert parse_ranges("17:00-02:00") == [(17 * 60, 26 * 60)]
    assert parse_ranges("10:00~14:00") == [(600, 840)]
    assert parse_ranges("10:00到14:00") == [(600, 840)]
    assert parse_ranges("没有时间") == []


@pytest.mark.parametrize("minute,expect", [
    (22 * 60, "22:00"),
    (23 * 60 + 30, "23:30"),
    (26 * 60, "次日 02:00"),
    (DAY_MINUTES, "24:00"),
])
def test_format_minute(minute, expect):
    assert format_minute(minute) == expect
