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
vehicle-abstraction/  VAL 车控抽象层（PoC 为模拟实现）
hmi/            React 座舱前端
deploy/         docker-compose / helm / k8s
scripts/        codegen、构建辅助
docs/           架构与设计文档
test/           端到端场景测试
```

### 新增一个 Agent 的标准流程（必须遵守）
1. 在 `agents/<name>/` 下按模板建目录（参考 `agents/navigation/`）。
2. 写 `manifest.yaml` 声明能力、权限、trust_level、deployment。
3. 继承 `agents/_sdk` 的 `BaseAgent` 实现业务逻辑，**不要重新实现 gRPC 契约**。
4. 写 `tests/` 契约测试 + 黄金用例。
5. 在 `deploy/docker-compose.yaml` 注册服务。
6. **不要修改编排核心代码**——Agent 通过注册中心被发现，编排对 Agent 无感。

## 4. 命名约定
- Intent：`<domain>.<action>`，如 `hvac.set`、`navigation.search_poi`。
- Permission scope：`<resource>.<action>[.<sub>]`，如 `vehicle.control.hvac`。
- Agent ID（manifest 内）：kebab-case，如 `food-ordering`。
- Python 包目录：snake_case，如 `agents/food_ordering/`（对应 agent_id `food-ordering`）。
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
Windows 无 make 时用 `scripts/gen-proto.ps1` 等价替代（见 README）。

**工程纪律**：改完主动跑 `make test`；不要注释报错或加绕过标记来"让它跑起来"，找根因；大改动先在设计文档对齐再动手。

## 7. 当前阶段
Phase 1 全量代码已落地（97+ Python + 2 Go + 8 proto，87/87 测试通过）。剩余：`make proto` 生成 gen/ → docker 整栈联调 → MiMo API key 验证。路线见 `docs/architecture/phase1-implementation-plan.md`。
