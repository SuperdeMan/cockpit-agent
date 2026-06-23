"""Bounded adaptive planning loop for T2 requests."""
from __future__ import annotations

import logging
import os
import time
from typing import AsyncIterator

from .executor import DagExecutor
from .models import Plan, PlanContext, StepResult, StepStatus
from observability import events as obs_events
from observability.metrics import metrics

logger = logging.getLogger("planner.loop")

THINKING_FILLER = "我正在根据当前结果继续处理。"


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
        self.max_iters = max_iters if max_iters is not None else int(
            os.getenv("PLANNER_LOOP_MAX_ITERS", "2"))
        self.budget_ms = budget_ms if budget_ms is not None else int(
            os.getenv("PLANNER_LOOP_BUDGET_MS", "5000"))
        self.observation_limit = observation_limit
        self.clock = clock or time.monotonic
        self._stream = stream_fn

    async def run(self, goal: str, initial_plan: Plan | None, agents: list,
                  ctx: PlanContext, user_text: str,
                  seed_results: list[StepResult] | None = None
                  ) -> AsyncIterator[dict]:
        results = list(seed_results or [])
        observations = [summarize(r) for r in results][-self.observation_limit:]
        deadline = self.clock() + self.budget_ms / 1000.0
        current = initial_plan
        replans = 0
        exhausted = False

        # Adaptive requests always provide immediate user-visible progress.
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
                stream_start = self.clock()
                try:
                    async for kind, payload in self._stream(
                            step.endpoint, step.intent, step.slots,
                            ctx, step.meta, timeout=timeout):
                        if kind == "speech":
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
                    observations.append(summarize(final_sr))
                    observations = observations[-self.observation_limit:]
                    if final_sr.status in (
                            StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                        yield await self.suspend(
                            final_sr, results, current, ctx)
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
                    if step_result.status in (
                            StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                        yield await self.suspend(
                            step_result, results, current, ctx)
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
        logger.info("T2 loop done: replans=%d exhausted=%s elapsed=%.0fms",
                     replans, exhausted, elapsed_ms)

        final = await self.aggregator.compose(user_text or goal, results)
        if exhausted and not final.get("follow_up"):
            final["follow_up"] = "要我继续吗？"
        yield {"kind": "final", **final}

