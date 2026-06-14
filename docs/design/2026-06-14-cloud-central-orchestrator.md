# 云端慢思考中枢：理解 → 规划 → 异构调度（车端快思考 / Agent / 工具）

- **状态**：草案（已与泓舟对齐骨架，2026-06-14）
- **交付对象**：后续开发者 / Agent（照此执行，不走偏）
- **关联代码**：`orchestrator/cloud/{engine,planning,executor,clients,models,aggregator,session}.py`、`orchestrator/edge/{server,cloud_client,val}.py`、`proto/cockpit/{agent,channel,registry}/v1/*.proto`、`registry/`、`gateway/cloud/`
- **关联文档**：`docs/architecture/cockpit-agent-architecture.md`（§5 编排器、§9 安全，唯一真相源）、`docs/design/2026-06-13-multi-intent-and-context.md`、`docs/design/2026-06-13-open-domain-latency.md`、`docs/design/2026-06-13-vehicle-control-command-architecture.md`

---

## 0. 一页速读（TL;DR）

把云端"慢思考"从 **一次性 DAG 编排器** 升级为 **能理解复杂意图、按需规划、并调度异构目标（云 Agent / 车端快思考 / 工具）的中枢**——但**严格在"延迟可控 + 车控经 VAL + 不自由放飞"的边界内**。

三档运行模型（延迟可控的根）：

| 档 | 处理 | LLM 往返 | 延迟 | 触发 |
|---|---|---|---|---|
| **T0 端侧快路径**（已有）| 简单车控/查询 | 0 | <500ms | `fast_intent` 命中本地白名单 |
| **T1 单次 DAG**（默认云路径＝今天）| 单域复杂 / 多意图 / 简单跨域 | 1 | ~1–1.5s | 上云的绝大多数 |
| **T2 有界 Agentic 循环**（新增）| 自适应 / 看结果再决策 / 失败需备选 | 2~N（有界）| 有界，流式首响<1s | 复杂度分诊判为 adaptive，或 T1 反应式升级 |

三块增量：
1. **统一能力契约**：云 Agent / 车端快思考 / 工具都注册进 Registry，Planner 一视同仁当"工具"挑，差异下沉到 **UnifiedDispatcher** 按 `deployment/kind` 路由。
2. **意图理解 + 复杂度分诊**：首次 planning call 同时产出 `{plan, complexity, goal}`；simple→T1、adaptive→T2；保留**反应式兜底**（simple 计划执行失败/标 `replan` 时升级 T2）。
3. **调用车端快思考**：`channel.proto` 加 `EdgeCall/EdgeResult` 两类帧，中枢可把计划里某步下发到**该车**的快执行器，经 **VAL** 执行并把结果回流供后续步骤依赖。车控仍只产 intent，执行权牢牢在车端 VAL（守 §9.1 P0 红线）。

**不变量（任何实现违反即视为 bug）**：T0/T1 路径与行为与今天完全一致；车控只经 VAL；危险动作二次确认；循环有界可审计；新增任何调度目标不改编排核心。

---

## 1. 现状与证据

### 1.1 云端"慢思考"今天是什么
- **单次 LLM → 静态 DAG → 执行 → 聚合**：`planning.py:72` `PlanBuilder.build()` 一次 LLM 调用（最多重试 1 次，`planning.py:87`）把 Agent 能力当工具，产出 `{steps:[…]}` JSON DAG；解析校验在 `planning.py:109`。
- **执行**：`executor.py:16` `DagExecutor` 做 Kahn 拓扑分层（`executor.py:171`）+ 层内 `asyncio.gather` 并行（`executor.py:46`）+ 超时/部分失败（`executor.py:79`、`_mark_skipped` `executor.py:158`）。
- **主循环**：`engine.py:63` `_orchestrate` 串规划→解析 endpoint→权限→执行→聚合；多轮确认/补槽挂起在 `engine.py:181` `_suspend`；单步开放域**流式直通**在 `engine.py:132`（D0 段，已实现"边想边说"）。
- **聚合**：`aggregator.py` 对多 step 用 LLM 合成连贯话术。

### 1.2 调度目标今天只有"云 Agent"
- Planner 只从 Registry 拿到的 Agent 里选（`engine.py:114` `list_agents`）；`clients.py:98` `call_agent` 直连 Agent gRPC endpoint。
- **车端不是可调度目标**：今天是单向的——端侧先判本地/上云（`edge/server.py:35` `Handle`），云端产出的 `vehicle.control` action 作为**终态结果**回流，由 `edge/server.py:151` `_dispatch_cloud_actions` 交 VAL 执行。云端**不能在计划中途调用车端并拿回结果**。
- **没有"工具"概念**：一切皆 Agent（完整 gRPC 契约 `agent.proto:9`）。轻量函数/外部 API 也得做成重型 Agent。

### 1.3 已具备、可直接复用的地基
- `AgentManifest.deployment`（`agent.proto:26`，edge|cloud）**已存在**——`agents/navigation/manifest.yaml:6` 即 `deployment: cloud`。
- `ExecuteResponse.data`（`agent.proto:64`）、`missing_slots`（`agent.proto:65`）已存在，结构化结果与缺槽已打通。
- 端云 **bidi 帧协议** `EdgeCloudChannel.Connect`（`channel.proto:6`）已在跑，且有 `Proactive`（`channel.proto:50`）云→端主动推送先例。
- 车端 **VAL** 已是完整流水线：归一化→校验→**安全门控**（`val.py:234`）→**二次确认判定**（`val.py:266`）→模拟→话术（`val.py:103` `_structured_execute`）。
- 权限过滤 `planning.py:227` `_filter_by_permission`：fail-closed，且**硬禁 third_party 碰 `vehicle.control`**（`planning.py:244`）——工具沙箱可直接复用。

---

## 2. 问题

1. **复杂自适应意图处理不了**：静态 DAG 一次成型，无法"看上一步结果再决定下一步"（如：查到充电站满了→自动找次近的；订位失败→换一家）。
2. **车端快思考不可被中枢编排**：跨域计划里若含车端步骤（且后续步骤依赖其结果），今天做不到；车控 action 只能当终态返回。
3. **无工具抽象**：轻量能力被迫做成 Agent，或干脆缺失。
4. **意图理解隐式**：复杂度不被显式评估，无法据此分级投入算力/时延。

---

## 3. 目标与非目标

### 3.1 目标
1. 中枢能**显式判定复杂度**并据此分级（T1 直执行 / T2 循环），常见请求延迟零回退。
2. 中枢能在一个计划里**异构调度**云 Agent、车端快思考、工具，三者对 Planner 透明。
3. 对自适应复杂意图，中枢能**有界地**规划→执行→观察→再规划，并保证延迟可控、可审计。
4. 全程守住架构 P0：车控经 VAL、危险动作二次确认、隐私最小化、可观测。

### 3.2 非目标（YAGNI / 架构红线）
- **不做**自由 Agent 协商 / 自组织（架构 §1.3）。T2 是**有界、被监督**的循环，不是放飞。
- **不让** LLM 直连车控（架构 §5.3）。中枢对车控只产 `AgentAction(intent)`。
- 本期工具**只做进程内确定性工具**（日期/单位/计算等）；HTTP/MCP 外部工具留后续。
- 不改 T0 端侧快路径的判定逻辑（多意图切分等已在 `fast_intent.py`，本设计不动）。

---

## 4. 总体方案

### 4.0 模块落点（基本都是扩展现有文件）

| 模块 | 落点 | 职责 | 新增/扩展 |
|---|---|---|---|
| Understanding + Planner | `planning.py` | 一次 LLM 产出 `{steps, complexity, goal}`；提供 `replan()` | 扩展 |
| Conductor（分诊台）| `engine.py` | 读 complexity 分派 T1/T2；反应式升级 | 扩展 |
| DagExecutor | `executor.py` | 单批 DAG 拓扑并行（T1 与 T2 每轮复用）| 不动 |
| **LoopController** | **新增 `loop.py`** | T2：迭代上限/时间预算/观察累积/再规划/流式 | 新增 |
| **UnifiedDispatcher** | **新增 `dispatch.py`** + `clients.py` | 按 `deployment/kind` 路由 cloud gRPC / edge bidi / tool | 新增 |
| **ToolRegistry** | **新增 `tools/`** | 进程内确定性工具 + 轻量 manifest | 新增 |
| Capability Registry | `registry/` | 索引纳入 edge/tool 能力 | 扩展 |
| Cloud Gateway 边车调度 | `gateway/cloud/` | 持有各车 bidi 流，暴露 `DispatchToEdge` 给编排器 | 扩展 |
| Edge edge_call 处理 | `edge/server.py` + `edge/cloud_client.py` | 收云端 `EdgeCall`→VAL→回 `EdgeResult` | 扩展 |

### 4.1 运行模型与请求生命周期

```
HMI/语音 → Edge Gateway → Edge Orchestrator → fast_intent
  ├─ 全部子意图命中本地 → 【T0】本地执行经 VAL → 秒回                （今天，不变）
  └─ 含非本地意图 → 整句上云 → Cloud Gateway → 【中枢 engine】
        Understanding+Planner（1 次 LLM）→ {steps(DAG), complexity, goal}
        ├─ complexity=simple → 【T1】DagExecutor 执行 → Aggregator → 流式输出
        │     └ 反应式兜底：某步 FAILED 有备选 / 结果含 data.replan=true → 升级 T2（已得结果作观察种子）
        └─ complexity=adaptive → 【T2 LoopController】
              loop（迭代 ≤MAX_ITERS，总预算 ≤BUDGET_MS）:
                执行本批（DagExecutor）→ 观察结果累积
                NEED_CONFIRM/NEED_SLOT? → 复用 engine._suspend 挂起，return
                再规划 replan(goal, 观察) → 返回 {done:true} 或下一批 steps
                done 或 预算耗尽 → break
              Aggregator → 流式输出

  每个 step 经 UnifiedDispatcher：
    deployment=cloud → 直连 Agent gRPC（今天 clients.call_agent）
    deployment=edge  → Cloud Gateway.DispatchToEdge(vehicle_id, EdgeCall) → 该车 → VAL → EdgeResult 回流
    kind=tool        → ToolRegistry 进程内调用
  车控类 step：中枢只产 intent → 车端 VAL 权限+安全态+二次确认 → 执行
```

**延迟可控三招**（落地必须实现，否则等于 loop-at-top）：
1. **分诊折叠进首个 planning call，零额外往返**：simple 直接执行＝今天的延迟。
2. **循环是例外路径**：常见请求根本不进 T2，P50 不变。
3. **流式首响 + 硬上限/预算**：复用 `engine.py:132` 流式直通能力，T2 进入即吐占位话术；`MAX_ITERS`/`BUDGET_MS` 到点返回 best-effort + "要我继续吗"。

> 建议初值（可 OTA/配置调）：`MAX_ITERS=2`（含首批共 3 批）、`BUDGET_MS=5000`、流式首 token 目标 <1s。

### 4.2 统一能力契约

**Manifest 加一个字段**（`agent.proto` AgentManifest）：
```proto
string kind = 12;   // "agent"（默认）| "tool" | "edge_fast"
```
- `deployment`（已存在，field 6）决定**传输**：cloud→直连 gRPC；edge→经车 bidi 通道。
- `kind` 决定**调度语义/治理**：`tool` 走 ToolRegistry、不可碰车控；`edge_fast` 是车端快执行器暴露的能力（车控/媒体），结果经 VAL。
- Planner 侧**完全无感**：`planning.py:195` `_build_catalog` 照旧把所有能力当工具列给 LLM。差异只在 UnifiedDispatcher。

**UnifiedDispatcher**（新增 `dispatch.py`，executor 通过它发起每个 step 的调用，替换今天 `executor.__init__` 注入的 `call_agent_fn`）：
```python
async def dispatch(step, ctx) -> ExecuteResponse:
    if step.kind == "tool":            return await tool_registry.call(step.intent, step.slots, ctx)
    if step.deployment == "edge":      return await edge_dispatch(ctx.vehicle_id, step, ctx)   # §4.5
    return await clients.call_agent(step.endpoint, step.intent, step.slots, ctx, step.meta)    # 今天
```
- `Step`（`models.py:18`）增加 `kind: str = "agent"`、`deployment: str = "cloud"`（解析时从 manifest 带入，`planning.py:141` 处填充）。
- **关键复用**：三条路径都返回 `ExecuteResponse` 语义，`executor.py:122` `_to_result` 不改即可统一转 `StepResult`。

### 4.3 意图理解 + 复杂度分诊

**planner prompt 扩展**（`planning.py:12` `_PLANNER_SYSTEM`）：在现有 DAG 输出上加顶层字段——
```json
{"complexity":"simple|adaptive","goal":"<一句话用户目标>","steps":[ … 同今天 … ]}
```
判定规则写进 prompt：
- **simple**：一次就能把步骤定全（单域、多意图并行、固定串行如搜→订），后续步骤不依赖"运行时才知道的结果分支"。
- **adaptive**：需要看中间结果才能决定后续（"满了就换次近的"、"订不上就换一家"、探索式查询）。
- `goal` 是 T2 再规划的锚点；simple 时可空。

**解析**（`planning.py:109` `_parse_and_validate`）：读出 `complexity`/`goal` 挂到 `Plan`（`models.py:51` 增 `complexity: str="simple"`、`goal: str=""`）。解析失败/缺字段→默认 `simple`（保守，不误入循环）。

**反应式兜底**（`engine.py` Conductor）：被判 simple 的计划，若执行中**某步 FAILED 且该意图有其他候选 Agent**，或某步 `data.replan==true`，则把已得结果作为观察种子，升级进 T2。这条覆盖"预判错"的风险（B 方案唯一短板）。

### 4.4 T2 有界 Agentic 循环（新增 `loop.py`）

```python
class LoopController:
    async def run(self, goal, plan, ctx, seed_results=None) -> AsyncIterator[dict]:
        observations = [summarize(r) for r in (seed_results or [])]
        deadline = now() + BUDGET_MS
        cur_plan = plan
        for it in range(MAX_ITERS + 1):
            if it > 0:
                yield {"kind":"speech","delta": THINKING_FILLER}   # 流式首响，仅首次
                decision = await planner.replan(goal, observations, agents, ctx)
                if decision.done or not decision.steps:
                    break
                cur_plan = decision.to_plan()
            async for sr in executor.run(cur_plan, ctx):
                observations.append(summarize(sr))
                if sr.status in (NEED_CONFIRM, NEED_SLOT):
                    yield await engine._suspend(sr, materialize(observations), cur_plan, ctx)  # 复用 F1 闭环
                    return
            if now() >= deadline:
                break
        final = await aggregator.compose(goal, materialize(observations))
        yield {"kind":"final", **final}
```

要点：
- **再规划即"是否完成"判定**：`planner.replan()` 一次 LLM 调用既决定"还要不要继续"（`done`）又给出"下一批 steps"——**不额外加 LLM 往返**（与 4.3 分诊同一设计哲学）。
- **观察压缩**：`summarize(sr)` 只留 `{intent, status, 关键 data 字段, 短 speech}`；只喂最近 K 条，控 token 膨胀（架构 §10 token 成本）。
- **确认/补槽不被碾过**：循环内遇 `NEED_CONFIRM/NEED_SLOT` 立即复用 `engine.py:181` `_suspend` 挂起，多轮闭环行为与 T1 完全一致（守 §9.1 危险动作二次确认）。
- **预算/上限**：`MAX_ITERS`、`BUDGET_MS` 双重护栏，到点 `aggregator` 出 best-effort + 追问"要不要继续"。
- **流式**：进入循环即吐占位话术；各 Agent 若支持流式，沿用 `clients.call_agent_stream`（`clients.py:105`）透传 speech delta。

### 4.5 调用车端快思考（最关键、最复杂）

**语义**：中枢把计划里 `deployment=edge` 的 step 下发到**发起本会话那台车**的快执行器，经 VAL 执行，结果回流中枢供后续步骤依赖。两种用法：① 中途执行车端动作并拿回结果；② 读车端实时状态（电量/车速/位置）辅助规划。

**proto 扩展**（`channel.proto`，复用已有 bidi 与 correlation_id）：
```proto
import "cockpit/common/v1/common.proto";
import "cockpit/agent/v1/agent.proto";

// DownFrame.body 增（云→端）
EdgeCall edge_call = 6;
// UpFrame.body 增（端→云）
EdgeResult edge_result = 6;

message EdgeCall  {
  string step_id = 1;
  cockpit.common.v1.Intent intent = 2;     // name + slots（车控/媒体/状态查询）
  map<string,string> meta = 3;             // trace_id, answer_length, confirmed …
}
message EdgeResult {
  string step_id = 1;
  cockpit.agent.v1.ExecuteResponse result = 2;  // 复用 Agent 执行结果语义（status/speech/data/actions）
}
```
> 复用 `ExecuteResponse` 是关键：EdgeResult 回到编排层后直接走 `executor.py:122` `_to_result`，与云 Agent 结果同构——`NEED_CONFIRM` 等状态天然兼容多轮闭环。

**传输路径**（per-vehicle 路由）：
```
中枢 executor → UnifiedDispatcher.edge_dispatch(vehicle_id, EdgeCall)
   → gRPC 调 Cloud Gateway.DispatchToEdge(vehicle_id, EdgeCall)    （新增内部 RPC，gateway 持有各车流）
   → Gateway 按 vehicle_id 找到该车 DownFrame 流 → 写 DownFrame{edge_call}
   → Edge cloud_client 读到 edge_call → 交 edge 执行器：复用 server._dispatch 逻辑 → VAL.execute()
   → Edge 回 UpFrame{edge_result} → Gateway 按 correlation/step_id 配对 → 返回 EdgeResult
   → Dispatcher 转 ExecuteResponse → _to_result → StepResult
```
- **新增内部接口**：Cloud Gateway（Go，持有 `vehicle_id → 活跃 DownFrame 流` 映射）暴露 `rpc DispatchToEdge(EdgeCallEnvelope) returns (EdgeResult)`（`EdgeCallEnvelope{vehicle_id, EdgeCall}`），供编排器（Python）调用。这是唯一的新跨服务接口。
- **Edge 侧**：`cloud_client.py:30` 的读循环增 `edge_call` 分支；执行复用 `server.py:151` `_dispatch_cloud_actions` 的 VAL 路径（车控→`val.execute`，含安全门控/确认）。
- **车端能力注册**：Edge 启动时向 Registry 注册一个 `deployment=edge, kind=edge_fast` 的 manifest（车控/媒体能力），使 Planner 能把它当工具选中。注册可经 Cloud Gateway 代理（端不直连 Registry）。

**安全（P0，焊死）**：
- 中枢对车控**只产 `Intent`**，绝不产 CAN/SOME-IP；执行权在 VAL。EdgeCall 携带的是意图+槽位，VAL 做合法性/权限/安全态/确认（`val.py:194/234/266`）。
- `EdgeResult.status==NEED_CONFIRM` → 中枢按 §4.4 挂起，走二次确认。
- 车端能力的 `requires_permissions` 仍受 `planning.py:227` 过滤；third_party 永不可得 `vehicle.control`（`planning.py:244`）。

**降级**：`DispatchToEdge` 超时 / 该车无活跃流 → 该 step `StepResult.FAILED`；计划按既有部分失败逻辑（`executor.py:158`）聚合，话术告知"车端这步没能完成"。注意：纯车控本应走 T0 端侧快路径离线可用；走到这里说明是"复杂请求里夹带车端步骤"且通道异常，属罕见边界。

### 4.6 工具调度（进程内确定性工具）

- **ToolRegistry**（`tools/`）：注册 Python 可调用 + 轻量 manifest（`kind=tool`、`requires_permissions`、capabilities）。启动时把工具 manifest 一并注入 Registry 能力索引，Planner 一视同仁。
- **运行**：UnifiedDispatcher 的 `kind=tool` 分支进程内直接调用，包成 `ExecuteResponse`（填 `data`/`speech`）。
- **沙箱**：工具**不得**声明/执行 `vehicle.control`（dispatcher 侧硬校验 + 复用权限过滤）；外部 HTTP 工具需 `network.external` 且走白名单（本期不做，留 §7 P3）。
- **本期工具示例**：`datetime.parse`（相对时间→绝对）、`unit.convert`、`math.eval`。够支撑"订今晚7点"这类槽位归一化，避免每次都丢给大模型。

---

## 5. 数据模型与接口变更（汇总）

| 文件 | 变更 | 影响面 |
|---|---|---|
| `proto/cockpit/agent/v1/agent.proto` | `AgentManifest` 加 `string kind = 12;` | 注册/路由；向后兼容（默认空＝agent）|
| `proto/cockpit/channel/v1/channel.proto` | `DownFrame` 加 `EdgeCall edge_call=6`；`UpFrame` 加 `EdgeResult edge_result=6`；新增 2 message + 2 import | 云↔端调用通道 |
| `proto/cockpit/channel/v1/channel.proto` 或 gateway 专用 proto | 新增 `rpc DispatchToEdge(EdgeCallEnvelope) returns (EdgeResult)` | 编排器↔Gateway |
| `orchestrator/cloud/models.py` | `Step` 加 `kind/deployment`；`Plan` 加 `complexity/goal` | 编排内部 |
| `orchestrator/cloud/planning.py` | prompt 加 complexity/goal；解析读出；新增 `replan()` | 规划 |
| `orchestrator/cloud/engine.py` | Conductor 分派 T1/T2 + 反应式升级 | 主循环 |
| `orchestrator/cloud/dispatch.py`（新）| UnifiedDispatcher 三路路由 | 执行 |
| `orchestrator/cloud/loop.py`（新）| LoopController | T2 |
| `orchestrator/cloud/tools/`（新）| ToolRegistry + 内置工具 | 工具 |
| `orchestrator/edge/cloud_client.py`、`server.py` | 处理 `edge_call`→VAL→`edge_result` | 端侧 |
| `gateway/cloud/` | 持有车流映射 + `DispatchToEdge` | 网关 |
| `registry/` | 索引 edge/tool 能力 | 注册 |

> 改 proto 后必跑 `make proto`（Windows：`scripts/gen-proto.ps1`）。

---

## 6. 安全、降级、可观测

### 6.1 安全（守架构 §9）
- **车控唯一路径＝VAL**：中枢/循环/工具都不得产生车身信号；edge step 经 VAL 全套门控。
- **规划/执行分离**：LLM 只产意图与"下一批做什么"，执行由确定性 executor + dispatcher 完成。
- **危险动作二次确认**：T1/T2/edge 三路遇 `NEED_CONFIRM` 统一走 `engine._suspend`。
- **工具沙箱**：`kind=tool` 禁车控、外部出口白名单；third_party 复用既有硬禁令。
- **循环有界可审计**：`MAX_ITERS`/`BUDGET_MS` + 每轮 trace，杜绝放飞（守 §1.3 非目标）。

### 6.2 降级矩阵（在架构 §3.3 基础上补充）
| 故障 | 降级 |
|---|---|
| 复杂度误判为 simple | 反应式升级 T2（§4.3）|
| T2 预算耗尽 | 返回 best-effort 聚合 + "要不要继续"|
| 车端通道不可达（edge step）| 该 step FAILED，部分聚合，话术告知 |
| 工具异常 | 该 step FAILED，Planner 下一轮可绕开 / 兜底话术 |
| replan LLM 失败 | 退出循环，用已有观察聚合 |

### 6.3 可观测（架构 §10）
- **Trace**：每轮迭代一个 span；记录 `complexity` 决策、迭代数、预算消耗、升级触发、edge/tool 调用耗时。
- **新增指标**：T1/T2 分流率、循环迭代分布、反应式升级率、`edge_dispatch` 时延/失败率、工具时延、预算超限率。
- **日志**：敏感字段（位置/支付）脱敏；edge step 落审计链（谁在何状态下令车端做了什么）。

---

## 7. 分阶段落地（按此顺序执行）

> 原则：每阶段独立可验证、可回归、不破坏 T0/T1 既有行为。**P1 是泓舟指定"最关键最复杂、先完成"的部分**，故 P0 只做其最小前置。

### P0 — 统一契约地基（最小前置，无行为变化）
- [ ] `agent.proto` 加 `kind`；`make proto`/`gen-proto.ps1` 重新生成。
- [ ] `models.py`：`Step` 加 `kind/deployment`，`planning.py:141` 解析时从 manifest 填入。
- [ ] 新增 `dispatch.py` `UnifiedDispatcher`：cloud 路径＝今天行为；edge/tool 先占位（未实现报明确错误）。`executor` 改为经 dispatcher 调用。
- [ ] 回归：`orchestrator/cloud/tests/` 全绿，行为零变化。

### P1 — 调用车端快思考（核心，先完成）
- [ ] `channel.proto` 加 `EdgeCall/EdgeResult` 帧 + import；`gateway` 专用 `DispatchToEdge`；重生成。
- [ ] Cloud Gateway：维护 `vehicle_id→DownFrame 流`；实现 `DispatchToEdge`（写 edge_call、按 step_id/correlation 配对收 edge_result、超时处理）。
- [ ] Edge：`cloud_client.py` 读循环加 `edge_call` 分支；复用 `server._dispatch`→`val.execute` 执行并回 `edge_result`。
- [ ] Edge 启动注册 `deployment=edge, kind=edge_fast` manifest（车控/媒体能力）到 Registry。
- [ ] `dispatch.py` 实现 edge 路由 + 降级（通道不可达→FAILED）。
- [ ] 测试：①Gateway 配对/超时单测 ②Edge `edge_call`→VAL 单测（含安全门控拒绝、NEED_CONFIRM）③e2e：一个含 edge step 的云计划端到端跑通，结果回流被后续 step 依赖。

### P2 — T2 有界循环
- [ ] `planning.py`：prompt 加 complexity/goal；解析；新增 `replan()`。
- [ ] `engine.py`：Conductor 按 complexity 分派；反应式升级。
- [ ] 新增 `loop.py` LoopController（迭代/预算/观察压缩/`_suspend` 复用/流式占位）。
- [ ] 测试：adaptive 黄金用例（满了换次近、订不上换家）；NEED_CONFIRM 在循环内正确挂起；预算超限返回 best-effort；simple 不进循环（P50 无回退）。

### P3 — 工具
- [ ] `tools/` ToolRegistry + 内置确定性工具（datetime/unit/math）+ 轻量 manifest 注入 Registry。
- [ ] `dispatch.py` tool 路由 + 沙箱校验（禁车控）。
- [ ] 测试：工具被 Planner 选中并执行；third_party/工具不可得 vehicle.control。
- [ ] （后续）HTTP/MCP 外部工具 + 出口白名单。

---

## 8. 验收标准

- **统一契约**：新增一个 `kind=tool` 或 `deployment=edge` 能力，**不改 `engine.py`/`planning.py` 核心**即被 Planner 调度（守架构 §4）。
- **车端可调度**：云计划 `[edge:hvac.set] → [cloud:确认话术]` 端到端通过，edge step 结果可被后续 step 依赖；车控全程经 VAL（断点验证无旁路）。
- **延迟可控**：simple 请求 P50 与改造前持平（无回退）；adaptive 首 token <1s、总时长 ≤`BUDGET_MS`；超限有 best-effort 返回。
- **安全**：循环内 `NEED_CONFIRM` 正确挂起并二次确认；工具/ third_party 无法执行 `vehicle.control`（用例验证 REJECTED）。
- **回归**：`python -m pytest orchestrator/ --import-mode=importlib` 全绿；`make e2e` 既有 4 条链路不破。

---

## 9. 风险与未决项

| # | 风险/未决 | 影响 | 建议 |
|---|---|---|---|
| R1 | T2 延迟尾部 | 体验 | 严守 MAX_ITERS/BUDGET_MS + 流式首响；复杂度误判靠反应式兜底 |
| R2 | 复杂度预判不准 | 漏/误入循环 | adaptive 偏保守；反应式升级覆盖漏判；积累用例回流调 prompt |
| R3 | Cloud Gateway 状态化（持车流）| 扩展性 | 单车流亲和；多实例时按 vehicle_id 路由到持流实例（一致性哈希/会话亲和），列为 P1 设计点 |
| R4 | edge step 与 T0 快路径职责重叠 | 认知成本 | 明确：T0＝端自主秒回；edge step＝中枢编排下发。二者都经 VAL，差别在"谁发起" |
| R5 | 观察累积 token 膨胀 | 成本/时延 | summarize 压缩 + 最近 K 条窗口 |
| R6 | replan 抖动（反复改主意）| 体验/成本 | replan prompt 要求"仅在必要时改计划"；迭代上限兜底 |
| R7 | 真实 VAL（Phase 2 接车）后 edge_call 时延 | 体验 | edge step 设独立 `latency_budget_ms`；超时降级 |

---

## 落地记录

（按阶段在此追加：日期、范围、验证结果、已知边界。）
