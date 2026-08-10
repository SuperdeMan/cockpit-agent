# B6 可执行性判定（ActionabilityClassifier）与前瞻契约（条件启动）

> **状态**：已批准、**条件启动**（触发条件见 §1；未触发前是设计蓝图不是待办）
> **交付对象**：满足触发条件时的实施者
> **关联**：`AGENTS.md` §4.2 裸对象澄清族条目、
> `test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml`（`nq.landmark.bare` 头注）、
> `docs/design/2026-08-02-intent-routing-adversarial-findings.md` §18/§19/§25、
> `docs/design/2026-07-28-intent-accuracy-data-flywheel.md`（P3b）
> **来源**：外部评审采纳批次 B6（裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)）

---

## 1. 触发条件（先读这个）

| 子项 | 启动条件 |
|---|---|
| §2 ActionabilityClassifier shadow | 二者之一：① 有真实流量（P3b 的 privacy-safe 统计管道就位，分母存在）；② 裸对象澄清族 badcase 再次成为主要矛盾（新增同族稳定红、或产品面投诉） |
| §3 端侧 NLU 放量顺序 | 无独立启动——它是 P3b 放量的**准入条款**，P3b 晋级 §4.1 时随行生效 |
| §4 Capability Contract 远期字段 | 逐字段独立触发（见 §4 表） |

## 2. ActionabilityClassifier：裸对象澄清族的第四条路

### 2.1 为什么是第四条路（前三条的尸检结论，不要重走）

该族现状是**已知无解**（AGENTS.md §4.2 专条）：「华润大厦」「上海」这类裸对象该澄清而不澄清/
误澄清，三条修法路径全部走完——写 guide 实测有害（4/10→1/10→退回 7/10，p≈0.02，findings
§18）；换预选池被裁定回退（§19.5）；clarify 型范例机制建成但治不了本族（裸专名之间
IDF-Dice 全 0.000，**检索是内容通道，而裸对象澄清是形态判据**；澄清范例天然与其补全版
近重复互抢，仓库刻意零生产 clarify 范例，§25）。§4.2 的结论原话：「下一次要动它得换判定
形态（形态/句法特征），不是再加一层检索式知识」。本设计就是那个「判定形态」的具体化——
外部评审独立得出同一方向（评审方向三），可视为交叉验证。

### 2.2 设计骨架

独立的确定性/轻模型前置判定（位置：planner 之前或并行 shadow，不进主链）：

```text
输入特征（全部可离线计算，不依赖检索）：
  有明确动作动词？ / 只有实体（裸专名/裸对象）？ / 存在对话焦点（focus 态可解释它）？
  是省略续问形态？ / 唯一默认动作存在？（如「上海」无焦点时无唯一动作）
  必填槽完整度 / 显式输入 or hands-free 通道 / 高风险对象在场？
输出：
  EXECUTE | CLARIFY | REJECT + confidence
```

**shadow 记录四元组**（评审建议照单采纳）：`actionability_decision / confidence /
planner_decision / human_gold`（gold 由既有对抗语料金标 + 后续真实流量标注供给）。
落 obs 事件（沿用诊断行管道），不影响主链任何决策。

### 2.3 纪律前置（本项目已付过学费的，实施者必读）

- **「诊断出一个洞 ≠ 洞是病因」**（§4.3）：shadow 读数显示分类器与 planner 分歧，不等于
  切换就能改善——canary 前必须做与 planner 决策的**对照实验**（分歧样本人工裁定，胜率
  显著才谈接管）；
- **准入门槛沿用 P3b 口径**：误执行类指标（疑问句/否定句误执行、forbidden route、高风险
  自动执行、极性错误）**全部为 0** 才允许从 shadow 进 canary；wrong-object <0.3% 同款；
- 形态特征不许退化成新一批正则 hint——特征必须是**句法/形态量**（词性、槽完整度、焦点态），
  不是字面模式表；出现「给某个专名写特判」的冲动时，那是范例/等价类的活，回它们的通道。

## 3. 端侧语义 NLU 放量顺序（并入 P3b 准入条款）

评审建议采纳为条款，P3b 晋级 §4.1 时写进其 DoD：

```text
shadow（现状，agree 68.8% 口径）
  → 只读查询类 intent 先行
  → 低风险、可回滚的设置类（effect=write 且 risk=low，字段由 B4 提供）
  → 普通车控
  → 高风险对象（trunk/door_lock/fuel_tank_cover/charging_port/行驶受限/支付）
    **永久保留确定性确认 + VAL 硬门（B1），不进模型自动执行放量序列**
```

每级晋升都要有上一级的分母与错对象率读数；「压错对象率的手段是 R4.1b 执行侧对象化，
不是调阈值」维持 §4.2 原判。

## 4. Capability Contract 远期字段（记档防遗忘，逐字段触发）

B4 已落 `effect`/`risk`/`confirmation`（有消费方）。其余字段**谁有消费方谁触发**：

| 字段 | 触发条件 |
|---|---|
| `input_schema` / `output_schema` | Planner 槽位类型错误成为稳定 badcase 族，或 typed Executor 立项 |
| `replayable` / `idempotency` | B5 §4.2 流式统一启动时随 `command_id` 一起 |
| `compensation` | 撤销/补偿类产品需求出现（如订单取消生命周期，M-C 后置项激活） |
| `timeout_budget` | 现 `latency_budget_ms` 已有——仅当预算需要分档治理时升格 |
| `version` / `deprecation` | 第三方 Agent 生态启动（能力退役需要迁移窗口）时 |

## 5. 验收判据（shadow 子项）

1. shadow 决策四元组进 obs 且 dashboard 可检索（沿用 badcase 排查贯通链路）；
2. 与既有对抗语料回放：`nq.landmark.bare` 合并族在分类器下的 CLARIFY 命中率显著高于
   planner 现状（这是它存在的第一性理由，不成立就撤）；
3. 主链零行为变化（shadow 铁律，同 P3a 影子「它的全部价值就是不生效」注释口径）；
4. canary 决策单独立卡，需泓舟拍板——本文件只授权到 shadow 为止。
