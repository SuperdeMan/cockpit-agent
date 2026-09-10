// AR04 跨页宿主的三个视图（形态修正 2026-09-09，实施记录第十五节）：
//  · AssistantFrame：非对话页把键盘避让统一做在根；
//  · CrossPageVoiceLayer：语音层的覆盖域 + 支持页的浮动在场（零布局占用）；
//  · AssistantSurface：占真实布局空间的宿主——提醒出口 + 支持页承诺面，没内容时连安全区都不留。
// 「支持页底部常驻两栏」（状态行 + 按钮行）已撤：闲置时它没有任何「此刻」的事实，与胶囊
// 「一次只说一件事」、采集点「没在采集就不渲染」两条既有判据相悖；助手的身份锚是光球，不是文字方块。
import { useState } from 'react'
import { KeyboardAvoidingView, Pressable, Text, View } from 'react-native'
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context'

import { captureSummary, capsuleVisible } from '@/core/presence/presence'
import { loopsAnimated, orbTempo, presenceOrbTempo } from '@/core/presence/orbPolicy'
import { settingsStore } from '@/core/settings/store'
import { FocusDock, focusDockVisible } from '@/features/chat/FocusDock'
import { PresenceCapsule } from '@/features/chat/PresenceCapsule'
import { VoiceSheet } from '@/features/chat/VoiceSheet'
import { AuroraOrb } from '@/ui/aurora'
import { ORB_A11Y } from '@/ui/aurora/AuroraOrb'
import { Icon, iconRuntimeAvailable } from '@/ui/Icon'
import { useBottomChrome } from '@/ui/layout/bottomChrome'
import { RADIUS, TARGET, scale } from '@/ui/tokens'
import { useAssistant, type AssistantRuntime } from './AssistantProvider'
import { ProactivePresenter, pickProactiveMessage } from './ProactivePresenter'

/** Chat 自己避让键盘；其他页统一在根避让，浮动在场与承诺面都留在键盘上方。 */
export function AssistantFrame({ children }: { children: React.ReactNode }) {
  const runtime = useAssistant()
  return <KeyboardAvoidingView style={{ flex: 1, backgroundColor: runtime?.p.bg }}
    behavior={runtime && runtime.facts.route !== '/' ? 'padding' : undefined}>{children}</KeyboardAvoidingView>
}

/** 根布局有界的覆盖域；跨页层始终量高，切横屏时不用等待新挂上的 onLayout。
 *  浮动在场排在层之前：层升起时它自己不渲染（层内大球 / 胶囊 / 停止键接管），层的暗区也没有东西可压。 */
export function CrossPageVoiceLayer({ children }: { children: React.ReactNode }) {
  const runtime = useAssistant()
  const [height, setHeight] = useState(0)
  const support = runtime && runtime.facts.route !== '/'
  const crossPage = support && runtime.scope.canPresent()
  const presence = support && runtime.scope.canCapture()
  return <View testID="app-route-scope" style={{ flex: 1 }} onLayout={(e) => setHeight(Math.round(e.nativeEvent.layout.height))}>
    {children}
    {presence ? <AssistantPresence runtime={runtime} /> : null}
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

/** 支持页的浮动在场：闲置只有一颗光球（右下角，零布局占用），有事时向左长出采集点 / 胶囊 / 动作键。
 *  判据全部复用对话页那一份——光球态 `snapshot.primary`、胶囊 `snapshot.capsule`、停播 `stoppable`、
 *  采集 `captureSummary`——这里只决定「摆在哪、此刻挂哪些」。压在地图瓦片上 ⇒ 一律实色底（map.tsx 既有判据）。 */
function AssistantPresence({ runtime }: { runtime: AssistantRuntime }) {
  const insets = useSafeAreaInsets()
  const chrome = useBottomChrome(runtime.facts.route)
  const { p, snapshot, settings, facts, cfg, stoppable, busy, motionEnv } = runtime
  const capture = captureSummary(snapshot.privacy)
  const action = stoppable ? 'stop-playback' : busy ? 'interrupt' : null
  // 胶囊画不画只读 presence.ts::capsuleVisible（打磨批 A，与对话页同一份）：承诺面在场时它不再重复「等你确认」。
  // 承诺面此刻有没有真的画出来是 AssistantSurface 的事实，判据同它那一行（focusDockVisible）。
  const dockShown = runtime.scope.canCapture() && focusDockVisible(snapshot, runtime.state.issues)
  const capsule = capsuleVisible(snapshot, dockShown)
  const live = !!capture || !!action || capsule
  // 整组浮动在场在层升起时让位——层内大球 / 胶囊 / 停止键接管全部三样，不只是胶囊（assistantPresence.test 锁）；
  // 键盘弹出时闲置的光球让位给输入，有事（声音 / 采集 / 在飞）仍留出口。
  if (snapshot.input === 'voice-sheet') return null
  if (facts.keyboardVisible && !live) return null
  const target = scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', settings.fontScale)
  // 闲置静帧（judgement 在 orbPolicy）：FAB 不是主角，也别让每个支持页都常驻一份循环动画
  const tempo = presenceOrbTempo(snapshot, motionEnv)
  const captureColor = capture?.tone === 'amber' ? p.amber : capture?.tone === 'camera' ? p.fg1 : p.teal
  const disc = { backgroundColor: p.panel, borderWidth: 1, borderColor: p.glassBdTop, boxShadow: p.glassShadow } as const
  return <View testID="assistant-presence" pointerEvents="box-none"
    style={{ position: 'absolute', left: 12, right: 12, bottom: 12 + insets.bottom + chrome, flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 8 }}>
    {capture ? <Pressable testID="assistant-capture-dot" accessibilityRole="button" accessibilityLabel={`${capture.text}；打开隐私栏`}
      onPress={() => runtime.setPrivacyOpen(true)} style={{ width: target, height: target, alignItems: 'center', justifyContent: 'center' }}>
      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: captureColor, boxShadow: `0 0 8px ${captureColor}` }} />
    </Pressable> : null}
    {capsule ? <View style={{ flexShrink: 1 }}>
      <PresenceCapsule p={p} fontScale={settings.fontScale} snapshot={snapshot} solid
        onPress={() => snapshot.capsule?.action === 'enable-driving'
          ? settingsStore.getState().update({ drivingManual: true })
          : runtime.setSheetOverride({ turnId: runtime.latestTurnId, mode: 'open' })} />
    </View> : null}
    {action ? <Pressable testID="assistant-action" accessibilityRole="button"
      accessibilityLabel={action === 'stop-playback' ? '停止播报' : '打断'}
      accessibilityHint={action === 'stop-playback' ? '只停止声音，不会开始录音' : undefined}
      onPress={action === 'stop-playback' ? runtime.onStopPlayback : runtime.onInterrupt}
      style={{ ...disc, borderColor: 'rgba(245,158,11,0.38)', minHeight: target, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 14, borderRadius: RADIUS.full }}>
      {iconRuntimeAvailable() ? <Icon name="stop" size={16} color={p.amber} /> : null}
      <Text style={{ color: p.amber, fontSize: p.font(13), fontWeight: '600' }}>{action === 'stop-playback' ? '停止播报' : '打断'}</Text>
    </Pressable> : null}
    {cfg.audioUrl ? <Pressable testID="assistant-orb" accessibilityRole="button"
      accessibilityLabel={`${ORB_A11Y[snapshot.primary]}，开始说话`} accessibilityHint="轻点开始说话，说完自动发送"
      onPress={runtime.onOrbTap}
      style={{ ...disc, width: target, height: target, borderRadius: RADIUS.full, alignItems: 'center', justifyContent: 'center' }}>
      <AuroraOrb size={target - 8} state={snapshot.primary} dim={snapshot.dim} animated={tempo !== 'static'} driving={tempo === 'slow'} />
    </Pressable> : null}
  </View>
}

/** 占真实布局空间的根宿主：提醒出口（所有支持路由）+ 承诺面（支持页；对话页的 Dock 住在 ChatBody 里）。
 *  两者都没有内容时整个不渲染——留一条空的底部安全区就又是一条常驻空条。 */
export function AssistantSurface() {
  const runtime = useAssistant()
  if (!runtime) return null
  const { p, snapshot, settings, facts } = runtime
  const reminder = !!pickProactiveMessage(runtime)
  const dock = facts.route !== '/' && runtime.scope.canCapture()
    && focusDockVisible(snapshot, runtime.state.issues)
  if (!reminder && !dock) return null
  return <SafeAreaView testID="assistant-surface" edges={['bottom']} style={{ backgroundColor: p.bg, paddingTop: 6 }}>
    <ProactivePresenter />
    {dock ? <FocusDock p={p} fontScale={settings.fontScale} snapshot={snapshot} onConfirm={runtime.onConfirm}
      onSlotReply={runtime.onSlotReply} issues={runtime.state.issues} onIssueAction={runtime.onIssueAction}
      onCancelTurn={runtime.onInterrupt} onReenableBargeIn={runtime.hf.recycle}
      expanded={runtime.dockExpanded} onExpandedChange={runtime.setDockExpanded} /> : null}
  </SafeAreaView>
}
