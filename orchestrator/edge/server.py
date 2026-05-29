"""Edge Orchestrator gRPC 服务：快意图本地秒回，慢意图上云，云端不可达则降级。

Phase 1 改进：云端 action 分发（车控→VAL）、连接状态追踪、降级增强。
"""
from __future__ import annotations
import os
import logging

from google.protobuf import struct_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2

from fast_intent import classify, is_local
from val import VAL
from edge_agents import edge_execute
from cloud_client import CloudClient

logger = logging.getLogger("edge.orchestrator")

_HIGH = float(os.getenv("FAST_INTENT_THRESHOLD_HIGH", "0.85"))


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d or {})
    return s


class EdgeOrchestratorServicer(orchestrator_pb2_grpc.EdgeOrchestratorServicer):
    def __init__(self):
        self.val = VAL()
        self.cloud = CloudClient()
        self.cloud_connected = False  # 连接状态追踪

    async def Handle(self, request, context):
        intent = classify(request.text)

        # 快路径：高置信本地意图，端侧秒回（离线可用，不依赖网络）
        if intent and intent["confidence"] >= _HIGH and is_local(intent["name"]):
            speech, action = edge_execute(intent, self.val)
            final = orchestrator_pb2.FinalResult(speech=speech)
            if action:
                final.actions.append(common_pb2.AgentAction(
                    type=action["type"], payload=_struct(action["payload"]),
                    require_confirm=action["require_confirm"]))
            logger.info("LOCAL %s -> %s", intent["name"], speech)
            yield orchestrator_pb2.HandleEvent(final=final)
            return

        # 慢路径：上云编排
        logger.info("CLOUD route: %s", request.text)
        try:
            got = False
            async for event in self.cloud.handle(request):
                got = True
                self.cloud_connected = True
                # 云端回流 action 分发：车控类走 VAL
                event = self._dispatch_cloud_actions(event)
                yield event
            if not got:
                yield orchestrator_pb2.HandleEvent(
                    final=orchestrator_pb2.FinalResult(speech="抱歉，我没能理解这个请求。"))
        except Exception as e:
            self.cloud_connected = False
            logger.warning("Cloud unavailable, degrade: %s", e)
            # 降级：尝试端侧 SLM（如有）或返回降级话术
            yield orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(
                speech="网络不太好，复杂请求暂时无法处理，不过车内控制依然可以正常使用。"))

    def _dispatch_cloud_actions(self, event):
        """云端回流 action 分发：车控类交 VAL 执行，落实规划/执行分离。

        LLM/Planner 只产出 vehicle.control 意图，真正下发由端侧 VAL 做：
        1. 权限校验
        2. 安全态门控（行驶中禁某些操作）
        3. 状态变更
        """
        which = event.WhichOneof("event")
        if which != "final":
            return event

        final = event.final
        dispatched_actions = []
        for action in final.actions:
            if action.type.startswith("vehicle.control"):
                # 车控 action：交 VAL 执行
                cmd = action.payload.fields.get("command", "").string_value or action.type
                args = dict(action.payload.fields) if action.payload else {}
                ok, msg = self.val.execute(cmd, args)
                if ok:
                    logger.info("VAL executed: %s -> %s", cmd, msg)
                    # 用 VAL 的执行结果替换原始 speech
                else:
                    logger.warning("VAL rejected: %s -> %s", cmd, msg)
                    # 安全门控拒绝：替换 speech
            dispatched_actions.append(action)

        return event
