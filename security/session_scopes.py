"""会话授权 scope 的**唯一**解析与兜底判据：端侧 T0 与云侧 Planner 共用。

为什么必须只有一份（AR05 F07 的实测代价）：
`granted_scopes` 由网关按 token 注入（`gateway/edge/auth.go::stampScopes`，客户端伪造值
先剥后写），云侧 `context.build_context` 一直按它算 `PlanContext.granted_permissions`，
`dispatch` 再按 `check_permission` 硬拒。**端侧 T0 快路径从来没读过这个键**——2026-09-09
离线反例：token 只授 `location.read`，一句「打开车窗」走快路径 B 直接进 VAL，车窗开了、
还回了一条 `vehicle.control` 动作。也就是说「这个账号不能控车」这句话在端侧不成立，
而 AR05 的能力摘要正要向用户宣称它成立。

本模块只做两件事，判据本体仍在 `security.permission.check_permission`：
1. `meta["granted_scopes"]` → scope 列表（逗号分隔，空段丢弃）；
2. 没有 scope 时按 `PERMISSIONS_FAIL_OPEN` 决定 PoC 默认全开还是 fail-closed。

返回值带 `source`（token / poc_default / fail_closed）：AR05 的 `session_info`
要如实告诉用户「你现在这些权限是 token 给的，还是 PoC 默认放行的」，两者不是一回事。
"""
from __future__ import annotations

import os

# 授权来源。写进 session_info.authorization_source，也用于端侧审计归因。
SOURCE_TOKEN = "token"
SOURCE_POC_DEFAULT = "poc_default"
SOURCE_FAIL_CLOSED = "fail_closed"

# PoC 默认权限：未注入 granted_scopes 时使用（fail-open for PoC）。
# 量产必须从会话 token/设备身份解析 scope，不得使用此默认值。
#
# ⚠ 这是全仓**唯一**一份 PoC 默认集（原先在 orchestrator/cloud/context.py）。新增 Agent 的
# `requires_permissions` 若不在这里，PoC 下该能力**永远不可达**：规划前就被 catalog 过滤掉，
# 症状是「每一句都兜底 chitchat」，而不是报权限错（reminder、商户 MCP 都踩过）。
POC_DEFAULT_SCOPES: list[str] = [
    "vehicle.control", "media.control", "navigation",
    "food.ordering",
    "location.read", "navigation.control",
    "network.external", "payment.invoke",
    "profile.read", "profile.write",
    # 真实商户 MCP（麦当劳/瑞幸，§9.9/§9.17）：桥对 workflow 与查单工具校验这两个
    # scope。PoC 里没有任何"商户授权"发放入口，漏在这里 = 能力永远不可达——
    # 2026-08-12 实测每一句真实下单都回「当前账号缺少商户授权」，而 e2e 自己往 meta
    # 塞 granted_scopes 所以一直是绿的。写操作的安全边界不在这里：require_confirm
    # 中央闸 + 只创建未支付订单 + 支付独立走 payment-gateway，三道都不受本行影响。
    "merchant.read", "merchant.write",
    # M4 P4 视觉：**单帧**（用户显式问「那是什么」时抓一张），不是 camera.read 连续流——
    # 后者在 conventions §3 维持 ❌ 禁。采集门控在端侧（默认不采），这里只是让能力可路由。
    "camera.frame",
]


def parse_granted_scopes(raw: str | None) -> list[str]:
    """`meta["granted_scopes"]`（逗号分隔）→ scope 列表。空/None → 空列表。"""
    if not raw:
        return []
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def fail_open_enabled() -> bool:
    """`PERMISSIONS_FAIL_OPEN`：默认 true = PoC 全开；量产翻 false = fail-closed。

    每次调用现读环境变量——部署档在进程生命周期内不变，但测试要能逐例翻转，
    缓存成模块常量会让「fail-closed 那一档」根本测不到。
    """
    return os.getenv("PERMISSIONS_FAIL_OPEN", "true").lower() != "false"


def resolve_granted_scopes(meta, *, vehicle_id: str = "", trace_id: str = "",
                           user_id: str = "", audit=None) -> tuple[list[str], str]:
    """从请求 meta 解析本轮**有效授权**，返回 `(scopes, source)`。

    - meta 带 granted_scopes → 原样使用，source=token（网关是该键的唯一权威）；
    - 不带且 fail-open → PoC 默认集，source=poc_default，并留审计（`audit` 可为
      `security.audit.AuditLogger`，缺省不留——端侧不强制依赖审计管道）；
    - 不带且 fail-closed → 空集，source=fail_closed（只有零权限能力可达）。
    """
    granted = parse_granted_scopes((dict(meta or {})).get("granted_scopes", ""))
    if granted:
        return granted, SOURCE_TOKEN
    if fail_open_enabled():
        granted = list(POC_DEFAULT_SCOPES)
        if audit is not None:
            try:
                audit.fail_open_scopes(vehicle_id=vehicle_id, user_id=user_id,
                                       trace_id=trace_id, scopes=granted)
            except Exception:      # 审计永远不许挡主链
                pass
        return granted, SOURCE_POC_DEFAULT
    return [], SOURCE_FAIL_CLOSED
