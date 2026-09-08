import { useState } from 'react'
import { KeyboardAvoidingView, Pressable, Text, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'

import { MIC_LABEL } from '@/core/presence/presence'
import { loopsAnimated, orbTempo } from '@/core/presence/orbPolicy'
import { VoiceSheet } from '@/features/chat/VoiceSheet'
import { FocusDock } from '@/features/chat/FocusDock'
import { TARGET, scale } from '@/ui/tokens'
import { useAssistant } from './AssistantProvider'

/** Chat 自己避让键盘；其他页统一把跨页操作栏留在键盘上方。 */
export function AssistantFrame({ children }: { children: React.ReactNode }) {
  const runtime = useAssistant()
  return <KeyboardAvoidingView style={{ flex: 1, backgroundColor: runtime?.p.bg }}
    behavior={runtime && runtime.facts.route !== '/' ? 'padding' : undefined}>{children}</KeyboardAvoidingView>
}

/** 根布局有界的覆盖域；跨页层始终量高，切横屏时不用等待新挂上的 onLayout。 */
export function CrossPageVoiceLayer({ children }: { children: React.ReactNode }) {
  const runtime = useAssistant()
  const [height, setHeight] = useState(0)
  const crossPage = runtime && runtime.facts.route !== '/' && runtime.scope.canPresent()
  return <View testID="app-route-scope" style={{ flex: 1 }} onLayout={(e) => setHeight(Math.round(e.nativeEvent.layout.height))}>
    {children}
    {crossPage ? <VoiceSheet p={runtime.p} fontScale={runtime.settings.fontScale} snapshot={runtime.snapshot}
      turn={runtime.turn} containerHeight={height} draftUserId={runtime.state.draftUserId}
      interruptedIds={runtime.state.interruptedIds} visionIds={runtime.state.visionIds}
      s2sNotice={runtime.snapshot.privacy.mic === 'cloudAudio' || runtime.snapshot.turnSource === 's2s'}
      candidates={runtime.core.candidates} motion={{ orb: orbTempo(runtime.snapshot, runtime.motionEnv), loops: loopsAnimated(runtime.motionEnv) }}
      driving={runtime.snapshot.driving} split={runtime.layout.mode === 'driving-landscape'} blurTarget={null} solid
      stoppable={runtime.stoppable} onStopPlayback={runtime.onStopPlayback} onOrbTap={runtime.onOrbTap}
      onCollapse={() => runtime.setSheetOverride({ turnId: runtime.latestTurnId, mode: 'dismissed' })} onSend={runtime.onSend} /> : null}
  </View>
}

/** 应用内跨页操作栏占真实布局空间，地图/设置内容不会被覆盖。 */
export function AssistantSurface() {
  const runtime = useAssistant()
  if (!runtime || !runtime.scope.canCapture()) return null
  const { p, snapshot, settings, state, facts } = runtime
  const crossPage = facts.route !== '/'
  const { mic, micActive, camera, visionUploading } = snapshot.privacy
  const captureText = [micActive ? '麦克风开启' : '', mic !== 'off' ? MIC_LABEL[mic].short : '',
    camera === 'singleFrame' ? '摄像头开启' : '', visionUploading ? '画面上传中' : ''].filter(Boolean).join(' · ')
  // 对话已有完整 Composer；全局条只在采集时补直达的物理停止出口（v1 也适用）。
  if (!crossPage && !captureText) return null
  const target = scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', settings.fontScale)
  const button = (id: string, label: string, onPress: () => void) => <Pressable key={id} testID={id}
    accessibilityRole="button" accessibilityLabel={label} onPress={onPress}
    style={{ minHeight: target, minWidth: target, paddingHorizontal: 10, justifyContent: 'center', alignItems: 'center', borderRadius: 12, backgroundColor: p.fill }}>
    <Text style={{ color: p.accent, fontSize: p.font(13) }}>{label}</Text>
  </Pressable>
  return <SafeAreaView testID="assistant-surface" edges={crossPage ? ['bottom'] : []} style={{ backgroundColor: p.bg, borderTopWidth: 1, borderColor: p.line }}>
    {crossPage ? <FocusDock p={p} fontScale={settings.fontScale} snapshot={snapshot} onConfirm={runtime.onConfirm}
      onCancelTurn={runtime.onInterrupt} onReenableBargeIn={runtime.hf.recycle}
      expanded={runtime.dockExpanded} onExpandedChange={runtime.setDockExpanded} /> : null}
    <View style={{ paddingHorizontal: 12, paddingVertical: 4, gap: 6 }}>
      <Pressable testID="assistant-capture-status" accessibilityRole="button" onPress={() => runtime.setPrivacyOpen(true)}
        style={{ minHeight: target, justifyContent: 'center' }}>
        <Text style={{ color: captureText ? p.amber : p.fg2, fontSize: p.font(12) }}>
          {captureText || `${settings.assistantName}在这里 · 麦克风关闭`}
        </Text>
      </Pressable>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
        {crossPage ? button('assistant-open', '展开回答', () => runtime.setSheetOverride({ turnId: runtime.latestTurnId, mode: 'open' })) : null}
        {crossPage ? button('assistant-talk', runtime.ptt.state === 'recording' || runtime.hf.fsm === 'LISTENING' ? '结束收音' : '说话', runtime.onOrbTap) : null}
        {runtime.stoppable ? button('assistant-stop-playback', '停止播报', runtime.onStopPlayback) : null}
        {micActive || mic !== 'off' || runtime.ptt.state !== 'idle' ? button('assistant-stop-mic', '停止收音', runtime.stopMic) : null}
        {crossPage && state.messages.some((m) => m.pending || m.streaming || m.processActive) ? button('assistant-cancel-request', '取消请求', runtime.onInterrupt) : null}
      </View>
    </View>
  </SafeAreaView>
}
