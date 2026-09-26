# Edge Orchestrator（端侧编排器）

端侧"快系统"。内含 Fast Intent + 端侧车控/媒体 Agent + 模拟 VAL（单进程简化）。

## v2 后续边界（未实现）

CA2-06 先做车辆身份与逐信号可信事件，CA2-11/12 做本地操作日志和有状态故障仿真，CA2-18 才接一种 OEM 驱动。
T0 与 VAL 继续确定性执行；CA2-13/14 的 T1e 是可选实验，NLU 当前 off/shadow 不因路线图合入而可执行。
云端不可用时所需操作准入/状态复核在车端，不把 Jev 或云 PG 变成快路径前提。
依赖和验收见 [路线图](../../docs/roadmap.md)与 [实施方案](../../docs/design/2026-09-26-cockpit-agent-v2-implementation-plan.md)。


## 流程
1. Fast Intent 对单意图或多意图片段做结构化分类。
2. 完全本地且无需确认的语义组经 `VAL` 秒回；本地轮 best-effort 写共享记忆。
3. 含导航、歌手/歌曲限定等慢片段的语义组完整上云，避免丢上下文或重复本地执行。
4. 云端可通过 `edge_call` 调用本车快能力；所有车控仍由 `EdgeCallExecutor → VAL` 执行。
5. 云端回流 action 做来源校验，已由 edge VAL 执行的动作只展示、不二次下发；未执行的
   `vehicle.control` 经 `edge_call.action_to_structured` 翻成 VAL 结构化命令走完整流水线
   （含安全门控），翻译失败再回退 legacy 串。
6. 云端不可达时给出降级提示，纯本地安全快路径仍可用。

## 安全约束
车控只经 `val.VAL` 下发（指令校验 + 安全态门控 + 高速禁开窗等）。

## 与云侧共用的判据（`runtime/`，落点由镜像依赖闭包决定）
`polarity`（指令极性）／`question_shape`（问句还是指令）／`cntime`（中文时间词）／
`clock`（业务时区墙钟）／`clause_split`（**复合句分隔符表**）。
⚠ `clause_split` 是**表共用、语义不共用**：本目录拆完每段还要各自解出一条命令
（`_resplit_on_he` 的「和」二次拆分、`_expand_paired_objects` 的并列对象展开、
场景句整句拦截都留在这里），云侧拆完只问「这一段有没有被计划覆盖」。

## Phase 1 已落地
- 端云双向流：Python Edge Orchestrator 的 `cloud_client.py` 持有持久 bidi，连接 Go Cloud Gateway；Go Edge Gateway 承担用户接入
- `edge_call`→VAL、动作卡回传与防双发
- 混合意图语义分组、本地/云端分流、危险动作确认
- 连接状态追踪、端侧轮记忆与降级增强
- VAL 状态 diff/启动快照、route/VAL span 经 NATS best-effort 发出
- collector debug 仅允许 `speed_kmh/battery/gear/location` 四类模拟环境量

## 待办
- 端侧 NLU 已有 shadow；后续执行放量与可选 T1e 按 CA2-13/14 评测，规则/阈值 OTA 仍待验证。
- 真实车辆适配按 CA2-18；C++/Rust 是否必要以目标板资源测量决定，不预设重写。
