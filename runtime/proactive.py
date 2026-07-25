"""主动消息发布客户端（M3 P0）——**生产方唯一出口**。

为什么不让生产方直接 `nc.publish("agent.proactive", ...)`：那样每个生产方各自节流、
跨生产方零协调，三个 Agent 同时想说话就响三次。统一主动引擎（`proactive/` 服务）把
「该不该现在说、说几条」收到一处裁决。

**fail-open 是硬要求**：走 NATS request/ack，治理器不在（没起/被关/卡住）就直发老主题
`agent.proactive`——行为逐字回落到治理器上线前。停掉治理器容器 = 一键回退。
NATS 的 no-responders 让「服务没起」是毫秒级失败，不是 500ms 超时干等。

信封契约见 `docs/conventions.md` §9.8；治理键全部可选，缺省 = 最保守但不静默。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("runtime.proactive")

GOVERNOR_SUBJECT = "agent.proactive.request"    # 生产方 → 治理器
LEGACY_SUBJECT = "agent.proactive"              # 治理器 → 网关（也是 fail-open 直发口）

# 优先级四档（生产方声明，治理器不猜；语义见 conventions §9.8）
P_CRITICAL = "critical"            # 安全播报：全豁免，立即发
P_USER_CONTRACT = "user_contract"  # 用户显式约定：免打扰/负荷/频控豁免，仍参与合并
P_ADVISORY = "advisory"            # 建议类：全套治理
P_AMBIENT = "ambient"              # 环境类：全套治理
PRIORITIES = (P_CRITICAL, P_USER_CONTRACT, P_ADVISORY, P_AMBIENT)

GOVERNED, DIRECT, SKIPPED = "governed", "direct", "skipped"


def _enc(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


async def publish_proactive(nc: Any, payload: dict, *, timeout_s: float = 0.5) -> str:
    """发一条主动消息。返回 'governed' | 'direct' | 'skipped'（nc 为空）。

    ack = 投递所有权移交：治理器收到即 ack，合并/延后在 ack 之后异步做，
    生产方不被合并窗口阻塞。
    """
    if nc is None:
        logger.info("proactive 未推送（无 NATS 连接）：%s",
                    str(payload.get("speech", ""))[:40])
        return SKIPPED
    data = _enc(payload)
    try:
        await nc.request(GOVERNOR_SUBJECT, data, timeout=timeout_s)
        return GOVERNED
    except Exception as e:                       # NoResponders / Timeout / 其他
        # 治理器缺席不是故障——是回落。用 info 级别，避免正常降级刷 warning。
        logger.info("主动治理器缺席（%s），直发 %s", type(e).__name__, LEGACY_SUBJECT)
        try:
            await nc.publish(LEGACY_SUBJECT, data)
            return DIRECT
        except Exception as e2:
            logger.warning("主动消息发布失败：%s", e2)
            return SKIPPED
