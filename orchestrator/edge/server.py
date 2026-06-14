"""Edge Orchestrator gRPC 服务：快意图本地秒回，慢意图上云，云端不可达则降级。

Phase 1 改进：云端 action 分发（车控→VAL）、连接状态追踪、降级增强。
"""
from __future__ import annotations
import os
import logging

from google.protobuf import struct_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2

from fast_intent import classify, classify_structured, is_local, split_and_classify, structured_to_legacy
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
        # 确认/补槽续接必须回到挂起会话所在的云端，不走本地快路径
        if request.is_confirmation:
            intent = None
            multi = None
        else:
            multi = split_and_classify(request.text)
            intent = None if multi else classify(request.text)

        # 快路径 A：多意图全部本地，并行执行聚合语音
        if multi:
            speeches = []
            for m_intent in multi:
                legacy = structured_to_legacy(m_intent)
                if legacy and legacy["confidence"] >= _HIGH and is_local(legacy["name"]):
                    # 结构化命令直通 VAL
                    ok, speech = self.val.execute(m_intent)
                    if not ok:
                        speech = speech or "操作失败"
                    speeches.append(speech)
                    logger.info("MULTI-LOCAL %s -> %s", legacy["name"], speech)
                else:
                    # 单个子意图无法本地处理，走云（保守策略）
                    logger.info("MULTI sub-intent needs cloud, falling through")
                    speeches = []
                    break
            if speeches:
                combined = "，".join(speeches)
                final = orchestrator_pb2.FinalResult(speech=combined)
                yield orchestrator_pb2.HandleEvent(final=final)
                return

        # 快路径 B：高置信本地意图，端侧秒回（离线可用，不依赖网络）
        if intent and intent["confidence"] >= _HIGH and is_local(intent["name"]):
            # 尝试结构化命令直通 VAL（覆盖新意图：trunk/door_lock/seat/ambient_light 等）
            structured = classify_structured(request.text)
            if structured:
                ok, speech = self.val.execute(structured)
                action_type = "vehicle.control" if structured.get("data", {}).get("object") not in ("media",) else "media.control"
                action = {
                    "type": action_type,
                    "payload": {"command": intent["name"], **intent.get("slots", {})},
                    "require_confirm": False,
                } if ok else None
            else:
                # 回退旧路径
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
        new_speech = final.speech
        for action in final.actions:
            if not action.type.startswith("vehicle.control"):
                continue
            # F14：payload 用 AsMap()（原生类型），避免 protobuf Value 类型错误
            payload = action.payload.AsMap() if action.payload else {}
            cmd = payload.get("command", action.type)
            ok, msg = self.val.execute(cmd, payload)
            if ok:
                logger.info("VAL executed: %s -> %s", cmd, msg)
                new_speech = msg  # 用 VAL 执行结果替换 speech
            else:
                logger.warning("VAL rejected: %s -> %s", cmd, msg)
                new_speech = msg  # 安全门控拒绝：替换为拒绝原因

        # F14：真正替换 speech（之前构建了 dispatched_actions 但丢弃了）
        final.speech = new_speech
        return event
