// 对话主屏（实施计划 M1-3/M1-6/M1-7 装配）：
//  - GatewaySession（下行帧→SessionCore.handleFrame，状态→connStatus）
//  - 双形态外壳：窗口短边 ≥600dp 平板双栏（右=车况摘要+最近卡片聚焦），旋转即时切
//  - 确认条按台账渲染（isPendingLive），位置征询条只激活最新一条
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
import { usePalette } from '../../ui/theme'
import { CardRenderer } from '../cards/CardRenderer'
import { VehiclePanel } from '../vehicle/VehiclePanel'
import { Composer } from './Composer'
import { MessageBubble } from './MessageBubble'

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
  return <ChatBody p={p} wired={wired} tablet={tablet} />
}

function ChatBody({
  p,
  wired,
  tablet,
}: {
  p: ReturnType<typeof usePalette>
  wired: Wired
  tablet: boolean
}) {
  const { core } = wired
  const { messages, pendingOps, vehState, connStatus, pendingLocationText } = useStore(core.store)
  const { settings } = useStore(settingsStore)

  const onSend = useCallback(
    (text: string, metaExtra?: Record<string, string>) => core.send(text, metaExtra),
    [core],
  )
  const onConfirm = useCallback(
    (reply: '确认' | '取消', operationId?: string) => core.confirmReply(reply, operationId),
    [core],
  )
  const onInterrupt = useCallback(() => core.cancelCurrentTurn(), [core])

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

  const dotColor = connStatus === 'open' ? p.green : connStatus === 'connecting' ? p.amber : p.red

  const chatColumn = (
    <View style={{ flex: 1 }}>
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
        contentContainerStyle={{ paddingVertical: 8 }}
      />
      <Composer
        p={p}
        quickCommands={settings.quickCommands}
        busy={busy}
        onSend={onSend}
        onInterrupt={onInterrupt}
      />
    </View>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: p.bg }} edges={['bottom']}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 8,
            paddingHorizontal: 14,
            paddingVertical: 10,
            borderBottomWidth: 1,
            borderColor: p.line,
          }}
        >
          <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: dotColor }} />
          <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '700', flex: 1 }}>
            {settings.assistantName}随行
          </Text>
          {!tablet ? (
            <Link href="/vehicle" style={{ color: p.accent, fontSize: p.font(14), padding: 6 }}>
              车辆
            </Link>
          ) : null}
          <Link href="/settings" style={{ color: p.accent, fontSize: p.font(14), padding: 6 }}>
            设置
          </Link>
        </View>
        {tablet ? (
          <View style={{ flex: 1, flexDirection: 'row' }}>
            {chatColumn}
            <View style={{ width: 340, borderLeftWidth: 1, borderColor: p.line }}>
              <ScrollView>
                <Text style={{ color: p.fg3, fontSize: p.font(12), padding: 12, paddingBottom: 0 }}>
                  车况
                </Text>
                <VehiclePanel p={p} vehState={vehState} />
                {latestCard ? (
                  <View style={{ padding: 12, gap: 6 }}>
                    <Text style={{ color: p.fg3, fontSize: p.font(12) }}>最近卡片</Text>
                    <CardRenderer p={p} card={latestCard} onSend={onSend} />
                  </View>
                ) : null}
              </ScrollView>
            </View>
          </View>
        ) : (
          chatColumn
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}
