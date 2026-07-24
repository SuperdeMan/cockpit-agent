"""DagExecutor：拓扑分层 + 并行执行 + 超时 + 部分失败。

WS3 §5。Kahn 拓扑排序分层 → 每层内 asyncio.gather 并行 → 层间串行。
"""
from __future__ import annotations
import asyncio
import logging
from typing import AsyncIterator
from collections import defaultdict, deque
from google.protobuf.json_format import MessageToDict

from .models import Plan, Step, StepResult, StepStatus, PlanContext, CyclicPlan

logger = logging.getLogger("planner.executor")


def _struct_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):     # 进程内 dispatcher/测试 stub 直接携带 dict，无需 Struct 往返
        return value
    return MessageToDict(value, preserving_proto_field_name=True)


class DagExecutor:
    def __init__(self, dispatcher=None, call_agent_fn=None):
        """
        dispatcher: UnifiedDispatcher-compatible object with dispatch(step, ctx).
        call_agent_fn: legacy async callable kept for existing embedders/tests.
        """
        if dispatcher is None:
            if call_agent_fn is None:
                raise ValueError("dispatcher or call_agent_fn is required")
            dispatcher = _LegacyDispatcher(call_agent_fn)
        self._dispatcher = dispatcher

    async def run(self, plan: Plan, ctx: PlanContext,
                  done: dict[str, StepResult] | None = None) -> AsyncIterator[StepResult]:
        """执行 DAG 计划，yield 每个 step 的结果。遇到 NEED_CONFIRM/NEED_SLOT 立即停止。

        done: 已完成步骤的种子结果（多轮确认续接时由 engine 传入），
        这些步骤不再执行，但其结果可被后继步骤的依赖判定与 slot_refs 使用。
        """
        done = dict(done) if done else {}
        try:
            layers = self._topo_layers(plan.steps, completed_ids=set(done))
        except CyclicPlan as e:
            logger.error("Cyclic plan detected: %s", e)
            yield StepResult(step_id="plan", status=StepStatus.FAILED, error=str(e))
            return

        for layer in layers:
            # 跳过已有结果（确认续接的种子）和依赖未就绪/已失败的
            runnable = [s for s in layer if s.id not in done and self._should_run(s, done)]
            if not runnable:
                continue

            # 并行执行本层
            coros = [self._exec_step(s, done, ctx) for s in runnable]
            results = await asyncio.gather(*coros, return_exceptions=True)

            # F17：用 zip(runnable, results) 还原 step_id，防止异常分支丢 step
            for step, res in zip(runnable, results):
                if isinstance(res, Exception):
                    res = StepResult(step_id=step.id, status=StepStatus.FAILED,
                                     error=str(res))
                elif not isinstance(res, StepResult):
                    res = StepResult(step_id=step.id, status=StepStatus.FAILED,
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
                self._dispatcher.dispatch(step, ctx),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Step %s timed out (%.1fs)", step.id, timeout)
            return StepResult(step_id=step.id, status=StepStatus.FAILED,
                              error="step_timeout")
        except Exception as e:
            logger.warning("Step %s failed: %s", step.id, e)
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))

        return self._enforce_capability_confirm(step, self._to_result(step.id, resp))

    @staticmethod
    def _enforce_capability_confirm(step: Step, result: StepResult) -> StepResult:
        """M0a-3 兜底闸（架构 §9.1 权威链）：capability 声明 require_confirm 的步骤，
        未经用户确认（engine._restore 注入 meta.confirmed 前）不得以 OK 落地副作用。

        - Agent 自身返回 NEED_CONFIRM（正路）不受影响；漏标时本闸改判 NEED_CONFIRM
          并扣下动作——副作用通道被守住；Agent 内部副作用由 VAL/payment-gateway 硬层把守。
        - confirmed 只可由 engine 注入；LLM/计划输出无从触达（_validated_steps 不读该字段）。
        - confirmed 放行=回到正常执行通道（动作仍经 dispatch→VAL），不是绕过硬层。
        契约测试：test_capability_confirm.py。"""
        if not step.require_confirm or result.status != StepStatus.OK:
            return result
        if (step.meta or {}).get("confirmed") == "true":
            return result
        logger.warning(
            "Step %s(%s): capability requires confirm but agent returned OK unconfirmed; "
            "withholding %d action(s), forcing NEED_CONFIRM (manifest 兜底)",
            step.id, step.intent, len(result.actions))
        ask = "这个操作需要您确认后才会执行，确定继续吗？"
        speech = (result.speech or "").strip()
        if speech and speech[-1] not in "。！？!?":
            speech += "。"
        return StepResult(
            step_id=result.step_id,
            status=StepStatus.NEED_CONFIRM,
            speech=(speech + ask) if speech else ask,
            ui_card=result.ui_card,
            actions=[],                    # 副作用扣下：用户确认后该步带 confirmed 重跑再产
            follow_up=result.follow_up or ask,
            data=result.data,
        )

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
        """将 ExecuteResponse 转为 StepResult。ws8 P1: 校验 action payload。"""
        status_map = {
            0: StepStatus.OK,
            1: StepStatus.NEED_CONFIRM,
            2: StepStatus.NEED_SLOT,
            3: StepStatus.FAILED,
            4: StepStatus.FAILED,
        }
        status = status_map.get(resp.status, StepStatus.FAILED)
        actions = []
        for a in resp.actions:
            # ws8 P1: action payload 校验——type 非空，payload 必须是 dict
            if not a.type or not a.type.strip():
                logger.warning("Step %s: action with empty type, dropping", step_id)
                continue
            payload = _struct_dict(a.payload)
            if a.type.startswith("vehicle.control") and not payload:
                logger.warning("Step %s: vehicle.control action with empty payload, dropping",
                               step_id)
                continue
            actions.append({
                "type": a.type,
                "payload": payload,
                "require_confirm": a.require_confirm,
            })
        return StepResult(
            step_id=step_id,
            status=status,
            speech=resp.speech,
            ui_card=_struct_dict(resp.ui_card) or None,
            actions=actions,
            follow_up=resp.follow_up,
            data=_struct_dict(resp.data),                       # F3：从 proto 读取结构化结果
            missing_slots=list(resp.missing_slots),              # F12：缺失槽位名
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
    def _topo_layers(steps: list[Step],
                     completed_ids: set[str] | None = None) -> list[list[Step]]:
        """Kahn 拓扑排序分层。环检测：剩余节点>0 但无入度0 → raise CyclicPlan。"""
        by_id = {s.id: s for s in steps}
        completed_ids = completed_ids or set()
        in_degree = defaultdict(int)
        children = defaultdict(list)
        for s in steps:
            for dep in s.depends_on:
                if dep in by_id:
                    in_degree[s.id] += 1
                    children[dep].append(s.id)
                elif dep not in completed_ids:
                    # Unknown dependencies remain blocked and fail closed.
                    in_degree[s.id] += 1

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


class _LegacyDispatcher:
    """Adapter for the pre-UnifiedDispatcher call signature."""

    def __init__(self, call_agent_fn):
        self._call = call_agent_fn

    async def dispatch(self, step: Step, ctx: PlanContext):
        return await self._call(
            step.endpoint, step.intent, step.slots, ctx, step.meta)
