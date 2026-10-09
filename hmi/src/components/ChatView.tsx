// Visual v2 I3: answers are text, confirmations are pinned, evidence uses the shared projection.
import { useEffect, useRef, useState } from 'react'
import { useSettings } from '../settings'
import { useDriving } from '../DrivingContext'
import type { Msg, ProcessStep } from '../types'
import { confirmationPresentation } from '../merchantUi.mjs'
import { PendingPolicyView, pendingMessages, type LocalConfirmation } from '../pendingPresentation.mjs'
import { controlItems, itemName, STATUS_WORD, type ControlItem } from '../controlResult.mjs'
import { CardRenderer } from './Cards'
import { ResultDetails } from './ResultDetails'
import { Icon } from './Icon'

type Props = {
  messages: Msg[]; awaitConfirm: boolean; livePendingOps?: string[]
  onConfirm: (reply: '确认' | '取消', operationId?: string) => void
  onQuick: (text: string) => void; partialUser?: string
  pendingPolicy?: PendingPolicyView; localConfirmation?: LocalConfirmation
  localMessageId?: string
}
export function ChatView({ messages, livePendingOps, onConfirm, onQuick, partialUser, pendingPolicy, localConfirmation, localMessageId }: Props) {
  const { settings } = useSettings()
  const { driving } = useDriving()
  const listRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const [atTail, setAtTail] = useState(true)
  const [hasNew, setHasNew] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [, tick] = useState(0)
  const now = Date.now()
  const rows = pendingMessages(messages, livePendingOps, localConfirmation, pendingPolicy, now, localMessageId)
  const hasDeadline = rows.some(m => m.operationId && pendingPolicy?.get(m.operationId)?.remaining !== undefined)
  useEffect(() => {
    if (!hasDeadline) return
    const timer = window.setInterval(() => tick(n => n + 1), 1000)
    return () => window.clearInterval(timer)
  }, [hasDeadline])

  const jump = () => {
    followRef.current = true
    setAtTail(true); setHasNew(false)
    const list = listRef.current
    if (list) list.scrollTo({ top: list.scrollHeight, behavior: 'auto' })
  }
  // D12 draft: the reader owns their scroll position. Only a pinned reader follows updates.
  useEffect(() => {
    if (followRef.current) jump()
    else setHasNew(true)
  }, [messages, partialUser])
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => { if (followRef.current) jump() })
    if (contentRef.current) observer.observe(contentRef.current)
    if (listRef.current) observer.observe(listRef.current)
    return () => observer.disconnect()
  }, [])
  const onScroll = () => {
    const list = listRef.current
    if (!list) return
    const near = list.scrollHeight - list.clientHeight - list.scrollTop <= 48
    followRef.current = near; setAtTail(near)
    if (near) setHasNew(false)
  }
  const localFirst = !!rows[0] && !rows[0].operationId
  const pinned = (localFirst ? rows[0] : rows.find(m => (m.operationId || m.id) === selected)) || rows[0]
  const cycle = () => {
    if (!pinned || localFirst) return
    const next = rows[(rows.indexOf(pinned) + 1) % rows.length]
    setSelected(next.operationId || next.id)
  }
  const retryText = (index: number) => {
    for (let i = index - 1; i >= 0; i--) if (messages[i].role === 'user') return messages[i].text
    return undefined
  }
  return <div className="au-conv-panel">
    <div className="au-chat-scroll">
    <div className={'chat' + (!atTail ? ' reading-history' : '')} ref={listRef} onScroll={onScroll}>
      <div className="au-chat-content" ref={contentRef}>
        {!driving && messages.length === 0 && <div className="au-welcome">
          <div className="au-welcome-title">我是{settings.assistantName}</div>
          <div className="au-welcome-sub">说出需求，或选择下方指令</div>
        </div>}
        {!driving && messages.map((message, index) => <Message key={message.id} msg={{ ...message, driving }}
          retryText={retryText(index)} onAction={onQuick} />)}
        {!driving && partialUser && <div className="au-user-row"><div className="au-user-bubble partial">
          {partialUser}<span className="au-cursor" />
        </div></div>}
      </div>
    </div>
    {!driving && hasNew && <button className="au-jump-latest" onClick={jump}>
      <Icon name="arrow-down" size={24} state="active" />有新消息
    </button>}
    </div>
    {pinned && <PendingBar msg={pinned} policy={pendingPolicy} localKind={!pinned.operationId ? localConfirmation : undefined}
      otherCount={rows.length - 1} onNext={localFirst ? undefined : cycle} onConfirm={onConfirm} />}
  </div>
}

function Message({ msg, onAction, retryText }: { msg: Msg; onAction: (text: string) => void; retryText?: string }) {
  const { developerMode } = useSettings()
  if (msg.role === 'user') return <div className="au-user-row"><div className="au-user-bubble">{msg.text}</div></div>
  if (msg.rejected) return <div className="au-rejected"><Icon name="info" size={24} />
    已忽略（疑似环境人声）· 如果是对我说的，请再说一遍</div>
  const proactive = msg.text.trim().startsWith('💡')
  const text = proactive ? msg.text.replace(/^💡\s*/, '') : msg.text.replace(/^出错了：/, '')
  const kind = msg.proactiveKind || ''
  const alert = kind === 'scene_verify' || (!msg.uiCard && !kind && /预警|拥堵|事故|绕行|路况|危险|注意|提醒您|减速|结冰|临时管制/.test(text))
  const labels: Record<string, string> = { scene_suggest: 'AI 建议', scene_verify: '执行反馈', reminder_fired: '提醒到点' }
  const label = labels[kind] || (msg.uiCard ? '任务完成' : alert ? '行程预警' : '提醒')
  const interrupted = msg.error && /已打断/.test(msg.text)
  return <article className={'au-message' + (msg.error ? ' au-error' : proactive ? ' au-proactive' : '')}
    data-tone={interrupted ? 'neutral' : msg.error ? 'danger' : alert ? 'warning' : 'accent'} data-message-id={msg.id}>
    {msg.error && <div className="au-message-label">{interrupted ? '已打断' : /超时/.test(msg.text) ? '请求超时' : '请求出错'}</div>}
    {proactive && <div className="au-message-label"><Icon name={alert ? 'warning' : 'info'} size={24} color="currentColor" />{label}</div>}
    {!!msg.process?.length && <ProcessArea steps={msg.process} active={msg.processActive} driving={msg.driving} />}
    {msg.pending && !msg.process?.length && <div className="au-thinking"><span className="au-think-dots"><i /><i /><i /></span>正在思考…</div>}
    {!!text && !msg.pending && <div className="au-answer">{text}{msg.streaming && <span className="au-cursor" />}</div>}
    {msg.uiCard && <CardRenderer card={msg.uiCard} onAction={onAction} />}
    <ControlResults msg={msg} />
    <ResultDetails msg={msg} onAction={onAction} />
    {msg.followUp && <div className="au-follow-up">{msg.followUp}</div>}
    {msg.error && retryText && <button className="au-retry" onClick={() => onAction(retryText)}>
      <Icon name="refresh" size={24} color="currentColor" />重试
    </button>}
    {developerMode && msg.actions?.length ? <div className="au-debug-meta">{msg.actions.map(a => a.type).join(' · ')}</div> : null}
    <TraceTag traceId={msg.traceId} />
  </article>
}

function TraceTag({ traceId }: { traceId?: string }) {
  const { developerMode } = useSettings()
  const [copied, setCopied] = useState(false)
  if (!developerMode || !traceId) return null
  return <button className="au-trace" onClick={() => {
    void navigator.clipboard?.writeText(traceId).then(() => setCopied(true)).catch(() => {})
  }} title={'复制 trace_id ' + traceId}>{copied ? '已复制' : '#' + traceId.slice(0, 8)}</button>
}

function PendingBar({ msg, policy, localKind, otherCount, onNext, onConfirm }: {
  msg: Msg; policy?: PendingPolicyView; localKind?: LocalConfirmation; otherCount: number
  onNext?: () => void; onConfirm: Props['onConfirm']
}) {
  const card = msg.uiCard
  const context = card && 'confirmation_context' in card && typeof card.confirmation_context === 'string' ? card.confirmation_context : ''
  const copy = localKind === 'location'
    ? { kind: 'location', label: '当前位置', detail: '用于附近搜索和导航；也可以拒绝，直接告诉我城市或地点。', confirmLabel: '允许' }
    : confirmationPresentation(context, card?.type || '')
  const meta = msg.operationId ? policy?.get(msg.operationId) : undefined
  const demo = localKind === 'demo'
  const blocked = demo || !!(meta && (!meta.canConfirm || meta.expired))
  const warning = copy.kind === 'vehicle' || copy.kind === 'merchant_cancel'
  const title = meta?.actionSummary || msg.text || copy.label
  return <section className="au-pending-bar" data-operation-id={msg.operationId || ''}
    data-tone={warning ? 'warning' : 'accent'} aria-label={'待确认 · ' + copy.label}>
    <div className="au-pending-heading">
      <Icon name={copy.kind === 'location' ? 'location' : warning ? 'warning' : 'dining'} size={28} color="var(--pending-accent)" />
      <div className="au-pending-copy">
        <div className="au-pending-title">{title}</div>
        <div className="au-pending-detail">{copy.label} · {copy.detail}</div>
        {meta?.remaining !== undefined && <div className="au-pending-detail au-num">剩余 {meta.remaining} 秒</div>}
        {demo && <div className="au-pending-detail">示例数据</div>}
        {blocked && !demo && <div className="au-pending-detail">此确认当前不可用，请重新发起请求。</div>}
      </div>
    </div>
    <div className="au-pending-buttons">
      <button disabled={demo} onClick={() => onConfirm('取消', msg.operationId)}>{localKind === 'location' ? '不允许' : '取消'}</button>
      <button className="primary" disabled={blocked} onClick={() => onConfirm('确认', msg.operationId)}>{copy.confirmLabel}</button>
    </div>
    {otherCount > 0 && <button className="au-pending-next" disabled={!onNext} onClick={onNext}>
      另有 {otherCount} 个待确认<Icon name="chevron-right" size={24} />
    </button>}
  </section>
}

function ControlResults({ msg }: { msg: Msg }) {
  if (msg.needConfirm) return null
  const items = controlItems(msg)
  if (!items.length) return null
  const foot = (item: ControlItem) => item.note || ({
    executed: '车辆已执行（本次未做状态核验）', verified: '车辆状态已确认', unchanged: '没有改动',
    unverified: '车辆状态还没核实', failed: '没有确认到这个操作真的生效，请留意车辆状态。', running: '正在下发到车辆…',
  }[item.status])
  return <div className="au-control-results">{items.map((item, index) => {
    const value = /^(-?\d+(?:\.\d+)?)(.*)$/.exec(item.value)
    return <section className="au-control-result" key={index} data-status={item.status}>
      <div className="au-control-head">
        <span className="au-control-icon"><Icon name={item.object === '空调' ? 'snowflake' : item.command.startsWith('seat.heating') ? 'seat-heat' : 'vehicle'} size={28} state="active" /></span>
        <strong>{itemName(item)}</strong><span className="au-control-status">{STATUS_WORD[item.status]}</span>
      </div>
      <div className="au-control-value"><span>{item.action}</span>
        {value ? <><b className="au-num">{value[1]}</b><span>{value[2]}</span></> : <b>{item.value}</b>}
      </div>
      <div className="au-control-evidence">{foot(item)}{item.sourceKind === 'simulated' && <span> · 模拟观测</span>}</div>
    </section>
  })}</div>
}

function ProcessArea({ steps, active, driving }: { steps: ProcessStep[]; active?: boolean; driving?: boolean }) {
  const [open, setOpen] = useState(false)
  const stages = deriveStages(steps, !!active)
  const total = stages.reduce((n, s) => n + (s.subs?.length || 1), 0)
  if (driving) return <div className="au-process-terse">{active ? '正在处理复杂任务…' : '处理过程（' + total + ' 步）'}</div>
  const expanded = active || open
  return <section className="au-process">
    {active ? <div className="au-process-caption">正在处理您的请求…</div>
      : <button className="au-process-toggle" aria-expanded={!!expanded} onClick={() => setOpen(!open)}>
        <Icon name="check" size={24} color="var(--au-online)" />处理过程（{total} 步）<Icon name="chevron-right" size={24} />
      </button>}
    {expanded && <div className="au-process-steps">{stages.map(stage => <div key={stage.key} className="au-process-step" data-status={stage.status}>
      <div className="au-process-step-line"><span className="au-process-mark" data-status={stage.status}>{stage.status === 'done' ? <Icon name="check" size={20} color="var(--au-online)" /> : <span className="au-step-dot" />}</span>
        <span>{stage.label}</span>{stage.summary && <span className="au-process-summary">— {stage.summary}</span>}
      </div>
      {stage.subs && <div className="au-process-subs">{stage.subs.map((sub, index) => <div key={index} data-status={sub.status}>
        <span className="au-process-mark" data-status={sub.status}>{sub.status === 'done' ? <Icon name="check" size={18} color="var(--au-online)" /> : <span className="au-step-dot" />}</span>
        {sub.label}{!active && sub.summary ? '：' + sub.summary : ''}
      </div>)}</div>}
    </div>)}</div>}
  </section>
}

type StageStatus = 'done' | 'active' | 'pending'
type Stage = { key: string; label: string; status: StageStatus; summary?: string; subs?: { label: string; status: StageStatus; summary?: string }[] }
const PHASE_ORDER = ['understand', 'plan', 'execute', 'synthesize']
const PHASE_LABEL: Record<string, string> = { understand: '理解需求', plan: '规划步骤', execute: '执行任务', synthesize: '整理结果' }

function deriveStages(steps: ProcessStep[], active: boolean): Stage[] {
  const understand = steps.find((s) => s.phase === 'understand')
  const plan = steps.find((s) => s.phase === 'plan')
  const execs = steps.filter((s) => s.phase === 'execute')
  const synth = steps.find((s) => s.phase === 'synthesize')
  const activePhase = synth ? 'synthesize' : execs.length ? 'execute' : plan ? 'plan' : understand ? 'understand' : ''
  const ai = PHASE_ORDER.indexOf(activePhase)
  const stat = (phase: string): StageStatus => {
    if (!active) return 'done'
    const i = PHASE_ORDER.indexOf(phase)
    return i < ai ? 'done' : i === ai ? 'active' : 'pending'
  }
  const subStat = (s: ProcessStep): StageStatus =>
    s.status === 'done' || (s.summary && s.status !== 'running') ? 'done' : s.status === 'running' ? 'active' : 'pending'
  const out: Stage[] = []
  if (understand) out.push({ key: 'understand', label: PHASE_LABEL.understand, status: stat('understand'), summary: understand.summary })
  if (plan) out.push({ key: 'plan', label: PHASE_LABEL.plan, status: stat('plan'), summary: plan.summary || (plan.label ? `已识别：${plan.label}` : undefined) })
  if (execs.length) out.push({ key: 'execute', label: PHASE_LABEL.execute, status: stat('execute'), subs: execs.map((s) => ({ label: s.label, status: active ? subStat(s) : 'done', summary: s.summary })) })
  if (synth) out.push({ key: 'synthesize', label: PHASE_LABEL.synthesize, status: stat('synthesize'), summary: synth.summary })
  return out
}
