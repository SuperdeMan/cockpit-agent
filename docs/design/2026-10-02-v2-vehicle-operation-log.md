# CA2-11：车端操作日志、接收端幂等与恢复查询

> 状态：2026-10-02 方案稿。§3 已决策：用户选 **A**（edge-orchestrator 新增命名卷，SQLite 落在卷上）；实现中，
> 基础设施摘要在发布时按实际内容批准。云端数据库 schema 不变；proto 只增字段。
> 依赖：[CA2-08 持久准入](2026-10-01-v2-durable-operation-admission.md)（operation 身份与判定表）、
> [CA2-09 确认绑定](2026-10-02-v2-confirmation-binding.md)、[CA2-10 结果证据](2026-10-02-v2-effect-evidence.md)、
> [CA2-12 故障注入](2026-09-27-v2-vehicle-state-and-simulation.md)。排序见[路线图](../roadmap.md)。

## 1. 缺口（2026-10-02 复核）

| 现状 | 后果 |
|---|---|
| 云网关只等到步骤预算（车控 2 s）为止，迟到的车端结果直接丢弃（`late/unknown edge result`） | 车端已执行，云端判超时，用户听到「处理超时」或「结果未知」 |
| 车端按到达顺序执行，不认操作身份；CA2-08 对 edge 执行方显式不支持 durable 准入 | 重发或用户「再试一次」会二次执行（「调高温度」会调两次） |
| 车端没有任何可写存储（edge 容器只挂了只读的证书与模型） | 执行后、落库前崩溃，重启后无从知道这条做没做过 |
| 超时步只能靠 Verifier 看车态翻案，而声明了 state_match 的只有 `hvac.set/on/off` | 其它车控超时一律 unknown |

## 2. 拟定契约（v1）

### 2.1 车端操作日志：接收端幂等

- edge 上会改状态的车控能力声明 `admission: durable`，放开 CA2-08 对 edge 执行方的限制。云端 `Step` 照 CA2-08 生成
  operation_id，随 `step_record` 跨挂起保持，经 `cockpit_operation` 头下发；确认恢复用同一 operation_id。
- 车端在 VAL 执行**之前**写 `executing` 记录（SQLite，WAL + `synchronous=FULL`），执行后结算为 done / failed，附 CA2-10 回执与 VAL 话术。
  同一 operation_id 再来时，按 CA2-08 同一张判定表（`runtime.operation.decide`）答 duplicate / in_progress / unknown / mismatch，不再执行。
- 重启时遗留的 `executing` 记录转为 `orphaned`（结局未知）：同一操作再来只答 unknown，**不盲目重放**。
- 只存最小字段：operation_id、vehicle_id、step_id、intent、参数摘要、状态、改动键、VAL 话术、时间戳。
  不存 user_id、原话或位置。保留 24 h，到期在写路径里清理。

### 2.2 恢复查询

- `EdgeCall` 新增只读变体 `operation_query`，沿用 `contract_query` 的先例：intent 必须为空，旧车端看到空 intent 直接拒绝。
  车端只从日志作答：{status, outcome, receipt, speech}，不触发 VAL。
- 云端：edge 步超时且带 operation_id 时，在剩余预算内查一次（上限 1.5 s）。
  - done：以记录的结果收口，证据里 ack 记为经查询确认；
  - executing / orphaned：如实 unknown；
  - absent（车端从没收到）：允许用**同一 operation_id** 重发一次，由接收端去重兜底；仍失败则 unknown。
- 不宣称 exactly-once：至少一次投递加接收端去重。补偿是新的受控动作，照样走权限与确认。

### 2.3 协调同步

v1 不做后台全量同步：云端只在超时或用户追问「刚才执行了吗」时按需查询。日志在车端是唯一权威，云端不另建一份。

## 3. 待决策

| 项 | 选项 | 影响 |
|---|---|---|
| 车端日志存储 | **A**：edge-orchestrator 新增命名卷（`deploy/docker-compose.yaml` 与云端 overlay），SQLite 落在卷上 | 满足「执行后落库前崩溃不重放」「回滚保留日志可读」；属基础设施变更，需要单独批准摘要；隐私登记新增车端存储（删除适配器与 seed/count/read/verify 用例） |
| | **B**：写在容器文件系统里（不挂卷） | 进程重启后保留、重新部署即丢；不动基础设施；不满足「回滚保留日志可读」 |
| | **C**：只放内存 | 只防同进程内的重复，不满足崩溃判据 |
| 能力版本 | edge 车控能力加 `admission: durable` 会改变能力版本 | 发布那一刻在途的确认挂起会被 CA2-09 绑定判「不一致」而拒绝，用户需重新发起（窗口不超过挂起 TTL 300 s） |

## 4. 验证计划

1. SQLite 存储契约，与 CA2-08 的内存孪生逐场景对照；崩溃注入：写下 executing 后杀进程重启 → orphaned → 再次请求不执行。
2. 云网关迟到结果与云端超时 → 恢复查询 → done 收口；absent → 同一 id 重发一次，被车端去重。
3. 旧车端 / 旧云端兼容；反向验证；全量与四门禁。真栈：基础设施变更获批后发布；模拟车上的车控探针需另取 remote-mutating 授权。
