// 首启引导（实施计划 M0-5）：选预设 → 填 FQDN/URL → 填 token（显尾 4 位）→ 连接测试 → 保存。
// cloud 预设：FQDN 校验（与 dev_stack_lib.cloud_endpoints 同构）后派生 8443/8444 两条 URL。
// prod 档不展示 lan/custom（app.config.ts extra.allowCustomServer）。
//
// UX v2.1 B1-13：这一屏**是用户见到的第一面**，此前它是一张系统默认样式的表单——
// 和进去之后的极光座舱是两个产品。上品牌只动渲染层：Aurora 底 + 玻璃分区 + 光球 + 色板 token
// + 安全区；**`onTest` / `onSave` / `derived` / `presets` 与全部 state 一行不改**。
// 顺带补上权限用途文案的落点（app.config.ts:99-101 的注释点名要它：manifest 里写了字不算合规）。
// v3 P5c（Figma 04 页 O 组）：去掉极光底与玻璃分区，换方向 B 的实色分组卡 + TextField + 底栏两键；
// 逻辑（onTest / onSave / derived / presets / state）仍一行不改。
import Constants from 'expo-constants'
import { useRouter } from 'expo-router'
import React, { useEffect, useState } from 'react'
import { KeyboardAvoidingView, ScrollView, Text, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { describeFailure, testConnection } from '@/core/api/connectionTest'
import { cloudEndpoints, isValidTailnetFqdn } from '@/core/config/endpoints'
import { loadServerConfig, saveServerConfig } from '@/core/config/storage'
import type { ServerConfig, ServerPreset } from '@/core/config/types'
import { settingsStore } from '@/core/settings/store'
import { AuroraOrb } from '@/ui/aurora'
import { Button } from '@/ui/Button'
import { Segmented } from '@/ui/Segmented'
import { TextField } from '@/ui/TextField'
import { usePalette } from '@/ui/theme'
import { RADIUS, textStyle } from '@/ui/tokens'

const ALLOW_CUSTOM =
  (Constants.expoConfig?.extra as { allowCustomServer?: boolean } | undefined)
    ?.allowCustomServer !== false

const URL_RE = /^https?:\/\/.+/

type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'ok' }
  | { kind: 'fail'; message: string }

export default function Onboarding() {
  const router = useRouter()
  const [preset, setPreset] = useState<ServerPreset>('cloud')
  const [fqdn, setFqdn] = useState('')
  const [edgeUrl, setEdgeUrl] = useState('')
  const [audioUrl, setAudioUrl] = useState('')
  const [token, setToken] = useState('')
  const [savedTokenTail, setSavedTokenTail] = useState('')
  const [test, setTest] = useState<TestState>({ kind: 'idle' })

  useEffect(() => {
    loadServerConfig().then((c) => {
      if (!c) return
      setPreset(c.preset)
      setFqdn(c.fqdn ?? '')
      setEdgeUrl(c.edgeUrl)
      setAudioUrl(c.audioUrl)
      setSavedTokenTail(c.token.slice(-4))
    })
  }, [])

  const derived = preset === 'cloud' && isValidTailnetFqdn(fqdn.trim()) ? cloudEndpoints(fqdn.trim()) : null
  const effectiveEdge = preset === 'cloud' ? (derived?.edgeUrl ?? '') : edgeUrl.trim()
  const effectiveAudio = preset === 'cloud' ? (derived?.audioUrl ?? '') : audioUrl.trim()
  const urlsValid =
    preset === 'cloud'
      ? derived !== null
      : URL_RE.test(effectiveEdge) && URL_RE.test(effectiveAudio)
  const canTest = urlsValid && token.trim().length > 0
  const canSave = canTest

  async function onTest() {
    if (!canTest) return
    setTest({ kind: 'testing' })
    const r = await testConnection(effectiveEdge, token.trim())
    setTest(r.ok ? { kind: 'ok' } : { kind: 'fail', message: describeFailure(r) })
  }

  async function onSave() {
    if (!canSave) return
    const cfg: ServerConfig = {
      preset,
      ...(preset === 'cloud' ? { fqdn: fqdn.trim() } : {}),
      edgeUrl: effectiveEdge,
      audioUrl: effectiveAudio,
      token: token.trim(),
    }
    await saveServerConfig(cfg)
    router.replace('/')
  }

  const presets: { key: ServerPreset; label: string }[] = [
    { key: 'cloud', label: '云栈（Tailnet）' },
    ...(ALLOW_CUSTOM
      ? ([
          { key: 'lan', label: '局域网' },
          { key: 'custom', label: '自定义' },
        ] as { key: ServerPreset; label: string }[])
      : []),
  ]

  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const fs = settings.fontScale
  const caption = textStyle('caption', fs)
  const body = textStyle('bodyM', fs)
  const group = (title: string, children: React.ReactNode) => (
    <View style={{ backgroundColor: p.surface, borderRadius: RADIUS.lg, padding: 16, gap: 12 }}>
      <Text style={[textStyle('titleM', fs), { color: p.fg1 }]}>{title}</Text>
      {children}
    </View>
  )
  const fqdnError = fqdn.trim().length > 0 && !derived ? '域名格式不对（须形如 xxx.ts.net，全小写）' : undefined
  // 权限用途（合规落点，app.config.ts:99-101）：三条照旧逐字说清——画板上的短版丢了「默认关 / 不持久化 / 不落盘」，不采用
  const permissions: [string, string][] = [
    ['麦克风', '按住光球说话时才开；免唤醒与端到端默认关，要你在设置里显式打开。'],
    ['定位', '只在位置相关请求时取一次坐标，坐标不持久化。'],
    ['摄像头', '只有开了「看图问答」且说出看图的话时才拍一张，不落盘不进记忆。'],
  ]

  // 键盘避让（B1-12）：Android 上 `behavior=undefined` 等于什么都不做，而 edge-to-edge 下
  // 系统的 adjustResize 也没把内容顶上去 ⇒ 两端都用 `padding`，由 RN 按键盘高度补底。
  // **不改 app.config 的 softwareKeyboardLayoutMode**——那是原生配置，动它要重建，B1 零原生变更。
  // 真机读数：「保存并进入」被键盘完全盖住、页面不上移（`e2e/artifacts/b1-12-onboarding-kbd.png`）。
  // v3 P5c（Figma 04 页 O 组）：两个动作钉在底栏（滚动区之外），键盘弹起时随避让一起上移、永远看得见。
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <SafeAreaView style={{ flex: 1 }} edges={['top', 'bottom']}>
        <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
          <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 24, paddingBottom: 16, gap: 16 }} keyboardShouldPersistTaps="handled">
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 16, paddingHorizontal: 4 }}>
              <AuroraOrb size={56} state="idle" animated />
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={[textStyle('headline', fs), { color: p.fg1 }]}>我是{settings.assistantName}</Text>
                <Text style={[body, { color: p.fg2 }]}>先连上你的座舱服务器</Text>
              </View>
            </View>

            {group(
              '服务器',
              <>
                {/* prod 构建只有云栈一个预设 ⇒ 不出选择器（Figma O 组注）；局域网 / 自定义只在允许自定义的构建出现 */}
                {presets.length > 1 ? (
                  <Segmented
                    p={p}
                    fontScale={fs}
                    value={preset}
                    options={presets.map((it) => ({ value: it.key, label: it.label }))}
                    onChange={(key) => {
                      setPreset(key)
                      setTest({ kind: 'idle' })
                    }}
                  />
                ) : null}
                {preset === 'cloud' ? (
                  <TextField
                    p={p}
                    fontScale={fs}
                    label="Tailnet 域名"
                    value={fqdn}
                    onChangeText={(v) => {
                      setFqdn(v)
                      setTest({ kind: 'idle' })
                    }}
                    placeholder="your-machine.tailxxxx.ts.net"
                    autoCapitalize="none"
                    autoCorrect={false}
                    error={fqdnError}
                    helper={derived ? `主链 ${derived.edgeUrl} ｜ 音频 ${derived.audioUrl}` : '只填域名，不带 https://'}
                  />
                ) : (
                  <>
                    <TextField
                      p={p}
                      fontScale={fs}
                      label="主链入口（edge）"
                      value={edgeUrl}
                      onChangeText={(v) => {
                        setEdgeUrl(v)
                        setTest({ kind: 'idle' })
                      }}
                      placeholder="http://192.168.1.10:18000"
                      autoCapitalize="none"
                      autoCorrect={false}
                    />
                    <TextField
                      p={p}
                      fontScale={fs}
                      label="音频入口（audio）"
                      value={audioUrl}
                      onChangeText={setAudioUrl}
                      placeholder="http://192.168.1.10:50059"
                      autoCapitalize="none"
                      autoCorrect={false}
                    />
                  </>
                )}
              </>,
            )}

            {group(
              '访问 token',
              <TextField
                p={p}
                fontScale={fs}
                label="粘贴服务器给你的访问 token"
                value={token}
                onChangeText={(v) => {
                  setToken(v)
                  setTest({ kind: 'idle' })
                }}
                placeholder="粘贴访问 token"
                autoCapitalize="none"
                autoCorrect={false}
                secureTextEntry
                helper={savedTokenTail ? `当前 ····${savedTokenTail}；只存本机安全存储，不进日志` : '只存本机安全存储，不进日志'}
              />,
            )}

            {/* 权限用途文案的合规落点（app.config.ts:99-101 的注释点名要它：
                manifest 里写了字不算合规，用户要在**要权限之前**看到为什么要） */}
            {group(
              '之后会用到的权限',
              <View style={{ gap: 8 }}>
                {permissions.map(([k, v]) => (
                  <Text key={k} style={[body, { color: p.fg2 }]}>
                    <Text style={{ color: p.fg1 }}>{k}：</Text>
                    {v}
                  </Text>
                ))}
              </View>,
            )}

            {test.kind === 'testing' ? <Text testID="onboarding-testing" style={[caption, { color: p.fg2 }]}>正在握手…</Text> : null}
            {test.kind === 'ok' ? <Text style={[caption, { color: p.green }]}>✓ 连接成功（握手通过）</Text> : null}
            {test.kind === 'fail' ? <Text style={[caption, { color: p.red }]}>{test.message}</Text> : null}
          </ScrollView>

          <View style={{ flexDirection: 'row', gap: 12, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 16 }}>
            <Button
              p={p}
              testID="onboarding-test"
              variant="tonal"
              fontScale={fs}
              label="连接测试"
              disabled={!canTest || test.kind === 'testing'}
              onPress={onTest}
              style={{ flex: 1 }}
            />
            <Button
              p={p}
              testID="onboarding-save"
              variant="filled"
              fontScale={fs}
              label="保存并进入"
              disabled={!canSave}
              onPress={onSave}
              style={{ flex: 1 }}
            />
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  )
}
