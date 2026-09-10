// 设置页（实施计划 M1-5 + M2 语音两分区）：分区=服务器（M0-5 复用，改配置回对话屏断开
// 重连）/ 显示 / 助手 / **语音输入** / **语音播报** / 能力开关（AGENT_CATALOG 全列，
// disabled_agents 生效）/ 记忆 / 定位 / 调试入口。
// 持久化 AsyncStorage（settings store）；buildMeta 键集由 settingsMeta.test.ts 钉住。
import { Link } from 'expo-router'
import { useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react'
import { Pressable, ScrollView, Switch, Text, TextInput, View } from 'react-native'
import { useStore } from 'zustand'

import { AGENT_CATALOG } from '@shared/types.ts'

import { formatBuildLabel, readBuildInfo } from '../../core/buildInfo'
import { developmentDiagnosticsEnabled } from '../../core/diagnostics'
import {
  fetchSessionInfo,
  summaryStale,
  type SessionInfoResult,
} from '../../core/api/sessionInfo'
import { loadServerConfig } from '../../core/config/storage'
import type { ServerConfig } from '../../core/config/types'
import { drivingActive, NO_EDGE_DRIVING } from '../../core/presence/drivingMode'
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
import { usePalette, type Palette } from '../../ui/theme'
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

/** 能力状态的用户可读说明——**唯一的一份**。未知状态原样标未知，不猜。 */
const CAPABILITY_STATUS_LABEL: Record<string, string> = {
  available: '可用',
  unauthorized: '当前账号未授权',
  unavailable: '服务未在线',
  unknown: '状态未知',
}

const AUTHORIZATION_SOURCE_LABEL: Record<string, string> = {
  token: '按 token 授权',
  poc_default: 'PoC 默认放行（不是真实授权）',
  fail_closed: '无授权（fail-closed）',
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
    <Pressable accessibilityRole="button" onPress={onRefresh} testID="settings-session-refresh">
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
        {line('服务端拒绝了当前 token，重新配置后才能继续。', 'settings-session-unauthorized')}
        <Link href="/onboarding" style={{ color: p.accent, fontSize: p.font(14) }}>重新配置</Link>
      </>
    )
  }
  if (result.kind === 'legacy') {
    return <>{line('当前服务端还不支持能力摘要（旧版本）。', 'settings-session-legacy')}{refresh}</>
  }
  if (result.kind === 'unknown') {
    // 网络/超时：**不说 token 有问题**，只说没取到。
    return (
      <>
        {line(`暂时取不到能力摘要（${result.reason}）。这不代表 token 有问题。`, 'settings-session-unknown')}
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
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {options.map((o) => (
          <Pressable
            key={o.v}
            onPress={() => onPick(o.v)}
            style={{
              backgroundColor: value === o.v ? p.accentSoft : 'transparent',
              borderWidth: 1,
              borderColor: value === o.v ? p.accent : p.line,
              borderRadius: 999,
              paddingHorizontal: 12,
              paddingVertical: 6,
            }}
          >
            <Text style={{ color: value === o.v ? p.accent : p.fg2, fontSize: p.font(13) }}>
              {o.label}
            </Text>
          </Pressable>
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
  const buildLabel = useMemo(() => formatBuildLabel(readBuildInfo()), [])
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

  return (
    // Modal 与 ScrollView 并列：同意页要盖住整屏，塞进 ScrollView 里会跟着滚
    <View style={{ flex: 1, backgroundColor: p.bg }}>
    <ScrollView style={{ backgroundColor: p.bg }} contentContainerStyle={{ padding: 14, paddingBottom: 14 + PRESENCE_LANE_DP, gap: 16 }}>
      <Section p={p} title="服务器">
        <Text style={{ color: p.fg2, fontSize: p.font(13) }}>
          {server ? server.edgeUrl : '未配置'}
        </Text>
        {server ? (
          // 身份取**服务端事实**；查不到才回落 token 尾 4 位，并说清这是回落
          // ——「token ····ab12」从来不是用户是谁，它只是一段凭证的尾巴（AR05 F06）。
          <Text testID="settings-identity" style={{ color: p.fg3, fontSize: p.font(12) }}>
            {session?.kind === 'ok' && session.summary.userId
              ? `账号 ${session.summary.userId}${session.summary.vehicleId ? ` · 关联车辆 ${session.summary.vehicleId}` : ''}`
              : `账号未知（token ····${server.token.slice(-4)}）`}
          </Text>
        ) : null}
        <Link href="/onboarding" style={{ color: p.accent, fontSize: p.font(14) }}>
          重新配置（保存后回对话页自动重连）
        </Link>
      </Section>

      {/* 身份与行车（B4-10 / 方案 §6.0 / AR05 R14）：**设备角色只决定布局**，
          窗口尺寸不决定权限、横屏不决定你是驾驶员、平板不自动获得车控。
          AR05 之前 App 判不了 token 的 scope（不透明串、无查询端点），只好拿角色推
          「不控车」——那是**客户端编的权限结论**。现在有 `GET /api/session` 了：
          能不能控车由上面那份摘要如实说，这里只说布局。 */}
      <Section p={p} title="身份与行车">
        <Text testID="settings-device-role-note" style={{ color: p.fg2, fontSize: p.font(13) }}>
          {settings.deviceRole === 'handheld'
            ? '手持陪伴端 · 只决定布局'
            : settings.deviceRole === 'mount'
              ? '支架 · 副驾协同 · 只决定布局'
              : '可信车载平板 · 只决定布局'}
        </Text>
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>
          选哪个角色都不会多出任何权限；能做什么看「账号与能力（服务端）」那份摘要。
        </Text>
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
              minHeight: 48,
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
      </Section>

      <Section p={p} title="显示">
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
      </Section>

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
      </Section>

      <Section p={p} title="语音输入（按住麦克风说话）">
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
      </Section>

      <Section p={p} title="语音播报">
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
        <Text style={{ color: p.fg3, fontSize: p.font(11), lineHeight: p.font(17) }}>
          自动：用语音问才播报，打字只显示文字（默认）。总是：打字也播报。静音：完全不出声（试听仍可用）。
        </Text>
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
          desc="唤醒命中、需要你确认时的两音提示（合成，不用音频文件）。默认开；行车档强制开"
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
        <Pressable
          disabled={previewing || !server?.audioUrl}
          onPress={() => {
            setPreviewing(true)
            setPreviewMsg('')
            void speechController(server?.audioUrl)
              .preview('你好，我是' + settings.assistantName + '，这是当前音色的效果。')
              .then((sounded) => {
                if (!sounded) setPreviewMsg(`没有出声：${settings.ttsProvider} 这个引擎没返回音频（后端没配它的 key，或 key 已失效）。换一个引擎再试。`)
              })
              .finally(() => setPreviewing(false))
          }}
          style={{
            alignSelf: 'flex-start',
            borderWidth: 1,
            borderColor: p.accent,
            borderRadius: 999,
            paddingHorizontal: 14,
            paddingVertical: 7,
            opacity: previewing ? 0.5 : 1,
          }}
        >
          <Text style={{ color: p.accent, fontSize: p.font(13) }}>
            {previewing ? '播放中…' : '试听'}
          </Text>
        </Pressable>
        {previewMsg ? (
          <Text style={{ color: p.amber, fontSize: p.font(12), marginTop: 8, lineHeight: p.font(18) }}>
            {previewMsg}
          </Text>
        ) : null}
      </Section>

      {/* M4 进阶语音。三个开关**默认全关/最保守**，且每条都在屏上说清代价——
          这不是文案洁癖：视觉与 S2S 是架构红线里点名要「文案说清差异」的两条
          （CLAUDE.md §5「唯一的受控例外」条件③、「视觉单帧同款三条件」第三条）。 */}
      <Section p={p} title="进阶语音（M4）">
        {!hfAvail.usable ? (
          <Text style={{ color: p.fg3, fontSize: p.font(12), lineHeight: p.font(18) }}>
            这个安装包里没有端侧语音引擎（VAD={String(hfAvail.vad)} / 唤醒词={String(hfAvail.kws)}），
            免唤醒不可用。装上带 M4 原生面的新版本后这里会自动出现。
          </Text>
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
            <Text style={{ color: p.fg3, fontSize: p.font(11), lineHeight: p.font(17) }}>
              三段式：收音时上传音频到语音识别服务，识别完成后将文字交给助手。
              端到端：仅在唤醒后的对话窗内上传原始音频；免唤醒待机时麦克风仍在本机监听，
              不上传音频。默认三段式。
            </Text>
            {settings.voicePipeline === 's2s' ? (
              <Text style={{ color: p.amber, fontSize: p.font(11), lineHeight: p.font(17) }}>
                已选端到端：本机麦克风的原始音频会在每次唤醒后的对话窗内上传。
              </Text>
            ) : null}
            {settings.s2sConsentAt > 0 ? (
              <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
                已于 {new Date(settings.s2sConsentAt).toLocaleString('zh-CN', { hour12: false })} 同意端到端上传原始音频
              </Text>
            ) : null}
          </>
        )}
        <SwitchRow
          p={p}
          settingKey="visionEnabled"
          label="看图问答"
          desc="只有当你说「这是什么」这类看图的话时才拍一张，其余时候一帧都不拍。默认关"
          value={settings.visionEnabled}
          onChange={(visionEnabled) => set({ visionEnabled })}
        />
        {settings.visionEnabled ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11), lineHeight: p.font(17) }}>
            拍到的画面只用于回答当前这一句，服务器上最多保留两分钟，不落盘、不进记忆、不进日志。
            手机上用的是后置摄像头（PoC 阶段代替车外摄像头，卡片上会标「模拟」）。
          </Text>
        ) : null}
      </Section>

      {/* 账号与能力（AR05 R14）：**服务端摘要**，不是客户端推断。三种「不能用」分开说——
          没授权 / 服务不在线 / 取不到；取不到就说取不到，不冒充「可用」也不冒充「没有」。 */}
      <Section p={p} title="账号与能力（服务端）">
        <SessionSummarySection
          p={p}
          result={session}
          loading={sessionLoading}
          onRefresh={() => void refreshSession(server)}
        />
      </Section>

      <Section p={p} title="能力开关（关掉的指令会被婉拒）">
        {AGENT_CATALOG.map((a) => (
          <SwitchRow
            key={a.id}
            p={p}
            settingKey={'agent-' + a.id}
            label={`${a.icon} ${a.label}`}
            desc={a.desc}
            value={settings.agents[a.id] !== false}
            onChange={() => toggleAgent(a.id)}
          />
        ))}
      </Section>

      <Section p={p} title="记忆与隐私">
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
      </Section>

      {/* UX v2.1 实验室。两个 v1 回滚开关（光球状态锚 + 状态胶囊 / 承诺面 Focus Dock）已删
          ——打磨批 E，裁决 J1：代码里只剩 v2 一条呈现路径。 */}
      <Section p={p} title="实验室（UX v2.1）">
        <SwitchRow
          p={p}
          settingKey="reduceMotionForce"
          label="减少动效（强制）"
          desc="光球、光标、思考点全部静止（系统「移除动画」开着时自动生效，这里是强制开）"
          value={settings.reduceMotionForce}
          onChange={(reduceMotionForce) => set({ reduceMotionForce })}
        />
        <SwitchRow
          p={p}
          settingKey="reduceTransparency"
          label="减少透明度"
          desc="语音层不再对背景做模糊，回到染色玻璃"
          value={settings.reduceTransparency}
          onChange={(reduceTransparency) => set({ reduceTransparency })}
        />
        <Link href="/state-gallery" style={{ color: p.accent, fontSize: p.font(14) }}>
          状态画廊（在场 13 态 + 7 种降级 / 主题过检）
        </Link>
      </Section>

      {developmentDiagnosticsEnabled() ? (
        <Section p={p} title="开发诊断（可采集、播放或发送）">
          <Link href="/debug" style={{ color: p.accent, fontSize: p.font(14) }}>
            主链发送与回放探针
          </Link>
          <Link href="/voice-spike" style={{ color: p.accent, fontSize: p.font(14) }}>
            语音采集与播放探针
          </Link>
        </Section>
      ) : null}

      <Section p={p} title="诊断与样本">
        <Link href="/capture-status" style={{ color: p.accent, fontSize: p.font(14) }}>
          采集状态（只读）
        </Link>
        <Link href="/card-gallery" style={{ color: p.accent, fontSize: p.font(14) }}>
          卡片画廊（M3 全卡族 / 主题过检）
        </Link>
        <Link href="/presence-trail" style={{ color: p.accent, fontSize: p.font(14) }}>
          在场轨迹（B2：光球为什么变了 / 麦为什么开了）
        </Link>
        <Link href="/turn-timeline" style={{ color: p.accent, fontSize: p.font(14) }}>
          轮次时间线（AR08：这一轮的时间花在哪一段）
        </Link>
        <Link href="/native-spike" style={{ color: p.accent, fontSize: p.font(14) }}>
          {developmentDiagnosticsEnabled() ? '原生状态与触感测试' : '原生状态（折叠姿态 / 电量 / 布局）'}
        </Link>
        <Link href="/blur-spike" style={{ color: p.accent, fontSize: p.font(14) }}>
          材质 spike（B3：真模糊 vs G1-tint 对照）
        </Link>
      </Section>

      <Text
        selectable
        testID="build-label"
        style={{ color: p.fg3, fontSize: p.font(11), textAlign: 'center', paddingBottom: 8 }}
      >
        {buildLabel}
      </Text>
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
