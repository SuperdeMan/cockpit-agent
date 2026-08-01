"""围栏触达的 OwnerKey 分组（M-B）。

围栏判定由车况驱动、天然跨 owner（同一个地点会同时命中两位乘员的提醒），
但触达必须按 OwnerKey 分组：一条 speech/card 只能属于一个人，
`items[0].user_id` 不能代表混合 owner 集合。
"""
import pytest

from agents.reminder.src.geofence import GeofenceWatcher
from agents.reminder.src.store import Reminder, ReminderStore


class Pub:
    def __init__(self):
        self.sent = []

    async def __call__(self, payload: dict):
        self.sent.append(payload)


def _loc_reminder(user_id, occupant_id, title, lat, lon):
    return Reminder(user_id=user_id, occupant_id=occupant_id, title=title,
                    kind="location",
                    extra={"place": "公司", "lat": lat, "lon": lon,
                           "radius_m": 200, "trigger_on": "arrive"})


async def _watcher_with(pub, *reminders, now=1000.0):
    s = ReminderStore(dsn="")
    await s.init()
    for r in reminders:
        await s.add(r)
    return GeofenceWatcher(s, pub, now_fn=lambda: now), s


_FAR = {"lat": 40.0, "lng": 116.0}
_NEAR = {"lat": 31.2, "lng": 121.4}


@pytest.mark.asyncio
async def test_two_owners_hitting_the_same_fence_are_published_separately():
    pub = Pub()
    w, _ = await _watcher_with(
        pub,
        _loc_reminder("u1", "primary", "主驾拿文件", 31.2, 121.4),
        _loc_reminder("u1", "occ-2", "乘客交表", 31.2, 121.4))

    await w.on_state([], {"location": _FAR})    # 首次观测只播种
    n = await w.on_state([], {"location": _NEAR})

    assert n == 2 and len(pub.sent) == 2
    by_occ = {p["owner_occupant_id"]: p for p in pub.sent}
    assert set(by_occ) == {"primary", "occ-2"}
    assert "主驾拿文件" in by_occ["primary"]["speech"]
    assert "乘客交表" not in by_occ["primary"]["speech"]
    assert "乘客交表" in by_occ["occ-2"]["speech"]


@pytest.mark.asyncio
async def test_two_users_hitting_the_same_fence_are_published_separately():
    pub = Pub()
    w, _ = await _watcher_with(
        pub,
        _loc_reminder("u1", "primary", "U1文件", 31.2, 121.4),
        _loc_reminder("u2", "primary", "U2快递", 31.2, 121.4))

    await w.on_state([], {"location": _FAR})
    n = await w.on_state([], {"location": _NEAR})

    assert n == 2
    assert {p["user_id"] for p in pub.sent} == {"u1", "u2"}


@pytest.mark.asyncio
async def test_geofence_card_actions_pin_owner_and_id():
    pub = Pub()
    w, _ = await _watcher_with(pub, _loc_reminder("u1", "occ-2", "交表", 31.2, 121.4))

    await w.on_state([], {"location": _FAR})
    await w.on_state([], {"location": _NEAR})

    action = pub.sent[0]["card"]["actions"][0]
    assert action["owner_occupant_id"] == "occ-2" and action["reminder_id"]
