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


@pytest.mark.asyncio
async def test_snooze_refire_gets_fresh_dedup_key():
    """snooze 保留原条目 id → dedup_key 只按 id 会把第二次触发在治理器 10 分钟
    去重窗里静默吞掉（「过5分钟再叫我」用户永远等不到）。触发时刻必须进 key：
    同一次触发的重投 key 相同（判重仍成立），跨次触发 key 必不同。"""
    pub = Pub()
    r = Reminder(user_id="u1", title="喝水", kind="time", fire_at=100)
    s = await _store_with(r)
    clock = {"t": 200.0}
    sched = ReminderScheduler(s, pub, now_fn=lambda: clock["t"])
    await sched.tick()
    k1 = pub.sent[0]["dedup_key"]

    # snooze：同一条目改期回 pending（保留 id），5 分钟后再触发
    await s.update_fire_at("u1", r.id, 500)
    clock["t"] = 600.0
    await sched.tick()
    assert len(pub.sent) == 2, "snooze 后第二次触发必须真的发出"
    k2 = pub.sent[1]["dedup_key"]
    assert k1 != k2, "跨次触发的 dedup_key 必须不同，否则被治理器去重窗吞掉"
    assert k1.rsplit("|", 1)[0] == k2.rsplit("|", 1)[0], "同一条目的 id 部分保持稳定"


# ── OwnerKey 分组（M-B）──────────────────────────────────
@pytest.mark.asyncio
async def test_two_users_due_in_the_same_tick_get_separate_payloads():
    """全局扫描可以跨 owner 原子领取，但**消费必须先分组**。

    此前整批共用一条 speech/card 且 `user_id` 取 due[0]——一个人会听到另一个人的
    提醒，而整条消息还被记在第一个人名下。
    """
    pub = Pub()
    s = await _store_with(
        Reminder(user_id="u1", title="U1的会", kind="time", fire_at=100),
        Reminder(user_id="u2", title="U2的药", kind="time", fire_at=100))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()

    assert n == 2 and len(pub.sent) == 2
    by_user = {p["user_id"]: p for p in pub.sent}
    assert set(by_user) == {"u1", "u2"}
    assert "U1的会" in by_user["u1"]["speech"] and "U2的药" not in by_user["u1"]["speech"]
    assert "U2的药" in by_user["u2"]["speech"] and "U1的会" not in by_user["u2"]["speech"]


@pytest.mark.asyncio
async def test_two_occupants_of_one_user_also_get_separate_payloads():
    pub = Pub()
    s = await _store_with(
        Reminder(user_id="u1", occupant_id="primary", title="主驾的会",
                 kind="time", fire_at=100),
        Reminder(user_id="u1", occupant_id="occ-2", title="乘客的药",
                 kind="time", fire_at=100))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()

    assert n == 2 and len(pub.sent) == 2
    by_occ = {p["owner_occupant_id"]: p for p in pub.sent}
    assert set(by_occ) == {"primary", "occ-2"}
    assert "主驾的会" in by_occ["primary"]["speech"]
    assert "乘客的药" in by_occ["occ-2"]["speech"]


@pytest.mark.asyncio
async def test_card_actions_pin_reminder_id_and_owner():
    """卡片 action 带精确 id + pinned owner：HMI 点「完成」不拿点击时的声纹身份去猜，
    也不按标题模糊匹配跨乘员操作同名提醒。**pin 只是数据路由，不是权限凭据。**"""
    pub = Pub()
    s = await _store_with(Reminder(user_id="u1", occupant_id="occ-2", title="吃药",
                                   kind="time", fire_at=100))
    await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()

    actions = pub.sent[0]["card"]["actions"]
    assert all(a["owner_occupant_id"] == "occ-2" for a in actions)
    assert all(a["reminder_id"] for a in actions)
