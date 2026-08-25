// 会话装配单例（M1-3/M1-7）：GatewaySession × SessionCore 跨路由共享——
// 对话屏与车况页读同一个 store；导航切页不重置对话（会话=App 启动一次，§2.1 语义）。
// 仅 edgeUrl+token 变化（设置页重配服务器）才断开重建。
import { GatewaySession } from '../api/gateway'
import type { ServerConfig } from '../config/types'
import { appLocationBridge } from '../location/appLocation'
import { currentMeta } from '../settings/store'
import { SessionCore } from './store'

export interface Wired {
  session: GatewaySession
  core: SessionCore
  cfgKey: string
}

let wired: Wired | null = null

export function ensureWired(cfg: ServerConfig): Wired {
  const cfgKey = `${cfg.edgeUrl}|${cfg.token}`
  if (wired?.cfgKey === cfgKey) return wired
  wired?.core.dispose()
  wired?.session.close()
  let core: SessionCore | null = null
  const session = new GatewaySession(
    { edgeUrl: cfg.edgeUrl, token: cfg.token },
    {
      onFrame: (dir, frame) => {
        if (dir === 'down') core?.handleFrame(frame)
      },
      onStatus: (s) => core?.setStatus(s),
    },
  )
  core = new SessionCore({
    transport: { send: (frame) => session.sendRaw(frame) },
    sessionId: session.sessionId,
    getMeta: currentMeta,
    location: appLocationBridge,
  })
  session.start()
  wired = { session, core, cfgKey }
  return wired
}

export function getWired(): Wired | null {
  return wired
}
