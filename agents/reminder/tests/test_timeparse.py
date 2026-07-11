"""timeparse 黄金用例：测试即规格。now 固定 2026-07-11(周六) 10:00 +8。"""
from datetime import datetime, timedelta, timezone

import pytest

from agents.reminder.src.timeparse import (
    OK, NEED_TIME, FAIL, parse_time_text, strip_time_expressions, format_display)

TZ = timezone(timedelta(hours=8), name="UTC+8")
NOW_LOCAL = datetime(2026, 7, 11, 10, 0, tzinfo=TZ)   # 周六
NOW = NOW_LOCAL.astimezone(timezone.utc)


def L(*a):
    return datetime(*a, tzinfo=TZ)


def ts(dt):
    return int(dt.timestamp())


def P(text):
    return parse_time_text(text, now=NOW, tz=TZ)


# ── 相对时间 ──
@pytest.mark.parametrize("text,delta", [
    ("20秒后提醒我测试", timedelta(seconds=20)),
    ("5分钟后提醒我", timedelta(minutes=5)),
    ("十分钟后叫我", timedelta(minutes=10)),
    ("半小时后提醒我给客户回电话", timedelta(minutes=30)),
    ("一个半小时后提醒我", timedelta(minutes=90)),
    ("两小时后提醒我", timedelta(hours=2)),
    ("1个小时之后提醒我", timedelta(hours=1)),
    ("45分钟以后叫我", timedelta(minutes=45)),
])
def test_relative(text, delta):
    r = P(text)
    assert r.status == OK
    assert r.fire_at == ts(NOW + delta)


# ── 绝对：日 + 段位 + 时刻 ──
@pytest.mark.parametrize("text,local", [
    ("明天早上八点提醒我带充电线", L(2026, 7, 12, 8, 0)),
    ("明天早上8点", L(2026, 7, 12, 8, 0)),
    ("明早八点叫我", L(2026, 7, 12, 8, 0)),
    ("明天下午三点半提醒我", L(2026, 7, 12, 15, 30)),
    ("明天 9:15 提醒我开会", L(2026, 7, 12, 9, 15)),
    ("明晚八点提醒我", L(2026, 7, 12, 20, 0)),
    ("后天中午提醒我吃药", L(2026, 7, 13, 12, 0)),
    ("后天中午一点提醒我", L(2026, 7, 13, 13, 0)),
    ("大后天晚上八点提醒我", L(2026, 7, 14, 20, 0)),
    ("今晚八点提醒我", L(2026, 7, 11, 20, 0)),
    ("今天下午四点提醒我取快递", L(2026, 7, 11, 16, 0)),
    ("晚上八点提醒我", L(2026, 7, 11, 20, 0)),        # 无日词：今天晚上
    ("下午三点提醒我", L(2026, 7, 11, 15, 0)),
    ("15:30提醒我", L(2026, 7, 11, 15, 30)),
    ("中午提醒我吃饭", L(2026, 7, 11, 12, 0)),          # 中午默认 12:00
    ("凌晨两点提醒我", L(2026, 7, 12, 2, 0)),           # 今天 02:00 已过 → 明天
    ("晚上12点提醒我", L(2026, 7, 12, 0, 0)),           # 晚上12点 = 次日 00:00
])
def test_absolute(text, local):
    r = P(text)
    assert r.status == OK, f"{text} -> {r.status}"
    assert r.fire_at == ts(local), f"{text}: got {r.fire_at}, want {ts(local)}"


# ── 裸 12 小时制：{h, h+12} 取最近未来 ──
@pytest.mark.parametrize("text,local", [
    ("八点提醒我", L(2026, 7, 11, 20, 0)),      # 08:00 已过 → 今天 20:00
    ("两点半提醒我", L(2026, 7, 11, 14, 30)),   # 02:30 已过 → 今天 14:30
    ("十一点提醒我", L(2026, 7, 11, 11, 0)),    # 11:00 未过 → 今天 11:00
    ("3点一刻提醒我", L(2026, 7, 11, 15, 15)),
])
def test_bare_clock_future_nearest(text, local):
    r = P(text)
    assert r.status == OK
    assert r.fire_at == ts(local)


# ── 周 / 日期 ──
@pytest.mark.parametrize("text,local", [
    ("周五下午三点提醒我", L(2026, 7, 17, 15, 0)),      # 本周五已过 → 下周五
    ("周日晚上七点提醒我", L(2026, 7, 12, 19, 0)),      # 本周日=明天
    ("下周三上午十点提醒我", L(2026, 7, 15, 10, 0)),
    ("7月20号早上九点提醒我", L(2026, 7, 20, 9, 0)),
    ("8月1日下午两点提醒我", L(2026, 8, 1, 14, 0)),
    ("3月5号早上八点提醒我", L(2027, 3, 5, 8, 0)),      # 今年已过 → 明年
    ("25号下午四点提醒我", L(2026, 7, 25, 16, 0)),      # 本月 25 未过
    ("5号早上八点提醒我", L(2026, 8, 5, 8, 0)),         # 本月 5 号已过 → 下月
])
def test_week_and_date(text, local):
    r = P(text)
    assert r.status == OK
    assert r.fire_at == ts(local)


# ── need_time / fail / 显式过去 ──
def test_day_without_clock_needs_time():
    for t in ("明天提醒我开会", "周三提醒我", "明天早上提醒我", "下午提醒我"):
        assert P(t).status == NEED_TIME, t


def test_unparseable_fails():
    for t in ("饭点提醒我", "到公司提醒我拿文件", ""):
        assert P(t).status == FAIL, t


def test_explicit_today_past_kept_as_is():
    r = P("今天九点提醒我")   # now 10:00，九点已过；显式"今天"不偷偷改天
    assert r.status == OK
    assert r.fire_at == ts(L(2026, 7, 11, 9, 0))
    assert r.fire_at < ts(NOW_LOCAL)


# ── display / strip ──
def test_display_readable():
    assert "明天" in P("明天早上八点提醒我").display
    assert "08:00" in P("明天早上八点提醒我").display
    assert "今天" in P("下午三点提醒我").display


def test_format_display_roundtrip():
    fire = ts(L(2026, 7, 12, 8, 0))
    assert format_display(fire, now=NOW, tz=TZ) == "明天 08:00"


def test_strip_time_expressions():
    assert strip_time_expressions("明天早上八点提醒我带充电线") == "提醒我带充电线"
    assert strip_time_expressions("半小时后提醒我给客户回电话") == "提醒我给客户回电话"
    assert strip_time_expressions("提醒我周五下午三点去接孩子") == "提醒我去接孩子"
