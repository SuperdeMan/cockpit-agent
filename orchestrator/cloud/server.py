"""Cloud Planner gRPC 服务：对 Cloud Gateway 暴露流式 Handle。

Phase 1：使用 PlannerEngine（DAG 编排 + 多轮 + 聚合）。
"""
from __future__ import annotations

import logging
import time

from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2
from google.protobuf import struct_pb2

from runtime.contract_version import AR05_CONTRACT_VERSION
from runtime.issues import issues_to_proto
from security import capability_status
from security.session_scopes import resolve_granted_scopes

from .engine import PlannerEngine

logger = logging.getLogger("planner.server")


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


#: 契约版本经 `runtime.contract_version` 单一声明（端侧同源）。别名保留供既有引用。
CONTRACT_VERSION = AR05_CONTRACT_VERSION
#: 能力摘要有效期（秒）。过期后客户端标未知/待刷新，不得用缓存授权执行。
_SESSION_INFO_TTL_S = 60


class CloudPlannerServicer(orchestrator_pb2_grpc.CloudPlannerServicer):
    def __init__(self, engine: PlannerEngine):
        self.engine = engine

    async def DescribeSession(self, request, context):
        """AR05 §6.1：会话身份与能力摘要的**只读**查询。

        零业务副作用：不调 LLM、不写会话历史、不碰挂起表。能力面取 Registry
        **完整目录**（`list_agents`），不是 Planner 当轮的语义 top-k 或预算裁剪后的
        catalog——后者会把「这轮没被选上」说成「你没有这个能力」。

        云侧看得见 edge Agent 的注册记录，但看不见那辆车的通道此刻在不在，
        故 edge 部署的能力一律给 `unknown`，由端侧用它自己的事实覆盖。
        """
        meta = dict(getattr(request, "meta", None) or {})
        granted, source = resolve_granted_scopes(meta)
        rows: list[dict] = []
        summary_status, summary_reason = "complete", ""
        try:
            agents = await self.engine.clients.list_agents()
        except Exception as exc:                     # 查询失败不许说成"没有能力"
            logger.warning("DescribeSession: registry list failed: %s", exc)
            agents = None
            summary_status, summary_reason = "partial", capability_status.REASON_QUERY_FAILED
        if agents is not None:
            for a in agents:
                manifest = getattr(a, "manifest", a)
                edge = str(getattr(manifest, "deployment", "") or "") == "edge"
                rows.append(capability_status.capability_row(
                    manifest, granted, online=None if edge else True))
        now = int(time.time() * 1000)
        ctx_ref = getattr(request, "context", None)
        return orchestrator_pb2.SessionInfoResponse(
            contract_version=CONTRACT_VERSION,
            # 认证与否由网关判定并注入；云侧只如实转述它拿到的授权来源。
            authenticated=bool(meta.get("authenticated") == "true"),
            user_id=str(getattr(ctx_ref, "user_id", "") or ""),
            vehicle_id=str(getattr(ctx_ref, "vehicle_id", "") or ""),
            authorization_source=source,
            granted_scopes=list(granted),
            capabilities=[
                orchestrator_pb2.CapabilityStatus(
                    id=r["id"], display_name=r["display_name"],
                    status=r["status"], reason_code=r["reason_code"])
                for r in capability_status.merge_rows(rows)],
            generated_at_ms=now,
            expires_at_ms=now + _SESSION_INFO_TTL_S * 1000,
            summary_status=summary_status,
            summary_reason=summary_reason,
        )

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
                    # AR05 §4.3：换题后仍有效的挂起（客户端据此撤下当前追问但保留任务）
                    held_operation_ids=list(
                        event.get("held_operation_ids") or []),
                )
                # 透传 ui_card（Agent 返回的结构化卡片数据给 HMI）
                ui_card = event.get("ui_card")
                if ui_card and isinstance(ui_card, dict):
                    final.ui_card.update(ui_card)
                _fill_contracts(final, event)
                yield orchestrator_pb2.HandleEvent(final=final)
