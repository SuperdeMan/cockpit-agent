"""Read-only display projections; ingestion keeps the original evidence intact."""
from __future__ import annotations

import re


_DEGRADED_MODE = re.compile(r"_(?:degraded|fallback)(?:_|$)|_salvage")

# Only classify the declared session namespace. This is observability metadata,
# never proof of a caller's identity or authority. Producers are reconciled in
# test_query_projection.py; unknown prefixes must not look like real HMI users.
ORIGIN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("demo-", "hmi"),
    ("app-", "app"),
    ("dashboard-", "dashboard"),
    ("replay-", "replay"),
    ("eval-", "test"),
    ("e2e-", "test"),
    ("ctxe2e-", "test"),
    ("central-", "test"),
    ("review-", "test"),
    ("nightly-", "test"),
    ("memtest-", "test"),
    ("probe-", "probe"),
    ("smoke-", "probe"),
    ("cloud-release-", "release"),
)


def origin_of(session_id: str | None) -> str:
    value = str(session_id or "")
    return next((origin for prefix, origin in ORIGIN_PREFIXES
                 if value.startswith(prefix)), "unknown")


def degraded_mode(plan_mode: str | None) -> bool:
    """The recorded transport degraded, fell back, or salvaged model output."""
    return bool(_DEGRADED_MODE.search(str(plan_mode or "")))


def span_view(span: dict) -> dict:
    """Normalize the historical HTTP SDK spelling only in the response copy."""
    if span.get("status") == "error":
        return {**span, "status": "err"}
    return span


def trace_view(trace: dict) -> dict:
    return {**trace, "spans": [span_view(span) for span in trace.get("spans", [])
                               if span.get("node") != "llm.call.meta"]}
