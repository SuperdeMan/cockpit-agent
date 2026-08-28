// VAD 引擎（实施计划 M4-1 ⛔）：onnxruntime-react-native 跑 **hmi 那一份** silero_vad.onnx，
// 每帧出语音概率 → `@shared/sileroEndpoint.mjs` 判端点 → speech-start/end 两个事件。
//
// **端点判定必须共用 sileroEndpoint.mjs，不许在这里另写一套**。它才是「说完没说完」的
// 判据（滞回 + 起播去抖 + 静音尾），HMI 与 App 分叉的那一刻，同一个人在两个端上会得到
// 两种「什么时候算说完了」——这正是 runtime/ 那批共享判定要消灭的形态。
// 本文件只做「音频 → 概率」这一段，即引擎；换 sherpa VAD / 车机 DSP 换的是本文件。
//
// 三处与 HMI `vadEngine.ts` 的必然不同（都不是判据差异）：
//  1. 帧源不同：HMI 是 AudioWorklet 直出 512 样本 Float32；这里是 micBus 的 1600 样本
//     Int16（≈100ms），**1600 不是 512 的整数倍**（=3×512+64）⇒ 必须留 carry 缓冲重切，
//     不能按帧丢余数（丢余数等于每 100ms 吃掉 4ms 音频，静音尾会系统性偏长）。
//  2. 模型来源不同：RN 没有 URL fetch 这条路，走 expo-asset 拿 localUri。
//  3. 原生缺席即禁用：`onnxruntime-react-native` 不在这个 APK 里时**不许抛到渲染树**
//     （坑账 §9.27），`available` 返回 false、调用方干净降级成「没有免唤醒」。
import { SileroEndpoint } from '@shared/sileroEndpoint.mjs'

/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-require-imports */

/** silero v4（sherpa 打包的这份）的窗口长度，与 hmi/src/vadEngine.ts 逐字相同 */
export const VAD_WINDOW = 512
const STATE_DIMS = [2, 1, 64]

export interface VadCallbacks {
  onSpeechStart(): void
  onSpeechEnd(): void
  onError?(msg: string): void
}

/** 原生在不在场——**必须在 require 之前问**。
 *  `onnxruntime-react-native/lib/binding.ts:14` 在**模块顶层**就调
 *  `NativeModules.Onnxruntime.install()`：原生没注册时那句抛
 *  `Cannot read property 'install' of null`，而它发生在模块求值期、
 *  经 Metro 的 require 抛出来时**外层的 try/catch 兜不住**（2026-08-28 真机实证：
 *  屏上是 LogBox 的「Uncaught Error」，栈顶就是这个 try 里的 require）。
 *  ⇒ 用与 svg/amap3d 同一个判据形态：先问 NativeModules 有没有那个键，再决定要不要引。
 *  同坑账 §9.27——原生缺席不许崩到渲染树上。 */
function ortNativePresent(): boolean {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const rn = require('react-native')
    return !!(rn?.NativeModules?.Onnxruntime || rn?.TurboModuleRegistry?.get?.('Onnxruntime'))
  } catch {
    return false
  }
}

function loadOrt(): any | null {
  if (!ortNativePresent()) return null
  try {
    return require('onnxruntime-react-native')
  } catch {
    return null
  }
}

/** 这个 APK 里有没有 ORT 原生面。false ⇒ 免唤醒整条链必须干净不出现，而不是点了报错。 */
export function vadNativeAvailable(): boolean {
  return !!loadOrt()?.InferenceSession
}

async function modelPath(): Promise<string> {
  const { Asset } = require('expo-asset')
  const asset = Asset.fromModule(require('../../../assets/models/silero_vad.onnx'))
  await asset.downloadAsync()
  const uri: string = asset.localUri || asset.uri
  // ORT 要文件路径不要 URI scheme；dev 下 expo-asset 给的是 file:///data/...
  return uri.startsWith('file://') ? uri.slice('file://'.length) : uri
}

export class VadEngine {
  private session: any = null
  private ort: any = null
  private ep: InstanceType<typeof SileroEndpoint>
  private h: any = null
  private c: any = null
  private carry = new Float32Array(0)
  private running = false
  private chain: Promise<void> = Promise.resolve()
  /** 512 样本窗旁路：控制器订阅它喂 `PcmRing` 前滚缓冲。
   *  **必须是 512 窗而不是进来的 1600 帧**——`pcmRing.mjs` 的 `takeLast(ms)` 按
   *  `FRAME_MS = 512/16000` 折算帧数，喂 1600 的帧会让「取 200ms」实际取到 700ms，
   *  pre-roll 里带上一轮 TTS 的尾巴 ⇒ ASR 把播报当用户说话。 */
  onWindow: ((win: Float32Array) => void) | null = null
  /** 逐窗概率旁路（**只给诊断屏**，不参与任何判定）。留它是因为 spike 取证要的是
   *  「概率分布长什么样」而不是「有没有触发」——只看事件的话，模型压根没跑起来
   *  （恒 0）和「跑了但没人说话」在屏上一模一样。 */
  onProb: ((prob: number) => void) | null = null

  constructor(silenceTailMs = 800) {
    this.ep = new SileroEndpoint({ minSilenceMs: silenceTailMs })
  }

  get active(): boolean {
    return this.running
  }

  /** 当前是否处于语音段（同 HMI：唤醒时用户已开口就跳过提示音） */
  get inSpeech(): boolean {
    return !!(this.ep as any).triggered
  }

  setSilenceTail(ms: number): void {
    ;(this.ep as any).cfg.minSilenceMs = ms
  }

  /** 预载模型（消除首帧 warmup）。原生缺席/模型缺失即抛，调用方据此禁用免唤醒。 */
  async load(): Promise<void> {
    if (this.session) return
    const ort = loadOrt()
    if (!ort?.InferenceSession) throw new Error('onnxruntime-react-native 不在本 APK 里')
    this.ort = ort
    this.session = await ort.InferenceSession.create(await modelPath())
  }

  private zeroState(): any {
    return new this.ort.Tensor('float32', new Float32Array(2 * 1 * 64), STATE_DIMS)
  }

  /** 开始判定。调用方负责把 micBus 的帧喂进 `accept`。 */
  async start(cb: VadCallbacks): Promise<void> {
    if (this.running) return
    await this.load()
    this.ep.reset()
    this.h = this.zeroState()
    this.c = this.zeroState()
    this.carry = new Float32Array(0)
    this.chain = Promise.resolve()
    this.running = true
    this.cb = cb
  }

  private cb: VadCallbacks | null = null

  /** 喂一帧 16k mono s16le（micBus 的形态）。内部重切成 512 样本窗口。 */
  accept(frame: Int16Array): void {
    if (!this.running) return
    // s16 → float，接上上一帧的余数
    const merged = new Float32Array(this.carry.length + frame.length)
    merged.set(this.carry, 0)
    for (let i = 0; i < frame.length; i++) merged[this.carry.length + i] = frame[i] / 32768
    let off = 0
    while (merged.length - off >= VAD_WINDOW) {
      const win = merged.slice(off, off + VAD_WINDOW)
      off += VAD_WINDOW
      this.onWindow?.(win) // 入环在推理入队**之前**，保证前滚缓冲的帧序（同 HMI）
      // 串行化推理：h/c 是跨帧状态，乱序等于把状态机搅坏（同 HMI 的 chain）
      this.chain = this.chain
        .then(() => this.infer(win))
        .catch((err) => this.cb?.onError?.(String(err)))
    }
    this.carry = merged.slice(off)
  }

  private async infer(win: Float32Array): Promise<void> {
    if (!this.running || !this.session) return
    const x = new this.ort.Tensor('float32', win, [1, VAD_WINDOW])
    const out = await this.session.run({ x, h: this.h, c: this.c })
    this.h = out.new_h
    this.c = out.new_c
    const prob = (out.prob.data as Float32Array)[0]
    this.onProb?.(prob)
    const ev = this.ep.accept(prob)
    if (ev === 'start') this.cb?.onSpeechStart()
    else if (ev === 'end') this.cb?.onSpeechEnd()
  }

  stop(): void {
    this.running = false
    this.cb = null
    this.carry = new Float32Array(0)
    this.chain = Promise.resolve()
    this.ep.reset()
  }

  /** 彻底释放（App 卸载）。session 释放后要重新 load 才能再用。 */
  async dispose(): Promise<void> {
    this.stop()
    try {
      await this.session?.release?.()
    } catch {
      /* ignore */
    }
    this.session = null
  }
}
