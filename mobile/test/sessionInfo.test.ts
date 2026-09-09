// 会话身份与能力摘要的取数（AR05 §6.1 / §6.3）。
//
// 核心是**四种失败不许混成一种**：明确认证拒绝 / 旧服务端 / 网络未知 / 只取到一半。
// 混成一种的代价很具体：把超时判成 token 失效，用户会在信号不好的地方反复重配
// 一份完全正确的配置。
import {
  fetchSessionInfo,
  parseSessionSummary,
  sessionInfoUrl,
  summaryStale,
} from '@/core/api/sessionInfo'

const EDGE = 'https://vehicle.example.ts.net:8443'
const TOKEN = 'tok-secret'

function fakeFetch(res: Partial<Response> & { json?: () => Promise<unknown> }) {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const impl = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    return { ok: true, status: 200, json: async () => ({}), ...res } as Response
  }) as unknown as typeof fetch
  return { impl, calls }
}

test('token 只走 Authorization 头，不进 URL（URL 会进访问日志）', async () => {
  const { impl, calls } = fakeFetch({})
  await fetchSessionInfo(EDGE, TOKEN, impl)

  expect(calls[0].url).toBe(sessionInfoUrl(EDGE))
  expect(calls[0].url).not.toContain(TOKEN)
  expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`)
})

test('401 / 403 = 服务端明确拒绝这个 token', async () => {
  for (const status of [401, 403]) {
    const { impl } = fakeFetch({ ok: false, status })
    expect(await fetchSessionInfo(EDGE, TOKEN, impl)).toEqual({ kind: 'unauthorized' })
  }
})

test('404 = 旧服务端，不是鉴权失败', async () => {
  const { impl } = fakeFetch({ ok: false, status: 404 })
  expect(await fetchSessionInfo(EDGE, TOKEN, impl)).toEqual({ kind: 'legacy' })
})

test('网络错误/超时 = 未知，绝不判成 token 失效', async () => {
  const impl = (async () => { throw new Error('Network request failed') }) as unknown as typeof fetch
  const r = await fetchSessionInfo(EDGE, TOKEN, impl)
  expect(r.kind).toBe('unknown')
  expect(r.kind === 'unknown' && r.reason).toContain('Network request failed')
})

test('503 也是未知，不是拒绝', async () => {
  const { impl } = fakeFetch({ ok: false, status: 503 })
  const r = await fetchSessionInfo(EDGE, TOKEN, impl)
  expect(r.kind).toBe('unknown')
})

test('没配服务器时直接给未知，不发请求', async () => {
  const { impl, calls } = fakeFetch({})
  expect((await fetchSessionInfo('', TOKEN, impl)).kind).toBe('unknown')
  expect(calls).toHaveLength(0)
})

test('解析：三种能力状态原样保留，未知状态不被改写成可用', () => {
  const s = parseSessionSummary({
    contract_version: 'ar05.1',
    authenticated: true,
    user_id: 'u1',
    vehicle_id: 'v1',
    authorization_source: 'token',
    granted_scopes: ['vehicle.control', 123],
    capabilities: [
      { id: 'edge-vehicle', display_name: '车辆控制', status: 'available', reason_code: '' },
      { id: 'edge-media', status: 'unauthorized', reason_code: 'scope_missing' },
      { id: 'reminder', status: 'unknown', reason_code: 'query_failed' },
      { status: 'available' },
    ],
    generated_at_ms: 100,
    expires_at_ms: 200,
    summary_status: 'partial',
    summary_reason: 'cloud_unreachable',
  })

  expect(s.grantedScopes).toEqual(['vehicle.control'])
  expect(s.capabilities.map((c) => `${c.id}:${c.status}`)).toEqual([
    'edge-vehicle:available', 'edge-media:unauthorized', 'reminder:unknown',
  ])
  // 没有 id 的行无法指认，丢弃
  expect(s.capabilities).toHaveLength(3)
  // 没给 display_name 就回落 id，不编名字
  expect(s.capabilities[1].displayName).toBe('edge-media')
  expect(s.summaryStatus).toBe('partial')
  expect(s.summaryReason).toBe('cloud_unreachable')
})

test('解析：旧服务端没有 summary_status 时按 complete 处理', () => {
  expect(parseSessionSummary({ user_id: 'u1' }).summaryStatus).toBe('complete')
})

test('解析：PoC 默认放行与 token 授权是两个来源', () => {
  expect(parseSessionSummary({ authorization_source: 'poc_default' }).authorizationSource)
    .toBe('poc_default')
  expect(parseSessionSummary({ authorization_source: 'token' }).authorizationSource).toBe('token')
})

test('摘要过期后标待刷新；没有有效期就不判过期', () => {
  const base = parseSessionSummary({ generated_at_ms: 100, expires_at_ms: 200 })
  expect(summaryStale(base, 199)).toBe(false)
  expect(summaryStale(base, 200)).toBe(true)
  expect(summaryStale(parseSessionSummary({}), 10_000)).toBe(false)
})
