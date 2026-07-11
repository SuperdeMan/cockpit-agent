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


# ── P1a：snooze 收编尸体 / update 两轮 / 重复规则 / 列表范围 ──

async def _fire(a, hours_ahead: int = 7):
    """把已建条目强制到点（fired），模拟触达后场景。"""
    return await a.store.claim_due(int(_NOW.timestamp()) + hours_ahead * 3600)


@pytest.mark.asyncio
async def test_snooze_button_reschedules_fired_no_zombie():
    """「稍后10分钟」按钮（send_text=10分钟后再提醒我X）改期原条目——根治 fired 尸体堆积。"""
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="今天下午三点提醒我给客户回电话")
    assert len(await _fire(a)) == 1
    res = await run_handle(a, "reminder.create", raw_text="10分钟后再提醒我给客户回电话")
    assert res.status == "ok" and "再提醒你" in res.speech
    assert res.ui_card["context"] == "updated"
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].status == "pending"     # 同一条改期，无第二条


@pytest.mark.asyncio
async def test_snooze_without_title_targets_latest_fired():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="今天下午三点提醒我给客户回电话")
    await _fire(a)
    res = await run_handle(a, "reminder.create", raw_text="过10分钟再叫我")
    assert res.status == "ok" and "给客户回电话" in res.speech
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].status == "pending"


@pytest.mark.asyncio
async def test_update_by_title_direct():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    res = await run_handle(a, "reminder.update", raw_text="把带充电线改到明天九点")
    assert res.status == "ok" and "改到" in res.speech and "09:00" in res.speech
    assert res.ui_card["context"] == "updated"
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1                                       # 改期不是新建


@pytest.mark.asyncio
async def test_update_two_turn_via_pending_action():
    """「改个时间」缺新时间 → NEED_SLOT 存 action=update → 下一轮裸时间续接改原条目。"""
    a = await _agent()
    created = await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    rid = created.ui_card["item"]["id"]
    ctx = make_context()
    res = await run_handle(a, "reminder.update",
                           raw_text="把带充电线的提醒改个时间", ctx=ctx)
    assert res.status == "need_slot" and "改到什么时候" in res.speech
    pend_json = ctx._memory.upsert_profile.call_args.args[2]
    assert json.loads(pend_json) == {"title": "带充电线", "action": "update", "id": rid}
    ctx2 = make_context(context_values={"profile.reminder_pending": pend_json})
    res2 = await run_handle(a, "reminder.create", raw_text="晚上八点", ctx=ctx2)
    assert res2.status == "ok" and "改到" in res2.speech
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].id == rid                # 还是原条目


@pytest.mark.asyncio
async def test_update_multi_match_clarifies():
    a = await _agent()
    ctx = make_context()
    await run_handle(a, "reminder.create", raw_text="今天下午三点提醒我喝水", ctx=ctx)
    await run_handle(a, "reminder.create", raw_text="今天下午五点提醒我喝水", ctx=ctx)
    res = await run_handle(a, "reminder.update", slots={"title": "喝水"},
                           raw_text="把喝水改到晚上八点", ctx=ctx)
    assert res.status == "need_slot" and "改第几条" in res.speech


@pytest.mark.asyncio
async def test_create_recurring_daily():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="每天早上八点提醒我吃药")
    assert res.status == "ok" and "每天" in res.speech and "首次" in res.speech
    times, _ = await a.store.list_split("u1")
    assert times[0].recur == "daily" and times[0].title == "吃药"
    assert res.ui_card["item"]["recur_label"] == "每天"


@pytest.mark.asyncio
async def test_create_recurring_workday_aligns_weekend():
    a = await _agent()   # 固定时钟 = 周六：9:30 已过 → 周日 → 工作日对齐到周一
    res = await run_handle(a, "reminder.create",
                           raw_text="每个工作日早上九点半提醒我开晨会")
    assert res.status == "ok"
    times, _ = await a.store.list_split("u1")
    lt = datetime.fromtimestamp(times[0].fire_at, _TZ)
    assert lt.weekday() == 0 and (lt.hour, lt.minute) == (9, 30)
    assert times[0].recur == "workday"


@pytest.mark.asyncio
async def test_complete_recurring_keeps_series():
    """重复系列「完成」只确认本次不杀系列；「取消」才结束系列。"""
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="每天早上八点提醒我吃药")
    res = await run_handle(a, "reminder.complete", raw_text="完成提醒：吃药")
    assert "下次" in res.speech and "取消" in res.speech
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].status == "pending"     # 系列还在
    res2 = await run_handle(a, "reminder.cancel", raw_text="取消吃药的提醒")
    assert res2.status == "ok"
    times, _ = await a.store.list_split("u1")
    assert times == []                                           # 系列级取消


@pytest.mark.asyncio
async def test_list_dahoutian_not_shadowed():
    """B1 回归：「大后天」不被"后天"分支截胡错一天。"""
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="大后天晚上八点提醒我洗车")
    res = await run_handle(a, "reminder.list", raw_text="大后天有什么安排")
    assert res.ui_card["date_label"].startswith("大后天")
    assert res.ui_card["items"][0]["title"] == "洗车"


@pytest.mark.asyncio
async def test_list_next_month_range():
    a = await _agent()
    fire = int(datetime(2026, 8, 5, 8, 0, tzinfo=_TZ).timestamp())
    await a.store.add(Reminder(user_id="u1", title="续保险", kind="time", fire_at=fire))
    res = await run_handle(a, "reminder.list", raw_text="下个月有什么安排")
    assert res.ui_card["date_label"] == "下个月 · 8月"
    assert [i["title"] for i in res.ui_card["items"]] == ["续保险"]


# ── P1c 跨域提醒：REMINDABLE_ACTIVE 消费（设计 2026-07-11-reminder-cross-domain）──

def _remindable(items):
    return {"profile.remindable_active": json.dumps(
        {"source": "info.sports", "label": "FIFA 世界杯 · 明天", "ts": 1,
         "items": items}, ensure_ascii=False)}


def _ts(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=_TZ).timestamp())


@pytest.mark.asyncio
async def test_cross_domain_ordinal_creates_at_kickoff_minus_lead():
    """trace b3ecd195 复现：「第一场提醒我观看」→ 开赛时刻-10分钟一轮成单。"""
    a = await _agent()
    k1 = _ts(2026, 7, 12, 3, 0)
    ctx = make_context(context_values=_remindable(
        [{"title": "葡萄牙 vs 西班牙", "fire_at": k1},
         {"title": "巴西 vs 阿根廷", "fire_at": _ts(2026, 7, 12, 19, 0)}]))
    res = await run_handle(a, "reminder.create", raw_text="第一场提醒我观看", ctx=ctx)
    assert res.status == "ok"
    assert "开始" in res.speech and "提前 10 分钟" in res.speech and "03:00" in res.speech
    times, _ = await a.store.list_split("u1")
    assert times[0].fire_at == k1 - 600
    assert times[0].title == "观看葡萄牙 vs 西班牙"


@pytest.mark.asyncio
async def test_cross_domain_lead_override():
    a = await _agent()
    k = _ts(2026, 7, 12, 19, 0)
    ctx = make_context(context_values=_remindable([{"title": "巴西 vs 阿根廷", "fire_at": k}]))
    res = await run_handle(a, "reminder.create",
                           raw_text="第一场开赛前半小时提醒我", ctx=ctx)
    assert res.status == "ok" and "提前 30 分钟" in res.speech
    times, _ = await a.store.list_split("u1")
    assert times[0].fire_at == k - 1800


@pytest.mark.asyncio
async def test_cross_domain_reference_single_direct():
    a = await _agent()
    k = _ts(2026, 7, 12, 3, 0)
    ctx = make_context(context_values=_remindable([{"title": "葡萄牙 vs 西班牙", "fire_at": k}]))
    res = await run_handle(a, "reminder.create", raw_text="开赛的时候提醒我", ctx=ctx)
    assert res.status == "ok" and "葡萄牙 vs 西班牙" in res.speech
    times, _ = await a.store.list_split("u1")
    assert times[0].fire_at == k - 600


@pytest.mark.asyncio
async def test_cross_domain_reference_multi_asks():
    a = await _agent()
    ctx = make_context(context_values=_remindable(
        [{"title": "A vs B", "fire_at": _ts(2026, 7, 12, 3, 0)},
         {"title": "C vs D", "fire_at": _ts(2026, 7, 12, 19, 0)}]))
    res = await run_handle(a, "reminder.create", raw_text="到时候提醒我看球", ctx=ctx)
    assert res.status == "need_slot" and "第几场" in res.speech
    times, _ = await a.store.list_split("u1")
    assert times == []                                   # 反问阶段不落单


@pytest.mark.asyncio
async def test_cross_domain_started_honest():
    a = await _agent()   # 固定时钟 7/11 10:00：7/11 02:00 已开赛
    ctx = make_context(context_values=_remindable(
        [{"title": "A vs B", "fire_at": _ts(2026, 7, 11, 2, 0)}]))
    res = await run_handle(a, "reminder.create", raw_text="第一场提醒我观看", ctx=ctx)
    assert res.status == "ok" and "已经开始" in res.speech
    times, _ = await a.store.list_split("u1")
    assert times == []


@pytest.mark.asyncio
async def test_cross_domain_pending_continuation_keeps_title():
    """trace 后续轮：「什么时候提醒你？」→「开赛的时候」——pending 标题 + 跨域时间。"""
    a = await _agent()
    k = _ts(2026, 7, 12, 3, 0)
    ctx = make_context(context_values={
        **_remindable([{"title": "葡萄牙 vs 西班牙", "fire_at": k}]),
        "profile.reminder_pending": json.dumps({"title": "观看世界杯第一场比赛"},
                                               ensure_ascii=False)})
    res = await run_handle(a, "reminder.create", raw_text="开赛的时候", ctx=ctx)
    assert res.status == "ok"
    times, _ = await a.store.list_split("u1")
    assert times[0].title == "观看世界杯第一场比赛" and times[0].fire_at == k - 600


@pytest.mark.asyncio
async def test_cross_domain_explicit_time_wins():
    a = await _agent()
    ctx = make_context(context_values=_remindable(
        [{"title": "A vs B", "fire_at": _ts(2026, 7, 12, 3, 0)}]))
    res = await run_handle(a, "reminder.create",
                           raw_text="第一场明天八点提醒我看回放", ctx=ctx)
    assert res.status == "ok"
    times, _ = await a.store.list_split("u1")
    assert times[0].fire_at == _ts(2026, 7, 12, 8, 0)    # 原话显式时间优先于跨域推导


@pytest.mark.asyncio
async def test_cross_domain_absent_zero_regression():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="第一场提醒我观看")
    assert res.status == "need_slot" and "什么时候" in res.speech   # 无 remindable：现状追问


@pytest.mark.asyncio
async def test_ordinal_continuation_after_clarify():
    """B2 补测：澄清后「取消第二条」经 REMINDERS_ACTIVE 精确选中，不误删第一条。"""
    a = await _agent()
    r1 = await a.store.add(Reminder(user_id="u1", title="喝水", kind="time", fire_at=10 ** 12))
    r2 = await a.store.add(Reminder(user_id="u1", title="喝水", kind="time", fire_at=10 ** 12 + 1))
    ctx = make_context(context_values={"profile.reminders_active": json.dumps(
        {"items": [{"id": r1.id, "title": "喝水"}, {"id": r2.id, "title": "喝水"}]},
        ensure_ascii=False)})
    res = await run_handle(a, "reminder.cancel", raw_text="取消第二条", ctx=ctx)
    assert res.status == "ok"
    assert (await a.store.get("u1", r2.id)).status == "cancelled"
    assert (await a.store.get("u1", r1.id)).status == "pending"
