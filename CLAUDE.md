# CLAUDE.md — 智能座舱 Multi-Agent 项目规则

> 本文件是项目最高工程约定。调整规范时先改本文档，再改实践。
> 架构唯一真相源：`docs/architecture/cockpit-agent-architecture.md`。

## 1. 项目是什么

云边协同的智能座舱 Agent 系统：端侧快系统处理高频、确定、安全敏感指令；云侧 Planner
编排复杂、多域、多轮任务。Agent 统一使用 gRPC 契约 + Manifest，经 Registry 发现。

## 2. 技术栈

| 层 | 主要技术 |
|---|---|
| Gateway | Go，grpc-go，WebSocket |
| Orchestrator / Agent / AI 服务 | Python 3.12，grpcio，FastAPI |
| HMI | React + TypeScript + Vite |
| Mobile | React Native + Expo + TypeScript |
| 服务通信 | gRPC；`proto/` 是唯一契约源 |
| 广播 / 存储 | NATS；Redis；PostgreSQL + pgvector |

## 3. 目录约定与能力流程

| 目录 | 职责 |
|---|---|
| `proto/` | gRPC 真相源；先改 proto，再 codegen |
| `gateway/` | edge/cloud 接入网关 |
| `orchestrator/edge/` | FastIntent、端侧编排、VAL 与车控知识库 |
| `orchestrator/cloud/` | Planner、Context、Loop、Executor、Aggregator |
| `llm-gateway/` | 所有 LLM/ASR/TTS/S2S/视觉调用出口 |
| `registry/` | Agent 注册、发现与 PostgreSQL round-trip |
| `memory/` | 记忆、画像、关系与建议准入 |
| `agents/` | 各领域 Agent；`agents/_sdk` 是公共 SDK |
| `skills/` | Planner guides、policies、exemplars |
| `runtime/` | 跨服务唯一判据与运行时公共件 |
| `security/` | 权限、scope、审核、注入防护 |
| `payment-gateway/` | 支付网关；Agent 不持支付凭证 |
| `proactive/` | 主动消息全局治理 |
| `observability/` | 事件、collector、trace、日志、指标 |
| `hmi/` / `mobile/` / `dashboard/` | 三个客户端/观测界面 |
| `deploy/` | Compose、Helm、K8s；不是 `.env` 真相源 |
| `scripts/` | codegen、构建、部署、迁移、探针 |
| `test/` | E2E、评测基线、旅程、CDP |
| `docs/` | 架构、设计、review、指南、历史 |
| `gen/` / `certs/` / `models/` | 生成物或本地资产，均 gitignore，不手改/提交 |

关键声明只允许一份权威：

- 车控对象/操作/风险/端侧意图：`orchestrator/edge/knowledge/commands.yaml`；
- 重试规则：`orchestrator/cloud/retry_policy.py`；
- D0/T2 流式状态：`orchestrator/cloud/stream_state.py`；
- 槽值形状：`orchestrator/cloud/slot_shape.py`；
- 候选集问答：`orchestrator/cloud/candidate_query.py`；
- 时间、极性、问句、安全信号等跨服务判据：`runtime/`；
- Planner 软知识：`skills/`；
- 商户能力/工具：`agents/mcp_bridge/servers.yaml`。

不要在第二个模块复制判据、词表或声明；跨进程消费用测试对账声明源。

### 新增能力的标准流程

#### 新增 Agent

1. 在 `agents/<name>/` 建 manifest、源码、README、tests；参考现有同类 Agent。
2. Manifest 声明 intent、权限、trust、deployment、latency、context scopes 与 capability 元数据。
3. 继承 `agents/_sdk::BaseAgent`，不重新实现 gRPC 契约。
4. 在部署配置注册服务；通过 Registry 发现，不改 orchestrator 核心路由分支。
5. 补契约测试、黄金用例、门禁与全量验证。

Manifest 约定：

- `route_hints` 只用于弱模型确定性兜底；领域知识留在 Agent，不写进 Planner 分支；
- `scope=clause` 时 pattern 与 `$text` 按分句，guard 仍对整句求值；
- 重任务标 `heavy: true`；
- 消费整句且一份计划最多一步的能力标 `whole_utterance: true`；
- 追问自由文本槽时声明 `slot_shapes`；形状名必须存在于 `slot_shape.SHAPES`；
- 只允许回答、不得动作/挂起的能力标 `response_only: true`；
- 主卡用 `display_priority`；需要候选/位置/车态等上下文时显式声明 `context_scopes`；
- 改派其他能力使用 `AgentResult.data["_escalate"]`，每轮最多一跳。

新增 manifest/proto 字段必须沿以下链路核对：SDK loader → Registry 持久化 round-trip →
Planner `Step` 装配 → pending serialize/restore → Executor/D0/T2 消费。LLM 同名字段无权覆盖。

#### 新增端侧车控能力

1. 用 `scripts/gen_capability_skeleton.py` 生成待办清单；生成器不替代人工裁决。
2. 修改 `commands.yaml`、`responses.yaml`、`nlu_objects.yaml`，明确：
   `require_confirm`、effect、edge intents、operate/attr/mode、权限与话术。
3. 人工补 FastIntent 规则、VAL 模拟分支、对抗覆盖和迁移探针。
4. 新对象在 `orchestrator/edge/tests/corpus/vehicle_objects.yaml` 留识别与 VAL 用例。
5. 运行 capability integrity、intent gate、smoke edge 与全部 edge tests。

名字存在不等于能力可达：自然语言 → intent → command object/operate → VAL 校验 → 执行/话术
五段都要通过；规则产出的 operate/attr/mode 必须在该对象声明值域内。

#### 改 proto

1. 只改 `proto/`；
2. 运行 `make proto` 或 `scripts/gen-proto.ps1`；
3. 检查 Python/Go 生成物包含新字段，但不 force-add `gen/`；
4. 补兼容默认值、序列化/恢复、Registry round-trip 与旧记录用例。

JSON null 在 `map<string,string>` 边界表示“未提供”，不得字符串化为 `"None"`；0、false 和空串
仍是合法值。

## 4. 命名约定

- Intent：`<domain>.<action>`，如 `hvac.set`；
- Permission scope：`<resource>.<action>[.<sub>]`；
- Agent ID：kebab-case；Python 包：snake_case；
- proto package：`cockpit.<service>.v<n>`；
- Python 模块 snake_case，Go 包小写，TS 组件 PascalCase。

## 5. 安全红线

1. 车控只能经 VAL；任何组件不得直接操作 CAN/SOME-IP。
2. LLM 只产意图/计划；确定性 Executor 执行并经过权限、VAL 与确认。
3. `require_confirm=true` 必须二次确认，权威来自 capability/受控配置，不信 LLM。
4. S2S 语音模型没有执行通道；唯一工具是 `escalate` 回文本主链。
5. `response_only` 能力不得返回 action、`NEED_CONFIRM` 或 `NEED_SLOT`；冲突在 dispatch/yield 前拒绝。
6. 安全问句只认服务端 `safety_origin_text`；LLM goal/reason 与补槽短句无授权权威。
7. secret/token/password 不进代码、commit、日志或文档；根 `.env` 是唯一运行时密钥源。
   Mobile 原生构建键只放 gitignore 的 `mobile/.env.local`，它不是后端 `.env` 的替代品。
8. 精确位置、音视频、支付默认最小化；未经用户开启与主动交互不得采集/上云。
9. 声纹只用于个性化，不参与权限、VAL、确认或支付。
10. 视觉默认关、只在触发时抓单帧、TTL 内存保存；连续流 `camera.read` 禁止。

## 6. 开发与验证

### 6.1 可切换真栈与发布

- 所有真栈动作前运行 `python scripts/dev_stack.py target show`；
- Windows 真栈/SSH/deploy 一律用 PowerShell，禁止用 Git Bash/MSYS 传远端引号参数；
- 统一从仓库根读取 gitignore 的 `dev-stack.local`，只允许 local/cloud，不存秘密；
- `target=cloud` 禁止本地 Compose；`target=local` 只用根 `compose.yaml` / `make up`；
- cloud deploy 只接受 clean、已提交、main 可达 SHA；先 dry-run，再单独授权 `--apply`；
- deploy 不自动 commit/merge/push；push 前列出完整 `origin/main..HEAD` 并单独授权；
- remote-safe 与 remote-mutating 分开；支付、商户写、真实车控、数据删除、系统配置逐轮授权；
- 不切换 target、不停别人的 Docker/Metro/Gradle/pytest，不处理别人的脏工作树；
- 三存储迁移只用 `scripts/cloud_data_migration.py`，final 先取得停写授权，replace 不 merge；
- 迁移/发布不自动修改 `.env`、安全组、Tailscale、CI/CD、systemd 或 schema。

完整操作与批准摘要见 `docs/dev-guide.md`。

### 6.2 命令与工程纪律

```bash
make proto     # 改 proto 后
make test      # 单测与契约测试
make up        # 仅 target=local
make e2e       # 仅 target=local
```

Windows 用对应 `.ps1`。cloud 档使用：

```powershell
python scripts/dev_stack.py status
python scripts/run_e2e.py --target cloud
python scripts/dev_stack.py hmi
python scripts/dev_stack.py dashboard
```

工程纪律：

- 改完主动验证；不靠注释报错、skip 或宽松断言“让它绿”；
- 大改先设计后实现；模型输出当不可信输入，一直校验到 hash/split/int/执行的值；
- Mobile 语音构建前运行 `scripts/fetch_mobile_voice_assets.ps1`；缺模型/原生件必须显式失败；
- 新增原生 npm 库后检查生成的 `PackageList.java`，构建成功或 APK 含 `.so` 不证明模块已注册；
- happy path 与干净会话证明不了多轮状态，验证失败后下一轮和至少三轮；
- 测试替被测系统注入的前提不再被验证；真实权限/上下文/清理必须另有用例；
- 证据绑定精确 SHA；自动 PASS 仍需检查 speech、actions、card、trace、cleanup 与 open ops。

全量固定口径、四道门禁和云端验证命令见 `AGENTS.md` §6。

## 7. 当前阶段与知识维护

当前是 Phase 1 工程化 PoC。当前 release、测试数字、QA 活项不在本文件维护：

- 接手与红线：`AGENTS.md`；
- QA/发布交接：`docs/reviews/2026-08-30-qa-closeout-handoff.md`；
- 架构：`docs/architecture/cockpit-agent-architecture.md`；
- 历史：`docs/agents-history.md`；
- 设计索引：`docs/design/README.md`；
- 量产 DoD：`docs/architecture/phase1-implementation-plan.md`。

长期质量规则：门禁是规格、知识要 A/B 证伪、输出通道先确认、记录不等于修复、声明源只留
一份、证据不跨 SHA 转借。`CLAUDE.md` 与 `AGENTS.md` 都不是变更日志；逐批过程只进 history。
