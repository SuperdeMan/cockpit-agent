# Android 流式文字上屏：匀速 reveal + 长回答不再被硬上限掐断（2026-09-18）

> 状态：**代码已实现并通过单测 / tsc / lint；真机 A/B 与清洁包因构建机内存耗尽未完成（§5）；服务端改动未 push / 未 deploy**。
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
- `features/chat/useRevealedText.ts`：挂载直出全文（历史恢复 / 复用 / 层升起时已流出的部分不重放）；只有**流式中**
  长出来的才追；streaming 落下时没追平的尾巴追完，不一次跳到位；换 id 直出；用户气泡不追；不看 reduce-motion
  （不是循环动效，它替代的「一段一段蹦」对动效敏感的人更糟）。节拍用 `setInterval`，只在有积压时存在。
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

## 5. 真机 A/B 读数：**未取得——候选包没能构建出来（机器 commit 耗尽）**

基线（§2 的真机读数）已在旧包 `990466d6d` 上取到；候选包三次构建都死在内存上，本页 A/B 栏空着，不补写。

| 尝试 | 参数 | 结果 |
|---|---|---|
| #1 | `-Release -Variant prod -CompileJobs 3` | Gradle daemon JVM 在 cxx-staging 阶段崩：`Native memory allocation (mmap) failed to map 177MB, G1 virtual space`（`hs_err_pid20112.log`），5 分钟 |
| #2 | + `GRADLE_OPTS` 小堆（`-Xms128m -Xmx2048m -XX:MaxMetaspaceSize=512m -XX:ActiveProcessorCount=2`，Kotlin in-process） | daemon 活了，**prefab 子 JVM** 崩（同一形态，532MB），2 分钟 |
| #3 | + `JAVA_TOOL_OPTIONS=-Xms32m -Xmx640m -XX:MaxMetaspaceSize=256m`（封所有子 JVM） | 过了 prefab，`:app:buildCMakeRelWithDebInfo[arm64-v8a]` 到 64/85 时 clang 起不来：`Couldn't execute program clang++.exe … (0x5AF)` = Windows 1455「页面文件太小」，12 分钟 |
| 接续 ×5 | 镜像目录内直接 `gradlew assembleRelease`（同 init script / -P 参数、`--max-workers=1`、重用 daemon） | 每次增量前进（arm64 85/85 完成，v7a 到 ~22/74），单次 1–4 分钟后同样 0x5AF；随后被 Claude Code 以「系统内存严重不足」终止，并要求不得自行重启 |

成因不在构建本身：整机 commit 上限 69.1GB（RAM 31.6 + 系统托管页面文件 38400MB，后者已到卷大小 1/8 的托管上限，C: 剩 38.6GB 也长不动），
`WindowsTerminal`（PID 26396）一个进程私有 commit **34.4GB**，其余全机只剩 0.9–1.5GB；三个并发 clang（每个 0.4–0.8GB）加 daemon 就撞顶。
两条可选的续路，都要人做决定：① 释放那 34GB（关掉 / 重启那份 Windows Terminal 或它的大 tab）后照常 `build_mobile.ps1 -Release -Variant prod -CompileJobs 3`；
② 内存不变时，把镜像 `.cxx/app/RelWithDebInfo/<hash>/<abi>/CMakeFiles/rules.ninja` 里 `pool compile` 的 `depth` 从 3 临时改 1（不换配置哈希、不动仓库）再接续——本轮改过一次又改回 3，
留待有人授权重启构建时用。原生中间产物（arm64 全部、v7a 一部分）留在 `D:\Android\builds\cxx\app\RelWithDebInfo\135z6d2r\`，接续可复用。

A/B 装置已进仓（`mobile/e2e/tools/stream_cadence_probe.py`：`prep` 从历史复制同一句 → `frames <label>` 采 framestats + `top -H`），拿到候选包后
按 §2 同一协议跑一遍即可对照：期望帧间隔从「中位 110ms、簇状」变成「≈33ms 匀速」，JS 线程仍 <60%。
⚠ 取证时 App 内「减少动效」强制开关（`reduceMotionForce`）被设为 true 以便 framestats 只反映内容变化；构建失败后设备已从 adb 掉线，
**这个开关还没改回 false**——下次设备在线先跑 `python mobile/e2e/tools/set_switch.py reduceMotionForce false`。
