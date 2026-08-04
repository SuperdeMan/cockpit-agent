# 意图与落域对抗语料

本目录只保存人工可审计的语义契约。运行时报告写到显式 `--out-json/--out-md`，不得在本目录留下临时结果。

设计规格：`docs/design/2026-08-02-intent-routing-adversarial-testing.md`（唯一真相源）。

## 文件

- `suites.yaml`：状态选择与重复策略，不保存 provider、model、secret。`gate.normal_repeats`
  不得小于 3；runner 与 baseline 资格闸必须消费同一份策略。
- `coverage_exemptions.yaml`：逐 intent、逐要求的显式豁免。
- `journey_links.yaml`：schema v2，链接现有 journey id，不复制旅程 gold。每条映射必须有
  `journey_id` / `assertion` / `rationale`：旅程绿灯只授权显式 claim，不能借给语义相似但不同的 case。
- `cases/*.yaml`：按攻击机制分文件；每个文件固定 `schema_version: 1` 与 `cases:`。

## 命名与字段

- case id：`attack.family.variant`，一经进入 reviewed 不改名；替代时 retired 原项并新增 id。
- `family_id`：同源原句、paraphrase、最小变体共享；用于 seen/unseen 防泄漏。
- `tags.attacks/domains/layers`：attacks 至少含一个 `A1`–`A9` 编号；domains 声明涉及域；layers 声明 L0–L3 执行层。细分机制另放 `tags.mechanisms`。
- plan：必要组之间 AND、组内 `any_of` OR；默认禁止未声明额外 intent。
  `dependencies[].carries` 表示 consumer 必须真正通过 `slot_refs` 消费 producer 的数据；
  只有 `depends_on` 不能证明数据已接线。
- adaptive：初始 `plan` 与 `replans[].after.result + plan` 分开写，result 形状对齐生产 observation。
- relation：变体必须同时有自己的 absolute gold，不能只写相对关系。`invariant` 默认只守
  路由不变；只有人审 gold 能证明“variant 不得引入 base 未观测槽位”时，才显式写
  `relation.expectation.slot_policy: subset`。两边原话相同不是槽位来源证据。
- `expected.engine`：**只有 L2 观测得到**（`required_agent_calls` / `forbidden_agent_calls` /
  `pending_confirm_after` / `max_agent_calls_per_intent`），写在别的层上是永远不会被裁的断言，
  契约直接报错。危险动作只写 `safety.no_side_effect_before_confirm` **证明不了确认闸**——
  副作用面只看动作有没有落地，替身恰好不产生动作时它恒真；真正的证据是那个 Agent 有没有
  被够着（agent call）与挂起有没有落库（pending state）。
- `expected.safety.side_effect_counts`：「**恰好** N 次副作用」的等式，
  形如 `{parking.pay: 1}`。**同样只有 L2 观测得到**（完整副作用面只在完整决策链上存在），
  写在别的层上契约直接报错。三条口径要记住：
  1. **声明即封闭**——未列出的键必须为 0 次，与 `plan.allow_extra_intents=false` 同一心智
     模型。只锁点名那一格的话，「顺手把后备箱也开了」照样绿。
  2. **它不能替代 `max_agent_calls_per_intent`**，两者量的不是一回事：后者量**调用**，
     前者量**副作用**；「调用了但替身没产生动作」时两者分叉，而那正是确认闸相关断言最容易
     假绿的地方。表达「说两遍确认一次只准付一次」时两条一起写。
  3. **全零非法**——那就是 `no_side_effect_before_confirm` 那句话，用等式再写一遍不是
     更强，只是多一条同义断言。契约会拦。
  计数键：engine 侧用 `intent`，端侧 VAL 命令用 `<object>.<operate>`，其余端侧动作用
  action `type`（`judge.side_effect_key`）。**键里刻意不含 payload**——否则同一动作换个
  参数就成了另一个键，等式当场失效，而失效的样子和「一次都没发生」一模一样。

## 轮次与证据单元

`turns` 里的**每一轮都会被执行**，同一条 case 的多轮跑在同一个会话上（L0 共用 Edge servicer、
L2 共用 Engine session 与 history）。证据单元命名：

- 单轮 case → `case_id@layer`；
- 多轮 case → `case_id#<轮号>@layer`（轮号从 1 起）；
- L3 → `case_id@l3`（journey 覆盖整条场景，不按轮拆）。

**只有第二轮存在时才可证的断言，就必须写成两轮**：`max_agent_calls_per_intent: 1` 在单轮里
恒真，「补槽答案不是确认」在单轮里根本没有补槽那一轮。

## 状态

`candidate → reviewed → stable → retired`。只有 `reviewed_by: human` 的案例可以进入 `reviewed`；只有固定 provider 重复稳定的案例可以进入 `stable`。`retired` 保留原文、原因和替代保护，不删除历史。

`stable` 只是语料生命周期状态，不等于永不失败。gate 必须如实报 `pass / unstable /
stable_fail`；不得因为一条 stable 当前变红就改 gold、降级状态或移出选集。

## 数据隔离

同源文本共享 `family_id`。进入 Skill/Exemplar/Hint 修复资产的 family 只能计入 `seen_regression`；未进入修复资产的 family 才能计入 `unseen_transfer`。

`family_id` 只防得住「作者记得它们同源」的那一半——换一个 family id，同一句原话就能同时进
remediation 与 holdout。所以另有两条按**输入事实**判的硬闸（`validate_cohort_isolation`）：

1. 同一句原话（NFKC 归一 + 去标点后）不得同时出现在两个 cohort；
2. `unseen_transfer` 的原话不得字面出现在 `skills/` 下被注入的知识里（Exemplar text、Guide
   golden/keywords、边界台账 texts）。

第 2 条**只证伪不证实**：知识里没有这句话不代表它没被拿去改过规则（Route Hint 是正则，对不上
字面），所以 `seen_regression` 一侧不设对称断言——保守地标成 seen 永远是允许的。

## 规模单位

**规模按唯一输入算，不按条数**（`utterance + context` 指纹）。同一句「附近的充电站」在 stable
里出现过 4 次，条数说 113、唯一输入只有 104。`suites.yaml` 的 `min_cases/max_cases` 作用在唯一
输入上；报告同时打印 `cases=` 与 `distinct_inputs=`，`meta.duplicate_input_groups` 列出重复组。
同输入不同机制可以各留一条（断言不同就有价值），但只计一个规模单位。

## 脱敏

真实 badcase 入库前删除姓名、电话、精确住址、车牌、账号与 token。无法确认脱敏完成时保持 `candidate` 且不提交原文。

## 清理

运行报告只写 `docs/reviews/eval/_ci-run-*` 或显式输出路径。corpus 内不放 trace、临时 prompt、模型原始回复和失败截图。retired 用例不删除；写 `retired_reason` 与替代 case/保护链接。
