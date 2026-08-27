// 卡片画廊调试屏（M3-1/M3-4）。与「主链帧调试屏」「语音 spike」同类：dev 取证入口，不进主链路。
//
// 它解决两个验收要求，都不是真栈能独立完成的：
//  · §8.3「全卡族截图归档」——一部分卡型真栈够不着（商户要营业时间、payment_receipt
//    要真付款而契约 §9.17 明说系统不执行最终付款），还有一部分边界分支真栈几乎不产
//    （付款码过期置灰、充电路线「全程无需补电」）。
//  · M3-4「深浅主题全卡族过一遍，跟随系统切换即时生效」——一屏切主题，比逐张跑真栈现实。
//
// ⚠ 屏内每条都标了「真栈」/「样本」：**样本截图不是读数**，它只证明渲染器对这份数据的
// 输出长这样，不证明后端会发这样的数据。两者混为一谈正是验收造假的常见形态。
import { useLocalSearchParams } from 'expo-router'
import { useMemo, useState } from 'react'
import { ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { settingsStore } from '@/core/settings/store'
import { CardRenderer, KNOWN_CARD_TYPES } from '@/features/cards/CardRenderer'
import { cardFixtures } from '@/features/cards/fixtures'
import { Chip } from '@/features/cards/parts'
import { usePalette } from '@/ui/theme'

export default function CardGallery() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const [log, setLog] = useState<string[]>([])
  // `?only=payment_qr` 只渲染匹配的卡型（子串匹配，逗号分隔多个）。
  // 加它的理由很实际：全表 40 条时用 adb 滚动定位极不可靠——慢拖会被卡内 Pressable
  // 吃掉、快扫又带惯性，同一条指令时灵时不灵。**取证屏就该能直达要取的那一条。**
  const { only } = useLocalSearchParams<{ only?: string }>()
  // 每次挂载重算：付款码倒计时、提醒「今天/明天」都是相对量（见 fixtures.ts 的说明）
  const all = useMemo(() => cardFixtures(), [])
  const fixtures = useMemo(() => {
    const keys = (only || '').split(',').map((k) => k.trim()).filter(Boolean)
    if (!keys.length) return all
    return all.filter((f) => keys.some((k) => String(f.card?.type || '').includes(k)))
  }, [all, only])

  // 覆盖度自检：注册表里有、但画廊没有样本的卡型 —— 归档时「少了谁」得当场看得见，
  // 不能等到事后数截图（数截图这件事没人会做第二次）。
  const uncovered = useMemo(() => {
    const covered = new Set(all.map((f) => f.card?.type))
    return KNOWN_CARD_TYPES.filter((t) => !covered.has(t))
  }, [all])

  return (
    <ScrollView style={{ flex: 1, backgroundColor: p.bg }} contentContainerStyle={{ padding: 12, gap: 14 }}>
      <View style={{ gap: 4 }}>
        <Text style={{ color: p.fg2, fontSize: p.font(12) }}>
          样本 {fixtures.length}
          {fixtures.length === all.length ? '' : `/${all.length}`} 条 / 注册卡型 {KNOWN_CARD_TYPES.length} 个
          {only ? `（已按 only=${only} 过滤）` : ''}
        </Text>
        <Text style={{ color: uncovered.length ? p.amber : p.fg3, fontSize: p.font(11) }}>
          {uncovered.length ? `未覆盖卡型：${uncovered.join('、')}` : '注册表卡型已全部有样本'}
        </Text>
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
          主题跟随设置页（system/dark/light）；卡内按钮在这里只记录不上行
        </Text>
      </View>

      {fixtures.map((f, i) => (
        <View key={`${f.label}:${i}`} style={{ gap: 6 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <Text style={{ color: p.fg3, fontSize: p.font(11), flex: 1 }}>
              {i + 1}. {f.label}
            </Text>
            <Chip p={p} tone={f.realStack ? 'accent' : 'plain'} text={f.realStack ? '真栈已验' : '样本'} />
          </View>
          <CardRenderer
            p={p}
            card={f.card}
            // 画廊里点按钮不上行——这屏是取证用的，误触发一次真实下单不值当
            onSend={(text) => setLog((prev) => [`${f.label} → ${text}`, ...prev].slice(0, 8))}
          />
        </View>
      ))}

      {log.length ? (
        <View style={{ gap: 2, borderTopWidth: 1, borderColor: p.line, paddingTop: 8 }}>
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>按钮回显（不上行）</Text>
          {log.map((l, i) => (
            <Text key={i} style={{ color: p.fg2, fontSize: p.font(11) }} numberOfLines={1}>
              {l}
            </Text>
          ))}
        </View>
      ) : null}
    </ScrollView>
  )
}
