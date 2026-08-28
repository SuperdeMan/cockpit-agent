// 视觉单帧的采集端（实施计划 M4-6）。挂在根布局上，但**平时不渲染任何东西**。
//
// 红线条件②「未命中一帧都不采」在 RN 上的具体含义：**挂载 CameraView 就是打开摄像头**。
// 所以这里刻意不做「常驻一个 1x1 隐藏预览、要用时拍一张」——那看起来省事，实际是
// 摄像头一直开着，与「默认关 + 命中才采」直接冲突。真实形态：
//   命中触发词 → setArmed(true) → 挂载 → 等 onCameraReady → takePictureAsync → 立刻卸载。
// 代价是每次抓帧多一次相机冷启动（真机上几百毫秒），**这个代价是要付的**。
//
// 另一处刻意：权限**不在挂载时申请**，在第一次真要抓帧时申请。开着视觉开关却从没问过
// 视觉问题的用户，不该被弹一次相机权限。
import { CameraView, useCameraPermissions } from 'expo-camera'
import { useCallback, useEffect, useRef, useState } from 'react'
import { View } from 'react-native'

import { registerVisionCapturer } from '@/core/vision/frame'

/** 相机就绪的最长等待。超时按失败处理（Agent 侧会诚实说没拿到画面） */
const READY_TIMEOUT_MS = 4000

export function VisionCapture({ enabled }: { enabled: boolean }): React.ReactElement | null {
  const [armed, setArmed] = useState(false)
  const [perm, requestPerm] = useCameraPermissions()
  const camRef = useRef<CameraView | null>(null)
  const readyRef = useRef<(() => void) | null>(null)
  // 用 ref 存权限申请函数，避免把它写进 registerVisionCapturer 的依赖里反复重注册。
  // 在 effect 里更新（渲染期写 ref 违反 React 纯度约束，且并发渲染下会写到被丢弃的
  // 那次渲染上）；首渲染由 useRef 的初值兜住。
  const permRef = useRef({ perm, requestPerm })
  useEffect(() => {
    permRef.current = { perm, requestPerm }
  })

  const capture = useCallback(async (): Promise<string> => {
    const { perm: p, requestPerm: req } = permRef.current
    if (!p?.granted) {
      const res = await req()
      if (!res.granted) return ''
    }
    setArmed(true)
    try {
      // 等 onCameraReady——文档明写「takePictureAsync 前必须等它」，不等会拿到黑帧
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          readyRef.current = null
          reject(new Error('camera ready timeout'))
        }, READY_TIMEOUT_MS)
        readyRef.current = () => {
          clearTimeout(timer)
          readyRef.current = null
          resolve()
        }
      })
      const pic = await camRef.current?.takePictureAsync({
        quality: 0.7,
        skipProcessing: true,
        shutterSound: false,
      })
      return pic?.uri || ''
    } catch {
      return ''
    } finally {
      setArmed(false) // 拍完立刻卸载 = 关摄像头。放 finally 里，任何失败路径也要关
    }
  }, [])

  useEffect(() => {
    registerVisionCapturer(enabled ? capture : null)
    return () => registerVisionCapturer(null)
  }, [enabled, capture])

  if (!armed) return null
  return (
    // 0 尺寸但仍挂载：Android 上完全不布局的 CameraView 可能拿不到 preview surface，
    // 给 1x1 且 opacity 0 是这类「无预览拍照」的通行做法
    <View
      pointerEvents="none"
      style={{ position: 'absolute', width: 1, height: 1, opacity: 0, top: 0, left: 0 }}
    >
      <CameraView
        ref={camRef}
        style={{ width: 1, height: 1 }}
        facing="back"
        onCameraReady={() => readyRef.current?.()}
      />
    </View>
  )
}
