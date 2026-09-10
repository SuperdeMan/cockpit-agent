// 时延统计口径（AR08 / A08-1）。
//
// 为什么要一份代码而不是「报告里写个 p95」：分位数有好几种定义，换一种能把同一批样本
// 的 p95 抬高或压低一整档。这里钉死 **nearest-rank**（排序后取第 `ceil(p*n)` 项，
// 1-indexed），定义随报告一起输出，谁都不用猜是哪一种。
//
// 另一条同等重要的纪律：**失败、超时、无声、没测到始终在总分母里**。
// 只统计成功样本的时延一定好看，而那正是要消灭的形态 —— 所以 `summarize` 的入参是
// 「本轮尝试」而不是「本轮读数」，输出里 measured / unmeasured / failed 三个分母并列，
// 想只报 p95 而不报覆盖率在类型上就做不到。

/** 一次尝试。`ms === null` = 没测到（NOT_MEASURED），**不是** 0。 */
export interface LatencySample {
  ms: number | null
  outcome: 'ok' | 'failed' | 'timeout' | 'cancelled' | 'silent'
  /** 便于回溯到原始那一轮 */
  interactionId?: string
}

export interface LatencySummary {
  /** 分位数定义，随报告输出——不许只写数字不写口径 */
  definition: 'nearest-rank(ceil(p*n), 1-indexed)'
  /** 总尝试数 = 分母 */
  n: number
  /** 有读数的样本数 */
  measured: number
  /** 有尝试但没读数（仪器缺失、事件没到） */
  unmeasured: number
  /** 非 ok 收尾的尝试数（失败/超时/取消/无声）——它们**留在** n 里 */
  nonOk: number
  p50: number | null
  p95: number | null
  min: number | null
  max: number | null
  /** measured / n。小于 1 时任何分位数都只是「测到的那部分」的读数 */
  coverage: number
}

/**
 * nearest-rank 分位数：排序后取第 `ceil(p * n)` 项（1-indexed）。
 * 空集返回 null。p 超出 (0,1] 抛错——静默夹到边界会让报告里出现一个没人能复算的数。
 */
export function percentileNearestRank(values: readonly number[], p: number): number | null {
  if (!(p > 0 && p <= 1)) throw new Error('percentile p must be in (0, 1], got ' + String(p))
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  const rank = Math.ceil(p * sorted.length)
  return sorted[rank - 1]
}

export function summarize(samples: readonly LatencySample[]): LatencySummary {
  const measuredValues: number[] = []
  let nonOk = 0
  for (const s of samples) {
    if (s.outcome !== 'ok') nonOk += 1
    if (typeof s.ms === 'number' && Number.isFinite(s.ms)) measuredValues.push(s.ms)
  }
  const n = samples.length
  return {
    definition: 'nearest-rank(ceil(p*n), 1-indexed)',
    n,
    measured: measuredValues.length,
    unmeasured: n - measuredValues.length,
    nonOk,
    p50: percentileNearestRank(measuredValues, 0.5),
    p95: percentileNearestRank(measuredValues, 0.95),
    min: measuredValues.length ? Math.min(...measuredValues) : null,
    max: measuredValues.length ? Math.max(...measuredValues) : null,
    coverage: n ? measuredValues.length / n : 0,
  }
}

/**
 * 一行人读的摘要。**必须带分母与覆盖率**——「p95 4.2s」单独一句话没有信息量，
 * 它可能是 30 条里测到 3 条算出来的。
 */
export function formatSummary(label: string, s: LatencySummary): string {
  const num = (v: number | null): string => (v === null ? 'NOT_MEASURED' : String(Math.round(v)) + 'ms')
  return (
    `${label}: n=${s.n} measured=${s.measured} unmeasured=${s.unmeasured} nonOk=${s.nonOk} ` +
    `p50=${num(s.p50)} p95=${num(s.p95)} min=${num(s.min)} max=${num(s.max)} ` +
    `coverage=${(s.coverage * 100).toFixed(0)}% [${s.definition}]`
  )
}
