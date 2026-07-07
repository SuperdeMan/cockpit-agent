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
| `mock` | 无任何 key 时的回显兜底 | — | — | — | — | mock |

## 路由与降级
- 未指定 model / 传空 → active provider 的 `primary`；传档位哨兵 `@fast` → active 的 `fast`（chitchat 按
  `model_pref` 传哨兵，**不传具体模型名**，避免切厂商后把 A 家模型名误发给 B 家）；传当前 provider
  不认识的具体模型名 → 回落 primary（防御）。primary 失败降级到 fast。
- **embedding 解耦**：`Embed` 走独立 `embed_provider()`（按 `LLM_EMBED_*` 配 DashScope 百炼），**与 active
  chat provider 无关**——切到无 embedding 能力的厂商（DeepSeek/MiniMax）也不影响记忆语义召回。
- 未配置任何 chat key → `MockProvider`，保证可离线跑通。
- 切换是网关**内存态**（重启回落 `LLM_PROVIDER` env 默认；HMI 启动时把本地存的选择重放回网关兜底）。
  多实例部署需外置到 Redis（本 PoC 未做）。

## HMI HTTP 代理（http_server.py，端口 50059）
HMI 是浏览器、不能直连 gRPC，故同进程内起一个 CORS 放开的 HTTP 代理：
- `POST /api/asr` 批处理语音识别、`POST /api/tts` 批处理合成、`GET /api/voices`(可带 `?provider=cosyvoice|qwen|mimo`) 音色列表（经 ASR/TTS Provider）。
- `GET /api/asr/stream`（**WebSocket**）流式识别上屏 + `GET /api/asr/stream/info` 引擎能力探测（见下节）。
- `GET /api/tts/stream`（**WebSocket**）服务端流式 TTS + `GET /api/tts/stream/info` 引擎+音色+可用性探测（见下节）。
- `GET /api/llm/providers` 列出已装配的 LLM 厂商+模型+可用性+当前 active（供 HMI 设置页两级选择）；`POST /api/llm/provider` `{provider,model?}` 全局切换 active 厂商/模型。
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
- **MiniMax T2A 流式**（`minimax`）：`/v1/t2a_v2` `stream:true`，SSE `data.audio`（hex）解码为 PCM@24k。复用 `MINIMAX_API_KEY`（与 MiniMax LLM 同 key）。**注意**：T2A 流式末尾会发一个 `status:2` 汇总帧把整段音频重发一次——须跳过（已有增量时）否则双份播放。
- mimo/minimax 的 TTS API 是「整段文本一次入」，靠 `providers._sentence_segments` 句级切分逐段流式合成、边说边播。
- 无对应 key → 工厂返 None → `stream/info` 报 unavailable → HMI 无感回退批处理 `/api/tts`。`mock` 引擎产静音分片供 nightly/无 key 验证协议。
- HMI 侧 `pcmPlayer.mjs` 无缝拼播、`cancel`/断连传播到供应商任务取消（barge-in）；`STREAMING_TTS_PROVIDERS`（`hmi/src/audio.ts`）须与本节引擎清单一致，否则漏配的引擎会误走批处理。

## Phase 1 已落地
- `cache.py` — LRU 缓存（messages 哈希，TTL 5min）
- `ratelimit.py` — 令牌桶限流（全局 + 每 key）
- `metrics.py` — 按模型统计 calls/tokens/latency/cost

## 后续
- 将 `security/` 内容审核/注入防护钩子接入统一网关请求链。
- proto 已预留 `tools/tool_calls`，Provider 的原生工具调用透传尚未实现；当前确定性工具
  由 Cloud Planner 的 `ToolRegistry` 调度。
