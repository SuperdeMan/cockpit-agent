# LLM Gateway

所有 LLM 调用的唯一出口。屏蔽厂商差异，提供多模型路由与降级。

## 接口（见 proto/cockpit/llm/v1/llm.proto）
- `Complete` 同步补全
- `CompleteStream` 流式补全

## 多 LLM 源 + 全局运行时切换（`llm_runtime.py`）
座舱「单一大脑」模型：进程内 provider 注册表持有所有**已配置 key** 的厂商，全局 active 经 HMI 设置页
运行时切换（`POST /api/llm/provider`），所有服务的 LLM 调用随之切换。一套参数化 `OpenAICompatibleProvider`
靠 `token_param`/`thinking_style`/`auth_style` 三个 per-provider 参数覆盖各家差异（换/加服务商只改
`_PROVIDER_SPECS` 的 env 与配置，不改调用方）。

| 厂商 id | endpoint | auth | token 参数 | 思考开关 | key（env） | primary / fast |
|---|---|---|---|---|---|---|
| `mimo` | token-plan-cn.xiaomimimo…/chat/completions | api-key | max_completion_tokens | `thinking:{type:disabled}` | `LLM_API_KEY` | mimo-v2.5-pro / mimo-v2.5 |
| `minimax` | api.minimaxi.com/v1/chat/completions | bearer | max_completion_tokens | `thinking:{type:disabled}` | `MINIMAX_API_KEY` | MiniMax-M3 |
| `deepseek` | api.deepseek.com/v1/chat/completions | bearer | max_tokens | `thinking:{type:disabled}`（v4 是推理模型，须关思考拿干净 content） | `DEEPSEEK_API_KEY` | deepseek-v4-pro / deepseek-v4-flash |
| `qwen` | dashscope…/compatible-mode/v1/chat/completions | bearer | max_tokens | `enable_thinking:false`（qwen3） | 复用 `LLM_EMBED_API_KEY`/`DASHSCOPE_ASR_KEY` | qwen3.7-max / qwen3.7-plus |
| `anthropic` | Anthropic Claude SDK（legacy，`LLM_PROVIDER=anthropic`） | — | — | — | `LLM_API_KEY` | env 指定 |
| `mock` | 无任何 key 时的回显兜底（严格栈 `REQUIRE_REAL_PROVIDERS=on` 时禁止成为 active，启动即拒） | — | — | — | — | mock |

> **推理内容出口清理（2026-07-12 实测）**：MiniMax-M3 **开思考**时把思考段以 `<think>…</think>`
> **内联在 content 头部**（非 `reasoning_content` 字段；关思考正常，mimo/deepseek/qwen 四态干净）
> → provider 出口统一剥离（`strip_think_block` + 流式 `ThinkStreamStripper` 头部状态机，未闭合=
> 截断在思考里则诚实置空）。另：TTS 入口（`_sentence_segments` 流式漏斗 + `/api/tts` 批处理）统一剥
> markdown（`_strip_md_tts`，与 `agents/_sdk/grounding.strip_markdown_speech` 配对口径），星号/表格符不进语音。

## 路由与降级（运行时硬化 P0-P2，2026-07-17，设计见 `docs/design/2026-07-17-llm-runtime-hardening.md`）
- 未指定 model / 传空 → active provider 的 `primary`；传档位哨兵 `@fast` → active 的 `fast`（chitchat 按
  `model_pref` 传哨兵，**不传具体模型名**，避免切厂商后把 A 家模型名误发给 B 家）；传当前 provider
  不认识的具体模型名 → 回落 primary（防御）。primary 失败降级到 fast。
- **429 单独分类**：`Retry-After ≤ LLM_429_WAIT_CAP_S`（默认 2s）且预算余量足 → 等一次重试**同模型**；
  否则跳过剩余档位（限流多为账号级）→ 映射 `RESOURCE_EXHAUSTED`（SDK 只对 UNAVAILABLE 重连重试，
  429 不再白打）；请求性 4xx（400/403/413/422）→ `INVALID_ARGUMENT`；上游超时 → `DEADLINE_EXCEEDED`。
- **流式降级**：`CompleteStream` **首 token 前**失败按档位链降级到下一模型；首 token 后不切
  （半段话术不可拼接）。
- **请求级 pin（D2）**：`meta.llm_provider`（可选 `meta.llm_model`）按指定厂商的档位表解析并调用
  （缓存/obs/健康记实际 serving），pin 到未配置厂商 → `INVALID_ARGUMENT` fail-closed。评测锁定与
  dashboard 重放 A/B 用；WS 入口 `meta` 字段整链透传（prefs 白名单 + SDK/planner contextvar）。
- **embedding 解耦**：`Embed` 走独立 `embed_provider()`（按 `LLM_EMBED_*` 配 DashScope 百炼），**与 active
  chat provider 无关**——切到无 embedding 能力的厂商（DeepSeek/MiniMax）也不影响记忆语义召回。
- 未配置任何 chat key → `MockProvider`，保证可离线跑通（严格栈 `REQUIRE_REAL_PROVIDERS=on` 时
  llm/embed/asr/tts 四处 mock 决议均启动即拒，豁免 `REQUIRE_REAL_EXEMPT`）。
- **active 切换持久化 Redis（`llm:active`）**：网关重启/重建自动恢复上次选择，不再回落
  `LLM_PROVIDER` env 默认（07-12 事故根治）；Redis 缺包/不可达降级进程内存态（仅告警不拒启）。
  HMI 启动时的选择重放保留（幂等兜底）。
- **被动健康 + 按需探针（D5）**：调用路径滚动窗口记账（ok/err/timeout/rate_limited/EWMA 时延，
  `health.py`），`GET /api/llm/providers` 附 `health` 块（HMI 设置页健康点据此渲染）；
  `POST /api/llm/probe {provider?}` 按需体检（1 条小请求）。**刻意无周期探活**——不给付费 API 烧闲置 token。

## HMI HTTP 代理（http_server.py，端口 50059）
HMI 是浏览器、不能直连 gRPC，故同进程内起一个 CORS 放开的 HTTP 代理：
- `POST /api/asr` 批处理语音识别、`POST /api/tts` 批处理合成、`GET /api/voices`(可带 `?provider=cosyvoice|qwen|mimo`) 音色列表（经 ASR/TTS Provider）。
- `GET /api/asr/stream`（**WebSocket**）流式识别上屏 + `GET /api/asr/stream/info` 引擎能力探测（见下节）。
- `GET /api/tts/stream`（**WebSocket**）服务端流式 TTS + `GET /api/tts/stream/info` 引擎+音色+可用性探测（见下节）。
- `GET /api/s2s`（**WebSocket**）端到端语音会话 + `GET /api/s2s/info` 能力探测（见下节）。
- `POST /api/voiceprint/identify|enroll` / `GET /api/voiceprint/info` /
  `PATCH /api/voiceprint/{occ}`（改称呼，不重录） / `DELETE /api/voiceprint/{occ}`
  声纹面（M4 P4，见下节）。
  > **跨域方法白名单**：HMI 永远跨域调本面，新增非 GET/POST 端点必须同步 `CORS_METHODS`——
  > 漏了浏览器 preflight 会直接挡下，服务端零日志（2026-07-26 声纹删除真机 P0）。
  > 契约测试 `tests/test_http_cors.py` 按「注册了什么方法就必须允许什么方法」自动比对。
- `POST /api/vision/frame` / `GET /api/vision/info` 视觉单帧面（M4 P4，见下节）。
- `GET /api/llm/providers` 列出已装配的 LLM 厂商+模型+可用性+当前 active+**health 被动健康块**（供 HMI 设置页两级选择与健康点）；`POST /api/llm/provider` `{provider,model?}` 全局切换 active（**持久化 Redis**）；`POST /api/llm/probe` `{provider?}` 按需体检指定厂商（1 条小请求回 ok/latency 并记入 health）。
- `GET /api/memory/session` / `GET /api/memory/context` 只读记忆（转发 memory gRPC，供 HMI 记忆视图）。
- ASR/TTS Provider 同样在无 `LLM_API_KEY` 时走 mock。

## 流式 ASR（实时识别上屏）
设计见 `docs/design/2026-06-30-asr-streaming-design.md`。WS `/api/asr/stream`：HMI 推音频帧（webm/opus）→ 网关**流式 ffmpeg** 转 PCM16 16k → 引擎 → 回 `{type:partial/final}`。引擎经 `ASR_STREAM_PROVIDER`/请求级覆盖切换（`providers.py` 的 `build_streaming_asr_provider` 工厂**按模型名路由**）：
- **DashScope qwen3**（默认，`qwen3-asr-flash-realtime-2026-02-10`，**id 须全小写**）：OpenAI 兼容 Realtime 协议（`/api-ws/v1/realtime`；base64 音频、`session.update`+`input_audio_buffer.append`、server_vad、`conversation.item.input_audio_transcription.text/.completed`）。
- **DashScope fun-asr / paraformer**（`fun-asr-realtime`）：DashScope **run-task** 协议（`/api-ws/v1/inference`；**二进制音频帧**、`run-task`→`result-generated`→`task-finished`）。与 qwen3 端点/协议不同，工厂自动按 id 路由。
- 两者复用百炼 `LLM_EMBED_API_KEY`（或独立 `DASHSCOPE_ASR_KEY`）。
- **MiMo 分块**（`mimo-chunked` 回退）：累积 PCM 每 ~1.2s 封 WAV 打 MiMo 批 ASR 产伪 partial，无百炼 key 时可用。
- 批处理 `/api/asr` 保留作回退；任一环失败 HMI 无感切回批处理。

## 流式 TTS（服务端 PCM 流式合成 + barge-in）
设计见 `docs/design/2026-07-04-r4.2-streaming-tts-bargein.md`。WS `/api/tts/stream`：HMI 送 `{type:start,provider,voice}`→`{type:text,delta}`(增量)→`{type:finish}`/`{type:cancel}`；网关经流式引擎**边合成边回** `{type:meta,sample_rate,format}` + **PCM 二进制音频帧** + `{type:done,first_chunk_ms}`。引擎经 `TTS_STREAM_PROVIDER`/请求级覆盖切换（`providers.py` 的 `build_tts_stream_provider` 工厂）：
- **DashScope cosyvoice-v3-flash**（默认）：**run-task** 协议（`/api-ws/v1/inference`；run-task→task-started→`continue-task`(每 delta)→**二进制音频帧**→finish-task→task-finished；PCM s16le 22050Hz，首帧 ~469ms）。音色须 v3 专属（`longxiaochun_v3` 等，v2 名会 418）。
- **DashScope qwen3-tts-flash-realtime**：**realtime** 协议（`/api-ws/v1/realtime`；session.update→`input_text_buffer.append`(每 delta)→`response.audio.delta`(base64)→commit/finish；PCM s16le 24000Hz，首帧 ~719ms）。含北京/上海/四川方言音色。cosyvoice/qwen 复用百炼 `LLM_EMBED_API_KEY`（或独立 `DASHSCOPE_ASR_KEY`）。
- **MiMo v2.5 流式**（`mimo`）：MiMo TTS `stream:true`+`audio:{format:pcm16}`，SSE 逐 chunk 取 `delta.audio.data`（base64 pcm16@24k）。复用 `LLM_API_KEY`。
- **MiniMax T2A 流式**（`minimax`）：**默认走 WebSocket 长连接**（`MiniMaxWsStreamingTTSProvider`，`wss://api.minimaxi.com/ws/v1/t2a_v2`，`Authorization: Bearer` 走 header）：`connected_success`→`task_start`→`task_started`→N×`task_continue`→`task_finish`→`task_finished`，音频在 `task_continued` 的 `data.audio`（**hex**）。复用 `MINIMAX_API_KEY`（与 MiniMax LLM 同 key）。两个必须记住的坑（2026-08-27 真栈取证）：① **`is_final` 是段级不是任务级**（首段几百毫秒就 IS_FINAL，其后还有 200+ 条音频帧，拿它收尾会截断大半）；② **逐字发 `task_continue` 是错误用法**（音频总长翻倍 + 撞 RPM 限流），所以仍走 `_sentence_segments`、只是开 `soft_break`。
  `MINIMAX_TTS_TRANSPORT=http` 退回旧的 HTTP 形态（`/v1/t2a_v2` `stream:true`，SSE `data.audio` hex；**注意**它末尾会发 `status:2` 汇总帧把整段重发一次——已有增量时须跳过否则双份播放）。
  **为什么默认换成 WS**：HTTP 版是 per-sentence 一次 POST，必须等 `_sentence_segments` 吐出一整句才发得出去，而 `_SENTENCE_END` 不含逗号 ⇒ 逐字到达的 speech_delta 要等到第一个句号。同一句同一节奏经云栈实测：**HTTP 首音 1453ms → WS 516~563ms**（比 cosyvoice 还快）。
- mimo（以及 `MINIMAX_TTS_TRANSPORT=http` 时的 minimax）的 TTS API 是「整段文本一次入」，靠 `providers._sentence_segments` 句级切分逐段流式合成、边说边播。
  `_sentence_segments(soft_break=True)` 让逗号/顿号/冒号也断，**只给长连接类 provider 用**——HTTP-per-request 类开了会把一句话拆成多次请求。
- 无对应 key → 工厂返 None → `stream/info` 报 unavailable → HMI 无感回退批处理 `/api/tts`。`mock` 引擎产静音分片供 nightly/无 key 验证协议。
- HMI 侧 `pcmPlayer.mjs` 无缝拼播、`cancel`/断连传播到供应商任务取消（barge-in）；`STREAMING_TTS_PROVIDERS`（`hmi/src/audio.ts`）须与本节引擎清单一致，否则漏配的引擎会误走批处理。

## 端到端语音 S2S（`s2s/` 子包，M4）

设计与协议冻结基线见 `docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`（§3.5 事件映射表 / §11 落地记录），
契约登记 `docs/conventions.md` §9.10。WS `/api/s2s`：HMI 推 PCM（16k mono s16le）→ provider
realtime 会话 → 回转写/回答增量/PCM 音频（24kHz）/turn 生命周期。

**四层分工（换厂商只加 `provider.py` 的子类，其余一字不动）**：

| 文件 | 职责 |
|---|---|
| `protocol.py` | 对上事件协议 + 单工具 `escalate` 定义。**HMI 只认这层，永不随厂商变** |
| `provider.py` | `BaseS2SProvider` 抽象 + qwen omni realtime 实现 + mock；工厂对 tools 不支持的型号 **fail-fast** |
| `session.py` | L-Session 状态机：turn 对账 / barge-in 残包丢弃 / 重连与摘要重注入 / DEGRADED / turn 悬挂看门狗 |
| `reflux.py` | 文本副产品**强制回灌** memory+obs（防记忆黑洞）+ 漏移交检测 |

**安全铁律**：S2S 会话内**没有任何执行通道**。模型唯一的工具是 `escalate`，它只把用户原话交回文本
主链——车控/支付照走 planner→executor→VAL→确认闸。**不得把 capability 清单注入 S2S 的 tools**
（那等于把执行判定权交给一个不过 planner 校验的模型）。S2S 是新的「话筒」，不是新的规划入口。

**两个最容易踩的实现点**：
- **`audio_done` 不能省**。本侧 VAD 判到端点后必须上行它，网关经 `commit_audio()` 补静音尾触发
  server VAD 收尾。server VAD 靠**连续静音**判「说完了」，而 HMI 端点后即停推流——不发就是死锁
  （provider 等静音 ↔ HMI 等定稿），表现为 turn 永久悬挂、**用户说什么都没有回复**（真机首验踩过）。
  静音尾长度须 > `silence_duration_ms`，与 `DashScopeRealtimeASRProvider` 同款打法。
- **只在 LISTENING 期推流**。provider `interrupt_response=true` 会自主判打断，SPEAKING 期继续推流
  就与「本侧权威」打架；不推流则它根本没有输入可判。
- 回灌里 **escalated 轮不重复写 memory**（主链已落）、**被打断轮只存已播增量**（provider 的
  `audio_transcript.done` 带完整全文，那≠用户听到的）。

验证：`test/e2e_s2s_probe.py`（协议探针，换厂商重跑）、`test/e2e_s2s.py`（真栈闭环）、
`test/e2e_s2s_resilience.py`（断连重连/降级）、`test/eval_s2s_escalation.py`（移交率门槛 ≥95%）。

## Phase 1 已落地
- `cache.py` — LRU 缓存（messages 哈希 + serving provider + thinking 并入 key，TTL 5min）
- `ratelimit.py` — 令牌桶限流（全局 + 每 key）
- `metrics.py` — 按模型统计 calls/tokens/latency/cost
- `health.py` — 每厂商被动健康滚动窗口（运行时硬化 D5，2026-07-17）

## 后续
- 将 `security/` 内容审核/注入防护钩子接入统一网关请求链。
- proto 已预留 `tools/tool_calls`，Provider 的原生工具调用透传尚未实现；当前确定性工具
  由 Cloud Planner 的 `ToolRegistry` 调度。


## 声纹（`speaker_embed.py`，M4 P4）

设计见 `docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md`，契约登记 `docs/conventions.md` §9.11。

**这一侧只做「音频→192 维向量」，不持有任何模板**——模板存储与比对在 memory 服务
（生物特征扩散到无状态服务就删不干净，而 GDPR 硬删级联是已立的红线）。

**注册与识别必须同信道**（2026-07-27 真机 P0）：`enroll` / `identify` 默认格式都是
`pcm16le`，HMI 三条路（注册 / 「试一试」/ 主链路识别）走同一个采集管线。曾经注册走
webm(opus 有损)、识别走原始 PCM，真机实测同人余弦 **0.73→0.48** 且探针会塌向别人的模板。
`format=webm` 仍受支持（经 ffmpeg 转码），但**不要拿它建模板**。

**每次 identify 打一行 INFO**（occupant/decision/score/runner_up/probe_ms/src），memory 侧
再打一行全量排名——「认不出」是静默降级，没有分数就无从调阈值；obs 那条 metric 指望不上
（collector 的 `apply_metric` 是固定键白名单，`vp_*` 全被丢掉，已立卡）。

三档决议（对齐 §9.4，启动期输出 `provider[voiceprint]=...` 一行）：

| 档 | 触发 | 行为 |
|---|---|---|
| `campplus` | 模型文件在且 sherpa-onnx 可导入 | 真实推理（CAM++ 192 维） |
| `mock` | 显式 `VOICEPRINT_PROVIDER=mock` | 确定性伪向量（离线单测/CI）；严格栈 fail-fast |
| `disabled` | 模型/依赖缺失 | **整面诚实禁用**，`occupant_id` 恒 primary = 逐字回落 P4 之前 |

模型 28MB 且 gitignore：`bash scripts/fetch-voice-models.sh voiceprint-campplus`
（本机实测 GitHub 约 25KB/s、要十几分钟，脚本支持 `curl -C -` 续传，**可重跑**）。
**拉不到不阻塞构建**——`models/voiceprint/.gitkeep` 保证目录存在，Dockerfile 的 `COPY models` 照常。

> 为什么模块叫 `speaker_embed` 而不是 `voiceprint`：`memory/voiceprint.py` 是模板与判定层，
> 两边同名会在跑全量单测时互相劫持 `sys.modules`（本仓库在 providers 通用包名上有前科）。

## 视觉单帧（`vision_frames.py`，M4 P4）

契约登记 `docs/conventions.md` §9.12。HMI 命中视觉触发词时抓一帧 POST 到 `/api/vision/frame`，
拿一个短 TTL 的 `frame_id`；**图像本体只在本进程内存 LRU 里活 120s，不落 Redis 不落盘**
（Redis 会持久化到磁盘=把车内外图像写进存储）。

`Complete/CompleteStream` 见到 `meta.vision_frame_id` 时把**最后一条 user 消息**升级为
OpenAI 多模态 content 数组；**帧过期/不存在则显式 `FAILED_PRECONDITION`**，绝不静默只发文本
——那样 VL 模型会答「看不清，画面有点模糊」，**它在假装看到了一张模糊的图**。

看图走 `llm_runtime` 的**独立 `qwen-vl` 档**（`internal: True`，不进 HMI「AI 大脑」切换列表），
调用方用请求级 pin 指定。**不要指向聊天大脑**：实测 `qwen3.7-max` 对多模态 content 直接 400，
而档位解析对不认识的模型是**静默回落 primary**——指错了不会报错，只会看不见图。
