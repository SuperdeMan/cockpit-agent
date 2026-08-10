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
