// mobile/test/presenceSeen.test.ts
// 「这条 error 气泡是什么时候出现的」——`Msg` 是共享类型、不能加字段（计划 §0 第 5 条），
// 所以时刻登记在收集器本地。
//
// 第 2 批遗留③钉的那条偏差：`lastError.at` 记的是「hook 第一次看见它」而不是「它发生的时刻」。
// 后果具体：**挂载时列表里最后一条若是 error 气泡，会白闪一次 4s 红胶囊**——一条几天前的
// 错误被渲染成刚刚发生。B1 的判据（计划 §0.1 第 3 批附加项③）：**只有挂载之后新出现的才算
// 「刚发生」**，挂载时就在的登记成一个永远过期的时刻。
//
// 这里测的是登记表本身与它跟 `derivePresence` 接起来之后的行为——hook 要渲染器才跑得起来，
// 而判据不该只活在渲染器里。
import { derivePresence, type PresenceInput } from '@/core/presence/presence'
import { SeenRegistry, pickLastError, type ErrorLike } from '@/features/chat/usePresence'

const NOW = 5_000_000

function base(over: Partial<PresenceInput> = {}): PresenceInput {
  return {
    now: NOW,
    connStatus: 'open',
    connChangedAt: NOW - 60_000,
    hfEnabled: false,
    hfUsable: false,
    hfFsm: 'IDLE',
    hfFsmChangedAt: NOW - 1000,
    ptt: 'idle',
    partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false,
    pendingOps: [],
    pendingLocation: false,
    voicePipeline: 'classic',
    visionCapturing: false,
    queued: 0,
    lastError: null,
    degradations: [],
    driving: false,
    identity: 'handheld',
    user: 'u1',
    ...over,
  }
}

const msg = (id: string, over: Partial<ErrorLike> = {}): ErrorLike => ({
  id,
  role: 'assistant',
  text: '出错了：连接超时',
  error: true,
  ...over,
})

describe('SeenRegistry', () => {
  test('firstSeen 第一次登记当前时刻，之后钟走了也返回同一个值', () => {
    let t = 1000
    const r = new SeenRegistry(200, () => t)
    expect(r.firstSeen('err:a')).toBe(1000)
    t = 9999
    expect(r.firstSeen('err:a')).toBe(1000)
    expect(r.firstSeen('err:b')).toBe(9999)
  })

  test('seed 把「挂载时就已存在」登记成给定时刻，firstSeen 不再用当前时刻覆盖它', () => {
    const r = new SeenRegistry(200, () => 9999)
    r.seed('err:old', 0)
    expect(r.firstSeen('err:old')).toBe(0)
  })

  test('seed 不覆盖已登记的值（第二次挂载不许把新错误洗成旧的）', () => {
    let t = 1000
    const r = new SeenRegistry(200, () => t)
    r.firstSeen('err:new') // 1000：挂载之后新出现的
    r.seed('err:new', 0) // 再挂载一次时的 seed
    expect(r.firstSeen('err:new')).toBe(1000)
  })

  test('超过容量逐出最老的（内存不许无界增长）', () => {
    let t = 0
    const r = new SeenRegistry(2, () => (t += 1))
    r.firstSeen('a') // 1
    r.firstSeen('b') // 2
    r.firstSeen('c') // 3，逐出 a
    expect(r.firstSeen('b')).toBe(2)
    expect(r.firstSeen('c')).toBe(3)
    expect(r.firstSeen('a')).toBe(4) // 已被逐出 ⇒ 重新登记
  })
})

describe('pickLastError', () => {
  const at = (id: string) => (id === 'err:m2' ? NOW - 1000 : 0)

  test('取最后一条助手 error 气泡，不是第一条', () => {
    const got = pickLastError([msg('m1', { text: '出错了：旧的' }), msg('m2')], at)
    expect(got).toEqual({ text: '出错了', at: NOW - 1000 })
  })

  test('没有 error 气泡 → null', () => {
    expect(pickLastError([msg('m1', { error: false })], at)).toBeNull()
  })

  test('用户气泡不算（error 只由助手侧标）', () => {
    expect(pickLastError([msg('m1', { role: 'user' })], at)).toBeNull()
  })

  test('非「出错了」开头的原话截 20 字', () => {
    const long = '一二三四五六七八九十一二三四五六七八九十又多出来的'
    const got = pickLastError([msg('m2', { text: long })], at)
    expect(got?.text).toBe(long.slice(0, 20))
  })
})

describe('接起来之后：挂载时就在的 error 不亮红胶囊', () => {
  test('seed(0) → pickLastError 给出 at=0 → derivePresence 判它早已过期', () => {
    const r = new SeenRegistry(200, () => NOW) // 「hook 第一次看见」就是此刻
    r.seed('err:m1', 0) // 挂载时它就在列表里
    const lastError = pickLastError([msg('m1')], (id) => r.firstSeen(id))
    expect(lastError).toEqual({ text: '出错了', at: 0 })
    expect(derivePresence(base({ lastError })).capsule).toBeUndefined()
  })

  test('对照：挂载之后新出现的那条照样亮 4s', () => {
    const r = new SeenRegistry(200, () => NOW) // 新气泡第一次被看见 = 此刻
    const lastError = pickLastError([msg('m9')], (id) => r.firstSeen(id))
    expect(lastError?.at).toBe(NOW)
    expect(derivePresence(base({ lastError })).capsule?.text).toBe('出错了')
  })
})
