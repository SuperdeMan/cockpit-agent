import { render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import type { LlmSummary } from '../types'

const summary: LlmSummary = {
  hours: 24,
  groups: [
    { caller: 'cloud-planner', model: 'mimo-v2.5-pro', calls: 12, prompt_tokens: 60000,
      completion_tokens: 900, errors: 0, avg_latency_ms: 2100.4, last_ts: 1783912215432 },
    { caller: '(未归属)', model: 'MiniMax-M3', calls: 3, prompt_tokens: 1200,
      completion_tokens: 40, errors: 2, avg_latency_ms: 300, last_ts: 1783912215432 },
  ],
}

vi.mock('../api', () => ({ fetchLlmSummary: vi.fn(() => Promise.resolve(summary)) }))

import { fmtTokens, LlmView } from './LlmView'

test('fmtTokens 万位收敛', () => {
  expect(fmtTokens(999)).toBe('999')
  expect(fmtTokens(60000)).toBe('6.0万')
  expect(fmtTokens(2628074)).toBe('263万')
})

test('按 caller×model 渲染归属表，未归属行高亮盯防', async () => {
  render(<LlmView lastTurn={null} />)
  expect(await screen.findByText('cloud-planner')).toBeTruthy()
  expect(screen.getByText('mimo-v2.5-pro')).toBeTruthy()
  expect(screen.getByText('(未归属)')).toBeTruthy()
  // 汇总块：总调用 15、未归属 3（应为 0 的盯防位）
  expect(screen.getByText('15')).toBeTruthy()
  expect(screen.getByText('未归属调用（应为 0）')).toBeTruthy()
  const blindRow = screen.getByText('(未归属)').closest('tr')
  expect(blindRow?.className).toContain('llm-row--blind')
})
