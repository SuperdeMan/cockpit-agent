"""可观测模块测试。"""
import asyncio

from observability.tracing import new_trace_id, set_trace_id, get_trace_id, inject_trace_meta
from observability.metrics import MetricsCollector


def test_trace_id_roundtrip():
    tid = new_trace_id()
    assert len(tid) == 16
    set_trace_id(tid)
    assert get_trace_id() == tid


def test_inject_trace_meta():
    set_trace_id("abc123")
    meta = {}
    inject_trace_meta(meta)
    assert meta["trace_id"] == "abc123"


def test_trace_id_is_isolated_between_async_tasks():
    async def worker(trace_id):
        set_trace_id(trace_id)
        await asyncio.sleep(0)
        return get_trace_id()

    async def run():
        return await asyncio.gather(worker("trace-a"), worker("trace-b"))

    assert asyncio.run(run()) == ["trace-a", "trace-b"]


def test_metrics_intent():
    m = MetricsCollector()
    m.record_intent("hvac.set", 50.0, True)
    m.record_intent("hvac.set", 100.0, True)
    m.record_intent("hvac.set", 200.0, False)
    snap = m.snapshot()
    assert snap["intents"]["hvac.set"]["count"] == 3
    assert snap["intents"]["hvac.set"]["error_rate"] > 0


def test_metrics_agent():
    m = MetricsCollector()
    m.record_agent_call("navigation", 150.0, True)
    m.record_agent_call("navigation", 300.0, False)
    snap = m.snapshot()
    assert snap["agents"]["navigation"]["count"] == 2


def test_metrics_route():
    m = MetricsCollector()
    m.record_route("local")
    m.record_route("local")
    m.record_route("cloud")
    m.record_degrade()
    snap = m.snapshot()
    assert snap["routes"]["local"] == 2
    assert snap["degrade_count"] == 1


def test_metrics_snapshot_empty():
    m = MetricsCollector()
    snap = m.snapshot()
    assert snap["intents"] == {}
    assert snap["degrade_count"] == 0
