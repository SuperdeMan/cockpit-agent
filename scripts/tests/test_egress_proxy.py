"""出口代理（deploy/egress-proxy.py）：只放行白名单主机的 443（CA2-17 S3）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def proxy(monkeypatch):
    monkeypatch.setenv("EGRESS_ALLOW", "mcp.mcd.cn,gwmcp.lkcoffee.com")
    spec = importlib.util.spec_from_file_location("egress_proxy_under_test", ROOT / "deploy" / "egress-proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("target,expected", [
    ("mcp.mcd.cn:443", ("mcp.mcd.cn", 443)),
    ("MCP.MCD.CN:443", ("mcp.mcd.cn", 443)),
    ("mcp.mcd.cn", ("mcp.mcd.cn", 443)),
    ("mcp.mcd.cn:8443", ("mcp.mcd.cn", 8443)),
    ("mcp.mcd.cn:abc", ("mcp.mcd.cn", -1)),
])
def test_connect_target_parsing(proxy, target, expected):
    assert proxy.parse_target(target) == expected


def test_only_allowlisted_hosts_on_443_pass(proxy):
    assert proxy.allowed(*proxy.parse_target("mcp.mcd.cn:443"))
    assert proxy.allowed(*proxy.parse_target("gwmcp.lkcoffee.com"))
    assert not proxy.allowed(*proxy.parse_target("mcp.mcd.cn:22"))
    assert not proxy.allowed(*proxy.parse_target("mcp.mcd.cn:8443"))
    assert not proxy.allowed(*proxy.parse_target("mcp.mcd.cn:abc"))
    assert not proxy.allowed(*proxy.parse_target("evil.example.cn:443"))
