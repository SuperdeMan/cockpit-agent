// 车况镜像（实施计划 M1-7 简版）：vehicle_state 帧的整体镜像直显——电量高亮、
// 开关态转 开/关。完整推导（vehicleStage.mjs）属 M3-2，此处刻意不引（白名单 phase 闸）。
import { ScrollView, Text, View } from 'react-native'

import type { Palette } from '../../ui/theme'

const KEY_LABEL: Record<string, string> = {
  battery: '电量',
  soc: '电量',
  range_km: '续航',
  window: '车窗',
  windows: '车窗',
  sunroof: '天窗',
  door: '车门',
  doors: '车门',
  lock: '车锁',
  hvac: '空调',
  ac: '空调',
  temperature: '温度',
  temp: '温度',
  seat_heat: '座椅加热',
  light: '灯光',
  lights: '灯光',
  media: '媒体',
}

function displayValue(v: unknown): string {
  if (typeof v === 'boolean') return v ? '开' : '关'
  if (v === 'on' || v === 'open' || v === 'true') return '开'
  if (v === 'off' || v === 'closed' || v === 'false') return '关'
  if (v && typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function VehiclePanel({ p, vehState }: { p: Palette; vehState: Record<string, unknown> }) {
  const entries = Object.entries(vehState)
  const isBattery = (k: string) => /battery|soc/i.test(k)
  const battery = entries.find(([k]) => isBattery(k))
  return (
    <ScrollView contentContainerStyle={{ padding: 14, gap: 10 }}>
      {battery ? (
        <View
          style={{
            backgroundColor: p.card,
            borderColor: p.line,
            borderWidth: 1,
            borderRadius: 14,
            padding: 14,
            alignItems: 'center',
          }}
        >
          <Text style={{ color: p.fg3, fontSize: p.font(12) }}>电量</Text>
          <Text style={{ color: p.green, fontSize: p.font(32), fontWeight: '700' }}>
            {displayValue(battery[1])}
            {typeof battery[1] === 'number' ? '%' : ''}
          </Text>
        </View>
      ) : null}
      {!entries.length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(13) }}>
          等待车况镜像…（连上网关后 vehicle_state 帧会推全量）
        </Text>
      ) : null}
      <View
        style={{
          backgroundColor: p.card,
          borderColor: p.line,
          borderWidth: 1,
          borderRadius: 14,
          paddingHorizontal: 14,
          paddingVertical: 6,
        }}
      >
        {entries
          .filter(([k]) => !isBattery(k))
          .map(([k, v]) => (
            <View
              key={k}
              style={{
                flexDirection: 'row',
                justifyContent: 'space-between',
                paddingVertical: 7,
                borderBottomWidth: 1,
                borderColor: p.line,
              }}
            >
              <Text style={{ color: p.fg2, fontSize: p.font(13) }}>{KEY_LABEL[k] || k}</Text>
              <Text style={{ color: p.fg1, fontSize: p.font(13) }} numberOfLines={1}>
                {displayValue(v)}
              </Text>
            </View>
          ))}
      </View>
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>车况镜像 · 与座舱实时同步（只读）</Text>
    </ScrollView>
  )
}
