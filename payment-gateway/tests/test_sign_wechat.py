"""WechatProvider v3 签名/验签/证书解密锁——离线零外呼。

微信无公开沙箱（设计 §1），本文件是微信路径唯一的准入证据面：
报文构造、Authorization 头、应答验签（公钥模式 + 平台证书懒加载 + 未知序列号
重拉）、AES-256-GCM 证书解密，全部用自造密钥/证书锁死。
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import json
import re

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

from payment_gateway.providers.base import (CLOSED, PAID, WAITING,
                                            PaymentChannelError,
                                            PaymentProviderError)
from payment_gateway.providers.wechat import WechatProvider, _pem

APIV3_KEY = "0123456789abcdef0123456789abcdef"   # 32 字节
PUB_KEY_ID = "PUB_KEY_ID_0123456789"


def _rsa_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return key, priv, pub


@pytest.fixture(scope="module")
def mch_keys():
    return _rsa_pem()


@pytest.fixture(scope="module")
def platform_keys():
    return _rsa_pem()


def _provider(mch_keys, platform_keys, handler=None, *,
              public_key_mode=True) -> WechatProvider:
    _, mch_priv, _ = mch_keys
    _, _, plat_pub = platform_keys
    p = WechatProvider(
        mchid="1900000001", mch_serial="MCHSERIAL01",
        mch_private_key=mch_priv, apiv3_key=APIV3_KEY,
        app_id="wx0000000000000001",
        public_key=plat_pub if public_key_mode else "",
        public_key_id=PUB_KEY_ID if public_key_mode else "")
    if handler is not None:
        p._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return p


def _signed_headers(platform_keys, body: str, serial: str = PUB_KEY_ID) -> dict:
    """扮演微信：对 ts\\nnonce\\nbody\\n 用平台私钥签名。"""
    key, _, _ = platform_keys
    ts, nonce = "1723350000", "testnonce001"
    msg = f"{ts}\n{nonce}\n{body}\n"
    sig = base64.b64encode(
        key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
    return {"Wechatpay-Timestamp": ts, "Wechatpay-Nonce": nonce,
            "Wechatpay-Signature": sig, "Wechatpay-Serial": serial,
            "Content-Type": "application/json"}


# ── 请求签名 ────────────────────────────────────────────────────

def test_auth_header_format_and_signature(mch_keys, platform_keys):
    key, _, _ = mch_keys
    p = _provider(mch_keys, platform_keys)
    header = p._auth_header("POST", "/v3/pay/transactions/native", '{"a":1}')
    m = re.fullmatch(
        r'WECHATPAY2-SHA256-RSA2048 mchid="1900000001",nonce_str="([0-9a-f]{32})",'
        r'signature="([^"]+)",timestamp="(\d+)",serial_no="MCHSERIAL01"', header)
    assert m, header
    nonce, sig, ts = m.group(1), m.group(2), m.group(3)
    msg = f"POST\n/v3/pay/transactions/native\n{ts}\n{nonce}\n" + '{"a":1}\n'
    key.public_key().verify(base64.b64decode(sig), msg.encode(),
                            padding.PKCS1v15(), hashes.SHA256())


def test_missing_credentials_fail_fast(platform_keys):
    with pytest.raises(PaymentProviderError):
        WechatProvider(mchid="", mch_serial="", mch_private_key="",
                       apiv3_key="", app_id="")


def test_pem_wraps_bare_base64():
    assert _pem("QUJD" * 40, tag="PUBLIC KEY").startswith(
        b"-----BEGIN PUBLIC KEY-----")


# ── 四接口 + 应答验签（公钥模式）────────────────────────────────

def test_native_create_qr_and_body(mch_keys, platform_keys):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        seen["auth"] = request.headers.get("Authorization", "")
        body = '{"code_url":"weixin://wxpay/bizpayurl?pr=abc"}'
        return httpx.Response(200, text=body,
                              headers=_signed_headers(platform_keys, body))

    p = _provider(mch_keys, platform_keys, handler)
    qr = asyncio.run(p.create_qr("pay_w1", 1500, "停车费"))
    assert qr.qr_content == "weixin://wxpay/bizpayurl?pr=abc"
    assert seen["body"]["out_trade_no"] == "pay_w1"
    assert seen["body"]["amount"] == {"total": 1500, "currency": "CNY"}
    assert seen["body"]["notify_url"].startswith("https://")
    assert seen["auth"].startswith("WECHATPAY2-SHA256-RSA2048 ")


def test_response_bad_signature_rejected(mch_keys, platform_keys):
    def handler(request):
        body = '{"code_url":"weixin://x"}'
        headers = _signed_headers(platform_keys, body)
        headers["Wechatpay-Signature"] = base64.b64encode(b"forged").decode()
        return httpx.Response(200, text=body, headers=headers)
    p = _provider(mch_keys, platform_keys, handler)
    with pytest.raises(PaymentChannelError):
        asyncio.run(p.create_qr("pay_w2", 100, "x"))


@pytest.mark.parametrize("trade_state,expect", [
    ("NOTPAY", WAITING), ("USERPAYING", WAITING), ("SUCCESS", PAID),
    ("CLOSED", CLOSED), ("REVOKED", CLOSED), ("PAYERROR", CLOSED)])
def test_query_state_mapping(mch_keys, platform_keys, trade_state, expect):
    def handler(request):
        body = json.dumps({"trade_state": trade_state,
                           "transaction_id": "4200001",
                           "amount": {"total": 1500, "payer_total": 1500}})
        return httpx.Response(200, text=body,
                              headers=_signed_headers(platform_keys, body))
    p = _provider(mch_keys, platform_keys, handler)
    st = asyncio.run(p.query("pay_w3"))
    assert st.state == expect
    if expect == PAID:
        assert st.trade_no == "4200001" and st.paid_amount_cents == 1500


def test_query_order_not_exist_is_waiting(mch_keys, platform_keys):
    def handler(request):
        return httpx.Response(404, text='{"code":"ORDER_NOT_EXIST"}')
    p = _provider(mch_keys, platform_keys, handler)
    assert asyncio.run(p.query("pay_w4")).state == WAITING


def test_close_204_no_verify(mch_keys, platform_keys):
    """204 无 body：验签跳过（无内容可验），成功语义直接成立。"""
    def handler(request):
        return httpx.Response(204)
    p = _provider(mch_keys, platform_keys, handler)
    assert asyncio.run(p.close("pay_w5")) is True


def test_refund_roundtrip(mch_keys, platform_keys):
    def handler(request):
        body = json.dumps({"refund_id": "50300001", "status": "PROCESSING"})
        return httpx.Response(200, text=body,
                              headers=_signed_headers(platform_keys, body))
    p = _provider(mch_keys, platform_keys, handler)
    ok, refund_id = asyncio.run(p.refund("pay_w6", 1500, "用户退款"))
    assert ok and refund_id == "pay_w6_r1"


# ── 平台证书模式（懒加载 + AES-GCM 解密 + 未知序列号重拉）────────

def _make_cert_payload(platform_keys) -> tuple[str, str]:
    """自签 x509 证书 → APIv3 key AES-256-GCM 加密（与真渠道同构）。"""
    key, _, _ = platform_keys
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Tenpay-Test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    nonce, aad = "abcdef123456", "certificate"
    ciphertext = AESGCM(APIV3_KEY.encode()).encrypt(nonce.encode(), pem, aad.encode())
    body = json.dumps({"data": [{
        "serial_no": "PLATSERIAL01",
        "encrypt_certificate": {"algorithm": "AEAD_AES_256_GCM",
                                "nonce": nonce, "associated_data": aad,
                                "ciphertext": base64.b64encode(ciphertext).decode()},
    }]})
    return body, "PLATSERIAL01"


def test_platform_cert_lazy_fetch_and_verify(mch_keys, platform_keys):
    cert_body, serial = _make_cert_payload(platform_keys)
    calls = {"certs": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/certificates":
            calls["certs"] += 1
            return httpx.Response(200, text=cert_body)   # TOFU：无验签头
        body = json.dumps({"trade_state": "SUCCESS", "transaction_id": "t1",
                           "amount": {"payer_total": 100}})
        return httpx.Response(200, text=body,
                              headers=_signed_headers(platform_keys, body,
                                                      serial=serial))

    p = _provider(mch_keys, platform_keys, handler, public_key_mode=False)
    st = asyncio.run(p.query("pay_w7"))
    assert st.state == PAID
    assert calls["certs"] == 1          # 懒加载一次
    asyncio.run(p.query("pay_w7"))
    assert calls["certs"] == 1          # 缓存命中不重拉


def test_unknown_serial_triggers_refetch(mch_keys, platform_keys):
    cert_body, serial = _make_cert_payload(platform_keys)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/certificates":
            return httpx.Response(200, text=cert_body)
        body = '{"trade_state":"NOTPAY"}'
        return httpx.Response(200, text=body,
                              headers=_signed_headers(platform_keys, body,
                                                      serial=serial))

    p = _provider(mch_keys, platform_keys, handler, public_key_mode=False)
    p._platform_certs = {"STALE_SERIAL": object()}      # 轮换窗口：缓存里没有新序列号
    p._certs_fetched_at = 9e12                          # 未到 TTL 也要即时重拉
    st = asyncio.run(p.query("pay_w8"))
    assert st.state == WAITING
    assert serial in p._platform_certs
