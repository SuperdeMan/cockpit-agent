// 回答文字匀速上屏的判据（2026-09-18，core/session/streamReveal.ts）。
// 钉六件事：① 每拍至少 1 个字、不越过真实长度；② 第一簇到达后在 REVEAL_LAG_MS 内追平（到达之间匀速）；
// ③ 没有积压压力时按最低速度匀速出、不一次到位；④ 真实文本不是已显示文本的延长 ⇒ 直接跳到位；
// ⑤ 整段一次到达按上限速度扫出；⑥ **成批到达**（每 600ms 来一批 60 字，真机搜索轮的形态）⇒ 显示跟到达速率走：
//    批与批之间不空等、落后不超过 REVEAL_MAX_LAG_MS——「吐一批、卡一下」变成连续的流。
import {
  REVEAL_LAG_MS,
  REVEAL_MAX_CPS,
  REVEAL_MAX_LAG_MS,
  REVEAL_MIN_CPS,
  REVEAL_TICK_MS,
  arrivalCps,
  revealAdvance,
  revealCps,
  revealInit,
} from '@/core/session/streamReveal'

/** 从 t=0 起每拍推进；`text` 固定（一次到达） */
const run = (from: string, text: string, ticks: number) => {
  let s = revealInit(from)
  const trail: string[] = []
  for (let i = 0; i < ticks; i += 1) {
    s = revealAdvance(s, text, REVEAL_TICK_MS, (i + 1) * REVEAL_TICK_MS)
    trail.push(s.shown)
  }
  return { s, trail }
}

test('每拍至少推进 1 个字，且不越过真实长度；已追平 / 文本变短 ⇒ 直接给真实文本并停止', () => {
  expect(revealAdvance(revealInit(''), '好', 0, 0).shown).toBe('好')
  const same = revealAdvance(revealInit('深圳'), '深圳', REVEAL_TICK_MS, 0)
  expect(same).toEqual({ shown: '深圳', cps: 0, target: 2, arrivals: [] })
  expect(revealAdvance(revealInit('深圳的历史很长'), '深圳', REVEAL_TICK_MS, 0).shown).toBe('深圳')
  const { trail } = run('', '一二三四五六七八九十', 40)
  for (const t of trail) expect(t.length).toBeLessThanOrEqual(10)
  expect(trail[trail.length - 1]).toBe('一二三四五六七八九十')
})

test('第一簇 34 个字到达后按节拍追平，不超过 REVEAL_LAG_MS（到达之间速度不变）', () => {
  const text = '很久以前，在一座被海雾常年笼罩的小岛上，住着一个叫阿澈的年轻人。'
  const budget = Math.ceil(REVEAL_LAG_MS / REVEAL_TICK_MS)
  const { s, trail } = run('', text, budget)
  expect(s.shown).toBe(text)
  // 匀速：追平之前每拍推进的字数相同（最后一拍可以短）
  const steps = trail.map((t, i) => t.length - (i ? trail[i - 1].length : 0)).filter((_, i) => trail[i].length < text.length)
  expect(new Set(steps).size).toBe(1)
})

test('少量积压按最低速度匀速出，不是一拍全到位', () => {
  // 4 个字、每拍 33ms：最低 40 字/秒 ⇒ 每拍 ceil(1.32)=2 个字，要两拍
  const { trail } = run('', '一二三四', 2)
  expect(trail).toEqual(['一二', '一二三四'])
  expect(revealCps(4, 0)).toBe(REVEAL_MIN_CPS)
})

test('整段替换（final 剥 markdown）⇒ 不追，直接跳到位', () => {
  const s = revealAdvance({ shown: '**深圳**的历', cps: 80, target: 9, arrivals: [] }, '深圳的历史', REVEAL_TICK_MS, 100)
  expect(s).toEqual({ shown: '深圳的历史', cps: 0, target: 5, arrivals: [] })
})

test('整段一次到达（只在 final 里给的 700 字）按上限速度扫出：既不一拍到位，也不慢过上限', () => {
  const text = '深圳'.repeat(350)
  expect(revealCps(text.length, 0)).toBe(REVEAL_MAX_CPS)
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

test('到达速率：两条样本以上才算；跨度太短算不出', () => {
  expect(arrivalCps([])).toBe(0)
  expect(arrivalCps([{ at: 0, len: 10 }])).toBe(0)
  expect(arrivalCps([{ at: 0, len: 10 }, { at: 10, len: 20 }])).toBe(0)
  expect(arrivalCps([{ at: 0, len: 10 }, { at: 1000, len: 110 }])).toBe(100)
})

test('成批到达（每 600ms 一批 60 字）⇒ 跟到达速率走：批间不空等、落后不超过 REVEAL_MAX_LAG_MS', () => {
  const batchChars = 60
  const batchGap = 600
  const batches = 8
  let text = ''
  let s = revealInit('')
  const idleTicksAfterWarmup: number[] = []
  let maxLagMs = 0
  const totalMs = batchGap * batches + REVEAL_MAX_LAG_MS
  let prevShownLen = 0
  for (let t = 0; t <= totalMs; t += REVEAL_TICK_MS) {
    // 到达：t 落在某批的到达时刻之后就把那批接上
    const arrived = Math.min(batches, Math.floor(t / batchGap) + 1)
    text = '字'.repeat(arrived * batchChars)
    s = revealAdvance(s, text, REVEAL_TICK_MS, t)
    // 落后 = 已到达但还没显示的字数换算成到达时间（按到达速率 100 字/s）
    const behindChars = text.length - s.shown.length
    maxLagMs = Math.max(maxLagMs, (behindChars * 1000) / 100)
    // 第三批起（速率已估出）：只要还有没显示的字，每拍都得在动
    if (arrived >= 3 && behindChars > 0 && s.shown.length === prevShownLen) idleTicksAfterWarmup.push(t)
    prevShownLen = s.shown.length
  }
  expect(s.shown).toBe(text)
  expect(idleTicksAfterWarmup).toEqual([])
  expect(maxLagMs).toBeLessThanOrEqual(REVEAL_MAX_LAG_MS + REVEAL_TICK_MS)
  // 稳态速度≈到达速率（100 字/s ⇒ 每拍 3–4 个字），不是「240ms 扫完一批然后空等」（那会是每拍 8 个字）；追平后 cps 归零是设计
  expect(s.cps).toBe(0)
  expect(revealCps(batchChars, 100)).toBe(100)
  expect(revealCps(batchChars, 100)).toBeLessThan((batchChars * 1000) / REVEAL_LAG_MS)
})
