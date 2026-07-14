// 对话视图（P3 · A-6「对话动态态」忠实重建）——语音助手的灵魂。
// 六态映射到真实 Msg 字段，不新增数据契约（types.ts 不改）：
//   思考中    msg.pending
//   流式输出  msg.streaming（虹彩光标 + 字数 + 微光扫过）
//   过程区    msg.process / processActive / driving（四阶段·进行中展开/完成折叠/行车单行）
//   确认条    msg.needConfirm && awaitConfirm && isLast（琥珀危险确认）
//   主动播报  text 以 '💡' 起头（App.tsx 注入）；带卡=任务完成报告，含预警词=行程预警
//   错误/超时 msg.error（红 × + 重试上一条用户消息）
// 视觉照 docs/design 【新】座舱Agent-HMI-A-6；inline 样式 + --au-* token，复用 AuroraOrb / CardRenderer。
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useSettings } from '../settings'
import { CardRenderer } from './Cards'
import { AuroraOrb, type OrbState } from './aurora'
import type { Action, Msg, ProcessStep } from '../types'

// ─── 语义色/灰阶（统一走 --au-* token，§3）───
const FG1 = 'var(--au-text)'
const FG2 = 'var(--au-text-2)'
const FG3 = 'var(--au-text-3)'
const TEAL = 'var(--au-primary)'
const AMBER = 'var(--au-warn)'
const GREEN = 'var(--au-online)'
const RED = 'var(--au-danger)'

// ─── 内联线性图标（lucide 风，避免引第三方依赖）───
function Svg({ size = 14, color = 'currentColor', sw = 2, style, children }: {
  size?: number; color?: string; sw?: number; style?: CSSProperties; children: ReactNode
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
      strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, ...style }}>
      {children}
    </svg>
  )
}
const IcCheck = (p: { size?: number; color?: string }) => <Svg {...p}><path d="M20 6 9 17l-5-5" /></Svg>
const IcX = (p: { size?: number; color?: string; style?: CSSProperties }) => <Svg {...p}><path d="M18 6 6 18M6 6l12 12" /></Svg>
const IcChevron = (p: { size?: number; color?: string; style?: CSSProperties }) => <Svg {...p}><path d="m6 9 6 6 6-6" /></Svg>
const IcArrowR = (p: { size?: number; color?: string }) => <Svg {...p}><path d="m9 18 6-6-6-6" /></Svg>
const IcAlert = (p: { size?: number; color?: string; style?: CSSProperties }) => (
  <Svg {...p}><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" /><path d="M12 9v4" /><path d="M12 17h.01" /></Svg>
)
const IcBulb = (p: { size?: number; color?: string }) => (
  <Svg {...p}><path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5a6 6 0 0 0-12 0c0 1.3.5 2.6 1.5 3.5.8.8 1.3 1.5 1.5 2.5" /><path d="M9 18h6" /><path d="M10 22h4" /></Svg>
)
const IcRefresh = (p: { size?: number; color?: string; style?: CSSProperties }) => (
  <Svg {...p}><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" /></Svg>
)

// 主动播报「预警」判据：含路况/安全关键词 → 琥珀；否则视为冷蓝信息提醒（如记忆早报）。
const ALERT_RE = /预警|拥堵|事故|绕行|路况|危险|注意|提醒您|减速|结冰|临时管制/

export function ChatView({
  messages,
  awaitConfirm,
  onConfirm,
  onQuick,
  partialUser,
}: {
  messages: Msg[]
  awaitConfirm: boolean
  onConfirm: (reply: '确认' | '取消') => void
  onQuick: (text: string) => void
  partialUser?: string // hands-free 聆听中的实时识别文字（issue②）
}) {
  const { settings } = useSettings()
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, partialUser])

  // 错误气泡「重试」需要最近一条用户消息——预先按下标算好往回传。
  const lastUserText = (i: number): string | undefined => {
    for (let j = i - 1; j >= 0; j--) if (messages[j].role === 'user') return messages[j].text
    return undefined
  }

  return (
    <div className="au-conv-panel">
      <div className="au-conv-head">
        <AuroraOrb size={36} state="idle" />
        <div className="au-conv-head-text">
          <div className="au-conv-head-name">{settings.assistantName}</div>
          <div className="au-conv-head-sub">AI 智能助手 · 对话</div>
        </div>
      </div>
      <div className="chat" ref={listRef}>
        {messages.length === 0 && <Welcome name={settings.assistantName} onQuick={onQuick} />}
        {messages.length > 0 && <div className="au-park-pill">泊车模式 · 已停车</div>}
        {messages.map((m, i) => (
          <MessageItem
            key={m.id}
            msg={m}
            isLast={i === messages.length - 1}
            awaitConfirm={awaitConfirm}
            onConfirm={onConfirm}
            onAction={onQuick}
            retryText={lastUserText(i)}
          />
        ))}
        {partialUser && <PartialUserBubble text={partialUser} />}
      </div>
    </div>
  )
}

function Welcome({ name, onQuick }: { name: string; onQuick: (t: string) => void }) {
  return (
    <div className="au-welcome">
      <AuroraOrb size={96} state="idle" />
      <div className="au-welcome-title">我是{name}</div>
      <div className="au-welcome-sub">按住下方光球说话，或点指令试试</div>
      <div className="au-welcome-chips">
        {['打开空调26度', '附近的充电站', '讲个笑话'].map((q) => (
          <button key={q} className="au-welcome-chip" onClick={() => onQuick(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── 消息分发：按状态选不同气泡形态 ───
function MessageItem({
  msg, isLast, awaitConfirm, onConfirm, onAction, retryText,
}: {
  msg: Msg
  isLast: boolean
  awaitConfirm: boolean
  onConfirm: (reply: '确认' | '取消') => void
  onAction: (text: string) => void
  retryText?: string
}) {
  if (msg.role === 'user') return <UserBubble text={msg.text} />

  // 主动播报（独立通知气泡，App.tsx 给 text 注入 '💡' 前缀）
  if (msg.text.trim().startsWith('💡')) return <ProactiveBubble msg={msg} onAction={onAction} />

  // 错误 / 超时
  if (msg.error) return <ErrorBubble msg={msg} retryText={retryText} onAction={onAction} />

  // R4.4：云端拒识（疑似环境人声）→ 弱化 muted 小气泡，静默忽略但留痕供纠错
  if (msg.rejected) return <RejectedBubble />

  // 危险车控二次确认（仅当前等待确认且为最后一条）
  if (msg.needConfirm && awaitConfirm && isLast) return <ConfirmBubble msg={msg} onConfirm={onConfirm} onAction={onAction} />

  return <AssistantBubble msg={msg} onAction={onAction} />
}

// ─── A-6.0 · 用户气泡（右对齐，交互蓝玻璃）───
function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 14 }}>
      <div style={{
        maxWidth: '78%', padding: '11px 16px', borderRadius: '18px 18px 4px 18px',
        background: 'rgba(70,214,224,0.12)', border: '1px solid rgba(70,214,224,0.22)',
        WebkitBackdropFilter: 'blur(16px)', backdropFilter: 'blur(16px)',
        boxShadow: '0 4px 16px rgba(0,0,0,0.22)',
      }}>
        <div style={{ fontSize: 14.5, lineHeight: 1.65, color: FG1, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{text}</div>
      </div>
    </div>
  )
}

// ─── R4.4 · 拒识提示（左对齐弱化 muted 小气泡）：云端判本轮疑似环境人声，静默忽略但留痕供纠错 ───
function RejectedBubble() {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 14 }}>
      <div style={{
        maxWidth: '78%', padding: '8px 14px', borderRadius: '14px 14px 14px 4px',
        background: 'var(--au-fill)', border: '1px dashed var(--au-fill-2)',
      }}>
        <div style={{ fontSize: 12.5, lineHeight: 1.5, color: FG3 }}>
          已忽略（疑似环境人声）· 如果是对我说的，请再说一遍
        </div>
      </div>
    </div>
  )
}

// ─── 聆听中的实时识别（issue②）：右对齐 ghost 用户气泡，淡显虚线 + 流式光标；定稿后由真实 UserBubble 接管 ───
function PartialUserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 14 }}>
      <div style={{
        maxWidth: '78%', padding: '11px 16px', borderRadius: '18px 18px 4px 18px',
        background: 'rgba(70,214,224,0.06)', border: '1px dashed rgba(70,214,224,0.30)',
        WebkitBackdropFilter: 'blur(16px)', backdropFilter: 'blur(16px)',
      }}>
        <div style={{ fontSize: 14.5, lineHeight: 1.65, color: FG2, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {text}<span className="au-cursor" />
        </div>
      </div>
    </div>
  )
}

// ─── 助手气泡外壳（左对齐 + 光球头像 + 玻璃；tone 切换语义边框）───
type Tone = 'normal' | 'confirm' | 'error'
function AIBubbleBase({
  children, orbState = 'idle', tone = 'normal', shimmer = false, driving = false, style,
}: {
  children: ReactNode; orbState?: OrbState; tone?: Tone; shimmer?: boolean; driving?: boolean; style?: CSSProperties
}) {
  const toneStyle: CSSProperties =
    tone === 'confirm'
      ? { border: '1px solid rgba(245,158,11,0.32)', borderTop: '1px solid rgba(245,158,11,0.45)', boxShadow: '0 4px 20px rgba(0,0,0,0.30),0 0 16px rgba(245,158,11,0.12),inset 0 1px 0 var(--au-fill-2)' }
      : tone === 'error'
        ? { border: '1px solid rgba(239,68,68,0.28)', borderTop: '1px solid rgba(239,68,68,0.40)', boxShadow: '0 4px 20px rgba(0,0,0,0.30),0 0 12px rgba(239,68,68,0.08),inset 0 1px 0 var(--au-fill-2)' }
        : {}
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 14 }}>
      <div style={{ width: 30, height: 30, flexShrink: 0, marginTop: 2 }}>
        <AuroraOrb state={orbState} size={30} driving={driving} />
      </div>
      <div style={{
        flex: 1, minWidth: 0, padding: '13px 15px', borderRadius: '4px 18px 18px 18px',
        background: 'var(--au-fill)', border: '1px solid var(--au-fill-2)',
        borderTop: '1px solid var(--au-hi)',
        WebkitBackdropFilter: 'blur(20px)', backdropFilter: 'blur(20px)',
        boxShadow: '0 4px 20px rgba(0,0,0,0.30),inset 0 1px 0 var(--au-fill-2)',
        position: 'relative', overflow: 'hidden', ...toneStyle, ...style,
      }}>
        {/* 流式微光扫过（§5 允许的 AI 时刻）*/}
        {shimmer && (
          <div style={{ position: 'absolute', inset: 0, width: '32%', background: 'linear-gradient(90deg,transparent,rgba(91,233,255,0.06),transparent)', animation: 'au-shimmer 2.8s ease-in-out infinite', pointerEvents: 'none' }} />
        )}
        {children}
      </div>
    </div>
  )
}

const HR = ({ color = 'var(--au-line)' }: { color?: string }) => <div style={{ height: 1, background: color }} />

// ─── 正常助手气泡：过程区 + 思考/流式/最终文本 + 卡片 + 动作 + 追问 ───
function AssistantBubble({ msg, onAction }: { msg: Msg; onAction: (t: string) => void }) {
  const hasProcess = !!(msg.process && msg.process.length > 0)
  const hasText = !!msg.text || !!msg.streaming
  const orbState: OrbState = msg.pending || msg.processActive ? 'thinking' : msg.streaming ? 'speaking' : 'idle'

  return (
    <AIBubbleBase orbState={orbState} shimmer={!!msg.streaming} driving={msg.driving}>
      {hasProcess && <ProcessArea steps={msg.process!} active={msg.processActive} driving={msg.driving} />}

      {/* 思考中：仅在没有过程区时单独显示（过程区本身已表达"处理中"）*/}
      {msg.pending && !hasProcess && <ThinkingInline />}

      {/* 最终/流式文本：过程区之后用 HR 分隔（照 A-6 完成态）*/}
      {hasText && !msg.pending && (
        <>
          {hasProcess && <div style={{ margin: '12px 0 10px' }}><HR /></div>}
          <StreamingText text={msg.text} streaming={!!msg.streaming} />
        </>
      )}

      {msg.uiCard && <div style={{ marginTop: hasText || hasProcess ? 12 : 0 }}><CardRenderer card={msg.uiCard} onAction={onAction} /></div>}

      {msg.actions?.map((a, j) => <ActionChip key={j} action={a} />)}

      {msg.followUp && <div style={{ marginTop: 8, fontSize: 13, color: FG2, lineHeight: 1.6 }}>{msg.followUp}</div>}

      {!msg.pending && <TraceTag traceId={msg.traceId} />}
    </AIBubbleBase>
  )
}

// ─── 观测角标：本轮 trace 短 id，点按复制完整 id → 可观测台搜索直达全链路详情 ───
function TraceTag({ traceId }: { traceId?: string }) {
  const [copied, setCopied] = useState(false)
  if (!traceId) return null
  const copy = () => {
    try {
      void navigator.clipboard?.writeText(traceId)
    } catch { /* 剪贴板不可用（非 https 等）时静默 */ }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
      <button
        onClick={copy}
        title={`复制 trace_id（${traceId}）用于排查`}
        style={{
          padding: '2px 8px', borderRadius: 7, background: 'transparent',
          border: '1px solid var(--au-line)', fontSize: 9.5, color: FG3,
          cursor: 'pointer', fontFamily: 'inherit', letterSpacing: '0.03em', opacity: 0.55,
        }}
      >
        {copied ? '已复制' : `#${traceId.slice(0, 8)}`}
      </button>
    </div>
  )
}

// ─── A-6.1 · 思考中（三点弹跳 + 文案）───
function ThinkingInline() {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ display: 'flex', gap: 5, alignItems: 'center', padding: '2px 0' }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ width: 7, height: 7, borderRadius: '50%', background: FG2, animation: `au-dot-bounce 1.5s ease-in-out ${i * 0.18}s infinite` }} />
          ))}
        </div>
        <span style={{ fontSize: 13, color: FG3 }}>正在思考…</span>
      </div>
      <div style={{ fontSize: 11, color: FG3, marginTop: 6 }}>正在整理信息，请稍候</div>
    </div>
  )
}

// ─── A-6.2 · 流式文本（虹彩光标 + 字数计）───
function StreamingText({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div style={{ position: 'relative' }}>
      <div style={{ fontSize: 14.5, lineHeight: 1.72, color: FG1, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {text}
        {streaming && <span className="au-cursor" />}
      </div>
      {streaming && (
        <div style={{ fontSize: 10.5, color: FG3, marginTop: 8 }}>
          <span className="au-num">{[...text].length}</span> 字 · 正在生成…
        </div>
      )}
    </div>
  )
}

// ─── A-6.3 · 过程区（四阶段；进行中展开 / 完成折叠 / 行车单行）───
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

const SQUARE: Record<StageStatus, CSSProperties> = {
  done: { background: 'rgba(52,211,153,0.15)', border: '1px solid rgba(52,211,153,0.35)' },
  active: { background: 'rgba(70,214,224,0.15)', border: '1px solid rgba(70,214,224,0.30)' },
  pending: { background: 'var(--au-fill)', border: '1px solid var(--au-fill-2)' },
}
const STAGE_COLOR: Record<StageStatus, string> = { done: GREEN, active: TEAL, pending: FG3 }
function StageDot({ status, size = 8 }: { status: StageStatus; size?: number }) {
  if (status === 'done') return <IcCheck size={size + 2} color="#34D399" />
  if (status === 'active') return <div style={{ width: size, height: size, borderRadius: '50%', background: TEAL, animation: 'au-orb-breathe 1.2s ease-in-out infinite' }} />
  return <div style={{ width: size, height: size, borderRadius: '50%', border: '1.5px solid var(--au-text-3)' }} />
}
const Spinner = ({ size = 12, color = TEAL }: { size?: number; color?: string }) => (
  <div style={{ width: size, height: size, border: `1.5px solid ${color}`, borderTopColor: 'transparent', borderRadius: '50%', animation: 'au-orb-spin 0.9s linear infinite', flexShrink: 0 }} />
)

function ProcessArea({ steps, active, driving }: { steps: ProcessStep[]; active?: boolean; driving?: boolean }) {
  const [open, setOpen] = useState(false)
  const stages = deriveStages(steps, !!active)
  const totalSteps = stages.reduce((a, s) => a + (s.subs ? s.subs.length : 1), 0)

  // 行车态 + 进行中：强制单行（NHTSA 视线安全）
  if (active && driving) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: FG2 }}>
        <Spinner size={12} />
        <span>正在处理复杂任务…</span>
      </div>
    )
  }

  // 进行中（展开态）：阶段链 + 当前阶段子步骤
  if (active) {
    return (
      <div>
        <div style={{ fontSize: 11.5, color: FG3, marginBottom: 12 }}>正在处理您的请求…</div>
        {stages.map((stage, si) => (
          <div key={stage.key} style={{ marginBottom: si < stages.length - 1 ? 10 : 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 18, height: 18, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, ...SQUARE[stage.status] }}>
                <StageDot status={stage.status} />
              </div>
              <span style={{ fontSize: 12.5, fontWeight: stage.status === 'active' ? 600 : 400, color: STAGE_COLOR[stage.status] }}>{stage.label}</span>
              {stage.status === 'done' && stage.summary && <span style={{ fontSize: 11, color: FG3, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>— {stage.summary}</span>}
              {stage.status === 'active' && !stage.subs && <Spinner size={12} />}
            </div>
            {stage.status === 'active' && stage.subs && stage.subs.length > 0 && (
              <div style={{ marginLeft: 26, marginTop: 8, display: 'flex', flexDirection: 'column', gap: 5 }}>
                {stage.subs.map((sub, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <div style={{ width: 14, height: 14, borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, ...SQUARE[sub.status] }}>
                      <StageDot status={sub.status} size={5} />
                    </div>
                    <span style={{ fontSize: 11.5, color: sub.status === 'done' ? FG2 : sub.status === 'active' ? FG1 : FG3, fontWeight: sub.status === 'active' ? 500 : 400 }}>
                      {sub.status === 'active' && '正在'}{sub.label}{sub.status === 'active' && '…'}
                    </span>
                    {sub.status === 'active' && <Spinner size={10} />}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    )
  }

  // 完成态：折叠概要行（可展开时间线）；行车态锁单行
  const expandable = !driving
  return (
    <div>
      <button
        type="button"
        onClick={() => expandable && setOpen((o) => !o)}
        disabled={!expandable}
        style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none', border: 'none', cursor: expandable ? 'pointer' : 'default', padding: 0, width: '100%', textAlign: 'left', color: FG1, fontFamily: 'inherit', marginBottom: open ? 12 : 0 }}
        aria-expanded={open && expandable}
      >
        <div style={{ width: 18, height: 18, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, ...SQUARE.done }}>
          <IcCheck size={10} color="#34D399" />
        </div>
        <span style={{ fontSize: 12.5, color: FG2, flex: 1 }}>
          处理过程 <span className="au-num" style={{ color: TEAL }}>({totalSteps}</span><span style={{ color: FG2 }}>步)</span>
        </span>
        {driving ? (
          <span style={{ fontSize: 10.5, color: AMBER, padding: '2px 8px', borderRadius: 6, background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.20)' }}>行车态</span>
        ) : (
          <IcChevron size={13} color={'var(--au-text-3)'} style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }} />
        )}
      </button>

      {open && expandable && (
        <div style={{ animation: 'au-slide-up .22s ease' }}>
          {stages.map((stage, si) => (
            <div key={stage.key} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: si < stages.length - 1 ? 10 : 0 }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
                <div style={{ width: 18, height: 18, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', animation: 'au-check-pop .3s ease', ...SQUARE.done }}>
                  <IcCheck size={10} color="#34D399" />
                </div>
                {si < stages.length - 1 && <div style={{ width: 1, height: 16, background: 'rgba(52,211,153,0.20)', margin: '3px 0' }} />}
              </div>
              <div style={{ paddingTop: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: GREEN, marginBottom: 2 }}>{stage.label}</div>
                {stage.summary && <div style={{ fontSize: 11, color: FG3, lineHeight: 1.55 }}>{stage.summary}</div>}
                {stage.subs && (
                  <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {stage.subs.map((sub, i) => (
                      <div key={i} style={{ fontSize: 11, color: FG3 }}>· {sub.label}{sub.summary ? `：${sub.summary}` : ''}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── A-6.4 · 确认条（危险车控；琥珀警告 + ≥50px 车规触控）───
function ConfirmBubble({ msg, onConfirm, onAction }: { msg: Msg; onConfirm: (r: '确认' | '取消') => void; onAction: (t: string) => void }) {
  return (
    <AIBubbleBase orbState="speaking" tone="confirm">
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 14 }}>
        <IcAlert size={16} color={AMBER} style={{ marginTop: 1 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, color: FG1, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{msg.text}</div>
          <div style={{ fontSize: 12, color: FG2, marginTop: 6 }}>当前 <span className="au-num" style={{ color: TEAL }}>已泊车</span> · 危险操作需二次确认</div>
        </div>
      </div>
      {msg.uiCard && <div style={{ marginBottom: 12 }}><CardRenderer card={msg.uiCard} onAction={onAction} /></div>}
      <div style={{ display: 'flex', gap: 10 }}>
        <button
          onClick={() => onConfirm('取消')}
          style={{ flex: 1, height: 50, borderRadius: 14, cursor: 'pointer', background: 'var(--au-fill)', border: '1px solid var(--au-line-2)', color: FG2, fontSize: 14, fontWeight: 500, fontFamily: 'inherit', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7 }}
        >
          <IcX size={15} />取消
        </button>
        <button
          onClick={() => onConfirm('确认')}
          style={{ flex: 2, height: 50, borderRadius: 14, cursor: 'pointer', background: 'rgba(245,158,11,0.14)', border: '1px solid rgba(245,158,11,0.38)', color: AMBER, fontSize: 14, fontWeight: 600, fontFamily: 'inherit', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, boxShadow: '0 0 16px rgba(245,158,11,0.12)' }}
        >
          <IcCheck size={15} color={AMBER} />确认
        </button>
      </div>
    </AIBubbleBase>
  )
}

// ─── A-6.5 · 主动播报（独立通知气泡；任务完成=冷蓝+报告卡 / 行程预警=琥珀 / 场景建议=冷蓝）───
// 标题按**种类**取（网关透传的 advisory），不能只看"有没有卡"——场景建议/执行反馈都带卡，
// 只看卡会把它们全标成「任务完成」（那是异步深调研的标题）。
const PROACTIVE_LABEL: Record<string, string> = {
  scene_suggest: '主动播报 · AI 建议',
  scene_verify: '主动播报 · 执行反馈',
  reminder_fired: '主动播报 · 提醒到点',
}

function ProactiveBubble({ msg, onAction }: { msg: Msg; onAction: (t: string) => void }) {
  const text = msg.text.replace(/^💡\s*/, '')
  const kind = msg.proactiveKind || ''
  const isReport = !!msg.uiCard
  // 执行反馈=有动作没生效 → 琥珀（同预警级别的"需要你知道"）；其余信息类走冷蓝。
  const isAlert = kind === 'scene_verify' || (!isReport && !kind && ALERT_RE.test(text))
  const accent = isAlert ? AMBER : TEAL
  const tintBg = isAlert ? 'rgba(245,158,11,0.08)' : 'rgba(70,214,224,0.07)'
  const tintBd = isAlert ? 'rgba(245,158,11,0.24)' : 'rgba(70,214,224,0.22)'
  const label = PROACTIVE_LABEL[kind]
    || (isReport ? '主动播报 · 任务完成' : isAlert ? '主动播报 · 行程预警' : '主动播报 · 提醒')

  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{
        display: 'flex', gap: 10, alignItems: 'flex-start', padding: '14px 15px',
        borderRadius: '14px 18px 18px 14px', background: tintBg, border: `1px solid ${tintBd}`,
        borderLeft: `3px solid ${accent}`, WebkitBackdropFilter: 'blur(20px)', backdropFilter: 'blur(20px)',
        boxShadow: `0 4px 20px rgba(0,0,0,0.24),0 0 12px ${isAlert ? 'rgba(245,158,11,0.08)' : 'rgba(70,214,224,0.08)'}`,
        ...(isAlert ? { animation: 'au-proactive-pulse-amber 3s ease-in-out infinite' } : {}),
      }}>
        <div style={{ width: 30, height: 30, flexShrink: 0, marginTop: 1 }}>
          <AuroraOrb state={isAlert ? 'speaking' : 'idle'} size={30} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
            {isAlert ? <IcAlert size={13} color={accent} /> : <IcBulb size={13} color={accent} />}
            <span style={{ fontSize: 11, fontWeight: 700, color: accent, letterSpacing: '0.05em' }}>{label}</span>
          </div>
          <div style={{ fontSize: 14, color: FG1, lineHeight: 1.65, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{text}</div>
        </div>
      </div>
      {/* 任务完成附带报告卡（异步深调研等）*/}
      {isReport && (
        <div style={{ marginLeft: 40, marginTop: 12 }}>
          <CardRenderer card={msg.uiCard!} onAction={onAction} />
        </div>
      )}
    </div>
  )
}

// ─── A-6.6 · 错误 / 超时（红 × + 重试上一条用户消息）───
function ErrorBubble({ msg, retryText, onAction }: { msg: Msg; retryText?: string; onAction: (t: string) => void }) {
  const title = /超时/.test(msg.text) ? '请求超时' : '请求出错'
  const body = msg.text.replace(/^出错了：/, '')
  return (
    <AIBubbleBase orbState="idle" tone="error">
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <div style={{ width: 22, height: 22, borderRadius: 6, background: 'rgba(239,68,68,0.14)', border: '1px solid rgba(239,68,68,0.28)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <IcX size={11} color={RED} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: RED, marginBottom: 4 }}>{title}</div>
          <div style={{ fontSize: 13, color: FG2, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{body}</div>
          {retryText && (
            <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                onClick={() => onAction(retryText)}
                style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', borderRadius: 10, background: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.28)', fontSize: 12.5, color: RED, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}
              >
                <IcRefresh size={12} color={RED} />重试
              </button>
              <span style={{ fontSize: 11.5, color: FG3 }}>或稍后再说</span>
            </div>
          )}
          <TraceTag traceId={msg.traceId} />
        </div>
      </div>
    </AIBubbleBase>
  )
}

// ─── 动作卡（T2 循环中间动作回执）───
function ActionChip({ action }: { action: Action }) {
  const summary =
    (action.payload?.command as string) ??
    (action.payload?.name as string) ??
    (action.payload && Object.keys(action.payload).length ? JSON.stringify(action.payload) : '')
  return (
    <div style={{ marginTop: 8, display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 11px', borderRadius: 10, background: 'var(--au-fill)', border: '1px solid var(--au-line-2)', maxWidth: '100%' }}>
      <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em', color: TEAL }}>{action.type}</span>
      {summary && <span className="au-num" style={{ fontSize: 12, color: FG2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{summary}</span>}
      {action.require_confirm && <span style={{ fontSize: 10.5, color: AMBER }}>需确认</span>}
    </div>
  )
}
