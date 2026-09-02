// dev 调试屏（实施计划 M0-6）：§2.3 下行 8 型帧原样落屏（滚动 JSON 列表），
// 上行帧同列（↑）。M0 冒烟入口：发「今天天气怎么样」应先后出现 speech_delta×N 与
// final（ui_card.type==='weather'）；断 Tailscale 再发 → 重连后自动 flush 送达。
import { Redirect } from 'expo-router'
import { useEffect, useRef, useState } from 'react'
import {
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'

import type { GatewayStatus } from '@/core/api/gateway'
import { GatewaySession } from '@/core/api/gateway'
import { loadServerConfig } from '@/core/config/storage'
import { getWired } from '@/core/session/wiring'

interface FrameRow {
  id: number
  at: string
  dir: 'up' | 'down'
  summary: string
  body: string
}

const MAX_ROWS = 300
const MAX_BODY = 800

function frameType(frame: unknown): string {
  if (frame && typeof frame === 'object' && 'type' in frame) {
    return String((frame as { type?: unknown }).type ?? '?')
  }
  if (frame && typeof frame === 'object' && 'text' in frame) return 'user_request'
  return '?'
}

export default function DebugScreen() {
  const [missing, setMissing] = useState(false)
  const [status, setStatus] = useState<GatewayStatus>('closed')
  const [rows, setRows] = useState<FrameRow[]>([])
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState('')
  const sessionRef = useRef<GatewaySession | null>(null)
  const seqRef = useRef(0)

  // 主动消息本地回放（B4-12）：帧形状照 `store.ts` 的 proactive 分支读的键
  const replayProactive = (priority: string) => {
    getWired()?.core.handleFrame({
      type: 'proactive',
      priority,
      speech: priority === 'critical' ? '后备箱没有关好' : '前面路况还行',
      advisory: 'scene_suggest',
      card: { type: 'scene_card', title: priority === 'critical' ? '后备箱未关' : '路况提示' },
      delivery_ids: ['local-' + Date.now()],
    })
  }

  useEffect(() => {
    let closed = false
    loadServerConfig().then((cfg) => {
      if (closed) return
      if (!cfg) {
        setMissing(true)
        return
      }
      const session = new GatewaySession(
        { edgeUrl: cfg.edgeUrl, token: cfg.token },
        {
          onStatus: setStatus,
          onFrame: (dir, frame) => {
            const body = JSON.stringify(frame)
            seqRef.current += 1
            const row: FrameRow = {
              id: seqRef.current,
              at: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
              dir,
              summary: frameType(frame),
              body: body.length > MAX_BODY ? `${body.slice(0, MAX_BODY)}…(${body.length}B)` : body,
            }
            setRows((prev) => [row, ...prev].slice(0, MAX_ROWS))
          },
        },
      )
      sessionRef.current = session
      setSessionId(session.sessionId)
      session.start()
    })
    return () => {
      closed = true
      sessionRef.current?.close()
      sessionRef.current = null
    }
  }, [])

  if (missing) return <Redirect href="/onboarding" />

  function onSend() {
    const text = input.trim()
    if (!text || !sessionRef.current) return
    sessionRef.current.sendText(text)
    setInput('')
  }

  // 键盘避让（B1-12）：Android 上 `behavior=undefined` 等于什么都不做，而 edge-to-edge 下
  // 系统的 adjustResize 也没把内容顶上去 ⇒ 两端都用 `padding`，由 RN 按键盘高度补底。
  // **不改 app.config 的 softwareKeyboardLayoutMode**——那是原生配置，动它要重建，B1 零原生变更。
  // 同 ChatScreen / onboarding：Android 上 `behavior=undefined` 等于什么都不做。
  return (
    <KeyboardAvoidingView style={styles.flex} behavior="padding">
      <View style={styles.header}>
        <View
          style={[
            styles.dot,
            status === 'open' ? styles.dotOpen : status === 'connecting' ? styles.dotConnecting : styles.dotClosed,
          ]}
        />
        <Text style={styles.headerText}>
          {status} ｜ {sessionId}
        </Text>
      </View>
      <FlatList
        style={styles.flex}
        data={rows}
        inverted
        keyExtractor={(r) => String(r.id)}
        renderItem={({ item }) => (
          <View style={styles.rowItem}>
            <Text style={styles.rowMeta}>
              {item.dir === 'up' ? '↑' : '↓'} {item.summary} · {item.at}
            </Text>
            <Text style={styles.rowBody}>{item.body}</Text>
          </View>
        )}
      />
      {/* B4-12 取证装置：本地回放一条主动消息帧，喂给**真的那个** SessionCore（getWired），
          于是对话页会长出气泡、播报仲裁会真的跑一遍。零后端——云栈没有 proactive 注入入口
          （`grep -rn proactive scripts/ observability/collector/server.py` 核过：零命中）。
          `delivery_ids` 每次唯一，否则幂等呈现会把第二次吞掉。 */}
      <View style={styles.composer}>
        <Pressable
          onPress={() => replayProactive('critical')}
          style={[styles.sendBtn, { backgroundColor: '#C2410C' }]}
        >
          <Text style={styles.sendText}>回放 critical</Text>
        </Pressable>
        <Pressable onPress={() => replayProactive('')} style={[styles.sendBtn, { backgroundColor: '#475569' }]}>
          <Text style={styles.sendText}>回放 普通（阴性）</Text>
        </Pressable>
      </View>
      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="发一句试试：今天天气怎么样"
          onSubmitEditing={onSend}
          returnKeyType="send"
        />
        <Pressable onPress={onSend} style={styles.sendBtn}>
          <Text style={styles.sendText}>发送</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: '#ddd',
  },
  headerText: { fontSize: 13, color: '#555' },
  dot: { width: 10, height: 10, borderRadius: 5 },
  dotOpen: { backgroundColor: '#1a7f37' },
  dotConnecting: { backgroundColor: '#e0a800' },
  dotClosed: { backgroundColor: '#c62828' },
  rowItem: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: '#eee',
  },
  rowMeta: { fontSize: 12, color: '#208AEF', fontWeight: '600' },
  rowBody: {
    fontSize: 11,
    color: '#333',
    fontFamily: Platform.select({ android: 'monospace', default: 'Courier' }),
  },
  composer: {
    flexDirection: 'row',
    gap: 8,
    padding: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderColor: '#ddd',
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderColor: '#ccc',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 15,
  },
  sendBtn: {
    backgroundColor: '#208AEF',
    borderRadius: 8,
    paddingHorizontal: 18,
    justifyContent: 'center',
  },
  sendText: { color: '#fff', fontSize: 15 },
})
