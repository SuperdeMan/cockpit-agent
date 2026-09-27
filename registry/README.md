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
- PostgreSQL 持久化已由 PgStore 实现；当前采用 JSON manifest 往返，数据库不可用时有内存降级。生产可用性与降级边界仍需运维验证。
- 多版本灰度路由（按 vehicle_group / 比例分流）。
- capability 粒度 pgvector 语义路由已实现；多租户隔离与规模验证仍待完善。


## CA2-05 契约准入

Register 校验能力契约 v2；156 项已冻结的旧接口按语义指纹兼容，未知新能力缺声明拒绝。
PgStore 重载不采纳损坏或未审的旧记录；JSON 往返保留 contract，不需要数据库 schema 变更。
ListRequest/ResolveRequest 的 capability_contract_version=2 表示能消费新契约；缺省旧读取方只看兼容能力。
过滤在关键词 top-k / pgvector 聚合之前进行，不能让不可见的新能力挤掉旧能力。
登记存在不证明当前在线或已获调用权限。协议和范围见 [CA2-05](../docs/design/2026-09-27-v2-capability-contract.md)。
