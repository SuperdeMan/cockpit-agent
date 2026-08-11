"""支付网关客户端（§9.17）——「Agent 只经网关支付、不持凭证」的机制化载体。

- parking-payment 与 mcp-bridge 共用；Agent 侧只发支付**意图**（金额来自业务
  事实源），渠道凭证/二维码生成/查单全部在 payment-gateway。
- trace 经 gRPC metadata `x-trace-id` 透传（contextvar 由 SDK server 在 Execute
  入口设置）；granted_scopes 编排层校验后不随 meta 下发，网关侧走 PoC fail-open
  留痕契约（server 端 `_check_scope`），权限单轨完整版是显式后置项。
- 调用失败（网关没起/网络）返回 None——Agent 按 R9 诚实降级出话术（OK 态），
  不假装支付在进行。
"""
from __future__ import annotations

import logging
import os

import grpc

from runtime.grpcio import aio_channel

from cockpit.payment.v1 import payment_pb2, payment_pb2_grpc

logger = logging.getLogger("sdk.payment")

_TIMEOUT_S = 8


def _metadata() -> list[tuple[str, str]]:
    md: list[tuple[str, str]] = []
    try:
        from observability.tracing import get_trace_id
        tid = get_trace_id()
        if tid:
            md.append(("x-trace-id", tid))
    except Exception:
        pass
    return md


class PaymentClient:
    """薄封装：Authorize / Capture / GetStatus / Cancel。channel 懒建、实例内复用。"""

    def __init__(self, addr: str = ""):
        self._addr = addr or os.getenv("PAYMENT_GATEWAY_ADDR",
                                       "payment-gateway:50071")
        self._channel = None
        self._stub = None

    def _get_stub(self) -> payment_pb2_grpc.PaymentGatewayStub:
        if self._stub is None:
            self._channel = aio_channel(self._addr)
            self._stub = payment_pb2_grpc.PaymentGatewayStub(self._channel)
        return self._stub

    async def authorize(self, *, agent_id: str, user_id: str, vehicle_id: str,
                        scene: str, amount_cents: int, description: str,
                        idempotency_key: str,
                        channel: int = 0, external_pay_url: str = "",
                        external_order_ref: str = ""):
        """建单（或幂等重取——confirm_token 的官方传递通道，§9.17）。失败返回 None。"""
        req = payment_pb2.AuthorizeRequest(
            agent_id=agent_id, user_id=user_id, vehicle_id=vehicle_id,
            scene=scene, amount_cents=amount_cents, currency="CNY",
            description=description, idempotency_key=idempotency_key,
            channel=channel, external_pay_url=external_pay_url,
            external_order_ref=external_order_ref)
        try:
            return await self._get_stub().Authorize(
                req, timeout=_TIMEOUT_S, metadata=_metadata())
        except grpc.RpcError as e:
            logger.warning("payment.Authorize 失败（%s）：%s",
                           e.code(), e.details())
            return None

    async def capture(self, payment_id: str, confirm_token: str):
        try:
            return await self._get_stub().Capture(
                payment_pb2.CaptureRequest(payment_id=payment_id,
                                           confirm_token=confirm_token),
                timeout=_TIMEOUT_S, metadata=_metadata())
        except grpc.RpcError as e:
            logger.warning("payment.Capture 失败（%s）：%s", e.code(), e.details())
            return None

    async def status(self, payment_id: str):
        try:
            return await self._get_stub().GetStatus(
                payment_pb2.GetStatusRequest(payment_id=payment_id),
                timeout=_TIMEOUT_S, metadata=_metadata())
        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.NOT_FOUND:
                logger.warning("payment.GetStatus 失败（%s）：%s",
                               e.code(), e.details())
            return None

    async def cancel(self, payment_id: str, reason: str = ""):
        try:
            return await self._get_stub().Cancel(
                payment_pb2.CancelRequest(payment_id=payment_id, reason=reason),
                timeout=_TIMEOUT_S, metadata=_metadata())
        except grpc.RpcError as e:
            logger.warning("payment.Cancel 失败（%s）：%s", e.code(), e.details())
            return None
