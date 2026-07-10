import { fireEvent, render, screen } from '@testing-library/react'

import { SpanWaterfall } from './SpanWaterfall'
import type { Span } from '../types'

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

test('renders one row per span with duration and status', () => {
  render(<SpanWaterfall spans={spans} />)
  expect(screen.getByText('route.cloud')).toBeTruthy()
  expect(screen.getByText('cloud.planning')).toBeTruthy()
  expect(screen.getByText('step.agent:weather')).toBeTruthy()
  expect(screen.getByText('1.20s')).toBeTruthy()
  expect(screen.getByText('err')).toBeTruthy()
})

test('click expands span attrs JSON', () => {
  render(<SpanWaterfall spans={spans} />)
  fireEvent.click(screen.getByText('cloud.planning'))
  expect(screen.getByText(/"plan"/)).toBeTruthy()
})

test('empty spans show hint', () => {
  render(<SpanWaterfall spans={[]} />)
  expect(screen.getByText(/没有采到 span/)).toBeTruthy()
})
