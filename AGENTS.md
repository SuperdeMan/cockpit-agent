# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

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

任何真栈动作（包括运行 E2E、Compose 或 `make up`）前，必须先读取 `dev-stack.local`：文件
缺失时按 `target=local` 处理，文件损坏则 fail closed。改动任何脚本目录或 manifest 前同样
必须读取该文件。该文件只允许 `target=local|cloud`，不得保存
token、密码、私钥或 URL。`target=cloud` 时禁止启动本地 Compose；本地只承载编辑、单测、
静态检查和 Vite。`target=local` 时继续只用根 `compose.yaml` / `make up`，根 `.env` 仍是唯一
运行时来源。

cloud deploy 只接受干净、已提交、main 可达的 SHA，不自动 commit、merge 或 push；未显式
标记 `remote_safe` 的 E2E 不得在 cloud 缺省运行，高影响开关也不替代人工红线授权。本阶段
不得自动写入 `target=cloud`，也不得停止另一个 agent 正在使用的本地 Docker。

1. **车控只经 VAL**。任何组件（含 LLM/Agent）不得直接碰 CAN/SOME-IP。
2. **LLM 不直连车控**：LLM 只产"意图/计划"，车控由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
3. **危险动作二次确认**（`require_confirm=true`）。
4. **不改编排核心来加 Agent**：Agent 经注册中心被发现，新增 Agent 不动 orchestrator。
5. **密钥/token 不进代码、不进 commit、不进日志**；用 `.env`（已 gitignore）。
6. **改 proto 先改 `proto/` 再 codegen**，不要手改生成代码。

---

## 4. ⚠️ 当前真实状态（别假设没验证的东西能跑）

### 4.0 当前快照（2026-08-15）

意图落域对抗测试按这个顺序接手：运行手册
[`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md) → 最终验收
[`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`](docs/reviews/2026-08-04-review-intent-adversarial-finalization.md)
→ 逐批证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§26**。
历史流水只查 [`docs/agents-history.md`](docs/agents-history.md)，不要再抄回本文件。

**最新后端全量基线**：`python -m pytest --import-mode=importlib`
**6257 passed / 15 skipped 零红**（2026-08-17 凌晨，QA 批 **Q7 残余（EL1+OR2）**后实测，
SHA `c1620d9` + 本批工作树）。分两段：`--ignore=scripts/tests` **5476 / 10**
+ `scripts/tests` **781 / 5**。较上一跳 6198 净 **+59**，**两条来源逐条点得上号**：
- **本轮产出 +55**：段 1 **+52**（`orchestrator/cloud/tests/test_execution_focus.py` **47**
  + `orchestrator/edge/tests/test_mixed_edge_executed.py` **5**）
  + 段 2 **+3**（`scripts/tests/test_probe_qa_regression.py`，探针尺子的单测）；
  证据 history **§53**。⚠ `test_actionability.py` 只改了判据形态、**没加用例**（仍 14 条）。
- **+4 来自云发布工作线 `ea5947c`**（段 2，**非本轮产出**）。
  ⚠ 这个数是**可复现对照**量出来的，不是猜：`ea5947c` 的临时 worktree 里
  `scripts/tests` 收 **783**、当前工作树收 **786**，差额 **+3** 正是本轮那个新文件。
  **段 1 不能这么比**——worktree 缺 codegen（3852 collected + **104 errors**）而主仓完整
  （5486），**分母破得不一样就不可比**；段 2 可比是因为它不依赖 codegen。
  段 1 因此用逐条点号（47+5=52），与实测 5424→5476 逐字对上。

⚠ 本轮期间 HEAD 被并行工作线推进过两次（`3fd121a` 见 history §53.7、
`c1620d9` 是云数据迁移/远程开发栈的**纯文档**提交，不碰代码）——
**「我没动过 git」不等于「HEAD 没动过」，这已经是本项目第二次记它**。

<details><summary>上一跳 6198 / 15 的记账（2026-08-16，QA 批 Q12，SHA `dcd9182`）</summary>

较其上一跳 6127 净 **+71 passed / −9 skipped**，
⚠ **三条来源，逐条点得上号**：
- **QA Q12 +39**（本轮产出，全在段 1）：`runtime/tests/test_cntime.py` 9 +
  `runtime/tests/test_slot_fidelity.py` 17 + `orchestrator/cloud/tests/test_slot_fidelity_wiring.py` 6
  + reminder `test_agent.py` 3 + reminder `test_timeparse.py` 2 + `_sdk/test_timewindow.py` 1
  + info `test_weather_answer.py` 1；证据 history **§52**。
- **+23 来自云发布工作线**（`afff49d`/`bf92c22` 的 22 条在
  `scripts/tests/test_cloud_release.py`，收尾合并 `e5d06a9` 再 +1，**均非本轮产出**；
  本地 main 在我开工前后 fast-forward 过，session 快照里的 `dc856ce` 已不是 HEAD）。
  ⚠ 攻击这个数用的是**收集数对照**而不是猜：`scripts/tests` 在 `dc856ce` 收 **756**、
  在 `bf92c22` 收 **778**，而段 1 的收集数两个 SHA **逐字相同**（worktree 里都缺 codegen，
  3817+104errors 对 3817+104errors——**分母一样破，对照就仍然成立**）。
- **+9 是环境不是代码**（原 skip 现 pass，段 2）：`scripts/tests` 有一批按
  「symlink/junction 能不能建 / PowerShell 在不在 / docker 在不在」跳过的用例。
  本次段 2 从 **PowerShell 起、PATH 全**，它们真的跑了。
  **跨环境比 skip 数没有意义，比 passed 数也要先对齐 skip。**

⚠ 分两段：`--ignore=scripts/tests` **5424 / 10** + `scripts/tests` **774 / 5**
（**必须先 `Remove-Item Env:\PYTHONIOENCODING`**）= **6198 / 15**。
合并 `e5d06a9` 后**整套重跑过一次**：段 1 与合并前**逐字相同**（5424/10），
差额全在段 2（773→774）——与上一次合入同一条工作线时的观察一致。

⚠ **这个数取完之后，云发布工作线又进来一笔 `ea5947c`**（cloud verification 事务化，
`scripts/tests/test_cloud_deploy_assets.py` +202 行）。**段 2 的收集数已从 778 变 783
（+5），本节的 6198 是 `ea5947c` 之前的读数、没有重跑**。
下一批开工前先把这 5 条点掉再谈自己的净增量——**同一条工作线本轮已经这样插进来三次**
（`afff49d`/`bf92c22` +22、`e5d06a9` +1、`ea5947c` +5），而三次我都没执行过 git。
> ✅ **这一笔已由 Q7 残余批点掉，实测是 +4 不是 +5**。推导链：本跳段 2 实跑
> 781+5=**786 collected**，减去本轮新增的 3 条 = **783**，与 `ea5947c` worktree 的
> `--co` 逐字相同；而上一跳实跑 774+5=**779**。⇒ `ea5947c` 带入 **+4 collected**，
> 且两跳 skip 数同为 5 ⇒ **这 +4 全是 passed**。
> 上面那个「778」是 `bf92c22` **worktree 里的 `--co`** 读数，与实跑口径差 1
> ——**收集数与 passed+skip 是两个口径，跨口径相减会凭空造出一条不明用例**。

</details>

前一跳 **6127 / 24**（2026-08-16 Q5 残余后，`dc856ce`）也是两条来源：
QA Q5 残余 +15（`agents/chitchat/tests/test_memory_provenance.py`，history **§51**）
+ `f5099f9` 带入 2。分段读数 5385/10 + 742/14。

前一跳 **6110 / 24**（QA Q6 + 云发布工作线合入）同样是两条来源：
QA Q6 净 **+25**（`memory/tests/test_executed_actions.py` 4 +
`agents/chitchat/tests/test_audit_answer.py` 21，证据 history **§50**；
合并前单独实测过 **5999 / 14** 对 `--co` 6013，那是相对 Q10 的 5974 的干净读数），
云发布批带入 **+111 passed / +10 skipped**。合并走 merge 不走 rebase，无冲突、零文件重叠；
**合并后仍重跑过一次**——「零文件重叠」不等于「零交互」（conftest / 共享 fixture），
实测段 1 与合并前逐字相同，差额全在段 2。

⚠ **仍是分两段跑的合计，不是一趟根跑**（原因见下）。
前一跳的分段读数 5385/10 + 742/14 = 6127/24，与当时 `--co` 的 **6151 collected** 对上。
前一跳 5974（+45，Q10）：新文件 `test_order_session_scope.py` 45 条；
同样分两段（5345/10 + 629/4，`--co` 5988）；证据 history **§49**。
⚠ **分段的原因是后台全量连续两次在同一位置（76%）被中止**，而 76% 正是
`scripts/tests` 那族拉真子进程的起点（与既有的「scripts/tests 要隔离复跑」同源）。
两段合起来分母等于全集，但**段 1 的 import 顺序与一趟根跑不同**（少了 scripts/tests
的 conftest），下次能一趟跑完时应重取一次。
前一跳 5929（+4，Q11）：reminder 160→**164**，证据 history **§47**。
前一跳 5925（+24，Q5）：memory 229→**250**、reminder 157→**160**；证据 history **§46**。前一跳 5901（+38，Q7）：`test_polarity.py` 26 +
`test_segment_backfill.py` 11 + `test_pending_cancel.py` 1；证据 history **§45**。
前一跳 5863（+27，Q2）：`test_candidate_sets.py` 21 + nearby 6；证据 history **§44**。前一跳 5836（+97，Q13）：`test_classifier_exit_parity.py` 93 +
dashcam 两行金标各占 golden/分段两个参数化 4；证据 history **§43**。
⚠ **首趟 192 failed 是我自己造的假红**——`Start-Process` 起的子进程没挂 PATH，
逐字命中下方那条已记录的签名。**记过的坑会换个方式再踩**。
前一跳 5739（+33，QA 阶段 2）：`test_pending_cancel.py` 11 +
`test_pending_operation_id.py` 7 + `test_pending_table.py` 12 + `test_engine_confirm.py` 3；
证据 history **§42**。⚠ 那一节初稿写过一个**没测就写的** 5745——基线数只能来自真实跑批。
前一跳 5706（+38，QA 阶段 1）逐条点号：`agents/_sdk` 7（新增 `test_safety_signal.py`
——**不该命中的用例占一半**）+ manual-rag 9 + road-safety 7 + chitchat 5 + parking-payment 3
+ `orchestrator/cloud` 7（新增 `test_safety_focus.py`）；证据 history **§41**。
⚠ **35m23s 不是回归变慢**：上一轮那 192 条假红是**快速失败**，这轮它们真的要跑
（`scripts/tests/test_run_e2e.py` 拉真子进程），且本机 30 个容器在占 CPU。
⚠ **这轮先踩了一次 §4.0 已经记过的坑**：用绝对路径 `& $py` 跑（`python` 不在 PATH）
→ **192 条假红**，数字逐字对上下方那条警告。**红了先问是不是前提变了**。
前一跳 5668（+9，双档复跑修复）：时区族 3（业务时区锚定 1 + 两条源码级守卫）+ nearby 6
（当轮忌口压过记忆 1 + 忌辣说法覆盖 1 + 停车插入语 1 + 时段词 1 + 约束词正反两向 2）；
证据 history **§40**。前一跳 5659（+46，EVA 余项 E1–E5）逐条点号：`agents/_sdk` 8（新增 `test_timewindow.py`：
时刻消歧 1+事件时刻 3+用餐窗 3+时钟换算 1）+ nearby 19（E5 并集召回 4+E1 用餐窗 5
+E2 属性维 7+假个性化守卫 2+类目别名话术 1）+ trip 14（E3 归城校正 7+城市序守卫 3
+天数守卫 2+顺延跨城 2）+ memory 5（谓词归一族）；证据 history **§39**。
前五跳：5613（+39，遗留六卡）见 **§38**、5574（+13，LLM_BACKUP/方向词/轨迹候选/
复合取消）见 **§37**、5561
（+41=基线陈旧 3+P3 簇 38）见 **§36**、5518 见 **§34**。⚠ 后台跑全量期间不许动
工作树/测试锚（本批首趟在 catalog 锚更新前启动、会假红一条，停掉重跑才是干净读数
——live 跑批纪律的全量版）。⚠ 本次实测 scripts/tests/test_e2e_stack_lease.py 12 条红
=与并行 journeys 的 **stack lease 冲突假红**（那些测试模拟 runner lease 树而
journeys 真持有 lease），隔离复跑 61/61+2skip 全绿——与既有「并行 Docker build 时
test_e2e_wrappers_ci 假红（隔离复跑 6/6）」同族：**全量要单独跑，不与任何 e2e/build
并行**。前五跳：5471→5500 见 §33、5464→5471 见 §32.1/§32.2、5457→5464 见 §32、
5440→5457 见 §31、5408→5440 见 §30。
⚠ Windows GBK 宿主是本仓常驻放大器，新写子进程/出站验签代码先想编码两端；
本机会话若带 `PYTHONIOENCODING`，scripts/tests 的拉子进程用例会 188 条假红
（§4.3 既有纪律，2026-08-13 又实测一次——全量必须在干净 env 跑）。
⚠ 同族第二形态（2026-08-14 实测烧掉一次全量）：**`python` 不在 PATH 的 shell 里跑
全量会 192 条假红**——e2e manifest 校验要求 `python` 可执行存在（`case
e2e_protocol_smoke.command executable does not exist: python`），scripts/tests 整族
连坐；判据同款「红了先问是不是前提变了」，把解释器目录挂上 PATH 后同批 293 条全绿。

**2026-08-15 EVA 主题四批（能力状态，流水只查 history §36–§40）**：
① P3 簇三 RFC（history **§36**）——G8 导航路线会话 + `navigation.reroute` 增量改道、
G4 主题行程检索步、G9 trip 跨城市（`Trip.cities` + 跨天衔接 leg）、G7 陈述 vs 请求入台账。
② 指令集全量真栈验证（报告
[`…eva-instruction-set-e2e-verification.md`](docs/reviews/2026-08-15-eva-instruction-set-e2e-verification.md)、
history **§37/§38**）——25 语料首轮 ✅12/⚠️10/🔒3，遗留六卡 P1–P6 收口后
**✅17/⚠️5/🔒3**；同批建了跨厂商备份档 `LLM_BACKUP=deepseek:deepseek-v4-flash`。
③ 余项立卡 E1–E5（卡
[`…eva-backlog-cards-e1-e5.md`](docs/design/2026-08-15-eva-backlog-cards-e1-e5.md)、
history **§39**）——事件时刻反推用餐窗 / 停车便利·无障碍·不排队属性维 / 多城确定性归城校正 +
主题接地读数 / 方差面只读探针 / 谓词别名老账（消费面两路并集 + 存量 18 行归一）。
④ 双档复跑修复（报告
[`…eva-dual-provider-rerun-and-fixes.md`](docs/reviews/2026-08-15-eva-dual-provider-rerun-and-fixes.md)、
history **§40**）——25 语料 × MiniMax/DeepSeek 各 27 轮，据「两档是否同时错」定位并修掉
**容器时区族**（所有「几点」判定偏 8 小时，8 处；唯一实现下沉 `runtime/clock.py` + 两条源码级守卫）、
**当轮忌口被记忆压过**、**约束词被当检索词**，外加画像 `place.company` 存量污染复位。
修复后 25 条重跑一遍取干净读数：**✅18 / ⚠️7 / 🔒3 面**（报告 §6）。
⚠ **这个数不能与六卡批的 ✅17/⚠️5 直接相减**——两轮是不同时点的独立采样，差额大头是
已知方差面翻面（人称解析在 DeepSeek 同轮是对的、主题标注本轮零候选走诚实降级）；
**能确定的结构性变化只有三条**：三§1#3 ⚠️→✅（用餐窗+忌口+停车三件齐）、六#6 ⚠️→✅、
时间族全线正确（「6点前」现在解析成 18:00 而不是 06:00）。本轮仍 ⚠️ 的七条逐条有归属，
**无新缺陷**（人称卡 2 条 / 主题方差 2 条 / 车控 🔒 2 条 / 南京池被限流 1 条）。

当前对标状态一句话：时间约束（到达时限 + 事件反推用餐窗）、真沿途、多城保序与归城校正、
模糊目的地推断、记忆六维消费（subject/polarity/轨迹/路线偏好/口味/无障碍）均**真栈在案**；
对抗语料 **591 唯一输入**（上界 591，余量 0）、catalog **144 条**、架构 **v1.29**
（该行的 v1.26 是 EVA 批时点读数，2026-08-16 随 QA 轮 Q6/Q10/Q5 与 Q12 两跳补齐、
2026-08-17 随 Q7 残余到 v1.29——新增架构 **§5.2.4 省略式开关的确定性消解**）。
**仍未做**：G10 订座票务（搁置，诚实桩）、复合句人称接送解析（§4.1 ②）、
**探索式 QA 轮的 Q8 与三处卡内残余**（Q2 的确定性消费方、Q10 的双入口收敛、
Q12 的规格维——**后两处都卡在 Q2 那一处**；阶段 0/1/2 与
Q13/Q2/**Q7 全维度**/Q5/Q11/Q10/Q6/Q12 主体、**存量数据清洗**已完成，
逐卡状态见 §4.1 ①、流水 history **§41–§53**、归档索引 **§47.5**）。
⚠ QA 那一轮**验的是别的东西**：EVA 四批验「能力面有没有那个维度」，QA 轮验
**会话状态、归属、审计与真实性**——所以它抓到的三个 P0 与 44 个 P1 与上面那些读数不矛盾，
两者测的不是同一个面。

**历史批次已归档，索引在 history §38.1 与 §40.1**（两次洁癖整理）：前者收 EVA 二轮五批
（§33/§33.1）、商户链路收口三段（§30–§32.2；2026-08-12 商户批在 §29/§30）、B5/B6 后
分组实测与真栈演练（§27/§28）；后者收上方四批的完整叙述与 §4.1 的五段完成态
（journeys 两红 §35 / 接地卡 R1 二期 §34 / EVA 二轮六批 §33 / 支付四批 §28 /
商户七批 §30–§32.2）。**归档是把叙述换成索引，不是再抄一遍**——段内 catalog/分组
数字均为批次时点读数，当前数以上方基线段与下表为准。⚠ 从演练段沉淀并仍有效的两条：
`--lane ci --full` 只选 1 个用例（引用它的 exit 0 别当 e2e 全绿）；
`test/eval_actionability.py` 是取证脚本不是准入闸（不进 CI blocking）。

**CI 已有四条 blocking 门禁**：skills 契约、范例契约、L0 对抗 strict
（`scripts/check_intent_gate.py`，B2）、**能力完整性**（`test/eval_capability_integrity.py`，
B4）。四条都是确定性检查、零 LLM、零网络——「跌破基线告警不阻塞」的哲学只适用于会随语料
漂移的意图基线，与这四条准入不同，不要合并。

| 意图落域证据 | 当前可引用事实 |
|---|---|
| L0 discovery | **81/81**，635 条 / **596 唯一输入**（2026-08-15 起 bounds [450,**596**]，仍**恰好用满**。十次递进 560→…→584→586→587→591→596，逐次占用理由写在 `suites.yaml` 头部——最近两跳：G7 陈述 vs 请求边界双向；**卡 Q9 新增 active intent `safety.driver_state`**（正 2 疲劳/饮酒 + 硬负 2 环境侧 + 对照 1，全部 `reviewed` 不进 gate 池）。没有删除旧尺子压数字） |
| gate 规模 | **139 stable / 129 唯一输入**，L0 strict **25/25，exit 0** |
| 对比模型正式 baseline | [`baseline_intent_adversarial.json`](docs/reviews/eval/baseline_intent_adversarial.json)；干净 `f0af9c0`，锁定 `deepseek:deepseek-v4-flash`，由当前 L3 原始字节/摘要/时间/精确路径契约重新取证并写入。**未随 `32e8718` 重取**——它仍是 DeepSeek 在 `f0af9c0` 的证据 |
| DeepSeek 完整 gate | **147/147**：L0 25、L1 117、L2 4、L3 1；exact **121/121**，raw 幻觉/校验后逃逸/不稳定均 **0/121**；L1/L2 各 **2 个独立进程 × 每进程 3 样本**（`f0af9c0`） |
| MiniMax 主模型 gate（`32e8718`，2026-08-10） | **141/147**；exact **116/121**、required **99/103**；raw 幻觉 **3/121**（原 8）、逃逸 **0/121**；不稳定 **4/121**（原 6）；`pass 141 / unstable 4 / stable_fail 2`，资格仍 `eligible=False` |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline 的 invocation 新鲜、exit 0，只证明该授权 case/claim。2026-08-10 新增 **A1-5**（weather→去处推荐，claim `adaptive_replan_continuity`）两趟独立各 **1/1**，但它服务的 case 仍是 `reviewed`，不进 gate 选集 |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 fallback **0**；MiniMax **11/122**，其中未声明 **2**（原 4） |
| 工具通道（协议层，**可跨 provider 比**） | 走成 `toolcall` 的比例是 **provider 属性**：`minimax:MiniMax-M3` 同用例 **13/27（48%）**、跨域 20 条 **9/20（45%）**；`deepseek:deepseek-v4-flash` 两组 **35/35（100%）**（p≈0.0002）。⚠ 代价只在**需要模型自己填结构化字段**的多阶段计划上兑现——那 20 条 stable 上两档通过率都是 20/20（findings §24）。**2026-08-10 起 `PLANNER_TOOLCALL_SALVAGE_RETRY=on` 默认开**：gate L1 双臂实测把 MiniMax 从 **51.3%（60/117）抬到 85.5%（100/117）**，+34.2pp、p=2.3e-08、重试成功率 ≈70%，代价墙钟 +38.5%（findings §26.5）。**引用 45~48% 那组数时注意它是 off 档口径** |
| 代码回归 | 全量基线见本节顶部（**6257**，2026-08-17 QA 批 Q7 残余后；**本轮产出只占 +55**）；分套件最近实测：`orchestrator/cloud` **919**（2026-08-17，Q7 残余 +47 执行事实进焦点与确定性计划）/ edge **718**（2026-08-17，Q7 残余 +5 混合路径同轮执行事实；此前 Q13 +95 命名收敛守卫、Q7 +37 极性与分段）/ nearby **99**（Q2 +6）/ navigation **132** / trip **85** / mcp-bridge **478**（Q10 +45：新文件 `test_order_session_scope.py`）/ memory **254**（Q5 +21、Q6 +4）/ chitchat **62**（Q6 +21、Q5 残余 +15）/ reminder **164**（Q5 +3、Q11 +4）/ `agents/_sdk` **+8**（新增 `test_timewindow.py`）（2026-08-15）。Skill / Exemplar（**307 条 / 22 域**，2026-08-15 实数——clarify 型机制已就位但**刻意零生产范例**，见 §4.2）/ L0 门禁均通过。catalog 目录 **145 条 / 12615 字符**（2026-08-15 QA 批 +1：`safety.driver_state`；余量 16000−12615=**3385**——**每次加能力都要把余量重新看一眼**，撑满时该做的是检索化 catalog 不是放大预算）。端侧能力面 **80 条**（vehicle 76 + media 4）/ VAL 车控对象 **67**。⚠ 2026-08-11 起 `VEHICLE_INTENTS` **不再手写**，由 `commands.yaml` 各对象的 `edge_intents` 派生（B4），数字不变但改的地方变了 |

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

**① 探索式真实用户 QA 轮 58 个问题：Q1–Q13 立卡，阶段 0/1/2 完成，阶段 3/4 部分完成**
（2026-08-15 立卡 / 2026-08-17 持续推进，逐批流水见下。报告
[`…exploratory-real-user-qa-deepseek-minimax.md`](docs/reviews/2026-08-15-exploratory-real-user-qa-deepseek-minimax.md)、
方案与全部读数
[`…qa-exploratory-root-cause-cards.md`](docs/design/2026-08-15-qa-exploratory-root-cause-cards.md)、
流水 history **§41–§53**，本轮归档索引 **§47.5**。）

接手先读卡的 **§1**（**八处**与报告定性不同的重判——每一处都改变了「该修什么」；
**第 8 处推翻的是卡自己写的落点**，所以「卡上怎么写」也要拿真栈验）
与 **§4**（阶段计划），
**别按报告的 T0/T1/T2 直接开工**——报告按症状分档，会把同一个根因拆到三个档里。

| 阶段 | 状态 | 内容 |
|---|---|---|
| 0 取证与止血 | ✅ 完成 | I-021 定性推翻（QA 轮零 create-order）、迷你集 `scripts/probe_qa_regression.py`、方法论进 `test/README.md` §5.3.1、清洗 dry-run 10 条 |
| 1 T0 安全与不可逆 | ✅ 完成 | manual-rag 零命中短路 + 来源类型 / 安全分级不进 LLM / `Focus.safety_alert`（契约 `conventions.md` §9.1）/ road-safety `safety.driver_state` / chitchat 兜底闸 / 支付字段 fail-closed。真栈 SF1·2·4·5 **3/3**，SF3 0–1/3（失败轮全是澄清，危险症状零复现） |
| 2 T1 共享状态机 | ✅ 完成 | **Q1** 取消判据收敛 `pending_cancel.py` → 确认帧带 `operation_id` → 小容量挂起表（≤3，LRU 有话术）+ HMI 同屏多条确认条；**Q3** 帧带 `request_id`、对不上即丢帧、看门狗每轮一只、抢占点名回 cancelled；**Q4** 位置闸拆 ORIGIN/ANCHOR + 否定邻接。契约 `conventions.md` §9.19/§9.20。真栈：confirm 组 **18/18**、CDP C11-13 × 3 轮 **9/9** |
| **3 T2 语义与真实性** | 🔄 **进行中** | Q13 ✅ / Q2 ✅（第一批）/ **Q7 ✅ 三维度全收口** / Q5 ✅ / **Q10 ✅ 三块（第四块依赖不成立，见下表）** / **Q6 ✅** / **数据清洗 `--apply` ✅** / **Q12 ✅ 主体**（规格维等 Q2 残余）。逐卡状态见下表 |
| 4 T3 能力面与长尾 | 🔄 | **Q11 ✅ 提前做**（它是清洗 `--apply` 的最后一道前置）；**Q8 / P2 长尾未开工** |

**逐卡状态与接手入口**（新会话从这张表开始；每张卡的落点、判据、读数在
[卡 §4 的各「实施记录」段](docs/design/2026-08-15-qa-exploratory-root-cause-cards.md)，
流水在 history §42–§53，**这里只留状态与残余**）：

> ⚠ **表按「接手顺序」排，不按卡号**——第一行就是下一个该动的。
> 洁癖整理三次：第一次（08-16）把未完成的挪到上面；**第二次（08-16，Q12 收尾）按接手顺序
> 重排，并把 Q8 从第一行挪到未完成项的最后**——它是**叶子**（不解锁任何别的卡），
> 而 Q2 残余是三条卡的**共同前置**却原本排在它后面。**前置排在叶子后面是排序 bug。**
> 第三次（**2026-08-17，Q7 残余收口**）把 Q7 移入已完成区，**Q2 残余升到第一行**。

| 卡 | 状态 | 残余（**接手要做的就是这一列**） |
|---|---|---|
| **Q2 候选集升格** | 🔄 载体+绑定已交付（**残余是三条卡的共同前置**）← **下一个（接手顺序第 6 步）** | **I-018/I-023 的确定性消费方没写**（「哪家最晚关门」「两个价格合计」）+ 缺一条到 Agent 的下发通道。属性已跨轮存住，算它的人还没写——**别把「数据在了」读成「问题解决了」** |
| Q10 查单绑会话 | 🔄 三缺一（第 7 步） | 查单/写路径/指代占位符三块 ✅（history **§49**）。**残余=「文本入口与按钮入口收敛到同一结构化解析链」，且它的依赖经取证不成立**——`Focus.candidate_sets` 只活在云侧 `context.py`，**没有下发到 Agent 的通道**（`mcp-bridge` 的 `context_scopes: []`）。要先有 Q2 残余那个确定性消费方与下发面 |
| **Q12 槽值保真** | 🔄 主体已交付（残余同第 7 步） | 原话回查（时间维）+ 中文时间词三份实现收敛已落地（history **§52**）。**残余=规格维（I-025②「少冰」）验不到**——被「第一杯」序数绑错候选集挡在前面，**卡在 Q2 残余**；且规格要按商家可选值域校验，那是 B6 §4 的 `input_schema` 远期字段 |
| **Q8 能力缺席** | ⏳ 未开工（**叶子，排最后=第 8 步**） | 补 `navigation.estimate` / `media.mute-unmute` / `hazard_light` / 方向盘加热端侧入口（走 B4 SOP，从 §4.2 那 14 条欠账里挑并删台账条目）。⚠ **开工前先决策语料额度**（见接手顺序第 8 步）。⚠ **2026-08-16 从 Q12 转来一条**：`navigation.navigate_to` 的 manifest **没有 `origin` 槽**，用户明说「从 X 出发」时 planner 无处可放——现状是**静默用当前位置**（探针 SL4 四轮合计 **12/13 红**），QA 轮那次是就近塞进 `destination`（导到出发地、0km）。**是能力缺席不是槽值保真** |
| Q5 身份/作用域/记忆写入 | 🔄 出处披露已收口 | ① 出处披露 **✅ 0/3 → 3/3**（`agents/chitchat/src/mem_source.py` 确定性追加，history **§51**）。⚠ 汇总行是 `[var]` 不是 `[det]`，**这是预期的**——主张的是「出处确定性」不是「整句确定性」。② **残余=实体归一**（`孩子`/`女儿`、`妈妈`/`用户的妈妈` 苏州 vs 杭州）与 **N3**（`导航去过济南市南山实验小学` 两条）——都是 `memory_item` 里的事实级脏数据，**清洗脚本刻意够不着**（history §48.5）。⚠ **这条不在 5–8 步的编号里，它跟 §4.1 ② person-pickup 卡是同一件事**：那张卡的召回源已被证明就是这两条 `memory_item`，**动那张卡之前先处理它们**（history §48.3） |
| Q4 位置前置闸 | ✅ | 后端 `need_location` 帧那条更彻底的方向仍是二期（卡 §3-Q4） |
| Q11 提醒准入 | ✅ | offer 准入那条（只在「明确未来事件 + 时间可用」时 offer）未做 |
| Q6 执行账本 | ✅ | 动作随会话轮次落库（`AppendTurn.actions`）+ chitchat 确定性审计出口，AU1 red→**3/3 `[det]`**。写入量量清楚了（38 天 763 个动作、有动作轮仅 24%）⇒ **不建表**，保留期由既有 TTL/ltrim 兜住。history **§50** |
| 数据清洗 `--apply` | ✅ | 四族终态 0/0/1 组保留/0；③ 改成逐组点名授权。**清洗只覆盖 `memory_relation` 与 `reminder_item`**，`memory_item` 里的事实级脏数据归 Q5 残余 |
| **Q7 端侧语义维度** | ✅ | 三个维度全部收口。EL1 **1/3 → 8/8 `[det]`**、OR2 **0/3 → 8/8 `[det]`**，确定性路径真栈命中 6/6、零 LLM。契约 `conventions.md` **§9.26**，history **§53** |
| Q1 确认/挂起/取消 | ✅ | — |
| Q3 HMI 响应归属 | ✅ | — |
| Q13 两分类出口收敛 | ✅ | — |

**接手顺序**（下面这个次序是有理由的，别按卡号做。**第 0–4 步于 2026-08-16 完成、
第 5 步于 2026-08-17 完成 ⇒ 新会话从第 6 步（Q2 残余）接手**）：

| # | 做什么 | **为什么排在这里** |
|---|---|---|
| ~~**0**~~ | ~~**数据清洗 `--apply`**~~ ✅ **完成** | 排最前**不是因为它简单，是因为它改变后面所有卡的读数**——已兑现：XS3 从「方差」变成 **0/3 稳定红**、CD2 从 2/3 升到 3/3，并**关掉了 person-pickup 卡的召回源悬案**（history **§48.3**）。⚠ 清洗只覆盖 `memory_relation` 与 `reminder_item`；N3 与实体归一在 `memory_item` 里，**没被清、也不该由脚本清** |
| ~~**1**~~ | ~~**Q10** 查单绑会话~~ ✅ **三块已交付** | I-021+I-026（查单）、I-037（写路径）、指代占位符三块完成，XS4 **1/3→3/3**、XS8 **0/3→3/3**（history **§49**）。⚠ **原表写的「依赖已满足（Q2 候选集已就位）」对第四块不成立**——候选集只在云侧、**无下发通道**，见下面新的第 4 步 |
| ~~**2**~~ | ~~**Q6** 执行账本~~ ✅ **已交付** | 它建立「**确定性 handler 回答系统持有的事实**」这个模式——而 **Q5 的出处披露残余复用同一个模式**。所以 Q6 必须在它前面。⚠ 先量清楚 533 轮 × N 动作的写入量与保留期再定表，别做成只涨不清的 |
| ~~**3**~~ | ~~**Q5 残余**：出处披露~~ ✅ **已交付**（XS3 0/3→3/3，history §51） | 紧跟 Q6，直接复用上一步的确定性消费方形态。现状是 **0/3 稳定红**（清洗后复跑改硬的读数，不再是此前记的「方差」），要的是机制不是提示词。**Q6 已把那个形态建好了**：`agents/chitchat/src/audit.py` 就是「确定性 handler 回答系统持有的事实」的样板，出处披露照它做 |
| ~~**4**~~ | ~~**Q12** 槽值保真~~ ✅ **主体已交付** | 收敛类改动照例照出一批连带——**实测是三份实现不是两份**（`weather.py` 还有一张只有中文的日词表，**那就是 I-041「英文 tomorrow」的根因**），外加两个活缺陷（`早晨八点`→20:00、`晚上12点`→中午）。同批把 I-029 重判给 Q8、I-025② 重判给 Q2 残余（history **§52**） |
| ~~**5**~~ | ~~**Q7 残余**：EL1 → OR2~~ ✅ **完成** | EL1 **1/3 → 8/8 `[det]`**、OR2 **0/3 → 8/8 `[det]`**（history **§53**）。⚠ **卡上「从 Focus 取对象」的定性被取证推翻**：`update_focus` 只由云侧规划轮驱动，端侧本地快路径那 40% 的车控动作**压根不在焦点里**（真栈对照：本地轮 0 个 focus key / 云侧规划轮 1 个）。真正缺的是 Q6 执行账本的**读侧**——`orchestrator/cloud/clients.py` 那一行没读 `actions`，而 `agents/_sdk/clients.py` 读了 |
| **6**（2026-08-16 洁癖整理时从第 7 步前移，留痕） | **Q2 残余**：I-018/I-023 的确定性消费方（「哪家最晚关门」「两个价格合计」）+ 候选集下发面 ← **接手从这里开始** | **它是三条残余的共同前置**（Q10 双入口收敛、Q12 规格维、以及后续任何要引用候选集的卡），而原来排在 Q8 后面——**前置排在叶子后面是排序 bug**。属性已跨轮存住，缺的只是算它的人 + 一条到 Agent 的下发通道 |
| **7** | **Q10 残余**：文本/按钮双入口收敛 | **必须排在第 6 步之后**：修法是「文本路径先在当前候选集里做确定性匹配」，而 `Focus.candidate_sets` 只活在云侧 `context.py`、**没有下发到 Agent 的通道**（取证见 history **§49.6**）。同理 **Q12 的规格维**（I-025②「少冰」验不到）也卡在这一处 |
| **不编号** | **Q5 残余**（`memory_item` 实体归一 + N3）| 它与 **§4.1 ② person-pickup 卡是同一件事**——那张卡的召回源已被证明就是这两条 `memory_item`（history §48.3）。**跟着那张卡做，别单独排队**；判据是「这条事实对不对」，只能人裁，脚本刻意够不着 |
| **8** | **Q8** 能力缺席 + **Q11** offer 准入 + P2 长尾 | 收尾——它是**叶子**，不解锁任何别的卡，所以放最后。⚠ **Q8 开工前先决策**：对抗语料唯一输入 **596/596 余量为 0**，而新增 active intent 必须补对抗覆盖（正 2/硬负 2/对照 1）。**先说明新能力为何值得占额度、再有原则地调 `suites.yaml` 的 `max_cases`**——不得先加语料撞闸后补理由。⚠ 本卡另含 Q12 转来的 `navigate_to` 缺 `origin` 槽一条 |

**第 0 步终态**（2026-08-16 泓舟逐族拍板执行，流水与判据 history **§48**）：
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

**② 复合句里的「接送某人」人称解析失效**（2026-08-15 双档复跑取证，**两档都红 ⇒ 系统缺口**）：
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

⚠ **对抗语料唯一输入 591 / 上界 591**——当前余量仍为 0。下次加 L0 语料必须先说明新增
能力或边界为何值得占额度，再有原则地调整 `suites.yaml` 的 `max_cases`；不得删旧尺子压数字，
也不得先加语料撞闸后再补理由。560→591 的九次递进与逐项占用写在 `suites.yaml` 头部。

> 逐批流水在 [`docs/agents-history.md`](docs/agents-history.md) **§15–§27**，逐条证据在
> `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§17–§26**。
> 本节按约定**只留状态、不留流水**——沉淀下来的判据在 §4.3，数字在 §4.0。

### 4.2 延后 / 条件待办索引（不进入当前主线）

> §4.1 只放正在行动的事项；本表保留尚未启动、等待外部条件或明确后置的入口。条件满足后先晋级 §4.1 并补完成判据；历史流水仍只查 `docs/agents-history.md`。

| 主题 | 当前状态 / 启动条件 | 权威入口 |
|---|---|---|
| **EVA 余项**（①–⑤ 已于 2026-08-15 立卡 E1–E5 全部处理，见 §4.0 段与 history §39；本行只剩 ⑥） | ⑥ **G10 订座/票务维持搁置**（本表内唯一仍未启动项）：诚实桩现状可接受，有合适 provider 时按 mcp-bridge 准入流程走，**不为对标造假订座**。⚠ E 批遗留的两处**已知边界**（不是待办，出现真实消费方再谈）：归城校正的判据是各城池质心，某城池被高德限流搜空时该城不参与判定（末轮真栈实例：潍坊）；方差面维持档案化，E4 探针首跑 15/15 未复现，**不加 hint、不动 gate 案例集** | [E1–E5 卡](docs/design/2026-08-15-eva-backlog-cards-e1-e5.md)、[验证报告](docs/reviews/2026-08-15-eva-instruction-set-e2e-verification.md) §5、[缺口分析](docs/design/2026-08-14-eva-round2-capability-gaps.md) §2 |
| **端侧车控能力台账余项**（B4 产出） | 门禁台账 `orchestrator/edge/knowledge/capability_exemptions.yaml` 共 **39 条**，四类：媒体别名 8 / 云侧域对象 11 / 座舱 UI 面 6 —— 这 25 条是「本来就不该有端侧 intent」，**不是欠账**；剩下 **14 条是欠账、只是本批不做**（`air_purifier`/`auto_hold`/`bluetooth`/`epb`/`equalizer`/`frunk`/`hotspot`/`key_tone`/`low_beam`/`navi_broadcast`/`surround_view`/`wifi`/`driving_mode`/`battery`——VAL 侧多有分支或话术，只是端侧没给 fast_intent 规则与意图名；云端计划仍可经 `action_to_structured` 走到）。这 14 条里有 **3 条待人裁**：① `frunk` 是 `require_confirm=true` 的危险对象却没有任何端侧 intent、与 `trunk` 不对称，**是刻意不给语音开还是漏了**；② `driving_mode` 与 `power_mode` 语义高度重叠，可能是同一件事的两个对象名（若重复应合并——别让 planner 面对两个分不开的工具）；③ `battery` 查询要不要补端侧意图。新增端侧意图时**从这 14 条里挑**并同步删台账条目 | [B4 方案](docs/design/2026-08-10-b4-capability-pack.md) §6.5、台账文件本身 |
| **P3b operate 抽取与放量** | 原表里并列的两个具体缺口（除雾能力缺席、「穿衣指数→股指」）已于 2026-08-10 修完，详见 history §23.1/§23.2，本行只留放量条件。**放量门槛不变**：operate 抽取 + 真实错对象率 <0.3%。⚠ 压这个数的手段是 **R4.1b P1 执行侧对象化**（让 VAL object 数从当前 67 继续长），**不是调阈值**；且当前 PoC 没有真实流量，这个数只有观测面、还没有分母 | [M5 P3 收尾](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) §P3 收尾 |
| live 路由回归进 CI | hint 退役后的召回保护目前是 live 人工车道，不是 CI 阻断；有稳定凭证、预算与 provider 方差处置后再接 CI | [旅程体系](docs/design/2026-07-14-journey-e2e-test-system.md) §4.3、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| `route_hints` 继续退役 | 当前实数 **11**；`mcp-bridge#0` 必须先过专项安全回归。旧三条单档候选不得按历史索引直接执行；当前只用 MiniMax 主模型 / DeepSeek 对比，MiMo key 失效不阻塞主线，切换 provider 时重新全覆盖取交集 | [M5 P2](docs/design/2026-07-28-intent-accuracy-data-flywheel.md)、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| M5 后续杠杆 | catalog 检索化当前是“有意不做”；16k 预算再次裁剪或保护集显著变瘦时重评。gold→范例现走 CLI；P4 仅在范例 ≥2k 且 N1 平台期 ≥2 周时启动 | [M5 P2/P4](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) |
| M-B / M-C / M-D 明确后置项 | 13 张验收主卡已清零；跨域删除 saga、完整隐私管理/迁移仪式、持久治理扩面与 MCP 生产化覆盖按 GDPR 完备性、量产迁移或新消费方触发 | [总体验收](docs/reviews/2026-07-26-acceptance-review-m0a-m4.md) §10.2/§11.2/§12.2、[OwnerKey 契约](docs/conventions.md) §9.13 |
| M2 / M3 产品化边界 | Ledger 自动续跑/任务中心，以及主动治理持久化、偏好学习、dashboard、远距 geocode 与真实商户均为显式未做；出现真实消费方或产品阶段后另立卡 | [M2 RFC](docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md) §9.6、[M3 RFC](docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md) §10.7 |
| M4 声纹 / 视觉余项 | 真麦校准、语音注册入口、模板漂移治理、视觉多轮与 `vp_*` 指标消费仍未收口；进入真机量产验收或 v2 前启动 | [M4 RFC](docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md) §11.5/§11.8、[声纹契约](docs/conventions.md) §9.11 |
| **可执行性判定 canary**（B6 shadow 的下一步） | **shadow 已落地并取证**（2026-08-11）：裸对象族召回 2/2、假阳性 4/574（0.70%），planner 对照 `nq.landmark.bare` 11/20。**canary 需泓舟单独拍板**（B6 §5 第 4 条，本文件只授权到 shadow）。在那之前该做的是 §2.3 明写的**对照实验**：拿 shadow 记下的分歧样本人工裁定，胜率显著才谈接管——「诊断出一个洞不等于这个洞就是病因」。⚠ 唯一漏判 `ex.colloquial.dark`「有点看不清路了」**不属于裸对象族**（与金标执行的「有点热」形态逐字同构，没有形态差异可用），它要的是「唯一默认动作是否存在」那个 catalog 特征，是 canary 前的独立一步 | [B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) §6.3/§6.7、回放 `test/eval_actionability.py` |
| 裸对象澄清族（**已知无解状态，不是待办**；⚠ **B6 shadow 已给出第四条路，见上一行**） | **三条路径已全部走完，该族目前无可用修法。** `nq.landmark.bare` 合并 11/20≈55% 的高方差边界句；`nq.landmark.explicit` 自己每条断言都过、被 relation `clarify_flip` 连累；同族第三条 `nq.city.bare`「上海」reviewed 未进池。路径 1「写 guide」实测**有害**（§18：4/10→1/10→退回 7/10，p≈0.02）；路径 3「换出预选池」执行并全量验证后由泓舟裁定回退（§19.5）；**路径 2「范例 schema 加 clarify 型」2026-08-10 已实现并合入**（schema+渲染+门禁+契约+11 条测试），但同批实测出它**治不了本族**：裸专名之间 IDF-Dice 全 0.000（检索是内容通道，而裸对象澄清是**形态判据**——这也解释了路径 1 为何有害），且澄清型范例天然与其「补全版」近重复（两条候选一条抢到明确请求 top-1、一条只靠 0.03 差距，全部撤回，**故仓库刻意没有 clarify.yaml**）。**下一次要动它得换判定形态（形态/句法特征），不是再加一层检索式知识** | 立卡整段写在语料 `test/eval_corpus/intent_adversarial/cases/negation_quotation.yaml` 的 `nq.landmark.bare` 头上；[findings §18/§19/**§25**](docs/design/2026-08-02-intent-routing-adversarial-findings.md)；机制契约 [`skills/exemplars/README.md`](skills/exemplars/README.md) §clarify 型 |
| B3-2「广州塔」地标解析（高德侧） | **不进落域账**。2026-08-10 同坐标直连复测：geocode 对（兴趣点／广州塔真坐标）、`near=None` 重搜 top1 就是广州塔，只有带深圳偏置的关键词搜索会顶出「广州仄仄科技有限公司」2.4km。即 R1 去偏置重搜机制本身是对的，但它**要多打一次高德**，跑批并发下正是最易被限流的那一次；掉一次退回就近弱匹配，掉两次成「暂时无法确定」——两次红的两种形态由此解释。归高德 QPS 一族，出现真实用户投诉或做并发治理时再启动 | [复杂意图·地标/停车](docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md)、判据与复测记在 `test/journeys/target_b.yaml` B3-2 注释 |
| `e2e_verify` 案例①：**前提变了不是修坏了** | 2026-08-11 全新重建的干净栈上 5 条红，**逐条定性为测试前提失效**，不是回归。该用例的前提写在它自己的注释里——「单句『打开空调』被端侧快路径直接执行，**必须用混合多意图句**才能规划出云侧 hvac 步」。2026-08-11 实测：混合句「帮我把空调打开，再查一下附近有什么好吃的」的 `route.mixed` span 记着 `local_actions:1`，**端侧把 hvac 那半自己执行了**（`val.execute`），云侧只剩 `nearby.search`——于是 `state_match` 那两条断言恒不可能满足。另两条 `schema` 断言失败是**测试查的 trace 与实际落的 trace 不是同一个**（按文本直查该 trace，`step.verify{mode:schema,verdict:sat}` 明明在），第 5 条是前四条的连带。**对账链本身是好的**（案例②/③ 全绿）。修它要先决定断言该指向什么（换一句仍能规划出云侧车控步的话术 / 或改测端侧 VAL 面），属独立一卡；B5/B6 结构上不可能造成它——那个决定发生在云端被调用之前 | 证据：本条 + `test/e2e_verify.py` 案例①注释、history §27.6 |
| B6 §4 Capability Contract 远期字段 | **逐字段独立触发，无当前行动**：`input_schema`/`output_schema`（槽位类型错误成稳定 badcase 族或 typed Executor 立项）、`compensation`（撤销/补偿类产品需求）、`version`/`deprecation`（第三方 Agent 生态启动）。`replayable`/`idempotency` **已由 B5 §4.2 就地收口**——不新造 `command_id`，复用 `_fingerprint` | [B6 方案](docs/design/2026-08-10-b6-actionability-forward.md) §4 |

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

```bash
cp .env.example .env        # 可选填 LLM_API_KEY；不填走 mock 也能跑
make proto                  # 生成 gen/python + gen/go（没有它什么都跑不起来）
python test/smoke_edge.py   # 验证端侧逻辑（无需 docker，应 13/13 通过）
make up                     # 起全栈（首次需调试，见 docs/dev-guide.md）
```
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
| 端侧车控能力（知识库 / 意图 / 话术）| `python test/eval_capability_integrity.py`（六维逐对象，CI blocking）+ `python scripts/check_intent_gate.py`（对抗覆盖 strict）+ `python -m pytest orchestrator/edge/tests -q`（含意图面迁移探针）。SOP 见 §7.1 |
| 新增服务（compose 里加一个自建镜像）| `python -m pytest runtime/tests -q`——它断言每个自建服务都拿到 `DEPLOY_PROFILE`、入口够得着部署形态闸、**且该服务 Dockerfile 真的 `COPY runtime`**；**加进 `x-python-env` anchor 不等于配上了**（有服务不用那个 anchor），**源码 import 得到不等于镜像里有**（collector/proactive 就这么断过 40 小时）|
| 给某个服务的入口加共享包 import（`runtime.*` / `observability.*`）| **另外查一遍有没有独立脚本直接 import 它**——`pytest` 绿不代表裸跑绿：根 `conftest.py` 会替测试把仓库根挂进 `sys.path`，**独立脚本没有这个待遇**。2026-08-16 实测：`fast_intent` 加了 `runtime.polarity` 之后，§5「任何人接手都先做这个」的 `python test/smoke_edge.py` 直接 `ModuleNotFoundError`，而全量 pytest 一条红都没有。再查**那个服务 Dockerfile 的依赖闭包**——少一行 `COPY` 就是「一重建就起不来」，而既有容器跑着旧镜像时**完全没有症状**。`python -m pytest runtime/tests -q` 会抓 `runtime` 那一类 |
| Planner 重试/守卫规则（B5）| **先改 `orchestrator/cloud/retry_policy.py` 的表，不要在主循环里加 `elif`**；同步方案附录 A 的清单表（`test_retry_policy.py` 逐列比对，改一处不改另一处即红）；`python -m pytest orchestrator/cloud/tests -q` |
| 中文时间词（时段/日词/中文数字/12h 修正）| **改 `runtime/cntime.py`，不要在消费方本地再写一张表**（此前三份实现给出三个答案）；`python -m pytest runtime/tests/test_cntime.py agents/_sdk/tests/test_timewindow.py agents/reminder/tests/test_timeparse.py agents/info/tests/test_weather_answer.py -q` |
| 槽值保真 / 给 `_resolve_slot_refs` 加挂点 | `python -m pytest runtime/tests/test_slot_fidelity.py orchestrator/cloud/tests/test_slot_fidelity_wiring.py -q`——后者含**覆盖面守卫**：每个调用点必须传 `ctx`（三条执行路径 executor / D0 / T2 共用这一个收口，少传不会报错、只会让挂点在那条路上不发生）|
| 省略式开关的确定性消解 / 执行事实进焦点（Q7 EL1-OR2）| `python -m pytest orchestrator/cloud/tests/test_execution_focus.py orchestrator/edge/tests/test_mixed_edge_executed.py -q`——含**接线守卫**（`build()` 真的走确定性路径且零 LLM）与**反向对照**（不该接管的句子仍走 LLM）。⚠ 判据里**不许出现任何对象词/领域词**，`fullmatch` 是安全边界不是写法习惯；动 `_FOCUSED_CONTROL_ELLIPSIS_RE` 后必须重跑 `test_actionability.py`（B6「只写不读」红线同族）。契约 `docs/conventions.md` §9.26 |
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

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
