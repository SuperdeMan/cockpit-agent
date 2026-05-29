# chitchat Agent (ecosystem)

开放域闲聊 / 情感陪伴 / 简单问答。系统的兜底 fallback。

| intent | 说明 |
|---|---|
| `chitchat.talk` | 闲聊兜底，经 LLM Gateway 生成，支持流式 |

演示了生态 Agent 的两个要点：① 经 SDK 的 `self.llm` 调 LLM Gateway；② 重写 `handle_stream` 实现流式话术（边生成边播报）。
