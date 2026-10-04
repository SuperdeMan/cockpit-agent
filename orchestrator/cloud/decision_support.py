"""Jev 判别的云端 shadow（JV02 起步 + JV03 首个任务「受话」）。

只观测、不改结果：规划之后异步问一次 llm-gateway `Decide`（任务 `addressed` v1），结果落 `decision.shadow` span，
与规划器自己的 `addressed` 同一句话对照，供离线校准。不进主链、不阻塞、失败只记一笔。span 里没有原话。

配置（服务端受控）：`DECISION_ADDRESSED_MODE` = off（缺省）| shadow；`DECISION_SHADOW_SCOPE` = synthetic（缺省，只对合成
E2E 会话发请求——把真实用户的话发给第三方要先定数据策略）| all；`DECISION_SHADOW_BUDGET_MS`（缺省 3000：shadow 不阻塞主链，1.5 s 时真栈约 1/12 慢样本超时）；
`DECISION_SHADOW_MAX_INFLIGHT`（缺省 4，满了丢样本并记 dropped，不排队）。网关侧另有总开关 `DECISION_ENABLED`。
设计：docs/design/2026-10-04-jev-decide-integration.md。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time

from google.protobuf.struct_pb2 import Struct
from cockpit.llm.v1 import llm_pb2

from observability import events as obs_events

logger = logging.getLogger("cloud.decision")

_MAX_TEXT_CHARS = 500


def _mode() -> str:
    mode = os.getenv("DECISION_ADDRESSED_MODE", "off").strip().lower()
    return mode if mode in ("off", "shadow") else "off"


def _scope() -> str:
    return "all" if os.getenv("DECISION_SHADOW_SCOPE", "synthetic").strip().lower() == "all" else "synthetic"


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def synthetic_session(ctx) -> bool:
    """合成 E2E 会话：运行器签发的 E2E 记忆能力，或合成用户（`e2e-` 前缀，签名身份由 E2E 密钥签发）。"""
    return bool(getattr(ctx, "e2e_memory_capability", "")) or str(getattr(ctx, "user_id", "")).startswith("e2e-")


def state_fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


class ShadowRunner:
    def __init__(self, clients, *, emitter=None, clock=time.monotonic):
        self._clients = clients
        self._emitter = emitter
        self._clock = clock
        self._inflight: set[asyncio.Task] = set()

    def _emit(self):
        return self._emitter or obs_events.get_emitter("cloud")

    def schedule_addressed(self, ctx, text: str, *, planner_addressed: bool, voice: bool) -> str:
        """调度一次受话 shadow；返回结论（scheduled / dropped / skipped:<原因>），从不抛、从不等。"""
        try:
            if _mode() != "shadow":
                return "skipped:off"
            text = (text or "").strip()
            if not text or len(text) > _MAX_TEXT_CHARS:
                return "skipped:text"
            if _scope() != "all" and not synthetic_session(ctx):
                return "skipped:scope"
            if len(self._inflight) >= _int_env("DECISION_SHADOW_MAX_INFLIGHT", 4):
                self._track(self._emit().emit_span(
                    getattr(ctx, "trace_id", ""), "decision.shadow", status="skipped",
                    attrs={"task": "addressed", "status": "DROPPED", "reason": "inflight_cap"}))
                return "dropped"
            self._track(self._run_addressed(ctx, text, bool(planner_addressed), bool(voice)))
            return "scheduled"
        except Exception as exc:          # shadow 绝不影响主链
            logger.debug("decision shadow not scheduled: %s", exc)
            return "skipped:error"

    def _track(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _run_addressed(self, ctx, text: str, planner_addressed: bool, voice: bool) -> None:
        started = self._clock()
        budget_ms = _int_env("DECISION_SHADOW_BUDGET_MS", 3000)
        request_id = str(getattr(ctx, "request_id", "") or "")
        payload = Struct()
        payload.update({"utterance": text})
        request = llm_pb2.DecideRequest(
            request_id=f"shadow-{request_id}"[:64],
            binding=llm_pb2.DecisionBinding(
                exchange_id=request_id,
                state_fingerprint=state_fingerprint(getattr(ctx, "session_id", ""), request_id, text)),
            tasks=[llm_pb2.DecisionTask(task_id="addressed", rubric_version="1", payload=payload)],
            budget_ms=budget_ms)
        attrs = {"task": "addressed", "planner_addressed": planner_addressed, "voice": voice}
        status = "ok"
        try:
            response = await self._clients.decide(request, timeout=budget_ms / 1000 + 0.5)
            result = response.results[0] if response.results else None
            p_true = result.answers[0].noul.p_true if result is not None and result.answers else None
            attrs.update(
                status=llm_pb2.DecisionStatus.Name(result.status) if result is not None else "NO_RESULT",
                reason=result.reason_code if result is not None else "",
                p_true=round(p_true, 3) if p_true is not None else None,
                agree=((p_true >= 0.5) == planner_addressed) if p_true is not None else None,
                input_tokens=response.usage.input_tokens if response.usage.known else None,
                model=response.model_used)
        except Exception as exc:
            status = "err"
            attrs.update(status="RPC_ERROR", reason=type(exc).__name__)
        try:
            await self._emit().emit_span(getattr(ctx, "trace_id", ""), "decision.shadow", status=status,
                                         duration_ms=(self._clock() - started) * 1000, attrs=attrs)
        except Exception as exc:
            logger.debug("decision shadow span not emitted: %s", exc)
