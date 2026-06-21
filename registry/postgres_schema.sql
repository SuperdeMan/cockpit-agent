-- Registry 持久化 schema（ws2 P0 + P1 语义路由）
-- Agent 注册信息存入 PostgreSQL，Registry 重启后秒恢复。
-- P1 新增 embedding 列（pgvector），支持语义路由。

CREATE TABLE IF NOT EXISTS agents (
    agent_id      VARCHAR(64) PRIMARY KEY,
    manifest      JSONB NOT NULL,
    endpoint      VARCHAR(256) NOT NULL,
    lease_id      VARCHAR(64),
    registered_at TIMESTAMPTZ DEFAULT now(),
    last_heartbeat TIMESTAMPTZ DEFAULT now(),
    status        VARCHAR(16) DEFAULT 'healthy',
    embedding     vector(384)              -- P1: capabilities 向量化，用于语义路由
);

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

-- P1: 向量索引（IVFFlat，适合中等规模数据集）
-- 如果表为空，CREATE INDEX 会失败；需先有数据后重建
-- CREATE INDEX IF NOT EXISTS idx_agents_embedding ON agents
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
