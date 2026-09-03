// mobile/src/ui/layout/sheetHeight.ts
// 语音层高度判据（B4-13 缺陷 A / 方案 §6「一屏一卡」「目标 ≥56dp」）。
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
// 「最小高」的定义（**只给行车档**；泊车路径一字不动，返回值与原来逐字节相同）：
//   固定 chrome（把手 + ScrollView 上下 padding + 底栏含一枚 56dp 键）
//   + 球 + 胶囊（行车档任何一档都要一眼看得见）
//   + **该档的主体**（0.62 = 回答两行；0.78 = 压缩卡「标题 + ≤2 字段 + 主按钮」）。
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
const HANDLE_DP = 12 // 把手：marginTop 8 + height 4
const SCROLL_PAD_DP = 32 // ScrollView contentContainerStyle padding 16（上 + 下）
const FOOTER_PAD_DP = 17 // 底栏 paddingVertical 8×2 + borderTopWidth 1
const GAP_DP = 12 // 组内 / 组间 gap（非 split 时 ScrollView 的 gap 也是 12）

/** 压缩卡（`DrivingCardSummary` + `CardShell`）的最小高 */
function cardMinDp(fontScale: FontScalePref): number {
  const shell = 2 + 24 + 4 * 8 // 边框 1×2 + padding 12×2 + gap 8×4（类型行/标题/两字段/按钮之间）
  const typeRow = scale(16, 'line', fontScale) // CardShell 的类型行（caption 12pt）
  const title = scale(25, 'line', fontScale) // driving-card-title（h2 18pt）
  const fields = 2 * scale(20, 'line', fontScale) // ≤2 个字段（body 15pt）
  return shell + typeRow + title + fields + scale(TARGET.driving, 'target', fontScale)
}

/** 行车档下该档「必须一眼看得见」的内容之和（dp）。逐项累加，不是拍的数 */
export function drivingSheetMinDp(detent: SheetDetent, split: boolean, fontScale: FontScalePref): number {
  const chrome =
    HANDLE_DP + SCROLL_PAD_DP + FOOTER_PAD_DP + scale(TARGET.driving, 'target', fontScale)
  // 球列：球 + gap + 胶囊一行（body 15pt）
  const orbCol = SHEET_ORB.driving + GAP_DP + scale(20, 'line', fontScale)
  // 该档主体：0.78 = 压缩卡；0.62 = 回答两行（行车 18pt / lineHeight 28）；0.4 = 只有球列
  const body =
    detent === 0.78 ? cardMinDp(fontScale) : detent === 0.62 ? 2 * scale(28, 'line', fontScale) : 0
  if (split) return chrome + Math.max(orbCol, body) // 横屏 40:60：两列并排，不相加
  return chrome + (body ? orbCol + GAP_DP + body : orbCol)
}

/**
 * 语音层目标高度（dp）。泊车 = 原来的纯比例；行车 = 比例与最小高取大，再 clamp 回记录区。
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
  if (!i.driving) return byRatio
  return Math.min(i.containerH, Math.max(byRatio, drivingSheetMinDp(i.detent, i.split, i.fontScale)))
}
