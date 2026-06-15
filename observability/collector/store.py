"""In-memory state for the observability collector."""
from __future__ import annotations

from collections import OrderedDict


class CollectorStore:
    """Aggregate vehicle state, traces, and agent runtime information."""

    def __init__(self, max_traces: int = 200):
        self.vehicle_state: dict = {}
        self.traces: OrderedDict[str, dict] = OrderedDict()
        self.agents: dict[str, dict] = {}
        self._max_traces = max_traces

    def apply_state(self, event: dict) -> None:
        for change in event.get("changes", []):
            self.vehicle_state[change["key"]] = change["new"]

    def apply_span(self, event: dict) -> None:
        trace_id = event.get("trace_id") or "unknown"
        trace = self.traces.get(trace_id)
        if trace is None:
            trace = {
                "trace_id": trace_id,
                "spans": [],
                "started": event.get("ts"),
            }
            self.traces[trace_id] = trace
            while len(self.traces) > self._max_traces:
                self.traces.popitem(last=False)

        trace["spans"].append(event)
        trace["updated"] = event.get("ts")
        self.traces.move_to_end(trace_id)

    def apply_metric(self, event: dict) -> None:
        agent = self.agents.setdefault(event["agent_id"], {})
        for key in (
            "count",
            "avg_ms",
            "error_rate",
            "route_hits",
            "degrade",
            "llm_tokens",
        ):
            if key in event:
                agent[key] = event[key]

    def apply_health(self, event: dict) -> None:
        agent = self.agents.setdefault(event["agent_id"], {})
        for key in (
            "healthy",
            "fail_count",
            "last_seen",
            "deployment",
            "kind",
        ):
            if key in event:
                agent[key] = event[key]

    def snapshot_traces(self, limit: int = 50) -> list[dict]:
        items = list(self.traces.values())[-limit:]
        return list(reversed(items))
