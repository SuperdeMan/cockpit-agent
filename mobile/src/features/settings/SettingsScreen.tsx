// 设置页（实施计划 M1-5 + M2 语音两分区）：分区=服务器（M0-5 复用，改配置回对话屏断开
// 重连）/ 显示 / 助手 / **语音输入** / **语音播报** / 能力开关（AGENT_CATALOG 全列，
// disabled_agents 生效）/ 记忆 / 定位 / 调试入口。
// 持久化 AsyncStorage（settings store）；buildMeta 键集由 settingsMeta.test.ts 钉住。
import { Link } from 'expo-router'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Pressable, ScrollView, Switch, Text, TextInput, View } from 'react-native'
import { useStore } from 'zustand'

import { AGENT_CATALOG } from '@shared/types.ts'

import { loadServerConfig } from '../../core/config/storage'
import type { ServerConfig } from '../../core/config/types'
import { needsS2sConsent, settingsStore, type AppSettings } from '../../core/settings/store'
import {
  fetchAsrProviders,
  fetchTtsProviders,
  type AsrProviderInfo,
} from '../../core/voice/catalog'
import { handsFreeAvailability } from '../../core/voice/handsFree'
import { speechController } from '../../core/voice/speech'
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
  options: Array<{ v: T; label: string }>
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
  label,
  desc,
  value,
  onChange,
}: {
  p: Palette
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
      <Switch value={value} onValueChange={onChange} />
    </View>
  )
}

export function SettingsScreen() {
  const { settings, update, toggleAgent } = useStore(settingsStore)
  const p = usePalette(settings)
  const [server, setServer] = useState<ServerConfig | null>(null)
  const [nameDraft, setNameDraft] = useState(settings.assistantName)
  const [ttsCatalog, setTtsCatalog] = useState<TtsProviderInfo[]>([])
  const [asrCatalog, setAsrCatalog] = useState<AsrProviderInfo[]>([])
  const [previewing, setPreviewing] = useState(false)
  // 免唤醒的原生可用性：**在渲染前问一次**。原生缺席时连开关都不渲染——
  // 这不是 UI 洁癖，是坑账 §9.27：原生缺席时崩在原生线程，ErrorBoundary 兜不住整屏红屏。
  const hfAvail = useMemo(() => handsFreeAvailability(), [])
  // 试听没出声时的那句话（M3 遗留 R1）。空串=没试过或出声了。
  // **必须有这个出口**：无 key 引擎在全链四段里没有任何一段会换引擎，结果就是完全安静，
  // 而屏上此前一个字都不说——用户只能对着一台安静的手机猜是不是自己音量关了。
  const [previewMsg, setPreviewMsg] = useState('')
  // 首次切端到端的一次性显式同意（方案 §5.2.2；红线三条件②的持久化证据）
  const [consentOpen, setConsentOpen] = useState(false)

  useEffect(() => {
    void loadServerConfig().then(setServer)
  }, [])
  useEffect(() => setNameDraft(settings.assistantName), [settings.assistantName])
  // 目录探测：两个端点都可能失败，catalog.ts 里各自回落静态表（不留空白设置页）
  useEffect(() => {
    if (!server?.audioUrl) return
    void fetchTtsProviders(server.audioUrl).then(setTtsCatalog)
    void fetchAsrProviders(server.audioUrl).then(setAsrCatalog)
  }, [server?.audioUrl])

  const ttsEngine = ttsCatalog.find((e) => e.id === settings.ttsProvider) ?? ttsCatalog[0]
  const asrEngine = asrCatalog.find((e) => e.id === settings.asrProvider)

  const set = (patch: Partial<AppSettings>) => update(patch)

  return (
    // Modal 与 ScrollView 并列：同意页要盖住整屏，塞进 ScrollView 里会跟着滚
    <View style={{ flex: 1, backgroundColor: p.bg }}>
    <ScrollView style={{ backgroundColor: p.bg }} contentContainerStyle={{ padding: 14, gap: 16 }}>
      <Section p={p} title="服务器">
        <Text style={{ color: p.fg2, fontSize: p.font(13) }}>
          {server ? server.edgeUrl : '未配置'}
        </Text>
        {server ? (
          <Text style={{ color: p.fg3, fontSize: p.font(12) }}>token ····{server.token.slice(-4)}</Text>
        ) : null}
        <Link href="/onboarding" style={{ color: p.accent, fontSize: p.font(14) }}>
          重新配置（保存后回对话页自动重连）
        </Link>
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
        <SwitchRow
          p={p}
          label="播报回答"
          desc="关闭后完全静默（试听仍可用）"
          value={settings.ttsEnabled}
          onChange={(ttsEnabled) => {
            set({ ttsEnabled })
            if (!ttsEnabled) speechController().stop() // 关掉要立刻停当前这段
          }}
        />
        <SwitchRow
          p={p}
          label="自动播报"
          desc="关闭后只显示文字，不出声"
          value={settings.autoplay}
          onChange={(autoplay) => {
            set({ autoplay })
            if (!autoplay) speechController().stop()
          }}
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
              label="免唤醒对话"
              desc="开启后麦克风常开：说唤醒词即可开始，答完 8 秒内可直接接着说。耗电，默认关"
              value={settings.handsFree}
              onChange={(handsFree) => set({ handsFree })}
            />
            {settings.handsFree ? (
              <SwitchRow
                p={p}
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
              三段式：语音在本机转成文字后，只上传文字。
              端到端：延迟更低，但会把你说话的原始音频上传到服务器（仅在唤醒后的对话窗内采集，
              未唤醒不采）。默认三段式。
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

      <Section p={p} title="能力开关（关掉的指令会被婉拒）">
        {AGENT_CATALOG.map((a) => (
          <SwitchRow
            key={a.id}
            p={p}
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
          label="记忆"
          desc="关闭后本会话不再抽取/使用长期记忆"
          value={settings.memoryEnabled}
          onChange={(memoryEnabled) => set({ memoryEnabled })}
        />
        <SwitchRow
          p={p}
          label="使用定位"
          desc="仅在位置相关请求时取当前坐标，坐标不持久化"
          value={settings.locationEnabled}
          onChange={(locationEnabled) => set({ locationEnabled })}
        />
      </Section>

      {/* UX v2.1 实验室：两个开关是**回滚路径**（§11.5）——纯 JS 批次出问题关开关，不重装。
          v1 的状态条与气泡内确认在 B1/B2 期间不删。 */}
      <Section p={p} title="实验室（UX v2.1）">
        <SwitchRow
          p={p}
          label="光球状态锚 + 状态胶囊"
          desc="关闭后回到 v1：免唤醒状态条、弱网横幅、通知条分开显示"
          value={settings.uxV2Presence}
          onChange={(uxV2Presence) => set({ uxV2Presence })}
        />
        <SwitchRow
          p={p}
          label="承诺面 Focus Dock"
          desc="确认 / 长任务 / 离线队列钉在输入区上方；关闭后回到气泡内确认按钮"
          value={settings.uxV2Dock}
          onChange={(uxV2Dock) => set({ uxV2Dock })}
        />
        <Link href="/state-gallery" style={{ color: p.accent, fontSize: p.font(14) }}>
          状态画廊（在场 13 态 + 7 种降级 / 主题过检）
        </Link>
      </Section>

      <Section p={p} title="调试">
        <Link href="/debug" style={{ color: p.accent, fontSize: p.font(14) }}>
          主链帧调试屏（M0）
        </Link>
        <Link href="/voice-spike" style={{ color: p.accent, fontSize: p.font(14) }}>
          语音采集/播放 spike（M2 取证）
        </Link>
        <Link href="/card-gallery" style={{ color: p.accent, fontSize: p.font(14) }}>
          卡片画廊（M3 全卡族 / 主题过检）
        </Link>
      </Section>
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
