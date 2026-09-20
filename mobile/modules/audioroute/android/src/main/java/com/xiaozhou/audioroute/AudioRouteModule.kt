package com.xiaozhou.audioroute

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.util.Log
import androidx.core.content.ContextCompat
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.util.concurrent.atomic.AtomicLong

/**
 * 音频路由事实透传（2026-09-20 D-09 / GPT-6 评审 F02「耳机断开」维）。
 *
 * 为什么要有它：`react-native-audio-api` 0.13.3 的 Android 端**从不**发 `routeChange`——
 * `AudioEvent.ROUTE_CHANGE` 只在枚举里，没有任何 Android 代码调它（iOS 才有 AVAudioSession 的路由变更）。
 * `audioFocus.ts` 那条「OldDeviceUnavailable ⇒ 停播（becoming-noisy：拔了耳机不能把私密内容外放）」
 * 处置在 Android 上因此是一条死监听。系统给的事实是 [AudioManager.ACTION_AUDIO_BECOMING_NOISY]
 * 广播（拔有线耳机 / A2DP 断开时由 AudioService 发），这里只把它翻成事件；要不要停、怎么停仍归
 * `audioFocus.ts`（与来电 / 抢焦点同一个统一出口）。
 *
 * 三条决定：
 *  1. **只透传，不判定**——与 foldstate / kws 同一纪律；
 *  2. 动态注册（不进 manifest）：只有 JS 订阅了才注册（OnStartObserving），App 没起来就没有停播的对象；
 *     API 33+ 用 RECEIVER_NOT_EXPORTED——系统广播照常到，别的 App 伪造的到不了；
 *  3. `stats()` 暴露 registered / count / lastAt：取证先看「监听装上没有」，再看「事件到没到」
 *     （M-B/M-C 那批的老账：声明存在 ≠ 能用）。
 */
private const val TAG = "AudioRouteModule"

class AudioRouteModule : Module() {
  private var receiver: BroadcastReceiver? = null
  private var wantObserving = false
  private val count = AtomicLong(0)
  @Volatile private var lastAt = 0L

  private fun register() {
    if (receiver != null) return
    val ctx = appContext.reactContext ?: return
    val r = object : BroadcastReceiver() {
      override fun onReceive(context: Context?, intent: Intent?) {
        if (intent?.action != AudioManager.ACTION_AUDIO_BECOMING_NOISY) return
        val n = count.incrementAndGet()
        lastAt = System.currentTimeMillis()
        Log.i(TAG, "ACTION_AUDIO_BECOMING_NOISY #$n")
        sendEvent("onBecomingNoisy", mapOf("at" to lastAt, "count" to n))
      }
    }
    ContextCompat.registerReceiver(
      ctx,
      r,
      IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY),
      ContextCompat.RECEIVER_NOT_EXPORTED,
    )
    receiver = r
  }

  private fun unregister() {
    val r = receiver ?: return
    receiver = null
    try {
      appContext.reactContext?.unregisterReceiver(r)
    } catch (e: IllegalArgumentException) {
      // 已经被系统注销（进程重建期）：不是错误
    }
  }

  override fun definition() = ModuleDefinition {
    Name("AudioRoute")
    Events("onBecomingNoisy")

    /** 取证读数：监听装上没有 / 到过几次 / 最近一次墙钟 */
    Function("stats") {
      mapOf("registered" to (receiver != null), "count" to count.get(), "lastAt" to lastAt)
    }

    OnStartObserving {
      wantObserving = true
      register()
    }
    OnStopObserving {
      wantObserving = false
      unregister()
    }
    // reactContext 在冷启动早期可能还是 null（同 foldstate 的账）：记下「想订阅」，进前台再补
    OnActivityEntersForeground {
      if (wantObserving) register()
    }
    OnDestroy { unregister() }
  }
}
