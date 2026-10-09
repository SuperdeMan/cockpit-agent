import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { OUTCOME_DISPLAY, outcomeDisplay } from '../../outcomeDisplay'
import { CodeBlock, Duration, IdChip, OutcomeBadge, ShareBar } from '.'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

test('display vocabulary covers each actual server outcome without owning classification', () => {
  const source = readFileSync(resolve(process.cwd(), '../runtime/outcome.py'), 'utf8')
  const table = source.split('CATEGORY_OF: dict[str, str] = {')[1].split('\n}')[0]
  const serverKinds = Array.from(table.matchAll(/^\s+"([a-z_]+)": CAT_/gm), match => match[1]).sort()
  expect(Object.keys(OUTCOME_DISPLAY).sort()).toEqual(serverKinds)
  expect(outcomeDisplay({ outcome: 'planner_failure', status: 'ok' })).toMatchObject({ label: '规划失败', tone: 'critical' })
  expect(outcomeDisplay({ outcome: 'future_outcome', outcome_category: 'permission_missing' })).toMatchObject({ label: 'future_outcome', tone: 'warn' })
  expect(outcomeDisplay({ outcome: 'future_outcome' })).toEqual({ label: 'future_outcome', tone: 'neutral', icon: undefined })
  expect(outcomeDisplay({ outcome: '__proto__', outcome_category: 'toString' })).toMatchObject({ label: '__proto__', tone: 'neutral' })
})

test('a successful pipeline with no ledger is neutral; failed legacy pipelines remain critical', () => {
  const { rerender } = render(<OutcomeBadge turn={{ status: 'ok' }} />)
  expect(screen.getByText('ok · 无账本').className).toContain('obs-tone--neutral')
  expect(screen.queryByText('成功')).toBeNull()
  expect(document.querySelector('svg')).toBeNull()
  rerender(<OutcomeBadge turn={{ status: 'timeout' }} />)
  expect(screen.getByText('超时 · 无账本').className).toContain('obs-tone--critical')
})

test('ID copies its full value and never reports success when clipboard rejects', async () => {
  const writeText = vi.fn().mockRejectedValueOnce(new Error('denied')).mockResolvedValueOnce(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const value = 'full-trace-id-that-is-longer-than-12'
  render(<IdChip value={value} />)
  const button = screen.getByRole('button', { name: `复制 trace ID ${value}` })
  fireEvent.click(button)
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('复制失败'))
  expect(screen.queryByText('已复制')).toBeNull()
  fireEvent.click(button)
  await waitFor(() => expect(screen.getByRole('status').textContent).toBe('已复制'))
  expect(writeText).toHaveBeenLastCalledWith(value)
})

test('code and missing measurements remain honest and readable', async () => {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
  render(<><CodeBlock code={'{"request":"原话"}'} truncated /><Duration ms={null} /><ShareBar value={null} /></>)
  fireEvent.click(screen.getByRole('button', { name: '复制代码' }))
  await waitFor(() => expect(screen.getByText('复制失败')).toBeTruthy())
  expect(screen.getAllByText('—')).toHaveLength(2)
  expect(screen.queryByText('0%')).toBeNull()
  expect(screen.getByLabelText('占比 未上报')).toBeTruthy()
})
