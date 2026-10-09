import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { fetchTurnDetail, searchTurns } from '../api'
import { LiveView } from './LiveView'
import type { LlmCall, LogEntry, Span, TurnDetail } from '../types'

const samples = vi.hoisted(() => ({ details: [] as (TurnDetail | null)[], evidence: [] as string[][] }))
vi.mock('../api', () => ({ fetchTurnDetail: vi.fn(), searchTurns: vi.fn() }))
vi.mock('../components/AgentList', () => ({ AgentList: () => null }))
vi.mock('../components/live/SimEnvPanel', () => ({ SimEnvPanel: () => null }))
vi.mock('../components/VehicleState', () => ({ VehicleState: ({ changed, changedTraceId }: { changed: Set<string>; changedTraceId?: string }) =>
  <div data-testid="changed" data-trace={changedTraceId}>{[...changed].join(',')}</div> }))
vi.mock('../components/live/CommandConsole', () => ({ fixtureCommandState: () => 'idle', fixtureCommandTrace: () => '',
  CommandConsole: ({ onTrace, changes }: { onTrace: (id: string) => void; changes: { key: string }[] }) => {
    samples.evidence.push(changes.map(change => change.key))
    return <><button onClick={() => onTrace('trace-a')}>发送 A</button><button onClick={() => onTrace('trace-b')}>发送 B</button></>
  },
}))
vi.mock('../components/live/LiveTrace', async original => {
  const actual = await original<typeof import('../components/live/LiveTrace')>()
  return { ...actual, LiveTrace: ({ detail }: { detail: TurnDetail | null }) => {
    samples.details.push(detail)
    return <pre data-testid="detail">{JSON.stringify(detail)}</pre>
  } }
})

function turn(trace = 'trace-a'): TurnDetail {
  return { turn: { trace_id: trace, session_id: 'session', ts: 1000, duration_ms: 500,
    user_text: trace, speech: 'final ' + trace, status: 'ok', path: 'local', input_source: 'text', is_confirmation: false,
    ui_card_type: '', actions: 1, error: '', badcase: 0, note: '' }, spans: [], llm_calls: [], logs: [] }
}
const effect = (trace = 'trace-a', id = 'val-1', key = 'window'): Span => ({ trace_id: trace, span_id: id, node: 'val.execute',
  ts: 1400, duration_ms: 5, status: 'ok', service: 'edge', attrs: { changes: [{ key, old: 0, new: 70 }] } })
const log = (trace: string, id: number): LogEntry => ({ trace_id: trace, id, ts: 1500 + id, msg: 'log ' + id,
  session_id: 'session', service: 'edge', level: 'WARNING', logger: 'test' })
const llm = (trace: string, id: number): LlmCall => ({ trace_id: trace, id, ts: 1300 + id, caller: 'planner', model: 'model-' + id,
  prompt_tokens: 1, completion_tokens: 1, latency_ms: 10, cache_hit: false, thinking: false, status: 'ok', error: '', prompt_tail: '', content_head: '' })
const props = { vehicle: { window: 0 }, changed: new Set(['window']), traces: [], agents: {} }
const read = () => JSON.parse(screen.getByTestId('detail').textContent || 'null') as TurnDetail | null
async function send(name = '发送 A') { await act(async () => { fireEvent.click(screen.getByRole('button', { name })) }) }
async function advance(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }

beforeEach(() => {
  vi.useFakeTimers(); vi.clearAllMocks(); samples.details.length = 0; samples.evidence.length = 0
  vi.mocked(searchTurns).mockResolvedValue([])
  vi.mocked(fetchTurnDetail).mockImplementation(async trace => turn(trace))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

test('only new current-trace VAL evidence highlights vehicle keys, for 2.5 seconds per event', async () => {
  const { rerender } = render(<LiveView {...props} />)
  expect(screen.getByTestId('changed').textContent).toBe('')
  await send()
  const wrongNode = { ...effect('trace-a', 'other'), node: 'cloud.outcome' }
  rerender(<LiveView {...props} traces={[{ trace_id: 'trace-b', spans: [effect('trace-b')] }, { trace_id: 'trace-a', spans: [wrongNode] }]} />)
  expect(screen.getByTestId('changed').textContent).toBe('')
  const valid = effect()
  rerender(<LiveView {...props} traces={[{ trace_id: 'trace-a', spans: [wrongNode, valid] }]} />)
  expect(screen.getByTestId('changed').textContent).toBe('window')
  await advance(2400)
  expect(screen.getByTestId('changed').textContent).toBe('window')
  await advance(101)
  expect(screen.getByTestId('changed').textContent).toBe('')
  rerender(<LiveView {...props} traces={[{ trace_id: 'trace-a', spans: [{ ...valid }] }]} />)
  expect(screen.getByTestId('changed').textContent).toBe('')
  rerender(<LiveView {...props} traces={[{ trace_id: 'trace-a', spans: [valid, effect('trace-a', 'val-2')] }]} />)
  expect(screen.getByTestId('changed').textContent).toBe('window')
})

test('switching traces excludes old evidence from every render and ignores a late old response', async () => {
  const oldRefresh = deferred<TurnDetail>(), next = deferred<TurnDetail>()
  const original = turn(); original.spans = [effect()]
  vi.mocked(fetchTurnDetail).mockResolvedValueOnce(original).mockImplementationOnce(() => oldRefresh.promise).mockImplementationOnce(() => next.promise)
  const { rerender } = render(<LiveView {...props} />)
  await send()
  expect(read()?.turn?.trace_id).toBe('trace-a')
  rerender(<LiveView {...props} liveLogs={[log('trace-a', 1)]} />)
  await advance(180)
  const from = samples.details.length, evidenceFrom = samples.evidence.length
  await send('发送 B')
  expect(samples.details.slice(from).every(detail => !detail?.turn && !detail?.spans.length)).toBe(true)
  expect(samples.evidence.slice(evidenceFrom).every(keys => !keys.includes('window'))).toBe(true)
  expect(screen.getByTestId('changed').textContent).toBe('')
  await act(async () => oldRefresh.resolve(original))
  expect(read()?.turn).toBeNull()
  const current = turn('trace-b'); current.spans = [effect(), effect('trace-b', 'b', 'seat_heating')]
  await act(async () => next.resolve(current))
  expect(read()?.turn?.trace_id).toBe('trace-b')
  expect(read()?.spans.map(span => span.trace_id)).toEqual(['trace-b'])
  expect(screen.getByTestId('changed').textContent).toBe('seat_heating')
})

test('late current-trace logs and LLM events refresh a final snapshot without emptying it', async () => {
  const { rerender } = render(<LiveView {...props} />)
  await send()
  expect(read()?.turn?.status).toBe('ok')
  const currentLog = log('trace-a', 1), otherLog = log('trace-b', 2)
  const updated = turn(); updated.logs = [currentLog]
  vi.mocked(fetchTurnDetail).mockResolvedValue(updated)
  const from = samples.details.length
  rerender(<LiveView {...props} lastTurn={turn('trace-b').turn} liveLogs={[currentLog, otherLog]} />)
  expect(read()?.turn?.status).toBe('ok')
  await advance(180)
  expect(read()?.logs).toHaveLength(1)
  expect(samples.details.slice(from).every(detail => detail?.turn?.status === 'ok')).toBe(true)
  const count = vi.mocked(fetchTurnDetail).mock.calls.length
  rerender(<LiveView {...props} lastTurn={turn('trace-b').turn} liveLogs={[currentLog, otherLog, log('trace-b', 3)]} />)
  await advance(180)
  expect(fetchTurnDetail).toHaveBeenCalledTimes(count)
  const currentCall = llm('trace-a', 1)
  vi.mocked(fetchTurnDetail).mockResolvedValue({ ...updated, llm_calls: [currentCall] })
  rerender(<LiveView {...props} liveLogs={[currentLog, otherLog]} liveLlmCalls={[currentCall, llm('trace-b', 2)]} />)
  await advance(180)
  expect(read()?.llm_calls[0].model).toBe('model-1')
})

test('unmount clears VAL highlight and arrival timers without starting another read', async () => {
  const current = turn(); current.spans = [effect()]
  vi.mocked(fetchTurnDetail).mockResolvedValue(current)
  const { rerender, unmount } = render(<LiveView {...props} />)
  await send()
  rerender(<LiveView {...props} liveLogs={[log('trace-a', 1)]} />)
  const count = vi.mocked(fetchTurnDetail).mock.calls.length
  unmount()
  await advance(5000)
  expect(fetchTurnDetail).toHaveBeenCalledTimes(count)
  expect(vi.getTimerCount()).toBe(0)
})
