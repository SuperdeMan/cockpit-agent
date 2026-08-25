# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

> 🧭 **只想知道「现在做什么」**：直接跳 **§4.1 的「接手入口」挑选表**（搜 `#### 接手入口`）。
> 探索式 QA 轮已**全部收口**，因此**没有默认的「下一个」**——那张表逐条写清了每个候选
> 「为什么现在能做 / 不能做」，**挑一个再开工，别按顺序往下扫**。
> 开工前的固定动作（先确认档位 `target show`、跑全量的固定口径）在同一节。
> §4.0 是当前快照（部署形态 / 测试基线 / 各类读数），**引用任何数字前先看它的日期**。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化 PoC**。主干与云端中枢、R2-R4 硬化主题、可观测台（badcase 排查贯通）、
旅程级验证体系（L3 journeys + L4 HMI CDP）、智能化升级 M0a→M4（S2S 双语音链路 / 声纹多用户 /
统一主动引擎 / 受控 MCP 桥 / Task Ledger + Outcome Verifier / Skill 层，已过跨阶段组合总体验收）
与 M5 数据飞轮（P0→P3a + P3 收尾）均已落地；首批真实外部能力在线（高德 / 和风 / Exa / Tushare /
api-football，无凭证回退 mock）。当前全量测试基线与批次证据见 §4.0 快照；逐批历史流水在
[`docs/agents-history.md`](docs/agents-history.md)。

---

## 2. 项目地图（先看文档，再看代码）

| 想了解 | 看这里 |
|---|---|
| 为什么这么设计（全局）| `docs/architecture/cockpit-agent-architecture.md` |
| 接下来分几步做、怎么验收 | `docs/architecture/phase1-implementation-plan.md` |
| 核心模块怎么编码 | `docs/architecture/detailed/ws{3,4,6,8}-*.md` |
| **怎么接真实 provider（高德/和风样板）** | `docs/guides/provider-integration.md` |
| **怎么扩 info 能力 / 加新独立 Agent 并打通** | `docs/design/2026-06-20-info-agent-expansion.md`、`docs/design/2026-06-20-standalone-agents-roadmap.md` |
| 前瞻设计 / 问题分析（多意图、ASR、车控、云端中枢、可观测）| `docs/design/` |
| 工程规则与铁律 | `CLAUDE.md` |
| 怎么搭环境、codegen、单服务调试 | `docs/dev-guide.md` |
| **当前云端数据迁移现场与远程开发接手** | `docs/reviews/2026-08-17-cloud-data-migration-handoff.md`（**迁移已 APPLIED、切云已验证通过**——迁移 apply 的两条根因见 `docs/design/2026-08-18-redis-migration-identity-root-causes.md`，验证通路的六条根因见 `docs/design/2026-08-18-cloud-switch-verification-root-causes.md`） |
| intent/scope/端口/错误码/env 速查 | `docs/conventions.md` |
| 怎么验证 | `test/README.md` |
| 历史批次流水（只进不出，查证据用） | `docs/agents-history.md` |

代码目录职责见 `CLAUDE.md` §3；每个服务子目录都有自己的 README。

---

## 3. 铁律（违反即视为 bug，详见 CLAUDE.md §5）

### 唯一运行环境

- 根目录 `.env` 是唯一的运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 全栈只允许用 `make up` 或 `docker compose -f compose.yaml ...` 启动；根 `compose.yaml` 显式加载根 `.env`，并以 `deploy/` 为 included Compose 的项目目录以保持构建路径不变。
- 不得直接以 `deploy/docker-compose.yaml` 为首个 Compose 文件启动，否则真实 Provider 可能静默回退 mock。

### 可切换远程真栈实施期红线

真栈动作前先运行 `python scripts/dev_stack.py target show` 确认目标。
任何真栈动作（包括运行 E2E、Compose 或 `make up`）前，必须由统一入口按仓库根目录定位并读取
`dev-stack.local`。它是仓库根目录的 Git-ignore 文件，不能按当前工作目录误判缺失：文件
缺失时按 `target=local` 处理，文件损坏则 fail closed。改动任何脚本目录或 manifest 前同样
必须读取该文件。该文件只允许 `target=local|cloud`，不得保存
token、密码、私钥或 URL。`target=cloud` 时禁止启动本地 Compose；本地只承载编辑、单测、
静态检查和 Vite。`target=local` 时继续只用根 `compose.yaml` / `make up`，根 `.env` 仍是唯一
运行时来源。

cloud deploy 只接受干净、已提交、main 可达的 SHA，不自动 commit、merge 或 push；`git push`
仍需单独授权。未显式标记 `remote_safe` 的 E2E 不得在 cloud 缺省运行；
`remote_mutating=true` 只允许精确 `--id` + `--allow-mutating`；该开关只是技术门禁，
不替代支付、商户写、真实车控、删数据或系统配置的本轮人工红线授权。本阶段
不得自动写入 `target=cloud`，也不得停止另一个 agent 正在使用的本地 Docker。

三存储迁云入口是 `scripts/cloud_data_migration.py`。online 本地不停写，final 必须先取得停写
授权并确认其他 agent 已结束；两阶段都是 replace，不是 merge。`apply/rollback` 默认 dry-run，
只有显式 `--apply` 才写入。工具不改 `.env`、安全组、Tailscale、CI/CD、systemd 或 schema，
不删除任何本地/云端卷、备份、release、镜像或迁移包；失败时 PostgreSQL、Redis、Collector
按同一份迁移前备份整组恢复。完整命令与授权检查点见 `docs/dev-guide.md` §8。

1. **车控只经 VAL**。任何组件（含 LLM/Agent）不得直接碰 CAN/SOME-IP。
2. **LLM 不直连车控**：LLM 只产"意图/计划"，车控由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
3. **危险动作二次确认**（`require_confirm=true`）。
4. **不改编排核心来加 Agent**：Agent 经注册中心被发现，新增 Agent 不动 orchestrator。
5. **密钥/token 不进代码、不进 commit、不进日志**；用 `.env`（已 gitignore）。
6. **改 proto 先改 `proto/` 再 codegen**，不要手改生成代码。

---

## 4. ⚠️ 当前真实状态（别假设没验证的东西能跑）

### 4.0 当前快照（2026-08-19）

意图落域对抗测试按这个顺序接手：运行手册
[`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md) → 最终验收
[`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`](docs/reviews/2026-08-04-review-intent-adversarial-finalization.md)
→ 逐批证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§26**。
历史流水只查 [`docs/agents-history.md`](docs/agents-history.md)，不要再抄回本文件。

**GitHub CI（2026-08-17 收口，流水 §54）**：`#350`–`#404` 连红 55 次、15 条真失败三族
（时区尺子未随 `runtime.clock` 收敛 / `chown(0,0)` 写死非 root 必炸 / `E2ECase` 契约漂移）
已全部修完；叙述原文归档 history **§60.2**。
> 两条仍有效的通用教训：**本地跑测试显式 `TZ=UTC0` 才等价 CI**（Windows CRT 认它）；
> **CI annotation 每 step 只保留 10 条**——红灯数到 9~10 就假定被截断，
> 改用 Linux 容器（`git bundle --all` + `python:3.12` + 非 root + `--init`）取全集。

**当前部署形态（2026-08-22 更新）**：`dev-stack.local` = **`target=cloud`**。
云端 release **`7a0e03a`**（2026-08-22 I-030 跨组批：组指代 + 跨组算子 + 下发面逐步
选组；`status` = ok、5/5 端点 healthy、`verify` = **verified**（`release_sha` 非空，
不是那种「一趟什么都没跑」的空成功）；**迷你集 `--group candidate` 20/21**——
考点 **CD5 3/3 `[det]`** / 对照 **CD7 3/3 `[det]`**（两条共用同一份前提＝干净 A/B）/
跨组 **CD6 2/3**，唯一那次红是 **T1 前提就没成立**、判据自报「读数不作数」）；
此前依次是 `9bdadda`（2026-08-22 Q12 规格维复验批，`--group spec` 9/9 全 `[det]`）、
`5c6300f`（2026-08-21 规格值域契约 `input_schema` + 真机台账门禁 +
单候选门店闸）、`032dd82`（person-pickup 批四次 apply：
`d89bebd` → `a8d2c78` → `2f2a3e7` → `032dd82`）、`e5cef21`（第 8 步）、
`86a9490`（第 7.5 步）、`8bea3b8`（Q2 残余 + `AUTH_TOKENS` 修复）
与切云那趟的 `34d72d7`、
三存储数据已迁入并逐表核对；
`python scripts/dev_stack.py status` = ok（5/5 端点 healthy）、
`verify` = **verified**（`e2e_remote_safe`、minimax/MiniMax-M3、lock `e2e`）；
`car-agent-backup.timer` 活跃、Tailnet Serve 五入口齐；
**cloud 缺省 E2E 2/2 PASS**（`e2e_protocol_smoke` **1/1** + `e2e_remote_safe` **8/8**，
零 skip；**2026-08-22 I-030 批在 release `7a0e03a` 上复跑实测**，
`run_e2e --target cloud` 无 `--id`）。交接页
[`…cloud-data-migration-handoff.md`](docs/reviews/2026-08-17-cloud-data-migration-handoff.md)
§6 七条完成判据**全部达成**。**本地 Docker 已停**（32 容器全 `Exited`、Docker Desktop
优雅退出、零进程残留；**只 stop 不 down**——卷停前停后同为 37 个、镜像/release/迁移包
一个未删）⇒ 回本地真栈的固定次序是 `target set local` → 人工启动 Docker Desktop →
`make up` → `status`。
根因分三段：迁移 apply **RC1–RC7**（history §55，另有两处未编号：PG 死列
`agents.embedding`、发布闸把 docstring 读成 schema 变更）、切云验证 **RC8–RC14**（§56）、
前端联调车道 **RC15/RC16**、单测环境串味 **RC17**（§57）——**合计 17 条编号根因**；另修一条偶发竞态（L3 报告被 mtime 分辨率读成陈旧，§57.11）。
> ⚠ 七条接手须知：① **真栈命令一律走 PowerShell**——Git Bash 的 `ssh` 是 MSYS 版，
> 会吃掉 `subprocess.list2cmdline` 的 `\"` 转义，远端 bash 报 `unexpected EOF`；
> ② 本地 `.env` 现含 `VITE_WS_TOKEN`（云端 `AUTH_REQUIRED=true` 必需，**不进 commit**）；
> ③ `target=cloud` 期间**不要起本地 Compose**，本地只承载编辑、单测、静态检查与 Vite；
> ④ 云端连接三参数走环境变量 `CAR_AGENT_DEPLOY_HOST` / `CAR_AGENT_DEPLOY_USER` /
> `CAR_AGENT_SSH_IDENTITY`，**不在 `.env`、不在 `dev-stack.local`**——缺任一项时
> `dev_stack` 返回 `configuration_rejected`（rc=2）；⑤ **cloud 档要两个键，
> `.env.example` 里刻意只有一个**：`VITE_WS_TOKEN` 有、**`TAILNET_FQDN` 没有**
> （后者派生五个云端端点，缺了 fail closed）。**不是忘了，是不能补**——`.env.example`
> 在发布闸里属 `runtime_config_contract`，该类别**无放行通道**（只有 `infrastructure`
> 有 digest 批准），且 `changed_paths` 取「已部署 SHA → 目标 SHA」全量 diff ⇒
> 这一笔只要在 main 上，`deploy` 就永远 `plan_rejected`，**连「先发一次版消化掉」
> 都不行**（发版本身就是被拒的那个动作）。2026-08-19 实测过一次（`c03d5a3` rc=3，
> 已由 `030c049` 撤回），两个键的说明改落 `docs/dev-guide.md` §可切换真栈。
> 要动它得先给该类别设计一条与 infrastructure 对等的批准锚点。
> ⑥ **这台机器 `python` 不在 PATH**（可执行文件在
> `%LOCALAPPDATA%/Programs/Python/Python312/`）。它会恶化成一个**空成功**：
> `e2e_manifest.yaml` 的 command 就是 `python`，于是 `dev_stack verify` 返回
> `case_ids: []` / `release_sha: null`——**一趟什么都没跑，却只报一行 `failed`**；
> `scripts/tests/test_e2e_target.py` 同样 collection error。
> **跑真栈与全量单测前先把它加进 PATH**（2026-08-19 实测，两处都撞到）。
> ⑦ 跑全量单测的固定口径（importlib / PATH / 干净 env / 隔离）**见下方「跑全量的
> 固定口径」块**——那里是唯一版本，这里不再抄。

**最新后端全量基线（2026-08-24 MiniMax-only QA 复验收口，`target=cloud` +
本地 Docker 已退）**：`python -m pytest -q -n auto --dist worksteal` =
**6969 passed / 32 skipped 零红**。
⚠ 耗时受宿主负载影响很大（慢的都是真子进程/全语料守卫），**别拿时长当回归信号**。
本跳 `6933 → 6969` = **+36**：cloud 编排净 +13（候选身份 / 后视镜焦点 /
五类窄 route hint 与误伤集 / 评测清单同构）、edge 门锁极性 +10、mcp-bridge +4、
reminder 原子批建 +2、两套 QA 工具 +7。上一跳 `6902 → 6933` = **+31**，逐条点号：`test_candidate_sets.py` **+11**
（组标签载体 3 / 组指代 5 / 下发面逐步选组 3）、`test_candidate_query.py` **+13**
（跨组比较·合计·切句 5 / 越界与点不到分流 2 / **误伤对照 4** / 组指代 1 / 相等 1）、
`test_engine_candidate_shortcut.py` **+4**（**engine 层接线守卫**——反向验证第一处
就露出「挂点零测试」）、三个产生方各 **+1**（组标签 = 卡上那个称呼，断言两处相等）。
上一跳 `6897 → 6902` = **+5**（08-22 白天复验批：mcp-bridge 选品续跑 4 + 规格槽跨跳保真 1）。
对账链：**6969**（08-24 MiniMax QA 批）← 6933（08-22 I-030 批）← 6902（08-22 复验批）← 6897（08-21 规格值域批）
← 6865（person-pickup 批）← 6786（第 8 步）。

> ⚠ **那条「73 vs 32」的差额归因写了三版，前两版都错，2026-08-21 把成因直接修掉了**
> （`_git_bash` 按 `parents[1]` 找 Git Bash，换个 shell 就算错 ⇒ 整族 skip）。
> 完整经过与两条判据见下方「跑全量的固定口径」的 **skip 对照**那条（**唯一版本**），
> 流水 history **§65.6**。这里不再抄——**同一件事在两处各写一版，正是它错三次的方式**。
历跳对账链（6623←6551←6257←6198←6127←…←5408）已归档：索引与原文全文见 history
**§60**，各跳批次证据 §30–§59。全量 pytest 盘点结论（无可安全精简的存量、耗时两极分布、
慢的都是刻意的真子进程/全语料守卫）见 history **§60.3**；其并行化落地（2026-08-23，
25min→~5min，四趟结果集逐字对账 + run_e2e dry-run 40.8s 的 profile 留卡）见 **§68**。

**跑全量的固定口径**（历跳教训收敛于此，每条这里是唯一版本；违反任一条都会制造假红或假读数）：
- **命令**：仓库根 `python -m pytest -q -n auto --dist worksteal`（= `make test`，需
  `pytest-xdist`，2026-08-23 起并行化：25min → ~5min，18 核串行是当时的主要矛盾）。
  `--import-mode=importlib` 已收敛进根 `pytest.ini` 的 addopts——裸 `pytest` 与 `make test`
  同口径，「不带它会被 `agents/*/tests/test_agent.py` 同名模块 collection error 截断整趟」
  的坑自此**不存在**（消灭而非记住，同 `_git_bash` 那条的处置）。
  **并行不改变对账口径**：loadfile×8 / worksteal×14 / worksteal×auto 三趟实测均
  **6933/32 与串行基线逐字一致**。若并行下偶发红：先按下方「隔离」条排查外部并行方，
  再串行复跑对照（`python -m pytest -q`，不带 `-n`）——红聚在同一文件内新增用例上才怀疑
  共享状态，届时用 loadfile 档降级取证（`--dist loadfile`，同文件同 worker）。
- **PATH**：`python` 必须在 PATH（本机在 `%LOCALAPPDATA%/Programs/Python/Python312/`）。
  不在会让 scripts/tests 整族 **192 条假红**（e2e manifest 校验要求 `python` 可执行存在），
  真栈侧还会让 `dev_stack verify` 变**空成功**（见上方接手须知 ⑥）。
- **干净 env**：不得带 `PYTHONIOENCODING`（拉子进程的用例会 **188 条假红**）。
  ⚠ **这台机器的 shell 里它是默认设着的**（`utf-8:surrogateescape`，2026-08-19 实测，
  第一趟全量因此白跑）——**跑之前先把它打印出来看一眼**，别把「我没设过」当成「它没被设」；
  清法 `Remove-Item Env:PYTHONIOENCODING`。Windows GBK 宿主是常驻放大器，
  新写子进程/出站验签代码先想编码两端。
  ⚠ **2026-08-20 又栽了一次，形态一模一样**：我清掉了 `CAR_AGENT_*` 却忘了它，
  跑批开头把两个都打印出来才看见 `PYTHONIOENCODING=[utf-8:surrogateescape]`
  ——当场停掉重跑。**「打印出来看一眼」要连着打印，别只打印你正在想的那一个。**
- **隔离**：不与任何 e2e / docker build / PowerShell 真栈命令（`deploy`/`status`/`verify`）
  并行——`test_e2e_wrappers_ci`（起真 PowerShell，单跑 ~93s）与 `test_e2e_stack_lease`
  （模拟 runner lease 树）都会被并行方污染成假红，隔离复跑即绿。**红了先问是不是前提变了。**
- **跑批期间不动工作树**：pytest 在 collect 那一刻定死用例集，**读数属于 collect 时的
  工作树**——期间 stash、追加用例、更新测试锚，读数就对不上 HEAD。
- **skip 对照**：`target=cloud` + 本地 Docker 停 = **32 skipped**（2026-08-20 两趟实测，
  含一趟 `-rs`）；`make up` 起本地栈会把其中 **9 条 Redis 集成**转 passed。
  **切换部署形态后 skip 数变化不是回归。**
  ⚠ **那 41 条的归因错过两次，2026-08-21 坐实并把成因修掉了。** 第一次记的是
  「`CAR_AGENT_*` 三件套」（08-20 改正：全仓没有任何 `skipif` 引用它们）；
  第二次记的是「PATH 里有没有 git」——**也不对**。真因是 `_git_bash()` 固定取
  `Path(shutil.which("git")).parents[1] / "bin/bash.exe"`，而 PATH 里暴露的 git
  是 `<GitRoot>/cmd/git.exe`（PowerShell）还是 `<GitRoot>/mingw64/bin/git.exe`
  （Git Bash）**取决于从哪个 shell 跑**：前者算对、后者算成
  `<GitRoot>/mingw64/bin/bash.exe`（不存在）⇒ 整族 skip。
  ⇒ **同一台机器、同一份代码、同一个 HEAD，换个 shell 就差 41 条。**
  已改成逐级向上找 `bin/bash.exe`（两种布局都命中），**两个 shell 现在一致**。
  32 条的真实构成（`-rs` 实测）：Redis 集成 9 / Windows-POSIX 能力差 ~12
  （symlink·FIFO·权限位·属主）/ 缺 `LLM_API_KEY` 与 ASR 端点 8 /
  `opentelemetry` 未装 + redis 探针镜像不在本地 2。
  > **两条判据**：① 对账差额时把「我同时改了什么」和「它真的读了什么」分开——
  > 这一条我照做了，仍然错，因为**「它真的读了什么」也要一路读到底**
  > （读到 `shutil.which` 为止不够，要读到那个 `parents[1]` 算出来的具体路径）；
  > ② **一个差额被记错两次，就别再记第三次了——去把它消灭掉。** 环境差异写进
  > 文档是权宜，能收敛的收敛掉才是修法。
- **对账**：基线对不上先怀疑**基线陈旧**（并行工作线合入过——「我没动过 git」不等于
  「HEAD 没动过」）；净增量要跟同一 SHA 逐条点号，不能跟文档里那个数比。
**2026-08-15 EVA 主题四批**（能力状态；完整叙述与读数 history **§36–§40**、修复后终态
计分 **§40.2**，原文归档 §60.2）：① P3 簇三 RFC——G8 路线会话 + `navigation.reroute` /
G4 主题行程检索步 / G9 跨城市 / G7 陈述 vs 请求台账（§36）；② 指令集 25 语料真栈验证 +
遗留六卡收口（§37/§38）；③ 余项立卡 E1–E5 全部处理（§39）；④ 双档复跑修复——容器时区族
8 处收敛 `runtime/clock.py` + 当轮忌口被记忆压过 + 约束词被当检索词（§40）。
跨批次计分不可直减（不同时点独立采样），仍 ⚠ 的条目逐条有归属、无新缺陷。

当前对标状态一句话：时间约束（到达时限 + 事件反推用餐窗）、真沿途、多城保序与归城校正、
模糊目的地推断、记忆六维消费（subject/polarity/轨迹/路线偏好/口味/无障碍）均**真栈在案**；
对抗语料 **619 唯一输入**（上界 619，余量 0）、catalog **152 条**、架构 **v1.37**
（该行的 v1.26 是 EVA 批时点读数，2026-08-16 随 QA 轮 Q6/Q10/Q5 与 Q12 两跳补齐、
2026-08-17 随 Q7 残余到 v1.29——新增 **§5.2.4 省略式开关的确定性消解**；
2026-08-19 随 Q2 残余到 v1.30——新增 §5.2.5 候选集上的聚合问答；
同日随 Q10 残余到 v1.31——新增 §5.2.6 候选集下发面：文本/按钮双入口收敛；
同日随第 7.5 步到 v1.32——§5.2.5 增**第三种算子：序数取值**；
同日随第 8 步到 v1.33——新增 §5.2.7 能力缺席：从「声明了」到「可达」是一条五段链；
2026-08-20 随 person-pickup 卡到 v1.34——新增 §5.2.8 指代到人：占位与具名是同一个实体、
救济路径要闭合；2026-08-21 随 Q12 规格维到 v1.35——新增 §5.2.9 能力槽位的值域契约：
声明了 ≠ 可达；2026-08-22 随 I-030 跨组到 **v1.36**——新增 §5.2.10 候选集的
「哪一组」：指代要闭合到组这一维；2026-08-24 随 MiniMax-only QA 复验到
**v1.37**——新增 §5.2.11 尾部方差闭环：确定事实不经过模型转述）。
**仍未做**：G10 订座票务（搁置，诚实桩）、~~复合句人称接送解析（§4.1 ②）~~
**✅ 2026-08-20 收口**（连同 Q5 残余，history **§64**；真栈 10/24 → **41/45**）、
**探索式 QA 轮的卡内残余**（~~Q2 的确定性消费方~~ ✅、
~~Q10 的双入口收敛 + Q2 的候选集下发面~~ ✅、~~Q8 能力缺席 + Q11 offer 准入 + P2 长尾~~
✅ 2026-08-19 第 8 步交付（六条能力上目录、offer 准入闸、P2 五条，流水 history **§63**）；
~~**Q12 的规格维**~~ **✅ 2026-08-21 收口 + 08-22 真栈复验通过**（B6 §4 `input_schema`
落地 + 真机台账门禁，history **§65**；复验掀开并修掉「选品这一跳整条链是断的」，
history **§66**；迷你集 `--group spec` **9/9 全 `[det]`**）、
~~**I-030 跨组比较**~~ **✅ 2026-08-22 收口**（组指代 + 跨组算子 + 下发面逐步选组，
history **§67**；取证把卡上定性改了一档——**不是「比较做不了」而是「跨组给出一个
算错的确定性答案」**，架构 **v1.36** §5.2.10、契约 §9.32）；
**Q10 的门店侧（I-024）仍未做**——见 §4.1 ① 逐卡表；~~**Q5 的实体归一**~~ ✅ 已随 person-pickup 卡收口；
另有一条**第 8 步记的账**：I-033 的**跨轮追问**
「哪个数据源失败了」需要会话级数据源账本（Q6 执行账本的同族扩展面），本批只做到本轮披露。
阶段 0/1/2/3/4 与 Q13/Q2/**Q7 全维度**/Q5/Q11/Q10/Q6/Q12 主体/**Q8**、
**存量数据清洗**已完成，
逐卡状态见 §4.1 ①、流水 history **§41–§53 + §58/§59/§61/§63/§65/§66**、归档索引 **§47.5**）。
⚠ QA 那一轮**验的是别的东西**：EVA 四批验「能力面有没有那个维度」，QA 轮验
**会话状态、归属、审计与真实性**——所以它抓到的三个 P0 与 44 个 P1 与上面那些读数不矛盾，
两者测的不是同一个面。

**历史批次已归档，索引在 history §38.1 / §40.1 / §60.1**（三次洁癖整理）：§38.1 收 EVA 二轮五批
（§33/§33.1）、商户链路收口三段（§30–§32.2；2026-08-12 商户批在 §29/§30）、B5/B6 后
分组实测与真栈演练（§27/§28）；§40.1 收上方四批的完整叙述与 §4.1 的五段完成态
（journeys 两红 §35 / 接地卡 R1 二期 §34 / EVA 二轮六批 §33 / 支付四批 §28 /
商户七批 §30–§32.2）；§60.1 收 §4.0 的基线对账链与 CI 连红段（原文全文在 §60.2）。**归档是把叙述换成索引，不是再抄一遍**——段内 catalog/分组
数字均为批次时点读数，当前数以上方基线段与下表为准。⚠ 从演练段沉淀并仍有效的两条：
`--lane ci --full` 只选 1 个用例（引用它的 exit 0 别当 e2e 全绿）；
`test/eval_actionability.py` 是取证脚本不是准入闸（不进 CI blocking）。

**CI 已有四条 blocking 门禁**：skills 契约、范例契约、L0 对抗 strict
（`scripts/check_intent_gate.py`，B2）、**能力完整性**（`test/eval_capability_integrity.py`，
B4）。四条都是确定性检查、零 LLM、零网络——「跌破基线告警不阻塞」的哲学只适用于会随语料
漂移的意图基线，与这四条准入不同，不要合并。

| 意图落域证据 | 当前可引用事实 |
|---|---|
| L0 discovery | **85/85**，658 条 / **619 唯一输入**（bounds [450,**619**]，仍**恰好用满**。十三次递进 560→…→596→610→614→619，逐次占用理由写在 `suites.yaml` 头部——最近一跳是 `reminder.create_batch` 的正 2 / 硬负 2 / relation 1，且因进入 route_hint 修复资产全部标 `seen_regression`；上一跳是 **person-pickup** 跨域边界双向各 2 例。没有删除旧尺子压数字） |
| gate 规模 | **139 stable / 129 唯一输入**，L0 strict **25/25，exit 0** |
| 对比模型正式 baseline | [`baseline_intent_adversarial.json`](docs/reviews/eval/baseline_intent_adversarial.json)；干净 `f0af9c0`，锁定 `deepseek:deepseek-v4-flash`，由当前 L3 原始字节/摘要/时间/精确路径契约重新取证并写入。**未随 `32e8718` 重取**——它仍是 DeepSeek 在 `f0af9c0` 的证据 |
| DeepSeek 完整 gate | **147/147**：L0 25、L1 117、L2 4、L3 1；exact **121/121**，raw 幻觉/校验后逃逸/不稳定均 **0/121**；L1/L2 各 **2 个独立进程 × 每进程 3 样本**（`f0af9c0`） |
| MiniMax 主模型 gate（`32e8718`，2026-08-10） | **141/147**；exact **116/121**、required **99/103**；raw 幻觉 **3/121**（原 8）、逃逸 **0/121**；不稳定 **4/121**（原 6）；`pass 141 / unstable 4 / stable_fail 2`，资格仍 `eligible=False` |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline 的 invocation 新鲜、exit 0，只证明该授权 case/claim。2026-08-10 新增 **A1-5**（weather→去处推荐，claim `adaptive_replan_continuity`）两趟独立各 **1/1**，但它服务的 case 仍是 `reviewed`，不进 gate 选集 |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 fallback **0**；MiniMax **11/122**，其中未声明 **2**（原 4） |
| 工具通道（协议层，**可跨 provider 比**） | 走成 `toolcall` 的比例是 **provider 属性**：`minimax:MiniMax-M3` 同用例 **13/27（48%）**、跨域 20 条 **9/20（45%）**；`deepseek:deepseek-v4-flash` 两组 **35/35（100%）**（p≈0.0002）。⚠ 代价只在**需要模型自己填结构化字段**的多阶段计划上兑现——那 20 条 stable 上两档通过率都是 20/20（findings §24）。**2026-08-10 起 `PLANNER_TOOLCALL_SALVAGE_RETRY=on` 默认开**：gate L1 双臂实测把 MiniMax 从 **51.3%（60/117）抬到 85.5%（100/117）**，+34.2pp、p=2.3e-08、重试成功率 ≈70%，代价墙钟 +38.5%（findings §26.5）。**引用 45~48% 那组数时注意它是 off 档口径** |
| 代码回归 | **全量基线只在本节顶部写一次**（本行刻意不复述那个数——它已经被这么写错过一次。「同一件事在两处各写一版」正是本文件记过会错三次的那个形态）；2026-08-24 fresh collect：`orchestrator/cloud` **1018** / edge **806** / mcp-bridge **559** / reminder **171**；其余最近读数：nearby **100** / navigation **167** / trip **85** / memory **265** / chitchat **62**。Skill / Exemplar **309 条 / 22 域**，四条确定性门禁均通过。catalog 目录 **152 条 / 13278 字符**，余量 16000−13278=**2722**——**每次加能力都要把余量重新看一眼**，撑满时该做的是检索化 catalog 不是放大预算。端侧能力面 **84 条**（vehicle 80 + media 4）/ VAL 车控对象 **68**。⚠ 2026-08-11 起 `VEHICLE_INTENTS` **不再手写**，由 `commands.yaml` 各对象的 `edge_intents` 派生（B4），数字不变但改的地方变了 |

⚠ **上表 MiniMax 行是 `32e8718` 读数，与当前代码已差好几批**（此后合入了 clarify 型范例
机制、salvage 重试默认开、B1–B4 四批）。**当前 SHA 没有对应的全量 gate 读数**——要引用
主模型总分就得重跑一次完整父 bundle。`5e8247d` 那批是换池态、案例集已不一致，**不要挪用**。
三批 141/141/140 **不是同一批红灯**（每批点名的 unstable 都换了一组），逐批过程记在
history §17–§19；沉淀下来的判据是 §4.3 的两条：读报告先看是哪几条、`eligible=True` 要压
整体底噪而不是修点名的那几条。

首份正式 baseline 已存在，但它是 **DeepSeek 对比/参考模型**在固定 provider、资产与代码快照下的
意图理解与落域证据；不证明 MiniMax 主模型、Agent 业务结果、外部 Provider 内容或跨模型平均质量。
MiniMax 在 `32e8718` 那批即使 process/provider/embedding/L3 身份完整，仍因 `gate_failures`、
raw 幻觉、未声明 fallback 与 `unstable_results` 被资格闸拒绝。后续写入仍必须由一次新的完整父报告
明确 `eligible=True`，不得手工改正式文件。

### 4.1 活跃待办（只列仍需行动的）

> 本节**只放还要动手的事**。已完成批次的流水一律只查
> [`docs/agents-history.md`](docs/agents-history.md)，不在这里复述
> （2026-08-15 洁癖整理把 journeys 两红 / 接地卡 R1 二期 / EVA 二轮六批 / 支付四批 /
> 商户七批五段完成态收走，归档索引见 history **§40.1**）。

#### 接手入口（2026-08-20 起：**没有默认的「下一个」了，要挑一个**）

探索式 QA 轮的编号序列与那一步不编号的（Q5 残余 + person-pickup 卡）**已全部走完**。
**剩下的都带前置条件或需要拍板**，按「值不值得现在做」排在下面——**挑一个再开工，
不要按顺序往下扫**：

| 候选 | 为什么现在能做 / 不能做 | 入口 |
|---|---|---|
| **Android 陪伴端 App（新客户端 `mobile/`，2026-08-23 立项）** | **M0 地基已完成（2026-08-25，M0-1..M0-8 代码面全过）**：Expo SDK 57 脚手架（小舟随行 / com.xiaozhou.companion / 三档 variant）、Metro+tsconfig 共享接线（`@shared/*`）、白名单守卫（反向验证三头、逐条具名红）、config/onboarding、主链调试屏（共享 ws.mjs 队列语义 fake 实证）、`scripts/build_mobile.ps1` 出 APK（**首构 BUILD SUCCESSFUL 21m48s**；定案=**ASCII 镜像工作区** `D:\Android\builds\`——subst 双根与中文单根两形态六连败实测不可用，证据链坑账 §9.11-17）、CI mobile job + mobile-apk 手动档。**真机面待 E3/E4 泓舟**（`adb devices` 仍空、tailnet 安卓节点离线）：五条补验清单在实施记录末条，缺一不算 M0 收口；App 专属 AUTH_TOKENS 条目也是 ⚠ 泓舟（M1 期间，手机档不含 vehicle.control）。**下一步 M1 对话 MVP，先做 M1-1 会话状态机（⛔ 本阶段最重）**。每次开工先跑 `check_android_env.ps1` | 接手从 [`docs/design/2026-08-24-mobile-app-implementation-plan.md`](docs/design/2026-08-24-mobile-app-implementation-plan.md) **§0 接手须知**开始（逐任务执行真相源，**M0 实施记录已写在其 §3 末**）；需求/选型/架构判断在 [`docs/design/2026-08-23-hmi-android-app-plan.md`](docs/design/2026-08-23-hmi-android-app-plan.md)（§9 决策记录） |
| **I-024 门店侧**（Q10 残余）| **可以开工但影响面要先量**：门店选项卡是 `NEED_SLOT` 结果，而 `extract_focus` 只从**成功步**抽候选 ⇒ 门店候选集根本不存在。放宽抽取影响面远超本卡，**动之前先枚举谁在读候选集**。⚠ 2026-08-22 起**多了一个读候选集的地方**（组指代 `resolve_candidate_scope` + 逐步下发 `candidate_set_for`，§9.32）——那份枚举要把它算进去 | 下方 ① Q10 行、契约 §9.28「三条边界」/§9.32 |
| **多意图复合句被单域吞掉**（2026-08-20 新立）| **可以开工，但别再逐句补范例**——person-pickup 迷你集最后剩的 4 次红全在这一族（mcd/trip/闲聊各自吞掉整轮）。要换判定形态（多意图是不是被完整覆盖），同「裸对象澄清族」那条 | §4.2 该行、卡 §6.7、history §64.6 |
| **`memory_item` supersede 信息衰减**（同日新立）| **等第二个可复现实例**。实据只有一条链（`person.child` 四跳越记越少），而修法要先分清「事实被更精确地重述」与「偏好发生了变化」——**偏好类就该新的赢**，不能直接搬关系边那条规则 | §4.2 该行、卡 §6.8、history §64.7 |
| **③ 支付余项** | **等外部**：支付宝沙箱恢复后重跑探针；微信商户号到位后真实联调 | 下方 ③ |

⚠ **开工前的固定动作**：`python scripts/dev_stack.py target show` 确认档位（当前
`target=cloud`，**起本地 Compose 是红线**）；跑全量前按「跑全量的固定口径」清
`PYTHONIOENCODING`、把 `python` 加进 PATH。

**① 探索式真实用户 QA 轮 58 个问题：Q1–Q13 立卡，四阶段 + 编号序列 + 不编号那一步全部走完，剩两条残余**
（2026-08-15 立卡 → 2026-08-22 收尾，逐批流水见下。**只剩 I-024 门店侧一条**
（Q12 规格维 2026-08-21 收口、08-22 真栈复验 9/9 通过；**I-030 跨组 2026-08-22 收口**，
history §67），
各有独立成因、都不是「下一个动手」的默认对象——见下方逐卡表与 §4.2。报告
[`…exploratory-real-user-qa-deepseek-minimax.md`](docs/reviews/2026-08-15-exploratory-real-user-qa-deepseek-minimax.md)、
方案与全部读数
[`…qa-exploratory-root-cause-cards.md`](docs/design/2026-08-15-qa-exploratory-root-cause-cards.md)、
流水 history **§41–§53 + §58/§59/§61/§62/§63/§64/§65/§66**，本轮归档索引 **§47.5**。）

接手先读卡的 **§1**（**八处**与报告定性不同的重判——每一处都改变了「该修什么」；
**第 8 处推翻的是卡自己写的落点**，所以「卡上怎么写」也要拿真栈验）
与 **§4**（阶段计划），
**别按报告的 T0/T1/T2 直接开工**——报告按症状分档，会把同一个根因拆到三个档里。

| 阶段 | 状态 | 内容 |
|---|---|---|
| 0 取证与止血 | ✅ 完成 | I-021 定性推翻（QA 轮零 create-order）、迷你集 `scripts/probe_qa_regression.py`、方法论进 `test/README.md` §5.3.1、清洗 dry-run 10 条 |
| 1 T0 安全与不可逆 | ✅ 完成 | manual-rag 零命中短路 + 来源类型 / 安全分级不进 LLM / `Focus.safety_alert`（契约 `conventions.md` §9.1）/ road-safety `safety.driver_state` / chitchat 兜底闸 / 支付字段 fail-closed。真栈 SF1·2·4·5 **3/3**，SF3 0–1/3（失败轮全是澄清，危险症状零复现） |
| 2 T1 共享状态机 | ✅ 完成 | **Q1** 取消判据收敛 `pending_cancel.py` → 确认帧带 `operation_id` → 小容量挂起表（≤3，LRU 有话术）+ HMI 同屏多条确认条；**Q3** 帧带 `request_id`、对不上即丢帧、看门狗每轮一只、抢占点名回 cancelled；**Q4** 位置闸拆 ORIGIN/ANCHOR + 否定邻接。契约 `conventions.md` §9.19/§9.20。真栈：confirm 组 **18/18**、CDP C11-13 × 3 轮 **9/9** |
| **3 T2 语义与真实性** | ✅ **主体完成** | Q13 ✅ / **Q2 ✅（载体 + 确定性消费方 §58 + 下发面 §61）** / **Q7 ✅ 三维度全收口** / Q5 ✅ / **Q10 ✅ 四块（双入口收敛 §61）** / **Q6 ✅** / **数据清洗 `--apply` ✅** / **Q12 ✅ 全部**。**残余只剩 I-024 门店侧一条**（Q12 规格维 08-21 收口、
I-030 跨组 08-22 收口），见下表；⚠ 同批掀开的**记忆语义召回失效**已于当日插队修完（§59）|
| 4 T3 能力面与长尾 | ✅ **完成** | **Q11 ✅**（写入闸/否定守卫 §47 + **offer 准入 §63**）；**Q8 ✅ 四块**（方向盘 / 双闪 / 静音 / `navigation.estimate`+`origin`）；**P2 长尾 ✅ 五条**（I-022/I-033/I-057/I-017/I-031）。流水 history **§63**。⚠ 本批新记一条账：I-033 的**跨轮追问**未做（需会话级数据源账本，独立一卡）|

**逐卡终态**（**新会话不从这张表开始**——从上方的接手入口挑选表开始；这张表用来
回答「某张卡当时是怎么裁的、读数在哪」。每张卡的落点、判据、读数在
[卡 §4 的各「实施记录」段](docs/design/2026-08-15-qa-exploratory-root-cause-cards.md)，
流水在 history §42–§53，**这里只留状态与残余**）：

> ⚠ **这张表已经没有「下一个」了——整张只作查证入口。** 2026-08-22 起 QA 轮逐卡**全部 ✅**，原来那条「以下已收口」的分隔行因此被删掉：**一个只有一侧有内容的分隔符是噪声**。行序是历史接手顺序的残留、**不再有含义**，按卡号 Ctrl+F 找即可。
> **「下一个做什么」看上方的接手入口挑选表**（那里才是活的）。
> 这一行的主张改过三轮，每一轮都是被表自己的内容打脸后改的——留在这里当判据：**表的排序/分区本身就是它的主张，主张失效了就得改，不是补一句说明。**
> 洁癖整理**十次**，逐次判据（只进不出，排新卡时照着做）：第一次（08-16）把未完成的挪到上面；**第二次（08-16，Q12 收尾）按接手顺序
> 重排，并把 Q8 从第一行挪到未完成项的最后**——它是**叶子**（不解锁任何别的卡），
> 而 Q2 残余是三条卡的**共同前置**却原本排在它后面。**前置排在叶子后面是排序 bug。**
> 第三次（**2026-08-17，Q7 残余收口**）把 Q7 移入已完成区，**Q2 残余升到第一行**。
> 第四次（**2026-08-19，Q2 残余收口**）**表被自己的表头打脸**——写着「第一行就是
> 下一个该动的」，而前两行已经是收口的 Q2 与记忆召回卡。已收口的一律移到分隔行以下；
> 同批修掉 Q5 行「状态 ✅ 而残余列里有真待办」的自相矛盾。
> 第五次（**2026-08-19，Q10 残余收口**）同因同修：收口的 Q10 移到分隔行以下，
> Q8 升到第一行——它是接手顺序里唯一还编号的一步（第 8 步）。
> 同批复跑掀开一条**新卡**（候选在手时 chitchat 仍会编造），当时排在 Q8 前面：
> 那是**真实性**问题（系统编了一个不存在的价格），而 Q8 是能力缺席。
> **排序判据是「哪条错得更严重」，不是「哪条先被记下来」。**
> 第六次（**同日，第 7.5 步收口**）那条已移到分隔行以下，Q8 重新回到第一行。
> 第七次（**同日，第 8 步收口**）同因同修：Q8 与 Q11 移到分隔行以下。
> **编号那一列到此清空**——剩下两行（Q12 规格维 / Q5 实体归一）本来就**刻意不编号**：
> 前者的修法是 B6 远期字段、后者跟着 person-pickup 卡走，都不是「下一个动手」的对象。
> 第八次（**2026-08-20，person-pickup 卡收口**）：Q5 行随那张卡一起做完了
> ——它俩本来就是同一件事，**表上「跟着那张卡做」那句话是对的**——移到分隔行以下，
> 分隔行以上只剩 Q12 规格维一行（仍在等 B6 远期字段，不是「下一个动手」的对象）。
> 第九次（**2026-08-21，Q12 规格维收口**）：分隔行以上**清空了**——Q12 是它上面最后一行。
> 留痕一句：它在这张表上挂着「等 B6 §4 的 `input_schema` 远期字段」等了五天，而真机一扫，
> **那个字段要表达的东西早就以更差的形式存在了**（一张藏在实现里、从没和真机对过的猜表）。
> **判据：接手一条「等某个字段」的待办时，先问那个字段要表达的东西现在是不是已经存在了。**
> 第十次（**2026-08-22，Q12 真栈复验 9/9 通过**）：**分区本身被删掉了**。
> 上一次（第九次）我把上半区清空、却把分隔行留着，还在留痕里指向「上方挑选表的
> **Q12 真栈复验**」——而那一行随复验通过也已删除。⇒ **清空一个分区之后，
> 分区就该跟着消失**；留着它等于让读者去一个永远为空的地方找东西，
> 而指向它的那句话就是下一个坏链。
> 第十一次（**2026-08-22，I-030 跨组收口**）：Q2 行残余列清空，同时**修掉上一条留痕
> 自己变成的坏链**——它指向挑选表里的「I-030 跨组比较」，而那一行随收口已经删了。
> ⇒ **判据再兑现一次，而且是打在写它的人身上：指路的句子要跟着被指的东西一起改。**
> ⇒ **下一个动手的不在这张表里**：见上方**接手入口挑选表**（I-024 门店侧 /
> 多意图复合句 / `memory_item` 衰减 / 支付余项），与 §4.2 条件待办索引。

| 卡 | 状态 | 残余（**接手要做的就是这一列**） |
|---|---|---|
| **Q12 槽值保真** | ✅ 完成（2026-08-21 规格维收口）| 原话回查（时间维）+ 中文时间词收敛（history §52）；**规格维 2026-08-21 收口**（history §65）——卡上「等 B6 `input_schema` 远期字段」的定性被真机推翻四处：planner **没有丢词**（3/3 填对），三个规格槽是**值域声明本身猜错了**（`ice` 查的「冰量」组在瑞幸不存在，冰档位是「温度」组的取值）⇒ 结构性死亡两个多月。修法=`input_schema` 落 `servers.yaml`（唯一声明）+ 真机台账单向门禁 + 删 `_SPEC_GROUPS` + 补 `size` 槽。**2026-08-22 真栈复验通过**（`--group spec` **9/9 全 `[det]`**）——复验本身又掀开一条：**选品那一跳整条链是断的**（`_product_choices` 不留上下文，用户点了商品之后门店与规格全丢），正是 I-025② 原文「卡片点击才生成生椰拿铁」那句话。已一并修掉（history **§66**）。**本卡零残余。**|
| Q5 身份/作用域/记忆写入 | ✅ **全部收口**（出处披露 §51 + **实体归一 2026-08-20 §64**）| ① 出处披露 **✅ 0/3 → 3/3**（`agents/chitchat/src/mem_source.py` 确定性追加，history **§51**）。⚠ 汇总行是 `[var]` 不是 `[det]`，**这是预期的**——主张的是「出处确定性」不是「整句确定性」。② 实体归一 ✅ **2026-08-20 随 person-pickup 卡一并收口**（泓舟逐条裁定：孩子≡女儿·学校=深圳市南山实验小学 / **妈妈=苏州、岳母才是杭州** / 补城市 + 删济南轨迹 / 清 16 条陈旧会话情景族）。**代码侧还各修了一处**——`resolve_person_place` 把匿名占位边与具名边数成两个人（一跳解析对该称谓永久失效），以及 `memory_item` supersede 按新旧让 `person.child` 四跳越记越少。history **§64** |
| **Q8 能力缺席** | ✅ **四块全收口**（2026-08-19 第 8 步，history **§63**）| 补齐 `navigation.estimate`（只算不导）/ `navigation.cancel`（终止本次导航，I-017）/ `volume.mute`+`unmute` / `warning_light`（双闪独立对象）/ 方向盘加热与高度打通 / `navigate_to`+`estimate` 的 `origin` 槽。⚠ **取证改了四处定性**：方向盘是「两个产出方给出不同形状的 VAL 命令 + 门禁跳过校验那一段」不是缺入口；双闪是生成器 family 表把它并进 headlight，真栈三次分别落 `power_mode.set`/`lane_assistance.close`/**`hvac.off`**；静音落 `volume` 不是卡上的 `media`；`is_standalone_cancel` 的过捕获比卡上宽得多（**取消导航/订单/提醒/播放**整族被吞）。**架构 §5.2.7 / 契约 §9.29** |
| Q11 提醒准入 | ✅ **三块全收口** | 否定守卫 + `fire_at<=0` 写入闸（§47）+ **offer 准入**（§63：只在「用户说出了时刻 + 至少提前 30 分钟 + 剥完时间词还剩一件事」时 offer；判据唯一声明处 `memory/offer_admission.py`，契约 §9.30）|
| ~~候选在手时 chitchat 仍会编造~~ | ✅ **已收口**（同日，第 7.5 步，history **§62**）——`candidate_query` 增**第三种算子「序数取值」**，把这句话拦在 plan 构建之前。**MC1 2/3 → 3/3 `[det]`**（三次话术逐字相同「「柠檬脆脆麦旋风」16 元。」）。⚠ 通用判据：**凡是「系统持有的事实」，判据面就得是闭合的**——留一条缝，模型就会从缝里编一个，而且**带着确定的语气**。⚠ 收窄手段只有一个：**序数与维度问句必须紧邻**（「第二天第一个景点多少钱」放行）。原残余描述：| 真栈原话：菜单卡就在上一轮，「麦当劳的第七个多少钱」落到 chitchat，答**「第七个是脆汁鸡腿堡，10.90 元」**——第 7 项其实是柠檬脆脆麦旋风 16.00 元，**商品名和价格都是编的**。⚠ 它落在**既有两条守卫之间的缝**里：云侧 `candidate_query` 那条确定性短路只覆盖**聚合问句**（最值/合计），而「第 N 个多少钱」是**单项查询**；I-052 防编造守卫又只在**零候选**时触发。判据与两者同源（**系统持有的事实绝不让 LLM 答**），但**扩劫持面要谨慎**——§9.27 那三段判据取窄是刻意的（短路看到全部流量，误伤代价是整轮被吞）。⚠ 这条同时**含一个落域问题**（本该落 `mcd.menu`），但**先修真实性再修落域**：落错域只是答非所问，编价格是假事实 |
| Q1 确认/挂起/取消 | ✅ | — |
| **Q2 候选集升格** | ✅ **全部交付**（消费方 §58 第 6 步 + 下发面 §61 第 7 步，2026-08-19） | **CD1 red→3/3 `[det]`、CD4（I-023 首次有探针）red→3/3 `[det]`**，history **§58**。⚠ **卡上那两个缺口上游还有两个洞**，只做卡上写的会「机制通了、读数照红」：白名单 7 个字段名是**猜的**（`open_hours` vs 产生方的 `open_today`）⇒ 营业时间**从来没进过候选集**、且被测试自己的 fixture 盖住；商户菜单 items 只在 `ui_card` 而 `extract_focus` 只读 `data` ⇒ **菜单从来没进过候选集**。① 下发通道 **✅ 已随第 7 步落地**（`context.candidate_downlink` + `context_scopes: [candidates]`，契约 §9.28）。② **I-030 跨组 ✅ 2026-08-22 收口，本卡零残余**（history **§67**、契约 **§9.32**、架构 **v1.36** §5.2.10）——⚠ **取证又把卡上定性改了一档**：卡写「跨组比较做不了」（答非所问），实际是**跨组给出一个算错的确定性答案**（两家菜单并存时「麦当劳的第二个多少钱」零方差答瑞幸的第二个，**商品名与价格都真实存在、没有一处对不上**，比编造更难被发现）。修法=保留键 `_candidate_label` 由产生方声明组标签（判据同 `_fallback`）+ 组指代 + 跨组算子 + 下发面**逐步按域选组** |
| Q3 HMI 响应归属 | ✅ | — |
| Q4 位置前置闸 | ✅ | 后端 `need_location` 帧那条更彻底的方向仍是二期（卡 §3-Q4） |
| Q6 执行账本 | ✅ | 动作随会话轮次落库（`AppendTurn.actions`）+ chitchat 确定性审计出口，AU1 red→**3/3 `[det]`**。写入量量清楚了（38 天 763 个动作、有动作轮仅 24%）⇒ **不建表**，保留期由既有 TTL/ltrim 兜住。history **§50** |
| **Q7 端侧语义维度** | ✅ | 三个维度全部收口。EL1 **1/3 → 8/8 `[det]`**、OR2 **0/3 → 8/8 `[det]`**，确定性路径真栈命中 6/6、零 LLM。契约 `conventions.md` **§9.26**，history **§53** |
| **Q10 商户双入口 + 查单绑会话** | ✅ **四块全收口**（§49 三块 + §61 第四块） | 双入口收敛已交付：候选集经 `context_scopes: [candidates]` 下发，桥侧 `candidate_ref.py` 确定性把「第七个」「巨无霸」翻成商家原名。⚠ **真栈推翻了卡上两处说法**：按钮路径**并不带**结构化引用（`send_text` 就是一句中文）；文本路径也不是完全不通（焦点块早就渲染前 5 项，模型自己数就能对）——真缺口是**渲染只有 5 项而菜单 20 款**。**残余=门店侧 I-024**：门店选项卡是 `NEED_SLOT` 结果而 `extract_focus` 只从成功步抽候选 ⇒ 门店候选集根本不存在，放宽抽取影响面远超本卡，**独立一卡**（契约 §9.28「三条边界」）|
| Q13 两分类出口收敛 | ✅ | — |
| 数据清洗 `--apply` | ✅ | 四族终态 0/0/1 组保留/0；③ 改成逐组点名授权。**清洗只覆盖 `memory_relation` 与 `reminder_item`**；`memory_item` 里的事实级脏数据当时归 Q5 残余，已于 **2026-08-20 由泓舟逐条人裁清掉**（history §64.4）——**判据仍是「这条事实对不对」，脚本永远够不着，下次脏了还得人裁** |
| 记忆语义召回失效（云端）| ✅ **已收口**（2026-08-19 插队处理，history **§59**）| **根因与「记忆」无关**：云端 `AUTH_TOKENS` **漏写了 user_id 段**（`<token>:v1:<scope串>:<scope串>`），网关取 `parts[1]` 得到 `v1` 当 user_id，而全部记忆在 `u1` 名下。**第 4 段 scopes 恰好没错位 ⇒ 权限全通、功能全正常、端点全 healthy、`verify` 全绿，只有记忆一条都召不回。** ⚠ **两次定性都被取证推翻**：先判「召回失效」——在云端跑一次真实 recall，第 1 条就命中（sim 0.6589），否掉；再判「切云/迁移动了什么」——迁移没动它，是**切云打开 `AUTH_REQUIRED` 让一条一直错的配置开始生效**。本地当时同样错，但它只有 3 段、被网关**静默跳过**⇒回落匿名 `u1`，**缺陷被另一个更严重的缺陷掩盖了**。四段修复：配置（云端+本地）／网关畸形即 **fail closed**／`e2e_remote_safe` 加 `remote-memory-recall` 只读探针（切云验证面此前**没有记忆**，所以「验证通过」与「记忆全废」同时成立了三天）／清 `v1` 污染 4 条（含 3 条 **fire_at=08-20 的 pending 提醒**，会真的推送）。读数 XS3 **0/3→3/3**、QA 迷你集 **33/39→36/39** |

**接手顺序**（这个次序是有理由的，别按卡号做）。
**第 0–8 步已全部完成**（2026-08-16 → 08-19，逐步的落点/判据/读数在 history
**§48–§59 + §61/§62/§63**，本表不再复述）；**不编号的那一步（Q5 残余 + §4.1 ②
person-pickup 卡）也已于 2026-08-20 一并完成**（history **§64**）
⇒ **这一轮到此全部结束**，新会话不要在这张表里找「下一个」——见 §4.1 ① 剩余残余
（**只剩 I-024**；Q12 规格维 2026-08-21 收口 + 08-22 真栈复验通过 history **§65/§66**，
I-030 跨组 2026-08-22 收口 history **§67**）、§4.1 ③ 支付余项，
与 **§4.2 条件待办索引**。

> 已完成那几步留下的**排序判据**（下次排新卡时照它做，2026-08-19 洁癖整理时压缩保留）：
> · **排最前的未必是最简单的**——第 0 步数据清洗排第一，是因为它**改变后面所有卡的读数**
>   （兑现了：XS3 从「方差」变成稳定红、CD2 从 2/3 升到 3/3）。
> · **前置排在叶子后面是排序 bug**——Q8 是叶子（不解锁任何卡）却一度排在 Q2 残余前面，
>   而 Q2 残余是三条卡的共同前置。
> · **建立一个形态的那一步要排在复用它的那几步前面**——Q6 建了「确定性 handler 回答
>   系统持有的事实」，Q5 出处披露与 Q2 聚合消费方都是照它做的。
> · **通道要和它的第一个消费方同批落**——下发面本来排在第 6 步，因为那一批的消费方
>   在云侧、不需要它，所以推到第 7 步与真实消费方一起落（B4：无消费方的声明只会漂移）。
>   **兑现了**：第 7 步取证发现下发投影该留什么字段，只有站在消费方那一侧才判得出来
>   （`id` 因此**刻意不下发**——给了它两个入口会重新分叉，只是反了个方向）。
> · **一步的读数掀开的新问题，编号带小数插在原地，不排进下一轮**（第 7.5 步）。
>   它是那一步的直接产物，排到队尾就会跟「新一轮要做什么」混在一起。
> · **同一批里排序看「哪条错得更严重」，不看「哪条先被记下来」**——第 7.5 步
>   （系统编了一个不存在的价格，**真实性**）插在 Q8（能力缺席，答不出来）前面。


| # | 做什么 | **为什么排在这里** |
|---|---|---|
| ~~**8**~~ | ~~**Q8** 能力缺席 + **Q11** offer 准入 + P2 长尾~~ ✅ **2026-08-19 完成**（history **§63**）| 收尾——它是**叶子**，不解锁任何别的卡，所以放最后。⚠ 那条「先说明理由再调 `max_cases`」的要求已兑现：596 → **610**，逐项占用写在 `suites.yaml` 头部。⚠ 本步**掀开一条它自己的配套**：把「取消导航」从裸取消闸里放出来之后必须补 `navigation.cancel`——**放开一条路却不给它落点，是把一个错换成另一个错**（这条判据记进架构 §5.2.7）|
| ~~**不编号**~~ | ~~**Q5 残余**（`memory_item` 实体归一 + N3）+ **§4.1 ② person-pickup 卡**~~ ✅ **2026-08-20 一并完成**（history **§64**）| 兑现了「跟着那张卡做，别单独排队」——**数据裁定与代码修法互为前提**：只清数据，一跳解析仍被匿名占位边卡死；只改代码，planner 仍读到那条无城市的记忆。⇒ **下一个动手的**（2026-08-22 更新：I-030 已收口）：§4.1 ① 的 **I-024 门店侧**、§4.1 ③ 支付余项，或 §4.2（person-pickup 批新记两条：`memory_item` supersede 信息衰减 / 多意图被单域吞掉）|

**存量数据清洗的终态（以及仍未清的那部分——它是 Q5 残余的全部内容）**
（2026-08-16 泓舟逐族拍板执行，流水与判据 history **§48**；对应已折叠的第 0 步）：
① 自环 **0**（`rel <> 'family'` 修正后自然归零）/ ② 主宾颠倒 1→**0** /
③ **只清「女儿」组**（同一所学校差城市前缀），**「孩子」组保留**（两所不同学校、
等实体归一）/ ④ `fire_at<=0` 3→**0**。`superseded_by` 列**首次有值**。
③ 已从「整族一次清」改成 **`--conflict-subject <subject>` 逐组点名**，不点名即 exit 2
——**脚本 docstring 里写着「必须人裁」，那就该是一道闸不是一行注释**。

⚠ **仍然成立、且下一个人最容易误读的一条**：清洗**只覆盖 `memory_relation` 与
`reminder_item`**。`memory_item` 里的事实级脏数据（N3「导航去过济南市南山实验小学」
两条、`妈妈`/`用户的妈妈` 苏州 vs 杭州、`用户的女儿在南山实验小学上学`）
**一条都没清、也不该由脚本清**——判据是「这条事实对不对」，只能人裁。归 Q5 残余。

**仍然成立的约束（不是流水，别收走）**：
- **泓舟 2026-08-15 拍板两条**：① 存量清洗已授权，但**先修入口再 `--apply`**（§40 那次
  复位已证明只清不修会再脏）；② **Q1 走 A+B+C 三条**，HMI 确认条相应支持多条。
- **Q3（HMI 并发归属）与 Q4（位置前置闸）在客户端 JS 里**，WS 探针从闸后面进来、
  跑多少轮都是假绿 ⇒ **必须走 `test/hmi_cdp/` 车道**。同理 `user_id` 客户端设不了，
  跨 user 隔离要配 `AUTH_TOKENS` 或走签名 e2e 身份车道。
- **验收口径**（卡 §3.5 两条，直接决定后面怎么验）：话术层断言只能用**形态判据**
  （有无动作/是否问句/是否逐字重复上一轮）；**单次取样不能当基线**，一律 `--repeat >= 3`。

**② 复合句里的「接送某人」人称解析失效** ✅ **2026-08-20 收口**（含 Q5 残余的数据裁定，
两件事本来就是同一件；流水 history **§64**、实施记录见卡 §6、架构 **v1.34** §5.2.8）。
真栈 **10/24 → 41/45**，一#5 与一#1 均由稳定红转绿，**45 次取样里长距离误导航一次都没有**。
⚠ **卡上三处定性全被真栈取证推翻**（这是第 8 步那条「卡自己写的落点也要验」的第二次兑现，
而且这次是三处）：① 接错城**不是 provider 乱返回，是我们自己那条去偏置全国重搜捞上来的**；
② `resolve_person_place` 对「女儿」「孩子」**早已永久返回 None**（匿名占位边与具名边被数成
两个人）；③「planner 转述丢了城市」**不成立**，丢城市的是 `memory_item` 本身
——history §48.3 那个「究竟召回了哪一条边」的悬案到此闭合：**召回的不是边**。
⚠ 本批**新记两条账**（都进了下方 §4.2）：`memory_item` 的 supersede 按新旧会让同一件事
越记越少（与关系边侧 2026-08-16 定的「保留信息最全的那条」规则相反）；
多意图复合句被单域吞掉（剩下 4 次红全在这一族，与人称解析无关）。
立卡时的原始现象与机制（**§2/§3 保留原文不改**）：
「接爸妈去吃饭」两档都给不出教学问（一档「暂时无法确定」、一档错问家地址）；
「带我去接孩子放学…」MiniMax 导到**济南**同名小学 2004km。根因已定位到行——
`_person_destination` 的「去掉人称词后还剩不剩实质内容」判据对**槽值**是对的，
套到**整句原话**上天然失效（复合请求必然有剩余内容）；一#1 另有独立成因=planner
转述记忆地点时丢了「深圳市」。修法候选、落点与**接手取证清单**（先建红绿迷你集再动码）
全在 [`docs/design/2026-08-15-person-pickup-resolution-card.md`](docs/design/2026-08-15-person-pickup-resolution-card.md)。
⚠ **「究竟召回了哪一条边」这个悬案已由 2026-08-16 数据清洗批关掉，答案是「两条都不是」**
（history **§48.3**）：不带城市那条**边**已 supersede、现行边只剩
`女儿 --place_of--> 深圳市南山实验小学`，而探针 XS3 三次取样**仍有两次答「南山实验小学」**
⇒ **召回源根本不是 `memory_relation`**，是 `memory_item` 里的同源第二份存储
（`3b2713f4…` semantic「用户的女儿在南山实验小学上学」+ `b9a0e860…` episodic
「今天要五点前到南山实验小学接女儿」）。**动这张卡前要处理的是这两条 `memory_item`**；
那张卡写的「关系边里存的是全名、planner 转述丢了城市」仍未被证实，
但**证伪它的实验已经做完了——不用再从关系边找**。

**③ 支付余项**：沙箱「支付→PAID→refund」段——泓舟已扫码但沙箱钱包内支付失败
（支付宝沙箱当日服务级故障），恢复后重跑 `python -u scripts/alipay_sandbox_probe.py`
（自动弹浏览器大码）；微信商户号到位后真实联调（代码按 v3 真实实现 + 签名单测锁死，
未经真环境验收）。
**量产边界（仍成立，不是待办但改支付/商户面前必须读）**：两家商户 token/账号均为
**服务级全局凭证**，不是多乘员独立账号；商户 token 与 `PAYMENT_EXTERNAL_PAY_HOSTS`
必须由运行时安全配置提供，空配置 fail-closed；**系统不执行最终付款**。
契约 [`docs/conventions.md`](docs/conventions.md) **§9.17**（支付网关）+ **§9.9**（桥）。

外部评审六批 **B1/B2**（2026-08-10）、**B3/B4**、**B5/B6**
（2026-08-11）全部实施合入并收口。✅ 冻结令已撤销，可以新增业务 Agent。

各批交付了什么、与方案有哪些差异、验证证据如何——**只读方案文档 §6/§7 的实施记录**
（[B1](docs/design/2026-08-10-b1-execution-safety-stopline.md)、
[B2](docs/design/2026-08-10-b2-gate-ci-branch-governance.md)、
[B3](docs/design/2026-08-10-b3-deploy-profile-fail-closed.md)、
[B4](docs/design/2026-08-10-b4-capability-pack.md)、
[B5](docs/design/2026-08-10-b5-planner-retry-stream-refactor.md)、
[B6](docs/design/2026-08-10-b6-actionability-forward.md)），裁决总览见
[`docs/reviews/2026-08-10-external-review-adoption.md`](docs/reviews/2026-08-10-external-review-adoption.md)。
落到契约面的六条：确认闸下沉 VAL（[`docs/conventions.md`](docs/conventions.md) §9.15）、
部署形态闸（§6 profile 段）、端侧能力声明契约（§9.16）、CI 四条 blocking 门禁（§4.0）、
`retry_policies` 与 `actionability` 两列观测（§8.1）。

⚠ **B5/B6 的触发条件当时并未命中**（B5=加新重试规则/新流式路径前；B6=真实流量分母
或该族再成主要矛盾），是泓舟 2026-08-11 直接指示推进的。两份方案的头部都留了痕——
**别把它读成「条件曾经满足过」**，那会让下一次「条件启动」的分量被稀释。

⚠ **对抗语料唯一输入 619 / 上界 619**（`suites.yaml` 的 `max_cases`，权威值）——当前余量仍为 0。下次加 L0 语料必须先说明新增
能力或边界为何值得占额度，再有原则地调整 `suites.yaml` 的 `max_cases`；不得删旧尺子压数字，
也不得先加语料撞闸后再补理由。560→619 的十三次递进与逐项占用写在 `suites.yaml` 头部。
⚠ 第十二跳（2026-08-20，person-pickup 卡）**理由回到最早那一条**：新裁定一条跨域边界
（`navigation-nearby.pickup-plus-meal`），台账契约要求**双向各 2 例**，机械地 +4。
它同时留下一条**门禁覆盖面的账**：`skills/exemplars/boundaries.yaml` 加裁定时，
范例门禁 `test/eval_exemplars.py` 只查「近重复有没有被裁定」，
**不查「裁定有没有对照语料兑现」**——后者在 `scripts/check_intent_gate.py` 里。
本批就是这么绿着提交、被全量 pytest 翻出来的（`test_boundary_ledger_maps_stable_ids…`
那条计数断言当场按住）。**登记完台账一定要再跑一次意图门禁。**
⚠ 第十一跳（2026-08-19，卡 Q8）同时留下一条**豁免边界**：端侧原子车控进
`coverage_exemptions.yaml` 豁免的是 **2+2+1 覆盖矩阵**，**不是 ingress 路由**——
双闪与静音修之前的真实症状恰恰是「路由到了云」，而**单测只测名字、走哪条路只有
`ei.local.*` 语料看得见**（dashcam 那条老账）。

> 逐批流水在 [`docs/agents-history.md`](docs/agents-history.md) **§15–§27**，逐条证据在
> `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§26**。
> 本节按约定**只留状态、不留流水**——沉淀下来的判据在 §4.3，数字在 §4.0。

### 4.2 延后 / 条件待办索引（不进入当前主线）

> §4.1 只放正在行动的事项；本表保留尚未启动、等待外部条件或明确后置的入口。条件满足后先晋级 §4.1 并补完成判据；历史流水仍只查 `docs/agents-history.md`。

| 主题 | 当前状态 / 启动条件 | 权威入口 |
|---|---|---|
| **EVA 余项**（①–⑤ 已于 2026-08-15 立卡 E1–E5 全部处理，见 §4.0 段与 history §39；本行只剩 ⑥） | ⑥ **G10 订座/票务维持搁置**（本表内唯一仍未启动项）：诚实桩现状可接受，有合适 provider 时按 mcp-bridge 准入流程走，**不为对标造假订座**。⚠ E 批遗留的两处**已知边界**（不是待办，出现真实消费方再谈）：归城校正的判据是各城池质心，某城池被高德限流搜空时该城不参与判定（末轮真栈实例：潍坊）；方差面维持档案化，E4 探针首跑 15/15 未复现，**不加 hint、不动 gate 案例集** | [E1–E5 卡](docs/design/2026-08-15-eva-backlog-cards-e1-e5.md)、[验证报告](docs/reviews/2026-08-15-eva-instruction-set-e2e-verification.md) §5、[缺口分析](docs/design/2026-08-14-eva-round2-capability-gaps.md) §2 |
| **端侧车控能力台账余项**（B4 产出） | 门禁台账 `orchestrator/edge/knowledge/capability_exemptions.yaml` 共 **39 条**，四类：媒体别名 8 / 云侧域对象 11 / 座舱 UI 面 6 —— 这 25 条是「本来就不该有端侧 intent」，**不是欠账**；剩下 **14 条是欠账、只是本批不做**（`air_purifier`/`auto_hold`/`bluetooth`/`epb`/`equalizer`/`frunk`/`hotspot`/`key_tone`/`low_beam`/`navi_broadcast`/`surround_view`/`wifi`/`driving_mode`/`battery`——VAL 侧多有分支或话术，只是端侧没给 fast_intent 规则与意图名；云端计划仍可经 `action_to_structured` 走到）。这 14 条里有 **3 条待人裁**：① `frunk` 是 `require_confirm=true` 的危险对象却没有任何端侧 intent、与 `trunk` 不对称，**是刻意不给语音开还是漏了**；② `driving_mode` 与 `power_mode` 语义高度重叠，可能是同一件事的两个对象名（若重复应合并——别让 planner 面对两个分不开的工具）；③ `battery` 查询要不要补端侧意图。新增端侧意图时**从这 14 条里挑**并同步删台账条目。⚠ **2026-08-19 卡 Q8 没有从这 14 条里挑，是刻意的**：QA 轮实际抓到的四处缺席一条都不在这张表上——方向盘**已声明**（断在 VAL 校验）、双闪**对象根本不存在**（被生成器 family 表并进了 headlight）、静音是 `volume` 缺一个**操作**、估算在云侧。**台账列的是「有对象没意图」，而这四处是别的形态**；本批因此新增对象 `warning_light` 与操作 `volume.mute/unmute`，台账 39 条不变 | [B4 方案](docs/design/2026-08-10-b4-capability-pack.md) §6.5、台账文件本身 |
| **P3b operate 抽取与放量** | 原表里并列的两个具体缺口（除雾能力缺席、「穿衣指数→股指」）已于 2026-08-10 修完，详见 history §23.1/§23.2，本行只留放量条件。**放量门槛不变**：operate 抽取 + 真实错对象率 <0.3%。⚠ 压这个数的手段是 **R4.1b P1 执行侧对象化**（让 VAL object 数从当前 67 继续长），**不是调阈值**；且当前 PoC 没有真实流量，这个数只有观测面、还没有分母 | [M5 P3 收尾](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) §P3 收尾 |
| live 路由回归进 CI | hint 退役后的召回保护目前是 live 人工车道，不是 CI 阻断；有稳定凭证、预算与 provider 方差处置后再接 CI | [旅程体系](docs/design/2026-07-14-journey-e2e-test-system.md) §4.3、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| `route_hints` 继续退役 | 当前实数 **18**（2026-08-24 QA 复验净增 7：MiniMax 重复采样残余的 5 条窄业务结构 + edge 门锁开/关极性 2 条）；`mcp-bridge#0` 必须先过专项安全回归。旧三条单档候选不得按历史索引直接执行；本批只证明 MiniMax 主模型当前需要这些纠偏，退役仍须跨 provider 全覆盖取交集 | [M5 P2](docs/design/2026-07-28-intent-accuracy-data-flywheel.md)、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| M5 后续杠杆 | catalog 检索化当前是“有意不做”；16k 预算再次裁剪或保护集显著变瘦时重评。gold→范例现走 CLI；P4 仅在范例 ≥2k 且 N1 平台期 ≥2 周时启动 | [M5 P2/P4](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) |
| M-B / M-C / M-D 明确后置项 | 13 张验收主卡已清零；跨域删除 saga、完整隐私管理/迁移仪式、持久治理扩面与 MCP 生产化覆盖按 GDPR 完备性、量产迁移或新消费方触发 | [总体验收](docs/reviews/2026-07-26-acceptance-review-m0a-m4.md) §10.2/§11.2/§12.2、[OwnerKey 契约](docs/conventions.md) §9.13 |
| M2 / M3 产品化边界 | Ledger 自动续跑/任务中心，以及主动治理持久化、偏好学习、dashboard、远距 geocode 与真实商户均为显式未做；出现真实消费方或产品阶段后另立卡 | [M2 RFC](docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md) §9.6、[M3 RFC](docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md) §10.7 |
| M4 声纹 / 视觉余项 | 真麦校准、语音注册入口、模板漂移治理、视觉多轮与 `vp_*` 指标消费仍未收口；进入真机量产验收或 v2 前启动 | [M4 RFC](docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md) §11.5/§11.8、[声纹契约](docs/conventions.md) §9.11 |
| **可执行性判定 canary**（B6 shadow 的下一步） | **shadow 已落地并取证**（2026-08-11）：裸对象族召回 2/2、假阳性 4/574（0.70%），planner 对照 `nq.landmark.bare` 11/20。**canary 需泓舟单独拍板**（B6 §5 第 4 条，本文件只授权到 shadow）。在那之前该做的是 §2.3 明写的**对照实验**：拿 shadow 记下的分歧样本人工裁定，胜率显著才谈接管——「诊断出一个洞不等于这个洞就是病因」。⚠ 唯一漏判 `ex.colloquial.dark`「有点看不清路了」**不属于裸对象族**（与金标执行的「有点热」形态逐字同构，没有形态差异可用），它要的是「唯一默认动作是否存在」那个 catalog 特征，是 canary 前的独立一步 | [B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) §6.3/§6.7、回放 `test/eval_actionability.py` |
| 裸对象澄清族（**已知无解状态，不是待办**；⚠ **B6 shadow 已给出第四条路，见上一行**） | **三条路径已全部走完，该族目前无可用修法。** `nq.landmark.bare` 合并 11/20≈55% 的高方差边界句；`nq.landmark.explicit` 自己每条断言都过、被 relation `clarify_flip` 连累；同族第三条 `nq.city.bare`「上海」reviewed 未进池。路径 1「写 guide」实测**有害**（§18：4/10→1/10→退回 7/10，p≈0.02）；路径 3「换出预选池」执行并全量验证后由泓舟裁定回退（§19.5）；**路径 2「范例 schema 加 clarify 型」2026-08-10 已实现并合入**（schema+渲染+门禁+契约+11 条测试），但同批实测出它**治不了本族**：裸专名之间 IDF-Dice 全 0.000（检索是内容通道，而裸对象澄清是**形态判据**——这也解释了路径 1 为何有害），且澄清型范例天然与其「补全版」近重复（两条候选一条抢到明确请求 top-1、一条只靠 0.03 差距，全部撤回，**故仓库刻意没有 clarify.yaml**）。**下一次要动它得换判定形态（形态/句法特征），不是再加一层检索式知识** | 立卡整段写在语料 `test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml` 的 `nq.landmark.bare` 头上；[findings §18/§19/**§25**](docs/design/2026-08-02-intent-routing-adversarial-findings.md)；机制契约 [`skills/exemplars/README.md`](skills/exemplars/README.md) §clarify 型 |
| B3-2「广州塔」地标解析（高德侧） | **不进落域账**。2026-08-10 同坐标直连复测：geocode 对（兴趣点／广州塔真坐标）、`near=None` 重搜 top1 就是广州塔，只有带深圳偏置的关键词搜索会顶出「广州仄仄科技有限公司」2.4km。即 R1 去偏置重搜机制本身是对的，但它**要多打一次高德**，跑批并发下正是最易被限流的那一次；掉一次退回就近弱匹配，掉两次成「暂时无法确定」——两次红的两种形态由此解释。归高德 QPS 一族，出现真实用户投诉或做并发治理时再启动 | [复杂意图·地标/停车](docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md)、判据与复测记在 `test/journeys/target_b.yaml` B3-2 注释 |
| `e2e_verify` 案例①：**前提变了不是修坏了** | 2026-08-11 全新重建的干净栈上 5 条红，**逐条定性为测试前提失效**，不是回归。该用例的前提写在它自己的注释里——「单句『打开空调』被端侧快路径直接执行，**必须用混合多意图句**才能规划出云侧 hvac 步」。2026-08-11 实测：混合句「帮我把空调打开，再查一下附近有什么好吃的」的 `route.mixed` span 记着 `local_actions:1`，**端侧把 hvac 那半自己执行了**（`val.execute`），云侧只剩 `nearby.search`——于是 `state_match` 那两条断言恒不可能满足。另两条 `schema` 断言失败是**测试查的 trace 与实际落的 trace 不是同一个**（按文本直查该 trace，`step.verify{mode:schema,verdict:sat}` 明明在），第 5 条是前四条的连带。**对账链本身是好的**（案例②/③ 全绿）。修它要先决定断言该指向什么（换一句仍能规划出云侧车控步的话术 / 或改测端侧 VAL 面），属独立一卡；B5/B6 结构上不可能造成它——那个决定发生在云端被调用之前 | 证据：本条 + `test/e2e_verify.py` 案例①注释、history §27.6 |
| **云端 shell 故障注入用例只在 Windows 跑**（2026-08-17 CI 收口顺带发现；2026-08-22 修掉了其中一半） | `scripts/tests/test_cloud_deploy_assets.py` 里 **41 条** 经 `_git_bash()` 起 shell 的用例，只找 `bash.exe`（Windows 路径），**Linux 上一律 `pytest.skip` ⇒ 它们在 CI 里从来没跑过**，只在泓舟这台机器上跑——守 `remote-data-migration.sh` 失败路径的那批断言，CI 是不设防的。✅ **2026-08-22 修掉的那一半**：候选原本固定取 `Path(which("git")).parents[1]`，于是**同一台 Windows 机器换个 shell 就整族 skip**（PowerShell 下 PATH 暴露 `<GitRoot>/cmd/git.exe` 算得对，Git Bash 下暴露 `<GitRoot>/mingw64/bin/git.exe` 算错）——这正是全量 skip 数在 32/73 之间跳、被记错两次的那个差额（history **§65.6**）。改成逐级向上找 `bin/bash.exe` 后两个 shell 一致。**剩下的那一半（Linux/CI 仍不跑）没动。**⚠ 修它要先决定用哪个 shell：Linux runner 上 `/bin/bash` 就在，但 `_run_cloud_bash` 现在的 argv/引号形态是按 Git Bash 调的（同 `run_go_tests.ps1` 那次的判据：**要问「怎么传参」而不是「什么系统」**），**换 shell 要重新验参数传递**，不是把 `bash.exe` 改成 `bash` 就完 | 本行 + `scripts/tests/test_cloud_deploy_assets.py::_git_bash`、history **§54.7** |
| **会话级数据源/降级账本**（I-033 的跨轮追问，2026-08-19 第 8 步新记） | **启动条件：出现第二个消费方**。本批已交付「本轮披露」那一半——体育结构化源回落通用检索时话术点名 vendor、卡片 `_prov` 打 `degraded`（契约 §9.3）。**没做的是跨轮**：用户下一句问「哪个数据源失败了」时，系统手上仍然没有可查的事实，只能让 LLM 猜（真栈实测它把方向说反成「联网检索不可用」）。修法形态与 Q6 执行账本同族——**「这一轮用了谁、降级没降级」得像动作一样入账**，而不是只活在这一轮的卡片上。⚠ 别在披露那一层继续加话术：**话术层判据验证不了「说的是不是真的」**（Q6 那批四版尺子前三版都假绿，同一条判据）。 | 本行 + history **§63.4/§63.7** + `agents/info/src/handlers/sports.py::_mark_sports_degraded` 的 docstring（覆盖面写在那里）|
| **`memory_item` 的 supersede 让同一件事越记越少**（2026-08-20 person-pickup 批新记）| **启动条件：出现第二个可复现实例，或 typed Executor / 记忆质量成主要矛盾**。实据是一条 `person.child` 链四跳：「用户的女儿在**深圳市**南山实验小学上学」→「用户有孩子，孩子在上学」→「用户的女儿叫小雨」→「用户有一个女儿」（live）——**每一跳都用更少的信息取代更多的信息**，而正是它把无城市的槽值喂给了 planner。关系边侧 2026-08-16 定的是**相反**的规则（保留信息最全的那条，不是最新的，E5 那次 dry-run 教的），`memory_item` 侧**没有对应实现**：`consolidate` 只按谓词等价类比新旧。⚠ **不能直接把关系边那条规则搬过来**——偏好类记忆（「以后不吃辣了」）**就该新的赢**。判据得先分清「事实被更精确地重述」与「偏好发生了变化」，这才是这一卡的真正内容 | 卡 [`…person-pickup-resolution-card.md`](docs/design/2026-08-15-person-pickup-resolution-card.md) §6.8、history **§64.7**、`memory/store.py::consolidate` |
| **多意图复合句被单域吞掉**（同批新记）| **启动条件：出现稳定可复现的族，而不是逐句补范例**。person-pickup 迷你集 45 次取样最后剩的 4 次红全在这一族、**与人称解析无关**：mcd ×2（「想点哪一款麦当劳餐品？」——接人那半没了）、trip ×1（「已结合天气为您规划学校1天行程」，动作 `trip.plan`）、闲聊式应答 ×1（不发动作也不给教学问）。⚠ **本批刻意不继续加范例**：已经加了两条接送句形，再按句形逐条补就是 per-utterance 调参了。要动它得换判定形态（多意图是不是被完整覆盖），不是再加一层检索式知识——同「裸对象澄清族」那条 | 卡 §6.7、history **§64.6** |
| B6 §4 Capability Contract 远期字段 | ~~`input_schema`~~ **✅ 2026-08-21 落地**（消费方=商户规格值域，契约 §9.31、流水 history §65；⚠ 触发命中了但**形态与方案预想的不一样**——不是 planner 填错类型，是我们声明的值域本身是猜的）。其余**逐字段独立触发，无当前行动**：`output_schema`（槽位类型错误成稳定 badcase 族或 typed Executor 立项）、`compensation`（撤销/补偿类产品需求）、`version`/`deprecation`（第三方 Agent 生态启动）。`replayable`/`idempotency` **已由 B5 §4.2 就地收口**——不新造 `command_id`，复用 `_fingerprint` | [B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) §4 |
| **瑞幸其余规格组还没有槽**（2026-08-21 新记）| **启动条件：出现 planner 真的在产的说法**。真机台账里另有 8 组：咖啡豆 / 咖啡浓度 / 奶油 / 吸管 / 气泡 / 茶风味 / 小料 / 咖啡液（`加单份浓缩` `加奶油` 这类用户很可能会说）。本批**刻意不补**——加槽要有 planner 产出证据（B4「不加即死字段」），现在补就是凭想象扩契约。补法是**改 `servers.yaml` 一处**：槽名 + `input_schema` 条目，下单链与预览卡 chip 两处自动跟上；组名与项名台账里都有，门禁会当场校。⚠ 线索来自 `runtime/slot_fidelity.undeclared_slots` 的日志（「planner 往契约外塞了东西」），**那是补能力的信号，不是要修的错** | 契约 [§9.31](docs/conventions.md)、台账 `agents/mcp_bridge/knowledge/merchant_specs_observed.yaml` |

### 4.3 读数纪律

- **话术层的断言只能用形态判据，不能用关键词排除**（2026-08-15 QA 批，**同一天自伤三次**）。
  按报告原文写「不许说 X」，模型换个说法就绕过去——同一条用例三次取样三种措辞
  （「其余行程不变」→「其他保持不变」→「其余保留」），一条排除词都没触发，判了 PASS。
  可靠的判据是**形态**：有没有动作 / 是不是问句 / **是否逐字重复上一轮**（探针为此加了
  `differs_from_turn`——「没回答问题」唯一可靠的机械判据）/ 动作名。
  ⚠ 反向也成立：SF3 复验时词表写窄，把「建议马上**停到安全位置**检查」这种**正确回答**
  判成了红——**尺子写错必须改，这和「不为某个模型的问题改案例集」不冲突**：
  那条针对被测对象做不到，这里是被测对象做对了、尺子认不出。
- **单次取样不能当基线，一律 `--repeat >= 3`**（同批）。SF4 单次 PASS、三次实测 **0/3**
  ——差一点把一条稳定红降级成方差面。报读数用「n/N + 方差标记」，不报单次结论。
  ⚠ **「安全类答案有方差」本身就是缺陷的论据，不是它的减分项**：答案对不对取决于
  这次模型怎么想，正是要消灭的形态。
- **把「不再危险」和「回答完美」分开报**（同批）。阶段 1 后 SF3 仍 0–1/3，但失败轮
  **全是澄清**，`volume.dec`/`wiper.speed.inc`/答天气一次都没复现。合并成一个百分比，
  就再也看不出这一批到底解决了什么。
- **「查到了一个真实副作用」不等于「这次操作产生了它」**（同批，I-021 定性推翻）。
  查单/查提醒/查记忆一旦不绑会话，就会把**历史**副作用搬进当前上下文，与「刚刚发生」
  无法区分——那一轮 58 个问题里至少 5 个是这同一个形态。
  **定性「系统做了 X」之前，先拿时间戳与 session 把它钉到某一轮上。**
- **改实现不等于加能力——planner 只看得见 manifest**（同批）。road-safety 里的确定性
  疲劳判据先落了地，但没有 manifest 声明时**根本路由不过来**，「困到睁不开眼」三次取样
  分别落到闲聊、拒识和音量。补上 `safety.driver_state` 后同一条用例 0/3→3/3。
- **认不出就返回空，绝不回落到某一档**（同批，**我自己引入的缺陷**）。
  `driver_state(text) or "fatigue"` 让「慢一点开可以吗」被答成「您现在的状态不适合继续开
  ——**困倦时**的反应时间和酒后接近」。用户从头到尾没说自己困——**系统声称了一件用户
  根本没说的事**，与 nearby 那几例假个性化同族。
- **判据的误杀面，要在接上「兜底」那一路之后才看得清**（同批）。安全判据原本用 `"灯亮"`
  这类通配，在 manual-rag 里没事；一接上 chitchat（**它看到的是全部流量**），
  「大灯亮了」「氛围灯亮着好看」就会被答成「请降低车速、就近检查处理」。
  修法=逐个列举具名警示灯，车上大多数灯（大灯/雾灯/日行灯/氛围灯/阅读灯/转向灯）
  一个都不在表里。**宁可漏一个告警，也不要对着一盏正常的灯劝人停车**——
  该判据的测试里**不该命中的用例占一半**。
- **「同一个 subject 有多个 object」不是冲突，除非那个谓词本身是单值的**（同批，
  清洗 dry-run 当场劝退）。首版把 `爸妈--family-->爸爸` 与 `爸妈--family-->妈妈` 判成冲突，
  **准备把「妈妈」标成失效——直接丢掉一个真实的人**。`family`/`owns`/`prefers_brand`
  天然一对多。这是 E5「dry-run 会自己劝退你」的第二例。
- **验证多轮系统必须跑「失败态之后再说一句」和 ≥3 轮**（2026-08-13 两次自伤的共同成因）。
  happy path 与干净会话证明不了会话状态是对的：挂起黑洞只在**拒绝之后**才出现，
  焦点覆盖只在**第三轮**才暴露（第二轮恰好紧邻搜索轮），CDP 用例也只验到「卡片渲染出来」
  而 bug 活在卡片之后。同族推论：**测试若替被测系统提供了某个前提，那条前提就不再被验证**
  （`e2e_merchant_mcp.py` 自己塞 `granted_scopes`，于是「scope 从没被发放过」绿了两个月）。
- **容器是 UTC、宿主是 UTC+8 ⇒ 时区类缺陷在本机永远不红**（2026-08-15 双档复跑）。
  真栈原文「预计 **05:17** 到达，比您要求的 17:00 早约 **703 分钟**」，两个 provider 逐字
  同构。这条规则本仓**早就写过三遍**（`scene/triggers.py`、`scene/agent.py`、
  `memory/server.py`，最后一处还写着「宿主机 UTC+8 跑单测永远不红」），G1 时间约束族
  仍然复发——**同一件事有三份各自正确的实现，就迟早会有第四份是错的**
  （B4 那条「不加第二份表达同一件事的**声明**」的孪生形态，这次栽在**实现**上）。
  现在业务时区的唯一定义在 `runtime/clock.py`，两条源码级守卫钉着
  （禁裸 `time.localtime/mktime`/`datetime.now()`、禁第二份 `timezone(timedelta(hours=8))`）。
  **写「几点」相关代码前先想一句：这个墙钟是业务语义还是进程语义。**
  - ⚠ **这条判据有个反向误用，2026-08-16 亲自踩到**：顺手把
    `mcdonalds._shanghai_timezone()` 也「收敛」成 `BUSINESS_TZ`，被一条既有断言
    当场按住——`BUSINESS_TZ` 是**车机业务墙钟**，那一份是**麦当劳中国的营业时区**，
    PoC 里同值 UTC+8 但**语义不同**（多时区时前者跟车走、后者不跟）。
    **「看起来是第二份定义」和「真的是同一件事」是两回事：判同不同要问语义，
    不是比数值。** 同批另有一处**确实是同一件事**（订单引用解析四处同族，
    history §49.3）——两个例子并列着看，这条判据才是完整的。
- **恒绿的断言比没有断言更糟**（2026-08-15，同批两次自伤）。上面那条守卫首版**没剥注释**，
  误伤了记录这条规则的注释本身；改成 tokenize 后用**空格拼接** token，
  `time.localtime(` 变成 `time . localtime (`，**注入缺陷时纹丝不动**。
  §4.3 既有的「反向验证要两头做」在这里救了场——只做「对照仍绿」那一半，
  一个永远不会红的守卫会让人以为这块被守着。**扫描类断言必须先注入一次缺陷看它红。**
  - ⚠ **注入验红只证明了一半，2026-08-16 补上另一半**：那条禁第二份业务时区的守卫
    正则尾部写死 `\)\s*\)`，要求 `timedelta(hours=8)` 后**紧跟**收尾括号，于是
    `timezone(timedelta(hours=8), "Asia/Shanghai")` **多一个参数就绕过去了**——
    一份完整的第二定义在它眼皮底下活着，而注入验红（用的是标准写法）照样通过。
    **写完扫描类断言要问的不只是「它会不会红」，还有「它够不够得着现实里那些写法」**
    ——去被扫的文件里真找一遍，别只拿自己脑子里那个标准形态验。
  - ⚠ **第三种绕过方式：词出现在文件里 ≠ 那里有第二份声明**（2026-08-16，Q12）。
    段位词的「唯一声明」扫描（同文件里出现 ≥3 个不同段位词即第二张表）套到**日词**上
    当场误伤——`timeparse._display()` 把「今天/明天/后天」当**话术**输出，
    presence 扫描分不开「一张表」和「一段话」。改用身份断言（消费方必须持有共享表
    **本身**）+ 对具名文件的断言，扫描那条留着兜「新模块自带一份」——
    **两条各管一半，且都要注入验红**。
  - ⚠ **守卫也会被「入口条件」绕过，不只被正则绕过**（同批，真栈第 3 次取样才抓到）：
    查单/取消的会话范围守卫挂在「槽位为空才走账本回填」这条路径上，而 planner 把
    `order_id` 填成了字面量「刚才那笔订单」⇒ **槽位非空 ⇒ 守卫整个不被调用**。
    单测三轮全绿。**问一句「什么情况下我这道闸根本不会被执行到」，
    和问「它判得对不对」一样重要。**
- **「挂点被调用了」和「挂点拿到了它要的东西」是两件事**（2026-08-16，Q12；
  「新增挂点必须枚举全部执行路径」的第三次应验，**形态是新的**）。前两次是漏了一条路
  （M2 Verifier 漏 D0、门店锚定漏 D0），这次 `loop.py` **调了函数却漏传 `ctx`**——
  于是跨轮门店锚定、城市补全、槽值保真在 T2 单步流式那条路上全部静默不生效。
  **症状与漏一条路完全相同：无日志、无报错、只是不发生。**
  修法同款：把覆盖面变成断言（每个调用点必须几个参数），不是「下次记得」。
- **防御要防到「维度」一级，不是「整值」一级**（2026-08-16，Q12，CLAUDE.md §6 那条
  「防到真正会被拿去 hash / split 的那个值」的时间版）。槽值保真的首版要求
  **整个值是裸时刻**才回填，而 planner 真实产出的是 `明天三点半`——**日留下了、
  段位丢了**，于是原样放行、落成次日 03:30，**把要修的症状在修完之后又复现了一次**。
  同族提问法：**我防的是不是「这个东西看起来完整」，而不是「我要的那一维在不在」。**
- **探针的隔离标记必须是模型不愿意扔掉的东西**（2026-08-16，Q12）。给用例加纯数字
  后缀做隔离，planner 转述标题时把它当噪声抹掉，于是第 2 次取样又撞上第 1 次留下的
  持久数据（**那次改期是正确行为**），3/3 被读成 1/3。换成「代号 xxxxxx 的评审会」
  后 7/7 保住。⚠ 这与 E4「探针不许替被测系统抽掉前提」不冲突——
  **这里取消的是探针自己造出来的前提**。
- **记忆是背景，用户当轮说的是前景；冲突时前景赢，并且要说出来**（2026-08-15）。
  「不要太辣」两档都推了川菜——记忆说爱吃川菜、当轮说不要辣，系统选了记忆。
  这是**假个性化的第三种形态**（前两种：声称参考却没参考、把别人的偏好套在你身上）。
- **约束词不是检索词**（2026-08-15）：planner 会把「不辣」「适合带老人」「晚饭」填进
  `keyword`/`cuisine`，直接拿去搜的后果是**要什么给了反面**（「不辣」搜出一串「辣可可」、
  「适合带老人」搜出家政公司）。判据取**正面白名单**，认不出退回干净类目词——
  宁可少一个检索维度。⚠ 同批还实证了另一半：**改完必须复跑同一条真栈探针**——
  首版只补了 `keyword` 分支，`cuisine` 是更早的早退分支、守卫够不着，复验一字不差复现。
- **「两档是否同时错」可以跨 provider 比，通过率不能**（2026-08-15）。同一份语料在
  主模型与对比模型上各跑一遍：两档相反=模型方差（档案化，不加 hint），两档逐字同错=
  系统缺陷（立刻能修）。这是**分离系统缺口与模型方差最便宜的工具**，
  与「语义层指标不可跨档直比」不矛盾——比的是**是否同时错**，不是分数高低。
- **修好一个洞才看得见下一个洞**（2026-08-15，E3）。六城行程的归城校正连跑四轮，每轮只
  暴露一个上游障碍：点名 POI 混进城市序 → 6 城只排 3 天（四座城一天都没有）→ `solve` 顺延
  把停靠点塞进下一座城那天 → 某城池被限流搜空、没有质心。**上一个不修，后面三个根本不会
  显形**。与「诊断出一个洞不等于这个洞就是病因」互补：那条讲**别急着认因**，这条讲
  **别指望一次看全**——所以「改完复跑同一条真栈探针」不是形式，是唯一能翻出下一层的办法。
- **探针替被测系统抽掉一个前提，和替它提供一个前提一样糟**（2026-08-15，E4）。方差探针
  首跑漏传位置 meta，「附近的咖啡店」5/5 全是问句，读起来像「过度澄清 100%」——实际是
  nearby 的**位置缺席诚实降级**（「我现在拿不到车辆的位置」）。补上位置后 5/5 直接出列表。
  §4.3 那条「测试若替被测系统提供了某个前提」有对称的另一半：**抽掉前提同样让读数失真**，
  而且方向恰好是「看起来更糟」，最容易被当成真缺陷去改生产。
- **「话里提到了人」不等于「这条记忆是关于那个人的」**（2026-08-15，E2）。按家人重排的
  首版只判「提没提到人」，于是「和老婆吃饭」命中了「父母腿脚不便」并据此排序——系统声称
  考虑了一件根本不适用的事，是缺口分析 §5① 那条假个性化的**另一种形态**。消费前必须按
  `subject` 或称谓同义词把记忆与话里那个人对上；泛指（老人/长辈）才放宽到祖辈族。
- **查「存了没用上」要把三个过滤器逐个排：scope / 谓词 / subject**（2026-08-15，E5）。
  条目在库里、scope 也对，仍可能永远召不回——因为消费方的 `scopes` 与 `predicate_prefix`
  在 `pg_store._score` 里是 **AND**，而这两个字段**都由 LLM 写、都会漂**。真栈实例：
  `place.avoid`/`poi.dislike`/`restaurant.no_queue` 三条 scope 全是 `profile.taste`，
  只因谓词没有 `taste.` 前缀就集体隐身；P5 的降权能兑现纯属另有一条 `taste.coffee` 也带了
  店名。**定式**：消费侧按 scope∪谓词两路并集召回（同 `place_of ∪ works_at ∪ lives_at`），
  写入侧靠归一表 + prompt 收敛源头。
- **拿不到结果时先分清「资源没了」和「这次没成」。** 2026-08-13 把高德的间歇性不可达
  误读成「配额被探针打光」，并据此在提交信息里写了「没跑通真栈整轮」；实测本机到
  `restapi.amap.com` IPv4 超时、IPv6 无路由而百度 90ms 正常——是环境不是配额也不是代码。
- `110/116`、`113/117`、raw `6/117`、旧 seen/unseen 对比均是历史口径/批次，**不得当当前结果**。
- `domain_hit_rate` 只要求命中交集；`exact_plan_set_rate` 要求必要组齐全、禁选为空、无额外项，二者不可直比。
- L1/L2 的正式 gate 必须是两个独立进程、每进程 3 样本；同进程 repeat 3 不能替代第二进程。
- baseline 的 `repeat_coverage=121/122` 是 L1/L2 121 个 live 单元重复、L3 按设计只跑一次；`process_policy_complete=true`，不是缺 shard。
- `fallback_plan_rate` 非零不自动失败；只有语料显式声明的 A8 能力缺席族可接受，未声明 fallback 一条即挡 baseline。
- Live 跑批前按运行手册 §2 同时设置 provider、model 与 embedding 三项；少一项整批证据作废。
- `pytest test/` 不是项目基线命令；换选集会改变 import 顺序与分母，不能拿来替代根跑。
  （2026-08-10 起它**不再有红**，但「不是基线」的理由是分母不同，与红不红无关。）
- **live 跑批期间不许动工作树**：资格闸逐进程校 `worktree_clean`，corroboration 进程比
  primary 晚起，跑批中途改文件会让它单独 fail-closed（2026-08-04 那批踩了两次）。评测产物写
  `docs/reviews/eval/_ci-run-*` 是安全的——那个前缀已 gitignore。
- 两次 141/147 不代表同一批红灯；读 MiniMax 报告先看**是哪几条**，再看总分。
- **不要再为「让 MiniMax 主模型出正式 baseline」发起跑批**（泓舟 2026-08-10 已裁定不追求）。
  DeepSeek 在 `f0af9c0` 的证据已满足 baseline 的用途；**资格闸拒绝带病读数正是它的设计
  目的——它在正确工作，不该为了拿绿灯去松它**。实测单元不稳定率 3~5% ⇒ 一趟零 unstable
  概率个位数百分比，继续跑批期望值极低；更不要以此为由动 gate 案例集或资格判据。
- **主模型是 `minimax:MiniMax-M3`，这是拍过板的**（泓舟 2026-08-10）：换档会牵动全部
  既有 baseline 与读数，且「通过率跨 provider 不可直比」意味着 DeepSeek 的 147/147
  **不构成「它更准」的证据**。DeepSeek 只作对比/参考轨。
- **加了知识就要拿对照跑证伪，不能只看它有没有被注入。** 2026-08-10 实测：一条 guide
  四次全部成功注入（`@lex:11` 未被裁），通过率却 4/10 → 1/10，退回后回到 7/10。
  「知识在场」和「知识有用」是两回事（findings §18.2）。
- **relation 断言会把 base 的方差放大成 variant 的稳定红**：`nq.landmark.explicit`
  自己每条断言都过却 0/10，只因 `clarify_flip` 要求 base 稳定。读 `stable_fail` 条数时
  先减掉这种连累项（§18.4）。
- **不要为了某个模型的问题去改动 gate 案例集**（泓舟 2026-08-10）：案例集是**尺子**，
  描述「我们要求系统做到什么」；某个 provider 当下做不到是被测对象的读数，不是尺子该
  让步的理由。与规格 §22.7「换出带病候选」不矛盾——那条针对**被测对象错了**，
  而「用例难、模型弱」正是 §22.7 要求留在门禁里的一类（§19.5）。
- **动 gate 案例集之前先问正式 baseline 还认不认得这个案例集**：案例集一变，
  每份报告都会带 `removed_cases` 拒绝原因（与模型无关），baseline 写入机制被冻结。
  离线比对 baseline 与语料的 id 集合即可零成本预判（§19.3/§19.5）。
- **「没到 6/6」是统计事实，不是定性**（2026-08-10）：三条候选在晋级闸前长得一模一样，
  逐条拉证据后是三种东西——一条要修生产（`nq.hvac.reported`）、一条要维持尺子的严格
  （`cp.hvac-news.swapped`，`clause_commute` 抓到 `limit="今天的"`）、一条什么都不用做
  （`nq.match.lastweek` 边界方差）。分歧可以出现在**同一个失败标签内部**（findings §20.1）。
- **claim 不能就近转借**：`dependency_continuity`（两个已规划的步骤之间结果被消费）
  证不了 `adaptive_replan_continuity`（第二步首轮压根不存在、看到结果才补出来）——
  首轮排满两步的计划照样满足前者，却正是 weather-outing badcase 要禁的形态（§20.4）。
- **跑 pytest 不要带 `PYTHONIOENCODING=utf-8`**（Windows 上会把拉子进程的用例弄成假红）
  ——动作型条目，展开与自查位置在运行手册 §11（findings §20.6）。
- **读任何落域数之前先看 `plan_modes`**（2026-08-10）：掉出 `toolcall` 的那些轮，模型是在
  自由文本里作答，输出分布与走 schema 的轮**本就不同**（MiniMax 内部实测 91% vs 50%，
  p≈0.036）。摘要行会打 `[!] N/M 轮没走成 toolcall`。⚠ `<通道>_no_action` 的后缀说的是
  **判断**不是掉档，`toolcall_no_action` 算走成了（§24.4）；而 `_kept` 后缀说的是**通道**
  （重试也没走成，用了抢救那份），**算掉档**。新增 plan_mode 值时先问它描述的是哪个。
- **门禁在两种模式下严厉程度不同**（2026-08-10）：普通 `--list` 只**展示** coverage gap，
  `--strict` 才把它升级成**阻断**。报绿之前先确认自己跑的是哪种模式。
  配套的老账：**管道会吞退出码**——`cmd | tail; echo $?` 拿到的是 `tail` 的，
  要读退出码就别接管道（本项目记过 `${PIPESTATUS[0]}`，2026-08-10 又踩一次，
  结果是把一个 exit 2 报成了 exit 0，见 findings §26.4）。
- **加 active intent 就要补对抗覆盖**（每 intent 正例 2 / 硬负例 2 / 对照 1）。
  想登记 `coverage_exemptions.yaml` 之前先过判据：那张表豁免的是「**不经云侧 LLM 落域、
  没有可测对象**」的端侧原子车控——**豁免的判据是「没得测」，不是「懒得写」**。
  新 case 标 `reviewed` 即可补上覆盖矩阵（`_AUTHORITATIVE={reviewed,stable}`）
  而**不进 gate 池**，于是不动案例集、不碰 baseline 比对面（§26.4）。
- **协议层指标要跨 provider 量，语义层指标不要**（2026-08-10）：同一份 `plan_modes`
  换档就从 45% 跳到 100%，这个对比有意义；同一份通过率换档就不可直比。
  **一条指标该不该跨档比，取决于它测的是被测系统还是被测模型**（§24.3）。
- **一个差异「显著」不等于它「贵」**：工具通道差异 p≈0.0002，代价却在 20 条 stable
  跨域样本上完全看不见（两档都 20/20）——**先问它在什么条件下兑现成损失**（§24.3）。
- **A/B 之前先证明两臂真的不同**（2026-08-10）：给 `replan()` 加形参后 harness 没跟着传，
  24 个样本两臂逐字相同，差点据此否掉自己的守卫；方向还正好是「看起来变差了」。
  同 §13.1「执行器有这个形参≠尺子也在传它」——动作型条目在运行手册 §11（findings §22.4）。
- **跨时段的读数不要合并平均**（2026-08-10）：`cp.adaptive.weather-outing` 上午 11 样本
  0 次「首轮判 simple」，下午 36 样本 10 次（≈28%）——**上午的 9/11 与下午的 25/36
  合起来那个数谁也代表不了**。⚠ 当时记成「工作树无改动可解释」，**那不是结论、是当时
  看不见足够的东西**：真因随后定位到**掉出工具通道的比例**（§23/§24），
  批次间波动的第一嫌疑人就是它——所以先看 `plan_modes` 再谈「模型状态」。
- **诊断出一个洞，不等于这个洞就是病因**（2026-08-10）：「空调先别关」检不回范例
  （0.305 vs 阈值 0.34）是可测量的事实；补上够得着的范例后每趟都检回，通过率
  15/18 → 16/18、p=1.000。**「检索够不着」与「补上就能修」是两个命题**（§22.1）。
- **「可选断言」等于把最该红的一类回归托付给「写用例的人记得加一行」**（2026-08-10，B1）。
  `no_side_effect_before_confirm` 只有 case 显式声明才判——于是「VAL 真执行了危险命令，
  而这条 case 恰好没声明」在报告里完全看不见。**安全类不变量必须由尺子自带、与 case
  声明无关**（`judge_edge_val_execution` 就是这么落的），只留一个显式豁免（确认轮）。
  同理：这类断言要配 `<面>_observed` 标志，**没观测和观测到零是两件事**。
- **反向验证要两头做**（2026-08-10，B1）：既证明「注入缺陷会红」，也证明「对照仍绿」。
  T2 那批回退后 speech/action 两条红、而零输出回退与空 delta 对照仍绿——**后者证明的是
  「没修过头」，和前者一样重要**。只做前一半，一个恒红的断言也能骗过验收。
- **测试红了先问「是修坏了还是前提变了」**（2026-08-10，B1）：纵深防御一旦建立，旧的
  **单层**突变测试会自然失效——`test_edge_premature_execution_...` 原来只打掉端侧
  `_confirm_required` 就能让危险动作落地，VAL fail-closed 后那个突变不再生效，测试转红。
  正确处置是改写成「两道闸都破」并**补一条把「单破一层不够」钉成断言**，不是回退防御。
- **诊断动作本身会污染下一次跑批**：`scripts/run_e2e.py` 会重建服务并把运行时标记成
  `runtime_freshness: unverified`，在两次全量批之间穿插它，下一批的 L3 阶段会重建
  llm-gateway、掐断 primary 的网关连接（大批 `planner_unreached`）。单独定性 L3 之后
  **整批重来**，别接着跑还没开始的那一批（§19.4，2026-08-10 亲自踩的）。
- **「我没动过 git」不等于「HEAD 没动过」**（2026-08-16，Q12）。全程没执行过任何 git
  命令，本地 main 仍然从 `dc856ce` fast-forward 到了 `bf92c22`（并行的云发布工作线），
  段 2 凭空多出 22 条用例。**攻击这类差额要用一次可复现的对照，不是猜**：
  在两个 SHA 上各建临时 worktree 数 `--co`（`scripts/tests` 756 vs 778，段 1 逐字相同）
  ——worktree 里缺 codegen、两边都是 3817+104 errors，**分母一样破，对照仍然成立**。
  同批第三条来源是**环境**：`scripts/tests` 有一批按 symlink/junction/PowerShell/docker
  可用性跳过的用例，换个 shell 起就从 skip 变 pass（9 条）。
  **跨环境比 skip 数没有意义，比 passed 数也要先对齐 skip。**
- **净增量要跟同一个 SHA 比，不能跟文档里那个数比**（2026-08-10 起两批都验过）。基线行
  会因为「实测之后又有提交加了测试却没刷新」而落后；对不上时**先怀疑基线陈旧**，再怀疑
  自己多出来了东西——反过来会凭空制造「N 条不明用例」的假问题。**每次刷新基线都要能把
  净增量逐条点上号**（B3/B4 那批 +132 = 六个新文件 55+27+5+24+18+3）。
- **校验要复刻消费方的解析，不发明通用真值语义**（2026-08-11，B3）：`AUTH_REQUIRED=1`
  在 Go 侧（`EqualFold(v,"true")`）是**关**，`GRPC_TLS=ON` 大写在 switch 精确匹配下也是
  **关**。一个「看起来是真」的检查会在这两处报绿而开关根本没开。同 CLAUDE.md §6
  「防御要防到真正会被拿去判定的那个值」。
- **静默回落就是要消灭的形态**（2026-08-11，B3）：未知 `DEPLOY_PROFILE` 值直接拒启、
  不回落 dev——拼错档位却按零校验跑，比没有这道闸更危险（运维以为自己在跑硬校验）。
  推广：**任何「认不出就用默认值」的分支，先问默认值错了会不会没人发现。**
- **「写进 anchor / 写进公共位置」不等于「每个消费方都有」**（2026-08-11，B3，同族第三次）。
  `DEPLOY_PROFILE` 加进 compose 的 `x-python-env` anchor 后，registry / edge-orchestrator /
  proactive 三个服务**根本没用那个 anchor**，闸形同虚设——纸面推理与 `docker compose config`
  都没抓到，**容器演练当场抓到**。前两次是 shop 域零范例（门禁只读 manifest，而 mcp-bridge
  能力由 servers.yaml 启动期合成）。修法是把覆盖面变成断言，不是「下次记得」。
- **扫不全的结构断言比没有更糟——它会让人去改本来是对的代码**（2026-08-11，B3）：
  上一条那个覆盖断言第一版只认绝对 import，`from .server import serve` 断链，
  14 个 Agent 全被误判成「够不着闸」。**写完扫描类断言先验证它扫到的集合非空且合理。**
- **不加第二份表达同一件事的声明**（2026-08-11，B4）：方案要 `risk` 落字段，落地改成派生
  ——B1 刚把「危险与否」收敛成 `require_confirm` 一个权威，第二份声明必然漂移；且它唯一的
  消费方（B6）尚未开工，先落即死字段。**对以后任何「再加个字段表达同一件事」都适用。**
- **一条断言抓什么，以实测为准不以命名为准**（2026-08-11，B4）：`edge_intents` 那条「挂错
  对象块」的断言**抓不到**动作段拼错（`trunk.opne` 照样解得出对象 `trunk`），那一族是
  「验证定义」车道抓的。红灯验证时要看**是哪条车道红**，不是只看 exit 码非零。
- **分母挑得越干净，假阳性越好看**（2026-08-11，B6）。B6 分类器的假阳性首版按「有 plan
  金标的轮」取分母，读出漂亮的 **0/472**——因为那个口径把 `ei.*` 端侧 ingress 用例
  （「暂停」「锁车门」，恰恰是零谓述标记的裸动词短语）整批挡在了分母外。改成「除澄清
  金标之外的全部轮」才是 4/574。**报假阳性率之前先问一句：被排除的那些是不是正好是
  最难的那批。**
- **确定性判据的 p 值分母是人为的**（2026-08-11，B6）：形态分类器对同一条输入判 20 次
  还是 2000 次都一样，重复不是独立样本。与 planner 的 11/20 摆一起算出 p=1.23e-3 只是
  为了同表可读，**真正的内容是「零方差命中 vs 45% 漏判」**，不是那个小数点。
- **「先验证覆盖再读结论」对差分等价同样成立**（2026-08-11，B5）：21 条场景在表驱动前后
  逐字一致，可首版语料只触发了 13 条策略里的 11 条——那两条上这 21/21 一个字都没说。
  补足语料后才 13/13。**等价性证明的强度上限是它覆盖到的那部分。**
- **同一件事的两份声明会在落地时暴露**（2026-08-11，B5/B6，B4 那条判据的第二三例）：
  B5 §4.1 草图既把 `FINAL_RECEIVED` 列成枚举第四态、又传 `got_final` 形参；B5 §3.1 的
  `metric_tag` 有 11 条会与 `name` 逐字相同、`preserve_previous` 与 `stage="accept"`
  说同一件事。**方案里长这样不算错，落地时照抄才是**——落地前逐字段问一遍「它有没有
  第二个真消费方」。
- **「代码里 import 得到」和「镜像里拷进去了」是两件事**（2026-08-11 真栈演练，B3 判据
  在 Docker 层的复发）：B3 给 `observability/collector` 与 `proactive` 的入口加了
  `from runtime.profile import enforce_deploy_profile`，`test_profile_coverage` 那条
  「够得着闸」的断言照过——**它读的是仓库里的源码**。可这两个 Dockerfile 没有
  `COPY runtime`，镜像里根本没这个包，**一重建就 ModuleNotFoundError 起不来**。
  既有容器跑的是加闸之前的镜像，于是这处断裂在 **40 小时里毫无症状**。
  修法是把构建闭包也变成断言（`test_service_image_contains_the_runtime_package`），
  不是「下次记得」。**推论：只改 Python 不代表不用重建——共享包新增消费方时，
  先查该服务 Dockerfile 的依赖闭包。**
- **反例最好从被测系统自己的知识库派生**（2026-08-11，B6）：「特征里不许有领域词汇」
  这句话手抄一份词表就等于没写（迟早与知识库漂移）。改成从 `commands.yaml` 派生
  对象 id / display_name / edge_intents 段来比对，**首跑就抓到「导航」**——它同时是
  谓词和 VAL 对象名。同 B4「`VEHICLE_INTENTS` 从手工集合改为知识库派生」。

---

## 5. 第一步（任何人接手都先做这个）

**第 0 条永远是先确认档位**——它决定后面哪几条能跑：

```bash
python scripts/dev_stack.py target show     # ← 先跑这个
```

`target=local`：

```bash
cp .env.example .env        # 可选填 LLM_API_KEY；不填走 mock 也能跑
make proto                  # 生成 gen/python + gen/go（没有它什么都跑不起来）
python test/smoke_edge.py   # 验证端侧逻辑（无需 docker，应 13/13 通过）
make up                     # 起全栈（首次需调试，见 docs/dev-guide.md）
```

`target=cloud`（**当前就是这一档**）：**`make up` / `make e2e` 是红线，不要跑**。
本地只承载编辑、单测、静态检查与 Vite；整栈状态与端到端走云档入口：

```bash
python scripts/dev_stack.py target show     # 确认 cloud
make proto                                  # 两档都要（codegen 是本地的）
python test/smoke_edge.py                   # 两档都能跑，不需要 docker
python scripts/dev_stack.py status          # 云端 5 个端点健康度 + 当前 release
python scripts/run_e2e.py --target cloud    # 缺省只跑 2 条 remote_safe
python scripts/dev_stack.py hmi             # 前端联调（dashboard 同理）
```

⚠ 云端连接三参数走环境变量 `CAR_AGENT_DEPLOY_HOST` / `CAR_AGENT_DEPLOY_USER` /
`CAR_AGENT_SSH_IDENTITY`（**不在 `.env`、不在 `dev-stack.local`**），缺任一项
`dev_stack` 返回 `configuration_rejected`。真栈命令**一律走 PowerShell**
（Git Bash 的 MSYS `ssh` 会吃掉转义，远端报 `unexpected EOF`）。
**要回本地真栈**：`target set local` → 人工启动 Docker Desktop → `make up` → `status`
（切档需要当轮授权，两个方向都算）。

环境/工具没装齐、Windows 无 make、单服务调试 → 看 `docs/dev-guide.md`。

---

## 6. 改完怎么自检（提交前必做）

| 改了什么 | 自检 |
|---|---|
| 任何 Python | `python -m py_compile <改动文件>`；相关 `python -m pytest <agent>/tests` |
| 端侧逻辑（fast_intent/val/edge_agents）| `python test/smoke_edge.py` |
| HMI / TTS | `cd hmi && npm test && npm run build` |
| Dashboard / 可观测 | `cd dashboard && npm test && npm run build`；全栈后查 `http://localhost:8092/healthz` 与 `http://localhost:5174`；badcase 贯通链路 `python test/e2e_obs.py`（turn 落库/obs.llm/日志关联/badcase/重启持久化） |
| proto | `make proto` 重新生成，确认 codegen 无错 |
| 端到端链路 | `make up` 后 `python test/e2e_ws.py` |
| 新增 Agent | 契约测试（参考 `agents/navigation/tests`）+ 在 compose 注册 |
| 端侧车控能力（知识库 / 意图 / 话术）| `python test/eval_capability_integrity.py`（六维逐对象，CI blocking）+ `python scripts/check_intent_gate.py`（对抗覆盖 strict）+ `python -m pytest orchestrator/edge/tests -q`（含意图面迁移探针 **与两条 VAL 校验断言**）。⚠ **门禁覆盖不到「规则产的命令过不过得了校验」那一段**——它只走 `edge_call.decode_intent` 一个产出方且跳过 `_validate_command`；端侧快路径那条由 `test_classifier_exit_parity.py::test_fast_path_command_is_accepted_by_val` 与 `test_corpus_objects.py::test_recognized_command_is_accepted_by_val` 守（**名字对不代表命令合法**，契约 §9.29 五段链）。SOP 见 §7.1 |
| 新增服务（compose 里加一个自建镜像）| `python -m pytest runtime/tests -q`——它断言每个自建服务都拿到 `DEPLOY_PROFILE`、入口够得着部署形态闸、**且该服务 Dockerfile 真的 `COPY runtime`**；**加进 `x-python-env` anchor 不等于配上了**（有服务不用那个 anchor），**源码 import 得到不等于镜像里有**（collector/proactive 就这么断过 40 小时）|
| 给某个服务的入口加共享包 import（`runtime.*` / `observability.*`）| **另外查一遍有没有独立脚本直接 import 它**——`pytest` 绿不代表裸跑绿：根 `conftest.py` 会替测试把仓库根挂进 `sys.path`，**独立脚本没有这个待遇**。2026-08-16 实测：`fast_intent` 加了 `runtime.polarity` 之后，§5「任何人接手都先做这个」的 `python test/smoke_edge.py` 直接 `ModuleNotFoundError`，而全量 pytest 一条红都没有。再查**那个服务 Dockerfile 的依赖闭包**——少一行 `COPY` 就是「一重建就起不来」，而既有容器跑着旧镜像时**完全没有症状**。`python -m pytest runtime/tests -q` 会抓 `runtime` 那一类 |
| Planner 重试/守卫规则（B5）| **先改 `orchestrator/cloud/retry_policy.py` 的表，不要在主循环里加 `elif`**；同步方案附录 A 的清单表（`test_retry_policy.py` 逐列比对，改一处不改另一处即红）；`python -m pytest orchestrator/cloud/tests -q` |
| 中文时间词（时段/日词/中文数字/12h 修正）| **改 `runtime/cntime.py`，不要在消费方本地再写一张表**（此前三份实现给出三个答案）；`python -m pytest runtime/tests/test_cntime.py agents/_sdk/tests/test_timewindow.py agents/reminder/tests/test_timeparse.py agents/info/tests/test_weather_answer.py -q` |
| 槽值保真 / 给 `_resolve_slot_refs` 加挂点 | `python -m pytest runtime/tests/test_slot_fidelity.py orchestrator/cloud/tests/test_slot_fidelity_wiring.py -q`（同文件另有 `undeclared_slots`：**契约**比原话少一维时只观测不改值，判据零领域词）——后者含**覆盖面守卫**：每个调用点必须传 `ctx`（三条执行路径 executor / D0 / T2 共用这一个收口，少传不会报错、只会让挂点在那条路上不发生）|
| 接送人称 / 目的地接地（person-pickup）| `python -m pytest agents/navigation/tests/test_person_destination.py agents/navigation/tests/test_dest_grounding.py memory/tests/test_relation.py -q`——三处判据各自有**反向对照断言**：给了具体地点的复合句不得被改写 / 已设置的常用地点不许被人称顶掉 / 两个具名孩子两所学校仍然是「问一句」。⚠ **动 `_DEST_CATEGORY_ANCHORS` 或 `boundaries.yaml` 之后必须再跑 `python scripts/check_intent_gate.py`**：台账每加一条裁定，`validate_boundary_coverage` 就要求**双向各 2 条**对照语料，而范例门禁 `test/eval_exemplars.py` **不查这一条**——2026-08-20 就是这么绿着提交、被全量 pytest 翻出来的 |
| 省略式开关的确定性消解 / 执行事实进焦点（Q7 EL1-OR2）| `python -m pytest orchestrator/cloud/tests/test_execution_focus.py orchestrator/edge/tests/test_mixed_edge_executed.py -q`——含**接线守卫**（`build()` 真的走确定性路径且零 LLM）与**反向对照**（不该接管的句子仍走 LLM）。⚠ 判据里**不许出现任何对象词/领域词**，`fullmatch` 是安全边界不是写法习惯；动 `_FOCUSED_CONTROL_ELLIPSIS_RE` 后必须重跑 `test_actionability.py`（B6「只写不读」红线同族）。契约 `docs/conventions.md` §9.26 |
| 商户规格值域 / `input_schema` 契约（mcp-bridge）| **改 `agents/mcp_bridge/servers.yaml` 一处**（槽名 + `input_schema` 条目），**不要在 `luckin.py` 里再写一张组名表**——那张 `_SPEC_GROUPS` 是照常见叫法猜的、和真机对不上两个多月（契约 §9.31）。`python -m pytest agents/mcp_bridge/tests -q`——含 `test_merchant_spec_contract.py`（**声明 ⊆ 真机台账，单向**）与选店/选品两跳的规格保真断言。⚠ **声明新组名之前先扫台账**：`python scripts/probe_merchant_specs.py --dept <id> --write --scanned-on YYYY-MM-DD`（**必须营业时段**，打烊门店取不到 `productAttrs`）。真栈复验 `python scripts/probe_qa_regression.py --group spec`——**三轮**（查店→选门店→选商品→预览），**只跑到 `need_confirm`、绝不发确认帧**（商户写要单轮人工授权）|
| 可执行性判定特征（B6 shadow）| `python -m pytest orchestrator/cloud/tests/test_actionability.py -q`（含「特征里不许有领域词汇」的知识库派生断言）+ `python test/eval_actionability.py` 看召回/假阳性两侧。⚠ 后者是**取证脚本不是准入闸**，不在 CI blocking 里 |

不要为了"让它跑起来"注释报错或加绕过标记——找根因（CLAUDE.md §6）。

---

## 7. 最常见任务：新增一个 Agent（最短路径）

1. 复制 `agents/navigation/` 结构到 `agents/<snake_name>/`（包目录 snake_case，agent_id kebab-case）。
2. 改 `manifest.yaml` 声明能力/权限/trust_level/deployment；**若 Agent 需要精确位置/电量等敏感上下文，必须声明 `context_scopes`**（`location` / `vehicle_state`，含调子 Agent 透传的 propagator）——否则编排按最小化下发会剥掉这些键。
3. 继承 `agents/_sdk` 的 `BaseAgent`，实现 `handle()`（**别重写 gRPC/注册**，SDK 已封装）。
4. 写 `tests/` 契约测试。
5. 在 `deploy/docker-compose.yaml` 注册服务（分配新端口，见 `docs/conventions.md` 端口表）。
6. **不改编排核心**——注册后 Planner 自动可路由。

详见 `agents/_sdk/README.md` 与 `CLAUDE.md` §3。

### 7.1 另一件常见任务：新增一个**端侧车控能力**（不是 Agent）

先跑骨架生成器，它会把要填的地方逐段打出来，并列出四处生成不了、必须人写的东西：

```bash
python scripts/gen_capability_skeleton.py rear_wiper --display-name 后雨刷 --operates open,close
```

判据：**这件事不该靠「记得改十来个地方」**。除雾能力落地那次漏了对抗覆盖，就是因为
「要改哪些地方」只活在某个人的记忆里。现在漏一处就有具名红灯——2026-08-11 用虚构能力
`rear_wiper` 全流程演练过：只加 `commands.yaml` 对象、其余不做时，话术 ×2 / 等价类 ×1 /
迁移探针 ×1 / 台账陈旧项 ×1 / L0 对抗覆盖 ×6 逐条报出来，逐项照做后全绿。
门禁是 `test/eval_capability_integrity.py`（六维逐对象断言，CI blocking）。
⚠ **它覆盖「声明→可达」五段链里的 ①②⑤，不覆盖 ③④**（规则产得出结构化命令 /
那条命令过得了 VAL 校验）：门禁逐条跑的是 `edge_call.decode_intent` 那**一个**产出方，
而端侧快路径 `fast_intent.classify_structured` 是**第二个产出方**，两者形状可以不同
（方向盘加热就是这么断的：一个产 `set`+`enabled`、一个产 `open`，后者知识库不认）。
③④ 由 `test_classifier_exit_parity.py` 与 `test_corpus_objects.py` 的两条 VAL 校验断言守
——**新对象要在 `orchestrator/edge/tests/corpus/vehicle_objects.yaml` 留一条识别语料**。
五段链全表见 `docs/conventions.md` §9.29。

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
