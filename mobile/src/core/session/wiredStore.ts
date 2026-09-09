// 「已接线的会话 store」的 useSyncExternalStore 端口（AR06 / A06-1）。
//
// 为什么要这一层：诊断/设置这类**可能在接线之前就被打开**的屏也要读会话事实（行车档、
// 用户压过没有），`getWired()` 于是可能是 null。原来两个屏各自写「useState 初值读一次 +
// useEffect 里再 setState 补挂载与订阅之间的缝 + subscribe」——那既是**两份**同样的接线，
// 又在 effect 里同步 setState（React 19 的 `react-hooks/set-state-in-effect`：级联渲染）。
//
// useSyncExternalStore 就是 React 给「外部可变数据源」的正规入口：订阅与读快照同一份，
// 天然没有「挂载与订阅之间的缝」。
//
// ⚠ `getSnapshot` 必须返回**引用稳定**的值：这里直接返回 zustand 的 `getState()`（同一次
// 更新内引用不变），不要在这里 map 成新对象——那会让 React 判定「快照每次都变」而死循环。
// 字段投影交给调用方在渲染里做。
import type { StoreApi } from 'zustand/vanilla'

import type { SessionState } from './store'
import { getWired } from './wiring'

const NOOP = (): void => {}

/** 订阅已接线会话 store；未接线时返回空取消订阅（与原来的 `if (!core) return` 同语义） */
export function subscribeWiredSession(onChange: () => void): () => void {
  return getWired()?.core.store.subscribe(onChange) ?? NOOP
}

/** 已接线会话的当前状态；未接线返回 null */
export function wiredSessionSnapshot(): SessionState | null {
  const store: StoreApi<SessionState> | undefined = getWired()?.core.store
  return store ? store.getState() : null
}
