// mobile/src/core/presence/activityLog.ts
// 采集激活环形日志（方案 §5.10 隐私栏「最近一次激活」）。**内存、20 条、不上传、不持久化**：
// 它回答的是「刚才麦克风/摄像头为什么开了」，不是审计——审计在服务端账本。
export type ActivitySource = 'mic' | 'camera' | 'location'

export interface ActivityEntry {
  source: ActivitySource
  note: string
  at: number
}

export class ActivityLog {
  private items: ActivityEntry[] = []
  private readonly subs = new Set<() => void>()

  constructor(
    private readonly capacity = 20,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  push(source: ActivitySource, note: string): void {
    this.items = [{ source, note, at: this.clock() }, ...this.items].slice(0, this.capacity)
    for (const fn of this.subs) fn()
  }

  /** 最新在前 */
  list(): ActivityEntry[] {
    return this.items.slice()
  }

  lastOf(source: ActivitySource): ActivityEntry | null {
    return this.items.find((e) => e.source === source) ?? null
  }

  subscribe(fn: () => void): () => void {
    this.subs.add(fn)
    return () => {
      this.subs.delete(fn)
    }
  }
}

/** App 级单例（隐私栏读；PTT / 免唤醒 / 视觉在激活处写） */
export const activityLog = new ActivityLog()
