// R4.3 唤醒词引擎（P2）：自建 sherpa-onnx KWS WASM 在浏览器本地跑「小舟小舟」检测（运行时 pinyin，免训练）。
// 命中即回调 → handsFreeController 调 FSM wake()。唤醒前音频不出浏览器（架构 §9.3）。
// sherpa KWS 是 pthread 构建，需 crossOriginIsolated（主 app 已经 COOP + COEP:credentialless，见 vite.config）。
// WASM 运行时在 /kws/（gitignore，scripts/build-kws-wasm.sh 生成）。
//
// 关键词 pinyin token 对模型 tokens.txt：小=x iǎo，舟=zh ōu（同 sherpa 默认「小爱同学=x iǎo ài t óng x ué」格式）。
// 关键词是 createKws 的运行时 config（非烤进模型），换词只需换本串——预设见 types.ts::WAKE_WORD_PRESETS。
export const DEFAULT_KEYWORDS = 'x iǎo zh ōu x iǎo zh ōu @小舟小舟' // ǎ=U+01CE ō=U+014D
const KWS_DIR = '/kws/'

// sherpa emscripten Module 全局单例：一页只 init 一次（脚本注入 + window.Module），复用。
let modulePromise: Promise<any> | null = null
function loadKwsModule(): Promise<any> {
  if (modulePromise) return modulePromise
  modulePromise = new Promise((resolve, reject) => {
    const w = window as any
    const injectMain = () => {
      w.Module = {
        locateFile: (p: string) => KWS_DIR + p,
        onRuntimeInitialized: () => resolve(w.Module),
        setStatus: () => {},
      }
      const em = document.createElement('script')
      em.src = KWS_DIR + 'sherpa-onnx-wasm-kws-main.js'
      em.onerror = () => reject(new Error('KWS wasm 主脚本加载失败'))
      document.body.appendChild(em)
    }
    if (typeof w.createKws === 'function') { injectMain(); return }
    const wrap = document.createElement('script')
    wrap.src = KWS_DIR + 'sherpa-onnx-kws.js' // 定义全局 createKws
    wrap.onload = injectMain
    wrap.onerror = () => reject(new Error('KWS 封装脚本加载失败（先跑 scripts/build-kws-wasm.sh）'))
    document.body.appendChild(wrap)
  })
  return modulePromise
}

function kwsConfig(keywords: string): any {
  return {
    featConfig: { samplingRate: 16000, featureDim: 80 },
    modelConfig: {
      transducer: {
        encoder: './encoder-epoch-12-avg-2-chunk-16-left-64.onnx',
        decoder: './decoder-epoch-12-avg-2-chunk-16-left-64.onnx',
        joiner: './joiner-epoch-12-avg-2-chunk-16-left-64.onnx',
      },
      tokens: './tokens.txt', provider: 'cpu', modelType: '', numThreads: 1, debug: 0,
      modelingUnit: 'cjkchar', bpeVocab: '',
    },
    maxActivePaths: 4, numTrailingBlanks: 1, keywordsScore: 2.0, keywordsThreshold: 0.2,
    keywords,
  }
}

function downsample(buf: Float32Array, inRate: number): Float32Array {
  if (inRate === 16000) return buf
  const r = inRate / 16000, n = Math.round(buf.length / r), out = new Float32Array(n)
  let io = 0, ib = 0
  while (io < n) {
    const nx = Math.round((io + 1) * r); let a = 0, c = 0
    for (let i = ib; i < nx && i < buf.length; i++) { a += buf[i]; c++ }
    out[io++] = a / (c || 1); ib = nx
  }
  return out
}

/** sherpa-onnx KWS 唤醒词引擎。start() 常开听「小舟小舟」，命中 onWake；stop() 拆机。 */
export class KwsEngine {
  private kws: any = null
  private stream: any = null
  private ctx: AudioContext | null = null
  private mic: MediaStreamAudioSourceNode | null = null
  private recorder: ScriptProcessorNode | null = null
  private mediaStream: MediaStream | null = null
  private running = false
  private ownsStream = true // false=用控制器传入的共享流（架构债 A），stop 时不停其 tracks
  private keywords: string

  constructor(keywords: string = DEFAULT_KEYWORDS) {
    this.keywords = keywords
  }

  /** 预载 WASM + createKws。失败即抛（调用方据此仅用 VAD 点击开启）。 */
  async load(): Promise<void> {
    const Module = await loadKwsModule()
    if (!this.kws) this.kws = (window as any).createKws(Module, kwsConfig(this.keywords))
  }

  get active(): boolean { return this.running }

  /** 换唤醒词：模型 WASM 单例复用，仅让下次 load() 用新关键词重建 kws 实例。运行中需 stop()+start() 才生效。 */
  setKeywords(kw: string): void {
    if (kw === this.keywords) return
    this.keywords = kw
    try { this.kws?.free?.() } catch { /* ignore */ }
    this.kws = null
  }

  async start(onWake: (keyword: string) => void, externalStream?: MediaStream): Promise<void> {
    if (this.running) return
    await this.load()
    this.stream = this.kws.createStream()
    this.ctx = new AudioContext({ sampleRate: 16000 })
    // 架构债 A：优先用控制器传入的共享 mic 流；无则自取
    this.ownsStream = !externalStream
    this.mediaStream = externalStream ?? await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    this.mic = this.ctx.createMediaStreamSource(this.mediaStream)
    // sherpa demo 同款 ScriptProcessor（已弃用但稳；KWS 内部缓冲，喂变长帧即可）
    this.recorder = this.ctx.createScriptProcessor(4096, 1, 1)
    this.recorder.onaudioprocess = (e) => {
      if (!this.running || !this.stream) return
      const samples = downsample(new Float32Array(e.inputBuffer.getChannelData(0)), this.ctx!.sampleRate)
      this.stream.acceptWaveform(16000, samples)
      while (this.kws.isReady(this.stream)) this.kws.decode(this.stream)
      const r = this.kws.getResult(this.stream)
      if (r && r.keyword && r.keyword.length > 0) {
        this.kws.reset(this.stream)
        onWake(r.keyword)
      }
    }
    this.mic.connect(this.recorder)
    this.recorder.connect(this.ctx.destination)
    this.running = true
  }

  stop(): void {
    this.running = false
    try {
      this.recorder?.disconnect()
      this.mic?.disconnect()
      if (this.ownsStream) this.mediaStream?.getTracks().forEach((t) => t.stop())
      void this.ctx?.close()
    } catch { /* ignore */ }
    try { this.stream?.free?.() } catch { /* ignore */ }
    this.recorder = null; this.mic = null; this.mediaStream = null; this.ctx = null; this.stream = null
  }
}
