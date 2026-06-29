// 对话视图：消息流 + 操作卡 + 确认条 + "思考中/流式"指示。
// 思考指示是 task 4 的前端侧改进：开放域慢响应时立刻给出可见反馈，
// 不让用户面对"死寂"等待；若后端流式下发 speech_delta 则逐字显示。
import { useEffect, useRef, useState } from 'react'
import { useSettings } from '../settings'
import { CardRenderer } from './Cards'
import { AuroraOrb } from './aurora'
import type { Action, Msg, ProcessStep } from '../types'

export function ChatView({
  messages,
  awaitConfirm,
  onConfirm,
  onQuick,
}: {
  messages: Msg[]
  awaitConfirm: boolean
  onConfirm: (reply: '确认' | '取消') => void
  onQuick: (text: string) => void
}) {
  const { settings } = useSettings()
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  return (
    <div className="chat" ref={listRef}>
      {messages.length === 0 && <Welcome name={settings.assistantName} onQuick={onQuick} />}
      {messages.map((m, i) => (
        <Bubble
          key={m.id}
          msg={m}
          isLast={i === messages.length - 1}
          awaitConfirm={awaitConfirm}
          onConfirm={onConfirm}
          onAction={onQuick}
        />
      ))}
    </div>
  )
}

function Welcome({ name, onQuick }: { name: string; onQuick: (t: string) => void }) {
  return (
    <div className="au-welcome">
      <AuroraOrb size={96} state="idle" />
      <div className="au-welcome-title">我是{name}</div>
      <div className="au-welcome-sub">按住麦克风说话，或点下方指令试试</div>
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

function Bubble({
  msg,
  isLast,
  awaitConfirm,
  onConfirm,
  onAction,
}: {
  msg: Msg
  isLast: boolean
  awaitConfirm: boolean
  onConfirm: (reply: '确认' | '取消') => void
  onAction?: (text: string) => void
}) {
  const cls = ['row', msg.role, msg.error ? 'is-error' : ''].join(' ').trim()
  return (
    <div className={cls}>
      {msg.role === 'assistant' && (
        <span className="avatar" aria-hidden>
          <span className="avatar-core" />
        </span>
      )}
      <div className={'bubble ' + msg.role}>
        {msg.process && msg.process.length > 0 && (
          <ProcessPanel steps={msg.process} active={msg.processActive} driving={msg.driving} />
        )}

        {msg.pending ? (
          <ThinkingDots />
        ) : msg.text || msg.streaming ? (
          <div className="text">
            {msg.text}
            {msg.streaming && <span className="caret" />}
          </div>
        ) : null}

        {msg.uiCard && <CardRenderer card={msg.uiCard} onAction={onAction} />}

        {msg.actions?.map((a, j) => (
          <ActionChip key={j} action={a} />
        ))}

        {msg.followUp && <div className="followup">{msg.followUp}</div>}

        {msg.needConfirm && awaitConfirm && isLast && (
          <div className="confirm-bar">
            <button className="yes" onClick={() => onConfirm('确认')}>
              确认
            </button>
            <button className="no" onClick={() => onConfirm('取消')}>
              取消
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function ActionChip({ action }: { action: Action }) {
  const summary =
    (action.payload?.command as string) ??
    (action.payload?.name as string) ??
    (action.payload && Object.keys(action.payload).length ? JSON.stringify(action.payload) : '')
  return (
    <div className="action">
      <span className="tag">{action.type}</span>
      {summary && <span className="action-sum">{summary}</span>}
      {action.require_confirm && <span className="confirm-flag">需确认</span>}
    </div>
  )
}

function ThinkingDots() {
  return (
    <div className="thinking" aria-label="思考中">
      <span className="eq">
        <i /><i /><i /><i />
      </span>
      <span className="thinking-text">正在思考…</span>
    </div>
  )
}

// 复杂任务过程区：气泡内嵌折叠条，分四阶段（理解需求→规划步骤→执行任务→整理结果）。
// 进行中：标题 + 已完成阶段概要 + 进行中步骤（「正在查询天气…」）。
// 完成后：默认折叠，可展开看四阶段时间线（执行任务含各能力子项）。
// 行车态（driving）：强制单行、不可展开（NHTSA 视线安全）。
function ProcessPanel({
  steps,
  active,
  driving,
}: {
  steps: ProcessStep[]
  active?: boolean
  driving?: boolean
}) {
  const [open, setOpen] = useState(false)
  const expandable = !active && !driving

  const understand = steps.find((s) => s.phase === 'understand')
  const planStep = steps.find((s) => s.phase === 'plan')
  const execs = steps.filter((s) => s.phase === 'execute')
  const synth = steps.find((s) => s.phase === 'synthesize')
  const running = execs.filter((s) => s.status === 'running')

  const title = active ? '正在处理复杂任务' : `处理过程（${execs.length} 个步骤）`

  return (
    <div className={'process' + (active ? ' is-active' : '') + (driving ? ' is-driving' : '')}>
      <button
        type="button"
        className="process-bar"
        onClick={() => expandable && setOpen((o) => !o)}
        aria-expanded={open && expandable}
        disabled={!expandable}
      >
        {active ? (
          <span className="process-spin" aria-hidden />
        ) : (
          <span className="process-caret" aria-hidden>
            {expandable ? (open ? '▾' : '▸') : '•'}
          </span>
        )}
        <span className="process-title">{title}</span>
        {expandable && <span className="process-hint">{open ? '收起' : '展开'}</span>}
      </button>

      {/* 进行中（非行车态）：已完成阶段概要 + 进行中步骤 */}
      {active && !driving && (
        <div className="process-live">
          {understand?.summary && <div className="process-live-row done">✓ {understand.summary}</div>}
          {planStep?.summary && <div className="process-live-row done">✓ 已识别：{planStep.summary}</div>}
          {running.map((s) => (
            <div key={s.step_id} className="process-live-row run">
              • 正在{s.label}…
            </div>
          ))}
        </div>
      )}

      {/* 完成后展开：四阶段编号时间线 */}
      {open && expandable && (
        <ol className="process-stages">
          {understand && (
            <li>
              <b>理解需求</b>：{understand.summary}
            </li>
          )}
          {planStep && (
            <li>
              <b>规划步骤</b>：{planStep.summary}
            </li>
          )}
          {execs.length > 0 && (
            <li>
              <b>执行任务</b>
              <ul className="process-exec">
                {execs.map((s) => (
                  <li key={s.step_id}>
                    <span className="exec-label">{s.label}</span>
                    {s.summary
                      ? `：${s.summary}`
                      : s.status === 'running'
                        ? '：未完成'
                        : ''}
                  </li>
                ))}
              </ul>
            </li>
          )}
          {synth && (
            <li>
              <b>整理结果</b>：{synth.summary}
            </li>
          )}
        </ol>
      )}
    </div>
  )
}
