# 意图对抗门禁跨进程置信契约（2026-08-05）

> 状态：**已获泓舟批准，进入实施**。本设计承接
> `docs/reviews/2026-08-04-review-intent-adversarial-finalization.md` 的两个最终方向：
> 先把独立进程写进 gate 置信契约，再在同一干净快照完成 L1+L2+L3 并仅由
> `eligible=True` 写首份正式 baseline。

## 1. 当前事实与问题本质

现有 `gate.normal_repeats: 3` 只让同一个 Python 解释器、同一个 `PlanBuilder`、检索缓存和
`ProviderLock` 连续采样三次。`_repeat_policy_complete()` 只数 `repetitions` 长度，
`baseline_eligibility()` 没有独立进程身份，因此同进程 3 次会被误记为完整置信证据。

2026-08-04/05 在干净 SHA `f0b08f8` 上取得的定向事实：

- 两个独立 L1 进程、每进程每 case 3 次：第一进程 5/5，第二进程 3/5；
- `cs.cancel-it.reminder`、`nq.hvac.keep-volume` 跨进程合计 6/6；
- `nq.dinner-music.drop-music` 在第二进程 1/3 多出 `media.pause`；
- `os.battery.car` 在第二进程 1/3 把 `charging.find` 变成 `charging.status`；
- 六条能力缺席 A8 用例表面 6/6 通过，但 raw Planner 幻觉率 6/6：全部靠 validator
  丢弃不存在的能力后落 `chitchat`。

因此收尾不是“继续重跑直到出现一趟全绿”，而是同时修两类可信度缺口：

1. 证据层必须证明 L1/L2 来自至少两个独立进程；
2. raw 幻觉必须按所有样本聚合，不能只看被选作展示证据的那一次。

## 2. 设计决策

### D1：正式入口仍是一个命令

日常与正式入口继续使用：

```powershell
python test/eval_intent_adversarial.py --suite gate --layer all --live \
  --provider minimax --model MiniMax-M3
```

外层进程成为 parent/controller，自动启动不可递归的 worker。不会新增要求操作者手工挑选
两份 JSON 的 merger 命令，避免把不同 SHA、provider、资产或选集拼在一起。

### D2：只给采样型层增加独立进程

`suites.yaml` 为 gate 声明：

```yaml
independent_processes: 2
independent_layers: [l1, l2]
```

- L0 是确定性规则/契约证据，跑一次；
- L1/L2 各要求两个独立进程，每进程继续遵守 suite 的 3 次采样；
- L3 自身已是新鲜、唯一 run-id 的跨进程/协议证据，保留一次，不无意义翻倍。

`--layer all` 的 parent 顺序启动三个 worker：

1. `primary`：执行 `all`，提供 L0、第一份 L1、第一份 L2、唯一 L3；
2. `corroboration-l1`：只执行 L1；
3. `corroboration-l2`：只执行 L2。

`--layer l1/l2` 各启动两个同层 worker；L0/L3/discovery 保持单进程。

### D3：worker 必须串行

`ProviderLock` 修改 llm-gateway 的全局 active provider。多个 worker 并发 pin/restore 会互相
覆盖并制造 provider drift，因此 parent 只允许串行 `subprocess.run()`。worker 临时报告写到
系统临时目录，不进入仓库，也不作为可手工复用的正式证据；聚合后的最终报告内嵌必要身份、
摘要和逐样本证据。

### D4：每次调用都有机器身份

每个 worker 报告必须包含：

```json
{
  "process_sample": {
    "bundle_id": "uuid",
    "process_run_id": "uuid",
    "role": "primary|corroboration-l1|corroboration-l2",
    "pid": 1234,
    "layer": "all|l1|l2"
  }
}
```

每个 repetition 同时记录 `process_run_id` 与进程内 `sample_index`。parent 不凭 PID 猜独立性，
而以自己实际启动的 worker、唯一 run-id 和报告回声共同证明；PID 仅用于审计。

### D5：身份不一致是基础设施失败，不是产品红灯

parent 对所有重叠证据 fail-closed 校验：

- bundle/role/layer 与实际启动计划一致；
- `process_run_id` 唯一且非空；
- code SHA、worktree clean 状态、provider/model/lock、资产指纹一致；
- suite、retrieval state、温度、选集与 corpus 状态一致；
- 重叠 unit 的 case id、layer、gold digest、准入能力清单一致；
- worker 报告存在、可解析，退出码只能是产品通过 0 或产品失败 1；
- 任一 infra、trace、provider drift、retrieval 降级都传到父报告并使运行退出 2。

不允许用“选一个看起来正常的 shard”绕过缺失或不一致证据。

### D6：跨进程分类使用错误签名的进程覆盖数

对 L1/L2 每个 evidence unit：

1. 任一样本危险路由 → `critical_fail`；
2. 所有要求进程中的所有样本都通过 → `pass`；
3. 同一个错误语义签名至少在两个不同进程各出现一次 → `stable_fail`；
4. 其余存在通过/失败翻面或错误签名不一致 → `unstable`。

这会把“同一进程内两次同错、另一进程全过”从旧的 `stable_fail` 正确降为 `unstable`，也会把
“两个进程各出现一次同错”从旧的两个孤立 `unstable` 合并为可复现的 `stable_fail`。
relation 仍在每个 worker 内用本 worker 的 base support 裁判，parent 只聚合完成后的结果，
绝不跨 worker 拼 relation 对照样本。

### D7：raw/validator/fallback 按全部样本聚合

repetition 除 `passed/signature/dangerous` 外还记录：

- `raw_intents`、`raw_observed`、`validation_observed`；
- `actual_intents`；
- `plan_from_fallback`。

一个 unit 只要任一样本出现 catalog 外 raw intent，就计入 raw capability hallucination；
任一样本走未声明 fallback，就进入 baseline 拒绝项。报告另写 `raw_observation_complete`，缺失
任何应观测的 L1/L2 样本同样 fail-closed，不能用缩小分母把指标变成 0。

### D8：baseline 只认 parent 聚合报告

正式资格新增硬条件：

- `meta.process_bundle_role == "parent"`；
- `meta.process_policy_complete is True`；
- `meta.raw_observation_complete is True`；
- L1/L2 每个声明 unit 均达到 suite 的进程数与每进程样本数；
- worker 身份/内容无冲突。

worker 模式禁止 `--write-baseline`。正式 baseline 仍沿用现有完整选集、正式比较源、无 repeat
覆盖、provider lock、clean SHA、L3 新鲜、零 unstable/stable_fail、零 raw 幻觉、零逃逸、
零 fallback/infra/drift 等所有硬闸；本设计不放宽任何门限。

### D9：晋级 provenance 与 gate 使用同一进程概念

2026-08-04 起的新 stable 晋级除 `stabilized_samples >= 6` 外还必须声明：

```yaml
stabilized_processes: 2
stabilized_samples_per_process: 3
stabilized_process_runs: [run-a, run-b]
```

run id 必须非空且唯一，样本总数必须不小于进程数乘每进程样本数。现有唯一 2026-08-04
晋级项 `cp.dep.charge-then-navigate` 回填已验收的 A/B 证据名称；更老存量仍按历史契约保留，
但新的 gate parent 会在当前快照上重新覆盖其运行置信。

### D10：产品修复顺序固定，route hint 不作为收尾工具

尺子落地后按以下顺序：

1. 在 Planner 通用协议中明确“动态 catalog 是 agent/intent 唯一白名单；请求能力缺席时
   `addressed=true, steps=[]`，不得编造、替代或输出缺席能力”，validator 保持第二道硬防线；
2. 用双进程 A8 定向批要求 raw 幻觉归零且不走未声明 fallback；
3. 对 dinner/battery 做 `on-failure` 消融，再只改通用 policy/exemplar/manifest 描述；
4. cancel/hvac 已有 6/6 正确面，不因旧单趟红灯追加资产；
5. 不为追 117/117 恢复或新增 route hint。

## 3. 报告结构

父报告新增：

```json
{
  "meta": {
    "process_bundle_role": "parent",
    "process_policy_complete": true,
    "raw_observation_complete": true,
    "process_sampling": {
      "bundle_id": "uuid",
      "required": {"l1": 2, "l2": 2},
      "observed": {"l1": 2, "l2": 2},
      "workers": [
        {"role": "primary", "process_run_id": "uuid", "pid": 1234,
         "layer": "all", "report_sha256": "2f1520db54330f3cb42d3d3c76674c1789c3982603f70970a3514c4289d86381", "exit_code": 1}
      ]
    }
  }
}
```

`results[*].repetitions` 保持向后兼容的列表形态，只增加进程与 raw 字段。Markdown 在摘要中明确
“进程数 × 每进程样本数”、worker 身份和 `process_policy_complete`，避免读者把 6 个扁平样本
重新误读为同一进程。

## 4. 验收标准

### 尺子

- 单进程 3 次不再满足 gate L1/L2 进程策略；
- 重复 process id、缺 shard、SHA/provider/assets/gold/选集不一致均退出 2；
- 两进程同错 → `stable_fail`，单进程翻面 → `unstable`；
- `--layer all` 只运行一次 L3，且 L1/L2 各有两个进程；
- worker 无法直接写 baseline；parent 缺任一进程证据时 `eligible=False`；
- 任一样本 raw 幻觉都能进入指标和 baseline 硬闸。

### 产品与真栈

- A8 双进程 raw capability hallucination 为 0，post-validation escape 仍为 0；
- dinner/battery 双进程不再出现已知偏离，cancel/hvac 无回归；
- L0 discovery 70/70、gate strict 19/19；
- 同一干净 SHA 上完成 gate L1+L2+L3，无 provider/infra/trace/retrieval/fallback 红灯；
- 只有最终父报告明确 `eligible=True` 才生成并提交
  `docs/reviews/eval/baseline_intent_adversarial.{json,md}`。

若仍不 eligible，正式 baseline 必须保持不存在；报告未关闭的具体 evidence unit 与首偏离，
不以重复跑到幸运全绿替代修复。

## 5. 明确不做

- 不修改 `.env`、CI/CD、数据库 schema；
- 不修改 HMI、Dashboard 或通用 `scripts/run_e2e.py`；
- 不并发切换 provider；
- 不把 L3 重跑两次冒充 L1/L2 采样独立性；
- 不放宽 raw 幻觉、fallback、L3 或 clean-worktree 资格门限；
- 不新增 route hint 追单趟全绿；
- 不自动晋级 corpus case，生命周期变化仍需人工批准。
