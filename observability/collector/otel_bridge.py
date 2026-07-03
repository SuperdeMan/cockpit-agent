"""Best-effort NATS obs.span -> real OTel SDK span -> OTLP exporter bridge.

Scope: collector-internal only (see docs/design/2026-07-03-r3.6-observability-
prometheus-otel-export.md §3.2). No changes to engine/dispatch/loop/circuit/agents.
No-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set — mirrors observability/tracing.py's
own try/except-ImportError degrade pattern, so the three new opentelemetry-* packages
have zero effect on any deployment that leaves the endpoint unset.

Span events (observability/events.py::emit_span) carry only
{trace_id, span_id, parent_id, node, status, duration_ms, attrs, ts} — no explicit
start/end timestamps, and parent_id is almost never populated by real call sites
today. We therefore export a flat-but-correctly-grouped bridge: trace_id is hashed
into a deterministic 128-bit OTel trace ID (same string -> same trace, so spans of
one logical trace group together in a viewer), while proper byte-exact parent/child
SpanContext linking is deliberately not attempted (would need a custom IdGenerator
for data that today is almost always absent — see design doc for the trade-off).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

logger = logging.getLogger("obs.collector.otel_bridge")

_tracer = None  # None = bridge inactive (default; zero behavior/dependency impact)


def init_otel_bridge(service_name: str = "cockpit-collector") -> bool:
    """Call once at collector startup. Returns True iff the bridge is active."""
    global _tracer
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""):
        return False
    try:
        from .. import tracing  # observability/tracing.py

        tracing.setup_tracing(service_name)
        from opentelemetry import trace

        _tracer = trace.get_tracer(f"{service_name}.bridge")
    except Exception as exc:  # ImportError (packages not installed) or SDK init failure
        logger.warning("OTLP span bridge inactive: %s", exc)
        _tracer = None
    return _tracer is not None


def _hash_id(*parts: str, nbytes: int) -> int:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).digest()[:nbytes]
    return int.from_bytes(digest, "big") or 1  # 0 is OTel's "invalid id" sentinel


def _safe_attr(value):
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, (str, bool, int, float)) for item in value
    ):
        return list(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def export_span(event: dict) -> None:
    """Best-effort convert one obs.span NATS event into a real OTel span. Never raises."""
    if _tracer is None:
        return
    try:
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            SpanKind,
            Status,
            StatusCode,
            TraceFlags,
            set_span_in_context,
        )

        trace_id_s = str(event.get("trace_id") or "unknown")
        span_id_s = str(event.get("span_id") or "")
        parent_id_s = str(event.get("parent_id") or "")

        otel_trace_id = _hash_id(trace_id_s, nbytes=16)  # 128-bit: stable per trace_id string
        parent_token = parent_id_s or "__root__"
        parent_span_id = _hash_id(trace_id_s, parent_token, nbytes=8)  # 64-bit
        parent_ctx = set_span_in_context(
            NonRecordingSpan(
                SpanContext(
                    trace_id=otel_trace_id,
                    span_id=parent_span_id,
                    is_remote=True,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
            )
        )

        ts_ms = event.get("ts") or int(time.time() * 1000)  # report time ~= span end time
        duration_ms = event.get("duration_ms") or 0
        end_ns = int(ts_ms) * 1_000_000
        start_ns = max(end_ns - int(duration_ms * 1_000_000), 0)

        span = _tracer.start_span(
            event.get("node", "unknown"),
            context=parent_ctx,
            kind=SpanKind.INTERNAL,
            start_time=start_ns,
        )
        status = event.get("status", "ok")
        span.set_status(Status(StatusCode.OK if status == "ok" else StatusCode.ERROR, status))
        span.set_attribute("cockpit.trace_id", trace_id_s)
        if span_id_s:
            span.set_attribute("cockpit.span_id", span_id_s)
        if parent_id_s:
            span.set_attribute("cockpit.parent_id", parent_id_s)
        for key, value in (event.get("attrs") or {}).items():
            span.set_attribute(f"cockpit.{key}", _safe_attr(value))
        span.end(end_time=end_ns)
    except Exception:
        logger.debug("otel span export failed (best-effort, ignored)", exc_info=True)
