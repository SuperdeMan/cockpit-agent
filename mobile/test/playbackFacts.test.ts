// 播放事实（AR03 / 评审 R06）：owner 集合的语义与 captureFacts 逐字同构——
// **旧一路收尾不能清掉另一路的事实**，否则「还有声音在放」这件事会在多路并存时读错。
import {
  getAudioPlaybackCounters,
  getAudioPlaybackSnapshot,
  setAudioPlaybackFact,
  subscribeAudioPlayback,
} from '@/core/voice/playbackFacts'

const owners: object[] = []
const own = () => {
  const o = {}
  owners.push(o)
  return o
}

afterEach(() => {
  for (const o of owners) setAudioPlaybackFact(o, false)
  owners.length = 0
})

test('单路播放：起播置真、停播置假，订阅者各收到一次', () => {
  const seen: boolean[] = []
  const stop = subscribeAudioPlayback(() => seen.push(getAudioPlaybackSnapshot().playing))
  const a = own()
  setAudioPlaybackFact(a, true)
  expect(getAudioPlaybackSnapshot().playing).toBe(true)
  setAudioPlaybackFact(a, false)
  expect(getAudioPlaybackSnapshot().playing).toBe(false)
  stop()
  expect(seen).toEqual([true, false])
})

test('两路并存：先起的那路收尾不把事实清掉（S2S 段与主链段叠在一起的形态）', () => {
  const a = own()
  const b = own()
  setAudioPlaybackFact(a, true)
  setAudioPlaybackFact(b, true)
  setAudioPlaybackFact(a, false)
  expect(getAudioPlaybackSnapshot().playing).toBe(true)
  setAudioPlaybackFact(b, false)
  expect(getAudioPlaybackSnapshot().playing).toBe(false)
})

test('重复置同值是空操作：不发订阅通知、不记计数', () => {
  const before = { ...getAudioPlaybackCounters() }
  let notified = 0
  const stop = subscribeAudioPlayback(() => { notified += 1 })
  const a = own()
  setAudioPlaybackFact(a, true)
  setAudioPlaybackFact(a, true)
  setAudioPlaybackFact(a, true)
  stop()
  expect(notified).toBe(1)
  expect(getAudioPlaybackCounters().starts).toBe(before.starts + 1)
  expect(getAudioPlaybackCounters().stops).toBe(before.stops)
})

test('从未置真的 owner 置假不记 stop 计数（迟到回调不许伪造一次收尾）', () => {
  const before = { ...getAudioPlaybackCounters() }
  setAudioPlaybackFact(own(), false)
  expect(getAudioPlaybackCounters()).toEqual(before)
})
