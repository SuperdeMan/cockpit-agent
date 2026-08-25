// 消息气泡（实施计划 M1-3）：气泡态全集 user/assistant/pending/streaming/error/rejected/超时
// + 过程区折叠条（execute 合并态在 store 已做）+ 确认条（按台账渲染、可多条并存）+
// followUp 提示 + trace_id 长按复制。文案对照 hmi ChatView 语义，不追像素。
import * as Clipboard from 'expo-clipboard'
import { useState } from 'react'
import { ActivityIndicator, Pressable, Text, View } from 'react-native'

import type { Msg } from '@shared/types.ts'

import type { Palette } from '../../ui/theme'
import { CardRenderer } from '../cards/CardRenderer'
import type { SendFn } from '../cards/parts'

// 主动播报标题按**种类**取（hmi ChatView PROACTIVE_LABEL 同款）
const PROACTIVE_LABEL: Record<string, string> = {
  scene_suggest: '主动播报 · AI 建议',
  scene_verify: '主动播报 · 执行反馈',
  reminder_fired: '主动播报 · 提醒到点',
}

function ProcessFold({ p, msg }: { p: Palette; msg: Msg }) {
  const [open, setOpen] = useState(false)
  const steps = msg.process || []
  if (!steps.length) return null
  const expanded = msg.processActive || open
  return (
    <View style={{ gap: 4 }}>
      <Pressable onPress={() => setOpen(!open)} disabled={!!msg.processActive}>
        <Text style={{ color: p.teal, fontSize: p.font(11) }}>
          {msg.processActive ? '⟳ 处理中…' : `${open ? '▾' : '▸'} 过程 ${steps.length} 步`}
        </Text>
      </Pressable>
      {expanded &&
        steps.map((s, i) => (
          <View key={i} style={{ flexDirection: 'row', gap: 6 }}>
            <Text style={{ color: s.status === 'running' ? p.amber : p.green, fontSize: p.font(11) }}>
              {s.status === 'running' ? '○' : '✓'}
            </Text>
            <Text style={{ color: p.fg3, fontSize: p.font(11), flex: 1 }} numberOfLines={2}>
              {s.label}
              {s.summary ? `：${s.summary}` : ''}
            </Text>
          </View>
        ))}
    </View>
  )
}

export interface BubbleProps {
  p: Palette
  msg: Msg
  /** 该气泡的确认条此刻是否可点（台账 live / 位置征询挂起） */
  confirmActive: boolean
  onConfirm(reply: '确认' | '取消', operationId?: string): void
  onSend: SendFn
}

export function MessageBubble({ p, msg, confirmActive, onConfirm, onSend }: BubbleProps) {
  const [copied, setCopied] = useState(false)
  if (msg.role === 'user') {
    return (
      <View style={{ alignItems: 'flex-end', marginVertical: 4 }}>
        <View
          style={{
            backgroundColor: p.accent,
            borderRadius: 16,
            borderBottomRightRadius: 4,
            paddingHorizontal: 14,
            paddingVertical: 9,
            maxWidth: '86%',
          }}
        >
          <Text style={{ color: '#fff', fontSize: p.font(15) }}>{msg.text}</Text>
        </View>
      </View>
    )
  }

  const longPressTrace = () => {
    if (!msg.traceId) return
    void Clipboard.setStringAsync(msg.traceId).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  const proactive = msg.proactiveKind !== undefined || msg.text.startsWith('💡 ')
  return (
    <View style={{ alignItems: 'flex-start', marginVertical: 4 }}>
      <Pressable
        onLongPress={longPressTrace}
        style={{
          backgroundColor: p.panel,
          borderColor: msg.error ? p.red : proactive ? p.accentSoft : p.line,
          borderWidth: 1,
          borderRadius: 16,
          borderBottomLeftRadius: 4,
          paddingHorizontal: 14,
          paddingVertical: 10,
          maxWidth: '92%',
          gap: 8,
        }}
      >
        {proactive ? (
          <Text style={{ color: p.teal, fontSize: p.font(11), fontWeight: '600' }}>
            {PROACTIVE_LABEL[msg.proactiveKind || ''] || '主动播报 · 任务提示'}
          </Text>
        ) : null}
        {msg.pending ? (
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <ActivityIndicator size="small" color={p.accent} />
            <Text style={{ color: p.fg3, fontSize: p.font(13) }}>正在思考…</Text>
          </View>
        ) : null}
        <ProcessFold p={p} msg={msg} />
        {msg.rejected ? (
          <Text style={{ color: p.fg3, fontSize: p.font(12), fontStyle: 'italic' }}>
            已忽略疑似环境人声（点错了可重说一遍）
          </Text>
        ) : null}
        {msg.text ? (
          <Text
            style={{
              color: msg.error ? p.red : p.fg1,
              fontSize: p.font(15),
              lineHeight: p.font(22),
            }}
          >
            {msg.text}
            {msg.streaming ? ' ▍' : ''}
          </Text>
        ) : null}
        {msg.uiCard ? <CardRenderer p={p} card={msg.uiCard} onSend={onSend} /> : null}
        {(msg.actions || []).length ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            已执行 {(msg.actions || []).map((a) => a.type).join('、')}
          </Text>
        ) : null}
        {msg.needConfirm && confirmActive ? (
          <View style={{ flexDirection: 'row', gap: 10, marginTop: 2 }}>
            <Pressable
              onPress={() => onConfirm('取消', msg.operationId)}
              style={{
                flex: 1,
                minHeight: 44,
                borderRadius: 12,
                borderWidth: 1,
                borderColor: p.line,
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Text style={{ color: p.fg2, fontSize: p.font(14) }}>取消</Text>
            </Pressable>
            <Pressable
              onPress={() => onConfirm('确认', msg.operationId)}
              style={{
                flex: 2,
                minHeight: 44,
                borderRadius: 12,
                backgroundColor: p.amberSoft,
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Text style={{ color: p.amber, fontSize: p.font(14), fontWeight: '600' }}>确认</Text>
            </Pressable>
          </View>
        ) : null}
        {msg.followUp ? (
          <Pressable onPress={() => onSend(msg.followUp!)}>
            <Text style={{ color: p.accent, fontSize: p.font(12) }}>💬 {msg.followUp}</Text>
          </Pressable>
        ) : null}
        {copied ? <Text style={{ color: p.green, fontSize: p.font(10) }}>trace 已复制</Text> : null}
      </Pressable>
    </View>
  )
}
