// 唤醒词参数档与实验计数器（AR07 / A07-1）。
//
// 三组主张，每组对着一种「读数看着正常、其实无效」的形态：
//  ① 参数校验：越界**抛错**而不是静默夹——夹过之后 A/B 两臂会是同一个档，
//     而报告上写着两个不同的数字，这种失败没有任何症状；
//  ② 回读：断言的是「**原生实际收到**的那一组值」，不是「我以为传了什么」；
//  ③ 计数分栏：原生命中 / 真的进了可交互态 / 命中但 FSM 没接 / 无命中的进入，
//     四种必须分开——混在一起调阈值就是照着被污染的成功率调。
const nativeLoads: { keywords: string; threshold: number; score: number }[] = []

jest.mock('../modules/kws', () => ({
  __esModule: true,
  KWS_NATIVE_AVAILABLE: true,
  default: {
    load: jest.fn(async (keywords: string, threshold: number, score: number) => {
      nativeLoads.push({ keywords, threshold, score })
    }),
    release: jest.fn(async () => {}),
    reset: jest.fn(async () => {}),
    acceptFrame: jest.fn(() => true),
    isLoaded: jest.fn(() => true),
    stats: jest.fn(() => ({ loaded: true, queued: 0, dropped: 0, processed: 0 })),
    addListener: jest.fn(() => ({ remove: () => {} })),
  },
}))

import { DEFAULT_SCORE, DEFAULT_THRESHOLD, KwsEngine } from '@/core/voice/kws'
import {
  EXPLORATION_PROFILES,
  KwsProfileError,
  PRODUCTION_PROFILE,
  sameProfileValues,
  validateKwsProfile,
} from '@/core/voice/kwsProfile'
import {
  OBSERVATION_WINDOW_MS,
  TRIAL_EVENT_CAP,
  endKwsTrial,
  formatKwsTrial,
  kwsTrialActive,
  kwsTrials,
  noteKeywordHit,
  noteKwsStats,
  noteListeningEntered,
  resetKwsTrialsForTest,
  startKwsTrial,
  summarizeKwsTrial,
} from '@/core/voice/kwsExperiment'

const cb = { onKeyword: () => {} }

describe('① 参数校验', () => {
  test('生产默认就是 kws.ts 的 DEFAULT_*（不许出现第二份 0.2）', () => {
    expect(PRODUCTION_PROFILE.threshold).toBe(DEFAULT_THRESHOLD)
    expect(PRODUCTION_PROFILE.score).toBe(DEFAULT_SCORE)
  })

  test('探索集只动 threshold —— 这就是「单变量」的那个单', () => {
    for (const p of EXPLORATION_PROFILES) expect(p.score).toBe(DEFAULT_SCORE)
    const thresholds = EXPLORATION_PROFILES.map((p) => p.threshold)
    expect(new Set(thresholds).size).toBe(thresholds.length) // 两臂不许是同一个档
  })

  test.each([
    ['threshold 0', { id: 'x', threshold: 0, score: 2 }],
    ['threshold >1', { id: 'x', threshold: 1.2, score: 2 }],
    ['threshold NaN', { id: 'x', threshold: Number.NaN, score: 2 }],
    ['score <0', { id: 'x', threshold: 0.2, score: -1 }],
    ['score >10', { id: 'x', threshold: 0.2, score: 11 }],
    ['空 id', { id: '  ', threshold: 0.2, score: 2 }],
  ])('%s 抛错，不静默夹到边界', (_label, bad) => {
    expect(() => validateKwsProfile(bad)).toThrow(KwsProfileError)
  })

  test('合法值返回规范化的**新对象**（调用方后改自己那份不影响已生效的档）', () => {
    const src = { id: ' B-thr015 ', threshold: 0.15, score: 2 }
    const out = validateKwsProfile(src)
    expect(out).toEqual({ id: 'B-thr015', threshold: 0.15, score: 2 })
    src.threshold = 0.9
    expect(out.threshold).toBe(0.15)
  })

  test('sameProfileValues 只看数值——A′ 回到 A 的判据', () => {
    expect(sameProfileValues(PRODUCTION_PROFILE, { id: "A'", threshold: 0.2, score: 2 })).toBe(true)
    expect(sameProfileValues(PRODUCTION_PROFILE, { id: 'B', threshold: 0.15, score: 2 })).toBe(false)
  })
})

describe('② 实际传给原生的值可回读', () => {
  beforeEach(() => {
    nativeLoads.length = 0
  })

  test('不传 profile ⇒ 生产默认；回读到的就是原生收到的那一组', async () => {
    const e = new KwsEngine()
    await e.start(cb)
    try {
      expect(nativeLoads).toHaveLength(1)
      expect(nativeLoads[0].threshold).toBe(DEFAULT_THRESHOLD)
      expect(nativeLoads[0].score).toBe(DEFAULT_SCORE)
      expect(e.appliedProfile()).toMatchObject({ id: PRODUCTION_PROFILE.id, threshold: DEFAULT_THRESHOLD })
    } finally {
      await e.stop()
    }
  })

  test('传实验档 ⇒ 原生收到实验值，回读也是实验值', async () => {
    const e = new KwsEngine()
    await e.start(cb, undefined, { id: 'B-thr015', threshold: 0.15, score: 2 })
    try {
      expect(nativeLoads[0].threshold).toBe(0.15)
      expect(e.appliedProfile()?.id).toBe('B-thr015')
    } finally {
      await e.stop()
    }
  })

  test('越界档**根本不会走到原生**（先抛错，不留一个半启动的引擎）', async () => {
    const e = new KwsEngine()
    await expect(e.start(cb, undefined, { id: 'bad', threshold: 5, score: 2 })).rejects.toThrow(KwsProfileError)
    expect(nativeLoads).toHaveLength(0)
    expect(e.appliedProfile()).toBeNull()
    expect(e.active).toBe(false)
  })

  test('stop 之后回读清空——「上一次实验的档」不许留在屏上冒充当前值', async () => {
    const e = new KwsEngine()
    await e.start(cb, undefined, { id: 'C-thr025', threshold: 0.25, score: 2 })
    expect(e.appliedProfile()).not.toBeNull()
    await e.stop()
    expect(e.appliedProfile()).toBeNull()
  })
})

describe('③ 计数分栏', () => {
  beforeEach(() => {
    resetKwsTrialsForTest()
    jest.useFakeTimers()
    // 单调时钟按假时钟走，观察窗才可控
    jest.spyOn(performance, 'now').mockImplementation(() => Date.now())
    jest.setSystemTime(new Date('2026-09-10T10:00:00Z'))
  })
  afterEach(() => {
    jest.restoreAllMocks()
    jest.useRealTimers()
  })

  test('没开实验时全部空转（生产路径零开销、零常态日志）', () => {
    expect(kwsTrialActive()).toBe(false)
    noteKeywordHit()
    noteListeningEntered()
    expect(kwsTrials()).toHaveLength(0)
  })

  test('命中 → 观察窗内进 LISTENING = 成功；窗外 = 命中但没进', () => {
    startKwsTrial('A', { id: 'A-default', threshold: 0.2, score: 2, keywords: 'kw' })
    noteKeywordHit()
    jest.advanceTimersByTime(500)
    noteListeningEntered() // 窗内

    jest.advanceTimersByTime(10_000)
    noteKeywordHit()
    jest.advanceTimersByTime(OBSERVATION_WINDOW_MS + 200)
    noteListeningEntered() // 窗外 ⇒ 不认领上一条，算「无命中的进入」
    endKwsTrial()

    const s = summarizeKwsTrial(kwsTrials()[0])
    expect(s.distinctHits).toBe(2)
    expect(s.enteredListening).toBe(1)
    expect(s.hitWithoutListening).toBe(1)
    expect(s.listeningWithoutHit).toBe(1)
  })

  test('观察窗内的第二次命中算重复触发，不重复计入分母', () => {
    startKwsTrial('A', null)
    noteKeywordHit()
    jest.advanceTimersByTime(300)
    noteKeywordHit() // 重复
    jest.advanceTimersByTime(200)
    noteListeningEntered()
    endKwsTrial()

    const s = summarizeKwsTrial(kwsTrials()[0])
    expect(s.keywordHits).toBe(2)
    expect(s.duplicateHits).toBe(1)
    expect(s.distinctHits).toBe(1) // 分母是独立尝试，不是命中次数
    expect(s.enteredListening).toBe(1)
  })

  test('followup 窗那种「没有命中的 LISTENING」不进唤醒分子', () => {
    startKwsTrial('A', null)
    noteListeningEntered()
    noteListeningEntered()
    endKwsTrial()
    const s = summarizeKwsTrial(kwsTrials()[0])
    expect(s.distinctHits).toBe(0)
    expect(s.enteredListening).toBe(0)
    expect(s.listeningWithoutHit).toBe(2)
  })

  test('原生丢帧/处理帧按增量算，队列峰值留最大值', () => {
    startKwsTrial('A', null, { loaded: true, queued: 1, dropped: 10, processed: 100 })
    noteKwsStats({ loaded: true, queued: 7, dropped: 12, processed: 400 })
    noteKwsStats({ loaded: true, queued: 3, dropped: 15, processed: 900 })
    endKwsTrial()
    const s = summarizeKwsTrial(kwsTrials()[0])
    expect(s.droppedDelta).toBe(5)
    expect(s.processedDelta).toBe(800)
    expect(s.queuedPeak).toBe(7)
  })

  test('没喂过原生统计 ⇒ 增量是 null，不是 0', () => {
    startKwsTrial('A', null)
    endKwsTrial()
    const s = summarizeKwsTrial(kwsTrials()[0])
    expect(s.droppedDelta).toBeNull()
    expect(s.processedDelta).toBeNull()
  })

  test('明细有界，丢弃数可见', () => {
    startKwsTrial('A', null)
    for (let i = 0; i < TRIAL_EVENT_CAP + 6; i += 1) {
      noteKeywordHit()
      jest.advanceTimersByTime(OBSERVATION_WINDOW_MS + 10) // 避免全被算成重复
    }
    endKwsTrial()
    expect(kwsTrials()[0].hits).toHaveLength(TRIAL_EVENT_CAP)
    expect(kwsTrials()[0].droppedEvents).toBe(6)
  })

  test('摘要一行里带 profile 与四种结果，且**不报唤醒率**（真分母是「说了几次」，机器数不出来）', () => {
    startKwsTrial('B 组', { id: 'B-thr015', threshold: 0.15, score: 2, keywords: 'kw' })
    noteKeywordHit()
    jest.advanceTimersByTime(300)
    noteListeningEntered()
    endKwsTrial()
    const line = formatKwsTrial(summarizeKwsTrial(kwsTrials()[0]))
    expect(line).toContain('B-thr015')
    expect(line).toContain('thr=0.15')
    expect(line).toContain('独立命中=1')
    expect(line).toContain('命中未进入=0')
    expect(line).toContain('观察窗=2000ms')
    expect(line).not.toMatch(/唤醒率|成功率/)
  })

  test('没回读到 profile 时明写 NOT_READ_BACK，不填一个默认值冒充', () => {
    startKwsTrial('未知档', null)
    endKwsTrial()
    expect(formatKwsTrial(summarizeKwsTrial(kwsTrials()[0]))).toContain('NOT_READ_BACK')
  })
})
