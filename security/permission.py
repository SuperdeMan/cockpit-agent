"""权限引擎：编排层 + 执行层共用。

有效权限 = min(trust_level 上限, 用户授权, 会话 token scope)。
third_party 额外剔除高危 scope。
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .scopes import (
    TRUST_LEVEL_CAPS, THIRD_PARTY_DENY_PREFIXES,
    is_scope_covered, ALL_SCOPES,
)


@dataclass
class AuthContext:
    """一次调用的认证/授权上下文。"""
    user_id: str = ""
    vehicle_id: str = ""
    token_scopes: list[str] = field(default_factory=list)   # 来自 WS4 token
    user_grants: dict[str, list[str]] = field(default_factory=dict)  # agent_id -> scopes


@dataclass
class Decision:
    """权限校验结果。"""
    allowed: bool
    missing: list[str] = field(default_factory=list)
    reason: str = ""


# 车控 scope 前缀（父）。third_party / tool 硬禁令据此判定。
_VEHICLE_CONTROL = "vehicle.control"


def _required_vehicle_control(required) -> list[str]:
    """列出 required 中命中车控的 scope（父 vehicle.control 或其子）。"""
    return [r for r in required
            if r == _VEHICLE_CONTROL or r.startswith(_VEHICLE_CONTROL + ".")]


def check_permission(*, agent_id: str, trust_level: str,
                     required, granted, kind: str = "agent") -> Decision:
    """运行时**唯一**权限决策。规划期 catalog 过滤与 dispatch 执行期均复用本函数。

    模型（与历史 dispatch/planning 内联逻辑逐字等价）：
    - 无 required → 放行（如 chitchat）。
    - 命中车控：third_party 或 tool 一律硬拒（无论是否授予）。
    - 其余：required 全部被 granted（父子覆盖，见 scopes.is_scope_covered）覆盖才放行。

    注：这里用扁平 granted + 父子覆盖，**不**做 trust_level cap 交集——运行时授予的是
    父 scope（如 vehicle.control），而 cap 只含子 scope，直接交集会误伤（如 first_party
    的 scene-orchestrator 需 vehicle.control）。cap 强上限属目标态，待 R3.1 真实 token
    scope 落地时随 caps 父子感知化再接（见 PermissionEngine.effective_scopes）。
    """
    required = list(required or [])
    if not required:
        return Decision(allowed=True)

    vc = _required_vehicle_control(required)
    if vc:
        if trust_level == "third_party":
            return Decision(allowed=False, missing=vc,
                            reason="third_party agents cannot request vehicle.control")
        if kind == "tool":
            return Decision(allowed=False, missing=vc,
                            reason="tools cannot request vehicle.control")

    granted_set = set(granted or [])
    missing = [r for r in required if not is_scope_covered(r, granted_set)]
    if missing:
        return Decision(allowed=False, missing=missing,
                        reason=f"missing permissions: {', '.join(missing)}")
    return Decision(allowed=True)


class PermissionEngine:
    """统一权限引擎。编排层（Planner）和执行层（VAL）共用。"""

    def effective_scopes(self, manifest, auth: AuthContext) -> set[str]:
        """计算某 Agent 在当前 auth 下的有效权限（trust-cap ∩ 授权，third_party 剔高危）。

        目标态（R3.1）scope 解析原语——当前**不在运行时决策主链**：运行时用扁平 granted +
        父子覆盖判定（见模块函数 check_permission）。此处 `cap & granted` 是扁平集合交集、
        不做父子覆盖（授予父 vehicle.control 时会与只含子 scope 的 cap 交空），待 R3.1 真实
        token_scopes/user_grants 落地、并把 TRUST_LEVEL_CAPS 改为父子感知后再接入决策。
        """
        trust = manifest.trust_level if hasattr(manifest, "trust_level") else "first_party"
        cap = TRUST_LEVEL_CAPS.get(trust, set())

        # 用户授权 + token scope 取并集
        agent_grants = set(auth.user_grants.get(manifest.agent_id, []))
        granted = set(auth.token_scopes) | agent_grants

        # 与 trust 上限取交集
        scopes = cap & granted

        # third_party 强制剔除高危
        if trust == "third_party":
            scopes = {s for s in scopes
                      if not any(s.startswith(p) for p in THIRD_PARTY_DENY_PREFIXES)}

        return scopes

    def check(self, manifest, required: list[str], auth: AuthContext) -> Decision:
        """校验 manifest 所需权限是否被 auth 满足（委托运行时唯一决策 check_permission）。

        auth 的 token_scopes 与该 agent 的 user_grants 取并集作为 granted；
        trust_level/agent_id 取自 manifest。
        """
        granted = set(auth.token_scopes) | set(
            auth.user_grants.get(getattr(manifest, "agent_id", ""), []))
        return check_permission(
            agent_id=getattr(manifest, "agent_id", ""),
            trust_level=getattr(manifest, "trust_level", "first_party"),
            required=required,
            granted=granted,
        )

    def check_action(self, action_type: str, manifest, auth: AuthContext) -> Decision:
        """校验单个动作的权限（执行层用）。"""
        scope_map = {
            "vehicle.control": "vehicle.control",
            "navigate": "navigation.control",
            "play": "media.control",
            "payment": "payment.invoke",
        }
        for prefix, scope in scope_map.items():
            if action_type.startswith(prefix):
                return self.check(manifest, [scope], auth)
        return Decision(allowed=True)
