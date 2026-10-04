// 对话记录里的一条消息（M1-3 建立）：user/assistant/pending/streaming/error/rejected/超时 + 过程区折叠条 +
// 追问 chips + 长按复制正文。
// 气泡内确认按钮已删（打磨批 E，裁决 J1）：承诺面 Focus Dock 是唯一的确认入口——同一个待确认不许有两个入口。
// Android Visual v3（D6，Figma UserBubble / AnswerBlock / AnswerStatus / ProcessFold / ProactiveHeader）：
//  · 用户 = accentSoft 实色气泡右对齐（圆角 20/20/6/20，无描边）；转写草稿 = 1.5 虚线 lineStrong + 光标；
//  · 助手 = **不再有气泡**，左对齐占满记录区；自上而下：类别头 / 过程区 → 正文 → 状态行 → 卡片 → 各项结果 →
//    追问 chips → 回执；
//  · 待确认不再给回答描琥珀边：确认只在 Dock 上（D-1 画板里回答就是普通正文）。
// 打磨批 A（评审 P04 / P12 / P14）：助手去头像，活跃态由 ThinkDots / StreamCursor 表达；长按 = 复制正文
// （两种都支持），trace 的排障通道在 /turn-timeline。
import * as Clipboard from 'expo-clipboard'
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import type { Msg } from '@shared/types.ts'

import type { FollowUpChip } from '../../core/session/followUps'
import type { Receipt } from '../../core/session/receipt'
import { hasAnswerText, isProactive } from '../../core/session/turnView'
import type { FontScalePref } from '../../core/settings/store'

import { StreamCursor, ThinkDots } from '../../ui/aurora'
import { Icon, iconRuntimeAvailable, type IconName } from '../../ui/Icon'
import type { Palette } from '../../ui/theme'
import { RADIUS, TARGET, textStyle } from '../../ui/tokens'
import { CardRenderer } from '../cards/CardRenderer'
import { ControlResult } from '../cards/ControlResult'
import { DrivingCardSummary } from '../cards/DrivingCardSummary'
import type { SendFn } from '../cards/parts'
import { ExecutionReceipt } from './ExecutionReceipt'
import { FollowUpChips } from './FollowUpChips'
import { useRevealedText } from './useRevealedText'
import { ResultDetailsFold } from './ResultDetailsFold'

// 主动播报标题按**种类**取（hmi ChatView PROACTIVE_LABEL 同款）；图标照 Figma ProactiveHeader
const PROACTIVE_LABEL: Record<string, string> = {
  scene_suggest: '主动播报 · AI 建议',
  scene_verify: '主动播报 · 执行反馈',
  reminder_fired: '主动播报 · 提醒到点',
}
const PROACTIVE_ICON: Record<string, IconName> = {
  scene_suggest: 'lightbulb',
  scene_verify: 'check-circle',
  reminder_fired: 'clock',
}

function ProcessFold({ p, msg, driving, fontScale }: { p: Palette; msg: Msg; driving: boolean; fontScale: FontScalePref }) {
  const [open, setOpen] = useState(false)
  const steps = msg.process || []
  if (!steps.length) return null
  // 行车极简（B4-11 / §6「过程区单行」）。两个来源都算：`Msg.driving` 是 Edge 按 VAL 标在
  // **这条消息**上的事实（types.ts:28 原话「行车态（由 Edge 按 VAL 标注）：行车极简、不可展开」），
  // `driving` 是**此刻**的行车档——历史消息在行车时也不该展开成十行。
  const terse = !!msg.driving || driving
  const expanded = !terse && (msg.processActive || open)
  const icons = iconRuntimeAvailable()
  // 进行中 = 刷新；收起 = 右箭头；展开 = 下箭头；行车单行不带图标（Figma ProcessFold 四态）
  const icon: IconName | null = !icons || terse ? null : msg.processActive ? 'refresh' : expanded ? 'chevron-down' : 'chevron-right'
  const label = msg.processActive
    ? terse
      ? `${steps[steps.length - 1]?.label || '处理中'}…`
      : '处理中…'
    : terse || icons
      ? `过程 ${steps.length} 步`
      : `${open ? '▾' : '▸'} 过程 ${steps.length} 步`
  return (
    <View style={{ gap: 4 }}>
      {/* 打磨批 A（评审 P14）：可点文字的触控高度 = 目标高（原来只有一行字高） */}
      <Pressable
        testID="process-fold-toggle"
        onPress={() => setOpen(!open)}
        disabled={terse || !!msg.processActive}
        accessibilityState={terse || msg.processActive ? undefined : { expanded }}
        style={{ minHeight: p.target(TARGET.parked), flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start' }}
      >
        {icon ? <Icon name={icon} size={14} color={p.teal} /> : null}
        <Text testID="process-fold" numberOfLines={1} style={[textStyle('caption', fontScale), { color: p.teal }]}>
          {label}
        </Text>
      </Pressable>
      {expanded &&
        steps.map((s, i) => {
          const running = s.status === 'running'
          return (
            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              {icons ? (
                <Icon name={running ? 'refresh' : 'check'} size={12} color={running ? p.amber : p.teal} />
              ) : (
                <Text style={[textStyle('caption', fontScale), { color: running ? p.amber : p.teal }]}>{running ? '○' : '✓'}</Text>
              )}
              <Text style={[textStyle('caption', fontScale), { color: p.fg3, flex: 1 }]} numberOfLines={2}>
                {s.label}
                {s.summary ? `：${s.summary}` : ''}
              </Text>
            </View>
          )
        })}
    </View>
  )
}

export interface BubbleProps {
  p: Palette
  msg: Msg
  /** 链路断开时回答只到了一部分的那条（`SessionState.uncertainIds`，判据 settleLinkLost）：文字原样留、
   *  标「网络断开」+ 重发键。一个字没到的那种走 error，不经这个标 */
  uncertain?: boolean
  /** 转写草稿（方案 §5.2.1）：虚线边 + 光标，定稿后由同一条气泡接管（HMI PartialUserBubble 同款形态） */
  draft?: boolean
  /** 被打断（方案 §5.2 规则 4）：文字定格 + 灰字「已打断」，不是错误样式 */
  interrupted?: boolean
  /** 端到端自答轮：角标「端到端」，长按看「转写由语音模型生成」（方案 §5.2.2，Q7） */
  s2s?: boolean
  /** 带过视觉抓帧：相机角标（方案 §5.5）；不做预览 */
  vision?: boolean
  /** 执行回执（core/session/receipt.ts 算好传进来；null=这条没有可回执的事） */
  receipt?: Receipt | null
  /** 追问 chips（P2b 从 Composer 挪进回答末尾）——**由宿主算**（判据 core/session/followUps.ts），
   *  组件只画；空 / 缺省 = 不画这一行 */
  chips?: FollowUpChip[]
  /** 循环类小动效要不要动（reduce-motion，判据 orbPolicy.loopsAnimated；B4-3） */
  loops: boolean
  /** 此刻行车档（§6「过程区单行」、chips 目标 56）：与消息自带的 `Msg.driving` 取或 */
  driving: boolean
  fontScale?: FontScalePref
  onSend: SendFn
  /** 重发（打磨批 F / 评审 P13 ③）：只给 error 与「发送状态未知」两类助手消息。宿主取紧邻的上一条用户原话、
   *  走 core.send（**新 request_id**，M3-W「不自动重发」不变——这是用户手动发起） */
  onResend?(): void
  /** 已经重发过：标一句「已重发」，不再给第二个重发键 */
  resent?: boolean
}

export function MessageBubble({ p, msg, uncertain, draft, interrupted, s2s, vision, receipt, chips, loops, driving, fontScale = 'normal', onSend, onResend, resent }: BubbleProps) {
  const [copied, setCopied] = useState(false)
  const [hint, setHint] = useState(false)
  // 回答文字匀速上屏（2026-09-18）：记录里的 msg.text 逐片即时累积，**只有显示**按节拍追（判据 streamReveal.ts）；
  // 逐片流式与只在 final 里到达的整段同一种走法。只给助手；用户气泡（转写草稿按稳定 segment 整段替换）不追
  const shownText = useRevealedText(msg.id, msg.text, msg.role === 'assistant')
  // 长按 = 复制正文（打磨批 A / P12）：用户与助手同一条路。端到端轮顺带给「转写由语音模型生成」的说明
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
  const caption = textStyle('caption', fontScale)
  const icons = iconRuntimeAvailable()
  // 流式光标 8×18（Figma StreamCursor）：高跟正文行高走
  const cursorH = p.font(18)
  if (msg.role === 'user') {
    return (
      <View style={{ alignItems: 'flex-end', marginVertical: 6 }}>
        <Pressable
          onLongPress={copyText}
          accessibilityHint="长按复制这句话"
          style={{
            backgroundColor: p.accentSoft,
            borderWidth: draft ? 1.5 : 0,
            borderStyle: 'dashed',
            borderColor: draft ? p.lineStrong : 'transparent',
            borderRadius: 20,
            borderBottomRightRadius: 6,
            paddingHorizontal: 16,
            paddingVertical: 12,
            maxWidth: '86%',
            gap: 2,
          }}
        >
          {s2s ? <Text style={[caption, { color: p.teal }]}>端到端</Text> : null}
          {vision ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
              {icons ? <Icon name="camera" size={12} color={p.fg3} /> : null}
              <Text style={[caption, { color: p.fg3 }]}>看图</Text>
            </View>
          ) : null}
          <Text style={[textStyle('bodyM', fontScale), { color: p.fg1 }]}>
            {msg.text}
            {draft ? <StreamCursor h={cursorH} animated={loops} /> : null}
          </Text>
          {hint ? <Text style={[caption, { color: p.fg3 }]}>转写由语音模型生成，可能与原话有出入</Text> : null}
          {copied ? <Text style={[caption, { color: p.green }]}>已复制</Text> : null}
        </Pressable>
      </View>
    )
  }

  const proactive = isProactive(msg)
  // 重发键（Figma AnswerStatus 的 Button/重发）：断网未收完与出错两种状态行共用；重发过只剩一句「已重发」
  const resend =
    (msg.error || uncertain) && !msg.pending ? (
      resent ? (
        <Text testID="bubble-resent" style={[caption, { color: p.fg3 }]}>已重发</Text>
      ) : onResend ? (
        <Pressable
          testID="bubble-resend"
          accessibilityRole="button"
          accessibilityLabel="重发这一句"
          onPress={onResend}
          style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', paddingHorizontal: 14, borderRadius: RADIUS.full, backgroundColor: p.surfaceHighest }}
        >
          <Text style={[textStyle('labelM', fontScale), { color: p.accent }]}>重发</Text>
        </Pressable>
      ) : null
    ) : null

  return (
    // 去气泡后回答占满记录区；整块仍是长按复制的目标（卡片、chips 里的按钮各自响应点按）
    <View style={{ marginVertical: 6 }}>
      <Pressable
        // e2e 判据（M3-5 flow ③）：**「消息补达」只能断言挂起态消失**——
        // 断言回答文本会被用户自己那条气泡满足（同一句话），那是假绿。
        testID={msg.pending ? 'msg-pending' : undefined}
        onLongPress={copyText}
        accessibilityHint={msg.text ? '长按复制回答' : undefined}
        style={{ gap: 10 }}
      >
        {s2s ? <Text style={[caption, { color: p.teal }]}>端到端</Text> : null}
        {proactive ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            {icons ? <Icon name={PROACTIVE_ICON[msg.proactiveKind || ''] ?? 'info'} size={14} color={p.teal} /> : null}
            <Text style={[caption, { color: p.teal, flexShrink: 1 }]}>
              {PROACTIVE_LABEL[msg.proactiveKind || ''] || '主动播报 · 任务提示'}
            </Text>
          </View>
        ) : null}
        {msg.pending ? (
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <ThinkDots color={p.accent} animated={loops} />
            <Text style={[textStyle('bodyM', fontScale), { color: p.fg3 }]}>正在思考…</Text>
          </View>
        ) : null}
        <ProcessFold p={p} msg={msg} driving={driving} fontScale={fontScale} />
        {msg.rejected ? <Text style={[caption, { color: p.fg3 }]}>已忽略疑似环境人声（点错了可重说一遍）</Text> : null}
        {msg.error ? (
          // 出错：警示图标 + 红字原话 + 重发，同一行（Figma AnswerStatus · Error）。不折行容器：长原话在中间一栏
          // 自己换行，图标与重发键留在两侧（真机 360dp 上 flexWrap 会让图标独占一行、键再掉一行）
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {icons ? <Icon name="warning" size={16} color={p.red} /> : null}
            {msg.text ? (
              <Text testID="bubble-text" style={{ ...textStyle('bodyM', fontScale), color: p.red, flexShrink: 1 }}>
                {msg.text}
              </Text>
            ) : null}
            {resend}
          </View>
        ) : hasAnswerText(msg, !!interrupted) ? (
          <Text testID="bubble-text" style={{ ...textStyle('bodyM', fontScale), color: p.fg1 }}>
            {shownText}
            {/* 光标跟着「还在长」走：流式中，或 final 已到但显示还没追到尾 */}
            {msg.streaming || shownText.length < msg.text.length ? <StreamCursor h={cursorH} animated={loops} /> : null}
          </Text>
        ) : null}
        {uncertain && !msg.error ? (
          // 断网未收完：正文原样在上，这一行说明 + 重发（Figma AnswerStatus · LinkLost）
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text testID="bubble-link-lost" style={[caption, { color: p.fg3, flexShrink: 1 }]}>网络断开，回答没有收完</Text>
            {resend}
          </View>
        ) : null}
        {interrupted ? <Text style={[caption, { color: p.fg3 }]}>已打断</Text> : null}
        {/* 行车档（Figma 08 页 DR-1「一屏一卡」）：记录里的卡与语音层同一张行车摘要——标题 + 主数值 + ≤2 字段 + 1 主按钮；
            看的是**此刻**的行车档，停车退出后恢复全量卡 */}
        {msg.uiCard ? (
          driving ? (
            <DrivingCardSummary p={p} fontScale={fontScale} card={msg.uiCard} onSend={onSend} />
          ) : (
            <CardRenderer p={p} card={msg.uiCard} onSend={onSend} />
          )
        ) : null}
        {/* 车控结果卡（D17）：与回执「执行」行同一份逐项结果；媒体控制不出卡 */}
        {receipt?.kind === 'action' && receipt.items.some((i) => i.kind === 'vehicle') ? (
          <ControlResult p={p} items={receipt.items.filter((i) => i.kind === 'vehicle')} at={receipt.executed.at} driving={driving} />
        ) : null}
        <ResultDetailsFold p={p} msg={msg} driving={driving} onSend={onSend} />
        {chips?.length ? (
          <FollowUpChips p={p} fontScale={fontScale} target={driving ? TARGET.driving : TARGET.parked} chips={chips} onSend={onSend} />
        ) : null}
        {receipt ? <ExecutionReceipt p={p} receipt={receipt} /> : null}
        {copied ? <Text style={[caption, { color: p.green }]}>已复制</Text> : null}
      </Pressable>
    </View>
  )
}
