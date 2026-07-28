import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
    intents: 'navigation.navigate_to', plan_mode: 'toolcall', gold_intents: '',
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
  // 数据飞轮 P0：plan_mode 徽记 + 实际落域 + gold 标注入口
  expect(screen.getByText('toolcall')).toBeTruthy()
  expect(screen.getByText(/实际: navigation.navigate_to/)).toBeTruthy()
  expect(screen.getByPlaceholderText(/正确落域标注/)).toBeTruthy()
})

test('saves gold intent label on enter', async () => {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, _init?: RequestInit) => {
    const u = String(url)
    if (u.includes('/api/intents/observed')) {
      return { ok: true, json: async () => ['nearby.search'] }
    }
    if (u.includes('/label')) {
      return { ok: true, json: async () => ({ ok: true }) }
    }
    return { ok: true, json: async () => detail }
  })
  vi.stubGlobal('fetch', fetchMock)

  render(<TurnDetailPanel traceId="trace123456789" />)
  await waitFor(() => expect(screen.getByText('导航去机场')).toBeTruthy())

  const input = screen.getByPlaceholderText(/正确落域标注/) as HTMLInputElement
  fireEvent.change(input, { target: { value: 'nearby.search' } })
  fireEvent.keyDown(input, { key: 'Enter' })

  await waitFor(() => {
    const labelCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/label'))
    expect(labelCall).toBeTruthy()
    expect(String(labelCall![1]?.body)).toContain('nearby.search')
  })
})
