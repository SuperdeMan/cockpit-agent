import { afterEach, expect, it } from 'vitest'
import { fixtureResponse, FIXTURE_TRACE, fixtureDetail, fixtureTurns, isFixture } from './fixtures'
import { navigate, readRoute, shareableUrl } from './navigation'

afterEach(() => history.replaceState(null, '', '/'))
it('has reproducible independent zero-duration events and LLM intervals', () => {
  history.replaceState(null, '', '/?fixture=turns')
  const detail = fixtureDetail(FIXTURE_TRACE)
  expect('turn' in detail && detail.spans.some(s => s.duration_ms === 0)).toBe(true)
  expect('turn' in detail && detail.llm_calls.length).toBe(3)
})
it('pending vehicle-control fixture contains only its cloud confirmation chain', () => {
  const detail = fixtureDetail(fixtureTurns[2].trace_id)
  expect('turn' in detail).toBe(true)
  if (!('turn' in detail) || !detail.turn) throw new Error('missing fixture')
  expect(detail.turn.path).toBe('cloud')
  expect(detail.spans.some(span => span.status === 'need_confirm')).toBe(true)
  expect(detail.spans.some(span => span.node === 'val.execute')).toBe(false)
  expect(detail.llm_calls.every(call => call.caller === 'cloud-planner' && call.ts <= detail.turn!.ts + detail.turn!.duration_ms)).toBe(true)
  expect(detail.logs.every(log => log.level === 'INFO')).toBe(true)
})
it('unknown offline endpoints fail closed and cannot fall through to live transport', async () => {
  history.replaceState(null, '', '/?fixture=unknown')
  expect(isFixture()).toBe(true)
  expect(fixtureResponse('/unknown', new URLSearchParams()).status).toBe(404)
})
it('supports back/forward-ready deep links and excludes credentials when copying', () => {
  history.replaceState(null, '', '/?fixture=turns&token=not-a-secret')
  navigate({ view: 'logs', trace: FIXTURE_TRACE })
  expect(readRoute()).toEqual({ view: 'logs', trace: FIXTURE_TRACE, query: '' })
  expect(shareableUrl()).not.toContain('token=')
  expect(shareableUrl()).toContain('fixture=turns')
})
