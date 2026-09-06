# 语音批 · 播报卡顿定因与修复 实施计划（逐任务，先测后码）

> 归属：Android 陪伴端 UX v2.1 收口后的**语音批**（与「KWS 阈值 A/B 批」并列的两个 B5 遗留项之一）。
> 上游账：`docs/design/2026-09-04-mobile-ux-v2-b5-implementation-plan.md` §6.2 遗留②、AGENTS.md Android 行。
> 本计划**不含** KWS 阈值 A/B（0.2/2.0 调参另拆，见 B4 §6.4 交裁 #4）。两批都动语音链路，
> 分开做是为了单变量：调 KWS 阈值会改送进唤醒的音频，与播报输出无关，混在一起读数不可归因。

---

## 0. 接手须知（先读）

**本计划推翻了 B5 留下的下一个嫌疑方向，Phase 1 又推翻了本计划自己的首选。** B5 §6.2 遗留② 写
「别再沿 Xruns 找、首要嫌疑 16kHz VOIP 通路」。2026-09-05 白天的取证先改判为「AudioContext 空闲不挂起
被 MIUI 静音」（C1）；**当晚 Phase 1（T1/T2）把 C1 排除、C2 未复现，成因定在 C3——收尾形态处理**：
divergent final 走批处理补段留下 2.5s 空白、mixed 形态把云端段整段丢掉。四组真机读数见 §1.5，
管线反证见 §1.6，修法改为把 HMI 段链搬到 mobile（§3 T4′）。§1.1–§1.4 保留为过程记录。

**证据绑定的精确坐标（后续复现必须同锚，否则读数不可比）：**

| 项 | 值 |
|---|---|
| 设备 | `5d432b6d` = 24072PX77C（goku），MIUI `V816`，Android release `16` |
| App 包锚 | `com.xiaozhou.companion` versionName 0.1.0，`lastUpdateTime 2026-09-04 23:41:40`（= B5 主线包锚，AEC 在包内） |
| 云栈 | `target=cloud`，`release_sha=9a3b6f2f08657464c5049a5abf8f6e989e398bce`，5/5 endpoint healthy |
| 播报引擎 | **App 默认 `minimax`/`female-tianmei`**（`src/core/settings/store.ts:100`）；HMI 默认 `cosyvoice`（两端默认不同，见 §1 C2） |

**红线（本计划全程）：**
- 纯 JS 改动，**Phase 1 与 Phase 2 首选项都不重建 APK**（热更即可）；若某项非改原生不可，单列、单独交泓舟裁，不混进 JS 批。
- 不动 `.env`、密钥、CI/CD、安全组、native patch（`patches/react-native-audio-api+0.13.3.patch` 不在本计划里动）。
- 不改共享件 `hmi/src/pcmPlayer.mjs`（判据只留一份）；本计划所有改动落在 mobile 侧 `src/core/voice/`。
- 播报输出路径是共享资源（`sharedAudioContext` 也被 `cueTone` 唤醒提示音用），改生命周期要同时顾到它。
- **感知类结论必须泓舟在场做一次盲听 A/B 才算数**——「单测绿 / 客观读数好」证明不了「人听着不卡了」。

---

## 1. 根因调查（本会话证据）

### 1.1 输出路径配置（22:55 那轮的读数已作废，原因见 §1.6）

真机驱动一轮文字问答，期间每 4s 采 `dumpsys media.audio_flinger`：本应用输出 track 全程 `F3`（FAST）/ 48000Hz /
`fmt 5`（PCM_FLOAT）。⚠ 当时写的「FastMixer 192→193、播报期健康」**不算播报期证据**：事后核 `AudioTrackImpl`
的 `f:` 计数全程恒 7643——那一轮**根本没出声**（`speakPolicy=auto` 下文字提问不播报，`speakAllowed('auto', voice=false)`
为 false）。真播报期的 FastMixer 读数改见 §1.6。logcat 零 `Skipped frames` / 零 Reanimated 告警仍成立。

### 1.2 空闲期：AudioContext 从不挂起 → MIUI 静音（新发现，直接实证）

播报结束后设备静置，logcat 出现连续的零数据计数，满 60s 触发 MIUI 省电静音：

```
23:00:01 AudioTrackImpl: [audioTrackData][zero] 43s(f:7643 ... z:43006) : pid 25868 sessionId 1457 sr 48000 ch 2 fmt 5
   ...（每秒一条，z 持续累加，f 不变=没有新音频写入）...
23:00:18 AudioTrackImpl: isLongTimeZeroData out of time 60S 2mS , no related active track
23:00:18 AudioTrackImpl: isLongTimeZeroData noteMuteAudioInNeed(0)
```

机制链（三处源码核过）：

1. `audioCtx.ts::sharedAudioContext()` 是懒建单例，首次播报/提示音时 `new AudioContext()` + `resume()`，
   此后**永不挂起、永不关闭**。`closeSharedAudioContext()` 仅定义、`src/` 内**零调用方**（本会话 grep）。
2. RN `react-native-audio-api` 的 AudioContext 一旦 running，其 Oboe 输出流（`AudioPlayer.cpp`：
   `Exclusive` + `LowLatency` + 48k）**持续回调**；图里没有在播的 source 时，destination 渲染**零**
   （`AudioDestinationNode::renderAudio` 先 `zero()` 再 mix，无源即全零）。
3. 于是 AudioTrack 被持续写零 → MIUI `isLongTimeZeroData` 满 60s 判「无关联活跃 track」→ `noteMuteAudioInNeed`。

**这解释了 B5 的悖论**：泓舟报「主观卡顿」，而 B5 四项客观指标（Xruns / HAL write blocked）全 0——
因为它们量的是「混音器有没有按时拿到数据」，静音 track 写的是零、**混音器每次都按时拿到了（拿到的是零）**，
所以这些指标看不见它。空闲静音造成的是**下一次开口时 track 处于被静音/降级态、首帧被吞或起播毛刺**，
指标层完全静默。泓舟两次报卡顿都在真人轮的**第 1、2 步**（每步之间有观察/记录的长间隔，极易越过 60s 空闲）
——与「静置后首句起播异常」的形态吻合。

### 1.3 下发不是瓶颈（云栈实测，节奏探针）

本会话写探针连 `/api/tts/stream`，按 LLM 出字节奏（25 字/秒）喂文本，记每个音频帧到达时刻，
并按 `pcmPlayer.mjs` 调度规则离线模拟播放游标：

| 引擎 | 首帧 | 分片数 | 均片时长 | 送达/实时 | 200ms jitter 下模拟 underrun | 最小裕量 |
|---|---|---|---|---|---|---|
| minimax | 646ms | **707** | 40.6ms | 4.79× | **0** | 243ms |
| cosyvoice | 650ms | **191** | 158ms | 4.38× | 0 | 381ms |

⇒ 网络送达远快于实时（4–5×），模拟零 underrun。**卡顿不来自下发**。但暴露一条与端侧负载相关的差异：
**minimax 送 707 个约 40ms 的碎片，是 cosyvoice（191 片）的 3.7 倍**。而 App 默认引擎正是 minimax。
每个碎片在 JS 线程上要做：`int16→float` + 线性重采样（24k→48k，`audioCtx.ts` 的 shim 里同步跑）
+ 5 次 JSI 调用（createBuffer/copyToChannel/createBufferSource/connect/start）。碎片越多、越密集
（p95 到达间隔 16ms、峰值 587ms 说明成簇到达），与流式文本渲染/在场动画抢同一条 JS 线程的机会越大。

### 1.4 顺带发现：MiniMax RPM 限流会把播报**截断**（独立出账，不在本计划里修）

节奏探针连续第二趟（burst 模式，整段一次给）在送出 279 片 / 11.4s 音频（全长 28s）后收到：

```
ctrl {"type":"error","message":"{'status_code': 1002, 'status_msg': 'rate limit exceeded(RPM)'}"}
```

服务端此后**没有 `done`**。原因：`MiniMaxWsStreamingTTSProvider` 走 `_sentence_segments(soft_break=True)`，
每个逗号/句号段一条 `task_continue`，一段 140 字回答约 9 个请求；两趟贴着发 ≈18 请求/分即触顶
⇒ 真实对话里**一分钟内两句长回答就可能撞线**。

客户端在这条路上的行为（`tts.ts::fallback()`）：`{type:error}` 到达时若 `audioStarted=true`，只 `settle()`——
**不停已排定的音频、也不为未合成的尾巴补批处理**；于是已缓冲的几秒播完后**戛然而止**，剩下的话永远不说，
且 `onEnd` 提前触发让免唤醒 FSM 在余音仍在放时就进 FOLLOWUP。听感是「说了一半停了」，也可能被报成「卡顿」。
两侧都有可改处（网关：段级退避/合段降请求数；客户端：出错后按已播文本的余量走 `synthesizeBatch` 补尾），
**另立卡片交泓舟裁**，不混进 C1/C2 的读数。

### 1.5 决定性读数：收尾形态在 mobile 上的段间空白 / 丢段（C3；2026-09-06 00:01–00:02 真机，包锚 `23:41:40`，minimax）

T1 探针加了两种收尾形态、走真实 `SpeechController`（`begin` → 逐字 `delta` → `finish`），读 `speaking` 轨迹：

| 形态 | 次 | 段数 | 第一段落 → 第二段起 | 结论 |
|---|---|---|---|---|
| divergent（final 与已流内容是两段话） | d1 | 2 | **2507ms** | 段间空白 = 批处理整段合成时间（另加首片 200ms jitter） |
| divergent | d2 | 2 | **2623ms** | 可复现 |
| mixed（本地回执 final(A) → 云端 delta(B) → 云端 final(B)） | d1 | **1** | — | **B 整段无声** |
| mixed | d2 | **1** | — | 可复现 |

机制（源码逐行核过）：

- **divergent**：`speech.ts::finish()` 判 `!sameSegment` 后 `session.completion.then(() => speakBatch(text))`——等 A 播完，
  再走 **HTTP 批处理**整段合成 B（`/api/tts`，minimax 批处理 ≈2.5s），中间就是那段空白。HMI 同一处走段链轮转成
  **新流式会话**（首音 ~0.6s）。
- **mixed**：`finish(A)` 之后 B 的 `delta()` 仍 `append` 进已收尾会话——网关 `text_queue` 已收 None 哨兵、后到的 text 无人
  消费（静默丢）；但客户端 `accum` 照样累加成 A+B；再 `finish(B)` 时 `speechCovered(A+B, B)` 因 `na.includes(nf)` 判
  「已覆盖」⇒ 不补尾、不 divergent ⇒ **B 一个字都不播**。若 A 恰已播完（`session` 已 null）则改走批处理补段 ⇒ 退化为
  divergent 的空白。
- 后端确有这种形态：`orchestrator/edge/server.py:776` 对混合意图的慢意图先发 `speech_delta="正在为您处理其他请求…"`，
  云端答案随后另一段到 ⇒ **混合意图轮天然是 mixed/divergent**。HMI 2026-07-18 批次（`71a5bb9`，
  `docs/design/2026-07-18-voice-interrupt-context-tts-batch.md` §3c）正是为泓舟当时报的「长内容断播」修了同一组断口；
  `tts.ts` 头注写明 mobile 显式没做段链（M4 观察项）。

⇒ **播报卡顿的成因定在 C3**：不是音频管线，是收尾形态处理。听感「播到一半停一下」= divergent 的 2.5s 空白；
「说了一半没了」= mixed 丢段。

### 1.6 管线读数（同锚；C1/C2 的反证）

| 读数 | 值 | 出处 |
|---|---|---|
| pcmPlayer 起点重排（`TtsSession.stats.underruns`） | **0 / 9 段**（5 段无负载 + 4 段免唤醒负载仿真：VoiceCommunication 麦 + VAD 推理 + KWS 每帧 + 逐 delta 重渲染） | 探针 stutter3 / stutter4 |
| 首音（排定） | 572–604ms 无负载；773–887ms 负载下；均 <1.5s | 同上 |
| MIUI `isLongTimeZeroData` | 空闲 ~62s 必触发；但下一句音频按排定时刻进入 `[fine]`（`m:0`、maxAmplitude 1.2e9），**不闸门、不延迟** ⇒ C1 排除 | logcat-stutter3 23:41:11 → 23:41:22 |
| 声学起播滞后（无 AEC 第二路麦） | 暖态 527–773ms、静置后 556/1358ms——仪器噪声大于效应，不作判据 | stutter3 |
| FastMixer `underruns`（真播报期） | 每段 0–2（设备级计数器、4ms 周期），与 2.5s 空白不是一个量级 | stutter5 |
| 探针取法坑 | ① `uiautomator dump` 在有常驻动画的屏上不写文件，pull 到旧树；改深链 `?auto=…&n=k` 自动触发；② 深链里的 `&` 要带引号透传到设备 shell；③ `screencap -p` 走 stdout 时 MIUI 会先打一行多显示器警告污染 PNG，写文件再 pull；④ Git Bash 会把 `/sdcard` 改写成本机路径 | 本轮 |

---

## 2. 候选裁决（Phase 1 后）

| # | 候选 | 机制 | Phase 1 结论 |
|---|---|---|---|
| C1 | 空闲静音起播毛刺 | AudioContext 不挂起 → 写零 → MIUI 60s 静音 → 下句起播被吞 | **排除**：静音必触发但不闸门下一句（§1.6） |
| C2 | JS 线程碎片调度负载 | minimax 碎片 × 每片重采样 + JSI，与渲染抢 JS 线程 → pcmPlayer 起点重排 | **未复现**：9 段 0 underrun，含负载仿真（§1.6）。真实对话页负载下可用 `TtsSession.stats` 再核，非闸门 |
| **C3** | **收尾形态：divergent 走批处理补段 / mixed 丢云端段** | §1.5 机制三条 | **确认**：两形态各两次复现（2507/2623ms 空白；段数 1） |

「pcmPlayer 起点重排」仍是值得留着的观测口（C2 的隐形通道，FastMixer 不计），T1 已把它接进 `TtsSession.stats`。

---

## 3. 任务清单（先测后码）

### Phase 1 — 定因（不改生产路径，只加诊断；一次泓舟盲听）

**T1 诊断探针（改 `src/app/voice-spike.tsx`，debug 屏，热更）**
在既有 `probeTtsStream` 旁加一个「真实播报 + 隐形通道读数」探针：走真实 `TtsSession`/`speechController`，
但把两条隐形通道显式打出来——
- 给 `newPcmPlayer` 接 `onUnderrun`，累计**本次播报的 pcmPlayer.underruns 与每次空白的时刻/时长**；
- 播报前后各读一次本应用 AudioTrack 的 `Server`/`FrmRdy` 帧游标（`dumpsys media.audio_flinger`，宿主侧脚本），
  算**排定起播时刻 vs 实际首帧推进**的滞后（C1 的客观量）；
- 记 ctx 在本次播报前已空闲多久、`ctx.state`。
读数出口两条（屏上一行 + console），同既有探针纪律。

**T2 C1 复现实验（纯 adb + T1 探针，不需泓舟）**
① 播一句（暖）→ ② 静置 65s，logcat 等到 `noteMuteAudioInNeed` → ③ 再播一句，读 T1 的起播滞后；
对照：暖态连播两句的起播滞后。**判据只许两种写法**：「静音后起播滞后明显大于暖态 ⇒ C1 成立」或
「两者相当 ⇒ C1 不是主因，转 C2」。滞后是客观量，这一步不依赖人耳。

**T3 泓舟盲听 A/B（唯一需要泓舟在场的一步）**
两个构建/两套设置各跑真人轮，泓舟只被告知「A / B」不被告知哪个是改后：
- 对 C1：A=现状、B=播报结束即 `suspend()`（见 T4）；
- 对 C2：A=minimax、B=cosyvoice（碎片数 3.7× 差异）或 B=加大 jitter/合片。
盲听分组结果 + T1/T2 客观读数一起，定 Phase 2 改哪条。**不许把「客观读数好」写成「泓舟不卡了」。**

### Phase 2 — 修复（按 Phase 1 定的因 = C3，逐条独立提交、各配肯定式验收；**改法换了，需泓舟再批一次**）

**T4′ 修 C3：把 HMI 段链语义搬到 mobile（首选，JS-only，不改共享件）**
- `tts.ts::TtsSession`：`finish()` 之后的 `append` 不再灌进已收尾会话（spent 守卫，返回 false 交控制器排队）；
  `accum` 只记本会话真正送出的文本——这一条直接修 mixed 的「已覆盖」误判。
- `speech.ts::SpeechController`：待播段队列（divergent 的 final、spent 后到的 delta/final），当前会话 `completion`
  后**轮转成新的流式 `TtsSession`**（同引擎同音色）接着播；轮转段失败才回批处理。`speaking` / `onSpeechEnded`
  在段间不落（250ms 复判，同 HMI `markTtsMaybeEnd`），免唤醒 FSM 不在多段中途掉出 SPEAKING 去开麦。`stop()` 清链。
- 判据复用 `@shared/ttsQueue.mjs::speechCovered`（不改）；参考实现 `hmi/src/audio.ts:474-560`。
- 验收（真机同锚，用 T1 探针）：divergent 段间空白从 2.5s 降到首音量级（<1s）；mixed 段数 = 2 且 B 有声；
  jest：spent 会话不吞 delta、覆盖误判用例、轮转顺序、stop 清链、段间 speaking 不落；全量 jest 绿 + tsc 0；
  泓舟真人一轮混合意图（车控 + 查询）盲听「不再停一下 / 不再少半句」。

**T5 保留为可选电量项（不再是卡顿修法）：空闲挂起 AudioContext**
- `audioCtx.ts` 加 `suspendSharedAudioContextWhenIdle()` / 或由 `SpeechController` 在 `speaking` 落 false
  且 DEFER 队列空后 grace（建议 2–3s，避开连播/提示音）挂起，下一次 `newPcmPlayer`/`cueTone` 前 `resume()`；
- 顾到 `cueTone` 也用同一 ctx（挂起判据要问「有没有任何消费方在用」，不能只看 TTS）；
- **必测 resume 代价**：resume 后首音是否越过验收判据 <1.5s（现读 minimax 646ms、cosyvoice 650ms，有 2.7× 余量，
  但 resume 独占 LowLatency 流可能加 warm-up）；若 resume 太贵，退一步用「keep-alive 到 idle N 秒再 suspend」两级。
- 附带收益：Oboe 独占低延迟流不再 24h 常开（电量）。

**T6 C2 的真实负载复核（非闸门）**：把 `TtsSession.stats` 接进对话页一轮真实混合意图播报的日志，读 underruns；
Phase 1 的负载仿真为 0，若真实对话页也为 0 则 C2 关账。

**T7 记录收口** 回填本计划 §6；更新 AGENTS.md 开项指针与 design/README 行。

---

## 4. 验收（绑定真机读数，不靠单测）

1. **C1 已消**：静置 >60s 后再播，logcat 不再出现针对本 track 的 `noteMuteAudioInNeed`；T1 起播滞后回到暖态量级。
2. **首音不退化**：T4 后 minimax/cosyvoice 首音仍 <1.5s（同锚复测，带两次读数）。
3. **泓舟盲听**：改后真人轮泓舟报「不卡了」，且盲听分组能把「改后」认出来（不是随机期望）。
4. **无回归**：`npm test`（现 495）/ `npm run typecheck` 0 error；`maestro test e2e/ --include-tags offline` rc=0。
5. 证据绑定精确 SHA/包锚；happy path 之外补至少一轮静置后首句 + 一轮连播。

---

## 5. 实施判断（开工前写下，撞到再补）

- **别再沿 Xruns/HAL 找**（B5 已证 + 本会话复现）：这两个指标对 C1/C2 都是盲区，看它们只会又得 0。
- **默认引擎差异是真实变量**：App=minimax（707 碎片）、HMI=cosyvoice（191 碎片），跨端读数不可直接搬。
- resume 首音代价是 T4 的成败关键，先量再定 suspend 策略（即挂 vs 延迟挂）。
- 感知与客观两套读数都要有；只有客观好、泓舟仍卡，就是选错了候选，回 Phase 1，别在 C 上再加补丁。

---

## 6. 实施记录（分批回填，每批一个会话，写完即停）

### 6.1 Phase 1「定因」（2026-09-05 晚 → 09-06 00:02；泓舟「批计划」后同会话执行）

**T1 已落**（本节提交）：`TtsSession.stats`（chunks/bytes/underruns/gaps）+ `hooks.onUnderrun(gapMs, atSec)`，
空白按「上一片排定结束 → 迟到片到达」在 pcmPlayer 更新 `nextStart` 之前算；`audioCtx.ts::peekSharedAudioContext()`
只看不建；voice-spike 三个探针：`卡顿探针`（真实 `TtsSession` + 无 AEC 第二路麦包络 + `load=hf` 免唤醒负载仿真）、
`段间 divergent` / `段间 mixed`（真实 `SpeechController`，读 `speaking` 轨迹），深链 `xiaozhou:///voice-spike?auto=stutter|divergent&n=k[&load=hf][&variant=mixed]`
自动触发，读数打 `stutter-json` / `divergent-json` 行进 logcat。jest 先红后绿（voiceTts +2，19/19），tsc 0。

**T2 读数**：§1.5（C3 四组）与 §1.6（管线五项）。**结论按计划只许的两种写法之一**：
「静音后起播滞后与暖态相当 ⇒ C1 不是主因」成立（AudioTrackImpl `[fine]` 行）；C2 未复现；C3 确认。

**T3（泓舟盲听）未做**：Phase 1 已用客观读数定因，盲听改到 T4′ 验收（改后混合意图轮）。

**改判与坑**：① 22:55 那轮零音频（`speakPolicy=auto` 文字提问不播），当时的 FastMixer 读数作废——**先证「演员在场」再读指标**
（§1.1）；② `uiautomator dump` 在常驻动画屏不写文件、pull 到旧树，判据要看它有没有说 `dumped to`（§1.6）；
③ 声学起播滞后仪器噪声大于效应，弃用；④ 探针的 minimax 音色是 `female-shaonv`（设备当前设置），不是默认 `female-tianmei`。

**未推送**：本会话提交均在本地，push 需泓舟单独授权。

### 6.2 Phase 2「T4′ 段链」（2026-09-06 上午；泓舟「批准，要按照能解决问题的目标推进」）

**已落**：`5cec2ac`（`d237b7d` 探针之上）。改动面：`tts.ts`（`spent` 守卫、`gateUntil` 音频闸门）、`speech.ts`（会话队列 +
250ms 宽限 + `turnStats`）、探针判据改读 `turnStats`；共享件 `hmi/src/*` 零改动。

**设计要点（与计划 §3 T4′ 的一处刻意加强）**：HMI 段链等前一段 completion 之后才开始轮转（新会话从建连起算，
首音 ~0.6s）；这里**合成提前、只闸播放**——收尾之后再到的文本立刻另起流式会话建连送文本，音频到了先扣着
（`held`），前一段 `completion` resolve 开闸按序推进播放器；`done` / 回退在闸门未开时延后。段间 `speaking` 不落，
`onSpeechEnded` / `onSilent` 只在队列排空 + 宽限后一次（FSM 的 `ttsEnd` 会把 SPEAKING 打到 FOLLOWUP 开麦）。
`stop()` 保留旧语义：停掉活着的轮同样算收尾（免唤醒靠 `onSilent`/`onSpeechEnded` 收 THINKING）。
mixed 的「已覆盖」误判由 `spent` 守卫修：finish 之后的 delta 不再进已收尾会话、不累积 `accum`。

**真机读数（同锚 `23:41:40`，minimax `female-shaonv`，真实 `SpeechController` 路径）**：

| 形态 | 修前（§1.5） | 修后 `5cec2ac` |
|---|---|---|
| divergent d1 / d2 | 段间空白 **2507 / 2623ms** | 段数 2，前段收尾→后段首片 **7 / 5ms** |
| mixed d1 / d2 | 段数 **1**（云端段无声） | 段数 **2**，收尾→首片 **1 / 5ms** |

可闻空白 = 读数 + 前段 settle 120ms + 首片 jitter 200ms ≈ **0.3s**（正常句间停顿量级）。同轮 `underruns` 0；
FastMixer 每段 +1（设备级计数器）。**验收①②③ 的机器半已过**：divergent 空白从 2.5s 降到首音量级以下、mixed 段数 = 2、
`speaking` 全程一次起落。

**反向验证**：speechChain 9 条 + voiceTts 4 条**先红后绿**（红因分别是 `turnStats`/队列不存在、`spent`/`gateUntil` 不存在）；
邻近套件 ttsSilent / presenceSignals / handsFree 绿；全量 jest **539/539**（526 → +13）、tsc 0。

**坑**：⑤ `adb reverse` 隔夜掉（坑 82 同形态），dev-client 落 `DevLauncherErrorActivity`，「bundle never ran」的真因不是 Metro
——脚本前置加了幂等 `adb reverse tcp:8081 tcp:8081`；⑥ 上午的声学底噪比夜里高 6×（0.0153 vs 0.0026），无 AEC 麦包络这条
读数在白天更不可用。

**仍开**：① **泓舟真人一轮混合意图（车控 + 查询）盲听**「不再停一下 / 不再少半句」——机器读数不替代人耳；
② T6 C2 真实对话页负载复核（非闸门）；③ T5 空闲挂起 ctx 改为可选电量项；④ §1.4 MiniMax RPM 限流截断另立卡片；
⑤ 未推送（本地领先 origin/main 5 个提交）。

### 6.3 泓舟真机复测反馈轮（2026-09-06 上午）：「介绍深圳历史」一轮两症状——截断定因 RPM、「嗡嗡/卡顿」四条候选

**泓舟原话**：「一是中间还是有卡顿或是说混杂的嗡嗡声，二是播报到『1980 年设立深圳经济特区』这里就停止了，没有把生成的文字内容全部播报完整。」

**症状二（截断）已定因、已修（网关 + 客户端）**：

- 云端 llm-gateway 日志（UTC）：`02:57:56` 第一条流 first=3647ms、190 片正常 done；`02:58:22` 第二条流 **`02:58:34 TTS stream provider error: rate limit exceeded(RPM)`**，此前 12.3s 已下发 **1163 片（≈50s 音频）**；此后无第三条流。设备侧 AudioTrackImpl：音频 10:58:27 起播、**10:59:32 止**——正好是把 50s 缓冲放完。
- 机制 = §1.4：`_sentence_segments(soft_break=True)` 按逗号逐条 `task_continue`，每条各算一次请求，本账号约 20/分；详细版回答一分钟内撞限，网关回 error，客户端已出声即 settle、不再合成剩余文本。
- **修（网关 `llm-gateway/providers.py`，待部署）**：`_minimax_segments` 首段贴第一个软断点保首音、其后按句末断（同文 14 请求 → 4 请求）；`_RpmBucket` 60s 滑窗限速（`MINIMAX_TTS_RPM` 默认 18）；`task_failed` 1002/1039 等窗口滚过后**重连续传**未收到 `is_final` 的段（`MINIMAX_TTS_RECONNECT_MAX` 默认 3），不再把整条流报 error。`tests/test_minimax_ws_pacing.py` +12（假 WS 序列 + 假钟：分段、滑窗、限速等待、重连续传、非限流失败照旧抛、`continuous_sound` 默认关/可开）；旧 providers.py 下整文件红、新版 44/44 绿。
- **修（客户端 `tts.ts`，`a0e11ad`）**：出过声后上游报错 → 等已排定音频放完再收尾（旧代码立刻 settle ⇒ 段链下一段闸门提前开会两段叠放、免唤醒 FSM 在余音里进 FOLLOWUP 开麦）。

**症状一（「卡顿或混杂的嗡嗡声」）四条候选，按证据排序，人耳待裁**：

| # | 候选 | 证据 | 处置 |
|---|---|---|---|
| ① | **系统通知的震动 + 提示音混进播报** | logcat：**10:59:22.8** `VibratorManagerService` uid 1000 NOTIFICATION 两次 200ms（10:59:23.1 / 23.6）+ systemui AudioTrack 提示音 10:59:23，此时播报还有 9s；10:57:42 另有一次通知震动。本应用（uid 10423）播报期间**零**震动 | 请泓舟核对当时是否来了消息；若是，非缺陷 |
| ② | **MiniMax 自身在标点处的停顿**（「一顿一顿」的卡顿感） | 同文同音色：逐分句 14 请求内部静音 **14 处 4.72s**；新分段 4 请求 **13 处 4.88s**；`continuous_sound=true` **13 处 4.16s**、首片慢 250ms ⇒ 停顿是音色韵律，与我们的请求粒度无关；**我在 §1.5 写的「接缝静音」因果被这组对照推翻**（同文对照做完才许下结论） | 两段 WAV 已发泓舟在电脑上听；`continuous_sound` 默认关、env 可开给人耳 A/B；要根治只能换音色/引擎 |
| ③ | 排定边界错帧（原生 `timeToSampleFrame` 截断 + pcmPlayer 浮点累加） | 单测：24k→48k 1163 片只 **1** 个边界错（首片重采样少 2 帧）、48k 同率 1 个、**22050 63/191 个** ⇒ minimax 上不足以成「嗡嗡」，但都是真缺陷 | **已修（本轮）**：`audioCtx.ts` 起点吸附帧格 + 1/4 帧、`duration` 用实际提交帧数；`test/audioShimSchedule.test.ts` +3 先红后绿 |
| ④ | 叙述段与答案段之间 ~20s 无声（LLM 生成慢：第一条流 first=3647ms、第二条流开在第一条结束 15s 后） | AudioTrackImpl：10:58:07 → 10:58:27 零填充 | 是 Planner/LLM 时延，不是播放；出账 |

**排除**：本应用震动 0；播报期 FastMixer 只 +9（≈36ms）；`underruns` 读数无法取（生产路径无日志，T6 仍开）。

**坑**：⑦ **先做同文对照再下因果结论**——「逐分句合成 ⇒ 片头片尾静音」听着合理、探针数出 14 个接缝也「印证」了它，真实 API 上 4 请求 vs 14 请求一比就翻；⑧ 块注释里写 `**帧数**/率` 会被 `*/` 提前关掉（tsc 红、babel 绿）。

**待办**：网关改动需 cloud deploy（dry-run → 泓舟授权 `--apply`）才能到手机；泓舟听两段 WAV 裁 ①②；T6。

**泓舟裁决（09-06 11:4x）**：「听两段在电脑上都正常，当时手机确定没有其他通知或提醒」⇒ 候选 ①②出局，
**「嗡嗡」是手机侧在播放过程中引入的**。deploy 授权、推送等问题修完。

**部署**：`dev_stack.py deploy --sha a05cb5f` dry-run `status=dry_run`、`blocking_changes=[]` → 泓舟授权 → `--apply`
`status=submitted` → 11:48 `status` 显示 `release_sha=a05cb5f…`、5/5 healthy → `verify` `status=verified`
（artifact `20260906T034959Z-a05cb5f.json`）。运行时改动只 `llm-gateway/providers.py`。

**部署后长文本验证（Node 探针打 `a05cb5f` 网关，1218 字 26 句，30 字/秒喂）**：`done` 到、1971 片、**81.8s 音频完整**、首片 785ms、
无 error ⇒ 截断修好。但送达/实时只 1.28×：第 18 个请求后滑窗把发送压住等到窗口滚过，模拟播放在 ~55s 处出现**一次 3.5s 空白**
（`needed_initial_buffer 3706ms`）。补第二层（未部署，待泓舟再授权 apply）：控制变量改为**客户端音频余量**
（已下发秒数 − 首片以来墙钟）——余量 <5s 继续按逗号切（别断粮）；≥5s 改按句末；≥10s 攒多句到 150 字再发（`_tts_send_now` 纯函数；
MiniMax 单次可到 1 万字）；泵空等 1s 且余量 <5s 先把攒着的发出去。请求数随之降到预算内，不再等窗口。单测 +4（策略纯函数 /
30s 余量下四句合一 / 余量掉下阈值后空等冲刷 / 余量驱动的逗号切换），网关 48/48。

**「嗡嗡」设备侧定位对照（voice-spike 深链，`ab.ps1 <n>`）**：1 分片流式（对话页同款）/ 2 分片流式 + VoiceCommunication 麦开
（AEC 通路 + VAD/KWS 负载）/ 3 批处理整段一个 buffer（无分片边界）/ 4 纯正弦经同一播放路径。判读：只 1、2 有 ⇒ 分片调度；
1、2、3 有、4 无 ⇒ 重采样或 TTS 音频本身在设备路径的处理；4 也有 ⇒ 输出路径；只 2 有 ⇒ AEC/VoIP 通路。

**部署 #2（`60a72a2`，第二层「余量驱动合并」）verify 失败定因（09-06 12:12 → 13:xx）**：泓舟授权 `--apply` 后
`status=submitted`，12:12:37 切栈、12:12:47 新容器被回滚脚本停掉、`current` 回 `a05cb5f`（远端状态证据
`state-20260906T041318Z-VERIFY_FAILED_ROLLED_BACK.json`）；再试两次因远端 `builds/releases/<sha>`、镜像「already exists」守卫 10s 内失败。
**不是代码**：新镜像 `import providers` 正常（py3.11.15、`_RpmBucket`/`_tts_send_now` 在），单独起 `hmi 1.24s / edge 1.39s / llm 2.89s` 即 200
（`/api/llm/providers` 200）；docker 日志 27 个新容器**无一自行退出**，全部 running 10.9s 后被手动停。**是验收闸与启动风暴赛跑**：
`verify_https_endpoints` 在 `compose up -d` 返回后立刻 `curl --fail` 五个端点、零就绪等待，而 27 容器同时冷启时 tailscaled 记
12:12:41 `dial 127.0.0.1:8090 refused`（edge）、**12:12:43 `->127.0.0.1:5173 reset by peer`（hmi）** —— 容器起来第 6s 打 hmi 即判红。
同一脚本 11:48（a05cb5f 首发）与 12:13（回滚验收）都恰好赶上 ⇒ 抛硬币。

- **修 `cf7091c`（`deploy/cloud/verify-release.sh`）**：非 200 每秒重试、五端点共享 120s 截止（`HTTPS_READY_TIMEOUT_S`），到点仍非 200 才
  `verify_error`（带最后 http_code 与已等秒数）；证据新增 `https_ready_s`。等待只放宽「何时判」，不放宽「判什么」。tests +2（晚就绪逐次序对 /
  永不就绪 timeout=0 立刻 rc=1 且不打下一端点），模块 185 passed / 1 skipped；README 发布事务边界补一条。
- **代价**：`deploy/cloud/**` 变更 ⇒ 基础设施聚合摘要 `d84a1f8a… → 499fc97c…`，dry-run `status=bootstrap_required`、
  `blocking_changes=[{infrastructure, deploy/cloud/verify-release.sh}]`。要按 2026-08-26 的批准锚流程（root 快照 + 备份 + 改写
  `/opt/car-agent/shared/release-infrastructure.json` + 失败自动还原）重新批准并安装 verify-release.sh，普通 deploy 才放行——
  这是改生产主机配置，**待泓舟授权**；材料已生成在 `.artifacts/infrastructure-approval/cf7091c…/`（只读审阅，未执行）。
- 60a72a2 的残留（builds 151M、releases 目录、52 个镜像 tag）按 README「保留为诊断/清理候选，不自动清理」口径留着，不阻碍新 SHA 部署。

**部署 #3（`cf7091c` = 第二层网关修正 + T6 + verify 就绪等待）已落（09-06 15:4x）**：泓舟「授权」→ 基础设施批准按材料执行
（`infrastructure_approved`，锚 `d84a1f8a… → 499fc97c…`，备份 `…/infrastructure-approvals/cf7091c…-499fc97c`）→ dry-run `status=dry_run`、
`blocking_changes=[]` → `--apply` 184s `status=submitted`（stderr 落盘、无报错）→ 远端 `state-20260906T074853Z-VERIFIED.json`、
`status` 5/5 healthy `release_sha=cf7091c…`、独立 `verify` 通过（artifact `20260906T075039Z-cf7091c.json`）。30 容器全在 `:cf7091c` 镜像，
docker 日志无非手动退出，tailscaled 本次切栈零 proxy error，`https_ready_s=0`（这次没等到就绪就已 200——等待是保险，不是每次都用上）。

**部署后长文本探针（打 `cf7091c` 网关，931 字 22 句，30 字/秒喂，jitter 200ms）**：首片 0.86s、`done` 到、4848 片、**204.6s 音频完整**，
下发 36.9s 即 5.54× 实时；模拟播放 **`sim_underruns=0`、总空白 0ms、最小余量 243ms**（a05cb5f 时同类长文本在 ~55s 处有一次 3.5s 空白、
`needed_initial_buffer 3706ms`；现在 `needed_initial_buffer=0`）⇒ 第二层「余量驱动合并」把请求数压进 RPM 预算、不再等窗口。
片内静音 94 处 48.3s 是音色自身的标点停顿（与 §6.3 同文对照结论一致，不是我们的请求粒度）。**截断 + 3.5s 空白两条在网关侧闭合**；
剩「嗡嗡」待真机对话页 `[speech-turn]` 读数（adb 上的 919fd6f9 是泓舟新接入的 OPPO PEUM00 测试机，ColorOS 14 / Android 14，已入 tailscale、装了 apk，泓舟 09-06 授权直接用；我一开始误记成「别人的设备」）。

**坑**：⑨ **验收闸自己也是被测系统**——「verify 失败」先问是被验的东西坏了还是闸在赛跑：容器全部存活 + 端点单起即 200 + tailscaled 的
`refused/reset` 时间戳，三样凑齐才敢说「闸误判」；⑩ dev_stack 把远端 stderr 直接透传到控制台、不落盘，后台跑 apply 时 `2>$null`
就把唯一的失败原因丢了——真栈动作的 stderr 必须落文件；⑪ 同一 SHA 失败后不能原地重试（builds/releases/镜像三道 already-exists 守卫），
重试=新提交。

### 6.4 「嗡嗡/喷麦」定因与修法（2026-09-06 下午，OPPO PEUM00 / ColorOS 14 / Android 14，泓舟新接入的测试机 `919fd6f9`）

**泓舟原话**（对话页重放「介绍深圳历史，尽量详细」，App 从本机 Metro 热载、含 T4′/T6/tts 修）：「有混叠的嗡嗡声，基本从开始播 2 秒后
就开始了，一直持续，像是喷麦的声音」。

**取证（全部 read-only）**：logcat 整段录制 + `dumpsys media.audio_flinger` 前后 + `top -H`：

| 读数 | 值 | 含义 |
|---|---|---|
| 我们输出轨（F2/FAST）`Underruns` 帧 | **6,587,136 ≈ 137s** | 两轮长回答 ≈ 3.5min 里**约六成的帧被混音器填零** |
| FastMixer 该轨 Full/Partial/Empty | 683 / **766** / **240**（10 位环绕计数） | 大部分 4ms 拍只交了部分帧 |
| 音频回调线程 `AudioTrack` CPU（探针播放中） | 62–68% | 单路 48k 流本不该这么贵 |
| JS 层 `TtsSession.stats.underruns` | **0** | 排定没错——空白不在 JS 调度层 |
| 播放路径 | `AAudioService: aaudio denied with imcompatible policy` → Oboe 退到 legacy `AudioStreamTrack`，`AUDIO_OUTPUT_FLAG_FAST` 成功，192 帧/4ms 一拍，`framesPerCallback=128` | 系统白名单外的 App 拿不到 AAudio/MMAP |
| 麦克风/模式 | 播报期间麦已关（PTT 松手 16:07:03 stop）、`Audio mode = MODE_NORMAL`、无 SCO/A2DP 路由 | **不是**通话链路 / 蓝牙 / AEC |
| 系统通知震动 | 本 App 自己的 PTT 震动 4 次，无系统通知 | 排除 |

**对照实验（voice-spike 深链，同一段 13s 文本，同一台机）**——变量只有「节点数」：

| 播放方式 | 该轨 Underruns 帧增量 | 声学证真（探针无 AEC 麦包络 / 起播滞后） |
|---|---|---|
| 逐片一个节点（旧，4 次） | **48,192 / 11,712 / 2,880 / 2,304**（0.05–1.0s，随会话状态波动） | 0.033 / 651ms |
| 整段一个 buffer（`auto=batch`） | **0** | — |
| **队列节点（新，2 次）** | **0 / 0** | 0.030 / 624ms（确实出声） |

⇒ **机制**：react-native-audio-api 的渲染回调每拍要过一遍图里所有 `AudioBufferSourceNode`；每 42ms 一片、每片一个节点、网关
5.5× 实时下发 ⇒ 待播节点数百上千，回调 4ms 内交不齐 192 帧，FastMixer 记 partial/empty、缺帧填零 ⇒ 人声底下持续嗡嗡。
对话页比探针页重得多（长回答待播节点上千 + UI：RenderThread 59% / 主线程 53% 常驻动画、Reanimated
`synchronouslyUpdateUIProps failed … Unable to find SurfaceMountingManager` 4 分钟 386 次带完整栈 36,730 行）⇒ 六成 vs 几个百分点。
边界错帧（§6.3 ③）与 MiniMax 音频本身都不是主因；C1/C2 的结论不变。

**修 `5eb71b5`**：`mobile/src/core/voice/queuePlayer.ts`——**一个** `AudioBufferQueueSourceNode`（库 0.13.3 自带，09-05 装机 APK 已含，
纯 JS 不重建）顺序 `enqueueBuffer`，片与片在音频线程逐样本拼接，队空填零、新片一到即续播；时间线记账沿用 pcmPlayer.mjs 规则，
`TtsSession.noteUnderrun` 读的 `nextStart`/`ctx.currentTime` 形状不变；重采样仍在 JS 侧。`audioCtx.ts::selectPcmPlayer` 纯函数选实现
（ctx 没有队列节点 ⇒ 回退旧逐片排定），`setPcmPlayerImpl('queue'|'nodes')` 缺省 queue；voice-spike 深链 `player=` 切实现做 A/B，
stutter-json 带 `player`。tests +6，jest 550/550、tsc 0。

**坑**：⑫ **库的 JS 包装 `start(when=0, offset=-1)` 缺省值过不了它自己的 `offset<0` 校验**（RangeError）——首轮 A/B 队列节点静默无声：
underrun 帧 0 看着像修好了，是探针的**麦克风包络全 0、声学起播 -1ms** 揭穿的（「先证演员在场」再一次）；显式传 `offset=0`，
start 失败改 `console.warn` 不再吞；⑬ **dev-client 连着 Metro 时 `console.log` 不进 logcat**（本机 OPPO 零条 `ReactNativeJS`，
`[speech-turn]` / `stutter-json` 都只剩屏上一行）⇒ 读数改走 `uiautomator dump`（要 `MSYS_NO_PATHCONV=1`，否则 Git Bash 把
`/sdcard` 改写成本机路径）+ 慢速 `input swipe` 翻页；T6 的日志出口另立卡片；⑭ 重载 App 后 AudioContext 重建 ⇒ 旧音轨还挂在
`dumpsys` 里（active=no、计数不动），取 **active=yes** 那条而不是最大值——首轮 A/B 三次「0 增量」就是读了旧轨；
⑮ 全量重载后紧跟的深链会被吞（首个 `auto=` 没跑，ctx 仍 none），先确认页面到了再发探针。

**对话页重放（队列播放器，17:22–17:29，泓舟两次 PTT）机器半**：新建轨 101 的 underrun 帧**全程 0**，直到 17:28:35 才出现一次 1536 帧（32ms）；HAL `out_write underrun` 零散 5 次（17:24:39 ×2、17:29:15 ×3），与修前同量级；FastMixer 线程级 651 → 659。对照修前同页同问 6,587,136 帧 ⇒ 机制层闭合。

**泓舟人耳半（09-06 17:3x）**：「可以了，没有卡顿和嗡嗡声了，也能完整播报了」⇒ 验收①②③ 在 OPPO 上全过；泓舟提出换 Xiaomi（MIUI，早上听到嗡嗡的那台）再验一轮作对比。

**Xiaomi 复验（24072PX77C / HyperOS V816 / Android 16，18:15–18:38）**：HyperOS 同样不给我们 MMAP 低延迟轨（`MiAudioPolicyManager … forbidding ull track`）、走 legacy FAST 轨；探针页 13s 新旧播放器轨级 underrun 帧都只 960（20ms，机器算力更强，探针份量不够拉开）；对话页「介绍广州的历史，详细一点」（单段 4.5 分钟、4370 片 ≈ 186s 音频）**轨级 underrun 帧全程持平（960 → 960）**，泓舟：「卡顿的嗡嗡声解决了」⇒ 嗡嗡在两台机上都闭合。**新症状**：播到「二、史前与先秦时期」「四、南越国时期」各停十几二十秒。T6：`firstAudioMs 29967`、`underruns 4`、gaps **21.8 / 20.5 / 17.4 / 26.1s**；网关 `TTS stream done: first=29532ms total=272261ms`；planner：memory recall 10:25:23 → LLM plan 10:25:37（14s）→ `Plan ready: simple, info.search` 10:25:41；活跃 LLM = **MiniMax-M3（推理模型）**。⇒ 空白发生在**文本上游**（info.search 合成/LLM 出字停滞），不是播放；网关在文本停时 1s 内已把攒着的都冲刷了。见 §6.5。

**仍开**：① §6.5 上游 20s 停顿（LLM/agent 时延，出语音批账）；② 对话页常驻 UI 负载与 Reanimated
对已卸载视图持续更新（386 次/4min）另立卡片；③ dev 期 JS 日志出口（console 不进 logcat）另立卡片；④ 混合意图轮盲听。

### 6.5 「播到二、四各停十几二十秒」定因与修法（2026-09-06 傍晚，Xiaomi 对话页「介绍广州的历史，详细一点」）

**先排除「上游出字停滞」**：collector `/api/turns/91e50833562fd6f1`——turn 起 18:25:23.0，规划 LLM 两跳 18:25:37 / 18:25:41
（MiniMax-M3，14s + 4s），exa 搜索 1.45s，info 合成 LLM 18:25:52，`step.agent:info` 11.07s，`aggregate` 18:25:52.3 ⇒
**整篇文本 29.3s 时一次到齐**（path=mixed，final 整篇 speech）。T6 `firstAudioMs 29967` 正好接在后面：TTS 首片本身不慢。
网关 `TTS stream done: first=29532ms total=272261ms chunks=4370`（186s 音频）—— **4 个空白全在 TTS 流内部**，T6 gaps
21.8 / 20.5 / 17.4 / 26.1s，间隔 ≈ 60s 一个周期。

**机制**：`finish(text)` 整篇到达 ⇒ `_minimax_segments` 在首片回来前把全文按逗号切成几十段（此时 lead 恒 0 ⇒ `soft_while` 一直为真），
泵对每段问 `_tts_send_now(lead=0 <10 ⇒ 立刻发)` ⇒ 首片回来前把 18 个配额一口气发光；MiniMax 并行合成这 18 段（约 40s 音频），
之后 `bucket.acquire` 卡到 60s 窗口滚过才放下一批 ⇒ 每 60s：放 18 段（≈40s 音频）+ 空 ≈20s。四个空白 = 四个窗口。
另一处：`_RpmBucket` **每条流一个**，而 MiniMax 的 RPM 是账号级——前后脚两段回答互不知情，第二段一开就撞 1002 重连等窗口
（下午 OPPO 两段各 first=8.7–9.4s 里含这一部分；工厂每请求 new 一个 provider，桶挂实例上也无用）。

**修（`llm-gateway/providers.py`）**：
- `_tts_send_now(..., awaiting_first_audio, critical_lead_s=2, reserve_tokens=3, hold_cap_chars=1200)`：首段发出后、首片音频回来前
  **只攒不发**（余量读数是假 0；攒到 1200 字兜底发）；余量 <2s 断粮在即才不计预算立刻发；预算见底（0<tokens≤3）攒到 150 字再发；
  其余规则不变。泵：`first_sent_at` + `first_audio_wait_s=2.5`（首片超时不再等，别把整条流卡死）；收尾 flush 不受门控（整篇到达 ⇒ 首段 + 一个大请求）。
- `_shared_rpm_bucket(api_key, rpm, clock)`：进程级按 (key, rpm) 共享滑窗；注入假钟的按钟分桶（单测互不串扰）。
- tests：+4（整段到达 ≤3 请求且不等窗口 / 首片门控超时照常发 / 两条流共用配额第二条等窗口而非撞 1002 / 注册表隔离），
  改 3（`(9.9,5,3)` 预算见底改攒、rpm=2 四段合并成 2 请求不再等 60s、重连续传的是合并后的那个请求）；模块 20/20，llm-gateway 全量 299/299。

**本机真 MiniMax 对照（931 字 22 句整段一次到达，`minimax_batch_e2e.py`）**：

| | 请求数 | 首片 | 206s 音频送达用时 | RPM 等待 | 模拟播放空白 |
|---|---|---|---|---|---|
| 修后（工作树） | **2** | 1.16s | 10.5s（≈20× 实时） | 0 | **0** |
| 修前（HEAD） | 见下 | | | | |

**坑**：⑯ **同一个「20 秒停顿」两种病**：早上 §6.3 候选 ④ 是 LLM/planner 时延，这次 collector 证明文本 29s 已到齐、空白在 TTS 流里——
先用 trace 把「文本何时到」钉死，再看播放；⑰ **余量驱动的策略在「余量未知」时会失效**：首片回来前 lead=0 与「客户端断粮」长得一样，
策略必须区分「没读数」和「读数为 0」；⑱ 限流桶的作用域要和服务商的限流作用域一致（账号级 vs 每流）；
⑲ 探针的 paced 喂法（30 字/秒）测不出整段到达的形态——形态本身是变量，两种都要喂。

**仍开**：① 部署（dry-run → 泓舟授权 apply）；② 部署后 Xiaomi 对话页重放同一问题验收（T6 gaps ≈ 空、轨级 underrun 持平）；
③ 规划 14s + 合成 9.5s 的首音 29s 是 LLM 侧时延（MiniMax-M3 推理模型），另立卡片。
