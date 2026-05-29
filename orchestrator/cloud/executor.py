"""DagExecutor：拓扑分层 + 并行执行 + 超时 + 部分失败。

WS3 §5。Kahn 拓扑排序分层 → 每层内 asyncio.gather 并行 → 层间串行。
"""
from __future__ import annotations
import asyncio
import logging
from typing import AsyncIterator
from collections import defaultdict, deque

from .models import Plan, Step, StepResult, StepStatus, PlanContext, CyclicPlan

logger = logging.getLogger("planner.executor")


class DagExecutor:
    def __init__(self, call_agent_fn):
        """
        call_agent_fn: async (endpoint, intent, slots, context_ref) -> ExecuteResponse
        """
        self._call = call_agent_fn

    async def run(self, plan: Plan, ctx: PlanContext) -> AsyncIterator[StepResult]:
        """执行 DAG 计划，yield 每个 step 的结果。遇到 NEED_CONFIRM/NEED_SLOT 立即停止。"""
        try:
            layers = self._topo_layers(plan.steps)
        except CyclicPlan as e:
            logger.error("Cyclic plan detected: %s", e)
            yield StepResult(step_id="plan", status=StepStatus.FAILED, error=str(e))
            return

        done: dict[str, StepResult] = {}

        for layer in layers:
            # 过滤掉被依赖 step 失败而跳过的
            runnable = [s for s in layer if self._should_run(s, done)]
            if not runnable:
                continue

            # 并行执行本层
            coros = [self._exec_step(s, done, ctx) for s in runnable]
            results = await asyncio.gather(*coros, return_exceptions=True)

            for res in results:
                if isinstance(res, Exception):
                    res = StepResult(step_id="unknown", status=StepStatus.FAILED,
                                     error=str(res))
                elif not isinstance(res, StepResult):
                    res = StepResult(step_id="unknown", status=StepStatus.FAILED,
                                     error=f"unexpected result: {res}")

                done[res.step_id] = res
                yield res

                # 终态：挂起（不继续后续层）
                if res.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    return

            # 部分失败：跳过依赖已失败 step 的后续步骤
            self._mark_skipped(plan.steps, done)

    async def _exec_step(self, step: Step, done: dict, ctx: PlanContext) -> StepResult:
        """执行单个 step。"""
        # 解析 slot_refs：用前序结果填 slot
        self._resolve_slot_refs(step, done)

        timeout = step.latency_budget_ms / 1000.0
        try:
            resp = await asyncio.wait_for(
                self._call(step.endpoint, step.intent, step.slots, ctx),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Step %s timed out (%.1fs)", step.id, timeout)
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error="timeout")
        except Exception as e:
            logger.warning("Step %s failed: %s", step.id, e)
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))

        return self._to_result(step.id, resp)

    def _resolve_slot_refs(self, step: Step, done: dict):
        """用前序 step 的结果填充 slot_refs。"""
        for slot_name, ref_path in step.slot_refs.items():
            if slot_name in step.slots:
                continue  # 已有值不覆盖
            value = self._resolve_ref(ref_path, done)
            if value is not None:
                step.slots[slot_name] = str(value)
            else:
                logger.warning("slot_ref %s -> %s resolved to None", slot_name, ref_path)

    @staticmethod
    def _resolve_ref(ref_path: str, done: dict) -> object:
        """解析 slot_ref 路径，如 's1.data.items.0.id'。"""
        parts = ref_path.split(".")
        if len(parts) < 3 or parts[1] != "data":
            return None
        step_id = parts[0]
        result = done.get(step_id)
        if not result:
            return None
        obj = result.data
        for key in parts[2:]:
            if isinstance(obj, dict):
                obj = obj.get(key)
            elif isinstance(obj, (list, tuple)):
                try:
                    obj = obj[int(key)]
                except (IndexError, ValueError):
                    return None
            else:
                return None
        return obj

    @staticmethod
    def _to_result(step_id: str, resp) -> StepResult:
        """将 ExecuteResponse 转为 StepResult。"""
        status_map = {
            0: StepStatus.OK,
            1: StepStatus.NEED_CONFIRM,
            2: StepStatus.NEED_SLOT,
            3: StepStatus.FAILED,
            4: StepStatus.FAILED,
        }
        status = status_map.get(resp.status, StepStatus.FAILED)
        actions = [
            {"type": a.type, "payload": dict(a.payload.fields) if a.payload else {},
             "require_confirm": a.require_confirm}
            for a in resp.actions
        ]
        return StepResult(
            step_id=step_id,
            status=status,
            speech=resp.speech,
            ui_card=dict(resp.ui_card.fields) if resp.ui_card else None,
            actions=actions,
            follow_up=resp.follow_up,
            data={},  # Agent 可通过 meta 传结构化结果
        )

    @staticmethod
    def _should_run(step: Step, done: dict) -> bool:
        """该 step 是否应该执行（所有依赖都已 OK）。"""
        for dep_id in step.depends_on:
            dep = done.get(dep_id)
            if not dep or dep.status != StepStatus.OK:
                return False
        return True

    @staticmethod
    def _mark_skipped(steps: list[Step], done: dict):
        """标记依赖已失败 step 的后续步骤为 SKIPPED。"""
        for s in steps:
            if s.id in done:
                continue
            for dep_id in s.depends_on:
                dep = done.get(dep_id)
                if dep and dep.status in (StepStatus.FAILED, StepStatus.SKIPPED):
                    done[s.id] = StepResult(step_id=s.id, status=StepStatus.SKIPPED,
                                            error=f"dependency {dep_id} failed")
                    break

    @staticmethod
    def _topo_layers(steps: list[Step]) -> list[list[Step]]:
        """Kahn 拓扑排序分层。环检测：剩余节点>0 但无入度0 → raise CyclicPlan。"""
        by_id = {s.id: s for s in steps}
        in_degree = defaultdict(int)
        children = defaultdict(list)
        for s in steps:
            in_degree[s.id] = len(s.depends_on)
            for dep in s.depends_on:
                children[dep].append(s.id)

        layers = []
        remaining = set(by_id.keys())

        while remaining:
            # 入度为 0 的节点
            layer_ids = [sid for sid in remaining if in_degree[sid] == 0]
            if not layer_ids:
                raise CyclicPlan(f"Cycle detected in plan: {remaining}")

            layers.append([by_id[sid] for sid in layer_ids])
            for sid in layer_ids:
                remaining.discard(sid)
                for child in children[sid]:
                    in_degree[child] -= 1

        return layers
