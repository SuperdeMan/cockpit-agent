import { render, screen } from '@testing-library/react'

import { TracePanel } from './TracePanel'

test('renders spans of a trace in order', () => {
  const trace = {
    trace_id: 'abcd1234ef',
    spans: [
      {
        trace_id: 'abcd1234ef',
        span_id: '1',
        ts: 1,
        service: 'edge',
        node: 'route.local',
        status: 'ok',
        duration_ms: 0,
        attrs: { intent: 'hvac.set' },
      },
      {
        trace_id: 'abcd1234ef',
        span_id: '2',
        ts: 2,
        service: 'edge',
        node: 'val.execute',
        status: 'ok',
        duration_ms: 8,
        attrs: {
          changes: [
            { key: 'hvac_on', old: false, new: true },
            { key: 'hvac_temp', old: 24, new: 26 },
          ],
        },
      },
    ],
  }

  render(<TracePanel traces={[trace]} />)

  expect(screen.getByText('route.local')).toBeTruthy()
  expect(screen.getByText('val.execute')).toBeTruthy()
  expect(
    document.querySelector('[data-node="val.execute"]')?.className,
  ).toContain('trace-node--val')
  expect(screen.getByText('hvac_on: false → true')).toBeTruthy()
  expect(screen.getByText('hvac_temp: 24 → 26')).toBeTruthy()
})
