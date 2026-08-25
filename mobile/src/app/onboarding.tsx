// 首启引导（实施计划 M0-5）：选预设 → 填 FQDN/URL → 填 token（显尾 4 位）→ 连接测试 → 保存。
// cloud 预设：FQDN 校验（与 dev_stack_lib.cloud_endpoints 同构）后派生 8443/8444 两条 URL。
// prod 档不展示 lan/custom（app.config.ts extra.allowCustomServer）。
import Constants from 'expo-constants'
import { useRouter } from 'expo-router'
import { useEffect, useState } from 'react'
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'

import { describeFailure, testConnection } from '@/core/api/connectionTest'
import { cloudEndpoints, isValidTailnetFqdn } from '@/core/config/endpoints'
import { loadServerConfig, saveServerConfig } from '@/core/config/storage'
import type { ServerConfig, ServerPreset } from '@/core/config/types'

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

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.label}>服务器预设</Text>
        <View style={styles.row}>
          {presets.map((p) => (
            <Pressable
              key={p.key}
              onPress={() => {
                setPreset(p.key)
                setTest({ kind: 'idle' })
              }}
              style={[styles.chip, preset === p.key && styles.chipActive]}
            >
              <Text style={preset === p.key ? styles.chipTextActive : styles.chipText}>
                {p.label}
              </Text>
            </Pressable>
          ))}
        </View>

        {preset === 'cloud' ? (
          <View>
            <Text style={styles.label}>Tailnet FQDN</Text>
            <TextInput
              style={styles.input}
              value={fqdn}
              onChangeText={(v) => {
                setFqdn(v)
                setTest({ kind: 'idle' })
              }}
              placeholder="your-machine.tailxxxx.ts.net"
              autoCapitalize="none"
              autoCorrect={false}
            />
            {fqdn.trim().length > 0 && !derived && (
              <Text style={styles.error}>FQDN 不合法（须形如 xxx.ts.net，全小写）</Text>
            )}
            {derived && (
              <Text style={styles.hint}>
                主链 {derived.edgeUrl} ｜ 音频 {derived.audioUrl}
              </Text>
            )}
          </View>
        ) : (
          <View>
            <Text style={styles.label}>主链入口（edge）</Text>
            <TextInput
              style={styles.input}
              value={edgeUrl}
              onChangeText={(v) => {
                setEdgeUrl(v)
                setTest({ kind: 'idle' })
              }}
              placeholder="http://192.168.1.10:18000"
              autoCapitalize="none"
              autoCorrect={false}
            />
            <Text style={styles.label}>音频入口（audio）</Text>
            <TextInput
              style={styles.input}
              value={audioUrl}
              onChangeText={setAudioUrl}
              placeholder="http://192.168.1.10:50059"
              autoCapitalize="none"
              autoCorrect={false}
            />
          </View>
        )}

        <Text style={styles.label}>
          访问 token{savedTokenTail ? `（当前 ····${savedTokenTail}）` : ''}
        </Text>
        <TextInput
          style={styles.input}
          value={token}
          onChangeText={(v) => {
            setToken(v)
            setTest({ kind: 'idle' })
          }}
          placeholder="AUTH_TOKENS 条目的 token 段"
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
        />

        <Pressable
          onPress={onTest}
          disabled={!canTest || test.kind === 'testing'}
          style={[styles.button, (!canTest || test.kind === 'testing') && styles.buttonDisabled]}
        >
          <Text style={styles.buttonText}>
            {test.kind === 'testing' ? '连接中…' : '连接测试'}
          </Text>
        </Pressable>
        {test.kind === 'ok' && <Text style={styles.ok}>✓ 连接成功（握手通过）</Text>}
        {test.kind === 'fail' && <Text style={styles.error}>{test.message}</Text>}

        <Pressable
          onPress={onSave}
          disabled={!canSave}
          style={[styles.buttonPrimary, !canSave && styles.buttonDisabled]}
        >
          <Text style={styles.buttonText}>保存并进入</Text>
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  container: { padding: 24, gap: 10 },
  label: { fontSize: 14, color: '#555', marginTop: 12 },
  row: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  chip: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 18,
    backgroundColor: '#eee',
  },
  chipActive: { backgroundColor: '#208AEF' },
  chipText: { color: '#333' },
  chipTextActive: { color: '#fff' },
  input: {
    borderWidth: 1,
    borderColor: '#ccc',
    borderRadius: 8,
    padding: 12,
    fontSize: 15,
    marginTop: 6,
  },
  button: {
    marginTop: 20,
    padding: 14,
    borderRadius: 10,
    backgroundColor: '#5a6b7a',
    alignItems: 'center',
  },
  buttonPrimary: {
    marginTop: 12,
    padding: 14,
    borderRadius: 10,
    backgroundColor: '#208AEF',
    alignItems: 'center',
  },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { color: '#fff', fontSize: 16 },
  ok: { color: '#1a7f37', marginTop: 8 },
  error: { color: '#c62828', marginTop: 8 },
  hint: { color: '#888', marginTop: 6, fontSize: 13 },
})
