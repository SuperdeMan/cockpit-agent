# 四模式路由与回答质量重设计（直答 / 联网查询 / 新闻 / 深度调研）

日期：2026-07-12　发起：泓舟　状态：实施中（P0→P2 分片推进，落地态见文末进度表）

## 0. 结论

用户一句话要落到四种回答模式之一：**①chitchat 直答**（不联网）、**②info.search 联网查询**、
**③info.news 新闻**、**④research.run 深度调研**。本设计解决两个问题：

- **A 模式进入准确率**：现状 Planner 一次 LLM 调用从能力目录裸猜 intent，prompt 无任何
  「直答 vs 联网 vs 新闻 vs 调研」判据，时效性判断在路由层完全缺席；chitchat 是
  「匹配失败/LLM 降级/权限过滤」的统一落点（系统性偏向陈旧直答）；确定性护栏只保
  research.run（2 条 priority=100 hint），其余三模式 0 条；四模式边界零评测。
- **B 各模式结果质量**：chitchat 无日期锚点/无时效护栏且默认快模型；info.search 查询零改写、
  证据薄不重试、重排完全不看 published 时间；news 摘要 LLM 调用漏关 thinking（存量 bug）；
  research 深挖是聚焦重跑、不复用上轮证据。

方案三层：**P0 路由准确率**（eval 先行 + 全声明式：manifest 判别化 + prompt 通用判据段 +
两条新 route_hints）、**P1 直答与时效兜底**（chitchat 日期锚点 + **引擎级 escalate 通用机制**）、
**P2 检索/合成质量**（薄证据重试、新鲜度加权重排、research 深挖种子复用等）。

## 1. 模式边界定义（判据，同时是语料标注标准）

| 模式 | 判据 | 反例（不属于） |
|---|---|---|
| chitchat 直答 | 闲聊/情绪/创作/观点 + **不随时间变化**的常识/原理/定义/历史 | 任何答案会随时间变化的事实 |
| info.search 联网 | 要**一个具体问题的答案**，且答案时效敏感或模型不确定：近期事件/价格/榜单/事实核查 | 只想浏览一批资讯（news）；要系统了解（research） |
| info.news 新闻 | 想**看一批**资讯：泛新闻、话题新闻（「X最新消息/X新闻」） | 对单个事件要解释（search） |
| research.run 调研 | **系统性**了解/对比/评估一个主题：多视角、分节报告 | 单问题快答（search） |

时效判据（planner prompt 通用段）：答案会随时间变化（近期事件/价格/榜单/比分/发布/
「最新/现在/昨晚/今年」类）→ 必须联网检索类能力，禁止凭记忆直答、禁止空计划、禁止当闲聊。

## 2. P0 模式路由准确率（全声明式，零新增热路径 LLM 调用）

### P0-1 评测先行（`test/eval_mode_routing.py` + `eval_corpus/mode_routing_cases.yaml`）

- 语料 122 条五桶：typical（四模式典型各 ~10）/ boundary（新闻vs搜索、调研vs搜索、直答vs搜索）/
  adversarial（时效伪装闲聊「昨晚欧冠谁赢了」、常青伪装、动词陷阱「搜索引擎是怎么工作的」）/
  followup（history 前置的「展开第三点」「详细讲讲第3条」）/ guardrail（天气/股票/赛事/提醒/
  导航/附近/车控不被四模式吸走）。
- 双口径：`--live` 真 `PlanBuilder.build()`（LLM 规划 + route_hints 后验 + 降级链的**端到端
  最终 intent**）归一成模式 + 混淆矩阵；离线确定性子集（`initial_intents`/`expect_det_intents`
  字段）直调 RouteHintEngine + 真实 manifests（同 eval_route_hints 装配路径）。
- `expect_mode` 支持 `a|b` 双容忍（对抗例的合法双落点，如 昨晚欧冠→sports|search）；
  weather 族归并（forecast/alerts/indices/air_quality 不苛求族内选择）。
- **语料描述目标态**：P0-4 新 hints 落地前确定性子集有 21 条预期内 FAIL（钉的就是缺口），
  落地后转 PASS 属预期 improvement，基线随之重写。
- 顺序纪律：先 `--live --write-baseline` 采改动前基线（含混淆矩阵），再动 P0-2/3/4。
  同时刷新滞后的 route_hints 基线（8 例 → 28 例，2026-07-03 起未随语料更新）。

### P0-2 manifest 判别化（声明式主承载）

capability description 是 Planner catalog 里 LLM 唯一能看到的判据文本（examples 只进
Registry 语义路由，不进 Planner prompt——所以两者都改，回归也要**双跑**）：

- chitchat：限定「闲聊/情绪/**不随时间变化的常识**」，明示「实时/近期信息必须走联网检索类能力」。
- info.search：「**凡答案会随时间变化的问题都用本能力**，不要闲聊直答」。
- info.news：「想**看一批**新闻时用；要具体答案→搜索；要系统了解→调研」。
- deep-research：补一句反界定「只要一个快答案时用搜索类能力」。

### P0-3 Planner prompt 通用判据段（≤10 行，不点名 agent_id/intent）

`_PLANNER_BASE` 在「== 通用规则 ==」前插「== 时效与深度 ==」段：时效判据 + 常识判据 +
深度判据（浏览→新闻类 / 要答案→搜索类 / 系统了解→调研类）。风险：Planner 一次调用同时承担
规划+受话+澄清（R4.4），prompt 增长须跑 eval_rejection / eval_rejection --clarify /
eval_registry_resolve / eval_mode_routing 四套 live 回归，零回归才合入。

### P0-4 补 route_hints（info manifest，priority 59）

R2.1 机制的对称补齐：research 有确定性召回护栏而 search/news 没有，弱 LLM 误判时只有
research 有网兜。两条新 hint（**59 级**：避开 trip.modify=60 同级撞车——同级按 agent_id
字典序 info < trip-planner 会改变评估顺序；仍高于 sports 58 / reminder 56 / nearby 55）：

- **info.search**：句首显式搜索动词（搜索(?!引擎)/搜一下/搜/查一下/查询/检索/百度一下…，
  裸「查」刻意排除防「查理和巧克力工厂」误伤），`^` 锚定防多意图句被 replace 吞掉；
  guard 覆盖已有专用域（天气/洗车/紫外线/指数/股价/新闻/赛事/提醒/待办/导航/路线/附近/
  加油站/充电/停车/餐厅/电量/胎压/说明书/歌曲/播放）+ 个人事实（我的/我老婆…→记忆召回，
  不上网搜）+ 调研词（让位 research 100）。
- **info.news**：动词+新闻词（看看/来点/有什么…{0,6}新闻|头条|资讯|要闻）、裸新闻句
  （^今天有什么新闻$ 式）、话题式（^X的新闻$/^X最新消息$/^X最新动态$）；guard
  播/放/听/联播（媒体域）+ 订阅/打开/关闭/提醒 + **搜/查**（「搜一下今天的新闻」双 hint
  互斥都不命中→留给 LLM，刻意的保守面）。
- slots：search 传 `$1` 捕获组（剥动词），news 留空（topic 由 handler
  `_extract_news_subject` 从 raw_text 兜底提取，比捕获组稳）。

### P0-5 回归 gate 与基线纪律

P0-2/3 合入后四套 live eval 零回归（不写基线）；P0-4 合入后 route_hints 基线重写
（28+~16 例）+ mode_routing 基线更新（确定性子集转绿属预期 improvement）。

## 3. P1 直答质量与时效兜底

### P1-1 chitchat 日期锚点 + 深度引导

- system prompt 注入「今天是YYYY年MM月DD日」（复用 `_sdk.grounding.shanghai_now`）+
  「实时/近期事实不确定就明说无法确认并建议联网查询，绝不编造」。
- manifest 加 slot `depth`：Planner 对知识/解释类问题传 `depth=deep` → `_resolve_model`
  升 primary 模型（寒暄/情绪仍 @fast）。会话级 `meta.model_pref` 语义不变，slot 优先。

### P1-2 引擎级 escalate 通用机制（**泓舟 2026-07-12 拍板**，唯一编排改动）

问题：任何路由改进都堵不死全部退化路径（LLM 抽风、解析失败、权限过滤都统一落
chitchat）。机制：Agent 执行后可在结果里声明「这题我不该答，改派给 X」，engine 通用消费
——与 RouteHintEngine 同一「机制化+契约测试」先例，任何 Agent 未来可用。

协议（登记 docs/conventions.md 保留键）：
```
AgentResult.data["_escalate"] = {"intent": str, "slots": dict[str,str], "reason": str}
```
engine 收 final 后有界消费：每轮最多 1 跳；目标步经 registry/agent_map 解析 +
`PlanBuilder._validated_steps` 装配（heavy/latency_budget/权限自动带出，**绝不裸
call_agent**——默认 10s 超时会打死 info.search 50s 预算）；escalated 结果中的
`_escalate` 不再消费（结构性防环）；消费后 `pop("_escalate")` 再进聚合（防 F3
slot_refs 误引用）。挂点两处：D0 流式直通 final 处（**streamed=True 时忽略**，已播报
不二次回答）+ executor 路径结果循环后。

chitchat 侧：system 加规则「必须获取实时信息才能正确回答时只输出
`<search>不超过20字的搜索词</search>`」；handle 解析 marker → 零语音 + `_escalate`
到 info.search；handle_stream **头部缓冲 <12 字符**判定 marker 前不 yield（零 delta
才允许 escalate，与 engine 端 streamed 忽略双保险）。

体验：误接时效题 → 快模型短判（~1s）→ 自动转 info.search（heavy → 过程区可见）→
搜索结果卡。放弃的替代方案：chitchat 子调用 info（AgentClient 协作，零编排改动，但等待
期无过程区、chitchat 预算被拖宽、角色越界）；仅护栏不自动纠正（多一轮往返）。

## 4. P2 检索/合成质量

- **info.search**：①薄证据（有正文源 <2）一轮确定性重试——先剥口语前缀再退
  「{query} 详细介绍」（仿 deep-research backtrack），`_merge_sources` 按 url 去重；
  ②新鲜度加权：新增 `rerank_fresh_authority`（排序键 `(-在时效窗口内, -tier, 原序)`），
  仅 recency_days>0 时启用；`rerank_by_authority` 一字不动（deep_research 两处在用）；
  ③合成源 top5→6（rest_cap 900→800，总材料 ~6400 字符）；④confidence=low 时 follow_up
  提示「深入调研」（纯文案，不自动升级）。
- **news**：①`_summarize_news_list` 显式 thinking=False（存量 bug：info.news heavy=true
  的 meta thinking=on 波及，与接地合成关思考策略矛盾）；②话题新闻 Exa 加
  livecrawl=preferred（单调用无并发超时风险；综合合并路径不开）。
- **research**：①plan prompt 子问题数与解析 cap 对齐（"5-6"/cap6、"8-9"/cap9）；
  ②backtrack 从「空结果」放宽到「<2 条」且**合并不替换**；③深挖种子复用：
  `_save_task` 每节存 citations 的 urls（≤3），`_resolve_deepen` 命中时 extractor 并行
  取上轮正文作 sq0「上轮结论回顾」预置证据（investigate 对已带 evidence 的子问题跳过
  检索——通用幂等化）；兼容旧 RESEARCH_ACTIVE 无 urls 字段。

## 5. 非目标（本次不做）

search→research 自动升级（仅 follow_up 文案）、逐句引用校验、策展级新闻源/RSS、
`_fallback` 改落点（chitchat 兜底不变，escalate 接住剩余）、异步报告 outbox 补发、
llm-gateway 预算级联死代码修复（`_read_budget` 的 deadline 分支从未被传参激活，另立卡）。

## 6. 验证与重建

- 评测：见 §2 P0-1/P0-5；全量 `python -m pytest --import-mode=importlib -q`。
- Docker（无卷挂载必 --build）：planning/engine→cloud-planner；chitchat 件→chitchat-agent；
  info 件→info-agent（manifest hints 经 registry 周期重注册生效）；research 件→
  deep-research-agent；`_sdk/grounding|source_quality`→info-agent + deep-research-agent。
- 手工验收：「昨晚欧冠谁赢了」不编造（P1 后出过程区→卡）；「地球为什么是圆的」直答；
  「查一下明天杭州天气」weather 不被劫持；「来点科技新闻」news_brief 无 DEADLINE；
  「深入调研固态电池」→「展开第2点」种子注入（extract ≤3 次）。

## 7. 进度与验收结果（2026-07-12 全部落地）

| 分片 | 内容 | 状态 |
|---|---|---|
| 1 | fix(news) thinking=False | ✅ f210eb2 |
| 2 | eval(mode-routing) 语料122+脚本+双基线+本文档 | ✅ 7dfd16c |
| 3 | feat(routing-declarative) manifest 判别化+prompt 判据段 | ✅ b91ae92 |
| 4 | feat(routing-hints) 两条 hints+语料反例+基线重写 | ✅ f8c14a3 |
| 5 | feat(chitchat-freshness) 日期锚点+depth | ✅ 9459614 |
| 6 | feat(engine-escalate) 机制+marker+契约测试 | ✅ 31cdf31 |
| 7 | feat(search-quality) 重试+新鲜度+top6 | ✅ 0603232 |
| 8 | feat(news-livecrawl) | ✅ 6fdaf2f |
| 9 | feat(research-depth) cap+backtrack+种子 | ✅ 797fa1f |

### 验收数字

- **受控对照（@minimax:MiniMax-M3，同 provider 前后比）**：mode_routing live
  101/120（84.2%）→ **115/120（95.8%）**，16 例改善 / 0 系统性回归（仅 1 例边界句
  run 间摇摆，已双容忍标注）；typical 40/40、followup 10/10、adversarial 23/24。
  改善集中在：常识过度联网恢复直答（一光年/珠峰/秦始皇）、search 被越级 research
  压回（磷酸铁锂区别/FSD进展）、时效误吸直答消除（下周新电影）。
- **确定性层**：eval_route_hints 28→47 例全过；mode_routing 确定性子集 36/57→**57/57**
 （切片 2 预钉的 21 个目标态缺口全部转绿）。
- **回归 gate**：eval_rejection @MiniMax 新 prompt 误拒 3.4%→**0%**、拦截率与旧 prompt
  完全同分（12/18；与 @mimo 基线的差异经 stash 隔离实验证实为 provider 属性）；
  eval_clarify 新旧 prompt 完全一致（direct 硬门槛 17/17）；eval_registry_resolve 15/15
 （deep-research desc 加长曾致 1 例被吸走，已回退原句——registry 字符集打分下
  desc 长度即权重，教训记入 manifest 注释）；全量 pytest **1335 passed / 7 skipped**。
- **正式基线（默认大脑 @mimo:mimo-v2.5-pro，全部落地后采集）**：**175/177（98.9%）**——
  typical 40/40、adversarial 24/24、followup 10/10、guardrail 15/16、boundary 29/30、
  确定性子集 57/57。弱模型在判据+确定性护栏加持下反超 MiniMax 裸跑（95.8%），印证
  「护栏对弱 LLM 增益最大」。仅剩 2 例：真边界句「增程vs纯电哪个适合北方」落 chitchat
 （有日期锚+escalate 护栏兜底）；「查电量」落 charging.status（合理落点，已双容忍标注）。
  provider 记录在 meta；跨 provider 对照数字见上，不混用基线。

### 实施中发现的坑（后来者注意）

1. **registry 离线路由 desc 长度即权重**：`_score` 按「query 去重字符 ∩ capability 文本」
   计分，任何 desc/examples 加长都会全局抬该能力对所有 query 的分——改 manifest 文本
   必跑 eval_registry_resolve。
2. **跨 provider 比 eval 基线会假回归**：rejection/clarify 基线 @mimo，切到 MiniMax 直比
   拦截率掉 22 个点全是 provider 属性——归因必须 stash 隔离同 provider 对照。
3. **live 评测与 docker build 并发会污染基线**：构建吃满 CPU/IO → llm-gateway 调用超时 →
   规划两次失败 → fallback chitchat 假失败（guardrail 桶 15/16 假塌到 8/16）。
4. **llm-gateway 重建后 active provider 回落 env 默认**（运行时切换是进程内存态）。
5. **route_hints 正则前瞻会被替代分支绕过**：`搜索(?!引擎)` 挡不住「搜索引擎」——回溯落到
   裸「搜」分支照样命中，须给裸分支也加 `搜(?!索)`。
6. **整句 guard vs 分支内前瞻**：news hint 的「打开」放整句 guard 会误拦「打开空调然后
   来点新闻」的新闻子句；app 动作词只危及 `^…$` 锚定的话题式分支，收进该分支句内前瞻。

## 8. 收尾续修（同日，泓舟真机反馈两项）

### 8.1 MiniMax 开思考泄漏 `<think>` 内联（正确性 bug）

四家 × complete/stream × 开/关思考 12 态探针：**仅 MiniMax-M3 开思考泄漏**——思考段内联在
content 头部（`<think>…</think>\n\n正文`）而非独立 reasoning_content 字段（后者流式分支
早已丢弃）；mimo/deepseek/qwen 干净、MiniMax 关思考（`thinking:{type:disabled}`）有效。
修复在 provider 出口统一剥（任何调用方都不该收到内部推理）：`strip_think_block` 纯函数
（complete）+ `ThinkStreamStripper` 流式头部状态机（判定窗 ≤len("<think>")，普通流零延迟；
未闭合=截断在思考里→诚实置空走空响应兜底）。只处理**头部**，正文中段字面 `<think>` 不动。

### 8.2 markdown 判断与落地：speech 不上渲染、后端出口硬剥

判断（泓舟问「是否要增加 markdown 渲染」）：**不上渲染**。三点理由：①speech 的第一消费者
是 TTS——渲染解决不了念星号/表格符；②Aurora Glass 契约=气泡短结论、结构化内容归卡片，
给气泡上 md 渲染是在鼓励 LLM 输出长结构文本，方向反了；③各家 provider 的 md 输出不稳定，
半吊子语法渲染反出乱码。若将来要「泊车阅读态」富文本报告，那是 research_report 卡渲染器的
独立需求（P3 另立）。

落地四层收口（prompt「不要 markdown」软约束保留，出口硬剥）：
- `aggregator.compose`：unary 全路径 final speech 统一剥（单步直出 + 多步 LLM 聚合两分支）；
- engine 流式：D0 直通增量过 `MdDeltaSoftener`（`**`/`` ` `` 跨 chunk 剥、尾悬单 `*` 暂存
  一拍防误伤乘号）、executor 逐步话术整段剥；
- TTS 漏斗：`_sentence_segments` 句子组装后剥（跨增量 ** 对已合并，剥不漏）+ `/api/tts`
  批处理入口同口径——覆盖卡片全文朗读等 HMI 直发路径；
- research 报告卡：`_parse_report` 对 summary/body 出口剥（`[N]` 引用标记保留）。

### 8.3 连带修复：parse_synth 截断 JSON 抢救

真栈 @MiniMax 实测：其 answer 比 MiMo 啰嗦，600 max_tokens 内 JSON 被截断 → 旧逻辑把整段
`{"answer": "…` 原始 JSON 当话术上屏。`parse_synth` 增截断抢救：JSON 外壳开头且解析失败时
正则抽 `"answer"` 字段已生成部分（`(?:[^"\\]|\\.)*` 保证不断在孤反斜杠）、反转义、降 low
置信；连 answer 都没有则返回 None 走诚实兜底——**绝不把 JSON 外壳念给用户**。

### 8.4 新踩坑（追加到 §7 坑单）

7. **编排侧不能 import agents/_sdk**：cloud-planner 镜像不含 agents/，`from agents._sdk…`
   直接 ModuleNotFoundError 崩成重启循环（真栈实测）。speech 剥离函数三处**自持实现**并
   配对注释（`_sdk/grounding` Agent 侧 ↔ `orchestrator/cloud/aggregator` 编排侧 ↔
   `llm-gateway/providers` TTS 侧），口径变化三处同步。
8. Exa 上游偶发 timeout 与本轮改动无关（真栈复测自愈）；日志同时抓到 route_hint 真实打回
   一次 MiniMax 误路由（「帮我查一下…区别」被规划成 chitchat → `s_hint_info_search` 兜回）
   ——P0-4 护栏在生产语境的第一个活证据。

### 8.5 badcase 0f4105c4：调研报告退化堆原文 + fallback 路径 md 残留

泓舟真机 trace `0f4105c4c2f45106`（「深入了解历史背景」@MiniMax）：speech 是上千字原始
维基正文堆砌、报告卡有 md 残留。obs.llm 定位根因：**synthesize 的 completion_tokens=2400
恰好打满 max_tokens → JSON 截断 → `_parse_report` 解析失败 → 整份退化 `_fallback_report`
（原文节选直灌 speech/body，且该路径从未剥过 md）**。而且是结构性的：旧要求 5-7节×
250-450字≈最多 3150 字，本身超 2400 token 预算，MiniMax 啰嗦必顶满。三针修复：

1. **`_parse_report` 截断抢救**（parse_synth 同族）：正则抢救 summary + 已完整生成的
   section 对象（对象内无嵌套花括号可逐块 json.loads），半截对象丢弃；overall 降 low、
   gaps 诚实标注「报告生成被长度截断」——截断报告的前几节是完好的，丢掉去堆原文是最差选择。
2. **预算与要求对齐**：sync 要求改 5-6节×180-300字（≈1800字 落 2400 tok 内有余量，
   节数同时对齐 MAX_SUBQ=6）；deep 改 8-9节×300-500字 + max_tokens 4000→6000
  （≈4500字 有余量；异步不受 90s 约束、150s 超时可容）。
3. **`_fallback_report` 可读性收敛**：节选剥 md + 截 200 字 + 截断处省略号 + gaps 加
   「自动合成暂不可用，以上为资料节选」诚实标注；summary 由 capped 节选构成（≤300字）
   ——彻底告别千字原文糊脸。抢救/回退/解析三条路径的 heading/body 现在全部过 md 剥离。

真栈复现原场景 @MiniMax 两轮（调研→深挖历史背景）全过：speech 174/222 字干净结论、
报告 3/6 节正文无 md 无堆砌。全量 1354 passed / 7 skipped。

### 8.6 badcase 6ce027fe：JSON 裸英文引号致 speech 拦腰截断

泓舟真机 trace `6ce027fecaad969f`（「阿根廷和英格兰的下一场比赛」@MiniMax）：speech 断在
「1986年马拉多纳的」。obs.llm 定位：completion 185/600 **并非** token 截断——MiniMax 在
answer 字符串里写了**裸英文双引号**（…马拉多纳的"上帝之手"…）→ 整份 JSON 非法 →
§8.3 的截断抢救按「下一个引号」取值，在裸引号处提前停——**抢救逻辑把转义病误当截断病**。

修复（三处解析共用）：`_sdk/grounding.extract_json_str_field(text, field, next_fields)`
边界式提取——字符串结尾按「引号 + 下一个已知字段名 / 收尾括号」判定，裸引号原样保留为
文本；返回 `(值, 是否闭合)`：闭合=转义病（confidence 等后续字段仍可信、照抽），未闭合=
真截断（降 low + 收口标点）。`parse_synth` 抢救路径与 `_parse_report` 的
summary/section 抢救（`_rescue_section`：块 loads 失败转边界式抽 heading/body，半截 body
丢弃）全部切换；两处合成 prompt 加软约束「JSON 字符串值内不要用英文双引号，引用用「」」。
真栈原句复验 @MiniMax：126 字完整结论、句末闭合。全量 1357 passed / 7 skipped。
