// 对话主屏（M1-3/M1-6/M1-7 装配，Aurora Glass 复刻轮重皮）：
//  - GatewaySession（下行帧→SessionCore.handleFrame，状态→connStatus）
//  - 双形态外壳：窗口短边 ≥600dp 平板双栏（右=玻璃舞台：车况+提醒+焦点卡），旋转即时切
//  - 确认条按台账渲染（isPendingLive），位置征询条只激活最新一条
//  - 视觉照 hmi shell.css：深空渐变+极光 blob 打底，顶栏=品牌光球+连接 pill，空对话=欢迎态大光球
// AR04：配置、会话和语音控制器由 AssistantProvider 持有；本屏只呈现记录与布局。
import { FlashList, type FlashListRef } from '@shopify/flash-list'
import { BlurTargetView } from 'expo-blur'
import { Link, Redirect, router, useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { KeyboardAvoidingView, Pressable, ScrollView, Text, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'

import { isPendingLive } from '@shared/pendingOps.mjs'
import type { Msg } from '@shared/types.ts'

import { precedingUserUtterance } from '../../core/session/actionSummary'
import { followUpChips, MAX_CHIPS } from '../../core/session/followUps'
import { showJumpToLatest, stickToBottom, STICK_TO_BOTTOM_THRESHOLD, timeDividers } from '../../core/session/history'
import { buildReceipt } from '../../core/session/receipt'
import { settingsStore, type FontScalePref } from '../../core/settings/store'
import { composerOrbAnimated, loopsAnimated, orbTempo } from '../../core/presence/orbPolicy'
import { composerInputMode } from '../../core/presence/drivingMode'
import { captureSummary, capsuleVisible } from '../../core/presence/presence'
import { lowPower } from '../../core/power/lowPower'
import { usePowerFacts } from '../../core/power/usePowerFacts'
import { AuroraBackground, AuroraOrb } from '../../ui/aurora'
import { Icon, iconRuntimeAvailable, type IconName } from '../../ui/Icon'
import { PANE_GAP, tabletopSplit } from '../../ui/layout/sizeClass'
import { usePalette } from '../../ui/theme'
import { TARGET, scale } from '../../ui/tokens'
import { StageDrawer } from '../stage/StageDrawer'
import { StagePane } from '../stage/StagePane'
import { Composer } from './Composer'
import { visibleQuickCommands } from '@/core/session/quickCommands'

import { FocusDock, focusDockVisible } from './FocusDock'
import { MessageBubble } from './MessageBubble'
import { PresenceCapsule } from './PresenceCapsule'
import { VoiceSheet } from './VoiceSheet'
import { useAssistant, type AssistantRuntime } from '../assistant/AssistantProvider'
import { useProactiveViewability } from '../assistant/ProactivePresenter'

// v1 的回滚分支（免唤醒状态条 / 通知条 / PTT 提示行 / 连接 pill / 弱网横幅 / 气泡内确认）已整段删除
// ——打磨批 E，裁决 J1：B4 起所有验证只跑 v2 路径，回滚分支是没人验证的代码。
/** FlashList 可见性判据：常量，模块级一份就够（原来是 useRef(...).current，那是渲染期读 ref） */
const VIEWABILITY_CONFIG = { itemVisiblePercentThreshold: 50, minimumViewTime: 300 }

export function ChatScreen() {
  const runtime = useAssistant()
  if (!runtime) return <Redirect href="/onboarding" />
  return <ChatBody runtime={runtime} />
}

/** 顶栏图标入口（hmi .au-icon-btn 同款：fill 底/圆角 12）；svg 原生缺席回退文字。
 *  B5-14（B4 Scanner 出账①）：热区从写死的 40dp 改成 §6 的目标——泊车 48 / 行车 56，跟字号 scale。
 *  40 是 hmi 的桌面尺寸，搬到手上两态都不达 48；行车档只管了层内与 Composer，**没管顶栏**。 */
function TopIconLink({
  p,
  href,
  icon,
  label,
  driving,
  fontScale,
}: {
  p: ReturnType<typeof usePalette>
  href: '/vehicle' | '/settings'
  icon: IconName
  label: string
  driving: boolean
  fontScale: FontScalePref
}) {
  const target = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  if (!iconRuntimeAvailable()) {
    return (
      <Link href={href} style={{ color: p.accent, fontSize: p.font(14), padding: 6 }}>
        {label}
      </Link>
    )
  }
  return (
    <Link href={href} asChild>
      <Pressable
        accessibilityLabel={label}
        style={{
          width: target,
          height: target,
          borderRadius: 12,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: p.fill,
          borderWidth: 1,
          borderColor: p.fill2,
        }}
      >
        <Icon name={icon} size={20} color={p.fg2} />
      </Pressable>
    </Link>
  )
}

/** 欢迎态（hmi ChatView Welcome 同款）：大光球 + 问候 + 快捷指令，替代此前的空白列表。
 *  打磨批 A（评审 P03 / V1）：外层可滚动、键盘弹起时大球 88→56、推荐块保留——原来第三条推荐被 Composer 遮半截。
 *  文案（P29）：主手势是轻点即说（B2 起），长按作次要说明。 */
function Welcome({
  p,
  name,
  hasVoice,
  quickCommands,
  animated = true,
  keyboardVisible = false,
  onSend,
}: {
  p: ReturnType<typeof usePalette>
  name: string
  hasVoice: boolean
  quickCommands: string[]
  /** reduce-motion（B4-3）：欢迎球也是循环动画的一份 */
  animated?: boolean
  /** 事实来自 InteractionScope.keyboardVisible（已有），不加新监听 */
  keyboardVisible?: boolean
  onSend: (text: string) => void
}) {
  return (
    // 键盘下的紧凑档（真机 7525784b6 复核：只缩球不够，第三条推荐仍被 Composer 切半——
    // 键盘上方只剩约 276dp，球 56 + 两行说明 + 两行推荐要 320dp）：去掉次要说明、收窄留白，三条推荐整行可见
    <ScrollView
      testID="welcome-scroll"
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={{ flexGrow: 1, alignItems: 'center', justifyContent: 'center', gap: keyboardVisible ? 6 : 10, padding: keyboardVisible ? 12 : 24 }}
    >
      <AuroraOrb size={keyboardVisible ? 56 : 88} state="idle" animated={animated} />
      <Text style={{ color: p.fg1, fontSize: p.font(keyboardVisible ? 22 : 26), fontWeight: '600', marginTop: keyboardVisible ? 6 : 14 }}>
        我是{name}
      </Text>
      <Text style={{ color: p.fg2, fontSize: p.font(14) }}>
        {hasVoice ? '点一下光球说话，或点指令试试' : '点下方指令试试，或直接输入'}
      </Text>
      {hasVoice && !keyboardVisible ? (
        <Text testID="welcome-secondary" style={{ color: p.fg3, fontSize: p.font(12) }}>也可以按住光球边说边放</Text>
      ) : null}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: keyboardVisible ? 8 : 10, justifyContent: 'center', marginTop: keyboardVisible ? 4 : 12 }}>
        {quickCommands.slice(0, 3).map((q) => (
          <Pressable
            key={q}
            testID="welcome-command"
            accessibilityRole="button"
            accessibilityLabel={`试试：${q}`}
            onPress={() => onSend(q)}
            style={{
              backgroundColor: p.fill,
              borderWidth: 1,
              borderColor: p.fill2,
              borderRadius: 999,
              paddingHorizontal: 18,
              paddingVertical: 10,
            }}
          >
            <Text style={{ color: p.fg1, fontSize: p.font(13) }}>{q}</Text>
          </Pressable>
        ))}
      </View>
    </ScrollView>
  )
}

function ChatBody({ runtime }: { runtime: AssistantRuntime }) {
  const { p, cfg, core, state, settings, ptt, hf, snapshot, layout, motionEnv, reduceMotion,
    turn, latestTurnId, busy, stoppable, sessionSummary,
    onSend, onConfirm, onSlotReply, onIssueAction, onInterrupt, onOrbTap,
    onStopPlayback, setSheetOverride, privacyOpen, setPrivacyOpen, draft, setDraft,
    dockExpanded, setDockExpanded } = runtime
  const { messages, pendingOps, vehState, pendingLocationText, uncertainIds, draftUserId,
    interruptedIds, s2sIds, visionIds, turnMeta, confirmLog, messageAt, resentIds } = state
  const [listHeight, setListHeight] = useState(0)
  const [columnHeight, setColumnHeight] = useState(0)
  // 打磨批 F（评审 P13）：时间分隔（判据 history.ts::timeDividers，5 分钟）与「回到最新」（history.ts::showJumpToLatest）。
  // now 取 snapshot.now：分隔标签只在跨天时变化，不需要自己的秒表
  const dividers = useMemo(() => timeDividers(messages, messageAt, snapshot.now), [messages, messageAt, snapshot.now])
  const listRef = useRef<FlashListRef<Msg>>(null)
  const [offsetFromBottom, setOffsetFromBottom] = useState(0)
  // 同一个离底读数的即时副本：onContentSizeChange 在同一帧里要用「增高之前」的离底距离判贴底，不能等 state
  const offsetRef = useRef(0)
  // 「有新内容」：离底期间记录变长了才出胶囊；回到底部即清
  const [awayCount, setAwayCount] = useState<number | null>(null)
  const jumpVisible = showJumpToLatest(offsetFromBottom, listHeight) && awayCount !== null && messages.length > awayCount
  if (showJumpToLatest(offsetFromBottom, listHeight)) {
    if (awayCount === null) setAwayCount(messages.length)
  } else if (awayCount !== null) setAwayCount(null)
  // 重发（评审 P13 ③）：取紧邻的上一条用户原话，走 onSend（core.send ⇒ 新 request_id），旧气泡留痕「已重发」
  const resendOf = (item: Msg): (() => void) | undefined => {
    if (item.role !== 'user' && !(item.error || uncertainIds.includes(item.id))) return undefined
    if (item.role === 'user') return undefined
    const at = messages.findIndex((m) => m.id === item.id)
    const text = precedingUserUtterance(messages, at)
    if (!text) return undefined
    return () => {
      if (onSend(text) !== false) core.markResent(item.id)
    }
  }
  // 首页推荐按**服务端能力摘要 + 用户开关**筛（AR05 §6.2）：没授权/没在线/用户关掉的
  // 能力不再摆推荐——点了必然被婉拒而用户不知道为什么。摘要不完整或还没查到时不筛，
  // 「此刻查不到」不许显示成「你没有这个功能」。判据在共享 quickCommands.mjs。
  const visibleCommands = useMemo(
    () => visibleQuickCommands(settings.quickCommands, sessionSummary, settings.agents),
    [settings.quickCommands, sessionSummary, settings.agents],
  )

  // B5-8 深链 xiaozhou://voice（Shortcuts「说话」）：升层**不开麦**（§12.2：进入后仍需一次手势才录音）。
  // 一次性消费：同一次进入只升一次，用户收起后不再被参数顶回去。
  // 无消息时 latestTurnId 与 usePresence:98 的判等两边都是 ''，override 照样生效（现读核过）。
  //
  // ⚠ 消费的是**参数本身**，不是在组件实例上记一个「已消费」的 ref。`+native-intent` 现在把
  // 「说话」深链送回**同一个**对话页（不再每点一次新建一个，见 core/nav/deepLink），
  // 实例级 ref 会让第二次点桌面「说话」永远不再升层。清掉参数后，下一次到达重新写入
  // `voice=1` 就又是一次真实的变化，两种进入方式的行为也就一致了。
  const { voice: voiceParam } = useLocalSearchParams<{ voice?: string }>()
  useEffect(() => {
    if (voiceParam !== '1') return
    setSheetOverride({ turnId: latestTurnId, mode: 'open' })
    router.setParams({ voice: undefined })
  }, [voiceParam, latestTurnId, setSheetOverride])

  // B4-7 tabletop（§7.3）：分界 = 铰链上缘（窗口坐标）− 内容区在窗口里的 y。onLayout 给的是相对父级的 y，
  // 这里要的是窗口坐标 ⇒ measureInWindow；量一次不够（旋转 / 展开会变），随 layout 重量
  const contentRef = useRef<View | null>(null)
  const [contentBox, setContentBox] = useState({ y: 0, h: 0 })
  useEffect(() => {
    if (layout.mode !== 'tabletop') return
    contentRef.current?.measureInWindow((_x, y, _w, h) => setContentBox({ y, h }))
  }, [layout.mode, layout.width, layout.height])

  // 开录即告知（红线三条件③在交互时刻的落实）：正在上传原始音频、或这一轮就是端到端发起的
  const s2sNotice = snapshot.privacy.mic === 'cloudAudio' || snapshot.turnSource === 's2s'
  // 位置征询条只激活最新一条（无 operation_id 的 needConfirm 气泡）
  const lastConsentId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i]
      if (m.role === 'assistant' && m.needConfirm && !m.operationId) return m.id
    }
    return null
  }, [messages])
  const confirmActiveOf = (m: Msg): boolean => {
    if (!m.needConfirm) return false
    if (m.operationId) return isPendingLive(pendingOps, m.operationId)
    return pendingLocationText !== null && m.id === lastConsentId
  }

  // v2 健康点：**在线是灰的**——一个持续亮着的绿点会一直占用注意力，而它什么也没说
  const healthColor =
    snapshot.transport === 'online' ? p.fg3 : snapshot.transport === 'reconnecting' ? p.amber : p.red
  // v2 采集点（隐私栏入口旁的第二颗点）：**没在采集就不渲染**——一个常驻的灰点会让
  // 「现在到底在不在采」这件事看不出来，而这正是常开麦最该让用户一眼看见的事（方案 §5.10）。
  // 文案与颜色只取 MIC_LABEL（评审 D4：读屏 label 里那句「本机处理」在 PTT 那一刻是假话）。
  // 文案、色调与支持页浮动采集点同出一份判据（presence.ts::captureSummary，AR04 第十五节）。
  const capture = captureSummary(snapshot.privacy)
  const captureDot = capture ? {
    color: capture.tone === 'amber' ? p.amber : capture.tone === 'camera' ? p.fg1 : p.teal,
    label: capture.text,
  } : null

  // §5.11 真模糊（B3 T9 裁决过）：被糊的背景 = 对话列表，BlurTargetView 包住它；ref 要先挂上再给 VoiceSheet
  // 渲 BlurView（首帧 null 会被 expo-blur 当成「没配」静默回落成 none——blur-spike.tsx 的 ready 模板）。
  // 回落 G1-tint 的情形（§5.11 末句）：减少透明度 / 行车档 / ref 还没挂上 /
  // 低电量 = lowPower(power)（B5-7，expo-battery：省电模式 ∨ 电量 <20%；原生缺席按 null 降级 ⇒ 不回落）
  const power = usePowerFacts()
  // 「ref 挂上了没有」变成一条**真状态**，由 onLayout 置位——渲染期就不必再读 ref.current
  // （react-hooks/refs：并发渲染下渲染期读 ref 可能读到不属于这一帧的值）。
  // 为什么不是 ref 回调：`BlurTargetView` 的 props 把 ref 声明成 `RefObject<View|null>`，
  // 回调形式过不了 tsc；而 onLayout 必定晚于 ref 挂载，语义同样成立。
  const blurTargetRef = useRef<View | null>(null)
  const [blurAttached, setBlurAttached] = useState(false)
  const onBlurTargetLayout = useCallback(() => setBlurAttached(true), [])
  const blurTarget =
    blurAttached && !settings.reduceTransparency && !snapshot.driving && !lowPower(power)
      ? blurTargetRef
      : null

  const splitLandscape = layout.mode === 'driving-landscape'
  const proactiveViewability = useProactiveViewability(core, runtime.scope,
    runtime.facts.route === '/' && !privacyOpen && !dockExpanded && snapshot.input !== 'voice-sheet')
  // B5-15：层覆盖整列时 Composer 整个被盖住 ⇒ 从无障碍树拿掉（真机抓到它与层内大球说明重复）。
  // 触摸侧本来就被层的暗区拦住了，这里补的是读屏那一半。
  const sheetCoversColumn = splitLandscape && snapshot.input === 'voice-sheet'
  const voiceSheetEl = runtime.facts.route === '/' && runtime.scope.canPresent() ? (
    <VoiceSheet
      p={p}
      fontScale={settings.fontScale}
      snapshot={snapshot}
      turn={turn}
      containerHeight={splitLandscape ? columnHeight : listHeight}
      draftUserId={draftUserId}
      interruptedIds={interruptedIds}
      visionIds={visionIds}
      s2sNotice={s2sNotice}
      candidates={core.candidates}
      motion={{ orb: orbTempo(snapshot, motionEnv), loops: loopsAnimated(motionEnv) }}
      driving={snapshot.driving}
      split={splitLandscape}
      blurTarget={blurTarget}
      // 行车档层壳 G0 实色（打磨批 A / P08 / V3，与「安全面 G0」一致）：记录不再透过层与层内文字叠字。
      // 泊车 solid=false，真模糊路径逐字节不变（支持页早已是 solid）
      solid={snapshot.driving}
      stoppable={stoppable}
      onStopPlayback={onStopPlayback}
      onCollapse={() => setSheetOverride({ turnId: latestTurnId, mode: 'dismissed' })}
      onOrbTap={splitLandscape ? onOrbTap : undefined}
      onSend={(t) => onSend(t)}
    />
  ) : null

  // 承诺面（AR03 / 评审 R09 横屏部分）：driving-landscape 下语音层原来挂在**整列**上，
  // 它的 60% 暗区 `absolute inset 0` 连 Dock 一起盖住 ⇒ 确认按钮必须先收层才够得到，
  // 与「Dock 永远不被别的轴覆盖」（presence.ts 头注 / 外部评审 P0-1）直接冲突。
  // 修法是给层一个**有边界的覆盖域**（记录区 + 胶囊 + Composer），Dock 落在覆盖域之外。
  // 非 split 路径逐字节不变：层仍住在记录区容器里、Dock 仍在原位置。
  // 承诺面此刻有没有真的画出来（宿主事实）：胶囊「一屏只出现一份」的判据 capsuleVisible 读它（打磨批 A / P28）
  const dockMounted = runtime.facts.route === '/' && runtime.scope.canCapture()
  const dockShown = dockMounted && focusDockVisible(snapshot, state.issues)
  // Composer 的 chips 行（打磨批 A / P01 / P02）：无消息 ⇒ 空（欢迎态自己有三条推荐）；有消息 ⇒ 最近一条
  // 助手回答的 follow-up + 候选集（判据 followUps.ts，与语音层同一份）；在飞时不给——催人打断自己。
  const composerChips =
    messages.length === 0 || !turn.assistant || turn.assistant.pending || turn.assistant.streaming
      ? []
      : followUpChips(turn.assistant.followUp, core.candidates, snapshot.driving ? 3 : MAX_CHIPS)
  const focusDockEl = dockMounted ? (
    <FocusDock
      p={p}
      fontScale={settings.fontScale}
      snapshot={snapshot}
      onConfirm={onConfirm}
      onSlotReply={onSlotReply}
      issues={state.issues}
      onIssueAction={onIssueAction}
      onCancelTurn={onInterrupt}
      onReenableBargeIn={hf.recycle}
      expanded={dockExpanded}
      onExpandedChange={setDockExpanded}
    />
  ) : null

  const chatColumn = (
    <View style={{ flex: 1 }}>
    {/* 语音层的**覆盖域**：层的暗区 `absolute inset 0` 只到这一层为止（testID 是这条不变量的取证锚，
        两种形态各有一个演员——横屏是本容器，竖屏是里面的记录区容器） */}
    <View
      testID={splitLandscape ? 'voice-sheet-scope' : undefined}
      style={{ flex: 1 }}
      onLayout={(e) => setColumnHeight(Math.round(e.nativeEvent.layout.height))}
    >
      <View
        testID={splitLandscape ? undefined : 'voice-sheet-scope'}
        style={{ flex: 1 }}
        onLayout={(e) => setListHeight(Math.round(e.nativeEvent.layout.height))}
      >
      <BlurTargetView ref={blurTargetRef} onLayout={onBlurTargetLayout} style={{ flex: 1 }}>
      {messages.length === 0 ? (
        <Welcome
          p={p}
          name={settings.assistantName}
          hasVoice={!!cfg.audioUrl}
          quickCommands={visibleCommands}
          animated={loopsAnimated(motionEnv)}
          keyboardVisible={!!runtime.facts.keyboardVisible}
          onSend={onSend}
        />
      ) : (
        <FlashList
          ref={listRef}
          data={messages}
          onViewableItemsChanged={proactiveViewability}
          viewabilityConfig={VIEWABILITY_CONFIG}
          // FlashList v2 聊天范式：自然序 + 从底部起渲 + 新消息自动跟底（阈值与 stickToBottom 共用一个数）
          maintainVisibleContentPosition={{ autoscrollToBottomThreshold: STICK_TO_BOTTOM_THRESHOLD, startRenderingFromBottom: true }}
          onScroll={(e) => {
            const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent
            const off = Math.max(0, Math.round(contentSize.height - layoutMeasurement.height - contentOffset.y))
            offsetRef.current = off
            setOffsetFromBottom(off)
          }}
          // 晚到的布局增高（卡片在文字之后才量出高度）FlashList 不跟：此前贴底的就再贴一次；判据 history.ts::stickToBottom
          onContentSizeChange={() => {
            if (stickToBottom(offsetRef.current, listHeight)) listRef.current?.scrollToEnd({ animated: false })
          }}
          scrollEventThrottle={100}
          extraData={[pendingOps, pendingLocationText, p.dark, settings.fontScale, uncertainIds, draftUserId, interruptedIds, s2sIds, visionIds, turnMeta, confirmLog, reduceMotion, snapshot.driving, dividers, resentIds]}
          keyExtractor={(m) => m.id}
          renderItem={({ item }) => (
            <View style={{ paddingHorizontal: 12 }}>
              {dividers[item.id] ? (
                <Text testID="time-divider" style={{ color: p.fg3, fontSize: p.font(11), textAlign: 'center', paddingVertical: 8 }}>
                  {dividers[item.id]}
                </Text>
              ) : null}
              <MessageBubble
                p={p}
                msg={item}
                loops={loopsAnimated(motionEnv)}
                driving={snapshot.driving}
                confirmActive={confirmActiveOf(item)}
                uncertain={uncertainIds.includes(item.id)}
                draft={item.id === draftUserId}
                interrupted={interruptedIds.includes(item.id)}
                vision={visionIds.includes(item.id)}
                s2s={s2sIds.includes(item.id)}
                receipt={
                  item.role === 'assistant'
                    ? buildReceipt({ messages, assistant: item, turnMeta, confirmLog, vehicleId: String(vehState.vehicle_id ?? '') })
                    : null
                }
                onSend={onSend}
                onResend={resendOf(item)}
                resent={resentIds.includes(item.id)}
              />
            </View>
          )}
          contentContainerStyle={{ paddingVertical: 10 }}
        />
      )}
      </BlurTargetView>
      {/* 回到最新（打磨批 F / 评审 P13 ②）：离底超一屏且有新内容才出；住在记录区容器里 ⇒ 落在 Composer 上方、
          与 Dock 不重叠（Dock 在容器之外）。实色底（压在记录上）。 */}
      {jumpVisible ? (
        <Pressable
          testID="jump-latest"
          accessibilityRole="button"
          accessibilityLabel="回到最新消息"
          onPress={() => listRef.current?.scrollToEnd({ animated: true })}
          style={{
            position: 'absolute',
            bottom: 10,
            alignSelf: 'center',
            minHeight: scale(36, 'target', settings.fontScale),
            paddingHorizontal: 14,
            borderRadius: 999,
            backgroundColor: p.panel,
            borderWidth: 1,
            borderColor: p.glassBdTop,
            boxShadow: p.glassShadow,
            justifyContent: 'center',
          }}
        >
          <Text style={{ color: p.accent, fontSize: p.font(12) }}>↓ 最新</Text>
        </Pressable>
      ) : null}
      {/* 非 driving-landscape：层住在记录区容器里（B4 及以前的形态，逐字节不变） */}
      {splitLandscape ? null : voiceSheetEl}
      </View>
      {/* 承诺面：**永远不被别的轴覆盖**（评审 P0-1——待确认时断网，那条确认照样钉着）。
          driving-landscape 下它挪到层的覆盖域之外（见本列末尾），这里不渲染 */}
      {splitLandscape ? null : focusDockEl}
      {/* 状态胶囊：一次只说一件「此刻」的事。点按默认打开语音层；建议胶囊（§6 触发③）
          点按 = 开行车档——**做什么由 derivePresence 给的 capsule.action 决定，判据不在这里**。
          画不画读 presence.ts::capsuleVisible（打磨批 A / P05 / P28）：层升起时层内已有一份、承诺面钉着时 Dock 已在说 */}
      {capsuleVisible(snapshot, dockShown) ? (
        <PresenceCapsule
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          onPress={() =>
            snapshot.capsule?.action === 'enable-driving'
              ? settingsStore.getState().update({ drivingManual: true })
              : setSheetOverride({ turnId: latestTurnId, mode: 'open' })
          }
        />
      ) : null}
      <Composer
        p={p}
        chips={composerChips}
        draft={draft}
        onDraftChange={setDraft}
        busy={busy}
        stoppable={stoppable}
        ptt={cfg.audioUrl ? ptt : null}
        orbState={snapshot.primary}
        orbDim={snapshot.dim}
        fontScale={settings.fontScale}
        onSend={onSend}
        onInterrupt={onInterrupt}
        onStopPlayback={onStopPlayback}
        // tabletop 下舞台已有一颗 120dp 大球在跑循环 ⇒ Composer 球让位（§11.4「同屏常态 1 个」）。
        // 判据仍是 orbPolicy，这一条例外太小不值得进纯函数（B4 §6.2 记一句）
        orbAnimated={composerOrbAnimated(snapshot, motionEnv) && layout.mode !== 'tabletop'}
        orbDriving={orbTempo(snapshot, motionEnv) === 'slow'}
        driving={snapshot.driving}
        inputMode={composerInputMode(snapshot.identity, snapshot.driving)}
        hideChips={splitLandscape}
        covered={sheetCoversColumn}
        onTap={onOrbTap}
      />
      {/* driving-landscape：层挂在**覆盖域**容器上，absolute 盖住记录区 + 胶囊 + Composer（B5-15 lever ②）。
          被盖住的 Composer 光球不再是唯一麦——层内大球接了 onOrbTap（§5.1.1「轻点始终能说」）。 */}
      {splitLandscape ? voiceSheetEl : null}
    </View>
    {/* AR03：Dock 在覆盖域之外 ⇒ 横屏下确认/取消不被暗区压暗、无需先收层。
        代价一条：横竖旋转时它换了挂载位置会重挂载，AR01 的「另有 N 个待处理」展开态随之收起；
        按稳定 ID 操作那条判据不受影响。 */}
    {splitLandscape ? focusDockEl : null}
    </View>
  )

  return (
    // top 边必须显式包含：真机顶栏会顶进系统状态栏（M1-8 首轮实测，手机态露头的第一个 bug）
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <SafeAreaView style={{ flex: 1 }} edges={['top', 'bottom']}>
        {/* 键盘避让（B1-12，真机读数=遮）：Android 上 `behavior=undefined` 等于什么都不做，
            而 edge-to-edge 下系统的 adjustResize 也没把内容顶上去——实测键盘弹起后输入框
            与发送键**整个被盖住**（`e2e/artifacts/b1-12-chat-kbd.png`，Maestro 08 拿不到
            `composer-send` 是同一件事的第二个读数）。两端都用 `padding`：由 RN 按键盘
            高度补底。**不改 app.config 的 softwareKeyboardLayoutMode**——那是原生配置，
            动它要重建，而 B1 零原生变更。 */}
        <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 10,
              paddingHorizontal: 14,
              paddingVertical: 10,
              borderBottomWidth: 1,
              borderColor: p.line,
            }}
          >
            <AuroraOrb size={30} state={busy ? 'thinking' : 'idle'} animated={busy && loopsAnimated(motionEnv)} />
            <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '600', flexShrink: 1 }} numberOfLines={1}>
              {settings.assistantName}随行
            </Text>
            {/* 连接：只留一个 7dp 健康点——「在线」这两个字在线时是噪声，它只在**不**在线时
                才是信息，而那时状态胶囊已经在说这件事了（方案 §5.1）。 */}
            <Pressable
                testID="health-dot"
                accessibilityRole="button"
                accessibilityLabel={`连接${snapshot.transport === 'online' ? '正常' : snapshot.transport === 'reconnecting' ? '重连中' : '已断开'}${captureDot ? '，' + captureDot.label : ''}；打开隐私栏`}
                onPress={() => setPrivacyOpen(true)}
                style={{
                  // B5-14（B4 Scanner 出账①）：与顶栏两枚钮同一表达式——泊车 48 / 行车 56
                  minWidth: scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', settings.fontScale),
                  height: scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', settings.fontScale),
                  flexDirection: 'row',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 5,
                }}
              >
                <View
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: 4,
                    backgroundColor: healthColor,
                    boxShadow: snapshot.transport === 'online' ? undefined : `0 0 8px ${healthColor}`,
                  }}
                />
                {captureDot ? (
                  <View
                    testID="capture-dot"
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 4,
                      backgroundColor: captureDot.color,
                      boxShadow: `0 0 8px ${captureDot.color}`,
                    }}
                  />
                ) : null}
              </Pressable>
            <View style={{ flex: 1 }} />
            {/* 舞台常驻的两种形态（双栏 / 桌面）里车况已在屏上，不重复给入口；抽屉与单栏保留 */}
            {layout.mode !== 'two-pane' && layout.mode !== 'tabletop' ? (
              <TopIconLink p={p} href="/vehicle" icon="vehicle" label="车辆" driving={snapshot.driving} fontScale={settings.fontScale} />
            ) : null}
            <TopIconLink p={p} href="/settings" icon="settings" label="设置" driving={snapshot.driving} fontScale={settings.fontScale} />
          </View>
          {layout.mode === 'two-pane' ? (
            <View style={{ flex: 1, flexDirection: 'row' }}>
              {/* book：左栏宽 = 铰链左缘 − gap/2，铰链落在 gap 正中（§7.3）；flat 双栏：对话 flex、舞台 stageWidth */}
              <View style={layout.posture === 'book' ? { width: layout.book.chat } : { flex: 1 }}>{chatColumn}</View>
              <View style={{ width: layout.posture === 'book' ? layout.book.gap : PANE_GAP }} />
              <StagePane
                p={p}
                mode="双栏"
                messages={messages}
                vehState={vehState}
                onSend={onSend}
                style={[
                  { marginVertical: 10, marginRight: 10 },
                  layout.posture === 'book' ? { flex: 1 } : { width: layout.stage },
                ]}
              />
            </View>
          ) : layout.mode === 'drawer' ? (
            <View style={{ flex: 1, flexDirection: 'row' }}>
              <View style={{ flex: 1 }}>{chatColumn}</View>
              <StageDrawer p={p} messages={messages} vehState={vehState} onSend={onSend} />
            </View>
          ) : layout.mode === 'tabletop' ? (
            <View ref={contentRef} style={{ flex: 1 }}>
              {/* 上半：舞台 + 大光球（铰链上方）；下半：转写 / 记录 + Composer。分界 = 铰链上缘（§7.3） */}
              <View style={{ height: tabletopSplit(contentBox.h, layout.hinge?.topDp ?? 0, contentBox.y) }}>
                <StagePane
                  p={p}
                  mode="桌面"
                  messages={messages}
                  vehState={vehState}
                  onSend={onSend}
                  orb={{
                    state: snapshot.primary,
                    animated: loopsAnimated(motionEnv),
                    driving: orbTempo(snapshot, motionEnv) === 'slow',
                  }}
                  style={{ flex: 1, marginHorizontal: 10, marginTop: 10 }}
                />
              </View>
              <View style={{ height: 8 }} />
              <View style={{ flex: 1 }}>{chatColumn}</View>
            </View>
          ) : (
            // single / driving-landscape（T11 填）
            chatColumn
          )}
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  )
}
