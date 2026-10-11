import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { AgentTile } from './AgentList'
import { fixtureAgents, observationNow } from '../fixtures'

afterEach(cleanup)
const NOW = 1791705491681
test('registry fractional Unix seconds display as a recent heartbeat', () => {
  const { rerender } = render(<AgentTile id="info" agent={{ healthy: true, count: 11, last_seen: 1791705489.2798107 }} now={NOW} />)
  expect(screen.getByText('2 秒前')).toBeTruthy()
  rerender(<AgentTile id="info" agent={{ healthy: true, count: 11, last_seen: NOW / 1000 - 125 }} now={NOW} />)
  expect(screen.getByText('2 分钟前')).toBeTruthy()
})
test.each([undefined, null, NaN, Infinity, 0])('missing or invalid heartbeat %s stays unreported', value => {
  render(<AgentTile id="info" agent={{ last_seen: value as number | undefined }} now={NOW} />)
  expect(screen.getByText('未上报时间')).toBeTruthy()
})
test('visual fixtures use the same seconds contract as collector health events', () => {
  history.replaceState(null, '', '/?fixture=live')
  const agent = Object.values(fixtureAgents)[0]
  render(<AgentTile id="fixture" agent={agent} now={observationNow()} />)
  expect(screen.getByText('2 秒前')).toBeTruthy()
  history.replaceState(null, '', '/')
})
