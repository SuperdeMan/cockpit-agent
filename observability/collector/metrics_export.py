"""Render CollectorStore.agents as Prometheus text exposition format.

Hand-written, zero new dependency — see docs/design/2026-07-03-r3.6-observability-
prometheus-otel-export.md §3.1 for rationale (data source is a flat overwrite-style
snapshot dict, not something needing a stateful client library). Pure function — no
FastAPI/NATS import — independently unit-testable.
"""
from __future__ import annotations

# circuit 三态编码为单一有序数值：数值越大越"坏"。配合 Grafana value mappings
# 上色，比"每状态一条 0/1 时间线"的 enum 多序列写法更简单，且和其余指标
# "一 agent 一行"的形状一致（见设计文档 §3.1）。
_CIRCUIT_STATE_VALUE = {"closed": 0, "half_open": 1, "open": 2}

# (name, help, prom_type, extractor) — extractor(agent_dict) -> value | None
# None 表示这个 agent 还没有该指标的数据，跳过这行样本（"缺样本"不等于"值为 0"）。
_METRICS: list[tuple[str, str, str, "callable"]] = [
    ("cockpit_agent_calls_total",
     "Cumulative agent dispatch calls since last cloud-planner restart.",
     "counter",
     lambda a: a.get("count")),
    ("cockpit_agent_latency_seconds_avg",
     "Average dispatch latency in seconds (cumulative average, not a histogram).",
     "gauge",
     lambda a: round(a["avg_ms"] / 1000.0, 6) if "avg_ms" in a else None),
    ("cockpit_agent_error_rate",
     "Cumulative error rate (0-1) since last cloud-planner restart.",
     "gauge",
     lambda a: a.get("error_rate")),
    ("cockpit_agent_circuit_state",
     "Circuit breaker state: 0=closed, 1=half_open, 2=open.",
     "gauge",
     lambda a: _CIRCUIT_STATE_VALUE.get(a["circuit"], 0) if "circuit" in a else None),
    ("cockpit_agent_healthy",
     "Registry health probe result (1=healthy, 0=unhealthy).",
     "gauge",
     lambda a: int(a["healthy"]) if "healthy" in a else None),
    ("cockpit_agent_health_fail_count",
     "Consecutive health-probe failures recorded by registry.",
     "gauge",
     lambda a: a.get("fail_count") if "fail_count" in a else None),
]


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus_metrics(store) -> str:
    """store: CollectorStore（只读取 .agents，不改动）。"""
    lines: list[str] = []
    agents = store.agents
    for name, help_text, prom_type, extractor in _METRICS:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {prom_type}")
        for agent_id in sorted(agents):
            agent = agents[agent_id]
            value = extractor(agent)
            if value is None:
                continue
            aid = _escape_label_value(agent_id)
            deployment = _escape_label_value(str(agent.get("deployment", "unknown")))
            kind = _escape_label_value(str(agent.get("kind", "unknown")))
            lines.append(
                f'{name}{{agent_id="{aid}",deployment="{deployment}",kind="{kind}"}} {value}'
            )
    return "\n".join(lines) + "\n"
