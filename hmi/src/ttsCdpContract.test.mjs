import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { validateCloudReleaseSnapshot } from '../../test/hmi_cdp/driver.mjs'

const cases = readFileSync(
  new URL('../../test/hmi_cdp/run_cases.mjs', import.meta.url), 'utf8')
const driver = readFileSync(
  new URL('../../test/hmi_cdp/driver.mjs', import.meta.url), 'utf8')
const settings = readFileSync(new URL('./settings.tsx', import.meta.url), 'utf8')
const app = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8')

test('C14 covers one real HMI business reply for every long-session persona', () => {
  assert.match(cases, /async C14\(cdp\)/)
  for (const persona of ['vehicle', 'family', 'merchant', 'information', 'adversarial']) {
    assert.match(cases, new RegExp(`persona: '${persona}'`))
  }
  assert.match(cases, /provider !== 'minimax'/)
  assert.match(cases, /model !== 'MiniMax-M3'/)
  assert.match(cases, /!call\.pinned/)
})

test('C14 proves MiniMax PCM reached a non-silent browser AudioBuffer', () => {
  assert.match(driver, /Network\.webSocketCreated/)
  assert.match(driver, /response\.opcode/)
  assert.match(driver, /Buffer\.from\(payloadData, 'base64'\)/)
  assert.match(driver, /installAudioProbe/)
  assert.match(driver, /nonzeroRatio/)
  assert.match(driver, /durationMs/)
  assert.match(cases, /female-tianmei/)
  assert.match(cases, /pcmBytes/)
  assert.match(cases, /peak/)
})

test('C14 barge-in proves both provider cancel and local source stop', () => {
  assert.match(cases, /type === 'cancel'/)
  assert.match(cases, /audioProbe\(\)/)
  assert.match(cases, /stopped\.stops/)
  assert.match(cases, /barge/)
})

test('C14 binds its collector to one exact healthy cloud release', () => {
  const sha = 'a'.repeat(40)
  const snapshot = {
    target: 'cloud', status: 'ok', release_sha: sha,
    healthy_endpoints: 5, warnings: [],
    endpoint_results: [
      { name: 'hmi', url: 'https://qa.example.invalid/', status: 'healthy' },
      { name: 'edge', url: 'https://qa.example.invalid:8443/healthz', status: 'healthy' },
      { name: 'audio', url: 'https://qa.example.invalid:8444/healthz', status: 'healthy' },
      { name: 'dashboard', url: 'https://qa.example.invalid:8445/', status: 'healthy' },
      { name: 'collector', url: 'https://qa.example.invalid:8446/healthz', status: 'healthy' },
    ],
  }

  assert.deepEqual(validateCloudReleaseSnapshot(
    snapshot, sha, { collectorUrl: 'https://qa.example.invalid:8446' }), [])
  assert.ok(validateCloudReleaseSnapshot(
    { ...snapshot, target: 'local' }, sha,
    { collectorUrl: 'https://qa.example.invalid:8446' }).length)
  assert.ok(validateCloudReleaseSnapshot(
    snapshot, 'b'.repeat(40),
    { collectorUrl: 'https://qa.example.invalid:8446' }).length)
  assert.ok(validateCloudReleaseSnapshot(
    snapshot, sha, { collectorUrl: 'http://localhost:8092' }).length)
})

test('C14 source takes start and end snapshots and writes release evidence', () => {
  assert.match(cases, /cloudReleaseSnapshot/)
  assert.match(cases, /releaseStart/)
  assert.match(cases, /releaseEnd/)
  assert.match(cases, /writeC14Artifact/)
  assert.match(cases, /CDP_EXPECTED_SHA/)
  assert.match(cases, /Object\.keys\(CASES\)\.filter\(\(id\) => id !== 'C14'\)/)
})

test('HMI LLM selection is request-pinned without startup global hot-switch', () => {
  assert.match(settings, /llm_provider/)
  assert.match(settings, /llm_model/)
  assert.doesNotMatch(app, /syncLlmProvider/)
})
