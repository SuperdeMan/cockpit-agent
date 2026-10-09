import { CommandConsole } from './live/CommandConsole'
import { isFixture, fixtureTurns } from '../fixtures'

const EDGE =
  (import.meta.env.VITE_EDGE_GATEWAY_URL as string | undefined) ||
  'http://localhost:8090'
export const EDGE_WS_URL = EDGE.replace(/^http/, 'ws') + '/ws'



export function genTraceId(): string {
  const bytes = new Uint8Array(8)
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join(
    '',
  )
}

// Badcase 重放：复用同一 Edge Gateway WS 通道原话重发（新 trace、独立 replay session，
// 不污染原会话上下文），返回新 trace_id 供对照面板轮询详情。fire-and-forget。
export function replayText(
  text: string,
  session: string,
  hooks?: { onState?: (state: string) => void },
): string {
  const traceId = genTraceId()
  if (isFixture()) {
    hooks?.onState?.('离线示例完成')
    return fixtureTurns[1].trace_id
  }
  const websocket = new WebSocket(EDGE_WS_URL)
  const timeout = setTimeout(() => {
    hooks?.onState?.('超时')
    websocket.close()
  }, 95_000)
  hooks?.onState?.('连接中')
  websocket.onopen = () => {
    hooks?.onState?.('执行中')
    websocket.send(
      JSON.stringify({
        text,
        session_id: session,
        is_confirmation: false,
        meta: { trace_id: traceId },
      }),
    )
  }
  websocket.onmessage = (event) => {
    try {
      const message = JSON.parse(String(event.data))
      if (message.type === 'final') {
        hooks?.onState?.('完成')
        clearTimeout(timeout)
        websocket.close()
      } else if (message.type === 'error') {
        hooks?.onState?.('失败')
        clearTimeout(timeout)
        websocket.close()
      }
    } catch {
      /* 忽略无法解析的事件（过程区等） */
    }
  }
  websocket.onerror = () => {
    hooks?.onState?.('网关连接失败')
    clearTimeout(timeout)
    websocket.close()
  }
  return traceId
}


// Compatibility entry. The live console and replay still use the same gateway.
export function CommandBar({ onTrace }: { onTrace?: (traceId: string) => void }) { return <CommandConsole onTrace={onTrace} /> }
