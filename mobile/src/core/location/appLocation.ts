// 定位桥（实施计划 M1-2）：取坐标 + @shared/location.mjs 纯函数拼 meta 键。
// meta 键与 HMI 完全同构（§2.2），仅 current_location_source 按契约填 'app'
// （source 是信息性透传，编排只按键归 scope——orchestrator/cloud/context.py:53）。
// 位置闸判定（isLocationDependent / shouldRequestLocationConsent）在 sendRouter 引共享纯导出；
// 本文件只做「取坐标」这半（守卫条款：共享侧用 navigator 的那个取坐标函数 mobile 禁引）。
//
// 取值策略在 `fixPolicy.ts`（缓存即用 → 最多等 2.5s → 陈旧回落 → 不带坐标照发）。坐标来源（2026-09-17）：
//   首选 modules/platformlocation——系统 LocationManager 各 provider 的 last-known + 向 network/gps 直接要一次；
//   原生缺席（旧 APK / jest）时退回 expo-location（GMS fused）那条老路。为什么换：见 fixPolicy.ts 头注第二段。
// 权限只读系统已授予的（申请仍走 expo-location，与征询条那条流程一致）。
import * as Location from 'expo-location'

import { buildRequestLocationMeta } from '@shared/location.mjs'

import platform, { type PlatformFix } from '../../../modules/platformlocation'
import type { LocationAcquisition, LocationBridge } from '../session/store'
import { settingsStore } from '../settings/store'
import {
  CONSENT_FIX_BUDGET_MS,
  FIX_BUDGET_MS,
  FRESH_FAILURE_BACKOFF_MS,
  FRESH_FIX_MAX_AGE_MS,
  WARM_FIX_BUDGET_MS,
  acquireFix,
  pickFreshest,
  withinBudget,
  type Fix,
  type FixDeps,
  type FixResult,
} from './fixPolicy'

function toFix(pos: Location.LocationObject | null | undefined): Fix | null {
  if (!pos || !pos.coords) return null
  return {
    lat: pos.coords.latitude,
    lng: pos.coords.longitude,
    accuracyM: pos.coords.accuracy ?? undefined,
    capturedAt: pos.timestamp,
    provider: 'gms',
  }
}

/** 系统 provider 的读数 → Fix。年龄用单调时钟折算成墙钟时刻（墙钟可能被拨过，`time` 不可信） */
function fromPlatform(f: PlatformFix, now: number): Fix {
  return {
    lat: f.latitude,
    lng: f.longitude,
    accuracyM: typeof f.accuracy === 'number' && Number.isFinite(f.accuracy) ? f.accuracy : undefined,
    capturedAt: now - Math.max(0, f.ageMs),
    provider: f.provider,
  }
}

/** 已授权 ⇒ true；未授权且 request=true ⇒ 弹系统申请（发送路径与征询路径都可能是首次授权） */
async function ensurePermission(request: boolean): Promise<boolean> {
  try {
    const perm = await Location.getForegroundPermissionsAsync()
    if (perm.granted) return true
    if (!request) return false
    return (await Location.requestForegroundPermissionsAsync()).granted
  } catch {
    return false
  }
}

/** 正在进行的现取（预热与发送共用同一条：同一时刻设备上最多一个定位请求在飞） */
let inflight: Promise<Fix | null> | null = null
/** 最近一次现取在预算内没拿到的时刻；0 = 没有 */
let lastFreshFailureAt = 0
/** 最近一次取值的读数（时间线 `location_acquired` 读它；不含坐标） */
let lastAcq: LocationAcquisition | null = null

async function lastKnownFix(maxAgeMs?: number): Promise<Fix | null> {
  if (platform) {
    const best = pickFreshest(platform.lastKnown(), maxAgeMs)
    return best ? fromPlatform(best, Date.now()) : null
  }
  // 旧 APK / jest：GMS fused 的 lastLocation，maxAge 由 expo 在原生侧按 location.time 过滤
  return toFix(await Location.getLastKnownPositionAsync(maxAgeMs ? { maxAge: maxAgeMs } : {}))
}

/** 现取一次。已经有一条在飞就复用它——发送路径等的就是预热那条，不再叠一个请求。
 *  系统 provider：network + gps 各要一次、先到先用（modules/platformlocation）。
 *  兜底（原生缺席）：GMS `Highest` + `timeInterval: 5min`（expo-location 57 把它喂给 `CurrentLocationRequest.maxUpdateAge`，
 *  test/expoLocationNative.test.ts 钉住这条映射）。到 WARM_FIX_BUDGET_MS 仍没结果就放手（记一次失败）。 */
function currentFix(): Promise<Fix | null> {
  if (inflight) return inflight
  const native: Promise<Fix | null> = platform
    ? platform.requestFix(WARM_FIX_BUDGET_MS).then((f) => (f ? fromPlatform(f, Date.now()) : null)).catch(() => null)
    : Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Highest, timeInterval: FRESH_FIX_MAX_AGE_MS })
        .then(toFix)
        .catch(() => null)
  const attempt = withinBudget(native, WARM_FIX_BUDGET_MS).then((fix) => {
    if (!fix) lastFreshFailureAt = Date.now()
    return fix
  })
  inflight = attempt
  void attempt.finally(() => {
    if (inflight === attempt) inflight = null
  })
  return attempt
}

const deps: FixDeps = {
  lastKnown: lastKnownFix,
  current: currentFix,
  now: () => Date.now(),
}

/** 刚在预算内失败过 ⇒ 这次发送不等（连珠炮式连问只付一次预算；后台那条继续试） */
function recentlyFailed(): boolean {
  return lastFreshFailureAt > 0 && Date.now() - lastFreshFailureAt < FRESH_FAILURE_BACKOFF_MS
}

function record(r: FixResult): void {
  const at = r.fix?.capturedAt
  lastAcq = {
    source: r.source,
    provider: r.fix?.provider ?? '',
    ageMs: typeof at === 'number' && at > 0 ? Math.max(0, Date.now() - at) : -1,
    waitedMs: r.waitedMs,
  }
}

/** 仅测试：清掉模块级状态 */
export function resetLocationStateForTest(): void {
  inflight = null
  lastFreshFailureAt = 0
  lastAcq = null
  warming = null
}

/** 共享纯函数拼键（数值格式单一来源），source 覆写为 'app'（仅在拿到坐标时） */
function metaOf(fix: Fix | null): Record<string, string> {
  if (!fix) return {}
  const meta = buildRequestLocationMeta(true, fix) as Record<string, string>
  return Object.keys(meta).length ? { ...meta, current_location_source: 'app' } : {}
}

let warming: Promise<void> | null = null

/** 后台补取一次定位：不阻塞任何人、同一时刻只跑一条、到点就放手。
 *  结果本身不用——它的意义是把系统缓存喂热，下一轮 `acquireFix` 第①步直接命中。
 *  只在定位开关打开且已授权时才动，**不弹**权限申请（预热不是用户动作）。 */
export function warmLocation(): void {
  if (!settingsStore.getState().settings.locationEnabled) return
  if (warming) return
  warming = (async () => {
    try {
      if (!(await ensurePermission(false))) return
      await currentFix()
    } catch {
      /* 预热失败无所谓：发送路径有自己的三步回落 */
    } finally {
      warming = null
    }
  })()
}

/** SessionCore 的 LocationBridge 实现：开关读 settings，坐标即取即用不持久化 */
export const appLocationBridge: LocationBridge = {
  isEnabled(): boolean {
    return settingsStore.getState().settings.locationEnabled
  },
  async refreshMeta(): Promise<Record<string, string>> {
    if (!this.isEnabled()) return {}
    if (!(await ensurePermission(true))) return {}
    const r = await acquireFix(deps, { budgetMs: FIX_BUDGET_MS, skipWait: recentlyFailed() })
    record(r)
    if (r.refresh) warmLocation()
    // 等权限弹窗 / 等坐标（最长 FIX_BUDGET_MS）期间用户把定位关了：开头那次检查只覆盖「开始取值」这个时点，
    // 拿到的坐标不能再返回（2026-09-19 GPT-6 评审 F04）。SessionCore 拼帧与离线补发前各自还会再看一次开关
    if (!this.isEnabled()) return {}
    return metaOf(r.fix)
  },
  async enable(): Promise<Record<string, string> | null> {
    if (!(await ensurePermission(true))) return null
    // 显式动作：即使刚失败过也再等一次（预算更长），用户点了「同意」就是在等这个结果
    const r = await acquireFix(deps, { budgetMs: CONSENT_FIX_BUDGET_MS })
    record(r)
    if (!r.fix) return null
    settingsStore.getState().update({ locationEnabled: true })
    if (r.refresh) warmLocation()
    return metaOf(r.fix)
  },
  lastAcquisition(): LocationAcquisition | null {
    return lastAcq
  },
}
