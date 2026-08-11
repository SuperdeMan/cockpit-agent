"""DagExecutor：拓扑分层 + 并行执行 + 超时 + 部分失败。

WS3 §5。Kahn 拓扑排序分层 → 每层内 asyncio.gather 并行 → 层间串行。
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import re
from typing import AsyncIterator
from collections import defaultdict, deque
from google.protobuf.json_format import MessageToDict

from . import verify as _verify
from .models import Plan, Step, StepResult, StepStatus, PlanContext, CyclicPlan
from observability import events as obs_events

logger = logging.getLogger("planner.executor")

# 传输不确定的失败：**响应没回来，不等于事情没发生**。这一族（且只有这一族）
# 允许拿世界状态翻案；Agent 明确报的错是**确定失败**，确定失败不许被状态翻案
# ——那个状态可能是别的原因造成的（M-C 不变量）。
# 这些是编排层自己产生的内部错误码，不是领域词汇（同 aggregator._ERROR_FRIENDLY）。
_UNCERTAIN_ERRORS = ("step_timeout", "timeout")
# `data["_verify"].exec` 的取值：世界状态证实了「它发生了」，但证明不了「这一步成功了」。
_EXEC_UNCERTAIN = "uncertain_confirmed"
# 同族第二种触发面（B5 §4.2）：流式已发出 action，final 却丢了。与 `_EXEC_UNCERTAIN`
# 分开取值是为了让「Agent 回了传输不确定的失败」和「流断在 final 之前」在观测上分得开
# ——两者的补救动作相同，成因不同。
_EXEC_STREAM_LOST_FINAL = "stream_lost_final"

# 流式丢 final 后按 readback 结果分档的话术（B5 §4.2）。三句**零领域字面量**：
# 不提任何具体对象或动作，只说「发出了 / 生效了 / 不确定」。
# UNSAT 档刻意只说前半句——后半句由聚合器的 `_VERIFY_NOTE["state_match"]` 统一补，
# 同一句话不写第二遍（B4 教训）。
_STREAM_LOST_FINAL_SAT_SPEECH = (
    "刚才没收到执行回执，不过我查了车辆状态，这个操作已经生效了。")
_STREAM_LOST_FINAL_UNSAT_SPEECH = "操作指令已发出。"
_STREAM_LOST_FINAL_SPEECH = "操作指令已发出，结果暂时无法确认，请留意车辆状态。"


def _dedup_enabled() -> bool:
    """重复副作用防抖总开关（M2 P2）。off = 回到 T2 放宽前的行为。"""
    return os.getenv("PLANNER_DEDUP_SIDE_EFFECTS", "on").strip().lower() != "off"


def _struct_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):     # 进程内 dispatcher/测试 stub 直接携带 dict，无需 Struct 往返
        return value
    return MessageToDict(value, preserving_proto_field_name=True)


class DagExecutor:
    def __init__(self, dispatcher=None, call_agent_fn=None, state_mirror=None):
        """
        dispatcher: UnifiedDispatcher-compatible object with dispatch(step, ctx).
        call_agent_fn: legacy async callable kept for existing embedders/tests.
        state_mirror: 车况镜像（M2 Verifier 的 state_match 求值源）。None = 无镜像 →
                      state_match 恒 UNKNOWN（不定罪），其余模式不受影响。
        """
        if dispatcher is None:
            if call_agent_fn is None:
                raise ValueError("dispatcher or call_agent_fn is required")
            dispatcher = _LegacyDispatcher(call_agent_fn)
        self._dispatcher = dispatcher
        self._mirror = state_mirror

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
        """执行单个 step。尾链：防抖(M2) → dispatch → _to_result → 确认兜底闸(M0a) → 对账(M2)。"""
        # 解析 slot_refs：用前序结果填 slot（**防抖判定必须在此之后**——指纹要含真实槽位）
        self._resolve_slot_refs(step, done)

        fingerprint = self._fingerprint(step)
        prior = self._find_duplicate(fingerprint, done)
        if prior is not None:
            return await self._replay_prior(step, prior, ctx)

        result = self._enforce_capability_confirm(
            step, await self._dispatch_once(step, ctx))
        result = await self._verify_outcome(step, result, ctx)
        # 只给「真产生了副作用」的成功结果打指纹：纯查询步重复执行无害（还可能要刷新数据）
        if result.status == StepStatus.OK and result.actions:
            result.fingerprint = fingerprint
        # 超时也打指纹：超时 ≠ 失败——Agent 可能已经执行完、只是响应没回来（副作用已
        # 发生）。不打指纹的话 T2 replan 重出同一动作会被原样重发（「明早提醒我」变两条
        # 提醒）。真失败（Agent 明确报错）仍不打——那要允许重跑，见 _find_duplicate。
        elif result.status == StepStatus.FAILED and result.error == "step_timeout":
            result.fingerprint = fingerprint
        return result

    @staticmethod
    def _fingerprint(step: Step) -> str:
        """(intent, 归一化 slots) 指纹。slots 排序后序列化——槽位顺序不该影响同一性判定。"""
        try:
            slots = json.dumps(step.slots or {}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            slots = str(sorted((step.slots or {}).items()))
        raw = f"{step.intent}|{slots}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:12]

    @staticmethod
    def _find_duplicate(fingerprint: str, done: dict) -> StepResult | None:
        """本轮内是否已成功执行过同一副作用动作。

        **对症 T2 放宽后的真实风险**（子 RFC §4.3 对 M0「副作用步不进循环体」的重定义）：
        replan 会对已完成步骤失忆而重复产出同一动作（「打开空调」被开两次）。
        比原闸精准——不阉割 T2 对副作用任务的编排能力，只挡重复执行；也比原闸可测。
        """
        if not fingerprint or not _dedup_enabled():
            return None
        for prior in done.values():
            if getattr(prior, "fingerprint", "") != fingerprint:
                continue
            if prior.status == StepStatus.OK:
                return prior
            # 超时前序也算命中：副作用可能已发生，同轮内绝不盲目重发（验收抓到的 P0：
            # 超时→FAILED→replan 重出同一动作→重复执行）。其他 FAILED（Agent 明确报错
            # =确定没做成）不命中——那必须允许重跑，否则失败被「复用」成假成功。
            if (prior.status == StepStatus.FAILED
                    and getattr(prior, "error", "") == "step_timeout"):
                return prior
        return None

    async def _replay_prior(self, step: Step, prior: StepResult,
                            ctx: PlanContext) -> StepResult:
        """以既有结果回填：**动作不重发**（重发才是要防的那件事），话术/卡片沿用。

        超时前序的回填是「不确定」的诚实版本：不沿用话术（上次没有话术）、不假装成功，
        告诉用户没有重复执行、请核实状态。话术按 R9 契约用 OK 承载（FAILED 会被聚合器
        吞成裸「处理失败」）。
        """
        timed_out = (prior.status == StepStatus.FAILED
                     and getattr(prior, "error", "") == "step_timeout")
        logger.info("Step %s(%s): duplicate side-effect suppressed (fingerprint=%s%s)",
                    step.id, step.intent, prior.fingerprint,
                    ", prior=timeout" if timed_out else "")
        try:
            await obs_events.get_emitter("cloud").emit_span(
                getattr(ctx, "trace_id", ""), "step.dedup",
                attrs={"step_id": step.id, "intent": step.intent,
                       "prior_step_id": prior.step_id,
                       "prior_timeout": timed_out})
        except Exception:
            pass
        if timed_out:
            return StepResult(
                step_id=step.id, status=StepStatus.OK,
                speech="刚才这个操作没拿到结果，可能已经生效；我没有重复执行，"
                       "请稍后确认一下状态再决定要不要重试。",
                actions=[], fingerprint=prior.fingerprint)
        return StepResult(
            step_id=step.id, status=StepStatus.OK, speech=prior.speech,
            ui_card=prior.ui_card, actions=[],       # ← 副作用不重放
            follow_up=prior.follow_up, data=dict(prior.data or {}),
            fingerprint=prior.fingerprint)

    async def _dispatch_once(self, step: Step, ctx: PlanContext) -> StepResult:
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
        return self._to_result(step.id, resp)

    async def _verify_outcome(self, step: Step, result: StepResult,
                              ctx: PlanContext,
                              allow_retry: bool = True) -> StepResult:
        """M2 Outcome Verifier：只验「声称成功」的步，期望全部来自 `step.verification`。

        `allow_retry=False` 供**流式直通路径**（engine D0 / loop T2）调用：话术已经流给
        用户了，重跑会重复播报，故那条路径只走 report 分支。**必须由它们显式调用本方法**
        ——流式直通不经 `_exec_step`，不接上就是「声明了却不生效」的静默缺口（真栈首验
        实测：weather 走 D0 流式，一条 step.verify span 都没有）。

        **本方法与 verify.py 不得出现任何 agent_id/intent 字面量分支**——新能力声明
        verification 即生效、编排核心零改动（契约测试 test_verify.py 源码断言 + 即插即用）。

        - `mode` 未声明 / 结果非 OK → 原样透传（零行为变化）
        - SAT / UNKNOWN → 透传（观测缺失不定罪）
        - UNSAT + retry → 重执行一次（仅 require_confirm=false 的步，副作用不重放）
        - UNSAT + report → **保持 OK**（R9 契约：话术型诚实提示走 OK，FAILED 上的话术
          会被聚合器吞成裸「抱歉，处理失败」），落 `data["_verify"]` 保留键供聚合器加口径
        """
        verification = step.verification or {}
        if not verification.get("mode"):
            return result
        if not _verify.enabled():
            return result
        if result.status != StepStatus.OK:
            return await self._verify_uncertain(step, result, ctx)

        attempts = 0
        verdict = await self._evaluate(step, result, ctx, attempts)
        while (verdict == _verify.UNSAT and allow_retry
               and _verify.retry_allowed(verification, step.require_confirm, attempts)):
            attempts += 1
            logger.info("Step %s(%s): verify unsat, retrying (%d)",
                        step.id, step.intent, attempts)
            retried = self._enforce_capability_confirm(
                step, await self._dispatch_once(step, ctx))
            if retried.status != StepStatus.OK:
                return retried          # 重试本身失败：交回常规失败通道，不再对账
            result = retried
            verdict = await self._evaluate(step, result, ctx, attempts)

        if verdict == _verify.UNSAT and self._should_report(result):
            data = dict(result.data or {})
            data["_verify"] = {"verdict": _verify.UNSAT,
                               "mode": verification.get("mode", ""),
                               "attempts": attempts}
            result = StepResult(
                step_id=result.step_id, status=result.status, speech=result.speech,
                ui_card=result.ui_card, actions=result.actions,
                follow_up=result.follow_up, data=data,
                missing_slots=result.missing_slots, error=result.error)
        return result

    async def _verify_uncertain(self, step: Step, result: StepResult,
                                ctx: PlanContext) -> StepResult:
        """对**传输不确定**的失败查一次世界状态（M-C）。

        缺口是 M2 就记着的：对账只验「声称成功」的步，于是「动作已经生效、响应却丢了」
        这一族永远报失败——用户听到「抱歉，处理超时」，而后备箱确实开了。

        三条边界，一条都不能松：
        - **只认传输不确定**（`_UNCERTAIN_ERRORS`）。Agent 明确报错是**确定失败**，
          确定失败不许被世界状态翻案：那个状态可能是别的原因造成的。
        - **只认声明式 state_match**。schema 模式验的是本次响应的内容，而响应根本没回来，
          拿它判定等于无中生有。
        - **不改 status**。世界状态能证明「它发生了」，证明不了「这一步成功了」；
          真正落到用户耳朵里的差别由聚合器用一句通用话术表达，**不伪造成功话术**
          （合成它需要领域知识，而这里必须零领域字面量）。
        """
        if result.status != StepStatus.FAILED:
            return result
        if (result.error or "") not in _UNCERTAIN_ERRORS:
            return result
        if (step.verification or {}).get("mode") != _verify.MODE_STATE_MATCH:
            return result
        verdict = await self._evaluate(step, result, ctx, 0)
        if verdict != _verify.SAT:
            return result
        data = dict(result.data or {})
        data["_verify"] = {"verdict": _verify.SAT,
                           "mode": _verify.MODE_STATE_MATCH,
                           "exec": _EXEC_UNCERTAIN, "attempts": 0}
        return StepResult(
            step_id=result.step_id, status=result.status, speech=result.speech,
            ui_card=result.ui_card, actions=result.actions,
            follow_up=result.follow_up, data=data,
            missing_slots=result.missing_slots, error=result.error)

    async def stream_uncertain_result(self, step: Step,
                                      ctx: PlanContext) -> StepResult:
        """流式已发出 action 而 final 丢了 → 查一次世界状态再定话术（B5 §4.2）。

        B1 只留下了标记 + 一句固定话术（它当时的边界写明「readback 归 B5 统一组件
        时做」）。本方法把那笔账还上，两条流式路径（engine D0 / loop T2）共用。

        与 `_verify_uncertain` 是同族，三条边界逐条照抄——**只认声明式
        `state_match`**（schema 验的是本次响应的内容，而响应根本没回来）、
        **只认传输不确定**（这里的触发面本身就是「流断在 final 之前」）、
        **不伪造成功话术**（SAT 那句只说「操作已经生效」，不合成任何领域词）。

        两处与 `_verify_uncertain` 不同，都有理由：
        - status 用 **OK 不用 FAILED**（R9 契约：FAILED 上的话术会被聚合器吞成裸
          「抱歉，处理失败」，而这一步恰恰要把话说清楚）；
        - **打指纹**。这是本方法存在的第二个理由，也是「幂等键随统一组件一起做」
          （B1 §6）的落点：`_exec_step` 早就给 `step_timeout` 打指纹了，理由原文是
          「超时 ≠ 失败——副作用可能已发生，不打指纹 T2 replan 重出同一动作会被原样
          重发」。**流断丢 final 与超时是同一种处境**，而 B1 合成的那份结果没有指纹
          ——replan 重出同一动作就会真的发第二遍。不新造 `command_id`：现成的
          `_fingerprint(intent, slots)` 就是那把键，第三套局部实现正是要避免的东西。
        """
        verdict = _verify.UNKNOWN
        if (_verify.enabled()
                and (step.verification or {}).get("mode") == _verify.MODE_STATE_MATCH):
            verdict = await self._evaluate(
                step, StepResult(step_id=step.id, status=StepStatus.OK), ctx, 0)
        if verdict == _verify.SAT:
            speech = _STREAM_LOST_FINAL_SAT_SPEECH
        elif verdict == _verify.UNSAT:
            speech = _STREAM_LOST_FINAL_UNSAT_SPEECH
        else:
            speech = _STREAM_LOST_FINAL_SPEECH
        data = {"_outcome_uncertain": True}
        if verdict in (_verify.SAT, _verify.UNSAT):
            data["_verify"] = {"verdict": verdict,
                               "mode": _verify.MODE_STATE_MATCH,
                               "exec": _EXEC_STREAM_LOST_FINAL, "attempts": 0}
        logger.warning(
            "Step %s(%s): stream lost final after action — readback=%s, "
            "not re-running", step.id, step.intent, verdict)
        return StepResult(step_id=step.id, status=StepStatus.OK, speech=speech,
                          data=data, fingerprint=self._fingerprint(step))

    @staticmethod
    def _should_report(result: StepResult) -> bool:
        """UNSAT 要不要补一句对账口径。

        **不要重复念**：Agent 走 R9 诚实降级（「周边暂时没找到」「服务暂时不可用」）时
        既不出卡也不带 data——它已经把情况交代清楚了，再补「这次没拿到实际内容」是啰嗦。
        真正该报的是「有卡/有动作、看起来办成了、但结果是空的」那种**假完成**。

        判据通用、零领域字面量：有 ui_card 或 actions 或非空 data = 声称有产出。
        （不论报不报，span 都已记 unsat——观测面不受影响。）
        """
        return bool(result.ui_card or result.actions or (result.data or {}))

    async def _evaluate(self, step: Step, result: StepResult, ctx: PlanContext,
                        attempts: int) -> str:
        verification = step.verification or {}
        try:
            # 槽位一并交给求值器：`$slot:` 动态期望要拿它取值（`_resolve_slot_refs`
            # 已经跑过，所以这里是**真实下发的**槽，不是 planner 的原始输出）。
            verdict = await _verify.evaluate(verification, result.data or {},
                                             mirror=self._mirror,
                                             slots=dict(step.slots or {}))
        except Exception as e:      # fail-open：对账是增强，绝不因它炸主链
            logger.warning("Step %s verify errored (ignored): %s", step.id, e)
            return _verify.UNKNOWN
        try:
            await obs_events.get_emitter("cloud").emit_span(
                getattr(ctx, "trace_id", ""), "step.verify",
                status="err" if verdict == _verify.UNSAT else "ok",
                attrs={"step_id": step.id, "intent": step.intent,
                       "mode": verification.get("mode", ""), "verdict": verdict,
                       "attempts": attempts})
        except Exception:
            pass
        return verdict

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
        # Planner/tool-call occasionally emits an exact data reference as the
        # value of an already-declared slot (for example
        # destination="${s1.data.items.0.name}"). Resolve that wire format
        # before applying the explicit slot_refs map; otherwise the literal
        # placeholder reaches the downstream agent and becomes a bogus POI.
        for slot_name, raw_value in list(step.slots.items()):
            if not isinstance(raw_value, str):
                continue
            match = re.fullmatch(r"\$\{([^{}]+)\}", raw_value.strip())
            if not match:
                continue
            value = self._resolve_ref(match.group(1), done)
            if value is not None:
                step.slots[slot_name] = str(value)
            else:
                logger.warning(
                    "slot placeholder %s -> %s resolved to None",
                    slot_name,
                    match.group(1),
                )

        for slot_name, ref_path in step.slot_refs.items():
            existing = step.slots.get(slot_name)
            # 「已有值不覆盖」——但**同一条引用路径被写了两遍不是「已有值」**。
            # 真栈实测：`nearby.detail` 同时给 `slots={"poi_id":"s1.data.items.0.id"}`
            # 和同值的 `slot_refs`；旧逻辑认定槽里已有值就跳过，于是那串路径**当成
            # 真 POI id 发给了下游**。判据同 `${...}` 那一段：值和引用长得不一样，
            # 长得一样就说明它是引用。
            if slot_name in step.slots and str(existing).strip() != str(ref_path).strip():
                continue
            value = self._resolve_ref(ref_path, done)
            if value is not None:
                step.slots[slot_name] = str(value)
            else:
                logger.warning("slot_ref %s -> %s resolved to None", slot_name, ref_path)

        # MiniMax 的自由对象 wire format 会把「目标槽引用另一个 slot_refs 槽」写成
        # destination="$ref.poi_id"。上面的显式引用先把 poi_id 解析成真实值，再在本步
        # 槽集合内做一次严格的**整值别名**替换；不接受任意路径/字符串插值，避免扩大
        # LLM 可解释语法面。
        for slot_name, raw_value in list(step.slots.items()):
            if not isinstance(raw_value, str):
                continue
            match = re.fullmatch(r"\$ref\.([A-Za-z_][A-Za-z0-9_]*)", raw_value.strip())
            if not match:
                continue
            alias = match.group(1)
            value = step.slots.get(alias)
            if value is not None and value != raw_value:
                step.slots[slot_name] = str(value)
            else:
                logger.warning("slot alias %s -> %s resolved to None", slot_name, alias)

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
