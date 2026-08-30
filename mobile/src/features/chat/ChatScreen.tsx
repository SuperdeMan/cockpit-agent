// 对话主屏（M1-3/M1-6/M1-7 装配，Aurora Glass 复刻轮重皮）：
//  - GatewaySession（下行帧→SessionCore.handleFrame，状态→connStatus）
//  - 双形态外壳：窗口短边 ≥600dp 平板双栏（右=玻璃舞台：车况+提醒+焦点卡），旋转即时切
//  - 确认条按台账渲染（isPendingLive），位置征询条只激活最新一条
//  - 视觉照 hmi shell.css：深空渐变+极光 blob 打底，顶栏=品牌光球+连接 pill，空对话=欢迎态大光球
// 改服务器配置 → 回本屏时按 edgeUrl+token 判变 → 断开重连（M1-5 服务器分区语义）。
import { FlashList } from '@shopify/flash-list'
import { Link, Redirect, useFocusEffect } from 'expo-router'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { KeyboardAvoidingView, Pressable, ScrollView, Text, View, useWindowDimensions } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { isPendingLive } from '@shared/pendingOps.mjs'
import type { Msg } from '@shared/types.ts'

import { loadServerConfig } from '../../core/config/storage'
import type { ServerConfig } from '../../core/config/types'
import { ensureWired, type Wired } from '../../core/session/wiring'
import type { SendOpts } from '../../core/session/store'
import { currentTurn } from '../../core/session/turnView'
import { settingsStore } from '../../core/settings/store'
import { activityLog } from '../../core/presence/activityLog'
import { MIC_LABEL } from '../../core/presence/presence'
import { AuroraBackground, AuroraOrb, Glass, type OrbState } from '../../ui/aurora'
import { Icon, iconRuntimeAvailable, type IconName } from '../../ui/Icon'
import { usePalette } from '../../ui/theme'
import { CardRenderer } from '../cards/CardRenderer'
import { ReminderSection } from '../vehicle/ReminderSection'
import { VehicleSection } from '../vehicle/VehiclePanel'
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
  const { width, height } = useWindowDimensions()
  const tablet = Math.min(width, height) >= 600

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
  return <ChatBody p={p} wired={wired} tablet={tablet} width={width} cfg={cfgState} />
}

/** 顶栏图标入口（hmi .au-icon-btn 同款：fill 底/圆角 12/40dp 热区）；svg 原生缺席回退文字 */
function TopIconLink({
  p,
  href,
  icon,
  label,
}: {
  p: ReturnType<typeof usePalette>
  href: '/vehicle' | '/settings'
  icon: IconName
  label: string
}) {
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
          width: 40,
          height: 40,
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
  onSend,
}: {
  p: ReturnType<typeof usePalette>
  name: string
  hasVoice: boolean
  quickCommands: string[]
  onSend: (text: string) => void
}) {
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 24 }}>
      <AuroraOrb size={88} state="idle" animated />
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
  tablet,
  width,
  cfg,
}: {
  p: ReturnType<typeof usePalette>
  wired: Wired
  tablet: boolean
  width: number
  cfg: ServerConfig
}) {
  const { core } = wired
  const {
    messages, pendingOps, vehState, connStatus, pendingLocationText, uncertainIds, draftUserId, interruptedIds, s2sIds,
  } = useStore(core.store)
  const { settings } = useStore(settingsStore)

  const [notice, setNotice] = useState('')
  // 发送入口（文本 / PTT / 免唤醒三条路共用这一个）。M4-6 视觉抓帧挂在这里，
  // 分支与 hmi/src/App.tsx:718-723 逐条对照：
  //  · 已带 vision_frame_id 的重发不再抓（`visionDone`）
  //  · **判据用共享的 needsVisionFrame**——采集面就是隐私面，判据分叉等于两个端的
  //    隐私边界不一样，而没有任何东西会红
  //  ⚠ 已知代价：抓帧要等相机冷启动（真机几百毫秒），这段时间用户自己那条气泡还没上屏。
  //    HMI 靠 `__bubbled` 先上屏再补发，App 侧的 SessionCore 没有那个入口 ⇒ 记为 M4 残留。
  const onSend = useCallback(
    (text: string, metaExtra?: Record<string, string>, opts?: SendOpts) => {
      const visionDone = metaExtra ? 'vision_frame_id' in metaExtra : false
      if (settings.visionEnabled && !visionDone && needsVisionFrame(text)) {
        activityLog.push('camera', `触发词「${text.slice(0, 12)}」`)
        void captureVisionFrame(cfg.audioUrl).then((fid) =>
          core.send(text, { ...(metaExtra || {}), vision_frame_id: fid }, opts),
        )
        return
      }
      core.send(text, metaExtra, opts)
    },
    [core, settings.visionEnabled, cfg.audioUrl],
  )
  const onConfirm = useCallback(
    (reply: '确认' | '取消', operationId?: string) => core.confirmReply(reply, operationId),
    [core],
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

  // 免唤醒离开 LISTENING 而没有定稿（退出词 / 语气词 / 误唤醒回收 / 回声）：草稿不留气泡。
  // 定稿路径里 commit 先于 FSM 换态（onSend 同步发生在 _finalizeSend 内），到这里已是 no-op
  useEffect(() => {
    if (hf.fsm !== 'LISTENING') core.discardDraftUser()
  }, [hf.fsm, core])

  // ── UX v2.1 在场收集器（B1-8/B1-10）。**判断全在 derivePresence 里**，这里只是把它接上屏 ──
  const snapshot = usePresence({ core, hf, ptt: cfg.audioUrl ? ptt : null, user: cfg.token.slice(-4), sheetOverride })
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

  const latestCard = useMemo(
    () => [...messages].reverse().find((m) => m.role === 'assistant' && m.uiCard)?.uiCard,
    [messages],
  )

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

  const chatColumn = (
    <View style={{ flex: 1 }}>
      <View style={{ flex: 1 }} onLayout={(e) => setListHeight(Math.round(e.nativeEvent.layout.height))}>
      {messages.length === 0 ? (
        <Welcome
          p={p}
          name={settings.assistantName}
          hasVoice={!!cfg.audioUrl}
          quickCommands={settings.quickCommands}
          onSend={onSend}
        />
      ) : (
        <FlashList
          data={messages}
          // FlashList v2 聊天范式：自然序 + 从底部起渲 + 新消息自动跟底
          maintainVisibleContentPosition={{ autoscrollToBottomThreshold: 0.2, startRenderingFromBottom: true }}
          extraData={[pendingOps, pendingLocationText, p.dark, settings.fontScale, uncertainIds, v2, dock, draftUserId, interruptedIds, s2sIds]}
          keyExtractor={(m) => m.id}
          renderItem={({ item }) => (
            <View style={{ paddingHorizontal: 12 }}>
              <MessageBubble
                p={p}
                msg={item}
                confirmActive={confirmActiveOf(item)}
                inlineConfirm={!(v2 && dock)}
                uncertain={uncertainIds.includes(item.id)}
                draft={item.id === draftUserId}
                interrupted={interruptedIds.includes(item.id)}
                s2s={s2sIds.includes(item.id)}
                onConfirm={onConfirm}
                onSend={onSend}
              />
            </View>
          )}
          contentContainerStyle={{ paddingVertical: 10 }}
        />
      )}
      {v2 ? (
        <VoiceSheet
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          turn={turn}
          containerHeight={listHeight}
          draftUserId={draftUserId}
          interruptedIds={interruptedIds}
          s2sNotice={s2sNotice}
          candidates={core.candidates}
          onCollapse={() => setSheetOverride({ turnId: latestTurnId, mode: 'dismissed' })}
          onInterrupt={interruptAndListen}
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
      {/* 状态胶囊：一次只说一件「此刻」的事。**B1 不接 onPress**——它是 26dp +
          accessibilityRole="text"，接了点按就同时违反读屏与热区两条（第 2 批遗留⑧） */}
      {v2 ? (
        <PresenceCapsule
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          onPress={() => setSheetOverride({ turnId: latestTurnId, mode: 'open' })}
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
        orbAnimated={snapshot.input !== 'voice-sheet'}
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
            <AuroraOrb size={30} state={busy ? 'thinking' : 'idle'} animated={busy} />
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
                  minWidth: 40,
                  height: 40,
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
            {!tablet ? <TopIconLink p={p} href="/vehicle" icon="vehicle" label="车辆" /> : null}
            <TopIconLink p={p} href="/settings" icon="settings" label="设置" />
          </View>
          {!v2 && linkWarn ? (
            <View style={{ backgroundColor: p.amberSoft, paddingHorizontal: 14, paddingVertical: 6 }}>
              <Text style={{ color: p.amber, fontSize: p.font(12) }}>
                {connStatus === 'connecting' ? '正在重连服务器…' : '连接已断开，正在重试'}
                ——这期间发出的消息会排队，连上后自动补发
              </Text>
            </View>
          ) : null}
          {tablet ? (
            <View style={{ flex: 1, flexDirection: 'row' }}>
              {chatColumn}
              {/* 平板右舞台（M3-2 三段，Aurora 复刻轮玻璃化）：车况 / 提醒 / 焦点卡。
                  三段都是**已经在会话里的事实的第二个视图**，不额外向后端取数 */}
              <Glass
                p={p}
                r={24}
                style={{
                  width: Math.min(400, Math.round(width * 0.42)),
                  marginVertical: 10,
                  marginRight: 10,
                  overflow: 'hidden',
                }}
              >
                <ScrollView contentContainerStyle={{ padding: 14, gap: 16 }}>
                  <VehicleSection p={p} vehState={vehState} />
                  <ReminderSection p={p} messages={messages} />
                  <View style={{ gap: 6 }}>
                    <Text style={{ color: p.fg3, fontSize: p.font(12) }}>焦点卡</Text>
                    {latestCard ? (
                      <CardRenderer p={p} card={latestCard} onSend={onSend} />
                    ) : (
                      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>本轮还没有卡片</Text>
                    )}
                  </View>
                </ScrollView>
              </Glass>
            </View>
          ) : (
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
