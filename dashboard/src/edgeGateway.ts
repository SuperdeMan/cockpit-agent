// Edge authenticates before the WebSocket upgrade, using the same contract as HMI.
// The collector's operator token is a different credential and is never reused here.
const EDGE = (import.meta.env.VITE_EDGE_GATEWAY_URL as string | undefined) || 'http://localhost:8090'
export const EDGE_WS_URL = EDGE.replace(/\/$/, '').replace(/^http/, 'ws') + '/ws'

export class MissingEdgeTokenError extends Error {
  constructor() {
    super('未配置网关访问令牌，指令尚未发送。请由维护者配置 Dashboard 的网关访问身份。')
    this.name = 'MissingEdgeTokenError'
  }
}

export function createEdgeSocket(): WebSocket {
  const token = ((import.meta.env.VITE_WS_TOKEN as string | undefined) || '').trim()
  if (!token) throw new MissingEdgeTokenError()
  const url = new URL(EDGE_WS_URL)
  url.searchParams.set('token', token)
  return new WebSocket(url.toString())
}

// Browsers do not expose the HTTP upgrade status to application JavaScript.
// A rejected token and a network failure must not be presented as a known cause.
export const EDGE_CONNECTION_FAILURE = '无法建立 Edge Gateway 连接，请检查网络或网关访问令牌。请求尚未发送。'
