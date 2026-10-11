// 唤醒 / 退场应答片段（2026-10-11，设计 docs/design/2026-10-11-wake-acknowledgement.md §3.2）。
// 合成与播放都换成假件：这里验的是「准备、复用、作废、随机不重复、时长、让位」这些纯逻辑。

type Synth = (text: string) => Promise<{ pcm: Int16Array; sampleRate: number }>

interface FakePlayer {
  pushed: Int16Array[]
  stopped: number
  rate: number
  push(x: Int16Array): number
  stop(): void
}

let synth: Synth
let players: FakePlayer[]
let ctxState: string | null

/** 每个字 100ms 的假合成：时长可从句子长度推出来 */
const byLength: Synth = async (text) => ({ pcm: new Int16Array(2400 * text.length), sampleRate: 24000 })

beforeEach(() => {
  jest.resetModules()
  players = []
  ctxState = 'running'
  synth = byLength
  jest.doMock('@/core/voice/tts', () => ({
    collectTtsPcm: (_cfg: unknown, text: string) => synth(text),
  }))
  jest.doMock('@/core/voice/audioCtx', () => ({
    newPcmPlayer: ({ sampleRate }: { sampleRate: number }) => {
      const p: FakePlayer = {
        pushed: [],
        stopped: 0,
        rate: sampleRate,
        push(x) {
          this.pushed.push(x)
          return 0
        },
        stop() {
          this.stopped += 1
        },
      }
      players.push(p)
      return p
    },
    peekSharedAudioContext: () => (ctxState ? { state: ctxState } : null),
    scheduleAudioIdle: () => {},
    sharedAudioContext: () => ({}),
  }))
})

function load(): typeof import('@/core/voice/voiceCue') {
  return require('@/core/voice/voiceCue')
}

test('准备好后就绪；播一条返回它的时长并整段推进播放器；不是这组音色一律返回 0', async () => {
  const m = load()
  const key = m.voiceCueKey('minimax', 'female-shaonv')
  expect(m.voiceCuesReady(key)).toBe(false)
  expect(m.playVoiceCue('wake', key)).toBe(0)
  expect(await m.prepareVoiceCues('https://x', 'minimax', 'female-shaonv')).toBe(true)
  expect(m.voiceCuesReady(key)).toBe(true)
  expect(m.voiceCueCounts()).toEqual({ wake: 5, exit: 3 })
  const ms = m.playVoiceCue('wake', key)
  expect([200, 300]).toContain(ms) // 两个字或三个字的那几句
  expect(players).toHaveLength(1)
  expect(players[0].rate).toBe(24000)
  expect(players[0].pushed).toHaveLength(1)
  expect(Math.round((players[0].pushed[0].length / 24000) * 1000)).toBe(ms)
  expect(m.playVoiceCue('wake', m.voiceCueKey('minimax', 'male-qn'))).toBe(0)
})

test('逐句串行合成；单句失败跳过、保留成功的', async () => {
  let inFlight = 0
  let maxInFlight = 0
  synth = async (text) => {
    inFlight += 1
    maxInFlight = Math.max(maxInFlight, inFlight)
    await Promise.resolve()
    inFlight -= 1
    if (text === '在呢' || text === '好的') throw new Error('provider 5xx')
    return byLength(text)
  }
  const m = load()
  expect(await m.prepareVoiceCues('https://x', 'p', 'v')).toBe(true)
  expect(maxInFlight).toBe(1)
  expect(m.voiceCueCounts()).toEqual({ wake: 4, exit: 2 })
})

test('唤醒类一句都没成 ⇒ 返回 false、不替换已就绪的那一组', async () => {
  const m = load()
  expect(await m.prepareVoiceCues('https://x', 'p', 'v1')).toBe(true)
  synth = async () => {
    throw new Error('offline')
  }
  expect(await m.prepareVoiceCues('https://x', 'p', 'v2')).toBe(false)
  expect(m.voiceCuesReady(m.voiceCueKey('p', 'v1'))).toBe(true)
  expect(m.voiceCuesReady(m.voiceCueKey('p', 'v2'))).toBe(false)
})

test('同一组正在合成就复用那一次；期间换了音色，旧的那次作废不落地', async () => {
  let release!: () => void
  const gate = new Promise<void>((r) => {
    release = r
  })
  synth = async (text) => {
    await gate
    return byLength(text)
  }
  const m = load()
  const a = m.prepareVoiceCues('https://x', 'p', 'v1')
  expect(m.prepareVoiceCues('https://x', 'p', 'v1')).toBe(a)
  const b = m.prepareVoiceCues('https://x', 'p', 'v2')
  release()
  expect(await a).toBe(false)
  expect(await b).toBe(true)
  expect(m.voiceCuesReady(m.voiceCueKey('p', 'v2'))).toBe(true)
  expect(m.voiceCuesReady(m.voiceCueKey('p', 'v1'))).toBe(false)
})

test('随机挑一条，但不连着两次同一条', async () => {
  const m = load()
  await m.prepareVoiceCues('https://x', 'p', 'v')
  const key = m.voiceCueKey('p', 'v')
  const rnd = jest.spyOn(Math, 'random').mockReturnValue(0)
  try {
    m.playVoiceCue('wake', key)
    m.playVoiceCue('wake', key)
    m.playVoiceCue('wake', key)
  } finally {
    rnd.mockRestore()
  }
  const [first, second, third] = players.map((p) => p.pushed[0])
  expect(second).not.toBe(first)
  expect(third).toBe(first) // 只是「不连着」，隔一次可以再出现
})

test('输出上下文没在跑（空闲挂起）⇒ 报给状态机的时长多算 resume 的余量', async () => {
  const m = load()
  await m.prepareVoiceCues('https://x', 'p', 'v')
  const key = m.voiceCueKey('p', 'v')
  ctxState = 'suspended'
  const ms = m.playVoiceCue('exit', key)
  const clipMs = Math.round((players[0].pushed[0].length / 24000) * 1000)
  expect(ms).toBe(clipMs + m.CUE_RESUME_ALLOWANCE_MS)
})

test('新播一条先停上一条；stopVoiceCue / clearVoiceCues 停掉正在播的，清空后不再就绪', async () => {
  const m = load()
  await m.prepareVoiceCues('https://x', 'p', 'v')
  const key = m.voiceCueKey('p', 'v')
  m.playVoiceCue('wake', key)
  m.playVoiceCue('exit', key)
  expect(players[0].stopped).toBe(1)
  m.stopVoiceCue()
  expect(players[1].stopped).toBe(1)
  m.playVoiceCue('wake', key)
  m.clearVoiceCues()
  expect(players[2].stopped).toBe(1)
  expect(m.voiceCuesReady(key)).toBe(false)
  expect(m.playVoiceCue('wake', key)).toBe(0)
})

test('唤醒提示音让位：只让一次、过期作废', () => {
  const m = load()
  m.muteNextWakeTone(1000)
  expect(m.takeWakeToneMuted(1000 + m.WAKE_TONE_MUTE_MS - 1)).toBe(true)
  expect(m.takeWakeToneMuted(1000 + 10)).toBe(false) // 取过即失效
  m.muteNextWakeTone(5000)
  expect(m.takeWakeToneMuted(5000 + m.WAKE_TONE_MUTE_MS)).toBe(false) // 过期
  expect(m.takeWakeToneMuted(0)).toBe(false)
})
