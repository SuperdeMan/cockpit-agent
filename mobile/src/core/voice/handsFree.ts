// 免唤醒回路控制器（实施计划 M4-4）：把**引擎无关**的 `@shared/voiceLoop.mjs` FSM
// 接到 RN 侧的真实外设——VAD（onnxruntime）/ KWS（sherpa 原生）/ ASR（既有 AsrSession）/
// 效果回调（send / stopTts / orb / partial / notice）。
//
// 与 HMI `handsFreeController.ts` 是**同一位置的两个平台实现**。FSM 一字不改是硬要求：
// 「什么时候算说完、误唤醒怎么回收、打断算不算数」这些判据只许有一份。
//
// 四处 RN 特有的处置（都不是判据差异）：
//  1. **一路麦、多路消费**走 `micBus`。HMI 靠共享 MediaStream（它自己记为「架构债 A」），
//     RN 侧没有 MediaStream，改成显式的帧总线——反而更干净。
//  2. **ASR 不自己开麦**。给 `AsrSession` 注入一个 `PushRecorder`（下方），由本控制器把
//     帧推进去。这样 ASR 结束时 `rec.stop()` 停的是那个假 recorder，**真麦继续开着**
//     ——「答完之后免唤醒续问」全靠这一条。
//  3. **pre-roll 走 PcmRing**，喂的是 VAD 重切出来的 512 样本窗（见 vad.ts::onWindow）。
//  4. **原生缺席即整条链不出现**：`enable()` 前先问 `handsFreeAvailable()`，UI 据此
//     连开关都不渲染（坑账 §9.27——原生缺席时崩的是原生线程，ErrorBoundary 兜不住）。
//
// 本批**刻意不做**的（写清楚免得被读成遗漏）：声纹识别（红线：声纹不作鉴权因子，
// 且 §2.3 信道约束下 App 不做注册入口）、RejectPolicy 拒识收紧（云端拒识信号 App 侧
// 尚未接出来）、唤醒提示音（要 mp3 解码，而 M2 关掉了 FFmpeg——见 app.config.ts）。
import { PcmRing } from '@shared/pcmRing.mjs'
import { S2SClient, s2sUrl } from '@shared/s2sClient.mjs'
import { VoiceLoop } from '@shared/voiceLoop.mjs'
import { stripLeadingWakeWord } from '@shared/utteranceHeuristics.mjs'

import { AsrSession, type AsrConfig } from './asr'
import { newPcmPlayer } from './audioCtx'
import { DEFAULT_KEYWORDS, KwsEngine, kwsNativeAvailable } from './kws'
import { micLease } from './micBus'
import { FRAME_SAMPLES, type FrameSink, type Recorder } from './recorder'
import { VadEngine, vadNativeAvailable } from './vad'

/* eslint-disable @typescript-eslint/no-explicit-any */

/** 续说/打断开 ASR 时注入的前滚（同 HMI 的 RESUME_PRE_ROLL_MS） */
const RESUME_PRE_ROLL_MS = 200
/** pre-roll 上限（PcmRing 容量 1500ms，同 HMI 的 MAX_PRE_ROLL_MS） */
const MAX_PRE_ROLL_MS = 1200
/** 唤醒词也会被 ASR 听见 → 定稿前剥掉（同 HMI；词表与 KWS 预设同源） */
const WAKE_WORDS = ['小舟小舟', '小舟']
/** FSM 判为「本地消化、不上云」的语义事件——S2S 下必须额外取消 provider 在飞的生成
 *  （它不知道我们把这句判成了噪声/退出）。名字与 voiceLoop.onMetric 的事件名一一对应。 */
const S2S_LOCAL_HANDLED = new Set(['exit_word', 'filler_dismissed', 'false_wake_dismissed'])

/**
 * 假 recorder：实现 `Recorder` 接口但不碰设备，帧由控制器 `push` 进来。
 * 它存在的唯一理由见头注 2——让 `AsrSession` 一行不改就能挂在常开麦上。
 */
class PushRecorder implements Recorder {
  private sink: FrameSink | null = null
  private _rate = 0

  get recording(): boolean {
    return !!this.sink
  }

  get deviceRate(): number {
    return this._rate
  }

  setDeviceRate(r: number): void {
    this._rate = r
  }

  async start(onFrame: FrameSink): Promise<void> {
    this.sink = onFrame
  }

  async stop(): Promise<void> {
    this.sink = null
  }

  push(frame: Int16Array): void {
    this.sink?.(frame)
  }
}

export interface HandsFreeDeps {
  audioUrl: string
  getAsrConfig(): Pick<AsrConfig, 'language' | 'provider' | 'model' | 'fallbackModel'>
  getSessionId(): string
  /** 定稿通过 FSM 本地治理 → 派发给对话链路（= SessionCore.send） */
  onSend(text: string, voice: { source: string; utteranceMs: number }): void
  /** barge-in：停播报 */
  onStopTts(): void
  /** (orbState, fsmState)；orbState=null 表示回 IDLE，把麦态交还给 PTT */
  onOrbState(orb: string | null, fsm: string): void
  onPartialText?(text: string): void
  /** THINKING 期被唤醒词打断 → 取消在飞的云端轮 */
  onCancelTurn?(): void
  onNotice?(msg: string): void
  /** 是否开唤醒词（关掉则只有「答完续问」，没有常开唤醒） */
  wakeWord?(): boolean
  config?: { followupWindowMs?: number; silenceTailMs?: number; endpointGraceMs?: number }
  // -- M4-5 S2S（不传/返回 classic 即完全走三段式原路径）--
  /** 红线：`s2s` 挡位会**上行原始音频**。默认必须是 classic，且只能由用户在设置里
   *  显式选择（CLAUDE.md §5「唯一的受控例外」条件①）。这里只读设置，不做默认值。 */
  getVoicePipeline?(): 'classic' | 's2s'
  getS2sConfig?(): { voice?: string; provider?: string; model?: string }
  getSessionMeta?(): { sessionId: string; userId?: string }
  /** S2S 自答轮的用户气泡（已过 FSM 本地治理的那句） */
  onS2sUserUtterance?(text: string): void
  /** S2S 自答的回答增量 */
  onS2sAnswerDelta?(text: string): void
  /** 逃逸：语音大模型把这句交回文本主链 -> 按既有 send 走（红线：S2S 会话内无执行通道） */
  onS2sEscalated?(utterance: string, turnId: string): void
  onS2sTurnEnd?(r: { turnId: string; reason: string; detail: string }): void
}

/** 免唤醒这条链在这个 APK 上到底可不可用。两个原生面**分开报**——
 *  「不可用」查不出是哪一半最耗时，M3-3 地图那次就是靠分开报一次命中的。 */
export function handsFreeAvailability(): { vad: boolean; kws: boolean; usable: boolean } {
  const vad = vadNativeAvailable()
  const kws = kwsNativeAvailable()
  // VAD 是必需的（没有端点判定就没有回路）；KWS 缺席只是没有唤醒词，续问仍成立
  return { vad, kws, usable: vad }
}

export class HandsFreeController {
  private vl: any
  private vad: VadEngine
  private kws: KwsEngine
  private mic: Recorder = micLease()
  private ring = new PcmRing(1500)
  private push = new PushRecorder()
  private asr: AsrSession | null = null
  private asrGen = 0
  /** S2S 会话（会话级挡位，enable 时定；null=classic）。降级期回落 classic 而不拆会话
   *  ——网关后台探活恢复后下一轮自动回 S2S（RFC §6.3 三种「离开 S2S」共用一条处理）。 */
  private s2s: any = null
  /** 已过 FSM 本地治理、待确定归属（自答/逃逸）的用户话 */
  private s2sPendingUser = ''
  private on = false
  private disposed = false
  /** 代际护栏：`enable()` 是不可中止的 async，其 await 间隙里的 `disable()` 必须让在途的
   *  enable 作废并回滚——否则诞生一个没人持有的孤儿控制器（HMI 侧 R4.3b P0 的原账 U1）。 */
  private epoch = 0
  private ttsSpeaking = false

  constructor(private deps: HandsFreeDeps) {
    const cfg = deps.config ?? {}
    this.vad = new VadEngine(cfg.silenceTailMs ?? 800)
    this.kws = new KwsEngine()
    this.vl = new VoiceLoop({
      config: {
        ...(cfg.followupWindowMs ? { followupWindowMs: cfg.followupWindowMs } : {}),
        ...(cfg.silenceTailMs ? { silenceTailMs: cfg.silenceTailMs } : {}),
        ...(cfg.endpointGraceMs !== undefined ? { endpointGraceMs: cfg.endpointGraceMs } : {}),
      },
      onState: (orb: string | null, fsm: string) => this.deps.onOrbState(orb, fsm),
      onOpenAsr: (o: { resume: boolean; sinceSpeechStartMs: number }) => this.openAsr(o),
      onCloseAsr: () => this.closeAsr(),
      onEndpoint: () => {
        // 无 server VAD 的引擎靠这一步请定稿。**S2S 下这一步同样不能省**：停推流后
        // provider 的 server VAD 永远等不到静音，turn 永不收束（HMI 真机首验的死锁）。
        if (this.s2s) this.s2s.commitAudio()
        else void this.asr?.stop()
      },
      onSend: (text: string, voice: { source: string; utteranceMs: number }) => {
        if (this.s2s) {
          // S2S 下 provider 已经在答了；这句只是「过了本地治理」，归属（自答/逃逸）
          // 要等下行帧才知道 => 先挂起，别现在就发给主链（发了就是双份）
          this.s2sPendingUser = text
          this.deps.onS2sUserUtterance?.(text)
          return
        }
        this.deps.onSend(text, voice)
      },
      onStopTts: () => {
        if (this.s2s) this.s2s.bargeIn()
        this.deps.onStopTts()
      },
      onCancelTurn: () => {
        if (this.s2s) this.s2s.cancelTurn()
        this.deps.onCancelTurn?.()
      },
      onDisableBargeIn: (reason: string) =>
        this.deps.onNotice?.('已关闭本次会话的语音打断（' + reason + '）'),
      onMetric: (name: string) => {
        // 本地消化的三类事件：provider 不知道我们把这句判掉了，要显式让它别答
        if (this.s2s && S2S_LOCAL_HANDLED.has(name)) this.s2s.cancelTurn()
      },
    })
  }

  get state(): string {
    return this.vl.state
  }

  get enabled(): boolean {
    return this.on
  }

  /** 开机：载模型 → 开麦 → 起 VAD（+ KWS，若开且在场）→ FSM 进 ARMED */
  async enable(): Promise<void> {
    if (this.disposed || this.on) return
    const myEpoch = ++this.epoch
    const alive = () => !this.disposed && this.epoch === myEpoch
    try {
      await this.vad.load()
      if (!alive()) return
      const wantKws = this.deps.wakeWord?.() !== false && kwsNativeAvailable()
      if (wantKws) {
        await this.kws.start({ onKeyword: () => this.onWake() }, DEFAULT_KEYWORDS)
        if (!alive()) {
          await this.kws.stop()
          return
        }
      }
      await this.vad.start({
        onSpeechStart: () => this.vl.vadSpeechStart(),
        onSpeechEnd: () => this.vl.vadSpeechEnd(),
        onError: (m: string) => this.deps.onNotice?.('语音检测异常：' + m),
      })
      this.vad.onWindow = (w) => {
        this.ring.push(w)
        this.s2s?.pushFrame(w) // LISTENING 门控在 s2sClient 内部（非 collecting 期静默丢）
      }
      if (this.deps.getVoicePipeline?.() === 's2s') this.startS2s()
      await this.mic.start((f) => this.onFrame(f))
      if (!alive()) {
        await this.teardown()
        return
      }
      this.on = true
      this.vl.handsFreeOn()
    } catch (e) {
      await this.teardown()
      throw e
    }
  }

  async disable(): Promise<void> {
    this.epoch++ // 作废在途的 enable
    if (!this.on) {
      await this.teardown()
      return
    }
    this.on = false
    this.vl.handsFreeOff()
    await this.teardown()
  }

  async dispose(): Promise<void> {
    this.disposed = true
    await this.disable()
    await this.vad.dispose()
  }

  // ─── 外部喂进来的状态（FSM 自己不可能知道的）───
  setNeedConfirm(v: boolean): void {
    this.vl.setNeedConfirm(v)
  }

  /** 播报文本随流式增量更新。**这条是回声防线的输入**——FSM 用它判「听到的是不是
   *  我刚说的那句」。2026-08-29 真机定位：此前只在 `ttsStart` 给一次，而那一刻文本
   *  还是空的 ⇒ `_overlapsTts` 恒 false，防线整条空转。 */
  setTtsText(text: string): void {
    this.vl.setTtsText(text)
  }

  /** 播报开始。text 供 FSM 判「助手念到了唤醒词」抑制自触发 */
  ttsStart(text: string): void {
    this.ttsSpeaking = true
    this.vl.setTtsText(text)
    this.vl.ttsStart()
  }

  ttsEnd(): void {
    this.ttsSpeaking = false
    this.vl.ttsEnd()
  }

  /** 本轮云端处理终结但没有播报（TTS 关 / 纯卡片 / 出错）——必须补调，
   *  否则 FSM 停在 THINKING 直到 100s 兜底，那段时间整个回路是聋的 */
  turnEnded(): void {
    if (this.ttsSpeaking) return
    this.vl.ttsEnd()
  }

  stats(): { fsm: string; ringFrames: number; kws: unknown } {
    return { fsm: this.vl.state, ringFrames: this.ring.frames, kws: this.kws.stats() }
  }

  // ─── 内部 ───
  private onFrame(frame: Int16Array): void {
    this.push.setDeviceRate(this.mic.deviceRate)
    this.vad.accept(frame) // 内部会把 512 窗回灌 ring（onWindow）
    this.kws.accept(frame)
    this.push.push(frame) // ASR 上行（未开 ASR 时 PushRecorder 无 sink，等于丢弃）
  }

  private onWake(): void {
    void this.kws.reset()
    this.vl.wake()
  }

  /** 建 S2S 会话。**会话级常驻**（唤醒后零建连延迟），收音门控靠 setCollecting。 */
  private startS2s(): void {
    const meta = this.deps.getSessionMeta?.() ?? { sessionId: this.deps.getSessionId() }
    const s2sCfg = this.deps.getS2sConfig?.() ?? {}
    const client = new S2SClient({
      playerFactory: (sampleRate: number) => newPcmPlayer({ sampleRate }),
      onTranscript: (text: string, final: boolean) => {
        if (!final) this.deps.onPartialText?.(text)
        else this.vl.asrFinal(stripLeadingWakeWord(text, WAKE_WORDS))
      },
      onAnswerDelta: (t: string) => this.deps.onS2sAnswerDelta?.(t),
      onFirstAudio: () => this.vl.ttsStart(),
      onTurnEnd: (r: { turnId: string; reason: string; detail: string }) => {
        this.s2sPendingUser = ''
        this.vl.ttsEnd()
        this.deps.onS2sTurnEnd?.(r)
      },
      onEscalated: (r: { turnId: string; utterance: string }) => {
        // 红线：S2S 会话内没有执行通道，逃逸就是把原话交回文本主链，
        // 此后 planner 校验 / 权限 / VAL / require_confirm 全量生效
        const utt = r.utterance || this.s2sPendingUser
        this.s2sPendingUser = ''
        if (utt) this.deps.onS2sEscalated?.(utt, r.turnId)
      },
      onSessionState: (st: string) => {
        if (st === 'degraded') this.deps.onNotice?.('语音链路降级，本轮回落三段式')
      },
      onUnsupported: (msg: string) => {
        this.deps.onNotice?.(msg + '（已回落三段式）')
        this.s2s = null // 之后 openAsr 会走 classic 分支
      },
    })
    this.s2s = client
    client.start(s2sUrl(this.deps.audioUrl), {
      session_id: meta.sessionId,
      ...(meta.userId ? { user_id: meta.userId } : {}),
      ...(s2sCfg.voice ? { voice: s2sCfg.voice } : {}),
      ...(s2sCfg.provider ? { provider: s2sCfg.provider } : {}),
      ...(s2sCfg.model ? { model: s2sCfg.model } : {}),
    })
  }

  private openAsr(opts: { resume: boolean; sinceSpeechStartMs: number }): void {
    if (this.s2s) {
      // S2S：不建 ASR，只开收音门控 + 注入 pre-roll
      this.s2s.setCollecting(true)
      if (opts.resume) {
        const ms = Math.min(RESUME_PRE_ROLL_MS + Math.max(0, opts.sinceSpeechStartMs || 0), MAX_PRE_ROLL_MS)
        const pre = this.ring.takeLast(ms)
        if (pre.length) this.s2s.pushPreRoll(pre)
      }
      return
    }
    this.closeAsr()
    const gen = ++this.asrGen
    const cfg = this.deps.getAsrConfig()
    const session = new AsrSession(
      { ...cfg, audioUrl: this.deps.audioUrl, sessionId: this.deps.getSessionId() },
      {
        onPartial: (t) => {
          if (gen !== this.asrGen) return // 陈旧会话的回调绝不打扰下一轮（HMI A1 原账）
          this.deps.onPartialText?.(t)
          this.vl.asrPartial(t)
        },
        onFinal: (t) => {
          if (gen !== this.asrGen) return
          // 唤醒词会被 ASR 一起听进去（pre-roll 已尽量不带，但真麦仍可能拖到尾音）
          this.vl.asrFinal(stripLeadingWakeWord(t, WAKE_WORDS))
        },
        onError: (m) => {
          if (gen !== this.asrGen) return
          this.deps.onNotice?.(m)
          this.vl.asrFinal('') // 空定稿 → FSM 按噪声句回收，不卡在 LISTENING
        },
      },
      this.push,
    )
    this.asr = session
    void session.start().catch((e) => {
      if (gen !== this.asrGen) return
      this.deps.onNotice?.(String(e?.message ?? e))
    })
    // pre-roll：唤醒进入不注入（resume=false），否则把唤醒词本身喂给 ASR
    if (opts.resume) {
      const ms = Math.min(RESUME_PRE_ROLL_MS + Math.max(0, opts.sinceSpeechStartMs || 0), MAX_PRE_ROLL_MS)
      const pre = this.ring.takeLast(ms)
      if (pre.length) this.pushPreRoll(pre)
    }
  }

  /** 把前滚的 Float32 切成 recorder 同形态的 Int16 帧推进 ASR */
  private pushPreRoll(pre: Float32Array): void {
    for (let off = 0; off < pre.length; off += FRAME_SAMPLES) {
      const slice = pre.subarray(off, Math.min(off + FRAME_SAMPLES, pre.length))
      const i16 = new Int16Array(slice.length)
      for (let i = 0; i < slice.length; i++) {
        const s = Math.max(-1, Math.min(1, slice[i]))
        i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
      this.push.push(i16)
    }
  }

  private closeAsr(): void {
    if (this.s2s) {
      this.s2s.setCollecting(false)
      return
    }
    const s = this.asr
    if (!s) return
    this.asrGen++
    this.asr = null
    void s.cancel()
  }

  /** 逃逸轮的主链回答回传给 provider（只为上下文连续，不为播报；RFC §3.2） */
  escalatedResult(turnId: string, text: string): void {
    this.s2s?.escalatedResult(turnId, text)
  }

  private async teardown(): Promise<void> {
    try {
      this.s2s?.close()
    } catch {
      /* ignore */
    }
    this.s2s = null
    this.s2sPendingUser = ''
    this.closeAsr()
    this.vad.onWindow = null
    this.vad.stop()
    this.ring.clear()
    await this.kws.stop()
    await this.mic.stop()
  }
}
