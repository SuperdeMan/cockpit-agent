"""「到我老婆生日那天早上八点提醒我订花」：那天是哪天在记忆里（2026-10-13，docs/design/2026-10-13-v2-200x5-followups.md §12）。

用户明说「记住…」时存下的生日 / 纪念日（`memory/extract.py`，person.birthday / person.anniversary）由提醒按同一份月日判据
（`runtime.cntime.month_day_of`）读回来定日子；记着不止一个、没记着、说的是那天前后的另一天，都照旧追问，不猜。
续接轮只答了时刻时，日子是被追问的那句原话里说的那天（`STEP_ORIGIN_META`）。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.reminder.src.agent import ReminderAgent
from agents.reminder.src.store import ReminderStore
from agents.reminder.src.timeparse import FAIL, ParsedTime
from runtime.slots import STEP_ORIGIN_META

_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 7, 11, 10, 0, tzinfo=_TZ).astimezone(timezone.utc)  # 周六 10:00

_WIFE = {"predicate": "person.birthday", "subject": "老婆", "text": "用户老婆的生日是5月20日"}
_SELF = {"predicate": "person.birthday", "subject": "", "text": "用户的生日是3月8日"}


async def _agent() -> ReminderAgent:
    a = ReminderAgent()
    a.store = ReminderStore(dsn="")
    await a.store.init()
    a._llm_time_fallback = AsyncMock(return_value=ParsedTime(FAIL))
    a._now_utc = lambda: _NOW
    return a


def _ctx(*items):
    """假记忆：按谓词前缀与 subject 过滤（subject 的称谓归一在 Memory 那边，这里只认原样）。"""
    ctx = make_context()

    async def recall(user_id, query="", *, predicate_prefix="", subject="", **kw):
        return [dict(it) for it in items
                if it["predicate"].startswith(predicate_prefix) and (not subject or it["subject"] == subject)]

    ctx._memory.recall.side_effect = recall
    return ctx


def _at(month, day, hour, year=2027):
    return int(datetime(year, month, day, hour, 0, tzinfo=_TZ).timestamp())


async def _times(a):
    times, _ = await a.store.list_split("u1")
    return [(r.title, r.fire_at) for r in times]


@pytest.mark.asyncio
@pytest.mark.parametrize("time_text", ["我老婆生日那天早上八点", "老婆生日那天早上八点", ""])
async def test_the_day_is_the_remembered_birthday(time_text):
    """核心旅程 E07：上一句记住了「我老婆的生日是五月二十号」，这一句按那天建定时提醒，答句说出日子的出处。"""
    a = await _agent()
    ctx = _ctx(_WIFE)
    res = await run_handle(a, "reminder.create", raw_text="到我老婆生日那天早上八点提醒我订花",
                           slots={"title": "订花", "time_text": time_text}, ctx=ctx)
    assert res.status == "ok"
    assert "按记住的" in res.speech and "老婆生日" in res.speech         # 叫法照用户 / 规划器的原话
    assert "5月20日" in res.speech and "订花" in res.speech
    assert await _times(a) == [("订花", _at(5, 20, 8))]
    assert ctx._memory.recall.await_args.kwargs["predicate_prefix"] == "person."
    assert ctx._memory.recall.await_args.kwargs["subject"] == "老婆"


@pytest.mark.asyncio
async def test_a_day_not_remembered_is_asked_and_says_why():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="到我老婆生日那天早上八点提醒我订花",
                           slots={"title": "订花", "time_text": "我老婆生日那天早上八点"}, ctx=_ctx())
    assert res.status == "need_slot" and "我还不知道你老婆生日是哪天" in res.speech
    assert await _times(a) == []


@pytest.mark.asyncio
async def test_the_remembered_day_without_a_clock_asks_for_the_hour_and_keeps_the_day():
    """「老婆生日那天提醒我订花」只说了日子：问那天几点；下一句只答「早上八点」，日子还是那天（不是明天早上八点）。"""
    a = await _agent()
    ctx = _ctx(_WIFE)
    first = await run_handle(a, "reminder.create", raw_text="我老婆生日那天提醒我订花",
                             slots={"title": "订花"}, ctx=ctx)
    assert first.status == "need_slot" and "你老婆生日是5月20日，那天几点提醒你" in first.speech
    res = await run_handle(a, "reminder.create", raw_text="早上八点",
                           slots={"title": "订花", "time_text": "早上八点"}, ctx=ctx,
                           meta={STEP_ORIGIN_META: "我老婆生日那天提醒我订花"})
    assert res.status == "ok" and await _times(a) == [("订花", _at(5, 20, 8))]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,expected", [
    ("十点", _at(7, 12, 10, year=2026)),          # 被追问的那句说了明天：明天十点（此前单独解析成今天 / 明天早上的十点）
    ("后天十点", _at(7, 13, 10, year=2026)),      # 回答自己说了哪天，以回答为准
    ("下周一十点", _at(7, 13, 10, year=2026)),    # 同上（拼起来时「明天」会先被认出——日词先于星期）
    ("半小时后", int(_NOW.timestamp()) + 1800),   # 相对时间不拼日子
])
async def test_a_follow_up_with_only_a_clock_keeps_the_day_said_before(answer, expected):
    a = await _agent()
    first = await run_handle(a, "reminder.create", raw_text="明天提醒我开会", ctx=_ctx())
    assert first.status == "need_slot"
    res = await run_handle(a, "reminder.create", raw_text=answer, slots={"title": "开会", "time_text": answer},
                           ctx=_ctx(), meta={STEP_ORIGIN_META: "明天提醒我开会"})
    assert res.status == "ok" and await _times(a) == [("开会", expected)]


@pytest.mark.asyncio
async def test_my_birthday_is_never_read_off_a_family_members_entry():
    """本人的生日只认本人的条目：老婆的、以及旧数据里漏填 subject 的「用户老婆的生日」都不算。"""
    a = await _agent()
    legacy = {"predicate": "person.birthday", "subject": "", "text": "用户老婆的生日是5月21日"}
    res = await run_handle(a, "reminder.create", raw_text="我生日那天早上提醒我订蛋糕",
                           slots={"title": "订蛋糕"}, ctx=_ctx(_WIFE, _SELF, legacy))
    assert res.status == "ok" and "按记住的你生日" in res.speech
    assert await _times(a) == [("订蛋糕", _at(3, 8, 8))]


@pytest.mark.asyncio
async def test_two_remembered_anniversaries_are_not_guessed_between():
    a = await _agent()
    ctx = _ctx({"predicate": "person.anniversary", "subject": "", "text": "用户的结婚纪念日是10月1日"},
               {"predicate": "person.anniversary", "subject": "", "text": "用户的恋爱纪念日是2月14日"})
    res = await run_handle(a, "reminder.create", raw_text="纪念日那天早上八点提醒我订餐厅",
                           slots={"title": "订餐厅"}, ctx=ctx)
    assert res.status == "need_slot" and "我还不知道纪念日是哪天" in res.speech
    assert await _times(a) == []


@pytest.mark.asyncio
async def test_our_wedding_anniversary_is_the_users_own():
    a = await _agent()
    ctx = _ctx({"predicate": "person.anniversary", "subject": "", "text": "用户和老婆的结婚纪念日是10月1日"},
               {"predicate": "person.anniversary", "subject": "妈妈", "text": "用户妈妈的结婚纪念日是6月6日"})
    res = await run_handle(a, "reminder.create", raw_text="我们结婚纪念日那天晚上七点提醒我订餐厅",
                           slots={"title": "订餐厅"}, ctx=ctx)
    assert res.status == "ok" and "按记住的你们结婚纪念日" in res.speech
    assert await _times(a) == [("订餐厅", _at(10, 1, 19, year=2026))]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    "我老婆生日前一天早上八点提醒我订花",      # 那天前后的另一天：换算另做，不按那天排
    "我老婆生日那天之前提醒我订花",
    "到我老婆生日蛋糕送到的时候提醒我",        # 不是一个日子
])
async def test_a_day_relative_to_the_remembered_one_is_not_scheduled_on_it(raw):
    a = await _agent()
    ctx = _ctx(_WIFE)
    res = await run_handle(a, "reminder.create", raw_text=raw, slots={"title": "订花"}, ctx=ctx)
    assert res.status == "need_slot" and await _times(a) == []
    ctx._memory.recall.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_birthday_that_is_the_errand_not_the_day_stays_in_the_title():
    """「提醒我给老婆过生日」里的生日是要办的事，不是日子：标题照留，时间按原话的明天早上八点，不去查记忆。"""
    a = await _agent()
    ctx = _ctx(_WIFE)
    res = await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我给老婆过生日", ctx=ctx)
    assert res.status == "ok" and await _times(a) == [("给老婆过生日", _at(7, 12, 8, year=2026))]
    ctx._memory.recall.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lunar_birthday_is_not_read_as_a_solar_date():
    a = await _agent()
    ctx = _ctx({"predicate": "person.birthday", "subject": "妈妈", "text": "用户妈妈的生日是农历八月十五"})
    res = await run_handle(a, "reminder.create", raw_text="我妈妈生日那天早上八点提醒我买蛋糕",
                           slots={"title": "买蛋糕"}, ctx=ctx)
    assert res.status == "need_slot" and await _times(a) == []
