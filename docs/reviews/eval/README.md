# 意图路由评测基线（T3.4）

本目录存放端侧 `fast_intent` 与云侧 `route_hints` 两套确定性路由规则的评测基线，
解决 `docs/reviews/2026-07-02-repo-audit-and-roadmap.md` §4 T3.4（G3：意图路由质量无评测基线）。
方案见 `docs/design/2026-07-03-r3.4-intent-eval-baseline.md`。

## 文件

| 文件 | 生成方 | 性质 |
|---|---|---|
| `baseline_fast_intent.{json,md}` | `test/eval_fast_intent.py --write-baseline` | 回归闸（端侧规则） |
| `baseline_route_hints.{json,md}` | `test/eval_route_hints.py --write-baseline` | 回归闸（云侧确定性路由） |
| `baseline_mode_routing.{json,md}` | `test/eval_mode_routing.py --write-baseline` | 回归闸（四模式端到端口径） |
| `baseline_registry_resolve.{json,md}` | `test/eval_registry_resolve.py --write-baseline` | 回归闸（registry top-1） |
| `baseline_rejection.{json,md}` / `baseline_clarify.{json,md}` | `test/eval_rejection.py --write-baseline` | 回归闸（受话判定/澄清） |
| `baseline_skills.{json,md}` | `test/eval_skills.py --live --ab --write-baseline` | 知识层有效性 Δ + 逐 skill 消融 |
| `baseline_exemplars.{json,md}` | `test/eval_exemplars.py --live --ab --write-baseline` | 范例层有效性 Δ（**只在注入子集算**，见下） |
| `baseline_routing.json` + `routing_bench.md` | `test/routing_bench.py --live --write-baseline` | **分布尺 N1**（不是回归闸） |
| `hint_retirement.<provider>.{json,md}` | `test/hint_retirement.py --live --provider …` | 规则退役判定（**按 provider 分文件**） |
| `journeys_report.{json,md}` | `scripts/run_e2e.py --milestone M-A --lane milestone --full --canonical ...` | 真栈 milestone canonical |
| `shadow_nlu_report.{json,md}` + `shadow_nlu_results.jsonl` | `scripts/evolve.py`（M1b Shadow NLU） | 离线对照：规则臂 75.9% vs LLM 91.2% |
| `edge_coverage_ceiling.{json,md}` + `_samples.jsonl` | `test/eval_edge_coverage_ceiling.py` | **可行性判据**（M5 P3a「先测天花板再训模型」） |
| `edge_nlu_train.{json,md}` + `edge_nlu_preds_<lane>.jsonl` | `scripts/train_edge_nlu.py` | 端侧 NLU 两车道结果 + θ 扫描（**两个口径都要印**） |
| `edge_capability_desc_ab.md` | 人工双臂差分（M5 P3 收尾） | **负结果**：判别化描述进 planner catalog 跨两档 Δ=0；收益在 registry 语义兜底 |
| `_ci-run-*`（gitignore，不入库） | CI 每次跑产生的临时报告 | 仅供当次 PR 查看，不覆盖基线 |

## 回归闸 vs 分布尺——别把两者混着读

上表大部分是**回归闸**：语料是策展的、规模有限，作用是「别倒退」，天天 exit=0 是正常的。
`routing_bench` 是**分布尺**：回答「系统这个月变聪明了吗」。**四个离线 eval 全绿与真机
日报同日出血完全可以并存**——这正是 M5 建 RoutingBench 的原因。

读 N1 必须配着三条限制（报告每次都印）：
1. **被排除的语料条数是隐藏分母**——rejection/clarify/s2s_escalation/registry_resolve/
   paraphrase/edge_regressions 都**没有落域金标**，不是落域尺子；另有逐条排除的
   `live:false` 端侧域用例与带 `initial_intents` 的护栏用例。
2. **域分布严重偏斜**——语料围绕「四模式路由」长出来，前三域占七成，navigation/hvac 个位数。
   **N1 涨不等于车控与导航变好。**
3. **canonical 高分主要说明语料已被用来修过系统**（那是 hint 钉出来的）。`paraphrase` 才是主指标。

`--domain-base`（飞书 8590 分层抽样）衡量的是**开放分布可服务率**，**不可与 Shadow NLU 的
91.2% 直比**：那是封闭集分类不受能力面约束，这是受能力面约束的真规划，且语料来自另一个
产品（「第一页」「火车票查询」这类句子不可能落对）。它量化的是**能力面缺口**。

## 意图与落域对抗套件的基线（`baseline_intent_adversarial.{json,md}`）

生成方 `test/eval_intent_adversarial.py --suite gate --layer all --live … --write-baseline`。
它与上表所有基线的区别是**有一道资格硬闸**：不合格的运行**一个字节都不碰**正式基线，
诊断改写到带时间戳的 `_ci-run-intent-adversarial-rejected-*`，并以退出码 2 打印全部拒绝原因。

资格要求（`baseline_eligibility()`，全部满足才写）：

| 闸 | 拒绝原因 |
|---|---|
| suite=gate、layer=all、retrieval-state=warm | `suite_not_gate` / `layer_not_all` / `cold_start_retrieval` |
| 选中案例状态全为 stable | `non_stable_cases_selected` |
| provider 显式 pin 且零漂移 | `provider_not_locked` / `provider_drift` |
| code SHA 非空、工作树干净 | `unknown_code_sha` / `dirty_worktree` |
| 资产指纹完整（无 `missing_assets`）| `asset_fingerprint_incomplete` |
| 声明层的证据单元齐全 | `case_set_incomplete` |
| 报告是 parent 聚合产物 | `not_parent_process_bundle` |
| L1/L2 独立进程数与每进程样本索引齐全 | `process_policy_incomplete` |
| L1/L2 每个应观测样本都有 raw 与 validator 证据 | `raw_observation_incomplete` |
| 无 `stable_fail` / `critical_fail` / `unstable` | `stable_failures` / `unstable_results` |
| 无基础设施错误 | `infrastructure_errors` |
| L3 选集非空且全过 | `l3_empty` / `l3_incomplete` |
| 相对既有基线无逐例回退 | `baseline_regressions` |

CLI **刻意不提供** `--update-baseline` / `--accept-failures` / `--force`——绕过参数存在本身
就会被用掉。

gate 的公开 CLI 会串行启动独立 worker，再由 parent 校验并合并不可变报告。正式资格要求
L1、L2 各 **2 个独立进程 × 每进程 3 个样本**；同一进程内重复 3 次不能替代第二个进程。
parent JSON 的 `meta.process_sampling` 记录 bundle、required/observed 进程数以及每个 worker 的
role、run id、pid、layer、原始报告 SHA-256 和退出码，不记录临时文件路径。worker JSON 只记录
`meta.process_sample`，其 Markdown 明确标成 `process_bundle_role=worker`，因此即使其余读数全绿
也不能写正式 baseline。

能力幻觉、validator 后逃逸与 fallback 均按 `results[*].repetitions` 的**全部样本**聚合，不再
只看代表样本。缺任一 L1/L2 repetition 的 `raw_observed` 或 `validation_observed` 都会令
`raw_observation_complete=false`；缺证据不是“0 次幻觉”，不得靠缩小指标分母取得资格。

资格闸不会只相信上述两个 complete flag：写入前会从 `process_sampling` 与
`results[*].repetitions` 独立复算正式 gate 的 L1/L2 `2×3` 身份矩阵、worker digest/exit、
样本索引及 raw 字段。预先计算的 eligibility 与当前报告不一致时按 rejected 处理。正式 JSON
与 Markdown 会先各自在目标目录完整写入临时文件，再替换成对；第二次替换失败会把两目标回滚
到原字节或原“不存在”状态，并清理临时文件，不能留下新 JSON 配旧 Markdown 的混合基线。
复算还直接消费每个 repetition 的 pass/danger/raw/actual/fallback；顶层 result、overall、metrics
与 fallback 列表只是展示缓存，不能覆盖逐样本失败、能力幻觉、validator 逃逸或未声明兜底。

**证据单元是 `case_id@layer`**：同一条 case 在 L1 绿、L2 红时是两条独立记录。用裸
`case_id` 作 key 会让后写的层覆盖先写的层，报告于是替产品把红灯藏起来。因此
`--layer all` 的 overall 是**证据单元 micro**，不是去重后的案例准确率。

**逐案例复现**：报告里每条 `expected.repro` 直接印了复现命令（`--case <id> --repeat 3
--diagnose`）。失败条目同时带 `repetitions`（每次的语义签名）、`divergence`（首偏离边界）
与 `ablations`（定向消融矩阵与因果等级）。**`suspect` 与 `supported` 必须分开读**：只有
同 provider、同资产指纹下「稳定错 → 消融后稳定对」才配叫 `supported`。

### `domain_hit_rate` 与 `exact_plan_set_rate` 是两把尺子（2026-08-02 正名）

RoutingBench 的历史 N1 口径是「实际域与期望域**有交集**即通过」，现在按它的真实语义
显式叫 **`domain_hit_rate`**（`meta.metrics.domain_hit_rate`，逐例 pass/fail 与既有基线
逐字不变）。它证明不了组合意图完整：**期望两个域、实际只命中一个，它照样绿**。

组合完整性由对抗套件的 **`exact_plan_set_rate`** 判——全部必要意图组满足（组间 AND、
组内 OR）、无 forbidden、无未授权额外项、依赖与关键槽位齐全、replan 轮同样成立。
两者量的不是同一件事，**不得直接比数值升降**：`domain_hit_rate` 用于看历史趋势，
`exact_plan_set_rate` 用于看计划是否真的完整。

## 软层 A/B 的记账口径（skills / exemplars 共用）

**Δ 只能在「实际注入」的子集上算。** 未注入的用例两臂 prompt **逐字相同**，翻面只可能是
采样方差——首轮 60 例实测 3 次翻面里有 2 次栽在这上面，总 Δ 因此显示为负而可归因 Δ 是正的。
`inj:` 归因字段就是用来分账的，不必靠复跑分类。

**短命进程必须先预热向量**：范例是百量级，评测进程活不到后台预热跑完，不预热的 A/B
等于只测了词法档、系统性低估语义通道（`eval_exemplars --live` 已内置）。

## 规则退役判定的四条纪律（`hint_retirement`）

1. **必须 pin provider，且跨 provider 取交集**——单档候选只证明那一档会，而 hint 的存在
   理由正是「弱 LLM 会漏/误路由」。`--intersect` 让判定机械可复算。
2. **n=1 不做判定**（`--repeat` 全部通过才算候选）。
3. **证据要覆盖全部命中句**，不是抽样几句——实测抽 3 句判 27 条可退役、全覆盖降到 21 条；
   **抽样偏差方向固定：命中面越大越容易被放行，而那恰是风险最高的一批。**
4. **`require_confirm` 的能力其 hint 不由路由评测裁决**（治理⑥，须专项安全回归）。

⚠ 报告**按 provider 分文件**，`--only` 局部跑另存 `.only-<scope>` 后缀：实测局部跑曾把
32 条的全量盘点整份换成 1 条，交集因此算出假的 0——**一份「看起来正常」的报告，内容可能
只是上次局部跑的残留。**

## 怎么跑

```bash
python test/eval_fast_intent.py     # 跑一次，和已入库基线比对（默认不阻塞，exit 0）
python test/eval_route_hints.py     # 同上，云侧
```

## 怎么更新基线

代码里的路由规则有意变化（新增/修正正则）时，人工确认新行为符合预期后重新生成：

```bash
python test/eval_fast_intent.py --write-baseline
python test/eval_route_hints.py --write-baseline
git add docs/reviews/eval/baseline_*.json docs/reviews/eval/baseline_*.md
```

**不要**在没看清失败原因前直接 `--write-baseline` 把一次失败"消音"——先确认是语料标注错了
还是代码真的引入了回归。

### Journey canonical

journey canonical 不是手工“写基线”。先提交全部 canonical 输入，再从运行中
`/api/llm/providers` 读取 active provider/model，最后执行：

```bash
python scripts/run_e2e.py --milestone M-A --lane milestone --full \
  --canonical --provider <active-provider> --model <active-model> --stale-policy error
```

runner 会锁定 resolved selection、provider/model/revision、代码提交、输入 digest 与各状态计数，
写后立即复算 freshness。筛选运行、dirty canonical 输入、provider 漂移、任何 skip 或失败都只能
留下当次 artifact，不能覆盖这里的 canonical 报告。报告中的历史数据必须连同这些 metadata
一起引用，不能只摘通过数。

## 已知限制

- Roadmap 卡面原写"以飞书 1465 意图库为标注集"——原始表 `feishu_tblN5NfQff850L5O_*.json`
  已 gitignore 且磁盘上不存在（仅一次性用于生成 `commands.yaml`/`entities.yaml`，未保留标注
  语料）。当前基线用现有可得数据源：`orchestrator/edge/tests/corpus/`（15+14 条）+
  `test/eval_corpus/`（历史回归案例转录），规模远小于"1465"。补全飞书全量语料留作后续增强，
  不阻塞本卡验收。
- 不覆盖 `orchestrator/edge/tests/corpus/vehicle_objects.yaml::val_execution`（VAL 执行状态机）
  和 `safety_gate.yaml`（安全门控）——两者测的是执行/安全维度，不是"自然语言→意图分类"，
  混入会稀释指标含义。
- 不覆盖 `_confirm_reply`（"行程含'行'"回归，`orchestrator/cloud/engine.py`）——它是独立的
  整句长度启发式，不走 `manifest.route_hints` 声明式规则，本评测的 `RouteHintEngine` 管不到。
- v1 CI（`intent-eval-baseline` job）跌破基线只告警（`::warning::`），不阻塞合并；两脚本均支持
  `--strict`（有回归 exit 1）作为预留接口，未在 CI 启用。

## 指标含义

两套逻辑都是纯规则引擎（不经 LLM），同代码同输入 100% 可复现，所以"跌破阈值"落地为**逐例
回归比对**（某条用例从基线里 pass 翻成这次 fail），不是聚合百分比的模糊容差。报告按分桶
（多分类 object 识别 vs 二元 guard-rail 通过率 vs 路由召回/守护）分别呈现，不加权平均——
两种指标性质不同，混合会互相掩盖信号。
