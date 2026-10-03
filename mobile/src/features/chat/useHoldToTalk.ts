// 光球「按住说话」手势（B2 T6 手势契约，方案 §5.1.1）的唯一实现——Composer 光球、空输入框背板、
// 欢迎态大球三处共用（v3 D4 一屏一球：欢迎态的麦克风是页面上的大球，Composer 退成无球形态；
// 契约原样搬过去，不另写一套）：
//  · 长按 ≥HOLD_MS：按下即开录；按住时上滑 ≥CANCEL_DY 取消（行车档禁用上滑取消，§5.1.1 行车条款）；松手发送；
//  · 长按识别前允许移动 HOLD_MAX_DISTANCE（列表纵向滚动与 chips 横滑不得误触）；
//  · 轻点不在这里：各处与自己的 Tap 组成 Exclusive（长按成立前抬手 = 轻点），轻点做什么由宿主判。
// 手势用 react-native-gesture-handler（PackageList.java:73 已注册，零新依赖）。
import { useRef } from 'react'
import { Gesture } from 'react-native-gesture-handler'

import type { PttHandle } from './usePtt'

/** 长按判定（ms）：方案 §5.1.1 的 ≥300；usePtt 的 MIN_DURATION_MS=320 是「录了多久」，是另一件事 */
export const HOLD_MS = 300
/** 长按识别前允许的移动（dp）：超过就交给滚动 */
export const HOLD_MAX_DISTANCE = 12
/** 按住时上滑多少算取消（dp） */
export const CANCEL_DY = 60

/**
 * 返回造「按住说话」手势的函数：每个 GestureDetector 要自己的一份手势实例。
 * 按下 / 取消的去重状态（held / cancelled）放在本 hook 的 ref 里，同一宿主造的几份共用——同一时刻只可能有一份在按。
 * 没有 ptt（语音未配置）或正在定稿时手势不启用。
 */
export function useHoldToTalk(ptt: PttHandle | null, driving: boolean): () => ReturnType<typeof Gesture.Pan> {
  const heldRef = useRef(false)
  const cancelledRef = useRef(false)
  const enabled = !!ptt && ptt.state !== 'finalizing'
  // 按住 = Pan.activateAfterLongPress：激活即按下；onUpdate 看上滑；结束即松手（取消过就不发）
  return () =>
    Gesture.Pan()
      .runOnJS(true)
      .enabled(enabled)
      .maxPointers(1)
      .minDistance(HOLD_MAX_DISTANCE)
      .activateAfterLongPress(HOLD_MS)
      .onStart(() => {
        heldRef.current = true
        cancelledRef.current = false
        ptt?.pressDown()
      })
      .onUpdate((e) => {
        // §5.1.1 行车条款：行车档**只保留**「按住—松开发送」，上滑取消禁用
        // （开车时的空间手势不可靠；取消这条路留给泊车态）
        if (!driving && heldRef.current && !cancelledRef.current && e.translationY < -CANCEL_DY) {
          cancelledRef.current = true
          ptt?.cancel()
        }
      })
      .onFinalize(() => {
        if (heldRef.current && !cancelledRef.current) ptt?.pressUp()
        heldRef.current = false
      })
}

/** 轻点手势（与按住说话组成 Exclusive）：长按判定之前抬手才算轻点 */
export function orbTap(onTap: () => void) {
  return Gesture.Tap()
    .runOnJS(true)
    .maxDuration(HOLD_MS - 20)
    .onEnd(() => onTap())
}
