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
//     （假设了当时的 detent 档位）。
//     ⚠ 外屏横那个后来被 B4 §6.4 **直接量到了：98.67dp**（不是 192，差一倍）。下面的 `OUTER_LANDSCAPE`
//     仍留 192 是因为几条老用例的读数绑在它上面（换数就不是同一条读数了）；**B5-15 的新用例一律用
//     98.67**。要用外屏横的真实空间做判断时看 98.67，别看 192。
//
// **2026-09-17（设计 §2）**：球与胶囊成了固定头区、转写 / 思考 / 回答进跟底的内容区 ⇒ 下限改按
// 「chrome（把手带 + padding 32 + **渐隐 24**）+ 头区（球 + 胶囊）+ 该档该看见的内容」：
//   0.4 = 转写 2 行 + 思考行 14；0.62 = 转写 1 行 + 回答 3 行；0.78 = 行车压缩卡 / 泊车 0.62 主体 + 卡头；
//   terse（行车回落）主体 0。三档下限单调不减。老用例里绑着旧数（240 / 200 / 447…）的期望按新清单换锚，
//   每处换锚都在用例里写明旧数与新数，别拿 09-11 的读数对。
import type { SheetDetent } from '@/core/presence/presence'
import { drivingSheetMinDp, parkedSheetMinDp, sheetHeightDp, sheetOrbDp } from '@/ui/layout/sheetHeight'

// ── 内容清单（独立于实现，逐条注明出处）──
// ⚠ B5-12（泓舟 B4 真机轮原话①）：底栏「收起 / 打断」整段撤掉，收起改为**顶缘把手带**下拖/轻点。
//   把手带接替 `voice-sheet-collapse` 的 §6「目标 ≥56dp」演员身份（testID 沿用）⇒ 清单里
//   原来的「把手 12 + 底栏 17 + 键 56」三项合并成一项「把手带 = 目标高」。
const SCROLL_PAD = 32 // 头区 paddingTop 16 + 内容区 paddingBottom 16
const FADE = 24 // 底缘渐隐（打磨批 A / P06）：内容区多留的 24dp——跟底后最新一行必须在它之上
const BTN = 56 // §6「目标 ≥56dp」：顶缘把手带 voice-sheet-collapse 的 minHeight（TARGET.driving）
/** 把手带（56，`minHeight` 不跟着 flex 缩）+ padding（32）+ 渐隐（24）。
 *  **层高低于它，收起演员与内容就一起装不下**——B4 实测 77dp 的层里 voice-sheet-collapse 被
 *  flex 压成 53.0dp < 56。 */
const CHROME = BTN + SCROLL_PAD + FADE
const ORB = 120 // §6 行车档层内大球
const ORB_PARKED = 88 // §6 泊车层内大球（B5-15 的球降级落到它）
const CAPSULE = 20 // 胶囊一行（body 15pt）
const GAP = 12 // 组内 / 组间 gap
const TRANSCRIPT_L = 28 // 转写一行（20pt / lineHeight 28）
const THINK = 14 // 思考三点一行：6dp 点 + paddingVertical 4×2（固定 dp）
const ANSWER_L = 28 // 回答一行（行车 18pt / lineHeight 28）
const CARD_SHELL = 2 + 24 + 4 * 8 // CardShell：边框 1×2 + padding 12×2 + 五个孩子之间四个 gap 8
const CARD = CARD_SHELL + 16 + 25 + 40 + BTN // 压缩卡：壳 + 类型行 + 标题 + ≤2 字段 + 主按钮 = 195

/** 大字档换算：scale(_, 'text'|'line', 'large') = round(x1.15)；scale(_, 'target', 'large') = round(x1.1) */
const L = (n: number) => Math.round(n * 1.15)
const T = (n: number) => Math.round(n * 1.1)

/** 该档「必须一眼看得见」的东西之和（竖排：相加；横屏 split：头区与内容区并排取 max）。
 *  固定 dp（把手 / padding / 渐隐 / gap / CardShell 的边框与内边距 / 球直径 / 思考行）不跟字号走，文字与目标跟。 */
const need = (detent: SheetDetent, split: boolean, large = false, terse = false): number => {
  const btn = large ? T(BTN) : BTN
  const chrome = btn + SCROLL_PAD + FADE
  const orbCol = ORB + GAP + (large ? L(CAPSULE) : CAPSULE)
  const card = CARD_SHELL + (large ? L(16) : 16) + (large ? L(25) : 25) + 2 * (large ? L(20) : 20) + btn
  const tl = large ? L(TRANSCRIPT_L) : TRANSCRIPT_L
  const al = large ? L(ANSWER_L) : ANSWER_L
  const body = terse ? 0 : detent === 0.78 ? card : detent === 0.62 ? tl + GAP + 3 * al : 2 * tl + GAP + THINK
  return chrome + (split ? Math.max(orbCol, body) : body ? orbCol + GAP + body : orbCol)
}

const ratio = (h: number, d: SheetDetent) => Math.round(h * d)
const call = (containerH: number, detent: SheetDetent, o: { driving?: boolean; split?: boolean; terse?: boolean } = {}) =>
  sheetHeightDp({ containerH, detent, driving: o.driving ?? true, split: o.split ?? false, fontScale: 'normal', terse: o.terse })

const OUTER_PORTRAIT = 578.67 // 实测（见头注）
const OUTER_LANDSCAPE = 192
const INNER = 573
const DETENTS: SheetDetent[] = [0.4, 0.62, 0.78]

// ── 症状一：外屏横，收起演员装不下 ────────────────────────────────────
test('缺陷 A 症状一：外屏横 192dp / 0.4 ⇒ 层高够一条 56dp 把手带 + padding + 渐隐', () => {
  // 纯比例给的是 77dp，连 112dp 的 chrome（把手带 56 + padding 32 + 渐隐 24）都装不下
  expect(ratio(OUTER_LANDSCAPE, 0.4)).toBeLessThan(CHROME)
  expect(call(OUTER_LANDSCAPE, 0.4, { split: true })).toBeGreaterThanOrEqual(CHROME)
})

test('缺陷 A 症状一：内容需求超过记录区时，层占满记录区（不是超出去）', () => {
  // 192dp 的记录区装不下 264dp 的需求 ⇒ 只能占满；**不许返回大于容器的值**（会溢出屏）
  expect(need(0.4, true)).toBeGreaterThan(OUTER_LANDSCAPE)
  expect(call(OUTER_LANDSCAPE, 0.4, { split: true })).toBe(OUTER_LANDSCAPE)
})

// ── 症状二：内屏，一屏一卡被裁出可视区 ────────────────────────────────
test('缺陷 A 症状二：内屏 573dp / 0.78 ⇒ 压缩卡装得下', () => {
  // 换锚记录：B4 时纯比例 447 < 下限 476；B5-12 撤底栏后下限 447 与比例逐 dp 相等；
  // 2026-09-17 chrome 加了渐隐 24 ⇒ 下限 **471 > 447**，这一档在内屏上重新由下限托住。
  expect(call(INNER, 0.78)).toBe(need(0.78, false))
  expect(call(INNER, 0.78)).toBeGreaterThanOrEqual(need(0.78, false))
  expect(call(500, 0.78)).toBe(need(0.78, false)) // round(500×0.78)=390 < 471 ⇒ 绑下限
  expect(call(500, 0.78)).toBeGreaterThan(ratio(500, 0.78))
})

test('缺陷 A 症状二：0.78 的最小高比 0.62 恰好多一张压缩卡、少「转写 1 行 + 回答 3 行」', () => {
  // 直接比**最小高**：拿 sheetHeightDp 的差去减会量到「比例与下限的混合」，不是下限的构成。
  expect(drivingSheetMinDp(0.78, false, 'normal') - drivingSheetMinDp(0.62, false, 'normal')).toBe(
    CARD - (TRANSCRIPT_L + GAP + 3 * ANSWER_L),
  )
})

// ── 回归护栏：主形态读数（换锚见头注）────────────────────────────────
test('外屏竖（实测 578.67dp）行车档：三档都由下限托住（120 球 + 18pt 回答本来就比比例高）', () => {
  // 真机 A/B（2026-09-03，角色 C）：行车档 ON 时容器 578.67 与 544.67 两种情况下层高**都是 269.0dp**
  // ——容器差 34dp 而层高不动，纯比例做不到这件事 ⇒ 绑的是下限。
  // 换锚记录：B4 chrome 117 ⇒ 269；B5-12 撤底栏 ⇒ 240；**2026-09-17 头区 + 转写 2 行 + 思考行 + 渐隐 ⇒ 358**。
  // 0.62 / 0.78 原来走比例（359 / 451），现在下限 400 / 471 压过比例。下一轮真机量到的应是 358 / 400 / 471。
  expect(call(OUTER_PORTRAIT, 0.4)).toBe(need(0.4, false))
  expect(call(OUTER_PORTRAIT, 0.4)).toBeGreaterThan(ratio(OUTER_PORTRAIT, 0.4))
  expect(call(OUTER_PORTRAIT, 0.62)).toBe(need(0.62, false))
  expect(call(OUTER_PORTRAIT, 0.62)).toBeGreaterThan(ratio(OUTER_PORTRAIT, 0.62))
  expect(call(OUTER_PORTRAIT, 0.78)).toBe(need(0.78, false))
  expect(call(OUTER_PORTRAIT, 0.78)).toBeGreaterThan(ratio(OUTER_PORTRAIT, 0.78))
})

test('设计 §2 的主力机读数表（normal 字号）：泊车 318 / 359 / 451，行车 358 / 400 / 471', () => {
  // 泊车只有 0.4 档变（231 → 318：识别 / 思考态要装下转写两行 + 思考行 + 渐隐），0.62 / 0.78 仍走比例逐 dp 不变
  expect(call(OUTER_PORTRAIT, 0.4, { driving: false })).toBe(318)
  expect(call(OUTER_PORTRAIT, 0.62, { driving: false })).toBe(359)
  expect(call(OUTER_PORTRAIT, 0.78, { driving: false })).toBe(451)
  expect(call(OUTER_PORTRAIT, 0.4)).toBe(358)
  expect(call(OUTER_PORTRAIT, 0.62)).toBe(400)
  expect(call(OUTER_PORTRAIT, 0.78)).toBe(471)
})

test('容器变了而下限没变时，层高不跟着容器动——真机 A/B 的判据形式', () => {
  // 这条才是「绑的是下限不是比例」的判别式：两个不同容器给出同一个层高。
  // 真机读到的正是这一对（578.67 与 544.67 都给 269.0dp）。
  expect(call(578.67, 0.4)).toBe(call(544.67, 0.4))
  expect(ratio(578.67, 0.4)).not.toBe(ratio(544.67, 0.4)) // 纯比例两者必然不同
})

test('泊车阴性：比例高过下限的档逐 dp 走比例（真机 568.33dp / 0.62 ⇒ 352）', () => {
  // 换锚记录：09-11 这条钉的是 0.4 档 227（那时泊车 0.4 下限 200 < 227）；现在 0.4 下限 318 压过比例，
  // 换 0.62 档做阴性：比例 352 > 下限 348 ⇒ 走比例
  expect(call(568.33, 0.62, { driving: false })).toBe(352)
  expect(ratio(568.33, 0.62)).toBe(352)
})

// ── 泊车档下限（2026-09-11 起有；2026-09-17 改构成）────────────────────────────
// 泊车原来是纯比例：矮容器上 0.4 × 容器装不下「把手带 48 + padding 32 + 球 88 + 胶囊」，球被层底裁掉。
// 与行车档同一条式子 `min(容器, max(比例, 下限))`，常量取泊车的。下面的清单同样独立于实现列一遍。
const BTN_PARKED = 48 // TARGET.parked：把手带 minHeight
const ANSWER_L_PARKED = 24 // 回答一行（泊车 16pt / lineHeight 24）
const CARD_HEAD = 2 + 24 + 16 // 0.78 档：CardShell 边框 + padding + 类型行（看得见卡头 = 知道下面还有卡）
const needParked = (detent: SheetDetent, split: boolean, large = false, terse = false): number => {
  const chrome = (large ? T(BTN_PARKED) : BTN_PARKED) + SCROLL_PAD + FADE
  const orbCol = ORB_PARKED + GAP + (large ? L(CAPSULE) : CAPSULE)
  const tl = large ? L(TRANSCRIPT_L) : TRANSCRIPT_L
  const answer = tl + GAP + 3 * (large ? L(ANSWER_L_PARKED) : ANSWER_L_PARKED)
  const head = 2 + 24 + (large ? L(16) : 16)
  const body = terse ? 0 : detent === 0.78 ? answer + GAP + head : detent === 0.62 ? answer : 2 * tl + GAP + THINK
  return chrome + (split ? Math.max(orbCol, body) : body ? orbCol + GAP + body : orbCol)
}

test('泊车档下限的数：0.4 = 318 / 0.62 = 348 / 0.78 = 402（normal 字号，竖排；09-11 是 200 / 260 / 314）', () => {
  const base = BTN_PARKED + SCROLL_PAD + FADE + ORB_PARKED + GAP + CAPSULE // chrome + 头区 = 224
  expect(parkedSheetMinDp(0.4, false, 'normal')).toBe(base + GAP + 2 * TRANSCRIPT_L + GAP + THINK)
  expect(parkedSheetMinDp(0.4, false, 'normal')).toBe(318)
  expect(parkedSheetMinDp(0.62, false, 'normal')).toBe(base + GAP + TRANSCRIPT_L + GAP + 3 * ANSWER_L_PARKED)
  expect(parkedSheetMinDp(0.62, false, 'normal')).toBe(348)
  expect(parkedSheetMinDp(0.78, false, 'normal')).toBe(348 + GAP + CARD_HEAD)
  expect(parkedSheetMinDp(0.78, false, 'normal')).toBe(402)
})

test('泊车档：主力机（外屏竖实测 578.67）0.62 / 0.78 比例高于下限 ⇒ 读数逐 dp 不变；0.4 由下限托住', () => {
  for (const d of [0.62, 0.78] as SheetDetent[]) {
    expect(ratio(OUTER_PORTRAIT, d)).toBeGreaterThan(needParked(d, false))
    expect(call(OUTER_PORTRAIT, d, { driving: false })).toBe(ratio(OUTER_PORTRAIT, d))
  }
  expect(ratio(OUTER_PORTRAIT, 0.4)).toBeLessThan(needParked(0.4, false))
  expect(call(OUTER_PORTRAIT, 0.4, { driving: false })).toBe(needParked(0.4, false))
})

test('泊车档：矮容器由下限托住——400dp 记录区 0.4 档给 318 不是 160；再矮就 clamp 到容器', () => {
  expect(ratio(400, 0.4)).toBe(160)
  expect(call(400, 0.4, { driving: false })).toBe(needParked(0.4, false))
  expect(call(400, 0.4, { driving: false })).toBeGreaterThan(ratio(400, 0.4))
  // 外屏横实测记录区 98.67：连下限都装不下 ⇒ 占满记录区，不许超出
  expect(call(98.67, 0.4, { driving: false, split: true })).toBe(98.67)
  // 老的推导容器 192 上 0.62 档：比例 119 < 下限 348 ⇒ 占满 192
  expect(call(OUTER_LANDSCAPE, 0.62, { driving: false })).toBe(OUTER_LANDSCAPE)
})

test('泊车档：容器变了而下限没变 ⇒ 层高不跟着容器动（与行车档同一判别式）', () => {
  expect(call(400, 0.4, { driving: false })).toBe(call(450, 0.4, { driving: false }))
  expect(ratio(400, 0.4)).not.toBe(ratio(450, 0.4))
})

test('泊车档最小高的构成：三档 × split × 两个字号档 × terse，逐项与清单对齐', () => {
  for (const d of DETENTS) {
    for (const split of [false, true]) {
      expect(parkedSheetMinDp(d, split, 'normal')).toBe(needParked(d, split))
      expect(parkedSheetMinDp(d, split, 'large')).toBe(needParked(d, split, true))
      expect(parkedSheetMinDp(d, split, 'normal', true)).toBe(needParked(d, split, false, true))
    }
  }
})

test('永远不超过记录区高度（三容器 × 三档 × 行车/泊车 × split × terse）', () => {
  for (const h of [OUTER_PORTRAIT, OUTER_LANDSCAPE, INNER, 0]) {
    for (const d of DETENTS) {
      for (const driving of [true, false]) {
        for (const split of [true, false]) {
          for (const terse of [true, false]) {
            expect(sheetHeightDp({ containerH: h, detent: d, driving, split, fontScale: 'normal', terse })).toBeLessThanOrEqual(h)
          }
        }
      }
    }
  }
})

// ── 2026-09-17：三档单调 + terse ─────────────────────────────────────
test('下限随档位单调不减（行车 / 泊车 × split × 两个字号档）——矮容器上「回答到了、层反而缩」不允许', () => {
  for (const driving of [true, false]) {
    for (const split of [false, true]) {
      for (const fs of ['normal', 'large'] as const) {
        const f = (d: SheetDetent) => (driving ? drivingSheetMinDp(d, split, fs) : parkedSheetMinDp(d, split, fs))
        expect(f(0.62)).toBeGreaterThanOrEqual(f(0.4))
        expect(f(0.78)).toBeGreaterThanOrEqual(f(0.62))
      }
    }
  }
})

test('0.4 档主体 = 识别 / 思考态该看见的：转写两行 + 思考行——去掉任何一项都对不上清单', () => {
  const headOnly = CHROME + ORB + GAP + CAPSULE
  expect(drivingSheetMinDp(0.4, false, 'normal')).toBe(headOnly + GAP + 2 * TRANSCRIPT_L + GAP + THINK)
  // 大字档：转写行跟字号走（28 → 32），思考行不跟
  expect(drivingSheetMinDp(0.4, false, 'large') - drivingSheetMinDp(0.4, false, 'normal')).toBe(
    (T(BTN) - BTN) + (L(CAPSULE) - CAPSULE) + 2 * (L(TRANSCRIPT_L) - TRANSCRIPT_L),
  )
})

test('terse（行车答后回落，内容区不渲染）：主体 0，只剩 chrome + 头区；泊车不回落但式子同样成立', () => {
  const headOnly = CHROME + ORB + GAP + CAPSULE
  for (const d of DETENTS) {
    expect(drivingSheetMinDp(d, false, 'normal', ORB, true)).toBe(headOnly)
    expect(drivingSheetMinDp(d, true, 'normal', ORB, true)).toBe(headOnly)
  }
  // 主力机行车档 0.4 回落：比例 231 < 264 ⇒ 层高 264（不是识别态的 358）——回落时不给转写留一块空白
  expect(call(OUTER_PORTRAIT, 0.4, { terse: true })).toBe(headOnly)
  expect(call(OUTER_PORTRAIT, 0.4, { terse: true })).toBeLessThan(call(OUTER_PORTRAIT, 0.4))
  // 泊车路径同一入参：主体 0
  expect(parkedSheetMinDp(0.4, false, 'normal', true)).toBe(BTN_PARKED + SCROLL_PAD + FADE + ORB_PARKED + GAP + CAPSULE)
})

// ── 横屏 40:60：头区在左、内容在右，最小高取 max 不是相加 ───────────────────
test('split（§6 横屏 40:60）：两列取 max 不相加 ⇒ 0.78 最小高正好少一个头区 + 一个 gap', () => {
  // 竖排 = chrome + 头区 + gap + 卡；split = chrome + max(头区, 卡) = chrome + 卡
  expect(drivingSheetMinDp(0.78, false, 'normal') - drivingSheetMinDp(0.78, true, 'normal')).toBe(
    ORB + GAP + CAPSULE + GAP,
  )
  expect(drivingSheetMinDp(0.78, true, 'normal')).toBe(CHROME + CARD)
  // 0.4 / 0.62 的主体都比头区（152）矮 ⇒ split 下限 = chrome + 头区，两档相等
  expect(drivingSheetMinDp(0.4, true, 'normal')).toBe(CHROME + ORB + GAP + CAPSULE)
  expect(drivingSheetMinDp(0.62, true, 'normal')).toBe(CHROME + ORB + GAP + CAPSULE)
})

// ── 大字档：最小高跟着 scale() 长，不是写死的 dp ───────────────────────
test('最小高的构成：三档 × split × 两个字号档，逐项与内容清单对齐', () => {
  // ⚠ 这一条的前两版都是废的，两次都是反向验证抓出来的：
  //  · v1 拿 sheetHeightDp(573, 0.78) 比 normal/large ⇒ 同时对「下限在不在」敏感（M4 把下限
  //    压到比例线以下，两档一起落回同一个比例值，断言退化成「447 > 447」恒假）；
  //  · v2 改比 drivingSheetMinDp 的大小 ⇒ **只要任何一项跟着 scale 长就绿**，对「哪些项跟着长」
  //    零敏感（M5 把 chrome 的按钮高写死 dp，落盘了却一条没红）。
  // 逐项等值才钉得住构成：固定 dp 不跟字号走、文字与触控目标跟。
  for (const d of DETENTS) {
    for (const split of [false, true]) {
      expect(drivingSheetMinDp(d, split, 'normal')).toBe(need(d, split))
      expect(drivingSheetMinDp(d, split, 'large')).toBe(need(d, split, true))
    }
  }
})

// ── B5-15 缺陷 A 横屏半：球降级判据 ────────────────────────────────────
// lever 顺序（做完一条量一次，装得下就不做下一条）：
//   ① 底栏撤掉（B5-12）：0.4 档最小高 269 → 240；外屏横记录区实测 **98.67dp**（B4 §6.4 直接量的，
//      不是 192 那个推导值）⇒ 仍装不下 ⇒ 要 lever ②；
//   ② driving-landscape 隐藏 chips + 语音层覆盖**整列**（记录区 + Composer）⇒ 容器从记录区高换成整列高；
//   ③ 仍装不下 ⇒ `sheetOrbDp` 把球降到泊车的 88。**判据兜底，不是主修法**。
// 2026-09-17 换锚：split 0.4 档最小高 = chrome 112 + 头区 152 = **264**（渐隐进了 chrome）；Xiaomi 外屏横整列 234 仍在阈值之下 ⇒ 88。
describe('B5-15 球降级：只在「全部 lever 之后仍装不下」时起作用', () => {
  test('容器装得下 0.4 档最小高 ⇒ 120（外屏竖实测 578.67）', () => {
    expect(sheetOrbDp({ containerH: OUTER_PORTRAIT, driving: true, split: false, fontScale: 'normal' })).toBe(ORB)
  })

  test('外屏横实测记录区 98.67 ⇒ 装不下 ⇒ 88；泊车永远 88', () => {
    expect(sheetOrbDp({ containerH: 98.67, driving: true, split: true, fontScale: 'normal' })).toBe(ORB_PARKED)
    expect(sheetOrbDp({ containerH: 98.67, driving: false, split: true, fontScale: 'normal' })).toBe(ORB_PARKED)
  })

  test('阈值就是 0.4 档最小高本身：恰好装得下 ⇒ 120，差 1dp ⇒ 88（两个字号档各验一次）', () => {
    for (const fs of ['normal', 'large'] as const) {
      const floor = need(0.4, true, fs === 'large')
      expect(sheetOrbDp({ containerH: floor, driving: true, split: true, fontScale: fs })).toBe(ORB)
      expect(sheetOrbDp({ containerH: floor - 1, driving: true, split: true, fontScale: fs })).toBe(ORB_PARKED)
    }
  })

  test('球降了最小高也跟着降——差额恰是两个球径之差，不是别的东西', () => {
    expect(drivingSheetMinDp(0.4, true, 'normal', ORB) - drivingSheetMinDp(0.4, true, 'normal', ORB_PARKED)).toBe(
      ORB - ORB_PARKED,
    )
  })

  test('sheetHeightDp 把降级后的球传下去：装不下的容器上层高按 88 的最小高算', () => {
    // 98.67 的容器：120 球要 264、88 球要 232，两者都 > 98.67 ⇒ 层高仍被 clamp 到容器；
    // 但**判据链要通**——换一个夹在两者之间的容器（250）就能看出球降级真的改变了返回值。
    expect(sheetHeightDp({ containerH: 250, detent: 0.4, driving: true, split: true, fontScale: 'normal' })).toBe(
      need(0.4, true) - (ORB - ORB_PARKED),
    )
    expect(sheetHeightDp({ containerH: 250, detent: 0.4, driving: true, split: true, fontScale: 'normal' })).toBeLessThanOrEqual(250)
  })
})
