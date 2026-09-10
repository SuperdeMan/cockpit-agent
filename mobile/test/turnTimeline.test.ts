// 轮次时间线的口径（AR08 / A08-1）。
//
// 这批断言守的不是「能不能记事件」，而是**四条会让报告说谎的边界**：
//  ① 测不到就是 null，绝不是 0（0 会被平均进去变成「非常快」）；
//  ② 跨时钟域不许相减（AudioContext 的 currentTime 和 JS 单调时钟不是同一根尺子）；
//  ③ 排定起播（play_scheduled）永远不能自己升级成声学首音（audible_onset）；
//  ④ 语音轮的交接口漏了一次，下一条文字请求不能认领到它。
import {
  MARKS_CAP,
  TIMELINE_CAP,
  attachMeasuredOnset,
  beginInteraction,
  dropPendingInteraction,
  durationOf,
  findByBubble,
  interactionTimelines,
  linkInteraction,
  markInteraction,
  offerPendingInteraction,
  resetTimelinesForTest,
  takePendingInteraction,
  timelineDroppedTurns,
  timelineMetrics,
} from '@/core/obs/turnTimeline'

beforeEach(() => resetTimelinesForTest())

describe('时长口径', () => {
  test('缺任一端 ⇒ null，不是 0', () => {
    const id = beginInteraction('ptt')
    markInteraction(id, 'endpoint_detected', { at: 1000 })
    const t = interactionTimelines()[0]
    expect(durationOf(t, 'endpoint_detected', 'play_scheduled')).toBeNull()
    expect(durationOf(t, 'asr_final', 'request_sent')).toBeNull()
  })

  test('同域两点相减；顺序取「起点第一次、终点最后一次」', () => {
    const id = beginInteraction('ptt')
    markInteraction(id, 'tts_text_sent', { at: 100 })
    markInteraction(id, 'first_pcm_received', { at: 350 })
    markInteraction(id, 'first_pcm_received', { at: 900 }) // 第二段
    const t = interactionTimelines()[0]
    expect(durationOf(t, 'tts_text_sent', 'first_pcm_received')).toBe(800)
  })

  test('跨时钟域 ⇒ null（不是一个看着正常的数）', () => {
    const id = beginInteraction('ptt')
    markInteraction(id, 'endpoint_detected', { at: 1000, domain: 'client_mono' })
    markInteraction(id, 'audible_onset', { at: 12, domain: 'external' })
    const t = interactionTimelines()[0]
    expect(durationOf(t, 'endpoint_detected', 'audible_onset')).toBeNull()
  })
})

describe('首音来源分档', () => {
  test('只有排定回调时，主指标是 null 且来源仍是 scheduled_proxy', () => {
    const id = beginInteraction('ptt')
    markInteraction(id, 'endpoint_detected', { at: 0 })
    markInteraction(id, 'play_scheduled', { at: 2400 })
    const m = timelineMetrics(interactionTimelines()[0])
    expect(m.firstAudioSource).toBe('scheduled_proxy')
    // 代理指标有数
    expect(m.utteranceEndToScheduled).toBe(2400)
    // 主指标（说完 → 真的听见）**没有**，不许拿代理值顶替
    expect(m.utteranceEndToAudible).toBeNull()
  })

  test('外部测得的声学首音才抬得动来源', () => {
    const id = beginInteraction('ptt')
    markInteraction(id, 'play_scheduled', { at: 2400 })
    attachMeasuredOnset(id, 2600, 'acoustic', 'external-mic')
    const m = timelineMetrics(interactionTimelines()[0])
    expect(m.firstAudioSource).toBe('acoustic')
  })
})

describe('身份与检索', () => {
  test('linkInteraction 只补不覆盖；ttsSessionIds 追加', () => {
    const id = beginInteraction('text')
    linkInteraction(id, { requestId: 'req-1', bubbleId: 'b1', ttsSessionIds: ['s1'] })
    linkInteraction(id, { requestId: 'req-2', traceId: 'tr', ttsSessionIds: ['s2'] })
    const t = interactionTimelines()[0]
    expect(t.ids.requestId).toBe('req-1') // 同一轮的 request_id 不许被改写
    expect(t.ids.traceId).toBe('tr')
    expect(t.ids.ttsSessionIds).toEqual(['s1', 's2'])
  })

  test('findByBubble 取最新一条', () => {
    const a = beginInteraction('text')
    linkInteraction(a, { bubbleId: 'dup' })
    const b = beginInteraction('text')
    linkInteraction(b, { bubbleId: 'dup' })
    expect(findByBubble('dup')?.interactionId).toBe(b)
  })
})

describe('有界', () => {
  test('超过 TIMELINE_CAP 丢最旧，丢弃数可见', () => {
    for (let i = 0; i < TIMELINE_CAP + 3; i += 1) beginInteraction('text')
    expect(interactionTimelines()).toHaveLength(TIMELINE_CAP)
    expect(timelineDroppedTurns()).toBe(3)
  })

  test('超过 MARKS_CAP 的事件计进本轮 dropped', () => {
    const id = beginInteraction('text')
    for (let i = 0; i < MARKS_CAP + 5; i += 1) markInteraction(id, 'first_pcm_received')
    const t = interactionTimelines()[0]
    expect(t.marks).toHaveLength(MARKS_CAP)
    expect(t.dropped).toBe(5)
  })
})

describe('语音轮交接口', () => {
  test('认领一次就没了——第二次拿到 null', () => {
    const id = beginInteraction('ptt')
    offerPendingInteraction(id)
    expect(takePendingInteraction()).toBe(id)
    expect(takePendingInteraction()).toBeNull()
  })

  test('撤回之后不再被认领（说了半句没发出去的那种）', () => {
    const id = beginInteraction('ptt')
    offerPendingInteraction(id)
    dropPendingInteraction(id)
    expect(takePendingInteraction()).toBeNull()
  })

  test('过期的交接口不许被下一条请求认领', () => {
    const id = beginInteraction('ptt')
    offerPendingInteraction(id)
    // 单调时钟往前推 31s：turnTimeline 用 performance.now()（jest 环境里有）
    const realNow = performance.now.bind(performance)
    const base = realNow()
    jest.spyOn(performance, 'now').mockImplementation(() => base + 31_000)
    try {
      expect(takePendingInteraction()).toBeNull()
    } finally {
      jest.restoreAllMocks()
    }
  })
})

describe('终态', () => {
  test.each([
    ['play_ended', 'play_ended'],
    ['stopped', 'stopped'],
    ['cancelled', 'cancelled'],
    ['failed', 'failed'],
  ] as const)('%s ⇒ terminal=%s', (event, expected) => {
    const id = beginInteraction('text')
    markInteraction(id, event)
    expect(timelineMetrics(interactionTimelines()[0]).terminal).toBe(expected)
  })

  test('还没收尾就是 open，不是「成功」', () => {
    const id = beginInteraction('text')
    markInteraction(id, 'request_sent')
    expect(timelineMetrics(interactionTimelines()[0]).terminal).toBe('open')
  })
})
