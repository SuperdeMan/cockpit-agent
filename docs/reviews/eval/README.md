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
