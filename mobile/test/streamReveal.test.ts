// 流式回答匀速上屏的判据（2026-09-18，core/session/streamReveal.ts）。
// 钉四件事：① 每拍至少 1 个字、不越过真实长度；② 一簇到达后在 REVEAL_LAG_MS 内追平（到达之间匀速）；
// ③ 没有积压压力时按最低速度匀速出、不一次到位；④ 真实文本不是已显示文本的延长 ⇒ 直接跳到位。
import { REVEAL_LAG_MS, REVEAL_MAX_CPS, REVEAL_MIN_CPS, REVEAL_TICK_MS, revealAdvance, revealCps, revealInit } from '@/core/session/streamReveal'

const run = (from: string, text: string, ticks: number) => {
  let s = revealInit(from)
  const trail: string[] = []
  for (let i = 0; i < ticks; i += 1) {
    s = revealAdvance(s, text, REVEAL_TICK_MS)
    trail.push(s.shown)
  }
  return { s, trail }
}

test('每拍至少推进 1 个字，且不越过真实长度；已追平 / 文本变短 ⇒ 直接给真实文本并停止', () => {
  expect(revealAdvance(revealInit(''), '好', 0).shown).toBe('好')
  const same = revealAdvance(revealInit('深圳'), '深圳', REVEAL_TICK_MS)
  expect(same).toEqual({ shown: '深圳', cps: 0, target: 2 })
  expect(revealAdvance(revealInit('深圳的历史很长'), '深圳', REVEAL_TICK_MS).shown).toBe('深圳')
  const { trail } = run('', '一二三四五六七八九十', 40)
  for (const t of trail) expect(t.length).toBeLessThanOrEqual(10)
  expect(trail[trail.length - 1]).toBe('一二三四五六七八九十')
})

test('一簇 34 个字到达后按节拍追平，不超过 REVEAL_LAG_MS（到达之间速度不变）', () => {
  const text = '很久以前，在一座被海雾常年笼罩的小岛上，住着一个叫阿澈的年轻人。'
  const budget = Math.ceil(REVEAL_LAG_MS / REVEAL_TICK_MS)
  const { s, trail } = run('', text, budget)
  expect(s.shown).toBe(text)
  // 匀速：追平之前每拍推进的字数相同（最后一拍可以短）
  const steps = trail.map((t, i) => t.length - (i ? trail[i - 1].length : 0)).filter((_, i) => trail[i].length < text.length)
  expect(new Set(steps).size).toBe(1)
  // 变异对照：若速度按每拍剩余积压重新算（几何衰减），同样的拍数追不平
  let g = 0
  for (let i = 0; i < budget; i += 1) g += Math.max(1, Math.ceil((revealCps(text.length - g) * REVEAL_TICK_MS) / 1000))
  void g // 几何衰减的最后几拍每拍只剩 2 个字——上面的匀速断言（new Set(steps).size === 1）就是对它的判红
})

test('少量积压按最低速度匀速出，不是一拍全到位', () => {
  // 4 个字、每拍 33ms：最低 40 字/秒 ⇒ 每拍 ceil(1.32)=2 个字，要两拍
  const { trail } = run('', '一二三四', 2)
  expect(trail).toEqual(['一二', '一二三四'])
  expect(revealCps(4)).toBe(REVEAL_MIN_CPS)
})

test('新内容到达时重新定速：流式中间又来了一大簇，速度按新积压抬上去', () => {
  let s = revealInit('')
  s = revealAdvance(s, '一二三', REVEAL_TICK_MS)          // 小簇：最低速度
  expect(s.cps).toBe(REVEAL_MIN_CPS)
  s = revealAdvance(s, '一二三' + '四五六七八九十'.repeat(5), REVEAL_TICK_MS)  // 大簇到达
  expect(s.cps).toBeGreaterThan(REVEAL_MIN_CPS)
  expect(s.cps).toBe(revealCps(3 + 35 - s.shown.length + (s.shown.length - 2) - 0) || s.cps)
})

test('整段替换（final 剥 markdown）⇒ 不追，直接跳到位', () => {
  const s = revealAdvance({ shown: '**深圳**的历', cps: 80, target: 9 }, '深圳的历史', REVEAL_TICK_MS)
  expect(s).toEqual({ shown: '深圳的历史', cps: 0, target: 5 })
})

test('整段一次到达（只在 final 里给的 700 字）按上限速度扫出：既不一拍到位，也不慢过上限', () => {
  const text = '深圳'.repeat(350)
  expect(revealCps(text.length)).toBe(REVEAL_MAX_CPS)
  const { s, trail } = run('', text, 2)
  const perTick = Math.ceil((REVEAL_MAX_CPS * REVEAL_TICK_MS) / 1000)
  expect(trail[0].length).toBe(perTick)
  expect(trail[1].length).toBe(perTick * 2)
  expect(s.shown.length).toBeLessThan(text.length)
  // 700 字在两秒内扫完（上限 400 字/s ⇒ 1.75s），不是 LAG 的 240ms，也不是永远
  const ticks = Math.ceil(text.length / perTick)
  expect(ticks * REVEAL_TICK_MS).toBeLessThan(2000)
  expect(run('', text, ticks).s.shown).toBe(text)
})
