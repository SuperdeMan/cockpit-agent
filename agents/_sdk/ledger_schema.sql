-- task_ledger：跨轮持久任务账本（M2 P0）。与 registry/memory/reminder 同一 PG 实例、
-- 独立表，由 SDK 单方 `CREATE IF NOT EXISTS` 持有（reminder_item 先例）。
-- 契约登记见 docs/conventions.md §9.6；设计见 docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md §2.2。
CREATE TABLE IF NOT EXISTS task_ledger (
  task_id         TEXT PRIMARY KEY,          -- uuid4（禁 id(obj)：corr_id 内存地址复用撞键的老教训）
  user_id         TEXT NOT NULL,
  session_id      TEXT NOT NULL DEFAULT '',  -- 受理会话（结果推送与记忆归属用）
  agent_id        TEXT NOT NULL,             -- 执行方（'deep-research'）
  kind            TEXT NOT NULL,             -- 任务类型：'research' | 后续 'trip' ...
  goal            TEXT NOT NULL,             -- 用户目标原话（原文入库供续接；进 obs 前脱敏）
  idempotency_key TEXT NOT NULL,             -- sha256(user_id|kind|归一化 goal)[:16]
  status          TEXT NOT NULL,             -- accepted|running|done|failed|cancelled|orphaned
  progress        TEXT NOT NULL DEFAULT '',  -- 人话进度（"检索中 3/9 个子问题"）
  budget          JSONB NOT NULL DEFAULT '{}',      -- {deadline_ts, llm_calls_max/used, ext_calls_max/used}
  result_ref      JSONB NOT NULL DEFAULT '{}',      -- 终态产物引用（card 摘要/memory key），不存全文
  origin_trace_id TEXT NOT NULL DEFAULT '',
  heartbeat_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ledger_user_active ON task_ledger (user_id, status);
CREATE INDEX IF NOT EXISTS idx_ledger_idem ON task_ledger (user_id, idempotency_key, status);

-- M-D：同一 (user_id, idempotency_key) 在**活跃态**下唯一。
-- `open()` 此前是「先 SELECT 再 INSERT」——两个实例可以同时查不到、同时插入，
-- 于是**同一个幂等请求有两个实例都拿到了执行权**（对写操作就是双下单）。
-- 判定权交给数据库：谁的 INSERT 成功谁执行，另一个拿到冲突并按 Duplicate 处理。
-- partial 是必须的：终态行要能共存（同一件事可以再做一次），只约束「在跑」的那些。
CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_active_idem
  ON task_ledger (user_id, idempotency_key)
  WHERE status IN ('accepted', 'running');
