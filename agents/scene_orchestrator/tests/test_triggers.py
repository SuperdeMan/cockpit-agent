"""触发运行时单测（D6/D7）。

两条底线：
1. **零执行权**——触发只产建议卡（speech+card+buttons），**绝不产 actions**。自动化规则在
   行车环境直接动车身是量产不可接受的安全面。
2. **边沿触发**——只在「从不满足 → 满足」发一次。否则 battery=19 每来一次状态广播就播一遍，
   成了骚扰风暴。
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.scene_orchestrator.src.store import Scene, SceneStore
from agents.scene_orchestrator.src.triggers import TriggerWatcher, enrich_env, next_fire_at

_TZ = timezone(timedelta(hours=8))


class FakeMirror:
    def __init__(self):
        self.cbs = []

    def on_change(self, cb):
        self.cbs.append(cb)

    async def fire(self, state):
        for cb in self.cbs:
            await cb([{"key": k, "new": v} for k, v in state.items()], state)


class Bus:
    def __init__(self, active=None):
        self.sent = []
        self.active = active or {}

    async def publish(self, p):
        self.sent.append(p)

    async def load(self, ids):
        return dict(self.active)


def _run(coro):
    return asyncio.run(coro)


async def _watcher(scenes, bus, mirror, **kw):
    store = SceneStore(dsn="")
    await store.init()
    for s in scenes:
        await store.save(s)
    w = TriggerWatcher(store, mirror, bus.publish, poll_s=999, tz=_TZ,
                       load_active=bus.load, **kw)
    mirror.on_change(w._on_state)
    return w


def _low_battery_scene():
    return Scene(user_id="u1", name="省电出行模式", description="降能耗",
                 actions=[{"type": "vehicle.control", "command": "ambient_light.close",
                           "params": {}, "require_confirm": False}],
                 triggers=[{"type": "event",
                            "spec": {"key": "battery", "op": "lt", "value": 20}}])


# ── 事件触发 ────────────────────────────────────────────────────────────────

def test_event_trigger_suggests_but_never_executes():
    """D6 铁律：触发产物只有建议卡，**没有 actions**——触发不是第二条执行入口。"""
    async def go():
        bus, mirror = Bus(), FakeMirror()
        await _watcher([_low_battery_scene()], bus, mirror)
        await mirror.fire({"battery": 15, "gear": "D"})
        return bus.sent
    sent = _run(go())
    assert len(sent) == 1
    p = sent[0]
    assert p["type"] == "scene_suggest" and "省电出行模式" in p["speech"]
    assert "actions" not in p, "触发路径绝不能带执行动作"
    assert p["card"]["buttons"][0]["send_text"] == "开启省电出行模式"   # 回发原话走语音链路


def test_event_trigger_is_edge_not_level():
    """边沿触发：battery 一直低于 20 也只播一次，不是每来一帧状态就播。"""
    async def go():
        bus, mirror = Bus(), FakeMirror()
        await _watcher([_low_battery_scene()], bus, mirror)
        await mirror.fire({"battery": 15, "gear": "D"})
        await mirror.fire({"battery": 14, "gear": "D"})
        await mirror.fire({"battery": 12, "gear": "D"})
        return bus.sent
    assert len(_run(go())) == 1, "持续满足只该在变沿发一次"


def test_edge_rearms_after_leaving_condition():
    """充上电回到 60% 再掉到 15% → 是新的一次变沿，应当再提醒（但受节流限制）。"""
    async def go():
        bus, mirror = Bus(), FakeMirror()
        w = await _watcher([_low_battery_scene()], bus, mirror, throttle_s=0)
        await mirror.fire({"battery": 15, "gear": "D"})
        await mirror.fire({"battery": 60, "gear": "D"})      # 条件解除 → 重新武装
        await mirror.fire({"battery": 10, "gear": "D"})
        return bus.sent
    assert len(_run(go())) == 2


def test_throttle_blocks_repeat_within_window():
    async def go():
        bus, mirror = Bus(), FakeMirror()
        await _watcher([_low_battery_scene()], bus, mirror, throttle_s=9999)
        await mirror.fire({"battery": 15, "gear": "D"})
        await mirror.fire({"battery": 60, "gear": "D"})
        await mirror.fire({"battery": 10, "gear": "D"})      # 变沿了，但在节流窗内
        return bus.sent
    assert len(_run(go())) == 1


def test_unsatisfied_condition_no_suggest():
    async def go():
        bus, mirror = Bus(), FakeMirror()
        await _watcher([_low_battery_scene()], bus, mirror)
        await mirror.fire({"battery": 80, "gear": "D"})
        return bus.sent
    assert _run(go()) == []


def test_scene_without_trigger_never_fires():
    async def go():
        bus, mirror = Bus(), FakeMirror()
        await _watcher([Scene(user_id="u1", name="钓鱼模式",
                              actions=[{"command": "fragrance.on"}])], bus, mirror)
        await mirror.fire({"battery": 5, "gear": "D"})
        return bus.sent
    assert _run(go()) == []


# ── 时间触发 ────────────────────────────────────────────────────────────────

def test_next_fire_at_daily():
    now = datetime(2026, 7, 14, 10, 0, tzinfo=_TZ)          # 周二 10:00
    ts = next_fire_at({"at": "12:30", "recur": "daily"}, now, _TZ)
    assert datetime.fromtimestamp(ts, _TZ).hour == 12
    assert datetime.fromtimestamp(ts, _TZ).day == 14        # 今天还没到 → 今天

    late = datetime(2026, 7, 14, 13, 0, tzinfo=_TZ)          # 已经过了 → 明天
    ts2 = next_fire_at({"at": "12:30", "recur": "daily"}, late, _TZ)
    assert datetime.fromtimestamp(ts2, _TZ).day == 15


def test_next_fire_at_workday_skips_weekend():
    fri_evening = datetime(2026, 7, 17, 20, 0, tzinfo=_TZ)   # 周五晚
    ts = next_fire_at({"at": "08:00", "recur": "workday"}, fri_evening, _TZ)
    assert datetime.fromtimestamp(ts, _TZ).weekday() == 0    # 跳过周末 → 周一


def test_next_fire_at_bad_spec():
    assert next_fire_at({"at": "25:99"}, datetime.now(timezone.utc), _TZ) == 0
    assert next_fire_at({}, datetime.now(timezone.utc), _TZ) == 0


def test_time_trigger_fires_and_rolls():
    """到点发建议卡；消费后重算下一次（recur 滚动），不会在同一天反复播。"""
    async def go():
        bus, mirror = Bus(), FakeMirror()
        s = Scene(user_id="u1", name="午睡模式",
                  actions=[{"type": "vehicle.control", "command": "volume.set",
                            "params": {"level": "0"}, "require_confirm": False}],
                  triggers=[{"type": "time", "spec": {"at": "12:30", "recur": "daily"}}])
        w = await _watcher([s], bus, mirror)
        base = datetime(2026, 7, 14, 12, 0, tzinfo=_TZ)
        assert await w.poll_once(base) == 0                  # 还没到点
        assert await w.poll_once(base.replace(hour=12, minute=31)) == 1
        assert await w.poll_once(base.replace(hour=12, minute=32)) == 0   # 不重复播
        return bus.sent
    sent = _run(go())
    assert len(sent) == 1 and "午睡模式" in sent[0]["speech"]
    assert "actions" not in sent[0]


# ── 驻车补做（P2 verify 挂的队列在这里兑现）─────────────────────────────────

def test_deferred_suggested_on_park_edge():
    async def go():
        bus = Bus({"scene_name": "午休模式", "activation_id": "g1",
                   "deferred": [{"command": "seat.recline", "reason": "座椅放平到160度"}]})
        mirror = FakeMirror()
        w = await _watcher([], bus, mirror)
        w._parked = False                                    # 先在行车态
        await mirror.fire({"gear": "P", "battery": 80})       # 驻车变沿
        return bus.sent
    sent = _run(go())
    assert len(sent) == 1
    assert "停好车" in sent[0]["speech"] and "座椅" in sent[0]["speech"]
    assert sent[0]["card"]["buttons"][0]["send_text"] == "开启午休模式"
    assert "actions" not in sent[0], "补做也只能是建议，不能自己执行"


def test_no_deferred_no_suggest():
    async def go():
        bus = Bus({"scene_name": "午休模式", "deferred": []})
        mirror = FakeMirror()
        w = await _watcher([], bus, mirror)
        w._parked = False
        await mirror.fire({"gear": "P"})
        return bus.sent
    assert _run(go()) == []


def test_deferred_not_resuggested_while_staying_parked():
    """一直停着不该反复提醒——只在 gear→P 的变沿发一次。"""
    async def go():
        bus = Bus({"scene_name": "午休模式",
                   "deferred": [{"command": "seat.recline", "reason": "座椅放平"}]})
        mirror = FakeMirror()
        w = await _watcher([], bus, mirror)
        w._parked = False
        await mirror.fire({"gear": "P"})
        await mirror.fire({"gear": "P", "battery": 70})
        await mirror.fire({"gear": "P", "battery": 69})
        return bus.sent
    assert len(_run(go())) == 1


# ── env 摊平 ────────────────────────────────────────────────────────────────

def test_enrich_env_flattens_location():
    env = enrich_env({"location": {"city": "深圳", "name": "科技园"}, "battery": 50})
    assert env["location.city"] == "深圳" and env["battery"] == 50
    assert enrich_env({"location": None})["location"] is None      # 不炸


# ── recur=once：消费即熄（2026-07-14 评审修复：原实现 pop 后重算，once 变 daily）────

def test_once_trigger_fires_only_once():
    """next_fire_at 只装填未来时刻（新建触发器不补发今天已过的点），故先在过点前 poll
    一次装填 due，再在过点后验证触发/熄火。"""
    async def go():
        bus, mirror = Bus(), FakeMirror()
        s = Scene(user_id="u1", name="观星模式", actions=[{"command": "ambient_light.close"}],
                  triggers=[{"type": "time", "spec": {"at": "21:00", "recur": "once"}}])
        w = await _watcher([s], bus, mirror)
        arm = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)     # 北京 20:00，装填今晚 21:00
        n0 = await w.poll_once(arm)
        n1 = await w.poll_once(arm + timedelta(hours=2))            # 北京 22:00，过点触发
        w._fired.clear()                                            # 排除节流干扰，单测 once 语义
        n2 = await w.poll_once(arm + timedelta(days=1, hours=2))    # 次日同刻
        return n0, n1, n2, bus.sent
    n0, n1, n2, sent = _run(go())
    assert (n0, n1) == (0, 1)
    assert n2 == 0, "once 触发后必须熄火，不能滚成 daily"
    assert len(sent) == 1


def test_daily_trigger_rolls_to_next_day():
    async def go():
        bus, mirror = Bus(), FakeMirror()
        s = Scene(user_id="u1", name="午休模式", actions=[{"command": "volume.set"}],
                  triggers=[{"type": "time", "spec": {"at": "12:30", "recur": "daily"}}])
        w = await _watcher([s], bus, mirror)
        arm = datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc)      # 北京 12:00，装填今天 12:30
        n0 = await w.poll_once(arm)
        n1 = await w.poll_once(arm + timedelta(hours=1))            # 北京 13:00，触发
        w._fired.clear()
        w._scenes_at = 0.0                                          # 缓存过期，模拟次日
        # 装填-触发两拍模型：触发后 pop，下一次 poll 重装填明天 12:30（真实运行 poll 每 30s
        # 一次，两拍间隔可忽略）——这里补一拍装填，再到点验证
        await w.poll_once(arm + timedelta(hours=2))                 # 重装填明天 12:30
        n2 = await w.poll_once(arm + timedelta(days=1, hours=1))    # 次日 13:00，再触发
        return n0, n1, n2
    n0, n1, n2 = _run(go())
    assert (n0, n1, n2) == (0, 1, 1), "daily 每天都该触发"


# ── enabled 场景短缓存（2026-07-14 评审修复：行车中车速广播连续，不缓存=DB 查询风暴）──

def test_enabled_scenes_cached_within_window():
    async def go():
        bus, mirror = Bus(), FakeMirror()
        w = await _watcher([_low_battery_scene()], bus, mirror)
        calls = {"n": 0}
        orig = w._store.list

        async def counting(uid, **kw):
            calls["n"] += 1
            return await orig(uid, **kw)

        w._store.list = counting
        await mirror.fire({"speed_kmh": 41})       # 模拟行车中连续车速广播
        await mirror.fire({"speed_kmh": 42})
        await mirror.fire({"speed_kmh": 43})
        return calls["n"]
    assert _run(go()) == 1, "10s 窗口内的状态风暴只该打一次 DB"
