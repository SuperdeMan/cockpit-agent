import test from 'node:test'
import assert from 'node:assert/strict'

import { VoiceLoop, VoiceState } from './voiceLoop.mjs'

// ─── fake clock + 事件记录：把时间源/定时器/效果全部注入，回路纯逻辑可完全离线断言 ───
function makeHarness(config = {}) {
  const events = []
  const rec = (name) => (...args) => events.push([name, ...args])

  let timeNow = 0
  let seq = 0
  const timers = new Map() // id -> { fireAt, fn }
  const setTimer = (fn, ms) => {
    const id = ++seq
    timers.set(id, { fireAt: timeNow + ms, fn })
    return id
  }
  const clearTimer = (id) => timers.delete(id)
  const advance = (ms) => {
    const target = timeNow + ms
    // 按时间顺序逐个触发（触发中新增/取消的定时器每轮重扫，处理重入）
    for (;;) {
      let pick = null
      for (const [id, t] of timers) {
        if (t.fireAt <= target && (!pick || t.fireAt < pick.fireAt || (t.fireAt === pick.fireAt && id < pick.id))) {
          pick = { id, ...t }
        }
      }
      if (!pick) break
      timers.delete(pick.id)
      timeNow = pick.fireAt
      pick.fn()
    }
    timeNow = target
  }

  const vl = new VoiceLoop({
    now: () => timeNow,
    setTimer,
    clearTimer,
    onState: rec('state'),
    onOpenAsr: rec('openAsr'),
    onCloseAsr: rec('closeAsr'),
    onEndpoint: rec('endpoint'),
    onSend: rec('send'),
    onStopTts: rec('stopTts'),
    onWakeChime: rec('chime'),
    onDisableBargeIn: rec('disableBargeIn'),
    onExitAck: rec('exitAck'),
    onCancelTurn: rec('cancelTurn'),
    onMetric: rec('metric'),
    config,
  })

  const count = (name) => events.filter((e) => e[0] === name).length
  const last = (name) => [...events].reverse().find((e) => e[0] === name)
  return { vl, events, advance, count, last }
}

// 常用推进：把回路带到 SPEAKING 态（wake→说话→定稿→播报开始）
function driveToSpeaking(h, { final = '放首歌', tts = '好的，正在为您播放音乐' } = {}) {
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.vadSpeechEnd()
  h.vl.asrFinal(final)
  h.vl.ttsStart()
  h.vl.setTtsText(tts)
}

test('IDLE⇄ARMED：hands-free 开进待机、关拆机', () => {
  const h = makeHarness()
  assert.equal(h.vl.state, VoiceState.IDLE)
  h.vl.handsFreeOn()
  assert.equal(h.vl.state, VoiceState.ARMED)
  h.vl.handsFreeOff()
  assert.equal(h.vl.state, VoiceState.IDLE)
})

test('wake：ARMED→LISTENING，唤醒音效 + 开 ASR 流', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  assert.equal(h.vl.state, VoiceState.LISTENING)
  assert.equal(h.count('chime'), 1)
  assert.equal(h.count('openAsr'), 1)
  assert.equal(h.count('endpoint'), 0)
})

test('全链路：wake→说话→端点定稿→THINKING→SPEAKING→FOLLOWUP→免唤醒续问回 LISTENING', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.vadSpeechEnd()
  assert.equal(h.count('endpoint'), 1) // VAD 端点 → 请定稿
  h.vl.asrFinal('今天杭州天气怎么样')
  assert.equal(h.last('send')[1], '今天杭州天气怎么样')
  assert.equal(h.vl.state, VoiceState.THINKING)
  assert.equal(h.count('closeAsr'), 1)

  h.vl.ttsStart()
  assert.equal(h.vl.state, VoiceState.SPEAKING)
  h.vl.ttsEnd()
  assert.equal(h.vl.state, VoiceState.FOLLOWUP)

  h.vl.vadSpeechStart() // 免唤醒续问：无需再喊唤醒词
  assert.equal(h.vl.state, VoiceState.LISTENING)
  assert.equal(h.count('openAsr'), 2) // 唤醒开一次 + 续问开一次
  assert.equal(h.count('chime'), 1)   // 续问不再响唤醒音
})

test('FOLLOWUP 超时（默认 8s）无接话 → 回 ARMED', () => {
  const h = makeHarness()
  driveToSpeaking(h)
  h.vl.ttsEnd()
  assert.equal(h.vl.state, VoiceState.FOLLOWUP)
  h.advance(7999)
  assert.equal(h.vl.state, VoiceState.FOLLOWUP)
  h.advance(1)
  assert.equal(h.vl.state, VoiceState.ARMED)
})

test('D5-1 误唤醒静默回收：唤醒后 5s 无 speech → 回 ARMED，不发请求', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.advance(5000)
  assert.equal(h.vl.state, VoiceState.ARMED)
  assert.equal(h.count('send'), 0)
  assert.equal(h.count('closeAsr'), 1)
})

test('误唤醒回收窗被真实开口撤销：5s 后仍在 LISTENING', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(5000)
  assert.equal(h.vl.state, VoiceState.LISTENING)
})

test('D5-2 dismiss：定稿不足 2 字 → 不上云，但继续聆听（FOLLOWUP，不踢回待机）', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('嗯')
  assert.equal(h.count('send'), 0)
  assert.equal(h.vl.state, VoiceState.FOLLOWUP) // P4：语气词不退出聆听，进续问窗
})

test('D5-2 dismiss：命中词表「没事」→ 本地丢弃', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('没事')
  assert.equal(h.count('send'), 0)
  assert.equal(h.vl.state, VoiceState.ARMED)
})

test('D5-2 例外：确认条可见时，本会被 dismiss 的定稿也必须上云（裸「取消」走 F1）', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.setNeedConfirm(true) // HMI 有挂起确认条
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('取消')
  assert.equal(h.last('send')[1], '取消')
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('D5-2 例外对照：无确认条时同样的短句「不」被本地 dismiss', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.setNeedConfirm(false)
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('不') // 1 字，<dismissMinChars
  assert.equal(h.count('send'), 0)
  assert.equal(h.vl.state, VoiceState.FOLLOWUP) // P4：没说清不上云但继续聆听
  // 确认条可见时同一短句必须放行（wake 从 FOLLOWUP 也进 LISTENING）
  h.vl.setNeedConfirm(true)
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('不')
  assert.equal(h.last('send')[1], '不')
})

test('D6 barge-in：SPEAKING 态持续开口 ≥300ms → 停播报转 LISTENING', () => {
  const h = makeHarness()
  driveToSpeaking(h)
  assert.equal(h.vl.state, VoiceState.SPEAKING)
  h.vl.vadSpeechStart()
  h.advance(300)
  assert.equal(h.count('stopTts'), 1)
  assert.equal(h.vl.state, VoiceState.LISTENING)
})

test('D6 回声指纹：SPEAKING 期 partial 命中播报文本 → 不打断（判为自触发）', () => {
  const h = makeHarness()
  driveToSpeaking(h, { tts: '今天杭州晴，26 度' })
  h.vl.vadSpeechStart()
  h.vl.asrPartial('今天杭州') // ⊂ 正在播的 TTS 文本
  h.advance(300)
  assert.equal(h.count('stopTts'), 0)
  assert.equal(h.vl.state, VoiceState.SPEAKING)
})

test('D6 护栏：开口不足 300ms 即结束 → 不打断', () => {
  const h = makeHarness()
  driveToSpeaking(h)
  h.vl.vadSpeechStart()
  h.advance(100)
  h.vl.vadSpeechEnd() // 200ms 内结束
  h.advance(300)
  assert.equal(h.count('stopTts'), 0)
  assert.equal(h.vl.state, VoiceState.SPEAKING)
})

test('D6 连续 2 次疑似自触发 → 本会话关闭 L3，后续 SPEAKING 开口不再打断', () => {
  const h = makeHarness()
  driveToSpeaking(h, { tts: '正在为您导航到首都机场' })
  // 第 1 次回声自触发
  h.vl.vadSpeechStart()
  h.vl.asrPartial('正在为您导航')
  h.advance(300)
  // 第 2 次回声自触发
  h.vl.vadSpeechStart()
  h.vl.asrPartial('到首都机场')
  h.advance(300)
  assert.equal(h.count('disableBargeIn'), 1)
  assert.equal(h.vl.bargeInDisabled, true)
  // L3 已关：真实持续开口也不再打断
  h.vl.vadSpeechStart()
  h.advance(300)
  assert.equal(h.count('stopTts'), 0)
  assert.equal(h.vl.state, VoiceState.SPEAKING)
})

test('barge-in 后真实定稿：复位自触发计数并正常转 THINKING', () => {
  const h = makeHarness()
  driveToSpeaking(h, { tts: '好的，正在为您播放音乐' })
  h.vl.vadSpeechStart()
  h.advance(300)
  assert.equal(h.vl.state, VoiceState.LISTENING) // 打断成功
  h.vl.asrFinal('改成周杰伦的歌')
  assert.equal(h.last('send')[1], '改成周杰伦的歌')
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('无音频回复（TTS 关/纯文本）：App 在 final 到达时调 ttsEnd，THINKING→FOLLOWUP', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('讲个笑话')
  assert.equal(h.vl.state, VoiceState.THINKING)
  h.vl.ttsEnd() // 无 ttsStart
  assert.equal(h.vl.state, VoiceState.FOLLOWUP)
})

test('R4.3b P0：THINKING 安全超时兜底——App 未回调 ttsEnd 也不永久卡死，超时回 FOLLOWUP', () => {
  const h = makeHarness({ thinkingMaxMs: 100000 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('查一下明天的天气')
  assert.equal(h.vl.state, VoiceState.THINKING)
  h.advance(99999)
  assert.equal(h.vl.state, VoiceState.THINKING) // 未到点，仍在处理
  h.advance(1)
  assert.equal(h.vl.state, VoiceState.FOLLOWUP) // 兜底触发，回可交互态（不永久全聋）
})

test('R4.3b P0：ttsStart 进 SPEAKING 清 THINKING 超时（正路播报不被兜底打回）', () => {
  const h = makeHarness({ thinkingMaxMs: 5000 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('讲个笑话')
  h.vl.ttsStart() // 播报开始 → SPEAKING，超时定时器随 _clearAllTimers 清除
  assert.equal(h.vl.state, VoiceState.SPEAKING)
  h.advance(6000) // 超过 thinkingMaxMs
  assert.equal(h.vl.state, VoiceState.SPEAKING) // 未被超时兜底打回 FOLLOWUP
})

test('hands-free 中途关闭（任意态）→ IDLE 拆机 + 关 ASR + 清定时器', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake() // LISTENING，ASR 已开
  h.vl.handsFreeOff()
  assert.equal(h.vl.state, VoiceState.IDLE)
  assert.equal(h.count('closeAsr'), 1)
  // 定时器已清：即便时间前进也不再触发误唤醒回收之类
  const stateCountBefore = h.count('state')
  h.advance(10000)
  assert.equal(h.count('state'), stateCountBefore)
})

test('配置注入：followupWindowMs / falseWakeMs 生效', () => {
  const h = makeHarness({ followupWindowMs: 3000, falseWakeMs: 2000 })
  // 误唤醒回收窗缩短到 2s
  h.vl.handsFreeOn()
  h.vl.wake()
  h.advance(1999)
  assert.equal(h.vl.state, VoiceState.LISTENING)
  h.advance(1)
  assert.equal(h.vl.state, VoiceState.ARMED)
  // FOLLOWUP 窗缩短到 3s
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('放首歌')
  h.vl.ttsStart()
  h.vl.ttsEnd()
  assert.equal(h.vl.state, VoiceState.FOLLOWUP)
  h.advance(3000)
  assert.equal(h.vl.state, VoiceState.ARMED)
})

test('唤醒词打断：SPEAKING 态喊唤醒词 → 停播报直接进 LISTENING', () => {
  const h = makeHarness()
  driveToSpeaking(h)
  h.vl.wake()
  assert.equal(h.count('stopTts'), 1)
  assert.equal(h.vl.state, VoiceState.LISTENING)
})

// ─── R4.3b P1：退出词 / 语气词 / 短语音 / 端点宽限合并 ───

test('U3 退出词：「退下吧」→ 退场应答 + 回 ARMED，不上云', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('退下吧')
  assert.equal(h.count('send'), 0)
  assert.equal(h.count('exitAck'), 1)
  assert.equal(h.vl.state, VoiceState.ARMED)
})

test('U3 退出词并入 dismiss：「没事了」去尾「了」命中「没事」→ 退场，不上云', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('没事了')
  assert.equal(h.count('send'), 0)
  assert.equal(h.vl.state, VoiceState.ARMED)
})

test('U3 退出词例外：确认条可见时「退下」照发上云（走 F1，不本地退场）', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.setNeedConfirm(true)
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('退下')
  assert.equal(h.last('send')[1], '退下')
  assert.equal(h.count('exitAck'), 0)
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('U5a 语气词：「嗯嗯」「哈哈」「啊」不上云，但继续聆听（P4 不退出聆听态）', () => {
  for (const w of ['嗯嗯', '哈哈', '啊']) {
    const h = makeHarness()
    h.vl.handsFreeOn()
    h.vl.wake()
    h.vl.vadSpeechStart()
    h.advance(200)
    h.vl.vadSpeechEnd()
    h.vl.asrFinal(w)
    assert.equal(h.count('send'), 0, `${w} 不应上云`)
    assert.equal(h.vl.state, VoiceState.FOLLOWUP, `${w} 应进续问窗继续聆听`)
  }
})

test('P4：说语气词后可直接接着说正事，不需重新唤醒（filler→FOLLOWUP→接话→LISTENING→send）', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('嗯') // 语气词 → 继续聆听
  assert.equal(h.vl.state, VoiceState.FOLLOWUP)
  assert.equal(h.count('chime'), 1) // 未重新唤醒（唤醒音仍只 1 次）
  h.vl.vadSpeechStart() // 直接接着说
  assert.equal(h.vl.state, VoiceState.LISTENING)
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('帮我查明天的天气')
  assert.equal(h.last('send')[1], '帮我查明天的天气')
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('P4：退出词仍回待机（ARMED），与语气词的继续聆听区分', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('退下吧')
  assert.equal(h.count('exitAck'), 1)
  assert.equal(h.vl.state, VoiceState.ARMED) // 退出意图 → 回待机（区别于 filler）
})

test('U5b 完整句直发不进宽限（「打开空调26度」即刻发送）', () => {
  const h = makeHarness({ endpointGraceMs: 700 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(500)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('打开空调26度')
  assert.equal(h.last('send')[1], '打开空调26度')
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('U5b 端点宽限合并：悬挂「导航去」停顿后续说「西溪湿地」→ 合并为一次请求', () => {
  const h = makeHarness({ endpointGraceMs: 700 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(300)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('导航去') // 悬挂结尾 → 进宽限，不立即发
  assert.equal(h.count('send'), 0)
  assert.equal(h.vl.state, VoiceState.LISTENING) // 仍在聆听（宽限微态）
  h.advance(300) // 宽限内续说
  h.vl.vadSpeechStart()
  assert.equal(h.count('openAsr'), 2) // 重开 ASR 接续
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('西溪湿地')
  assert.equal(h.last('send')[1], '导航去西溪湿地')
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('U5b 宽限满无续说 → 送出原文进 THINKING', () => {
  const h = makeHarness({ endpointGraceMs: 700 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(300)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('导航去')
  assert.equal(h.count('send'), 0)
  h.advance(700) // 宽限满
  assert.equal(h.last('send')[1], '导航去')
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('U5b qwen3 提前定稿：悬挂结尾 + VAD 仍在 speech → 零等待续说拼接', () => {
  const h = makeHarness({ endpointGraceMs: 700 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(300)
  // 不 vadSpeechEnd（VAD 仍在 speech）→ qwen3 服务端 800ms 提前 final
  h.vl.asrFinal('帮我导航去') // 悬挂 + speechActive → 立即续说
  assert.equal(h.count('send'), 0)
  assert.equal(h.vl.state, VoiceState.LISTENING)
  assert.equal(h.count('openAsr'), 2)
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('西湖')
  assert.equal(h.last('send')[1], '帮我导航去西湖')
})

test('U5b endpointGraceMs=0 关闭合并：悬挂结尾也即刻发送（不拖延）', () => {
  const h = makeHarness({ endpointGraceMs: 0 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(300)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('导航去')
  assert.equal(h.last('send')[1], '导航去') // 无宽限，直发
  assert.equal(h.vl.state, VoiceState.THINKING)
})

test('U5b 宽限中拆机（handsFreeOff）→ 清待发前缀，grace 定时器不再触发', () => {
  const h = makeHarness({ endpointGraceMs: 700 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(300)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('导航去') // 宽限
  h.vl.handsFreeOff()
  assert.equal(h.vl.state, VoiceState.IDLE)
  h.advance(1000) // grace 已随拆机清除
  assert.equal(h.count('send'), 0)
})

test('U2 THINKING 真打断：处理中喊唤醒词 → 取消在飞轮 + 提示音 + 重新聆听', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('帮我查一个很复杂的问题吗') // 完整句 → 送出进 THINKING
  assert.equal(h.vl.state, VoiceState.THINKING)
  h.vl.wake() // 处理中喊「小舟小舟」
  assert.equal(h.count('cancelTurn'), 1) // 请求取消在飞的云端处理
  assert.equal(h.count('chime'), 2)      // 唤醒 + 打断各一次
  assert.equal(h.vl.state, VoiceState.LISTENING) // 重新聆听
})

test('U2 THINKING 期 VAD speech 不打断（仅唤醒词可打断，防环境音误触）', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(400)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('讲个笑话')
  assert.equal(h.vl.state, VoiceState.THINKING)
  h.vl.vadSpeechStart() // THINKING 期的 speech（环境音）
  h.advance(500)
  assert.equal(h.count('cancelTurn'), 0)
  assert.equal(h.vl.state, VoiceState.THINKING) // 岿然不动
})

test('P3 obs 指标：wake/filler/exit/merge/barge_in/cancel 各在语义点发出', () => {
  const metrics = (h) => h.events.filter((e) => e[0] === 'metric').map((e) => e[1])
  // wake
  let h = makeHarness()
  h.vl.handsFreeOn(); h.vl.wake()
  assert.ok(metrics(h).includes('wake'))
  // filler_dismissed
  h = makeHarness()
  h.vl.handsFreeOn(); h.vl.wake(); h.vl.vadSpeechStart(); h.vl.vadSpeechEnd(); h.vl.asrFinal('嗯嗯')
  assert.ok(metrics(h).includes('filler_dismissed'))
  // exit_word
  h = makeHarness()
  h.vl.handsFreeOn(); h.vl.wake(); h.vl.vadSpeechStart(); h.advance(400); h.vl.vadSpeechEnd(); h.vl.asrFinal('退下吧')
  assert.ok(metrics(h).includes('exit_word'))
  // endpoint_merge（宽限续说）
  h = makeHarness({ endpointGraceMs: 700 })
  h.vl.handsFreeOn(); h.vl.wake(); h.vl.vadSpeechStart(); h.advance(300); h.vl.vadSpeechEnd(); h.vl.asrFinal('导航去')
  h.advance(200); h.vl.vadSpeechStart()
  assert.ok(metrics(h).includes('endpoint_merge'))
  // turn_cancelled（THINKING 打断）
  h = makeHarness()
  h.vl.handsFreeOn(); h.vl.wake(); h.vl.vadSpeechStart(); h.advance(400); h.vl.vadSpeechEnd(); h.vl.asrFinal('讲个笑话')
  h.vl.wake()
  assert.ok(metrics(h).includes('turn_cancelled'))
})

test('orbState 映射：各态对应 AuroraOrb 视觉态', () => {
  const h = makeHarness()
  assert.equal(h.vl.orbState, 'idle')
  h.vl.handsFreeOn()
  assert.equal(h.vl.orbState, 'armed')
  h.vl.wake()
  assert.equal(h.vl.orbState, 'listening')
  h.vl.vadSpeechStart()
  h.vl.asrFinal('放首歌')
  assert.equal(h.vl.orbState, 'thinking')
  h.vl.ttsStart()
  assert.equal(h.vl.orbState, 'speaking')
  h.vl.ttsEnd()
  assert.equal(h.vl.orbState, 'listening') // FOLLOWUP 用邀请式聆听辉光
})

// ─── R4.4 P0：定稿 onSend 第二参带 hands-free 来源（供云端拒识判定）───
test('R4.4：wake 进入定稿 onSend 带 source=wake', () => {
  const h = makeHarness({ endpointGraceMs: 0 })
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.advance(500)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('导航去西溪湿地')
  const e = h.last('send')
  assert.equal(e[1], '导航去西溪湿地')
  assert.equal(e[2].source, 'wake')
})

test('R4.4：FOLLOWUP 续问定稿 onSend 带 source=followup', () => {
  const h = makeHarness({ endpointGraceMs: 0 })
  driveToSpeaking(h)           // wake→定稿(source=wake)→SPEAKING
  h.vl.ttsEnd()                // SPEAKING → FOLLOWUP
  h.vl.vadSpeechStart()        // 免唤醒追问 → LISTENING(source=followup)
  h.advance(500)
  h.vl.vadSpeechEnd()
  h.vl.asrFinal('那再放一首')
  assert.equal(h.last('send')[2].source, 'followup')
})

test('R4.4：barge-in 打断后定稿 onSend 带 source=bargein 且 utteranceMs>0', () => {
  const h = makeHarness({ endpointGraceMs: 0 })
  driveToSpeaking(h)           // SPEAKING
  h.vl.vadSpeechStart()        // SPEAKING 起 barge-in 计时
  h.advance(400)               // 过 bargeInMinMs(300) → _bargeInFire → LISTENING(source=bargein)
  h.advance(600)               // 模拟继续说
  h.vl.vadSpeechEnd()          // 累计 utteranceMs
  h.vl.asrFinal('等一下换一首')
  const e = h.last('send')
  assert.equal(e[2].source, 'bargein')
  assert.ok(e[2].utteranceMs > 0)
})
