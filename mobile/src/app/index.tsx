// 首页（M0 极简版）：有配置 → 摘要 + 入口；无配置 → 引导页。正式对话 UI 是 M1-3。
import { Link, Redirect, useFocusEffect } from 'expo-router'
import { useCallback, useState } from 'react'
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native'

import { loadServerConfig } from '@/core/config/storage'
import type { ServerConfig } from '@/core/config/types'

export default function Home() {
  const [state, setState] = useState<'loading' | 'missing' | 'ready'>('loading')
  const [cfg, setCfg] = useState<ServerConfig | null>(null)

  useFocusEffect(
    useCallback(() => {
      let alive = true
      loadServerConfig().then((c) => {
        if (!alive) return
        setCfg(c)
        setState(c ? 'ready' : 'missing')
      })
      return () => {
        alive = false
      }
    }, []),
  )

  if (state === 'loading') {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    )
  }
  if (state === 'missing' || !cfg) return <Redirect href="/onboarding" />

  return (
    <View style={styles.container}>
      <Text style={styles.title}>小舟随行</Text>
      <Text style={styles.line}>服务器：{cfg.edgeUrl}</Text>
      <Text style={styles.line}>token：····{cfg.token.slice(-4)}</Text>
      <Link href="/debug" style={styles.button}>
        打开调试屏（主链帧）
      </Link>
      <Link href="/onboarding" style={styles.buttonSecondary}>
        重新配置服务器
      </Link>
      <Text style={styles.hint}>对话界面随 M1 交付；当前为 M0 地基版。</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  container: { flex: 1, padding: 24, gap: 12 },
  title: { fontSize: 28, fontWeight: '700', marginBottom: 8 },
  line: { fontSize: 15, color: '#444' },
  button: {
    marginTop: 16,
    padding: 14,
    borderRadius: 10,
    backgroundColor: '#208AEF',
    color: '#fff',
    textAlign: 'center',
    fontSize: 16,
    overflow: 'hidden',
  },
  buttonSecondary: {
    padding: 14,
    borderRadius: 10,
    backgroundColor: '#eee',
    color: '#333',
    textAlign: 'center',
    fontSize: 16,
    overflow: 'hidden',
  },
  hint: { marginTop: 24, color: '#888', fontSize: 13 },
})
