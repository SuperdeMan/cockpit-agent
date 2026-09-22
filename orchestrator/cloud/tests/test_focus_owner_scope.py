"""Focus 里**私有那一半**按乘员归属（评审二轮 R7，2026-09-22）。

历史与长期记忆早就按 `user_id + occupant_id` 读；Focus 只按 `user_id + session_id`。
同一辆车、同一账号、两位已识别乘员时，A 的「不吃辣」会成为 B 问「我今天说过不吃辣吗」的答案
——归属错了，而话术还说「您这次说过」。

本批只给**明确私有**的两格加归属：会话约束（`session_constraints`）与活动任务帧（`active_task`）。
共享车辆 / 路线状态（活动路线、安全告警、上一轮意图、候选台账）**刻意仍然共享**：
它们是车上所有人都看得见的同一块屏、同一条路（边界写在设计文档 §4）。
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from types import SimpleNamespace

from orchestrator.cloud.context import ContextManager, Focus
from orchestrator.cloud.models import Plan
from orchestrator.cloud.session import SessionStore


def _mgr():
    return ContextManager(SimpleNamespace(), SessionStore(redis_url=""))


def _plan(text):
    return Plan(steps=[], raw_text=text)


def _save(mgr, occupant, text):
    asyncio.run(mgr.update_focus("s1", _plan(text), [], user_id="u1",
                                 exchange_id=f"x-{occupant}", occupant_id=occupant))


def _constraints(mgr, occupant):
    focus = asyncio.run(mgr._load_focus("s1", "u1", occupant_id=occupant))
    return dict(getattr(focus, "session_constraints", None) or {}) if focus else {}


def test_a_second_occupants_constraint_is_not_read_back_as_the_firsts():
    mgr = _mgr()
    _save(mgr, "alice", "我不吃辣")
    assert _constraints(mgr, "alice") == {"no_spicy": True}
    assert _constraints(mgr, "bob") == {}, "B 不该继承 A 的口味"


def test_each_occupant_keeps_their_own_constraints_across_turns():
    mgr = _mgr()
    _save(mgr, "alice", "我不吃辣")
    _save(mgr, "bob", "我不想排队")
    assert _constraints(mgr, "alice") == {"no_spicy": True}
    assert _constraints(mgr, "bob") == {"no_queue": True}
    # A 回来改口：只改自己的
    _save(mgr, "alice", "今天想吃辣")
    assert _constraints(mgr, "alice") == {"no_spicy": False}
    assert _constraints(mgr, "bob") == {"no_queue": True}


def test_the_default_occupant_sees_what_it_wrote():
    """未识别乘员（`primary`）照旧连续——归属不确定时不改变今天的行为。"""
    mgr = _mgr()
    _save(mgr, "primary", "我不吃辣")
    assert _constraints(mgr, "primary") == {"no_spicy": True}
    assert _constraints(mgr, "") == {"no_spicy": True}        # 空 = primary


def test_legacy_flat_focus_belongs_to_primary_not_to_whoever_reads_it():
    """上一版写下的扁平 `session_constraints`（没有归属）：算 `primary` 的，别人读不到。"""
    store = SessionStore(redis_url="")
    legacy = asdict(Focus(session_constraints={"no_spicy": True}))
    legacy.pop("by_occupant", None)
    asyncio.run(store.save_focus("s1", legacy, owner_user_id="u1"))
    mgr = ContextManager(SimpleNamespace(), store)
    assert _constraints(mgr, "primary") == {"no_spicy": True}
    assert _constraints(mgr, "bob") == {}


def test_shared_vehicle_state_stays_shared():
    """共享那一半不按人分：B 说「取消导航」要能取消 A 发起的那条路线。"""
    mgr = _mgr()
    focus = Focus(active_route={"destination": "深圳湾公园", "ts": 1.0},
                  safety_alert={"level": "warn", "ts": 1.0},
                  session_constraints={"no_spicy": True})
    asyncio.run(mgr.session.save_focus("s1", asdict(focus), owner_user_id="u1"))
    seen = asyncio.run(mgr._load_focus("s1", "u1", occupant_id="bob"))
    assert (seen.active_route or {}).get("destination") == "深圳湾公园"
    assert (seen.safety_alert or {}).get("level") == "warn"
    assert not (seen.session_constraints or {}), "私有那一半仍然不跨人"


def test_an_active_task_frame_is_private_to_its_occupant():
    mgr = _mgr()
    store = mgr.session
    focus = Focus(active_task={"task_id": "t1", "intent": "nearby.order",
                               "slots": {"restaurant_name": "川菜·名店1"},
                               "ts": 9_999_999_999.0})
    asyncio.run(store.save_focus("s1", asdict(focus), owner_user_id="u1"))
    # 旧记录（没有归属）算 primary 的
    mine = asyncio.run(mgr._load_focus("s1", "u1", occupant_id="primary"))
    other = asyncio.run(mgr._load_focus("s1", "u1", occupant_id="bob"))
    assert (mine.active_task or {}).get("task_id") == "t1"
    assert not (other.active_task or {}), "B 的改口不该落到 A 正在办的那件事上"


# ── 端到端：A 说过的约束不会被 B 的回问念成「您这次说过」 ────────────────────

def test_the_constraint_recall_exit_answers_per_occupant():
    """engine 的确定性读出口（W18-b）读的是**说话人自己的**投影。"""
    from orchestrator.cloud.tests.test_engine_confirm import _make_engine, _run

    engine, spy, _session = _make_engine()

    def _req(text, occupant):
        return SimpleNamespace(
            text=text, session_id="sess-1", request_id=f"r-{occupant}",
            is_confirmation=False, operation_id="",
            meta={"occupant_id": occupant},
            context=SimpleNamespace(user_id="u1", vehicle_id="v1"))

    _run(engine, _req("我不吃辣", "alice"))
    mine = _run(engine, _req("我今天说过不吃辣吗", "alice"))[-1]
    theirs = _run(engine, _req("我今天说过不吃辣吗", "bob"))[-1]

    assert "不吃辣" in mine["speech"]
    assert "您这次说过" not in theirs["speech"], theirs["speech"]
