"""端侧 `DescribeSession`：会话身份与能力摘要（AR05 §6.1 / R14）。

要守的三件事：

1. **注册 ≠ 在线，在线 ≠ 有权限**——三种"不能用"必须分得开；
2. **「此刻查不到」不等于「你没有这个能力」**——云侧取不到时标 partial，
   不是把云能力从列表里悄悄抹掉；
3. 响应与日志**不含 token**。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from server import EdgeOrchestratorServicer


def _describe(srv, scopes: str | None = "vehicle.control", *, extra_meta=None):
    meta = dict(extra_meta or {})
    if scopes is not None:
        meta["granted_scopes"] = scopes
    req = orchestrator_pb2.SessionInfoRequest(
        meta=meta, context=common_pb2.ContextRef(user_id="u1", vehicle_id="v1"))
    return asyncio.run(srv.DescribeSession(req, None))


def _servicer(cloud_response=None, cloud_error=None):
    srv = EdgeOrchestratorServicer()

    async def fake(meta, context=None, *, timeout=3.0):
        if cloud_error is not None:
            raise cloud_error
        return cloud_response

    srv.cloud.describe_session = fake
    return srv


def _by_id(resp):
    return {c.id: c for c in resp.capabilities}


def test_capability_status_separates_unauthorized_from_available():
    srv = _servicer(cloud_response=orchestrator_pb2.SessionInfoResponse(
        summary_status="complete"))

    resp = _describe(srv, "vehicle.control")

    caps = _by_id(resp)
    assert caps["edge-vehicle"].status == "available"
    assert caps["edge-vehicle"].reason_code == ""
    # 同一份 token 没有 media.control ⇒ 媒体能力如实标未授权，不跟着车控一起说"能用"
    assert caps["edge-media"].status == "unauthorized"
    assert caps["edge-media"].reason_code == "scope_missing"


def test_no_scope_at_all_marks_every_capability_unauthorized(monkeypatch):
    monkeypatch.setenv("PERMISSIONS_FAIL_OPEN", "false")
    srv = _servicer(cloud_response=orchestrator_pb2.SessionInfoResponse())

    resp = _describe(srv, scopes=None)

    assert resp.authorization_source == "fail_closed"
    assert {c.status for c in resp.capabilities} == {"unauthorized"}


def test_poc_default_is_reported_as_a_different_source_than_a_token(monkeypatch):
    """PoC 默认放行与明确 token 授权**不是一回事**，摘要必须分开说。"""
    monkeypatch.delenv("PERMISSIONS_FAIL_OPEN", raising=False)
    srv = _servicer(cloud_response=orchestrator_pb2.SessionInfoResponse())

    assert _describe(srv, scopes=None).authorization_source == "poc_default"
    assert _describe(srv, "vehicle.control").authorization_source == "token"


def test_cloud_unreachable_marks_summary_partial_not_empty_capabilities():
    """云端查不到 ⇒ partial + 原因；本地那半照常给，不整份作废也不谎称完整。"""
    srv = _servicer(cloud_error=RuntimeError("cloud channel not connected"))

    resp = _describe(srv, "vehicle.control")

    assert resp.summary_status == "partial"
    assert resp.summary_reason == "cloud_unreachable"
    assert "edge-vehicle" in _by_id(resp)


def test_cloud_partial_reason_is_propagated():
    srv = _servicer(cloud_response=orchestrator_pb2.SessionInfoResponse(
        summary_status="partial", summary_reason="query_failed"))

    resp = _describe(srv, "vehicle.control")

    assert (resp.summary_status, resp.summary_reason) == ("partial", "query_failed")


def test_edge_rows_override_the_clouds_unknown_for_the_same_capability():
    """云侧看不见车辆通道在不在，只能给 unknown；端侧就在车上，它的判断更权威。"""
    srv = _servicer(cloud_response=orchestrator_pb2.SessionInfoResponse(
        summary_status="complete",
        capabilities=[
            orchestrator_pb2.CapabilityStatus(
                id="edge-vehicle", status="unknown",
                reason_code="vehicle_channel_unverified"),
            orchestrator_pb2.CapabilityStatus(id="reminder", status="available"),
        ]))

    caps = _by_id(_describe(srv, "vehicle.control"))

    assert caps["edge-vehicle"].status == "available"
    assert caps["reminder"].status == "available", "云侧独有的能力不能被丢掉"
    assert len([c for c in caps if c == "edge-vehicle"]) == 1, "同一能力不许出现两行"


def test_summary_never_echoes_a_token():
    """响应里不得出现凭证——哪怕调用方把它塞进了 meta。"""
    srv = _servicer(cloud_response=orchestrator_pb2.SessionInfoResponse())

    resp = _describe(srv, "vehicle.control",
                     extra_meta={"token": "super-secret-token"})

    assert "super-secret-token" not in str(resp)


def test_summary_carries_contract_version_and_freshness_window():
    srv = _servicer(cloud_response=orchestrator_pb2.SessionInfoResponse())

    resp = _describe(srv, "vehicle.control")

    assert resp.contract_version.startswith("ar05.")
    assert resp.expires_at_ms > resp.generated_at_ms
    assert resp.user_id == "u1" and resp.vehicle_id == "v1"


def test_contract_version_is_declared_once_for_both_sides():
    """端云各写一个字面量，第一次 bump 就会各说各话。"""
    from runtime.contract_version import AR05_CONTRACT_VERSION
    from orchestrator.cloud.server import CONTRACT_VERSION

    assert CONTRACT_VERSION is AR05_CONTRACT_VERSION


# ─── 确认策略的风险档由端侧 VAL 受控知识纠正（AR05 §4.1）────────────────────
#
# 真栈实测（release `00d1925`）：「打开后备箱」的 confirm_policy 拿到 risk=medium /
# reason_code=agent_requested，而 `trunk` 在 VAL 的 commands.yaml 里 require_confirm=true。
# 云侧看不到那份知识（B1 后确认闸下沉 VAL、capability 不再声明），只能诚实记
# agent_requested；**端侧在出口盖**才是这条判据该住的地方（同 driving 的先例）。

from cockpit.orchestrator.v1 import orchestrator_pb2 as _pb


def _policy_event(intent: str, risk: str = "medium", channels=("touch", "text", "voice")):
    final = _pb.FinalResult(speech="要打开吗？", need_confirm=True, operation_id="op-1")
    final.confirm_policy.CopyFrom(_pb.ConfirmPolicy(
        operation_id="op-1", risk=risk, allowed_channels=list(channels),
        action_summary=intent, object_summary=intent.split(".")[0],
        reason_code="agent_requested", target_intent=intent))
    return _pb.HandleEvent(final=final)


def test_val_declared_danger_raises_the_risk_and_names_the_real_reason():
    srv = EdgeOrchestratorServicer()

    out = srv._stamp_confirm_policy(_policy_event("trunk.open"))

    policy = out.final.confirm_policy
    assert policy.risk == "high", "VAL 声明 require_confirm 的对象不能报 medium"
    assert policy.reason_code == "require_confirm", "原因要说实话：这是受控声明的危险动作"


def test_edge_never_lowers_a_risk_the_cloud_already_raised():
    """只提升不降低：VAL 说 low 只意味着「没在这份知识里被标危险」，不等于安全。"""
    srv = EdgeOrchestratorServicer()

    out = srv._stamp_confirm_policy(_policy_event("window.open", risk="high"))

    assert out.final.confirm_policy.risk == "high"


def test_non_dangerous_object_keeps_the_cloud_value():
    srv = EdgeOrchestratorServicer()
    out = srv._stamp_confirm_policy(_policy_event("window.open"))
    assert out.final.confirm_policy.risk == "medium"
    assert out.final.confirm_policy.reason_code == "agent_requested"


def test_unknown_or_missing_target_intent_changes_nothing():
    srv = EdgeOrchestratorServicer()
    assert srv._stamp_confirm_policy(
        _policy_event("nothing.likethis")).final.confirm_policy.risk == "medium"
    blank = _policy_event("trunk.open")
    blank.final.confirm_policy.target_intent = ""
    assert srv._stamp_confirm_policy(blank).final.confirm_policy.risk == "medium"


def test_turn_without_a_confirm_policy_is_untouched():
    srv = EdgeOrchestratorServicer()
    plain = _pb.HandleEvent(final=_pb.FinalResult(speech="晴。"))
    out = srv._stamp_confirm_policy(plain)
    assert out.final.HasField("confirm_policy") is False


def test_voice_forbidden_objects_lose_the_voice_channel(monkeypatch):
    """既有禁令不因用户在 App 上点了按钮而放宽。"""
    srv = EdgeOrchestratorServicer()
    objects = (srv.val.commands or {}).get("objects") or {}
    assert "trunk" in objects
    monkeypatch.setitem(objects["trunk"], "voice_forbidden", True)

    out = srv._stamp_confirm_policy(_policy_event("trunk.open"))

    assert list(out.final.confirm_policy.allowed_channels) == ["touch", "text"]
