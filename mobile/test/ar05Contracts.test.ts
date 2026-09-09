// AR05 结构化契约在 App 侧的消费（方案 §11 字段表 / §4 / §5）。
//
// 三条主张：
//  1. **有结构化字段就以它为准**，没有（旧网关连键都不带）逐字走既有路径；
//  2. 截止时刻是服务端权威，客户端只读不续期；
//  3. 未知枚举不落到「按最宽松处理」——策略不可信时按高风险，未知恢复动作不给入口。
import { SessionCore, type PendingOp } from '@/core/session/store'
import { derivePresence, type PresenceInput } from '@/core/presence/presence'
import { pinCommitment, sortCommitments, type DockItem } from '@/core/presence/commitment'
import { dismissIssue, mergeIssues, readFinalContracts, type IssueView } from '@/core/session/contracts'
import { commitmentTitle } from '@/core/session/actionSummary'
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'
import { AGENT_CATALOG, DEFAULT_QUICK_COMMANDS, SYSTEM_QUICK_COMMAND_AGENTS } from '@shared/types.ts'
import { serverAgentId, visibleQuickCommands } from '@/core/session/quickCommands'
import { parseSessionSummary } from '@/core/api/sessionInfo'

class FakeTransport {
  sent: any[] = []
  send(frame: object): boolean {
    this.sent.push(frame)
    return true
  }
  sendIfOpen(frame: object): boolean { return this.send(frame) }
  lastUserFrame(): any {
    return [...this.sent].reverse().find((f) => typeof f.text === 'string')
  }
}

function newCore() {
  const transport = new FakeTransport()
  const core = new SessionCore({
    transport,
    sessionId: 'ar05-test',
    getMeta: () => ({ memory_enabled: 'false' }),
    location: { isEnabled: () => false, refreshMeta: async () => ({}), enable: async () => null },
  })
  return { transport, core }
}

const NOW = 1_800_000_000_000

function policyFrame(rid: string, over: Record<string, unknown> = {}) {
  return {
    type: 'final',
    request_id: rid,
    speech: '确认打开后备箱吗？',
    need_confirm: true,
    operation_id: 'op-1',
    confirm_policy: {
      operation_id: 'op-1',
      risk: 'high',
      allowed_channels: ['touch', 'text'],
      action_summary: 'trunk.open（后备箱）',
      object_summary: 'trunk',
      reason_code: 'require_confirm',
      expires_at_ms: NOW + 60_000,
      server_now_ms: NOW,
      summary_source: 'capability',
      target_intent: 'trunk.open',
      ...over,
    },
  }
}

beforeEach(() => {
  jest.useFakeTimers()
  jest.setSystemTime(NOW)
})
afterEach(() => {
  jest.useRealTimers()
})

// ── 台账：策略与截止时刻 ────────────────────────────────────────────────

describe('确认策略进台账', () => {
  test('服务端截止时刻进台账，并且客户端不自己续期', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame(policyFrame(rid))

    const [op] = core.store.getState().pendingOps
    expect(op.id).toBe('op-1')
    expect(op.expiresAtMs).toBe(NOW + 60_000)
    expect(op.policy?.risk).toBe('high')
    expect(op.policy?.actionSummary).toBe('trunk.open（后备箱）')
    // 服务端给的 60s 比本地 300s TTL 短：以服务端为准
    expect(op.expiresAtMs! - op.ts).toBeLessThan(PENDING_TTL_MS)
    core.dispose()
  })

  test('旧网关不带 confirm_policy 时行为逐字不变', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: rid, speech: '确认吗？',
      need_confirm: true, operation_id: 'op-legacy',
    })

    const [op] = core.store.getState().pendingOps
    expect(op.id).toBe('op-legacy')
    expect(op.expiresAtMs).toBeUndefined()
    expect(op.policy).toBeUndefined()
    core.dispose()
  })

  test('补槽请求也进台账并带自己的槽与截止时刻', () => {
    const { transport, core } = newCore()
    core.send('帮我点一杯')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: rid, speech: '要在哪家店？',
      operation_id: 'op-slot',
      slot_request: {
        operation_id: 'op-slot', slot: 'store', display_name: '门店',
        shape: 'item_name', suggestions: ['望京店', '国贸店'],
        state: 'active', remaining_slots: ['store'], prompt: '说个门店名',
        expires_at_ms: NOW + 90_000, server_now_ms: NOW,
      },
    })

    const [op] = core.store.getState().pendingOps
    expect(op.id).toBe('op-slot')
    expect(op.slot?.slot).toBe('store')
    expect(op.slot?.displayName).toBe('门店')
    expect(op.slot?.suggestions).toEqual(['望京店', '国贸店'])
    expect(op.expiresAtMs).toBe(NOW + 90_000)
    core.dispose()
  })

  test('没有 need_confirm 也没有 slot_request 的轮不进台账', () => {
    const { transport, core } = newCore()
    core.send('讲个笑话')      // 中性语料：位置依赖句会先走征询闸，那是另一条用例
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '晴。', operation_id: 'op-x' })

    expect(core.store.getState().pendingOps).toEqual([])
    core.dispose()
  })
})

// ── 结构化问题 ──────────────────────────────────────────────────────────

describe('结构化问题', () => {
  test('issues 进 state，未知恢复动作被丢弃但问题仍在', () => {
    const { transport, core } = newCore()
    core.send('打开车窗')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: rid, speech: '当前账号没有车辆控制权限，这个操作没有执行。',
      issues: [{
        code: 'permission.scope_missing',
        message: '当前账号没有车辆控制权限',
        severity: 'error', scope: 'capability',
        affected_capabilities: ['vehicle.control'],
        recovery: [{ kind: 'rm -rf /', label: '修' },
          { kind: 'open_capability_settings', label: '查看能力与连接' }],
      }],
    })

    const [issue] = core.store.getState().issues
    expect(issue.code).toBe('permission.scope_missing')
    expect(issue.affectedCapabilities).toEqual(['vehicle.control'])
    expect(issue.recovery.map((r) => r.kind)).toEqual(['open_capability_settings'])
    core.dispose()
  })

  test('session 级问题跨轮保留，轮级问题被下一轮覆盖', () => {
    const before: IssueView[] = [
      { code: 'auth.rejected', message: 'token 无效', severity: 'error', scope: 'session',
        requestId: '', operationId: '', affectedCapabilities: [], recovery: [] },
      { code: 'safety.val_rejected', message: '高速行驶中不开窗', severity: 'warning',
        scope: 'request', requestId: 'r1', operationId: '', affectedCapabilities: [], recovery: [] },
    ]
    const merged = mergeIssues(before, [
      { code: 'service.degraded', message: '语音退回批处理', severity: 'warning',
        scope: 'request', requestId: 'r2', operationId: '', affectedCapabilities: [], recovery: [] },
    ])

    expect(merged.map((i) => i.code)).toEqual(['auth.rejected', 'service.degraded'])
  })

  test('同一条 session 级问题不会累积成两行', () => {
    const auth: IssueView = {
      code: 'auth.rejected', message: 'token 无效', severity: 'error', scope: 'session',
      requestId: '', operationId: '', affectedCapabilities: [], recovery: [],
    }
    expect(mergeIssues([auth], [auth]).length).toBe(1)
  })

  test('收起按 code + 归属定位，不按下标', () => {
    const items: IssueView[] = [
      { code: 'safety.val_rejected', message: 'a', severity: 'warning', scope: 'operation',
        requestId: '', operationId: 'op-1', affectedCapabilities: [], recovery: [] },
      { code: 'safety.val_rejected', message: 'b', severity: 'warning', scope: 'operation',
        requestId: '', operationId: 'op-2', affectedCapabilities: [], recovery: [] },
    ]
    expect(dismissIssue(items, 'safety.val_rejected', 'op-1').map((i) => i.operationId)).toEqual(['op-2'])
  })

  test('没有 issues 键时不产生任何问题（旧网关）', () => {
    expect(readFinalContracts({ type: 'final', speech: 'x' }).hasContracts).toBe(false)
    expect(readFinalContracts({ type: 'final', speech: 'x' }).issues).toEqual([])
  })
})

// ── 在场：风险档与补槽 ──────────────────────────────────────────────────

function presenceInput(ops: PresenceInput['pendingOps']): PresenceInput {
  return {
    now: NOW, connStatus: 'open', connChangedAt: NOW - 60_000,
    hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', hfFsmChangedAt: NOW - 60_000,
    ptt: 'idle', partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false, pendingOps: ops, pendingLocation: false, queued: 0,
    uncertainIds: [], lastError: null, identity: 'handheld',
    voicePipeline: 'classic', user: 'u1',
  } as unknown as PresenceInput
}

describe('在场消费真实契约', () => {
  test('服务端说低风险就按低风险；没说仍按高风险（未知不许猜低）', () => {
    const low = derivePresence(presenceInput([
      { id: 'op1', ts: NOW, summary: '调温度', risk: 'low', expiresAt: NOW + 30_000 },
    ])).commitment[0]
    expect(low.kind === 'confirm' && low.risk).toBe('low')

    const unknown = derivePresence(presenceInput([
      { id: 'op2', ts: NOW, summary: '打开后备箱' },
    ])).commitment[0]
    expect(unknown.kind === 'confirm' && unknown.risk).toBe('high')
  })

  test('服务端截止时刻直接进 Dock，不再拿 ts + 本地 TTL 顶替', () => {
    const item = derivePresence(presenceInput([
      { id: 'op1', ts: NOW, summary: 'x', expiresAt: NOW + 45_000 },
    ])).commitment[0]
    expect(item.kind === 'confirm' && item.expiresAt).toBe(NOW + 45_000)
  })

  test('补槽项由 slot_request 产出，held 不抢占当前问题但仍可被选回', () => {
    const snapshot = derivePresence(presenceInput([
      {
        id: 'op-held', ts: NOW, summary: '',
        slot: { missing: '门店', state: 'held', expiresAt: NOW + 60_000, suggestions: [] },
      },
      {
        id: 'op-active', ts: NOW, summary: '',
        slot: { missing: '餐品', state: 'active', expiresAt: NOW + 60_000, suggestions: ['拿铁'] },
      },
    ]))

    const pinned = pinCommitment(snapshot.commitment)
    expect(pinned?.item.id).toBe('op-active')
    expect(pinned?.others).toBe(1)
    expect(snapshot.commitment.map((c) => c.id)).toEqual(['op-active', 'op-held'])
  })

  test('高风险确认仍排在补槽之前', () => {
    const items: DockItem[] = [
      { kind: 'slot', id: 's', missing: '门店', state: 'active', expiresAt: NOW, suggestions: [] },
      { kind: 'confirm', id: 'c', summary: 'x', risk: 'high', expiresAt: NOW + 1 },
    ]
    expect(sortCommitments(items).map((i) => i.id)).toEqual(['c', 's'])
  })
})

// ── 承诺卡标题 ──────────────────────────────────────────────────────────

describe('承诺卡标题', () => {
  const messages = [
    { id: 'm1', role: 'user', text: '打开后备箱' },
    { id: 'm2', role: 'assistant', text: '这项操作可能影响车辆安全，请确认是否继续。', operationId: 'op-1' },
  ] as any

  test('服务端摘要优先', () => {
    expect(commitmentTitle(messages, 'op-1', 'trunk.open（后备箱）')).toBe('trunk.open（后备箱）')
  })

  test('服务端没给才回落上一条用户原话', () => {
    expect(commitmentTitle(messages, 'op-1', '')).toBe('打开后备箱')
    expect(commitmentTitle(messages, 'op-1', '   ')).toBe('打开后备箱')
  })
})

// ── 显式补槽回复入口（方案 §4.2）────────────────────────────────────────

describe('显式补槽回复', () => {
  function withSlot() {
    const { transport, core } = newCore()
    core.send('帮我点一杯')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: rid, speech: '要在哪家店？', operation_id: 'op-slot',
      slot_request: {
        operation_id: 'op-slot', slot: 'store', display_name: '门店',
        suggestions: ['望京店'], state: 'active', remaining_slots: ['store'],
        expires_at_ms: NOW + 90_000, server_now_ms: NOW,
      },
    })
    return { transport, core }
  }

  test('按 operationId 走挂起续接通道，并即时出账', () => {
    const { transport, core } = withSlot()
    core.slotReply('op-slot', '望京店')

    const frame = transport.lastUserFrame()
    expect(frame.text).toBe('望京店')
    expect(frame.is_confirmation).toBe(true)
    expect(frame.operation_id).toBe('op-slot')
    expect(core.store.getState().pendingOps).toEqual([])
    core.dispose()
  })

  test('挂起不存在 / 不是补槽 / 空值 一律不上行', () => {
    const { transport, core } = withSlot()
    const before = transport.sent.length

    core.slotReply('op-nope', '望京店')
    core.slotReply('op-slot', '   ')
    core.slotReply('', '望京店')

    expect(transport.sent.length).toBe(before)
    core.dispose()
  })

  test('过期的补槽点下去不上行（点了没反应好过打错人）', () => {
    const { transport, core } = withSlot()
    const before = transport.sent.length
    jest.setSystemTime(NOW + 91_000)

    core.slotReply('op-slot', '望京店')

    expect(transport.sent.length).toBe(before)
    core.dispose()
  })

  test('同时挂着位置征询时，补槽回复不会被它消费', () => {
    const { transport, core } = withSlot()
    // 造一条位置征询：位置依赖句 + 定位未开启 ⇒ 走征询闸
    core.send('附近的充电站')
    expect(core.store.getState().pendingLocationText).not.toBeNull()

    core.slotReply('op-slot', '望京店')

    const frame = transport.lastUserFrame()
    expect(frame.operation_id).toBe('op-slot')
    expect(frame.text).toBe('望京店')
    // 位置征询原样还在，没被这一下吃掉
    expect(core.store.getState().pendingLocationText).not.toBeNull()
    core.dispose()
  })
})

// ── 首页推荐的能力绑定（方案 §6.2）─────────────────────────────────────

describe('系统推荐与能力摘要对账', () => {
  test('每条系统默认示例都要有能力绑定——漏一条就会永远绕过筛选', () => {
    const missing = DEFAULT_QUICK_COMMANDS.filter((c) => !SYSTEM_QUICK_COMMAND_AGENTS[c])
    expect(missing).toEqual([])
  })

  test('绑定指向的能力必须真的在能力目录里', () => {
    const ids = new Set(AGENT_CATALOG.map((a) => a.id))
    const bad = Object.entries(SYSTEM_QUICK_COMMAND_AGENTS).filter(([, id]) => !ids.has(id))
    expect(bad).toEqual([])
  })

  test('端侧两项的服务端 agent_id 与 Registry 注册名一致', () => {
    expect(serverAgentId('vehicle')).toBe('edge-vehicle')
    expect(serverAgentId('media')).toBe('edge-media')
    // 其余项 UI id 与注册名同名，不需要额外映射
    expect(serverAgentId('reminder')).toBe('reminder')
  })

  test('没有车控授权时车控推荐不再出现，用户自定义短语原样保留', () => {
    const summary = parseSessionSummary({
      summary_status: 'complete',
      capabilities: [{ id: 'chitchat', status: 'available' }],
    })
    const out = visibleQuickCommands(
      [...DEFAULT_QUICK_COMMANDS, '帮我把车开到月球'], summary, {})

    expect(out).not.toContain('打开空调26度')
    expect(out).toContain('讲个笑话')
    expect(out).toContain('帮我把车开到月球')
  })

  test('摘要只取到一半时不筛（此刻查不到 ≠ 你没有这个功能）', () => {
    const partial = parseSessionSummary({ summary_status: 'partial', capabilities: [] })
    expect(visibleQuickCommands(DEFAULT_QUICK_COMMANDS, partial, {}))
      .toEqual([...DEFAULT_QUICK_COMMANDS])
  })
})
