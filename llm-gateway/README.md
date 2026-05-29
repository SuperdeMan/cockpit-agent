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
- 未配置 `LLM_API_KEY` → 自动用 `MockProvider`，保证可离线跑通。

## Phase 1 已落地
- `cache.py` — LRU 缓存（messages 哈希，TTL 5min）
- `ratelimit.py` — 令牌桶限流（全局 + 每 key）
- `metrics.py` — 按模型统计 calls/tokens/latency/cost

## 待办
- TODO: 内容审核钩子接入。
- TODO: 工具调用(tools) 透传。
