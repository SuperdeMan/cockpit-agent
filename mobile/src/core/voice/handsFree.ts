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
import type { PcmPlayerLike } from './queuePlayer'
import { setAudioCaptureFact } from './captureFacts'
import { setAudioPlaybackFact } from './playbackFacts'
import { DEFAULT_KEYWORDS, KwsEngine, kwsNativeAvailable } from './kws'
import { noteKeywordHit } from './kwsExperiment'
import { PRODUCTION_PROFILE, type KwsProfile } from './kwsProfile'
import { micLease } from './micBus'
import { FRAME_SAMPLES, type FrameSink, type Recorder } from './recorder'
import { VadEngine, vadNativeAvailable } from './vad'

 

/** 续说/打断开 ASR 时注入的前滚（同 HMI 的 RESUME_PRE_ROLL_MS） */
const RESUME_PRE_ROLL_MS = 200
/** pre-roll 上限（PcmRing 容量 1500ms，同 HMI 的 MAX_PRE_ROLL_MS） */
const MAX_PRE_ROLL_MS = 1200
/** 唤醒词也会被 ASR 听见 → 定稿前剥掉（同 HMI；词表与 KWS 预设同源） */
const WAKE_WORDS = ['小舟小舟', '小舟']
/** FSM 判为「本地消化、不上云」的语义事件——S2S 下必须额外取消 provider 在飞的生成
 *  （它不知道我们把这句判成了噪声/退出）。名字与 voiceLoop.onMetric 的事件名一一对应。 */
const S2S_LOCAL_HANDLED = new Set(['exit_word', 'filler_dismissed', 'false_wake_dismissed', 'echo_dismissed'])

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
  /** 续问窗回声被 FSM 丢弃（voiceLoop echo_dismissed）→ UI 短显提示（方案 §5.2 规则 5） */
  onEchoDismissed?(): void
  onPartialText?(text: string): void
  /** THINKING 期被唤醒词打断 → 取消在飞的云端轮 */
  onCancelTurn?(): void
  onNotice?(msg: string): void
  /** 结构化降级信号（UX v2.1 §12.1）。与 onNotice 并行：notice 是人话，这两条是给 Presence 的事实 */
  onBargeInDisabled?(reason: string): void
  onPipelineDegraded?(kind: 'degraded' | 'unsupported', message: string): void
  /** 是否开唤醒词（关掉则只有「答完续问」，没有常开唤醒） */
  wakeWord?(): boolean
  config?: {
    followupWindowMs?: number
    silenceTailMs?: number
    endpointGraceMs?: number
    /** AR07 实验档。**不传就是生产默认**——实验取值只活在本次会话里，
     *  不写进用户设置、不改生产默认（否则下一次的「A 组」已经不是 A 了）。 */
    kwsProfile?: KwsProfile
  }
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
  private requested = false
  private lifecycle: Promise<void> = Promise.resolve()
  private ttsSpeaking = false
  /** 「本次收尾是用户按的停播」——只在 stopSpeaking() 的同步调用栈内为真 */
  private userStopping = false

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
        if (!this.on) return
        if (this.s2s) this.commitS2sAudio()
        else void this.asr?.stop()
      },
      onSend: (text: string, voice: { source: string; utteranceMs: number }) => {
        if (!this.on) return
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
      onDisableBargeIn: (reason: string) => {
        this.deps.onNotice?.('已关闭本次会话的语音打断（' + reason + '）')
        this.deps.onBargeInDisabled?.(reason)
      },
      onMetric: (name: string) => {
        // 本地消化的四类事件：provider 不知道我们把这句判掉了，要显式让它别答
        // （第 4 类 echo_dismissed 是第 3 批附加②：真机实录过一整轮「自己答自己的回声」）
        if (this.s2s && S2S_LOCAL_HANDLED.has(name)) this.s2s.cancelTurn()
        // 回声提示只有这一路信号（barge-in 那一路的 _countSelfTrigger 没有 metric——共享文件不改，记遗留）
        if (name === 'echo_dismissed') this.deps.onEchoDismissed?.()
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
    if (this.disposed) return
    if (this.requested) return this.lifecycle
    this.requested = true
    const myEpoch = ++this.epoch
    const pending = this.lifecycle.then(() => this.enableNow(myEpoch))
    this.lifecycle = pending.catch(() => {})
    return pending
  }

  private async enableNow(myEpoch: number): Promise<void> {
    const alive = () => !this.disposed && this.epoch === myEpoch
    if (!alive()) return
    try {
      await this.vad.load()
      if (!alive()) return
      const wantKws = this.deps.wakeWord?.() !== false && kwsNativeAvailable()
      if (wantKws) {
        await this.kws.start(
          { onKeyword: () => { if (alive()) this.onWake() } },
          DEFAULT_KEYWORDS,
          this.deps.config?.kwsProfile ?? PRODUCTION_PROFILE,
        )
        if (!alive()) {
          await this.kws.stop()
          return
        }
      }
      await this.vad.start({
        onSpeechStart: () => { if (alive() && this.on) this.vl.vadSpeechStart() },
        onSpeechEnd: () => { if (alive() && this.on) this.vl.vadSpeechEnd() },
        onError: (m: string) => { if (alive() && this.on) this.deps.onNotice?.('语音检测异常：' + m) },
      })
      if (!alive()) {
        await this.teardown()
        return
      }
      this.vad.onWindow = (w) => {
        if (!alive() || !this.on) return
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
      if (!alive()) return
      this.requested = false
      throw e
    }
  }

  async disable(): Promise<void> {
    this.epoch++ // 作废在途的 enable
    this.requested = false
    this.on = false
    this.vl.handsFreeOff()
    // 立即关闭传输/麦 lease，不把撤回排到模型加载或权限弹窗之后。
    const stopping = this.teardown()
    const pending = this.lifecycle.then(() => stopping)
    this.lifecycle = pending.catch(() => {})
    await pending
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
    // 用户停播那一次由 stopSpeaking() 自己把 FSM 收到 ARMED；这里短路，避免同一次停播
    // 先进 FOLLOWUP（开 8s 续问窗 + 起一只定时器）再被改写
    if (this.userStopping) return
    this.vl.ttsEnd()
  }

  /** 「只停播」（AR03 / 评审 R06）：停当前所有出声，**不发 cancel 帧、不开麦、不开续问窗**。
   *  与另外两条命令的分界：`wakeManually()` 是「停止后开始说话」，`onCancelTurn` 那条是「取消在飞请求」。 */
  stopSpeaking(): void {
    if (!this.on) return
    this.userStopping = true
    try {
      // S2S 自答：本地立刻停播 + 上行让网关 cancel provider（同 barge-in 的既有出口）
      this.s2s?.bargeIn()
      // 主链 TTS：与 barge-in 走同一个停播出口
      this.deps.onStopTts()
      this.vl.stopSpeaking()
    } finally {
      this.userStopping = false
    }
  }

  /** 本轮云端处理终结但没有播报（TTS 关 / 纯卡片 / 出错）——必须补调，
   *  否则 FSM 停在 THINKING 直到 100s 兜底，那段时间整个回路是聋的 */
  turnEnded(): void {
    if (this.ttsSpeaking || this.userStopping) return
    this.vl.ttsEnd()
  }

  /** 轻点光球（方案 §5.1.1）= 一次「手动唤醒」：FSM 的公开入口 wake()——ARMED/FOLLOWUP 进聆听、
   *  SPEAKING 先停播再听、THINKING 取消在飞轮再听。FSM 一字不改，KWS 与命中时同样 reset */
  wakeManually(): void {
    if (!this.on) return
    void this.kws.reset()
    this.vl.wake()
  }

  /** 录音中轻点 = 结束并提交：与 FSM 的 onEndpoint 效果逐字同构（S2S 请收尾 / classic 请定稿） */
  endUtterance(): void {
    if (!this.on || this.vl.state !== 'LISTENING') return
    if (this.s2s) this.commitS2sAudio()
    else void this.asr?.stop()
  }

  /** 「结束本轮收音」/「重新开启插话」的正式实现（评审 D7）：FSM 拆机再装机——
   *  handsFreeOff → IDLE（关 ASR、清定时器、**复位会话级 _bargeInDisabled**）→ handsFreeOn → ARMED。
   *  引擎、麦、KWS 都不动（不是 disable/enable），也不碰持久化设置（B1 那 50ms 的 false 窗口没了）。
   *  onBargeInDisabled('') = 「已复位」，Presence 据此撤掉 Dock 里那条降级 */
  recycle(): void {
    if (!this.on) return
    this.vl.handsFreeOff()
    this.vl.handsFreeOn()
    this.deps.onBargeInDisabled?.('')
  }

  stats(): { fsm: string; ringFrames: number; kws: unknown } {
    return { fsm: this.vl.state, ringFrames: this.ring.frames, kws: this.kws.stats() }
  }

  // ─── 内部 ───
  private onFrame(frame: Int16Array): void {
    if (!this.on || this.disposed) return
    this.push.setDeviceRate(this.mic.deviceRate)
    this.vad.accept(frame) // 内部会把 512 窗回灌 ring（onWindow）
    this.kws.accept(frame)
    this.push.push(frame) // ASR 上行（未开 ASR 时 PushRecorder 无 sink，等于丢弃）
  }

  private onWake(): void {
    if (!this.on) return
    // AR07 计数：记的是**原生命中**，不是「唤醒成功」——FSM 接不接得看下一步。
    // 没在实验会话里时这一句是空转（生产路径零开销、零常态日志）。
    noteKeywordHit()
    void this.kws.reset()
    this.vl.wake()
  }

  /** 建 S2S 会话。**会话级常驻**（唤醒后零建连延迟），收音门控靠 setCollecting。 */
  private startS2s(): void {
    const epoch = this.epoch
    const meta = this.deps.getSessionMeta?.() ?? { sessionId: this.deps.getSessionId() }
    const s2sCfg = this.deps.getS2sConfig?.() ?? {}
    const alive = () => this.epoch === epoch && !this.disposed && this.s2s === client
    const client = new S2SClient({
      // 注入传输适配器：真实 send 才报上行；撤回后屏蔽旧 WS 的消息与发送。
      // 共享 S2SClient 的模型/聚包/播放实现保持原样。
      wsFactory: (url: string) => this.s2sSocket(url, alive, client),
      playerFactory: (sampleRate: number) => this.playbackReporting(newPcmPlayer({ sampleRate })),
      onTranscript: (text: string, final: boolean) => {
        if (!alive() || !this.on) return
        if (final) setAudioCaptureFact('s2sUploading', client, false)
        if (!final) this.deps.onPartialText?.(text)
        else this.vl.asrFinal(stripLeadingWakeWord(text, WAKE_WORDS))
      },
      onAnswerDelta: (t: string) => { if (alive()) this.deps.onS2sAnswerDelta?.(t) },
      onFirstAudio: () => { if (alive()) this.vl.ttsStart() },
      onTurnEnd: (r: { turnId: string; reason: string; detail: string }) => {
        if (!alive()) return
        setAudioCaptureFact('s2sUploading', client, false)
        this.s2sPendingUser = ''
        this.vl.ttsEnd()
        this.deps.onS2sTurnEnd?.(r)
      },
      onEscalated: (r: { turnId: string; utterance: string }) => {
        if (!alive()) return
        // 红线：S2S 会话内没有执行通道，逃逸就是把原话交回文本主链，
        // 此后 planner 校验 / 权限 / VAL / require_confirm 全量生效
        const utt = r.utterance || this.s2sPendingUser
        this.s2sPendingUser = ''
        if (utt) this.deps.onS2sEscalated?.(utt, r.turnId)
      },
      onSessionState: (st: string) => {
        if (!alive()) return
        if (st === 'degraded') {
          setAudioCaptureFact('s2sUploading', client, false)
          this.deps.onNotice?.('语音链路降级，本轮回落三段式')
          this.deps.onPipelineDegraded?.('degraded', '语音链路降级，本轮回落三段式')
        }
      },
      onUnsupported: (msg: string) => {
        if (!alive()) return
        this.deps.onNotice?.(msg + '（已回落三段式）')
        this.deps.onPipelineDegraded?.('unsupported', msg)
        this.s2s = null // 之后 openAsr 会走 classic 分支
        client.close()
        setAudioCaptureFact('s2sUploading', client, false)
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
    if (!this.on) return
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
      setAudioCaptureFact('s2sUploading', this.s2s, false)
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
    const client = this.s2s
    this.s2s = null
    try {
      client?.close()
    } catch {
      /* ignore */
    }
    if (client) setAudioCaptureFact('s2sUploading', client, false)
    this.s2sPendingUser = ''
    this.closeAsr()
    this.vad.onWindow = null
    this.vad.stop()
    this.ring.clear()
    await Promise.all([this.mic.stop(), this.kws.stop()])
  }

  /** AR03 播放事实：S2S 自答走的是这一路播放器，**不经 `SpeechController`**——评审前它在整个播报事实面之外
   *  （`derivePresence` 只读 `speaking`），端到端挡位自答时屏上没有播报态、也就没有任何停止入口。
   *  起点取「首片真的推进去」（`started` 翻真），终点取 `stop()`：`S2SClient` 的每条收尾路径
   *  （turn end 等排定音频播完 / bargeIn / cancelTurn / close）都经 `_stopPlayback` ⇒ 一个包装盖全，
   *  不在六七个调用点各记一次（漏一处就是一个永远亮着的停止键）。 */
  private playbackReporting(player: PcmPlayerLike | null): PcmPlayerLike | null {
    // 无音频上下文时 S2SClient 的约定是「工厂给 null ⇒ 静默降级」；包装不许把它变成一个会抛的壳
    if (!player) return null
    // 建好播放器就算「这一路还可能出声」（S2S 的 audio_meta 先到、首片随后）
    setAudioPlaybackFact(player, true, 'live')
    return {
      get started() { return player.started },
      get nextStart() { return player.nextStart },
      get underruns() { return player.underruns },
      get ctx() { return player.ctx },
      push(int16: Int16Array) {
        const written = player.push(int16)
        if (player.started) setAudioPlaybackFact(player, true)
        return written
      },
      drainedAt: () => player.drainedAt(),
      remainingSec: () => player.remainingSec(),
      stop() {
        setAudioPlaybackFact(player, false)
        setAudioPlaybackFact(player, false, 'live')
        player.stop()
      },
    }
  }

  private commitS2sAudio(): void {
    this.s2s?.setCollecting(false)
    this.s2s?.commitAudio()
    if (this.s2s) setAudioCaptureFact('s2sUploading', this.s2s, false)
  }

  private s2sSocket(url: string, alive: () => boolean, owner: object): any {
    const socket = new WebSocket(url)
    let closed = false
    const proxy: any = {
      onopen: null, onmessage: null, onerror: null, onclose: null,
      get readyState() { return socket.readyState },
      get binaryType() { return socket.binaryType },
      set binaryType(value: BinaryType) { socket.binaryType = value },
      send(data: string | ArrayBuffer) {
        if (closed || !alive()) return
        socket.send(data)
        if (typeof data !== 'string') setAudioCaptureFact('s2sUploading', owner, true)
      },
      close() {
        closed = true
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null
        try { socket.close() } finally { setAudioCaptureFact('s2sUploading', owner, false) }
      },
    }
    socket.onopen = (e) => { if (!closed && alive()) proxy.onopen?.(e) }
    socket.onmessage = (e) => { if (!closed && alive()) proxy.onmessage?.(e) }
    socket.onerror = (e) => { if (!closed && alive()) proxy.onerror?.(e) }
    socket.onclose = (e) => {
      setAudioCaptureFact('s2sUploading', owner, false)
      if (!closed && alive()) proxy.onclose?.(e)
    }
    return proxy
  }
}
