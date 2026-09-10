// expo-router 的外部 URL 拦截点（`+native-intent`，SDK 52+ 的官方入口）。
//
// 只做两件事：把 URL 交给 `core/nav/deepLink` 出判据，再把判据执行掉。
// 判据、理由和真机实测数据都在那个文件里，这里刻意不放任何决策。
//
// 为什么两步都由我们发、而不是「先弹栈再把 URL 交回 router」：
// `router.*` 的动作进 `routingQueue`，由 React 渲染时排空；而返回 href 会让 expo-router
// 的 linking listener **当场同步**派发 NAVIGATE。两条路径的先后不可控——NAVIGATE 先落地
// 就会被随后排空的弹栈连目的地一起弹掉。同一个队列里按序发两步才没有这个竞态。
//
// 返回 null = 这条 URL 我们已经自己导航过了，别再交给 router——它会再推一屏，
// 那正是本次要修的缺陷。
import { router, type Href } from 'expo-router'

import { intentSteps, planIntent } from '@/core/nav/deepLink'

/**
 * 外部 URL 是运行期字符串，类型化路由那个字面量联合描述不了它，所以断言只能发生在这里
 * ——**唯一**一处边界。代价是已知的：路径不对应任何路由时 `navigate` 是静默 no-op
 * （本 App 没有 `+not-found`，在此之前这类深链同样什么都不做），用户会停在对话页。
 */
const asHref = (href: string): Href => href as Href

export function redirectSystemPath({ path, initial }: { path: string; initial: boolean }): string | null {
  const plan = planIntent(path, { initial })
  if (plan.kind === 'handoff') return plan.href
  for (const step of intentSteps(plan, router.canDismiss())) {
    if (step.op === 'dismissTo') router.dismissTo(asHref(step.href))
    else router.navigate(asHref(step.href))
  }
  return null
}
