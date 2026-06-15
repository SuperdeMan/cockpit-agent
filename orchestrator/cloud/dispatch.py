"""Unified step dispatcher for cloud agents, vehicle edge executors, and tools."""
from __future__ import annotations

import logging
import time

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from observability import events as obs_events
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

    @staticmethod
    def _step_node(step: Step) -> str:
        if step.kind == "tool":
            return f"step.tool:{step.intent}"
        if step.deployment == "edge":
            return f"step.edge:{step.intent}"
        return f"step.agent:{step.agent_id}"

    async def _emit_step(
        self,
        step: Step,
        ctx: PlanContext,
        ok: bool,
        elapsed_ms: float,
    ) -> None:
        try:
            emitter = obs_events.get_emitter("cloud")
            await emitter.emit_span(
                getattr(ctx, "trace_id", ""),
                self._step_node(step),
                status="ok" if ok else "err",
                duration_ms=elapsed_ms,
                attrs={
                    "intent": step.intent,
                    "agent_id": step.agent_id,
                    "kind": step.kind,
                    "deployment": step.deployment,
                },
            )
            snapshot = metrics.agent_snapshot(step.agent_id)
            if snapshot:
                await emitter.emit_metric(step.agent_id, **snapshot)
        except Exception:
            pass

    async def _finish(
        self,
        step: Step,
        ctx: PlanContext,
        response: agent_pb2.ExecuteResponse,
        elapsed_ms: float = 0,
    ) -> agent_pb2.ExecuteResponse:
        await self._emit_step(
            step,
            ctx,
            response.status == agent_pb2.ExecuteResponse.OK,
            elapsed_ms,
        )
        return response

    async def dispatch(self, step: Step, ctx: PlanContext):
        required = list(step.required_permissions or [])
        if step.trust_level == "third_party" and any(
                permission == "vehicle.control" or
                permission.startswith("vehicle.control.")
                for permission in required):
            _audit.permission_denied(
                step.agent_id, required, trace_id=getattr(ctx, 'trace_id', ''))
            metrics.record_agent_call(step.agent_id, 0, False)
            return await self._finish(
                step,
                ctx,
                _failure(
                    agent_pb2.ExecuteResponse.REJECTED,
                    "permission_denied",
                    "third_party agents cannot request vehicle.control",
                ),
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
            return await self._finish(
                step,
                ctx,
                _failure(
                    agent_pb2.ExecuteResponse.REJECTED,
                    "permission_denied",
                    f"missing permissions: {', '.join(missing)}",
                ),
            )

        if step.kind == "tool":
            if any(p == "vehicle.control" or p.startswith("vehicle.control.")
                   for p in required):
                _audit.permission_denied(
                    step.agent_id, required,
                    trace_id=getattr(ctx, 'trace_id', ''))
                return await self._finish(
                    step,
                    ctx,
                    _failure(
                        agent_pb2.ExecuteResponse.REJECTED,
                        "permission_denied",
                        "tools cannot request vehicle.control",
                    ),
                )
            if self._tools is None:
                return await self._finish(
                    step,
                    ctx,
                    _failure(
                        agent_pb2.ExecuteResponse.FAILED,
                        "tool_unavailable",
                        f"tool registry unavailable for {step.intent}",
                    ),
                )
            start = time.monotonic()
            try:
                resp = await self._tools.call(step.intent, step.slots, ctx)
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(
                    step.agent_id, elapsed,
                    resp.status == agent_pb2.ExecuteResponse.OK)
                return await self._finish(step, ctx, resp, elapsed)
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(step.agent_id, elapsed, False)
                logger.warning("Tool %s failed: %s", step.intent, exc)
                return await self._finish(
                    step,
                    ctx,
                    _failure(
                        agent_pb2.ExecuteResponse.FAILED,
                        "tool_error",
                        str(exc),
                    ),
                    elapsed,
                )

        if step.deployment == "edge":
            if not ctx.vehicle_id:
                metrics.record_agent_call(step.agent_id, 0, False)
                return await self._finish(
                    step,
                    ctx,
                    _failure(
                        agent_pb2.ExecuteResponse.FAILED,
                        "edge_unreachable",
                        "missing vehicle_id",
                    ),
                )
            start = time.monotonic()
            try:
                resp = await self._edge_call(ctx.vehicle_id, step, ctx)
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(
                    step.agent_id, elapsed,
                    resp.status == agent_pb2.ExecuteResponse.OK)
                return await self._finish(step, ctx, resp, elapsed)
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                metrics.record_agent_call(step.agent_id, elapsed, False)
                logger.warning("Edge step %s failed: %s", step.id, exc)
                return await self._finish(
                    step,
                    ctx,
                    _failure(
                        agent_pb2.ExecuteResponse.FAILED,
                        "edge_unreachable",
                        str(exc),
                    ),
                    elapsed,
                )

        start = time.monotonic()
        try:
            resp = await self._cloud_call(
                step.endpoint, step.intent, step.slots, ctx, step.meta)
            elapsed = (time.monotonic() - start) * 1000
            metrics.record_agent_call(
                step.agent_id, elapsed,
                resp.status == agent_pb2.ExecuteResponse.OK)
            return await self._finish(step, ctx, resp, elapsed)
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            metrics.record_agent_call(step.agent_id, elapsed, False)
            await self._emit_step(step, ctx, False, elapsed)
            raise
