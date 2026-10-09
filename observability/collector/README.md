# Observability Collector

轻量可观测聚合服务。订阅 NATS 观测事件，内存维护最近状态（实时 WS 推流），并把
轮次/链路/LLM 调用/日志落 SQLite 持久化（badcase 排查跨重启可查），经 REST 提供给
`dashboard/`。

订阅的 subject：`vehicle.state.changed`、`obs.span`、`obs.metric`、`obs.agent.health`、
`obs.turn`（轮次收口）、`obs.llm`（LLM 调用）、`obs.log`（结构化日志，2026-07-10 起）。

## 运行

```bash
export PYTHONPATH=$PWD:$PWD/gen/python
export NATS_URL=nats://localhost:4222
export OBS_DB_PATH=./obs.db   # 不设=内存库（可跑但不跨重启）；compose 挂 obs-data 卷
export E2E_IDENTITY_SECRET=...  # 运维凭据的派生根（与根 .env 同值）；不设则除健康与指标外一律 503
python -m observability.collector.main
```

默认监听 `8092`。完整接口清单见 `docs/conventions.md` §8；核心新增：

- `GET /api/sessions` / `GET /api/sessions/{id}/turns` — 会话列表与轮次流水
- `GET /api/turns/{trace_id}` — 轮次详情（turn + spans + llm_calls + logs 一次取全）
- `GET /api/search` / `GET /api/logs` — 轮次检索（文本/状态/badcase）与日志检索
- `POST /api/turns/{trace_id}/badcase` / `GET /api/export/{trace_id}` — 标记与单轮全量导出
- 既有 `/api/traces*`、`/api/agents`、`/api/vehicle/state`、`/metrics`、`WS /stream` 不变
  （`/stream` 增播 `turn`/`llm`/`log` 事件类型）

### Dashboard Visual v2 只读查询（2026-10-09）

只扩充查询，不改表结构、采集写路径或保留策略。旧版 `/api/search`、`/api/sessions`
默认仍返回数组；传 `paginated=1` 才返回 `{items,total,limit,offset}`。`total` 是同一组筛选
在分页前的总数（分别为轮次、会话），不是当前页条数；`limit` 保留原有语义，`offset >= 0`。

| 接口 | 新增契约 |
|---|---|
| `/api/search` | `origin`、`category`、`outcome` 支持逗号分隔多选；同参数内 OR，不同参数间 AND。可选布尔 `edge_disagreement`、`actionability_disagreement`、`degraded`、`has_warnings`、`labeled`；`min_duration_ms >= 0`；`offset`、`paginated`。原 `q/status/session/badcase/since/until/limit` 保留 |
| 轮次查询 | search / session turns / detail / export 的 turn 均带 `outcome_category`（直接调用 `runtime.outcome.category_of`）、`origin`、`warning_count` |
| `/api/sessions` | 行带 `first_user_text`（按时间及 trace ID 取该会话首句）、`origin`；支持 `origin` 多选与 `offset/paginated`。文本命中某轮仍返回整个会话的计数和首句 |
| `/api/turns/{trace_id}` 与 export | 先从 `llm.call.meta` 合并 `pinned/requested_tier`，再从返回的 spans 隐藏它；历史 `error` span 只在查询副本映射为 `err`，持久行保留原值 |
| `/api/llm/summary` | 每个 caller × model 分组增加 `fallback_calls` 与 `zero_usage_calls`。后者仅计 `status=ok` 且输入、输出 token 都为 0 的成功调用，不表示真实消耗为零；失败调用独立计入 `errors` |
| `/api/meta` | 受运维令牌保护；返回 `content_capture: bool`、`retention_days: number`、`debug_vehicle_control: bool`、`query_features: ["turn_filters", "turn_pagination", "session_pagination"]` |

`origin` 唯一前缀表在 `query_projection.py`：`hmi/app/dashboard/replay/test/probe/release/unknown`。
它只描述会话命名空间，不证明身份；合成前缀与 HMI / App / 指令台 / 重放 / E2E / 发布探针
有生产方对账测试。未登记的前缀显示 `unknown`。

分歧读取既有 `!=` 后缀；`degraded` 覆盖 `_degraded` / `_fallback`（包括后续限定后缀）及
`_salvage*` 通道；`has_warnings` 与 `warning_count` 同数 WARN / WARNING / ERROR /
CRITICAL / FATAL 日志；`labeled` 指非空 `gold_intents`。这些筛选不把 `status=ok` 等同于业务成功。

`/api/meta` 的配置只反映当前运行时，不能证明任意缺失 trace 曾存在或已被清理。
未知 trace 仍返回 `{"error":"not found"}`；不从保留天数推断「已过保留期」。

## 持久化与保留

- SQLite（stdlib，WAL）四张表：`turns` / `spans` / `llm_calls` / `logs`；
  写入 best-effort，持久层故障不影响实时流。
- `llm_calls` 自 2026-07-17 增 `provider` 列（实际 serving 的 LLM 厂商，供「哪个脑答的」审计；
  `_ensure_column` 加法迁移兼容既有 volume 旧库）；`obs.llm` 事件另带
  `requested_tier`/`pinned` 字段（广播可见，未落列）。
- `OBS_RETENTION_DAYS`（默认 7）定期清理；**badcase 标记的轮次及其链路数据豁免**。
- 内容级字段（用户原话/话术/plan/LLM 输入输出）受 `OBS_CONTENT_CAPTURE` 门控 +
  统一脱敏（`observability/redact.py`）；off 时只存长度与哈希指纹。

## 安全边界

- `POST /api/debug/vehicle` 只允许 `speed_kmh/battery/gear/location`。
- 除 `/healthz`、`/metrics`、`/api/agents` 外，读写都要运维令牌（`runtime/obs_access.py`，
  [设计](../../docs/design/2026-10-03-v2-collector-access.md)）：HTTP 用 `Authorization: Bearer`，`/stream` 首帧
  `{"type":"auth","token":…}`；密钥由 `E2E_IDENTITY_SECRET` 派生，缺失时这些接口一律 503。
  `python scripts/obs_token.py` 打印一枚 12 小时内有效的令牌。
- 非开发环境必须设置 `DEBUG_VEHICLE_CONTROL=false`、`OBS_CONTENT_CAPTURE=off`（仍无多车隔离、无告警）。
- collector 是旁路；NATS 或 collector 故障不得影响座舱主链路。

## 验证

```bash
python -m pytest --import-mode=importlib observability/collector/tests -q
curl http://localhost:8092/healthz
python test/e2e_obs.py   # 真栈：turn 落库/obs.llm/日志关联/badcase/重启持久化（16 断言）
```

## 车辆维度与有效期（CA2-06）

`/api/vehicle/state?vehicle_id=v1` 保留纯值响应；`/api/vehicle/observation?vehicle_id=v1` 返回逐信号来源/质量/时效。
`/stream?vehicle_id=v1` 只推该车状态，发送前在连接锁内读取最新投影，每秒检查过期；调试/trace 面仍为受限 PoC。
来源校验复用 `runtime.vehicle_state`，公共 `VEHICLE_STATE_TRUST` 配置与启用状态见 [实施记录](../../docs/design/2026-09-27-v2-vehicle-state-and-simulation.md)。
