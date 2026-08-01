"""投递账本语义（M-C，内存分支；PG 分支由真栈覆盖）。

核心命题只有一句：**publish 成功 ≠ 用户收到**。这个文件钉死账本这一侧——
什么算「还没送到」、什么算「合同完成」、以及**不重播陈旧内容**。
"""
import pytest

from proactive.delivery_store import (
    DISPATCHED, EXPIRED, PENDING, PRESENTED, DeliveryStore, is_durable,
)


async def _store() -> DeliveryStore:
    s = DeliveryStore(dsn="")     # 强制内存分支
    await s.init()
    return s


def _env(text="到点了：吃药", user="u1", agent="reminder"):
    return {"type": "reminder_fired", "speech": text, "agent_id": agent,
            "user_id": user, "card": {"type": "reminder_card"}}


def test_only_contract_and_safety_tiers_are_durable():
    """advisory/ambient 本来就可以不说，为它们付持久化代价不划算。"""
    assert is_durable("user_contract") and is_durable("critical")
    assert not is_durable("advisory") and not is_durable("ambient")
    assert not is_durable("")


@pytest.mark.asyncio
async def test_persisted_message_is_undelivered_until_presented():
    s = await _store()
    did = await s.persist(_env(), priority="user_contract")
    assert did

    assert [r["delivery_id"] for r in await s.undelivered()] == [did]
    await s.mark_dispatched([did])
    # **发出去了仍然算没送到**——网关 write 成功不是用户看见了
    assert [r["delivery_id"] for r in await s.undelivered()] == [did]
    assert (await s.get(did))["state"] == DISPATCHED

    assert await s.mark_presented(did) is True
    assert await s.undelivered() == []
    assert (await s.get(did))["state"] == PRESENTED


@pytest.mark.asyncio
async def test_repeat_ack_is_idempotent_and_unknown_id_is_not():
    s = await _store()
    did = await s.persist(_env(), priority="user_contract")
    assert await s.mark_presented(did) is True
    assert await s.mark_presented(did) is True          # 迟到/重复 ACK 幂等成功
    assert await s.mark_presented("no-such-id") is False


@pytest.mark.asyncio
async def test_payload_round_trips_whole_envelope_for_restart():
    """存完整信封而不是 forwardable 产物——重启恢复要重建 Item。"""
    s = await _store()
    env = dict(_env(), priority="user_contract", ttl_ms=300000,
               conditions=[{"key": "speed_kmh", "op": "lt", "value": 100}])
    did = await s.persist(env, priority="user_contract", ttl_ms=300000,
                          dedup_key="k1")
    row = (await s.undelivered())[0]
    assert row["delivery_id"] == did and row["dedup_key"] == "k1"
    assert row["payload"]["conditions"] == env["conditions"]
    assert row["payload"]["speech"] == env["speech"]


@pytest.mark.asyncio
async def test_expired_messages_are_never_replayed():
    """过了 ttl 还没送到就作废——半小时后突然播一句「到点了」比不播更糟。"""
    s = await _store()
    did = await s.persist(_env(), priority="user_contract", ttl_ms=1)
    row = s._mem[did]
    row["expires_at"] = row["created_at"] - 1        # 强制过期
    assert await s.expire_stale() == 1
    assert await s.undelivered() == []
    assert (await s.get(did))["state"] == EXPIRED


@pytest.mark.asyncio
async def test_ttl_zero_never_expires():
    """`ttl_ms<=0` = 不过期。用户合同不该被一个默认过期时间悄悄吃掉——
    「到点提醒我」没说「五分钟内没看见就算了」。"""
    s = await _store()
    did = await s.persist(_env(), priority="user_contract", ttl_ms=0)
    assert await s.expire_stale() == 0
    assert [r["delivery_id"] for r in await s.undelivered()] == [did]


@pytest.mark.asyncio
async def test_final_states_leave_the_ledger():
    """账本不能只进不出：治理器判丢也要销账，否则 HMI 一连上就重投一堆已丢的。"""
    s = await _store()
    did = await s.persist(_env(), priority="user_contract")
    await s.mark_final(did, "dropped", "conditions_unmet")
    assert await s.undelivered() == []
    assert (await s.get(did))["reason"] == "conditions_unmet"


@pytest.mark.asyncio
async def test_presented_wins_over_a_late_drop():
    """迟到的销账不能让已完成的合同倒退。"""
    s = await _store()
    did = await s.persist(_env(), priority="user_contract")
    await s.mark_presented(did)
    await s.mark_final(did, "dropped", "late")
    assert (await s.get(did))["state"] == PRESENTED


@pytest.mark.asyncio
async def test_undelivered_is_owner_filterable_and_ordered_oldest_first():
    s = await _store()
    a = await s.persist(_env(text="A", user="u1"), priority="user_contract")
    b = await s.persist(_env(text="B", user="u2"), priority="user_contract")
    s._mem[a]["created_at"] = 100
    s._mem[b]["created_at"] = 200

    assert [r["delivery_id"] for r in await s.undelivered()] == [a, b]
    assert [r["delivery_id"] for r in await s.undelivered(user_id="u2")] == [b]


@pytest.mark.asyncio
async def test_owner_delete_removes_the_ledger_rows():
    """payload 里的话术与卡片摘要是个人数据：删记忆却留着投递账本是假删除。"""
    s = await _store()
    await s.persist(dict(_env(user="u1"), occupant_id="primary"), priority="user_contract")
    await s.persist(dict(_env(user="u1"), occupant_id="occ-2"), priority="user_contract")
    await s.persist(_env(user="u2"), priority="user_contract")

    assert await s.forget_owner("u1", "occ-2") == 1
    left = {(r["user_id"], r["occupant_id"]) for r in await s.undelivered()}
    assert left == {("u1", "primary"), ("u2", "primary")}

    assert await s.forget_owner("u1") == 1              # 空 occupant = 该 user 全部
    assert {r["user_id"] for r in await s.undelivered()} == {"u2"}


@pytest.mark.asyncio
async def test_owner_comes_from_the_envelope_owner_key():
    """owner 取 M-B 的 OwnerKey：reminder 触达信封用 `owner_occupant_id`。"""
    s = await _store()
    did = await s.persist(dict(_env(), owner_occupant_id="occ-2"),
                          priority="user_contract")
    assert (await s.get(did))["occupant_id"] == "occ-2"


@pytest.mark.asyncio
async def test_memory_fallback_reports_itself_as_not_durable():
    """无 PG 时必须诚实：不假装持久（调用方据此标 durable=false）。"""
    s = await _store()
    assert s.pg_ok is False
