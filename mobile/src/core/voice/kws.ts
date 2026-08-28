// 唤醒词引擎（实施计划 M4-2 ⛔）：原生 sherpa-onnx KeywordSpotter 的 JS 面。
//
// 与 HMI `kwsEngine.ts` 是**同一位置的两个平台实现**——同一份 zipformer 模型、同一种
// 关键词串格式（`x iǎo zh ōu x iǎo zh ōu @小舟小舟`）、同一组阈值。刻意不换 int8 量化版：
// 换模型等于换掉阈值成立的前提，唤醒率一旦出问题就分不清是「手机麦不同」还是「模型不同」。
// 省下的 8MB 不值这个不确定性（M4 之后单独调优时再说，那时是单变量）。
//
// **唤醒前音频不出设备**（架构 §9.3）：整条检测在原生进程内，命中之前一个字节都不上行。
// 这也是为什么不能用「一直开着 ASR 流、在云端匹配唤醒词」那种省事做法。
import KwsNative, { KWS_NATIVE_AVAILABLE, type KwsStats } from '../../../modules/kws'

/** 与 HMI `kwsEngine.ts::DEFAULT_KEYWORDS` 逐字相同（ǎ=U+01CE ō=U+014D） */
export const DEFAULT_KEYWORDS = 'x iǎo zh ōu x iǎo zh ōu @小舟小舟'
/** 同 HMI `kwsConfig()` 的 keywordsThreshold / keywordsScore */
export const DEFAULT_THRESHOLD = 0.2
export const DEFAULT_SCORE = 2.0

export interface KwsCallbacks {
  onKeyword(keyword: string): void
  onError?(msg: string): void
}

/** 这个 APK 里有没有 KWS 原生面。false ⇒ 唤醒词整条链不出现（坑账 §9.27）。 */
export function kwsNativeAvailable(): boolean {
  return KWS_NATIVE_AVAILABLE
}

export class KwsEngine {
  private sub: { remove(): void } | null = null
  private loaded = false

  get active(): boolean {
    return this.loaded
  }

  async start(cb: KwsCallbacks, keywords = DEFAULT_KEYWORDS): Promise<void> {
    if (this.loaded) return
    const native = KwsNative
    if (!native) throw new Error('KWS 原生模块不在本 APK 里')
    this.sub = native.addListener('onKeyword', (e) => {
      const k = e?.keyword || ''
      if (k) cb.onKeyword(k)
    })
    try {
      await native.load(keywords, DEFAULT_THRESHOLD, DEFAULT_SCORE)
      this.loaded = true
    } catch (e) {
      this.sub?.remove()
      this.sub = null
      throw e
    }
  }

  /** 喂一帧 16k mono s16le。原生侧立即返回（推理在它自己的线程上）。
   *  返回 false = 这帧被丢了（队列满/未载入），调用方不必处理，诊断看 `stats()`。 */
  accept(frame: Int16Array): boolean {
    if (!this.loaded || !KwsNative) return false
    try {
      return KwsNative.acceptFrame(frame)
    } catch {
      return false
    }
  }

  /** 唤醒后必须调：不重置的话同一段音频会在后续帧里持续复现同一个命中 */
  async reset(): Promise<void> {
    if (!this.loaded || !KwsNative) return
    try {
      await KwsNative.reset()
    } catch {
      /* ignore */
    }
  }

  /** 丢帧数在这里看——**丢帧会降低唤醒率，所以它必须是可见的**，不能静默 */
  stats(): KwsStats | null {
    if (!KwsNative) return null
    try {
      return KwsNative.stats()
    } catch {
      return null
    }
  }

  async stop(): Promise<void> {
    this.sub?.remove()
    this.sub = null
    if (!this.loaded) return
    this.loaded = false
    try {
      await KwsNative?.release()
    } catch {
      /* ignore */
    }
  }
}
