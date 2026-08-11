# B5 Planner 重试策略表驱动 + D0/T2 流式判定统一（条件启动）

> **状态**：已批准、**条件启动**（触发条件见 §1；未触发前本文件是设计蓝图不是待办）
> **交付对象**：满足触发条件时的实施者
> **关联**：`orchestrator/cloud/planning.py`、`orchestrator/cloud/engine.py`（D0）、
> `orchestrator/cloud/loop.py`（T2）
> **来源**：外部评审采纳批次 B5（裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)），
> 评审原建议「拆五层 + 九段 Kernel」已降档（理由见 §2 头注）。

---

## 1. 触发条件（先读这个）

| 子项 | 启动条件 | 在那之前 |
|---|---|---|
| §3 RetryPolicy 表驱动 | **下一次要往 planning.py 加新的重试/守卫规则时**，先做本重构再加规则（评审不做清单第 3 条「不要继续加零散 `elif` 守卫」，已采纳为纪律） | planning.py 冻结新增 `elif` 守卫；确需修 bug 不受限 |
| §4 流式判定统一 | **下一次要新增流式执行路径**（第三条流式通道、或 D0/T2 任一要改 fallback 语义）时 | B1 已用最小 diff 修正 T2 判定 bug，正确性无欠账 |

为什么不立即做：两处都没有正确性缺陷（B1 后），重构收益是「未来改动不踩雷」；而 planning.py
是 1184 条 orchestrator 测试与全部 live 读数的被测核心，无消费场景的重构=纯风险。同时
**已有读数纪律**约束（AGENTS.md §4.3）：重构合入后旧 SHA 的 live 读数作废，需重跑完整父
bundle 才能再引用主模型总分——这笔成本只该在「反正要动它」时一起付。

## 2. 现状（为什么这债是真的）

`planning.py` 单个主流程同时处理：toolcall / toolcall salvage / tool retry / JSON fallback /
clarify marker / focus continuity / open-close 极性 / multi-action omission / adaptive
consistency / no-action / directive addressed / explicit-input addressed retry / plan repair /
route hint / fallback / capability validation。互相影响的状态至少有：`retry_with_tool`、
`salvage_kept`、`clarification_tool_retry`、`semantic_guard_retry`、`correction`、
`no_action`、`last_mode`、`plan_mode`。

这些守卫的共同形态是「要求模型重新回答」而非篡改计划——这比 route hint 安全（评审与本项目
判断一致），但每加一条都要人脑推演与既有状态的交互；`plan_modes` 后缀语义已经需要专门的
读数纪律来防误读（§4.3「`_no_action` 说的是判断、`_kept` 说的是通道」）。这是典型的
「还能维护，但每次维护成本在涨」的债。

> 评审建议的五层拆分（CatalogAuthority→WireInvoker→PlanParser→PlanValidator→RetryController
> →FallbackPolicy）与九段 Execution Kernel 不采纳：PoC 单人研发下，五层抽象的日常导航成本
> 会立刻兑现，而其收益（多人并行改不同层）不会。取其中真正解痛的一件：**把重试规则从
> 控制流里拿出来变成数据**。

## 3. RetryPolicy 表驱动

### 3.1 目标形态

```python
@dataclass(frozen=True)
class RetryPolicy:
    name: str                    # 与现 plan_mode 值对齐（salvage_retry / clarification_tool_retry / ...）
    trigger: TriggerKind         # 哪类验证失败/信号触发（枚举，不是回调——可枚举才可审计）
    wire_modes: frozenset[str]   # 适用通道（toolcall / json / salvage）
    attempt_limit: int           # 每请求最多触发次数
    correction_template: str     # 回灌给模型的反馈模板 key
    risk_class: str              # normal / semantic / safety（safety 类不允许静默放弃）
    metric_tag: str              # 观测归因（plan_modes 后缀，沿用现有口径）
    preserve_previous: bool      # 是否保留上一次合法计划作回落（salvage 语义）

@dataclass
class PlanAttemptState:
    attempt: int
    wire_mode: str
    raw: str
    parsed: Plan | None
    validation_errors: list[ValidationError]
    fallback_candidate: Plan | None
```

主循环收敛为：验证 → 匹配 policy（按声明顺序，首个命中）→ 执行重问 → 记 `metric_tag`。
每条 policy 可单测（构造 trigger 断言重问行为）、可消融（跑批时按 name 关掉单条）、可观测
（`plan_modes` 沿用不换口径——**保护既有读数可比性**）。

### 3.2 迁移纪律（重构不许改行为）

1. 先从现 planning.py **提取重试规则清单**（含触发条件、次数上限、模板原文），逐条登记——
   这份清单本身入库（`docs/design/` 附录或模块 docstring），是行为快照；
2. 表化逐条进行，每条迁移配「行为等价测试」：同输入下重问次数、回灌文案、`plan_modes`
   序列逐字一致；
3. 全部迁移完跑根全量 + L0 门禁；**live 层面**：迁移是行为等价的，原则上不需重跑 live，
   但下一次引用主模型总分前须按 §4.3 纪律重跑父 bundle（顺带完成）；
4. A/B 教训前置（§4.3「A/B 之前先证明两臂真的不同」）：消融开关做好后，先用一条已知有效
   的 policy（如 salvage retry，已有 +34.2pp 读数）验证「关掉它读数确实变化」——证明消融
   通道是活的，再交付。

## 4. D0/T2 流式判定统一

### 4.1 目标形态

抽一个纯函数 + 状态枚举（放 `orchestrator/cloud/stream_state.py`）：

```python
class StreamAttempt(Enum):
    NO_OUTPUT = auto()        # 未流出任何内容
    SPEECH_EMITTED = auto()   # 已流出话术
    ACTION_EMITTED = auto()   # 已流出动作（含话术+动作）
    FINAL_RECEIVED = auto()   # 拿到 final

def allow_unary_fallback(state: StreamAttempt) -> bool:
    return state is StreamAttempt.NO_OUTPUT

def outcome_uncertain(state: StreamAttempt, got_final: bool) -> bool:
    return state is StreamAttempt.ACTION_EMITTED and not got_final
```

D0（`engine.py:411` 起）与 T2（`loop.py:184` 起）各自的局部布尔（`streamed`/`did_speak`/
B1 新增的 `did_action`）替换为对该枚举的状态推进；fallback 与「结果不确定」判定改调纯函数。
判定表从此一处维护、两处消费，第三条流式路径按同款接入。

### 4.2 顺带项（仅在本子项启动时做）

- `ACTION_EMITTED` 无 final 的「结果不确定」处置从 B1 的话术版升级为接 Outcome Verifier
  readback（查世界状态对账后再定话术）；
- 幂等键（`command_id`）设计随本子项一起评估——单独做会产生第三套局部实现（B1 §6 的边界
  在此收口）。

## 5. 验收判据

- RetryPolicy：规则清单文档≡代码表（检查脚本比对 name 集合）；单条消融可跑通且读数有响应；
  全量 pytest 与 L0 门禁绿；`plan_modes` 口径零变化（现有 findings 读数继续可引用）。
- 流式统一：D0/T2 现有全部流断测试（含 B1 三场景）在共享判定下逐字复绿；判定纯函数 100%
  分支覆盖（它小，做得到）。

---

## 附录 A. 重试规则清单（**重构前**的 planning.py 行为快照）

> §3.2 第 1 步的产物。这份清单是**从重构前的 `planning.py`（`abc3f49`）读出来的**，
> 不是从新代码倒推的——它的用途正是当行为基准。`orchestrator/cloud/retry_policy.py`
> 的 `RETRY_POLICIES` 必须与本表的 name 集合逐条对齐，
> 由 `orchestrator/cloud/tests/test_retry_policy.py::test_inventory_matches_the_code_table`
> 解析本表比对（表变了而代码没变，或反过来，都会红）。
>
> `attempt_limit` 的读法是**「只在前 N 次尝试上生效」**（`attempt < N`），
> 不是「最多触发 N 次」——原代码里那些条件写的是 `attempt == 0`，
> 「首轮才判」与「至多判一次」在「首轮不成立、次轮成立」时不是一回事。

| # | name | 段 | 触发条件（重构前源码位置） | attempt_limit | 适用通道 | 校正模板 | 下一轮通道 |
|---|---|---|---|---|---|---|---|
| 1 | `clarification_contract_violated` | guard | 上一轮要过澄清专用 schema，本轮没按 `addressed=true/steps=[]/clarify` 交（`planning.py:1215`） | 2 | tool | — | — |
| 2 | `plan_only_contract_violated` | guard | 上一轮要过计划修正专用 schema，本轮顶层字段越界（`:1228`） | 2 | tool | — | — |
| 3 | `complete_conditional_clarified` | guard | 完整条件句仍被判成澄清（`:1241`） | 2 | 全部 | `complete_conditional` | `plan_only` |
| 4 | `focused_list_batch_conflict` | guard | 「换一批」离开了结构焦点的 list 能力（`:1258`） | 1 | 全部 | `focused_list_batch` | — |
| 5 | `focus_dependent_conflict` | guard | 省略续问跨离了结构焦点命名空间（`:1269`） | 1 | 全部 | `focus_dependent` | — |
| 6 | `open_close_polarity_inverted` | guard | 选了与原话开/关极性相反的 sibling 能力（`:1280`） | 1 | 全部 | `open_close_polarity` | — |
| 7 | `clarify_goal_with_steps` | guard | goal 说要澄清却仍出执行 steps；或裸对象被安上动作；或工具标记只给 goal 不给澄清卡（`:1302`） | 2 | 全部 | `clarify_goal_with_steps` | `clarification` |
| 8 | `multi_action_omitted` | guard | 原话多个肯定动作，simple 计划只出一步（`:1330`） | 1 | 全部 | `multi_action_omitted` | — |
| 9 | `directive_not_addressed` | guard | 祈使指令被判 `addressed=false`（`:1347`） | 2 | 全部 | `directive_not_addressed` | — |
| 10 | `explicit_input_not_addressed` | guard | 显式输入（非 hands-free 语音）被判 `addressed=false`（`:1359`） | 1 | 全部 | `explicit_input_not_addressed` | — |
| 11 | `salvage_wire_accepted` | accept | 计划可用，但它是掉档轮从自由文本里抢救出来的（`:1374`） | 1 | salvage | `toolcall_salvage_retry`（default） | — |
| 12 | `no_action_unconfirmed` | tail | 模型说「受话了、不该做任何动作」，而输入本身没给出确定性语法证据（`:1389`） | 2 | 全部 | `no_action_unconfirmed`（default） | — |
| 13 | `schema_validation_failed` | tail | 工具通道的参数没过结构/能力白名单校验，且没有更具体的诊断（`:1402`） | 1 | tool | `schema_validation_failed`（default） | — |

**三段的语义差别**（不是排版，是求值方式）：

- `guard`：**首个命中即停**。重构前它是一条 `if/elif` 链加两条 `not semantic_guard_retry`
  与两条 `parsed is not None` 守卫——逐条核过，十条**本来就互斥**，first-match 是它的
  忠实表达而不是新约束（核法：每条命中都置 `parsed = None`，而后续每条的条件都要求
  `parsed` 非空，或显式检查 `not semantic_guard_retry`）。
- `accept`：计划已判为可用之后才问的那一件事，同样首个命中即停。
- `tail`：计划不可用之后的收尾，**逐条求值不互斥**——#12 是计数器（它的命中会与 guard
  段并存：guard 判掉计划之后 `_looks_like_no_action(data)` 仍可能为真），#13 只有在
  「还没有更具体的校正」时才补一条通用反馈。

**校正槽的写入语义**：`override`=命中即写（guard 段互斥，故至多一条写）；
`default`=只在槽还空着时写。校正与 `next_wire` **只在 attempt 0 落**——重构前
`for attempt in range(2)`，attempt 1 写进去的校正没有任何一处读得到（逐字核过），
统一成一条规则比让每条策略自己带 `if attempt == 0` 更不容易写错。

### A.1 与 §3.1 草图的字段差异（逐条给理由）

| 草图字段 | 落地 | 理由 |
|---|---|---|
| `trigger: TriggerKind` | ✅ 保留 | 枚举可审计；谓词按枚举注册在 `planning.py`（放 `retry_policy.py` 会与 planning 循环 import） |
| `wire_modes` | ✅ 保留 | 有真实内容：#1/#2/#13 只可能在工具通道成立、#11 只在 salvage 通道成立，控制器据此过滤 |
| `attempt_limit` | ✅ 保留，语义校准 | 见上：读作「只在前 N 次尝试上生效」 |
| `correction_template` | ✅ 保留 | 模板搬进 `retry_policy.py` 的注册表，按 key 取；`$focus_intent`/`$clarify_head` 两个占位符从 state 统一渲染（不建第二张参数表） |
| `metric_tag` | ❌ 不落 | 13 条里 11 条的 tag 会与 `name` 逐字相同（B4 教训：不加第二份表达同一件事的声明）；剩下 2 条（#11 的 `toolcall_salvage_kept`、#12 的 `{last_mode}_no_action`）本来就是**按 mode 算出来的**，常量字段装不下。归因改走新增的 `plan.retry_policies`（命中策略名列表），`plan_modes` 口径逐字不变 |
| `risk_class` | ❌ 不落 | 草图设它是为「safety 类不允许静默放弃」。B1 已经把每一处安全判定搬出 planning.py（确认权威在 VAL），13 条重试**没有一条**能静默放弃一个安全决策——判掉计划的唯一去向是 `_fallback`，不是执行原计划。字段落下来会是死字段；真出现安全类重试再加，那时它有消费方 |
| `preserve_previous` | ❌ 不落 | 与 `stage="accept"` 说的是同一件事（accept 段的全部语义就是「这份能用，先存起来再要一次」）。将来若出现不 preserve 的 accept 策略再拆 |
| `PlanAttemptState.validation_errors` | ❌ 不落 | `_parse_and_validate_data` 校验失败只返回 `None`，没有结构化错误可填；空列表字段属于先落即死 |
