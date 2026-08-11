"""Planner 重试策略表（B5 §3）。

`planning.py` 的规划主循环同时处理十来条「让模型重新回答」的守卫。它们的形态是好的
——**要求重问而不是篡改计划**，比 route hint 安全——但每加一条都要人脑推演它与既有
状态（`retry_with_tool` / `salvage_kept` / `clarification_tool_retry` /
`plan_only_tool_retry` / `no_action` / `correction`）的交互。B5 取其中真正解痛的一件：
**把重试规则从控制流里拿出来变成数据**，于是每条可单测、可消融、可清点。

## 三段求值（不是排版，是求值方式）

- `guard`：**首个命中即停**。重构前是一条 `if/elif` 链 + 两条 `not semantic_guard_retry`
  + 两条 `parsed is not None` 守卫，逐条核过十条本来就互斥——first-match 是它的忠实
  表达，不是新加的约束。
- `accept`：计划判为可用之后才问的那一件事（salvage 语义），同样首个命中即停。
- `tail`：计划不可用之后的收尾，**逐条求值、不互斥**。

## 校正槽

`override` = 命中即写（guard 段互斥，至多一条写）；`default` = 只在槽空着时写。
校正与 `next_wire` **只在 attempt 0 落**：重构前 `for attempt in range(2)`，
attempt 1 写进去的校正没有任何一处读得到（逐字核过）。把这条统一成一条规则，
比让每条策略自己带 `if attempt == 0` 更不容易写错。

## 与 §3.1 草图的字段差异

逐条理由写在方案文档附录 A.1（`docs/design/2026-08-10-b5-planner-retry-stream-refactor.md`）
——`metric_tag` / `risk_class` / `preserve_previous` / `validation_errors` 四个字段
落地时都是**死字段或第二份声明**，故不落；归因改走 `plan.retry_policies`，
`plan_modes` 口径逐字不变（保护既有 findings 读数的可比性）。

## 消融

`PLANNER_RETRY_DISABLE=<name>[,<name>...]` 按 name 关掉单条策略，用于跑批消融。
**它必须先被证明是活的**（§3.2 第 4 条，「A/B 之前先证明两臂真的不同」的前置版）：
`salvage_wire_accepted` 这一条有已知读数（+34.2pp），关掉它必须与
`PLANNER_TOOLCALL_SALVAGE_RETRY=off` 在「只调一次 LLM」这件事上表现一致——
`test_retry_policy.py` 把这条钉成断言。
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from string import Template

logger = logging.getLogger("planner.retry")


class TriggerKind(Enum):
    """哪一类信号触发重问。**枚举不是回调——可枚举才可审计。**

    谓词按 kind 注册在 `planning.py`（判据要读 catalog / working_set / 领域正则，
    放在本模块会与 planning 循环 import）。
    """

    CLARIFICATION_CONTRACT_VIOLATED = "clarification_contract_violated"
    PLAN_ONLY_CONTRACT_VIOLATED = "plan_only_contract_violated"
    COMPLETE_CONDITIONAL_CLARIFIED = "complete_conditional_clarified"
    FOCUSED_LIST_BATCH_CONFLICT = "focused_list_batch_conflict"
    FOCUS_DEPENDENT_CONFLICT = "focus_dependent_conflict"
    OPEN_CLOSE_POLARITY_INVERTED = "open_close_polarity_inverted"
    CLARIFY_GOAL_WITH_STEPS = "clarify_goal_with_steps"
    MULTI_ACTION_OMITTED = "multi_action_omitted"
    DIRECTIVE_NOT_ADDRESSED = "directive_not_addressed"
    EXPLICIT_INPUT_NOT_ADDRESSED = "explicit_input_not_addressed"
    SALVAGE_WIRE_ACCEPTED = "salvage_wire_accepted"
    NO_ACTION_UNCONFIRMED = "no_action_unconfirmed"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"


# ── 通道（与 plan_mode 的 wire 取值同名，口径不另起）────────────────────────
WIRE_TOOLCALL = "toolcall"
WIRE_SALVAGE = "toolcall_salvage"
WIRE_JSON = "json"
WIRE_TOOLCALL_FALLBACK = "toolcall_fallback"

WIRE_ALL = frozenset({WIRE_TOOLCALL, WIRE_SALVAGE, WIRE_JSON,
                      WIRE_TOOLCALL_FALLBACK})
# 工具通道两态：模型用了工具（toolcall）与掉档后同轮抢救（toolcall_salvage）。
# 「上一轮要过专用 schema」这一族只可能在这两态成立——专用 schema 的前置是
# `retry_with_tool`，而它蕴含 `use_tool`。
WIRE_TOOL = frozenset({WIRE_TOOLCALL, WIRE_SALVAGE})

STAGE_GUARD = "guard"
STAGE_ACCEPT = "accept"
STAGE_TAIL = "tail"
_FIRST_MATCH_STAGES = frozenset({STAGE_GUARD, STAGE_ACCEPT})

CORRECTION_OVERRIDE = "override"
CORRECTION_DEFAULT = "default"

NEXT_WIRE_NONE = ""
NEXT_WIRE_CLARIFICATION = "clarification"
NEXT_WIRE_PLAN_ONLY = "plan_only"


@dataclass(frozen=True)
class RetryPolicy:
    name: str
    trigger: TriggerKind
    stage: str
    wire_modes: frozenset
    attempt_limit: int          # 只在前 N 次尝试上生效（attempt < N），不是「至多 N 次」
    correction_template: str    # 模板 key；"" = 本策略不回灌
    correction_mode: str = CORRECTION_OVERRIDE
    next_wire: str = NEXT_WIRE_NONE


@dataclass
class PlanAttemptState:
    """一次规划尝试的判据输入（§3.1 的 `PlanAttemptState`）。

    `data` 与 `parsed` 都要在：**判据落在原始 dict 上还是校验结果上是有区别的**
    ——模型规划了 3 步但全被能力集校验丢掉时 `data["steps"]` 非空、`parsed` 为 None，
    那是「规划错了」不是「不需要动作」（同 `_looks_like_no_action` 的注释）。
    """

    attempt: int
    wire_mode: str
    data: object                       # 模型这一轮的线格式（dict / None）
    parsed: object                     # Plan | None（守卫命中后由控制器置空）
    text: str = ""
    working_set: object = None
    catalog: object = None
    ctx: object = None
    toolcall: bool = False
    retry_with_tool: bool = False
    fallback_candidate: object = None  # 已抢救出来的回落计划
    clarification_expected: bool = False
    plan_only_expected: bool = False
    goal_requires_clarification: bool = False
    clarification_marker: bool = False
    complete_conditional_goal_marker: bool = False
    correction: str = ""               # 控制器同步进来的校正槽当前值


# ── 校正模板注册表 ─────────────────────────────────────────────────────────
#
# 原文逐字搬自重构前的 planning.py（行为快照，见方案附录 A）。占位符用
# `string.Template` 的 `$name`——模板里有 JSON 花括号，`str.format` 会当成字段炸掉。

_CLARIFY_MARKER_HEAD = (
    "\n\n校验反馈：上一版工具提交已正确判断需要澄清，并按协议保持 "
    "steps=[]。现在不要继续只输出 goal 标记，请补全澄清卡。"
)
_CLARIFY_CONTRADICTION_HEAD = (
    "\n\n校验反馈：上一版 goal 已表明需要澄清，或用户只给了待解析对象，"
    "却仍输出执行 steps，决策与计划矛盾。"
)

CORRECTION_TEMPLATES = {
    "complete_conditional": (
        "\n\n校验反馈：用户原话是完整条件句，已经同时给出条件前件和条件"
        "后件。未来条件尚未知不是歧义，而是 adaptive 计划的触发点；不要向"
        "用户反问选哪一个动作。首轮只规划用于观察或查询条件前件的合法能力，"
        "设置 complexity=adaptive，并在 goal 保留完整条件目标；条件后件留给"
        "观察结果后的 replan，不得提前无条件执行。"
    ),
    "focused_list_batch": (
        "\n\n校验反馈：用户原话要求在当前列表焦点上换一批，结构焦点已明确"
        "候选用途为 list、上一轮意图=$focus_intent"
        "。这不是查看某个条目详情，也不是切换到同命名空间的其他操作；请只"
        "复用上一轮意图对应的 capability_ref，并保留用户给出的筛选条件。"
    ),
    "focus_dependent": (
        "\n\n校验反馈：用户原话是依赖结构焦点的省略表达，上一版却澄清、"
        "拒绝或跨离了焦点。上一轮意图=$focus_intent"
        "。除非原话显式切换主题，本轮必须在同一能力命名空间内选择与当前"
        "动作相符的 capability_ref；不得跨到其他命名空间。"
    ),
    "open_close_polarity": (
        "\n\n校验反馈：上一版选择了与用户明确开/关极性相反的 sibling 能力。"
        "请逐字核对原话中的打开或关闭动作，只从本轮 catalog 选择同对象、"
        "同极性的 capability_ref；其余已正确步骤保持不变。"
    ),
    "clarify_goal_with_steps": (
        "$clarify_head"
        "不要替用户选择或猜测任何动作；请通过 submit_plan 输出 "
        "{\"addressed\":true,\"steps\":[],\"clarify\":{"
        "\"question\":\"针对当前对象的口语化问题\",\"options\":["
        "{\"label\":\"明确动作一\",\"send_text\":\"包含当前对象的完整指令\"},"
        "{\"label\":\"明确动作二\",\"send_text\":\"包含当前对象的另一完整指令\"}]}}。"
    ),
    "multi_action_omitted": (
        "\n\n校验反馈：用户原话包含多个肯定动作，但上一版 simple 计划只输出了"
        "一个 step。请逐项对照本轮 capability description/contract 复核覆盖面；"
        "heavy=true 只表示该能力内部流程复杂，不代表它自动承接原话里的其他"
        "显式动作。如果单个能力的声明确实完整承接全部动作，可以保持一个 step；"
        "否则为遗漏动作补齐 step，并按真实数据依赖设置 depends_on 与 slot_refs。"
        "不要增加原话未声明的动作。"
    ),
    "directive_not_addressed": (
        "\n\n校验反馈：用户原话是直接对助手发出的祈使指令，上一版却标为 "
        "addressed=false。请重新逐句规划，并保持 submit_plan 参数结构不变。"
    ),
    "explicit_input_not_addressed": (
        "\n\n校验反馈：本轮来自显式输入，不是 hands-free 语音旁听。上一版仅返回 "
        "addressed=false，无法完成显式请求；请重新逐句规划，并继续通过 "
        "submit_plan 提交。"
    ),
    # 掉出工具通道后的重试提示（泓舟 2026-08-10 拍板：维持 MiniMax 主模型 + salvage
    # 轮强制重试）。刻意**不重复输出协议全文**——`_TOOLCALL_SECTION` 本来就恒拼在
    # system 里，这里只点明「上一版没用工具」这件事实。
    "toolcall_salvage_retry": (
        "\n\n校验反馈：上一版没有调用 submit_plan 工具，而是直接输出了文本。"
        "本轮必须通过调用 submit_plan 提交同样的判断，不要以文本形式输出 JSON，"
        "也不要输出任何解释。"
    ),
    "no_action_unconfirmed": (
        "\n\n校验反馈：上一版返回空 steps，但用户原话并非整句纯否定。"
        "请逐个核对仍为肯定的诉求；有合法动作就补齐步骤，没有才继续空数组。"
    ),
    "schema_validation_failed": (
        "\n\n校验反馈：上一版 submit_plan 参数没有通过结构或能力白名单校验。"
        "请严格保持顶层 addressed/steps 与 steps 数组元素层级，逐字复制本轮 "
        "capability_ref，并继续通过 submit_plan 提交。"
    ),
}


def render_correction(key: str, state: PlanAttemptState) -> str:
    """按 key 取模板并用**统一的**一套参数渲染。

    参数从 state 算，不建第二张「策略→参数」表：多一张表就多一处会与策略表漂移
    的声明。模板用不到的参数自然忽略。
    """
    focus = getattr(state.working_set, "focus", None)
    params = {
        "focus_intent": str(getattr(focus, "last_intent", "") or ""),
        "clarify_head": (_CLARIFY_MARKER_HEAD if state.clarification_marker
                         else _CLARIFY_CONTRADICTION_HEAD),
    }
    return Template(CORRECTION_TEMPLATES[key]).safe_substitute(params)


# ── 策略表：**声明顺序即求值顺序** ─────────────────────────────────────────

RETRY_POLICIES: tuple = (
    RetryPolicy(
        name="clarification_contract_violated",
        trigger=TriggerKind.CLARIFICATION_CONTRACT_VIOLATED,
        stage=STAGE_GUARD, wire_modes=WIRE_TOOL, attempt_limit=2,
        correction_template=""),
    RetryPolicy(
        name="plan_only_contract_violated",
        trigger=TriggerKind.PLAN_ONLY_CONTRACT_VIOLATED,
        stage=STAGE_GUARD, wire_modes=WIRE_TOOL, attempt_limit=2,
        correction_template=""),
    RetryPolicy(
        name="complete_conditional_clarified",
        trigger=TriggerKind.COMPLETE_CONDITIONAL_CLARIFIED,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=2,
        correction_template="complete_conditional",
        next_wire=NEXT_WIRE_PLAN_ONLY),
    RetryPolicy(
        name="focused_list_batch_conflict",
        trigger=TriggerKind.FOCUSED_LIST_BATCH_CONFLICT,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=1,
        correction_template="focused_list_batch"),
    RetryPolicy(
        name="focus_dependent_conflict",
        trigger=TriggerKind.FOCUS_DEPENDENT_CONFLICT,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=1,
        correction_template="focus_dependent"),
    RetryPolicy(
        name="open_close_polarity_inverted",
        trigger=TriggerKind.OPEN_CLOSE_POLARITY_INVERTED,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=1,
        correction_template="open_close_polarity"),
    RetryPolicy(
        name="clarify_goal_with_steps",
        trigger=TriggerKind.CLARIFY_GOAL_WITH_STEPS,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=2,
        correction_template="clarify_goal_with_steps",
        next_wire=NEXT_WIRE_CLARIFICATION),
    RetryPolicy(
        name="multi_action_omitted",
        trigger=TriggerKind.MULTI_ACTION_OMITTED,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=1,
        correction_template="multi_action_omitted"),
    RetryPolicy(
        name="directive_not_addressed",
        trigger=TriggerKind.DIRECTIVE_NOT_ADDRESSED,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=2,
        correction_template="directive_not_addressed"),
    RetryPolicy(
        name="explicit_input_not_addressed",
        trigger=TriggerKind.EXPLICIT_INPUT_NOT_ADDRESSED,
        stage=STAGE_GUARD, wire_modes=WIRE_ALL, attempt_limit=1,
        correction_template="explicit_input_not_addressed"),
    RetryPolicy(
        name="salvage_wire_accepted",
        trigger=TriggerKind.SALVAGE_WIRE_ACCEPTED,
        stage=STAGE_ACCEPT, wire_modes=frozenset({WIRE_SALVAGE}),
        attempt_limit=1, correction_template="toolcall_salvage_retry",
        correction_mode=CORRECTION_DEFAULT),
    RetryPolicy(
        name="no_action_unconfirmed",
        trigger=TriggerKind.NO_ACTION_UNCONFIRMED,
        stage=STAGE_TAIL, wire_modes=WIRE_ALL, attempt_limit=2,
        correction_template="no_action_unconfirmed",
        correction_mode=CORRECTION_DEFAULT),
    RetryPolicy(
        name="schema_validation_failed",
        trigger=TriggerKind.SCHEMA_VALIDATION_FAILED,
        stage=STAGE_TAIL, wire_modes=WIRE_TOOL, attempt_limit=1,
        correction_template="schema_validation_failed",
        correction_mode=CORRECTION_DEFAULT),
)

POLICY_BY_NAME = {policy.name: policy for policy in RETRY_POLICIES}


def policies_for(stage: str) -> tuple:
    return tuple(policy for policy in RETRY_POLICIES if policy.stage == stage)


def disabled_policies() -> frozenset:
    """`PLANNER_RETRY_DISABLE=<name>[,<name>...]` —— 跑批消融用。

    未知名字**不静默吞掉**：拼错策略名却按「什么都没关」跑，读数会被当成
    「关了也没变化」（同 B3「静默回落就是要消灭的形态」）。
    """
    raw = os.getenv("PLANNER_RETRY_DISABLE", "")
    names = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = names - set(POLICY_BY_NAME)
    if unknown:
        raise ValueError(
            f"PLANNER_RETRY_DISABLE 里有未知策略名: {sorted(unknown)}；"
            f"可用: {sorted(POLICY_BY_NAME)}")
    return frozenset(names)


@dataclass
class RetryController:
    """按表求值：验证 → 匹配 policy → 执行重问 → 记归因。

    `predicates` 由 `planning.py` 注入（TriggerKind → 谓词）。缺任何一条即抛——
    **表和谓词必须同时被想到**，少一条会静默变成「这条守卫从此不生效」。
    """

    predicates: dict
    disabled: frozenset = frozenset()
    correction: str = ""
    next_wire: str = NEXT_WIRE_NONE
    fired: list = field(default_factory=list)
    _counts: Counter = field(default_factory=Counter)

    def __post_init__(self):
        missing = {policy.trigger for policy in RETRY_POLICIES} - set(self.predicates)
        if missing:
            raise ValueError(
                f"重试策略缺少谓词: {sorted(kind.value for kind in missing)}")

    def begin_attempt(self, state: PlanAttemptState) -> None:
        """每次尝试开始时同步校正槽、清掉上一轮的 next_wire。"""
        state.correction = self.correction
        self.next_wire = NEXT_WIRE_NONE

    def run(self, stage: str, state: PlanAttemptState) -> list:
        """求值一段，返回本段命中的策略列表。

        guard/accept 首个命中即停；tail 逐条求值（#12 计数器与 #13 通用校正
        不互斥，重构前也是两条独立的 `if`）。
        """
        hits = []
        for policy in policies_for(stage):
            if not self._eligible(policy, state):
                continue
            if not self.predicates[policy.trigger](state):
                continue
            self._apply(policy, state)
            hits.append(policy)
            if stage in _FIRST_MATCH_STAGES:
                break
        return hits

    def _eligible(self, policy: RetryPolicy, state: PlanAttemptState) -> bool:
        return (policy.name not in self.disabled
                and state.wire_mode in policy.wire_modes
                and state.attempt < policy.attempt_limit)

    def _apply(self, policy: RetryPolicy, state: PlanAttemptState) -> None:
        self._counts[policy.name] += 1
        self.fired.append(policy.name)
        logger.info("Planner retry policy fired: %s (attempt=%d wire=%s)",
                    policy.name, state.attempt, state.wire_mode)
        if policy.stage == STAGE_GUARD:
            state.parsed = None        # 守卫的共同处置：这一版不可用，重新要一份
        if state.attempt != 0:
            # 校正与下一轮通道只有「还有下一轮」时才有意义（`range(2)`）。
            return
        if policy.correction_template and not (
                policy.correction_mode == CORRECTION_DEFAULT and self.correction):
            self.correction = render_correction(policy.correction_template, state)
            state.correction = self.correction
        if policy.next_wire:
            self.next_wire = policy.next_wire
