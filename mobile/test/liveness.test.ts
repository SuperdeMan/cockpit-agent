// 连接探活守卫（2026-08-27）。
//
// 被测的是「什么时候该判死」这条判据本身——它两个方向都会错：
// 判早了（一次抖动就重连）会把健康连接踢掉，判晚了/不判就是那个真实缺陷
// （断网后 UI 仍说「在线」，send 把帧写进死 socket、消息真的丢）。
import { startLiveness, type LivenessTimers } from '@/core/api/liveness'

/** 手动驱动的定时器（不碰真实时钟） */
function fakeTimers() {
  const q: Array<(() => void) | null> = []
  const timers: LivenessTimers = {
    set: (fn) => {
      q.push(fn)
      return q.length - 1
    },
    clear: (id) => {
      q[id as number] = null
    },
  }
  return {
    timers,
    /** 触发当前所有待跑的（探活每轮只排一个，所以等价于「走一轮」） */
    async fire() {
      const pending = q.slice()
      for (let i = 0; i < pending.length; i++) {
        if (pending[i]) {
          q[i] = null
          pending[i]!()
        }
      }
      // 让 probe 的 then/finally 跑完
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    },
    live: () => q.filter(Boolean).length,
  }
}

function fakeAppState(initial: 'active' | 'background' = 'active') {
  const handlers: Array<(s: string) => void> = []
  return {
    currentState: initial as never,
    addEventListener(_t: 'change', h: (s: never) => void) {
      handlers.push(h as (s: string) => void)
      return { remove: () => handlers.splice(handlers.indexOf(h as never), 1) }
    },
    emit(s: string) {
      this.currentState = s as never
      handlers.slice().forEach((h) => h(s))
    },
  }
}

describe('startLiveness', () => {
  test('探得通就不判死', async () => {
    const t = fakeTimers()
    const onDead = jest.fn()
    startLiveness({
      probe: async () => true,
      isOpen: () => true,
      onDead,
      timers: t.timers,
      appState: fakeAppState() as never,
      failThreshold: 2,
    })
    await t.fire()
    await t.fire()
    await t.fire()
    expect(onDead).not.toHaveBeenCalled()
  })

  test('连续失败达阈值判死一次（不是每轮都判）', async () => {
    const t = fakeTimers()
    const onDead = jest.fn()
    startLiveness({
      probe: async () => false,
      isOpen: () => true,
      onDead,
      timers: t.timers,
      appState: fakeAppState() as never,
      failThreshold: 2,
    })
    await t.fire()
    expect(onDead).not.toHaveBeenCalled() // 一次失败不算数——抖动是常态
    await t.fire()
    expect(onDead).toHaveBeenCalledTimes(1)
  })

  test('失败一次后又通了 → 计数清零，不判死', async () => {
    const t = fakeTimers()
    const onDead = jest.fn()
    const results = [false, true, false]
    let i = 0
    startLiveness({
      probe: async () => results[i++] ?? true,
      isOpen: () => true,
      onDead,
      timers: t.timers,
      appState: fakeAppState() as never,
      failThreshold: 2,
    })
    await t.fire()
    await t.fire()
    await t.fire()
    expect(onDead).not.toHaveBeenCalled()
  })

  test('probe 抛出等同探不通，且探活不会就此停摆', async () => {
    const t = fakeTimers()
    const onDead = jest.fn()
    startLiveness({
      probe: async () => {
        throw new Error('boom')
      },
      isOpen: () => true,
      onDead,
      timers: t.timers,
      appState: fakeAppState() as never,
      failThreshold: 2,
    })
    await t.fire()
    await t.fire()
    expect(onDead).toHaveBeenCalledTimes(1)
    expect(t.live()).toBe(1) // 下一轮已排上，没有静默停摆
  })

  test('后台不探（省电；后台本来就不保证，坑账 §9.5）', async () => {
    const t = fakeTimers()
    const probe = jest.fn(async () => false)
    const onDead = jest.fn()
    startLiveness({
      probe,
      isOpen: () => true,
      onDead,
      timers: t.timers,
      appState: fakeAppState('background') as never,
      failThreshold: 2,
    })
    await t.fire()
    await t.fire()
    await t.fire()
    expect(probe).not.toHaveBeenCalled()
    expect(onDead).not.toHaveBeenCalled()
  })

  test('连接已经 closed 时不探（重连链路在跑，再探没意义）', async () => {
    const t = fakeTimers()
    const probe = jest.fn(async () => false)
    startLiveness({
      probe,
      isOpen: () => false,
      onDead: jest.fn(),
      timers: t.timers,
      appState: fakeAppState() as never,
    })
    await t.fire()
    expect(probe).not.toHaveBeenCalled()
  })

  test('回到前台立刻探一次（不用等满一个周期）', async () => {
    const t = fakeTimers()
    const probe = jest.fn(async () => true)
    const app = fakeAppState('background')
    startLiveness({
      probe,
      isOpen: () => true,
      onDead: jest.fn(),
      timers: t.timers,
      appState: app as never,
    })
    expect(probe).not.toHaveBeenCalled()
    app.emit('active')
    await Promise.resolve()
    expect(probe).toHaveBeenCalledTimes(1)
  })

  test('切后台再回前台不会带着旧的失败计数直接判死', async () => {
    const t = fakeTimers()
    const onDead = jest.fn()
    const app = fakeAppState('active')
    startLiveness({
      probe: async () => false,
      isOpen: () => true,
      onDead,
      timers: t.timers,
      appState: app as never,
      failThreshold: 2,
    })
    await t.fire() // 前台失败一次
    app.currentState = 'background' as never
    await t.fire() // 后台那轮：跳过并清零
    app.emit('active') // 回前台立刻探（第 1 次失败）
    await Promise.resolve()
    await Promise.resolve()
    expect(onDead).not.toHaveBeenCalled()
  })

  test('stop 之后不再探，重复 stop 安全', async () => {
    const t = fakeTimers()
    const probe = jest.fn(async () => false)
    const stop = startLiveness({
      probe,
      isOpen: () => true,
      onDead: jest.fn(),
      timers: t.timers,
      appState: fakeAppState() as never,
    })
    stop()
    stop()
    await t.fire()
    expect(probe).not.toHaveBeenCalled()
    expect(t.live()).toBe(0)
  })
})
