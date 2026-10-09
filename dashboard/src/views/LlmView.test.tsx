import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { LlmSummary, LlmSummaryGroup } from '../types'
import { fetchLlmSummary } from '../api'
import { fmtTokens, LlmView } from './LlmView'

vi.mock('../api', () => ({ fetchLlmSummary: vi.fn() }))
const fetch = vi.mocked(fetchLlmSummary)
const group = (patch: Partial<LlmSummaryGroup> = {}): LlmSummaryGroup => ({
  caller: 'cloud-planner', model: 'mimo-v2.5-pro', calls: 12, prompt_tokens: 60000,
  completion_tokens: 900, errors: 0, avg_latency_ms: 2100.4, last_ts: 1783912215432,
  fallback_calls: 0, zero_usage_calls: 0, ...patch,
})
const tile = (label: string) => screen.getByText(label, { selector: '.obs-stat-tile__label' }).closest('.obs-stat-tile') as HTMLElement
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, failed) => { resolve = done; reject = failed })
  return { promise, resolve, reject }
}
beforeEach(() => { fetch.mockReset(); fetch.mockResolvedValue({ hours: 24, groups: [] }) })
afterEach(cleanup)

test('token formatter retains compact numbers and never makes missing numeric data zero', () => {
  expect(fmtTokens(999)).toBe('999')
  expect(fmtTokens(60000)).toBe('6.0万')
  expect(fmtTokens(2628074)).toBe('263万')
  expect(fmtTokens(Number.NaN)).toBe('—')
})

test('five stat cards show exact counts, errors and fallback separately; unassigned groups are pinned first', async () => {
  fetch.mockResolvedValue({ hours: 24, groups: [
    group(),
    group({ caller: '(未归属)', model: 'MiniMax-M3', calls: 3, prompt_tokens: 1200, completion_tokens: 40, errors: 2, fallback_calls: 1 }),
  ] })
  render(<LlmView lastTurn={null} />)
  await screen.findByRole('table')
  expect(document.querySelectorAll('.obs-stat-tile')).toHaveLength(5)
  expect(tile('调用次数').textContent).toContain('15')
  expect(tile('错误').textContent).toContain('2')
  expect(tile('错误').textContent).toContain('降级另计：1 次')
  expect(tile('未归属调用').textContent).toContain('归属盲区 · 应为 0')
  const first = document.querySelector('tbody tr')
  expect(first?.textContent).toContain('(未归属)')
  expect(within(first as HTMLElement).getByText('归属盲区')).toBeTruthy()
  expect(first?.className).toContain('obs-usage__blind')
  expect(screen.queryByText(/都已.*兜底/)).toBeNull()
})

test('only a whole zero-token group is unreported; a reported group can have a real zero output', async () => {
  fetch.mockResolvedValue({ hours: 24, groups: [
    group({ caller: 'no-usage', prompt_tokens: 0, completion_tokens: 0, calls: 5, zero_usage_calls: 4 }),
    group({ caller: 'input-only', prompt_tokens: 15, completion_tokens: 0 }),
  ] })
  render(<LlmView lastTurn={null} />)
  await screen.findByRole('table')
  const missing = screen.getByText('no-usage').closest('tr') as HTMLElement
  expect(within(missing).getAllByText('— 未上报')).toHaveLength(2)
  expect(within(missing).getByLabelText(/tokens 占比 未上报/)).toBeTruthy()
  const partial = screen.getByText('input-only').closest('tr') as HTMLElement
  expect(within(partial).queryByText('— 未上报')).toBeNull()
  expect(partial.querySelectorAll('td')[4].textContent).toBe('0')
  expect(tile('未上报用量').textContent).toContain('4')
  expect(tile('未上报用量').textContent).toContain('成功调用')
  expect(tile('tokens').textContent).toContain('15')
})

test('an all-zero reported window shows unknown token total instead of zero', async () => {
  fetch.mockResolvedValue({ hours: 24, groups: [group({ prompt_tokens: 0, completion_tokens: 0, zero_usage_calls: 12 })] })
  render(<LlmView lastTurn={null} />)
  await screen.findByRole('table')
  expect(tile('tokens').querySelector('.obs-stat-tile__value')?.textContent).toBe('—')
  expect(tile('tokens').textContent).toContain('输入与输出均未上报')
})

test('legacy C9 absence counts unreported groups, never invents successful call or fallback totals', async () => {
  const legacy = group({ caller: 'legacy', calls: 370, prompt_tokens: 0, completion_tokens: 0, errors: 17 })
  delete legacy.zero_usage_calls
  delete legacy.fallback_calls
  fetch.mockResolvedValue({ hours: 24, groups: [legacy, group({ caller: 'new-api', zero_usage_calls: 1 })] })
  render(<LlmView lastTurn={null} />)
  await screen.findByRole('table')
  const unknown = tile('未上报分组')
  expect(unknown.querySelector('.obs-stat-tile__value')?.textContent).toBe('1组')
  expect(unknown.textContent).toContain('未提供成功调用数')
  expect(screen.queryByText('未上报用量', { selector: '.obs-stat-tile__label' })).toBeNull()
  expect(tile('错误').textContent).toContain('降级另计：— 未上报')
  expect(screen.getByText(/未上报分组数不代表调用次数/)).toBeTruthy()
})

test('switching windows invalidates old success and failure responses', async () => {
  const day = deferred<LlmSummary>()
  const week = deferred<LlmSummary>()
  fetch.mockReturnValueOnce(day.promise).mockReturnValueOnce(week.promise).mockResolvedValue({ hours: 1, groups: [group({ caller: 'hourly' })] })
  render(<LlmView lastTurn={null} />)
  fireEvent.click(screen.getByRole('radio', { name: '7 天' }))
  fireEvent.click(screen.getByRole('radio', { name: '1 小时' }))
  await screen.findByText('hourly')
  await act(async () => {
    day.resolve({ hours: 24, groups: [group({ caller: 'stale day' })] })
    week.reject(new Error('stale week failed'))
  })
  expect(screen.queryByText('stale day')).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(fetch).toHaveBeenLastCalledWith(1)
})

test('loading, request error with retry, and a valid empty window render separately', async () => {
  const pending = deferred<LlmSummary>()
  fetch.mockReturnValueOnce(pending.promise)
  render(<LlmView lastTurn={null} />)
  expect(screen.getAllByRole('status', { name: '加载中' })).toHaveLength(6)
  expect(screen.queryByText('该时间窗内没有 LLM 调用')).toBeNull()
  await act(async () => pending.reject(new Error('request unavailable')))
  expect(screen.getByRole('alert').textContent).toContain('用量加载失败')
  expect(screen.queryByText('该时间窗内没有 LLM 调用')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await waitFor(() => expect(screen.getByText('该时间窗内没有 LLM 调用')).toBeTruthy())
  expect(tile('调用次数').querySelector('.obs-stat-tile__value')?.textContent).toBe('0次')
})

test('window refresh retains the previous summary and its actual time window, including after failure', async () => {
  const pending = deferred<LlmSummary>()
  fetch.mockResolvedValueOnce({ hours: 24, groups: [group({ caller: 'old-day' })] })
    .mockReturnValueOnce(pending.promise).mockResolvedValue({ hours: 168, groups: [group({ caller: 'new-week' })] })
  render(<LlmView lastTurn={null} />)
  await screen.findByText('old-day')
  fireEvent.click(screen.getByRole('radio', { name: '7 天' }))
  expect(screen.getByText('old-day')).toBeTruthy()
  expect(screen.queryByRole('status', { name: '加载中' })).toBeNull()
  expect(screen.getByText('正在更新 · 显示上次结果（24 小时）')).toBeTruthy()
  expect(screen.getByLabelText('最近 24 小时 用量汇总').getAttribute('aria-busy')).toBe('true')
  expect(tile('调用次数').textContent).toContain('近 24 小时')
  expect(tile('调用次数').textContent).not.toContain('近 7 天')
  await act(async () => pending.reject(new Error('refresh unavailable')))
  expect(screen.getByText('old-day')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('上次 24 小时的结果')
  fireEvent.click(screen.getByRole('button', { name: '重试' }))
  await screen.findByText('new-week')
  expect(screen.queryByText('old-day')).toBeNull()
  expect(tile('调用次数').textContent).toContain('近 7 天')
})
