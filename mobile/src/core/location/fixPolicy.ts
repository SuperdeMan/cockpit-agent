// 发送前「拿一个坐标」的取值策略（2026-09-16 时延复盘，2026-09-17 定位来源复盘）——纯逻辑、注入依赖、node 直接单测。
//
// 第一个要修的形态（OPPO PEUM00 / prod `40ccb9b6e` / 对话页 `/turn-timeline` 实测）：
//   16:39:05 发「今天天气怎么样」 → `+20094ms request_sent` → 云端 9.5s → 首片音频 +29.7s。
//   发送前那 20s 一个字节都没上行，用户看着「思考中」转圈。
// 成因链：`currentFix` 用 `Accuracy.Highest` 等**新**定位、上限 20s；expo-location 把它翻译成 GMS
//   `CurrentLocationRequest{priority=HIGH_ACCURACY, maxUpdateAge=1000ms}`——系统缓存里只要不是 1s 内的
//   定位一律不认；室内 GPS 没有天空、GMS 的网络定位在大陆经常等不到 ⇒ 稳定地等满 20s 才回落。
//
// 第二个要修的形态（2026-09-16 23:4x，同一台机在家）：回落到的 GMS fused 缓存是**三天前公司那一点**
//   （`dumpsys location`：fused provider 的 last location `et=+8d18h` 两天没变），而系统 network provider
//   在 23:42:52 / 23:47:52 都刚拿到家里的定位——GMS 那条路对它视而不见。于是「我在哪」答公司地址、
//   导航起点是公司，用户说「重新获取定位」也还是那份缓存。所以坐标来源改成系统 LocationManager
//   （modules/platformlocation：各 provider 的 last-known + 向 network/gps 直接要一次），GMS 只剩兜底。
//
// 判据（三条，按序）：
//   ① 系统缓存里有不超过 FRESH_FIX_MAX_AGE_MS 的定位 ⇒ **立刻用它发**，一毫秒都不等。
//      缓存超过 REFRESH_AFTER_MS ⇒ 顺手让调用方在后台补取一次（不阻塞本轮），让下一轮更新。
//   ② 缓存里没有 ⇒ 现取一次，但**只等 budget**（缺省 FIX_BUDGET_MS；系统 network provider 真机上
//      0.1–0.3s 就回）。这条新取的 promise 不取消——它晚点 resolve 会把系统缓存喂热。
//   ③ 预算到了还没有 ⇒ 缓存里任何年龄的定位都比没有强，**年龄随 `current_location_at` 如实上行**，
//      服务端按年龄决定能不能当「此刻」念（agents/_sdk/location.py）；再没有就不带坐标照发
//      （Q4 判据：闸认不准就放行，后端诚实降级）。
export interface Fix {
  lat: number
  lng: number
  accuracyM?: number
  /** 定位产生的墙钟时刻（ms）。缺失 ⇒ 当作「不知道多旧」，命中缓存也要求后台补取 */
  capturedAt?: number
  /** gps / network / fused / passive / gms；诊断标签，不上行 */
  provider?: string
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

/** 缓存不超过这个年龄就直接用。系统 network provider 每 5 分钟都在刷（sceneservice / 系统自己要），
 *  超过 1 分钟就现取一次——真机上 0.1–0.3s，换来起点 / 周边不再落在一分钟前的位置 */
export const FRESH_FIX_MAX_AGE_MS = 60_000
/** 命中的缓存比这还旧 ⇒ 后台补取（本轮照发） */
export const REFRESH_AFTER_MS = 30_000
/** 缓存里没有时，发送前最多等新定位这么久（network provider 真机 0.1–0.3s；GPS 户外几秒） */
export const FIX_BUDGET_MS = 2_500
/** 用户在征询条上点了「同意」：这是显式动作，多等一会儿换一个真坐标 */
export const CONSENT_FIX_BUDGET_MS = 8_000
/** 后台预热 / 补取一次的上限：到点就当这次没取到，让下一次预热还能发起 */
export const WARM_FIX_BUDGET_MS = 20_000
/** 一次现取刚在预算内失败过，这段时间内的发送不再等：直接用陈旧缓存，后台继续试。
 *  只防连珠炮式的连问各付一次预算；不能长——环境会变（出门上车 GPS 就有了），
 *  2026-09-16 那版 10 分钟的退避正是「重新获取定位」还给旧坐标的原因之一 */
export const FRESH_FAILURE_BACKOFF_MS = 30_000

/** 系统各 provider last-known 里挑最新鲜的一条（modules/platformlocation 的输出；纯函数，jest 直测）。
 *  判据：坐标合法、年龄非负且不超过 maxAgeMs（不传不限）；年龄最小者胜，同龄取精度更好的。 */
export interface PlatformFixLike {
  provider: string
  latitude: number
  longitude: number
  accuracy: number | null
  ageMs: number
}

export function pickFreshest<T extends PlatformFixLike>(fixes: readonly T[], maxAgeMs?: number): T | null {
  let best: T | null = null
  for (const f of fixes) {
    if (!Number.isFinite(f.latitude) || !Number.isFinite(f.longitude)) continue
    if (f.latitude < -90 || f.latitude > 90 || f.longitude < -180 || f.longitude > 180) continue
    if (f.latitude === 0 && f.longitude === 0) continue // 空值坐标
    if (!Number.isFinite(f.ageMs)) continue
    const age = Math.max(0, f.ageMs)
    if (maxAgeMs !== undefined && age > maxAgeMs) continue
    if (!best) { best = f; continue }
    const bestAge = Math.max(0, best.ageMs)
    if (age < bestAge) best = f
    else if (age === bestAge && (f.accuracy ?? Infinity) < (best.accuracy ?? Infinity)) best = f
  }
  return best
}

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

  // ③ 任何年龄的缓存 > 没有（年龄随 current_location_at 上行，服务端按它判能不能当「此刻」）
  const stale = await deps.lastKnown().catch(() => null)
  if (stale) return { fix: stale, source: 'stale', refresh: true, waitedMs: waited() }
  return { fix: null, source: 'none', refresh: true, waitedMs: waited() }
}
