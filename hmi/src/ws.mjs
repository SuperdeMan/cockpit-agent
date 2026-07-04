// 韧性 WebSocket 客户端：指数退避重连 + 有界发送队列（断线不静默丢消息）。
//
// 解决两个实测痛点：
//  1. 断线时旧逻辑 `readyState !== OPEN` 直接 return → 用户消息石沉大海（"无响应"）。
//     这里改为入有界队列，重连 onopen 后按序 flush，绝不静默吞最新消息。
//  2. 旧逻辑固定 1.5s 重连、无退避 → 服务抖动时反复风暴。改为 1s→30s 指数退避 + 抖动，
//     onopen 成功后归零。
//
// 刻意「不做」JS 侧假死探测强制重连：服务端已对 HMI 连接周期发 WS Ping（15s），浏览器
// 透明回 Pong 维持连接，但 ping 帧不触发 onmessage——长任务（开思考 30s+）期间应用层本就
// 静默。若按「应用层静默」强制重连会误杀健康的长任务连接，与服务端保活设计冲突。真正断连
// 由浏览器 onclose 触发，交给这里的退避重连即可。请求级「永远思考中」的兜底放在 UI 看门狗。
//
// 纯逻辑 + 注入 WebSocket 工厂/定时器，可用 node:test 无 DOM 单测。

// attempt=0,1,2,... → min*2^attempt 封顶 max，叠加 [0, base/2) 抖动，避免重连风暴同步化。
export function nextBackoff(attempt, minMs = 1000, maxMs = 30000, rand = Math.random) {
  const a = Math.max(0, attempt | 0)
  const base = Math.min(maxMs, minMs * Math.pow(2, a))
  return Math.min(maxMs, base + rand() * (base / 2))
}

// appendToken 把会话鉴权 token 拼到 WS URL 查询串（R3.1）。空 token 原样返回；
// 已有 query 用 & 追加。edge-gateway 在 WS upgrade 前校验 ?token=。
export function appendToken(url, token) {
  if (!token) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}token=${encodeURIComponent(token)}`
}

const OPEN = 1

export class ResilientWebSocket {
  constructor(url, opts = {}) {
    this.url = url
    this._onMessage = opts.onMessage || (() => {})
    this._onStatus = opts.onStatus || (() => {})
    this._wsFactory = opts.wsFactory || ((u) => new WebSocket(u))
    this._minBackoff = opts.minBackoffMs ?? 1000
    this._maxBackoff = opts.maxBackoffMs ?? 30000
    this._maxQueue = opts.maxQueue ?? 32
    // 注意：必须用箭头包一层——直接存 `{ set: setTimeout }` 后经 `this._timers.set(...)` 调用会
    // 把 setTimeout 的 this 绑成该对象，浏览器抛 "TypeError: Illegal invocation"（重连时触发）。
    this._timers = opts.timers || { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h) }
    this._rand = opts.rand || Math.random

    this._ws = null
    this._queue = []
    this._attempt = 0
    this._userClosed = false
    this._reconnectTimer = null
  }

  start() {
    this._userClosed = false
    this._connect()
  }

  get isOpen() {
    return !!this._ws && this._ws.readyState === OPEN
  }

  // 发送：连接就绪直接发；否则入有界队列（满则丢最旧、保最新），重连后 flush。
  // 返回 true=已即时发出，false=已入队。
  send(obj) {
    const raw = typeof obj === 'string' ? obj : JSON.stringify(obj)
    if (this.isOpen) {
      this._ws.send(raw)
      return true
    }
    this._queue.push(raw)
    while (this._queue.length > this._maxQueue) this._queue.shift()
    return false
  }

  close() {
    this._userClosed = true
    if (this._reconnectTimer) {
      this._timers.clear(this._reconnectTimer)
      this._reconnectTimer = null
    }
    try { this._ws && this._ws.close() } catch { /* ignore */ }
  }

  _connect() {
    this._onStatus('connecting')
    let ws
    try {
      ws = this._wsFactory(this.url)
    } catch {
      this._scheduleReconnect()
      return
    }
    this._ws = ws
    ws.onopen = () => {
      this._attempt = 0
      this._onStatus('open')
      this._flush()
    }
    ws.onmessage = (ev) => {
      let data
      try { data = JSON.parse(ev.data) } catch { return }
      this._onMessage(data)
    }
    ws.onerror = () => { try { ws.close() } catch { /* ignore */ } }
    ws.onclose = () => {
      this._onStatus('closed')
      if (!this._userClosed) this._scheduleReconnect()
    }
  }

  _scheduleReconnect() {
    if (this._userClosed) return
    const delay = nextBackoff(this._attempt, this._minBackoff, this._maxBackoff, this._rand)
    this._attempt += 1
    this._reconnectTimer = this._timers.set(() => {
      this._reconnectTimer = null
      this._connect()
    }, delay)
  }

  _flush() {
    if (!this.isOpen) return
    const pending = this._queue
    this._queue = []
    for (const raw of pending) {
      try { this._ws.send(raw) } catch { this._queue.push(raw) }
    }
  }
}
