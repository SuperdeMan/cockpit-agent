"""Tool registry and unified ExecuteResponse adapter."""
from __future__ import annotations

from google.protobuf import struct_pb2

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from .builtin import ToolInputError, datetime_parse, math_eval, unit_convert


def _struct(values: dict) -> struct_pb2.Struct:
    result = struct_pb2.Struct()
    result.update(values)
    return result


class ToolRegistry:
    def __init__(self, now_fn=None):
        self._now_fn = now_fn
        self._handlers = {
            "datetime.parse": datetime_parse,
            "unit.convert": unit_convert,
            "math.eval": math_eval,
        }
        self.manifest = agent_pb2.AgentManifest(
            agent_id="builtin-tools",
            version="1.0.0",
            display_name="座舱确定性工具",
            category="core",
            trust_level="system",
            deployment="cloud",
            latency_budget_ms=300,
            kind="tool",
            capabilities=[
                agent_pb2.Capability(
                    intent="datetime.parse",
                    description="把相对或自然语言时间归一化为 ISO 8601",
                    slots=["text"],
                    examples=["明天19:30", "今晚7点"],
                ),
                agent_pb2.Capability(
                    intent="unit.convert",
                    description="转换长度、质量、速度和温度单位",
                    slots=["value", "from_unit", "to_unit"],
                    examples=["1.5公里等于多少米"],
                ),
                agent_pb2.Capability(
                    intent="math.eval",
                    description="计算受限的纯算术表达式",
                    slots=["expression"],
                    examples=["2加3乘4"],
                ),
            ],
        )

    async def call(self, intent: str, slots: dict, _ctx):
        handler = self._handlers.get(intent)
        if handler is None:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.FAILED,
                error=common_pb2.ErrorInfo(
                    code="tool_not_found", message=f"unknown tool: {intent}"),
            )
        try:
            data, speech = handler(slots, self._now_fn)
        except ToolInputError as exc:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.REJECTED,
                speech=str(exc),
                error=common_pb2.ErrorInfo(
                    code="invalid_request", message=str(exc)),
            )
        except Exception as exc:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.FAILED,
                error=common_pb2.ErrorInfo(
                    code="tool_error", message=str(exc)),
            )
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK,
            speech=speech,
            data=_struct(data),
        )

    async def register(self, clients):
        await clients.register_manifest(self.manifest, "tool://builtin")
