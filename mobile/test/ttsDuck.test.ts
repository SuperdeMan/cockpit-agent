// 语义打断的「压低」（2026-10-11，设计 docs/design/2026-10-11-semantic-barge-in.md §4.4）：
// 播放器都接到共享上下文上的一个播报总线 GainNode，压低 / 恢复只调总线增益。
// 这里验的是**接线与增益形状**：总线一个上下文一条、接到 destination；两种播放器实现都接到总线；
// 压低 0.3、恢复 1、带斜坡；压着期间新建的总线也从 0.3 起；没有 createGain 的上下文（单测替身）回落 destination。

function fakeCtx(opts: { gain?: boolean } = {}) {
  const gains: any[] = []
  const nodes: any[] = []
  const ctx: any = {
    sampleRate: 48000,
    currentTime: 2,
    destination: { tag: 'dest' },
    createBuffer: (_ch: number, length: number, sr: number) => ({ length, sampleRate: sr, copyToChannel() {} }),
    createBufferSource() {
      const src: any = { connected: null, connect(d: unknown) { src.connected = d }, start() {}, stop() {}, onEnded: null }
      Object.defineProperty(src, 'buffer', { set() {} })
      nodes.push(src)
      return src
    },
    createBufferQueueSource() {
      const node: any = { connected: null, connect(d: unknown) { node.connected = d }, enqueueBuffer: () => '0',
        start() {}, stop() {}, clearBuffers() {} }
      nodes.push(node)
      return node
    },
  }
  if (opts.gain !== false) {
    ctx.createGain = () => {
      const g: any = {
        connected: null,
        connect(d: unknown) { g.connected = d },
        gain: {
          value: 1,
          calls: [] as unknown[][],
          cancelScheduledValues(t: number) { this.calls.push(['cancel', t]) },
          setValueAtTime(v: number, t: number) { this.calls.push(['set', v, t]) },
          linearRampToValueAtTime(v: number, t: number) { this.calls.push(['ramp', v, t]); this.value = v },
        },
      }
      gains.push(g)
      return g
    }
  }
  return { ctx, gains, nodes }
}

let mod: typeof import('@/core/voice/audioCtx')

beforeEach(() => {
  jest.resetModules()
  mod = require('@/core/voice/audioCtx')
})

test('总线：一个上下文一条、接到 destination、重复取用同一条；换上下文重建；没有 createGain 回落 destination', () => {
  const a = fakeCtx()
  const bus = mod.ttsBus(a.ctx) as any
  expect(a.gains).toHaveLength(1)
  expect(bus.connected).toBe(a.ctx.destination)
  expect(mod.ttsBus(a.ctx)).toBe(bus)
  const b = fakeCtx()
  expect(mod.ttsBus(b.ctx)).not.toBe(bus)
  const plain = fakeCtx({ gain: false })
  expect(mod.ttsBus(plain.ctx)).toBe(plain.ctx.destination)
})

test('压低 0.3、恢复 1，都带斜坡；压着期间新建的总线也从 0.3 起', () => {
  const a = fakeCtx()
  const bus = mod.ttsBus(a.ctx) as any
  mod.duckTts(true)
  expect(bus.gain.calls.at(-1)).toEqual(['ramp', mod.DUCK_GAIN, 2 + 0.08])
  expect(mod.ttsDucked()).toBe(true)
  const b = fakeCtx()
  expect((mod.ttsBus(b.ctx) as any).gain.value).toBe(mod.DUCK_GAIN)
  mod.duckTts(false)
  expect((mod.ttsBus(b.ctx) as any).gain.calls.at(-1)).toEqual(['ramp', 1, 2 + 0.08])
  expect(mod.ttsDucked()).toBe(false)
})

test('两种播放器实现都接到总线：队列节点与逐片排定（PcmPlayer 的 destination 选项）', () => {
  const q = fakeCtx()
  mod.selectPcmPlayer(q.ctx, { sampleRate: 48000 }, 'queue').push(new Int16Array(480).fill(100))
  expect(q.nodes[0].connected).toBe(mod.ttsBus(q.ctx))
  const n = fakeCtx()
  mod.selectPcmPlayer(n.ctx, { sampleRate: 48000 }, 'nodes').push(new Int16Array(480).fill(100))
  expect(n.nodes[0].connected).toBe(mod.ttsBus(n.ctx))
})
