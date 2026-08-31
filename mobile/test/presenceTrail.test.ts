// mobile/test/presenceTrail.test.ts
// 在场轨迹（方案 §11.5）：只在轴变化时记、每秒 tick 不刷屏；记变化的轴与变化的输入；环形 20；mark 打点。
import { derivePresence, type PresenceInput } from '@/core/presence/presence'
import { PresenceTrail } from '@/core/presence/presenceTrail'

const NOW = 5_000_000
function base(over: Partial<PresenceInput> = {}): PresenceInput {
  return {
    now: NOW, connStatus: 'open', connChangedAt: NOW - 60_000,
    hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', hfFsmChangedAt: NOW - 1000, ptt: 'idle', partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false, pendingOps: [], pendingLocation: false, voicePipeline: 'classic',
    visionCapturing: false, queued: 0, lastError: null, degradations: [], driving: false,
    identity: 'handheld', user: 'u1',
    ...over,
  }
}

test('只在轴变化时记；同快照每秒 tick 不刷屏；记下变化的轴与变化的输入', () => {
  let t = 0
  const trail = new PresenceTrail(20, () => (t += 1))
  const i1 = base()
  trail.record(i1, derivePresence(i1))
  expect(trail.list()).toHaveLength(1)
  const tick = base({ now: NOW + 1000 })
  trail.record(tick, derivePresence(tick)) // 只有 now 变了：不记
  expect(trail.list()).toHaveLength(1)
  const i2 = base({ now: NOW + 2000, ptt: 'recording' })
  trail.record(i2, derivePresence(i2))
  const top = trail.list()[0]
  expect(top.kind).toBe('snapshot')
  if (top.kind === 'snapshot') {
    expect(top.primary).toBe('listening')
    expect(top.input).toBe('voice-sheet')
    expect(top.changedAxes).toEqual(expect.arrayContaining(['capture', 'primary', 'input', 'capsule', 'privacy.mic']))
    expect(top.changedInputs).toEqual(['ptt'])
  }
})

test('环形 20 条，最新在前', () => {
  const trail = new PresenceTrail(20, () => 1)
  for (let k = 0; k < 25; k += 1) {
    const i = base({ ptt: k % 2 ? 'recording' : 'idle' })
    trail.record(i, derivePresence(i))
  }
  expect(trail.list()).toHaveLength(20)
})

test('mark：外设时刻打点（首反馈时延 = mark 到下一条 listening 快照的时间差）', () => {
  let t = 100
  const trail = new PresenceTrail(20, () => (t += 30))
  trail.mark('fsm:LISTENING')
  const i = base({ hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING' })
  trail.record(i, derivePresence(i))
  const [snap, mark] = trail.list()
  expect(mark).toEqual({ kind: 'mark', at: 130, label: 'fsm:LISTENING' })
  expect(snap.kind === 'snapshot' && snap.primary).toBe('listening')
  expect(snap.at - mark.at).toBe(30)
})

test('clear 清空并复位「上一次」：清空后第一条快照重新算作全轴变化', () => {
  const trail = new PresenceTrail(20, () => 1)
  const i = base()
  trail.record(i, derivePresence(i))
  trail.clear()
  expect(trail.list()).toHaveLength(0)
  trail.record(i, derivePresence(i))
  expect(trail.list()).toHaveLength(1)
})

test('B2-14 真机修正：record 在渲染期调用 → 订阅者不得在同一个同步段里被回调', async () => {
  // 真机实录（2026-08-31，logcat）：`Cannot update a component (PresenceTrailScreen)
  // while rendering a different component (ChatBody)` —— usePresence 在 ChatBody 渲染期
  // 调 record()，同步 notify() 就等于在渲染另一个组件时给轨迹屏 setState。
  // 数据仍要同步可读（record 是同步的，去重与顺序不变），只把「通知」推迟一个微任务。
  const calls: number[] = []
  const trail = new PresenceTrail(20, () => 1)
  trail.subscribe(() => calls.push(1))
  const i = base({ ptt: 'recording' })
  trail.record(i, derivePresence(i))
  expect(calls).toEqual([])
  expect(trail.list()).toHaveLength(1)
  await Promise.resolve()
  await Promise.resolve()
  expect(calls).toEqual([1])
})

test('B2-14 真机修正：同一个同步段里多次 push 只通知一次（渲染期不刷屏）', async () => {
  const calls: number[] = []
  const trail = new PresenceTrail(20, () => 1)
  trail.subscribe(() => calls.push(1))
  for (const ptt of ['recording', 'idle', 'recording'] as const) {
    const i = base({ ptt })
    trail.record(i, derivePresence(i))
  }
  expect(calls).toEqual([])
  await Promise.resolve()
  await Promise.resolve()
  expect(calls).toEqual([1])
  expect(trail.list()).toHaveLength(3)
})
