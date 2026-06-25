# Memory 服务

上下文的唯一真相源：短期会话、车辆上下文、**长期分层语义记忆**。上下文按 scope 取数（隐私最小化）。

2026-06-25 从 mock KV 重构为分层语义记忆，完整设计见
[`docs/design/2026-06-25-memory-system-redesign.md`](../docs/design/2026-06-25-memory-system-redesign.md)。

## 分层
- **L0 会话**：`AppendTurn` / `GetSession`（Redis，连不上自动降级内存）。
- **L1 车辆上下文**：`GetContext(scopes)` 按 scope 返回片段（敏感 scope 脱敏，如 `vehicle.location` 只给城市级）。
- **L2 语义画像**：稳定偏好/个人实体（`taste.*`、`person.pet` 等），带 `predicate`、向量化、`superseded_by` 时序-lite。
- **L3 情景**：显著事件，聚合源。
- **L4 程序记忆**：从 L3 高频行为派生 routine，经 `agent.proactive` 主动建议。

## 接口（见 `proto/cockpit/memory/v1/memory.proto`）
| RPC | 用途 |
|---|---|
| `GetContext` / `AppendTurn` / `GetSession` | 车辆上下文 + 会话短期记忆（`AppendTurn` 带 `user_id` 时每 4 轮触发异步抽取巩固） |
| `UpsertProfile` | 写画像字段（如常用地点 `places`，镜像为 highly_sensitive memory_item） |
| `Remember` | 写语义/情景记忆（抽取管线或 Agent 显式） |
| `Recall` | 语义召回（向量 + scope/occupant + 时序融合；`predicate_prefix` 精确优先，`min_score/min_confidence/max_age_days` 阈值） |
| `ForgetUser` / `ExportUser` | 合规：被遗忘权（硬删）/ 数据导出 |

## 存储与 embedding
- **PostgreSQL + pgvector**：单表 `memory_item`（`schema.sql`，`kind` 区分 semantic/episodic/procedural）。无 `POSTGRES_DSN` 降级纯内存（lexical 召回）。
- **embedding 走 llm-gateway → 阿里云百炼 text-embedding-v4**（1024 维，`EMBED_DIM` 配置）。无 `LLM_EMBED_API_KEY` 时**诚实降级 lexical，绝不哈希伪语义**喂规划。
- 关键文件：`pg_store.py`（向量存储）、`store.py`（门面）、`extract.py`（四分类抽取治理 + PII/坐标黑名单）、`routine.py`（routine 派生）、`server.py`（gRPC）。

## 隐私
- 三档 `privacy_level`：`normal` / `sensitive`（用户主动告知的个人实体，可泛化召回）/ `highly_sensitive`（家/公司精确地址，泛化召回排除、仅 scope/predicate 定向可读）。
- 抽取黑名单：精确坐标、电话/证件号（PII）、第三方隐私、Agent 推断的敏感画像 → 丢弃。

## 测试
- 单点单测：`tests/test_pg_store.py`、`test_store.py`、`test_extract.py`、`test_server_rpc.py`、`test_routine.py`（内存兜底，不连 PG/Redis）。
- 复杂场景集：`tests/test_scenarios.py`（8 例：偏好演化/多乘员隔离/隐私三档/过期/routine/抽取纵深/合规/召回契约）。
- 全栈断言 E2E：`../test/e2e_memory.py`（6 链路，连真栈，自清理可重入）。
