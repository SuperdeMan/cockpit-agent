// mobile/src/ui/layout/sheetHeight.ts
// 语音层高度判据（B4-13 缺陷 A / 方案 §6「一屏一卡」「目标 ≥56dp」；2026-09-11 下限扩到泊车）。
//
// `sheetDetent`（0.4 / 0.62 / 0.78）是**比例**——它表达不了「内容有固有最小高」。
// 2026-09-03 真机上两个同源症状：
//  · 外屏横 记录区只有 **192dp** ⇒ 0.4×192 = 77dp，连「把手 12 + 底栏 17 + 56dp 收起键」
//    这 85dp 硬 chrome 都装不下，RN 的 flex 收缩把 `voice-sheet-collapse` 压到 **53.0dp < 56**（违反 §6）；
//  · 内屏 记录区 **573dp** ⇒ 0.78×573 = 447dp，`driving-card-title` 的 bounds y2<y1
//    （uiautomator 表示「被裁到可视区外」）⇒ §6「一屏一卡」那张压缩卡用户看不到。
// 判据盲区在于 `presence.test.ts` 只验 detent 的**数值**，验不出「那个高度装不装得下内容」——
// 只有真机能露（§6.3 缺陷 A）。这里把「最小高」写成判据补上这一面。
//
// **B5-12（泓舟 B4 真机轮原话①）**：底栏「收起 / 打断」整段撤掉——收起改为顶缘**把手带**下拖 /
// 轻点（打断并进 Composer 的合一键）。把手带接替 `voice-sheet-collapse` 的 §6「目标 ≥56dp」演员身份
// （testID 沿用，`target_probe` 与 B4 的读数表都不用改）⇒ 原来的三项 chrome「把手 12 + 底栏 17 +
// 键 56」合并成一项「把手带 = 目标高」，**chrome 从 117 降到 88（行车、标准字号）**。
// 这是缺陷 A 横屏半的第一个 lever（B4 §6.4：横屏底栏占 73dp，撤掉是净收益）。
//
// **2026-09-11（用户：「上升幅度要按手机尺寸适配，光球可能被遮」）**：下限原来**只给行车档**，泊车
// 一直是纯比例。矮容器上 0.4 × 容器装不下「把手带 48 + padding 32 + 球 88 + 胶囊」，球被层底裁掉——
// 同一个 detent 在不同尺寸的手机上「完成程度」不一样，正是用户看到的那件事。泊车路径现在走同一条
// `min(容器, max(比例, 下限))`，只是常量取泊车的（目标 48 / 球 88 / 回答 16pt）。主力机（外屏竖
// 578.67dp）三档比例都高于下限 ⇒ **读数逐 dp 不变**；矮容器才由下限托住。
//
// 「最小高」的定义：
//   固定 chrome（把手带含一枚目标高 + ScrollView 上下 padding）
//   + 球 + 胶囊（任何一档都要一眼看得见）
//   + **该档的主体**（0.62 = 回答两行；0.78 = 行车压缩卡「标题 + ≤2 字段 + 主按钮」/ 泊车「回答两行 + 卡头」）。
// 转写、chips、更长的回答是**可滚的附属**，不进最小高——它们本来就在 ScrollView 里。
// 横屏车载（split，§6「40:60」）两列并排 ⇒ 取 max 不是相加。
//
// 为什么住在 `ui/layout/` 而不是 `core/presence/presence.ts`（**对泓舟「写进 presence.test.ts」
// 的一处偏离，理由在此**）：这些数是 **VoiceSheet 自己的排版常量**（把手 12 / padding 32 /
// 底栏 17 / 球 120）加 `ui/tokens` 的 TARGET·TYPE，而全仓 `core/` 没有一处 import `ui/`
// （`presence.ts` 头注写着「零 RN import」）。写进 core 就得在 core 里复制一份 tokens，
// 那正是「声明源只留一份」要避免的。判据仍只有这一份，`VoiceSheet` 只读结果。
import type { FontScalePref } from '../../core/settings/store'
import type { SheetDetent } from '../../core/presence/presence'
import { TARGET, scale } from '../tokens'

/** 层内大球直径：行车 120 / 泊车 88（§6）。**VoiceSheet 从这里读**，不再各写一份字面量 */
export const SHEET_ORB = { driving: 120, parked: 88 } as const

/** VoiceSheet 的固定排版（逐条对应 `VoiceSheet.tsx` 的样式；改那边要同步改这里） */
const SCROLL_PAD_DP = 32 // ScrollView contentContainerStyle padding 16（上 + 下）
const GAP_DP = 12 // 组内 / 组间 gap（非 split 时 ScrollView 的 gap 也是 12）

/** 压缩卡（`DrivingCardSummary` + `CardShell`）的最小高 */
function cardMinDp(fontScale: FontScalePref): number {
  const shell = 2 + 24 + 4 * 8 // 边框 1×2 + padding 12×2 + gap 8×4（类型行/标题/两字段/按钮之间）
  const typeRow = scale(16, 'line', fontScale) // CardShell 的类型行（caption 12pt）
  const title = scale(25, 'line', fontScale) // driving-card-title（h2 18pt）
  const fields = 2 * scale(20, 'line', fontScale) // ≤2 个字段（body 15pt）
  return shell + typeRow + title + fields + scale(TARGET.driving, 'target', fontScale)
}

/** 泊车 0.78 档的卡头：CardShell 边框 1×2 + padding 12×2 + 类型行（caption 12pt / 16）。
 *  泊车走注册表全量渲，卡高不可知；「看得见卡头」是能保证的最小承诺——它告诉用户下面还有一张卡。 */
function parkedCardHeadDp(fontScale: FontScalePref): number {
  return 2 + 24 + scale(16, 'line', fontScale)
}

/** 行车档下该档「必须一眼看得见」的内容之和（dp）。逐项累加，不是拍的数。
 *  `orb` 是层内大球直径：默认行车的 120，B5-15 的球降级会把 88 传进来（最小高跟着降）。 */
export function drivingSheetMinDp(
  detent: SheetDetent,
  split: boolean,
  fontScale: FontScalePref,
  orb: number = SHEET_ORB.driving,
): number {
  // B5-12：把手带（`minHeight` = 目标高，它就是层内那枚 ≥56dp 演员）+ 内容区上下 padding
  const chrome = scale(TARGET.driving, 'target', fontScale) + SCROLL_PAD_DP
  // 球列：球 + gap + 胶囊一行（body 15pt）
  const orbCol = orb + GAP_DP + scale(20, 'line', fontScale)
  // 该档主体：0.78 = 压缩卡；0.62 = 回答两行（行车 18pt / lineHeight 28）；0.4 = 只有球列
  const body =
    detent === 0.78 ? cardMinDp(fontScale) : detent === 0.62 ? 2 * scale(28, 'line', fontScale) : 0
  if (split) return chrome + Math.max(orbCol, body) // 横屏 40:60：两列并排，不相加
  return chrome + (body ? orbCol + GAP_DP + body : orbCol)
}

/** 泊车档「必须一眼看得见」的内容之和（dp）。结构与行车档同一条，常量取泊车的：
 *  把手带 48、球 88、回答 16pt / lineHeight 24；0.78 档的主体 = 回答两行 + 卡头。 */
export function parkedSheetMinDp(detent: SheetDetent, split: boolean, fontScale: FontScalePref): number {
  const chrome = scale(TARGET.parked, 'target', fontScale) + SCROLL_PAD_DP
  const orbCol = SHEET_ORB.parked + GAP_DP + scale(20, 'line', fontScale)
  const answer2 = 2 * scale(24, 'line', fontScale)
  const body =
    detent === 0.78 ? answer2 + GAP_DP + parkedCardHeadDp(fontScale) : detent === 0.62 ? answer2 : 0
  if (split) return chrome + Math.max(orbCol, body)
  return chrome + (body ? orbCol + GAP_DP + body : orbCol)
}

/** 层内大球直径（dp）：行车 120，但容器连 0.4 档最小高都装不下时降到泊车的 88
 *  （缺陷 A 横屏半的**最后一道保险**）。在它之前的 lever：底栏撤掉（B5-12）、driving-landscape
 *  隐藏 chips 且语音层覆盖整列（B5-15）——都做完仍装不下才降球。
 *  ⚠ 阈值取 **0.4 档**（最小的那一档）：0.4 都装不下才谈降级，别的档只会更装不下。 */
export function sheetOrbDp(i: {
  containerH: number
  driving: boolean
  split: boolean
  fontScale: FontScalePref
}): number {
  if (!i.driving) return SHEET_ORB.parked
  return drivingSheetMinDp(0.4, i.split, i.fontScale, SHEET_ORB.driving) <= i.containerH
    ? SHEET_ORB.driving
    : SHEET_ORB.parked
}

/**
 * 语音层目标高度（dp）：比例与该档最小高取大，再 clamp 回记录区。行车 / 泊车走同一条式子，
 * 只是最小高的常量不同（`drivingSheetMinDp` / `parkedSheetMinDp`）。
 * clamp 是硬的：宁可占满记录区，也不许返回大于容器的值（会顶出屏外）。
 */
export function sheetHeightDp(i: {
  detent: SheetDetent
  /** 记录区高度（`ChatScreen` 的 listHeight），**不是屏高** */
  containerH: number
  driving: boolean
  /** `layout.mode === 'driving-landscape'`（§6 横屏 40:60） */
  split: boolean
  fontScale: FontScalePref
}): number {
  const byRatio = Math.round(i.containerH * i.detent)
  // B5-15：球降了最小高也跟着降——否则「降球」只改了渲染、判据仍按 120 要空间，两边对不上
  const floor = i.driving
    ? drivingSheetMinDp(i.detent, i.split, i.fontScale, sheetOrbDp(i))
    : parkedSheetMinDp(i.detent, i.split, i.fontScale)
  return Math.min(i.containerH, Math.max(byRatio, floor))
}
