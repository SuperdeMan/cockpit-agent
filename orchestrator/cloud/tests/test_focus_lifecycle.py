"""焦点生命周期拆分（评审 2026-09-19 F04 / W09）。

旧形态：整个 Focus 一个 Redis key、`_FOCUS_TTL=300`——沉默 5 分钟后活动路线、会话约束、
安全告警随「上个对象是空调」一起消失。拆成两层：**短时引用**（对象 / 属性 / 上个地点 /
上个城市 / 上一轮意图 / 最新候选视图）按 `focus_ts` 300s 过期；**活动状态**（候选台账按各组 ts、
活动路线、安全告警、会话约束、门店锚定）各按自己的时效活，key TTL 抬到 2h。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from types import SimpleNamespace

import orchestrator.cloud.context as ctxmod
import orchestrator.cloud.session as sessmod
from orchestrator.cloud.context import ContextManager, Focus, expire_short_term
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus
from orchestrator.cloud.session import SessionStore


def _full_focus(ts: float) -> Focus:
    return Focus(
        obj="空调", attr="温度", positions=["副驾"], last_poi="万象城",
        last_destination="万象城", last_city="深圳", last_intent="hvac.set",
        last_agent_id="vehicle", last_choices=["甲", "乙"], last_choice_purpose="list",
        candidate_sets=[{"source_intent": "nearby.search", "purpose": "list",
                         "ts": time.time(), "is_fallback": False, "label": "餐饮",
                         "items": [{"name": "甲"}, {"name": "乙"}]}],
        active_route={"destination": "广州塔", "ts": time.time()},
        safety_alert={"level": "critical", "signal": "机油灯", "ts": time.time()},
        session_constraints={"no_spicy": True},
        last_places=[{"name": "甲", "lng": 113.9, "lat": 22.5}],
        last_places_ts=time.time(),
        focus_ts=ts,
    )


def test_key_ttl_is_two_hours_now():
    assert sessmod._FOCUS_TTL == 7200
    assert ctxmod._FOCUS_SHORT_TTL_S == 300


def test_short_term_references_expire_after_five_minutes_of_silence():
    focus = expire_short_term(_full_focus(time.time() - 601), now=time.time())
    assert focus.obj == "" and focus.attr == "" and focus.positions == []
    assert focus.last_poi == "" and focus.last_destination == "" and focus.last_city == ""
    assert focus.last_intent == ""
    # 最新候选视图从台账重新派生：台账那组自己还活着（按它的 900s），序数指代就还有参照系
    assert focus.last_choices == ["甲", "乙"]


def test_the_choice_view_follows_the_ledgers_own_ttl():
    stale = _full_focus(time.time() - 1000)
    stale.candidate_sets[0]["ts"] = time.time() - 1000       # 台账那组也过了 900s
    focus = expire_short_term(stale, now=time.time())
    assert focus.last_choices == [] and focus.last_choice_purpose == ""


def test_active_state_survives_the_same_silence():
    focus = expire_short_term(_full_focus(time.time() - 601), now=time.time())
    assert focus.candidate_sets and focus.active_route["destination"] == "广州塔"
    assert focus.safety_alert["signal"] == "机油灯"
    assert focus.session_constraints == {"no_spicy": True}
    assert focus.last_places


def test_fresh_focus_keeps_everything():
    focus = expire_short_term(_full_focus(time.time() - 30), now=time.time())
    assert focus.obj == "空调" and focus.last_choices == ["甲", "乙"]


def test_legacy_focus_without_a_stamp_is_not_expired():
    focus = expire_short_term(_full_focus(0.0), now=time.time())
    assert focus.obj == "空调"


def test_load_applies_the_split_and_save_stamps_the_time():
    session = SessionStore(redis_url="")
    cm = ContextManager(SimpleNamespace(), session)
    plan = Plan(steps=[Step(id="s1", agent_id="vehicle", intent="hvac.set",
                            slots={"position": "副驾", "temperature": "26"})],
                raw_text="副驾空调26度，我不吃辣")
    asyncio.run(cm.update_focus("sess-l", plan, [StepResult(step_id="s1", status=StepStatus.OK)],
                                user_id="u1"))
    saved = asyncio.run(session.load_focus("sess-l", owner_user_id="u1"))
    assert saved["focus_ts"] > 0
    # 十分钟后再读：对象没了，约束还在
    saved["focus_ts"] = time.time() - 700
    asyncio.run(session.save_focus("sess-l", saved, owner_user_id="u1"))
    loaded = asyncio.run(cm._load_focus("sess-l", "u1"))
    assert loaded.obj == "" and loaded.session_constraints == {"no_spicy": True}


def test_after_silence_the_next_turn_still_relays_route_and_constraints():
    """「沉默十分钟再说继续刚才的行程」：路线与约束接力到下一轮，短时指代不接。"""
    session = SessionStore(redis_url="")
    cm = ContextManager(SimpleNamespace(), session)
    old = _full_focus(time.time() - 700)
    asyncio.run(session.save_focus("sess-r", asdict(old), owner_user_id="u1"))
    asyncio.run(cm.update_focus("sess-r", Plan(steps=[], raw_text="继续刚才的行程"), [],
                                user_id="u1"))
    focus = asyncio.run(cm._load_focus("sess-r", "u1"))
    assert focus is not None
    assert focus.active_route["destination"] == "广州塔"
    assert focus.session_constraints == {"no_spicy": True}
    assert focus.obj == "" and focus.last_intent == ""
