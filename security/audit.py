"""审计事件结构化。所有安全相关事件留痕。"""
from __future__ import annotations
import json
import time
import logging
from dataclasses import dataclass, field, asdict

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


# 为类型提示延迟导入
from .permission import AuthContext
