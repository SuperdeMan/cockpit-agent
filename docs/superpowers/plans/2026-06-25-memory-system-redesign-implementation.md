# 记忆系统分层重构 — 分期实施计划

> **执行状态（2026-06-25）**：**P0-P3 全部已落地**，全量 **835 passed, 6 skipped**，零回归。
> - P0 地基：proto 4 RPC + 单表 `memory_item`(+治理字段) + `pg_store.py`(真模型门控/精确优先/阈值/高敏排除) + SDK remember/recall。
> - P1 自动抽取：`memory/extract.py`(四分类+黑名单) + `store.consolidate`(去重/冲突 supersede) + `server.AppendTurn` 每 4 轮 fire-and-forget 触发 + 编排器透传 user_id。
> - P2 召回注入：`engine._recall` → `planner.build(memory=)` → `_format_memory` 结构化注入 planner prompt（阈值+门控）。
> - P3 主动雏形：`memory/routine.py`(routine 检测) + `store.derive_routines`(写 procedural+建议)。
>
> **后续四项已落地（2026-06-25，全量 839 passed/6 skipped）**：
> 1. **live-PG 验证 ✅**（已授权）：`memory/requirements.txt` 补 `asyncpg`，重建 memory 容器连真实 cockpit 库，`memory_item` 29 列建表成功（含 `vector(384)`/治理字段），gRPC Remember/Recall/Export/Forget + scope 过滤端到端跑通、行落 PG/删后清零。
> 2. **per-Agent `ctx.recall` ✅**：food 点餐前 `ctx.recall(predicate_prefix="taste.")` 取学到口味并入话术（精确读取）；`testing.make_context` 加 `recall` 默认。
> 3. **proactive 投递 ✅**：memory 在 consolidate 后 `derive_routines` 对新 routine 发 `agent.proactive`（复用 road-safety payload，`nats-py`，best-effort）。HMI 投递一跳仍是项目既有待办。
> 4. **places 收敛 ✅**：**memory 侧镜像**（UpsertProfile 写 places → 镜像 `memory_item` 高敏；GetContext 优先新表回退旧 KV；delete 一并清；`migrate_places` 一次性迁移）——**navigation 零触碰，named-places 零回归**。
>
> **仍待接**：① 真实 bge 语义召回需 embedding 模型（建议走 llm-gateway embedding API，对齐 registry 注释，未装时诚实降级 lexical）；② proactive→HMI 投递一跳（全项目既有待办）；③ 全栈 E2E（重建 cloud-planner/navigation 后跑跨轮偏好召回）。评审采纳变更见设计稿 §0。
>
> **状态**：分期执行中（2026-06-25）。设计依据见 [`docs/design/2026-06-25-memory-system-redesign.md`](../../design/2026-06-25-memory-system-redesign.md)，调研依据见 [`docs/research/2026-06-25-cockpit-and-agent-memory-systems.md`](../../research/2026-06-25-cockpit-and-agent-memory-systems.md)。
>
> **For agentic workers:** 推荐用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 逐任务落地。步骤用 `- [ ]` 勾选跟踪。每完成一个 Phase 跑该 Phase 的自检再进下一个。

**Goal**：把 memory 服务从"Redis KV 画像 + mock"升级为"对话→自动抽取→pgvector 语义画像/情景→语义召回→注入"的分层记忆系统，多用户就绪、时序-lite、可遗忘，且零回归既有 4 RPC 与 `profile.places`。

**Architecture**：复用 registry 已验证的 `bge-small-zh` + pgvector 通路（`registry/store.py:152-175`、`registry/postgres_schema.sql`）。memory 服务自己拥有异步抽取（经 llm-gateway，唯一 LLM 出口）。proto 在 `cockpit.memory.v1` 内**追加** RPC（向后兼容）。

**Tech Stack**：Python 3.11、grpcio、asyncpg/psycopg、pgvector、sentence-transformers（可选，hash 兜底）、pytest。

**铁律对齐**：改 proto 先改 `proto/` 再 codegen（不手改 `gen/`）；docker 无卷挂载，改源码必 `--build` 重建 `memory`/`navigation`/`cloud-planner`；红线动作（schema 迁移落到生产 PG、改 .env）先问 @泓舟。

---

## File Structure

- Modify `proto/cockpit/memory/v1/memory.proto` — 追加 `Remember/Recall/ForgetUser/ExportUser` + `MemoryItem` 等 message。
- Create `memory/pg_store.py` — pgvector 存储层（仿 `registry/store.py`：embed + cosine 检索 + supersede + forget/export），内存兜底。
- Create `memory/schema.sql` — `memory_semantic` / `memory_episodic` 建表（含 ivfflat 索引）。
- Modify `memory/store.py` — 旧 4 RPC 路径不动；新增对 `pg_store` 的委托；`export/delete_profile` 扩到全量。
- Modify `memory/server.py` — 实现新 4 RPC；接异步 consolidate。
- Create `memory/extract.py` — 抽取管线（对话→候选事实，调 llm-gateway，mock 可测）。
- Modify `memory/requirements.txt` / `memory/Dockerfile` — pgvector 客户端、可选 embedding 模型、PG env。
- Modify `agents/_sdk/clients.py` — `MemoryClient.remember/recall`。
- Modify `agents/_sdk/base.py` — `Context.recall(...)`；`save_profile` 内部改走 `Remember`（签名不变）。
- Modify `agents/navigation/agent.py`（写 places 处）— `save_profile` 落新表，验证写读闭环。
- Modify `orchestrator/cloud/engine.py` + `orchestrator/cloud/clients.py` — 规划前注入 `Recall`；轮次后触发 consolidate。
- Modify `deploy/docker-compose.yaml` — memory 连 registry 的 Postgres（`POSTGRES_*` env）。
- Tests：`memory/tests/test_pg_store.py`、`memory/tests/test_extract.py`、`agents/_sdk` 与 `orchestrator/cloud` 既有记忆测试扩展。

---

## Phase P0 — 地基：schema + 存储 + Remember/Recall + 合规（无自动抽取）

### Task P0-1：proto 追加新 RPC 并 codegen
- [ ] 在 `memory.proto` 追加 `MemoryItem`、`RememberRequest/Response`、`RecallRequest/Response`、`ForgetUserRequest/Response`、`ExportUserRequest/Response` 与 4 个 rpc（字段见设计稿 §6）。
- [ ] `make proto`（或 `scripts/gen-proto.ps1`）重新生成，确认 `gen/` 无错、旧 stub 不变。

### Task P0-2：建表与 pgvector 存储层 ✅
- [x] 写 `memory/schema.sql`（**单表 `memory_item`**，含评审治理字段 `tenant_id/vehicle_id/memory_level/privacy_level/expires_at/valid_to/embedding_model/source_turn_ids/review_status` + 索引）。
- [x] 写 `memory/pg_store.py`：`_embed`（`bge-small-zh-v1.5`/384，**无真实模型返回 NULL，不做哈希伪语义**）、`remember`、`recall`（精确优先 `predicate_prefix` + 阈值 `min_score/min_confidence/max_age_days` + 高敏默认排除 + `expires_at` 过期过滤 + **真实模型门控**：无模型降级 lexical）、`supersede`、`forget`、`export`。**无 PG 时内存兜底（lexical 召回）**。

### Task P0-3：server 实现新 RPC
- [ ] `server.py` 实现 `Remember/Recall/ForgetUser/ExportUser`，委托 `pg_store`；旧 4 RPC 不动。
- [ ] `ForgetUser` 同时清 Redis `profile/session`；`ExportUser` 汇总 KV + 两表。

### Task P0-4：SDK 写读接口
- [ ] `MemoryClient.remember(items)` / `recall(...)`（含 UNAVAILABLE 重连，仿现有 `get_context`）。
- [ ] `Context.recall(query, *, scopes, kinds, top_k)`；`Context.save_profile` 内部改造为构造 `MemoryItem(kind=semantic, provenance=user_stated)` 调 `Remember`（**签名与返回不变**）。

### Task P0-5：纯增量写读闭环验证（**不动 navigation**）✅
> 评审修订：P0 **不改 navigation/places 主链路**（避免打断已跑通的 named-places）。新能力用独立测试验证；places 迁移整体移到 P1/P1.5。
- [x] `save_profile` 默认行为不变（仍走旧 Redis KV）；`GetContext("profile.places")` 仍读旧 KV。
- [x] 新表写读由 `memory/tests/test_pg_store.py` + `test_server_rpc.py` 直接验证（不经 navigation）。

### Task P0-6：P0 单测 ✅
- [x] `memory/tests/test_pg_store.py`：remember→recall 命中、scope/occupant/kind 过滤、supersede 只取现行、forget/export；**评审新增**：predicate 精确、高敏默认排除、过期过滤、置信度阈值、治理字段默认、语义不可用降级。
- [x] `memory/tests/test_server_rpc.py`：4 RPC 经真实 proto 走通（importlib 唯一名加载避免裸 `server` 与 edge 冲突）。

**P0 DoD / 自检** ✅（2026-06-25 实测）
```
make proto                                  # 已重生成
python -m pytest memory/tests -q            # 27 passed
python -m pytest -q                         # 821 passed, 6 skipped（基线 798，零回归）
```

---

## Phase P1 — 自动抽取与巩固（异步管线）✅ 已落地

### Task P1-1：抽取管线（含治理）
- [ ] `memory/extract.py`：`extract(turns)` 调 llm-gateway 返回结构化候选 `[MemoryItem]`。LLM 不可用→空、静默。
- [ ] **四分类写策略**（设计稿 §7）：explicit_preference 直写高信；temporary_preference 带 `expires_at`；inferred_preference 低置信需多次证据；sensitive_fact 默认不自动写。
- [ ] **抽取黑名单**（服务端二次校验丢弃命中候选）：一次性命令、未确认地址、实时坐标、车内音视频、第三方隐私、歧视/敏感画像。

### Task P1-2：consolidate 触发与冲突处理
- [ ] `server.AppendTurn` 累积计数，达阈值（N=4 轮或显式会话结束）触发 `consolidate(session_id)` 为 fire-and-forget task（不阻塞）。
- [ ] consolidate：候选向量化→同 predicate 现行条目：无则插入；等价则计数+刷新；冲突则旧条 `superseded_by` + 插新（时序-lite）。情景入 `memory_episodic`。
- [ ] Planner DAG JSON 链路不触发抽取（与"思考"同样隔离）。

### Task P1-3：places 开始双写 + 衰减（评审修订）
- [ ] navigation `save_profile` 改为**双写**：旧 Redis KV + 新 `memory_item`（predicate=`place.*`，`privacy_level=highly_sensitive`，**仅用户显式设置写入**）。
- [ ] `GetContext("profile.places")` 改为优先新表、回退旧 KV；**跑 named-places 全量回归**。
- [ ] recall 排序纳入 `last_used_at/use_count` 衰减；命中召回回写 `last_used_at`。

### Task P1.5（稳定后）：旧 KV 迁移收敛
- [ ] 双写稳定后，一次性把旧 `profile.places` KV 迁入 `memory_item`，下线旧读路径。**单独一步，不与 P1 混做**。

### Task P1-4：P1 单测
- [ ] `memory/tests/test_extract.py`：mock LLM 返回候选→去重/等价计数/冲突 supersede 三分支；坐标被过滤。

**P1 DoD / 自检** ✅（`memory/tests/test_extract.py` 7 例 + `test_server_rpc.py` 触发 2 例；全量 830 passed）

---

## Phase P2 — 召回注入（让记忆真正起作用）✅ 已落地（P2-2 增量待接）

### Task P2-1：engine 规划注入
- [ ] `clients.py` 加 `recall(...)` 透传；`engine.py` 规划前（与 `_history` 同位置）调 `Recall(query=本轮文本, top_k=3, 现行高置信)`，拼进 planner system prompt。`memory_enabled=false` 时跳过。

### Task P2-2：Agent ctx.recall 接入
- [ ] food 点餐前 `ctx.recall("口味偏好", scopes=["profile.taste"])`；navigation 取常去地点。择 1-2 个高价值 Agent 接入示范。

### Task P2-3：P2 回归
- [ ] 慢意图/复杂混合意图回归不被注入破坏；新增"跨轮偏好召回"用例（"我不吃辣"→隔轮点餐召回口味）。

**P2 DoD / 自检** ✅ 进程内（`test_engine_context.py::test_memory_injected_into_planner_prompt`；全量 831 passed）。端到端 `make up` E2E 待 live-PG 验证一起做。

---

## Phase P3 — 主动雏形（procedural，最小）✅ 检测/派生已落地（NATS 投递待接）

### Task P3-1：最小 routine 检测
- [ ] 从 `memory_episodic` 聚合"时间+地点+动作"频次，命中阈值产出 1 条 procedural 记忆。
- [ ] 经已有 `agent.proactive` 通道（road-safety 投递一跳样板）发主动建议；行车态门控复用现有过程区双态逻辑。
- [ ] 多乘员仅预留 `occupant_id`，不实装识别。

**P3 DoD** ✅ 检测/派生（`memory/routine.py` + `store.derive_routines`，`test_routine.py` 4 例，全量 835 passed）。**待接**：把 `derive_routines` 产出的 `suggestion` 经 `agent.proactive` NATS 投递（复用 road-safety 样板，项目既有"投递一跳待接"）。

---

## 全程红线检查（每 Phase 提交前）
- [ ] 抽取/召回产物**不含精确坐标/音视频**（家/公司显式设置例外，标 highly_sensitive）；召回**不跨 occupant**；高敏默认不参与泛化召回。
- [ ] 无真实 embedding 模型时**不拿哈希向量当语义召回**喂 planner（降级 lexical 或 predicate 精确）。
- [ ] `ForgetUser` 为**硬删**（GDPR 被遗忘权），不软删。
- [ ] 未碰 `.env`、未对真实 PG 做建表/迁移而未告知 @泓舟。
- [ ] docker 改源码后已 `--build` 重建 `memory`/`navigation`/`cloud-planner`。
- [ ] 全量 `pytest` 不回归；改完即验，不注释报错绕过。
