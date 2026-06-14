"""Bounded adaptive planning loop for T2 requests."""
from __future__ import annotations

import os
import time
from typing import AsyncIterator

from .models import Plan, PlanContext, StepResult, StepStatus

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
                 observation_limit: int = 6, clock=None):
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

            current = None
            if self.clock() >= deadline:
                exhausted = True
                break

        final = await self.aggregator.compose(user_text or goal, results)
        if exhausted and not final.get("follow_up"):
            final["follow_up"] = "要我继续吗？"
        yield {"kind": "final", **final}
