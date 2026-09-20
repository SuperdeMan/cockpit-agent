"""一轮对话的**终态账本**——「受话 / 理解 / 支持 / 执行失败」分账的唯一词表（评审 W13，2026-09-20）。

## 它解决什么

生产 collector 里一轮的结局只有两种可见形态：`status=ok` 与 `planner_technical_failure`。
「不是对助手说的」「听清了但不知指谁」「能力不支持」「没权限」「问了一句又问一句」「做了一半」
全混在 `ok` 里，于是评审 §9.2 那张指标表（false reject / clarification utility / whole-turn
completion…）一个都算不出来——**分不开的账没法量，量不到的账没法改**。

## 词表纪律

- **封闭词表**：每个 kind 都对应 engine 里一条真实出口；`CATEGORY_OF` 把它们归到评审 §5.1 的
  八类里，报表按类聚合、排查按 kind 定位。新出口必须先在这里登记，`test_outcome.py` 钉着
  「engine 声明的每个 kind 都在词表里」。
- **只记机制，不猜语义**：`no_plan` 是「规划两轮零步」这个事实，不是「用户没说清」；
  `no_action` 是模型明说「受话了但不该做事」，它究竟是 unsupported 还是本来就无需动作，这里不判。
- **执行类终态按结果集算**（`outcome_of_results`）：全 OK ⇒ `completed`；有 OK 也有 FAILED /
  `_refused` ⇒ `partial`；全 FAILED ⇒ `failed`。这是 W12「每个 goal 有终态」里**精确**的那一半——
  按分句猜「哪一段没人管」在生产分布上不成立（设计文档 §5 W12 记账）。

## 为什么住在 runtime/

端侧本地轮（T0 授权闸、VAL 拒绝、本地执行）与云侧规划轮是同一本账的两页；端侧镜像够不着
`orchestrator/cloud`（落点判据 = 镜像依赖闭包，同 `issues.py`）。本批只有云侧写账，端侧那页留作后续。
"""
from __future__ import annotations

# ── 评审 §5.1 的八类 ─────────────────────────────────────────────────────
CAT_NOT_ADDRESSED = "not_addressed"          # 不是在对助手说
CAT_AMBIGUOUS = "ambiguous"                  # 听清了，但不知道要什么 / 指谁
CAT_UNSUPPORTED = "unsupported"              # 理解了，当前能力不支持 / 无动作可做
CAT_PERMISSION = "permission_missing"        # 已接入但当前主体无权限
CAT_POLICY_BLOCKED = "policy_blocked"        # 安全 / 业务前置不满足
CAT_FAILURE = "dependency_or_planner_failure"  # 服务 / 规划失败
CAT_PROGRESS = "progress"                    # partial / pending / uncertain / completed
CAT_SESSION = "session_control"              # 确认 / 取消 / 挂起表 / 会话事实等确定性出口

# ── kind → 类。声明顺序按 engine 出口出现的先后 ────────────────────────────
CATEGORY_OF: dict[str, str] = {
    # 挂起表与确认（W01 / Q1）
    "pending_missing": CAT_SESSION,      # 带寻址键却对不上任何挂起
    "pending_ambiguous": CAT_SESSION,    # ≥2 条待确认、裸「确认」问一次
    "pending_asked": CAT_SESSION,        # 「可以吗 / 确认吗」念出挂着什么
    "no_pending": CAT_SESSION,           # 裸确认词、没有待确认
    "cancelled": CAT_SESSION,            # 取消了一条挂起
    "pending_expired": CAT_SESSION,      # 挂起计划恢复不出来
    "store_fenced": CAT_SESSION,         # 隐私删除写栅栏期间不保存挂起
    # 安全与注入
    "safety_origin_blocked": CAT_POLICY_BLOCKED,
    "injection_rejected": CAT_POLICY_BLOCKED,
    # 系统持有的事实（零 LLM 读出口）
    "candidate_missing": CAT_AMBIGUOUS,  # 引用了候选、一份都没有
    "fact_answered": CAT_SESSION,        # 候选聚合 / 挂起状态 / 数据源 / 执行史
    "constraint_noted": CAT_SESSION,     # 纯偏好陈述已登记（W13 F09-a）
    # 规划轮出口
    "not_addressed": CAT_NOT_ADDRESSED,  # 语音源 + 模型判非受话
    "unresolved_object": CAT_AMBIGUOUS,  # 模型想澄清却没交出卡（W13 F09-b）
    "planner_failure": CAT_FAILURE,      # 技术失败终态（F09）
    "permission_missing": CAT_PERMISSION,
    "clarify": CAT_AMBIGUOUS,            # 出了澄清卡，等选择
    "cancel_unresolved": CAT_AMBIGUOUS,  # 取消话没落到任何东西上
    "no_plan": CAT_UNSUPPORTED,          # 规划两轮零步、无兜底可答
    # 执行
    "pending_confirm": CAT_PROGRESS,
    "pending_slot": CAT_PROGRESS,
    "completed": CAT_PROGRESS,
    "partial": CAT_PROGRESS,
    "failed": CAT_FAILURE,
    "uncertain": CAT_PROGRESS,           # 动作已发、final 没回、按世界状态定
    "stream_lost": CAT_FAILURE,          # 只流了话术、final 丢了
    "escalate_failed": CAT_FAILURE,      # 改派装配 / 执行失败
}

OUTCOME_KINDS = frozenset(CATEGORY_OF)

#: 结果集里「这一步没做成」的两种形态：FAILED 状态，或 Agent 经保留键 `_refused` 声明拒绝
#: （话术型拒绝按契约 §9.5 用 OK 落地，只有这个键分得开它与真完成）。
_FAILED_STATUSES = frozenset({"failed", "rejected"})


def category_of(kind: str) -> str:
    return CATEGORY_OF.get(str(kind or ""), "")


def _status_name(result) -> str:
    status = getattr(result, "status", None)
    return str(getattr(status, "value", status) or "").lower()


def _refused(result) -> bool:
    data = getattr(result, "data", None)
    return isinstance(data, dict) and bool(data.get("_refused"))


def outcome_of_results(results) -> str:
    """执行类 final 的终态：`completed` / `partial` / `failed`。

    只看**这一轮的结果集**：状态是 executor 盖的章，`_refused` 是 Agent 的声明，两者都不是
    模型话术。空结果集记 `failed`（聚合器会说「暂时无法处理」——那不是完成）。
    挂起结果（NEED_CONFIRM / NEED_SLOT）不该出现在这里（挂起 final 各有自己的 kind）；
    真出现了按「没做成」算，宁可少报一次 completed。
    """
    rows = list(results or [])
    if not rows:
        return "failed"
    done = 0
    undone = 0
    for r in rows:
        name = _status_name(r)
        if name == "ok" and not _refused(r):
            done += 1
        elif name in _FAILED_STATUSES or _refused(r) or name in ("need_confirm", "need_slot"):
            undone += 1
        else:
            # skipped / pending / running：既没做成也不算失败，不计入
            continue
    if done and not undone:
        return "completed"
    if done and undone:
        return "partial"
    # 全没做成，或全是 skipped / pending（什么都没发生也不是完成）
    return "failed"
