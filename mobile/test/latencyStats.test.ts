// 时延统计口径（AR08 / A08-1）。
//
// ⚠ 这批用例刻意**不用全成功表**。方案里那条纪律——「失败分母不能被成功重试覆盖」——
// 只有在人工小表里同时放进失败、超时、没测到三种样本，才验得到；全绿的表连分母算错都看不出来。
import { formatSummary, percentileNearestRank, summarize, type LatencySample } from '@/core/obs/latencyStats'

describe('percentileNearestRank', () => {
  // 逐值人工核过：n=10，p50 → ceil(0.5*10)=5 → 第 5 项；p95 → ceil(0.95*10)=10 → 第 10 项
  const ten = [120, 90, 300, 210, 150, 180, 600, 240, 270, 130]

  test('n=10 的 p50 / p95 落在人工算好的那一项上', () => {
    expect(percentileNearestRank(ten, 0.5)).toBe(180)
    expect(percentileNearestRank(ten, 0.95)).toBe(600)
  })

  test('n=1 时 p50 与 p95 是同一个样本（小样本不许被读成分布）', () => {
    expect(percentileNearestRank([42], 0.5)).toBe(42)
    expect(percentileNearestRank([42], 0.95)).toBe(42)
  })

  test('空集返回 null，不返回 0', () => {
    expect(percentileNearestRank([], 0.5)).toBeNull()
  })

  test('不排序的输入也对（内部拷贝后排序，不改调用方的数组）', () => {
    const src = [3, 1, 2]
    expect(percentileNearestRank(src, 0.5)).toBe(2)
    expect(src).toEqual([3, 1, 2])
  })

  test('p 越界抛错，不静默夹到边界', () => {
    expect(() => percentileNearestRank(ten, 0)).toThrow()
    expect(() => percentileNearestRank(ten, 1.5)).toThrow()
  })
})

describe('summarize：失败与没测到都留在分母里', () => {
  // 人工小表：6 次尝试 —— 3 次正常有读数、1 次超时无读数、1 次失败**但有读数**、1 次取消无读数
  const table: LatencySample[] = [
    { ms: 1200, outcome: 'ok' },
    { ms: 2400, outcome: 'ok' },
    { ms: 3600, outcome: 'ok' },
    { ms: null, outcome: 'timeout' },
    { ms: 9000, outcome: 'failed' },
    { ms: null, outcome: 'cancelled' },
  ]

  test('n 是尝试数，不是成功数', () => {
    const s = summarize(table)
    expect(s.n).toBe(6)
    expect(s.measured).toBe(4)
    expect(s.unmeasured).toBe(2)
    expect(s.nonOk).toBe(3)
    expect(s.coverage).toBeCloseTo(4 / 6)
  })

  test('分位数只在有读数的样本上算，但覆盖率把这件事说出来', () => {
    const s = summarize(table)
    // 有读数的四个：1200 / 2400 / 3600 / 9000；p50 → ceil(0.5*4)=2 → 2400
    expect(s.p50).toBe(2400)
    // p95 → ceil(0.95*4)=4 → 9000（那次失败的读数**没有**被踢出去）
    expect(s.p95).toBe(9000)
    expect(s.min).toBe(1200)
    expect(s.max).toBe(9000)
    expect(s.coverage).toBeLessThan(1)
  })

  test('只留成功样本会得到明显更好看的数——所以不许那么算', () => {
    const okOnly = summarize(table.filter((x) => x.outcome === 'ok'))
    const all = summarize(table)
    expect(okOnly.p95).toBe(3600)
    expect(all.p95).toBe(9000)
    expect(okOnly.n).toBeLessThan(all.n)
  })

  test('全部没测到时分位数是 null，n 仍然是尝试数', () => {
    const s = summarize([
      { ms: null, outcome: 'timeout' },
      { ms: null, outcome: 'silent' },
    ])
    expect(s.n).toBe(2)
    expect(s.measured).toBe(0)
    expect(s.p50).toBeNull()
    expect(s.p95).toBeNull()
    expect(s.coverage).toBe(0)
  })

  test('空表不炸', () => {
    const s = summarize([])
    expect(s.n).toBe(0)
    expect(s.coverage).toBe(0)
    expect(s.p50).toBeNull()
  })
})

describe('formatSummary', () => {
  test('一行里同时带分母、覆盖率和分位数定义', () => {
    const line = formatSummary('L 简短确定回答', summarize([
      { ms: 1000, outcome: 'ok' },
      { ms: null, outcome: 'failed' },
    ]))
    expect(line).toContain('n=2')
    expect(line).toContain('measured=1')
    expect(line).toContain('unmeasured=1')
    expect(line).toContain('nonOk=1')
    expect(line).toContain('coverage=50%')
    expect(line).toContain('nearest-rank')
  })

  test('没测到打印成 NOT_MEASURED，不打印 0ms', () => {
    const line = formatSummary('空桶', summarize([{ ms: null, outcome: 'timeout' }]))
    expect(line).toContain('p95=NOT_MEASURED')
    expect(line).not.toContain('p95=0ms')
  })
})
