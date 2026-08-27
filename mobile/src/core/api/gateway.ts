// 主链会话客户端最小版（实施计划 M0-6）：@shared/ws.mjs 的韧性 WS（指数退避重连 +
// 有界队列，断线不丢消息）+ §2.2 用户请求帧。
// 上行帧逐字段对照 hmi/src/App.tsx:691-714；meta 键值全 string（网关是
// map[string]string，塞非 string 整帧静默丢弃——坑账 #4）；`__` 前缀键不得上行。
import { ResilientWebSocket } from '@shared/ws.mjs'
import { edgeWsUrl } from '../config/endpoints'
import { genTraceId, newSessionId, uid } from '../obs/trace'
import { httpProbe, startLiveness, type LivenessOpts } from './liveness'

export type GatewayStatus = 'connecting' | 'open' | 'closed'

export interface UserFrameOpts {
  isConfirmation?: boolean
  /** Q1-B：确认/取消指向哪一条挂起；空=普通请求（不发键） */
  operationId?: string
  metaExtra?: Record<string, string>
  /** 会话级偏好 meta（settings buildMeta + 位置键；M1 起接管缺省两键） */
  metaBase?: Record<string, string>
}

export interface UserFrame {
  text: string
  session_id: string
  request_id: string
  is_confirmation: boolean
  operation_id?: string
  meta: Record<string, string>
}

// App 侧固定 meta（§2.2）：occupant 恒 primary/空（声纹是座舱端能力，App 不做注册）。
// metaExtra 过滤与 HMI stripInternalMeta（App.tsx:44-47）同构：`__` 前缀键与空值不上行。
export function buildUserFrame(
  text: string,
  sessionId: string,
  opts: UserFrameOpts = {},
): UserFrame {
  const extra = opts.metaExtra
    ? Object.fromEntries(
        Object.entries(opts.metaExtra).filter(([k, v]) => !k.startsWith('__') && v !== ''),
      )
    : {}
  return {
    text,
    session_id: sessionId,
    request_id: uid(),
    is_confirmation: opts.isConfirmation ?? false,
    ...(opts.operationId ? { operation_id: opts.operationId } : {}),
    meta: {
      ...(opts.metaBase ?? { assistant_name: '小舟', memory_enabled: 'true' }),
      ...extra,
      occupant_id: 'primary',
      occupant_name: '',
      trace_id: genTraceId(),
    },
  }
}

export interface GatewayHandlers {
  /** 上/下行帧回调（M0 调试屏原样落屏；M1 起接会话状态机） */
  onFrame?: (dir: 'up' | 'down', frame: unknown) => void
  onStatus?: (status: GatewayStatus) => void
}

interface SessionOpts {
  sessionId?: string
  /** 测试注入；缺省用 RN 全局 WebSocket */
  wsFactory?: (url: string) => WebSocket
  /** 探活注入（测试用）。传 `false` 关掉探活（单测里不想起真定时器时用） */
  liveness?: Partial<Pick<LivenessOpts, 'probe' | 'intervalMs' | 'failThreshold' | 'timers' | 'appState'>> | false
}

export class GatewaySession {
  readonly sessionId: string
  private readonly ws: ResilientWebSocket
  private readonly onFrame?: GatewayHandlers['onFrame']
  private stopLiveness: (() => void) | null = null
  private readonly livenessOpts: SessionOpts['liveness']
  private readonly edgeUrl: string

  constructor(
    cfg: { edgeUrl: string; token: string },
    handlers: GatewayHandlers = {},
    opts: SessionOpts = {},
  ) {
    this.sessionId = opts.sessionId ?? newSessionId()
    this.onFrame = handlers.onFrame
    this.edgeUrl = cfg.edgeUrl
    this.livenessOpts = opts.liveness
    this.ws = new ResilientWebSocket(edgeWsUrl(cfg.edgeUrl, cfg.token), {
      onMessage: (frame: unknown) => this.onFrame?.('down', frame),
      onStatus: (s: GatewayStatus) => handlers.onStatus?.(s),
      ...(opts.wsFactory ? { wsFactory: opts.wsFactory } : {}),
    })
  }

  start(): void {
    this.ws.start()
    // 探活（见 liveness.ts 头注）：RN 上 onclose 可能永远不来，那时 send() 会把帧
    // 写进死 socket、队列语义失效。探活失败＝确知断网 ⇒ 走 reconnectNow 让状态与队列回正。
    if (this.livenessOpts === false) return
    const o = this.livenessOpts ?? {}
    this.stopLiveness = startLiveness({
      probe: o.probe ?? httpProbe(this.edgeUrl),
      isOpen: () => this.isOpen,
      onDead: () => this.ws.reconnectNow(),
      ...(o.intervalMs !== undefined ? { intervalMs: o.intervalMs } : {}),
      ...(o.failThreshold !== undefined ? { failThreshold: o.failThreshold } : {}),
      ...(o.timers ? { timers: o.timers } : {}),
      ...(o.appState ? { appState: o.appState } : {}),
    })
  }

  close(): void {
    this.stopLiveness?.()
    this.stopLiveness = null
    this.ws.close()
  }

  get isOpen(): boolean {
    return Boolean(this.ws.isOpen)
  }

  /** 返回 true=已即时发出，false=断线已入有界队列（重连后自动 flush） */
  sendText(text: string, opts: UserFrameOpts = {}): boolean {
    const frame = buildUserFrame(text, this.sessionId, opts)
    this.onFrame?.('up', frame)
    return Boolean(this.ws.send(frame))
  }

  /** 打断（§2.2）：{type:'cancel', session_id} */
  cancel(): boolean {
    const frame = { type: 'cancel', session_id: this.sessionId }
    this.onFrame?.('up', frame)
    return Boolean(this.ws.send(frame))
  }

  /** 通用上行（M1 会话状态机自建帧：用户请求/cancel/proactive_ack 都走这里） */
  sendRaw(frame: object): boolean {
    this.onFrame?.('up', frame)
    return Boolean(this.ws.send(frame))
  }
}
