// 队列节点播放器（语音批 2026-09-06「嗡嗡」修法）：一个 AudioBufferQueueSourceNode 顺序吃片。
// 这里验的是**记账与节点调用形状**：首片攒 jitter 后 start 一次、后续只 enqueue 不再建节点/不再 start、
// 队空后到的片从 now 记账并先回调 onUnderrun（TtsSession.noteUnderrun 靠回调时刻的 nextStart 算空白）、
// 重采样后按实际目标帧数记时长、stop 后再 push 重新建节点。真机上的对照（FastMixer underrun 帧数）见计划 §6.3。
import { PcmPlayer } from '@shared/pcmPlayer.mjs'
import { selectPcmPlayer } from '@/core/voice/audioCtx'
import { QueuePcmPlayer } from '@/core/voice/queuePlayer'

 

function fakeQueueCtx(sampleRate: number, opts: { withQueue?: boolean } = {}) {
  const calls: string[] = []
  const nodes: any[] = []
  const buffers: { length: number; sampleRate: number }[] = []
  const ctx: any = {
    sampleRate,
    currentTime: 0,
    destination: { tag: 'dest' },
    createBuffer(_ch: number, length: number, sr: number) {
      const b = { length, sampleRate: sr, copyToChannel: jest.fn() }
      buffers.push(b)
      return b
    },
    createBufferSource() {
      const src: any = { connect() {}, start() {}, stop() {}, onEnded: null }
      Object.defineProperty(src, 'buffer', { set() {} })
      return src
    },
  }
  if (opts.withQueue !== false) {
    ctx.createBufferQueueSource = (o: unknown) => {
      const node: any = {
        options: o,
        queue: [] as any[],
        connected: null as unknown,
        starts: [] as number[],
        stopped: 0,
        cleared: 0,
        connect(d: unknown) {
          node.connected = d
        },
        enqueueBuffer(b: any) {
          node.queue.push(b)
          calls.push('enqueue')
          return String(node.queue.length - 1)
        },
        start(when: number, offset?: number) {
          // 库的 JS 包装：start(when = 0, offset = -1) 且 `offset && offset < 0` 抛 RangeError ⇒ 缺省调用就是无声
          if (offset === undefined || offset < 0) throw new RangeError('offset must be a finite non-negative number: ' + offset)
          node.starts.push(when)
          calls.push('start@' + when.toFixed(3))
        },
        stop() {
          node.stopped += 1
          calls.push('stop')
        },
        clearBuffers() {
          node.cleared += 1
          calls.push('clear')
        },
      }
      nodes.push(node)
      calls.push('createNode')
      return node
    }
  }
  return { ctx, calls, nodes, buffers }
}

const chunk = (n: number, v = 1000) => new Int16Array(n).fill(v)

test('同率：首片建一个节点、连 destination、入队、start(now+jitter)、报 onFirstAudio；后续只入队', () => {
  const { ctx, calls, nodes } = fakeQueueCtx(48000)
  ctx.currentTime = 5
  const first = jest.fn()
  const p = new QueuePcmPlayer({ ctx, sampleRate: 48000, jitterMs: 200, onFirstAudio: first })
  expect(p.push(chunk(4800))).toBeCloseTo(5.2, 9) // 首片起播 = now + 0.2
  expect(first).toHaveBeenCalledTimes(1)
  expect(nodes).toHaveLength(1)
  expect(nodes[0].connected).toBe(ctx.destination)
  expect(nodes[0].options).toEqual({ pitchCorrection: false })
  expect(p.nextStart).toBeCloseTo(5.3, 9) // 0.1s 音频
  expect(p.push(chunk(4800))).toBeCloseTo(5.3, 9) // 无缝拼上一片尾巴
  expect(p.push(chunk(2400))).toBeCloseTo(5.4, 9)
  expect(p.nextStart).toBeCloseTo(5.45, 9)
  expect(nodes).toHaveLength(1) // 不再建节点
  expect(nodes[0].starts).toEqual([5.2]) // 不再 start
  expect(nodes[0].queue).toHaveLength(3)
  expect(calls).toEqual(['createNode', 'enqueue', 'start@5.200', 'enqueue', 'enqueue'])
  expect(p.underruns).toBe(0)
  expect(p.remainingSec()).toBeCloseTo(0.45, 9)
})

test('队列被追平：这一片从 now 记账、underruns+1、onUnderrun 在更新 nextStart 之前回调（空白 = now − 旧 nextStart）', () => {
  const { ctx, nodes } = fakeQueueCtx(48000)
  ctx.currentTime = 1
  const seen: { nextStart: number; now: number }[] = []
  const p = new QueuePcmPlayer({
    ctx,
    sampleRate: 48000,
    jitterMs: 100,
    onUnderrun: () => seen.push({ nextStart: p.nextStart, now: ctx.currentTime }),
  })
  p.push(chunk(4800)) // 起播 1.1，结束 1.2
  ctx.currentTime = 1.5 // 迟到 300ms：节点这段时间在填零
  expect(p.push(chunk(4800))).toBe(1.5)
  expect(p.underruns).toBe(1)
  expect(seen).toHaveLength(1)
  expect(seen[0].nextStart).toBeCloseTo(1.2, 9) // 浮点：1 + 0.1 + 0.1
  expect(seen[0].now).toBe(1.5)
  expect(p.nextStart).toBeCloseTo(1.6, 9)
  expect(nodes).toHaveLength(1) // 续播用同一个节点，不重建
  expect(nodes[0].starts).toHaveLength(1)
})

test('24k → 48k：buffer 建在 ctx 率上、时长按实际提交帧数记，首片比名义短的那 2 帧不会在下一片留缝', () => {
  const { ctx, buffers } = fakeQueueCtx(48000)
  const p = new QueuePcmPlayer({ ctx, sampleRate: 24000, jitterMs: 0 })
  const w0 = p.push(chunk(1024))
  expect(w0).toBe(0)
  expect(buffers[0].sampleRate).toBe(48000)
  expect(buffers[0].length).toBeGreaterThan(2000)
  expect(buffers[0].length).toBeLessThanOrEqual(2048)
  expect(p.nextStart).toBeCloseTo(buffers[0].length / 48000, 12)
  const w1 = p.push(chunk(1024))
  expect(w1).toBeCloseTo(buffers[0].length / 48000, 12) // 严丝合缝：下一片记账起点 = 上一片实际帧数结束
  expect(p.nextStart).toBeCloseTo((buffers[0].length + buffers[1].length) / 48000, 12)
})

test('空片 / 重采样后无样本：不入队、不记账、返回 null', () => {
  const { ctx, nodes } = fakeQueueCtx(48000)
  const p = new QueuePcmPlayer({ ctx, sampleRate: 48000 })
  expect(p.push(new Int16Array(0))).toBeNull()
  expect(nodes).toHaveLength(0)
  expect(p.started).toBe(false)
  expect(p.drainedAt()).toBe(ctx.currentTime)
})

test('stop：停节点 + 清队列 + 记账归零；再 push 当新首片（新节点、重新攒 jitter）', () => {
  const { ctx, nodes, calls } = fakeQueueCtx(48000)
  ctx.currentTime = 2
  const first = jest.fn()
  const p = new QueuePcmPlayer({ ctx, sampleRate: 48000, jitterMs: 200, onFirstAudio: first })
  p.push(chunk(4800))
  p.push(chunk(4800))
  p.stop()
  expect(nodes[0].stopped).toBe(1)
  expect(nodes[0].cleared).toBe(1)
  expect(p.started).toBe(false)
  expect(p.remainingSec()).toBe(0)
  ctx.currentTime = 3
  expect(p.push(chunk(4800))).toBeCloseTo(3.2, 9)
  expect(nodes).toHaveLength(2)
  expect(first).toHaveBeenCalledTimes(2)
  expect(calls.slice(-3)).toEqual(['createNode', 'enqueue', 'start@3.200'])
  p.stop()
  p.stop() // 幂等
  expect(nodes[1].stopped).toBe(1)
})

test('selectPcmPlayer：ctx 有 createBufferQueueSource 且 impl=queue → 队列播放器；impl=nodes 或 ctx 没有该方法 → 旧的逐片排定', () => {
  const withQueue = fakeQueueCtx(48000).ctx
  const withoutQueue = fakeQueueCtx(48000, { withQueue: false }).ctx
  expect(selectPcmPlayer(withQueue, { sampleRate: 24000 }, 'queue')).toBeInstanceOf(QueuePcmPlayer)
  expect(selectPcmPlayer(withQueue, { sampleRate: 24000 }, 'nodes')).toBeInstanceOf(PcmPlayer)
  expect(selectPcmPlayer(withoutQueue, { sampleRate: 24000 }, 'queue')).toBeInstanceOf(PcmPlayer)
})
