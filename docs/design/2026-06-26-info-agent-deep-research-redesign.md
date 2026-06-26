# 信息 Agent 重构设计 —— 联网查询分层 + 独立深度调研 Agent + 新闻升级（2026-06-26）

> **状态：P0 已落地（2026-06-26，评审通过后实现）。** 本文是把「联网查询 / 深度调研 / 新闻查询」从「单文件 god-object + 单轮检索、
> 深度调研缺失」重构为「**新建独立 `deep-research` Agent（四段流水线）** + info 联网查询分层升档 + 检索/接地内核抽到 `_sdk` 共享」的设计真相源。
>
> **P0 落地清单**：`agents/_sdk/{grounding,retrieval}.py`（共享内核）+ `agents/deep_research/`（models/pipeline/agent/manifest/main/Dockerfile/tests）+ info `_search` 切共享内核（零回归 122 passed）+ `progress.py`/`aggregator.py`/`planning.py` 接线 + `research_report` 卡（types.ts/Cards.tsx）+ compose 注册（端口 50073）。测试：deep_research 15 + 编排路由 2 新增，编排+_sdk 170、HMI 38+build 全过。
>
> **实现时对本文的一处务实修正**：原 §3.0 计划把 `search_factory`（`build_search_provider`）也抽到 `_sdk`，实现时改为**搜索 provider 仍归 info 拥有、deep-research 进程内 `import` 复用**（与 `trip_planner→navigation` 既有先例一致），避免 `_sdk → 具体 agent` 的反向依赖。仍抽到 `_sdk` 的是真正中立的 `grounding.py`（接地合成）+ `retrieval.py`（检索编排）。「化解 provider 重复」的目标不变。
> 目标定位：**座舱原生的「问答—调研」连续体**——快问快答秒回、深问深查渐进式语音简报 + 可读报告。
> 本次**不含**多模态调研（图表/PDF 生成）、不含付费深调研模型自训。
> 涉及 `agents/deep_research/`（新）、`agents/info/`、`agents/_sdk/`、`orchestrator/cloud/{progress,aggregator,planning}.py`、`deploy/`、`memory/`、`hmi/`。
> 前序记录见 `docs/design/2026-06-22-search-quality-and-card-redesign.md`（搜索质量重构，本文**继承**其接地合成/引用/诚实弃权内核）、
> `docs/design/2026-06-24-complex-task-thinking-and-process-region.md`（过程区+动态思考，本文复用其四阶段事件）、
> `docs/design/2026-06-20-standalone-agents-roadmap.md`（独立 Agent 扩展范式，本文新增的 deep-research 遵循其 manifest/打通契约）。

---

## 0. 一句话主张

座舱信息助手的护城河不是「车机版 Perplexity」（行车不能读长报告、手机深读更强），而是
**接地「我」的渐进式语音调研 + 可落地的产物**：把车辆上下文（位置/电量/行程）与
记忆画像（带老人/预算/口味）作为研究的隐含约束，边查边用 TTS 播简报，结论落成
可存记忆 / 推手机 / 转导航的产物。当前 info.search 只做了「单轮检索 + 一段合成」，
**深度调研能力完全缺失**。

把项目铁律「LLM 只产意图/计划，确定性 Executor 落地」**复刻进新的 `deep-research` Agent 内部**——这正是本次内核：
**LLM 提议研究计划与子问题，确定性循环检索/接地/出报告**，与 trip-planner 刚落地的同款流水线同构。

---

## 1. 市场调研结论（支撑设计取舍）

| 维度 | 关键事实 | 对设计的含义 |
|---|---|---|
| 「深度调研」已成独立产品品类（OpenAI/Perplexity/Gemini/Grok，2025）| 架构高度收敛：**澄清意图 → 规划分解子问题 → 迭代式 agentic 检索（读数百源、可回溯/转向）→ 接地合成带引用的分节报告**。耗时按分钟计（Perplexity 2–4min、Gemini 8–10min）| 这是**当前 info.search 缺的那一层**。照搬其「规划→迭代检索→分节合成」骨架，但用座舱可接受的**有界预算**落地 |
| Stanford STORM（学术范式）| 「多视角问询合成大纲」：先发现不同视角→模拟「带视角的写手向专家提问、答案接地可信源」→curate 成大纲→出带引用的长文；比 outline-RAG 基线组织性 +25%、覆盖面 +10% | **决定子问题分解方式**：不是平铺关键词，而是**带视角**（背景/对比/风险/最新进展）地拆，提升覆盖与结构 |
| 单轮 vs 迭代检索（arXiv 2509.04820 / MultiHop-RAG）| 单轮检索在「来源+时间+对比+实体」复合约束的多跳问题上**结构性漏掉后跳证据**；迭代检索+重排在多跳上带来更大绝对增益（证据召回 74.67%，~3 次检索）| **量化证据**：现 `_search` 单轮对复杂/多跳问题有天花板。深度调研 = 有界多轮迭代，对症 |
| 引用可信度仍是软肋 | 实测 Perplexity 引用幻觉率 ~37%、部分报告更高 | 项目已有的**强制引用 + 无依据即诚实弃权**内核是差异化资产，必须**贯穿到调研每一节**，不能因为「报告更长」就放松 |
| 国内车机（蔚来 NOMI Agents，2025-03）| 多 agent 框架显式分出**知识 agent（车百科/百科问答）**、生成 agent、任务 agent，经记忆/规划/动作协同 | 竞品已把「知识问答」单列角色。要赢在**调研深度 + 车辆接地**，不是又一个百科问答 |
| 国内车机（理想同学，2025）| MCP/A2A + CUA（Cockpit Using Agent）：车机 Agent 作 MAS 总控、分解规划后调三方 agent/app 完成复杂指令（MindGPT-4o）| 行业收敛到「多 agent + tool-use + 规划分解」（与本架构同构）。差异化要落在**信息域的接地与产物**，而非编排范式本身 |
| 座舱安全/形态约束 | 行车单次瞥屏 ≤2s（NHTSA）；座舱诉求是 TTS 播报、不是读长文（项目既有结论）| 深调研产物必须**双形态**：行车=渐进语音简报+一段结论；泊车/手机=可读分节报告。绝不在行车态弹长报告 |

来源见文末。

---

## 2. 现状盘点（`agents/info/src/agent.py`，1094 行）

**做到了**（继承 2026-06-22 重构，质量在线）：
- 接地合成内核 `_synthesize_grounded`（agent.py:530）：喂正文级原料、强制引用、**无依据即诚实弃权**——这是好资产，**抽到 `_sdk` 共享、被 info 与 deep-research 复用**。
- Exa 正文级检索 + 时效敏感 `livecrawl`（agent.py:601）+ AnySearch extract 补抓（agent.py:513）。
- 新闻 TTS 化编号速览（agent.py:1007 `_news`）、赛事结构化真实数据、天气/股票。
- `info.search`/`info.news` 已在 `HEAVY_INTENTS`（`orchestrator/cloud/progress.py:14`）→ 已触发动态思考 + 四阶段过程区。
- manifest `latency_budget_ms: 50000` 的注释（manifest.yaml:7）**已预留**「深度调研动态开思考后合成更慢」——但从未实现。

**差距（与「问答—调研连续体」的距离）**：

1. **联网查询只有单轮**（agent.py:589 `_search`）：一次检索 → 一次合成。对「多跳/对比/时间线/全面了解」类问题踩单轮检索的天花板（§1 量化证据），无分解、无迭代、无回溯。
2. **深度调研零实现**：全仓 grep `research/深度调研` 无任何能力（仅 manifest 注释与 provider 参数命名）。用户要「深入调研一下 X」时，落到单轮 `_search`，给一段浅答。
3. **检索/合成内核绑死在 info**：`_synthesize_grounded`/检索编排是私有方法，新 Agent 无法复用——**必须先抽到 `_sdk`**（否则新建独立 Agent 会重复造轮子，这正是「独立 Agent」取舍的最大成本，本设计用共享内核化解）。
4. **产物是「一段话 + 证据卡」**：无分节报告、无「存记忆/推手机/转导航」的落地闭环——座舱差异化没兑现。
5. **车辆/画像未接地进检索**：`_search` 只用 query 槽位，未把位置/电量/行程/记忆偏好作为研究约束（手机版 DR 给不了的恰恰是这个，却没用上）。
6. **新闻无个性化、无深挖桥接**：泛热点速览，未用 memory 画像排序，「深入讲讲这条」无法升入调研。

---

## 3. 目标架构

### 3.0 新建独立 `deep-research` Agent + 抽共享内核（地基；决策①）

按「独立 Agent」决策（§8），深度调研是**新的一等 Agent**，走项目「新增 Agent 标准流程」（CLAUDE.md §3）：

```
agents/deep_research/              # 包目录 snake_case；agent_id 'deep-research'（kebab-case）
  manifest.yaml                    # cap research.run；trust_level first_party；deployment cloud
  Dockerfile
  src/
    agent.py                       # DeepResearchAgent(BaseAgent)：四段流水线编排（§3.3）
    pipeline.py                    # plan/investigate/synthesize/brief 纯函数（仿 trip_planner/pipeline.py）
    models.py                      # ResearchTask/SubQuestion/Evidence/Report dataclass（§3.1）
  tests/                           # 契约测试 + 各阶段单测

agents/_sdk/                       # 抽出共享内核（与 _sdk/landmark、_sdk/location 同范式）
  grounding.py                     # 接地合成内核（从 info._synthesize_grounded/_parse_synth 抽出）
  retrieval.py                     # 检索编排（Exa+时效+livecrawl+extract 补抓统一封装）
  search_factory.py                # build_search_provider 提到共享层（info 与 deep-research 共用，零重复）
```

- 守约束：**不重写 gRPC/注册**（继承 `BaseAgent`）、**不改编排核心加 Agent**（注册中心发现）。
- 端口：`deploy/docker-compose.yaml` 注册新服务，分配新端口（见 `docs/conventions.md` 端口表，建议 `50073`，现 charging 50068/scene 50069/road-safety 50072）。
- **化解重复**：检索/合成/provider 工厂抽到 `_sdk` 共享层，info 改为引用共享内核（顺带消除 info 私有重复）——这是采纳「独立 Agent」后**避免 provider/内核重复**的关键动作。

> 注：info god-file（1094 行）本身的全面模块化**不在本期强制范围**；本期只「抽出被 deep-research 复用的内核」这一最小必要切口。info 内部 handlers 拆分作为可选清理（§5 备注）。

### 3.1 研究对象数据模型（调研的地基）

所有调研、报告卡、多轮追问、落地动作都作用在这个对象上（落 `agents/deep_research/src/models.py`）。

```
ResearchTask {
  task_id, session_id, user_id,
  question,                       # 原始研究问题
  constraints: {                  # 座舱差异化：把「我」接地进研究
    location?, vehicle_state?,    # 来自 meta（按 manifest context_scopes 下发）
    profile_prefs: [str],         # 来自 memory 召回（带老人/预算/口味…）
    time_now,
  },
  status: planning|investigating|synthesizing|done|failed,
  plan:  [SubQuestion],
  budget: {max_subq, max_rounds, deadline_s},
  report: Report | null,
}
SubQuestion {
  sq_id, text, perspective,       # STORM 式视角：背景/对比/风险/最新进展/适配「我」
  status: pending|searching|answered|gap,
  evidence: [Evidence],
  finding, confidence,            # 该子问题接地结论 + 置信度
}
Evidence { source_idx, title, url, source, published, excerpt, used:bool }
Report {
  summary,                        # 一段式结论（行车 TTS，≤2–3 句）
  sections: [{heading, body, citations:[source_idx], confidence}],
  sources: [{idx,title,url,source,published}],
  overall_confidence, gaps:[str], # 诚实标注未覆盖到的部分
}
```

### 3.2 联网查询分层 —— 编排层路由 quick/deep（修差距 1）

深度调研已是独立 Agent，「快问快答 ↔ 深度调研」的分层在**编排层**完成（不再 info 内部自调）：

```
用户问句
 └─(a) 显式深调研（"深入调研/研究一下/全面对比 X"）→ Planner 直接路由 research.run（新 Agent）
 └─(b) 普通搜索（"搜一下/查一下 X"）→ Planner 路由 info.search（现有单轮，秒级，不动）
 └─(c) 确定性兜底（planning.py）：弱 LLM 把"深入调研 X"误判成 chitchat/info.search 时，
        按触发词表纠偏到 research.run（沿用 trip 兜底的数据驱动触发词范式，不硬编进编排核心逻辑）
```

- info `info.search` 的 quick 路径**完全不动**（秒回、诚实弃权内核不变），仅其私有合成/检索改为引用 `_sdk` 共享内核（行为等价）。
- 判据双保险：**确定性触发词表**（数据驱动、可配）+ **Planner LLM 规划**。宁可少升档（普通搜索仍给可用答案），不可把简单问题拖进 30s 调研。

### 3.3 深度调研四段流水线（`agents/deep_research`，新增；修差距 2）

> 内核：**事实全部确定性产出**（检索/证据/置信度），**LLM 只在 (a) 提议子问题、(c) 受约束合成、(d) 润色话术**。
> 把「单轮 → 有界迭代检索」「平铺 → 多视角分解」两条线（§1 证据）落到实现，预算受座舱延迟硬约束。

**(a) Plan 规划 —— LLM 提议结构化子问题**
LLM 把 `question` 分解为 **3–5 个带视角的子问题**（STORM 多视角：背景/对比/风险/最新进展/「适配我」），
只产结构化 JSON、不产结论。把 `constraints`（位置/电量/画像）注入 prompt，让子问题接地「我」。
过程区 `plan` 阶段展示「研究计划：N 个子问题」。
*座舱澄清克制*：手机版 DR 会反问澄清，行车不便多轮——默认用 `constraints` 代替澄清，仅严重歧义时一次 `NEED_SLOT`。

**(b) Investigate 检索 —— 确定性有界迭代循环**
对每个子问题经 `_sdk/retrieval.py` 做 Exa 正文级检索（时效敏感开 `livecrawl`、`recency` 按需、空正文 extract 补抓）。
**子问题间并行**（asyncio.gather）压延迟。迭代：每子问题 1 轮基础检索；LLM 评估「覆盖不足/有 gap」→ 最多
**再追加 1 轮**（仿 DR 的回溯/转向），受全局硬预算约束：`max_subq≤5 × max_rounds≤2`、检索阶段总
`deadline≈35s`（给合成留时间，见 §6 延迟预算）。过程区 `execute` 渐进播报「已查到关于 X 的资料…」。

**(c) Synthesize 接地合成 —— 复用 `_sdk` 内核、升级为分节报告**
复用 `_sdk/grounding.py`（继承强制引用 + 无依据弃权），但产出**分节 `Report`**：
每个子问题/视角一节（结论 + 引用编号 + 置信度），跨节去冲突（沿用「不混矛盾数字」规则），整体
`gaps` 诚实标注未覆盖。一次 LLM 调用产结构化 JSON（控 token 防超时，沿用现有裁剪策略）。

**(d) Brief & Report 简报 + 报告 —— 确定性产出（决策②；修差距 4）**
- **TTS 简报**（行车安全）：`Report.summary` 一段式（≤2–3 句核心结论）+「完整报告已生成，停车后可看/已为你存好」。
- **`research_report` 卡**：分节正文 + 来源「前 N + 更多」+ 置信度徽标 + `gaps`；**泊车可读**、可「发送到手机 / 存为记忆」。
- 落地动作钩子：报告可转记忆（`agent.proactive`/memory 链路）、可桥接 trip-planner/navigation（「研究下周末去哪玩」→ 转行程）。

### 3.4 座舱原生差异化（护城河，对齐 §0；决策②）

| 能力 | 手机版 DR | 本设计（座舱独占） |
|---|---|---|
| 接地「我」 | 不知道你的车/位置/行程/画像 | `constraints` 把位置/电量/行程/记忆偏好作为研究隐含约束 |
| 形态 | 读长报告 | 行车=渐进语音简报+一段结论；泊车/手机=可读分节报告（双态门控复用过程区 driving 标注） |
| 产物 | 一份文档 | 可存记忆 / 推手机 / **转导航/转行程**（与 navigation/trip-planner 联动）|
| 反馈 | 进度条 | 复用已建**四阶段过程区**（规划→检索→合成）渐进播报，0 额外协议成本 |

### 3.5 新闻查询升级（`info.news` 适度增强；修差距 6）

新闻仍归 info（不迁出）。增强复用新建的调研 Agent：
- **个性化**（P2，复用 memory）：用记忆画像（关注科技/某公司/球队）对主题排序/筛选；无画像回退现有泛热点。
- **深挖桥接**（P2）：「深入讲讲第 2 条 / 这事来龙去脉」→ 编排层以该条主题路由 `research.run`（跨 Agent），复用同一流水线。
- **主动早报雏形**（可选，接现有 `agent.proactive`→HMI 链路）：routine 触发早间播报关注主题新闻。

### 3.6 编排接线（progress.py / aggregator.py / planning.py）

- `progress.py:14` `HEAVY_INTENTS` 加 `research.run`（自动获得动态思考 + 四阶段过程区）。
- `aggregator.py:43` `_card_priority`：`research_report` 是调研主卡，给独显高优先槽（与 `trip_itinerary`/`charging_route` 同级），防多意图被 `card_group` 吞。
- planning.py 兜底：弱 LLM 把「深入调研 X」误判为 chitchat 时，确定性兜底路由到 `research.run`（数据驱动触发词，**不硬编进编排核心逻辑**）。

---

## 4. 契约与改动清单

| 类别 | 改动 | 备注 |
|---|---|---|
| 新 Agent | `agents/deep_research/`：manifest（cap `research.run`、`context_scopes:[location,vehicle_state]`、`requires_permissions:[network.external,location.read]`、`latency_budget_ms≈85000`<90s 窗口、`edge_intents:[]`）+ BaseAgent + pipeline + tests + Dockerfile | 走「新增 Agent 标准流程」，编排核心不动 |
| `_sdk` | 抽 `grounding.py`/`retrieval.py`/`search_factory.py` 共享内核 | info 与 deep-research 共用，零重复 |
| info | `_search`/`_news` 改引用 `_sdk` 共享内核（行为等价）；其余不动 | god-file 全面拆分非本期强制 |
| Proto | **不需要改** | `ui_card` 自由 Struct（MessageToDict）；`research_report` 卡免改 proto/网关 |
| Orchestrator | `progress.py` HEAVY_INTENTS +`research.run`；`aggregator._card_priority` +`research_report`；planning 兜底触发词 | 复用已有过程区/卡择优机制 |
| Deploy | `docker-compose.yaml` 注册 deep-research-agent（新端口、注入 EXA/ANYSEARCH key、`POI`/`LLM` 等 env）| 复用 Exa/AnySearch，无新 provider key |
| Memory | 调研结果可存（profile/typed）；调研读画像偏好作约束 | 复用刚重构的 memory 服务 |
| HMI | `research_report` 卡渲染（分节/来源/置信度/gaps，泊车可读）+「发送到手机/存记忆」钩子 | 复用证据卡「前 N + 更多」范式 |

---

## 5. 分期（P0 → P2）

**P0 —— 地基 + 可信深调研 MVP（✅ 已落地 2026-06-26）**
新建 `deep-research` Agent + 抽共享内核到 `_sdk`（info 切到共享内核）+ `ResearchTask` 模型 +
Plan/Investigate/Synthesize/Brief 四段流水线（有界并行迭代检索 + 分节接地报告）+ `info.search` 编排分层 +
`research_report` 卡 + HEAVY_INTENTS/aggregator/compose 接线。
*验收*：「深入调研下理想 i8 的智驾方案」→ 命中 research.run、多视角子问题、每节带真实来源引用、置信度与 gaps 诚实标注；
单轮浅答消除；行车给语音简报、泊车给可读报告；延迟落在端到端窗口内（过程区渐进、不静默）。

**P1 —— 接地「我」+ 多轮研究上下文（✅ 已落地 2026-06-26）**
`constraints` 注入（时间/电量/**位置坐标反查城市**/**memory 画像语义召回**）让子问题接地「我」+
多轮追问复用 `ResearchTask`（落 memory profile KV `research_active`，「展开第 N 点」聚焦上轮对应小节
深挖、不重跑整份调研）+ 报告「存记忆」钩子（`记一下`→`ctx.remember`）。编排层补深挖追问路由
`_RESEARCH_FOLLOWUP_RE`（「展开第N点/再深入第2节/这部分详细讲讲」→ research.run）。
*验收*：真栈 e2e「深入调研固态电池」→「展开第1点」聚焦上轮第1节（量产时间表）出聚焦报告。
*紧前修复*：端侧 fast-intent 对裸「电池」过度匹配成电量查询（劫持含「电池」的调研主题）→ 收窄为
须与电量级/状态词同现（`fast_intent.py`，「深入调研固态电池」不再被劫持）。
*未做（顺延 P2）*：报告「推手机」（无真实手机通道，留 stub）。

**P2 —— 新闻个性化 + 深挖桥接 + 主动早报（✅ 已落地 2026-06-26）**
①**个性化**：`info._news` 泛新闻时 `ctx.recall` 召回画像兴趣→抽关键词(剥「用户关注/喜欢…」前缀)→
命中新闻置顶(稳定排序)+话术「已为你优先放了关注的X」；有明确 topic 不重排。②**深挖桥接**：`info._news`
落 `news_active`(标题列表) + 编排 `_RESEARCH_FOLLOWUP_RE` 加「第N条/这条新闻」→ research.run，
deep_research `_resolve_news_deepen` 取第N条标题做小型调研(`{title}（事件来龙去脉、背景与影响）`)。
③**主动早报雏形**：`info.on_start` 订阅 NATS `vehicle.state.changed`，晨间(6–10点)首次起步(挂挡/车速>0)
每日一次聚合 top3 新闻→发 `agent.proactive`(edge 网关已订阅→广播 HMI，复用 road-safety 范式)。
*验收*：泛新闻按画像置顶；「详细讲讲第2条」对该新闻做调研；晨间起步播报早报。
*注*：早报个性化受限于 proactive 广播无 user 上下文(暂用泛 top 新闻)；时间门控(6-10点)难单测，逻辑拆 `_has_drive_start` 单测。

**增量 —— 异步分钟级深调研（✅ 已落地 2026-06-26，解同步 ~90s 上限封顶的报告深度）**
用户明示延后/报告类信号（不急/慢慢查/查完告诉我/要详细完整报告，`_ASYNC_MARK`，仅认显式延后措辞、
不认「彻底/认真」以免改变即时预期）→ `_kickoff_async` **立即返回受理 ack（不带报告卡）** + spawn 后台
`asyncio` task（`_bg_tasks` 持引用防 GC、完成自动 discard）；后台跑 `deep=True` 更深流水线（子问题 6→9、
合成 `max_tokens` 2400→4000 / `timeout` 55→150 / 小节 5-7→8-12 / 每节证据 3→4，**不在请求路径故不受
90s 网关上限**）→ 落 memory（后台用 Agent 级持久 `self.memory` 重建 `Context`）→ 经 NATS `agent.proactive`
发**带 `card` 的报告**；edge 网关 NATS 桥透传 `p["card"]`（纯 JSON 加一字段，**无需改 proto**）→ HMI
`proactive` 分支渲染报告卡 + 朗读结论。Agent 补 `on_start` 连 NATS（**只发布不订阅**）；尾部延后语
`_ASYNC_NOISE_RE` 清理，防噪声污染子问题与报告卡 `question`。
*验收*：真栈 e2e（`test/e2e_research_async.py`）「深入调研固态电池…不急慢慢查查完告诉我」→ **秒级受理 ack**
→ ~分钟后主动推送 **8 节 / 33 源 / ~2820 字 / 置信度 high** 报告卡（深于同步 6 节/2153 字，且越过 90s 封顶）。
*可发现性补强*（2026-06-26）：异步是**显式延后语**触发，用户自然说「深度调研 X」走同步、猜不到异步存在 →
**同步出报告后 `follow_up` 主动教**「想要更深更完整的报告，说『慢慢查、查完告诉我』，我后台查完主动推给你」，
让用户跟着提示发现异步（零行为改变、零风险）。真栈探针验证同步路径 follow_up 确含该引导。

> 备注：① **info god-file 全面 handlers 化**作为可选清理项（非阻塞）；② **逐子问题渐进语音简报**需扩展
> agent→engine 流式契约（现过程区由编排层发、agent 仅 speech/final），留后续批次；③ 报告「推手机」无真实
> 手机通道，留 stub；④ **proactive 实时广播不补发**——后台查的几分钟内 HMI WS 断开（刷新/切走）则该次报告
> 丢失；要彻底稳须加「按会话补发 / 重连重放」（NATS 持久化或会话级 outbox），留后续小批次。

---

## 6. 易再踩约束（继承现有记忆，写进设计）

- **改 `progress.py`/`aggregator.py`/`planning.py` 必重建 cloud-planner；改 info/deep-research 必重建对应 agent 容器**（无卷挂载）——本会话前序已栽两次。
- **诚实优先**：检索/合成失败一律诚实报错或标 `gap`，**绝不 fallback mock 假数据**、绝不为「报告显得完整」编造来源/数字/因果（继承 [[info-agent-search-overhaul]]）。
- **强制引用贯穿每一节**：报告越长越要守，不能放松（§1 引用幻觉证据）。
- **跨 Agent 调用透传父 meta**（定位/电量）；`ui_card` 走 `MessageToDict` 后是原生 dict（继承 [[agent-collab-guardrails-gap]]）。
- **延迟硬约束**：深调研必须 < 90s 端到端窗口；LLM 上游 deadline 自治理（cap ~75s），避免激进派生 deadline 致接地合成爆预算「处理超时」（comms-hardening 已栽过，commit 2c2fd43）。
- **过程区脱敏**：渐进播报只发结构化阶段摘要，**绝不**下发 raw reasoning/prompt/参数（继承 [[complex-task-thinking-process-region]]）。
- **接不到资料 / 无定位 → 诚实降级**，不臆造来源或结论。
- **新 Agent 需 `context_scopes` 显式声明 location/vehicle_state**，否则编排最小化下发会剥掉这些键（继承 [[context-system-redesign]]）。

---

## 7. 测试计划

- **Agent 单测**（`agents/deep_research/tests`）：Plan schema 解析（子问题数/视角约束）、Investigate 有界预算
  （max_subq/max_rounds 不超、并行）、迭代追加轮（gap → 1 轮回溯）、Synthesize 分节引用（无依据节标 gap、不混冲突数字）、
  constraints 注入（画像/电量进 prompt）、多轮复用不重查、确认/收尾不循环。
- **共享内核单测**（`agents/_sdk/tests`）：`grounding.py` 接地合成（mock LLM）、`retrieval.py` 时效/补抓降级链；info 切到共享内核后零回归。
- **编排单测**：tier 路由（显式深调研→research.run、普通搜索→info.search、弱 LLM 兜底纠偏）。
- **全栈 E2E**（新增 `test/e2e_research.py`）：深调研问题 → 过程区四阶段 → 分节报告卡 + 真实来源；
  行车态降语音简报、泊车态可读；断言每节有引用、gaps 诚实、延迟在窗口内。
- **回归**：`info.search` quick 路径零回归（秒回、不误升档）；新闻速览不回归；全量 `pytest` 全绿 + HMI build。
- **基准对照**（可选）：构造 mini 多跳问题集，量化单轮 vs 深调研的证据召回/事实准确，作重构前后对照。

---

## 8. 已定方向（2026-06-26，用户拍板）

1. **承载位置 = 独立 `deep-research` Agent**（非 info 内承载）。理由：彻底隔离长跑 agentic 循环、可独立扩缩容、信息域职责清晰。
   代价（provider/内核重复）由「抽检索+接地合成内核到 `_sdk` 共享」化解（§3.0）。
2. **产物定位 = 渐进语音简报 + 泊车/手机读报告**（双态），非纯报告卡。行车安全 + 差异化护城河（§3.4）。
3. **节奏 = 先交 P0 评审、再迭代 P1/P2**。本次只交本设计文档；P0 实现待评审确认后另起。

---

## 9. 实测修复（2026-06-26，P0/P1 上线后真栈实测）

用户真栈实测暴露三个问题，根因经 deep-research-agent 日志定位（非参数微调）：

| # | 实测现象 | 根因（日志佐证）| 修复 |
|---|---|---|---|
| R1 | 调研结果只用一个信源、堆网页原文（登录/搜索/栏目/markdown）| `synthesis failed: DEADLINE_EXCEEDED`——分节合成**开思考**(MiMo 2048 reasoning)+大材料在 40s 内超时 → 退化 `_fallback_report` 直接堆首条来源原始正文 | `synthesize` **thinking=False**（分节合成是「组织已检索证据」的结构化任务，深度来自多轮检索而非此步）+ timeout 50；材料每子问题证据 3→2 |
| R2 | case「loop engineering」结果完全跑偏成「锂电池/电量72%」| **P1 注入的 `vehicle_state=电量72%` 污染子问题**：实测生成「loop engineering在电动汽车领域…中等电量状态(如72%)」→ 检索到锂电池 BMS | `_constraints` **删除电量注入**（与研究主题无关）；位置仅在地理相关(本地/选城/宜居)才注入；画像 min_score 0.25→0.35；`_constraints_note` 改「可选背景、仅当相关才结合否则忽略」；plan prompt 强约束「紧扣主题字面、不引入主题外领域」；视角去「适配用户」改「应用」 |
| R3 | `exa timeout` 大量、证据稀疏 | 子问题被 LLM 写成长句+括号举例（差搜索词）+ `livecrawl×5 并发`（Exa 18s 无重试）| plan prompt 要求**子问题≤25字、像搜索查询**；研究检索**不开 livecrawl、不收窄时效窗口**（时效由合成按来源 published 呈现）|
| R4 | markdown/网页噪声未渲染/未清理 | Exa 正文含页眉导航；合成输出含 markdown | 检索 `_clean_excerpt` 剔除导航噪声行；合成 prompt 要求 **body 纯文本、无 markdown、不贴网址**（链接在来源区，HMI 无需 markdown 渲染器）|
| R5 | info.search 同类问题：大正文(整页 wiki)合成超时退化堆原文（用户「同样处理」）| `grounded_synthesis`（info 与深调研共用的 _sdk 接地内核）经 HEAVY_INTENT 自动开思考 → 大正文 deadline 超时 | `grounded_synthesis` **默认 thinking=False**（接地合成是结构化抽取，不需深推理）+ timeout 20→25；info.search 实测 13s 干净合成「固态电池是…」（不再堆 wiki 表）|
| R6 | 报告太短(~985字)达不到深调研质量；信源疑非 Exa | 排查确认**信源确是 Exa**(Google/MS/IBM/RedHat/腾讯云/BAAI/学术，混少量内容农场)；`synthesize` max_tokens 1400 卡长度 + 材料太少(每节 2 证据×600字) | 子问题 5→6、每子问题检索 4→5、材料每节证据 2→3/正文配额 600→1000、`synthesize` max_tokens 1400→2400/timeout 55、prompt 要求**每节 250-450 字、综合多条来源、成体系**；实测 **985→2153 字/6 节/23 源/59s**（压在 85s 预算内）。真·分钟级超深需异步（P2）|

**实测验证（修复后真栈）**：「loop engineering」→ 多节/置信 high，准确定义为 AI 工程新范式（不再漂移电池）；
「动态数据流架构…全球首款…」→ 干净分节 + **诚实纠正「并非全球首款」**；单信源堆原文与登录/搜索/markdown 噪声消除；
**报告 985→2153 字（深 2.2 倍）**；info.search 大页面 13s 干净合成不再堆原文。

> 教训：①「接地我」要**按相关性注入**，绝不无条件把车辆状态塞进所有研究（电量与 99% 的研究主题无关）；
> ②深度调研的「深」在**多轮迭代检索**，合成步该快（thinking 关），开思考反而触发超时退化；
> ③LLM 生成的研究问题 ≠ 好搜索词，要约束简短；④报告深度 = `max_tokens` × 材料丰度共同决定（喂料不足则空泛），
> 且**同步路径受 ~90s 网关上限封顶**（sync realistic max ≈ 2000-2200 字/6 节）——**已落地异步分钟级深调研**
> 越过此封顶（见上「增量」：受理即返回 + 后台 `deep=True` + `agent.proactive` 推报告卡，真栈 8 节/33 源/~2820 字）。

> ⑤异步后台任务可安全长跑：Agent gRPC 服务进程常驻，`asyncio.create_task` + 集合持引用即可（不依赖请求级
> contextvar/ctx——后台显式传 meta、用持久 `self.memory` 重建 `Context`）；proactive 的 NATS JSON 桥**天然可
> 透传卡片**（加键即可、无需动 proto），是异步「推可读产物」的低成本通道。

## 10. 来源（市场调研）

- OpenAI, Introducing deep research — openai.com/index/introducing-deep-research（o3 端到端 RL、多步检索回溯、分析师级报告）
- Deep Research Agents: A Systematic Examination And Roadmap — arXiv 2506.18096（品类综述）
- Perplexity vs Gemini Deep Research 对比（ask-iterate-cite ~2–4min / 结构化报告 ~8–10min；引用幻觉率）— aicomparison.ai、blog.getbind.co、aryabhconsulting.com
- Stanford STORM（多视角问询合成大纲、接地引用）— storm-project.stanford.edu/research/storm、github.com/stanford-oval/storm
- One-shot vs Iterative Retrieval for RAG — arXiv 2509.04820；MultiHop-RAG（单轮漏后跳证据、迭代检索增益）
- 蔚来 NOMI Agents 多 agent 框架（知识/生成/任务 agent）— gasgoo.com 2025-03 报道
- 理想同学 CUA / MindGPT-4o（MCP/A2A、规划分解调三方）— 知乎《座舱 Agent 工程化研究》、腾讯新闻 2025-08
