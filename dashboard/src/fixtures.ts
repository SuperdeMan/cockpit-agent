// Explicitly synthetic, deterministic visual QA data. No production transport is
// reachable when ?fixture= is present, including unrecognised fixture names.
import type { AgentInfo, CollectorMeta, LlmCall, LogEntry, Span, Turn, TurnDetail, VehicleSignal, VehicleState } from './types'

export const FIXTURE_NOW = Date.parse('2026-10-09T00:10:00+08:00')
export const FIXTURE_TRACE = 'f17a000000000001'
export function fixtureName() { return new URLSearchParams(window.location.search).get('fixture') }
export function isFixture() { return fixtureName() !== null }
export function observationNow() { return isFixture() ? FIXTURE_NOW : Date.now() }

const BASE_TURN: Turn = {
  trace_id: FIXTURE_TRACE, session_id: 'demo-fixture-navigation', ts: FIXTURE_NOW - 31000,
  duration_ms: 6400, user_text: '去海岸城要开多久', speech: '地图服务暂时不可用，没查到「海岸城」，请稍后再试。',
  status: 'ok', path: 'cloud', input_source: 'text', is_confirmation: false, ui_card_type: '', actions: 0,
  error: '', badcase: 0, note: '', intents: 'navigation.estimate', plan_mode: 'toolcall',
  outcome: 'completed', outcome_category: 'progress', origin: 'hmi', warning_count: 6,
}

const EXAMPLES: Partial<Turn>[] = [
  {},
  { user_text: '帮我估算一下去公司的时间', speech: '路况暂时读不到，请稍后再试。', duration_ms: 5900 },
  { user_text: '打开车窗', speech: '车窗会全部打开，要继续吗？', status: 'need_confirm', outcome: 'pending_confirm', duration_ms: 2100, intents: 'window.open', warning_count: 0 },
  { user_text: '第一场开始前提醒我观看', speech: '想在开赛前多久提醒你？', outcome: 'pending_slot', duration_ms: 7800, intents: 'reminder.create', warning_count: 0 },
  { user_text: '找一家附近评分高的川菜馆', speech: '找到了 3 家餐馆，请选择目的地。', duration_ms: 9600, edge_nlu: 'nearby.search != navigation.start', warning_count: 0 },
  { user_text: '查一下今天人工智能行业的重要新闻', speech: '这个问题我暂时处理不了，请稍后再试。', outcome: 'planner_failure', outcome_category: 'dependency_or_planner_failure', plan_mode: 'toolcall_degraded', duration_ms: 12400, origin: 'probe', warning_count: 0 },
  { user_text: '关闭后视镜加热', speech: '已关闭后视镜加热。', duration_ms: 2.6, path: 'local', outcome: '', outcome_category: '', intents: 'mirror.heat', warning_count: 0 },
  { user_text: '帮我把副驾座椅调成按摩模式', speech: '这项操作需要车主授权。', outcome: 'permission_missing', outcome_category: 'permission_missing', origin: 'app', duration_ms: 3300, warning_count: 0 },
  { user_text: '哪个先不用了', speech: '', status: 'rejected', outcome: 'not_addressed', outcome_category: 'not_addressed', origin: 'app', warning_count: 0 },
  { user_text: '取消导航', speech: '已取消导航。', outcome: 'cancelled', outcome_category: 'session_control', duration_ms: 1000, warning_count: 0 },
  { user_text: '那个提醒不用了，取消吧', speech: '没找到要取消的提醒。', outcome: 'cancel_unresolved', outcome_category: 'ambiguous', duration_ms: 2600, warning_count: 0 },
  { user_text: '今天的天气适合去哪玩', speech: '今天晴，适合户外。附近的推荐暂时没取到。', outcome: 'partial', duration_ms: 11200, warning_count: 2 },
  { user_text: '空调调到26度', speech: '已为你把空调调到 26 度。', path: 'local', outcome: '', outcome_category: '', duration_ms: 2.8, warning_count: 0, origin: 'dashboard', intents: 'hvac.set' },
]

export const fixtureTurns: Turn[] = EXAMPLES.map((example, index) => ({
  ...BASE_TURN, trace_id: 'f17a' + (index + 1).toString().padStart(12, '0'),
  session_id: index < 2 ? BASE_TURN.session_id : 'demo-fixture-' + index,
  ts: index < 9 ? BASE_TURN.ts - index * 61000 : BASE_TURN.ts - 12 * 60000 - (index - 9) * 61000,
  badcase: index === 1 || index === 5 ? 1 : 0, note: index === 1 || index === 5 ? '离线示例：复核服务降级后的表达' : '',
  ...example,
}))

function makeSpan(node: string, end: number, duration = 0, attrs: Record<string, unknown> = {}, status = 'ok', index = 0): Span {
  return { trace_id: FIXTURE_TRACE, span_id: 'fixture-span-' + node + '-' + index, ts: BASE_TURN.ts + end, service: node.startsWith('provider') ? 'navigation' : 'orchestrator', node, status, duration_ms: duration, attrs }
}
const fixtureSpans: Span[] = [
  makeSpan('route.cloud', 32, 0, { path: 'cloud' }),
  makeSpan('nlu.shadow', 54, 0, { shadow: true }),
  makeSpan('cloud.planning', 2720, 0, { plan: JSON.stringify({ steps: [{ id: 's1', agent_id: 'navigation', intent: 'navigation.estimate', slots: { destination: '海岸城' }, depends_on: [] }] }), llm_raw: '离线示例：规划输出', plan_mode: 'toolcall' }),
  makeSpan('step.agent:navigation', 5790, 2690, { intent: 'navigation.estimate', agent_id: 'navigation', kind: 'agent', deployment: 'cloud' }),
  ...[2840, 2940, 5480, 5540, 5600, 5660, 5720, 5780].map((end, i) => makeSpan('provider.amap.place_' + (i % 2 ? 'text' : 'around'), end, 49, { source: 'fixture' }, 'ok', i)),
  makeSpan('decision.shadow', 3380, 660, { shadow: true }),
  makeSpan('aggregate', 6290), makeSpan('cloud.outcome', 6380, 0, { kind: 'completed' }),
]
function makeCall(caller: string, end: number, latency: number, index: number): LlmCall {
  return { id: index, trace_id: FIXTURE_TRACE, ts: BASE_TURN.ts + end, caller, model: 'MiniMax-M3', provider: 'minimax', fallback: 0, pinned: 0, requested_tier: 'fast', prompt_tokens: 9726 + index, completion_tokens: index === 1 ? 10 : 137, latency_ms: latency, cache_hit: 0, thinking: 0, status: 'ok', error: '', prompt_tail: '离线示例：上下文末段', content_head: '离线示例：输出内容' }
}
const fixtureCalls = [makeCall('cloud-planner', 1150, 950, 1), makeCall('cloud-planner', 2770, 1570, 2), makeCall('navigation', 5580, 2280, 3)]
export const fixtureLogs: LogEntry[] = Array.from({ length: 12 }, (_, i) => ({
  id: i + 1, ts: BASE_TURN.ts + [32, 1050, 1080, 2670, 2730, 2780, 2930, 5490, 5530, 5570, 5610, 5650][i],
  service: i < 6 ? 'orchestrator' : 'navigation', level: i < 6 ? 'INFO' : 'WARNING', logger: 'fixture',
  msg: i < 6 ? '离线示例：完成链路步骤 ' + (i + 1) : '离线示例：destination POI search failed: provider quota unavailable (' + (i % 2 ? 'place_text' : 'place_around') + ')',
  trace_id: FIXTURE_TRACE, session_id: BASE_TURN.session_id,
}))

export function fixtureDetail(trace: string): TurnDetail | { error: string } {
  if (fixtureName() === 'missing' || !fixtureTurns.some(t => t.trace_id === trace)) return { error: 'not found' }
  const turn = fixtureTurns.find(t => t.trace_id === trace)!
  const local = turn.path === 'local'
  const spans = turn.outcome === 'pending_confirm' ? [
    makeSpan('route.cloud', 20), makeSpan('cloud.planning', 800, 0, { plan: JSON.stringify({ steps: [{ id: 's1', agent: 'edge-vehicle', intent: 'window.open' }] }) }),
    makeSpan('step.edge:vehicle', 1800, 700, { intent: 'window.open', result: 'NEED_CONFIRM' }, 'need_confirm'),
    makeSpan('suspended', 2000, 0, {}, 'need_confirm'), makeSpan('cloud.outcome', 2080, 0, { kind: 'pending_confirm' }),
  ] : local ? [
    makeSpan('val.execute', 2, .3, { changes: [{ key: 'hvac_temp', old: 24, new: 26 }, { key: 'hvac_on', old: false, new: true }] }),
    makeSpan('route.local', 2.8, 0, { intent: 'hvac.set' }), makeSpan('nlu.shadow', 22, 0, { shadow: true }),
  ] : fixtureSpans
  const calls = turn.outcome === 'pending_confirm' ? [makeCall('cloud-planner', 760, 700, 1)] : fixtureCalls
  const logs = turn.outcome === 'pending_confirm' ? [{ ...fixtureLogs[0], ts: BASE_TURN.ts + 1800, service: 'edge', msg: '离线示例：NEED_CONFIRM，动作等待确认。' }] : fixtureLogs
  return { turn, spans: spans.map(s => ({ ...s, trace_id: trace, ts: s.ts - BASE_TURN.ts + turn.ts })),
    llm_calls: local ? [] : calls.map(c => ({ ...c, trace_id: trace, ts: c.ts - BASE_TURN.ts + turn.ts })),
    logs: local ? [] : logs.map(l => ({ ...l, trace_id: trace, ts: l.ts - BASE_TURN.ts + turn.ts })) }
}

export const fixtureVehicle: VehicleState = { hvac_on: true, hvac_temp: 26, hvac_wind_speed: 3, fragrance: false, steering_wheel_heating: false, seat_heating: true, seat_ventilation: false, seat_massage: false, window: 0, sunroof: 0, door_lock: 'locked', trunk: 'closed', wiper: false, headlight: false, rear_view_mirror: 'unfolded', ambient_light: true, ambient_light_color: 'green', ambient_light_brightness: 60, media: 'playing', volume: 35, screen_brightness: 70, driving_mode: 'comfort', cabin_temp: 25, child_lock: true, warning_light: null, speed_kmh: 0, battery: 78, gear: 'P' }
export const fixtureSignals: Record<string, VehicleSignal> = Object.fromEntries(Object.keys(fixtureVehicle).map(key => [key, { quality: key === 'warning_light' ? 'unavailable' : 'good', source_kind: 'simulated', authenticated: true, observed_at_ms: FIXTURE_NOW, expires_at_ms: FIXTURE_NOW + 60000 }]))
export const fixtureAgents: Record<string, AgentInfo> = Object.fromEntries(['navigation', 'info', 'chitchat', 'manual', 'music', 'reminder', 'scene', 'vehicle', 'charging', 'mcp-bridge', 'planner', 'edge-fast'].map((name, index) => [name, { healthy: true, kind: index > 9 ? 'edge_fast' : 'agent', deployment: 'cloud', last_seen: FIXTURE_NOW - 2000, ...(index < 3 ? { count: 128 - index * 31, avg_ms: 86 + index * 41, error_rate: 0 } : {}) }]))

export const fixtureMeta: CollectorMeta = { content_capture: true, retention_days: 7, debug_vehicle_control: true, query_features: ['turn_filters', 'turn_pagination', 'session_pagination'] }

export function fixtureResponse(path: string, params: URLSearchParams, init: RequestInit = {}): Response {
  const name = fixtureName() || 'turns'
  const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
  if (name === 'token-invalid') return response({ error: 'unauthorized' }, 401)
  if (name === 'token-not-configured') return response({ error: 'operator access not configured' }, 503)
  if (name === 'token-unreachable') throw new TypeError('Fixture collector unreachable')
  if (name === 'error' && path !== '/api/meta') return response({ error: 'fixture unavailable' }, 500)
  if (path === '/api/meta') return response({ ...fixtureMeta, content_capture: name !== 'content-off', debug_vehicle_control: name !== 'live-disabled' })
  if (path === '/api/search') {
    let turns = name === 'empty' || name === 'filtered' ? [] : [...fixtureTurns]
    const query = (params.get('q') || '').toLowerCase()
    turns = turns.filter(t => (!query || [t.user_text, t.speech, t.trace_id].some(v => v.toLowerCase().includes(query)))
      && (!params.get('badcase') || !!t.badcase) && (!params.get('session') || t.session_id === params.get('session'))
      && ['origin', 'outcome'].every(k => !params.get(k) || params.get(k)!.split(',').includes(String(t[k as keyof Turn])))
      && (!params.get('category') || params.get('category')!.split(',').includes(t.outcome_category || ''))
      && (!params.get('since') || t.ts >= Number(params.get('since')))
      && (!params.get('min_duration_ms') || t.duration_ms >= Number(params.get('min_duration_ms')))
      && (!params.get('has_warnings') || !!t.warning_count)
      && (!params.get('edge_disagreement') || t.edge_nlu?.includes('!='))
      && (!params.get('actionability_disagreement') || t.actionability?.includes('!='))
      && (!params.get('labeled') || !!t.gold_intents)
      && (!params.get('degraded') || /_degraded|_fallback|_salvage/.test(t.plan_mode || '')))
    const limit = Number(params.get('limit') || 200), offset = Number(params.get('offset') || 0)
    if (name === 'legacy') return response(turns.slice(0, limit).map(({ origin: _origin, outcome_category: _category, warning_count: _warnings, ...turn }) => turn))
    return response(params.has('paginated') ? { items: turns.slice(offset, offset + limit), total: turns.length, limit, offset } : turns.slice(0, limit))
  }
  if (path === '/api/sessions') return response(name === 'empty' ? [] : [...new Set(fixtureTurns.map(t => t.session_id))].map(session_id => {
    const turns = fixtureTurns.filter(t => t.session_id === session_id)
    return { session_id, first_ts: turns[turns.length - 1].ts, last_ts: turns[0].ts, turns: turns.length, errors: 0, rejected: 0, badcases: turns.filter(t => t.badcase).length, first_user_text: turns[turns.length - 1].user_text, origin: turns[0].origin }
  }))
  if (/^\/api\/sessions\/.+\/turns$/.test(path)) return response(fixtureTurns.filter(t => t.session_id === decodeURIComponent(path.split('/')[3])))
  if (path === '/api/logs') return response(name === 'empty' ? [] : fixtureLogs.filter(l => (!params.get('service') || l.service === params.get('service')) && (!params.get('level') || l.level === params.get('level')) && (!params.get('q') || l.msg.includes(params.get('q')!))))
  if (path === '/api/intents/observed') return response(['navigation.estimate', 'hvac.set', 'reminder.create', 'window.open'])
  if (path === '/api/llm/summary') return response({ hours: Number(params.get('hours') || 24), groups: name === 'empty' ? [] : [
    { caller: 'cloud-planner', model: 'MiniMax-M3', calls: 81, prompt_tokens: 526730, completion_tokens: 12770, errors: 2, avg_latency_ms: 1438, last_ts: FIXTURE_NOW, fallback_calls: 0, zero_usage_calls: 0 },
    { caller: '(未归属)', model: 'MiniMax-M3', calls: 397, prompt_tokens: 696100, completion_tokens: 87630, errors: 0, avg_latency_ms: 2014, last_ts: FIXTURE_NOW, fallback_calls: 0, zero_usage_calls: 0 },
    { caller: 'chitchat', model: 'MiniMax-M3', calls: 60, prompt_tokens: 0, completion_tokens: 0, errors: 0, avg_latency_ms: 796, last_ts: FIXTURE_NOW - 10000, fallback_calls: 0, zero_usage_calls: 60 },
  ] })
  const trace = decodeURIComponent(path.split('/')[3] || '')
  if (init.method === 'POST') {
    const turn = fixtureTurns.find(t => t.trace_id === trace)
    const data = JSON.parse(String(init.body || '{}'))
    if (turn && path.endsWith('/badcase')) { turn.badcase = data.badcase ? 1 : 0; turn.note = data.note || '' }
    else if (turn && path.endsWith('/label')) turn.gold_intents = data.gold_intents || ''
    else if (path === '/api/debug/vehicle') {
      if (name === 'live-disabled') return response({ ok: false, error: 'debug disabled' }, 403)
      return response({ ok: true })
    } else return response({ error: 'fixture operation unavailable' }, 400)
    return response({ ok: true })
  }
  if (path.startsWith('/api/turns/') || path.startsWith('/api/export/')) {
    const detail = fixtureDetail(trace)
    if (name === 'live-running' && 'turn' in detail) return response({ ...detail, turn: null, spans: detail.spans.filter(s => s.node !== 'nlu.shadow'), llm_calls: [], logs: [] })
    if (name === 'content-off' && 'turn' in detail && detail.turn) return response({ ...detail, turn: { ...detail.turn, user_text: '<len=9 sha=a1b2c3d4>', speech: '<len=32 sha=b2c3d4e5>' }, spans: detail.spans.map(s => ({ ...s, attrs: s.node === 'cloud.planning' ? { plan: '<len=120 sha=c3d4e5f6>' } : s.attrs })), llm_calls: detail.llm_calls.map(c => ({ ...c, prompt_tail: '<len=160 sha=d4e5f6a7>', content_head: '<len=45 sha=e5f6a7b8>' })) })
    return response(detail)
  }
  return response({ error: 'unknown offline fixture endpoint' }, 404)
}
