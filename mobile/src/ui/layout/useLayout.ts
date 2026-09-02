// mobile/src/ui/layout/useLayout.ts
// 布局事实收集（B4-1）：窗口尺寸（useWindowDimensions，旋转 / 展开即时重算）+ 折叠姿态（B3 hook）
// + 行车档 → sizeClass.layoutMode()。**零判断**：所有「该用哪种布局」都在 sizeClass.ts 里且有测试。
// 原生 foldstate 缺席（旧 APK）时 useFoldState 恒 null ⇒ posture 恒 flat（方案 §11.1 B4 行「缺席按 flat 降级」）。
// 切屏事件（§7.4 的 PTT 松手）**不在这里算**——渲染期用 ref 记 prev 在 StrictMode 双渲下会把事件吞掉，
// 放消费方（ChatScreen）的 useEffect 里用 screenSwitch() 判（T7）。
import { useMemo } from 'react'
import { PixelRatio, useWindowDimensions } from 'react-native'

import type { FoldEvent } from '../../../modules/foldstate'
import { foldPosture, type FoldPosture } from './foldPosture'
import {
  bookSplit,
  heightClass,
  layoutMode,
  stageWidth,
  widthClass,
  type HeightClass,
  type LayoutMode,
  type WidthClass,
} from './sizeClass'
import { useFoldState } from './useFoldState'

export interface LayoutSpec {
  mode: LayoutMode
  width: number
  height: number
  widthClass: WidthClass
  heightClass: HeightClass
  posture: FoldPosture
  /** 舞台宽（two-pane / drawer 用） */
  stage: number
  /** book：左栏宽与 gap（铰链落 gap 正中） */
  book: { chat: number; gap: number }
  /** 铰链几何（dp，窗口坐标）；无折叠特征时 null */
  hinge: { leftDp: number; topDp: number; widthDp: number; heightDp: number } | null
  /** 原生事件原样带出（T7 的切屏判定读 state） */
  fold: FoldEvent | null
  landscape: boolean
}

export function useLayout(driving: boolean): LayoutSpec {
  const { width, height } = useWindowDimensions()
  const fold = useFoldState()
  return useMemo(() => {
    const posture = foldPosture(fold)
    const px = PixelRatio.get()
    const b = fold?.bounds ?? null
    const hinge = b
      ? { leftDp: b.left / px, topDp: b.top / px, widthDp: (b.right - b.left) / px, heightDp: (b.bottom - b.top) / px }
      : null
    return {
      mode: layoutMode({ width, height, posture, driving }),
      width,
      height,
      widthClass: widthClass(width),
      heightClass: heightClass(height),
      posture,
      stage: stageWidth(width),
      book: bookSplit(width, hinge && posture === 'book' ? { leftDp: hinge.leftDp, widthDp: hinge.widthDp } : null),
      hinge,
      fold,
      landscape: width > height,
    }
  }, [width, height, fold, driving])
}
