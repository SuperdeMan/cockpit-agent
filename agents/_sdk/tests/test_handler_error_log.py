"""handler 抛异常：照旧转成 agent_error，同时落一条带位置的告警——只记类型与抛出点，不记异常文字（2026-10-17 demo-dqt1fu 复测）。"""
from __future__ import annotations

import asyncio
import logging

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from agents._sdk.server import _Servicer

_THIRD_PARTY = "商家返回的任意文字"


class RaisingAgent:
    manifest = None
    memory = None

    async def handle(self, intent, ctx, meta):
        raise KeyError(_THIRD_PARTY)

    async def handle_stream(self, intent, ctx, meta):
        raise KeyError(_THIRD_PARTY)
        yield  # pragma: no cover


def _request():
    return agent_pb2.ExecuteRequest(
        session_id="s1", intent=common_pb2.Intent(name="mcd.menu"),
        context=common_pb2.ContextRef(session_id="s1", user_id="u1", vehicle_id="v1"),
        meta={"trace_id": "t1"})


def test_a_raising_handler_logs_type_and_location_but_not_the_message(caplog):
    service = _Servicer(RaisingAgent())

    async def go():
        response = await service.Execute(_request(), None)
        events = [event async for event in service.ExecuteStream(_request(), None)]
        return response, events

    with caplog.at_level(logging.WARNING, logger="agent.sdk.server"):
        response, events = asyncio.run(go())

    assert response.error.code == "agent_error" and events[-1].final.error.code == "agent_error"
    records = [r.getMessage() for r in caplog.records if r.name == "agent.sdk.server"]
    assert len(records) == 2, records
    for message in records:
        assert "KeyError" in message and "test_handler_error_log.py" in message and "mcd.menu" in message, message
        assert _THIRD_PARTY not in message, message
