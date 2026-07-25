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

-- M2 记忆图谱 P0（2026-07-25）：偏好加权与衰减。**加列不建 preference 新表**——字段级对照后
-- 真缺口只有下面三个，其余（predicate/confidence/source_turn_ids/superseded_by/privacy_level）
-- 本表全都有；建表会推翻上面那条「两表合并为单表」的决策，且 supersede/隐私分级/GDPR 级联/
-- 召回打分要重写一遍（子 RFC 2026-07-25-m2-memory-graph-rfc.md §2.1，泓舟拍板）。
-- 缺省值让存量条目行为逐字不变：weight=0 → 召回回退 confidence（weighting.effective_confidence）。
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS weight         REAL NOT NULL DEFAULT 0;
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS evidence_count INT  NOT NULL DEFAULT 1;
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS half_life_days REAL NOT NULL DEFAULT 0;
-- consent：''=未特别声明（沿用 privacy_level 治理）；'explicit'=用户显式授权过的敏感画像。
-- v1 只写不读，为 §5 生命周期强制项预留（消费在 M3/M4 的敏感画像功能）。
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS consent        TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_mem_user ON memory_item (tenant_id, user_id, occupant_id, superseded_by);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memory_item (kind);
CREATE INDEX IF NOT EXISTS idx_mem_predicate ON memory_item (user_id, predicate);

-- ── M2 记忆图谱 P1：关系边（2026-07-25）──────────────────────────────────
-- **为什么它独立成表而偏好不**（子 RFC §2.2）：关系边的 subject 不是当前用户
-- （「小雨 × 是女儿」「XX小学 × 是小雨的学校」），查询模式是**按实体名双向精确查 + 一跳**
-- 而非语义相似召回；塞进 memory_item 要么污染召回（关系边被当偏好注进 prompt），
-- 要么加一堆 kind='relation' 的特判。
-- v1 只存边 + 一跳，不做多跳推理/图算法/自动实体消歧（那些没有消费方）。
-- **红线**：GDPR forget() 必须同事务级联删本表，否则关系边（家人、孩子学校——恰恰最敏感
-- 的那部分）是假删除。契约测试 memory/tests/test_relation.py 直接锁。
CREATE TABLE IF NOT EXISTS memory_relation (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    user_id         TEXT NOT NULL,
    occupant_id     TEXT NOT NULL DEFAULT 'primary',
    subject         TEXT NOT NULL,                      -- 实体名（"小雨"）
    rel             TEXT NOT NULL,                      -- 封闭词表，见 memory/relation.py::REL_VOCAB
    object          TEXT NOT NULL,                      -- 实体名或字面值（"女儿" / "XX小学"）
    object_ref      TEXT NOT NULL DEFAULT '',           -- 可选：memory_item.id 或 profile.places 键
    confidence      REAL NOT NULL DEFAULT 1.0,
    provenance      TEXT NOT NULL DEFAULT 'user_stated',
    privacy_level   TEXT NOT NULL DEFAULT 'sensitive',  -- 家人/地点默认敏感（非 highly：要能被定向查到）
    consent         TEXT NOT NULL DEFAULT '',
    source_turn_ids TEXT NOT NULL DEFAULT '',           -- 证据溯源（§5 强制）
    valid_from      BIGINT NOT NULL DEFAULT 0,
    superseded_by   TEXT,
    created_at      BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON memory_relation (user_id, occupant_id, subject);
CREATE INDEX IF NOT EXISTS idx_rel_object  ON memory_relation (user_id, occupant_id, object);
-- pgvector ivfflat（与 registry 一致，需先有数据再建索引才高效；PoC 数据量小可暂不建）
-- CREATE INDEX IF NOT EXISTS idx_mem_embedding ON memory_item
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
