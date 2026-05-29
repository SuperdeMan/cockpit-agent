"""PaymentGateway gRPC 服务。Agent 不持凭证，所有支付经此服务。"""
from __future__ import annotations
import logging

# 注：proto 生成后 import 路径为 cockpit.payment.v1
# 当前 gen/ 未生成，用 try/except 保证不阻塞
try:
    from cockpit.payment.v1 import payment_pb2, payment_pb2_grpc
except ImportError:
    payment_pb2 = None
    payment_pb2_grpc = None

from store import PaymentStore

logger = logging.getLogger("payment.server")


class PaymentGatewayServicer:
    def __init__(self):
        self.store = PaymentStore()

    async def Authorize(self, request, context):
        order = await self.store.authorize(
            agent_id=request.agent_id,
            user_id=request.user_id,
            vehicle_id=request.vehicle_id,
            scene=request.scene,
            amount_cents=request.amount_cents,
            currency=request.currency,
            description=request.description,
            idempotency_key=request.idempotency_key,
        )
        return payment_pb2.AuthorizeResponse(
            payment_id=order.payment_id,
            require_confirm=True,
            confirm_prompt=f"确认支付 {order.amount_cents/100:.0f} 元（{order.scene}）？",
        )

    async def Capture(self, request, context):
        ok, result = await self.store.capture(request.payment_id, request.confirm_token)
        return payment_pb2.CaptureResponse(
            ok=ok,
            receipt_id=result if ok else "",
            error="" if ok else result,
        )

    async def Cancel(self, request, context):
        ok = await self.store.cancel(request.payment_id)
        return payment_pb2.CancelResponse(ok=ok)

    async def GetStatus(self, request, context):
        order = await self.store.get(request.payment_id)
        if not order:
            return payment_pb2.GetStatusResponse(status=4)  # FAILED
        status_map = {"authorized": 1, "captured": 2, "cancelled": 3, "failed": 4}
        return payment_pb2.GetStatusResponse(
            status=status_map.get(order.status, 0),
            payment_id=order.payment_id,
            amount_cents=order.amount_cents,
            scene=order.scene,
        )
