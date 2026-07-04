// R4.3 P1 免唤醒回路控制器：把引擎无关的 voiceLoop.mjs FSM 接到真实外设——
// VadEngine（VAD 端点事件）+ StreamingRecognizer（每 utterance 一条 /api/asr/stream）+ App 效果
// （send/stopTTS/orb/notice）。App 只管开关它并喂 needConfirm/tts 生命周期，不碰 FSM 内部。
import { VoiceLoop } from './voiceLoop.mjs'
import { VadEngine } from './vadEngine'
import { StreamingRecognizer, asrStreamUrl } from './audio'

export type HandsFreeDeps = {
  audioApi: string
  getAsrConfig: () => { language: string; provider: string; model: string }
  onSend: (text: string) => void
  onStopTts: () => void
  onOrbState: (orb: string | null) => void // null = FSM 回 IDLE，交还 mic 态
  onNotice?: (msg: string) => void
  config?: { followupWindowMs?: number; silenceTailMs?: number }
}

export class HandsFreeController {
  private vl: any // VoiceLoop（.mjs 无声明，as any 避免 TS7016 噪声）
  private vad: VadEngine
  private asr: StreamingRecognizer | null = null
  private deps: HandsFreeDeps
  private on = false

  constructor(deps: HandsFreeDeps) {
    this.deps = deps
    this.vad = new VadEngine(deps.config?.silenceTailMs ?? 800)
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

  /** 开 hands-free：预载模型（失败不启用）→ VAD 常开 → FSM 进 ARMED。返回是否成功。 */
  async enable(): Promise<boolean> {
    if (this.on) return true
    try {
      await this.vad.load()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      console.error('[hands-free] VAD 模型加载失败:', e)
      this.deps.onNotice?.('语音模型未就绪（' + msg + '）：先跑 scripts/fetch-voice-models 下载 silero 模型')
      return false
    }
    try {
      await this.vad.start({
        onSpeechStart: () => this.vl.vadSpeechStart(),
        onSpeechEnd: () => this.vl.vadSpeechEnd(),
        onError: (m) => this.deps.onNotice?.('VAD：' + m),
      })
    } catch (e) {
      this.deps.onNotice?.('无法开启麦克风：' + (e instanceof Error ? e.message : String(e)))
      return false
    }
    this.on = true
    this.vl.handsFreeOn()
    return true
  }

  disable(): void {
    if (!this.on) return
    this.on = false
    this.vl.handsFreeOff()
    this.vad.stop()
    this.closeAsr()
    this.deps.onOrbState(null)
  }

  // 点光球开启聆听（VAD-only 无唤醒词时的「一次点击开启」，设计备选链②）；有 KWS 后由唤醒词代劳
  wake(): void { if (this.on) this.vl.wake() }

  // ─── App 侧状态/生命周期喂给 FSM ───
  setNeedConfirm(v: boolean): void { if (this.on) this.vl.setNeedConfirm(v) }
  setTtsText(t: string): void { if (this.on) this.vl.setTtsText(t) }
  ttsStart(): void { if (this.on) this.vl.ttsStart() }
  ttsEnd(): void { if (this.on) this.vl.ttsEnd() }
  setSilenceTail(ms: number): void { this.vad.setSilenceTail(ms); this.vl.cfg.silenceTailMs = ms }
  setFollowupWindow(ms: number): void { this.vl.cfg.followupWindowMs = ms }

  // ─── 内部 ───
  private openAsr(): void {
    if (this.asr) return
    const cfg = this.deps.getAsrConfig()
    this.asr = new StreamingRecognizer()
    void this.asr
      .start(asrStreamUrl(this.deps.audioApi), {
        language: cfg.language,
        provider: cfg.provider,
        model: cfg.model,
        onPartial: (t) => this.vl.asrPartial(t),
        onFinal: (t) => this.vl.asrFinal(t),
        onError: (m) => { this.deps.onNotice?.('实时识别不可用：' + m); this.vl.asrFinal('') },
      })
      .catch((e) => { this.deps.onNotice?.('识别启动失败：' + e); this.vl.asrFinal('') })
  }

  private closeAsr(): void {
    try { this.asr?.stop() } catch { /* ignore */ }
    this.asr = null
  }

  // 唤醒音效：短促上扬 beep（WebAudio，无需资源文件），给用户听觉确认已进入聆听
  private chime(): void {
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
