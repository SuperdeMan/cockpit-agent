package com.xiaozhou.kws

import android.util.Log
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.typedarray.Int16Array
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/**
 * 唤醒词检测（M4-2）——sherpa-onnx KeywordSpotter 的极窄桥。
 *
 * **它刻意不含任何策略**：唤醒词串、阈值、命中之后做什么，全在 JS
 * （handsFree.ts / voiceLoop.mjs）。这里只负责「喂音频 → 报命中」，
 * 换引擎（Porcupine / 车机 DSP）时被替换的就是本文件，FSM 一字不改。
 * 与 HMI 侧 `kwsEngine.ts` 是同一位置的两个平台实现，模型与关键词格式逐字相同。
 *
 * 三条不显然的决定：
 *  1. **推理不在 JS 线程**。zipformer 每帧解码是毫秒级但不是零，10fps 同步跑会把
 *     JS 线程（也就是整个 UI）钉住。`acceptFrame` 只做「拷贝 + 入队」立即返回，
 *     命中经事件回 JS。
 *  2. **队列有界，丢帧要报数**。推理慢于实时时队列会无限涨；宁可丢帧也不能涨内存。
 *     但丢帧会降低唤醒率 ⇒ `stats()` 把 dropped 暴露出去，**不静默**。
 *  3. **样本必须在 acceptFrame 内拷完**。Int16Array 背后是 JS 堆上的 ArrayBuffer，
 *     函数返回后不保证有效——异步线程再去读就是读野内存。
 *  4. **解码线程与 release 之间的两条纪律**（2026-08-28 补，M4 首轮遗留的竞态）：
 *     解码线程**在锁内重读 spotter/stream**（锁外抓到的引用可能已经 release 掉），
 *     且 release **join 掉解码线程再返回**（不 join 的话 release→load 会并存两条
 *     解码线程喂同一条 stream）。两条各治一个问题，缺一条都不够。
 */
private const val TAG = "KwsModule"
private const val ASSET_DIR = "kws"
private const val MODEL_TAG = "epoch-12-avg-2-chunk-16-left-64"
private const val SAMPLE_RATE = 16000
/** 队列上限 ≈ 3 秒音频（30 帧 @100ms）。超过说明推理已经严重落后，继续攒没有意义。 */
private const val MAX_QUEUED_FRAMES = 30
/** release 等解码线程退出的上限。一次解码是毫秒级，1s 够宽；超时只记日志不阻塞调用方。 */
private const val JOIN_TIMEOUT_MS = 1000L

class KwsModule : Module() {
  private var spotter: KeywordSpotter? = null
  private var stream: OnlineStream? = null
  private var worker: Thread? = null
  private val queue = LinkedBlockingQueue<FloatArray>()
  private val running = AtomicBoolean(false)
  private val dropped = AtomicLong(0)
  private val processed = AtomicLong(0)

  override fun definition() = ModuleDefinition {
    Name("Kws")

    Events("onKeyword")

    /**
     * 载入模型并起检测线程。
     * @param keywords sherpa 关键词串（`x iǎo zh ōu x iǎo zh ōu @小舟小舟`），与 HMI
     *                 `kwsEngine.ts::DEFAULT_KEYWORDS` 同格式同来源（types.ts 预设表）。
     */
    AsyncFunction("load") { keywords: String, threshold: Double, score: Double ->
      loadInternal(keywords, threshold.toFloat(), score.toFloat())
    }

    AsyncFunction("release") { releaseInternal() }

    /** 重置解码状态（唤醒命中后必须调，否则同一段音频会连续命中） */
    AsyncFunction("reset") {
      // 同 loop()：字段在锁内重读，锁外抓到的引用可能已被 release 掉（头注 4）
      synchronized(this@KwsModule) {
        val s = stream
        val sp = spotter
        if (s != null && sp != null) sp.reset(s)
      }
    }

    /** 喂一帧 16k mono s16le。立即返回；命中走 onKeyword 事件。 */
    Function("acceptFrame") { pcm: Int16Array ->
      if (!running.get()) return@Function false
      if (queue.size >= MAX_QUEUED_FRAMES) {
        dropped.incrementAndGet()
        return@Function false
      }
      // 拷贝必须在这里做完（见头注 3）；顺带 s16 → float [-1,1]，sherpa 只吃后者
      val n = pcm.length
      val f = FloatArray(n)
      for (i in 0 until n) f[i] = pcm[i] / 32768.0f
      queue.offer(f)
      true
    }

    Function("isLoaded") { spotter != null }

    Function("stats") {
      mapOf(
        "loaded" to (spotter != null),
        "queued" to queue.size,
        "dropped" to dropped.get(),
        "processed" to processed.get(),
      )
    }

    OnDestroy { releaseInternal() }
  }

  private fun assetPath(name: String) = "$ASSET_DIR/$name"

  private fun loadInternal(keywords: String, threshold: Float, score: Float) {
    releaseInternal()
    val ctx = appContext.reactContext ?: throw Exceptions.ReactContextLost()
    val config = KeywordSpotterConfig(
      featConfig = FeatureConfig(sampleRate = SAMPLE_RATE, featureDim = 80),
      modelConfig = OnlineModelConfig(
        transducer = OnlineTransducerModelConfig(
          encoder = assetPath("encoder-$MODEL_TAG.onnx"),
          decoder = assetPath("decoder-$MODEL_TAG.onnx"),
          joiner = assetPath("joiner-$MODEL_TAG.onnx"),
        ),
        tokens = assetPath("tokens.txt"),
        numThreads = 1,
        modelType = "zipformer2",
        modelingUnit = "cjkchar",
        provider = "cpu",
      ),
      // 关键词文件仍要给（sherpa 要求配置项非空），但真正生效的是 createStream 的运行时串
      keywordsFile = assetPath("keywords.txt"),
      keywordsThreshold = threshold,
      keywordsScore = score,
      maxActivePaths = 4,
      numTrailingBlanks = 1,
    )
    val sp = KeywordSpotter(assetManager = ctx.assets, config = config)
    spotter = sp
    stream = sp.createStream(keywords)
    queue.clear()
    dropped.set(0)
    processed.set(0)
    running.set(true)
    val t = Thread({ loop() }, "kws-decode")
    t.isDaemon = true
    worker = t
    t.start()
    Log.i(TAG, "KWS loaded keywords=$keywords threshold=$threshold score=$score")
  }

  private fun loop() {
    while (running.get()) {
      val frame = try {
        queue.poll(200, java.util.concurrent.TimeUnit.MILLISECONDS)
      } catch (e: InterruptedException) {
        null
      } ?: continue
      var decoded = false
      try {
        synchronized(this) {
          // **字段必须在锁内重读**（头注 4）。在锁外抓 spotter/stream 的引用，
          // 与 releaseInternal 之间就有一个窗口：引用拿到手之后、进锁之前 release
          // 跑完，锁一放开我们就拿着已经 release 掉的原生指针去 acceptWaveform。
          val sp = spotter
          val st = stream
          if (running.get() && sp != null && st != null) {
            st.acceptWaveform(frame, SAMPLE_RATE)
            while (sp.isReady(st)) sp.decode(st)
            val r = sp.getResult(st)
            if (r.keyword.isNotEmpty()) {
              // 命中即重置：不重置的话同一段音频会在后续帧里持续复现同一个 keyword
              sp.reset(st)
              sendEvent("onKeyword", mapOf("keyword" to r.keyword))
            }
            decoded = true
          }
        }
        if (decoded) processed.incrementAndGet()
      } catch (e: Throwable) {
        Log.w(TAG, "decode failed: ${e.message}")
      }
    }
  }

  private fun releaseInternal() {
    running.set(false)
    val t = worker
    worker = null
    queue.clear()
    // **等解码线程真的退出再往下走**（头注 4）。只 interrupt 不 join 的直接后果不是
    // 野指针（那由锁内重读挡住了），是 loadInternal 的 release→load 序列会造出
    // **两条解码线程**：老线程还在 while 里，新的 running=true 一置它就继续跑，
    // 两条线程喂同一条 stream。join 之后「上一轮已经彻底结束」才是真的。
    if (t != null && t !== Thread.currentThread()) {
      t.interrupt()
      try {
        t.join(JOIN_TIMEOUT_MS)
      } catch (e: InterruptedException) {
        Thread.currentThread().interrupt()
      }
      // 超时不静默：走到这里说明解码卡在一次 JNI 调用里，下一轮 load 会与它并存。
      // 仍然继续 release——锁内重读保证它读到的是 null，不会用到已释放的指针。
      if (t.isAlive) Log.w(TAG, "kws-decode 未在 ${JOIN_TIMEOUT_MS}ms 内退出")
    }
    synchronized(this) {
      // 顺序有讲究：stream 持有 spotter 内部的解码状态，先放 stream 再放 spotter
      stream?.release()
      stream = null
      spotter?.release()
      spotter = null
    }
  }
}
