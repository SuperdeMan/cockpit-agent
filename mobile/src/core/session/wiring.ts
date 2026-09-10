// 会话装配单例（M1-3/M1-7）：GatewaySession × SessionCore 跨路由共享——
// 对话屏与车况页读同一个 store；导航切页不重置对话（会话=App 启动一次，§2.1 语义）。
// 仅 edgeUrl+token 变化（设置页重配服务器）才断开重建。
import { GatewaySession } from '../api/gateway'
import type { ServerConfig } from '../config/types'
import { appLocationBridge } from '../location/appLocation'
import { currentMeta } from '../settings/store'
import { speechController } from '../voice/speech'
import { attachHistoryPersistence, historyKey, loadHistory, restoreHistory } from './history'
import { SessionCore } from './store'

export interface Wired {
  session: GatewaySession
  core: SessionCore
  cfgKey: string
  /** 会话记录的存储键（打磨批 F）：设置页「清除对话记录」按它清存量 */
  historyKey: string
  /** 停掉记录的节流写入（dispose 时调） */
  detachHistory(): void
}

let wired: Wired | null = null

export function ensureWired(cfg: ServerConfig): Wired {
  const cfgKey = `${cfg.edgeUrl}|${cfg.audioUrl}|${cfg.token}`
  if (wired?.cfgKey === cfgKey) return wired
  wired?.detachHistory()
  wired?.core.dispose()
  wired?.session.close()
  const speech = speechController(cfg.audioUrl)
  speech.setForeground(false) // 根宿主就绪后开闸，连接期间的早到帧不能启动音频。
  speech.stop()
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
    transport: {
      send: (frame, hooks) => session.sendRaw(frame, hooks),
      discardQueued: (requestId) => session.discardQueued(requestId),
      sendIfOpen: (frame) => session.sendIfOpen(frame),
    },
    sessionId: session.sessionId,
    getMeta: currentMeta,
    location: appLocationBridge,
    // 播报端口（M2-3）：audioUrl 跟着服务器配置走，换服务器时同一个控制器改地址即可
    speech,
  })
  session.start()
  // 打磨批 F（裁决 J3）：冷启动先恢复上次的只读记录（键按账号 × 服务器），再挂节流写入。
  // restore 只在记录为空时生效——恢复到达前用户已经发了话就不覆盖；不触发播报、不发 ACK、不重发。
  const key = historyKey(cfg)
  const built = core
  void loadHistory(key).then((snap) => {
    if (wired?.core === built) restoreHistory(built, snap)
  })
  const detachHistory = attachHistoryPersistence(core, key)
  wired = { session, core, cfgKey, historyKey: key, detachHistory }
  return wired
}

export function getWired(): Wired | null {
  return wired
}

export function disposeWired(): void {
  wired?.detachHistory()
  wired?.core.dispose()
  wired?.session.close()
  wired = null
  speechController().setForeground(false)
}
