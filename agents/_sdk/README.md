# Agent SDK

让一个 Agent 只需关心"业务逻辑"。gRPC 契约、注册发现、健康检查、LLM/Memory 客户端由 SDK 提供。

## v2 能力契约与后续边界

CA2-05 已接入版本 2 能力契约，声明、准入、版本探测和参数检查见 [实施记录](../../docs/design/2026-09-27-v2-capability-contract.md)。
CA2-08/11 的持久操作与恢复仍待实现。
当前 ledger 的 best-effort 不能直接套用到未来承诺可恢复的副作用工作流；新增写档须先具备可靠落账和对账。
Jev 只通过拟新增的网关 Decide client 访问，Agent 不自行调用供应商或复制权限判据。
详见 [实施方案](../../docs/design/2026-09-26-cockpit-agent-v2-implementation-plan.md)。


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
    effect: read
    contract:
      version: 2
      revision: "1"
      effect: read
      parameters: {a: {type: string}, b: {type: string}}
      additional_parameters: reject
      applicability: {status: not_vehicle_specific, vehicle_models: [], software_versions: []}
      preconditions: [permission, handler]
      idempotency: unknown
      verification: none
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
        return AgentResult(speech=reply)
```

启动：`python agents/my-agent/main.py`（SDK 自动注册到 Registry 并起 gRPC server）。

## 关键约束
- **不要**自己实现 gRPC servicer / 注册逻辑——SDK 已封装。
- **不要**在 Agent 内直接操作车控；产出 `action("vehicle.control", ...)` 意图，由端侧 Executor 经 VAL 校验执行。
- `handle_stream` 默认把 handle 结果包成单事件；要流式话术（如闲聊）就重写它。

## 响应式 Agent：`on_start()` 生命周期钩子（可选）
需要后台循环的 Agent（如订阅 NATS 做主动播报的 `road-safety`）重写 `async def on_start(self)`：
`serve()` 起完 gRPC server 后以后台任务调用一次（fail-open，异常被吞不影响请求-响应服务）。
范本见 `agents/road_safety/src/agent.py`。默认无操作，普通请求-响应 Agent 无需关心。

## 测试
用 `agents/_sdk/testing.py` 的 `run_handle` 直接驱动 `handle`，无需起 server。见各 Agent 的 `tests/`。


## 能力契约 v2

新增能力必须声明 `contract`，旧 156 项兼容指纹只用于迁移，不自动扩充。
旧 `effect` 保留 read/write；细分 `contract.effect` 为 read/information_task/state_change/external_write。
写能力必须声明权限；契约摘要不是授权，不替代权限、确认、VAL 或幂等账本。

`parameters` 约束现有字符串 wire，不隐式换算单位或替 Agent 填槽。缺参仍由 handle 的 NEED_SLOT 返回；
单位或区域空值是未明确，不能当作已验证。涉及车型/软件范围的写能力在可信视图接入前拒绝执行。
`preconditions` 引用原 permission/confirmation/handler/val；`verification` 引用原 Verification。

SDK 在 Execute/ExecuteStream 首个业务事件之前核对契约版本/参数。AgentClient 先读取目标 Describe，
使用目标摘要并剥掉父能力摘要，读版本和执行共享原超时预算，不自动重试写请求。
非公开内部 RPC 保持既有 handler 权威；这个契约不是对内部调用的新增授权。
