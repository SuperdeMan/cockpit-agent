// mobile/src/core/presence/fixtures.ts
// 状态画廊语料：**每次调用重新取时间基准**（倒计时是相对量，同 cards/fixtures.ts 的教训）。
// 样本走的是 derivePresence 本尊——画廊里看到的就是生产代码算出来的，不是手摆的快照；
// `recoverable_error` / `safety_blocked` / `fatal` 三种 B1 **没有生产产出方**（`usePresence`
//   只 push permission_denied / audio_echo_degraded / service_degraded / transport_unknown），
//   标 `producible: false`——这里只证明「渲得出来」，不证明「会出现」。这条由
//   `presenceFixtures.test.ts` 从**产出方源码**盘点比对，手写标记漂了当场红。
// ⚠ `slot` 连这一步都做不到：`derivePresence` **没有产出 slot 项的代码路径**（协议无
//   `missing_slots`），而本文件的纪律是「样本走 derivePresence 本尊」⇒ 画廊**无从证明**
//   slot 渲得出来。要证明它就得手搓一个假 snapshot，那正是这条纪律要挡的事，所以不做。
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
    hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', hfFsmChangedAt: NOW - 500, ptt: 'idle', partial: '',
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
    // 评审 D2：进入待机 3s 后胶囊消失、青环仍在——画廊要能看见「没有胶囊」这个态
    mk('armed-quiet', hf('ARMED', { hfFsmChangedAt: NOW - 10_000 })),
    // 评审 D3：免唤醒开着时 error 也要出得来（此前被 armed 遮蔽）
    mk('error-hf-on', hf('ARMED', { hfFsmChangedAt: NOW - 10_000, lastError: { text: '出错了', at: NOW - 500 } })),
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
    // B2 T3 语音层三档 detent：只录音 0.4（listening-ptt 那条就是）/ 有回答 0.62 / 有主卡 0.78；
    // 外加一条「用户下拉过」证明 dismissed 真的收得起来
    mk('sheet-answering', {
      turn: { pending: false, streaming: true, processActive: false, processLabel: '', processSince: 0 },
      voice: { turnSource: 'ptt', override: null, answer: true, card: false },
    }),
    mk('sheet-card', { speaking: true, voice: { turnSource: 'handsfree', override: null, answer: true, card: true } }),
    mk('sheet-dismissed', {
      turn: { pending: false, streaming: true, processActive: false, processLabel: '', processSince: 0 },
      voice: { turnSource: 'ptt', override: 'dismissed', answer: true, card: false },
    }),
    // B4-11 行车档：常驻（C）/ 播报中（B）/ 建议胶囊。三条都 producible——手动开关 + 角色就能造
    mk('driving-resident-C', { driving: true, identity: 'trusted-tablet' }),
    mk('driving-answer-B', {
      driving: true, identity: 'mount', speaking: true,
      voice: { turnSource: 'handsfree', override: null, answer: true, card: false },
    }),
    mk('driving-suggest', { drivingSuggest: true, identity: 'trusted-tablet' }),
    mk('looking', { visionCapturing: true }),
    mk('reconnecting', { connStatus: 'connecting', connChangedAt: NOW - 5_000 }),
    mk('offline-with-confirm', { connStatus: 'closed', connChangedAt: NOW - 30_000, pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }], queued: 2 }),
    // 断网但没有待确认——这是 queue 项**唯一**会被 pin 住的形态。上面两条带 queued 的样本
    // 都同时带 pendingOps，confirm 排 rank 0、queue 排 rank 4 ⇒ queue 只进「另有 N 个」计数，
    // 它自己那句「N 条消息排队中」一条都渲不出来（第 2 批实测，Maestro 09 因此曾必红）。
    mk('offline-queue-only', { connStatus: 'closed', connChangedAt: NOW - 30_000, queued: 3 }),
    mk('error', { lastError: { text: '出错了', at: NOW - 500 } }),
    mk('deg-permission', { degradations: [{ kind: 'permission_denied', what: 'mic', text: '需要麦克风权限，请在系统设置里允许' }] }),
    mk('deg-service', { degradations: [{ kind: 'service_degraded', text: '语音链路降级，本轮回落三段式' }] }),
    mk('deg-echo', { degradations: [{ kind: 'audio_echo_degraded', reason: 'repeated-self-trigger' }] }),
    mk('deg-transport-unknown', { degradations: [{ kind: 'transport_unknown', messageIds: ['m1'] }] }),
    mk('deg-recoverable', { degradations: [{ kind: 'recoverable_error', text: '响应超时了', at: NOW }] }, false),
    mk('deg-safety-blocked', { degradations: [{ kind: 'safety_blocked', text: '高速行驶中请勿打开车窗/天窗' }] }, false),
    mk('deg-fatal', { degradations: [{ kind: 'fatal', text: 'token 握手失败，请重新配置服务器' }] }, false),
  ]
}
