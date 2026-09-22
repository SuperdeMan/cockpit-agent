"""挂起存储的读写三态（评审二轮 R8，2026-09-22）。

`load_all` 在「配了 Redis 但连不上」时返回 `[]`，与「表是空的」逐字相同：engine 因此
把一次故障说成「当前没有待确认的操作」，甚至对带 `operation_id` 的请求回 `pending_missing`
并下发 `closed_operation_ids`——把**可能还在**的挂起告诉客户端说没了。
`save_pending` 的 `False` 同样把「后端连不上」与「隐私删除写栅栏」混成一句
「正在清除你的数据」。

两侧都换成显式结果：读 `found / empty / unavailable`，写 `saved / unavailable / privacy_fenced`。
不能执行照旧不执行，但话术与结构化字段必须说的是**真的**那一件。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from orchestrator.cloud.models import SessionState
from orchestrator.cloud.session import (
    PENDING_EMPTY, PENDING_FOUND, PENDING_UNAVAILABLE, SAVE_FENCED, SAVE_OK,
    SAVE_UNAVAILABLE, SessionStore,
)

from .test_engine_confirm import _make_engine, _req, _run


def _state(op="op-1", phase="wait_confirm"):
    return SessionState(phase=phase, owner_user_id="u1", operation_id=op,
                        pending_step_id="s1", completed_results={},
                        pending_plan={"goal": "把全车门解锁", "raw_text": "把全车门解锁"})


class _DeadRedisStore(SessionStore):
    """配了 Redis、连不上：`_redis()` 恒 None（与生产上连接获取失败逐字同形）。"""

    def __init__(self):
        super().__init__(redis_url="redis://unreachable:6379/0")

    async def _redis(self):
        return None


# ── 存储层：三态 ──────────────────────────────────────────────────────────

def test_load_all_result_distinguishes_empty_from_unavailable():
    live = SessionStore(redis_url="")
    entries, state = asyncio.run(live.load_all_result("s1", owner_user_id="u1"))
    assert (entries, state) == ([], PENDING_EMPTY)

    asyncio.run(live.save("s1", _state()))
    entries, state = asyncio.run(live.load_all_result("s1", owner_user_id="u1"))
    assert state == PENDING_FOUND and [s.operation_id for s in entries] == ["op-1"]

    dead = _DeadRedisStore()
    entries, state = asyncio.run(dead.load_all_result("s1", owner_user_id="u1"))
    assert (entries, state) == ([], PENDING_UNAVAILABLE)


def test_load_all_keeps_its_list_shape_for_existing_callers():
    dead = _DeadRedisStore()
    assert asyncio.run(dead.load_all("s1", owner_user_id="u1")) == []


def test_save_pending_result_distinguishes_unavailable_from_a_privacy_fence():
    live = SessionStore(redis_url="")
    status, evicted = asyncio.run(live.save_pending_result("s1", _state()))
    assert status == SAVE_OK and evicted is None

    dead = _DeadRedisStore()
    status, _ = asyncio.run(dead.save_pending_result("s1", _state("op-2")))
    assert status == SAVE_UNAVAILABLE

    fenced = SessionStore(redis_url="")
    fenced._owner_fences["u1"] = time.time() + 300
    status, _ = asyncio.run(fenced.save_pending_result("s1", _state("op-3")))
    assert status == SAVE_FENCED


def test_save_pending_keeps_its_bool_shape_for_existing_callers():
    dead = _DeadRedisStore()
    saved, evicted = asyncio.run(dead.save_pending("s1", _state()))
    assert saved is False and evicted is None


# ── engine：读不到时说「读不到」，不说「没有」 ────────────────────────────

def _engine_with_dead_store():
    engine, spy, _session = _make_engine()
    engine.session = _DeadRedisStore()
    engine.context.session = engine.session
    return engine, spy


def test_an_addressed_confirm_while_the_store_is_unavailable_is_not_reported_closed():
    engine, spy = _engine_with_dead_store()
    final = _run(engine, _req("确认", is_confirmation=True, operation_id="op-1"))[-1]
    assert "已经不在" not in final["speech"]
    assert "暂时" in final["speech"] or "读不到" in final["speech"]
    assert not final.get("closed_operation_ids")     # 绝不替服务端宣布它没了
    assert not final.get("actions")
    assert spy.llm_plan_calls == 0


def test_a_bare_confirm_while_the_store_is_unavailable_says_so():
    engine, spy = _engine_with_dead_store()
    final = _run(engine, _req("确认"))[-1]
    assert "没有待确认的操作" not in final["speech"]
    assert "读不到" in final["speech"] or "暂时" in final["speech"]
    assert spy.llm_plan_calls == 0                   # 裸确认词仍不下交 planner


def test_asking_for_the_pending_list_while_the_store_is_unavailable_says_so():
    engine, _spy = _engine_with_dead_store()
    final = _run(engine, _req("现在还有待确认的操作吗"))[-1]
    assert "没有待确认" not in final["speech"]
    assert "查不到" in final["speech"] or "读不到" in final["speech"]


def test_an_ordinary_request_still_plans_while_the_store_is_unavailable():
    """fail-open：挂起表读不到不该让一次普通请求整轮不可用。"""
    engine, spy = _engine_with_dead_store()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    assert spy.llm_plan_calls == 1


# ── engine：存不下时说「存不下」，不说「正在清除你的数据」 ──────────────────

def test_a_suspend_that_cannot_be_saved_says_the_store_is_unavailable():
    engine, _spy = _engine_with_dead_store()
    final = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]
    assert "正在清除你的数据" not in final["speech"]
    assert "暂时" in final["speech"] or "存不下" in final["speech"]
    assert not final.get("need_confirm")
    assert not final.get("operation_id")


def test_a_privacy_fenced_suspend_keeps_todays_wording():
    engine, _spy, session = _make_engine()
    session._owner_fences["u1"] = time.time() + 300
    final = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]
    assert "正在清除你的数据" in final["speech"]
