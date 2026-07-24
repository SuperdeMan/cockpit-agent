"""PlannerEngine：编排主循环（规划→校验→执行→聚合）。

WS3 §3。串联 planning / executor / aggregator / session。
多轮确认闭环（F1）：NEED_CONFIRM 挂起后，确认轮只重跑挂起步骤（已完成结果种子化），
且 confirmed 标记严格限定在挂起那一步——后续 require_confirm 步骤各自再走确认（架构 §9.1）。
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from typing import AsyncIterator

from .models import Plan, Step, StepResult, StepStatus, PlanContext, SessionState
from .planning import PlanBuilder
from .executor import DagExecutor
from .aggregator import Aggregator, MdDeltaSoftener, strip_markdown_speech
from .session import SessionStore
from .loop import LoopController
from .clients import set_llm_pin
from .context import ContextManager, build_context, _POC_DEFAULT_SCOPES
from .progress import (is_complex, phase_label, result_summary, step_summary,
                       task_summary, plan_steps_summary)
from observability import events as obs_events
from observability.metrics import metrics
from observability.redact import gate_content
from observability.tracing import set_session_id, set_trace_id

logger = logging.getLogger("planner.engine")

# wait_slot 语境内取消词（旅程 B5-1）：补槽追问是当前活跃语境，句中出现取消语义即指它。
# 不复用 _confirm_reply——其「占据整句」规则（防子串误判）会拦住「那个提醒不用了，取消吧」。
_SLOT_CANCEL_RE = re.compile(r"取消|不用了|算了|不需要了|不要了|别设了|别提醒了|不设了")

# 确认/取消话术词表（语音兜底；HMI 确认按钮走 is_confirmation 显式标记）
_YES_WORDS = ("确认", "确定", "好的", "好啊", "可以", "订吧", "订了", "是的",
              "嗯", "行", "ok", "付吧", "支付", "下单", "就这家", "就它")
_NO_WORDS = ("取消", "不用", "不要", "算了", "不订", "不付", "不了", "别订", "先不")

_RESULT_FIELDS = {"step_id", "status", "speech", "ui_card", "actions",
                  "follow_up", "data", "missing_slots", "error"}

# _POC_DEFAULT_SCOPES 已迁入 context.py（此处 re-export 兼容既有 `from ...engine import _POC_DEFAULT_SCOPES`）。
__all__ = ["PlannerEngine", "_POC_DEFAULT_SCOPES"]


# R4.4：拒识/澄清 env 门控（模块级、实时读——env 翻转即刻生效，且测试可 monkeypatch）。
# REJECT 默认 on（作用域已被 hands-free opt-in + input_source 双重限定）；CLARIFY 默认 off
# （影响所有云端路由，比拒识作用域大，真栈验收后独立 commit 翻 on，母卡 §5）。
def _reject_enabled() -> bool:
    return os.getenv("REJECT_NON_ADDRESSED", "on").lower() != "off"


def _clarify_enabled() -> bool:
    return os.getenv("CLARIFY_ENABLED", "off").lower() == "on"


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
        set_session_id(ctx.session_id)  # 云端进程内观测事件/日志自动带会话维度
        # 运行时硬化 D2：请求级 LLM pin——planner/aggregator 的 LLM 调用与 Agent 同脑
        set_llm_pin(ctx.prefs.get("llm_provider", ""), ctx.prefs.get("llm_model", ""))
        text = (getattr(request, "text", "") or "").strip()
        ctx.raw_text = text  # 透传给 Agent（供 navigate_to 等 fallback 槽位提取）
        mem_on = ctx.prefs.get("memory_enabled", "true") != "false"

        assistant_speech = ""
        rejected = False
        async for ev in self._orchestrate(request, ctx, text, mem_on):
            # R4.4：剥离内部标记键，消费端（server.py）看不到；同时记本轮是否拒识。
            if ev.pop("_rejected", False):
                rejected = True
            if ev.get("kind") == "final" and ev.get("speech"):
                assistant_speech = ev["speech"]
            yield ev

        # R4.4：拒识轮 user+assistant 均不落库——不污染指代消解、不触发 memory 画像抽取
        # （母卡 D3；落库本就在编排循环之后，时序天然支持）。
        if mem_on and text and not rejected:
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
        # R2（中断-恢复，Q1 口径）：插话**不清除**挂起——插话轮正常处理，挂起在 TTL 内
        # 可回头「确认」/裸答案续接；新话轮若再产生挂起，_suspend 单槽覆盖旧挂起
        # （确认条 UI 也只有一个，语义一致）。held_pending 贯穿本轮：完成路径经
        # _settle_session 跳过 clear，并在 final 上补一句软提醒。
        held_pending = None
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
                # 答非所问：用户插话——保留挂起按新请求处理（R2；原实现在此丢弃挂起，
                # 用户回头说「确认」只能得到「当前没有待确认的操作」，旅程 B2-1 抓到）
                held_pending = pending
        elif pending and pending.phase == "wait_slot":
            # F12：补槽续接——判定用户是在回答追问还是换了话题。
            # 「取消/不用了」对补槽挂起同样是取消（镜像 wait_confirm；旅程 B5-1 抓到：
            # R2 保留挂起后「那个提醒不用了，取消吧」被当槽位答案吃掉，挂起成黑洞）。
            # 用语境内包含词表而非 _confirm_reply（其"占据整句"规则会拦住长取消句——
            # 补槽追问是当前活跃语境，句中出现取消语义即指它，误伤面可忽略）。
            if _SLOT_CANCEL_RE.search(text or ""):
                await self.session.clear(ctx.session_id)
                yield {"kind": "final", "speech": "好的，已为您取消。"}
                return
            if self._is_topic_change(text):
                # 答非所问：用户插话——保留挂起按新请求处理（R2，下轮裸答案仍可续接）
                held_pending = pending
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

            # R4.4 D6-1：hands-free 语音源 + LLM 判非受话 → 静默拒识（route_hints 兜底的 steps
            # 一并作废）。显式输入（push-to-talk/文本/候选选择）无 input_source，永不拒识。必须在
            # `if not plan.steps` 之前——addressed=false 时 steps 恰为空，否则先走空计划话术+TTS
            # 令拒识失效（母卡实施计划 §0-4）。
            if (ctx.prefs.get("input_source", "").startswith("voice_")
                    and not plan.addressed and _reject_enabled()):
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id, "rejected", attrs={"reason": "not_addressed"})
                yield {"kind": "final", "speech": "",
                       "ui_card": {"type": "rejected", "reason": "not_addressed"},
                       "_rejected": True}
                return

            if not plan.steps:
                # R4.4 D6-3：路由歧义澄清（CLARIFY 开 + 本轮非 clarify_resume 深度=1 才生效）。
                # P0 时 CLARIFY_ENABLED 默认 off → 恒 None，行为=今天；P1 翻 on 后短路出卡。
                clarify = (plan.clarify if (_clarify_enabled()
                           and ctx.prefs.get("clarify_resume") != "1") else None)
                if clarify:
                    await obs_events.get_emitter("cloud").emit_span(ctx.trace_id, "clarify")
                    yield {"kind": "final", "speech": clarify["question"],
                           "ui_card": {"type": "intent_choice", **clarify}}
                    return
                # R4.4 D5-2：诚实降级话术（含 fallback 低分不硬执行的场景），比「无法处理」更引导重说。
                yield {"kind": "final", "speech": "抱歉，我没听清您想让我做什么，可以换个说法吗。"}
                return

            # C. 解析 endpoint（Registry）
            # badcase 排查内容级采集（OBS_CONTENT_CAPTURE 门控）：plan 结构 + LLM 原始输出。
            # 此前 LLM raw 只进 stdout 截 500 字符（planning.py），与 trace 无关联。
            await obs_events.get_emitter("cloud").emit_span(
                ctx.trace_id,
                "cloud.planning",
                attrs={
                    "complexity": plan.complexity,
                    "steps": len(plan.steps),
                    "plan": gate_content(json.dumps(
                        [{"id": s.id, "agent": s.agent_id, "intent": s.intent,
                          "slots": s.slots} for s in plan.steps],
                        ensure_ascii=False, default=str), 1200),
                    "llm_raw": gate_content(plan.raw_llm, 1200),
                    # M0b Skill 层注入名单（"<mode>:<name>"），badcase 归因用
                    **({"skills": ",".join(plan.skills)} if plan.skills else {}),
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
                and plan.steps[0].deployment == "cloud"
                # M0a-3：capability 声明 require_confirm 的步不走流式直通——D0 会把
                # 流中 action 直接放行，绕开 executor 的确认兜底闸；走 executor 路径。
                and not plan.steps[0].require_confirm):
            step = plan.steps[0]
            _d0_start = time.monotonic()
            streamed = False
            softener = MdDeltaSoftener()   # 流式增量剥 **/`（final 由 compose 出口彻底清理）
            final_sr: StepResult | None = None
            try:
                async for kind, payload in self.clients.call_agent_stream(
                        step.endpoint, step.intent, step.slots, ctx, step.meta):
                    if kind == "speech":
                        streamed = True
                        payload = softener.feed(payload)
                        if payload:
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
                focus_plan = plan
                # 通用 escalate（一跳）：Agent 声明「这题我不该答，改派给 X」。仅当未播报过任何
                # 增量（streamed=False）才生效——已流出话术再改派会双重回答（agent 端零 delta
                # 才 escalate + 此处 streamed 忽略，双保险）。检测到即剥键（含忽略场景），
                # 防 F3 slot_refs/下游误引用保留键。
                esc = self._parse_escalate(final_sr)
                if esc is not None and isinstance(final_sr.data, dict):
                    final_sr.data.pop("_escalate", None)
                if streamed:
                    esc = None
                if esc is not None:
                    sink: dict = {}
                    async for ev in self._run_escalated(esc, ctx, agents, sink):
                        yield ev
                    if sink.get("suspended"):
                        return
                    if sink.get("results"):
                        results = sink["results"]
                        focus_plan = sink["plan"]
                    else:
                        # 改派装配/执行失败：原 speech 为空（agent 零播报），给诚实兜底话术
                        await self._settle_session(ctx, held_pending)
                        yield {"kind": "final",
                               "speech": "这个需要联网查询，刚才没查成，请再说一次。"}
                        return
                if final_sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    yield await self._suspend(final_sr, results, plan, ctx)
                    return
                await self._settle_session(ctx, held_pending)
                if mem_on:
                    await self.context.update_focus(ctx.session_id, focus_plan, results)
                final = await self.aggregator.compose(text or plan.raw_text, results)
                self._append_pending_hint(final, held_pending)
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
            # 气泡只留最终答案，避免与过程区重复刷屏。整步文本此处就位，直接完整剥 md。
            if (step_result.speech and step_result.status == StepStatus.OK
                    and not complex_task):
                yield {"kind": "speech",
                       "delta": strip_markdown_speech(step_result.speech) + "。"}

            # 挂起：需确认/需补槽。prior=本轮新完成步（种子是上轮已播报过的，切掉）；
            # 非复杂路径逐步 speech 已流出，但那只在单步计划成立（多步即 is_complex），
            # 单步无前序——无双重播报面。
            if step_result.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                yield await self._suspend(step_result, results, plan, ctx,
                                          prior=results[len(seed_results):])
                return

        # 通用 escalate（一跳）：executor 路径——多步计划里第一个声明改派的步结果被
        # escalated 结果替换，其余步结果保留进聚合（每轮预算 1 跳，与 D0 路径共享同一机制）。
        esc_i = next((i for i, r in enumerate(results)
                      if self._parse_escalate(r) is not None), None)
        if esc_i is not None:
            esc = self._parse_escalate(results[esc_i])
            if isinstance(results[esc_i].data, dict):
                results[esc_i].data.pop("_escalate", None)
            sink: dict = {}
            async for ev in self._run_escalated(esc, ctx, agents, sink,
                                                prior=results[len(seed_results):]):
                yield ev
            if sink.get("suspended"):
                return
            if sink.get("results"):
                results[esc_i:esc_i + 1] = sink["results"]
            # 装配失败：保留原步结果（speech 为空），其余步正常聚合——多步场景不用兜底话术
            # 压掉别的步产出

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
        await self._settle_session(ctx, held_pending)
        if mem_on:
            await self.context.update_focus(ctx.session_id, plan, results)  # 焦点态供下轮指代
        if show_process:
            yield self._progress("synthesize", "整理结果",
                                 summary="合并各步结果生成回复", status="start")
        final = await self.aggregator.compose(
            text or plan.raw_text, results, thinking=complex_task)
        self._append_pending_hint(final, held_pending)
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id,
            "aggregate",
        )
        yield {"kind": "final", **final}

    @staticmethod
    def _parse_escalate(result: StepResult) -> dict | None:
        """解析 Agent 结果里的通用改派声明 `data["_escalate"]={"intent","slots","reason"}`。

        非法（缺 intent / slots 非 dict）→ None（忽略，不炸主链）。协议登记见
        docs/conventions.md「Agent→编排结果保留键」。不剥离键——消费点自行 pop。"""
        data = getattr(result, "data", None)
        esc = data.get("_escalate") if isinstance(data, dict) else None
        if not isinstance(esc, dict):
            return None
        intent = esc.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            return None
        raw_slots = esc.get("slots")
        slots = ({str(k): str(v) for k, v in raw_slots.items()}
                 if isinstance(raw_slots, dict) else {})
        return {"intent": intent.strip(), "slots": slots,
                "reason": str(esc.get("reason") or "")}

    async def _run_escalated(self, esc: dict, ctx: PlanContext, agents: list,
                             sink: dict,
                             prior: list[StepResult] | None = None) -> AsyncIterator[dict]:
        """执行通用 escalate 改派（每轮最多一跳）。

        目标 intent 在 agent 目录里找到承接方后经 `PlanBuilder._validated_steps` 装配成单步
        mini-plan 交 executor 执行——heavy/latency_budget/权限自动带出（**绝不裸 call_agent**：
        其默认 10s 超时会打死 info.search 这类 50s 预算的重域步）；heavy 步照常发过程区事件。
        过程区/挂起 final 事件原样透传；结果经 sink 回传：
          sink["results"] 完成的 StepResult 列表（已剥离二跳 _escalate——结构性防环）
          sink["plan"]    mini-plan（焦点态更新用）
          sink["suspended"]=True 已 yield 挂起 final（调用方直接 return）
        装配失败（intent 无承接 Agent / 校验不过）→ sink 留空，调用方自行兜底。"""
        if not agents:
            agents = await self.clients.list_agents()
        agent_map = {a.manifest.agent_id: a for a in agents}
        aid = next((a.manifest.agent_id for a in agents
                    if any(c.intent == esc["intent"]
                           for c in a.manifest.capabilities)), "")
        steps = self.planner._validated_steps([{
            "id": "esc1", "agent_id": aid, "intent": esc["intent"],
            "slots": esc["slots"], "depends_on": [], "slot_refs": {},
        }], agent_map) if aid else []
        if not steps:
            logger.warning("Escalate target intent %r has no serving agent; ignored",
                           esc["intent"])
            return
        mini = Plan(steps=steps, raw_text=ctx.raw_text)
        show_esc_process = is_complex(mini)
        if show_esc_process:
            for s in mini.steps:
                s.meta = {**s.meta, "thinking": "on"}
            yield self._progress("execute", phase_label(steps[0].intent),
                                 status="running", step_id=steps[0].id)
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id, "escalate",
            attrs={"intent": esc["intent"], "reason": esc.get("reason", "")})
        results: list[StepResult] = []
        async for sr in self.executor.run(mini, ctx):
            if isinstance(sr.data, dict):
                sr.data.pop("_escalate", None)   # 单跳预算：二跳声明不消费（结构性防环）
            results.append(sr)
            if sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                yield await self._suspend(sr, results, mini, ctx, prior=prior)
                sink["suspended"] = True
                return
            if show_esc_process and sr.status == StepStatus.OK:
                yield self._progress("execute", phase_label(steps[0].intent),
                                     summary=step_summary(steps[0], sr),
                                     status="done", step_id=steps[0].id)
        sink["results"] = results
        sink["plan"] = mini

    @staticmethod
    def _progress(phase: str, label: str, summary: str = "",
                  status: str = "done", step_id: str = "") -> dict:
        """构造过程区事件。内容仅来自脱敏的步骤语义/结果，绝不含 prompt/reasoning/参数。"""
        return {"kind": "progress", "phase": phase, "label": label,
                "summary": summary, "status": status, "step_id": step_id}

    async def _suspend(self, step_result: StepResult, results: list[StepResult],
                       plan: Plan, ctx: PlanContext,
                       prior: list[StepResult] | None = None) -> dict:
        """挂起待确认/待补槽：保存会话态并构造 final 事件。executor 与流式两路共用，
        保证 F1 多轮确认闭环行为一致。

        prior=本轮**新完成且尚未播报**的步骤结果（旅程 A1-4）：多步/adaptive 计划里
        前序结论只存在于各步 speech（复杂任务不逐步流出、聚合器在挂起时不会跑），
        挂起 final 又会整体替换 HMI 气泡——不前缀简报，用户就会被凭空追问
        （「查到雨才建提醒」却没听到有雨）。调用方负责剔除确认续接种子与已流式
        播报的结果，防双重播报；挂起步自身不进前缀（trip 确认话术本就是完整叙述）。"""
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
        brief = self._prior_brief(prior or [], step_result)
        return {
            "kind": "final",
            "speech": (brief + (step_result.speech or "")) if brief else step_result.speech,
            "follow_up": step_result.follow_up,
            "actions": step_result.actions,
            "ui_card": step_result.ui_card,
            "need_confirm": step_result.status == StepStatus.NEED_CONFIRM,
        }

    @staticmethod
    def _prior_brief(prior: list[StepResult], step_result: StepResult) -> str:
        """挂起前缀：前序已完成步的脱敏简报（安全计数/首句，同过程区口径）。

        身份比较（is）而非 step_id——T2 各轮 replan 的步 id 可能撞名；短回执
        （「好的」类）不值一播，滤掉。"""
        parts = []
        for r in prior:
            if r is step_result or r.status != StepStatus.OK:
                continue
            s = strip_markdown_speech(result_summary(r)).strip().rstrip("。！？!?；;，,")
            if len(s) >= 4:
                parts.append(s)
        return "；".join(parts) + "。" if parts else ""

    async def _settle_session(self, ctx: PlanContext, held_pending) -> None:
        """本轮正常收口时的会话清理（R2）：插话轮（held_pending 非空）**不清挂起**——
        用户 TTL 内回头「确认」/裸答案仍可续接；非插话轮照旧 clear（消费/过期清理）。
        不刷新 TTL：挂起窗口以首次挂起时刻起算，插话不无限续命。"""
        if held_pending is None:
            await self.session.clear(ctx.session_id)

    @staticmethod
    def _append_pending_hint(final: dict, held_pending) -> None:
        """插话轮的 final 补软提醒：告知挂起还在（Q1 决策的配套——插话后 HMI 确认条
        已被新消息顶掉，不提示的话用户忘了挂起、说「确认」会显得凭空执行）。原地改 final。"""
        if held_pending is None or not isinstance(final, dict):
            return
        goal = ""
        try:
            goal = (held_pending.pending_plan or {}).get("goal") or ""
        except AttributeError:
            pass
        what = f"「{goal[:20]}」" if goal else "刚才的操作"
        ask = "确认" if held_pending.phase == "wait_confirm" else "继续补充"
        hint = f"对了，{what}还在等你{ask}。"
        follow = str(final.get("follow_up") or "")
        final["follow_up"] = (follow + (" " if follow else "") + hint).strip()

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
        判断方式：①文本以"动作动词"开头（讲/播/打开/关闭/搜/查…）→ 新意图；
        ②疑问/回忆式（什么来着/……吗/？）→ 新意图——问题不是槽位答案（旅程 B5-1：
        R2 保留挂起后「我刚才让你提醒我什么来着」被当 time_text 吃掉，挂起成黑洞）。
        否则视为槽位补充。
        """
        t = (text or "").strip()
        if not t:
            return False
        # 疑问/回忆式不是槽位答案
        if any(k in t for k in ("什么来着", "来着")) or t.endswith(("吗", "？", "?", "呢")):
            return True
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
