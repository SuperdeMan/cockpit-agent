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
python -m observability.collector.main
```

默认监听 `8092`。完整接口清单见 `docs/conventions.md` §8；核心新增：

- `GET /api/sessions` / `GET /api/sessions/{id}/turns` — 会话列表与轮次流水
- `GET /api/turns/{trace_id}` — 轮次详情（turn + spans + llm_calls + logs 一次取全）
- `GET /api/search` / `GET /api/logs` — 轮次检索（文本/状态/badcase）与日志检索
- `POST /api/turns/{trace_id}/badcase` / `GET /api/export/{trace_id}` — 标记与单轮全量导出
- 既有 `/api/traces*`、`/api/agents`、`/api/vehicle/state`、`/metrics`、`WS /stream` 不变
  （`/stream` 增播 `turn`/`llm`/`log` 事件类型）

## 持久化与保留

- SQLite（stdlib，WAL）四张表：`turns` / `spans` / `llm_calls` / `logs`；
  写入 best-effort，持久层故障不影响实时流。
- `OBS_RETENTION_DAYS`（默认 7）定期清理；**badcase 标记的轮次及其链路数据豁免**。
- 内容级字段（用户原话/话术/plan/LLM 输入输出）受 `OBS_CONTENT_CAPTURE` 门控 +
  统一脱敏（`observability/redact.py`）；off 时只存长度与哈希指纹。

## 安全边界

- `POST /api/debug/vehicle` 只允许 `speed_kmh/battery/gear/location`。
- 非开发环境必须设置 `DEBUG_VEHICLE_CONTROL=false`、`OBS_CONTENT_CAPTURE=off`，
  并把 collector 置于正式鉴权边界之后（无鉴权、无多车隔离、无告警）。
- collector 是旁路；NATS 或 collector 故障不得影响座舱主链路。

## 验证

```bash
python -m pytest --import-mode=importlib observability/collector/tests -q
curl http://localhost:8092/healthz
python test/e2e_obs.py   # 真栈：turn 落库/obs.llm/日志关联/badcase/重启持久化（16 断言）
```
