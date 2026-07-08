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

import { looksComplete, isFiller, matchExitWord, graphemeLen } from './utteranceHeuristics.mjs'

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
  dismissWords: ['没事', '不是叫你', '别听了', '没叫你'], // D5-2 本地 dismiss 词表（与 exitWords 并入同一匹配器）
  selfTriggerLimit: 2,    // D6：连续疑似自触发次数到此关闭 L3
  thinkingMaxMs: 100000,  // R4.3b P0：THINKING 安全超时兜底——App 四种终局未回调 ttsEnd 时防永久卡死
  // R4.3b P1（U3 退出）：退下类指令本地消化（短应答 + 回待机），不上云。needConfirm 时照发走 F1（红线）。
  exitWords: ['退下', '退下吧', '下去吧', '退出聆听', '再见', '拜拜', '闭嘴', '别说了', '先这样', '没事了', '不用了', '结束对话'],
  endpointGraceMs: 700,   // R4.3b P1（U5b）：端点宽限合并窗——定稿疑似没说完时等续说的时长；0 即关
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
      onExitAck = () => {},        // U3：命中退出/dismiss 词 → 播退场应答（外设播「好的」短语音）
      onCancelTurn = () => {},     // U2/P2：THINKING 期唤醒词打断 → 请求取消在飞的云端处理
      onMetric = () => {},         // P3 obs：语义事件名（wake/filler_dismissed/endpoint_merge/…）→ 外设累计
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
    this.onExitAck = onExitAck
    this.onCancelTurn = onCancelTurn
    this.onMetric = onMetric
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
    this._bargeInDisabled = false      // 自触发降级：会话级不可恢复（回声连续误触）
    this._vadBargeInDisabled = false   // R4.4 P2 嘈杂降级：仅唤醒词模式关 VAD 打断，可恢复（与自触发分开）
    // R4.3b P1 端点宽限合并（U5b）：待发文本 + 续说前缀 + 本轮 speech 累计时长（短语音噪声判据）。
    this._pendingText = ''    // 疑似没说完、正在宽限等续说的定稿文本
    this._pendingPrefix = ''  // 续说重开 ASR 前的已定稿前缀（下次定稿拼在其后）
    this._utteranceMs = 0     // 本轮已结束 speech 段的累计时长
    this._speechStartAt = 0   // 当前 speech 段起点（now()）；0=当前无进行中 speech
    this._listenSource = ''   // R4.4：本轮聆听进入来源（wake|followup|bargein）→ 定稿 onSend 带给云端拒识判定
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
  // 清端点宽限态（放弃续说合并）：被 wake/dismiss/send/拆机等中断路径调用，防前缀泄漏到下一轮。
  _clearPending() {
    this._clearTimer('grace')
    this._pendingText = ''
    this._pendingPrefix = ''
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
    this._clearPending()
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
    this._clearPending()
    this._closeAsr()
    this._speechActive = false
    this._enter(VoiceState.ARMED)
  }
  _gotoThinking() {
    this._clearAllTimers()
    this._clearPending()
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
    this._closeAsr() // 从 LISTENING 进入（filler/没说清继续聆听）时 ASR 仍开着；正常 SPEAKING→FOLLOWUP 已关，幂等
    this._speechActive = false
    this._enter(VoiceState.FOLLOWUP)
    this._setTimer('followup', this.cfg.followupWindowMs, () => this._gotoArmed())
  }
  _enterListening({ speechAlreadyStarted = false, fromBargeIn = false, source = '' } = {}) {
    this._clearAllTimers()
    this._cameFromBargeIn = fromBargeIn
    // R4.4：source 仅在新一轮聆听进入时给（wake/followup/bargein）；宽限续说/merge 不传 →
    // 保持本轮首值（一句被端点合并的续说仍归属首次进入来源）。
    if (source) this._listenSource = source
    this._echoSuspected = false
    this._speechActive = speechAlreadyStarted
    // 新一段聆听重置本轮 speech 计时；续说/打断进入时 speech 已在进行，起点即此刻。
    this._utteranceMs = 0
    this._speechStartAt = speechAlreadyStarted ? this.now() : 0
    if (!this._asrOpen) {
      this._asrOpen = true
      // resume=true（续问/打断/宽限续说）→ 控制器可注入短 pre-roll 补 VAD 判定延迟首字；
      // resume=false（KWS 唤醒进入）→ 不注入 pre-roll，避免把唤醒词本身喂进 ASR（真麦「小周」误上屏根因）。
      this.onOpenAsr({ resume: speechAlreadyStarted })
    }
    this._enter(VoiceState.LISTENING)
    // 唤醒进入且尚无 speech → 挂误唤醒回收窗；续问/打断进入时 speech 已起，不挂。
    if (!speechAlreadyStarted) {
      this._setTimer('falseWake', this.cfg.falseWakeMs, () => { this.onMetric('false_wake_dismissed'); this._gotoArmed() })
    }
  }

  // ─── 外部注入的状态（HMI 侧持有，非 FSM 自身能知）───
  setNeedConfirm(v) {
    this._needConfirm = !!v
  }
  // R4.4 P2：嘈杂降级「仅唤醒词」时关 VAD barge-in（可恢复，与自触发 _bargeInDisabled 分开）。
  setVadBargeInDisabled(v) {
    this._vadBargeInDisabled = !!v
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
      this._clearPending() // 唤醒是新意图 → 放弃任何在宽限中的续说残留
      this.onMetric('wake')
      this.onWakeChime()
      this._enterListening({ source: 'wake' })
    } else if (this.state === VoiceState.SPEAKING) {
      // 唤醒词打断不受 300ms VAD 护栏约束（显式意图）；若唤醒词恰在播报文本内则由 Worker 抑制。
      this._clearPending()
      this.onStopTts()
      this.onWakeChime()
      this._enterListening({ source: 'wake' })
    } else if (this.state === VoiceState.THINKING) {
      // U2 真打断（P2）：处理中喊唤醒词 → 取消在飞的云端处理 + 提示音 + 重新聆听。
      // 仅 KWS 唤醒词可打断 THINKING（显式意图）；VAD speech 不行（THINKING 期环境音太易误触）。
      this._clearPending()
      this.onMetric('turn_cancelled')
      this.onCancelTurn()
      this.onWakeChime()
      this._enterListening({ source: 'wake' })
    }
  }

  vadSpeechStart() {
    switch (this.state) {
      case VoiceState.LISTENING:
        if (this._pendingText) {
          // U5b 宽限内续说：把待发文本作前缀，重开 ASR 接着收（「导航去」+「西溪湿地」拼一句）
          this.onMetric('endpoint_merge')
          this._pendingPrefix = this._pendingText
          this._pendingText = ''
          this._clearTimer('grace')
          this._enterListening({ speechAlreadyStarted: true })
        } else {
          this._speechActive = true
          this._speechStartAt = this.now() // 记 speech 段起点（短语音噪声判据）
          this._clearTimer('falseWake') // 有真实开口 → 撤销误唤醒回收
        }
        break
      case VoiceState.FOLLOWUP:
        this._enterListening({ speechAlreadyStarted: true, source: 'followup' }) // 免唤醒追问
        break
      case VoiceState.SPEAKING:
        // 自触发降级（不可恢复）或 R4.4 P2 嘈杂降级（可恢复）时，VAD 不打断——但 wake()（KWS
        // 唤醒词）仍能打断（显式意图，走上面 wake 分支不受此门控），故「仅唤醒词打断」天然成立。
        if (this._bargeInDisabled || this._vadBargeInDisabled) return
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
        if (this._speechStartAt) { this._utteranceMs += this.now() - this._speechStartAt; this._speechStartAt = 0 }
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
    // 端点宽限合并：本轮若在续说前缀之后 → 拼接（「导航去」+「西溪湿地」）。前缀已消费即清。
    const prefix = this._pendingPrefix
    this._pendingPrefix = ''
    const t = (prefix + (text || '')).trim()

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
      // 什么都没说清（ASR 空定稿）→ 不上云，但**继续聆听**（进续问窗），不踢回待机
      this._gotoFollowup()
      return
    }

    // D5-2 红线：确认条可见时一切定稿照发上云（裸「取消/不要」走 F1）——下列本地消化仅在无挂起确认时生效。
    // P4 真麦修复：区分「退出意图」与「没说清」——退出词回待机（ARMED），filler/短语音/没说清进续问窗
    // （FOLLOWUP，orb 仍聆听态、可直接接着说、8s 无接话才回待机），不因一句语气词就把用户踢出聆听。
    if (!this._needConfirm) {
      // U3 退出/dismiss 词**优先**（明确退出意图，去尾语气词后占据整句匹配）→ 短应答后回待机，不上云。
      // 优先于下面的 filler/短语音——否则「退下」这类退出词会因短而被当「继续聆听」，与用户意图相反。
      if (matchExitWord(t, this._exitDismissWords())) {
        this.onMetric('exit_word')
        this.onExitAck()
        this._gotoArmed()
        return
      }
      // U5a 语气词：纯口头噪声（嗯嗯/哈哈/唔）不上云，但**继续聆听**（进续问窗，不踢回待机）
      if (isFiller(t)) { this.onMetric('filler_dismissed'); this._gotoFollowup(); return }
      // U5a 短语音噪声：说话过短 + 字数极少（误触/杂音）→ 继续聆听
      if (this._currentUtteranceMs() < 300 && graphemeLen(t) <= 2) { this.onMetric('filler_dismissed'); this._gotoFollowup(); return }
      // 旧 D5-2 dismissMinChars 短句兜底 → 继续聆听
      if (graphemeLen(t) < this.cfg.dismissMinChars) { this._gotoFollowup(); return }
    }

    this._selfTriggerCount = 0

    // U5b 端点宽限合并（完整度优先——完整句直发不拖慢；qwen3 提前定稿的完整句同样不必等）
    if (looksComplete(t) || this.cfg.endpointGraceMs <= 0) {
      this._finalizeSend(t)
    } else if (this._speechActive) {
      // 悬挂结尾且 VAD 仍在 speech（qwen3 服务端提前定稿、客户端静音尾更长）→ 零等待续说拼接
      this.onMetric('endpoint_merge')
      this._pendingPrefix = t
      this._closeAsr()
      this._enterListening({ speechAlreadyStarted: true })
    } else {
      // 悬挂结尾且已停 → 进宽限等续说；宽限满则发送
      this._enterPendingSend(t)
    }
  }

  _finalizeSend(text) {
    // R4.4：第二参带 hands-free 来源 + 本轮 speech 时长 → App 拼成 meta.input_source 上云做拒识判定。
    // 旧调用方（不读第二参）兼容。默认 'followup'（最保守：续问窗是最大垃圾入口）。
    this.onSend(text, { source: this._listenSource || 'followup', utteranceMs: Math.round(this._currentUtteranceMs()) })
    this._gotoThinking()
  }

  // 退出/dismiss 合并词表（去尾语气词后精确匹配，见 utteranceHeuristics.matchExitWord）
  _exitDismissWords() {
    return [...(this.cfg.exitWords || []), ...(this.cfg.dismissWords || [])]
  }

  // 本轮已结束 speech 段累计 + 进行中段（用 _speechActive 作是否在段内的权威判据，避免 now()=0 歧义）
  _currentUtteranceMs() {
    return this._utteranceMs + (this._speechActive ? this.now() - this._speechStartAt : 0)
  }

  // 进宽限微态（仍是 LISTENING，orb 不变）：关掉刚定稿的会话，挂宽限定时器等续说
  _enterPendingSend(text) {
    this._closeAsr()
    this._speechActive = false
    this._pendingText = text
    this._setTimer('grace', this.cfg.endpointGraceMs, () => this._flushPendingSend())
  }

  // 宽限满无续说：把待发文本送出
  _flushPendingSend() {
    const text = this._pendingText
    this._clearPending()
    if (text) this._finalizeSend(text)
    else this._gotoArmed()
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
    this.onMetric('barge_in')
    this.onStopTts()
    this._enterListening({ speechAlreadyStarted: true, fromBargeIn: true, source: 'bargein' })
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
