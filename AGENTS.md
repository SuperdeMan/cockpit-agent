# AGENTS.md — 接手者入口

> 先读本文件，再动代码。工程规则最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是
> [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。
> 当前 QA 与发布交接统一看
> [`docs/reviews/2026-08-30-qa-closeout-handoff.md`](docs/reviews/2026-08-30-qa-closeout-handoff.md)。
> 逐批历史只查 [`docs/agents-history.md`](docs/agents-history.md)，不要把历史流水抄回本文件。

## 1. 项目是什么

云边协同的智能座舱 multi-agent 系统。端侧快系统处理高频、安全敏感和离线能力；云侧
Planner 处理复杂、多域、多轮任务。Agent 统一使用 gRPC 契约 + Manifest，经 Registry 发现；
车控只经 VAL，LLM 只产意图/计划。

当前阶段是 **Phase 1 工程化 PoC**。工程主干、云端中枢、真实 Provider、语音回路、记忆、
可观测、旅程验证、M0a→M4、M5 数据飞轮与探索式 QA 的编号开发批均已落地。PoC 已可运行，
但真实 CAN/SOME-IP、量产账号体系、完整隐私治理和部分外部能力仍是明确边界。

## 2. 文档地图

| 想了解 | 权威入口 |
|---|---|
| 当前 release、测试证据、QA 活项 | `docs/reviews/2026-08-30-qa-closeout-handoff.md` |
| 工程规则、目录、安全红线 | `CLAUDE.md` |
| 全局架构 | `docs/architecture/cockpit-agent-architecture.md` |
| Phase 1 计划与量产 DoD | `docs/architecture/phase1-implementation-plan.md` |
| 环境、端口、命名、错误码 | `docs/conventions.md` |
| 本地/云端开发与部署 | `docs/dev-guide.md` |
| 测试分层与 E2E | `test/README.md` |
| 意图对抗测试 | `docs/guides/intent-adversarial-testing.md` |
| 真实 Provider 接入 | `docs/guides/provider-integration.md` |
| MiniMax 原始 QA 问题 | `docs/reviews/2026-08-26-minimax-cloud-qa-findings.md` |
| MiniMax 根因与修复批 | `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md` |
| 安全确认写闸 | `docs/design/2026-08-30-qa-safety-confirmed-write-guard.md` |
| Android App 当前计划 | `docs/design/2026-09-02-mobile-ux-v2-b4-implementation-plan.md`（**B4 计划已拆 2026-09-02，草案待批**；§0 第 3 条两个待裁项先裁；B3 收口读数与遗留出账仍在 `docs/design/2026-09-01-mobile-ux-v2-b3-implementation-plan.md` §6.1–§6.4） |
| 历史流水 | `docs/agents-history.md`（只追加） |

服务子目录各有 README；改某个服务前先读该目录 README。

## 3. 不可违反的规则

### 3.1 运行环境

- 根目录 `.env` 是唯一运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 本地真栈只用 `make up` 或根 `compose.yaml`；不得以 `deploy/docker-compose.yaml` 为首文件。
- 任何 E2E、Compose、部署或脚本/manifest 改动前，先从仓库根读取 `dev-stack.local`。
- `dev-stack.local` 只允许 `target=local|cloud`，不得保存 token、密码、私钥或 URL。
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

## 4. 当前真实状态（2026-09-03）

### 4.0 发布快照

| 项目 | 当前值 |
|---|---|
| 真栈目标 | `target=cloud` |
| 远端 main / QA 文档 HEAD | 运行 `git rev-parse origin/main`；纯 docs/test 可领先 production release |
| 生产 release | `a729b984a7e66f508d0a11218713b6e51c8f7620` |
| 回滚点 | `e9fa602e7991b212de4c1ea8c8e95c3673891c1f` |
| status | 5/5 endpoint healthy，零 warning |
| verify | `verified`；artifact `20260830T110922Z-a729b98.json` |
| 全量单测 | `7770 passed / 32 skipped / 13 warnings` |
| Cloud Planner | `1278 passed / 1 skipped` |
| Planner + Info | `289 passed` |

`862617b` 与 `26de242` 是 release 之后的探针/文档提交，不是新生产 release。

### 4.1 QA 状态

- 探索式 QA 的 Q1–Q13、MiniMax C1–C16、M1–M6、B1–B7 和 I-024 已完成；
- 安全专项在 `e9fa602` 上 5 例、15/15 PASS；
- 完整 information persona 在 `e9fa602` 上 57/59，提醒/导航清理、零挂起与 release 连续均证明；
- `limit:null → "None" → ValueError` 已由 `a729b98` 修复，新闻 3 个干净会话零 internal error；
- **QA 仍非全绿**。剩余活项只看 QA 当前交接页 §5，不从历史批次表找。

当前主要活项：

| 活项 | 性质 | 入口 |
|---|---|---|
| 安全问句偶尔落 `info.search` | 回答安全但错域、无 manual provenance | QA 交接页 §5 |
| safety focus 持续阻断后续 charging plan | 安全状态解除时机的产品裁决 | QA 交接页 §5 |
| MiniMax TTS RPM / barge-in 残帧 | 外部配额与协议/客户端边界 | QA 交接页 §5 |
| manual-rag 生产发布差额 | 当前工作树已实现并本地验证 Xiaomi SU7 真实手册索引（278 页→269 chunks；retrieval 主集 22/22 + holdout 8/8）；shared-model 哈希清单、远端 preflight 与 cloud 只读挂载已接线。生产 `a729b98` 仍为 mock | `docs/design/2026-09-03-xiaomi-su7-manual-rag-implementation-plan.md` + QA 交接页 §5；2026-09-03 已取得基础设施/push/deploy 授权，下一步按 exact SHA bootstrap 索引与批准锚、dry-run、deploy、real 决议/卡片/问答验收；完成前不写已关闭 |
| gRPC RuntimeWarning | test-only fixture 债务 | QA 交接页 §5 |

### 4.2 当前活项与其他可接工作

| 主题 | 启动条件 / 入口 |
|---|---|
| Android App B5 | **B4 四批已收口（2026-09-03）**，逐批读数/坑/遗留出账在 `docs/design/2026-09-02-mobile-ux-v2-b4-implementation-plan.md` §6.1–§6.4（**§6.4 是收口节：交裁清单 / 出账去向核销 / §1.1 逐行核销**）。接手先看 `git status` / `git log --oneline origin/main..HEAD`——⛔ **本地与远端已分叉**（两边各有对方没有的提交），怎么合未定。⛔ **语音读数口径仍是 B3 那条**：AEC 补丁 `96a6830` 于 B3 第 2 批首次进包（主线包锚 `2026-09-02 16:45:36`）⇒ 此前所有唤醒率/回声/端点读数作废，**没记 `adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime` 的语音读数视为无效**。B4 交付：**§11.2 B4 四条 ✅✅✅✅**（形态矩阵五张齐；Edge 标 `driving` → 行车档自动进入 + 目标 ≥56dp 机器读数 56.0 PASS vs 44.0 FAIL + 30s 退出宽限翻转；VAL 拒绝原话是安全话术非权限拒绝、无全屏层；reduce-motion 静帧 0.00% 配活体 99.49%），提示音人耳盲测 6/6（补均衡约束后），book 铰链逐像素落 gap；两缺陷已修（`sheetDetent` 纯比例装不下行车档内容 → 新判据 `ui/layout/sheetHeight.ts` 给内容最小高下限；`/native-spike` 写死 `useLayout(false)` 的取证装置在说谎）。**仍开的活项**：① **T13 八格 + T14 五人小样本等一次真机轮**——分屏 / PTT 松手 / 「换一批」chip / `shutter` 盲测 / TalkBack·Scanner / 系统动画缩放那一路 / `Msg.driving` 单独验（**这一格不需要人在场，只差设备**）/ B2 表#6 顺序取证（**核销时才发现它一直没进任何 ⬜ 表**）；② **Maestro 06/07/08/09 全部取不到**——driver 装不上（`INSTALL_FAILED_USER_RESTRICTED`，与锁屏无关，**需设备侧放行 USB 安装**）；③ **§11.4 状态可读性至今无外部分布读数**（唯一那次是 B2 闸里泓舟自评 6/6，1 人非外部；T14 协议与计分表已备好，材料要真机截图）；④ **Dock `safety_blocked` 待 Q16**（协议无结构化标记，客户端不许正则猜）。**下一步 B5（一趟重建收三笔账）**：`foldstate`「订阅即查询」不成立的定性（**任何新挂载的 `useFoldState` 实例都停在 flat 直到铰链再动**，坑账 §9.72）、B3′ 默认助理角色启用（第一问过、第二问不过；spike 分支永不合并）、`expo-battery` 低电量材质回落；另转 B5：`charging_list` 的 hmi `types.ts` 型名 + `_prov`、浅色下光球与壳底对比 12.4/255 不达标（转「动光球浅色高光参数」A-1 §10）、缺陷 A 在外屏横那一半（容器 192dp < 内容需求 269dp，要动 `ChatScreen` 布局才装得下 120dp 球）。**待裁**：唤醒率 5–6/10 不达标已裁**另开 KWS 阈值 A/B 批**（必须在同一个含 AEC 的包上做，排期未定）；**播报卡顿的无 AEC 对照构建归哪一趟仍待澄清**（并进 B5 vs 单开 B4′，两条互斥选项被同时选中） |
| 支付余项 | 等支付宝沙箱恢复、微信商户号到位；不做最终付款 |
| 端侧能力台账 | `orchestrator/edge/knowledge/capability_exemptions.yaml` 与 reachability 测试 |
| `memory_item` 信息衰减 | 出现第二个可复现实例后再立项，不凭单例改 supersede |
| M5 后续 | catalog 再次裁剪或范例规模/真实流量达到文档触发条件后启动 |
| 订座/票务 | 有真实 Provider 再做，不为对标造假能力 |
| 可执行性 canary | shadow 分布人工裁定后再由用户拍板；入口 `docs/design/2026-08-10-b6-actionability-forward.md` |

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

- QA：`docs/reviews/2026-08-30-qa-closeout-handoff.md`；
- 云端迁移/发布：`docs/dev-guide.md` + `docs/reviews/2026-08-17-cloud-data-migration-handoff.md`；
- Planner/安全：架构 §5.2.13、约定 §9.40、安全专题设计；
- mobile：当前 B2 实施计划和 `mobile/README.md`。

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
- `AGENTS.md` 是规则与当前入口，不是变更日志；逐批过程写 `docs/agents-history.md`。
- 已完成的 implementation plan 保留作实施证据，但不作为“下一步”入口。
- 文档中的“今天/最近”只用于引用原始用户话术；状态一律写绝对日期。
