// mobile/src/features/chat/PrivacyRail.tsx
// 隐私栏（方案 §5.10）：红线三条件的**实时**表达。G0 实色；读 snapshot.privacy + 激活日志。
// 「当前用户」= token 身份（App 端没有声纹，§2.3 信道约束），不做「未确认说话人 / 访客模式」。
import { useEffect, useState } from 'react'
import { Modal, Pressable, ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { activityLog, type ActivityEntry } from '@/core/presence/activityLog'
import type { PresenceSnapshot } from '@/core/presence/presence'
import { settingsStore, type FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

// 麦克风这一行**按 capture 再分一次**，不是直接映射 `privacy.mic` 的三档。
// 原因是判据层把两件事并进了同一个 `edge`：唤醒词待机（端侧 KWS，一个字节都不出机）
// 与**正在录音**（App 的 PTT 走的是服务端 ASR——`core/voice/asr.ts` 连的是
// `ws://…/api/asr/stream`，音频是上传的）。合成一句「转文字后只上传文字」在录音那一刻
// 就是**一句假话**，而这块屏的全部价值就是它说的是真的。
// 判据层要不要多一档（`cloudAsr`）留给 B2/B4 裁——那会动 PresenceSnapshot 类型、
// 画廊样本与覆盖度守卫；B1 先用文案把两件事分开。
function micText(snapshot: PresenceSnapshot): string {
  if (snapshot.privacy.mic === 'off') return '关'
  if (snapshot.privacy.mic === 'cloudAudio') return '原始音频上传中（端到端对话）'
  if (snapshot.capture === 'armed') return '唤醒词待机（端侧监听，不上传）'
  return '正在录音 · 音频上传到语音识别服务（识别完只留文字）'
}

function when(e: ActivityEntry | null): string {
  if (!e) return '—'
  const d = new Date(e.at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')} ${e.note}`
}

export function PrivacyRail({
  p,
  fontScale,
  snapshot,
  visible,
  onClose,
  onStopMic,
}: {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  visible: boolean
  onClose(): void
  /** 一键关闭本轮麦克风（停 PTT / 免唤醒 / ASR 流） */
  onStopMic(): void
}) {
  const [, force] = useState(0)
  useEffect(() => activityLog.subscribe(() => force((n) => n + 1)), [])
  // 订阅式读设置（不是 `getState()`）：这三个按钮**自己就会改设置**，读一份不订阅的快照
  // 会让「关闭免唤醒」按下之后按钮还留在那儿——一个说着假话的隐私面板比没有面板更糟
  const { settings, update } = useStore(settingsStore)
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  const row = (k: string, v: string, tone: string = p.fg1) => (
    <View style={{ flexDirection: 'row', gap: 12, paddingVertical: 6, borderBottomWidth: 1, borderColor: p.line }}>
      <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fontScale), width: 88 }}>{k}</Text>
      <Text style={{ color: tone, fontSize: scale(TYPE.caption, 'text', fontScale), flex: 1 }}>{v}</Text>
    </View>
  )
  const btn = (label: string, run: () => void) => (
    <Pressable
      accessibilityRole="button"
      onPress={run}
      style={{
        minHeight: scale(TARGET.parked, 'target', fontScale),
        justifyContent: 'center',
        paddingHorizontal: 12,
        borderRadius: RADIUS.md,
        borderWidth: 1,
        borderColor: p.fill2,
        backgroundColor: p.fill,
      }}
    >
      <Text style={{ color: p.fg1, fontSize: scale(TYPE.caption, 'text', fontScale) }}>{label}</Text>
    </Pressable>
  )
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={onClose} accessibilityLabel="关闭隐私栏" />
      <View
        testID="privacy-rail"
        style={{
          backgroundColor: solid,
          borderTopLeftRadius: RADIUS['2xl'],
          borderTopRightRadius: RADIUS['2xl'],
          padding: 16,
          gap: 10,
          maxHeight: '70%',
        }}
      >
        <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fontScale), fontWeight: '600' }}>
          隐私 · 现在在采集什么
        </Text>
        <ScrollView>
          {row('麦克风', micText(snapshot), snapshot.privacy.mic === 'cloudAudio' || snapshot.capture !== 'armed' ? p.amber : p.fg1)}
          {row('摄像头', snapshot.privacy.camera === 'singleFrame' ? '正在抓一帧（触发词命中）' : '关')}
          {row('最近一次', `麦 ${when(activityLog.lastOf('mic'))}`)}
          {row('', `摄像头 ${when(activityLog.lastOf('camera'))}`)}
          {row('当前用户', `token ····${snapshot.privacy.user}（App 端身份 = token，不做声纹）`)}
          <Text
            style={{
              color: p.fg3,
              fontSize: scale(TYPE.micro, 'text', fontScale),
              lineHeight: scale(17, 'line', fontScale),
              paddingTop: 8,
            }}
          >
            唤醒词监听在本机，不上传。按住说话与唤醒后的收音，音频会传到你自己的服务器做识别，
            识别完只留文字；端到端挡位则把原始音频交给语音大模型，仅在唤醒后的对话窗内采集。
            拍到的画面只用于回答当前这一句，服务器最多保留两分钟，不落盘、不进记忆。
          </Text>
        </ScrollView>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          {btn('关闭本轮麦克风', () => {
            onStopMic()
            onClose()
          })}
          {settings.handsFree
            ? btn('关闭免唤醒', () => {
                update({ handsFree: false })
                onClose()
              })
            : null}
          {settings.visionEnabled
            ? btn('关闭看图问答', () => {
                update({ visionEnabled: false })
                onClose()
              })
            : null}
        </View>
      </View>
    </Modal>
  )
}
