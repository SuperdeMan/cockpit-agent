import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import { TurnDetailPanel } from './TurnDetailPanel'
import type { TurnDetail } from '../types'

const detail: TurnDetail = {
  turn: {
    trace_id: 'trace123456789', session_id: 's1', ts: 1720000000000,
    duration_ms: 1534, user_text: '导航去机场', speech: '已为您规划路线',
    status: 'ok', path: 'cloud', input_source: 'voice_wake',
    is_confirmation: 0, ui_card_type: 'route_plan', actions: 1,
    error: '', badcase: 0, note: '',
  },
  spans: [
    {
      trace_id: 'trace123456789', span_id: 'p1', ts: 1720000000500,
      service: 'cloud', node: 'cloud.planning', status: 'ok', duration_ms: 800,
      attrs: { plan: '[{"agent":"navigation"}]', llm_raw: '{"steps":[...]}' },
    },
  ],
  llm_calls: [
    {
      trace_id: 'trace123456789', ts: 1720000000400, caller: 'cloud-planner',
      model: 'mimo-v2.5', prompt_tokens: 900, completion_tokens: 120,
      latency_ms: 750, cache_hit: 0, thinking: 0, status: 'ok', error: '',
      prompt_tail: '用户说: 导航去机场', content_head: '{"steps":[]}',
    },
  ],
  logs: [
    {
      ts: 1720000000600, service: 'cloud-planner', level: 'INFO',
      logger: 'planner.engine', msg: 'Plan ready', trace_id: 'trace123456789',
      session_id: 's1',
    },
  ],
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => detail,
  })))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

test('renders turn content, plan, llm calls and logs', async () => {
  render(<TurnDetailPanel traceId="trace123456789" />)

  await waitFor(() => expect(screen.getByText('导航去机场')).toBeTruthy())
  expect(screen.getByText('已为您规划路线')).toBeTruthy()
  expect(screen.getByText(/route_plan/)).toBeTruthy()
  expect(screen.getByText('[{"agent":"navigation"}]')).toBeTruthy()
  expect(screen.getAllByText('cloud-planner').length).toBeGreaterThan(0)
  expect(screen.getByText('mimo-v2.5')).toBeTruthy()
  expect(screen.getByText('Plan ready')).toBeTruthy()
  expect(screen.getByText(/标记 badcase/)).toBeTruthy()
  expect(screen.getByText('#trace1234567')).toBeTruthy()
})
