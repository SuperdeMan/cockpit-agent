# 意图对抗最终收尾实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]` / `- [x]`) syntax for tracking.

> 日期：2026-08-05
>
> 状态：已完成；Task 1–8 全部完成；三轮独立复审的证据缺口已在
> `f0af9c0` 关闸，DeepSeek 对比/参考 baseline 已按当前 L3 原始证据契约重新写入，MiniMax
> 主模型仍 `eligible=False`；收尾证据已提交并推送到授权分支
>
> 关联设计：`docs/design/2026-08-05-intent-adversarial-cross-process-confidence.md`
>
> 最终裁定：`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`

**Goal:** 把 gate 的 L1/L2 置信证据从“同进程重复三次”升级为“每层至少两个独立进程、每进程三次”，修掉全样本 raw Planner 能力幻觉与仍可跨进程复现的两条产品方差，并只在同一干净快照的 L1+L2+L3 父报告明确 `eligible=True` 时写入首份正式 baseline。

**Architecture:** `test/eval_intent_adversarial.py` 保持唯一公开入口，外层 parent 串行拉起不可递归的 worker；worker 继续复用现有单进程执行路径，系统临时目录保存分片，新的纯函数模块校验身份并聚合结果。L0 与 L3 只在 primary worker 执行一次，L1/L2 各由两个独立进程取样。逐 repetition 保存进程身份与 raw/validator/fallback 证据，父报告按跨进程错误签名分类并 fail-closed 判定 baseline。产品侧以请求级 `capability_ref` 隔离 LLM wire 与真实 `agent_id/intent`，宿主在 validator 前解析，现有 Plan/执行/proto 不变；知识与范例只动态示范 ref，不拥有调用权，不新增 route hint。

**Tech Stack:** Python 3.12、pytest、dataclasses、PyYAML、`subprocess`、`tempfile`、现有 `ProviderLock`、PlanBuilder、Skill/Exemplar 检索、L3 journey runner 与 Markdown/JSON 报告器。

---

## 实施约束

- 工作目录固定为 `.worktrees/intent-adversarial-finalization`，分支固定为 `codex/intent-adversarial-finalization`。
- 根 `.env` 只读；活跑所需 provider/model/embedding 参数只在单条命令的进程环境中设置。
- 全栈只复用根 `compose.yaml` 启动的服务；不得直接以 `deploy/docker-compose.yaml` 为首文件。
- worker 必须串行执行。`ProviderLock` 改变全局 active provider，并发 worker 会互相覆盖。
- 每个生产改动必须先运行新增测试并观察预期失败，再写最小实现，再跑相关回归。
- 不新增或恢复 route hint，不修改 HMI、Dashboard、CI/CD、数据库 schema、`.env` 或通用 `scripts/run_e2e.py`。
- 不按领域硬编码 capability ref，不放宽 raw 幻觉定义，不从分母移除 invalid ref，不保留生产 legacy LLM 输出旁路。
- 不删除既有报告或历史证据。worker 临时分片只写系统临时目录；最终父报告按现有报告目录规则落盘。
- 每项由实施者完成后依次做规格审查、代码质量审查；有问题回到同一实施者修复并复审。
- 每个任务只暂存本任务列出的路径，提交前检查 `git diff --cached --check` 与 `git status --short`。

## Task 1：冻结 suite 独立进程与晋级 provenance 契约

**Files:**

- Modify: `test/support/intent_adversarial_contract.py`
- Modify: `test/test_intent_adversarial_contract.py`
- Modify: `test/eval_corpus/intent_adversarial/suites.yaml`
- Modify: `test/eval_corpus/intent_adversarial/cases/composition.yaml`

- [x] **Step 1：先写会失败的 suite 契约测试**

  在 `test/test_intent_adversarial_contract.py` 覆盖：

  ```python
  assert suites["gate"].independent_processes == 2
  assert suites["gate"].independent_layers == ("l1", "l2")
  assert suites["discovery"].independent_processes == 1
  assert suites["discovery"].independent_layers == ()
  ```

  另构造非法 YAML，证明进程数小于 2、层含 `l0/l3`、discovery 声明多进程都会被静态校验拒绝。

- [x] **Step 2：先写会失败的晋级 provenance 测试**

  对 `stabilized_at >= 2026-08-04` 的 stable case，分别证明缺少或伪造以下字段会报错：

  ```yaml
  stabilized_processes: 2
  stabilized_samples_per_process: 3
  stabilized_process_runs: [promotion-charge-nav-a, promotion-charge-nav-b]
  ```

  覆盖 run id 重复、进程数不足、每进程样本不足、`stabilized_samples` 小于乘积四种反例。

- [x] **Step 3：运行红灯并记录失败原因**

  ```powershell
  python -m pytest test/test_intent_adversarial_contract.py -q
  ```

  预期新增测试因 `SuiteConfig` 无字段、loader 不解析、provenance 只校验总样本而失败。

- [x] **Step 4：实现最小契约**

  在 `SuiteConfig` 尾部增加向后兼容默认值：

  ```python
  independent_processes: int = 1
  independent_layers: tuple[str, ...] = ()
  ```

  loader 将 YAML 列表规范化成 tuple；契约校验 gate 的采样层只能是 `l1/l2`，discovery 必须单进程。把现有 `_stabilized_samples_errors()` 扩成同一处完整 provenance 校验，不另造旁路。

- [x] **Step 5：更新正式声明**

  `suites.yaml` 的 gate 增加 `independent_processes: 2` 与 `independent_layers: [l1, l2]`；discovery 显式保持 1/空。给 `cp.dep.charge-then-navigate` 回填已验收的 A/B 独立进程取证名称与 2×3 样本结构，总样本仍为 6。

- [x] **Step 6：验证并提交**

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

- [x] **Step 1：为逐 repetition 完整证据写红灯测试**

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

- [x] **Step 2：为跨进程分类写参数化红灯测试**

  在新文件测试以下规则：任一危险样本为 `critical_fail`；两个进程全样本通过才 `pass`；同一错误签名覆盖两个不同进程为 `stable_fail`；错误只在一个进程或签名不一致为 `unstable`；重复 `process_run_id` 不能冒充两个进程。

- [x] **Step 3：为 worker 计划与身份校验写红灯测试**

  纯函数 `worker_specs(layer, suite)` 必须返回：

  - `all`：`primary/all`、`corroboration-l1/l1`、`corroboration-l2/l2`；
  - `l1` 或 `l2`：同层 primary 与 corroboration 两项；
  - `l0`、`l3` 或 discovery：一项 primary。

  `validate_worker_bundle()` 覆盖 bundle/role/layer/run id/SHA/clean/provider/model/assets/suite/retrieval/temperature/selection/corpus/gold digest/admitted catalog 不一致，以及缺报告、解析失败、非法退出码、infra/drift/retrieval 降级。

- [x] **Step 4：运行红灯**

  ```powershell
  python -m pytest test/test_intent_adversarial_process.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py -q
  ```

- [x] **Step 5：实现无 I/O 的合并核心**

  新模块只放 dataclass、身份校验、结果反序列化、跨进程分类与父报告合并纯函数；不得在其中启动子进程或读 `.env`。relation 只聚合同一 worker 已完成的裁判结果，不跨 worker 重新配对 base/support。

- [x] **Step 6：让执行路径记录所有样本**

  `eval_intent_adversarial.py` 在构造 repetition 时传入 worker run id 与样本序号；raw、validator 后 intents、fallback 都逐样本保存。顶层展示证据仍可选择代表样本，但指标不得再只读取代表样本。

- [x] **Step 7：验证并提交**

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

- [x] **Step 1：写隐藏 worker 参数与防递归红灯测试**

  参数固定为 `--_worker`、`--_bundle-id`、`--_process-run-id`、`--_worker-role`、`--_worker-report`，help 使用 `argparse.SUPPRESS`。worker 缺任一身份字段退出 2；worker 携带 `--write-baseline` 退出 2；worker 不得再拉起 subprocess。

- [x] **Step 2：写父控制器子进程形状红灯测试**

  monkeypatch `subprocess.run`，断言 `--layer all` 串行启动三个 worker，且只有 primary 收到 `--layer all`；L1/L2 各启动两个；L0/L3/discovery 维持当前单进程执行。每个 worker 报告路径位于 `tempfile.TemporaryDirectory()`。

- [x] **Step 3：写退出码红灯测试**

  产品红灯 worker 可返回 1 并被纳入合并；任一 worker 返回 2、报告缺失/不可解析、身份冲突或基础设施红灯，parent 返回 2；合并成功但产品红灯返回 1；全绿返回 0。

- [x] **Step 4：实现 parent/worker 分流**

  公开 argv 在需要跨进程时进入 `_run_parent_bundle()`；隐藏 worker 路径调用现有单进程主体。parent 给所有 worker 复用原公开过滤参数与显式 provider/model，但禁止 worker 写正式 baseline。使用 `sys.executable` 与脚本绝对路径，显式传递必要环境，不能读取或打印 secret。

- [x] **Step 5：确保 L3 只跑一次**

  primary/all 保留现有 `_l3_evidence()`；L1/L2 corroboration worker 不可收到 L3 selection，父合并器验证恰有一份新鲜 L3 证据。

- [x] **Step 6：验证并提交**

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

- [x] **Step 1：写 baseline 资格红灯测试**

  从现有 eligible fixture 出发逐项删除或改坏：

  ```python
  meta["process_bundle_role"] = "parent"
  meta["process_policy_complete"] = True
  meta["raw_observation_complete"] = True
  ```

  证明缺字段分别产生 `not_parent_process_bundle`、`process_policy_incomplete`、`raw_observation_incomplete`。worker 报告即使其他字段全绿也不可写 baseline。

- [x] **Step 2：写全样本 raw/fallback 聚合红灯测试**

  代表样本通过但另一 repetition 存在 catalog 外 raw intent 时，幻觉分子必须为 1；任一未声明 fallback 都进入 `unexpected_fallback_plans`；缺任一应观测 L1/L2 repetition 时 `raw_observation_complete=False`。

- [x] **Step 3：写 Markdown 证据形状红灯测试**

  摘要必须明确 parent/worker 身份、`L1 2×3`、`L2 2×3`、worker run id/role/layer/exit/report digest，并显示 `process_policy_complete` 与 `raw_observation_complete`。

- [x] **Step 4：实现父报告 meta 与全样本指标**

  parent 写入 `process_sampling.bundle_id/required/observed/workers`，worker 报告写 `process_sample`。报告哈希使用原始 worker JSON bytes 的 SHA-256；父报告内嵌必要逐样本证据，不把临时路径当可重放证据。

- [x] **Step 5：验证正式写入只能发生在父层**

  `write_baseline_if_eligible()` 对 JSON、Markdown 各自执行原子替换，第二个文件写入失败时回滚；
  不承诺进程硬终止下的跨文件原子事务。CLI worker 路径无论参数组合都到不了 writer。正式比较源、
  完整 case set、L3 新鲜度、raw 幻觉零、fallback 零、clean SHA 等旧硬闸全部保留。

- [x] **Step 6：验证并提交**

  ```powershell
  python -m pytest test/test_intent_adversarial_report.py test/test_eval_intent_adversarial_cli.py test/test_intent_adversarial_process.py -q
  python -m py_compile test/support/intent_adversarial_report.py test/eval_intent_adversarial.py
  git diff --check
  ```

  提交：`test: fail closed on incomplete process evidence`

## Task 5：以请求级 capability_ref 消除 raw Planner 能力幻觉

前三次通用软约束已经完成但未达到 Task 5 DoD：`af49ffb`（catalog 白名单 prompt）、
`3387cdd`（运行时 `enum/oneOf` tool schema）、`43ad952`（catalog 尾置）在同六条 A8、
`minimax:MiniMax-M3`、两个独立 L1 进程 × 每进程每 case 3 次下，都是最终 6/6 pass、escape 0、
无意外 fallback、process/infra/provider/retrieval/trace 完整，但 raw capability hallucination 仍
6/6。`strict=true + steps.maxItems=0` 仍返回一步的隔离探针说明不能再把 schema 当模型强制器。
这些读数只作为协议升级依据，不勾成成功证据。

**Files:**

- Modify: `orchestrator/cloud/planning.py`
- Modify: `orchestrator/cloud/context.py`
- Modify: `orchestrator/cloud/skills.py`
- Modify: `orchestrator/cloud/exemplars.py`
- Create: `orchestrator/cloud/tests/test_planning_capability_refs.py`
- Create: `test/test_intent_adversarial_capability_refs.py`
- Modify: `orchestrator/cloud/tests/test_planning.py`
- Modify: `orchestrator/cloud/tests/test_planning_toolcall.py`
- Modify: `orchestrator/cloud/tests/test_planning_no_action.py`
- Modify: `orchestrator/cloud/tests/test_planning_intent_rehome.py`
- Modify: `orchestrator/cloud/tests/test_planning_reject.py`
- Modify: `orchestrator/cloud/tests/test_planning_skill_repairs.py`
- Modify: `orchestrator/cloud/tests/test_catalog_budget.py`
- Modify: `orchestrator/cloud/tests/test_context.py`
- Modify: `orchestrator/cloud/tests/test_emotion.py`
- Modify: `orchestrator/cloud/tests/test_skills.py`
- Modify: `orchestrator/cloud/tests/test_exemplars.py`
- Modify: `orchestrator/cloud/tests/test_engine_confirm.py`
- Modify: `orchestrator/cloud/tests/test_engine_context.py`
- Modify: `orchestrator/cloud/tests/test_engine_escalate.py`
- Modify: `orchestrator/cloud/tests/test_engine_focus.py`
- Modify: `orchestrator/cloud/tests/test_engine_multiturn_context.py`
- Modify: `orchestrator/cloud/tests/test_engine_reject.py`
- Modify: `orchestrator/cloud/tests/test_engine_stream.py`
- Modify: `orchestrator/cloud/tests/test_multi_intent.py`
- Modify: `orchestrator/cloud/tests/test_regression_intent_integrity.py`
- Modify: `orchestrator/cloud/tests/test_suspend_prior.py`
- Modify: `test/support/intent_adversarial_trace.py`
- Modify: `test/test_intent_adversarial_trace.py`
- Modify: `test/test_intent_adversarial_runtime.py`
- Modify: `test/test_eval_intent_adversarial_cli.py`
- Modify: `test/e2e_planner_toolcall.py`
- Modify: `test/eval_skills.py`
- Modify: `test/eval_exemplars.py`
- Modify: `skills/policies/negation-and-deferral.yaml`
- Modify: `skills/guides/conditional-reminder.yaml`
- Modify: `skills/guides/navigation-with-stop.yaml`
- Modify: `skills/guides/multi-day-trip.yaml`
- Modify: `skills/guides/weather-outing.yaml`
- Modify: `skills/guides/charging-strategy.yaml`
- Modify: `skills/guides/shop-order-flow.yaml`

除两个专用 contract 测试外，上述测试文件来自全仓 `PlanBuilder(`、直接调用
`_submit_plan_tools/_parse_and_validate_data` 与 legacy wire/schema fixture 检索，包含 engine、
context、emotion、多意图、回归完整性、挂起恢复、评测 builder 探针及真实 provider toolcall
探针。Task 5 的 staged scope 显式允许在 Task 5B 统一迁移这些 fixture；提交时仍只暂存实际发生
迁移的上述路径，不把无关工作树改动带入。

### Task 5A：先冻结引用、wire 与 raw 证据契约

Task 5A 只创建两份专用 contract fixture，并在必要处用最小 fake catalog/agent；不修改现有
engine、planning、skill、评测或 E2E fixture 的 legacy 输出。目的只有一个：让每个新增红灯都能
归因到一个尚未实现的 capability-ref 契约，而不是在生产 seam 改动前制造大面积伴随红。

- [x] **Step 1：写请求级 ref 与最终 catalog 可见面的红灯测试**

  在新建 `orchestrator/cloud/tests/test_planning_capability_refs.py` 中用最小 fake agent 固定：

  ```python
  refs = build_refs([("alpha", "alpha.one"), ("beta", "beta.two")])
  assert refs.by_ref == {
      "cap_0001": ("alpha", "alpha.one"),
      "cap_0002": ("beta", "beta.two"),
  }
  assert all("alpha" not in ref and "beta" not in ref for ref in refs.by_ref)
  ```

  输入乱序/重复时结果仍按 `(agent_id, intent)` 排序且去重；连续两次 build 各创建独立映射
  对象，不共享可变状态、不进 module cache。ref 只保证请求内确定，测试不得把某次编号写成跨请求
  兼容承诺。再把 `PLANNER_CATALOG_BUDGET_CHARS` 压低，预算对象必须是最终注入 prompt 的
  ref→语义能力映射**全文**，计入抬头、ref、箭头/标点、能力说明、slots 与换行。测试一个不可变
  `PlannerCapabilityCatalog` 同时产出 `visible_agents`、`semantic_mapping_text`、双向 refs、
  schema/resolver/validator 共用的 `agent_map` 与 `catalog_stats`；权限过滤或预算裁掉的 agent 在
  这些视图中同时消失。禁止“旧 catalog 先计费、完整 agents 先建 refs、prompt 再无预算追加
  mapping”的错位。

  增加两类“无可继续裁”反例：①候选全部是 protected；②只剩最后一个非 protected agent 且它
  自身就超预算。两者都必须沿用现有行为，至少保留一个 agent、不截断 mapping，允许
  `chars_final > PLANNER_CATALOG_BUDGET_CHARS` 并告警；stats 的 full/final/dropped 必须与实际
  注入文本及 visible set 一致，永远不返回空 catalog。

- [x] **Step 2：写普通 JSON、tool schema 与末尾 prompt 的红灯测试**

  普通 JSON 与 toolcall system prompt 都只描述以下 step wire：

  ```json
  {"id":"s1","capability_ref":"cap_0001","slots":{},"depends_on":[],"slot_refs":{}}
  ```

  step `properties` 精确包含 `id`、`capability_ref`、`slots`、`depends_on`、`slot_refs`；
  `required` 至少包含 `id`、`capability_ref`。两处均不得含 `agent_id/intent`；enum 精确等于本请求
  refs，空映射约束 `steps=[]`。user message 的最后顺序必须是 skill → exemplar → context/history
  → ref→语义能力映射 → 用户原话。映射只渲染最终 visible catalog，ref 文本不带语义。断言
  catalog/enum/resolver/validator 四者 pair 集完全相等。

  同一专用 contract 文件静态锁住 `_PLANNER_BASE`、`_CATALOG_ALLOWLIST_SECTION`、
  `_REPLAN_SYSTEM`：删掉当前顶层
  legacy step 与“并行独立 / 串行依赖 / 混合关系”三组 pair 示例，改成无能力身份的语义/DAG
  规则；若展示 wire，只能写 `capability_ref:"<从本请求映射选择>"`，不得把 `cap_0001` 静态
  绑定 HVAC/media/nearby 等领域。规划/replan system prompt 均不得再含 legacy step 输出形状。

- [x] **Step 3：写 build、toolcall salvage、retry 与 replan 共图红灯测试**

  在专用 contract 文件中将
  `_parse_and_validate_data(wire, catalog: PlannerCapabilityCatalog, fallback_text)` 固定为唯一解析
  seam。用一个最小 spy 捕获一次 `build()` 的首轮 toolcall、同轮文本 salvage 与第二轮普通 JSON：
  三条分支必须收到同一个不可变 catalog，不能 retry 后重新编号。有效 ref 在 validator 前还原
  为真实 pair；最终
  `Plan.steps[*].agent_id/intent`、Executor 输入与 proto 结构保持不变。`replan()` 在权限过滤和
  catalog 预算后为该次请求创建新 catalog，且包括 `done=true` 空 steps 在内的每个 replan wire
  都经过同一 seam，不得直接调用 `_validated_steps()`；prompt 与解析共用它，返回的
  `ReplanDecision.steps` 仍是现有 `Step`。本步骤只新增这一组专用 spy，不迁移任何存量模拟 LLM
  fixture；只直接测试 `_validated_steps()` 的单测将来仍可构造解析后的内部 pair，生产代码不得
  保留 legacy wire 旁路。

- [x] **Step 4：写动态 skill/exemplar 渲染与旧形状禁入红灯测试**

  在专用 contract 文件内构造各一条内存 skill/exemplar，不改正式 YAML。锁住
  `exemplars.render_block()` 与 skill `few_shots` 渲染接收本请求 refs：YAML 仍存真实
  agent/intent 供治理，注入 prompt 时输出 `capability_ref`；任一步不在最终映射中则整条示例
  不注入，不能留下半条 DAG。`render_for_names()` 的 replan 继承必须用 replan 映射重新渲染。
  另以最小 scanner fixture 证明实际注入块出现 legacy step 的 `agent_id/intent` 输出形状时失败。
  `test/eval_skills.py` / `test/eval_exemplars.py` 与 7 个正式 skill YAML 的迁移统一留到 Task 5B
  Step 9，不在红灯提交中展开。

- [x] **Step 5：写 invalid sentinel、no-action 与合法 DAG 回归红灯测试**

  在新建 `test/test_intent_adversarial_capability_refs.py` 用一个最小 trace sink 构造五类 validator
  前 wire：有效 ref、未知 ref、缺失 ref、非字符串 ref、继续输出 legacy `agent_id/intent`。有效
  ref 的现有 `raw_intents` 必须还原真实
  intent；其余每个无效 step 都写 `__invalid_capability_reference__`，保留在 raw candidate 与
  原有幻觉聚合分母，不能静默丢弃或另开不计闸指标。trace 同时覆盖普通 JSON、toolcall、retry、
  replan。`attach_validation_trace()` 只包装上述 seam，显式拿到解析前 wire、同一不可变 refs、
  解析后 pair 与 validator 结果；resolver/trace 异常继续使 `raw_observed=False` 并记
  `trace_errors`，不能用空 raw 列表冒充完整观测。

  同一专用文件证明连续两次 `addressed=true, steps=[]` 走现有
  `*_no_action`，不调用 `_fallback`；带 invalid ref 的非空 steps 不得伪装成 no-action。另锁住
  合法单步与多步 DAG：`depends_on`、`slot_refs`、完整 slots、clarify、skill repairs 以及唯一
  intent re-home 的 validator 内部行为不回归。

- [x] **Step 6：运行全部新增测试并记录预期红灯**

  ```powershell
  python -m pytest orchestrator/cloud/tests/test_planning_capability_refs.py test/test_intent_adversarial_capability_refs.py -q
  ```

  预期只出现以下可归因红因：`PlannerCapabilityCatalog/_assemble_capability_catalog` 尚不存在；
  最终 mapping 全文预算/至少留一 agent 契约尚未实现；schema/prompt 仍暴露 legacy pair；统一
  build/retry/replan seam 尚不存在；动态资产渲染尚不接 refs；trace 尚不会解析 ref/sentinel。
  不在 5A 运行存量大套件或迁移旧 fixture；若出现与上述契约无关的批量失败，先缩回专用 fixture，
  不能把伴随红当作“预期 TDD 红灯”。

### Task 5B：最小实现引用协议并取得真栈证据

- [x] **Step 7：实现最终 visible catalog 的请求级映射**

  在 `planning.py` 增加私有不可变 `PlannerCapabilityCatalog` 与唯一入口
  `_assemble_capability_catalog()`；在 `context.py` 抽出复用现有 protected/drop 规则、以调用方
  提供的**实际 mapping renderer** 逐轮计费的预算装配函数。`build()` / `replan()` 先按 granted
  permissions 过滤，再只调用该入口。每轮候选变化都重新按 pair 排序编号、渲染全文并计 budget；
  最终对象一次性提供 visible agents、mapping text、双向 refs、`agent_map` 与 stats，
  `_submit_plan_tools()`、resolver、validator、prompt 不得另读原始 agents。预算循环至少保留一个
  agent；当候选全是 protected 或只剩最后一个 agent 时停止继续裁，允许既有略超预算语义，
  不截断能力条目，并如实记录 stats/告警。对象只作为调用栈局部变量传递，不挂到 `Plan`、全局、
  缓存、日志外部契约或 proto；ref catalog 在 user message 最后封口。

- [x] **Step 8：实现统一 wire 解析并保留 validator 第二防线**

  `_submit_plan_tools()`、普通 JSON prompt、toolcall/salvage、第二轮 JSON retry、`replan()` 都只收
  `capability_ref`。迁移 `_PLANNER_BASE` 三组 legacy pair 示例、`_CATALOG_ALLOWLIST_SECTION` 与
  `_REPLAN_SYSTEM`，静态 prompt 只留无能力身份规则。统一
  `_parse_and_validate_data(wire, catalog, fallback_text)` 在 `_validated_steps()` 前把有效 ref
  复制成内部
  `agent_id/intent`；legacy key、未知/缺失/错类型 ref 使整份计划按现有原子语义拒绝，但不得在
  trace 前消失。build/replan 的全部 wire 分支只能走该 seam，replan 删除直调 validator 的旁路。
  `_validated_steps()` 继续防御最终 pair、slots、depends_on、slot_refs 与 catalog 漂移，不把 ref
  解析当 validator 替代品。不增加任何领域分支、route hint 或 legacy 生产旁路。

- [x] **Step 9：动态渲染软资产并迁移测试 fixture**

  `skills.py` / `exemplars.py` 的规划轮与 `render_for_names()` 都显式接收 refs；结构化语义资产
  只有在映射命中时才渲染成当前请求的 `capability_ref`。把 7 个已列 YAML 的自由文本输出 JSON
  改成语义规则或结构化 few-shot，并让 `test/eval_skills.py` / `test/eval_exemplars.py` 的 CI scanner
  拒绝后来新增的旧形状。

  此时再统一迁移 Files 中全部存量 fixture：planning、engine、context、emotion、多意图、回归、挂起、
  skills、评测 runtime/CLI 的模拟 LLM wire 改成 `capability_ref`；
  `test_emotion.py` 的 legacy `_STEP` 与直接 `_submit_plan_tools/_parse_and_validate_data` 调用必须改用
  请求级 catalog。`test/e2e_planner_toolcall.py` 删除静态 `_CATALOG`/旧 `agent_id+intent` 字段断言，
  用生产装配路径生成 mapping/schema，并在 toolcall 与文本 salvage 两路都断言 step 具备
  `id+capability_ref` 且不含 legacy pair。validator 单测的 pair 输入继续明确标注为“resolver 后
  内部形状”。Task 5A 的专用红灯也在最小实现后转绿。

- [x] **Step 10：让 trace 在字段迁移后保持同一 raw 口径**

  `attach_validation_trace()` 包装统一 `_parse_and_validate_data` seam，在 ref 解析前保存 wire 快照，
  并以传入的同一不可变 catalog 构造裁判快照：有效 ref 还原真实 raw intent，无效/legacy step
  写 sentinel。最终 accepted snapshot 仍来自真实 Plan。
  build 与 replan 都必须产生可聚合的 validation trace；`RepeatOutcome.raw_intents`、
  `raw_observed`、raw capability hallucination 与 baseline eligibility 不改字段、不改分母、不放宽。

- [x] **Step 11：跑受影响回归并确认全部转绿**

  ```powershell
  python -m py_compile orchestrator/cloud/planning.py orchestrator/cloud/context.py orchestrator/cloud/skills.py orchestrator/cloud/exemplars.py test/support/intent_adversarial_trace.py
  python -m pytest orchestrator/cloud/tests/test_planning_capability_refs.py orchestrator/cloud/tests/test_planning.py orchestrator/cloud/tests/test_planning_toolcall.py orchestrator/cloud/tests/test_planning_no_action.py orchestrator/cloud/tests/test_planning_intent_rehome.py orchestrator/cloud/tests/test_planning_reject.py orchestrator/cloud/tests/test_planning_skill_repairs.py orchestrator/cloud/tests/test_catalog_budget.py orchestrator/cloud/tests/test_context.py orchestrator/cloud/tests/test_emotion.py orchestrator/cloud/tests/test_skills.py orchestrator/cloud/tests/test_exemplars.py orchestrator/cloud/tests/test_engine_confirm.py orchestrator/cloud/tests/test_engine_context.py orchestrator/cloud/tests/test_engine_escalate.py orchestrator/cloud/tests/test_engine_focus.py orchestrator/cloud/tests/test_engine_multiturn_context.py orchestrator/cloud/tests/test_engine_reject.py orchestrator/cloud/tests/test_engine_stream.py orchestrator/cloud/tests/test_multi_intent.py orchestrator/cloud/tests/test_regression_intent_integrity.py orchestrator/cloud/tests/test_suspend_prior.py test/test_intent_adversarial_capability_refs.py test/test_intent_adversarial_trace.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py test/test_eval_intent_adversarial_cli.py -q
  python test/eval_skills.py
  python test/eval_exemplars.py
  python -m pytest scripts/tests/test_e2e_arch_guard.py -q
  git diff --check
  ```

  明确检查现有合法多步、`depends_on`、`slot_refs`、slots、no-action 与 replan 回归；不能只跑
  新增 ref 单测。

- [x] **Step 12：运行同六条 A8 的 2×3 真栈验收后再进入 Task 6**

  先验证真实 provider 的 toolcall wire。前提是根 `compose.yaml` 启动的 llm-gateway 可达，且
  MiniMax 凭证已配置；凭证/服务缺失时脚本只可记录 SKIP，不能把 SKIP 当 Task 5 验收通过：

  ```powershell
  python test/e2e_planner_toolcall.py --providers minimax --rounds 1
  ```

  四类 probe 的 toolcall 或生产允许的文本 salvage 必须全部满足 `addressed`、`steps` 与
  `id+capability_ref` wire，且不再接受 `agent_id/intent` schema/arguments。该探针通过后再运行同六条
  A8：

  ```powershell
  python test/eval_intent_adversarial.py --suite gate --layer l1 --live --provider minimax --model MiniMax-M3 --repeat 3 --diagnose --case cc.missing.fallback-still-works --case cc.missing.nearby-search --case cc.missing.reminder --case cc.missing.research --case cc.missing.shop --case cc.missing.trip-plan
  ```

  父报告必须证明两个独立 L1 process run id、每进程每 case 3 次，且每个 repetition 都有完整
  raw/validator/process/provider/retrieval/trace 证据。验收值固定为：六条最终 6/6 pass、raw
  capability hallucination 0、post-validation escape 0、unexpected fallback 0、零 infra/provider/
  retrieval/trace drift。任一 invalid sentinel、缺 raw 样本或字段迁移导致的分母缩小都算失败；
  不得用领域硬编码、route hint、放宽 raw 定义或删除 invalid ref 关闭红灯。只有满足上述条件才
  提交 `feat: enforce referential planner capability contract` 并进入 Task 6。

## Task 6：收敛 dinner 与 battery 两条跨进程方差

**Files:**

- Modify: `skills/policies/negation-and-deferral.yaml`
- Modify: `skills/exemplars/nearby.yaml` only if ablation proves the existing exemplar is not selected for a generalizable retrieval reason
- Modify: `agents/charging_planner/manifest.yaml`
- Modify: `skills/exemplars/charging.yaml`
- Modify: `test/test_intent_adversarial_contract.py`
- Modify: `orchestrator/cloud/tests/test_catalog_budget.py`

- [x] **Step 1：先用父 CLI 做 on-failure 诊断**

  同一命令只选 `nq.dinner-music.drop-music`、`os.battery.car` 以及 relation 所需 base，保留两独立 L1 进程。记录首偏离、检索名单与消融结果；不得把某一趟全绿当成已修复。

- [x] **Step 2：写知识层回归红灯**

  为否定 policy 增加一个与对抗原句不同的 golden：尚未执行的并列诉求被“别/不用”取消时，既不能生成原动作，也不能生成 pause/stop/close 等反向动作；只有明确描述正在进行的状态并要求停止时才生成停止动作。

  为 charging 增加一个非同句 exemplar，表达“车辆电量已经见底/趴窝”是补能求助，应落 `charging.find`；`charging.status` 仅承接明确询问当前百分比、剩余续航或充电状态。manifest 的 description/examples 与该边界同步。

- [x] **Step 3：运行红灯并确认不是自证**

  ```powershell
  python test/eval_skills.py
  python test/eval_exemplars.py
  python -m pytest test/test_intent_adversarial_contract.py orchestrator/cloud/tests/test_catalog_budget.py -q
  ```

  golden/exemplar 不得复制 `找家川菜馆，音乐就不用放了` 或 `车没电了`；现有 nearby 反例“找家火锅店，歌就不用放了”若已被检索，则不追加重复条目。

- [x] **Step 4：做最小知识修复**

  只修改通用 policy、charging manifest 与非同句 exemplar。若 Step 1 证明 dinner 的失败是 existing exemplar 召回排序问题，先修可泛化的检索描述/关键词；不得把对抗 utterance 写入资产，不得新增 route hint。

- [x] **Step 5：双进程复验四条历史不稳定 case**

  同时选择 dinner、battery、`cs.cancel-it.reminder`、`nq.hvac.keep-volume`。要求每条在两个独立 L1 进程各三次全过；cancel/hvac 不因修复回归；无 raw 幻觉、fallback、infra/provider/retrieval 红灯。

- [x] **Step 6：验证并提交**

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

- [x] **Step 1：提交前置代码后确认快照干净**

  ```powershell
  git status --short
  git rev-parse HEAD
  ```

  ignored/generated 文件不影响 git clean 结论；任何 tracked diff 都必须先归入前置任务提交。

- [x] **Step 2：跑确定性门禁**

  ```powershell
  python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-finalization-discovery-l0.json
  python test/eval_intent_adversarial.py --suite gate --layer l0 --strict --out-json docs/reviews/eval/_ci-run-intent-finalization-gate-l0.json
  ```

  实际结果为 discovery 76/76、561 条/522 唯一输入；gate strict 25/25、139 stable/129 唯一输入。契约计数因本批有意新增资产而变化，已同步到入口文档与运行手册。

- [x] **Step 3：跑完整父 bundle，不先写 baseline**

  显式设置 `LLM_GATEWAY_ADDR=localhost:50052`、`EXEMPLAR_EMBED_TIMEOUT=8`、
  `SKILL_EMBED_TIMEOUT=8`；从不含根 `.env` 的验证 worktree 运行时还必须让
  `E2E_STACK_ROOT` 指向同仓库主 checkout。按用户指定的替代模型运行 gate
  `--layer all --live --provider deepseek --model deepseek-v4-flash`。要求 parent 按顺序完成
  primary/all、corroboration-l1、corroboration-l2，L3 只跑一次。

- [x] **Step 4：审计父报告资格**

  核对 clean SHA、provider/model/lock、process run id、L1/L2 各 2×3、L3 selection/new run id、raw/escape/fallback、gold/asset digest、infra/trace/retrieval、全部 repeat status 与 `eligibility.reasons`。

- [x] **Step 5：仅在 eligible=True 时写 baseline**

  以同一已提交 SHA 再运行相同完整命令并加 `--write-baseline`。writer 自己必须再次确认资格；生成的 JSON/Markdown 一并提交。若仍为 false，不创建 baseline，不放宽阈值，只把未关项写入最终 review/findings。

- [x] **Step 6：验证并提交证据**

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

- [x] **Step 1：同步事实，不复制历史流水**

  把 suite 规模、跨进程命令、报告字段、分类规则、raw 口径、当前最终结果与 baseline 有无同步到入口、架构、运行手册、corpus README、findings 与最终 review。运行手册中的旧规模更新为本轮实测 gate 139 stable / 129 唯一输入；`AGENTS.md` 只保留当前快照和活跃残余，逐批命令与长证据只进 findings/agents-history。

- [x] **Step 2：关闭计划 checklist 并做文档一致性检查**

  只把实际完成的 checkbox 改为 `[x]`；未满足项保留 `[ ]` 并在最终 review 写明阻塞证据。全仓搜索并清理过期的“同进程 repeat 3 足够”、旧 122、raw 幻觉误写为 0 与虚构 baseline 表述。

- [x] **Step 3：跑受影响回归**

  ```powershell
  python -m py_compile test/eval_intent_adversarial.py test/support/intent_adversarial_contract.py test/support/intent_adversarial_runtime.py test/support/intent_adversarial_report.py test/support/intent_adversarial_process.py orchestrator/cloud/planning.py
  python -m pytest test/test_build_intent_adversarial_candidates.py test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_trace.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_report.py test/test_intent_adversarial_process.py test/test_eval_intent_adversarial_cli.py -q
  python test/eval_skills.py
  python test/eval_exemplars.py
  python -m pytest scripts/tests/test_e2e_arch_guard.py -q
  python test/smoke_edge.py
  ```

- [x] **Step 4：跑项目正式后端基线**

  ```powershell
  python -m pytest --import-mode=importlib
  ```

  不用 `pytest test/` 替代根命令；该目录选集的裸 `server` import 冲突仍按独立 P1 记账。
  新鲜结果：**4490 passed / 16 skipped / 0 failed**（收集 4506 项，15m28s）。

- [x] **Step 5：做最终独立 review**

  新 reviewer 从批准设计、最终 review 与 merge-base 开始审查整分支，重点构造：伪造两个 run id、缺 shard、worker 写 baseline、L3 重复、代表样本遮掉 raw 幻觉、provider drift、relation 跨 worker 错配、单次幸运全绿。P0/P1 必须修复并复审；P2 记录但不得与 baseline eligibility 混写。
  最终结论：无 P0/P1；唯一 P2 为“跨文件原子”措辞歧义，已改成逐文件原子替换、第二文件
  失败回滚且不承诺进程硬终止下的跨文件事务。

- [x] **Step 6：提交文档与最终修复**

  ```powershell
  git diff --check
  git status --short
  ```

  提交：`docs: finalize cross-process intent gate evidence`
  已完成：`719844e`；提交前 `git diff --cached --check` 通过。

- [x] **Step 7：推送已授权分支**

  ```powershell
  git push -u origin codex/intent-adversarial-finalization
  ```

  推送后复核本地 HEAD 与远端 branch SHA 一致，最终交付报告列出提交、测试、正式 baseline 状态与仍存在的残余，不把历史证据冒充本轮新证据。
  首次推送已成功建立 `origin/codex/intent-adversarial-finalization` 跟踪分支；本 checklist
  收口提交推送后再做最终 SHA 一致性检查。

## 2026-08-09 执行修订

Task 8 的第一次“完成”判断被独立 reviewer 重开：`e4899c3` 仍允许伪造/重复 worker PID 与
report digest、L3 嵌套 provider/result 身份缺失，以及 embedding model 身份丢失。`c6a7f85`
已逐项 fail-closed；`63485da` 又以 capability contract 驱动的通用 retry 修复 heavy compound
漏动作，未新增 route hint 或领域硬编码。

干净 `63485da` 的两条正式证据必须分账：

- `minimax:MiniMax-M3`（主模型）：139/147、raw hallucination 5/121、unexpected fallback 4、
  critical/stable/unstable = 1/2/5，资格 reasons 非空，**没有写 baseline**；
- `deepseek:deepseek-v4-flash`（对比模型）：资格预跑与正式写入批均 147/147；正式批
  raw/escape/instability 0，2 条 fallback 均已声明 A8，文件自身重算 `eligible=True`，
  已写成对 baseline。

因此 Task 7 的“首份正式 baseline”只对 DeepSeek 对比/参考轨达成；MiniMax 主模型的 P0 产品红灯
仍保留，不影响验证写入机制已经关闸，也不得借 DeepSeek 绿灯注销。

## 2026-08-09 第三次独立复审修订

`63485da` 仍可从多份 L3 候选里挑一份，也没有携带可重算的源 JSON 原始字节；旧时间与宽松
相对路径可协同改写。`63c6a58`、`0e88347`、`f0af9c0` 依次落实唯一候选、Base64 原始字节与
SHA-256、当前时间窗、真实 8 位 runner 后缀和严格四段路径。路径反向矩阵与最终针对性选集通过。

同一 `f0af9c0` 重新取证：MiniMax 141/147、`eligible=False`；DeepSeek 预检与正式 writer 均
147/147、`eligible=True`。第一次正式调用因漏带宿主/worktree 进程变量被 exit 2 拒绝且未碰
正式文件；补齐 `LLM_GATEWAY_ADDR`、两项 embedding timeout 与 `E2E_STACK_ROOT` 后，正式 writer
写入 JSON/Markdown SHA-256
`af7d3c907663b11ddeb846e4a0c67a1a674b0d9ea221f510fdae6b7ada0a2d0c` /
`1525c9939afa3ad2b036d03af7ea1bc408e03c920bb16e566b1bf930a6261d11`，写后资格重算为 true。
