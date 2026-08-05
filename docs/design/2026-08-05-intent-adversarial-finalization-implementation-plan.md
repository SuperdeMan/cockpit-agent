# 意图对抗最终收尾实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 日期：2026-08-05
>
> 状态：已批准，执行中
>
> 关联设计：`docs/design/2026-08-05-intent-adversarial-cross-process-confidence.md`
>
> 最终裁定：`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`

**Goal:** 把 gate 的 L1/L2 置信证据从“同进程重复三次”升级为“每层至少两个独立进程、每进程三次”，修掉全样本 raw Planner 能力幻觉与仍可跨进程复现的两条产品方差，并只在同一干净快照的 L1+L2+L3 父报告明确 `eligible=True` 时写入首份正式 baseline。

**Architecture:** `test/eval_intent_adversarial.py` 保持唯一公开入口，外层 parent 串行拉起不可递归的 worker；worker 继续复用现有单进程执行路径，系统临时目录保存分片，新的纯函数模块校验身份并聚合结果。L0 与 L3 只在 primary worker 执行一次，L1/L2 各由两个独立进程取样。逐 repetition 保存进程身份与 raw/validator/fallback 证据，父报告按跨进程错误签名分类并 fail-closed 判定 baseline。产品修复仅进入 Planner 通用 catalog 契约与现有 policy/exemplar/manifest 知识层，不新增 route hint。

**Tech Stack:** Python 3.12、pytest、dataclasses、PyYAML、`subprocess`、`tempfile`、现有 `ProviderLock`、PlanBuilder、Skill/Exemplar 检索、L3 journey runner 与 Markdown/JSON 报告器。

---

## 实施约束

- 工作目录固定为 `.worktrees/intent-adversarial-finalization`，分支固定为 `codex/intent-adversarial-finalization`。
- 根 `.env` 只读；活跑所需 provider/model/embedding 参数只在单条命令的进程环境中设置。
- 全栈只复用根 `compose.yaml` 启动的服务；不得直接以 `deploy/docker-compose.yaml` 为首文件。
- worker 必须串行执行。`ProviderLock` 改变全局 active provider，并发 worker 会互相覆盖。
- 每个生产改动必须先运行新增测试并观察预期失败，再写最小实现，再跑相关回归。
- 不新增或恢复 route hint，不修改 HMI、Dashboard、CI/CD、数据库 schema、`.env` 或通用 `scripts/run_e2e.py`。
- 不删除既有报告或历史证据。worker 临时分片只写系统临时目录；最终父报告按现有报告目录规则落盘。
- 每项由实施者完成后依次做规格审查、代码质量审查；有问题回到同一实施者修复并复审。
- 每个任务只暂存本任务列出的路径，提交前检查 `git diff --cached --check` 与 `git status --short`。

## Task 1：冻结 suite 独立进程与晋级 provenance 契约

**Files:**

- Modify: `test/support/intent_adversarial_contract.py`
- Modify: `test/test_intent_adversarial_contract.py`
- Modify: `test/eval_corpus/intent_adversarial/suites.yaml`
- Modify: `test/eval_corpus/intent_adversarial/cases/composition.yaml`

- [ ] **Step 1：先写会失败的 suite 契约测试**

  在 `test/test_intent_adversarial_contract.py` 覆盖：

  ```python
  assert suites["gate"].independent_processes == 2
  assert suites["gate"].independent_layers == ("l1", "l2")
  assert suites["discovery"].independent_processes == 1
  assert suites["discovery"].independent_layers == ()
  ```

  另构造非法 YAML，证明进程数小于 2、层含 `l0/l3`、discovery 声明多进程都会被静态校验拒绝。

- [ ] **Step 2：先写会失败的晋级 provenance 测试**

  对 `stabilized_at >= 2026-08-04` 的 stable case，分别证明缺少或伪造以下字段会报错：

  ```yaml
  stabilized_processes: 2
  stabilized_samples_per_process: 3
  stabilized_process_runs: [promotion-charge-nav-a, promotion-charge-nav-b]
  ```

  覆盖 run id 重复、进程数不足、每进程样本不足、`stabilized_samples` 小于乘积四种反例。

- [ ] **Step 3：运行红灯并记录失败原因**

  ```powershell
  python -m pytest test/test_intent_adversarial_contract.py -q
  ```

  预期新增测试因 `SuiteConfig` 无字段、loader 不解析、provenance 只校验总样本而失败。

- [ ] **Step 4：实现最小契约**

  在 `SuiteConfig` 尾部增加向后兼容默认值：

  ```python
  independent_processes: int = 1
  independent_layers: tuple[str, ...] = ()
  ```

  loader 将 YAML 列表规范化成 tuple；契约校验 gate 的采样层只能是 `l1/l2`，discovery 必须单进程。把现有 `_stabilized_samples_errors()` 扩成同一处完整 provenance 校验，不另造旁路。

- [ ] **Step 5：更新正式声明**

  `suites.yaml` 的 gate 增加 `independent_processes: 2` 与 `independent_layers: [l1, l2]`；discovery 显式保持 1/空。给 `cp.dep.charge-then-navigate` 回填已验收的 A/B 独立进程取证名称与 2×3 样本结构，总样本仍为 6。

- [ ] **Step 6：验证并提交**

  ```powershell
  python -m pytest test/test_intent_adversarial_contract.py test/test_eval_intent_adversarial_cli.py -q
  python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-finalization-l0-contract.json
  git diff --check
  ```

  提交：`test: declare independent process confidence contract`

## Task 2：建立逐样本证据与纯跨进程合并器

**Files:**

- Create: `test/support/intent_adversarial_process.py`
- Create: `test/test_intent_adversarial_process.py`
- Modify: `test/support/intent_adversarial_runtime.py`
- Modify: `test/support/intent_adversarial_report.py`
- Modify: `test/eval_intent_adversarial.py`
- Modify: `test/test_intent_adversarial_runtime.py`
- Modify: `test/test_intent_adversarial_report.py`

- [ ] **Step 1：为逐 repetition 完整证据写红灯测试**

  扩展 `RepeatOutcome`，并要求最终 `AdversarialResult.repetitions` 的每项具备：

  ```python
  {
      "process_run_id": "run-a",
      "sample_index": 0,
      "passed": False,
      "signature": "extra:media.pause",
      "dangerous": False,
      "raw_intents": ["nearby.search", "media.pause"],
      "raw_observed": True,
      "validation_observed": True,
      "actual_intents": ["nearby.search", "media.pause"],
      "plan_from_fallback": False,
  }
  ```

  新字段必须有安全默认值，保证 L0 与现有构造器不被迫伪造 raw 证据。

- [ ] **Step 2：为跨进程分类写参数化红灯测试**

  在新文件测试以下规则：任一危险样本为 `critical_fail`；两个进程全样本通过才 `pass`；同一错误签名覆盖两个不同进程为 `stable_fail`；错误只在一个进程或签名不一致为 `unstable`；重复 `process_run_id` 不能冒充两个进程。

- [ ] **Step 3：为 worker 计划与身份校验写红灯测试**

  纯函数 `worker_specs(layer, suite)` 必须返回：

  - `all`：`primary/all`、`corroboration-l1/l1`、`corroboration-l2/l2`；
  - `l1` 或 `l2`：同层 primary 与 corroboration 两项；
  - `l0`、`l3` 或 discovery：一项 primary。

  `validate_worker_bundle()` 覆盖 bundle/role/layer/run id/SHA/clean/provider/model/assets/suite/retrieval/temperature/selection/corpus/gold digest/admitted catalog 不一致，以及缺报告、解析失败、非法退出码、infra/drift/retrieval 降级。

- [ ] **Step 4：运行红灯**

  ```powershell
  python -m pytest test/test_intent_adversarial_process.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py -q
  ```

- [ ] **Step 5：实现无 I/O 的合并核心**

  新模块只放 dataclass、身份校验、结果反序列化、跨进程分类与父报告合并纯函数；不得在其中启动子进程或读 `.env`。relation 只聚合同一 worker 已完成的裁判结果，不跨 worker 重新配对 base/support。

- [ ] **Step 6：让执行路径记录所有样本**

  `eval_intent_adversarial.py` 在构造 repetition 时传入 worker run id 与样本序号；raw、validator 后 intents、fallback 都逐样本保存。顶层展示证据仍可选择代表样本，但指标不得再只读取代表样本。

- [ ] **Step 7：验证并提交**

  ```powershell
  python -m pytest test/test_intent_adversarial_process.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py test/test_eval_intent_adversarial_cli.py -q
  python -m py_compile test/support/intent_adversarial_process.py test/support/intent_adversarial_runtime.py test/support/intent_adversarial_report.py test/eval_intent_adversarial.py
  git diff --check
  ```

  提交：`test: merge intent evidence across independent processes`

## Task 3：把单一 CLI 改成串行 parent/worker 控制器

**Files:**

- Modify: `test/eval_intent_adversarial.py`
- Modify: `test/support/intent_adversarial_process.py`
- Modify: `test/test_eval_intent_adversarial_cli.py`
- Modify: `test/test_intent_adversarial_process.py`

- [ ] **Step 1：写隐藏 worker 参数与防递归红灯测试**

  参数固定为 `--_worker`、`--_bundle-id`、`--_process-run-id`、`--_worker-role`、`--_worker-report`，help 使用 `argparse.SUPPRESS`。worker 缺任一身份字段退出 2；worker 携带 `--write-baseline` 退出 2；worker 不得再拉起 subprocess。

- [ ] **Step 2：写父控制器子进程形状红灯测试**

  monkeypatch `subprocess.run`，断言 `--layer all` 串行启动三个 worker，且只有 primary 收到 `--layer all`；L1/L2 各启动两个；L0/L3/discovery 维持当前单进程执行。每个 worker 报告路径位于 `tempfile.TemporaryDirectory()`。

- [ ] **Step 3：写退出码红灯测试**

  产品红灯 worker 可返回 1 并被纳入合并；任一 worker 返回 2、报告缺失/不可解析、身份冲突或基础设施红灯，parent 返回 2；合并成功但产品红灯返回 1；全绿返回 0。

- [ ] **Step 4：实现 parent/worker 分流**

  公开 argv 在需要跨进程时进入 `_run_parent_bundle()`；隐藏 worker 路径调用现有单进程主体。parent 给所有 worker 复用原公开过滤参数与显式 provider/model，但禁止 worker 写正式 baseline。使用 `sys.executable` 与脚本绝对路径，显式传递必要环境，不能读取或打印 secret。

- [ ] **Step 5：确保 L3 只跑一次**

  primary/all 保留现有 `_l3_evidence()`；L1/L2 corroboration worker 不可收到 L3 selection，父合并器验证恰有一份新鲜 L3 证据。

- [ ] **Step 6：验证并提交**

  ```powershell
  python -m pytest test/test_eval_intent_adversarial_cli.py test/test_intent_adversarial_process.py -q
  python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-finalization-l0-parent.json
  git diff --check
  ```

  提交：`feat: orchestrate intent gate process bundles`

## Task 4：让报告与 baseline 资格 fail-closed 消费进程契约

**Files:**

- Modify: `test/support/intent_adversarial_report.py`
- Modify: `test/eval_intent_adversarial.py`
- Modify: `test/test_intent_adversarial_report.py`
- Modify: `test/test_eval_intent_adversarial_cli.py`
- Modify: `docs/reviews/eval/README.md`

- [ ] **Step 1：写 baseline 资格红灯测试**

  从现有 eligible fixture 出发逐项删除或改坏：

  ```python
  meta["process_bundle_role"] = "parent"
  meta["process_policy_complete"] = True
  meta["raw_observation_complete"] = True
  ```

  证明缺字段分别产生 `not_parent_process_bundle`、`process_policy_incomplete`、`raw_observation_incomplete`。worker 报告即使其他字段全绿也不可写 baseline。

- [ ] **Step 2：写全样本 raw/fallback 聚合红灯测试**

  代表样本通过但另一 repetition 存在 catalog 外 raw intent 时，幻觉分子必须为 1；任一未声明 fallback 都进入 `unexpected_fallback_plans`；缺任一应观测 L1/L2 repetition 时 `raw_observation_complete=False`。

- [ ] **Step 3：写 Markdown 证据形状红灯测试**

  摘要必须明确 parent/worker 身份、`L1 2×3`、`L2 2×3`、worker run id/role/layer/exit/report digest，并显示 `process_policy_complete` 与 `raw_observation_complete`。

- [ ] **Step 4：实现父报告 meta 与全样本指标**

  parent 写入 `process_sampling.bundle_id/required/observed/workers`，worker 报告写 `process_sample`。报告哈希使用原始 worker JSON bytes 的 SHA-256；父报告内嵌必要逐样本证据，不把临时路径当可重放证据。

- [ ] **Step 5：验证正式写入只能发生在父层**

  `write_baseline_if_eligible()` 继续原子写 JSON/Markdown；CLI worker 路径无论参数组合都到不了 writer。正式比较源、完整 case set、L3 新鲜度、raw 幻觉零、fallback 零、clean SHA 等旧硬闸全部保留。

- [ ] **Step 6：验证并提交**

  ```powershell
  python -m pytest test/test_intent_adversarial_report.py test/test_eval_intent_adversarial_cli.py test/test_intent_adversarial_process.py -q
  python -m py_compile test/support/intent_adversarial_report.py test/eval_intent_adversarial.py
  git diff --check
  ```

  提交：`test: fail closed on incomplete process evidence`

## Task 5：消除能力缺席用例的 raw Planner 幻觉

**Files:**

- Modify: `orchestrator/cloud/planning.py`
- Modify: `orchestrator/cloud/tests/test_planning.py`
- Modify: `orchestrator/cloud/tests/test_planning_toolcall.py`
- Modify: `test/test_intent_adversarial_runtime.py`

- [ ] **Step 1：写 prompt 契约红灯测试**

  `_planner_system()` 的普通 JSON 与 toolcall 两条路径都必须声明：动态 catalog 是 agent/intent 唯一白名单；不得编造、替换或输出缺席能力；请求只有缺席能力可承接时返回 `addressed=true, steps=[]`。`replan()` 的系统约束同样不得越过当前 catalog。

- [ ] **Step 2：写 validator 第二防线回归**

  现有 `_validated_steps()` 对未知 agent/intent 的剔除保持不变；测试明确 prompt 约束不是替代 validator。合法空计划不应因 `addressed=true` 被误判成解析失败后落未声明 fallback。

- [ ] **Step 3：运行红灯**

  ```powershell
  python -m pytest orchestrator/cloud/tests/test_planning.py orchestrator/cloud/tests/test_planning_toolcall.py test/test_intent_adversarial_runtime.py -q
  ```

- [ ] **Step 4：先做 prompt-only 最小修复**

  在通用 Planner base 与 replan 契约加入 catalog 白名单纪律，不往静态 tool schema 硬编码全量 intent，也不加领域分支。只有真实双进程 A8 仍出现 raw 幻觉时，才允许把“本轮动态 catalog 的 agent/intent 约束”收紧到调用时生成的 tool schema；该升级必须新增 schema 与 catalog 一致性单测。

- [ ] **Step 5：运行两进程 A8 定向真栈**

  使用唯一公开 CLI、显式锁定 `minimax:MiniMax-M3`，选择六条能力缺席 case，并检查父报告：raw capability hallucination 0、post-validation escape 0、无未声明 fallback、两个 L1 process run id 各三样本、零 provider/infra/trace/retrieval drift。

- [ ] **Step 6：验证并提交**

  ```powershell
  python -m pytest orchestrator/cloud/tests/test_planning.py orchestrator/cloud/tests/test_planning_toolcall.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py -q
  python -m py_compile orchestrator/cloud/planning.py
  python test/eval_skills.py
  git diff --check
  ```

  提交：`fix: constrain planner output to the live catalog`

## Task 6：收敛 dinner 与 battery 两条跨进程方差

**Files:**

- Modify: `skills/policies/negation-and-deferral.yaml`
- Modify: `skills/exemplars/nearby.yaml` only if ablation proves the existing exemplar is not selected for a generalizable retrieval reason
- Modify: `agents/charging_planner/manifest.yaml`
- Modify: `skills/exemplars/charging.yaml`
- Modify: `test/test_intent_adversarial_contract.py`
- Modify: `orchestrator/cloud/tests/test_catalog_budget.py`

- [ ] **Step 1：先用父 CLI 做 on-failure 诊断**

  同一命令只选 `nq.dinner-music.drop-music`、`os.battery.car` 以及 relation 所需 base，保留两独立 L1 进程。记录首偏离、检索名单与消融结果；不得把某一趟全绿当成已修复。

- [ ] **Step 2：写知识层回归红灯**

  为否定 policy 增加一个与对抗原句不同的 golden：尚未执行的并列诉求被“别/不用”取消时，既不能生成原动作，也不能生成 pause/stop/close 等反向动作；只有明确描述正在进行的状态并要求停止时才生成停止动作。

  为 charging 增加一个非同句 exemplar，表达“车辆电量已经见底/趴窝”是补能求助，应落 `charging.find`；`charging.status` 仅承接明确询问当前百分比、剩余续航或充电状态。manifest 的 description/examples 与该边界同步。

- [ ] **Step 3：运行红灯并确认不是自证**

  ```powershell
  python test/eval_skills.py
  python test/eval_exemplars.py
  python -m pytest test/test_intent_adversarial_contract.py orchestrator/cloud/tests/test_catalog_budget.py -q
  ```

  golden/exemplar 不得复制 `找家川菜馆，音乐就不用放了` 或 `车没电了`；现有 nearby 反例“找家火锅店，歌就不用放了”若已被检索，则不追加重复条目。

- [ ] **Step 4：做最小知识修复**

  只修改通用 policy、charging manifest 与非同句 exemplar。若 Step 1 证明 dinner 的失败是 existing exemplar 召回排序问题，先修可泛化的检索描述/关键词；不得把对抗 utterance 写入资产，不得新增 route hint。

- [ ] **Step 5：双进程复验四条历史不稳定 case**

  同时选择 dinner、battery、`cs.cancel-it.reminder`、`nq.hvac.keep-volume`。要求每条在两个独立 L1 进程各三次全过；cancel/hvac 不因修复回归；无 raw 幻觉、fallback、infra/provider/retrieval 红灯。

- [ ] **Step 6：验证并提交**

  ```powershell
  python test/eval_skills.py
  python test/eval_exemplars.py
  python -m pytest test/test_intent_adversarial_contract.py orchestrator/cloud/tests/test_catalog_budget.py -q
  python -m pytest scripts/tests/test_e2e_arch_guard.py -q
  git diff --check
  ```

  提交：`fix: stabilize negation and depleted battery routing`

## Task 7：在同一干净快照取得完整 gate 证据并按资格条件写 baseline

**Files:**

- Conditionally Create: `docs/reviews/eval/baseline_intent_adversarial.json`
- Conditionally Create: `docs/reviews/eval/baseline_intent_adversarial.md`
- Modify: `docs/reviews/eval/README.md`

- [ ] **Step 1：提交前置代码后确认快照干净**

  ```powershell
  git status --short
  git rev-parse HEAD
  ```

  ignored/generated 文件不影响 git clean 结论；任何 tracked diff 都必须先归入前置任务提交。

- [ ] **Step 2：跑确定性门禁**

  ```powershell
  python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-finalization-discovery-l0.json
  python test/eval_intent_adversarial.py --suite gate --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-finalization-gate-l0.json
  ```

  要求 discovery 70/70、555 条/516 唯一输入；gate strict 19/19、133 stable/123 唯一输入，且契约计数若因有意新增资产变化，先解释再同步文档。

- [ ] **Step 3：跑完整父 bundle，不先写 baseline**

  显式设置 `LLM_GATEWAY_ADDR=localhost:50052`、`EXEMPLAR_EMBED_TIMEOUT=8`、`SKILL_EMBED_TIMEOUT=8`，运行 gate `--layer all --live --provider minimax --model MiniMax-M3`。要求 parent 按顺序完成 primary/all、corroboration-l1、corroboration-l2，L3 只跑一次。

- [ ] **Step 4：审计父报告资格**

  核对 clean SHA、provider/model/lock、process run id、L1/L2 各 2×3、L3 selection/new run id、raw/escape/fallback、gold/asset digest、infra/trace/retrieval、全部 repeat status 与 `eligibility.reasons`。

- [ ] **Step 5：仅在 eligible=True 时写 baseline**

  以同一已提交 SHA 再运行相同完整命令并加 `--write-baseline`。writer 自己必须再次确认资格；生成的 JSON/Markdown 一并提交。若仍为 false，不创建 baseline，不放宽阈值，只把未关项写入最终 review/findings。

- [ ] **Step 6：验证并提交证据**

  baseline 生成时提交：`test: establish cross-process intent baseline`

  baseline 未生成时，本任务不为“形式完整”制造空文件；证据随 Task 8 文档提交。

## Task 8：同步单一真相源、全量回归、最终独立审查与推送

**Files:**

- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/architecture/cockpit-agent-architecture.md`
- Modify: `docs/guides/intent-adversarial-testing.md`
- Modify: `test/eval_corpus/intent_adversarial/README.md`
- Modify: `docs/design/2026-08-02-intent-routing-adversarial-testing.md`
- Modify: `docs/design/2026-08-02-intent-routing-adversarial-findings.md`
- Modify: `docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`
- Modify: `docs/design/2026-08-05-intent-adversarial-cross-process-confidence.md`
- Modify: `docs/design/2026-08-05-intent-adversarial-finalization-implementation-plan.md`
- Modify: `docs/design/README.md`
- Modify: `docs/agents-history.md`
- Modify: `test/README.md`

- [ ] **Step 1：同步事实，不复制历史流水**

  把 suite 规模、跨进程命令、报告字段、分类规则、raw 口径、当前最终结果与 baseline 有无同步到入口、架构、运行手册、corpus README、findings 与最终 review。运行手册中旧的 gate 122 唯一输入改为当前 123；`AGENTS.md` 只保留当前快照和活跃残余，逐批命令与长证据只进 findings/agents-history。

- [ ] **Step 2：关闭计划 checklist 并做文档一致性检查**

  只把实际完成的 checkbox 改为 `[x]`；未满足项保留 `[ ]` 并在最终 review 写明阻塞证据。全仓搜索并清理过期的“同进程 repeat 3 足够”、旧 122、raw 幻觉误写为 0 与虚构 baseline 表述。

- [ ] **Step 3：跑受影响回归**

  ```powershell
  python -m py_compile test/eval_intent_adversarial.py test/support/intent_adversarial_contract.py test/support/intent_adversarial_runtime.py test/support/intent_adversarial_report.py test/support/intent_adversarial_process.py orchestrator/cloud/planning.py
  python -m pytest test/test_build_intent_adversarial_candidates.py test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_trace.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py test/test_intent_adversarial_process.py test/test_eval_intent_adversarial_cli.py -q
  python test/eval_skills.py
  python test/eval_exemplars.py
  python -m pytest scripts/tests/test_e2e_arch_guard.py -q
  python test/smoke_edge.py
  ```

- [ ] **Step 4：跑项目正式后端基线**

  ```powershell
  python -m pytest --import-mode=importlib
  ```

  不用 `pytest test/` 替代根命令；该目录选集的裸 `server` import 冲突仍按独立 P1 记账。

- [ ] **Step 5：做最终独立 review**

  新 reviewer 从批准设计、最终 review 与 merge-base 开始审查整分支，重点构造：伪造两个 run id、缺 shard、worker 写 baseline、L3 重复、代表样本遮掉 raw 幻觉、provider drift、relation 跨 worker 错配、单次幸运全绿。P0/P1 必须修复并复审；P2 记录但不得与 baseline eligibility 混写。

- [ ] **Step 6：提交文档与最终修复**

  ```powershell
  git diff --check
  git status --short
  ```

  提交：`docs: finalize cross-process intent gate evidence`

- [ ] **Step 7：推送已授权分支**

  ```powershell
  git push -u origin codex/intent-adversarial-finalization
  ```

  推送后复核本地 HEAD 与远端 branch SHA 一致，最终交付报告列出提交、测试、正式 baseline 状态与仍存在的残余，不把历史证据冒充本轮新证据。
