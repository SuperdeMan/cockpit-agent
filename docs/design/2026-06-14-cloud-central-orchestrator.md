# 云端慢思考中枢：理解 → 规划 → 异构调度（车端快思考 / Agent / 工具）

- **状态**：已落地，持续维护（2026-06-14）
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
| **T1 单次 DAG**（默认云路径＝2026-06-14 设计基线）| 单域复杂 / 多意图 / 简单跨域 | 1 | ~1–1.5s | 上云的绝大多数 |
| **T2 有界 Agentic 循环**（新增）| 自适应 / 看结果再决策 / 失败需备选 | 2~N（有界）| 有界，流式首响<1s | 复杂度分诊判为 adaptive，或 T1 反应式升级 |

三块增量：
1. **统一能力契约**：云 Agent / 车端快思考 / 工具都注册进 Registry，Planner 一视同仁当"工具"挑，差异下沉到 **UnifiedDispatcher** 按 `deployment/kind` 路由。
2. **意图理解 + 复杂度分诊**：首次 planning call 同时产出 `{plan, complexity, goal}`；simple→T1、adaptive→T2；保留**反应式兜底**（simple 计划执行失败/标 `replan` 时升级 T2）。
3. **调用车端快思考**：`channel.proto` 加 `EdgeCall/EdgeResult` 两类帧，中枢可把计划里某步下发到**该车**的快执行器，经 **VAL** 执行并把结果回流供后续步骤依赖。车控仍只产 intent，执行权牢牢在车端 VAL（守 §9.1 P0 红线）。

**不变量（任何实现违反即视为 bug）**：T0/T1 路径与行为与 2026-06-14 设计基线一致；车控只经 VAL；危险动作二次确认；循环有界可审计；新增任何调度目标不改编排核心。

---

## 1. 现状与证据

### 1.1 云端"慢思考"的 2026-06-14 设计基线
- **单次 LLM → 静态 DAG → 执行 → 聚合**：`planning.py:72` `PlanBuilder.build()` 一次 LLM 调用（最多重试 1 次，`planning.py:87`）把 Agent 能力当工具，产出 `{steps:[…]}` JSON DAG；解析校验在 `planning.py:109`。
- **执行**：`executor.py:16` `DagExecutor` 做 Kahn 拓扑分层（`executor.py:171`）+ 层内 `asyncio.gather` 并行（`executor.py:46`）+ 超时/部分失败（`executor.py:79`、`_mark_skipped` `executor.py:158`）。
- **主循环**：`engine.py:63` `_orchestrate` 串规划→解析 endpoint→权限→执行→聚合；多轮确认/补槽挂起在 `engine.py:181` `_suspend`；单步开放域**流式直通**在 `engine.py:132`（D0 段，已实现"边想边说"）。
- **聚合**：`aggregator.py` 对多 step 用 LLM 合成连贯话术。

### 1.2 设计基线中的调度目标只有"云 Agent"
- Planner 只从 Registry 拿到的 Agent 里选（`engine.py:114` `list_agents`）；`clients.py:98` `call_agent` 直连 Agent gRPC endpoint。
- **车端不是可调度目标**：设计基线是单向的——端侧先判本地/上云（`edge/server.py:35` `Handle`），云端产出的 `vehicle.control` action 作为**终态结果**回流，由 `edge/server.py:151` `_dispatch_cloud_actions` 交 VAL 执行。云端**不能在计划中途调用车端并拿回结果**。
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
2. **车端快思考不可被中枢编排**：跨域计划里若含车端步骤（且后续步骤依赖其结果），设计基线做不到；车控 action 只能当终态返回。
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
- 云端中枢 P0-P3 主体不重写 T0 判定；后续体验修复已在不绕过 VAL 的前提下扩展
  混合意图语义组分流，详见本文落地记录。

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
  ├─ 完全本地且无需确认的语义组 → 【T0】本地执行经 VAL → 秒回
  └─ 慢意图/需确认语义组（主句+续接限定）→ Cloud Gateway → 【中枢 engine】
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
    deployment=cloud → 直连 Agent gRPC（设计基线为 clients.call_agent）
    deployment=edge  → Cloud Gateway.DispatchToEdge(vehicle_id, EdgeCall) → 该车 → VAL → EdgeResult 回流
    kind=tool        → ToolRegistry 进程内调用
  车控类 step：中枢只产 intent → 车端 VAL 权限+安全态+二次确认 → 执行
```

**延迟可控三招**（落地必须实现，否则等于 loop-at-top）：
1. **分诊折叠进首个 planning call，零额外往返**：simple 直接执行＝设计基线延迟。
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

**UnifiedDispatcher**（新增 `dispatch.py`，executor 通过它发起每个 step 的调用，替换设计基线中 `executor.__init__` 注入的 `call_agent_fn`）：
```python
async def dispatch(step, ctx) -> ExecuteResponse:
    if step.kind == "tool":            return await tool_registry.call(step.intent, step.slots, ctx)
    if step.deployment == "edge":      return await edge_dispatch(ctx.vehicle_id, step, ctx)   # §4.5
    return await clients.call_agent(step.endpoint, step.intent, step.slots, ctx, step.meta)    # 设计基线
```
- `Step`（`models.py:18`）增加 `kind: str = "agent"`、`deployment: str = "cloud"`（解析时从 manifest 带入，`planning.py:141` 处填充）。
- **关键复用**：三条路径都返回 `ExecuteResponse` 语义，`executor.py:122` `_to_result` 不改即可统一转 `StepResult`。

### 4.3 意图理解 + 复杂度分诊

**planner prompt 扩展**（`planning.py:12` `_PLANNER_SYSTEM`）：在现有 DAG 输出上加顶层字段——
```json
{"complexity":"simple|adaptive","goal":"<一句话用户目标>","steps":[ … 同设计基线 … ]}
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
- [x] `agent.proto` 加 `kind`；`make proto`/`gen-proto.ps1` 重新生成。
- [x] `models.py`：`Step` 加 `kind/deployment`，`planning.py:141` 解析时从 manifest 填入。
- [x] 新增 `dispatch.py` `UnifiedDispatcher`：cloud 路径保持设计基线行为；edge/tool 先占位（未实现报明确错误）。`executor` 改为经 dispatcher 调用。
- [x] 回归：`orchestrator/cloud/tests/` 全绿，行为零变化。

### P1 — 调用车端快思考（核心，先完成）
- [x] `channel.proto` 加 `EdgeCall/EdgeResult` 帧 + import；`gateway` 专用 `DispatchToEdge`；重生成。
- [x] Cloud Gateway：维护 `vehicle_id→DownFrame 流`；实现 `DispatchToEdge`（写 edge_call、按 step_id/correlation 配对收 edge_result、超时处理）。
- [x] Edge：`cloud_client.py` 读循环加 `edge_call` 分支；复用 `server._dispatch`→`val.execute` 执行并回 `edge_result`。
- [x] Edge 启动注册 `deployment=edge, kind=edge_fast` manifest（车控/媒体能力）到 Registry。
- [x] `dispatch.py` 实现 edge 路由 + 降级（通道不可达→FAILED）。
- [x] 测试：Gateway 配对/超时、Edge `edge_call`→VAL、编排层 edge dispatch 集成均已覆盖。

### P2 — T2 有界循环
- [x] `planning.py`：prompt 加 complexity/goal；解析；新增 `replan()`。
- [x] `engine.py`：Conductor 按 complexity 分派；反应式升级。
- [x] 新增 `loop.py` LoopController（迭代/预算/观察压缩/`_suspend` 复用/流式占位）。
- [x] 测试：adaptive、NEED_CONFIRM、预算超限、simple 路径和流式直通均已覆盖。

### P3 — 工具
- [x] `tools/` ToolRegistry + 内置确定性工具（datetime/unit/math）+ 轻量 manifest 注入 Registry。
- [x] `dispatch.py` tool 路由 + 沙箱校验（禁车控）。
- [x] 测试：工具执行和 `vehicle.control` 拒绝均已覆盖。
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

### 2026-06-14 代码落地（验收通过）

**已实现**

- P0：`AgentManifest.kind`、`Step.kind/deployment/required_permissions/trust_level`、`Plan.complexity/goal`、统一 `UnifiedDispatcher`。
- P1：`EdgeCall/EdgeResult/EdgeCallEnvelope` 与 `DispatchToEdge`；Cloud Gateway 按 `vehicle_id + correlation_id` 配对；请求车辆与握手车辆强绑定（`bindRequestVehicle`）；Edge 在同一 bidi 流处理 `edge_call`，经 VAL 返回结果；端侧车控/媒体 manifest 注册；危险动作沿用 `NEED_CONFIRM` 闭环。
- P2：首次规划输出 `simple/adaptive`；新增有界 `LoopController`、观察压缩、迭代/时间双预算、反应式升级、跨批次结果依赖与确认/补槽挂起恢复；**T2 流式直通**（循环内单步 cloud agent 尝试 `call_agent_stream`，yield speech delta，失败回退 executor）。
- P3：进程内 `datetime.parse`、`unit.convert`、`math.eval` 工具及 manifest 注册；工具禁止声明/执行 `vehicle.control`。
- 安全补强：规划与执行双层 permission scope 校验，支持父 scope 覆盖子 scope；third-party 车控硬拒绝；Edge 畸形回包拒绝；所有云调度车控仍只发送 intent，执行只经过 VAL。
- **PoC 权限入口**：`engine._build_context` 在 `granted_scopes` 缺失时注入 PoC 默认 scope（`vehicle.control, media.control, navigation, ...`），带 warning 日志标记”量产必须从 token 解析”。显式 `granted_scopes` 优先。
- **可观测接线**：`dispatch.py` 全路径接入 `MetricsCollector.record_agent_call`（时延/成功）；权限拒绝走 `AuditLogger.permission_denied`；`engine.py` 记录 complexity 分诊（`complexity.simple/adaptive`）和 reactive upgrade；`loop.py` 记录 T2 循环时延与耗尽状态。
- **混合意图执行**：`fast_intent.split_and_classify_any` 拆分全部子意图（不做全有全无过滤）；`server.py` 混合路径：本地意图经 VAL 秒回，非本地意图上云编排。
- **多步执行中间反馈**：`engine.py` 规划完成后 yield "正在处理"，每步完成后 yield 该步 speech。
- **TTS 语音输入时停止**：`Composer.tsx` 的 `beginRecord()` 调用 `stopTTS()`。
- **Planner 隐式车控增强**：prompt 增加"隐式车控必须识别"规则（"再高/低一点""我冷/热"→映射为 hvac.inc/dec/set）。
- **DispatchToEdge proto codegen 修复**：`gen/go/` 重新生成，cloud-gateway 重建。

**该阶段测试证据（2026-06-14 历史基线）**

- `python -m pytest --import-mode=importlib`：**312 passed, 2 skipped**（该阶段当时全量；
  当前结果见本文最后一条落地记录）。
- 其中 `orchestrator/` 212 passed（含 T2 流式 4 条、PoC 默认 scope 2 条、混合意图 5 条、中间反馈 1 条）。
- 其中 `test/` 46 passed, 2 skipped。
- 其他服务（agents/llm-gateway 等）54 passed。
- `python test/smoke_edge.py`：**13 passed, 0 failed**（快意图/VAL/edge_execute 全链路）。
- `python test/e2e_ws.py`：4 条链路全部跑通（车控/多意图/闲聊/预订确认），E2E 逻辑正确。
- `test_all_registered_edge_intents_are_executable_through_val`：71 条意图（67 vehicle + 4 media）全部经 `EdgeCallExecutor → VAL` 成功或正确进入 `NEED_CONFIRM`。
- `test_hierarchical_edge_intents_update_the_expected_val_state`：10 层级意图（`aircon.wind_speed.set`、`screen.brightness.set/inc`、`steering_wheel.height.set`、`steering_wheel.heating.open/close`）映射正确。
- Gateway `bindRequestVehicle`：跨车辆请求拒绝（`PermissionDenied`）、握手车辆身份填充。
- Go 单测：配对/超时/跨车辆 3 条测试。
- Proto codegen：`EdgeCall`、`EdgeResult`、`EdgeCallEnvelope` 在 Go/Python 生成代码中均存在。

**已知边界（不在本迭代范围）**

- Cloud Gateway 多实例扩展性（车流状态在单实例内存）。
- HTTP/MCP 外部工具。
- HMI 侧 `granted_scopes` 的真实 token 解析（当前用 PoC 默认值）。
- Prometheus/OTel metrics export 端点（当前仅内存快照）。
- 编排路径 `start_span()` 接入（tracing 基础设施已有，未调用）。
- 真正的服务端 PCM 流式 TTS（句子级增量合成与顺序播放已在 HMI 落地）。
- Docker 部署需将 `.env` 复制到 `deploy/` 目录（docker compose 从 `deploy/` 运行，读取该目录下的 `.env`）。

### 2026-06-14 体验修复（混合意图中间态 / 多轮车控执行 / 全部 review 项）

**背景**：联调发现两个体验问题——① 混合意图下慢意图无中间态反馈，结果在模型跑完后突然出现；② 多轮中被中枢判为车控的跟进句（"再高一点""我好冷"）看起来"没执行"（无动作卡、话术空泛）。借此对中枢做了一轮 code review，并落地全部修复项。

**根因**
- case1：HMI 流式状态机一轮仅一个 pending 占位；混合路径先为本地段 yield 终态 `final`，HMI 清空 `pendingIdRef`，导致云段 `speech_delta` 无处归属被丢、`final` 突现。
- case2：① 云端 edge_call（`EdgeCallExecutor`）执行车控后不回 `AgentAction` → HMI 无动作卡；② VAL 的 `aircon.inc/dec` 落兜底分支，温度原地不动且话术走 `generic_success`。

**已修复**
- **R1** edge_call 回动作卡 + 防双发：`edge_call.py` 成功后回填 `AgentAction`（车控→`vehicle.control` / 媒体→`media.control`），payload 打 `_origin=edge_val`；`server._dispatch_cloud_actions` 跳过该标记（已在车端执行），仅展示不二次下发。
- **R2** VAL 相对调温：`val.py` aircon 增 inc/dec 真正 ±1 度（夹 16–32）并区分风速；新增 `hvac_inc/dec_success` 话术并回显目标温度。本地/云端两路同时受益。
- **R3** HMI 多段流式：`App.tsx` 对 `speech_delta`/`action` 在无活跃占位时新建气泡；新增 `action` 事件处理（此前被静默丢弃）。
- **R4** 端侧本地轮写记忆：edge orchestrator 给纯本地快路径 best-effort 异步写共享记忆（gated on `memory_enabled`，失败静默、不破坏离线），补齐云端跟进指代上下文。
- **R5** 映射收敛：`edge_call._to_structured` 对象白名单改由 VAL `commands.yaml` 提供（消除与知识库漂移），无知识库时回退内置集。
- **R6** 云段即时占位：混合路径本地 `final` 后立即下发占位 delta，并以 `_mixed_subrequest` meta 让云端不重复占位文案。
- **R8** 危险动作守红线：本地快路径命中 `require_confirm` 对象（trunk/door_lock/fuel_tank_cover/charging_port）不再秒回，落云端经 edge_call→`NEED_CONFIRM` 二次确认（此前本地路径直接执行，违反 CLAUDE.md §5 危险动作必确认）。

**测试证据**
- 新增 8 条单测（edge_call 动作卡/标记/相对调温、`_dispatch` 跳过/执行、`_confirm_required`、危险动作路由云端、普通意图仍秒回）。
- `pytest --import-mode=importlib`：**318 passed**（不含 2 条需起 ASR 服务的集成用例；起全栈时 320 passed, 2 skipped）。
- `test/smoke_edge.py`：13/13。HMI `tsc --noEmit`：通过。

**已知边界 / 待全栈验证**
- R3/R4/R6 的端到端表现需 `make up` + `test/e2e_ws.py` 在真实流式/记忆/路由链路上复核（单测覆盖各部件，未覆盖跨服务流）。
- R8 改变了示例"解锁车门端侧秒回"——door_lock 现走云端确认；离线时危险动作不可用（安全默认）。
- aircon 风速 `set` 话术仍用 `hvac_set_success`（温度口径），属既有遗留，未在 2026-06-14 实施批处理。

### 后续接手清单（按优先级）

1. **Cloud Gateway 扩展性**：当前车流状态在单实例内存中；多实例需 vehicle_id 会话亲和或一致性路由，并处理旧流替换与心跳过期。
2. **工具后续**：HTTP/MCP 外部工具仍未实现；落地时必须要求 `network.external` 并使用出口白名单/超时/响应大小限制。
3. **真实权限注入**：从会话 token/设备身份解析 `granted_scopes`，替换 PoC 默认值。
4. **可观测导出**：接入 Prometheus endpoint 或 OTel exporter；编排路径接入 `start_span()`。

### 2026-06-14 慢意图 TTS 与混合意图上下文修复

**问题与根因**

- HMI 过去只在 `final` 事件调用整段 `/api/tts`，所以文字流式结束后才开始合成和播报。
- 混合意图逐片路由导致“导航去南京欢乐谷”与“走最快路线”被拆散；媒体主句先在
  本地执行，歌手限定再单独上云，既丢上下文又可能重复播放。

**已实现**

- `hmi/src/ttsQueue.mjs`：完整句标点或长度阈值触发切句；音频并行预取、严格按入队
  顺序播放；新请求和录音可取消旧会话。
- `hmi/src/audio.ts` / `App.tsx`：`speech_delta` 触发增量合成，`final` 只冲刷未播尾部；
  无流式回复继续兼容整段 TTS。
- `orchestrator/edge/server.py`：不可独立分类的续接片段附着到前一个主意图，按整个
  语义组决定本地或上云。导航目的地/路线偏好、媒体动作/歌手限定不再被拆散。
- 根 `README.md`、接手入口、测试说明、设计索引和模块 README 已统一到当前状态。

**验证证据**

- `python -m pytest --import-mode=importlib -q`：**321 passed, 2 skipped**。
- `python test/smoke_edge.py`：**13 passed, 0 failed**。
- `python -m pytest orchestrator/edge/tests/test_server_dispatch.py orchestrator/edge/tests/test_multi_intent_split.py -q`：**34 passed**。
- `node --test hmi/src/ttsQueue.test.mjs`：**5 passed**。
- `npm run build`（`hmi/`）：Vite 生产构建通过。

**该子阶段未重新执行**

- Docker 全栈重建、浏览器人工试听、`python test/e2e_ws.py`。此前 4 条 E2E 链路的
  通过记录保留，但不作为 2026-06-14 实施批的新鲜验证。

**仍需后续处理**

- 真正的服务端 PCM 流式 TTS；当前方案以短句为单位调用现有批量 `/api/tts`。
- 在真实 MiMo TTS 和车载网络环境测量首句合成时延、队列积压和打断体验。

### 2026-06-14 慢意图完整性与复杂意图回归修复

**用户报告场景**

1. “讲个笑话，顺便查北京天气”以及下一轮“讲个笑话”被慢意图回答成无关追问。
2. “船形地点→附近餐饮→附近停车→绿色氛围灯→空调二十三度→出发”只完成氛围灯，
   云端其余步骤持续处理中。

**根因与修复**

- Planner 可能接受含非法 Agent/intent 的部分计划并静默执行剩余步骤，导致用户意图
  被丢弃却仍返回完成话术。现在任一步非法会原子拒绝整份计划，触发重试/降级。
- chitchat 的 `text` 槽位可能为空或残留旧轮内容。现在始终用当前用户原话覆盖。
- PoC 默认 scope 缺少实际 Agent 声明的定位、导航、外网和支付权限，复杂计划会在
  执行前被过滤。默认集合已补齐，量产仍必须由 token 注入。
- 端侧没有识别中文数字温度，且句末“出发吧”会附着到最近的本地空调组。现在支持
  16–32 度中文数字，并将出发类续接附着到最近的云端行程/导航语义组。

**新鲜验证证据**

- `python -m pytest --import-mode=importlib -q`：**325 passed, 2 skipped**。
- `python test/smoke_edge.py`：**13 passed, 0 failed**。
- `npm test`（`hmi/`）：**5 passed**；`npm run build` 通过。
- Docker 18 个容器运行正常；两个用户报告场景均完成全栈回放：
  - 笑话/天气组合返回笑话，并明确说明当前无实时天气能力；下一轮纯笑话只回答当前轮。
  - 复杂行程先本地完成绿色氛围灯和空调 23 度，再依次完成地点、餐饮、停车与导航，
    云端终态约 6.66 秒返回。

**仍需验证**

- 2026-06-14 实施批未重新运行标准 `python test/e2e_ws.py` 四链路套件；其历史通过记录保留。
- 天气仍缺真实 `info` Agent/WeatherProvider，当前只能诚实降级，不能返回实时天气。
