"""NatsLogHandler：obs.log 上报（级别门槛 / trace 放行 / 自激励防护）。"""
import asyncio
import logging

import pytest

from observability import events as events_mod
from observability.logging import NatsLogHandler
from observability.tracing import set_session_id, set_trace_id


@pytest.fixture(autouse=True)
def _clean_ctx(monkeypatch):
    set_trace_id("")
    set_session_id("")
    yield
    set_trace_id("")
    set_session_id("")


def _capture_handler(monkeypatch):
    """让 get_emitter 返回启用态 emitter，_emit 改为同步捕获。"""
    sent: list[tuple[str, dict]] = []
    emitter = events_mod.EventEmitter("test-svc", nats_url="nats://example")

    async def fake_emit(subject, payload):
        sent.append((subject, payload))

    monkeypatch.setattr(emitter, "_emit", fake_emit)
    monkeypatch.setattr(events_mod, "get_emitter", lambda service="cloud": emitter)
    handler = NatsLogHandler("test-svc")
    return handler, sent


def _record(name: str, level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_warning_ships_and_info_dropped_without_trace(monkeypatch):
    handler, sent = _capture_handler(monkeypatch)

    async def run():
        handler.emit(_record("planner.engine", logging.WARNING, "boom"))
        handler.emit(_record("planner.engine", logging.INFO, "quiet"))
        await asyncio.sleep(0)  # 让 create_task 的 _emit 执行

    asyncio.run(run())
    assert len(sent) == 1
    subject, payload = sent[0]
    assert subject == "obs.log"
    assert payload["level"] == "WARNING"
    assert payload["msg"] == "boom"


def test_info_with_trace_ships(monkeypatch):
    handler, sent = _capture_handler(monkeypatch)

    async def run():
        set_trace_id("trace-log-1")
        set_session_id("sess-log-1")
        handler.emit(_record("planner.engine", logging.INFO, "Plan ready"))
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(sent) == 1
    payload = sent[0][1]
    assert payload["trace_id"] == "trace-log-1"
    assert payload["session_id"] == "sess-log-1"


def test_self_excitation_guard(monkeypatch):
    """obs.* / nats logger 的日志绝不转发——发送失败日志再触发发送=风暴循环。"""
    handler, sent = _capture_handler(monkeypatch)

    async def run():
        handler.emit(_record("obs.events", logging.ERROR, "emit failed"))
        handler.emit(_record("nats.aio.client", logging.WARNING, "reconnect"))
        handler.emit(_record("asyncio", logging.ERROR, "task exception"))
        await asyncio.sleep(0)

    asyncio.run(run())
    assert sent == []


def test_no_event_loop_is_silent(monkeypatch):
    """无运行中事件循环（启动早期/工作线程）：直接放弃，不抛不阻塞。"""
    handler, sent = _capture_handler(monkeypatch)
    handler.emit(_record("planner.engine", logging.ERROR, "early"))
    assert sent == []


def test_message_redacted(monkeypatch):
    handler, sent = _capture_handler(monkeypatch)

    async def run():
        handler.emit(_record("x", logging.WARNING, "token=secret-value leaked"))
        await asyncio.sleep(0)

    asyncio.run(run())
    assert "secret-value" not in sent[0][1]["msg"]
