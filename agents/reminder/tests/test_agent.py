"""ReminderAgent 契约测试：不起 gRPC，直驱 handle（agents/_sdk/testing.py 夹具）。"""
import json
from unittest.mock import AsyncMock

import pytest

from datetime import datetime, timedelta, timezone

from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
from agents.reminder.src.agent import ReminderAgent
from agents.reminder.src.store import Reminder, ReminderStore
from agents.reminder.src.timeparse import FAIL, ParsedTime

_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 7, 11, 10, 0, tzinfo=_TZ).astimezone(timezone.utc)  # 周六 10:00


async def _agent() -> ReminderAgent:
    a = ReminderAgent()
    a.store = ReminderStore(dsn="")          # 每例独立内存 store
    await a.store.init()
    a._llm_time_fallback = AsyncMock(return_value=ParsedTime(FAIL))  # 默认 LLM 兜底失败
    a._now_utc = lambda: _NOW                # 固定时钟：用例不随跑测时刻漂移
    return a


def test_manifest():
    assert assert_manifest_consistent(ReminderAgent()) is True


@pytest.mark.asyncio
async def test_create_absolute_time():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    assert res.status == "ok"
    assert "明天" in res.speech and "08:00" in res.speech and "带充电线" in res.speech
    assert res.ui_card["type"] == "reminder_card" and res.ui_card["context"] == "created"
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].title == "带充电线"


@pytest.mark.asyncio
async def test_create_relative_time():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="半小时后提醒我给客户回电话")
    assert res.status == "ok" and "给客户回电话" in res.speech


@pytest.mark.asyncio
async def test_create_without_time_asks_and_saves_pending():
    a = await _agent()
    ctx = make_context()
    res = await run_handle(a, "reminder.create", raw_text="提醒我开会", ctx=ctx)
    assert res.status == "need_slot" and "time_text" in res.missing_slots
    assert "什么时候" in res.speech
    # NEED_SLOT 时把标题存进 REMINDER_PENDING（经 profile KV）
    args = ctx._memory.upsert_profile.call_args
    assert args.args[1] == "reminder_pending" and "开会" in args.args[2]


@pytest.mark.asyncio
async def test_create_resumes_pending_title():
    a = await _agent()
    ctx = make_context(context_values={
        "profile.reminder_pending": json.dumps({"title": "买牛奶"}, ensure_ascii=False)})
    res = await run_handle(a, "reminder.create", raw_text="晚上八点", ctx=ctx)
    assert res.status == "ok" and "买牛奶" in res.speech


@pytest.mark.asyncio
async def test_create_todo_without_time():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="记一下要买牛奶")
    assert res.status == "ok" and "买牛奶" in res.speech
    _, todos = await a.store.list_split("u1")
    assert len(todos) == 1 and todos[0].kind == "todo"


@pytest.mark.asyncio
async def test_list_today_writes_active_and_card():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="今晚八点提醒我取快递")
    ctx = make_context()
    res = await run_handle(a, "reminder.list", raw_text="我今天有什么安排", ctx=ctx)
    assert res.status == "ok"
    card = res.ui_card
    assert card["type"] == "reminder_list" and card["view"] == "day"
    assert card["items"][0]["title"] == "取快递"
    keys = [c.args[1] for c in ctx._memory.upsert_profile.call_args_list]
    assert "reminders_active" in keys


@pytest.mark.asyncio
async def test_list_week_is_multi_view():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    res = await run_handle(a, "reminder.list", raw_text="这周有什么安排")
    assert res.ui_card["view"] == "multi"


@pytest.mark.asyncio
async def test_list_empty_honest():
    a = await _agent()
    res = await run_handle(a, "reminder.list", raw_text="我今天有什么安排")
    assert res.status == "ok" and "没有" in res.speech and res.ui_card is None


@pytest.mark.asyncio
async def test_complete_by_title():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    res = await run_handle(a, "reminder.complete", raw_text="带充电线那条办完了")
    assert res.status == "ok" and "带充电线" in res.speech
    times, _ = await a.store.list_split("u1", statuses=("done",))
    assert len(times) == 1


@pytest.mark.asyncio
async def test_complete_by_ordinal_via_active_state():
    a = await _agent()
    r = await a.store.add(Reminder(user_id="u1", title="回电话", kind="time",
                                   fire_at=10 ** 12))
    ctx = make_context(context_values={"profile.reminders_active": json.dumps(
        {"items": [{"id": r.id, "title": "回电话"}]}, ensure_ascii=False)})
    res = await run_handle(a, "reminder.complete", raw_text="完成第一条", ctx=ctx)
    assert res.status == "ok" and "回电话" in res.speech


@pytest.mark.asyncio
async def test_cancel_single_and_not_found():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    res = await run_handle(a, "reminder.cancel", raw_text="不用提醒我带充电线了")
    assert res.status == "ok" and "取消" in res.speech
    res2 = await run_handle(a, "reminder.cancel", raw_text="取消买牛奶那条")
    assert res2.status == "failed" and "没找到" in res2.speech


@pytest.mark.asyncio
async def test_cancel_multi_match_clarifies_and_deletes_nothing():
    """方案乙回归：同名多条命中时不擅自删（旧实现 hits[0] 会静默少删），
    反问澄清并写入 active，用户续接「第二条」精确删一条。"""
    a = await _agent()
    ctx = make_context()
    await run_handle(a, "reminder.create", raw_text="今天下午三点提醒我喝水", ctx=ctx)
    await run_handle(a, "reminder.create", raw_text="今天下午五点提醒我喝水", ctx=ctx)
    res = await run_handle(a, "reminder.cancel", slots={"title": "喝水"},
                           raw_text="把喝水那条删了", ctx=ctx)
    assert res.status == "need_slot" and "2 条" in res.speech and "哪条" in res.speech
    times, _ = await a.store.list_split("u1")
    assert len(times) == 2                     # 澄清阶段一条都没删（旧实现会静默删掉第一条）
    assert res.ui_card and len(res.ui_card["items"]) == 2   # 候选卡列出两条供用户选


@pytest.mark.asyncio
async def test_cancel_all_needs_confirm_then_executes():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    await run_handle(a, "reminder.create", raw_text="记一下要买牛奶")
    res = await run_handle(a, "reminder.cancel", raw_text="把提醒都清空")
    assert res.status == "need_confirm" and "2 条" in res.speech
    res2 = await run_handle(a, "reminder.cancel", raw_text="把提醒都清空",
                            meta={"confirmed": "true"})
    assert res2.status == "ok" and "清空" in res2.speech
    times, todos = await a.store.list_split("u1")
    assert times == [] and todos == []


@pytest.mark.asyncio
async def test_create_past_explicit_time_asks_again():
    a = await _agent()   # 固定时钟 10:00：今天凌晨一点必然已过
    res = await run_handle(a, "reminder.create", raw_text="今天凌晨一点提醒我看球")
    assert res.status == "need_slot" and "已经过了" in res.speech
