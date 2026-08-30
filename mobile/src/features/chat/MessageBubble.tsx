// 消息气泡（M1-3 建立，Aurora Glass 复刻轮重皮）：气泡态全集 user/assistant/pending/streaming/
// error/rejected/超时 + 过程区折叠条 + 确认条（按台账渲染、可多条并存）+ followUp + trace 长按复制。
// 视觉照 hmi ChatView A-6：user=交互蓝玻璃右对齐（18/18/4/18），assistant=光球头像+玻璃（4/18/18/18），
// confirm/error 换语义 tone 边框。光球头像只在气泡活跃（pending/streaming/process）时跑动画——
// 判据取「这条消息此刻在动」而非「是不是最后一条」，列表里历史气泡全部静态（§10 性能纪律）。
import * as Clipboard from 'expo-clipboard'
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import type { Msg } from '@shared/types.ts'

import { isProactive } from '../../core/session/turnView'

import { AuroraOrb, StreamCursor, ThinkDots, type OrbState } from '../../ui/aurora'
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
  /** 气泡内还渲不渲确认按钮：Focus Dock 开着时为 false——**同一个待确认不许有两个入口**，
   *  两处都能点会让「点了哪一个」变成猜（承诺面开关关掉时它就回来） */
  inlineConfirm: boolean
  /** 发送状态未知（断线瞬间发出的那条，`SessionState.uncertainIds`） */
  uncertain?: boolean
  /** 转写草稿（方案 §5.2.1）：虚线边 + 光标，定稿后由同一条气泡接管（HMI PartialUserBubble 同款形态） */
  draft?: boolean
  /** 被打断（方案 §5.2 规则 4）：文字定格 + 灰字「已打断」，不是错误样式 */
  interrupted?: boolean
  /** 端到端自答轮：角标「端到端」，长按看「转写由语音模型生成」（方案 §5.2.2，Q7） */
  s2s?: boolean
  /** 带过视觉抓帧：📷 角标（方案 §5.5）；不做预览 */
  vision?: boolean
  onConfirm(reply: '确认' | '取消', operationId?: string): void
  onSend: SendFn
}

export function MessageBubble({ p, msg, confirmActive, inlineConfirm, uncertain, draft, interrupted, s2s, vision, onConfirm, onSend }: BubbleProps) {
  const [copied, setCopied] = useState(false)
  const [hint, setHint] = useState(false)
  if (msg.role === 'user') {
    return (
      <View style={{ alignItems: 'flex-end', marginVertical: 5 }}>
        <Pressable
          onLongPress={
            s2s
              ? () => {
                  setHint(true)
                  setTimeout(() => setHint(false), 2500)
                }
              : undefined
          }
          style={{
            backgroundColor: `${p.accent}1F`,
            borderWidth: 1,
            borderStyle: draft ? 'dashed' : 'solid',
            borderColor: draft ? `${p.accent}4D` : `${p.accent}38`,
            borderRadius: 18,
            borderBottomRightRadius: 4,
            paddingHorizontal: 15,
            paddingVertical: 10,
            maxWidth: '86%',
            boxShadow: p.dark ? '0 4px 16px rgba(0,0,0,0.22)' : '0 2px 10px rgba(10,14,26,0.06)',
          }}
        >
          {s2s ? <Text style={{ color: p.teal, fontSize: p.font(10), marginBottom: 2 }}>端到端</Text> : null}
          {vision ? <Text style={{ color: p.fg3, fontSize: p.font(10), marginBottom: 2 }}>📷 看图</Text> : null}
          <Text style={{ color: p.fg1, fontSize: p.font(15), lineHeight: p.font(23) }}>
            {msg.text}
            {draft ? <StreamCursor h={p.font(15)} /> : null}
          </Text>
          {hint ? (
            <Text style={{ color: p.fg3, fontSize: p.font(10), marginTop: 4 }}>转写由语音模型生成，可能与原话有出入</Text>
          ) : null}
        </Pressable>
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

  const proactive = isProactive(msg)
  // 光球态与动画开关：活跃中（思考/流式/过程区推进）才动，历史气泡静态
  const active = !!(msg.pending || msg.streaming || msg.processActive)
  const orbState: OrbState = msg.pending || msg.processActive ? 'thinking' : msg.streaming ? 'speaking' : 'idle'
  // 语义 tone：待确认=琥珀 / 错误=红 / 常态=玻璃（hmi AIBubbleBase toneStyle 同款）
  const tone =
    msg.needConfirm && confirmActive
      ? { borderColor: 'rgba(245,158,11,0.32)', borderTopColor: 'rgba(245,158,11,0.45)' }
      : msg.error
        ? { borderColor: 'rgba(239,68,68,0.28)', borderTopColor: 'rgba(239,68,68,0.40)' }
        : { borderColor: p.fill2, borderTopColor: p.hi }

  return (
    <View style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-start', marginVertical: 5 }}>
      <View style={{ marginTop: 2 }}>
        <AuroraOrb size={28} state={orbState} animated={active} />
      </View>
      <Pressable
        // e2e 判据（M3-5 flow ③）：**「消息补达」只能断言挂起态消失**——
        // 断言回答文本会被用户自己那条气泡满足（同一句话），那是假绿。
        testID={msg.pending ? 'msg-pending' : undefined}
        onLongPress={longPressTrace}
        style={{
          flex: 1,
          backgroundColor: p.fill,
          borderWidth: 1,
          ...tone,
          borderRadius: 18,
          borderTopLeftRadius: 4,
          paddingHorizontal: 14,
          paddingVertical: 11,
          maxWidth: '92%',
          gap: 8,
          boxShadow: p.dark
            ? '0 4px 20px rgba(0,0,0,0.30), inset 0 1px 0 rgba(255,255,255,0.06)'
            : '0 3px 14px rgba(10,14,26,0.07)',
        }}
      >
        {s2s ? <Text style={{ color: p.teal, fontSize: p.font(11), fontWeight: '600' }}>端到端</Text> : null}
        {proactive ? (
          <Text style={{ color: p.teal, fontSize: p.font(11), fontWeight: '600' }}>
            {PROACTIVE_LABEL[msg.proactiveKind || ''] || '主动播报 · 任务提示'}
          </Text>
        ) : null}
        {msg.pending ? (
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <ThinkDots color={p.accent} />
            <Text style={{ color: p.fg3, fontSize: p.font(13) }}>正在思考…</Text>
          </View>
        ) : null}
        {uncertain ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            发送状态未知（网络刚断过；连上后若无回音请再说一次）
          </Text>
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
              lineHeight: p.font(23),
            }}
          >
            {msg.text}
            {msg.streaming ? <StreamCursor h={p.font(15)} /> : null}
          </Text>
        ) : null}
        {interrupted ? <Text style={{ color: p.fg3, fontSize: p.font(11) }}>已打断</Text> : null}
        {msg.uiCard ? <CardRenderer p={p} card={msg.uiCard} onSend={onSend} /> : null}
        {(msg.actions || []).length ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            已执行 {(msg.actions || []).map((a) => a.type).join('、')}
          </Text>
        ) : null}
        {msg.needConfirm && confirmActive && inlineConfirm ? (
          <View style={{ flexDirection: 'row', gap: 10, marginTop: 2 }}>
            <Pressable
              testID="confirm-cancel"
              onPress={() => onConfirm('取消', msg.operationId)}
              style={{
                flex: 1,
                minHeight: 44,
                borderRadius: 14,
                borderWidth: 1,
                borderColor: p.fill2,
                backgroundColor: p.fill,
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Text style={{ color: p.fg2, fontSize: p.font(14) }}>取消</Text>
            </Pressable>
            <Pressable
              testID="confirm-accept"
              onPress={() => onConfirm('确认', msg.operationId)}
              style={{
                flex: 2,
                minHeight: 44,
                borderRadius: 14,
                backgroundColor: p.amberSoft,
                borderWidth: 1,
                borderColor: 'rgba(245,158,11,0.35)',
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
