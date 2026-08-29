// mobile/src/core/presence/fixtures.ts
// 状态画廊语料：**每次调用重新取时间基准**（倒计时是相对量，同 cards/fixtures.ts 的教训）。
// 样本走的是 derivePresence 本尊——画廊里看到的就是生产代码算出来的，不是手摆的快照；
// `slot` 与 `safety_blocked` 两种 B1 没有生产产出方，这里只证明「渲得出来」，不证明「会出现」。
import { derivePresence, type PresenceInput, type PresenceSnapshot } from './presence'

export interface PresenceFixture {
  label: string
  /** 真栈能否产出（false = 只有画廊样本） */
  producible: boolean
  snapshot: PresenceSnapshot
}

export function presenceFixtures(): PresenceFixture[] {
  const NOW = Date.now()
  const base: PresenceInput = {
    now: NOW, connStatus: 'open', connChangedAt: NOW - 60_000,
    hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', ptt: 'idle', partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false, pendingOps: [], pendingLocation: false, voicePipeline: 'classic',
    visionCapturing: false, queued: 0, lastError: null, degradations: [], driving: false,
    identity: 'handheld', user: 'ab12',
  }
  const mk = (label: string, over: Partial<PresenceInput>, producible = true): PresenceFixture => ({
    label, producible, snapshot: derivePresence({ ...base, ...over }),
  })
  const hf = (fsm: PresenceInput['hfFsm'], extra: Partial<PresenceInput> = {}) => ({ hfEnabled: true, hfUsable: true, hfFsm: fsm, ...extra })
  return [
    mk('idle', {}),
    mk('armed', hf('ARMED')),
    mk('listening-ptt', { ptt: 'recording' }),
    mk('recognizing-partial', { ptt: 'recording', partial: '附近有什么好吃的' }),
    mk('listening-s2s', hf('LISTENING', { voicePipeline: 's2s' })),
    mk('thinking', { turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 } }),
    mk('processing-long', { turn: { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW - 12_000 } }),
    mk('speaking', { speaking: true }),
    mk('followup', hf('FOLLOWUP')),
    mk('attention-confirm', { pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }] }),
    mk('attention-two', { pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }, { id: 'op2', ts: NOW - 5_000, summary: '要解锁车门吗？' }], queued: 1 }),
    mk('attention-location', { pendingLocation: true }),
    mk('looking', { visionCapturing: true }),
    mk('reconnecting', { connStatus: 'connecting', connChangedAt: NOW - 5_000 }),
    mk('offline-with-confirm', { connStatus: 'closed', connChangedAt: NOW - 30_000, pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }], queued: 2 }),
    mk('error', { lastError: { text: '出错了', at: NOW - 500 } }),
    mk('deg-permission', { degradations: [{ kind: 'permission_denied', what: 'mic', text: '需要麦克风权限，请在系统设置里允许' }] }),
    mk('deg-service', { degradations: [{ kind: 'service_degraded', text: '语音链路降级，本轮回落三段式' }] }),
    mk('deg-echo', { degradations: [{ kind: 'audio_echo_degraded', reason: 'repeated-self-trigger' }] }),
    mk('deg-transport-unknown', { degradations: [{ kind: 'transport_unknown', messageIds: ['m1'] }] }),
    mk('deg-recoverable', { degradations: [{ kind: 'recoverable_error', text: '响应超时了', at: NOW }] }),
    mk('deg-safety-blocked', { degradations: [{ kind: 'safety_blocked', text: '高速行驶中请勿打开车窗/天窗' }] }, false),
    mk('deg-fatal', { degradations: [{ kind: 'fatal', text: 'token 握手失败，请重新配置服务器' }] }),
  ]
}
