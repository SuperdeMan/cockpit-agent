# QA 轮当前交接：已闭合范围、生产证据与剩余活项

> 状态：**开发批与安全主链已闭合，QA 验收仍非全绿**
> 更新时间：2026-09-26（R0 量尺取样、纯定义问写闸补口发布与独立 status/verify）
> 受众：接手 QA、Planner、Info、语音/TTS 或发布验证的人
> 历史流水：[`docs/agents-history.md`](../agents-history.md)（只追加，不在本页复述逐批过程）

## 1. 一句话结论

**QA 验收仍非全绿**。最后一次已登记生产 release 是 `634c28786360a7b297d197d9d81fdd84308bb821`，
对应 2026-09-26 v2 R0 基线发现的纯定义问云侧写闸补口；已独立核对 status/运行 SHA 并完成 verify。
发布、status、verify 与最新专项证据集中维护在本页 §2；AGENTS.md §4.0 只保留入口。

v2 与 Jev 已纳入 [后续路线图](../roadmap.md)，共用基线量尺已开始，R1/Jev 新运行时实现与验收未开始；
规划层方差、挂确认时结果完整性和双端/声学未验面继续有明确任务，不能借研究合入宣称关闭。
已完成批次仍使用原 SHA，历史证据见 §3–4 和 [agents-history](../agents-history.md)。

## 2. 当前发布与证据边界

| 项目 | 最后登记事实 / 使用限制 |
|---|---|
| 源码与文档起点 | `47c62b44d335a3c76da90f89f05fa2fc887c2742`；本轮 fetch 后 HEAD 与 origin/main 一致；后续纯文档提交允许领先 production |
| 生产 release | `634c28786360a7b297d197d9d81fdd84308bb821`，2026-09-26 R0 纯定义问云侧写闸补口；上一版 `5a2f4c9d674fb27c98539f1d57ff8cc93bca4c76` |
| status / verify | `ok`、`5/5 endpoint healthy`、零 warning，release/running SHA 均为 `634c2878`；verify `verified`，`20260926T103016Z-634c287.json` |
| 后端代码验证 | `634c2878` 提交前树：9968 passed / 4 failed / 32 skipped / 11 warnings；4 条均为 Windows PowerShell 模块路径导致 Get-FileHash 缺失，仅该子进程改用系统模块目录后原文件 13/13。四门禁通过、smoke_edge 13/13；两次读数不合并成一次全绿。见 [R0 记录](../design/2026-09-26-v2-runtime-r0-r1-execution.md) |
| 手册最近专项（新 release 尚未重跑） | `5a2f4c9d`：章节 187/187、视觉 35/35、胎压复合句 16/20（有手册步 16/16）、词法自信三句 8/9、调节类 6/6、口语语料 39/43 与 38/43（失败在规划层）、指代 8/9、空调模式 3/3、多步按话术 3/3、原 36 题可发送子集 31/31，车态零差异；[二批记录](../design/2026-09-26-manual-rag-colloquial-recall.md) §8 |
| 手册证据限制 | `.artifacts/manual-rag-colloquial/live-*-5a2f4c9d*.json`；被预检拦下的 5 条旧表述未发送，不能报当前 36/36；需确认混合轮最终消息没有手册卡，话术通过不等于完整卡片/正文呈现通过 |
| 历史 QA 证据 | §3–4、T24/T47 等分别绑定自己的 release/provider；不转借到 `5a2f4c9d`。原入口全部历史发布行保存在 [快照](../history/2026-09-26-entry-status-snapshot.md) |
| Android 包与验收 | [剩余待办总表](../design/2026-09-14-android-remaining-todos.md)记录设备与包身份；服务端 SHA、APK SHA、设备安装状态分列，本次未验包 |
| v2 / Jev | R0 共用量尺已实现并暴露/修复 V207，完整基线待测；Decide、ResultBundle、持久操作、多车逐信号、T1e、OEM 驱动尚未实现；[首批执行](../design/2026-09-26-v2-runtime-r0-r1-execution.md) |

复核当前现场先运行 `python scripts/dev_stack.py target show`，再按授权范围运行 status/verify 与专项探针。
`origin/main`、生产 release 和设备包不是一个版本号；5/5 健康也不能证明完整业务正确。

本页所有 `.artifacts/` 路径都是根仓本地 ignored 证据，不随 git clone 移植；
缺失时应按原记录的精确 release/模型重新取证，不能把文档记过当作 artifact 仍在。

## 3. 已闭合的 QA 范围

### 3.1 探索式真实用户 QA

- 2026-08-15 的 533 个真实业务轮、58 个问题已经完成去重、根因分类和立卡；
- Q1–Q13、Q5 残余、person-pickup 和编号序列最后一条 I-024 均已完成；
- 根因卡与重判保留在
  [`2026-08-15-qa-exploratory-root-cause-cards.md`](../design/2026-08-15-qa-exploratory-root-cause-cards.md)；
- 原始双模型报告保留在
  [`2026-08-15-exploratory-real-user-qa-deepseek-minimax.md`](2026-08-15-exploratory-real-user-qa-deepseek-minimax.md)。

### 3.2 MiniMax 云端修复批

- 2026-08-26 原始长会话自动读数为 282 PASS / 33 FAIL，但自动 PASS 不等于业务正确；
- C1–C16 六批、M1–M6 余项和 B1–B7 全量收尾已经落地；
- 完整根因、批次设计和真栈读数查
  [`2026-08-27-minimax-qa-root-cause-fix-plan.md`](../design/2026-08-27-minimax-qa-root-cause-fix-plan.md)；
- 原始问题/trace 查
  [`2026-08-26-minimax-cloud-qa-findings.md`](2026-08-26-minimax-cloud-qa-findings.md)。

### 3.3 安全确认写闸

以下执行出口已经共用同一份问句副作用判据：

1. Planner focused/normal build；
2. adaptive replan receiver；
3. Agent `_escalate` mini-plan；
4. fallback capability scan；
5. D0/T2 流式 action 出口；
6. 挂起恢复后的再规划与改派。

`Capability.response_only` 由 manifest 声明，经 Registry round-trip、`Step` 装配和恢复态保持；
Executor 在 dispatch 前拒绝 `response_only + require_confirm`，在 dispatch 后拒绝 action、
`NEED_CONFIRM` 和 `NEED_SLOT`。`safety_origin_text` 由服务端盖章并跨 replan/suspend/restore
保持，LLM `goal/reason` 与当前补槽短句都没有安全授权权威。

专题设计与实现证据查
[`2026-08-30-qa-safety-confirmed-write-guard.md`](../design/2026-08-30-qa-safety-confirmed-write-guard.md)。

### 3.4 Planner null 槽

长会话中 MiniMax 曾输出 `limit:null`；旧装配将它写成字符串 `"None"`，Info Agent
执行 `int("None")` 后返回“Agent 内部错误：ValueError”。release `a729b98` 的修法是在
`_validated_steps` 中把 JSON null 当成“未提供”，同时保留 0、false 和空字符串。

长会话探针也已补上通用守卫：任何以“Agent 内部错误”开头的话术必须判红，不能再藏在
自动 PASS 中。

## 4. 生产复验

### 4.1 安全专项（release `e9fa602`）

`probe_qa_regression.py --group safety --repeat 3`：5 例、15/15 PASS。

- 红色机油灯首轮、跨轮追问和“慢一点开”均给出停车/熄火建议；
- 疲劳驾驶后拒绝提醒仍不允许继续危险驾驶；
- 零动作、零挂起、未进入商户写能力。

Artifact：`.artifacts/dev-stack-verifications/qa-safety-e9fa602-repeat3.json`。

### 4.2 完整 information persona（release `e9fa602`）

官方长会话结果：57/59 PASS、1 warning、零中止、零 cleanup failure、fallback=0、
104 次 LLM 全 pinned。

清理结果：

- 测试提醒完成创建 → 改期 → 取消，最终列表为 0；
- 活动导航最终发出 `navigate_cancel`；
- 零 open operation；
- 全程无商户 intent/预览卡，因此无商户草稿；
- release 首尾均为 `e9fa602`。

Artifact：`.artifacts/dev-stack-verifications/qa-long-information-e9fa602.json`。

### 4.3 新闻专项（release `a729b98`）

3 个干净会话、每个 5 个新闻业务轮：15/15 无 internal error，零中止、零 cleanup failure、
release 连续。Artifact：`.artifacts/dev-stack-verifications/qa-news-repeat3-a729b98.json`。

### 4.4 真实车型手册（历史生产 `434a046`，36 题已闭合）

- 278 页 `SU7用户手册` 生成 269 个文本 chunk、350 个图片放置 / 299 个 blob；v2 包
  `648cdf3d…400ed` 经 shared-model bootstrap 只读挂载，启动决议仍为 approved real provider；
- release `434a046` 的完整 36 题精确落域 36/36、内容 36/36；14 个高风险问法全部 3/3，
  合计 64/64；
- 64 轮均为单一 `manual` 卡、approved real provenance、零 action/need_confirm/probe error；逐轮
  读取完整车态，最终 diff={}；雨刮与安全带俗称继续返回正确手册图片；
- 统一 verify、5/5 endpoint healthy、零 warning；verify artifact
  `.artifacts/dev-stack-verifications/20260903T130534Z-434a046.json`。手册真栈 artifact
  `.artifacts/manual-rag-live-validation/20260903T130655Z-final-434a046.json`，SHA=`3fed8c94…d63a`；
- exact 代码本地全量 7833/34/4，确定性 retrieval 36/36。这里关闭的是原36题基准项，不代表
  整本手册范围全绿；2026-09-04 扩面结果见下节。

### 4.5 整本手册范围复核（历史生产 `7b594f37`）

- 原 PDF 重建与 `.mrag` 逐字一致；离线候选的269页、160索引路径、187 outline叶子、35视觉
  语义、原36题均全绿，证明当前失败不支持“应改向量库”的结论；
- 当前生产自然化187叶子首轮181/187（96.79%）；6条失败后两轮均6/6，全部为2/3，
  无0/3稳定章节失败，但不能写187/187稳定通过；其中哨兵模式首轮有1次opening-handshake timeout；
- 当前生产视觉30/35（85.71%）；位置灯、左右转向、后雾灯、近光灯为0/3稳定零命中；
- 章节主批+失败复验199轮、视觉主批+失败复验45轮均action=0、need_confirm=0、完整26项
  车态diff={}；章节主批有上述1次transport probe error，视觉为零。初版不安全名词短语探针曾发
  一次幂等`hvac.on`，已中止并由双安全预检取代，不能藏掉；
- 原36题当前整批有7条旧表述在联网前被安全预检拒绝，故不报当前36/36；用户点名的雨刮与
  “小人背宝剑”安全问法已各3/3，均返回预期PDF页和图片，零动作、车态不变；
- 本地候选只扩三字caption的视觉语境匹配，并在manifest增加明确SU7手册来源的窄hint；离线
  视觉35/35、显式来源222/222；该候选随后发布并继续修复LLM故障降级，终态见下节。证据与hash见
  `docs/design/2026-09-04-xiaomi-su7-manual-rag-full-coverage-validation-plan.md` §5。

### 4.6 整本手册生产闭合（历史验收锚 `9a3b6f2f`）

- 0.3.2首次发布到`805711cf`后，五个三字caption稳定缺口消失；但章节主批184/187、视觉
  34/35仍出现4次`Agent 内部错误：RuntimeError`。Trace证明均已落`manual.query`且Agent执行
  失败，不是路由或BM25问题；
- 0.3.3对LLM RuntimeError做一次有界重试，配额/参数/鉴权类不重试；仍失败时返回已检索的真实
  PDF卡与诚实降级话术，ValueError等编程异常继续显式失败。精确代码全量 7861 passed / 34 skipped / 5 warnings，0 failed；
- 发布后独立章节批 **187/187**，p50=9365.659ms、p95=14446.685ms、max=34710.202ms；视觉
  **35/35**，p50=7886.125ms、p95=13456.285ms、max=21204.482ms；
- `雨刮器怎么打开`与“小人背宝剑”各3/3，分别稳定返回PDF第95/193页与对应图片；所有正式轮
  action=0、need_confirm=0、probe error=0、完整26项车态diff={}；
- 统一verify、5/5 status均通过。章节主artifact SHA=`508756d6…baf80`，视觉SHA=
  `7c05d04a…e0762`，全量日志SHA=`ab40f81e…d91df`，verify SHA=`7a521902…ad44`。

## 5. 当前活项

2026-09-26 接续：手册口语/复合句尚有规划方差，确认轮完整结果呈现进入 CA2-02–04；
会话四轮未关项按 [原待办](../design/2026-09-24-conversation-review-round4-remediation.md) §7 重证；
Android 按 [总表](../design/2026-09-14-android-remaining-todos.md)。统一优先级见 [路线图](../roadmap.md)，
本轮未实施这些改动。下面保留历史 QA 五项的逐条处置，不代表全部仍待修。

2026-09-19 逐条收口，过程与证据见 [QA 轮剩余活项收口](../design/2026-09-19-qa-residual-closeout.md)。

- **2026-09-11 那条「端侧新闻规则误判 `media.play` / Planner 漏拒」已于 2026-09-14 修掉并发布**
  （`696899b5`，随 `9ced633b` 上生产）：端侧 `runtime/reported_speech.py::is_reported_speech` 语域闸 +
  Planner「语音来源 ∧ 播报语域 ∧ 两轮空手 ⇒ 不受话」；发布后只读复跑 24 轮，播报语域 11/12 静默拒识、
  首轮失败原话端侧不再执行、零动作。**没修的那一半**：乘客句「他昨天跟我说那个项目黄了」没有播报语域，
  文本上与「用户向助手转述」不可区分，归声学（真人 + 背景源），见
  [逐条核实](2026-09-11-voice-input-acceptance-live-findings.md) 末段。

| 活项 | 已登记处置状态（原 09-19 表，含后续补记） | 证据 / 下一步 |
|---|---|---|
| 安全问句偶尔落 `info.search`（T24） | **已修、已发布 `1eb25a70`、真栈复验通过** | 根因是 manifest 没把 Agent 早就实现的「告警 ⇒ 按等级给续驾结论」说出来（planner 只看 description），既有 hint 只认「高速/路上」开头。修法全在 `agents/road_safety/manifest.yaml`：描述 + 续驾 hint（122 < manual 124，手册地盘不动）+ 话术「出现X时」；`test_route_hints.py` 37 passed、`eval_route_hints` 118/118、范例 +1、四门禁全过、两处变异判红。真栈（`1eb25a70`，`minimax:MiniMax-M3`）：干净会话「红色机油灯亮了还能继续开吗」**3/3** `safety.driving_advice`、`safety_advice` 卡 `_prov.mode=deterministic vendor=road-safety`、零动作零挂起（trace `fe64eab2c9da4a17972c02b4f2f04df9` / `206c1e0acad748e8968adb821a577c6e` / `1090381819284b6e8be35f2953884416`）；「水温报警了还可以继续行驶吗」1/1（话术「出现水温报警时不要大意…」）；对照「机油灯亮了怎么办」2/2 仍 `manual.query`；`probe_qa_regression --group safety --repeat 3` **15/15**（SF3 三趟 manual → safety → safety 零动作）。information persona 整场未重跑，T24 那一格只在这里闭合 |
| safety focus 持续阻断后续 charging plan（T47） | **已裁决（A）、已发布 `0d414816`、真栈 T47 格 3/3 闭合** | 用户 2026-09-19 裁 A；同日实施：`runtime.safety_signal.alert_resolved`（完成态解除陈述 ∧ 点名告警对象 ∧ 非问句非指令非否定；「我会靠边」不算）⇒ 编排清焦点 + `safety_alert_cleared` 旗挡接力；road-safety / chitchat 解除轮不再读旧告警；`alert_level` 去掉解除分句（「机油灯灭了但是水温灯亮了」仍登记水温灯）。runtime 13 / focus 25 / 三 Agent 139 / cloud+runtime+edge 2853，四处变异判红；persona 补解除轮。真栈（`0d414816`，persona 同形序列 ×3）：解除轮 3/3 chitchat 零动作、**T47 句 3/3 `charging.plan`**、后续「现在还能继续开吗」3/3 无「未解除」（收口页 §3.4）。顺带抓到并修掉两处「解除/告警陈述到不了输入侧登记」的漏点：① `dest_choice` 挂起把它整句当目的地吞掉 ⇒ `_is_topic_change` 安全信号一律判换题（**已发布 `b342e3bb`**，带挂起序列 ×3 零吞句）；② 规划轮在技术失败 / 授权缺失 / 澄清 / 取消未命中 / 没听清五条出口提前 `return`，`extract_focus` 没跑到 ⇒ `_register_input_facts`（**已发布 `ca4bf370`**，真栈：解除句落技术失败出口那一趟 T6 不再「未解除」，收口页 §3.5） |
| MiniMax TTS 长文本 / RPM 边界 | **按 2026-09-06 证据销账** | 原 887 字样本的服务商回包 `rate limit exceeded (RPM)` 是 08-30 的历史证据；生产 `a09c73a`：931 字整段 2 请求（修前 79 请求、4×60s 等待、4 个 ~20s 空白）、204.6s 音频完整、`sim_underruns=0`；账号级共享限流桶（并发配额）；泓舟人耳 OPPO / Xiaomi 两机 ✅（Xiaomi 176.2s 音频 underruns 0、gaps []）。**仍开、独立记**：混合意图轮盲听；长会话探针的 TTS 采样车道自 `e9fa602` 后未在新 release 重跑 |
| barge-in 在途残帧 | **裁决完、判据已改** | 客户端必须丢弃且已在丢弃（HMI / mobile `disposed` 守卫；CDP C14；mobile 新用例钉住 6144 / 8192 字节不进播放器）；服务端「零字节」在全双工上不可判，改为「最后一片残帧 ≤ 1s 在途窗口 ∧ ≤5s 关闭」（`probe_qa_long_sessions.py::_BARGE_IN_FLIGHT_MS`），未计时的残帧仍判红。下次长会话跑批生效 |
| 全量 warning | **gRPC fixture 债务已修；其余分类留档** | gRPC `UnaryUnaryCall._invoke was never awaited` = trip 测试三次 `asyncio.run` 共用真 `LLMClient`（经系统代理各等一轮超时），改显式「不可达」替身，trip_planner 99 passed 零告警、9.86s → 0.73s。Starlette `httpx2` 弃用（第三方，换依赖是红线）、`audioop` 历史项已在 09-25 的 `0a876b73` 移除（见会话四轮 §5.7）、regex / WordPiece（第三方）留着不藏。当前全量条目见收口页 §7 |

2026-09-08 Android AR04 的发现与处置（客户端包、源码修复与生产分别记录）：

- 后端 `a09c73a5da3181708279bc1f3e90acb1519606a0` 的提醒错域仍未修。原 trace `21798d30258aa5bf` 已查明实际 `minimax:MiniMax-M3` 首次生成非法 steps，重试给空计划，随后 `toolcall_degraded → chitchat.talk → info.search`；数据库零创建。问题在格式化规划失败及后续降级，不能从这一个样本推导稳定错域率。
- 提醒标题污染已由 `573ad46` 修复并于 2026-09-09 发布；220 条服务测试与 CI 通过，真实创建 trace `510339013295c9a7` 已证数据库 title 不再带创建指令前缀。成功创建 trace `458440c431c99c8f` 的 Planner 原 title 已是干净标记，污染发生在 Agent 回填原话；五条旧记录没有被改库清洗。
- 对话页“说话可打断”提示已由 `a731973` 修复为“播报中”；OPPO 当前包 `573ad46d9` 已在实际播放时取证，麦克风计数为 0，不再承诺直接语音打断。此项客户端缺陷已修，不能据此关闭更广的音频矩阵。

OPPO 默认/全屏折叠与本地征询/草稿保留证据已取；2026-09-09 新增一条真实 Keyguard 提醒也已完成：锁屏/亮屏未解锁均不提前 ACK，解锁呈现后只播一次，本地收起后重入无重播。临时偏好均恢复。当前证据见 [AR04 第十四节](../design/2026-09-08-ar04-presentation-ack-implementation.md)；原五条回执保持 `065efd85e` 锚，新提醒/发布绑定 `573ad46`。服务端多 operationId 实机组合等仍缺，QA 非全绿。

以上活项是独立问题，不反推安全确认写闸未上线；同样也不能因为安全闸已上线就把它们写成已关闭。

## 6. 证据纪律

1. release、全量测试和真栈 artifact 必须绑定同一个明确 SHA；
2. 邻近 SHA 的单测不能转借给已部署 SHA；纯 docs/test 提交要明确写“HEAD 领先 release”；
3. 探针自动 PASS 不是业务正确，必须读 `fails`、trace、actions、card 和 cleanup；
4. 单次采样不能当基线；有模型方差的轮至少 `--repeat 3`；
5. 回放只证明“当前尺子会如何重判旧话术”，不证明当前系统会生成同样话术；
6. 长会话必须显式 `--expected-sha`，默认 HEAD 在 docs/test 领先 release 时会正确拒绝；
7. 商户写、支付、真实车控、数据删除和系统配置仍要单轮人工授权。

## 7. 接手路径

只处理当前 QA 活项时，按以下顺序：

1. 本页 §2、§5；
2. 安全专题设计 §12：
   [`2026-08-30-qa-safety-confirmed-write-guard.md`](../design/2026-08-30-qa-safety-confirmed-write-guard.md)；
3. MiniMax fix plan 的最终状态与部署后读数；
4. 对应 artifact 和 collector trace；
5. 需要历史原因时再查 `docs/agents-history.md` §84–§88。

不要从已完成的 superpowers implementation plan 继续顺序执行；它们是实施记录，不是当前待办。
