# 语音批 · 播报卡顿定因与修复 实施计划（逐任务，先测后码）

> 归属：Android 陪伴端 UX v2.1 收口后的**语音批**（与「KWS 阈值 A/B 批」并列的两个 B5 遗留项之一）。
> 上游账：`docs/design/2026-09-04-mobile-ux-v2-b5-implementation-plan.md` §6.2 遗留②、AGENTS.md Android 行。
> 本计划**不含** KWS 阈值 A/B（0.2/2.0 调参另拆，见 B4 §6.4 交裁 #4）。两批都动语音链路，
> 分开做是为了单变量：调 KWS 阈值会改送进唤醒的音频，与播报输出无关，混在一起读数不可归因。

---

## 0. 接手须知（先读）

**本计划推翻了 B5 留下的下一个嫌疑方向。** B5 §6.2 遗留② 写「别再沿 Xruns 找、首要嫌疑 16kHz
VOIP 通路」。本会话（2026-09-05）真机取证后**改判**：播报卡顿不是 HAL deadline miss（B5 已证），
也没有证据指向 16kHz VOIP 的音质/延迟；真机上唯一被坐实的机制是**输出 AudioContext 空闲期从不挂起、
被 MIUI 以「长时间零数据」静音**。详见 §1。

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

### 1.1 输出路径在播报期是健康的（复核 B5，方向一致）

真机驱动一轮长回答（`am start` 到前台 → 注入问句 → 发送），期间每 4s 采 `dumpsys media.audio_flinger`：

- 本应用输出 track 全程 `F3`（FAST）/ 48000Hz / `fmt 5`（PCM_FLOAT）/ 播放音量 −19dB（不是 −inf），确在出声；
- **FastMixer `underruns` 75s 内 192 → 193**（1 次），`writeErrors=0`；
- logcat 本轮**零** `Choreographer: Skipped N frames`、**零** `Reanimated ... synchronouslyUpdateUIProps failed`（对比 B3 那轮 150–376 条）。

⇒ 播报**进行中**没有原生 deadline miss，也没有 JS/UI 线程卡死信号。B5 的 Xrun 结论在本设备复现。

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

---

## 2. 两个候选（按证据强弱排序）

| # | 候选 | 机制 | 证据 | 该不该改 |
|---|---|---|---|---|
| **C1** | **空闲静音起播毛刺** | AudioContext 不挂起 → 持续写零 → MIUI 60s 后静音 → 下句起播被吞/毛刺 | **前置条件已实证**（§1.2 logcat）；与泓舟「第 1、2 步」时序吻合 | **该改**（同时是电量账：LowLatency 独占流 24h 常开） |
| **C2** | **JS 线程碎片调度负载** | minimax 707 碎片 × 每片同步重采样 + 5 JSI，与渲染/动画抢 JS 线程 → pcmPlayer 起点重排 → 可闻空白 | 架构可推（§1.3）；本轮**无正向实证**（FastMixer 平、无 Skipped frames） | 待 Phase 1 定；确认才改 |

「pcmPlayer 起点重排」= 可闻空白但 FastMixer 不计 underrun 的第二条隐形通道：JS 迟推一片 → 上一片播完
到下一片 `start(now)` 之间 destination 渲零 → 混音器按时拿到零 → 听感是空白、指标是 0。与 C1 同一类「指标盲区」。

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

### Phase 2 — 修复（按 Phase 1 定的因，逐条独立提交、各配肯定式验收）

**T4 修 C1：空闲挂起 AudioContext（首选，JS-only）**
- `audioCtx.ts` 加 `suspendSharedAudioContextWhenIdle()` / 或由 `SpeechController` 在 `speaking` 落 false
  且 DEFER 队列空后 grace（建议 2–3s，避开连播/提示音）挂起，下一次 `newPcmPlayer`/`cueTone` 前 `resume()`；
- 顾到 `cueTone` 也用同一 ctx（挂起判据要问「有没有任何消费方在用」，不能只看 TTS）；
- **必测 resume 代价**：resume 后首音是否越过验收判据 <1.5s（现读 minimax 646ms、cosyvoice 650ms，有 2.7× 余量，
  但 resume 独占 LowLatency 流可能加 warm-up）；若 resume 太贵，退一步用「keep-alive 到 idle N 秒再 suspend」两级。
- 附带收益：Oboe 独占低延迟流不再 24h 常开（电量）。

**T5 修 C2（仅当 Phase 1 确认）：降 JS 线程碎片压力（JS-only）**
候选（择一或组合，Phase 1 读数定）：① 抬高 `pcmPlayer` 首片 jitter 下限 / 按引擎分档（minimax 碎片多，
给更大缓冲）；② 在 `audioCtx.ts` shim 层**合片**（攒到 ≥N ms 再 createBuffer/start，减少 JSI 与 source 数）；
③ 把重采样从每片同步路径挪开。**不改 `pcmPlayer.mjs` 本身**（共享）——参数经构造入参、合并逻辑在 mobile shim。

**T6 记录收口** 回填本计划 §6；更新 AGENTS.md 开项指针（改后端 16kHz 嫌疑那句）与 design/README 行。

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

（待开工）
