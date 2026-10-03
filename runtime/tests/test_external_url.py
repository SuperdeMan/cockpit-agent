"""CA2-17 S3：外部 URL 的一份判据（商户服务器地址、支付链接）。"""
from __future__ import annotations

import pytest

from runtime.external_url import https_url_host, normalize_hostname, pay_url_allowed


@pytest.mark.parametrize("url,host", [
    ("https://m.mcd.cn/mcp/scanToPay?x=1", "m.mcd.cn"),
    ("https://M.MCD.CN:443/pay", "m.mcd.cn"),
    ("https://gwmcp.lkcoffee.com/order/user/mcp", "gwmcp.lkcoffee.com"),
])
def test_clean_https_urls_yield_their_host(url, host):
    assert https_url_host(url) == host


@pytest.mark.parametrize("url", [
    "http://m.mcd.cn/pay",                 # 不是 https
    "https://m.mcd.cn:8443/pay",           # 非 443
    "https://user:pw@m.mcd.cn/pay",        # userinfo
    "https://evil.cn@m.mcd.cn/pay",
    " https://m.mcd.cn/pay",               # 首尾空白
    "https://m.mcd.cn/pa y",               # 内部空白
    "https://m.mcd.cn/pay\x00",            # 控制字符
    "https://127.0.0.1/pay",               # IP
    "https://[::1]/pay",
    "https://m.mcd.cn:abc/pay",            # 端口不是数字
    "", None, 42,
])
def test_anything_else_has_no_host(url):
    assert https_url_host(url) == ""


def test_pay_url_needs_exact_allowlisted_host():
    hosts = ["m.mcd.cn", " ", "open.lkcoffee.com."]
    assert pay_url_allowed("https://m.mcd.cn/mcp/scanToPay", hosts)
    assert pay_url_allowed("https://open.lkcoffee.com/pay", hosts)        # 白名单同样归一化
    assert not pay_url_allowed("https://evil.m.mcd.cn/pay", hosts)        # 子域不算
    assert not pay_url_allowed("https://m.mcd.cn.evil.cn/pay", hosts)
    assert not pay_url_allowed("https://m.mcd.cn:8443/pay", hosts)
    assert not pay_url_allowed("https://m.mcd.cn/pay", [])
    assert not pay_url_allowed("https://m.mcd.cn/pay", None)


@pytest.mark.parametrize("value", ["faß.de", "支付.example", "a..b", "-a.cn", "localhost", "1.2.3.4"])
def test_hostname_normalization_rejects_unsafe_forms(value):
    assert normalize_hostname(value) == ""


def test_bridge_and_gateway_share_the_single_implementation():
    from agents.mcp_bridge.src import admission
    assert admission.normalize_hostname is normalize_hostname
    assert admission.https_url_host is https_url_host
