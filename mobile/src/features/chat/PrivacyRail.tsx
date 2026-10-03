// mobile/src/features/chat/PrivacyRail.tsx
// 隐私栏（方案 §5.10）：红线三条件的**实时**表达。G0 实色；读 snapshot.privacy + 激活日志。
// 「当前用户」= token 身份（App 端没有声纹，§2.3 信道约束），不做「未确认说话人 / 访客模式」。
// v3 P5c（Figma 04 页 P-1）：surfaceHigh 实色底、顶角 28、标题 + 关闭键；键值行 body/m、行间分隔；
// 「结束本轮收音」整宽 Tonal，「关闭免唤醒 / 关闭看图问答」两个 Outlined 各半。内容与判据不动。
import { useEffect, useState } from 'react'
import { Modal, Pressable, ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { activityLog, type ActivityEntry } from '@/core/presence/activityLog'
import { MIC_LABEL, type PresenceSnapshot } from '@/core/presence/presence'
import { settingsStore, type FontScalePref } from '@/core/settings/store'
import { Button } from '@/ui/Button'
import { Icon, iconRuntimeAvailable } from '@/ui/Icon'
import { RADIUS, TARGET, scale, textStyle } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

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
  const body = textStyle('bodyM', fontScale)
  const row = (k: string, v: string, tone: string = p.fg1) => (
    <View style={{ flexDirection: 'row', gap: 12, paddingVertical: 10, borderBottomWidth: 1, borderColor: p.line }}>
      <Text style={[body, { color: p.fg2, width: 84 }]}>{k}</Text>
      <Text style={[body, { color: tone, flex: 1 }]}>{v}</Text>
    </View>
  )
  const mic = activityLog.lastOf('mic')
  const cam = activityLog.lastOf('camera')
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={{ flex: 1, backgroundColor: p.scrim }} onPress={onClose} accessibilityLabel="关闭隐私栏" />
      <View
        testID="privacy-rail"
        style={{
          backgroundColor: p.surfaceHigh,
          borderTopLeftRadius: RADIUS['3xl'],
          borderTopRightRadius: RADIUS['3xl'],
          paddingHorizontal: 16,
          paddingTop: 12,
          paddingBottom: 16,
          gap: 12,
          maxHeight: '75%',
        }}
      >
        <View style={{ alignSelf: 'center', width: 32, height: 4, borderRadius: 2, backgroundColor: p.lineStrong }} />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 8 }}>
          <Text style={[textStyle('titleL', fontScale), { color: p.fg1, flex: 1 }]}>隐私 · 现在在采集什么</Text>
          <Pressable
            testID="privacy-rail-close"
            accessibilityRole="button"
            accessibilityLabel="关闭隐私栏"
            onPress={onClose}
            style={{ width: scale(TARGET.parked, 'target', fontScale), height: scale(TARGET.parked, 'target', fontScale), alignItems: 'center', justifyContent: 'center' }}
          >
            {iconRuntimeAvailable() ? <Icon name="close" size={22} color={p.fg2} /> : <Text style={[body, { color: p.fg2 }]}>关闭</Text>}
          </Pressable>
        </View>
        <ScrollView>
          {/* 四档文案与颜色都取 MIC_LABEL（B2 T2）：颜色与文字不许说两件事——
              B1 那条 `mic==='cloudAudio' || capture!=='armed'` 让默认空闲态的「关」也涂成琥珀（评审 D5） */}
          {row('麦克风', snapshot.privacy.micActive ? '开启' : '关')}
          {row('音频处理', MIC_LABEL[snapshot.privacy.mic].long, MIC_LABEL[snapshot.privacy.mic].tone === 'amber' ? p.amber : p.fg1)}
          {row('摄像头', snapshot.privacy.camera === 'singleFrame' ? '正在抓一帧（触发词命中）' : '关')}
          {row('画面上传', snapshot.privacy.visionUploading ? '单帧上传中' : '无')}
          {/* 最近一次并成一行：摄像头有过激活才补第二句（原来的空标签行在新版式里像排版故障） */}
          {row('最近一次', `麦 ${when(mic)}${cam ? `\n摄像头 ${when(cam)}` : ''}`)}
          {row('当前用户', `token ····${snapshot.privacy.user}（App 端身份 = token，不做声纹）`)}
          <Text style={[textStyle('caption', fontScale), { color: p.fg3, paddingTop: 10 }]}>
            唤醒词监听在本机，不上传。按住说话与唤醒后的收音，音频会传到你自己的服务器做识别，
            识别完只留文字；端到端挡位仅在唤醒后的对话窗内把原始音频上传给语音大模型。
            拍到的画面只用于回答当前这一句，服务器最多保留两分钟，不落盘、不进记忆。
          </Text>
        </ScrollView>
        <View style={{ gap: 8 }}>
          <Button
            p={p}
            testID="privacy-stop-mic"
            variant="tonal"
            fontScale={fontScale}
            label="结束本轮收音"
            onPress={() => {
              onStopMic()
              onClose()
            }}
          />
          {settings.handsFree || settings.visionEnabled ? (
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {settings.handsFree ? (
                <Button
                  p={p}
                  testID="privacy-hands-free-off"
                  variant="outlined"
                  fontScale={fontScale}
                  label="关闭免唤醒"
                  style={{ flex: 1 }}
                  onPress={() => {
                    update({ handsFree: false })
                    onClose()
                  }}
                />
              ) : null}
              {settings.visionEnabled ? (
                <Button
                  p={p}
                  testID="privacy-vision-off"
                  variant="outlined"
                  fontScale={fontScale}
                  label="关闭看图问答"
                  style={{ flex: 1 }}
                  onPress={() => {
                    update({ visionEnabled: false })
                    onClose()
                  }}
                />
              ) : null}
            </View>
          ) : null}
        </View>
      </View>
    </Modal>
  )
}
