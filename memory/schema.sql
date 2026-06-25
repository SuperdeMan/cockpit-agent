-- 分层记忆：语义画像 / 情景记忆 / 程序记忆 统一单表（2026-06-25 重构，评审后补治理字段）。
-- 与 registry 同一 PostgreSQL 实例、独立表，不触碰 agents 表。
-- 设计见 docs/design/2026-06-25-memory-system-redesign.md（两表合并为单表，kind 区分）。

CREATE TABLE IF NOT EXISTS memory_item (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL DEFAULT 'semantic',   -- semantic | episodic | procedural
    tenant_id      TEXT NOT NULL DEFAULT 'default',    -- 多车企/多环境隔离（对齐统一品牌ID趋势）
    user_id        TEXT NOT NULL,
    occupant_id    TEXT NOT NULL DEFAULT 'primary',    -- 多用户维度
    vehicle_id     TEXT NOT NULL DEFAULT '',           -- 车级偏好（座椅/氛围灯/空调）用；用户级留空
    memory_level   TEXT NOT NULL DEFAULT 'user',       -- user | vehicle | occupant | session
    predicate      TEXT,                               -- 语义谓词/键，如 taste.spicy；情景留空
    text           TEXT NOT NULL,                      -- 自然语言陈述/事件摘要（向量化源）
    value_json     JSONB,                              -- 结构化值（可空）
    embedding      vector(384),                        -- 仅真实模型时非空；无模型时 NULL（不做哈希伪语义）
    embedding_model TEXT NOT NULL DEFAULT '',          -- 向量来源模型（换模型时据此重算）
    provenance     TEXT NOT NULL DEFAULT 'user_stated',-- user_stated | agent_inferred
    confidence     REAL NOT NULL DEFAULT 1.0,
    review_status  TEXT NOT NULL DEFAULT 'user_confirmed', -- auto_extracted|user_confirmed|rejected|corrected
    scope          TEXT NOT NULL DEFAULT '',           -- 隐私/权限 scope
    privacy_level  TEXT NOT NULL DEFAULT 'normal',     -- normal | sensitive | highly_sensitive
    valid_from     BIGINT NOT NULL DEFAULT 0,          -- epoch 秒
    valid_to       BIGINT NOT NULL DEFAULT 0,          -- 0=至今；配合 superseded_by 做时序查询
    expires_at     BIGINT NOT NULL DEFAULT 0,          -- 0=不过期；临时偏好（"今天别走高速"）设此
    superseded_by  TEXT,                               -- 被哪条取代（时序-lite，NULL=现行）
    source_turn_ids TEXT NOT NULL DEFAULT '',          -- 抽取证据轮次（逗号分隔），可追溯纠错
    last_used_at   BIGINT,
    use_count      INT NOT NULL DEFAULT 0,
    salience       REAL NOT NULL DEFAULT 0.5,          -- 情景显著度
    entities       JSONB,                              -- 情景实体（["西湖","杭州"]）
    source_ts      BIGINT,
    source_session TEXT,
    created_at     BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mem_user ON memory_item (tenant_id, user_id, occupant_id, superseded_by);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memory_item (kind);
CREATE INDEX IF NOT EXISTS idx_mem_predicate ON memory_item (user_id, predicate);
-- pgvector ivfflat（与 registry 一致，需先有数据再建索引才高效；PoC 数据量小可暂不建）
-- CREATE INDEX IF NOT EXISTS idx_mem_embedding ON memory_item
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
