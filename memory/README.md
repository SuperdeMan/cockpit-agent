# Memory 服务

上下文的唯一真相源：短期会话、车辆上下文、长期画像。上下文按 scope 取数（隐私最小化）。

## 接口（见 proto/cockpit/memory/v1/memory.proto）
- `GetContext(scopes)` — 按需返回上下文片段（scope -> JSON）
- `AppendTurn` / `GetSession` — 会话短期记忆（Redis，连不上自动降级内存）

## Phase 1 已落地
- 画像管理：`export_profile`（导出）、`delete_profile`（删除，合规）、`update_profile`（更新）
- 敏感 scope 脱敏：`vehicle.location` 上云只给城市级，不给精确位置
- 用户画像优先从 `_profiles` 取，兜底 mock

## scope 约定
`vehicle.location` `vehicle.state` `profile.taste` …

## 待办
- TODO(Phase1): vehicle.* 接真实车辆状态服务（当前 mock）。
- TODO(Phase1): profile.* 接画像库（结构化 + 向量，当前内存）。
- TODO(Phase1): PostgreSQL + pgvector 持久化。
