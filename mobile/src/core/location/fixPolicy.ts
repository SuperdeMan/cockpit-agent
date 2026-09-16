// 发送前「拿一个坐标」的取值策略（2026-09-16 时延复盘）——纯逻辑、注入依赖、node 直接单测。
//
// 要修的形态（OPPO PEUM00 / prod `40ccb9b6e` / 对话页 `/turn-timeline` 实测）：
//   16:39:05 发「今天天气怎么样」 → `+20094ms request_sent` → 云端 9.5s → 首片音频 +29.7s。
//   发送前那 20s 一个字节都没上行，用户看着「思考中」转圈。
// 成因链：`currentFix` 用 `Accuracy.Highest` 等**新**定位、上限 20s；expo-location 把它翻译成 GMS
//   `CurrentLocationRequest{priority=HIGH_ACCURACY, maxUpdateAge=1000ms}`——系统缓存里只要不是 1s 内的
//   定位一律不认；室内 GPS 没有天空、GMS 的网络定位在大陆经常等不到 ⇒ 稳定地等满 20s 才回落到
//   `getLastKnownPositionAsync()`。HMI 那边浏览器 `maximumAge: 30s / timeout: 10s`，桌面机秒回，
//   于是「同一句话 Android 比 HMI 慢很多」。
//
// 判据（三条，按序）：
//   ① 系统缓存里有不超过 FRESH_FIX_MAX_AGE_MS 的定位 ⇒ **立刻用它发**，一毫秒都不等。
//      天气 / 周边 / 出发地对几分钟前的坐标都不敏感，而 `current_location_at` 会如实上行，新鲜度仍由下游判。
//      缓存超过 REFRESH_AFTER_MS ⇒ 顺手让调用方在后台补取一次（不阻塞本轮），让下一轮更新。
//   ② 缓存里没有 ⇒ 现取一次，但**只等 budget**（缺省 FIX_BUDGET_MS）。这条新取的 promise 不取消——
//      它晚点 resolve 会把系统缓存喂热，正是下一轮①能命中的原因。
//   ③ 预算到了还没有 ⇒ 缓存里任何年龄的定位都比没有强（同旧行为的回落），再没有就不带坐标照发
//      （Q4 判据：闸认不准就放行，后端诚实降级）。
export interface Fix {
  lat: number
  lng: number
  accuracyM?: number
  /** 定位产生的墙钟时刻（ms）。缺失 ⇒ 当作「不知道多旧」，命中缓存也要求后台补取 */
  capturedAt?: number
}

export interface FixTimers {
  set(fn: () => void, ms: number): unknown
  clear(id: unknown): void
}

export interface FixDeps {
  /** 系统缓存里不超过 maxAgeMs 的定位；不传 maxAgeMs = 不限龄。没有 ⇒ null */
  lastKnown(maxAgeMs?: number): Promise<Fix | null>
  /** 现取一次新定位。可能很久才 resolve（室内 GPS）；策略只等预算，**不取消它** */
  current(): Promise<Fix | null>
  now(): number
  timers?: FixTimers
}

export type FixSource = 'cached' | 'fresh' | 'stale' | 'none'

export interface FixResult {
  fix: Fix | null
  source: FixSource
  /** true = 调用方应在后台补取一次（缓存偏旧 / 回落到了陈旧坐标 / 什么都没拿到） */
  refresh: boolean
  /** 本轮为拿坐标真的等了多久（ms） */
  waitedMs: number
}

export interface AcquireOpts {
  budgetMs?: number
  freshMaxAgeMs?: number
  refreshAfterMs?: number
  /** true = 跳过第②步的等待，直接回落到陈旧缓存（调用方知道最近一次现取刚失败过：室内 / 无天空，
   *  再等一次预算也是白等——只让后台补取继续试）。仍会现取一次以喂热缓存，只是不等它 */
  skipWait?: boolean
}

/** 缓存不超过这个年龄就直接用（天气 / 周边 / 出发地对分钟级陈旧不敏感） */
export const FRESH_FIX_MAX_AGE_MS = 5 * 60_000
/** 命中的缓存比这还旧 ⇒ 后台补取（本轮照发） */
export const REFRESH_AFTER_MS = 60_000
/** 缓存里没有时，发送前最多等新定位这么久 */
export const FIX_BUDGET_MS = 3_000
/** 用户在征询条上点了「同意」：这是显式动作，多等一会儿换一个真坐标 */
export const CONSENT_FIX_BUDGET_MS = 8_000
/** 后台预热 / 补取一次的上限：到点就当这次没取到，让下一次预热还能发起 */
export const WARM_FIX_BUDGET_MS = 20_000
/** 一次现取刚在预算内失败过（室内 / 无天空），这段时间内的发送不再等：直接用陈旧缓存，后台继续试。
 *  真机（OPPO，室内，候选包 `bcb10eb08-dirty`）：失败后 2 分钟内的两轮 `request_sent` +93 / +90ms，
 *  超过退避再发的两轮各等满预算 +3109 / +3097ms——GMS 在这台机上室内从未在预算内给过定位，
 *  每 2 分钟让用户再付一次 3s 没有换来任何东西，退避放到 10 分钟。 */
export const FRESH_FAILURE_BACKOFF_MS = 10 * 60_000

const defaultTimers: FixTimers = {
  set: (fn, ms) => {
    const id = setTimeout(fn, ms)
    // node（jest）下真定时器要 unref，否则 worker 不退出；RN 里 id 是数字，可选链跳过
    ;(id as unknown as { unref?: () => void })?.unref?.()
    return id
  },
  clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
}

/** 等 p 最多 ms；超时返回 null，p 自己继续跑（不取消） */
export function withinBudget<T>(p: Promise<T | null>, ms: number, timers: FixTimers = defaultTimers): Promise<T | null> {
  return new Promise<T | null>((resolve) => {
    let settled = false
    const id = timers.set(() => {
      if (settled) return
      settled = true
      resolve(null)
    }, ms)
    p.then(
      (v) => {
        if (settled) return
        settled = true
        timers.clear(id)
        resolve(v)
      },
      () => {
        if (settled) return
        settled = true
        timers.clear(id)
        resolve(null)
      },
    )
  })
}

export async function acquireFix(deps: FixDeps, opts: AcquireOpts = {}): Promise<FixResult> {
  const budget = opts.budgetMs ?? FIX_BUDGET_MS
  const freshMaxAge = opts.freshMaxAgeMs ?? FRESH_FIX_MAX_AGE_MS
  const refreshAfter = opts.refreshAfterMs ?? REFRESH_AFTER_MS
  const t0 = deps.now()
  const waited = (): number => Math.max(0, deps.now() - t0)

  // ① 够新的缓存：立刻用
  const cached = await deps.lastKnown(freshMaxAge).catch(() => null)
  if (cached) {
    const age = typeof cached.capturedAt === 'number' && cached.capturedAt > 0 ? deps.now() - cached.capturedAt : Infinity
    return { fix: cached, source: 'cached', refresh: age > refreshAfter, waitedMs: waited() }
  }

  // ② 现取，只等预算（刚失败过就一毫秒都不等，但仍发起一次让缓存有机会变热）
  const attempt = deps.current().catch(() => null)
  const fresh = opts.skipWait ? null : await withinBudget(attempt, budget, deps.timers)
  if (fresh) return { fix: fresh, source: 'fresh', refresh: false, waitedMs: waited() }

  // ③ 任何年龄的缓存 > 没有
  const stale = await deps.lastKnown().catch(() => null)
  if (stale) return { fix: stale, source: 'stale', refresh: true, waitedMs: waited() }
  return { fix: null, source: 'none', refresh: true, waitedMs: waited() }
}
