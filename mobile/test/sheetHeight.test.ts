// mobile/test/sheetHeight.test.ts
// B4-13 缺陷 A：`sheetDetent` 是**比例**，表达不了「内容有固有最小高」。
// 本文件钉的是「**装不装得下**」，不是「层高等于某个数」——所以下面的内容清单
// 照 §6 与 `VoiceSheet.tsx` 的排版**独立列一遍**，不 import 实现的常量：
// 拿实现的常量去断言实现，等于什么都没验（B1 第 1 批坑③「期望值恰等于初值」的同一处陷阱）。
//
// 三个容器高都是**记录区高度**（`listHeight`，不是屏高）。⚠ 只有第一个是直接量到的：
//   外屏竖 **578.67dp** —— 2026-09-03 真机**直接实测**：uiautomator 里那个 [0,313][1080,2049] 的
//     容器节点（顶接头栏底 313、底与 voice-sheet 底重合），1736px @480dpi ÷ 3。
//     ⛔ 本文件第一版写的 728dp 是**估值冒充实测**（840 屏高减估算 chrome），据它断言「外屏竖三档
//     不受下限影响」——实测下来 0.4 与 0.78 两档都受下限影响。估算不能进判据。
//   外屏横 192dp / 内屏 573dp —— **推导值不是实测**：由 §6.3 记的层高 77dp@0.4 与 447dp@0.78 反推
//     （假设了当时的 detent 档位）。真机直接量这两个要转屏/展开，归 T13 余格。
import type { SheetDetent } from '@/core/presence/presence'
import { drivingSheetMinDp, sheetHeightDp } from '@/ui/layout/sheetHeight'

// ── 内容清单（独立于实现，逐条注明出处）──
// ⚠ B5-12（泓舟 B4 真机轮原话①）：底栏「收起 / 打断」整段撤掉，收起改为**顶缘把手带**下拖/轻点。
//   把手带接替 `voice-sheet-collapse` 的 §6「目标 ≥56dp」演员身份（testID 沿用）⇒ 清单里
//   原来的「把手 12 + 底栏 17 + 键 56」三项合并成一项「把手带 = 目标高」，chrome 117 → 88。
const SCROLL_PAD = 32 // ScrollView contentContainerStyle padding 16（上 + 下）
const BTN = 56 // §6「目标 ≥56dp」：顶缘把手带 voice-sheet-collapse 的 minHeight（TARGET.driving）
/** 把手带（56，`minHeight` 不跟着 flex 缩）+ 内容区上下 padding（32）。
 *  **层高低于它，收起演员与内容就一起装不下**——B4 实测 77dp 的层里 voice-sheet-collapse 被
 *  flex 压成 53.0dp < 56；底栏撤掉后压不着把手带了，但 88 以下内容区仍只剩负空间。 */
const CHROME = BTN + SCROLL_PAD
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
  const chrome = btn + SCROLL_PAD // B5-12：把手带（= 目标高）+ 内容区 padding
  const orbCol = ORB + GAP + (large ? L(CAPSULE) : CAPSULE)
  const card = CARD_SHELL + (large ? L(16) : 16) + (large ? L(25) : 25) + 2 * (large ? L(20) : 20) + btn
  const body = detent === 0.78 ? card : detent === 0.62 ? 2 * (large ? L(28) : 28) : 0
  return chrome + (split ? Math.max(orbCol, body) : body ? orbCol + GAP + body : orbCol)
}

const ratio = (h: number, d: SheetDetent) => Math.round(h * d)
const call = (containerH: number, detent: SheetDetent, o: { driving?: boolean; split?: boolean } = {}) =>
  sheetHeightDp({ containerH, detent, driving: o.driving ?? true, split: o.split ?? false, fontScale: 'normal' })

const OUTER_PORTRAIT = 578.67 // 实测（见头注）
const OUTER_LANDSCAPE = 192
const INNER = 573

// ── 症状一：外屏横，收起演员装不下 ────────────────────────────────────
test('缺陷 A 症状一：外屏横 192dp / 0.4 ⇒ 层高够一条 56dp 把手带 + padding', () => {
  // 纯比例给的是 77dp，连 88dp 的 chrome（把手带 56 + 内容区 padding 32）都装不下
  expect(ratio(OUTER_LANDSCAPE, 0.4)).toBeLessThan(CHROME)
  expect(call(OUTER_LANDSCAPE, 0.4, { split: true })).toBeGreaterThanOrEqual(CHROME)
})

test('缺陷 A 症状一：内容需求超过记录区时，层占满记录区（不是超出去）', () => {
  // 192dp 的记录区装不下 269dp 的需求 ⇒ 只能占满；**不许返回大于容器的值**（会溢出屏）
  expect(need(0.4, true)).toBeGreaterThan(OUTER_LANDSCAPE)
  expect(call(OUTER_LANDSCAPE, 0.4, { split: true })).toBe(OUTER_LANDSCAPE)
})

// ── 症状二：内屏，一屏一卡被裁出可视区 ────────────────────────────────
test('缺陷 A 症状二：内屏 573dp / 0.78 ⇒ 压缩卡装得下', () => {
  // ⚠ B5-12 之后这一格的事实变了，照实记：B4 时纯比例 447 < 下限 476 ⇒ 卡被裁出可视区，缺 **29dp**；
  // 撤掉的底栏 chrome（把手 12 + 底栏 17）**恰好就是那 29dp** ⇒ 下限降到 447，与纯比例**逐 dp 相等**。
  // 这一档因此从「靠下限撑」变成「纯比例刚好够、余量 0」——所以下面第二条在这个容器上已经**不具判别力**，
  // 判别力靠第三、四条：换一个更矮的容器，下限仍然是真的在兜底。
  expect(call(INNER, 0.78)).toBeGreaterThanOrEqual(need(0.78, false))
  expect(call(500, 0.78)).toBe(need(0.78, false)) // round(500×0.78)=390 < 447 ⇒ 绑下限
  expect(call(500, 0.78)).toBeGreaterThan(ratio(500, 0.78))
})

test('缺陷 A 症状二：0.78 的最小高比 0.62 恰好多一张压缩卡、少两行回答', () => {
  // 直接比**最小高**：在 573dp 上 0.62 的比例（355）已经压过它的下限（337），
  // 拿 sheetHeightDp 的差去减会量到「比例与下限的混合」，不是下限的构成。
  expect(drivingSheetMinDp(0.78, false, 'normal') - drivingSheetMinDp(0.62, false, 'normal')).toBe(
    CARD - ANSWER_2L,
  )
})

// ── 回归护栏：主形态与泊车路径一字不动 ────────────────────────────────
test('外屏竖（实测 578.67dp）：0.4 受下限、0.62 与 0.78 走比例（B5-12 之后）', () => {
  // 真机 A/B（2026-09-03，角色 C）：行车档 ON 时容器 578.67 与 544.67 两种情况下层高**都是 269.0dp**
  // ——容器差 34dp 而层高不动，纯比例做不到这件事 ⇒ 绑的是下限；行车档 OFF 时容器 568.33 ⇒ 层高
  // 227.0dp = round(568.33×0.4) 逐 dp 等于纯比例。
  // ⚠ 那组真机读数是 B4 的 chrome 117 下取的。B5-12 撤底栏后 chrome 88 ⇒ 0.4 档下限 269 → **240**
  // （仍压过比例 231，这一档不变）；0.78 档下限 476 → **447 < 比例 451** ⇒ **改走比例**（只差 4dp）。
  // 下一轮真机量到的 0.4 档层高应是 240 不是 269——**读数换锚了，别拿 B4 的 269 对**。
  expect(call(OUTER_PORTRAIT, 0.4)).toBe(need(0.4, false)) // 240 > round(231)
  expect(call(OUTER_PORTRAIT, 0.4)).toBeGreaterThan(ratio(OUTER_PORTRAIT, 0.4))
  expect(call(OUTER_PORTRAIT, 0.62)).toBe(ratio(OUTER_PORTRAIT, 0.62)) // 359 > 下限 308
  expect(call(OUTER_PORTRAIT, 0.78)).toBe(ratio(OUTER_PORTRAIT, 0.78)) // 451 > 下限 447
  expect(call(OUTER_PORTRAIT, 0.78)).toBeGreaterThanOrEqual(need(0.78, false)) // 走比例也仍装得下卡
})

test('容器变了而下限没变时，层高不跟着容器动——真机 A/B 的判据形式', () => {
  // 这条才是「绑的是下限不是比例」的判别式：两个不同容器给出同一个层高。
  // 真机读到的正是这一对（578.67 与 544.67 都给 269.0dp）。
  expect(call(578.67, 0.4)).toBe(call(544.67, 0.4))
  expect(ratio(578.67, 0.4)).not.toBe(ratio(544.67, 0.4)) // 纯比例两者必然不同
})

test('泊车阴性：同一屏行车档关 ⇒ 逐 dp 退回纯比例（真机 568.33dp ⇒ 227）', () => {
  expect(call(568.33, 0.4, { driving: false })).toBe(227)
  expect(ratio(568.33, 0.4)).toBe(227)
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
