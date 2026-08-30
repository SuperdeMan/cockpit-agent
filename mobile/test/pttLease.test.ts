// mobile/test/pttLease.test.ts
// §0 第 5 条：PTT 在免唤醒开着时今天是坏的——AsrSession 用 recorder() 单例，已录时 start 静默 return
// （一帧收不到，recorder.ts:50）、stop 会把免唤醒的真麦停掉（:89）。修法：PTT 的 AsrSession 也领一路 micLease。
// 这里验的是**组合**：一路 lease 常开（免唤醒）时，第二路（PTT）能收到帧、停下时真麦不关。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { AsrSession } from '@/core/voice/asr'
import { micBusStats, micLease, resetMicBusForTest } from '@/core/voice/micBus'
import { setRecorderForTest, type FrameSink, type Recorder } from '@/core/voice/recorder'

class FakeRecorder implements Recorder {
  starts = 0
  stops = 0
  sink: FrameSink | null = null
  deviceRate = 16000
  get recording(): boolean {
    return !!this.sink
  }
  async start(onFrame: FrameSink): Promise<void> {
    this.starts += 1
    this.sink = onFrame
  }
  async stop(): Promise<void> {
    this.stops += 1
    this.sink = null
  }
  emit(n = 1600): void {
    this.sink?.(new Int16Array(n))
  }
}

class FakeWs {
  static last: FakeWs | null = null
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(readonly url: string) {
    FakeWs.last = this
  }
  send(d: unknown): void {
    this.sent.push(d)
  }
  close(): void {
    this.readyState = 3
  }
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }
  get binarySent(): unknown[] {
    return this.sent.filter((s) => typeof s !== 'string')
  }
}

const origWs = (global as unknown as { WebSocket: unknown }).WebSocket
let rec: FakeRecorder
beforeEach(() => {
  resetMicBusForTest()
  rec = new FakeRecorder()
  setRecorderForTest(rec)
  ;(global as unknown as { WebSocket: unknown }).WebSocket = FakeWs
})
afterEach(() => {
  setRecorderForTest(null)
  ;(global as unknown as { WebSocket: unknown }).WebSocket = origWs
})

test('免唤醒常开一路时，PTT 的 AsrSession（micLease）能收到帧并上行；PTT 停下真麦不关', async () => {
  const handsFree = micLease()
  await handsFree.start(() => {})
  expect(rec.starts).toBe(1)
  const ptt = new AsrSession(
    { audioUrl: 'https://x', language: 'zh', provider: 'dashscope', model: 'fun-asr-realtime' },
    { onFinal() {}, onError() {} },
    micLease(),
  )
  await ptt.start()
  FakeWs.last!.open()
  expect(micBusStats().active).toBe(2)
  rec.emit(1600)
  expect(FakeWs.last!.binarySent).toHaveLength(1) // PTT 这一路拿到了帧
  await ptt.cancel()
  expect(rec.stops).toBe(0) // 真麦仍开着——免唤醒那一路还在
  expect(micBusStats().active).toBe(1)
  await handsFree.stop()
  expect(rec.stops).toBe(1)
})

/** 剥掉注释再扫源码：`usePtt.ts` 的头注里**要**写清楚「recorder() 单例为什么不能用」，
 *  裸扫会红在那句解释上（同「`dec` 是 `declared` 的子串」那条：零领域词断言不能裸扫源码）。
 *  只剥整行 `//` 与块注释，不动字符串字面量里的 `//`。 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((l) => !l.trimStart().startsWith('//'))
    .join('\n')
}

test('usePtt 的 AsrSession 必须领 micLease，不再用 recorder() 单例（源码级断言）', () => {
  const src = readFileSync(resolve(__dirname, '../src/features/chat/usePtt.ts'), 'utf8')
  const code = stripComments(src)
  // 通道自检：剥完之后代码还在（否则「没匹配到 recorder()」可能只是因为把整个文件剥没了）
  expect(code).toContain('export function usePtt')
  expect(code).toContain('micLease()')
  expect(code).not.toMatch(/\brecorder\(\)/)
})
