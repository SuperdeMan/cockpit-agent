# M4 子 RFC：全双工 S2S 核心设计（realtime adapter 协议 × 三层状态机 × 安全链分工）

> 日期：2026-07-25
> 状态：**P0→P3 已落地并真栈验证**（落地记录与设计偏差见 §11）。P4（声纹多用户 / 视觉入口）未实施，边界速写见 §8。
> 依据：母提案 `2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.H / §6-M4 / §8-5「先锁 adapter 协议不锁厂商」/ §8-6「sim.adas 低优先 backlog 非 M4 DoD」
> 前序：M0a→M3 全部完成（Skill 层 / submit_plan / 自进化 / Ledger+Verifier / 统一主动引擎 / 位置提醒 / MCP 桥）
> 范围：**S2S 接入的四件咬合结构件**——①统一 realtime adapter 协议 ②全双工三层状态机（含 barge-in / 重连 / 工具调用打断 / 状态恢复）③S2S×确定性安全链分工契约 ④双链路共存与域灰度。声纹多用户与视觉入口给**边界速写**（§8，接手人自行展开设计）。

---

## 0. TL;DR 与关键设计判断

泓舟点名「生产级全双工 S2S 状态机」为最关键件。我的判断：**状态机本身不是最难的，最难的是它周围五个会返工整期的结构性决策**——本 RFC 把它们全部锁定：

1. **三层状态机的权威关系**（§2.2）：HMI voiceLoop（六态半双工）× S2S provider 会话（server VAD/response 生命周期）× 编排执行（planner/executor 在飞任务）。定「**我们侧是权威、provider 侧是执行器**」——所有用户可感知状态由本侧状态机决定，provider 事件只是输入。
2. **provider session = 可丢弃缓存，不是真相源**（§2.3）：S2S 长连断了，provider 侧对话上下文即灭。对话历史唯一真相源保持在本侧（memory AppendTurn 既有链），重连 = 新 session + 上下文重注入。这条定错了，重连恢复就无从谈起。
3. **工具调用绝不在 S2S 会话内闭环**（§5）：S2S 模型的 function call 输出「意图+槽位」后**必须回本侧确定性链**（planner 校验→executor→VAL/确认链），执行结果以文本事件回注 S2S 供其播报——Eva 同款分工「端到端负责听感，执行仍确定性」。车控/支付/任何副作用**没有一条路径**绕过 M0a 确认闸与 VAL。
4. **音频通道拓扑**（§2.1）：S2S 会话 WS 落 **llm-gateway 音频面**（50059，与流式 ASR/TTS 同门），**不经 edge-gateway 代理**——但文本副产品必须回灌对话主链（§7），否则 S2S 轮次成为记忆/观测黑洞（接手人最容易漏的整合点）。
5. **打断不是一件事，是三层语义**（§4.3）：听感打断（provider server VAD 自动 response 取消）／任务打断（在飞编排任务，复用 M1a `{type:cancel}` 通道与 M2 Ledger cancel）／工具调用中打断（call 已派发结果未回——**结果回来后静默丢弃不播报，副作用步照常走完确认链**）。每层权威不同、传播方向不同，混为一谈必出双播/幽灵执行。
6. **双链路是常态不是过渡**（§6）：S2S 只授权 chitchat/轻查询域；非授权域**轮级逃逸**回三段式链路。逃逸判定点在 S2S 首个语义事件（转写/工具调用），不做「S2S 说到一半拽回来」——宁可首响应慢半拍，不接受播到一半换嗓子。
7. **不自研声学，不接管 KWS/VAD 资产**：唤醒/退出/dismiss/误唤醒治理全部留在 HMI 既有层（R4.3 资产），S2S 只在 LISTENING 之后接管「听清+回答」——143 个 node 测试保护的东西一个不动。

## 1. 现状资产盘点（设计的复用面与接缝）

| 资产 | 位置/形态 | 在 S2S 设计中的角色 |
|---|---|---|
| HMI 语音 FSM：六态 `IDLE/ARMED/LISTENING/THINKING/SPEAKING/FOLLOWUP`，效果回调注入（onOpenAsr/onSend/onStopTts/onCancelTurn…），KWS 唤醒/VAD/barge-in/退出词/端点宽限全套 | `hmi/src/voiceLoop.mjs`（457 行，143 node 测试族保护） | **原样保留为权威状态机**；S2S 模式下六态语义重映射（§4.1），KWS/退出词/dismiss 零改动 |
| 双 WS 拓扑：对话文本 WS（edge-gateway 8090 `/ws`，含 `{type:cancel}`→gRPC ctx 取消传播，M1a/R4.3b）＋音频 WS（llm-gateway 50059 `/api/asr/stream`、TTS 流） | `gateway/edge/`、`llm-gateway/http_server.py` | S2S 会话 WS 开在音频面同门（§2.1）；cancel 通道复用（§4.3） |
| realtime 客户端先例：DashScope `/api-ws/v1/realtime`（OpenAI realtime 风格，session.update/事件流/base64 音频）与 `/api-ws/v1/inference`（run-task）两壳的 WS 客户端都已真栈验证 | `llm-gateway/providers.py`（qwen3 ASR realtime / cosyvoice TTS） | adapter 的 provider 侧实现直接续用这套 WS 工程（心跳/超时/事件解析惯例） |
| 服务端流式 TTS + barge-in v1（HMI 停播+cancel 收尾）、TTS 分层（引擎→音色） | R4.2 资产 | 三段式链路照旧；S2S 模式下 TTS 由 provider 音频流替代，**HMI 播放器复用**（PCM 播放/停止面一致） |
| 受话判定+澄清（R4.4：planner 一次 LLM 输出 addressed/clarify，hands-free 源门控） | planning/engine D6 | §5.3：S2S 授权域内由 S2S 模型自答自判；**逃逸到确定性链的轮次照走 R4.4 全套** |
| M1a submit_plan（named tool_choice、轮内降级）；M0a require_confirm 中央闸；R9 话术契约 | planning/executor | §5：S2S 工具调用落地为「向确定性链投递一句等价文本指令」，上述全部机制**天然全量生效** |
| M2 Task Ledger（长任务可查可停）+ Outcome Verifier | `_sdk/ledger.py` + executor 尾链 | 长任务在 S2S 会话中受理→照常开单；S2S 播报其 ack；完成推送经 M3 主动引擎（下行） |
| M3 统一主动引擎（六路主动收敛，频控/驾驶负荷/同窗合并，fail-open） | `proactive/` + `runtime/proactive.py` | §4.4：主动消息在 S2S SPEAKING 期到达 → **不抢话**，treated as 排队下行事件，S2S 轮结束后按治理器节律播 |
| qwen3.5-omni realtime（候选首厂商）：WS + server VAD + **tool calling**（返回 function name/args/call id）+ `response.audio.delta` 流式音频，OpenAI realtime 风格事件 | DashScope 公开文档（2026-07 口径） | adapter 协议的地气参照；Step-Audio 自托管为第二候选（协议映射见 §3.4） |

## 2. 总体拓扑与三条结构性决策

### 2.1 通道拓扑：S2S 会话 WS 落 llm-gateway 音频面

```
HMI mic ──PCM──> llm-gateway /api/s2s (WS, 新)  <──双向──>  S2S provider (WS realtime)
  │                    │ 事件下行：transcript/answer_text/audio.delta/tool_intent/turn 生命周期
  │                    │ 音频下行：PCM（HMI 既有播放器）
  └──文本对话 WS(8090)──> edge-gateway → 编排主链   ←──工具调用逃逸/文本回灌（§5/§7）
```

- **决策：不经 edge-gateway 代理**。理由：音频面（ASR/TTS 流）本来就在 llm-gateway HTTP 面直连（现状拓扑），S2S 是同类流量；经 edge-gateway 加一跳纯增延迟与断点，鉴权用音频面既有口径。**代价与补偿**：edge-gateway 看不见 S2S 轮次 → §7 文本副产品回灌为**强制项**（不是可选优化）。
- llm-gateway 内新模块 `s2s/`（会话管理+adapter），不与批处理/流式 ASR/TTS 抢生命周期。

### 2.2 三层状态机：我们侧权威，provider 事件只是输入

| 层 | 持有者 | 职责 | 权威范围 |
|---|---|---|---|
| L-HMI：voiceLoop 六态 | HMI | 唤醒/聆听/打断/退出/续问窗——**用户可感知状态的唯一权威** | 何时听、何时说、何时打断 |
| L-Session：S2S 会话态 | llm-gateway `s2s/` | provider 连接生命周期、轮次对账（response id ↔ 本侧 turn id）、重连、上下文重注入 | provider 何时收音频、response 取消、session 重建 |
| L-Exec：编排执行态 | engine/executor/Ledger | 逃逸轮的计划执行、确认挂起、长任务 | 一切副作用 |

**铁律：下层事件永远不直接驱动上层状态迁移，只作为上层状态机的输入。** 例：provider 的 server VAD 判了「用户说完了」→ 上报 `turn.detected_end` 事件 → L-HMI 决定是否进 THINKING（它还要过本侧 VAD/宽限窗的既有判据）；provider 断连 → L-Session 静默重建，L-HMI 只有在重建超时才感知（降级提示）。反例（禁止）：provider `response.created` 直接把 HMI 置为 SPEAKING——若此时本侧刚判定 barge-in，就会出现「打断了却又开始说」的竞态。

### 2.3 provider session = 可丢弃缓存

S2S 会话在 provider 侧持有对话上下文（其多轮能力来源），但**本侧不依赖它存活**：

- 对话历史唯一真相源 = 本侧 memory（AppendTurn 既有链，经 §7 回灌）。
- **重连 = 新 session + 重注入**：`session.update`（或等价 system 注入）带三样——①近 N 轮对话摘要（memory GetSession 既有接口，N=4 与 planner 口径一致）②人设/话术契约（简短系统提示，与 chitchat persona 同源）③当前焦点（planner:focus 既有键，可选）。**不逐字回放全部历史**（token 成本+provider 兼容性），摘要粒度足够 chitchat 域连续性。
- 推论：**任何时刻杀掉 provider session 都无损**——这是重连、厂商切换、A/B 的共同地基；也是「锁协议不锁厂商」在状态层的真正含义。

## 3. 统一 realtime adapter 协议（llm-gateway 内）

### 3.1 设计原则

- **两端两个契约**：对上（HMI/回灌）一个**本侧事件协议**（穿越厂商差异，永不随厂商变）；对下（provider）一个 **`BaseS2SProvider` 抽象**（每厂商一实现）。HMI 只认识本侧协议。
- 事件命名走本侧语义，不照抄 OpenAI realtime（避免「协议漂移即 HMI 重写」）；但字段语义与之可映射（降低厂商实现成本）。

### 3.2 对上事件协议（`/api/s2s` WS，JSON 文本帧 + 二进制 PCM 帧）

上行（HMI→网关）：

```jsonc
{"type": "session.start", "session_id", "user_id", "voice", "context": {...}}  // context=重注入材料，HMI 不组装、网关向 memory 取
{"type": "audio"}            // 之后跟二进制 PCM 帧（16k mono s16le，与 /api/asr/stream 同格式）
{"type": "audio_done"}       // 本轮音频段推完（本侧 VAD 判到端点）→ 网关请 provider 收尾定稿
{"type": "barge_in"}         // L-HMI 判定打断（本侧 VAD/KWS 权威）→ 网关 cancel 当前 response
{"type": "cancel_turn"}      // THINKING 期取消（对齐既有 onCancelTurn 语义）
{"type": "escalated_result", "turn_id", "text"}  // 逃逸轮主链回答播完后回传（见下）
{"type": "session.end"}
```

- **`audio_done` 为何存在**（真机首验后补入协议）：provider 的 server VAD 靠**连续静音输入**判「说完了」，而 L-HMI 在本侧 VAD 判到端点后就停止推流——provider 永远等不到静音，turn 永不收束，**用户说什么都没有回复**。这一步缺了就是死锁：provider 等静音 ↔ HMI 等定稿才进 THINKING 才关收音。故端点判定权留在本侧（与 classic 的 `onEndpoint → asr.stop()` 逐字同构），收尾动作经 `audio_done` 交给网关，由 `BaseS2SProvider.commit_audio()` 按厂商方式实现（qwen 补静音尾，长度须 > `silence_duration_ms`——ASR 面早就踩过同一个坑）。**HMI 不必知道任何 provider 的 VAD 参数。**

- **`escalated_result` 为何存在**（P0 探针后补入协议）：逃逸轮的执行/回答全在主链，S2S 会话对此**一无所知**——下轮闲聊时模型不知道空调已经调过了，多轮连续性断在每个逃逸轮上。故 HMI 在逃逸轮的主链回答落地后，把回答文本回传网关，网关以 `function_call_output` 注入 provider session（**R1 实测：不发 `response.create` 则完全静默，不会与主链播报撞成双播**）。这条通道**只为上下文连续，不为播报**。
  - 丢了也不坏：R2 实测悬挂 `function_call` 不影响后续对话——HMI 没回传（用户切走/网络抖）时，S2S 只是少一条上下文，会话照常。**无需补偿逻辑**。

下行（网关→HMI）：

```jsonc
{"type": "turn.transcript", "turn_id", "text", "final": bool}   // 用户话转写（上屏复用 partial 气泡）
{"type": "turn.answer_delta", "turn_id", "text"}                // 回答文本增量（字幕/气泡）
{"type": "turn.audio_meta", "turn_id", "sample_rate"}           // 之后跟二进制 PCM 帧（复用既有播放器）
{"type": "turn.end", "turn_id", "reason": "complete|cancelled|escalated"}
{"type": "turn.escalated", "turn_id", "utterance"}              // §6 逃逸：本轮改走文本主链，HMI 按既有 send(utterance) 流程走（含 THINKING 态）
{"type": "session.state", "state": "ready|reconnecting|degraded"}  // degraded=重连超限，HMI 回落三段式（§6.3）
```

- **turn_id 由网关生成**（uuid4；provider 的 response id 只在 L-Session 内部对账用）——上层协议不透传厂商 id（可丢弃缓存原则的协议面）。
- 逃逸轮（`turn.escalated`）**不含音频**：网关不代理执行，HMI 拿 utterance 走既有文本 WS——执行/卡片/确认全部走存量通道，S2S 面零职责。

### 3.3 对下 provider 抽象（`BaseS2SProvider`）

```python
class BaseS2SProvider:
    async def open(self, *, voice, system, context_summary, tools) -> None
    async def send_audio(self, pcm: bytes) -> None
    async def cancel_response(self) -> None          # barge-in 落点：立即停止当前生成
    async def inject_text(self, role, text) -> None  # 工具结果/上下文补注入（§5.2）
    async def close(self) -> None
    def events(self) -> AsyncIterator[S2SEvent]      # 归一化事件流，见下
```

归一化事件（provider→L-Session）：`transcript(text, final)` / `answer_delta(text)` / `audio_delta(pcm)` / `tool_call(name, args, call_id)` / `turn_started` / `turn_done(reason)` / `error(kind)`。**tools 参数**：open 时注入**单一工具 `escalate(utterance)`**——不是把座舱能力清单塞给 S2S 模型（§5.1 讲为什么）。

### 3.4 厂商映射与探针清单（接手人首件事，M1a 探针先例）

- qwen3.5-omni-flash-realtime（首选）：WS `/api-ws/v1/realtime` 同壳（仓库已有两个客户端先例）；server VAD→`transcript`；`response.audio.delta`→`audio_delta`；tool calling→`tool_call`。**★待探针**：①server VAD 与本侧 barge_in（response.cancel）竞态行为 ②tool call 后 `inject_text` 结果再续说的事件序 ③session 内上下文长度上限（决定重注入摘要粒度）④中文口语时延（首音频包 P50/P95）。
- Step-Audio 系自托管（第二候选，验证「锁协议」）：映射同一抽象；无 server 侧工具调用时 `tool_call` 由**转写文本过本侧轻分类**兜底（协议允许 provider 无原生 tool calling——adapter 层能力降级表）。
- **探针脚本先行**（`test/e2e_s2s_probe.py`，M1a `e2e_planner_toolcall` 同款打法）：文档≠实测是本仓库每次接新协议的固定教训（ASR 双协议、qwen finish_reason 前科）。

### 3.5 P0 探针实测结果（2026-07-25，协议冻结基线）

`test/e2e_s2s_probe.py --case all` 实测 **12/12 协议断言通过**。本节即**冻结基线**：HMI 与网关按此并行开发；厂商 API 迭代后重跑本探针验证未漂移。

**选型结论（★T，探针挖出的一票否决项）**：`session.created` **不校验 model**——传任意名字（含 `totally-not-a-real-model-xyz`）都返回 session，只是 echo 名字、配置回落默认。真判据是**默认值是否偏离**（默认 voice=Chelsie/in=pcm16/out=pcm24）。据此逐个实测 tools 支持度：

| 模型 | `session.updated.tools` | 车控句行为 | 可用性 |
|---|---|---|---|
| **`qwen3.5-omni-flash-realtime`** | `['escalate']` 完整回显 | 正确发 `function_call` | ✅ **选定** |
| `qwen3-omni-flash-realtime` | **`<无>`（静默丢弃）** | 口头自答"我可以帮你开空调…" | ❌ 无 escalate 通道 |

**`qwen3-omni-flash-realtime` 不可用于 §5.1 分工契约**——tools 被静默丢弃意味着模型没有移交出口，§5.3 的「漏移交」从概率问题变成必然问题，安全设计整体不成立。**这条是文档读不出来的**（两个模型的 API 文档都写"支持函数调用"）。

**事件映射表（实测事件谱 → 本侧归一化事件）**

| provider 事件 | 归一化 | 备注 |
|---|---|---|
| `input_audio_buffer.speech_started` / `.speech_stopped` / `.committed` | （L-Session 内部） | server VAD 判定，**不驱动 L-HMI 迁移**（§2.2 铁律） |
| `conversation.item.input_audio_transcription.delta` | `transcript(final=False)` | 用户话增量 |
| `conversation.item.input_audio_transcription.completed` | `transcript(final=True)` | 字段名 `transcript` |
| `response.created` | `turn_started` | **server VAD 自驱**（`create_response=True`）——L-Session **不必手动 `response.create`** |
| `response.audio_transcript.delta` | `answer_delta` | 助手回答文本增量 |
| `response.audio.delta` | `audio_delta` | base64 PCM，**24kHz**（见下） |
| `response.function_call_arguments.delta` | （累积，不产事件） | 带 `call_id` **不带 name** |
| `response.function_call_arguments.done` | **`tool_call`** | `name`/`call_id`/`arguments` 在此齐备 → **adapter 归一化落点**（不必等 `response.done`） |
| `response.done` | `turn_done(reason)` | `status=completed\|cancelled`；`output[].type=message\|function_call` |

**★1 barge-in 竞态**：`response.cancel` → `response.done status=cancelled`，**零音频残包**。本侧丢弃保护仍保留（cancel 在途时 delta 可能已在飞），但 provider 侧配合良好。
- **实测挖出的坑**：被 cancel 的轮次，`response.audio_transcript.done` 仍带**完整全文**（30 字），而实播音频只有 3 包（≈1s）。**该文本 ≠ 用户听到的内容**——§7 回灌 assistant 文本时必须按截断处理，否则记忆里存着用户从没听到的话。

**★2 事件序**：见上表。逃逸轮**零音频零文本**（`output=[function_call]`，无 `audio_transcript.delta`）——模型不会先口头答应再移交，§6.2「判定点在生成前」的听感前提**实测成立**。

**R1 双播风险（探针新增，RFC 原文未列）**：回注 `function_call_output` 后**不发 `response.create` → 完全静默**（6s 内只有 `conversation.item.created`）。§5.2「逃逸轮结果只为上下文连续、不为播报」的前提成立——若 provider 自动续说，就会与主链 TTS 撞成双播，设计需整体返工。

**R2 悬挂韧性（探针新增）**：`function_call` 不回注 output 也**不坏会话**，下一轮正常。→ 回灌 provider 上下文失败**无需补偿逻辑**（fail-safe）。

**★3 上下文**：provider 侧多轮保持实测成立（轮2 正确答出轮1 说的名字）。§2.3 前提站住。
- **未钉死**：session 内上下文长度上限（provider 未在 session 暴露该字段）。对我们的用途无实际约束——重注入材料（近 4 轮摘要+persona+焦点）量级 <2KB。真实风险不在上限而在**长会话累积**（成本/时延/触限），故实现给 `S2S_SESSION_MAX_TURNS` 旋钮：超限主动重建 session + 摘要重注入（**复用 §4.5 重连路径，零新机制**）。

**★4 时延**（3 轮采样，自音频推完起算）：首音频包 **P50=609ms / max=703ms**；首文本增量 **P50=328ms**。灰度门槛 §6.2 的首音频 P95 以此为基线。

**输出采样率**：`output_audio_format` 只报 `"pcm"`，采样率**未在协议中声明**——按「字节数 ÷ 2 ÷ 采样率 ≈ 语音时长」反推为 **24kHz**（403200B → 24k≈8.4s 匹配 42 字语速；16k≈12.6s 明显过慢）。下行 `turn.audio_meta{sample_rate:24000}`；**HMI `PcmPlayer` 已支持 `sampleRate` 注入且默认 24000**（qwen TTS 同采样率），播放器零改动。

## 4. 全双工会话状态机

### 4.1 L-HMI：六态语义重映射（不加新状态，不动 KWS/退出/dismiss）

S2S 模式 = voiceLoop 的一组新效果回调注入（构造参数本来就是全注入的——D4 设计红利）：

| 态 | 三段式（现状） | S2S 模式差异 |
|---|---|---|
| IDLE/ARMED | KWS 待机 | **零改动**（唤醒仍本地 KWS，S2S 不参与——不把唤醒外包给云） |
| LISTENING | 开 /api/asr/stream | 开收音门并推 PCM（会话常驻）；上屏数据源换 `turn.transcript`；**本侧 VAD 判到端点 → 上行 `audio_done` 请 provider 收尾**（原文写「provider server VAD 自驱、本侧不请定稿」是错的，见 §11.3-5） |
| THINKING | 等编排 final | 等 `turn.answer_delta` 首包；**逃逸轮**收到 `turn.escalated` → 改挂既有文本链路等待（对用户无感，仍是 THINKING） |
| SPEAKING | 播 TTS 流 | 播 `turn.audio` PCM；**barge-in 判定仍在本侧**（既有 VAD bargeInMinMs 判据）→ 上行 `barge_in` + 本地立即停播（不等网关回执——听感优先，provider 取消是异步收尾） |
| FOLLOWUP | 8s 续问窗 | **零改动**；续问直接复用同一 S2S 会话（多轮上下文是 S2S 的主场） |

**全双工的实质增量**：LISTENING 与 SPEAKING 不再互斥于音频通道（provider 持续收音）——但**用户可感知语义仍是回合制**（说→答）。v1 刻意不做「边听边抢答」的重叠对话（thinking 期免唤醒插话已有 onCancelTurn 资产覆盖），全双工红利先兑现在：唤醒后零 ASR 建连延迟（会话常驻）、barge-in 后无缝续听（不重建通道）、多轮上下文免重注入。

### 4.2 L-Session 状态机（网关 `s2s/session.py`）

```
CONNECTING → READY ⇄ IN_TURN（turn_started..turn_done）
     ↑          │
     └── RECONNECTING（指数退避 ≤3 次）── 超限 → DEGRADED（下行 session.state，HMI 回落三段式）
```

- 对账：本侧 turn_id ↔ provider response id 映射表；**barge_in 到达时**：置当前 turn `cancelling`，调 `cancel_response()`，其后到达的该 response 音频/文本增量**全部丢弃**（防「打断后残包续播」——R4.2 barge-in v1 的同款教训在会话层重现）。
- 心跳：复用 aiohttp WS heartbeat 惯例（providers.py 既有 20s）。

### 4.3 打断语义三层表（本 RFC 最重要的表之一）

| 层 | 触发 | 权威 | 动作链 | 关键约束 |
|---|---|---|---|---|
| ①听感打断 | SPEAKING 期本侧 VAD 判 speech ≥ bargeInMinMs（或 KWS） | **L-HMI** | HMI 立即停播 → 上行 `barge_in` → L-Session cancel_response + 丢残包 → 进 LISTENING（同会话续听） | provider server VAD 可能也自判打断——**以本侧为准**，provider 侧多取消一次无害（幂等），少取消由 barge_in 兜底 |
| ②任务打断 | 逃逸轮 THINKING 期用户再说话/取消词 | L-HMI → 编排 | 既有 `onCancelTurn` → 文本 WS `{type:cancel}`（M1a 通道）→ gRPC ctx 取消；长任务另有 Ledger cancel 语义（「别查了」是新指令不是打断） | **零新机制**——存量通道原样 |
| ③工具调用中打断 | S2S 轮已派发 escalate、确定性链在飞，用户打断 | L-Session | 标记该 turn `abandoned`：执行结果回来后**不再 inject_text 回 S2S、不播报**；**但副作用步照常走完**（确认挂起照常挂——确认是 SessionState 语义，用户下句「确定」仍能续接） | 打断≠回滚：已进确认链的动作由确认链自己收束，静默丢弃的只是「播报权」。防两个错误：结果回来抢话（双播）、以为打断就该取消副作用（用户打断常是为了补充，不是反悔） |

### 4.4 与 M3 主动引擎的交叉

主动消息（提醒触达/任务完成推送/围栏触发）在 S2S SPEAKING 期到达：**不抢话**——M3 治理器本来就有投递期复核/延后语义，S2S 会话状态经网关上报为一个「驾驶负荷」类情境输入（`s2s_speaking=true`），治理器按既有 advisory 延后逻辑排队，S2S turn.end 后放行。**接手人只需把该情境位接进 M3 断言源**，治理逻辑零新增。

### 4.5 重连与状态恢复时序（§2.3 的落地）

```
断连（网络抖/provider 1011）
→ L-Session 进 RECONNECTING：新 session + open(context_summary=memory 近4轮摘要+persona+焦点)
→ 成功：READY，HMI 无感（正在录的音频在网关侧 ring buffer 续灌，≤2s 补偿——复用 R4.3b pcmRing 前滚思想）
→ 3 次退避失败：DEGRADED 下行 → HMI 本轮回落三段式（§6.3），后台每 30s 探活，恢复后下轮回 S2S
```

**IN_TURN 中断连**：当前 turn 按 `turn.end(reason=cancelled)` 收束 + HMI 出「刚才说到一半断了，你可以再说一遍」话术（诚实降级，不假装无事）。

## 5. S2S × 确定性安全链分工（安全上最重要的一节）

### 5.1 单工具 `escalate`：S2S 模型只有一个出口，没有能力清单

**决策：不把座舱 capability 清单注入 S2S 会话的 tools。** S2S 模型 open 时只拿到一个工具：

```json
{"name": "escalate", "description": "用户的请求超出闲聊/常识问答范围（车辆控制、导航、查询实时信息、提醒、支付等一切需要执行或查询车辆/外部系统的请求）时调用。把用户请求原样转述。", "parameters": {"utterance": "string"}}
```

理由（对齐三条既有决议）：
- **M1a 教训直接适用**：tool schema 会三向改变模型输出分布——把几十个 capability 塞进 S2S 会话，误触发/漏触发/编造槽位在语音场景没有旅程级护栏可兜（S2S 轮不过 planner 校验）。单工具把「判定权」压缩为二元：自答 or 移交——错误面只剩「该移交没移交」（§5.3 有背板）。
- **权威链无损**：utterance 进入文本主链后，submit_plan/route_hints/Skill 注入/require_confirm 闸/VAL/R4.4 澄清**逐字全量生效**——S2S 不是新的规划入口，只是新的「话筒」。审计上：S2S 轮次的任何副作用都能在既有 trace 里找到完整决策链。
- 灰度期（chitchat 域）连 escalate 的槽位质量都不影响执行正确性（utterance 是原话转述，槽位抽取在主链做）。

### 5.2 逃逸轮完整时序（正常路径）

```
用户（S2S 会话中）：「把空调调到24度」
→ provider tool_call: escalate(utterance="把空调调到24度")
→ L-Session：turn 转 escalated；下行 turn.escalated{utterance} + turn.end(escalated)
→ HMI：按既有 send(utterance, input_source=voice) 走文本 WS → 端侧 fast_intent 先接（车控秒回！）
   或上云主链（R4.4 受话判定→planner→executor→VAL/确认）
→ 回答经既有 TTS 链路播报（三段式）；HMI 回 FOLLOWUP
→ HMI 上行 `escalated_result{turn_id, text=主链回答}`（§3.2）→ 网关以 function_call_output 注入
   provider session，保持上下文连续（**不发 response.create，故不播报**——R1 实测已钉死）
```

- **端侧快路径的保留是这个设计的隐藏红利**：escalate 落回 HMI send 意味着「打开空调」仍走 fast_intent 毫秒路径——S2S 不在车控链路上，既保安全又保时延。
- 逃逸轮的听感代价：回答用三段式 TTS（换了引擎音色）——**缓解**：TTS 音色配置与 S2S voice 选同系音色（HMI 设置两级选择资产）；v1 接受「执行类回答音色略不同」，不接受「同一句话播到一半换嗓子」（这就是 §6.2 逃逸判定点前置的原因）。

### 5.3 该移交没移交的背板（S2S 模型自答了车控怎么办）

S2S 模型直接口头答应「好的，空调已调到24度」但没 escalate——车没动，模型说谎。三道背板：
1. **物理背板**：S2S 面没有任何执行通道——最坏结果是「口头答应没办事」，**绝无「没确认就执行」**（安全事故不可能，体验事故可能）。
2. **检测背板**：网关对 `answer_delta` 聚合文本跑**轻量承诺检测**（「已/帮你/好的，…开/关/调/导航」类模式 + 无 escalate 记录 → span 标 `s2s_false_promise`）——进 obs，自进化 nightly（M1b 资产）自动挖掘该族 badcase，量化「漏移交率」供灰度决策。**v1 只检测不拦截**（拦截=打断已播出的音频，听感更糟；靠 persona 提示+域灰度收窄把率压低）。
3. **语料背板**：S2S 上线评测集必含「闲聊中夹车控」对抗句（journeys 新 lane，§9），漏移交率是灰度门槛硬指标。

### 5.4 R4.4 受话判定在 S2S 下的位置

S2S 授权域内（chitchat）：受话判定由 S2S 模型的对话能力天然承担（乘客对话/电台声它答非所问的代价=一句废话，可接受）；**逃逸轮**进入主链后 R4.4 全套照走（hands-free 源标记随 send 透传，input_source=voice 既有约定）。KWS 唤醒仍是总闸——S2S 会话只在唤醒后的交互窗内收音（ARMED 态不推流，隐私边界与现状一致：**音频不出车的例外仅在用户主动唤醒的会话窗内**，与 §9.3 数据合规对齐，RFC 明示这是隐私口径变化点——三段式只上行「定稿文本」，S2S 上行「原始音频」，需在隐私声明与设置开关（HMI「语音引擎」选项）中显式呈现，默认关、用户显式选择 S2S 才开）。

## 6. 双链路共存与域灰度

### 6.1 常态双链路

`VOICE_PIPELINE=classic|s2s`（会话级，HMI 设置切换，默认 classic）——**s2s 挡位下三段式也不下线**：逃逸轮、DEGRADED 降级、非语音入口（打字）全走三段式。二者共享：memory、obs、TTS 播放器、voiceLoop FSM、唤醒层。

### 6.2 域灰度 = escalate 描述的宽窄，不是运行时白名单

灰度推进（chitchat 先行 → +轻查询 → …）通过**收放 escalate 工具的 description 边界**实现（「实时信息查询也自答」vs「一切查询移交」），配合评测门槛推进；**不做运行时按 intent 拦截 S2S 回答**——判定点在模型生成前（工具选择），生成后拦截必然截断音频。每档灰度门槛：漏移交率（§5.3-3）+ chitchat 质量对照（S2S vs 三段式同语料）+ 首音频包 P95。

### 6.3 逃逸/降级的听感统一

三种「离开 S2S」路径共用同一 HMI 处理：`turn.escalated`（本轮走主链）、`session.state=degraded`（回落三段式直到恢复）、用户设置切回 classic。HMI 无分支特判——都收敛为「本轮按既有 send 流程走」。

## 7. 文本副产品回灌（防黑洞，强制项）

S2S 轮次绕过 edge-gateway，若不回灌则：记忆断代（下次 planner 看不到这几轮）、obs 无轮次记录（badcase 无从查）、自进化失明。**网关在每个 turn.end 后强制回灌**：

1. **memory**：AppendTurn(user=transcript, assistant=answer_text)——走既有 gRPC 接口，session_id 同一会话（记忆抽取/画像管线零改动，S2S 轮天然参与）。
   - **被打断轮按截断处理**（★1 实测）：provider 的 `audio_transcript.done` 带模型已生成的**完整全文**，但用户只听到了打断前那一小段。回灌时只存**已播出的增量**（`answer_delta` 累积到 barge_in 时刻为止）并标 `truncated`——否则记忆里存着用户从没听到的话，下轮 planner 据此指代就会错。
2. **obs**：`obs.turn` 事件（path="s2s"，speech=answer_text，转写进 user_text）+ span `s2s.turn`（provider/时延/打断/escalated/false_promise 标记）——dashboard 既有三级下钻直接可用。
3. escalated 轮不重复回灌（主链已落，只补 span 关联 turn_id↔trace_id）。

## 8. 非核心件边界速写（接手人展开，本 RFC 只锁边界）

- **声纹多用户**：识别点在**唤醒后首句**（LISTENING 首个定稿/S2S 首个 transcript 送声纹服务，DashScope/3D-Speaker 档）；产出 `occupant_id` 注入 send meta 与 S2S session context——**下游零改动**（memory 的 occupant 维度 schema 早已就位，M2 图谱 preference 表带 occupant）。边界：v1 只做「区分主驾/乘客的记忆隔离」，不做声纹开锁级安全（声纹不是鉴权因子，红线）；注册流程（「记住我的声音」）设计留接手人。DoD 承接母提案「多用户记忆隔离旅程」。
- **视觉入口**：「那是什么」车外摄像头单帧 → 走**既有 escalate 同构**（图片+问题投文本主链新 capability `vision.describe`，qwen3-omni 图片理解档）——不进 S2S 会话流（视频流实时理解是 v2，成本与隐私都另议）；帧采集触发词与隐私门控（默认不采、请求时单帧）是关键约束。
- **sim.adas.***：低优先 backlog 维持（§8-6 拍板），不进 M4。

## 9. 分期、DoD 与测试

**P0 探针+协议冻结**：`e2e_s2s_probe.py` 钉死 qwen3.5-omni 四个★（§3.4）→ 按实测修订 §3 协议 → 冻结对上事件协议（HMI/网关并行开发的接口基线）。
**P1 链路最小闭环**：网关 `s2s/`（session+qwen adapter）+ HMI s2s 回调组 + chitchat 域自答 + escalate 逃逸 + 回灌。DoD：真栈——闲聊多轮连续（S2S 会话上下文生效）、「打开空调」逃逸后端侧秒回、记忆/obs 回灌断言（e2e_s2s.py）。
**P2 打断/重连/降级**：barge-in 三层表全实现 + 重连重注入 + DEGRADED 回落。DoD：SPEAKING 期打断 ≤300ms 停播且续听不重建；kill provider 连接后 2s 内无感恢复、3 次失败回落三段式并诚实播报；工具调用中打断零双播（journeys 新 lane：S2S 语音旅程 4 条——闲聊连续/夹车控/打断/断线）。
**P3 灰度与门槛**：false_promise 检测接 obs+自进化；同语料 S2S vs classic 对照（chitchat 质量+首音频 P95）；漏移交对抗集 ≥95% 移交率达标才扩下一域。
**P4（并行线，接手人）**：声纹注册+隔离旅程；视觉单帧入口。

## 10. 不做清单与风险

**不做**：重叠对话/抢答式全双工（v1 语义仍回合制）；S2S 域内运行时拦截（判定点前置）；把 capability 清单注入 S2S tools（§5.1）；唤醒外包云端（KWS 本地保留）；视频流实时理解；声纹作鉴权因子；自研声学。

| 风险 | 缓解 |
|---|---|
| S2S 漏移交（口头答应车控） | 物理背板（无执行通道）+ false_promise 检测量化 + 对抗集门槛 + escalate description 调参（§5.3） |
| provider server VAD 与本侧 barge-in 竞态 | 本侧权威 + cancel 幂等 + 残包丢弃（turn 对账表）；P0 探针①专项 |
| 重注入摘要不足致 S2S「失忆感」 | 摘要粒度 A/B（近 4 轮 vs 8 轮）；探针③先钉上下文上限 |
| 逃逸轮音色切换的听感割裂 | 同系音色配置 + 逃逸判定前置（绝不中途换嗓）；灰度问卷验证接受度 |
| 原始音频上云的隐私面扩大 | 默认 classic、显式开关、仅唤醒窗内收音、隐私声明更新（§5.4）|
| 厂商协议漂移（realtime API 迭代快） | adapter 两端契约隔离；本侧事件协议冻结于 P0；探针脚本可重跑验证 |

---

## 11. 落地记录（2026-07-25，P0→P3）

> 本节是**实施后**回填的事实与偏差。设计部分（§0-§10）保留原文，被实测推翻处已在 §3.5 标注。

### 11.1 交付物

| 期 | 交付 | 提交 |
|---|---|---|
| P0 | `test/e2e_s2s_probe.py` 协议探针（7 组 12 断言）→ 选型钉死 + §3.5 冻结基线 | `4bcf4b2` |
| P1 | 网关 `llm-gateway/s2s/`（protocol/provider/session/reflux 四层）+ `/api/s2s`、`/api/s2s/info`；HMI `s2sClient.mjs` + 效果回调组 + 设置挡位；`test/e2e_s2s.py` | `d4b1be6` |
| P2 | 打断三层实现 + 重连重注入 + DEGRADED + turn 看门狗；`test/e2e_s2s_resilience.py` | 本批 |
| P3 | `test/eval_s2s_escalation.py` + `s2s_escalation_cases.yaml`（24 条含对抗集）；`s2s_false_promise` 进 obs | 本批 |

### 11.2 设计被实测修正的地方

1. **选型从「候选」变成「唯一可用」**（§3.5 ★T）：`session.created` 不校验 model，假名字也返回
   session。逐个实测 tools 支持度才发现 `qwen3-omni-flash-realtime`（无 `.5`）**静默丢弃 tools**
   ——它上面 §5.1 的分工契约整体不成立。**两个型号的官方文档都写「支持函数调用」。**
2. **协议补 `escalated_result`**（§3.2）：原设计漏了「逃逸轮的执行结果怎么回到 S2S 会话」。
   不补则多轮连续性断在每个逃逸轮上。R1/R2 实测保证了它既不双播也丢得起。
3. **收音门控收敛为「只在 LISTENING 期推流」**（比 §4.1 原文保守）：provider
   `interrupt_response=true` 会自主判打断，SPEAKING 期推流就与「本侧权威」打架。不推流则它
   根本没机会自主打断——**本侧 100% 权威是靠不给它输入实现的，不是靠约定**。
4. **被打断轮的文本 ≠ 用户听到的**（§4.2/§7）：provider 的 `audio_transcript.done` 带完整全文。
   回灌只存已播增量并标 `truncated`，否则记忆里存着用户从没听到的话，下轮指代必错。
5. **P2 的 journeys lane 改为 e2e 场景覆盖**：§9 原写「journeys 新 lane 4 条」，但 journeys
   runner 是**文本驱动**（发文本给 edge-gateway），跑不了音频通道——S2S 旅程无法用它表达。
   改为在 `e2e_s2s.py` 覆盖四种形态（闲聊连续/夹车控/打断/断线），断言更强（能验残包与回灌）。

### 11.3 实施中发现并修掉的缺陷（真栈才暴露）

1. **turn 悬挂无收口**：客户端慢读 → 下行 send 背压 → 事件泵阻塞 → provider 侧数据丢 →
   该 turn 永不 `turn_done`，HMI 干等到 voiceLoop 100s 兜底。加 turn 看门狗
   （`S2S_TURN_TIMEOUT_S=45`）诚实收 `turn.end(error, provider_silent)`——**不追究具体成因，
   任何原因导致的悬挂都在此收口**（同 M2 Ledger「可查可停可诚实报告中断」的思想）。
2. **重连泄漏 ClientSession**：`_reconnect` 直接覆盖 `self.provider` 没关旧的，每次重连泄漏一个
   aiohttp session。由韧性验证的 "Unclosed client session" 警告暴露。
3. **e2e 自身的写法错误**：「先推完音频再读」不是真实客户端行为（浏览器 WS 事件驱动、总在读），
   顺序写法会自造背压把整轮回答弄丢。改并发读写。
5. **【真机首验的死锁，最严重的一个】S2S 下 `onEndpoint` 被做成了空操作**，理由写的是「provider
   server VAD 自驱轮次」——但 server VAD 靠**连续静音输入**判端点，而 HMI 在本侧 VAD 判到端点后就
   停止推流，provider 永远等不到静音：turn 开了却永不收束，**用户说什么都没有回复**（网关日志里是
   一串「turn 悬挂超 45s 未收束」）。修法=端点判定权留在本侧、经新增的 `audio_done` 上行帧请网关收尾，
   `BaseS2SProvider.commit_audio()` 按厂商方式实现（qwen 补静音尾）。
   - **两个可复用的教训**：① 仓库的 ASR provider 早就解决过同一问题（流末补静音尾，注释写着「须 >
     silence_duration_ms 才生效」）——接同一族协议时**先去翻既有实现踩过的坑**，我这次没翻；
     ② **e2e 把它整个掩盖了**：脚本自己发 13 帧静音，替生产代码做了它没做的事。e2e 模拟客户端就得
     **只做客户端做的事**，生产缺的那一步必须在测试里也缺出来。修 e2e 后同一套断言立刻复现死锁。

4. **评测把调用失败算成了模型判断**：音频路径首跑报移交率 85.7%，逐条看才发现 4 条命中 provider 侧
   `algorithm server connection closed`，而脚本把「没拿到 function_call」一律记作「模型选择自答」——
   其中 2 条 `expect=escalate` 判红、**2 条 `expect=self_answer` 假绿**。这类缺陷会让指标朝好看的方向
   失真（错误越多，自答准确率越高），比单纯的红灯更危险。修法：error 单列、重试一次、**排除出分母**，
   且错误率 >10% 直接判「报告作废」（同 journeys「provider 漂移即作废」口径）。修后两条路径都 24/24。

### 11.4 验证证据

| 项 | 结果 |
|---|---|
| 协议探针 `e2e_s2s_probe.py --case all` | **12/12** 协议断言通过 + 3 项量测（首音频 P50=609ms/max=703ms、首文本 P50=328ms、输出 24kHz） |
| 真栈 `e2e_s2s.py` | **25/25**（自答闭环 / 多轮上下文 / escalate 逃逸零音频 / 打断 0ms 响应零残包 / 打断后同会话续听且上下文仍在 / 工具调用中打断不播报 / unsupported 回落 / 回灌 memory+obs 且逃逸轮不重复写） |
| 韧性 `e2e_s2s_resilience.py` | **11/11**（断连→625ms 重连 + 摘要重注入 / 重连后记得断线前的话 / IN_TURN 断连诚实收 `cancelled+disconnected` / 持续不可达→DEGRADED 且不再上行音频） |
| 灰度 `eval_s2s_escalation.py` | 两条路径都 **24/24=100%**：文本路径移交 14/14、自答 10/10，613ms/轮；**音频路径**（含转写误差，与线上一致）移交 14/14、自答 10/10，2160ms/轮，零 provider 错误 ← 门槛 ≥95%。对抗集含 6 条夹在闲聊里的动作句（「今天真热啊，把空调开低一点」「有点冷」「这歌不好听，换一首」） |
| 单测 | 网关 **67**（协议/事件映射/工厂拒不支持型号/三层打断/重连降级/看门狗/**音频段收尾 5**[死锁回归护栏：audio_done 必落 commit、新音频与 barge_in 撤在途静音尾、DEGRADED 空操作、静音总时长须超 VAD 判定窗]/回灌两条易漏项/源码级铁律 3）+ HMI node **28** |
| 全量 | `pytest` **2189 passed, 7 skipped**（M3 基线 2122，净 +67）；HMI node **171**（143 既有 + 28）；tsc 错误数不变、build 通过 |

### 11.5 余项（都不阻塞，按需取用）

| 余项 | 说明 |
|---|---|
| **§4.4 主动消息与 S2S 播报的交叉未实现** | RFC 说「把 `s2s_speaking` 情境位接进 M3 断言源，治理逻辑零新增」，但「上报」本身要建一条**网关→proactive 的新链路**，不是零成本；且 §9 的 P1/P2 DoD 未列此项。**实际影响很小**：HMI 侧绝大多数主动消息只出气泡不播报，唯一会出声的是「异步深调研完成」（带 card 那类），只有它可能与 S2S 播报重叠。真要做，正确形态仍是治理器侧延后而非 HMI 侧排队——因为 critical 类（安全预警）**应该**抢话，HMI 队列区分不了 |
| 真麦声学验收 | 打断手感、唤醒后首字、S2S 音色接受度——浏览器声学层 CI 测不了，同 R4.3 惯例留泓舟 |
| 重叠对话（抢答式全双工） | §10 明确不做：v1 语义仍回合制 |
| 中途重连的**端到端**验证 | 已由宿主内代理注入验证会话层（真 provider、真断线）；穿过 `/api/s2s` 的那一跳未注入断连——需要容器级网络故障注入，价值不抵成本 |
| `s2s_false_promise` 的自进化接线 | 检测已进 obs span，nightly 挖掘该族 badcase 待 M1b 流水线加一条规则 |
| 声纹多用户 / 视觉入口（P4） | §8 边界速写在案，未实施 |
| 逃逸轮音色切换 | 已配同系音色选项（设置页「直连音色」），真机接受度待验收 |
