# 意图理解与落域对抗测试修复验收（2026-08-04）

> 结论：**修复本身有效，但“可生成正式 baseline”的总目标仍未达成。**
> 最后一趟完整三样本 L1 gate 为 **113/117（96.6%）**，4 条均为
> `unstable`，无 `stable_fail`、无 provider 漂移、无检索降级、无基础设施错误。
> 正式 baseline 未写入；资格闸继续因 `unstable_results` / `gate_failures` / 非
> `--layer all` / 工作树不干净 / raw planner 幻觉率非零而拒绝。

## 1. 本轮修了什么

| 问题 | 修复 | 防假绿证据 |
|---|---|---|
| gate 声明 `normal_repeats` 但普通样本仍只跑 1 次 | 执行计划与 baseline 资格闸共用 suite 策略；gate `<3` 直接拒绝 | 一次幸运通过不再满足 `_repeat_policy_complete()` |
| relation 靠“原话相同”猜测槽位应保持 | `invariant` 默认只守路由；仅显式 `slot_policy: subset` 才比槽位 | `cs.news.stale-trip` 固定 provider 3/3，不再把可选默认槽位当历史串味 |
| L3 链接只要 journey id 存在就可借绿灯 | `journey_links.yaml` 升 schema v2，强制 `assertion+rationale`，并核 case/layer/journey | 删除 3 条语义错映射，报告显式带出授权 claim |
| Windows 深层 TEMP 导致 L3 token 原子写路径 264 字符 | L3 改用短系统临时根，保留唯一 invocation id | discovery / gate 的 A1-2 各 1/1，报告身份与 provider 锁定核对通过 |
| 模型返回 `steps=[{...}, "..."]` 会让整趟崩溃 | planner 校验到元素层，任一非 object 则整份计划原子拒绝 | 混合元素最小复现先红后绿；不保留“合法半句” |
| `cp.dep.menu-then-order` 只要两域就可假绿 | dependency gold 补 `carries: [item]` | 立即把“两步都在、但没数据接线”打成 0/3，证明旧绿灯不成立 |
| shop guide/few-shot 仍会随机漏掉依赖和 `slot_refs` | 在已注入 guide 内增加受限 `plan_repairs`：只连接已存在且唯一的 producer/consumer，不新增 intent，不覆盖真值 | 无触发词、已有商品值、guide 被裁剪均不修；实际作用单列 `skill_effects` |
| 否定 policy 增长 48 字挑掉 navigation guide | 压缩 policy，增真实三 guide 候选混合的预算回归 | 两条 navigation knowledge-injection 契约恢复 3/3，不相关 guide 被裁剪 |
| mixed negation 及“车内净化模式”局部知识稀薄 | 增不复制对抗原句的 volume/manual 范例与紧凑 few-shot | manual 对照两进程各 repeat 3，合计 12/12；当轮检回 `manual#6@vec:0.87` |
| `plan_repairs.source_path=data.items...` 污染动态架构词表 | Skill 词表提取仅跳过 `source_path` 值，其他文本和 producer/consumer intent 仍全量扫描 | 新反向构造先稳定红；修后四个同根架构守卫 + 新用例 5/5，守卫全文件 89/89 |

`plan_repairs` 是确定性的结构归一，不是第四条硬路由通道。它的边界是：

- 资产必须是本轮真正注入的 `full/canary` skill；`shadow/off/!clipped` 不生效；
- 只能给计划已经选出的两步补 `slot_refs + depends_on`，多生产者/多消费者不猜；
- 不覆盖用户或模型已给真值，不绕过 manifest / validator / Runtime Policy / VAL；
- 报告与 `cloud.planning` span 保留 `skill_effects`，不把修过的计划冒充模型原生正确。

## 2. 真栈证据

### 2.1 过程读数

| 批次 | 读数 | 它证明什么 |
|---|---:|---|
| 修尺子后首趟完整 L1 | 115/117 | 暴露 menu 依赖漏接与 mixed negation；无 infra/retrieval 降级 |
| 首轮知识/产品修复后完整 L1 | 109/117 | 抓到否定 policy 把 navigation guide 挤出预算的回归，证明不能只看定向 case |
| 预算与 manual 知识修复后最终完整 L1 | **113/117** | provider `minimax:MiniMax-M3` 锁定；706 次检索、0 降级；0 trace/infra 错误；117/117 重复覆盖 |

最终报告：`docs/reviews/eval/_ci-run-intent-gate-l1-finalized.{json,md}`。关键指标：

- `exact_plan_set_rate` 113/117（96.6%）；`required_group_recall` 97/99（98.0%）；
- `dependency_pass_rate` 3/3；`relation_pass_rate` 29/29；
- raw planner `capability_hallucination_rate` 6/117（5.1%），校验后逃逸 0/117；
- `repeat_coverage` 117/117，`instability_rate` 4/117（3.4%）；
- `retrieval_degraded=0`、`provider_drift=false`、`infrastructure_errors=[]`。

### 2.2 最终 4 条红灯

| case | 本趟失败形态 | 定性 |
|---|---|---|
| `cs.cancel-it.reminder` | `reminder.cancel` 变成 `reminder.complete` | `unstable`，其他完整/定向批出现过正确面 |
| `nq.dinner-music.drop-music` | 多出 `media.pause` | `unstable`；不再是漏 `nearby.search` |
| `nq.hvac.keep-volume` | 正确出 `volume.dec`，但多出 `media.pause` | `unstable`；原来的 `hvac.off` 误动作已消失 |
| `os.battery.car` | `charging.find` 变成 `charging.status` | `unstable`，其他批次有正确面 |

这 4 条不是基础设施伪红，也不是已证明的稳定产品缺陷：它们在同一 provider/资产下
有跨进程翻面证据。同一进程内的 3 次采样具有相关性，因此“三样本”消除了单次幸运
通过，却没有消除跨进程方差。继续为追一趟 117/117 而追加 route hint 会把方差固化成新规则，
不是本轮应采取的修复。

后端全量首次收尾时曾出现 4 条架构守卫红灯，四条都指向
`orchestrator/cloud/verify.py` 的通用参数 `data`。根因不在 verifier：动态词表会从
Skill 的所有点号字符串提取 intent，新的 `source_path: data.items.0.name` 被误当成
`data.*` 意图，再反向把参数名判为领域泄漏。修复只排除这个结构字段的值，
`producer_intent/consumer_intent` 仍被词表收集；不改 `verify.py` 参数名规避尺子。
修复后重跑项目规定的后端全量命令：**3996 passed / 11 skipped / 0 failed**，
耗时 26m37s（收集 4007 项）。

### 2.3 离线与代码回归

| 验证 | 结果 |
|---|---:|
| 对抗契约/裁判/CLI + Skill/Exemplar + planner 受影响子集 | **267 passed** |
| 动态架构守卫全文件 | **89 passed** |
| `eval_skills.py` | **PASS**（16/16 golden，反例误召回 1/8 在既定上限内） |
| `eval_exemplars.py` | **PASS**（242 条 / 17 域；域错配 4/161，2.5%） |
| discovery L0 | **70/70**（555 条 / 516 唯一输入） |
| gate L0 `--strict` | **19/19，exit 0**（133 stable / 123 唯一输入） |
| gate L3 `--list` | **A1-2**（非空且只含授权链接） |
| 后端全量 | **3996 passed / 11 skipped / 0 failed** |

## 3. 验收判定

| 目标 | 判定 |
|---|---|
| 尺子不再因单次幸运通过、槽位误报、无关 L3 借绿灯而假绿 | **达成** |
| 真栈跑批不再被畸形 step 或 Windows 深路径打断 | **达成** |
| Skill 扩展字段不误污染动态架构守卫词表 | **达成** |
| 已确认的 menu 依赖接线、mixed negation、manual 知识与预算回归有可观测修复 | **达成** |
| stable 规模和正式 L3 选集前置 | **达成**：133 条 / 123 唯一输入，A1-2 已有授权证据 |
| 完整 gate 全绿 | **未达成**：113/117，4 `unstable` |
| 正式 baseline 可写 | **未达成**：资格闸正当拒绝，本轮未生成 baseline |

## 4. 后续只剩两个有效方向

1. 将跨进程采样正式纳入 gate 的置信契约，不再把一进程内 3 次当成 3 份独立证据；
2. 在干净快照上跑完 L1+L2+L3，同时满足 raw planner 幻觉率门限，只有资格闸真正
   `eligible=True` 时才允许写第一份 baseline。
