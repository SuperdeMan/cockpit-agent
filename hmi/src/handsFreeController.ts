// R4.3 P1 免唤醒回路控制器：把引擎无关的 voiceLoop.mjs FSM 接到真实外设——
// VadEngine（VAD 端点事件）+ StreamingRecognizer（每 utterance 一条 /api/asr/stream）+ App 效果
// （send/stopTTS/orb/partial/notice）。App 只管开关它并喂 needConfirm/tts 生命周期，不碰 FSM 内部。
import { VoiceLoop } from './voiceLoop.mjs'
import { VadEngine } from './vadEngine'
import { KwsEngine, DEFAULT_KEYWORDS } from './kwsEngine'
import { StreamingRecognizer, asrStreamUrl, prepareCueSet, playCue, clearCues, makeS2sPlayer } from './audio'
import { PcmRing } from './pcmRing.mjs'
import { VoiceprintIdentifier, postIdentify } from './voiceprintIdentifier.mjs'
import { stripLeadingWakeWord, isFiller } from './utteranceHeuristics.mjs'
import { bumpVoiceMetric } from './voiceMetrics.mjs'
import { RejectPolicy } from './rejectPolicy.mjs'
import { S2SClient, s2sUrl } from './s2sClient.mjs'

// R4.3b P4：续说（followup/宽限续说）开 ASR 时注入的前滚缓冲——只补 VAD speech-start 判定
// 延迟（去抖 64ms + 几帧）的首字；刻意短，不带回声/上轮 TTS 尾。唤醒进入注入 0（不带唤醒词，见 openAsr）。
const RESUME_PRE_ROLL_MS = 200
// barge-in 动态 pre-roll 上限：打断确认窗（bargeInMinMs 300ms）+VAD 判定延迟期间 ASR 未开，
// FSM 会带来 sinceSpeechStartMs，pre-roll = 它 + RESUME_PRE_ROLL_MS，按此上限截断（pcmRing 容量 1500ms）。
const MAX_PRE_ROLL_MS = 1200

// 唤醒提示语候选（issue①）：短促，唤醒时随机播一条求变化；回声靠 getUserMedia 的 AEC 兜底（同 barge-in 前提）。
const WAKE_CUE_TEXTS = ['在呢', '我在', '你说', '请讲', '我在听']
// 退场应答候选（R4.3b P1 U3）：说「退下吧」等退出词后短促回一句再闭麦回待机（TTS 关时静默）。
const EXIT_CUE_TEXTS = ['好的', '好嘞', '我先退下了']

// M4：FSM 判为「本地消化、不上云」的语义事件——S2S 下须额外取消 provider 在飞的生成
// （它不知道我们把这句判成了噪声/退出）。名字与 voiceLoop.onMetric 的事件名一一对应。
const S2S_LOCAL_HANDLED = new Set(['exit_word', 'filler_dismissed', 'false_wake_dismissed'])

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
  // ── M4 S2S（默认 classic，不传即完全走原路径）──
  getS2sConfig?: () => { pipeline: 'classic' | 's2s'; voice?: string; provider?: string; model?: string }
  getSessionMeta?: () => { sessionId: string; userId?: string; vehicleId?: string }
  onS2sUserUtterance?: (text: string) => void // S2S 自答轮的用户气泡（过了 FSM 本地治理的）
  onS2sAnswerDelta?: (text: string) => void   // S2S 自答的回答增量（气泡/字幕）
  onS2sEscalated?: (utterance: string, turnId: string) => void // 逃逸 → App 按既有 send 走
  onS2sTurnEnd?: (r: { turnId: string; reason: string; detail: string }) => void
  // ── M4 P4 声纹（不传即完全不识别，occupantId 恒 primary）──
  getVoiceprintConfig?: () => { enabled: boolean }
  onVoiceprintResult?: (r: { decision?: string; display_name?: string }) => void
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
  // M4：S2S 会话（会话级挡位，enable 时定；null=classic）。DEGRADED 期自动回落 classic
  // 而不拆会话——网关后台探活恢复后下一轮自动回 S2S（RFC §6.3 三种「离开 S2S」共用一条处理）。
  private s2s: any = null
  private s2sPendingUser = '' // 已过 FSM 本地治理、待确定归属（自答/逃逸）的用户话
  private needConfirm = false // 主链挂起确认镜像（S2S 分支 onSend 判「必须上主链」用）
  // M4 P4 声纹：唤醒后首句边说边识别，结果锁定整个唤醒窗（null=该面禁用/未开）。
  private vp: VoiceprintIdentifier | null = null
  // VAD 当前是否在语音段中——声纹探针只收这段里的帧（arm 时也要带上它，见 openAsr）。
  private vadInSpeech = false

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
      onState: (orb: string, fsm: string) => {
        // M4 P4：回 ARMED/IDLE = 本次唤醒窗结束 → 解锁说话人，下次唤醒重识。
        // 用 onState 既有的第二参（fsmState），**不给 voiceLoop 加回调**（同 S2S 期的纪律）。
        // S2S 侧同步归位 primary：不归位的话上一个人会残留到下一唤醒窗，下一位乘员
        // 识别结果回来之前收束的自答轮会被记进上一个人的记忆。
        if (fsm === 'ARMED' || fsm === 'IDLE') {
          if (this.vp) { this.vp.reset(); this.s2s?.setOccupant('primary', '') }
        }
        this.deps.onOrbState(orb)
      },
      onOpenAsr: (o: { resume?: boolean; sinceSpeechStartMs?: number }) => this.openAsr(o),
      onCloseAsr: () => this.closeAsr(),
      // 端点判定权在**本侧 VAD**（S2S 与 classic 同构）：classic 请引擎定稿，S2S 提交音频段
      // 让 provider 收尾。**S2S 下绝不能空操作**——provider 的 server VAD 靠连续静音判「说完
      // 了」，而我们在端点后就停推流，它永远等不到静音，turn 永不收束＝用户永远没有回复
      // （真机首验的死锁：provider 等静音、HMI 等定稿才进 THINKING 才关收音）。
      onEndpoint: () => {
        // M4 P4：说完了还没攒够 1.5s 有效语音 → 用已有的这段发一次（短问句「你知道我是谁」
        // 约 1.2s，不补发就永远不识别，症状与认错人一模一样）。够不够格由网关判。
        this.vp?.flush()
        if (this.useS2s()) { this.s2s.commitAudio(); return }
        try { this.asr?.stop() } catch { /* ignore */ }
      },
      // S2S：本轮 provider 已在生成回答，不再走文本主链——FSM 照常进 THINKING（等首音频）。
      // 用户气泡**不在此刻上屏**：本轮还不知道是自答还是逃逸，逃逸轮的用户气泡由 send() 自己插，
      // 这里插就成双份。暂存，等 answer_delta 首包（=确定自答）再 flush。实测两者互斥：
      // 逃逸轮零文本零音频、自答轮无 tool_call（RFC §3.5）。
      // **必须同步调用 deps.onSend**：`_finalizeSend` 是「先 onSend 再进 THINKING」，而
      // App 的 onOrbState 在离开 LISTENING 时清 partial ghost，靠的正是「真实用户气泡已由
      // send 同步接管」这个不变量。一旦这里改成异步（我曾为等声纹结果加过 150ms 软等待），
      // 气泡插入就落到状态迁移之后，与并发的另一次 send 交错时会出现气泡与回答错位。
      // 声纹也根本不需要这个等待：识别在用户说到 1.5s 时就发出，而端点还要再等一个静音尾
      // （默认 800ms），结果早就回来了——**为一个几乎不生效的优化牺牲一条不变量是亏的**。
      onSend: (t: string, vm?: { source: string; utteranceMs: number }) => {
        // D5-2 红线在 S2S 下的兑现：**确认条可见时一切定稿必须上主链**。S2S 模型没有
        // 任何机制感知主链的挂起确认——「确认/取消」留给它只会被当闲聊自答（「好的已
        // 确认」而后备箱没开；「已为您取消」而挂起还活着），是假承诺。此时取消 provider
        // 在飞的生成（同本地治理 bargeIn 语义），这句话由确定性主链接管。
        if (this.useS2s() && !this.needConfirm) { this.s2sPendingUser = t; return }
        if (this.useS2s()) this.s2s.bargeIn()
        this.deps.onSend(t, vm)
      },
      onStopTts: () => { this.s2s?.bargeIn(); this.deps.onStopTts() },
      onWakeChime: () => this.chime(),
      onDisableBargeIn: (r: string) => this.deps.onNotice?.('已关闭语音打断（' + r + '）'),
      onExitAck: () => this.exitAck(), // U3：退出词命中 → 播退场应答
      onCancelTurn: () => { this.s2s?.cancelTurn(); this.deps.onCancelTurn?.() }, // U2：THINKING 打断
      onMetric: (name: string) => {
        // S2S 下 FSM 的「本地消化」判定（退出词/语气词/短语音/误唤醒）必须取消 provider 在飞的
        // 生成——不然一句「嗯」会被 S2S 当一轮答出来，R4.3b 辛苦做的本地治理在 S2S 下全失效。
        // onMetric 就是 FSM 的语义事件总线（RFC §4.1「一组新效果回调注入」），voiceLoop 零改动。
        if (this.useS2s() && S2S_LOCAL_HANDLED.has(name)) this.s2s.bargeIn()
        bumpVoiceMetric(name)
      }, // P3 obs：语音事件计数（localStorage，供真麦验收）
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
        // M4 P4：语音段状态同步给声纹识别器——`onFrame` 是**原始帧旁路不做门控**，
        // 识别器若按墙钟累计，唤醒后那一秒的提示音+静音会把探针稀释到谁都认不出。
        onSpeechStart: () => { this.vadInSpeech = true; this.vp?.setSpeaking(true); this.vl.vadSpeechStart() },
        onSpeechEnd: () => { this.vadInSpeech = false; this.vp?.setSpeaking(false); this.vl.vadSpeechEnd() },
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
    // P2：VAD 帧旁路——持续入前滚缓冲，且若 PCM 直传 ASR 已开则同帧喂入（保帧序）。
    // M4：S2S 挡位下同一帧喂 S2S 会话（其内部按 LISTENING 门控决定是否上行）。
    this.pcmRing.clear()
    // M4 P4：同一帧再旁路一份给声纹识别器（它自己按 arm 门控决定收不收——未唤醒不采集）。
    this.vad.onFrame = (f) => {
      this.pcmRing.push(f); this.asr?.pushFrame(f); this.s2s?.pushFrame(f); this.vp?.pushFrame(f)
    }
    this.on = true
    this.openVoiceprintIfEnabled()
    this.openS2sIfSelected()
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
    this.vp?.reset()
    this.vp = null
    this.closeS2s()
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

  // 语音提示音集：随引擎/音色/TTS 开关刷新（enable 时也调用一次）——唤醒 wake + 退场 exit 一并预合成。
  // 用当前选定引擎+音色合成（prepareCueSet 内流式引擎走 /api/tts/stream 收全 PCM），
  // 唤醒应答与正文同声同色；引擎不可用时才回落 MiMo 批处理，保证仍是真人声而非 beep。
  refreshWakeCue(): void {
    const tts = this.deps.getTts?.()
    if (this.on && tts?.enabled) {
      void prepareCueSet(this.deps.audioApi, tts.voiceId,
                         { wake: WAKE_CUE_TEXTS, exit: EXIT_CUE_TEXTS }, tts.provider || '')
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
  // needConfirm 本层也留一份：S2S 分支的 onSend 要靠它判「这句必须上主链」（只喂 vl
  // 的话 S2S 旁路看不见挂起确认——验收抓到的确认链断口）。
  setNeedConfirm(v: boolean): void { this.needConfirm = v; if (this.on) this.vl.setNeedConfirm(v) }
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

  // ─── M4 S2S ───
  /** 本轮是否走 S2S。DEGRADED 期返回 false → openAsr 自动落回 classic（RFC §6.3），
   *  网关探活恢复后（session.state=ready）下一轮自动回 S2S，HMI 无分支特判。 */
  private useS2s(): boolean {
    return !!this.s2s && this.s2s.active && !this.s2s.degraded
  }

  private openS2sIfSelected(): void {
    const cfg = this.deps.getS2sConfig?.()
    if (!cfg || cfg.pipeline !== 's2s') return
    const meta = this.deps.getSessionMeta?.() || { sessionId: '' }
    const words = this.wakeStripWords()
    const strip = (t: string) => stripLeadingWakeWord(t, words)
    this.s2s = new S2SClient({
      playerFactory: (sr: number) => makeS2sPlayer(sr),
      // 转写喂 FSM（退出词/dismiss/filler/回声判据全量复用），并上屏
      onTranscript: (t: string, final: boolean) => {
        const s = strip(t)
        if (final) { this.vl.asrFinal(s); return }
        this.vl.asrPartial(s)
        if (!isFiller(s)) this.deps.onPartialText?.(s)
      },
      onAnswerDelta: (t: string) => {
        this.flushS2sUser() // 首个回答增量 = 确定自答 → 用户气泡先上屏（保证气泡顺序）
        this.deps.onS2sAnswerDelta?.(t)
      },
      onFirstAudio: () => { this.ttsSpeaking = true; this.vl.ttsStart() },
      onTurnEnd: (r: { turnId: string; reason: string; detail: string }) => {
        this.ttsSpeaking = false
        // 无文本无音频却也没逃逸（异常轮）：用户确实说了话，照样上屏，不静默吞
        if (r.reason !== 'escalated') this.flushS2sUser()
        else this.s2sPendingUser = ''  // 逃逸轮的用户气泡由 send() 插
        this.deps.onS2sTurnEnd?.(r)
        // escalated 轮由主链接管播报与收窗（App 侧走既有 send/TTS 生命周期）→ 本层不收窗
        if (r.reason !== 'escalated') this.vl.ttsEnd()
      },
      onEscalated: (r: { turnId: string; utterance: string }) => {
        this.s2sPendingUser = ''
        this.deps.onS2sEscalated?.(r.utterance, r.turnId)
      },
      onSessionState: (state: string) => {
        if (state === 'degraded') this.deps.onNotice?.('语音直连不可用，已切回常规链路')
      },
      onUnsupported: (msg: string) => {
        // 首次建会话失败/网关断开 → 彻底回落 classic（不留半死会话）
        this.deps.onNotice?.('S2S 不可用，已切回常规链路：' + msg)
        this.closeS2s()
      },
    })
    const asr = this.deps.getAsrConfig()
    this.s2s.start(s2sUrl(this.deps.audioApi), {
      session_id: meta.sessionId,
      user_id: meta.userId || '',
      vehicle_id: meta.vehicleId || '',
      voice: cfg.voice || '',
      provider: cfg.provider || '',
      model: cfg.model || '',
      language: asr.language,
      vad_silence_ms: this.vl.cfg.silenceTailMs,
    })
  }

  private closeS2s(): void {
    try { this.s2s?.close() } catch { /* ignore */ }
    this.s2s = null
    this.s2sPendingUser = ''
  }

  /** 把暂存的用户话上屏（幂等）。 */
  private flushS2sUser(): void {
    const t = this.s2sPendingUser
    if (!t) return
    this.s2sPendingUser = ''
    this.deps.onS2sUserUtterance?.(t)
  }

  /** 逃逸轮主链回答落地后回传，保持 provider 上下文连续（RFC §3.2；不触发播报）。 */
  escalatedResult(turnId: string, text: string): void {
    this.s2s?.escalatedResult(turnId, text)
  }

  /** 当前挡位是否真在 S2S 上跑（供 App 决定用哪条播报链路）。 */
  get s2sActive(): boolean {
    return this.useS2s()
  }

  /** 主动消息此刻不宜出声：S2S 交互进行中（LISTENING/THINKING/SPEAKING/FOLLOWUP）。
   *  classic 的互斥由 queueTTS 的 activeReply/streamSession 前提保障；S2S 自答轮不经
   *  dispatch()，那个前提恒空 → 主动 TTS 会与模型音频直接混音（两个播放器共用
   *  AudioContext 互不知情），且其 onplay/onend 会把 FSM 从 SPEAKING 误推 FOLLOWUP。
   *  此时降级为只出气泡——信息不丢，只是不抢话。 */
  get proactiveTtsBlocked(): boolean {
    if (!this.useS2s()) return false
    const st = this.vl.state
    return this.ttsSpeaking || (st !== 'IDLE' && st !== 'ARMED')
  }

  /**
   * 本轮说话人（M4 P4）。未开声纹/未识别/认不出一律 'primary' = P4 之前的行为。
   * App 在 buildMeta 时读它，随每轮 meta 上云 → 记忆按乘员隔离。
   */
  get occupantId(): string {
    return this.vp?.occupantId ?? 'primary'
  }

  /** 本轮说话人的显示名（HMI 可视化用，如「小雨」；未识别为空串）。 */
  get occupantName(): string {
    return this.vp?.displayName ?? ''
  }

  // 声纹面：设置里开了才建。**它必须可以随时死掉**——识别器不存在时 occupantId 恒 primary，
  // 整条语音链路逐字回落到 P4 之前（同 M3 给主动引擎选落点的判据）。
  private openVoiceprintIfEnabled(): void {
    this.vp = null
    if (!this.deps.getVoiceprintConfig?.()?.enabled) return
    const uid = this.deps.getSessionMeta?.().userId || ''
    this.vp = new VoiceprintIdentifier({
      identify: postIdentify(this.deps.audioApi, uid),
      onResult: (r: { occupant_id?: string; decision?: string; display_name?: string }) => {
        // S2S 挡位：识别一落地就告诉网关本窗说话人——自答轮的记忆回灌按它隔离
        // （classic/逃逸轮由 send() meta 带，不经此路）。认不出也发（=primary），
        // 语义与 meta 口径逐字一致。
        // 称呼取识别器的（它对非 accept 一律给空串——认不出不叫名字），不取原始响应，
        // 否则 S2S 挡位会绕过这条口径、对没认出来的人直接喊 primary 的名字。
        if (this.useS2s()) this.s2s.setOccupant(r.occupant_id || 'primary', this.vp?.displayName || '')
        this.deps.onVoiceprintResult?.(r)
      },
    })
  }

  // ─── 内部 ───
  // P2 PCM 直传（U4 根治）：不用 MediaRecorder，用 vadEngine.onFrame 喂帧 + 前滚缓冲；partial/final 剥唤醒词残留。
  // P4 真麦修复：仅续说路径（resume=true：续问/打断/宽限续说，无唤醒词）注入 pre-roll 补 VAD 判定延迟首字；
  // KWS 唤醒进入（resume=false）不注入——KWS 命中点往回取恰是唤醒词本身，会被识别成同音字残留上屏（「小周」）。
  // barge-in 修复：FSM 带 sinceSpeechStartMs（打断确认窗耗时）→ pre-roll 动态回取到 speech 起点，
  // 固定 200ms 盖不住 300ms+ 确认窗导致的「打断漏首字」在此根治。
  private openAsr(opts: { resume?: boolean; sinceSpeechStartMs?: number } = {}): void {
    // M4 P4：唤醒后首句才识别（resume=true 是续说/续问/打断，同一唤醒窗不重识——
    // 轮内改判会让同一段对话的前后半截落进不同乘员的记忆里）。两个挡位共用这一处。
    // 带上当前 VAD 语音段状态：唤醒词连着说完就接下一句时（「小舟，我是谁」）此刻已在语音段中，
    // 不带的话要等下一次 speechStart 才开始收，第一句就白等了。
    this.vp?.arm(!opts.resume, this.vadInSpeech)
    // S2S：会话常驻，进 LISTENING 只是开收音门（省掉 ASR 建连——全双工的第一份红利）。
    // pre-roll 口径与 classic 逐字一致：唤醒进入注 0（否则唤醒词自己被识别成同音字上屏），
    // 续说/打断按 speech 起点回取（真麦「小周」误上屏的同一根因，别在 S2S 上重犯）。
    if (this.useS2s()) {
      const preRollMs = opts.resume
        ? Math.min(RESUME_PRE_ROLL_MS + Math.max(0, opts.sinceSpeechStartMs || 0), MAX_PRE_ROLL_MS)
        : 0
      if (preRollMs > 0) this.s2s.pushPreRoll(this.pcmRing.takeLast(preRollMs))
      this.s2s.setCollecting(true)
      return
    }
    if (this.asr) return
    const cfg = this.deps.getAsrConfig()
    const gen = ++this.asrGen // 本条会话代号；下方回调只在代号仍为当前时才作数
    const fresh = () => gen === this.asrGen // 会话未被 closeAsr 取代
    const words = this.wakeStripWords()
    const strip = (t: string) => stripLeadingWakeWord(t, words)
    this.asr = new StreamingRecognizer()
    // 唤醒进入 pre-roll=0（唤醒词不进 ASR）；续说/打断进入按 speech 起点回取（无唤醒词，安全）
    const preRollMs = opts.resume
      ? Math.min(RESUME_PRE_ROLL_MS + Math.max(0, opts.sinceSpeechStartMs || 0), MAX_PRE_ROLL_MS)
      : 0
    const preRoll = preRollMs > 0 ? this.pcmRing.takeLast(preRollMs) : new Float32Array(0)
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
    // S2S：关收音门而不拆会话（多轮上下文是 S2S 的主场，拆了就白瞎）
    this.s2s?.setCollecting(false)
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
