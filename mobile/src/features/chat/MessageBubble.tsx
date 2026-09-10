// 消息气泡（M1-3 建立，Aurora Glass 复刻轮重皮）：气泡态全集 user/assistant/pending/streaming/
// error/rejected/超时 + 过程区折叠条 + 待确认的琥珀边（按台账渲染、可多条并存）+ followUp + 长按复制正文。
// 气泡内确认按钮已删（打磨批 E，裁决 J1）：承诺面 Focus Dock 是唯一的确认入口——同一个待确认不许有两个入口。
// 视觉照 hmi ChatView A-6：user=交互蓝玻璃右对齐（18/18/4/18），assistant=玻璃左对齐（4/18/18/18），
// confirm/error 换语义 tone 边框。
// 打磨批 A（评审 P04 / P12 / P14）：助手气泡**去头像**——同屏可操作的光球只剩顶栏与 Composer 两颗，
// 活跃态（思考 / 流式）由气泡内的 ThinkDots / StreamCursor 表达；长按 = 复制正文（两种气泡都支持），
// trace 的排障通道搬到 /turn-timeline。
import * as Clipboard from 'expo-clipboard'
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import type { Msg } from '@shared/types.ts'

import type { Receipt } from '../../core/session/receipt'
import { isProactive } from '../../core/session/turnView'

import { StreamCursor, ThinkDots } from '../../ui/aurora'
import type { Palette } from '../../ui/theme'
import { CardRenderer } from '../cards/CardRenderer'
import type { SendFn } from '../cards/parts'
import { ExecutionReceipt } from './ExecutionReceipt'

// 主动播报标题按**种类**取（hmi ChatView PROACTIVE_LABEL 同款）
const PROACTIVE_LABEL: Record<string, string> = {
  scene_suggest: '主动播报 · AI 建议',
  scene_verify: '主动播报 · 执行反馈',
  reminder_fired: '主动播报 · 提醒到点',
}

function ProcessFold({ p, msg, driving }: { p: Palette; msg: Msg; driving: boolean }) {
  const [open, setOpen] = useState(false)
  const steps = msg.process || []
  if (!steps.length) return null
  // 行车极简（B4-11 / §6「过程区单行」）。两个来源都算：`Msg.driving` 是 Edge 按 VAL 标在
  // **这条气泡**上的事实（types.ts:28 原话「行车态（由 Edge 按 VAL 标注）：行车极简、不可展开」），
  // `driving` 是**此刻**的行车档——历史气泡在行车时也不该展开成十行。
  const terse = !!msg.driving || driving
  const expanded = !terse && (msg.processActive || open)
  return (
    <View style={{ gap: 4 }}>
      {/* 打磨批 A（评审 P14）：可点文字的触控高度 44（原来只有一行字高） */}
      <Pressable
        testID="process-fold-toggle"
        hitSlop={2}
        onPress={() => setOpen(!open)}
        disabled={terse || !!msg.processActive}
        style={{ minHeight: 44, justifyContent: 'center' }}
      >
        <Text testID="process-fold" numberOfLines={1} style={{ color: p.teal, fontSize: p.font(11) }}>
          {msg.processActive
            ? terse
              ? `⟳ ${steps[steps.length - 1]?.label || '处理中'}…`
              : '⟳ 处理中…'
            : terse
              ? `过程 ${steps.length} 步`
              : `${open ? '▾' : '▸'} 过程 ${steps.length} 步`}
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
  /** 该气泡的待确认此刻是否仍活着（台账 live / 位置征询挂起）：只决定琥珀边，按钮在 Dock */
  confirmActive: boolean
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
  /** 执行回执（core/session/receipt.ts 算好传进来；null=这条没有可回执的事） */
  receipt?: Receipt | null
  /** 循环类小动效要不要动（reduce-motion，判据 orbPolicy.loopsAnimated；B4-3） */
  loops: boolean
  /** 此刻行车档（§6「过程区单行」）：与气泡自带的 `Msg.driving` 取或 */
  driving: boolean
  onSend: SendFn
}

export function MessageBubble({ p, msg, confirmActive, uncertain, draft, interrupted, s2s, vision, receipt, loops, driving, onSend }: BubbleProps) {
  const [copied, setCopied] = useState(false)
  const [hint, setHint] = useState(false)
  // 长按 = 复制正文（打磨批 A / P12）：用户与助手两种气泡同一条路。端到端轮顺带给「转写由语音模型生成」的说明
  const copyText = () => {
    if (msg.text) {
      void Clipboard.setStringAsync(msg.text).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      })
    }
    if (s2s) {
      setHint(true)
      setTimeout(() => setHint(false), 2500)
    }
  }
  if (msg.role === 'user') {
    return (
      <View style={{ alignItems: 'flex-end', marginVertical: 5 }}>
        <Pressable
          onLongPress={copyText}
          accessibilityHint="长按复制这句话"
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
            {draft ? <StreamCursor h={p.font(15)} animated={loops} /> : null}
          </Text>
          {hint ? (
            <Text style={{ color: p.fg3, fontSize: p.font(11), marginTop: 4 }}>转写由语音模型生成，可能与原话有出入</Text>
          ) : null}
          {copied ? <Text style={{ color: p.green, fontSize: p.font(11), marginTop: 4 }}>已复制</Text> : null}
        </Pressable>
      </View>
    )
  }

  const proactive = isProactive(msg)
  // 语义 tone：待确认=琥珀 / 错误=红 / 常态=玻璃（hmi AIBubbleBase toneStyle 同款）
  const tone =
    msg.needConfirm && confirmActive
      ? { borderColor: 'rgba(245,158,11,0.32)', borderTopColor: 'rgba(245,158,11,0.45)' }
      : msg.error
        ? { borderColor: 'rgba(239,68,68,0.28)', borderTopColor: 'rgba(239,68,68,0.40)' }
        : { borderColor: p.fill2, borderTopColor: p.hi }

  return (
    // 去头像后气泡左对齐、宽度用满记录区（原来 28dp 球 + 8 间距 + 92% 上限，360dp 屏正文损失约 36dp）
    <View style={{ alignItems: 'stretch', marginVertical: 5 }}>
      <Pressable
        // e2e 判据（M3-5 flow ③）：**「消息补达」只能断言挂起态消失**——
        // 断言回答文本会被用户自己那条气泡满足（同一句话），那是假绿。
        testID={msg.pending ? 'msg-pending' : undefined}
        onLongPress={copyText}
        accessibilityHint={msg.text ? '长按复制回答' : undefined}
        style={{
          backgroundColor: p.fill,
          borderWidth: 1,
          ...tone,
          borderRadius: 18,
          borderTopLeftRadius: 4,
          paddingHorizontal: 14,
          paddingVertical: 11,
          maxWidth: '100%',
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
            <ThinkDots color={p.accent} animated={loops} />
            <Text style={{ color: p.fg3, fontSize: p.font(13) }}>正在思考…</Text>
          </View>
        ) : null}
        {uncertain ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            发送状态未知（网络刚断过；连上后若无回音请再说一次）
          </Text>
        ) : null}
        <ProcessFold p={p} msg={msg} driving={driving} />
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
            {msg.streaming ? <StreamCursor h={p.font(15)} animated={loops} /> : null}
          </Text>
        ) : null}
        {interrupted ? <Text style={{ color: p.fg3, fontSize: p.font(11) }}>已打断</Text> : null}
        {msg.uiCard ? <CardRenderer p={p} card={msg.uiCard} onSend={onSend} /> : null}
        {receipt ? <ExecutionReceipt p={p} receipt={receipt} /> : null}
        {msg.followUp ? (
          // 打磨批 A（评审 P14）：可点文字的触控高度 44
          <Pressable testID="followup-link" hitSlop={2} onPress={() => onSend(msg.followUp!)} style={{ minHeight: 44, justifyContent: 'center' }}>
            <Text style={{ color: p.accent, fontSize: p.font(12) }}>💬 {msg.followUp}</Text>
          </Pressable>
        ) : null}
        {copied ? <Text style={{ color: p.green, fontSize: p.font(11) }}>已复制</Text> : null}
      </Pressable>
    </View>
  )
}
