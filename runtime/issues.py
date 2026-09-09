"""结构化问题（AR05 `Issue`）的**跨服务唯一**声明与装配。

端侧 T0（权限拒绝、VAL 安全拒绝、降级）和云侧 Planner（技术失败、服务降级）
都要产这种东西。两边各写一份枚举，第一次改动就会漂移——而漂移的代价是客户端
按 code 分流的那张表突然对不上，症状是「问题显示成未知」而不是报错。

判据边界：本模块只定义**受控值**与**形状**，不判断「什么时候该产 issue」——
那是各出口自己的事实。客户端设备侧的问题（麦克风被拒、TTS 无声）由客户端按
实际设备结果产，同一套 code 与恢复动作。

字段表见 `docs/design/2026-09-09-ar05-structured-contracts-implementation-plan.md` §11.3。
"""
from __future__ import annotations

# ── Issue.code 受控枚举 ────────────────────────────────────────────────────
ISSUE_PERMISSION_SCOPE_MISSING = "permission.scope_missing"   # 业务 scope 不足（≠ 设备权限）
ISSUE_SAFETY_VAL_REJECTED = "safety.val_rejected"             # VAL 安全门控拒绝
ISSUE_SERVICE_DEGRADED = "service.degraded"                   # 云端不可达/能力降级
ISSUE_TRANSPORT_ERROR = "transport.error"                     # 网络/超时
ISSUE_AUTH_REJECTED = "auth.rejected"                         # token 被明确拒绝
ISSUE_PLANNER_TECHNICAL_FAILURE = "planner.technical_failure" # 规划技术失败（非法/空计划）
ISSUE_TTS_SILENT = "tts.silent"                               # 客户端：该出声却没出声
ISSUE_ASR_FALLBACK = "asr.fallback"                           # 客户端：流式退批处理
ISSUE_DEVICE_PERMISSION_DENIED = "device.permission_denied"   # 客户端：系统权限被拒

SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR = "info", "warning", "error"
SCOPE_REQUEST, SCOPE_OPERATION, SCOPE_SESSION, SCOPE_CAPABILITY = (
    "request", "operation", "session", "capability")

# ── 恢复动作 kind ─────────────────────────────────────────────────────────
# **只用受控 kind**：客户端只实现自己已支持的这几个固定动作，绝不执行服务端
# 下发的任意 URL、脚本或命令（AR05 §5.1）。
RECOVERY_OPEN_VOICE_SETTINGS = "open_voice_settings"
RECOVERY_OPEN_CAPABILITY_SETTINGS = "open_capability_settings"
RECOVERY_RECONFIGURE_CONNECTION = "reconfigure_connection"
RECOVERY_RETRY_REQUEST = "retry_request"
RECOVERY_REPLAY_AUDIO = "replay_audio"
RECOVERY_DISMISS = "dismiss"

RECOVERY_KINDS = frozenset({
    RECOVERY_OPEN_VOICE_SETTINGS, RECOVERY_OPEN_CAPABILITY_SETTINGS,
    RECOVERY_RECONFIGURE_CONNECTION, RECOVERY_RETRY_REQUEST,
    RECOVERY_REPLAY_AUDIO, RECOVERY_DISMISS,
})

ISSUE_CODES = frozenset({
    ISSUE_PERMISSION_SCOPE_MISSING, ISSUE_SAFETY_VAL_REJECTED,
    ISSUE_SERVICE_DEGRADED, ISSUE_TRANSPORT_ERROR, ISSUE_AUTH_REJECTED,
    ISSUE_PLANNER_TECHNICAL_FAILURE, ISSUE_TTS_SILENT, ISSUE_ASR_FALLBACK,
    ISSUE_DEVICE_PERMISSION_DENIED,
})


def build_issue(code: str, message: str, *, severity: str = SEVERITY_WARNING,
                scope: str = SCOPE_REQUEST, request_id: str = "",
                operation_id: str = "", affected_capabilities=None,
                recovery=None) -> dict:
    """装一条 issue。`recovery` 传 `[(kind, label), ...]`，未知 kind 直接丢弃。

    丢弃而不是抛：一条问题的**主体信息是 message**，因为一个拼错的恢复 kind
    把整条问题弄没了，用户就连「出什么事了」都看不到。
    """
    return {
        "code": code,
        "message": message,
        "severity": severity,
        "scope": scope,
        "request_id": request_id,
        "operation_id": operation_id,
        "affected_capabilities": [str(c) for c in (affected_capabilities or [])],
        "recovery": [{"kind": kind, "label": label}
                     for kind, label in (recovery or [])
                     if kind in RECOVERY_KINDS],
    }


def issues_to_proto(issues, orchestrator_pb2) -> list:
    """`build_issue` 的 dict 列表 → `orchestrator.v1.Issue` 列表。

    proto 模块由调用方传入：`runtime/` 不硬依赖 codegen 产物，而端侧与云侧
    各自的镜像里 `gen/python` 的位置不同。**转换只有这一份**——两边各写一次，
    下一个新字段就会只在一边被填上。
    """
    out = []
    for item in (issues or []):
        if not isinstance(item, dict) or not item.get("code"):
            continue
        out.append(orchestrator_pb2.Issue(
            code=item.get("code", ""),
            message=item.get("message", ""),
            severity=item.get("severity", ""),
            scope=item.get("scope", ""),
            request_id=item.get("request_id", ""),
            operation_id=item.get("operation_id", ""),
            affected_capabilities=list(item.get("affected_capabilities") or []),
            recovery=[orchestrator_pb2.RecoveryAction(
                kind=r.get("kind", ""), label=r.get("label", ""))
                for r in (item.get("recovery") or []) if isinstance(r, dict)],
        ))
    return out
