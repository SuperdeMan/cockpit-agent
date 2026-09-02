// mobile/src/features/chat/VoiceSheet.tsx
// 语音层（方案 §5.2）：PTT / 唤醒词 / S2S 三种说话方式升起的**同一张层**。
// **它不持有任何转写 / 回答状态**——它是「对话记录里当前这一轮」的视图（§5.2 规则 2）：
// 转写读当前用户气泡、回答读当前助手气泡、卡片读 final.ui_card；收起、切后台、折叠展开都不会丢，
// 因为根本没有「等收起再写」这一步。升不升起、升多高由 derivePresence 定（`snapshot.input` /
// `snapshot.sheetDetent`），这里只渲染与转发手势。
// 材质：外壳 G1（Glass）；零新依赖——手势用随 expo-router 在场的 react-native-gesture-handler
// （PackageList.java:73 已注册），高度用 reanimated。
// 性能纪律（方案 §11.4）：层开着时它的 88dp 大球是**唯一**跑循环动画的光球，Composer 主球转静态。
import { BlurView } from 'expo-blur'
import { useEffect, useState, type RefObject } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { Gesture, GestureDetector } from 'react-native-gesture-handler'
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated'

import { edgeGlowActive, type OrbTempo } from '@/core/presence/orbPolicy'
import type { PresenceSnapshot } from '@/core/presence/presence'
import type { CandidateState } from '@/core/session/candidates'
import { followUpChips, MAX_CHIPS } from '@/core/session/followUps'
import type { TurnView } from '@/core/session/turnView'
import type { FontScalePref } from '@/core/settings/store'
import { CardRenderer } from '@/features/cards/CardRenderer'
import { DrivingCardSummary } from '@/features/cards/DrivingCardSummary'

import { FollowUpChips } from './FollowUpChips'
import { AuroraOrb, EdgeGlow, Glass, StreamCursor, ThinkDots } from '@/ui/aurora'
import { GLASS, RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export interface VoiceSheetProps {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  /** 当前这一轮（core/session/turnView.ts::currentTurn 算出来的事实） */
  turn: TurnView
  /** 层可用的高度（包裹列表的那个 View 的 onLayout 高度）；0=还没量到，不渲染 */
  containerHeight: number
  /** 转写草稿气泡 id（`SessionState.draftUserId`）：光标跟着草稿走，不跟着 capture 轴走 */
  draftUserId: string | null
  /** 被打断的助手气泡（方案 §5.2 规则 4）：回答定格 + 灰字「已打断」，不改红 */
  interruptedIds: readonly string[]
  /** 端到端挡位的开录即告知（红线三条件③）：层的第一行 G0 实色条 */
  s2sNotice: boolean
  /** 上一轮 final 记下的候选集（chips 的第二个来源；判据在 core/session/followUps.ts） */
  candidates: CandidateState
  /** 带过视觉抓帧的用户气泡（层里的转写前缀 📷；方案 §5.5，不做预览） */
  visionIds: readonly string[]
  /** 动效策略（判据 core/presence/orbPolicy.ts；B4-3）：大球节律 + 循环类小动效动不动 */
  motion: { orb: OrbTempo; loops: boolean }
  /** 行车档（`snapshot.driving`）：120dp 球、18pt 回答、按钮 56、一屏一卡、chips ≤3（§6） */
  driving: boolean
  /** 横屏车载（`layout.mode === 'driving-landscape'`）：左 40% 球 + 转写 / 右 60% 回答 + 卡 */
  split: boolean
  /** 被糊的背景（B4-8 / §5.11）：非 null ⇒ 真模糊路径（BlurView + 更薄的 tint）；
   *  null ⇒ 回落 G1-tint（减少透明度 / 行车档 / ref 还没挂上）。判据全在 ChatScreen，本组件只消费 */
  blurTarget: RefObject<View | null> | null
  /** 下拉 / 点「收起」/ 点暗区 */
  onCollapse(): void
  /** ■ 打断：T3 只停播报与取消在飞轮；T6 接上「打断后再听」 */
  onInterrupt(): void
  onSend(text: string): void
}

/** 下拉多少算「收起」（dp） */
export const SHEET_DISMISS_DY = 80

/** 层壳底（第 3 批附加项①，**§5.11 G1 的 tint 落地，不是新裁决**）：`Glass` 的 `glassBg` 在暗色下
 *  只有 5.6%（那是**卡壳**用的），语音层套上它之后记录里的气泡会透过层与层内文字重叠、两边都难读
 *  （第 2 批真机 `b2-03-capsule-attention.png`）。这里在 Glass 内垫一层 `p.bg` 同色系实色，
 *  不透明度取 G1 的 `GLASS.frosted.tint`；Glass 自己的白色薄膜与光照边框仍叠在它上面。
 *  方案 §5.2「记录变暗 40%、**仍可见**」保留——身后暗区一字未动。 */
function shellTint(bg: string, alpha: number): string {
  const n = parseInt(bg.replace('#', ''), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}
/** 收起动画时长（ms） */
const COLLAPSE_MS = 180

export function VoiceSheet(props: VoiceSheetProps) {
  const { p, fontScale, snapshot, turn, containerHeight } = props
  const open = snapshot.input === 'voice-sheet' && containerHeight > 0
  const target = Math.round(containerHeight * snapshot.sheetDetent)
  // 挂载态比 open 晚 COLLAPSE_MS 关掉：让收起动画播完再卸载
  const [mounted, setMounted] = useState(open)
  const h = useSharedValue(0)
  useEffect(() => {
    if (open) {
      setMounted(true)
      h.value = withSpring(target, { damping: 18, stiffness: 160 })
      return
    }
    h.value = withTiming(0, { duration: COLLAPSE_MS })
    const t = setTimeout(() => setMounted(false), COLLAPSE_MS)
    return () => clearTimeout(t)
  }, [open, target, h])
  const sheetStyle = useAnimatedStyle(() => ({ height: h.value }))
  // 只认「向下拖」。不加方向约束时这条 Pan 会把层内 chips 的横滑一并吃掉——T9 真机实测：
  // 400ms / 900ms 两次横滑，chips 带逐字节 **0.00%** 变化，而同两帧的层内大球框差 **99.98%**
  // （屏是活的，观测通道开着）⇒ 第二个 chip 永远够不到。只在向下超过 10dp 才激活：横滑
  // 永远够不到这个阈值，手势留给 FollowUpChips 的 ScrollView；收起只需要向下，所以这条
  // 约束不影响 T3 已验的收起路径（500px 下拖远超 10dp）。⚠ 真机复验见 §6.3。
  const pan = Gesture.Pan()
    .runOnJS(true)
    .activeOffsetY(10)
    .onEnd((e) => {
      if (e.translationY > SHEET_DISMISS_DY) props.onCollapse()
    })
  if (!mounted) return null

  const user = turn.user
  const assistant = turn.assistant
  const busy = snapshot.agent !== 'idle'
  const body = scale(TYPE.body, 'text', fontScale)
  const driving = props.driving
  // B4-11 §6「目标 ≥56dp」：层内按钮 / chips 行车 56、泊车 48
  const targetBtn = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const capturing = snapshot.capture === 'listening' || snapshot.capture === 'recognizing'
  // 行车档答后回落（§5.2 规则 3 行车条款）：detent 已回 0.4 且此刻不忙 ⇒ 层里只剩球 + 胶囊。
  // **层不消失**（常驻，§6），消失的是内容。判据在 derivePresence 的 sheetDetent，这里只读结果。
  const terse = driving && snapshot.sheetDetent === 0.4 && snapshot.agent === 'idle' && !capturing
  const capsuleColor =
    snapshot.capsule?.tone === 'red'
      ? p.red
      : snapshot.capsule?.tone === 'amber'
        ? p.amber
        : snapshot.capsule?.tone === 'accent'
          ? p.accent
          : p.fg2
  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      {/* 记录变暗、仍可见（§5.2）：点暗区 = 收起。
          40% → 60% 是第 3 批附加项①授权的升级档：58% 的壳底之后记录仍以未压暗的 ~45% 强度
          透过来（真机同帧两条同类文字带：层外幅度 41.9 / 层内 18.8），层内答案与记录里的
          同一段话叠在一起两边都难读。60% 之后透出降到 ~30%。「仍可见」保留 */}
      <Pressable
        accessibilityLabel="收起语音层"
        onPress={props.onCollapse}
        style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.6)' }}
      />
      <GestureDetector gesture={pan}>
        <Animated.View testID="voice-sheet" style={[{ position: 'absolute', left: 0, right: 0, bottom: 0 }, sheetStyle]}>
          <Glass
            p={p}
            r={RADIUS['2xl']}
            style={{ flex: 1, overflow: 'hidden', borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }}
          >
            {/* 壳底（§5.11 G1 frosted）：真模糊在场 = BlurView + 更薄的 tint；否则 = B2 附加①的 tint（.58）。
                同屏只有这一个 BlurView（§5.11 禁「同屏多个动态 Blur」）——顶栏与舞台压在静态深空底上，糊了没收益 */}
            {props.blurTarget ? (
              <>
                <BlurView
                  pointerEvents="none"
                  blurMethod="dimezisBlurView"
                  blurTarget={props.blurTarget}
                  intensity={60}
                  tint={p.dark ? 'dark' : 'light'}
                  style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}
                />
                <View
                  pointerEvents="none"
                  testID="voice-sheet-shell"
                  style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: shellTint(p.bg, GLASS.frosted.tintOverBlur) }}
                />
              </>
            ) : (
              <View
                pointerEvents="none"
                testID="voice-sheet-shell"
                style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: shellTint(p.bg, GLASS.frosted.tint) }}
              />
            )}
            {/* 顶缘极光（方案 §5.2 规则 6）：只在 listening / thinking */}
            <EdgeGlow active={edgeGlowActive(snapshot)} animated={props.motion.loops} />
            {/* 把手（G2 只给光球与把手，§5.11） */}
            <View style={{ alignSelf: 'center', width: 36, height: 4, borderRadius: 2, backgroundColor: p.fill2, marginTop: 8 }} />
            {props.s2sNotice ? (
              <View
                testID="s2s-notice"
                accessibilityLiveRegion="polite"
                style={{ backgroundColor: p.dark ? '#3B2A0A' : '#FFF4DB', paddingVertical: 6, paddingHorizontal: 12, marginTop: 8 }}
              >
                <Text style={{ color: p.amber, fontSize: scale(TYPE.caption, 'text', fontScale), textAlign: 'center' }}>
                  端到端语音 · 原始音频将在本轮上传
                </Text>
              </View>
            ) : null}
            <ScrollView
              contentContainerStyle={
                props.split
                  ? { padding: 16, gap: 16, flexDirection: 'row', alignItems: 'flex-start' }
                  : { padding: 16, gap: 12, alignItems: 'center' }
              }
              keyboardShouldPersistTaps="handled"
            >
              {/* 横屏车载 split（§6「横屏 40:60」）：左 40% 球 + 转写 + 胶囊 / 右 60% 回答 + chips + 卡。
                  **非 split 时这两个容器只是透明分组**（同样 gap 12 + 居中 + 撑满宽），逐项排版不变。 */}
              <View
                style={
                  props.split
                    ? { width: '40%', gap: 12, alignItems: 'center' }
                    : { alignSelf: 'stretch', gap: 12, alignItems: 'center' }
                }
              >
                {/* 转写区：大字 20pt。T4 起它是草稿气泡（增量沉淀），定稿后仍是同一条。
                    行车档回落后（terse）只剩球 + 胶囊，转写也收掉 */}
                {!terse && user ? (
                  <Text
                    testID="voice-sheet-transcript"
                    accessibilityLiveRegion="polite"
                    style={{
                      color: p.fg1,
                      fontSize: scale(20, 'text', fontScale),
                      lineHeight: scale(28, 'line', fontScale),
                      textAlign: 'center',
                    }}
                  >
                    {user && props.visionIds.includes(user.id) ? '📷 ' : ''}
                    {user.text}
                    {user.id === props.draftUserId ? <StreamCursor h={scale(20, 'text', fontScale)} animated={props.motion.loops} /> : null}
                  </Text>
                ) : null}
                {/* 大光球：snapshot.primary 驱动（listening→thinking→speaking→followup）；十条不变量内。
                    行车档 120dp（§6），泊车 88 */}
                <AuroraOrb size={driving ? 120 : 88} state={snapshot.primary} dim={snapshot.dim} animated={props.motion.orb !== 'static'} driving={props.motion.orb === 'slow'} />
                {/* 胶囊文案（同 §4.3，此处放大） */}
                {snapshot.capsule ? (
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    {snapshot.capsule.live ? (
                      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: p.accent, boxShadow: `0 0 10px ${p.accent}` }} />
                    ) : null}
                    <Text style={{ color: capsuleColor, fontSize: body }}>{snapshot.capsule.text}</Text>
                  </View>
                ) : null}
              </View>
              {terse ? null : (
                <View
                  style={
                    props.split ? { flex: 1, gap: 12 } : { alignSelf: 'stretch', gap: 12, alignItems: 'center' }
                  }
                >
                  {/* 回答区：speech_delta 逐字 + StreamCursor；pending 时 ThinkDots。行车档 18pt（§6） */}
                  {assistant?.pending ? <ThinkDots color={p.accent} animated={props.motion.loops} /> : null}
                  {assistant?.text ? (
                    <Text
                      testID="voice-sheet-answer"
                      accessibilityLiveRegion="polite"
                      style={{
                        color: assistant.error ? p.red : p.fg1,
                        fontSize: scale(driving ? TYPE.h2 : TYPE.body + 1, 'text', fontScale),
                        lineHeight: scale(driving ? 28 : 24, 'line', fontScale),
                        alignSelf: 'stretch',
                      }}
                    >
                      {assistant.text}
                      {assistant.streaming ? <StreamCursor h={scale(driving ? TYPE.h2 : TYPE.body + 1, 'text', fontScale)} animated={props.motion.loops} /> : null}
                    </Text>
                  ) : null}
                  {assistant && props.interruptedIds.includes(assistant.id) ? (
                    <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fontScale) }}>已打断</Text>
                  ) : null}
                  {/* follow-up chips（方案 §5.2 图）：答完了才给——流式/思考中给等于催人打断自己。
                      行车档 ≤3 条、行高 56（§6） */}
                  {assistant && !assistant.streaming && !assistant.pending ? (
                    <FollowUpChips
                      p={p}
                      fontScale={fontScale}
                      target={driving ? TARGET.driving : TARGET.parked}
                      chips={followUpChips(assistant.followUp, props.candidates, driving ? 3 : MAX_CHIPS)}
                      onSend={props.onSend}
                    />
                  ) : null}
                  {/* 卡片：泊车走注册表全量渲（card_group 的主卡/折叠由 CardRenderer 判，这里不判）；
                      行车档走压缩卡「标题 + ≤2 字段 + 1 主按钮」（§6 一屏一卡）——**不改 34 个渲染器** */}
                  {assistant?.uiCard ? (
                    <View style={{ alignSelf: 'stretch' }}>
                      {driving ? (
                        <DrivingCardSummary p={p} fontScale={fontScale} card={assistant.uiCard} onSend={props.onSend} />
                      ) : (
                        <CardRenderer p={p} card={assistant.uiCard} onSend={props.onSend} />
                      )}
                    </View>
                  ) : null}
                </View>
              )}
            </ScrollView>
            <View
              style={{
                flexDirection: 'row',
                justifyContent: 'center',
                gap: 24,
                paddingVertical: 8,
                borderTopWidth: 1,
                borderColor: p.line,
              }}
            >
              <Pressable
                testID="voice-sheet-collapse"
                accessibilityRole="button"
                onPress={props.onCollapse}
                style={{ minHeight: targetBtn, minWidth: 96, justifyContent: 'center', alignItems: 'center' }}
              >
                <Text style={{ color: p.fg2, fontSize: body }}>⌄ 收起</Text>
              </Pressable>
              {busy ? (
                <Pressable
                  testID="voice-sheet-interrupt"
                  accessibilityRole="button"
                  onPress={props.onInterrupt}
                  style={{ minHeight: targetBtn, minWidth: 96, justifyContent: 'center', alignItems: 'center' }}
                >
                  <Text style={{ color: p.amber, fontSize: body }}>■ 打断</Text>
                </Pressable>
              ) : null}
            </View>
          </Glass>
        </Animated.View>
      </GestureDetector>
    </View>
  )
}
