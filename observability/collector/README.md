# Observability Collector

轻量可观测聚合服务。订阅 NATS 的车辆状态、span、Agent 指标和健康事件，在内存中
维护最近状态，并通过 REST 快照与 WebSocket 增量提供给 `dashboard/`。

## 运行

```bash
export PYTHONPATH=$PWD:$PWD/gen/python
export NATS_URL=nats://localhost:4222
python -m observability.collector.main
```

默认监听 `8092`。常用接口见 `docs/conventions.md` §8。

## 安全边界

- `POST /api/debug/vehicle` 只允许 `speed_kmh/battery/gear/location`。
- 非开发环境必须设置 `DEBUG_VEHICLE_CONTROL=false`。
- collector 是旁路；NATS 或 collector 故障不得影响座舱主链路。
- 当前数据仅存内存，不提供持久化、鉴权、多车隔离或告警。

## 验证

```bash
python -m pytest observability/collector/tests -q
curl http://localhost:8092/healthz
```
