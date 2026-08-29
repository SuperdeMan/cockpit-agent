"""ReminderAgent 契约测试：不起 gRPC，直驱 handle（agents/_sdk/testing.py 夹具）。"""
import json
from unittest.mock import AsyncMock

import pytest

from datetime import datetime, timedelta, timezone

from agents._sdk.shared_state import (REMINDERS_ACTIVE, REMINDER_PENDING,
                                      owner_scoped)
from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
from agents.reminder.src.agent import ReminderAgent
from agents.reminder.src.store import CANCELLED, Reminder, ReminderStore
from agents.reminder.src.timeparse import FAIL, ParsedTime

_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 7, 11, 10, 0, tzinfo=_TZ).astimezone(timezone.utc)  # 周六 10:00

# M-B：per-speaker 会话态（列表序号 / 待补槽）按 OwnerKey 收窄——底层 profile KV 是
# user 级的，放裸 key 就是两位乘员共用一份：A 列了表，B 说「取消第二个」会命中 A 的第二条。
_PENDING_KEY = owner_scoped(REMINDER_PENDING, "u1", "primary")
_ACTIVE_KEY = owner_scoped(REMINDERS_ACTIVE, "u1", "primary")
_PENDING_SCOPE = f"profile.{_PENDING_KEY}"
_ACTIVE_SCOPE = f"profile.{_ACTIVE_KEY}"


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
    a._llm_time_fallback = AsyncMock(
        return_value=ParsedTime(
            "ok",
            int(_NOW.timestamp()),
            "今天 10:00",
        ),
    )
    ctx = make_context()
    res = await run_handle(a, "reminder.create", raw_text="提醒我开会", ctx=ctx)
    assert res.status == "need_slot" and "time_text" in res.missing_slots
    assert "什么时候" in res.speech
    a._llm_time_fallback.assert_not_awaited()
    # NEED_SLOT 时把标题存进 REMINDER_PENDING（经 profile KV）
    args = ctx._memory.upsert_profile.call_args
    assert args.args[1] == _PENDING_KEY and "开会" in args.args[2]


@pytest.mark.asyncio
async def test_create_resumes_pending_title():
    a = await _agent()
    ctx = make_context(context_values={
        _PENDING_SCOPE: json.dumps({"title": "买牛奶"}, ensure_ascii=False)})
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
async def test_explicit_remind_wording_beats_todo_slot():
    """原话优先（B2-2 @M3 canonical）：planner 误填 kind=todo 时，显式「提醒我」话术
    仍走定时提醒（无时间→追问），不被槽位改写成待办静默成单。"""
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="提醒我吃降压药",
                           slots={"kind": "todo"})
    assert res.status == "need_slot"
    assert "什么时候" in res.speech
    _, todos = await a.store.list_split("u1")
    assert todos == []                       # 没被建成待办


@pytest.mark.asyncio
async def test_todo_slot_honored_without_remind_wording():
    """无「提醒/叫我」冲突时 kind=todo 槽位照常生效（槽位兜底面不回退）。"""
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="记一下要交物业费",
                           slots={"kind": "todo"})
    assert res.status == "ok"
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
    assert _ACTIVE_KEY in keys


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
    ctx = make_context(context_values={_ACTIVE_SCOPE: json.dumps(
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
    assert res2.status == "ok" and "没找到" in res2.speech   # R9：诚实降级用 OK（FAILED 话术会被聚合器吞）


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
    assert res.missing_slots == ["index"]
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
async def test_cancel_all_confirmation_recovers_pending_when_slots_and_raw_are_gone():
    """确认轮的「确定」不再含 all 槽；agent 必须保存并恢复清空动作。"""
    a = await _agent()
    ctx = make_context()
    await a.store.add(Reminder(
        user_id="u1",
        title="检查验收结果",
        kind="time",
        fire_at=10**12,
    ))

    first = await run_handle(
        a,
        "reminder.cancel",
        raw_text="把提醒都清空",
        ctx=ctx,
    )
    assert first.status == "need_confirm"
    saved = ctx._memory.upsert_profile.await_args
    assert saved.args[1] == _PENDING_KEY
    continued = make_context(context_values={
        _PENDING_SCOPE: saved.args[2],
    })

    confirmed = await run_handle(
        a,
        "reminder.cancel",
        slots={},
        raw_text="确定",
        ctx=continued,
        meta={"confirmed": "true"},
    )
    assert confirmed.status == "ok" and "已清空全部" in confirmed.speech
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
async def test_two_creates_in_one_turn_do_not_collapse_into_one(monkeypatch):
    """一句话要两条提醒时，第二步不许把第一步刚建的那条**改期**（Q12 取证抓到）。

    真栈原句「明天下午四点提醒我开会，**三点半再提醒我一次**」规划成两个
    `reminder.create` 步，两步的 `raw` 是同一句话、都含「再提醒」，于是第二步命中
    同名收编、把 0.2 秒前建出来的那条挪到了 15:30：**用户要两条、库里只有一条**，
    而话术照说「15:30 和 16:00 各提醒你一次」——系统声称的与它真做的不一致（同 Q6）。
    """
    a = await _agent()
    meta = {"trace_id": "trace-same-turn"}
    raw = "明天下午四点提醒我开会，三点半再提醒我一次"
    r1 = await run_handle(a, "reminder.create", raw_text=raw, meta=meta,
                          slots={"title": "开会", "time_text": "明天下午四点"})
    r2 = await run_handle(a, "reminder.create", raw_text=raw, meta=meta,
                          slots={"title": "开会", "time_text": "明天下午三点半"})
    assert r1.status == "ok" and r2.status == "ok"
    assert r2.ui_card["context"] == "created"          # 不是 updated
    times, _ = await a.store.list_split("u1")
    assert len(times) == 2
    assert sorted(t.to_card_item(now=_NOW, tz=_TZ)["time_display"] for t in times) \
        == ["明天 15:30", "明天 16:00"]


@pytest.mark.asyncio
async def test_batch_create_is_independent_of_the_planners_step_count():
    """SL1：MiniMax 即使给零步，也由窄能力一次原子表达「同一件事提醒两次」。"""
    a = await _agent()
    raw = "明天下午四点提醒我开会，三点半再提醒我一次"
    res = await run_handle(
        a, "reminder.create_batch", raw_text=raw,
        meta={"trace_id": "trace-batch"})

    assert res.status == "ok"
    assert res.ui_card["type"] == "card_group"
    assert len(res.ui_card["items"]) == 2
    times, _ = await a.store.list_split("u1")
    assert len(times) == 2
    assert sorted(t.to_card_item(now=_NOW, tz=_TZ)["time_display"] for t in times) \
        == ["明天 15:30", "明天 16:00"]
    assert all(t.title == "开会" for t in times)


@pytest.mark.asyncio
async def test_batch_create_rejects_non_batch_shape_without_partial_write():
    a = await _agent()
    res = await run_handle(
        a, "reminder.create_batch",
        raw_text="明天下午四点提醒我开会，顺便查一下天气")
    assert res.status == "need_slot"
    times, todos = await a.store.list_split("u1")
    assert times == [] and todos == []


@pytest.mark.asyncio
async def test_a_later_turn_still_reschedules_the_same_title(monkeypatch):
    """反向对照：**下一轮**说「再提醒」仍然是改期，不是新建。

    只做上面那一半会得到一个「每次 snooze 都堆一条」的系统——
    §4.3「反向验证要两头做」：既证注入缺陷会红，也证没修过头。
    """
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天下午四点提醒我开会",
                     meta={"trace_id": "turn-1"})
    res = await run_handle(a, "reminder.create", raw_text="明天三点半再提醒我开会",
                           meta={"trace_id": "turn-2"})
    assert res.ui_card["context"] == "updated"
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1


@pytest.mark.asyncio
async def test_missing_trace_id_keeps_the_old_behaviour():
    """没有 trace_id（端侧直发/老数据）时逐字维持此前行为——认不出就不排除。"""
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天下午四点提醒我开会")
    res = await run_handle(a, "reminder.create", raw_text="明天三点半再提醒我开会")
    assert res.ui_card["context"] == "updated"


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
    ctx2 = make_context(context_values={_PENDING_SCOPE: pend_json})
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
async def test_cross_domain_departure_word_form_selects_item_not_ambiguous():
    """G1（EVA 二轮）：navigation 带到达时限时会同时写「出发前往X/到达X」两事件。
    「到时候提醒我出发」按词形收窄直取出发事件（此前两未来项会被当歧义反问）。"""
    a = await _agent()
    depart = _ts(2026, 12, 1, 16, 30)
    arrive = _ts(2026, 12, 1, 17, 0)
    ctx = make_context(context_values=_remindable(
        [{"title": "出发前往实验小学", "fire_at": depart},
         {"title": "到达实验小学", "fire_at": arrive}]))
    res = await run_handle(a, "reminder.create", raw_text="到时候提醒我出发", ctx=ctx)
    assert res.status == "ok"
    times, _ = await a.store.list_split("u1")
    assert times[0].fire_at == depart - 600            # 该出发前 10 分钟
    assert times[0].title == "出发前往实验小学"          # 不拼成「出发出发前往…」


@pytest.mark.asyncio
async def test_cross_domain_reference_waits_for_parallel_producer(monkeypatch):
    """A same-turn sports step may publish REMINDABLE_ACTIVE concurrently."""
    a = await _agent()
    k = _ts(2026, 7, 12, 3, 0)
    ctx = make_context()
    ctx.load_shared_state = AsyncMock(side_effect=[
        None,
        json.dumps({
            "source": "info.sports",
            "items": [{"title": "葡萄牙 vs 西班牙", "fire_at": k}],
        }, ensure_ascii=False),
    ])
    sleep = AsyncMock()
    monkeypatch.setattr("agents.reminder.src.agent.asyncio.sleep", sleep)

    res = await run_handle(
        a,
        "reminder.create",
        raw_text="欧联第一场是谁踢？开赛前提醒我",
        ctx=ctx,
    )

    assert res.status == "ok"
    assert "葡萄牙 vs 西班牙" in res.speech
    assert ctx.load_shared_state.await_count == 2
    sleep.assert_awaited_once()


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
        _PENDING_SCOPE: json.dumps({"title": "观看世界杯第一场比赛"},
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
    ctx = make_context(context_values={_ACTIVE_SCOPE: json.dumps(
        {"items": [{"id": r1.id, "title": "喝水"}, {"id": r2.id, "title": "喝水"}]},
        ensure_ascii=False)})
    res = await run_handle(a, "reminder.cancel", raw_text="取消第二条", ctx=ctx)
    assert res.status == "ok"
    # C10-A 后 `store.get` 默认只给 ACTIVE，读终态要显式点名 statuses——
    # 这条断言查的正是「它变成 cancelled 了没有」，属于该显式的那一类。
    assert (await a.store.get(
        "u1", r2.id, statuses=(CANCELLED,))).status == "cancelled"
    assert (await a.store.get("u1", r1.id)).status == "pending"


# ── Q5/I-045：默认查询范围收窄，但不隐藏 ──────────────────────────────────

async def _seed_raw(agent, title: str, fire_at: int):
    """直接塞进存储（绕过 create 的时间解析与**写入闸**，用例要造过期/存量项）。

    ⚠ 刻意绕闸：**写入闸挡的是新写入，存量脏数据还在库里**（清洗 `--apply`
    才管那些）。展示层的过滤必须独立成立——两件事分开验，否则「闸建好了」
    会被误读成「用户不会再看到那三条」。
    """
    agent.store._mem[f"r-{title}"] = Reminder(
        id=f"r-{title}", user_id="u1", occupant_id="primary",
        title=title, fire_at=fire_at, kind="time", status="pending",
        created_at=1)


@pytest.mark.asyncio
async def test_default_list_scope_excludes_expired_but_reports_the_count():
    """真栈实测原样：「我现在有哪些进行中的任务」答出「全部共 20 条」，
    头三条是 7 月的过期项——用户问的是现在，得到的是一份考古清单。

    **收窄不等于隐藏**：过期的另计并显式报数，一条都不许悄悄消失。
    """
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "七月的旧提醒", now - 30 * 86400)
    await _seed_raw(a, "上周的旧提醒", now - 7 * 86400)
    await _seed_raw(a, "明天带伞", now + 86400)

    res = await run_handle(a, "reminder.list", raw_text="我现在有哪些进行中的任务")

    assert "明天带伞" in res.speech
    assert "七月的旧提醒" not in res.speech
    assert "2 条已过期" in res.speech, "过期项必须报数，不许悄悄消失"


@pytest.mark.asyncio
async def test_default_list_without_expired_items_is_still_labelled_upcoming():
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "明天带伞", now + 86400)

    res = await run_handle(a, "reminder.list", raw_text="查看我的提醒")

    assert "接下来共 1 条" in res.speech
    assert "全部共" not in res.speech


@pytest.mark.asyncio
async def test_default_list_reports_exact_total_beyond_display_limit():
    a = await _agent()
    now = int(_NOW.timestamp())
    for idx in range(51):
        await _seed_raw(a, f"未来提醒{idx:02d}", now + (idx + 1) * 60)

    res = await run_handle(a, "reminder.list", raw_text="查看我的提醒")

    assert "接下来共 51 条" in res.speech
    assert len(res.ui_card["items"]) == 50


@pytest.mark.asyncio
async def test_todo_only_does_not_report_expired_timed_reminders():
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "旧闹钟", now - 60)
    await a.store.add(Reminder(user_id="u1", title="买牛奶", kind="todo"))

    res = await run_handle(a, "reminder.list", raw_text="只看待办")

    assert "待办共 1 条" in res.speech
    assert "已过期" not in res.speech


@pytest.mark.asyncio
async def test_dated_reminder_scope_excludes_undated_todos():
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "明天带伞", now + 86400)
    await a.store.add(Reminder(user_id="u1", title="买牛奶", kind="todo"))

    res = await run_handle(a, "reminder.list", raw_text="明天有什么提醒")

    assert "明天带伞" in res.speech
    assert "买牛奶" not in res.speech
    assert "共 1 条" in res.speech
    assert res.ui_card["todos"] == []


@pytest.mark.asyncio
async def test_default_list_finds_future_item_after_more_than_fifty_expired_rows():
    """LIMIT 必须作用在「未来项」上，不能先被最早的 50 条过期项占满。

    真栈里标题检索能找到明天下午的提醒，但默认列表称没有进行中；根因正是
    ``ORDER BY fire_at ASC LIMIT 50`` 后才在 Agent 层过滤过期项。
    """
    a = await _agent()
    now = int(_NOW.timestamp())
    for idx in range(51):
        await _seed_raw(a, f"旧提醒{idx:02d}", now - (idx + 1) * 60)
    await _seed_raw(a, "明天下午交周报", now + 86400)

    res = await run_handle(a, "reminder.list", raw_text="查看我的提醒")

    assert "明天下午交周报" in res.speech
    assert "51 条已过期" in res.speech
    assert [item["title"] for item in res.ui_card["items"]] == ["明天下午交周报"]


@pytest.mark.asyncio
async def test_explicit_all_still_shows_everything():
    """反向对照：用户明说「全部」时不收窄——那是他要的。"""
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "七月的旧提醒", now - 30 * 86400)
    await _seed_raw(a, "明天带伞", now + 86400)

    res = await run_handle(a, "reminder.list", raw_text="看全部提醒")

    assert "七月的旧提醒" in res.speech


@pytest.mark.asyncio
async def test_invalid_fire_at_zero_is_not_shown_as_upcoming():
    """`fire_at=0` 的**定时**提醒永远不会触发，而按 fire_at 升序排还**永远排在最前**
    ——I-056 里用户看到的「妈妈住杭州、停车位在B2」就是这三条（N2 实证：时间解析
    失败 + 非任务陈述被建成提醒，两个缺陷叠加后仍然写进了库）。

    它们与过期项一起计数、一起不进「接下来」。真正的 todo 不受影响。
    """
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "妈妈住杭州", 0)
    await _seed_raw(a, "明天带伞", now + 86400)

    res = await run_handle(a, "reminder.list", raw_text="我现在有哪些进行中的任务")

    assert "明天带伞" in res.speech
    assert "妈妈住杭州" not in res.speech
    assert "1 条已过期" in res.speech


# ── Q11 写入闸 + 否定守卫 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_rejects_a_reminder_that_can_never_fire():
    """N2：**时间解析失败了，创建仍然成功**——库里躺着三条 `fire_at=0` 的 pending。

    存储层对「什么是一条有效提醒」零校验，是这个缺陷能落库的最后一环：
    上游两个缺陷叠加之后，**仍然写进了库**。
    """
    from agents.reminder.src.store import InvalidReminder
    a = await _agent()
    with pytest.raises(InvalidReminder):
        await a.store.add(Reminder(user_id="u1", title="妈妈住杭州",
                                   kind="time", fire_at=0))


@pytest.mark.asyncio
async def test_store_still_accepts_todos_and_location_reminders():
    """反向对照：待办与位置提醒**本来就没有时刻**，不许被这道闸误挡。"""
    a = await _agent()
    await a.store.add(Reminder(user_id="u1", title="买牛奶", kind="todo"))
    await a.store.add(Reminder(user_id="u1", title="到公司提醒我",
                               kind="location", extra={"place": "公司"}))


@pytest.mark.asyncio
async def test_explicit_no_reminder_is_honoured():
    """I-009②：「接爸妈去吃饭，**别建提醒**」实测建了一条，正文含「别建提醒」四个字。"""
    for raw in ("接爸妈去吃饭，别建提醒", "不用提醒我", "这个不要提醒", "别设闹钟"):
        a = await _agent()
        res = await run_handle(a, "reminder.create", raw_text=raw)
        assert res.status == "ok" and "不建提醒" in res.speech, raw
        times, todos = await a.store.list_split("u1")
        assert not times and not todos, f"{raw} 仍然建了提醒"


@pytest.mark.asyncio
async def test_double_negative_still_creates():
    """反向对照：「**别忘了**提醒我八点开会」真实语义是**要建**。
    挡它等于反向漏执行——同 `runtime.polarity` 的双重否定例外。"""
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="别忘了明天八点提醒我开会")
    assert "不建提醒" not in res.speech


# ── C10：序数参照系 / 标题精确度 / 任务性准入 / 跨轮幂等 ────────────────────

@pytest.mark.asyncio
async def test_ordinal_reference_frame_ignores_expired_backlog():
    """C10-A（真栈 T59 的真根因）：库里沉着过期垃圾时，一次成功的取消**不许**
    把序数参照系静默换成一份用户从来没看见过的考古清单。

    F 组调查指出既有同形用例跑绿的前提是**空库**；这条把那个前提补掉。
    """
    a = await _agent()
    now = int(_NOW.timestamp())
    for idx in range(12):                       # 12 条过期沉积，够顶满 10 条上限
        await _seed_raw(a, f"过期垃圾{idx:02d}", now - (idx + 1) * 86400)
    await _seed_raw(a, "明天带伞", now + 86400)
    await _seed_raw(a, "后天交周报", now + 2 * 86400)

    ctx = make_context()
    await run_handle(a, "reminder.complete", raw_text="完成明天带伞", ctx=ctx)

    written = [c.args[2] for c in ctx._memory.upsert_profile.call_args_list
               if c.args[1] == _ACTIVE_KEY]
    assert written, "取消/完成后必须刷新序数参照系"
    titles = [item["title"] for item in json.loads(written[-1])["items"]]
    assert titles == ["后天交周报"], f"序数参照系指向了用户看不见的条目：{titles}"


@pytest.mark.asyncio
async def test_cancel_prefers_the_exact_title_over_a_substring_match():
    """C10-B 精确度阶梯：planner 转述会放宽标题，子串于是同时命中两条。

    逐字相等的那条存在时只认它——否则**取消掉的可能不是用户点名的那条**。
    """
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "评审会", now + 3600)
    await _seed_raw(a, "准备评审会材料", now + 7200)

    res = await run_handle(a, "reminder.cancel", slots={"title": "评审会"},
                           raw_text="取消评审会")

    assert res.status == "ok" and "「评审会」" in res.speech
    remaining = sorted(r.title for r in (await a.store.list_split("u1"))[0])
    assert remaining == ["准备评审会材料"]


@pytest.mark.asyncio
async def test_cancel_without_an_exact_match_still_clarifies():
    """反向对照：没有逐字相等时，多条子串命中仍走澄清（既有行为不变）。"""
    a = await _agent()
    now = int(_NOW.timestamp())
    await _seed_raw(a, "准备评审会材料", now + 3600)
    await _seed_raw(a, "评审会纪要", now + 7200)

    res = await run_handle(a, "reminder.cancel", slots={"title": "评审会"},
                           raw_text="取消评审会")

    assert res.status == "need_slot" and res.missing_slots == ["index"]
    assert len((await a.store.list_split("u1"))[0]) == 2


@pytest.mark.asyncio
async def test_create_refuses_a_question_shaped_title():
    """C10-C：问句不是一件待办。真栈实录里它建成功了，还进了序数参照系。"""
    a = await _agent()
    res = await run_handle(
        a, "reminder.create", raw_text="明天早上八点提醒我刚才那个提醒现在几点")

    assert res.status == "ok"                    # 诚实拒绝用 OK（R9）
    assert "不像一件要做的事" in res.speech
    assert (await a.store.list_split("u1")) == ([], [])


@pytest.mark.asyncio
async def test_create_refuses_a_third_person_statement_title():
    a = await _agent()
    res = await run_handle(
        a, "reminder.create",
        slots={"title": "用户计划2026年国庆前往青岛4天行程",
               "time_text": "明天早上八点"},
        raw_text="明天早上八点提醒我用户计划2026年国庆前往青岛4天行程")

    assert res.status == "ok" and "不像一件要做的事" in res.speech
    assert (await a.store.list_split("u1")) == ([], [])


@pytest.mark.asyncio
async def test_create_still_admits_real_tasks():
    """硬负对照：准入不许把真任务挡在外面（正 2）。"""
    a = await _agent()
    for raw in ("明天早上八点提醒我参加评审会", "明天早上九点提醒我交周报"):
        res = await run_handle(a, "reminder.create", raw_text=raw)
        assert res.status == "ok" and "不像一件要做的事" not in res.speech, raw
    titles = sorted(r.title for r in (await a.store.list_split("u1"))[0])
    assert titles == ["交周报", "参加评审会"]


@pytest.mark.asyncio
async def test_create_is_idempotent_across_turns():
    """C10-E：同 owner + 逐字同标题 + 同时刻的 pending 已存在 ⇒ 收编不新建。

    真栈实录：长会话里同一句被重复规划，库里于是躺着三条一模一样的 09:00。
    """
    a = await _agent()
    first = await run_handle(a, "reminder.create",
                             raw_text="明天早上九点提醒我吃药",
                             meta={"trace_id": "turn-1"})
    assert first.status == "ok" and "好的" in first.speech

    again = await run_handle(a, "reminder.create",
                             raw_text="明天早上九点提醒我吃药",
                             meta={"trace_id": "turn-2"})

    assert again.status == "ok" and "已经有一条了" in again.speech
    assert again.ui_card is None, "没建东西就不该给一张「已创建」的卡"
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1


@pytest.mark.asyncio
async def test_same_turn_two_steps_are_still_not_deduplicated():
    """Q12 的裁定原样保留：**同轮不收编**。

    「明天下午四点提醒我开会，三点半再提醒我一次」会被规划成两个 create 步，
    两步的 `raw` 是同一句话——按轮次排除，否则第二步会把第一步刚建的那条改掉/吞掉，
    用户要两条、库里只有一条，而话术照说「各提醒你一次」。
    """
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天下午四点提醒我开会",
                     meta={"trace_id": "same-turn"})
    second = await run_handle(a, "reminder.create", raw_text="明天下午四点提醒我开会",
                              meta={"trace_id": "same-turn"})

    assert "已经有一条了" not in second.speech
    times, _ = await a.store.list_split("u1")
    assert len(times) == 2


# ── 域词漏进标题槽：查空之后再削一次尾（余项 ③ 症状②，2026-08-29）─────────────
# 真栈逐字实录（deployed `ed53f8f`，干净会话）：
#   「取消参加代号17879686214的评审会的提醒」→ 落域正确（`reminder.cancel`）
#   → 「没找到这条提醒」，而紧接着同一 owner 的列表里**它还在**。
# 机制：`find_by_title` 是 `title LIKE %q%`，q 比库里那条标题**长**就必然不匹配；
# planner 把整串（含「的提醒」）塞进 `title` 槽时，`q == raw` 那条兜底削尾不触发。
# 讽刺的地方值得记一笔：**「取消X的提醒」正是落域最可靠的那种说法**
# （同日受控对照：带域词 18/18，不带 3/12）——说得更清楚反而查不到。

@pytest.mark.asyncio
async def test_cancel_finds_target_when_domain_word_leaks_into_the_title_slot():
    a = await _agent()
    ctx = make_context()
    await run_handle(a, "reminder.create",
                     raw_text="明天下午四点提醒我参加代号889001的评审会", ctx=ctx)
    res = await run_handle(a, "reminder.cancel",
                           slots={"title": "参加代号889001的评审会的提醒"},
                           raw_text="取消参加代号889001的评审会的提醒", ctx=ctx)
    assert res.status == "ok", res.speech
    assert "取消了" in res.speech and "参加代号889001的评审会" in res.speech
    times, todos = await a.store.list_split("u1")
    assert len(times) + len(todos) == 0, "削尾之后应当真的取消掉，不是只把话说对"


@pytest.mark.asyncio
async def test_domain_tail_trim_only_runs_after_an_empty_lookup():
    """**只能把「没找到」变成「找到」**：本来就命中的那次匹配一个字都不许变。

    库里真有一条叫「妈妈的提醒」时，第一次逐字查就命中，削尾这一步压根不该发生
    （削了会变成「妈妈」，可能捞到另一条）。
    """
    a = await _agent()
    ctx = make_context()
    await run_handle(a, "reminder.create",
                     raw_text="明天下午四点提醒我妈妈的提醒", ctx=ctx)
    await run_handle(a, "reminder.create",
                     raw_text="明天下午五点提醒我妈妈生日", ctx=ctx)
    res = await run_handle(a, "reminder.cancel", slots={"title": "妈妈的提醒"},
                           raw_text="取消妈妈的提醒", ctx=ctx)
    assert res.status == "ok", res.speech
    assert "妈妈的提醒" in res.speech
    times, todos = await a.store.list_split("u1")
    left = [r.title for r in times + todos]
    assert left == ["妈妈生日"], f"削尾误伤了另一条：{left}"


@pytest.mark.asyncio
async def test_bare_domain_word_is_not_trimmed():
    """光杆「提醒」不削——`_TITLE_DOMAIN_TAIL_RE` 要求带「的/这条/那条」这类连接词。

    一条真叫「买提醒」的待办被削成「买」，就会去捞「买牛奶」；
    **扩大匹配面这一步必须比缩小匹配面更保守。**
    """
    a = await _agent()
    ctx = make_context()
    await run_handle(a, "reminder.create", raw_text="明天下午四点提醒我买牛奶", ctx=ctx)
    res = await run_handle(a, "reminder.cancel", slots={"title": "买提醒"},
                           raw_text="取消买提醒", ctx=ctx)
    assert "没找到这条提醒" in res.speech, res.speech
    times, todos = await a.store.list_split("u1")
    assert [r.title for r in times + todos] == ["买牛奶"], "削光杆域词捞错了条目"
