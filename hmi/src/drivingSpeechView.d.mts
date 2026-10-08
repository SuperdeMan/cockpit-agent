import type { Msg } from './types'
export class DrivingSpeechView {
  constructor(capacity?: number)
  observe(frame: unknown, registry: { bubbleFor(frame: unknown): string | null }): void
  forMessage(message?: Msg): { text: string; source: 'speech' | 'answer' }
}
