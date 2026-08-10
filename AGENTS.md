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
→ 逐批证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§19**。
历史流水只查 [`docs/agents-history.md`](docs/agents-history.md)，不要再抄回本文件。

**最新后端全量基线**：`python -m pytest --import-mode=importlib`
**4490 passed / 16 skipped / 0 failed**（收集 4506 项，单进程 18m14s，2026-08-09 实测）。
2026-08-10 分组实测：`pytest test/` **1127 passed / 9 skipped**（裸 `server` 导入冲突已修，
该目录选集**不再有红**，但它仍不是项目基线命令——分母不同）、`orchestrator/` **1093 passed**、
端侧 smoke **13/13**。HMI `node --test` **225/225**、Dashboard vitest **17/17** 为 2026-08-09
实测，Go 网关数字沿用 2026-08-04 批次；本轮没有改前端、Go、`.env` 或 CI。

| 意图落域证据 | 当前可引用事实 |
|---|---|
| L0 discovery | **76/76**，561 条 / 522 唯一输入 |
| gate 规模 | **139 stable / 129 唯一输入**，L0 strict **25/25，exit 0** |
| 对比模型正式 baseline | [`baseline_intent_adversarial.json`](docs/reviews/eval/baseline_intent_adversarial.json)；干净 `f0af9c0`，锁定 `deepseek:deepseek-v4-flash`，由当前 L3 原始字节/摘要/时间/精确路径契约重新取证并写入。**未随 `32e8718` 重取**——它仍是 DeepSeek 在 `f0af9c0` 的证据 |
| DeepSeek 完整 gate | **147/147**：L0 25、L1 117、L2 4、L3 1；exact **121/121**，raw 幻觉/校验后逃逸/不稳定均 **0/121**；L1/L2 各 **2 个独立进程 × 每进程 3 样本**（`f0af9c0`） |
| MiniMax 主模型 gate（`32e8718`，2026-08-10） | **141/147**；exact **116/121**、required **99/103**；raw 幻觉 **3/121**（原 8）、逃逸 **0/121**；不稳定 **4/121**（原 6）；`pass 141 / unstable 4 / stable_fail 2`，资格仍 `eligible=False` |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline 的 invocation 新鲜、exit 0，只证明该授权 case/claim。2026-08-10 新增 **A1-5**（weather→去处推荐，claim `adaptive_replan_continuity`）两趟独立各 **1/1**，但它服务的 case 仍是 `reviewed`，不进 gate 选集 |
| 三条候选取证（2026-08-10，`f71fd3c`） | `cp.hvac-news.swapped` **2/6**、`nq.hvac.reported` **2/6**（`critical_fail` ×2，真缺陷）、`nq.match.lastweek` **5/6**；`cp.adaptive.weather-outing` L1 三趟 **9/11**。全部 `minimax:MiniMax-M3`、检索零降级、`worktree_clean=true` |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 fallback **0**；MiniMax **11/122**，其中未声明 **2**（原 4） |
| 代码回归 | `orchestrator/` **1093 passed**、`pytest test/` **1127 passed / 9 skipped**；端侧 smoke **13/13**；Skill / Exemplar（253 条 / 20 域）/ L0 门禁均通过 |

⚠ **MiniMax 三批 141/141/140 都不是同一批红灯**：`f0af9c0` 点名的 6 条 unstable 在
`32e8718` 全部转绿，红灯换成另一批边界单元（其中 5 条单跑 10 样本全绿）；`5e8247d`
换池后又换一批（换池随后已回退）。按实测单元不稳定率 3~5%，一趟完整 gate 恰好零 unstable
的概率是个位数百分比——**`eligible=True` 不是「把点名的几条修好」能达成的，是要压整体
底噪**（findings §17.6/§19.2）。
⚠ 上表 MiniMax 行是 `32e8718` 读数，**与当前代码差一个 `skills/exemplars/sunroof.yaml`**
（该范例已单独取证 10/10）；`5e8247d` 那批是换池态、案例集已不一致，**不要挪用**。
严格说当前 SHA 没有对应的全量读数，需要时重跑。

首份正式 baseline 已存在，但它是 **DeepSeek 对比/参考模型**在固定 provider、资产与代码快照下的
意图理解与落域证据；不证明 MiniMax 主模型、Agent 业务结果、外部 Provider 内容或跨模型平均质量。
MiniMax 本轮即使 process/provider/embedding/L3 身份完整，仍因 `gate_failures`、raw 幻觉、未声明
fallback 与 `unstable_results` 被资格闸拒绝。后续写入仍必须由一次新的完整父报告
明确 `eligible=True`，不得手工改正式文件。

### 4.1 活跃待办（只列仍需行动的）

| 优先级 | 待办 | 完成判据 |
|---|---|---|
| — | **§4.1 当前没有活跃待办** | 见下方收口说明；条件型 / 后置项在 §4.2 |

> 2026-08-10（收官批）已收口：**`cp.adaptive.weather-outing` 的账第三次改写，
> 真因在输出通道，不在知识。** 先补两处观测面（`plan_mode` 从 M1a 起就采集却从没进过
> 报告；`wire.get("complexity","simple")` 是静默默认，`toolcall` 有 schema 强制而
> `salvage`/`fallback` 没有），然后 27 样本按通道分账：
> **`toolcall` 10/11（91%） vs `toolcall_salvage` 7/14（50%），Fisher p≈0.036**；
> 而 `complexity_declared` 全 `True`——**模型在 salvage 通道上是明写 `simple` 的**，
> 我加的标记把自己的「静默默认」假设证伪了。
> ⇒ 判据＝**同一段 prompt，走 schema 作答与掉进自由文本作答，输出分布不是一回事**
> （schema 的 required+enum 是作答脚手架，不只是校验）；
> 以及**一个会左右全部落域读数的前提，必须每批都印出来**——`meta.plan_modes` 与摘要行
> 已落地，此前这个数**没有任何消费方**，于是波动只会被读成「模型今天状态不好」
> （§22.5 那句「工作树无改动可解释」正是这么来的）。
> **没有**去改 provider 的 tool-calling，也没有在 salvage 上编规则补判 adaptive；
> salvage 占比高是 provider 侧可靠性问题，选项与代价见 findings §23.3，**待泓舟拍板**
> （已入 §4.2）。逐条 findings §23。
>
> 2026-08-10（晚批）已收口：**两条新 P2 各有结论，但都不是「修好了」**——
> ① `nq.hvac-keep.dont` 的「短句检索够不着」**是真的**（0.305 vs 阈值 0.34，对称 Dice
> 惩罚短句），但**补上够得着的范例之后通过率没动**（15/18 → 16/18，p=1.000，
> 范例 6/6 趟都检回），按纪律退回，判据＝**诊断出一个洞不等于这个洞就是病因**；
> ② weather-outing 的 `replan 出空计划`补了确定性守卫（`complexity=adaptive` 是计划
> 自己的声明，replan 收场即自相矛盾，与既有条件目标守卫对称；**只在第一次 replan 上
> 生效**，否则每个 adaptive 请求白加一次 LLM 往返），六条单测 + 两次反向构造。
> 过程中抓到**尺子漂移**：L1 harness 调 `replan()` 没传 `adaptive`，导致我第一组
> 「守卫 vs 对照」24 个样本**两臂逐字相同、什么都没测**——判据＝**A/B 之前先证明
> 两臂真的不同**（§13.1 判据一的第四次兑现）。逐条 findings §22。
>
> 2026-08-10（下午批 · 生产侧）已收口：**`nq.hvac.reported` 的真缺陷已修**——
> 根因不是「模型不懂否定」（常驻 policy 每轮都在），是**「引述」这个结构库里一条范例都没有**，
> 检回来的是 `volume#1`「空调别动，把声音调低一点」→ `volume.dec`——**它到场了，但它教的
> 是「混合否定保留并列肯定诉求」这条判据**，而引述句要的是「整句一个动作步都不出」。
> 补 `chitchat#12`「后座喊着要关空调，你别听他的」后 A/B **5/12 → 11/12
> （双尾 Fisher p=0.027）**，七条 hvac 护栏 12/12 零回归。
> **没走常驻 policy 是因为预算余量只剩 1 个字符**（policies + 最大 guide = 2599/2600）。
> 判据＝**看检索名单要问「到场的这条范例教的是不是这一条判据」，不是「有没有范例到场」**。
> findings §21（该节初稿把 `volume#1` 原文读成乱码写错过一次，已更正并留痕）。
>
> 2026-08-10（下午批）已收口：**三条未晋级候选全部按跨进程契约取证完毕**
> （`cp.hvac-news.swapped` 2/6＝尺子抓对了 `limit="今天的"` 的槽位污染，维持严格口径；
> `nq.hvac.reported` 2/6＝转真缺陷，升 P1 见上；`nq.match.lastweek` 5/6＝边界方差，不动）；
> **weather-outing 的真实 L3 claim 已建**（journey `A1-5` 进 `regression_a.yaml`，
> `journey_links.yaml` 新增 claim 枚举值 `adaptive_replan_continuity`——**没有复用
> `dependency_continuity`**，那条证不了「第二步是看到结果后才补出来的」）；
> **snooze /「离开X」/「到X之前」三个提醒词形已补端到端用例**（tag `reminder_wordform`，
> 两趟独立 live 各 3/3）。逐条证据 findings §20。
>
> 2026-08-10（上午批）已收口：`pytest test/` 裸 `server` 导入冲突（按路径认模块，15 红 → 0）、
> hint 退役后的陈旧离线评测资产（`eval_route_hints` 76/86 → **78/78**，`--strict` exit 0，
> 顺带补上「删除留痕」后发现基线陈旧的其实是 **45 条**不是 9 条）、宽 journeys 两条外部
> 依赖残留（B3-1 gold 改条件式 + 离线三态守卫；B3-2 归高德侧，见 §4.2）、
> MiniMax 原 6 条 unstable 全部转绿（两条真缺陷修在 `apply_plan_repairs` 与
> `_MULTI_ACTION_CONNECTOR_RE`，raw 幻觉 8→3、未声明 fallback 4→2）。
>
> **主模型 `eligible=True` 与裸对象澄清族**两条 P0 已移入 §4.2 —— 它们的结论都是
> 「不要行动」（继续跑批期望值极低 / 三条修法两否一未启动），留在活跃表里会让接手人
> 误以为有工可开。**M5 P3b 同理移入 §4.2**：它的完成判据「真实流量错对象率 <0.3%」
> 本身就是「先别开工」，且与 §4.2 既有的「端侧能力面与 P3b 前置」是同一件事，
> 两处并列只会让人以为有两笔账。

### 4.2 延后 / 条件待办索引（不进入当前主线）

> §4.1 只放正在行动的事项；本表保留尚未启动、等待外部条件或明确后置的入口。条件满足后先晋级 §4.1 并补完成判据；历史流水仍只查 `docs/agents-history.md`。

| 主题 | 当前状态 / 启动条件 | 权威入口 |
|---|---|---|
| 端侧能力面与 P3b 前置（含 **P3b operate 抽取与放量**） | 除雾 intent 仍缺席；“穿衣指数→股指”仍是规则错配。对象桥接、operate 抽取和真实错对象率 <0.3% 齐备后才放量。⚠ 压这个数的手段是 **R4.1b P1 执行侧对象化**（让 object 数从 65 长上去），**不是调阈值**；且当前 PoC 没有真实流量，这个数只有观测面、还没有分母 | [M5 P3 收尾](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) §P3 收尾 |
| live 路由回归进 CI | hint 退役后的召回保护目前是 live 人工车道，不是 CI 阻断；有稳定凭证、预算与 provider 方差处置后再接 CI | [旅程体系](docs/design/2026-07-14-journey-e2e-test-system.md) §4.3、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| `route_hints` 继续退役 | 当前实数 **11**；`mcp-bridge#0` 必须先过专项安全回归。旧三条单档候选不得按历史索引直接执行；当前只用 MiniMax 主模型 / DeepSeek 对比，MiMo key 失效不阻塞主线，切换 provider 时重新全覆盖取交集 | [M5 P2](docs/design/2026-07-28-intent-accuracy-data-flywheel.md)、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| M5 后续杠杆 | catalog 检索化当前是“有意不做”；16k 预算再次裁剪或保护集显著变瘦时重评。gold→范例现走 CLI；P4 仅在范例 ≥2k 且 N1 平台期 ≥2 周时启动 | [M5 P2/P4](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) |
| M-B / M-C / M-D 明确后置项 | 13 张验收主卡已清零；跨域删除 saga、完整隐私管理/迁移仪式、持久治理扩面与 MCP 生产化覆盖按 GDPR 完备性、量产迁移或新消费方触发 | [总体验收](docs/reviews/2026-07-26-acceptance-review-m0a-m4.md) §10.2/§11.2/§12.2、[OwnerKey 契约](docs/conventions.md) §9.13 |
| M2 / M3 产品化边界 | Ledger 自动续跑/任务中心，以及主动治理持久化、偏好学习、dashboard、远距 geocode 与真实商户均为显式未做；出现真实消费方或产品阶段后另立卡 | [M2 RFC](docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md) §9.6、[M3 RFC](docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md) §10.7 |
| M4 声纹 / 视觉余项 | 真麦校准、语音注册入口、模板漂移治理、视觉多轮与 `vp_*` 指标消费仍未收口；进入真机量产验收或 v2 前启动 | [M4 RFC](docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md) §11.5/§11.8、[声纹契约](docs/conventions.md) §9.11 |
| MiniMax 主模型 `eligible=True` | **不是跑批问题，是方向问题**。三批读数 `f0af9c0` 141/147 → `32e8718` 141/147（raw 幻觉 8→3、不稳定 6→4、未声明 fallback 4→2）→ `5e8247d` 140/147（换池态，已回退）。**每批红灯都是从同一个边界池里重新抽的**，换掉具体红灯不提高整体资格概率；实测单元不稳定率 3~5% ⇒ 一趟零 unstable 概率个位数百分比。启动条件＝泓舟先拍一个方向：压底噪 / 换判据口径 / 接受主模型不出正式 baseline。**在此之前继续跑批期望值极低** | [findings §17.6/§19.2](docs/design/2026-08-02-intent-routing-adversarial-findings.md)、[最终 review §7](docs/reviews/2026-08-04-review-intent-adversarial-finalization.md) |
| 裸对象澄清族（留在门禁内，已立卡） | `nq.landmark.bare` 合并 11/20≈55% 的高方差边界句；`nq.landmark.explicit` 自己每条断言都过、被 relation `clarify_flip` 连累；同族第三条 `nq.city.bare`「上海」reviewed 未进池。**路径 1「写 guide」实测否掉**（§18：4/10→1/10→退回 7/10，p≈0.02 有害）；**路径 3「换出预选池」执行并全量验证后由泓舟裁定回退**（§19.5：总分没变好且会冻结 baseline 写入）。**路径 2（范例 schema 加 clarify 型）是唯一未启动项**——要改契约 + 门禁 + 检索消费面，面较大，有人愿意投入时才启动 | 立卡整段写在语料 `test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml` 的 `nq.landmark.bare` 头上；[findings §18/§19](docs/design/2026-08-02-intent-routing-adversarial-findings.md) |
| Planner 工具通道可靠性（`toolcall_salvage` 占比） | 2026-08-10 实测 `minimax:MiniMax-M3` 上 27 样本里约一半掉出工具通道，而掉出去那一半的规划质量只有走成的一半（91% → 50%，p≈0.036）。**这是 provider 侧 tool-calling 可靠性，不是知识/落域问题**。选项＝换档或换 provider / salvage 轮强制重试工具通道 / 接受并在读数里始终分账（`meta.plan_modes` 已每批可见）。三者代价与影响面都超出尺子批次的授权，待泓舟拍板 | [findings §23](docs/design/2026-08-02-intent-routing-adversarial-findings.md) |
| B3-2「广州塔」地标解析（高德侧） | **不进落域账**。2026-08-10 同坐标直连复测：geocode 对（兴趣点／广州塔真坐标）、`near=None` 重搜 top1 就是广州塔，只有带深圳偏置的关键词搜索会顶出「广州仄仄科技有限公司」2.4km。即 R1 去偏置重搜机制本身是对的，但它**要多打一次高德**，跑批并发下正是最易被限流的那一次；掉一次退回就近弱匹配，掉两次成「暂时无法确定」——两次红的两种形态由此解释。归高德 QPS 一族，出现真实用户投诉或做并发治理时再启动 | [复杂意图·地标/停车](docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md)、判据与复测记在 `test/journeys/target_b.yaml` B3-2 注释 |

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
- **跑 pytest 不要带 `PYTHONIOENCODING=utf-8`**：子进程按 UTF-8 写、父进程按 GBK 解，
  Windows 上会把拉子进程的用例弄成假红（3 红 vs 不设时 154 全过，§20.6）。
  要看中文输出就把 JSON 落盘再读文件。
- **读任何落域数之前先看 `plan_modes`**（2026-08-10）：掉出 `toolcall` 的那些轮，模型是在
  自由文本里作答，输出分布与走 schema 的轮**本就不同**（实测 91% vs 50%，p≈0.036）。
  摘要行会打 `[!] N/M 轮没走成 toolcall——这些轮与走 schema 的轮不可直比`（§23.2）。
- **A/B 之前先证明两臂真的不同**（2026-08-10）：给 `replan()` 加了 `adaptive` 守卫后跑
  「守卫 vs 对照」共 24 样本，正要定性「守卫有害」时才查到 **L1 harness 调 `replan()`
  根本没传这个形参**——两臂逐字相同，那组读数对守卫什么都没测。方向还正好是「看起来
  变差了」，最容易被当成结论。同 §13.1「执行器有这个形参≠尺子也在传它」（§22.4）。
- **同一 SHA、逐字相同的注入，跨时段的失败形态分布可以完全不同**（2026-08-10）：
  `cp.adaptive.weather-outing` 上午 11 样本 0 次「首轮判 simple」，下午 36 样本 10 次
  （≈28%）。**上午的 9/11 与下午的 25/36 不要合并平均**——形态都换了，合起来那个数
  谁也代表不了（§22.5）。
- **诊断出一个洞，不等于这个洞就是病因**（2026-08-10）：「空调先别关」检不回范例
  （0.305 vs 阈值 0.34）是可测量的事实；补上够得着的范例后每趟都检回，通过率
  15/18 → 16/18、p=1.000。**「检索够不着」与「补上就能修」是两个命题**（§22.1）。
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
