# WS2 Registry 生产化详细设计

- **状态**：草案（2026-06-20）
- **交付对象**：Registry / 平台开发者
- **关联代码**：`registry/`（当前实现）、`proto/cockpit/registry/v1/registry.proto`、`agents/_sdk/server.py`（周期重注册）、`agents/_sdk/agent_client.py`（endpoint 解析）
- **关联文档**：`docs/architecture/cockpit-agent-architecture.md`、`docs/architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md` §4.4

---

## 1. 现状与证据

- **当前 Registry**：`registry/` 是内存注册表（Go），Agent 启动时 `Register(manifest, endpoint)`，lease 机制 + 周期重注册自愈（`server.py:103-114`，默认 10s）。**无持久化**——重启后所有注册丢失，靠 Agent 周期重注册在一个周期内补回。
- **路由方式**：`ResolveAgents(intent, query, top_k)` 按关键词匹配 manifest.capabilities（精确匹配 intent 前缀），**无语义路由**。
- **AgentClient endpoint 解析**：`agent_client.py:120-124` 硬编码 port_map + `<AGENT_ID>_ENDPOINT` env，**不经 Registry 动态解析**。
- **测试**：`test/sdk/test_reregister.py`（验证重注册自愈）。

## 2. 问题

1. **重启数据丢失**：Registry 重启后所有 Agent 注册信息清零，靠周期重注册自愈有 10s 窗口期。
2. **单实例瓶颈**：无多实例扩展方案，单点故障。
3. **路由精度低**：纯关键词匹配——"找个吃饭的地方"无法路由到 `food-ordering`（需语义理解）。
4. **AgentClient 不经 Registry**：硬编码 port_map → 新增 Agent 必须手动更新 port_map + compose env。

## 3. 目标

1. Registry 注册信息持久化（PostgreSQL），重启秒恢复。
2. 支持多实例部署（Redis 分布式锁 / 一致性哈希）。
3. `ResolveAgents` 语义路由（capabilities/examples 向量化 + pgvector 相似度检索）。
4. AgentClient 经 Registry 动态解析 endpoint（去掉硬编码 port_map）。

## 4. 方案

### 4.1 持久化（P0）

**数据模型**（PostgreSQL）：
```sql
CREATE TABLE agents (
    agent_id    VARCHAR(64) PRIMARY KEY,
    manifest    JSONB NOT NULL,           -- 完整 manifest（含 capabilities）
    endpoint    VARCHAR(256) NOT NULL,
    lease_id    VARCHAR(64),
    registered_at TIMESTAMPTZ DEFAULT now(),
    last_heartbeat TIMESTAMPTZ DEFAULT now(),
    status      VARCHAR(16) DEFAULT 'healthy'  -- healthy / unhealthy / draining
);
CREATE INDEX idx_agents_status ON agents(status);
```

**流程变更**：
- `Register` → UPSERT 到 PostgreSQL（幂等，同 agent_id 更新 endpoint + heartbeat）
- `ResolveAgents` → 从 PostgreSQL 读（加内存缓存，TTL 5s）
- 心跳：Agent 周期重注册时更新 `last_heartbeat`；Registry 后台协程扫 `last_heartbeat > 30s` 的标 `unhealthy`
- **重启恢复**：从 PostgreSQL 加载全量注册表到内存 → 0 窗口期

### 4.2 多实例（P1）

**方案**：每个 Registry 实例独立服务，共享同一个 PostgreSQL。
- 注册写入：PostgreSQL UPSERT 天然幂等，多实例写同一行安全。
- 路由读取：各实例本地缓存 + TTL，最终一致（5s 延迟可接受）。
- **无需 Redis 分布式锁**——PostgreSQL 的 UPSERT + 行锁足够。

**部署**：`deploy/docker-compose.yaml` 的 registry 服务改为 `deploy: replicas: 2`（或 Helm replicas）。

### 4.3 语义路由（P1）

**方案**：capabilities/examples 向量化 + pgvector 相似度检索。

```sql
ALTER TABLE agents ADD COLUMN embedding vector(384);  -- 或 768/1536 取决于模型

CREATE INDEX idx_agents_embedding ON agents
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
```

**向量化时机**：
- Agent 注册时，把 `manifest.capabilities[].intent + description + examples` 拼成文本，经 embedding 模型（本地小模型如 `bge-small-zh` 或 LLM Gateway）向量化，存入 `embedding` 字段。
- `ResolveAgents(query)` → query 向量化 → pgvector cosine 检索 top_k → 返回。

**渐进策略**：
- Phase 1 先用本地 `bge-small-zh`（无外部依赖，延迟 <10ms）
- Phase 2 可升级为 LLM Gateway embedding API

### 4.4 AgentClient 动态解析（P0）

**方案**：`AgentClient._resolve_endpoint` 改为经 Registry 的 `ResolveAgents` 获取 endpoint，port_map 作为 fallback。

```python
async def _resolve_endpoint(self, agent_id: str) -> str:
    # 1. 优先 <AGENT_ID>_ENDPOINT env（本地调试用）
    env_key = f"{agent_id.upper().replace('-', '_')}_ENDPOINT"
    if os.getenv(env_key):
        return os.getenv(env_key)
    # 2. 经 Registry 解析
    try:
        agents = await self._registry.resolve(intent="", query="", top_k=10)
        for a in agents:
            if a.agent_id == agent_id:
                return a.endpoint
    except Exception:
        pass
    # 3. Fallback: 硬编码 port_map（PoC 兜底）
    return _PORT_MAP.get(agent_id, "")
```

> 注：需要给 `AgentClient` 注入 `RegistryClient`（从 `BaseAgent` 传入）。`BaseAgent.__init__` 新增 `self.registry = RegistryClient()`。

## 5. 分阶段落地

| 阶段 | 内容 | 改动范围 |
|---|---|---|
| **P0** | PostgreSQL 持久化 + AgentClient 经 Registry 解析 | `registry/` + `agent_client.py` + `base.py` + compose |
| **P1** | 多实例 + 语义路由（pgvector） | `registry/` + PostgreSQL schema + embedding |
| **P2** | 灰度路由（版本 + 比例分流） | `registry/` + manifest 扩展 |

## 6. 验收

- [ ] Registry 重启后 ≤1s 所有 Agent 可路由（PostgreSQL 恢复，无需等周期重注册）
- [ ] 杀掉一个 Agent 容器 → ≤30s 自动标 unhealthy → 不被路由
- [ ] AgentClient 调用 info agent（50067）不经硬编码 port_map 成功
- [ ] 语义路由："找个吃饭的地方" → food-ordering（非精确匹配）
- [ ] 多实例：2 个 Registry 实例同时运行，注册/路由正常
- [ ] `pytest` 全绿 + `smoke_edge.py` 13/13

## 7. 风险

- **PostgreSQL 依赖**：当前 PoC 可无 PG 运行（内存模式）。→ 保留内存 fallback，PG 可选。
- **embedding 模型体积**：`bge-small-zh` ~100MB，需打进 Registry 镜像。→ 用远程 embedding API 可避免，但增加延迟。
- **语义路由误匹配**：embedding 相似度不等于意图匹配。→ 保留关键词精确匹配作为 first pass，embedding 作为 second pass。
