import { genTraceId } from './CommandBar'

test('genTraceId returns 16 hex chars', () => {
  expect(genTraceId()).toMatch(/^[0-9a-f]{16}$/)
})
