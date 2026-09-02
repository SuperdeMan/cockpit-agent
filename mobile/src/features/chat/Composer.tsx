// 输入区（M1-3 + M2-2 PTT，Aurora Glass 复刻轮重皮）+ B2 T6 手势契约（方案 §5.1.1）：
//  · 轻点光球：开始录音（与免唤醒开关无关）/ 录音中 = 结束并提交 / 播报中 = 先停 TTS 再录 / 思考中 = 展开语音层
//  · 长按光球 / 空输入框 ≥300ms：按住说话；**按住时上滑 ≥60dp 取消**（微信惯例，Q13）；松手发送
//  · 长按识别前允许移动 12dp（列表纵向滚动与 chips 横滑不得误触）
//  · 输入框有字时长按走原生选择，**只有光球仍可 PTT**
//  · TalkBack：轻点切换开始 / 停止，label 随状态；热区 56dp（≥48）
// 手势用 react-native-gesture-handler（PackageList.java:73 已注册，零新依赖）。
// 「轻点到底做什么」的判据不在这里——Composer 只报告手势，ChatScreen 的 onTap 决定走免唤醒的
// 手动唤醒还是 PTT 的 tap 会话（哪个引擎持有麦是 ChatScreen 知道的事实）。
import { useRef, useState } from 'react'
import { ScrollView, Text, TextInput, View, Pressable } from 'react-native'
import { Gesture, GestureDetector } from 'react-native-gesture-handler'

import type { FontScalePref } from '../../core/settings/store'
import { AuroraOrb, type OrbState } from '../../ui/aurora'
import { Icon, iconRuntimeAvailable } from '../../ui/Icon'
import { ORB_A11Y } from '../../ui/aurora/AuroraOrb'
import { AURORA, type Palette } from '../../ui/theme'
import { RADIUS, TARGET, scale } from '../../ui/tokens'
import type { PttHandle } from './usePtt'

/** 长按判定（ms）：方案 §5.1.1 的 ≥300；usePtt 的 MIN_DURATION_MS=320 是「录了多久」，是另一件事 */
export const HOLD_MS = 300
/** 长按识别前允许的移动（dp）：超过就交给滚动 */
export const HOLD_MAX_DISTANCE = 12
/** 按住时上滑多少算取消（dp） */
export const CANCEL_DY = 60

export interface ComposerProps {
  p: Palette
  quickCommands: string[]
  /** 有在飞轮（pending/streaming/process 任一）→ 显示打断 */
  busy: boolean
  /** 语音输入把手；null=服务器未配置（没有 audioUrl 就没有语音） */
  ptt: PttHandle | null
  /** 光球主态由调用方给（v2=snapshot.primary，v1=ChatScreen 里的旧推导）——
   *  Composer 自己不再推导：同一个「此刻是什么态」的判据抄两份就会给出两个答案 */
  orbState: OrbState
  /** reconnecting 期 ×0.6 亮度（`snapshot.dim`） */
  orbDim?: boolean
  /** 语音层开着时主球转静态（同屏循环动画常态 1 个，方案 §11.4） */
  orbAnimated?: boolean
  /** 行车档：光球 ×0.5 频率 ×0.6 透明度（判据 orbPolicy.orbTempo，B4-3） */
  orbDriving?: boolean
  fontScale: FontScalePref
  onSend(text: string): void
  onInterrupt(): void
  /** 轻点光球（判据在 ChatScreen） */
  onTap(): void
}

export function Composer({ p, quickCommands, busy, ptt, orbState, orbDim, orbAnimated, orbDriving, fontScale, onSend, onInterrupt, onTap }: ComposerProps) {
  const [input, setInput] = useState('')
  const heldRef = useRef(false)
  const cancelledRef = useRef(false)
  const submit = () => {
    const text = input.trim()
    if (!text) return
    onSend(text)
    setInput('')
  }
  const recording = ptt?.state === 'recording'
  const finalizing = ptt?.state === 'finalizing'

  // 按住 = Pan.activateAfterLongPress：激活即按下；onUpdate 看上滑；结束即松手（取消过就不发）
  const makeHold = (enabled: boolean) =>
    Gesture.Pan()
      .runOnJS(true)
      .enabled(enabled)
      .maxPointers(1)
      .minDistance(HOLD_MAX_DISTANCE)
      .activateAfterLongPress(HOLD_MS)
      .onStart(() => {
        heldRef.current = true
        cancelledRef.current = false
        ptt?.pressDown()
      })
      .onUpdate((e) => {
        if (heldRef.current && !cancelledRef.current && e.translationY < -CANCEL_DY) {
          cancelledRef.current = true
          ptt?.cancel()
        }
      })
      .onFinalize(() => {
        if (heldRef.current && !cancelledRef.current) ptt?.pressUp()
        heldRef.current = false
      })
  const tap = Gesture.Tap()
    .runOnJS(true)
    .maxDuration(HOLD_MS - 20)
    .onEnd(() => onTap())
  const orbGesture = Gesture.Exclusive(makeHold(!!ptt && !finalizing), tap)
  // 空输入框的「背板即录音键」（B3-3 / B2 出账 plateGesture）：不再把手势挂在包 TextInput 的
  // 父 View 上——Android 的 TextInput 自己消费触摸（长按=光标/选择），RNGH 抢不到（B2 真机
  // 实录 + 本轮自证：长按 4s 出的是光标水滴柄，层不升）。A-spike 三个竞争配置全败：
  // blocksExternalGesture(Native ref) 与 Exclusive(hold, Gesture.Native()) 真机均不起作用，
  // disallowInterruption 在 RNGH 2.32 的 PanGesture 上根本不存在（tsc TS2339）。
  // ⇒ 改为空输入框时铺一层透明触摸层，把「与原生 TextInput 抢触摸」整个绕开：
  // 轻点→聚焦弹键盘（原生 focus 语义由我们转发），长按→PTT（与光球同款、已验证可用的形态）。
  // 有字时该层卸载，原生长按选择完整回归。a11y：触摸层不进无障碍树（TalkBack 的双击激活走
  // a11y action 直达 TextInput，不经普通触摸；长按录音对 TalkBack 本来就不是唯一入口，§8.1）。
  const inputRef = useRef<TextInput>(null)
  const plateTap = Gesture.Tap()
    .runOnJS(true)
    .maxDuration(HOLD_MS - 20)
    .onEnd(() => inputRef.current?.focus())
  const plateGesture = Gesture.Exclusive(makeHold(!!ptt && !finalizing), plateTap)
  const plateOverlayOn = !!ptt && !finalizing && input.length === 0

  const a11yLabel = recording ? '小舟，结束并发送' : `${ORB_A11Y[orbState]}，开始说话`

  return (
    <View
      style={{
        borderTopWidth: 1,
        borderColor: p.line,
        backgroundColor: p.dark ? 'rgba(6,8,15,0.55)' : 'rgba(237,241,250,0.72)',
      }}
    >
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 12, paddingTop: 8 }}>
        {quickCommands.map((c) => (
          <Pressable
            key={c}
            onPress={() => onSend(c)}
            style={{ backgroundColor: p.fill, borderWidth: 1, borderColor: p.fill2, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 7 }}
          >
            <Text style={{ color: p.fg2, fontSize: p.font(12) }}>{c}</Text>
          </Pressable>
        ))}
      </ScrollView>
      <View style={{ flexDirection: 'row', gap: 10, padding: 10, alignItems: 'flex-end' }}>
        {ptt ? (
          <GestureDetector gesture={orbGesture}>
            <View
              testID="composer-orb"
              accessible
              accessibilityRole="button"
              accessibilityLabel={a11yLabel}
              accessibilityHint="轻点开始说话，说完自动发送；长按可按住说话，上滑取消"
              style={{
                width: scale(TARGET.driving, 'target', fontScale),
                height: scale(TARGET.driving, 'target', fontScale),
                borderRadius: RADIUS.full,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: recording ? p.accentSoft : 'transparent',
              }}
            >
              <AuroraOrb size={44} state={orbState} dim={orbDim} animated={orbAnimated ?? true} driving={orbDriving} />
            </View>
          </GestureDetector>
        ) : null}
        <View style={{ flex: 1 }}>
          <TextInput
            ref={inputRef}
            testID="composer-input"
            style={{
              backgroundColor: p.fill,
              borderWidth: 1,
              borderColor: p.fill2,
              borderRadius: 14,
              paddingHorizontal: 14,
              paddingVertical: 10,
              fontSize: p.font(15),
              color: p.fg1,
              maxHeight: 120,
            }}
            value={input}
            onChangeText={setInput}
            placeholder={recording ? '正在听…' : '和小舟说点什么…'}
            placeholderTextColor={p.fg3}
            multiline
            onSubmitEditing={submit}
            submitBehavior="blurAndSubmit"
            returnKeyType="send"
          />
          {plateOverlayOn ? (
            <GestureDetector gesture={plateGesture}>
              <View
                testID="composer-plate-overlay"
                accessible={false}
                importantForAccessibility="no"
                style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
              />
            </GestureDetector>
          ) : null}
        </View>
        {busy ? (
          <Pressable
            onPress={onInterrupt}
            style={{ backgroundColor: p.amberSoft, borderWidth: 1, borderColor: 'rgba(245,158,11,0.3)', borderRadius: 14, paddingHorizontal: 14, minHeight: 44, justifyContent: 'center' }}
          >
            <Text style={{ color: p.amber, fontSize: p.font(14) }}>■ 打断</Text>
          </Pressable>
        ) : null}
        {/* 发送键改圆形极光图标（B2 出账④，对标小艺 / 小爱）。虹彩纪律三处之一不变，只是从矩形「发送」
            变成圆形图标；Maestro 流都按 id 驱动（`grep '"发送"' e2e/*.yaml` 为空，核过）。
            svg 原生缺席仍回退文字——iconRuntimeAvailable() 是既有判据（坑账 §9.27） */}
        <Pressable
          testID="composer-send"
          accessibilityRole="button"
          accessibilityLabel="发送"
          onPress={submit}
          style={{
            experimental_backgroundImage: AURORA.gradient,
            width: scale(44, 'target', fontScale),
            height: scale(44, 'target', fontScale),
            borderRadius: RADIUS.full,
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 4px 22px rgba(91,140,255,0.45)',
          }}
        >
          {iconRuntimeAvailable() ? (
            <Icon name="send" size={22} color="#fff" />
          ) : (
            <Text style={{ color: '#fff', fontSize: p.font(15), fontWeight: '600' }}>发</Text>
          )}
        </Pressable>
      </View>
    </View>
  )
}
