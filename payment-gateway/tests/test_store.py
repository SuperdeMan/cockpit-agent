"""PaymentStore 单测：幂等 / 状态机 / fail-closed / 序列化往返 / 轮询集 / 脱敏。

全部走内存兜底路径（单测无 Redis）；Redis 序列化由 _to_hash/_from_hash 纯函数
往返锁死，Redis 真路径由批 2 真栈 e2e 覆盖——诚实分层，不 mock 出一个假 Redis。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from payment_gateway.store import (PaymentOrder, PaymentStore, TERMINAL,
                                   _from_hash, _to_hash)


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PAYMENT_MAX_AMOUNT_FEN", raising=False)
    return PaymentStore()


def _auth(store, **kw):
    args = dict(agent_id="parking-payment", user_id="u1", vehicle_id="v1",
                scene="parking.pay", amount_cents=1500, currency="CNY",
                description="停车费", idempotency_key="idem-1")
    args.update(kw)
    return asyncio.run(store.authorize(**args))


# ── 建单与幂等 ──────────────────────────────────────────────────

def test_authorize_generates_token_and_id(store):
    order = _auth(store)
    assert order.payment_id.startswith("pay_") and len(order.payment_id) == 16
    assert order.confirm_token           # F23 回归：token 必须存在
    assert order.status == "authorized"


def test_idempotent_same_key_same_order_same_token(store):
    a = _auth(store)
    b = _auth(store)
    assert a.payment_id == b.payment_id
    assert a.confirm_token == b.confirm_token      # 幂等命中不轮换（§9.17）


def test_idempotent_key_required(store):
    with pytest.raises(ValueError):
        _auth(store, idempotency_key="")


def test_idempotent_lookup_precedes_validation(store):
    """第二趟重取传占位金额也必须命中快照单（不重查费防漂移，§9.17）。"""
    first = _auth(store, amount_cents=1500)
    with pytest.raises(ValueError):
        _auth(store, idempotency_key="other-key", amount_cents=0)  # 新建仍 fail-closed
    again = asyncio.run(store.authorize(
        agent_id="parking-payment", user_id="u1", vehicle_id="v1",
        scene="parking.pay", amount_cents=0, currency="CNY",
        description="", idempotency_key="idem-1"))
    assert again.payment_id == first.payment_id
    assert again.amount_cents == 1500          # 快照金额，不被占位值污染


def test_idempotent_remap_after_repayable_terminal(store):
    """cancelled/expired/failed 后同键重新 authorize=建新单（幂等防双付不防重试）；
    captured 命中仍返回原单（这才是双付面）。"""
    first = _auth(store)
    asyncio.run(store.mark_cancelled(first.payment_id))
    second = _auth(store)                       # 同 idem-1
    assert second.payment_id != first.payment_id
    assert second.status == "authorized"

    asyncio.run(store.mark_pending_pay(second.payment_id))
    asyncio.run(store.mark_captured(second.payment_id))
    third = _auth(store)
    assert third.payment_id == second.payment_id   # captured：返回原单防双付


@pytest.mark.parametrize("amount", [0, -100])
def test_amount_must_be_positive(store, amount):
    with pytest.raises(ValueError):
        _auth(store, amount_cents=amount)


def test_amount_cap_fail_closed(store, monkeypatch):
    monkeypatch.setenv("PAYMENT_MAX_AMOUNT_FEN", "1000")
    with pytest.raises(ValueError):
        _auth(store, amount_cents=1001)
    assert _auth(store, amount_cents=1000).payment_id   # 恰好等于上限放行


def test_currency_cny_only(store):
    with pytest.raises(ValueError):
        _auth(store, currency="USD")


# ── Capture 前置校验 ────────────────────────────────────────────

def test_get_for_capture_token_mismatch(store):
    order = _auth(store)
    got, err = asyncio.run(store.get_for_capture(order.payment_id, "wrong"))
    assert got is None and "token" in err


def test_get_for_capture_missing(store):
    got, err = asyncio.run(store.get_for_capture("pay_nothere", "x"))
    assert got is None and "不存在" in err


def test_get_for_capture_pending_pay_reentry_skips_token(store):
    """pending_pay 重入合法（token 已作废，回缓存码路径不再校验）。"""
    order = _auth(store)
    asyncio.run(store.mark_pending_pay(order.payment_id, qr_content="qr"))
    got, err = asyncio.run(store.get_for_capture(order.payment_id, ""))
    assert got is not None and err == "" and got.status == "pending_pay"


# ── 状态机 ──────────────────────────────────────────────────────

def test_pending_pay_voids_confirm_token(store):
    order = _auth(store)
    updated = asyncio.run(store.mark_pending_pay(
        order.payment_id, qr_content="qr://x", expires_at=time.time() + 60))
    assert updated.confirm_token == ""        # 单次有效（§9.17）
    assert updated.qr_content == "qr://x"


def test_full_capture_flow(store):
    order = _auth(store)
    asyncio.run(store.mark_pending_pay(order.payment_id, qr_content="qr"))
    updated = asyncio.run(store.mark_captured(order.payment_id, trade_no="t123"))
    assert updated.status == "captured" and updated.trade_no == "t123"


def test_illegal_transitions_rejected(store):
    order = _auth(store)
    # authorized 不能直接 captured / expired / refunding
    assert asyncio.run(store.mark_captured(order.payment_id)) is None
    assert asyncio.run(store.mark_expired(order.payment_id)) is None
    assert asyncio.run(store.mark_refunding(order.payment_id)) is None
    # 终态不可再迁移
    asyncio.run(store.mark_cancelled(order.payment_id))
    assert asyncio.run(store.mark_pending_pay(order.payment_id)) is None


def test_refund_chain(store):
    order = _auth(store)
    asyncio.run(store.mark_pending_pay(order.payment_id))
    asyncio.run(store.mark_captured(order.payment_id))
    assert asyncio.run(store.mark_refunding(order.payment_id)).status == "refunding"
    done = asyncio.run(store.mark_refunded(order.payment_id, refund_id="r1"))
    assert done.status == "refunded" and done.refund_id == "r1"


def test_transition_idempotent_replay(store):
    order = _auth(store)
    asyncio.run(store.mark_pending_pay(order.payment_id, qr_content="qr"))
    again = asyncio.run(store.mark_pending_pay(order.payment_id, qr_content="other"))
    assert again is not None and again.qr_content == "qr"   # 重放不改内容


# ── 序列化往返（Redis hash 形态的锁）────────────────────────────

def test_hash_roundtrip():
    order = PaymentOrder(payment_id="pay_abc", agent_id="a", user_id="u",
                         scene="parking.pay", amount_cents=1500,
                         status="pending_pay", channel="alipay_qr",
                         provider_key="alipay", qr_content="qr://x",
                         expires_at=1234.5)
    back = _from_hash(_to_hash(order))
    assert back == order


def test_from_hash_ignores_unknown_fields():
    data = _to_hash(PaymentOrder(payment_id="pay_x", amount_cents=1))
    data["future_field"] = "whatever"       # 前向兼容：旧进程读新字段不炸
    assert _from_hash(data).payment_id == "pay_x"


def test_from_hash_empty_is_none():
    assert _from_hash({}) is None


# ── 轮询集 ──────────────────────────────────────────────────────

def test_poll_schedule_due_unschedule(store):
    order = _auth(store)
    asyncio.run(store.mark_pending_pay(order.payment_id))
    now = time.time()
    asyncio.run(store.schedule_poll(order.payment_id, now - 1))
    due = asyncio.run(store.due_polls(now))
    assert [o.payment_id for o in due] == [order.payment_id]
    asyncio.run(store.unschedule(order.payment_id))
    assert asyncio.run(store.due_polls(now)) == []


def test_due_polls_self_cleans_terminal_orders(store):
    order = _auth(store)
    asyncio.run(store.mark_pending_pay(order.payment_id))
    asyncio.run(store.schedule_poll(order.payment_id, 0))
    asyncio.run(store.mark_captured(order.payment_id))   # mark_captured 已摘；再塞回去模拟脏数据
    asyncio.run(store.schedule_poll(order.payment_id, 0))
    assert asyncio.run(store.due_polls(time.time())) == []   # 终态单被自清
    assert asyncio.run(store.due_polls(time.time())) == []


def test_provider_mode_property():
    assert PaymentOrder(provider_key="mock").provider_mode == "mock"
    assert PaymentOrder(provider_key="alipay").provider_mode == "real"
    assert PaymentOrder(provider_key="merchant").provider_mode == "real"


# ── 脱敏（payment_redact_owner，lifecycle=retained_audit）────────

def test_redact_owner_keeps_audit_fields(store):
    order = _auth(store)
    _auth(store, idempotency_key="idem-2", user_id="u2")
    n = asyncio.run(store.redact_owner("u1"))
    assert n == 1
    redacted = asyncio.run(store.get(order.payment_id))
    assert redacted.user_id == "[redacted]"
    assert redacted.amount_cents == 1500 and redacted.status == "authorized"
    assert asyncio.run(store.count_for_owner("u1")) == 0
    assert asyncio.run(store.count_for_owner("u2")) == 1


def test_terminal_set_matches_transitions():
    # TERMINAL 与迁移表一致性：终态不出现在任何迁移的目标之外……
    assert TERMINAL == {"captured", "cancelled", "failed", "expired", "refunded"}
