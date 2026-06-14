"""Unified step dispatcher for cloud agents, vehicle edge executors, and tools."""
from __future__ import annotations

import logging
import time

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from observability.metrics import metrics
from security.audit import AuditLogger
from security.scopes import is_scope_covered

from .models import PlanContext, Step

logger = logging.getLogger("planner.dispatch")

_audit = AuditLogger()


def _failure(status: int, code: str, message: str) -> agent_pb2.ExecuteResponse:
    return agent_pb2.ExecuteResponse(
        status=status,
        speech="",
        error=common_pb2.ErrorInfo(code=code, message=message),
    )


class UnifiedDispatcher:
    """Route one plan step without exposing transport details to the executor."""

    def __init__(self, cloud_call, edge_call, tools=None):
        self._cloud_call = cloud_call
        self._edge_call = edge_call
        self._tools = tools

    async def dispatch(self, step: Step, ctx: PlanContext):
        required = list(step.required_permissions or [])
        if step.trust_level == "third_party" and any(
                permission == "vehicle.control" or
                permission.startswith("vehicle.control.")
                for permission in required):
            _audit.permission_denied(
                step.agent_id, required, trace_id=getattr(ctx, 'trace_id', ''))
            metrics.record_agent_call(step.agent_id, 0, False)
            return _failure(
                agent_pb2.ExecuteResponse.REJECTED,
                "permission_denied",
                "third_party agents cannot request vehicle.control",
            )
        missing = [
            permission for permission in required
            if not is_scope_covered(
                permission, set(ctx.granted_permissions or []))
        ]
        if missing:
            _audit.permission_denied(
                step.agent_id, missing, trace_id=getattr(ctx, 'trace_id', ''))
            metrics.record_agent_call(step.agent_id, 0, False)
            return _failure(
                agent_pb2.ExecuteResponse.REJECTED,
                "permission_denied",
                f"missing permissions: {', '.join(missing)}",
            )

        if step.kind == "tool":
            if any(p == "vehicle.control" or p.startswith("vehicle.control.")
                   for p in required):
                _audit.permission_denied(
                    step.agent_id, required,
                    trace_id=getattr(ctx, 'trace_id', ''))
                return _failure(
                    agent_pb2.ExecuteResponse.REJECTED,
                    "permission_denied",
                    "tools cannot request vehicle.control",
                )
            if self._tools is None:
                return _failure(
                    agent_pb2.ExecuteResponse.FAILED,
                    "tool_unavailable",
                    f"tool registry unavailable for {step.intent}",
                )
            start = time.monotonic()
            try:
                resp = await self._tools.call(step.intent, step.slots, ctx)
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(
                    step.agent_id, elapsed,
                    resp.status == agent_pb2.ExecuteResponse.OK)
                return resp
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(step.agent_id, elapsed, False)
                logger.warning("Tool %s failed: %s", step.intent, exc)
                return _failure(
                    agent_pb2.ExecuteResponse.FAILED,
                    "tool_error",
                    str(exc),
                )

        if step.deployment == "edge":
            if not ctx.vehicle_id:
                metrics.record_agent_call(step.agent_id, 0, False)
                return _failure(
                    agent_pb2.ExecuteResponse.FAILED,
                    "edge_unreachable",
                    "missing vehicle_id",
                )
            start = time.monotonic()
            try:
                resp = await self._edge_call(ctx.vehicle_id, step, ctx)
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(
                    step.agent_id, elapsed,
                    resp.status == agent_pb2.ExecuteResponse.OK)
                return resp
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(step.agent_id, elapsed, False)
                logger.warning("Edge step %s failed: %s", step.id, exc)
                return _failure(
                    agent_pb2.ExecuteResponse.FAILED,
                    "edge_unreachable",
                    str(exc),
                )

        start = time.monotonic()
        try:
            resp = await self._cloud_call(
                step.endpoint, step.intent, step.slots, ctx, step.meta)
            elapsed = (time.monotonic() - start) * 1000
            metrics.record_agent_call(
                step.agent_id, elapsed,
                resp.status == agent_pb2.ExecuteResponse.OK)
            return resp
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            metrics.record_agent_call(step.agent_id, elapsed, False)
            raise
