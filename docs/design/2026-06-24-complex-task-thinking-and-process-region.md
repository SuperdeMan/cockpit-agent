# 复杂任务：动态思考(thinking) + 可折叠过程区（2026-06-24）

> 落地记录（本项目自己的设计）。外部对标见
> [`docs/research/2026-06-24-cockpit-agent-process-display-benchmark.md`](../research/2026-06-24-cockpit-agent-process-display-benchmark.md)。
> 受众：维护本仓库的人/AI。涉及 `proto/`、`llm-gateway/`、`agents/_sdk/`、
> `orchestrator/cloud/`、`orchestrator/edge/server.py`、`gateway/edge/main.go`、`hmi/`。

## 背景

当前 MiMo 经 `LLM_DISABLE_THINKING=true` **进程级**关闭思考：
`llm-gateway/providers.py` 的 `OpenAICompatibleProvider` 构造时把开关存进
`self.disable_thinking`，`complete/stream` 对每次调用都发 `body["thinking"]={"type":"disabled"}`。
这保护了 Planner JSON、聚合改写、接地合成等**结构化任务**不被 reasoning 吃空 content，
但也让行程规划/深度调研这类**该深想**的任务拿不到思考。

同时，复杂任务（行程/调研）在座舱里目前是「一句占位 → 长时间静默 → 出结果」，缺少
ChatGPT 式的「正在分析 / 规划 / 执行」过程反馈。对标调研结论：通用 Agent 已收敛为
「默认折叠 → 展开看步骤/摘要 → 过程与答案分离、**绝不露 raw CoT**」，座舱还要叠加
「行车极简 / 泊车展开」的安全双态（NHTSA 单次瞥屏≤2s）。

目标：①复杂任务后端动态开思考提质（reasoning 留后端、不下发）；②前端给可折叠过程区。
两者由**同一个「复杂任务」判据**驱动；普通车控/闲聊/单条轻查询零过程、零额外延迟。

## 产品决策（已定）

1. **过程区展开内容 = 步骤 + 思考摘要**；摘要由**后端按每步结构化结果合成**（脱敏），
   **绝不**解析/下发模型 raw reasoning（守需求红线「不暴露 prompt/reasoning/敏感字段」）。
2. **UI = 助手气泡内嵌折叠条**；默认折叠显示最新一行，可展开完整时间线，最终答案仍在
   气泡正文。
3. **thinking 覆盖 = 凡命中统一「复杂任务」判据者全覆盖**（不手挑 Agent），唯一例外是
   结构化 JSON 的 Planner DAG 生成始终不开。
4. **行车安全态 = 行车/泊车双态**：行驶中只一行不可展开，泊车/驻车才允许展开时间线。

## 统一判据 `is_complex`（`orchestrator/cloud/progress.py`，新）

```
HEAVY_INTENTS = {trip.plan, trip.modify, info.search, info.news, charging.plan}
is_complex(plan) = plan.complexity == "adaptive"
                   or len(plan.steps) >= 2
                   or any(s.intent in HEAVY_INTENTS for s in plan.steps)
```

同一判据同时决定「是否开思考」和「是否发过程区事件」，二者不会打架。该模块还提供四阶段
文案合成（全部脱敏，绝不含 prompt/reasoning/参数）：`task_summary(plan)`（理解需求——自然
语言任务类型，如「识别为多步骤出行规划任务」）、`plan_steps_summary(plan)`（规划步骤——能力名
清单，如「行程规划、天气查询、充电规划」）、`phase_label(intent)`（执行——动作短语，「正在
{label}…」要通顺）、`step_summary(step,result)`（执行结果——安全计数或完整首句，关键数字不
腰斩）。

## Part A —— 动态思考（后端）

LLM 协议无需改 proto（`CompleteRequest.meta` 已是 `map<string,string>`），用
`meta["thinking"]="on"` 透传。链路：

- `llm-gateway/providers.py`：`complete/stream` 增 `thinking` 形参；
  `effective_disable = self.disable_thinking if thinking is None else (not thinking)`；
  开思考时**不发** `thinking` 键（回 MiMo 原生思考态）并把 `max_completion_tokens`
  抬到 `max(max_tokens, 2048)`；响应仍只取 `content`，**不取/不下发** `reasoning_content`。
- `llm-gateway/server.py`：`Complete/CompleteStream` 读 `meta["thinking"]` 传给 provider；
  缓存键并入 thinking。
- `agents/_sdk/_ctx.py`（新）：把 `_current_meta`/`_set_current_meta`/`get_current_meta`
  从 `base.py` 抽到中立模块，解 base↔clients 循环依赖。
- `agents/_sdk/clients.py`：`LLMClient.complete/stream` 增 `thinking`，为 `None` 时从
  `get_current_meta()` 读 `meta["thinking"]`。**所有 Agent 的 LLM 调用据此自动覆盖，
  无需改各 Agent 业务码**——因为 `_Servicer.Execute/ExecuteStream` 已把整份 request.meta
  写入 `_current_meta`（server.py:74/91）。
- `orchestrator/cloud/clients.py`：`llm_complete(..., thinking=False)`；**Planner 调用恒
  `thinking=False`**（结构化 JSON），Aggregator 由 engine 显式传入。`call_agent` 增 `timeout`，
  由 `dispatch.py` 传 `step.latency_budget_ms/1000`（原固定 10s 会卡死 trip-planner 的 20s
  预算，属现存隐患，一并修）。
- `orchestrator/cloud/aggregator.py`：`compose(..., thinking=False)` 透传到 `_aggregate_speech`。
- `orchestrator/cloud/engine.py`：规划后 `complex=is_complex(plan)`；complex 时给每个 step
  `meta["thinking"]="on"`（经 ExecuteRequest.meta → agent `_current_meta` → SDK 自动开），
  且 `aggregator.compose(..., thinking=True)`。
- 预算：`agents/trip_planner/manifest.yaml` 20000→30000；`agents/info` 合成处 `complete`
  超时随 thinking 放宽。

## Part B —— 过程区（事件协议 + 编排发射 + 行车标注 + HMI）

事件端到端透传已验证：cloud-gateway `handleRequest`、edge `cloud_client.py`/`server.py`
对**非 final** 事件原样转发（cloud_client 仅在 `HasField("final")` 时 break）。只需动三处：
**发射点（engine/server）**、**Go `eventToMap`**、**HMI**。

1. **proto**：`HandleEvent` oneof 增 `ProcessUpdate progress = 4`，新增 `ProcessUpdate`
   `{phase,label,summary,status,step_id,driving}`。改后 `scripts/gen-proto.ps1` 重生
   `gen/python`+`gen/go`。
2. **engine 发射**（仅 complex；非 complex 维持现状=零延迟）四阶段：规划后发 `understand`
   + `plan`；执行前为每个待执行步骤发 `execute`(status=running) 占位（HMI 折叠态「正在查询
   天气…」），各步完成发同 `step_id` 的 `execute`(status=done, summary=step_summary)（HMI 按
   step_id 合并 running→done）；聚合前发 `synthesize`。覆盖 DAG 主路 / T2 `loop.run`（D0 流式
   仅非复杂）。原 `len>1` 的「正在为您处理…」占位由过程区取代。
3. **cloud server.py**：`kind=="progress"` → `HandleEvent(progress=ProcessUpdate(...))`。
4. **edge server.py**：转发 progress 事件时按 `VAL.state`（`speed_kmh`/`gear`）置
   `progress.driving`（driving = speed>0 或 gear∈{D,R,S}），覆盖 slow + mixed 两路径。
5. **gateway/edge/main.go**：`eventToMap` 增 `HandleEvent_Progress` → `{"type":"process",…}`。
6. **HMI**：`types.ts` 加 `ProcessStep` 与 `Msg.process/processActive/driving`；
   `App.tsx.handleEvent` 加 `type==='process'` 分支（挂当前气泡、累积步骤），`final` 时
   `processActive=false`（收尾即折叠）；`ChatView` 新增 `ProcessPanel` 内嵌折叠条
   （折叠=一行最新/「规划过程（N 步）」，展开=步骤时间线，`driving` 时强制折叠不可展开）。
   过程区**不接 TTS**（语音只读最终答案）。

## 不改动（已确认天然透传）

cloud-gateway Go（`DownFrame_Event` 整体转发）、edge `cloud_client.py`（非 final 原样
yield）、`channel.proto`、edge `_dispatch_cloud_actions`（只碰 final）。

## 验证

见仓库根 `AGENTS.md` 自检入口与本特性测试。要点：复杂任务（去杭州两天带老人+天气+充电）
过程区流式→折叠→可展开见步骤+摘要、thinking 生效；行车态降为单行不可展开；普通车控/闲聊
零过程零额外延迟；Planner JSON 不回归（thinking 恒关）。

**测试证据（2026-06-24 实测）**：
- 全量 `python -m pytest --import-mode=importlib`：**798 passed, 6 skipped**（基线 783，本特性新增）。
- 新增单测：`orchestrator/cloud/tests/test_progress.py`（is_complex 判据 + phase_label +
  step_summary 计数/首句 + **摘要脱敏不泄漏 prompt/params/meta** + analyze_summary）；
  `llm-gateway/tests/test_thinking.py`（provider：开思考不发 disabled 键 + token 抬到 2048；
  关思考发 disabled 键、token 不抬）；`agents/_sdk/tests/test_thinking_ctx.py`（LLMClient 从
  contextvar 自动判定思考、显式传参优先）。
- 行为变更回归：`test_engine_stream.py::test_multi_step_plan_does_not_stream` 改为断言多步复杂
  任务走过程区 progress 事件（不再 "正在为您处理" speech 占位、不再逐步刷 speech）。
- 端侧 `python test/smoke_edge.py`：13/13。
- HMI `npm run build` 通过；`npm test` 31/31；`tsc --noEmit` 本特性新增代码零类型错误
  （仅余仓库既有 `.mjs` 声明告警）。
- Go 网关：已经 `docker compose build edge-gateway cloud-gateway` **编译通过**（验证 Go 改动）。
- **端到端（真实全栈 + 真实 MiMo/高德/和风，22 容器）**：`test/e2e_process_region.py` 全绿——
  - 闲聊「讲个笑话」→ `process=0`；车控「打开空调26度」→ 端侧秒回 `process=0`（零过程零延迟）；
  - 复杂「去杭州两天带老人+天气+充电」→ 四阶段过程区：`[understand]识别为多步骤出行规划任务`
    → `[plan]行程规划、天气查询、充电规划` → `[execute]` 各能力（running 占位「正在查询天气…」
    + done 带完整结果「杭州未来2天…21~25℃」不截断）（脱敏断言无泄漏、`driving=false`），最终
    给出**针对带老人/轻松/防雨的西溪湿地两天行程** `need_confirm`——开思考后方案质量明显更用心。

### 后续修复（2026-06-25）：WS 长任务保活——过程区"看不到"根因
复杂任务执行期（heavy Agent 开思考）可能 **30s+ 无任何 WS 流量**，而 `gateway/edge/main.go` 的
`handleWS` 在 `stream.Recv` 流式循环里**不读 WS 控制帧** → gorilla 不自动 pong → 连接被 idle 掐断，
过程区与最终答案一起丢（这是"看不到流程"的根因，**后端/网关本身投递正常**）。修：edge-gateway 对每条
HMI WS 连接加**服务端周期 Ping(15s，`WriteControl`，可与写并发)**。注意浏览器不主动发 WS ping（靠服务端
保活/TCP），故 `test/e2e_process_region.py` 用 `ping_interval=None` 忠实模拟浏览器——修后全绿。
（HMI 端若仍看不到：重建过 edge-gateway，需**刷新页面重连**；若刷新后仍无，再查 HMI 过程区渲染组件。）

> 构建环境适配（国内网络，与本特性逻辑无关，但为可复现构建一并修）：
> ① `deploy/docker-compose.yaml` 的 `http-proxy` 失效 tag **已修为 `envoyproxy/envoy:v1.29-latest`**
> （2026-06-25；原 `v1.29` 无 patch 号拉不到，envoy 只发 vX.Y.N 与 vX.Y-latest）；
> ② 两个 Go 网关 Dockerfile 加 `GOPROXY=goproxy.cn,direct` + `GOSUMDB=off`；
> ③ `llm-gateway` Dockerfile 把 apt 源换阿里云镜像（装 ffmpeg）。
