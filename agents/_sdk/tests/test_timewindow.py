"""共享时刻/时间窗解析单测（E1）：时刻消歧、事件时刻、用餐窗反推。

时刻消歧那几条与 navigation 的 `test_parse_arrive_by_rules` 是同一份实现
（E1 下沉后 `_parse_arrive_by = parse_clock_time`），两处都留着是刻意的：
一处证「共享实现对」，一处证「导航侧对外行为没变」。
"""
import time

from agents._sdk.timewindow import (
    DINING_BUFFER_MIN, DINING_DWELL_MIN, clock_minutes, dining_window,
    fmt_clock, parse_clock_time, parse_event_time)

# 2026-08-14 周五 14:00（与 navigation 既有用例同一基准）
_NOW = int(time.mktime((2026, 8, 14, 14, 0, 0, 0, 0, -1)))


def test_clock_time_disambiguates_bare_hours():
    assert time.localtime(parse_clock_time("5点", now_ts=_NOW))[:5] == (2026, 8, 14, 17, 0)
    late = int(time.mktime((2026, 8, 14, 20, 0, 0, 0, 0, -1)))
    assert time.localtime(parse_clock_time("5点", now_ts=late))[:5] == (2026, 8, 15, 5, 0)
    assert time.localtime(parse_clock_time("下午5点半", now_ts=_NOW))[:5] == (2026, 8, 14, 17, 30)
    assert time.localtime(parse_clock_time("17:00", now_ts=_NOW))[:5] == (2026, 8, 14, 17, 0)
    assert time.localtime(parse_clock_time("23点", now_ts=_NOW))[:5] == (2026, 8, 14, 23, 0)
    assert parse_clock_time("尽快", now_ts=_NOW) is None


def test_event_time_needs_both_a_clock_and_an_event_word():
    for raw, word in (("晚上7点的电影，先找个地方吃饭", "电影"),
                      ("7点半那场话剧", "话剧"),
                      ("电影是晚上七点的", "电影"),
                      ("下午3点的高铁，先吃点东西", "高铁")):
        got = parse_event_time(raw, now_ts=_NOW)
        assert got is not None and got[1] == word, raw


def test_arrive_by_phrasing_is_not_read_as_an_event():
    """「5点前到」是到达时限（navigation 的 arrive_by），不是事件时刻——
    两条链不许抢同一句，否则同一个数字会被两处各解释一遍。"""
    for raw in ("五点前到学校", "17:00 我必须到公司", "帮我6点前赶到"):
        assert parse_event_time(raw, now_ts=_NOW) is None, raw


def test_event_word_too_far_from_the_clock_is_not_paired():
    """时刻与事件词隔了大半句 → 不成对（避免把无关的两个词凑成约束）。"""
    raw = "7点提醒我出门买菜顺便看看有没有便宜的西红柿然后晚上再说电影的事"
    assert parse_event_time(raw, now_ts=_NOW) is None


def test_dining_window_is_derived_backwards_from_the_event():
    ev = parse_event_time("晚上7点的电影，先吃个饭", now_ts=_NOW)
    w = dining_window(ev[0], now_ts=_NOW)
    assert fmt_clock(w["event_ts"]) == "19:00"
    assert fmt_clock(w["leave_ts"]) == "18:30"        # 事件 − 路上预留
    assert fmt_clock(w["seat_ts"]) == "17:30"         # 离席 − 用餐时长
    assert w["dwell_min"] == DINING_DWELL_MIN and w["buffer_min"] == DINING_BUFFER_MIN
    assert w["tight"] is False


def test_dining_window_flags_tight_instead_of_squeezing():
    """来不及就是来不及——不压缩窗口凑出一个能满足的数（编数据比说不行更糟）。"""
    ev = parse_event_time("晚上7点的电影，先吃个饭",
                          now_ts=int(time.mktime((2026, 8, 14, 18, 0, 0, 0, 0, -1))))
    w = dining_window(ev[0], now_ts=int(time.mktime((2026, 8, 14, 18, 0, 0, 0, 0, -1))))
    assert w["tight"] is True


def test_window_params_are_injectable():
    w = dining_window(_NOW + 7200, dwell_min=90, buffer_min=45, now_ts=_NOW)
    assert (w["event_ts"] - w["leave_ts"]) == 45 * 60
    assert (w["leave_ts"] - w["seat_ts"]) == 90 * 60


def test_clock_minutes_matches_local_wall_clock():
    ts = int(time.mktime((2026, 8, 14, 17, 30, 0, 0, 0, -1)))
    assert clock_minutes(ts) == 17 * 60 + 30
    assert fmt_clock(ts) == "17:30"
