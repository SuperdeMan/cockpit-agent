import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

// Run the actual exported upload functions with HTTP replaced; no microphone or server.
const source = ts.createSourceFile('audio.ts', readFileSync(new URL('./audio.ts', import.meta.url), 'utf8'), ts.ScriptTarget.ES2020, true)
const functions = source.statements.filter(node => ts.isFunctionDeclaration(node)
  && ['enrollVoiceprint', 'identifySpeaker'].includes(node.name?.text)).map(node => node.getText(source)).join('\n')
assert.equal(source.statements.filter(node => ts.isFunctionDeclaration(node)
  && ['enrollVoiceprint', 'identifySpeaker'].includes(node.name?.text)).length, 2)
const compiled = ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } }).outputText

function harness() {
  const requests = []
  const api = {}
  vm.runInNewContext(compiled, { exports: api, Blob, FormData,
    memoryAuth: () => ({ Authorization: 'Bearer test-only' }),
    fetch: async (url, init) => { requests.push({ url, ...init }); return { json: async () => ({ ok: true, decision: 'accept' }) } },
  })
  return { api, requests }
}

test('voiceprint uploads preserve only the PCM view bytes for ordinary and shared buffers', async () => {
  for (const BufferType of [ArrayBuffer, SharedArrayBuffer]) {
    const buffer = new BufferType(10)
    const all = new Int16Array(buffer)
    all.set([111, -32768, 1200, 32767, 222])
    const clip = all.subarray(1, 4)
    const expected = [-32768, 1200, 32767]
    const { api, requests } = harness()
    await api.enrollVoiceprint('https://fixture.invalid', 'test-user', 'Test', [clip], 'pcm16le', 'occ-existing')
    await api.identifySpeaker('https://fixture.invalid', 'test-user', clip)
    const sample = requests[0].body.get('sample')
    assert.deepEqual([...new Int16Array(await sample.arrayBuffer())], expected)
    assert.deepEqual([...new Int16Array(await requests[1].body.arrayBuffer())], expected)
    assert.ok(requests[0].url.endsWith('&format=pcm16le&occupant_id=occ-existing'))
    assert.ok(requests[1].url.endsWith('&format=pcm16le'))
    assert.ok(requests.every(r => r.method === 'POST' && r.headers.Authorization === 'Bearer test-only'))
    assert.deepEqual([...all], [111, ...expected, 222], 'upload never mutates the source samples')
  }
})

test('existing Blob clips remain supported by enrollment and identification', async () => {
  const { api, requests } = harness()
  const clip = new Blob([new Uint8Array([1, 2, 3, 4])])
  await api.enrollVoiceprint('https://fixture.invalid', 'test-user', 'Test', [clip])
  await api.identifySpeaker('https://fixture.invalid', 'test-user', clip)
  assert.deepEqual([...new Uint8Array(await requests[0].body.get('sample').arrayBuffer())], [1, 2, 3, 4])
  assert.equal(requests[1].body, clip)
})
