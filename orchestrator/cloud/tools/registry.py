"""Tool registry and unified ExecuteResponse adapter."""
from __future__ import annotations

from google.protobuf import struct_pb2

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2
from runtime.capability_contract import declaration, to_proto as contract_proto

from .builtin import ToolInputError, datetime_parse, math_eval, unit_convert


#: 工具输入错误的用户话术（Android N-01，2026-09-24）：按工具给一句中文 + 怎么接着说。REJECTED 在 executor 里映射成
#: FAILED，聚合器对单步失败「Agent 自己的失败话术原样透传」（C11-B）——修前 `speech=str(exc)`，真机上英文问时间听到的是
#: 「unsupported datetime format」。英文诊断串只进 `error.message`（日志 / 观测）。
_REJECT_SPEECH = {
    "datetime.parse": "这个时间我没能换算出来，换个说法再试一次。",
    "unit.convert": "这两个单位我换算不了，目前支持长度、质量、速度和温度。",
    "math.eval": "这个算式我算不出来，换成纯数字的加减乘除再试一次。",
}
_REJECT_SPEECH_DEFAULT = "这一步没能完成，换个说法再试一次。"


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
                    contract=contract_proto(declaration(["text"], "read")),
                ),
                agent_pb2.Capability(
                    intent="unit.convert",
                    description="转换长度、质量、速度和温度单位",
                    slots=["value", "from_unit", "to_unit"],
                    examples=["1.5公里等于多少米"],
                    contract=contract_proto(declaration(
                        ["value", "from_unit", "to_unit"], "read", parameters={
                            "value": {"type": "number"}, "from_unit": {"type": "string"},
                            "to_unit": {"type": "string"}})),
                ),
                agent_pb2.Capability(
                    intent="math.eval",
                    description="计算受限的纯算术表达式",
                    slots=["expression"],
                    examples=["2加3乘4"],
                    contract=contract_proto(declaration(["expression"], "read")),
                ),
            ],
        )

    async def call(self, intent: str, slots: dict, ctx):
        handler = self._handlers.get(intent)
        if handler is None:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.FAILED,
                error=common_pb2.ErrorInfo(
                    code="tool_not_found", message=f"unknown tool: {intent}"),
            )
        try:
            # 这一步的用户原话（派发已按 `step_call_context` 换好）：时间工具在规划器漏给 / 缩掉时钟问句时按它认（2026-10-09）
            data, speech = handler(slots, self._now_fn, str(getattr(ctx, "raw_text", "") or ""))
        except ToolInputError as exc:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.REJECTED,
                speech=_REJECT_SPEECH.get(intent, _REJECT_SPEECH_DEFAULT),
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
