// S2S 会话客户端（M4）——连网关 /api/s2s，把对上事件协议翻成 voiceLoop 能吃的效果。
//
// 设计依据：docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md（§3.2 对上协议、§4.1 六态重映射）。
// 纯逻辑 + 注入（WebSocket 工厂 / PCM 播放器工厂 / 定时器），可用 node:test 无 DOM 单测——
// 同 ws.mjs / voiceLoop.mjs 的做法。
//
// **本文件不含任何状态机**：用户可感知状态的唯一权威是 voiceLoop（L-HMI）。这里只做
// 「协议翻译 + 音频播放 + 收音门控」，事件一律回调出去由 controller 决定怎么驱动 FSM。
// 违反这条就会重现 RFC §2.2 反例：provider 事件直接把 HMI 置 SPEAKING，与本侧 barge-in 竞态。
//
// 收音门控的收敛（比 RFC §4.1 原文更保守，理由见 §4.3①）：**只在 LISTENING 期推流**。
// provider 的 server VAD 带 interrupt_response=true——SPEAKING 期若继续推流，provider 会
// 自己判打断并 cancel，与「本侧权威」冲突。不推流则 provider 根本不会自主打断，本侧 100%
// 权威；而会话常驻带来的「唤醒后零建连延迟」红利照旧（省的是建连，不是推流）。

import { float32ToInt16 } from './pcmRing.mjs'

// 上行帧类型（与 llm-gateway/s2s/protocol.py 一一对应，改一处必改两处）
export const UP = {
  START: 'session.start',
  AUDIO: 'audio',
  BARGE_IN: 'barge_in',
  CANCEL_TURN: 'cancel_turn',
  ESCALATED_RESULT: 'escalated_result',
  END: 'session.end',
}
// 下行帧类型
export const DOWN = {
  TRANSCRIPT: 'turn.transcript',
  ANSWER_DELTA: 'turn.answer_delta',
  AUDIO_META: 'turn.audio_meta',
  TURN_END: 'turn.end',
  ESCALATED: 'turn.escalated',
  SESSION_STATE: 'session.state',
  UNSUPPORTED: 'unsupported',
}

const OPEN = 1
const SEND_CHUNK_BYTES = 3200 // ~100ms @16k s16le：与 /api/asr/stream 同聚包粒度

export function s2sUrl(apiBase) {
  return apiBase.replace(/^http/, 'ws') + '/api/s2s'
}

export class S2SClient {
  /** @param {object} o
   *  @param {(url:string)=>any} [o.wsFactory]
   *  @param {(sampleRate:number)=>any|null} [o.playerFactory]  返回 null=无音频上下文（静默降级）
   *  @param {object} [o.timers]  {set,clear}
   *  @param {(text:string,final:boolean)=>void} [o.onTranscript]   用户话转写（上屏）
   *  @param {(text:string)=>void} [o.onAnswerDelta]                回答文本增量（气泡）
   *  @param {()=>void} [o.onFirstAudio]                            首片起播 → FSM ttsStart
   *  @param {(r:{turnId,reason,detail})=>void} [o.onTurnEnd]       音频播完后 → FSM ttsEnd
   *  @param {(r:{turnId,utterance})=>void} [o.onEscalated]         逃逸 → 走既有 send
   *  @param {(state:string)=>void} [o.onSessionState]              ready|reconnecting|degraded
   *  @param {(msg:string)=>void} [o.onUnsupported]                 回落三段式
   */
  constructor(o = {}) {
    this._wsFactory = o.wsFactory || ((u) => new WebSocket(u))
    this._playerFactory = o.playerFactory || (() => null)
    this._timers = o.timers || { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h) }
    this.onTranscript = o.onTranscript || (() => {})
    this.onAnswerDelta = o.onAnswerDelta || (() => {})
    this.onFirstAudio = o.onFirstAudio || (() => {})
    this.onTurnEnd = o.onTurnEnd || (() => {})
    this.onEscalated = o.onEscalated || (() => {})
    this.onSessionState = o.onSessionState || (() => {})
    this.onUnsupported = o.onUnsupported || (() => {})

    this.ws = null
    this.opened = false
    this.collecting = false      // LISTENING 门控：false 时 VAD 帧不上行
    this.state = ''              // 网关上报的会话态
    this.degraded = false
    this.player = null
    this.sampleRate = 24000
    this.turnId = ''             // 当前轮（供 barge_in 后的残帧判断/回传）
    this._buf = []               // 待发 Int16 聚包
    this._bufBytes = 0
    this._startMsg = null        // ws 未 open 时缓存 session.start
    this._endTimer = null
    this._pendingEnd = null      // 已收 turn.end、等音频播完
    this._closed = false
  }

  get active() {
    return !!this.ws && !this._closed
  }

  /** 建会话。opts 进 session.start 帧（session_id/user_id/voice/provider/model/vad_silence_ms）。 */
  start(url, opts = {}) {
    if (this.ws) return
    this._closed = false
    const msg = { type: UP.START, ...opts }
    let ws
    try {
      ws = this._wsFactory(url)
    } catch (e) {
      this.onUnsupported('S2S 连接失败：' + (e && e.message ? e.message : String(e)))
      return
    }
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    this._startMsg = msg
    ws.onopen = () => {
      this.opened = true
      this._send(this._startMsg)
      this._startMsg = null
    }
    ws.onmessage = (ev) => this._onMessage(ev)
    ws.onerror = () => { if (!this.opened) this.onUnsupported('S2S 连接失败') }
    ws.onclose = () => {
      const wasOpen = this.opened
      this.opened = false
      this.ws = null
      this._stopPlayback()
      if (!this._closed) {
        // 网关侧断开（非用户主动 close）→ 回落三段式；网关内部的 provider 重连由
        // session.state 上报，不会走到这里（那条 WS 不断）。
        this.onUnsupported(wasOpen ? 'S2S 会话已断开' : 'S2S 连接失败')
      }
    }
  }

  close() {
    this._closed = true
    this._clearEndTimer()
    this._send({ type: UP.END })
    this._stopPlayback()
    try { this.ws && this.ws.close() } catch { /* ignore */ }
    this.ws = null
    this.opened = false
    this.collecting = false
    this._buf = []
    this._bufBytes = 0
  }

  // ─── 收音（VAD 帧旁路复用：与 classic 的 StreamingRecognizer.pushFrame 同接口面）───
  /** LISTENING 门控。关时立即 flush 已攒帧（末尾几十毫秒不丢），并停止后续上行。 */
  setCollecting(on) {
    const next = !!on
    if (next === this.collecting) return
    this.collecting = next
    if (!next) this._flush()
  }

  /** @param {Float32Array} frame VAD 帧（与 pcmRing 同源）。非 collecting 期静默丢弃。 */
  pushFrame(frame) {
    if (!this.collecting || !frame || !frame.length) return
    const i16 = float32ToInt16(frame)
    this._buf.push(i16)
    this._bufBytes += i16.length * 2
    if (this._bufBytes >= SEND_CHUNK_BYTES) this._flush()
  }

  /** 注入前滚缓冲（唤醒/续说的首字补偿，与 classic 的 pre-roll 同思想）。 */
  pushPreRoll(frame) {
    if (!frame || !frame.length) return
    const i16 = float32ToInt16(frame)
    this._buf.unshift(i16)
    this._bufBytes += i16.length * 2
  }

  _flush() {
    if (!this._buf.length) return
    const total = this._buf.reduce((n, a) => n + a.length, 0)
    const merged = new Int16Array(total)
    let off = 0
    for (const a of this._buf) { merged.set(a, off); off += a.length }
    this._buf = []
    this._bufBytes = 0
    if (this.ws && this.ws.readyState === OPEN && !this.degraded) {
      try {
        this._send({ type: UP.AUDIO })  // 声明式前导帧（与 ASR 面同惯例）
        this.ws.send(merged.buffer)
      } catch { /* 帧丢弃静默 */ }
    }
  }

  // ─── 打断 / 取消 / 逃逸回传 ───
  /** ①听感打断：**先本地停播**（听感优先，不等网关回执），再上行让网关 cancel provider。 */
  bargeIn() {
    this._clearEndTimer()
    this._pendingEnd = null
    this._stopPlayback()
    this._send({ type: UP.BARGE_IN })
  }

  cancelTurn() {
    this._clearEndTimer()
    this._pendingEnd = null
    this._stopPlayback()
    this._send({ type: UP.CANCEL_TURN })
  }

  /** 逃逸轮主链回答回传（只为 provider 上下文连续，不为播报；见 RFC §3.2）。 */
  escalatedResult(turnId, text) {
    if (!turnId) return
    this._send({ type: UP.ESCALATED_RESULT, turn_id: turnId, text: text || '' })
  }

  _send(obj) {
    if (!this.ws || this.ws.readyState !== OPEN) return false
    try {
      this.ws.send(JSON.stringify(obj))
      return true
    } catch {
      return false
    }
  }

  // ─── 下行 ───
  _onMessage(ev) {
    if (typeof ev.data !== 'string') {
      this._onAudio(ev.data)
      return
    }
    let m
    try { m = JSON.parse(ev.data) } catch { return }
    switch (m.type) {
      case DOWN.TRANSCRIPT:
        this.turnId = m.turn_id || this.turnId
        this.onTranscript(m.text || '', !!m.final)
        break
      case DOWN.ANSWER_DELTA:
        this.turnId = m.turn_id || this.turnId
        this.onAnswerDelta(m.text || '')
        break
      case DOWN.AUDIO_META:
        this.turnId = m.turn_id || this.turnId
        this.sampleRate = m.sample_rate || 24000
        this._openPlayer()
        break
      case DOWN.ESCALATED:
        this.turnId = m.turn_id || this.turnId
        this.onEscalated({ turnId: m.turn_id || '', utterance: m.utterance || '' })
        break
      case DOWN.TURN_END:
        this._onTurnEnd(m)
        break
      case DOWN.SESSION_STATE:
        this.state = m.state || ''
        this.degraded = this.state === 'degraded'
        if (this.degraded) this._stopPlayback()
        this.onSessionState(this.state)
        break
      case DOWN.UNSUPPORTED:
        this.onUnsupported(m.message || 'S2S 不可用')
        break
      default:
        break
    }
  }

  _openPlayer() {
    if (this.player) return
    try {
      this.player = this._playerFactory(this.sampleRate)
    } catch {
      this.player = null
    }
  }

  _onAudio(data) {
    if (!this.player) this._openPlayer()
    if (!this.player) return  // 无音频上下文：静默丢弃（文本气泡仍在，诚实降级）
    const i16 = data instanceof Int16Array ? data : new Int16Array(data)
    if (!i16.length) return
    const first = !this.player.started
    this.player.push(i16)
    if (first) this.onFirstAudio()
  }

  /** turn 结束：等已排定音频播完再回调（否则 FSM 提前进 FOLLOWUP，尾音被下一轮掐掉）。 */
  _onTurnEnd(m) {
    const info = { turnId: m.turn_id || '', reason: m.reason || '', detail: m.detail || '' }
    // escalated 轮无音频、cancelled 轮已停播 → 立即回调，不等
    if (info.reason === 'escalated' || info.reason === 'cancelled' || !this.player) {
      this._stopPlayback()
      this.onTurnEnd(info)
      return
    }
    const remainMs = (this.player.remainingSec ? this.player.remainingSec() : 0) * 1000
    this._pendingEnd = info
    this._clearEndTimer()
    this._endTimer = this._timers.set(() => {
      this._endTimer = null
      const pending = this._pendingEnd
      this._pendingEnd = null
      this._stopPlayback()
      if (pending) this.onTurnEnd(pending)
    }, remainMs + 120)
  }

  _clearEndTimer() {
    if (this._endTimer !== null) {
      this._timers.clear(this._endTimer)
      this._endTimer = null
    }
  }

  _stopPlayback() {
    if (this.player) {
      try { this.player.stop() } catch { /* ignore */ }
      this.player = null
    }
  }
}
