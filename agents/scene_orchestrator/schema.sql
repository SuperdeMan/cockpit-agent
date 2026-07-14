-- scene_item：与 registry/memory/reminder 同一 PG 实例、独立表（启动幂等建表）。
-- 列名与 Scene DSL 顶层键**一一同名**（v2.1 修正①）——store 的 to_row/from_row 是无脑映射，
-- 不做改名翻译，防两侧漂移。
CREATE TABLE IF NOT EXISTS scene_item (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  name        TEXT NOT NULL,
  aliases     JSONB NOT NULL DEFAULT '[]',
  description TEXT NOT NULL DEFAULT '',
  goal        TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL DEFAULT 'user',
  status      TEXT NOT NULL DEFAULT 'enabled',
  guards      JSONB NOT NULL DEFAULT '[]',
  actions     JSONB NOT NULL,
  triggers    JSONB NOT NULL DEFAULT '[]',
  created_at  BIGINT NOT NULL,
  updated_at  BIGINT NOT NULL,
  use_count   INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scene_user ON scene_item (user_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scene_user_name ON scene_item (user_id, name);
