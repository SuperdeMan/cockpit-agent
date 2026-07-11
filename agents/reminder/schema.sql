-- reminder_item：与 registry/memory 同一 PG 实例、独立表（启动幂等建表）
CREATE TABLE IF NOT EXISTS reminder_item (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  vehicle_id  TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'time',
  fire_at     BIGINT NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending',
  created_at  BIGINT NOT NULL,
  fired_at    BIGINT NOT NULL DEFAULT 0,
  source      TEXT NOT NULL DEFAULT 'user',
  recur       TEXT NOT NULL DEFAULT '',
  extra       JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_reminder_due ON reminder_item (status, fire_at);
CREATE INDEX IF NOT EXISTS idx_reminder_user ON reminder_item (user_id, status);
