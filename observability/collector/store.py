"""In-memory state for the observability collector."""
from __future__ import annotations

from collections import OrderedDict
from runtime.vehicle_state import VehicleStateStore, LEGACY_VEHICLE


class CollectorStore:
    """Aggregate vehicle state, traces, and agent runtime information."""

    def __init__(self, max_traces: int = 200, *, state_policy=None, wall_ms=None, monotonic=None):
        clocks = {"wall_ms": wall_ms}
        if monotonic is not None:
            clocks["monotonic"] = monotonic
        self.vehicle_states = VehicleStateStore(state_policy, **clocks)
        self.traces: OrderedDict[str, dict] = OrderedDict()
        self.agents: dict[str, dict] = {}
        self._max_traces = max_traces

    @property
    def vehicle_state(self) -> dict:
        return self.vehicle_states.snapshot(LEGACY_VEHICLE)

    def apply_state(self, event: dict):
        return self.vehicle_states.ingest(event)

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
            "circuit",
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
