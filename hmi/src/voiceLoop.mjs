// 语音回路状态机（R4.3 D4）——免唤醒连续对话 + 唤醒词 + 播报中打断的「大脑」。
// 纯逻辑、零 DOM/WASM 依赖：时间源、定时器、引擎事件、效果全部注入（node 可测），
// UI 与 KWS/VAD Worker 只是它的外设。消费的事件即「量产替换车机 DSP 的接口面」——
// 换 sherpa-onnx / Porcupine / 车机 DSP，本文件一字不改（见设计卡 §4 D4、§5 P1）。
//
// 状态迁移（设计卡 D4）：
//   IDLE ─handsFreeOn→ ARMED ─wake→ LISTENING ─(VAD end + 定稿)→ THINKING ─ttsStart→ SPEAKING
//   SPEAKING ─ttsEnd→ FOLLOWUP ─VAD speech→ LISTENING（免唤醒追问） / ─8s 超时→ ARMED
//   SPEAKING ─barge-in（VAD≥300ms 过护栏）→ stopTTS + LISTENING
//            （语义打断，2026-10-11：注入了插话判定时改为压低旧回答 + LISTENING，定稿后 THINKING 等判定——
//             判受话才停旧回答、发出这一句；判非受话恢复音量回 SPEAKING。设计 2026-10-11-semantic-barge-in.md）
//   ARMED ─wake（外设注入了唤醒应答）→ LISTENING（先不开 ASR：240ms 判断窗看用户是不是一口气接着说——
//            接着说 ⇒ 判断窗那段作前滚开 ASR、不回应答；停下来 ⇒ 播「我在」，播完才开 ASR。设计 2026-10-11-wake-acknowledgement.md）
//   LISTENING ─唤醒后 5s 无 speech→ ARMED（误唤醒静默回收，不发请求）
//   THINKING/SPEAKING/FOLLOWUP ─turnRejected（云端判非受话）→ ARMED（结束这次连续对话，不再开续问窗）
//   任意态 ─handsFreeOff→ IDLE（拆机）
//   非 IDLE ─systemInterrupt（来电 / 闹钟 / 抢焦点）→ ARMED（停播、放弃收音与续问窗，不发）
//
// 续问窗的截止时刻从助手答完那一刻起算：窗里收进来又被本地丢掉的一句（语气词 / 空定稿 / 回声 / 短噪声）
// 只回到窗的剩余时间，不续窗（嘈杂车厢里窗口曾经可以一直不关；设计 2026-10-10-handsfree-followup-rejection §4.2）。
//
// push-to-talk 与文本输入不进本 FSM（原路径原样保留）；hands-free 关闭即整个 FSM 挂空。

import { looksComplete, isFiller, matchExitWord, graphemeLen } from './utteranceHeuristics.mjs'
import { normSpeech } from './ttsQueue.mjs'

/** 回声判据的重合比阈值（`_overlapsTts`）。**方向是泓舟 2026-08-29 当轮定的：宁可吞掉真续问**
 *  ——判错成回声=用户多说一遍；判错成真话=进正反馈死循环、反复上云，回声里的词还可能被当指令。
 *  两边代价不对称，所以阈值往「容易判成回声」那边偏。0.75 的由来是两条**真机原字**都要收住：
 *  「深圳市的」(4) 与播报的公共子序列是「深圳市」(3)=0.75；
 *  「阿加门农」(4) 与「阿伽门农是…」的是「阿门农」(3)=0.75。而真续问「那明天呢」只有 0.25，留了间距。 */
const ECHO_OVERLAP_RATIO = 0.75

/** 最长公共**子序列**长度（滚动 DP，O(n·m) 时间 / O(m) 空间）。
 *
 *  ⚠ **这里刻意不用「最长连续子串」——那一版被真机打穿过**（2026-08-29）：
 *  播报「阿**伽**门农是希腊联军统帅，」，ASR 听成「阿**加**门农」，
 *  中间一个同音字替换就把连续匹配劈成 `阿` 与 `门农` 两段，最长一段只有 2/4=0.5，
 *  判不出是回声。**同音字替换正是中文 ASR 的主要错误形态**，而它恰好是连续子串最怕的。
 *  子序列容忍替换：`阿门农` 仍是 3/4=0.75，收得住。
 *  代价是判据变松（子序列可以跳着匹配），这个方向是刻意选的——见 ECHO_OVERLAP_RATIO 头注。 */
function longestCommonRun(a, b) {
  if (!a || !b) return 0
  let prev = new Uint16Array(b.length + 1)
  let cur = new Uint16Array(b.length + 1)
  for (let i = 1; i <= a.length; i += 1) {
    for (let j = 1; j <= b.length; j += 1) {
      cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1])
    }
    const swap = prev
    prev = cur
    cur = swap
    cur.fill(0)
  }
  return prev[b.length]
}

/** 文本重合只证明「疑似回声」。调用方负责用当前播报/采集状态限定适用范围。 */
export function isTtsEcho(text, reference) {
  const tts = normSpeech(reference || '')
  const heard = normSpeech(text || '')
  if (!tts || !heard) return false
  if (tts.includes(heard) || heard.includes(tts)) return true
  return longestCommonRun(heard, tts) / heard.length >= ECHO_OVERLAP_RATIO
}

/** 没喊唤醒词就收进来的聆听来源（续问窗 / 播报中插话）。上云时带 `voice_` 前缀成 `meta.input_source`；
 *  云端同一组来源走续问窗自己的受话判定（`orchestrator/cloud/admission.py::CONTINUATION_SOURCES`，两边由测试对账）。 */
export const CONTINUATION_SOURCES = Object.freeze(['followup', 'bargein'])

/** 这一轮的 `meta.input_source` 是不是续问窗来的（客户端据此把那句先显示成待定，等云端判定）。 */
export function isContinuationInput(inputSource) {
  return CONTINUATION_SOURCES.some((source) => inputSource === 'voice_' + source)
}

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
  // 语义打断（2026-10-11）：插话那句整句就是让助手住嘴——本地立刻停、不上云、不回应答（退出词那张表另有「好的」应答）。
  // 播报中的「好了 / 知道了」几乎都是在叫停；就算是乘客对别人说的，代价也只是停了播报——与今天「谁出声都停」相比只少不多。
  // 「暂停」刻意不在表里：它也可能是「把音乐暂停」，交给判定 + 云端（判受话照样先停旧回答）。
  bargeInStopWords: ['停', '停下', '停一下', '停停', '别说了', '别念了', '别讲了', '不用说了', '不用念了',
    '闭嘴', '安静', '够了', '好了', '可以了', '行了', '知道了'],
  bargeInJudgeMs: 6000,   // 插话判定的等待上限；到点按「判不出」处理（停旧回答、照今天的路径发出）
  // 唤醒应答（2026-10-11）：只对注入了 onWakeAck 的外设生效。看的是唤醒之后的**逐窗原始概率**，不是 VAD 的说话段——
  // 唤醒词刚说完时端点检测的静音尾还没走完，平滑状态几乎总在说话段里（R4.3b P4 用它跳过提示音就是这样失败的）。
  wakeAckDecisionMs: 240,   // 判断窗：唤醒后看这么久再决定「一口气接着说」还是「停下来等应答」
  wakeAckSpeechProb: 0.5,   // 单窗算「在说话」的概率阈值（Silero 口径）
  wakeAckOneshotRatio: 0.4, // 判断窗里说话窗占比到此即「接着说」：不回应答，直接收这句
  wakeAckTailMs: 150,       // 应答播完后再等这么久才开 ASR（扬声器余音 / 车厢混响）
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
      // 语义打断（2026-10-11）：(text, previous, done) 插话那句问一次是不是对助手说的，done(true|false|null)。
      // 不传 = 旧行为（一打断就停播）；previous 是正在播的那段（回声参照文本）。
      onJudgeBargeIn = null,
      onDuckTts = () => {},        // (on) 播报压低 / 恢复（只调音量，不停播）
      holdBargeIn = () => true,    // 此刻能不能「先压低、后判定」（S2S 挡位等不能时走旧行为）
      // 唤醒应答（2026-10-11）：() => ms，播一句「我在 / 在呢…」并返回它的时长；0 = 没播（没就绪 → 回落 onWakeChime）。
      // 不传 = 旧行为（提示音 + 立刻开 ASR）。注入了就由 wake({ack}) 决定这一次走不走判断窗。
      onWakeAck = null,
      onVadReset = () => {},       // 应答播完：清掉 VAD 端点状态（应答的回声会让它停在说话段里）
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
    this.onJudgeBargeIn = onJudgeBargeIn
    this.onDuckTts = onDuckTts
    this.holdBargeIn = holdBargeIn
    this.onWakeAck = onWakeAck
    this.onVadReset = onVadReset
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
    this._speechStartAt = null // 当前 speech 段起点（now()）；null=当前无进行中 speech（不用 0：假时钟与时间原点下 0 是合法时刻）
    this._listenSource = ''   // R4.4：本轮聆听进入来源（wake|followup|bargein）→ 定稿 onSend 带给云端拒识判定
    this._followupDeadline = null // 当前续问窗的截止时刻（now() 口径）；null=没有在跑的续问窗
    // 语义打断：旧回答被压低、等插话那句的判定（LISTENING 收音 → THINKING 判定）。期间 ttsEnd 只记「旧回答播完了」、不迁移。
    this._held = false
    this._heldEnded = false
    this._judge = null        // { token, text, voice } 在等判定的那句；null = 没有
    this._judgeSeq = 0
    // 唤醒应答：判断窗 / 应答播放中（状态是 LISTENING，ASR 还没开）。null = 不在这一段。
    // { phase: 'deciding' | 'acking', at: 唤醒时刻, windows: 判断窗收到的窗数, speech: 其中算说话的窗数 }
    this._wake = null
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
    if (state !== VoiceState.LISTENING) this._wake = null // 离开聆听（拆机 / 抢占 / 回待机）：判断窗与应答作废
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
    this._dropHold()
    this._speechActive = false
    // 会话级护栏在拆机时复位：重新开 hands-free 视为新会话
    this._selfTriggerCount = 0
    this._bargeInDisabled = false
    this._cameFromBargeIn = false
    this._followupDeadline = null
    this._enter(VoiceState.IDLE)
  }
  _gotoArmed() {
    this._clearAllTimers()
    this._clearPending()
    this._closeAsr()
    this._dropHold()
    this._speechActive = false
    this._followupDeadline = null
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
    this._followupDeadline = this.now() + this.cfg.followupWindowMs
    this._enter(VoiceState.FOLLOWUP)
    this._setTimer('followup', this.cfg.followupWindowMs, () => this._gotoArmed())
  }
  // 续问窗里收进来、又被本地丢掉的一句：回到窗的**剩余**时间，到点回 ARMED——噪声不续窗。
  // 唤醒词 / 插话进来的那一句没有在跑的续问窗，照旧开一个完整的（不因一声「嗯」把刚叫醒的用户踢走，P4 判据）。
  _backToFollowup() {
    if (this._listenSource !== 'followup' || this._followupDeadline === null) {
      this._gotoFollowup()
      return
    }
    const left = this._followupDeadline - this.now()
    if (left <= 0) {
      this.onMetric('followup_expired')
      this._gotoArmed()
      return
    }
    this._clearAllTimers()
    this._closeAsr()
    this._speechActive = false
    this._enter(VoiceState.FOLLOWUP)
    this._setTimer('followup', left, () => this._gotoArmed())
  }
  // preRollMs：外设按它**精确**回取前滚（不再叠固定前滚）——只有唤醒后一口气接着说时给，回取的正好是唤醒之后那段。
  // speechStartAt：本段 speech 的起点（now() 口径）；不给就按「此刻 − 打断确认窗」推。
  _enterListening({ speechAlreadyStarted = false, fromBargeIn = false, source = '', preRollMs = 0, speechStartAt = null } = {}) {
    this._wake = null // 正式开听：唤醒判断窗 / 应答到此结束
    this._clearAllTimers()
    this._cameFromBargeIn = fromBargeIn
    // R4.4：source 仅在新一轮聆听进入时给（wake/followup/bargein）；宽限续说/merge 不传 →
    // 保持本轮首值（一句被端点合并的续说仍归属首次进入来源）。
    if (source) this._listenSource = source
    this._echoSuspected = false
    this._speechActive = speechAlreadyStarted
    // barge-in 漏首字根因：打断需过 bargeInMinMs 确认窗（+VAD 判定延迟），期间 ASR 未开，
    // 固定 200ms pre-roll 盖不住这 300ms+ → 首字必丢。把「距 VAD speech 起点的耗时」告诉
    // 控制器，pre-roll 按它动态回取（仅 barge-in 进入；续说/追问起点即此刻，传 0 维持原短 pre-roll）。
    // 判据用 speechAlreadyStarted 而非时间戳真值——now() 可为 0（同 _currentUtteranceMs 的处理）。
    const sinceSpeechStartMs = (fromBargeIn && speechAlreadyStarted)
      ? Math.max(0, this.now() - (this._speechStartAt ?? this.now())) : 0
    // 新一段聆听重置本轮 speech 计时；续说进入时 speech 已在进行，起点即此刻。
    // 打断进入从 VAD 判到开口那一刻算起（含 300ms 确认窗）：否则「暂停」「换台」这类两字指令只剩确认之后那一截，
    // 会被短语音规则当噪声丢掉（2026-10-11 语义打断补）。
    this._utteranceMs = 0
    this._speechStartAt = speechAlreadyStarted ? (speechStartAt ?? this.now() - sinceSpeechStartMs) : null
    if (!this._asrOpen) {
      this._asrOpen = true
      // resume=true（续问/打断/宽限续说）→ 控制器可注入短 pre-roll 补 VAD 判定延迟首字；
      // resume=false（KWS 唤醒进入）→ 不注入 pre-roll，避免把唤醒词本身喂进 ASR（真麦「小周」误上屏根因）。
      // 唤醒后一口气接着说：精确回取唤醒之后那段（不含唤醒词本身——KWS 命中在唤醒词说完之后）。
      this.onOpenAsr({ resume: speechAlreadyStarted, sinceSpeechStartMs, ...(preRollMs > 0 ? { preRollMs } : {}) })
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
  // ack：这一次要不要走唤醒应答（外设注入了 onWakeAck 才有意义）。唤醒词命中传 true；
  // 轻点光球这类手动唤醒传 false——人已经准备开口了，照旧提示音 + 立刻开听。
  wake({ ack = true } = {}) {
    if (this._wake) return // 判断窗 / 应答播放中：同一声唤醒词的重复命中不再起一轮
    if (this.state === VoiceState.ARMED || this.state === VoiceState.FOLLOWUP) {
      this._clearPending() // 唤醒是新意图 → 放弃任何在宽限中的续说残留
      this.onMetric('wake')
      this._beginWake(ack)
    } else if (this.state === VoiceState.SPEAKING) {
      // 唤醒词打断不受 300ms VAD 护栏约束（显式意图）；若唤醒词恰在播报文本内则由 Worker 抑制。
      this._clearPending()
      this.onStopTts()
      this._beginWake(ack)
    } else if (this.state === VoiceState.THINKING) {
      // U2 真打断（P2）：处理中喊唤醒词 → 取消在飞的云端处理 + 提示音 + 重新聆听。
      // 仅 KWS 唤醒词可打断 THINKING（显式意图）；VAD speech 不行（THINKING 期环境音太易误触）。
      this._clearPending()
      if (this._judge) {
        // 插话还在判定：没有在飞的云端轮可取消——作废这次判定、停掉压着的旧回答，按唤醒重新聆听
        this._judge = null
        this._stopHeld()
      } else {
        this.onMetric('turn_cancelled')
        this.onCancelTurn()
      }
      this._beginWake(ack)
    }
  }

  // 唤醒进入聆听。外设没注入应答 / 这一次不要应答 ⇒ 旧路径逐字不变：提示音 + 立刻开 ASR（不注入前滚）。
  // 要应答 ⇒ 先进聆听态（光球立刻变），ASR 先不开：判断窗里看用户是不是一口气接着说。
  _beginWake(ack) {
    if (!ack || !this.onWakeAck) {
      this.onWakeChime()
      this._enterListening({ source: 'wake' })
      return
    }
    this._clearAllTimers()
    this._closeAsr()
    this._speechActive = false
    this._speechStartAt = null
    this._utteranceMs = 0
    this._listenSource = 'wake'
    this._wake = { phase: 'deciding', at: this.now(), windows: 0, speech: 0 }
    this._enter(VoiceState.LISTENING)
    this._setTimer('wake', this.cfg.wakeAckDecisionMs, () => this._decideWake())
  }

  // 判断窗满：接着说 ⇒ 不应答，判断窗那段作前滚开 ASR；停下来 ⇒ 播应答，播完（+余音）才开 ASR。
  _decideWake() {
    const w = this._wake
    if (!w || w.phase !== 'deciding' || this.state !== VoiceState.LISTENING) return
    if (w.windows > 0 && w.speech / w.windows >= this.cfg.wakeAckOneshotRatio) {
      // 「小舟小舟打开空调」：KWS 命中时人还在说——回应答只会盖住他，ASR 晚开的这一截用精确前滚补回
      this.onMetric('wake_oneshot')
      this._enterListening({ speechAlreadyStarted: true, preRollMs: Math.max(1, this.now() - w.at), speechStartAt: w.at })
      return
    }
    let ms = 0
    try {
      ms = Number(this.onWakeAck()) || 0
    } catch {
      ms = 0
    }
    if (ms <= 0) {
      // 应答没就绪（播报静音 / 片段还在合成 / 合成失败）：回落提示音，立刻开听（同旧路径）
      this.onWakeChime()
      this._enterListening({ source: 'wake' })
      return
    }
    // 应答播放期间不开 ASR、不认 VAD 起止：端上没有回声消除时，自己的「我在」会被麦收进来当成用户的话
    this.onMetric('wake_ack')
    w.phase = 'acking'
    this._setTimer('wake', ms + this.cfg.wakeAckTailMs, () => {
      if (this._wake !== w || this.state !== VoiceState.LISTENING) return
      // 应答的回声让 VAD 端点停在说话段里：清掉，用户紧接着开口才会有自己的起点（否则短指令会被当短噪声丢掉）
      this.onVadReset()
      this._enterListening({ source: 'wake' })
    })
  }

  // 逐窗语音概率（外设每个 VAD 窗报一次）。只在唤醒判断窗里有用：判断用户是不是一口气接着说。
  vadProb(prob) {
    const w = this._wake
    if (!w || w.phase !== 'deciding') return
    w.windows += 1
    if (Number(prob) >= this.cfg.wakeAckSpeechProb) w.speech += 1
  }

  vadSpeechStart() {
    // 唤醒判断窗 / 应答播放中：开没开口看逐窗概率（vadProb），应答期间的「开口」多半是自己的回声
    if (this._wake) return
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
        this._speechStartAt = this.now() // 打断 speech 起点：确认后开 ASR 按它回取 pre-roll，首字不丢
        this._echoSuspected = false
        this._setTimer('bargeIn', this.cfg.bargeInMinMs, () => this._bargeInFire())
        break
      default:
        break // ARMED/THINKING/IDLE：非 KWS 的 speech 不触发任何动作
    }
  }

  vadSpeechEnd() {
    if (this._wake) return // 同 vadSpeechStart：ASR 还没开，没有可定稿的东西
    if (this.state === VoiceState.LISTENING) {
      if (this._speechActive) {
        this._speechActive = false
        if (this._speechStartAt !== null) { this._utteranceMs += this.now() - this._speechStartAt; this._speechStartAt = null }
        this.onEndpoint() // 端点到达 → 请引擎定稿
      }
    } else if (this.state === VoiceState.SPEAKING) {
      // 开口不足 bargeInMinMs 即结束 → _bargeInFire 时会看到 !speechActive 从而不打断
      this._speechActive = false
      this._speechStartAt = null // 未成打断的短 speech：清起点，防陈旧值污染下次 pre-roll 回取
    }
  }

  asrPartial(text) {
    if (this._wake) return // 唤醒判断 / 应答期间 ASR 没开：到这里的只能是上一条会话的迟到回调
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
    if (this.state !== VoiceState.LISTENING || this._wake) return
    // 端点宽限合并：本轮若在续说前缀之后 → 拼接（「导航去」+「西溪湿地」）。前缀已消费即清。
    const prefix = this._pendingPrefix
    this._pendingPrefix = ''
    const t = (prefix + (text || '')).trim()

    // 打断后回声自检（D6）：从 barge-in 进入的 LISTENING，定稿为空/高度重合播报文本 = 自触发
    if (this._cameFromBargeIn) {
      this._cameFromBargeIn = false
      if (!t || this._overlapsTts(t)) {
        this._countSelfTrigger()
        // 语义打断：旧回答只是压低了——恢复音量接着播（播完了就照常进续问窗），不因一次回声把整段回答掐掉
        if (this._held) { this._releaseHeld('followup'); return }
        this._gotoArmed()
        return
      }
      this._selfTriggerCount = 0 // 真实打断 → 复位连续计数
    } else if (this._listenSource === 'followup' && this._overlapsTts(t)) {
      // 续问窗回声（2026-08-29 真机实证补）。此前这道自检**只挂在 barge-in 那一路**，
      // 而 FOLLOWUP 进 LISTENING 走的是 `_enterListening({source:'followup'})`、
      // `fromBargeIn` 缺省 false ⇒ 续问窗里的回声一路畅通。
      // 端上没有平台 AEC（Oboe 输入流默认 VoiceRecognition 预设，见实施计划 M4-R1），
      // 播报会被自己的麦收进来，余音正好落在 FOLLOWUP ⇒ 回声被当成「答完接着说」那一句
      // 送上云 → 云端再答一遍 → 再播一遍 ⇒ **正反馈环**（实测天气答完后连着两轮「深圳市。」）。
      //
      // 与 barge-in 那条**刻意不同的两处处置**：
      //  · **不计 `_countSelfTrigger`**——那个计数是给「要不要关掉 barge-in」用的，
      //    而这里根本没有打断任何东西；混进去会让 barge-in 被无关的回声关掉。
      //  · **不回 ARMED 而是回 FOLLOWUP**——没有 AEC 时几乎每轮都会命中，回 ARMED
      //    等于把「答完 8 秒内接着说」在 Android 上整个废掉。丢掉这一句、把窗留着才对
      //    （留的是窗的剩余时间，回声不续窗）。
      this.onMetric('echo_dismissed')
      this._backToFollowup()
      return
    }

    if (!t) {
      // 什么都没说清（ASR 空定稿）→ 不上云，但**继续聆听**（回续问窗），不踢回待机
      this._dismiss()
      return
    }

    // D5-2 红线：确认条可见时一切定稿照发上云（裸「取消/不要」走 F1）——下列本地消化仅在无挂起确认时生效。
    // P4 真麦修复：区分「退出意图」与「没说清」——退出词回待机（ARMED），filler/短语音/没说清进续问窗
    // （FOLLOWUP，orb 仍聆听态、可直接接着说、8s 无接话才回待机），不因一句语气词就把用户踢出聆听。
    if (!this._needConfirm) {
      // 语义打断：插话那句整句就是「停 / 别说了 / 够了」——本地立刻停旧回答、回待机，不上云、不回应答
      if (this._held && matchExitWord(t, this.cfg.bargeInStopWords)) {
        this.onMetric('barge_in_stop')
        this._stopHeld()
        this._gotoArmed()
        return
      }
      // U3 退出/dismiss 词**优先**（明确退出意图，去尾语气词后占据整句匹配）→ 短应答后回待机，不上云。
      // 优先于下面的 filler/短语音——否则「退下」这类退出词会因短而被当「继续聆听」，与用户意图相反。
      if (matchExitWord(t, this._exitDismissWords())) {
        this.onMetric('exit_word')
        this._stopHeld() // 语义打断里旧回答还压着：先停，再播退场应答
        this.onExitAck()
        this._gotoArmed()
        return
      }
      // U5a 语气词：纯口头噪声（嗯嗯/哈哈/唔）不上云，但**继续聆听**（回续问窗，不踢回待机）
      if (isFiller(t)) { this.onMetric('filler_dismissed'); this._dismiss(); return }
      // U5a 短语音噪声：说话过短 + 字数极少（误触/杂音）→ 继续聆听
      if (this._currentUtteranceMs() < 300 && graphemeLen(t) <= 2) { this.onMetric('filler_dismissed'); this._dismiss(); return }
      // 旧 D5-2 dismissMinChars 短句兜底 → 继续聆听
      if (graphemeLen(t) < this.cfg.dismissMinChars) { this._dismiss(); return }
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
    const voice = { source: this._listenSource || 'followup', utteranceMs: Math.round(this._currentUtteranceMs()) }
    if (this._held) {
      // 语义打断：旧回答还压着——先问这句是不是对助手说的，答案回来再决定停不停。
      // 确认条可见时不判（D5-2 红线：一切定稿照发上云），照旧停旧回答直接发。
      if (!this._needConfirm) { this._startJudge(text, voice); return }
      this._stopHeld()
    }
    this.onSend(text, voice)
    this._gotoThinking()
  }

  // ─── 语义打断（2026-10-11，设计 docs/design/2026-10-11-semantic-barge-in.md）───
  // 判定期间状态机在 THINKING（ASR 已关、旧回答压低在播）；ttsStart 不迁移、ttsEnd 只记「旧回答播完了」。
  _startJudge(text, voice) {
    const token = ++this._judgeSeq
    this._judge = { token, text, voice }
    this._gotoThinking()
    this._setTimer('judge', this.cfg.bargeInJudgeMs, () => this._judged(token, null))
    try {
      this.onJudgeBargeIn(text, this._ttsText, (verdict) => this._judged(token, verdict))
    } catch {
      this._judged(token, null)
    }
  }

  _judged(token, verdict) {
    const judge = this._judge
    if (!judge || judge.token !== token) return // 过期：期间被唤醒词 / 停播 / 拆机作废了
    this._judge = null
    this._clearTimer('judge')
    if (this.state !== VoiceState.THINKING || !this._held) return
    if (verdict === false) {
      // 不是对助手说的：恢复音量接着播，这次对话不结束；判定期间旧回答已经播完 → 同续问窗拒识，结束这次对话
      this.onMetric('barge_in_rejected')
      this._releaseHeld('armed')
      return
    }
    // 受话 ⇒ 停旧回答、发出这一句（带「已判过」，云端不再判第二遍）；判不出 ⇒ 照今天的路径发出，云端照旧判——失灵不拦人
    this.onMetric(verdict === true ? 'barge_in_admitted' : 'barge_in_unjudged')
    this._stopHeld()
    this.onSend(judge.text, verdict === true ? { ...judge.voice, admitted: true } : judge.voice)
    this._setTimer('thinking', this.cfg.thinkingMaxMs, () => {
      if (this.state === VoiceState.THINKING) this._gotoFollowup()
    })
  }

  // 放回旧回答：恢复音量；还在播 → 回 SPEAKING（播完照常进续问窗）；压着期间已播完 → endedTo（'followup' / 'armed'）
  _releaseHeld(endedTo) {
    const ended = this._heldEnded
    this._held = false
    this._heldEnded = false
    this.onDuckTts(false)
    this._clearPending()
    this._closeAsr()
    if (!ended) { this._gotoSpeaking(); return }
    if (endedTo === 'armed') { this.onMetric('rejected_session_end'); this._gotoArmed(); return }
    this._gotoFollowup()
  }

  // 停掉压着的旧回答：先停、后放手——Android 的停播会同步回调 ttsEnd，放手前到达的那一下归旧回答，
  // 不会把状态机推进续问窗。恢复音量放在最后：下一段播报（新一轮的回答）按原音量出声。
  _stopHeld() {
    if (!this._held) return
    this.onStopTts()
    this._held = false
    this._heldEnded = false
    this.onDuckTts(false)
  }

  // 离开整个打断判定（回待机 / 拆机）：作废在等的判定；旧回答若还压着，恢复音量（停不停由调用方负责）
  _dropHold() {
    this._judge = null
    if (!this._held) return
    this._held = false
    this._heldEnded = false
    this.onDuckTts(false)
  }

  // 本地丢掉这一句（空 / 语气词 / 短噪声）：语义打断里旧回答还压着 → 放回原音量接着播；否则回续问窗的剩余时间
  _dismiss() {
    if (this._held) this._releaseHeld('followup')
    else this._backToFollowup()
  }

  // 退出/dismiss 合并词表（去尾语气词后精确匹配，见 utteranceHeuristics.matchExitWord）
  _exitDismissWords() {
    return [...(this.cfg.exitWords || []), ...(this.cfg.dismissWords || [])]
  }

  // 本轮已结束 speech 段累计 + 进行中段（用 _speechActive 作是否在段内的权威判据，避免 now()=0 歧义）
  _currentUtteranceMs() {
    return this._utteranceMs + (this._speechActive && this._speechStartAt !== null ? this.now() - this._speechStartAt : 0)
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
    if (this._held) return // 压着的是旧回答：它的分段起播不代表新一轮开始说话
    if (this.state === VoiceState.THINKING) this._gotoSpeaking()
  }
  // 助手回复播完，或无音频时（TTS 关/纯文本）由 App 在 final 到达时调用 → 收窗进 FOLLOWUP。
  ttsEnd() {
    if (this._held) { this._heldEnded = true; return } // 语义打断期间旧回答播完：记一笔，等判定决定去向
    if (this.state === VoiceState.SPEAKING || this.state === VoiceState.THINKING) {
      this._gotoFollowup()
    }
  }
  // 云端判这一句「不是对助手说的」（静默拒识，2026-10-10）：结束这次连续对话，回 ARMED（只听唤醒词），不开续问窗。
  // 拒识轮没有播报，App 照旧会补一次 turnEnded/ttsEnd——在它之前调本入口，之后那一下在 ARMED 里是空操作。
  // 此前拒识轮按「无声终态」收尾 ⇒ THINKING → FOLLOWUP 重新计 8 秒：乘客在聊天时一句接一句被收进来、上云、再开窗。
  // LISTENING 里不动：那是用户已经又开口了（唤醒词抢进来的新一轮），旧轮的拒识不能把它收掉。
  turnRejected() {
    if (this._judge) return // 插话还在判定：那句没发出去，这个拒识不是它的
    if (this.state === VoiceState.THINKING || this.state === VoiceState.SPEAKING || this.state === VoiceState.FOLLOWUP) {
      this.onMetric('rejected_session_end')
      this._gotoArmed()
    }
  }
  // 用户主动停播（AR03 / 评审 R06）：SPEAKING/THINKING → **ARMED**，与 ttsEnd 的唯一差别是不开续问窗。
  // 「停止」这个动作里没有「接着说」的意思——FOLLOWUP 的 8s 窗口意味着下一句不用唤醒词就上行 ASR，
  // 把它当成停播的副产品就是隐式开采集。刻意不复用 handsFreeOff→On（recycle 那条路会顺带复位
  // 会话级 _bargeInDisabled 与自触发计数，那是「重新开启插话」的语义，不该被一次停播做掉）。
  stopSpeaking() {
    if (this._held) {
      // 语义打断期间（收音中 / 判定中）按停：作废判定、停掉压着的旧回答
      this._judge = null
      this._stopHeld()
      this._gotoArmed()
      return
    }
    if (this.state === VoiceState.SPEAKING || this.state === VoiceState.THINKING) {
      this._gotoArmed()
    }
  }
  // 系统抢占（来电 / 闹钟 / 别的 App 抢焦点；2026-09-21 G-07）：非 IDLE 的任何态都回 ARMED。
  // 播报 / 处理中同 stopSpeaking；**收音与续问窗放弃**（关 ASR 不定稿、不发）——铃声 / 通话把这一句毁了，
  // 发上去只是噪声，且 FOLLOWUP 的 8s 免唤醒窗在来电时开着等于隐式采集。ARMED 本身不动（麦归 KWS，不上行）。
  // 与 handsFreeOff→On（recycle）的分界：不复位会话级护栏（自触发计数 / _bargeInDisabled），这不是「重新开启插话」。
  systemInterrupt() {
    if (this.state === VoiceState.IDLE || this.state === VoiceState.ARMED) return
    if (this.state === VoiceState.LISTENING || this.state === VoiceState.FOLLOWUP) this.onMetric('system_interrupt_capture')
    this._gotoArmed()
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
    if (this.onJudgeBargeIn && this.holdBargeIn()) {
      // 语义打断：先压低、不停播，听完这句、问过是不是对助手说的再决定（乘客一句话不再掐断回答）
      this._held = true
      this._heldEnded = false
      this.onDuckTts(true)
    } else {
      this.onStopTts()
    }
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
    return isTtsEcho(t, this._ttsText)
  }

  // ─── 供 UI / 测试读取 ───
  get orbState() {
    return ORB_FOR[this.state]
  }
  get bargeInDisabled() {
    return this._bargeInDisabled
  }
}
