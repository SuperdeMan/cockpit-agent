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
  assert.deepEqual(h.last('send'), ['send', '今天杭州天气怎么样'])
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

test('D5-2 dismiss：定稿不足 2 字 → 本地丢弃回 ARMED，不上云', () => {
  const h = makeHarness()
  h.vl.handsFreeOn()
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('嗯')
  assert.equal(h.count('send'), 0)
  assert.equal(h.vl.state, VoiceState.ARMED)
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
  assert.deepEqual(h.last('send'), ['send', '取消'])
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
  assert.equal(h.vl.state, VoiceState.ARMED)
  // 确认条可见时同一短句必须放行
  h.vl.setNeedConfirm(true)
  h.vl.wake()
  h.vl.vadSpeechStart()
  h.vl.asrFinal('不')
  assert.deepEqual(h.last('send'), ['send', '不'])
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
  assert.deepEqual(h.last('send'), ['send', '改成周杰伦的歌'])
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
