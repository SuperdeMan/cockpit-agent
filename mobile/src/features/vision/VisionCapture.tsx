// 相机只在一次经授权的视觉请求中挂载；旧二进制没有内存采集能力时禁止开相机。
import { CameraView, useCameraPermissions } from 'expo-camera'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AppState, View } from 'react-native'

import { settingsStore } from '@/core/settings/store'
import { base64ToBytes } from '@/core/voice/base64'
import { cancelVisionCapture, registerVisionCapturer, reportVisionCameraActive } from '@/core/vision/frame'
import { checkCaptureSignal, withCaptureSignal } from '@/core/vision/cancellation'
import { memoryCamera } from '@/core/vision/nativeCamera'
import type { InteractionScope } from '@/core/session/interactionScope'

export function VisionCapture({ enabled, scope }: { enabled: boolean; scope?: InteractionScope }): React.ReactElement | null {
  const [armed, setArmed] = useState<number | null>(null)
  const [perm, requestPerm] = useCameraPermissions()
  const camRef = useRef<CameraView | null>(null)
  const readyRef = useRef<{ resolve(): void; reject(error: Error): void } | null>(null)
  const epoch = useRef(0)
  const active = useRef<number | null>(null)
  const allowed = useRef(false)
  const permRef = useRef({ perm, requestPerm })
  useEffect(() => { permRef.current = { perm, requestPerm } })

  const capture = useCallback(async (signal: AbortSignal): Promise<Uint8Array | null> => {
    checkCaptureSignal(signal)
    if (!allowed.current || active.current !== null || !memoryCamera()) return null
    const id = ++epoch.current
    active.current = id
    const revoke = () => {
      if (active.current !== id) return
      readyRef.current = null
      setArmed(null) // 触发原生解绑；物理 CLOSED 由原生事件报告，不在此处猜测。
    }
    signal.addEventListener('abort', revoke, { once: true })
    try {
      const { perm: permission, requestPerm: request } = permRef.current
      if (!permission?.granted) {
        const result = await withCaptureSignal(request(), signal)
        checkCaptureSignal(signal)
        if (!result.granted) return null
      }
      checkCaptureSignal(signal)
      if (!allowed.current) return null
      const ready = new Promise<void>((resolve, reject) => { readyRef.current = { resolve, reject } })
      setArmed(id)
      await withCaptureSignal(ready, signal)
      checkCaptureSignal(signal)
      if (!allowed.current || !camRef.current) return null
      // memoryOnly 在已核验的 Android 原生补丁里先于所有文件路径分流。
      // skipProcessing/base64 不是零落盘保证；该能力缺席时上方已 fail closed。
      const options = { memoryOnly: true, quality: 0.7, shutterSound: false }
      const picture = await withCaptureSignal(camRef.current.takePictureAsync(options), signal)
      checkCaptureSignal(signal)
      if (!allowed.current || !picture?.base64 || picture.uri) return null
      const bytes = base64ToBytes(picture.base64)
      picture.base64 = undefined
      return bytes
    } finally {
      signal.removeEventListener('abort', revoke)
      if (active.current === id) {
        active.current = null
        readyRef.current = null
        setArmed(null)
      }
    }
  }, [])

  useEffect(() => {
    const native = memoryCamera()
    if (!native) return
    const sub = native.addListener('cameraActiveChanged', ({ active: cameraActive }) => reportVisionCameraActive(cameraActive))
    reportVisionCameraActive(native.cameraActive === true)
    return () => sub.remove()
  }, [])

  useEffect(() => {
    let foreground = AppState.currentState === 'active'
    const sync = () => {
      allowed.current = enabled && foreground && (!scope || scope.canCapture()) && settingsStore.getState().settings.visionEnabled && memoryCamera() !== null
      registerVisionCapturer(allowed.current ? capture : null)
      if (!allowed.current) { cancelVisionCapture(); setArmed(null) }
    }
    // Zustand 通知同步撤回能力，不能等 React 下一次 effect 才作废已在等待的权限回调。
    const off = settingsStore.subscribe(sync)
    const offScope = scope?.subscribe(sync)
    const sub = AppState.addEventListener('change', (state) => { foreground = state === 'active'; sync() })
    sync()
    return () => {
      allowed.current = false
      off()
      offScope?.()
      sub.remove()
      registerVisionCapturer(null)
    }
  }, [enabled, capture, scope])

  if (!enabled || armed === null) return null
  return (
    <View pointerEvents="none" style={{ position: 'absolute', width: 1, height: 1, opacity: 0, top: 0, left: 0 }}>
      <CameraView key={armed} ref={camRef} style={{ width: 1, height: 1 }} facing="back"
        onCameraReady={() => { if (active.current === armed) readyRef.current?.resolve() }}
        onMountError={() => { if (active.current === armed) readyRef.current?.reject(new Error('相机启动失败')) }} />
    </View>
  )
}
