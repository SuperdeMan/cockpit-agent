"""PlannerEngine：编排主循环（规划→校验→执行→聚合）。

WS3 §3。串联 planning / executor / aggregator / session。
多轮确认闭环（F1）：NEED_CONFIRM 挂起后，确认轮只重跑挂起步骤（已完成结果种子化），
且 confirmed 标记严格限定在挂起那一步——后续 require_confirm 步骤各自再走确认（架构 §9.1）。
"""
from __future__ import annotations
import logging
from typing import AsyncIterator

from .models import Plan, Step, StepResult, StepStatus, PlanContext, SessionState
from .planning import PlanBuilder
from .executor import DagExecutor
from .aggregator import Aggregator
from .session import SessionStore
from security.permission import PermissionEngine, AuthContext

logger = logging.getLogger("planner.engine")

# 确认/取消话术词表（语音兜底；HMI 确认按钮走 is_confirmation 显式标记）
_YES_WORDS = ("确认", "确定", "好的", "好啊", "可以", "订吧", "订了", "是的",
              "嗯", "行", "ok", "付吧", "支付", "下单", "就这家", "就它")
_NO_WORDS = ("取消", "不用", "不要", "算了", "不订", "不付", "不了", "别订", "先不")

_RESULT_FIELDS = {"step_id", "status", "speech", "ui_card", "actions",
                  "follow_up", "data", "missing_slots", "error"}


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
        text = (getattr(request, "text", "") or "").strip()

        plan: Plan | None = None
        seed_results: list[StepResult] = []

        # A. 多轮续接：存在挂起的待确认会话时，判定本轮是否在回应确认
        pending = await self.session.load(ctx.session_id)
        if pending and pending.phase == "wait_confirm":
            reply = self._confirm_reply(text, ctx.is_confirmation)
            if reply == "no":
                await self.session.clear(ctx.session_id)
                yield {"kind": "final", "speech": "好的，已为您取消。"}
                return
            if reply == "yes":
                plan, seed_results = self._restore(pending)
                if plan is None:
                    await self.session.clear(ctx.session_id)
                    yield {"kind": "final",
                           "speech": "刚才的操作已过期，麻烦您再说一遍需求。"}
                    return
                logger.info("Resuming plan for session %s (confirm step %s)",
                            ctx.session_id, pending.pending_step_id)
            else:
                # 答非所问：用户换了话题，丢弃挂起任务，按新请求处理
                await self.session.clear(ctx.session_id)
        elif pending and pending.phase == "wait_slot":
            # F12：补槽续接——把用户文本填入挂起 step 的 missing_slots，然后恢复执行
            plan, seed_results = self._restore(pending)
            if plan is None:
                await self.session.clear(ctx.session_id)
                yield {"kind": "final",
                       "speech": "刚才的操作已过期，麻烦您再说一遍需求。"}
                return
            # Phase 1 简单版：直接用用户原始文本填 slot（Agent LLM 能理解自然语言）
            for step in plan.steps:
                if step.id == pending.pending_step_id:
                    for slot_name in (pending.missing_slots or []):
                        step.slots[slot_name] = text
                    break
            logger.info("Resuming plan for session %s (slot fill step %s, text=%s)",
                        ctx.session_id, pending.pending_step_id, text[:20])
        elif ctx.is_confirmation:
            # 带确认标记但没有挂起任务（TTL 过期 / 重复点击）
            yield {"kind": "final",
                   "speech": "当前没有待确认的操作。需要我帮您做什么？"}
            return

        if plan is None:
            # B. 新规划
            agents = await self.clients.list_agents()
            plan = await self.planner.build(text, agents, ctx,
                                            granted_permissions=ctx.granted_permissions)

            if not plan.steps:
                yield {"kind": "final", "speech": "抱歉，我暂时无法处理这个请求。"}
                return

            # C. 解析 endpoint（Registry）
            await self._resolve_endpoints(plan)

            # C2. 权限校验（F2）：执行前对每个 step 做强制校验
            plan = self._enforce_permissions(plan, ctx)

        # D. 执行 DAG（确认续接时：已完成结果作种子，只跑剩余步骤）
        done_seed = {r.step_id: r for r in seed_results}
        results: list[StepResult] = list(seed_results)
        async for step_result in self.executor.run(plan, ctx, done=done_seed):
            results.append(step_result)

            # 挂起：需确认/需补槽
            if step_result.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                await self.session.save(ctx.session_id, SessionState(
                    phase="wait_confirm" if step_result.status == StepStatus.NEED_CONFIRM else "wait_slot",
                    pending_step_id=step_result.step_id,
                    missing_slots=list(step_result.missing_slots),  # F12：保存缺失槽位名
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
        final = await self.aggregator.compose(text or plan.raw_text, results)
        yield {"kind": "final", **final}

    def _build_context(self, request) -> PlanContext:
        # granted_permissions 来源：HandleRequest.meta["granted_scopes"]（逗号分隔）
        # PoC 阶段由 Edge Gateway 注入；量产换成 token scope（WS4）
        meta = dict(getattr(request, "meta", {}) or {})
        raw_scopes = meta.get("granted_scopes", "")
        granted = [s.strip() for s in raw_scopes.split(",") if s.strip()] if raw_scopes else []

        return PlanContext(
            request_id=getattr(request, "request_id", ""),
            session_id=getattr(request, "session_id", ""),
            user_id=getattr(request.context, "user_id", "") if hasattr(request, "context") and request.context else "",
            vehicle_id=getattr(request.context, "vehicle_id", "") if hasattr(request, "context") and request.context else "",
            is_confirmation=getattr(request, "is_confirmation", False),
            granted_permissions=granted,
        )

    @staticmethod
    def _confirm_reply(text: str, flagged: bool) -> str | None:
        """判定本轮是否在回应待确认任务。返回 "yes" | "no" | None（答非所问）。

        否定词优先（"确认取消"按取消处理）；HMI 按钮带显式标记即肯定；
        语音兜底只认短肯定话术，避免长句误判成确认。
        """
        t = (text or "").strip().lower()
        if any(k in t for k in _NO_WORDS):
            return "no"
        if flagged:
            return "yes"
        if t and len(t) <= 8 and any(k in t for k in _YES_WORDS):
            return "yes"
        return None

    def _restore(self, state: SessionState) -> tuple[Plan | None, list[StepResult]]:
        """从挂起态恢复计划与已完成结果。

        挂起步骤本身（NEED_CONFIRM/NEED_SLOT 那条）不进种子——它要带 confirmed 标记重跑；
        confirmed 只注入挂起那一步，不污染后续 require_confirm 步骤。
        """
        try:
            steps = [Step(**s) for s in state.pending_plan.get("steps", [])]
            if not steps:
                return None, []

            for s in steps:
                if s.id == state.pending_step_id:
                    s.meta = {**s.meta, "confirmed": "true"}

            seeds: list[StepResult] = []
            for sid, d in (state.completed_results or {}).items():
                if sid == state.pending_step_id:
                    continue
                d = {k: v for k, v in dict(d).items() if k in _RESULT_FIELDS}
                d["status"] = StepStatus(d.get("status", "ok"))
                if d["status"] in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    continue
                seeds.append(StepResult(**d))

            return Plan(steps=steps, raw_text=state.pending_plan.get("raw_text", "")), seeds
        except Exception as e:
            logger.warning("Failed to restore plan: %s", e)
            return None, []

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

    def _enforce_permissions(self, plan: Plan, ctx: PlanContext) -> Plan:
        """F2：执行层权限校验兜底。

        权限校验主要在规划阶段完成（planning._filter_by_permission，fail-closed），
        此处作为执行层二次校验：记录越权告警，供 Phase 2 扩展为硬拒绝。
        当前因 Step 不含 manifest，无法做运行时权限判定，依赖规划阶段过滤。
        Phase 2：Step 增加 manifest 缓存 → 此处可做 perms.check() 硬拒绝。
        """
        if ctx.granted_permissions:
            logger.debug("Permission enforcement: %d steps, granted=%s",
                         len(plan.steps), ctx.granted_permissions)
        return plan

    @staticmethod
    def _serialize_plan(plan: Plan) -> dict:
        # meta 故意不持久化：confirmed 标记只在确认那一轮由 _restore 注入，防止重放
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
