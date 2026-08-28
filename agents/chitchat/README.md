# chitchat Agent (ecosystem)

开放域闲聊 / 情绪陪伴 / **不随时间变化的常识问答**。系统的兜底 fallback。

| intent | 说明 |
|---|---|
| `chitchat.talk` | 闲聊兜底，经 LLM Gateway 生成，支持流式；slot `depth=deep` 时用 primary 模型（知识/解释类），默认 @fast |

演示了生态 Agent 的两个要点：① 经 SDK 的 `self.llm` 调 LLM Gateway；② 重写 `handle_stream` 实现流式话术（边生成边播报）。

## 时效护栏（2026-07-12 四模式路由与回答质量）

- system prompt 注入**今日日期** + 「实时/近期事实不确定就明说，绝不编造」。
- **escalate 改派**：LLM 判定「必须联网才能答对」时只输出 `<search>搜索词</search>`——agent 零播报、
  经通用协议 `AgentResult.data["_escalate"]` 改派 `info.search`（engine 有界一跳消费，协议见
  `docs/conventions.md` §9.1）。流式路径头部缓冲 ≤8 字符判定标记，普通回复无感。
- 兜底属性使 chitchat 是所有降级路径的落点——误接时效题时上述两层保证不用陈旧知识瞎答。

## 确定性前置（**不问 LLM** 的那几类）

`_deterministic_reply` 是 `handle` 与 `handle_stream` 的**唯一入口**（源码级断言守着——
只在一条路径上加闸等于没加，本仓踩过三次）。命中即零 LLM 直答：墙钟（几点/日期/星期）、
声纹身份（「我是谁」）、安全告警建议、**执行史审计**（「刚才实际执行了什么」）。

⚠ 审计那条的判据与话术 2026-08-28 起住在 `runtime/session_facts.py`（C4-B），
chitchat 只是**兜底位的第二个消费方**：真正的短路在编排层、落域**之前**——
闸建在这里的代价是「别的域接走就够不着」（QA T37 被 planner 接给了 reminder.list）。
改判据去那一份，别在这里加第二套。
