import { describe, expect, it } from 'vitest'
import { LANES, laneOf, markerOf } from '../../laneOf'
import type { LlmCall, LogEntry, Span } from '../../types'
import { axisTicks, coveredDuration, formatRelative, getTimelineRange, logTone, projectTimeline, selectRange } from './model'

const span = (node: string, ts: number, duration_ms = 0): Span => ({
  trace_id: 'turn', span_id: node, node, ts, duration_ms, status: 'ok', service: 'test', attrs: {},
})
const call = (ts: number, latency_ms: number): LlmCall => ({
  trace_id: 'turn', ts, latency_ms, caller: 'planner', model: 'test', prompt_tokens: 0, completion_tokens: 0,
  cache_hit: false, thinking: false, status: 'ok', error: '', prompt_tail: '', content_head: '',
})
const log = (ts: number): LogEntry => ({ trace_id: 'turn', session_id: 'test', ts, service: 'test', level: 'WARN', logger: '', msg: 'example' })

describe('appendix D lane source', () => {
  it.each([
    ['route.cloud', 'edge'], ['route.local', 'edge'], ['route.mixed', 'edge'], ['nlu.shadow', 'edge'],
    ['noop.message', 'edge'], ['step.edge:vehicle', 'edge'], ['val.execute', 'val'],
    ['cloud.planning', 'llm'], ['cloud.planning.other', 'cloud'], ['step.agent:navigation', 'agent'],
    ['agent_client.http', 'agent'], ['provider.amap.place', 'external'], ['step.tool:call', 'external'],
    ['payment.intent', 'external'], ['cloud.outcome', 'cloud'], ['t2.run', 'cloud'],
    ['step.dedup', 'cloud'], ['step.verify', 'cloud'], ['aggregate', 'cloud'], ['suspended', 'cloud'],
    ['escalate', 'cloud'], ['system.note', 'cloud'], ['decision.shadow', 'cloud'],
    ['asr.stream', 'voice'], ['s2s.turn', 'voice'], ['unregistered.node', 'other'],
  ])('%s belongs to %s', (node, expected) => expect(laneOf(node)).toBe(expected))

  it('keeps a fixed lane order and handles LLM/log records without fake node names', () => {
    expect(LANES.map(lane => lane.id)).toEqual(['edge', 'val', 'llm', 'agent', 'external', 'cloud', 'voice', 'other', 'logs'])
    expect(laneOf('', 'llm')).toBe('llm')
    expect(laneOf('', 'log')).toBe('logs')
    expect(markerOf('nlu.shadow')).toBe('shadow')
    expect(markerOf('decision.shadow')).toBe('shadow')
    expect(markerOf('cloud.outcome')).toBe('outcome')
    expect(markerOf('route.mixed')).toBe('event')
  })
})

it('preserves negative starts and late events while aligning spans, calls and logs to turn.ts', () => {
  const data = projectTimeline({ turn: { ts: 1000, duration_ms: 200 },
    spans: [span('val.execute', 1010, 25), span('nlu.shadow', 1450), span('llm.call.meta', 9000)],
    llmCalls: [call(1100, 80)], logs: [log(1490)],
  })
  expect(data.range).toEqual({ start: -15, end: 490 })
  expect(data.items.map(item => [item.kind, item.start, item.end])).toEqual([
    ['span', -15, 10], ['llm', 20, 100], ['span', 450, 450], ['log', 490, 490],
  ])
  expect(data.turnDuration).toBe(200)
  expect(formatRelative(-15)).toBe('−15 ms')
})

it('without a turn only measures the observed window, never supplies a complete request duration', () => {
  const data = projectTimeline({ spans: [span('step.agent:a', 4200, 400), span('route.local', 3000)] })
  expect(data.hasTurn).toBe(false)
  expect(data.baseTs).toBe(3000)
  expect(data.turnDuration).toBeNull()
  expect(data.range).toEqual({ start: 0, end: 1200 })
})

it('comparison uses a shared relative range without modifying event timestamps', () => {
  const first = { turn: { ts: 1000, duration_ms: 500 }, spans: [span('val.execute', 1200, 100)] }
  const second = { turn: { ts: 900000, duration_ms: 800 }, spans: [span('val.execute', 900200, 100)] }
  expect(getTimelineRange(first)).toEqual({ start: 0, end: 500 })
  expect(getTimelineRange(second)).toEqual({ start: 0, end: 800 })
  const shared = { start: 0, end: 800 }
  expect(selectRange(shared, getTimelineRange(first))).toBe(shared)
  expect(projectTimeline(first).items[0].start).toBe(projectTimeline(second).items[0].start)
})

it('live now extends only the axis, without making a running span', () => {
  const data = projectTimeline({ turn: { ts: 1000, duration_ms: 0 },
    spans: [span('route.cloud', 1010)], live: true, now: 2400,
  })
  expect(data.range).toEqual({ start: 0, end: 1400 })
  expect(data.items).toHaveLength(1)
  expect(data.items[0]).toMatchObject({ duration: 0, start: 10, end: 10 })
  expect(data.nowOffset).toBe(1400)
})

it('now is independent of a future-dated observation and includes clocks preceding the base', () => {
  const input = { turn: { ts: 1000, duration_ms: 0 }, spans: [span('route.cloud', 4000)], live: true }
  const future = projectTimeline({ ...input, now: 1500 })
  expect(future.nowOffset).toBe(500)
  expect(future.range.end).toBe(3000)
  const before = projectTimeline({ ...input, now: 900 })
  expect(before.nowOffset).toBe(-100)
  expect(before.range).toEqual({ start: -100, end: 3000 })
  expect(projectTimeline({ ...input, live: false, now: 1500 }).nowOffset).toBeNull()
})

it('coverage unions nested intervals and excludes the parts outside a known turn', () => {
  const data = projectTimeline({ turn: { ts: 1000, duration_ms: 500 }, spans: [
    span('step.agent:a', 1200, 300), span('aggregate', 1600, 250),
  ], llmCalls: [call(1150, 100)], logs: [log(1700)] })
  expect(coveredDuration(data.items, 500)).toBe(350)
})

it('unknown timestamps and negative durations remain unknown; duplicate record IDs remain selectable', () => {
  const data = projectTimeline({ turn: { ts: 1000, duration_ms: 200 }, spans: [
    span('route.local', Number.NaN), span('val.execute', 1050, -5), span('val.execute', 1050, 4),
  ] })
  expect(data.items.find(item => item.label === 'route.local')).toMatchObject({ start: null, end: null })
  expect(data.items.find(item => item.label === 'val.execute' && item.duration === null)).toBeTruthy()
  expect(new Set(data.items.map(item => item.id)).size).toBe(3)
  expect(data.range).toEqual({ start: 0, end: 200 })
  expect(formatRelative(null)).toBe('—')
})

it.each(['WARN', 'WARNING', 'warn'])('%s is a warning tick', level => expect(logTone(level)).toBe('warn'))
it.each(['ERROR', 'CRITICAL', 'FATAL', 'error'])('%s is a critical tick', level => expect(logTone(level)).toBe('critical'))

it('ticks remain finite and ordered for sub-millisecond, negative and long ranges', () => {
  for (const range of [{ start: 0, end: 2.8 }, { start: -10, end: 100 }, { start: 0, end: 180000 }]) {
    const ticks = axisTicks(range)
    expect(ticks[0]).toBe(range.start)
    expect(ticks.at(-1)).toBe(range.end)
    expect(ticks.every(Number.isFinite)).toBe(true)
    expect(ticks).toEqual([...ticks].sort((a, b) => a - b))
  }
})
