# B3 DEPLOY_PROFILE 生产配置档：prod 下 fail-open 即拒绝启动

> **状态**：已批准待实施（源自外部评审采纳批次 B3，裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)）
> **交付对象**：后续实施者
> **关联**：`runtime/`（Python 共享启动面）、`gateway/`（Go 侧）、`.env.example`、
> `deploy/docker-compose.yaml`、`registry/`
> **时机**：B1 之后近期（prod 档校验表引用 B1 的行为，见 §2.2 第 10 项）

---

## 0. 一段话给接手者

当前全部安全开关是「默认关、演示翻开」的 PoC 形态——`AUTH_REQUIRED=false`、
`PERMISSIONS_FAIL_OPEN=true`、`GRPC_TLS=off`（`.env.example:206-249`，R3.1/R3.2 拍板设计，
注释成文）。这不是缺陷，缺的是**第四种运行形态**：一个 `prod` 档，在其中任何 fail-open
配置都导致**服务拒绝启动**（而不是打印 warning 继续跑）。本批新增 `DEPLOY_PROFILE`
三档与启动期校验，dev/demo 档逐字保持现状，纯增量；顺带给 Registry 补一个静态 admission
（token→agent_id 绑定，默认关）。评审开的完整量产清单（JWT/OIDC、Secret Manager、SBOM、
每服务唯一证书）**不在本批**——记为 prod-target 注释项防遗忘。

## 1. 现状与证据

| 开关 | 当前默认 | 出处 |
|---|---|---|
| `AUTH_REQUIRED` | `false`（无 token 允许匿名，回落默认身份） | `.env.example:206-216` |
| `PERMISSIONS_FAIL_OPEN` | `true`（无 granted_scopes 注入 PoC 全量权限） | `.env.example:235-237`、`security/permission.py` |
| `GRPC_TLS` | `off`；开启后也是单张共享 mesh 证书（可证成员、不可辨服务） | `.env.example:239-249` |
| `OBS_CONTENT_CAPTURE` | `on`（可观测采集内容明文） | `.env.example:272` |
| `REQUIRE_REAL_PROVIDERS` | `off`（缺凭证静默回退 mock） | `.env.example:169` |
| Postgres 口令 | compose 硬编码 `cockpit/cockpit` | `deploy/docker-compose.yaml:90-91` |
| Registry Register | 接受任意调用方 Manifest，同 `agent_id` 直接覆盖，无身份绑定 | `registry/` handler；R3.1 设计只做了会话层 |
| trust-level cap | 声明存在，未进执行期决策主链 | `docs/conventions.md` 权限章 |

## 2. 方案

### 2.1 `DEPLOY_PROFILE=dev|demo|prod`（默认 `dev`）

- **dev**（默认）：零校验，逐字现状。`.env.example` 不改任何现有默认值。
- **demo**：软校验——不满足 §2.2 强制表时启动打印一段聚合 warning（一次性、显眼），
  不阻断。给「对外演示前自查」用，对应 R3.1 注释里「演示翻开」的清单化。
- **prod**：硬校验——§2.2 任一不满足，**进程以非零码退出**，错误信息逐项列出
  「哪个键、当前值、要求值、为什么」。

### 2.2 prod 强制表（v1，只列当前有实现承载的）

| # | 检查 | 要求 |
|---|---|---|
| 1 | `AUTH_REQUIRED` | `true` |
| 2 | `PERMISSIONS_FAIL_OPEN` | `false` |
| 3 | `GRPC_TLS` | `on` |
| 4 | `AUTH_TOKENS` | 非空，且不含 `.env.example` 示例 token 字面 |
| 5 | 匿名回退身份 | 禁用（`AUTH_REQUIRED=true` 时代码路径已保证，校验冗余声明） |
| 6 | `OBS_CONTENT_CAPTURE` | `off`（量产不明文采内容） |
| 7 | `REQUIRE_REAL_PROVIDERS` | `on`（真栈不许静默 mock；豁免走既有 `REQUIRE_REAL_EXEMPT`） |
| 8 | `POSTGRES_PASSWORD` | 存在且 ≠ `cockpit`（compose 需先把口令抽成 env 引用，见 §3 步骤 2） |
| 9 | `LLM_API_KEY` 等凭证 | 按 `REQUIRE_REAL_PROVIDERS` 既有严格闸联动，不重复造 |
| 10 | S2S / 视觉隐私三条件 | 维持红线默认（`classic` 默认、视觉默认关）——prod 档断言这些默认未被翻转为「默认开」 |

**prod-target 注释项**（校验代码里以注释存在，防遗忘，不实现）：JWT/OIDC 替换静态 token、
每服务唯一 mTLS 身份、Secret Manager、WebSocket Origin 白名单、SBOM/SAST、trust-level cap
进执行主链。触发条件：真实公网面或第三方 Agent 生态启动。

### 2.3 实现落点

- **Python 全体服务**：`runtime/profile.py` 新增 `enforce_deploy_profile()`——读 env、
  按档执行校验；在 `runtime/grpcio.py` 的 server 工厂（全 Python 服务建 server 的必经点）
  调用一次。**不逐服务改 main**，一处生效。
- **Go gateway**：`gateway/` 内对应实现一份（两个 gateway 入口各调用；Go 侧无共享 runtime，
  按其 config 读取惯例写）。
- **compose**：根 `compose.yaml` 透传 `DEPLOY_PROFILE`（缺省 dev）；Postgres 口令改为
  `${POSTGRES_PASSWORD:-cockpit}` 形式（dev 缺省不变，prod 可覆盖）。
- `.env.example` 尾部新增 profile 段，写清三档语义与 prod 强制表指针。

### 2.4 Registry 静态 admission（评审「身份绑定 agent_id」的 PoC 版）

- env `REGISTRY_ADMISSION_TOKENS`（缺省空=关闭，现状不变）：形如
  `tok1:navigation,tok2:charging-planner|scene-orchestrator`。
- 非空时：`Register` 调用必须带 meta token，且申报的 `agent_id` 在该 token 的允许集内；
  不满足拒绝注册（审计日志记 who/claimed/allowed）。
- prod 档强制表 v2（随第三方生态）再升级为证书身份绑定；本版把「任何人可覆盖任何
  agent_id」这个最大的洞先关上，实现小时级。

## 3. 实施步骤

| 步骤 | 内容 | 自检 |
|---|---|---|
| 1 | `runtime/profile.py` + 单测（三档 × 满足/不满足矩阵） | `pytest runtime/`（如无测试目录则并入 orchestrator 选集惯例位置） |
| 2 | compose 口令抽 env + `DEPLOY_PROFILE` 透传 | `make up`（dev 档）行为逐字不变 |
| 3 | Go gateway 实现 | `go build ./...` + gateway 单测 |
| 4 | Registry admission | 契约测试：无 token 注册（admission 关）通过；开启后错 token/错 agent_id 被拒 |
| 5 | prod 档演练 | 本地 `DEPLOY_PROFILE=prod` 起栈：默认 env 下**每个服务都应拒绝启动**且报错可读；补齐 env 后可起 |
| 6 | 文档 | `.env.example`、`docs/conventions.md` env 表、`docs/dev-guide.md` 各补 profile 段 |

> ⚠ 触及 `.env.example` 与 compose——CLAUDE.md 红线（env/CI 配置）范围，实施前需泓舟对
> 「按 B3 方案执行」确认一次。dev 档零行为变化是本方案的硬约束，验收第一条就是它。

## 4. 验收判据

1. dev 档：全量测试基线不变（根 pytest 数字只增不减）、`make up` 起栈行为与改前一致；
2. prod 档：§2.2 表逐项做「单项不满足→拒绝启动」矩阵演练（10 项各一次）；
3. demo 档：warning 聚合输出一次、不阻断；
4. Registry admission 契约测试绿；
5. `docs/conventions.md` env 表收录 `DEPLOY_PROFILE`/`REGISTRY_ADMISSION_TOKENS`。

## 5. 风险与不做

- **风险**：启动校验误伤 CI/测试环境——校验只在 `prod` 档硬阻断，CI 不设 `DEPLOY_PROFILE`
  即 dev，零影响；nightly 若未来跑 prod 档演练，属显式选择。
- **不做**：JWT/OIDC、Secret Manager、每服务唯一证书、SBOM/SAST/镜像扫描、trust cap 主链
  接入（§2.2 prod-target 注释项）；WebSocket Origin 白名单随 JWT 一批做（同为公网面前提）。
- **不做**：把现有任何默认值改严——「PoC 默认关」是拍过板的形态，本批只加档不改默认。
