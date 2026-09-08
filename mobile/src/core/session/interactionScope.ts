/** 应用宿主的事实闸；不保存语音状态，语音 FSM 仍只有 voiceLoop 一份。 */
export const ASSISTANT_ROUTES = new Set(['/', '/map', '/vehicle', '/settings'])

export interface InteractionFacts {
  route: string
  foreground: boolean
  focused: boolean
  configured?: boolean
  occluded?: number
  keyboardVisible?: boolean
}

export class InteractionScope {
  private listeners = new Set<() => void>()
  private facts: InteractionFacts
  private blockers = new Set<object>()
  constructor(initial: InteractionFacts) { this.facts = initial }
  snapshot = (): InteractionFacts => this.facts
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
  update(patch: Partial<InteractionFacts>): void {
    const next = { ...this.facts, ...patch }
    if (Object.keys(next).every((key) => next[key as keyof InteractionFacts] === this.facts[key as keyof InteractionFacts])) return
    this.facts = next
    // 同步通知：撤回不能等 React 下一帧，更不能等权限/ASR 的 promise 完成。
    for (const fn of this.listeners) fn()
  }
  canCapture = (): boolean => this.facts.configured !== false && this.facts.foreground && ASSISTANT_ROUTES.has(this.facts.route)
  canPresent = (): boolean => this.canCapture() && this.facts.focused && !this.facts.keyboardVisible && !this.blockers.size
  blockPresentation(): () => void {
    const owner = {}
    this.blockers.add(owner)
    this.update({ occluded: this.blockers.size })
    return () => { this.blockers.delete(owner); this.update({ occluded: this.blockers.size }) }
  }
}
