# B4 Capability Pack v1：把「新增一个能力」变成原子动作

> **状态**：**已实施并合入 main**（2026-08-11，提交 `b9eb09e` / `7b68047` / `8ee3ea4` / `c6b14b2`，实施记录见 §6）。源自外部评审采纳批次 B4，裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)。
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

---

## 6. 实施记录（2026-08-11，已合入 main）

**状态：已实施并验收。** 四个提交：`b9eb09e`（§2.1 门禁 + 存量清零 + 进 CI）、
`7b68047`（§2.2 schema 增量）、`8ee3ea4`（§2.2 意图名单派生）、`c6b14b2`（§2.2 骨架生成器
+ §4 判据 1 演练）。本节记录与方案的差异和验证证据。

### 6.1 门禁首跑抓到 22 条真缺陷（不是形式检查）

首跑 61 条，其中 39 条是「`commands.yaml` 从《公版语音指令表》整表导出，范围本来就比 VAL
可执行面大」（云侧域 / 座舱 UI / 媒体别名），逐条进台账。**剩下 22 条全是真缺陷**：

| # | 族 | 条数 | 内容 |
|---|---|---|---|
| 1 | 崩溃 | 1 | `steering_wheel.height.set` 不带值 → `KeyError` 把整条执行抛出去。**同款坑 aircon 风速那处早就修过**（`setdefault`），这个孪生分支漏了；而 `edge_call._missing_required_value` 因为 `attr` 在场提前返回、压根不会拦它 |
| 2 | 自相矛盾的状态 | 4 | `_simulate` 兜底一律写 `state[f"{obj}_{operate}"] = True`，实测 `lane_assistance_open` 与 `lane_assistance_close` **同时为 True**。这种键恒真、永远无法被证否——执行后对账面上是个**恒真的空洞** |
| 3 | 通用话术 | 8 | window/sunroof/sunshade 的 `set`、`power_mode.set`、两个车道辅助开关落 `generic_success`（「好的」）。「开到 50%」和「全开」回执一模一样 |
| 4 | 只实现一半的档位对象 | 9 | `energy_recovery` / `wiper.speed` 有 `set` 没 `inc/dec`；`fragrance` 有开关没档位；`sunshade` 有开关没开度 |
| — | 等价类 | 1 | `accompany_home` 不在 `nlu_objects.yaml`（语料 11 条「伴我回家」全标 `车外灯`），漏收会让影子比对把这一族恒判 differ |

第 1、2 两族值得单独记一笔：它们都不是「还没做」，是**做了一半且看不出来**——
崩溃那条只在不带值时触发，恒真键那条从来不报错。这类缺陷正是逐对象跑一遍才会掉出来的。

### 6.2 与方案的差异（逐条给理由）

| # | 方案原文 | 实际落地 | 理由 |
|---|---|---|---|
| 1 | §2.2「`VEHICLE_INTENTS` 运行时派生自 commands.yaml（**对象×操作**）」 | 改成**声明式派生**：各对象声明 `edge_intents`，`VEHICLE_INTENTS` 取其并 | 先量了差集：机械派生 234 条 vs 手工 76 条（手工独有 38 / 派生独有 196）。意图名承载了 commands.yaml 里没有的四类判断——用哪个对象别名（`hvac`→`aircon`）、哪个 mode/attr 值得单独占名（有 `seat.heating.on` 没有 `seat.recline.on`）、动词用 `on/off` 还是 `open/close`、**这个对象该不该出现在端侧能力面**（整表导出含 weather/flight/hotel）。更硬的一条：机械派生会**复活 2026-08-04 刻意删掉的 `aircon.inc/dec`**——「一个动作只能有一个名字」推不出来。声明式派生保住了方案要的那个结果（手工集合退役、漏写事故面消失），只是名字仍由人写、写在对象定义里 |
| 2 | §2.2「`risk: low\|medium\|high` 新增必填」 | **不落声明字段**，改 `capability_meta.risk_of()` 派生 | ① B1 刚把「危险与否」收敛成 `require_confirm` 这一个权威（契约 §9.15），再手写一份 risk 就是第二份危险声明，两份会漂移；② risk 声明的唯一消费方是 B6，而 B6 条件启动、尚未开工——先落没有消费方的字段就是死字段（§2.3 自己写着这条纪律）。B6 开工时直接调 `risk_of` |
| 3 | §2.2「`aliases` 新增，供 fast_intent 规则与 nlu_objects 骨架生成」 | **本批不加**，附证伪证据 | 拿 `display_name` 给 28 个可达对象各造一句触发探针，只有 20/28 能被端侧规则认回来；8 条未命中里多数是探针造句本身不成立（「打开方向盘」「设置动力模式」）。也就是说硬断言会造假红、软的又不 gate 任何东西——有用的版本其实是「每个对象一条**规范触发例句**」，那是另一件事。骨架生成用 `display_name` 就够 |
| 4 | §2.2「catalog 清点基线改为关系断言」 | **已经是**，无需改 | `test_catalog_budget.py` 用的是 `len(caps) >= 70` 并写着「不钉死数字：新增车控意图是正常演进」 |
| 5 | §4 判据 2「存量 67 对象全部带显式 `require_confirm`/`effect`/`risk`」 | `require_confirm` 本就全有、`effect` 已补全 67 个；`risk` 见差异 2 | — |
| 6 | — | 演练当场加了 `_build_response_key` 的**约定式兜底** | 见 §6.3 |

### 6.3 §4 判据 1 的演练（虚构能力 `rear_wiper`）

只写 `commands.yaml` 一个对象、其余全不做时，红灯全部**具名**：

| 门禁 | 红灯 |
|---|---|
| 能力完整性 · 话术定义 | 2（`rear_wiper.open/close` 落 `generic_success`）|
| 能力完整性 · 等价类 | 1（不在 `nlu_objects.yaml`）|
| 迁移探针 | 1（能力面变了——**这是显式签收点，不是错误**）|
| 等价类台账守卫 | 1（`test_nlu_bridge::test_no_stale_entries`）|
| L0 对抗覆盖 `--strict` | 6（逐 requirement 报 `has 0, need 2`）——**正是除雾那次漏掉的那道** |

逐项照做后全绿，随后完整还原。

**演练当场发现清单还缺一处，就地消掉了它**：新对象光加 `commands.yaml` + 写好 responses，
话术仍落 `generic_success`——因为 `_build_response_key` 还要手写一个分支，而这处同步点
不在任何清单里。修法不是把它写进清单，是**消掉它**：加约定式兜底，按
`<object>_<on|off|操作>_success` 找一次，**找得到才用**（已有专属分支优先，找不到仍落
generic）。判据：**清单上少一项，比清单上多写一行提醒更可靠。**

### 6.4 验证证据

反向验证两头做：**8 条突变**逐条证明在**对应车道**变红且 exit 1——抹 response key /
抹等价类 / 抹 `require_confirm` / 台账写错车道名 / 回退 `steering_wheel` 崩溃修复 /
删 `effect` / `effect` 与 operates 矛盾 / `effect` 取值非法；对照组在每次突变前后都全绿。
另有一条纠正：动作段拼错（`trunk.opne`）**不由** `edge_intents` 那条断言抓（它照样解得出
对象），由「验证定义」车道抓——**一条断言抓什么要以实测为准，不以命名为准**，已写进注释。

回归：edge `558 → 579 passed`（+21，可逐条点名：`test_capability_gaps` 18 +
`test_vehicle_intents_migration` 3）、能力门禁 exit 0、L0 门禁 2/2 exit 0（无管道读码）、
端侧 smoke 13/13、`eval_fast_intent` 57/57 无回归、范例门禁域错配 2.5%。

### 6.5 台账里三条**标为待确认**的（不装作已裁定）

1. **`frunk`（前备箱）**：`require_confirm=true` 的危险对象，却没有任何端侧 intent——
   是刻意不给语音开，还是漏了？与 `trunk`（后备箱，有 intent）不对称。
2. **`driving_mode` 与 `power_mode`**：语义高度重叠，可能是同一件事的两个对象名。
   若确认重复应合并——别让 planner 面对两个分不开的工具（同 `aircon.inc/dec` 那一课）。
3. **`battery`**：电量查询目前走云侧 `vehicle_state` 上下文而非端侧 intent，要不要补一个
   端侧查询意图未定。

另有 14 个对象在台账里**明确标注「是欠账，只是本批不做」**（`air_purifier` / `auto_hold` /
`bluetooth` / `epb` / `equalizer` / `hotspot` / `key_tone` / `low_beam` / `navi_broadcast` /
`surround_view` / `wifi` / `driving_mode` / `battery` / `frunk`）：它们**是**真车控对象、
VAL 侧多数已有分支或话术，只是端侧没有 fast_intent 规则与意图名，云端计划仍可经
`action_to_structured` 走到。新增端侧意图时应从这一组里挑，并把对应条目从台账删掉。

### 6.6 顺带记档的两个发现

- **`LOCAL_INTENTS` 与 `VEHICLE_INTENTS` 是两个不同的问题**，不要合并：前者是**路由**
  （这句归端侧还是上云），后者是**能力目录**（云侧 planner 看得见哪些车控工具）。
  实测 `LOCAL_INTENTS` 164 条里有 **87 条不在能力目录里**——它们端侧接得住，但 planner
  规划不到。这与门禁台账里那 14 条「是欠账」高度重合，是同一件事的两个视角。
- **规则侧对「胎压」吐的对象名是 `tire_pressure`，VAL 对象叫 `tire_pressure_monitoring`**
  ——`nlu_objects.yaml` 记的「三套命名」在这里又出现一次。
