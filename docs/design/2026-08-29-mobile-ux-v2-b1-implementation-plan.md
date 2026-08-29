# UX v2.1 · B1「在场与锚」实施计划（逐任务）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 状态：**已批准开工（泓舟 2026-08-29「开工，拆实施计划」）**
> 交付对象：`mobile/` 执行者（人或 Agent）
> 上游真相源：[`2026-08-29-mobile-ux-v2-presence-redesign.md`](2026-08-29-mobile-ux-v2-presence-redesign.md)（方案 v2.1；本计划只展开 **B1**，B2–B5 见其 §11.1）
> 纪律：沿用 [`2026-08-24-mobile-app-implementation-plan.md`](2026-08-24-mobile-app-implementation-plan.md) §0 接手须知 + §9 坑账；每任务「先测后码、一任务一提交」；**不动 `hmi/`、不动共享判据、不改编排核心**（唯一例外走 §10 的「共享模块有 bug」条款，本批没有）

**Goal:** 把 App 的「此刻在干嘛」收成一个多轴派生态 `PresenceSnapshot`，由光球（含三个新态）+ 一枚状态胶囊 + 一块承诺面 Focus Dock + 一张隐私栏承载；顺带落 token 层、Onboarding 上品牌、键盘避让读数、`/state-gallery` 取证屏与三条 Maestro 流。**全 JS，不重建 APK。**

**Architecture:** `core/presence/presence.ts` 是一个纯函数 `derivePresence(input) → PresenceSnapshot`（六个正交轴 + 一个视觉主态 `primary`），输入全部来自既有状态机（`SessionCore` store / 免唤醒 FSM / PTT / 设置 / 播报控制器 / 视觉抓帧），**不新增状态机、不改共享 FSM**。UI 侧 `usePresence()` 收集输入并调用它；`PresenceCapsule` 读 `capsule`、`FocusDock` 读 `commitment[] + degradation[]`、`AuroraOrb` 读 `primary`、顶栏读 `transport/privacy`。两个设置开关 `uxV2Presence / uxV2Dock`（缺省开）保留 v1 形态作回滚路径。

**Tech Stack:** React Native 0.86 / Expo 57 / expo-router / zustand / react-native-reanimated 4.5 / jest-expo（`mobile/test/**/*.test.ts`，纯逻辑测试）/ Maestro 2.9（`mobile/e2e/`）。

---

## 0. 接手须知（先读）

1. **开工前提**（不变）：`powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；Metro `cd mobile && npx expo start --dev-client`；真机 dev-client 已装（本批**不重建**，任何「要重建」的念头都说明走偏了——B1 零新原生依赖）。
2. **每个任务的顺序是固定的**：写失败测试 → 跑出红 → 最小实现 → 跑绿 → `tsc` → 提交。`mobile/` 的测试命令：`cd mobile && npx jest test/<file>.test.ts`（单文件）/ `npm test`（全量，当前 234 通过）/ `npm run typecheck`。
3. **提交只加自己的路径**：`git add -- <paths>` 再 `git commit`。共享工作树里可能有别的会话的改动（2026-08-29 当时是 `docs/design/2026-08-24-…`、`mobile/package.json`、`mobile/patches/`），**不要 `git add -A`**（坑账：共享树 `git add` 会扫进别人的改动）。
4. **真机取证一律截图**（`adb exec-out screencap -p -d <displayId>`，折叠屏 id 用 `dumpsys SurfaceFlinger --display-id` 取）；`uiautomator dump` 在有常驻动画的屏上不可信（坑账 §9.40/48）。
5. **三个结构性事实，写代码前记住**：
   - `Msg` 类型在 `hmi/src/types.ts`（共享，**不能加字段**）——任何「气泡上要多显示一个态」都走 `SessionState` 的并列字段（本计划的 `uncertainIds` 就是这么做的），或追加一条新消息。
   - `pendingOps` 的 TTL 是共享模块 `pendingOps.mjs::PENDING_TTL_MS = 300_000`——倒计时**只许读它**，UI 不另存时间。
   - 协议里**没有** `missing_slots`、没有 VAL 拒绝的结构化标记（2026-08-29 grep 全空）⇒ `DockItem.slot` 与 `Degradation.safety_blocked` 在 B1 **只有类型与画廊样本、没有生产产出方**；方案 §13 Q16 / 新增 Q19 挂账后端。不许在客户端用正则猜「这句是不是 VAL 拒绝」。
6. 方案里已裁决的默认值（Q1–Q18）不在本计划重议；本计划新增的实施判断集中在 §12。

---

## 1. 文件结构（先定边界，再拆任务）

### 新建

| 文件 | 职责 | 依赖 |
|---|---|---|
| `mobile/src/ui/tokens.ts` | 间距 / 圆角 / 字阶 / 动效 / 触控目标 / 材质三档 token + `scale()` | 无 RN import |
| `mobile/src/core/presence/presence.ts` | `PresenceSnapshot` 类型 + `derivePresence()` 纯函数 + 胶囊文案表 | `pendingOps.mjs`（只读 TTL） |
| `mobile/src/core/presence/commitment.ts` | `DockItem` 类型、稳定排序、钉住项选择、确认剩余时间 | 无 |
| `mobile/src/core/presence/fixtures.ts` | `presenceFixtures()`：每个 `primary` 与每种 `degradation` 至少一条样本 | presence.ts |
| `mobile/src/features/chat/usePresence.ts` | 收集输入（store / hf / ptt / settings / 播报 / 视觉 / 时钟）→ `derivePresence` | 上述 + 既有 hooks |
| `mobile/src/features/chat/PresenceCapsule.tsx` | 状态胶囊（读 `snapshot.capsule`） | tokens |
| `mobile/src/features/chat/FocusDock.tsx` | 承诺面（读 `commitment[]` / `degradation[]`），G0 实色 | tokens、commitment |
| `mobile/src/features/chat/PrivacyRail.tsx` | 隐私栏（读 `snapshot.privacy` + 最近激活日志），G0 | tokens |
| `mobile/src/core/presence/activityLog.ts` | 采集激活环形日志（20 条，内存，不上传） | 无 |
| `mobile/src/app/state-gallery.tsx` | 取证屏：逐样本渲光球 + 胶囊 + Dock + 隐私栏摘要；`?only=` 直达 | fixtures |
| `mobile/test/tokens.test.ts` `presence.test.ts` `commitment.test.ts` `presenceFixtures.test.ts` `activityLog.test.ts` | 纯逻辑守卫 | jest |
| `mobile/e2e/06-confirm-dock.yaml` `08-keyboard-no-hide.yaml` `09-state-gallery.yaml` | Maestro 扩流 | — |

### 修改

| 文件 | 改什么 | 为什么 |
|---|---|---|
| `mobile/src/ui/aurora/AuroraOrb.tsx` | `OrbState` 加 `attention / looking / muted`；琥珀环 / 一次白环 / 去饱和停旋 + 灰环；`dim` prop | 方案 §4.2 三新态，十条不变量内 |
| `mobile/src/core/session/store.ts` | `syncPruneTimer` 精确调度；剪枝到期时追加「确认已过期」消息；`queued` 计数；`uncertainIds` | 方案 §5.3 / §5.7 |
| `mobile/src/core/voice/speech.ts` | `speaking` 可订阅（`subscribeSpeaking`） | Presence 的 `agent.speaking` 需要一个不靠链式覆盖回调的来源 |
| `mobile/src/core/vision/frame.ts` | `capturing` 可订阅 | `looking` 态 |
| `mobile/src/core/voice/handsFree.ts` | 新增 deps `onBargeInDisabled(reason)` / `onPipelineDegraded(kind, msg)` | 结构化降级信号（§12.1），不再只靠 notice 字符串 |
| `mobile/src/features/chat/useHandsFree.ts` | 透出 `bargeInDisabled` / `pipelineDegraded` | 同上 |
| `mobile/src/features/chat/usePtt.ts` | `errorKind: 'permission' \| 'start' \| 'asr' \| ''` | `permission_denied` 降级项 |
| `mobile/src/core/settings/store.ts` | `uxV2Presence` / `uxV2Dock` / `deviceRole` 字段 | 回滚开关（§11.5）；身份轴占位 |
| `mobile/src/features/settings/SettingsScreen.tsx` | 「实验室」分区两开关 + `/state-gallery` 链接 | 同上 |
| `mobile/src/features/chat/ChatScreen.tsx` | 四条窄条 → 胶囊 + Dock；顶栏健康点 + 采集点 + 隐私栏入口；键盘避让 | 方案 §4.3 / §5.1 / §5.10 / §7.6 |
| `mobile/src/features/chat/Composer.tsx` | 光球读 `primary`；热区 56 / 球 44；删提示行 | §5.1 |
| `mobile/src/features/chat/MessageBubble.tsx` | Dock 开时气泡内不渲染确认按钮；`uncertain` 灰字 | §5.3 / §5.7 |
| `mobile/src/app/onboarding.tsx` | 上品牌（AuroraBackground + Glass + 光球 + 色板 + 安全区 + 权限用途文案） | §5.8 |
| `mobile/src/app/_layout.tsx` | 注册 `state-gallery` 路由 | — |
| `mobile/e2e/README.md`、`docs/design/2026-08-24-mobile-app-implementation-plan.md`（只加 §B1 实施记录指针）、`AGENTS.md` §4.1 | 记录 | 收口任务 |

---

## 2. 任务清单

### Task 1: token 层骨架（`ui/tokens.ts`）

**Files:**
- Create: `mobile/src/ui/tokens.ts`
- Test: `mobile/test/tokens.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/tokens.test.ts
// token 层（UX v2.1 §5.9）：数值逐值照 Figma A-1 设计系统；`scale()` 是「大字」档同时放大
// 文字 / 目标 / 行高的唯一入口——此前 Palette.font() 只放大文字，容器与热区不跟着长（P13）。
import { GLASS, MOTION, RADIUS, SPACE, TARGET, TYPE, scale } from '@/ui/tokens'

describe('tokens 数值照 A-1 设计系统', () => {
  test('4px 栅格与圆角阶', () => {
    expect(SPACE).toEqual([4, 8, 12, 16, 24, 32, 48])
    expect(RADIUS).toEqual({ sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 })
  })
  test('触控目标：泊车 48 / 行车 56（Guidelines :325-327）', () => {
    expect(TARGET).toEqual({ parked: 48, driving: 56 })
  })
  test('材质三档：G0 不透明不模糊；G2 只给光球与把手（§5.11）', () => {
    expect(GLASS.solid.blur).toBe(0)
    expect(GLASS.solid.opacity).toBeGreaterThanOrEqual(0.96)
    expect(GLASS.frosted.blur).toBeGreaterThan(0)
    expect(GLASS.reactive.blur).toBeGreaterThanOrEqual(GLASS.frosted.blur)
  })
  test('MOTION 含光球四态基准时长（毫秒）', () => {
    expect(MOTION.orbIdle).toBe(4000)
    expect(MOTION.fast).toBeLessThan(MOTION.base)
    expect(MOTION.base).toBeLessThan(MOTION.slow)
  })
})

describe('scale()：大字档同时放大文字、目标与行高', () => {
  test('normal 档原样', () => {
    expect(scale(15, 'text', 'normal')).toBe(15)
    expect(scale(48, 'target', 'normal')).toBe(48)
  })
  test('large 档：文字 ×1.15、目标 ×1.1、行高 ×1.15', () => {
    expect(scale(15, 'text', 'large')).toBe(17)
    expect(scale(48, 'target', 'large')).toBe(53)
    expect(scale(23, 'line', 'large')).toBe(26)
  })
  test('缺省档参数 = normal', () => {
    expect(scale(20, 'text')).toBe(20)
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/tokens.test.ts`
Expected: FAIL — `Cannot find module '@/ui/tokens'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/ui/tokens.ts
// 设计 token（UX v2.1 §5.9 / §5.11）。数值逐值照 Figma A-1 设计系统
// （docs/design/【新】座舱Agent-HMI-A-1 Design System.zip → guidelines/Guidelines.md
//  §间距 4px 栅格 / §圆角 / §字阶 / §触控目标 / §10 光球动效）。
// 色板仍在 theme.ts（Palette）；这里只放**尺寸、节律、材质档**，与色板分文件是因为
// 色板随深浅主题变、尺寸不随主题变。
// 使用纪律：**新组件必用；旧组件只在被触碰时顺手换**，不做全仓扫荡（34 个卡渲染器逐处改
// 是独立批，与 M3-V「等宽数字铁律」同一处置）。
import type { FontScalePref } from '../core/settings/store'

/** 4px 栅格 */
export const SPACE = [4, 8, 12, 16, 24, 32, 48] as const

export const RADIUS = { sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 } as const

/** 字阶（pt）。mono 用系统等宽——JetBrains Mono 未随 App 打包（M3-V 刻意不做） */
export const TYPE = { display: 32, h1: 24, h2: 18, body: 15, caption: 12, micro: 11, mono: 'monospace' } as const

/** 过渡与光球基准节律（ms）。光球各态的旋转/呼吸时长仍在 AuroraOrb.tsx（照 A-1 §10），
 *  这里只登记 idle 呼吸基准，供状态画廊与减少动效判断引用 */
export const MOTION = { fast: 120, base: 180, slow: 260, orbIdle: 4000 } as const

/** 触控目标（dp）：泊车 48 / 行车 56（Guidelines :325-327） */
export const TARGET = { parked: 48, driving: 56 } as const

/** 材质三档（方案 §5.11）。frosted/reactive 的 blur 在 B3 spike 前**不真的用**——
 *  RN 无 backdrop-filter，B1 的 G1 就是 theme.ts 的 glass（tint 版）。 */
export const GLASS = {
  /** G0 Solid/Safety：确认、错误、隐私说明、行车限制、压在不可控内容上的浮层 */
  solid: { blur: 0, opacity: 0.96 },
  /** G1 Frosted：顶栏、语音层外壳、舞台抽屉、Onboarding 容器、卡壳 */
  frosted: { blur: 28, tint: 0.58, border: 0.16 },
  /** G2 Reactive：只给光球、语音层把手、选中态 chip */
  reactive: { blur: 34, tint: 0.42, specular: 0.32 },
} as const

export type ScaleKind = 'text' | 'target' | 'line'

/** 「大字」档的唯一放大入口：文字 ×1.15、触控目标 ×1.1、行高 ×1.15；normal 原样。
 *  四舍五入到整 dp（RN 的 lineHeight/height 取小数会在 Android 上出现半像素缝）。 */
export function scale(size: number, kind: ScaleKind, pref: FontScalePref = 'normal'): number {
  if (pref !== 'large') return size
  const k = kind === 'target' ? 1.1 : 1.15
  return Math.round(size * k)
}
```

- [ ] **Step 4: 跑测试确认绿 + 类型检查**

Run: `cd mobile && npx jest test/tokens.test.ts && npm run typecheck`
Expected: PASS（7 tests）；`tsc` 0 error

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/ui/tokens.ts mobile/test/tokens.test.ts
git commit -m "feat(mobile): UX v2.1 B1-1 token 层骨架——间距/圆角/字阶/目标/材质三档 + scale()"
```

---

### Task 2: 承诺项与降级项的纯逻辑（`core/presence/commitment.ts`）

**Files:**
- Create: `mobile/src/core/presence/commitment.ts`
- Test: `mobile/test/commitment.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/commitment.test.ts
// Focus Dock 的承诺项（方案 §5.3 / §4.1 commitment 轴）：
//  · 排序稳定：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列；同类按最早到期在前
//  · 钉住项 = 排序后的第一项；其余只报个数（**不轮播**，评审 P1-2 采纳）
//  · 确认剩余时间**只读共享 TTL**（pendingOps.mjs::PENDING_TTL_MS），UI 不另存时间
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import {
  confirmRemainingMs,
  pinCommitment,
  sortCommitments,
  type DockItem,
} from '@/core/presence/commitment'

const NOW = 1_000_000

const confirm = (id: string, ts: number, risk: 'low' | 'high' = 'high'): DockItem => ({
  kind: 'confirm',
  id,
  summary: `动作 ${id}`,
  risk,
  expiresAt: ts + PENDING_TTL_MS,
})

describe('sortCommitments', () => {
  test('类别顺序：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列', () => {
    const items: DockItem[] = [
      { kind: 'queue', id: 'q', count: 2 },
      { kind: 'task', id: 't', label: '规划路线', startedAt: NOW - 9000 },
      { kind: 'slot', id: 's', missing: '你的位置' },
      confirm('c-low', NOW, 'low'),
      confirm('c-high', NOW, 'high'),
    ]
    expect(sortCommitments(items).map((i) => i.id)).toEqual(['c-high', 'c-low', 's', 't', 'q'])
  })
  test('同类按最早到期在前', () => {
    const items = [confirm('later', NOW - 1000), confirm('sooner', NOW - 60_000)]
    expect(sortCommitments(items).map((i) => i.id)).toEqual(['sooner', 'later'])
  })
  test('排序不改原数组（纯函数）', () => {
    const items = [confirm('b', NOW - 1000), confirm('a', NOW - 5000)]
    const copy = items.slice()
    sortCommitments(items)
    expect(items).toEqual(copy)
  })
})

describe('pinCommitment', () => {
  test('空 → null', () => {
    expect(pinCommitment([])).toBeNull()
  })
  test('钉住排序后的第一项，others = 其余个数', () => {
    const items: DockItem[] = [{ kind: 'queue', id: 'q', count: 1 }, confirm('c', NOW)]
    const pinned = pinCommitment(items)
    expect(pinned?.item.id).toBe('c')
    expect(pinned?.others).toBe(1)
  })
})

describe('confirmRemainingMs', () => {
  test('剩余 = expiresAt - now，下限 0', () => {
    const c = confirm('c', NOW - 10_000)
    expect(confirmRemainingMs(c, NOW)).toBe(PENDING_TTL_MS - 10_000)
    expect(confirmRemainingMs(c, NOW + PENDING_TTL_MS + 5)).toBe(0)
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/commitment.test.ts`
Expected: FAIL — `Cannot find module '@/core/presence/commitment'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/core/presence/commitment.ts
// Focus Dock 的承诺项（方案 §5.3）——「系统欠用户一个动作或一个结果」的那些东西。
// 纯类型 + 纯函数，零 RN import（jest 直接跑）。
//
// 四种 + 两种「B1 没有生产产出方」的：
//  · confirm：来自 pendingOps（危险动作二次确认）与位置授权征询（subkind='location'）
//  · slot：**协议里没有 missing_slots**（2026-08-29 grep 全空）⇒ B1 只有类型与画廊样本，
//    产出方等后端（方案 Q19）。不许在客户端猜「这句是不是在补槽」。
//  · task：长任务（process 帧持续 >8s）
//  · queue：离线队列（SessionState.queued）
// 排序稳定（评审 P1-2 采纳）：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列；
// 同类最早到期在前；Dock 只钉第一项，其余报个数、**不轮播**。

export type DockItem =
  | {
      kind: 'confirm'
      id: string
      /** 用户看得懂的动作摘要（气泡原话截断） */
      summary: string
      risk: 'low' | 'high'
      /** = 台账 ts + PENDING_TTL_MS；**只读共享 TTL**，UI 不另存时间 */
      expiresAt: number
      /** 位置授权征询：按钮文案换成「允许 / 拒绝」，不上行 operation_id */
      subkind?: 'location'
    }
  | { kind: 'slot'; id: string; missing: string }
  | { kind: 'task'; id: string; label: string; startedAt: number }
  | { kind: 'queue'; id: string; count: number }

const KIND_ORDER: Record<DockItem['kind'], number> = { confirm: 0, slot: 2, task: 3, queue: 4 }

function rank(item: DockItem): number {
  if (item.kind === 'confirm') return item.risk === 'high' ? 0 : 1
  return KIND_ORDER[item.kind]
}

function dueAt(item: DockItem): number {
  if (item.kind === 'confirm') return item.expiresAt
  if (item.kind === 'task') return item.startedAt
  return Number.MAX_SAFE_INTEGER
}

/** 稳定排序：类别 → 到期时间。不改原数组。 */
export function sortCommitments(items: readonly DockItem[]): DockItem[] {
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => rank(a.item) - rank(b.item) || dueAt(a.item) - dueAt(b.item) || a.i - b.i)
    .map((x) => x.item)
}

/** Dock 只钉一项：排序后的第一项 + 其余个数。空台账返回 null（Dock 不渲染）。 */
export function pinCommitment(items: readonly DockItem[]): { item: DockItem; others: number } | null {
  const sorted = sortCommitments(items)
  if (!sorted.length) return null
  return { item: sorted[0], others: sorted.length - 1 }
}

export function confirmRemainingMs(item: Extract<DockItem, { kind: 'confirm' }>, now: number): number {
  return Math.max(0, item.expiresAt - now)
}
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd mobile && npx jest test/commitment.test.ts && npm run typecheck`
Expected: PASS（6 tests）；`tsc` 0

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/presence/commitment.ts mobile/test/commitment.test.ts
git commit -m "feat(mobile): UX v2.1 B1-2 承诺项类型与稳定排序（Dock 钉一项不轮播）"
```

---

### Task 3: 在场派生（`core/presence/presence.ts`）

**Files:**
- Create: `mobile/src/core/presence/presence.ts`
- Test: `mobile/test/presence.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/presence.test.ts
// derivePresence（方案 §4.1）——多轴事实 + 单一视觉主态。
// 两组主张：
//  A. primary 的优先级固定：offline > attention > looking > listening/recognizing > speaking
//     > thinking/processing > followup > armed > idle（逐对交换即红）
//  B. **轴独立**：改 transport 不许动 commitment；这是多轴与单枚举的可测差别——
//     评审那个案例（待确认时断网，确认被盖掉）在这里有一条专门的断言
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { derivePresence, type PresenceInput } from '@/core/presence/presence'

const NOW = 5_000_000

function base(over: Partial<PresenceInput> = {}): PresenceInput {
  return {
    now: NOW,
    connStatus: 'open',
    connChangedAt: NOW - 60_000,
    hfEnabled: false,
    hfUsable: false,
    hfFsm: 'IDLE',
    ptt: 'idle',
    partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false,
    pendingOps: [],
    pendingLocation: false,
    voicePipeline: 'classic',
    visionCapturing: false,
    queued: 0,
    lastError: null,
    degradations: [],
    driving: false,
    identity: 'handheld',
    user: 'u1',
    ...over,
  }
}

describe('A. primary 优先级', () => {
  const cases: Array<[string, Partial<PresenceInput>, string]> = [
    ['idle', {}, 'idle'],
    ['armed', { hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' }, 'armed'],
    ['followup', { hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' }, 'listening'],
    ['thinking', { turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 } }, 'thinking'],
    ['processing', { turn: { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW - 1000 } }, 'thinking'],
    ['speaking', { speaking: true }, 'speaking'],
    ['listening(PTT)', { ptt: 'recording' }, 'listening'],
    ['listening(hf)', { hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING' }, 'listening'],
    ['looking', { visionCapturing: true }, 'looking'],
    ['attention', { pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }] }, 'attention'],
    ['offline', { connStatus: 'closed', connChangedAt: NOW - 30_000 }, 'muted'],
  ]
  test.each(cases)('%s → primary=%s', (_name, over, expected) => {
    expect(derivePresence(base(over)).primary).toBe(expected)
  })

  test('逐对交换：更高档在场时低档不得胜出', () => {
    // attention 与 listening 同时在场 → attention
    const both = base({
      pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }],
      ptt: 'recording',
    })
    expect(derivePresence(both).primary).toBe('attention')
    // looking 与 speaking 同时在场 → looking
    expect(derivePresence(base({ visionCapturing: true, speaking: true })).primary).toBe('looking')
    // offline 压过一切
    expect(
      derivePresence(base({ connStatus: 'closed', connChangedAt: NOW - 30_000, ptt: 'recording' })).primary,
    ).toBe('muted')
  })

  test('error 只在无在飞轮时短显 4s，之后回 idle', () => {
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 1000 } })).primary).toBe('idle')
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 1000 } })).capsule?.text).toBe('出错了')
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 4001 } })).capsule).toBeUndefined()
  })
})

describe('B. 轴独立', () => {
  test('评审案例：待确认时断网——Dock 仍钉着确认，胶囊说断开，光球 muted', () => {
    const snap = derivePresence(
      base({
        connStatus: 'closed',
        connChangedAt: NOW - 30_000,
        pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }],
        queued: 1,
      }),
    )
    expect(snap.primary).toBe('muted')
    expect(snap.capsule?.text).toContain('已断开')
    expect(snap.commitment.map((c) => c.kind)).toEqual(['confirm', 'queue'])
    expect(snap.transport).toBe('offline')
  })
  test('transport 变化不动 commitment（逐轴断言）', () => {
    const ops = [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }]
    const online = derivePresence(base({ pendingOps: ops }))
    const offline = derivePresence(base({ pendingOps: ops, connStatus: 'closed', connChangedAt: NOW - 30_000 }))
    expect(offline.commitment).toEqual(online.commitment)
  })
  test('reconnecting 3s 内不显示（沿用弱网横幅 3s 延迟）', () => {
    expect(derivePresence(base({ connStatus: 'connecting', connChangedAt: NOW - 1000 })).capsule).toBeUndefined()
    expect(derivePresence(base({ connStatus: 'connecting', connChangedAt: NOW - 3500 })).capsule?.text).toBe('正在重连…')
  })
  test('privacy 轴：唤醒词待机=edge；端到端录音中=cloudAudio；PTT=edge（三段式只上传文字）', () => {
    expect(derivePresence(base({ hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' })).privacy.mic).toBe('edge')
    expect(
      derivePresence(base({ hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING', voicePipeline: 's2s' })).privacy.mic,
    ).toBe('cloudAudio')
    expect(derivePresence(base({ ptt: 'recording' })).privacy.mic).toBe('edge')
    expect(derivePresence(base()).privacy.mic).toBe('off')
    expect(derivePresence(base({ visionCapturing: true })).privacy.camera).toBe('singleFrame')
  })
})

describe('commitment 构造', () => {
  test('pendingOps → confirm 项：expiresAt = ts + PENDING_TTL_MS，摘要来自输入', () => {
    const snap = derivePresence(base({ pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }] }))
    expect(snap.commitment[0]).toEqual({
      kind: 'confirm',
      id: 'op1',
      summary: '打开后备箱',
      risk: 'high',
      expiresAt: NOW - 1000 + PENDING_TTL_MS,
    })
  })
  test('位置授权征询 → confirm(subkind=location)，risk=low', () => {
    const snap = derivePresence(base({ pendingLocation: true }))
    expect(snap.commitment[0]).toMatchObject({ kind: 'confirm', subkind: 'location', risk: 'low' })
    expect(snap.primary).toBe('attention')
  })
  test('process 持续 >8s 才进 task 项', () => {
    const turn = (since: number) => ({ pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: since })
    expect(derivePresence(base({ turn: turn(NOW - 5000) })).commitment).toEqual([])
    expect(derivePresence(base({ turn: turn(NOW - 9000) })).commitment[0]).toMatchObject({ kind: 'task', label: '规划路线' })
  })
  test('queued>0 → queue 项', () => {
    expect(derivePresence(base({ queued: 3 })).commitment[0]).toMatchObject({ kind: 'queue', count: 3 })
  })
})

describe('capsule 文案', () => {
  test.each([
    [{ hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' } as Partial<PresenceInput>, '说「小舟小舟」'],
    [{ ptt: 'recording' } as Partial<PresenceInput>, '在听…'],
    [{ ptt: 'finalizing' } as Partial<PresenceInput>, '识别中…'],
    [{ speaking: true } as Partial<PresenceInput>, '播报中 · 说话可打断'],
    [{ hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' } as Partial<PresenceInput>, '可以接着说'],
    [{ visionCapturing: true } as Partial<PresenceInput>, '看一眼…'],
    [{ pendingOps: [{ id: 'op1', ts: NOW, summary: 'x' }] } as Partial<PresenceInput>, '等你确认'],
  ])('%o → %s', (over, text) => {
    expect(derivePresence(base(over)).capsule?.text).toBe(text)
  })
  test('partial 在 recognizing 时进胶囊', () => {
    expect(derivePresence(base({ ptt: 'recording', partial: '附近有什么' })).capsule?.text).toBe('附近有什么')
  })
  test('processing 显示当前步骤', () => {
    const turn = { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW }
    expect(derivePresence(base({ turn })).capsule?.text).toBe('规划路线…')
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/presence.test.ts`
Expected: FAIL — `Cannot find module '@/core/presence/presence'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/core/presence/presence.ts
// 在场模型（UX v2.1 §4）：`derivePresence()` 是一个**纯函数**——输入是既有状态机各自的事实
// （SessionCore store / 免唤醒 FSM / PTT / 设置 / 播报 / 视觉 / 时钟），输出是
// `PresenceSnapshot`：六个正交轴（transport / capture / agent / commitment / privacy / degradation）
// + **唯一的视觉主态** `primary`。
//
// 不是第四台状态机：voiceLoop / PTT / 轮态一字不改，这里只回答「此刻该让用户看到什么」。
// 输出是多轴不是单枚举（外部评审 P0-1，采纳）：待确认时断网，`offline` 不许盖掉那条确认——
// Dock 读 `commitment[]`，永远不被别的轴覆盖；光球与胶囊只读 `primary` / `capsule`。
//
// 零 RN import；jest 直接跑（test/presence.test.ts）。
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { sortCommitments, type DockItem } from './commitment'

export type OrbState =
  | 'idle'
  | 'thinking'
  | 'speaking'
  | 'armed'
  | 'listening'
  | 'attention'
  | 'looking'
  | 'muted'

export type Degradation =
  | { kind: 'recoverable_error'; text: string; at: number }
  | { kind: 'transport_unknown'; messageIds: string[] }
  | { kind: 'permission_denied'; what: 'mic' | 'camera' | 'location'; text: string }
  | { kind: 'service_degraded'; text: string }
  | { kind: 'safety_blocked'; text: string }
  | { kind: 'audio_echo_degraded'; reason: string }
  | { kind: 'fatal'; text: string }

export type Identity = 'handheld' | 'mount' | 'trusted-tablet'

export interface PresenceInput {
  now: number
  connStatus: 'connecting' | 'open' | 'closed'
  /** 上次 connStatus 变化的时刻（reconnecting 3s 延迟用） */
  connChangedAt: number
  hfEnabled: boolean
  hfUsable: boolean
  hfFsm: 'IDLE' | 'ARMED' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'FOLLOWUP' | string
  ptt: 'idle' | 'recording' | 'finalizing'
  partial: string
  turn: {
    pending: boolean
    streaming: boolean
    processActive: boolean
    processLabel: string
    /** process 首帧到达时刻；0=无 */
    processSince: number
  }
  /** 播报控制器：首片音频已起播且未结束 */
  speaking: boolean
  pendingOps: Array<{ id: string; ts: number; summary: string }>
  pendingLocation: boolean
  voicePipeline: 'classic' | 's2s'
  visionCapturing: boolean
  queued: number
  lastError: { text: string; at: number } | null
  degradations: Degradation[]
  driving: boolean
  identity: Identity
  user: string
}

export interface PresenceSnapshot {
  transport: 'online' | 'reconnecting' | 'offline'
  capture: 'off' | 'armed' | 'listening' | 'recognizing' | 'looking'
  agent: 'idle' | 'thinking' | 'processing' | 'speaking' | 'followup'
  commitment: DockItem[]
  privacy: { mic: 'off' | 'edge' | 'cloudAudio'; camera: 'off' | 'singleFrame'; user: string }
  degradation: Degradation[]
  identity: Identity
  driving: boolean
  primary: OrbState
  /** reconnecting 期间光球 ×0.6 亮度（不是新态） */
  dim: boolean
  capsule?: { text: string; tone: 'neutral' | 'accent' | 'amber' | 'red'; live?: boolean }
  input: 'voice-sheet' | 'composer' | 'none'
}

/** reconnecting 胶囊延迟（沿用 ChatScreen 弱网横幅那条 3s：重连是常态，每次都弹会让真断网没人看） */
export const RECONNECTING_GRACE_MS = 3000
/** error 胶囊短显 */
export const ERROR_SHOW_MS = 4000
/** process 持续多久才算「长任务」进 Dock */
export const LONG_TASK_MS = 8000

export function derivePresence(i: PresenceInput): PresenceSnapshot {
  // ── transport ──
  const transport: PresenceSnapshot['transport'] =
    i.connStatus === 'open' ? 'online' : i.connStatus === 'connecting' ? 'reconnecting' : 'offline'
  const reconnectingShown = transport === 'reconnecting' && i.now - i.connChangedAt >= RECONNECTING_GRACE_MS

  // ── capture ──
  const hfOn = i.hfEnabled && i.hfUsable
  const hfListening = hfOn && (i.hfFsm === 'LISTENING' || i.hfFsm === 'FOLLOWUP')
  const capture: PresenceSnapshot['capture'] = i.visionCapturing
    ? 'looking'
    : i.ptt === 'recording' || (hfOn && i.hfFsm === 'LISTENING')
      ? i.partial
        ? 'recognizing'
        : 'listening'
      : i.ptt === 'finalizing'
        ? 'recognizing'
        : hfOn && (i.hfFsm === 'ARMED' || i.hfFsm === 'FOLLOWUP')
          ? 'armed'
          : 'off'

  // ── agent ──
  const agent: PresenceSnapshot['agent'] = i.speaking
    ? 'speaking'
    : i.turn.processActive
      ? 'processing'
      : i.turn.pending || i.turn.streaming || (hfOn && i.hfFsm === 'THINKING')
        ? 'thinking'
        : hfOn && i.hfFsm === 'FOLLOWUP'
          ? 'followup'
          : 'idle'

  // ── commitment ──
  const items: DockItem[] = i.pendingOps.map((op) => ({
    kind: 'confirm',
    id: op.id,
    summary: op.summary,
    risk: 'high',
    expiresAt: op.ts + PENDING_TTL_MS,
  }))
  if (i.pendingLocation) {
    items.push({
      kind: 'confirm',
      id: '__location__',
      summary: '使用当前位置',
      risk: 'low',
      expiresAt: Number.MAX_SAFE_INTEGER,
      subkind: 'location',
    })
  }
  if (i.turn.processActive && i.turn.processSince > 0 && i.now - i.turn.processSince > LONG_TASK_MS) {
    items.push({ kind: 'task', id: '__task__', label: i.turn.processLabel || '处理中', startedAt: i.turn.processSince })
  }
  if (i.queued > 0) items.push({ kind: 'queue', id: '__queue__', count: i.queued })
  const commitment = sortCommitments(items)
  const hasAttention = commitment.some((c) => c.kind === 'confirm' || c.kind === 'slot')

  // ── privacy ──
  const micActive = capture === 'listening' || capture === 'recognizing'
  const privacy: PresenceSnapshot['privacy'] = {
    mic: micActive ? (i.voicePipeline === 's2s' && hfOn ? 'cloudAudio' : 'edge') : capture === 'armed' ? 'edge' : 'off',
    camera: i.visionCapturing ? 'singleFrame' : 'off',
    user: i.user,
  }

  // ── primary（固定顺序，写进测试）──
  const errorLive = !!i.lastError && i.now - i.lastError.at < ERROR_SHOW_MS && agent === 'idle'
  let primary: OrbState
  if (transport === 'offline') primary = 'muted'
  else if (hasAttention) primary = 'attention'
  else if (capture === 'looking') primary = 'looking'
  else if (capture === 'listening' || capture === 'recognizing') primary = 'listening'
  else if (agent === 'speaking') primary = 'speaking'
  else if (agent === 'thinking' || agent === 'processing') primary = 'thinking'
  else if (agent === 'followup') primary = 'listening'
  else if (capture === 'armed') primary = 'armed'
  else primary = 'idle'

  // ── capsule（一次一条；胶囊说「此刻」，Dock 说「欠着」）──
  let capsule: PresenceSnapshot['capsule']
  if (transport === 'offline') capsule = { text: '已断开 · 消息会排队', tone: 'red' }
  else if (reconnectingShown) capsule = { text: '正在重连…', tone: 'amber' }
  else if (hasAttention) {
    const first = commitment.find((c) => c.kind === 'confirm' || c.kind === 'slot')
    capsule = { text: first?.kind === 'slot' ? '还差一个信息' : '等你确认', tone: 'amber' }
  } else if (capture === 'looking') capsule = { text: '看一眼…', tone: 'accent' }
  else if (capture === 'recognizing') capsule = { text: i.partial || '识别中…', tone: 'accent', live: true }
  else if (capture === 'listening') capsule = { text: '在听…', tone: 'accent', live: true }
  else if (agent === 'speaking') capsule = { text: '播报中 · 说话可打断', tone: 'accent' }
  else if (agent === 'processing') capsule = { text: `${i.turn.processLabel || '处理中'}…`, tone: 'neutral' }
  else if (agent === 'thinking') capsule = { text: '正在思考…', tone: 'neutral' }
  else if (agent === 'followup') capsule = { text: '可以接着说', tone: 'accent', live: true }
  else if (capture === 'armed') capsule = { text: '说「小舟小舟」', tone: 'neutral' }
  else if (errorLive) capsule = { text: i.lastError!.text, tone: 'red' }

  const input: PresenceSnapshot['input'] =
    capture === 'listening' || capture === 'recognizing' ? 'voice-sheet' : 'composer'

  return {
    transport,
    capture,
    agent,
    commitment,
    privacy,
    degradation: i.degradations,
    identity: i.identity,
    driving: i.driving,
    primary,
    dim: transport === 'reconnecting',
    ...(capsule ? { capsule } : {}),
    input,
  }
}
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd mobile && npx jest test/presence.test.ts && npm run typecheck`
Expected: PASS（≈24 tests）；`tsc` 0

- [ ] **Step 5: 反向验证（不提交这一步的改动）**

把 `primary` 判断里的 `else if (hasAttention)` 与 `else if (capture === 'looking')` 两行**互换顺序**，跑 `npx jest test/presence.test.ts` —— 期望「逐对交换」那条红（attention 与 listening 同时在场应为 attention）。还原后再跑一次绿。这一步是方案 §4.1「反向验证」的落实，做完写进提交信息。

- [ ] **Step 6: 提交**

```bash
git add -- mobile/src/core/presence/presence.ts mobile/test/presence.test.ts
git commit -m "feat(mobile): UX v2.1 B1-3 derivePresence——六轴事实 + 唯一视觉主态（反向验证：交换 attention/looking 顺序即红）"
```

---

### Task 4: SessionCore——精确剪枝、到期留痕、离线队列计数、发送状态未知

**Files:**
- Modify: `mobile/src/core/session/store.ts`（`SessionState`、`dispatch`、`setStatus`、`syncPruneTimer`、`handleFrame` final 分支）
- Test: `mobile/test/sessionStore.test.ts`（追加一个 describe）

- [ ] **Step 1: 写失败测试（追加到 `mobile/test/sessionStore.test.ts` 末尾）**

```ts
describe('UX v2.1 B1-4：承诺面的账本侧', () => {
  test('剪枝按项到期精确调度：到期那一秒出账，并在记录里留「确认已过期」', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op1' })
    expect(core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op1'])
    // 共享 TTL 300s：到 299s 还在
    jest.advanceTimersByTime(299_000)
    expect(core.store.getState().pendingOps).toHaveLength(1)
    // 300s 整出账（v1 是固定 30s 轮询，最坏晚 30s；现在按项到期调度）
    jest.advanceTimersByTime(1_100)
    expect(core.store.getState().pendingOps).toHaveLength(0)
    const last = msgs(core)[msgs(core).length - 1]
    expect(last.role).toBe('assistant')
    expect(last.text).toContain('确认已过期')
    expect(last.text).toContain('打开后备箱') // 摘要来自原气泡
    core.dispose()
  })

  test('服务端 closed 出账不留「过期」痕（那是被处理了，不是过期）', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op1' })
    core.send('算了')
    const rid2 = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid2, speech: '好的', closed_operation_ids: ['op1'] })
    expect(msgs(core).some((m) => m.text.includes('确认已过期'))).toBe(false)
    core.dispose()
  })

  test('离线入队计数：transport.send 返回 false 累加，连上归零', () => {
    const transport = new FakeTransport()
    transport.send = (frame: object) => {
      transport.sent.push(frame)
      return false // 断线：入队
    }
    const core = new SessionCore({
      transport,
      sessionId: 'app-test01',
      getMeta: () => ({}),
      location: fakeLocation(false),
    })
    core.setStatus('closed')
    core.send('现在几点')
    core.send('讲个笑话')
    expect(core.store.getState().queued).toBe(2)
    core.setStatus('open') // ws.mjs onopen 时 flush 队列 → 计数归零
    expect(core.store.getState().queued).toBe(0)
    core.dispose()
  })

  test('探活判死时在飞轮标「发送状态未知」，收到终态帧即清', () => {
    const { transport, core } = newCore()
    core.setStatus('open')
    core.send('现在几点')
    const rid = transport.lastUserFrame().request_id
    const pendingId = assistants(core)[0].id
    core.setStatus('closed') // liveness.onDead → reconnectNow → onStatus('closed')
    expect(core.store.getState().uncertainIds).toEqual([pendingId])
    core.setStatus('open')
    core.handleFrame({ type: 'final', request_id: rid, speech: '八点' })
    expect(core.store.getState().uncertainIds).toEqual([])
    core.dispose()
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/sessionStore.test.ts -t "B1-4"`
Expected: FAIL — 第 1 条在 300s 后仍有 1 条（旧 30s 轮询恰好在 300s 时也会剪……**注意**：旧实现 `setInterval(30s)` 在 300s 整那一跳也会剪掉，所以第 1 条的「到期」断言可能碰巧绿——红的是「确认已过期」文案断言）；第 3/4 条 `queued` / `uncertainIds` 为 `undefined`。

- [ ] **Step 3: 改实现**

`mobile/src/core/session/store.ts`：

(a) `SessionState` 加两个字段（在 `lastEmotion` 之后）：

```ts
  /** 断线期间入队（transport.send 返回 false）的上行帧数；连上即归零（ws.mjs onopen 会 flush） */
  queued: number
  /** 探活判死那一刻仍在飞的助手气泡 id——它们的请求可能写进了死 socket（M3-W 残留窗），
   *  UI 标「发送状态未知」；终态帧到达或超时即清。**不自动重发**（重发同一 request_id
   *  意味着车控可能执行两次，M3-W 定案） */
  uncertainIds: string[]
```

初始值：`queued: 0, uncertainIds: []`。

(b) `PRUNE_INTERVAL_MS` 注释改为「上界」并加导入：

```ts
import { PENDING_TTL_MS, closePendings, openPending, prunePendings } from '@shared/pendingOps.mjs'
// 剪枝调度上界：按项到期精确调度，min(下一条到期, 30s)。30s 是没有任何挂起时的兜底轮询上界，
// 不再是「最坏晚 30s 才出账」——那正是 P5「按钮无解释消失」的一半成因
const PRUNE_INTERVAL_MS = 30_000
```

(c) `setStatus` 改为：

```ts
  setStatus(status: GatewayStatus): void {
    const prev = this.store.getState().connStatus
    if (status === prev) return
    if (status === 'closed' && prev === 'open') {
      // 探活判死 / onclose：此刻在飞的轮可能写进了死 socket（M3-W 残留窗）——标未知，不重发
      const inFlight = this.registry.bubbleIds()
      this.store.setState({ connStatus: status, uncertainIds: inFlight })
      return
    }
    if (status === 'open') {
      // onopen 时 ws.mjs 已 flush 队列
      this.store.setState({ connStatus: status, queued: 0 })
      return
    }
    this.store.setState({ connStatus: status })
  }
```

⚠ `RequestRegistry` 是共享模块（`hmi/src/requestRouting.mjs`），**不能加方法**。在 SessionCore 里自己维护在飞 id 集合：`private readonly inFlight = new Set<string>()`，`dispatch` 里 `open` 后 `add(pendingId)`，`clearWatchdog`（所有终态路径都经它）里 `delete`。于是上面的 `this.registry.bubbleIds()` 改成 `[...this.inFlight]`。

(d) `dispatch` 里 `this.deps.transport.send(frame)` 改为：

```ts
    const sentNow = this.deps.transport.send(frame)
    if (!sentNow) this.store.setState((s) => ({ queued: s.queued + 1 }))
```

`cancelCurrentTurn` / `proactive_ack` 的 `send` 不计数（它们不是用户消息）。

(e) `clearWatchdog` 末尾追加：

```ts
    this.inFlight.delete(bubbleId)
    const s = this.store.getState()
    if (s.uncertainIds.includes(bubbleId)) {
      this.store.setState({ uncertainIds: s.uncertainIds.filter((x) => x !== bubbleId) })
    }
```

`armWatchdog` 的超时回调里在 `this.registry.dropBubble(id)` 之后同样执行 `this.inFlight.delete(id)` 与 `uncertainIds` 过滤（超时也是终态）。

(f) `syncPruneTimer` 整段替换：

```ts
  /** 本地限龄（App.tsx:630-640 的语义 + B1 的精确调度）：后端挂起 TTL 到点就没了，前端跟着老化。
   *  v1 固定 30s 轮询，最坏晚 30s 出账、且**静默消失**；现在按「下一条到期时刻」调度，
   *  到期出账时在记录里留一行「确认已过期」（P5：承诺消失必有理由）。 */
  private syncPruneTimer(): void {
    if (this.pruneTimer) {
      clearTimeout(this.pruneTimer)
      this.pruneTimer = null
    }
    const ops = this.store.getState().pendingOps
    if (!ops.length) return
    const now = Date.now()
    const nextExpiry = Math.min(...ops.map((o) => o.ts + PENDING_TTL_MS))
    const delay = Math.max(0, Math.min(nextExpiry - now, PRUNE_INTERVAL_MS))
    this.pruneTimer = setTimeout(() => {
      this.pruneTimer = null
      const before = this.store.getState().pendingOps
      const after = prunePendings(before)
      if (after.length !== before.length) {
        const expired = before.filter((o) => !after.some((a) => a.id === o.id))
        this.store.setState({ pendingOps: after })
        for (const op of expired) this.noteExpired(op.id)
      }
      this.syncPruneTimer()
    }, delay)
  }

  /** 到期留痕：找到带这条 operationId 的助手气泡，取其原话做摘要，追加一条说明 */
  private noteExpired(operationId: string): void {
    const src = this.store.getState().messages.find((m) => m.operationId === operationId)
    const summary = (src?.text || '').replace(/\s+/g, ' ').slice(0, 24)
    this.appendMessage({
      id: uid(),
      role: 'assistant',
      text: summary ? `⏱ 「${summary}」的确认已过期，需要的话再说一次` : '⏱ 刚才那条确认已过期，需要的话再说一次',
    })
  }
```

`pruneTimer` 的类型从 `ReturnType<typeof setInterval>` 改为 `ReturnType<typeof setTimeout>`；`dispose()` 里 `clearInterval` 改 `clearTimeout`。`handleFrame` 的 final 分支里已有 `closePendings(prunePendings(...))` 后调 `syncPruneTimer()`，不用改；`confirmReply` 同。

(g) 摘要来源：`noteExpired` 用 `msg.operationId` 找气泡——`final` 分支已把 `operationId` 写进气泡（`store.ts:366`），无需改。

- [ ] **Step 4: 跑测试确认绿 + 全量**

Run: `cd mobile && npx jest test/sessionStore.test.ts && npm test && npm run typecheck`
Expected: 新增 4 条 PASS；全量 234 + 4 + 前面任务新增（tokens 7 / commitment 6 / presence ≈24）全绿；`tsc` 0

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts
git commit -m "feat(mobile): UX v2.1 B1-4 账本侧——剪枝按项到期精确调度 + 到期留痕 + 离线入队计数 + 发送状态未知"
```

---

### Task 5: 四路结构化信号（播报 / 视觉 / 回声 / 权限）

**Files:**
- Modify: `mobile/src/core/voice/speech.ts`（`subscribeSpeaking`）
- Modify: `mobile/src/core/vision/frame.ts`（`subscribeVisionCapturing`）
- Modify: `mobile/src/core/voice/handsFree.ts`（deps `onBargeInDisabled` / `onPipelineDegraded`）
- Modify: `mobile/src/features/chat/useHandsFree.ts`（透出 `bargeInDisabled` / `pipelineDegraded`）
- Modify: `mobile/src/features/chat/usePtt.ts`（`errorKind`）
- Test: `mobile/test/presenceSignals.test.ts`（新建）

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/presenceSignals.test.ts
// B1-5：Presence 的四路输入信号必须是**可订阅的结构化事实**，不是回调链上的字符串。
// 此前 `speechController` 的 onSpeechBegan/Ended 只能被一个消费方链式覆盖（useHandsFree 就是
// 这么接的），第二个消费方要么覆盖掉第一个、要么复制那套「存 prev 再链回去」的脆弱写法。
import { registerVisionCapturer, subscribeVisionCapturing, captureVisionFrame } from '@/core/vision/frame'
import { SpeechController } from '@/core/voice/speech'

// 播放器与 TTS 会话在 jest 里不存在：只订阅状态，不真播
jest.mock('@/core/voice/audioCtx', () => ({ newPcmPlayer: jest.fn(() => ({ push() {}, remainingSec: () => 0, stop() {} })) }))

describe('speech.subscribeSpeaking', () => {
  test('首片音频 → true；stop → false；退订后不再收', () => {
    const sc = new SpeechController('https://x')
    const seen: boolean[] = []
    const off = sc.subscribeSpeaking((v) => seen.push(v))
    ;(sc as unknown as { setSpeaking(v: boolean): void }).setSpeaking(true)
    sc.stop()
    off()
    ;(sc as unknown as { setSpeaking(v: boolean): void }).setSpeaking(true)
    expect(seen).toEqual([true, false])
    expect(sc.speaking).toBe(true)
  })
})

describe('vision.subscribeVisionCapturing', () => {
  test('抓帧期间 true，结束（含失败）后 false', async () => {
    const seen: boolean[] = []
    const off = subscribeVisionCapturing((v) => seen.push(v))
    let release: () => void = () => {}
    registerVisionCapturer(() => new Promise<string>((r) => (release = () => r(''))))
    const p = captureVisionFrame('https://audio')
    await Promise.resolve()
    expect(seen).toEqual([true])
    release()
    await p
    expect(seen).toEqual([true, false])
    off()
    registerVisionCapturer(null)
  })
})

```

回声回调那一条**不放在这个文件**——`HandsFreeController` 构造时会 `new VadEngine/KwsEngine/micLease`，只有 `test/handsFree.test.ts` 那套 `jest.doMock` + `jest.resetModules` + `require` 的接线替身能构造它。追加到 `test/handsFree.test.ts` 末尾（复用其 `makeCtl`）：

```ts
test('⑤ FSM 关闭 barge-in → onBargeInDisabled(reason) 与 onNotice 同时收到（UX v2.1 B1-5）', () => {
  const bargeIn: string[] = []
  const notices: string[] = []
  const { ctl } = makeCtl({
    onNotice: (m: string) => notices.push(m),
    onBargeInDisabled: (r: string) => bargeIn.push(r),
  })
  // 直接触发 FSM 的回调（VoiceLoop 构造时由控制器注入 onDisableBargeIn），不跑引擎
  ctl.vl.onDisableBargeIn('repeated-self-trigger')
  expect(bargeIn).toEqual(['repeated-self-trigger'])
  expect(notices[0]).toContain('语音打断')
})
```

（`makeCtl` 返回的 `ctl` 在那个文件里是 `any`，可直接取 `vl`。）

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/presenceSignals.test.ts`
Expected: FAIL — `subscribeSpeaking is not a function` / `subscribeVisionCapturing` 未导出 / `onBargeInDisabled` 未调用

- [ ] **Step 3: 改实现**

(a) `mobile/src/core/voice/speech.ts`——在 `SpeechController` 类里加：

```ts
  /** 播报中（首片音频起播 → 播完/停）。Presence 的 agent 轴读它；**可多订阅**，
   *  不再要求消费方链式覆盖 onSpeechBegan/Ended（那套写法第二个消费方就会把第一个顶掉）。 */
  speaking = false
  private readonly speakingSubs = new Set<(v: boolean) => void>()

  subscribeSpeaking(fn: (v: boolean) => void): () => void {
    this.speakingSubs.add(fn)
    return () => {
      this.speakingSubs.delete(fn)
    }
  }

  private setSpeaking(v: boolean): void {
    if (this.speaking === v) return
    this.speaking = v
    for (const fn of this.speakingSubs) fn(v)
  }
```

挂点：`begin()` 里 `onFirstAudio` 回调内 `this.setSpeaking(true)`（放在 `this.onSpeechBegan?.(...)` 之前）；`onEnd` 回调内 `this.setSpeaking(false)`；`stop()` 开头 `this.setSpeaking(false)`；`speakBatch()` 在 `this.extra = { player }` 之后 `this.setSpeaking(true)`、`await done` 之后 `this.setSpeaking(false)`；`preview()` 的 `onFirstAudio` 里 `this.setSpeaking(true)`、`await session.completion` 后 `this.setSpeaking(false)`。

(b) `mobile/src/core/vision/frame.ts`——加：

```ts
let capturing = false
const capturingSubs = new Set<(v: boolean) => void>()

/** 抓帧进行中（挂载相机 → 拍完卸载）。Presence 的 `looking` 态与隐私栏读它。 */
export function subscribeVisionCapturing(fn: (v: boolean) => void): () => void {
  capturingSubs.add(fn)
  return () => {
    capturingSubs.delete(fn)
  }
}

function setCapturing(v: boolean): void {
  if (capturing === v) return
  capturing = v
  for (const fn of capturingSubs) fn(v)
}

export function isVisionCapturing(): boolean {
  return capturing
}
```

`captureVisionFrame` 改为：

```ts
export async function captureVisionFrame(audioUrl: string): Promise<string> {
  const fn = capturer
  if (!fn || !audioUrl) return ''
  setCapturing(true)
  try {
    const uri = await fn()
    if (!uri) return ''
    const blob = await (await fetch(uri)).blob()
    const r = await fetch(`${audioUrl}/api/vision/frame?mime=image/jpeg`, {
      method: 'POST',
      body: blob,
      headers: { 'Content-Type': 'image/jpeg' },
    })
    if (!r.ok) return ''
    const j = (await r.json()) as { frame_id?: string }
    return j.frame_id || ''
  } catch {
    return ''
  } finally {
    setCapturing(false)
  }
}
```

(c) `mobile/src/core/voice/handsFree.ts`——`HandsFreeDeps` 加两条（在 `onNotice` 之后）：

```ts
  /** 结构化降级信号（UX v2.1 §12.1）。与 onNotice 并行：notice 是人话，这两条是给 Presence 的事实 */
  onBargeInDisabled?(reason: string): void
  onPipelineDegraded?(kind: 'degraded' | 'unsupported', message: string): void
```

`onDisableBargeIn` 改为：

```ts
      onDisableBargeIn: (reason: string) => {
        this.deps.onNotice?.('已关闭本次会话的语音打断（' + reason + '）')
        this.deps.onBargeInDisabled?.(reason)
      },
```

`startS2s` 里 `onSessionState` 与 `onUnsupported` 各加一行：`this.deps.onPipelineDegraded?.('degraded', '语音链路降级，本轮回落三段式')` / `this.deps.onPipelineDegraded?.('unsupported', msg)`。

(d) `mobile/src/features/chat/useHandsFree.ts`——`HandsFreeUi` 加：

```ts
  /** 会话级关闭了语音打断（回声连续自触发）；空串=未关。会话级不可恢复（voiceLoop 语义） */
  bargeInDisabled: string
  /** 本轮语音链路降级说明；下一轮 FSM 进 LISTENING 时清 */
  pipelineDegraded: string
```

hook 内加 `const [bargeInDisabled, setBargeInDisabled] = useState('')`、`const [pipelineDegraded, setPipelineDegraded] = useState('')`；deps 里加 `onBargeInDisabled: (r) => setBargeInDisabled(r)`、`onPipelineDegraded: (_k, m) => setPipelineDegraded(m)`；`onOrbState` 里 `if (f === 'LISTENING') setPipelineDegraded('')`；effect 清理里两者置空；返回对象加两字段。

(e) `mobile/src/features/chat/usePtt.ts`——`PttHandle` 加 `errorKind: 'permission' | 'start' | 'asr' | ''`；`setError` 的三处分别配 `setErrorKind('asr')`（`onError` / 没听清）、`'permission'`（`PermissionDeniedError`）、`'start'`（其它启动失败）；`pressDown` 开头 `setErrorKind('')`；返回对象加 `errorKind`。

- [ ] **Step 4: 跑测试确认绿 + 全量**

Run: `cd mobile && npx jest test/presenceSignals.test.ts test/handsFree.test.ts && npm test && npm run typecheck`
Expected: `presenceSignals` 2 PASS + `handsFree` 新增第 ⑤ 条 PASS；全量绿（既有用例不受影响）；`tsc` 0

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/voice/speech.ts mobile/src/core/vision/frame.ts mobile/src/core/voice/handsFree.ts mobile/src/features/chat/useHandsFree.ts mobile/src/features/chat/usePtt.ts mobile/test/presenceSignals.test.ts mobile/test/handsFree.test.ts
git commit -m "feat(mobile): UX v2.1 B1-5 四路结构化信号——播报/抓帧可订阅、回声与链路降级回调、PTT 错误分类"
```

---

### Task 6: 光球三新态（`AuroraOrb.tsx`）

**Files:**
- Modify: `mobile/src/ui/aurora/AuroraOrb.tsx`
- Test: 类型层由 `tsc` 守；视觉由 Task 13 的状态画廊真机截图守（本任务无 jest——光球是 Reanimated 组件，仓库里没有组件渲染测试，不为这一个开先例）

- [ ] **Step 1: 改类型与状态判定**

**`OrbState` 只许有一份定义**：Task 3 已在 `core/presence/presence.ts` 导出它（含三个新态）。把 `AuroraOrb.tsx:23` 的本地 `export type OrbState = …` **删掉**，改为：

```ts
import type { OrbState } from '../../core/presence/presence'
export type { OrbState }
```

（`ui/aurora/index.ts` 的 `export { AuroraOrb, type OrbState } from './AuroraOrb'` 不用改。依赖方向 UI → core，presence.ts 零 RN import，不会把 reanimated 拖进 jest。）⇒ **Task 6 依赖 Task 3**（§3 依赖图已改）。

`AuroraOrb` 的 props 加 `dim?: boolean`（reconnecting 期 ×0.6 亮度）。函数体开头在 `const armed = ...` 之后加：

```ts
  const attention = state === 'attention'
  const looking = state === 'looking'
  const muted = state === 'muted'
  // 三个新态只加环与节律，不碰七层、四色、波纹色（方案 §10.1 十条不变量）
  const spins = animated && !muted
```

`glow` 行改为 `const glow = speaking ? 1.35 : listening ? 1.15 : armed ? 0.8 : muted ? 0.6 : 1`。
`useEffect` 里 `if (!animated) return` 改为 `if (!spins) return`（muted 停旋转但呼吸也停：muted 时整段 return 即可）；依赖数组把 `animated` 换成 `spins`。

- [ ] **Step 2: 外层 View 加去饱和与调暗**

最外层 `<View style={{ width: size, height: size }} accessibilityLabel="小舟">` 改为：

```tsx
    <View
      style={{
        width: size,
        height: size,
        opacity: dim ? 0.6 : 1,
        // RN 0.86 Android 支持 filter.saturate；iOS 不支持时静默忽略（本 App 只交付 Android）
        ...(muted ? { filter: [{ saturate: 0.4 }] } : {}),
      }}
      accessibilityLabel={ORB_A11Y[state]}
      accessibilityRole="image"
    >
```

文件顶部加：

```ts
/** TalkBack 读法随状态变（§8.1）；Composer 上的可点光球再包一层 button role */
export const ORB_A11Y: Record<OrbState, string> = {
  idle: '小舟',
  thinking: '小舟，正在思考',
  speaking: '小舟，播报中',
  armed: '小舟，等待唤醒',
  listening: '小舟，在听',
  attention: '小舟，等你确认',
  looking: '小舟，正在看一眼',
  muted: '小舟，已断开',
}
```

- [ ] **Step 3: 加三种环**

把「聆听/待机态：单圈交互蓝聆听环」那段 `{(listening || armed) && (...)}` 之后追加：

```tsx
      {/* 等你确认：琥珀环（琥珀本就是确认态语义色，A-6.4），呼吸 3s；不改球体 */}
      {attention && (
        <Animated.View
          pointerEvents="none"
          style={[
            layer,
            {
              top: -size * 0.14, left: -size * 0.14, right: -size * 0.14, bottom: -size * 0.14,
              borderWidth: 2,
              borderColor: 'rgba(245,158,11,0.35)',
            },
            animated ? bodyStyle : null,
          ]}
        />
      )}
      {/* 看一眼：一次性白环扩散（与 speaking 的三青环区分：一次 vs 连续） */}
      {animated && looking && <Shutter size={size} />}
      {/* 离线：灰环，静止 */}
      {muted && (
        <View
          pointerEvents="none"
          style={[
            layer,
            {
              top: -size * 0.1, left: -size * 0.1, right: -size * 0.1, bottom: -size * 0.1,
              borderWidth: 1,
              borderColor: 'rgba(255,255,255,0.22)',
            },
          ]}
        />
      )}
```

文件末尾加 `Shutter`：

```tsx
/** looking 快门：白环 0.9→1.35 扩散并淡出，**只放一次**（300ms），之后停在不可见 */
function Shutter({ size }: { size: number }) {
  const t = useSharedValue(0)
  useEffect(() => {
    t.value = 0
    t.value = withTiming(1, { duration: 300, easing: Easing.out(Easing.ease) })
    return () => cancelAnimation(t)
  }, [t])
  const style = useAnimatedStyle(() => ({
    transform: [{ scale: 0.9 + 0.45 * t.value }],
    opacity: 0.9 * (1 - t.value),
  }))
  return (
    <Animated.View
      pointerEvents="none"
      style={[
        {
          position: 'absolute',
          top: -size * 0.1, left: -size * 0.1, right: -size * 0.1, bottom: -size * 0.1,
          borderRadius: 9999,
          borderWidth: 1.5,
          borderColor: 'rgba(255,255,255,0.8)',
        },
        style,
      ]}
    />
  )
}
```

`attention` 态下 `bodyDur` 取 idle 的 4s（`bodyDur` 表达式不用改：attention 不命中 thinking/speaking/listening/armed，落到 4）。

- [ ] **Step 4: 类型检查 + 既有测试**

Run: `cd mobile && npm run typecheck && npm test`
Expected: `tsc` 0（`OrbState` 扩展是纯增量，`Composer.tsx:39` 等既有用法不受影响）；全量绿

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/ui/aurora/AuroraOrb.tsx
git commit -m "feat(mobile): UX v2.1 B1-6 光球三新态 attention/looking/muted——只加环与节律，七层四色不动"
```

---

### Task 7: 设置开关（回滚路径）与身份占位

**Files:**
- Modify: `mobile/src/core/settings/store.ts`
- Modify: `mobile/src/features/settings/SettingsScreen.tsx`（新「实验室」分区，放在「调试」之前）
- Test: `mobile/test/settingsMeta.test.ts`（追加）

- [ ] **Step 1: 写失败测试（追加到 `settingsMeta.test.ts` 末尾）**

```ts
describe('UX v2.1 开关与身份（B1-7）', () => {
  test('缺省：两个 v2 开关开、身份=手持', () => {
    expect(DEFAULT_APP_SETTINGS.uxV2Presence).toBe(true)
    expect(DEFAULT_APP_SETTINGS.uxV2Dock).toBe(true)
    expect(DEFAULT_APP_SETTINGS.deviceRole).toBe('handheld')
  })
  test('存量设置没有这三个键 → 合并后取缺省（向前兼容）', () => {
    const merged = mergeStoredSettings(JSON.stringify({ theme: 'dark' }))
    expect(merged.uxV2Presence).toBe(true)
    expect(merged.deviceRole).toBe('handheld')
  })
  test('三个键**不上行**（buildMeta 键集不变）', () => {
    expect(Object.keys(buildMeta(DEFAULT_APP_SETTINGS)).sort()).toEqual([...HMI_META_KEYS].sort())
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/settingsMeta.test.ts -t "B1-7"`
Expected: FAIL — `uxV2Presence` 为 `undefined`

- [ ] **Step 3: 改实现**

`mobile/src/core/settings/store.ts`：`AppSettings` 末尾加

```ts
  // ── UX v2.1（B1）──
  /** 在场模型 + 光球主态 + 状态胶囊。关掉即回 v1 的四条状态条（回滚路径，§11.5） */
  uxV2Presence: boolean
  /** Focus Dock 承诺面。关掉即回气泡内确认按钮 */
  uxV2Dock: boolean
  /** 产品身份（方案 §6.0）。B1 只占位，B4 才有 UI 与行为差异；**窗口尺寸不决定它** */
  deviceRole: 'handheld' | 'mount' | 'trusted-tablet'
```

`DEFAULT_APP_SETTINGS` 末尾加 `uxV2Presence: true, uxV2Dock: true, deviceRole: 'handheld',`。

`mobile/src/features/settings/SettingsScreen.tsx`：在 `<Section p={p} title="调试">` 之前插入：

```tsx
      {/* UX v2.1 实验室：两个开关是**回滚路径**（§11.5）——纯 JS 批次出问题关开关，不重装。
          v1 的状态条与气泡内确认在 B1/B2 期间不删。 */}
      <Section p={p} title="实验室（UX v2.1）">
        <SwitchRow
          p={p}
          label="光球状态锚 + 状态胶囊"
          desc="关闭后回到 v1：免唤醒状态条、弱网横幅、通知条分开显示"
          value={settings.uxV2Presence}
          onChange={(uxV2Presence) => set({ uxV2Presence })}
        />
        <SwitchRow
          p={p}
          label="承诺面 Focus Dock"
          desc="确认 / 长任务 / 离线队列钉在输入区上方；关闭后回到气泡内确认按钮"
          value={settings.uxV2Dock}
          onChange={(uxV2Dock) => set({ uxV2Dock })}
        />
        <Link href="/state-gallery" style={{ color: p.accent, fontSize: p.font(14) }}>
          状态画廊（在场 13 态 + 7 种降级 / 主题过检）
        </Link>
      </Section>
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd mobile && npx jest test/settingsMeta.test.ts && npm run typecheck`
Expected: PASS；`tsc` 0（`/state-gallery` 路由在 Task 14 注册前 `Link` 的 href 类型由 expo-router typed routes 生成——若 `tsc` 报 href 不合法，先在 `app/_layout.tsx` 注册 `<Stack.Screen name="state-gallery" options={{ title: '调试 · 状态画廊' }} />` 并建一个空的 `app/state-gallery.tsx` 占位：`export default function StateGallery() { return null }`，Task 14 再填。）

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/settings/store.ts mobile/src/features/settings/SettingsScreen.tsx mobile/test/settingsMeta.test.ts mobile/src/app/_layout.tsx mobile/src/app/state-gallery.tsx
git commit -m "feat(mobile): UX v2.1 B1-7 实验室开关 uxV2Presence/uxV2Dock + deviceRole 占位"
```

---

### Task 8: 采集激活日志 + `usePresence` 收集器

**Files:**
- Create: `mobile/src/core/presence/activityLog.ts`
- Create: `mobile/src/features/chat/usePresence.ts`
- Test: `mobile/test/activityLog.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/activityLog.test.ts
// 隐私栏「最近一次激活」的数据源（方案 §5.10）：20 条内存环形日志，**不上传、不持久化**。
import { ActivityLog } from '@/core/presence/activityLog'

test('环形 20 条，最新在前', () => {
  const log = new ActivityLog(20, () => 1000)
  for (let i = 0; i < 25; i += 1) log.push('mic', `唤醒词命中 ${i}`)
  const items = log.list()
  expect(items).toHaveLength(20)
  expect(items[0].note).toBe('唤醒词命中 24')
  expect(items[19].note).toBe('唤醒词命中 5')
})

test('lastOf 取该来源最近一条；没有返回 null', () => {
  let t = 0
  const log = new ActivityLog(20, () => (t += 1000))
  log.push('mic', '按住说话')
  log.push('camera', '触发词「这是什么」')
  log.push('mic', '唤醒词命中')
  expect(log.lastOf('mic')).toMatchObject({ note: '唤醒词命中', at: 3000 })
  expect(log.lastOf('camera')).toMatchObject({ note: '触发词「这是什么」' })
  expect(log.lastOf('location')).toBeNull()
})

test('订阅：每次 push 通知一次；退订后不再收', () => {
  const log = new ActivityLog(5, () => 0)
  let n = 0
  const off = log.subscribe(() => (n += 1))
  log.push('mic', 'a')
  log.push('mic', 'b')
  off()
  log.push('mic', 'c')
  expect(n).toBe(2)
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/activityLog.test.ts`
Expected: FAIL — `Cannot find module '@/core/presence/activityLog'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/core/presence/activityLog.ts
// 采集激活环形日志（方案 §5.10 隐私栏「最近一次激活」）。**内存、20 条、不上传、不持久化**：
// 它回答的是「刚才麦克风/摄像头为什么开了」，不是审计——审计在服务端账本。
export type ActivitySource = 'mic' | 'camera' | 'location'

export interface ActivityEntry {
  source: ActivitySource
  note: string
  at: number
}

export class ActivityLog {
  private items: ActivityEntry[] = []
  private readonly subs = new Set<() => void>()

  constructor(
    private readonly capacity = 20,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  push(source: ActivitySource, note: string): void {
    this.items = [{ source, note, at: this.clock() }, ...this.items].slice(0, this.capacity)
    for (const fn of this.subs) fn()
  }

  /** 最新在前 */
  list(): ActivityEntry[] {
    return this.items.slice()
  }

  lastOf(source: ActivitySource): ActivityEntry | null {
    return this.items.find((e) => e.source === source) ?? null
  }

  subscribe(fn: () => void): () => void {
    this.subs.add(fn)
    return () => {
      this.subs.delete(fn)
    }
  }
}

/** App 级单例（隐私栏读；PTT / 免唤醒 / 视觉在激活处写） */
export const activityLog = new ActivityLog()
```

- [ ] **Step 4: 写 `usePresence`（无单测：它只是把既有 hook 的输出喂给纯函数；纯函数在 Task 3 已守）**

```ts
// mobile/src/features/chat/usePresence.ts
// 在场收集器：把既有状态机的事实收齐 → derivePresence（纯函数，Task 3）。
// 这里**不做任何判断**——所有「谁压过谁」都在 presence.ts 里且有测试；这里只负责订阅与计时。
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from 'zustand'

import type { SessionCore } from '@/core/session/store'
import { settingsStore } from '@/core/settings/store'
import { speechController } from '@/core/voice/speech'
import { isVisionCapturing, subscribeVisionCapturing } from '@/core/vision/frame'
import { derivePresence, type Degradation, type PresenceSnapshot } from '@/core/presence/presence'

import type { HandsFreeUi } from './useHandsFree'
import type { PttHandle } from './usePtt'

export interface UsePresenceOpts {
  core: SessionCore
  hf: HandsFreeUi
  ptt: PttHandle | null
  /** token 对应的 user_id（隐私栏「当前：xx」）；ServerConfig 里没有就显示 token 尾 4 位 */
  user: string
}

/** 只在这些秒级量变化时才需要重算：倒计时 / 3s 延迟 / 4s error / 8s 长任务 */
const TICK_MS = 1000

export function usePresence({ core, hf, ptt, user }: UsePresenceOpts): PresenceSnapshot {
  const { messages, pendingOps, connStatus, pendingLocationText, queued, uncertainIds } = useStore(core.store)
  const { settings } = useStore(settingsStore)

  // 播报中 / 抓帧中：订阅式信号（Task 5）
  const [speaking, setSpeaking] = useState(() => speechController().speaking)
  const [visionCapturing, setVisionCapturing] = useState(isVisionCapturing)
  useEffect(() => speechController().subscribeSpeaking(setSpeaking), [])
  useEffect(() => subscribeVisionCapturing(setVisionCapturing), [])

  // connStatus 变化时刻（reconnecting 3s 延迟的基准）
  const connChangedAt = useRef(Date.now())
  const prevConn = useRef(connStatus)
  if (prevConn.current !== connStatus) {
    prevConn.current = connStatus
    connChangedAt.current = Date.now()
  }

  // 最近一条错误（4s 短显）：取最后一条 error 气泡的出现时刻
  const lastError = useMemo(() => {
    const m = [...messages].reverse().find((x) => x.role === 'assistant' && x.error)
    return m ? { text: m.text.startsWith('出错了') ? '出错了' : m.text.slice(0, 20), at: errorSeenAt(m.id) } : null
  }, [messages])

  // 秒级时钟：只驱动依赖 now 的分支
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(t)
  }, [])

  // 在飞轮 + 过程区
  const active = [...messages].reverse().find((m) => m.role === 'assistant' && (m.pending || m.streaming || m.processActive))
  const processLabel = active?.process?.length ? active.process[active.process.length - 1].label : ''
  const processSince = active?.processActive ? processSeenAt(active.id) : 0

  // 降级轴（§12.1）：B1 只接今天就有信号的四种
  const degradations: Degradation[] = []
  if (ptt?.errorKind === 'permission') degradations.push({ kind: 'permission_denied', what: 'mic', text: ptt.error })
  if (hf.bargeInDisabled) degradations.push({ kind: 'audio_echo_degraded', reason: hf.bargeInDisabled })
  if (hf.pipelineDegraded) degradations.push({ kind: 'service_degraded', text: hf.pipelineDegraded })
  if (uncertainIds.length) degradations.push({ kind: 'transport_unknown', messageIds: uncertainIds })

  // pendingOps 的摘要：带该 operationId 的助手气泡原话
  const ops = pendingOps.map((op) => ({
    id: op.id,
    ts: op.ts,
    summary: (messages.find((m) => m.operationId === op.id)?.text || '待确认的操作').replace(/\s+/g, ' ').slice(0, 24),
  }))

  return derivePresence({
    now,
    connStatus,
    connChangedAt: connChangedAt.current,
    hfEnabled: settings.handsFree,
    hfUsable: hf.availability.usable,
    hfFsm: hf.fsm,
    ptt: ptt?.state ?? 'idle',
    partial: ptt?.partial || hf.partial || '',
    turn: {
      pending: !!active?.pending,
      streaming: !!active?.streaming,
      processActive: !!active?.processActive,
      processLabel,
      processSince,
    },
    speaking,
    pendingOps: ops,
    pendingLocation: pendingLocationText !== null,
    voicePipeline: settings.voicePipeline,
    visionCapturing,
    queued,
    lastError,
    degradations,
    driving: !!active?.driving,
    identity: settings.deviceRole,
    user,
  })
}

// ── 时刻登记：Msg 类型是共享的、不能加字段，所以「这条错误/过程是什么时候出现的」在这里记 ──
const seenAt = new Map<string, number>()
function firstSeen(key: string): number {
  const t = seenAt.get(key)
  if (t) return t
  const n = Date.now()
  seenAt.set(key, n)
  if (seenAt.size > 200) seenAt.delete(seenAt.keys().next().value as string)
  return n
}
function errorSeenAt(id: string): number {
  return firstSeen('err:' + id)
}
function processSeenAt(id: string): number {
  return firstSeen('proc:' + id)
}
```

- [ ] **Step 5: 跑测试 + 类型检查**

Run: `cd mobile && npx jest test/activityLog.test.ts && npm run typecheck`
Expected: PASS（3）；`tsc` 0（`HandsFreeUi.bargeInDisabled/pipelineDegraded`、`PttHandle.errorKind`、`SessionState.queued/uncertainIds` 都在 Task 4/5 已加）

- [ ] **Step 6: 提交**

```bash
git add -- mobile/src/core/presence/activityLog.ts mobile/src/features/chat/usePresence.ts mobile/test/activityLog.test.ts
git commit -m "feat(mobile): UX v2.1 B1-8 采集激活日志 + usePresence 收集器（只订阅与计时，不判断）"
```

---

### Task 9: 状态胶囊与 Focus Dock 组件

**Files:**
- Create: `mobile/src/features/chat/PresenceCapsule.tsx`
- Create: `mobile/src/features/chat/FocusDock.tsx`

- [ ] **Step 1: 写 `PresenceCapsule`**

```tsx
// mobile/src/features/chat/PresenceCapsule.tsx
// 状态胶囊（方案 §4.3）：替代 v1 的四条窄条——一次只说一件「此刻」的事；「欠着」的归 Dock。
// 材质 G1-tint（叠在深空底上）；文字不用极光（虹彩纪律）。
import { Pressable, Text, View } from 'react-native'

import type { PresenceSnapshot } from '@/core/presence/presence'
import { RADIUS, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'
import type { FontScalePref } from '@/core/settings/store'

export function PresenceCapsule({
  p,
  fontScale,
  snapshot,
  onPress,
}: {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onPress?(): void
}) {
  const c = snapshot.capsule
  if (!c) return null
  const fg = c.tone === 'amber' ? p.amber : c.tone === 'red' ? p.red : c.tone === 'accent' ? p.accent : p.fg2
  return (
    <View style={{ alignItems: 'center', paddingVertical: 4 }}>
      <Pressable
        testID="presence-capsule"
        onPress={onPress}
        accessibilityRole="text"
        accessibilityLiveRegion="polite"
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: 6,
          minHeight: scale(26, 'target', fontScale),
          paddingHorizontal: 12,
          borderRadius: RADIUS.full,
          backgroundColor: p.glassBg,
          borderWidth: 1,
          borderColor: p.glassBdRight,
          borderTopColor: p.glassBdTop,
          boxShadow: p.glassShadow,
        }}
      >
        {c.live ? (
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: p.accent, boxShadow: `0 0 8px ${p.accent}` }} />
        ) : null}
        <Text numberOfLines={1} style={{ color: fg, fontSize: scale(TYPE.micro + 1, 'text', fontScale), maxWidth: 260 }}>
          {c.text}
        </Text>
      </Pressable>
    </View>
  )
}
```

- [ ] **Step 2: 写 `FocusDock`**

```tsx
// mobile/src/features/chat/FocusDock.tsx
// 承诺面（方案 §5.3）：读 `commitment[]`（钉一项 + 其余个数）与 `degradation[]`（有出口的降级）。
// 材质 **G0 实色**（§5.11：确认/错误/隐私说明不许半透明；坑账 §9.36 同判据）。
// 确认按钮比例照 A-6.4：取消 flex1 / 确认 flex2；剩余时间**只读共享 TTL**（commitment.ts）。
import { useEffect, useState } from 'react'
import { Linking, Pressable, Text, View } from 'react-native'

import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { confirmRemainingMs, pinCommitment, type DockItem } from '@/core/presence/commitment'
import type { Degradation, PresenceSnapshot } from '@/core/presence/presence'
import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export interface FocusDockProps {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onConfirm(reply: '确认' | '取消', operationId?: string): void
  onCancelTurn(): void
  /** 「另有 N 个待处理 ›」点开 → 调用方决定怎么展示（B1：滚到列表底部即可） */
  onOthers?(): void
  onReenableBargeIn?(): void
}

function fmt(ms: number): string {
  const s = Math.ceil(ms / 1000)
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s}s`
}

export function FocusDock(props: FocusDockProps) {
  const { p, fontScale, snapshot } = props
  const pinned = pinCommitment(snapshot.commitment)
  const degradations = snapshot.degradation.filter((d) => d.kind !== 'transport_unknown' && d.kind !== 'recoverable_error')
  if (!pinned && !degradations.length) return null
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  return (
    <View testID="focus-dock" style={{ paddingHorizontal: 12, paddingBottom: 6, gap: 6 }}>
      {pinned ? <CommitmentCard {...props} item={pinned.item} others={pinned.others} solid={solid} /> : null}
      {degradations.map((d) => (
        <DegradationRow key={d.kind} p={p} fontScale={fontScale} d={d} solid={solid} onReenableBargeIn={props.onReenableBargeIn} />
      ))}
    </View>
  )
}

function CommitmentCard({
  p,
  fontScale,
  item,
  others,
  solid,
  onConfirm,
  onCancelTurn,
  onOthers,
}: FocusDockProps & { item: DockItem; others: number; solid: string }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (item.kind !== 'confirm' || item.subkind === 'location') return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [item])
  const h = scale(TARGET.parked, 'target', fontScale)
  const border = item.kind === 'confirm' ? 'rgba(245,158,11,0.38)' : p.line
  return (
    <View
      testID={item.kind === 'confirm' ? 'dock-confirm' : `dock-${item.kind}`}
      accessibilityLiveRegion="assertive"
      style={{
        backgroundColor: solid,
        borderRadius: RADIUS.lg,
        borderWidth: 1,
        borderColor: border,
        padding: 10,
        gap: 8,
        boxShadow: item.kind === 'confirm' ? '0 0 16px rgba(245,158,11,0.12), 0 8px 24px rgba(0,0,0,0.3)' : '0 8px 24px rgba(0,0,0,0.25)',
      }}
    >
      {item.kind === 'confirm' ? (
        <>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body, 'text', fontScale) }}>⚠</Text>
            <Text numberOfLines={1} style={{ color: p.fg1, fontSize: scale(TYPE.body, 'text', fontScale), fontWeight: '600', flex: 1 }}>
              {item.summary}
            </Text>
            <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>
              {item.subkind === 'location' ? '位置授权' : '危险动作 · 需二次确认'}
            </Text>
          </View>
          {item.subkind !== 'location' ? (
            <View style={{ gap: 4 }}>
              <View style={{ height: 2, borderRadius: 1, backgroundColor: p.fill2, overflow: 'hidden' }}>
                <View
                  style={{
                    height: 2,
                    width: `${Math.round((confirmRemainingMs(item, now) / PENDING_TTL_MS) * 100)}%`,
                    backgroundColor: p.amber,
                  }}
                />
              </View>
              <Text testID="dock-countdown" style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>
                {fmt(confirmRemainingMs(item, now))} 后过期
              </Text>
            </View>
          ) : null}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              testID="dock-cancel"
              accessibilityRole="button"
              onPress={() => onConfirm('取消', item.subkind === 'location' ? undefined : item.id)}
              style={{ flex: 1, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: p.fill2, backgroundColor: p.fill, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>{item.subkind === 'location' ? '拒绝' : '取消'}</Text>
            </Pressable>
            <Pressable
              testID="dock-accept"
              accessibilityRole="button"
              onPress={() => onConfirm('确认', item.subkind === 'location' ? undefined : item.id)}
              style={{ flex: 2, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: 'rgba(245,158,11,0.38)', backgroundColor: p.amberSoft, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={{ color: p.amber, fontSize: scale(TYPE.body - 1, 'text', fontScale), fontWeight: '600' }}>{item.subkind === 'location' ? '允许' : '确认'}</Text>
            </Pressable>
          </View>
        </>
      ) : item.kind === 'task' ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: p.teal, fontSize: scale(TYPE.body, 'text', fontScale) }}>⟳</Text>
          <Text numberOfLines={1} style={{ color: p.fg1, fontSize: scale(TYPE.body - 1, 'text', fontScale), flex: 1 }}>{item.label}…</Text>
          <Pressable accessibilityRole="button" onPress={onCancelTurn} style={{ minHeight: h, paddingHorizontal: 12, justifyContent: 'center' }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>取消</Text>
          </Pressable>
        </View>
      ) : item.kind === 'queue' ? (
        <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>
          {item.count} 条消息排队中，连上后自动补发
        </Text>
      ) : (
        <Text style={{ color: p.fg1, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>还差一个信息：{item.missing}</Text>
      )}
      {others > 0 ? (
        <Pressable onPress={onOthers} accessibilityRole="button">
          <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>另有 {others} 个待处理 ›</Text>
        </Pressable>
      ) : null}
    </View>
  )
}

const DEGRADATION_TEXT: Record<Exclude<Degradation['kind'], 'transport_unknown' | 'recoverable_error'>, (d: Degradation) => string> = {
  permission_denied: (d) => (d.kind === 'permission_denied' ? d.text : ''),
  service_degraded: (d) => (d.kind === 'service_degraded' ? d.text : ''),
  safety_blocked: (d) => (d.kind === 'safety_blocked' ? d.text : ''),
  audio_echo_degraded: () => '环境回声较强，本轮已关闭插话；点「停止播报」后再说',
  fatal: (d) => (d.kind === 'fatal' ? d.text : ''),
}

function DegradationRow({
  p,
  fontScale,
  d,
  solid,
  onReenableBargeIn,
}: {
  p: Palette
  fontScale: FontScalePref
  d: Degradation
  solid: string
  onReenableBargeIn?(): void
}) {
  if (d.kind === 'transport_unknown' || d.kind === 'recoverable_error') return null
  const text = DEGRADATION_TEXT[d.kind](d)
  const action =
    d.kind === 'permission_denied'
      ? { label: '去系统设置', run: () => void Linking.openSettings() }
      : d.kind === 'audio_echo_degraded' && onReenableBargeIn
        ? { label: '重新开启插话', run: onReenableBargeIn }
        : null
  return (
    <View
      testID={`dock-${d.kind}`}
      style={{ backgroundColor: solid, borderRadius: RADIUS.md, borderWidth: 1, borderColor: d.kind === 'safety_blocked' || d.kind === 'fatal' ? 'rgba(239,68,68,0.35)' : p.line, padding: 10, flexDirection: 'row', alignItems: 'center', gap: 8 }}
    >
      <Text style={{ color: p.fg2, fontSize: scale(TYPE.caption, 'text', fontScale), flex: 1 }}>{text}</Text>
      {action ? (
        <Pressable accessibilityRole="button" onPress={action.run} style={{ minHeight: scale(TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 8 }}>
          <Text style={{ color: p.accent, fontSize: scale(TYPE.caption, 'text', fontScale) }}>{action.label}</Text>
        </Pressable>
      ) : null}
    </View>
  )
}
```

⚠ `audio_echo_degraded` 的「重新开启插话」：voiceLoop 的 `_bargeInDisabled` 是**会话级不可恢复**（`voiceLoop.mjs:129` 注释）——B1 的做法是关掉再打开免唤醒开关（等于重建控制器）。`onReenableBargeIn` 在 ChatScreen 里实现为 `update({ handsFree: false })` 后下一帧 `update({ handsFree: true })`。不改共享 FSM。

- [ ] **Step 3: 类型检查**

Run: `cd mobile && npm run typecheck`
Expected: 0 error

- [ ] **Step 4: 提交**

```bash
git add -- mobile/src/features/chat/PresenceCapsule.tsx mobile/src/features/chat/FocusDock.tsx
git commit -m "feat(mobile): UX v2.1 B1-9 状态胶囊 + Focus Dock（G0 实色、钉一项、倒计时读共享 TTL、降级出口）"
```

---

### Task 10: 对话屏接线——四条窄条 → 胶囊 + Dock；光球读 primary

**Files:**
- Modify: `mobile/src/features/chat/ChatScreen.tsx`
- Modify: `mobile/src/features/chat/Composer.tsx`
- Modify: `mobile/src/features/chat/MessageBubble.tsx`

- [ ] **Step 1: `Composer` 改为读 `primary`、热区 56、删提示行**

`Composer` props 加 `orbState: OrbState`、`orbDim?: boolean`、`fontScale: FontScalePref`；**删除** `orbState` 的本地推导（`Composer.tsx:39`）与提示行（`:40-43`、`:75-88`）；PTT 按钮改：

```tsx
        {ptt ? (
          <Pressable
            testID="composer-orb"
            onPressIn={ptt.pressDown}
            onPressOut={ptt.pressUp}
            disabled={finalizing}
            accessibilityRole="button"
            accessibilityLabel={recording ? '松开发送' : '按住说话'}
            style={{
              width: scale(TARGET.driving, 'target', fontScale),
              height: scale(TARGET.driving, 'target', fontScale),
              borderRadius: RADIUS.full,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: recording ? p.accentSoft : 'transparent',
            }}
          >
            <AuroraOrb size={44} state={orbState} dim={orbDim} animated />
          </Pressable>
        ) : null}
```

（导入 `RADIUS, TARGET, scale` from `'../../ui/tokens'`，`FontScalePref` from settings store。）`placeholder` 保留 `recording ? '正在听…' : '和小舟说点什么…'`。

⚠ 回滚路径：`uxV2Presence=false` 时 ChatScreen 传入的 `orbState` 仍是旧推导值（见 Step 3），Composer 自己不再推导。

- [ ] **Step 2: `MessageBubble` 气泡内确认按钮加开关；「发送状态未知」灰字**

props 加 `inlineConfirm: boolean`（Dock 开时为 false）、`uncertain?: boolean`。`{msg.needConfirm && confirmActive ? (...)}` 改为 `{msg.needConfirm && confirmActive && inlineConfirm ? (...)}`；在 `{msg.pending ? ...}` 之后加：

```tsx
        {uncertain ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>发送状态未知（网络刚断过；连上后若无回音请再说一次）</Text>
        ) : null}
```

- [ ] **Step 3: `ChatScreen` 接线**

(a) 导入：`usePresence`、`PresenceCapsule`、`FocusDock`、`activityLog`、`type OrbState`。删除 `HF_LABEL` / `HF_DOT` 常量**不要删**——`uxV2Presence=false` 的回滚分支仍用它们（B4 稳定后再删，§11.5）。

(b) `ChatBody` 里在 `hf` 之后加：

```tsx
  const snapshot = usePresence({ core, hf, ptt: cfg.audioUrl ? ptt : null, user: cfg.token.slice(-4) })
  const v2 = settings.uxV2Presence
  const dock = settings.uxV2Dock
  // 回滚分支的光球态（v1 推导，逐字照 Composer 旧代码）
  const legacyOrb: OrbState = ptt.state === 'recording' ? 'speaking' : ptt.state === 'finalizing' ? 'thinking' : 'idle'
  const { uncertainIds } = useStore(core.store)
  const reenableBargeIn = useCallback(() => {
    settingsStore.getState().update({ handsFree: false })
    setTimeout(() => settingsStore.getState().update({ handsFree: true }), 50)
  }, [])
```

(c) `chatColumn` 里，把「免唤醒状态条」与「hf.error || notice」两段包进 `{!v2 ? (...) : null}`；在 `<Composer>` 之前插入：

```tsx
      {v2 && dock ? (
        <FocusDock
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          onConfirm={onConfirm}
          onCancelTurn={onInterrupt}
          onReenableBargeIn={reenableBargeIn}
        />
      ) : null}
      {v2 ? <PresenceCapsule p={p} fontScale={settings.fontScale} snapshot={snapshot} /> : null}
```

`<Composer>` 传 `orbState={v2 ? snapshot.primary : legacyOrb}`、`orbDim={v2 && snapshot.dim}`、`fontScale={settings.fontScale}`。`MessageBubble` 传 `inlineConfirm={!(v2 && dock)}`、`uncertain={uncertainIds.includes(item.id)}`；FlashList 的 `extraData` 加 `uncertainIds, v2, dock`。

(d) 弱网横幅 `{linkWarn ? (...)}` 包进 `{!v2 && linkWarn ? ... : null}`（v2 下胶囊已表达）。顶栏连接 pill 改为**健康点**：v2 时只渲染 7dp 点（`transport==='online'` 灰 `p.fg3`、`reconnecting` 琥珀、`offline` 红），不带文字；非 v2 保持旧 pill。顶栏光球 `state={busy ? 'thinking' : 'idle'}` 保持（品牌球只 idle/thinking，方案 §5.1）。

(e) PTT 与免唤醒的激活写日志：`usePtt` 的 `onFinal` 不变；在 `ptt` 创建后加

```tsx
  useEffect(() => {
    if (ptt.state === 'recording') activityLog.push('mic', '按住说话')
  }, [ptt.state])
  useEffect(() => {
    if (hf.fsm === 'LISTENING') activityLog.push('mic', settings.voicePipeline === 's2s' ? '唤醒词命中 · 端到端（原始音频上传）' : '唤醒词命中')
  }, [hf.fsm, settings.voicePipeline])
```

视觉：`onSend` 里 `captureVisionFrame` 之前 `activityLog.push('camera', `触发词「${text.slice(0, 12)}」`)`。

- [ ] **Step 4: 类型检查 + 全量测试 + 真机冒烟**

Run: `cd mobile && npm run typecheck && npm test`
Expected: `tsc` 0；全量绿。
真机（Metro 热载）：① 发「打开后备箱」→ Dock 出现（`dock-confirm`）、倒计时递减、点「取消」→ Dock 消失；② 关闭「承诺面」开关 → 确认回到气泡内；③ 关闭「光球状态锚」开关 → 旧四条状态条回来。截图三张存 `mobile/e2e/artifacts/b1-10-*.png`（目录 gitignore，见 Task 17）。

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/Composer.tsx mobile/src/features/chat/MessageBubble.tsx
git commit -m "feat(mobile): UX v2.1 B1-10 对话屏接线——四条窄条收成胶囊 + Dock，光球读 primary，两开关保留 v1 回滚路径"
```

---

### Task 11: 隐私栏（Privacy Rail）

**Files:**
- Create: `mobile/src/features/chat/PrivacyRail.tsx`
- Modify: `mobile/src/features/chat/ChatScreen.tsx`（顶栏采集点 + 打开隐私栏）

- [ ] **Step 1: 写 `PrivacyRail`**

```tsx
// mobile/src/features/chat/PrivacyRail.tsx
// 隐私栏（方案 §5.10）：红线三条件的**实时**表达。G0 实色；读 snapshot.privacy + 激活日志。
// 「当前用户」= token 身份（App 端没有声纹，§2.3 信道约束），不做「未确认说话人 / 访客模式」。
import { useEffect, useState } from 'react'
import { Modal, Pressable, ScrollView, Text, View } from 'react-native'

import { activityLog, type ActivityEntry } from '@/core/presence/activityLog'
import type { PresenceSnapshot } from '@/core/presence/presence'
import { settingsStore, type FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

const MIC_TEXT = {
  off: '关',
  edge: '本机处理（唤醒词待机 / 转文字后只上传文字）',
  cloudAudio: '原始音频上传中（端到端）',
} as const

function when(e: ActivityEntry | null): string {
  if (!e) return '—'
  const d = new Date(e.at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')} ${e.note}`
}

export function PrivacyRail({
  p,
  fontScale,
  snapshot,
  visible,
  onClose,
  onStopMic,
}: {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  visible: boolean
  onClose(): void
  /** 一键关闭本轮麦克风（停 PTT / 免唤醒 / ASR 流） */
  onStopMic(): void
}) {
  const [, force] = useState(0)
  useEffect(() => activityLog.subscribe(() => force((n) => n + 1)), [])
  const { settings, update } = settingsStore.getState()
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  const row = (k: string, v: string, tone: string = p.fg1) => (
    <View style={{ flexDirection: 'row', gap: 12, paddingVertical: 6, borderBottomWidth: 1, borderColor: p.line }}>
      <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fontScale), width: 88 }}>{k}</Text>
      <Text style={{ color: tone, fontSize: scale(TYPE.caption, 'text', fontScale), flex: 1 }}>{v}</Text>
    </View>
  )
  const btn = (label: string, run: () => void) => (
    <Pressable accessibilityRole="button" onPress={run} style={{ minHeight: scale(TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 12, borderRadius: RADIUS.md, borderWidth: 1, borderColor: p.fill2, backgroundColor: p.fill }}>
      <Text style={{ color: p.fg1, fontSize: scale(TYPE.caption, 'text', fontScale) }}>{label}</Text>
    </Pressable>
  )
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={onClose} accessibilityLabel="关闭隐私栏" />
      <View testID="privacy-rail" style={{ backgroundColor: solid, borderTopLeftRadius: RADIUS['2xl'], borderTopRightRadius: RADIUS['2xl'], padding: 16, gap: 10, maxHeight: '70%' }}>
        <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fontScale), fontWeight: '600' }}>隐私 · 现在在采集什么</Text>
        <ScrollView>
          {row('麦克风', MIC_TEXT[snapshot.privacy.mic], snapshot.privacy.mic === 'cloudAudio' ? p.amber : p.fg1)}
          {row('摄像头', snapshot.privacy.camera === 'singleFrame' ? '正在抓一帧（触发词命中）' : '关')}
          {row('最近一次', `麦 ${when(activityLog.lastOf('mic'))}`)}
          {row('', `摄像头 ${when(activityLog.lastOf('camera'))}`)}
          {row('当前用户', `token ····${snapshot.privacy.user}（App 端身份 = token，不做声纹）`)}
          <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale), lineHeight: scale(17, 'line', fontScale), paddingTop: 8 }}>
            三段式：语音在本机转成文字后只上传文字。端到端：上传原始音频，仅在唤醒后的对话窗内采集。
            拍到的画面只用于回答当前这一句，服务器最多保留两分钟，不落盘、不进记忆。
          </Text>
        </ScrollView>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          {btn('关闭本轮麦克风', () => { onStopMic(); onClose() })}
          {settings.handsFree ? btn('关闭免唤醒', () => { update({ handsFree: false }); onClose() }) : null}
          {settings.visionEnabled ? btn('关闭看图问答', () => { update({ visionEnabled: false }); onClose() }) : null}
        </View>
      </View>
    </Modal>
  )
}
```

- [ ] **Step 2: 顶栏采集点 + 入口**

`ChatScreen` 顶栏健康点旁加 8dp 采集点：`snapshot.privacy.mic === 'cloudAudio'` 琥珀 / `mic !== 'off'` 青 / `camera === 'singleFrame'` 白 / 其余不渲染；点按（整个健康点 + 采集点区域是一个 `Pressable`，热区 40dp）→ `setPrivacyOpen(true)`。挂 `<PrivacyRail visible={privacyOpen} onClose={() => setPrivacyOpen(false)} onStopMic={() => { ptt.pressUp(); if (settings.handsFree) reenableBargeIn() }} … />`（`onStopMic` 的免唤醒半边 = 关再开，等于打断当前收音窗；B2 的语音层会给它更准确的实现）。

- [ ] **Step 3: 类型检查 + 真机冒烟**

Run: `cd mobile && npm run typecheck`；真机：免唤醒开 → 顶栏青点 → 点开 → 「本机处理（唤醒词待机…）」；切端到端并唤醒 → 琥珀点 + 「原始音频上传中」；截图存档。

- [ ] **Step 4: 提交**

```bash
git add -- mobile/src/features/chat/PrivacyRail.tsx mobile/src/features/chat/ChatScreen.tsx
git commit -m "feat(mobile): UX v2.1 B1-11 隐私栏——麦/摄像头/最近激活/当前用户实时指示 + 一键关闭"
```

---

### Task 12: 键盘避让——先取读数，再修

**Files:**
- Modify: `mobile/src/features/chat/ChatScreen.tsx:358-360`、`mobile/src/app/onboarding.tsx:97-100`、`mobile/src/app/debug.tsx:96-99`
- Create: `mobile/e2e/08-keyboard-no-hide.yaml`

- [ ] **Step 1: 取读数（真机，HyperOS 输入法）**

对话页点输入框弹键盘 → `adb exec-out screencap -p -d <displayId> > mobile/e2e/artifacts/b1-12-chat-kbd.png`；Onboarding 页同样一张。判据：**发送键与输入框是否被键盘盖住**。把两张图的结论写进本任务提交信息（「遮 / 不遮」各一）。

- [ ] **Step 2: 写 Maestro 流（无论读数如何都要有——它是以后这条不回退的探针）**

```yaml
# mobile/e2e/08-keyboard-no-hide.yaml
# 流 ⑧：键盘弹出时发送键仍可点（UX v2.1 P6）。**刻意不 hideKeyboard**——
# 01/02/03 里的 hideKeyboard 是 Maestro 树里只剩输入法的问题（坑账），不是遮挡证据；
# 这条才是遮挡的直接判据：树里拿不到发送键就红。
appId: com.xiaozhou.companion
name: 08 键盘弹出不遮发送键
tags:
  - online
---
- runFlow: subflows/open-app.yaml
- tapOn:
    id: "composer-input"
- inputText: "现在几点"
- assertVisible:
    id: "composer-send"
- tapOn:
    id: "composer-send"
- assertVisible: "现在几点"
```

- [ ] **Step 3: 按读数二选一改代码**

**A. 读数=遮挡**（三处 `KeyboardAvoidingView`）：

```tsx
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior="padding"
          keyboardVerticalOffset={Platform.OS === 'android' ? 0 : 0}
        >
```

并在 `mobile/app.config.ts` 的 `android` 段确认 `softwareKeyboardLayoutMode: 'resize'`（缺省即是；若 edge-to-edge 下 `resize` 失效，`behavior="padding"` 由 RN 用键盘高度补底）。

**B. 读数=不遮**：三处改成不再传 `behavior`（删掉那行死代码），注释写明「Android `adjustResize` 已由系统重排，KeyboardAvoidingView 只为 iOS 后路保留」。

- [ ] **Step 4: 跑流**

Run: `maestro test --no-reinstall-driver mobile/e2e/08-keyboard-no-hide.yaml`
Expected: `1/1 Flows Passed`（A 与 B 两种读数下都必须通过——这条流就是「修没修对」的判据）

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/features/chat/ChatScreen.tsx mobile/src/app/onboarding.tsx mobile/src/app/debug.tsx mobile/e2e/08-keyboard-no-hide.yaml
git commit -m "fix(mobile): UX v2.1 B1-12 键盘避让——真机读数=<遮/不遮>，<A/B> 修法 + Maestro 08 流钉住"
```

---

### Task 13: Onboarding 上品牌

**Files:**
- Modify: `mobile/src/app/onboarding.tsx`（整文件重写视觉；**逻辑函数 `onTest/onSave/derived/presets` 一行不改**）

- [ ] **Step 1: 重写渲染部分**

保留文件顶部的 import 与 `Onboarding()` 内所有 state/逻辑；把 `return (...)` 与 `styles` 替换为：

```tsx
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const fs = settings.fontScale
  const label = (t: string) => (
    <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fs), marginTop: 6 }}>{t}</Text>
  )
  const input = (props: React.ComponentProps<typeof TextInput>) => (
    <TextInput
      placeholderTextColor={p.fg3}
      {...props}
      style={{
        backgroundColor: p.fill,
        borderWidth: 1,
        borderColor: p.fill2,
        borderRadius: RADIUS.md,
        paddingHorizontal: 12,
        minHeight: scale(TARGET.parked, 'target', fs),
        color: p.fg1,
        fontSize: scale(TYPE.body, 'text', fs),
      }}
    />
  )
  const button = (text: string, run: () => void, primary: boolean, disabled: boolean) => (
    <Pressable
      onPress={run}
      disabled={disabled}
      accessibilityRole="button"
      style={{
        minHeight: scale(TARGET.parked, 'target', fs),
        borderRadius: RADIUS.lg,
        alignItems: 'center',
        justifyContent: 'center',
        opacity: disabled ? 0.4 : 1,
        ...(primary
          ? { experimental_backgroundImage: AURORA.gradient, boxShadow: '0 4px 22px rgba(91,140,255,0.45)' }
          : { backgroundColor: p.fill, borderWidth: 1, borderColor: p.fill2 }),
      }}
    >
      <Text style={{ color: primary ? '#fff' : p.fg1, fontSize: scale(TYPE.body, 'text', fs), fontWeight: '600' }}>{text}</Text>
    </Pressable>
  )

  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <SafeAreaView style={{ flex: 1 }} edges={['top', 'bottom']}>
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView contentContainerStyle={{ padding: 20, gap: 14 }} keyboardShouldPersistTaps="handled">
            <View style={{ alignItems: 'center', gap: 8, paddingVertical: 12 }}>
              <AuroraOrb size={88} state="idle" animated />
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.display - 6, 'text', fs), fontWeight: '600', marginTop: 8 }}>我是小舟</Text>
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fs) }}>先连上你的座舱服务器</Text>
            </View>

            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>服务器</Text>
              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
                {presets.map((it) => (
                  <Pressable key={it.key} onPress={() => { setPreset(it.key); setTest({ kind: 'idle' }) }} style={{ paddingVertical: 8, paddingHorizontal: 14, borderRadius: RADIUS.full, backgroundColor: preset === it.key ? p.accentSoft : p.fill, borderWidth: 1, borderColor: preset === it.key ? p.accent : p.fill2 }}>
                    <Text style={{ color: preset === it.key ? p.accent : p.fg2, fontSize: scale(TYPE.caption, 'text', fs) }}>{it.label}</Text>
                  </Pressable>
                ))}
              </View>
              {preset === 'cloud' ? (
                <View>
                  {label('Tailnet FQDN')}
                  {input({ value: fqdn, onChangeText: (v) => { setFqdn(v); setTest({ kind: 'idle' }) }, placeholder: 'your-machine.tailxxxx.ts.net', autoCapitalize: 'none', autoCorrect: false })}
                  {fqdn.trim().length > 0 && !derived && <Text style={{ color: p.red, fontSize: scale(TYPE.caption, 'text', fs), marginTop: 6 }}>FQDN 不合法（须形如 xxx.ts.net，全小写）</Text>}
                  {derived && <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fs), marginTop: 6 }}>主链 {derived.edgeUrl} ｜ 音频 {derived.audioUrl}</Text>}
                </View>
              ) : (
                <View>
                  {label('主链入口（edge）')}
                  {input({ value: edgeUrl, onChangeText: (v) => { setEdgeUrl(v); setTest({ kind: 'idle' }) }, placeholder: 'http://192.168.1.10:18000', autoCapitalize: 'none', autoCorrect: false })}
                  {label('音频入口（audio）')}
                  {input({ value: audioUrl, onChangeText: setAudioUrl, placeholder: 'http://192.168.1.10:50059', autoCapitalize: 'none', autoCorrect: false })}
                </View>
              )}
            </Glass>

            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>访问 token</Text>
              {label(savedTokenTail ? `当前 ····${savedTokenTail}；只存本机安全存储，不进日志` : '只存本机安全存储，不进日志')}
              {input({ value: token, onChangeText: (v) => { setToken(v); setTest({ kind: 'idle' }) }, placeholder: 'AUTH_TOKENS 条目的 token 段', autoCapitalize: 'none', autoCorrect: false, secureTextEntry: true })}
            </Glass>

            {/* 权限用途文案的合规落点（app.config.ts:99-101 注释点名：manifest 里写了字不算合规） */}
            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>之后会用到的权限</Text>
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.caption, 'text', fs), lineHeight: scale(18, 'line', fs) }}>
                麦克风：按住光球说话时才开；免唤醒与端到端默认关，要你在设置里显式打开。{'\n'}
                定位：只在位置相关请求时取一次坐标，坐标不持久化。{'\n'}
                摄像头：只有开了「看图问答」且说出看图的话时才拍一张，不落盘不进记忆。
              </Text>
            </Glass>

            {button(test.kind === 'testing' ? '连接中…' : '连接测试', onTest, false, !canTest || test.kind === 'testing')}
            {test.kind === 'ok' && <Text style={{ color: p.green, fontSize: scale(TYPE.caption, 'text', fs) }}>✓ 连接成功（握手通过）</Text>}
            {test.kind === 'fail' && <Text style={{ color: p.red, fontSize: scale(TYPE.caption, 'text', fs) }}>{test.message}</Text>}
            {button('保存并进入', onSave, true, !canSave)}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  )
```

新增 import：`SafeAreaView` from `react-native-safe-area-context`；`useStore` from `zustand`；`settingsStore` from `@/core/settings/store`；`AuroraBackground, AuroraOrb, Glass` from `@/ui/aurora`；`AURORA, usePalette` from `@/ui/theme`；`RADIUS, TARGET, TYPE, scale` from `@/ui/tokens`。删除 `StyleSheet` import 与整个 `styles`。Task 12 若选 B 路径，这里的 `KeyboardAvoidingView` 也同样处理。

- [ ] **Step 2: 类型检查 + 真机截图（深浅各一）**

Run: `cd mobile && npm run typecheck`；设置页「重新配置」进入 → 截图；`cmd uimode night no` 切浅色再截一张。

- [ ] **Step 3: 提交**

```bash
git add -- mobile/src/app/onboarding.tsx
git commit -m "feat(mobile): UX v2.1 B1-13 Onboarding 上品牌——Aurora 底 + 玻璃 + 光球 + 色板 + 安全区 + 权限用途文案"
```

---

### Task 14: 状态画廊（`/state-gallery`）+ 样本覆盖守卫

**Files:**
- Create: `mobile/src/core/presence/fixtures.ts`
- Create/Replace: `mobile/src/app/state-gallery.tsx`
- Modify: `mobile/src/app/_layout.tsx`（若 Task 7 未注册）
- Test: `mobile/test/presenceFixtures.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/presenceFixtures.test.ts
// 状态画廊的覆盖度守卫（同 card-gallery 的「注册表卡型必须都有样本」）：
// 每个 primary（8 个光球态）与每种 degradation（7 种）都要有样本，缺一即红——
// 「少了谁」得当场看得见，不能等到事后数截图。
import { presenceFixtures } from '@/core/presence/fixtures'
import type { Degradation, OrbState } from '@/core/presence/presence'

const PRIMARIES: OrbState[] = ['idle', 'armed', 'listening', 'thinking', 'speaking', 'attention', 'looking', 'muted']
const DEGRADATIONS: Degradation['kind'][] = [
  'recoverable_error', 'transport_unknown', 'permission_denied', 'service_degraded', 'safety_blocked', 'audio_echo_degraded', 'fatal',
]

test('每个 primary 至少一条样本', () => {
  const covered = new Set(presenceFixtures().map((f) => f.snapshot.primary))
  expect(PRIMARIES.filter((s) => !covered.has(s))).toEqual([])
})

test('每种 degradation 至少一条样本', () => {
  const covered = new Set(presenceFixtures().flatMap((f) => f.snapshot.degradation.map((d) => d.kind)))
  expect(DEGRADATIONS.filter((k) => !covered.has(k))).toEqual([])
})

test('样本标签唯一（?only= 直达靠它）', () => {
  const labels = presenceFixtures().map((f) => f.label)
  expect(new Set(labels).size).toBe(labels.length)
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/presenceFixtures.test.ts`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 写样本**

```ts
// mobile/src/core/presence/fixtures.ts
// 状态画廊语料：**每次调用重新取时间基准**（倒计时是相对量，同 cards/fixtures.ts 的教训）。
// 样本走的是 derivePresence 本尊——画廊里看到的就是生产代码算出来的，不是手摆的快照；
// `slot` 与 `safety_blocked` 两种 B1 没有生产产出方，这里只证明「渲得出来」，不证明「会出现」。
import { derivePresence, type PresenceInput, type PresenceSnapshot } from './presence'

export interface PresenceFixture {
  label: string
  /** 真栈能否产出（false = 只有画廊样本，方案 §0 接手须知 5） */
  producible: boolean
  snapshot: PresenceSnapshot
}

export function presenceFixtures(): PresenceFixture[] {
  const NOW = Date.now()
  const base: PresenceInput = {
    now: NOW, connStatus: 'open', connChangedAt: NOW - 60_000,
    hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', ptt: 'idle', partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false, pendingOps: [], pendingLocation: false, voicePipeline: 'classic',
    visionCapturing: false, queued: 0, lastError: null, degradations: [], driving: false,
    identity: 'handheld', user: 'ab12',
  }
  const mk = (label: string, over: Partial<PresenceInput>, producible = true): PresenceFixture => ({
    label, producible, snapshot: derivePresence({ ...base, ...over }),
  })
  const hf = (fsm: PresenceInput['hfFsm'], extra: Partial<PresenceInput> = {}) => ({ hfEnabled: true, hfUsable: true, hfFsm: fsm, ...extra })
  return [
    mk('idle', {}),
    mk('armed', hf('ARMED')),
    mk('listening-ptt', { ptt: 'recording' }),
    mk('recognizing-partial', { ptt: 'recording', partial: '附近有什么好吃的' }),
    mk('listening-s2s', hf('LISTENING', { voicePipeline: 's2s' })),
    mk('thinking', { turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 } }),
    mk('processing-long', { turn: { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW - 12_000 } }),
    mk('speaking', { speaking: true }),
    mk('followup', hf('FOLLOWUP')),
    mk('attention-confirm', { pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }] }),
    mk('attention-two', { pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }, { id: 'op2', ts: NOW - 5_000, summary: '要解锁车门吗？' }], queued: 1 }),
    mk('attention-location', { pendingLocation: true }),
    mk('looking', { visionCapturing: true }),
    mk('reconnecting', { connStatus: 'connecting', connChangedAt: NOW - 5_000 }),
    mk('offline-with-confirm', { connStatus: 'closed', connChangedAt: NOW - 30_000, pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }], queued: 2 }),
    mk('error', { lastError: { text: '出错了', at: NOW - 500 } }),
    mk('deg-permission', { degradations: [{ kind: 'permission_denied', what: 'mic', text: '需要麦克风权限，请在系统设置里允许' }] }),
    mk('deg-service', { degradations: [{ kind: 'service_degraded', text: '语音链路降级，本轮回落三段式' }] }),
    mk('deg-echo', { degradations: [{ kind: 'audio_echo_degraded', reason: 'repeated-self-trigger' }] }),
    mk('deg-transport-unknown', { degradations: [{ kind: 'transport_unknown', messageIds: ['m1'] }] }),
    mk('deg-recoverable', { degradations: [{ kind: 'recoverable_error', text: '响应超时了', at: NOW }] }),
    mk('deg-safety-blocked', { degradations: [{ kind: 'safety_blocked', text: '高速行驶中请勿打开车窗/天窗' }] }, false),
    mk('deg-fatal', { degradations: [{ kind: 'fatal', text: 'token 握手失败，请重新配置服务器' }] }),
  ]
}
```

- [ ] **Step 4: 写画廊屏（替换 Task 7 的占位）**

```tsx
// mobile/src/app/state-gallery.tsx
// 状态画廊（UX v2.1 §11.2 B1 验收 + §11.4 可读性判据的取数屏）。与 card-gallery 同类：
// dev 取证入口，不进主链路；`?only=` 直达；每条标「真栈可产 / 仅样本」。
import { useLocalSearchParams } from 'expo-router'
import { useMemo } from 'react'
import { ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { presenceFixtures } from '@/core/presence/fixtures'
import { settingsStore } from '@/core/settings/store'
import { Chip } from '@/features/cards/parts'
import { FocusDock } from '@/features/chat/FocusDock'
import { PresenceCapsule } from '@/features/chat/PresenceCapsule'
import { AuroraBackground, AuroraOrb } from '@/ui/aurora'
import { usePalette } from '@/ui/theme'

export default function StateGallery() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const { only } = useLocalSearchParams<{ only?: string }>()
  const all = useMemo(() => presenceFixtures(), [])
  const fixtures = useMemo(() => {
    const keys = (only || '').split(',').map((k) => k.trim()).filter(Boolean)
    return keys.length ? all.filter((f) => keys.some((k) => f.label.includes(k))) : all
  }, [all, only])
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <ScrollView contentContainerStyle={{ padding: 12, gap: 18 }}>
        <Text style={{ color: p.fg2, fontSize: p.font(12) }}>
          样本 {fixtures.length}{fixtures.length === all.length ? '' : `/${all.length}`} 条{only ? `（only=${only}）` : ''} · 主题跟随设置页 · 按钮在这里只记录不上行
        </Text>
        {fixtures.map((f) => (
          <View key={f.label} testID={`state-${f.label}`} style={{ gap: 6, borderTopWidth: 1, borderColor: p.line, paddingTop: 10 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Text style={{ color: p.fg3, fontSize: p.font(11), flex: 1 }}>{f.label} · primary={f.snapshot.primary}</Text>
              <Chip p={p} tone={f.producible ? 'accent' : 'amber'} text={f.producible ? '真栈可产' : '仅样本'} />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <AuroraOrb size={44} state={f.snapshot.primary} dim={f.snapshot.dim} animated />
              <View style={{ flex: 1 }}>
                <PresenceCapsule p={p} fontScale={settings.fontScale} snapshot={f.snapshot} />
              </View>
            </View>
            <FocusDock
              p={p}
              fontScale={settings.fontScale}
              snapshot={f.snapshot}
              onConfirm={() => {}}
              onCancelTurn={() => {}}
              onReenableBargeIn={() => {}}
            />
            <Text style={{ color: p.fg3, fontSize: p.font(10) }}>
              transport={f.snapshot.transport} capture={f.snapshot.capture} agent={f.snapshot.agent} mic={f.snapshot.privacy.mic} camera={f.snapshot.privacy.camera}
            </Text>
          </View>
        ))}
      </ScrollView>
    </View>
  )
}
```

`_layout.tsx` 注册：`<Stack.Screen name="state-gallery" options={{ title: '调试 · 状态画廊' }} />`。

- [ ] **Step 5: 跑测试 + 真机截图归档（深浅各一套）**

Run: `cd mobile && npx jest test/presenceFixtures.test.ts && npm run typecheck`
Expected: 3 PASS；`tsc` 0。真机 `xiaozhou://state-gallery` 深色一套、`cmd uimode night no` 浅色一套，存 `mobile/e2e/artifacts/b1-14-*.png`。⚠ 同屏 23 个光球都在动——画廊里 **`animated` 只给可见区**在 B1 不做（取证屏，帧率不是判据）；但要记一条读数：画廊滚动是否掉帧，进实施记录。

- [ ] **Step 6: 提交**

```bash
git add -- mobile/src/core/presence/fixtures.ts mobile/src/app/state-gallery.tsx mobile/src/app/_layout.tsx mobile/test/presenceFixtures.test.ts
git commit -m "feat(mobile): UX v2.1 B1-14 状态画廊 /state-gallery——8 主态 + 7 降级样本走生产 derivePresence，覆盖度守卫"
```

---

### Task 15: Maestro 扩流（06 / 09）+ e2e README

**Files:**
- Create: `mobile/e2e/06-confirm-dock.yaml`、`mobile/e2e/09-state-gallery.yaml`
- Modify: `mobile/e2e/README.md`

- [ ] **Step 1: 写两条流**

```yaml
# mobile/e2e/06-confirm-dock.yaml
# 流 ⑥：危险动作 → Focus Dock 钉住确认（倒计时在走）→ 取消 → Dock 消失（UX v2.1 P5）。
# 与 02 的区别：02 验的是气泡内确认（v1 路径，uxV2Dock 关时仍有效）；这条验承诺面。
# 「到期留痕」（300s）不在这里等——那条由 sessionStore.test.ts 用假时钟守；
# 这里只验「钉住 + 倒计时可见 + 取消后消失」。
appId: com.xiaozhou.companion
name: 06 危险动作进 Focus Dock
tags:
  - online
---
- runFlow: subflows/open-app.yaml
- tapOn:
    id: "composer-input"
- inputText: "打开后备箱"
- hideKeyboard
- tapOn:
    id: "composer-send"
- extendedWaitUntil:
    visible:
      id: "dock-confirm"
    timeout: 45000
- assertVisible:
    id: "dock-countdown"
- assertVisible:
    id: "presence-capsule"
- tapOn:
    id: "dock-cancel"
- extendedWaitUntil:
    notVisible:
      id: "dock-confirm"
    timeout: 20000
```

```yaml
# mobile/e2e/09-state-gallery.yaml
# 流 ⑨：状态画廊离线冒烟（CI 跑）：三个新光球态 + 承诺面 + 降级行渲得出来、不崩。
appId: com.xiaozhou.companion
name: 09 状态画廊离线冒烟
tags:
  - offline
---
- runFlow: subflows/open-app.yaml
- openLink: "xiaozhou://state-gallery?only=attention-two,looking,offline-with-confirm,deg-echo"
- extendedWaitUntil:
    visible:
      id: "state-attention-two"
    timeout: 20000
- assertVisible: "另有 1 个待处理"
- assertVisible: "条消息排队中"
- assertVisible: "环境回声较强"
```

- [ ] **Step 2: 跑**

Run: `maestro test --no-reinstall-driver mobile/e2e/09-state-gallery.yaml && maestro test --no-reinstall-driver mobile/e2e/06-confirm-dock.yaml`
Expected: 各 `1/1 Flows Passed`。⚠ 06 的 `hideKeyboard` 保留——它防的是 Maestro 树问题（坑账），与 08 验的遮挡是两件事。

- [ ] **Step 3: README 加三条流与状态**

在 `mobile/e2e/README.md` 「状态」段下追加：`06 危险动作进 Focus Dock（online）/ 08 键盘不遮发送键（online）/ 09 状态画廊离线冒烟（offline，CI）`，并写实跑读数（时长）。`.github/workflows/mobile-apk.yml` 若按 tag 跑 offline，09 会自动进 CI——**不改 workflow**（CI/CD 是红线路径，改了发版要摘要批准）。

- [ ] **Step 4: 提交**

```bash
git add -- mobile/e2e/06-confirm-dock.yaml mobile/e2e/09-state-gallery.yaml mobile/e2e/README.md
git commit -m "test(mobile): UX v2.1 B1-15 Maestro 06 承诺面 / 09 状态画廊离线冒烟"
```

---

### Task 16: B1 真机验收轮（方案 §11.2 B1 清单逐条打钩）

**Files:**
- 无代码；读数写进 Task 17 的实施记录

- [ ] **Step 1: 逐条取证（MIX Fold 4，`target=cloud`）**

| # | 项 | 做法 | 判据 |
|---|---|---|---|
| 1 | 免唤醒开 → 光球 `armed` 青环 | 设置开免唤醒 → 回对话页截图 | 青环 0.18α 可见；胶囊「说「小舟小舟」」3s 内出现 |
| 2 | 唤醒 → `listening` | 真人说「小舟小舟」（**需泓舟**） | 光球青环 0.4α + 胶囊「在听…」带青点 |
| 3 | 危险动作 → Dock | 「打开后备箱」 | `dock-confirm` + 倒计时递减（两次截图相差 ≥5s 数字变小） |
| 4 | 到期留痕 | 等 300s 不点 | Dock 消失**同时**记录里出现「⏱ 「要打开后备箱吗？」的确认已过期」 |
| 5 | 待确认 + 断网 | 「打开后备箱」后开飞行模式 ≥30s | 光球 `muted`、胶囊「已断开 · 消息会排队」、**Dock 仍钉着确认** |
| 6 | 隐私栏三行 | 免唤醒待机 / PTT 中 / 端到端录音中各截一张 | 麦克风行分别为「本机处理」「本机处理」「原始音频上传中（琥珀）」 |
| 7 | VAL 拒绝 → Dock | 云栈 debug 注入 `speed_kmh=90` 后说「打开天窗」 | **B1 预期：只在气泡里看到 VAL 原话，Dock 无 `safety_blocked`**（协议无结构化标记，接手须知 5）——把这条写成「未接、待 Q16」而不是绿 |
| 8 | 状态画廊 | 深浅各一套截图 | 23 条样本全渲，`presenceFixtures.test.ts` 绿 |
| 9 | 200% 字号 | 系统「显示大小/字体」拉到最大 → 对话页 / Dock / 胶囊 / Onboarding 截图 | 不裁字；Dock 按钮仍 ≥48dp |
| 10 | 键盘 | Maestro 08 | 通过 |
| 11 | 回滚 | 关两个实验室开关 | v1 四条状态条与气泡内确认回来；Maestro 02 仍通过 |
| 12 | 性能 | 语音层未做（B2），本批只看：Dock 出现时对话列表不卡顿；画廊滚动帧率 | 主观不掉帧；记读数 |
| 13 | 无障碍 | Accessibility Scanner 扫对话页 | 严重项 0（先取基线，超出记进遗留） |

- [ ] **Step 2: 反向验证两条**

- 把 `presence.ts` 里 `if (transport === 'offline') primary = 'muted'` 临时移到 `hasAttention` 之后 → `npx jest test/presence.test.ts` 应红两条（优先级 + 评审案例）；还原。
- 把 `FocusDock.tsx` 的 `pinCommitment` 换成 `items[0]` → `attention-two` 样本里钉住的变成先到的 op1 而不是最早到期（本例相同）……这条反向验证**无区分度**，改用 `commitment.test.ts` 的排序用例（已有）。写进记录：Dock 的排序判据由纯函数测试守，组件层不再重复验。

---

### Task 17: 记录收口

**Files:**
- Modify: `docs/design/2026-08-24-mobile-app-implementation-plan.md`（§7 末尾加一行指针：「UX v2.1 B1 实施记录见 `2026-08-29-mobile-ux-v2-b1-implementation-plan.md` §11」）
- Modify: 本文件 §11 实施记录（读数、遗留、坑）
- Modify: `AGENTS.md` §4.1 Android 行（B1 状态 + 下一步 B2）
- Modify: `mobile/.gitignore`（加 `e2e/artifacts/`）

- [ ] **Step 1: 写实施记录**：每个任务的读数（jest 计数 234 → N、`tsc` 0、Maestro 06/08/09 时长、真机 13 条打钩表、反向验证结果、画廊帧率主观读数）；遗留（`slot` / `safety_blocked` 无产出方待 Q16/Q19；`reenableBargeIn` 的关再开是权宜、B2 语音层接管；键盘读数与选的路径）。
- [ ] **Step 2: 提交**

```bash
git add -- docs/design/2026-08-29-mobile-ux-v2-b1-implementation-plan.md docs/design/2026-08-24-mobile-app-implementation-plan.md AGENTS.md mobile/.gitignore
git commit -m "docs(mobile): UX v2.1 B1 实施记录 + 验收读数 + 遗留出账"
```

---

## 3. 任务依赖与并行度

```
T1 tokens ─┐
T2 commitment ─┬─ T3 presence ─┬─ T6 光球（OrbState 单一来源）─┐
T4 store ──────┘               ├─ T8 usePresence ─ T9 组件 ─ T10 接线 ─ T11 隐私栏 ─ T12 键盘 ─ T13 Onboarding ─ T14 画廊 ─ T15 e2e ─ T16 验收 ─ T17 记录
T5 信号 ───────────────────────┘
T7 开关（独立，T10 之前）
```

T1/T2/T4/T5/T7 互不依赖，可由不同 subagent 并行（各自只加自己的路径提交）；T3 依赖 T2；**T6 依赖 T3**（`OrbState` 从 presence.ts 引）；T8 依赖 T3/T4/T5/T7；之后串行。

## 4. 「不负优化」判据在 B1 的取数点（方案 §11.4）

| 判据 | B1 取数 |
|---|---|
| 首反馈时延 | PTT 按下 → 光球 `listening`：`usePtt` 的 `setState('recording')` 到 Composer 重渲，本批不加打点，用 Reanimated 的 `useAnimatedReaction` 打点留到 B2 语音层（那里才有唤醒→听的真实链） |
| 状态可读性 | 画廊 6 张截图（armed / listening / thinking / speaking / attention / muted）给 5 名外部用户——**B2 后统一做**（评审建议的闸在 B2→B3 之间） |
| 承诺不丢 | Task 16 第 4/5 条 |
| 键盘 | Task 12 |
| 性能 | Task 16 第 12 条（主观）；同屏循环动画实例数：对话页常态 = Composer 主球 1 个（顶栏球只在 busy 时动） |
| 无障碍 | Task 16 第 13 条 |
| 回归 | `npm test` 条数只增不减；hmi node:test 288 不变（本批不碰 hmi） |

## 5. 实施判断（写在开工前，做的时候撞到再补）

1. **`Msg` 不能加字段**（共享类型）⇒ 「发送状态未知」「过程首帧时刻」「错误出现时刻」全部用并列结构（`uncertainIds` / `seenAt` map）；到期留痕用**追加一条消息**而不是改原气泡。
2. **剪枝调度改 `setTimeout` 链**而不是继续 `setInterval`：判据（`prunePendings`）一字不改，只改触发粒度；测试用假时钟量「300s 整出账」。
3. **`slot` / `safety_blocked` 只有类型与样本**：协议里没有 `missing_slots`、没有 VAL 拒绝标记，客户端不许猜。方案 Q16 已挂 `confirm_policy`；本计划**新增 Q19：`final.missing_slots`**（NEED_SLOT 的结构化字段）——写进方案 §13 由泓舟排期。
4. **回声「重新开启插话」= 关再开免唤醒**：voiceLoop 的 `_bargeInDisabled` 会话级不可恢复，B1 不改共享 FSM；B2 语音层给它正式出口。
5. **`audio_echo_degraded` 的可见性依赖 M4-R1 那条「回声防线拿不到播报文本」的修复已提交但真机未复验**（AGENTS.md）——B1 验收若 Dock 从不出现这条，先证明 `onDisableBargeIn` 有没有被调到（Task 5 的单测证明接线，真机证明触发），别把「没出现」读成「修好了」。
6. **顶栏品牌球不读 `primary`**：方案 §5.1 写明它只 idle/thinking；状态锚是 Composer 主球，两个球同时表达状态会打架。
7. **画廊 23 个光球同屏全动**：取证屏不管帧率；但如果真机滚动明显掉帧，记读数、不改（B4 的 reduce-motion 会给画廊一个静帧开关）。
8. **Task 12 的两条路径都要留 Maestro 08**：读数是「今天这台机、这个输入法」的，流才是以后不回退的判据。
