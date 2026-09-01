package com.xiaozhou.foldstate

import androidx.core.content.ContextCompat
import androidx.core.util.Consumer
import androidx.window.java.layout.WindowInfoTrackerCallbackAdapter
import androidx.window.layout.FoldingFeature
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowLayoutInfo
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

// 折叠姿态事实透传（B3 / 方案 §7.3）。注册监听即回推当前值（WindowManager 的行为），
// 所以 JS 侧不需要「查询一次当前值」的方法——订阅就是查询。
class FoldStateModule : Module() {
  private var adapter: WindowInfoTrackerCallbackAdapter? = null

  private val consumer = Consumer<WindowLayoutInfo> { info ->
    val fold = info.displayFeatures.filterIsInstance<FoldingFeature>().firstOrNull()
    sendEvent(
      "onFoldChange",
      mapOf(
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
      ),
    )
  }

  override fun definition() = ModuleDefinition {
    Name("FoldState")
    Events("onFoldChange")

    OnStartObserving {
      val activity = appContext.currentActivity ?: return@OnStartObserving
      val a = WindowInfoTrackerCallbackAdapter(WindowInfoTracker.getOrCreate(activity))
      adapter = a
      a.addWindowLayoutInfoListener(activity, ContextCompat.getMainExecutor(activity), consumer)
    }
    OnStopObserving {
      adapter?.removeWindowLayoutInfoListener(consumer)
      adapter = null
    }
  }
}
