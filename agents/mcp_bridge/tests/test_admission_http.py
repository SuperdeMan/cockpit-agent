"""admission 批 3 扩展契约：transport/headers ${VAR} 展开 / 缺 env 拒载 /
compensate 两态 / compensate_tool 存在性校验。"""
import asyncio
import os
import textwrap

import pytest

from agents.mcp_bridge.src.admission import (COMPENSATE_POLICIES, REJECT_COMPENSATE,
                                             REJECT_ENV, ToolSpec, ServerSpec,
                                             admit, load_servers)


def _write_yaml(tmp_path, body: str) -> str:
    p = tmp_path / "servers.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(p)


def test_transport_http_fields_parsed(tmp_path, monkeypatch):
    monkeypatch.setenv("T_TOKEN", "sekret-token-1")
    path = _write_yaml(tmp_path, """
        servers:
          - id: merchant
            transport: streamable_http
            url: https://mcp.example.cn
            headers:
              Authorization: "Bearer ${T_TOKEN}"
            version: ""
            pay_url_hosts: [Pay.Example.CN]
            tools:
              - {name: t1, intent: m.menu, write: false}
    """)
    spec = load_servers(path)[0]
    assert spec.transport == "streamable_http"
    assert spec.url == "https://mcp.example.cn"
    assert spec.headers["Authorization"] == "Bearer sekret-token-1"
    assert spec.pay_url_hosts == ["pay.example.cn"]     # 域名归一小写
    assert spec.env_error == ""


def test_missing_env_marks_server_rejected(tmp_path, monkeypatch):
    """缺 token → 具名拒载理由，不静默拿空 token 出站吃 401（§9.9）。"""
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    path = _write_yaml(tmp_path, """
        servers:
          - id: merchant
            transport: streamable_http
            url: https://mcp.example.cn
            headers:
              Authorization: "Bearer ${NOPE_TOKEN}"
            version: ""
            tools: []
    """)
    spec = load_servers(path)[0]
    assert REJECT_ENV in spec.env_error
    assert "NOPE_TOKEN" in spec.env_error


def test_stdio_defaults_unchanged(tmp_path):
    """存量 stdio 声明零改动照旧解析（demo-coffee 的形态）。"""
    path = _write_yaml(tmp_path, """
        servers:
          - id: demo
            command: [python, -m, x]
            version: "0.1.0"
            tools:
              - {name: t, intent: s.menu, write: false}
    """)
    spec = load_servers(path)[0]
    assert spec.transport == "stdio" and spec.url == "" and spec.headers == {}
    assert spec.env_error == ""


def _spec(tools) -> ServerSpec:
    return ServerSpec(id="s", command=[], version="", tools=tools)


def _offered(*names):
    return [{"name": n, "inputSchema": {}} for n in names]


def test_compensate_tool_must_exist_in_allowlist():
    """存在性校验（批 3 补死）：声明了却不在白名单=「声明存在≠能用」的准入器残留。"""
    t = ToolSpec(name="order.create", intent="m.order", write=True,
                 require_confirm=True, compensate_tool="order.cancel")
    admitted, rejected = admit(_spec([t]), _offered("order.create", "order.cancel"))
    assert not admitted and any(REJECT_COMPENSATE in r for r in rejected)

    cancel = ToolSpec(name="order.cancel", intent="m.cancel", write=True,
                      require_confirm=True, compensate_tool="order.cancel")
    admitted2, rejected2 = admit(_spec([t, cancel]),
                                 _offered("order.create", "order.cancel"))
    assert {a[0].name for a in admitted2} == {"order.create", "order.cancel"}
    assert rejected2 == []


def test_abandon_unpaid_requires_confirm_prompt():
    bare = ToolSpec(name="order.create", intent="m.order", write=True,
                    require_confirm=True, compensate_policy="abandon_unpaid")
    admitted, rejected = admit(_spec([bare]), _offered("order.create"))
    assert not admitted and any(REJECT_COMPENSATE in r for r in rejected)

    ok = ToolSpec(name="order.create", intent="m.order", write=True,
                  require_confirm=True, compensate_policy="abandon_unpaid",
                  confirm_prompt="准备下单：{args}。下单后需扫码支付，不支付将自动取消，确认吗？")
    admitted2, rejected2 = admit(_spec([ok]), _offered("order.create"))
    assert len(admitted2) == 1 and rejected2 == []


def test_unknown_compensate_policy_rejected():
    t = ToolSpec(name="x", intent="m.x", write=True, require_confirm=True,
                 compensate_policy="whatever")
    admitted, rejected = admit(_spec([t]), _offered("x"))
    assert not admitted and any("whatever" in r for r in rejected)
    assert set(COMPENSATE_POLICIES) == {"tool", "abandon_unpaid"}


def test_read_tools_unaffected_by_compensate_rules():
    t = ToolSpec(name="menu", intent="m.menu", write=False)
    admitted, rejected = admit(_spec([t]), _offered("menu"))
    assert len(admitted) == 1 and rejected == []
