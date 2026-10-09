import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { CommandConsole, fixtureCommandTrace } from './CommandConsole'
import { fixtureTurns } from '../../fixtures'
import { changesOf } from './LiveTrace'
import type { Span } from '../../types'

const sockets: FakeSocket[] = []
class FakeSocket {
  sent: string[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  constructor() { sockets.push(this) }
  send(value: string) { this.sent.push(value) }
  close() {}
}
beforeEach(() => { sockets.length = 0; history.replaceState(null, '', '/'); vi.stubGlobal('WebSocket', FakeSocket) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })
test('only a user send opens the gateway and never authorizes a confirmation', () => {
  render(<CommandConsole />)
  expect(sockets).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: '打开后备箱' }))
  act(() => sockets[0].onopen?.())
  expect(JSON.parse(sockets[0].sent[0])).toMatchObject({ text: '打开后备箱', is_confirmation: false })
  act(() => sockets[0].onmessage?.({ data: JSON.stringify({ type: 'final', speech: '需要你确认。' }) }))
  expect(screen.getByText('完成').className).toContain('neutral')
  expect(screen.getByText('未采到车身变化证据')).toBeTruthy()
  expect(screen.queryByText('成功')).toBeNull()
})
test('gateway errors and unreachable sockets are distinct states', () => {
  const { unmount } = render(<CommandConsole />)
  fireEvent.click(screen.getByRole('button', { name: '打开后备箱' }))
  act(() => sockets[0].onmessage?.({ data: JSON.stringify({ type: 'error', message: '请求被拒' }) }))
  expect(screen.getByText('失败')).toBeTruthy()
  unmount()
  render(<CommandConsole />)
  fireEvent.click(screen.getByRole('button', { name: '打开后备箱' }))
  act(() => sockets[1].onerror?.())
  expect(screen.getByText('连不上网关')).toBeTruthy()
})
test('change evidence only comes from val.execute changes arrays', () => {
  const span = { node: 'cloud.outcome', attrs: { changes: [{ key: 'window', old: 0, new: 100 }] } } as unknown as Span
  expect(changesOf([span])).toEqual({ changes: [], hasEvidence: false })
  expect(changesOf([{ ...span, node: 'val.execute', attrs: { changes: [] } }])).toEqual({ changes: [], hasEvidence: true })
})

test('a sent request timeout stays unknown and ignores a late final after cleanup', async () => {
  vi.useFakeTimers()
  render(<CommandConsole />)
  fireEvent.click(screen.getByRole('button', { name: '打开后备箱' }))
  act(() => sockets[0].onopen?.())
  const late = sockets[0].onmessage!
  await act(async () => { await vi.advanceTimersByTimeAsync(35000) })
  expect(screen.getByText('结果未取得')).toBeTruthy()
  expect(screen.getByText(/结果未取得，是否执行未知/)).toBeTruthy()
  expect(screen.queryByText('连不上网关')).toBeNull()
  expect(document.querySelector('.command-console')?.getAttribute('data-command-reason')).toBe('result-timeout')
  act(() => late({ data: JSON.stringify({ type: 'final', speech: '迟到的完成' }) }))
  expect(screen.queryByText('完成')).toBeNull()
  expect(screen.queryByText('迟到的完成')).toBeNull()
  expect(sockets[0].onmessage).toBeNull()
})

test('a connection timeout that never sent the request remains unreachable', async () => {
  vi.useFakeTimers()
  render(<CommandConsole />)
  fireEvent.click(screen.getByRole('button', { name: '打开后备箱' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(35000) })
  expect(sockets[0].sent).toEqual([])
  expect(screen.getByText('连不上网关')).toBeTruthy()
  expect(screen.getByText('连接超时，请求尚未发送。')).toBeTruthy()
  expect(document.querySelector('.command-console')?.getAttribute('data-command-reason')).toBe('connect-timeout')
})

test('a dropped connection after send reports an unknown result rather than a failed connection', () => {
  render(<CommandConsole />)
  fireEvent.click(screen.getByRole('button', { name: '打开后备箱' }))
  act(() => sockets[0].onopen?.())
  act(() => sockets[0].onclose?.())
  expect(screen.getByText('结果未取得')).toBeTruthy()
  expect(screen.queryByText('连不上网关')).toBeNull()
  expect(document.querySelector('.command-console')?.getAttribute('data-command-reason')).toBe('connection-lost')
})

test('pending fixture uses the shared cloud trace and all demo sends stay offline', async () => {
  vi.useFakeTimers()
  history.replaceState(null, '', '/?fixture=live-pending')
  expect(fixtureCommandTrace()).toBe(fixtureTurns[2].trace_id)
  const onTrace = vi.fn()
  render(<CommandConsole onTrace={onTrace} />)
  fireEvent.click(screen.getByRole('button', { name: /^#/ }))
  expect(new URLSearchParams(location.search).get('trace')).toBe(fixtureTurns[2].trace_id)
  fireEvent.click(screen.getByRole('button', { name: '打开后备箱' }))
  await act(async () => { await vi.advanceTimersByTimeAsync(300) })
  expect(onTrace).toHaveBeenCalledWith(fixtureTurns[2].trace_id, '打开后备箱')
  expect(sockets).toHaveLength(0)
  expect(screen.getByText(/未发送到网关/)).toBeTruthy()
})
