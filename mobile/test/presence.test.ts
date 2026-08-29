// mobile/test/presence.test.ts
// derivePresence（方案 §4.1）——多轴事实 + 单一视觉主态。
// 两组主张：
//  A. primary 的优先级固定：offline > attention > looking > listening/recognizing > speaking
//     > thinking/processing > followup > armed > idle（逐对交换即红）
//  B. **轴独立**：改 transport 不许动 commitment；这是多轴与单枚举的可测差别——
//     评审那个案例（待确认时断网，确认被盖掉）在这里有一条专门的断言
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { derivePresence, type PresenceInput } from '@/core/presence/presence'

const NOW = 5_000_000

function base(over: Partial<PresenceInput> = {}): PresenceInput {
  return {
    now: NOW,
    connStatus: 'open',
    connChangedAt: NOW - 60_000,
    hfEnabled: false,
    hfUsable: false,
    hfFsm: 'IDLE',
    ptt: 'idle',
    partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false,
    pendingOps: [],
    pendingLocation: false,
    voicePipeline: 'classic',
    visionCapturing: false,
    queued: 0,
    lastError: null,
    degradations: [],
    driving: false,
    identity: 'handheld',
    user: 'u1',
    ...over,
  }
}

describe('A. primary 优先级', () => {
  const cases: Array<[string, Partial<PresenceInput>, string]> = [
    ['idle', {}, 'idle'],
    ['armed', { hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' }, 'armed'],
    ['followup', { hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' }, 'listening'],
    ['thinking', { turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 } }, 'thinking'],
    ['processing', { turn: { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW - 1000 } }, 'thinking'],
    ['speaking', { speaking: true }, 'speaking'],
    ['listening(PTT)', { ptt: 'recording' }, 'listening'],
    ['listening(hf)', { hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING' }, 'listening'],
    ['looking', { visionCapturing: true }, 'looking'],
    ['attention', { pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }] }, 'attention'],
    ['offline', { connStatus: 'closed', connChangedAt: NOW - 30_000 }, 'muted'],
  ]
  test.each(cases)('%s → primary=%s', (_name, over, expected) => {
    expect(derivePresence(base(over)).primary).toBe(expected)
  })

  test('逐对交换：更高档在场时低档不得胜出', () => {
    // attention 与 listening 同时在场 → attention
    const both = base({
      pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }],
      ptt: 'recording',
    })
    expect(derivePresence(both).primary).toBe('attention')
    // attention 与 looking 同时在场 → attention
    // （相邻两档只有这一条测得出来：上面那条 attention+PTT 的 capture 是 listening 不是 looking，
    //   交换 attention/looking 两行时它照样绿——反向验证据此补进来的）
    expect(
      derivePresence(
        base({ pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }], visionCapturing: true }),
      ).primary,
    ).toBe('attention')
    // looking 与 speaking 同时在场 → looking
    expect(derivePresence(base({ visionCapturing: true, speaking: true })).primary).toBe('looking')
    // offline 压过一切
    expect(
      derivePresence(base({ connStatus: 'closed', connChangedAt: NOW - 30_000, ptt: 'recording' })).primary,
    ).toBe('muted')
  })

  // 第 1 批遗留②：上面那条的名字强于它的覆盖——补齐余下四对。每条都让**被比较的两态同时在场**，
  // 判据是「交换那一对的两行即红」（本批逐对做过变异验证，读数进 §6.2）。
  // ⚠ 其中两对（speaking↔thinking / thinking↔followup）的裁决**不在 primary 链上**：
  //   primary 那两行都读 `agent`，而 agent 是单值 ⇒ 两个分支永不同真，交换它们是空操作。
  //   真正的裁决点在 agent 轴自己的优先级里，所以这两对断言同时钉 `agent` 与 `primary`。
  test('共存④ listening↔speaking：在听时对方开播 → 光球仍 listening（麦优先于嘴）', () => {
    const both = base({ ptt: 'recording', speaking: true })
    const snap = derivePresence(both)
    expect(snap.capture).toBe('listening')
    expect(snap.agent).toBe('speaking')
    expect(snap.primary).toBe('listening')
  })

  test('共存⑤ speaking↔thinking：边播报边有在飞轮 → speaking（裁决在 agent 轴）', () => {
    const both = base({
      speaking: true,
      turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    })
    const snap = derivePresence(both)
    expect(snap.agent).toBe('speaking')
    expect(snap.primary).toBe('speaking')
  })

  test('共存⑥ thinking↔followup：接话窗内又发起了一轮 → thinking（裁决在 agent 轴）', () => {
    const both = base({
      hfEnabled: true,
      hfUsable: true,
      hfFsm: 'FOLLOWUP',
      turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    })
    const snap = derivePresence(both)
    expect(snap.agent).toBe('thinking')
    expect(snap.primary).toBe('thinking')
  })

  test('共存⑦ followup↔armed：FOLLOWUP 同时满足 capture=armed 与 agent=followup → listening', () => {
    const snap = derivePresence(base({ hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' }))
    expect(snap.capture).toBe('armed')
    expect(snap.agent).toBe('followup')
    expect(snap.primary).toBe('listening')
  })

  test('error 只在无在飞轮时短显 4s，之后回 idle', () => {
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 1000 } })).primary).toBe('idle')
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 1000 } })).capsule?.text).toBe('出错了')
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 4001 } })).capsule).toBeUndefined()
  })
})

describe('B. 轴独立', () => {
  test('评审案例：待确认时断网——Dock 仍钉着确认，胶囊说断开，光球 muted', () => {
    const snap = derivePresence(
      base({
        connStatus: 'closed',
        connChangedAt: NOW - 30_000,
        pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }],
        queued: 1,
      }),
    )
    expect(snap.primary).toBe('muted')
    expect(snap.capsule?.text).toContain('已断开')
    expect(snap.commitment.map((c) => c.kind)).toEqual(['confirm', 'queue'])
    expect(snap.transport).toBe('offline')
  })
  test('transport 变化不动 commitment（逐轴断言）', () => {
    const ops = [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }]
    const online = derivePresence(base({ pendingOps: ops }))
    const offline = derivePresence(base({ pendingOps: ops, connStatus: 'closed', connChangedAt: NOW - 30_000 }))
    expect(offline.commitment).toEqual(online.commitment)
  })
  test('reconnecting 3s 内不显示（沿用弱网横幅 3s 延迟）', () => {
    expect(derivePresence(base({ connStatus: 'connecting', connChangedAt: NOW - 1000 })).capsule).toBeUndefined()
    expect(derivePresence(base({ connStatus: 'connecting', connChangedAt: NOW - 3500 })).capsule?.text).toBe('正在重连…')
  })
  test('privacy 轴：唤醒词待机=edge；端到端录音中=cloudAudio；PTT=edge（三段式只上传文字）', () => {
    expect(derivePresence(base({ hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' })).privacy.mic).toBe('edge')
    expect(
      derivePresence(base({ hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING', voicePipeline: 's2s' })).privacy.mic,
    ).toBe('cloudAudio')
    expect(derivePresence(base({ ptt: 'recording' })).privacy.mic).toBe('edge')
    expect(derivePresence(base()).privacy.mic).toBe('off')
    expect(derivePresence(base({ visionCapturing: true })).privacy.camera).toBe('singleFrame')
  })
})

describe('commitment 构造', () => {
  test('pendingOps → confirm 项：expiresAt = ts + PENDING_TTL_MS，摘要来自输入', () => {
    const snap = derivePresence(base({ pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }] }))
    expect(snap.commitment[0]).toEqual({
      kind: 'confirm',
      id: 'op1',
      summary: '打开后备箱',
      risk: 'high',
      expiresAt: NOW - 1000 + PENDING_TTL_MS,
    })
  })
  test('位置授权征询 → confirm(subkind=location)，risk=low', () => {
    const snap = derivePresence(base({ pendingLocation: true }))
    expect(snap.commitment[0]).toMatchObject({ kind: 'confirm', subkind: 'location', risk: 'low' })
    expect(snap.primary).toBe('attention')
  })
  test('process 持续 >8s 才进 task 项', () => {
    const turn = (since: number) => ({ pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: since })
    expect(derivePresence(base({ turn: turn(NOW - 5000) })).commitment).toEqual([])
    expect(derivePresence(base({ turn: turn(NOW - 9000) })).commitment[0]).toMatchObject({ kind: 'task', label: '规划路线' })
  })
  test('queued>0 → queue 项', () => {
    expect(derivePresence(base({ queued: 3 })).commitment[0]).toMatchObject({ kind: 'queue', count: 3 })
  })
})

describe('capsule 文案', () => {
  test.each([
    [{ hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' } as Partial<PresenceInput>, '说「小舟小舟」'],
    [{ ptt: 'recording' } as Partial<PresenceInput>, '在听…'],
    [{ ptt: 'finalizing' } as Partial<PresenceInput>, '识别中…'],
    [{ speaking: true } as Partial<PresenceInput>, '播报中 · 说话可打断'],
    [{ hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' } as Partial<PresenceInput>, '可以接着说'],
    [{ visionCapturing: true } as Partial<PresenceInput>, '看一眼…'],
    [{ pendingOps: [{ id: 'op1', ts: NOW, summary: 'x' }] } as Partial<PresenceInput>, '等你确认'],
  ])('%o → %s', (over, text) => {
    expect(derivePresence(base(over)).capsule?.text).toBe(text)
  })
  test('partial 在 recognizing 时进胶囊', () => {
    expect(derivePresence(base({ ptt: 'recording', partial: '附近有什么' })).capsule?.text).toBe('附近有什么')
  })
  test('processing 显示当前步骤', () => {
    const turn = { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW }
    expect(derivePresence(base({ turn })).capsule?.text).toBe('规划路线…')
  })
})
