// 发送前取坐标的策略（core/location/fixPolicy.ts）：缓存即用 → 只等预算 → 陈旧回落 → 没有就不带。
// 要钉住的是 2026-09-16 那个形态**不再可能**：缓存里有坐标却在发送前等满 20s。
import {
  FIX_BUDGET_MS,
  FRESH_FIX_MAX_AGE_MS,
  REFRESH_AFTER_MS,
  acquireFix,
  pickFreshest,
  withinBudget,
  type Fix,
  type FixDeps,
  type FixTimers,
} from '@/core/location/fixPolicy'

/** 手动推进的假定时器：到点的回调按 fire(ms) 触发；clear 记账供断言 */
class FakeTimers implements FixTimers {
  pending = new Map<number, { at: number; fn: () => void }>()
  cleared: number[] = []
  private seq = 0
  constructor(private readonly clock: { now: number }) {}
  set(fn: () => void, ms: number): unknown {
    const id = ++this.seq
    this.pending.set(id, { at: this.clock.now + ms, fn })
    return id
  }
  clear(id: unknown): void {
    this.cleared.push(id as number)
    this.pending.delete(id as number)
  }
  /** 时钟推进到 t，触发所有到点的定时器 */
  advanceTo(t: number): void {
    this.clock.now = t
    for (const [id, e] of [...this.pending]) {
      if (e.at <= t) {
        this.pending.delete(id)
        e.fn()
      }
    }
  }
}

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const flush = async (hops = 6) => {
  for (let i = 0; i < hops; i += 1) await Promise.resolve()
}

/** 所有用例的时钟起点；fixture 的时间戳相对它算，不引用还没解构出来的 clock */
const T0 = 1_000_000

const fix = (capturedAt: number, tag = 0): Fix => ({ lat: 22.5 + tag, lng: 113.9, accuracyM: 30, capturedAt })

function makeDeps(opts: {
  cached?: Fix | null
  stale?: Fix | null
  current?: () => Promise<Fix | null>
}) {
  const clock = { now: T0 }
  const timers = new FakeTimers(clock)
  const calls = { lastKnown: [] as (number | undefined)[], current: 0 }
  const deps: FixDeps = {
    lastKnown: async (maxAgeMs) => {
      calls.lastKnown.push(maxAgeMs)
      return maxAgeMs ? (opts.cached ?? null) : (opts.stale ?? null)
    },
    current: () => {
      calls.current += 1
      return opts.current ? opts.current() : new Promise<Fix | null>(() => {})
    },
    now: () => clock.now,
    timers,
  }
  return { deps, clock, timers, calls }
}

test('缓存里有 1 分钟内的坐标：立刻用，不现取、不等一毫秒、不要求补取', async () => {
  const { deps, calls } = makeDeps({ cached: fix(T0 - 30_000) })
  const r = await acquireFix(deps)
  expect(r.source).toBe('cached')
  expect(r.fix?.lat).toBe(22.5)
  expect(r.refresh).toBe(false)
  expect(r.waitedMs).toBe(0)
  expect(calls.current).toBe(0)
  expect(calls.lastKnown).toEqual([FRESH_FIX_MAX_AGE_MS])
})

test('缓存偏旧（超过 REFRESH_AFTER_MS 但在 FRESH_FIX_MAX_AGE_MS 内）：照用，但要求后台补取', async () => {
  const { deps } = makeDeps({ cached: fix(T0 - REFRESH_AFTER_MS - 1) })
  const r = await acquireFix(deps)
  expect(r.source).toBe('cached')
  expect(r.refresh).toBe(true)
})

test('缓存没有时间戳：当作不知道多旧，用它但要求补取', async () => {
  const { deps } = makeDeps({ cached: { lat: 1, lng: 2 } })
  const r = await acquireFix(deps)
  expect(r.source).toBe('cached')
  expect(r.refresh).toBe(true)
})

test('无缓存、新定位在预算内到达：用新的，定时器被清、不再查陈旧缓存', async () => {
  const d = deferred<Fix | null>()
  const { deps, clock, timers, calls } = makeDeps({ current: () => d.promise, stale: fix(0) })
  const p = acquireFix(deps)
  await flush()
  expect(calls.current).toBe(1)
  timers.advanceTo(clock.now + 500)
  d.resolve(fix(clock.now, 1))
  const r = await p
  expect(r.source).toBe('fresh')
  expect(r.fix?.lat).toBe(23.5)
  expect(r.refresh).toBe(false)
  expect(r.waitedMs).toBe(500)
  expect(timers.cleared).toHaveLength(1)
  expect(timers.pending.size).toBe(0)
  expect(calls.lastKnown).toEqual([FRESH_FIX_MAX_AGE_MS])
})

test('无缓存、新定位迟迟不来：预算到点回落到任意年龄的缓存，要求补取；晚到的新定位不抛错', async () => {
  const d = deferred<Fix | null>()
  const { deps, clock, timers, calls } = makeDeps({ current: () => d.promise, stale: fix(T0 - 6 * 24 * 3600_000) })
  const p = acquireFix(deps)
  await flush()
  timers.advanceTo(clock.now + FIX_BUDGET_MS)
  const r = await p
  expect(r.source).toBe('stale')
  expect(r.fix?.capturedAt).toBe(T0 - 6 * 24 * 3600_000)
  expect(r.refresh).toBe(true)
  expect(r.waitedMs).toBe(FIX_BUDGET_MS)
  expect(calls.lastKnown).toEqual([FRESH_FIX_MAX_AGE_MS, undefined])
  d.resolve(fix(clock.now)) // 20s 后才到的那次：只喂系统缓存，这里没人再等它
  await flush()
})

test('无缓存、新定位失败、陈旧缓存也没有：不带坐标（null），要求补取', async () => {
  const { deps } = makeDeps({ current: () => Promise.reject(new Error('gps off')) })
  const r = await acquireFix(deps)
  expect(r.fix).toBeNull()
  expect(r.source).toBe('none')
  expect(r.refresh).toBe(true)
})

test('lastKnown 抛异常不影响回落链：走现取', async () => {
  const clock = { now: 5_000 }
  const timers = new FakeTimers(clock)
  const deps: FixDeps = {
    lastKnown: async () => { throw new Error('no provider') },
    current: async () => fix(clock.now, 2),
    now: () => clock.now,
    timers,
  }
  const r = await acquireFix(deps)
  expect(r.source).toBe('fresh')
  expect(r.fix?.lat).toBe(24.5)
})

test('预算可注入：征询路径给更长的预算', async () => {
  const d = deferred<Fix | null>()
  const { deps, clock, timers } = makeDeps({ current: () => d.promise })
  const p = acquireFix(deps, { budgetMs: 8_000 })
  await flush()
  timers.advanceTo(clock.now + 3_000)
  expect(timers.pending.size).toBe(1) // 3s 还没到点
  timers.advanceTo(clock.now + 8_000)
  const r = await p
  expect(r.source).toBe('none')
})

test('withinBudget：超时返回 null 且原 promise 不被取消；先到则清定时器', async () => {
  const clock = { now: 0 }
  const timers = new FakeTimers(clock)
  const slow = deferred<number | null>()
  const p1 = withinBudget(slow.promise, 100, timers)
  timers.advanceTo(100)
  expect(await p1).toBeNull()
  slow.resolve(7)
  expect(await slow.promise).toBe(7)

  const fast = deferred<number | null>()
  const p2 = withinBudget(fast.promise, 100, timers)
  fast.resolve(9)
  expect(await p2).toBe(9)
  expect(timers.pending.size).toBe(0)
})

test('skipWait：刚失败过就一毫秒都不等——仍发起现取喂缓存，但直接回落到陈旧缓存', async () => {
  const d = deferred<Fix | null>()
  const { deps, timers, calls } = makeDeps({ current: () => d.promise, stale: fix(T0 - 3600_000) })
  const r = await acquireFix(deps, { skipWait: true })
  expect(r.source).toBe('stale')
  expect(r.waitedMs).toBe(0)
  expect(r.refresh).toBe(true)
  expect(calls.current).toBe(1) // 现取照样发起（喂热缓存）
  expect(timers.pending.size).toBe(0) // 但没有为它排定时器
  d.resolve(null)
  await flush()
})

test('skipWait 时缓存够新仍走缓存：跳过的只是等待，不是第①步', async () => {
  const { deps, calls } = makeDeps({ cached: fix(T0 - 1_000) })
  const r = await acquireFix(deps, { skipWait: true })
  expect(r.source).toBe('cached')
  expect(calls.current).toBe(0)
})

// ── 系统各 provider 的 last-known 里挑最新鲜的（2026-09-17 定位来源复盘）──
const pf = (provider: string, ageMs: number, accuracy: number | null = 30, lat = 22.54, lng = 113.94) =>
  ({ provider, latitude: lat, longitude: lng, accuracy, ageMs })

test('pickFreshest：年龄最小者胜，与 provider 顺序无关——GMS fused 那份三天前的缓存排不到前面', () => {
  const fixes = [pf('gps', 6 * 24 * 3600_000), pf('network', 5 * 60_000), pf('fused', 3 * 24 * 3600_000), pf('passive', 5 * 60_000 + 10)]
  expect(pickFreshest(fixes)?.provider).toBe('network')
})

test('pickFreshest：maxAge 之外的不要；同龄取精度更好的；非法 / 空值坐标跳过', () => {
  expect(pickFreshest([pf('network', 90_000)], 60_000)).toBeNull()
  expect(pickFreshest([pf('network', 1_000, 44), pf('gps', 1_000, 12)], 60_000)?.provider).toBe('gps')
  expect(pickFreshest([pf('network', 1_000, null), pf('gps', 1_000, 500)], 60_000)?.provider).toBe('gps')
  expect(pickFreshest([pf('network', 1_000, 30, 0, 0), pf('gps', 5_000, 30, 91, 0), pf('passive', 5_000, 30, Number.NaN, 1)])).toBeNull()
  expect(pickFreshest([])).toBeNull()
})

test('pickFreshest：负年龄（时钟抖动）按 0 处理，不会因此被判成未来而丢掉', () => {
  expect(pickFreshest([pf('network', -50)], 60_000)?.provider).toBe('network')
})
