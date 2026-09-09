// mobile/src/ui/layout/bottomChrome.ts
// 页面自己的底部浮层（今天只有地图信息条）按路由向浮动助手上报占位高度；助手浮在它上方，
// 不压住「全览 / 收起详情」（AR04 第十五节）。上报的是**事实**（量到的高度 + 自身下边距），
// 「浮到哪」这个判据只在 AssistantPresence 里算一次。按路由登记：地图页留在导航栈里时，
// 设置页不该被它的信息条顶高。零 RN import，jest 直接跑。
import { useSyncExternalStore } from 'react'

const heights = new Map<string, number>()
const listeners = new Set<() => void>()

export function reportBottomChrome(route: string, height: number): void {
  const next = Math.max(0, Math.round(height))
  if ((heights.get(route) ?? 0) === next) return
  if (next === 0) heights.delete(route)
  else heights.set(route, next)
  for (const fn of listeners) fn()
}

export function bottomChromeOf(route: string): number {
  return heights.get(route) ?? 0
}

export function subscribeBottomChrome(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

export function useBottomChrome(route: string): number {
  return useSyncExternalStore(subscribeBottomChrome, () => bottomChromeOf(route))
}
