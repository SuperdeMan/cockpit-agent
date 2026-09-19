package com.xiaozhou.kws

import android.util.Log
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import expo.modules.kotlin.exception.CodedException
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
 *  5. **解码线程的生命周期标记是它自己的，不是全局 `running`**（2026-09-19 GPT-6 评审 F07）。
 *     此前 loop 以 `running` 为循环条件：join 超时后 loadInternal 接着 `running.set(true)`，
 *     卡在 JNI 里的旧线程醒来读到 true 就继续消费**新**队列、喂新 stream。现在每条 [Worker]
 *     带自己的 `alive`，release 只翻它那一位；`running` 只管 acceptFrame 收不收帧。
 *     join 超时不再「记日志然后照常」：模块记住那条线程（`stale`），它确认退出前 load
 *     **抛 [KwsWorkerStuckException] 拒绝加载**（JS 侧 kws.start 拿到异常 ⇒ 免唤醒开关弹回并说明，
 *     用户稍后再开即可），不再让两条线程并存。⚠ join 的 1s 是告警阈值不是 release 的上限：
 *     旧线程若正握着锁在 JNI 里解码，随后的 release 仍会等它出锁——正确性优先于时延。
 */
private const val TAG = "KwsModule"
private const val ASSET_DIR = "kws"
private const val MODEL_TAG = "epoch-12-avg-2-chunk-16-left-64"
private const val SAMPLE_RATE = 16000
/** 队列上限 ≈ 3 秒音频（30 帧 @100ms）。超过说明推理已经严重落后，继续攒没有意义。 */
private const val MAX_QUEUED_FRAMES = 30
/** release 等解码线程退出的告警阈值。一次解码是毫秒级，1s 够宽；超时记日志 + 进入 stale（头注 5）。 */
private const val JOIN_TIMEOUT_MS = 1000L
/** load 遇到 stale 线程时再给它的最后宽限：它此刻已不在锁里（release 已出锁），正常几毫秒内退出 */
private const val STALE_GRACE_MS = 200L

/** 上一条解码线程没退出就被要求重新加载。code 固定，JS 侧可按它分流。 */
class KwsWorkerStuckException :
  CodedException("KWS_WORKER_STUCK", "唤醒词引擎上一条解码线程未退出，暂不能重新加载；请稍后再开", null)

class KwsModule : Module() {
  private var spotter: KeywordSpotter? = null
  private var stream: OnlineStream? = null
  private var worker: Worker? = null
  /** join 超时仍活着的旧解码线程；它确认退出前拒绝 load（头注 5） */
  private var stale: Worker? = null
  private val queue = LinkedBlockingQueue<FloatArray>()
  /** acceptFrame 收不收帧。**不是**解码线程的循环条件（头注 5） */
  private val running = AtomicBoolean(false)
  private val dropped = AtomicLong(0)
  private val processed = AtomicLong(0)

  /** 解码线程 + 它自己的停止标记。release 只翻这一位，别的线程的 alive 与它无关。 */
  private inner class Worker : Thread("kws-decode") {
    val alive = AtomicBoolean(true)
    override fun run() = loop(alive)
  }

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
    // 头注 5：上一条线程 join 超时后仍活着 ⇒ 不允许开始下一次加载。它此刻已经不在锁里，
    // 再给一小段宽限；还活着就是真卡住了，抛给 JS 说清楚，而不是并存两条线程
    stale?.let { old ->
      if (old.isAlive) {
        try {
          old.join(STALE_GRACE_MS)
        } catch (e: InterruptedException) {
          Thread.currentThread().interrupt()
        }
      }
      if (old.isAlive) throw KwsWorkerStuckException()
      stale = null
    }
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
    val t = Worker()
    t.isDaemon = true
    worker = t
    t.start()
    Log.i(TAG, "KWS loaded keywords=$keywords threshold=$threshold score=$score")
  }

  private fun loop(alive: AtomicBoolean) {
    while (alive.get()) {
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
          // 判的是**自己的** alive（头注 5）：全局 running 已经是下一代的了
          val sp = spotter
          val st = stream
          if (alive.get() && sp != null && st != null) {
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
    t?.alive?.set(false)
    queue.clear()
    // **等解码线程真的退出再往下走**（头注 4）。只 interrupt 不 join 的直接后果不是
    // 野指针（那由锁内重读挡住了），是 loadInternal 的 release→load 序列会造出
    // **两条解码线程**：老线程还在 while 里，两条线程喂同一条 stream。
    // join 之后「上一轮已经彻底结束」才是真的。
    if (t != null && t !== Thread.currentThread()) {
      t.interrupt()
      try {
        t.join(JOIN_TIMEOUT_MS)
      } catch (e: InterruptedException) {
        Thread.currentThread().interrupt()
      }
      // 超时不静默、也不「照常」：解码卡在一次 JNI 调用里。它的 alive 已是 false、醒来就退出，
      // 但在它确认退出前不许开始下一次加载（头注 5，loadInternal 检查 stale）。
      if (t.isAlive) {
        Log.w(TAG, "kws-decode 未在 ${JOIN_TIMEOUT_MS}ms 内退出，进入 stale")
        stale = t
      }
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
