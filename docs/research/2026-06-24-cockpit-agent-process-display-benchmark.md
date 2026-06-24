# 座舱 AI Agent「复杂任务过程展示」对标调研

> 调研日期：2026-06-24
> 目的：为本项目「复杂任务动态思考（thinking）+ 可折叠过程区」功能提供行业对标与设计依据。
> 性质：外部产品/行业对标笔记，非本项目自己的设计落地（后者见 docs/design/）。
> 信息边界：座舱厂商（尤其中国新势力）的过程展示 UI 公开资料稀缺，本报告对一手来源与二手综述、推断做了显式区分。

## TL;DR（结论先行）

1. **通用 Agent 的「过程区」已是成熟标配、范式收敛**：默认折叠 → 点开看「步骤/摘要」→ 过程与答案分离，且几乎无人暴露 raw reasoning。代表：OpenAI o1、Perplexity、Claude、Gemini。
2. **座舱产品几乎没有一个把「文字思考过程」当作展示重点**。海外（奔驰、特斯拉、大众）用拟人 avatar / 语音承载状态；中国新势力（蔚来、理想）把资源压在「多 Agent + 主动服务 + 情感」，而不是「给用户看推理」。
3. **「慢任务的等待反馈」是行业公认未解决的痛点**（AutoTech 综述明确点名）。→ 本项目要做的座舱文字过程区处在空白地带：差异化机会，但没有成熟竞品范本可抄。
4. **最该借鉴的是「通用 Agent 的披露范式 × 座舱的安全约束」的组合**：o1 的「摘要不露 raw CoT」+ Perplexity 的「折叠步骤」+ 奔驰/蔚来的「拟人占位」，落进「行车极简 / 泊车展开」双态。

## 二、通用 Agent：过程区范式已收敛

| 产品 | 过程展示形态 | 关键设计 |
|---|---|---|
| OpenAI o1/o3 | 「Thought for Ns」折叠条，点开是思考摘要 | 不暴露 raw CoT，只给摘要 |
| Perplexity Pro Search | Steps：Searching→Reading sources→Evaluating | 只展示工具动作，渐进披露、可折叠，过程/答案分离 |
| Claude / Gemini | extended thinking / "Show thinking" 可展开块 | 默认折叠、可选展开 |
| Manus / Devin / Operator | 独立面板实时展示执行轨迹 | 过程是产品主体，放独立区域不混进对话 |

**OpenAI o1 官方取舍（一手，最值得借鉴）** — [Learning to Reason with LLMs](https://openai.com/index/learning-to-reason-with-llms/)：
> "...we have decided **not to show the raw chains of thought to users**. ... We strive to partially make up for it by **teaching the model to reproduce any useful ideas from the chain of thought in the answer**."

落到本项目三条原则：① raw reasoning 不下发；② 「有用想法复述进答案」，让答案自带归因；③ 过程区 = 摘要/动作，不是原始推理。

## 三、海外座舱：拟人化 + 车控隔离，不做文字推理

- **奔驰 MBUX Virtual Assistant**（CES 2024 首发，CES 2025 接 ChatGPT-4o）：星形 avatar + 4 种情绪/视觉态动画（含 "thoughtful"），不滚动推理文字。
- **特斯拉 Grok**（2025-07 上车）：**Grok 与车控完全隔离，只做对话/问答，不能控车**；车控仍走确定性命令。→ 印证本项目红线「LLM 不直连车控」。
- **大众 IDA（Cerence+ChatGPT）、宝马（Alexa LLM）**：语音对话 + 极简卡片，过程靠 TTS 占位句。

## 四、中国新势力：押注多 Agent 主动智能（与本项目最相关）

- **蔚来 NOMI Agents**（NIO IN 2024）：多 Agent 框架（多模态感知 + 认知中枢 + 情感引擎 + 多专家 Agent），六能力模块 Understanding / Reasoning / Tool Use / Multi-Agent / Reflection / Alignment，落地 6 Agent（Parking/Guardian/Service Manager/Explore/DJ/Travel Memories）。叙事是「情感+主动+多 Agent」，**不把展示推理作为卖点**；其架构与本项目「分层编排+多 Agent 注册」高度同构。来源：[carnewschina](https://carnewschina.com/2024/04/12/nomi-gpt-nios-ai-assistant-just-got-a-whole-lot-more-real/)、[TechNode](https://technode.com/2024/07/29/nio-reveals-progress-on-autonomous-driving-chips-in-car-os-and-ai-assistant/)、[Just Auto](https://www.just-auto.com/news/nio-adds-automotive-grade-gpt-to-nomi-in-car-assistant/)、[Gasgoo](https://autonews.gasgoo.com/m/70032446.html)。
- **理想同学**：2025 独立 App + MindGPT + VLA 司机大模型；**过程展示无公开 UI 细节**（若有显著过程区媒体必报道）。
- **极越 Simo**：重「执行」（全车控、自动驾驶到点、区分主副驾），不重展示过程。
- **行业定调**：[AutoTech 综述](https://autotech.news/automotive-ai-agent-product-development-and-commercialization/) 把「延迟与等待反馈」列为**未解决挑战**，当前 agent「不暴露 reasoning，只交付输出」。另见 [Medium 行业文](https://danieldavenport.medium.com/ai-in-car-agents-chinas-bid-to-redefine-global-automotive-experiences-90979d4cd1a4)。

## 五、为什么座舱集体不做文字过程区

1. **视线安全硬约束**：NHTSA 视线准则——单次离路 ≤2s、单任务累计 ≤12s（欧盟 ESoP、日本 JAMA 同类）。持续刷新的思考文字反复吸引视线，合规风险。
2. **拟人 > 文字**：瞥表情/听一句话的认知负荷远低于读步骤。
3. **车控隔离让「过程」无关紧要**：需要展示过程的只有云端复杂任务（行程/调研），这类在座舱里本就少。

## 六、值得借鉴的做法（按优先级）

| # | 借鉴点 | 来源 | 对本项目动作 |
|---|---|---|---|
| 1 | 行车/泊车双态 | NHTSA+座舱通例 | 用 meta 车速/档位 gate 展开（复用 VAL「行驶中禁 X」） |
| 2 | 语音优先承载思考态 | 奔驰/蔚来 | 先发一句 TTS 占位，再上屏过程区 |
| 3 | 摘要不露 raw CoT | o1 | Planner 产出 summary，而非解析 reasoning_content |
| 4 | 有用想法复述进答案 | o1 | 答案自带「综合天气与路况…」式归因 |
| 5 | 折叠步骤+过程/答案分离 | Perplexity | 折叠条+展开 step 时间线，答案独立 |
| 6 | 拟人/情绪态填等待 | 奔驰/蔚来 | 进行中用 avatar 动效/呼吸灯 |
| 7 | step→脱敏中文 label | Perplexity | intent→label，绝不下发 prompt/params |

## 七、与本项目的印证 + 落地建议

**印证**：特斯拉 Grok「LLM 不碰车控」= 本项目红线；端云混合 = 本项目快慢分层；NOMI 多 Agent 六模块 ≈ 本项目注册中心架构。

**修正建议**：
1. 别做成纯文字过程区 → 抄「o1 摘要 × Perplexity 折叠步骤 × 奔驰/蔚来拟人占位」的混合。
2. **双态是安全底线**：行车态只留一行+语音，泊车/副驾才展开时间线+摘要。
3. 空白地带要自证：座舱无范本，需体验评估验证「过程区是否真降低体感延迟、是否分散注意力」。

## 八、参考来源

**一手**：[OpenAI — Learning to Reason with LLMs](https://openai.com/index/learning-to-reason-with-llms/)
**二手（确切 URL）**：[carnewschina](https://carnewschina.com/2024/04/12/nomi-gpt-nios-ai-assistant-just-got-a-whole-lot-more-real/) · [TechNode](https://technode.com/2024/07/29/nio-reveals-progress-on-autonomous-driving-chips-in-car-os-and-ai-assistant/) · [Just Auto](https://www.just-auto.com/news/nio-adds-automotive-grade-gpt-to-nomi-in-car-assistant/) · [Gasgoo](https://autonews.gasgoo.com/m/70032446.html) · [Automotive World](https://www.automotiveworld.com/news-releases/nio-in-2024-successfully-held/) · [AutoTech](https://autotech.news/automotive-ai-agent-product-development-and-commercialization/) · [Medium](https://danieldavenport.medium.com/ai-in-car-agents-chinas-bid-to-redefine-global-automotive-experiences-90979d4cd1a4) · [HN](https://news.ycombinator.com/item?id=41527520)
**URL 截断未给深链**：奔驰 mercedes-benz.com（CES 2024/25）· 特斯拉 reuters.com / electrek.co（2025-07）· Perplexity perplexity.ai/hub · 理想 cnevpost.com
**行业标准**：NHTSA Visual-Manual Guidelines (2013) 单次≤2s/总≤12s · 欧盟 ESoP · 日本 JAMA

> 维护说明：时点对标（2026-06-24），结论以发布日为准；更新追加「更新」小节，不覆盖原结论。