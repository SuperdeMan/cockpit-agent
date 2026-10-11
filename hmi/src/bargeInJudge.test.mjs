import test from 'node:test'
import assert from 'node:assert/strict'

import { BARGE_IN_JUDGE_PATH, judgeBargeIn } from './bargeInJudge.mjs'

// 假 fetch：记下请求，按脚本回包（或抛错 / 一直不回直到被 abort）
function fakeFetch(reply) {
  const calls = []
  const impl = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) })
    if (reply === 'hang') {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => reject(new Error('aborted')))
      })
    }
    if (reply instanceof Error) throw reply
    return { ok: reply.status === undefined || reply.status < 400, json: async () => reply.body }
  }
  return { impl, calls }
}

const Q = { utterance: ' 妈你到哪了 ', previous: '深圳明天多云。', assistantName: '小航', sessionId: 's-1' }

test('判定请求：路径、Bearer 头与正文字段（原话去首尾空白、正在播的那段作 previous）', async () => {
  const f = fakeFetch({ body: { ok: true, addressed: false, votes: '0,0', ms: 1800 } })
  const v = await judgeBargeIn('https://car.example:8444/', Q, { token: 'tok', fetchImpl: f.impl })
  assert.equal(v, false)
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].url, 'https://car.example:8444' + BARGE_IN_JUDGE_PATH)
  assert.equal(f.calls[0].init.method, 'POST')
  assert.equal(f.calls[0].init.headers.Authorization, 'Bearer tok')
  assert.deepEqual(f.calls[0].body, {
    utterance: '妈你到哪了', previous: '深圳明天多云。', assistant_name: '小航', session_id: 's-1' })
})

test('判定请求：只认布尔 addressed，其余一律判不出（null）', async () => {
  for (const [body, expected] of [
    [{ addressed: true }, true], [{ addressed: false }, false], [{ addressed: null }, null],
    [{ addressed: 'true' }, null], [{}, null], [null, null],
  ]) {
    const f = fakeFetch({ body })
    assert.equal(await judgeBargeIn('http://gw', Q, { fetchImpl: f.impl }), expected, JSON.stringify(body))
  }
})

test('判定请求：鉴权失败 / 旧网关没有这个接口 / 网络错误 ⇒ 判不出，不抛', async () => {
  for (const reply of [{ status: 401, body: { ok: false } }, { status: 404, body: {} }, new Error('offline')]) {
    const f = fakeFetch(reply)
    assert.equal(await judgeBargeIn('http://gw', Q, { fetchImpl: f.impl }), null)
  }
})

test('判定请求：到点没回 ⇒ 主动放弃（abort）并判不出', async () => {
  const f = fakeFetch('hang')
  const started = Date.now()
  assert.equal(await judgeBargeIn('http://gw', Q, { fetchImpl: f.impl, timeoutMs: 30 }), null)
  assert.ok(Date.now() - started < 2000)
})

test('判定请求：没有原话或没有基址 ⇒ 不发请求、判不出；没有 token 不带 Authorization', async () => {
  const f = fakeFetch({ body: { addressed: true } })
  assert.equal(await judgeBargeIn('http://gw', { utterance: '  ' }, { fetchImpl: f.impl }), null)
  assert.equal(await judgeBargeIn('', Q, { fetchImpl: f.impl }), null)
  assert.equal(f.calls.length, 0)
  await judgeBargeIn('http://gw', Q, { fetchImpl: f.impl })
  assert.equal('Authorization' in f.calls[0].init.headers, false)
})
