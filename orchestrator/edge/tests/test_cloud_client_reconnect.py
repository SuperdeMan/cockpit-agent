"""K1（R4.0）：持久通道在 cloud-gateway `pause/unpause`（同 IP 冻结再解冻）后必须自愈。

两条独立缺陷、两条独立守护：

1) **重连尝试无界挂起**（`test_reconnect_is_bounded_and_self_heals`）：冻结窗口内发起的
   握手会卡在半开的 HTTP/2（服务端冻结不发 HelloAck），无 deadline 的握手 read 永久挂起。
   修复：`_run` 用 `asyncio.wait_for(self._open(), _CONNECT_WAIT_S)` 给每次尝试加超时。

2) **心跳强制重连打死 _run**（`test_ping_forced_reconnect_does_not_kill_run_loop`，K1 的真正根因）：
   冻结场景靠 **app 层心跳**（累计丢 pong）检测，`_ping_loop` 调 `_cancel_stream()` 强制重连，
   `_read_loop` 的 `read()` 随之抛 `asyncio.CancelledError`；`_run` 若把它当任务取消 re-raise，
   整个重连循环任务直接死亡、通道永不重连。换 IP 场景不中招是因为走 grpc keepalive 抛的普通
   `AioRpcError`（被 `except Exception` 正常接住）。修复：`_run` 用 `_closing` 区分"流被取消"
   （继续重连）与"任务被取消"（真正退出）。

去掉任一修复，对应测试会失败（前者第一条冻结尝试永久挂起、后者 _run 被 CancelledError 打死）。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.channel.v1 import channel_pb2

from cloud_client import CloudClient


class _FakeChannel:
    async def close(self):
        return None


def _helloack():
    return channel_pb2.DownFrame(
        correlation_id="v1-hello",
        hello_ack=channel_pb2.HelloAck(ok=True, heartbeat_sec=15))


# ─── 缺陷 1：重连尝试无界挂起 ───────────────────────────────────────────

class _FreezeThenHealStream:
    """frozen=True：read() 永久阻塞（可被 wait_for 取消）——服务端冻结、HelloAck 永不到达。
    frozen=False：首次 read() 回 HelloAck，之后读循环静默阻塞保持会话存活。"""

    def __init__(self, frozen: bool):
        self._frozen = frozen
        self._acked = False
        self.cancelled = False

    async def write(self, frame):
        return None

    async def read(self):
        if self._frozen:
            await asyncio.sleep(3600)
        if not self._acked:
            self._acked = True
            return _helloack()
        await asyncio.sleep(3600)

    def cancel(self):
        self.cancelled = True


def test_reconnect_is_bounded_and_self_heals(monkeypatch):
    """冻结窗口内的握手有界超时后重试，服务端恢复即用新 channel 自愈。"""
    monkeypatch.setattr("cloud_client._CONNECT_WAIT_S", 0.15)
    monkeypatch.setattr("cloud_client._MAX_RECONNECT_DELAY_S", 0.05)
    monkeypatch.setattr("cloud_client.aio_channel", lambda addr, **kw: _FakeChannel())

    UNFREEZE_AFTER = 3
    calls = {"n": 0}

    class _Stub:
        def __init__(self, _ch):
            pass

        def Connect(self):
            calls["n"] += 1
            return _FreezeThenHealStream(frozen=calls["n"] < UNFREEZE_AFTER)

    async def scenario():
        client = CloudClient(stub_factory=_Stub)
        client._backoff = 0.01
        await client._ensure_started()
        for _ in range(200):
            if client._connected.is_set():
                break
            await asyncio.sleep(0.02)
        connected = client._connected.is_set()
        n = calls["n"]
        client._closing = True
        await client.aclose()
        return connected, n

    connected, n = asyncio.run(scenario())
    assert connected, "冻结解除后持久通道应自愈连上（说明单次重连未永久挂起）"
    assert n >= UNFREEZE_AFTER, f"应发生多次有界重连尝试而非卡在第一条（实际 Connect {n} 次）"


# ─── 缺陷 2：心跳强制重连打死 _run（K1 真正根因）─────────────────────────

class _PongOrFreezeStream:
    """握手总成功。pong=False：读循环阻塞、对 Ping 不回 Pong（app 心跳判缺 pong→cancel）；
    pong=True：对 Ping 回 Pong（会话稳定）。cancel() 令阻塞的 read() 抛 CancelledError（模拟 grpc.aio）。"""

    _CANCEL = object()

    def __init__(self, pong: bool):
        self._pong = pong
        self._acked = False
        self._q: asyncio.Queue = asyncio.Queue()
        self.cancelled = False

    async def write(self, frame):
        if frame.WhichOneof("body") == "ping" and self._pong:
            self._q.put_nowait(channel_pb2.DownFrame(
                correlation_id=frame.correlation_id,
                pong=channel_pb2.Pong(ts=1)))

    async def read(self):
        if not self._acked:
            self._acked = True
            return _helloack()
        item = await self._q.get()
        if item is self._CANCEL:
            raise asyncio.CancelledError()
        return item

    def cancel(self):
        self.cancelled = True
        self._q.put_nowait(self._CANCEL)


def test_ping_forced_reconnect_does_not_kill_run_loop(monkeypatch):
    """app 心跳缺 pong → _cancel_stream() 令 read 抛 CancelledError，_run 必须重连而非被打死。"""
    monkeypatch.setattr("cloud_client._PING_INTERVAL_S", 0.05)
    monkeypatch.setattr("cloud_client._MISSED_PONG_LIMIT", 2)
    monkeypatch.setattr("cloud_client._CONNECT_WAIT_S", 0.3)
    monkeypatch.setattr("cloud_client._MAX_RECONNECT_DELAY_S", 0.05)
    monkeypatch.setattr("cloud_client.aio_channel", lambda addr, **kw: _FakeChannel())

    calls = {"n": 0}

    class _Stub:
        def __init__(self, _ch):
            pass

        def Connect(self):
            calls["n"] += 1
            return _PongOrFreezeStream(pong=calls["n"] >= 2)   # 第 1 条冻结、第 2 条起健康

    async def scenario():
        client = CloudClient(stub_factory=_Stub)
        client._backoff = 0.01
        await client._ensure_started()
        for _ in range(250):
            if client._connected.is_set() and calls["n"] >= 2:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)   # 再观察一会：确认没被 CancelledError 打死
        healthy = (client._connected.is_set()
                   and client._run_task is not None and not client._run_task.done())
        n = calls["n"]
        client._closing = True
        await client.aclose()
        return healthy, n

    healthy, n = asyncio.run(scenario())
    assert healthy, "心跳强制重连后通道应稳定连上且 _run 未被 CancelledError 打死"
    assert n >= 2, f"应重连到第二条健康流（实际 Connect {n} 次）"
