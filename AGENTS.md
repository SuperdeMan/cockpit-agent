# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化 PoC**。主干与云端中枢、R2-R4 硬化主题、可观测台（badcase 排查贯通）、
旅程级验证体系（L3 journeys + L4 HMI CDP）、智能化升级 M0a→M4（S2S 双语音链路 / 声纹多用户 /
统一主动引擎 / 受控 MCP 桥 / Task Ledger + Outcome Verifier / Skill 层，已过跨阶段组合总体验收）
与 M5 数据飞轮（P0→P3a + P3 收尾）均已落地；首批真实外部能力在线（高德 / 和风 / Exa / Tushare /
api-football，无凭证回退 mock）。当前全量测试基线与批次证据见 §4.0 快照；逐批历史流水在
[`docs/agents-history.md`](docs/agents-history.md)。

---

## 2. 项目地图（先看文档，再看代码）

| 想了解 | 看这里 |
|---|---|
| 为什么这么设计（全局）| `docs/architecture/cockpit-agent-architecture.md` |
| 接下来分几步做、怎么验收 | `docs/architecture/phase1-implementation-plan.md` |
| 核心模块怎么编码 | `docs/architecture/detailed/ws{3,4,6,8}-*.md` |
| **怎么接真实 provider（高德/和风样板）** | `docs/guides/provider-integration.md` |
| **怎么扩 info 能力 / 加新独立 Agent 并打通** | `docs/design/2026-06-20-info-agent-expansion.md`、`docs/design/2026-06-20-standalone-agents-roadmap.md` |
| 前瞻设计 / 问题分析（多意图、ASR、车控、云端中枢、可观测）| `docs/design/` |
| 工程规则与铁律 | `CLAUDE.md` |
| 怎么搭环境、codegen、单服务调试 | `docs/dev-guide.md` |
| intent/scope/端口/错误码/env 速查 | `docs/conventions.md` |
| 怎么验证 | `test/README.md` |
| 历史批次流水（只进不出，查证据用） | `docs/agents-history.md` |

代码目录职责见 `CLAUDE.md` §3；每个服务子目录都有自己的 README。

---

## 3. 铁律（违反即视为 bug，详见 CLAUDE.md §5）

### 唯一运行环境

- 根目录 `.env` 是唯一的运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 全栈只允许用 `make up` 或 `docker compose -f compose.yaml ...` 启动；根 `compose.yaml` 显式加载根 `.env`，并以 `deploy/` 为 included Compose 的项目目录以保持构建路径不变。
- 不得直接以 `deploy/docker-compose.yaml` 为首个 Compose 文件启动，否则真实 Provider 可能静默回退 mock。

1. **车控只经 VAL**。任何组件（含 LLM/Agent）不得直接碰 CAN/SOME-IP。
2. **LLM 不直连车控**：LLM 只产"意图/计划"，车控由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
3. **危险动作二次确认**（`require_confirm=true`）。
4. **不改编排核心来加 Agent**：Agent 经注册中心被发现，新增 Agent 不动 orchestrator。
5. **密钥/token 不进代码、不进 commit、不进日志**；用 `.env`（已 gitignore）。
6. **改 proto 先改 `proto/` 再 codegen**，不要手改生成代码。

---

## 4. ⚠️ 当前真实状态（别假设没验证的东西能跑）

### 4.0 当前快照（2026-08-10）

意图落域对抗测试按这个顺序接手：运行手册
[`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md) → 最终验收
[`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`](docs/reviews/2026-08-04-review-intent-adversarial-finalization.md)
→ 逐批证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§24**。
历史流水只查 [`docs/agents-history.md`](docs/agents-history.md)，不要再抄回本文件。

**最新后端全量基线**：`python -m pytest --import-mode=importlib`
**4643 passed / 14 skipped / 0 failed**（单进程 15m13s，2026-08-10 深夜 B1/B2 后实测，
退出码 0）。较 B1 前净 **+36**，且这 36 条可**逐条点名**：`test_val_confirm_gate` 18 +
`test_cloud_degraded_fallback` 9 + `test_loop` T2 三场景及对照 4 + 对抗 runtime 突变探针 5。

> ⚠ **上一份基线记的 4601 本身就旧了 6 条**：临时 worktree 在 `612abc7` 上干净量得
> **4621 collected**（= 4607 passed / 14 skipped），而 4601 是同日更早一次实测，之后
> `cc87056`（补除雾对抗覆盖）等提交又加了测试却没刷新这一行。**净增量要跟同一个 SHA
> 比，不能跟文档里那个数比**——差额不查清楚就会被当成「我这批多出来 6 条不明用例」。
2026-08-10 深夜分组实测（B1/B2 后）：`orchestrator/` **1215 passed**（其中 edge 558 /
cloud 655）、`pytest test/` **1144 passed / 9 skipped**（该目录选集不再有红，但它仍不是
项目基线命令——分母不同）、端侧 smoke **13/13**、L0 门禁 `python scripts/check_intent_gate.py`
**2/2 exit 0**（discovery 81/81、gate strict 25/25）、范例门禁域错配 **4/160=2.5%**、
skills 门禁 PASS。HMI `node --test` **225/225**、Dashboard vitest **17/17** 为 2026-08-09
实测，Go 网关数字沿用 2026-08-04 批次；2026-08-10 全天没有改前端或 Go
（`.env.example`/compose 只加了 `PLANNER_TOOLCALL_SALVAGE_RETRY` 一项，
契约见 [`docs/conventions.md`](docs/conventions.md) env 表与 §8.1）。
**CI 深夜动过一次**：B2 在 `intent-eval-baseline` job 加了 L0 strict 阻断步骤
（`scripts/check_intent_gate.py`），是本仓库第三条 blocking 门禁。

| 意图落域证据 | 当前可引用事实 |
|---|---|
| L0 discovery | **81/81**，574 条 / **535 唯一输入**（2026-08-10 深夜补 `cloud_degraded` 族 +5 条；bounds [450,540]——⚠ **距上界只剩 5**） |
| gate 规模 | **139 stable / 129 唯一输入**，L0 strict **25/25，exit 0** |
| 对比模型正式 baseline | [`baseline_intent_adversarial.json`](docs/reviews/eval/baseline_intent_adversarial.json)；干净 `f0af9c0`，锁定 `deepseek:deepseek-v4-flash`，由当前 L3 原始字节/摘要/时间/精确路径契约重新取证并写入。**未随 `32e8718` 重取**——它仍是 DeepSeek 在 `f0af9c0` 的证据 |
| DeepSeek 完整 gate | **147/147**：L0 25、L1 117、L2 4、L3 1；exact **121/121**，raw 幻觉/校验后逃逸/不稳定均 **0/121**；L1/L2 各 **2 个独立进程 × 每进程 3 样本**（`f0af9c0`） |
| MiniMax 主模型 gate（`32e8718`，2026-08-10） | **141/147**；exact **116/121**、required **99/103**；raw 幻觉 **3/121**（原 8）、逃逸 **0/121**；不稳定 **4/121**（原 6）；`pass 141 / unstable 4 / stable_fail 2`，资格仍 `eligible=False` |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline 的 invocation 新鲜、exit 0，只证明该授权 case/claim。2026-08-10 新增 **A1-5**（weather→去处推荐，claim `adaptive_replan_continuity`）两趟独立各 **1/1**，但它服务的 case 仍是 `reviewed`，不进 gate 选集 |
| 三条候选取证（2026-08-10，`f71fd3c`） | `cp.hvac-news.swapped` **2/6**、`nq.hvac.reported` **2/6**（`critical_fail` ×2，真缺陷）、`nq.match.lastweek` **5/6**；`cp.adaptive.weather-outing` L1 三趟 **9/11**。全部 `minimax:MiniMax-M3`、检索零降级、`worktree_clean=true` |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 fallback **0**；MiniMax **11/122**，其中未声明 **2**（原 4） |
| 工具通道（协议层，**可跨 provider 比**） | 走成 `toolcall` 的比例是 **provider 属性**：`minimax:MiniMax-M3` 同用例 **13/27（48%）**、跨域 20 条 **9/20（45%）**；`deepseek:deepseek-v4-flash` 两组 **35/35（100%）**（p≈0.0002）。⚠ 代价只在**需要模型自己填结构化字段**的多阶段计划上兑现——那 20 条 stable 上两档通过率都是 20/20（findings §24）。**2026-08-10 起 `PLANNER_TOOLCALL_SALVAGE_RETRY=on` 默认开**：gate L1 双臂实测把 MiniMax 从 **51.3%（60/117）抬到 85.5%（100/117）**，+34.2pp、p=2.3e-08、重试成功率 ≈70%，代价墙钟 +38.5%（findings §26.5）。**引用 45~48% 那组数时注意它是 off 档口径** |
| 代码回归 | `orchestrator/` **1215 passed**、`pytest test/` **1144 passed / 9 skipped**；端侧 smoke **13/13**；Skill / Exemplar（**254 条 / 20 域**——clarify 型机制已就位但**刻意零生产范例**，见 §4.2）/ L0 门禁均通过。端侧能力面 **80 条**（vehicle 76 + media 4）/ VAL 车控对象 **67** |

⚠ **MiniMax 三批 141/141/140 都不是同一批红灯**：`f0af9c0` 点名的 6 条 unstable 在
`32e8718` 全部转绿，红灯换成另一批边界单元（其中 5 条单跑 10 样本全绿）；`5e8247d`
换池后又换一批（换池随后已回退）。按实测单元不稳定率 3~5%，一趟完整 gate 恰好零 unstable
的概率是个位数百分比——**`eligible=True` 不是「把点名的几条修好」能达成的，是要压整体
底噪**（findings §17.6/§19.2）。
⚠ 上表 MiniMax 行是 `32e8718` 读数，**与当前代码已差好几批**：`skills/exemplars/`
多了 `sunroof.yaml` 与 `chitchat#12`，planner 多了 adaptive replan 自查与
`complexity_declared` 留痕。`5e8247d` 那批是换池态、案例集已不一致，**不要挪用**。
**当前 SHA 没有对应的全量 gate 读数**——要引用主模型总分就得重跑一次完整父 bundle。

首份正式 baseline 已存在，但它是 **DeepSeek 对比/参考模型**在固定 provider、资产与代码快照下的
意图理解与落域证据；不证明 MiniMax 主模型、Agent 业务结果、外部 Provider 内容或跨模型平均质量。
MiniMax 本轮即使 process/provider/embedding/L3 身份完整，仍因 `gate_failures`、raw 幻觉、未声明
fallback 与 `unstable_results` 被资格闸拒绝。后续写入仍必须由一次新的完整父报告
明确 `eligible=True`，不得手工改正式文件。

### 4.1 活跃待办（只列仍需行动的）

**外部评审 B1/B2 已全部实施合入**（2026-08-10 深夜，五个提交，裁决总览
[`docs/reviews/2026-08-10-external-review-adoption.md`](docs/reviews/2026-08-10-external-review-adoption.md)）。
两个 P0 已封死：确认权威下沉 VAL（`confirmed` fail-closed，契约
[`docs/conventions.md`](docs/conventions.md) §9.15）+ 云端降级兜底三道挡板；T2
`elif streamed` 不可达致的重跑已修。L0 门禁进 CI 作阻断，唯一入口
`scripts/check_intent_gate.py`。实施记录见两份方案文档文末与 history §25。
✅ **冻结令已撤销**——可以新增业务 Agent 了（B1 验收判据第 6 条兑现）。

| 待办 | 状态 | 权威入口 |
|---|---|---|
| B2 剩余：**main 轻档分支保护**（禁 force-push / 禁删除） | **待泓舟本人操作**——本机无 `gh`、无 `GITHUB_TOKEN`，AI 侧调不了 API；网页两个勾即可 | [B2 方案 §6.1](docs/design/2026-08-10-b2-gate-ci-branch-governance.md) |

⚠ **对抗语料唯一输入 535 / 上界 540**——只剩 5 个名额。下次加 L0 语料前先评估要不要抬
`suites.yaml` 的 `max_cases`，别撞上再回头改（判据：bounds 是规模守卫，抬它要说明为什么
这批语料值得占额度）。

> 2026-08-10 全天批次的**完整记录在** [`docs/agents-history.md`](docs/agents-history.md)
> **§15–§25**，逐条证据在 `docs/design/2026-08-02-intent-routing-adversarial-findings.md`
> **§17–§26**。本节按约定**只留状态、不留流水**——沉淀下来的判据在 §4.3，数字在 §4.0。

### 4.2 延后 / 条件待办索引（不进入当前主线）

> §4.1 只放正在行动的事项；本表保留尚未启动、等待外部条件或明确后置的入口。条件满足后先晋级 §4.1 并补完成判据；历史流水仍只查 `docs/agents-history.md`。

| 主题 | 当前状态 / 启动条件 | 权威入口 |
|---|---|---|
| **P3b operate 抽取与放量** | 原表里并列的两个具体缺口（除雾能力缺席、「穿衣指数→股指」）已于 2026-08-10 修完，详见 history §23.1/§23.2，本行只留放量条件。**放量门槛不变**：operate 抽取 + 真实错对象率 <0.3%。⚠ 压这个数的手段是 **R4.1b P1 执行侧对象化**（让 VAL object 数从当前 67 继续长），**不是调阈值**；且当前 PoC 没有真实流量，这个数只有观测面、还没有分母 | [M5 P3 收尾](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) §P3 收尾 |
| live 路由回归进 CI | hint 退役后的召回保护目前是 live 人工车道，不是 CI 阻断；有稳定凭证、预算与 provider 方差处置后再接 CI | [旅程体系](docs/design/2026-07-14-journey-e2e-test-system.md) §4.3、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| `route_hints` 继续退役 | 当前实数 **11**；`mcp-bridge#0` 必须先过专项安全回归。旧三条单档候选不得按历史索引直接执行；当前只用 MiniMax 主模型 / DeepSeek 对比，MiMo key 失效不阻塞主线，切换 provider 时重新全覆盖取交集 | [M5 P2](docs/design/2026-07-28-intent-accuracy-data-flywheel.md)、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| M5 后续杠杆 | catalog 检索化当前是“有意不做”；16k 预算再次裁剪或保护集显著变瘦时重评。gold→范例现走 CLI；P4 仅在范例 ≥2k 且 N1 平台期 ≥2 周时启动 | [M5 P2/P4](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) |
| M-B / M-C / M-D 明确后置项 | 13 张验收主卡已清零；跨域删除 saga、完整隐私管理/迁移仪式、持久治理扩面与 MCP 生产化覆盖按 GDPR 完备性、量产迁移或新消费方触发 | [总体验收](docs/reviews/2026-07-26-acceptance-review-m0a-m4.md) §10.2/§11.2/§12.2、[OwnerKey 契约](docs/conventions.md) §9.13 |
| M2 / M3 产品化边界 | Ledger 自动续跑/任务中心，以及主动治理持久化、偏好学习、dashboard、远距 geocode 与真实商户均为显式未做；出现真实消费方或产品阶段后另立卡 | [M2 RFC](docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md) §9.6、[M3 RFC](docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md) §10.7 |
| M4 声纹 / 视觉余项 | 真麦校准、语音注册入口、模板漂移治理、视觉多轮与 `vp_*` 指标消费仍未收口；进入真机量产验收或 v2 前启动 | [M4 RFC](docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md) §11.5/§11.8、[声纹契约](docs/conventions.md) §9.11 |
| 裸对象澄清族（**已知无解状态，不是待办**） | **三条路径已全部走完，该族目前无可用修法。** `nq.landmark.bare` 合并 11/20≈55% 的高方差边界句；`nq.landmark.explicit` 自己每条断言都过、被 relation `clarify_flip` 连累；同族第三条 `nq.city.bare`「上海」reviewed 未进池。路径 1「写 guide」实测**有害**（§18：4/10→1/10→退回 7/10，p≈0.02）；路径 3「换出预选池」执行并全量验证后由泓舟裁定回退（§19.5）；**路径 2「范例 schema 加 clarify 型」2026-08-10 已实现并合入**（schema+渲染+门禁+契约+11 条测试），但同批实测出它**治不了本族**：裸专名之间 IDF-Dice 全 0.000（检索是内容通道，而裸对象澄清是**形态判据**——这也解释了路径 1 为何有害），且澄清型范例天然与其「补全版」近重复（两条候选一条抢到明确请求 top-1、一条只靠 0.03 差距，全部撤回，**故仓库刻意没有 clarify.yaml**）。**下一次要动它得换判定形态（形态/句法特征），不是再加一层检索式知识** | 立卡整段写在语料 `test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml` 的 `nq.landmark.bare` 头上；[findings §18/§19/**§25**](docs/design/2026-08-02-intent-routing-adversarial-findings.md)；机制契约 [`skills/exemplars/README.md`](skills/exemplars/README.md) §clarify 型 |
| B3-2「广州塔」地标解析（高德侧） | **不进落域账**。2026-08-10 同坐标直连复测：geocode 对（兴趣点／广州塔真坐标）、`near=None` 重搜 top1 就是广州塔，只有带深圳偏置的关键词搜索会顶出「广州仄仄科技有限公司」2.4km。即 R1 去偏置重搜机制本身是对的，但它**要多打一次高德**，跑批并发下正是最易被限流的那一次；掉一次退回就近弱匹配，掉两次成「暂时无法确定」——两次红的两种形态由此解释。归高德 QPS 一族，出现真实用户投诉或做并发治理时再启动 | [复杂意图·地标/停车](docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md)、判据与复测记在 `test/journeys/target_b.yaml` B3-2 注释 |
| **外部评审采纳批次 B3/B4**（DEPLOY_PROFILE 生产配置档 / Capability Pack v1） | **前置条件已满足**（B1 于 2026-08-10 完成），B3 可随时启动——其 prod 档校验项要引用 B1 落地的开关，现在引用得到了；B4 的 CI 完整性检查可先行、单一声明源随后。均已出完整方案，接手直接读方案文档 | [B3 方案](docs/design/2026-08-10-b3-deploy-profile-fail-closed.md)、[B4 方案](docs/design/2026-08-10-b4-capability-pack.md)、[裁决总览](docs/reviews/2026-08-10-external-review-adoption.md) |
| **外部评审采纳批次 B5/B6**（Planner 重试表驱动+流式统一 / ActionabilityClassifier shadow） | **仍条件启动**（B1 只解除了 B5 的代码依赖，没触发它的启动条件），条件写死在各自方案 §1：B5=下次加重试规则或新流式路径前必做（此前 planning.py 冻结新增 `elif` 守卫）；B6=真实流量分母就位或裸对象澄清族再成主要矛盾。⚠ B1 给 B5 留了一笔账：`_outcome_uncertain` 目前只有标记 + 话术、**没有 readback 对账**，接 Outcome Verifier 归 B5 统一组件时做。B6 与本表「裸对象澄清族已知无解」条目呼应——它就是那条「换判定形态」的预留出口 | [B5 方案](docs/design/2026-08-10-b5-planner-retry-stream-refactor.md)、[B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) |

### 4.3 读数纪律

- `110/116`、`113/117`、raw `6/117`、旧 seen/unseen 对比均是历史口径/批次，**不得当当前结果**。
- `domain_hit_rate` 只要求命中交集；`exact_plan_set_rate` 要求必要组齐全、禁选为空、无额外项，二者不可直比。
- L1/L2 的正式 gate 必须是两个独立进程、每进程 3 样本；同进程 repeat 3 不能替代第二进程。
- baseline 的 `repeat_coverage=121/122` 是 L1/L2 121 个 live 单元重复、L3 按设计只跑一次；`process_policy_complete=true`，不是缺 shard。
- `fallback_plan_rate` 非零不自动失败；只有语料显式声明的 A8 能力缺席族可接受，未声明 fallback 一条即挡 baseline。
- Live 跑批前按运行手册 §2 同时设置 provider、model 与 embedding 三项；少一项整批证据作废。
- `pytest test/` 不是项目基线命令；换选集会改变 import 顺序与分母，不能拿来替代根跑。
  （2026-08-10 起它**不再有红**，但「不是基线」的理由是分母不同，与红不红无关。）
- **live 跑批期间不许动工作树**：资格闸逐进程校 `worktree_clean`，corroboration 进程比
  primary 晚起，跑批中途改文件会让它单独 fail-closed（本轮踩了两次）。评测产物写
  `docs/reviews/eval/_ci-run-*` 是安全的——那个前缀已 gitignore。
- 两次 141/147 不代表同一批红灯；读 MiniMax 报告先看**是哪几条**，再看总分。
- **不要再为「让 MiniMax 主模型出正式 baseline」发起跑批**（泓舟 2026-08-10 已裁定不追求）。
  DeepSeek 在 `f0af9c0` 的证据已满足 baseline 的用途；**资格闸拒绝带病读数正是它的设计
  目的——它在正确工作，不该为了拿绿灯去松它**。实测单元不稳定率 3~5% ⇒ 一趟零 unstable
  概率个位数百分比，继续跑批期望值极低；更不要以此为由动 gate 案例集或资格判据。
- **主模型是 `minimax:MiniMax-M3`，这是拍过板的**（泓舟 2026-08-10）：换档会牵动全部
  既有 baseline 与读数，且「通过率跨 provider 不可直比」意味着 DeepSeek 的 147/147
  **不构成「它更准」的证据**。DeepSeek 只作对比/参考轨。
- **加了知识就要拿对照跑证伪，不能只看它有没有被注入。** 2026-08-10 实测：一条 guide
  四次全部成功注入（`@lex:11` 未被裁），通过率却 4/10 → 1/10，退回后回到 7/10。
  「知识在场」和「知识有用」是两回事（findings §18.2）。
- **relation 断言会把 base 的方差放大成 variant 的稳定红**：`nq.landmark.explicit`
  自己每条断言都过却 0/10，只因 `clarify_flip` 要求 base 稳定。读 `stable_fail` 条数时
  先减掉这种连累项（§18.4）。
- **不要为了某个模型的问题去改动 gate 案例集**（泓舟 2026-08-10）：案例集是**尺子**，
  描述「我们要求系统做到什么」；某个 provider 当下做不到是被测对象的读数，不是尺子该
  让步的理由。与规格 §22.7「换出带病候选」不矛盾——那条针对**被测对象错了**，
  而「用例难、模型弱」正是 §22.7 要求留在门禁里的一类（§19.5）。
- **动 gate 案例集之前先问正式 baseline 还认不认得这个案例集**：案例集一变，
  每份报告都会带 `removed_cases` 拒绝原因（与模型无关），baseline 写入机制被冻结。
  离线比对 baseline 与语料的 id 集合即可零成本预判（§19.3/§19.5）。
- **「没到 6/6」是统计事实，不是定性**（2026-08-10）：三条候选在晋级闸前长得一模一样，
  逐条拉证据后是三种东西——一条要修生产（`nq.hvac.reported`）、一条要维持尺子的严格
  （`cp.hvac-news.swapped`，`clause_commute` 抓到 `limit="今天的"`）、一条什么都不用做
  （`nq.match.lastweek` 边界方差）。分歧可以出现在**同一个失败标签内部**（findings §20.1）。
- **claim 不能就近转借**：`dependency_continuity`（两个已规划的步骤之间结果被消费）
  证不了 `adaptive_replan_continuity`（第二步首轮压根不存在、看到结果才补出来）——
  首轮排满两步的计划照样满足前者，却正是 weather-outing badcase 要禁的形态（§20.4）。
- **跑 pytest 不要带 `PYTHONIOENCODING=utf-8`**（Windows 上会把拉子进程的用例弄成假红）
  ——动作型条目，展开与自查位置在运行手册 §11（findings §20.6）。
- **读任何落域数之前先看 `plan_modes`**（2026-08-10）：掉出 `toolcall` 的那些轮，模型是在
  自由文本里作答，输出分布与走 schema 的轮**本就不同**（MiniMax 内部实测 91% vs 50%，
  p≈0.036）。摘要行会打 `[!] N/M 轮没走成 toolcall`。⚠ `<通道>_no_action` 的后缀说的是
  **判断**不是掉档，`toolcall_no_action` 算走成了（§24.4）；而 `_kept` 后缀说的是**通道**
  （重试也没走成，用了抢救那份），**算掉档**。新增 plan_mode 值时先问它描述的是哪个。
- **门禁在两种模式下严厉程度不同**（2026-08-10）：普通 `--list` 只**展示** coverage gap，
  `--strict` 才把它升级成**阻断**。报绿之前先确认自己跑的是哪种模式。
  配套的老账：**管道会吞退出码**——`cmd | tail; echo $?` 拿到的是 `tail` 的，
  要读退出码就别接管道（本项目记过 `${PIPESTATUS[0]}`，2026-08-10 又踩一次，
  结果是把一个 exit 2 报成了 exit 0，见 findings §26.4）。
- **加 active intent 就要补对抗覆盖**（每 intent 正例 2 / 硬负例 2 / 对照 1）。
  想登记 `coverage_exemptions.yaml` 之前先过判据：那张表豁免的是「**不经云侧 LLM 落域、
  没有可测对象**」的端侧原子车控——**豁免的判据是「没得测」，不是「懒得写」**。
  新 case 标 `reviewed` 即可补上覆盖矩阵（`_AUTHORITATIVE={reviewed,stable}`）
  而**不进 gate 池**，于是不动案例集、不碰 baseline 比对面（§26.4）。
- **协议层指标要跨 provider 量，语义层指标不要**（2026-08-10）：同一份 `plan_modes`
  换档就从 45% 跳到 100%，这个对比有意义；同一份通过率换档就不可直比。
  **一条指标该不该跨档比，取决于它测的是被测系统还是被测模型**（§24.3）。
- **一个差异「显著」不等于它「贵」**：工具通道差异 p≈0.0002，代价却在 20 条 stable
  跨域样本上完全看不见（两档都 20/20）——**先问它在什么条件下兑现成损失**（§24.3）。
- **A/B 之前先证明两臂真的不同**（2026-08-10）：给 `replan()` 加形参后 harness 没跟着传，
  24 个样本两臂逐字相同，差点据此否掉自己的守卫；方向还正好是「看起来变差了」。
  同 §13.1「执行器有这个形参≠尺子也在传它」——动作型条目在运行手册 §11（findings §22.4）。
- **跨时段的读数不要合并平均**（2026-08-10）：`cp.adaptive.weather-outing` 上午 11 样本
  0 次「首轮判 simple」，下午 36 样本 10 次（≈28%）——**上午的 9/11 与下午的 25/36
  合起来那个数谁也代表不了**。⚠ 当时记成「工作树无改动可解释」，**那不是结论、是当时
  看不见足够的东西**：真因随后定位到**掉出工具通道的比例**（§23/§24），
  批次间波动的第一嫌疑人就是它——所以先看 `plan_modes` 再谈「模型状态」。
- **诊断出一个洞，不等于这个洞就是病因**（2026-08-10）：「空调先别关」检不回范例
  （0.305 vs 阈值 0.34）是可测量的事实；补上够得着的范例后每趟都检回，通过率
  15/18 → 16/18、p=1.000。**「检索够不着」与「补上就能修」是两个命题**（§22.1）。
- **「可选断言」等于把最该红的一类回归托付给「写用例的人记得加一行」**（2026-08-10，B1）。
  `no_side_effect_before_confirm` 只有 case 显式声明才判——于是「VAL 真执行了危险命令，
  而这条 case 恰好没声明」在报告里完全看不见。**安全类不变量必须由尺子自带、与 case
  声明无关**（`judge_edge_val_execution` 就是这么落的），只留一个显式豁免（确认轮）。
  同理：这类断言要配 `<面>_observed` 标志，**没观测和观测到零是两件事**。
- **反向验证要两头做**（2026-08-10，B1）：既证明「注入缺陷会红」，也证明「对照仍绿」。
  T2 那批回退后 speech/action 两条红、而零输出回退与空 delta 对照仍绿——**后者证明的是
  「没修过头」，和前者一样重要**。只做前一半，一个恒红的断言也能骗过验收。
- **测试红了先问「是修坏了还是前提变了」**（2026-08-10，B1）：纵深防御一旦建立，旧的
  **单层**突变测试会自然失效——`test_edge_premature_execution_...` 原来只打掉端侧
  `_confirm_required` 就能让危险动作落地，VAL fail-closed 后那个突变不再生效，测试转红。
  正确处置是改写成「两道闸都破」并**补一条把「单破一层不够」钉成断言**，不是回退防御。
- **诊断动作本身会污染下一次跑批**：`scripts/run_e2e.py` 会重建服务并把运行时标记成
  `runtime_freshness: unverified`，在两次全量批之间穿插它，下一批的 L3 阶段会重建
  llm-gateway、掐断 primary 的网关连接（大批 `planner_unreached`）。单独定性 L3 之后
  **整批重来**，别接着跑还没开始的那一批（§19.4，本轮亲自踩的）。

---

## 5. 第一步（任何人接手都先做这个）

```bash
cp .env.example .env        # 可选填 LLM_API_KEY；不填走 mock 也能跑
make proto                  # 生成 gen/python + gen/go（没有它什么都跑不起来）
python test/smoke_edge.py   # 验证端侧逻辑（无需 docker，应 13/13 通过）
make up                     # 起全栈（首次需调试，见 docs/dev-guide.md）
```
环境/工具没装齐、Windows 无 make、单服务调试 → 看 `docs/dev-guide.md`。

---

## 6. 改完怎么自检（提交前必做）

| 改了什么 | 自检 |
|---|---|
| 任何 Python | `python -m py_compile <改动文件>`；相关 `python -m pytest <agent>/tests` |
| 端侧逻辑（fast_intent/val/edge_agents）| `python test/smoke_edge.py` |
| HMI / TTS | `cd hmi && npm test && npm run build` |
| Dashboard / 可观测 | `cd dashboard && npm test && npm run build`；全栈后查 `http://localhost:8092/healthz` 与 `http://localhost:5174`；badcase 贯通链路 `python test/e2e_obs.py`（turn 落库/obs.llm/日志关联/badcase/重启持久化） |
| proto | `make proto` 重新生成，确认 codegen 无错 |
| 端到端链路 | `make up` 后 `python test/e2e_ws.py` |
| 新增 Agent | 契约测试（参考 `agents/navigation/tests`）+ 在 compose 注册 |

不要为了"让它跑起来"注释报错或加绕过标记——找根因（CLAUDE.md §6）。

---

## 7. 最常见任务：新增一个 Agent（最短路径）

1. 复制 `agents/navigation/` 结构到 `agents/<snake_name>/`（包目录 snake_case，agent_id kebab-case）。
2. 改 `manifest.yaml` 声明能力/权限/trust_level/deployment；**若 Agent 需要精确位置/电量等敏感上下文，必须声明 `context_scopes`**（`location` / `vehicle_state`，含调子 Agent 透传的 propagator）——否则编排按最小化下发会剥掉这些键。
3. 继承 `agents/_sdk` 的 `BaseAgent`，实现 `handle()`（**别重写 gRPC/注册**，SDK 已封装）。
4. 写 `tests/` 契约测试。
5. 在 `deploy/docker-compose.yaml` 注册服务（分配新端口，见 `docs/conventions.md` 端口表）。
6. **不改编排核心**——注册后 Planner 自动可路由。

详见 `agents/_sdk/README.md` 与 `CLAUDE.md` §3。

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
