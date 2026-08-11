"""PaymentGateway gRPC 服务。Agent 不持凭证，所有支付经此服务。

契约 docs/conventions.md §9.17；时序 ws6 §2。要点：
- **Authorize 本地建单不碰渠道**；Capture=确认后亮码（调渠道 precreate），
  `captured`（钱已到账）由轮询 worker 推进——本文件不做任何「同步扣款」。
- **执行层再校验**（ws8 §1.3 不信任上游）：metadata `x-granted-scopes` 含
  `payment.invoke` 才放行；metadata 缺失走 PoC fail-open（audit.fail_open_scopes
  留痕，与 orchestrator context 的 _POC_DEFAULT_SCOPES 同一契约）。
- **PAYMENT_REAL_SCENES 白名单**（fail-closed）：scene 不在名单强制 mock provider
  ——防 mock 数据算出的金额走真渠道收真钱（设计 2.7）。
- MERCHANT_HOSTED 单段：Authorize 直接登记落 pending_pay（确认已由 MCP 写工具闸
  完成），支付链接域名必须 ∈ PAYMENT_EXTERNAL_PAY_HOSTS（网关层白名单，桥侧
  pay_url_hosts 是第一层——两层各自持有防单点绕过）。
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlparse

import grpc

try:
    from cockpit.payment.v1 import payment_pb2, payment_pb2_grpc
except ImportError:      # proto 未生成时 store/provider 逻辑仍可独立测试
    payment_pb2 = None
    payment_pb2_grpc = None

try:
    # 容器平坦布局（PYTHONPATH=/app/payment-gateway）
    from providers import PaymentChannelError, resolve_payment_providers
    from store import PaymentStore
except ImportError:
    # 包形态（tests 经 payment_gateway 别名加载——不占 providers/store 裸名，
    # 与 llm-gateway 测试的同名模块零冲突）
    from .providers import PaymentChannelError, resolve_payment_providers
    from .store import PaymentStore

logger = logging.getLogger("payment.server")

_SCOPE = "payment.invoke"

_STATUS_MAP = {
    "authorized": 1, "captured": 2, "cancelled": 3, "failed": 4,
    "pending_pay": 5, "expired": 6, "refunding": 7, "refunded": 8,
}
_CHANNEL_MAP = {"alipay_qr": 1, "wechat_qr": 2, "merchant_hosted": 3}


def _qr_expire_s() -> int:
    try:
        return int(os.getenv("PAYMENT_QR_EXPIRE_S", "300"))
    except ValueError:
        return 300


def _merchant_expire_s() -> int:
    try:
        return int(os.getenv("PAYMENT_MERCHANT_EXPIRE_S", "1800"))
    except ValueError:
        return 1800


def _real_scenes() -> set[str]:
    return {s.strip() for s in os.getenv("PAYMENT_REAL_SCENES", "").split(",")
            if s.strip()}


def _external_pay_hosts() -> set[str]:
    return {h.strip().lower() for h in
            os.getenv("PAYMENT_EXTERNAL_PAY_HOSTS", "").split(",") if h.strip()}


def _host_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return bool(host) and host in _external_pay_hosts()


class PaymentGatewayServicer(
        payment_pb2_grpc.PaymentGatewayServicer if payment_pb2_grpc else object):

    def __init__(self, store: PaymentStore | None = None, providers=None,
                 audit=None):
        self.store = store or PaymentStore()
        self.providers = providers if providers is not None \
            else resolve_payment_providers()
        self._audit = audit
        if self._audit is None:
            try:
                from security.audit import AuditLogger
                self._audit = AuditLogger()
            except ImportError:      # 镜像缺 security/ 时诚实缺席（本地单测可注入）
                logger.warning("security.audit 不可用——支付审计缺席")

    # ── 横切 ────────────────────────────────────────────────────

    @staticmethod
    def _meta(context) -> dict[str, str]:
        try:
            return {k: v for k, v in (context.invocation_metadata() or [])}
        except Exception:
            return {}

    def _check_scope(self, context, *, user_id: str, vehicle_id: str,
                     agent_id: str, trace_id: str) -> bool:
        """执行层 scope 校验。metadata 缺失=PoC fail-open（audit 留痕）；
        带了但没有 payment.invoke=硬拒。"""
        raw = self._meta(context).get("x-granted-scopes")
        if raw is None:
            if self._audit:
                self._audit.fail_open_scopes(vehicle_id=vehicle_id, user_id=user_id,
                                             trace_id=trace_id, scopes=[_SCOPE])
            return True
        granted = {s.strip() for s in raw.split(",") if s.strip()}
        if _SCOPE in granted:
            return True
        if self._audit:
            self._audit.permission_denied(agent_id, [_SCOPE], trace_id=trace_id)
        return False

    def _trace_id(self, context) -> str:
        return self._meta(context).get("x-trace-id", "")

    async def _span(self, trace_id: str, node: str, status: str = "ok",
                    attrs: dict | None = None) -> None:
        try:
            from observability import events as obs_events
            await obs_events.get_emitter("payment").emit_span(
                trace_id or "payment-worker", node, status=status, attrs=attrs or {})
        except Exception:            # 观测面绝不拖垮支付主链
            pass

    def _pick_provider_key(self, scene: str, channel: int) -> tuple[str, str]:
        """返回 (provider_key, channel_str)。fail-closed：场景不在白名单强制 mock。"""
        name = {1: "alipay", 2: "wechat"}.get(channel) or \
            os.getenv("PAYMENT_DEFAULT_CHANNEL", "alipay").strip().lower()
        channel_str = {"alipay": "alipay_qr", "wechat": "wechat_qr"}.get(name, "")
        if scene not in _real_scenes():
            return "mock", channel_str
        return name, channel_str

    # ── RPC ─────────────────────────────────────────────────────

    async def Authorize(self, request, context):
        trace_id = self._trace_id(context)
        if not self._check_scope(context, user_id=request.user_id,
                                 vehicle_id=request.vehicle_id,
                                 agent_id=request.agent_id, trace_id=trace_id):
            await context.abort(grpc.StatusCode.PERMISSION_DENIED,
                                f"missing scope {_SCOPE}")

        is_merchant = request.channel == 3   # MERCHANT_HOSTED
        if is_merchant:
            if not request.external_pay_url:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                                    "MERCHANT_HOSTED 需要 external_pay_url")
            if not _host_allowed(request.external_pay_url):
                if self._audit:
                    self._audit.pay_url_denied(request.agent_id,
                                               request.external_pay_url,
                                               trace_id=trace_id)
                await context.abort(
                    grpc.StatusCode.PERMISSION_DENIED,
                    "external_pay_url 域名不在 PAYMENT_EXTERNAL_PAY_HOSTS 白名单")
            provider_key, channel_str = "merchant", "merchant_hosted"
        else:
            provider_key, channel_str = self._pick_provider_key(
                request.scene, request.channel)
            if provider_key not in self.providers:
                await context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"渠道 {provider_key} 未配置（PAYMENT_VENDOR）——fail-closed 不换渠道")

        try:
            order = await self.store.authorize(
                agent_id=request.agent_id, user_id=request.user_id,
                vehicle_id=request.vehicle_id, scene=request.scene,
                amount_cents=request.amount_cents, currency=request.currency,
                description=request.description,
                idempotency_key=request.idempotency_key,
                channel=channel_str, provider_key=provider_key,
                external_pay_url=request.external_pay_url,
                external_order_ref=request.external_order_ref,
            )
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

        # merchant_hosted 单段：登记即亮（链接就是「码」），过期收口交给 worker
        if is_merchant and order.status == "authorized":
            expires = time.time() + _merchant_expire_s()
            order = await self.store.mark_pending_pay(
                order.payment_id, qr_content=order.external_pay_url,
                pay_url=order.external_pay_url, expires_at=expires) or order
            await self.store.schedule_poll(order.payment_id, expires)

        if self._audit:
            self._audit.payment_invoked(request.agent_id, order.payment_id,
                                        order.amount_cents, trace_id=trace_id)
        await self._span(trace_id, "payment.authorize", attrs={
            "payment_id": order.payment_id, "scene": order.scene,
            "channel": order.channel, "provider": order.provider_key,
            "amount_cents": order.amount_cents, "status": order.status,
        })
        amount = f"{order.amount_cents // 100}.{order.amount_cents % 100:02d}"
        return payment_pb2.AuthorizeResponse(
            payment_id=order.payment_id,
            require_confirm=not is_merchant,
            confirm_prompt=f"确认支付 {amount} 元（{order.description or order.scene}）吗？",
            confirm_token=order.confirm_token,
            status=_STATUS_MAP.get(order.status, 0),
            provider_mode=order.provider_mode,
        )

    async def Capture(self, request, context):
        trace_id = self._trace_id(context)
        order, err = await self.store.get_for_capture(
            request.payment_id, request.confirm_token)
        if err:
            return payment_pb2.CaptureResponse(ok=False, error=err)

        if order.status == "pending_pay":     # 重入：回缓存码，不重打渠道
            return payment_pb2.CaptureResponse(
                ok=True, receipt_id=order.payment_id,
                qr_content=order.qr_content, pay_url=order.pay_url,
                expires_at_ms=int(order.expires_at * 1000),
                trade_no=order.trade_no, provider_mode=order.provider_mode)

        provider = self.providers.get(order.provider_key)
        if provider is None:
            return payment_pb2.CaptureResponse(
                ok=False, error=f"渠道 {order.provider_key} 未配置")
        try:
            # 参数取订单快照（§9.17：用户确认的金额=扣的金额）
            qr = await provider.create_qr(order.payment_id, order.amount_cents,
                                          order.description or order.scene)
        except PaymentChannelError as e:
            await self.store.mark_failed(order.payment_id, str(e))
            await self._span(trace_id, "payment.capture", status="error",
                             attrs={"payment_id": order.payment_id,
                                    "error": str(e)[:200]})
            return payment_pb2.CaptureResponse(ok=False, error=f"渠道下单失败：{e}")

        expires = qr.expires_at or (time.time() + _qr_expire_s())
        order = await self.store.mark_pending_pay(
            order.payment_id, qr_content=qr.qr_content, pay_url=qr.pay_url,
            channel_trade_ref=qr.channel_trade_ref, expires_at=expires) or order
        await self.store.schedule_poll(order.payment_id, time.time() + 3)
        await self._span(trace_id, "payment.capture", attrs={
            "payment_id": order.payment_id, "provider": order.provider_key,
            "expires_at": int(expires),
        })
        return payment_pb2.CaptureResponse(
            ok=True, receipt_id=order.payment_id,
            qr_content=order.qr_content, pay_url=order.pay_url,
            expires_at_ms=int(expires * 1000),
            provider_mode=order.provider_mode)

    async def Cancel(self, request, context):
        order = await self.store.get(request.payment_id)
        if not order:
            return payment_pb2.CancelResponse(ok=False)
        if order.status == "pending_pay" and order.channel != "merchant_hosted":
            provider = self.providers.get(order.provider_key)
            if provider is not None:
                try:
                    await provider.close(order.payment_id)
                except PaymentChannelError as e:
                    logger.warning("Cancel 渠道关单失败（照常本地取消）：%s", e)
        cancelled = await self.store.mark_cancelled(request.payment_id)
        return payment_pb2.CancelResponse(ok=cancelled is not None)

    async def GetStatus(self, request, context):
        order = await self.store.get(request.payment_id)
        if not order:
            await context.abort(grpc.StatusCode.NOT_FOUND,
                                f"payment {request.payment_id} 不存在")
        return payment_pb2.GetStatusResponse(
            status=_STATUS_MAP.get(order.status, 0),
            payment_id=order.payment_id,
            amount_cents=order.amount_cents,
            scene=order.scene,
            trade_no=order.trade_no,
            channel=_CHANNEL_MAP.get(order.channel, 0),
            expires_at_ms=int(order.expires_at * 1000),
        )

    async def Refund(self, request, context):
        trace_id = self._trace_id(context)
        order = await self.store.get(request.payment_id)
        if not order:
            return payment_pb2.RefundResponse(ok=False, error="订单不存在")
        if order.status != "captured":
            return payment_pb2.RefundResponse(
                ok=False, error=f"仅已支付订单可退（当前 {order.status}）")
        if request.amount_cents and request.amount_cents != order.amount_cents:
            return payment_pb2.RefundResponse(
                ok=False, error="v1 仅支持全额退款（amount_cents 传 0 或原额）")
        provider = self.providers.get(order.provider_key)
        if provider is None:
            return payment_pb2.RefundResponse(
                ok=False, error=f"渠道 {order.provider_key} 未配置")
        await self.store.mark_refunding(order.payment_id)
        try:
            ok, result = await provider.refund(
                order.payment_id, order.amount_cents, request.reason)
        except PaymentChannelError as e:
            ok, result = False, str(e)
        if not ok:
            # 退款失败退回 captured（钱还在账上，事实如此）
            order2 = await self.store.get(order.payment_id)
            if order2 and order2.status == "refunding":
                order2.status = "captured"
                await self.store._save(order2)
            await self._span(trace_id, "payment.refund", status="error",
                             attrs={"payment_id": order.payment_id,
                                    "error": result[:200]})
            return payment_pb2.RefundResponse(ok=False, error=result)
        # v1 语义：渠道受理即 refunded（支付宝当面付同步完成；微信最终到账
        # 确认需退款查询，超出 v1——§9.17 已注明）
        await self.store.mark_refunded(order.payment_id, refund_id=result)
        if self._audit:
            self._audit.payment_refunded(order.agent_id, order.payment_id,
                                         order.amount_cents, result,
                                         trace_id=trace_id)
        await self._span(trace_id, "payment.refund", attrs={
            "payment_id": order.payment_id, "refund_id": result})
        return payment_pb2.RefundResponse(ok=True, refund_id=result)
