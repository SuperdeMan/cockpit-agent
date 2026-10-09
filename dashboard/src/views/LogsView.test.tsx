import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { LogsView } from './LogsView'
import { fetchLogs } from '../api'
import type { LogEntry } from '../types'

vi.mock('../api', () => ({ fetchLogs: vi.fn() }))
const fetch = vi.mocked(fetchLogs)
const BASE = 1783912215000
const entry = (msg: string, patch: Partial<LogEntry> = {}): LogEntry => ({
  ts: BASE, service: 'navigation', level: 'INFO', logger: 'nav', msg, trace_id: 'trace-a', session_id: 'session-a', ...patch,
})
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, failed) => { resolve = done; reject = failed })
  return { promise, resolve, reject }
}
const messages = () => [...document.querySelectorAll('.obs-log-message')].map(node => node.textContent)

beforeEach(() => { fetch.mockReset(); fetch.mockResolvedValue([]) })
afterEach(() => { cleanup(); vi.useRealTimers(); history.replaceState(null, '', '/') })

test('global logs keep repeated events and consume every new object in a batched live buffer once', async () => {
  const historical = [entry('重复消息', { id: 1 }), entry('重复消息', { id: 2 })]
  fetch.mockResolvedValue(historical)
  const { rerender } = render(<LogsView liveLogs={[]} />)
  await screen.findByRole('table')
  const first = entry('重复消息', { ts: BASE + 10 })
  const second = { ...first }
  rerender(<LogsView liveLogs={[first, second]} lastLog={second} />)
  await waitFor(() => expect(messages()).toEqual(['重复消息', '重复消息', '重复消息', '重复消息']))
  rerender(<LogsView liveLogs={[first, second]} lastLog={second} />)
  expect(messages()).toHaveLength(4)
  expect(screen.queryByText(/×/)).toBeNull()
})

test('REST overlaps pair one occurrence at a time only within the active request', async () => {
  const pending = deferred<LogEntry[]>()
  fetch.mockReturnValueOnce(pending.promise)
  const prior = entry('挂载前缓冲', { ts: BASE - 1 })
  const { rerender } = render(<LogsView liveLogs={[prior]} />)
  const event = entry('相同事件')
  const another = { ...event }
  rerender(<LogsView liveLogs={[prior, event, another]} />)
  await act(async () => pending.resolve([
    { ...prior, id: 1 }, { ...event, id: 2 }, { ...event, id: 3 },
  ]))
  expect(messages()).toEqual(['挂载前缓冲', '相同事件', '相同事件'])
  const later = { ...event }
  rerender(<LogsView liveLogs={[prior, event, another, later]} />)
  await waitFor(() => expect(messages()).toHaveLength(4))
  expect(messages().filter(msg => msg === '相同事件')).toHaveLength(3)
})

test('scrolling upward pauses without changing the visible rows; resume flushes all buffered events in time order', async () => {
  fetch.mockResolvedValue([entry('原有日志')])
  const { rerender } = render(<LogsView liveLogs={[]} />)
  await screen.findByRole('table')
  const list = screen.getByLabelText('日志列表')
  Object.defineProperties(list, { scrollHeight: { configurable: true, value: 1200 }, clientHeight: { configurable: true, value: 300 } })
  list.scrollTop = 900
  fireEvent.scroll(list)
  list.scrollTop = 200
  fireEvent.scroll(list)
  expect(screen.getByText('已暂停')).toBeTruthy()
  const later = entry('后到的时间早', { ts: BASE + 10 })
  const earlier = entry('先到的时间晚', { ts: BASE + 20 })
  rerender(<LogsView liveLogs={[earlier, later]} />)
  await screen.findByRole('button', { name: '2 条新日志 · 回到最新' })
  expect(messages()).toEqual(['原有日志'])
  expect(list.scrollTop).toBe(200)
  fireEvent.click(screen.getByRole('button', { name: '2 条新日志 · 回到最新' }))
  expect(messages()).toEqual(['原有日志', '后到的时间早', '先到的时间晚'])
  expect(screen.getByText('跟随最新')).toBeTruthy()
  expect(list.scrollTop).toBe(1200)
})

test('q and level keep remote search semantics, and an old response cannot replace the new search', async () => {
  const old = deferred<LogEntry[]>()
  fetch.mockReturnValueOnce(old.promise)
  const { rerender } = render(<LogsView liveLogs={[]} />)
  fireEvent.change(screen.getByRole('textbox', { name: '按日志内容搜索' }), { target: { value: 'historic failure' } })
  fetch.mockResolvedValue([entry('历史窗口外命中', { level: 'ERROR', ts: BASE - 1000000 })])
  fireEvent.click(within(screen.getByRole('group')).getByRole('button', { name: /^ERROR/ }))
  await screen.findByText('历史窗口外命中')
  expect(fetch).toHaveBeenLastCalledWith({ service: '', level: 'ERROR', q: 'historic failure', limit: 300 })
  await act(async () => old.resolve([entry('旧查询结果')]))
  expect(screen.queryByText('旧查询结果')).toBeNull()
  const matched = entry('HISTORIC FAILURE live', { level: 'ERROR', ts: BASE + 1 })
  rerender(<LogsView liveLogs={[entry('ignore'), matched]} />)
  await screen.findByText('HISTORIC FAILURE live')
  expect(screen.queryByText('ignore')).toBeNull()
})

test('multiple services query independently with the same remote filters and only trace remains a local filter', async () => {
  fetch.mockImplementation(async params => {
    if (!params.service) return [entry('nav'), entry('edge', { service: 'edge' })]
    return [entry(params.service + ' old match', { service: params.service, trace_id: params.service === 'edge' ? '' : 'trace-a' })]
  })
  render(<LogsView />)
  await screen.findByRole('table')
  fireEvent.click(screen.getByRole('button', { name: /服务 · 全部/ }))
  const serviceDialog = screen.getByRole('dialog')
  fireEvent.click(within(serviceDialog).getByRole('checkbox', { name: 'navigation' }))
  fireEvent.click(within(serviceDialog).getByRole('checkbox', { name: 'edge' }))
  await screen.findByText('navigation old match')
  expect(await screen.findByText('edge old match')).toBeTruthy()
  expect(fetch.mock.calls.slice(-2).map(([params]) => params)).toEqual([
    { service: 'edge', level: '', q: '', limit: 300 },
    { service: 'navigation', level: '', q: '', limit: 300 },
  ])
  fireEvent.keyDown(serviceDialog, { key: 'Escape' })
  const calls = fetch.mock.calls.length
  fireEvent.click(screen.getByRole('checkbox', { name: '只看带 trace' }))
  expect(messages()).toEqual(['navigation old match'])
  expect(fetch).toHaveBeenCalledTimes(calls)
  expect(screen.getByText(/带 trace 1 条（仅筛选已加载范围）/)).toBeTruthy()
  fireEvent.click(screen.getByRole('radio', { name: '1000' }))
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith({ service: 'navigation', level: '', q: '', limit: 1000 }))
})

test('JSON expands as a code block and trace navigates to its turn without nested buttons', async () => {
  const json = JSON.stringify({ message: '完整消息', detail: 'x'.repeat(1100) })
  fetch.mockResolvedValue([entry(json)])
  const { container } = render(<LogsView />)
  fireEvent.click(await screen.findByRole('button', { name: json }))
  const code = container.querySelector('pre code')
  expect(code?.textContent).toContain('x'.repeat(1100))
  expect(code?.textContent).toContain('\n')
  expect(container.querySelector('button button')).toBeNull()
  fireEvent.click(screen.getAllByRole('button', { name: '在轮次页打开 trace trace-a' })[0])
  expect(new URLSearchParams(location.search).get('view')).toBe('turns')
  expect(new URLSearchParams(location.search).get('trace')).toBe('trace-a')
})

test('loading, failure, and empty states are separate; retry is available after failure', async () => {
  const pending = deferred<LogEntry[]>()
  fetch.mockReturnValueOnce(pending.promise)
  render(<LogsView />)
  expect(screen.getAllByRole('status', { name: '加载中' })).toHaveLength(10)
  expect(screen.queryByText('还没有日志')).toBeNull()
  await act(async () => pending.reject(new Error('collector unavailable')))
  expect(screen.getByRole('alert').textContent).toContain('日志加载失败')
  expect(screen.queryByText('还没有日志')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  expect(await screen.findByText('还没有日志')).toBeTruthy()
})

test('legacy lastLog prop still appends subsequent events', async () => {
  const { rerender } = render(<LogsView lastLog={null} />)
  await screen.findByText('还没有日志')
  rerender(<LogsView lastLog={entry('legacy event')} />)
  expect(await screen.findByText('legacy event')).toBeTruthy()
})

test('search debounces for 200ms while retaining and identifying the previous filter result', async () => {
  vi.useFakeTimers()
  const initial = deferred<LogEntry[]>()
  const search = deferred<LogEntry[]>()
  fetch.mockReturnValueOnce(initial.promise).mockReturnValueOnce(search.promise)
  render(<LogsView />)
  await act(async () => initial.resolve([entry('上次结果')]))
  const list = screen.getByLabelText('日志列表')
  fireEvent.change(screen.getByRole('textbox', { name: '按日志内容搜索' }), { target: { value: 'old' } })
  expect(messages()).toEqual(['上次结果'])
  expect(screen.queryByRole('status', { name: '加载中' })).toBeNull()
  expect(list.getAttribute('aria-busy')).toBe('true')
  expect(screen.getByText(/正在更新 · 显示上次结果/).textContent).toContain('上次筛选：服务 全部 · 级别 全部 · 内容 不限')
  act(() => vi.advanceTimersByTime(199))
  expect(fetch).toHaveBeenCalledTimes(1)
  fireEvent.change(screen.getByRole('textbox', { name: '按日志内容搜索' }), { target: { value: 'old complete' } })
  act(() => vi.advanceTimersByTime(199))
  expect(fetch).toHaveBeenCalledTimes(1)
  act(() => vi.advanceTimersByTime(1))
  expect(fetch).toHaveBeenLastCalledWith({ service: '', level: '', q: 'old complete', limit: 300 })
  await act(async () => search.resolve([entry('新筛选结果')]))
  expect(messages()).toEqual(['新筛选结果'])
  expect(list.getAttribute('aria-busy')).toBe('false')
  expect(screen.queryByText(/显示上次结果/)).toBeNull()
})

test('a failed filter refresh preserves the previous rows and their previous limit with an explicit failure', async () => {
  fetch.mockResolvedValueOnce([entry('保留旧范围')]).mockRejectedValueOnce(new Error('refresh offline'))
  render(<LogsView />)
  await screen.findByRole('table')
  fireEvent.click(screen.getByRole('radio', { name: '1000' }))
  await screen.findByRole('alert')
  expect(messages()).toEqual(['保留旧范围'])
  expect(screen.getByRole('alert').textContent).toContain('更新失败：refresh offline · 显示上次结果')
  expect(screen.getByText(/最近 300 条 · 已加载 1 条/)).toBeTruthy()
  expect(screen.queryByText(/最近 1000 条 · 已加载/)).toBeNull()
  expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
})
