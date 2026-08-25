// 设置页（实施计划 M1-5）：分区=服务器（M0-5 复用，改配置回对话屏断开重连）/ 显示 /
// 助手 / 能力开关（AGENT_CATALOG 全列，disabled_agents 生效）/ 记忆 / 定位 / 调试入口。
// 持久化 AsyncStorage（settings store）；buildMeta 键集由 settingsMeta.test.ts 钉住。
import { Link } from 'expo-router'
import { useEffect, useState, type ReactNode } from 'react'
import { Pressable, ScrollView, Switch, Text, TextInput, View } from 'react-native'
import { useStore } from 'zustand'

import { AGENT_CATALOG } from '@shared/types.ts'

import { loadServerConfig } from '../../core/config/storage'
import type { ServerConfig } from '../../core/config/types'
import { settingsStore, type AppSettings } from '../../core/settings/store'
import { usePalette, type Palette } from '../../ui/theme'

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

  useEffect(() => {
    void loadServerConfig().then(setServer)
  }, [])
  useEffect(() => setNameDraft(settings.assistantName), [settings.assistantName])

  const set = (patch: Partial<AppSettings>) => update(patch)

  return (
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

      <Section p={p} title="调试">
        <Link href="/debug" style={{ color: p.accent, fontSize: p.font(14) }}>
          主链帧调试屏（M0）
        </Link>
      </Section>
    </ScrollView>
  )
}
