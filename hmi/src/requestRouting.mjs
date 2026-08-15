// 响应归属登记簿（QA 卡 Q3 的 HMI 半边）。
//
// 此前归属靠 `pendingIdsRef` 这个 FIFO 加**一条注释里的假设**：
// 「网关 WS 串行（一轮事件流 drain 到 EOF 才读下一条），故 fifo[0] 恒为当前
// 正在收流的轮」。这个假设在用户抢发时不成立——端侧快路径可以秒回、云侧还在流，
// 两条流交错，FIFO 头就不是正在收流的那轮（I-048/I-053①/I-022）。
//
// 修法不是把 FIFO 猜得更准，是**让帧自己说它属于谁**：每轮 dispatch 生成
// `request_id` 随请求上行，网关把它盖在该轮每一帧上，这里按 id 归属。
//
// 两条纪律：
// 1. **带了 id 却对不上 = 丢帧**，不回落 FIFO。对不上只有一种解释——那轮已经
//    结算过了（超时/打断/错误）；把它挂到当前轮就是「响应错挂」本身。
// 2. **没带 id 才回落 FIFO**。旧网关与主动推送不带 id，这条保证滚动升级不黑屏。
//
// 看门狗同理**每轮一只**：旧实现 `armWatchdog` 开头就 `clearTimeout(单槽)`，
// 第二个请求一来第一个请求的超时保护被清掉——那轮既没有 final 也没人再救它，
// 就是报告里「下一轮长期正在思考，需要刷新标签页」的成因。

export class RequestRegistry {
  constructor() {
    this._byRequest = new Map()   // requestId -> bubbleId
    this._fifo = []               // bubbleId[]，无 request_id 的帧回落用
    this._latest = null           // 最新一轮 bubbleId（只有它驱动 TTS/候选记录）
  }

  /** 登记一轮请求。 */
  open(requestId, bubbleId) {
    if (requestId) this._byRequest.set(requestId, bubbleId)
    this._fifo.push(bubbleId)
    this._latest = bubbleId
    return bubbleId
  }

  /** 这一帧属于哪个气泡？不改变状态。null = 丢弃。 */
  bubbleFor(frame) {
    const rid = frame && frame.request_id
    if (rid) return this._byRequest.get(rid) ?? null
    return this._fifo.length ? this._fifo[0] : null
  }

  /** 终态帧（final/error/cancelled）：归属并注销。null = 丢弃。 */
  settle(frame) {
    const rid = frame && frame.request_id
    if (rid) {
      const bubble = this._byRequest.get(rid)
      if (bubble === undefined) return null
      this._forget(rid, bubble)
      return bubble
    }
    const bubble = this._fifo.shift() ?? null
    if (bubble !== null) this._forgetByBubble(bubble)
    return bubble
  }

  /** 看门狗超时/本地打断：按气泡注销，返回是否确有此轮。 */
  dropBubble(bubbleId) {
    const had = this._fifo.includes(bubbleId)
    this._forgetByBubble(bubbleId)
    return had
  }

  /** 硬终止（连接级 error）：清空全部在飞轮，返回它们的气泡 id。 */
  drainAll() {
    const all = [...this._fifo]
    this._byRequest.clear()
    this._fifo = []
    return all
  }

  isLatest(bubbleId) {
    return bubbleId !== null && bubbleId === this._latest
  }

  /** 无在飞轮时为续流新建一轮（混合意图云段的 speech_delta 先于占位到达）。 */
  adopt(bubbleId) {
    this._fifo.unshift(bubbleId)
    this._latest = bubbleId
    return bubbleId
  }

  get inFlight() {
    return this._fifo.length
  }

  _forget(rid, bubble) {
    this._byRequest.delete(rid)
    const i = this._fifo.indexOf(bubble)
    if (i >= 0) this._fifo.splice(i, 1)
  }

  _forgetByBubble(bubble) {
    for (const [rid, b] of this._byRequest) if (b === bubble) this._byRequest.delete(rid)
    const i = this._fifo.indexOf(bubble)
    if (i >= 0) this._fifo.splice(i, 1)
  }
}
