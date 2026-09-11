// 设置页（实施计划 M1-5 + M2 语音两分区；打磨批 B 分级重排）。
// 五组用户设置：通用 / 助手 / 语音 / 隐私 / 账号与连接；工程入口一个不少，全部搬进「开发者选项」
// ——prod 默认隐藏，构建行连点 7 次解锁（判据只在 core/diagnostics.ts::developerOptionsVisible）。
// 持久化 AsyncStorage（settings store）；buildMeta 键集由 settingsMeta.test.ts 钉住。
import { Link } from 'expo-router'
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { Alert, Pressable, ScrollView, Switch, Text, TextInput, View } from 'react-native'
import { useStore } from 'zustand'

import { AGENT_CATALOG } from '@shared/types.ts'

import { formatBuildLabel, readBuildInfo } from '../../core/buildInfo'
import { DEVELOPER_UNLOCK_TAPS, developerOptionsVisible, developmentDiagnosticsEnabled } from '../../core/diagnostics'
import {
  fetchSessionInfo,
  summaryStale,
  type SessionInfoResult,
} from '../../core/api/sessionInfo'
import { loadServerConfig } from '../../core/config/storage'
import type { ServerConfig } from '../../core/config/types'
import { activityLog, type ActivitySource } from '../../core/presence/activityLog'
import { drivingActive, NO_EDGE_DRIVING } from '../../core/presence/drivingMode'
import { MIC_LABEL } from '../../core/presence/presence'
import { clearHistory } from '../../core/session/history'
import { MOBILE_QUICK_COMMAND_ORDER } from '../../core/session/quickCommands'
import { subscribeWiredSession, wiredSessionSnapshot } from '../../core/session/wiredStore'
import { getWired } from '../../core/session/wiring'
import { needsS2sConsent, settingsStore, type AppSettings } from '../../core/settings/store'
import {
  fetchAsrProviders,
  fetchTtsProviders,
  type AsrProviderInfo,
} from '../../core/voice/catalog'
import { handsFreeAvailability } from '../../core/voice/handsFree'
import { speechController } from '../../core/voice/speech'
import { PRESENCE_LANE_DP } from '../../ui/layout/bottomChrome'
import { Pill } from '../../ui/Pill'
import { usePalette, type Palette } from '../../ui/theme'
import { TARGET } from '../../ui/tokens'
import { useAssistant } from '../assistant/AssistantProvider'
import { S2sConsentSheet } from './S2sConsentSheet'
import type { TtsProviderInfo } from '@shared/types.ts'

function Section({ p, title, children }: { p: Palette; title: string; children: ReactNode }) {
  return (
    <View style={{ gap: 8 }}>
      <Text style={{ color: p.fg3, fontSize: p.font(12), fontWeight: '600' }}>{title}</Text>
      <View
        style={{
          backgroundColor: p.card,
          borderColor: p.line,
          borderWidth: 1,
          borderRadius: 14,
          padding: 12,
          gap: 10,
        }}
      >
        {children}
      </View>
    </View>
  )
}

/** 组内小标题（打磨批 B 分级：一组里再分几段，不再是十二个平级分区） */
function SubHead({ p, title }: { p: Palette; title: string }) {
  return (
    <Text style={{ color: p.fg2, fontSize: p.font(13), fontWeight: '600', marginTop: 4 }}>{title}</Text>
  )
}

/** 能力状态的用户可读说明——**唯一的一份**。未知状态原样标未知，不猜。 */
const CAPABILITY_STATUS_LABEL: Record<string, string> = {
  available: '可用',
  unauthorized: '当前账号未授权',
  unavailable: '服务未在线',
  unknown: '状态未知',
}

// 打磨批 B（评审 P17 / D5）：用户话术，不再是运维语言
const AUTHORIZATION_SOURCE_LABEL: Record<string, string> = {
  token: '按访问令牌授权',
  poc_default: '演示模式（未做真实授权）',
  fail_closed: '未授权',
}

function SessionSummarySection({
  p,
  result,
  loading,
  onRefresh,
}: {
  p: Palette
  result: SessionInfoResult | null
  loading: boolean
  onRefresh(): void
}) {
  const line = (text: string, testID?: string) => (
    <Text testID={testID} style={{ color: p.fg2, fontSize: p.font(13) }}>{text}</Text>
  )
  const refresh = (
    <Pressable accessibilityRole="button" onPress={onRefresh} testID="settings-session-refresh" style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center' }}>
      <Text style={{ color: p.accent, fontSize: p.font(13) }}>{loading ? '刷新中…' : '刷新'}</Text>
    </Pressable>
  )
  if (!result) {
    return <>{line(loading ? '正在读取…' : '尚未读取', 'settings-session-idle')}{refresh}</>
  }
  if (result.kind === 'unauthorized') {
    // 服务端**明确**拒绝：这一条才该引导重新配置。
    return (
      <>
        {line('服务端拒绝了当前访问令牌，重新配置后才能继续。', 'settings-session-unauthorized')}
        <Link href="/onboarding" style={{ color: p.accent, fontSize: p.font(14) }}>重新配置</Link>
      </>
    )
  }
  if (result.kind === 'legacy') {
    return <>{line('服务端暂不提供能力列表。', 'settings-session-legacy')}{refresh}</>
  }
  if (result.kind === 'unknown') {
    // 网络/超时：**不说 token 有问题**，只说没取到。
    return (
      <>
        {line(`暂时取不到能力摘要（${result.reason}）。这不代表访问令牌有问题。`, 'settings-session-unknown')}
        {refresh}
      </>
    )
  }
  const s = result.summary
  const stale = summaryStale(s)
  return (
    <>
      {line(
        `账号 ${s.userId || '未知'}${s.vehicleId ? ` · 服务端关联车辆 ${s.vehicleId}` : ''}`,
        'settings-session-identity',
      )}
      {line(AUTHORIZATION_SOURCE_LABEL[s.authorizationSource] ?? `授权来源 ${s.authorizationSource || '未知'}`)}
      {s.summaryStatus === 'partial' ? (
        <Text testID="settings-session-partial" style={{ color: p.fg3, fontSize: p.font(12) }}>
          这份摘要只取到一部分（{s.summaryReason || '原因未知'}），没列出的能力状态未知。
        </Text>
      ) : null}
      {stale ? (
        <Text testID="settings-session-stale" style={{ color: p.fg3, fontSize: p.font(12) }}>
          摘要已过期，点刷新重新读取。
        </Text>
      ) : null}
      <View style={{ gap: 6 }}>
        {s.capabilities.length === 0
          ? line('服务端没有返回任何能力。', 'settings-session-empty')
          : s.capabilities.map((c) => (
            <View key={c.id} testID={`settings-capability-${c.id}`} style={{ flexDirection: 'row', gap: 8 }}>
              <Text style={{ color: p.fg1, fontSize: p.font(13), flex: 1 }}>{c.displayName}</Text>
              <Text style={{ color: c.status === 'available' ? p.fg2 : p.fg3, fontSize: p.font(12) }}>
                {CAPABILITY_STATUS_LABEL[c.status] ?? `未知状态（${c.status}）`}
              </Text>
            </View>
          ))}
      </View>
      {refresh}
    </>
  )
}

function ChoiceRow<T extends string>({
  p,
  label,
  value,
  options,
  onPick,
}: {
  p: Palette
  label: string
  value: T
  options: { v: T; label: string }[]
  onPick(v: T): void
}) {
  return (
    <View style={{ gap: 6 }}>
      <Text style={{ color: p.fg2, fontSize: p.font(13) }}>{label}</Text>
      {/* 2026-09-11 两档制：单选项走 Pill（外框 48、视觉 36），选中态进无障碍 selected——此前 ChoiceRow 不暴露 selected，
          角色回读只能截图（打磨批坑账） */}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {options.map((o) => (
          <Pill
            key={o.v}
            p={p}
            testID={`choice-${String(o.v)}`}
            selected={value === o.v}
            accessibilityLabel={`${label}：${o.label}`}
            paddingHorizontal={12}
            label={o.label}
            onPress={() => onPick(o.v)}
          />
        ))}
      </View>
    </View>
  )
}

function SwitchRow({
  p,
  settingKey,
  label,
  desc,
  value,
  onChange,
}: {
  p: Palette
  /** 开关自己的稳定句柄：`settings-switch-<settingKey>`。**必填**——见下方注释 */
  settingKey: string
  label: string
  desc?: string
  value: boolean
  onChange(v: boolean): void
}) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: p.fg1, fontSize: p.font(14) }}>{label}</Text>
        {desc ? <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{desc}</Text> : null}
      </View>
      {/* testID 必填的理由（AR06 / A06-2，坑账 AR03 那条）：自动化此前按「标签之后第一枚开关」
          配对，而多行 desc 会把开关挤出文字带 ⇒ 点中的是**下一行**那枚；更糟的是回读时读到的
          也是那枚错开关的新值，看起来完全像「设置生效了」。把开关和它改的那个键绑死，
          「建立前提 → 回读」才是同一个对象。accessibilityLabel 让 TalkBack 与截图取证也认得出它。 */}
      <Switch
        testID={'settings-switch-' + settingKey}
        accessibilityLabel={label}
        value={value}
        onValueChange={onChange}
      />
    </View>
  )
}

/** 首页示例的编辑（打磨批 B「助手 · 首页示例」）：移除 / 添加 / 恢复默认顺序。
 *  用户自定义过的列表在水合时原样保留（裁决 J2，判据 mergeStoredSettings）。 */
function QuickCommandsEditor({ p, commands, onChange }: { p: Palette; commands: string[]; onChange(next: string[]): void }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const t = draft.trim()
    if (!t || commands.includes(t)) return
    onChange([...commands, t])
    setDraft('')
  }
  return (
    <View style={{ gap: 8 }}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {commands.map((c) => (
          <Pill
            key={c}
            p={p}
            accessibilityLabel={`移除示例：${c}`}
            textColor={p.fg1}
            paddingHorizontal={12}
            label={`${c}  ×`}
            onPress={() => onChange(commands.filter((x) => x !== c))}
          />
        ))}
      </View>
      <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={add}
          placeholder="添加一条示例"
          placeholderTextColor={p.fg3}
          style={{ flex: 1, borderWidth: 1, borderColor: p.line, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8, color: p.fg1, fontSize: p.font(14) }}
        />
        <Pressable accessibilityRole="button" onPress={add} style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', paddingHorizontal: 12 }}>
          <Text style={{ color: p.accent, fontSize: p.font(13) }}>添加</Text>
        </Pressable>
      </View>
      <Pressable accessibilityRole="button" onPress={() => onChange([...MOBILE_QUICK_COMMAND_ORDER])} style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', alignSelf: 'flex-start' }}>
        <Text style={{ color: p.accent, fontSize: p.font(13) }}>恢复默认示例</Text>
      </Pressable>
    </View>
  )
}

const ACTIVITY_SOURCE_LABEL: Record<ActivitySource, string> = { mic: '麦克风', camera: '摄像头', location: '定位' }

function hhmm(ms: number): string {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 隐私记录（打磨批 B / 评审 D2）：采集状态的**用户版**——隐私栏的那几行 + 最近激活日志。
 *  JSON 版留在 /capture-status（开发者选项）。文案与色调只取 MIC_LABEL（同隐私栏，一份判据）。 */
function PrivacyRecord({ p }: { p: Palette }) {
  const runtime = useAssistant()
  const [, force] = useState(0)
  useEffect(() => activityLog.subscribe(() => force((n) => n + 1)), [])
  const privacy = runtime?.snapshot.privacy
  const entries = activityLog.list().slice(0, 8)
  const row = (k: string, v: string, tone: string = p.fg1) => (
    <View key={k + v} style={{ flexDirection: 'row', gap: 12, paddingVertical: 4 }}>
      <Text style={{ color: p.fg3, fontSize: p.font(12), width: 72 }}>{k}</Text>
      <Text style={{ color: tone, fontSize: p.font(12), flex: 1 }}>{v}</Text>
    </View>
  )
  return (
    <View testID="settings-privacy-record" style={{ gap: 2 }}>
      {privacy ? (
        <>
          {row('麦克风', privacy.micActive ? '开启' : '关')}
          {row('音频处理', MIC_LABEL[privacy.mic].long, MIC_LABEL[privacy.mic].tone === 'amber' ? p.amber : p.fg1)}
          {row('摄像头', privacy.camera === 'singleFrame' ? '正在抓一帧（触发词命中）' : '关')}
          {row('画面上传', privacy.visionUploading ? '单帧上传中' : '无')}
        </>
      ) : (
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>连上座舱后这里会显示麦克风与摄像头此刻的状态</Text>
      )}
      <Text style={{ color: p.fg2, fontSize: p.font(12), marginTop: 6 }}>最近激活（本次启动后，只在本机、不上传）</Text>
      {entries.length ? (
        entries.map((e) => (
          <Text key={`${e.at}-${e.source}`} style={{ color: p.fg3, fontSize: p.font(12) }}>
            {hhmm(e.at)} · {ACTIVITY_SOURCE_LABEL[e.source]} · {e.note}
          </Text>
        ))
      ) : (
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>还没有采集</Text>
      )}
    </View>
  )
}

/** 服务器的显示名：云栈取 FQDN 第一段，其余取主机名——完整地址进「重新配置」页，不在设置页直出 */
function serverName(cfg: ServerConfig): string {
  if (cfg.fqdn) return cfg.fqdn.split('.')[0]
  const m = /^[a-z]+:\/\/([^/:?#]+)/i.exec(cfg.edgeUrl)
  return m?.[1] ?? '已配置'
}

export function SettingsScreen() {
  const { settings, update, toggleAgent } = useStore(settingsStore)
  const p = usePalette(settings)
  // B5-4 缺陷 C：自动进入的行车档要有可发现的退出口（B4 真机：开关灰着、App 在行车档里、3h12m 退不出）。
  // 只搬事实不复制判据（drivingActive 是唯一一份）；订阅法照 native-spike。无 ticker：30s 宽限到点
  // 这一行不会自己消失，下一次 store 变化才重渲（与取证屏同一取舍）。
  const core = getWired()?.core ?? null
  // 会话事实经 useSyncExternalStore 读（wiredStore.ts）：订阅与快照同一份，没有「挂载与订阅
  // 之间的缝」，也不用在 effect 里同步 setState。
  const wiredSession = useSyncExternalStore(subscribeWiredSession, wiredSessionSnapshot)
  const drivingFact = {
    edge: wiredSession?.drivingEdge ?? NO_EDGE_DRIVING,
    dismissedAt: wiredSession?.drivingDismissedAt ?? 0,
  }
  // `Date.now()` 留在渲染期：这里显示的是「此刻自动行车档成不成立」，30s 退出宽限本来就要用
  // 本帧墙钟判；存进 state 会在停表期间留下陈旧读数。设置页没有 ticker，事实随 store 事件重算。
  // eslint-disable-next-line react-hooks/purity -- 见上
  const autoDrivingNow = drivingActive({ manual: false, edge: drivingFact.edge, now: Date.now(), dismissedAt: drivingFact.dismissedAt })
  const autoDriving = !settings.drivingManual && autoDrivingNow
  const [server, setServer] = useState<ServerConfig | null>(null)
  const [nameDraft, setNameDraft] = useState(settings.assistantName)
  const [ttsCatalog, setTtsCatalog] = useState<TtsProviderInfo[]>([])
  const [asrCatalog, setAsrCatalog] = useState<AsrProviderInfo[]>([])
  const [previewing, setPreviewing] = useState(false)
  // AR05 R14：会话身份与能力摘要。null = 还没查过；查询失败/旧服务端各有各的展示，
  // **不把「此刻查不到」显示成「你没有这些能力」**。
  const [session, setSession] = useState<SessionInfoResult | null>(null)
  const [sessionLoading, setSessionLoading] = useState(false)
  // 免唤醒的原生可用性：**在渲染前问一次**。原生缺席时连开关都不渲染——
  // 这不是 UI 洁癖，是坑账 §9.27：原生缺席时崩在原生线程，ErrorBoundary 兜不住整屏红屏。
  const hfAvail = useMemo(() => handsFreeAvailability(), [])
  // 构建身份一行（常驻包流程）：报问题先抄它——设备跑的是哪份代码只认这一处读数
  const build = useMemo(() => readBuildInfo(), [])
  const buildLabel = useMemo(() => formatBuildLabel(build), [build])
  // 开发者选项显隐——判据只在 core/diagnostics.ts；这里只读结果 + 数点击
  const developerVisible = developerOptionsVisible(settings, build)
  // 点数记在 ref、提示用 state：连点的回调可能是同一份闭包（快速连点时渲染跟不上），读 state 会停在 1
  const tapsRef = useRef(0)
  const [buildTaps, setBuildTaps] = useState(0)
  // 试听没出声时的那句话（M3 遗留 R1）。空串=没试过或出声了。
  // **必须有这个出口**：无 key 引擎在全链四段里没有任何一段会换引擎，结果就是完全安静，
  // 而屏上此前一个字都不说——用户只能对着一台安静的手机猜是不是自己音量关了。
  const [previewMsg, setPreviewMsg] = useState('')
  // 首次切端到端的一次性显式同意（方案 §5.2.2；红线三条件②的持久化证据）
  const [consentOpen, setConsentOpen] = useState(false)

  useEffect(() => {
    void loadServerConfig().then(setServer)
  }, [])
  // 渲染期调整派生状态而不是 effect：设置在别处被改时，草稿要在**同一帧**跟上，
  // 否则会闪一帧旧名字（React 官方 "adjusting state when a prop changes"）。
  const [nameSeen, setNameSeen] = useState(settings.assistantName)
  if (nameSeen !== settings.assistantName) {
    setNameSeen(settings.assistantName)
    setNameDraft(settings.assistantName)
  }
  // 目录探测：两个端点都可能失败，catalog.ts 里各自回落静态表（不留空白设置页）
  useEffect(() => {
    if (!server?.audioUrl) return
    void fetchTtsProviders(server.audioUrl).then(setTtsCatalog)
    void fetchAsrProviders(server.audioUrl).then(setAsrCatalog)
  }, [server?.audioUrl])

  const refreshSession = useMemo(
    () => async (cfg: ServerConfig | null) => {
      if (!cfg) return
      setSessionLoading(true)
      try {
        setSession(await fetchSessionInfo(cfg.edgeUrl, cfg.token))
      } finally {
        setSessionLoading(false)
      }
    },
    [],
  )
  // 换服务器时「正在查」要在**同一帧**亮起来（渲染期调整派生状态），否则会闪一帧
  // 「已经换了服务器、却还显示上一个账号的能力摘要」——AR05 明写旧身份不得留在新会话上。
  const [serverSeen, setServerSeen] = useState(server)
  if (serverSeen !== server) {
    setServerSeen(server)
    setSession(null)
    setSessionLoading(!!server)
  }
  useEffect(() => {
    if (!server) return
    let alive = true
    void (async () => {
      const r = await fetchSessionInfo(server.edgeUrl, server.token)
      if (!alive) return
      setSession(r)
      setSessionLoading(false)
    })()
    return () => {
      alive = false
    }
  }, [server])

  const ttsEngine = ttsCatalog.find((e) => e.id === settings.ttsProvider) ?? ttsCatalog[0]
  const asrEngine = asrCatalog.find((e) => e.id === settings.asrProvider)

  const set = (patch: Partial<AppSettings>) => update(patch)

  // 解锁 = 构建行连点 DEVELOPER_UNLOCK_TAPS 次（打磨批 B）。已经可见时点它什么也不做
  const onBuildTap = () => {
    if (developerVisible) return
    tapsRef.current += 1
    if (tapsRef.current >= DEVELOPER_UNLOCK_TAPS) {
      tapsRef.current = 0
      setBuildTaps(0)
      set({ developerUnlocked: true })
    } else setBuildTaps(tapsRef.current)
  }
  const connLabel =
    wiredSession?.connStatus === 'open' ? '已连接' : wiredSession?.connStatus === 'connecting' ? '正在连接' : '未连接'
  const note = (text: string, tone: string = p.fg3) => (
    <Text style={{ color: tone, fontSize: p.font(11), lineHeight: p.font(17) }}>{text}</Text>
  )
  const link = (href: '/state-gallery' | '/card-gallery' | '/presence-trail' | '/turn-timeline' | '/native-spike' | '/blur-spike' | '/capture-status' | '/debug' | '/voice-spike', label: string) => (
    <Link href={href} style={{ color: p.accent, fontSize: p.font(14), paddingVertical: 6 }}>
      {label}
    </Link>
  )

  return (
    // Modal 与 ScrollView 并列：同意页要盖住整屏，塞进 ScrollView 里会跟着滚
    <View style={{ flex: 1, backgroundColor: p.bg }}>
    <ScrollView style={{ backgroundColor: p.bg }} contentContainerStyle={{ padding: 14, paddingBottom: 14 + PRESENCE_LANE_DP, gap: 16 }}>
      {/* ── 通用 ── */}
      <Section p={p} title="通用">
        <ChoiceRow
          p={p}
          label="主题"
          value={settings.theme}
          options={[
            { v: 'system', label: '跟随系统' },
            { v: 'dark', label: '深色' },
            { v: 'light', label: '浅色' },
          ]}
          onPick={(theme) => set({ theme })}
        />
        <ChoiceRow
          p={p}
          label="字号"
          value={settings.fontScale}
          options={[
            { v: 'normal', label: '标准' },
            { v: 'large', label: '大字' },
          ]}
          onPick={(fontScale) => set({ fontScale })}
        />
        <SwitchRow
          p={p}
          settingKey="keepAwake"
          label="保持屏幕常亮"
          desc="车载支架上看行程/路线卡时不熄屏（耗电，默认关）"
          value={settings.keepAwake}
          onChange={(keepAwake) => set({ keepAwake })}
        />
        <SubHead p={p} title="无障碍" />
        <SwitchRow
          p={p}
          settingKey="reduceMotionForce"
          label="减少动效"
          desc="光球、光标、思考点全部静止（系统「移除动画」开着时自动生效）"
          value={settings.reduceMotionForce}
          onChange={(reduceMotionForce) => set({ reduceMotionForce })}
        />
        <SwitchRow
          p={p}
          settingKey="reduceTransparency"
          label="减少透明度"
          desc="语音层不再对背景做模糊，改用不透明底"
          value={settings.reduceTransparency}
          onChange={(reduceTransparency) => set({ reduceTransparency })}
        />
      </Section>

      {/* ── 助手 ── */}
      <Section p={p} title="助手">
        <View style={{ gap: 6 }}>
          <Text style={{ color: p.fg2, fontSize: p.font(13) }}>昵称（下一轮起生效）</Text>
          <TextInput
            value={nameDraft}
            onChangeText={setNameDraft}
            onEndEditing={() => {
              const v = nameDraft.trim()
              if (v) set({ assistantName: v })
            }}
            style={{
              borderWidth: 1,
              borderColor: p.line,
              borderRadius: 10,
              paddingHorizontal: 12,
              paddingVertical: 8,
              color: p.fg1,
              fontSize: p.font(14),
            }}
          />
        </View>
        <ChoiceRow
          p={p}
          label="回答长度"
          value={settings.answerLength}
          options={[
            { v: 'short', label: '简短' },
            { v: 'standard', label: '标准' },
            { v: 'detailed', label: '详细' },
          ]}
          onPick={(answerLength) => set({ answerLength })}
        />
        <ChoiceRow
          p={p}
          label="模型偏好"
          value={settings.model}
          options={[
            { v: 'auto', label: '自动' },
            { v: 'fast', label: '快' },
            { v: 'deep', label: '深' },
          ]}
          onPick={(model) => set({ model })}
        />
        <SubHead p={p} title="首页示例" />
        {note('首页与欢迎态显示的示例指令；没有对应能力的会自动隐藏')}
        <QuickCommandsEditor p={p} commands={settings.quickCommands} onChange={(quickCommands) => set({ quickCommands })} />
      </Section>

      {/* ── 语音 ── */}
      <Section p={p} title="语音">
        <SubHead p={p} title="语音输入" />
        <ChoiceRow
          p={p}
          label="识别引擎"
          value={settings.asrProvider}
          options={[
            ...asrCatalog.map((e) => ({
              v: e.id,
              label: e.available ? e.label : e.label + '（未配置）',
            })),
            { v: 'off', label: '不用流式（整段识别）' },
          ]}
          onPick={(asrProvider) => {
            // 换引擎要同时换模型：模型 id 是跟着引擎走的，留着上一个引擎的 model
            // 会让 start 帧带一个该引擎不认识的名字（这类错误只表现为连不上）
            const next = asrCatalog.find((e) => e.id === asrProvider)
            set({ asrProvider, ...(next?.models?.[0] ? { asrModel: next.models[0] } : {}) })
          }}
        />
        {asrEngine && asrEngine.models.length > 1 ? (
          <ChoiceRow
            p={p}
            label="识别模型"
            value={settings.asrModel}
            options={asrEngine.models.map((m) => ({ v: m, label: m }))}
            onPick={(asrModel) => set({ asrModel })}
          />
        ) : null}
        <ChoiceRow
          p={p}
          label="语言"
          value={settings.asrLanguage}
          options={[
            { v: 'zh', label: '中文' },
            { v: 'en', label: 'English' },
          ]}
          onPick={(asrLanguage) => set({ asrLanguage })}
        />

        <SubHead p={p} title="播报" />
        <ChoiceRow
          p={p}
          label="播报"
          value={settings.speakPolicy}
          options={[
            { v: 'auto' as const, label: '自动' },
            { v: 'always' as const, label: '总是' },
            { v: 'silent' as const, label: '静音' },
          ]}
          onPick={(speakPolicy) => {
            set({ speakPolicy })
            if (speakPolicy === 'silent') speechController().stop() // 关掉要立刻停当前这段
          }}
        />
        {note('自动：用语音问才播报，打字只显示文字（默认）。总是：打字也播报。静音：完全不出声（试听仍可用）。')}
        <SwitchRow
          p={p}
          settingKey="hapticsEnabled"
          label="触感"
          desc="唤醒、需要确认、出错、拍一张时轻微振动。默认开"
          value={settings.hapticsEnabled}
          onChange={(hapticsEnabled) => set({ hapticsEnabled })}
        />
        <SwitchRow
          p={p}
          settingKey="cueToneEnabled"
          label="提示音"
          desc="唤醒命中、需要你确认时的两音提示。默认开；行车档强制开"
          value={settings.cueToneEnabled}
          onChange={(cueToneEnabled) => set({ cueToneEnabled })}
        />
        {ttsCatalog.length ? (
          <ChoiceRow
            p={p}
            label="引擎"
            value={settings.ttsProvider}
            options={ttsCatalog.map((e) => ({
              v: e.id,
              label: e.available ? e.label : e.label + '（未配置）',
            }))}
            onPick={(ttsProvider) => {
              const next = ttsCatalog.find((e) => e.id === ttsProvider)
              const first = next?.voices?.[0]?.voice_id
              set({ ttsProvider, ...(first ? { voiceId: first } : {}) })
            }}
          />
        ) : null}
        {ttsEngine?.voices?.length ? (
          <ChoiceRow
            p={p}
            label="音色"
            value={settings.voiceId}
            options={ttsEngine.voices.map((v) => ({
              v: v.voice_id,
              label: v.name + (v.gender === 'male' ? '·男' : v.gender === 'female' ? '·女' : ''),
            }))}
            onPick={(voiceId) => set({ voiceId })}
          />
        ) : null}
        <Pill
          p={p}
          tone="accent"
          testID="voice-preview"
          disabled={previewing || !server?.audioUrl}
          label={previewing ? '播放中…' : '试听'}
          onPress={() => {
            setPreviewing(true)
            setPreviewMsg('')
            void speechController(server?.audioUrl)
              .preview('你好，我是' + settings.assistantName + '，这是当前音色的效果。')
              .then((sounded) => {
                // 打磨批 B（评审 P17 / D5）：用户话术；引擎 key 是不是配了属于服务端排障，不在这里说
                if (!sounded) setPreviewMsg('这个音色暂时不可用，换一个试试。')
              })
              .finally(() => setPreviewing(false))
          }}
        />
        {previewMsg ? (
          <Text style={{ color: p.amber, fontSize: p.font(12), lineHeight: p.font(18) }}>
            {previewMsg}
          </Text>
        ) : null}

        {/* 免唤醒与端到端：开关**默认全关/最保守**，且每条都在屏上说清代价——
            视觉与 S2S 是架构红线里点名要「文案说清差异」的两条
            （CLAUDE.md §5「唯一的受控例外」条件③、「视觉单帧同款三条件」第三条）。 */}
        <SubHead p={p} title="免唤醒与端到端" />
        {!hfAvail.usable ? (
          <>
            {note('这个安装包里没有端侧语音引擎，免唤醒不可用；装上带语音引擎的版本后这里会自动出现。')}
            {developerVisible ? note(`VAD=${String(hfAvail.vad)} / 唤醒词=${String(hfAvail.kws)}`) : null}
          </>
        ) : (
          <>
            <SwitchRow
              p={p}
              settingKey="handsFree"
              label="免唤醒对话"
              desc="开启后麦克风常开：说唤醒词即可开始，答完 8 秒内可直接接着说。耗电，默认关"
              value={settings.handsFree}
              onChange={(handsFree) => set({ handsFree })}
            />
            {settings.handsFree ? (
              <SwitchRow
                p={p}
                settingKey="wakeWordEnabled"
                label="唤醒词「小舟小舟」"
                desc={
                  hfAvail.kws
                    ? '关掉后不常驻监听唤醒词，只保留「答完 8 秒内可接着说」'
                    : '本安装包没有唤醒词引擎，只能用「答完接着说」'
                }
                value={settings.wakeWord && hfAvail.kws}
                onChange={(wakeWord) => set({ wakeWord })}
              />
            ) : null}
            <ChoiceRow
              p={p}
              label="语音链路"
              value={settings.voicePipeline}
              options={[
                { v: 'classic' as const, label: '三段式（默认）' },
                { v: 's2s' as const, label: '端到端' },
              ]}
              onPick={(voicePipeline) => {
                // 首次切端到端弹一次性显式同意（方案 §5.2.2）；同意过的直接切；切回三段式永远不问
                if (voicePipeline === 's2s' && needsS2sConsent(settings)) setConsentOpen(true)
                else set({ voicePipeline })
              }}
            />
            {note('三段式：收音时上传音频到语音识别服务，识别完成后将文字交给助手。端到端：仅在唤醒后的对话窗内上传原始音频；免唤醒待机时麦克风仍在本机监听，不上传音频。默认三段式。')}
            {settings.voicePipeline === 's2s'
              ? note('已选端到端：本机麦克风的原始音频会在每次唤醒后的对话窗内上传。', p.amber)
              : null}
            {settings.s2sConsentAt > 0
              ? note(`已于 ${new Date(settings.s2sConsentAt).toLocaleString('zh-CN', { hour12: false })} 同意端到端上传原始音频`)
              : null}
          </>
        )}
      </Section>

      {/* ── 隐私 ── */}
      <Section p={p} title="隐私">
        <SwitchRow
          p={p}
          settingKey="memoryEnabled"
          label="记忆"
          desc="关闭后本会话不再抽取/使用长期记忆"
          value={settings.memoryEnabled}
          onChange={(memoryEnabled) => set({ memoryEnabled })}
        />
        <SwitchRow
          p={p}
          settingKey="locationEnabled"
          label="使用定位"
          desc="仅在位置相关请求时取当前坐标，坐标不持久化"
          value={settings.locationEnabled}
          onChange={(locationEnabled) => set({ locationEnabled })}
        />
        <SwitchRow
          p={p}
          settingKey="visionEnabled"
          label="看图问答"
          desc="只有当你说「这是什么」这类看图的话时才拍一张，其余时候一帧都不拍。默认关"
          value={settings.visionEnabled}
          onChange={(visionEnabled) => set({ visionEnabled })}
        />
        {settings.visionEnabled
          ? note('拍到的画面只用于回答当前这一句，服务器上最多保留两分钟，不落盘、不进记忆、不进日志。手机上用的是后置摄像头（代替车外摄像头，卡片上会标「模拟」）。')
          : null}
        <SubHead p={p} title="隐私记录" />
        <PrivacyRecord p={p} />
        {/* 打磨批 F（裁决 J3）：记录本机持久化后要有清除口；二次确认，清当前会话与存量。挂起台账是服务端的账，不动 */}
        <Pressable
          testID="settings-clear-history"
          accessibilityRole="button"
          onPress={() =>
            Alert.alert('清除对话记录', '会同时清空当前会话与本机保存的记录，不可恢复。', [
              { text: '取消', style: 'cancel' },
              {
                text: '清除',
                style: 'destructive',
                onPress: () => {
                  const w = getWired()
                  w?.core.clearMessages()
                  if (w) void clearHistory(w.historyKey)
                },
              },
            ])
          }
          style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', alignSelf: 'flex-start' }}
        >
          <Text style={{ color: p.red, fontSize: p.font(13) }}>清除对话记录</Text>
        </Pressable>
      </Section>

      {/* ── 账号与连接 ── */}
      <Section p={p} title="账号与连接">
        {/* 打磨批 B（评审 P17 / D5）：不再直出 URL 与令牌尾巴——完整地址在「重新配置」页 */}
        <Text testID="settings-server" style={{ color: p.fg1, fontSize: p.font(14) }}>
          {server ? `${connLabel} · ${serverName(server)}` : '尚未配置服务器'}
        </Text>
        {server ? (
          // 身份取**服务端事实**；查不到就说没确认，不拿凭证尾巴冒充身份（AR05 F06）
          <Text testID="settings-identity" style={{ color: p.fg3, fontSize: p.font(12) }}>
            {session?.kind === 'ok' && session.summary.userId
              ? `账号 ${session.summary.userId}${session.summary.vehicleId ? ` · 关联车辆 ${session.summary.vehicleId}` : ''}`
              : '账号未确认 · 访问令牌已保存'}
          </Text>
        ) : null}
        {/* 账号与能力（AR05 R14）：**服务端摘要**，不是客户端推断。三种「不能用」分开说——
            没授权 / 服务不在线 / 取不到；取不到就说取不到，不冒充「可用」也不冒充「没有」。 */}
        <SubHead p={p} title="能力（服务端）" />
        <SessionSummarySection
          p={p}
          result={session}
          loading={sessionLoading}
          onRefresh={() => void refreshSession(server)}
        />
        <SubHead p={p} title="能力开关（关掉的指令会被婉拒）" />
        {AGENT_CATALOG.map((a) => (
          <SwitchRow
            key={a.id}
            p={p}
            settingKey={'agent-' + a.id}
            label={a.label}
            desc={a.desc}
            value={settings.agents[a.id] !== false}
            onChange={() => toggleAgent(a.id)}
          />
        ))}
        {/* 身份与行车（B4-10 / 方案 §6.0 / AR05 R14）：**设备角色只决定布局**，
            窗口尺寸不决定权限、横屏不决定你是驾驶员、平板不自动获得车控。
            能不能控车由上面那份摘要如实说，这里只说布局。 */}
        <SubHead p={p} title="设备角色与行车档" />
        <Text testID="settings-device-role-note" style={{ color: p.fg2, fontSize: p.font(13) }}>
          {settings.deviceRole === 'handheld'
            ? '手持陪伴端 · 只决定布局'
            : settings.deviceRole === 'mount'
              ? '支架 · 副驾协同 · 只决定布局'
              : '可信车载平板 · 只决定布局'}
        </Text>
        {note('选哪个角色都不会多出任何权限；能做什么看上面那份能力列表。')}
        <ChoiceRow
          p={p}
          label="设备角色"
          value={settings.deviceRole}
          options={[
            { v: 'handheld' as const, label: '手持' },
            { v: 'mount' as const, label: '支架 / 副驾' },
            { v: 'trusted-tablet' as const, label: '可信车载平板' },
          ]}
          onPick={(deviceRole) => set({ deviceRole })}
        />
        <SwitchRow
          p={p}
          settingKey="drivingManual"
          label="行车档"
          desc="目标放大、过程区单行、文本输入按角色收起。座舱判定行车时自动进入（每轮回答后判定），停车 30 秒后自动退出"
          value={settings.drivingManual}
          onChange={(drivingManual) => set({ drivingManual })}
        />
        {autoDriving ? (
          <Pressable
            testID="driving-auto-exit"
            accessibilityRole="button"
            accessibilityLabel="自动行车中，退出行车档"
            onPress={() => core?.dismissDriving()}
            style={{
              minHeight: p.target(TARGET.parked),
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingHorizontal: 12,
              borderRadius: 10,
              backgroundColor: p.accentSoft,
            }}
          >
            <Text style={{ color: p.fg2, fontSize: p.font(13) }}>自动行车中 · 座舱判定为行驶</Text>
            <Text style={{ color: p.accent, fontSize: p.font(13), fontWeight: '600' }}>退出</Text>
          </Pressable>
        ) : null}
        <Link href="/onboarding" style={{ color: p.accent, fontSize: p.font(14), paddingVertical: 6 }}>
          重新配置连接（保存后回对话页自动重连）
        </Link>
      </Section>

      {/* ── 开发者选项（prod 默认隐藏；判据 developerOptionsVisible）── */}
      {developerVisible ? (
        <Section p={p} title="开发者选项">
          {note('只读的取证屏与画廊：不采集、不播放、不发送。报问题时把底部的构建行一起抄上。')}
          {link('/state-gallery', '状态画廊')}
          {link('/card-gallery', '卡片画廊')}
          {link('/presence-trail', '在场轨迹')}
          {link('/turn-timeline', '轮次时间线')}
          {link('/native-spike', developmentDiagnosticsEnabled() ? '原生状态与触感测试' : '原生状态')}
          {link('/blur-spike', '材质对照')}
          {link('/capture-status', '采集状态（JSON）')}
          {developmentDiagnosticsEnabled() ? (
            <>
              <SubHead p={p} title="操作诊断（只在 dev 包；会采集、播放或发送）" />
              {link('/debug', '主链发送与回放探针')}
              {link('/voice-spike', '语音采集与播放探针')}
            </>
          ) : null}
          {build.variant === 'prod' ? (
            <Pressable
              testID="developer-hide"
              accessibilityRole="button"
              onPress={() => set({ developerUnlocked: false })}
              style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', alignSelf: 'flex-start' }}
            >
              <Text style={{ color: p.fg2, fontSize: p.font(13) }}>隐藏开发者选项</Text>
            </Pressable>
          ) : null}
        </Section>
      ) : null}

      {/* 构建行：报问题先抄它。连点 DEVELOPER_UNLOCK_TAPS 次解锁开发者选项 */}
      <Pressable testID="build-label-tap" onPress={onBuildTap} style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center' }}>
        <Text
          selectable
          testID="build-label"
          style={{ color: p.fg3, fontSize: p.font(11), textAlign: 'center' }}
        >
          {buildLabel}
        </Text>
        {!developerVisible && buildTaps >= 3 ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11), textAlign: 'center' }}>
            再点 {DEVELOPER_UNLOCK_TAPS - buildTaps} 次进入开发者选项
          </Text>
        ) : null}
      </Pressable>
    </ScrollView>
      <S2sConsentSheet
        p={p}
        fontScale={settings.fontScale}
        visible={consentOpen}
        onAccept={() => {
          set({ voicePipeline: 's2s', s2sConsentAt: Date.now() })
          setConsentOpen(false)
        }}
        onDecline={() => setConsentOpen(false)}
      />
    </View>
  )
}
