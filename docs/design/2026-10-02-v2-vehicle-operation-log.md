# CA2-11：车端操作日志、接收端幂等与恢复查询

> 状态：2026-10-02 实现完成，待发布。§3 已决策：用户选 **A**（edge-orchestrator 新增命名卷，SQLite 落在卷上）；
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
| 车端截止超时被分发器记成 `edge_unreachable`，与「没送达」混在一起，执行器看到的错误码还是空的 | 「可能已执行」被当成「确定没执行」，超时翻案也够不着 |

## 2. 契约（v1，已实现）

### 2.1 一份准入循环，两个接收方

CA2-08 的准入循环（`admit`、`OperationGate`、受控话术）下沉到 `runtime/operation_gate.py`。SDK servicer 与车端
`EdgeCallExecutor` 共用；`agents/_sdk/operations.py` 只做再导出，`OperationStoreError` 移到 `runtime.operation`。
判定表仍是 `runtime.operation.decide`，日志名仍是 `agent.sdk.operations`。

### 2.2 车端操作日志：接收端幂等

- edge 上会改状态的车控与媒体能力声明 `admission: durable`；契约规则放开为「SDK agent（cloud）或车端执行器（edge_fast/edge）」，工具仍不许。
  ABI 摘要不含契约，冻结迁移清单里的 ABI 登记不受影响；变的只是契约摘要与能力版本。
- 云端 `Step` 照 CA2-08 生成 operation_id，随 `step_record` 跨挂起保持，经 `cockpit_operation` 头下发；确认恢复用同一 operation_id。
- 车端入口 `EdgeCallExecutor.dispatch`：只读查询 → 契约预检 → 准入 → VAL → 结算。主体写成 `vehicle:<vehicle_id>`，
  日志不存说话人；`execute` 保留为不经准入的 VAL 翻译（单测用），生产接线只走 `dispatch`，测试钉着接线。
- 日志是 `orchestrator/edge/operation_log.py`（SQLite，WAL + `synchronous=FULL`）。每个方法一条带条件的 SQL，
  逐条对齐云端 SQL 与内存孪生；CA2-08 的 22 个准入场景原样跑在它上面。
- 进程启动时把遗留的 `executing` 记录转为 `orphaned`：同一操作再来只答 unknown，**不盲目重放**。
- 只存 operation_id、主体（车辆）、agent、trace_id、摘要信封（意图、参数摘要、绑定摘要、能力版本、阶段）与结果引用；
  不存说话人、原话或位置。24 h 之前的记录在写入路径里清掉。

### 2.3 恢复查询

- `EdgeCall.operation_query`（字段 5）：只读，intent 必须为空（沿用 `contract_query` 的先例，旧车端看到空 intent 直接拒绝）。
  车端只读日志，回 `data._operation = {operation_id, decision: "query", status, phase?, outcome?}`；没有记录时 `status=absent`。
- 分发器把车端 `DEADLINE_EXCEEDED` 当作执行器超时（结局未知），不再记成 `edge_unreachable`（确定没执行）。
- 执行器：edge 步超时且带 operation_id ⇒ 查一次（上限 1.5 s）：
  - done：OK，零领域话术「刚才没收到执行回执，车端记录显示这个操作已经完成。」，`_operation.decision = recovered`，带指纹防同轮重发；
  - failed：FAILED（`edge_reported_failure`）；
  - absent：用**同一** operation 重发一次，晚到的第一份由车端日志去重；再超时就是 unknown；
  - accepted / orphaned / cancelled / 没问到：保持超时（unknown），不重发。
- 不宣称恰好一次：至少一次投递加接收端去重。补偿是新的受控动作，照样走权限与确认。

### 2.4 协调同步

v1 不做后台全量同步：云端只在超时时按需查询。日志在车端是唯一权威，云端不另建一份。

## 3. 决策与代价

| 项 | 结论 | 代价 |
|---|---|---|
| 车端日志存储 | **A**：edge-orchestrator 挂命名卷 `edge-operations:/data`，云端 overlay 钉名 `car-agent-edge-operations`，`EDGE_OPERATION_LOG=/data/edge-operations.sqlite3` | 属基础设施变更，发布需批准摘要；没配路径时退回进程内内存（只防同进程重复，启动时告警） |
| 能力版本 | edge 写能力的契约摘要变了 | 发布那一刻在途的确认挂起会被 CA2-09 绑定判「不一致」而拒绝（窗口不超过挂起 TTL 300 s）；不带契约头的旧调用方不再被当作兼容的旧契约 |
| 契约探测 | 新摘要不在冻结清单里，`dispatch_to_edge` 每次派发前先做一次只读契约探测 | 每条云端车控多一次车端往返；上线后实测，必要时按车辆缓存探测结果 |
| 隐私 | 日志不存说话人、原话与位置，保留 24 h，不登记为用户数据存储 | trace_id 只是关联键；对应的观测数据仍按 collector 的删除流程处理 |

## 4. 验证

| 验证面 | 结果 |
|---|---|
| 账本语义 | CA2-08 的 22 个准入场景跑在 SQLite 上全过；新增「错误绑定不能结算」进共享场景后，内存孪生、SQLite、嵌入式 PostgreSQL 16.2（`test/probe_operation_admission_sql.py`，22/22）三份实现一致 |
| 车端专属 | 重启后 executing → unknown 且不重放；已结算记录跨重启可读；24 h 过期；无路径时只在内存；存储不可用即拒绝 |
| 车端入口 | 同编号重投不再执行；确认恢复同一记录且只执行一次；缺编号拒绝；换参数复用编号拒绝；执行中掉电后答 unknown；只读查询不碰 VAL；读能力不准入；接线测试 |
| 云端 | 截止超时 → 超时而非不可达；done / failed / absent 重发同一身份 / 其余保持未知；没有 operation 的步不查；客户端只发只读查询 |
| 反向验证 | 18 处注入缺陷全部判红；首轮漏了「结算不核对绑定」，补共享场景后转红 |
| 架构守卫 | 全仓调用图分析的两处纯查找加了记忆化：词表与违规结果和不缓存时逐项相等，单条用例 62.7 s → 16.9 s（改动前基线 36.6 s） |

## 5. 已知边界

- 只覆盖云端派发的车控；车端 T0 本地快路径不经云端，不写这本日志。
- 云端只在超时时查；用户追问「刚才执行了吗」暂不读车端日志。
- 契约探测的额外往返未优化。
