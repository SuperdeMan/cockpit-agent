# Agent Registry

Agent 的黄页：注册、发现、能力路由。新增 Agent 注册即可被 Planner 路由，编排核心无需改动。

## 接口（见 proto/cockpit/registry/v1/registry.proto）
- `Register` / `Deregister` — Agent 自注册（由 SDK 自动调用）
- `ResolveAgents` — 按 intent 精确 / query 语义 检索候选，带权限过滤
- `ListAgents` — 列举（供 Planner 把全部能力作为"工具"喂给 LLM）

## Phase 1 已落地
- `store.py` — 健康探测（`mark_healthy`/`mark_unhealthy`）+ 自动摘除（连续 3 次失败标记不健康，不再被路由）+ 路由过滤不健康 Agent

## 待办
- TODO(Phase1): PostgreSQL 持久化（当前内存版，重启丢失）。
- TODO(Phase1): 多版本灰度路由（按 vehicle_group / 比例分流）。
- TODO(Phase1): 向量语义路由（capabilities/examples 向量化检索）。
