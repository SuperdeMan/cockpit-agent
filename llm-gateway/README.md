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
- `POST /api/asr` 语音识别、`POST /api/tts` 语音合成、`GET /api/voices` 音色列表（经 ASR/TTS Provider）。
- `GET /api/memory/session` / `GET /api/memory/context` 只读记忆（转发 memory gRPC，供 HMI 记忆视图）。
- ASR/TTS Provider 同样在无 `LLM_API_KEY` 时走 mock。

## Phase 1 已落地
- `cache.py` — LRU 缓存（messages 哈希，TTL 5min）
- `ratelimit.py` — 令牌桶限流（全局 + 每 key）
- `metrics.py` — 按模型统计 calls/tokens/latency/cost

## 后续
- 将 `security/` 内容审核/注入防护钩子接入统一网关请求链。
- proto 已预留 `tools/tool_calls`，Provider 的原生工具调用透传尚未实现；当前确定性工具
  由 Cloud Planner 的 `ToolRegistry` 调度。
