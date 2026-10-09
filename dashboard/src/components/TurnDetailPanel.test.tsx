import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ApiError, fetchExport, fetchIntentOptions, fetchSessionTurns, fetchTurnDetail, markBadcase, saveLabel } from '../api'
import { replayText } from './CommandBar'
import { TurnDetailPanel } from './TurnDetailPanel'
import { foldTurnLogs } from './inspector/model'
import type { TurnDetail } from '../types'

vi.mock('../api', async original => {
  const actual = await original<typeof import('../api')>()
  return { ...actual, fetchExport: vi.fn(), fetchIntentOptions: vi.fn(), fetchSessionTurns: vi.fn(),
    fetchTurnDetail: vi.fn(), markBadcase: vi.fn(), saveLabel: vi.fn() }
})
vi.mock('./CommandBar', () => ({ genTraceId: () => 'nonce', replayText: vi.fn(() => 'replay-trace') }))

const detail: TurnDetail = {
  turn: { trace_id: 'trace123456789', session_id: 's1', ts: 1720000000000, duration_ms: 1534,
    user_text: '导航去机场', speech: '已为您规划路线', status: 'ok', path: 'cloud', input_source: 'voice_wake',
    is_confirmation: 0, ui_card_type: 'route_plan', actions: 1, error: '', badcase: 0, note: '',
    intents: 'navigation.navigate_to', plan_mode: 'toolcall', gold_intents: '', outcome: 'completed' },
  spans: [{ trace_id: 'trace123456789', span_id: 'p1', ts: 1720000000500, service: 'cloud', node: 'cloud.planning',
    status: 'ok', duration_ms: 800, attrs: { plan: '[{"id":"s1","agent":"navigation","intent":"navigation.navigate_to","slots":{"destination":"机场"},"depends_on":[]}]', llm_raw: '{"steps":[]}' } }],
  llm_calls: [{ trace_id: 'trace123456789', ts: 1720000000400, caller: 'cloud-planner', model: 'mimo-v2.5',
    prompt_tokens: 900, completion_tokens: 120, latency_ms: 750, cache_hit: 0, thinking: 0, status: 'ok',
    error: '', prompt_tail: '用户说: 导航去机场', content_head: '{"steps":[]}' }],
  logs: [{ id: 1, ts: 1720000000600, service: 'cloud-planner', level: 'INFO', logger: 'planner.engine',
    msg: 'Plan ready', trace_id: 'trace123456789', session_id: 's1' }],
}
const cloned = () => structuredClone(detail)
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTurnDetail).mockResolvedValue(cloned())
  vi.mocked(fetchSessionTurns).mockResolvedValue([detail.turn!])
  vi.mocked(fetchIntentOptions).mockResolvedValue(['navigation.navigate_to', 'nearby.search'])
  vi.mocked(fetchExport).mockResolvedValue({ ...detail, exported: true })
  vi.mocked(markBadcase).mockResolvedValue(true)
  vi.mocked(saveLabel).mockResolvedValue(true)
  vi.mocked(replayText).mockReturnValue('replay-trace')
})
afterEach(() => { cleanup(); vi.useRealTimers() })

test('inspector separates its five tabs and shows server evidence without claiming pipeline success', async () => {
  render(<TurnDetailPanel traceId="trace123456789" />)
  await screen.findByRole('heading', { name: '导航去机场' })
  expect(screen.getByText('已为您规划路线')).toBeTruthy()
  expect(screen.getByText('route_plan')).toBeTruthy()
  expect(screen.getByText('已完成')).toBeTruthy()
  expect(screen.queryByText('成功')).toBeNull()
  expect(screen.queryByText('mimo-v2.5')).toBeNull()
  fireEvent.click(screen.getByRole('tab', { name: '规划' }))
  expect(screen.getByRole('table', { name: '规划步骤' })).toBeTruthy()
  expect(within(screen.getByRole('table')).getByText('navigation.navigate_to')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: 'LLM 1' }))
  fireEvent.click(screen.getByRole('button', { name: /cloud-planner.*mimo-v2.5/ }))
  expect(screen.getByText('用户说: 导航去机场')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: '日志 1' }))
  expect(screen.getByText('Plan ready')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: '原始 JSON' }))
  await waitFor(() => expect(screen.getByText(/"exported": true/)).toBeTruthy())
})

test('D11 folds alternating per-turn duplicates by service + level + complete message', async () => {
  const value = cloned()
  const base = value.logs[0]
  value.logs = ['A', 'B', 'A', 'B', 'A', 'B'].map((msg, index) => ({ ...base, id: index + 1,
    ts: base.ts + index * 100, level: 'WARNING', msg }))
  vi.mocked(fetchTurnDetail).mockResolvedValue(value)
  const folded = foldTurnLogs(value.logs)
  expect(folded).toHaveLength(2)
  expect(folded.map(row => [row.log.msg, row.count, row.log.ts])).toEqual([['A', 3, base.ts], ['B', 3, base.ts + 100]])
  expect(foldTurnLogs([...value.logs, { ...base, msg: 'A', service: 'another' }, { ...base, msg: 'A', level: 'ERROR' }])).toHaveLength(4)
  render(<TurnDetailPanel traceId="trace123456789" />)
  await screen.findByRole('heading', { name: '导航去机场' })
  fireEvent.click(screen.getByRole('tab', { name: '日志 6' }))
  expect(document.querySelectorAll('.inspector-log')).toHaveLength(2)
  expect(screen.getAllByText(/×3 · 首次/)).toHaveLength(2)
  expect(screen.getByText(/本轮 6 条.*合并为 2 行/)).toBeTruthy()
})

test('D12 replay requires confirmation and then uses an independent replay session exactly once', async () => {
  const replay = cloned()
  replay.turn = { ...replay.turn!, trace_id: 'replay-trace', session_id: 'replay-result' }
  vi.mocked(fetchTurnDetail).mockImplementation(async trace => trace === 'replay-trace' ? replay : cloned())
  render(<TurnDetailPanel traceId="trace123456789" />)
  await screen.findByRole('heading', { name: '导航去机场' })
  const opener = screen.getByRole('button', { name: '重放对照' })
  opener.focus(); fireEvent.click(opener)
  const dialog = screen.getByRole('dialog', { name: '重放这一轮？' })
  expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: '取消' }))
  expect(replayText).not.toHaveBeenCalled()
  fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
  expect(replayText).not.toHaveBeenCalled()
  fireEvent.click(opener)
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '重放' }))
  expect(replayText).toHaveBeenCalledOnce()
  expect(vi.mocked(replayText).mock.calls[0][0]).toBe('导航去机场')
  expect(vi.mocked(replayText).mock.calls[0][1]).toMatch(/^replay-\d+-nonce$/)
  await screen.findByText('观测已到达')
  expect(document.querySelectorAll('.inspector-compare-timeline')).toHaveLength(2)
})

test('badcase save failure keeps its draft open and does not change the displayed record', async () => {
  const changed = vi.fn()
  vi.mocked(markBadcase).mockResolvedValueOnce(false)
  render(<TurnDetailPanel traceId="trace123456789" onChanged={changed} />)
  await screen.findByRole('heading', { name: '导航去机场' })
  fireEvent.click(screen.getByRole('button', { name: '标记 badcase' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'badcase 备注' }), { target: { value: '路线不正确' } })
  fireEvent.click(within(screen.getByRole('dialog', { name: '标记 badcase' })).getByRole('button', { name: '保存' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('textbox', { name: 'badcase 备注' })).toHaveProperty('value', '路线不正确')
  expect(screen.queryByText('badcase 已保存')).toBeNull()
  expect(changed).not.toHaveBeenCalled()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '保存' }))
  await screen.findByText('badcase 已保存')
  expect(markBadcase).toHaveBeenLastCalledWith('trace123456789', true, '路线不正确')
  expect(changed).toHaveBeenCalledOnce()
})

test('multi-select annotation saves observed choices on Enter and keeps failures visible', async () => {
  vi.mocked(saveLabel).mockRejectedValueOnce(new Error('标注未保存：HTTP 500'))
  render(<TurnDetailPanel traceId="trace123456789" />)
  await screen.findByRole('heading', { name: '导航去机场' })
  fireEvent.click(screen.getByRole('button', { name: '标注落域' }))
  fireEvent.click(await screen.findByRole('checkbox', { name: 'nearby.search' }))
  fireEvent.keyDown(screen.getByRole('textbox', { name: '搜索标注意图' }), { key: 'Enter' })
  await screen.findByText('标注未保存：HTTP 500')
  expect(screen.queryByText('落域标注已保存')).toBeNull()
  expect(screen.getByRole('checkbox', { name: 'nearby.search' })).toHaveProperty('checked', true)
  fireEvent.keyDown(screen.getByRole('textbox', { name: '搜索标注意图' }), { key: 'Enter' })
  await screen.findByText('落域标注已保存')
  expect(saveLabel).toHaveBeenLastCalledWith('trace123456789', 'nearby.search')
})

test('manual intent annotation stays available when observed candidates fail', async () => {
  vi.mocked(fetchIntentOptions).mockRejectedValue(new Error('候选接口不可用'))
  render(<TurnDetailPanel traceId="trace123456789" />)
  await screen.findByRole('heading', { name: '导航去机场' })
  fireEvent.click(screen.getByRole('button', { name: '标注落域' }))
  await screen.findByText('候选加载失败：候选接口不可用')
  const input = screen.getByRole('textbox', { name: '补充标注意图' })
  fireEvent.change(input, { target: { value: 'new.intent, second.intent,new.intent' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  await screen.findByText('落域标注已保存')
  expect(saveLabel).toHaveBeenLastCalledWith('trace123456789', 'new.intent,second.intent')
})

test('FATAL logs contribute to warning count and retain their severity label', async () => {
  const value = cloned()
  value.logs[0] = { ...value.logs[0], level: 'FATAL' }
  vi.mocked(fetchTurnDetail).mockResolvedValue(value)
  render(<TurnDetailPanel traceId="trace123456789" />)
  await screen.findByRole('heading', { name: '导航去机场' })
  fireEvent.click(screen.getByRole('button', { name: /1 条告警/ }))
  expect(screen.getByText('FATAL').className).toContain('inspector-log__level--critical')
})

test('late detail responses cannot replace the currently selected trace', async () => {
  const first = deferred<TurnDetail>(), second = deferred<TurnDetail>()
  vi.mocked(fetchTurnDetail).mockImplementation(trace => trace === 'first' ? first.promise : second.promise)
  const { rerender } = render(<TurnDetailPanel traceId="first" />)
  rerender(<TurnDetailPanel traceId="second" />)
  const next = cloned()
  next.turn = { ...next.turn!, trace_id: 'second', user_text: '第二轮' }
  await act(async () => second.resolve(next))
  await screen.findByRole('heading', { name: '第二轮' })
  await act(async () => first.resolve(cloned()))
  expect(screen.queryByRole('heading', { name: '导航去机场' })).toBeNull()
  expect(screen.getByRole('heading', { name: '第二轮' })).toBeTruthy()
})

test('unmounting an inspector stops replay polling and ignores its late response', async () => {
  const pending = deferred<TurnDetail>()
  const changed = vi.fn()
  vi.mocked(fetchTurnDetail).mockImplementation(trace => trace === 'replay-trace' ? pending.promise : Promise.resolve(cloned()))
  const { unmount } = render(<TurnDetailPanel traceId="trace123456789" onChanged={changed} />)
  await screen.findByRole('heading', { name: '导航去机场' })
  vi.useFakeTimers()
  fireEvent.click(screen.getByRole('button', { name: '重放对照' }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '重放' }))
  unmount()
  await act(async () => { pending.resolve(cloned()); await vi.advanceTimersByTimeAsync(100_000) })
  expect(fetchTurnDetail).toHaveBeenCalledTimes(2)
  expect(changed).not.toHaveBeenCalled()
})

test('content capture fingerprints are explicit and cannot be replayed as user speech', async () => {
  const hidden = cloned()
  hidden.turn = { ...hidden.turn!, user_text: '<len=12 sha=a1b2c3d4>', speech: '<len=20 sha=f1f2f3f4>' }
  hidden.spans[0].attrs.plan = '<len=300 sha=aabbccdd>'
  vi.mocked(fetchTurnDetail).mockResolvedValue(hidden)
  render(<TurnDetailPanel traceId="trace123456789" />)
  await screen.findByText('内容未采集 · 长度 12 · 指纹 a1b2c3d4')
  expect(screen.getByRole('button', { name: '重放对照' })).toHaveProperty('disabled', true)
  fireEvent.click(screen.getByRole('tab', { name: '规划' }))
  expect(screen.getByText('内容未采集 · 长度 300 · 指纹 aabbccdd')).toBeTruthy()
  expect(replayText).not.toHaveBeenCalled()
})

test('request errors remain distinct from missing traces and retry uses the same trace', async () => {
  vi.mocked(fetchTurnDetail).mockRejectedValueOnce(new ApiError(500, '服务暂时不可用')).mockResolvedValueOnce({ error: 'not found' })
  render(<TurnDetailPanel traceId="missing-trace" />)
  await screen.findByText('轮次加载失败')
  expect(screen.queryByText('没找到这轮')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await screen.findByText('没找到这轮')
  expect(screen.queryByText('已过保留期')).toBeNull()
  expect(fetchTurnDetail).toHaveBeenLastCalledWith('missing-trace')
})

test('same-session navigation uses observed ordering and zero-count tabs remain available', async () => {
  const first = { ...detail.turn!, trace_id: 'earlier', ts: detail.turn!.ts - 1000 }
  const last = { ...detail.turn!, trace_id: 'later', ts: detail.turn!.ts + 1000 }
  const value = cloned(); value.llm_calls = []; value.logs = []
  vi.mocked(fetchTurnDetail).mockResolvedValue(value)
  vi.mocked(fetchSessionTurns).mockResolvedValue([last, detail.turn!, first])
  const open = vi.fn()
  render(<TurnDetailPanel traceId="trace123456789" onOpenTrace={open} />)
  await screen.findByText('同会话 2/3')
  fireEvent.click(screen.getByRole('button', { name: '同会话下一轮' }))
  expect(open).toHaveBeenLastCalledWith('later')
  fireEvent.click(screen.getByRole('tab', { name: 'LLM 0' }))
  expect(screen.getByText('本轮没有 LLM 调用记录')).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: '日志 0' }))
  expect(screen.getByText('本轮没有关联日志')).toBeTruthy()
})
