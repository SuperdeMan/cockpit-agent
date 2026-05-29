# LLM Gateway

所有 LLM 调用的唯一出口。屏蔽厂商差异，提供多模型路由与降级。

## 接口（见 proto/cockpit/llm/v1/llm.proto）
- `Complete` 同步补全
- `CompleteStream` 流式补全

## 路由与降级
- 请求未指定 model → 依次尝试 `LLM_MODEL_PRIMARY`、`LLM_MODEL_FALLBACK`，前者失败降级到后者。
- 未配置 `LLM_API_KEY` → 自动用 `MockProvider`，保证 PoC 可离线跑通。

## 扩展厂商
在 `providers.py` 新增 `BaseProvider` 子类并在 `build_provider()` 注册即可。

## 待办
- TODO(Phase1): 缓存 / 限流 / 配额与成本统计 / 内容审核 / 工具调用(tools) 透传。
