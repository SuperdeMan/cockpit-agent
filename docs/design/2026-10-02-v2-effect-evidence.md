# CA2-10：执行结果证据（回执 / 状态满足 / 观测归属 / 已核实分开）

> 状态：2026-10-02 契约冻结稿，实现中。proto 只增字段（`ResultEntry.evidence`），不改数据库 schema、`.env` 或 CI/CD。
> 依赖：[CA2-06 车辆观测](2026-09-27-v2-vehicle-state-and-simulation.md)（逐信号质量/时效、可选 `operation_id`）、
> [CA2-08 持久准入](2026-10-01-v2-durable-operation-admission.md)、[CA2-09 确认绑定](2026-10-02-v2-confirmation-binding.md)。排序见[路线图](../roadmap.md)。

## 1. 缺口（2026-10-02 复核）

| 现状 | 后果 |
|---|---|
| Verifier 只有 sat/unsat/unknown 一个结论；正常路径的 SAT 不写进结果，ResultBundle 一律 `unknown` | 「核实过」和「没核」在结果里分不开 |
| `state_match` 只看当前快照的值对不对 | 动作前目标就已满足也判 sat，「状态满足」和「这次动作导致了它」混为一谈 |
| 快照直接剔除陈旧 / 坏质量信号，unknown 不带原因 | 缺失、陈旧、uncertain、unavailable、期望取不到、没有镜像，在结果里是同一个词 |
| 车端 VAL 发布的观测从不带操作关联（`vehicle_driver.observation` 支持 `operation_id`，生产路径没传） | 云端区分不了「这一步改的」和「别人改的 / 本来就是」 |
| 最终回复之后才由车端执行的云侧动作（`pending_edge`），车端退旧显示时整行清空 | 「为什么不知道」没有留下来 |

## 2. 冻结的契约

### 2.1 四个事实分开

词表只有一份：`runtime/effect_evidence.py`。

| 事实 | 取值 | 含义 |
|---|---|---|
| `ack` | `acknowledged` / `unknown` | 执行方是否回了 OK。超时、流断在 final 之前为 unknown |
| `state` | `satisfied` / `unsatisfied` / `unknown` | 期望键最新一份**良好且未过期**的观测是否满足声明期望，不论成因 |
| `observed` | `attributed` / `unchanged` / `missing` / `unattributed` | 执行方报告改动的期望键是否都收到了带本步关联键的观测；`unchanged` = 执行方报告本步没改任何期望键 |
| `verified` | 布尔 | `state=satisfied ∧ observed=attributed`。动作前就已满足（`unchanged`）永远不是 verified |

- `reasons` 是固定码，只解释 unknown / unsatisfied / 未核实：`mirror_unavailable`、`signal_missing`、`signal_stale`、
  `signal_uncertain`、`signal_unavailable`、`expectation_unresolved`、`value_mismatch`、`ack_lost`、
  `already_satisfied`、`observation_missing`、`not_attributed`、`dispatched_after_reply`。
- `source_kind` / `authenticated` 取自被依赖观测的元数据，多键取最弱：任一来源未认证即 false；`simulated` 原样保留，不提升为实车。
- 回执与核实相互独立：回执丢了但带关联键的观测到了，`verified` 可以为真，`ack` 仍是 unknown（同 M-C「其实已经生效」）。

### 2.2 关联键：只用于归属，不是授权、也不是幂等键

- 声明了 `state_match` 的步在构造时生成进程内 `observation_ref`（32 位小写 hex），经 `meta["cockpit_observation_ref"]` 下发。
  不进 `step_record`：确认恢复是新的一次派发，换新键。客户端 prefs、计划与旧 meta 里的同名键一律剥离。
- 车端只接受 32 位小写 hex。在执行这一条 VAL 命令期间产生、且属于本次改动的观测样本带 `operation_id=<ref>`
  （CA2-06 的可选字段，在签名覆盖范围内）；启动后首包被提升为快照时，未改动的键不带。
- 车端在响应 `data` 回 `_receipt = {"observation_ref": <回显，可空>, "changed": [VAL 执行前后变化的键]}`。
  「这条命令改没改」只有执行方知道；回显为空表示车端没打关联，云端不再等带关联键的观测。
- 车端操作日志、接收端幂等键与恢复查询归 CA2-11，届时另行设计，不复用本键的语义。

### 2.3 求值

- 每个期望键从 `mirror.view(vehicle_id)` 读值与元数据：缺失 / stale / uncertain / unavailable / `$slot:` 取不到各有原因码；
  良好且未过期的值才参与比较，比较规则沿用 `_values_equal`。
- 归属：回执声明改动的期望键，其观测的 `operation_id` 必须等于本步关联键；回执缺席时（旧车端、回执丢失），
  看到任一期望键带本步关联键也算归属。
- 轮询：已核实、或已满足且没有待到的归属，即刻返回；否则等到 `timeout_ms`。等待上限与今天一致。
- 原结论（sat/unsat/unknown）、retry、`_verify` 与聚合器口径**完全不变**：结论仍按状态算。
  证据另存 `data["_evidence"]`，`step.verify` span 带同一份摘要。T1 执行器、D0、T2 三条路径都经 `_verify_outcome` /
  `_verify_uncertain` / `stream_uncertain_result`，证据来自同一个函数。
- 重复副作用防抖回放的结果不带前一步的证据：它没有执行，前一步的证据不属于它。
- T2 再规划的观测摘要剥掉 `_receipt` / `_evidence`：模型看到的观测与今天逐字一致，证据不是规划输入。

### 2.4 呈现

- `ResultEntry` 增加 `VerificationEvidence evidence = 12`（ack、state、observed、verified、reasons、source_kind、authenticated）。
  缺省 = 这一步没有声明状态核验。`verification` 字符串语义不变（状态结论）；正常路径的 SAT 开始写出。
- 每键明细（键名、取值、观测时刻）只进 span，不进公开结果。
- `pending_edge` 行带 `ack=unknown, state=unknown, observed=unattributed, reasons=[dispatched_after_reply]`；
  车端改写这类行（清话术与卡片）时保留证据。
- 网关映射 `evidence`；HMI / Android 共享投影按白名单透传，UI 与话术不变。

## 3. 兼容与回滚

- 旧车端不回 `_receipt`、不打关联键：`observed=unattributed`、不 verified，原结论不变。
- 旧云端不下发关联键：车端照常回 `_receipt`（回显为空），不打关联。
- proto 只增字段，旧客户端忽略；旧网关丢弃该字段，不影响其它字段。
- 回滚：回退 release。证据只是新增的观测面，不参与授权、确认、重试、话术与挂起。

## 4. 验证计划

1. 纯函数：四事实组合表——动作前已满足、部分键已满足、陈旧、uncertain、unavailable、缺失、期望取不到、无镜像、回执丢失但已归属、回执声明改动而观测未到。
2. 真 VAL + 真 `VehicleStateStore`：车端执行产生带关联键样本 → 云端镜像 → verified；已满足 → unchanged；观测丢失 → missing；首包快照只给改动键打关联。
3. T1 / D0 / T2 同一步同一证据；防抖回放不带证据；pending_edge 行与车端改写保留证据；网关映射；共享投影。
4. 反向验证、全量、四门禁；发布后只读核对。模拟车上的真栈车控探针属于 `remote_mutating`，另取授权。
