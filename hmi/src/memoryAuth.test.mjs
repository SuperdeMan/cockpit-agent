import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { postIdentify } from './voiceprintIdentifier.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const audio = readFileSync(join(here, 'audio.ts'), 'utf8')

test('the memory helper sends the same configured bearer used by the HMI session', () => {
  assert.ok(audio.includes('import.meta.env.VITE_WS_TOKEN'))
  const start = audio.indexOf('const memoryAuth')
  assert.ok(start >= 0, 'memoryAuth helper missing')
  const helper = audio.slice(start, audio.indexOf('\n\n', start))
  assert.ok(helper.includes('Authorization'))
  assert.ok(helper.includes('Bearer ${'))
})

test('every memory and voiceprint request carries the bearer (CA2-15 S1)', () => {
  // The gateway acts for the token's owner only; a request without it is refused.
  const marker = '${apiBase}/api/'
  const calls = []
  for (let at = audio.indexOf(marker); at >= 0; at = audio.indexOf(marker, at + 1)) {
    const path = audio.slice(at + marker.length, at + marker.length + 12)
    if (!path.startsWith('memory') && !path.startsWith('voiceprint')) continue
    const close = audio.indexOf('})', at)
    calls.push(audio.slice(at, close + 2))
  }
  assert.ok(calls.length >= 10, `expected every memory/voiceprint fetch, saw ${calls.length}`)
  for (const call of calls) {
    assert.ok(call.includes('memoryAuth()'), `missing bearer: ${call.slice(0, 80)}`)
  }
})

test('speaker identification sends the bearer it is given', async () => {
  const seen = []
  const saved = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    seen.push({ url, headers: init.headers })
    return { ok: true, json: async () => ({ occupant_id: 'primary', decision: 'no_templates' }) }
  }
  try {
    await postIdentify('https://gw', 'u1', 'tok-1')(new Int16Array(4))
    await postIdentify('https://gw', 'u1')(new Int16Array(4))
  } finally {
    globalThis.fetch = saved
  }
  assert.equal(seen[0].headers.Authorization, 'Bearer tok-1')
  assert.ok(!('Authorization' in seen[1].headers))
})
