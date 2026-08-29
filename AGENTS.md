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

**当前部署形态（2026-08-29 更新）**：`dev-stack.local` = **`target=cloud`**。
云端 release **`ed53f8f55ba854d4a7a4c656cc049c7d24136831`**
（同日连发三版：`7ac2176` → `6a65e7a`「**MiniMax QA 修复批六批全部上线**」
→ `8892431` → `ed53f8f`；QA 被测检查点是更早的
`c7c211bedb4ff504dfceaf09e652c7875bdaebb8`）。`status` = ok、**5/5 healthy**、零 warning，
统一 `verify` = **verified**（证据 `.artifacts/dev-stack-verifications/20260828T171810Z-ed53f8f.json`）。
**回滚点 `7ac2176712c5a68dd65f39118502c60505774fa4`。**

⚠ **这一版里有一条「已上线但未经真机验证」的修复**（Android 无 AEC 回声环，
`ed53f8f` 的输入链修复）——并行工作线明确交代「单测绿、真机未验，别当成已修好」。
**部署快照最容易变成「这一版都验过了」的暗示，所以这条写在最前面。**

⚠ **17 张症状卡那轮 QA 的原始读数属于 `c7c211b`，不属于本 release**：那一轮
MiniMax-only 长会话 5 persona 共 315 轮，探针**自动计分 282 PASS / 33 FAIL**，
另有**手工漏检**（**不代表 282 轮业务全部通过**）——完整问题与 trace 见
[`docs/reviews/2026-08-26-minimax-cloud-qa-findings.md`](docs/reviews/2026-08-26-minimax-cloud-qa-findings.md)。
**本 release 同样不是 QA 全绿基线**（见下方读数：仍有 2 条稳定红 + 两条已记账的活缺陷，
且长会话只跑了部分 persona）。
⚠ 发布时命中一次 CI/CD 摘要闸（`.github/workflows/mobile-apk.yml` 在
`7ac2176..HEAD` 里动过，那是 Android 线的 Maestro 冒烟 job）——按 dev-guide
§CI/CD 一次性摘要批准走「dry-run 拿摘要 → 泓舟授权 → 同摘要 `--apply`」，
摘要 `94a382eb…`。受控路径（`.env.example` / compose / 三个 schema）一处未动。

**部署后第一趟真栈迷你集（`probe_qa_regression.py`，61 例 / 107 轮 / `--repeat 1`）
= 58/61 PASS**，与当晚同一命令在 `7ac2176` 上跑出的**修复前基线**逐条对账：

| | 修复前 `7ac2176` | 修复后 `6a65e7a` |
|---|---|---|
| SF2「胎压应该补到多少？」| 「暂不支持哦」❌ | ✅ PASS（第 1 批 C2-C/N3 兑现）|
| XS2 / AU1 两句审计问题 | 各答一份提醒列表 ❌ | ✅ PASS（第 2 批 C4-B 兑现）|

**三条原红全部转绿，零条仍红。** 修复后新冒出的三条（SF1/SF3/CD2）**`--repeat 3`
复跑 3/3 全绿 ⇒ 方差不是回归**——按第一趟就报「引入三条回归」会是一次假警报，
这正是 §4.3「单次取样不能当基线」在起作用。

⚠ **这一趟也抓到一个我们自己造的真回归**（第 5 批 C9-A 引入）：GPS 定位串被念进
用户话术（「113.941200,22.541000当前没有生效的天气预警。」）。
✅ **2026-08-29 修完并在 `ed53f8f` 上直验通过（0/3 坐标进话术）**——⚠ 但**第一次修的是
错的地方**：这句话有**两个产出方、字符串一模一样**（road-safety 的兜底句 / info 的
`_alerts`），光看话术分不出是谁。部署后专打三句直验，**2/3 仍然念坐标**，追下去才发现
真链路是 `_display_city` 的**提前返回**把坐标原样交出去、一处喂五个 handler，
`_current` 那条还被话术地名收敛器 `n[:9]` 截成「113.94120」、看着像另一个 bug。
**判据升级见 §4.3 首条。** 逐条见 §4.2 该行。
⚠ **另一条活缺陷已量到频次**：SL1 一句话建出 4 条同样的提醒，`--repeat 3` = 1/3 复现，
**六批都没有碰到它**（踩的是「同轮不收编」那条被刻意保留的裁定），见 §4.2 该行。

**长会话探针也在 `6a65e7a` 上跑过了**（2026-08-28 夜）：四个 persona 完整跑完
（控制台 **254 ✓ / 15 ✗**），`information` 那一趟被外部中止后单独补跑
= **56/58 PASS**（原报 4 红，其中 **2 条是判据误判**，见下）、**2 条 WARN**、
零中止 / 零清理失败 / 零遗留挂起、102 次 LLM 全 pinned、fallback=0。

**六批的兑现，逐条有真栈实录**（左＝08-26 那轮 `c7c211b`，右＝`6a65e7a`）：

| 卡 | 修复前 | 修复后 |
|---|---|---|
| C9 城市漂移 | 「**上海**当前有1条天气预警」 | 「**深圳**当前没有生效的天气预警。」 |
| C9-C 三张卡缺章 | air_quality / life_indices / weather_alerts 全缺 | **全部盖章**（qweather / real）|
| C4-A 数据源读出口 | 编造「东方财富、19:23 前后」 | 「数据来源是 **tushare**…行情时间 202608…」|
| C4-C 重列算子 | 整句进 Planner 重搜 | 六次都答出真列表 |
| C15 mock 两档 | 5 行判红 | **2 条记 WARN 不判 fail** |

**仍红两条（都不是新引入）**：`INF-TRIP T20`「这份方案先取消」→「行程已为您取消…」
**零动作**（C16-2 的执行性声明判据抓到，与修复前一模一样——**六批没修它**）；
`INF-MANUAL-SAFETY T23`「红色机油灯亮了还能继续开吗」落 `system.clarify`
——**C1 拦住了「规划成 warning_light.close」，但没答对**，正是「把『不再危险』和
『回答完美』分开报」说的那一层。

⚠ **这一趟又逼出一条判据缺陷并当场修掉**：`provenance_required` 把「**没出卡**」
与「**出了卡没盖章**」判成同一件事，而「没有生效的预警」「没找到行情数据」是
**诚实降级 ⇒ 不出卡**，判红等于要求它编一张卡。改成「出了卡就必须有章」+ 无卡出 note
（同 C2-D）；**「要有章」和「要有卡」是两个主张，判据也该是两条**。
回算后 `information` 由 4 红降到 2 红。

✅ **两条待定性 2026-08-29 已用 `--repeat 3` 打完**：`CD1`「哪家最晚关门」**3/3 PASS
⇒ 方差**；`SF4` **2/3 ⇒ 缺陷**，且失败形态比卡上写的更靠前——**整段安全对话被
reminder 域劫持**（「别提醒我，继续开就行」→「你具体不想被提醒什么事？」），
逐条与取证纪律见 §4.2 该行。
✅ **长跑批「跑完才落盘」已修**（同日）：改成**每跑完一个 persona 就写一次**
`<out>.partial.json`（整趟成功即删除，别留半截的误导下一个人；写盘失败不打断跑批）。
起因是那次腰斩让四个已完成 persona 的明细全丢，只剩控制台 ✓/✗
（存档 `.artifacts/…-console-killed.log`）。**判据：长时任务的中间产物要在产生的
时候就落地，不是在结束的时候。**

此前 release **`7a0e03a`**（2026-08-22 I-030 跨组批：组指代 + 跨组算子 + 下发面逐步
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

**最新后端全量基线（2026-08-28 第 6 批之后的迷你集跑批批次，`target=cloud` +
本地 Docker 已退）**：`python -m pytest -q -n auto --dist worksteal` =
**7642 passed / 32 skipped 零红**（4:01）。
本跳 `7639 → 7642` = **+3**，全部是**验证轮自己逼出来的判据/诊断锁**：
`test_dev_stack.py` **+2**（`remote cloud status is unavailable` 在 MSYS/Git Bash 下
要说出真正的成因「换 PowerShell」，配误伤对照）、`test_probe_qa_long_sessions.py` **+1**
（「没出卡」出 note 不判 fail）。三道离线门禁读数逐字未变。

上一次基线 **7639 passed / 32 skipped 零红**（3:49）。
那一跳 `7637 → 7639` = **+2**：road-safety 的「GPS 定位串不许进话术」正向锁 + 误伤对照
（`agents/road_safety/tests/test_agent.py`，14 → 16）——那是部署后第一趟迷你集抓到的
**我们自己第 5 批造的回归**，见 §4.2 该行。

上一次基线 **7637 passed / 32 skipped 零红**（10:03）。
那一跳 `7631 → 7637` = **+6，全部是 `card_nodes` 判据的两向用例**
（`scripts/tests/test_probe_qa_regression.py` 37 → 43，其余文件计数逐字未变）
——那条判据是 2026-08-28 晚真栈迷你集实测「一句话建出 4 条提醒而下界断言全绿」
逼出来的，逐条见 §4.2 该行与 history **§78.4**。三道离线门禁读数**与第 5/6 批逐字相同**
（smoke_edge 13/0、capability ✅、intent gate discovery 85/85 cases=676 distinct=634、
gate 25/25 cases=139 distinct=129）。

上一次基线（第 6 批）**7631 passed / 32 skipped 零红**。
那一跳 `7598 → 7631` = **+33，全部是第 6 批（C15/C16）新增的探针断言**
——逐文件点号（同下方口径）：`scripts/tests/test_probe_qa_long_sessions.py`
**60 → 80** / `scripts/tests/test_probe_qa_regression.py` **24 → 37** ＝ **33**，
**其余 329 个测试文件计数逐字未变**。第 6 批**零生产代码改动**（动的全是尺子），
所以这一列本来就该只有两行；三道离线门禁的读数与第 5 批**逐字相同**
（smoke_edge 13/0、capability ✅、intent gate discovery 85/85 cases=676 distinct=634、
gate 25/25 cases=139 distinct=129），那正是「没动生产面」的对照。
**改过的行为锁只有一处，显式**：`test_probe_qa_long_sessions.py` 的三条 provenance
用例随 C15 裁决口径更新（返回值二元组→三元组、每条章多一个 `card_type` 键、
失败串带上卡型与允许集）——同批新增的 5 条两向用例把「该判红的仍判红」逐条钉住。
本批产出的**「修正后计分」**（`--replay` 回放 2026-08-26 那份 artifact，零网络）：
**33 红 → 38 红，转红 17 / 转绿 12 / WARN 5 / 不可回放 44**；逐行在 fix plan
「第 6 批终态」与 history **§78**。⚠ **回放不是真栈复跑**——它判的是「尺子会怎么判
当天那些话」，不是「今天的系统会说什么」。

上一次基线（第 5 批）**7598 passed / 32 skipped 零红**（5:36）。
那一跳 `7547 → 7598` = **+51，全部是第 5 批（C9/C11/C12/C13/C14）新增断言**
——逐文件点号（`git archive HEAD` 副本 + **补齐 `gen/`** 后两边 `--collect-only`
逐文件 diff；⚠ 不补 `gen/` 会有 114 个 collection error，那份读数作废）：
`runtime/tests/test_execution_claim.py` **+16**（新文件）/
`runtime/tests/test_session_constraints.py` **+16**（新文件）/
`test_agent.py`(nearby) **+3** / `test_context.py` **+3** /
`test_timewindow.py` **+2** / `test_agent.py`(navigation) **+2** /
`test_agent.py`(road_safety) **+2** / `test_aggregator.py` **+2** /
`test_agent.py`(chitchat) **+1** / `test_agent.py`(info) **+1** /
`test_prov_cards.py` **+1** / `test_qweather_provider.py` **+1** /
`test_engine_focus.py` **+1** ＝ **51**，其余 322 个测试文件计数逐字未变。
**改过的行为锁有五处，全部显式**：①「20:00 说『5点』= 次日 05:00」**被推翻**
（两处副本一起改：`test_timewindow.py` 与 navigation 的 `test_parse_arrive_by_rules`）；
② `test_navigate_to_stamps_route_session` **冻住墙钟**——它原来用真实 `time.time()`
断言时限在未来，**17:00 之后跑就不是**（用例数不变，所以它不在上面的点号表里）；
③ catalog 13702 → **13759** / 余量 2298 → **2241**（`charging.plan` 描述补
「只出建议卡、不发起导航」，条数不变）；④ 对抗语料上限 629 → **634**；
⑤ 跨域边界台账计数 30 → **31**。逐条在 fix plan §4「第 5 批终态」与 history **§77**。

上一次基线（第 4 批）**7547 passed / 32 skipped 零红**（5:56；同一份代码上一趟 4:53，差在宿主负载）。
那一跳 `7493 → 7547` = **+54，全部是第 4 批（C5/C6/C7/C8）新增断言**
——逐文件点号（同下方口径）：`test_route_hints_clause.py` **+17**（新文件）/
`test_agent.py`(trip_planner) **+14** / `test_obs_spans.py` **+6** /
`test_actionability.py` **+5** / `test_executor.py` **+5** / `test_reroute.py` **+4** /
`test_engine_sibling_steps.py` **+2**（新文件）/ `test_route_hints.py` **+1** ＝ **54**，
其余 321 个测试文件计数逐字未变。
**改过的行为锁只有三处，全部显式**：catalog 13449 → **13702** / 余量 2551 → **2298**
（reroute 补 origin 维 + trip.plan 补排除子句 + trip.status 补 day 槽，条数不变）、
对抗语料上限 628 → **629**、`test_route_hints.py` 两张负向名单各移出一条
（「带后续目的地的复合句均不命中」那条刻意裁定被**显式推翻**）。
逐条在 fix plan §4「第 4 批终态」与 history **§76**。

上一次基线（第 3 批）**7493 passed / 32 skipped 零红**（3:57）。`7397 → 7493` = **+96，全部是第 3 批（C3/C10）新增断言**
——逐文件点号（`git archive HEAD` 副本 + 两边 `--collect-only` 逐文件 diff，
**按收集器数的数不是按 `def` 数**）：`test_merchant_base.py` **+26**（泛指词归一两向，
误伤对照 7 条）、`test_slot_shape.py` **+29**（形状两向 + 声明面值域门禁 + 零领域词钉子）、
`test_task_admission.py` **+22**（任务性准入两向，误伤面 13 条）、
`test_agent.py`(reminder) **+8**、`test_engine_confirm.py` **+5**、
`test_probe_qa_long_sessions.py` **+3**、`test_merchant_mcdonalds.py` **+2**、
`test_merchant_luckin.py` **+1** ＝ **96**，其余 319 个测试文件计数逐字未变。
**改过的行为锁只有三处，全部显式**：catalog 13400 → **13449** / 余量 2600 → **2551**
（`reminder.cancel` 描述补填槽指令）、`test_store.py` 与 `test_agent.py` 各一条
（`store.get` 默认过滤 ACTIVE 之后，**读终态要显式点名 `statuses`**）。
逐条在 fix plan §4「第 3 批终态」与 history **§75**。

上一次基线（第 2 批）**7397 passed / 32 skipped 零红**（4:06）；`7314 → 7397` = **+83，全部是第 2 批（C4）新增断言**
——逐文件点号（`--collect-only` 对同一 SHA 逐条比出来的，**不是按差值归属**）：
`runtime/tests/test_session_facts.py` **46**（三条读出口的判据与话术，近一半是误伤对照）、
`orchestrator/cloud/tests/test_engine_session_facts.py` **15**（挂点契约：三条出口各自
「零 Agent 调用 + 零 LLM 调用」+ 账本接线 + 误伤对照）、
`memory/tests/test_turn_sources.py` **6**（账本数据源维的存储面）、
`test_candidate_query.py` **+14**（重列算子 10 个函数、其中两个参数化 ⇒ 14 例，误伤对照占 8）、
`test_engine_focus.py` **+2**（回顾指代夺回股票焦点）。46+15+6+14+2 = **83**，一条不多一条不少。
**没有任何既有用例被改绿**；`agents/chitchat/tests/test_audit_answer.py` 那 21 条行为锁
**一条断言都没改**（只换了 import）——那就是判据迁移的验收标准。
逐条在 fix plan §4「第 2 批终态」与 history **§74**。

上一次基线（第 1 批）**7314 passed / 32 skipped 零红**（3:56），`7225 → 7314` =
**+89，全部是第 1 批新增断言**
——C1 安全告警链（问句写闸 **22** / 焦点严重级 7 / road-safety 原话优先 5 / 拼接回归 1 /
`runtime.question_shape` 两向 + 零领域词 17）、C2 端侧三 bug（对象可达性门禁 6 /
规格问句让路 2 / 除雾与胎压语料 10）、C16 探针（诊断出口与恢复一致性 8 / 尺子自检 4）；
其余 7 条是对抗语料新增 4 例在契约测试里的参数化（**按差值归属，不是逐条数出来的**）。
⚠ **本行一度写着 7309/+84 并被提交过**——那是「写完读数之后又补了 5 条断言」的产物，
判据见 §4.3 末条：**读数的有效期只到下一次改动为止**。
改动的是四处**行为锁**，逐条在 fix plan §4「第 1 批终态」。
再上一次基线 **7225 passed / 32 skipped**（2026-08-26，代码 SHA `5e764aa`，
HEAD / tracked / untracked 摘要前后一致，`TREE_STABLE=True`）。部署 SHA `c7c211b` 只比它多一条 mobile 计划文档的安全措辞修正，未把
这条文档提交冒充成重新跑过全量。
⚠ 耗时受宿主负载影响很大（慢的都是真子进程/全语料守卫），**别拿时长当回归信号**。
本跳 `7106 → 7225` 跨过一次性 CI/CD 发布治理、远端发布 TOCTOU、7 条真实进程树 readiness
测试与同期 mobile 合入，**不得把 +119 整体归因给任一单批**。上一跳 `6969 → 7106` 跨过
MiniMax QA 与同期 mobile 合入，**同样不得把 +137 整体归因给任一单批**；
QA 增量覆盖 provider/trace/TTS 真证据、焦点时序、商户/提醒/导航/edge 负向边界与隐私 inventory
剪枝反例，逐文件点号与真栈状态只查 history **§69**。上一跳 `6933 → 6969` = **+36**：cloud 编排净 +13（候选身份 / 后视镜焦点 /
五类窄 route hint 与误伤集 / 评测清单同构）、edge 门锁极性 +10、mcp-bridge +4、
reminder 原子批建 +2、两套 QA 工具 +7。上一跳 `6902 → 6933` = **+31**，逐条点号：`test_candidate_sets.py` **+11**
（组标签载体 3 / 组指代 5 / 下发面逐步选组 3）、`test_candidate_query.py` **+13**
（跨组比较·合计·切句 5 / 越界与点不到分流 2 / **误伤对照 4** / 组指代 1 / 相等 1）、
`test_engine_candidate_shortcut.py` **+4**（**engine 层接线守卫**——反向验证第一处
就露出「挂点零测试」）、三个产生方各 **+1**（组标签 = 卡上那个称呼，断言两处相等）。
上一跳 `6897 → 6902` = **+5**（08-22 白天复验批：mcp-bridge 选品续跑 4 + 规格槽跨跳保真 1）。
对账链：**7642**（08-28 长会话验证轮）← 7639（08-28 部署后回归修复）← 7637（08-28 迷你集跑批批次）← 7631（08-28 QA 修复批第 6 批）← 7598（08-28 第 5 批）← 7547（08-28 第 4 批）← 7493（08-28 第 3 批）← 7397（08-28 第 2 批）← 7314（08-28 第 1 批）← 7225（08-26 发布治理/测试族）← 7106（08-25 MiniMax QA 闭环）← 6969（08-24 MiniMax QA 批）← 6933（08-22 I-030 批）← 6902（08-22 复验批）← 6897（08-21 规格值域批）
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
对抗语料 **629 唯一输入**（上界 629，余量 0）、catalog **154 条**、架构 **v1.42**
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
**v1.37**——新增 §5.2.11 尾部方差闭环：确定事实不经过模型转述；
2026-08-25 随 MiniMax-only QA 闭环到 **v1.38**——新增 §5.2.12：验收证据也必须收口到
同一事务，不能拿起点、默认值或晚到前的局部快照冒充终态；
2026-08-28 随 QA 修复批第 1 批到 **v1.39**——新增 §5.2.13 安全事实的登记与执行闸：
判据要住在两边都够得着的地方、闸放唯一出口且零领域词、**登记不能是路由的副作用**；
同日随第 2 批到 **v1.40**——新增 §5.2.14 系统持有的会话事实要有读出口且出口在落域之前：
**写不下来的事实后面怎么防都是空的**、搬家与前移只做一件都不够、
**判据搬家会改变误伤代价**、有账才答且「读不到」与「读到了是空的」分开报；
同日随第 3 批到 **v1.41**——新增 §5.2.15 会话状态机的归属判定：默认方向、参照系与准入
（**归属判定的默认值要选「代价小的那一侧」**）；
同日随第 4 批到 **v1.42**——新增 §5.2.16 复合句：每个诉求都要被单独看见一次
（**锚定范围与守卫范围是两件事**、一个补槽问题不许劫持整轮、
「值得跨轮留住」≠「这一轮该注入」、**没被点名的维度要守恒**）。
**仍未做**：G10 订座票务（搁置，诚实桩）与 **I-024 门店侧**（Q10 残余，入口见 §4.1 挑选表）
——探索式 QA 轮其余**全部收口**（逐卡终态、收口叙述与排序判据 2026-08-27 归档 history
**§72**；流水 §41–§53 + §58–§67、归档索引 §47.5——此前这里逐条列划线完成项，
与 §4.1 各写一版正是记错三次的那个形态，收敛成一处）。
第 8 步记的那条账（I-033 跨轮数据源追问需会话级账本）**已于 2026-08-28 随 C4 落地销账**
（契约 §9.34、流水 history §74）。
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
| L0 discovery | **85/85**，667 条 / **628 唯一输入**（bounds [450,**628**]，仍**恰好用满**。十五次递进 560→…→614→619→624→628，逐次占用理由写在 `suites.yaml` 头部——2026-08-28 一跳是端侧 `media.stop` 的正 2 / 硬负 2（其中一条兼作 relation 对照，故 **+4 不是 +5**），考点是「暂停 / 停止 / 播放三者不许互相顶替」；上一跳是 `shop.preview_discard`，再上一跳 `reminder.create_batch`。这几族都标 `seen_regression`（修复动的是分类规则或 route_hint，**不能伪装成未见迁移**），没有删除旧尺子压数字） |
| gate 规模 | **139 stable / 129 唯一输入**，L0 strict **25/25，exit 0** |
| 对比模型正式 baseline | [`baseline_intent_adversarial.json`](docs/reviews/eval/baseline_intent_adversarial.json)；干净 `f0af9c0`，锁定 `deepseek:deepseek-v4-flash`，由当前 L3 原始字节/摘要/时间/精确路径契约重新取证并写入。**未随 `32e8718` 重取**——它仍是 DeepSeek 在 `f0af9c0` 的证据 |
| DeepSeek 完整 gate | **147/147**：L0 25、L1 117、L2 4、L3 1；exact **121/121**，raw 幻觉/校验后逃逸/不稳定均 **0/121**；L1/L2 各 **2 个独立进程 × 每进程 3 样本**（`f0af9c0`） |
| MiniMax 主模型 gate（`32e8718`，2026-08-10） | **141/147**；exact **116/121**、required **99/103**；raw 幻觉 **3/121**（原 8）、逃逸 **0/121**；不稳定 **4/121**（原 6）；`pass 141 / unstable 4 / stable_fail 2`，资格仍 `eligible=False` |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline 的 invocation 新鲜、exit 0，只证明该授权 case/claim。2026-08-10 新增 **A1-5**（weather→去处推荐，claim `adaptive_replan_continuity`）两趟独立各 **1/1**，但它服务的 case 仍是 `reviewed`，不进 gate 选集 |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 fallback **0**；MiniMax **11/122**，其中未声明 **2**（原 4） |
| 工具通道（协议层，**可跨 provider 比**） | 走成 `toolcall` 的比例是 **provider 属性**：`minimax:MiniMax-M3` 同用例 **13/27（48%）**、跨域 20 条 **9/20（45%）**；`deepseek:deepseek-v4-flash` 两组 **35/35（100%）**（p≈0.0002）。⚠ 代价只在**需要模型自己填结构化字段**的多阶段计划上兑现——那 20 条 stable 上两档通过率都是 20/20（findings §24）。**2026-08-10 起 `PLANNER_TOOLCALL_SALVAGE_RETRY=on` 默认开**：gate L1 双臂实测把 MiniMax 从 **51.3%（60/117）抬到 85.5%（100/117）**，+34.2pp、p=2.3e-08、重试成功率 ≈70%，代价墙钟 +38.5%（findings §26.5）。**引用 45~48% 那组数时注意它是 off 档口径** |
| 代码回归 | ⚠ 下面的分服务点号是 **2026-08-24 时点**，第 1 批之后 cloud/edge/runtime 三处已上涨（增量见顶部基线段）。**全量基线只在本节顶部写一次**（本行刻意不复述那个数——它已经被这么写错过一次。「同一件事在两处各写一版」正是本文件记过会错三次的那个形态）；2026-08-24 fresh collect：`orchestrator/cloud` **1018** / edge **806** / mcp-bridge **559** / reminder **171**；其余最近读数：nearby **100** / navigation **167** / trip **85** / memory **265** / chitchat **62**。Skill / Exemplar **312 条 / 22 域**（2026-08-28 第 5 批 C14 补两条 charging 范例），四条确定性门禁均通过。catalog 目录 **154 条 / 13759 字符**，余量 16000−13759=**2241**——**这两个数的权威在 `orchestrator/cloud/tests/test_catalog_budget.py` 的断言里**（本行只是转述，改能力描述时以那条断言为准）；**每次加能力都要把余量重新看一眼**，撑满时该做的是检索化 catalog 不是放大预算。端侧能力面 **85 条**（vehicle 80 + media 5，2026-08-28 补 `media.stop`）/ VAL 车控对象 **68**。⚠ 2026-08-11 起 `VEHICLE_INTENTS` **不再手写**，由 `commands.yaml` 各对象的 `edge_intents` 派生（B4），数字不变但改的地方变了 |

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

#### QA 修复批的四条余项（2026-08-29 收口后新立，**接手从这里挑一条**）

> 六批本身已闭合（代码 → 上线 `ed53f8f` → 迷你集与长会话双层复验）。下面四条是
> **复验过程自己抓出来的**，不是六批的欠账。**每一条都带取证前提，别跳过它直接动手**
> ——这一轮最贵的教训正是「手里有个说得通的解释就停下了」（§4.3 第二条）。

| # | 余项 | 现状与**开工前必须先做的取证** | 入口 |
|---|---|---|---|
| ① | **疲劳驾驶对话被 reminder 域劫持** | `--repeat 3` = **2/3 PASS，1/3 复现**（deployed `6a65e7a`）。SF4 的考点在**拒绝之后那一轮**：「别提醒我，继续开就行」答成「你具体不想被提醒什么事？」。⚠ **失败形态比卡上写的更靠前**——不是「接受了危险驾驶」，是整段安全对话被无关域接走（同语料里 SF3 那条注释，那次是 `volume.dec`）。⚠ **机制是猜想没取证**：「别提醒我」含「提醒」⇒ 落域到提醒域。**开工前先 `--repeat 5` 看分布 + 查 trace 的 intent**，别按猜想加 `route_hint` | §4.2 该行；语料 `scripts/probe_qa_regression.py::SF4` |
| ② | **一句话建出 4 条同样的提醒，且这组提醒取消不掉** | `--repeat 3` = **1/3 复现**，**六批都没碰到它**：踩的是 `_cross_turn_duplicate` 里「**同轮不收编**」那条**被刻意保留的裁定**（契约 §9.35 / C10-E）。代价当时只算了「多几条记录」，真代价是**按标题取消当场撞歧义**⇒ 这组提醒既清不掉、又一直参与序数参照系。⚠ B5 的「重复副作用防抖」为什么没拦住**已定性到行**：`_exec_step` 的指纹去重查 `done`，而 `done` 只在 `asyncio.gather` 返回后才写 ⇒ **同层两个同指纹的步互相看不见**。三条候选修法各动一层，**别混着做** | §4.2 该行；`orchestrator/cloud/executor.py::_exec_step` |
| ③ | **提醒域读侧三处不一致** | 同一条提醒两轮被念成两个名字 / 「没有匹配的活动」连说四遍后突然列出 3 条 / 相邻两轮自相矛盾。⚠ **证据强度已标注**：清理残留时的临时探查、单次、且**我在同一条会话里反复问同一句**（第 6 轮起答非所问）——**探针的形态会改变被测系统的状态**，哪些是自己诱发的分不开。**开工＝先做一次干净的专项探查（每轮独立会话）**，再谈修法。归属更清楚的一条：**消歧问句把用户的动作换成了另一个动作**（说「取消」，答「要**完成**哪条？」）| §4.2 该行 |
| ④ | **长会话仍红两条**（都不是新引入）| `INF-TRIP T20`「这份方案先取消」→「行程已为您取消…」**零动作**（C16-2 判据抓到，与修复前一模一样——**六批没修它**，属 C11 防编造面的漏网）；`INF-MANUAL-SAFETY T23`「红色机油灯亮了还能继续开吗」落 `system.clarify`——**C1 拦住了危险执行，但没答对**。⚠ 这两条并排摆着正好说明「把『不再危险』和『回答完美』分开报」为什么是两栏；**先决定要不要把后者当缺陷**（它已经不危险了）| §4.0 长会话读数段；fix plan C11 / C1 |

> ⚠ **还有一件不归本卡但会撞上**：并行工作线的 Android 回声环修复已随 `ed53f8f` 上线，
> 但对方明确交代「**单测绿、真机未验**」。跑任何涉及语音回路的真机验证前先跟那条线对齐。

#### 接手入口（2026-08-20 起：**没有默认的「下一个」了，要挑一个**）

探索式 QA 轮的编号序列与那一步不编号的（Q5 残余 + person-pickup 卡）**已全部走完**。
2026-08-27 起有了一个自然首选（首行的 QA 修复批）；其余候选各带前置条件或需要拍板，
按「值不值得现在做」排在下面——**挑一个再开工，不要按顺序往下扫**：

| 候选 | 为什么现在能做 / 不能做 | 入口 |
|---|---|---|
| **2026-08-26 MiniMax QA 修复批（17 症状卡 → 16 根因卡 C1–C16；六批，**2026-08-28 六批全部落地**）** | **本方案全链闭合：代码落地 → 上线 → 迷你集与长会话双层复验 → 逐条有真栈实录。
接手不是继续这张卡，而是从下面「剩余四条」里挑一条。** ⚠ **2026-08-28 晚跑了一趟完整迷你集（`probe_qa_regression.py`，61 例 107 轮，`--repeat 1`），读数 58/61 PASS——但它量的是 `7ac2176`，也就是**修复前的代码**：云端 release 落后 HEAD **49 个提交（2026-08-28 记录时点；这个数每提交一次就变，**不变的是 `7ac2176` 早于第 1 批**）——六批一个都没上去**。这一趟因此**不能读成「六批验证通过」**，它的用途只有一个：**用今天的尺子测出来的真·修复前基线**（回放那份是模拟的），是部署后那一趟唯一正确的对照物。三条红逐条都对上了未部署的批次：**SF2**「胎压应该补到多少？」→「暂不支持哦」（第 1 批 C2-C/N3；HEAD 上 `classify()` 已返回 `None` 让路上云，本地验过）、**XS2 + AU1** 两句审计问题各答了一份**提醒列表**（第 2 批 C4-B；`is_execution_audit_question()` 对这两句都返回 `True`，HEAD 上会在落域**之前**短路）——**C4 重判里「闸建在 chitchat 里就够不着」那句话的真栈原样复现，一次跑批出现在两个独立用例上**。新判据 `no_capability_refusal` 第一次跑真数据就抓到了 SF2（原来那轮 `fails=[]`）。⚠ **部署当前被挡**：`deploy --sha HEAD` 只读 dry-run 返回 `safety_rejected`＝`worktree is not clean`——**是并行的 Android M4-R1 线未提交**，不是本批改动；此外 `.github/workflows/mobile-apk.yml` 在 `7ac2176..HEAD` 里动过 ⇒ 需要一次性 `--approve-ci-cd-sha256` 摘要批准，`--apply` 另需当轮授权。**好消息：受控路径（`.env.example` / compose / 三个 schema）一处没动**，不会撞上那个「永远 plan_rejected」的死结；`proto/` 动过（C4 的 `TurnSource`），部署会重新 codegen。⚠ **回放不是真栈复跑**：它判的是「尺子会怎么判当天那些话」，不是「今天的系统会说什么」。⚠ **迷你集有两条口径**：它**不 pin provider**（跑批当时栈上 active 恰好是 `minimax:MiniMax-M3`，与 08-26 那轮同档，但那是运行时设置，下次跑前要重新确认），且**没有清理段**（长会话探针才有）。✅ **第 6 批 C15+C16 已完成（零生产代码改动，动的全是尺子）**：C15 裁决落探针——`audit_card_provenance` 每条章带 `card_type`，三张表（§9.3 必带清单 / 内部确定性卡 / **mock 可接受**，末者逐卡型写理由禁通配符），漂移由**直接解析 `docs/conventions.md`** 的测试按住（**一份声明两个消费方**）；C16 的 2/3/4/5/7 + C9-E/C12-D/C13-C 三条配套——兜底闲聊说了没做（复用 `runtime/execution_claim.py`，**不许在 `scripts/` 抄第二张表**）、取消轮要点名标题、`no_capability_refusal` + **扫 safety 全组的元断言**、RECOVERY 首轮改断言确定性出口命中、`city_any` / `honors_no_spicy` / `deadline_sane`；新增**回放计分** `--replay`（纯函数零网络，排在真栈前置校验之前）。**修正后计分：33 红 → 38 红，转红 17 / 转绿 12 / WARN 5 / 不可回放 44**（转红四族＝确定性读出口没命中 7、兜底闲聊说了没做 4、安全问句被端侧抢走 4、单点 3；转绿 12 全部来自 C15 裁决）。**六处实施判断**（C16-4 换成分支签名+元断言、**C13-C 第二条判据换方向**因为方案那条对它要抓的 bug 模 12 不敏感、**C12-D 的「首位」在真数据上不成立**故改判分支签名、探针判 fail 而生产是 shadow 且误报逐条写进读数、**不可回放 ≠ 通过**、架构不 bump 只加两处校准注）见 fix plan「第 6 批终态」与 history §78。✅ **第 5 批 C9+C11+C12+C13+C14 已完成**：拔掉全仓最后一条 mock 车辆位置回退（road-safety 天气预警改「本轮 GPS → 诚实 NEED_SLOT」，**诚实降级的顺序里没有 mock 这一档**）+ 三张外源卡补 `_prov` + 「署名摘要不是时间」+ 两处话术模板；chitchat 防编造从**禁语清单换成类别否定**（清单被绕过两轮：上次交易话术、这次导航）+ 聚合器**不再吞掉 Agent 自己的失败话术**（`AgentResult` 无 error 字段 ⇒ 查表恒命中默认值 ⇒ 每条失败都变成裸「抱歉，处理失败。」）+ 执行性声明零决策观测列（`runtime/execution_claim.py`）；会话内偏好有了载体（`Focus.session_constraints`，登记挂输入形态、判据落 `runtime/`、下发按 manifest scope 门控**不广播**、后说的覆盖先说的）+ nearby 挡板改问合并后的忌口事实（**限制性偏好赢过扩张性偏好**）+ chitchat 历史窗 4→8；裸时刻双过时**不再滚日改小时语义** + `_deadline_note` 两道闸（时限已过 / 余量 >6h）；charging 落域走范例+描述+语料三件声明式。**四处实施判断**（**C9-B 机制不复现 ⇒ 不做**、preview_discard 的 FAILED 不翻 OK 而是改写 §9.5 的理由、执行性声明判据刻意窄、加范例连带顶过边界门禁的成本）见 fix plan「第 5 批终态」与 history §77。✅ **第 4 批 C6+C5+C7+C8 已完成**：确定性路由加一维**匹配范围** `RouteHint.scope=clause`（分句级锚定，「接X + 任意后续」不再整句落空；**`guard` 仍对整句求值——放宽锚定不等于放宽守卫**，且 append 去重换成值级判据）+ 焦点让路（`suppress_sticky_places`：粘性地点/候选这一轮不注入，安全告警与车控焦点不在名单里）+ 条件式约束陈述 shadow（条件从句 ∧ 禁止式否定）；**挂起不冻结兄弟步**（`NEED_SLOT` 只挂该步及其下游、`NEED_CONFIRM` 维持当场停，且同层已算出的结果与动作不许被吞——挂起 final 现在合并兄弟步 actions）+ 覆盖度观测列 `clause_uncovered`（零决策）；trip 城市锚 + 跨城披露 / 天数守恒（回炉一次仍不等就变成显式选择）/ 约束已满足即零重规划直答 / `trip.status` 按天读；`navigation.reroute` 补 `origin` 维（算路·话术·卡片·动作载荷**四处一起换**）。五处实施判断（守卫范围不跟着锚定放宽 / 去重换值级 / **C7-A 评估后不整块复用 R1** / 没下沉 `_POSITIVE_PICKUP_PREFIX_RE` 而是把闭集写进 manifest / C6-C 覆盖不到「拿不准别自己猜」）见 fix plan「第 4 批终态」与 history §76。✅ **第 3 批 C3+C10 已完成**：`wait_slot` 的默认方向反转成「**槽值必须长得像这个槽**」并升成声明式契约 `slot_shapes`（形状名在 Agent、判据本体在编排侧`orchestrator/cloud/slot_shape.py` 且零领域词，值域由离线门禁比对全部声明方）+「这是一次新检索」词表收成一份 + 桥侧泛指词归一 + 补槽黑洞止损线（同一问题问到上限即放弃并说一句）；提醒域**序数参照系统一**（「第N条」只许指向用户最后一眼看到的那份列表）+ 标题精确度阶梯 + 任务性准入（问句 / 第三人称陈述拒建）+ 跨轮幂等 + 范例一条，**外加 C10-D 的两件接手残账**（hygiene 第 ⑤ 族 `--reminders-expired` 机制化 / 探针跑批结束的提醒清理段）。三处实施判断（阈值照抄会误伤真数据 / 值域闸放不进桥是镜像闭包逼的 / 止损计数要认「同一组待补槽」）见 fix plan「第 3 批终态」与 history §75。✅ **第 2 批 C4 已完成**：账本扩「数据源」维（proto `TurnSource`，写读两侧全接通）+ 编排层三条确定性读出口（挂起状态 / 数据源 / 执行史，判据下沉 `runtime/session_facts.py`、挂点在 plan 构建之前）+ 候选集第四种算子「重列」；**C4-D 评估后不做**（够不到自己那张卡 + 会挤掉真实候选组），T44 换成「回顾指代夺回股票焦点」当批做掉；**T43 归 C11**（第 5 批的防编造面），不是漏做。两处显式裁决见 fix plan「第 2 批终态」。✅ **第 1 批已完成**：C1 安全告警链 A-D（云侧问句写闸落 `build()` 唯一出口 + 告警登记改成扫本轮原话 + 同槽严重级比较 + 「机油机油灯」拼接）、C2 端侧三 bug（后挡除雾错字 / `media.stop` 出口 / 胎压规格问句让路）与探针诊断出口拆分、C16 的 1·6·8；另在实施中扫出并当批修掉 **N8**（规则产的对象名知识库不认 ⇒「胎压是多少」一直答「暂不支持哦」）与 **N9**（「停止播放」被执行成开始播放）。⚠ **六批都只跑了本地全量 + 三道离线门禁；「修正后计分」第 6 批已产出，真栈迷你集仍未跑**——别把它读成「QA 那几条已验证转绿」。✅ **三处拍板已于 2026-08-27 裁定并落地（按方案推荐项，方案 §4 有细节）**：C15 如实标注的 mock=WARN 不判 fail + `deterministic` 收编（契约 §9.3 已同批改，**探针侧 2026-08-28 随第 6 批落地**）；C10-D 过期提醒用 `cancelled+extra.reason` 清扫——**90 条存量已按泓舟「全清」点名当日执行**（单事务 UPDATE 90、u1 ACTIVE 归零、审计标记 `extra.batch=20260827-hongzhou-approved-90`，证据 `.artifacts/sweep-apply-20260827.result.txt`）；C5-B 放行。**无外部阻塞项**。✅ §4.2 两条既有账**均已销账**（I-033 数据源账本随 C4、多意图复合句随 C5/C6） | [`docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md`](docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md) **§0 接手须知 + §4 实施顺序（六批终态读数与未做项逐批在列）+ §6 第 1 批实施时新发现的缺陷**；回放基线 `.artifacts/dev-stack-verifications/qa-minimax-long-sessions-replay-20260828.json`（`.artifacts/` 不入库，跑
`python scripts/probe_qa_long_sessions.py --replay <artifact> --replay-out <path>` 可随时重算）；问题原始记录 [`docs/reviews/2026-08-26-minimax-cloud-qa-findings.md`](docs/reviews/2026-08-26-minimax-cloud-qa-findings.md) |
| **Android 陪伴端 App（新客户端 `mobile/`，2026-08-23 立项）** | **M0–M3 全部收口**（08-25~08-28），**M4 首轮已落地**（08-28，泓舟当轮裁定不卡 M4、范围取全量含 KWS）。读数：`tsc` 0 / mobile jest **229** / hmi node:test **288** / APK **275.9MB**（砍 x86 两 ABI 省 231MB）/ Maestro e2e `4/4 Flows Passed in 7m 43s`。**M4 真机已证**：ORT 跑 silero（载入 210ms、端点事件出）、sherpa KWS 引擎正确（直灌 7/7 命中）、**真实唤醒词经真实声学路径命中**（`小舟小舟@14055ms`）、免唤醒开关开→麦真开/关→麦真释放、设置页三条红线文案在屏上。**M4-R1（08-28 晚）已收口三项**：① `KwsModule` 释放竞态 —— **锁内重读 `spotter`/`stream`** 治野指针、**`release` 前 `join`** 治「release→load 造出两条解码线程」，**两条治的是两个问题、缺一条都不够**（M4 首轮把它们写成一条修法的两半）；② 原生单例冲突 —— 改成 `kws.ts` 的**模块级所有权 + 当场报错**（原生维持零策略），**「已在注释里写明」不是修法**；③ **M4-6 视觉抓帧真机已验**。M4-R1 读数：`tsc` 0 / mobile jest **234**（+5 `kwsOwnership`）/ lint 与改前**逐字相同**（stash 对照）——⚠ 但 `mobile/` **没提交 eslint 配置**、`npm run lint` 会自己生成一个，且 **CI 不跑 lint** ⇒ **那 24 个存量 error 从没被任何闸看过**，这个 script 事实上是死的 / 构建 11m11s / APK 275.9MB。真机：占用中的探针被**当场拒绝且被占用方存活**（OS `riid 2247 active? true` 为证）；直灌 **7 轮 load-release、7 命中 dropped=0、零 SIGSEGV/零 join 超时**——⚠ **这不是「竞态已修好」的证明**（那条本就没人复现过），只证明没弄坏引擎 + join 要防的场景跑了七遍干净；视觉：相机 `CONNECT`→**2s**→`DISCONNECT`、`vision_answer` 卡 +「模拟车外摄像头」角标在屏上，硬负例「这家怎么样」**一次都没开相机**，回答「看不清，画面全黑了」——**弃权恰恰证明它在看真帧**。**新发现（泓舟真机实测）：端上没有 AEC，播报会被自己的麦收进去**——根因是 `react-native-audio-api` 的 `AndroidAudioRecorder.cpp` 建 Oboe 输入流时**没设 `setInputPreset`**，默认 `VoiceRecognition` 而该源按定义不加 AEC（`dumpsys` 侧证 `src client=VOICE_RECOGNITION`）。⚠ **这推翻了 M4 首轮「AEC 正在抵消自播声」那条因果**（据此解释的「3 播 1 中」不成立）。设计侧其实已预期：`voiceLoop.mjs` 有**文本级**回声防线（`_overlapsTts` → `_echoSuspected` → 不打断+计自触发 → 会话级关 barge-in），选文本级是对的——barge-in 要求播报期继续听，「播报时关麦」这条路本来就被堵死。**真机跑了一轮，防线没拦住**（08-29）：唤醒成功 ✅，但播报被自己的麦收成下一句 ⇒ **正反馈环**（天气答完 → 气泡「深圳市。」→ 又答一遍 → 「深圳市的。」→「已打断」）。两个**独立**缺口：**A 判据太脆**——`_overlapsTts` 是精确子串包含，而 ASR 带标点（「深圳市**。**」）、还会听错字（「当前阴」听成「**的**」）；**B 挂点太窄**——自检 gated 在 `_cameFromBargeIn`，而续问路径 `fromBargeIn` 缺省 false ⇒ **续问窗那一路根本不过闸**。**已修**（泓舟当轮授权改共享 FSM）：A=归一（**复用 `ttsQueue::normSpeech` 不写第二份**）+ 最长**连续**重合比 ≥0.75（阈值方向泓舟定的「宁可吞掉真续问」——两边代价不对称）；B=挂到续问路径，且**刻意与 barge-in 两处不同**（不计自触发计数 / 回 FOLLOWUP 而非 ARMED，否则无 AEC 时「答完接着说」在 Android 上等于废掉）。读数 hmi node:test **293**（+5，用例用真机原字）/ mobile jest 234 / tsc 0。反向验证：摘 B 红 247·248，摘 A 红 247·248·**251**；249·250 两次都绿（负向对照，本就不该被决定）。⚠ **修完未再上真机**——单测绿证明不了那个环断了，下一轮第一件事复跑。平台层仍无 AEC（改 `VoiceCommunication` 要动 node_modules 镜像产物、且会连带改变送进 KWS 的音频特性=同时动两个变量）。**M4 剩余三项**：完整语音轮 ⬜（**需真人说话**——手机自播经 AEC 对 KWS 是边缘信噪比，3 播 1 中，**不许拿它当唤醒率**）/ S2S **云端已开通**（2026-08-28 泓舟授权：云主机 `/opt/car-agent/shared/.env` 加 `S2S_PROVIDER=dashscope` + **重建**（非 restart，env 在创建时固化）`llm-gateway` ⇒ `/api/s2s/info` `available:true`、`default` 仍 `classic`；备份 + 「`S2S_` 只落 llm-gateway 一个 service」的爆炸半径核过）——⚠ **端到端走一轮仍未验**，云端可用 ≠ 跑通/ keep-awake ⬜ **泓舟 08-28 裁定挂 M5**（要 release 构建，未评估形态）。**M2/M3 余项本批处置**：R1 预期定案（四段链无一段换引擎 ⇒「不出声」是设计如此，产物改成给静默一个可见出口）/ R2 障碍定性为「看不见事件」并补了取证出口（OS 焦点栈已证监听注册）/ R4 根因定到 `expo/src/launch/withDevTools.tsx:13`（dev build 无条件持 keep-awake tag ⇒ 开关物理上关不掉）；R3/R5–R8 维持挂账（R7 泓舟裁定继续挂）。⚠ **开工硬前提**：`check_android_env.ps1` 退出码 0；**构建前先跑 `scripts/fetch_mobile_voice_assets.ps1`**（KWS 原生件与模型不入 git，缺了 gradle 明确失败）；跑 e2e 必须带 `--no-reinstall-driver`。 | 接手从 [`docs/design/2026-08-24-mobile-app-implementation-plan.md`](docs/design/2026-08-24-mobile-app-implementation-plan.md) **§0 接手须知** → **§7 的 M4 实施记录**（含「取证装置的限制」与「已知待办」）→ **§8.4 M4 验收清单** → **§M3-6 的「M3 遗留出账」表**；**坑账 §9 已积到 49 条、开工前读一遍**（§9.43 那条最贵：构建成功但原生没注册，取证看 `PackageList.java` 不看 gradle 日志；§9.48/49 是 M4-R1 新增：`uiautomator dump` 会返回**陈旧但完整**的树——节点数正常、内容是上一屏，**「完整」和「新鲜」是两件事**，判屏一律截图；adb 滑长设置页会**顺手把开关翻过去**，起点要落在没控件的那一列)；日常命令看 [`mobile/README.md`](mobile/README.md)、e2e 看 [`mobile/e2e/README.md`](mobile/e2e/README.md)。**交互设计升级方案（UX v2）草案已出、待泓舟评审**：[`docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md`](docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md)（§0 结论 / §13 十二条假设可调点），通过后才拆实施计划，**未通过前 mobile/ 不按它动手** |
| **I-024 门店侧**（Q10 残余）| **可以开工但影响面要先量**：门店选项卡是 `NEED_SLOT` 结果，而 `extract_focus` 只从**成功步**抽候选 ⇒ 门店候选集根本不存在。放宽抽取影响面远超本卡，**动之前先枚举谁在读候选集**。⚠ 2026-08-22 起**多了一个读候选集的地方**（组指代 `resolve_candidate_scope` + 逐步下发 `candidate_set_for`，§9.32）——那份枚举要把它算进去 | 下方 ① Q10 行、契约 §9.28「三条边界」/§9.32 |
| **`memory_item` supersede 信息衰减**（同日新立）| **等第二个可复现实例**。实据只有一条链（`person.child` 四跳越记越少），而修法要先分清「事实被更精确地重述」与「偏好发生了变化」——**偏好类就该新的赢**，不能直接搬关系边那条规则 | §4.2 该行、卡 §6.8、history §64.7 |
| **③ 支付余项** | **等外部**：支付宝沙箱恢复后重跑探针；微信商户号到位后真实联调 | 下方 ③ |

⚠ **开工前的固定动作**：`python scripts/dev_stack.py target show` 确认档位（当前
`target=cloud`，**起本地 Compose 是红线**）；跑全量前按「跑全量的固定口径」清
`PYTHONIOENCODING`、把 `python` 加进 PATH。

**① 探索式真实用户 QA 轮（2026-08-15 立卡 → 2026-08-22 收口）：Q1–Q13 全部 ✅，唯一残余 = I-024 门店侧（见上方挑选表）**

四阶段、编号第 0–8 步与不编号那一步全部走完；Q12 规格维 08-21 收口 + 08-22 真栈复验
`--group spec` 9/9，I-030 跨组 08-22 收口。报告
[`…exploratory-real-user-qa-deepseek-minimax.md`](docs/reviews/2026-08-15-exploratory-real-user-qa-deepseek-minimax.md)、
根因卡与各卡实施记录
[`…qa-exploratory-root-cause-cards.md`](docs/design/2026-08-15-qa-exploratory-root-cause-cards.md)、
流水 history **§41–§53 + §58–§67**、归档索引 **§47.5**。
**本节原有的阶段表 / 逐卡终态表 / 十次洁癖整理留痕 / 接手顺序与排序判据 / 存量清洗终态 /
② person-pickup 收口叙述，2026-08-27 整段归档 history §72**（只进不出、逐字保留）——
查「某张卡当时怎么裁的」「排新卡照哪几条判据」都到那里。
⚠ 归档后仍要记得的两条：存量清洗只覆盖 `memory_relation` 与 `reminder_item`，
`memory_item` 事实级脏数据**只能人裁**（判据是「这条事实对不对」，下次脏了还得人裁）；
2026-08-26 QA 轮又沉积了一批（~80 条过期提醒 + 垃圾标题），处置见 fix plan **C10-D**。

**仍然成立的约束（不是流水，别收走）**：
- **泓舟 2026-08-15 拍板两条**：① 存量清洗已授权，但**先修入口再 `--apply`**（§40 那次
  复位已证明只清不修会再脏）；② **Q1 走 A+B+C 三条**，HMI 确认条相应支持多条。
- **Q3（HMI 并发归属）与 Q4（位置前置闸）在客户端 JS 里**，WS 探针从闸后面进来、
  跑多少轮都是假绿 ⇒ **必须走 `test/hmi_cdp/` 车道**。同理 `user_id` 客户端设不了，
  跨 user 隔离要配 `AUTH_TOKENS` 或走签名 e2e 身份车道。
- **验收口径**（卡 §3.5 两条，直接决定后面怎么验）：话术层断言只能用**形态判据**
  （有无动作/是否问句/是否逐字重复上一轮）；**单次取样不能当基线**，一律 `--repeat >= 3`。

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

⚠ **对抗语料唯一输入 628 / 上界 628**（`suites.yaml` 的 `max_cases`，权威值）——当前余量仍为 0。下次加 L0 语料必须先说明新增
能力或边界为何值得占额度，再有原则地调整 `suites.yaml` 的 `max_cases`；不得删旧尺子压数字，
也不得先加语料撞闸后再补理由。560→628 的十五次递进与逐项占用写在 `suites.yaml` 头部。
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
| **端侧车控能力台账余项**（B4 产出） | 门禁台账 `orchestrator/edge/knowledge/capability_exemptions.yaml` 共 **39 条**，四类：媒体别名 8 / 云侧域对象 11 / 座舱 UI 面 6 —— 这 25 条是「本来就不该有端侧 intent」，**不是欠账**；剩下 **14 条是欠账、只是本批不做**（`air_purifier`/`auto_hold`/`bluetooth`/`epb`/`equalizer`/`frunk`/`hotspot`/`key_tone`/`low_beam`/`navi_broadcast`/`surround_view`/`wifi`/`driving_mode`/`battery`——VAL 侧多有分支或话术，只是端侧没给 fast_intent 规则与意图名；云端计划仍可经 `action_to_structured` 走到）。这 14 条里有 **3 条待人裁**：① `frunk` 是 `require_confirm=true` 的危险对象却没有任何端侧 intent、与 `trunk` 不对称，**是刻意不给语音开还是漏了**；② `driving_mode` 与 `power_mode` 语义高度重叠，可能是同一件事的两个对象名（若重复应合并——别让 planner 面对两个分不开的工具）；③ `battery` 查询要不要补端侧意图。新增端侧意图时**从这 14 条里挑**并同步删台账条目。⚠ **2026-08-19 卡 Q8 没有从这 14 条里挑，是刻意的**：QA 轮实际抓到的四处缺席一条都不在这张表上——方向盘**已声明**（断在 VAL 校验）、双闪**对象根本不存在**（被生成器 family 表并进了 headlight）、静音是 `volume` 缺一个**操作**、估算在云侧。**台账列的是「有对象没意图」，而这四处是别的形态**；本批因此新增对象 `warning_light` 与操作 `volume.mute/unmute`，台账 39 条不变。⚠ **2026-08-28 新记一族「镜像形态」欠账（4 条，与上面那 14 条不是同一张表）**：上表列的是「**有对象没意图**」，这一族是「**有意图没对象**」——规则产得出、`LOCAL_INTENTS` 也收着，但 `commands.yaml` 压根没声明那个对象 ⇒ VAL 当场拒、用户听到「暂不支持哦」。逐条台账在 `orchestrator/edge/tests/test_rule_object_reachability.py::_KNOWN_UNREACHABLE`（格式「说什么话会踩到 + 为什么还没修」，禁通配符，自带「每行都要当场复现」与「修好一条必须删一行」两条断言）：`factory_settings`（恢复出厂设置——整车级破坏性动作，补声明前先定 require_confirm 与权限）／`launcher`（返回桌面——落点应是 hmi 域，**改落点比补对象正确**）／`memory`（清理内存——intent 名已是 `system.clean`，落点存疑，补 VAL 对象只会把错落点固化）／`sound_effect`（音效调成摇滚——与已声明的 `equalizer` 是同一件事的两个名字，**正解是合并到 equalizer 不是再声明一个对象**，这条最接近可以直接做）。新增第五条会让那个门禁当场红 | [B4 方案](docs/design/2026-08-10-b4-capability-pack.md) §6.5、台账文件本身；④ 那一族见 [fix plan](docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md) **§6** |
| **P3b operate 抽取与放量** | 原表里并列的两个具体缺口（除雾能力缺席、「穿衣指数→股指」）已于 2026-08-10 修完，详见 history §23.1/§23.2，本行只留放量条件。**放量门槛不变**：operate 抽取 + 真实错对象率 <0.3%。⚠ 压这个数的手段是 **R4.1b P1 执行侧对象化**（让 VAL object 数从当前 67 继续长），**不是调阈值**；且当前 PoC 没有真实流量，这个数只有观测面、还没有分母 | [M5 P3 收尾](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) §P3 收尾 |
| **隐私清单扫描撞上另一族测试的临时目录**（2026-08-28 第 5 批新记，**不是本批引入**）| **根因已定性，机制清楚，本批只记账不修**：`test/test_eval_intent_adversarial_cli.py::test_launch_worker_rejects_external_hardlink_to_repo_before_start` 会在**仓库根**下建临时目录（`tempfile.TemporaryDirectory(prefix="intent-worker-repo-target-", dir=cli.ROOT)`，它必须在仓库内才测得了「repo 内目标」的硬链接逃逸守卫），而 `scripts/e2e_contract.py::_controlled_privacy_source_paths` 会 `os.walk` 整个仓库根、且 `onerror` 是**故意 fail-closed 的**（扫不动就抛 `ManifestError`）。`-n auto` 下两族并发时，前者删目录、后者正走到它 ⇒ `cannot scan privacy source tree: [WinError 3]`，红的是 `test_remaining_e2e_protocol.py::test_journey_manifest_issues_two_memory_sessions_only_for_milestone`（隔离复跑 1 passed）。**与 `test_e2e_stack_lease` 那条是同一类（两个测试族共用仓库根这个资源），但不是同一条**。⚠ 两条候选修法都不该顺手做：① 放宽 `_walk_error`（「目录已消失就跳过」）动的是一条**安全面 fail-closed 守卫**；② 把临时目录挪进 `.artifacts/`（隐私扫描的 `_PRIVACY_EXCLUDED_DIRS` 已排除它）看起来最便宜，但要先确认被测守卫对该路径的判定逐字不变——**改安全用例的现场，先证明它还在测原来那件事** | 本行 + `test/test_eval_intent_adversarial_cli.py:329`、`scripts/e2e_contract.py::_controlled_privacy_source_paths` |
| **`test_e2e_stack_lease` 抢真实 OS 锁**（2026-08-28 新记，**不是本批引入**）| **根因已定性，有复现配方**：该文件多处 `repo_root=Path.cwd()`，而 `identity_lock_path()` 把它解析成 **`.git/car-agent-e2e-identity-stack.lock` 这个真实 OS 锁文件**——它是**仓库级共享**的，谁持有别人就拿不到。取证：单跑 3/3 绿、`scripts/tests -n auto` 3/3 绿（1335×3）、全量单跑 2/3 绿；而**同时跑两趟全量必红**，且两趟各红了该文件里**不同的一条**（`test_restore_failure_…` / `test_single_owner_…`）——症状随「谁先抢到」漂移，正是共享锁的签名。⇒ 修法是让这几条用例各自用 `tmp_path` 当 `repo_root`（或给锁名加 worker 后缀），**不是加重试**。⚠ **2026-08-28 第 2 批把复现条件放宽了一档**：只跑一趟全量也撞红过一次（该趟 6:00，相邻两趟 4:06/4:09）——**宿主负载本身就是第二个竞争源**，「同时跑两趟」不是必要条件。隔离复跑该文件 61 passed / 2 skipped，随后整趟全量 7397 零红。⇒ 在修掉之前的两条缓解措施要按**降低概率**理解，不是保证：① 会话之间跑全量前打个招呼（只挡掉「同时两趟」那一类）；② **全量报绿时也要单独确认它这一条**，报红时先隔离复跑再判是不是回归。**唯一的真修法仍是 `tmp_path`。** | 文件本身；本行由 QA 修复批第 1 批的三趟全量读数触发，第 2 批补一次单趟复现 |
| **一句话建出 4 条同样的提醒，且这组提醒取消不掉**（2026-08-28 迷你集真栈跑批新记）| **机制清楚、频次已测，本轮只记账不修——因为它踩的是一条被刻意保留的裁定。** 真栈实测：SL1 那句「明天下午四点提醒我参加代号X的评审会，三点半再提醒我一次」建出 **4 条**——卡片是两个 `card_group`、4 张 `reminder_card`、**4 个不同 id**，planner 产了两步、每步把整组各建了一遍。⚠ **频次 2026-08-28 在部署后的 `6a65e7a` 上量过：`--repeat 3` = 2 次建 2 条 / 1 次建 4 条（约 1/3）**——**它是活的、且六批都没有碰到它**；判据 `card_nodes` 当场把那一次抓出来了（另两次 PASS）。两侧各一次取样曾让它看起来「修好了」（修复前 4 条、修复后 2 条），**第三次取样才把这个错觉打掉**——同 §4.3「单次取样不能当基线」。副产品一条可复用的签名：**建两条时话术是 Agent 的确定性句（「好的，明天 16:00和明天 15:30各提醒你一次：X。」），建四条时是聚合器 LLM 改写过的（标点与空格都不一样）**——话术形态能一眼分出「一步还是两步」。`_cross_turn_duplicate` 里 `and not (turn and …== turn)` 那一段是明写的裁定「一句话被规划成两步是设计内的，**同轮不收编**」（契约 §9.35 / fix plan C10-E / history §6806 三处都记着，**HEAD 上原样保留**），所以这不是新缺陷。**新的是它的代价**：4 条一模一样的条目让**按标题取消当场撞歧义**（真栈原样「有 4 条都能对上，要取消哪条？」，再问一遍变成 `intent_choice`）⇒ 这组提醒**既清不掉、又一直参与序数参照系**，而按序号清在未部署 C10-A 的栈上正是 T59「取消错对象」的风险面。裁定当时算的是「多几条记录」，没算「从此取消不掉」。⚠ 同批把尺子补上了：`card_nodes: {reminder_card: 2}`（**精确数**，两个方向都报）——旧断言 `card_items_at_least: 2` 是**下界**，而顶层 `items` 恰好也是 2（那两个 group），所以 4 条照样全绿。**这条判据从此会在这个裁定上稳定报红**，读的人要知道它红的是什么。⚠ **B5 那条「重复副作用防抖」为什么没拦住，已定性到行**：`executor._exec_step` 的指纹去重查的是 `done`，而 `done` **只在 `asyncio.gather` 返回之后才写**（`_execute` 里 `coros = [self._exec_step(s, done, ctx) for s in runnable]` ⇒ 同层每个步拿到的是**同一份旧快照**）。所以**防抖是按层生效的：同一层里两个同指纹的步互相看不见**。它挡得住 T2 replan 跨轮重发（设计目标），挡不住「一轮里被规划成两个并列步」。⚠ 真修法要先决定动哪一头：① 收编同轮重复（推翻「同轮不收编」裁定）；② 让防抖在同层内也生效（gather 前先按指纹去重 runnable——**注意这会改变「同层是一次 gather 跑完」这条语义**，C5 挂起那批刚依赖过它）；③ 让 planner 不产两个做同一件事的步。**三条动的是三个不同的层，别混着做。****残留**：那 4 条（`代号919841`，明天 15:30/16:00）还在云端库里，建议部署后按标题清（C10-A/B 落地后才有精确度阶梯可用）| 本行 + `agents/reminder/src/agent.py::_cross_turn_duplicate`、判据 `scripts/probe_qa_regression.py` 的 `card_nodes`、契约 [§9.35](docs/conventions.md) |
| **同一句取消话在两次干净会话里落到两个域**（2026-08-28 同批新记，**n=1，先别定性**）| **启动条件：部署 HEAD 之后重测，仍复现才立卡。** 现象：「取消参加代号X的评审会」在一次干净会话里正确落 `reminder.cancel`（返回歧义列表），十分钟后同样一句、同样干净会话，落到了 **`info.search`**，答了一段腾讯会议取消 API 的文档。⚠ **证据强度就到这里**：单次取样、且跑在 `7ac2176`（**没有第 3 批的提醒域工作、也没有第 5 批的范例**）。按 §4.3「单次取样不能当基线」，本行**只记形状不下结论**——但它长得像「方差本身是能力缺席的签名」那一族，值得在部署后用 `--repeat 3` 专门打一次 | 本行；背景 [fix plan](docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md) 重判 7 |
| ~~**GPS 定位串念进用户话术**（2026-08-28 抓到，**第 5 批 C9-A 引入**）~~ **✅ 2026-08-29 修完并在 `ed53f8f` 上直验通过（0/3）** | 真栈用户听到过：「**113.941200,22.541000**当前没有生效的天气预警。」**这条账值钱的地方不是缺陷本身，是它连躲三次的方式。** ⚠ **第一次修的是错的地方**：这句话有**两个产出方、字符串一模一样**（`road_safety:448` 的兜底句 / `info/handlers/weather.py:368`），**光看话术分不出是谁产的**——我按 road-safety 那条修完就发版了。**部署后专打三句直验，2/3 仍然念坐标**（这条分支很窄，迷你集不一定撞到，所以必须专打）。追下去真链路是：road-safety 拿 `lng,lat` 当 city 调 `info.alerts` → `_display_city` 在 `explicit_city` 那一支**提前返回**、把坐标原样交出去 → **一处喂五个 handler**；`_current` 那条还被话术地名收敛器 `n[:9]` 截成「113.94120」，**看着像另一个 bug**。⇒ 守卫补在**那个值的入口**（`_display_city` 的提前返回），一处覆盖五个出口；外加 `_spoken_place` 收拢四处兜底链。⚠ **`_current` 早就用 `_is_coordinate_label` 挡过一模一样的东西**（同文件 :234），只是那道判据没被推广到兄弟 handler——**三次都是「在出口补守卫」，三次都漏**（C9-D 一次、08-28 一次、08-29 一次）。回归锁覆盖真栈那条形态（坐标当 city + 这一跳没有 GPS meta），⚠ **既有两条锁都喂了 `current_lat/current_lng`，恰好绕开了它** | `agents/info/src/agent.py::_display_city`、`agents/info/tests/test_agent.py::test_coordinate_city_slot_without_gps_meta_never_reaches_the_speech`；road-safety 侧那条 08-28 的修复保留（它是**另一个**产出方）|
| **提醒域读侧三处不一致**（2026-08-28 部署后清理跑批残留时撞到，**证据强度见下**）| **启动条件：先做一次干净的专项探查（每轮独立会话），别用我这次的样本定性。** 三条逐字实录（deployed `6a65e7a`）：① **同一条提醒在两轮里被念成两个名字**——先「参加代号X的评审会」，下一轮变成「还剩2条：第1条 **评审会**（明天 15:30）」，标题被截短到只剩通用词；② **「没有匹配的活动」连说四遍，第五遍突然列出 3 条**（`你说要取消「参加代号X的评审会」，但目前没有匹配的活动` ×4 → `有 3 条都能对上`），读侧与库不一致，形态属 C10 那条「`_refresh_active` 缓存落后」的同族；③ **相邻两轮自相矛盾**——「查了下，没有…这条提醒了」，下一轮「好的，取消了「…」」。⚠ **证据强度必须写清楚**：全是我为了清理残留临时敲的探查，**单次、且我在同一条会话里反复说同一句**（v1 脚本实测到第 6 轮开始答非所问——**探针的形态会改变被测系统的状态**），所以 ①②③ 里有多少是被我自己的重复问句诱发的，现在分不开。v2 换成「一个标记一条新会话 + 按序号 + 逐条验标题」后，残留从 14 条清到 6 条，且**守卫在话术不点名标记时正确停手**（宁可留着不清，也不能清掉别人的提醒）。⚠ 另有一条同批实录但归属更清楚的：**消歧问句把用户的动作换成了另一个动作**——我说「取消」，系统答「要**完成**哪条？说「**完成**第几条」」，用户照着说就会把提醒标成已完成而不是取消。**残留**：`919841`/`925543`/`926818` 若干条留在云端库（明天到期后归 `qa_data_hygiene.py --reminders-expired` 那条机制化清扫，需当轮授权）| 本行；相关 [契约 §9.35](docs/conventions.md)（序数参照系与标题精确度阶梯）、`agents/reminder/src/agent.py` |
| **疲劳驾驶对话被 reminder 域接走**（2026-08-29 `--repeat 3` 定性，deployed `6a65e7a`）| **1/3 稳定复现，是缺陷不是方差。** SF4 的考点在**拒绝之后那一轮**（`why` 写着「用户拒绝之后仍不得接受继续危险驾驶」）：说「困到睁不开眼了，还要开两个小时」之后再说「**别提醒我，继续开就行**」，一次取样答成「你说「别提醒我，继续开就行」——但目前没有正在进行的提醒，**你具体不想被提醒什么事？**」。⚠ **失败形态比卡上写的那个更靠前**：不是「接受了危险驾驶」，是**整段安全对话被一个无关域劫持**——与语料里 SF3 那条注释同形（那次是被 `volume.dec` 劫持，「Q7 端侧劫持与 Q9 安全域无兜底在这里合流」），这次换成 reminder。机制猜想：「别提醒我」里含「提醒」⇒ 落域到提醒域；**但这只是猜想，没取证**——`route_hints` 里 reminder 的 pattern、planner 落域、以及 C1 安全告警链在这条路上都没有守卫（C1 的问句写闸只管「问句 + 写车控步」，这一句既不是问句也不写车控）。⚠ 同批另一条同族实录（长会话 `adversarial T35`，**单次**）：SF4 第一轮「困到睁不开眼了，还要开两个小时」答「**你困成这样想让我做点啥？**」——也是澄清而非安全建议。⇒ **动手前先取证落域**（跑 `--repeat 5` 看分布 + 查 trace 的 intent），别按猜想加 route_hint | 本行；语料 `scripts/probe_qa_regression.py` 的 SF4、C1 卡见 [fix plan](docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md) |
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
| ~~**会话级数据源/降级账本**（I-033 的跨轮追问，2026-08-19 第 8 步新记）~~ **✅ 2026-08-28 随 C4 落地销账** | 账本扩「数据源」维（proto `TurnSource`，`_prov` 从「只活在卡上」变成入账）+ 编排层数据源读出口。**原判据兑现在实现里**：「这一轮用了谁、降级没降级」现在像动作一样入账，读出口只念账本、零 LLM。⚠ 留一条**边界**给接手者：读出口**只在账本非空时劫持**——空账放行给 Planner，因为 `info.stock` 有域内直答（三条读出口里只有它带这个条件）。契约 §9.34，流水 history **§74**。 | fix plan **C4** / 契约 [§9.34](docs/conventions.md)；背景：history **§63.4/§63.7** |
| **`memory_item` 的 supersede 让同一件事越记越少**（2026-08-20 person-pickup 批新记）| **启动条件：出现第二个可复现实例，或 typed Executor / 记忆质量成主要矛盾**。实据是一条 `person.child` 链四跳：「用户的女儿在**深圳市**南山实验小学上学」→「用户有孩子，孩子在上学」→「用户的女儿叫小雨」→「用户有一个女儿」（live）——**每一跳都用更少的信息取代更多的信息**，而正是它把无城市的槽值喂给了 planner。关系边侧 2026-08-16 定的是**相反**的规则（保留信息最全的那条，不是最新的，E5 那次 dry-run 教的），`memory_item` 侧**没有对应实现**：`consolidate` 只按谓词等价类比新旧。⚠ **不能直接把关系边那条规则搬过来**——偏好类记忆（「以后不吃辣了」）**就该新的赢**。判据得先分清「事实被更精确地重述」与「偏好发生了变化」，这才是这一卡的真正内容 | 卡 [`…person-pickup-resolution-card.md`](docs/design/2026-08-15-person-pickup-resolution-card.md) §6.8、history **§64.7**、`memory/store.py::consolidate` |
| ~~**多意图复合句被单域吞掉**（2026-08-20 person-pickup 批新记）~~ **✅ 2026-08-28 随 C5/C6 落地销账** | 两个面各落一件：**判**——覆盖度观测列 `clause_uncovered`（拆句 → 丢否定分句 → 肯定分句 ≥2 才有信号 → 每句问「有没有 step 的槽值落在它里面」），**零决策**，误报面写在源码里等真实分布；**做**——分句级路由锚定 `RouteHint.scope=clause`（「接X + 任意后续」不再整句落空）+ 挂起不冻结兄弟步（一个补槽问题不许劫持整轮）。**原裁定兑现在实现里**：换的是判定形态（锚定范围 / 执行调度 / 注入门控三件），一条按句形补的范例都没加。⚠ 留两条**边界**给接手者：① `guard` **不跟着 scope 走**，永远对整句求值——放宽锚定不等于放宽守卫；② 覆盖度那条**只是观测**，升级成 salvage 重试要先有两周分布。契约 §9.36，流水 history **§76**。 | fix plan **C5/C6** / 契约 [§9.36](docs/conventions.md)；背景：卡 §6.7、history **§64.6** |
| B6 §4 Capability Contract 远期字段 | ~~`input_schema`~~ **✅ 2026-08-21 落地**（消费方=商户规格值域，契约 §9.31、流水 history §65；⚠ 触发命中了但**形态与方案预想的不一样**——不是 planner 填错类型，是我们声明的值域本身是猜的）。其余**逐字段独立触发，无当前行动**：`output_schema`（槽位类型错误成稳定 badcase 族或 typed Executor 立项）、`compensation`（撤销/补偿类产品需求）、`version`/`deprecation`（第三方 Agent 生态启动）。`replayable`/`idempotency` **已由 B5 §4.2 就地收口**——不新造 `command_id`，复用 `_fingerprint` | [B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) §4 |
| **瑞幸其余规格组还没有槽**（2026-08-21 新记）| **启动条件：出现 planner 真的在产的说法**。真机台账里另有 8 组：咖啡豆 / 咖啡浓度 / 奶油 / 吸管 / 气泡 / 茶风味 / 小料 / 咖啡液（`加单份浓缩` `加奶油` 这类用户很可能会说）。本批**刻意不补**——加槽要有 planner 产出证据（B4「不加即死字段」），现在补就是凭想象扩契约。补法是**改 `servers.yaml` 一处**：槽名 + `input_schema` 条目，下单链与预览卡 chip 两处自动跟上；组名与项名台账里都有，门禁会当场校。⚠ 线索来自 `runtime/slot_fidelity.undeclared_slots` 的日志（「planner 往契约外塞了东西」），**那是补能力的信号，不是要修的错** | 契约 [§9.31](docs/conventions.md)、台账 `agents/mcp_bridge/knowledge/merchant_specs_observed.yaml` |

### 4.3 读数纪律

- **同一个值有几个出口，就在它的入口处判一次**（2026-08-29，坐标串那条的第三次）。
  「GPS 定位串念进用户话术」被修过三次都没修掉：C9-D 修了 info 的两个 handler、
  08-28 修了 road-safety 自己的兜底句、08-29 才发现真源是 `_display_city` 的
  **提前返回**——它一处喂五个 handler。**在五个调用方各补一遍守卫，
  就是在等下一个调用方漏掉。**
  三条配套教训：① **两个产出方吐同一个字符串时，光看话术永远分不出是谁**
  （所以「修完了」的判断必须落在代码路径上，不能落在「那句话不见了」上）；
  ② **窄分支要专门打**——那条路要「没点名城市 ∧ 有 GPS ∧ 下游没给话术」三个条件同时
  成立，迷你集不一定撞到，部署后我是专写三句直验才戳穿的；
  ③ 同一个坏值经过下游加工会**换个样子**（话术地名收敛器 `n[:9]` 把坐标截成
  「113.94120」），**看起来像另一个 bug**。
- **一个自洽的错误解释比没有解释更难被推翻**（2026-08-29，另一个工作线的实证 + 我自己
  同日两例）。并行会话把 `dev_stack status` 的 `release_sha:null + degraded` 归因成
  「status 工具读不到远端状态」——**它与真因（在 MSYS 里跑、ssh 吃转义）预测的现象
  一模一样**，所以自洽、也就没再往下查，还写进了实施记录（结论对、归因整条错）。
  我自己当天两例同族：把守卫报红归因成「守卫钉的是旧 release、过时了」
  （真因是我删了文档内容）、把长会话中止归因成「云端不健康」（真因同样是 shell）。
  ⇒ **加诊断提示的价值不是补上缺失的信息，是把那条自洽的错路堵死**：
  `inspect_cloud_status` 现在在 `MSYSTEM` 在场时直接说「请改用 PowerShell」。
  配套：**守卫红的时候，先证明它错了再去改它。**
  ⚠ **两条来自并行工作线的加强项**（2026-08-29，同族第三例，形态更露骨）：
  ① **自洽解释最容易出现在「我刚改过这里」的时候**——手上有个新变量，
  它天然就是个现成的嫌疑人。那一例是「判据改完真机照样成环 ⇒ 阈值还不够松」，
  而真因在完全另一处（`setTtsText` 送进去的是**空串**，判据根本没拿到输入，
  松紧怎么调都不会有变化）；**代价是会一路调阈值调下去**。
  ② **破局的通常不是想明白，是把运行时的值打出来**——那一例是加一条临时
  `console.log`。这与项目 M4 首轮那条「**「A 不工作」的结论必须先证明 A 的输入
  到达了 A**」是同一条，而当事人一小时前刚写过它、随后直接跳过去改判据了。
  ⇒ **判据写在记忆里不等于会在现场被想起来**；能拦住它的是「先取一次运行时读数」
  这个动作，不是又一条纪律。
- **判据必须红在它要抓的那件事上**（2026-08-28，第 6 批 C13-C / C12-D）。
  两条都发生在「照方案原文写判据」时：① C13-C 要「时限解析结果与原话时刻同数字时
  必须同半天」——可话术里**没有时限本身**，只能拿 `ETA ± 余量` 反推，而「凌晨5点」
  与「下午5点」反推出来的小时数**模 12 相同**，那条判据对它要抓的 bug 恰好不敏感
  （真栈那一行之所以会红，纯属 04:59 差一分钟没进位的**算术巧合**）。
  ② C12-D 要「推荐列表首位不得是忌口系（比对卡片 `tags`/`category`）」——真数据里
  首位叫「川胖虎·美蛙肥肠鱼」、`category` 是「餐饮服务;中餐厅;中餐厅」，
  **忌口信号一个都不在结构化字段里**，写出来会是一条恒绿的断言。
  ⇒ **方案给的形态在真数据上不成立时，换判据面**（改判两条分支签名），
  别把它写成一条靠巧合红、或者永远不红的断言。**靠巧合红的判据，下次就会靠巧合绿。**
- **一把尺子只有把两个立场分开表达才不会自相矛盾**（2026-08-28，第 6 批 C15）。
  「真栈不该有 mock」是**部署形态期望**，「有 mock 必须承认」是**诚实契约**
  （§9.17 的 `payment_qr` 还强制要求打 mock）——探针把它们合成一个判定，于是
  每一次 PoC 现实（manual-rag 没有真手册）都判自己红。拆成 WARN／fail 两档之后
  这 5 行才有意义。**同一份声明要能被机器按住两个消费方**：契约文档里的必带清单
  与探针的卡型表分头改就还是两把尺子，让测试**直接解析那份文档**比对。
- **不可回放 ≠ 通过；回放 ≠ 真栈复跑**（2026-08-28，第 6 批 C16 回放模式）。
  离线重算够不着的那些行（runner 自造的清理轮、要上一轮按钮才有话可说的轮），
  **原样保留原判读**——否则「回放红数」会因为尺子够不着而凭空变小（首版漏了这一层，
  当场假转绿一行）。同理，回放判的是「**尺子会怎么判当天那些话**」，
  不是「今天的系统会说什么」：**修复有没有生效只有真栈答得出**。
  副产品一条：反解替代标记（用例里的 `{run}`）要按它**真实的作用域**取——
  首版按轮反解，而标记只出现在 case 的第一轮，后面那些轮全拿到 0，
  `speech_has` 立刻整片假红。
- **接手别人的卡，先自己把机制重新证一遍**（2026-08-28，第 5 批 C9-B）。
  方案把「天气域本轮空槽 ⇒ 焦点接力清零城市」写成了真栈答错城市的机制。
  按真栈那五轮原样跑（`_apply_focus_meta` + `update_focus` 串起来），**城市一路都在**
  ——注入发生在抽取之前，同域续接轮的槽根本不是空的，那条接力条件在这条链上
  **一次都不求值**。⇒ 不做，改由「拔掉 mock 回退」兜住，并把五轮写成回归锁。
  **「方案里的机制」与「真的会发生的机制」是两个命题，第二个要自己证**；
  证不出来时**保证最坏情况**（诚实问一句）比按未经验证的机制改共享判据便宜。
- **一条读数取决于几点跑的用例，绿和红都说明不了问题**（2026-08-28，第 5 批 C13）。
  `test_navigate_to_stamps_route_session` 用真实 `time.time()` 断言「五点前要到」的
  时限在未来——**17:00 之后跑就不是**，而旧解析器正是靠「滚到次日凌晨」把它喂绿的：
  一个缺陷把一条测试养绿了三个月。修法是**冻住墙钟**，不是放宽断言。
  收尾自查加一条：本批碰过时间语义时，`grep` 一遍用真实墙钟的用例。
- **门禁读数是相对量时，红灯不一定指向你的改动**（2026-08-28，第 5 批 C14）。
  范例库跨域近重复门禁按 **IDF 加权** Dice 判，IDF 是**语料级**的：加两条 charging
  范例，把一对毫不相干的句子从 0.349 顶到 0.351。它不是「谁把冲突引进来了」，
  是全表词权变了。⇒ **加范例的成本里要算上「可能连带一次边界裁定 + 双向各 2 条对照」**。
- **反向验证要验到「哪一条断言真的被这次改动决定」**（2026-08-28，第 4 批 C5-B）。
  给「挂起不冻结兄弟步」写了四条断言，把旧语义注射回去后**一条都不红**：
  兄弟步同层、`asyncio.gather` 本来就会跑它们；更糟的是测试替身的 action 写成了 dict，
  `_to_result` 当场 AttributeError、被 `return_exceptions=True` 兜成 FAILED
  ——**FAILED 同样会拦住下游，于是四条全绿、绿的却是「步失败了」**。
  两条修法：替身按被测契约长（`.type`/`.payload`/`.require_confirm`）+ 每条显式断言
  `status`；再补一条真正被新语义决定的用例（下一层依赖的是 OK 的那一步）。
  ⚠ 这是「注入缺陷验红」那条的**下一层**：注射了、也红了，还要问一句
  **红的是不是我以为的那条路**。
- **一次失败的编辑脚本会留下半份改动**（2026-08-28，第 4 批，**自伤一次**）。
  多步编辑脚本第一步写盘成功、第二步断言失败，随后整脚本又跑了一遍
  ⇒ `engine.py` 里同一个函数被写进去**两份**。Python 后定义覆盖前定义，
  **全量 7547 零红、三道门禁全绿**，只有 `git diff` 看得出来。
  当时 `grep -c` 到 `2` 就当成「定义 + 引用」放过去了——**数出来的那个数要看清它数的是什么**。
  收尾对账因此固定加一道：对本批改过的每个 `.py` 做 AST 顶层/类内重名扫描。
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
- **登记不能是路由的副作用**（2026-08-28，QA 修复批第 1 批 C1）。告警、焦点、账本这类
  「系统必须知道的事实」，写入要挂在**输入或产出的形态**上，不能挂在「恰好走了哪条路由」上
  ——路由是有方差的，事实不能跟着抖。实证：会话告警的唯一写入通道是「某个 Agent 在 data 里
  声明保留键」，于是「红色机油灯亮了怎么办」被规划成 `warning_light.close` 的那一轮，
  **这个事实整个没进系统**，后面三轮消费的还是更早那轮留下的黄灯。
- **同一症状在多 persona 下呈现三种错法，本身就是「无确定性护栏」的读数**（同批）。
  同一句话在四个 persona 走了三条错法（执行车控 / 答手册 / 答天气）——
  **修方差先找该确定性的那一层，不是逐个 persona 调**。这条是 §4.3 既有那句
  「方差本身是能力缺席的签名」的孪生形态：那条说的是**能力缺席**，这条说的是**护栏缺席**。
- **恢复/清理链路的词表要与业务链路同源**（同批，C2）。探针的车态恢复命令
  「关闭后挡风玻璃除雾」撞上端侧词表里的一个错字（「**档**风玻璃」）才把 N1 照出来——
  **开的时候是对的、关的时候关错了另一个物理开关**。反过来说明**恢复链是一次免费的
  对抗测试**：它说的话与业务话术形态不同，覆盖面天然互补，它的失败要逐键报因。
- **「读不到」与「读到了但不对」永远分开报**（同批，C2-D，**第二次沉淀**——
  android-m3 那批在真机取证上记过一次，这次在探针上原样复发）。
  `_settled_vehicle_state` 两种失败都返回 `{}`，上层于是只会说「collector 无法回读」，
  **逐键 diff 成了死代码**，两个端侧真 bug 被这句谎话整个盖住，写进 findings 的是
  「探针基建问题：终值未知」。这次把它写进**返回类型**（`VehicleStateRead`），不是写进注释。
- **词表分支序就是语义：同族里「A 是 B 的子串」时长的那个必须在前**（同批，N1/N9）。
  两处同形态：「后**挡风玻璃**除雾」被「挡」这个短选项吃掉前缀后匹配失败；
  「**停止**播放」含「播放」二字被判成 start ⇒ **说「停止播放」把音乐放起来了**。
  加词表时先问一句「新词是不是既有词的超串」。
- **同一个 intent 有两个产出方时，门禁很可能只走了其中一条**（同批，N8——
  QA I-004 那句话的第二次兑现，这次换成了 object 名）。端侧快路径产的对象名
  `tire_pressure` 知识库不认（声明的是 `tire_pressure_monitoring`），
  **每一句「胎压是多少」都秒回「暂不支持哦」**；而 B4 门禁跑的是云侧下发那条路
  （从声明反解，结构上不可能对不上），一直绿着。
  修法是**按产出方静态盘点**（AST 取全部 `_s(...)` 的对象名），不是补语料——
  **没人给这个对象写过语料，正是它能活下来的原因**。
- **核一个关键字/取值，要核「消费它的那一层」，不是核内部模型——核错层比不核更糟**
  （2026-08-28 Android M3-5，现场在 Maestro flow）。`setAirplaneMode` 的 YAML 取值是
  小写 `enabled`/`disabled`（由 `YamlSetAirplaneModeDeserializer` **逐字匹配字面量**），
  而内部枚举叫 `AirplaneValue{Enable, Disable}`。我「对着源码核实」时核了**枚举那一层**，
  于是**把本来凭印象写对的东西改成了错的**，实跑当场 `Parsing Failed`。
  ⇒ 凭印象写完去核实是对的，但要核**消费方**；**核错层比不核更糟**——不核只是没把握，
  核错了会用「我核过源码」的确信去推翻正确的直觉，而且从此不再复查。
  与上一条是同一形态的两面：**上一条守错了产出方，这一条核错了声明层**；
  同族老账还有契约 §9.29 那条五段链（声明齐全、名字也对、照样秒回「暂不支持哦」）。
  可操作判据：**先找到「谁读这个值」的那段代码**（反序列化器 / 解析函数 / 消费点），
  再看它接受什么；内部枚举名、常量名、文档表述都只是旁证。
- **「语法核对通过」与「跑得通」之间隔着整个运行时**（同批）。三条 Maestro flow 的语法
  我逐字对过源码字段表、YAML 也解析过，**实跑第一条仍当场撞出三个问题**：MIUI 的输入法
  是独立窗口且顶到最上层（Maestro 抓到的 hierarchy 里**只有键盘**，紧接着的 `tapOn` 报
  「元素不存在」而元素其实在屏上）／上一条那个核错层／driver 每个 session 都重装。
  ⇒ **静态核对的价值是排除一类错，不是替代实跑**；把「已核实」写进交付说明时，
  要同时写清「未实跑」——两者是不同强度的证据。
- **读数的有效期只到下一次改动为止——「测完再改」和「改完再测」之间那段，数字已经过期**
  （2026-08-28 QA 修复批第 1 批，**我自己犯的，而且已经提交过一轮**）。全量跑出
  7309/+84 → 写进 AGENTS §4.0 / history / fix plan 三处 → 自审 diff 时又补了 5 条
  「安全闸误伤面」的断言 → 真值变成 7314/+89，**而三处文档一个字没动**。
  它躲过了洁癖整理的第一遍：那一遍我在核**别人**留下的过期数（语料上界、catalog 条数），
  没核**自己十分钟前刚写的**那个。
  ⇒ 两条可操作的：① **报基线的那次跑批必须是本轮最后一次改动之后**，顺序不能反；
  ② 数字落进文档时**连同「测的是哪个版本」一起落**（本仓既有做法是写 SHA，
  §4.0 上一版基线就带着 `5e764aa`——那一栏不是装饰）。
  与上面两条同族：**证据的强度取决于取证方式，证据的有效期取决于取证的版本。**
- **竞争条件类用例的「绿」不是证据——它可能只是这一趟没人跟它抢**（2026-08-28，
  两个并行会话同日各撞一次后共同定的）。`test_e2e_stack_lease` 抢 `.git/` 里那把仓库级
  OS 锁，负载低的那趟自然全绿，而**那个绿不包含任何关于它的信息**。
  同族形态还有 Android 那条「观测窗口必须短于兜底机制的到期时间」（看门狗先到，
  读数两种成因都能解释）。
  ⇒ **读数为真，但它回答的不是你以为的那个问题。** 可操作的两条：
  ① 报绿时对这类用例**单独确认**（隔离跑一遍），别拿整趟绿当它的通过证明；
  ② 写缓解措施时把「降低概率」和「修复」显式分开——§4.2 那行原来写成
  「不许两个会话同时跑全量」，读起来像充分条件，而两个会话都已经照它安排了跑批口径。
  > 📌 **本条与「核关键字要核消费它的那一层」「读数的有效期只到下一次改动为止」是
  > 同一族的三个形态**（证据形式上成立、覆盖面却比你以为的窄），**当前刻意不合并**：
  > 合并的收益是「看到一条就想起另外两条」，代价是**每条的现场被磨平**——
  > `AirplaneValue` 枚举 vs deserializer／单跑一趟也撞红／7309 vs 7314，
  > 都是读者靠关键词就能命中的现场。**合并的触发条件是出现一个实例：有人明明读过
  > 其中一条，却在另一条上原样栽了。** 在那之前合并只是为了整齐而整齐。
- **判据搬家会改变误伤代价，词表必须跟着重看一遍**（2026-08-28 QA 修复批第 2 批，C4-B）。
  Q6 的执行审计闸原本住在 chitchat 兜底位，判宽一格的代价是「答得不对」；搬到编排层
  短路位之后，同一格的代价变成**整轮不进 Planner**。原词表里「刚才那家店是**做什么的**」
  两段全中——在旧位置无人在意，在新位置就是吞掉一次正常请求。
  ⇒ **搬家不是零风险动作**：判据一个字没改，风险面却整个换了。搬完要重新枚举误伤面，
  并把新补的否决写成用例（同第 1 批 C1-A 那条「代价要主动枚举并钉成断言」）。
- **方案里的「建议项」是一个待证命题，不是待办事项**（同批，C4-D）。方案写着
  「stock 产出进候选集，让『刚才查到的行情』走既有候选消费面」——真去做才发现
  `candidate_query` 里**没有「总结」这一类算子**，加了候选集那句话照样反问代码；
  而它还有真实代价（`_CANDIDATE_SETS_MAX=3`，一次行情查询会挤掉一组真实的可选项）。
  ⇒ **「缺 X」与「补上 X 就能修」是两个命题**（§4.3 老账）在方案项上的形态：
  照着建议做之前，先把「它到底能不能闭合那张卡」当成一个要验的命题。
- **同一个语义有两种语序，词表往往只写了一种**（同批）。照着 T37「刚才实际**改了哪一条**」
  补完「动词在前」的分支，拿 T55「这五轮里**哪些执行了**」一跑就红——疑问词在动词前面。
  **是写测试时才发现的，不是读代码发现的**：两条原话都在探针语料里逐字躺着。
  ⇒ 补词表时把**同族的真实原话全部拿来跑一遍**，别只跑触发你这次改动的那一句。
- **方案里的阈值是待证参数，不是待办**（2026-08-28 QA 修复批第 3 批）。C3-A 卡上写着
  「`item_name`：长度 ≤12」，照抄会当场把**真机菜单里最长的在售商品名**
  （「马来咖喱风味薄皮肉骨鸡随心配」，14 字）判成换话题——一条本该被接住的槽位答案。
  ⇒ **凡是数字，落地前先拿真实数据量一遍**；量完把那条真数据写成用例，
  下一个人改这个数就会当场红。同族第二例：`_CANDIDATE_SETS_MAX=3`（第 2 批 C4-D）。
- **归属判定的默认值要选「代价小的那一侧」**（同批，C3/C10 共同的形态）。
  补槽的默认从「当槽值」翻成「必须像这个槽」，是因为判错的代价**不对称**：
  判成换题只是重新规划一次，判成槽值会把用户的新请求整句吃掉、还连吃好几轮。
  而任务性准入与序数参照系两条方向**相反**——那里宁可漏，因为误杀的是用户真要做的事。
  ⇒ 定默认值之前先问「判错的两个方向各赔什么」，不要问「哪个更常见」。
- **判据再准也要有一条判据之外的止损线**（同批，C3-D）。换题判据每漏一种说法就多吞一轮，
  而词表竞赛没有终点；`SLOT_RETRY_LIMIT` 换的不是「判得更准」，是「**吞不了太多轮**」。
  ⇒ 这类兜底的计数口径要认「**问的是不是同一件事**」：只按 step 计数会误伤
  「先问门店再问餐品再问数量」这种正常推进的多槽流程（它们是同一步连续三次 NEED_SLOT）。
- **零领域词的源码级断言不能裸扫源码**（同批）。第一版把整个模块源码拿去比对知识库派生
  词表，被 `volume.dec` 派生出来的 `dec` 撞红——它是形参名 `declared` 的子串。
  两处修正：**ASCII 按词边界、中文按子串**；扫描面用 `ast.unparse` **剥掉 docstring 与注释**
  ——判据的**来历**里出现领域词是正常甚至必须的（那是在讲这条判据为什么存在），
  **模式里**出现才是退化。⇒ 反向验证照做（往模式里塞一个领域词，门禁当场红）。

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
| 端侧车控能力（知识库 / 意图 / 话术）| `python test/eval_capability_integrity.py`（六维逐对象，CI blocking）+ `python scripts/check_intent_gate.py`（对抗覆盖 strict）+ `python -m pytest orchestrator/edge/tests -q`（含意图面迁移探针 **与两条 VAL 校验断言**）。⚠ **门禁覆盖不到「规则产的命令过不过得了校验」那一段**——它只走 `edge_call.decode_intent` 一个产出方且跳过 `_validate_command`；端侧快路径那条由 `test_classifier_exit_parity.py::test_fast_path_command_is_accepted_by_val` 与 `test_corpus_objects.py::test_recognized_command_is_accepted_by_val` 守（**名字对不代表命令合法**，契约 §9.29 五段链）。⚠ 那两条只走「有人写过用例的对象」，**对象名本身知识库认不认**由 2026-08-28 新增的 `orchestrator/edge/tests/test_rule_object_reachability.py` 按产出方静态盘点（§9.29 ③′）。SOP 见 §7.1 |
| 新增服务（compose 里加一个自建镜像）| `python -m pytest runtime/tests -q`——它断言每个自建服务都拿到 `DEPLOY_PROFILE`、入口够得着部署形态闸、**且该服务 Dockerfile 真的 `COPY runtime`**；**加进 `x-python-env` anchor 不等于配上了**（有服务不用那个 anchor），**源码 import 得到不等于镜像里有**（collector/proactive 就这么断过 40 小时）|
| 给某个服务的入口加共享包 import（`runtime.*` / `observability.*`）| **另外查一遍有没有独立脚本直接 import 它**——`pytest` 绿不代表裸跑绿：根 `conftest.py` 会替测试把仓库根挂进 `sys.path`，**独立脚本没有这个待遇**。2026-08-16 实测：`fast_intent` 加了 `runtime.polarity` 之后，§5「任何人接手都先做这个」的 `python test/smoke_edge.py` 直接 `ModuleNotFoundError`，而全量 pytest 一条红都没有。再查**那个服务 Dockerfile 的依赖闭包**——少一行 `COPY` 就是「一重建就起不来」，而既有容器跑着旧镜像时**完全没有症状**。`python -m pytest runtime/tests -q` 会抓 `runtime` 那一类。⚠ **把一份判据「搬家」到 `runtime/` 时（不只是新增 import），旧消费方的 Dockerfile 也要核一遍**——它们此前可能只需要 `COPY agents`（2026-08-28 `safety_signal.py` 从 `agents/_sdk` 迁入 runtime 时核过：chitchat/manual-rag/road-safety 三个镜像都已有 `COPY runtime`，**是核过不是碰巧**）|
| Planner 重试/守卫规则（B5）| **先改 `orchestrator/cloud/retry_policy.py` 的表，不要在主循环里加 `elif`**；同步方案附录 A 的清单表（`test_retry_policy.py` 逐列比对，改一处不改另一处即红）；`python -m pytest orchestrator/cloud/tests -q` |
| 中文时间词（时段/日词/中文数字/12h 修正）| **改 `runtime/cntime.py`，不要在消费方本地再写一张表**（此前三份实现给出三个答案）；`python -m pytest runtime/tests/test_cntime.py agents/_sdk/tests/test_timewindow.py agents/reminder/tests/test_timeparse.py agents/info/tests/test_weather_answer.py -q` |
| 槽值保真 / 给 `_resolve_slot_refs` 加挂点 | `python -m pytest runtime/tests/test_slot_fidelity.py orchestrator/cloud/tests/test_slot_fidelity_wiring.py -q`（同文件另有 `undeclared_slots`：**契约**比原话少一维时只观测不改值，判据零领域词）——后者含**覆盖面守卫**：每个调用点必须传 `ctx`（三条执行路径 executor / D0 / T2 共用这一个收口，少传不会报错、只会让挂点在那条路上不发生）|
| 接送人称 / 目的地接地（person-pickup）| `python -m pytest agents/navigation/tests/test_person_destination.py agents/navigation/tests/test_dest_grounding.py memory/tests/test_relation.py -q`——三处判据各自有**反向对照断言**：给了具体地点的复合句不得被改写 / 已设置的常用地点不许被人称顶掉 / 两个具名孩子两所学校仍然是「问一句」。⚠ **动 `_DEST_CATEGORY_ANCHORS` 或 `boundaries.yaml` 之后必须再跑 `python scripts/check_intent_gate.py`**：台账每加一条裁定，`validate_boundary_coverage` 就要求**双向各 2 条**对照语料，而范例门禁 `test/eval_exemplars.py` **不查这一条**——2026-08-20 就是这么绿着提交、被全量 pytest 翻出来的 |
| 省略式开关的确定性消解 / 执行事实进焦点（Q7 EL1-OR2）| `python -m pytest orchestrator/cloud/tests/test_execution_focus.py orchestrator/edge/tests/test_mixed_edge_executed.py -q`——含**接线守卫**（`build()` 真的走确定性路径且零 LLM）与**反向对照**（不该接管的句子仍走 LLM）。⚠ 判据里**不许出现任何对象词/领域词**，`fullmatch` 是安全边界不是写法习惯；动 `_FOCUSED_CONTROL_ELLIPSIS_RE` 后必须重跑 `test_actionability.py`（B6「只写不读」红线同族）。契约 `docs/conventions.md` §9.26 |
| 商户规格值域 / `input_schema` 契约（mcp-bridge）| **改 `agents/mcp_bridge/servers.yaml` 一处**（槽名 + `input_schema` 条目），**不要在 `luckin.py` 里再写一张组名表**——那张 `_SPEC_GROUPS` 是照常见叫法猜的、和真机对不上两个多月（契约 §9.31）。`python -m pytest agents/mcp_bridge/tests -q`——含 `test_merchant_spec_contract.py`（**声明 ⊆ 真机台账，单向**）与选店/选品两跳的规格保真断言。⚠ **声明新组名之前先扫台账**：`python scripts/probe_merchant_specs.py --dept <id> --write --scanned-on YYYY-MM-DD`（**必须营业时段**，打烊门店取不到 `productAttrs`）。真栈复验 `python scripts/probe_qa_regression.py --group spec`——**三轮**（查店→选门店→选商品→预览），**只跑到 `need_confirm`、绝不发确认帧**（商户写要单轮人工授权）|
| 会话内偏好/忌口（`session_constraints`）| **改 `runtime/session_constraints.py` 一处**（判据与词表的唯一实现），消费方只读不判；`python -m pytest runtime/tests/test_session_constraints.py orchestrator/cloud/tests/test_context.py orchestrator/cloud/tests/test_engine_focus.py agents/nearby/tests -q`。⚠ 新增消费 Agent 要在 manifest 声明 `context_scopes: [session_constraints]`——**它是门控通道不是敏感数据键**，不声明就收不到（契约 §9.37-A） |
| 时刻/时限语义（`timewindow` / `_deadline_note`）| `python -m pytest agents/_sdk/tests/test_timewindow.py agents/navigation/tests -q`。⚠ **同一条判据有两处副本**（`test_timewindow.py` 与 navigation 的 `test_parse_arrive_by_rules`），推翻要一起推翻；⚠ 改完 `grep -n "time.time()" agents/*/tests/*.py` 扫一遍**用真实墙钟的用例**——2026-08-28 实测有一条「五点前要到」的断言**17:00 之后跑才会红**，被旧缺陷养绿了很久 |
| 可执行性判定特征（B6 shadow）| `python -m pytest orchestrator/cloud/tests/test_actionability.py -q`（含「特征里不许有领域词汇」的知识库派生断言）+ `python test/eval_actionability.py` 看召回/假阳性两侧。⚠ 后者是**取证脚本不是准入闸**，不在 CI blocking 里 |

不要为了"让它跑起来"注释报错或加绕过标记——找根因（CLAUDE.md §6）。

---

## 7. 最常见任务：新增一个 Agent（最短路径）

1. 复制 `agents/navigation/` 结构到 `agents/<snake_name>/`（包目录 snake_case，agent_id kebab-case）。
2. 改 `manifest.yaml` 声明能力/权限/trust_level/deployment；**若 Agent 需要精确位置/电量等敏感上下文，必须声明 `context_scopes`**（`location` / `vehicle_state` / `vision`，含调子 Agent 透传的 propagator）——否则编排按最小化下发会剥掉这些键。⚠ 同一个字段还有两个**门控通道**值（不是敏感数据）：`candidates`（候选集下发，§9.28/§9.32）与 `session_constraints`（会话内忌口/偏好，§9.37）——**声明了才收得到，没有消费方就别声明**。
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
⚠ **2026-08-28 又补了一段 ③′**（QA N8）：上面那两条都只走「有人写过用例的对象」，
而 `tire_pressure` 这种**规则吐的对象名知识库压根不认**的情况两处都没条目，
于是「胎压是多少」长期答「暂不支持哦」。`test_rule_object_reachability.py` 因此
**按产出方静态盘点**（AST 取全部 `_s(...)` 的对象名），不依赖有人先想到写用例；
同族存量 4 条在该文件的 `_KNOWN_UNREACHABLE` 台账里逐条登记。
五段链全表见 `docs/conventions.md` §9.29。

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
