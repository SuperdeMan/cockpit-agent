// R4.3 VAD 引擎（P1，D2/D3）：onnxruntime-web 单线程跑 silero VAD（已 P0 验证：无需跨源隔离）。
// AudioWorklet 采 16k 帧 → 主线程 ORT 推理每帧语音概率 → SileroEndpoint 判端点 → speech-start/end 回调。
// 引擎无关的 voiceLoop.mjs FSM 只消费这两个事件；换车机 DSP 即替换本文件，FSM 不动。
import * as ort from 'onnxruntime-web/wasm' // CPU/WASM-only 包（非 jsep，避免 webgpu 依赖）
import { SileroEndpoint } from './sileroEndpoint.mjs'

// 单线程（不依赖 SharedArrayBuffer / COOP-COEP，P0 已验证）。wasm 资源由 Vite 的依赖优化器/构建
// 自动定位（`onnxruntime-web/wasm` 入口经 import.meta.url 引用，dev+build 都由 Vite 处理）——
// 不设 wasmPaths 指向 public/（Vite dev 会给动态 import 加 ?import 而无法把 public 文件当模块处理）。
ort.env.wasm.numThreads = 1

export type VadCallbacks = {
  onSpeechStart: () => void
  onSpeechEnd: () => void
  onError?: (msg: string) => void
}

const MODEL_URL = '/models/silero_vad.onnx'
const WORKLET_URL = '/vad-capture-worklet.js'

function zeroState(): ort.Tensor {
  return new ort.Tensor('float32', new Float32Array(2 * 1 * 64), [2, 1, 64])
}

/** silero VAD 运行时。start() 常开采音+推理，端点事件经回调出；stop() 拆机。 */
export class VadEngine {
  private session: ort.InferenceSession | null = null
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private src: MediaStreamAudioSourceNode | null = null
  private node: AudioWorkletNode | null = null
  private h = zeroState()
  private c = zeroState()
  private ep: InstanceType<typeof SileroEndpoint>
  private running = false
  private chain: Promise<void> = Promise.resolve()

  constructor(silenceTailMs = 800) {
    this.ep = new SileroEndpoint({ minSilenceMs: silenceTailMs })
  }

  /** 预载模型（可提前调用以消除首帧 warmup）。ORT wasm 缺失/加载失败即抛，调用方据此禁用 hands-free。 */
  async load(): Promise<void> {
    if (this.session) return
    this.session = await ort.InferenceSession.create(MODEL_URL)
  }

  get active(): boolean {
    return this.running
  }

  setSilenceTail(ms: number): void {
    this.ep.cfg.minSilenceMs = ms
  }

  async start(cb: VadCallbacks): Promise<void> {
    if (this.running) return
    await this.load()
    this.ctx = new AudioContext({ sampleRate: 16000 })
    await this.ctx.audioWorklet.addModule(WORKLET_URL)
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    this.src = this.ctx.createMediaStreamSource(this.stream)
    this.node = new AudioWorkletNode(this.ctx, 'vad-capture')
    this.node.port.onmessage = (e: MessageEvent) => this.enqueue(e.data as Float32Array, cb)
    this.src.connect(this.node) // 不连 destination：只采集不外放
    this.ep.reset()
    this.h = zeroState()
    this.c = zeroState()
    this.running = true
  }

  // 串行化推理，保证 h/c 状态连续、端点计时不乱（silero ~1-3ms << 32ms 帧间隔，不会积压）
  private enqueue(frame: Float32Array, cb: VadCallbacks): void {
    this.chain = this.chain.then(() => this.infer(frame, cb)).catch((err) => cb.onError?.(String(err)))
  }

  private async infer(frame: Float32Array, cb: VadCallbacks): Promise<void> {
    if (!this.running || !this.session) return
    const x = new ort.Tensor('float32', frame, [1, 512])
    const out = await this.session.run({ x, h: this.h, c: this.c })
    this.h = out.new_h as ort.Tensor
    this.c = out.new_c as ort.Tensor
    const prob = (out.prob.data as Float32Array)[0]
    const ev = this.ep.accept(prob)
    if (ev === 'start') cb.onSpeechStart()
    else if (ev === 'end') cb.onSpeechEnd()
  }

  stop(): void {
    this.running = false
    try {
      this.node?.disconnect()
      this.src?.disconnect()
      this.stream?.getTracks().forEach((t) => t.stop())
      void this.ctx?.close()
    } catch {
      /* ignore */
    }
    this.node = null
    this.src = null
    this.stream = null
    this.ctx = null
    this.chain = Promise.resolve()
    this.ep.reset()
  }
}
