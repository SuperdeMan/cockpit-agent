"""Verify-Repair 后台对账单测。

要害是**代际护栏 + 单飞**（v2.1 修正③）：SCENE_ACTIVE 是单槽、Verify 是几秒后的异步任务，
旧 task 醒来时场景可能已被新激活覆盖或已退出——直接读单槽会给新场景错账、对已退出的场景假警。
"""
import asyncio
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.scene_orchestrator.src.verify import VerifyManager

_IDS = ("s1", "u1", "v1")


class FakeMirror:
    def __init__(self, state=None):
        self._state = state or {}

    def snapshot(self):
        return dict(self._state)


class Bus:
    """收 proactive 消息 + 冒充 SCENE_ACTIVE 单槽。"""

    def __init__(self, active=None):
        self.sent: list[dict] = []
        self.active = active or {}

    async def publish(self, payload):
        self.sent.append(payload)

    async def load(self, ids):
        return dict(self.active)

    async def save(self, ids, v):
        self.active = dict(v)


def _mgr(mirror, bus, wait=0.01):
    return VerifyManager(mirror, bus.publish, bus.load, bus.save, wait_s=wait)


def _act(cmd, params=None, **kw):
    a = {"type": "vehicle.control", "command": cmd, "params": params or {}}
    a.update(kw)
    return a


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0.01)


def _run(coro):
    return asyncio.run(coro)


# ── 对账 ────────────────────────────────────────────────────────────────────

def test_all_satisfied_stays_silent():
    """全达成 → 不打扰（HMI 已有执行反馈）。"""
    async def go():
        bus = Bus({"activation_id": "g1"})
        m = _mgr(FakeMirror({"hvac_temp": 22}), bus)
        m.schedule(_IDS, "钓鱼模式", "g1", [_act("hvac.set", {"temperature": "22"})])
        await _settle()
        return bus.sent
    assert _run(go()) == []


def test_unmet_action_reported_honestly():
    """被 VAL 安全门控拒掉的动作必须诚实汇报——这正是「失败对用户静默」的对症解。"""
    async def go():
        bus = Bus({"activation_id": "g1"})
        m = _mgr(FakeMirror({"seat_recline": 90, "hvac_temp": 22}), bus)
        m.schedule(_IDS, "午休模式", "g1",
                   [_act("seat.recline", {"angle": "160"}),
                    _act("hvac.set", {"temperature": "22"})])
        await _settle()
        return bus.sent
    sent = _run(go())
    assert len(sent) == 1
    assert sent[0]["type"] == "scene_verify"
    assert "座椅" in sent[0]["speech"] and "没有生效" in sent[0]["speech"]
    assert "行车" not in sent[0]["speech"], "别猜原因——可能是低电量/儿童锁"
    assert sent[0]["card"]["type"] == "scene_card"


def test_report_is_merged_not_spammed():
    """每次激活至多一条汇报，不逐条轰炸。"""
    async def go():
        bus = Bus({"activation_id": "g1"})
        m = _mgr(FakeMirror({"seat_recline": 90, "hvac_temp": 30, "volume": 50}), bus)
        m.schedule(_IDS, "午休模式", "g1",
                   [_act("seat.recline", {"angle": "160"}),
                    _act("hvac.set", {"temperature": "22"}),
                    _act("volume.set", {"level": "0"})])
        await _settle()
        return bus.sent
    sent = _run(go())
    assert len(sent) == 1 and len(sent[0]["card"]["actions_preview"]) == 3


def test_empty_mirror_is_silent():
    """镜像没数据 = 无法验证（≠ 失败）→ 静默。fail-open 铁律，绝不假警。"""
    async def go():
        bus = Bus({"activation_id": "g1"})
        m = _mgr(FakeMirror({}), bus)
        m.schedule(_IDS, "午休模式", "g1", [_act("seat.recline", {"angle": "160"})])
        await _settle()
        return bus.sent
    assert _run(go()) == []


def test_no_checkable_action_no_task():
    """一条可对账的动作都没有 → 干脆不起 task。"""
    async def go():
        bus = Bus({"activation_id": "g1"})
        m = _mgr(FakeMirror({"x": 1}), bus)
        m.schedule(_IDS, "空场景", "g1", [{"type": "navigate", "payload": {"destination": "家"}}])
        assert not m._tasks
        await _settle()
        return bus.sent
    assert _run(go()) == []


# ── 代际护栏 + 单飞（v2.1 修正③）───────────────────────────────────────────

def test_stale_generation_is_dropped():
    """旧 task 醒来发现单槽已是新场景 → 静默放弃（否则会拿旧清单给新场景错账）。"""
    async def go():
        bus = Bus({"activation_id": "g2"})            # 单槽已被新激活覆盖
        m = _mgr(FakeMirror({"seat_recline": 90}), bus)
        m.schedule(_IDS, "午休模式", "g1", [_act("seat.recline", {"angle": "160"})])
        await _settle()
        return bus.sent
    assert _run(go()) == [], "代际不匹配必须静默放弃"


def test_deactivate_cancels_inflight_verify():
    """退出场景 → 掐掉在飞对账，不给已退出的场景发假警。"""
    async def go():
        bus = Bus({"activation_id": "g1"})
        m = _mgr(FakeMirror({"seat_recline": 90}), bus, wait=0.05)
        m.schedule(_IDS, "午休模式", "g1", [_act("seat.recline", {"angle": "160"})])
        m.cancel("u1")
        await _settle()
        return bus.sent
    assert _run(go()) == []


def test_new_activation_cancels_old_task():
    """同 user 新激活先 cancel 旧 task（单飞，与代际校验双保险）。"""
    async def go():
        bus = Bus({"activation_id": "g2"})
        m = _mgr(FakeMirror({"seat_recline": 90, "volume": 50}), bus, wait=0.05)
        m.schedule(_IDS, "午休模式", "g1", [_act("seat.recline", {"angle": "160"})])
        m.schedule(_IDS, "钓鱼模式", "g2", [_act("volume.set", {"level": "0"})])
        await _settle()
        return bus.sent
    sent = _run(go())
    assert len(sent) == 1 and "钓鱼模式" in sent[0]["speech"]


# ── on_fail 三路处置 ────────────────────────────────────────────────────────

def test_retry_suggest_offers_button():
    async def go():
        bus = Bus({"activation_id": "g1"})
        m = _mgr(FakeMirror({"seat_recline": 90}), bus)
        m.schedule(_IDS, "午休模式", "g1",
                   [_act("seat.recline", {"angle": "160"}, on_fail="retry_suggest")])
        await _settle()
        return bus.sent
    sent = _run(go())
    btns = sent[0]["card"]["buttons"]
    assert btns and btns[0]["send_text"] == "开启午休模式"      # 回发原话走正常语音链路
    assert "再试一次" in sent[0]["speech"]


def test_defer_p_writes_deferred_queue():
    """defer_p → 挂驻车补做队列（P3 消费）；写前再校验代际。"""
    async def go():
        bus = Bus({"activation_id": "g1", "deferred": []})
        m = _mgr(FakeMirror({"seat_recline": 90}), bus)
        m.schedule(_IDS, "午休模式", "g1",
                   [_act("seat.recline", {"angle": "160"}, on_fail="defer_p")])
        await _settle()
        return bus
    bus = _run(go())
    assert len(bus.active["deferred"]) == 1
    assert bus.active["deferred"][0]["command"] == "seat.recline"
    assert "停好车" in bus.sent[0]["speech"]


def test_defer_p_not_written_on_stale_generation():
    """等待期间用户退出/换了场景 → deferred 不能写进去（脏数据）。"""
    async def go():
        bus = Bus({"activation_id": "g1", "deferred": []})
        m = _mgr(FakeMirror({"seat_recline": 90}), bus)
        m.schedule(_IDS, "午休模式", "g1",
                   [_act("seat.recline", {"angle": "160"}, on_fail="defer_p")])
        await asyncio.sleep(0.005)
        bus.active = {"activation_id": "g9", "deferred": []}     # 代际变了
        await _settle()
        return bus
    bus = _run(go())
    assert bus.active["deferred"] == []
    assert bus.sent == [], "代际不匹配连汇报都不该发"
