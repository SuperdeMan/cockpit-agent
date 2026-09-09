import test from 'node:test'
import assert from 'node:assert/strict'
import {
  channelAllowed,
  clockSkewOf,
  isExpired,
  parseConfirmPolicy,
  parseFinalContracts,
  parseIssues,
  parseSlotRequest,
} from './contracts.mjs'

// ── 四种「没有值」必须分得开 ───────────────────────────────────────────────

test('缺字段=旧服务端，值为空对象=服务端说没有', () => {
  const legacy = parseFinalContracts({ speech: 'x', need_confirm: true })
  assert.equal(legacy.hasContracts, false)
  assert.equal(legacy.confirmPolicy, null)

  const modern = parseFinalContracts({ speech: 'x', issues: [] })
  assert.equal(modern.hasContracts, true)
  assert.deepEqual(modern.issues, [])
})

test('未知风险档不许落到「按最低风险处理」', () => {
  const p = parseConfirmPolicy({ operation_id: 'op1', risk: 'catastrophic' })
  assert.equal(p.riskKnown, false)
  assert.equal(p.usable, false, '认不出的风险档必须停止确认，不是默认放行')
})

test('已知风险档可用', () => {
  assert.equal(parseConfirmPolicy({ risk: 'high' }).usable, true)
})

test('未知渠道被剔除，剩下的仍然生效', () => {
  const p = parseConfirmPolicy({ risk: 'high', allowed_channels: ['touch', 'telepathy'] })
  assert.deepEqual(p.allowedChannels, ['touch'])
  assert.equal(channelAllowed(p, 'touch'), true)
  assert.equal(channelAllowed(p, 'voice'), false)
})

test('策略缺席时不收窄渠道——回落既有三个入口', () => {
  assert.equal(channelAllowed(null, 'voice'), true)
  assert.equal(channelAllowed({ allowedChannels: [] }, 'voice'), true)
})

// ── 补槽 ──────────────────────────────────────────────────────────────────

test('空建议值给自由输入，不造可点的假选项', () => {
  const r = parseSlotRequest({ operation_id: 'op1', slot: 'store', suggestions: [] })
  assert.deepEqual(r.suggestions, [])
  assert.equal(r.displayName, 'store', '没有中文名就显示机器名，不编')
})

test('建议值原样保留；未知 state 回落 active', () => {
  const r = parseSlotRequest({ slot: 's', suggestions: ['望京店'], state: 'zombie' })
  assert.deepEqual(r.suggestions, ['望京店'])
  assert.equal(r.state, 'active')
})

test('held 状态原样保留——换题后原任务仍在，只是不抢占当前问题', () => {
  assert.equal(parseSlotRequest({ slot: 's', state: 'held' }).state, 'held')
})

// ── 结构化问题 ────────────────────────────────────────────────────────────

test('不认识的恢复 kind 被丢掉，但问题本身仍要显示', () => {
  const [issue] = parseIssues([{
    code: 'permission.scope_missing',
    message: '当前账号没有车辆控制权限',
    recovery: [{ kind: 'rm -rf /', label: '修复' },
      { kind: 'open_capability_settings', label: '查看能力' }],
  }])
  assert.equal(issue.message, '当前账号没有车辆控制权限')
  assert.deepEqual(issue.recovery.map((r) => r.kind), ['open_capability_settings'])
})

test('没有 code 的条目直接丢弃（它无法被分流）', () => {
  assert.deepEqual(parseIssues([{ message: '出错了' }]), [])
})

test('未知 code 保留并显示文案——服务端可能比我新', () => {
  const [issue] = parseIssues([{ code: 'quota.exhausted', message: '额度用完了' }])
  assert.equal(issue.code, 'quota.exhausted')
  assert.equal(issue.severity, 'warning', '缺省 severity 是 warning')
  assert.equal(issue.scope, 'request')
})

test('issues 不是数组时返回空数组，不抛', () => {
  assert.deepEqual(parseIssues(null), [])
  assert.deepEqual(parseIssues('boom'), [])
})

// ── 截止时刻：服务端权威，客户端只读 ──────────────────────────────────────

test('有服务端截止时刻就用它，不用本地 TTL', () => {
  const now = 1_000_000
  const entry = { expiresAtMs: now + 5_000, ts: 0 }
  assert.equal(isExpired(entry, now), false)
  assert.equal(isExpired(entry, now + 6_000), true)
})

test('没有服务端截止时刻才回落本地 TTL', () => {
  const now = 1_000_000
  assert.equal(isExpired({ ts: now - 10 }, now, 300_000), false)
  assert.equal(isExpired({ ts: now - 300_001 }, now, 300_000), true)
})

test('钟差只纠正判定时刻，不改服务端给的那个时刻', () => {
  const now = 1_000_000
  // 本机比服务端慢 10s：服务端认为已经过期了，客户端不能因为自己的表慢就继续显示
  const entry = { expiresAtMs: now + 5_000, clockSkewMs: 10_000 }
  assert.equal(isExpired(entry, now), true)
})

test('钟差采样取服务端此刻减本机此刻；没有就 0', () => {
  assert.equal(clockSkewOf({ serverNowMs: 1_005 }, 1_000), 5)
  assert.equal(clockSkewOf({}, 1_000), 0)
  assert.equal(clockSkewOf(null, 1_000), 0)
})

// ── 整帧投影 ──────────────────────────────────────────────────────────────

test('一帧里可以既有补槽又有问题，两者互不覆盖', () => {
  const parsed = parseFinalContracts({
    slot_request: { operation_id: 'op1', slot: 'store' },
    issues: [{ code: 'service.degraded', message: '语音退回批处理' }],
  })
  assert.equal(parsed.slotRequest.slot, 'store')
  assert.equal(parsed.issues.length, 1)
})
