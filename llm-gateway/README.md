# LLM Gateway

所有 LLM 调用的唯一出口。屏蔽厂商差异，提供多模型路由与降级。

## 接口（见 proto/cockpit/llm/v1/llm.proto）
- `Complete` 同步补全
- `CompleteStream` 流式补全

## 支持的 Provider
- `xiaomimimo` — 小米 MiMo API（OpenAI 兼容格式）。已验证连通。
- `anthropic` — Anthropic Claude API
- `mock` — 无 key 时的回显兜底

## 路由与降级
- 请求未指定 model → 依次尝试 `LLM_MODEL_PRIMARY`、`LLM_MODEL_FALLBACK`，前者失败降级到后者。
- 调用方可显式指定 model 做**分层**：开放域/闲聊走 `LLM_MODEL_FAST`（低延迟），复杂规划走 `LLM_MODEL_PRIMARY`（见 chitchat Agent 的 model_pref 处理）。
- 未配置 `LLM_API_KEY` → 自动用 `MockProvider`，保证可离线跑通。

## HMI HTTP 代理（http_server.py，端口 50059）
HMI 是浏览器、不能直连 gRPC，故同进程内起一个 CORS 放开的 HTTP 代理：
- `POST /api/asr` 批处理语音识别、`POST /api/tts` 语音合成、`GET /api/voices` 音色列表（经 ASR/TTS Provider）。
- `GET /api/asr/stream`（**WebSocket**）流式识别上屏 + `GET /api/asr/stream/info` 引擎能力探测（见下节）。
- `GET /api/memory/session` / `GET /api/memory/context` 只读记忆（转发 memory gRPC，供 HMI 记忆视图）。
- ASR/TTS Provider 同样在无 `LLM_API_KEY` 时走 mock。

## 流式 ASR（实时识别上屏）
设计见 `docs/design/2026-06-30-asr-streaming-design.md`。WS `/api/asr/stream`：HMI 推音频帧（webm/opus）→ 网关**流式 ffmpeg** 转 PCM16 16k → 引擎 → 回 `{type:partial/final}`。引擎经 `ASR_STREAM_PROVIDER`/请求级覆盖切换（`providers.py` 的 `build_streaming_asr_provider` 工厂**按模型名路由**）：
- **DashScope qwen3**（默认，`qwen3-asr-flash-realtime-2026-02-10`，**id 须全小写**）：OpenAI 兼容 Realtime 协议（`/api-ws/v1/realtime`；base64 音频、`session.update`+`input_audio_buffer.append`、server_vad、`conversation.item.input_audio_transcription.text/.completed`）。
- **DashScope fun-asr / paraformer**（`fun-asr-realtime`）：DashScope **run-task** 协议（`/api-ws/v1/inference`；**二进制音频帧**、`run-task`→`result-generated`→`task-finished`）。与 qwen3 端点/协议不同，工厂自动按 id 路由。
- 两者复用百炼 `LLM_EMBED_API_KEY`（或独立 `DASHSCOPE_ASR_KEY`）。
- **MiMo 分块**（`mimo-chunked` 回退）：累积 PCM 每 ~1.2s 封 WAV 打 MiMo 批 ASR 产伪 partial，无百炼 key 时可用。
- 批处理 `/api/asr` 保留作回退；任一环失败 HMI 无感切回批处理。

## Phase 1 已落地
- `cache.py` — LRU 缓存（messages 哈希，TTL 5min）
- `ratelimit.py` — 令牌桶限流（全局 + 每 key）
- `metrics.py` — 按模型统计 calls/tokens/latency/cost

## 后续
- 将 `security/` 内容审核/注入防护钩子接入统一网关请求链。
- proto 已预留 `tools/tool_calls`，Provider 的原生工具调用透传尚未实现；当前确定性工具
  由 Cloud Planner 的 `ToolRegistry` 调度。
