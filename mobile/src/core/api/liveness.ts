// 连接探活（2026-08-27 真机实测逼出来的）。
//
// **要修的形态**：RN 的 WebSocket 在飞行模式下 `onclose`/`onerror` **都不来**
// （MIX Fold 4 / Android 16 实测：断网 4 分钟，`readyState` 仍是 OPEN、顶栏仍显示「在线」）。
// 后果不只是状态显示不对——`ResilientWebSocket.send()` 看 `isOpen` 为真就直接写进那个
// 死 socket，**帧不入队、直接丢**：复现过一次（开飞行模式后 1 秒内发送 → 恢复网络后
// 不补发 → 95s 后看门狗给「响应超时」）。「断线不丢消息」这条队列语义在这个窗口里是失效的。
//
// **判据取「独立探活失败」，不取「应用层静默」**——这是 `ws.mjs` 头注那条约束的分界：
// 静默可能只是后端在跑长任务（开思考 30s+），拿它判死会误杀健康连接；
// 而 HTTP 探不到 `/healthz` 是真实的网络不可达证据。
//
// **只在前台探**：后台本来就不保证（坑账 §9.5：省电模式会杀后台 socket，PoC 是前台交互档），
// 后台还定时发请求纯属耗电。
//
// ⚠ 已知限制（不许说成「全覆盖」）：探的是 HTTP 端点，不是这条 WS 本身。
// 「网络通、但服务端已经单方面关掉了这条连接而客户端不知道」这种情形探活探不出来，
// 那一类仍然靠 send 失败或看门狗兜底。
import { AppState, type AppStateStatus } from 'react-native'

export interface LivenessTimers {
  set(fn: () => void, ms: number): unknown
  clear(id: unknown): void
}

export interface LivenessOpts {
  /** 探一次：true=网络可达。默认实现见 `httpProbe` */
  probe: () => Promise<boolean>
  /** 只在连接自称 open 时探——已经 closed 的话重连链路在跑，再探没意义也费电 */
  isOpen: () => boolean
  /** 连续失败达阈值时调用（接 GatewaySession.reconnectNow） */
  onDead: () => void
  intervalMs?: number
  /** 连续失败几次才判死。1 次就判会被一次抖动误杀 */
  failThreshold?: number
  timers?: LivenessTimers
  /** 测试注入：省得在 jest 里摆弄真 AppState */
  appState?: {
    currentState: AppStateStatus
    addEventListener(type: 'change', h: (s: AppStateStatus) => void): { remove(): void }
  }
}

const DEFAULT_INTERVAL_MS = 15000
const DEFAULT_FAIL_THRESHOLD = 2
const PROBE_TIMEOUT_MS = 4000

/** 默认探针：HEAD/GET `{edgeUrl}/healthz`，4s 超时。
 *  ⚠ 超时必须自己写——裸 fetch 在断网时会一直挂着（M2 那次断网挂死的同一条老账）。 */
export function httpProbe(edgeUrl: string): () => Promise<boolean> {
  return async () => {
    const ctl = new AbortController()
    const t = setTimeout(() => ctl.abort(), PROBE_TIMEOUT_MS)
    try {
      const res = await fetch(`${edgeUrl.replace(/\/+$/, '')}/healthz`, {
        method: 'GET',
        signal: ctl.signal,
      })
      return res.ok
    } catch {
      return false
    } finally {
      clearTimeout(t)
    }
  }
}

/** 启动探活，返回 stop。重复 stop 是安全的。 */
export function startLiveness(opts: LivenessOpts): () => void {
  const interval = opts.intervalMs ?? DEFAULT_INTERVAL_MS
  const threshold = opts.failThreshold ?? DEFAULT_FAIL_THRESHOLD
  const timers: LivenessTimers = opts.timers ?? {
    set: (fn, ms) => setTimeout(fn, ms),
    clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
  }
  const appState = opts.appState ?? AppState

  let stopped = false
  let fails = 0
  let timer: unknown = null
  let inFlight = false

  const arm = () => {
    if (stopped) return
    timer = timers.set(tick, interval)
  }

  const tick = () => {
    timer = null
    if (stopped) return
    // 前台 + 自称 open 才探。任一不满足就跳过这一轮，并把失败计数清零：
    // 切后台期间的失败不该攒着，回到前台第一轮就判死是误杀。
    if (appState.currentState !== 'active' || !opts.isOpen() || inFlight) {
      fails = 0
      arm()
      return
    }
    inFlight = true
    void opts
      .probe()
      // probe 抛出等同于探不通：默认实现自己 catch 了，但注入的探针可能 reject——
      // 没有这一层就是一个未处理拒绝，而且**探活会静默停在这一轮**（比探不通更糟）。
      .then((ok) => ok, () => false)
      .then((ok) => {
        if (stopped) return
        if (ok) {
          fails = 0
          return
        }
        fails += 1
        // 开发期日志：连接类故障最难查的就是「到底探没探、探的结果是什么」，
        // 而这条链路平时完全静默。生产构建里 __DEV__ 为 false，整段被剥掉。
        if (__DEV__) console.log(`[liveness] probe failed (${fails}/${threshold})`)
        if (fails >= threshold) {
          fails = 0
          if (__DEV__) console.log('[liveness] 判死 → reconnectNow')
          opts.onDead()
        }
      })
      .finally(() => {
        inFlight = false
        arm()
      })
  }

  arm()
  // 回到前台立刻探一次：锁屏一段时间回来最可能已经断了，等满一个周期太久
  const sub = appState.addEventListener('change', (s: AppStateStatus) => {
    if (s !== 'active' || stopped) return
    if (timer != null) {
      timers.clear(timer)
      timer = null
    }
    tick()
  })

  return () => {
    if (stopped) return
    stopped = true
    if (timer != null) timers.clear(timer)
    try {
      sub.remove()
    } catch {
      /* ignore */
    }
  }
}
