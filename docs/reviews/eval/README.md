# 意图路由评测基线（T3.4）

本目录存放端侧 `fast_intent` 与云侧 `route_hints` 两套确定性路由规则的评测基线，
解决 `docs/reviews/2026-07-02-repo-audit-and-roadmap.md` §4 T3.4（G3：意图路由质量无评测基线）。
方案见 `docs/design/2026-07-03-r3.4-intent-eval-baseline.md`。

## 文件

| 文件 | 生成方 |
|---|---|
| `baseline_fast_intent.{json,md}` | `test/eval_fast_intent.py --write-baseline` |
| `baseline_route_hints.{json,md}` | `test/eval_route_hints.py --write-baseline` |
| `_ci-run-*`（gitignore，不入库） | CI 每次跑产生的临时报告，仅供当次 PR 查看，不覆盖基线 |

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
