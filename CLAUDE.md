# CLAUDE.md — 智能座舱 Multi-Agent 项目规则

> 本文件是项目的最高工程约定，所有人（含 AI 协作者）在本仓库工作必须遵守。
> 调整规范时：先改本文档，再改实践，不要反过来。

## 1. 项目是什么

云边协同的智能座舱 AI Agent 系统。架构范式为**分层混合编排**：端侧"快系统"处理高频/确定/安全敏感指令（车控、媒体），云侧"慢系统"用 LLM Planner 编排复杂、跨域、多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

**完整设计见 `docs/architecture/cockpit-agent-architecture.md`（架构唯一真相源）。** 任何与该文档冲突的实现都视为 bug。

## 2. 技术栈（不要随意偏离）

| 层 | 语言/框架 |
|---|---|
| 接入网关 gateway/ | Go (grpc-go + websocket) |
| 编排器 orchestrator/、Agent agents/、各 AI 服务 | Python 3.11 (grpcio + FastAPI) |
| 车控抽象层 vehicle-abstraction/ | C++（PoC 阶段可用 Python 模拟） |
| HMI hmi/ | React + TypeScript + Vite |
| 服务间通信 | gRPC（proto/ 为单一真相源） |
| 异步/广播 | NATS |
| 存储 | Redis（短期）、PostgreSQL + pgvector（长期/向量） |

## 3. 目录约定（什么放哪）

```
proto/          gRPC 契约——所有接口的唯一真相源，改接口先改这里再 codegen
gateway/        Go 接入网关（edge/ 端侧，cloud/ 云侧）
orchestrator/   edge/ 端侧编排+FastIntent；cloud/ 云端 Planner
llm-gateway/    LLM 多模型网关（所有 LLM 调用的唯一出口）
registry/       Agent 注册中心
memory/         记忆/画像服务
agents/         所有 Agent；_sdk/ 是公共 SDK，每个 Agent 一个子目录
security/       权限引擎、scope 定义、内容审核、注入防护
payment-gateway/  统一支付网关（Agent 不持支付凭证）
observability/  可观测模块：NATS 事件出口、collector、trace/日志/指标
hmi/            React 座舱前端
dashboard/      React 开发/演示可观测台（不进入车控执行主链）
deploy/         docker-compose / helm / k8s
scripts/        codegen、构建辅助（含 gen-certs.* 生成 mTLS 证书）
runtime/        共享 gRPC 运行时（keepalive/优雅停机/mTLS 工厂；全 Python 服务经此建 channel/server）
docs/           架构与设计文档
test/           端到端场景测试
gen/            codegen 产出（gitignore，不要手动编辑）
certs/          服务间 mTLS 证书（gitignore；scripts/gen-certs.* 生成，仅 .gitkeep 入库）
```

> 注：`vehicle-abstraction/` 在架构文档中规划，当前 PoC 阶段 VAL 实现位于 `orchestrator/edge/val.py`（Python 模拟）。

### 新增一个 Agent 的标准流程（必须遵守）
1. 在 `agents/<name>/` 下按模板建目录（参考 `agents/navigation/`）。
2. 写 `manifest.yaml` 声明能力、权限、trust_level、deployment；需要精确位置/电量等敏感上下文的 Agent 还要声明 `context_scopes`（`location`/`vehicle_state`），否则编排最小化下发会剥掉这些键。
   - **确定性路由（R2.1）**：弱 LLM 会漏/误路由该 Agent 的重域意图时，用 `route_hints` 声明兜底（`pattern`/`intent`/`policy`=`replace`\|`append`/`priority`/`guard`/`slots`；`slots` 值支持 `$text`=原话、`$1..`=捕获组）——编排核心 `orchestrator/cloud/route_hints.py::RouteHintEngine` 通用消费，**取代**过去在 `planning.py` 加正则兜底的做法。
   - **重域能力**（需开思考+过程区，如多轮检索/LLM 重生成）在该 capability 标 `heavy: true`（编排 `progress.is_complex` 据此判定）。
   - 出**主卡**的 Agent 在 `ui_card` 加 `display_priority`（`0`=主卡多意图下独显 / `1`=交互候选 / 缺省 `2`=普通信息卡），聚合器据此择优。
3. 继承 `agents/_sdk` 的 `BaseAgent` 实现业务逻辑，**不要重新实现 gRPC 契约**。
4. 写 `tests/` 契约测试 + 黄金用例。
5. 在 `deploy/docker-compose.yaml` 注册服务。
6. **不要修改编排核心代码**——Agent 通过注册中心被发现，编排对 Agent 无感；确定性路由 / 重域标记 / 卡片优先级全由步骤 2 的 manifest 声明式字段表达，**不在 `planning`/`context`/`aggregator`/`progress` 加硬编码**（R2.1 已把历史硬编码全部机制化，铁律已由 `test_planning.py` 契约测试固化）。执行后想把请求**改派**给别的能力（如「这题需要联网才能答」），用 `AgentResult.data["_escalate"]` 保留键声明——engine 通用消费、每轮最多一跳（协议登记 `docs/conventions.md` §9.1，契约测试 `test_engine_escalate.py`），同样不改编排核心。

## 4. 命名约定
- Intent：`<domain>.<action>`，如 `hvac.set`、`navigation.search_poi`。
- Permission scope：`<resource>.<action>[.<sub>]`，如 `vehicle.control.hvac`。
- Agent ID（manifest 内）：kebab-case，如 `charging-planner`。
- Python 包目录：snake_case，如 `agents/charging_planner/`（对应 agent_id `charging-planner`）。
- proto package：`cockpit.<service>.v<n>`。
- Python 模块 snake_case，Go 包小写，TS 组件 PascalCase。

## 5. 安全红线（架构级，违反即拒绝合并）
- **车控只能经 VAL 下发**。任何组件（含 LLM/Agent）不得直接操作 CAN/SOME-IP。
- **LLM 不直连车控**：LLM 只产出"意图/计划"，车控动作由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
- 危险动作（`require_confirm=true`）必须用户二次确认。
- 密钥/token 不进代码、不进 commit、不进日志；用 `.env`（已 gitignore），模板见 `.env.example`。
- 敏感数据（车内音视频、精确位置、支付）默认不出车，上云最小化。

## 6. 开发与验证

```bash
make proto        # 由 proto/ 生成 Go/Python 代码（改 proto 后必跑）
make up           # docker-compose 起全栈(PoC)
make down         # 停
make test         # 运行各服务单测 + 契约测试
make e2e          # 端到端场景测试
```
Windows 无 make 时用 `scripts/gen-proto.ps1`、`scripts/run_e2e.ps1` 等价替代（见 README）。

**工程纪律**：改完主动跑 `make test`；不要注释报错或加绕过标记来"让它跑起来"，找根因；大改动先在设计文档对齐再动手。

## 7. 当前阶段
截至 2026-07-10，Phase 1 工程化 PoC 主干、云端中枢 P0-P3、R2-R4 硬化主题（架构还债/
安全/语音回路/拒识澄清等）与可观测台（badcase 排查贯通：会话/轮次/日志/LLM + SQLite
持久化）已落地，运行模型为 T0 端侧快路径 / T1 单次 DAG / T2 有界 Agentic 循环。
当前事实、测试证据和待办统一维护在 `AGENTS.md`；设计与落地记录见
`docs/design/`。原始量产级目标和未完成项见
`docs/architecture/phase1-implementation-plan.md`，不要把当前 PoC 验收等同于该计划
全部 DoD 已完成。
