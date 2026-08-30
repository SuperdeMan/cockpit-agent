// mobile/src/core/voice/tapTalk.ts
// 轻点即说（方案 §5.1.1、Q2）：轻点光球开始录音，**说完自动收尾**——与免唤醒开关无关。
//
// 方案写的收尾是「ASR 网关的 vad_silence_ms 服务端静音尾」。写计划时核了消费方：
// `llm-gateway/providers.py:762`「仅 qwen3 realtime 的 server_vad 消费；fun-asr 走客户端 stop 端点」，
// 而 App 默认 `asrModel='fun-asr-realtime'`（settings/store.ts:81）⇒ 缺省引擎上服务端尾**收不了尾**。
// 所以主判据是**端侧 VAD**（VadEngine：M4 已在 APK 里、真机已验）：语音结束事件 → asr.stop()；
// vad_silence_ms 照传（qwen3 用户白得一层）；VAD 缺席（原生不在 / 载入失败）→ TAP_MAX_MS 硬上限。
// 三层都是「收尾」，谁先到谁收；**没有任何一层是「不收尾」**——轻点录音不能变成永远开着的麦。
//
// 麦：ASR 与 VAD 各领一路 micLease（一路采集多路消费，micBus 头注）。免唤醒开着时不走这里
// （那时轻点 = HandsFreeController.wakeManually，FSM 自带 VAD 收尾），所以 VAD 引擎不会有两份。
import { micLease } from './micBus'
import type { Recorder } from './recorder'
import { AsrSession, type AsrCallbacks, type AsrConfig } from './asr'
import { VadEngine, vadNativeAvailable } from './vad'

/** 端侧 VAD 静音尾（ms）。与 voiceLoop DEFAULTS.silenceTailMs 同值——那是共享判据，
 *  这里只是把同一个数交给 VadEngine 与 ASR 网关，不另立判据 */
export const TAP_SILENCE_MS = 800
/** 无端点时的硬上限（ms；HMI listenSeconds 同值） */
export const TAP_MAX_MS = 15_000

/** 端点源：生产用 VAD，测试注入假的 */
export interface TapEndpoint {
  start(onSpeechEnd: () => void): Promise<void>
  stop(): Promise<void>
}

/** 生产端点：VadEngine + 自己的一路 micLease。原生缺席返回 null（→ 只剩硬上限 + 服务端尾） */
export function vadEndpoint(): TapEndpoint | null {
  if (!vadNativeAvailable()) return null
  const vad = new VadEngine(TAP_SILENCE_MS)
  const lease = micLease()
  return {
    async start(onSpeechEnd) {
      await vad.load()
      await vad.start({ onSpeechStart: () => {}, onSpeechEnd, onError: () => {} })
      await lease.start((f) => vad.accept(f))
    },
    async stop() {
      await lease.stop()
      vad.stop()
      await vad.dispose()
    },
  }
}

export interface TapTalkDeps {
  endpoint: TapEndpoint | null
  /** ASR 的 recorder（缺省 micLease()；测试注入假的） */
  rec?: Recorder
}

export class TapTalkSession {
  private readonly asr: AsrSession
  private cap: ReturnType<typeof setTimeout> | null = null
  private ended = false

  constructor(
    cfg: AsrConfig,
    cb: AsrCallbacks,
    private readonly deps: TapTalkDeps,
  ) {
    this.asr = new AsrSession({ ...cfg, vadSilenceMs: cfg.vadSilenceMs ?? TAP_SILENCE_MS }, cb, deps.rec ?? micLease())
  }

  get active(): boolean {
    return this.asr.active
  }

  async start(): Promise<void> {
    await this.asr.start()
    if (this.deps.endpoint) {
      try {
        await this.deps.endpoint.start(() => void this.stop())
      } catch {
        // 端点起不来（模型载入失败等）：不阻塞录音，硬上限兜底
      }
    }
    this.cap = setTimeout(() => void this.stop(), TAP_MAX_MS)
  }

  /** 收尾（端点到 / 上限到 / 用户再点一下）：幂等 */
  async stop(): Promise<void> {
    if (this.ended) return
    this.ended = true
    this.clearCap()
    await this.deps.endpoint?.stop()
    await this.asr.stop()
  }

  /** 取消：不定稿、不回调 */
  async cancel(): Promise<void> {
    if (this.ended) return
    this.ended = true
    this.clearCap()
    await this.deps.endpoint?.stop()
    await this.asr.cancel()
  }

  private clearCap(): void {
    if (this.cap !== null) {
      clearTimeout(this.cap)
      this.cap = null
    }
  }
}
