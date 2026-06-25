# 2026-06-25 — 座舱 Agent 与通用 Agent「记忆系统」对标

> **调研日期**：2026-06-25
> **目的**：为本项目 memory 服务重构（见 [`docs/design/2026-06-25-memory-system-redesign.md`](../design/2026-06-25-memory-system-redesign.md)）提供外部依据——别人（国内外座舱 + 通用 Agent 框架 + 学术界）的记忆系统怎么做、走到哪、踩了哪些坑。
> **信息边界**：座舱产品部分多为**二手**（厂商发布会通稿、媒体解读、技术博客），非一手实测；带「推断」标注的是我的归纳，不是原文结论。学术部分为**论文摘要级**（一手 PDF 摘要 + 二手解读），未逐篇精读全文。结论以各来源**发布日**为准。

---

## 1. 为什么调研

本项目 memory 服务是核心服务里最没进化的一块：长期"画像"实现上只是一个 Redis JSON blob，只有 navigation 一个 Agent 在写（家/公司地点），车辆上下文是 mock，没有语义检索/偏好抽取/遗忘/多用户/时序（证据见设计稿 §1）。而架构文档 §7 早就承诺了"向量+结构化"的长期记忆层。重构前先看清行业与研究的事实坐标，避免闭门造车，也避免过度工程。

---

## 2. 国内座舱 Agent 的记忆系统（重点）

> 2025 是国内座舱从"语音助手/聊天机器人"集体转向"主动式智能体（Agent）座舱"的一年，记忆是这轮升级的核心卖点之一。([汽车之心：从聊天机器人到场景智能体](https://www.autobit.xyz/news/4339.html))

| 厂商 / 产品 | 记忆相关能力（按通稿/解读） | 对本项目的关键启示 |
|---|---|---|
| **吉利 · 超级 Eva**（Flyme Auto 2，千里科技+阶跃星辰+吉利，极氪 8X 首发） | ① **行业首发「流动记忆」**：记住用车习惯，并**统一吉利全品牌用户 ID**，让记忆/数据在旗下所有品牌车型间安全无缝流转；② **下一代座舱 Agent OS 内置「全场景化、自我训练成长」的记忆系统**；③ 个性化 AI 伙伴：可自定义名称/昵称/音色/性格/交互风格 | **记忆绑定用户身份而非车机**、可跨车迁移；记忆系统应"自我成长"（持续抽取）；**人格/称呼本身是一类画像记忆**（本项目已有 `assistant_name` 偏好，可纳入统一画像） |
| **蔚来 · NOMI Agents**（NomiGPT 多智能体架构） | ① 多智能体按**任务复杂度与时间跨度**建立**短时记忆 + 长时记忆**；② 短时：用户近期感兴趣话题、提到的人和物；③ **长时：记住用户及家人朋友，记住每一个人的喜好**；④ 由此演化主动、情感化服务 | **多用户/家庭记忆**是明确产品方向（"每个人的喜好"）——印证我们"多用户就绪"的决策；短时/长时分层与任务编排绑定 |
| **理想 · 理想同学 / Mind GPT / VLA 司机大模型** | 把"好的司机大模型"的终极指标拆成驾驶能力、专业性、以及**通过 Agent 和记忆能力建立信任**（理解用户意图、自主完成任务）；理想同学底座 300B，VLA 2025 下半年上车 | **"记忆 = 信任"** 的产品定位；记忆是 Agent 自主完成任务的前提，而非附属功能 |
| **小鹏 · AI 天玑（XOS 5.x）** | AI 座舱持续迭代（时空光影、宝贝护航、跨品牌手机互联等）；记忆/画像未在公开材料里单列强调（**推断**：能力存在但非当期主打点） | 关注点更偏交互与生态互联；记忆叙事弱于吉利/蔚来 |

来源：[观察者：千里/阶跃/吉利发布座舱 Agent OS](https://www.guancha.cn/GongSi/2025_07_28_784536.shtml)、[凤凰：Eva 与常见"AI助手"有何不同](https://auto.ifeng.com/c/8m0AZoLchjE)、[腾讯：超拟人智能体 Eva 上车](https://news.qq.com/rain/a/20250821A06AI200)、[证券时报：超级 EVA+G-ASD 4.0](https://www.stcn.com/article/detail/3681579.html)、[盖世：基于 NomiGPT 的车载 AI 方案](https://auto.gasgoo.com/news/202503/25I70421334C106.shtml)、[品玩：解读理想 VLA 司机大模型](https://www.pingwest.com/a/304595)、[知乎：理想"司机大模型"到底是什么](https://zhuanlan.zhihu.com/p/1903917270532625696)。

---

## 3. 国外座舱 / 语音 Agent

| 厂商 / 产品 | 记忆相关能力 | 启示 |
|---|---|---|
| **BMW Intelligent Voice Assistant 2.0**（基于 Amazon LLM） | 分析驾驶员**日常路线、音乐偏好、座椅调节习惯**生成定制建议；样板场景：探测到"每周一早上常停某咖啡店"→ 主动提示"要去附近的星巴克吗？" | **routine/程序性记忆 → 主动服务**的最具体样板（时间+地点+动作模式） |
| **Cerence / Bosch / Continental / Harman**（座舱语音 Tier-1） | 行业把 2025 座舱 Agent 的标志能力定义为**"自进化"= 长期记忆 + 反馈学习 + 主动认知** | 三件套：长期记忆（存）+ 反馈学习（改）+ 主动认知（用） |

来源：[ResearchAndMarkets：2025 座舱 AI 三大趋势（深度交互/大小模型共生/自进化）](https://www.businesswire.com/news/home/20250602474496/en/Global-and-China-Application-of-AI-in-Automotive-Cockpits-Research-Report-2025-Focus-on-3-Major-Trends---Deep-Interaction-Symbiosis-of-Large-and-Small-Models-Self-evolution---ResearchAndMarkets.com)、[GlobeNewswire：全球及中国座舱 AI 应用报告 2025](https://www.globenewswire.com/news-release/2025/05/22/3086409/28124/en/Global-and-China-Application-of-AI-in-Automotive-Cockpits-Research-Report-2025-Featuring-19-Cockpit-AI-Application-Cases-of-Suppliers-and-17-Cockpit-AI-Application-Cases-of-OEMs.html)。

---

## 4. 通用 Agent 记忆的工程与学术范式

### 4.1 记忆类型（2026 共识词汇）
- **Working / Core（工作/核心）**：当前对话窗口内的"在场"信息，相当于 RAM。
- **Episodic（情景）**：发生过什么——会话历史、事件。
- **Semantic（语义）**：已知的事实与偏好——"用户口味偏辣""家在 X"。
- **Procedural（程序）**：怎么做事的套路、惯例、工作流——**目前最不成熟、但对"行为一致 + 主动"最关键**。

### 4.2 代表性框架

| 框架 | 核心做法 | 可借鉴点 |
|---|---|---|
| **MemGPT / Letta** | 把上下文窗口当"虚拟内存"由 LLM 自己分页管理：Core(RAM，在窗口内) / Recall(可搜索的对话历史，像磁盘) / Archival(工具查询的冷存储) | **分层 hot→cold**，按需调入上下文，控制 token |
| **Mem0** | 框架无关；**对话→抽取要点→存向量库→检索时注入**；多 scope 身份标签（user_id/agent_id/session_id/app_id）；**异步写**（不阻塞回复）；检索用**多信号融合**（语义 + BM25 关键词 + 实体）+ 重排；区分 **provenance**（用户说的 vs Agent 推断的） | 抽取-存储-检索管线、多 scope、异步、provenance——**直接对应本项目要补的环节** |
| **Zep / Graphiti** | **时序知识图谱**：每条事实带 `valid_at` / `invalid_at` 双时态；三层（episodic 原始消息 / semantic 实体与事实 / community 社区摘要） | **"变化是演进不是覆盖"**——更新偏好时不删旧值，标记失效；可回答"某时点它相信什么" |
| **A-MEM / MemoryOS** | A-MEM：Zettelkasten 式互联笔记网络，由 Agent 自主建链；MemoryOS：短/中/长期三级，不同级不同管理策略 | 记忆可结构化成网络；分级生命周期管理 |

### 4.3 车载专属基准：VehicleMemBench
[VehicleMemBench](https://arxiv.org/pdf/2603.23840)（可执行的车载 Agent **多用户长期记忆**基准）把"车比聊天机器人多出来的难点"讲得最清楚：
- **多用户/多乘员**是车载记忆的核心差异点：用户身份切换、偏好冲突消解、共享车内的隐私。
- 追踪的偏好类目：音乐/娱乐、导航/路线、温度/舒适、个人习惯与沟通风格、历史交互。
- 车载专属挑战：**行车安全态门控、实时性、分心最小化、共享车隐私**。
- 评测任务：跨多轮/多会话保持、多用户场景、按乘员个性化准确率、抗幻觉、上下文相关检索。

---

## 5. 横向提炼：共同趋势 & 公认未解坑

**趋势（值得本项目对齐）**
1. **自进化 / 自我成长**：记忆系统持续从交互中抽取，而非靠 Agent 显式写入（吉利"自我训练成长"、Cerence"自进化"、mem0 抽取管线）。
2. **多用户 / 家庭记忆**：记住每个人的喜好、按乘员个性化（蔚来、VehicleMemBench）——**车载的核心差异点**。
3. **记忆绑定身份、可迁移**：记忆挂在用户而非车机上，跨车型/品牌流转（吉利流动记忆 + 统一 ID）。
4. **记忆 = 信任 / 主动服务的前提**：记忆是 Agent 自主完成任务、主动建议的基础设施（理想；BMW 周一咖啡）。
5. **时序/双时态**：偏好会变，"变化是演进不是覆盖"（Zep）。
6. **隐私端云分割 + 可遗忘**：敏感数据端侧、上云最小化、可导出可删除（VehicleMemBench、各厂合规叙事）。

**公认未解的坑（mem0《State of AI Agent Memory 2026》）**：时序抽象（长上下文下 ~25% 性能跌）、把"变化"当"替换"而非演进、记忆陈旧（高相关但已过时）、跨会话身份解析（匿名/多设备）、隐私/同意缺标准化、领域评测难（通用 benchmark 不预测专业场景表现）。([mem0 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)、[State of Agent Memory 研究综述](https://dev.to/vektor_memory_43f51a32376/the-state-of-ai-agent-memory-in-2026-what-the-research-actually-shows-3aja))

---

## 6. 对本项目的启示（结论）

1. **补"自动抽取"这一环**：把对话→偏好/事件抽取做成异步管线（mem0 范式），不再依赖各 Agent 手写画像。这是当前最大缺口。
2. **多用户就绪**：数据模型与 scope 带 occupant 维度，即便 PoC 默认单用户（对齐蔚来/VehicleMemBench，避免日后改 schema）。
3. **分层 + hot→cold**：保留 Redis 会话(工作记忆)，新增 pgvector 语义画像 + 情景记忆（对齐 MemGPT/MemoryOS 分级）。
4. **时序-lite 不上重图谱**：用 `valid_from + superseded_by`（不覆盖旧值）拿到 Zep 的核心收益，但不引入完整时序知识图谱（PoC 过度工程）。schema 留出长成图谱的形状。
5. **隐私端云分割 + 可遗忘**：复用已有脱敏；扩展现有 `export_profile/delete_profile` 到全量记忆。
6. **主动服务接口预留**：procedural/routine → 主动建议（BMW 周一咖啡），可复用项目已有 `agent.proactive` 通道（road-safety 已在用），列为后期阶段。

> 这些启示如何落到本项目的数据模型、proto、抽取/检索管线与分阶段计划，见设计稿 [`docs/design/2026-06-25-memory-system-redesign.md`](../design/2026-06-25-memory-system-redesign.md)。

---

## 来源汇总
- 国内座舱：[观察者-座舱 Agent OS](https://www.guancha.cn/GongSi/2025_07_28_784536.shtml)、[凤凰-Eva](https://auto.ifeng.com/c/8m0AZoLchjE)、[腾讯-Eva 上车](https://news.qq.com/rain/a/20250821A06AI200)、[证券时报-超级 EVA](https://www.stcn.com/article/detail/3681579.html)、[汽车之心-场景智能体](https://www.autobit.xyz/news/4339.html)、[盖世-NomiGPT](https://auto.gasgoo.com/news/202503/25I70421334C106.shtml)、[品玩-理想 VLA](https://www.pingwest.com/a/304595)、[知乎-司机大模型](https://zhuanlan.zhihu.com/p/1903917270532625696)
- 国外座舱：[ResearchAndMarkets-2025 座舱三大趋势](https://www.businesswire.com/news/home/20250602474496/en/Global-and-China-Application-of-AI-in-Automotive-Cockpits-Research-Report-2025-Focus-on-3-Major-Trends---Deep-Interaction-Symbiosis-of-Large-and-Small-Models-Self-evolution---ResearchAndMarkets.com)、[GlobeNewswire-座舱 AI 报告 2025](https://www.globenewswire.com/news-release/2025/05/22/3086409/28124/en/Global-and-China-Application-of-AI-in-Automotive-Cockpits-Research-Report-2025-Featuring-19-Cockpit-AI-Application-Cases-of-Suppliers-and-17-Cockpit-AI-Application-Cases-of-OEMs.html)
- 通用 Agent 记忆：[Mem0 vs Letta/MemGPT](https://vectorize.io/articles/mem0-vs-letta)、[mem0-State of Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)、[State of Agent Memory 综述](https://dev.to/vektor_memory_43f51a32376/the-state-of-ai-agent-memory-in-2026-what-the-research-actually-shows-3aja)
- 学术：[VehicleMemBench（车载多用户长期记忆基准）](https://arxiv.org/pdf/2603.23840)、[Toward Personalized LLM-Powered Agents（综述）](https://arxiv.org/pdf/2602.22680)、[Persistent Memory & User Profiles](https://arxiv.org/pdf/2510.07925)
