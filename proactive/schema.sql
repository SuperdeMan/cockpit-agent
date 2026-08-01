-- proactive_delivery：未送达主动消息的账本（M-C）。与 registry/memory/reminder 同一
-- PostgreSQL 实例、独立表（启动幂等建表）。
--
-- **为什么要这张表**：治理器此前把待发/延后队列全放进程内存。M3 RFC 记的理由是
-- 「这些消息的生命周期以秒计，落库不值当」——那句话对 advisory/ambient 成立，
-- 对 `user_contract` 不成立：「到点提醒我」是用户显式约定，它的生命周期不是秒，
-- 是「直到我看见」。ack 之后到真正投出去之间有合并窗（默认 1.5s）加最长 ttl 的延后
-- 队列，进程在这段里死掉消息就没了，而生产方早已收到 accepted、不会重发。
--
-- **只给 critical / user_contract 落库**：advisory/ambient 本来就可以不说，
-- 为它们付持久化代价不划算。
--
-- **列集刻意最小**：一辆车一个 HMI、量级个位数，所以没有 present lease、
-- state_version 并发协议、多实例 outbox 认领这些为高并发准备的机制
-- （规格里有，本批按第一性原理裁掉，判据记在验收报告）。
CREATE TABLE IF NOT EXISTS proactive_delivery (
  delivery_id   TEXT PRIMARY KEY,
  dedup_key     TEXT   NOT NULL DEFAULT '',
  user_id       TEXT   NOT NULL DEFAULT '',
  occupant_id   TEXT   NOT NULL DEFAULT 'primary',   -- M-B OwnerKey 的另一半
  source        TEXT   NOT NULL DEFAULT '',          -- 生产方 agent_id（只用于观测/排障）
  priority      TEXT   NOT NULL,
  payload       JSONB  NOT NULL DEFAULT '{}',        -- 完整信封：重启要靠它重建 Item
  -- pending → dispatched → presented；dropped/expired 是终态。
  -- **只有 presented 是通知合同完成**——网关 write 成功不算，HMI 渲染出来才算。
  state         TEXT   NOT NULL DEFAULT 'pending',
  reason        TEXT   NOT NULL DEFAULT '',
  attempts      INT    NOT NULL DEFAULT 0,
  created_at    BIGINT NOT NULL,
  expires_at    BIGINT NOT NULL DEFAULT 0,           -- 0=不过期；过期后不重播陈旧内容
  dispatched_at BIGINT NOT NULL DEFAULT 0,
  presented_at  BIGINT NOT NULL DEFAULT 0
);

-- 未送达扫描（HMI 连上时重投 / 治理器重启时恢复，同一份账）
CREATE INDEX IF NOT EXISTS idx_delivery_open
  ON proactive_delivery (state, created_at);
-- owner 级删除（M-B privacy 口径的延伸：payload 里的话术与卡片摘要是个人数据）
CREATE INDEX IF NOT EXISTS idx_delivery_owner
  ON proactive_delivery (user_id, occupant_id);
