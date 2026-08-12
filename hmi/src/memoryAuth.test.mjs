import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const audio = readFileSync(join(here, 'audio.ts'), 'utf8')

test('memory forget sends the same configured bearer used by the HMI session', () => {
  assert.match(audio, /import\.meta\.env\.VITE_WS_TOKEN/)
  const start = audio.indexOf('export async function forgetMemory')
  const end = audio.indexOf('export type VoiceprintOccupant', start)
  const body = start >= 0 && end > start ? audio.slice(start, end) : ''
  assert.match(body, /Authorization/)
  assert.match(body, /Bearer \$\{/)
})
