// 对话视图：消息流 + 操作卡 + 确认条 + "思考中/流式"指示。
// 思考指示是 task 4 的前端侧改进：开放域慢响应时立刻给出可见反馈，
// 不让用户面对"死寂"等待；若后端流式下发 speech_delta 则逐字显示。
import { useEffect, useRef } from 'react'
import { useSettings } from '../settings'
import { CardRenderer } from './Cards'
import type { Action, Msg } from '../types'

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
        />
      ))}
    </div>
  )
}

function Welcome({ name, onQuick }: { name: string; onQuick: (t: string) => void }) {
  return (
    <div className="welcome">
      <div className="welcome-orb" aria-hidden>
        <span className="o1" />
        <span className="o2" />
        <span className="o3" />
      </div>
      <div className="welcome-title">我是{name}</div>
      <div className="welcome-sub">按住麦克风说话，或点下方指令试试</div>
      <div className="welcome-chips">
        {['打开空调26度', '附近的充电站', '讲个笑话'].map((q) => (
          <button key={q} className="welcome-chip" onClick={() => onQuick(q)}>
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
}: {
  msg: Msg
  isLast: boolean
  awaitConfirm: boolean
  onConfirm: (reply: '确认' | '取消') => void
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
        {msg.pending ? (
          <ThinkingDots />
        ) : (
          <div className="text">
            {msg.text}
            {msg.streaming && <span className="caret" />}
          </div>
        )}

        {msg.uiCard && <CardRenderer card={msg.uiCard} />}

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
