# Agent Registry

Agent 的黄页：注册、发现、能力路由。新增 Agent 注册即可被 Planner 路由，编排核心无需改动。

## 接口（见 proto/cockpit/registry/v1/registry.proto）
- `Register` / `Deregister` — Agent 自注册（由 SDK 自动调用）
- `ResolveAgents` — 按 intent 精确 / query 语义 检索候选，带权限过滤
- `ListAgents` — 列举（供 Planner 把全部能力作为"工具"喂给 LLM）

## Phase 1 已落地
- 每 5 秒主动探测 Agent gRPC endpoint；连续 3 次失败自动摘除，恢复后重新参与路由。
- `tool://`、`edge://` 虚拟端点不做 gRPC 探测，避免误判。
- 健康快照经 NATS `obs.agent.health` best-effort 发出，供 collector/Dashboard 展示。

## 后续量产项
- PostgreSQL 持久化（当前内存版，重启丢失）。
- 多版本灰度路由（按 vehicle_group / 比例分流）。
- 向量语义路由（capabilities/examples 向量化检索）。
