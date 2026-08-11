"""PollWorker 单测：查单推进 / 过期收口 / merchant_hosted / 续轮 / 回执推送。

mock provider 的 AUTOPAY 语义当测试旋钮：0=立即支付完成、-1=永不。
「崩溃续轮」测的是 worker 重建后从 store 轮询集接续——store 内存路径下持久性
由同一实例承担（Redis 跨进程持久由批 2 真栈 e2e 覆盖）。
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from payment_gateway.providers.base import PaymentChannelError
from payment_gateway.providers.mock import MockPaymentProvider
from payment_gateway.store import PaymentStore
from payment_gateway.worker import PollWorker, _next_interval


class SpyAudit:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _rec(*a, **kw):
            self.calls.append((name, a, kw))
        return _rec

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


class SpyNats:
    """扮演 NATS：request 成功 → publish_proactive 走 governed 档。"""

    def __init__(self):
        self.requests = []

    async def request(self, subject, data, timeout=None):
        self.requests.append((subject, json.loads(data.decode())))
        return object()

    async def publish(self, subject, data):   # fail-open 兜底口（本测试不该走到）
        self.requests.append((subject, json.loads(data.decode())))


@pytest.fixture()
def rig(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("PAYMENT_MOCK_AUTOPAY_S", "0")
    store = PaymentStore()
    providers = {"mock": MockPaymentProvider()}
    audit, nc = SpyAudit(), SpyNats()
    worker = PollWorker(store, providers, audit=audit, nc=nc)
    return store, providers, audit, nc, worker


def _pending_order(store, *, channel="alipay_qr", provider_key="mock",
                   expires_in=300.0, idem="idem-w1", scene="parking.pay",
                   external_order_ref=""):
    async def _make():
        order = await store.authorize(
            agent_id="parking-payment", user_id="u1", vehicle_id="v1",
            scene=scene, amount_cents=1500, currency="CNY", description="停车费",
            idempotency_key=idem, channel=channel, provider_key=provider_key,
            external_order_ref=external_order_ref)
        await store.mark_pending_pay(order.payment_id, qr_content="qr://x",
                                     expires_at=time.time() + expires_in)
        await store.schedule_poll(order.payment_id, 0)
        return order
    return asyncio.run(_make())


def test_paid_promotes_to_captured_with_audit_and_receipt(rig):
    store, _, audit, nc, worker = rig
    order = _pending_order(store)
    n = asyncio.run(worker.tick())
    assert n == 1
    final = asyncio.run(store.get(order.payment_id))
    assert final.status == "captured"
    assert final.trade_no == f"mocktrade_{order.payment_id}"
    assert audit.named("payment_captured")
    # 回执信封（§9.8）：user_contract 档 + payment|id 去重键 + mock 打 _prov
    subject, payload = nc.requests[0]
    assert subject == "agent.proactive.request"
    assert payload["type"] == "payment_result"
    assert payload["priority"] == "user_contract"
    assert payload["dedup_key"] == f"payment|{order.payment_id}"
    assert payload["user_id"] == "u1"
    assert "15.00" in payload["speech"]
    assert payload["ui_card"]["type"] == "payment_receipt"
    assert payload["ui_card"]["_prov"]["mode"] == "mock"


def test_waiting_reschedules_until_expiry(rig, monkeypatch):
    monkeypatch.setenv("PAYMENT_MOCK_AUTOPAY_S", "-1")   # 永不支付
    store, _, _, _, worker = rig
    order = _pending_order(store)
    asyncio.run(worker.tick())
    assert asyncio.run(store.get(order.payment_id)).status == "pending_pay"
    due_later = asyncio.run(store.due_polls(time.time() + 60))
    assert [o.payment_id for o in due_later] == [order.payment_id]   # 已重排下一轮


def test_expiry_closes_channel_and_marks_expired(rig, monkeypatch):
    monkeypatch.setenv("PAYMENT_MOCK_AUTOPAY_S", "-1")
    store, providers, _, nc, worker = rig
    order = _pending_order(store, expires_in=-1)          # 已过有效期
    asyncio.run(worker.tick())
    final = asyncio.run(store.get(order.payment_id))
    assert final.status == "expired"
    assert order.payment_id in providers["mock"]._closed  # 渠道关单被调
    assert nc.requests == []                              # 过期不发「支付成功」


def test_merchant_hosted_only_expires_never_captures(rig):
    store, providers, _, _, worker = rig
    live = _pending_order(store, channel="merchant_hosted", provider_key="merchant",
                          expires_in=600, idem="idem-m1", scene="mcd.order")
    dead = _pending_order(store, channel="merchant_hosted", provider_key="merchant",
                          expires_in=-1, idem="idem-m2", scene="mcd.order")
    asyncio.run(worker.tick())
    assert asyncio.run(store.get(live.payment_id)).status == "pending_pay"
    assert asyncio.run(store.get(dead.payment_id)).status == "expired"
    # merchant 永不查渠道：provider 集合里压根没有 "merchant" 键，查了就会炸——
    # 没炸本身就是「不查渠道」的证据；再钉一层：mock provider 未被问过这两单
    assert live.payment_id not in providers["mock"]._created


def test_worker_rebuild_resumes_from_poll_set(rig):
    """崩溃恢复：worker 重建（store 存续）→ 轮询集接续，单照常推进。"""
    store, providers, audit, nc, worker1 = rig
    order = _pending_order(store)
    del worker1
    worker2 = PollWorker(store, providers, audit=audit, nc=nc)
    asyncio.run(worker2.tick())
    assert asyncio.run(store.get(order.payment_id)).status == "captured"


def test_query_error_backs_off_not_drops(rig):
    store, providers, _, _, worker = rig

    class BoomProvider(MockPaymentProvider):
        async def query(self, payment_id):
            raise PaymentChannelError("网络抖动")

    providers["mock"] = BoomProvider()
    order = _pending_order(store)
    asyncio.run(worker.tick())
    assert asyncio.run(store.get(order.payment_id)).status == "pending_pay"
    still = asyncio.run(store.due_polls(time.time() + 30))
    assert [o.payment_id for o in still] == [order.payment_id]   # 退避重轮，不丢单


def test_terminal_order_in_poll_set_gets_unscheduled(rig):
    store, _, _, nc, worker = rig
    order = _pending_order(store)
    asyncio.run(store.mark_captured(order.payment_id))
    asyncio.run(store.schedule_poll(order.payment_id, 0))   # 脏轮询项
    asyncio.run(worker.tick())
    assert asyncio.run(store.due_polls(time.time())) == []
    assert nc.requests == []          # 已终态不重发回执


def test_next_interval_steps():
    from payment_gateway.store import PaymentOrder
    now = time.time()
    assert _next_interval(PaymentOrder(created_at=now - 5), now) == 3.0
    assert _next_interval(PaymentOrder(created_at=now - 45), now) == 5.0
    assert _next_interval(PaymentOrder(created_at=now - 120), now) == 8.0
