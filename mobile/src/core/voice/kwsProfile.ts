// 唤醒词参数档（AR07 / A07-1）。
//
// 为什么要单独一个文件：AR07 要做的是**单变量 A/B**，而单变量 A/B 有两个前提，
// 少一个结论就不成立：
//   ① 生产默认与实验取值分开——实验档只活在本次实验会话里，绝不写进用户设置、
//      更不改生产默认（那样下一次「A 组」就已经不是 A 了）；
//   ② 实际传给原生的那组值**可回读**——「我以为传了 0.15」和「原生收到 0.15」是两件事，
//      本仓已经有过一整批因为「改了文件却没换 APK」而作废的读数。
//
// 校验的口径：**越界抛错，不静默夹**。夹过的值最坏的形态是 A/B 两臂其实是同一个档，
// 而报告上写着两个不同的数字——那种失败不会有任何症状。

/**
 * 阈值默认值的**唯一声明处**（与 HMI `kwsEngine.ts::kwsConfig()` 的
 * keywordsThreshold / keywordsScore 逐值相同）。
 *
 * ⚠ 为什么住在这里而不是 kws.ts：kws.ts 要 import 本文件的 validate/PRODUCTION_PROFILE，
 * 本文件再回头 import kws.ts 的常量就形成**循环 import** —— 模块初始化顺序下
 * `PRODUCTION_PROFILE.threshold` 会是 `undefined`，而 `undefined` 一路传到原生 load()
 * 也不会报错，只是唤醒忽然不灵了。本轮单测第一次跑就抓到这条（Expected 0.2, Received undefined）。
 * kws.ts 改成从这里再导出，声明源仍然只有一份。
 */
export const DEFAULT_THRESHOLD = 0.2
export const DEFAULT_SCORE = 2.0

export interface KwsProfile {
  /** 档名。报告按它分栏，也是回读时的比对物 */
  id: string
  /** sherpa-onnx `keywords_threshold`：命中概率门槛，(0, 1] */
  threshold: number
  /** sherpa-onnx `keywords_score`：关键词分数加成，[0, 10] */
  score: number
}

/**
 * 生产默认。**它就是 A 组**——`kws.ts` 的 DEFAULT_* 是唯一真相源，这里只引用不复制，
 * 否则两个地方各写一份 0.2，改一处的那天 A 组会静默变成两个值。
 *
 * ⚠ 架构当前写明「HMI 与 mobile 同模型同阈值」。本文件**不改变**这个承诺：
 * 它只让实验能在不动默认的前提下跑起来。若最终证据支持手机用独立声学 profile，
 * 那是 AR07 §5 的一次范围明确的架构决策，要先改约定再改实现。
 */
export const PRODUCTION_PROFILE: KwsProfile = {
  id: 'A-default',
  threshold: DEFAULT_THRESHOLD,
  score: DEFAULT_SCORE,
}

/** 探索集的候选（AR07 §3.2）：只动 threshold，score 恒定——单变量的那个「单」 */
export const EXPLORATION_PROFILES: readonly KwsProfile[] = [
  PRODUCTION_PROFILE,
  { id: 'B-thr015', threshold: 0.15, score: DEFAULT_SCORE },
  { id: 'C-thr025', threshold: 0.25, score: DEFAULT_SCORE },
]

export class KwsProfileError extends Error {}

/**
 * 校验一个档。返回**规范化后的新对象**（调用方后来改自己那份也影响不到已生效的档）。
 * 任何越界都抛 `KwsProfileError`，不夹、不回落默认。
 */
export function validateKwsProfile(p: KwsProfile): KwsProfile {
  if (!p || typeof p.id !== 'string' || !p.id.trim()) {
    throw new KwsProfileError('profile.id 必须是非空字符串——报告按它分栏，没名字的档没法归档')
  }
  if (!Number.isFinite(p.threshold) || p.threshold <= 0 || p.threshold > 1) {
    throw new KwsProfileError(`threshold 必须在 (0, 1] 内，实得 ${String(p.threshold)}`)
  }
  if (!Number.isFinite(p.score) || p.score < 0 || p.score > 10) {
    throw new KwsProfileError(`score 必须在 [0, 10] 内，实得 ${String(p.score)}`)
  }
  return { id: p.id.trim(), threshold: p.threshold, score: p.score }
}

/** 两个档是不是同一组数值（用来断言「A′ 真的回到了 A」，以及防止 A/B 其实同档） */
export function sameProfileValues(a: KwsProfile, b: KwsProfile): boolean {
  return a.threshold === b.threshold && a.score === b.score
}
