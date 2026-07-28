# 意图理解与落域规划的系统性升级：从规则工厂到数据飞轮

> 日期：2026-07-28
> 状态：方向已获泓舟确认（2026-07-28），P0 落地中（分支 `feat/m5-p0-data-flywheel`）；P1-P4 待逐期开工
> 交付对象：`scripts/evolve.py`、`observability/` + `dashboard/`、`orchestrator/cloud/`（planner 上下文工程）、`skills/`（新增 exemplars 通道）、`orchestrator/edge/`（P3 端侧 NLU）
> 关联：母提案 [`2026-07-24-eva-benchmark-intelligence-upgrade.md`](2026-07-24-eva-benchmark-intelligence-upgrade.md)（C1/C2/E7）；[`2026-07-04-r4.1b-edge-objectification-and-nlu-decision.md`](2026-07-04-r4.1b-edge-objectification-and-nlu-decision.md)（识别侧 A/B 决策卡）；[`2026-07-24-m1b-self-evolution-shadow-nlu-rfc.md`](2026-07-24-m1b-self-evolution-shadow-nlu-rfc.md)（自进化 v1）；`skills/README.md`（检索注入范式，本方案的机制母版）；`docs/reviews/eval/shadow_nlu_report.md`
> 触发：泓舟 2026-07-28 提问——「落域/规划准确率永远在靠 badcase 改 manifest，偏规则式；这还算 agent 吗？是架构不好吗？如何系统性解决？」本文以全链路机制调查（三路并行盘点 + 关键断点逐一亲证）回答这三问。

---

## 0. TL;DR — 三问三答

**问 1：靠 manifest 定义来落域，还符合「agent」的定义吗？**
一半符合。「依赖 manifest」实际是两种性质完全不同的依赖：

- capability `description` 进 planner prompt、由 LLM 做工具选择（`context.py:147` → `planning.py:400`）——这就是 tool-use agent 的标准形态（Claude/GPT 的 tools 同样靠 description 路由）。这一半不是规则式，是 agentic 的正道。
- `route_hints` 正则在 **LLM 之后**以 `policy=replace` 直接改写整条计划（`planning.py:387`、`route_hints.py:62-68`）——全仓 32 条 hint 里 **31 条是 replace**。引擎文档自述其存在理由是「弱 LLM 常漏/误路由」（`route_hints.py:3`）。这不是在兜底，是在**替模型做决定**。

判据不在「用不用 manifest」，在**修一个 badcase 的产物是正则还是数据**。今天的答案是正则（§1.3）。

**问 2：是架构不好导致的吗？**
运行时架构不是。分层混合编排 / 规划执行分离 / 声明式扩展与对标对象同构（母提案 §2.1），不需要推翻。真正缺的是架构文档自己许诺过、但从未兑现的那一层：§15 R5「建标注与回流闭环」，以及 §10 的「意图识别准确率 / 路由命中率」指标——`observability/metrics.py:36` 的 `record_intent`/`record_route` 至今是**死代码**（全仓零业务调用）。「数据 → 智能」的回路在五个断点上断路（§2），于是「人写规则」成了准确率唯一的增长通路。**这是数据基础设施缺位，不是编排范式错误。**

**问 3：系统性解法？**
把「修 badcase 的标准产物」从规则换成数据：一次标注同时生成三种资产——**评测用例**（尺子变长）+ **检索范例**（当天泛化）+ **训练标注**（长期泛化）。围绕它建四段飞轮：

| 期 | 一句话 | 量级 |
|---|---|---|
| P0 修断点、立尺子 | evolve 提案半环一行级修复；落域指标接活；badcase 标注载体；catalog 裁剪缺陷应急 | 1-2 天 |
| P1 范例库 | planner 注入第三通道（骑 skills 双通道检索的现成机制），修复即时泛化，不再写正则 | 3-5 天 |
| P2 度量驱动治理 | 分布级落域周报；hint 影子裸跑退役流水线（规则只出不进）；强模型影子分诊 | 3-5 天 |
| P3 端侧 NLU | 8590 语料 + 范例库训练端侧分类器（Shadow NLU 已证 LLM 91.2% vs 规则 75.9%）；规则退回安全白名单本职 | 1-2 周 |
| P4 蒸馏（条件触发） | 范例库 ≥2k 且指标平台期后才谈；前置是 llm-gateway 完整样本导出 | 远期 |

「更像 agent」的可观测定义：**paraphrase 能泛化、低置信会开口问、一次修复会自动传播到同族说法**。端侧确定性快路径永远存在（安全与延迟是车规），那不是「不像 agent」，是本分。

---

## 1. 证据：痛点的机制构成

### 1.1 一句话的旅程（现状链路）

```
用户话术
 └─ 端侧 fast_intent（1727 行手写规则，无模型）
     ├─ conf≥0.85 且 LOCAL_INTENTS 且非危险 → VAL 本地执行（不上云）
     └─ 否则上云（端侧分类结果不随行，edge/server.py:602 原样转发）
         └─ 云侧 engine → ContextManager 装配（catalog 全量注入+预算裁剪）
             → skills 双通道检索注入（LLM 之前，软知识）
             → PlanBuilder 一次 LLM（submit_plan 工具强制，同时产出 steps/addressed/clarify/emotion）
             → RouteHintEngine（LLM 之后，32 条正则可 replace 整条计划）
             → engine 消费（拒识 → 澄清 → 执行）
```

### 1.2 规则面的真实规模

| 层 | 载体 | 规模 | 性质 |
|---|---|---|---|
| 端侧 fast_intent | `orchestrator/edge/fast_intent.py`（1727 行） | **313 个判定分支 / 284 个意图返回点** / 100 种对象 / 160 条 LOCAL_INTENTS / 22 处正则 | 关键词共现规则；置信度是硬编码字面量（0.9/0.92/0.95），不是算出来的 |
| 云侧 route_hints | 11/14 个 manifest | **32 条 hint（replace 31 / append 1）**，pattern 32 + guard 25 = 57 条编译正则，正则源文本 5275 字符 | LLM 之后整条计划改写；priority 靠跨 agent 手工避让（`agents/info/manifest.yaml:86-89` 自述「59 避开 60，因同级按 agent_id 字典序会反转评估顺序」） |
| 云侧确定性前置 | `planning.py:56-62,361-365`、`engine.py:129-195` | 祈使前缀否决 addressed、挂起/裸确认短路、注入检测 | 合法规则（系统持有事实/流程态，见 §4 准入判据） |
| 知识面 skills | `skills/` | 4 guide + 2 policy = **74 行知识正文** | 声明式知识（非规则）——但供给近乎为零，AGENTS.md 自述「M0b 后净增智能≈0」 |
| capability 描述 | 14 份 manifest / 47 个云侧 capability | 199 条 `examples` 例句；desc 判别式长描述只出现在被 badcase 打过的域 | LLM 语义落域的主依据；**examples 不进 planner prompt（§3-D4）** |

对照组：能力总量 125（云 47 + 端 78）、16 个 agent。**规则面（端 1727 行 + 云 5275 字符正则）与知识面（74 行）的比例约为 40:1。**

### 1.3 改进循环的生产函数：badcase → 规则

manifest 已事实成为 badcase 的主要修复落点（git log --follow 统计）：

- `agents/info/manifest.yaml` 改动 **17 次**，其中 6 次提交信息直接写着 badcase/劫持/真机漏例；nearby 7 次中 4 次、charging 7 次中 4 次同类。
- **活例 A（规则的负外部性）**：为修「『那是什么』落到 chitchat」加的 vision hint 含过宽分支 `|帮我看看`（`agents/vision/manifest.yaml:30`，replace，priority 58），guard 只挡了「这家/第N个/详情」——于是「帮我看看附近有什么咖啡店」被整句劫持进视觉问答，**连续两天出现在自进化日报里未修**（`docs/reviews/badcase/2026-07-26.md`、`2026-07-27.md`）。旧范式的下一步是给 guard 追加「附近|咖啡|吃的」……第 N 个词。
- **活例 B**：总体验收抓到 `shop.order` 路由掷硬币，修复方式＝再加一条 route_hint（验收报告 `:177`）。
- **活例 C**：「记住，我女儿叫小满」族被 addressed 判定方差误拒（07-27 日报 8 条 knowledge_gap 同根），修复方式＝directive 祈使前缀正则前置（07-27 当晚部署）。这条本身合法（系统持有事实的确定性判据），但说明**当前工具箱里「规则」是唯一可用的修复工具**。

结论：不是谁犯了错——在「无标注、无指标、无范例通道」的现状下，写规则就是唯一理性选择。要改变行为，先改变工具箱。

---

## 2. 五个断点：为什么 M0b/M1a/M1b 之后痛点仍在

M0b（skills）/M1a（submit_plan）/M1b（自进化+Shadow NLU）方向全对，但「数据 → 智能」回路在五处断路（关键处已逐一亲证）：

**断点① 自进化的「提案」半环从未闭合过。**
`PROPOSAL_FORBIDDEN` 含裸词 `"val"`（`evolve.py:67`），`forbidden_hit()` 对提案**全文**做小写子串匹配（`evolve.py:317-319`）；而三类提案模板的样板文案里自带 `eval 语料候选`（`:344`、`:355`，`eval` ⊃ `val`）与 `require_confirm`（`:336`）——**三类结构化草案 100% 自触发降级为纯报告**。证据：`.work/*/proposals/` 四天全空，两份日报所有提案标注「命中修改面白名单禁区」。治理③的本意是「不许碰 VAL/权限/支付」，实际效果是「不许提任何案」。
伴生缺陷：`plan_degraded` 信号读的是 collector 的 **200 条内存环**而非 SQLite（`evolve.py:180-183` 走 `/api/traces`，异常静默吞掉），collector 一重启历史就挖不到；triage LLM 批失败整批降 unknown 无重试（07-27 有 8/23 因此归因失败）。

**断点② 数据不可收割。**
obs schema 没有任何「正确落域」标注载体（`turns` 表只有二元 `badcase` + 自由文本 `note`）；plan 埋在 `spans.attrs` 的 JSON 字符串里（截 1200 字符、默认 7 天过期），SQL 无法按域聚合；`/api/export/{trace_id}` 只能单轮导出；`llm_calls` 只存 `prompt_tail` 尾 500 + `content_head` 头 800——**连一条完整的训练样本都复原不出来**。HMI 侧没有任何用户反馈通道。

**断点③ 线上没有落域指标。**
`record_intent`/`record_route` 是死代码；四个离线 eval 门禁天天 `exit=0`，与真机日报同日出血并存（07-27：73 轮里 23 轮命中信号，**19 轮 plan_mode=toolcall_degraded ≈ 26%**）。离线 eval 是回归闸（防倒退），不是分布尺（量进步）——「系统这个月变聪明了吗」这个问题今天无法回答。

**断点④ 三笔数据资产在闲置。**
① `feishu_intents_full.jsonl` 8590 条（domain/object 金标）只用于覆盖率报告，无训练消费方（全仓零微调/蒸馏钩子）；② Shadow NLU 已给出量化结论——规则 hit 75.9% vs LLM domain 准确率 91.2%，**navi 净增 42.2%、setting（最大流量 4087 条）净增 24.3%**——但报告无任何程序消费方（RFC 明确 v1 不接运行时）；③ manifest 里 199 条 `examples` 例句**不进 planner prompt**（`context.py:140-148` 只渲染 intent/slots/desc），只喂 registry 的逐字符打分。

**断点⑤ 规则只进不出。**
没有任何流程会问「这条 hint 模型现在自己会了吗」。`eval_route_hints` 116 条只保「该命中的命中」（外加 guardrail），从不问「还需要吗」。hint 的 priority 空间已经手工避让排布（§1.2），每加一条全局耦合加深一分。

---

## 3. 顺带发现的四个运行时缺陷（本次调查产物，修复归 P0/P2）

**D1（P0 排查+应急）catalog 预算会把 navigation 整域裁出 prompt。**
catalog 是**全量注入**不是检索——`PLANNER_CATALOG_TOP_K=20 > agent 数 16`，语义预筛恒 no-op（`context.py:392-393`）；渲染预算 8000 字符（`context.py:49`），超了就从尾部丢**非受保护** agent（`context.py:122-128`），而保护判据是「兜底 Agent ∪ **有 route_hints 的 Agent**」（`_always_include`，`context.py:55-61`）——与领域重要性无关。按当前 manifests 复算：全量渲染 9682 字符 > 8000，四个无 hint 的 agent（road-safety / parking-payment / **navigation** / manual-rag）**全部被裁**后仍 8101 字符，裁无可裁。`render_catalog` 的 docstring 还写着「正常情况下根本不触发裁剪」（`context.py:118`）——那是 M3/M4 新增 mcp-bridge、vision 之前的旧假设。navigation 恰是 Shadow NLU 里缺口最大的域（56.4%）。**真栈实际影响待复核**（可能被 trip-planner 的 navigate hint 部分掩蔽）：当前没有任何 obs 记录 catalog 渲染长度与被裁名单——静默降级完全不可见，这本身就是断点③的又一例。

**D2（P2）端云信息断链。** fast_intent 的分类结果不随请求上云（`edge/server.py:602` 原样转发），云侧 planner 不知道端侧已经算出的 domain/object/置信度。这既浪费一次免费信号，也让「端云分歧」这个最有价值的标注线索无从谈起。

**D3（P0 只加观测）replace hint 会跳过澄清。** hint 施加点在一切之后（`planning.py:387`），对「只有 clarify 的空计划」同样补步——补出 step 后 `engine.py:220` 的澄清分支不再进入。是否合意未有定论，先加命中/改写/跳澄清三个计数，拿数据再裁决。

**D4（P2）registry 关键词打分是逐单字符命中**（`registry/store.py:124-127`，注释自承中文噪声），并因此产生 desc 长度偏置——deep-research 的 manifest 注释明说「desc 刻意不加长，否则把 trip 的流量吸过来」。描述写法在为打分算法的缺陷让路，倒果为因。

---

## 4. 目标、北极星指标与规则准入判据

**目标一句话**：让准确率的边际增长来自**数据积累与模型泛化**，而不是人写规则；规则退回「安全与延迟」的本职。

### 4.1 北极星指标（周报，ProviderLock 锁定）

| # | 指标 | 现状 | 目标 |
|---|---|---|---|
| N1 | 分布级落域准确率（RoutingBench，canonical/paraphrase 拆列 + 分域混淆矩阵） | **不存在**（只有离线策展 eval，最大 177 例） | 建立基线后逐月爬坡，paraphrase 列为主指标 |
| N2 | 规则依赖率：hint 实际改写率 / 规则净增速率（新增−退役） / 端侧规则外承接率 | **不可测**（无 obs 计数） | 改写率趋降；净增速率 ≤0 |
| N3 | 修复泛化率：badcase 家族修复后，其 paraphrase 集通过率 | 无此概念（修复只保 canonical） | ≥80% |
| N4 | 提案可应用率：evolve 结构化提案占比与人审采纳率 | **0%**（断点①） | 结构化占比 ≥50%，采纳率可观测 |
| 辅 | toolcall_degraded 率 | 峰值 ~26%（07-27） | <5%（依赖在途的 M-D provider capability 协商，不重做） |
| 辅 | clarify_recall（该问的问了） | **1/6**（`eval_corpus/clarify_cases.yaml` 基线） | ≥4/6（P2 与低置信联动顺带修） |

### 4.2 规则准入判据（治理约定，随本文生效）

允许写规则的三类：**① 安全/权限/确认**（VAL 白名单、require_confirm、注入检测）；**② 端侧延迟敏感的高频确定形态**（车控核心快路径）；**③ 系统持有事实的确定性直答与流程态短路**（墙钟/身份/祈使记忆指令/挂起确认——「系统持有的事实不要交给 LLM」）。

不再允许新增正则的两类（须例外评审）：**语义边界消歧**（该走范例+描述）与**跨域组合知识**（该走 skill）。

新增 hint 的准入三件套：guard 必填 + guardrail 反例用例同 PR + `origin` 溯源注释；存量与新增 hint 全部进 nightly 影子裸跑名单（P2），退役由数据说话。

---

## 5. 方案：数据飞轮

```
                    ┌─────────────────────────────────────────────┐
                    │              一次 badcase 标注               │
                    │   （dashboard 一键：utterance → gold 落域）   │
                    └───────┬──────────────┬──────────────┬───────┘
                            ▼              ▼              ▼
                      评测用例         检索范例        训练标注
                   （RoutingBench）（exemplars 第三通道）（分类器/蒸馏语料）
                            │              │              │
              N1/N3 周报 ◄──┘   当天泛化 ◄──┘   P3 端侧 NLU / P4 蒸馏
                            │
                  hint 影子裸跑 → 退役候选（N2 规则净增 ≤0）
```

### P0 修断点、立尺子（1-2 天）

1. **evolve 三修**：`forbidden_hit` 改为对提案的**目标字段**（intent、目标文件路径）判定，不再全文子串（回填验证：用 07-26/27 的 `triaged.jsonl` 重跑 propose，必须产出草案）；`plan_degraded` 信号改走 `/api/turns/{id}`（SQLite 持久路径）；triage 批失败重试一次。
2. **落域可观测**：plan 的 intents 摘要升为 `turns` 列（或 `plan_summary` 表）使 SQL 可按域聚合；`record_intent`/`record_route` 接活；新增计数——hint 命中/改写/跳澄清、catalog 渲染长度与被裁名单（D1/D3 观测）。
3. **标注载体**：dashboard badcase 视图加「正确落域」结构化标注（下拉 = 47 云 capability + 端侧意图集），`/api/export/labels` 批量导出（utterance → gold intent → 实际 plan）。
4. **D1 应急**：推荐先把 `PLANNER_CATALOG_BUDGET_CHARS` 提到 16000（16 agent 量级下催化剂成本可忽略，静默丢域的代价远大于 token），保护判据机制化留给 P2 检索化根治；真栈复核 navigation 是否确实被裁及被谁掩蔽。
5. **示范修复**：vision hint 删过宽分支 `|帮我看看`（规则**收窄**而非增生），「帮我看看附近有什么咖啡店→nearby.search」进 guardrail 语料——新范式第一例。

### P1 范例库 Exemplar Store（3-5 天）——飞轮的核心新机制

**契约**：`skills/exemplars/<domain>.yaml`，每条 `{text, plan: [{agent, intent, slots骨架}], source: trace|manual|manifest, added, tags}`。定位为**最软知识层**（权威链在 PlanningGuide 之下）：只作为 few-shot 影响 LLM 判断，**不做硬路由**——这是它与 route_hints 的本质区别，也是它劫持面远小于 hint 的原因。

**注入**：planner 第三通道，机制整体骑 `skills.py` 现成范式——hybrid 双通道检索（词法 bigram + 语义余弦同源 Embed）、fail-open、预算硬帽（k=2-3、约 700 字符）、`plan.exemplars` obs 归因（对齐 `plan.skills` 契约）、T2 再规划继承。语义阈值不拍脑袋：照抄 skills 的 paraphrase 扫描定阈方法论（0.40 的来历）。

**三个来源**：
1. badcase 标注一键转化（dashboard 按钮：标注完成 → 生成范例 + RoutingBench golden 各一条）；
2. **manifest 199 条 examples 批量导入**（source=manifest，只带 intent 无槽）——死资产即刻盘活；
3. **evolve 第四类提案**：route_error/slot_error 默认产**范例草案**而不是 regex 草案（现有的 bigram 拼 pattern 生成器 `_kw_pattern` 随之退役）。范例草案不改任何运行规则、天然过 CI 门禁，人审只看「gold 标得对不对」——这就是 N4 从 0% 起飞的路径。

**门禁**：范例文件的 intent 存在性静态校验（照抄 `eval_skills.py` 的 expect_* 校验）；每条范例自动生成 golden 进 RoutingBench；CI 阻断。

**DoD**：「附近咖啡店」badcase 族改由范例修复（vision hint 不加任何 guard 词）；修复泛化率（N3）首测 ≥80%。

### P2 度量驱动治理（3-5 天）

1. **RoutingBench 周报**：聚合现有语料（mode_routing 122 + route_hints 116 + rejection 47 + clarify 23 + journeys 86 句 + badcase 转化持续增量），统一「utterance → 期望落域」口径的 runner，输出 N1 分域混淆矩阵与 canonical/paraphrase 拆列；8590 语料做 domain 级底座。独立 runner，不占 M-A 的五个 E2E 主分组。
2. **hint 退役流水线**：nightly 对 hint 命中语料双臂裸跑（hint off / on），「模型+范例已答对」的 hint 出**退役候选提案**（人审后降级为 guardrail 用例保留）——规则第一次有了「出口」。首个试点：收窄后的 vision hint。
3. **强模型影子分诊**：evolve 归因追加一步——badcase 用 `@primary` 强档同 prompt 影子重规划：影子对了＝模型能力问题（累积 P4 蒸馏证据）；影子也错＝信息/知识问题（走范例或 skill 提案）。「模型不行还是信息不够」从此不靠猜。
4. **catalog 检索化（D1 根治 + D4）**：`PLANNER_CATALOG_TOP_K` 调至 8-10 让 registry 语义预筛真正运转；`_always_include` 判据机制化（manifest 显式 `core` 声明，替代「有 hint 就保护」的巧合耦合）；registry 逐字符打分换 bigram/词级（`eval_registry_resolve` 20 条基线护航）。
5. **端云透传 + 分歧驱动标注（D2）**：慢路径上云带 `meta._edge_nlu`（domain/object/conf），planner 上下文加一行「端侧初判」；**端云分歧轮自动进标注队列**——把标注成本聚焦在信息量最大的样本上，这是 Shadow NLU 从离线报告走向在线数据引擎的第一步。

**DoD**：第一期周报出数（N1 基线成立）；hint 存量首次净减（≥1 条退役）。

### P3 端侧 NLU（1-2 周，可与 R4.1b P1 对象化并行）

R4.1b 决策卡设定的 B 路启动条件**已经满足**：规则表逼近 ~300 条（现 313 个分支）、覆盖率 <85%（现 76.2%，端侧应接子集 80.0%）、且 Shadow NLU 已量化收益（LLM 91.2% vs 规则 75.9%）。「NLU 只解决识别不解决执行」的裁决仍然有效——执行侧对象化（R4.1b P1）并行推进，识别了执行不了的域不切。

- **训练集**：8590（domain/object 金标）+ 范例库 + P0 起积累的标注；**模型**：小型中文 encoder 意图分类头，ONNX 端侧部署（完全复用声纹 CAM++ 先例：`models/` 拉取、缺失不阻塞、disabled 整链回落）。
- **形态**：fast_intent 改双层——分类器产出 `(domain, intent, conf)`，接架构 §3.2 的 θ_high/θ_low 双阈值（该伪码写了半年，第一次拿到真概率——现状规则命中是 0/1，置信度是硬编码字面量）。高置信本地执行（仍过 LOCAL_INTENTS ∩ VAL ∩ require_confirm 三闸）；中置信上云带初判；低置信裸句上云。让路守卫与安全白名单保留为规则层。
- **灰度**：先 shadow 线上双跑只记 obs 不生效（Shadow NLU 在线化），按域放量——navi/setting 先行（正是净增最大两域）。

**DoD**：端侧覆盖率 76.2% → 85%+，hijack_guard 28 条回归全绿、误接率不升。

### P4 planner 蒸馏/微调（条件触发，不排期）

触发条件：范例库 ≥2k 且 N1 平台期 ≥2 周。前置：llm-gateway 增加完整样本导出钩子（obs 现存的 tail 500/head 800 截断片段不可复原样本）。形态：强档标注（影子分诊已在积累）→ `@fast` 档 SFT/LoRA。不做端侧大模型微调（架构既有裁决不变）。

---

## 6. 复用不重建

| 既有机制 | 在飞轮中的角色 |
|---|---|
| `skills.py` hybrid 检索/预算/fail-open/归因/T2 继承 | exemplars 第三通道的机制母版，逐项对齐 |
| `eval_skills.py` 三车道 + CI blocking 范式 | RoutingBench 与范例门禁直接复制 |
| `evolve.py` 五步流水线 + 治理①-⑥ | 修断点后挂第四类提案与影子分诊；**人审红线不变**（治理⑤：不自动改仓库） |
| registry pgvector + llm-gateway Embed 同源 | catalog 检索化与范例向量共用一个出口 |
| obs SQLite + dashboard badcase 视图 | 标注载体与导出的挂点 |
| ProviderLock / 请求级 pin | 周报与退役判定的评测锁定（canonical 跨 provider 不可直比的既有纪律） |
| M-D `GetCapabilities`（在途） | toolcall_degraded 治理直接引用，不重做 |
| R4.4 置信度三段式 | 低置信 → 澄清联动（clarify_recall 1/6 的修复走这里） |
| R4.1b 端侧对象化 P1 | P3 的执行侧地基，并行不串行 |

## 7. 分期依赖与排期建议

P0 → P1 → P2 → P3 严格顺序（P4 条件触发）。与在途的验收余项 M-A~M-D **无共享文件面**；建议 M-A（可信尺子）收口后开工——两把尺子各管各的（M-A 管 E2E 真实性，RoutingBench 管落域分布），互不占用对方协议。若期号沿用主线命名，本方案即候选 **M5**。

## 8. 风险与明确不做的事

| 风险 | 对策 |
|---|---|
| 范例污染/错标 | CI 门禁（intent 存在性 + golden 自检）+ 人审 gold；范例是最软层，错了只影响 few-shot 不锁死路由 |
| 检索误召回制造 prompt 噪声 | 预算硬帽 + k=2-3 + 语义阈值按扫描定（skill 层教训：钳制下限按语义定，不拍 0） |
| 分类器把安全意图误分 | 执行仍过 LOCAL_INTENTS ∩ VAL ∩ require_confirm 三闸——**识别错≠执行错**（与「声纹不作鉴权因子」同一论证结构） |
| 端侧算力/包体 | 小模型 ONNX + 缺失禁用回落规则（models/ 声纹先例） |
| provider 方差污染评测与退役判定 | ProviderLock pin（已有机制），退役判定必须 pin |
| 标注负担失控 | 不搞全量标注：badcase 驱动 + 端云分歧驱动，只标信息量最大的样本 |

**明确不做**：不换编排范式（T0/T1/T2 不动）；不动安全红线（VAL 唯一车控路径 / 中央确认闸 / 声纹非鉴权 / S2S 单出口 escalate）；不自动合入规则或知识（提案永远人审，提高的是提案质量与可应用率，不是自动化程度）；不在本轮做端侧大模型或全量微调；`sim.adas.*` 维持 backlog。
