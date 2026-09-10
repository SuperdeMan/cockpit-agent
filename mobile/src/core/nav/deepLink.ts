// 外部入口（桌面 App Shortcuts、通知、adb 深链）到路由的规范化。
//
// 为什么需要这一层（2026-09-10 OPPO 实测定因）：
// expo-router 对**运行中**收到的深链发的是 `NAVIGATE` 且不带 `pop`，而 StackRouter 只在
// 「目标恰好是栈顶」时复用现有屏，否则一律推一个新的。于是从桌面反复点「说话」会一屏一屏
// 地堆下去，用户没有任何办法清掉（点 20 次：Views 151→3483、PSS 207→407MB，完全线性）。
// `xiaozhou://voice` 还要更糟：它先推一个 voice 屏，再被 `Redirect` 的 replace 换成
// **第二个对话页**——每点一次就多一整个对话页。
//
// 判据：**深链是「把我送到一个目的地」，不是「再开一份」。** 由此三条：
//   1. 认得出的目的地：先回到栈底的对话页，再进目的地 ⇒ 无论点多少次，栈深恒 ≤ 2；
//   2. `voice` 不是目的地而是一条指令（「回对话页并升起语音层」），规范化成对话页的
//      参数，根本不占一屏；
//   3. **认不出的一律原样交回 router**——dev 客户端的 `exp://…/--/…` 形态、以后新增的
//      路由、第三方回跳，都不猜也不吞。少改一个 URL 只是没优化，猜错一个 URL 是丢功能。
//
// 这里只出判据，不碰 router；副作用在 `src/app/+native-intent.tsx` 里执行，
// 于是「该怎么走」这件事可以脱离原生环境单测。

/** 本 App 的 scheme（`app.config.ts` 里三档共用同一个字面量）。 */
const APP_SCHEME = 'xiaozhou://'

/** 语音层深链：`xiaozhou://voice`（桌面 Shortcut「说话」）。它是指令不是目的地。 */
const VOICE_PATH = '/voice'

/** 对话页收到的「请升起语音层」参数。ChatScreen 消费后**自己清掉**，所以下一次到达能再触发。 */
export const VOICE_PARAM = 'voice'

export type IntentPlan =
  /** 交回 router 自己处理：冷启动（栈还没建），或这条 URL 我们不认识。 */
  | { kind: 'handoff'; href: string }
  /** 运行中收到的、认得出的目的地：由我们按「进入一个目的地」的语义导航。 */
  | { kind: 'enter'; href: string; home: boolean }

export type IntentStep =
  | { op: 'dismissTo'; href: string }
  | { op: 'navigate'; href: string }

/**
 * 拆出 URL 里的路由部分。**只认本 App 的 scheme**，其它一切返回 null。
 *
 * `xiaozhou://voice` 的 `voice` 落在 host 位而不是 path 位，`xiaozhou:///settings` 才在
 * path 位——两种写法在 expo-router 里等价，所以这里统一补成 `/voice` / `/settings`。
 */
export function splitAppUrl(url: string): { path: string; query: string } | null {
  if (typeof url !== 'string') return null
  if (url.slice(0, APP_SCHEME.length).toLowerCase() !== APP_SCHEME) return null
  let rest = url.slice(APP_SCHEME.length)
  const hash = rest.indexOf('#')
  if (hash >= 0) rest = rest.slice(0, hash)
  const q = rest.indexOf('?')
  let path = q >= 0 ? rest.slice(0, q) : rest
  const query = q >= 0 ? rest.slice(q + 1) : ''
  if (!path.startsWith('/')) path = `/${path}`
  // 结尾的 `/` 不改变目的地；去掉它，`/settings/` 与 `/settings` 才不会被当成两个地方
  if (path.length > 1 && path.endsWith('/')) path = path.replace(/\/+$/, '') || '/'
  return { path, query }
}

/** 往 query 里补一个参数；已经有同名键就不重复加（保持幂等）。 */
function withParam(query: string, key: string, value: string): string {
  const pairs = query ? query.split('&').filter(Boolean) : []
  if (pairs.some((p) => p.split('=')[0] === key)) return pairs.join('&')
  pairs.push(`${key}=${value}`)
  return pairs.join('&')
}

function toHref(path: string, query: string): string {
  return query ? `${path}?${query}` : path
}

/** 目的地是不是对话页（栈底那一层）。 */
export function isHome(href: string): boolean {
  const path = href.split('?')[0]
  return path === '' || path === '/'
}

/**
 * 把一条外部 URL 规范化成 href。认不出就原样返回——判据见文件头第 3 条。
 */
export function normalizeIntentHref(url: string): string {
  const parsed = splitAppUrl(url)
  if (!parsed) return url
  if (parsed.path === VOICE_PATH) {
    // 「说话」= 回对话页并升层。变成对话页的参数，栈里就不会多出 voice 屏，
    // 也不会因为 Redirect 的 replace 而多出第二个对话页。
    return toHref('/', withParam(parsed.query, VOICE_PARAM, '1'))
  }
  return toHref(parsed.path, parsed.query)
}

/**
 * 这条 URL 该怎么走。
 *
 * `initial=true`（冷启动的首个 URL）一律 handoff：那时栈还不存在，router 自己建的初始状态
 * 就是对的，我们插手只会把它建歪。
 */
export function planIntent(url: string, opts: { initial: boolean }): IntentPlan {
  const parsed = splitAppUrl(url)
  if (!parsed) return { kind: 'handoff', href: url }
  const href = normalizeIntentHref(url)
  if (opts.initial) return { kind: 'handoff', href }
  return { kind: 'enter', href, home: isHome(href) }
}

/**
 * 运行中进入一个目的地的动作序列。
 *
 * 目的地是对话页 ⇒ 一步 `dismissTo`：弹掉它上面的所有屏并把新参数带上（栈里没有对话页时
 * expo-router 的 POP_TO 会用它替换当前屏，也不会堆积）。
 * 目的地不是对话页 ⇒ 先回对话页再进去，保证「返回」落到对话页而不是用户上次留下的半截栈。
 * 两种情况的栈深都恒定，点一万次也一样。
 */
export function intentSteps(plan: Extract<IntentPlan, { kind: 'enter' }>, canDismiss: boolean): IntentStep[] {
  if (plan.home) return [{ op: 'dismissTo', href: plan.href }]
  const steps: IntentStep[] = []
  if (canDismiss) steps.push({ op: 'dismissTo', href: '/' })
  steps.push({ op: 'navigate', href: plan.href })
  return steps
}
