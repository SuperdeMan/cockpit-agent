"""Edge Orchestrator gRPC 服务：快意图本地秒回，慢意图上云，云端不可达则降级。

Phase 1 改进：云端 action 分发（车控→VAL）、连接状态追踪、降级增强。
"""
from __future__ import annotations
import os
import logging

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2

from fast_intent import classify, classify_structured, is_local, split_and_classify, split_and_classify_any, structured_to_legacy
from val import VAL
from edge_agents import edge_execute
from cloud_client import CloudClient
from edge_call import EdgeCallExecutor

logger = logging.getLogger("edge.orchestrator")

_HIGH = float(os.getenv("FAST_INTENT_THRESHOLD_HIGH", "0.85"))


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d or {})
    return s


class EdgeOrchestratorServicer(orchestrator_pb2_grpc.EdgeOrchestratorServicer):
    def __init__(self):
        self.val = VAL()
        self.cloud = CloudClient(edge_call_executor=EdgeCallExecutor(self.val))
        self.cloud_connected = False  # 连接状态追踪

    async def Handle(self, request, context):
        # 从 request.meta 读取 HMI 设置
        meta = dict(request.meta) if request.meta else {}
        answer_length = meta.get("answer_length", "short")

        # 确认/补槽续接必须回到挂起会话所在的云端，不走本地快路径
        if request.is_confirmation:
            intent = None
            multi = None
            mixed_intents = None
        else:
            multi = split_and_classify(request.text)
            mixed_intents = None
            if multi:
                intent = None
            else:
                # 全有全无失败 → 尝试混合拆分（本地+非本地）
                mixed_intents = split_and_classify_any(request.text)
                intent = None if mixed_intents else classify(request.text)

        # 快路径 A：多意图全部本地，并行执行聚合语音
        if multi:
            speeches = []
            actions = []
            for m_intent in multi:
                legacy = structured_to_legacy(m_intent)
                if legacy and legacy["confidence"] >= _HIGH and is_local(legacy["name"]):
                    # 结构化命令直通 VAL
                    ok, speech = self.val.execute(m_intent, answer_length=answer_length)
                    if not ok:
                        speech = speech or "操作失败"
                    speeches.append(speech)
                    # 构造 action
                    obj = m_intent.get("data", {}).get("object", "")
                    action_type = "media.control" if obj in ("media", "music", "radio", "online_radio", "audiobook", "opera", "news", "video", "TV") else "vehicle.control"
                    actions.append(common_pb2.AgentAction(
                        type=action_type,
                        payload=_struct({"command": legacy["name"], **legacy.get("slots", {})}),
                        require_confirm=False,
                    ))
                    logger.info("MULTI-LOCAL %s -> %s", legacy["name"], speech)
                else:
                    # 单个子意图无法本地处理，走云（保守策略）
                    logger.info("MULTI sub-intent needs cloud, falling through")
                    speeches = []
                    break
            if speeches:
                combined = "，".join(speeches)
                final = orchestrator_pb2.FinalResult(speech=combined)
                final.actions.extend(actions)
                yield orchestrator_pb2.HandleEvent(final=final)
                return

        # 快路径 A2：混合意图（部分本地 + 部分非本地）。
        # 本地意图立即经 VAL 执行，非本地意图上云编排。
        if mixed_intents:
            local_speeches = []
            local_actions = []
            cloud_parts = []  # 非本地意图的原始文本片段
            for m_intent in mixed_intents:
                legacy = structured_to_legacy(m_intent)
                if legacy and legacy["confidence"] >= _HIGH and is_local(legacy["name"]):
                    ok, speech = self.val.execute(m_intent, answer_length=answer_length)
                    if not ok:
                        speech = speech or "操作失败"
                    local_speeches.append(speech)
                    obj = m_intent.get("data", {}).get("object", "")
                    action_type = "media.control" if obj in ("media", "music", "radio", "online_radio", "audiobook", "opera", "news", "video", "TV") else "vehicle.control"
                    local_actions.append(common_pb2.AgentAction(
                        type=action_type,
                        payload=_struct({"command": legacy["name"], **legacy.get("slots", {})}),
                        require_confirm=False,
                    ))
                    logger.info("MIXED-LOCAL %s -> %s", legacy["name"], speech)
                else:
                    # 非本地意图：从原始文本中提取对应片段
                    raw = m_intent.get("_raw_text", "")
                    if raw:
                        cloud_parts.append(raw)
                    logger.info("MIXED-CLOUD %s (non-local)",
                                m_intent.get("data", {}).get("object", ""))

            if cloud_parts:
                # 有非本地意图：先返回本地结果，再把非本地片段上云
                if local_speeches:
                    combined = "，".join(local_speeches)
                    final = orchestrator_pb2.FinalResult(speech=combined)
                    final.actions.extend(local_actions)
                    yield orchestrator_pb2.HandleEvent(final=final)

                # 把非本地子句拼接后上云
                cloud_text = "，".join(cloud_parts)
                logger.info("MIXED: local done, sending to cloud: %s", cloud_text[:60])
                try:
                    got = False
                    # 构造只含非本地子句的请求副本
                    cloud_req = orchestrator_pb2.HandleRequest(
                        text=cloud_text,
                        session_id=request.session_id,
                        request_id=request.request_id,
                        is_confirmation=False,
                        meta=request.meta,
                        context=request.context,
                    )
                    async for event in self.cloud.handle(cloud_req):
                        got = True
                        self.cloud_connected = True
                        event = self._dispatch_cloud_actions(event, answer_length)
                        yield event
                    if not got:
                        yield orchestrator_pb2.HandleEvent(
                            final=orchestrator_pb2.FinalResult(
                                speech="非本地请求处理失败，请稍后重试。"))
                except Exception as e:
                    self.cloud_connected = False
                    logger.warning("MIXED cloud unavailable: %s", e)
                    yield orchestrator_pb2.HandleEvent(
                        final=orchestrator_pb2.FinalResult(
                            speech="网络不太好，部分请求暂时无法处理。"))
                return
            else:
                # 全部本地（不应该到这里，multi 应该已经捕获）
                if local_speeches:
                    combined = "，".join(local_speeches)
                    final = orchestrator_pb2.FinalResult(speech=combined)
                    final.actions.extend(local_actions)
                    yield orchestrator_pb2.HandleEvent(final=final)
                return

        # 快路径 B：高置信本地意图，端侧秒回（离线可用，不依赖网络）
        if intent and intent["confidence"] >= _HIGH and is_local(intent["name"]):
            # 尝试结构化命令直通 VAL（覆盖新意图：trunk/door_lock/seat/ambient_light 等）
            structured = classify_structured(request.text)
            if structured:
                ok, speech = self.val.execute(structured, answer_length=answer_length)
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
        cloud_speech = ""
        cloud_has_actions = False
        try:
            got = False
            async for event in self.cloud.handle(request):
                got = True
                self.cloud_connected = True
                # 云端回流 action 分发：车控类走 VAL
                event = self._dispatch_cloud_actions(event, answer_length)
                which = event.WhichOneof("event")
                if which == "final":
                    cloud_speech = event.final.speech
                    cloud_has_actions = len(event.final.actions) > 0
                yield event
            if not got:
                yield orchestrator_pb2.HandleEvent(
                    final=orchestrator_pb2.FinalResult(speech="抱歉，我没能理解这个请求。"))
                return
        except Exception as e:
            self.cloud_connected = False
            logger.warning("Cloud unavailable, degrade: %s", e)
            yield orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(
                speech="网络不太好，复杂请求暂时无法处理，不过车内控制依然可以正常使用。"))
            return

        # 兜底：云端返回空 speech 且无 actions → 尝试端侧 VAL 本地执行
        # 场景：LLM 规划失败 → chitchat 兜底但无实质回复 → 但原意可能是车控
        if not cloud_speech and not cloud_has_actions:
            local_structured = classify_structured(request.text)
            if local_structured:
                ok, speech = self.val.execute(local_structured, answer_length=answer_length)
                if ok and speech:
                    obj = local_structured.get("data", {}).get("object", "")
                    action_type = "media.control" if obj in ("media", "music", "radio") else "vehicle.control"
                    action = common_pb2.AgentAction(
                        type=action_type,
                        payload=_struct({"command": f"{obj}.{local_structured['data'].get('operate', '')}"}),
                        require_confirm=False,
                    )
                    final = orchestrator_pb2.FinalResult(speech=speech)
                    final.actions.append(action)
                    logger.info("CLOUD-DEGRADED-LOCAL %s -> %s", obj, speech)
                    yield orchestrator_pb2.HandleEvent(final=final)

    def _dispatch_cloud_actions(self, event, answer_length="short"):
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
            payload = MessageToDict(
                action.payload, preserving_proto_field_name=True
            ) if action.payload else {}
            cmd = payload.get("command", action.type)
            ok, msg = self.val.execute(cmd, payload, answer_length=answer_length)
            if ok:
                logger.info("VAL executed: %s -> %s", cmd, msg)
                new_speech = msg  # 用 VAL 执行结果替换 speech
            else:
                logger.warning("VAL rejected: %s -> %s", cmd, msg)
                new_speech = msg  # 安全门控拒绝：替换为拒绝原因

        # F14：真正替换 speech（之前构建了 dispatched_actions 但丢弃了）
        final.speech = new_speech
        return event
