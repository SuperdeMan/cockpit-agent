"""ReminderStore 内存分支语义（PG 分支由 test/e2e_reminder.py 真栈覆盖）。"""
import pytest

from agents.reminder.src.store import Reminder, ReminderStore


async def _store() -> ReminderStore:
    s = ReminderStore(dsn="")   # 强制内存分支
    await s.init()
    return s


@pytest.mark.asyncio
async def test_add_and_list_ordering():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="B", kind="time", fire_at=2000))
    await s.add(Reminder(user_id="u1", title="A", kind="time", fire_at=1000))
    await s.add(Reminder(user_id="u1", title="T", kind="todo"))
    times, todos = await s.list_split("u1")
    assert [r.title for r in times] == ["A", "B"]      # fire_at 升序
    assert [r.title for r in todos] == ["T"]


@pytest.mark.asyncio
async def test_list_range_filters():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="今早", kind="time", fire_at=1000))
    await s.add(Reminder(user_id="u1", title="下周", kind="time", fire_at=9000))
    times, _ = await s.list_split("u1", from_ts=0, to_ts=5000)
    assert [r.title for r in times] == ["今早"]


@pytest.mark.asyncio
async def test_claim_due_atomic_and_cross_user():
    s = await _store()
    r1 = await s.add(Reminder(user_id="u1", title="X", kind="time", fire_at=100))
    await s.add(Reminder(user_id="u2", title="Y", kind="time", fire_at=100))
    due1 = await s.claim_due(200)
    due2 = await s.claim_due(200)                       # 二次领取必须为空（防重复触发）
    assert sorted(d.title for d in due1) == ["X", "Y"]  # 跨用户
    assert all(d.status == "fired" for d in due1)
    assert due2 == []
    assert (await s.get("u1", r1.id)).status == "fired"


@pytest.mark.asyncio
async def test_todo_and_future_never_claimed():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="T", kind="todo"))
    await s.add(Reminder(user_id="u1", title="F", kind="time", fire_at=10 ** 12))
    assert await s.claim_due(10 ** 9) == []


@pytest.mark.asyncio
async def test_find_by_title_and_set_status():
    s = await _store()
    r = await s.add(Reminder(user_id="u1", title="买牛奶", kind="time", fire_at=1000))
    assert [h.id for h in await s.find_by_title("u1", "牛奶")] == [r.id]
    assert await s.set_status("u1", r.id, "done")
    assert (await s.get("u1", r.id)).status == "done"
    assert await s.find_by_title("u1", "牛奶") == []    # done 不入默认过滤
    assert not await s.set_status("u1", "no-such-id", "done")


@pytest.mark.asyncio
async def test_cancel_all_scoped_to_user():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="A", kind="time", fire_at=1000))
    await s.add(Reminder(user_id="u1", title="B", kind="todo"))
    await s.add(Reminder(user_id="u2", title="C", kind="time", fire_at=1000))
    assert await s.cancel_all("u1") == 2
    times, todos = await s.list_split("u1")
    assert times == [] and todos == []
    times2, _ = await s.list_split("u2")
    assert len(times2) == 1


@pytest.mark.asyncio
async def test_update_fire_at_reschedules_and_revives_fired():
    s = await _store()
    r = await s.add(Reminder(user_id="u1", title="回电话", kind="time", fire_at=100))
    await s.claim_due(200)                                   # → fired（尸体）
    assert await s.update_fire_at("u1", r.id, 900)           # snooze 收编
    got = await s.get("u1", r.id)
    assert got.status == "pending" and got.fire_at == 900
    assert not await s.update_fire_at("u1", "no-such", 900)
    await s.set_status("u1", r.id, "done")
    assert not await s.update_fire_at("u1", r.id, 999)       # done/cancelled 不可改期


@pytest.mark.asyncio
async def test_roll_recurring_only_from_fired():
    s = await _store()
    r = await s.add(Reminder(user_id="u1", title="吃药", kind="time",
                             fire_at=100, recur="daily"))
    assert not await s.roll_recurring("u1", r.id, 500)       # 还没 fired 不滚
    await s.claim_due(200)
    assert await s.roll_recurring("u1", r.id, 500)
    got = await s.get("u1", r.id)
    assert got.status == "pending" and got.fire_at == 500 and got.recur == "daily"


def test_to_card_item_recur_label():
    r = Reminder(id="r1", user_id="u1", title="吃药", kind="time",
                 fire_at=10 ** 10, recur="daily")
    assert r.to_card_item()["recur_label"] == "每天"
    assert "recur_label" not in Reminder(id="r2", user_id="u1", title="X",
                                         kind="time", fire_at=10 ** 10).to_card_item()


def test_to_card_item_contract():
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 11, 10, 0, tzinfo=tz).astimezone(timezone.utc)
    fire = int(datetime(2026, 7, 12, 8, 0, tzinfo=tz).timestamp())
    r = Reminder(id="rid1", user_id="u1", title="带充电线", kind="time",
                 fire_at=fire, status="pending")
    item = r.to_card_item(now=now, tz=tz)
    assert item == {"id": "rid1", "title": "带充电线", "kind": "time",
                    "status": "pending", "time_display": "明天 08:00",
                    "fire_at_ms": fire * 1000}
    todo = Reminder(id="rid2", user_id="u1", title="买牛奶", kind="todo")
    assert todo.to_card_item(now=now, tz=tz)["time_display"] == ""
