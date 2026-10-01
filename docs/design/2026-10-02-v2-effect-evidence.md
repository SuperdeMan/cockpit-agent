# CA2-10：执行结果证据（回执 / 状态满足 / 观测归属 / 已核实分开）

> 状态：2026-10-02 已部署 `a772e783`（status 5/5、verify verified）。proto 只增字段（`ResultEntry.evidence`），不改数据库 schema、`.env` 或 CI/CD。
> 模拟车上的真栈车控证据探针属于 `remote_mutating`，本包发布时未运行，见 §5。
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

## 5. 实现与证据（2026-10-02）

| 验证面 | 精确版本 / 结果 |
|---|---|
| 专项 | runtime 词表 22；云侧 `test_effect_evidence.py` 32（组合表、轮询、关联键、T1/D0/T2 同证据、回执丢失两条路、回放/规划/执行方边界、ResultBundle 序列化）；车端 `test_observation_ref.py` 7（真 VAL + 真驱动 + 真 `VehicleStateStore` 直到云端 `assess`） |
| 旧结论不动 | `eval_state_match` 委托 `assess`；测试里原样保留旧实现作对照，在 1000+ 组期望 × 快照 × 槽位组合上逐值相等；`test_verify.py` 57 条照旧通过 |
| 反向验证 | 24 处注入缺陷全部判红（含：动作前已满足算归属、任意标记算本步、首包快照全打标、关联键越出命令、执行方自带证据、回放继承、T2 观测带证据、客户端信任 verified、网关丢字段） |
| 本地全量 | `a772e783`：10507 passed / 35 skipped / 11 warnings（302.55 s）；四门禁、smoke 13/13、`capability_inventory --check`；Go 两个网关、HMI 359/359、Android 共享模块相关 69/69 |
| 发布 | dry-run 零阻断（无 schema 摘要）；status ok、release/running 均为 `a772e783`、5/5、零 warning；verify `20261001T184602Z-a772e78.json`（e2e_remote_safe / MiniMax-M3）verified |
| 固定语料 | 固定语料 20×3：60/60 完成、100 轮，业务红 5（4 轮未走手册、V207 两轮缺「露营」，均为既有签名），证据错误 0、open operations 0，216 次 LLM 全为 minimax/MiniMax-M3，零动作、零车态变化；语料全是只读问句、不含 state_match 步，ResultBundle 未出现 evidence；p50/p95/p99 6860/19781/27782 ms |

真栈车控证据：`scripts/probe_effect_evidence.py`（条件句确定性升成 T2，后件经执行器在车端执行）会在共享模拟车上开、关空调，
属于 `remote_mutating`，需要另取本轮授权后运行；期望读数是「开」verified/attributed、「再开」unchanged/already_satisfied、「关」verified，
且每条证据 `source_kind=simulated`、结束时车态与开始一致。

## 6. 已知边界

- 只有声明了 `state_match` 的能力有证据：目前是 `hvac.set` / `hvac.on` / `hvac.off`。其它车控对象要先在能力声明里写期望态，不在本包扩。
- 最终回复之后才由车端执行的云侧动作（`pending_edge`）本轮观测不到，证据如实写 `dispatched_after_reply`；车端改写这类行时保留它。
- 关联键是每次派发一枚的归属线索，不是幂等键；回执丢失后的恢复查询、车端操作日志归 CA2-11。
- `verified` 只说明「本步改动的期望键被打了本步关联键的良好观测满足」；签名来源只证明来源，模拟来源永远不代表实车动作。
