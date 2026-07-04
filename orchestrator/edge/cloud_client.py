"""端→云客户端：端侧编排器的慢路径上云通道。

架构 §8：端云通信走 EdgeCloudChannel 双向流（bidi 帧协议），经 Cloud Gateway 到达 Cloud Planner。

R2.3：**进程内单条持久 bidi 长连 + corr_id 多路复用 + 心跳 + 断线重连**（取代逐请求建流/握手）。
逻辑对标 `gateway/edge/main.go::ChannelClient`（已删的 Go 参考实现）：
- 后台 `_run` 维护一条 `Connect()` 流，断开按指数退避+抖动重连；
- `handle()` 仍是 async-generator（对外契约不变），经 corr_id 复用同一条流；
- `_reader` 单读循环按 corr_id 把 DownFrame.event 路由到各请求队列，并就地服务云→端 edge_call；
- `_pinger` 15s 心跳，连丢 Pong 超限即断流触发重连；
- 断连时在途请求快速失败（投 _FAIL 哨兵），由上层（server.py）出降级话术。

云侧 `gateway/cloud/main.go::channelServer.Connect` 本就按 corr_id 并发多路复用，故本次仅改端侧。
"""
from __future__ import annotations
import asyncio
import contextlib
import logging
import os
import time
import uuid

import grpc
from cockpit.channel.v1 import channel_pb2, channel_pb2_grpc
from cockpit.orchestrator.v1 import orchestrator_pb2

from runtime.grpcio import aio_channel

logger = logging.getLogger("edge.cloud_client")

# 在途请求失败哨兵：断连时投入各 pending 队列，handle() 消费到即抛错让上层降级。
_FAIL = object()

_PING_INTERVAL_S = float(os.getenv("CLOUD_CHANNEL_PING_S", "15"))
_MISSED_PONG_LIMIT = int(os.getenv("CLOUD_CHANNEL_MISSED_PONG", "3"))
_MAX_RECONNECT_DELAY_S = float(os.getenv("CLOUD_CHANNEL_MAX_BACKOFF_S", "30"))
_CONNECT_WAIT_S = float(os.getenv("CLOUD_CHANNEL_CONNECT_WAIT_S", "10"))


def _vehicle_of(request) -> str:
    ctx = getattr(request, "context", None)
    return getattr(ctx, "vehicle_id", "") if ctx else ""


def _dns_target(addr: str) -> str:
    """强制 dns resolver（换 IP 后重解析）；已带 scheme 的原样返回。对标 Go dnsTarget。"""
    return addr if "://" in addr else f"dns:///{addr}"


class CloudClient:
    """端→云持久通道门面。`handle()` 契约（async-generator，异常上抛）保持不变。"""

    def __init__(self, edge_call_executor=None, stub_factory=None):
        self.addr = os.getenv("CLOUD_GATEWAY_ADDR", "cloud-gateway:8080")
        self.vehicle_id = os.getenv("VEHICLE_ID", "v1")
        # R3.1 层 2（通道鉴权）：Hello 携带 channel session_token，云网关按 AUTH_REQUIRED 校验；
        # 默认空 → 云侧默认放行（保持现状）。
        self.channel_token = os.getenv("CLOUD_CHANNEL_TOKEN", "")
        self._edge_calls = edge_call_executor
        self._stub_factory = stub_factory or channel_pb2_grpc.EdgeCloudChannelStub

        self._ch: grpc.aio.Channel | None = None
        self._stream = None
        self._send_lock = asyncio.Lock()          # 串行化 stream.write（request/ping/edge_result/hello）
        self._pending: dict[str, asyncio.Queue] = {}   # corr_id -> 事件队列（多路复用）
        self._connected = asyncio.Event()
        self._run_task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._missed_pong = 0
        self._backoff = 0.5
        self._closing = False
        self._started = False

    # ─── 生命周期 ───

    async def _ensure_started(self):
        if not self._started:
            self._started = True
            self._run_task = asyncio.create_task(self._run())

    async def _run(self):
        """维护单条持久 bidi 流：连接 → 读循环（阻塞至流断）→ 退避重连。"""
        while not self._closing:
            try:
                # 单次(重)连必须有界（K1）：cloud-gateway `pause/unpause`（同 IP 冻结再解冻）
                # 时，冻结窗口内发起的连接会卡在半开的 HTTP/2 握手（服务端冻结不发 SETTINGS/
                # HelloAck），无 deadline 的握手会永久挂起——且在部分 Docker/WSL2 环境下即便解冻
                # 也不恢复，表现为通道永不自愈、只能重启 edge-orchestrator。加超时保证任何单次
                # 尝试有界失败，_run 退避后丢弃这条卡死的 channel、用全新 channel 再试，解冻后的
                # 干净连接即可自愈。见 docs/design/2026-07-04-r4.0-*。
                await asyncio.wait_for(self._open(), timeout=_CONNECT_WAIT_S)
                self._backoff = 0.5
                self._missed_pong = 0
                self._connected.set()
                self._ping_task = asyncio.create_task(self._ping_loop())
                await self._read_loop()          # 阻塞直到流结束/出错
            except asyncio.CancelledError:
                # 仅当真正在关闭时才让 _run 退出（aclose 先置 _closing=True 再 cancel 本任务）。
                # 否则这条 CancelledError 来自 _ping_loop 的 _cancel_stream()——强制重连时 cancel 掉
                # grpc 流，_read_loop 的 read() 随之抛 CancelledError；绝不能把它当任务取消 re-raise
                # 掉整个 _run，否则 _run 任务死亡、通道永不重连。这正是 K1（pause/unpause 同 IP 冻结
                # 不自愈）的真正根因：冻结场景靠 app-ping 检测缺 pong 触发 _cancel_stream()，而非
                # 换 IP 场景那样由 grpc keepalive 抛 AioRpcError（走 except Exception 正常重连）。
                if self._closing:
                    raise
                logger.warning("Cloud channel stream cancelled, reconnecting")
            except Exception as exc:
                logger.warning("Cloud channel session ended: %s", exc)
            finally:
                self._end_session()
            if self._closing:
                break
            jitter = self._backoff / 4.0
            await asyncio.sleep(self._backoff + (uuid.uuid4().int % 1000) / 1000.0 * jitter)
            self._backoff = min(self._backoff * 2, _MAX_RECONNECT_DELAY_S)

    def _hello_frame(self) -> channel_pb2.UpFrame:
        """构造 Hello 握手帧。R3.1 层 2：带 channel session_token（云网关按 AUTH_REQUIRED 校验）。"""
        return channel_pb2.UpFrame(
            correlation_id=f"{self.vehicle_id}-hello",
            hello=channel_pb2.Hello(
                vehicle_id=self.vehicle_id,
                session_token=self.channel_token,
            ),
        )

    async def _open(self):
        """建流 + 发 Hello + 收 HelloAck（对标 Go connect()）。失败抛错由 _run 退避重连。

        每次(重)连都重建 channel 强制 DNS 重解析：依赖容器重建换 IP 后，复用旧 channel 会一直连
        旧 IP（裸 host:port 走 passthrough resolver 只解析一次），故对标 Go connect() 每次新建 +
        dns:/// scheme（连接失败时重解析），根治「换 IP 后需重启本服务」。
        """
        if self._ch is not None:
            old, self._ch = self._ch, None
            with contextlib.suppress(Exception):
                await old.close()
        self._ch = aio_channel(_dns_target(self.addr))
        stub = self._stub_factory(self._ch)
        self._stream = stub.Connect()
        async with self._send_lock:
            await self._stream.write(self._hello_frame())
        ack = await self._stream.read()
        if ack is grpc.aio.EOF:
            raise RuntimeError("stream closed during hello")
        if ack.WhichOneof("body") == "hello_ack" and not ack.hello_ack.ok:
            raise RuntimeError(f"hello rejected: {ack.hello_ack.reason}")
        logger.info("Cloud channel connected as %s", self.vehicle_id)

    def _end_session(self):
        """一次会话结束（断连/出错）：清连接态、停心跳、在途请求快速失败。"""
        self._connected.clear()
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None
        self._cancel_stream()
        for q in list(self._pending.values()):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(_FAIL)

    def _cancel_stream(self):
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.cancel()

    async def aclose(self):
        """优雅停机：停后台任务、失败在途、关 channel。经 main.run_aio_server(on_shutdown=) 调用。"""
        self._closing = True
        if self._ping_task is not None:
            self._ping_task.cancel()
        self._cancel_stream()
        if self._run_task is not None:
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
        for q in list(self._pending.values()):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(_FAIL)
        if self._ch is not None:
            with contextlib.suppress(Exception):
                await self._ch.close()

    # ─── 读/心跳循环 ───

    async def _read_loop(self):
        while True:
            down = await self._stream.read()
            if down is grpc.aio.EOF:
                raise RuntimeError("stream EOF")
            which = down.WhichOneof("body")
            if which == "event":
                q = self._pending.get(down.correlation_id)
                if q is not None:
                    q.put_nowait(down.event)
            elif which == "edge_call":
                await self._service_edge_call(down)
            elif which == "pong":
                self._missed_pong = 0
            elif which == "proactive":
                # proactive→HMI 现走 NATS（edge 网关广播）；通道 Proactive 帧本次不接线，仅记录。
                logger.info("Proactive over channel (ignored, NATS path used): %s",
                            down.proactive.type)
            # hello_ack 已在 _open 处理；其余忽略

    async def _service_edge_call(self, down):
        """云→端 edge_call：经 VAL executor 执行后回写 EdgeResult（同 corr_id，对标原逐请求语义）。"""
        call = down.edge_call
        if self._edge_calls is None:
            logger.warning("Received edge_call without executor")
            result = channel_pb2.EdgeResult(step_id=call.step_id)
            result.result.status = 3  # FAILED
        else:
            result = channel_pb2.EdgeResult(
                step_id=call.step_id,
                result=self._edge_calls.execute(call),
            )
        async with self._send_lock:
            await self._stream.write(channel_pb2.UpFrame(
                correlation_id=down.correlation_id,
                edge_result=result,
            ))

    async def _ping_loop(self):
        try:
            while self._connected.is_set():
                await asyncio.sleep(_PING_INTERVAL_S)
                if not self._connected.is_set():
                    return
                self._missed_pong += 1
                now_ms = int(time.time() * 1000)
                try:
                    async with self._send_lock:
                        await self._stream.write(channel_pb2.UpFrame(
                            correlation_id=f"{self.vehicle_id}-ping-{now_ms}",
                            ping=channel_pb2.Ping(ts=now_ms),
                        ))
                except Exception as exc:
                    logger.debug("ping send failed: %s", exc)
                    return
                if self._missed_pong > _MISSED_PONG_LIMIT:
                    logger.warning("Missed too many pongs, forcing reconnect")
                    self._cancel_stream()   # 断流 → _read_loop 出错 → _run 重连
                    return
        except asyncio.CancelledError:
            pass

    # ─── 请求（多路复用）───

    async def handle(self, request):
        """通过持久 EdgeCloudChannel 转发请求，yield HandleEvent 直到 final；断连则抛错由上层降级。"""
        vid = _vehicle_of(request)
        if vid and not self._started:
            self.vehicle_id = vid   # 首个请求前采用其 vehicle_id 作握手身份
        await self._ensure_started()

        try:
            await asyncio.wait_for(self._connected.wait(), timeout=_CONNECT_WAIT_S)
        except asyncio.TimeoutError:
            raise RuntimeError("cloud channel not connected")

        # corr_id 必须全局唯一——cloud-gateway 按它做幂等去重(10min TTL)，撞车会被当重复
        # 静默丢弃致挂起。曾用 id(request)(内存地址被 GC 复用)撞车，改 uuid4 根治。
        corr_id = f"{request.session_id}-{uuid.uuid4().hex}"
        q: asyncio.Queue = asyncio.Queue()
        self._pending[corr_id] = q
        try:
            async with self._send_lock:
                await self._stream.write(channel_pb2.UpFrame(
                    correlation_id=corr_id,
                    request=request,
                ))
            while True:
                item = await q.get()
                if item is _FAIL:
                    raise RuntimeError("cloud channel disconnected")
                yield item
                if item.HasField("final"):
                    break
        finally:
            self._pending.pop(corr_id, None)
