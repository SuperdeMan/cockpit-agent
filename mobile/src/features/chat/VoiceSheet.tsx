// mobile/src/features/chat/VoiceSheet.tsx
// 语音层（方案 §5.2）：PTT / 唤醒词 / S2S 三种说话方式升起的**同一张层**。
// **它不持有任何转写 / 回答状态**——它是「对话记录里当前这一轮」的视图（§5.2 规则 2）：
// 转写读当前用户气泡、回答读当前助手气泡、卡片读 final.ui_card；收起、切后台、折叠展开都不会丢，
// 因为根本没有「等收起再写」这一步。升不升起、升多高由 derivePresence 定（`snapshot.input` /
// `snapshot.sheetDetent`），这里只渲染与转发手势。
// 材质：外壳 G1（Glass）；零新依赖——手势用随 expo-router 在场的 react-native-gesture-handler
// （PackageList.java:73 已注册），高度用 reanimated。
// 性能纪律（方案 §11.4）：层开着时它的 88dp 大球是**唯一**跑循环动画的光球，Composer 主球转静态。
import { useEffect, useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { Gesture, GestureDetector } from 'react-native-gesture-handler'
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated'

import type { PresenceSnapshot } from '@/core/presence/presence'
import type { TurnView } from '@/core/session/turnView'
import type { FontScalePref } from '@/core/settings/store'
import { CardRenderer } from '@/features/cards/CardRenderer'
import { AuroraOrb, Glass, StreamCursor, ThinkDots } from '@/ui/aurora'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
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
  /** 下拉 / 点「收起」/ 点暗区 */
  onCollapse(): void
  /** ■ 打断：T3 只停播报与取消在飞轮；T6 接上「打断后再听」 */
  onInterrupt(): void
  onSend(text: string): void
}

/** 下拉多少算「收起」（dp） */
export const SHEET_DISMISS_DY = 80
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
  const pan = Gesture.Pan()
    .runOnJS(true)
    .onEnd((e) => {
      if (e.translationY > SHEET_DISMISS_DY) props.onCollapse()
    })
  if (!mounted) return null

  const user = turn.user
  const assistant = turn.assistant
  const busy = snapshot.agent !== 'idle'
  const body = scale(TYPE.body, 'text', fontScale)
  const target48 = scale(TARGET.parked, 'target', fontScale)
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
      {/* 记录变暗 40%、仍可见（§5.2）：点暗区 = 收起 */}
      <Pressable
        accessibilityLabel="收起语音层"
        onPress={props.onCollapse}
        style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.4)' }}
      />
      <GestureDetector gesture={pan}>
        <Animated.View testID="voice-sheet" style={[{ position: 'absolute', left: 0, right: 0, bottom: 0 }, sheetStyle]}>
          <Glass
            p={p}
            r={RADIUS['2xl']}
            style={{ flex: 1, overflow: 'hidden', borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }}
          >
            {/* 把手（G2 只给光球与把手，§5.11） */}
            <View style={{ alignSelf: 'center', width: 36, height: 4, borderRadius: 2, backgroundColor: p.fill2, marginTop: 8 }} />
            <ScrollView contentContainerStyle={{ padding: 16, gap: 12, alignItems: 'center' }} keyboardShouldPersistTaps="handled">
              {/* 转写区：大字 20pt。T4 起它是草稿气泡（增量沉淀），定稿后仍是同一条 */}
              {user ? (
                <Text
                  testID="voice-sheet-transcript"
                  style={{
                    color: p.fg1,
                    fontSize: scale(20, 'text', fontScale),
                    lineHeight: scale(28, 'line', fontScale),
                    textAlign: 'center',
                  }}
                >
                  {user.text}
                  {user.id === props.draftUserId ? <StreamCursor h={scale(20, 'text', fontScale)} /> : null}
                </Text>
              ) : null}
              {/* 大光球：snapshot.primary 驱动（listening→thinking→speaking→followup）；十条不变量内 */}
              <AuroraOrb size={88} state={snapshot.primary} dim={snapshot.dim} animated />
              {/* 胶囊文案（同 §4.3，此处放大） */}
              {snapshot.capsule ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  {snapshot.capsule.live ? (
                    <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: p.accent, boxShadow: `0 0 10px ${p.accent}` }} />
                  ) : null}
                  <Text style={{ color: capsuleColor, fontSize: body }}>{snapshot.capsule.text}</Text>
                </View>
              ) : null}
              {/* 回答区：speech_delta 逐字 + StreamCursor；pending 时 ThinkDots */}
              {assistant?.pending ? <ThinkDots color={p.accent} /> : null}
              {assistant?.text ? (
                <Text
                  testID="voice-sheet-answer"
                  style={{
                    color: assistant.error ? p.red : p.fg1,
                    fontSize: scale(TYPE.body + 1, 'text', fontScale),
                    lineHeight: scale(24, 'line', fontScale),
                    alignSelf: 'stretch',
                  }}
                >
                  {assistant.text}
                  {assistant.streaming ? <StreamCursor h={scale(TYPE.body + 1, 'text', fontScale)} /> : null}
                </Text>
              ) : null}
              {assistant && props.interruptedIds.includes(assistant.id) ? (
                <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fontScale) }}>已打断</Text>
              ) : null}
              {/* 卡片：card_group 的主卡/折叠由 CardRenderer 的注册表决定（T8），这里不判 */}
              {assistant?.uiCard ? (
                <View style={{ alignSelf: 'stretch' }}>
                  <CardRenderer p={p} card={assistant.uiCard} onSend={props.onSend} />
                </View>
              ) : null}
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
                style={{ minHeight: target48, minWidth: 96, justifyContent: 'center', alignItems: 'center' }}
              >
                <Text style={{ color: p.fg2, fontSize: body }}>⌄ 收起</Text>
              </Pressable>
              {busy ? (
                <Pressable
                  testID="voice-sheet-interrupt"
                  accessibilityRole="button"
                  onPress={props.onInterrupt}
                  style={{ minHeight: target48, minWidth: 96, justifyContent: 'center', alignItems: 'center' }}
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
