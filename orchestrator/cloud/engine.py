"""PlannerEngine：编排主循环（规划→校验→执行→聚合）。

WS3 §3。串联 planning / executor / aggregator / session。
"""
from __future__ import annotations
import logging
from typing import AsyncIterator

from .models import Plan, StepResult, StepStatus, PlanContext, SessionState
from .planning import PlanBuilder
from .executor import DagExecutor
from .aggregator import Aggregator
from .session import SessionStore
from security.permission import PermissionEngine, AuthContext

logger = logging.getLogger("planner.engine")


class PlannerEngine:
    """编排主循环。engine 是唯一持有全局状态的地方。"""

    def __init__(self, clients, planner: PlanBuilder, executor: DagExecutor,
                 aggregator: Aggregator, session: SessionStore,
                 perms: PermissionEngine):
        self.clients = clients
        self.planner = planner
        self.executor = executor
        self.aggregator = aggregator
        self.session = session
        self.perms = perms

    async def run(self, request) -> AsyncIterator[dict]:
        """编排主循环。yield 事件：{"kind": "speech"|"action"|"final", ...}"""
        ctx = self._build_context(request)

        # A. 多轮续接
        pending = await self.session.load(ctx.session_id)
        if pending and getattr(request, "is_confirmation", False):
            plan = self._resume_plan(pending, request)
            if plan:
                logger.info("Resuming plan for session %s", ctx.session_id)
            else:
                pending = None  # 续接失败，当新请求处理

        if not pending or not getattr(request, "is_confirmation", False):
            # B. 新规划
            agents = await self.clients.list_agents()
            plan = await self.planner.build(request.text, agents, ctx,
                                            granted_permissions=ctx.granted_permissions)

            if not plan.steps:
                yield {"kind": "final", "speech": "抱歉，我暂时无法处理这个请求。"}
                return

            # C. 解析 endpoint（Registry）
            await self._resolve_endpoints(plan)

        # D. 执行 DAG
        results: list[StepResult] = []
        async for step_result in self.executor.run(plan, ctx):
            results.append(step_result)

            # 挂起：需确认/需补槽
            if step_result.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                await self.session.save(ctx.session_id, SessionState(
                    phase="wait_confirm" if step_result.status == StepStatus.NEED_CONFIRM else "wait_slot",
                    pending_step_id=step_result.step_id,
                    completed_results={r.step_id: r.__dict__ for r in results},
                    pending_plan=self._serialize_plan(plan),
                ))
                yield {
                    "kind": "final",
                    "speech": step_result.speech,
                    "follow_up": step_result.follow_up,
                    "actions": step_result.actions,
                    "need_confirm": step_result.status == StepStatus.NEED_CONFIRM,
                }
                return

        # E. 聚合 + 输出
        await self.session.clear(ctx.session_id)
        final = await self.aggregator.compose(request.text, results)
        yield {"kind": "final", **final}

    def _build_context(self, request) -> PlanContext:
        return PlanContext(
            request_id=getattr(request, "request_id", ""),
            session_id=getattr(request, "session_id", ""),
            user_id=getattr(request.context, "user_id", "") if hasattr(request, "context") and request.context else "",
            vehicle_id=getattr(request.context, "vehicle_id", "") if hasattr(request, "context") and request.context else "",
            is_confirmation=getattr(request, "is_confirmation", False),
        )

    def _resume_plan(self, state: SessionState, request) -> Plan | None:
        """从挂起态恢复计划。"""
        try:
            plan_data = state.pending_plan
            steps = [Step(**s) for s in plan_data.get("steps", [])]
            # 恢复已完成 step 的结果
            return Plan(steps=steps, raw_text=plan_data.get("raw_text", ""))
        except Exception as e:
            logger.warning("Failed to resume plan: %s", e)
            return None

    async def _resolve_endpoints(self, plan: Plan):
        """为 plan 中没有 endpoint 的 step 解析 endpoint。"""
        for step in plan.steps:
            if step.endpoint:
                continue
            try:
                agents = await self.clients.resolve(query=step.intent, top_k=1)
                if agents:
                    step.endpoint = agents[0].endpoint
                else:
                    logger.warning("No agent found for intent %s", step.intent)
            except Exception as e:
                logger.warning("Resolve failed for %s: %s", step.intent, e)

    @staticmethod
    def _serialize_plan(plan: Plan) -> dict:
        return {
            "steps": [
                {"id": s.id, "agent_id": s.agent_id, "endpoint": s.endpoint,
                 "intent": s.intent, "slots": s.slots, "depends_on": s.depends_on,
                 "slot_refs": s.slot_refs, "require_confirm": s.require_confirm,
                 "latency_budget_ms": s.latency_budget_ms}
                for s in plan.steps
            ],
            "raw_text": plan.raw_text,
        }
