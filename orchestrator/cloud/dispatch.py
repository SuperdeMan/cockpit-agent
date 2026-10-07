"""Unified step dispatcher for cloud agents, vehicle edge executors, and tools."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time

from google.protobuf import json_format

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from observability import events as obs_events
from observability.metrics import metrics
from security.audit import AuditLogger
from security.permission import check_permission
from runtime.capability_contract import argument_error, rejected

from .circuit import CircuitBreakerManager
from .models import PlanContext, Step, step_call_context

logger = logging.getLogger("planner.dispatch")

_audit = AuditLogger()


def _deadline_exceeded(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    try:
        return callable(code) and getattr(code(), "name", "") == "DEADLINE_EXCEEDED"
    except Exception:
        return False


#: 步骤 span 里的错误码与拒绝原因（CA2-19 §8.4 登记）：错误码、契约拒绝的原因码都由系统生成，只收标识符形状的值——
#: Agent 自己写的任意文字（可能夹带第三方内容）不进 span。权限拒绝缺哪些权限已有审计日志，这里只记错误码。
_ERROR_CODE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_REJECT_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,47}(?::[A-Za-z0-9_.]{1,48})?")
_CONTRACT_REJECTED = "capability_contract_rejected"


def error_attrs(response) -> dict:
    """ExecuteResponse 的错误码（契约拒绝再带原因码）→ 步骤 span 属性；没有或不是标识符形状 ⇒ 空。

    三处步骤 span（调度层、D0 流式、T2 流式）共用这一份。此前三处都只记 ok / err：因契约或权限被拒的步
    在线上只是一个 err，统计不到（S4 迁严格契约时只能拿规划记录间接估算）。
    """
    error = getattr(response, "error", None)
    code = str(getattr(error, "code", "") or "")
    if not _ERROR_CODE_RE.fullmatch(code):
        return {}
    attrs = {"error_code": code}
    reason = str(getattr(error, "message", "") or "")
    if code == _CONTRACT_REJECTED and _REJECT_REASON_RE.fullmatch(reason):
        attrs["reject_reason"] = reason
    return attrs


def _failure(status: int, code: str, message: str) -> agent_pb2.ExecuteResponse:
    return agent_pb2.ExecuteResponse(
        status=status,
        speech="",
        error=common_pb2.ErrorInfo(code=code, message=message),
    )


class UnifiedDispatcher:
    """Route one plan step without exposing transport details to the executor."""

    def __init__(self, cloud_call, edge_call, tools=None, breakers=None, edge_query=None):
        self._cloud_call = cloud_call
        self._edge_call = edge_call
        # CA2-11: read-only lookup in the vehicle's operation log (None = not wired).
        self._edge_query = edge_query
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
        raw_text_from: str = "",
        errors: dict | None = None,
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
                    # W16-b 可观测：这一步读的是自己的起点原话而不是本轮原话（只在换了时出现）
                    **({"raw_text_from": raw_text_from} if raw_text_from else {}),
                    **(errors or {}),
                },
            )
            snapshot = metrics.agent_snapshot(step.agent_id)
            if snapshot:
                # 熔断状态并入 Agent 指标供 Dashboard 展示（peek snapshot 不创建新 breaker）
                cb = self._breakers.snapshot().get(step.endpoint or step.agent_id)
                snapshot["circuit"] = cb["state"] if cb else "closed"
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
            raw_text_from=("origin" if step_call_context(step, ctx) is not ctx else ""),
            errors=error_attrs(response),
        )
        return response

    async def query_edge_operation(self, step: Step, ctx: PlanContext, timeout: float) -> str:
        """CA2-11: what the vehicle's operation log says about this step's operation.

        Returns the recorded status (done / failed / accepted / orphaned / cancelled), "absent"
        when the vehicle never received it, or "" when the vehicle could not be asked.
        Read-only on the vehicle: the query never reaches VAL.
        """
        if self._edge_query is None or step.deployment != "edge" or not ctx.vehicle_id                 or not getattr(step, "operation_id", ""):
            return ""
        try:
            resp = await asyncio.wait_for(
                self._edge_query(ctx.vehicle_id, step.operation_id, step_id=f"{step.id}-recover",
                                 timeout=timeout), timeout=timeout)
        except Exception as exc:
            logger.warning("Edge operation query for step %s failed: %s", step.id, type(exc).__name__)
            return ""
        if resp.status != agent_pb2.ExecuteResponse.OK:
            return ""
        record = json_format.MessageToDict(resp.data).get("_operation") if resp.HasField("data") else None
        status = record.get("status") if isinstance(record, dict) else ""
        return status if isinstance(status, str) else ""

    async def dispatch(self, step: Step, ctx: PlanContext):
        reason = argument_error(step.capability_contract, step.slots)
        if reason:
            return await self._finish(step, ctx, rejected(reason))
        # 权限校验（执行期硬拒）：与规划期 catalog 过滤同源 check_permission——
        # third_party/tool 车控硬禁令 + required 父子覆盖判定，越权步在传输前 REJECTED。
        decision = check_permission(
            agent_id=step.agent_id, trust_level=step.trust_level,
            required=step.required_permissions,
            granted=ctx.granted_permissions, kind=step.kind)
        if not decision.allowed:
            _audit.permission_denied(
                step.agent_id, decision.missing, trace_id=getattr(ctx, 'trace_id', ''))
            metrics.record_agent_call(step.agent_id, 0, False)
            return await self._finish(
                step,
                ctx,
                _failure(
                    agent_pb2.ExecuteResponse.REJECTED,
                    "permission_denied",
                    decision.reason,
                ),
            )

        if step.kind == "tool":
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
                if _deadline_exceeded(exc):
                    # CA2-11: the vehicle got the command and did not answer in time. Whether it ran
                    # is unknown, which is the executor's timeout, not "unreachable" (never ran).
                    logger.warning("Edge step %s: no answer before the deadline", step.id)
                    await self._finish(step, ctx, _failure(agent_pb2.ExecuteResponse.FAILED,
                                                           "edge_timeout", "deadline exceeded"), elapsed)
                    raise asyncio.TimeoutError() from exc
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
            # W16-b：续接轮里的下游步读自己的起点原话（`models.step_raw_text`）；新计划零拷贝
            resp = await self._cloud_call(
                step.endpoint, step.intent, step.slots, step_call_context(step, ctx), step.meta,
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
