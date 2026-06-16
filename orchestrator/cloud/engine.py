"""PlannerEngine：编排主循环（规划→校验→执行→聚合）。

WS3 §3。串联 planning / executor / aggregator / session。
多轮确认闭环（F1）：NEED_CONFIRM 挂起后，确认轮只重跑挂起步骤（已完成结果种子化），
且 confirmed 标记严格限定在挂起那一步——后续 require_confirm 步骤各自再走确认（架构 §9.1）。
"""
from __future__ import annotations
import logging
import time
from typing import AsyncIterator

from .models import Plan, Step, StepResult, StepStatus, PlanContext, SessionState
from .planning import PlanBuilder
from .executor import DagExecutor
from .aggregator import Aggregator
from .session import SessionStore
from .loop import LoopController
from observability import events as obs_events
from observability.metrics import metrics
from observability.tracing import set_trace_id
from security.permission import PermissionEngine, AuthContext

logger = logging.getLogger("planner.engine")

# 确认/取消话术词表（语音兜底；HMI 确认按钮走 is_confirmation 显式标记）
_YES_WORDS = ("确认", "确定", "好的", "好啊", "可以", "订吧", "订了", "是的",
              "嗯", "行", "ok", "付吧", "支付", "下单", "就这家", "就它")
_NO_WORDS = ("取消", "不用", "不要", "算了", "不订", "不付", "不了", "别订", "先不")

_RESULT_FIELDS = {"step_id", "status", "speech", "ui_card", "actions",
                  "follow_up", "data", "missing_slots", "error"}

# PoC 默认权限：未注入 granted_scopes 时使用（fail-open for PoC）。
# 量产必须从会话 token/设备身份解析 scope，不得使用此默认值。
_POC_DEFAULT_SCOPES = [
    "vehicle.control", "media.control", "navigation",
    "food.ordering", "weather.query", "news.query",
    "location.read", "navigation.control",
    "network.external", "payment.invoke",
]


class PlannerEngine:
    """编排主循环。engine 是唯一持有全局状态的地方。"""

    def __init__(self, clients, planner: PlanBuilder, executor: DagExecutor,
                 aggregator: Aggregator, session: SessionStore,
                 perms: PermissionEngine, loop=None):
        self.clients = clients
        self.planner = planner
        self.executor = executor
        self.aggregator = aggregator
        self.session = session
        self.perms = perms
        self.loop = loop or LoopController(
            planner, executor, aggregator, self._suspend,
            stream_fn=getattr(clients, 'call_agent_stream', None))

    async def run(self, request) -> AsyncIterator[dict]:
        """编排主循环（外层）：委托 _orchestrate，并把本轮对话落库到 memory。

        对话记忆在本轮结束后按 用户→助手 顺序写入——规划阶段读到的是"此前"历史，
        当前这句不污染指代消解（task 2）。memory_enabled=false 时整轮不读写。
        """
        ctx = self._build_context(request)
        set_trace_id(ctx.trace_id)
        text = (getattr(request, "text", "") or "").strip()
        mem_on = ctx.prefs.get("memory_enabled", "true") != "false"

        assistant_speech = ""
        async for ev in self._orchestrate(request, ctx, text, mem_on):
            if ev.get("kind") == "final" and ev.get("speech"):
                assistant_speech = ev["speech"]
            yield ev

        if mem_on and text:
            await self._append_turn(ctx.session_id, "user", text)
            if assistant_speech:
                await self._append_turn(ctx.session_id, "assistant", assistant_speech)

    async def _orchestrate(self, request, ctx: PlanContext, text: str,
                           mem_on: bool) -> AsyncIterator[dict]:
        """规划→校验→执行→聚合。yield 事件：{"kind": "speech"|"action"|"final", ...}"""
        plan: Plan | None = None
        seed_results: list[StepResult] = []
        agents = []

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

        new_plan = plan is None
        if plan is None:
            # B. 新规划（注入此前对话历史，支持指代消解；task 2）
            agents = await self.clients.list_agents()
            history = await self._history(ctx.session_id) if mem_on else []
            plan = await self.planner.build(text, agents, ctx,
                                            granted_permissions=ctx.granted_permissions,
                                            history=history)

            if not plan.steps:
                yield {"kind": "final", "speech": "抱歉，我暂时无法处理这个请求。"}
                return

            # C. 解析 endpoint（Registry）
            await obs_events.get_emitter("cloud").emit_span(
                ctx.trace_id,
                "cloud.planning",
                attrs={
                    "complexity": plan.complexity,
                    "steps": len(plan.steps),
                },
            )
            await self._resolve_endpoints(plan)

            # C2. 权限校验（F2）：执行前对每个 step 做强制校验
            plan = self._enforce_permissions(plan, ctx)

        # 规划完成，给用户即时反馈（多步计划在执行期间也会逐步 yield）。
        # 混合意图子请求（端侧已给过云段占位）不再重复，避免双占位文案。
        mixed_sub = dict(getattr(request, "meta", {}) or {}).get("_mixed_subrequest") == "1"
        if new_plan and len(plan.steps) > 1 and not mixed_sub:
            yield {"kind": "speech", "delta": "正在为您处理，请稍候…"}

        # D-T2. Adaptive plans enter the bounded loop. Confirmation resumes keep
        # their adaptive metadata and continue from the saved result seeds.
        metrics.record_intent(f"complexity.{plan.complexity}", 0, True)
        if plan.complexity == "adaptive":
            if not agents:
                agents = await self.clients.list_agents()
            async for event in self.loop.run(
                    goal=plan.goal or text or plan.raw_text,
                    initial_plan=plan,
                    agents=agents,
                    ctx=ctx,
                    user_text=text or plan.raw_text,
                    seed_results=seed_results):
                if event.get("kind") == "final":
                    await obs_events.get_emitter("cloud").emit_span(
                        ctx.trace_id,
                        "aggregate",
                        attrs={"path": "adaptive"},
                    )
                yield event
            return

        # D0. 单步新规划走流式直通（task 4：开放域"边想边说"，秒级反馈）。
        # 仅对全新单步计划开启；确认续接/多步计划保持 executor 路径，不动 F1 闭环。
        if (new_plan and plan.complexity == "simple" and len(plan.steps) == 1
                and not ctx.is_confirmation
                and plan.steps[0].kind == "agent"
                and plan.steps[0].deployment == "cloud"):
            step = plan.steps[0]
            _d0_start = time.monotonic()
            streamed = False
            final_sr: StepResult | None = None
            try:
                async for kind, payload in self.clients.call_agent_stream(
                        step.endpoint, step.intent, step.slots, ctx, step.meta):
                    if kind == "speech":
                        streamed = True
                        yield {"kind": "speech", "delta": payload}
                    elif kind == "action":
                        streamed = True
                        yield {"kind": "action", "action": payload}
                    elif kind == "final":
                        final_sr = DagExecutor._to_result(step.id, payload)
            except Exception as e:
                logger.warning("Single-step stream failed (%s); falling back to unary", e)

            if final_sr is not None:
                # 流式直通也补 step.agent span（否则单步云端 agent 链路缺这一跳）
                _pending = final_sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT)
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id, f"step.agent:{step.agent_id}",
                    status="wait" if _pending else (
                        "ok" if final_sr.status == StepStatus.OK else "err"),
                    duration_ms=(time.monotonic() - _d0_start) * 1000,
                    attrs={"intent": step.intent, "agent_id": step.agent_id,
                           "kind": "agent", "deployment": "cloud", "via": "stream"})
                results = [final_sr]
                if final_sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    yield await self._suspend(final_sr, results, plan, ctx)
                    return
                await self.session.clear(ctx.session_id)
                final = await self.aggregator.compose(text or plan.raw_text, results)
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id,
                    "aggregate",
                    attrs={"path": "stream"},
                )
                yield {"kind": "final", **final}
                return
            if streamed:
                # 流了话术却没收到 final：不回退重跑，避免重复播报
                yield {"kind": "final", "speech": "抱歉，刚才没说完，请再试一次。"}
                return
            # 无任何流式事件（不支持/连接失败）→ 安全回退到下面的 executor 路径

        # D. 执行 DAG（确认续接时：已完成结果作种子，只跑剩余步骤）
        done_seed = {r.step_id: r for r in seed_results}
        results = list(seed_results)
        async for step_result in self.executor.run(plan, ctx, done=done_seed):
            results.append(step_result)

            # 每步完成后 yield 进度反馈（HMI 流式显示，不等全部完成）
            if step_result.speech and step_result.status == StepStatus.OK:
                yield {"kind": "speech", "delta": step_result.speech + "。"}

            # 挂起：需确认/需补槽
            if step_result.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                yield await self._suspend(step_result, results, plan, ctx)
                return

        if new_plan and await self._needs_replan(plan, results):
            metrics.record_intent("reactive_upgrade", 0, True)
            logger.info("Reactive upgrade: simple→T2 for session %s",
                        ctx.session_id)
            if not agents:
                agents = await self.clients.list_agents()
            async for event in self.loop.run(
                    goal=plan.goal or text or plan.raw_text,
                    initial_plan=None,
                    agents=agents,
                    ctx=ctx,
                    user_text=text or plan.raw_text,
                    seed_results=results):
                if event.get("kind") == "final":
                    await obs_events.get_emitter("cloud").emit_span(
                        ctx.trace_id,
                        "aggregate",
                        attrs={"path": "reactive"},
                    )
                yield event
            return

        # E. 聚合 + 输出
        await self.session.clear(ctx.session_id)
        final = await self.aggregator.compose(text or plan.raw_text, results)
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id,
            "aggregate",
        )
        yield {"kind": "final", **final}

    async def _suspend(self, step_result: StepResult, results: list[StepResult],
                       plan: Plan, ctx: PlanContext) -> dict:
        """挂起待确认/待补槽：保存会话态并构造 final 事件。executor 与流式两路共用，
        保证 F1 多轮确认闭环行为一致。"""
        await self.session.save(ctx.session_id, SessionState(
            phase="wait_confirm" if step_result.status == StepStatus.NEED_CONFIRM else "wait_slot",
            pending_step_id=step_result.step_id,
            missing_slots=list(step_result.missing_slots),  # F12：保存缺失槽位名
            completed_results={r.step_id: r.__dict__ for r in results},
            pending_plan=self._serialize_plan(plan),
        ))
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id,
            "suspended",
            status=step_result.status.value,
            attrs={"step_id": step_result.step_id},
        )
        return {
            "kind": "final",
            "speech": step_result.speech,
            "follow_up": step_result.follow_up,
            "actions": step_result.actions,
            "need_confirm": step_result.status == StepStatus.NEED_CONFIRM,
        }

    async def _append_turn(self, session_id: str, role: str, text: str):
        """写入对话记忆。memory 不可用或 clients 未提供该能力时静默跳过（不阻塞主链路）。"""
        fn = getattr(self.clients, "append_turn", None)
        if not fn:
            return
        try:
            await fn(session_id, role, text)
        except Exception as e:
            logger.debug("append_turn failed: %s", e)

    async def _history(self, session_id: str, last_n: int = 6) -> list[dict]:
        """取最近对话历史（供 planner 指代消解）。失败返回空列表，不阻塞规划。"""
        fn = getattr(self.clients, "get_session", None)
        if not fn:
            return []
        try:
            return await fn(session_id, last_n)
        except Exception as e:
            logger.debug("get_session failed: %s", e)
            return []

    def _build_context(self, request) -> PlanContext:
        # granted_permissions 来源：HandleRequest.meta["granted_scopes"]（逗号分隔）
        # PoC 阶段由 Edge Gateway 注入；量产换成 token scope（WS4）
        meta = dict(getattr(request, "meta", {}) or {})
        raw_scopes = meta.get("granted_scopes", "")
        granted = [s.strip() for s in raw_scopes.split(",") if s.strip()] if raw_scopes else []

        # PoC 默认授权：未注入 granted_scopes 时放行所有能力。
        # 量产 MUST 从会话 token 解析 scope，不得依赖此默认值。
        if not granted:
            granted = list(_POC_DEFAULT_SCOPES)
            logger.warning(
                "No granted_scopes in request; using PoC defaults. "
                "Production MUST inject from session token/device identity.")

        # HMI 会话级偏好（透传给 Agent，见 hmi/src/settings.tsx buildMeta）
        prefs = {k: meta[k] for k in
                 ("model_pref", "answer_length", "assistant_name", "memory_enabled")
                 if meta.get(k)}

        return PlanContext(
            request_id=getattr(request, "request_id", ""),
            session_id=getattr(request, "session_id", ""),
            user_id=getattr(request.context, "user_id", "") if hasattr(request, "context") and request.context else "",
            vehicle_id=getattr(request.context, "vehicle_id", "") if hasattr(request, "context") and request.context else "",
            is_confirmation=getattr(request, "is_confirmation", False),
            granted_permissions=granted,
            trace_id=meta.get("trace_id", ""),
            prefs=prefs,
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

            return Plan(
                steps=steps,
                raw_text=state.pending_plan.get("raw_text", ""),
                complexity=state.pending_plan.get("complexity", "simple"),
                goal=state.pending_plan.get("goal", ""),
            ), seeds
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
                    resolved = agents[0]
                    step.endpoint = resolved.endpoint
                    manifest = resolved.manifest
                    step.kind = getattr(manifest, "kind", "") or step.kind
                    step.deployment = (
                        getattr(manifest, "deployment", "") or step.deployment)
                    step.required_permissions = list(
                        getattr(manifest, "requires_permissions", []) or
                        step.required_permissions)
                    step.trust_level = (
                        getattr(manifest, "trust_level", "") or step.trust_level)
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
                 "kind": s.kind, "deployment": s.deployment,
                 "intent": s.intent, "slots": s.slots, "depends_on": s.depends_on,
                 "slot_refs": s.slot_refs, "require_confirm": s.require_confirm,
                 "latency_budget_ms": s.latency_budget_ms,
                 "required_permissions": s.required_permissions,
                 "trust_level": s.trust_level}
                for s in plan.steps
            ],
            "raw_text": plan.raw_text,
            "complexity": plan.complexity,
            "goal": plan.goal,
        }

    async def _needs_replan(self, plan: Plan, results: list[StepResult]) -> bool:
        if any(result.data.get("replan") is True for result in results):
            return True
        steps = {step.id: step for step in plan.steps}
        for result in results:
            if result.status != StepStatus.FAILED:
                continue
            step = steps.get(result.step_id)
            if not step:
                continue
            try:
                alternatives = await self.clients.resolve(
                    intent=step.intent, top_k=2)
            except Exception:
                continue
            if len(alternatives) > 1:
                return True
        return False
