"""Cloud Planner gRPC 服务：对 Cloud Gateway 暴露流式 Handle。

Phase 1：使用 PlannerEngine（DAG 编排 + 多轮 + 聚合）。
"""
from __future__ import annotations

from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2
from google.protobuf import struct_pb2

from runtime.issues import issues_to_proto

from .engine import PlannerEngine


def _fill_contracts(final, event: dict) -> None:
    """AR05 结构化契约 dict → proto。**只在服务端真产出时填**，不发恒空消息：
    旧客户端要能分辨「没有这个字段」和「有但为空」（同 driving 那条反过来的先例——
    driving 是恒带键，这三个是有才带，因为它们的缺席本身就是「本轮没有挂起/没有问题」）。"""
    policy = event.get("confirm_policy")
    if isinstance(policy, dict) and policy:
        final.confirm_policy.CopyFrom(orchestrator_pb2.ConfirmPolicy(
            operation_id=policy.get("operation_id", ""),
            risk=policy.get("risk", ""),
            allowed_channels=list(policy.get("allowed_channels") or []),
            action_summary=policy.get("action_summary", ""),
            object_summary=policy.get("object_summary", ""),
            reason_code=policy.get("reason_code", ""),
            expires_at_ms=int(policy.get("expires_at_ms") or 0),
            server_now_ms=int(policy.get("server_now_ms") or 0),
            summary_source=policy.get("summary_source", ""),
            target_intent=policy.get("target_intent", ""),
        ))
    slot = event.get("slot_request")
    if isinstance(slot, dict) and slot:
        final.slot_request.CopyFrom(orchestrator_pb2.SlotRequest(
            operation_id=slot.get("operation_id", ""),
            slot=slot.get("slot", ""),
            display_name=slot.get("display_name", ""),
            shape=slot.get("shape", ""),
            suggestions=list(slot.get("suggestions") or []),
            state=slot.get("state", ""),
            remaining_slots=list(slot.get("remaining_slots") or []),
            prompt=slot.get("prompt", ""),
            expires_at_ms=int(slot.get("expires_at_ms") or 0),
            server_now_ms=int(slot.get("server_now_ms") or 0),
        ))
    final.issues.extend(issues_to_proto(event.get("issues"), orchestrator_pb2))


def _to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


class CloudPlannerServicer(orchestrator_pb2_grpc.CloudPlannerServicer):
    def __init__(self, engine: PlannerEngine):
        self.engine = engine

    async def Handle(self, request, context):
        async for event in self.engine.run(request):
            kind = event.get("kind")
            if kind == "speech":
                yield orchestrator_pb2.HandleEvent(speech_delta=event["delta"])
            elif kind == "action":
                yield orchestrator_pb2.HandleEvent(action=event["action"])
            elif kind == "progress":
                # 复杂任务过程区增量（脱敏）。driving 由 Edge 按 VAL 实时标注，此处恒 False。
                yield orchestrator_pb2.HandleEvent(
                    progress=orchestrator_pb2.ProcessUpdate(
                        phase=event.get("phase", ""),
                        label=event.get("label", ""),
                        summary=event.get("summary", ""),
                        status=event.get("status", ""),
                        step_id=event.get("step_id", ""),
                    ))
            elif kind == "final":
                actions = []
                for a in event.get("actions", []):
                    if isinstance(a, dict):
                        actions.append(common_pb2.AgentAction(
                            type=a.get("type", ""),
                            payload=_to_struct(a.get("payload", {})),
                            require_confirm=a.get("require_confirm", False),
                        ))
                    else:
                        actions.append(a)
                final = orchestrator_pb2.FinalResult(
                    speech=event.get("speech", ""),
                    follow_up=event.get("follow_up", ""),
                    need_confirm=event.get("need_confirm", False),
                    actions=actions,
                    emotion=event.get("emotion", "") or "",
                    # Q1-B 挂起寻址键（只有挂起 final 非空）
                    operation_id=event.get("operation_id", "") or "",
                    # Q1-C 本轮关掉的挂起（HMI 据此撤确认条）
                    closed_operation_ids=list(
                        event.get("closed_operation_ids") or []),
                )
                # 透传 ui_card（Agent 返回的结构化卡片数据给 HMI）
                ui_card = event.get("ui_card")
                if ui_card and isinstance(ui_card, dict):
                    final.ui_card.update(ui_card)
                _fill_contracts(final, event)
                yield orchestrator_pb2.HandleEvent(final=final)
