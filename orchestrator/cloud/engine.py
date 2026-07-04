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
from .context import ContextManager, build_context, _POC_DEFAULT_SCOPES
from .progress import (is_complex, phase_label, step_summary,
                       task_summary, plan_steps_summary)
from observability import events as obs_events
from observability.metrics import metrics
from observability.tracing import set_trace_id

logger = logging.getLogger("planner.engine")

# 确认/取消话术词表（语音兜底；HMI 确认按钮走 is_confirmation 显式标记）
_YES_WORDS = ("确认", "确定", "好的", "好啊", "可以", "订吧", "订了", "是的",
              "嗯", "行", "ok", "付吧", "支付", "下单", "就这家", "就它")
_NO_WORDS = ("取消", "不用", "不要", "算了", "不订", "不付", "不了", "别订", "先不")

_RESULT_FIELDS = {"step_id", "status", "speech", "ui_card", "actions",
                  "follow_up", "data", "missing_slots", "error"}

# _POC_DEFAULT_SCOPES 已迁入 context.py（此处 re-export 兼容既有 `from ...engine import _POC_DEFAULT_SCOPES`）。
__all__ = ["PlannerEngine", "_POC_DEFAULT_SCOPES"]


class PlannerEngine:
    """编排主循环。engine 是唯一持有全局状态的地方。"""

    def __init__(self, clients, planner: PlanBuilder, executor: DagExecutor,
                 aggregator: Aggregator, session: SessionStore, loop=None):
        self.clients = clients
        self.planner = planner
        self.executor = executor
        self.aggregator = aggregator
        self.session = session
        # 权限决策单轨化（R2.2）：唯一决策点是 security.permission.check_permission
        # （规划期 catalog 过滤 + dispatch 执行期硬拒同源复用），编排层不再持权限引擎。
        # trust-cap 强上限（K4）待 scope 层次化/IdP 后接线，届时扩 check_permission，不在此处复注入。
        self.context = ContextManager(clients, session)  # 上下文统一门面（装配+焦点态）
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
        ctx.raw_text = text  # 透传给 Agent（供 navigate_to 等 fallback 槽位提取）
        mem_on = ctx.prefs.get("memory_enabled", "true") != "false"

        assistant_speech = ""
        async for ev in self._orchestrate(request, ctx, text, mem_on):
            if ev.get("kind") == "final" and ev.get("speech"):
                assistant_speech = ev["speech"]
            yield ev

        if mem_on and text:
            await self.context.append_turn(ctx.session_id, "user", text,
                                           ctx.user_id, ctx.vehicle_id)
            if assistant_speech:
                await self.context.append_turn(ctx.session_id, "assistant", assistant_speech,
                                               ctx.user_id, ctx.vehicle_id)

    async def _orchestrate(self, request, ctx: PlanContext, text: str,
                           mem_on: bool) -> AsyncIterator[dict]:
        """规划→校验→执行→聚合。yield 事件：{"kind": "speech"|"action"|"final", ...}"""
        plan: Plan | None = None
        seed_results: list[StepResult] = []
        agents = []
        working_set = None  # 新规划轮由 ContextManager 装配；确认/补槽续接保持 None

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
            # F12：补槽续接——判定用户是在回答追问还是换了话题
            if self._is_topic_change(text):
                # 答非所问：用户换了话题，丢弃挂起任务，按新请求处理
                await self.session.clear(ctx.session_id)
                plan, seed_results = None, []
            else:
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
        elif ctx.is_confirmation or self._is_bare_confirm_word(text):
            # 带确认标记，或裸"确认/取消"，但没有挂起任务（TTL 过期/上一步异常/重复点击）。
            # 关键：裸确认词绝不下交 Planner——否则会借历史把"确认"重规划成上一意图的重复
            # 执行（反复 trip.modify），即用户报告的"确认后又改一遍并再次要确认"死循环。
            yield {"kind": "final",
                   "speech": "当前没有待确认的操作。您可以重新告诉我需求。"}
            return

        new_plan = plan is None
        if plan is None:
            # ws8 P1: 注入检测——疑似 prompt injection 时拦截，不进 Planner
            from security.injection import detect_injection
            if detect_injection(text):
                logger.warning("Prompt injection detected, rejecting: %s", text[:80])
                yield {"kind": "final",
                       "speech": "抱歉，您的请求包含异常内容，无法处理。"}
                return

            # B. 新规划：经 ContextManager 统一装配（catalog 语义预筛 + 此前对话历史
            # + 长期偏好记忆，统一字符预算渲染）。失败子项各自降级，不阻塞规划。
            working_set = await self.context.assemble(
                text, ctx, mem_on=mem_on,
                granted_permissions=ctx.granted_permissions)
            agents = working_set.catalog
            plan = await self.planner.build(
                text, working_set, ctx,
                granted_permissions=ctx.granted_permissions)

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
            # 权限校验按步在 dispatch 执行期硬拒（与规划期 catalog 过滤同源 check_permission），
            # 此处不再做计划级兜底（原 _enforce_permissions 为空壳，已移除）。

        # 统一「复杂任务」判据，驱动①动态开思考②过程区。普通车控/闲聊/单条轻查询
        # 不命中——零过程、零额外延迟（需求第 6 条）。
        complex_task = is_complex(plan)
        if complex_task:
            # 给每个 step 打 thinking=on：经 ExecuteRequest.meta → agent _current_meta →
            # SDK LLMClient 自动开思考，无需改各 Agent 业务码。确认续接的重跑步骤也受益。
            for s in plan.steps:
                s.meta = {**s.meta, "thinking": "on"}
        # 过程区只对「全新复杂任务」展示；确认/补槽续接是快速收尾，不再起一段过程区。
        # 四阶段：理解需求 → 规划步骤 →（执行任务）→ 整理结果。前两段在此发。
        show_process = complex_task and new_plan
        if show_process:
            yield self._progress("understand", "理解需求",
                                 summary=task_summary(plan), status="done")
            yield self._progress("plan", "规划步骤",
                                 summary=plan_steps_summary(plan), status="done")

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
                    seed_results=seed_results,
                    working_set=working_set,
                    show_process=show_process, thinking=complex_task):
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
        # 复杂单步（如独立的 trip.plan / info.search）排除在外——走 executor 才能发过程区。
        if (new_plan and plan.complexity == "simple" and len(plan.steps) == 1
                and not ctx.is_confirmation and not complex_task
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
                if mem_on:
                    await self.context.update_focus(ctx.session_id, plan, results)
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
        # 执行任务阶段：先为每个待执行步骤发「进行中」占位（HMI 折叠态显示「正在查询天气…」），
        # 各步完成后再发同 step_id 的「完成」事件（HMI 按 step_id 合并 running→done）。
        if show_process:
            for s in plan.steps:
                if s.id not in done_seed:
                    yield self._progress("execute", phase_label(s.intent),
                                         status="running", step_id=s.id)
        async for step_result in self.executor.run(plan, ctx, done=done_seed):
            results.append(step_result)

            # 过程区：每步完成发一条脱敏「完成」进度（仅复杂任务）。
            # 完成事件：OK 正常完成；NEED_CONFIRM/NEED_SLOT 也算"本轮已产出方案"（待确认/补槽），
            # 否则过程区永远停在"未完成"（此步不会再有 done 事件，如行程规划/调整）。
            if show_process and step_result.status in (
                    StepStatus.OK, StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                step = next((s for s in plan.steps if s.id == step_result.step_id), None)
                if step is not None:
                    summary = step_summary(step, step_result)
                    if step_result.status == StepStatus.NEED_CONFIRM:
                        summary = (summary or "已生成方案") + "（待确认）"
                    elif step_result.status == StepStatus.NEED_SLOT:
                        summary = summary or "需要补充信息"
                    yield self._progress(
                        "execute", phase_label(step.intent),
                        summary=summary, status="done", step_id=step.id)

            # 非复杂任务每步完成后 yield 话术（HMI 流式显示）；复杂任务逐步信息走过程区，
            # 气泡只留最终答案，避免与过程区重复刷屏。
            if (step_result.speech and step_result.status == StepStatus.OK
                    and not complex_task):
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
                    seed_results=results,
                    working_set=working_set,
                    show_process=show_process, thinking=complex_task):
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
        if mem_on:
            await self.context.update_focus(ctx.session_id, plan, results)  # 焦点态供下轮指代
        if show_process:
            yield self._progress("synthesize", "整理结果",
                                 summary="合并各步结果生成回复", status="start")
        final = await self.aggregator.compose(
            text or plan.raw_text, results, thinking=complex_task)
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id,
            "aggregate",
        )
        yield {"kind": "final", **final}

    @staticmethod
    def _progress(phase: str, label: str, summary: str = "",
                  status: str = "done", step_id: str = "") -> dict:
        """构造过程区事件。内容仅来自脱敏的步骤语义/结果，绝不含 prompt/reasoning/参数。"""
        return {"kind": "progress", "phase": phase, "label": label,
                "summary": summary, "status": status, "step_id": step_id}

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
            "ui_card": step_result.ui_card,
            "need_confirm": step_result.status == StepStatus.NEED_CONFIRM,
        }

    # 对话落库(append_turn)/历史·记忆召回(_history/_recall)/上下文构建(build_context)
    # 均已迁入 context.py（ContextManager + 模块级 build_context），统一上下文生命周期。

    @staticmethod
    def _build_context(request) -> PlanContext:
        """委托 context.build_context（保留方法名供既有测试 engine._build_context 直接调用）。"""
        return build_context(request)

    @staticmethod
    def _confirm_reply(text: str, flagged: bool) -> str | None:
        """判定本轮是否在回应待确认任务。返回 "yes" | "no" | None（答非所问）。

        否定词优先（"确认取消"按取消处理）；HMI 按钮带显式标记即肯定；
        语音兜底只认短肯定话术，避免长句误判成确认。
        """
        t = (text or "").strip().lower()
        # "词占据整句"判定：肯定/否定词须近似为全句（len(t) ≤ 词长+slack），不做宽松子串包含。
        # 否则"第二天行程换一个"含"行"、"可以换X"含"可以"、"第二天不要去长城"含"不要"会被误判。
        if any(k in t and len(t) <= len(k) + 3 for k in _NO_WORDS):
            return "no"
        if flagged:
            return "yes"
        if any(k in t and len(t) <= len(k) + 2 for k in _YES_WORDS):
            return "yes"
        return None

    @staticmethod
    def _is_bare_confirm_word(text: str) -> bool:
        """文本是否就是一句裸"确认/取消"（判定与语音兜底 _confirm_reply 完全一致）。

        无挂起任务时用于拦截：绝不能把裸"确认"交给 Planner——否则它会借对话历史把
        "确认"重规划成上一意图的重复执行（如反复 trip.modify），表现为"确认后又改一遍
        并再次要确认"的死循环。挂起任务丢失（TTL 过期/上一步异常/重复点击）时优雅兜底。"""
        return PlannerEngine._confirm_reply(text, False) is not None

    @staticmethod
    def _is_topic_change(text: str) -> bool:
        """判定 wait_slot 状态下用户是否换了话题（答非所问）。

        典型场景：Agent 追问"您要去哪里？"，用户回答"讲个笑话"——这不是在补槽。
        判断方式：文本以"动作动词"开头（讲/播/打开/关闭/搜/查…）→ 新意图；否则视为槽位补充。
        """
        t = (text or "").strip()
        if not t:
            return False
        # 以动作动词开头 → 大概率是新意图（不是在回答补槽追问）
        _verbs = (
            "讲", "说", "播放", "暂停", "打开", "关闭", "关掉",
            "调高", "调低", "搜", "查", "订", "预订", "帮我",
            "今天", "现在", "最近", "有没有", "怎么样", "多少",
        )
        return any(t.startswith(v) for v in _verbs)

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
                    step.context_scopes = list(
                        getattr(manifest, "context_scopes", []) or step.context_scopes)
                else:
                    logger.warning("No agent found for intent %s", step.intent)
            except Exception as e:
                logger.warning("Resolve failed for %s: %s", step.intent, e)

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
                 "trust_level": s.trust_level,
                 "context_scopes": s.context_scopes}
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
