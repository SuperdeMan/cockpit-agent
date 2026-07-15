# 旅程级端到端验证体系：跨 Agent 自主执行 × 全场景连续对话 × HMI 二次交互

- **状态**：已落地 + 红灯修复收官（P0-P3 全落地，9 项红灯按泓舟拍板全部修复；终态
  回归级 15/15、目标级 16/18，遗留两卡见 §10 尾；随实施持续更新的落地记录在 §10）
- **交付对象**：后续执行者（人或 AI agent）
- **关联**：`test/`（现有 e2e/eval 资产）、`orchestrator/cloud/engine.py`（多轮续接）、
  `hmi/src/App.tsx`+`components/Cards.tsx`（二次交互协议面）、
  `observability/collector`（obs.db 断言面）、`docs/conventions.md` §9（跨轮状态契约）、
  `.github/workflows/nightly-e2e.yml`（门禁先例）

---

## 0. 一句话

现有验证体系是**单链路验收**（每个 e2e 脚本验一个主题的一次交互闭环），而「把事情办完」「对话不割裂」这两个目标能力的度量单位是**旅程**（journey：一个 session 内 多轮 × 多域 × 多 Agent × 二次交互 × 主动推送 × 终态）。本方案新建 **L3 旅程层**（数据驱动 journey 语料 + 统一 runner + 能力记分卡）与 **L4 HMI 交互层**（把历史临时 CDP 脚本固化进仓），语料分**回归级**（保护已有能力，必须绿）与**目标级**（定义能力标尺，允许红——红灯即工作项），让测试集本身成为这两个能力的**验收标准与演进牵引**。

---

## 1. 现状与证据

### 1.1 系统已具备的相关能力（= 回归级语料的来源）

| 机制 | 现状 | 证据 |
|---|---|---|
| T0 端侧快路径 / T1 单次 DAG / T2 有界 Agentic 循环 | ✅ 运行模型 | `AGENTS.md` §1，`orchestrator/cloud/{planning,engine,loop}.py` |
| DAG 步间结果传递（`slot_refs`） | ✅ 机制在，弱 LLM 下产出不稳 | conventions §9.1（"`slot_refs` 不得引用 `_` 前缀键"侧证机制存在） |
| 引擎级改派（`AgentResult.data["_escalate"]`，每轮 1 跳） | ✅ | conventions §9.1，`test_engine_escalate.py` |
| 跨域交接（共享状态 KV） | ✅ 8 个契约键 | conventions §9：`REMINDABLE_ACTIVE`（sports→reminder 一轮成单）、`NEWS_ACTIVE`（新闻→深挖）、`RESEARCH_ACTIVE`、`TRIP_ACTIVE`、`SCENE_*`、`REMINDER_*` |
| 聚合器多卡择优 / 途经点并入 / 导航去重 | ✅ | charging.find waypoint 并入 `navigate.waypoints`；`display_priority` |
| 确认闭环（NEED_CONFIRM）/ 补槽（NEED_SLOT）续接 | ✅ 单挂起，TTL 300s | `engine.py:112-158`，`SessionState` |
| 异步长任务有始有终 | ✅ 深调研受理即答→后台跑→NATS 主动推报告卡 | `test/e2e_research_async.py` |
| 定时任务触达 | ✅ reminder 到点经 `agent.proactive`→HMI 带卡 | `test/e2e_reminder.py` 7/7 |
| 场景全生命周期 | ✅ 造→确认→激活→verify-repair 后台对账→真恢复→询问式触发建议卡 | `test/e2e_scene.py` 26/26 |
| 焦点态跨轮指代 | ✅ `Focus`（对象/位置/属性/上个 POI，Redis） | `docs/design/2026-06-25-context-system-redesign.md` |
| 端侧轮写共享记忆（快慢路径历史一致的基础） | ✅ best-effort | AGENTS.md「对话上下文/指代」行 |
| 拒识/澄清（`intent_choice` 卡） | ✅ | R4.4，`e2e_rejection.py` |
| 主动播报 | ✅ road-safety 预警节流 / 晨间早报雏形 / routine | `agents/road_safety`、info P2 |

### 1.2 现有验证资产（可复用件，不重造）

| 资产 | 复用点 |
|---|---|
| `test/e2e_scene.py` 的成熟原语 | `ask()`（WS 单轮）、`ask_confirm()`（**只在真挂起时**补确认）、`settle()`、`reset_env()`（VAL 状态跨运行持久，必须归零）、`debug_vehicle()`（collector→NATS 压车况） |
| collector API | `/api/vehicle/state`（终态断言）、`/api/debug/vehicle`（压环境）、`/api/sessions/{id}/turns`、`/api/turns/{id}`（route/status/时延断言）、POST badcase（失败自动收藏→dashboard 重放） |
| 主动推送通道 | `gateway/edge/main.go:362-384`：NATS `agent.proactive` → 广播所有已连 HMI WS，帧 `{"type":"proactive","speech":...,"card"?...}` —— runner 挂一条常驻 WS 即可等推送 |
| eval 语料模式 | `test/eval_corpus/*.yaml`（YAML 语料 + runner + 报告 + `docs/reviews/eval/` 基线 + CI 非阻塞告警），`a\|b` 双容忍先例（mode_routing） |
| mock/live 双轨先例 | nightly 跑 `--case` 裁剪的 mock 确定性子集；live 评测记录 active provider 防跨 provider 假回归 |
| 合成会话前缀 | `e2e-` 跳过记忆抽取；`memtest-` 豁免（专测抽取巩固）——conventions §9.2 |

### 1.3 两个决定测试分层的关键协议事实（本次核实）

**① HMI 二次交互在协议层 = 「合成一句文本再发送」。**
`hmi/src/components/Cards.tsx` 全部卡内交互收口到 `onAction(text)` → `App.send(text, metaExtra?)`：
- `Cards.tsx:1059`：行程卡停靠点 → `导航去第{N}天的{name}`
- `Cards.tsx:1256`：场景列表 → `开启{name}`
- `Cards.tsx:1295`：通用按钮 → 后端下发的 `button.send_text`（如 reminder「稍后10分钟」）
- `Cards.tsx:1327`：POI 列表项 → `看{name}的详情`（另经 `meta.nearby_poi_id` 透传高德 POI id）
- `Cards.tsx:1400`：详情卡 → `导航去{name}`

仅两个例外带结构化标记：确认条（`is_confirmation: true`）与 POI 详情（`meta.nearby_poi_id`）。
**推论**：旅程测试在 WS 协议层发「按钮等价文本」即可高保真覆盖二次交互的**后端续接**；CDP 层只需抽样验证「卡渲染出来了 + 点击后发出的 WS 帧文本正确」这一段前端合成逻辑（含 `ordinalSelectIn` 裸序号、`nearby_poi_id` 透传等 **HMI 自有语义**——这些协议层模拟不到，必须真点）。

**② 挂起任务遇「无关话轮」当前会被直接丢弃。**
`orchestrator/cloud/engine.py:128-135`：`wait_confirm` 收到非确认非取消的话 → `session.clear()` 按新请求处理；`wait_slot` 判定换话题 → 同样清。
**推论**：「确认挂起时插一句别的、再回来确认」这种连续对话核心体验**当前不存在**——B2 组用例以目标级红灯钉住它，倒逼产品决策（见 §7 开放问题 Q1）。

---

## 2. 问题：为什么现有体系测不出这两个能力

1. **单链路 ≠ 旅程**。20 个 e2e 脚本各验一个主题；最长的 `e2e_trip.py` 6 轮也是单域。没有任何测试在一个 session 里走完「聊天→控车→导航→办事」的跨域长会话。
2. **断言停在"本轮响应"**。没有「事情办完了没」的**终态**断言口径（车况终值 / DB 落库 / 主动推送到达 / 完成回执话术）——「有始有终」无从度量。
3. **二次交互验证零散且缺 HMI 层**。确认条、四种选择卡、卡内按钮、主动推送分散在各脚本；历史 CDP 验证全是临时脚本（仓内 `grep 9222/Runtime.evaluate` 零命中），不可重复、不可回归。
4. **没有能力刻度**。路由质量有混淆矩阵和基线，「自主执行率」「上下文保持率」没有任何量化指标，能力是否在变好说不出来。
5. **已知缺口没有固化为可跑的红灯**。中断-恢复（§1.3②）、单句跨步结果传递、低电量主动补能建议、记忆参数化车控……这些缺口散在个人记忆里，没有变成「跑一下就知道修没修」的用例。

---

## 3. 目标：两个能力的操作化定义（可度量）

### 能力 A：跨 Agent 自主执行——"说一句话 → 把事情办完"

| # | 子能力 | 判据（旅程终态可断言） |
|---|---|---|
| A1 | 单句多域并行 | 一句话含 N 件事 → N 件全部落地（车况变化 + 动作 payload + 卡片），一次聚合回执 |
| A2 | 中间结果传递 | 后一步消费前一步产出（搜到的店→导航目的地；赛程时刻→提醒时刻），终态里是**具体值**而非用户原话 |
| A3 | 交接与改派 | 答不了的自动改派（escalate）、跨域数据经共享状态交接，用户无感知无重问 |
| A4 | 异步有始有终 | 长任务受理即回执 → 后台完成 → **主动**推送结果 → 结果可继续交互 |
| A5 | 失败诚实 / 部分完成 | 多步中一步失败：其余步照常完成 + 明说哪步没办成；数据界外不编造 |

### 能力 B：全场景连续对话——"聊天、控车、导航、办事不割裂"

| # | 子能力 | 判据 |
|---|---|---|
| B1 | 跨域指代消解 | 「去那儿」「第二家」「那附近」跨域解析到前轮实体（focus/共享状态） |
| B2 | 中断-恢复 | 挂起（确认/补槽/选择）中插入无关轮，之后仍可回来续接完成 |
| B3 | 域间信息迁移 | 前轮信息影响后轮决策（天气→行程调整；电量→导航补能；记忆→车控参数） |
| B4 | 快慢路径一致 | 端侧轮（T0）与云端轮共享同一份历史/状态，互相可引用 |
| B5 | 长会话鲁棒 | 15+ 轮混合会话不丢焦点、列表叠加后「第N个」指最新、无记忆污染 |

### 能力 C：HMI 二次交互正确执行

判据链：**卡片渲染 → 控件存在 → 点击 → 发出正确协议帧 → 后端正确续接 → 终态达成**。
协议层（旅程 runner）覆盖后三环全量；CDP 层覆盖前三环抽样 + HMI 自有语义全量。

### 记分卡（北极星指标，报告随每次运行产出）

| 指标 | 口径 | P0 目标 | 长期目标 |
|---|---|---|---|
| 任务完成率 | A 组旅程终态断言通过 / 总 | 回归级 100% | 目标级逐季收敛 |
| 上下文保持率 | B 组指代/续接类轮级断言通过 / 总 | 收基线 | ≥90% |
| 二次交互成功率 | C 组用例通过 / 总 | 回归级 100% | 100% |
| 诚实率 | A5 组 + 全部旅程的反编造断言（`speech_not`） | 100%（诚实是红线不是目标） | 100% |
| 主动闭环率 | A4 组 wait_push 到达且可续接 / 总 | 回归级 100% | 100% |
| 时延达标率 | obs.db 每轮 duration ≤ 语料声明 budget | P0 只收基线 | P1 定阈值后 ≥95% |
| 零泄漏 | speech 无 markdown/`<think>`/JSON 外壳（抽全部旅程断言） | 100% | 100% |

---

## 4. 方案：在现有 L0-L2 之上加两层

```
L0 单测/契约（1576 passed）           —— 不动
L1 eval 路由/拒识/澄清（5 套语料）     —— 不动
L2 单链路 e2e（20 个 e2e_*.py）        —— 不动；部分能力被 L3 旅程「引用」避免重复
L3 旅程层（本方案核心）e2e_journeys.py + test/journeys/*.yaml
L4 HMI 交互层（CDP 固化）test/hmi_cdp/ + cdp_cases.yaml
```

### 4.1 L3 旅程层

**载体**：数据驱动 YAML 语料 + 统一 runner（`test/e2e_journeys.py`）。
理由：旅程数量到几十条且结构同质（说/点/等/断言），脚本式（每旅程一个 .py）不可扩展；YAML 语料资产化已被 `eval_corpus/` 五套验证（可 review、可增补、可统计）。

**Journey schema**（示例即规范）：

```yaml
suite: A-autonomy
journeys:
  - id: A1-2-nav-charging-waypoint
    title: 导航+沿途充电一句话办完
    level: regression      # regression=必须绿 | target=能力标尺（允许红，红=工作项）
    lane: live             # live=真 LLM/真 provider | mock=nightly 确定性子集
    requires: [AMAP_KEY]   # 缺则 SKIP（沿用按 key 跳过约定）
    retry: 1               # 仅涉高德 QPS 等已知外因抖动的旅程允许 1 次重试
    setup:
      vehicle: {battery: 45, gear: P, speed_kmh: 0}   # debug_vehicle 压值
      reset: true                                      # reset_env（VAL 持久态归零）
    turns:
      - say: 导航去东部华侨城，沿途帮我找个充电站
        expect:
          latency_s: 40
          speech_any: [充电, 途经]           # 命中其一即可（LLM 话术波动容忍）
          speech_not: ["**", "<think>", '{"answer"']   # 零泄漏红线，runner 全局默认+可追加
          cards_any: [charging_route, route_plan]
          action: {type: navigate, payload_has: [waypoints]}
    finally:
      - no_duplicate_action: navigate       # 聚合器去重
```

**turn 操作原语**（runner 实现面）：

| 原语 | 语义 | 复用/依据 |
|---|---|---|
| `say` | WS 发文本（session_id 恒定 `e2e-jrn-*`；记忆类旅程用 `memtest-`） | `e2e_scene.ask` |
| `confirm` / `cancel` | 发「确认/取消」+ `is_confirmation`，且**仅当上轮 `need_confirm`**（防 P2 幂等跳过后断言错位） | `e2e_scene.ask_confirm` |
| `press` | 二次交互：优先从**上一轮实收卡片 JSON** 取 `buttons[i].send_text` 原样发（保真）；前端合成类（行程停靠点等）按语料写死等价文本，并在 C 组用 CDP 验证前端合成与之一致 | §1.3① |
| `wait_push` | 常驻 WS 等 `{"type":"proactive"}`，断言 speech/card 类型，超时可配（异步调研 ≤5min） | `main.go:362-384` |
| `env` | `debug_vehicle(key,value)` 压车况/行车态/电量 | collector debug API |
| `probe` | 记忆/状态探针：问一句、断言答案证明状态在（如「我刚才让你干什么」） | — |
| `sleep` / `settle` | 等 VAL 落地 + NATS diff 回镜像 | `e2e_scene.settle` |

**expect 断言原语**：`speech_any/all/not`、`cards_any`、`card_field`（如 `actions_preview>=2`）、`action`（类型+payload 键/值）、`need_confirm`、`follow_up_any`、`latency_s`、`vehicle`（经 collector 终态，如 `{hvac_temp: 24}`）、`obs`（`/api/turns` 的 status/route 归属）、`push`。
**journey 级 `finally`**：终态车况、落库回读（如 reminder list 回读条数）、`no_duplicate_action`、记忆探针。

**失败即 badcase**：runner 对失败轮自动 POST collector badcase 标记（带 journey id 注释）→ dashboard 收藏夹一键重放排查。测试体系与 badcase 观测闭环打通，红灯直接变成可下钻的工单。

**报告**：`docs/reviews/eval/journeys_report.{json,md}`——按 suite/level 通过率 + §3 记分卡 + 红灯清单（id/现象/首损轮/obs trace_id）+ **active provider 与时间戳**（防跨 provider 假回归，R4.4 的坑）。

### 4.2 L4 HMI 交互层（CDP 固化）

**现状**：历史所有 CDP 验证（Aurora Glass 8 卡族、nearby「点一下第九个」、R4.2 TTS 三态……）都是会话临时脚本，仓内不存在。**本方案把它固化为 `test/hmi_cdp/`**：

- `driver.mjs`：宿主 Node 零依赖 CDP 驱动（成熟经验：headless Edge + `--remote-debugging-port`；`Network.webSocketFrameSent/Received` 拦 WS 帧断言「点击→发出的文本」；`Runtime.evaluate` 查 DOM/卡片；截图留档）。
- `cdp_cases.yaml`：C 组用例语料（选择器 + 动作 + 帧断言 + DOM 断言）。
- 定位约定：卡片根节点补 `data-testid="{card.type}"`（HMI 一次性小改，属测试性改造不属功能）。

**分工原则**：后端续接语义在 L3 全量测；L4 只测协议层模拟不到的东西——渲染正确性、前端文本合成、`ordinalSelectIn` 裸序号、`meta.nearby_poi_id` 透传、确认条、proactive 卡渲染+TTS 入队、过程区门控（行车态极简）、右舞台联动。

### 4.3 运行矩阵

| 车道 | 内容 | 触发 | LLM/provider |
|---|---|---|---|
| mock 子集 | `lane: mock` 旅程（确定性路由：route_hints 可达 + 无真实外部数据依赖） | nightly CI（挂进 `nightly-e2e.yml`，沿用 `--case` 先例） | MockProvider |
| live 全量 | 全部旅程 | 本地手动 / release 前（`make e2e-journeys`） | 声明 active provider；**禁与 docker build 并发**（IO 打满→LLM 超时假失败，2026-07-12 实证） |
| CDP | C 组 | 本地手动 / release 前 | 同 live |

前置纪律（写进 runner 启动检查）：全栈起后 settle ≥40s（registry 重注册 10s + 车况快照 30s）；宿主 5173 未被占（CDP 用例）；`reset_env` 每旅程必跑。

### 4.4 双层语料是本方案的灵魂

- **regression 级**：把散在 20 个 e2e 脚本里的跨域能力，以「旅程口径」重新集成（不是复制断言——是把它们串进同一个 session 验连续性）。必须 100% 绿，红了就是回归。
- **target 级**：把目标能力写成用例。**预期一部分首跑就红**——红灯清单 = 「让系统具备这两个能力」的工程 backlog，每条红灯按现有节奏立决策卡（修 / 降级为 backlog / 修正用例预期）。记分卡跟踪红→绿的演进。

---

## 5. 测试集（全量语料定稿）

> 标注：〔R〕regression / 〔T〕target；〔m〕mock 可跑 / 〔L〕live only；
> 预期：🟢 现应通过 / 🟡 可能通过（机制在但没验过这条路径）/ 🔴 预期红灯（已知缺口，红灯即工作项）。
> 已有 e2e 覆盖的生命周期（scene 26 断言、trip 6 轮、memory 6 链路）**不重复搬运**，旅程集通过「引用挂接」（runner 顺序调用既有脚本）纳入统计。

### 5.1 A 组：跨 Agent 自主执行（14 条）

**A1 单句多域并行**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| A1-1 车控×媒体×信息 | ①「把空调调到24度，播放一首林俊杰的歌，顺便说下今天深圳天气」 | `vehicle.hvac_temp=24`；action 含 media.play；weather 卡；一次聚合回执三件事都提到；latency≤15s | 〔R/L〕 | 🟢 |
| A1-2 导航×充电 | ①「导航去东部华侨城，沿途帮我找个充电站」（电量压 45%） | navigate.payload 含 waypoints；charging_route/route_plan 卡；无重复导航动作 | 〔R/L〕 | 🟢 |
| A1-3 提醒×天气 | ①「明早八点提醒我带伞，看下明天会不会下雨」②probe「我有哪些提醒」 | 天气**意图先答**（会/不会下雨）；reminder 落库（②回读含"带伞"）；两卡共存 | 〔R/L〕 | 🟢 |
| A1-4 条件依赖 DAG | ①「查下明天天气，要是下雨就提醒我明早带伞」 | 目标：天气步产出 → 条件成立才建提醒（终态二选一自洽：下雨→提醒在列；不下雨→明说不用）| 〔T/L〕 | 🔴 条件执行弱 |

**A2 中间结果传递**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| A2-1 搜店→导航单句 | ①「找一家附近评分高的川菜馆，直接导航过去」 | navigate.dest 为**具体店名**（非"川菜馆"原话）；place 卡与路线卡并存 | 〔T/L〕 | 🔴 slot_refs 产出不稳 |
| A2-2a 赛程→提醒两轮 | ①「明天世界杯有哪些比赛」②「第一场开始前提醒我看」 | ②一轮成单：speech 含具体队名+时刻+提前量；不反问时间（REMINDABLE 交接） | 〔R/L〕 | 🟢 P1c 已落地 |
| A2-2b 赛程→提醒单句 | ①「明天世界杯第一场是谁踢？开赛前提醒我」 | 同上但单句完成；允许"诚实无数据"分支（api-football 免费档今天±1） | 〔T/L〕 | 🟡 |
| A2-3 行程×充电编织 | 电量压 30% → ①「规划周末杭州两日游」→ confirm | trip_itinerary 卡含充电停靠；话术提到补电 | 〔R/L〕 | 🟢 weave 已有 |
| A2-4 导航 ETA→提醒 | ①「导航去宝安机场」②「到之前一刻钟提醒我给张姐打电话」 | 目标：navigation 产 ETA 写 `REMINDABLE_ACTIVE`（契约"trip/charging 即插"）→按预计到达-15min 成单；**可接受降级**：诚实说明按预计时长转时间提醒 | 〔T/L〕 | 🔴 navigation 未产 ETA；位置触发 P1b 未做 |

**A3 交接与改派**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| A3-1 chitchat 时效改派 | ①「昨晚欧冠决赛比分是多少」 | 双容忍：直接路由 sports/search **或** chitchat `_escalate` 一跳；红线=不编造比分（speech_not 断言无凭空数字，经证据卡佐证） | 〔R/L〕 | 🟢 escalate 已落地 |
| A3-2 新闻→深挖 | ①「今天有什么科技新闻」②「详细讲讲第2条」 | ②出 research_report 且主题=第2条标题（NEWS_ACTIVE 桥接） | 〔R/L〕 | 🟢 |
| A3-3 搜索→调研升级 | ①问一个薄证据问题（如小众技术对比）②按 follow_up 引导说「那深入调研一下」 | ②路由 research.run 且复用①话题 | 〔R/L〕 | 🟡 引导已有，续接路径没验过 |

**A4 异步/长任务有始有终**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| A4-1 异步调研全闭环 | ①「帮我调研下2026固态电池量产进展，不急，查完告诉我」→ wait_push(≤5min) ②「展开第2点」 | ①秒级受理 ack 无卡；push 带 research_report 卡；②聚焦深挖**不重跑**（RESEARCH_ACTIVE） | 〔R/L〕 | 🟢 ①=e2e_research_async；②粘合未验 |
| A4-2 提醒到点→按钮改期 | ①「过2分钟提醒我接水」→ wait_push ② press「稍后10分钟」③ probe「我的提醒」 | push 带 reminder_card；②改期**原条目**；③回读仍 1 条无 fired 尸体 | 〔R/L〕 | 🟢 e2e_reminder 4b 已验，旅程口径串联 |
| A4-3 场景生命周期 | 引用挂接 `e2e_scene.py`（26 断言） | —— | 〔R/L〕 | 🟢 |
| A4-4 过程区回执 | A4-1 ①的同轮附加断言：收到 `progress` 四阶段事件流 | 理解→规划→执行→整理；running/done 合并 | 〔R/m〕 | 🟢 e2e_process_region 已有，并入原语 |

**A5 失败诚实 / 部分完成**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| A5-1 部分失败回执 | docker stop trip-planner-agent → ①「规划明天杭州一日游，顺便看下明天杭州天气」→ finally 恢复容器 | 天气正常答出；行程**明说没办成**（不整体报错、不假装成功、不静默吞）；obs 该 step=FAILED | 〔T/L〕 | 🟡 FAILED 话术有，"部分完成清单式回执"未验 |
| A5-2 数据界外诚实 | ①「下周三世界杯有什么比赛」 | 诚实告知拿不到（免费档界外），不列无关场次不编造 | 〔R/L〕 | 🟢 2026-07-06 #4 已修 |
| A5-3 危险动作不被旅程稀释 | ①「打开后备箱，顺便播放音乐」 | 音乐即执行；后备箱**仍走确认**（多意图不绕过 require_confirm）→ confirm 后 vehicle 终态变化 | 〔R/m〕 | 🟢 有单测，旅程口径复核 |

### 5.2 B 组：全场景连续对话（13 条）

**B1 跨域指代**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| B1-1 POI 列表→导航 | ①「附近有什么好吃的火锅店」② say「就去第二家」 | ② navigate.dest=①列表第 2 家店名（协议层直发，验后端 focus/last_poi；HMI `ordinalSelectIn` 路径归 C2-4） | 〔T/L〕 | 🟡 dest_choice 的「第N个」有，place_list 的「去第N家」未验 |
| B1-2 导航→天气焦点迁移 | ①「导航去深圳湾公园」②「那边现在天气怎么样」 | ②天气落点=深圳湾/南山（focus 位置），非当前定位重复 | 〔T/L〕 | 🟡 |
| B1-3 导航→周边 | ①「导航去万象天地」②「那附近有停车场吗」 | ② nearby 检索中心=万象天地坐标 | 〔T/L〕 | 🟡 |
| B1-4 槽位跨轮继承 | ①「明天杭州天气怎么样」②「后天呢」 | ②=后天@杭州（不丢城市、不答今天） | 〔R/L〕 | 🟢 历史注入已有 |
| B1-5 车控对象继承（快路径连续性） | ①「打开主驾座椅加热」②「副驾也开一下」 | ② seat_heat 副驾生效（vehicle 终态）；无论端侧接住还是上云兜底，**用户无感** | 〔T/m〕 | 🔴 fast_intent 单轮 pattern，大概率漏 |
| B1-6 研究报告回指 | 引用 A4-1 ②（RESEARCH_ACTIVE「展开第N点」） | —— | 〔R/L〕 | 🟢 |

**B2 中断-恢复**（当前引擎清挂起——目标行为见 §7 Q1，全组 target）

| id | 逐轮脚本 | 关键断言（目标行为） | 级 | 预期 |
|---|---|---|---|---|
| B2-1 确认挂起+插话 | ①「创建一个下班模式：空调24度，氛围灯橙色」→need_confirm ②「今天天气怎么样」③「好了，确认保存」 | ②正常答天气；③场景保存成功（scene list 回读含"下班模式"）——**当前**：③会得到"没有待确认的操作" | 〔T/L〕 | 🔴 engine.py:128 清挂起 |
| B2-2 补槽挂起+插话 | ①「提醒我吃降压药」→NEED_SLOT 问时间 ②「先把音量调到20」③「晚上九点」 | ③成单"吃降压药 21:00"（`REMINDER_PENDING` 是 profile KV，agent 层可能仍能续——正好测出 engine SessionState 与 agent 层 PENDING 双层挂起的真实交互） | 〔T/L〕 | 🟡 双层机制赛跑，结果未知 |
| B2-3 选择卡挂起+插话 | ①「导航去惠州找个充电站」→dest_choice ②「现在几点」③「第一个」 | ③正确回填①的候选第 1 项继续规划 | 〔T/L〕 | 🔴 |

**B3 域间信息迁移**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| B3-1 行程×天气续改 | ①「周末去珠海玩两天」（行程含逐日天气）→confirm ②「哪天要下雨的话，把那天改成室内的安排」 | ②按①天气数据定位目标天并 modify（雨天→室内 POI）；无雨则明说不用改 | 〔T/L〕 | 🔴 trip×weather 展示有，反向驱动 modify 未有 |
| B3-2 电量×导航主动建议 | 电量压 15% → ①「导航去广州塔」（跨城 >100km） | 目标：导航照发 + **主动**提示续航不足/给沿途补能建议（advisory 不强加动作）——车辆接地护城河用例 | 〔T/L〕 | 🔴 需显式说充电才触发 |
| B3-3 记忆×车控参数化 | 会话1（`memtest-`）：「记住我最喜欢26度」；新会话：①「空调调到我喜欢的温度」 | `vehicle.hvac_temp=26`；难点=该句可能被端侧 fast_intent 以缺参劫持，需云端记忆召回参与 | 〔T/L〕 | 🔴 |
| B3-4 天气×出行联动 | ①「今天天气怎么样，适合出行吗」 | 意图先答（适合/不适合+依据）+ 出行建议不反问目的地 | 〔R/L〕 | 🟢 2026-07-13 badcase⑥ 已修 |

**B4 快慢路径一致**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| B4-1 端侧轮进共享历史 | ①「把音量调到15」（T0 端侧）②「我刚才让你把音量调到多少」 | ②答 15（云端读到端侧轮历史） | 〔R/L〕 | 🟢 best-effort 写入已有 |
| B4-2 场景句不被端侧劫持 | ①「开启午休模式，温度26」 | 整句上云走 scene（custom_params 26）不被空调分支拆走 | 〔R/m〕 | 🟢 2026-07-14 修根，护栏回归 |

**B5 长会话鲁棒**

| id | 逐轮脚本 | 关键断言 | 级 | 预期 |
|---|---|---|---|---|
| B5-1 「一次通勤」20 轮 showcase | ①「早上好」②「今天上班路上会堵吗」③「导航去南山科技园」④「路上找家咖啡店顺路买一杯」→waypoint_choice ⑤「第二个」⑥「到公司前提醒我交周报」⑦「播放点新闻」⑧「详细讲讲第2条」⑨env 压 speed=60 行车态 ⑩「空调调到23度」⑪「刚才让你提醒我什么来着」⑫「取消那个提醒吧」⑬probe 提醒列表空 …… | 逐轮各自断言 + 终态：导航在途含咖啡途经点、提醒建了又销、⑪答"交周报"（回指第⑥轮）、全程无"请再说一遍需求"类断链话术 | 〔R+T 混合/L〕 | 🟡 单环节都有，串联从未跑过 |
| B5-2 列表叠加消歧 | ①「附近的火锅店」（place_list）②「找个充电站去惠州的路上用」（dest_choice/站候选）③「第一个」 | ③指**最新**列表（充电候选），不串台到火锅 | 〔T/L〕 | 🟡 |

### 5.3 C 组：HMI 二次交互（CDP，12 条）

全部断言链 = 渲染（DOM/testid）→ 点击 → **`Network.webSocketFrameSent` 实拦帧文本** → 后端续接（final 帧）→ 视觉留档（截图）。

| id | 用例 | 帧断言要点 |
|---|---|---|
| C1-1 确认条 | 「打开后备箱」→确认条渲染→点确认 | 帧含 `is_confirmation:true`；随后车况镜像 trunk 开 |
| C1-2 取消 | 同上点取消 | 帧为取消语义；车况不变 |
| C2-1 intent_choice 澄清卡 | 「处理一下停车的事」（CLARIFY on）→点「找附近停车场」 | 帧=选项 send_text；续接 nearby |
| C2-2 dest_choice | 惠州充电→点第 1 候选 | 帧=「第一个」等价文本；续接出 charging_route |
| C2-3 waypoint_choice | 顺路咖啡→点第 2 家 | 续接 navigate.waypoints 更新 + route_plan |
| C2-4 place_list 裸序号 | 火锅列表→**语音框输入「点一下第九个」**（走 `ordinalSelectIn`） | 前端改写生效：发出的详情请求带 `meta.nearby_poi_id`=第 9 项 id（协议层模拟不到，必须真点） |
| C3-1 行程卡停靠点 | trip 卡点第 2 天某停靠点 | 帧文本=「导航去第2天的X」（前端合成与 L3 语料一致性校验点） |
| C3-2 reminder 卡按钮 | 到点卡点「稍后10分钟」 | 帧=按钮 send_text；后端改期原条目 |
| C3-3 scene_list | 场景列表点「开启露营模式」 | 帧=「开启露营模式」；确认链/执行链续接 |
| C4-1 主动推送渲染 | 等 reminder 到点/异步调研 push | proactive 卡渲染 + TTS 入队（audio 调用被触发）；建议卡可点 |
| C5-1 过程区门控 | 复杂调研出四阶段过程区；「打开空调」无过程区；env 压行车态→过程区极简不可展开 | DOM 断言三态 |
| C6-1 右舞台联动 | place_list→地图 POI 测距环；charging→SoC 时间线；车况舞台显示 debug 压入的真值 | DOM/数据断言 |

### 5.4 G 全局质量门（跨旅程横切）

- **时延基线**：runner 从 obs.db 汇总每轮 duration，按 T0/简单云/复杂三档出 P50/P95。P0 只采集写报告；P1 由泓舟拍阈值后变门禁（参考现状：网关 90s cap、trip budget 40s、端侧秒回）。
- **零泄漏**：`speech_not: ["**","<think>",'{"answer"']` 为 runner 全局默认断言。
- **无断链话术**：回归级旅程全程 `speech_not: ["请再说一遍需求","没有待确认的操作"]`（除非用例显式预期）。
- **记忆无污染**：`e2e-` 前缀会话零抽取（conventions §9.2 已保证，旅程终态抽查画像无测试残留）。

---

## 6. 分阶段落地

### P0：骨架 + 回归级旅程（先让"旅程"这个口径存在）
1. `test/e2e_journeys.py` runner（原语见 §4.1，全部复用 §1.2 既有件）+ `test/journeys/` 语料目录 + schema 校验。
2. 落回归级旅程 **~16 条**（§5 标〔R〕者），其中 `lane: mock` 可确定性跑的（A5-3、B4-2、A4-4 等）标出。
3. 报告 + 记分卡初版入 `docs/reviews/eval/`；失败自动 badcase 收藏。
4. **DoD**：真栈 live 全绿（回归级 100%）；mock 子集在本地 MockProvider 栈全绿；报告含 provider 声明与时延基线。

### P1：目标级红灯集 + 决策流
1. 落目标级旅程 **~15 条**（§5 标〔T〕者）。
2. 首跑产出**红灯清单**（预期 🔴 约 8-10 条），每条按「修 / 立卡进 backlog / 修正用例预期」三选一给泓舟批——**这份清单就是"让系统具备两能力"的工程路线图**。
3. §7 Q1（中断-恢复目标行为）等开放问题随红灯清单一并拍板。
4. **DoD**：红灯清单评审完毕、每条有归属；记分卡建立首个演进基线。

### P2：HMI CDP 层
1. `test/hmi_cdp/driver.mjs`（Node 零依赖）+ `cdp_cases.yaml` + HMI 卡片根节点补 `data-testid`。
2. 落 C 组 12 条 + 截图留档目录约定。
3. **DoD**：C 组回归级全绿；「点击→帧文本」断言对 §1.3① 五处前端合成逐一校验通过。

### P3：门禁化与常态运营
1. mock 旅程子集挂 `nightly-e2e.yml`；live 全量进 release 前手动清单（`make e2e-journeys`）。
2. 记分卡按次追加到基线文件，dashboard badcase 收藏夹作为红灯排查入口的操作手册写进 `test/README.md`。
3. **DoD**：nightly 连续 3 次全绿；`test/README.md`、`AGENTS.md` §4、`docs/design/README.md` 同步。

---

## 7. 开放问题（需泓舟拍板）

- **Q1 中断-恢复的目标行为**（决定 B2 组断言）。我的建议：**单挂起 + 插话不清除**——无关轮正常处理但保留挂起（TTL 内），用户回头说「确认」仍可续；仅当新话轮与挂起**同域冲突**（又发起一个新场景创建）才覆盖旧挂起。不建议做多层挂起栈（复杂度对座舱场景不值）。
- **Q2 时延阈值**：P0 采完基线后定，还是现在先拍粗值（端侧≤1.5s / 简单云≤8s / 复杂≤90s）？建议前者，用数据说话。
- **Q3 CDP 车道的运营位**：建议不进 nightly（浏览器层脆），只进 release 前手动清单——是否接受？
- **Q4 B5-1「一次通勤」20 轮旅程**是否要作为对外 demo 剧本双用（showcase + 测试）？若是，话术脚本会额外打磨。

---

## 8. 风险与既有坑（写进 runner/运行手册）

| 风险 | 对策 |
|---|---|
| LLM 话术波动导致假红 | 轮级断言优先**状态化**（车况/落库/动作 payload/卡类型），话术只用 `speech_any` 容忍集；`a\|b` 双容忍先例 |
| 跨 provider 基线不可比 | 报告强制记录 active provider；对比基线须同 provider（stash 隔离实验的教训） |
| 评测与 docker build 并发 → LLM 超时假失败 | runner 启动检查 + 手册明令 |
| VAL 状态跨运行持久 / P2 幂等跳过 | 每旅程 `reset_env` + `ask_confirm` 条件续接（e2e_scene 已趟平） |
| registry 重注册 10s / 车况快照 30s | 起栈 settle ≥40s |
| 高德 QPS 限流偶发回退 mock | 涉高德旅程 `retry: 1`，且断言写「真实数据或诚实降级」双容忍，不硬断真数据 |
| api-football 免费档只开今天±1天 | 赛事旅程日期相对化（「明天」），允许诚实无数据分支 |
| 旅程失败级联误报 | runner 每旅程独立 session、独立 setup；轮失败即终止该旅程后续轮（报首损轮），不连坐其它旅程 |
| CDP：宿主 5173 被占 / hmi 无卷挂载 | driver 启动自检端口；改前端必 `up -d --build hmi` |
| 合成会话污染画像/烧 token | `e2e-jrn-` 前缀走 §9.2 跳过表；记忆旅程专用 `memtest-` |

---

## 9. 不做什么（边界）

- 不动 L0-L2 存量测试与 eval 基线；不改编排核心来「配合测试」——目标级红灯的修复本身也必须走 manifest/机制化路线（CLAUDE.md §3 铁律）。
- 不做真麦声学层自动化（KWS 命中率/误唤醒仍按 R4.3 §10 留人工验收）；语音链路只测协议层（`input_source` 拒识等已有 e2e 覆盖）。
- 不引入外部测试框架/服务；runner 沿用 stdlib+websockets 的既有风格。
- 不在 P0-P3 内实现红灯用例对应的产品能力——那是红灯清单评审后的独立主题。

## 10. 落地记录（2026-07-14 起，随实施更新）

### P0 已完成：runner + 回归级 15 条真栈收敛全绿（commit `8b0bfc2`）

`test/e2e_journeys.py` + `test/journeys/regression_{a,b}.yaml`，真栈 @mimo 全绿
（套件跑 14/15，唯一失败 A5-2 为 api-football 断供，按 SKIP 语义修正后过）。
报告与时延基线 `docs/reviews/eval/journeys_report.{json,md}`。

**首跑即抓到一个存量真 bug（已修，commit `6470209`）**：
- **scene custom_params 槽位声明了从不消费**——「开启午休模式，温度26」有三种真实路由
  变体（route_hint replace 对「LLM 已路由同 intent」让路）：①hint 灌原句→文本解析活；
  ②Planner 归一化 slots.scene=「午休模式」+ `custom_params={'temperature':'26'}`（manifest
  声明了该槽 LLM 就会产）→ **agent 只做文本解析，槽位无人读**，确认轮 raw_text=「确认」
  后两个文本源都没有 26 → 用户拿到默认 24；③LLM 拆两步。e2e_scene 同款用例过是因为恰好
  走了变体①——**路由变体依赖的缺陷只有旅程层多次真跑才暴露**。修法：原话优先、槽位兜底
  （键别名映射+validate_action 同轨+值三态容忍 dict/JSON/Python repr）。

**runner 侧的两个协议校准（实现即文档）**：
- **混合意图一次请求发多个 final**（端侧本地段先 final、云段再 final，`server.py` 快路径
  A2）——runner 宽限窗收齐合并，向 HMI 口径对齐；否则「空调+音乐+天气」只断言到第一个
  final 就误报丢意图。
- **媒体短话术回执就是「好的」**——媒体断言必须状态化（`media.control` 动作）不能靠话术；
  端侧分组有运行间差异（media 可能本地执行、也可能与天气整组上云），动作断言两态通吃。

**语料校准三则（前置条件要造出「需要该能力」的局面）**：
- A1-2 电量 45% 去 47.7km 被系统正确判「无需充电」——语料错，改 10%；随后暴露两条合法
  路由（charging.find→waypoint 并入 / charging.plan→advisory+候选），断言收敛到用户可见
  契约（导航发出+充电信息呈现+不重复导航）。
- 外部数据源断供（api-football 超时→「抱歉，处理失败」）按 `skip_journey_if_speech_any`
  判 SKIP 不判 FAIL——回归级红灯必须只对回归响。
- `dest_choice`/`waypoint_choice` 不是卡类型，是 `poi_list` 卡的 `purpose` 字段
  （`hmi/src/types.ts:327`）。

### P0 真栈跑出的存量质量项（进入红灯清单，均有 obs trace 佐证）

| # | 发现 | 证据 | 建议归属 |
|---|---|---|---|
| Q1 | **provider 故障零降级**：api-football 超时 → step FAILED → 用户拿到裸「抱歉，处理失败」，不改派搜索、不说明数据源故障 | A2-2a/A3-1/A5-2 首跑，`provider.apifootball.fixtures timeout` span | 目标级工作项（sports/info 域故障降级策略） |
| Q2 | **charging.plan 直达判定零保留余量**：电量 10%（续航 50km）对 47.7km 判「足够直达，无需途中补电」——2.3km 余量，weave 的 0.85 系数未作用于该路径 | A1-2 首跑话术 | 目标级工作项（直达判定引入保留余量+显式请求优先） |
| Q3 | **sports 日期界外回「今天」口径答非所问**：「下周三有什么比赛」→「**今天**没有查询到…」；「昨晚欧冠决赛比分」→ 列今天在踢的资格赛 | A5-2/A3-1 通过轮话术 | 目标级工作项（日期语义与免费档门对齐话术） |

### P1 目标级 18 条首跑：7 绿 / 11 红（2026-07-14，@mimo，详单见 journeys_report.md）

**先说意外的绿**（这些能力其实已经在了，此前无人验证过）：
- **A2-1 单句搜店→导航直达**：「找评分最高的川菜馆直接导航过去」→ 真店名（灯花·川小馆
  4.7 分）+ 路线，不反问——单句中间结果传递成立。
- **A2-2b 单句赛程→提醒成单**、A3-3 薄证据→引导→调研续接同话题、**A5-1 部分失败诚实
  回执**（trip 故障注入后天气照答+明说规划失败）、B1-1「就去第二家」后端直达、
  **B1-5 车控对象跨轮继承**（「副驾也开一下」→ plan `seat.heating.on {position:passenger}`，
  obs trace `47b7a90b2b9d4fbe`）、**B2-2 补槽挂起+插话后裸「晚上九点」仍续接成单**——
  agent 层 `REMINDER_PENDING`（profile KV）活过了引擎清挂起，证明中断-恢复在 agent 层
  模式可行，B2-1 的修复方向有现成参照。

**红灯清单（11 条，按根因归并为 9 个工作项，泓舟按「修/立卡/改预期」三选一评审）**：

| # | 根因 | 命中旅程 | 现象（真栈原文） | 建议 |
|---|---|---|---|---|
| R1 | **POI 解析就近关键词命中压过知名地标/城市语义**（横切之王，5 例同族） | B3-2/A2-4/B1-2①/B2-3/B5-2（+B1-3 nearby 按名重搜异地同名） | 「导航去**广州塔**」→深南大道「广州仄仄科技有限公司」4.6km；「**宝安国际机场**」→北环大道入口 3.9km；「**大梅沙海滨公园**」→红树林海滨生态公园；「去**惠州**的路上」→0.3km 的「惠州出口」（顺带压掉 dest_choice 泛目的地判定前提）；「那附近停车场」→**呼和浩特**万象天地 | **建议按回归级对待**（高频真实句）：知名地标/城市名走 landmark 官方名解析+name_matches 的既有路径（现只覆盖视觉俗称），或 keyword 搜索结果对「城市级/地标级」query 加语义校验 |
| R2 | **确认挂起被插话清除**（engine.py:128 `session.clear`） | B2-1 | 创建场景→NEED_CONFIRM→问天气→「确认」→「当前没有待确认的操作」 | 按 §7 Q1 建议实现「单挂起+插话不清除、TTL 内可续、同域冲突才覆盖」；B2-2 的 agent 层 KV 模式是参照 |
| R3 | **焦点位置迁移缺失**：weather/nearby 不消费 focus.last_poi | B1-2②/B1-3 | 导航去大梅沙后「那边天气」→答当前南山；「那附近停车场」→按名全国重搜 | focus 已存对象/位置（上下文重构落的），info.weather/nearby.search 消费即可 |
| R4 | **reminder create hint 无疑问式 guard**：回忆类问题被当新提醒 | B5-1⑪ | 「我刚才让你提醒我什么来着」→「好的，我刚才让你提醒我什么来着。什么时候提醒你？」（还覆盖了在挂的 PENDING） | manifest create hint 补 guard（什么来着/什么了/吗/来着）；接近回归级 |
| R5 | **记忆陈述句无人接**：被端侧温度劫持执行+云端 scene.create 抢 | B3-3 | 「记住，我最喜欢的空调温度是26度」→当场把空调调到 26 +「将创建空调20度…保存吗？」；新会话「调到我喜欢的温度」→端侧当「开空调」 | 「记住/我喜欢」陈述句让路云端记忆写入；「我喜欢的温度」参数化车控需记忆召回参与执行链 |
| R6 | **条件依赖 DAG 弱**：条件分支丢步 | A1-4 | 「查明天会不会下雨，要是下雨就提醒我带伞」→天气步丢失+reminder 反问时间 | 立卡（planner 条件语义）或改预期为「至少把两件事都办/都答」 |
| R7 | **navigation 不产 REMINDABLE/ETA** | A2-4②/B5-1⑥ | 「到之前一刻钟提醒我打电话」→「暂不支持哦」(2.0s 端侧劫持)；「到公司之前提醒我交周报」→反问时间 | REMINDABLE_ACTIVE 契约本就设计 trip/charging「即插」——navigation 产 ETA 写入即可；另查「打电话」端侧劫持 |
| R8 | **trip×weather 反向修改未实现** | B3-1 | 「哪天下雨换成室内」→重规划把原行程原样端回（大雨天仍排海滨泳场）+「确认按此调整吗」 | trip.modify 消费 Day.weather 做室内约束（propose 已有天气软约束，modify 没有） |
| R9 | **provider 故障零降级**（含 P0 Q1） | B5-1②（+P0 A2-2a/A3-1/A5-2） | 「上班路上会堵吗」→「抱歉，处理失败」；api-football 超时→裸「处理失败」 | 故障时改派搜索或诚实说明数据源故障 |

另两条 P0 已录质量项仍在册：**Q2** charging.plan 直达判定零保留余量（10%→50km 对 47.7km
判足够）、**Q3** sports 日期界外回「今天」口径答非所问。花絮：B5-1①「早上好」→
「宝贝起床了吗？」（chitchat 语气跑偏，观察项不立卡）。

**两处假通过被本轮收紧钉死**（测试体系自身的校准，佐证「断言状态化」纪律）：
B3-1 原靠尾句「确认按此**调整**吗」撞中容忍词（行程根本没动）→ 判据收紧为「点名室内/
明说无需」；B3-3 原靠端侧劫持污染蒙对终态 26 → 新增 `action_absent` 断言把劫持钉成显性红。

### 收官全量跑（2026-07-15 canonical run，33 条一次连续执行，19 分钟）

**回归级 13/13 全绿 + 2 SKIP**（A2-2a/A5-2：api-football 当晚断供，skip 语义按设计生效；
两条在源活着时各有绿灯证据）；**目标级 7/18，11 红与首跑完全一致——红灯集稳定可复现**
（backlog 的价值前提）。记分卡：autonomy 13/18 · continuity 11/20 · honesty 3/3 ·
proactive 2/4 · interaction 3/6 · safety 1/1。时延基线（65 轮）：P50 5.7s / P95 40s /
max 62.8s——P1 定阈值的数据依据（§7 Q2）。报告工件 `journeys_report.{json,md}` 即本次。

### P2 已完成：HMI CDP 层固化（`test/hmi_cdp/`，2026-07-15 真栈真浏览器）

宿主 Node22 零依赖 driver（headless Edge + `Network.webSocketFrameSent` 实拦出帧）+
7 条 C 组用例。**决策：不给产品代码加 testid**——按可见文本选按钮/断言（文本即契约），
HMI 零改动。结果：

- ✅ C1 确认条：渲染→点确认→帧 `is_confirmation:true`→collector `trunk=open` 车况真变。
- ✅ C2a place_list 裸序号：「点一下第二个」→ HMI 改写「看金凤皇鲜切鸡煲火锅(南山店)的
  详情」+ `meta.nearby_poi_id` 透传——`App.tsx send()` 序号改写语义的真帧实锤。
- ⏭️ C2b dest_choice 回填：前提被 R1 族压掉（「惠州」被就近解析成「惠州出口」直接出
  charging_route，不出候选卡；「第一个」原样发出证明 HMI 无候选可改写，**非 HMI 缺陷**）
  ——用例改为前提未成立判 SKIP，R1 修复后自动恢复有效。
- ✅ C3 scene_list 卡按钮：点「露营模式」→帧「开启露营模式」→确认条→点取消链路通。
- ✅ C4 主动推送渲染：到点「提醒到点」卡（琥珀脉冲）出现→点「完成」→帧「完成提醒：X」。
- ✅ C5 过程区门控：重域调研出「理解需求…」四阶段；简单车控零过程。
- ✅ C6 右舞台车况联动：debug 压电量 55 → 舞台渲染 55。

坑两则：①headless 无地理定位——driver 三件套（`Browser.grantPermissions` +
`Emulation.setGeolocationOverride` + 预置 localStorage `cockpit.settings.v1.locationEnabled`
后 reload），「附近」类用例才有定位；②React 受控输入必须走原生 setter + `input` 事件，
直接赋值 value 不生效。截图证据 `test/hmi_cdp/shots/`（gitignore）。

### 红灯修复（2026-07-15，泓舟拍板「9 项全按建议修」+ Q1-Q4 按建议执行）

**批次1（快赢，commit `96093ad`）**：R4 reminder hint 回忆式 guard（「提醒我什么来着」不再
被当新提醒）/ R9 provider 故障降级升级为回落接地搜索（`_search(skip_sports=True)` 防二次吃
超时；FAILED 话术改 OK——聚合器吞 FAILED 的 scene 同坑，3 个旧契约单测按新契约更新）/
Q3 `_sports_date` 补 昨晚·后天·周X·下周X（自然周口径）+ 日期门控按所问口径诚实 /
Q2 charging 直达判定 15% 保留余量 + 短途尾缓冲不吞唯一补电点。真栈双源齐挂
（apifootball+exa 同晚超时）实测降级链层层诚实。

**批次2（commit `983bedf`）**：
- **R1**：navigation `_dest_matches` 包含式强校验（name_matches 的 2 字公共子串对直报
  目的地太松）→ 不匹配先去偏置全国重搜再地标 LLM，都验证不出保留原结果；就近类目流
  strict=False 免伤。裸城市名走 provider 新增 `geocode_level`（高德 level 权威判据）：
  navigation ≤4 字先判行政级→导航行政中心；charging `_clarify_vague_destination` 挂
  `_plan`+`_find` 双入口。**真栈五连转正**：广州塔→广州塔、宝安机场→深圳宝安国际机场、
  大梅沙→大梅沙海滨公园、惠州→dest_choice 候选卡（B2-3/B5-2 前提恢复）。
- **R2**：engine 插话不清挂起（`held_pending` 贯穿 + `_settle_session` + final 软提醒；
  新挂起单槽覆盖；「取消」仍即时）。B2-1 转绿（插话天气后「确认」→「下班模式建好了」）、
  B2-3 转绿。**测试防假绿**：插话轮必须用不自挂起的单步计划（stub planner 恒回两步
  计划会覆盖旧挂起「因错误的理由通过」）。
- **R3**：B1-2 被 R1 连带修复（focus→weather 链路本就在，是错 POI 进焦点装成缺失）；
  B1-3 真根因=nearby 把地名 location 槽交给**无城市偏置 geocode**（「万象天地」全国歧义
  →呼和浩特）→ `_resolve_center` 按当前坐标偏置搜名+包含校验取坐标；manifest guard
  放行焦点指代句（那附近/那边）。B1-3 转绿（润玺一期-东停车场）。
- **B3-2 本体**：navigation `_range_advisory`（续航盖不住本程含 15% 余量→话术主动建议
  补能，advisory 零动作，fail-open）。B3-2 转绿。

**批次3（R5/R6/R7/R8 + B2-3 序号尾巴）**：
- **R7**：navigation `_route_plan_to` 按 ETA 写 `REMINDABLE_ACTIVE`（「即插」契约兑现）+
  reminder `_REMINDABLE_REF_RE` 补 到之前/抵达前/快到 + `parse_lead` 一刻钟=900s +
  端侧电话分支对 提醒/别忘了/记得 让路。**A2-4 转绿**（「到之前一刻钟提醒我给张姐打电话」
  →「到达深圳宝安国际机场 今天 12:23 开始，提前 15 分钟提醒你」）。
- **R5**：端侧空调分支对 记住/我喜欢+温度 让路（偏好陈述不再被当场执行、参数化句不再
  「开了」敷衍）+ scene manifest 反界定（偏好陈述≠创建场景）+ planner prompt 偏好槽位
  护栏（**只能取自记忆召回，召回不到留空追问，绝不臆造数值执行**——首验抓到 LLM 臆造
  22 度直接开空调）。turn1 已验通过（「好嘞，已经记住啦」纯记忆应答）。
- **R6**：planner prompt 条件依赖判据（如果/要是…就→adaptive 先查后定）+ reminder hint
  guard 排除条件连词（**首验发现 hint 的「提醒我」把条件句整句抢走，prompt 根本没见到**）
  ——A1-4 断言同步收紧（原被用户原话回声蒙过=假绿）。
- **R8**：trip `_modify_rainy_days_indoor`——按 `Day.weather` **确定性**定位雨天 → 全部
  雨天合并一次 propose+ground 强室内约束（首验逐天各跑一轮 85.9s 撑爆 90s 网关窗口）→
  无雨诚实「不用调整」。
- **B2-3 序号尾巴**：新共享键 `CHARGING_DEST_CHOICES`（登记 shared_state.py+conventions
  §9）——澄清时存候选、续接轮 `_resolve_dest_ordinal` 把引擎补槽的字面「第一个」按序
  回填真名（消费即清）；首验发现真栈拿字面搜 POI 选到当前位置旁无关站。

**测试方法论追加**：mock KV 不回读（`make_context` 的 AsyncMock）——共享态断言必须
KV 钉内存（scene 测试 KV.bind 同款），本批抓到两个「因兜底断言蒙混」的假绿单测并收紧。

### 修复后旅程账本（2026-07-15 批次3 收口）

| 旅程 | 修复前 | 修复后 | 备注 |
|---|---|---|---|
| A2-4 | 🔴「暂不支持哦」 | ✅ | 「到之前一刻钟提醒我给张姐打电话」→「到达深圳宝安国际机场 今天 12:23，提前 15 分钟提醒你」 |
| B1-2/B1-3 | 🔴 | ✅ | 焦点位置迁移（天气@大梅沙 / 停车场@润玺一期） |
| B2-1/B2-2/B2-3 | 🔴🟡🔴 | ✅✅✅ | 中断-恢复三态全通 |
| B3-1 | 🔴 假重排→超时 | ✅ | 「第1天、第2天预计有雨，已把当天安排改成室内为主」36.3s |
| B3-2 | 🔴 | ✅ | 广州塔真解析 + 低电量补能 advisory |
| **A1-4** | 🔴 条件被吞直接建单 | 🔴（能力跃迁后残余） | **条件链已通**：plan=adaptive 只排天气步→T2 查到雨自主补建 reminder（`t2.iter replans=1`）→挂起追问时刻。残余两点：①挂起话术未携带前序天气结论（engine `_suspend` 只带挂起步 speech，用户听不到「明天有雨」）②「明早」无时刻 timeparse 不出→追问（半合理）。修复路径：`_suspend` 对 adaptive 多结果场景前缀已完成步简报（注意别让 trip 确认双重播报）｜留卡 |
| **B3-3** | 🔴 端侧劫持污染 | 🔴（根因移到记忆层） | 端侧/规划侧已修（turn1「记住啦」纯记忆应答✓、turn3 上云✓）；真栈探针揪出记忆两缺陷：**M1 场景参数漏进个人偏好**（钓鱼模式的空调 22 度被抽取成「你最喜欢 22 度」）、**M2 显式新偏好（26）未时序覆盖旧值**（superseded_by 未生效或 26 未被抽取）。属记忆子系统主题，立卡 |

**B5-1 黑洞互作修复（复验时抓到的 R2 次生 bug）**：wait_slot 挂起在 R2 后不被插话清除，
而 `_is_topic_change` 动词表判不出「我刚才让你提醒我什么来着」「那个提醒不用了」——全被
当补槽答案吃掉，挂起成黑洞（修复前首次换话题即清反而自愈）。三针：①wait_slot 语境内
取消词表 `_SLOT_CANCEL_RE`（`_confirm_reply` 的整句规则拦长句，不适用）②疑问/回忆式
（什么来着/吗/？/呢 结尾）判换话题③reminder REF 放宽 `到[^，。]{0,6}之?前`（「到公司
之前」中间隔词）。

**最终复验（2026-07-15）：回归级 15/15；目标级 16/18**（首跑 7/18）。**B5-1「一次通勤」
14 轮整旅程首次全绿**：搜索故障诚实降级→导航→顺路咖啡「第二个」设途经点→**「到公司之前
提醒我交周报」一轮成单（到达 12:51 提前 10 分钟）**→行车态车控→新闻→**「提醒我什么来着」
正确列提醒清单**→「那个不用了取消吧」多条候选澄清。B1-1「就去第二家」直达、B5-2 列表
消歧（重写为 nearby 双列表——原 dest_choice 前提被 R1 的「城市级直接成路线」更优行为
取代）、B4-1 均绿（回忆类 LLM 单采样方差给 retry:1 度量语义）。

**遗留卡（两张，独立会话认领）**：①A1-4 残余（`_suspend` 携带前序天气结论，注意别让
trip 确认双重播报）；②B3-3 记忆 M1/M2（抽取黑名单补场景参数 + 显式偏好陈述的时序覆盖）。
