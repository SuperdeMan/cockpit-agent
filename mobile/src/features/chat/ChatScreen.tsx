// 对话主屏（M1-3/M1-6/M1-7 装配，Aurora Glass 复刻轮重皮）：
//  - GatewaySession（下行帧→SessionCore.handleFrame，状态→connStatus）
//  - 双形态外壳：窗口短边 ≥600dp 平板双栏（右=玻璃舞台：车况+提醒+焦点卡），旋转即时切
//  - 确认条按台账渲染（isPendingLive），位置征询条只激活最新一条
//  - 视觉照 hmi shell.css：深空渐变+极光 blob 打底，顶栏=品牌光球+连接 pill，空对话=欢迎态大光球
// 改服务器配置 → 回本屏时按 edgeUrl+token 判变 → 断开重连（M1-5 服务器分区语义）。
import { FlashList } from '@shopify/flash-list'
import { Link, Redirect, useFocusEffect } from 'expo-router'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, View, useWindowDimensions } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { isPendingLive } from '@shared/pendingOps.mjs'
import type { Msg } from '@shared/types.ts'

import { loadServerConfig } from '../../core/config/storage'
import type { ServerConfig } from '../../core/config/types'
import { ensureWired, type Wired } from '../../core/session/wiring'
import { settingsStore } from '../../core/settings/store'
import { AuroraBackground, AuroraOrb, Glass } from '../../ui/aurora'
import { Icon, iconRuntimeAvailable, type IconName } from '../../ui/Icon'
import { usePalette } from '../../ui/theme'
import { CardRenderer } from '../cards/CardRenderer'
import { ReminderSection } from '../vehicle/ReminderSection'
import { VehicleSection } from '../vehicle/VehiclePanel'
import { captureVisionFrame, needsVisionFrame } from '../../core/vision/frame'
import { Composer } from './Composer'
import { MessageBubble } from './MessageBubble'
import { useHandsFree } from './useHandsFree'
import { usePtt } from './usePtt'

// 免唤醒 FSM 态 → 用户看得懂的一行字与一个点的颜色。
// **不直接显示 FSM 名字**：ARMED/FOLLOWUP 对用户没有意义，而「在不在听」有。
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
  const { messages, pendingOps, vehState, connStatus, pendingLocationText } = useStore(core.store)
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
    (text: string, metaExtra?: Record<string, string>) => {
      const visionDone = metaExtra ? 'vision_frame_id' in metaExtra : false
      if (settings.visionEnabled && !visionDone && needsVisionFrame(text)) {
        void captureVisionFrame(cfg.audioUrl).then((fid) =>
          core.send(text, { ...(metaExtra || {}), vision_frame_id: fid }),
        )
        return
      }
      core.send(text, metaExtra)
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
    onFinal: (text) => onSend(text),
  })
  // 免唤醒（M4-4）。**定稿走的是同一个 onSend**——前置路由/位置闸/候选拦截/视觉抓帧
  // 一条都不能因为「这句是免唤醒说出来的」而绕过。
  const hf = useHandsFree({
    audioUrl: cfg.audioUrl,
    sessionId: wired.session.sessionId,
    enabled: settings.handsFree,
    needConfirm: pendingOps.length > 0,
    onSend: (text) => onSend(text),
    onNotice: setNotice,
    onCancelTurn: () => core.cancelCurrentTurn(),
  })

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

  const chatColumn = (
    <View style={{ flex: 1 }}>
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
          extraData={[pendingOps, pendingLocationText, p.dark, settings.fontScale]}
          keyExtractor={(m) => m.id}
          renderItem={({ item }) => (
            <View style={{ paddingHorizontal: 12 }}>
              <MessageBubble
                p={p}
                msg={item}
                confirmActive={confirmActiveOf(item)}
                onConfirm={onConfirm}
                onSend={onSend}
              />
            </View>
          )}
          contentContainerStyle={{ paddingVertical: 10 }}
        />
      )}
      {/* 免唤醒状态条（M4-4）。**只在真开着的时候占高度**——一个常驻的空条会让
          「现在到底在不在听」这件事变得看不出来，而这正是常开麦最该让用户看见的事。
          文案给的是 FSM 态的人话版，不是 FSM 名字：用户不需要知道 ARMED 是什么。 */}
      {settings.handsFree && hf.availability.usable ? (
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
      {hf.error || notice ? (
        <View style={{ backgroundColor: p.amberSoft, paddingHorizontal: 14, paddingVertical: 6 }}>
          <Text style={{ color: p.amber, fontSize: p.font(12) }}>{hf.error || notice}</Text>
        </View>
      ) : null}
      <Composer
        p={p}
        quickCommands={settings.quickCommands}
        busy={busy}
        ptt={cfg.audioUrl ? ptt : null}
        onSend={onSend}
        onInterrupt={onInterrupt}
      />
    </View>
  )

  return (
    // top 边必须显式包含：真机顶栏会顶进系统状态栏（M1-8 首轮实测，手机态露头的第一个 bug）
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <SafeAreaView style={{ flex: 1 }} edges={['top', 'bottom']}>
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        >
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
            {/* 连接 pill（hmi .au-conn 同款）：点 + 短字 */}
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
            <View style={{ flex: 1 }} />
            {!tablet ? <TopIconLink p={p} href="/vehicle" icon="vehicle" label="车辆" /> : null}
            <TopIconLink p={p} href="/settings" icon="settings" label="设置" />
          </View>
          {linkWarn ? (
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
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  )
}
