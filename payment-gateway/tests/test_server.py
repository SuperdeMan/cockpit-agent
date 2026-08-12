"""PaymentGatewayServicer 契约测试。

钉死的回归：**Authorize 必须回传 confirm_token**（真实化前的 bug：store 生成了
token 但 server 漏填 → Capture 结构性不可达，全链路断在这一行）。
其余：scope 执行层校验（fail-open 留痕 / 硬拒）、PAYMENT_REAL_SCENES fail-closed、
merchant_hosted 单段 + pay_url 域名白名单、Capture 亮码与重入、NOT_FOUND、Refund。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import grpc
import pytest

from payment_gateway.providers.mock import MockPaymentProvider
from payment_gateway.server import PaymentGatewayServicer
from payment_gateway.store import PaymentStore
from security.audit import AuditLogger

from cockpit.payment.v1 import payment_pb2


class _Abort(Exception):
    def __init__(self, code, details):
        self.code, self.details = code, details


class FakeContext:
    def __init__(self, metadata: tuple = ()):
        self._md = metadata

    def invocation_metadata(self):
        return self._md

    async def abort(self, code, details=""):
        raise _Abort(code, details)


class SpyAudit:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _rec(*a, **kw):
            self.calls.append((name, a, kw))
        return _rec

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


class CapturingAudit(AuditLogger):
    def __init__(self):
        self.events = []

    def log(self, event):
        self.events.append(event)
        super().log(event)


@pytest.fixture()
def env(monkeypatch):
    for var in ("REDIS_URL", "PAYMENT_REAL_SCENES", "PAYMENT_EXTERNAL_PAY_HOSTS",
                "PAYMENT_MAX_AMOUNT_FEN", "PAYMENT_MOCK_AUTOPAY_S"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture()
def servicer(env):
    return PaymentGatewayServicer(
        store=PaymentStore(),
        providers={"mock": MockPaymentProvider()},
        audit=SpyAudit(),
    )


def _authorize_req(**kw):
    args = dict(agent_id="parking-payment", user_id="u1", vehicle_id="v1",
                scene="parking.pay", amount_cents=1500, currency="CNY",
                description="停车费", idempotency_key="idem-1")
    args.update(kw)
    return payment_pb2.AuthorizeRequest(**args)


def _do(coro):
    return asyncio.run(coro)


# ── Authorize ───────────────────────────────────────────────────

def test_authorize_returns_confirm_token_regression(servicer):
    """F23 回归钉死：token 生成了必须出网，否则 Capture 结构性不可达。"""
    resp = _do(servicer.Authorize(_authorize_req(), FakeContext()))
    assert resp.confirm_token != ""
    assert resp.require_confirm is True
    assert resp.status == payment_pb2.GetStatusResponse.AUTHORIZED
    assert resp.provider_mode == "mock"
    assert "15.00" in resp.confirm_prompt


def test_authorize_idempotent_same_token(servicer):
    a = _do(servicer.Authorize(_authorize_req(), FakeContext()))
    b = _do(servicer.Authorize(_authorize_req(), FakeContext()))
    assert (a.payment_id, a.confirm_token) == (b.payment_id, b.confirm_token)


def test_authorize_amount_cap_aborts(servicer, env):
    env.setenv("PAYMENT_MAX_AMOUNT_FEN", "1000")
    with pytest.raises(_Abort) as e:
        _do(servicer.Authorize(_authorize_req(amount_cents=99999), FakeContext()))
    assert e.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_authorize_scope_fail_open_when_metadata_absent(servicer):
    _do(servicer.Authorize(_authorize_req(), FakeContext()))
    assert servicer._audit.named("fail_open_scopes")     # PoC fail-open 必留痕


def test_authorize_scope_denied_when_scope_missing(servicer):
    ctx = FakeContext(metadata=(("x-granted-scopes", "vehicle.control"),))
    with pytest.raises(_Abort) as e:
        _do(servicer.Authorize(_authorize_req(), ctx))
    assert e.value.code == grpc.StatusCode.PERMISSION_DENIED
    assert servicer._audit.named("permission_denied")


def test_authorize_scope_granted_passes(servicer):
    ctx = FakeContext(metadata=(("x-granted-scopes", "payment.invoke,media.control"),))
    resp = _do(servicer.Authorize(_authorize_req(), ctx))
    assert resp.payment_id
    assert not servicer._audit.named("fail_open_scopes")


def test_real_scene_whitelist_defaults_to_mock(servicer):
    """PAYMENT_REAL_SCENES 空 → 即使请求点名支付宝也强制 mock（fail-closed 2.7）。"""
    resp = _do(servicer.Authorize(
        _authorize_req(channel=payment_pb2.ALIPAY_QR), FakeContext()))
    order = _do(servicer.store.get(resp.payment_id))
    assert order.provider_key == "mock"
    assert resp.provider_mode == "mock"


def test_real_scene_without_configured_channel_aborts(servicer, env):
    """场景在白名单但渠道未配置 → fail-closed 拒绝，不静默换渠道。"""
    env.setenv("PAYMENT_REAL_SCENES", "parking.pay")
    with pytest.raises(_Abort) as e:
        _do(servicer.Authorize(
            _authorize_req(channel=payment_pb2.ALIPAY_QR), FakeContext()))
    assert e.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_audit_payment_invoked_on_authorize(servicer):
    _do(servicer.Authorize(_authorize_req(), FakeContext()))
    assert servicer._audit.named("payment_invoked")


# ── merchant_hosted 单段 ────────────────────────────────────────

def test_merchant_hosted_registers_pending_pay(servicer, env):
    env.setenv("PAYMENT_EXTERNAL_PAY_HOSTS", "m.mcd.cn")
    resp = _do(servicer.Authorize(_authorize_req(
        scene="mcd.order", channel=payment_pb2.MERCHANT_HOSTED,
        external_pay_url="https://m.mcd.cn/mcp/scanToPay?orderId=1",
        external_order_ref="mcd-1", idempotency_key="idem-m1"), FakeContext()))
    assert resp.status == payment_pb2.GetStatusResponse.PENDING_PAY
    assert resp.require_confirm is False      # 确认已由 MCP 写工具闸完成
    order = _do(servicer.store.get(resp.payment_id))
    assert order.channel == "merchant_hosted"
    assert order.qr_content.startswith("https://m.mcd.cn/")
    assert order.expires_at > time.time()


@pytest.mark.parametrize("bad_url", [
    "https://evil.example.com/pay?x=1",          # 域名不在白名单
    "http://m.mcd.cn/pay",                        # 非 https
    "",                                           # 缺链接
])
def test_merchant_hosted_pay_url_gate(servicer, env, bad_url):
    env.setenv("PAYMENT_EXTERNAL_PAY_HOSTS", "m.mcd.cn")
    with pytest.raises(_Abort):
        _do(servicer.Authorize(_authorize_req(
            scene="mcd.order", channel=payment_pb2.MERCHANT_HOSTED,
            external_pay_url=bad_url, idempotency_key="idem-m2"), FakeContext()))


def test_merchant_hosted_empty_whitelist_rejects_all(servicer):
    with pytest.raises(_Abort) as e:
        _do(servicer.Authorize(_authorize_req(
            scene="mcd.order", channel=payment_pb2.MERCHANT_HOSTED,
            external_pay_url="https://m.mcd.cn/pay",
            idempotency_key="idem-m3"), FakeContext()))
    assert e.value.code == grpc.StatusCode.PERMISSION_DENIED
    assert servicer._audit.named("pay_url_denied")


def test_pay_url_denied_audit_redacts_url_credentials_and_location(env, caplog):
    env.setenv("PAYMENT_EXTERNAL_PAY_HOSTS", "m.mcd.cn")
    audit = CapturingAudit()
    service = PaymentGatewayServicer(
        store=PaymentStore(),
        providers={"mock": MockPaymentProvider()},
        audit=audit,
    )
    denied_url = (
        "https://audit-user:audit-password@EVIL.EXAMPLE.COM.:443/"
        "private/checkout?access_token=query-secret#fragment-secret"
    )

    with caplog.at_level(logging.WARNING, logger="security.audit"):
        with pytest.raises(_Abort):
            _do(service.Authorize(_authorize_req(
                scene="mcd.order", channel=payment_pb2.MERCHANT_HOSTED,
                external_pay_url=denied_url,
                idempotency_key="idem-audit-redaction"), FakeContext()))

    assert len(audit.events) == 2  # scope fail-open + pay_url_denied
    event = audit.events[-1]
    assert event.event == "pay_url_denied"
    assert event.extra == {
        "url_host": "evil.example.com",
        "url_sha256": hashlib.sha256(denied_url.encode("utf-8")).hexdigest(),
        "url_length": len(denied_url),
    }
    serialized = event.to_json()
    for secret in (
        denied_url,
        "audit-user",
        "audit-password",
        "/private/checkout",
        "query-secret",
        "fragment-secret",
    ):
        assert secret not in serialized
        assert secret not in caplog.text


def test_pay_url_denied_audit_marks_unparseable_url_invalid(env, caplog):
    env.setenv("PAYMENT_EXTERNAL_PAY_HOSTS", "m.mcd.cn")
    audit = CapturingAudit()
    service = PaymentGatewayServicer(
        store=PaymentStore(),
        providers={"mock": MockPaymentProvider()},
        audit=audit,
    )
    denied_url = "https://[broken/private?access_token=parse-secret"

    with caplog.at_level(logging.WARNING, logger="security.audit"):
        with pytest.raises(_Abort):
            _do(service.Authorize(_authorize_req(
                scene="mcd.order", channel=payment_pb2.MERCHANT_HOSTED,
                external_pay_url=denied_url,
                idempotency_key="idem-audit-invalid"), FakeContext()))

    event = audit.events[-1]
    assert event.event == "pay_url_denied"
    assert event.extra == {
        "url_host": "invalid",
        "url_sha256": hashlib.sha256(denied_url.encode("utf-8")).hexdigest(),
        "url_length": len(denied_url),
    }
    assert denied_url not in event.to_json()
    assert denied_url not in caplog.text
    assert "parse-secret" not in caplog.text


# ── Capture ─────────────────────────────────────────────────────

def _authorized(servicer):
    return _do(servicer.Authorize(_authorize_req(), FakeContext()))


def test_capture_lights_qr(servicer):
    auth = _authorized(servicer)
    resp = _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id=auth.payment_id, confirm_token=auth.confirm_token),
        FakeContext()))
    assert resp.ok and resp.qr_content == f"mockpay://{auth.payment_id}"
    assert resp.expires_at_ms > time.time() * 1000
    assert resp.qr_svg.startswith("data:image/svg+xml;base64,")   # HMI 零依赖直渲
    order = _do(servicer.store.get(auth.payment_id))
    assert order.status == "pending_pay"
    assert order.confirm_token == ""          # 单次有效


def test_authorize_returns_snapshot_amount(servicer):
    resp = _do(servicer.Authorize(_authorize_req(), FakeContext()))
    assert resp.amount_cents == 1500          # 幂等重取方（第二趟）的金额来源


def test_capture_wrong_token_rejected(servicer):
    auth = _authorized(servicer)
    resp = _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id=auth.payment_id, confirm_token="forged"), FakeContext()))
    assert not resp.ok and "token" in resp.error


def test_capture_reentry_returns_cached_qr(servicer):
    auth = _authorized(servicer)
    first = _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id=auth.payment_id, confirm_token=auth.confirm_token),
        FakeContext()))
    again = _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id=auth.payment_id, confirm_token=auth.confirm_token),
        FakeContext()))
    assert again.ok and again.qr_content == first.qr_content


def test_capture_missing_order(servicer):
    resp = _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id="pay_none", confirm_token="x"), FakeContext()))
    assert not resp.ok


# ── Cancel / GetStatus / Refund ─────────────────────────────────

def test_cancel_authorized(servicer):
    auth = _authorized(servicer)
    resp = _do(servicer.Cancel(payment_pb2.CancelRequest(
        payment_id=auth.payment_id), FakeContext()))
    assert resp.ok
    order = _do(servicer.store.get(auth.payment_id))
    assert order.status == "cancelled"


def test_get_status_not_found_aborts(servicer):
    with pytest.raises(_Abort) as e:
        _do(servicer.GetStatus(payment_pb2.GetStatusRequest(
            payment_id="pay_missing"), FakeContext()))
    assert e.value.code == grpc.StatusCode.NOT_FOUND


def test_get_status_maps_new_states(servicer):
    auth = _authorized(servicer)
    _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id=auth.payment_id, confirm_token=auth.confirm_token),
        FakeContext()))
    resp = _do(servicer.GetStatus(payment_pb2.GetStatusRequest(
        payment_id=auth.payment_id), FakeContext()))
    assert resp.status == payment_pb2.GetStatusResponse.PENDING_PAY
    assert resp.channel == payment_pb2.ALIPAY_QR   # 默认渠道标注（执行仍是 mock）


def test_refund_only_after_captured(servicer):
    auth = _authorized(servicer)
    resp = _do(servicer.Refund(payment_pb2.RefundRequest(
        payment_id=auth.payment_id), FakeContext()))
    assert not resp.ok

    _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id=auth.payment_id, confirm_token=auth.confirm_token),
        FakeContext()))
    _do(servicer.store.mark_captured(auth.payment_id, trade_no="t1"))
    ok = _do(servicer.Refund(payment_pb2.RefundRequest(
        payment_id=auth.payment_id), FakeContext()))
    assert ok.ok and ok.refund_id == f"{auth.payment_id}_r1"
    order = _do(servicer.store.get(auth.payment_id))
    assert order.status == "refunded"
    assert servicer._audit.named("payment_refunded")


def test_refund_partial_rejected(servicer):
    auth = _authorized(servicer)
    _do(servicer.Capture(payment_pb2.CaptureRequest(
        payment_id=auth.payment_id, confirm_token=auth.confirm_token),
        FakeContext()))
    _do(servicer.store.mark_captured(auth.payment_id))
    resp = _do(servicer.Refund(payment_pb2.RefundRequest(
        payment_id=auth.payment_id, amount_cents=100), FakeContext()))
    assert not resp.ok and "全额" in resp.error
