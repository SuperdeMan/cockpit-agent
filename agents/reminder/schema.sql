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

-- M-B：提醒的 owner 是 (user_id, occupant_id)。vehicle_id 只描述提醒的车辆环境，
-- 不参与 owner 判定。存量行由列默认值归 primary——**不按标题、创建时间或当前声纹猜**
-- 真实 owner，这是一次有损归属迁移且不可自动恢复。加法式 DDL，随启动幂等应用。
ALTER TABLE reminder_item
  ADD COLUMN IF NOT EXISTS occupant_id TEXT NOT NULL DEFAULT 'primary';
CREATE INDEX IF NOT EXISTS idx_reminder_owner_status
  ON reminder_item (user_id, occupant_id, status);
