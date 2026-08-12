"""审计事件结构化。所有安全相关事件留痕。"""
from __future__ import annotations
import hashlib
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from urllib.parse import urlsplit

logger = logging.getLogger("security.audit")


@dataclass
class AuditEvent:
    ts: float = field(default_factory=time.time)
    trace_id: str = ""
    vehicle_id: str = ""
    user_id: str = ""
    agent_id: str = ""
    event: str = ""   # permission_denied | safety_gated | payment_invoked | injection_blocked
    intent: str = ""
    required: list[str] = field(default_factory=list)
    decision: str = ""  # rejected | allowed | blocked
    reason: str = ""
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class AuditLogger:
    """结构化审计日志。安全事件落盘/上报（当前用 logging，可接 Kafka/文件）。"""

    def log(self, event: AuditEvent):
        logger.warning("[AUDIT] %s", event.to_json())

    def permission_denied(self, agent_id: str, missing: list[str],
                          auth: AuthContext = None, trace_id: str = ""):
        self.log(AuditEvent(
            event="permission_denied", agent_id=agent_id,
            required=missing, decision="rejected",
            reason=f"missing: {missing}",
            trace_id=trace_id,
            user_id=auth.user_id if auth else "",
            vehicle_id=auth.vehicle_id if auth else "",
        ))

    def safety_gated(self, command: str, reason: str, vehicle_id: str = "",
                     trace_id: str = ""):
        self.log(AuditEvent(
            event="safety_gated", intent=command,
            decision="blocked", reason=reason,
            vehicle_id=vehicle_id, trace_id=trace_id,
        ))

    def payment_invoked(self, agent_id: str, payment_id: str, amount: int,
                        trace_id: str = ""):
        self.log(AuditEvent(
            event="payment_invoked", agent_id=agent_id,
            decision="authorized",
            extra={"payment_id": payment_id, "amount_cents": amount},
            trace_id=trace_id,
        ))

    def payment_captured(self, agent_id: str, payment_id: str, amount: int,
                         trade_no: str = "", trace_id: str = ""):
        """渠道确认收款（worker 查单推进 captured 时）。§9.17 三事件之二。"""
        self.log(AuditEvent(
            event="payment_captured", agent_id=agent_id,
            decision="allowed",
            extra={"payment_id": payment_id, "amount_cents": amount,
                   "trade_no": trade_no},
            trace_id=trace_id,
        ))

    def payment_refunded(self, agent_id: str, payment_id: str, amount: int,
                         refund_id: str = "", trace_id: str = ""):
        self.log(AuditEvent(
            event="payment_refunded", agent_id=agent_id,
            decision="allowed",
            extra={"payment_id": payment_id, "amount_cents": amount,
                   "refund_id": refund_id},
            trace_id=trace_id,
        ))

    def pay_url_denied(self, agent_id: str, pay_url: str, trace_id: str = ""):
        """merchant_hosted 支付链接域名不在白名单被拒（防钓鱼，§9.17）。"""
        raw_url = pay_url or ""
        try:
            parsed_host = urlsplit(raw_url).hostname or ""
            normalized_host = parsed_host.encode("idna").decode("ascii") \
                .rstrip(".").lower()
        except (UnicodeError, ValueError):
            normalized_host = ""
        self.log(AuditEvent(
            event="pay_url_denied", agent_id=agent_id,
            decision="blocked",
            reason="external_pay_url host not in PAYMENT_EXTERNAL_PAY_HOSTS",
            extra={
                "url_host": normalized_host or "invalid",
                "url_sha256": hashlib.sha256(raw_url.encode("utf-8")).hexdigest(),
                "url_length": len(raw_url),
            },
            trace_id=trace_id,
        ))

    def fail_open_scopes(self, vehicle_id: str = "", user_id: str = "",
                         trace_id: str = "", scopes: list[str] = None):
        """fail-open 兜底：请求无 granted_scopes，PoC 默认全授。量产应关（PERMISSIONS_FAIL_OPEN=false）。"""
        self.log(AuditEvent(
            event="fail_open_default_scopes", decision="allowed",
            reason="no granted_scopes in request; using PoC default scopes",
            required=list(scopes or []),
            vehicle_id=vehicle_id, user_id=user_id, trace_id=trace_id,
        ))


# 为类型提示延迟导入
from .permission import AuthContext
