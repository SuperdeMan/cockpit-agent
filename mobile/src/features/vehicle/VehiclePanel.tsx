// 车况镜像（M1-7 立，M3-2 完整化）：vehicle_state 帧的镜像展示。
// 三格指标（电量/续航/挡位）复用 `@shared/vehicleStage.mjs::stageMetrics`——
// **推导住在共享模块**，两端不各算一份：续航是「有 range_km 就直用、没有就按满电折算」，
// 缺数据一律 '--' 而不是假装 62%（那条纪律是 HMI 侧写的，App 抄它的结论不抄它的代码）。
import { ScrollView, Text, View } from 'react-native'

import { stageMetrics } from '@shared/vehicleStage.mjs'

import type { Palette } from '../../ui/theme'

const KEY_LABEL: Record<string, string> = {
  battery: '电量',
  soc: '电量',
  range_km: '续航',
  gear: '挡位',
  speed: '车速',
  window: '车窗',
  windows: '车窗',
  sunroof: '天窗',
  door: '车门',
  doors: '车门',
  lock: '车锁',
  trunk: '后备箱',
  frunk: '前备箱',
  charge_port: '充电口盖',
  fuel_cap: '油箱盖',
  hvac: '空调',
  ac: '空调',
  temperature: '温度',
  temp: '温度',
  fan_speed: '风量',
  seat_heat: '座椅加热',
  steering_wheel_heat: '方向盘加热',
  defrost: '除雾',
  wiper: '雨刷',
  light: '灯光',
  lights: '灯光',
  headlight: '大灯',
  warning_light: '双闪',
  ambient_light: '氛围灯',
  mirror: '后视镜',
  media: '媒体',
  volume: '音量',
}

function displayValue(v: unknown): string {
  if (typeof v === 'boolean') return v ? '开' : '关'
  if (v === 'on' || v === 'open' || v === 'true') return '开'
  if (v === 'off' || v === 'closed' || v === 'false') return '关'
  if (v && typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/** 三格指标条（电量 / 续航 / 挡位）。`compact` 供平板右面板用（不占整屏） */
export function VehicleMetrics({
  p,
  vehState,
  compact,
}: {
  p: Palette
  vehState: Record<string, unknown>
  compact?: boolean
}) {
  const metrics = stageMetrics(vehState)
  return (
    <View style={{ flexDirection: 'row', gap: 8 }}>
      {metrics.map((m) => (
        <View
          key={m.label}
          style={{
            flex: 1,
            backgroundColor: p.card,
            borderColor: p.line,
            borderWidth: 1,
            borderRadius: 14,
            paddingVertical: compact ? 10 : 14,
            alignItems: 'center',
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'baseline' }}>
            <Text style={{ color: p.fg1, fontSize: p.font(compact ? 20 : 26), fontWeight: '700' }}>
              {m.value}
            </Text>
            {m.unit ? (
              <Text style={{ color: p.fg2, fontSize: p.font(12), marginLeft: 2 }}>{m.unit}</Text>
            ) : null}
          </View>
          <Text style={{ color: p.fg3, fontSize: p.font(11), marginTop: 3 }}>{m.label}</Text>
        </View>
      ))}
    </View>
  )
}

/** 明细镜像列表（三格指标已单列，这里不再重复电量/续航/挡位） */
export function VehicleDetails({ p, vehState }: { p: Palette; vehState: Record<string, unknown> }) {
  const rest = Object.entries(vehState).filter(([k]) => !/^(battery|soc|range_km|gear)$/i.test(k))
  if (!rest.length) return null
  return (
    <View
      style={{
        backgroundColor: p.card,
        borderColor: p.line,
        borderWidth: 1,
        borderRadius: 14,
        paddingHorizontal: 14,
        paddingVertical: 4,
      }}
    >
      {rest.map(([k, v], i) => (
        <View
          key={k}
          style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            paddingVertical: 7,
            borderBottomWidth: i === rest.length - 1 ? 0 : 1,
            borderColor: p.line,
          }}
        >
          <Text style={{ color: p.fg2, fontSize: p.font(13) }}>{KEY_LABEL[k] || k}</Text>
          <Text style={{ color: p.fg1, fontSize: p.font(13), flex: 1, textAlign: 'right' }} numberOfLines={1}>
            {displayValue(v)}
          </Text>
        </View>
      ))}
    </View>
  )
}

/** 平板右面板的车况段（无滚动容器，由外层 ScrollView 承载） */
export function VehicleSection({ p, vehState }: { p: Palette; vehState: Record<string, unknown> }) {
  return (
    <View style={{ gap: 8 }}>
      <Text style={{ color: p.fg3, fontSize: p.font(12) }}>车况</Text>
      <VehicleMetrics p={p} vehState={vehState} compact />
      {!Object.keys(vehState).length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>等待车况镜像…</Text>
      ) : null}
    </View>
  )
}

/** 手机「车辆」整页 */
export function VehiclePanel({ p, vehState }: { p: Palette; vehState: Record<string, unknown> }) {
  const empty = !Object.keys(vehState).length
  return (
    <ScrollView contentContainerStyle={{ padding: 14, gap: 10 }}>
      <VehicleMetrics p={p} vehState={vehState} />
      {empty ? (
        <Text style={{ color: p.fg3, fontSize: p.font(13) }}>
          等待车况镜像…（连上网关后 vehicle_state 帧会推全量）
        </Text>
      ) : null}
      <VehicleDetails p={p} vehState={vehState} />
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>车况镜像 · 与座舱实时同步（只读）</Text>
    </ScrollView>
  )
}
