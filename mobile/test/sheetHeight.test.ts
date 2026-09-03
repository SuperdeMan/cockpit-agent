// mobile/test/sheetHeight.test.ts
// B4-13 缺陷 A：`sheetDetent` 是**比例**，表达不了「内容有固有最小高」。
// 本文件钉的是「**装不装得下**」，不是「层高等于某个数」——所以下面的内容清单
// 照 §6 与 `VoiceSheet.tsx` 的排版**独立列一遍**，不 import 实现的常量：
// 拿实现的常量去断言实现，等于什么都没验（B1 第 1 批坑③「期望值恰等于初值」的同一处陷阱）。
//
// 三个容器高是 2026-09-03 真机实测的**记录区高度**（`listHeight`，不是屏高）：
//   外屏竖 728dp（1080×2520 @480 ⇒ 360×840dp 减顶栏/Composer）
//   外屏横 192dp（840×360dp；缺陷 A 症状一就出在这里：0.4×192 = 77dp）
//   内屏   573dp（2224×2488 @480 ⇒ 741×829dp，two-pane 的对话列；症状二：0.78×573 = 447dp）
import type { SheetDetent } from '@/core/presence/presence'
import { drivingSheetMinDp, sheetHeightDp } from '@/ui/layout/sheetHeight'

// ── 内容清单（独立于实现，逐条注明出处）──
const HANDLE = 12 // VoiceSheet 把手：marginTop 8 + height 4
const SCROLL_PAD = 32 // ScrollView contentContainerStyle padding 16（上 + 下）
const FOOTER_PAD = 17 // 底栏 paddingVertical 8×2 + borderTopWidth 1
const BTN = 56 // §6「目标 ≥56dp」：底栏那枚 voice-sheet-collapse（TARGET.driving）
/** 一枚 56dp 键 + 把手 + 底栏内边距：**层高低于它，RN 的 flex 收缩就会把按钮压小**
 *  （真机实测 77dp 的层 ⇒ voice-sheet-collapse 53.0dp < 56） */
const CHROME_HARD = HANDLE + FOOTER_PAD + BTN
const CHROME = CHROME_HARD + SCROLL_PAD
const ORB = 120 // §6 行车档层内大球（泊车 88）
const CAPSULE = 20 // 胶囊一行（body 15pt）
const GAP = 12 // 组内 / 组间 gap
const ANSWER_2L = 56 // 回答区两行（行车 18pt / lineHeight 28）
const CARD_SHELL = 2 + 24 + 4 * 8 // CardShell：边框 1×2 + padding 12×2 + 五个孩子之间四个 gap 8
const CARD = CARD_SHELL + 16 + 25 + 40 + BTN // 压缩卡：壳 + 类型行 + 标题 + ≤2 字段 + 主按钮 = 195

/** 大字档换算：scale(_, 'text'|'line', 'large') = round(x1.15)；scale(_, 'target', 'large') = round(x1.1) */
const L = (n: number) => Math.round(n * 1.15)
const T = (n: number) => Math.round(n * 1.1)

/** 该档「必须一眼看得见」的东西之和（竖排：相加；横屏 split：两列取 max）。
 *  固定 dp（把手 / padding / gap / CardShell 的边框与内边距 / 球直径）不跟字号走，文字与目标跟。 */
const need = (detent: SheetDetent, split: boolean, large = false): number => {
  const btn = large ? T(BTN) : BTN
  const chrome = HANDLE + SCROLL_PAD + FOOTER_PAD + btn
  const orbCol = ORB + GAP + (large ? L(CAPSULE) : CAPSULE)
  const card = CARD_SHELL + (large ? L(16) : 16) + (large ? L(25) : 25) + 2 * (large ? L(20) : 20) + btn
  const body = detent === 0.78 ? card : detent === 0.62 ? 2 * (large ? L(28) : 28) : 0
  return chrome + (split ? Math.max(orbCol, body) : body ? orbCol + GAP + body : orbCol)
}

const ratio = (h: number, d: SheetDetent) => Math.round(h * d)
const call = (containerH: number, detent: SheetDetent, o: { driving?: boolean; split?: boolean } = {}) =>
  sheetHeightDp({ containerH, detent, driving: o.driving ?? true, split: o.split ?? false, fontScale: 'normal' })

const OUTER_PORTRAIT = 728
const OUTER_LANDSCAPE = 192
const INNER = 573

// ── 症状一：外屏横，56dp 键被压到 53 ──────────────────────────────────
test('缺陷 A 症状一：外屏横 192dp / 0.4 ⇒ 层高够一枚 56dp 键 + 把手 + 底栏', () => {
  // 纯比例给的是 77dp，连 85dp 的硬 chrome 都装不下 ⇒ 按钮被 flex 压成 53
  expect(ratio(OUTER_LANDSCAPE, 0.4)).toBeLessThan(CHROME_HARD)
  expect(call(OUTER_LANDSCAPE, 0.4, { split: true })).toBeGreaterThanOrEqual(CHROME_HARD)
})

test('缺陷 A 症状一：内容需求超过记录区时，层占满记录区（不是超出去）', () => {
  // 192dp 的记录区装不下 269dp 的需求 ⇒ 只能占满；**不许返回大于容器的值**（会溢出屏）
  expect(need(0.4, true)).toBeGreaterThan(OUTER_LANDSCAPE)
  expect(call(OUTER_LANDSCAPE, 0.4, { split: true })).toBe(OUTER_LANDSCAPE)
})

// ── 症状二：内屏，一屏一卡被裁出可视区 ────────────────────────────────
test('缺陷 A 症状二：内屏 573dp / 0.78 ⇒ 压缩卡装得下', () => {
  expect(ratio(INNER, 0.78)).toBeLessThan(need(0.78, false)) // 447 < 476：今天装不下
  expect(call(INNER, 0.78)).toBeGreaterThanOrEqual(need(0.78, false))
})

test('缺陷 A 症状二：0.78 的最小高比 0.62 恰好多一张压缩卡、少两行回答', () => {
  // 直接比**最小高**：在 573dp 上 0.62 的比例（355）已经压过它的下限（337），
  // 拿 sheetHeightDp 的差去减会量到「比例与下限的混合」，不是下限的构成。
  expect(drivingSheetMinDp(0.78, false, 'normal') - drivingSheetMinDp(0.62, false, 'normal')).toBe(
    CARD - ANSWER_2L,
  )
})

// ── 回归护栏：主形态与泊车路径一字不动 ────────────────────────────────
test('外屏竖三档全部不受下限影响（比例本来就够）', () => {
  expect([call(OUTER_PORTRAIT, 0.4), call(OUTER_PORTRAIT, 0.62), call(OUTER_PORTRAIT, 0.78)]).toEqual([
    ratio(OUTER_PORTRAIT, 0.4),
    ratio(OUTER_PORTRAIT, 0.62),
    ratio(OUTER_PORTRAIT, 0.78),
  ])
})

test('泊车档：三个容器 × 三档一律等于纯比例（下限只给行车档）', () => {
  const detents: SheetDetent[] = [0.4, 0.62, 0.78]
  for (const h of [OUTER_PORTRAIT, OUTER_LANDSCAPE, INNER]) {
    for (const d of detents) {
      expect(call(h, d, { driving: false, split: false })).toBe(ratio(h, d))
      expect(call(h, d, { driving: false, split: true })).toBe(ratio(h, d))
    }
  }
})

test('永远不超过记录区高度（三容器 × 三档 × 行车/泊车 × split）', () => {
  const detents: SheetDetent[] = [0.4, 0.62, 0.78]
  for (const h of [OUTER_PORTRAIT, OUTER_LANDSCAPE, INNER, 0]) {
    for (const d of detents) {
      for (const driving of [true, false]) {
        for (const split of [true, false]) {
          expect(sheetHeightDp({ containerH: h, detent: d, driving, split, fontScale: 'normal' })).toBeLessThanOrEqual(h)
        }
      }
    }
  }
})

// ── 横屏 40:60：两列并排，最小高取 max 不是相加 ───────────────────────
test('split（§6 横屏 40:60）：两列取 max 不相加 ⇒ 0.78 最小高正好少一个球列 + 一个 gap', () => {
  // 竖排 = chrome + 球列 + gap + 卡；split = chrome + max(球列, 卡) = chrome + 卡
  expect(drivingSheetMinDp(0.78, false, 'normal') - drivingSheetMinDp(0.78, true, 'normal')).toBe(
    ORB + GAP + CAPSULE + GAP,
  )
  expect(drivingSheetMinDp(0.78, true, 'normal')).toBe(CHROME + CARD)
})

// ── 大字档：最小高跟着 scale() 长，不是写死的 dp ───────────────────────
test('最小高的构成：三档 × split × 两个字号档，逐项与内容清单对齐', () => {
  // ⚠ 这一条的前两版都是废的，两次都是反向验证抓出来的：
  //  · v1 拿 sheetHeightDp(573, 0.78) 比 normal/large ⇒ 同时对「下限在不在」敏感（M4 把下限
  //    压到比例线以下，两档一起落回同一个比例值，断言退化成「447 > 447」恒假）；
  //  · v2 改比 drivingSheetMinDp 的大小 ⇒ **只要任何一项跟着 scale 长就绿**，对「哪些项跟着长」
  //    零敏感（M5 把 chrome 的按钮高写死 dp，落盘了却一条没红）。
  // 逐项等值才钉得住构成：固定 dp 不跟字号走、文字与触控目标跟。
  const detents: SheetDetent[] = [0.4, 0.62, 0.78]
  for (const d of detents) {
    for (const split of [false, true]) {
      expect(drivingSheetMinDp(d, split, 'normal')).toBe(need(d, split))
      expect(drivingSheetMinDp(d, split, 'large')).toBe(need(d, split, true))
    }
  }
})
