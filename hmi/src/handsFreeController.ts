// R4.3 P1 免唤醒回路控制器：把引擎无关的 voiceLoop.mjs FSM 接到真实外设——
// VadEngine（VAD 端点事件）+ StreamingRecognizer（每 utterance 一条 /api/asr/stream）+ App 效果
// （send/stopTTS/orb/partial/notice）。App 只管开关它并喂 needConfirm/tts 生命周期，不碰 FSM 内部。
import { VoiceLoop } from './voiceLoop.mjs'
import { VadEngine } from './vadEngine'
import { KwsEngine, DEFAULT_KEYWORDS } from './kwsEngine'
import { StreamingRecognizer, asrStreamUrl, prepareWakeCue, playWakeCue, clearWakeCue } from './audio'

// 唤醒提示语候选（issue①）：短促，唤醒时随机播一条求变化；回声靠 getUserMedia 的 AEC 兜底（同 barge-in 前提）。
const WAKE_CUE_TEXTS = ['在呢', '我在', '你说', '请讲', '我在听']

export type HandsFreeDeps = {
  audioApi: string
  getAsrConfig: () => { language: string; provider: string; model: string }
  onSend: (text: string) => void
  onStopTts: () => void
  onOrbState: (orb: string | null) => void // null = FSM 回 IDLE，交还 mic 态
  onPartialText?: (text: string) => void    // 聆听中的实时识别文字（issue②：hands-free 上屏）
  onNotice?: (msg: string) => void
  wakeWord?: () => boolean                   // 是否开唤醒词（KWS）
  getWakeKeywords?: () => string             // 选定唤醒词的 KWS pinyin token 串
  getAssistantName?: () => string            // 助手名（D6：助手 TTS 念到它/唤醒词则抑制 KWS 自触发）
  getTts?: () => { enabled: boolean; voiceId: string } // 唤醒提示音是否合成 + 用哪个音色
  config?: { followupWindowMs?: number; silenceTailMs?: number }
}

export class HandsFreeController {
  private vl: any // VoiceLoop（.mjs 无声明，as any 避免 TS7016 噪声）
  private vad: VadEngine
  private kws: KwsEngine
  private asr: StreamingRecognizer | null = null
  private deps: HandsFreeDeps
  private on = false
  private ttsSpeaking = false // 助手是否正在播报（D6：据此抑制 KWS 自触发）
  private ttsText = ''        // 当前播报文本（D6：含唤醒词/助手名时抑制 KWS）
  private sharedStream: MediaStream | null = null // 架构债 A：VAD/KWS/ASR 共用的单路 mic 流
  // R4.3b P0（U1 孤儿活控制器）：enable() 是不可中止的 async，其 await 间隙里发生的 disable()
  // 旧实现因 `if(!on)return` 直接失效 → in-flight enable 照样置 on/建 mic/VAD/KWS，诞生孤儿。
  // 代际护栏：enable 入口快照 epoch，每个 await 后校验；disable/dispose 先自增 epoch 使在途 enable 作废并回滚。
  private epoch = 0
  private disposed = false // dispose() 后永不再 enable（App 卸载用）
  // R4.3b P0（A1 陈旧 ASR 回调劫杀下一轮）：每条 ASR 会话一个代号，closeAsr 自增。
  // 陈旧会话（被静默回收/超时兜底）的 onPartial/onFinal/onError 代号不符即丢弃，绝不打扰下一轮 FSM。
  private asrGen = 0

  constructor(deps: HandsFreeDeps) {
    this.deps = deps
    this.vad = new VadEngine(deps.config?.silenceTailMs ?? 800)
    this.kws = new KwsEngine()
    this.vl = new VoiceLoop({
      config: {
        followupWindowMs: deps.config?.followupWindowMs ?? 8000,
        silenceTailMs: deps.config?.silenceTailMs ?? 800,
      },
      onState: (orb: string) => this.deps.onOrbState(orb),
      onOpenAsr: () => this.openAsr(),
      onCloseAsr: () => this.closeAsr(),
      onEndpoint: () => { try { this.asr?.stop() } catch { /* ignore */ } },
      onSend: (t: string) => this.deps.onSend(t),
      onStopTts: () => this.deps.onStopTts(),
      onWakeChime: () => this.chime(),
      onDisableBargeIn: (r: string) => this.deps.onNotice?.('已关闭语音打断（' + r + '）'),
    })
  }

  get enabled(): boolean {
    return this.on
  }

  /** 开 hands-free：预载模型（失败不启用）→ VAD 常开 → FSM 进 ARMED。返回是否成功。
   *  代际护栏：入口快照 epoch，每个 await 之后校验；期间被 disable/dispose（epoch 变化）即回滚已获资源并 false。 */
  async enable(): Promise<boolean> {
    if (this.disposed) return false
    if (this.on) return true
    const ep = ++this.epoch
    try {
      await this.vad.load()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      console.error('[hands-free] VAD 模型加载失败:', e)
      this.deps.onNotice?.('语音模型未就绪（' + msg + '）：先跑 scripts/fetch-voice-models 下载 silero 模型')
      return false
    }
    if (ep !== this.epoch) return false // 被取代（VAD 模型已缓存，无外部资源需回滚）
    // 架构债 A：单路 mic——一次 getUserMedia，VAD/KWS/ASR 共用，避免三路各占一条麦、AEC/AGC 互相打架。
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
    } catch (e) {
      this.deps.onNotice?.('无法开启麦克风：' + (e instanceof Error ? e.message : String(e)))
      return false
    }
    if (ep !== this.epoch) { stream.getTracks().forEach((t) => t.stop()); return false } // 回滚已获 mic
    this.sharedStream = stream
    try {
      await this.vad.start({
        onSpeechStart: () => this.vl.vadSpeechStart(),
        onSpeechEnd: () => this.vl.vadSpeechEnd(),
        onError: (m) => this.deps.onNotice?.('VAD：' + m),
      }, stream)
    } catch (e) {
      this.deps.onNotice?.('VAD 启动失败：' + (e instanceof Error ? e.message : String(e)))
      stream.getTracks().forEach((t) => t.stop())
      if (this.sharedStream === stream) this.sharedStream = null // 仅回收本次独占的流，不误伤已接管的更新 enable
      return false
    }
    if (ep !== this.epoch) { // 被取代：回滚本次的 VAD + mic，绝不诞生孤儿
      stream.getTracks().forEach((t) => t.stop())
      if (this.sharedStream === stream) { this.vad.stop(); this.sharedStream = null } // 已被更新 enable 接管则不动
      return false
    }
    this.on = true
    this.vl.handsFreeOn()
    this.refreshWakeCue() // 唤醒提示音预合成（best-effort，失败自动回退 beep）
    if (this.deps.wakeWord?.()) this.startKws()
    return true
  }

  disable(): void {
    this.epoch++ // 先自增：使任何在途 enable 的下一个代际检查失败、自行回滚（U1 根治）
    if (!this.on) return
    this.on = false
    this.vl.handsFreeOff()
    this.vad.stop()
    this.kws.stop()
    this.closeAsr()
    this.sharedStream?.getTracks().forEach((t) => t.stop())
    this.sharedStream = null
    clearWakeCue()
    this.deps.onOrbState(null)
  }

  /** App 卸载调用：等价 disable + 永久封禁再 enable（防 StrictMode remount 复活旧实例）。 */
  dispose(): void {
    this.disposed = true
    this.disable()
  }

  // 唤醒词开关（设置变化时 App 调用）：开则起 KWS 常开听唤醒词，命中 → FSM wake()
  setWakeWord(on: boolean): void {
    if (!this.on) return
    if (on && !this.kws.active) this.startKws()
    else if (!on && this.kws.active) this.kws.stop()
  }

  // 换唤醒词（设置里选了别的词）：运行中且 KWS 开着 → 停后按新关键词重建常开
  updateWakeKeywords(): void {
    if (!this.on || !this.kws.active) return
    this.kws.stop()
    this.startKws()
  }

  // 唤醒提示音：随音色/TTS 开关刷新（enable 时也调用一次）
  refreshWakeCue(): void {
    const tts = this.deps.getTts?.()
    if (this.on && tts?.enabled) {
      void prepareWakeCue(this.deps.audioApi, tts.voiceId, WAKE_CUE_TEXTS).catch(() => { /* 回退 beep */ })
    } else {
      clearWakeCue()
    }
  }

  private startKws(): void {
    this.kws.setKeywords(this.deps.getWakeKeywords?.() ?? DEFAULT_KEYWORDS)
    void this.kws
      .start(() => { if (!this.kwsSuppressed()) this.vl.wake() }, this.sharedStream ?? undefined)
      .catch((e) => this.deps.onNotice?.('唤醒词未就绪（' + (e instanceof Error ? e.message : String(e)) + '）：跑 scripts/build-kws-wasm.sh 生成 KWS 运行时'))
  }

  // D6：助手正在播报且文本含唤醒词/助手名 → 抑制 KWS（防念到自己名字自触发）；VAD 打断不受影响。
  private kwsSuppressed(): boolean {
    if (!this.ttsSpeaking || !this.ttsText) return false
    const name = this.deps.getAssistantName?.() || ''
    const display = (this.deps.getWakeKeywords?.() ?? DEFAULT_KEYWORDS).split('@')[1] || ''
    return (!!name && this.ttsText.includes(name)) || (!!display && this.ttsText.includes(display))
  }

  // 点光球开启聆听（VAD-only 无唤醒词时的「一次点击开启」，设计备选链②）；有 KWS 后由唤醒词代劳
  wake(): void { if (this.on) this.vl.wake() }

  // ─── App 侧状态/生命周期喂给 FSM ───
  setNeedConfirm(v: boolean): void { if (this.on) this.vl.setNeedConfirm(v) }
  setTtsText(t: string): void { this.ttsText = t || ''; if (this.on) this.vl.setTtsText(t) }
  ttsStart(): void { this.ttsSpeaking = true; if (this.on) this.vl.ttsStart() }
  ttsEnd(): void { this.ttsSpeaking = false; if (this.on) this.vl.ttsEnd() }
  // R4.3b P0（U2 死锁）：本轮终结（无播报/纯卡片/error/超时）时 App 调此放 FSM 出 THINKING。
  // 命名表意（= ttsEnd 语义），App 侧读起来即「这一轮结束了」，不必知道内部靠 TTS 生命周期驱动。
  turnEnded(): void { this.ttsSpeaking = false; if (this.on) this.vl.ttsEnd() }
  setSilenceTail(ms: number): void { this.vad.setSilenceTail(ms); this.vl.cfg.silenceTailMs = ms }
  setFollowupWindow(ms: number): void { this.vl.cfg.followupWindowMs = ms }

  // ─── 内部 ───
  private openAsr(): void {
    if (this.asr) return
    const cfg = this.deps.getAsrConfig()
    const gen = ++this.asrGen // 本条会话代号；下方回调只在代号仍为当前时才作数
    const fresh = () => gen === this.asrGen // 会话未被 closeAsr 取代
    this.asr = new StreamingRecognizer()
    void this.asr
      .start(asrStreamUrl(this.deps.audioApi), {
        language: cfg.language,
        provider: cfg.provider,
        model: cfg.model,
        // partial 既喂 FSM（端点/回声判据）又上屏（issue②：hands-free 也边说边出字）
        onPartial: (t) => { if (!fresh()) return; this.vl.asrPartial(t); this.deps.onPartialText?.(t) },
        onFinal: (t) => { if (!fresh()) return; this.vl.asrFinal(t) },
        // A1：陈旧会话的兜底超时/断流 onError 到达时代号已变 → 丢弃，绝不用空 final 打回下一轮
        onError: (m) => { if (!fresh()) return; this.deps.onNotice?.('实时识别不可用：' + m); this.vl.asrFinal('') },
      }, this.sharedStream ?? undefined)
      .catch((e) => { if (!fresh()) return; this.deps.onNotice?.('识别启动失败：' + e); this.vl.asrFinal('') })
  }

  private closeAsr(): void {
    this.asrGen++ // 使旧会话的一切后续回调作废（A1）
    try { this.asr?.stop() } catch { /* ignore */ }
    this.asr = null
  }

  // 唤醒音效：优先播预合成的人声提示（issue①）；未就绪回退短促上扬 beep（WebAudio，无需资源文件）
  private chime(): void {
    if (playWakeCue()) return
    try {
      const AC = window.AudioContext || (window as any).webkitAudioContext
      const ctx = new AC()
      const o = ctx.createOscillator()
      const g = ctx.createGain()
      o.type = 'sine'
      o.frequency.setValueAtTime(660, ctx.currentTime)
      o.frequency.exponentialRampToValueAtTime(990, ctx.currentTime + 0.12)
      g.gain.setValueAtTime(0.0001, ctx.currentTime)
      g.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18)
      o.connect(g); g.connect(ctx.destination)
      o.start(); o.stop(ctx.currentTime + 0.2)
      o.onended = () => void ctx.close()
    } catch { /* 静默 */ }
  }
}
