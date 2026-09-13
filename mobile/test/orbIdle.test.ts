// 光球空闲静置（2026-09-13）：表在 core/presence/orbIdle.ts，判据在 orbPolicy.orbStill。
// 三条要钉住：到点翻 still；任何 touch 立即恢复并重新起表；只有 idle / armed 会静置，听 / 想 / 说不会。
import { IdleClock, ORB_IDLE_STILL_MS } from '@/core/presence/orbIdle'
import { composerOrbAnimated, orbStill, orbTempo } from '@/core/presence/orbPolicy'

function fakeTimers() {
  const q: { fn: () => void; at: number }[] = []
  let now = 0
  return {
    timers: {
      set: (fn: () => void, ms: number) => {
        const t = { fn, at: now + ms }
        q.push(t)
        return t
      },
      clear: (id: unknown) => {
        const i = q.indexOf(id as { fn: () => void; at: number })
        if (i >= 0) q.splice(i, 1)
      },
    },
    advance(ms: number) {
      now += ms
      for (const t of q.slice().sort((a, b) => a.at - b.at)) {
        if (t.at <= now) {
          q.splice(q.indexOf(t), 1)
          t.fn()
        }
      }
    },
  }
}

describe('IdleClock', () => {
  test('起表后 ORB_IDLE_STILL_MS 到点翻 still，并通知订阅者', () => {
    const ft = fakeTimers()
    const clock = new IdleClock(ORB_IDLE_STILL_MS, ft.timers)
    const seen: boolean[] = []
    clock.subscribe(() => seen.push(clock.isStill))
    clock.touch()
    ft.advance(ORB_IDLE_STILL_MS - 1)
    expect(clock.isStill).toBe(false)
    ft.advance(1)
    expect(clock.isStill).toBe(true)
    expect(seen).toEqual([true])
  })

  test('touch 立即恢复并重新起表：静置后一碰就动，再过一整段才再停', () => {
    const ft = fakeTimers()
    const clock = new IdleClock(1000, ft.timers)
    clock.touch()
    ft.advance(1000)
    expect(clock.isStill).toBe(true)
    clock.touch()
    expect(clock.isStill).toBe(false) // 同步恢复，不等下一帧
    ft.advance(999)
    expect(clock.isStill).toBe(false)
    ft.advance(1)
    expect(clock.isStill).toBe(true)
  })

  test('连续 touch 只保留最后一只表（不会因为早先那只到点而提前静置）', () => {
    const ft = fakeTimers()
    const clock = new IdleClock(1000, ft.timers)
    clock.touch()
    ft.advance(600)
    clock.touch()
    ft.advance(600) // 第一只表的 1000ms 早就过了
    expect(clock.isStill).toBe(false)
    ft.advance(400)
    expect(clock.isStill).toBe(true)
  })

  test('dispose 后不再翻 still', () => {
    const ft = fakeTimers()
    const clock = new IdleClock(1000, ft.timers)
    clock.touch()
    clock.dispose()
    ft.advance(2000)
    expect(clock.isStill).toBe(false)
  })
})

describe('orbPolicy × 空闲静置', () => {
  const still = { reduceMotion: false, idleStill: true }
  const live = { reduceMotion: false, idleStill: false }
  test('只有 idle / armed 会静置；听 / 想 / 说 / 等确认 / 看一眼永远照动', () => {
    for (const primary of ['idle', 'armed'] as const) {
      expect(orbStill({ primary }, still)).toBe(true)
      expect(orbTempo({ driving: false, primary }, still)).toBe('static')
      expect(composerOrbAnimated({ input: 'composer', primary }, still)).toBe(false)
    }
    for (const primary of ['listening', 'thinking', 'speaking', 'attention', 'looking'] as const) {
      expect(orbStill({ primary }, still)).toBe(false)
      expect(orbTempo({ driving: false, primary }, still)).toBe('loop')
      expect(composerOrbAnimated({ input: 'composer', primary }, still)).toBe(true)
    }
  })
  test('表没翻时一切照旧（缺省 idleStill 也一样）', () => {
    expect(orbTempo({ driving: false, primary: 'idle' }, live)).toBe('loop')
    expect(orbTempo({ driving: false, primary: 'idle' }, { reduceMotion: false })).toBe('loop')
    expect(composerOrbAnimated({ input: 'composer', primary: 'idle' }, live)).toBe(true)
  })
  test('行车档下静置同样成立（停比慢更省），静置 > 行车 ×0.5', () => {
    expect(orbTempo({ driving: true, primary: 'idle' }, still)).toBe('static')
    expect(orbTempo({ driving: true, primary: 'speaking' }, still)).toBe('slow')
  })
})
