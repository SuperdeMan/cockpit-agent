// 首启引导（实施计划 M0-5）：选预设 → 填 FQDN/URL → 填 token（显尾 4 位）→ 连接测试 → 保存。
// cloud 预设：FQDN 校验（与 dev_stack_lib.cloud_endpoints 同构）后派生 8443/8444 两条 URL。
// prod 档不展示 lan/custom（app.config.ts extra.allowCustomServer）。
//
// UX v2.1 B1-13：这一屏**是用户见到的第一面**，此前它是一张系统默认样式的表单——
// 和进去之后的极光座舱是两个产品。上品牌只动渲染层：Aurora 底 + 玻璃分区 + 光球 + 色板 token
// + 安全区；**`onTest` / `onSave` / `derived` / `presets` 与全部 state 一行不改**。
// 顺带补上权限用途文案的落点（app.config.ts:99-101 的注释点名要它：manifest 里写了字不算合规）。
import Constants from 'expo-constants'
import { useRouter } from 'expo-router'
import React, { useEffect, useState } from 'react'
import { KeyboardAvoidingView, Pressable, ScrollView, Text, TextInput, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { describeFailure, testConnection } from '@/core/api/connectionTest'
import { cloudEndpoints, isValidTailnetFqdn } from '@/core/config/endpoints'
import { loadServerConfig, saveServerConfig } from '@/core/config/storage'
import type { ServerConfig, ServerPreset } from '@/core/config/types'
import { settingsStore } from '@/core/settings/store'
import { AuroraBackground, AuroraOrb, Glass } from '@/ui/aurora'
import { AURORA, usePalette } from '@/ui/theme'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'

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
  const label = (t: string) => (
    <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fs), marginTop: 6 }}>{t}</Text>
  )
  const input = (props: React.ComponentProps<typeof TextInput>) => (
    <TextInput
      placeholderTextColor={p.fg3}
      {...props}
      style={{
        backgroundColor: p.fill,
        borderWidth: 1,
        borderColor: p.fill2,
        borderRadius: RADIUS.md,
        paddingHorizontal: 12,
        minHeight: scale(TARGET.parked, 'target', fs),
        color: p.fg1,
        fontSize: scale(TYPE.body, 'text', fs),
      }}
    />
  )
  const button = (text: string, run: () => void, primary: boolean, disabled: boolean) => (
    <Pressable
      onPress={run}
      disabled={disabled}
      accessibilityRole="button"
      style={{
        minHeight: scale(TARGET.parked, 'target', fs),
        borderRadius: RADIUS.lg,
        alignItems: 'center',
        justifyContent: 'center',
        opacity: disabled ? 0.4 : 1,
        ...(primary
          ? { experimental_backgroundImage: AURORA.gradient, boxShadow: '0 4px 22px rgba(91,140,255,0.45)' }
          : { backgroundColor: p.fill, borderWidth: 1, borderColor: p.fill2 }),
      }}
    >
      <Text style={{ color: primary ? '#fff' : p.fg1, fontSize: scale(TYPE.body, 'text', fs), fontWeight: '600' }}>
        {text}
      </Text>
    </Pressable>
  )

  // 键盘避让（B1-12）：Android 上 `behavior=undefined` 等于什么都不做，而 edge-to-edge 下
  // 系统的 adjustResize 也没把内容顶上去 ⇒ 两端都用 `padding`，由 RN 按键盘高度补底。
  // **不改 app.config 的 softwareKeyboardLayoutMode**——那是原生配置，动它要重建，B1 零原生变更。
  // 真机读数：「保存并进入」被键盘完全盖住、页面不上移（`e2e/artifacts/b1-12-onboarding-kbd.png`）。
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <SafeAreaView style={{ flex: 1 }} edges={['top', 'bottom']}>
        <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
          <ScrollView contentContainerStyle={{ padding: 20, gap: 14 }} keyboardShouldPersistTaps="handled">
            <View style={{ alignItems: 'center', gap: 8, paddingVertical: 12 }}>
              <AuroraOrb size={88} state="idle" animated />
              <Text
                style={{
                  color: p.fg1,
                  fontSize: scale(TYPE.display - 6, 'text', fs),
                  fontWeight: '600',
                  marginTop: 8,
                }}
              >
                我是{settings.assistantName}
              </Text>
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fs) }}>先连上你的座舱服务器</Text>
            </View>

            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>服务器</Text>
              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
                {presets.map((it) => (
                  <Pressable
                    key={it.key}
                    onPress={() => {
                      setPreset(it.key)
                      setTest({ kind: 'idle' })
                    }}
                    accessibilityRole="button"
                    style={{
                      paddingVertical: 8,
                      paddingHorizontal: 14,
                      borderRadius: RADIUS.full,
                      backgroundColor: preset === it.key ? p.accentSoft : p.fill,
                      borderWidth: 1,
                      borderColor: preset === it.key ? p.accent : p.fill2,
                    }}
                  >
                    <Text
                      style={{
                        color: preset === it.key ? p.accent : p.fg2,
                        fontSize: scale(TYPE.caption, 'text', fs),
                      }}
                    >
                      {it.label}
                    </Text>
                  </Pressable>
                ))}
              </View>
              {preset === 'cloud' ? (
                <View>
                  {label('Tailnet FQDN')}
                  {input({
                    value: fqdn,
                    onChangeText: (v) => {
                      setFqdn(v)
                      setTest({ kind: 'idle' })
                    },
                    placeholder: 'your-machine.tailxxxx.ts.net',
                    autoCapitalize: 'none',
                    autoCorrect: false,
                  })}
                  {fqdn.trim().length > 0 && !derived && (
                    <Text style={{ color: p.red, fontSize: scale(TYPE.caption, 'text', fs), marginTop: 6 }}>
                      FQDN 不合法（须形如 xxx.ts.net，全小写）
                    </Text>
                  )}
                  {derived && (
                    <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fs), marginTop: 6 }}>
                      主链 {derived.edgeUrl} ｜ 音频 {derived.audioUrl}
                    </Text>
                  )}
                </View>
              ) : (
                <View>
                  {label('主链入口（edge）')}
                  {input({
                    value: edgeUrl,
                    onChangeText: (v) => {
                      setEdgeUrl(v)
                      setTest({ kind: 'idle' })
                    },
                    placeholder: 'http://192.168.1.10:18000',
                    autoCapitalize: 'none',
                    autoCorrect: false,
                  })}
                  {label('音频入口（audio）')}
                  {input({
                    value: audioUrl,
                    onChangeText: setAudioUrl,
                    placeholder: 'http://192.168.1.10:50059',
                    autoCapitalize: 'none',
                    autoCorrect: false,
                  })}
                </View>
              )}
            </Glass>

            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>访问 token</Text>
              {label(savedTokenTail ? `当前 ····${savedTokenTail}；只存本机安全存储，不进日志` : '只存本机安全存储，不进日志')}
              {input({
                value: token,
                onChangeText: (v) => {
                  setToken(v)
                  setTest({ kind: 'idle' })
                },
                placeholder: 'AUTH_TOKENS 条目的 token 段',
                autoCapitalize: 'none',
                autoCorrect: false,
                secureTextEntry: true,
              })}
            </Glass>

            {/* 权限用途文案的合规落点（app.config.ts:99-101 的注释点名要它：
                manifest 里写了字不算合规，用户要在**要权限之前**看到为什么要） */}
            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>
                之后会用到的权限
              </Text>
              <Text
                style={{
                  color: p.fg2,
                  fontSize: scale(TYPE.caption, 'text', fs),
                  lineHeight: scale(18, 'line', fs),
                }}
              >
                麦克风：按住光球说话时才开；免唤醒与端到端默认关，要你在设置里显式打开。{'\n'}
                定位：只在位置相关请求时取一次坐标，坐标不持久化。{'\n'}
                摄像头：只有开了「看图问答」且说出看图的话时才拍一张，不落盘不进记忆。
              </Text>
            </Glass>

            {button(test.kind === 'testing' ? '连接中…' : '连接测试', onTest, false, !canTest || test.kind === 'testing')}
            {test.kind === 'ok' && (
              <Text style={{ color: p.green, fontSize: scale(TYPE.caption, 'text', fs) }}>✓ 连接成功（握手通过）</Text>
            )}
            {test.kind === 'fail' && (
              <Text style={{ color: p.red, fontSize: scale(TYPE.caption, 'text', fs) }}>{test.message}</Text>
            )}
            {button('保存并进入', onSave, true, !canSave)}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  )
}
