"""Bounded adaptive planning loop for T2 requests."""
from __future__ import annotations

import logging
import os
import time
from typing import AsyncIterator

from .executor import DagExecutor
from .models import Plan, PlanContext, StepResult, StepStatus
from .progress import make_progress, phase_label, step_summary
from observability import events as obs_events
from observability.metrics import metrics

logger = logging.getLogger("planner.loop")

THINKING_FILLER = "我正在根据当前结果继续处理。"

# ── T2 分档预算（M2 P2，母提案 §4.B 档位表 / 子 RFC §4.1）────────────────
# 原本是单值 env（2 次 / 5s）两档共用：对「查天气→再按结果补建提醒」这种条件依赖链太紧，
# 对误入 T2 的简单句又白等。改为按 plan.complexity 选档——**放宽的前置条件（Ledger 可
# 中断长任务 + Verifier 对账假完成）已在 M2 P0/P1 就位**，故此处才敢动预算。
#
# Background 档（6+ 次 / 30-300s）**刻意不在这里**：长任务归 Task Ledger 语义（受理即返回、
# 心跳/取消/预算另有一套），不占请求线程的循环预算。
#
# 灰度姿态（子 RFC §4.2）：先落 Complex 3 次/12s 这一档，journeys regression 15/15 且
# P95 增量 ≤10% 才继续放到 3-4 次/15s。任一红灯回退上一档 = 改一行 env。
_TIERS = {
    "simple": (2, 8000),        # Interactive：simple 误入 T2 的兜底（escalate/replan）
    "adaptive": (3, 12000),     # Complex：多意图、条件依赖链
}
_DEFAULT_TIER = "simple"


def tier_budget(complexity: str) -> tuple[int, int]:
    """按复杂度取 (max_iters, budget_ms)，env 可逐档覆盖（评测钉档用）。

    覆盖优先级：全局 env（`PLANNER_LOOP_MAX_ITERS`/`_BUDGET_MS`，两档同时生效——
    保留它是为了老配置与一键回退不失效）> 分档 env（`..._COMPLEX`）> 内置档位。
    """
    iters, budget = _TIERS.get(complexity or "", _TIERS[_DEFAULT_TIER])
    if complexity == "adaptive":
        iters = _env_int("PLANNER_LOOP_MAX_ITERS_COMPLEX", iters)
        budget = _env_int("PLANNER_LOOP_BUDGET_MS_COMPLEX", budget)
    return (_env_int("PLANNER_LOOP_MAX_ITERS", iters),
            _env_int("PLANNER_LOOP_BUDGET_MS", budget))


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def summarize(result: StepResult) -> dict:
    """Keep only bounded, decision-relevant observation fields."""
    data = dict(result.data or {})
    if len(data) > 12:
        data = dict(list(data.items())[:12])
    return {
        "step_id": result.step_id,
        "status": result.status.value,
        "data": data,
        "speech": (result.speech or "")[:160],
        "error": (result.error or "")[:120],
    }


class LoopController:
    def __init__(self, planner, executor, aggregator, suspend_fn,
                 max_iters: int | None = None, budget_ms: int | None = None,
                 observation_limit: int = 6, clock=None, stream_fn=None):
        self.planner = planner
        self.executor = executor
        self.aggregator = aggregator
        self.suspend = suspend_fn
        # 显式传参（测试/特殊接线）恒定优先；缺省则每轮按 plan.complexity 选档（M2 P2）。
        # 保留 self.max_iters/budget_ms 属性作为「当轮生效值」——既有测试与日志都读它。
        self._pinned_iters = max_iters
        self._pinned_budget = budget_ms
        self.max_iters, self.budget_ms = self._budget_for(_DEFAULT_TIER)
        self.observation_limit = observation_limit
        self.clock = clock or time.monotonic
        self._stream = stream_fn

    def _budget_for(self, complexity: str) -> tuple[int, int]:
        iters, budget = tier_budget(complexity)
        return (self._pinned_iters if self._pinned_iters is not None else iters,
                self._pinned_budget if self._pinned_budget is not None else budget)

    async def run(self, goal: str, initial_plan: Plan | None, agents: list,
                  ctx: PlanContext, user_text: str,
                  seed_results: list[StepResult] | None = None,
                  working_set=None,
                  show_process: bool = False, thinking: bool = False
                  ) -> AsyncIterator[dict]:
        results = list(seed_results or [])
        n_seed = len(results)      # 种子（确认续接带入，上轮已播报）不进挂起前缀
        spoken: set[int] = set()   # 已流式播报过的结果（id()）——挂起前缀不再复读
        observations = [summarize(r) for r in results][-self.observation_limit:]
        # M2 P2 分档：本轮预算按计划复杂度取（adaptive=Complex 档、其余=Interactive 档）
        complexity = getattr(initial_plan, "complexity", "") or _DEFAULT_TIER
        self.max_iters, self.budget_ms = self._budget_for(complexity)
        deadline = self.clock() + self.budget_ms / 1000.0
        current = initial_plan
        replans = 0
        exhausted = False

        def _prior(sr):
            """本轮新完成且未播报的结果，供挂起 final 前缀简报（旅程 A1-4：
            adaptive 先查天气再补建 reminder，追问时刻前用户必须先听到天气结论）。"""
            return [r for r in results[n_seed:]
                    if id(r) not in spoken and r is not sr]

        # 即时反馈：复杂任务由过程区承载（不再用 filler speech，避免气泡刷屏 + 被 TTS 念出）；
        # 非过程区路径（如 reactive 非复杂）保留原 filler 话术。
        if not show_process:
            yield {"kind": "speech", "delta": THINKING_FILLER}

        while True:
            if current is None:
                if replans >= self.max_iters or self.clock() >= deadline:
                    exhausted = self.clock() >= deadline
                    break
                try:
                    decision = await self.planner.replan(
                        goal,
                        observations[-self.observation_limit:],
                        agents,
                        ctx,
                        granted_permissions=ctx.granted_permissions,
                        working_set=working_set,
                    )
                except Exception:
                    break
                replans += 1
                if decision.done or not decision.steps:
                    break
                current = decision.to_plan(goal)

            done_seed = {result.step_id: result for result in results}

            # T2 流式直通：单步 cloud agent 尝试流式，yield speech delta。
            # 与 engine.py T1 快路径同模式：流式成功则 yield delta 并收集结果，
            # 失败回退到 executor unary 路径。
            streamed = False
            if (self._stream and len(current.steps) == 1
                    and current.steps[0].kind == "agent"
                    and current.steps[0].deployment == "cloud"):
                step = current.steps[0]
                if hasattr(self.executor, '_resolve_slot_refs'):
                    self.executor._resolve_slot_refs(step, done_seed)
                timeout = step.latency_budget_ms / 1000.0
                final_sr = None
                did_speak = False
                stream_start = self.clock()
                try:
                    async for kind, payload in self._stream(
                            step.endpoint, step.intent, step.slots,
                            ctx, step.meta, timeout=timeout):
                        if kind == "speech":
                            did_speak = did_speak or bool(payload)
                            yield {"kind": "speech", "delta": payload}
                        elif kind == "action":
                            yield {"kind": "action", "action": payload}
                        elif kind == "final":
                            final_sr = DagExecutor._to_result(step.id, payload)
                except Exception as exc:
                    logger.warning(
                        "T2 stream failed for %s, falling back: %s",
                        step.id, exc)
                    final_sr = None

                if final_sr is not None:
                    streamed = True
                    # M2 Verifier：T2 流式直通同样不经 executor._exec_step，显式对账
                    # （与 engine D0 同款；allow_retry=False——话术已流出，不重跑）。
                    if hasattr(self.executor, "_verify_outcome"):
                        final_sr = await self.executor._verify_outcome(
                            step, final_sr, ctx, allow_retry=False)
                    # T2 流式直通也补 step.agent span（与 engine.py D0 一致，否则
                    # 单步 cloud agent 在 T2 循环里缺这一跳——trace 丢失该 Agent 身份，
                    # NEED_CONFIRM/NEED_SLOT 挂起时尤其明显）。
                    _pending = final_sr.status in (
                        StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT)
                    try:
                        await obs_events.get_emitter("cloud").emit_span(
                            ctx.trace_id, f"step.agent:{step.agent_id}",
                            status="wait" if _pending else (
                                "ok" if final_sr.status == StepStatus.OK else "err"),
                            duration_ms=(self.clock() - stream_start) * 1000,
                            attrs={"intent": step.intent, "agent_id": step.agent_id,
                                   "kind": "agent", "deployment": "cloud",
                                   "via": "stream"})
                    except Exception:
                        pass
                    results.append(final_sr)
                    if did_speak:
                        spoken.add(id(final_sr))   # 话术已流出，前缀不复读
                    observations.append(summarize(final_sr))
                    observations = observations[-self.observation_limit:]
                    if show_process and final_sr.status == StepStatus.OK:
                        yield make_progress(
                            "execute", phase_label(step.intent),
                            summary=step_summary(step, final_sr),
                            status="done", step_id=step.id)
                    if final_sr.status in (
                            StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                        yield await self.suspend(
                            final_sr, results, current, ctx,
                            prior=_prior(final_sr))
                        return
                elif streamed:
                    # Streamed speech but no final — best-effort, avoid re-run.
                    streamed = True
                    empty_sr = StepResult(
                        step_id=step.id, status=StepStatus.OK, speech="")
                    results.append(empty_sr)
                    observations.append(summarize(empty_sr))

            if not streamed:
                async for step_result in self.executor.run(
                        current, ctx, done=done_seed):
                    results.append(step_result)
                    observations.append(summarize(step_result))
                    observations = observations[-self.observation_limit:]
                    if show_process and step_result.status == StepStatus.OK:
                        step = next((s for s in current.steps
                                     if s.id == step_result.step_id), None)
                        if step is not None:
                            yield make_progress(
                                "execute", phase_label(step.intent),
                                summary=step_summary(step, step_result),
                                status="done", step_id=step.id)
                    if step_result.status in (
                            StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                        yield await self.suspend(
                            step_result, results, current, ctx,
                            prior=_prior(step_result))
                        return

            try:
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id,
                    "t2.iter",
                    attrs={
                        "replans": replans,
                        "results": len(results),
                    },
                )
            except Exception:
                pass
            current = None
            if self.clock() >= deadline:
                exhausted = True
                break

        elapsed_ms = (self.clock() - (deadline - self.budget_ms / 1000.0)) * 1000
        metrics.record_intent("t2_loop", elapsed_ms, not exhausted)
        logger.info("T2 loop done: tier=%s replans=%d/%d exhausted=%s elapsed=%.0fms",
                    complexity, replans, self.max_iters, exhausted, elapsed_ms)

        if show_process:
            yield make_progress("synthesize", "整理结果",
                                summary="合并各步结果生成回复", status="start")
        final = await self.aggregator.compose(
            user_text or goal, results, thinking=thinking)
        if exhausted and not final.get("follow_up"):
            final["follow_up"] = "要我继续吗？"
        yield {"kind": "final", **final}

