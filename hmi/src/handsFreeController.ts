// R4.3 P1 免唤醒回路控制器：把引擎无关的 voiceLoop.mjs FSM 接到真实外设——
// VadEngine（VAD 端点事件）+ StreamingRecognizer（每 utterance 一条 /api/asr/stream）+ App 效果
// （send/stopTTS/orb/partial/notice）。App 只管开关它并喂 needConfirm/tts 生命周期，不碰 FSM 内部。
import { VoiceLoop } from './voiceLoop.mjs'
import { VadEngine } from './vadEngine'
import { KwsEngine, DEFAULT_KEYWORDS } from './kwsEngine'
import { StreamingRecognizer, asrStreamUrl, prepareCueSet, playCue, clearCues, isStreamingTtsProvider } from './audio'

// 流式引擎下唤醒/退场提示音的回落音色（MiMo 批处理，流式引擎音色 MiMo 无）
const CUE_FALLBACK_VOICE = '冰糖'
import { PcmRing } from './pcmRing.mjs'
import { stripLeadingWakeWord, isFiller } from './utteranceHeuristics.mjs'
import { bumpVoiceMetric } from './voiceMetrics.mjs'
import { RejectPolicy } from './rejectPolicy.mjs'

// R4.3b P4：续说（followup/barge-in/宽限续说）开 ASR 时注入的前滚缓冲——只补 VAD speech-start 判定
// 延迟（去抖 64ms + 几帧）的首字；刻意短，不带回声/上轮 TTS 尾。唤醒进入注入 0（不带唤醒词，见 openAsr）。
const RESUME_PRE_ROLL_MS = 200

// 唤醒提示语候选（issue①）：短促，唤醒时随机播一条求变化；回声靠 getUserMedia 的 AEC 兜底（同 barge-in 前提）。
const WAKE_CUE_TEXTS = ['在呢', '我在', '你说', '请讲', '我在听']
// 退场应答候选（R4.3b P1 U3）：说「退下吧」等退出词后短促回一句再闭麦回待机（TTS 关时静默）。
const EXIT_CUE_TEXTS = ['好的', '好嘞', '我先退下了']

export type HandsFreeDeps = {
  audioApi: string
  getAsrConfig: () => { language: string; provider: string; model: string }
  // R4.4：第二参带 hands-free 来源 → App 拼 meta.input_source 上云做拒识判定（旧调用方可略）。
  onSend: (text: string, voice?: { source: string; utteranceMs: number }) => void
  onStopTts: () => void
  onOrbState: (orb: string | null) => void // null = FSM 回 IDLE，交还 mic 态
  onPartialText?: (text: string) => void    // 聆听中的实时识别文字（issue②：hands-free 上屏）
  onCancelTurn?: () => void                  // U2/P2：THINKING 期唤醒词打断 → App 发 {type:cancel} 给网关
  onNotice?: (msg: string) => void
  wakeWord?: () => boolean                   // 是否开唤醒词（KWS）
  getWakeKeywords?: () => string             // 选定唤醒词的 KWS pinyin token 串
  getAssistantName?: () => string            // 助手名（D6：助手 TTS 念到它/唤醒词则抑制 KWS 自触发）
  getTts?: () => { enabled: boolean; voiceId: string; provider?: string } // 唤醒提示音是否合成 + 音色 + 引擎
  config?: { followupWindowMs?: number; silenceTailMs?: number; endpointGraceMs?: number }
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
  // R4.3b P2（U4 根治）：前滚缓冲——VAD 帧持续入环，开 ASR 时取最近 PRE_ROLL_MS 注入 PCM 直传流，
  // 补回 KWS 检测窗那段 MediaRecorder 采不到的音频。同一帧也喂当前 asr（PCM 模式）。
  private pcmRing = new PcmRing(1500)
  // R4.4 P2：连续云端拒识 → 聆听收紧策略（纯逻辑，见 rejectPolicy.mjs）。基准续问窗随
  // setFollowupWindow（用户设置）同步；tighten/wake_only 直改 vl.cfg 不动基准，restore 还原到基准。
  private rejectPolicy: RejectPolicy

  constructor(deps: HandsFreeDeps) {
    this.deps = deps
    this.vad = new VadEngine(deps.config?.silenceTailMs ?? 800)
    this.kws = new KwsEngine()
    this.vl = new VoiceLoop({
      config: {
        followupWindowMs: deps.config?.followupWindowMs ?? 8000,
        silenceTailMs: deps.config?.silenceTailMs ?? 800,
        endpointGraceMs: deps.config?.endpointGraceMs ?? 700, // U5b 端点宽限合并窗
      },
      onState: (orb: string) => this.deps.onOrbState(orb),
      onOpenAsr: (o: { resume?: boolean }) => this.openAsr(o),
      onCloseAsr: () => this.closeAsr(),
      onEndpoint: () => { try { this.asr?.stop() } catch { /* ignore */ } },
      onSend: (t: string, vm?: { source: string; utteranceMs: number }) => this.deps.onSend(t, vm),
      onStopTts: () => this.deps.onStopTts(),
      onWakeChime: () => this.chime(),
      onDisableBargeIn: (r: string) => this.deps.onNotice?.('已关闭语音打断（' + r + '）'),
      onExitAck: () => this.exitAck(), // U3：退出词命中 → 播退场应答
      onCancelTurn: () => this.deps.onCancelTurn?.(), // U2：THINKING 打断 → 透传 App 发网关取消
      onMetric: (name: string) => bumpVoiceMetric(name), // P3 obs：语音事件计数（localStorage，供真麦验收）
    })
    this.rejectPolicy = new RejectPolicy({ baseFollowupMs: this.vl.cfg.followupWindowMs })
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
    // P2：VAD 帧旁路——持续入前滚缓冲，且若 PCM 直传 ASR 已开则同帧喂入（保帧序）
    this.pcmRing.clear()
    this.vad.onFrame = (f) => { this.pcmRing.push(f); this.asr?.pushFrame(f) }
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
    this.vad.onFrame = null // 停 VAD 帧旁路（前滚缓冲 + PCM 直传）
    this.vad.stop()
    this.kws.stop()
    this.closeAsr()
    this.pcmRing.clear()
    this.sharedStream?.getTracks().forEach((t) => t.stop())
    this.sharedStream = null
    clearCues()
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

  // 语音提示音集：随音色/TTS 开关刷新（enable 时也调用一次）——唤醒 wake + 退场 exit 一并预合成。
  // 提示音走批处理 /api/tts（短固定语，无需流式）；流式引擎（cosyvoice/qwen）的音色 MiMo 批处理无，
  // 故回落 MiMo 音色合成（提示音是通用应答，音色一致性非关键），保证仍是真人声而非 beep。
  refreshWakeCue(): void {
    const tts = this.deps.getTts?.()
    if (this.on && tts?.enabled) {
      const cueVoice = isStreamingTtsProvider(tts.provider) ? CUE_FALLBACK_VOICE : tts.voiceId
      void prepareCueSet(this.deps.audioApi, cueVoice, { wake: WAKE_CUE_TEXTS, exit: EXIT_CUE_TEXTS })
        .catch(() => { /* wake 全失败 → 唤醒回退 beep；exit 无则静默退场 */ })
    } else {
      clearCues()
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
  // 用户设置（App effect）改续问窗 → 同步 FSM + 收紧策略基准（restore/tighten 以最新设置为准）。
  setFollowupWindow(ms: number): void { this.vl.cfg.followupWindowMs = ms; this.rejectPolicy.setBaseFollowupMs(ms) }

  // R4.4 P2：连续云端拒识自适应收紧（≥2 减半续问窗 → ≥3 仅唤醒词），一次成功交互即复位。
  // 直改 vl.cfg（不走 setFollowupWindow，避免污染策略基准）；仅唤醒词模式额外关 VAD barge-in。
  notifyRejected(): void {
    const a = this.rejectPolicy.onRejected()
    if (!a) return
    if (a.type === 'tighten') {
      this.vl.cfg.followupWindowMs = a.followupMs
      this.deps.onNotice?.('周围有点吵，我收紧了聆听窗口')
    } else if (a.type === 'wake_only') {
      this.vl.cfg.followupWindowMs = 0
      this.vl.setVadBargeInDisabled(true)
      const ww = (this.deps.getWakeKeywords?.() ?? DEFAULT_KEYWORDS).split('@')[1] || '小舟小舟'
      this.deps.onNotice?.(`环境较嘈杂，说「${ww}」再叫我`)
      bumpVoiceMetric('reject_downgrade')
    }
  }

  notifyAccepted(): void {
    const a = this.rejectPolicy.onAccepted()
    if (!a) return                         // 无连续拒识 → 无需复位
    this.vl.cfg.followupWindowMs = a.followupMs
    this.vl.setVadBargeInDisabled(false)
    bumpVoiceMetric('reject_recovered')
  }

  // 剥离前滚缓冲带入的唤醒词残留（「小舟小舟…」）——只用完整唤醒词 + 助手名，不用单字（避免「小明」被剥成「明」）。
  private wakeStripWords(): string[] {
    const display = (this.deps.getWakeKeywords?.() ?? DEFAULT_KEYWORDS).split('@')[1] || ''
    const name = this.deps.getAssistantName?.() || ''
    return [...new Set([display, name].filter(Boolean))]
  }

  // ─── 内部 ───
  // P2 PCM 直传（U4 根治）：不用 MediaRecorder，用 vadEngine.onFrame 喂帧 + 前滚缓冲；partial/final 剥唤醒词残留。
  // P4 真麦修复：仅续说路径（resume=true：续问/打断/宽限续说，无唤醒词）注入短 pre-roll 补 VAD 判定延迟首字；
  // KWS 唤醒进入（resume=false）不注入——KWS 命中点往回取恰是唤醒词本身，会被识别成同音字残留上屏（「小周」）。
  private openAsr(opts: { resume?: boolean } = {}): void {
    if (this.asr) return
    const cfg = this.deps.getAsrConfig()
    const gen = ++this.asrGen // 本条会话代号；下方回调只在代号仍为当前时才作数
    const fresh = () => gen === this.asrGen // 会话未被 closeAsr 取代
    const words = this.wakeStripWords()
    const strip = (t: string) => stripLeadingWakeWord(t, words)
    this.asr = new StreamingRecognizer()
    // 唤醒进入 pre-roll=0（唤醒词不进 ASR）；续说进入取 RESUME_PRE_ROLL_MS 补首字（无唤醒词，安全）
    const preRoll = opts.resume ? this.pcmRing.takeLast(RESUME_PRE_ROLL_MS) : new Float32Array(0)
    void this.asr
      .startPcm(asrStreamUrl(this.deps.audioApi), {
        language: cfg.language,
        provider: cfg.provider,
        model: cfg.model,
        vadSilenceMs: this.vl.cfg.silenceTailMs, // B2：客户端静音尾透传 qwen3 server_vad
        // partial 剥唤醒词残留后喂 FSM（端点/回声判据）；上屏跳过纯语气词（P4：「嗯」不闪 ghost 气泡）
        onPartial: (t) => { if (!fresh()) return; const s = strip(t); this.vl.asrPartial(s); if (!isFiller(s)) this.deps.onPartialText?.(s) },
        onFinal: (t) => { if (!fresh()) return; this.vl.asrFinal(strip(t)) },
        // A1：陈旧会话的兜底超时/断流 onError 到达时代号已变 → 丢弃，绝不用空 final 打回下一轮
        onError: (m) => { if (!fresh()) return; this.deps.onNotice?.('实时识别不可用：' + m); this.vl.asrFinal('') },
      }, preRoll)
      .catch((e) => { if (!fresh()) return; this.deps.onNotice?.('识别启动失败：' + e); this.vl.asrFinal('') })
  }

  private closeAsr(): void {
    this.asrGen++ // 使旧会话的一切后续回调作废（A1）
    try { this.asr?.stop() } catch { /* ignore */ }
    this.asr = null
  }

  // U3 退场应答：命中退出/dismiss 词 → 播一句「好的」再回待机；TTS 关（无 exit cue）时静默，
  // 退场刻意不 beep（退下不该再发声）——playCue 未就绪返回 false 即静默。
  private exitAck(): void {
    playCue('exit')
  }

  // 唤醒音效：优先播预合成的人声提示（issue①）；未就绪回退短促上扬 beep（WebAudio，无需资源文件）。
  // R4.3b P4 真麦修复：删掉「inSpeech 则跳过」——唤醒词刚说完的瞬间 VAD 几乎必然还在 speech 段
  // （静音尾未到），会导致提示音几乎总被跳过；唤醒必播是泓舟 P3-UX 验收过的正确行为，回声靠 AEC 兜。
  private chime(): void {
    if (playCue('wake')) return
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
