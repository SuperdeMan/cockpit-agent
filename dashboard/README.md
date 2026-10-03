# Observability Dashboard

独立于座舱 HMI 的开发/演示可观测台。2026-07-10 起以 **badcase 排查为第一公民**重构为四视图
（设计：`docs/design/2026-07-10-dashboard-badcase-observability-redesign.md`）：

- **会话**（默认页）：会话列表 → 轮次时间线 → 轮次详情三级下钻。详情一屏聚合：
  用户原话/最终话术、Planner plan + LLM 原始输出（`cloud.planning` span 门控采集）、
  span 瀑布、LLM 调用列表（obs.llm）、按 trace 关联的日志（obs.log）、
  badcase 标记/备注、导出 JSON。搜索框支持文本过滤，**粘贴 HMI 气泡复制的
  trace_id 直达该轮**。
- **总览**：原五面板（对照实验指令台 / 实时链路 / 车辆状态 diff / 环境量滑块 /
  Agent 健康·调用·时延·错误率·熔断·降级·token）。
- **日志**：结构化日志检索（服务/级别/关键词过滤 + WS 实时追加）。
- **收藏**：badcase 列表（保留期豁免）+ 一键重放对照（原话经 Edge Gateway 重发，
  新旧两轮并排）。

数据源 = collector（NATS 聚合 + SQLite 持久化，重启不丢，`OBS_RETENTION_DAYS` 默认 7 天）。

## 运行

```bash
npm ci
npm run dev
```

默认地址为 `http://localhost:5174`。环境变量：

- `VITE_COLLECTOR_URL`：默认 `http://localhost:8092`
- `VITE_COLLECTOR_TOKEN`：collector 运维令牌，`python scripts/dev_stack.py dashboard` 启动时自动注入；
  没有注入（如云上 8445）时第一次被拒会请你粘贴 `python scripts/obs_token.py` 的输出，只存本页会话
- `VITE_EDGE_GATEWAY_URL`：默认 `http://localhost:8090`

## 验证

```bash
npm test
npm run build
```

真栈端到端：`python test/e2e_obs.py`（起全栈后跑，覆盖 turn 落库/plan 采集/LLM 记录/
日志关联/badcase 标记/重启持久化）。

## 边界

Dashboard 不直接写空调、车窗等车控状态。命令复用 Edge Gateway，车控仍只经 VAL；
动态滑块只调用 collector 的环境量 debug 接口。

`DEBUG_VEHICLE_CONTROL` 仅用于本地演示；非开发环境必须设为 `false`。collector 的读写都要运维令牌
（[设计](../docs/design/2026-10-03-v2-collector-access.md)），dashboard 只在本页会话里保存它。内容级采集（用户原话/话术/plan/LLM 输入输出）由
`OBS_CONTENT_CAPTURE` 门控（统一脱敏）——**量产必须 off**，off 后仅保留长度与
哈希指纹，链路形状排查不受影响。

## 观测质量（CA2-06）

当前 UI 显式订阅历史 v1；Collector 提供按 vehicle_id 查询的值与观测元数据接口。
完整投影替换旧值，过期或断连清空，缺失速度/挡位/开关显示未知；模拟标识不代表真实执行验证。
[接入和签名配置](../docs/design/2026-09-27-v2-vehicle-state-and-simulation.md)。
