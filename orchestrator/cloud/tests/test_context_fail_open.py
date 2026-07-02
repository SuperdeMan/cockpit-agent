"""build_context fail-open / fail-closed 开关（R2.2 权限单轨化）。

无 granted_scopes 时：默认（PERMISSIONS_FAIL_OPEN 未设/on）走 PoC 全开兜底；
翻 false 走 fail-closed（granted 留空，仅无权限 Agent 可达）。显式 scope 始终被尊重。
"""
from types import SimpleNamespace

from orchestrator.cloud.context import build_context, _POC_DEFAULT_SCOPES


def _req(granted_scopes: str = ""):
    return SimpleNamespace(
        request_id="r1", session_id="s1", is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
        meta={"granted_scopes": granted_scopes} if granted_scopes else {},
    )


def test_fail_open_default_grants_poc_scopes(monkeypatch):
    monkeypatch.delenv("PERMISSIONS_FAIL_OPEN", raising=False)   # 默认 = 开
    ctx = build_context(_req())
    assert ctx.granted_permissions == list(_POC_DEFAULT_SCOPES)


def test_fail_closed_grants_nothing(monkeypatch):
    monkeypatch.setenv("PERMISSIONS_FAIL_OPEN", "false")
    ctx = build_context(_req())
    assert ctx.granted_permissions == []


def test_explicit_scopes_respected_regardless_of_flag(monkeypatch):
    monkeypatch.setenv("PERMISSIONS_FAIL_OPEN", "false")
    ctx = build_context(_req("location.read,network.external"))
    assert ctx.granted_permissions == ["location.read", "network.external"]
