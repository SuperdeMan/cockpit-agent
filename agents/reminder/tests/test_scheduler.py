"""调度器 tick 语义：领取→合并播报→publish 一次；异常不炸循环。"""
import pytest

from agents.reminder.src.scheduler import ReminderScheduler
from agents.reminder.src.store import Reminder, ReminderStore


class Pub:
    def __init__(self, fail: bool = False):
        self.sent, self.fail = [], fail

    async def __call__(self, payload: dict):
        if self.fail:
            raise RuntimeError("nats down")
        self.sent.append(payload)


async def _store_with(*reminders) -> ReminderStore:
    s = ReminderStore(dsn="")
    await s.init()
    for r in reminders:
        await s.add(r)
    return s


@pytest.mark.asyncio
async def test_tick_no_due_no_publish():
    pub = Pub()
    s = await _store_with(Reminder(user_id="u1", title="F", kind="time", fire_at=10 ** 12))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 100.0).tick()
    assert n == 0 and pub.sent == []


@pytest.mark.asyncio
async def test_tick_single_fired_payload_contract():
    pub = Pub()
    s = await _store_with(Reminder(user_id="u1", title="给客户回电话",
                                   kind="time", fire_at=100))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()
    assert n == 1 and len(pub.sent) == 1
    p = pub.sent[0]
    assert p["type"] == "reminder_fired" and p["agent_id"] == "reminder"
    assert "给客户回电话" in p["speech"]
    assert p["card"]["type"] == "reminder_card" and p["card"]["context"] == "fired"
    assert p["card"]["item"]["title"] == "给客户回电话"
    labels = [a["label"] for a in p["card"]["actions"]]
    assert labels == ["完成", "稍后10分钟"]
    assert p["card"]["actions"][1]["send_text"] == "10分钟后再提醒我给客户回电话"


@pytest.mark.asyncio
async def test_tick_merges_multiple_into_one_publish():
    pub = Pub()
    s = await _store_with(
        Reminder(user_id="u1", title="A", kind="time", fire_at=100),
        Reminder(user_id="u1", title="B", kind="time", fire_at=110))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()
    assert n == 2 and len(pub.sent) == 1          # 合并为一次播报，防连环轰炸
    p = pub.sent[0]
    assert "2 条" in p["speech"] and "A" in p["speech"] and "B" in p["speech"]
    assert p["card"]["type"] == "card_group"
    assert [c["item"]["title"] for c in p["card"]["items"]] == ["A", "B"]


@pytest.mark.asyncio
async def test_tick_survives_publish_failure():
    pub = Pub(fail=True)
    s = await _store_with(Reminder(user_id="u1", title="X", kind="time", fire_at=100))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()
    assert n == 1                                  # 已领取（fired 不回滚），失败仅日志
    assert (await s.claim_due(300)) == []          # 不会重复触发


@pytest.mark.asyncio
async def test_tick_rolls_recurring_to_next_and_no_refire():
    """P1a：重复系列触发后滚动（fired→pending 下一次），一次性条目留 fired。"""
    pub = Pub()
    s = await _store_with(
        Reminder(user_id="u1", title="吃药", kind="time", fire_at=100, recur="daily"),
        Reminder(user_id="u1", title="一次性", kind="time", fire_at=100))
    sched = ReminderScheduler(s, pub, now_fn=lambda: 200.0)
    assert await sched.tick() == 2 and len(pub.sent) == 1
    times, _ = await s.list_split("u1", statuses=("pending",))
    assert [r.title for r in times] == ["吃药"]              # 滚动回 pending
    assert times[0].fire_at == 100 + 86400                   # 下一天同刻（固定 +8 无夏令时）
    fired, _ = await s.list_split("u1", statuses=("fired",))
    assert [r.title for r in fired] == ["一次性"]            # 非重复保持 fired
    assert await sched.tick() == 0                           # 滚动后不重复触发


@pytest.mark.asyncio
async def test_tick_recurring_rolls_even_if_publish_fails():
    pub = Pub(fail=True)
    s = await _store_with(Reminder(user_id="u1", title="吃药", kind="time",
                                   fire_at=100, recur="daily"))
    assert await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick() == 1
    times, _ = await s.list_split("u1", statuses=("pending",))
    assert len(times) == 1 and times[0].fire_at > 200        # 投递失败系列不停摆
