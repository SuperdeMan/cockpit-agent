package com.xiaozhou.platformlocation

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.util.concurrent.atomic.AtomicBoolean

// 系统定位事实透传（2026-09-17）。只透传，不判定——取值策略住在 src/core/location/fixPolicy.ts。
//
// 两个出口：
//   lastKnown()          —— 各 provider 的 last-known 位置，带**单调时钟**年龄（ageMs），不信墙钟；
//   requestFix(timeoutMs) —— 向 network + gps 各要一次新定位，先到先用，到点全部取消并 resolve null。
// 权限：只读系统已经授予的（expo-location 的授权流程照旧），没授权就返回空 / null，绝不弹申请。
class PlatformLocationModule : Module() {
  private val context: Context?
    get() = appContext.reactContext

  private fun fine(ctx: Context): Boolean =
    ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

  private fun coarse(ctx: Context): Boolean =
    ctx.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

  private fun project(loc: Location, provider: String): Map<String, Any?> = mapOf(
    "provider" to provider,
    "latitude" to loc.latitude,
    "longitude" to loc.longitude,
    "accuracy" to (if (loc.hasAccuracy()) loc.accuracy.toDouble() else null),
    "time" to loc.time,
    "ageMs" to ((SystemClock.elapsedRealtimeNanos() - loc.elapsedRealtimeNanos) / 1_000_000.0),
  )

  private fun lastKnown(): List<Map<String, Any?>> {
    val ctx = context ?: return emptyList()
    if (!fine(ctx) && !coarse(ctx)) return emptyList()
    val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager ?: return emptyList()
    val providers = listOf(
      LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER, "fused", LocationManager.PASSIVE_PROVIDER,
    )
    val out = ArrayList<Map<String, Any?>>()
    val known = runCatching { lm.allProviders }.getOrDefault(emptyList())
    for (p in providers) {
      if (!known.contains(p)) continue
      try {
        val loc = lm.getLastKnownLocation(p) ?: continue
        out.add(project(loc, p))
      } catch (_: SecurityException) {
      } catch (_: IllegalArgumentException) {
      }
    }
    return out
  }

  private fun requestFix(timeoutMs: Double, promise: Promise) {
    val ctx = context
    if (ctx == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
      promise.resolve(null)
      return
    }
    val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
    if (lm == null) {
      promise.resolve(null)
      return
    }
    val wanted = ArrayList<String>()
    if (fine(ctx) || coarse(ctx)) wanted.add(LocationManager.NETWORK_PROVIDER)
    if (fine(ctx)) wanted.add(LocationManager.GPS_PROVIDER)
    val active = wanted.filter { p -> runCatching { lm.isProviderEnabled(p) }.getOrDefault(false) }
    if (active.isEmpty()) {
      promise.resolve(null)
      return
    }
    val done = AtomicBoolean(false)
    val signals = active.associateWith { CancellationSignal() }
    val handler = Handler(Looper.getMainLooper())
    var pending = active.size
    fun finish(value: Map<String, Any?>?) {
      if (!done.compareAndSet(false, true)) return
      handler.removeCallbacksAndMessages(null)
      for (s in signals.values) runCatching { s.cancel() }
      promise.resolve(value)
    }
    handler.post {
      for (p in active) {
        try {
          lm.getCurrentLocation(p, signals[p], ctx.mainExecutor) { loc: Location? ->
            if (loc != null) finish(project(loc, p))
            else if (--pending <= 0) finish(null)
          }
        } catch (_: SecurityException) {
          if (--pending <= 0) finish(null)
        } catch (_: IllegalArgumentException) {
          if (--pending <= 0) finish(null)
        }
      }
    }
    handler.postDelayed({ finish(null) }, timeoutMs.toLong().coerceIn(500L, 60_000L))
  }

  override fun definition() = ModuleDefinition {
    Name("PlatformLocation")
    Function("lastKnown") { lastKnown() }
    AsyncFunction("requestFix") { timeoutMs: Double, promise: Promise -> requestFix(timeoutMs, promise) }
  }
}
