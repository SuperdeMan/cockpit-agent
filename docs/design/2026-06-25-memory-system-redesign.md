# 设计：记忆系统分层重构（语义画像 + 情景 + 多用户 + 时序-lite）

> **状态**：评审修订 v2 + P0-P3 + 后续四项全落地（2026-06-25，839 passed/6 skipped，零回归；live-PG 已验证，详见 §0.1）
> **交付对象**：后续执行者（人或 AI），按 [`docs/superpowers/plans/2026-06-25-memory-system-redesign-implementation.md`](../superpowers/plans/2026-06-25-memory-system-redesign-implementation.md) 的 checklist 落地。
> **关联**：
> - 调研依据：[`docs/research/2026-06-25-cockpit-and-agent-memory-systems.md`](../research/2026-06-25-cockpit-and-agent-memory-systems.md)
> - 现状代码：`memory/store.py`、`memory/server.py`、`proto/cockpit/memory/v1/memory.proto`
> - 消费方：`orchestrator/cloud/engine.py`（对话记忆/历史注入）、`agents/_sdk/base.py`+`clients.py`（`ctx.fetch/history/save_profile`）
> - 可复用基础设施：`registry/store.py:152-175`（embedding）、`registry/postgres_schema.sql`（pgvector）
> - 既有画像用法：[`docs/design/2026-06-23-named-places.md`](2026-06-23-named-places.md)（`profile.places`）
> - 架构真相源：`docs/architecture/cockpit-agent-architecture.md` §7

---

## 0. 评审修订记录（2026-06-25）

一轮评审后采纳的变更（✅采纳 / ◐缩范围 / ⚠️未采纳并说明）：

| # | 评审意见 | 处置 | 落点 |
|---|---|---|---|
| 1 | P0/P1 places 边界自相矛盾 | ✅ 采纳 P0/P1/P1.5 分级；**代码本就未动 navigation** | §10、plans P0/P1/P1.5 |
| 2 | 新增 SessionState RPC 排 P0 | ⚠️ **未采纳**：会话态已在 `orchestrator/cloud/session.py`（`SessionState`: phase/pending_plan/pending_step_id/missing_slots/ttl）跑通，named-places 正走它；改为**写清 L0 边界**（编排器拥有临时任务态、memory 拥有对话轮次+长期层），不加 memory RPC | §4.1 |
| 3 | 数据模型补 11 个治理字段 | ◐ 采纳高价值子集：`tenant_id/vehicle_id/memory_level/privacy_level/expires_at/valid_to/embedding_model/source_turn_ids/review_status`；**不加** `app_id`（冗余）、`write_policy`（转策略逻辑）、`deleted_at` 软删（坚持 GDPR 硬删） | §5、`memory/schema.sql` |
| 4 | 隐私规则与 places 坐标冲突 | ✅ 全采纳：四级分层；家/公司坐标仅"用户显式设置"可入云、标 `highly_sensitive`、禁自动抽取、默认不参与泛化召回 | §9 |
| 5 | Recall 太粗 | ✅ 全采纳：predicate 精确优先、阈值（min_score/min_confidence/max_age）、结构化注入模板 | §8 |
| 6 | 自动抽取易过度记忆 | ✅ 全采纳进 P1：四分类写策略 + 抽取黑名单 | §7 |
| 7 | hash embedding 不能当语义召回 | ✅ 采纳：无真实模型时 embedding 存 NULL、语义降级 lexical、绝不哈希伪语义喂 planner | §8、`pg_store.py` |

> P0-P3 代码已按上述实现并通过全量回归（零回归）。落地清单见 [实施计划顶部执行状态](../superpowers/plans/2026-06-25-memory-system-redesign-implementation.md)。

### 0.1 后续四项落地（2026-06-25，全量 839 passed/6 skipped）

1. **live-PG 验证 ✅**（已授权建表）：`asyncpg` 补依赖、重建 memory 容器连真实 cockpit 库；`memory_item` 29 列（含 `vector(384)` + 治理字段）建表成功，gRPC Remember/Recall/Export/Forget + scope 过滤端到端验证、行落 PG/删后清零。**真实 bge 语义召回仍需 embedding 模型**（未装→诚实降级 lexical，与 registry 现状一致；建议走 llm-gateway embedding API）。
2. **per-Agent `ctx.recall` ✅**：food 点餐前精确召回口味（`predicate_prefix="taste."`）入话术。
3. **proactive 投递 ✅**：memory 在 consolidate 后派生 routine 并发 `agent.proactive`（复用 road-safety payload）。HMI 投递一跳为项目既有待办。
4. **places 收敛 ✅（关键设计决策）**：采用 **memory 侧镜像**——`UpsertProfile` 写 places 时镜像为 `memory_item`（`place.*`，`highly_sensitive`，supersede-or-insert）；`GetContext("profile.places")` 优先读新表、回退旧 KV；`delete_profile` 一并清镜像；`migrate_places` 一次性迁移。**navigation 一行未改 → named-places 零回归**。这比"改 navigation 双写"更安全，是对评审 Issue 1"零回归"诉求的最优解。

---

## 1. 现状与证据

memory 服务是核心服务里最没进化的一块。代码事实：

| 能力 | 实现 | 证据 |
|---|---|---|
| RPC | 仅 4 个：`GetContext` / `AppendTurn` / `GetSession` / `UpsertProfile` | `proto/cockpit/memory/v1/memory.proto:6-11` |
| 会话短期记忆 | Redis list `sess:{id}`，截断 50 条；内存兜底 | `memory/store.py:45-60` |
| "长期画像" | **一个 Redis JSON blob** `profile:{user_id}`，整份读写 | `memory/store.py:62-83` |
| 画像写入 | **只有 navigation 一个 Agent 在写**（家/公司地点） | `agents/_sdk/base.py:47-54`、named-places 设计 |
| 车辆上下文 | **写死的 mock** | `memory/store.py:18-22` |
| 脱敏 | 位置只给城市级 | `memory/store.py:132-137` |
| 消费 | engine 写轮次 + 读最近 6 轮做指代消解；Agent `ctx.fetch(scopes)` | `engine.py:79-82,381-390`；`_sdk/base.py:38-45` |

**与架构 §7 承诺的差距**：架构说长期层是"云侧画像库（**向量+结构化**）…持久（可遗忘/导出/删除）"。实际：

| 架构承诺 | 实际 | 差距 |
|---|---|---|
| 向量 + 结构化画像库 | Redis KV blob | ❌ 无向量、无语义检索 |
| 从交互学习偏好 | 只有显式手写（仅 places） | ❌ 无自动抽取 |
| 可遗忘/导出/删除 | `export/delete_profile` 存在但只覆盖 KV blob | ⚠️ 半成品 |
| 车辆上下文按需快照 | mock | ❌ 未接真实端侧 |

> 反讽点：Registry 服务**已经**用上 pgvector + `bge-small-zh` 语义检索（`registry/store.py:126-175`），基础设施就在仓库里，唯独 memory 没用。

---

## 2. 问题

1. **学不到东西**：用户说过"我不吃辣""常去星巴克""孩子叫朵朵"——下次全忘。除了 places，没有任何偏好沉淀。
2. **检索原始**：只能按 scope 精确取 KV，或取最近 N 轮原文。无法"语义召回与当前问题相关的历史偏好/事件"。
3. **单用户假设**：`user_id` 单值，无乘员维度。车是多人空间（蔚来"记住每个人喜好"、VehicleMemBench 把多用户列为车载核心差异点）。
4. **无时序**：偏好变化只能覆盖，丢失"曾经如此、现已改变"。
5. **不可主动**：没有 routine/程序记忆，做不了"周一要去星巴克吗"这类主动服务。

---

## 3. 目标 / 非目标

**目标（本轮）**
- 补齐"对话→自动抽取偏好/事件→向量+结构化存储→语义召回→注入"的完整管线（mem0 范式）。
- 数据模型与 scope **多用户就绪**（默认单用户 `occupant_id="primary"`）。
- **时序-lite**：偏好更新走 `superseded_by`（不覆盖旧值）。
- 复用 Registry 的 pgvector + `bge-small-zh` 通路，**零新基础设施**。
- 合规闭环：导出/遗忘扩到全量记忆。
- **零回归**：现有 4 RPC 与 `profile.places` 链路不变。

**非目标（本轮不做，留后期/不做）**
- ❌ 完整时序知识图谱（Zep/Graphiti 全量）——PoC 过度工程，但 schema 留出长成它的形状。
- ❌ 声纹/人脸的乘员身份识别——只留 `occupant_id` 接口，识别交给未来端侧。
- ❌ 跨车型/品牌记忆迁移（吉利"流动记忆"）——记录为愿景，不在本轮。
- ❌ procedural/主动服务**完整**落地——仅预留接口与一个最小 routine PoC（P3）。

---

## 4. 设计总览：分层记忆模型

```
L0 工作/会话记忆    Redis 会话(hot)            —— 已有，保留
L1 车辆上下文       端侧快照/meta 透传          —— 已部分有(电量/定位经 meta)，本轮补齐取数口径
L2 语义画像【新】   结构化+向量(pgvector)      —— 偏好:口味/音乐/路线/舒适/称呼/人物
L3 情景记忆【新】   事件摘要+向量              —— "去过西湖"/"上周在X充电"
L4 程序记忆【P3】   routine 模式               —— 时间+地点+动作 → 主动服务
```

**L0 边界（评审后明确，§4.1）**：L0 拆两半，是**显式决策**不是遗漏——
- **临时会话任务态**（待确认/待补槽/挂起 Plan）**留在编排器** `orchestrator/cloud/session.py`（`SessionState`: phase/pending_plan/pending_step_id/missing_slots/ttl，Redis+90s TTL）。它是热路径每轮读写、与编排器 `Plan` 类型强耦合、90s 即过期的临时态——搬进 memory 会多一跳 RPC 且泄漏编排领域类型。named-places 补槽（"深圳腾讯滨海大厦"→`place_address`）正走它，已有回归。
- **对话轮次 + 长期层**（L2-L4）归 memory 服务。

**横切关注点（贯穿 L2-L4）**
- **多用户 scope**：每条记忆带 `user_id + occupant_id`，召回时按身份过滤（mem0 多 scope 范式）。
- **provenance + confidence**：区分"用户明说"vs"Agent 推断"，带置信度。
- **时序-lite**：`valid_from` + `superseded_by`（新记忆取代旧记忆，旧的标记失效不删）。
- **衰减/遗忘**：`last_used_at` + 命中计数参与召回排序；低显著度旧情景可定期清理；显式 GDPR 删除。
- **隐私端云分割**：精确位置/音视频不进云端记忆，只存抽象（城市、"常去咖啡店"类别）；复用现有脱敏。

---

## 5. 数据模型（PostgreSQL + pgvector，复用 registry 实例）

> **实现注（P0 已落地，2026-06-25）**：实现层把下面两张表**合并为单表 `memory_item`**（`kind` 列区分 semantic/episodic/procedural），因为 proto 的 `MemoryItem` 本就是统一形状，单表让 `Recall` 一次查询跨类型、SQL 减半。type-specific 字段（salience/entities）设为可空。**评审后补治理字段**：`tenant_id`（多车企隔离）、`vehicle_id`+`memory_level`（车级 vs 用户级偏好）、`privacy_level`（normal/sensitive/highly_sensitive）、`expires_at`（临时偏好）、`valid_to`（时序查询）、`embedding_model`（换模型重算）、`source_turn_ids`（抽取证据）、`review_status`（生命周期）。真相源以 **`memory/schema.sql`** 为准；下文两表模型保留作概念说明。

新增两张表（与 registry 同一 Postgres 实例，独立表）。embedding 维度、模型、hash 兜底**完全照搬** `registry/store.py:152-175`（`bge-small-zh-v1.5`，384 维，无模型时 hash 兜底）。

```sql
-- L2 语义画像：一条 = 一个可独立失效的偏好/事实
CREATE TABLE IF NOT EXISTS memory_semantic (
    id            TEXT PRIMARY KEY,           -- uuid
    user_id       TEXT NOT NULL,
    occupant_id   TEXT NOT NULL DEFAULT 'primary',
    predicate     TEXT NOT NULL,              -- 谓词/键: "taste.spicy","place.home","person.child"
    value_json    JSONB,                      -- 结构化值
    text          TEXT NOT NULL,              -- 自然语言陈述(嵌入源): "用户不吃辣"
    embedding     vector(384),
    provenance    TEXT NOT NULL DEFAULT 'user_stated', -- user_stated | agent_inferred
    confidence    REAL NOT NULL DEFAULT 1.0,
    scope         TEXT NOT NULL,              -- 隐私/权限: "profile.taste"
    valid_from    BIGINT NOT NULL,            -- epoch
    superseded_by TEXT,                       -- 被哪条取代(NULL=现行)
    last_used_at  BIGINT,
    use_count     INT NOT NULL DEFAULT 0,
    source_session TEXT,
    created_at    BIGINT NOT NULL
);
-- L3 情景记忆：一条 = 一个显著事件
CREATE TABLE IF NOT EXISTS memory_episodic (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    occupant_id   TEXT NOT NULL DEFAULT 'primary',
    summary       TEXT NOT NULL,              -- "在杭州西湖玩了一下午"
    embedding     vector(384),
    entities      JSONB,                      -- ["西湖","杭州"]
    event_ts      BIGINT NOT NULL,
    salience      REAL NOT NULL DEFAULT 0.5,
    scope         TEXT NOT NULL DEFAULT 'episodic.general',
    source_session TEXT,
    created_at    BIGINT NOT NULL
);
-- 索引：pgvector ivfflat（照搬 registry 注释做法）+ 常用过滤列
CREATE INDEX IF NOT EXISTS idx_sem_user ON memory_semantic (user_id, occupant_id, superseded_by);
CREATE INDEX IF NOT EXISTS idx_epi_user ON memory_episodic (user_id, occupant_id);
```

**与现有存储的关系（迁移策略，保证零回归）**
- L0 会话：**不动**，继续 Redis。
- `profile.places`（家/公司）：**P0 不迁**，navigation 链路完全不变；P1 起新写入双写到 `memory_semantic`（predicate=`place.home`），`GetContext("profile.places")` 内部改为优先读新表、回退旧 KV。逐步收敛，零回归。
- 车辆上下文 mock：本轮只规范取数口径（L1），真实端侧快照接入仍按 AGENTS.md 待办，不在本设计强求。

---

## 6. 接口（proto 演进，向后兼容）

`cockpit.memory.v1` **保留 4 个旧 RPC 不变**，新增 4 个（gRPC 加 RPC/message 向后兼容）：

```proto
rpc Remember (RememberRequest) returns (RememberResponse); // 写语义/情景记忆(抽取管线或Agent显式)
rpc Recall   (RecallRequest)   returns (RecallResponse);   // 语义召回(向量+scope+时序融合)
rpc ForgetUser (ForgetUserRequest) returns (ForgetUserResponse); // 合规:删全量记忆
rpc ExportUser (ExportUserRequest) returns (ExportUserResponse); // 合规:导出全量记忆

message MemoryItem {
  string id = 1; string kind = 2;            // "semantic"|"episodic"|"procedural"
  string user_id = 3; string occupant_id = 4;
  string predicate = 5; string text = 6; string value_json = 7;
  string provenance = 8; float confidence = 9; string scope = 10;
  int64 valid_from = 11; string superseded_by = 12;
  int64 source_ts = 13; string source_session = 14;
}
message RememberRequest { repeated MemoryItem items = 1; }
message RememberResponse { repeated string ids = 1; bool ok = 2; }
message RecallRequest {
  string user_id = 1; string occupant_id = 2;
  string query = 3; repeated string scopes = 4; repeated string kinds = 5;
  uint32 top_k = 6; bool include_superseded = 7;
}
message RecallResponse { repeated MemoryItem items = 1; repeated float scores = 2; }
message ForgetUserRequest { string user_id = 1; string occupant_id = 2; repeated string scopes = 3; }
message ForgetUserResponse { bool ok = 1; uint32 deleted = 2; }
message ExportUserRequest { string user_id = 1; }
message ExportUserResponse { string json = 1; }
```

> 不开 v2：v1 内追加 RPC 不破坏既有 stub；`memory`、`navigation`、`cloud-planner` 重新 codegen 即可（均 import memory_pb2）。
>
> **字段完整集以 `proto/cockpit/memory/v1/memory.proto` 为准**：评审后 `MemoryItem` 增 `tenant_id/vehicle_id/memory_level/embedding_model/privacy_level/valid_to/expires_at/review_status/source_turn_ids`；`RecallRequest` 增 `predicate_prefix/min_score/min_confidence/max_age_days`（见 §8）。

**scope 命名**（延续 `<resource>.<action/sub>`）：`profile.taste` `profile.music` `profile.route` `profile.comfort` `profile.persona` `profile.person` `episodic.general`。

---

## 7. 抽取与巩固管线（异步，P1）

mem0 范式，**memory 服务自己拥有抽取**（"上下文唯一真相源"原则），经 llm-gateway（唯一 LLM 出口）：

```
AppendTurn 累积 → 达阈值(每 N=4 轮或会话结束) → 触发异步 consolidate(session_id)
  consolidate:
    1. 取该会话最近未抽取轮次
    2. llm-gateway 抽取候选事实/事件(结构化JSON, 含 predicate/text/provenance/confidence/scope)
    3. 对每条候选: 向量化 → 在 memory_semantic 找同 predicate 现行条目
         - 无 → 插入(新偏好)
         - 有且语义等价 → 命中计数+刷新 last_used
         - 有但冲突(值变了) → 旧条目 superseded_by=新id, 插入新条目(时序-lite)
    4. 情景事件 → memory_episodic
```

要点：① **异步、不阻塞回复**（fire-and-forget task 或 NATS 触发）；② Planner 的 DAG JSON 调用**不**走抽取（与"思考"一样，结构化链路不污染）；③ LLM 不可用时整条管线静默跳过（与现有 best-effort 一致）。

**抽取治理（评审后补，防过度记忆）**——抽取结果按四类分别处置：

| 类别 | 例子 | 写入策略 |
|---|---|---|
| explicit_preference | "以后导航别走高速" | 可直接写，confidence 高，`review_status=user_confirmed` |
| temporary_preference | "今天别走高速" | 只写短期，带 `expires_at`（当日/数小时） |
| inferred_preference | 多次把空调调到 23℃ | 低置信 `agent_inferred`，需**多次证据**累积才升信 |
| sensitive_fact | 家、公司、孩子姓名、联系人 | **默认不自动写**，除非用户明确设置（`highly_sensitive`） |

**抽取黑名单（绝不抽取）**：一次性命令、未确认的地址、实时坐标、车内音视频内容、第三方隐私、可能引发歧视/敏感画像的内容。抽取 prompt 硬约束 + 服务端二次校验（命中黑名单的候选直接丢弃）。

---

## 8. 检索与注入（评审后细化）

**召回三策略**（`Recall`）：
1. **精确画像优先**：传 `predicate_prefix`（如 `place.` `taste.`）或 `scope` 时**按谓词/scope 精确过滤，不先走向量**——读 `profile.places.company` 这种该确定性命中，不该模糊检索。结构化读仍可继续走 `GetContext`。
2. **语义召回带阈值**：`min_score` / `min_confidence` / `max_age_days` / `top_k` 过滤，避免低相关记忆污染 planner；`expires_at` 到期不召回；`highly_sensitive` 默认排除（除非定向）。
3. **真实模型门控**：语义向量召回**仅当有真实 embedding 模型**；无模型时降级 lexical 关键词召回，**绝不拿哈希向量当语义检索**（见 §13 风险）。

**注入点**（P2）——结构化、不直接拼长文本：
```
已知用户记忆：
- [profile.taste | confidence=0.92 | user_stated] 用户不吃辣
- [profile.route | confidence=0.80 | agent_inferred] 用户倾向少走高速
使用规则：① 仅在与当前任务相关时使用；② 不向用户暴露 confidence；③ 高风险动作仍需确认。
```
- 模板固定、有 `max_injected_chars`(~400) 上限、阈值过滤（`top_k=3`/`min_confidence=0.5`/仅 `kinds=["semantic"]`）、失败降级（召回失败不阻塞规划）。
- engine 规划前在 `_history()` 同位置把记忆注入 **planner prompt**（`planning._format_memory`）；planner 仍输出严格 DAG JSON，thinking 对 planner 恒关（与现状一致）。`memory_enabled=false` 时跳过。
- Agent 侧 `ctx.recall(query, scopes, kinds, predicate_prefix, min_*)` 已就绪（P0），按需逐 Agent 接入（如 food 点餐前取 `profile.taste`）；engine 注入已惠及所有 Agent 的规划。
- 后期可加 BM25 + 实体融合 + 重排（mem0），**非本轮**。

---

## 9. 隐私 / 合规 / 端云分割（评审后重写：不一刀切，分级）

旧表述"精确位置一律不进云端"与 named-places 存 lat/lng 自相矛盾。改为**按数据类型分级**：

| 类型 | 能否入云端 memory | 策略 |
|---|---|---|
| 实时 GPS | ❌ 不入 | 端侧快照或粗城市级；抽取禁落坐标 |
| **家/公司常用地点** | ✅ 但**必须用户显式设置** | `privacy_level=highly_sensitive`、**禁自动抽取**、可导出可删除、**默认不参与泛化召回**（仅 `predicate_prefix=place.`/`scope=profile.places` 定向读取时返回） |
| 常去咖啡店/商场 | ◐ 可自动推断但低置信 | **不存精确坐标**，只存 POI 名/城市/类别，`privacy_level=sensitive` |
| 行车轨迹 | ❌ 不入 memory | 走专门轨迹系统，默认不接入 Agent 记忆 |

准确表述：**实时 GPS 与自动抽取的位置不进云端记忆；用户显式设置的家/公司等常用地点可进入，但标高敏、可导出可删除、默认不参与泛化召回。**

- **可遗忘/导出（GDPR 硬删）**：`ForgetUser` **物理删除** `memory_item + Redis profile/会话`（不软删，"被遗忘权"要求不可再检索）；`ExportUser` 汇总导出。扩展现有 `delete_profile/export_profile`。
- **多用户隔离**：召回严格按 `occupant_id` 过滤，避免把驾驶员偏好泄给乘客 session（即便 PoC 单用户也按此口径写代码）。
- **provenance + review_status 可审计**：`agent_inferred` 与 `user_stated` 分开存；`review_status`(auto_extracted/user_confirmed/rejected/corrected) 记生命周期；主动服务/高风险动作只信高置信 `user_stated`。

---

## 10. 与现有系统接线

| 组件 | 改动 | 回归风险 |
|---|---|---|
| `proto/` | 加 4 RPC + messages，codegen | 低（追加式） |
| `memory/` | 新增 `pg_store.py`（pgvector，仿 registry）；`server.py` 实现 4 RPC + 异步 consolidate；旧 4 RPC 不动 | 低（旧路径不变） |
| `agents/_sdk` | `MemoryClient.remember/recall`；`Context.recall(...)`；`save_profile` 内部改走 `Remember`（保留签名） | 低（签名兼容） |
| `orchestrator/cloud/engine.py` | 规划前注入 `Recall` 结果；轮次写入后触发 consolidate（gated on `memory_enabled`） | 中（注入影响 prompt，需回归慢意图） |
| `deploy` | memory 容器加 Postgres 依赖与 env（复用 registry 的 PG）；可选打包 embedding 模型 | 低 |
| HMI 记忆视图 | 可选：展示/删除"已记住的偏好"（合规可视化） | 低（增量） |

---

## 11. 分阶段落地（概览，详细 checklist 见 plans 文档）

- **P0 地基**：proto 4 RPC + `memory_semantic/episodic` 表 + pgvector store（仿 registry）+ `Remember/Recall/ForgetUser/ExportUser` + SDK `remember/recall` + 单测。**navigation `save_profile` 改走 Remember**（验证写读闭环）。
- **P1 自动抽取**：异步 consolidate 管线（memory↔llm-gateway）+ 去重/supersede/衰减 + `profile.places` 双写收敛 + 抽取单测（mock LLM）。
- **P2 召回注入**：engine 规划注入 `Recall` + Agent `ctx.recall` 接入（food 取口味、navigation 取常去地点）+ 端到端回归。
- **P3 主动雏形**：最小 routine 检测（时间+地点+动作频次）→ 经已有 `agent.proactive` 通道发主动建议（复用 road-safety 投递一跳）；多乘员仍预留不实装。

---

## 12. 验收

- **单测**：pgvector store 写/召回/supersede/forget（Postgres + 内存兜底两条路径）；抽取管线（mock LLM 返回候选→去重/冲突 supersede）；SDK remember/recall；engine 注入不破坏既有慢意图回归。全量 `pytest` 不回归（当前 798 passed 基线）。
- **端到端**（重建后人工）：①"我不吃辣"→隔轮点餐 Agent 召回到口味；②"把家设成X"→走新表→"导航回家"直达（零回归 named-places）；③ 改偏好→旧条目 superseded、召回只给现行；④ `ForgetUser` 后召回为空、导出可见。
- **隐私**：抽取产物不含精确坐标；召回不跨 occupant。

---

## 13. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 抽取质量参差（LLM 抽错偏好/过度记忆） | provenance+confidence 分级；抽取 prompt 严格约束"只抽稳定偏好"；主动服务只信高置信 user_stated；HMI 可删 |
| 注入抬高规划 token/时延 | top_k 小（如 3）、只注现行高置信；Planner JSON 链路不注入；与"思考"一样按 `is_complex` 选择性注入 |
| 隐私越界（坐标/音视频落库） | 端云分割硬约束 + 脱敏 + 抽取 prompt 禁令 + 评审 checklist |
| embedding 模型镜像体积/启动慢 | 与 registry 同款 `bge-small-zh`；无模型时 hash 兜底（功能降级不阻断），生产再换 llm-gateway embedding API |
| 过度工程 | 明确非目标：不上完整时序图谱、不做声纹、多乘员只预留 |

---

## 14. 影响面与重建

改 `proto/` → `make proto` → 重建 `memory`/`navigation`/`cloud-planner`（均 import memory_pb2，docker 无卷挂载须 `--build`）。memory 容器需连 Postgres（复用 registry 的 `POSTGRES_*` env）。详见 plans 文档逐项 checklist 与自检命令。
