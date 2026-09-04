package com.xiaozhou.foldstate

import androidx.core.content.ContextCompat
import androidx.core.util.Consumer
import androidx.window.java.layout.WindowInfoTrackerCallbackAdapter
import androidx.window.layout.FoldingFeature
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowLayoutInfo
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

// 折叠姿态事实透传（B3 / 方案 §7.3；B5-6 补「查当前值」）。
// ⚠ 「注册监听即回推当前值 ⇒ 订阅就是查询」只对**第一个** JS 订阅者成立：Expo 的 OnStartObserving 只在
//    0→1 个订阅者时触发一次，之后再挂载的 useFoldState 实例什么都收不到，直到铰链下一次物理动作
//    （B4 两轮实证：新屏读 flat/events 0，动一下铰链立刻 book/events 4）。所以这里缓存最近一次投影，
//    JS 侧新实例用 current() 拿初值。
class FoldStateModule : Module() {
  private var adapter: WindowInfoTrackerCallbackAdapter? = null
  @Volatile private var last: Map<String, Any?>? = null
  private var wantObserving = false

  private fun project(info: WindowLayoutInfo): Map<String, Any?> {
    val fold = info.displayFeatures.filterIsInstance<FoldingFeature>().firstOrNull()
    return mapOf(
      "present" to (fold != null),
      "state" to when (fold?.state) {
        FoldingFeature.State.HALF_OPENED -> "halfOpened"
        FoldingFeature.State.FLAT -> "flat"
        else -> "none"
      },
      "orientation" to when (fold?.orientation) {
        FoldingFeature.Orientation.HORIZONTAL -> "horizontal"
        FoldingFeature.Orientation.VERTICAL -> "vertical"
        else -> "none"
      },
      "isSeparating" to (fold?.isSeparating ?: false),
      "bounds" to fold?.bounds?.let {
        mapOf("left" to it.left, "top" to it.top, "right" to it.right, "bottom" to it.bottom)
      },
    )
  }

  private val consumer = Consumer<WindowLayoutInfo> { info ->
    val m = project(info)
    last = m
    sendEvent("onFoldChange", m)
  }

  // currentActivity 为 null（冷启动早期）时原来是静默 return ⇒ 永远没有监听；现在记下「想订阅」，进前台再补
  private fun start() {
    if (adapter != null) return
    val activity = appContext.currentActivity ?: return
    val a = WindowInfoTrackerCallbackAdapter(WindowInfoTracker.getOrCreate(activity))
    adapter = a
    a.addWindowLayoutInfoListener(activity, ContextCompat.getMainExecutor(activity), consumer)
  }

  override fun definition() = ModuleDefinition {
    Name("FoldState")
    Events("onFoldChange")

    /** 最近一次投影；从未收到过事件时 null（JS 侧按 flat 降级，与原生缺席同一条路） */
    Function("current") { last }

    OnStartObserving {
      wantObserving = true
      start()
    }
    OnStopObserving {
      wantObserving = false
      adapter?.removeWindowLayoutInfoListener(consumer)
      adapter = null
    }
    OnActivityEntersForeground {
      if (wantObserving) start()
    }
  }
}
