"""AR05 结构化契约的**唯一**装配点：确认策略、补槽请求、结构化问题。

字段表见 `docs/design/2026-09-09-ar05-structured-contracts-implementation-plan.md` §11。
这里只做「把服务端已经持有的事实摆成契约形状」，不新增判据：

- 确认要求的权威是 capability 声明（`Step.require_confirm`）与真实挂起状态，
  不是 LLM 的 goal/reason，也不是用户 meta；
- 截止时刻取 `SessionState.expires_at`（SessionStore 落盘时算的绝对时刻），
  客户端只读不续期——续期发生在这里之外就等于挂起窗口被无声延长；
- 摘要取**已验证的**挂起步骤对象/槽值；取不到才回退用户原话，并在
  `summary_source` 里说清楚这是回退，不把模型话术冒充执行事实。

刻意不在这里判「这个对象危不危险」：那份知识住在端侧 `commands.yaml`
（`capability_meta.risk_of`），云侧再写一份就是第二份危险声明（B1 的教训）。
云侧能诚实回答的是「这一步的 capability 声明了 require_confirm 没有」。
"""
from __future__ import annotations

import time

from runtime.issues import (  # noqa: F401 —— 受控枚举经本模块对云侧统一出口
    ISSUE_AUTH_REJECTED, ISSUE_PERMISSION_SCOPE_MISSING,
    ISSUE_PLANNER_TECHNICAL_FAILURE, ISSUE_SAFETY_VAL_REJECTED,
    ISSUE_SERVICE_DEGRADED, ISSUE_TRANSPORT_ERROR,
    RECOVERY_DISMISS, RECOVERY_OPEN_CAPABILITY_SETTINGS,
    RECOVERY_OPEN_VOICE_SETTINGS, RECOVERY_RECONFIGURE_CONNECTION,
    RECOVERY_REPLAY_AUDIO, RECOVERY_RETRY_REQUEST,
    SCOPE_CAPABILITY, SCOPE_OPERATION, SCOPE_REQUEST, SCOPE_SESSION,
    SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARNING,
    build_issue,
)

from .slot_shape import shape_of

# 受控枚举。客户端见到不认识的值一律「显示 message、不猜语义」，不得默认放行。
RISK_LOW, RISK_MEDIUM, RISK_HIGH = "low", "medium", "high"
CHANNEL_TOUCH, CHANNEL_TEXT, CHANNEL_VOICE = "touch", "text", "voice"
# 今天真实存在的三个回复入口。更严的渠道规则必须先有生产权威与执行校验，
# 不能先在 UI 上杜撰（AR05 §4.1）。
DEFAULT_CHANNELS = (CHANNEL_TOUCH, CHANNEL_TEXT, CHANNEL_VOICE)

REASON_REQUIRE_CONFIRM = "require_confirm"      # capability 声明
REASON_AGENT_REQUESTED = "agent_requested"      # Agent 本轮自己返回 NEED_CONFIRM
SUMMARY_FROM_CAPABILITY = "capability"
SUMMARY_FROM_UTTERANCE = "user_utterance"

STATE_ACTIVE, STATE_HELD = "active", "held"

# Issue 的受控枚举与装配住在 `runtime/issues.py`——端侧 T0 也要产同一种东西，
# 云侧镜像与端侧镜像各写一份必然漂移（§11.3）。这里只 re-export，不复制。


def now_ms() -> int:
    return int(time.time() * 1000)


def _expires_ms(state) -> int:
    """挂起记录的绝对截止时刻（epoch ms）。取不到给 0＝未知，客户端回落既有 TTL。"""
    value = float(getattr(state, "expires_at", 0) or 0)
    return int(value * 1000) if value > 0 else 0


def _slot_values(step) -> dict:
    return {k: v for k, v in (getattr(step, "slots", None) or {}).items()
            if isinstance(v, (str, int, float)) and str(v).strip()}


def action_summary(step) -> str:
    """「这次要确认的到底是什么」——由**已验证的**步骤 intent + 槽值合成。

    刻意不读 `plan.goal`：那是模型写的一句话，不是执行事实（AR05 §4.1）。
    """
    if step is None:
        return ""
    intent = str(getattr(step, "intent", "") or "").strip()
    if not intent:
        return ""
    values = _slot_values(step)
    if not values:
        return intent
    detail = "，".join(f"{k}={v}" for k, v in sorted(values.items()))
    return f"{intent}（{detail}）"


def object_summary(step) -> str:
    """动作对象：intent 的域段（`hvac.set` → `hvac`）。零领域词表。"""
    intent = str(getattr(step, "intent", "") or "").strip()
    return intent.split(".")[0] if intent else ""


def build_confirm_policy(*, operation_id: str, step, state,
                         user_text: str = "") -> dict:
    """NEED_CONFIRM 轮的确认策略。返回 dict（cloud 事件面统一用 dict，server 再转 proto）。"""
    declared = bool(getattr(step, "require_confirm", False))
    summary = action_summary(step)
    source = SUMMARY_FROM_CAPABILITY
    if not summary:
        # 没有结构化摘要时**明确回退用户原话**，并说清出处——不把模型 goal 当执行事实。
        summary = (user_text or "").strip()
        source = SUMMARY_FROM_UTTERANCE
    return {
        "operation_id": operation_id,
        # 声明了 require_confirm 的是 CLAUDE.md §5 的危险动作档；Agent 本轮自行要求确认
        # 的记 medium——它确实需要用户点头，但没有受控声明背书，不冒充最高档。
        "risk": RISK_HIGH if declared else RISK_MEDIUM,
        "allowed_channels": list(DEFAULT_CHANNELS),
        "action_summary": summary,
        "object_summary": object_summary(step),
        "reason_code": REASON_REQUIRE_CONFIRM if declared else REASON_AGENT_REQUESTED,
        "target_intent": str(getattr(step, "intent", "") or ""),
        "expires_at_ms": _expires_ms(state),
        "server_now_ms": now_ms(),
        "summary_source": source,
    }


def slot_suggestions(step_result) -> list[str]:
    """本轮**用户真的看见了**的候选项名字，作为补槽建议值。

    判据复用 `context._is_choice_card`（「这张卡序号是它的合法答案」）与
    `context._candidate_items`（「名字是唯一必需字段」）——同一个问题不写第二份。
    不是选择卡就返回空：没渲染成列表的东西用户一眼没见过，做成可点选项就是假选项。
    """
    from .context import _candidate_items, _is_choice_card

    card = getattr(step_result, "ui_card", None)
    if not _is_choice_card(card):
        return []
    items = card.get("items") if isinstance(card, dict) else None
    if not isinstance(items, list):
        return []
    return [item["name"] for item in _candidate_items(items) if item.get("name")]


def build_slot_request(*, operation_id: str, step, step_result, state,
                       suggestions=None, display_names=None,
                       slot_state: str = STATE_ACTIVE) -> dict:
    """NEED_SLOT 轮的补槽请求。只描述**当前这一个槽**，剩余的进 remaining_slots。

    `suggestions` 只能来自本次真实 Agent 结果/候选集；没有就留空——空建议下客户端
    给文字/语音输入，不制造可点击的假选项（AR05 §4.2）。
    """
    missing = [s for s in (getattr(step_result, "missing_slots", None) or []) if s]
    slot = missing[0] if missing else ""
    shapes = dict(getattr(state, "slot_shapes", None) or {})
    if suggestions is None:
        suggestions = slot_suggestions(step_result)
    return {
        "operation_id": operation_id,
        "slot": slot,
        "display_name": (display_names or {}).get(slot, ""),
        # 形状取 `slot_shape.shape_of`：声明优先、缺省按槽名兜底（order_id 那条
        # 写路径身份闸不能因为漏声明而变松）。
        "shape": shape_of(slot, shapes),
        "suggestions": [str(s) for s in (suggestions or []) if str(s).strip()],
        "state": slot_state,
        "remaining_slots": missing,
        "prompt": str(getattr(step_result, "follow_up", "") or ""),
        "expires_at_ms": _expires_ms(state),
        "server_now_ms": now_ms(),
    }
