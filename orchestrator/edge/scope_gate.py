"""端侧 T0 执行出口的授权闸：本地快路径也必须按会话 scope 判「这个账号能不能控车」。

## 为什么需要它（AR05 F07，2026-09-09 离线反例）

网关按 token 注入 `meta["granted_scopes"]`，云侧 `context.build_context` 一直照它算
`PlanContext.granted_permissions`，`dispatch` 再按 `security.permission.check_permission`
硬拒。**端侧从来没读过这个键**：token 只授 `location.read` 时，「打开车窗」走快路径 B
直接进 VAL——车窗开了，还回了一条 `vehicle.control` 动作。三条快路径（多意图 A、混合
A2、单意图 B）与云端降级兜底四个出口都是这样。

于是「当前账号不能控车」这句话在端侧根本不成立，而 AR05 的能力摘要正要向用户宣称它成立。
**摘要与执行必须同源**：这里复用 `check_permission`（运行时唯一权限决策）与
`security.session_scopes`（granted_scopes 解析 + fail-open 兜底），不新建第二份判据。

## 需要哪个 scope 由声明面回答

`capabilities.build_edge_manifests()` 就是端侧向 Registry 声明的那份
`edge_intents × requires_permissions`（edge-vehicle→`vehicle.control`、
edge-media→`media.control`）。这里按 intent 名查那份声明；结构化命令只有对象名时，
按 `edge_call.action_type_for`（对象→控制域的既有唯一入口）回落。
两条路都不新增对象词表——**新增车控能力时不用回来改这个文件**。
"""
from __future__ import annotations

import logging

from edge_call import action_type_for
from security.permission import check_permission

logger = logging.getLogger("edge.scope_gate")

# 端侧执行器在权限模型里的身份：系统级、edge_fast（与注册给 Registry 的 manifest 一致）。
_EDGE_TRUST_LEVEL = "system"
_EDGE_KIND = "edge_fast"

# 被拒 scope 的用户可读名。纯显示用，不参与判定；缺词条回落 scope 原名。
_SCOPE_CN = {
    "vehicle.control": "车辆控制",
    "media.control": "媒体控制",
}

_INTENT_SCOPES: dict[str, tuple[str, ...]] | None = None


def _intent_scopes() -> dict[str, tuple[str, ...]]:
    """intent 名 → 该能力声明的 requires_permissions（取自端侧 manifest，惰性构建）。

    构建失败（缺 gen/ 或 registry 依赖）不抛：回落到按对象的 `action_type_for`，
    闸仍然生效——声明面读不到时宁可用更粗的判据，也不能整条链失去权限校验。
    """
    global _INTENT_SCOPES
    if _INTENT_SCOPES is None:
        table: dict[str, tuple[str, ...]] = {}
        try:
            from capabilities import build_edge_manifests
            for manifest in build_edge_manifests():
                required = tuple(manifest.requires_permissions)
                for intent in manifest.edge_intents:
                    table[intent] = required
        except Exception as e:                      # pragma: no cover - 依赖缺失兜底
            logger.warning("edge manifest scopes unavailable, falling back to object map: %s", e)
        _INTENT_SCOPES = table
    return _INTENT_SCOPES


def required_scopes(*, intent_name: str = "", structured: dict | None = None) -> list[str]:
    """本条本地指令需要的 scope。

    优先按 intent 名查端侧 manifest 声明；查不到时按结构化命令的对象回落
    `action_type_for`（媒体对象→`media.control`，其余→`vehicle.control`）。
    两者都给不出时按 `vehicle.control` 兜底——未知的车端写操作按最强要求判，
    不因为「没认出来」而放行。
    """
    declared = _intent_scopes().get(intent_name or "")
    if declared:
        return list(declared)
    obj = ((structured or {}).get("data") or {}).get("object", "") if structured else ""
    if obj:
        return [action_type_for(obj)]
    return ["vehicle.control"]


def denial_speech(missing: list[str]) -> str:
    """被拒时对用户说什么。

    只说**业务授权**不足，绝不引导到 Android 麦克风/定位这类系统权限页
    （AR05 V07：业务 scope 不足与设备权限拒绝是两件事，恢复出口也不同）。
    """
    names = [_SCOPE_CN.get(s, s) for s in missing] or ["相应"]
    return f"当前账号没有{('、'.join(dict.fromkeys(names)))}权限，这个操作没有执行。"


def check_local_execution(*, granted, intent_name: str = "",
                          structured: dict | None = None):
    """本地执行前的授权判定。返回 `security.permission.Decision`。

    `granted` 由 `security.session_scopes.resolve_granted_scopes` 给出：token 授权、
    PoC 默认或 fail-closed 空集——本函数不重新解释兜底策略，只做判定。
    """
    required = required_scopes(intent_name=intent_name, structured=structured)
    return check_permission(
        agent_id="edge-fast", trust_level=_EDGE_TRUST_LEVEL,
        required=required, granted=granted, kind=_EDGE_KIND)
