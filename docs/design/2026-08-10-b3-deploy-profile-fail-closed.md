# B3 DEPLOY_PROFILE 生产配置档：prod 下 fail-open 即拒绝启动

> **状态**：**已实施并合入 main**（2026-08-11，提交 `0055629` + `fdbee6e`，实施记录见 §6）。源自外部评审采纳批次 B3，裁决见
> [`../reviews/2026-08-10-external-review-adoption.md`](../reviews/2026-08-10-external-review-adoption.md)。
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

---

## 6. 实施记录（2026-08-11，已合入 main）

**状态：已实施并验收。** 两个提交：`0055629`（步骤 1-2，Python 侧闸 + compose/.env.example）、
`fdbee6e`（步骤 3-4，Go 网关侧 + Registry admission）；本节记录与方案的差异和验证证据。

### 6.1 与方案的差异（都是落地时发现的，逐条给理由）

| # | 方案原文 | 实际落地 | 理由 |
|---|---|---|---|
| 1 | 强制表 §2.2 第 5 项「匿名回退身份禁用（校验冗余声明）」 | **做实**为层 2 端云通道身份：`CLOUD_CHANNEL_TOKEN` 非空、∈ `CLOUD_CHANNEL_TOKENS`、非示例值 | 层 1 的匿名回退确由第 1 项覆盖，但 edge→cloud 那条通道有自己的 token 面：`channelTokenAllowed` 在 `AUTH_REQUIRED=false` 时恒放行，翻 true 后若允许集为空则**恒拒绝**（端云直接断链）。两种都是 prod 不该出现的形态，一条判据同时挡住，比留一个恒真项有用 |
| 2 | 第 9 项「`LLM_API_KEY` 等凭证」 | **不单列检查**，由第 7 项 `REQUIRE_REAL_PROVIDERS` 的既有严格闸联动 | 方案本就写了「不重复造」。落地时确认：严格栈开着时缺凭证的 provider 决议本来就 fail-fast。这里再写一份「凭证非空」清单只会多一处会漂移的表（provider 数量在变） |
| 3 | 第 10 项「S2S / 视觉隐私三条件」 | 改由**源码级断言测试**守（`runtime/tests/test_privacy_defaults.py`），不进运行期表 | 这三个默认挡位只写在 HMI `DEFAULT_SETTINGS` 里，**没有任何 env 能把它们翻成默认开**——一条读 env 的运行期检查在这里恒真，是死检查。判据同 shop 域零范例事故：**「能力从哪里声明」和「能力写在哪个文件」是两件事**，检查要打在真正的声明处。顺带把同族的 `handsFree`/`wakeWordEnabled` 一并钉住 |
| 4 | 强制表十项 | **加了两项（11/12）**：`DEBUG_VEHICLE_CONTROL=false`、`GRAFANA_ADMIN_PASSWORD` 非默认 | 两项都有实现承载、都在 `.env.example` 里成文写着「非开发环境必须设为 false / 对外暴露必须改」。第 11 项尤其不能少：它开着时 collector 暴露**无鉴权**的 `POST /api/debug/vehicle`，经 NATS 直接改车速/档位/儿童锁——**那正是 VAL 安全门控的判定输入**。序号沿用 §2.2 的编号空间（9/10 空号保留），便于两边对齐 |
| 5 | §2.3「Go gateway 内对应实现一份」 | Go 侧**只校验它自己消费的键**（1/3/4/5），并按 role 声明每项适用范围 | 把 Postgres/Grafana 口令灌进网关容器只为让它「校验得全」，是把凭据铺得更广换一份重复读数。整栈层面不漏：持有该键的 Python 服务会拒绝启动。role 分档是必须的——edge 环境本就没有 `CLOUD_CHANNEL_TOKENS`、cloud 环境本就没有 `AUTH_TOKENS`，一张不分角色的表会判假红；而「键不在我环境里就跳过」是按缺席 fail-open |
| 6 | — | 未知 `DEPLOY_PROFILE` 值**拒绝启动**，不回落 dev | 方案没写这一条。`DEPLOY_PROFILE=production`（拼错）若回落 dev，运维会以为自己在跑硬校验而实际零校验——**静默回落正是本批要消灭的形态** |

### 6.2 落地时纠正的一个错误假设（值得记住）

把 `DEPLOY_PROFILE` 加进 compose 的 `x-python-env` anchor 后，我按「所有 Python 服务都用这个
anchor」继续往下做。**容器演练当场证否**：`DEPLOY_PROFILE=prod docker compose run registry`
照常起来了，服务正常 serving。查下来 `registry` / `edge-orchestrator` / `proactive` 三个服务
根本没有 `<<: *python-env`，它们各自列 env。

- 判据：**「写进 anchor」不等于「每个服务都有」**——与 shop 域零范例事故（门禁只读
  `manifest.yaml`，而 `mcp-bridge` 的能力由 `servers.yaml` 启动期合成）是同一族。
- 修法不是「下次记得」：新增 `runtime/tests/test_profile_coverage.py`，断言每个**自建镜像**
  的服务都带 `DEPLOY_PROFILE`（纯前端 hmi/dashboard 走显式豁免且必须写理由），且每个服务
  入口都够得着闸（Python 经 `aio_server()` 或显式调用，Go 经 `deployprofile.Enforce`）。
- 写这条断言时又踩了一次同类：第一版的 import 扫描只认绝对 import，`agents/_sdk/__init__.py`
  的 `from .server import serve` 断链，14 个 Agent 全被误判成「够不着闸」。**一个扫不全的
  结构断言比没有更糟——它会让人去改本来是对的代码。**

### 6.3 验证证据

**反向验证两头做**（B1 那一课）：

| 面 | 注入缺陷会红 | 对照仍绿 |
|---|---|---|
| Python 强制表 | 29 条单项突变逐条断言「**只**破这一项且 exit 78」 | 合规 env 在 prod 档零 violation |
| Go 网关表 | 12 条单项突变（含 role 隔离两向） | edge/cloud 各自合规 env 零 violation |
| dev 档 | — | 三种 env 各一条：零校验、**stderr 一个字都不打** |
| 覆盖面 | 漏配服务/够不着闸即红 | 扫描非空断言（`>=20` 个 Python 入口），防「恒空清单恒绿」 |
| admission | 无 token / 错 token / 越权申报 三形态 PERMISSION_DENIED，且**在写入之前** | 关闭时不带 token 照常注册 |

**真进程演练**（不是单测；`DEPLOY_PROFILE` 与默认 env）：

| 演练 | 结果 |
|---|---|
| `python -m registry.main`（prod，默认 env） | **exit 78**，10/10 项逐项打印 |
| `python -m orchestrator.cloud.main`（prod，默认 env） | **exit 78**，10/10 项 |
| `registry.main`（prod，**补齐 env**） | 打印「10 项生产配置校验全部通过」后继续启动，随后因证书路径不存在失败——**过了闸、死在别的原因上**，正是要证明的 |
| `python -m registry.main`（demo，默认 env） | 打印聚合 warning，**不退出**（继续跑到连不上 PG 才失败） |
| `DEPLOY_PROFILE=production` | **exit 78**，「不是合法档位」 |
| `DEPLOY_PROFILE=prod docker compose run --rm --no-deps registry`（容器） | **exit 78**（修 §6.2 之前是照常 serving，那次假绿是这次改动的起点） |

**回归**：`runtime/tests/` 87 passed；registry 65 / cloud 655 / edge 558 / agents 993 passed；
Go 侧 `go build ./...` + `go vet ./gateway/...` + `go test ./gateway/deployprofile -v` 全绿
（容器内 `golang:1.24`——本机无 Go 工具链）；`docker compose config -q` exit 0。

### 6.4 已知边界（不是缺陷，是本批的选择）

- **Python 侧闸在 `aio_server()` 里，不在各 main 的第一行。** 于是个别服务（如 registry）
  会先建好 store、连上 PG，再走到闸然后退出。「拒绝启动/不对外提供服务」的语义成立，但不是
  「第一行就退」。要更早只能逐服务改 main，与方案 §2.3「不逐服务改 main，一处生效」冲突——
  本批选后者。Go 网关没有这个问题（`Enforce` 就在 `main` 第一行，先于任何监听/拨号）。
- **`prod` 档从没有在**整栈**上跑通过**（只逐服务演练）。整栈 prod 需要先生成 mTLS 证书、
  配齐两层 token 与真实 provider 凭证，属于「量产迁移」而非本批范围；§4 验收判据第 2 条的
  「10 项矩阵演练」由 29 条突变 + 上表的真进程演练共同满足。
- **§2.2 的 prod-target 注释项**（JWT/OIDC、每服务唯一证书、Secret Manager、WS Origin 白名单、
  SBOM/SAST、trust cap 进主链）逐字保留在 `runtime/profile.py` 模块注释里，未实现。
