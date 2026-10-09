import { fireEvent, render, screen, within } from '@testing-library/react'
import { vi } from 'vitest'

import { SpanWaterfall } from './SpanWaterfall'
import { Timeline } from './timeline'
import type { LlmCall, LogEntry, Span } from '../types'

const spans: Span[] = [
  {
    trace_id: 't1', span_id: 'a', ts: 1000, service: 'edge',
    node: 'route.cloud', status: 'ok', duration_ms: 5, attrs: { text: '你好' },
  },
  {
    trace_id: 't1', span_id: 'b', ts: 2400, service: 'cloud',
    node: 'cloud.planning', status: 'ok', duration_ms: 1200,
    attrs: { steps: 1, plan: '[{"agent":"weather"}]' },
  },
  {
    trace_id: 't1', span_id: 'c', ts: 3000, service: 'cloud',
    node: 'step.agent:weather', status: 'err', duration_ms: 500, attrs: {},
  },
]

test('compatibility entry renders mapped lanes without claiming complete turn timing', () => {
  render(<SpanWaterfall spans={spans} />)
  expect(screen.getByText('route.cloud')).toBeTruthy()
  expect(screen.getByText('cloud.planning')).toBeTruthy()
  expect(screen.getByText('step.agent:weather')).toBeTruthy()
  expect(screen.getByText('相对观测开始')).toBeTruthy()
  expect(screen.getByText(/轮次起止未采集/)).toBeTruthy()
  expect(screen.getByRole('button', { name: /step.agent:weather.*err/ })).toBeTruthy()
})

test('selecting a span opens a keyboard-reachable attribute detail with no inferred parent', () => {
  render(<SpanWaterfall spans={spans} />)
  fireEvent.click(screen.getByText('cloud.planning'))
  const detail = screen.getByRole('complementary', { name: '选中记录详情' })
  expect(within(detail).getByText('plan')).toBeTruthy()
  expect(within(detail).getByText('[{"agent":"weather"}]')).toBeTruthy()
  expect(within(detail).getByText('— 采集未带')).toBeTruthy()
  fireEvent.click(within(detail).getByRole('button', { name: '关闭记录详情' }))
  expect(screen.queryByRole('complementary')).toBeNull()
})

test('empty spans show hint', () => {
  render(<SpanWaterfall spans={[]} />)
  expect(screen.getByText('未采到链路片段')).toBeTruthy()
  expect(screen.queryByText(/过期|NATS 掉线/)).toBeNull()
})

const llm: LlmCall = { id: 8, trace_id: 't1', ts: 2000, caller: 'planner', model: 'test-model',
  latency_ms: 400, prompt_tokens: 0, completion_tokens: 0, cache_hit: false, thinking: false,
  status: 'ok', error: '', prompt_tail: '', content_head: '' }
const log: LogEntry = { id: 3, trace_id: 't1', session_id: 's', ts: 1800, service: 'navigation',
  level: 'FATAL', logger: 'provider', msg: 'upstream unavailable' }

test('spans, LLM calls and logs share a turn-relative axis and metadata spans stay hidden', () => {
  const onLogSelect = vi.fn()
  render(<Timeline turn={{ ts: 1000, duration_ms: 3000 }} spans={[...spans,
    { ...spans[0], span_id: 'hidden', node: 'llm.call.meta', ts: 9000 },
  ]} llmCalls={[llm]} logs={[log]} relativeRange={{ start: -10, end: 4000 }} onLogSelect={onLogSelect} />)
  const timeline = screen.getByRole('region', { name: '轮次时间线' })
  expect(timeline.getAttribute('data-range-start')).toBe('-10')
  expect(timeline.getAttribute('data-range-end')).toBe('4000')
  expect(screen.queryByText('llm.call.meta')).toBeNull()
  expect(screen.getByText('llm · planner')).toBeTruthy()
  const tick = screen.getByRole('button', { name: /FATAL.*upstream unavailable/ })
  expect(tick.className).toContain('timeline-log-tick--critical')
  fireEvent.click(tick)
  expect(onLogSelect).toHaveBeenCalledWith(log)
})

test('provider lane initially collapses with counts, then exposes every recorded call', () => {
  const providers = [1, 2].map(index => ({ ...spans[0], node: 'provider.amap.place', span_id: `provider-${index}`,
    ts: 1500 + index * 100, duration_ms: 50, status: index === 2 ? 'error' : 'ok' }))
  render(<Timeline spans={providers} />)
  expect(screen.getByText('2 次 · 1 种调用 · 合计 100 ms · 失败 1')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /provider.amap.place ·/ })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /展开外部服务泳道/ }))
  expect(screen.getAllByRole('button', { name: /provider.amap.place ·/ })).toHaveLength(2)
})

test('zero-duration events and shadow intervals have separate geometry, with late records outside the turn', () => {
  const { container } = render(<Timeline turn={{ ts: 1000, duration_ms: 5 }} spans={[
    { ...spans[0], span_id: 'route', node: 'route.local', ts: 1001, duration_ms: 0 },
    { ...spans[0], span_id: 'val', node: 'val.execute', ts: 1002, duration_ms: .2 },
    { ...spans[0], span_id: 'shadow', node: 'nlu.shadow', ts: 1010, duration_ms: 0 },
    { ...spans[0], span_id: 'decision', node: 'decision.shadow', ts: 1004, duration_ms: 2 },
  ]} />)
  expect(container.querySelectorAll('.timeline-event--shadow')).toHaveLength(1)
  expect(container.querySelectorAll('.timeline-bar--shadow')).toHaveLength(1)
  expect(container.querySelectorAll('.timeline-event--event')).toHaveLength(1)
  expect(screen.getByText('轮次外')).toBeTruthy()
  expect(screen.getByRole('region', { name: '轮次时间线' }).getAttribute('data-range-end')).toBe('10')
})

test('list view exposes the same records and logs remain directly selectable', () => {
  const onLogSelect = vi.fn()
  render(<Timeline spans={spans} llmCalls={[llm]} logs={[log]} onLogSelect={onLogSelect} />)
  fireEvent.click(screen.getByRole('button', { name: '列表视图' }))
  const table = screen.getByRole('table')
  expect(within(table).getAllByRole('row')).toHaveLength(6)
  expect(within(table).getByRole('columnheader', { name: '相对起止' })).toBeTruthy()
  fireEvent.click(within(table).getByRole('button', { name: /FATAL.*upstream unavailable/ }))
  expect(onLogSelect).toHaveBeenCalledWith(log)
})

test('a log-only or LLM-only trace is readable without a fabricated span or a fabricated zero usage', () => {
  render(<Timeline spans={[]} llmCalls={[llm]} />)
  fireEvent.click(screen.getByRole('button', { name: /llm · planner ·/ }))
  expect(screen.getByText('— / — · usage 未上报')).toBeTruthy()
  expect(screen.queryByText('未采到链路片段')).toBeNull()
})

test('live now advances the range while the actual reported item count stays unchanged', () => {
  const { rerender } = render(<Timeline turn={{ ts: 1000, duration_ms: 0 }} spans={[spans[0]]} live now={1200} />)
  expect(screen.getByRole('region', { name: '轮次时间线' }).getAttribute('data-range-end')).toBe('200')
  rerender(<Timeline turn={{ ts: 1000, duration_ms: 0 }} spans={[spans[0]]} live now={1500} />)
  expect(screen.getByRole('region', { name: '轮次时间线' }).getAttribute('data-range-end')).toBe('500')
  expect(screen.getAllByRole('button', { name: /route.cloud ·/ })).toHaveLength(1)
  expect(screen.queryByText(/调用进行中|等待响应/)).toBeNull()
})

test('now line uses the real relative clock instead of a future observation at the range end', () => {
  const { container, rerender } = render(<Timeline turn={{ ts: 1000, duration_ms: 0 }} spans={[
    { ...spans[0], ts: 4000, duration_ms: 0 },
  ]} live now={1500} />)
  const line = container.querySelector<HTMLElement>('.timeline-now-line')!
  expect(line.dataset.nowMs).toBe('500')
  expect(parseFloat(line.style.left)).toBeCloseTo(100 / 6)
  expect(screen.getByText('现在 500 ms')).toBeTruthy()
  expect(screen.getByRole('region', { name: '轮次时间线' }).getAttribute('data-range-end')).toBe('3000')
  rerender(<Timeline turn={{ ts: 1000, duration_ms: 3000 }} spans={spans} now={1500} />)
  expect(container.querySelector('.timeline-now-line')).toBeNull()
  expect(screen.queryByText('现在 500 ms')).toBeNull()
})

test('a pending turn can show now before its first observed span without a fabricated call', () => {
  const { container } = render(<Timeline turn={{ ts: 1000, duration_ms: 0 }} spans={[]} live now={900} />)
  expect(screen.getByText('现在 −100 ms')).toBeTruthy()
  expect(screen.getByText('等待链路片段上报；目前没有可画的调用。')).toBeTruthy()
  expect(container.querySelector('.timeline-now-line')?.getAttribute('data-now-ms')).toBe('-100')
  expect(screen.queryAllByTestId('timeline-bar')).toHaveLength(0)
  expect(screen.queryAllByTestId('timeline-event')).toHaveLength(0)
})

test('LLM content-capture markers use the existing human-readable projection', () => {
  render(<Timeline spans={[]} llmCalls={[{ ...llm, prompt_tail: '<len=160 sha=d4e5f6a7>', content_head: '<len=45 sha=e5f6a7b8>' }]} />)
  fireEvent.click(screen.getByRole('button', { name: /llm · planner ·/ }))
  fireEvent.click(screen.getByText('提示词末段 / 输出头部'))
  expect(screen.getByText('内容未采集 · 长度 160 · 指纹 d4e5f6a7')).toBeTruthy()
  expect(screen.getByText('内容未采集 · 长度 45 · 指纹 e5f6a7b8')).toBeTruthy()
  expect(screen.queryByText('<len=160 sha=d4e5f6a7>')).toBeNull()
})

test('log tooltip escapes the scrolling axis and is available on keyboard focus', () => {
  render(<Timeline spans={[]} logs={[log]} />)
  const tick = screen.getByRole('button', { name: /FATAL.*upstream unavailable/ })
  fireEvent.focus(tick)
  const tooltip = screen.getByRole('tooltip')
  expect(document.body.contains(tooltip)).toBe(true)
  expect(document.querySelector('.timeline-chart-scroll')?.contains(tooltip)).toBe(false)
  expect(tooltip.textContent).toContain('upstream unavailable')
  expect(tick.getAttribute('aria-describedby')).toBe(tooltip.id)
  fireEvent.keyDown(tick, { key: 'Escape' })
  expect(screen.queryByRole('tooltip')).toBeNull()
})
