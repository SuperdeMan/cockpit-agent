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

### 4.0 当前快照（2026-08-14）

意图落域对抗测试按这个顺序接手：运行手册
[`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md) → 最终验收
[`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`](docs/reviews/2026-08-04-review-intent-adversarial-finalization.md)
→ 逐批证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§26**。
历史流水只查 [`docs/agents-history.md`](docs/agents-history.md)，不要再抄回本文件。

**最新后端全量基线**：`python -m pytest --import-mode=importlib`
**5559 passed / 14 skipped**（2026-08-15 P3 簇批后实测，单独跑 13m36s）。较留档
5518 净 **+41** 逐条点上号：**+3 是基线陈旧**（5518 实测于 ca933f3，其后 `de6128f`
B1-3 nearby 又加 3 条没刷新——「对不上先怀疑基线陈旧」§4.3 纪律第二次应验，
worktree 对比取证）+ 本批 38（G8 test_route_session_focus 8 + test_reroute 13、
G4 extract 4 + pipeline 5、G9 extract 3 + pipeline 5；989-951 分套件对比数吻合）。
⚠ 全量执行中一条台账条数锚（`test_boundary_ledger_maps_...` 27→29）按其自述仪式
红了一次——「裁定加了，兑现物加了吗」——兑现物已证后补断言即绿，不是回归。
上一基线 5518 的构成见 history **§34**。⚠ 本次实测 scripts/tests/test_e2e_stack_lease.py 12 条红
=与并行 journeys 的 **stack lease 冲突假红**（那些测试模拟 runner lease 树而
journeys 真持有 lease），隔离复跑 61/61+2skip 全绿——与既有「并行 Docker build 时
test_e2e_wrappers_ci 假红（隔离复跑 6/6）」同族：**全量要单独跑，不与任何 e2e/build
并行**。前五跳：5471→5500 见 §33、5464→5471 见 §32.1/§32.2、5457→5464 见 §32、
5440→5457 见 §31、5408→5440 见 §30。
⚠ Windows GBK 宿主是本仓常驻放大器，新写子进程/出站验签代码先想编码两端；
本机会话若带 `PYTHONIOENCODING`，scripts/tests 的拉子进程用例会 188 条假红
（§4.3 既有纪律，2026-08-13 又实测一次——全量必须在干净 env 跑）。
⚠ 同族第二形态（2026-08-14 实测烧掉一次全量）：**`python` 不在 PATH 的 shell 里跑
全量会 192 条假红**——e2e manifest 校验要求 `python` 可执行存在（`case
e2e_protocol_smoke.command executable does not exist: python`），scripts/tests 整族
连坐；判据同款「红了先问是不是前提变了」，把解释器目录挂上 PATH 后同批 293 条全绿。

**2026-08-14 EVA 指令集二轮对标五批**（缺口分析获批当日实施，流水 history **§33**、
方案 `docs/design/2026-08-14-eva-round2-capability-gaps.md` + 实施计划同日档 §7）：
批 A 存量七条（nearby 评分先排后截／trip「第一站」序数／主动治理器跨 owner 不合并／
死槽位摘除／conventions §9.18）；批 B 导航五刀（`arrive_by` 时限求解+ETA 量化+出发提醒
反向环／顺路候选真沿途 45% 采样／landmark 俗称+自然地物／`route_pref`→高德 strategy
——该参数此前全仓从未使用／多途经点保序）；批 C 记忆消费面（proto `subject`/`polarity`
+ 四个确定性消费出口，修 nearby **假个性化**：口味检索前置+负偏好软降权+subject 并取）；
批 D 未来事件→询问式提醒建议（`reminder_card` offer 态，零执行权不破）；批 E 类目扩展
（动物园族）+「餐饮」默认仅饮食信号下成立+氛围软重排。catalog 锚点 11866→**11928**；
HMI node **258** + Vite build；四门禁绿。**真栈已验**（同日 `make up --build` 30 容器
+ EVA 语料 13 轮 WS 探针，读数与批 F 三修见实施计划 **§8** / history **§33.1**）：
样板轮「导航去东方之门，路上买杯咖啡，五点前要到」一轮给出时限判定+真沿途候选+
逐家 ETA+记忆不走高速叠加；「老婆喜欢粤菜」subject 链抽取→消费全通。批 F 三修=
demo 咖啡 hint 补导航语境让路词（整条计划曾被 replace 成 demo 下单）/route.* 记忆
消费撤销极性过滤（方向在谓词名里）/轨迹写入补挂 search_poi 分支（挂点枚举第三次
应验）。当日晚接手会话已收口两项：目的地接地就近包含误伤家族（R1 二期，见 §4.1 状态段）
与 G7 询问式真栈补验（抓修抽取 prompt 日期锚缺失+offer 显示时区错一天两个真 bug，
`cc7e5ba`——G7 通道真栈首次亮相「我记下了…要到时候提前提醒你吗？」）。

**2026-08-15 EVA 二轮余项 P3 簇收口**（六提交 `36a0ff2`…`03f7e33`，三独立 RFC +
待议项，流水 history **§36**）：G8 导航会话状态（`_route_session` 保留键→
`Focus.active_route`→`navigation.reroute` 增量改道，「途经点不去了/换条路/改去Y」
有对象可指；conventions §9.1 第五行）、G4 主题行程（`build_theme_pool` LLM 提议+
高德接地入池，「跟着《X》游Y」通了）、G9 trip 跨城市（`Trip.cities` 保口述序+
跨天衔接 leg——充电编织首次覆盖跨城段）、G7 双通道抢话边界收口（陈述 vs 请求
入台账）。对抗语料 579→**591**（第六至九次适用，恰好用满）、catalog **144 条/
12418 字符**、架构 **v1.25**。**仍未做**：G10 订座票务（搁置，诚实桩）、
谓词别名清洗（条件触发未命中）。

**2026-08-13/14 商户链路收口聚焦实测**（demo-3ukshz 二轮 + 打磨 + searchType
勘误，逐批流水 history **§32/§32.1/§32.2**、方案与勘误 设计文档 §5/§5.1）：
最终态一句话——瑞幸：聚合菜单主卡（多种子 ≤12 款、「在售不止这些」口径）、
预览规格 chips（只上 `_SPEC_GROUPS` 四族）、改规格真栈生效（冰→热）、换店/取消；
麦当劳：**「附近的麦当劳」真栈答出高新中五道餐厅**（109 款、8 分类导航）——
「仅碧海君庭」曾是错误结论，真因是 `searchType` 写死 1=**搜索收藏餐厅**（勘误
§32.2；判据：**「接口返回了数据」≠「接口在按你以为的语义工作」**）；
`_refused` 拒绝句由聚合器确定性附加（不进 LLM，恰好一次）；焦点 `last_places`
扩第四个安全标量 `city`。聚焦回归 cloud+bridge+nearby **1261 绿**、
HMI **258/258**+Vite build、四门禁全绿（catalog **11866**）。

**2026-08-13 demo-mkemhn 六批聚焦实测**：mcp-bridge **404 passed**（+8）、
`orchestrator/cloud` **786**（含锚定门控/限龄/线契约归一/位移防御新守卫）、
nearby **56**（+4）、HMI node **257/257**（+3）且 Vite build 通过；四条 blocking
门禁全绿（L0 strict discovery 618 条/579 唯一输入恰好用满、gate 25/25；范例域错配
2.4% 持平）；真栈抽验 4/4——无位置不再报异地门店、纯发现句诚实降级、
「选择瑞幸门店：X」一轮直达菜单、营养查询给最接近条目。

**2026-08-13 商户 badcase 批聚焦实测**：mcp-bridge **396 passed**、
`orchestrator/cloud` 焦点/执行器新增回归 6 条、HMI node **254/254** 且 Vite build 通过；
CDP **C10**（只读选品卡，不创建订单）端到端通过——选品卡 3 款 / 商品图 2/3 张
**真加载**（`naturalWidth>0`，不是断言 `src` 存在）/ 帧文本正确且非确认帧 /
**选品后换话题仍正常**。C1/C3/C5/C6 全绿（首轮红、复跑即绿 = registry 重注册期假红）。
⚠ C2a/C10 会被高德间歇不可达阻塞——本机到 `restapi.amap.com` IPv4 超时、IPv6 无路由，
百度 90ms 正常，**不是配额也不是代码**；用例把这种情况报成「前置降级，非卡片结论」。

（2026-08-12 商户批的逐项读数已归档 history **§29/§30**，不再在本节留对账段。）

**2026-08-11 分组实测**（B5/B6 后）：edge **579**、cloud **721**、registry **65**、
agents **993**、`runtime/tests` **109**、observability **73**；端侧 smoke **13/13**；
L0 门禁 **2/2 exit 0**（discovery 81/81、gate strict 25/25）；能力门禁 exit 0；
`eval_fast_intent` 57/57、`eval_route_hints` 78/78、skills / 范例门禁 PASS
（范例域错配 4/160=2.5%）；新增离线回放 `test/eval_actionability.py`
（零 LLM、零网络，**不进 CI blocking——它是取证脚本不是准入闸**）。
**Go 侧与前端数字未随本批重测**：Go `go build ./...` + `go vet` +
`go test ./gateway/deployprofile` 全绿、HMI `node --test` **225/225**、
Dashboard vitest **17/17** 分别是 B3/B4（2026-08-11）与 2026-08-09 实测——
B5/B6 只动 Python 侧（`orchestrator/cloud/` 与 `observability/collector/db.py`）。

**真栈演练（2026-08-11，`make up` 全量重建 30 容器）**：`e2e_ws` / `e2e_obs` /
`e2e_context` / `e2e_process_region` / `e2e_proactive` / `e2e_ledger` / `e2e_scene`
**全 PASS**；`turns` 表实测 `plan_mode` 三档齐全（`toolcall` / `toolcall_salvage` /
`toolcall_degraded`）、`actionability` 列真实写入（`execute|0.95` 等）——B6 §5 第 1 条
「shadow 进 obs 且可检索」由真栈而非单测坐实；既有 302 turns + 2377 spans 在
加法式迁移后完整保留。⚠ **`--lane ci --full` 只选 1 个用例**（`e2e_protocol_smoke`），
引用它的 exit 0 时别当成 e2e 全绿。`e2e_verify` 5 条红已逐条定性为**前提失效**（见 §4.2）。
**本次演练抓到一个 B3 埋的真缺陷**：collector 与 proactive 的 Dockerfile 没
`COPY runtime`，加闸后**一重建就起不来**（见 §4.3 同名条目）。

**CI 已有四条 blocking 门禁**：skills 契约、范例契约、L0 对抗 strict
（`scripts/check_intent_gate.py`，B2）、**能力完整性**（`test/eval_capability_integrity.py`，
B4）。四条都是确定性检查、零 LLM、零网络——「跌破基线告警不阻塞」的哲学只适用于会随语料
漂移的意图基线，与这四条准入不同，不要合并。

| 意图落域证据 | 当前可引用事实 |
|---|---|
| L0 discovery | **81/81**，618 条 / **579 唯一输入**（2026-08-13 起 bounds [450,**579**]，仍**恰好用满**。五次递进 560→564→568→570→571→579，逐次占用理由写在 `suites.yaml` 头部：`luckin.menu` 覆盖、跨轮锚定双向对照、`mcd.nutrition` 覆盖、跨域边界台账双向各 2 例、demo-mkemhn 两条新边界双向各 2 例；没有删除旧尺子压数字） |
| gate 规模 | **139 stable / 129 唯一输入**，L0 strict **25/25，exit 0** |
| 对比模型正式 baseline | [`baseline_intent_adversarial.json`](docs/reviews/eval/baseline_intent_adversarial.json)；干净 `f0af9c0`，锁定 `deepseek:deepseek-v4-flash`，由当前 L3 原始字节/摘要/时间/精确路径契约重新取证并写入。**未随 `32e8718` 重取**——它仍是 DeepSeek 在 `f0af9c0` 的证据 |
| DeepSeek 完整 gate | **147/147**：L0 25、L1 117、L2 4、L3 1；exact **121/121**，raw 幻觉/校验后逃逸/不稳定均 **0/121**；L1/L2 各 **2 个独立进程 × 每进程 3 样本**（`f0af9c0`） |
| MiniMax 主模型 gate（`32e8718`，2026-08-10） | **141/147**；exact **116/121**、required **99/103**；raw 幻觉 **3/121**（原 8）、逃逸 **0/121**；不稳定 **4/121**（原 6）；`pass 141 / unstable 4 / stable_fail 2`，资格仍 `eligible=False` |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline 的 invocation 新鲜、exit 0，只证明该授权 case/claim。2026-08-10 新增 **A1-5**（weather→去处推荐，claim `adaptive_replan_continuity`）两趟独立各 **1/1**，但它服务的 case 仍是 `reviewed`，不进 gate 选集 |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 fallback **0**；MiniMax **11/122**，其中未声明 **2**（原 4） |
| 工具通道（协议层，**可跨 provider 比**） | 走成 `toolcall` 的比例是 **provider 属性**：`minimax:MiniMax-M3` 同用例 **13/27（48%）**、跨域 20 条 **9/20（45%）**；`deepseek:deepseek-v4-flash` 两组 **35/35（100%）**（p≈0.0002）。⚠ 代价只在**需要模型自己填结构化字段**的多阶段计划上兑现——那 20 条 stable 上两档通过率都是 20/20（findings §24）。**2026-08-10 起 `PLANNER_TOOLCALL_SALVAGE_RETRY=on` 默认开**：gate L1 双臂实测把 MiniMax 从 **51.3%（60/117）抬到 85.5%（100/117）**，+34.2pp、p=2.3e-08、重试成功率 ≈70%，代价墙钟 +38.5%（findings §26.5）。**引用 45~48% 那组数时注意它是 off 档口径** |
| 代码回归 | 分组数字见上方 2026-08-11 实测段（`orchestrator/` 合计 **1300** = edge 579 + cloud 721）；Skill / Exemplar（**283 条 / 22 域**，2026-08-14 实数——clarify 型机制已就位但**刻意零生产范例**，见 §4.2）/ L0 门禁均通过。端侧能力面 **80 条**（vehicle 76 + media 4）/ VAL 车控对象 **67**。⚠ 2026-08-11 起 `VEHICLE_INTENTS` **不再手写**，由 `commands.yaml` 各对象的 `edge_intents` 派生（B4），数字不变但改的地方变了 |

⚠ **上表 MiniMax 行是 `32e8718` 读数，与当前代码已差好几批**（此后合入了 clarify 型范例
机制、salvage 重试默认开、B1–B4 四批）。**当前 SHA 没有对应的全量 gate 读数**——要引用
主模型总分就得重跑一次完整父 bundle。`5e8247d` 那批是换池态、案例集已不一致，**不要挪用**。
三批 141/141/140 **不是同一批红灯**（每批点名的 unstable 都换了一组），逐批过程记在
history §17–§19；沉淀下来的判据是 §4.3 的两条：读报告先看是哪几条、`eligible=True` 要压
整体底噪而不是修点名的那几条。

首份正式 baseline 已存在，但它是 **DeepSeek 对比/参考模型**在固定 provider、资产与代码快照下的
意图理解与落域证据；不证明 MiniMax 主模型、Agent 业务结果、外部 Provider 内容或跨模型平均质量。
MiniMax 在 `32e8718` 那批即使 process/provider/embedding/L3 身份完整，仍因 `gate_failures`、
raw 幻觉、未声明 fallback 与 `unstable_results` 被资格闸拒绝。后续写入仍必须由一次新的完整父报告
明确 `eligible=True`，不得手工改正式文件。

### 4.1 活跃待办（只列仍需行动的）

**journeys 两条红已收口（2026-08-14 深夜，history §35）**：取证后两条定性都反转——
① A3-2 不是回归是**两把尺子打架**：8-02 泓舟量词收窄裁定（「第N条详细讲讲」=列表项
跟进、正确落域 info，对抗语料 cs.more.news CI 守着）与 journeys 旧期望矛盾，7/25 的
绿靠当时未收窄的宽 hint——语料话术对齐既有裁定（改显式调研措辞，NEWS_ACTIVE 桥接
覆盖不丢），真栈+终验绿；② B1-3 不是哪批弄断的，是**同族能力只建了一半**：engine
早按 location scope 把焦点目的地坐标注进 meta，weather 侧确定性消费（B1-2 稳定绿）
而 nearby 从没接、一直靠 planner LLM 填槽的软路径——nearby `_near` 补指代词+焦点
坐标级（与 info 同款信任链），真栈+终验绿。终验全量 journeys（单独跑）：A3-2/B1-3
双绿；A1-5 因三跑 1 绿 2 红实锤为已档案化的主模型方差面（llm_raw steps=[]、salvage
no_action），按其真实性质 regression→target 降级（8-10 新增时标错级，L3 claim 链
不受影响）；换算后回归级 **15/15 全绿**、目标级 19/22（红=A1-5/A2-1/B5-1，均
7/25 绿过或方差族，backlog 观察不立卡）。

**目的地接地「就近包含误伤」家族（R1 第二期）——已实施收口（2026-08-14 当日）**：
按卡先取证再动码。取证改判了方向：D1 候选集内重排被数据否掉（五条红例的本体根本
不在 near 候选集里，修复数 0/5）；红例族扩到千岛湖/东湖；还抓到行政级分支新红例族
（高德 geocode 对多义裸名返回错省区划——「西湖」现状会直接导去**台湾苗栗西湖乡**）。
落地=D2 类目锚词复核（机场/湖/滩，每条有红例背书）+集内双匹配+wide 双匹配+主干级
名字匹配（治「虹桥机场 vs 上海虹桥国际机场」官方名断包含）+区县级就近合理性收窄
（≤150km，市级以上不动）；D4 历史指代直用 episodic 轨迹坐标独立合入。验收：迷你集
**14/14 真高德全绿**（红 7 修复/绿 7 无回归，R1 一期既得全保）、navigation 111、
route_hints 80/80、**真栈复验 5/5**（本卡四条 trace 全接到本体，「上次那个湖」坐标
逐位复用轨迹零重搜）。取证数据/决策/遗留全在
[`docs/design/2026-08-14-dest-grounding-containment-card.md`](docs/design/2026-08-14-dest-grounding-containment-card.md)
**§7**；commit `f3df5bb` / `4e7310b`。

**EVA 指令集二轮对标（2026-08-14，六批全合入 main 并真栈验证）**：缺口分析→五批实施
（A 存量七条 / B 导航五刀 / C 记忆消费面 / D 询问式事件提醒 / E 类目属性）→ 真栈 13 轮
探针 + 批 F 三修复验全通。状态一句话：时限判定/真沿途候选/多途经保序/俗称与自然地物
推断/route_pref→strategy/记忆 subject·polarity 全链（「老婆喜欢粤菜」抽取→消费）均
真栈在案；样板轮与逐轮读数见
[实施计划](docs/design/2026-08-14-eva-round2-implementation.md) **§7/§8**，流水
history **§33/§33.1**。当日晚接手会话收口接地卡与 G7 补验（history **§34**）；
journeys 全量已重跑（35/37，两条稳定存量红已立上方待办）。
**余项 P3 簇已于 2026-08-15 三 RFC 收口**（G8 路线会话/G4 主题行程/G9 跨城市 +
G7 双通道抢话边界，见 §4.0 段与 history **§36**）；只剩 G10 搁置与谓词别名条件
触发，见 §4.2 索引行。

**支付基础设施真实化（四批，2026-08-11，全部实施推 main）**：`d964e1d`（网关
双渠道核心）/ `94f7afc`（parking 闭环+HMI 支付卡+桥 streamable_http）/ `4a5cab0`
（麦当劳/瑞幸真机激活）/ `6f06b41`（端到端体验修复）。方案/10 条裁决/逐批实施
记录的唯一权威：
[`docs/design/2026-08-11-payment-infrastructure-and-merchant-mcp.md`](docs/design/2026-08-11-payment-infrastructure-and-merchant-mcp.md)
§2/§6；契约 [`docs/conventions.md`](docs/conventions.md) **§9.17**（支付网关：
状态机/幂等三层链/token 幂等重取/场景白名单 fail-closed）+ **§9.9**（桥：
transport/补偿两态/pay_url 闭环/speech_mode）；逐批流水 history **§28**。
当前能力状态一句话：停车缴费真栈闭环（e2e_payment 3/3）；麦当劳/瑞幸已在
[`2026-08-12-merchant-mcp-full-flow.md`](docs/design/2026-08-12-merchant-mcp-full-flow.md)
从只读三件推进为受控复合工作流——选店/选品/规格、预览计价、确认后创建未支付订单、
安全支付入口与查单均已实现，瑞幸另有再次确认取消，麦当劳官方无远程取消；系统不执行
最终付款。支付宝沙箱四接口中 precreate 真码/query/close/GBK 验签修已真栈验证。

**支付余项**：① 沙箱「支付→PAID→refund」段——泓舟已扫码但沙箱钱包内支付失败
（支付宝沙箱当日服务级故障），恢复后重跑 `python -u scripts/alipay_sandbox_probe.py`
（自动弹浏览器大码）；② 微信商户号到位后真实联调（代码按 v3 真实实现+签名单测
锁死，未经真环境验收）。商户工作流的量产边界仍是：两家 token/账号均为服务级全局凭证，
不是多乘员独立账号；商户 token 与 `PAYMENT_EXTERNAL_PAY_HOSTS` 必须由运行时安全配置提供，
空配置 fail-closed；未授权最终付款。2026-08-12 真实验证共创建 5 笔未支付订单（瑞幸 3、
麦当劳 2；契约发现与浏览器拒绝路径使数量超过原计划 3），三笔瑞幸均已取消、两笔麦当劳
均由商户自动取消；没有一笔付款。最终浏览器 C9 已分别精确命中瑞幸“已取消”和麦当劳
“订单已取消”；旧版只断言“收到回复”的宽松 C9 截图不算查单终态证据。

**商户 badcase 收口七批（2026-08-13，全部实施推 main）**：`5819ca5` / `c2f8965` /
`c595d99` / `4ba36db` / `50a5ee0` / `10e6074` / `1f16260`。逐批流水与判据在
history **§30**，跨轮锚定方案见
[`docs/design/2026-08-13-cross-turn-store-anchor.md`](docs/design/2026-08-13-cross-turn-store-anchor.md)。
能力状态一句话：真实商户链**在实栈里第一次可达**（`merchant.read/write` 此前全仓无发放入口，
e2e 自己塞 `granted_scopes` 所以一直是绿的）；两家各有只读当店菜单
（`luckin.menu` 3 条搜索结果 / `mcd.menu` 全店菜单，均带价格与商品图，域名走
`servers.yaml::image_hosts` 精确白名单）；「这家店」跨轮可解析（`focus.last_places` +
`PlanContext.focus_places`，provenance 同前缀同下标一字不松）。
营养表已由 `mcd.menu` 改名 **`mcd.nutrition`**——问热量与问价钱是两个能力。
demo-mkemhn/demo-3ukshz 两轮复盘与 searchType 勘误的全部批次见上方
2026-08-13/14 聚焦实测段与
[`2026-08-13-demo-mkemhn-merchant-hmi-hardening.md`](docs/design/2026-08-13-demo-mkemhn-merchant-hmi-hardening.md)
（§5/§5.1），流水 history **§31–§32.2**。

⚠ 本批有**两次自伤**（为躲一句话术把拒绝改成 `NEED_SLOT`，挂起会话吞掉后续每一句；
焦点每轮重建把门店列表抹空），都是泓舟打回来才发现的。沉淀的两条纪律已入 §4.3。

能力状态一句话（demo-mkemhn/3ukshz 两轮复盘后）：位置丢失不再报异地门店冒充
「附近」；候选卡按钮/指名门店在焦点与草稿双过期后**自愈**（escalate 重取门店）
而非死路；掉进 chitchat 的轮不再编造「已找到门店/请确认订单」；预览卡可一键
换店保商品、点选规格；两家菜单各有全量方案（麦当劳分类导航/瑞幸多种子聚合）。
坐标可信链与 S2S 红线零放松。逐批提交号与证据只查 history §31–§32.2。

外部评审六批 **B1/B2**（2026-08-10）、**B3/B4**、**B5/B6**
（2026-08-11）全部实施合入并收口。✅ 冻结令已撤销，可以新增业务 Agent。

各批交付了什么、与方案有哪些差异、验证证据如何——**只读方案文档 §6/§7 的实施记录**
（[B1](docs/design/2026-08-10-b1-execution-safety-stopline.md)、
[B2](docs/design/2026-08-10-b2-gate-ci-branch-governance.md)、
[B3](docs/design/2026-08-10-b3-deploy-profile-fail-closed.md)、
[B4](docs/design/2026-08-10-b4-capability-pack.md)、
[B5](docs/design/2026-08-10-b5-planner-retry-stream-refactor.md)、
[B6](docs/design/2026-08-10-b6-actionability-forward.md)），裁决总览见
[`docs/reviews/2026-08-10-external-review-adoption.md`](docs/reviews/2026-08-10-external-review-adoption.md)。
落到契约面的六条：确认闸下沉 VAL（[`docs/conventions.md`](docs/conventions.md) §9.15）、
部署形态闸（§6 profile 段）、端侧能力声明契约（§9.16）、CI 四条 blocking 门禁（§4.0）、
`retry_policies` 与 `actionability` 两列观测（§8.1）。

⚠ **B5/B6 的触发条件当时并未命中**（B5=加新重试规则/新流式路径前；B6=真实流量分母
或该族再成主要矛盾），是泓舟 2026-08-11 直接指示推进的。两份方案的头部都留了痕——
**别把它读成「条件曾经满足过」**，那会让下一次「条件启动」的分量被稀释。

⚠ **对抗语料唯一输入 571 / 上界 571**——当前余量仍为 0。下次加 L0 语料必须先说明新增
能力或边界为何值得占额度，再有原则地调整 `suites.yaml` 的 `max_cases`；不得删旧尺子压数字，
也不得先加语料撞闸后再补理由。560→571 的四次递进与逐项占用写在 `suites.yaml` 头部。

> 逐批流水在 [`docs/agents-history.md`](docs/agents-history.md) **§15–§27**，逐条证据在
> `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§26**。
> 本节按约定**只留状态、不留流水**——沉淀下来的判据在 §4.3，数字在 §4.0。

### 4.2 延后 / 条件待办索引（不进入当前主线）

> §4.1 只放正在行动的事项；本表保留尚未启动、等待外部条件或明确后置的入口。条件满足后先晋级 §4.1 并补完成判据；历史流水仍只查 `docs/agents-history.md`。

| 主题 | 当前状态 / 启动条件 | 权威入口 |
|---|---|---|
| **EVA 二轮余项**（搁置面） | P3 簇三项已于 2026-08-15 三 RFC 收口（G8/G4/G9，history §36；「留档待议」的 G7 双通道抢话同批入台账 `chitchat-reminder.statement-vs-request`）。仍留两项：① **G10 订座/票务维持搁置**：诚实桩现状可接受，有合适 provider 时按 mcp-bridge 准入流程走，不为对标造假；② 抽取谓词别名散置老账（u1 存量 coffee.brand/consume.coffee/beverage.coffee 并存）——`_PRED_CANON` 只治新增不治存量，出现消费方误伤再启动数据清洗 | [缺口分析](docs/design/2026-08-14-eva-round2-capability-gaps.md)、三 RFC `docs/design/2026-08-15-g{8,4,9}-*.md` |
| **端侧车控能力台账余项**（B4 产出） | 门禁台账 `orchestrator/edge/knowledge/capability_exemptions.yaml` 共 **39 条**，四类：媒体别名 8 / 云侧域对象 11 / 座舱 UI 面 6 —— 这 25 条是「本来就不该有端侧 intent」，**不是欠账**；剩下 **14 条是欠账、只是本批不做**（`air_purifier`/`auto_hold`/`bluetooth`/`epb`/`equalizer`/`frunk`/`hotspot`/`key_tone`/`low_beam`/`navi_broadcast`/`surround_view`/`wifi`/`driving_mode`/`battery`——VAL 侧多有分支或话术，只是端侧没给 fast_intent 规则与意图名；云端计划仍可经 `action_to_structured` 走到）。这 14 条里有 **3 条待人裁**：① `frunk` 是 `require_confirm=true` 的危险对象却没有任何端侧 intent、与 `trunk` 不对称，**是刻意不给语音开还是漏了**；② `driving_mode` 与 `power_mode` 语义高度重叠，可能是同一件事的两个对象名（若重复应合并——别让 planner 面对两个分不开的工具）；③ `battery` 查询要不要补端侧意图。新增端侧意图时**从这 14 条里挑**并同步删台账条目 | [B4 方案](docs/design/2026-08-10-b4-capability-pack.md) §6.5、台账文件本身 |
| **P3b operate 抽取与放量** | 原表里并列的两个具体缺口（除雾能力缺席、「穿衣指数→股指」）已于 2026-08-10 修完，详见 history §23.1/§23.2，本行只留放量条件。**放量门槛不变**：operate 抽取 + 真实错对象率 <0.3%。⚠ 压这个数的手段是 **R4.1b P1 执行侧对象化**（让 VAL object 数从当前 67 继续长），**不是调阈值**；且当前 PoC 没有真实流量，这个数只有观测面、还没有分母 | [M5 P3 收尾](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) §P3 收尾 |
| live 路由回归进 CI | hint 退役后的召回保护目前是 live 人工车道，不是 CI 阻断；有稳定凭证、预算与 provider 方差处置后再接 CI | [旅程体系](docs/design/2026-07-14-journey-e2e-test-system.md) §4.3、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| `route_hints` 继续退役 | 当前实数 **11**；`mcp-bridge#0` 必须先过专项安全回归。旧三条单档候选不得按历史索引直接执行；当前只用 MiniMax 主模型 / DeepSeek 对比，MiMo key 失效不阻塞主线，切换 provider 时重新全覆盖取交集 | [M5 P2](docs/design/2026-07-28-intent-accuracy-data-flywheel.md)、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| M5 后续杠杆 | catalog 检索化当前是“有意不做”；16k 预算再次裁剪或保护集显著变瘦时重评。gold→范例现走 CLI；P4 仅在范例 ≥2k 且 N1 平台期 ≥2 周时启动 | [M5 P2/P4](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) |
| M-B / M-C / M-D 明确后置项 | 13 张验收主卡已清零；跨域删除 saga、完整隐私管理/迁移仪式、持久治理扩面与 MCP 生产化覆盖按 GDPR 完备性、量产迁移或新消费方触发 | [总体验收](docs/reviews/2026-07-26-acceptance-review-m0a-m4.md) §10.2/§11.2/§12.2、[OwnerKey 契约](docs/conventions.md) §9.13 |
| M2 / M3 产品化边界 | Ledger 自动续跑/任务中心，以及主动治理持久化、偏好学习、dashboard、远距 geocode 与真实商户均为显式未做；出现真实消费方或产品阶段后另立卡 | [M2 RFC](docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md) §9.6、[M3 RFC](docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md) §10.7 |
| M4 声纹 / 视觉余项 | 真麦校准、语音注册入口、模板漂移治理、视觉多轮与 `vp_*` 指标消费仍未收口；进入真机量产验收或 v2 前启动 | [M4 RFC](docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md) §11.5/§11.8、[声纹契约](docs/conventions.md) §9.11 |
| **可执行性判定 canary**（B6 shadow 的下一步） | **shadow 已落地并取证**（2026-08-11）：裸对象族召回 2/2、假阳性 4/574（0.70%），planner 对照 `nq.landmark.bare` 11/20。**canary 需泓舟单独拍板**（B6 §5 第 4 条，本文件只授权到 shadow）。在那之前该做的是 §2.3 明写的**对照实验**：拿 shadow 记下的分歧样本人工裁定，胜率显著才谈接管——「诊断出一个洞不等于这个洞就是病因」。⚠ 唯一漏判 `ex.colloquial.dark`「有点看不清路了」**不属于裸对象族**（与金标执行的「有点热」形态逐字同构，没有形态差异可用），它要的是「唯一默认动作是否存在」那个 catalog 特征，是 canary 前的独立一步 | [B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) §6.3/§6.7、回放 `test/eval_actionability.py` |
| 裸对象澄清族（**已知无解状态，不是待办**；⚠ **B6 shadow 已给出第四条路，见上一行**） | **三条路径已全部走完，该族目前无可用修法。** `nq.landmark.bare` 合并 11/20≈55% 的高方差边界句；`nq.landmark.explicit` 自己每条断言都过、被 relation `clarify_flip` 连累；同族第三条 `nq.city.bare`「上海」reviewed 未进池。路径 1「写 guide」实测**有害**（§18：4/10→1/10→退回 7/10，p≈0.02）；路径 3「换出预选池」执行并全量验证后由泓舟裁定回退（§19.5）；**路径 2「范例 schema 加 clarify 型」2026-08-10 已实现并合入**（schema+渲染+门禁+契约+11 条测试），但同批实测出它**治不了本族**：裸专名之间 IDF-Dice 全 0.000（检索是内容通道，而裸对象澄清是**形态判据**——这也解释了路径 1 为何有害），且澄清型范例天然与其「补全版」近重复（两条候选一条抢到明确请求 top-1、一条只靠 0.03 差距，全部撤回，**故仓库刻意没有 clarify.yaml**）。**下一次要动它得换判定形态（形态/句法特征），不是再加一层检索式知识** | 立卡整段写在语料 `test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml` 的 `nq.landmark.bare` 头上；[findings §18/§19/**§25**](docs/design/2026-08-02-intent-routing-adversarial-findings.md)；机制契约 [`skills/exemplars/README.md`](skills/exemplars/README.md) §clarify 型 |
| B3-2「广州塔」地标解析（高德侧） | **不进落域账**。2026-08-10 同坐标直连复测：geocode 对（兴趣点／广州塔真坐标）、`near=None` 重搜 top1 就是广州塔，只有带深圳偏置的关键词搜索会顶出「广州仄仄科技有限公司」2.4km。即 R1 去偏置重搜机制本身是对的，但它**要多打一次高德**，跑批并发下正是最易被限流的那一次；掉一次退回就近弱匹配，掉两次成「暂时无法确定」——两次红的两种形态由此解释。归高德 QPS 一族，出现真实用户投诉或做并发治理时再启动 | [复杂意图·地标/停车](docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md)、判据与复测记在 `test/journeys/target_b.yaml` B3-2 注释 |
| `e2e_verify` 案例①：**前提变了不是修坏了** | 2026-08-11 全新重建的干净栈上 5 条红，**逐条定性为测试前提失效**，不是回归。该用例的前提写在它自己的注释里——「单句『打开空调』被端侧快路径直接执行，**必须用混合多意图句**才能规划出云侧 hvac 步」。2026-08-11 实测：混合句「帮我把空调打开，再查一下附近有什么好吃的」的 `route.mixed` span 记着 `local_actions:1`，**端侧把 hvac 那半自己执行了**（`val.execute`），云侧只剩 `nearby.search`——于是 `state_match` 那两条断言恒不可能满足。另两条 `schema` 断言失败是**测试查的 trace 与实际落的 trace 不是同一个**（按文本直查该 trace，`step.verify{mode:schema,verdict:sat}` 明明在），第 5 条是前四条的连带。**对账链本身是好的**（案例②/③ 全绿）。修它要先决定断言该指向什么（换一句仍能规划出云侧车控步的话术 / 或改测端侧 VAL 面），属独立一卡；B5/B6 结构上不可能造成它——那个决定发生在云端被调用之前 | 证据：本条 + `test/e2e_verify.py` 案例①注释、history §27.6 |
| B6 §4 Capability Contract 远期字段 | **逐字段独立触发，无当前行动**：`input_schema`/`output_schema`（槽位类型错误成稳定 badcase 族或 typed Executor 立项）、`compensation`（撤销/补偿类产品需求）、`version`/`deprecation`（第三方 Agent 生态启动）。`replayable`/`idempotency` **已由 B5 §4.2 就地收口**——不新造 `command_id`，复用 `_fingerprint` | [B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) §4 |

### 4.3 读数纪律

- **验证多轮系统必须跑「失败态之后再说一句」和 ≥3 轮**（2026-08-13 两次自伤的共同成因）。
  happy path 与干净会话证明不了会话状态是对的：挂起黑洞只在**拒绝之后**才出现，
  焦点覆盖只在**第三轮**才暴露（第二轮恰好紧邻搜索轮），CDP 用例也只验到「卡片渲染出来」
  而 bug 活在卡片之后。同族推论：**测试若替被测系统提供了某个前提，那条前提就不再被验证**
  （`e2e_merchant_mcp.py` 自己塞 `granted_scopes`，于是「scope 从没被发放过」绿了两个月）。
- **拿不到结果时先分清「资源没了」和「这次没成」。** 2026-08-13 把高德的间歇性不可达
  误读成「配额被探针打光」，并据此在提交信息里写了「没跑通真栈整轮」；实测本机到
  `restapi.amap.com` IPv4 超时、IPv6 无路由而百度 90ms 正常——是环境不是配额也不是代码。
- `110/116`、`113/117`、raw `6/117`、旧 seen/unseen 对比均是历史口径/批次，**不得当当前结果**。
- `domain_hit_rate` 只要求命中交集；`exact_plan_set_rate` 要求必要组齐全、禁选为空、无额外项，二者不可直比。
- L1/L2 的正式 gate 必须是两个独立进程、每进程 3 样本；同进程 repeat 3 不能替代第二进程。
- baseline 的 `repeat_coverage=121/122` 是 L1/L2 121 个 live 单元重复、L3 按设计只跑一次；`process_policy_complete=true`，不是缺 shard。
- `fallback_plan_rate` 非零不自动失败；只有语料显式声明的 A8 能力缺席族可接受，未声明 fallback 一条即挡 baseline。
- Live 跑批前按运行手册 §2 同时设置 provider、model 与 embedding 三项；少一项整批证据作废。
- `pytest test/` 不是项目基线命令；换选集会改变 import 顺序与分母，不能拿来替代根跑。
  （2026-08-10 起它**不再有红**，但「不是基线」的理由是分母不同，与红不红无关。）
- **live 跑批期间不许动工作树**：资格闸逐进程校 `worktree_clean`，corroboration 进程比
  primary 晚起，跑批中途改文件会让它单独 fail-closed（2026-08-04 那批踩了两次）。评测产物写
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
  **整批重来**，别接着跑还没开始的那一批（§19.4，2026-08-10 亲自踩的）。
- **净增量要跟同一个 SHA 比，不能跟文档里那个数比**（2026-08-10 起两批都验过）。基线行
  会因为「实测之后又有提交加了测试却没刷新」而落后；对不上时**先怀疑基线陈旧**，再怀疑
  自己多出来了东西——反过来会凭空制造「N 条不明用例」的假问题。**每次刷新基线都要能把
  净增量逐条点上号**（B3/B4 那批 +132 = 六个新文件 55+27+5+24+18+3）。
- **校验要复刻消费方的解析，不发明通用真值语义**（2026-08-11，B3）：`AUTH_REQUIRED=1`
  在 Go 侧（`EqualFold(v,"true")`）是**关**，`GRPC_TLS=ON` 大写在 switch 精确匹配下也是
  **关**。一个「看起来是真」的检查会在这两处报绿而开关根本没开。同 CLAUDE.md §6
  「防御要防到真正会被拿去判定的那个值」。
- **静默回落就是要消灭的形态**（2026-08-11，B3）：未知 `DEPLOY_PROFILE` 值直接拒启、
  不回落 dev——拼错档位却按零校验跑，比没有这道闸更危险（运维以为自己在跑硬校验）。
  推广：**任何「认不出就用默认值」的分支，先问默认值错了会不会没人发现。**
- **「写进 anchor / 写进公共位置」不等于「每个消费方都有」**（2026-08-11，B3，同族第三次）。
  `DEPLOY_PROFILE` 加进 compose 的 `x-python-env` anchor 后，registry / edge-orchestrator /
  proactive 三个服务**根本没用那个 anchor**，闸形同虚设——纸面推理与 `docker compose config`
  都没抓到，**容器演练当场抓到**。前两次是 shop 域零范例（门禁只读 manifest，而 mcp-bridge
  能力由 servers.yaml 启动期合成）。修法是把覆盖面变成断言，不是「下次记得」。
- **扫不全的结构断言比没有更糟——它会让人去改本来是对的代码**（2026-08-11，B3）：
  上一条那个覆盖断言第一版只认绝对 import，`from .server import serve` 断链，
  14 个 Agent 全被误判成「够不着闸」。**写完扫描类断言先验证它扫到的集合非空且合理。**
- **不加第二份表达同一件事的声明**（2026-08-11，B4）：方案要 `risk` 落字段，落地改成派生
  ——B1 刚把「危险与否」收敛成 `require_confirm` 一个权威，第二份声明必然漂移；且它唯一的
  消费方（B6）尚未开工，先落即死字段。**对以后任何「再加个字段表达同一件事」都适用。**
- **一条断言抓什么，以实测为准不以命名为准**（2026-08-11，B4）：`edge_intents` 那条「挂错
  对象块」的断言**抓不到**动作段拼错（`trunk.opne` 照样解得出对象 `trunk`），那一族是
  「验证定义」车道抓的。红灯验证时要看**是哪条车道红**，不是只看 exit 码非零。
- **分母挑得越干净，假阳性越好看**（2026-08-11，B6）。B6 分类器的假阳性首版按「有 plan
  金标的轮」取分母，读出漂亮的 **0/472**——因为那个口径把 `ei.*` 端侧 ingress 用例
  （「暂停」「锁车门」，恰恰是零谓述标记的裸动词短语）整批挡在了分母外。改成「除澄清
  金标之外的全部轮」才是 4/574。**报假阳性率之前先问一句：被排除的那些是不是正好是
  最难的那批。**
- **确定性判据的 p 值分母是人为的**（2026-08-11，B6）：形态分类器对同一条输入判 20 次
  还是 2000 次都一样，重复不是独立样本。与 planner 的 11/20 摆一起算出 p=1.23e-3 只是
  为了同表可读，**真正的内容是「零方差命中 vs 45% 漏判」**，不是那个小数点。
- **「先验证覆盖再读结论」对差分等价同样成立**（2026-08-11，B5）：21 条场景在表驱动前后
  逐字一致，可首版语料只触发了 13 条策略里的 11 条——那两条上这 21/21 一个字都没说。
  补足语料后才 13/13。**等价性证明的强度上限是它覆盖到的那部分。**
- **同一件事的两份声明会在落地时暴露**（2026-08-11，B5/B6，B4 那条判据的第二三例）：
  B5 §4.1 草图既把 `FINAL_RECEIVED` 列成枚举第四态、又传 `got_final` 形参；B5 §3.1 的
  `metric_tag` 有 11 条会与 `name` 逐字相同、`preserve_previous` 与 `stage="accept"`
  说同一件事。**方案里长这样不算错，落地时照抄才是**——落地前逐字段问一遍「它有没有
  第二个真消费方」。
- **「代码里 import 得到」和「镜像里拷进去了」是两件事**（2026-08-11 真栈演练，B3 判据
  在 Docker 层的复发）：B3 给 `observability/collector` 与 `proactive` 的入口加了
  `from runtime.profile import enforce_deploy_profile`，`test_profile_coverage` 那条
  「够得着闸」的断言照过——**它读的是仓库里的源码**。可这两个 Dockerfile 没有
  `COPY runtime`，镜像里根本没这个包，**一重建就 ModuleNotFoundError 起不来**。
  既有容器跑的是加闸之前的镜像，于是这处断裂在 **40 小时里毫无症状**。
  修法是把构建闭包也变成断言（`test_service_image_contains_the_runtime_package`），
  不是「下次记得」。**推论：只改 Python 不代表不用重建——共享包新增消费方时，
  先查该服务 Dockerfile 的依赖闭包。**
- **反例最好从被测系统自己的知识库派生**（2026-08-11，B6）：「特征里不许有领域词汇」
  这句话手抄一份词表就等于没写（迟早与知识库漂移）。改成从 `commands.yaml` 派生
  对象 id / display_name / edge_intents 段来比对，**首跑就抓到「导航」**——它同时是
  谓词和 VAL 对象名。同 B4「`VEHICLE_INTENTS` 从手工集合改为知识库派生」。

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
| 端侧车控能力（知识库 / 意图 / 话术）| `python test/eval_capability_integrity.py`（六维逐对象，CI blocking）+ `python scripts/check_intent_gate.py`（对抗覆盖 strict）+ `python -m pytest orchestrator/edge/tests -q`（含意图面迁移探针）。SOP 见 §7.1 |
| 新增服务（compose 里加一个自建镜像）| `python -m pytest runtime/tests -q`——它断言每个自建服务都拿到 `DEPLOY_PROFILE`、入口够得着部署形态闸、**且该服务 Dockerfile 真的 `COPY runtime`**；**加进 `x-python-env` anchor 不等于配上了**（有服务不用那个 anchor），**源码 import 得到不等于镜像里有**（collector/proactive 就这么断过 40 小时）|
| 给某个服务的入口加共享包 import（`runtime.*` / `observability.*`）| 先查**那个服务 Dockerfile 的依赖闭包**——少一行 `COPY` 就是「一重建就起不来」，而既有容器跑着旧镜像时**完全没有症状**。`python -m pytest runtime/tests -q` 会抓 `runtime` 那一类 |
| Planner 重试/守卫规则（B5）| **先改 `orchestrator/cloud/retry_policy.py` 的表，不要在主循环里加 `elif`**；同步方案附录 A 的清单表（`test_retry_policy.py` 逐列比对，改一处不改另一处即红）；`python -m pytest orchestrator/cloud/tests -q` |
| 可执行性判定特征（B6 shadow）| `python -m pytest orchestrator/cloud/tests/test_actionability.py -q`（含「特征里不许有领域词汇」的知识库派生断言）+ `python test/eval_actionability.py` 看召回/假阳性两侧。⚠ 后者是**取证脚本不是准入闸**，不在 CI blocking 里 |

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

### 7.1 另一件常见任务：新增一个**端侧车控能力**（不是 Agent）

先跑骨架生成器，它会把要填的地方逐段打出来，并列出四处生成不了、必须人写的东西：

```bash
python scripts/gen_capability_skeleton.py rear_wiper --display-name 后雨刷 --operates open,close
```

判据：**这件事不该靠「记得改十来个地方」**。除雾能力落地那次漏了对抗覆盖，就是因为
「要改哪些地方」只活在某个人的记忆里。现在漏一处就有具名红灯——2026-08-11 用虚构能力
`rear_wiper` 全流程演练过：只加 `commands.yaml` 对象、其余不做时，话术 ×2 / 等价类 ×1 /
迁移探针 ×1 / 台账陈旧项 ×1 / L0 对抗覆盖 ×6 逐条报出来，逐项照做后全绿。
门禁是 `test/eval_capability_integrity.py`（六维逐对象断言，CI blocking）。

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
