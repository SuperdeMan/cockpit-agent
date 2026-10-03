// 设置页（实施计划 M1-5 + M2 语音两分区；打磨批 B 分级重排；v3 P5a 换 Figma 04 页 S 组的视觉：
// 分区 = accent 标题 + surface 圆角 16 的组、行间分隔线；单选一律分段按钮；开关 M3 配色；音色是图标格子）。
// 五组用户设置：通用 / 助手 / 语音 / 隐私 / 账号与连接；工程入口一个不少，全部搬进「开发者选项」
// ——prod 默认隐藏，构建行连点 7 次解锁（判据只在 core/diagnostics.ts::developerOptionsVisible）。
// 持久化 AsyncStorage（settings store）；buildMeta 键集由 settingsMeta.test.ts 钉住。
import { Link } from 'expo-router'
import { Fragment, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { Alert, Pressable, ScrollView, Switch, Text, View, useWindowDimensions } from 'react-native'
import { useStore } from 'zustand'

import {
  AGENT_CATALOG, ASR_MODES, ASR_PROVIDER_FALLBACK, asrEngineOptions, asrModeOf, pickAsrEngine,
  type AsrProviderInfo, type TtsProviderInfo,
} from '@shared/types.ts'

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
import { DEFAULT_APP_SETTINGS, needsS2sConsent, settingsStore, type AppSettings } from '../../core/settings/store'
import { voiceIcon } from '../../core/settings/voiceIcon'
import { fetchAsrProviders, fetchTtsProviders } from '../../core/voice/catalog'
import { handsFreeAvailability } from '../../core/voice/handsFree'
import { speechController } from '../../core/voice/speech'
import { Button } from '../../ui/Button'
import { Icon, iconRuntimeAvailable } from '../../ui/Icon'
import { PRESENCE_LANE_DP } from '../../ui/layout/bottomChrome'
import { SUPPORT_NAV_WIDTH, supportWide } from '../../ui/layout/sizeClass'
import { ListGroup, ListItem, ListSectionHeader, switchColors } from '../../ui/ListItem'
import { Pill } from '../../ui/Pill'
import { Segmented } from '../../ui/Segmented'
import { TextField } from '../../ui/TextField'
import { usePalette, type Palette } from '../../ui/theme'
import { RADIUS, SPACE, TARGET, textStyle } from '../../ui/tokens'
import { useAssistant } from '../assistant/AssistantProvider'
import { S2sConsentSheet } from './S2sConsentSheet'
import { labelUnits, VOICE_GRID_GAP, voiceGridColumns } from './voiceGrid'

/** 分区（Figma ListSection/Header + 分组卡）：accent 标题 + surface 圆角 16 的组，行间 line 分隔。
 *  组里每个子项都是一「行」（ChoiceRow / SwitchRow / NoteRow / Block / LinkRow / ListItem），各自带内边距 */
function Section({ p, title, children }: { p: Palette; title: string; children: ReactNode }) {
  return (
    <View>
      <ListSectionHeader p={p} title={title} fontScale={p.fontScale} />
      <ListGroup p={p}>{children}</ListGroup>
    </View>
  )
}

/** 组内一段自由内容（输入框、能力摘要、隐私记录）：与 ListItem 同样的左右 16、上下 12 */
function Block({ children, testID }: { children: ReactNode; testID?: string }) {
  return (
    <View testID={testID} style={{ paddingHorizontal: SPACE[3], paddingVertical: SPACE[2], gap: 8 }}>
      {children}
    </View>
  )
}

/** 组内单独一行说明（caption 次级色）：代价、隐私口径这类必须在屏上说清的话 */
function NoteRow({ p, text, tone, testID }: { p: Palette; text: string; tone?: string; testID?: string }) {
  return (
    <Block testID={testID}>
      <Text style={[textStyle('caption', p.fontScale), { color: tone ?? p.fg3 }]}>{text}</Text>
    </Block>
  )
}

type DevHref = '/state-gallery' | '/card-gallery' | '/presence-trail' | '/turn-timeline' | '/native-spike' | '/blur-spike' | '/capture-status' | '/debug' | '/voice-spike'

/** 入口行（Figma ListItem/Kind=Link）：accent 标题，整行可点；路由仍是 expo-router 的 Link（href 留在树上） */
function LinkRow({ p, href, title, subtitle }: { p: Palette; href: DevHref | '/onboarding'; title: string; subtitle?: string }) {
  return (
    <Link href={href} asChild>
      <ListItem p={p} kind="link" title={title} subtitle={subtitle} fontScale={p.fontScale} />
    </Link>
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
    <Text testID={testID} style={[textStyle('caption', p.fontScale), { color: p.fg2 }]}>{text}</Text>
  )
  const refresh = (
    <Pressable accessibilityRole="button" onPress={onRefresh} testID="settings-session-refresh" style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', alignSelf: 'flex-start' }}>
      <Text style={[textStyle('labelL', p.fontScale), { color: p.accent }]}>{loading ? '刷新中…' : '刷新'}</Text>
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
        <Link href="/onboarding" style={[textStyle('labelL', p.fontScale), { color: p.accent }]}>重新配置</Link>
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
        <Text testID="settings-session-partial" style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>
          这份摘要只取到一部分（{s.summaryReason || '原因未知'}），没列出的能力状态未知。
        </Text>
      ) : null}
      {stale ? (
        <Text testID="settings-session-stale" style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>
          摘要已过期，点刷新重新读取。
        </Text>
      ) : null}
      <View style={{ gap: 6 }}>
        {s.capabilities.length === 0
          ? line('服务端没有返回任何能力。', 'settings-session-empty')
          : s.capabilities.map((c) => (
            <View key={c.id} testID={`settings-capability-${c.id}`} style={{ flexDirection: 'row', gap: 8 }}>
              <Text style={[textStyle('caption', p.fontScale), { color: p.fg2, flex: 1 }]}>{c.displayName}</Text>
              {/* 可用 = 绿、未授权 = 琥珀、其余（不在线 / 未知）次级色——Figma S-3 能力摘要 */}
              <Text style={[textStyle('caption', p.fontScale), { color: c.status === 'available' ? p.green : c.status === 'unauthorized' ? p.amber : p.fg3 }]}>
                {CAPABILITY_STATUS_LABEL[c.status] ?? `未知状态（${c.status}）`}
              </Text>
            </View>
          ))}
      </View>
      {refresh}
    </>
  )
}

// 选项标签的显示宽度（汉字 1、其余 0.5）见 voiceGrid.ts::labelUnits：分段按钮一段只放得下 6 个汉字宽；「English」只有 3.5

/** 单选行（Figma ListItem/Kind=Segmented）：标题 + 分段按钮（语义仍是单选，`choice-<值>` 句柄逐项沿用），
 *  可选一行说明。选项多或标签长（引擎列表）分段放不下，退回一排可换行的单选胶囊 */
function ChoiceRow<T extends string>({
  p,
  label,
  value,
  options,
  onPick,
  note,
}: {
  p: Palette
  label: string
  value: T
  options: { v: T; label: string }[]
  onPick(v: T): void
  note?: string
}) {
  const segmented = options.length <= 3 && options.every((o) => labelUnits(o.label) <= 6)
  return (
    <ListItem
      p={p}
      title={label}
      fontScale={p.fontScale}
      below={
        <View style={{ gap: 8 }}>
          {segmented ? (
            <Segmented
              p={p}
              fontScale={p.fontScale}
              value={value}
              onChange={onPick}
              options={options.map((o) => ({
                value: o.v,
                label: o.label,
                testID: `choice-${String(o.v)}`,
                accessibilityLabel: `${label}：${o.label}`,
              }))}
            />
          ) : (
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
          )}
          {note ? <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>{note}</Text> : null}
        </View>
      }
    />
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
    <ListItem
      p={p}
      title={label}
      subtitle={desc}
      fontScale={p.fontScale}
      right={
        // testID 必填的理由（AR06 / A06-2，坑账 AR03 那条）：自动化此前按「标签之后第一枚开关」
        // 配对，而多行 desc 会把开关挤出文字带 ⇒ 点中的是**下一行**那枚；更糟的是回读时读到的
        // 也是那枚错开关的新值，看起来完全像「设置生效了」。把开关和它改的那个键绑死，
        // 「建立前提 → 回读」才是同一个对象。accessibilityLabel 让 TalkBack 与截图取证也认得出它。
        <Switch
          testID={'settings-switch-' + settingKey}
          accessibilityLabel={label}
          value={value}
          onValueChange={onChange}
          {...switchColors(p, value)}
        />
      }
    />
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
      <View style={{ flexDirection: 'row', gap: 8, alignItems: 'flex-end' }}>
        <View style={{ flex: 1 }}>
          <TextField p={p} fontScale={p.fontScale} label="添加一条示例" value={draft} onChangeText={setDraft} onSubmitEditing={add} placeholder="例如：附近的充电站" />
        </View>
        <Button p={p} variant="tonal" label="添加" fontScale={p.fontScale} onPress={add} />
      </View>
      <Pressable accessibilityRole="button" onPress={() => onChange([...MOBILE_QUICK_COMMAND_ORDER])} style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', alignSelf: 'flex-start' }}>
        <Text style={[textStyle('labelL', p.fontScale), { color: p.accent }]}>恢复默认示例</Text>
      </Pressable>
    </View>
  )
}

/** 音色格子（Figma S-2）：三列等宽，图标 + 名字，性别（取自引擎元数据）另起一行小字；选中 = accent 描边 + accent 浅底。
 *  名字与性别分两行：真机上 MiniMax 的音色名比画板样例长，「甜美女性 · 女」挤在一行会被截成「甜美…」（P5a 真机）。
 *  按行切三个一组、末行补空位：flexWrap + flexGrow 会把落单的最后一格拉满整行。
 *  语义仍是单选：`choice-<voice_id>` 句柄沿用、选中进无障碍 selected */
function VoiceGrid({
  p,
  voices,
  value,
  onPick,
}: {
  p: Palette
  voices: { voice_id: string; name: string; gender?: string }[]
  value: string
  onPick(id: string): void
}) {
  // 列数按量到的宽度与最长的名字算（voiceGrid.ts）：两字名三列，四字名在手机外屏退两列，不截成「甜美…」
  const [width, setWidth] = useState(0)
  const cols = voiceGridColumns(width, voices.map((v) => v.name), textStyle('labelM', p.fontScale).fontSize ?? 13)
  const rows: (typeof voices)[] = []
  for (let i = 0; i < voices.length; i += cols) rows.push(voices.slice(i, i + cols))
  // 每格定宽（量到宽度之后）：格子带 10 内边距 + 1 描边、补位格没有，都用 flex:1 时 Yoga 先给格子留出内边距再均分，
  // 末行那一格会比上面宽约 11dp（2026-10-03 真机「男主持」）。没量到宽度时（首帧 / 测试）才退回 flex:1
  const cell = width > 0 ? { width: (width - (cols - 1) * VOICE_GRID_GAP) / cols } : { flex: 1 }
  return (
    <View testID="voice-grid" style={{ gap: VOICE_GRID_GAP }} onLayout={(e) => setWidth(Math.floor(e.nativeEvent.layout.width))}>
      {rows.map((row, r) => (
        <View key={r} style={{ flexDirection: 'row', gap: VOICE_GRID_GAP }}>
          {row.map((v) => {
            const on = v.voice_id === value
            const gender = v.gender === 'male' ? '男声' : v.gender === 'female' ? '女声' : ''
            return (
              <Pressable
                key={v.voice_id}
                testID={`choice-${v.voice_id}`}
                accessibilityRole="button"
                accessibilityLabel={`音色：${v.name}${gender ? `，${gender}` : ''}`}
                accessibilityState={{ selected: on }}
                onPress={() => onPick(v.voice_id)}
                style={{
                  ...cell,
                  minHeight: p.target(TARGET.parked),
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 8,
                  paddingHorizontal: 10,
                  paddingVertical: 6,
                  borderRadius: RADIUS.md,
                  borderWidth: 1,
                  borderColor: on ? p.accent : 'transparent',
                  backgroundColor: on ? p.accentSoft : p.surfaceHigh,
                }}
              >
                {iconRuntimeAvailable() ? <Icon name={voiceIcon(v)} size={18} color={on ? p.accent : p.fg2} /> : null}
                <View style={{ flex: 1 }}>
                  <Text numberOfLines={1} style={[textStyle('labelM', p.fontScale), { color: on ? p.accent : p.fg1 }]}>
                    {v.name}
                  </Text>
                  {gender ? <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>{gender}</Text> : null}
                </View>
              </Pressable>
            )
          })}
          {Array.from({ length: cols - row.length }, (_, k) => (
            <View key={`pad-${k}`} style={cell} />
          ))}
        </View>
      ))}
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
  const caption = textStyle('caption', p.fontScale)
  const row = (k: string, v: string, tone: string = p.fg1) => (
    <View key={k + v} style={{ flexDirection: 'row', gap: 12, paddingVertical: 4 }}>
      <Text style={[caption, { color: p.fg3, width: 72 }]}>{k}</Text>
      <Text style={[caption, { color: tone, flex: 1 }]}>{v}</Text>
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
        <Text style={[caption, { color: p.fg3 }]}>连上座舱后这里会显示麦克风与摄像头此刻的状态</Text>
      )}
      <Text style={[caption, { color: p.fg2, marginTop: 6 }]}>最近激活（本次启动后，只在本机、不上传）</Text>
      {entries.length ? (
        entries.map((e) => (
          <Text key={`${e.at}-${e.source}`} style={[caption, { color: p.fg3 }]}>
            {hhmm(e.at)} · {ACTIVITY_SOURCE_LABEL[e.source]} · {e.note}
          </Text>
        ))
      ) : (
        <Text style={[caption, { color: p.fg3 }]}>还没有采集</Text>
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
  // 清除对话记录的结果（G-04）：本机删除要回读，失败要说出来——「界面清空」不等于「本机记录删掉了」
  const [clearResult, setClearResult] = useState<{ ok: boolean; at: number } | null>(null)
  const [nameDraft, setNameDraft] = useState(settings.assistantName)
  const [ttsCatalog, setTtsCatalog] = useState<TtsProviderInfo[]>([])
  // ASR 目录先用共享兜底表（离线 / 探测未回也能选），探测回来再换；TTS 保持原样（[] 期间不渲染）
  const [asrCatalog, setAsrCatalog] = useState<AsrProviderInfo[]>(ASR_PROVIDER_FALLBACK)
  const [previewing, setPreviewing] = useState(false)
  // AR05 R14：会话身份与能力摘要。null = 还没查过；查询失败/旧服务端各有各的展示，
  // **不把「此刻查不到」显示成「你没有这些能力」**。
  const [session, setSession] = useState<SessionInfoResult | null>(null)
  const [sessionLoading, setSessionLoading] = useState(false)
  // 免唤醒的原生可用性：**在渲染前问一次**。原生缺席时连开关都不渲染——
  // 这不是 UI 洁癖，是坑账 §9.27：原生缺席时崩在原生线程，ErrorBoundary 兜不住整屏红屏。
  const hfAvail = useMemo(() => handsFreeAvailability(), [])
  // 免唤醒的运行事实（启动失败原因）来自宿主的 hf；Provider 没挂（引导页 / 测试）时是 null
  const runtime = useAssistant()
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
  // 大屏版式（v3 P6）：选中的页；窗口宽决定窄屏铺开还是列表–详情（sizeClass.supportWide）
  const [pageKey, setPageKey] = useState('general')
  const { width: windowWidth } = useWindowDimensions()

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
  // 「方式 → 引擎」两级（2026-09-14，判据与 HMI 同一份：@shared/types.ts 的 asr* 函数）
  const asrMode = asrModeOf(asrCatalog, settings.asrProvider)
  const asrEngines = asrEngineOptions(asrCatalog, asrMode)
  const asrEngineKey = (e: { provider: string; model: string }) => `${e.provider}/${e.model}`
  const asrCurrent = asrEngines.find((e) => e.provider === settings.asrProvider && e.model === settings.asrModel)

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
  const caption = textStyle('caption', p.fontScale)
  const dev = developmentDiagnosticsEnabled()

  const secGeneral = (
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
  )

  const secAssistant = (
      <Section p={p} title="助手">
        <Block>
          <TextField
            p={p}
            fontScale={p.fontScale}
            label="昵称（下一轮起生效）"
            helper="唤醒词不变，仍是「小舟小舟」"
            value={nameDraft}
            onChangeText={setNameDraft}
            onEndEditing={() => {
              const v = nameDraft.trim()
              if (v) set({ assistantName: v })
            }}
          />
        </Block>
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
  )

  const secExamples = (
      <Section p={p} title="首页示例">
        <NoteRow p={p} text="首页与欢迎态显示的示例指令；没有对应能力的会自动隐藏" />
        <Block>
          <QuickCommandsEditor p={p} commands={settings.quickCommands} onChange={(quickCommands) => set({ quickCommands })} />
        </Block>
      </Section>
  )

  const secVoice = (
      <Section p={p} title="语音">
        {/* 拍板（计划 §5）：长选项缩成「实时 / 整句」，原来括号里的话搬到说明行（说明取共享表的 hint，不另写一份） */}
        <ChoiceRow
          p={p}
          label="识别方式"
          value={asrMode}
          options={ASR_MODES.map((m) => ({ v: m.id, label: m.label }))}
          note={ASR_MODES.map((m) => `${m.label}：${m.hint}`).join('；')}
          onPick={(mode) => {
            if (mode === asrMode) return
            // 换方式：优先本端默认那一对（fun-asr 主），其次该方式下首个可用；(provider, model) 永远成对写入
            const picked = pickAsrEngine(asrEngineOptions(asrCatalog, mode), {
              provider: DEFAULT_APP_SETTINGS.asrProvider, model: DEFAULT_APP_SETTINGS.asrModel,
            })
            if (picked) set({ asrProvider: picked.provider, asrModel: picked.model })
          }}
        />
        {asrEngines.length ? (
          <ChoiceRow
            p={p}
            label={asrMode === 'realtime' ? '实时引擎' : '整句引擎'}
            value={asrCurrent ? asrEngineKey(asrCurrent) : ''}
            options={asrEngines.map((e) => ({ v: asrEngineKey(e), label: e.available ? e.label : e.label + '（未配置）' }))}
            onPick={(key) => {
              // 模型 id 跟着引擎走：留着上一个引擎的 model 会让 start 帧带一个该引擎不认识的名字（只表现为连不上）
              const e = asrEngines.find((x) => asrEngineKey(x) === key)
              if (e) set({ asrProvider: e.provider, asrModel: e.model })
            }}
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
        <ChoiceRow
          p={p}
          label="播报"
          value={settings.speakPolicy}
          options={[
            { v: 'auto' as const, label: '自动' },
            { v: 'always' as const, label: '总是' },
            { v: 'silent' as const, label: '静音' },
          ]}
          note="自动：用语音问才播报，打字只显示文字（默认）。总是：打字也播报。静音：完全不出声（试听仍可用）。"
          onPick={(speakPolicy) => {
            set({ speakPolicy })
            if (speakPolicy === 'silent') speechController().stop() // 关掉要立刻停当前这段
          }}
        />
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
            label="播报引擎"
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
          <ListItem
            p={p}
            title="音色"
            fontScale={p.fontScale}
            below={<VoiceGrid p={p} voices={ttsEngine.voices} value={settings.voiceId} onPick={(voiceId) => set({ voiceId })} />}
          />
        ) : null}
        <Block>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            <Button
              p={p}
              testID="voice-preview"
              variant="tonal"
              fontScale={p.fontScale}
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
            {previewMsg ? <Text style={[caption, { color: p.amber, flex: 1 }]}>{previewMsg}</Text> : null}
          </View>
        </Block>
      </Section>
  )

  // ── 免唤醒与端到端 ──：开关**默认全关/最保守**，且每条都在屏上说清代价——
  // 视觉与 S2S 是架构红线里点名要「文案说清差异」的两条
  // （CLAUDE.md §5「唯一的受控例外」条件③、「视觉单帧同款三条件」第三条）。
  // 组里每行单独一项（不用 Fragment 包几行）：分组卡按子项画分隔线
  const secHandsFree = (
      <Section p={p} title="免唤醒与端到端">
        {!hfAvail.usable ? <NoteRow p={p} text="这个安装包里没有端侧语音引擎，免唤醒不可用；装上带语音引擎的版本后这里会自动出现。" /> : null}
        {!hfAvail.usable && developerVisible ? <NoteRow p={p} text={`VAD=${String(hfAvail.vad)} / 唤醒词=${String(hfAvail.kws)}`} /> : null}
        {hfAvail.usable ? (
          <SwitchRow
            p={p}
            settingKey="handsFree"
            label="免唤醒对话"
            desc="开启后麦克风常开：说唤醒词即可开始，答完 8 秒内可直接接着说。耗电，默认关"
            value={settings.handsFree}
            onChange={(handsFree) => set({ handsFree })}
          />
        ) : null}
        {/* G-06：开关是意图、这一行是事实——回路没起来时开关照亮，但原因就在它下面，不让「亮着的开关」替死回路说话 */}
        {hfAvail.usable && settings.handsFree && runtime?.hf.error ? (
          <Block>
            <Text testID="handsfree-error" style={[caption, { color: p.amber }]}>
              {`免唤醒没有启动：${runtime.hf.error}。${runtime.hf.errorKind === 'permission' ? '请在系统设置里允许小舟随行使用麦克风' : '关掉再打开可以重试'}`}
            </Text>
          </Block>
        ) : null}
        {hfAvail.usable && settings.handsFree ? (
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
        {hfAvail.usable ? (
          <ChoiceRow
            p={p}
            label="语音链路"
            value={settings.voicePipeline}
            options={[
              { v: 'classic' as const, label: '三段式' },
              { v: 's2s' as const, label: '端到端' },
            ]}
            note="三段式：收音时上传音频到语音识别服务，识别完成后将文字交给助手。端到端：仅在唤醒后的对话窗内上传原始音频；免唤醒待机时麦克风仍在本机监听，不上传音频。默认三段式。"
            onPick={(voicePipeline) => {
              // 首次切端到端弹一次性显式同意（方案 §5.2.2）；同意过的直接切；切回三段式永远不问
              if (voicePipeline === 's2s' && needsS2sConsent(settings)) setConsentOpen(true)
              else set({ voicePipeline })
            }}
          />
        ) : null}
        {hfAvail.usable && settings.voicePipeline === 's2s' ? (
          <NoteRow p={p} tone={p.amber} text="已选端到端：本机麦克风的原始音频会在每次唤醒后的对话窗内上传。" />
        ) : null}
        {hfAvail.usable && settings.s2sConsentAt > 0 ? (
          <NoteRow p={p} text={`已于 ${new Date(settings.s2sConsentAt).toLocaleString('zh-CN', { hour12: false })} 同意端到端上传原始音频`} />
        ) : null}
      </Section>
  )

  const secPrivacy = (
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
        {settings.visionEnabled ? (
          <NoteRow p={p} text="拍到的画面只用于回答当前这一句，服务器上最多保留两分钟，不落盘、不进记忆、不进日志。手机上用的是后置摄像头（代替车外摄像头，卡片上会标「模拟」）。" />
        ) : null}
        {/* 打磨批 F（裁决 J3）：记录本机持久化后要有清除口；二次确认，清当前会话与存量。挂起台账是服务端的账，不动 */}
        <ListItem
          p={p}
          kind="danger"
          title="清除对话记录"
          fontScale={p.fontScale}
          testID="settings-clear-history"
          onPress={() =>
            Alert.alert('清除对话记录', '会同时清空当前会话与本机保存的记录，不可恢复。', [
              { text: '取消', style: 'cancel' },
              {
                text: '清除',
                style: 'destructive',
                onPress: () => {
                  const w = getWired()
                  w?.core.clearMessages()
                  if (w) void clearHistory(w.historyKey).then((ok) => setClearResult({ ok, at: Date.now() }))
                },
              },
            ])
          }
        />
        {clearResult ? (
          <Block>
            <Text testID="settings-clear-history-result" style={[caption, { color: clearResult.ok ? p.fg3 : p.amber }]}>
              {clearResult.ok
                ? `已清除当前会话与本机记录（${new Date(clearResult.at).toLocaleTimeString('zh-CN', { hour12: false })}，删完已回读确认）`
                : '当前会话已清空，但本机保存的记录没有删掉（存储层未兑现），可稍后再试；报问题时抄上底部构建行'}
            </Text>
          </Block>
        ) : null}
      </Section>
  )

  const secPrivacyRecord = (
      <Section p={p} title="隐私记录">
        <Block>
          <PrivacyRecord p={p} />
        </Block>
      </Section>
  )

  const secAccount = (
      <Section p={p} title="账号与连接">
        {/* 打磨批 B（评审 P17 / D5）：不再直出 URL 与令牌尾巴——完整地址在「重新配置」页 */}
        <Block>
          <Text testID="settings-server" style={[textStyle('bodyM', p.fontScale), { color: p.fg1 }]}>
            {server ? `${connLabel} · ${serverName(server)}` : '尚未配置服务器'}
          </Text>
          {server ? (
            // 身份取**服务端事实**；查不到就说没确认，不拿凭证尾巴冒充身份（AR05 F06）
            <Text testID="settings-identity" style={[caption, { color: p.fg3 }]}>
              {session?.kind === 'ok' && session.summary.userId
                ? `账号 ${session.summary.userId}${session.summary.vehicleId ? ` · 关联车辆 ${session.summary.vehicleId}` : ''}`
                : '账号未确认 · 访问令牌已保存'}
            </Text>
          ) : null}
        </Block>
        {/* 账号与能力（AR05 R14）：**服务端摘要**，不是客户端推断。三种「不能用」分开说——
            没授权 / 服务不在线 / 取不到；取不到就说取不到，不冒充「可用」也不冒充「没有」。 */}
        <Block>
          <Text style={[textStyle('bodyM', p.fontScale), { color: p.fg1 }]}>能力摘要</Text>
          <SessionSummarySection p={p} result={session} loading={sessionLoading} onRefresh={() => void refreshSession(server)} />
        </Block>
      </Section>
  )

  const secCaps = (
      <Section p={p} title="能力开关">
        <NoteRow p={p} text="关掉的指令会被婉拒" />
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
      </Section>
  )

  // ── 设备（Figma S-4）── 身份与行车（B4-10 / 方案 §6.0 / AR05 R14）：**设备角色只决定布局**，
  // 窗口尺寸不决定权限、横屏不决定你是驾驶员、平板不自动获得车控。能不能控车由「能力摘要」如实说，这里只说布局。
  // 拍板（计划 §5）：选项缩成「手持 / 支架 / 车载平板」，原来的长标签搬到说明行
  const secDevice = (
      <Section p={p} title="设备">
        <ChoiceRow
          p={p}
          label="设备角色"
          value={settings.deviceRole}
          options={[
            { v: 'handheld' as const, label: '手持' },
            { v: 'mount' as const, label: '支架' },
            { v: 'trusted-tablet' as const, label: '车载平板' },
          ]}
          note="只决定布局：支架 = 车载支架或副驾手持（行车时输入框折成键盘按钮）；车载平板 = 可信车载平板（行车时不出输入框）。选哪个都不会多出任何权限，能做什么看「能力摘要」。"
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
          <ListItem
            p={p}
            testID="driving-auto-exit"
            title="自动行车中 · 座舱判定为行驶"
            accessibilityLabel="自动行车中，退出行车档"
            fontScale={p.fontScale}
            right={<Text style={[textStyle('labelL', p.fontScale), { color: p.accent }]}>退出</Text>}
            onPress={() => core?.dismissDriving()}
          />
        ) : null}
        <LinkRow p={p} href="/onboarding" title="重新配置连接" subtitle="保存后回对话页自动重连" />
      </Section>
  )

  // 开发者（prod 默认隐藏；判据 developerOptionsVisible）
  const secDev = (
        <Section p={p} title="开发者">
          <NoteRow p={p} text="只读的取证屏与画廊：不采集、不播放、不发送。报问题时把底部的构建行一起抄上。" />
          <LinkRow p={p} href="/state-gallery" title="状态画廊" />
          <LinkRow p={p} href="/card-gallery" title="卡片画廊" />
          <LinkRow p={p} href="/presence-trail" title="在场轨迹" />
          <LinkRow p={p} href="/turn-timeline" title="轮次时间线" />
          <LinkRow p={p} href="/native-spike" title={dev ? '原生状态与触感测试' : '原生状态'} />
          <LinkRow p={p} href="/blur-spike" title="材质对照" />
          <LinkRow p={p} href="/capture-status" title="采集状态（JSON）" />
          {dev ? <NoteRow p={p} text="操作诊断（只在 dev 包；会采集、播放或发送）" /> : null}
          {dev ? <LinkRow p={p} href="/debug" title="主链发送与回放探针" /> : null}
          {dev ? <LinkRow p={p} href="/voice-spike" title="语音采集与播放探针" /> : null}
          {build.variant === 'prod' ? (
            <ListItem
              p={p}
              kind="danger"
              title="隐藏开发者选项"
              fontScale={p.fontScale}
              testID="developer-hide"
              onPress={() => set({ developerUnlocked: false })}
            />
          ) : null}
        </Section>
  )

  // 构建行：报问题先抄它。连点 DEVELOPER_UNLOCK_TAPS 次解锁开发者选项
  const buildRow = (
      <Pressable testID="build-label-tap" onPress={onBuildTap} style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center', marginTop: SPACE[2] }}>
        <Text selectable testID="build-label" style={[caption, { color: p.fg3, textAlign: 'center' }]}>
          {buildLabel}
        </Text>
        {!developerVisible && buildTaps >= 3 ? (
          <Text style={[caption, { color: p.fg3, textAlign: 'center' }]}>再点 {DEVELOPER_UNLOCK_TAPS - buildTaps} 次进入开发者选项</Text>
        ) : null}
      </Pressable>
  )
  const consentSheet = (
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
  )

  // 页（v3 P6，Figma 07 页 SP-1）：窄屏依次铺开（同 v3 P5a）；大屏左侧导航列、右侧只放选中的那一页。
  // 判据只有 sizeClass.supportWide 一份；行车档一律单栏。交互上只是把分区变成导航列表，没有新功能
  const pages: { key: string; title: string; body: ReactNode }[] = [
    { key: 'general', title: '通用', body: secGeneral },
    { key: 'assistant', title: '助手', body: <>{secAssistant}{secExamples}</> },
    { key: 'voice', title: '语音', body: <>{secVoice}{secHandsFree}</> },
    { key: 'privacy', title: '隐私', body: <>{secPrivacy}{secPrivacyRecord}</> },
    { key: 'account', title: '账号与连接', body: <>{secAccount}{secCaps}</> },
    { key: 'device', title: '设备', body: secDevice },
    ...(developerVisible ? [{ key: 'developer', title: '开发者', body: secDev }] : []),
  ]
  const wide = supportWide(windowWidth, settings.drivingManual || autoDrivingNow)

  if (!wide) {
    return (
      // Modal 与 ScrollView 并列：同意页要盖住整屏，塞进 ScrollView 里会跟着滚
      <View style={{ flex: 1, backgroundColor: p.bg }}>
        <ScrollView style={{ backgroundColor: p.bg }} contentContainerStyle={{ paddingHorizontal: SPACE[3], paddingBottom: SPACE[3] + PRESENCE_LANE_DP, gap: SPACE[1] }}>
          {pages.map((pg) => (
            <Fragment key={pg.key}>{pg.body}</Fragment>
          ))}
          {buildRow}
        </ScrollView>
        {consentSheet}
      </View>
    )
  }
  const current = pages.find((pg) => pg.key === pageKey) ?? pages[0]
  return (
    <View testID="settings-wide" style={{ flex: 1, backgroundColor: p.bg, flexDirection: 'row' }}>
      <ScrollView style={{ width: SUPPORT_NAV_WIDTH, flexGrow: 0 }} contentContainerStyle={{ padding: SPACE[3], gap: SPACE[1] }}>
        {pages.map((pg) => {
          const on = pg.key === current.key
          return (
            <Pressable
              key={pg.key}
              testID={`settings-nav-${pg.key}`}
              accessibilityRole="button"
              accessibilityState={{ selected: on }}
              onPress={() => setPageKey(pg.key)}
              style={{
                minHeight: p.target(TARGET.parked),
                justifyContent: 'center',
                paddingHorizontal: SPACE[3],
                borderRadius: RADIUS.full,
                backgroundColor: on ? p.accentSoft : undefined,
              }}
            >
              <Text style={[textStyle('labelL', p.fontScale), { color: on ? p.accent : p.fg1 }]}>{pg.title}</Text>
            </Pressable>
          )
        })}
        {buildRow}
      </ScrollView>
      <ScrollView testID="settings-detail" style={{ flex: 1 }} contentContainerStyle={{ paddingRight: SPACE[3], paddingBottom: SPACE[3] + PRESENCE_LANE_DP, gap: SPACE[1] }}>
        {current.body}
      </ScrollView>
      {consentSheet}
    </View>
  )
}
