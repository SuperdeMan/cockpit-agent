// 连接测试（实施计划 M0-5）：开 WS {edge}/ws?token=，onopen 即成功随后主动关；
// 握手失败给出 close code 与「检查 Tailscale / token」提示。
import { edgeWsUrl } from '../config/endpoints'

export type ConnectionTestResult =
  | { ok: true }
  | { ok: false; code?: number; reason?: string }

const TEST_TIMEOUT_MS = 10000

export function testConnection(
  edgeUrl: string,
  token: string,
  wsFactory: (url: string) => WebSocket = (u) => new WebSocket(u),
): Promise<ConnectionTestResult> {
  return new Promise((resolve) => {
    let settled = false
    const settle = (r: ConnectionTestResult) => {
      if (settled) return
      settled = true
      resolve(r)
    }
    let ws: WebSocket
    try {
      ws = wsFactory(edgeWsUrl(edgeUrl, token))
    } catch (e) {
      settle({ ok: false, reason: String(e) })
      return
    }
    const timer = setTimeout(() => {
      try {
        ws.close()
      } catch {
        /* ignore */
      }
      settle({ ok: false, reason: '超时（10s 无响应）' })
    }, TEST_TIMEOUT_MS)
    ws.onopen = () => {
      clearTimeout(timer)
      settle({ ok: true })
      try {
        ws.close()
      } catch {
        /* ignore */
      }
    }
    ws.onclose = (ev: { code?: number; reason?: string }) => {
      clearTimeout(timer)
      settle({ ok: false, code: ev?.code, reason: ev?.reason })
    }
    ws.onerror = () => {
      /* RN 会跟着触发 onclose，这里不重复 settle */
    }
  })
}

export function describeFailure(r: Extract<ConnectionTestResult, { ok: false }>): string {
  const code = r.code ? `close code ${r.code}` : (r.reason ?? '未知错误')
  return `连接失败（${code}）——检查设备 Tailscale 是否在线、token 是否正确`
}
