"""AlipayProvider 签名/验签/报文构造锁——离线零外呼。

自造 RSA 密钥对扮演双方：商户私钥签请求（被测代码），「支付宝」用同一对私钥签
响应（测试代码扮演渠道），公钥互验。四接口经 httpx.MockTransport 回放，含
ACQ.TRADE_NOT_EXIST 的语义分支（未扫码 ≠ 错误）。
"""
from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from payment_gateway.providers.alipay import (AlipayProvider, _ensure_pem,
                                              _extract_node, _fen_to_yuan,
                                              _yuan_to_fen)
from payment_gateway.providers.base import (CLOSED, PAID, WAITING,
                                            PaymentChannelError,
                                            PaymentProviderError)


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return key, priv, pub


def _provider(keypair, handler=None) -> AlipayProvider:
    key, priv, pub = keypair
    p = AlipayProvider(app_id="2021000000000001", app_private_key=priv,
                       alipay_public_key=pub,
                       gateway="https://openapi-sandbox.test/gateway.do")
    if handler is not None:
        p._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return p


def _signed_response(keypair, method: str, node: dict) -> str:
    """扮演支付宝：对 node 的紧凑 JSON 原文签名（与真网关同构）。"""
    key, _, _ = keypair
    node_key = method.replace(".", "_") + "_response"
    node_text = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
    sig = base64.b64encode(
        key.sign(node_text.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
    return '{"%s":%s,"sign":"%s"}' % (node_key, node_text, sig)


# ── 纯函数 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cents,expect", [
    (1, "0.01"), (100, "1.00"), (1500, "15.00"), (12345, "123.45"), (20000, "200.00")])
def test_fen_to_yuan(cents, expect):
    assert _fen_to_yuan(cents) == expect


@pytest.mark.parametrize("s,expect", [
    ("15.00", 1500), ("0.01", 1), ("123.45", 12345), ("7", 700), ("", 0), ("x", 0)])
def test_yuan_to_fen(s, expect):
    assert _yuan_to_fen(s) == expect


def test_extract_node_nested_and_string_braces():
    text = '{"alipay_trade_query_response":{"msg":"a{b}c","sub":{"k":"v"}},"sign":"S"}'
    assert _extract_node(text, "alipay_trade_query_response") == \
        '{"msg":"a{b}c","sub":{"k":"v"}}'
    assert _extract_node(text, "not_there") == ""


def test_ensure_pem_wraps_bare_base64():
    pem = _ensure_pem("QUJD" * 30, private=False)
    assert pem.startswith(b"-----BEGIN PUBLIC KEY-----")
    assert b"\n" in pem


# ── 签名与验签 ──────────────────────────────────────────────────

def test_sign_order_and_roundtrip(keypair):
    """待签名串：除 sign 外非空参数按 key 升序 k=v 拼接；公钥可验。"""
    key, _, _ = keypair
    p = _provider(keypair)
    params = {"method": "alipay.trade.precreate", "app_id": "x",
              "biz_content": '{"a":1}', "charset": "utf-8", "empty": ""}
    sig = p._sign(params)
    unsigned = "app_id=x&biz_content={\"a\":1}&charset=utf-8&method=alipay.trade.precreate"
    key.public_key().verify(base64.b64decode(sig), unsigned.encode(),
                            padding.PKCS1v15(), hashes.SHA256())


def test_verify_response_accepts_signed_rejects_tampered(keypair):
    p = _provider(keypair)
    good = _signed_response(keypair, "alipay.trade.query",
                            {"code": "10000", "trade_status": "TRADE_SUCCESS"})
    assert p._verify_response(good, "alipay_trade_query_response")
    tampered = good.replace("TRADE_SUCCESS", "TRADE_CLOSED")
    assert not p._verify_response(tampered, "alipay_trade_query_response")


def test_missing_credentials_fail_fast():
    with pytest.raises(PaymentProviderError):
        AlipayProvider(app_id="", app_private_key="", alipay_public_key="")


# ── 四接口（MockTransport 回放）──────────────────────────────────

def test_precreate_success_builds_expected_request(keypair):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, text=_signed_response(
            keypair, "alipay.trade.precreate",
            {"code": "10000", "msg": "Success", "out_trade_no": "pay_x",
             "qr_code": "https://qr.alipay.com/bax0"}))

    p = _provider(keypair, handler)
    qr = asyncio.run(p.create_qr("pay_x", 1500, "停车费"))
    assert qr.qr_content == "https://qr.alipay.com/bax0"
    biz = json.loads(seen["params"]["biz_content"])
    assert biz == {"out_trade_no": "pay_x", "total_amount": "15.00",
                   "subject": "停车费"}
    assert seen["params"]["sign_type"] == "RSA2"
    assert seen["params"]["sign"]


def test_precreate_business_failure_raises(keypair):
    def handler(request):
        return httpx.Response(200, text=_signed_response(
            keypair, "alipay.trade.precreate",
            {"code": "40004", "sub_msg": "余额不足"}))
    p = _provider(keypair, handler)
    with pytest.raises(PaymentChannelError):
        asyncio.run(p.create_qr("pay_x", 100, "x"))


@pytest.mark.parametrize("trade_status,expect", [
    ("WAIT_BUYER_PAY", WAITING), ("TRADE_SUCCESS", PAID),
    ("TRADE_FINISHED", PAID), ("TRADE_CLOSED", CLOSED)])
def test_query_status_mapping(keypair, trade_status, expect):
    def handler(request):
        return httpx.Response(200, text=_signed_response(
            keypair, "alipay.trade.query",
            {"code": "10000", "trade_status": trade_status,
             "trade_no": "202608110001", "total_amount": "15.00"}))
    p = _provider(keypair, handler)
    st = asyncio.run(p.query("pay_x"))
    assert st.state == expect
    if expect == PAID:
        assert st.trade_no == "202608110001"
        assert st.paid_amount_cents == 1500


def test_query_trade_not_exist_is_waiting(keypair):
    """出码未扫时渠道侧无单——是 WAITING 不是错误（当面付语义）。"""
    def handler(request):
        return httpx.Response(200, text=_signed_response(
            keypair, "alipay.trade.query",
            {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST"}))
    p = _provider(keypair, handler)
    assert asyncio.run(p.query("pay_x")).state == WAITING


def test_close_treats_not_exist_as_success(keypair):
    def handler(request):
        return httpx.Response(200, text=_signed_response(
            keypair, "alipay.trade.close",
            {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST"}))
    p = _provider(keypair, handler)
    assert asyncio.run(p.close("pay_x")) is True


def test_refund_success_and_failure(keypair):
    def ok_handler(request):
        return httpx.Response(200, text=_signed_response(
            keypair, "alipay.trade.refund",
            {"code": "10000", "fund_change": "Y"}))
    p = _provider(keypair, ok_handler)
    ok, refund_id = asyncio.run(p.refund("pay_x", 1500, "用户退款"))
    assert ok and refund_id == "pay_x_r1"

    def fail_handler(request):
        return httpx.Response(200, text=_signed_response(
            keypair, "alipay.trade.refund",
            {"code": "40004", "sub_msg": "交易状态不合法"}))
    p2 = _provider(keypair, fail_handler)
    ok2, err = asyncio.run(p2.refund("pay_x", 1500, ""))
    assert not ok2 and "40004" in err


def test_unsigned_response_rejected(keypair):
    """渠道响应没有合法签名（如中间人改包/网关错误页）→ 拒绝，不吞。"""
    def handler(request):
        return httpx.Response(200, text='{"alipay_trade_query_response":'
                                        '{"code":"10000"},"sign":"forged"}')
    p = _provider(keypair, handler)
    with pytest.raises(PaymentChannelError):
        asyncio.run(p.query("pay_x"))


def test_gbk_response_with_chinese_verifies(keypair):
    """2026-08-11 沙箱真实联调抓到的 bug 的回归钉：支付宝网关响应是 **GBK**，
    签名覆盖的是 GBK 原始字节——中文（如 sub_msg「交易不存在」）一出现，按
    UTF-8 encode 验签必炸（全 ASCII 响应两种编码字节相同，单测此前全部侥幸绿）。"""
    key, _, _ = keypair
    node = ('{"code":"40004","sub_code":"ACQ.TRADE_NOT_EXIST",'
            '"sub_msg":"交易不存在"}')
    sig = base64.b64encode(
        key.sign(node.encode("gbk"), padding.PKCS1v15(), hashes.SHA256())).decode()
    body = ('{"alipay_trade_query_response":%s,"sign":"%s"}' % (node, sig)).encode("gbk")

    def handler(request):
        return httpx.Response(
            200, content=body,
            headers={"Content-Type": "text/html;charset=GBK"})

    p = _provider(keypair, handler)
    st = asyncio.run(p.query("pay_x"))
    assert st.state == WAITING          # 验签过 + TRADE_NOT_EXIST 归 WAITING
