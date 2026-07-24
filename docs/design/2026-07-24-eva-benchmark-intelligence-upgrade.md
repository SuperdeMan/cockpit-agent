# 智能化升级提案：对标吉利超级 Eva 的架构评审与 Skill 化演进

> 日期：2026-07-24（v1.2 同日修订）
> 状态：**提案 v1.2（两轮外部评审已处置，二轮结论「架构方向通过」；待泓舟确认 §8 与开工顺序）**
> 输入：吉利超级 Eva 公开信息调研（来源 §1.5）+ 仓库编排核心/能力底座全量盘点 + 外部评审（ChatGPT，对 `main@2fd9aa6` 做了代码核对；处置记录 §9）
> 关联：`docs/architecture/cockpit-agent-architecture.md`（v1.2 基线）、`AGENTS.md`
> 说明：按泓舟要求「以产品智能化为先，可不受既有工程红线约束」。本提案区分了**安全红线**
> （保留，§5 给理由）与**工程红线**（本轮允许重写，但用「新机制」而非「加例外」的方式重写）。
>
> **版本记录**：v1.0 首版；v1.1 采纳外部评审——修正 3 处事实（端侧模型 7B 非 70B、350TPS 限编码场景峰值、navigation ETA 已产）、新增升级前置 P0（运行期 mock 回退盖真章，§2.4）、tool-calling 拆 V1/V2（submit_plan 先行）、Skill 分型 guide/policy/workflow、T2 放宽改分档+前置条件、MCP 准入收紧、emotion 短 TTL、分期重排 M0a-M4。v1.2 采纳第二轮评审——新增存量缺口 §2.4-3（capability `require_confirm` 中央未强制落实，进 M0a）、`submit_plan` 删 require_confirm、PolicyPack 更名 PlannerPolicyPack（软约束）+ 权威链、M0b 拆 Shadow Retrieval→Canary Injection、M1 DoD 分协议/功能两层、Background T2 限 deep-research 试点+守卫、Verifier 声明式契约、自进化安全治理、Memory/MCP 生命周期强制项、`sim.adas.*` 命名、M1 起子 RFC 制。

---

## 0. TL;DR

1. **超级 Eva 的本质**是「模型矩阵（Step 3.5 Flash 等）+ 端到端语音 + 端云协同 + 车辆执行层 + 生态与数据飞轮」的整体系统，架构哲学与本仓库同构（分层端云、规划与执行分离、确定性动作层）。**我们输的不是架构，是三件事：模型接入形态（无结构化规划输出/tool-calling）、规划知识的扩展方式（中央 prompt 单体）、进化速度（badcase 全人肉修）。**
2. **核心方案是把「智能」也变成声明式**：R2.1 已把*确定性路由*外迁到 manifest（route_hints），这次把*规划知识*外迁成 **Skill 层**（分三型：PlanningGuide 领域组合知识 / PlannerPolicyPack 跨域规划软约束 / WorkflowTemplate 流程模板，均自带黄金用例，按需注入 planner）；同时给 LLM 出口升级**结构化规划输出**（V1 单一 `submit_plan` 工具，V2 才做真 agentic tool loop）。合起来：增加规划知识与组合策略=只投/改 skill 文件，不再改中央代码（新增**可执行能力**仍需 Capability/Agent 实现——skill 管「怎么用工具」，不产生工具）。**Skill 是扩展智能的机制，不替代 Agent 运行时——Agent 仍是部署/隔离/信任边界，DAG Executor/VAL/确认链全保留。**
3. **相对体量最可赢的一项是自进化闭环**：obs.db 全量落库 + 8590 行语料 + 33 条旅程 + 1717 pytest 的资产已经齐了，缺的只是「badcase 自动挖掘 → skill/hint 补丁提案 → eval 门禁 → 人审合入」流水线。Eva 靠 850 万辆车做数据飞轮，这条流水线是单人团队可实施的轻量数据闭环路径。
4. **升级前必须先还一笔账**（§2.4）：navigation/charging 在真实 provider 运行期失败后回退 mock、却仍用真实 provider 盖 `_prov` 章——违反架构 §9.5 铁律③，「安全工程诚实度」这个长板必须先补洞再谈对标。
5. 分期（§6，开工顺序已拍板 §8-7）：M0a 真实性与安全基线 → M0b PlanningGuide 三步制 → M1a `submit_plan` 结构化输出 → M1b 自进化 v1 + Cloud Shadow NLU → M2 Task Ledger/Outcome Verifier + T2 分档放宽 + 记忆图谱 → M3 主动治理 + 受控 MCP → M4 形态升级（S2S/声纹/视觉；sim.adas 为低优先 backlog）。每期独立 DoD 与回归护栏；**M1 起每期先出子 RFC 再编码**，本文件只锁方向与边界。

---

## 1. 对标拆解：超级 Eva 到底是什么

### 1.1 时间线（公开信息）

| 时间 | 事件 |
|---|---|
| 2024-01 | 吉利发布星睿 AI 大模型 |
| 2025 CES | 全域 AI 1.0 |
| 2025-08 | 「行业首个 AI 座舱」+ 超拟人情感智能体 **Eva 1.0**，首搭银河 M9（星睿大模型 + 阶跃星辰端到端语音模型 + 动态记忆大模型） |
| 2026 CES（2026-01） | **全域 AI 2.0**：WAM 世界行为模型、「1+2+N」全域多智能体协同框架、全域感知/记忆引擎 |
| 2026-02/03 | 阶跃星辰发布并开源 **Step 3.5 Flash**（196B MoE / 激活 11B，为 Agent 场景设计；官方口径**编码类任务单流峰值约 350 token/s**，非通用吞吐） |
| 2026-04-17 | **超级 Eva** 随极氪 8X 量产上市（吉利 × 阶跃星辰 × 千里科技三方联研，+ 千里浩瀚 G-ASD 4.0 舱驾融合） |
| 2026-07-17 | WAIC 升级发布：「物理 AI」叙事，三大核心能力=高情商情感交互 / 长链路复杂推理 / 舱驾融合整车执行 |

### 1.2 能力模型（E1-E7）

| # | 能力 | 公开描述要点 |
|---|---|---|
| **E1 情感化端到端语音** | 阶跃端到端**语音语义一体化**模型：音频直接进大模型、直接输出意图标签+执行参数，抛弃 ASR→LLM→TTS 三段式；低延迟、有情绪、超拟人 |
| **E2 长链路自主规划** | 「带我去接孩子放学，顺便找家麦当劳，5 点前到学校」→ 自主拆解为导航→搜索→时间评估→（到场）泊车的动作序列；理解模糊指令（「世界杯、开幕式、水果姐」→ 找到歌）；宣称「全程无需二次确认」 |
| **E3 舱驾融合整车执行** | 与千里浩瀚 G-ASD「共享同一套感知/记忆/决策引擎」而非 API 互调；语音激活辅助驾驶、跟车/变道/避障、自主泊车——「语音即操控，思考即驾驶」 |
| **E4 动态记忆/个人化** | 短期记忆存对话实体+情感标签；长期记忆构建**关系图谱+偏好概率值**；「越用越懂你」，从通用 AI → 个人 AI |
| **E5 主动服务** | 「服务找人」：感知车内人员状态、车况、外部环境 → 主动调温度/音乐/氛围灯/驾驶模式，主动关怀 |
| **E6 生态执行** | 点咖啡、订电影票、订餐厅、订机票；接入吉利售后/租车/生活服务生态；「AI Agent 应用广场」 |
| **E7 自进化** | WAM「理解→规划→预演→判断→修正」闭环 + 人类在环价值函数：按用户实际行为反馈持续调整决策权重；数据飞轮（850 万辆车、百亿公里、2500 万 clips） |

### 1.3 技术底座

- **WAM 世界行为模型**（分层）：上层 MLLM（千亿级参数）做宏观任务规划、拆子任务；下层「动作专家 + 世界模型」预演行动后果、选最优路径。舱驾共用。
- **模型矩阵**（阶跃）：Step 3.5 Flash（推理/规划）+ 端到端语音大模型 + 视觉理解大模型（车内外感知/车位识别）+ Step Edge 端侧模型。端云协同（阶跃公开数据：纯端 Step-GUI 40% → 端云协同 57%）。**注意：超级 Eva 的能力来自模型+端云协同+工具生态+车辆执行层+数据闭环的整体，不能归因为某一个模型。**
- **算力**：云端星睿智算中心 23.5 EFLOPS（阿里云合作）；车端 AI Box 200 TOPS NPU / 200GB/s + **70 亿参数（7B）端侧多模态模型**（瞭望原文「全球首发70亿参数端侧多模态大模型」）；座舱芯片天玑 C-X1（NVIDIA×联发科 Blackwell）；智驾 H9 双 Thor 1400 TOPS + 5 激光雷达。
- **五层座舱架构**（媒体拆解口径）：算力层（云+端）→ AI 架构层（AI OS/多模型）→ 高频基础体验层 → AI 智能体层（传统语音控制）→ **AGI 层（Eva）**。

### 1.4 含金量辨析（哪些是真门槛，哪些不必照抄）

**真门槛（结构性差距）**：
1. **端到端语音语义一体化**（E1）——三段式换掉后，延迟与情感表达是代际差；这是模型层能力。
2. **长链路自主规划**（E2）——需要结构化规划输出/tool-calling 级规划模型 + 足够的工具面；我们的单次 DAG + 有界循环在「链路长度」与「中途自适应」上有真实差距。
3. **自进化闭环**（E7）——「badcase → 策略更新」的自动化程度决定迭代速度。
4. **生态执行闭环**（E6）——点咖啡/订票是**交易闭环**（账号/支付/履约），技术门槛不高但用户感知极强，且需要承载机制。

**不必照抄 / 营销水分**：
1. 「全程无需二次确认」——对涉车控/涉钱危险动作免确认是功能安全负资产；我们按 capability 声明分级确认的机制是对的，不跟（§5）。
2. 「舱驾共享同一套引擎」——对无智驾域的我们不可达；可达的是「语音→ADAS 设定」的受控接口（§4.H）。
3. 7B 端侧模型 + 200 TOPS AI Box、1400 TOPS 智驾——硬件叙事；PoC 对标点是**端侧语义理解的存在性**（小模型即可），不是自建同规格硬件。

**对标姿势**：Eva 背后是 850 万辆车的数据组织。我们是单人+AI 的 PoC，对标意义是**能力形态对齐**（每个 E 维度有机制化承载），不是规模对齐；对外表述写「能力目标」，不宣称同等级（§8-4）。小体量能赢的维度：架构可插拔性、自进化的工程化速度、安全工程的诚实度——第三样正因 §2.4 的发现要先补洞。

### 1.5 调研来源

- [吉利超级Eva+千里浩瀚G-ASD 4.0量产上车，极氪8X首发（搜狐）](https://m.sohu.com/a/998849334_115931)
- [超拟人智能体Eva上车：吉利如何用AI重新定义新一代座舱（汽车头条）](https://m.qctt.cn/news/1812893)
- [吊打传统车机！吉利超级EVA亮相WAIC：物理AI时代来了（凤凰科技）](https://tech.ifeng.com/c/8ut9E9yC6OL)
- [WAIC：吉利携生态伙伴带来全新超级Eva（中华网）](https://hea.china.com/articles/20260717/202607171919898.html)
- [阶跃 Step 3.5 Flash 大规模上车，极氪8X 超级Eva 4/17 量产（新浪）](https://news.sina.cn/sx/2026-04-18/detail-inhuwsse1640985.d.html)
- [WAM世界行为模型+千亿参数+1400TOPS：拆解吉利全域AI 2.0 技术底座（雷峰网）](https://m.leiphone.com/category/transportation/m9C3xQ2WvN6BzA1Yk.html)
- [吉利全域AI 2.0发布，Eva智能体、千里浩瀚G-ASD全面进化（新华网）](http://www.news.cn/auto/20260114/231a9da94ef54d348c15bb41f21fedcc/c.html)
- [吉利发布行业首个AI座舱，超拟人情感智能体Eva上车（瞭望，7B 端侧模型原文出处）](http://lw.xinhuanet.com/20250825/ae840d4d0d3541a0bb59716eef6785d6/c.html)
- [解读吉利AI座舱：Eva与常见AI助手有何不同（凤凰汽车）](https://auto.ifeng.com/c/8m0AZoLchjE)
- [吉利发布千里浩瀚 G-ASD、WAM模型（中新网）](https://www.chinanews.com/cj/2026/01-07/10547803.shtml)
- [Step-3.5-Flash 官方仓库（350 tok/s 为编码场景单流峰值口径）](https://github.com/stepfun-ai/Step-3.5-Flash)

---

## 2. 现状评审

### 2.1 做对了的（升级的地基，不推倒）

1. **规划/执行分离 + VAL 唯一车控路径 + 按 capability 分级的 `require_confirm`**——与 Eva 的「上层 MLLM 规划 / 下层动作专家执行」同构。这不是我们落后的部分，是已经对齐的部分。
2. **声明式扩展机制已经打通一半**：manifest 的 `capabilities`（registry pgvector 语义路由）+ `route_hints`（7 个 Agent 约 26 条 pattern，确定性兜底）+ `heavy`（思考/过程区）+ `context_scopes`（最小化下发）+ `display_priority`（卡片择优）。「加 Agent 不改编排核心」在确定性层面已兑现（R2.1），**这套机制正是 Skill 层可以直接骑上去的底座**。
3. **可观测与评测资产超配**（自进化的全部原料）：`obs.turn/llm/log` SQLite 落库 + dashboard 三级下钻/badcase 收藏重放；语料 `feishu_intents_full.jsonl` 8590 行、模式路由 122 条、route_hints 87 例、rejection 48、clarify 24、edge 回归 28；L3 journeys 33 条（regression 15 必绿）+ L4 CDP 6 例；22 个 e2e 脚本；全量 1717 pytest。
4. **多 LLM 运行时**：4+1 provider 一套参数化抽象、档位降级、请求级 pin、active 持久化——接入新模型形态（结构化输出、realtime 语音）有唯一出口，不用动全栈。
5. **语音工程链路**接近行业水位：流式 ASR（qwen3 realtime/fun-asr）、流式 TTS（cosyvoice 首帧 ~530ms）、KWS 唤醒 + VAD + barge-in + FSM（143 node 测试）——差的是模型形态（§2.2 C5），不是工程。
6. **记忆分层**（L0-L4）+ 四分类抽取 + 黑名单治理 + 隐私三档 + GDPR 删除/导出——底盘健康，缺的是图谱化（C4）。
7. **跨域交接与旅程闭环**：REMINDABLE_ACTIVE「即插」契约已被 sports、navigation（ETA→到达提醒，`agents/navigation/src/agent.py:573-583`，旅程 A2-4 绿）消费——「导航到达前一刻钟提醒」这类链路是通的。

### 2.2 智能天花板 C1-C6（证据化）

**C1 规划知识是中央 prompt 单体。**
`orchestrator/cloud/planning.py:30-154` 的 `_PLANNER_BASE`：7 组 few-shot 嵌满 agent/意图字面量（hvac/media/nearby/charging/trip/info），跨域组合启发式全部硬编码——「导航+顺路吃饭合并单步」（134-136）、「多日出行必出 trip.plan」（131-133）、「隐式车控识别」（145-147）、「省略式追问延续」（141-144）、「时效/深度判据」（116-127）。R2.1 解决了*确定性路由*的声明化，但*规划知识*仍只有两个去处：中央 prompt（污染全局、每改必全量回归）或 Agent 内部 handler（到不了规划期）。这就是历史上「planner prompt 无日期锚」「预测 hint」等 badcase 反复要动中央的根因；`AGENTS.md` 自述「编排核心零领域字面量」只对确定性代码成立，不含这份 prompt。

**C2 端侧语义是 1727 行规则的枚举上限。**
`orchestrator/edge/fast_intent.py`：正则 + 字符共现（约 284 个返回出口、263 处 `in t:` 判定），150 条意图 pattern / 62 对象，端侧 Agent 数 = 0（LOCAL_INTENTS 硬编码，不经 manifest 体系）。共现规则的结构性副作用是「劫持 badcase 家族」（提醒句被体感共现劫持成开空调 c9bcf8c2、场景句被拆散等，均有专项修复史）；2026-07-03 用飞书全量语料离线实测识别率约 72%（会话记录；原语料已 gitignore，仓库存转录子集）。每个新增端侧对象都要改中央文件，是「必须改中央代码」清单里最重的一条。

**C3 Agentic 能力被三道闸压死。**
① LLM 出口只有文本补全：proto 预留了 `CompleteRequest.tools`(field 5)/`CompleteResponse.tool_calls`(field 6) 但从未使用，且**均为 `google.protobuf.Struct`——`Message` 无 `tool_call_id/name`、`CompleteChunk` 无工具调用增量**（`proto/cockpit/llm/v1/llm.proto:25-51`）。现状规划靠 `_extract_json`（`planning.py:484-487`）从首个 `{` 截到末个 `}` 硬解析、重试 1 次，历史上为此修过合成 JSON 截断/裸引号抢救。**推论修正（v1.1）：现有 Struct 字段足以承载 V1「单工具 submit_plan」结构化输出；V2 真 tool loop（多轮调用关联、流式 tool delta、幂等）需要 proto 演进**（§4.B）。② T2 有界循环默认 **2 次 replan / 5 秒预算 / observation 6 条**（`loop.py:42-45`，`.env.example:25-26`）。③ escalate 改派**每轮 1 跳**（`engine.py:497-548`）。对「带我去接孩子放学顺便找麦当劳 5 点前到」级别的长链路任务，表达力与预算都不够——这是 E2 差距的技术根源，不是模型智商问题。

**C4 记忆有分层无图谱。**
现状是「记忆条目库」（`memory_item` 单表 + pgvector 召回 top-3），Eva 口径是「关系图谱 + 偏好概率值 + 情感标签」。差距具体化：偏好没有权重/置信衰减（「说过一次爱吃辣」和「每周三次点川菜」同权）、实体没有关系边（家人/常去地点/设备偏好互相孤立）、抽取不带情绪维度。底盘（抽取流水线、谓词归一、等价类 supersede）已在，缺上层结构。

**C5 感知与表达单模态，主动性是雏形。**
不存在的东西（盘点确认）：车内外视觉、声纹/多用户识别（`occupant_id` 恒 'primary'）、多音区、情感 TTS（provider 请求体连 speed 都未注入）、全双工 S2S（现为半双工回合制 + barge-in）。主动性四路并存但各自为政且浅：routine（min_count=3 一句建议）、晨间早报（雏形）、scene 触发（D6 只发建议卡）、road-safety 播报 + reminder 调度。**（v1.1 校正）ETA 型到达提醒已打通**（navigation 写 REMINDABLE_ACTIVE，A2-4 绿）；仍缺的是**真实 geofence/arrival 事件驱动**的 P1b 位置触发（到达/下车/电量类 B 类事件）。「服务找人」需要的**统一情境判断与节流治理**不存在。

**C6 生态执行缺位。**
12 个云 Agent 全自研，交易闭环只有 parking-payment（设计即模拟）；无第三方能力接入面（无 MCP、无应用广场机制）。Eva 演示的「点咖啡/订票」在本架构里今天没有承载物——manifest 理论上支持 third_party Agent，但「为每个外部服务写一个 gRPC Agent」的接入成本挡住了生态。

### 2.3 一句话评审结论

架构选型（分层混合编排、契约化 Agent、声明式治理、评测资产）在方向上与 Eva 同构且质量高于典型 PoC；**天花板全部集中在「智能的三个供给侧」——模型接入形态（C3）、知识扩展方式（C1/C2）、进化速度（C4-C6 的共因）**。升级沿本仓库已验证的路线走：把每一种智能供给都机制化、声明式化，而不是把编排器改写成自由 Agent。

### 2.4 外部评审补充发现：升级前置 P0（已逐条核实）

**运行期 mock 回退 + 盖真章**，违反架构 §9.5 决议铁律③（「运行期真实源失败 → 诚实降级说拿不到，绝不改供 mock 假数据」）：

1. `agents/navigation/src/agent.py:106-146`：POI 搜索遇 `ProviderError` 回退 `self._fallback`（mock）继续出结果，且 `attach(..., self.poi)` 用**真实 provider** 盖 `_prov` 章——mock POI 会被标成 real 并可能被导航过去（铁律③点名的危害场景）。
2. `agents/charging_planner/src/agent.py:296-319`：`plan_route` 同款——回退 mock 成功时仍 `attach(..., self.charging)` 盖真章。

这两处的「失败回退 mock 保证链路不阻断」注释是 2026-06 早期决策，2026-07-17 数据真实性治理清扫时的已知盲区是 news/nearby，这两处漏网。**修复方向按铁律③：运行期失败 → 诚实降级话术，而不仅是改盖章**（落地勘误：话术按 R9 契约以 **OK 状态**承载——executor 不映射 error、聚合器对单步 FAILED 只读 `r.error`，FAILED 话术会被吞成裸「抱歉，处理失败」；nearby 试点原用 FAILED 属同坑，一并对齐）；`test/e2e_strict_stack.py` 探针扩充电条目。列入 M0a，先于一切智能升级。

**（v1.2 第二轮评审新增）3. capability 级 `require_confirm` 未被中央强制落实**：manifest `Capability.require_confirm` 字段存在（`agent.proto:41`），但 `_validated_steps` 装配 Step 时只读取了 `heavy`，未写入 `require_confirm`（`planning.py:407-424`，已核实）；云路径确认现依赖 Agent 自身返回 NEED_CONFIRM / `AgentAction.require_confirm`（`executor.py:161` 仅透传 action 级标记）与端侧 `commands.yaml`/VAL 层。**Agent 实现漏标时，manifest 声明不会自动兜底**——属防御纵深缺一层（现网未出事故，但违背「manifest 是治理依据」的架构承诺）。修复（M0a）：中央合成 `effective_require_confirm = capability.require_confirm ∨ agent_action.require_confirm ∨ VAL/runtime 安全策略`，并加四条契约测试：① LLM 输出不得降低安全等级；② Agent 漏标由 capability 声明强制确认；③ 下游（Agent/VAL）可把低风险动作**升级**为需确认、不可反向；④ 任何上游不可覆盖 VAL 判定。

---

## 3. 差距矩阵

| Eva 能力 | 我们的现状 | 差距定级 | 可达性判断 |
|---|---|---|---|
| E1 端到端语音 | 三段式（流式化工程很好） | 代际差 | **中期可达**：经 llm-gateway 统一 realtime adapter 接 S2S provider；情感 TTS 参数是短期可达 |
| E2 长链路规划 | T1 单次 DAG 稳、T2 2 步/5s | 结构差 | **短期**提升结构化规划可靠性（submit_plan + Skill 层）；Ledger/Verifier 与工具面就位后**中期**形成长链执行闭环（§4.A/B/I） |
| E3 舱驾融合 | 无智驾域 | 不对称 | **演示级可达**：VAL 扩 adas.* 模拟域受控接口（§4.H），诚实标注 |
| E4 动态记忆 | 条目库+召回，无图谱 | 半代差 | **中期可达**：偏好图谱+衰减+情感标签（§4.D），底盘已在 |
| E5 主动服务 | 四路雏形，无统一治理；ETA 提醒已通、缺 geofence | 半代差 | **中期可达**：统一主动引擎（§4.E），机制整合为主 |
| E6 生态执行 | 无承载机制 | 结构差 | **机制短期可达，生态长期**：受控 MCP 接入层（§4.F）；BD 是非技术瓶颈 |
| E7 自进化 | 资产齐全、闭环人肉 | 组织差 | **短期可达且是相对优势**：badcase→补丁→eval 流水线（§4.G） |

---

## 4. 升级方案

总原则：**每一项都以「新的声明式机制」落地，落地后修订架构文档（内容合入 bump 次版本）**。边界（防误读）：**Skill 是扩展智能的机制，不是新的运行时外壳**——Agent 仍是部署/隔离/责任/信任边界，分层编排、DAG Executor、VAL、安全确认全保留。方案间依赖：A/B 是地基，C/G 骑在 A/B 上，D/E/F 独立可并行，H 是形态层，I 是 B 的放宽前置。

### 4.A Skill 层（对症 C1；v1.1 按评审意见分型）

**定义**：Skill = 规划期领域知识的声明式包，是 route_hints（确定性路由）在「LLM 规划知识」维度的对等物。参照 Anthropic Agent Skills 的渐进披露思想（metadata 常驻索引、body 按需加载）。**分三型**（v1.1，避免一个 SkillPack 混装知识/策略/流程导致边界混乱）：

| 型 | 职责 | 装配方式 | 例 |
|---|---|---|---|
| **PlanningGuide** | 告诉 Planner 何时/如何组合能力（领域组合知识 + few-shot） | description 语义预筛 top-N 注入 | 多日行程、导航顺路停靠、条件提醒 |
| **PlannerPolicyPack** | 对 Planner 的跨域规划指导（**软约束**，小而常驻） | 常驻注入（不预筛） | 时效性判据、禁编造/留空追问、状态查询不硬套 |
| **WorkflowTemplate**（v2） | 可版本化的确定性 DAG 模板，LLM 只填槽、engine 展开 | 命中后展开（scene compiler 哲学） | 接人→顺路用餐→导航→到达提醒 |

> 评审建议的第四类 `SkillSpec`（可执行能力契约）**不采纳为新对象**：它就是现有 manifest `capability`（intent/slots/description/examples + registry 语义索引），已存在且经 PgStore round-trip，再造一层是重复建设。

**权威链（v1.2 明确）**：prompt 层的 policy 永远是**软约束**；确认、权限、隐私、行驶状态的最终执行权在下列硬层，优先级自上而下、上游不可覆盖下游：

```
VAL / payment-gateway / Runtime Policy（context_scopes 过滤等）
  > Capability Manifest（require_confirm / permissions 声明）
  > Plan Validator（_validated_steps 校验）
  > PlannerPolicyPack（软）
  > PlanningGuide（软）
```

**目录**（v1：一个顶层，型作子目录——guide/policy/workflow 同属「规划知识」域、装配通道同源，分三个顶层只增加寻址成本）：
```
skills/
  guides/<kebab-name>.yaml       # type: guide
  policies/<kebab-name>.yaml     # type: policy
  workflows/<kebab-name>.yaml    # type: workflow（v2）
agents/<x>/skills/               # Agent 私有知识（后置，双轨制待 §8-1 拍板）
```

**guide 文件形态**：
```yaml
name: multi-day-trip
type: guide
description: 多日出行/N日游/带家人出游的规划知识   # 常驻语义索引（embedding 预筛用）
priority: 60
knowledge: |
  「去X玩N天/N日游/带老人/带娃」是行程规划意图，必须出 trip.plan 步…
few_shots:
  - user: 帮我规划周末去杭州两天…
    plan: {steps: [{intent: trip.plan, slots: {...}}]}
golden:                          # 自带黄金用例，接 eval CI
  - text: 下周去成都玩三天带爸妈
    expect_intents: [trip.plan]
owner: trip-planner
version: 1
```

**装配**：复用 `ContextManager` 既有模式（catalog top-K 预筛 + 字符预算）：guide 的 `description` 参与 embedding 预筛（与 registry capability 向量同源），每轮 top-N（建议 3）在 `_SKILL_BUDGET`（建议 2400 字符）内注入 planner user message 专属区；policy 常驻（总量小、严控）。**`_PLANNER_BASE` 瘦身为纯通用契约**（输出 schema、并行/串行纪律、受话判定），领域 few-shot 全部迁出。

**v1 迁移选件**（v1.1 对齐评审建议）：guides = `multi-day-trip`（131-136）、`navigation-with-stop`（134-136 顺路合并）、`conditional-reminder`（37-39/74-79 条件依赖→adaptive，对应旅程 A1-4 痛点族）；policies = `freshness-and-depth`（116-127）、`implicit-vehicle-control`（145-151，隐式车控识别与状态查询的**规划纪律**；其安全语义仍由 manifest/VAL 硬层承担，常驻不预筛）。

**与现有机制的关系**：`route_hints` = LLM 之后的确定性纠错；`skill` = LLM 之前的知识供给。一个 badcase 先问「是路由错还是知识缺」，再决定投 hint 还是投 skill。**治理**：golden 进 eval CI（`test/eval_skills.py`）；`obs.turn` 增记本轮注入的 skill 名单（badcase 归因）；热更新走文件加载 + mtime（v1 不动 registry schema）。

### 4.B 结构化规划输出 V1 → Agentic Tool Loop V2（对症 C3；v1.1 按评审意见分层）

**V1（M1）：单一 `submit_plan` 工具做结构化输出。** 不把每个 capability 暴露为可直接执行的 tool——那是工具循环语义（模型期待拿到工具结果继续推理），我们的 T1 是一次规划后确定性执行，语义不符。V1 只给模型一个工具：

```
submit_plan(plan: PlanSchema)   # steps/depends_on/slots/slot_refs/complexity
```

**schema 不含 `require_confirm`（v1.2）**——是否确认不是 LLM 的决定权（权威链见 §4.A），中央按 capability ∨ agent_action ∨ VAL 合成（§2.4-3，M0a 落实）。模型经原生 function calling 强制输出合法 Plan JSON → 仍走现有 Plan Validator（`_validated_steps`）、DAG Executor、VAL。**收益**：消灭 `_extract_json` 脆弱解析（截断/裸引号族工程债退役），零语义变化、不让模型绕开任何安全机制。**现有 proto Struct 字段即可承载**（单轮单工具）；改动面=providers.py `_build_body`/解析 + server.py 透传 + clients.py + planning.py 消费。灰度 `PLANNER_TOOLCALL=on|off` 双路径并存，A/B 对照。**DoD 分两层（v1.2）**：协议层=tool call 成功率 / JSON Schema 通过率 / 解析错误归零；功能层=`_validated_steps` 后有效计划率、planner fallback 率、mode_routing + journeys 对照不低于 JSON 路径、P50/P95 延迟与单轮 token 成本增量在预算内——**分 provider 统计，不混合平均**（意图/槽位/依赖准确率并入 mode_routing 对照口径，不另建标注集）。

**V2（按需，非本轮承诺）：真 agentic tool loop。** 只有确实需要「边执行边选工具」时才做，且需要 proto 演进与执行治理：typed `ToolDefinition`/`ToolCall`、`tool_call_id`、tool result message、流式 tool-call delta、幂等键与调用账本、checkpoint/resume、side-effect budget。**不用一对自由形态 Struct 长期支撑这一层。**

**T2 放宽（v1.1 改为分档 + 前置条件，见 §4.I）**：

| 档位 | 预算 | 场景 | 放宽时机 |
|---|---|---|---|
| Interactive | 2-3 次 / 5-8s | 普通车内交互 | M2（Ledger/Verifier 就位后） |
| Complex Interactive | 3-4 次 / 12-15s | 多意图、条件依赖 | M2 |
| Background | 6+ 次 / 30-300s | 深调研、异步行程规划 | **M0 仅限 deep-research 异步通道试点**（守卫见下）；通用 Background 档等 Task Ledger（M2） |

每档另限：LLM 调用次数与 token、外部工具调用数、**有副作用动作数（M0 简单闸：副作用步不进 T2 循环体，只能在终态计划中出现并走确认链）**、同一幂等键重复执行。escalate 每轮 1 跳防环保留。
**Background 试点守卫（v1.2，「只读」≠零风险）**：deadline 与超时明确终态、可 cancel、token/金额/外部 API 次数预算、observation 大小上限、**禁一切写操作与副作用工具**、检索到的外部内容按不可信数据处理（防 prompt injection）。
放宽 Interactive 档前必须先解决结果验证与长会话状态——canonical 旅程报告（@M3，跨 provider 不可直比）目标级 13/18、P95 25.5s，其中 B2-2 已修、A2-1/B5-2 属语料与采样方差、A1-4/B5-1 为已立卡残余；把这两张卡清掉 + §4.I 就位，是扩预算的前提。

### 4.C 端侧语义层（对症 C2；v1.1 命名纠偏）

分两步走，不赌硬件，**命名按部署位置诚实区分**（评审意见采纳）：
1. **Cloud Shadow NLU（影子评测，M1）**：小模型（@fast 档，server-side）对 `feishu_intents_full.jsonl` 8590 行离线跑意图+槽位 JSON，与 fast_intent 规则并行对照出混淆矩阵——先拿数据再决定切换范围（本仓库「eval 先行」的既定打法）。**它不叫 T0.5**——server-side 方案不具备端侧的离线/毫秒属性。
2. **Edge Semantic NLU（真 T0.5，按影子数据决策后）**：规则降级为**安全白名单层**（车控/媒体毫秒路径 + 离线兜底，保留 LOCAL_INTENTS 秒回），规则未命中的长尾进端侧 NPU 小模型（int4，Qwen3-0.6B/1.7B 级，对标 Step Edge 的角色）。置信度门控沿用 θ_high/θ_low（架构 §3.2 本来就画了「规则+端侧小模型」，这一步是把图上画了、一直没建的框建出来）。
- 附带收益：断网 SLM 兜底问答（架构 §6.3 EdgeLLM 框）同一模型承载；共现劫持类 badcase 家族从机制上退役。

### 4.D 记忆图谱化（对症 C4）

在 `memory_item` 之上加**偏好/关系层**（不推倒抽取流水线）：
- 新表 `preference(subject, predicate, object, weight, confidence, evidence_count, last_seen, half_life)`：巩固任务把重复出现的 semantic 条目聚合成带权偏好（「爱吃辣 0.9」vs「提过一次 0.3」），时间衰减；`relation(entity_a, rel, entity_b)` 存人物/地点/设备关系边。
- 抽取流水线增可选 `emotion` 标签，**默认短 TTL（会话级/24h），不入长期画像；形成长期情绪画像必须用户显式授权**（v1.1 采纳评审隐私意见，与隐私三档机制对齐）。
- 召回注入升级：画像块从「top-3 相似条目」升级为「结构化偏好摘要 + 相关条目」，走既有 recall 通道与预算。
- 多用户（声纹落地前）先以 `occupant_id` 维度把 schema 用起来（已支持 scope）。
- **生命周期强制项（v1.2，详设计入 M2 子 RFC）**：条目带 `user_id/occupant_id`（已有）与**证据引用**（派生偏好可溯源到原始轮次）；consent 字段（哪类画像经用户同意）；冲突处理沿用谓词等价类 supersede（已有）；**级联删除**——GDPR ForgetUser 硬删原始证据时，派生 preference/relation 必须随之删除或降权重算。

### 4.E 统一主动引擎（对症 C5 的主动性半区）

把四路主动（routine / scene 触发 / road-safety / reminder）收敛到一个**主动治理器**：生产方发 `agent.proactive.request`（带 kind/重要度/情境断言），治理器统一判「该不该说、现在说还是攒着说」——全局频控、免打扰时段、驾驶负荷门控（车速/导航状态）、同类合并去重——再发既有 `agent.proactive` 到 HMI/TTS。判断规则复用 scene 的三态求值器（SAT/UNSAT/UNKNOWN，unknown 不打扰）。补断链：**reminder P1b 位置触发（geofence/arrival 事件，B 类事件触发地基；ETA 型提醒已通，v1.1 校正）**。
DoD 场景：「电量 18% + 导航回家途中 + 顺路有桩」→ 一条合并建议，而不是三个 Agent 各响一次。

### 4.F 生态接入：受控 MCP 桥（对症 C6；v1.1 准入收紧）

不为每个外部服务写 gRPC Agent，改为**一个 `mcp-bridge` Agent** 承载 MCP servers，但**接入不是动态放行**（v1.1 采纳评审意见）：
- **人工准入**：每个 MCP server + 每个 tool 进入 manifest 级 allowlist 才可注册为 capability；**版本锁定**（server/schema 版本变更需重新准入）；密钥经 `.env`/payment-gateway 既有边界，Agent 不持凭证。
- 权限：一律 `trust_level: third_party` + `network.external`；写操作 `require_confirm: true`；涉支付走 payment-gateway 确认流。
- 运行治理：调用走既有熔断（dispatch）+ obs 审计（`obs.turn`/spans 记 server/tool/参数摘要）。
- **首批只接两个工具**：一个只读（如充电桩实时价格查询）+ 一个可确认写入（如示例咖啡下单 mock→真实），验证「Eva 点咖啡」等价链路后再扩。
- MCP 只做**生态桥**，不替代内部 gRPC——内部核心能力保持强类型低延迟协议（评审共识，坚持）。
- **写操作生命周期强制项（v1.2，详设计入 M3 子 RFC）**：幂等键、订单状态机、timeout/cancel、补偿/退款路径、收据与审计记录——缺一不接真实商户。

### 4.G 自进化闭环 v1（对症 E7，相对体量的最大杠杆）

流水线（nightly，`scripts/` + collector API）：
1. **挖掘**：从 obs.db 拉当日 FAILED/拒识/澄清/低置信/用户即时重述（同 session 相邻轮高相似）轮次，聚类归因（LLM 分类：路由错/知识缺/槽位错/数据源错/话术差）。
2. **提案**：按归因产出候选补丁——route_hint 草案 / skill（guide）草案含 golden / 语料新增 / 纯报告（需人工的架构问题）。
3. **门禁**：候选补丁在隔离分支自动跑 `eval_fast_intent` + `eval_mode_routing` + `eval_skills` + journeys regression，出对照报告（改善/回退/中性）。
4. **人审**：报告落 `docs/reviews/badcase/<date>.md`，只产 **draft PR** 不碰主干，泓舟拍板合入。**不做全自动合入**——「人类在环价值函数」我们的实现就是泓舟在环。

**安全治理（v1.2）**：obs 数据进 LLM 前脱敏（对齐 `OBS_CONTENT_CAPTURE` 既有脱敏口径：位置/姓名/车牌/订单号）；badcase 里的用户文本与网页内容一律按**不可信数据**处理，隔离于提案 prompt 的指令区（防注入）；**自动提案的修改面白名单 = guide / route_hint / eval 语料**，禁止生成或修改 VAL、权限、确认等级、payment、PolicyPack；评测用独立 holdout 集（同一 badcase 不得同时进补丁与验收）；涉副作用 capability 的 route_hint 补丁强制专项安全回归。
- 复用的全部是已有资产（obs.db、eval 脚本、journeys、badcase 收藏夹），新写的只有挖掘聚类与报告器。

### 4.H 形态升级：语音与「舱驾」（对症 E1/E3）

- **情感 TTS（M1/M2 顺手做）**：provider 请求体补 emotion/instruct/speed 参数（cosyvoice v3 支持指令化情感），话术层按对话情绪（§4.D emotion 短 TTL 标签）选情感参数。
- **端到端语音（M4）**：**先定统一 realtime adapter 协议（llm-gateway 内），再按真机延迟与工具调用能力选型**（v1.1 采纳「先锁协议不锁厂商」）；候选含 qwen3-omni realtime（官方支持流式音频+函数调用）、开源 Step-Audio 系自托管。**先限 chitchat/轻查询直通**——S2S 输出「意图标签+槽位」时仍回 planner/executor 安全链（Eva 同款分工：端到端负责听感，执行仍确定性）。HMI voiceLoop FSM/KWS/barge-in 资产原样复用。
- **声纹多用户（M4）**：说话人识别接入（DashScope/3D-Speaker 档），`occupant_id` 真实化，串起记忆多用户维度。
- **「舱驾融合」演示（诚实标注为演示级）**：VAL 扩 **`sim.adas.*`** 演示域（跟车距离/变道请求/泊车启动；v1.2 采纳评审命名——`sim.` 前缀 + HMI 恒显「模拟」标识，避免与未来真实智驾接口混淆），语音激活走 `require_confirm` + 行驶状态门控。**不做真实智驾**。

### 4.I Task Ledger + Outcome Verifier（v1.1 新增，Interactive 档放宽的前置）

- **Task Ledger**：跨轮持久任务账本（任务 id/目标/步骤状态/幂等键/预算消耗/checkpoint），承载「异步深调研、多轮长任务、中断续接」的持久语义——现有 `_suspend` 挂起态只覆盖确认/补槽窗口，escalate 是轮内改派，都不该被拉长为持久任务通道。落点：编排器侧新模块 + Redis/PG（与 SessionState 分层）。
- **Outcome Verifier**：步骤级结果验证（scene 的 Verify-Repair 已是同思想的先例：期望态对账 + on_fail 策略），推广为通用「执行后对账」钩子——车控步对 VAL 镜像、查询步对非空/新鲜度、写操作对回读。
- **Verifier 声明式化（v1.2，防长成下一个 fast_intent.py）**：领域期望由 capability 声明，中央只执行通用策略，不得按域硬编码：

```yaml
# manifest capability 新增（可选）
verification:
  mode: none | schema | readback | state_match
  timeout_ms: 2000
  on_fail: report | retry | replan
  max_attempts: 1
```
- 两者就位后按 §4.B 档位表逐档放宽 Interactive/Complex 预算。

---

## 5. 安全边界：保留什么、放宽什么

**保留（安全红线，不因对标放弃）**：
1. 车控只经 VAL、LLM 不直连车控、规划/执行分离——Eva 自己也是「MLLM 规划 / 动作专家执行」的分离结构。
2. **危险动作按 capability 声明分级确认**（现状机制即如此：`require_confirm` 是能力级声明，后备箱/支付/场景创建确认，hvac 等常规控制不打扰；VAL 另有行驶状态门控）——「全程无需二次确认」不跟；也不走向反面「全部确认」。v1.1 按评审意见把措辞从「涉车控二次确认」修正为「分级确认」，机制不变。
3. 敏感数据最小化（context_scopes）、密钥不进代码、抽取黑名单——视觉/声纹/emotion 接入时同样过这套治理（emotion 默认短 TTL，§4.D）。
4. **数据真实性铁律（§9.5）优先于智能升级**——§2.4 两处存量违例先修。

**放宽/重写（工程红线，按「建新机制」方式动）**：
1. 「不改编排核心加能力」在本轮**升格**：允许大改编排核心，但目的都是建 Skill 层/结构化输出/主动治理器/Task Ledger 这些新的声明式机制——改完之后「加聪明行为不改中央」覆盖面比现在更大。铁律精神不变，机制换代。
2. T2 循环预算从「硬保守」放宽为「分档配置 + 前置条件」（§4.B/4.I）。
3. 「第一版不做端侧大模型」（架构 §1.3 非目标）到期作废——Edge Semantic NLU 就是端侧小模型，属 Phase 2 目标提前（先过 Cloud Shadow 影子评测）。

---

## 6. 分期路线（v1.1 按评审建议重排）

> 每期独立可验收、独立可停。工作量按本仓库既往主题（R4.x/场景编排级别）估。

**M0a 真实性与安全基线（数天~1 周）**
- §2.4-1/2 两处 mock 回退按铁律③修（诚实 FAILED + strict_stack 探针断言）；**§2.4-3 capability `require_confirm` 中央强制落实**（effective 合成 + 四条契约测试）；本文档事实修正随 v1.1/v1.2 已完成；冻结 journeys/badcase 基线（canonical 报告 + provider 锁定）；定义 skill 三型 schema 契约。
- DoD：strict_stack 全绿含新断言；确认兜底四条契约测试绿；基线报告入库。
- **落地记录（2026-07-24，代码卡三张 + 契约全落地）**：①navigation 四处运行期 mock 回退（search/reverse_geocode/locate/poi_detail）→ 诚实降级 OK 话术、`_fallback` 字段删除（结构性根除）；②charging_planner 三处（find/find_near_destination/plan）同治，「服务坏了」与「真没有」话术区分；③nearby 试点 2 处 FAILED→OK 对齐 R9 契约（FAILED 话术被聚合器吞是三 Agent 共坑）；④`require_confirm` 中央落实=`_validated_steps` 从 capability 读入（LLM 字段不读）+ `executor._enforce_capability_confirm` 兜底闸（漏标改判 NEED_CONFIRM 扣动作）+ D0 流式直通排除 require_confirm 步（流中 action 会绕闸）；契约测试 `test_capability_confirm.py` 四条全绿；⑤strict_stack 探针 +充电条目（故障注入型断言在 unit 层锁定，e2e 不可行为诚实边界）；⑥`skills/README.md` 三型 schema 契约定稿。**基线冻结：升级前 canonical journeys 基线 = `docs/reviews/eval/journeys_report.md`（2026-07-15 @MiniMax-M3，回归 15/15 / 目标 13/18 / P95 25.5s）——M0b canary 对照与 T2 放宽护栏以此为准；真栈重跑（journeys/strict_stack）留栈起时执行。**

**M0b PlanningGuide：Shadow Retrieval → Canary Injection（约 1-1.5 周；v1.2 拆三步——注入即改行为，「影子」只到检索为止，且瘦身与注入不可同时全量、否则效果不可归因）**
- ① **Shadow Retrieval**：只检索 + obs 记录候选 skill，**不注入**——用真实请求测召回率/误召回率/Top-K 命中。
- ② **Canary Injection**：feature flag 下实验组=瘦身 Base + skill 注入，对照组=现有完整 Base，A/B 对照。
- ③ **Full Migration**：A/B 达标后删中央领域知识、全量启用。
- 载荷同前：guides×3（multi-day-trip / navigation-with-stop / conditional-reminder）+ policies×2（freshness-and-depth / implicit-vehicle-control）；`eval_skills.py` + obs.turn 记注入名单。**保持现有 JSON 规划路径，不动 T2**（Background 档 deep-research 试点可单独并行，守卫见 §4.B）。
- DoD：①出召回报告；②canary 组 mode_routing 122 / journeys regression 15 不低于对照组；③全量后 `_PLANNER_BASE` 减半 + 契约测试「加规划知识=只投 skill 文件」。
- **落地记录（2026-07-24，步① Shadow + canary 机制全落地；步② A/B 与步③ Full Migration 留待下批）**：
  - 机制：`orchestrator/cloud/skills.py`（SkillStore mtime 热更 / **纯词法检索**=keywords 命中+bigram 重合（零网络、离线确定，embedding 升级由 shadow 召回数据决定）/ 预算渲染）；`SKILLS_MODE=off|shadow|canary|full`（默认 shadow 零行为变化）；`_PLANNER_BASE_SLIM` 与注入块双路径并存；`Plan.skills` + `cloud.planning` span `skills` 属性（badcase 归因）；Dockerfile `COPY skills` + pyyaml + compose/.env.example 接线。
  - 载荷：guides×3（multi-day-trip / navigation-with-stop / conditional-reminder）+ policies×2（freshness-and-depth / implicit-vehicle-control），knowledge 从 `_PLANNER_BASE` 逐字迁移保行为。
  - 验证：`test_skills.py` 11 条（加载/检索命中/反例静默/渲染预算/**即插即用契约**（tmp 目录投新 guide 文件即被检索，零中央代码）/四态注入——canary 断言瘦身 base 不双份、date 锚在 skills 块前）；`test/eval_skills.py` 离线召回 **5/5**、反例误召回 1/6（纯导航句召回 navigation-with-stop，判定为可接受噪声——注入内容对导航句无害，shadow 持续观察）；**真栈 shadow 冒烟 PASS**（多日出行/条件句两探针，span 记录 `shadow:multi-day-trip`/`shadow:conditional-reminder`，行为零变化：行程×天气联动与 adaptive 条件链正常）。
  - 真栈复验（M0a+M0b 同栈）：e2e_ws 通过（含 cancel 打断）；strict_stack PASS（weather=qweather/place_list=amap/route_plan=amap 全 real；充电探针本轮未出 `_prov` 卡=无定位纯语音路径，探针下限 ≥2 满足）；**journeys regression 15/15 全绿**（@minimax provider 锁定；含 A5-3 后备箱危险确认链与 B4-2 场景确认链——M0a 确认兜底闸对既有确认流零破坏；A3-1 现场演示诚实降级话术）；全量 pytest **1740 passed / 7 skipped**（skip 回落 7 证实上批 +2 系栈未起波动）。
  - 环境注：本机 winnat 动态保留区（50063-50162）挡 50070/50071 宿主发布——`compose.winnat.local.yaml`（不入库）取消这两个端口的宿主发布（宿主侧无直连，容器网不受影响）；根治需管理员扩管理排除区（见文件头注释）。

**M1a `submit_plan` 结构化输出（约 1 周；开工首件事=出「Provider tool-calling 兼容」子 RFC——四家 OpenAI 兼容 + anthropic 的 tool_calls 格式矩阵）**
- `submit_plan` V1（providers/server/clients/planning 四件 + 灰度 A/B）。
- DoD：按 §4.B 两层口径——协议层归零 + 功能层对照不劣化、分 provider 统计。

**M1b 自进化 v1 + Cloud Shadow NLU（约 1 周）**
- 自进化 v1 流水线（含 §4.G 安全治理）；Cloud Shadow NLU 影子评测（8590 语料混淆矩阵→切换建议）；情感 TTS 参数并行落、**不作阻塞 DoD**。
- DoD：首份 nightly badcase 报告落库；影子评测报告产出切换建议。

**M2 执行治理与放宽（约 2-3 周；先出 Ledger/Verifier + 记忆图谱生命周期子 RFC）**
- Task Ledger + Outcome Verifier（§4.I，verification 声明式契约）；T2 Interactive/Complex 档逐档放宽（journeys 时延与红灯双指标护栏）；记忆图谱（preference/relation + 巩固聚合 + 注入升级 + §4.D 生命周期强制项）。
- DoD：中断-续接/幂等旅程用例入 L3；放宽后 P95 不劣化超阈；偏好演进旅程绿。

**M3 主动与生态（约 2-3 周；先出 MCP 安全与交易生命周期子 RFC）**
- 统一主动引擎（四路收敛 + P1b geofence）；受控 MCP 桥（一读一写首批，准入/审计/熔断全过 + §4.F 写操作生命周期强制项）。
- DoD：合并主动建议旅程绿；MCP 下单确认链 CDP 用例绿。

**M4 形态升级（4 周+，供应商依赖弹性）**
- 统一 realtime adapter → S2S 灰度（chitchat 先行）；声纹多用户；VAL sim.adas.* 演示域；视觉入口（车外「那是什么」以图片问答起步）。
- DoD：语音双链路可切换；多用户记忆隔离旅程。（`sim.adas.*` 演示域=低优先级 backlog，**非 M4 必做 DoD**——2026-07-24 拍板 §8-6。）

---

## 7. 不做清单与风险

**不做**：自研基座模型；全双工声学自研（等 S2S provider 成熟度）；自建 7B 端侧硬件军备（先影子评测定规格）；多 Agent 自由协商自组织（架构 §1.3 维持）；免确认执行；真实智驾；MCP 动态放行注册。

| 风险 | 缓解 |
|---|---|
| 弱模型对 submit_plan 结构化输出拒答/畸形 | 规划轮 pin 强模型（既有机制）；JSON 双路径灰度保底；DoD 只考核协议错误率 |
| skill 注入膨胀 token/干扰路由 | top-N=3 + 独立预算 + priority；policy 严控总量；obs 记注入名单可归因；golden 反例护栏 |
| Edge NLU 端侧算力假设不成立 | Cloud Shadow 影子评测先行，数据说话再定端侧件 |
| 生态接入的账号/合规（点咖啡真实闭环） | 机制先行（受控 MCP+确认流），真实商户 BD 明确标注为非技术依赖 |
| 自进化提案质量低造成审阅负担 | 门禁自动跑 eval 过滤中性/回退项；报告分级（高置信补丁 vs 仅报告） |
| 大改编排核心期间主干不稳 | 全程 env 门控双路径 + journeys regression 15 条每步必绿（既有红绿灯打法） |
| T2 放宽放大延迟/费用/重复副作用 | 分档预算 + 副作用步禁入循环体（M0 简单闸）→ Ledger/Verifier 就位后才放交互档（M2） |

---

## 8. 决策点拍板记录（2026-07-24 泓舟拍板，全部落定）

1-5 条**全部通过**（Skill 顶层三型目录 / 规划轮手动 pin 不建自动选择器 / 情感 TTS M1-M2 并行不阻塞 / 内部对标 Eva 外部只写能力目标 / S2S 先锁 adapter 协议不锁厂商）。另拍板：

6. **ADAS 演示域**：`sim.adas.*` 进入**低优先级 backlog**，**不作为 M4 必做 DoD**。
7. **开工顺序**：**M0a → M0b → M1a（submit_plan）→ M1b（自进化 / Shadow NLU）**。
8. **实施文档**：M0a/M0b **不出**单独实施文档，直接依据本设计文档实施（§2.4/§6 已到 file:line 级规格）；M1a 开工时先出「Provider tool-calling 兼容」子 RFC（v1.2 既定），M1b/M2/M3 按 v1.2 子 RFC 制。

---

## 9. 外部评审处置记录（2026-07-24，ChatGPT 评审对 `main@2fd9aa6`）

评审总评：有条件通过（战略方向 8.5/10，事实与证据 6/10）。逐条处置（均经仓库/原始来源核实后决定）：

| 评审意见 | 处置 | 依据 |
|---|---|---|
| P0 「70B 端侧模型」应为 7B | **采纳** | 瞭望原文「70亿参数端侧多模态大模型」，已核 |
| P0 tool-calling 与 DAG 规划混用，先做单一 submit_plan | **采纳**（§4.B V1/V2 分层） | 全 capability 暴露为 tool 隐含工具循环语义，与 T1 不符；submit_plan 拿到解析可靠性且零语义变化 |
| P0 「无需改 proto」只对极简方案成立 | **采纳**（口径改为 V1 够用/V2 需演进） | 已核 `llm.proto:25-51`：tools/tool_calls 均 Struct，Message 无 tool_call_id，Chunk 无 delta |
| P0 T2 直接 6/30s 风险高，先建 Ledger/Verifier | **采纳改形**（§4.B 分档 + §4.I；Background 档先行放宽——只读无副作用，deep-research 异步已有先例） | 交互档确需前置；一刀切前置会挡住零风险的后台查询型放宽 |
| P0 运行期 mock 回退盖真章 | **采纳且加深**（§2.4/M0a；按铁律③改诚实 FAILED，不止改盖章） | 已核 navigation:106-146 / charging:296-319 属实 |
| P1 SkillPack 拆四类对象 | **采纳三类，拒绝 SkillSpec**（§4.A） | guide/policy/workflow 分型有效；SkillSpec 与现有 manifest capability 重复 |
| P1 三个新顶层目录（skills/workflows/policies） | **改形**：单顶层 skills/ 分型子目录 | 三型同属规划知识域、装配同源；目录约定最小增量 |
| P1 server-side @fast 不是 T0.5 | **采纳**（Cloud Shadow NLU / Edge Semantic NLU 命名，§4.C） | 命名应诚实反映部署位置 |
| P1 MCP 动态注册过宽 | **采纳**（人工准入/版本锁定/一读一写首批，§4.F） | 与 trust_level/权限模型对齐 |
| P1 emotion 长期存储风险 | **采纳**（短 TTL 默认+授权后入画像，§4.D） | 与隐私三档一致 |
| P1 「涉车控全部确认不合理」 | **措辞修正，机制不变**（§5-2） | 现状本就是 capability 级分级确认；原文缩写措辞误导，评审系对措辞的误读 |
| 「不应重写成纯 Skill Agent」 | **无需改方案，补防误读边界句**（§4 总则） | 原提案从未提出替代 Agent 运行时；接受表述责任 |
| navigation 不产 ETA 说法错误 | **采纳**（§2.1-7/§2.2-C5 已校正：ETA 已通，缺 geofence P1b） | 已核 navigation:573-583 + AGENTS.md A2-4 转绿记录；v1.0 引用了首跑红灯清单的过期结论，我的核对失误 |
| journeys 13/18 五红作为放宽前提证据 | **方向采纳，事实校准** | 13/18 为 @M3 canonical（跨 provider 不可直比，@mimo 曾 16/18）；五红中 B2-2 已修、两条属语料/方差、两条已立卡 |
| Eva 表述降温 + 350TPS 限定口径 | **采纳**（§0/§1.1/§1.3） | leiphone/官方仓库均为编码场景单流峰值口径 |
| 实施顺序 M0a/M0b/M1/M2/M3/M4 | **采纳**（§6 重排；游标微调：Background 档放宽提前至 M0 并行） | 真实性前置、影子先行、ledger 后放宽的次序成立 |
| 决策点建议（目录混合/自动选模型/TTS 时机/口径/S2S 锁协议） | 4 条采纳；**自动选模型拒绝**（§8-2） | 与「单一大脑不自动 failover」既有决策冲突 |

### 第二轮评审处置（2026-07-24，对 v1.1；结论「架构方向通过」，并认可上一轮对自动选模型与 SkillSpec 的两项拒绝）

| 评审意见 | 处置 | 依据 |
|---|---|---|
| `submit_plan` schema 应删 `require_confirm`（确认权不在 LLM） | **采纳**（§4.B） | 原则正确；且连带核实存量缺口（下一行） |
| capability `require_confirm` 未被中央强制落实 | **采纳且核实为真**（§2.4-3，进 M0a） | 已核 `planning.py:407-424` 装配 Step 只读 heavy、`executor.py:161` 仅透传 action 级标记；云路径靠 Agent 自觉 + 端侧 commands.yaml/VAL 兜底 |
| PolicyPack 更名 PlannerPolicyPack（软约束）+ 权威链五层排序 | **采纳**（§4.A） | prompt 层策略不可能是硬安全策略；v1.1「跨域硬判据」措辞错误，示例中确认/隐私移出 policy |
| M0b 不是真影子，拆 Shadow Retrieval → Canary → Full | **采纳**（§6 M0b 三步制） | 注入即改行为；瘦身与注入同时全量则效果不可归因，实验设计成立 |
| M1 DoD 不能只有解析归零，补功能层指标 + 分 provider | **采纳改形**（§4.B 两层 DoD） | 裁剪：意图/槽位/依赖准确率并入 mode_routing 对照口径，不另建标注集 |
| Background T2 非零风险，限 deep-research 试点 + 六项守卫 | **采纳**（§4.B） | 成本失控/限流/上下文膨胀/丢任务/注入确为「只读」剩余风险 |
| Outcome Verifier 须声明式（capability.verification 契约） | **采纳**（§4.I） | 否则中央 verifier 会长成下一个 fast_intent.py，与 manifest 哲学一致 |
| 自进化闭环补安全治理（脱敏/不可信数据/修改面白名单/holdout/draft PR/副作用回归） | **采纳**（§4.G；脱敏对齐 OBS_CONTENT_CAPTURE 既有口径） | 全部低成本高价值；修改面白名单与「禁改硬层」呼应权威链 |
| Memory/MCP 补生命周期强制项（consent/证据溯源/级联删除；幂等/订单态/补偿/审计） | **采纳**（§4.D/§4.F 列强制项，M2/M3 子 RFC 展开） | memory 已有 occupant/supersede/ForgetUser，缺口是派生级联与 consent；MCP 写操作全部需建 |
| 文字四处（skill 文件句精确化 / E2 分短中期 / 飞轮等价物→轻量闭环 / sim.adas 命名） | **采纳**（§0/§3/§4.H） | 均为准确性修正 |
| M1 起各期出子 RFC，不依赖总提案直接编码 | **采纳**（§0-5/§6） | 与本仓库「一主题一设计稿」惯例一致 |
| 本轮无拒绝项 | — | 两处为改形（DoD 指标裁剪、治理对齐既有脱敏机制），其余全盘采纳 |
