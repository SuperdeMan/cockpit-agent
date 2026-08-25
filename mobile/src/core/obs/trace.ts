// 观测贯通（实施计划 §2.2）：trace_id 与 HMI genTraceId 同构（hmi/src/App.tsx:61-66）
// ——8 随机字节 → 16 hex，随 meta 上行，可观测台按它直达该轮。
// 会话 id：`app-` + 随机 6 位（主设计文档 §2.1：不在记忆抽取跳过名单，观测面可分端；
// 每次 App 启动新会话，同 HMI 每次刷新语义）。

// 不引 DOM lib：crypto 全局按结构化类型探测（Hermes 有 getRandomValues，randomUUID 视版本）
type MaybeCrypto = {
  getRandomValues?: (b: Uint8Array) => unknown
  randomUUID?: () => string
}

export function genTraceId(): string {
  const bytes = new Uint8Array(8)
  const c = (globalThis as { crypto?: MaybeCrypto }).crypto
  if (c && typeof c.getRandomValues === 'function') c.getRandomValues(bytes)
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

// 与 HMI 的 uid()（App.tsx:54-57）同构：request_id 每轮新生成
export function uid(): string {
  const c = (globalThis as { crypto?: MaybeCrypto }).crypto
  return c && typeof c.randomUUID === 'function'
    ? c.randomUUID()
    : Math.random().toString(36).slice(2)
}

export function newSessionId(): string {
  return 'app-' + Math.random().toString(36).slice(2, 8)
}
