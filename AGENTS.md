# AGENTS.md — 接手者入口

> 先读本文件，再动代码。本文件是项目规则与接手入口，工程细则见 [`CLAUDE.md`](CLAUDE.md)；架构主入口是
> [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。
> 当前 QA 与发布交接统一看
> [`docs/reviews/2026-08-30-qa-closeout-handoff.md`](docs/reviews/2026-08-30-qa-closeout-handoff.md)。
> 逐批历史只查 [`docs/agents-history.md`](docs/agents-history.md)，不要把历史流水抄回本文件。

## 1. 项目是什么

云边协同的智能座舱 multi-agent 系统。端侧快系统处理高频、安全敏感和离线能力；云侧
Planner 处理复杂、多域、多轮任务。Agent 统一使用 gRPC 契约 + Manifest，经 Registry 发现；
车控只经 VAL，LLM 只产意图/计划。用户端有两个：座舱 HMI（`hmi/`）与 Android 陪伴端
「小舟随行」（`mobile/`，React Native + Expo），同一后端大脑、同 `user_id` 共享记忆、各自
独立会话，两端共享的是判据不是 UI（架构 §2.4、约定 §9.33）。

当前阶段是 **Phase 1 工程化 PoC**。工程主干、云端中枢、真实 Provider、语音回路、记忆、
可观测、旅程验证、M0a→M4、M5 数据飞轮与探索式 QA 的编号开发批均已落地。PoC 已可运行，
但真实 CAN/SOME-IP、量产账号体系、完整隐私治理和部分外部能力仍是明确边界。
后续按 [v2 路线图](docs/roadmap.md)推进可验证运行时与可选 Jev 判别层；规划采用不代表已经实现。

## 2. 文档地图

| 想了解 | 权威入口 |
|---|---|
| 当前 release、测试证据、QA 活项 | `docs/reviews/2026-08-30-qa-closeout-handoff.md` |
| 工程规则、目录、安全红线 | `CLAUDE.md` |
| 全局架构 | `docs/architecture/cockpit-agent-architecture.md` |
| 后续路线图 / 可领取工作包 | [路线图](docs/roadmap.md) / [v2 实施方案](docs/design/2026-09-26-cockpit-agent-v2-implementation-plan.md) |
| v2 目标架构 / 研究采纳交接 | [目标分册](docs/architecture/cockpit-agent-v2-target-architecture.md) / [本轮交接](docs/reviews/2026-09-26-v2-roadmap-handoff.md) |
| 历史 Phase 1 目标与 DoD | `docs/architecture/phase1-implementation-plan.md`（差距已映射到 v2） |
| 环境、端口、命名、错误码 | `docs/conventions.md` |
| 本地/云端开发与部署 | `docs/dev-guide.md` |
| 测试分层与 E2E | `test/README.md` |
| 意图对抗测试 | `docs/guides/intent-adversarial-testing.md` |
| 真实 Provider 接入 | `docs/guides/provider-integration.md` |
| MiniMax 原始 QA 问题 | `docs/reviews/2026-08-26-minimax-cloud-qa-findings.md` |
| MiniMax 根因与修复批 | `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md` |
| 安全确认写闸 | `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md` |
| Android App 当前入口 | [剩余待办总表](docs/design/2026-09-14-android-remaining-todos.md) + [mobile/README.md](mobile/README.md)；设备包与服务端版本分别核对 |
| Android 构建与跨工具交接 | [`操作指南`](docs/guides/android-build-and-device-validation.md)：共享镜像/设备串行占用、低内存参数、长任务接续与精确验包 |
| 历史流水 | `docs/agents-history.md`（只追加） |

服务子目录各有 README；改某个服务前先读该目录 README。

## 3. 不可违反的规则

### 3.1 运行环境

- 根目录 `.env` 是唯一运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 本地真栈只用 `make up` 或根 `compose.yaml`；不得以 `deploy/docker-compose.yaml` 为首文件。
- 任何 E2E、Compose、部署或脚本/manifest 改动前，先从仓库根读取 `dev-stack.local`。
- `dev-stack.local` 只允许 `target=local|cloud`，不得保存 token、密码、私钥或 URL。
- 2026-09-20 起云端签名 E2E 身份车道已开（`shared/.env` 与根 `.env` 同一份 `E2E_IDENTITY_SECRET`）：干净用户实验用
  `scripts/probe_history_window.py` 这一路（`scripts.e2e_identity.sign_identity`），不再需要改 `AUTH_TOKENS`；秘密照旧不进代码 / 日志。
- `target=cloud` 时禁止启动本地 Compose；本地只做编辑、单测、静态检查和 Vite。
- 不停止其他 agent 正在使用的 Docker、Metro、Gradle、pytest 或真栈进程。

### 3.2 云端发布

- 真栈动作前先运行 `python scripts/dev_stack.py target show`。
- Windows 真栈、SSH 与 deploy 命令一律用 PowerShell；Git Bash/MSYS 会改写参数引号。
- cloud deploy 只接受 clean、已提交、main 可达的 SHA；先 dry-run，再经人工授权 `--apply`。
- deploy 不自动 commit、merge 或 push；`git push` 必须单独授权。
- push 前必须逐条展示 `origin/main..HEAD`，让用户看到会被一并推走的提交。
- 未标 `remote_safe` 的 E2E 不在 cloud 缺省运行；`remote_mutating=true` 仍需精确 `--id`、
  `--allow-mutating` 和本轮人工授权。
- 支付、商户写、真实车控、数据删除、系统配置不因通用 deploy/E2E 授权自动放行。
- 三存储迁云只用 `scripts/cloud_data_migration.py`；final 必须先取得停写授权。
- 不修改 `.env`、安全组、Tailscale、CI/CD、systemd、数据库 schema，除非用户逐项授权。

### 3.3 架构安全

1. 车控只经 VAL；任何组件不得直接碰 CAN/SOME-IP。
2. LLM 不直连车控：Planner 产计划，确定性 Executor 经权限与 VAL 执行。
3. 危险动作必须二次确认；`require_confirm` 权威来自 capability manifest/受控配置，不信 LLM。
4. 新 Agent 经 Registry 发现；不得为加 Agent 修改 orchestrator 核心路由分支。
5. secret/token/password 不进代码、commit、日志或文档。
6. 改 proto 先改 `proto/`，再 `buf generate proto`；绝不手改 `gen/`。
7. `Capability.response_only` 是只响应能力的权威；D0/T2/Executor 都必须 fail closed。
8. 安全问句的权威文本是服务端 `safety_origin_text`；LLM goal/reason 和补槽短句无授权权威。

### 3.4 v2 / Jev 增量约束

- 先复用 Step、WorkingSet、SessionState、Ledger、Verifier；不再建一套会话、重试或授权判据。
- 稳定 goal_id 由服务端持有；`PLANNER_GOALS` 保持 off，模型覆盖关系不能授予执行权。
- Jev 仅经拟新增网关 Decide 提供建议；不作为聊天/业务 Agent，off 零外呼、shadow 零副作用。
- 新目标态字段/脚本/开关未实现时明确标注；不把模型概率、仿真或 ACK 当真实执行证明。
- 每个实现包保留旧数据兼容、实际 dispatch 出口覆盖与回退；schema/配置/CI/CD/生产变更仍按 §3.2 授权。

## 4. 当前真实状态（2026-09-26）

### 4.0 发布快照

发布 SHA、status、verify、当前测试证据只维护在
[QA/发布交接 §2](docs/reviews/2026-08-30-qa-closeout-handoff.md)。`origin/main` 可因纯文档提交领先生产；
引用现场状态前重新核对。`5/5 endpoint healthy` 只说明健康度，不能替代业务验收。

- 本仓仍是 Phase 1 工程化 PoC；v2 已纳入规划，新增契约、Jev Decide、T1e 与真实车辆驱动均未实现。
- Android 包身份与设备验收看 [剩余待办总表](docs/design/2026-09-14-android-remaining-todos.md)，不得用服务端 SHA 代替 APK 身份。
- 历史手册基线 `9a3b6f2f08657464c5049a5abf8f6e989e398bce` 的读数只属该 SHA，
  完整发布流水已迁到 [入口状态快照](docs/history/2026-09-26-entry-status-snapshot.md)，不再往本节堆批次。

### 4.1 QA 状态

**QA 仍非全绿**。旧开发批闭合不代表当前发布或 v2 已验收。现有问题继续在
[QA 交接页](docs/reviews/2026-08-30-qa-closeout-handoff.md)、各轮修复记录和 Android 总表维护；
v2 的可重复旅程、故障矩阵与模型收益门槛见 [实施方案](docs/design/2026-09-26-cockpit-agent-v2-implementation-plan.md)。

### 4.2 当前活项与其他可接工作

| 主题 | 启动条件 / 唯一接续入口 |
|---|---|
| v2 主线与 Jev 支线 | [路线图](docs/roadmap.md)；先 CA2-01/JV00 共用基线，再步骤归属/结果契约与 Decide 离线契约；新行为默认未启用 |
| 手册口语召回与复合问句 | [2026-09-26 设计](docs/design/2026-09-26-manual-rag-colloquial-recall.md) §8；尚存规划方差与挂确认时结果完整呈现，纳入 R0/R1；Jev 不能代替此修复 |
| 对话评审四轮 | [逐条重证与分批落地](docs/design/2026-09-24-conversation-review-round4-remediation.md) §7；已修项不重新立项，未触发项保持条件 |
| 对话评审三轮 | [修复记录](docs/design/2026-09-23-conversation-review-round3-remediation.md)；历史批次与待裁决项按原表追溯 |
| Android 工程、真机、真人验收与 AM5 | [剩余待办总表](docs/design/2026-09-14-android-remaining-todos.md)；v2 只建立依赖，不宣称旧批全部签收 |
| 可执行性 / 端侧 NLU | actionability 与 EDGE_NLU 保持 shadow；[B6 设计](docs/design/2026-08-10-b6-actionability-forward.md) + v2 R3，未达到门槛不得切执行 |
| 支付、订座、票务 | 等真实 Provider/账号与独立授权；不做最终付款，不为对标造能力 |
| 端侧能力台账 | `orchestrator/edge/knowledge/capability_exemptions.yaml` 与 reachability 测试 |
| memory_item 衰减、M5 扩容 | 保留原触发条件（第二个可复现实例 / 目录规模或流量证据），不因 v2 规划自动重开 |

### 4.3 读数纪律

- **记录缺陷不等于修复缺陷**：台账必须标“仅记录 / 已修 / 待触发”；修好后回归锁仍保留。
- **分布不代替逐条证据**：unstable 可能是边界方差，也可能是稳定低通过率；逐条看 trace。
- **扫描类断言必须做反向验证**：临时注入一处目标缺陷，证明它真的会红，再恢复实现。
- **先确认输出通道**：toolcall、salvage、流式和 deterministic handler 的分布不可混算。
- **总数跨趟通常不可比**：时间、商户营业、provider/QPS、判据版本都会改变分母；逐条对原红。
- **证据不跨 SHA**：本地全量、部署 release、真栈 artifact 与后续 docs/test 提交分栏记录。

## 5. 接手第一步

```powershell
python scripts/dev_stack.py target show
git status --short --branch
git log -5 --oneline --decorate
```

然后按任务读取：

- 后续升级：[路线图](docs/roadmap.md) → [实施方案](docs/design/2026-09-26-cockpit-agent-v2-implementation-plan.md)，先核对基线再领取 CA2/JV 包；
- QA：`docs/reviews/2026-08-30-qa-closeout-handoff.md`；
- 云端迁移/发布：`docs/dev-guide.md` + `docs/reviews/2026-08-17-cloud-data-migration-handoff.md`；
- Planner/安全：架构 §5.2.13、约定 §9.40、安全专题设计；
- mobile：`mobile/README.md` + `docs/design/README.md` 中最新 mobile implementation plan。

引用任何 release、测试数或长会话结果前，先核对 SHA 与 artifact；不得从旧段落抄数字。

## 6. 改完怎么验证

### 6.1 全量固定口径

PowerShell：

```powershell
$env:TZ = 'UTC0'
Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
python -X utf8 -m pytest -q -n 8 --dist worksteal
```

- `pytest.ini` 已固定 `--import-mode=importlib`；不要重复另造口径。
- 内存充足可用 `-n auto`；可用内存约 6GB 时用 `-n 8`，避免 worker 被 OOM 杀死。
- 并行偶发红先单文件/串行复跑；OS lock、真实子进程和其他 agent 会污染读数。
- 跑批期间不改工作树；读数属于 collect 时的树。
- `target=cloud` + 本地 Docker 停时，32 skipped 是当前 Windows 基线。

### 6.2 四道 blocking 门禁与端侧 smoke

```powershell
python test/smoke_edge.py
python test/eval_skills.py
python test/eval_exemplars.py
python scripts/check_intent_gate.py
python test/eval_capability_integrity.py
```

四道 CI blocking 门禁是 skills、exemplars、L0 strict、capability integrity；均为零 LLM、零网络。

### 6.3 云端验证

```powershell
python scripts/dev_stack.py target show
python scripts/dev_stack.py status
python scripts/dev_stack.py verify
```

- `submitted` 不等于部署成功；必须独立跑 status 与 verify。
- worktree 部署可用干净隔离克隆/工作树，只复制 `dev-stack.local`，不要复制 `.env`。
- status/verify 需要根 `.env` 的 Tailnet 端点与 token；deploy 本身不读根 `.env`。
- 长会话必须显式 `--expected-sha <完整40位release>`。
- 迷你集默认不 pin provider，运行前先确认实际 provider/model；迷你集没有清理段。

### 6.4 证据纪律

- release SHA、测试 SHA、artifact SHA 分栏记录；不把邻近提交的全量结果转借给 release。
- 自动 PASS 不等于业务正确：读 speech、actions、card、trace、cleanup 和 open operations。
- 单次采样不当基线；模型方差项至少 repeat 3。
- 回放是尺子重算，不是当前真栈复跑。
- 测试 fixture 的环境/顺序问题要隔离定性，不能用 skip 或注释绕过。

## 7. 常见工程任务

### 7.1 新增 Agent

1. 新建 `agents/<name>/manifest.yaml`、源码、README、tests；
2. 遵守 `proto/cockpit/agent/v1/agent.proto`；
3. 注册服务，不改 orchestrator 核心分支；
4. 加 capability 契约、权限、确认、provenance 与验证用例；
5. 跑服务测试、Cloud Planner、门禁和全量。

详细流程见 `CLAUDE.md` §3。

### 7.2 新增端侧车控能力

1. 改 `orchestrator/edge/knowledge/commands.yaml`；
2. 明确对象、operate、权限、`require_confirm`、drive/voice 限制；
3. 让生成器派生意图，不手写第二份集合；
4. 跑 capability integrity、intent gate 和 edge tests；
5. 确认规则产出的命令能通过 VAL，不只验证名字存在。

### 7.3 改 proto / manifest

- proto 先改真相源，再 codegen；generated 文件 gitignore，不手改、不 force-add；
- manifest 新字段要检查 YAML loader、Registry 持久化 round-trip、Step 装配、挂起恢复和执行出口；
- 可选 JSON null 在 map<string,string> 边界视为“未提供”，不得转成 `"None"`。

## 8. 协作与文档

- 默认中文；结论先行，代码/命令/变量用英文。
- 变更前读规则；大改先给方案，用户确认后实施。
- 修改后主动验证，不能用注释、skip 或宽松断言掩盖失败。
- 工作树可能有别人改动；只碰本任务文件，禁止 `git reset --hard`、rebase、force-push。
- 删除文件/目录、改 `.env`/密钥/CI/CD、数据库迁移、push、生产部署都要人工授权。
- `AGENTS.md` 是规则与当前入口，不是变更日志；逐批过程写 `docs/agents-history.md`，发布数字只维护 QA 交接 §2。
- 后续排序只维护 `docs/roadmap.md`；研究保留原论据，实施方案登记任务，目标架构必须和现状分栏。
- 已完成的 implementation plan 保留作实施证据，但不作为“下一步”入口。
- 文档中的“今天/最近”只用于引用原始用户话术；状态一律写绝对日期。
