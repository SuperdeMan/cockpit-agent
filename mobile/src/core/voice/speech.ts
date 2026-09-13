// 播报控制器（实施计划 M2-3）：SessionCore 的 SpeechSink 端口实现。
//
// 职责分界刻意画在这：**「开没开播报」「用哪个引擎/音色」这类读设置的判定住在这里**，
// SessionCore 只管「哪一轮该出声」。这样会话状态机继续是零副作用、jest 可回放的纯逻辑，
// 而设置一改（关掉播报）立刻能停当前这段——两件事各自有唯一的落点。
//
// 段链（语音批 T4′，2026-09-06，修 C3「播报卡顿」）：一轮播报不再是「一个 TtsSession」而是**会话队列**。
// 收尾之后还有话——divergent final（final 与已流内容是两段话）、mixed 形态（本地回执 final 先到、云端段随后）——
// 就另起一个流式会话，**立刻**建连送文本合成，音频闸在前一段的 completion 上（`TtsSession.gateUntil`），
// 前一段播完开闸接着播。真机读数（计划 §1.5，包锚 2026-09-04 23:41:40）：旧实现 divergent 走批处理补段
// 留下 2507/2623ms 空白，mixed 把云端段整段丢掉；HMI 2026-07-18 段链修的正是这组断口。
// 与 HMI 的差别：HMI 等前一段 completion 之后才开始轮转（新会话从建连起算），这里合成提前、只闸播放。
// 段间 `speaking` 不落，`onSpeechEnded` / `onSilent` 只在队列排空 + 宽限后触发一次——免唤醒 FSM 的
// ttsEnd 会把 SPEAKING 打到 FOLLOWUP 开麦，多段中途落一次就是一次误开麦。
import { PendingSpeech } from '@shared/proactiveSpeech.mjs'

import { findByBubble, markInteraction, type TimelineEvent } from '../obs/turnTimeline'
import type { SpeechSink } from '../session/store'
import { settingsStore, speakAllowed } from '../settings/store'
import { newPcmPlayer, scheduleAudioIdle, sharedAudioContext } from './audioCtx'
import { setAudioPlaybackFact } from './playbackFacts'
import { proactiveSpeechDecision } from './proactivePolicy'
import { ttsStreamUrl } from './audioUrls'
import { TtsSession, synthesizeBatch, type TtsConfig } from './tts'
import { warmSocket } from './warmSocket'

 

/** 队列排空后到宣布「这轮播完」的宽限：给 mixed 形态里紧跟第一段之后才到的云端段一个接上的机会
 *  （同 HMI `markTtsMaybeEnd` 的 250ms 复判）。 */
export const SEGMENT_GRACE_MS = 250

/** 一轮播报的读数（T6 观测口）：探针/取证读它；生产路径只记录不判断。__DEV__ 下另打一行 `[speech-turn]` 到 console（logcat 可抓） */
export interface TurnReport {
  at: number
  bubbleId: string
  provider: string
  sounded: boolean
  /** 出过声的段数 */
  segments: number
  /** 前一段收尾 → 后一段首片起播（ms） */
  gapsMs: number[]
  firstAudioMs: number
  totalMs: number
  sessions: {
    divergent: boolean
    startedAtMs: number
    firstAudioAtMs: number
    endedAtMs: number
    chunks: number
    bytes: number
    underruns: number
    gaps: { atSec: number; gapMs: number }[]
  }[]
}

const TURN_LOG_CAP = 20

/** 一次性播一段 PCM（设置页试听回退、主动消息批处理）；返回播完的 promise */
function playPcm(pcm: Int16Array, sampleRate: number): { player: any; done: Promise<void> } {
  const player = newPcmPlayer({ sampleRate })
  player.push(pcm)
  const ms = player.remainingSec() * 1000 + 120
  const done = new Promise<void>((res) => setTimeout(res, ms))
  return { player, done }
}

/** 「整段没出声」的三种成因（AR05 F05）：只有第一种是服务失败。 */
export type SilentKind = 'synthesis_failed' | 'interrupted' | 'no_text'

export class SpeechController implements SpeechSink {
  /** 播报整段没出声时的出口（wiring 注入 → 屏上一句提示）。不设即静默，同旧行为。
   *
   *  第二参是**为什么没出声**（AR05 F05）。三者必须分开：
   *   · `synthesis_failed` —— 本轮有话要说、自然收尾、却一个字节都没出 ⇒ 真的该报；
   *   · `interrupted` —— 用户按停 / 换轮 / barge-in 打断 ⇒ **不是服务失败**，报了就是冤枉；
   *   · `no_text` —— 纯卡片回复，本来就没有要播的话 ⇒ 同样不该报。
   *  旧消费方只读第一参，行为逐字不变。 */
  onSilent: ((reason: string, kind: SilentKind) => void) | null = null
  /** M4 免唤醒回路的两条腿：真出声了 → FSM 进 SPEAKING；播完 → FSM 进 FOLLOWUP。
   *  **必须挂在「首片音频真起播」而不是「begin 被调用」上**——begin 之后可能一个字节
   *  都不出（引擎无 key / 纯卡片回复），那种情况下 FSM 不该进 SPEAKING 再等一个永远
   *  不来的 ttsEnd。没出声的那条腿由 onSilent → 调用方补 turnEnded() 走。 */
  onSpeechBegan: ((text: string) => void) | null = null
  /** 本轮播报文本**每次变长都要报一次**（2026-08-29 真机定位）。
   *  ⚠ 别指望 `onSpeechBegan` 那一次：它挂在**首片音频**上，而那一刻 `spokenText`
   *  还基本是空的（文本靠 `delta()` 之后才累积）——真机实测送出去的是 `len=0`，
   *  于是 FSM 的回声防线拿着空串，`_overlapsTts` 第一行就短路，**整条防线空转**。
   *  拿它当回声参照的消费方要的是「**此刻已经播出去了什么**」，那是个会变的量，
   *  只送一次必然是错的。 */
  onSpeechText: ((text: string) => void) | null = null
  onSpeechEnded: (() => void) | null = null
  /** 播报中（首片音频起播 → 播完/停）。Presence 的 agent 轴读它；**可多订阅**，
   *  不再要求消费方链式覆盖 onSpeechBegan/Ended（那套写法第二个消费方就会把第一个顶掉）。 */
  private streamSpeaking = false
  // 批处理拥有自己的播放事实；它结束时不能清掉同一控制器仍在播放的主链段。
  private batchPlaying = false
  private batchActive = false
  private readonly batchOwner = {}
  get speaking(): boolean { return this.streamSpeaking || this.batchPlaying }
  private readonly speakingSubs = new Set<(v: boolean) => void>()
  /** 本轮会话队列：[0] 在播（或即将播），其后各段闸在前一段的 completion 上 */
  private queue: TtsSession[] = []
  private extra: { player: any } | null = null
  private bubble = ''
  private emotion = ''
  private beganAt = 0
  /** 本轮已流式出去的文本（FSM 用它判「助手是不是念到了唤醒词」抑制自触发） */
  private spokenText = ''
  private streamEchoText = ''
  private batchEchoText = ''
  /** 当前已起播通路的参照文本，供手动录音保存快照。
   *  文本与音频未逐字对齐，只能用于「待核对」，不能据此断言说话人或自动删除真复述。
   *  合成等待、停止、播完后均返回空，旧轮文本不能影响下一次录音。 */
  get echoReference(): string {
    return [this.streamSpeaking ? this.streamEchoText : '', this.batchPlaying ? this.batchEchoText : '']
      .filter(Boolean).join(' ')
  }
  /** 首音时延（ms，验收判据「体感 <1.5s」的机器读数）；未出声为 0 */
  lastFirstAudioMs = 0
  /** begin 时按三档裁决的结果；finish 尊重它（同一轮不许 begin 说播、finish 又不播） */
  private allowed = false
  /** 本轮出过声没有（宽限收尾时决定报不报 onSilent） */
  private turnSounded = false
  private graceTimer: ReturnType<typeof setTimeout> | null = null
  private lastSegEndAt = 0
  /** 本轮读数（探针 / 验收读它）：segments = 出过声的段数；gapsMs = 前一段收尾 → 后一段首片起播 */
  turnStats: { segments: number; gapsMs: number[] } = { segments: 0, gapsMs: [] }
  /** 本轮各段会话的元数据（收尾时汇成 TurnReport） */
  private turnSessions: {
    session: TtsSession
    divergent: boolean
    startedAt: number
    firstAudioAt: number
    endedAt: number
  }[] = []
  private readonly turnLog: TurnReport[] = []
  private readonly turnSubs = new Set<(r: TurnReport) => void>()
  /** 主动消息仲裁要的两个事实（ChatScreen 用 setter 喂，同 setAudioUrl 形态）：
   *  控制器本来就读设置，但它不认识行车档与 S2S 在不在忙 */
  private proactiveCtx = { driving: false, s2sBusy: false }
  /** DEFER 队列（共享 `PendingSpeech`：有界 3 条、按 deliveryId 去重、溢出丢最旧） */
  private readonly deferred = new PendingSpeech()
  private flushing = false
  private foreground = true
  private stopEpoch = 0

  constructor(private audioUrl: string) {}

  setAudioUrl(url: string): void {
    this.audioUrl = url
  }

  /** 宿主同步撤回；回前台只开闸，旧轮的 allowed/异步结果不会恢复。 */
  setForeground(active: boolean): void {
    if (this.foreground === active) return
    this.foreground = active
    if (!active) this.stop()
  }

  setProactiveCtx(ctx: { driving: boolean; s2sBusy: boolean }): void {
    const wasBusy = this.proactiveCtx.s2sBusy
    this.proactiveCtx = ctx
    // 「S2S 空闲即补播」（M-C 头注）：DEFER 的**阻塞条件就是 s2sBusy**（decideSpeech 只在
    // s2sBusy 时给 DEFER），所以补播挂在这一刻
    if (wasBusy && !ctx.s2sBusy) void this.flushDeferred()
  }

  /** 主动消息到达：三档 + 行车事实 → 说 / 抢话 / 排队 / 只气泡（判据 proactivePolicy.ts） */
  proactive(text: string, msg: { priority?: string; hasCard: boolean; deliveryId?: string }): void {
    if (!this.foreground) return
    const policy = settingsStore.getState().settings.speakPolicy
    const d = proactiveSpeechDecision(
      { priority: msg.priority, hasText: !!text.trim(), hasCard: msg.hasCard },
      { policy, driving: this.proactiveCtx.driving, s2sBusy: this.proactiveCtx.s2sBusy,
        ttsBusy: this.batchActive || this.queue.length > 0 || this.speaking },
    )
    if (d === 'bubble') return
    if (d === 'defer') {
      this.deferred.push({ text, deliveryId: msg.deliveryId })
      return
    }
    if (d === 'interrupt') this.stop()
    void this.speakBatch(text)
  }

  /** 补播 DEFER 队列。
   *  ⚠ 补播跟占用解除走：`begin()` 第一件事就是 `stop()`，把补播挂在那里会让
   *  攒下的旧话在**新一轮开口的瞬间**倒出来，两段音频叠着放——正是 M-C 头注要避免的那件事。
   *  真正的阻塞条件是 ① S2S 在忙、② 自己的播报在跑，所以挂在这两条各自解除的那一刻。 */
  private async flushDeferred(): Promise<void> {
    if (!this.foreground || this.flushing || this.batchActive || this.speaking || this.proactiveCtx.s2sBusy) return
    this.flushing = true
    const epoch = this.stopEpoch
    try {
      // 串行：drain() 一次给全部，同时喂给 speakBatch 就是几段音频叠着放
      for (const it of this.deferred.drain()) {
        if (!this.foreground || epoch !== this.stopEpoch) break
        await this.speakBatch(it.text)
      }
    } finally {
      this.flushing = false
    }
  }

  subscribeSpeaking(fn: (v: boolean) => void): () => void {
    this.speakingSubs.add(fn)
    return () => {
      this.speakingSubs.delete(fn)
    }
  }

  /** 订阅每轮收尾的读数；返回退订函数 */
  subscribeTurnReports(fn: (r: TurnReport) => void): () => void {
    this.turnSubs.add(fn)
    return () => {
      this.turnSubs.delete(fn)
    }
  }

  /** 最近 TURN_LOG_CAP 轮的读数（最新在后） */
  turnReports(): readonly TurnReport[] {
    return this.turnLog
  }

  private emitTurnReport(): void {
    const now = Date.now()
    const report: TurnReport = {
      at: now,
      bubbleId: this.bubble,
      provider: settingsStore.getState().settings.ttsProvider,
      sounded: this.turnSounded,
      segments: this.turnStats.segments,
      gapsMs: [...this.turnStats.gapsMs],
      firstAudioMs: this.lastFirstAudioMs,
      totalMs: this.beganAt ? now - this.beganAt : 0,
      sessions: this.turnSessions.map((m) => ({
        divergent: m.divergent,
        startedAtMs: m.startedAt - this.beganAt,
        firstAudioAtMs: m.firstAudioAt ? m.firstAudioAt - this.beganAt : -1,
        endedAtMs: m.endedAt ? m.endedAt - this.beganAt : -1,
        chunks: m.session.stats.chunks,
        bytes: m.session.stats.bytes,
        underruns: m.session.stats.underruns,
        gaps: [...m.session.stats.gaps],
      })),
    }
    this.turnLog.push(report)
    if (this.turnLog.length > TURN_LOG_CAP) this.turnLog.shift()
    for (const fn of this.turnSubs) {
      try {
        fn(report)
      } catch {
        /* 一个观察者抛异常不该影响别人，更不该影响收尾 */
      }
    }
    if (typeof __DEV__ !== 'undefined' && __DEV__) {
       
      console.log('[speech-turn]', JSON.stringify(report))
    }
  }

  private setSpeaking(v: boolean): void {
    if (this.streamSpeaking === v) return
    const before = this.speaking
    this.streamSpeaking = v
    // AR03：主链这一路的播放事实。挂在既有的 speaking 翻转上——它本来就是「首片音频起播 → 播完/停」，
    // 与 `playbackFacts` 要的语义逐字相同，不另立第二个时刻
    setAudioPlaybackFact(this, v)
    if (before !== this.speaking) for (const fn of this.speakingSubs) fn(this.speaking)
  }

  private setBatchPlaying(v: boolean): void {
    if (this.batchPlaying === v) return
    const before = this.speaking
    this.batchPlaying = v
    setAudioPlaybackFact(this.batchOwner, v)
    if (before !== this.speaking) for (const fn of this.speakingSubs) fn(this.speaking)
  }

  private cfg(emotion: string): TtsConfig {
    const s = settingsStore.getState().settings
    return {
      audioUrl: this.audioUrl,
      provider: s.ttsProvider,
      voice: s.voiceId,
      ...(emotion ? { emotion } : {}),
    }
  }

  begin(bubbleId: string, emotion: string, voice = false): void {
    if (!this.foreground || !speakAllowed(settingsStore.getState().settings.speakPolicy, voice)) {
      this.stop()
      return
    }
    this.resetTurn(bubbleId, emotion)
    this.allowed = true
    this.openSession(null)
  }

  /** 本轮气泡对应的时间线（AR08）。没开轮就返回 null——诊断缺席不影响播报，
   *  但也**绝不**在这里补开一轮：补出来的轮没有发送/ASR 那半段，会污染分母。 */
  private markTurn(event: TimelineEvent, detail?: string, bubbleId = this.bubble): void {
    if (!bubbleId) return
    const id = findByBubble(bubbleId)?.interactionId
    if (id) markInteraction(id, event, detail ? { detail } : undefined)
  }

  /** 上一轮没播完就发了新的：先停，两轮同时出声比少听一句更糟；再把本轮读数归零 */
  private resetTurn(bubbleId: string, emotion: string): void {
    this.stop(false) // 新轮仍允许自然结束后补播；显式停播/后台撤回才清空 DEFER。
    this.bubble = bubbleId
    this.emotion = emotion
    this.beganAt = Date.now()
    this.lastFirstAudioMs = 0
    this.spokenText = ''
    this.streamEchoText = ''
    this.turnSounded = false
    this.lastSegEndAt = 0
    this.turnStats = { segments: 0, gapsMs: [] }
    this.turnSessions = []
  }

  private tail(): TtsSession | null {
    return this.queue.length ? this.queue[this.queue.length - 1] : null
  }

  /** 起一段流式会话并入队。gate 非空 = 闸在前一段的 completion 上（合成不等、播放等）；divergent = 因 final 与已流内容是两段话而另起 */
  private openSession(gate: Promise<void> | null, divergent = false): TtsSession {
    this.cancelGrace()
    // 要出声了：现在就把输出上下文拿到手（空闲挂起过的原地 resume）。这一刻离首片音频还隔着
    // 规划 + 合成那 2s+，resume 的成本全藏在里面；等首片到了再 resume 就落在首音时延上。
    sharedAudioContext()
    const epoch = this.stopEpoch
    const rec = { session: null as unknown as TtsSession, divergent, startedAt: Date.now(), firstAudioAt: 0, endedAt: 0 }
    const session = new TtsSession(this.cfg(this.emotion), {
      onFirstChunk: () => {
        if (!this.foreground || epoch !== this.stopEpoch) return
        this.markTurn('first_pcm_received', divergent ? 'divergent-segment' : 'segment')
      },
      onFirstAudio: () => {
        if (!this.foreground || epoch !== this.stopEpoch) return
        rec.firstAudioAt = Date.now()
        // ⚠ play_scheduled，**不是** audible_onset：这一刻只是 node.start() 已经调过
        this.markTurn('play_scheduled', divergent ? 'divergent-segment' : 'segment')
        if (!this.turnSounded) this.lastFirstAudioMs = Date.now() - this.beganAt
        this.turnSounded = true
        this.turnStats.segments += 1
        if (this.lastSegEndAt) this.turnStats.gapsMs.push(Date.now() - this.lastSegEndAt)
        const wasSpeaking = this.speaking
        this.setSpeaking(true)
        // 段间不落 ⇒ 多段一轮只报一次「开始」（FSM 的 ttsStart 只认 THINKING 态，重复调是空转）
        if (!wasSpeaking) this.onSpeechBegan?.(this.spokenText)
      },
      onEnd: () => {
        if (epoch !== this.stopEpoch) return
        rec.endedAt = Date.now()
        this.onSegmentEnd(session)
      },
    })
    rec.session = session
    // AR03：这一路「还可能出声」从建会话起算——停播键的可用面读它（首片未起播的缓冲段也要能停）
    setAudioPlaybackFact(this, true, 'live')
    this.turnSessions.push(rec)
    if (gate) session.gateUntil(gate)
    this.queue.push(session)
    session.start()
    // 本段文本送去合成的时刻（AR08）。挂在这里而不是 delta()：mixed / divergent 各自另起一段，
    // 每段都该有自己的「送出 → 首片 → 排定」三点，合成一条会把段间空白算进首片时延。
    this.markTurn('tts_text_sent', divergent ? 'divergent-segment' : 'segment')
    return session
  }

  private stopping = false

  private onSegmentEnd(session: TtsSession): void {
    const i = this.queue.indexOf(session)
    if (i >= 0) this.queue.splice(i, 1)
    this.lastSegEndAt = Date.now()
    if (this.stopping) return // stop() 统一收尾，不在这里再挂宽限
    if (this.queue.length) return // 下一段已闸在本段 completion 上，开闸自动接着播；speaking 不落
    this.armGrace()
  }

  private armGrace(): void {
    this.cancelGrace()
    this.graceTimer = setTimeout(() => {
      this.graceTimer = null
      if (this.queue.length) return
      this.finishTurn(true)
    }, SEGMENT_GRACE_MS)
  }

  private cancelGrace(): void {
    if (this.graceTimer !== null) {
      clearTimeout(this.graceTimer)
      this.graceTimer = null
    }
  }

  /** 这轮播报的唯一收尾出口：先出读数，再 speaking 落、没出过声报 onSilent、报 onSpeechEnded、（自然收尾才）补播 DEFER。
   *  `natural` = 队列自己放空后过了宽限；`false` = 被 `stop()` 打断（换轮 / barge-in / 用户按停）。
   *  **DEFER 只跟自然收尾走**（AR03 修 R06）：此前无条件补播 ⇒ 用户按下「停止播报」的同一瞬间，
   *  攒着的主动消息立刻开口，「一步只停播、队列清空」当场不成立。AR04：显式停止清 DEFER，
   *  文字记录保留；普通新轮的 resetTurn 不清队列，自然收尾仍可补播。 */
  private finishTurn(natural: boolean, bubbleId = this.bubble): void {
    // ⚠ bubbleId 得显式传：`stop()` 会先把 `this.bubble` 清掉再叫进来，
    // 拿字段当归属的话「用户按了停」这一下永远落不到任何一轮上（本轮实测）。
    this.markTurn(natural ? 'play_ended' : 'stopped', this.turnSounded ? 'sounded' : 'silent', bubbleId)
    if (this.turnSessions.length) this.emitTurnReport()
    const sounded = this.turnSounded
    this.setSpeaking(false)
    setAudioPlaybackFact(this, false, 'live')
    if (!sounded) {
      const s = settingsStore.getState().settings
      // 取消 / 主动停播不是服务失败；纯卡片轮本来就没词可播。判据在这里定一次，
      // 消费方不再各自猜「这次该不该提示」（AR05 §5.1「取消、主动静音不能被当成服务失败」）。
      const kind: SilentKind = !natural
        ? 'interrupted'
        : this.spokenText.trim()
          ? 'synthesis_failed'
          : 'no_text'
      this.onSilent?.(`当前播报引擎（${s.ttsProvider}）没有返回音频，可在设置里换一个`, kind)
    }
    this.onSpeechEnded?.()
    if (natural) void this.flushDeferred()
    // 这一轮没声音了：空闲一段时间后挂起输出上下文（补播 / 下一轮 / 提示音再取用时自动 resume）
    scheduleAudioIdle()
    // 给下一轮预热一条合成连接（warmSocket.ts）：下一轮 begin 时握手已经做完
    if (this.foreground && this.audioUrl) warmSocket(ttsStreamUrl(this.audioUrl))
  }

  delta(bubbleId: string, text: string): void {
    if (!this.foreground || !this.allowed || bubbleId !== this.bubble) return
    this.spokenText += text
    this.streamEchoText = this.spokenText
    this.onSpeechText?.(this.spokenText) // 让 FSM 手里那份参照文本跟着变长（见 onSpeechText 头注）
    const tail = this.tail()
    if (tail && !tail.spent) {
      tail.append(text)
      return
    }
    // 收尾之后还有话（mixed：本地回执 final 已到，这是云端段）→ 另起一段，闸在前一段上；前一段已播完则不闸
    this.openSession(tail ? tail.completion : null).append(text)
  }

  finish(bubbleId: string, text: string): void {
    // 三档在 begin 裁过；这里再看一眼「静音」——用户可能在这一轮中途把它关了
    if (!this.foreground || !this.allowed || settingsStore.getState().settings.speakPolicy === 'silent' || !text) return
    // **整句定稿在这里，三条分支都要报**（2026-08-29 第二次真机复跑才补上）：
    // 上一版只在 `delta()` 与 `speakBatch()` 报，而 `sameSegment=true`（流式正常收尾）
    // 这条**最常走的路径**两个都不经过；答案若是一次性 final 送达（一次 delta 都没有），
    // `spokenText` 更是从头到尾空着 ⇒ FSM 的回声参照仍是空串，防线照旧空转。
    // `text` 就是本轮要播的整句，正是回声判据要比对的那一份。
    this.onSpeechText?.(text)
    this.streamEchoText = text
    // 会话对不上（begin 时还没开播报 / 已被停）：整段走批处理，不静默丢掉这次播报（旧行为原样保留）
    if (bubbleId !== this.bubble) {
      void this.speakBatch(text)
      return
    }
    const tail = this.tail()
    if (!tail) {
      // 首段已播完、宽限内外云端 final 才到（mixed 慢到）→ 另起流式段，不再走批处理
      this.openSession(null).finish(text)
      return
    }
    if (!tail.spent) {
      const sameSegment = tail.finish(text)
      // divergent：本段按已流内容收尾，final 另起一段**立刻合成**、闸在本段上——等本段播完开闸接着播
      if (!sameSegment) this.openSession(tail.completion, true).finish(text)
      return
    }
    // tail 已收尾（mixed：本地回执 final 已到，这是云端 final）→ 新段
    this.openSession(tail.completion).finish(text)
  }

  /** 服务端拒识是无声终态。即使设置为静音、从未建 TTS 会话，也须结束 FSM 的等待。 */
  endSilentTurn(): void {
    const hadTurn = this.queue.length > 0
    this.stop()
    if (!hadTurn) this.onSpeechEnded?.()
  }

  stop(clearDeferred = true): void {
    this.stopEpoch++
    this.allowed = false
    if (clearDeferred) this.deferred.drain()
    this.cancelGrace()
    const hadTurn = this.queue.length > 0
    const endedBubble = this.bubble
    const q = this.queue
    this.queue = []
    this.bubble = ''
    this.stopping = true
    try {
      for (const s of q) s.stop()
    } finally {
      this.stopping = false
    }
    this.extra?.player?.stop()
    this.extra = null
    this.batchActive = false
    this.setBatchPlaying(false)
    setAudioPlaybackFact(this.batchOwner, false, 'live')
    // 旧语义原样保留：停掉一个活着的轮也算这轮收尾（没出过声 ⇒ onSilent；免唤醒靠这两条收 THINKING）
    if (hadTurn) this.finishTurn(false, endedBubble)
    else {
      this.setSpeaking(false)
      setAudioPlaybackFact(this, false, 'live')
    }
  }

  /** 设置页试听：走**流式**这条真实路径。
   *  **返回值是「有没有真出声」**——不是「有没有跑完」。原注释写的「无 key 引擎无感回退
   *  出声」是个假前提（tts.ts 头注查实了四段链，没有一段会换引擎），设置页据此把
   *  「没响」显式说出来，而不是让用户对着一个安静的手机猜。 */
  async preview(text: string): Promise<boolean> {
    if (!this.foreground) return false
    this.resetTurn('__preview__', '')
    const epoch = this.stopEpoch
    const session = this.openSession(null)
    session.finish(text)
    await session.completion
    return epoch === this.stopEpoch && this.turnSounded
  }

  /** 批处理播一段（主动消息 / 会话对不上时的兜底共用） */
  async speakBatch(text: string): Promise<boolean> {
    if (!this.foreground || this.batchActive) return false
    this.batchActive = true
    this.batchEchoText = text
    const epoch = this.stopEpoch
    setAudioPlaybackFact(this.batchOwner, true, 'live') // 合成等待也必须有停止出口。
    // 批处理这条腿不走 `delta()`，整句一次给 ⇒ 参照文本要在这里补一次，
    // 否则「流式不可用 → 回落批处理」的那些轮回声防线又是空转的（同 onSpeechText 头注）
    this.onSpeechText?.(text)
    try {
      const out = await synthesizeBatch(this.cfg(''), text)
      if (!this.foreground || epoch !== this.stopEpoch || !out) return false
      const { player, done } = playPcm(out.pcm, out.sampleRate)
      this.extra = { player }
      this.setBatchPlaying(true)
      await done
      if (epoch !== this.stopEpoch) return false
      this.setBatchPlaying(false)
      setAudioPlaybackFact(this.batchOwner, false, 'live')
      if (this.extra?.player === player) this.extra = null
      return true
    } catch {
      return false
    } finally {
      if (epoch === this.stopEpoch) {
        this.batchActive = false
        if (!this.extra) setAudioPlaybackFact(this.batchOwner, false, 'live')
        void this.flushDeferred()
        scheduleAudioIdle()
      }
    }
  }
}

let controller: SpeechController | null = null

export function speechController(audioUrl?: string): SpeechController {
  if (!controller) controller = new SpeechController(audioUrl ?? '')
  else if (audioUrl) controller.setAudioUrl(audioUrl)
  return controller
}
