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


class PermissionEngine:
    """统一权限引擎。编排层（Planner）和执行层（VAL）共用。"""

    def effective_scopes(self, manifest, auth: AuthContext) -> set[str]:
        """计算某 Agent 在当前 auth 下的有效权限。"""
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
        """校验 manifest 所需权限是否被 auth 满足。"""
        if not required:
            return Decision(allowed=True)

        eff = self.effective_scopes(manifest, auth)
        missing = [r for r in required if not is_scope_covered(r, eff)]

        if missing:
            return Decision(
                allowed=False,
                missing=missing,
                reason=self._explain(manifest, missing),
            )
        return Decision(allowed=True)

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

    @staticmethod
    def _explain(manifest, missing: list[str]) -> str:
        agent = manifest.agent_id if hasattr(manifest, "agent_id") else "该功能"
        hints = {
            "location": "定位",
            "vehicle.control": "车辆控制",
            "payment": "支付",
            "network": "网络",
            "profile": "个人资料",
        }
        for m in missing:
            for k, v in hints.items():
                if m.startswith(k):
                    return f"需要{v}权限，请在设置中授权"
        return f"缺少权限：{', '.join(missing)}"
