# Agent SDK

让一个 Agent 只需关心"业务逻辑"。gRPC 契约、注册发现、健康检查、LLM/Memory 客户端由 SDK 提供。

## 写一个 Agent 只要两步

**1. manifest.yaml** — 声明能力（见架构文档 §4.3）
```yaml
agent_id: my-agent
version: 0.1.0
category: ecosystem        # core | ecosystem
trust_level: third_party   # system | first_party | third_party
deployment: cloud          # edge | cloud
latency_budget_ms: 2000
fallback: chitchat
capabilities:
  - intent: my.do_something
    description: ...
    slots: [a, b]
    examples: ["示例话术1", "示例话术2"]
requires_permissions: [location.read]
```

**2. 继承 BaseAgent，实现 handle()**
```python
from agents._sdk import BaseAgent, AgentResult, NEED_SLOT

class MyAgent(BaseAgent):
    def __init__(self):
        import os
        super().__init__(os.path.join(os.path.dirname(__file__), "..", "manifest.yaml"))

    async def handle(self, intent, ctx, meta) -> AgentResult:
        if not intent.slots.get("a"):
            return AgentResult(status=NEED_SLOT, follow_up="缺少参数 a")
        data = await ctx.fetch("location")           # 按需取上下文
        reply = await self.llm.complete([...])        # 需要时调 LLM
        return AgentResult(speech=reply).action("navigate", {"to": "x"})
```

启动：`python agents/my-agent/main.py`（SDK 自动注册到 Registry 并起 gRPC server）。

## 关键约束
- **不要**自己实现 gRPC servicer / 注册逻辑——SDK 已封装。
- **不要**在 Agent 内直接操作车控；产出 `action("vehicle.control", ...)` 意图，由端侧 Executor 经 VAL 校验执行。
- `handle_stream` 默认把 handle 结果包成单事件；要流式话术（如闲聊）就重写它。

## 测试
用 `agents/_sdk/testing.py` 的 `run_handle` 直接驱动 `handle`，无需起 server。见各 Agent 的 `tests/`。
