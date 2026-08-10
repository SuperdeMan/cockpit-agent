# B4 Capability Pack v1：把「新增一个能力」变成原子动作

> **状态**：已批准待实施（源自外部评审采纳批次 B4，裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)）
> **交付对象**：后续实施者
> **关联**：`orchestrator/edge/knowledge/*.yaml`、`orchestrator/edge/fast_intent.py`、
> `orchestrator/edge/val.py`、`test/eval_corpus/intent_adversarial/`、CI
> **时机**：近期；§2.1 完整性检查可先行独立交付（B2 合入后接进同一 CI job）

---

## 0. 一段话给接手者

「新增一个车控能力」当前不是一个原子动作，而是「同时记得改十来个位置」。这不是猜测——
除雾能力落地（`db6c963` + `cc87056`）的 stat 就是清单：commands.yaml(+45)、
responses.yaml(+32)、nlu_objects.yaml(+7)、fast_intent.py(+25)、val.py(+8)、
edge_call.py(+3)、vehicle.py(+7)、catalog 清点基线(+13)、对抗覆盖(+123，**第一次漏了**，
`--strict` exit 2 被管道吞成假绿)、conventions.md。本批的解法**不是**评审建议的「另立
`capabilities/*.yaml` 新格式」，而是：① 先做 CI 能力完整性检查（把「漏一处」变红灯，
独立交付）；② 把既有权威源 `commands.yaml` 的对象定义**扩成单一声明源**（补 effect/risk/
coverage 字段），能派生的派生、不能派生的用检查兜住。理由：仓库已有「capability 描述由
VAL 知识库机械生成」的管道（CLAUDE.md §3），收敛到既有源比再加一个源少一次全量迁移。

## 1. 现状与证据

### 1.1 新增能力的真实同步面（除雾实测，2026-08-10）

| # | 位置 | 内容 | 性质 |
|---|---|---|---|
| 1 | `orchestrator/edge/knowledge/commands.yaml` | 对象+操作+require_confirm | **权威源**（VAL 执行依据；capability 描述由此机械生成） |
| 2 | `orchestrator/edge/knowledge/responses.yaml` | 话术 key | 人工，漏写回落 generic |
| 3 | `orchestrator/edge/knowledge/nlu_objects.yaml` | 对象等价类台账 | 人工裁定 |
| 4 | `orchestrator/edge/fast_intent.py` | 触发规则 + `VEHICLE_INTENTS` 手工集合 | 人工，**漏写=能力不可达** |
| 5 | `orchestrator/edge/val.py` / `edge_call.py` / `edge_agents_mod/vehicle.py` | 模拟/桥接白名单 | 人工小改 |
| 6 | `orchestrator/cloud/tests/test_catalog_budget.py` | catalog 清点基线数字 | 人工对数 |
| 7 | `test/eval_corpus/intent_adversarial/cases/` | 对抗覆盖（正 2/硬负 2/对照 1 每 intent） | 人工，**除雾第一次就漏了这处** |
| 8 | `docs/conventions.md` 等 | 文档 | 人工 |

### 1.2 已有的机械化（别重复建）

- 端侧 capability 描述 ← commands.yaml 机械生成（CLAUDE.md §3 成文）；
- 对抗覆盖矩阵检查已在 `--strict` 档阻断（B2 把它接进 CI 后，第 7 处的遗漏已有红灯）；
- 覆盖豁免有判据化台账（`coverage_exemptions.yaml`，豁免判据=「没得测」非「懒得写」）。

### 1.3 必须吸收的既有判据

**「能力从哪里声明」和「能力写在哪个文件」是两件事**（AGENTS.md §4.3；shop 域零范例
事故根因——门禁只读 manifest，而 mcp-bridge 能力由 `servers.yaml` 启动期合成，发生过两次）。
⇒ 完整性检查的能力清单必须从**全部声明源联合**取（manifest + VAL 知识库 + servers.yaml
合成面），不许只读单一文件。

## 2. 方案

### 2.1 第一步（先行）：CI 能力完整性检查 `test/eval_capability_integrity.py`

对**联合能力清单**（§1.3）逐条断言，任一缺失即红：

| 检查 | 断言 | 数据源 |
|---|---|---|
| 执行定义 | 端侧车控 intent ↔ commands.yaml 对象/操作 双向一致（`VEHICLE_INTENTS` 无孤儿、commands 对象无不可达） | fast_intent × commands.yaml |
| 话术定义 | 对象×操作的 response key 存在（或显式登记「共用 generic」） | commands.yaml × responses.yaml |
| 等价类 | 对象在 nlu_objects.yaml 有行（或登记待裁定——**待裁定是状态不是缺失**，与 `unmapped` 语义对齐） | commands.yaml × nlu_objects.yaml |
| 风险定义 | 对象有 `require_confirm` 显式值（现状缺省 False 的隐式约定升级为显式声明） | commands.yaml |
| 对抗覆盖 | 复用 `--strict` 矩阵，不重复实现——本检查只确认矩阵入口被执行 | B2 门禁 |
| 验证定义 | 对象有 state 模拟键（Outcome Verifier 可对账）或显式豁免 | commands.yaml × val 模拟表 |

落点：与 skills/exemplar 门禁同 CI job、同为 blocking（确定性检查准入）。预计首跑会揪出
存量欠账（隐式 require_confirm、缺 response key 的对象）——**首批红灯照单补齐或显式豁免，
不放宽检查**（案例集是尺子的同款纪律）。

### 2.2 第二步：commands.yaml 对象定义扩为单一声明源

对象 schema 增量（示意，实施时按现有 YAML 结构融合）：

```yaml
front_defogger:
  display_name: 前挡除雾
  operations: [open, close]
  require_confirm: false          # 已有，升级为必填
  effect: write                   # 新增：read | write（B1/B6 消费；查询类对象标 read）
  risk: low                       # 新增：low | medium | high（require_confirm=true ⇒ ≥high）
  aliases: [前挡除雾, 前风挡除霜]  # 新增：供 fast_intent 规则与 nlu_objects 骨架生成
  coverage:
    required_families: [canonical, paraphrase, negation, object_flip]   # 缺省即此，可省略
```

派生与检查的分工（**能运行时派生的不落盘生成**，避免生成物漂移）：

| 资产 | 处置 |
|---|---|
| `VEHICLE_INTENTS` 意图名单 | **运行时派生**自 commands.yaml（对象×操作），fast_intent.py 手工集合退役——名单漏写这一事故面直接消失 |
| 触发规则（正则/词表） | 仍人工写（规则质量不可生成）；完整性检查兜「active intent 无触发规则」 |
| nlu_objects / coverage 骨架 | 生成器产**骨架文件片段**（`scripts/gen_capability_skeleton.py <object>`），人工填内容后提交——生成的是待办清单不是成品 |
| catalog 清点基线 | 检查改为「派生数与声明数一致」的关系断言，替代手工对数字 |
| Registry manifest（云侧 Agent 能力） | 不动——Agent 能力已由 manifest 声明式机制覆盖（R2.1），本批只收敛**端侧车控**声明面 |

### 2.3 明确的边界

- **不另立 `capabilities/*.yaml` 顶层格式**（评审原建议）：多一个源=多一类漂移；
  commands.yaml 已是 VAL 执行与描述生成的权威，扩它即可。
- **不做** `input_schema`/`output_schema`/`idempotency`/`compensation` 等完整 Contract v2
  字段——消费方（typed Executor/Verifier readback/补偿流程）都还不存在，先声明就是死字段；
  `effect`/`risk` 两个字段例外，因为 B1（CLOUD-DEGRADED 只兜 write 之外的判定升级）与
  B6（ActionabilityClassifier 特征）有明确消费点。触发条件记入 §4。
- **媒体/云侧 Agent 能力不在本批**——端侧车控是同步面最碎、事故刚发生的地方，先收敛它。

## 3. 实施步骤

| 步骤 | 内容 | 自检 |
|---|---|---|
| 1 | 完整性检查脚本 + 存量欠账清零（补齐或显式豁免） | 脚本本地绿；全量 pytest 基线不减 |
| 2 | 接进 CI（与 skills 门禁并列 blocking） | CI 绿 + 红灯验证（临时抹掉一个对象的 response key → 红） |
| 3 | commands.yaml schema 增量（effect/risk/aliases 必填化）+ 迁移存量 67 对象 | 检查步骤 1 的断言升级后仍绿 |
| 4 | `VEHICLE_INTENTS` 运行时派生 + fast_intent 手工集合退役 | 端侧 smoke 13/13；`eval_fast_intent.py` 基线不降；L0 discovery/gate strict exit 0 |
| 5 | 骨架生成器 `scripts/gen_capability_skeleton.py` | 用一个假想新对象走通「生成→填空→全绿」流程并记入 dev-guide |
| 6 | 文档：CLAUDE.md §3 knowledge 段、conventions.md、新增能力 SOP 更新 | — |

步骤 1-2 可独立先交付（依赖 B2 的 CI job 存在）；3-5 是第二步主体；每步独立提交可回退。

## 4. 验收判据与触发条件

1. **终局验收**：演练「新增一个虚构能力」——只写 commands.yaml 一个对象 + 触发规则 +
   按骨架填空，全部门禁绿、能力端到端可达（smoke + L0）；中途任何遗漏都有具名红灯。
2. 存量 67 对象全部带显式 `require_confirm`/`effect`/`risk`；catalog 清点从对数字变为
   关系断言。
3. Contract v2 余项触发条件（记档不排期）：typed slots 需求出现（Planner 结构化槽位校验
   反复出笼统 bug）时上 `input_schema`；补偿/撤销类产品需求出现时上 `compensation`。

## 5. 风险

| 风险 | 处置 |
|---|---|
| `VEHICLE_INTENTS` 派生改变 fast_intent 行为 | 派生结果与现手工集合做一次性 diff 断言（迁移测试），差集必须人工逐条裁定后才切换 |
| 存量欠账清零工作量超预期 | 完整性检查支持 `# integrity-exempt: <reason>` 行内豁免，首批可带豁免合入，豁免清单进 §4.2 式台账逐步清 |
| 与对抗覆盖矩阵重复实现漂移 | §2.1 明确复用不重写；唯一入口仍是 B2 的 gate 脚本 |
