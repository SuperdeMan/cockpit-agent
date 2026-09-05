// 对话主屏（M1-3/M1-6/M1-7 装配，Aurora Glass 复刻轮重皮）：
//  - GatewaySession（下行帧→SessionCore.handleFrame，状态→connStatus）
//  - 双形态外壳：窗口短边 ≥600dp 平板双栏（右=玻璃舞台：车况+提醒+焦点卡），旋转即时切
//  - 确认条按台账渲染（isPendingLive），位置征询条只激活最新一条
//  - 视觉照 hmi shell.css：深空渐变+极光 blob 打底，顶栏=品牌光球+连接 pill，空对话=欢迎态大光球
// 改服务器配置 → 回本屏时按 edgeUrl+token 判变 → 断开重连（M1-5 服务器分区语义）。
import { FlashList } from '@shopify/flash-list'
import { BlurTargetView } from 'expo-blur'
import { Link, Redirect, useFocusEffect, useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BackHandler, KeyboardAvoidingView, Pressable, Text, useWindowDimensions, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { isPendingLive } from '@shared/pendingOps.mjs'
import type { Msg } from '@shared/types.ts'

import { loadServerConfig } from '../../core/config/storage'
import type { ServerConfig } from '../../core/config/types'
import { buildReceipt } from '../../core/session/receipt'
import { ensureWired, type Wired } from '../../core/session/wiring'
import type { SendOpts } from '../../core/session/store'
import { currentTurn } from '../../core/session/turnView'
import { settingsStore, type FontScalePref } from '../../core/settings/store'
import { activityLog } from '../../core/presence/activityLog'
import { useReduceMotion } from '../../core/a11y/reduceMotion'
import { composerOrbAnimated, edgeGlowActive, loopsAnimated, orbTempo } from '../../core/presence/orbPolicy'
import { composerInputMode, sheetResident } from '../../core/presence/drivingMode'
import { MIC_LABEL } from '../../core/presence/presence'
import { lowPower } from '../../core/power/lowPower'
import { usePowerFacts } from '../../core/power/usePowerFacts'
import { AuroraBackground, AuroraOrb, type OrbState } from '../../ui/aurora'
import { Icon, iconRuntimeAvailable, type IconName } from '../../ui/Icon'
import { PANE_GAP, screenSwitch, tabletopSplit } from '../../ui/layout/sizeClass'
import { useLayout } from '../../ui/layout/useLayout'
import { usePalette } from '../../ui/theme'
import { TARGET, scale } from '../../ui/tokens'
import { StageDrawer } from '../stage/StageDrawer'
import { StagePane } from '../stage/StagePane'
import { speechController } from '../../core/voice/speech'
import { captureVisionFrame, needsVisionFrame } from '../../core/vision/frame'
import { Composer } from './Composer'
import { FocusDock } from './FocusDock'
import { MessageBubble } from './MessageBubble'
import { PresenceCapsule } from './PresenceCapsule'
import { PrivacyRail } from './PrivacyRail'
import { VoiceSheet } from './VoiceSheet'
import { useHandsFree } from './useHandsFree'
import { usePresence, type SheetOverride } from './usePresence'
import { usePtt } from './usePtt'

// 免唤醒 FSM 态 → 用户看得懂的一行字与一个点的颜色。
// **不直接显示 FSM 名字**：ARMED/FOLLOWUP 对用户没有意义，而「在不在听」有。
// ⚠ B1 之后这两张表只在 **`uxV2Presence=false` 的回滚分支**里用（v2 下这些话由状态胶囊说）。
// 它们**刻意留着**——回滚路径不是一句话，是一段真的要能跑起来的代码（§11.5）；B4 稳定后再删。
const HF_LABEL: Record<string, string> = {
  ARMED: '待唤醒 · 说「小舟小舟」',
  LISTENING: '在听…',
  THINKING: '思考中…',
  SPEAKING: '播报中 · 说话可打断',
  FOLLOWUP: '可以直接接着说',
  IDLE: '免唤醒未启动',
}
const HF_DOT: Record<string, string> = {
  ARMED: '#64748B',
  LISTENING: '#22D3EE',
  THINKING: '#A78BFA',
  SPEAKING: '#34D399',
  FOLLOWUP: '#22D3EE',
  IDLE: '#475569',
}

export function ChatScreen() {
  const [cfgState, setCfgState] = useState<'loading' | 'missing' | ServerConfig>('loading')
  const [wired, setWired] = useState<Wired | null>(null)

  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)

  // 配置装载与变更检测（回本屏即重查：设置页改完服务器返回时生效）
  useFocusEffect(
    useCallback(() => {
      let alive = true
      void loadServerConfig().then((cfg) => {
        if (!alive) return
        setCfgState(cfg ?? 'missing')
      })
      return () => {
        alive = false
      }
    }, []),
  )

  // 按配置建立/复用会话单例（wiring.ts：仅 edgeUrl+token 变化才断开重建）
  useEffect(() => {
    if (cfgState === 'loading' || cfgState === 'missing') return
    setWired(ensureWired(cfgState))
  }, [cfgState])

  if (cfgState === 'missing') return <Redirect href="/onboarding" />
  if (cfgState === 'loading' || !wired) {
    return <View style={{ flex: 1, backgroundColor: p.bg }} />
  }
  return <ChatBody p={p} wired={wired} cfg={cfgState} />
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

/** 欢迎态（hmi ChatView Welcome 同款）：大光球 + 问候 + 快捷指令，替代此前的空白列表 */
function Welcome({
  p,
  name,
  hasVoice,
  quickCommands,
  animated = true,
  onSend,
}: {
  p: ReturnType<typeof usePalette>
  name: string
  hasVoice: boolean
  quickCommands: string[]
  /** reduce-motion（B4-3）：欢迎球也是循环动画的一份 */
  animated?: boolean
  onSend: (text: string) => void
}) {
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 24 }}>
      <AuroraOrb size={88} state="idle" animated={animated} />
      <Text style={{ color: p.fg1, fontSize: p.font(26), fontWeight: '600', marginTop: 14 }}>
        我是{name}
      </Text>
      <Text style={{ color: p.fg2, fontSize: p.font(14) }}>
        {hasVoice ? '按住下方光球说话，或点指令试试' : '点下方指令试试，或直接输入'}
      </Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, justifyContent: 'center', marginTop: 12 }}>
        {quickCommands.slice(0, 3).map((q) => (
          <Pressable
            key={q}
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
    </View>
  )
}

function ChatBody({
  p,
  wired,
  cfg,
}: {
  p: ReturnType<typeof usePalette>
  wired: Wired
  cfg: ServerConfig
}) {
  const { core } = wired
  const {
    messages, pendingOps, vehState, connStatus, pendingLocationText, uncertainIds, draftUserId, interruptedIds, s2sIds, visionIds,
    turnMeta, confirmLog,
  } = useStore(core.store)
  const { settings } = useStore(settingsStore)

  const [notice, setNotice] = useState('')
  // 发送入口（文本 / PTT / 免唤醒三条路共用这一个）。M4-6 视觉抓帧挂在这里，
  // 分支与 hmi/src/App.tsx:718-723 逐条对照：
  //  · 已带 vision_frame_id 的重发不再抓（`visionDone`）
  //  · **判据用共享的 needsVisionFrame**——采集面就是隐私面，判据分叉等于两个端的
  //    隐私边界不一样，而没有任何东西会红
  const onSend = useCallback(
    (text: string, metaExtra?: Record<string, string>, opts?: SendOpts) => {
      const visionDone = metaExtra ? 'vision_frame_id' in metaExtra : false
      if (settings.visionEnabled && !visionDone && needsVisionFrame(text)) {
        activityLog.push('camera', `触发词「${text.slice(0, 12)}」`)
        // 先落气泡（方案 §5.5）：用户那句话**立刻**上屏带 📷，vision_frame_id 迟到再补进 meta——
        // 相机冷启动几百毫秒，这段时间用户自己的话不该还没出现。草稿 / S2S 转正的气泡直接复用
        const bubbleId = opts?.bubbleId ?? core.beginUserBubble(text)
        core.markVision(bubbleId)
        void captureVisionFrame(cfg.audioUrl).then((fid) =>
          core.send(text, { ...(metaExtra || {}), vision_frame_id: fid }, { ...(opts || {}), bubbleId }),
        )
        return
      }
      core.send(text, metaExtra, opts)
    },
    [core, settings.visionEnabled, cfg.audioUrl],
  )
  const onConfirm = useCallback(
    (reply: '确认' | '取消', operationId?: string) => {
      // 位置授权同意 = 定位这一档「开了」：激活日志有了第三个产出方（评审 D8 那条 location）
      if (!operationId && pendingLocationText !== null && reply === '确认') activityLog.push('location', '位置授权 · 同意')
      core.confirmReply(reply, operationId)
    },
    [core, pendingLocationText],
  )
  const onInterrupt = useCallback(() => core.cancelCurrentTurn(), [core])
  // 语音输入（M2-2）：定稿走与文本完全相同的 send 路径——前置路由/位置闸/候选拦截
  // 一条都不能因为「这句是说出来的」而绕过
  const ptt = usePtt({
    audioUrl: cfg.audioUrl,
    sessionId: wired.session.sessionId,
    onPartial: (t) => core.draftUser(t),
    onDiscard: () => core.discardDraftUser(),
    onFinal: (text) => onSend(text, undefined, { source: 'ptt', bubbleId: core.commitDraftUser() ?? undefined }),
  })
  // 免唤醒（M4-4）。**定稿走的是同一个 onSend**——前置路由/位置闸/候选拦截/视觉抓帧
  // 一条都不能因为「这句是免唤醒说出来的」而绕过。
  const hf = useHandsFree({
    audioUrl: cfg.audioUrl,
    sessionId: wired.session.sessionId,
    enabled: settings.handsFree,
    needConfirm: pendingOps.length > 0,
    onPartial: (t) => core.draftUser(t),
    onSend: (text) => onSend(text, undefined, { source: 'handsfree', bubbleId: core.commitDraftUser() ?? undefined }),
    // S2S 自答轮沉淀（方案 §5.2.2）：这一轮此前在对话里零痕迹
    onS2sUserUtterance: (t) => core.s2sUserUtterance(t),
    onS2sAnswerDelta: (t) => core.s2sAnswerDelta(t),
    onS2sTurnEnd: (r) => core.s2sTurnEnd(r.reason),
    // 逃逸走的是同一个 onSend（视觉抓帧 / 前置路由 / 位置闸一条不少）；用户气泡复用 S2S 那条
    onS2sEscalated: (utt) => onSend(utt, undefined, { source: 's2s', bubbleId: core.takeS2sUserBubble() ?? undefined }),
    onNotice: setNotice,
    onCancelTurn: () => core.cancelCurrentTurn(),
  })

  // 语音层的显式操作（点胶囊打开 / 下拉收起），钉在当前轮上；换轮自动失效（判据在 derivePresence）
  const [sheetOverride, setSheetOverride] = useState<SheetOverride | null>(null)
  const turn = useMemo(() => currentTurn(messages), [messages])
  const latestTurnId = turn.assistant?.id ?? ''
  const [listHeight, setListHeight] = useState(0)

  // B5-8 深链 xiaozhou://voice（Shortcuts「说话」）：升层**不开麦**（§12.2：进入后仍需一次手势才录音）。
  // 一次性消费：同一次进入只升一次，用户收起后不再被参数顶回去。
  // 无消息时 latestTurnId 与 usePresence:98 的判等两边都是 ''，override 照样生效（现读核过）。
  const { voice: voiceParam } = useLocalSearchParams<{ voice?: string }>()
  const voiceParamConsumed = useRef(false)
  useEffect(() => {
    if (voiceParam !== '1' || voiceParamConsumed.current) return
    voiceParamConsumed.current = true
    setSheetOverride({ turnId: latestTurnId, mode: 'open' })
  }, [voiceParam, latestTurnId])

  // 免唤醒离开 LISTENING 而没有定稿（退出词 / 语气词 / 误唤醒回收 / 回声）：草稿不留气泡。
  // 定稿路径里 commit 先于 FSM 换态（onSend 同步发生在 _finalizeSend 内），到这里已是 no-op
  useEffect(() => {
    if (hf.fsm !== 'LISTENING') core.discardDraftUser()
  }, [hf.fsm, core])

  // ── UX v2.1 在场收集器（B1-8/B1-10）。**判断全在 derivePresence 里**，这里只是把它接上屏 ──
  // B4-10：横屏是 §6 触发③ 的一个条件，直接从窗口算——**不能经 layout**（useLayout 读
  // snapshot.driving，排在 usePresence 之后；拿它当入参就成环了）
  const win = useWindowDimensions()
  const snapshot = usePresence({
    core,
    hf,
    ptt: cfg.audioUrl ? ptt : null,
    user: cfg.token.slice(-4),
    sheetOverride,
    landscape: win.width > win.height,
  })
  // B4-6 布局：`tablet = min(w,h) >= 600` 那个单布尔换成 useLayout 五模式（判据全在 sizeClass.ts，本文件零判断）。
  // 放在 usePresence 之后——它读 snapshot.driving（行车档改布局，§7.2 第四行）。
  const layout = useLayout(snapshot.driving)
  // B4-12 主动消息仲裁要的两个事实喂给播报控制器（它读设置，但不认识行车档与 S2S 在不在忙）。
  // 判据全在 proactivePolicy.ts，这里只搬事实
  useEffect(() => {
    const s2sBusy =
      settings.voicePipeline === 's2s' && (hf.fsm === 'LISTENING' || hf.fsm === 'THINKING' || hf.fsm === 'SPEAKING')
    speechController().setProactiveCtx({ driving: snapshot.driving, s2sBusy })
  }, [snapshot.driving, hf.fsm, settings.voicePipeline])
  // B4-3 动效环境：事实在 core/a11y/reduceMotion.ts，判据在 orbPolicy.ts，这里只把布尔发下去
  const reduceMotion = useReduceMotion()
  const motionEnv = { reduceMotion }

  // B4-7 tabletop（§7.3）：分界 = 铰链上缘（窗口坐标）− 内容区在窗口里的 y。onLayout 给的是相对父级的 y，
  // 这里要的是窗口坐标 ⇒ measureInWindow；量一次不够（旋转 / 展开会变），随 layout 重量
  const contentRef = useRef<View | null>(null)
  const [contentBox, setContentBox] = useState({ y: 0, h: 0 })
  useEffect(() => {
    if (layout.mode !== 'tabletop') return
    contentRef.current?.measureInWindow((_x, y, _w, h) => setContentBox({ y, h }))
  }, [layout.mode, layout.width, layout.height])

  // §7.4：外屏↔内屏切换瞬间，正在按住的 PTT 按松手处理（手指物理上一定离开了那块屏）；轻点会话按「结束并提交」。
  // 切屏判定在 effect 里（渲染期用 ref 记 prev 会被 StrictMode 双渲吞掉事件）
  const prevFoldRef = useRef(layout.fold)
  useEffect(() => {
    const sw = screenSwitch(prevFoldRef.current, layout.fold)
    prevFoldRef.current = layout.fold
    if (!sw || ptt.state !== 'recording') return
    if (ptt.mode === 'hold') ptt.pressUp()
    else if (ptt.mode === 'tap') ptt.tap()
  }, [layout.fold, ptt])
  // 开录即告知（红线三条件③在交互时刻的落实）：正在上传原始音频、或这一轮就是端到端发起的
  const s2sNotice = snapshot.privacy.mic === 'cloudAudio' || snapshot.turnSource === 's2s'
  const v2 = settings.uxV2Presence
  const dock = settings.uxV2Dock
  // 回滚分支的光球态（v1 推导，逐字照搬 B1 之前 Composer 里的那一行）：
  // 关掉开关时光球要真的退回 v1 的三态，而不是停在 v2 的某个态上
  const legacyOrb: OrbState = ptt.state === 'recording' ? 'speaking' : ptt.state === 'finalizing' ? 'thinking' : 'idle'
  // v1 的第四条窄条（PTT 提示行）：B1 把它从 Composer 里删了，v2 下由状态胶囊表达；
  // 回滚分支要拿回来，否则「关了开关」只退回三条
  const legacyHint = ptt.partial || (ptt.state === 'finalizing' ? (ptt.slow ? '网络似乎不太顺，正在重试…' : '识别中…') : '') || ptt.error || ''
  const legacyHintIsError = !ptt.partial && ptt.state !== 'finalizing' && !!ptt.error
  // 轻点光球：哪个引擎持有麦，就由谁开始听——免唤醒开着 = 手动唤醒（FSM 自带 VAD 收尾）；
  // 否则 = PTT 的 tap 会话（端侧 VAD / 服务端尾 / 15s 三层收尾）。这是「谁持有麦」的事实，不是判据
  const hfOn = settings.handsFree && hf.availability.usable
  const startListening = useCallback(() => {
    if (!hfOn) {
      ptt.tap()
      return
    }
    if (hf.fsm === 'LISTENING') hf.endUtterance()
    else hf.wake()
  }, [hfOn, hf, ptt])
  // ■ 打断 / 播报中轻点：先停（cancel 帧 + stop TTS），再听（方案 §5.2 规则 4：层不收、speaking→listening）
  const interruptAndListen = useCallback(() => {
    core.cancelCurrentTurn()
    startListening()
  }, [core, startListening])
  // 光球轻点契约（方案 §5.1.1 表）：播报中 = 停播再录；思考 / 执行中 = 展开语音层；其余 = 开始 / 结束录音
  const onOrbTap = useCallback(() => {
    if (snapshot.agent === 'speaking') interruptAndListen()
    else if (snapshot.agent === 'thinking' || snapshot.agent === 'processing') setSheetOverride({ turnId: latestTurnId, mode: 'open' })
    else startListening()
  }, [snapshot.agent, interruptAndListen, latestTurnId, startListening])
  // 「关闭本轮麦克风」（隐私栏）与「重新开启插话」（Dock）：评审 D7——不再翻持久化开关
  const stopMic = useCallback(() => {
    ptt.cancel()
    if (hfOn) hf.recycle()
  }, [ptt, hfOn, hf])

  // ── 采集激活日志（隐私栏「最近一次」读它）：**在麦克风/摄像头真的开起来的那一处写** ──
  useEffect(() => {
    if (ptt.state === 'recording') activityLog.push('mic', '按住说话')
  }, [ptt.state])
  useEffect(() => {
    if (hf.fsm === 'LISTENING')
      activityLog.push('mic', settings.voicePipeline === 's2s' ? '唤醒词命中 · 端到端（原始音频上传）' : '唤醒词命中')
  }, [hf.fsm, settings.voicePipeline])

  const busy = messages.some((m) => m.pending || m.streaming || m.processActive)
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

  // 弱网提示条（M3-4）：**延迟 3 秒**再显示——重连本来就是常态（切基站/锁屏回来都会闪一下），
  // 每次都弹一条会把「正常自愈」渲染成「出事了」，用户学会忽略它之后真断网也就没人看了。
  const [linkWarn, setLinkWarn] = useState(false)
  useEffect(() => {
    if (connStatus === 'open') {
      setLinkWarn(false)
      return
    }
    const t = setTimeout(() => setLinkWarn(true), 3000)
    return () => clearTimeout(t)
  }, [connStatus])

  const conn =
    connStatus === 'open'
      ? { color: p.green, label: '在线' }
      : connStatus === 'connecting'
        ? { color: p.amber, label: '连接中' }
        : { color: p.red, label: '已断开' }
  // v2 健康点：**在线是灰的**——一个持续亮着的绿点会一直占用注意力，而它什么也没说
  const healthColor =
    snapshot.transport === 'online' ? p.fg3 : snapshot.transport === 'reconnecting' ? p.amber : p.red
  // v2 采集点（隐私栏入口旁的第二颗点）：**没在采集就不渲染**——一个常驻的灰点会让
  // 「现在到底在不在采」这件事看不出来，而这正是常开麦最该让用户一眼看见的事（方案 §5.10）。
  // 文案与颜色只取 MIC_LABEL（评审 D4：读屏 label 里那句「本机处理」在 PTT 那一刻是假话）。
  // 顺序即优先级：音频离机（两档琥珀）> 正在抓一帧 > 唤醒词待机——待机是常态、抓帧是事件，
  // 事件排在常态前面（B1 第 4 批坑⑥：待机排前面时，免唤醒开着抓帧那档永远显示不出来）。
  const mic = snapshot.privacy.mic
  const captureDot =
    mic !== 'off' && MIC_LABEL[mic].tone === 'amber'
      ? { color: p.amber, label: MIC_LABEL[mic].short }
      : snapshot.privacy.camera === 'singleFrame'
        ? { color: p.fg1, label: '正在抓一帧画面' }
        : mic !== 'off'
          ? { color: p.teal, label: MIC_LABEL[mic].short }
          : null
  const [privacyOpen, setPrivacyOpen] = useState(false)

  // §5.11 真模糊（B3 T9 裁决过）：被糊的背景 = 对话列表，BlurTargetView 包住它；ref 要先挂上再给 VoiceSheet
  // 渲 BlurView（首帧 null 会被 expo-blur 当成「没配」静默回落成 none——blur-spike.tsx 的 ready 模板）。
  // 回落 G1-tint 的情形（§5.11 末句）：减少透明度 / 行车档 / ref 还没挂上 /
  // 低电量 = lowPower(power)（B5-7，expo-battery：省电模式 ∨ 电量 <20%；原生缺席按 null 降级 ⇒ 不回落）
  const power = usePowerFacts()
  const blurTargetRef = useRef<View | null>(null)
  const [blurReady, setBlurReady] = useState(false)
  useEffect(() => setBlurReady(true), [])
  const blurTarget =
    blurReady && blurTargetRef.current && !settings.reduceTransparency && !snapshot.driving && !lowPower(power)
      ? blurTargetRef
      : null

  // §7.5 返回顺序：隐私栏 > 语音层（行车档 B/C 的常驻层除外——它不是「可收」的层）> 页面默认
  // （根屏返回 = 退 Activity，M3-W 定案不变；predictiveBackGestureEnabled 是原生配置，本批不碰）。
  // 收音中按返回：derivePresence 的 capturing 分支让层保持——返回不等于取消录音，
  // 取消只有上滑与「关闭本轮麦克风」两条路，刻意不加第三条。
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (privacyOpen) {
        setPrivacyOpen(false)
        return true
      }
      if (snapshot.input === 'voice-sheet' && !sheetResident(snapshot.identity, snapshot.driving)) {
        setSheetOverride({ turnId: latestTurnId, mode: 'dismissed' })
        return true
      }
      return false
    })
    return () => sub.remove()
  }, [privacyOpen, snapshot.input, snapshot.identity, snapshot.driving, latestTurnId])

  const chatColumn = (
    <View style={{ flex: 1 }}>
      <View style={{ flex: 1 }} onLayout={(e) => setListHeight(Math.round(e.nativeEvent.layout.height))}>
      <BlurTargetView ref={blurTargetRef} style={{ flex: 1 }}>
      {messages.length === 0 ? (
        <Welcome
          p={p}
          name={settings.assistantName}
          hasVoice={!!cfg.audioUrl}
          quickCommands={settings.quickCommands}
          animated={loopsAnimated(motionEnv)}
          onSend={onSend}
        />
      ) : (
        <FlashList
          data={messages}
          // FlashList v2 聊天范式：自然序 + 从底部起渲 + 新消息自动跟底
          maintainVisibleContentPosition={{ autoscrollToBottomThreshold: 0.2, startRenderingFromBottom: true }}
          extraData={[pendingOps, pendingLocationText, p.dark, settings.fontScale, uncertainIds, v2, dock, draftUserId, interruptedIds, s2sIds, visionIds, turnMeta, confirmLog, reduceMotion, snapshot.driving]}
          keyExtractor={(m) => m.id}
          renderItem={({ item }) => (
            <View style={{ paddingHorizontal: 12 }}>
              <MessageBubble
                p={p}
                msg={item}
                loops={loopsAnimated(motionEnv)}
                driving={snapshot.driving}
                confirmActive={confirmActiveOf(item)}
                inlineConfirm={!(v2 && dock)}
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
                onConfirm={onConfirm}
                onSend={onSend}
              />
            </View>
          )}
          contentContainerStyle={{ paddingVertical: 10 }}
        />
      )}
      </BlurTargetView>
      {v2 ? (
        <VoiceSheet
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          turn={turn}
          containerHeight={listHeight}
          draftUserId={draftUserId}
          interruptedIds={interruptedIds}
          visionIds={visionIds}
          s2sNotice={s2sNotice}
          candidates={core.candidates}
          motion={{ orb: orbTempo(snapshot, motionEnv), loops: loopsAnimated(motionEnv) }}
          driving={snapshot.driving}
          split={layout.mode === 'driving-landscape'}
          blurTarget={blurTarget}
          onCollapse={() => setSheetOverride({ turnId: latestTurnId, mode: 'dismissed' })}
          onSend={(t) => onSend(t)}
        />
      ) : null}
      </View>
      {/* 免唤醒状态条（M4-4）。**只在真开着的时候占高度**——一个常驻的空条会让
          「现在到底在不在听」这件事变得看不出来，而这正是常开麦最该让用户看见的事。
          文案给的是 FSM 态的人话版，不是 FSM 名字：用户不需要知道 ARMED 是什么。 */}
      {!v2 && settings.handsFree && hf.availability.usable ? (
        <View
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            paddingHorizontal: 14,
            paddingVertical: 6,
            backgroundColor: hf.fsm === 'LISTENING' ? p.accentSoft : 'transparent',
          }}
        >
          <View
            style={{
              width: 7,
              height: 7,
              borderRadius: 999,
              backgroundColor: HF_DOT[hf.fsm] ?? p.fg3,
            }}
          />
          <Text style={{ color: p.fg2, fontSize: p.font(12), flexShrink: 0 }}>
            {HF_LABEL[hf.fsm] ?? '免唤醒'}
          </Text>
          {hf.partial ? (
            <Text numberOfLines={1} style={{ color: p.fg1, fontSize: p.font(12), flex: 1 }}>
              {hf.partial}
            </Text>
          ) : null}
        </View>
      ) : null}
      {!v2 && (hf.error || notice) ? (
        <View style={{ backgroundColor: p.amberSoft, paddingHorizontal: 14, paddingVertical: 6 }}>
          <Text style={{ color: p.amber, fontSize: p.font(12) }}>{hf.error || notice}</Text>
        </View>
      ) : null}
      {/* v1 的第四条窄条：PTT 提示行。B1 把它从 Composer 里删了（v2 下由状态胶囊表达），
          回滚分支要把它拿回来——否则「关掉开关」只退回三条，不叫回到 v1 */}
      {!v2 && legacyHint ? (
        <Text
          numberOfLines={2}
          style={{
            color: legacyHintIsError ? p.amber : p.fg2,
            fontSize: p.font(13),
            paddingHorizontal: 14,
            paddingTop: 6,
          }}
        >
          {ptt.state === 'recording' ? '🎙 ' : ''}
          {legacyHint}
        </Text>
      ) : null}
      {/* 承诺面：**永远不被别的轴覆盖**（评审 P0-1——待确认时断网，那条确认照样钉着） */}
      {v2 && dock ? (
        <FocusDock
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          onConfirm={onConfirm}
          onCancelTurn={onInterrupt}
          onReenableBargeIn={hf.recycle}
        />
      ) : null}
      {/* 状态胶囊：一次只说一件「此刻」的事。点按默认打开语音层；建议胶囊（§6 触发③）
          点按 = 开行车档——**做什么由 derivePresence 给的 capsule.action 决定，判据不在这里**。 */}
      {v2 ? (
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
        quickCommands={settings.quickCommands}
        busy={busy}
        ptt={cfg.audioUrl ? ptt : null}
        orbState={v2 ? snapshot.primary : legacyOrb}
        orbDim={v2 && snapshot.dim}
        fontScale={settings.fontScale}
        onSend={onSend}
        onInterrupt={onInterrupt}
        // tabletop 下舞台已有一颗 120dp 大球在跑循环 ⇒ Composer 球让位（§11.4「同屏常态 1 个」）。
        // 判据仍是 orbPolicy，这一条例外太小不值得进纯函数（B4 §6.2 记一句）
        orbAnimated={composerOrbAnimated(snapshot, motionEnv) && layout.mode !== 'tabletop'}
        orbDriving={orbTempo(snapshot, motionEnv) === 'slow'}
        driving={snapshot.driving}
        inputMode={composerInputMode(snapshot.identity, snapshot.driving)}
        onTap={onOrbTap}
      />
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
            {/* 连接：v2 只留一个 7dp 健康点——「在线」这两个字在线时是噪声，它只在**不**在线时
                才是信息，而那时状态胶囊已经在说这件事了（方案 §5.1）。v1 保留原来的 pill */}
            {v2 ? (
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
            ) : (
              <View
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 6,
                  backgroundColor: p.fill,
                  borderWidth: 1,
                  borderColor: p.fill2,
                  borderRadius: 999,
                  paddingHorizontal: 10,
                  paddingVertical: 3,
                }}
              >
                <View
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: 4,
                    backgroundColor: conn.color,
                    boxShadow: `0 0 8px ${conn.color}`,
                  }}
                />
                <Text style={{ color: p.fg2, fontSize: p.font(11) }}>{conn.label}</Text>
              </View>
            )}
            <View style={{ flex: 1 }} />
            {/* 舞台常驻的两种形态（双栏 / 桌面）里车况已在屏上，不重复给入口；抽屉与单栏保留 */}
            {layout.mode !== 'two-pane' && layout.mode !== 'tabletop' ? (
              <TopIconLink p={p} href="/vehicle" icon="vehicle" label="车辆" driving={snapshot.driving} fontScale={settings.fontScale} />
            ) : null}
            <TopIconLink p={p} href="/settings" icon="settings" label="设置" driving={snapshot.driving} fontScale={settings.fontScale} />
          </View>
          {!v2 && linkWarn ? (
            <View style={{ backgroundColor: p.amberSoft, paddingHorizontal: 14, paddingVertical: 6 }}>
              <Text style={{ color: p.amber, fontSize: p.font(12) }}>
                {connStatus === 'connecting' ? '正在重连服务器…' : '连接已断开，正在重试'}
                ——这期间发出的消息会排队，连上后自动补发
              </Text>
            </View>
          ) : null}
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
          {v2 ? (
            <PrivacyRail
              p={p}
              fontScale={settings.fontScale}
              snapshot={snapshot}
              visible={privacyOpen}
              onClose={() => setPrivacyOpen(false)}
              onStopMic={stopMic}
            />
          ) : null}
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  )
}
