"""能力摘要：把「注册目录 + 本次授权」算成用户能看懂的可用状态（AR05 §6.1）。

端云共用一份判据。要点是**三种"不能用"必须分得开**：

- `unauthorized`：注册着、服务也在，但**当前账号没有这条能力要的 scope**；
- `unavailable`：注册着但通道/服务不在线；
- `unknown`：依赖不可核实（查询失败、云侧看不到车辆通道在不在）。

把后两者说成 `available` 就是在宣称一件没被证实的事；把它们混成一种，
用户就无从知道该去连设备还是该去要授权。**注册 ≠ 在线，在线 ≠ 有权限。**

判定本体仍是 `security.permission.check_permission`（运行时唯一权限决策），
这里只做「一个 manifest + 一份 granted → 一行摘要」的投影。
"""
from __future__ import annotations

from .permission import check_permission

STATUS_AVAILABLE = "available"
STATUS_UNAUTHORIZED = "unauthorized"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNKNOWN = "unknown"

REASON_SCOPE_MISSING = "scope_missing"
REASON_OFFLINE = "offline"
REASON_QUERY_FAILED = "query_failed"
#: 云侧看得见 edge Agent 的注册记录，但**看不见那辆车的通道此刻在不在**。
#: 端侧回答同一个问题时才有资格说 available。
REASON_VEHICLE_CHANNEL_UNVERIFIED = "vehicle_channel_unverified"


def capability_row(manifest, granted, *, online: bool | None = True,
                   unknown_reason: str = "") -> dict:
    """一个 manifest → 一行能力摘要 dict（字段同 `CapabilityStatus`）。

    `online=None` 表示**调用方无法核实在线与否**——那就诚实给 unknown，
    而不是默认它活着。权限不足优先于在线与否：没授权时，在不在线不影响结论。
    """
    agent_id = str(getattr(manifest, "agent_id", "") or "")
    required = list(getattr(manifest, "requires_permissions", None) or [])
    decision = check_permission(
        agent_id=agent_id,
        trust_level=str(getattr(manifest, "trust_level", "") or "first_party"),
        required=required,
        granted=granted,
        kind=str(getattr(manifest, "kind", "") or "agent"),
    )
    if not decision.allowed:
        status, reason = STATUS_UNAUTHORIZED, REASON_SCOPE_MISSING
    elif online is None:
        status, reason = STATUS_UNKNOWN, (
            unknown_reason or REASON_VEHICLE_CHANNEL_UNVERIFIED)
    elif not online:
        status, reason = STATUS_UNAVAILABLE, REASON_OFFLINE
    else:
        status, reason = STATUS_AVAILABLE, ""
    return {
        "id": agent_id,
        "display_name": str(getattr(manifest, "display_name", "") or "") or agent_id,
        "status": status,
        "reason_code": reason,
    }


def merge_rows(*groups) -> list[dict]:
    """按 id 合并多组摘要，**后来的更有权威**（端侧对 edge 能力比云侧更清楚）。

    合并而不是拼接：同一个能力出现两行，用户看到的就是自相矛盾的两句话。
    """
    merged: dict[str, dict] = {}
    for rows in groups:
        for row in (rows or []):
            if isinstance(row, dict) and row.get("id"):
                merged[row["id"]] = row
    return sorted(merged.values(), key=lambda r: r["id"])
