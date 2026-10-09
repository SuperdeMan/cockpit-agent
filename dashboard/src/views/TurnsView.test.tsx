import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { TurnsView } from './TurnsView'
import { searchTurnPage } from '../api'
import { fixtureTurns } from '../fixtures'
import type { Page, Turn } from '../types'
import { useRoute } from '../navigation'

vi.mock('../api', () => ({ searchTurnPage: vi.fn(), fetchSessions: vi.fn(async () => []) }))
vi.mock('../components/TurnDetailPanel', () => ({ TurnDetailPanel: () => <div>Inspector</div> }))
const search = vi.mocked(searchTurnPage)
beforeEach(() => { search.mockReset() })
afterEach(() => { cleanup(); history.replaceState(null, '', '/') })

test('legacy responses never claim a total and filter only loaded rows', async () => {
  search.mockResolvedValue({ items: [fixtureTurns[0], fixtureTurns[7]], limit: 200, offset: 0 })
  render(<TurnsView lastTurn={null} />)
  await screen.findByText('最近 2 轮')
  expect(screen.getByText('筛选限已加载范围')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'App' }))
  await waitFor(() => expect(screen.queryByText(fixtureTurns[0].user_text)).toBeNull())
  expect(screen.getByText(fixtureTurns[7].user_text)).toBeTruthy()
  expect(screen.queryByText(/共 \d+ 轮/)).toBeNull()
})
test('saved preset exempts the time window and does not nest interactive controls', async () => {
  search.mockResolvedValue({ items: [fixtureTurns[1]], total: 1, limit: 200, offset: 0 })
  const { container } = render(<TurnsView saved lastTurn={null} />)
  await screen.findByText('最近 1 轮 · 共 1 轮')
  expect(search.mock.calls[0][0].badcase).toBe(1)
  expect(search.mock.calls[0][0].since).toBeUndefined()
  expect(container.querySelector('button button')).toBeNull()
})
test('an old request cannot replace a newer search result', async () => {
  let resolveOld!: (page: Page<Turn>) => void
  search.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve })).mockResolvedValue({ items: [fixtureTurns[7]], total: 1, limit: 200, offset: 0 })
  render(<TurnsView lastTurn={null} />)
  await waitFor(() => expect(search).toHaveBeenCalledTimes(1))
  fireEvent.change(screen.getByLabelText('搜索轮次'), { target: { value: '副驾' } })
  await screen.findByText(fixtureTurns[7].user_text)
  await act(async () => resolveOld({ items: [fixtureTurns[0]], total: 1, limit: 200, offset: 0 }))
  expect(screen.queryByText(fixtureTurns[0].user_text)).toBeNull()
})

test('D21 arrows move focus without opening; Enter opens the focused turn', async () => {
  search.mockResolvedValue({ items: fixtureTurns.slice(0, 3), total: 3, limit: 200, offset: 0 })
  const { container } = render(<TurnsView lastTurn={null} />)
  await screen.findByText('最近 3 轮 · 共 3 轮')
  const rows = container.querySelectorAll<HTMLButtonElement>('.turn-row')
  act(() => rows[0].focus())
  fireEvent.keyDown(rows[0], { key: 'ArrowDown' })
  expect(document.activeElement).toBe(rows[1])
  expect(new URL(location.href).searchParams.get('trace')).toBeNull()
  fireEvent.keyDown(rows[1], { key: 'Enter' })
  expect(new URL(location.href).searchParams.get('trace')).toBe(fixtureTurns[1].trace_id)
})

test('trace lookup bypasses the time window and retained filters while ordinary searches restore them', async () => {
  const old = { ...fixtureTurns[0], ts: fixtureTurns[0].ts - 48 * 3600000, badcase: 0 }
  search.mockImplementation(async params => ({ items: params.q === old.trace_id ? [old] : [fixtureTurns[0], fixtureTurns[7]], limit: 200, offset: 0 }))
  render(<TurnsView lastTurn={null} />)
  await screen.findByText('最近 2 轮')
  fireEvent.click(screen.getByRole('button', { name: 'App' }))
  fireEvent.click(screen.getByRole('button', { name: /标志 全部/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: '已标 badcase' }))
  fireEvent.change(screen.getByRole('spinbutton', { name: '耗时至少（秒）' }), { target: { value: '10' } })
  const input = screen.getByLabelText('搜索轮次')
  fireEvent.change(input, { target: { value: `#${old.trace_id}` } })
  await waitFor(() => expect(search.mock.lastCall?.[0]).toEqual({ q: old.trace_id, limit: 200 }))
  await screen.findByText(old.user_text)
  expect(new URLSearchParams(location.search).get('q')).toBe(`#${old.trace_id}`)
  expect(screen.getByText('trace 查找 · 不限其他筛选')).toBeTruthy()
  fireEvent.change(input, { target: { value: '普通搜索' } })
  await waitFor(() => expect(search.mock.lastCall?.[0]).toMatchObject({ q: '普通搜索', origin: 'app', badcase: 1, min_duration_ms: 10000 }))
  expect(search.mock.lastCall?.[0].since).toBeTypeOf('number')
  expect(new URLSearchParams(location.search).get('q')).toBe('普通搜索')
})

test('a trace prefix also bypasses the saved preset rather than hiding an existing unmarked turn', async () => {
  search.mockResolvedValue({ items: [fixtureTurns[0]], limit: 200, offset: 0 })
  render(<TurnsView lastTurn={null} saved query={fixtureTurns[0].trace_id.slice(0, 8)} />)
  await screen.findByText(fixtureTurns[0].user_text)
  expect(search.mock.lastCall?.[0]).toEqual({ q: fixtureTurns[0].trace_id.slice(0, 8), limit: 200 })
})

test('route-backed local search synchronizes q without resetting the input or issuing a request loop', async () => {
  history.replaceState(null, '', '/?view=turns&q=initial')
  search.mockResolvedValue({ items: [], limit: 200, offset: 0 })
  function Routed() { const route = useRoute(); return <TurnsView lastTurn={null} query={route.query} traceId={route.trace} /> }
  render(<Routed />)
  await waitFor(() => expect(search).toHaveBeenCalledTimes(1))
  fireEvent.change(screen.getByLabelText('搜索轮次'), { target: { value: 'typed text' } })
  await waitFor(() => expect(search.mock.lastCall?.[0].q).toBe('typed text'))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 220)) })
  expect(screen.getByLabelText('搜索轮次')).toHaveProperty('value', 'typed text')
  expect(new URLSearchParams(location.search).get('q')).toBe('typed text')
  expect(search).toHaveBeenCalledTimes(2)
  act(() => { history.replaceState(null, '', '/?view=turns&q=from-history'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await waitFor(() => expect(search.mock.lastCall?.[0].q).toBe('from-history'))
  expect(screen.getByLabelText('搜索轮次')).toHaveProperty('value', 'from-history')
})
