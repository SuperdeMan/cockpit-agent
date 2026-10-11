// 根导航器是否就绪：给组件树之外发命令式导航的地方用（目前只有 `src/app/+native-intent.tsx`）。
//
// 为什么需要（2026-10-11 OPPO 真机闪退定因）：`router.navigate / dismissTo` 只是把动作放进 expo-router 的
// routingQueue，真正派发在它自己的 effect 里，派发前 `store.assertIsReady()`——根导航器还没挂上就**在那个
// effect 里抛**「Attempted to navigate before mounting the Root Layout component」，release 包里是致命异常、
// 整个 App 闪退。运行期深链（`initial: false`）完全可能早于根导航器就绪送达：导航容器一挂上就订阅了 url 事件，
// 而根导航器要等初始状态解析完、根布局渲染出 Stack 才就绪；这期间冷启动途中的第二次 `am start`、桌面快捷方式
// 连点（singleTask 走 onNewIntent）都会落进来。抛点在别人的 effect 里，我们 try/catch 包不住，只能**就绪之后再发**。
//
// 判据只认导航容器自己的 `isReady()`（与那条断言同一个判据），由根布局用官方 hook 拿到容器 ref 后登记进来；
// 登记之前一律视为未就绪。

/** 轮询间隔：深链只在启动那一小段会等，50 ms 一次的开销可以忽略。 */
export const NAV_READY_POLL_MS = 50

/** 最多等多久。慢机冷启动根导航器几秒内总会就绪；再等不到就把 URL 交回 router（它自己的处理带 try/catch）。 */
export const NAV_READY_WAIT_MS = 10_000

type Probe = () => boolean

let probe: Probe | null = null

/** 根布局登记就绪判据；返回值用作 effect 清理，只撤自己登记的那一个。 */
export function registerNavigatorProbe(p: Probe): () => void {
  probe = p
  return () => {
    if (probe === p) probe = null
  }
}

export function navigatorReady(): boolean {
  try {
    return probe?.() === true
  } catch {
    return false
  }
}

/** 等根导航器就绪：就绪 ⇒ true；`waitMs` 内等不到 ⇒ false。按轮询次数计时，不依赖墙钟。 */
export function whenNavigatorReady(waitMs = NAV_READY_WAIT_MS, pollMs = NAV_READY_POLL_MS): Promise<boolean> {
  if (navigatorReady()) return Promise.resolve(true)
  return new Promise((resolve) => {
    let left = Math.max(1, Math.ceil(waitMs / pollMs))
    const tick = () => {
      if (navigatorReady()) return resolve(true)
      left -= 1
      if (left <= 0) return resolve(false)
      setTimeout(tick, pollMs)
    }
    setTimeout(tick, pollMs)
  })
}
