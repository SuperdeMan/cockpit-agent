"""Best-effort observability event publishing over NATS."""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import time
import uuid

logger = logging.getLogger("obs.events")
_INITIAL_CONNECT_TIMEOUT = 0.25
_CONNECT_RETRY_DELAY = 1.0
_QUEUE_LIMIT = 1000

change_source: contextvars.ContextVar[str] = contextvars.ContextVar(
    "change_source",
    default="vehicle",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class EventEmitter:
    """Publish observability events without affecting the primary request path."""

    def __init__(self, service: str, nats_url: str | None = None):
        self.service = service
        self.nats_url = (
            nats_url if nats_url is not None else os.getenv("NATS_URL", "")
        )
        self._nc = None
        self._lock = asyncio.Lock()
        self._disabled = not self.nats_url
        self._next_connect_at = 0.0
        self._queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(
            maxsize=_QUEUE_LIMIT
        )
        self._worker_task: asyncio.Task | None = None

    async def _conn(self):
        if self._disabled:
            return None
        if self._nc is not None:
            return self._nc
        if time.monotonic() < self._next_connect_at:
            return None

        async with self._lock:
            if self._nc is not None or self._disabled:
                return self._nc
            if time.monotonic() < self._next_connect_at:
                return None
            try:
                import nats

                self._nc = await asyncio.wait_for(
                    nats.connect(
                        self.nats_url,
                        connect_timeout=_INITIAL_CONNECT_TIMEOUT,
                        max_reconnect_attempts=-1,  # 断后无限自动重连（对齐 collector）
                        reconnect_time_wait=2,
                        allow_reconnect=True,
                    ),
                    timeout=_INITIAL_CONNECT_TIMEOUT,
                )
                logger.info(
                    "observability events connected to NATS (service=%s)",
                    self.service,
                )
            except Exception as exc:
                self._next_connect_at = time.monotonic() + _CONNECT_RETRY_DELAY
                logger.debug("NATS unavailable, observability retry delayed: %s", exc)
        return self._nc

    async def _publish(self, subject: str, payload: dict) -> None:
        try:
            nc = await self._conn()
            if nc is None:
                return
            await nc.publish(
                subject,
                json.dumps(payload, ensure_ascii=False).encode(),
            )
        except Exception as exc:
            logger.debug("emit %s failed: %s", subject, exc)

    async def _run_worker(self) -> None:
        while True:
            subject, payload = await self._queue.get()
            try:
                await self._publish(subject, payload)
            finally:
                self._queue.task_done()

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._run_worker(),
                name=f"obs-events-{self.service}",
            )

    async def _emit(self, subject: str, payload: dict) -> None:
        if self._disabled:
            return
        payload.setdefault("ts", _now_ms())
        payload.setdefault("service", self.service)
        # 会话维度自动携带：请求入口 set_session_id 一次，所有事件免逐点透传。
        # 后台任务（无请求上下文）取到空串则不注入，保持事件干净。
        if not payload.get("session_id"):
            from observability.tracing import get_session_id

            sid = get_session_id()
            if sid:
                payload["session_id"] = sid
        try:
            self._queue.put_nowait((subject, payload))
        except asyncio.QueueFull:
            logger.debug("observability queue full; dropped %s", subject)
            return
        self._ensure_worker()

    async def emit_span(
        self,
        trace_id,
        node,
        status="ok",
        duration_ms=0,
        attrs=None,
        parent_id="",
        span_id="",
    ) -> None:
        await self._emit(
            "obs.span",
            {
                "trace_id": trace_id,
                "span_id": span_id or uuid.uuid4().hex[:12],
                "parent_id": parent_id,
                "node": node,
                "status": status,
                "duration_ms": round(duration_ms, 1),
                "attrs": attrs or {},
            },
        )

    async def emit_turn(
        self,
        trace_id,
        session_id,
        *,
        user_text="",
        speech="",
        status="ok",
        path="",
        input_source="",
        is_confirmation=False,
        ui_card_type="",
        actions=0,
        duration_ms=0,
        error="",
        ts=None,
    ) -> None:
        """轮次收口事件（badcase 排查核心）：一次 Handle = 一条 turn。

        内容字段（user_text/speech）经 OBS_CONTENT_CAPTURE 门控 + 统一脱敏；
        error 恒脱敏（异常串可能夹带敏感参数）。ts 传请求开始时刻（缺省=发射时刻）。
        """
        from observability.redact import gate_content, redact

        payload = {
            "trace_id": trace_id,
            "session_id": session_id,
            "user_text": gate_content(user_text, 500),
            "speech": gate_content(speech, 1000),
            "status": status,
            "path": path,
            "input_source": input_source,
            "is_confirmation": bool(is_confirmation),
            "ui_card_type": ui_card_type,
            "actions": actions,
            "duration_ms": round(duration_ms, 1),
            "error": redact(error)[:300] if error else "",
        }
        if ts is not None:
            payload["ts"] = ts
        await self._emit("obs.turn", payload)

    async def emit_llm(
        self,
        *,
        trace_id="",
        session_id="",
        caller="",
        model="",
        prompt_tokens=0,
        completion_tokens=0,
        latency_ms=0,
        cache_hit=False,
        thinking=False,
        status="ok",
        error="",
        prompt_tail="",
        content_head="",
    ) -> None:
        """LLM 调用事件（llm-gateway 唯一出口收口）：模型/tokens/时延/缓存按 trace 归档。
        prompt_tail/content_head 受 OBS_CONTENT_CAPTURE 门控 + 脱敏。"""
        from observability.redact import gate_content, redact

        await self._emit(
            "obs.llm",
            {
                "trace_id": trace_id,
                "session_id": session_id,
                "caller": caller,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": round(latency_ms, 1),
                "cache_hit": bool(cache_hit),
                "thinking": bool(thinking),
                "status": status,
                "error": redact(error)[:300] if error else "",
                "prompt_tail": gate_content(prompt_tail, 500),
                "content_head": gate_content(content_head, 800),
            },
        )

    async def emit_state(self, changes, source, trace_id="") -> None:
        await self._emit(
            "vehicle.state.changed",
            {
                "trace_id": trace_id,
                "source": source,
                "changes": changes,
            },
        )

    async def emit_metric(
        self,
        agent_id,
        count,
        avg_ms,
        error_rate,
        **extra,
    ) -> None:
        await self._emit(
            "obs.metric",
            {
                "agent_id": agent_id,
                "count": count,
                "avg_ms": avg_ms,
                "error_rate": error_rate,
                **extra,
            },
        )

    async def emit_health(
        self,
        agent_id,
        healthy,
        fail_count,
        last_seen,
        deployment="",
        kind="",
    ) -> None:
        await self._emit(
            "obs.agent.health",
            {
                "agent_id": agent_id,
                "healthy": healthy,
                "fail_count": fail_count,
                "last_seen": last_seen,
                "deployment": deployment,
                "kind": kind,
            },
        )

    async def close(self) -> None:
        if self._worker_task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.flush(), timeout=1)
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
        if self._nc is None:
            return
        try:
            await self._nc.drain()
        except Exception:
            pass

    async def flush(self) -> None:
        """Wait until queued events have been published or dropped."""
        await self._queue.join()


_default_emitters: dict[str, EventEmitter] = {}


def get_emitter(service: str = "cloud") -> EventEmitter:
    """Return one best-effort emitter per service in the current process."""
    emitter = _default_emitters.get(service)
    if emitter is None:
        emitter = EventEmitter(service)
        _default_emitters[service] = emitter
    return emitter
