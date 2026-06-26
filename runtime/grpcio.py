"""共享 gRPC 运行时：keepalive 拨号 / 建服务 / 优雅停机。

全 Python 服务统一经此创建 channel 与 server，根治两类系统性问题：

1. **空闲连接被静默掐断**（断连/无响应头号根因）。容器网络/NAT/代理会回收
   长时间无流量的 TCP；裸 `grpc.aio.insecure_channel` 不发 keepalive，两端都
   察觉不到死连，直到下次 RPC 挂住才超时。这里统一开启 HTTP/2 keepalive：
   每 ``KEEPALIVE_TIME_MS`` 主动 ping、``KEEPALIVE_TIMEOUT_MS`` 内无 ack 即判死
   并触发底层重连，**空闲也 ping**（``permit_without_calls``）。
2. **重建容器时硬杀在途请求**。裸 ``await server.wait_for_termination()`` 收到
   SIGTERM 直接终止；``run_aio_server`` 改为优雅停机，``server.stop(grace)``
   排空在途 RPC 再退出。

所有阈值经 env 覆盖，默认值对量产安全。客户端 keepalive 周期须 ≥ 服务端容忍的
最小 ping 间隔（``MIN_PING_INTERVAL_MS``），否则会被服务端 GOAWAY——本模块的
默认值已对齐（client 20s ≥ server 10s）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

import grpc

logger = logging.getLogger("runtime.grpcio")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


# 每 20s 主动 keepalive ping；10s 内无 ack 判定连接死亡并重连；空闲连接也 ping。
KEEPALIVE_TIME_MS = _int_env("GRPC_KEEPALIVE_TIME_MS", 20000)
KEEPALIVE_TIMEOUT_MS = _int_env("GRPC_KEEPALIVE_TIMEOUT_MS", 10000)
# 服务端容忍 client ping 的最小间隔，须 ≤ 客户端 KEEPALIVE_TIME_MS，否则被 GOAWAY。
MIN_PING_INTERVAL_MS = _int_env("GRPC_MIN_PING_INTERVAL_MS", 10000)
MAX_MESSAGE_BYTES = _int_env("GRPC_MAX_MESSAGE_BYTES", 16 * 1024 * 1024)
# 服务端并发 RPC 上限（背压）。默认 0=不限（保持既有行为）；运维可经 env 设上限。
MAX_CONCURRENT_RPCS = _int_env("GRPC_MAX_CONCURRENT_RPCS", 0)
# 优雅停机排空在途 RPC 的宽限秒数。
SHUTDOWN_GRACE_S = float(os.getenv("GRPC_SHUTDOWN_GRACE_S", "") or 10)


def _common_options() -> list[tuple[str, int]]:
    return [
        ("grpc.keepalive_time_ms", KEEPALIVE_TIME_MS),
        ("grpc.keepalive_timeout_ms", KEEPALIVE_TIMEOUT_MS),
        ("grpc.keepalive_permit_without_calls", 1),
        ("grpc.http2.max_pings_without_data", 0),
        ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
        ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
    ]


def channel_options() -> list[tuple]:
    """客户端 channel 选项：keepalive + 重连退避 + 大消息。"""
    return _common_options() + [
        ("grpc.initial_reconnect_backoff_ms", 500),
        ("grpc.min_reconnect_backoff_ms", 500),
        ("grpc.max_reconnect_backoff_ms", 10000),
    ]


def server_options() -> list[tuple]:
    """服务端选项：keepalive + 容忍客户端高频 ping（min_ping_interval）。"""
    return _common_options() + [
        ("grpc.http2.min_ping_interval_without_data_ms", MIN_PING_INTERVAL_MS),
        ("grpc.http2.min_time_between_pings_ms", MIN_PING_INTERVAL_MS),
    ]


def aio_channel(addr: str, *, extra_options: list[tuple] | None = None) -> grpc.aio.Channel:
    """统一 keepalive 的 async insecure channel。替换裸 ``grpc.aio.insecure_channel``。"""
    return grpc.aio.insecure_channel(addr, options=channel_options() + (extra_options or []))


def aio_server(*, max_concurrent_rpcs: int | None = None,
               extra_options: list[tuple] | None = None) -> grpc.aio.Server:
    """统一 keepalive（+ 可选并发上限）的 async server。替换裸 ``grpc.aio.server()``。"""
    cap = max_concurrent_rpcs if max_concurrent_rpcs is not None else MAX_CONCURRENT_RPCS
    return grpc.aio.server(
        options=server_options() + (extra_options or []),
        maximum_concurrent_rpcs=cap or None,
    )


async def run_aio_server(server: grpc.aio.Server, *, name: str = "",
                         grace: float | None = None, on_shutdown=None) -> None:
    """运行 server 至 SIGTERM/SIGINT，再优雅停机（排空在途 RPC）。

    替换裸 ``await server.wait_for_termination()``。``on_shutdown`` 为可选清理
    协程（cancel 后台任务、关连接等），在 ``server.stop`` 前执行。Windows 或非
    主线程无 ``add_signal_handler`` 时优雅回退为 ``wait_for_termination``（行为
    同改造前，仅少了信号触发的主动优雅停机——容器内 Linux 不受影响）。
    """
    grace = SHUTDOWN_GRACE_S if grace is None else grace
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = False
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed = True
        except (NotImplementedError, RuntimeError, ValueError):
            pass  # Windows / 非主线程：回退 wait_for_termination

    if not installed:
        await server.wait_for_termination()
        return

    waiter = asyncio.ensure_future(server.wait_for_termination())
    stopper = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (waiter, stopper):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

    logger.info("[%s] received shutdown signal, draining (grace=%.0fs)", name or "grpc", grace)
    if on_shutdown is not None:
        with contextlib.suppress(Exception):
            await on_shutdown()
    await server.stop(grace)
