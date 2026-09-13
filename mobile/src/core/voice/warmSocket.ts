// 音频面 WebSocket 预热池（2026-09-13，性能评审 §4）。
//
// 为什么：ASR 在按下 PTT / 唤醒进 LISTENING 那一刻才建 `wss://…/api/asr/stream`，TTS 在请求发出那一刻才建
// `…/api/tts/stream`。一条新 WSS = TCP + TLS + upgrade，本项目的测试路径（Tailscale 走美西 DERP 中继）上
// 手机侧一次 ≈1.4s、PC 侧 2–3s。ASR 的音频先攒在 `preOpen` 不丢字，但**定稿至少晚一个握手**（说得越短越明显）；
// TTS 与规划那 2s+ 重叠时藏得住，端侧本地快路径（车控 / 电量，0.5s 就有 final）藏不住——首音 = 握手 + 首片。
//
// 做法：每个 URL 最多预热**一条**连接，需要时 `takeWarmSocket` 取走（OPEN 才算数，取走后由调用方接管全部回调），
// 用完再 `warmSocket` 补一条。超龄（WARM_MAX_AGE_MS）的连接不取：中继路径上长时间空闲的连接可能已经半死，
// 与其拿一条可疑的不如像今天一样现连。网关侧代价 = 每客户端两条等着 `start` 帧的空闲连接（aiohttp 心跳 20s）。
//
// **预热不是采集**：ASR 的采集事实（`captureFacts.asrUploading`）只在真发出音频帧时置位，
// 一条没发 `start` 的空闲连接与隐私面无关。
/** 空闲连接的最长年龄：超过就当没预热，避免在中继路径上拿到一条半死的连接 */
export const WARM_MAX_AGE_MS = 4 * 60_000

interface Entry {
  ws: WebSocket
  createdAt: number
  open: boolean
}

const pool = new Map<string, Entry>()

/** 测试注入：假 WebSocket 工厂与时钟 */
let factory: (url: string) => WebSocket = (url) => new WebSocket(url)
let clock: () => number = () => Date.now()
export function setWarmSocketFactoryForTest(f: ((url: string) => WebSocket) | null, now?: () => number): void {
  factory = f ?? ((url) => new WebSocket(url))
  clock = now ?? (() => Date.now())
  for (const [url, e] of pool) {
    try { e.ws.close() } catch { /* ignore */ }
    pool.delete(url)
  }
}

function stale(e: Entry): boolean {
  return clock() - e.createdAt > WARM_MAX_AGE_MS
}

function forget(url: string, e: Entry): void {
  if (pool.get(url) === e) pool.delete(url)
}

/** 预热一条：已有一条活着且没超龄就什么都不做；超龄 / 已死的先丢再开 */
export function warmSocket(url: string): void {
  if (!url) return
  const cur = pool.get(url)
  if (cur) {
    if (!stale(cur) && cur.ws.readyState <= 1 /* CONNECTING | OPEN */) return
    pool.delete(url)
    try { cur.ws.close() } catch { /* ignore */ }
  }
  let ws: WebSocket
  try {
    ws = factory(url)
  } catch {
    return // 没有 WebSocket（jest 环境 / 极旧运行时）：不预热就是今天的行为
  }
  const entry: Entry = { ws, createdAt: clock(), open: false }
  ws.onopen = () => { entry.open = true }
  ws.onerror = () => { forget(url, entry) }
  ws.onclose = () => { forget(url, entry) }
  ws.onmessage = null
  pool.set(url, entry)
}

/** 取走一条 OPEN 的预热连接；没有 / 还在连 / 已死 / 超龄 ⇒ null（调用方现连）。
 *  取走即移出池，四个回调已清空，由调用方接管。 */
export function takeWarmSocket(url: string): WebSocket | null {
  const cur = pool.get(url)
  if (!cur) return null
  // 还在握手：留在池里等它开好（这一次照旧现连），别把它消费掉
  if (!cur.open && cur.ws.readyState === 0 && !stale(cur)) return null
  pool.delete(url)
  if (!cur.open || cur.ws.readyState !== 1 || stale(cur)) {
    try { cur.ws.close() } catch { /* ignore */ }
    return null
  }
  cur.ws.onopen = null
  cur.ws.onerror = null
  cur.ws.onclose = null
  cur.ws.onmessage = null
  return cur.ws
}

/** 换服务器 / 退出：把池里的连接都关掉 */
export function dropWarmSockets(): void {
  for (const [url, e] of pool) {
    pool.delete(url)
    try { e.ws.close() } catch { /* ignore */ }
  }
}

/** 诊断：池里有几条、各自开没开 */
export function warmSocketStats(): { url: string; open: boolean; ageMs: number }[] {
  return [...pool.entries()].map(([url, e]) => ({ url, open: e.open, ageMs: clock() - e.createdAt }))
}
