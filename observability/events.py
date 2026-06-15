"""Best-effort observability event publishing over NATS."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import time
import uuid

logger = logging.getLogger("obs.events")

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

    async def _conn(self):
        if self._disabled:
            return None
        if self._nc is not None:
            return self._nc

        async with self._lock:
            if self._nc is not None or self._disabled:
                return self._nc
            try:
                import nats

                self._nc = await nats.connect(
                    self.nats_url,
                    connect_timeout=2,
                    max_reconnect_attempts=3,
                    allow_reconnect=True,
                )
                logger.info(
                    "observability events connected to NATS (service=%s)",
                    self.service,
                )
            except Exception as exc:
                self._disabled = True
                logger.debug("NATS unavailable, observability disabled: %s", exc)
        return self._nc

    async def _emit(self, subject: str, payload: dict) -> None:
        try:
            nc = await self._conn()
            if nc is None:
                return
            payload.setdefault("ts", _now_ms())
            payload.setdefault("service", self.service)
            await nc.publish(
                subject,
                json.dumps(payload, ensure_ascii=False).encode(),
            )
        except Exception as exc:
            logger.debug("emit %s failed: %s", subject, exc)

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
        if self._nc is None:
            return
        try:
            await self._nc.drain()
        except Exception:
            pass
