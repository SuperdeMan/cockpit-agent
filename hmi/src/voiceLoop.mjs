// 语音回路状态机（R4.3 D4）——免唤醒连续对话 + 唤醒词 + 播报中打断的「大脑」。
// 纯逻辑、零 DOM/WASM 依赖：时间源、定时器、引擎事件、效果全部注入（node 可测），
// UI 与 KWS/VAD Worker 只是它的外设。消费的事件即「量产替换车机 DSP 的接口面」——
// 换 sherpa-onnx / Porcupine / 车机 DSP，本文件一字不改（见设计卡 §4 D4、§5 P1）。
//
// 状态迁移（设计卡 D4）：
//   IDLE ─handsFreeOn→ ARMED ─wake→ LISTENING ─(VAD end + 定稿)→ THINKING ─ttsStart→ SPEAKING
//   SPEAKING ─ttsEnd→ FOLLOWUP ─VAD speech→ LISTENING（免唤醒追问） / ─8s 超时→ ARMED
//   SPEAKING ─barge-in（VAD≥300ms 过护栏）→ stopTTS + LISTENING
//   LISTENING ─唤醒后 5s 无 speech→ ARMED（误唤醒静默回收，不发请求）
//   任意态 ─handsFreeOff→ IDLE（拆机）
//
// push-to-talk 与文本输入不进本 FSM（原路径原样保留）；hands-free 关闭即整个 FSM 挂空。

export const VoiceState = {
  IDLE: 'IDLE',
  ARMED: 'ARMED',
  LISTENING: 'LISTENING',
  THINKING: 'THINKING',
  SPEAKING: 'SPEAKING',
  FOLLOWUP: 'FOLLOWUP',
}

// FSM 态 → AuroraOrb 视觉态（armed/listening 为 R4.3 新增；见 AuroraOrb.tsx）。
// FOLLOWUP 用 listening 的邀请式辉光，暗示「可直接接着说」区别于 ARMED 的待机微光。
const ORB_FOR = {
  [VoiceState.IDLE]: 'idle',
  [VoiceState.ARMED]: 'armed',
  [VoiceState.LISTENING]: 'listening',
  [VoiceState.THINKING]: 'thinking',
  [VoiceState.SPEAKING]: 'speaking',
  [VoiceState.FOLLOWUP]: 'listening',
}

export const DEFAULTS = {
  followupWindowMs: 8000, // FOLLOWUP 免唤醒续问窗；超时回 ARMED（设计卡 D4，默认 8s）
  silenceTailMs: 800,     // VAD 静音尾（端点判据，由 Worker 消费；此处透传+记录）
  falseWakeMs: 5000,      // D5-1：唤醒后无 speech 的静默回收窗
  bargeInMinMs: 300,      // D6：SPEAKING 态判打断需 VAD speech 持续时长
  dismissMinChars: 2,     // D5-2：定稿字数下限，不足视为误唤醒噪声句
  dismissWords: ['没事', '不是叫你', '别听了', '没叫你'], // D5-2 本地 dismiss 词表
  selfTriggerLimit: 2,    // D6：连续疑似自触发次数到此关闭 L3
  thinkingMaxMs: 100000,  // R4.3b P0：THINKING 安全超时兜底——App 四种终局未回调 ttsEnd 时防永久卡死
}

// 汉字按码点计数（'嗯'=1、'好的'=2），避免 UTF-16 代理对误判长度。
function graphemeLen(s) {
  return [...s].length
}

export class VoiceLoop {
  constructor(opts = {}) {
    const {
      now = Date.now,
      setTimer = (fn, ms) => setTimeout(fn, ms),
      clearTimer = (h) => clearTimeout(h),
      // 效果回调（外设）——全部可选，默认空操作
      onState = () => {},          // (orbState, fsmState) 每次状态变化
      onOpenAsr = () => {},        // 进入 LISTENING：开一条 /api/asr/stream
      onCloseAsr = () => {},       // 离开 LISTENING：关该条 ASR WS
      onEndpoint = () => {},       // VAD 端点：请定稿（无 server VAD 的引擎靠它 stop 录音）
      onSend = () => {},           // 定稿通过 dismiss 校验：派发给对话链路（=App.send）
      onStopTts = () => {},        // barge-in：停播报（=audio.stopTTS）
      onWakeChime = () => {},      // 唤醒音效
      onDisableBargeIn = () => {}, // (reason) 连续自触发 → 本会话关 L3 + toast
      config = {},
    } = opts

    this.now = now
    this._setTimerFn = setTimer
    this._clearTimerFn = clearTimer
    this.onState = onState
    this.onOpenAsr = onOpenAsr
    this.onCloseAsr = onCloseAsr
    this.onEndpoint = onEndpoint
    this.onSend = onSend
    this.onStopTts = onStopTts
    this.onWakeChime = onWakeChime
    this.onDisableBargeIn = onDisableBargeIn
    this.cfg = { ...DEFAULTS, ...config }

    this.state = VoiceState.IDLE
    this._timers = {}        // 命名定时器句柄
    this._asrOpen = false
    this._speechActive = false
    this._needConfirm = false // HMI 是否有挂起的确认条（D5-2 例外的唯一权威）
    this._ttsText = ''        // 正在播报的 TTS 文本（D6 回声指纹比对）
    this._echoSuspected = false
    this._cameFromBargeIn = false
    this._selfTriggerCount = 0
    this._bargeInDisabled = false
  }

  // ─── 定时器封装（全部走注入，node 测试用 fake clock）───
  _setTimer(name, ms, fn) {
    this._clearTimer(name)
    this._timers[name] = this._setTimerFn(fn, ms)
  }
  _clearTimer(name) {
    if (name in this._timers) {
      this._clearTimerFn(this._timers[name])
      delete this._timers[name]
    }
  }
  _clearAllTimers() {
    for (const name of Object.keys(this._timers)) this._clearTimer(name)
  }

  // ─── 状态进入（集中触发 onState；ASR 开关与定时器由各 goto 显式管理）───
  _enter(state) {
    this.state = state
    this.onState(ORB_FOR[state], state)
  }
  _closeAsr() {
    if (this._asrOpen) {
      this._asrOpen = false
      this.onCloseAsr()
    }
  }

  _gotoIdle() {
    this._clearAllTimers()
    this._closeAsr()
    this._speechActive = false
    // 会话级护栏在拆机时复位：重新开 hands-free 视为新会话
    this._selfTriggerCount = 0
    this._bargeInDisabled = false
    this._cameFromBargeIn = false
    this._enter(VoiceState.IDLE)
  }
  _gotoArmed() {
    this._clearAllTimers()
    this._closeAsr()
    this._speechActive = false
    this._enter(VoiceState.ARMED)
  }
  _gotoThinking() {
    this._clearAllTimers()
    this._closeAsr()
    this._speechActive = false
    this._enter(VoiceState.THINKING)
    // R4.3b P0（U2/§2.2b 死锁兜底）：THINKING 离场仅靠 ttsStart/ttsEnd 两条腿，而 App 在
    // 「TTS 关 / 纯卡片无语音 / error / 看门狗超时 / TTS 合成全失败」五种终局可能不回调 ttsEnd。
    // 正路是 App 在这些分支补调 turnEnded()；本定时器只作最后防线，避免任一遗漏导致永久全聋。
    // 迁出 THINKING 的各 goto 均 _clearAllTimers()，正常路径此定时器随即清除、永不触发。
    this._setTimer('thinking', this.cfg.thinkingMaxMs, () => {
      if (this.state === VoiceState.THINKING) this._gotoFollowup()
    })
  }
  _gotoSpeaking() {
    this._clearAllTimers()
    this._speechActive = false
    this._echoSuspected = false
    this._enter(VoiceState.SPEAKING)
  }
  _gotoFollowup() {
    this._clearAllTimers()
    this._speechActive = false
    this._enter(VoiceState.FOLLOWUP)
    this._setTimer('followup', this.cfg.followupWindowMs, () => this._gotoArmed())
  }
  _enterListening({ speechAlreadyStarted = false, fromBargeIn = false } = {}) {
    this._clearAllTimers()
    this._cameFromBargeIn = fromBargeIn
    this._echoSuspected = false
    this._speechActive = speechAlreadyStarted
    if (!this._asrOpen) {
      this._asrOpen = true
      this.onOpenAsr()
    }
    this._enter(VoiceState.LISTENING)
    // 唤醒进入且尚无 speech → 挂误唤醒回收窗；续问/打断进入时 speech 已起，不挂。
    if (!speechAlreadyStarted) {
      this._setTimer('falseWake', this.cfg.falseWakeMs, () => this._gotoArmed())
    }
  }

  // ─── 外部注入的状态（HMI 侧持有，非 FSM 自身能知）───
  setNeedConfirm(v) {
    this._needConfirm = !!v
  }
  setTtsText(text) {
    this._ttsText = (text || '').toString()
  }

  // ─── 引擎/UI 事件入口 ───
  handsFreeOn() {
    if (this.state === VoiceState.IDLE) this._gotoArmed()
  }
  handsFreeOff() {
    if (this.state !== VoiceState.IDLE) this._gotoIdle()
  }

  // KWS 唤醒词命中。ARMED/FOLLOWUP 待机中 → 进聆听；SPEAKING 中 → 唤醒词打断（KWS 在 D6 保持有效）。
  wake() {
    if (this.state === VoiceState.ARMED || this.state === VoiceState.FOLLOWUP) {
      this.onWakeChime()
      this._enterListening()
    } else if (this.state === VoiceState.SPEAKING) {
      // 唤醒词打断不受 300ms VAD 护栏约束（显式意图）；若唤醒词恰在播报文本内则由 Worker 抑制。
      this.onStopTts()
      this.onWakeChime()
      this._enterListening()
    }
  }

  vadSpeechStart() {
    switch (this.state) {
      case VoiceState.LISTENING:
        this._speechActive = true
        this._clearTimer('falseWake') // 有真实开口 → 撤销误唤醒回收
        break
      case VoiceState.FOLLOWUP:
        this._enterListening({ speechAlreadyStarted: true }) // 免唤醒追问
        break
      case VoiceState.SPEAKING:
        if (this._bargeInDisabled) return
        this._speechActive = true
        this._echoSuspected = false
        this._setTimer('bargeIn', this.cfg.bargeInMinMs, () => this._bargeInFire())
        break
      default:
        break // ARMED/THINKING/IDLE：非 KWS 的 speech 不触发任何动作
    }
  }

  vadSpeechEnd() {
    if (this.state === VoiceState.LISTENING) {
      if (this._speechActive) {
        this._speechActive = false
        this.onEndpoint() // 端点到达 → 请引擎定稿
      }
    } else if (this.state === VoiceState.SPEAKING) {
      // 开口不足 bargeInMinMs 即结束 → _bargeInFire 时会看到 !speechActive 从而不打断
      this._speechActive = false
    }
  }

  asrPartial(text) {
    const t = (text || '').trim()
    if (!t) return
    if (this.state === VoiceState.LISTENING) {
      this._speechActive = true
      this._clearTimer('falseWake') // partial 早于 vadSpeechStart 的引擎也能撤销回收
    } else if (this.state === VoiceState.SPEAKING) {
      // D6 回声指纹：SPEAKING 期若有 ASR partial 且被正在播的 TTS 文本包含 → 判为自触发回声
      if (this._overlapsTts(t)) this._echoSuspected = true
    }
  }

  asrFinal(text) {
    if (this.state !== VoiceState.LISTENING) return
    const t = (text || '').trim()

    // 打断后回声自检（D6）：从 barge-in 进入的 LISTENING，定稿为空/高度重合播报文本 = 自触发
    if (this._cameFromBargeIn) {
      this._cameFromBargeIn = false
      if (!t || this._overlapsTts(t)) {
        this._countSelfTrigger()
        this._gotoArmed()
        return
      }
      this._selfTriggerCount = 0 // 真实打断 → 复位连续计数
    }

    if (!t) {
      // 什么都没说清 → 静默回收，不上云
      this._gotoArmed()
      return
    }

    const isDismiss =
      graphemeLen(t) < this.cfg.dismissMinChars || this.cfg.dismissWords.includes(t)
    // D5-2 分界：本地 dismiss 仅在「无挂起确认」时生效；确认条可见时一切定稿照发
    // （裸「取消/不要」必须到云端走 F1 确认闭环——承接 R4.1 §5.3 红线）。
    if (isDismiss && !this._needConfirm) {
      this._gotoArmed()
      return
    }

    this._selfTriggerCount = 0
    this.onSend(t)
    this._gotoThinking()
  }

  // 助手回复开始播报（TTS 首帧/首句起播）：THINKING → SPEAKING。
  ttsStart() {
    if (this.state === VoiceState.THINKING) this._gotoSpeaking()
  }
  // 助手回复播完，或无音频时（TTS 关/纯文本）由 App 在 final 到达时调用 → 收窗进 FOLLOWUP。
  ttsEnd() {
    if (this.state === VoiceState.SPEAKING || this.state === VoiceState.THINKING) {
      this._gotoFollowup()
    }
  }

  // ─── 内部判定 ───
  _bargeInFire() {
    if (this.state !== VoiceState.SPEAKING) return
    if (!this._speechActive) return // 开口 <300ms 已结束 → 不打断
    if (this._echoSuspected) {
      this._countSelfTrigger() // 疑似回声 → 计入自触发，不打断
      return
    }
    this.onStopTts()
    this._enterListening({ speechAlreadyStarted: true, fromBargeIn: true })
  }

  _countSelfTrigger() {
    this._selfTriggerCount += 1
    if (this._selfTriggerCount >= this.cfg.selfTriggerLimit) {
      this._bargeInDisabled = true
      this.onDisableBargeIn('repeated-self-trigger')
    }
  }

  _overlapsTts(t) {
    const tts = this._ttsText
    if (!tts || !t) return false
    return tts.includes(t) || t.includes(tts)
  }

  // ─── 供 UI / 测试读取 ───
  get orbState() {
    return ORB_FOR[this.state]
  }
  get bargeInDisabled() {
    return this._bargeInDisabled
  }
}
