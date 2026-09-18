# Android 流式文字上屏：匀速 reveal + 长回答不再被硬上限掐断（2026-09-18）

> 状态：**已实现、已 push、已 deploy `3abd325e`（status ok / verify verified）、OPPO 清洁包 `3abd325ea` 真机 A/B 闭合（§5）**。
> 用户口述两条：① 回答文字「一大段突然跳变上屏」；② 长文本「生成展示不全」。
> 证据绑定：设备 = OPPO PEUM00 / Android 14（test 机）、常驻包 `990466d6d`（2026-09-17 16:12）；云端 `target=cloud`、
> 生产 release `f1a99063`；服务端读数取自 collector（`/api/sessions` / `/api/turns/{trace}`）；PC 探针 `scripts/probe_stream_cadence.py`、真机取证
> `mobile/e2e/tools/stream_cadence_probe.py`（ui / prep / frames 三个子命令），本页只留读数。

## 0. 一句话

两条症状是两件事：**「跳变」是服务端 delta 成簇到达、客户端原样即时上屏**（不是 JS 线程卡住），修法是把**显示**与
**记录**分开、显示按节拍匀速追（`core/session/streamReveal.ts`）；**「展示不全」是 chitchat 把 `max_tokens` 当长度控制用**
（standard=220 token），用户明确要长内容时模型越过提示语、被硬上限在句中掐断，修法是长度只由提示语约束、上限只做兜底
（300/600/900），并把 D0 流式的 gRPC 截止从 30s 放到 60s，免得放长之后换成被编排层掐断。

## 1. 症状 ② 的证据：截断发生在服务端，且只发生在长请求上

collector 里 App 最近几轮（`app-wyi61a` 09-18 00:07–00:12，PTT）：

| 轮 | 输入 | 路径 | speech 字数 | 结尾 |
|---|---|---|---|---|
| `fd0d5d3165ca537f` | 给我讲一个很长的故事。 | D0 stream，chitchat.talk（depth=deep） | 357 | `…然后解了缆绳。\n\n离开港口` |
| `9c787157a4e0e9e1`（09-17 `app-yihvnn`） | tell me the history of Shenzhen in detail | 同上 | 426 | `…如今，深圳是中国` |
| `53b3dfa5bfde1165` | 给我介绍一下深圳的历史。 | 同上 | 160 | 完整句 |

两条被掐断的都停在句中；chitchat 的 `_LENGTH["standard"] = (220, "回答控制在两三句话内。")`，App 默认 `answerLength=standard`。
近 200 个会话里 66 轮 chitchat：字数 p50 29 / p90 71 / p95 293 / max 426，**≥300 字的 3 轮里 2 轮被掐**——上限只在用户
明确要长内容时才被撞到，而撞到永远只能产出半句。final 与流式文本逐字一致（PC 探针三次 `final == streamed: True`），
客户端没有再截一刀。

## 2. 症状 ① 的证据：簇状到达，客户端不忙

PC 探针（同一 WS 契约、同一云栈、`memory_enabled=false`）：

| 问句 | 首片 | 片数 | 簇内间隔 p50 | 簇间 p90 / max | 每片字数 | 速率 |
|---|---|---|---|---|---|---|
| 给我讲一个很长的故事。 | +13.3s | 31 | 2ms | 147 / 187ms | 3.4（max 11） | 116 字/s |
| 给我介绍一下深圳的历史。 | +4.5s | 23 | 5ms | 44 / 74ms | 3.7（max 13） | 286 字/s |

⇒ 模型的 SSE 是「几片一簇（0–5ms）、簇间停 50–190ms」，网关与端侧编排逐事件转发、不攒。

真机（OPPO，文字发送同一句，App 内「减少动效」强制开 ⇒ 光球 / 光标 / 思考点全静帧，`dumpsys gfxinfo framestats`
里每一帧都是**内容变化**）：流式约 6s 内 55 帧、帧间隔中位 ~110ms（分布 100–300ms，个别 312 / 438ms），
即每 ~110ms 长出十来个字、停顿后一次长出二三十个字；主 JS 线程（`mqt_v_js`）流式期间 25–52%、其余时间 0–8%。
旧包每片 delta 都 `setState` ⇒ 整棵 ChatBody 重渲一次（约 9ms），JS 有余量，屏上节奏就是服务端节奏。

## 3. 修法

### 3.1 客户端：显示与记录分开，显示按节拍追（mobile）

- `core/session/streamReveal.ts`（纯函数）：`revealAdvance(state, text, dt)`。**每次新内容到达**时按「此刻积压在
  `REVEAL_LAG_MS`=240ms 内摊平」定速（不低于 `REVEAL_MIN_CPS`=40 字/s），到达之间匀速；每拍（`REVEAL_TICK_MS`=33ms）
  至少 1 个字、不越过真实文本；真实文本不是已显示文本的延长（final 剥 markdown、气泡复用）⇒ 直接跳到位。
- `features/chat/useRevealedText.ts`：挂载直出全文（历史恢复 / 复用 / 层升起时已流出的部分不重放）；挂载后文本**延长**就追——
  逐片流式增量、只在 final 里到达的整段（unary Agent）、executor 整步话术、错误文案同一种走法（§7 之后；此前只追流式中长出来的）；
  换 id 直出；用户气泡不追；不看 reduce-motion（不是循环动效，它替代的「一段一段蹦」对动效敏感的人更糟）。节拍用 `setInterval`，只在有积压时存在。
- 消费点两处：`MessageBubble` 的助手正文、`VoiceSheet` 的回答区；光标跟着「还在长」走（流式中或显示未追到尾）。
- `Msg.text`、TTS（`speech.delta`）、历史持久化、`turnView` 一个字不延迟——它们读的仍是记录。

### 3.2 服务端：上限只做兜底，截止放宽（agents/chitchat、orchestrator/cloud）

- `agents/chitchat/src/agent.py::_LENGTH`：short/standard/detailed 的 `max_tokens` 140/220/440 → **300/600/900**，提示语不变；
  流式调用显式 `timeout=_STREAM_TIMEOUT_S=55`。
- `orchestrator/cloud/clients.py::AGENT_STREAM_TIMEOUT_S`（env `CLOUD_AGENT_STREAM_TIMEOUT_S`，缺省 60）成为
  `call_agent_stream` 的缺省截止（此前 30）：MiniMax-M3 实测 ~40 token/s、尾部 ~20 token/s，900 token 在尾部要 45s；
  超过截止走的是 D0「只流了话术」那档——屏上已流出的整段会被「抱歉，刚才没说完」替掉。客户端看门狗 95s、规划 p90 4s，
  60s 留得下。T2 按 `step.latency_budget_ms` 显式传，不受影响。

### 3.3 不做的

- 不改 D0「只流了话术」那句话术（`test_d0_speech_then_lost_final_keeps_its_wording` 钉着，自进化兜底话术模式认它）。
- 不给 `CompleteChunk` 加 `finish_reason`：撞上限的句中截断现在无法从流里判出来；上限放到兜底位置之后它应当极少发生，
  出现第二例再做。
- HMI 的 ChatView 不动（同 `final` 替换语义，未观察到同一症状）。

## 4. 验证

- mobile：`streamReveal.test`（5）+ `revealedText.test`（5）新增；全量 jest **105 套件 1095 全绿**；tsc 0、`eslint . --max-warnings 0` 0。
- Python：`agents/chitchat/tests` + `orchestrator/cloud/tests/{test_stream_state,test_loop,test_engine_stream,test_engine_escalate}`
  140 passed。全量固定口径 pytest **未跑**（机器 commit 只剩 ~1GB，见 §5）。
- 真机 A/B：同一句「给我讲一个很长的故事。」、同一装置（framestats + `top -H`），见 §5。

## 5. 真机 A/B 读数（OPPO PEUM00，清洁包 `3abd325ea`）

同一句「给我讲一个很长的故事。」、同一装置（App 内减少动效强制开 ⇒ framestats 每帧 = 内容变化；`top -H -d 1` 采主 JS 线程）：

| 包 | 回答 | 流式期间帧间隔 | 停顿后的表现 | 主 JS 线程（流式期间，每秒） |
|---|---|---|---|---|
| 旧 `990466d6d`（§2 基线） | 297 字 | ~6s 内 55 帧，**中位 ~110ms**，100–300ms 一步、每步长出十来个字 | 312 / 438ms 停顿后一次长出二三十字 | 25–52% |
| 新 `3abd325ea` 第 1 趟 | 208 字（trace `ade289641cd9aa24`） | ~3s 内 ~75 帧，**稳定 15–20ms**（少数 30–50ms） | 391ms 停顿后仍按节拍逐字追出 | 27–60% |
| 新 `3abd325ea` 第 2 趟 | **727 字**（trace `e11074577e7e2e6b`；同一问法昨天被掐在 357 字） | ~9s 内 ~190 帧，**中位 17ms**（9–40ms） | 392ms 停顿后同上 | 36–79% |

⇒ 屏上的节奏从「服务端簇间隔」变成「reveal 节拍」：每拍一帧文字 + 一帧跟底滚动（≈16ms 一帧），一簇二三十字在 240ms 内摊平，
不再一次蹦出来；代价是流式期间 JS 线程从 25–52% 升到 36–79%（每秒 30 次叶子级 Text 更新 + scrollToEnd），仍有余量。
第 2 趟 727 字完整收尾（「（待续）你想听下一段吗？」），同一问法昨天在 357 字被硬上限掐断——症状 ② 在真机路径上同样闭合。
减少动效开关取证后已改回 false（`set_switch.py` 回读 `after=False`）；开着动效再跑一趟只看 JS 线程（帧被光球动画淹没）：短回答 19%。

### 5.1 候选包怎么来的：构建机 commit 耗尽，接续 + 单 clang 才出包

| 尝试 | 参数 | 结果 |
|---|---|---|
| #1 | `-Release -Variant prod -CompileJobs 3` | Gradle daemon JVM 在 cxx-staging 阶段崩：`Native memory allocation (mmap) failed to map 177MB`（`hs_err_pid20112.log`） |
| #2 | + `GRADLE_OPTS` 小堆（`-Xms128m -Xmx2048m -XX:MaxMetaspaceSize=512m -XX:ActiveProcessorCount=2`，Kotlin in-process） | daemon 活了，**prefab 子 JVM** 崩（同一形态，532MB） |
| #3 | + `JAVA_TOOL_OPTIONS=-Xms32m -Xmx640m -XX:MaxMetaspaceSize=256m` | 过了 prefab，`:app:buildCMakeRelWithDebInfo[arm64-v8a]` 64/85 时 clang 起不来：`(0x5AF)` = Windows 1455「页面文件太小」 |
| 接续 ×5（dirty 树） | 镜像目录内 `gradlew assembleRelease`（同 init script / -P、`--max-workers=1`、重用 daemon） | 每次增量前进后同样 0x5AF；被 Claude Code 以内存不足终止 |
| 清洁树 #1（用户释放内存后） | `-CompileJobs 3`，不封堆 | 物理内存 7.7GB 空闲但 **commit 仍只剩 1.5GB**（页面文件到托管上限）：v7a 编译时 clang `0xC000001D`（`ucrtbase!abort`，clang 内部分配失败） |
| 清洁树接续 | 把生成的 `<abi>/CMakeFiles/rules.ninja` 里 `pool compile` 的 `depth` 3 → **1**（不换配置哈希、不动仓库；完成后改回 3）、`--max-workers=1`、同一构建身份（`XIAOZHOU_BUILD_SHA=3abd325ea` / `_AT=2026-09-18 12:10`） | **BUILD SUCCESSFUL 11m10s**；按脚本 §6 逐项验包：KWS / ORT `.so`、内嵌 bundle、`app.config` variant=prod build=3abd325ea、签名 SHA-1 `5e8f1606…f625` 与 README 一致；落 `D:/Android/builds/apk/xiaozhou-companion-prod-release-3abd325ea-20260918-1233.apk`，SHA-256 `4fad2538…5795` 设备端逐字节相同、非 DEBUGGABLE、`lastUpdateTime 2026-09-18 12:36:42`。它现在是 OPPO 常驻包 |

成因：整机 commit 上限 69.1GB（RAM 31.6 + 页面文件 38400MB，后者已到卷大小 1/8 的托管上限），`WindowsTerminal` 一个进程私有 commit 34GB；
物理内存释放不等于 commit 释放。三个并发 clang（heavy folly 模板各 0.5–0.8GB）+ daemon 就撞顶，单 clang 就过。
`mobile_device.ps1 -Install` 本身装机成功但回读阶段挂住（10 分钟未返回，装机时刻 12:36:42 已落），验包改为手工读 `pm path` + `sha256sum`。

## 6. 服务端发布证据

push `4566c0e7..3abd325e`（四条：`29b9f0ab` mobile / `a1b680e9` chitchat+cloud / `9d33f7df` docs / `3abd325e` 探针）；deploy dry-run 零阻断 → apply `submitted`（基线 `f1a99063`）
→ status `ok`、`release_sha` = `running_release_sha` = `3abd325e` → verify `verified`（`20260918T041530Z-3abd325.json`，`minimax:MiniMax-M3`，lock `e2e`，83s）。
部署后 PC 探针：「给我讲一个很长的故事。」standard ×3（51 / 85 / ~100 字，模型自己收短）与 detailed ×1（**流 17.5s、约 1500 字**）全部句尾完整、`final == streamed`；
「请详细介绍一下深圳的历史，至少五百字。」136 字完整。detailed 那条在旧上限 440 token 下必然被掐。

## 7. 追加（2026-09-18 下午）：联网搜索「一次性全量打印」——改派后的 info.search 走了 unary

用户复测：联网搜索结果整段一次上屏，与聊天回答的逐字流不一致。collector 里 `app-2652cv` 12:59–13:00 三轮（「讲一下深圳的历史」
「讲一个很长的故事」×2）的 span 是 `chitchat.talk:stream` → `info.search:unary`，speech 783 / 497 / 311 字：chitchat 流式起步、判定要联网、
`<search>` 改派 → `_run_escalated` 一律经 executor 走 **unary** → 整段只在 final 里到达；而直连的 `info.search` 计划（09-13 `app-tojcn8`
三轮）走 D0 是逐片流的。同一个 Agent 两条路两种流法，用户看到的就是「有时流、有时蹦」。

修法（服务端，`orchestrator/cloud/engine.py`）：把 D0 的单步流式直通抽成 `_stream_single_step`（过程区 running / 槽位解析 / 流事件 /
response_only 闸 / Verifier 对账 / 来源 / span / 过程区 done），`_run_escalated` 对**单步云端、不需确认**的改派步走同一份（资格条件与 D0
逐字相同；edge 步 / 需确认步照旧 executor）；流零输出 ⇒ 回退 executor unary（与 D0 同款）；流了话术没 final ⇒ D0 同一档处置
（`_STREAM_LOST_FINAL_SPEECH` 一句共用，字不动）。`test_engine_escalate` 契约 h 钉住：改派步经 stream、slots / thinking 照旧带出、
零输出回退、edge 写步仍 unary。

修法（客户端）：`useRevealedText` 从「只追流式中长出来的」改为「任何延长都追」，`streamReveal.revealCps` 加上限 `REVEAL_MAX_CPS`=400 字/s——
只在 final 里到达的整段（天气 / 新闻 / 赛事这类 unary Agent、executor 整步话术）也是扫出来的（700 字约 1.8s，远快于读速），
不再一下蹦出；≤96 字的簇仍在 240ms 内追平。新闻的话术是一次 JSON 归纳后拼出来的清单，服务端无法逐片流，靠这一条与流式观感对齐。

验证：Python `orchestrator/cloud/tests` + `agents/chitchat/tests` 1394 passed；mobile 全量 jest 105 套件 1096、tsc 0、`eslint . --max-warnings 0` 0。

### 7.1 发布与真栈证据

push `0021ff86..a4b47748`（`69f6d986` cloud / `44d88bd2` mobile / `a4b47748` docs）；deploy dry-run 零阻断 → apply submitted（基线 `3abd325e`）→ status ok、
`running_release_sha` = `a4b47748` → verify verified（`20260918T054145Z-a4b4774.json`）。

部署后 PC 探针（`memory_enabled=false`）：直接规划成 `info.search` 的两轮（「最近有什么好看的电影推荐？」459 字 / 「现在的油价是多少？」496 字）
都逐片流（6 片 ×76 字、~1s 一片 / 346 片 ×1.4 字——同一条合成路径，片的粗细是 MiniMax SSE 自己的方差，客户端 reveal 两种都抹平）；
「深圳最近发生了什么大事？」规划成 `info.news`，381 字整段在 final 到达（news 是一次 JSON 归纳，服务端无法逐片），由客户端 400 字/s 扫出。
探针没有复现 chitchat → `<search>` 改派（没有用户记忆上下文时模型都直接回答），改派路径的真栈证据靠真机（§7.2）。

### 7.2 新包真机（OPPO，清洁包 `a4b477489`，脚本一次构建成功 + 验包，装机 14:50:20，设备端 SHA-256 与本地一致、非 DEBUGGABLE）

同一装置、同一句「给我讲一个很长的故事。」三趟（会话 `app-yr17vm`）：89 / 289 / 283 字，三趟模型都直接回答、**没有触发改派**（今天中午你那三轮
是 chitchat → `<search>` 改派，模型的判断随记忆上下文与措辞漂），所以改派路径的真机帧读数本轮没拿到；它现在与 D0 走的是同一个函数
（`_stream_single_step`），单测契约 h 钉住。三趟的显示节奏与上一批一致：流式期间帧间隔 9–60ms 逐字追出、中位 ~20–30ms，服务端停顿（272 / 364ms）后不再蹦；
JS 线程流式期间 19–59%。取证后 `reduceMotionForce` 已改回 false（回读 `after=False`）。OPPO 常驻包现为 `a4b477489`。

要在真机上亲眼看改派路径：用中午那种措辞（「the. 讲一下深圳的历史。」/「讲一个很长的回事」）多试几次，collector 里该轮 span 出现
`chitchat.talk:stream` + `info.search:stream`（此前是 `:unary`）即命中；屏上应看到「联网检索」过程条之后文字逐片流出。

## 8. 追加（2026-09-18 傍晚）：搜索轮「吐一批、卡一下」——到达成批，reveal 要跟到达速率走

用户在新包上复测联网搜索（`app-iqkgrw` 16:19「给我讲一下深圳的历史，网上搜索下」，直接规划成 `info.search`、D0 流、929 字、合成 12.7s）：
文字是流的，但一批一批、批间有顿感。取证：

- 服务端源头是细粒度的：同一句 PC 探针两次 174 片（均 4.8 字，p50 间隔 13ms，两次 400–589ms 停顿）/ 590 片（均 1.4 字，p50 0ms，最长 311ms）。
- 真机（同一句，减少动效强制开）帧序列是**「15–20ms 连续 10–15 帧 → 0.5–1.5s 没有内容帧」反复**（`389 502 537 488 489 480 198`、`302 497 503 497 531 487 507 488`…），
  主 JS 线程流式期间 24–62%、簇内帧间隔规整 ⇒ 不是 JS 饱和；手机到云 RTT 20–55ms、直连（同一时刻 PC 17ms）⇒ 不是链路延迟本身。
  到手机的**到达**就是成批的（tailnet / WiFi 侧把小帧攒成批，具体在哪一层本轮没有继续拆——手机上没有逐帧到达时间的取证口）。
- 旧 reveal 按「240ms 内追平」定速：一批 60 字 240ms 扫完、然后空等半秒到一秒 ⇒ 正是用户看到的形态。chitchat 轮没这种感觉是因为它到达连续。

修法（客户端，`streamReveal.ts`，`44d88bd2` 之后的第三版）：像抖动缓冲——速度 = 最近 `REVEAL_RATE_WINDOW_MS`=2s 内到达的字数 / 跨度，
但显示落后真实文本不超过 `REVEAL_MAX_LAG_MS`=1.2s，夹在 40–400 字/s。细粒度流按自身速率逐字（不变）；每 600ms 一批 60 字 ⇒ 100 字/s 连续流出、
批间不空等（单测 ⑥：第三批起没有一拍空转、落后 ≤1.2s）；整段一次到达仍按上限扫出。代价：显示比到达最多晚 1.2s——远快于播报读速。

### 8.1 真机 A/B（OPPO，清洁包 `d32f81c23`，18:11:57 装机，设备端 SHA-256 与本地一致、非 DEBUGGABLE）

清洁包 `xiaozhou-companion-prod-release-d32f81c23-20260918-1642.apk`（脚本首跑再次死于 clang abort，同 §5.1 的 depth=1 接续 22m43s 出包，
验包：KWS / ORT `.so`、内嵌 bundle、`app.config` variant=prod build=d32f81c23、签名 `5e8f1606…f625`）。同一句「给我讲一下深圳的历史，网上搜索下」、
同一装置（减少动效强制开，`set_switch.py` 回读 `after=True`），两趟有效样本（另一趟发送未触发、无服务端轮，不计）：

| 包 | 流式期间的内容帧间隔 | 无内容帧的空档 | 主 JS 线程 |
|---|---|---|---|
| `a4b477489`（§8 旧 reveal，search1） | 簇内 15–20ms，簇间 **0.5–1.5s**（`389 502 537 488 489 480 198`、`302 497 503 497 531 487 507 488`… 反复） | 每 0.3–0.5s 一段、共十来段 | 24–62% |
| `d32f81c23` 第 1 趟（764 字，trace `dc6dc268e49a4320`） | 4–241ms，典型 10–100ms，**16s 内没有一段 ≥0.3s 的空档** | 无 | 60–88% |
| `d32f81c23` 第 2 趟 | 2–122ms，两次 324 / 399ms | 无 | 35–77% |

⇒ 到达仍是成批的（到达方式没变），但显示已经按到达速率连续流出；「吐一批、卡一下」消失。代价：流式期间 JS 线程比旧版高
（连续 30 拍/s 的叶子更新 + 跟底滚动叠在到达处理上），峰值 88% 仍未饱和——若后续更长的回答让它撞顶，下一手是把 delta 到达合并成 ≤20 次/s 的 store 更新、
或给气泡加 memo；本轮不动。取证后 `reduceMotionForce` 已改回 false。OPPO 常驻包现为 `d32f81c23`。

顺带修正一处装置坑：`set_switch.py` 在 App 刚被 force-stop 又深链拉起的瞬间可能 `NOT_FOUND settings-switch-reduceMotionForce`（设置页还没渲出来），
再跑一次即可；那一趟若不核回读就会把「动效开着」的帧序列当成内容帧（2344 帧全是光球动画）。
