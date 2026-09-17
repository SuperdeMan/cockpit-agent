// mobile/src/features/chat/VoiceSheet.tsx
// 语音层（方案 §5.2）：PTT / 唤醒词 / S2S 三种说话方式升起的**同一张层**。
// **它不持有任何转写 / 回答状态**——它是「对话记录里当前这一轮」的视图（§5.2 规则 2）：
// 转写读当前用户气泡、回答读当前助手气泡、卡片读 final.ui_card；收起、切后台、折叠展开都不会丢，
// 因为根本没有「等收起再写」这一步。升不升起、升多高由 derivePresence 定（`snapshot.input` /
// `snapshot.sheetDetent`），这里只渲染与转发手势。
// 材质：外壳 G1（Glass）；零新依赖——手势用随 expo-router 在场的 react-native-gesture-handler
// （PackageList.java:73 已注册），高度用 reanimated。
// 性能纪律（方案 §11.4）：层开着时它的 88dp 大球是**唯一**跑循环动画的光球，Composer 主球转静态。
//
// **2026-09-17 结构（设计 2026-09-17 §1）：光球是层的锚，不是内容；内容才滚，锚不滚。**
// 原来大球住在 ScrollView 的内容流里（转写之后、回答之前）：转写多两行就把球推到层底之下，往下滚去看回答又把
// 球滚到把手带之下——用户看到的「光球被下面 / 上面挡住」都是它在流里。现在：
//   · 固定头区 `voice-sheet-header` = 大球 + 胶囊（竖屏在把手带下方，横屏车载在左列），不进滚动区；
//   · 内容区 `voice-sheet-scroll` = 转写 → 思考三点 → 回答 → 已打断 → chips → 卡片，**跟底**（判据复用记录列表那份
//     `history.ts::followOnContentChange`）：层升起 / 新一轮 / 回答开始无条件贴底，之后离底不超过阈值就跟、上滚不拽；
//   · 层高下限随之改按「chrome + 头区 + 该档该看见的内容」（`ui/layout/sheetHeight.ts`）。
import { BlurView } from 'expo-blur'
import { useEffect, useRef, useState, type RefObject } from 'react'
import { Pressable, ScrollView, Text, View, type LayoutChangeEvent } from 'react-native'
import { Gesture, GestureDetector } from 'react-native-gesture-handler'
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated'

import {
  SHEET_PAN_ACTIVATE_DY,
  SHEET_PAN_FAIL_DX,
  sheetDragOffset,
  sheetDragOutcome,
  sheetPanAtTop,
  type SheetRect,
} from '@/ui/layout/sheetGesture'

import { edgeGlowActive, type OrbTempo } from '@/core/presence/orbPolicy'
import { sheetCapsuleText, type PresenceSnapshot } from '@/core/presence/presence'
import type { CandidateState } from '@/core/session/candidates'
import { followUpChips, MAX_CHIPS } from '@/core/session/followUps'
import { followOnContentChange } from '@/core/session/history'
import type { TurnView } from '@/core/session/turnView'
import type { FontScalePref } from '@/core/settings/store'
import { CardRenderer } from '@/features/cards/CardRenderer'
import { DrivingCardSummary } from '@/features/cards/DrivingCardSummary'

import { FollowUpChips } from './FollowUpChips'
import { AuroraOrb, EdgeGlow, Glass, StreamCursor, ThinkDots } from '@/ui/aurora'
import { ORB_A11Y } from '@/ui/aurora/AuroraOrb'
import { Icon, iconRuntimeAvailable } from '@/ui/Icon'
import { SHEET_BOTTOM_FADE_DP, sheetHeightDp, sheetOrbDp } from '@/ui/layout/sheetHeight'
import { GLASS, RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

/** 层底缘渐隐高度：判据搬到了 `ui/layout/sheetHeight.ts`（它是层高下限的一项），这里只转出口 */
export { SHEET_BOTTOM_FADE_DP } from '@/ui/layout/sheetHeight'

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
  /** 横屏车载（`layout.mode === 'driving-landscape'`）：左 40% 球 + 胶囊 / 右 60% 转写 + 回答 + 卡 */
  split: boolean
  /** 被糊的背景（B4-8 / §5.11）：非 null ⇒ 真模糊路径（BlurView + 更薄的 tint）；
   *  null ⇒ 回落 G1-tint（减少透明度 / 行车档 / ref 还没挂上）。判据全在 ChatScreen，本组件只消费 */
  blurTarget: RefObject<View | null> | null
  /** 跨页时底层可能是地图或设置文字，用实色底保证回答可读。 */
  solid?: boolean
  /** 从顶缘把手带下拖 / 轻点把手带 / 点暗区 / 返回键（B5-12 之后底栏没有了） */
  /** 此刻该给停播键吗（判据 `core/voice/stopPlayback.ts::canStopPlayback`，AR03）：层内停止键**只在这时挂载** */
  stoppable?: boolean
  /** 只停播（AR03 / 评审 R06）：停当前出声，不取消在飞请求、不开麦 */
  onStopPlayback?(): void
  onCollapse(): void
  /** 轻点层内大球 = 开始说话（B5-15，只在 split 时给）：driving-landscape 下层覆盖整列，
   *  Composer 的光球被盖住 ⇒ 层内大球接替它，否则「轻点始终能说」（§5.1.1）在横屏断掉 */
  onOrbTap?: () => void
  onSend(text: string): void
}

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
/** 头区 / 内容区的排版常量——与 `ui/layout/sheetHeight.ts` 的下限逐项对应，改这里要同步改那边 */
const PAD = 16
const GAP = 12

export function VoiceSheet(props: VoiceSheetProps) {
  const { p, fontScale, snapshot, turn, containerHeight } = props
  const open = snapshot.input === 'voice-sheet' && containerHeight > 0
  const driving = props.driving
  // 内容区画不画「当前这一轮」：判据在 derivePresence 的 `sheetBody`（收音初始草稿未出现 / 行车档答后回落 ⇒ 只剩头区），
  // 这里只读结果。**层不消失**（常驻，§6），消失的是内容。
  const bodyEmpty = snapshot.sheetBody === 'none'
  // B4-13 缺陷 A：detent 是比例，表达不了「内容有固有最小高」——行车 / 泊车都过 sheetHeightDp 的下限
  // （判据与真机容器读数都在 ui/layout/sheetHeight.ts）。内容区为空时主体 0。
  const target = sheetHeightDp({
    detent: snapshot.sheetDetent,
    containerH: containerHeight,
    driving,
    split: props.split,
    fontScale,
    terse: bodyEmpty,
  })
  // 挂载态比 open 晚 COLLAPSE_MS 关掉：让收起动画播完再卸载
  const [mounted, setMounted] = useState(open)
  // 「要展开了」在渲染期就挂上（React 官方 adjusting state）：effect 要等 commit 完才跑，
  // 层会晚一帧才进树，展开动画的第一帧因此丢掉。收起仍走 effect 里的定时器（真要等动画播完）。
  const [openSeen, setOpenSeen] = useState(open)
  if (openSeen !== open) {
    setOpenSeen(open)
    if (open) setMounted(true)
  }
  const h = useSharedValue(0)
  // 跟手位移（2026-09-11 整层下滑收起）：拖动中层随手指下移，松手收起或回弹；每次展开归零
  const dragY = useSharedValue(0)
  useEffect(() => {
    if (open) {
      dragY.value = 0
      h.value = withSpring(target, { damping: 18, stiffness: 160 })
      return
    }
    h.value = withTiming(0, { duration: COLLAPSE_MS })
    const t = setTimeout(() => setMounted(false), COLLAPSE_MS)
    return () => clearTimeout(t)
  }, [open, target, h, dragY])
  const sheetStyle = useAnimatedStyle(() => ({ height: h.value, transform: [{ translateY: dragY.value }] }))

  // ── 内容区跟底（2026-09-17，设计 §3）──
  // 判据不新写：`followOnContentChange(离底, 视口, 旗)`——旗为真无条件贴底，否则离底 ≤ 0.2×视口才贴。
  // 旗在三个时刻挂上（都是「用户等着看的东西来了」）：层升起、当前轮的用户气泡换了（新一轮）、助手气泡换了（回答开始）；
  // 在 `onContentSizeChange` 里消费——新内容要等 ScrollView 量完才滚得到（与记录列表 `ownSendRef` 同一个坑）。
  // 离底读数在 `onScroll` 上记 ref：RN 不会因内容长了就发 scroll 事件，所以 ref 里就是「增高之前」的离底距离。
  const scrollRef = useRef<ScrollView>(null)
  const offsetRef = useRef(0)
  const viewportRef = useRef(0)
  const pendingFollowRef = useRef(true)
  // 滚动区离开了顶部（真机 `05-sheet-open`：跟到末尾后被裁的首行紧贴头区，像裁切故障）⇒ 顶缘也给一条渐隐，
  // 与底缘同高同色；只在离开顶部时画——贴顶时首行就在 paddingTop 之下，压一层渐隐会把它糊掉。
  // 只在跨过 0 的那一刻 setState（不是每个 scroll 事件都重渲）。
  const [scrolledAway, setScrolledAway] = useState(false)
  const scrolledAwayRef = useRef(false)
  const userId = turn.user?.id ?? ''
  const assistantId = turn.assistant?.id ?? ''
  useEffect(() => {
    pendingFollowRef.current = true
  }, [open, userId, assistantId])
  const onContentSizeChange = () => {
    if (!followOnContentChange(offsetRef.current, viewportRef.current, pendingFollowRef.current)) return
    pendingFollowRef.current = false
    scrollRef.current?.scrollToEnd({ animated: false })
  }

  // 整层下滑收起（2026-09-11，用户：「整页任意位置下滑即可收起，市面 App 都这么做」）。
  // B5-12 把 Pan 限定在把手带是为了避开两个冲突，这里各用正解而不是回避：
  //  · 与层内 ScrollView 抢位移 ⇒ `Gesture.Native()` 包住 ScrollView 并声明 simultaneous，
  //    **只在手势开始时滚动区处于顶部**才接管下拉（`atTop`）；不在顶部的拖动整段属于滚动区；
  //  · 吃掉 chips 横滑 ⇒ `failOffsetX`：横向先动 16dp 即失败，`activeOffsetY(12)` 之前也不激活，
  //    轻点（chips / 卡内按钮 / 停止键 / 把手带）照旧落到 Pressable。
  // 2026-09-17：内容区跟底之后流式期间滚动区常在底部，「在顶部才接管」会让收起手势失效 ⇒ 手指落在滚动区**之外**
  // （把手带 / 头区）永远接管（`sheetPanAtTop`，判据在 sheetGesture.ts）；滚动区的矩形由内容区 onLayout 时
  // 相对 Pan 所在的 View 量一次（`measureLayout`；量不到 = 退回 09-11 的偏移判据）。
  // 松手判定在 `ui/layout/sheetGesture.ts`（距离 80 或快甩），这里只转发。
  // 滚动偏移与「手势开始时在不在顶部」用 shared value 存（不用 ref）：手势回调在渲染期定义、触摸时才跑，
  // `react-hooks/refs` 无法证明这一点会判红；shared value 的 get/set 是它认可的可变外部状态入口（StageDrawer 同款）。
  const scrollY = useSharedValue(0)
  const atTop = useSharedValue(true)
  const scrollRect = useSharedValue<SheetRect | null>(null)
  const dragRef = useRef<View>(null)
  const contentRef = useRef<View>(null)
  const scrollGesture = Gesture.Native()
  const pan = Gesture.Pan()
    .runOnJS(true)
    .activeOffsetY(SHEET_PAN_ACTIVATE_DY)
    .failOffsetX([-SHEET_PAN_FAIL_DX, SHEET_PAN_FAIL_DX])
    .simultaneousWithExternalGesture(scrollGesture)
    .onBegin((e) => {
      atTop.set(sheetPanAtTop({ x: e.x, y: e.y, scrollRect: scrollRect.get(), scrollOffset: scrollY.get() }))
    })
    .onUpdate((e) => {
      dragY.set(sheetDragOffset(atTop.get(), e.translationY))
    })
    .onEnd((e) => {
      const outcome = sheetDragOutcome({ atTop: atTop.get(), translationY: e.translationY, velocityY: e.velocityY })
      if (outcome === 'dismiss') props.onCollapse()
      else dragY.set(withSpring(0, { damping: 20, stiffness: 220 }))
    })
    .onFinalize((_e, success) => {
      // 被系统手势 / 取消打断（没走到 onEnd）时别把层留在半路
      if (!success) dragY.set(withSpring(0, { damping: 20, stiffness: 220 }))
    })
  // 内容区相对 Pan 所在 View 的矩形：布局变了（层高动画 / 旋转）就重量。react-test-renderer 没有宿主实例 ⇒ 两个 ref 都是 null，
  // 矩形留 null、退回偏移判据。
  const onContentLayout = (e: LayoutChangeEvent) => {
    viewportRef.current = Math.round(e.nativeEvent.layout.height)
    const node = dragRef.current
    const wrap = contentRef.current
    if (!node || !wrap || typeof wrap.measureLayout !== 'function') return
    wrap.measureLayout(node, (x, y, w, hh) => {
      scrollRect.set({ x, y, w, h: hh })
    })
  }
  if (!mounted) return null

  const user = turn.user
  const assistant = turn.assistant
  const body = scale(TYPE.body, 'text', fontScale)
  // B4-11 §6「目标 ≥56dp」：层内按钮 / chips 行车 56、泊车 48。
  // B5-12 之后层内唯一的目标演员是顶缘把手带（底栏撤了），它照旧用这个值。
  const targetBtn = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  // B5-15 缺陷 A 横屏半的最后一道保险：全部 lever 之后容器仍装不下 0.4 档最小高 ⇒ 球降到泊车的 88。
  // **判据只有 sheetHeight.ts 一份**，这里只读结果；`sheetHeightDp` 里用的是同一个函数，两边不会打架。
  const orbDp = sheetOrbDp({ containerH: containerHeight, driving, split: props.split, fontScale })
  const capsuleText = sheetCapsuleText(snapshot)
  const capsuleColor =
    snapshot.capsule?.tone === 'red'
      ? p.red
      : snapshot.capsule?.tone === 'amber'
        ? p.amber
        : snapshot.capsule?.tone === 'accent'
          ? p.accent
          : p.fg2
  // 壳底色只算一次：壳本身与底缘渐隐遮罩同源（solid=实色 p.bg；真模糊=更薄的 tint；否则 G1 tint）。
  // 行车档由 ChatScreen 传 `solid`（打磨批 A / P08 / V3）：G0 实色，记录不再透过层与层内文字叠字。
  const blurred = !!props.blurTarget && !props.solid
  const shellColor = props.solid ? p.bg : shellTint(p.bg, blurred ? GLASS.frosted.tintOverBlur : GLASS.frosted.tint)
  // 渐隐的起点用同一 rgb、alpha 0——用 `transparent`（黑色 alpha 0）在浅色壳上会先经过一段灰
  const fadeFrom = shellTint(p.bg, 0)
  const answerSize = scale(driving ? TYPE.h2 : TYPE.body + 1, 'text', fontScale)

  // ── 固定头区：大球 + 胶囊（不进滚动区）──
  // 大光球：snapshot.primary 驱动（listening→thinking→speaking→followup）；十条不变量内。行车档 120dp（§6），泊车 88。
  // B5-15：split 时层覆盖整列 ⇒ Composer 的光球被盖住，大球接替「轻点即说」（§5.1.1「轻点始终能说」；行车条款：只轻点，无长按上滑）。
  const header = (
    <View
      testID="voice-sheet-header"
      style={props.split ? { width: '40%', gap: GAP, alignItems: 'center' } : { paddingTop: PAD, paddingHorizontal: PAD, gap: GAP, alignItems: 'center' }}
    >
      {props.split && props.onOrbTap ? (
        <Pressable
          testID="voice-sheet-orb"
          accessibilityRole="button"
          accessibilityLabel={`${ORB_A11Y[snapshot.primary]}，开始说话`}
          onPress={props.onOrbTap}
          style={{ width: orbDp, height: orbDp, borderRadius: orbDp / 2, alignItems: 'center', justifyContent: 'center' }}
        >
          <AuroraOrb size={orbDp} state={snapshot.primary} dim={snapshot.dim} animated={props.motion.orb !== 'static'} driving={props.motion.orb === 'slow'} />
        </Pressable>
      ) : (
        <AuroraOrb size={orbDp} state={snapshot.primary} dim={snapshot.dim} animated={props.motion.orb !== 'static'} driving={props.motion.orb === 'slow'} />
      )}
      {/* 胶囊文案（同 §4.3，此处放大）；识别中不复读转写（判据 presence.ts::sheetCapsuleText） */}
      {capsuleText ? (
        <View testID="voice-sheet-capsule" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {snapshot.capsule?.live ? (
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: p.accent, boxShadow: `0 0 10px ${p.accent}` }} />
          ) : null}
          <Text style={{ color: capsuleColor, fontSize: body }}>{capsuleText}</Text>
        </View>
      ) : null}
    </View>
  )

  // ── 可滚内容区：转写 → 思考 → 回答 → 已打断 → chips → 卡片；sheetBody='none' 时整个不渲染 ──
  const content = bodyEmpty ? null : (
    <>
      {/* 转写区：大字 20pt。T4 起它是草稿气泡（增量沉淀），定稿后仍是同一条。
          打磨批 A（P07）：收音中还没识别出字 ⇒ **不渲染光标**（一根孤零零的光标条像残影）；
          2026-09-17 起也不再渲染灰字「在听…」占位——头区胶囊已经在说这一句（一屏一份状态），转写来了再出现。 */}
      {user && user.text ? (
        <Text
          testID="voice-sheet-transcript"
          accessibilityLiveRegion="polite"
          style={{
            color: p.fg1,
            fontSize: scale(20, 'text', fontScale),
            lineHeight: scale(28, 'line', fontScale),
            textAlign: props.split ? 'left' : 'center',
            alignSelf: 'stretch',
          }}
        >
          {props.visionIds.includes(user.id) ? (iconRuntimeAvailable() ? <Icon name="camera" size={18} color={p.fg3} /> : '看图 ') : null}
          {props.visionIds.includes(user.id) ? ' ' : ''}
          {user.text}
          {user.id === props.draftUserId ? <StreamCursor h={scale(20, 'text', fontScale)} animated={props.motion.loops} /> : null}
        </Text>
      ) : null}
      {/* 回答区：speech_delta 逐字 + StreamCursor；pending 时 ThinkDots。行车档 18pt（§6） */}
      {assistant?.pending ? <ThinkDots color={p.accent} animated={props.motion.loops} /> : null}
      {assistant?.text ? (
        <Text
          testID="voice-sheet-answer"
          accessibilityLiveRegion="polite"
          style={{
            color: assistant.error ? p.red : p.fg1,
            fontSize: answerSize,
            lineHeight: scale(driving ? 28 : 24, 'line', fontScale),
            alignSelf: 'stretch',
          }}
        >
          {assistant.text}
          {assistant.streaming ? <StreamCursor h={answerSize} animated={props.motion.loops} /> : null}
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
    </>
  )

  // 滚动区 + 底缘渐隐（P06）：内容底部多留 24dp，遮罩压在滚动区最下 24dp、不拦触摸。
  // 滚动区包在 Native 手势里与整层 Pan simultaneous；`onScroll` 记偏移与离底距离。
  // `overScrollMode="never"`：顶部下拉时层在跟手，Android 的边缘辉光叠上去像两个东西在动。
  const scrollRegion = (
    <View ref={contentRef} testID="voice-sheet-content" style={{ flex: 1 }} onLayout={onContentLayout}>
      <GestureDetector gesture={scrollGesture}>
        <ScrollView
          ref={scrollRef}
          testID="voice-sheet-scroll"
          contentContainerStyle={
            props.split
              ? { paddingBottom: PAD + SHEET_BOTTOM_FADE_DP, gap: GAP, alignItems: 'stretch' }
              : { paddingTop: GAP, paddingHorizontal: PAD, paddingBottom: PAD + SHEET_BOTTOM_FADE_DP, gap: GAP, alignItems: 'center' }
          }
          keyboardShouldPersistTaps="handled"
          onScroll={(e) => {
            const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent
            scrollY.set(contentOffset.y)
            offsetRef.current = Math.max(0, Math.round(contentSize.height - layoutMeasurement.height - contentOffset.y))
            const away = contentOffset.y > 1
            if (away !== scrolledAwayRef.current) {
              scrolledAwayRef.current = away
              setScrolledAway(away)
            }
          }}
          onContentSizeChange={onContentSizeChange}
          scrollEventThrottle={16}
          overScrollMode="never"
        >
          {content}
        </ScrollView>
      </GestureDetector>
      <View
        pointerEvents="none"
        testID="voice-sheet-fade"
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          height: SHEET_BOTTOM_FADE_DP,
          experimental_backgroundImage: `linear-gradient(to bottom, ${fadeFrom}, ${shellColor})`,
        }}
      />
      {scrolledAway ? (
        <View
          pointerEvents="none"
          testID="voice-sheet-fade-top"
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: 0,
            height: SHEET_BOTTOM_FADE_DP,
            experimental_backgroundImage: `linear-gradient(to bottom, ${shellColor}, ${fadeFrom})`,
          }}
        />
      ) : null}
    </View>
  )

  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      {/* 记录变暗、仍可见（§5.2）：点暗区 = 收起。
          40% → 60% 是第 3 批附加项①授权的升级档：58% 的壳底之后记录仍以未压暗的 ~45% 强度
          透过来（真机同帧两条同类文字带：层外幅度 41.9 / 层内 18.8），层内答案与记录里的
          同一段话叠在一起两边都难读。60% 之后透出降到 ~30%。「仍可见」保留 */}
      {/* ⚠ B5-12 真机抓到：暗区原来也叫「收起语音层」，与新的把手带**说明重复**——读屏念两遍
          （B4 Scanner 出账③「多个项目具有相同的说明」的同一形态，只是这次是本批自己造的）。
          暗区从无障碍树里拿掉：同一个动作读屏侧已由把手带提供（role=button + hint），
          可达性不减；点暗区收起对视力用户逐字节不变。 */}
      <Pressable
        importantForAccessibility="no-hide-descendants"
        accessibilityElementsHidden
        onPress={props.onCollapse}
        style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.6)' }}
      />
      <Animated.View testID="voice-sheet" style={[{ position: 'absolute', left: 0, right: 0, bottom: 0 }, sheetStyle]}>
        {/* 整层 Pan 挂在这一层：把手带、通知条、头区、滚动区都在它之内（2026-09-11） */}
        <GestureDetector gesture={pan}>
        <View ref={dragRef} testID="voice-sheet-drag" style={{ flex: 1 }}>
        <Glass
          p={p}
          r={RADIUS['2xl']}
          style={{ flex: 1, overflow: 'hidden', borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }}
        >
          {/* 壳底（§5.11 G1 frosted）：真模糊在场 = BlurView + 更薄的 tint；否则 = B2 附加①的 tint（.58）。
              同屏只有这一个 BlurView（§5.11 禁「同屏多个动态 Blur」）——顶栏与舞台压在静态深空底上，糊了没收益 */}
          {blurred ? (
            <>
              <BlurView
                pointerEvents="none"
                blurMethod="dimezisBlurView"
                blurTarget={props.blurTarget ?? undefined}
                intensity={60}
                tint={p.dark ? 'dark' : 'light'}
                style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}
              />
              <View
                pointerEvents="none"
                testID="voice-sheet-shell"
                style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: shellColor }}
              />
            </>
          ) : (
            <View
              pointerEvents="none"
              testID="voice-sheet-shell"
              style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: shellColor }}
            />
          )}
          {/* 顶缘极光（方案 §5.2 规则 6）：只在 listening / thinking */}
          <EdgeGlow active={edgeGlowActive(snapshot)} animated={props.motion.loops} />
          {/* 顶缘把手带（B5-12，泓舟 B4 真机轮原话①）：底栏「收起 / 打断」撤掉——收起 = 向下拖
              （2026-09-11 起整层任意位置都行，Pan 挂在外层）/ 轻点把手带 / 点暗区 / 返回键，
              打断 = Composer 的 ⬆/■ 合一键（B5-13）。
              它接替 voice-sheet-collapse 的 §6「目标 ≥56dp」演员身份（testID 沿用，探针脚本不改）。
              把手本身仍是 G2 的那条 36×4（§5.11），只是外面套了一条 ≥56dp 的可点带。 */}
          <View>
            <Pressable
              testID="voice-sheet-collapse"
              accessibilityRole="button"
              accessibilityLabel="收起语音层"
              accessibilityHint="向下拖或轻点收起"
              onPress={props.onCollapse}
              style={{ minHeight: targetBtn, alignItems: 'center', justifyContent: 'center' }}
            >
              <View style={{ width: 36, height: 4, borderRadius: 2, backgroundColor: p.fill2 }} />
            </Pressable>
            {/* 层内停止键（AR03 / 评审 R06 + R09 横屏）：**只在真的有声音时挂载**。
                driving-landscape 下层覆盖整个记录区 + Composer，合一键够不到 ⇒ 不给这一枚就只能
                「先收层再停」，正是 R09 那条。绝对定位在把手带那一行右侧：那行 `minHeight` 已经是
                目标高，**不改行高 ⇒ `ui/layout/sheetHeight.ts` 的 chrome 一个不动**。
                与 B5-12「撤掉底栏收起/打断两枚常驻键」不冲突——撤的是常驻键，这是条件出现的单一停播键。 */}
            {props.stoppable && props.onStopPlayback ? (
              <Pressable
                testID="voice-sheet-stop"
                accessibilityRole="button"
                accessibilityLabel="停止播报"
                accessibilityHint="只停止声音，不会开始录音"
                onPress={props.onStopPlayback}
                style={{
                  position: 'absolute',
                  right: 8,
                  top: 0,
                  bottom: 0,
                  minWidth: targetBtn,
                  paddingHorizontal: 10,
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Text style={{ color: p.amber, fontSize: scale(TYPE.body - 1, 'text', fontScale), fontWeight: '600' }}>
                  停止播报
                </Text>
              </Pressable>
            ) : null}
          </View>
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
          {/* 横屏车载 split（§6「横屏 40:60」）：左 40% 头区 / 右 60% 内容区。竖屏：头区在上、内容区在下。
              两种形态**同一份内容顺序**，只是头区的位置不同。 */}
          {props.split ? (
            <View style={{ flex: 1, flexDirection: 'row', paddingTop: PAD, paddingHorizontal: PAD, gap: PAD, alignItems: 'flex-start' }}>
              {header}
              {scrollRegion}
            </View>
          ) : (
            <>
              {header}
              {scrollRegion}
            </>
          )}
        </Glass>
        </View>
        </GestureDetector>
      </Animated.View>
    </View>
  )
}
