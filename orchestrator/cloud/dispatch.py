"""Unified step dispatcher for cloud agents, vehicle edge executors, and tools."""
from __future__ import annotations

import logging
import os
import time

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from observability import events as obs_events
from observability.metrics import metrics
from security.audit import AuditLogger
from security.scopes import is_scope_covered

from .circuit import CircuitBreakerManager
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

    def __init__(self, cloud_call, edge_call, tools=None, breakers=None):
        self._cloud_call = cloud_call
        self._edge_call = edge_call
        self._tools = tools
        # 熔断：按 endpoint 隔离故障 Agent。连续失败 N 次 → 打开，后续调用快速失败，
        # 不再每次吃满 latency_budget 超时（"服务超时"放大器）；冷却后半开探测自愈。
        self._breakers = breakers if breakers is not None else CircuitBreakerManager(
            failure_threshold=int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5") or 5),
            recovery_timeout=float(os.getenv("CIRCUIT_RECOVERY_TIMEOUT_S", "30") or 30),
        )

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
        pending: bool = False,
    ) -> None:
        try:
            emitter = obs_events.get_emitter("cloud")
            await emitter.emit_span(
                getattr(ctx, "trace_id", ""),
                self._step_node(step),
                status="wait" if pending else ("ok" if ok else "err"),
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
        st = response.status
        pending = st in (
            agent_pb2.ExecuteResponse.NEED_CONFIRM,
            agent_pb2.ExecuteResponse.NEED_SLOT,
        )
        await self._emit_step(
            step, ctx,
            st == agent_pb2.ExecuteResponse.OK,
            elapsed_ms,
            pending=pending,
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
            breaker = self._breakers.get(f"edge:{ctx.vehicle_id}")
            if not breaker.allow():
                metrics.record_agent_call(step.agent_id, 0, False)
                logger.warning("Circuit open for edge vehicle %s, fast-failing", ctx.vehicle_id)
                return await self._finish(
                    step,
                    ctx,
                    _failure(
                        agent_pb2.ExecuteResponse.FAILED,
                        "edge_unreachable",
                        "车端暂时不可达（熔断保护中），请稍后重试。",
                    ),
                )
            start = time.monotonic()
            try:
                resp = await self._edge_call(ctx.vehicle_id, step, ctx)
                elapsed = (time.monotonic() - start) * 1000
                breaker.record_success()
                metrics.record_agent_call(
                    step.agent_id, elapsed,
                    resp.status == agent_pb2.ExecuteResponse.OK)
                return await self._finish(step, ctx, resp, elapsed)
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                breaker.record_failure()
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

        breaker = self._breakers.get(step.endpoint or step.agent_id)
        if not breaker.allow():
            metrics.record_agent_call(step.agent_id, 0, False)
            logger.warning("Circuit open for %s (%s), fast-failing",
                           step.agent_id, step.endpoint)
            return await self._finish(
                step,
                ctx,
                _failure(
                    agent_pb2.ExecuteResponse.REJECTED,
                    "circuit_open",
                    f"{step.agent_id} 暂时不可用（熔断保护中），请稍后重试。",
                ),
            )
        start = time.monotonic()
        try:
            # 用 step 自己的 latency_budget 作 Execute 超时（原固定 10s 会卡死慢 Agent，
            # 尤其开思考后）。下限兜底，缺省/异常仍走默认。
            budget_ms = getattr(step, "latency_budget_ms", 0) or 0
            timeout = max(budget_ms / 1000.0, 10.0) if budget_ms else 10.0
            resp = await self._cloud_call(
                step.endpoint, step.intent, step.slots, ctx, step.meta,
                timeout=timeout, context_scopes=step.context_scopes)
            elapsed = (time.monotonic() - start) * 1000
            # 收到响应=endpoint 存活（业务 FAILED 不算 endpoint 故障，不误触熔断）。
            breaker.record_success()
            metrics.record_agent_call(
                step.agent_id, elapsed,
                resp.status == agent_pb2.ExecuteResponse.OK)
            return await self._finish(step, ctx, resp, elapsed)
        except Exception as exc:
            # 不再 re-raise：单个 Agent 超时/不可达降级为 FAILED step，不炸整条 DAG
            # （executor 已容忍失败 step）；记熔断，连续失败后快速失败省掉满超时等待。
            elapsed = (time.monotonic() - start) * 1000
            breaker.record_failure()
            metrics.record_agent_call(step.agent_id, elapsed, False)
            logger.warning("Cloud agent %s failed: %s", step.agent_id, exc)
            return await self._finish(
                step,
                ctx,
                _failure(
                    agent_pb2.ExecuteResponse.FAILED,
                    "agent_unreachable",
                    str(exc),
                ),
                elapsed,
            )
