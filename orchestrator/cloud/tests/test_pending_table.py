"""小容量挂起表（QA 卡 Q1-C，泓舟 2026-08-15 拍板做）。

单槽 `_suspend` 覆盖旧挂起的语义，注释里的理由是「确认条 UI 也只有一个」——
**这个语义在两个并行任务下就不成立了**（I-051 商户补槽跨域劫持、I-037① 无订单
却进退款确认）。改成 ≤3 条的挂起表 + LRU 淘汰最旧。

三条纪律：
1. **寻址靠 `operation_id`（Q1-B）**，不是靠猜——所以 B 必须先做。
2. **淘汰要有话术**。静默丢弃就是 B3 那条「认不出就用默认值」的确认版。
3. **不刷新 TTL**：挂起窗口以各自首次挂起时刻起算，后来者不给前者续命。
"""
from __future__ import annotations

import asyncio

from orchestrator.cloud.models import SessionState
from orchestrator.cloud.session import SessionStore, _PENDING_CAPACITY


def _state(op: str, phase: str = "wait_confirm", **kw) -> SessionState:
    return SessionState(phase=phase, owner_user_id="u1", operation_id=op, **kw)


def _save(store, op, **kw):
    return asyncio.run(store.save_pending("s", _state(op, **kw)))


# ─── 存储层 ───

def test_capacity_is_three():
    assert _PENDING_CAPACITY == 3


def test_multiple_pendings_coexist_and_are_addressable():
    store = SessionStore(redis_url="")
    for op in ("op-a", "op-b", "op-c"):
        ok, evicted = _save(store, op)
        assert ok is True and evicted is None

    for op in ("op-a", "op-b", "op-c"):
        got = asyncio.run(store.load("s", owner_user_id="u1", operation_id=op))
        assert got is not None and got.operation_id == op

    # 无寻址键 = 最近一条（语音兜底的既有语义）
    latest = asyncio.run(store.load("s", owner_user_id="u1"))
    assert latest.operation_id == "op-c"


def test_lru_evicts_the_oldest_and_reports_it():
    """淘汰必须**返回被淘汰的那条**——调用方要拿它说话，不能静默丢。"""
    store = SessionStore(redis_url="")
    for op in ("op-a", "op-b", "op-c"):
        _save(store, op)

    ok, evicted = _save(store, "op-d", pending_plan={"goal": "x"})
    assert ok is True
    assert evicted is not None and evicted.operation_id == "op-a"

    assert asyncio.run(store.load(
        "s", owner_user_id="u1", operation_id="op-a")) is None
    for op in ("op-b", "op-c", "op-d"):
        assert asyncio.run(store.load(
            "s", owner_user_id="u1", operation_id=op)) is not None


def test_resaving_same_operation_id_replaces_not_appends():
    store = SessionStore(redis_url="")
    _save(store, "op-a", pending_step_id="s1")
    ok, evicted = _save(store, "op-a", pending_step_id="s2")
    assert ok is True and evicted is None
    got = asyncio.run(store.load("s", owner_user_id="u1", operation_id="op-a"))
    assert got.pending_step_id == "s2"
    assert len(asyncio.run(store.load_all("s", owner_user_id="u1"))) == 1


def test_clear_one_leaves_the_others():
    store = SessionStore(redis_url="")
    for op in ("op-a", "op-b"):
        _save(store, op)

    assert asyncio.run(store.clear(
        "s", owner_user_id="u1", operation_id="op-a")) is True

    assert asyncio.run(store.load(
        "s", owner_user_id="u1", operation_id="op-a")) is None
    assert asyncio.run(store.load(
        "s", owner_user_id="u1", operation_id="op-b")) is not None


def test_clear_all_still_works():
    """privacy 删除与「整会话作废」仍需要一次清空的能力。"""
    store = SessionStore(redis_url="")
    for op in ("op-a", "op-b"):
        _save(store, op)
    assert asyncio.run(store.clear("s", owner_user_id="u1")) is True
    assert asyncio.run(store.load_all("s", owner_user_id="u1")) == []


def test_each_entry_keeps_its_own_deadline():
    """不刷新 TTL：先挂起的那条到点就该消失，不因后来者续命。

    单槽时代 TTL 挂在 Redis key 上，多槽下**再存一条就等于给旧条续命**——
    「挂起窗口以首次挂起时刻起算」那条纪律会被无声架空。所以截止时刻逐条存。
    """
    import time as _t
    store = SessionStore(redis_url="")
    _save(store, "op-old")
    entries = asyncio.run(store.load_all("s", owner_user_id="u1"))
    assert entries and entries[0].expires_at > 0     # 逐条有截止时刻
    entries[0].expires_at = _t.time() - 5            # 把它推到过去
    _save(store, "op-new")                           # 后来者不该救活它

    assert asyncio.run(store.load(
        "s", owner_user_id="u1", operation_id="op-old")) is None
    assert asyncio.run(store.load(
        "s", owner_user_id="u1", operation_id="op-new")) is not None


# ─── 编排层 ───

def test_second_suspension_does_not_kill_the_first():
    """I-051/I-037① 的机制修法：两个并行任务各自留一条挂起，先来的那条还能确认。

    单槽时代这里必然丢一条——「确认条 UI 只有一个」的语义在两个任务下不成立。
    """
    from orchestrator.cloud.tests.test_engine_confirm import (
        _make_engine, _req, _run)
    engine, spy, session = _make_engine()

    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    op2 = _run(engine, _req("再找一家川菜馆订明晚8点三位"))[-1]["operation_id"]
    assert op1 and op2 and op1 != op2

    ops = {s.operation_id for s in
           asyncio.run(session.load_all("sess-1", owner_user_id="u1"))}
    assert ops == {op1, op2}

    # 用寻址键确认**先来的那条**——单槽时代它已经不在了
    final = _run(engine, _req("确认", is_confirmation=True,
                              operation_id=op1))[-1]
    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert op1 in (final.get("closed_operation_ids") or [])
    left = asyncio.run(session.load_all("sess-1", owner_user_id="u1"))
    assert [s.operation_id for s in left] == [op2]      # 另一条原样活着


def test_eviction_is_spoken_not_silent():
    """淘汰要有话术。静默丢弃 = B3 那条「认不出就用默认值」的确认版。"""
    from orchestrator.cloud.tests.test_engine_confirm import (
        _make_engine, _req, _run)
    engine, _, session = _make_engine()

    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    for i in range(2):
        _run(engine, _req(f"再找第{i}家川菜馆订明晚8点三位"))
    final = _run(engine, _req("最后再找一家川菜馆订后天9点四位"))[-1]

    assert asyncio.run(session.load(
        "sess-1", owner_user_id="u1", operation_id=op1)) is None
    assert "过期" in (final.get("follow_up") or ""), "被淘汰的挂起必须说一句"
    assert op1 in (final.get("closed_operation_ids") or [])


def test_cancel_reports_the_closed_operation():
    from orchestrator.cloud.tests.test_engine_confirm import (
        _make_engine, _req, _run)
    engine, _, _ = _make_engine()
    op = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    final = _run(engine, _req("取消"))[-1]
    assert final.get("closed_operation_ids") == [op]


def test_interjection_closes_nothing():
    """对照：插话轮既不消费也不淘汰，closed 必须是空——否则 HMI 会把还活着的
    确认条撤掉（「没修过头」那一半的反向验证）。"""
    from orchestrator.cloud.tests.test_engine_confirm import (
        _make_engine_interject, _req, _run)
    engine, _, session = _make_engine_interject()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    final = _run(engine, _req("帮我看看附近有什么景点"))[-1]
    assert not final.get("closed_operation_ids")
    assert asyncio.run(session.load_all("sess-1", owner_user_id="u1"))


def test_legacy_single_dict_payload_still_loads():
    """上一版部署留下的单对象负载不能让整条会话读不出来（滚动升级窗口）。"""
    import json
    store = SessionStore(redis_url="")
    raw = json.dumps({"phase": "wait_confirm", "owner_user_id": "u1",
                      "pending_step_id": "s1"})
    assert store._decode(raw, "u1")[0].pending_step_id == "s1"
