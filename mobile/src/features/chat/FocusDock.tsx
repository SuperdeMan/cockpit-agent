// mobile/src/features/chat/FocusDock.tsx
// 承诺面（方案 §5.3）：读 `commitment[]`（钉一项 + 其余个数）与 `degradation[]`（有出口的降级）。
// 材质 **G0 实色**（§5.11：确认/错误/隐私说明不许半透明；坑账 §9.36 同判据）。
// 确认按钮比例照 A-6.4：取消 flex1 / 确认 flex2；剩余时间**只读共享 TTL**（commitment.ts）。
import { Linking, Pressable, Text, useWindowDimensions, View } from 'react-native'

import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { confirmRemainingMs, pinCommitment, type DockItem } from '@/core/presence/commitment'
import type { Degradation, PresenceSnapshot } from '@/core/presence/presence'
import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

import { dockLabelMode } from './dockLabel'

export interface FocusDockProps {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onConfirm(reply: '确认' | '取消', operationId?: string): void
  onCancelTurn(): void
  /** 「另有 N 个待处理 ›」点开 → 调用方决定怎么展示（B1：滚到列表底部即可） */
  onOthers?(): void
  onReenableBargeIn?(): void
}

function fmt(ms: number): string {
  const s = Math.ceil(ms / 1000)
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s}s`
}

export function FocusDock(props: FocusDockProps) {
  const { p, fontScale, snapshot } = props
  const pinned = pinCommitment(snapshot.commitment)
  const degradations = snapshot.degradation.filter((d) => d.kind !== 'transport_unknown' && d.kind !== 'recoverable_error')
  if (!pinned && !degradations.length) return null
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  return (
    <View testID="focus-dock" style={{ paddingHorizontal: 12, paddingBottom: 6, gap: 6 }}>
      {pinned ? <CommitmentCard {...props} item={pinned.item} others={pinned.others} solid={solid} /> : null}
      {degradations.map((d) => (
        // key 带上区分维：同一种 kind 上游今天最多 push 一次，但 mic + camera 两个
        // permission_denied 是随时会出现的形态，那时 `key={d.kind}` 就是 React key 冲突
        <DegradationRow
          key={`${d.kind}:${'what' in d ? d.what : 'reason' in d ? d.reason : ''}`}
          p={p}
          fontScale={fontScale}
          driving={props.snapshot.driving}
          d={d}
          solid={solid}
          onReenableBargeIn={props.onReenableBargeIn}
        />
      ))}
    </View>
  )
}

function CommitmentCard({
  p,
  fontScale,
  snapshot,
  item,
  others,
  solid,
  onConfirm,
  onCancelTurn,
  onOthers,
}: FocusDockProps & { item: DockItem; others: number; solid: string }) {
  // **时钟只有一个**：`usePresence` 已经在每秒 tick，`snapshot.now` 是那一份的读数。
  // 这里曾经自己起过一份 `setInterval`，而它在生产路径上是冻的——`derivePresence` 每秒现造
  // 新的 `DockItem`，`useEffect(…, [item])` 依赖的是对象引用 ⇒ 每秒 cleanup + 重建，本地
  // now 停在挂载那一刻。状态画廊里它反而会走（静态 snapshot、引用稳定）：**取证屏与生产
  // 路径在这一点上的输入形态相反，画廊绿证明不了生产绿**（第 2 批坑⑤）。
  const now = snapshot.now
  // B4-11 §6「目标 ≥56dp」：行车 56 / 泊车 48（TARGET 是唯一的一份，不在这里写数）
  const h = scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const border = item.kind === 'confirm' ? 'rgba(245,158,11,0.38)' : p.line
  // 右侧标签的让位（评审 ❌-1）：200% 字号下它随标题同比放大、把标题挤成「这..」。
  // 隐藏时把分类并进标题的读屏 label，信息不丢，只是不再抢那一行。
  const { fontScale: sysScale } = useWindowDimensions()
  const labelMode = dockLabelMode(fontScale, sysScale)
  const kindLabel = item.kind === 'confirm' ? (item.subkind === 'location' ? '位置授权' : '危险动作 · 需二次确认') : ''
  return (
    // ⚠ `accessibilityLiveRegion` **不在这一层**：这个子树里有每秒变的倒计时，挂在根上会让
    // TalkBack 每秒重播整张卡。live region 只挂在下面那些「内容变了才该播一次」的摘要行上。
    <View
      testID={item.kind === 'confirm' ? 'dock-confirm' : `dock-${item.kind}`}
      style={{
        backgroundColor: solid,
        borderRadius: RADIUS.lg,
        borderWidth: 1,
        borderColor: border,
        padding: 10,
        gap: 8,
        boxShadow: item.kind === 'confirm' ? '0 0 16px rgba(245,158,11,0.12), 0 8px 24px rgba(0,0,0,0.3)' : '0 8px 24px rgba(0,0,0,0.25)',
      }}
    >
      {item.kind === 'confirm' ? (
        <>
          <View accessibilityLiveRegion="assertive" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body, 'text', fontScale) }}>⚠</Text>
            <Text
              numberOfLines={2}
              accessibilityLabel={labelMode === 'hidden' ? `${kindLabel}：${item.summary}` : undefined}
              style={{ color: p.fg1, fontSize: scale(TYPE.body, 'text', fontScale), fontWeight: '600', flex: 1, flexShrink: 1 }}
            >
              {item.summary}
            </Text>
            {labelMode === 'full' ? (
              <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale), flexShrink: 0 }}>{kindLabel}</Text>
            ) : null}
          </View>
          {item.subkind !== 'location' ? (
            <View style={{ gap: 4 }}>
              <View style={{ height: 2, borderRadius: 1, backgroundColor: p.fill2, overflow: 'hidden' }}>
                <View
                  style={{
                    height: 2,
                    width: `${Math.round((confirmRemainingMs(item, now) / PENDING_TTL_MS) * 100)}%`,
                    backgroundColor: p.amber,
                  }}
                />
              </View>
              <Text testID="dock-countdown" style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>
                {fmt(confirmRemainingMs(item, now))} 后过期
              </Text>
            </View>
          ) : null}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              testID="dock-cancel"
              accessibilityRole="button"
              onPress={() => onConfirm('取消', item.subkind === 'location' ? undefined : item.id)}
              style={{ flex: 1, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: p.fill2, backgroundColor: p.fill, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>{item.subkind === 'location' ? '拒绝' : '取消'}</Text>
            </Pressable>
            <Pressable
              testID="dock-accept"
              accessibilityRole="button"
              onPress={() => onConfirm('确认', item.subkind === 'location' ? undefined : item.id)}
              style={{ flex: 2, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: 'rgba(245,158,11,0.38)', backgroundColor: p.amberSoft, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={{ color: p.amber, fontSize: scale(TYPE.body - 1, 'text', fontScale), fontWeight: '600' }}>{item.subkind === 'location' ? '允许' : '确认'}</Text>
            </Pressable>
          </View>
        </>
      ) : item.kind === 'task' ? (
        <View accessibilityLiveRegion="assertive" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: p.teal, fontSize: scale(TYPE.body, 'text', fontScale) }}>⟳</Text>
          <Text numberOfLines={1} style={{ color: p.fg1, fontSize: scale(TYPE.body - 1, 'text', fontScale), flex: 1 }}>{item.label}…</Text>
          <Pressable accessibilityRole="button" onPress={onCancelTurn} style={{ minHeight: h, paddingHorizontal: 12, justifyContent: 'center' }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>取消</Text>
          </Pressable>
        </View>
      ) : item.kind === 'queue' ? (
        <Text accessibilityLiveRegion="assertive" style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>
          {item.count} 条消息排队中，连上后自动补发
        </Text>
      ) : (
        <Text accessibilityLiveRegion="assertive" style={{ color: p.fg1, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>
          还差一个信息：{item.missing}
        </Text>
      )}
      {others > 0 ? (
        <Pressable onPress={onOthers} accessibilityRole="button">
          <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>另有 {others} 个待处理 ›</Text>
        </Pressable>
      ) : null}
    </View>
  )
}

const DEGRADATION_TEXT: Record<Exclude<Degradation['kind'], 'transport_unknown' | 'recoverable_error'>, (d: Degradation) => string> = {
  permission_denied: (d) => (d.kind === 'permission_denied' ? d.text : ''),
  service_degraded: (d) => (d.kind === 'service_degraded' ? d.text : ''),
  safety_blocked: (d) => (d.kind === 'safety_blocked' ? d.text : ''),
  audio_echo_degraded: () => '环境回声较强，本轮已关闭插话；点「停止播报」后再说',
  fatal: (d) => (d.kind === 'fatal' ? d.text : ''),
}

function DegradationRow({
  p,
  fontScale,
  driving,
  d,
  solid,
  onReenableBargeIn,
}: {
  p: Palette
  fontScale: FontScalePref
  driving: boolean
  d: Degradation
  solid: string
  onReenableBargeIn?(): void
}) {
  if (d.kind === 'transport_unknown' || d.kind === 'recoverable_error') return null
  const text = DEGRADATION_TEXT[d.kind](d)
  const action =
    d.kind === 'permission_denied'
      ? { label: '去系统设置', run: () => void Linking.openSettings() }
      : d.kind === 'audio_echo_degraded' && onReenableBargeIn
        ? { label: '重新开启插话', run: onReenableBargeIn }
        : null
  return (
    <View
      testID={`dock-${d.kind}`}
      style={{ backgroundColor: solid, borderRadius: RADIUS.md, borderWidth: 1, borderColor: d.kind === 'safety_blocked' || d.kind === 'fatal' ? 'rgba(239,68,68,0.35)' : p.line, padding: 10, flexDirection: 'row', alignItems: 'center', gap: 8 }}
    >
      <Text style={{ color: p.fg2, fontSize: scale(TYPE.caption, 'text', fontScale), flex: 1 }}>{text}</Text>
      {action ? (
        <Pressable accessibilityRole="button" onPress={action.run} style={{ minHeight: scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 8 }}>
          <Text style={{ color: p.accent, fontSize: scale(TYPE.caption, 'text', fontScale) }}>{action.label}</Text>
        </Pressable>
      ) : null}
    </View>
  )
}
