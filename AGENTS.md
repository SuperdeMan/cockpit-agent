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

> 截至 **2026-08-03**。本节只保留当前事实与最近批次摘要；**逐批次历史流水（只进不出，查证据用）
> 整体在 [`docs/agents-history.md`](docs/agents-history.md)**——新批次收口时把完整记录追加到
> 那边、在这里刷新摘要，不要再往本文件堆流水。

**全量测试基线**：后端 `python -m pytest --import-mode=importlib` **3783 passed / 11 skipped /
0 failed**（2026-08-02 单进程实测 14m02s；对抗测试体系 +128，零回归）；HMI `node --test` **225/225**；
Dashboard vitest **17/17**；端侧 smoke 13/13；Go 网关 vet+test 通过。
**2026-08-03 修复批次 +24 用例**（端侧对抗回归 17 / intent 归位 5 / skill 预算头寸 2，另有
混合分组 2 条并入既有文件）：`test/` + `orchestrator/` + `agents/` 实测 **2521 passed**。
⚠ **本机当前 `scripts/tests/` 有 32 条红**，但**与 clean HEAD `de6ef22` 逐条一致**
（既有 e2e 运行器的环境路径问题，非本批引入）——同一命令在本机与 CI 上的分母不同，
**引用基线前先确认是在哪台机器上跑的**。

**意图落域对抗测试体系（2026-08-02 建成，2026-08-03 首轮发现全部修复 + 尺子自身 12 条硬化）**：
📘 **接手人从运行手册开始：[`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md)**
（常青指南：怎么跑 / 红了怎么查 / 修 badcase 的产物 / 加用例自查 / 晋级与 baseline 前置 / 残留优先级）。
规格 `docs/design/2026-08-02-intent-routing-adversarial-testing.md`（§21 落地记录、§22 尺子硬化）、
实施计划同名 `-implementation-plan.md`、**发现清单 + 修复批次记录另册**
`docs/design/2026-08-02-intent-routing-adversarial-findings.md`（**修复批次的单一入口**）。入口
`test/eval_intent_adversarial.py`，语料 `test/eval_corpus/intent_adversarial/`（527 条 /
九类攻击 / 143 组最小对照 / **20 条** boundaries 台账双向覆盖 / 云侧 129 个 active intent
覆盖清零 + 61 条端侧原子车控逐条豁免）。**113 条晋级 stable**（设计要求 120–160，
未达线）；**L3 证据未取得**（既有 e2e 运行器在本机 `lease_protocol` 失败），正式
baseline 被资格闸正当拒绝、文件未生成。

**修复批次读数（2026-08-03，minimax:MiniMax-M3 warm）**：L0 **70/70**（首轮 65/70，5 条全修）；
L1 发现轨原始 evidence unit **425/468**，扣掉 6 条网关伤亡后 **431/468**（首轮 400/458）；
**`stable_fail` 27 → 5**。⚠ **只引用这两行**——同日独立评审（见下）判定
`exact_plan_set_rate` / `seen-unseen` / `capability_hallucination_rate` / `relation_pass_rate` /
`instability_rate` 口径均不成立，**本批不拿它们当结论**，尤其**不拿 unseen 涨幅当泛化证据**
（cohort 有原句泄漏）。评审同时认可「原始 evidence unit 结果可用」且「产品缺陷仍按原始
断言逐条复现」——本批正是这么做的，逐条对照表在 findings §5.5。
**规则净变化 10 → 9**（退役 1、收窄 1、新增 0，N2 成立）。本批**一行没动尺子**：
修尺子与修被测对象同批进行正是该体系明令禁止的事，评审的 12 条另开一批（findings §6.4）。

**2026-08-03 独立对抗 review：验收不通过 → 同日 12 条全部修复（尺子硬化批）。** 报告
[`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`](docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md)
确认 baseline 完整选集可被正常过滤参数绕过、L2 丢 Edge 副作用、多轮只执行第一轮，以及
指标/维度/trace/relation/L3 新鲜度/seen-unseen 隔离等问题。

**尺子硬化批（2026-08-03，逐条对照见该报告 §7——唯一入口）**：3 P0 / 7 P1 / 2 P2 逐条修复，
**每条配一个反向构造测试**（先注入这条评审描述的缺陷，证明修完之后它会红）。
专项单测 **120 → 172**；**生产路由 / Skill / Exemplar / Hint / manifest / `.env` / CI 一个字节未改**
——这批只动尺子，与「修尺子和修被测对象不同批」的纪律一致。
本机全量 `pytest` 与 clean HEAD 逐条对照：**32 条红完全一致**（既有 e2e 运行器环境问题，
本批零引入；对照法=`git stash` 后同目录重跑，不能用新建 worktree——它缺 `gen/`，分母不同）。

四条新发现（都不在评审清单里）：**① 原句泄漏比评审点名的多 4 倍**——指纹闸一开，另有 13 条
`unseen_transfer` 的原话字面就在 `skills/exemplars/*.yaml` 里，family 闸对它们全绿；连同 family
闭包共 **23 条改标 `seen_regression`**（unseen 477→454），只改标签不动 gold。
**② 空选集原来是绿的**（`--case <打错的 id>` 跑完 0 条 exit=0），现作参数错误退出 2。
**③「任何状态变化都算副作用」会把正确行为判红**（「打开空调，再把后备箱打开」里空调本地执行
是对的），口径必须窄到「需要二次确认的对象被端侧执行」，且判定要用生产自己的 `VAL._need_confirm()`。
**④ 唯一 run 目录光靠时间戳不够**（同微秒两次调用撞 id）。

**现在能引用什么**：L0 全量 **70/70**（零网络、确定性、exit 0）、172 条专项单测、逐条原始断言复现。
**不能引用什么**：`exact_plan_set_rate` / seen-unseen / `planner_capability_hallucination_rate` /
`instability_rate` 的**新口径读数还不存在**——修好口径不等于量过，要等一次固定 provider 的
L1/L2/L3 全量。**stable 规模按唯一输入算只有 104**（原报 113 条里 9 个是重复输入），
`--strict` 正确退出非零；正式 baseline 仍未生成（L3 证据未取得）。

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

**意图落域对抗测试（2026-08-03 修复批次收口后的余项，按优先级）**：

| 待办 | 出处 | 说明 |
|---|---|---|
| ~~**尺子自身 3 P0 / 7 P1 / 2 P2**~~ | 2026-08-03 独立评审 | **已收口（尺子硬化批，逐条对照见评审 §7）**：12 条逐条修 + 逐条反向构造测试，专项单测 120→172，生产侧零改动。**留下的不是修不动的，是量不到的**——见下面三行 |
| **新口径读数不存在（最高优先）** | 评审 §7.3 | 口径修好了但没量过：`exact_plan_set_rate` / seen-unseen / `planner_capability_hallucination_rate` / `instability_rate` 都要等**一次固定 provider 的 L1/L2/L3 全量**才有数。在那之前**任何这几个指标的数字都不可引用**，包括 findings §5.4 的旧全量对照表 |
| **23 条改标 seen 后 unseen 覆盖变薄** | 评审 §7.2-①、§7.3 | 指纹闸抓出 13 条 `unseen_transfer` 的原话字面就在 `skills/exemplars/*.yaml` 里（family 闸对它们全绿），连同 family 闭包共 23 条改标 seen。补法是**新写真正没进过知识的话术**，不是把标签改回去；受影响的是 stale-history invariant、weather/news/trip 三族、`nn-find-go` 边界四条 |
| **两条消融 arm 未在真实失败上跑过** | 评审 §7.3 | `cloud-direct`（绕 Edge）与 `planner-only`（不恢复会话状态）已接通并有 layer 归属守卫，但要 live 才跑得起来；在跑起来之前 `EDGE_DIVERGENCE` / `STATE_RESTORE_DIVERGENCE` 只是可达，不是验证过 |
| **`parking` 缺「查停车费」能力** | findings §6.1 | `parking-payment` 只有 `parking.pay` 一个 capability，「我想先知道多少钱」系统答不了，模型被迫选唯一那个（`require_confirm=true` 兜住了钱不会自己出去）。修法是补 `parking.query_fee` 读能力，按 CLAUDE.md §3 流程走，属新增能力 |
| **「有点看不清路了」该开大灯还是开雨刷** | findings §6.2 | `ex.colloquial.dark` 维持 `candidate`，等泓舟拍板。⚠ 澄清开关兜底翻 on 后这类天然歧义句更可能走澄清卡，定 gold 时要把 `decision.allowed` 一起定 |
| **`stable` 规模实为 104 < 120** | 规格 §21.8 / §22.2 | 原来报的 113 条里有 9 个是重复输入。`validate_suite_counts` 现按**唯一输入**判，`--strict` 正确退出非零。补齐要新写案例，**不能靠改口径** |
| **L3 证据仍未取得** | 规格 §21.8 | 既有 e2e 运行器在本机 `lease_protocol` / `identity_cleanup` 失败。**这是 e2e 运行器的账**，不属对抗套件。资格闸本身已修好（不再依赖「碰巧写不进去」当保护） |
| **发现轨主跑仍是 `--ablations off`** | 规格 §21.8 | 红灯只有首偏离点、尚未逐条建立因果证据。trace 已接通主入口（校验前候选 / Hint 前计划**免费**就有），但消融要 live |
| **`instability_rate` 3.1% → 4.1%** | 2026-08-03 修复批次 | 本批唯一明确变差的指标。新增抖动集中在新恢复/新增的边界用例上（它们本就站在两域分界线）。⚠ **这两个数是旧口径**（分母含只跑过一次的证据单元）；新口径分母只含真的重复过的单元并另给 `repeat_coverage`，两组数**不可直比**，要等新的 live 全量 |
| **seen 掉的那 1 条** | 2026-08-03 修复批次 | `bd.ns-poi-road.right.seen`「路上怎么样」→ `safety.driving_advice`（gold 要 `safety.road_condition`）。**不能声称与本批无关**——新增常驻 policy 与 `_CLARIFY_SECTION` 判据改变了每一次规划的 prompt。等消融归因 |

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
