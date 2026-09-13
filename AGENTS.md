# AGENTS.md — 接手者入口

> 先读本文件，再动代码。工程规则最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是
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
| Android App 当前入口 | `mobile/README.md` + [`完整评审`](docs/reviews/2026-09-07-android-ux-full-review.md) + [`分批处理建议`](docs/design/2026-09-07-android-review-remediation-batches.md) + [`性能/时延评审`](docs/reviews/2026-09-12-android-performance-latency-review.md)；启动前核对工作树与代码/设备版本 |
| Android 构建与跨工具交接 | [`操作指南`](docs/guides/android-build-and-device-validation.md)：共享镜像/设备串行占用、低内存参数、长任务接续与精确验包 |
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

## 4. 当前真实状态（2026-09-11）

### 4.0 发布快照

| 项目 | 当前值 |
|---|---|
| 真栈目标 | `target=cloud` |
| 远端 main / QA 文档 HEAD | 运行 `git rev-parse origin/main`；纯 docs/test 可领先 production release |
| 生产 release | `e38cd75c88a01f46ea23ad94ce667db61ea2d0a3`（2026-09-12 00:00 apply；Android 四项体验修正 + 路线几何） |
| 上一生产基线 | `f8fd15152d78592e4e5625bab22d4bd5e654738d`；本轮未回滚 |
| status | 2026-09-12 00:01 发布后独立复核：5/5 endpoint healthy，零 warning，`release_sha` 与 `running_release_sha` 均为 `e38cd75` |
| verify | `verified`；artifact `20260911T161103Z-e38cd75.json` |
| 代码验证 | `979854a`（本批代码，`e38cd75` 只多一个 mobile 补丁 + 守卫测试）本机全量 **8243 passed / 32 skipped / 14 warnings**，0 failed（`TZ=UTC0 -n 8 --dist worksteal`，618s）；mobile **1001**（999 + 补丁守卫 2）+ tsc/lint 0，HMI **333**。smoke_edge / 四道门禁 / gateway 本批未改未重跑。详情见 [四项体验修正记录](docs/design/2026-09-11-android-sheet-controls-map-tabletop.md) §7 |
| manual-rag | 整本范围生产证据仍绑定历史 `9a3b6f2f08657464c5049a5abf8f6e989e398bce`：独立章节187/187、视觉35/35、雨刮/背宝剑各3/3。本次未重跑整本，详情见 QA 交接页 §4.6 |
| 自然问法边界 | 原36题完整真栈只在 `434a046` 闭合；`9a3b6f2f` 当轮有7条旧表述被安全预检拒绝。本次未宣称新 release 36/36 |
| 证据边界 | 当前部署/status/verify 绑定 **`e38cd75`**；真栈同题对照：`从深圳湾公园到深圳北站多远` 在 `f8fd151` 上的 `route_plan` 卡零几何，在 `e38cd75` 上带 `origin_loc` / `destination_loc` / 240 点 `path`（零动作）。OPPO 包见四项体验修正记录 §7.3。语音采纳的证据仍绑 `f8fd151` / OPPO 包 `f8fd15152`（APK SHA-256 `6fc093a9…66103`）。来源与拒识出口已贯通，但真实 MiniMax-M3 文字注入续测：正常请求6/6、背景静默拒识6/12；另有首轮播报句误命中端侧 `media.play`，已停止探针造成的模拟播放。**拒识仍未验收，声学未验**；[逐条结果](docs/reviews/2026-09-11-voice-input-acceptance-live-findings.md)。手册整本与旧 Android 验收继续保留各自历史 SHA |

`b3a2aed` 是 v2 首次生产 release；`434a046`、`7b594f37`、`805711cf` 是后续生产历史；
`a406e22` / `423ed23` 是 v1 发布历史。
当前 release 不借用 `a729b98` 的 Cloud Planner、Planner+Info 或新闻专项数字。

### 4.1 QA 状态

- 探索式 QA 的 Q1–Q13、MiniMax C1–C16、M1–M6、B1–B7 和 I-024 已完成；
- 安全专项在 `e9fa602` 上 5 例、15/15 PASS；
- 完整 information persona 在 `e9fa602` 上 57/59，提醒/导航清理、零挂起与 release 连续均证明；
- `limit:null → "None" → ValueError` 已由 `a729b98` 修复，新闻 3 个干净会话零 internal error；
- manual-rag 已在`9a3b6f2f`闭合整本范围：独立章节187/187、视觉35/35、点名泛化问法各3/3；
- **QA 仍非全绿**。剩余活项只看 QA 当前交接页 §5，不从历史批次表找。

当前主要活项：

| 活项 | 性质 | 入口 |
|---|---|---|
| 安全问句偶尔落 `info.search` | 回答安全但错域、无 manual provenance | QA 交接页 §5 |
| safety focus 持续阻断后续 charging plan | 安全状态解除时机的产品裁决 | QA 交接页 §5 |
| MiniMax TTS RPM / barge-in 残帧 | 外部配额与协议/客户端边界 | QA 交接页 §5 |
| gRPC RuntimeWarning | test-only fixture 债务 | QA 交接页 §5 |

### 4.2 当前活项与其他可接工作

| 主题 | 启动条件 / 入口 |
|---|---|
| Android App | 进行中：[AR05 实施方案](docs/design/2026-09-09-ar05-structured-contracts-implementation-plan.md)（步骤 0–6 已实施并发布 `d425b9c`：T0 授权闸、Registry 声明往返、四份契约端云贯通、F09 窄修复、会话摘要、Android 消费面、网关编译与测试、真栈契约证据与 OPPO 固定包 `d425b9c2d`；**步骤 7（2026-09-10）只补验证、零代码改动**，见方案 §12.4。**仍未签收**，逐条见 [§9.4 签收条件](docs/design/2026-09-09-ar05-structured-contracts-implementation-plan.md) 与 §9.5 V01–V12 逐格表：**全格闭合 3（V01/V04/V11）、部分 8、仅离线 1**（前一轮是 0 格全闭合）。剩余阻塞收敛成三个前提，都要人工授权或真人参与——① **一个无 `vehicle.control` 的受限 token**（卡 V05 负例、V07 的 `permission.scope_missing`、V02 的「新身份不继承」；云端 `AUTH_TOKENS` 只有一条全授权条目，改根 `.env` 是红线）；② **一个旧 release 的部署环境 + 一次 Redis 重启**（卡 V06 挂起恢复保真、V10 的新客户端+旧服务端）；③ **真人听音**（卡 V08）。原「缺中文输入通道」这条阻塞**对卡片渲染取证不再成立**：纯 ASCII 英文语料在真栈上走同一条确认/补槽闭环（与中文语料逐字同构），`adb shell input text` 即可注入，确认卡/补槽卡/issue 卡三种渲染都已在 `d425b9c2d` 固定包上取到。⚠ 这不等于中文输入不再需要——卡片渲染不经过按语种分支的代码，但 ASR/NLU/IME 与任何把中文文本当被测对象的流程仍需真正的中文通道（AR06 A06-3/V04 照旧），且英文语料确实会改变**路由**（`navigate to Hangzhou` 绕过了模糊目的地澄清）；AR04 的服务端多 operationId 实机组合也已闭合（真机「待处理事项（3）」三条真实 operationId 并存、取消末项不串账）。Planner 技术失败降级已在 AR05 步骤 2–4 修复并取到真栈实例与真机 issue 卡，但触发率 2/7 不可稳定复现，V09 不计闭合。实现与设备基线：[AR04 第十五节](docs/design/2026-09-08-ar04-presentation-ack-implementation.md) + `mobile/README.md`。Android 代码 `1c67807`：支持页浮动在场、闲置光球静帧、滚动内容留余量；OPPO 两轮证据与资源恢复见 AR04。AR02/AR03/AR10 与 Xiaomi 对照范围独立。 |
| Android App AR06～AR11 方案 | [接续路线与六份独立方案](docs/design/2026-09-09-android-ar06-ar11-execution-roadmap.md)：AR06 release/lint、AR07 KWS A/B、AR08 首音、AR09 UI 性能/观测、AR10 固定包 UX 验收、AR11 Android M5 交付规划。2026-09-09 已落盘，均为草案、未实施；附逐批 Goal 文本、前序未签收项与完成边界。2026-09-10 补[工程交付优先与集中验收安排](docs/design/2026-09-10-android-goal-delivery-and-acceptance-plan.md)，默认不让人工签收阻塞独立工程任务。**2026-09-10 工程交付批已实施**（`7481cb5`→`c5b2c9b`）：[AR06～AR09 实施记录](docs/design/2026-09-10-ar06-ar09-engineering-implementation.md)——AR06 的 lint 门禁（0/0，反向验证会红，**CI 已实跑通过**）、e2e dev/release 分轨、中文输入在 prod 固定包上闭合（推翻「CLI 未装」的旧判断）、验包（APK SHA-256 端本一致、非 DEBUGGABLE）；AR07/08/09 交付的是**仪器**（KWS 单变量入口与四分栏计数、轮次时间线与 nearest-rank 统计、只读有界诊断页），**唤醒率与首音仍然没有数字**；UI 性能已取 P0/P3/P5 首批读数。⚠ 其中的 **F1「内存泄漏、超门槛 55×」当日晚被单变量实验推翻**：那是取证装置自己的走法（深链一路前进、从不返回 ⇒ 30 轮后 120 个屏活在栈里），换成用户真实路径（进页面后按返回键）后 Views 静置回到冷基线 151、PSS 回到 256MB，**屏是会被正确释放的**；F2 的 99% 卡顿随之改写为「那个状态正常导航到不了」。逐条更正与真缺陷见下一行的余项收口。附录记两件真栈结果：受限 token 已加并可用（`edge-vehicle` 等 14/17 `scope_missing`）；受限身份端到端**安全面成立而解释面不成立**（6/6 零动作，但话术 6 种、有一条谎称已执行、0/6 提权限）——归 AR05 未修。另附[AR10 验收准备材料](docs/design/2026-09-10-ar10-acceptance-preparation.md)（入场条件/冻结表/五人脚本/计分/报告骨架，零参与者、零结论）与[AM5 交付计划](docs/design/2026-09-10-android-m5-delivery-plan.md)（五包可派工任务、DoD 与工作量依据；零生产能力实现）。⚠ 同日一次由本会话造成的生产回退（手动 compose 漏 `RELEASE_SHA` ⇒ edge-gateway 被换成 8 月镜像、`/api/session` 404 数分钟）已恢复并逐条记录；**`status` 5/5 全程没发现它**，验证面缺「运行镜像 tag == current SHA」的对账。 |
| Android App 页面打磨批 A→G | [2026-09-10 打磨批计划 + 回填](docs/design/2026-09-10-android-ui-polish-batches.md)（评审出处 [2026-09-10 打磨评审](docs/reviews/2026-09-10-android-ui-polish-review.md)）：A 对话页信息层级（+D 启动图、J2 首页示例）→ E 删 v1 回滚路径 → B 设置页五组 + 开发者选项（prod 隐藏、构建行连点 7 次）→ C 车辆页中文化 + 线性图标 + 最小字号 → F 聊天基线（持久化 50 条、时间分隔、回到最新、重发）→ G 承诺卡人话摘要（云侧取 Registry 能力描述，`trunk.open` → 「打开后备箱」）。提交链 `0aee251`→`bd71f89` 共 10 个，**已 push**；批 G **已 deploy `d532c6d`** 并真栈复验。真机三包：`7525784b6`（批 A，12 态）/ `7fc8d9894`（最终包，12/12 态 + 冷启动恢复）/ `e95d07725`（F 追加：FlashList v2 只在 data 变化时跟底，历史一恢复列表变长就把晚到的卡片 / 键盘下的新气泡压在 Composer 下 ⇒ `stickToBottom` 一处判据；Maestro 01/08 由红转绿）。Maestro 追加包 8 条 = 6 ✅ + 09 ❌（driver 在动画页检出问题，失败截图里目标已在屏）+ 03 ⛔（飞行模式段 driver 挂起，机制未钉死，见文档「批 F 追加」未达项 ③ 与集中处理表）。Xiaomi 对照四格 4/4 已在 `e95d07725` 上截到（MIX Fold 4 外屏 360×840dp / 横 840×360dp；横屏行车档下 Dock 落在 Composer 之下）。**未闭合**：「草稿为空 ⇒ 在听…」占位格、三条待办并存截图、「重发」真机截图（断网路径到不了：uncertain ∧ pending 没有出口，下一批裁决）、Maestro 03 的 driver 挂起（工具侧）。 |
| Android App AR01～AR11 余项收口 | [2026-09-10 余项收口](docs/design/2026-09-10-ar-residuals-closeout.md)（`ea509ff`→`046fb5c`，**未推送、未 deploy**）：四件工程正题。① **推翻上一轮的 F1**——「30 次路由循环内存不回落、超门槛 55×」是探针只推不弹造成的，`Views` 那一列一眼可辨（151→7651、每轮 +500、`Activities` 恒 1）；加上返回键后静置逐字回到 151。真缺陷是**外部深链只推不弹且用户可达**：桌面 Shortcut「说话」点 20 次 ⇒ Views 151→3483、PSS 207→407MB，用户无法自救。修法＝`+native-intent` 把深链规范成「目的地」（voice 变成对话页参数、进目的地前先回栈底、只接管五个真入口、认不出的原样交回），并同时修掉它会引入的回归（voice 参数改为消费参数本身而非组件实例 ref）。② **AR05 解释面**：受限身份 6 次 6 种说法、一条谎称已执行 ⇒ 过滤 catalog 时把被挡下的那半留作理由，命中即出确定性 `permission.scope_missing`（**一次 LLM 都不调**，恢复出口指能力设置不指系统权限页）；路由复用 Registry top-1 与既有 `CLARIFY_FALLBACK_MIN`，不加新词表新阈值，分不清就退回今天的行为。③ **`status` 运行镜像对账**：健康检查答不了「活着的是哪一份代码」——远端 preflight 现在报运行容器的镜像 tag，不一致／彼此不一致／读不到三种分开报，任一 ⇒ degraded。④ p3 探针改走用户的路（`--nav back` 为默认），内存读数带上对象计数。⑤ **E-02 闭合**：Dock 前提两趟真栈跑通（`uxV2Dock` 回读 → 流 02 与流 06 各 RC=0，两条流都走取消、零车控执行）。四处判据均双向反向验证判红。**真机证据**绑固定包 `1f241c8c5`（APK SHA-256 `4c2c16bd…6985`，设备侧回读逐字节相同、非 DEBUGGABLE）：深链三臂 A 3483→**183** views、B 2490→**278**，而**故意不接管**的对照臂 C 修复前后都是 **16911**（逐字相同 ⇒ 装置照旧测得出「涨」）；AR09 P3 修正协议下 Views 151→**151**，第 1 趟（冷起）+88.7MB、第 2 趟（暖起）**+1.0MB** ⇒ 那 88.7MB 是首次触达成本不是累积，帧 4.10%/3.71% janky、p50 17/16ms。**本机全量固定口径 8231 passed / 32 skipped / 0 failed**（上一基线 8205/32）；mobile 876 + tsc/lint 0、hmi 333、`go build/vet/test ./gateway/...` 全绿、四道门禁 + smoke_edge 13 全过。⚠ 顺带定死一条操作判据：**Maestro 的 driver 与任何走 `uiautomator` 的工具互斥**（driver 不停就报 `UiAutomationService already registered!`，症状是整机 dump 全被 SIGKILL，看着像被测对象坏了）。⑥ **AR05 V05 / E-06 车控端到端负例闭合**（用户单独授权该用例）：受限身份发两种车控语料——`打开后备箱`（需确认）被**云侧新判据**拦下且 `confirm_policy`/`operation_id` 根本没生成（scope 闸先于确认闸），`打开空调26度`（无需确认、闸失效即当场执行）被**端侧 T0 闸**拦下，两次车态读数逐字段相同、零执行零残留。**②③ 已 deploy 并真栈复验**（生产 release 已从 `d425b9c` 推进到 `74852a7`，push/dry-run/apply 逐步单独授权，含 CI/CD digest；该次发布同时把上一轮已 push 未部署的 AR06～AR09 整批带上生产）：受限身份同题六次 **6/6 逐字相同**、6/6 `permission.scope_missing` 点名「导航助手」、恢复出口指能力设置（此前是 6 次 6 种、含一条谎称已执行、0/6 提原因）；`status` 新列 `running_release_sha` 与 `release_sha` 两次读数均一致，并在一次 apply 报 runtime 失败时**当场回答了「生产有没有被动过」**。**未闭合**：E-03 唤醒率（本轮未安排）／E-04 首音（缺外部时基与真人）、E-08 五人 UX（缺参与者）、E-07 旧服务端+Redis 重启、P1/P2/P4 与系统级动画缩放那一臂；AR05 的 V06/V08/V09/V10 照旧未闭合。 |
| Android 四项体验修正（2026-09-11，用户口述） | [设计与实施记录](docs/design/2026-09-11-android-sheet-controls-map-tabletop.md)：① 语音层整层任意位置下滑收起（滚动区在顶部才接管、横滑不吃、跟手 + 快甩）+ 泊车路径补内容下限（矮容器不再裁球，主力机读数逐 dp 不变）；② 可点控件两档制——按钮 = `TARGET`（48/56），胶囊/chip = `PILL`（36/44）经 `ui/Pill`，外框仍撑到触控目标；③ 地图路线——navigation / charging_planner 的 `route_plan` / `charging_route` 卡带 `origin_loc` / `destination_loc` / 途经点坐标 / 抽样折线（`hmi/src/types.ts` 加可选字段），客户端 `core/map/geometry.ts` 一份判据、地图页（高德 SDK）画折线与角色标注、舞台内嵌地图；④ 桌面姿态上半横排。本地：mobile jest / tsc / lint、navigation + charging 单测、hmi 333 全绿（计数见记录 §7）。**已 push（`979854a` + `e38cd75`）、已 deploy `e38cd75`（dry-run 零阻断 → apply submitted → status 5/5 healthy、running SHA 对齐 → verify verified）**，真栈同题对照证明 `route_plan` 卡从零几何变成带起终点坐标 + 240 点折线。首个候选包在 OPPO 上抓到一个真缺陷：Marker 自定义标注触发库的 `update` 命令、Fabric 互操作层把缺席的 args 当 null ⇒ 整个 App 退到桌面；修法是 patch-package 让命令永远带数组（`e38cd75`）。**未闭合**：折叠机桌面姿态横排要 Xiaomi 真机；整层下滑的手感只在 OPPO 上验过一次（深链升层 → 内容区下滑 → 层收起）。 |
| Android 语音输入采纳 | `f8fd151` 已推送、部署、verified；OPPO 同 SHA 包。输入来源与拒识终态已修，**端侧新闻快捷规则误判、Planner no-action/技术失败漏拒仍仅记录未修**。真实文字续测12/18符合预期（背景6/12、正常6/6），声学未验；[当前核实与后续范围](docs/reviews/2026-09-11-voice-input-acceptance-live-findings.md) |
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
- `AGENTS.md` 是规则与当前入口，不是变更日志；逐批过程写 `docs/agents-history.md`。
- 已完成的 implementation plan 保留作实施证据，但不作为“下一步”入口。
- 文档中的“今天/最近”只用于引用原始用户话术；状态一律写绝对日期。
