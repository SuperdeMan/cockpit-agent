# Android 陪伴端剩余待办总表（2026-09-14）

> 状态：**2026-09-14 梳理 + 同日推进一批**。本页是 Android App 剩余事项的唯一总表：按「谁能推进」分三栏
> （工程可独立完成 / 只缺真机时间 / 缺真人、外部条件或授权），每条给出处、卡点与直接接续入口。
> 逐批实施证据仍在各自的实施记录里，本页只指过去、不复述。
> 代码基线：梳理起点 `af7a009b`（main，clean，`target=cloud`，生产 release `43436398`）；本日推进落在
> `c036fd14`（mobile）+ `696899b5`（orchestrator）+ `cbf17b96` / `883452d2`（mobile，真机上抓到的第六、七条），**本地 commit、未 push、未 deploy**。

## 0. 一句话

Android 的工程主干（M0～M4、UX v2 B1–B5、AR01～AR09 工程批、打磨 A–G、四项体验修正、性能两轮、语音采纳）
都已落地；**剩下的不是一条主线，而是三类尾巴**：① 可独立做的工程项（本日做掉的六条半在 §1，剩六条在 §2.1）；
② 一批只缺真机时间的复验格（§2.2）；③ 一批要真人说话、旧环境、产品裁决或授权才能动的验收格与 AM5 决策（§2.3）。
接手先看 §2，别从各批实施记录里重新找「下一步」。

## 1. 本日推进（2026-09-14）

| # | 事项 | 出处 | 处置 | 判据落点 |
|---|---|---|---|---|
| 1 | 「重发」到不了：断网时在飞的轮永远「正在思考…」，重连后再等 95s 才收成「响应超时」 | [打磨批集中处理表 #5](2026-09-10-android-ui-polish-batches.md#集中处理表交用户单独授权) | **已修** `c036fd14`：断开判定（探活 `reconnectNow` / `onclose`）都摘掉旧 socket 的 onmessage、网关只重放主动消息 ⇒ 这条轮不可能再有回音，当场结算成终态——一个字没到 = error 气泡 `LINK_LOST_TEXT` + 重发；已到一部分 = 文字原样、标「网络断开，回答没有收完」+ 重发。断开期间**入队**的轮不经这里（B1-16 的看门狗暂停/重起照旧） | `core/session/store.ts::settleLinkLost`（唯一一处）；`sessionStore.test` +3、`messageBubble.test` 改 1 |
| 2 | 行车档 + 可信车载平板下冷启动落 `/settings`，常驻语音层压住设置页下 40%，行车档开关够不到 | [打磨批 A 未达项①](2026-09-10-android-ui-polish-batches.md)、Xiaomi 对照未达项③ | **已修** `c036fd14`：常驻是对话页的形态；`derivePresence` 加 `supportRoute` 输入，支持页只撤常驻那一条升层路径（收音 / 语音轮在飞 / 点开仍升），闲置回到 AR04 的浮动光球。「温深链进设置页时层不在」的不一致随之消失（之前取决于上一轮有没有 dismissed override） | `core/presence/presence.ts`（`resident && !i.supportRoute`）；`presence.test` +3 |
| 3 | 角色 ChoiceRow 无 `accessibilityState.selected`，自动化只能截图回读 | 打磨批 A 未达项② | **已在 2026-09-11 两档制迁移时闭合**（`979854a`：单选项走 `ui/Pill`，`selected` 进无障碍 `selected`）——本次核对源码后销账，不再列 | `ui/Pill.tsx` |
| 4 | 端侧新闻快捷规则把播报句当指令：「欢迎收听今天的节目，本台记者为您报道新闻。」0.97s 判 `media.play` | [语音采纳真栈核实 §3/§6-1](../reviews/2026-09-11-voice-input-acceptance-live-findings.md) | **已修** `696899b5`：新增 `runtime/reported_speech.py::is_reported_speech`（播报 / 转述**语域**标记，零领域词，源码级断言）；`classify_structured` 出口只盖写操作（与 question_shape / polarity 同一落点）；`split_and_classify_any` 拆句前先判整句（「本台记者提醒您，请打开车窗」第二段单独看是干净指令）。正常「播放新闻 / 我要听体育新闻 / 来段新闻」逐字不变 | `runtime/reported_speech.py`；`runtime/tests/test_reported_speech.py`、`orchestrator/edge/tests/test_reported_speech_gate.py` |
| 5 | Planner 无动作 / 拒识保真：播报句两轮空手后落 chitchat 安慰或 `planner.technical_failure` | 同上 §5/§6-2 | **部分已修** `696899b5`：语音来源 + 播报语域 + 两轮都没拆出步 ⇒ `Plan(addressed=False)` 走既有静默拒识，不再是「该回应但计划失败」。三条边界：文字输入不动、模型真拆出步不动、抢救出的计划不动。**没有播报语域的乘客句**（「他昨天跟我说那个项目黄了」1/3）仍归模型——文本上分不出「对我说」和「对别人说」，那是声学问题（§4 H6） | `orchestrator/cloud/planning.py`（`_register_not_addressed`，`is_voice_input_source` 一份前缀表 engine/planner 共用）；`test_planning_reject` +6 |
| 6 | 用户自己发的那句不跟到底：从设置页进对话页后列表停在历史中段，发出的问题和它的回答都落在折叠线下，屏上只剩「↓ 最新」 | 本日真机（§5，验重发终态时抓到） | **已修** `cbf17b96`：`stickToBottom` 保护的是「在读历史的人不被新内容拽走」，发送是用户自己的动作——最后一条用户消息的 id 变了就挂旗，下一次 `onContentSizeChange` 无条件 `scrollToEnd`（旗在那里消费：新行要等 FlashList 量完才滚得到），之后回到阈值判据 | `core/session/history.ts::lastUserMessageId / followOnContentChange`；`history.test` +2 |
| 7 | 「重发」发出去的是 24 字摘要不是原话：`what is the weather in Shenzhen today` 重发成 `what is the weather in S`，服务端答「没查到「S」的天气」 | 本日真机（§5 包 #2，按下重发键后抓到） | **已修** `883452d2`：`resendOf` 读的是 Dock / 留痕 / 回执共用的摘要函数（`precedingUserUtterance`，截 `SUMMARY_MAX=24`）；拆出 `precedingUserText`（全文、空白归一、仍跳过「确认 / 取消」），摘要改为它的截断；重发读全文。打磨批 F 当时到不了重发键，所以从没在真机上按过它 | `core/session/actionSummary.ts::precedingUserText`；`actionSummary.test` +1（变异：全文也截 ⇒ 恰红这一条） |

本地验证（工作树 = `696899b5`；`cbf17b96` 追加 history 24 passed、`883452d2` 追加 actionSummary 6 passed，各 tsc 0 / eslint 0；全量固定口径 `TZ=UTC0 -n 8 --dist worksteal` **8310 passed / 32 skipped / 13 warnings**，315s）：mobile jest 97 suites / **1020 passed**、tsc 0、eslint 0/0；`orchestrator/edge/tests` + `runtime/tests` **1478 passed**；`orchestrator/cloud/tests` **1330 passed / 1 skipped**；smoke_edge 13；四道门禁（skills / exemplars 域错配 1.8% / L0 strict 2/2 / capability integrity）全过。反向验证：七处变异（不结算 / 部分分支关 / supportRoute 忽略 / 出口闸关 / 混合拆分闸关 / planner 块关 / 语域表清空）各自只红在自己的用例，按字节恢复。
真机复验与固定包见 §5。

## 2. 剩余待办

图例：**E** = 工程可独立完成 · **D** = 只缺真机时间（OPPO test / Xiaomi compare，不需要真人说话）· **H** = 缺真人、外部条件、产品裁决或授权。

### 2.1 E｜工程项（可直接开工）

| ID | 事项 | 出处 | 卡点 / 判断 | 接续入口 |
|---|---|---|---|---|
| E-01 | 渲染放大：Provider 整份 store 订阅、FlashList `extraData` 每渲染新数组、`usePresence` 未 memo、挂起确认时每秒整树重渲 | [性能评审 §2.4 / §6-10](../reviews/2026-09-12-android-performance-latency-review.md) | 评审自判低优先级（50 条记录时 JS 线程 ≤2.5%）。⚠ `app.config.ts` 已开 `experiments.reactCompiler`，闭包与字面量的自动 memo 可能已经覆盖了一半——**先量再改**：真机 `top -H` 的 `mqt_js` 在 50 条记录 + 一次 speech_delta 风暴下的读数 | `features/assistant/AssistantProvider.tsx`、`features/chat/ChatScreen.tsx`、`usePresence.ts` |
| E-02 | Maestro 03（断网入队恢复补达）在飞行模式段挂 driver，两包三趟 | [打磨批集中处理表 #6](2026-09-10-android-ui-polish-batches.md#集中处理表交用户单独授权) | App 侧两条假设已被推翻（树不每秒变、光球不是变量），剩 driver 自己在飞行模式下 hierarchy 取数不回。修法在 flow 不在 App：把 `setAirplaneMode` 换成不经 driver 的断网（runner 用 `cmd connectivity airplane-mode` 分三段跑，`launchApp: stopApp: false` 接续），并把「关飞行模式后 Tailscale 不自愈」写进 runner（`am force-stop com.tailscale.ipn` + 重启）。补达语义本身已有一次真机旁证。**本日顺手改了 flow 本身的两处**（未在设备上跑）：① 计时器不再用 `waitForAnimationToEnd`——光球 09-13 起空闲 30s 静置，它会提前返回；② 末尾加 `bubble-resend` 不可见断言——§1 #1 之后，帧写进死 socket 也会让 `msg-pending` 消失，那不是补达 | `mobile/e2e/03-offline-resend.yaml`、`mobile/e2e/README.md` |
| E-03 | AR09 P1 长内容（500 条本地消息）缺离线灌数 harness | [余项收口 §7 E-05](2026-09-10-ar-residuals-closeout.md) | 做法已定（仿 `card-gallery` 加只读调试路由，用真的 chat 列表渲 N 条合成消息）。价值有限：持久化上限 50 条把「长记录」按住了，只有单会话内长聊才会超；与 E-01 一起做才划算 | `src/app/`（dev 取证屏族） |
| E-04 | catalog 预筛 `PLANNER_CATALOG_TOP_K` 20→8 / 前缀缓存（prompt 10.5k token 里 13.6k 字符每轮相同、`cache_hit` 0） | 性能评审 §3.3 / §6-9 | 云端；要过落域门禁（`eval_intent_adversarial --suite gate`）与 M1a 顺序契约的重新 A/B。规划 LLM p50 2.29s 是现在整条链路最大的单段 | `orchestrator/cloud/context.py`、`planning.py`（catalog 渲染） |
| E-05 | manifest 加「必填槽」声明 → 「只在必填槽缺失时才重试」 | 性能评审 §9.2 | 离线 A/B：14 轮强制重试里 8 轮纯代价、3 轮有益，但 `slots: [query, limit]` 只是名字列表，表达不出更细的规则。新字段要走 SDK loader → Registry round-trip → Step 装配 → pending serialize 整条链（CLAUDE.md §3） | `agents/_sdk/manifest.py`、`registry/`、`orchestrator/cloud/retry_policy.py` |
| E-06 | 语音采纳实施记录 §3 保留的边界：端侧直接命中 / 挂起续接不经拒识、外部播报参照、`RejectPolicy` 自适应收紧 | [语音采纳实施 §3](2026-09-11-android-voice-input-acceptance.md) | 本日 #4 把「端侧直接命中」里播报语域这一族堵上了；其余仍是设计边界，不是缺陷。`RejectPolicy` 自适应要先有真实拒识分布（H6）再定参 | `mobile/src/core/voice/handsFree.ts`、`orchestrator/cloud/engine.py` |

### 2.2 D｜只缺真机时间

| ID | 事项 | 出处 | 需要什么 | 接续入口 |
|---|---|---|---|---|
| ~~D-01~~ | 本日三项真机复验：重发终态 / 支持页不再被常驻层盖住 / 行车档 Pill 44/56 | §1 #1 #2 + 四项修正 §7.3 | **本日闭合**（§5，包 `696899b59`）；顺带抓到并修掉「用户自己发的那句不跟到底」（`cbf17b96`，包 #2 复验） | §5 |
| ~~D-02~~ | 输出上下文空闲 15s 挂起没有真机证据 | 性能评审 §7.2 | **本日闭合**（§5）：播完后 AudioTrack 归零并消失，下一轮照常出声；首音同题对照未做 | §5 |
| ~~D-03~~ | 手机侧文字轮分段时延一格没取到 | 性能评审 §9.7 | **本日取到一格**（§5）：`发送→排定` 3061ms。装置：Maestro driver 在对话页两次挂死于 `viewHierarchy` / `isWindowUpdating`，但 `inputText` 已把中文打进输入框——之后用 adb 点发送即可，不必等 driver | §5 |
| D-04 | 折叠机桌面姿态舞台内嵌地图 | 四项修正 §7.3 | 用户 2026-09-14 口述「会出地图」，无截图；要一张 Xiaomi 90° 姿态下的 `stage-map` 截图才算证据 | Xiaomi compare |
| D-05 | AR02 完整设备矩阵：视觉失败 / 超时 / 进程中断，六态免唤醒 × 视觉并发，S2S 并发 | [AR02 实施记录](2026-09-07-ar02-capture-privacy-implementation.md) | 只缺时间；改原生实现要重记 APK 身份 | AR02 记录末节 |
| D-06 | AR03 余项：多段段链（prod 无段数出口，要构造）、横屏 × 层内停止键交叉 | [AR03 实施记录](2026-09-08-ar03-stop-playback-landscape-implementation.md) | 横屏只有 Xiaomi 外屏够得到（R1） | AR03 记录第十节 |
| D-07 | AR05 V07 设备权限拒绝未验；V12 横屏未取证 | [AR05 §9.5](2026-09-09-ar05-structured-contracts-implementation-plan.md) | 撤麦克风权限后的 `device.permission_denied` issue 卡；横屏同 D-06 | `probe_ar05_matrix.py` |

### 2.3 H｜缺真人、外部条件、产品裁决或授权

| ID | 事项 | 出处 | 缺什么 | 备好的产物 |
|---|---|---|---|---|
| H-01 | 本日两个提交的 push 与 deploy（云端改动：边缘快路径语域闸 + planner 不受话裁决） | §1 | `git push` 与 `deploy --apply` 各需单独授权；push 前列 `origin/main..HEAD`（此刻恰两条） | 全量本地验证已过（§1）；部署后按 [语音采纳核实 §4](../reviews/2026-09-11-voice-input-acceptance-live-findings.md) 的 18 轮矩阵复跑，播报句两格期望从 1/3、3/3 变 3/3、3/3 |
| H-02 | E-03 KWS 唤醒率 A/B（AR07） | [余项收口 §7](2026-09-10-ar-residuals-closeout.md) | 真人近场 A/B/C 各 10 次 | 参数入口、回读、四分栏计数器（AR07 仪器） |
| H-03 | E-04 首音声学校准（AR08） | 同上 | 外部时基 + 真人：同一时基录「说完」与「扬声器首音」 | `attachMeasuredOnset` 入口、分桶统计 |
| H-04 | AR05 V08 真人听音（ASR/S2S 回退、TTS 无声 / 部分失败） | AR05 §9.5 | 真人 | 离线按成因分档已判红 |
| H-05 | E-07 旧 release 部署 + 一次 Redis 重启（AR05 V06 挂起恢复保真、V10 新客户端 + 旧服务端） | 同上 | 一个旧 release 环境与 Redis 重启授权（生产不允许） | `probe_ar05_matrix.py` |
| H-06 | 语音拒识声学验收（乘客闲聊 / 新闻播报 / 本 App 播报中按住） | [语音采纳 §4 协议](2026-09-11-android-voice-input-acceptance.md) | 真人 + 背景播报源；文字注入样本推导不出说话人 | 协议表已定；本日 #4/#5 只解决了文本语域那一半 |
| H-07 | E-08 AR10 固定包五人 UX 验收 | [AR10 准备材料](2026-09-10-ar10-acceptance-preparation.md) | 五位参与者 + 一个冻结候选包 | 入场条件 / 冻结表 / 脚本 / 计分 / 报告骨架 |
| H-08 | 规划强制重试 `salvage_wire_accepted` 的云端 A/B | 性能评审 §9.2 | 门禁轨 L1/L2 在本进程装配 PlanBuilder，云端 `.env` 不在路上 ⇒ 要 `target=local` 起本地栈或打 gRPC 隧道，~1,400 次规划 | 裁决暂为「保留」；E-05 做完这条 A/B 才有意义 |
| H-09 | AM5 五包（账号 / 公网投递 / 签名更新 / 隐私 SDK / 观测准入） | [AM5 交付计划](2026-09-10-android-m5-delivery-plan.md) | §5 六项决策 D1–D6（IdP、首发范围、后台触达、签名 / 渠道、系统入口、遥测）+ AR10 结论回填 | 每包已是可派工任务（输入 / 输出 / DoD / 工作量依据） |

### 2.4 R｜已裁决、不改（防止被当成待办）

| ID | 事项 | 裁决 |
|---|---|---|
| R-01 | OPPO 两块屏都进不了 `driving-landscape`（外屏宽只到 medium、内屏高不 compact） | AR03 定性为**支持范围**问题，带进 AR10 裁决；横屏格一律在 Xiaomi 外屏取 |
| R-02 | 横屏行车档下 Dock 落在 Composer 之下（与竖屏相反） | B4 横屏可达性布局，记录不改 |
| R-03 | AR05 V09 技术失败 2/7 不可稳定复现 | 「单次采样不当基线」，不计闭合也不再追 |
| R-04 | 光球视觉 | 用户 2026-09-13 裁决「空闲静置、视觉一帧不改」，已落 `43436398` |
| R-05 | 规划强制重试 | 离线 A/B 不是空转（3/14 有益），保留；见 H-08 / E-05 |

### 2.5 本日顺带发现、未处理

| ID | 事项 | 出处 | 去向 |
|---|---|---|---|
| N-01 | 英文问时间「what is the time in Shenzhen now」回 `unsupported datetime format`（内部错误串直出）| §5 包 #2 `probe-follow.log` | 云侧 / 时间类 Agent 的错误话术面；与 AR05「内部错误串不许吐给用户」同族，另立项 |
| N-02 | 常驻语音层（C 身份）里最后一轮是 error 气泡时，红字在层内容区底部被裁一行 | §5 `chat-driving-tablet-resident-696899b59.png` | 语音层内容区对长错误文案的布局，归下一轮打磨 |

## 3. 不要再从这些地方找「下一步」

- `mobile/README.md` 的 AR04 段仍写「服务端多 operationId 实机组合未闭合」——它已由 AR05 V01 在真机闭合（「待处理事项（3）」三条真实 operationId 并存、取消末项不串账），本页不再列。
- 各批实施记录末节的「未达项」已全部归入 §2；若某条在两处状态不一致，以本页为准并回改那一处。
- 已完成的 implementation plan 只作实施证据（AGENTS.md §8）。

## 4. 本日明确没做的事

- 没有 push、deploy、改 `.env` / 安全组 / Tailscale / CI；没有动 Xiaomi 对照机；
- 没有取得任何真人语音、唤醒率或首音读数；
- 没有招募 AR10 参与者；没有替用户做 AM5 的六项决策；
- 乘客句（无播报语域）的漏拒仍归模型，本日没有给它加词表——文本上它与「用户在向助手转述」不可区分。

## 5. 真机复验（OPPO test 机，固定包）

包 #1：`xiaozhou-companion-prod-release-696899b59-20260914-1403.apk`（源 `696899b5`，clean tree，`-CompileJobs 3` + `-Xmx2048m`，12m55s），
APK SHA-256 `bc21fa78…0da2` 本地 `Get-FileHash` 与设备 `pm path` + `sha256sum` 逐字相同，flags 无 DEBUGGABLE，`lastUpdateTime 2026-09-14 14:03:48`，
`/turn-timeline` 底行 `v0.1.0 · prod · 696899b59 · 2026-09-14 13:49`。证据目录 `%LOCALAPPDATA%\car-agent\artifacts\TODO-20260914-134815-696899b5\`
（探针脚本 + `<状态>-696899b59.png/.xml/.json` + 各 `probe-*.log`）。系统设置只动了飞行模式（探针自己开关并确认关闭）；App 内开关
（减少动效 / 行车档 / 角色 / 播报档）逐次回读并还原；Maestro driver 跑完即 force-stop。

| 格 | 结果 | 证据 |
|---|---|---|
| 重发终态（§1 #1） | ✅ 在线发一句、1.7s 后开飞行模式：+31.8s 仍「正在思考…」，**+36.9s 探活判死那一刻**胶囊变「已断开 · 消息会排队」、`msg-pending` 消失；重连后气泡为红字「发送状态未知：网络断开前没有收到回音，可以重发。」+ 「重发」键，**没有**「响应超时」、没有第二次结算（`chat-after-reconnect-bottom`）。⚠ 探针第一趟没在离线期间截到重发键：列表停在历史中段、新气泡在折叠线下（屏上只有「↓ 最新」）——那是下一行的缺陷，不是结算没发生 | `probe-resend.log`、`chat-airplane-90s`、`chat-after-reconnect-bottom-696899b59.png` |
| 用户自己发的那句要跟到底（本日新发现） | **缺陷已修** `cbf17b96`（`history.ts::lastUserMessageId / followOnContentChange`）：从设置页进对话页后列表停在历史中段，发出的问题与回答都落在折叠线下。包 #2 复验见下表 | `chat-airplane-90s-696899b59.png`（「↓ 最新」+ 老回答） |
| 支持页不再被常驻层盖住（§1 #2） | ✅ 可信车载平板 + 行车档手动开：force-stop 后冷深链 `/settings` → `voice-sheet` **无**、`assistant-presence` / `assistant-orb` 有；深链 `/` → 对话页 `voice-sheet` 有（常驻只撤支持页）；温深链回 `/settings` → 仍无；行车档开关滚进可点带 `(786,1285)-(914,1359)` 并成功关掉（上一轮「够不到」的那枚） | `settings-driving-tablet-cold / chat-driving-tablet-resident / settings-driving-tablet-warm-696899b59.png` + 同帧 xml、`probe-support-sheet.log` |
| 行车档 Pill 44/56（四项修正未验格） | ✅ 状态画廊 `driving-answer-B`（driving=true）胶囊外框 **56.0dp** / 药丸 **44.0dp**，同屏泊车样本 `speaking` / `thinking` 48.0 / 36.0（density 2.75）；真实对话页行车档下发送键 / 光球 56.0（泊车 44 / 56，发送键泊车 44 是 Composer 既有写法） | `probe-gallery-pills.log`、`probe-pills-driving.log`、`state-gallery-driving-pills-696899b59.png` |
| 空闲挂起（D-02） | ✅ 播报「总是」发一轮：播报中 `AudioTrack` 6.8%，播完 +15s 降到 3.4%、+27s 起 **0.0%**，静置后 `top -H -n 3` 里该线程**不存在**（改前常驻 4.6–6.8%）；再发一轮 `AudioTrack` 回来 7.1% 并出声。⚠ 首音「不变差」没有同题对照（两轮语料不同，时间线又被 set_switch 的 force-stop 清掉），只证明「挂起后下一轮能出声」 | `probe-audio-idle-2.log` |
| 手机侧文字轮分段时延（D-03） | ✅ 取到一格：Maestro `inputText`「深圳明天天气怎么样」+ adb 点发送（driver 随后在 `isWindowUpdating` 挂死两次，中文已进输入框）：`发送→排定(文本轮)` **3061ms**（`request_sent +0 / tts_text_sent +54 / first_pcm +3060 / play_scheduled +3062`），终态 `play_ended`，trace `5ad5904fc8cc0acb`；直连路径、release `43436398` | `zh-turn-timeline-696899b59.png`、`send-and-timeline.log` |

装置坑（本日）：① 探针的可点带上限沿用了「常驻层顶边 1150」——层不在了之后它让开关滚不进带里；改成 1700（右下角浮动光球的带之上），
页面本身留了 `PRESENCE_LANE` 底边距，不是产品缺陷；② `set_switch.py` 会 force-stop App，`/turn-timeline` 的内存时间线随之清空，读时间线要在它之前；
③ `top -H` 的 THREAD 列含空格（`OkHttp TaskRunn`），按行尾进程名锚定才取得到线程名。

包 #2：`xiaozhou-companion-prod-release-cbf17b96e-20260914-1520.apk`（源 `cbf17b96`，clean tree，12m28s），APK SHA-256 `dc931f81…0955` 端本一致，
flags 无 DEBUGGABLE，`lastUpdateTime 2026-09-14 15:21:41`；证据目录 `%LOCALAPPDATA%\car-agent\artifacts\TODO2-20260914-150649-cbf17b96\`。

| 格 | 结果 | 证据 |
|---|---|---|
| 用户自己发的那句跟到底（§1 #6） | ✅ 把列表拖到历史中段（离底超过一屏，`follow-before-send` 截图停在贵阳老回答）→ adb 发一句 → 发出后**用户气泡在视口内**、回答到达后仍在底部；两趟一致（第一趟 `what is the time…`，第二趟 `what is the weather in Guangzhou today`）。⚠ 对照臂「不发时不被拽走」没取到读数：「↓ 最新」胶囊只在离底期间记录变长时才出（设计如此），单靠拖动不出胶囊 | `follow-before-send / follow-after-send / follow-after-answer-cbf17b96e.png`、`probe-follow*.log` |
| 重发终态（离线期间就能看到重发键） | ✅ 同一探针在包 #2 上**+37.0s 判死那一刻**同帧取到 `bubble-resend` + 「发送状态未知：…可以重发。」（包 #1 因列表没跟底截不到）；重连后仍是重发键、无「响应超时」 | `chat-resend-offline-cbf17b96e.png`、`chat-after-reconnect-cbf17b96e.png`、`probe-resend.log` |
| 按下「重发」 | ✅ 旧气泡标「已重发」、新请求发出并得到回答（`chat-resend-after-press-cbf17b96e.png`）。装置：`input tap`（零时长）在重发键上不生效，120ms 的 swipe-tap 才动（同 HyperOS 坑账）。**抓到 §1 #7**：重发出去的是截断的摘要 | `chat-resend-after-press-cbf17b96e.png` |
| 重发全文（§1 #7） | ✅ 包 #3 上按最新那枚「重发」（120ms）：旧气泡「已重发」、新用户气泡是**全文**「what is the weather in Shenzhen today」（包 #2 上同一动作发出的是「what is the weather in S」）；本轮云端答的是 `planner.technical_failure` issue 卡（生产 `43436398`，英文语料，与本页无关） | `chat-resend-full-text-883452d29.png`、`press-last-resend.log` |

包 #3（**当前 OPPO 常驻包**）：`xiaozhou-companion-prod-release-883452d29-20260914-1600.apk`（源 `883452d2`，clean tree，12m37s），APK SHA-256 `7599e9f5…f15a` 端本一致，
flags 无 DEBUGGABLE，`lastUpdateTime 2026-09-14 16:01:41`；证据目录 `%LOCALAPPDATA%\car-agent\artifacts\TODO3-20260914-154639-883452d2\`。
⚠ 探针坑：历史里留着前两个包结算出的 error 气泡（带重发键、按设计持久化），`fx.find(rid='bubble-resend')` 取到的是最上面的旧键 ⇒ 要按 y 最大的那枚；
装置一律先数基线再判「新出现」。设备收尾：飞行模式 0、减少动效关、Maestro driver 已 force-stop、App 内其余设置未动。

顺带记一条不属于 Android 的读数：英文问时间「what is the time in Shenzhen now」服务端回了一句 `unsupported datetime format`（原样落到用户话术里）——归云侧 / Agent 的错误文案面，见 §2.5。
