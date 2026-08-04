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
api-football，无凭证回退 mock）。当前全量测试基线与最近批次见 §4.0 快照；逐批历史流水在
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

### 4.0 当前快照（新会话从这里开始）

> 截至 **2026-08-04**。本节只保留当前事实与最近批次摘要；**逐批次历史流水（只进不出，查证据用）
> 整体在 [`docs/agents-history.md`](docs/agents-history.md)**——新批次收口时把完整记录追加到
> 那边、在这里刷新摘要，不要再往本文件堆流水。
>
> **接手意图落域对抗测试的新会话，按这个顺序读**：① 运行手册
> [`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md)
> §0「现在能引用什么」+ §10「已知残留与下一步优先级」；② 评审报告
> [`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`](docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md)
> **§10–§10.11**（新口径读数与本期全部结论，含一条**已更正的错误定性**在 §10.8）
> **＋ §10.15**（深夜产品批的回填：那里更正了本报告的两条定性，并写下「门禁红也不等于
> 有稳定缺陷」）；③ 发现与修复的单一入口
> [`docs/design/2026-08-02-intent-routing-adversarial-findings.md`](docs/design/2026-08-02-intent-routing-adversarial-findings.md)
> **§7（发现）· §8（2026-08-03 深夜修复）· §9–§11（2026-08-04 尺子硬化 / 门禁分布 / L3 解封）**；
> ④ 本文 §4.1 活跃待办；⑤ 完整流水 `docs/agents-history.md` **§5 / §6 / §7 / §8**。
> **live 跑批前先看运行手册 §2 的三个环境变量**——少给一个整趟白跑。
>
> **2026-08-03 晚追加四批**（评审 §10.12 / §10.13 / §10.14）：`clause_commute`
> 口径裁定（relation 改对照 `supp(base)`，`relation_pass_rate 90.9%` **作废**）·
> 「恰好 N 次副作用」契约字段 · `--tag` 选集自报口径 · `stable` 规模
> **104 → 122**（`--strict` 转绿）+ 一条真栈撞出的生产健壮性修复（`50c2b3f`）。
> ⚠ **最该先读的是 §10.14.4**：补规模**不等于**门禁能跑绿——现有 stable 里有两条
> 稳定红，那是比 L3 更硬的 baseline 前置。
>
> **2026-08-03 深夜再两批**（产品 `9d6ae0e` + 语料 `6eb9bab`，逐条证据 findings **§8**）：
> 上面那两条稳定红**已修完**并在 gate 全量 L1 里复验通过。真因都和立账时的猜测不一样——
> `ex.homophone.aircon` 是 **hvac 域一条范例都没有**（`skills/exemplars/` 的 199 条金标全部
> 来自云侧 manifest，车控是端侧能力、天然空白）；`nq.umbrella.both` **不是回归**
> （检索名单在通过的那次与失败的两次逐字相同，是 guide 把并列判据写成「提醒本身已有明确
> 时间」）。gate 全量现 **110/116（94.8%）**，剩下两条 `stable_fail` 逐条独立复跑**都翻面**
> ⇒ **门禁不绿的原因已从「稳定缺陷」变成「方差」**（findings §8.5 的新判据）。
> 语料侧补 unseen 10 条（唯一输入 502 → **512**）。

**全量测试基线**：后端 `python -m pytest --import-mode=importlib` **3948 passed / 11 skipped /
0 failed**（2026-08-04 单进程实测 15m48s）；HMI `node --test` **225/225**；
Dashboard vitest **17/17**；端侧 smoke 13/13；Go 网关 vet+test 通过。
⚠ **「本机 32 条既有红」这条历史记载是误判，已修（`0fac11f`）**：真因是
`.claude/worktrees/` 下留着一份完整 checkout，而隐私清单扫描的排除表只写了 `.worktrees`
（带点）→ `runtime/privacy_registry.py` 被读两遍 → `duplicate privacy candidate entries`
→ **33 条契约测试凭空变红**。同目录差分实证：`test_e2e_stack_lease.py` 旧排除表 10 failed /
新排除表 61 passed。**判据：差分证明的是「不是这批引入的」，不是「这是环境问题不用管」**
——上次对照 clean HEAD 得出「32 条逐条一致」方法没错，但两边共用同一个环境成因。
**2026-08-03/04 对抗测试期累计**：对抗专项单测 **178 → 240**（`test/test_intent_adversarial_*.py`
+ `test_eval_intent_adversarial_cli.py` + `test_build_intent_adversarial_candidates.py`；
2026-08-04 relation 口径第二次裁定 +5）；
新增 `orchestrator/cloud/tests/test_planning_no_action.py` 8 条、`agents/parking_payment/` 9 条；
`orchestrator/` 全量 **936 passed**（`orchestrator/cloud` 477）；端侧 smoke **13/13**；
L0 全量 **70/70 exit 0**（**551 条 / 512 唯一输入**，2026-08-03 深夜补 unseen 10 条后）；
**`gate --layer l0 --strict` exit 0**（stable 132 条 / 唯一输入 **122** ≥ 120）。
~~⚠ 本机当前 `scripts/tests/` 有 32 条红~~ **已全部消失**（见上方基线注）。
「同一命令在本机与 CI 上的分母不同，引用基线前先确认是在哪台机器上跑的」这句仍然有效。

**意图落域对抗测试体系（2026-08-02 建成，2026-08-03 首轮发现全部修复 + 尺子自身 12 条硬化）**：

> 已收口的条目**不再列在这里**（本表只留活的）——完整逐条记录见 [`docs/agents-history.md`](docs/agents-history.md) **§5**，读数与裁定见评审报告 §10–§10.11。
📘 **接手人从运行手册开始：[`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md)**
（常青指南：怎么跑 / 红了怎么查 / 修 badcase 的产物 / 加用例自查 / 晋级与 baseline 前置 / 残留优先级）。
规格 `docs/design/2026-08-02-intent-routing-adversarial-testing.md`（§21 落地记录、§22 尺子硬化）、
实施计划同名 `-implementation-plan.md`、**发现清单 + 修复批次记录另册**
`docs/design/2026-08-02-intent-routing-adversarial-findings.md`（**修复批次的单一入口**）。入口
`test/eval_intent_adversarial.py`，语料 `test/eval_corpus/intent_adversarial/`（**551 条 /
512 唯一输入** / 九类攻击 / 144 组最小对照 / **20 条** boundaries 台账双向覆盖 /
云侧 129 个 active intent 覆盖清零 + 61 条端侧原子车控逐条豁免）。
**132 条 stable / 唯一输入 122**（设计要求 120–160，**2026-08-03 晚已达线**，
`--strict` exit 0）；正式 baseline 仍未生成，**前置有两条**：L3 证据未取得（既有 e2e
运行器在本机 `lease_protocol` 失败）+ **gate 全量 L1 跑不到全绿**——⚠ 但这条的性质
2026-08-03 深夜变了：原来那两条稳定红**已修完**，当前 **110/116（94.8%）**，剩下的两条
`stable_fail` 逐条独立复跑**都翻面** ⇒ **门禁不绿的原因已是方差而非稳定缺陷**
（findings §8.5）。资格闸要求无 `stable_fail` **且**无 `unstable`，故仍正当拒绝、文件未生成。

**修复批次读数（2026-08-03，minimax:MiniMax-M3 warm）**：L0 **70/70**（首轮 65/70，5 条全修）；
L1 发现轨原始 evidence unit **425/468**，扣掉 6 条网关伤亡后 **431/468**（首轮 400/458）；
**`stable_fail` 27 → 5**。⚠ **只引用这两行**——同日独立评审（见下）判定
`exact_plan_set_rate` / `seen-unseen` / `capability_hallucination_rate` / `relation_pass_rate`（**该指标 2026-08-03 晚再次改口径，90.9% 那版也已作废**，见评审 §10.12） /
`instability_rate` 口径均不成立，**本批不拿它们当结论**，尤其**不拿 unseen 涨幅当泛化证据**
（cohort 有原句泄漏）。评审同时认可「原始 evidence unit 结果可用」且「产品缺陷仍按原始
断言逐条复现」——本批正是这么做的，逐条对照表在 findings §5.5。
**规则净变化 10 → 9**（退役 1、收窄 1、新增 0，N2 成立）。本批**一行没动尺子**：
修尺子与修被测对象同批进行正是该体系明令禁止的事，评审的 12 条另开一批（findings §6.4）。

**⭐ 新口径读数第一次存在（2026-08-03 晚，`13e7e3f`）。** 固定 provider
`minimax:MiniMax-M3` 锁定、工作树干净、L1 全量 **470 证据单元**、`--ablations on-failure`
（消融第一次在真实失败上跑起来）、检索 **2040 次调用零降级**、探针与基础设施错误均为 0。
**完整表在评审报告 §10，那是唯一入口。** 头三行：原始 evidence unit **438/470（93.2%）**；
`planner_capability_hallucination_rate` **2.3%**（11/470）而 `post_validation_escape_rate`
**0%**——**旧文档里反复引用的「0% 幻觉」是错的**，它实为校验后逃逸率，拆开才看得见
「模型每 43 次编一次能力、validator 全拦下了」；`dependency_pass_rate` **20%（1/5）**
是全表最差的一格——⚠ **但它的第一版定性是错的，更正见评审 §10.8**：主导形态是**漏第二步**
而不是「接线没接上」，唯一两步都在的那条接线完全正确、红在 gold 上（与 find-vs-go 台账打架）。
且 20% 的分母含 3 条 `unstable`、报告存的是失败那一次，**它不等于「20% 的时候接不上」**。
真因已抓到一条并修好：**guide 自己在制造漏步**（`multi-day-trip` 只讲「必须出 trip.plan」，
模型读成「只出」）。
`instability_rate` **14.5%（19/131）**配 `repeat_coverage` **27.9%**：不是变差了，
是旧分母含着从没复跑过的单元，**14.5% 与旧报的 3.1% 不是同一个量**。
seen **95.8%** vs unseen **92.7%**（差 3.1 点，⚠ 中间隔着 23 条改标，不可与历史 12 点比）。

**尺子六批硬化 + 一批产品修复（2026-08-03，专项单测 178 → 210）**，三轮独立复审的
P0/P1 逐条关闭、每条配注入式反向构造：`9219016`（检索中途降级 / 兜底冒充判断）、
`8f06db5`（复审 §8 的 2 P0+5 P1）、`cd3646b`（语料：澄清裁定 + trunk.open 真缺口）、
`5bbc7ef`（探针不许比被观察的东西更脆弱）、`2b619c3`（复审 §9 的 2 P0+2 P1）、
`13e7e3f`（消融臂替身也会打死跑批）、`a60f08b`（打印失败别弄丢跑完的结果 / 预期兜底不拦闸）；
产品侧 `56e19ff`（「受话了但不该做任何动作」是判断不是解析失败）。

**这一轮最该记住的三条**：① **探针两次把整趟全量打死**（畸形 `slots`、消融替身缺
`_ordered_hints`），而两次生产侧都安然无恙——**观察者不能比被观察的东西更脆弱**；
第二条只在 `--ablations on-failure` 下可达而主跑一直是 `off`，于是「已接通」被当成
「已实现」好几批，判据入册：**没在真实路径上跑过的分支不算实现过**。
② **两条守红线的测试自己绿得没道理**——P0-1 突变测试在契约层就 exit 2 根本没走到资格闸
（退出码恰好相同），`_gold_changes` 的 fixture 用 list 而真实格式是 dict。
③ **一条永远红的闸很快就没人再看它说什么**——预期内的兜底（A8 能力缺席族）被一并拦下，
修法是语料声明 `tags.expects_fallback` 而不是猜形状（形状上它和否定族一模一样）。

**2026-08-03 独立对抗 review：首轮验收不通过；同日硬化后复审仍不通过。** 报告
[`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`](docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md)
确认 baseline 完整选集可被正常过滤参数绕过、L2 丢 Edge 副作用、多轮只执行第一轮，以及
指标/维度/trace/relation/L3 新鲜度/seen-unseen 隔离等问题。

**尺子硬化批（2026-08-03，修复方对照见该报告 §7）**：3 P0 / 7 P1 / 2 P2 逐条处理，
**每条配一个反向构造测试**（先注入这条评审描述的缺陷，证明修完之后它会红）。
专项单测 **120 → 178**；**生产路由 / Skill / Exemplar / Hint / manifest / `.env` / CI 一个字节未改**
——这批只动尺子，与「修尺子和修被测对象不同批」的纪律一致。
本机全量 `pytest` 与 clean HEAD 逐条对照：**32 条红完全一致**（既有 e2e 运行器环境问题，
本批零引入；对照法=`git stash` 后同目录重跑，不能用新建 worktree——它缺 `gen/`，分母不同）。

随后 `9219016` 又补“语义检索中途降级 / fallback 计划冒充判断”两条守卫，专项测试增至 187；
独立复审 §8 当时仍留下 **2 P0 / 5 P1**。第三批 `8f06db5` 继续处理这 7 族残留，
`cd3646b` 再按唯一输入补出第二条真实 `trunk.open` 正例，专项测试增至 **201**。

**第三批独立复审（同报告 §9，当前快照 `cd3646b`）**：Engine 未观测、指标分母、L1 层适用性、
relation-only 扩跑与唯一输入 coverage 已关闭；仍有 **2 P0 / 2 P1**。P0 是：①
`--write-baseline --baseline <其他/不存在路径>` 可绕开既有正式 baseline 的 diff；②
`gold_digest` 漏掉 addressed/assert-plan/complexity/dependency/slot/replan/retrieval 等真实裁判字段。
P1 是 Planner 两次 build attempt 的 raw 证据取了第一次，以及 L3 仍未核 report 的
run/code/lock 身份。因此仍不得写“baseline-ready / 第三批全部收口”。

四条新发现（都不在评审清单里）：**① 原句泄漏比评审点名的多 4 倍**——指纹闸一开，另有 13 条
`unseen_transfer` 的原话字面就在 `skills/exemplars/*.yaml` 里，family 闸对它们全绿；连同 family
闭包共 **23 条改标 `seen_regression`**（unseen 477→454），只改标签不动 gold。
**② 空选集原来是绿的**（`--case <打错的 id>` 跑完 0 条 exit=0），现作参数错误退出 2。
**③「任何状态变化都算副作用」会把正确行为判红**（「打开空调，再把后备箱打开」里空调本地执行
是对的），口径必须窄到「需要二次确认的对象被端侧执行」，且判定要用生产自己的 `VAL._need_confirm()`。
**④ 唯一 run 目录光靠时间戳不够**（同微秒两次调用撞 id）。

**现在能引用什么**：L0 全量 **70/70**（零网络、确定性、exit 0）、当前提交快照的 **231 条**专项单测、
逐条原始断言复现、评审 §10 的 L1 新口径全表（**`relation_pass_rate` 那一行除外，已作废**）。
**不能引用什么**：`relation_pass_rate 90.9%`（2026-08-03 晚改口径，评审 §10.12）；
L2/L3 的新口径全量读数；正式 baseline 仍未生成——**前置两条**：L3 证据、
~~stable 规模~~（已达 122）、~~两条稳定红~~（**已修，findings §8.1/§8.2**）、
**gate 全量跑不绿（现因方差，110/116）**。

四条判据先记住：① **`domain_hit_rate` ≠ `exact_plan_set_rate`**——前者是 RoutingBench
的历史交集口径（期望两域只命中一域照样绿），后者要求必要组全满足 + 无禁选 + 无未授权
额外项，两者不可直比；② **seen 98.0% vs unseen 86.0%**，修复原句上的表现证明不了泛化，
读任何「落域涨了」先问哪一档涨了。**⚠ 2026-08-03 追加：先问那一档本身分得干净吗**——评审 P1-7 查出 3 条完全相同的 utterance+context 同时落在两个 cohort，这两个数字在语料按 canonical fingerprint 去泄漏、重新晋级之前不可引用；③ **两次独立进程滤不干净噪声**——两趟都绿的案例第三趟
仍有 5.9% 落进 `unstable`，门禁红一条先看 `repeat_status`；④ 本套件自身首轮被抓到 7 个
缺陷，其中 4 个是同一形态——**失败被记成了别的东西**（不稳定 / 无证据 / 基础设施错误 /
把运行器故障折算成产品失败）。

**最新（2026-08-02）三件事**：
① **验收报告实现结果复核：13/13 张主卡为真**（逐卡核到 file:line + 抽跑 224 测试；无实现缺失、
无测试造假；3 条低危当批修掉，1 条入账=MCP `compensate_tool` 可达性校验未机制化，**下个 server
接入时必须顺手机制化**）。报告
`docs/reviews/2026-08-02-review-acceptance-impl-and-badcase-intelligence.md`。
② **「天气适合去哪玩」badcase 三连修复**——定性为**落域准确 ≠ 智能**的标本：三轮落域单轮口径
都「对」，用户三次没得到答案（planner goal 全写对、步骤只拆单域一步；质疑轮 planner 改对了
「室内景点」却被 nearby 类目子串扫描降级回「景点」）。修复全声明式：`skills/guides/weather-outing.yaml`
组合判据 + 范例 nearby#23/#24 + nearby 室内扇出（商场/电影院/博物馆**串行**检索防高德 CUQPS）+
`weather_context` 话术承接，**编排核心零改动**；真栈三句原话翻正、冷启动 adaptive 两轮链打通、
guide golden live 7/7。判据入册（报告 §2.4）：**goal 是免费的对照物**（goal 说推荐而 steps 无
推荐步＝可检测的缺口信号）；上下文依赖的 badcase 不标扁平 gold。
③ **nightly 的第二笔账——compose 容器名写死**：`c75df13` 收窄 mock 判据后本地 9/9 PASS，但
定时 run #33（跑在含该修复的 `c3e8e6c` 上）仍红 1 条 `e2e_research_async`。根因是 M-A 批
（`0263971`）把 `car-agent-postgres-1` 等容器名写死进 11 个文件共 13 处——**容器名的 project 段
派生自启动目录名**（本地 `car-agent`、CI checkout `cockpit-agent`），其中一处还被契约测试
钉成断言（「守红线的测试自己要被审计」再添一例）。已全部改为经 `test/support/e2e.py` 的
`compose_argv`/`compose_exec_argv`/`postgres_psql_argv` 按 service 名寻址（尊重运行器的
`E2E_STACK_ROOT` 租约），新增守卫 `scripts/tests/test_e2e_container_names.py`（**先反验能抓住
修复前源码再启用**）。**CI 已确认**：dispatch run **#34**（`f61c3c8`）success——nightly 自
`#30`（2026-07-29）连红四次后第一次绿。

**验收 13 卡全部收口（2026-08-01）**：2026-07-26 总体验收报告 §7 的 13 张主卡四批全部合入
main——M-A 测试真实性 / M-B 多乘员隔离（OwnerKey：**识别对了却存不下来等于没识别**）/
M-C 可靠触达（**publish 成功 ≠ 用户收到**）/ M-D 外部生态（**声明存在 ≠ 能用**）。每批的
「明确未做」逐条附判据在验收报告 §9 / §10.2 / §11.2 / §12.2——**未做不等于没账**；契约沉淀在
`docs/conventions.md` §9.13 / §9.8 / §9.9。

**CI 收口（2026-08-01）**：run #232（`176dd20`）七 job 全绿，`#217` 后第一次（中间红 15 次）。
最后 7 条里两条是真代码缺陷（首次 canonical 晋升在 Linux 必崩 / go wrapper 的 `\"` 转义只对
Windows PowerShell Legacy 传参成立），能躲住全因一段 `if os.name == "nt": return` 把校验整层
跳过了。复现法与四条可复用判据见 §4.3。

**M5 数据飞轮（当前主线）P0→P3a + P3 收尾全部落地**（2026-07-28→08-01；母提案
`docs/design/2026-07-28-intent-accuracy-data-flywheel.md`，全量评审零 P0/P1：
`docs/reviews/2026-07-30-review-m5-data-flywheel.md`）。一句话：修落域 badcase 的标准产物从
正则换成**数据**——`skills/exemplars/` 范例库（权威链最软层，写错是噪声不是事故）+
`boundaries.yaml` 跨域边界裁定台账（人裁一次、机器守不许悄悄新增）；规则第一次有出口
（hint 退役流水线，存量 **32→9**：M5 P2 退到 10，2026-08-03 对抗测试批再退 `reminder#1`；退役判据=跨 provider 交集 + 覆盖全部命中句）；RoutingBench
分布尺（读数配三条限制：隐藏分母 / 域偏斜 / canonical 高分是 hint 钉出来的）；端侧语义 NLU
识别侧建成（**只到 shadow**，`on` 挡位是产品决策刻意不存在——θ=0.8 达 85% 覆盖的代价是约 1%
请求识别成错对象）。**下一步 P3b：operate 抽取 + 放量**，开工判据=错对象率 <0.3%（`nlu.shadow`
span 的 `path` × `nlu_gate` × `nlu_vs_rule` 现在能从真实流量算出上界）。活跃待办见 §4.1。

**再往前的批次**（M0a→M4 六期、总体验收、S2S/声纹真机四批、Skill 层闭环四批、R1-R4 硬化、
各域重构）：完整记录在 [`docs/agents-history.md`](docs/agents-history.md)；架构级结论都已
定稿归档进架构文档（附录 C v1.2→v1.19 按主题索引）。

### 4.1 活跃待办与已知余项

**意图落域对抗测试（2026-08-03 第三批复审后的余项，按优先级）**：

| 待办 | 出处 | 说明 |
|---|---|---|
| **23 条改标 seen 后 unseen 覆盖变薄** | 评审 §7.2-①、§7.3 | 指纹闸抓出 13 条 `unseen_transfer` 的原话字面就在 `skills/exemplars/*.yaml` 里（family 闸对它们全绿），连同 family 闭包共 23 条改标 seen。补法是**新写真正没进过知识的话术**，不是把标签改回去；受影响的是 stale-history invariant、weather/news/trip 三族、`nn-find-go` 边界四条 |
| ~~**`stable` 规模实为 104 < 120**~~ | 规格 §21.8 → 评审 **§10.14** | **已收口**：预选池等量换出换入（`gate_candidate` 被钉死在 140，换出 8 条带病 / 换入 8 条新案例），两趟独立 live 取证后晋级 **19 条**——`stable` 113 → **132**、唯一输入 **104 → 122**、`--strict` **exit 0**。两趟抓到 3 条翻面，单跑一趟会多晋级 1–2 条噪声用例 |
| ~~**现有 stable 集合里有稳定红**~~ | 评审 §10.14.4 → findings **§8.1/§8.2** | **已收口（`9d6ae0e`）**，且两条真因都与立账时的猜测不同：`ex.homophone.aircon` 是 **hvac 域一条范例都没有**（`exemplars=[]`；范例库 199 条金标全来自云侧 manifest，端侧车控天然空白）→ 新建 `skills/exemplars/hvac.yaml`，复验两趟独立进程都绿、检回靠 **`@vec:0.69` 语义通道**（词法没够着＝是转移不是背诵）。`nq.umbrella.both` **不是回归**——检索名单在通过的那次与失败的两次**逐字相同**，真因是 guide 把并列判据写成「提醒本身已有明确时间」，「没说时间」于是成了判条件句的证据。修法是判据换成条件/否定/顺承**三分**（⚠ 只修中间一分会把否定句一起翻正） |
| **gate 全量跑不绿：18 条已修掉 14，剩 4 条（P0）** | findings **§10 → §12** | **3 趟 × repeat 3（9 样本/条）实测**：稳绿 97（83.6%）· **跨进程翻面 0** · 稳红 0 · **进程内抖 18（15.5%）**。⚠ §8.5 立的「跨进程 3/3 ↔ 0/3 翻面」这个机制**不成立**，真根因是**算术**：`gate.normal_repeats: 1` 让「两趟独立进程都过」只买到 **2 个样本**（真实通过率 93% 的用例，2 样本全过概率 86%）。已机制化：`stabilized_at >= 2026-08-04` 的晋级必须声明 `stabilized_samples ≥ 6`，存量按日期豁免。**18 条不是随机分布的：A4 组合 7 + A9 表达攻击 4 = 11/18**。**不降级**——降回 reviewed 会让唯一输入 122 → 104、`--strict` 重新变红，且删掉门禁对最有价值那片区域的覆盖。判据：**`unstable` 是被测对象的属性，不是语料质量的属性**。**2026-08-04 下午产品批已修掉 14 条**（findings §12）：先用 `--repeat 5` 单跑一遍就发现**其中 3 条其实已是 `stable_fail`**（`cp.dep.menu-then-order` **0/5**）——分布口径看不见这个。逐条根因**过半是「这个域没有知识」不是模型抖**：shop 域一条范例都没有、赛事范例全带专名没有泛指、导航范例动词一律写作「导航」。修完两趟独立 live（各 `--repeat 3`＝6 样本，`retrieval_degraded 0`）**14/18 全过、三条原 stable_fail 全部转绿**。剩 4 条：`cp.adaptive.rain-umbrella` 5/6 · `ex.homophone.charging` 5/6 · `nq.dinner-music.drop-music` 5/6 · `cs.news.stale-trip` 4/6（⚠ 修前 5/5，查过与本批无关但**没有证明无关**，挂账）。⚠ **6 样本是晋级线不是「修好了」** |
| ~~**`hvac.set` 的 Verifier 核的不是那件事**~~ | findings §11.2 → **§12.5** | **已收口**：`Verification.expect` 支持 `$slot:<槽名>` 动态期望（`verify.resolve_expect_keys`），`hvac.set` 改声明 `{"hvac_on":"true","hvac_temp":"$slot:temperature"}`。槽缺席时那一键判 **UNKNOWN 不判 UNSAT**——「这一步没声明温度」不等于「温度没设成」，一条断言不能同时服务两个命题。配 8 条测试含**接线断言**（走真 executor 尾链，不直接调求值器：声明存在 ≠ 能用）|
| ~~**planner 把值算进 goal 却没写进 slots**~~ | findings §11.2 → **§12.5** | **已收口（泓舟裁定：分情况）**：① 记忆有值 → 值必须落进 slots（`implicit-vehicle-control` 补一句「只写在 goal 里等于没写」）；② 记忆确实没值 → `NEED_SLOT` 追问几度。落地是**知识库声明 + 通用消费**（`commands.yaml` 的 `value_required_operates` + `EdgeCallExecutor._missing_required_value`，零对象字面量）。B3-3 的 `speech_not` 不受影响——那一轮记忆里有 26。另补检测器 `cloud.planning.goal_value_dropped`（goal 有数字而全部 slots 无数字），**第一例机器可判到值一级**的「goal 是免费对照物」|
| **引用了另一步却不声明 `depends_on`（新，已修）** | findings **§12.4** | `cp.dep.search-then-detail` 的计划本来是对的（`nearby.detail{poi_id: s1.data...}`），红在 `depends_on` 为空。**这不是断言挑剔，执行期真会坏**：`_topo_layers` 只看 `depends_on`，两步并行下发，s2 取 s1 结果时 s1 还没回来 → 字面量路径当成真 POI id 发下去。同链第二处：那串路径同时写进 `slots` 与 `slot_refs`，「已有值不覆盖」把它当已有值跳过。两处按归一修，与「`depends_on` 非 list 归空」同族。判据：**引用了另一步的输出，就是依赖的定义** |
| **两道门禁的能力面盲区（新，已修）** | findings **§12.6** | `eval_exemplars` / `eval_skills` 的 `_known_intents` 只读 `agents/*/manifest.yaml`，而 `mcp-bridge` 的 `capabilities: []` 是**有意的**（能力由 `servers.yaml` 准入清单启动期合成）⇒ **整个 shop 域既写不了范例也写不了 guide golden**，而那个域里正躺着一条 0/5 的稳定红。判据：**「能力从哪里声明」和「能力写在哪个文件」是两件事** |
| **畸形模型输出的防御只做到容器层（已修，判据留档）** | 本会话真栈 | 一趟 140 选集的 L1 跑批被一次 `depends_on: [["s0"]]` 整趟打死（`TypeError: unhashable`）。`50c2b3f` 已修两处（`depends_on` 元素 / `slot_refs` value）。判据：**模型输出是不可信输入，防御要一路防到真正会被拿去 hash / 拿去 split 的那个值，不是防到最外层容器为止** |
| ~~**L3 证据仍未取得**~~ | 规格 §21.8 → findings **§11** | **已取得（2026-08-04）**：「运行器在本机 `lease_protocol` 失败」**已不成立**，跑完产出完整 `journeys_report.json`——回归级 15/15、目标级 14/20，**L3 选集 5/6，唯一红是 `B3-3`**（记忆×车控填值：回复只剩一个「度」字、终态 `hvac_temp=20` 期望 26）。极可能是隐私扫描修复顺带解开的（`lease_protocol` 归属的测试正是被那个 bug 打红的）。**判据：把一条红归成「别人的账」之后，要留一个复查它的触发器**——归因写进文档就没人重验了 |
| **组合漏第二步（原「依赖接线 1/5」，⚠ 定性已更正）** | 评审 **§10.8** | 初版写「两步都规划出来了只是没连起来」——**逐条拉计划后发现只对 1 条成立，而那 1 条恰恰不是接线问题**。真形态：3 条 unstable 是**漏第二步**、1 条是 gold 与 find-vs-go 台账打架（已修，3/3）、1 条通过。`trip-then-navigate` 是唯一 `causal=supported`：**guide 自己在制造漏步**（`multi-day-trip` 只讲「必须出 trip.plan」、示例全是并列步，模型读成「只出」），已补组合判据 + golden，实测 3/3。⚠ **20% 不等于「20% 的时候接不上」**——分母含 3 条 unstable 且报告存的是失败那一次。**全族回归已补跑**（`--tag composition` 51 单元 ×3）：两条修复都不在失败名单、3/3 站住；剩下的 `cp.dep.menu-then-order` 等仍是漏第二步的 unstable，按规矩不进修复清单 |
| ~~**契约缺「恰好 N 次副作用」断言**~~ | 评审 §10.7 → **§10.13** | **已收口**：`expected.safety.side_effect_counts`（等式、声明即封闭、只在 L2、全零非法）。上界那条**保留**——`max_agent_calls_per_intent` 量调用、新字段量副作用，「调用了但没产生动作」时两者分叉。真栈反验：注入 `gold=2` 精确红在该断言上，恢复 `gold=1` 后 3/3 |
| ~~**`clause_commute` 族系统性红**~~ | 评审 §10.11 → **§10.12** | **已收口（口径裁定，2026-08-03 晚）**。⚠ 立账时的定性「差的是槽位拼写」**是错的**：只覆盖 6 条红里的 2 条，且其中一条红的是 base。真因是**拿一次采样代表一个句子的行为**——实测同句自抖 58.8%（含槽位）/ 23.5%（仅 intent），而 relation 当时逐次配对比签名。裁定：对照方改成 `supp(base)`，**槽位留在签名里**。`relation_pass_rate 90.9%` 随之作废 |
| **子句间槽位串味（⚠ 定性已更正，部分收口）** | 评审 §10.12.5 → findings **§8.3** | 原定性「前一子句的时间限定词串到后一子句」**只对了一半**。串味说预测的是「明天」，实测拿到的是整串「**明天早上八点**」——而「早上」在原话两个子句里都不存在；同一串**逐字出现在** `reminder#28` 的槽位里，也出现在 `agents/reminder/manifest.yaml` 的 capability description（`_catalog_item` 把 desc 渲进 catalog，**每次规划都在**）。铁证是 `nq.umbrella.both`：原话**一个时间词都没有**，照样产出 `{title:"带伞", time_text:"明天早上八点"}`，两个槽位与 `reminder#28` 一起照抄。修完 **3/3 红 → 1/3 红**，幻影变成「明天八点」——**「早上」是照抄示例字面（已修），「明天」才是真串味（仍在，`unstable` 不进清单）** |
| ~~**「有点热」落 `hvac.inc`**~~（描述已作废） | 评审 §10.11 → findings **§8.4** | **它现在产出 `aircon.dec`——方向是对的**。红在别处：`hvac.*` 与 `aircon.*` 是同一动作的两个 intent 名（见下条），gold 的 `any_of` 只收 `hvac.*` |
| ~~**`hvac.*` 与 `aircon.*` 是同一动作的两个名字**~~ | findings **§8.4** | **已收口（`64f4a96` 语料 + `5d95ceb` 治本）**：删掉 `aircon.inc/dec` 统一到 `hvac.inc/dec`（能力面 78 → 76；风速 `aircon.wind_speed.*` 保留）。live 复验四条曾随机红的用例 **4/4、instability 0%**。判据：**一个动作只能有一个名字——尤其当能力面只靠名字区分时**；别名分裂的代价是双向的，既制造假红也**削弱否定断言**（禁 `hvac.inc` 却不禁 `aircon.inc`）|
| ~~**A4 83.3% / A9 83.0%**~~（已并入上一行） | 评审 §10 → findings **§10.2** | **新读数就是上面那张分布表**：门禁 18 条不稳定用例里 A4 占 7、A9 占 4（11/18）。**「A4/A9 最弱」与「门禁抖」是同一件事的两种读法**——前者是分布口径，后者是它在门禁上的表现形式。逐条通过率见 findings §10.2 |


**M5 待办（都不阻塞）**：

| 待办 | 出处 | 说明 |
|---|---|---|
| ~~**78 个端侧 capability 只有 2 句描述**~~ | P3a 影子第一条观测 | **已收口（`627f34c`）**，但**卡片归因不成立**：planner 侧 Δ=0 跨两档零翻面，收益在 registry 兜底路径。详见 `docs/agents-history.md` 与 `docs/reviews/eval/edge_capability_desc_ab.md` |
| **能力面缺除雾意图** | P3 收尾（原 badcase 真根因） | `commands.yaml` 的 aircon 有 除雾/除霜/内外循环 等 mode，但 `VEHICLE_INTENTS` 里**没有任何除雾 intent**——`关闭强力前除雾` 无论哪一臂都只能在错误答案之间抖（实测 A 臂 `hvac.off`×3/`chitchat`×1，B 臂 `accompany_home.close`×3/`hvac.off`×1）。**这是缺能力不是缺描述**。补它要动 fast_intent + LOCAL_INTENTS + VAL 三处，属新增车控能力，非 P3 范围 |
| **P3b：operate 抽取 + 放量** | P3a | ~~对象桥接~~ 已落地（`nlu_objects.yaml`，`603127b`）。剩 operate 抽取（开/关/调到 N，准入判据②该走规则）+ 放量。开工判据不变：**错对象率压到 <0.3%**；压它的手段是补 R4.1b P1 执行侧对象化，不是调阈值。**新变化：这个数第一次可以从真实流量算出来**——`nlu.shadow` span 现在同时落 `path`（误接/漏接）、`nlu_vs_rule` 四态、`nlu_gate` 三档，`path!=cloud ∧ gate=high ∧ differ` 就是错对象率的上界 |
| ~~**影子盲区：规则误接看不见**~~ | P3a | **已收口（`0944659`）**：四条路径全挂，响应后 fire-and-forget |
| ~~**tokenizer parity 在 CI 上条件性 skip**~~ | 2026-07-30 评审 | **已收口（`fc88a21`）**：冻结 golden（260 例 + 行为等价词表子集 1096 条）进库，CI 零依赖必跑。⚠ 它守的是**算法**；生产词表正确性仍由导出时 `vocab.json` 同源 + `_assert_onnx_parity` 守——**换底座后本测试仍会绿**，该做的是重生成 fixture 并人审 diff |
| **规则把「穿衣指数」判成股指** | P3 收尾扫出 | `指数` 标签 179 条 100% 被 `fast_intent` 判成 `stock`（「查深圳的穿衣指数」→ 股指）。桥接表**刻意不收**这条等价（收进去等于把真 badcase 洗成 agree）。按 M5 范式修法是投范例/收窄规则，不是加 guard 词 |

| 待办 | 出处 | 说明 |
|---|---|---|
| **召回保护从 CI 阻断降级为人工触发**（⚠ 2026-08-02 **代价变大了**） | P2 退役 | 19 条 hint 的召回断言原本是**阻断 pytest**（理由：语料在 continue-on-error 观测步），退役后保护改端到端口径进 `mode_routing_cases.yaml`，而那是 **live 车道不在 CI**。**追加**：nightly 收口后 `e2e_trip` / `e2e_journeys` 也退出了 nightly、`e2e_context` 子集减半——**同一条 hint 退役，第二次收走自动回归覆盖**。这两笔账现在指向同一张卡：**让 live 车道进 CI**（真栈+LLM，密钥/成本/模型方差另议）。在它兑现之前，trip 多轮闭环 / reminder.list / scene.activate 的自动回归只剩人工触发的 milestone 车道。**2026-08-03 第三次**：`reminder.create`（最后一条 reminder hint）与 `deep-research#1` 量词收窄同批落地，代表形态已迁入 `mode_routing_cases.yaml`——**同一笔账又厚了一层** |
| **MiMo 第三档复验** | P2 退役 | 这批 hint 当初正是为 MiMo 写的（`route_hints.py` 开篇），但本环境 MiMo 无可用 key（探针 `all models failed`），证据只覆盖 minimax + deepseek。**切回 MiMo 须复验**——每条退役的原位注释都写了这句与 `git log -S` 恢复方法。**2026-08-03 又添两条**：`reminder#1` 退役、`deep-research#1` 收窄，证据同样只覆盖 minimax + deepseek |
| ~~**nearby 规则群内讧**~~ | P2 双臂裸跑 | **已收口（2026-07-30，`47eac1c`）**：真根因是金标自相矛盾，不是规则打规则。详见 `docs/agents-history.md` |
| **`mcp-bridge#0`（shop.order）退役** | P2 交集 | 两档都判可退役，但 `require_confirm=true` → 治理⑥要求专项安全回归，**路由评测不构成安全证据**。等安全回归 |
| **3 条单档候选** | P2 交集 | `deep-research#0`、`info#4`、`trip-planner#1` 只在单档成立，被交集正确挡下。补第三档 provider 后再判 |
| **N1 域偏斜** | P2 RoutingBench | 语料前三域占七成，navigation 仅 5 例、hvac 1 例——**这些域的 N1 几乎测不出东西**。P3b 的对象桥接恰好要这些域的数据，可合并推进 |
| **catalog 检索化** | P2 有意不做 | TOP_K 未下调。**受保护集合已从 14 瘦到 13**（nearby 退光 hint 后失去资格，2026-07-30）——这正是 P2 说的杠杆方向：先让保护集瘦下来，不是硬调 top_k。⚠ 附带风险已记账在 `test_catalog_budget.py`：预算若再被追上，被裁的将含**周边发现这个高频功能** |
| **`gold_intents` 供给** | P1/P2 | 标注载体、导出 API、dashboard 入口、`from-labels` 工具链全部就位，但**真实标注量目前只有个位数**——机制建好≠供给在增长（skill 层同款教训）。飞轮真正转起来要看 `source=trace` 的范例条数 |

其余可选方向（都不阻塞，按需取用）：
- `sim.adas.*` 演示域——**低优先 backlog，非 M4 DoD**（2026-07-24 §8-6 拍板）。
- 真麦声学验收（S2S 打断手感 / 声纹识别率与误认率）——浏览器声学层 CI 测不了，同 R4.3 惯例留泓舟。
- 下方各余项表里的条目。

**验收余项批次（M-B/M-C/M-D）的「明确未做」**——逐条判据在验收报告 §10.2/§11.2/§12.2 与
`docs/agents-history.md` §2，此处只留索引：**M-B**＝跨域 L2/L3/L4 删除 saga 与 privacy registry
协议、obs 四表 owner 化与原文脱敏、Reminder/SceneAdmin 管理服务、独立迁移 CLI、真栈多乘员 E2E
矩阵、声纹注册单事务；**M-C**＝多实例 outbox worker 与 present lease、HMI IndexedDB 收件箱、
分来源灰度、`research_report` 表、位置提醒地理谓词（ttl 兜住陈旧补播）；**M-D**＝`mcp_operation`
本地镜像（**刻意不建**：商户是订单状态的真相源）、Ledger owner-v2 cutover、HMI operation card、
双 bridge profile、GDPR retained-audit。

**M4 P4 的已知余项**

| 余项 | 出处 | 说明 |
|---|---|---|
| 跨乘员共享记忆 | P4 RFC §4.2 / §10-2 | **拍板 v1 不做**：现有条目 `memory_level` 恒为 `user`，做读侧共享=全部共享=隔离归零；真共享层要改抽取分类，是独立一期。现状是第二乘员查不到主驾存的「家在哪」，各自教一次 |
| `min_consistency` 的窗只有 0.05 | P4 RFC §11.2-1 | 合格注册最低 0.53、三段不同人最高 0.48，取 0.50 居中。合成音色的段间一致性偏高，真人语音很可能要重调 |
| 声纹语音注册入口 / 模板漂移巩固 | P4 RFC §11.5 | v1 只做设置页录入；「记住我的声音」的多轮引导、以及「高置信样本回灌模板」的治理都留 v2 |
| 视觉多轮追问（「它旁边那个呢」） | P4 RFC §11.5 | 需帧的会话级驻留 + 指代解析，v2 |
| **obs 丢掉 `vp_*` 指标字段** | P4 RFC §11.8（2026-07-26 发现） | collector 的 `apply_metric` 是固定键白名单（count/avg_ms/error_rate/…），网关发的 `vp_decision`/`vp_score`/`vp_runner_up`/`vp_occupant` 全被丢掉——RFC 承诺的「四态全进 obs 供 M1b nightly 挖掘调阈值」**实际没落地**。当前靠两侧日志兜住（网关一行 + memory 一行全量排名）。真要做 nightly 调优得给 metric 开自由键或单列一张表 |
| 真人阈值只有一个会话的样本 | P4 RFC §11.8 | 0.45 是按一次真机实测（同人 0.52/异人 0.12）复标的，样本量=1。identify 日志现在有分数了，攒够线上分布再收敛 threshold/margin |
| **既有缺陷（非 P4 引入）**：显式记忆陈述**间歇被拒识** | P4 RFC §11.5 / §11.9（2026-07-27 定性修正） | **此前的归因是错的**：不是「planner 出空计划、记忆其实存进去了」，而是 **R4.4 拒识**——planner 判 `addressed=false`（当成乘客间闲聊）→ 静默短路，**整轮零回话且按设计不落库不进画像**（比原描述严重：说了、没回、也没记）。实测 `status=rejected`：「记住，我女儿叫小满」某次 3/3 被拒、换一批 6 条又只拒 1 条——**是 LLM addressed 判定的方差**。建议修法：**「记住/记一下/别忘了」这类祈使前缀确定性判为 addressed，不问模型**（同「系统持有的事实不交给 LLM」一族——用户用祈使句直接对助手说话，是不是在跟你说话不需要模型判断） |
| ~~**常去地点（家/公司）不分乘员**~~ | 2026-07-27 乘员维度盘点 | **已收口（M-B，2026-08-01）**：唯一真相源改为 owner-scoped `memory_item place.*`，`GetContextRequest`/`UpsertProfileRequest` 加 `occupant_id`，SDK `fetch/save_profile` 透传；upsert 变 per-key patch（不再整块 map 覆盖），primary dual-read legacy KV 只补缺失 key，**非 primary 永不读 KV** |
| ~~**提醒（reminder）无 occupant 维度**~~ | 同上 | **已收口（M-B）**：`reminder_item.occupant_id` + owner 索引，CRUD/list/cancel/序号态全按 OwnerKey；scheduler/geofence 领取后**先按 OwnerKey 分组再发 payload**，卡片 action 带 `reminder_id`+`owner_occupant_id`。M3 余项「多用户维度的免打扰/频控」仍在（治理器侧，属 M-C） |
| **HMI 记忆面板：单行删除已收口，L2-L4 未做** | 同上 | **M-B 已修单行删除**：面板默认只列当前 occupant，删除改走 `DeleteMemoryItem`（OwnerKey+item id），`identity.name` 只读引导到声纹设置。**仍缺**「清空该乘员学到的记忆 / 删除该乘员全部数据 / 删除该用户全部数据」三级入口——它们要跨 memory/reminder/scene/obs 四域 saga，属 M-B 明确后置项 |
| ~~端侧快路径的轮次不带身份~~ | 同上 | **已收口（M-B）**：`_record_local_turn` 从 `request.context`/`meta` 取 OwnerKey，一次本地请求写成一个完整 exchange（`<request_id>:user` / `:assistant:0`）。仍不触发抽取（端侧快路径多为车控/媒体，本就无偏好可抽）——那是有意的，不是缺口 |

**M4 S2S 的已知余项**

| 余项 | 出处 | 说明 |
|---|---|---|
| **主动×S2S：治理器侧 `s2s_speaking` 情境位未接** | M4 RFC §4.4 / §11.5 | 上报要建网关→proactive 的新链路（非零成本），P1/P2 DoD 未列。**HMI 侧已由 M-C 兜住**：网关透传 `priority` 后按档仲裁——critical 抢话 / user_contract 排队待空闲补播，S2S 交互中主动语音不再一刀切静音（2026-08-02 复核顺手修掉仲裁漂移）。彻底做仍应在治理器侧延后 |
| 真麦声学验收 | M4 RFC §11.5 | 打断手感 / 唤醒后首字 / S2S 音色接受度——浏览器声学层 CI 测不了，同 R4.3 惯例留泓舟 |
| 穿过 `/api/s2s` 那一跳的断连注入 | M4 RFC §11.5 | 会话层重连已用宿主内代理注入真断线验过（真 provider）；容器级网络故障注入价值不抵成本 |
| `s2s_false_promise` 接自进化 | M4 RFC §11.5 | 检测已进 obs span，nightly 挖掘该族 badcase 待 M1b 流水线加一条规则 |
| 重叠对话（抢答式全双工） | M4 RFC §10 | 明确不做：v1 语义仍回合制（全双工红利先兑现在零建连延迟/无缝续听/多轮免重注入） |

**M3 的已知余项（都不阻塞 M4，按需取用）**

| 余项 | 出处 | 说明 |
|---|---|---|
| 治理器无持久化 | M3 RFC §7 | 重启丢待发/延后队列与频控计数；这些消息生命周期以秒计，落库不值当（e2e 正是靠「重启即净初态」做重置） |
| 合并话术是确定性拼接 | M3 RFC §7 | LLM 改写合并留 v2，且必须限定「只重述不新增事实」 |
| `obs.proactive.decision` 无消费方 | M3 RFC §5 | 事件已在发，dashboard 主动治理视图后置 |
| 位置提醒依赖 debug 注入的 location | M3 RFC §3 | PoC 无真实 GPS 流（road-safety 的「进入新区域」同一条路）；接真车 GPS 后围栏模块零改动 |
| MCP 首批是演示商户 | M3 RFC §10.7 | 真实商户 BD 是非技术依赖；resources/prompts/sampling、HTTP/SSE transport、动态放行均不做 |
| 多用户维度的免打扰/频控 | M3 RFC §7 | `occupant_id` 恒 primary，无消费方 |
| 主动消息的偏好学习 | M3 RFC §7 | 「这类别再提醒我」有价值，但要先有治理器产生的数据 |

**M2 的已知余项（都不阻塞后续，按需取用）**

| 余项 | 出处 | 说明 |
|---|---|---|
| T2 Complex 档只放到第一档 3 次/12s | 核心件 RFC §4.2 | `3-4 次/15s` 与 Interactive 跟进等下一轮 journeys 双指标数据；回退=改一行 env |
| `consent` 列只写不读 | 记忆图谱 RFC §3.1 | 为敏感画像预留，消费在 M3/M4 |
| 偏好加权不追溯存量 | 记忆图谱 RFC §10.4 | 老条目只有再次被巩固时才进加权体系（批量回填要重算全部历史证据，收益不抵风险） |
| Ledger 的 HMI 任务中心面板 | 核心件 RFC §7 | proactive 推送已覆盖告知；dashboard 任务视图可选后置 |
| checkpoint 自动 resume / NATS cancel 推送 / readback 求值器 | 核心件 RFC §7 | v1 刻意不做，理由在案 |
| 多跳图推理 / 实体消歧 / 跨用户关系 | 记忆图谱 RFC §7 | 无消费方，不做 |
| `sim.adas.*` 演示域 | 母提案 §8-6 | 低优先 backlog，**非 M4 必做 DoD** |

---

### 4.2 跨期方法论（M2/M3 沉淀，后续直接适用）

1. **消费面先于存储面**——存了没人查就是死数据。新机制先定「谁消费、消费什么」，
   没有消费方的东西不建。（M3 复用两次：删掉无人消费的 `merge_group`；位置提醒
   字段级对照后发现 `extra` JSONB 够用，**不加新列**。）
2. **挂点清单要枚举所有执行路径**——Verifier 按设计挂在 executor 尾链，真栈首验才发现
   engine D0 / loop T2 两条流式直通绕过它，声明了却静默不生效。新增执行期钩子必查全路径。
3. **周期性全量快照会把「变更回调」变成「定时器」**（M3 新增）——`vehicle.state.changed`
   每 30s 一次全量快照，挂在上面、语义是「变了才做」的消费方**必须自己比对上一次**。
   road-safety 是唯一漏掉的那个：车停着不动也每 30 秒查一次天气预警，一个查不到的地名
   就能**打开共享的 qweather 熔断器**，把整个天气域拖垮（journeys 因此红 3 条）。

---

### 4.3 CI / nightly 现状（2026-08-02 更新）

**✅ CI 已收口**：run **#232**（`176dd20`）七个 job 全绿（2026-08-01），是 `#217` 之后第一次绿；
此后 push 均绿（#237 `3a665c2`）。逐条修复记录迁至 `docs/agents-history.md` §3。
**✅ nightly 已收口**：mock 车道判据按 `c75df13` 收窄（见下方判据）之后，定时 run #33 剩的
最后一条红（`e2e_research_async` 容器名写死）于 2026-08-02 修复（`f61c3c8`，见 §4.0 ③），
dispatch run **#34** 全绿确认——自 `#30`（2026-07-29）连红四次后第一次。

**判据先行：本地全绿 ≠ CI 绿。** 本地习惯单进程跑全量，CI 是 **Ubuntu + 分组跑**——两个差异
各自藏着一类缺陷；写死的名字（路径、平台、目录派生的容器名）都在赌启动环境。

**怎么复现 Linux CI（下次直接照做，比读 annotation 快得多）**：
```bash
git bundle create /tmp/repo.bundle --all          # 要带真历史，git archive 不含 .git
docker run -d --name ci-repro python:3.12 sleep infinity   # 用完整镜像，slim 没有 git
docker cp /tmp/repo.bundle ci-repro:/tmp/ && docker exec ci-repro sh -c \
  'git clone -q /tmp/repo.bundle /repo && pip install -q pytest pytest-asyncio pyyaml cryptography websockets'
docker exec ci-repro sh -c 'cd /repo && python -m pytest scripts/tests/ -q --import-mode=importlib'
```
`test_run_go_tests_wrapper` 还需要 pwsh（`_powershell()` 找不到就 skip，**skip 会伪装成绿**）：
从 GitHub Releases 拉 `powershell-7.4.6-linux-x64.tar.gz` 解到 `/opt/microsoft/powershell/7`
（国内约 35KB/s，用 `curl -C -` 断点续传分多次拉）。
⚠ 容器里有 5 条会假红（无 init 收割僵尸进程的三条 reap 用例 + 两条缺 test 支撑模块），
**它们不在 CI 失败集里**——比对时以 CI 的清单为准，别追容器自己的噪声。

| 项 | 说明 |
|---|---|
| 怎么查（仍然有效） | `python scripts/ci_annotations.py [run_id]` 读 annotation（**免 admin**；不带参数=最新一次 run，刚 push 时那次可能还在跑，要显式给 id）。⚠ **annotation 只有 pytest 的摘要行，长断言会被截断**（go wrapper 那条的 argv diff 就看不全）——**真要定位就起 Linux 容器**（上方复现步骤），在容器里加一行 `traceback.print_exc` 三秒就看见了。⚠ **不要把诊断细节塞进 `canonical_rejection_reasons`**——那是契约字段，已有测试锁死「只有这一项」，前人试过被三条测试拦下（改走 stderr 也撞了 runner 的输出契约） |
| nightly 复现法 | 不要动 `.env`（红线）。**把空 key `export` 给跑 runner 的那个 shell**——子进程继承，而 compose 插值里 shell 优先于 `.env`：`export LLM_API_KEY= LLM_EMBED_API_KEY= MINIMAX_API_KEY= DEEPSEEK_API_KEY= DASHSCOPE_ASR_KEY=` → 重建 llm-gateway（`--force-recreate --no-deps`）→ `python scripts/run_e2e.py --lane nightly --full --stale-policy warn`。⚠ **只在 `docker compose` 那一行加前缀是不够的**：`e2e_degrade` 自己会重建 llm-gateway，会把真 key 读回来（我第一次就栽在这，跑出三条假绿）。跑完再 `--force-recreate` 一次即恢复真 provider |

**mock 车道的判据（2026-08-01 被推翻并改写，nightly 收口的核心产物）**：
原判据是「确定性路由：**route_hints 可达**」（旅程体系设计文档 2026-07-14 §4.3）
——它把「有规则撑着」当成了「不依赖模型」，而 route_hints 是**会被数据退役的**。
**新判据：mock-safe ⟺ 这条路径不经过模型判断。** 只有四类：①端侧快路径
（`fast_intent` + VAL，零 LLM）②兜底 Agent（`PLANNER_FALLBACK_AGENT`，
`planning._fallback` 第一分支由**结构**保证被路由到）③确定性解析与流程态短路
（timeparse / 挂起态裸确认 / 注入检测）④协议传输层。**「它有 hint 撑着」不算。**
更一般的一条：**在 mock 栈上断言「模型选对了 Agent」，测的永远是规则不是系统。**

**四条可复用判据**：
1. 跨平台 CI 里，被 `os.name == "nt"` 提前 return 掉的校验，正是另一边会先触发的那一层
   ——**「本地绿」只证明本地那条分支绿**。这轮 7 条里有 6 条是这个形状。
2. **一段「吞掉整类异常」的兼容代码，会连它不该吞的那一种一起吞掉。**
   `_fsync_directory` 对 nt 吞 `OSError` 本意是「Windows 没有目录 fd」，结果把
   「目录根本不存在」也吞了——于是一个顺序缺陷（先恢复后建目录）在 Windows 上
   永远不可见。吞异常要按**具体错误码**吞，不按平台整段吞。
3. **拿不到失败理由时，先问「是没有诊断通道，还是没有那个环境」。** 这批红了半个月，
   期间试过往契约字段塞理由、试过走 stderr，都被拦下；真正的解法是花二十分钟起一个
   Linux 容器——**在能复现的地方，`traceback.print_exc` 就够了**。
4. **一个治理动作（退役规则、改判据、收窄清单）要问一句「谁在靠它」。** M5 P2 退役
   hint 时记了「离线 eval 的召回保护降级为人工触发」这笔账，但**没人问 nightly 靠不靠
   这些 hint**——结果它当晚就红，连红三次没定位。退役/收窄之前，先 grep 一遍谁把它
   当前提写进了注释里（A4-2 的「mock-safe：route_hints 确定性路由」就明写着）。

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
