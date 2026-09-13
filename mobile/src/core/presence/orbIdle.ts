// 光球「空闲静置」的钟（2026-09-13，性能评审 §2.1 的裁决：视觉一帧不改，只改「多久没人理它之后停下来」）。
//
// 真机读数（OPPO PEUM00 / prod 包）：对话页已连接、光球 idle 呼吸 + 三层旋转时进程 CPU 155–160%
// （RenderThread 62–66% + hwuiTask×2 各 28% + 主线程 25%），光球静止时 7–10%。一台放在支架上的
// 手机只要停在对话页就烧 1.5 核，而那时屏上什么都没发生。
//
// 这里只有一块表，不做判断：`touch()` = 有人理它了（触摸 / 键盘 / 状态变化 / 回到前台 / 换页），
// ORB_IDLE_STILL_MS 内没有再来一次就翻成 still。「静置时哪些球停、哪些球照转」是 orbPolicy 的判据
// （听 / 想 / 说永远不静置，只有 idle / armed 会）。定时器可注入，jest 不碰真时钟。
export const ORB_IDLE_STILL_MS = 30_000

export interface IdleClockTimers {
  set(fn: () => void, ms: number): unknown
  clear(id: unknown): void
}

const REAL_TIMERS: IdleClockTimers = {
  set: (fn, ms) => {
    const id = setTimeout(fn, ms)
    // node（jest）里的定时器对象会把 worker 钉住不退出；RN 的是数字，没有 unref，可选链跳过
    ;(id as unknown as { unref?: () => void }).unref?.()
    return id
  },
  clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
}

export class IdleClock {
  private timer: unknown = null
  private still = false
  private readonly subs = new Set<() => void>()

  constructor(
    private readonly ms: number = ORB_IDLE_STILL_MS,
    private readonly timers: IdleClockTimers = REAL_TIMERS,
  ) {}

  get isStill(): boolean {
    return this.still
  }

  /** useSyncExternalStore 的两个入口（箭头函数：解构传递也不丢 this） */
  readonly snapshot = (): boolean => this.still
  readonly subscribe = (fn: () => void): (() => void) => {
    this.subs.add(fn)
    return () => {
      this.subs.delete(fn)
    }
  }

  /** 有人理它了：重新起表；已经静置的立即恢复（先恢复再起表，恢复不等下一帧） */
  readonly touch = (): void => {
    if (this.timer !== null) this.timers.clear(this.timer)
    this.timer = this.timers.set(() => {
      this.timer = null
      if (this.still) return
      this.still = true
      this.publish()
    }, this.ms)
    if (this.still) {
      this.still = false
      this.publish()
    }
  }

  dispose(): void {
    if (this.timer !== null) this.timers.clear(this.timer)
    this.timer = null
    this.subs.clear()
  }

  private publish(): void {
    for (const fn of this.subs) fn()
  }
}
