"""R3.2 mTLS：runtime/grpcio.py 的 TLS 门控。

默认关（GRPC_TLS 未设/off）= insecure 逐字保持现状；开启走 secure 且缺证书干净报错。
真实握手在 test/e2e_mtls.py（真栈）验证。
"""
from __future__ import annotations

import asyncio

import grpc
import pytest

from runtime import grpcio


@pytest.mark.parametrize("val,expected", [
    ("on", True), ("true", True), ("1", True), ("yes", True),
    ("off", False), ("false", False), ("", False), ("0", False), ("no", False),
])
def test_tls_enabled_env_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("GRPC_TLS", val)
    assert grpcio._tls_enabled() is expected


def test_tls_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("GRPC_TLS", raising=False)
    assert grpcio._tls_enabled() is False


def test_aio_channel_and_bind_port_insecure_when_off(monkeypatch):
    monkeypatch.delenv("GRPC_TLS", raising=False)

    async def go():
        ch = grpcio.aio_channel("localhost:50051")
        assert isinstance(ch, grpc.aio.Channel)
        await ch.close()
        server = grpcio.aio_server()
        assert grpcio.bind_port(server, "[::]:0") > 0   # :0 = 任意空闲端口

    asyncio.run(go())


def test_tls_on_missing_certs_raises(monkeypatch):
    # TLS 开启但证书路径不存在 → 走 secure 分支并干净抛错（不静默退化 insecure）。
    monkeypatch.setenv("GRPC_TLS", "on")
    monkeypatch.setenv("GRPC_TLS_CA", "/nonexistent/ca.crt")
    monkeypatch.setenv("GRPC_TLS_CERT", "/nonexistent/server.crt")
    monkeypatch.setenv("GRPC_TLS_KEY", "/nonexistent/server.key")
    grpcio._channel_creds.cache_clear()
    grpcio._server_creds.cache_clear()

    async def go():
        with pytest.raises(FileNotFoundError):
            grpcio.aio_channel("localhost:50051")

    asyncio.run(go())
    grpcio._channel_creds.cache_clear()   # 复位，避免污染后续
