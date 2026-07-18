const SENTENCE_END = /[。！？!?；;\n…]+/u
const SOFT_BREAKS = ['，', ',', '、', '：', ':']

export class TtsTextBuffer {
  constructor(maxChars = 48) {
    this.maxChars = maxChars
    this.pending = ''
    this.streamed = ''
  }

  push(delta) {
    if (!delta) return []
    this.streamed += delta
    this.pending += delta
    return this.#drain(false)
  }

  finish(finalText = '') {
    const final = finalText.trim()
    let chunks = []

    if (!this.streamed) {
      this.pending += final
    } else if (!final || final === this.streamed || this.streamed.endsWith(final)) {
      // final 已完整包含在流式文本中，只需冲刷尚未成句的尾巴。
    } else if (final.startsWith(this.streamed)) {
      this.pending += final.slice(this.streamed.length)
    } else {
      // 中间反馈与最终总结不是同一段文本：先播完反馈尾巴，再播最终总结。
      chunks = this.#drain(true)
      this.pending = final
    }

    chunks.push(...this.#drain(true))
    return chunks
  }

  reset() {
    this.pending = ''
    this.streamed = ''
  }

  #drain(force) {
    const chunks = []

    while (this.pending) {
      const sentence = this.pending.match(SENTENCE_END)
      if (sentence?.index !== undefined) {
        const end = sentence.index + sentence[0].length
        this.#take(end, chunks)
        continue
      }

      if (this.pending.length >= this.maxChars) {
        let end = this.maxChars
        for (const mark of SOFT_BREAKS) {
          const candidate = this.pending.lastIndexOf(mark, this.maxChars - 1)
          if (candidate >= Math.floor(this.maxChars / 2)) {
            end = Math.max(end === this.maxChars ? 0 : end, candidate + 1)
          }
        }
        this.#take(end || this.maxChars, chunks)
        continue
      }
      break
    }

    if (force && this.pending.trim()) {
      this.#take(this.pending.length, chunks)
    }
    return chunks
  }

  #take(end, chunks) {
    const chunk = this.pending.slice(0, end).trim()
    this.pending = this.pending.slice(end)
    if (chunk) chunks.push(chunk)
  }
}

// ── 流式收尾判定（audio.ts StreamingTtsSession.finish 消费，纯函数可测）──
// 已流式播报的文本 vs 最终文本的关系分类：final 与流式增量只是「化妆品级差异」
// （markdown 剥法不同/标点空白）→ 视为已播完不再重播（旧逻辑整段重发=复读）；
// 截然不同（混合意图「本地回执」+「云端总结」两段话）→ 由调用方链为下一段合成。
export function normSpeech(s) {
  return (s || '').replace(/[^\p{Script=Han}\p{L}\p{N}]+/gu, '')
}

/** 已流式文本是否已覆盖最终文本（true=无需再播；false=最终文本是另一段话）。 */
export function speechCovered(accum, final) {
  const na = normSpeech(accum)
  const nf = normSpeech(final)
  if (!nf) return true
  if (!na) return false
  return na === nf || na.endsWith(nf) || na.includes(nf)
}

export class OrderedPlaybackQueue {
  constructor(prepare, play, dispose = (item) => item?.dispose?.()) {
    this.prepare = prepare
    this.play = play
    this.dispose = dispose
    this.generation = 0
    this.controller = new AbortController()
    this.tail = Promise.resolve()
  }

  enqueue(value) {
    const generation = this.generation
    const signal = this.controller.signal
    const prepared = Promise.resolve().then(() => this.prepare(value, signal))

    const task = this.tail.catch(() => undefined).then(async () => {
      let item
      try {
        item = await prepared
      } catch (error) {
        if (signal.aborted || generation !== this.generation) return
        throw error
      }

      if (signal.aborted || generation !== this.generation) {
        this.dispose(item)
        return
      }
      await this.play(item, signal)
    })

    this.tail = task.catch(() => undefined)
    return task.catch((error) => {
      if (signal.aborted || generation !== this.generation) return
      throw error
    })
  }

  cancel() {
    this.generation += 1
    this.controller.abort()
    this.controller = new AbortController()
    this.tail = Promise.resolve()
  }
}
