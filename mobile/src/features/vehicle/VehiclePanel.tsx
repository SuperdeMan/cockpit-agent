// 车况镜像（M1-7 立，M3-2 完整化；打磨批 C 补齐键表与值枚举）：vehicle_state 帧的镜像展示。
// 三格指标（电量/续航/挡位）复用 `@shared/vehicleStage.mjs::stageMetrics`——
// **推导住在共享模块**，两端不各算一份：续航是「有 range_km 就直用、没有就按满电折算」，
// 缺数据一律 '--' 而不是假装 62%（那条纪律是 HMI 侧写的，App 抄它的结论不抄它的代码）。
//
// 键表（评审 P20 / V6）：**声明源是 `orchestrator/edge/knowledge/commands.yaml` 的对象清单**——这里不是第二份表，
// 是那份表的中文展示映射，由 `test/vehiclePanel.test.ts` 逐 id 对账 display_name（漂了当场红）；
// 声明源之外的键只收 VAL 模拟车态真的会推的那几个（真栈帧样本同一测试入库）。
// `null` / `undefined` 行不渲染；认不出的键收进折叠的「其他」，不再以英文键名直出。
import { useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'

import { stageMetrics } from '@shared/vehicleStage.mjs'

import { PRESENCE_LANE_DP } from '../../ui/layout/bottomChrome'
import type { Palette } from '../../ui/theme'
import { TARGET } from '../../ui/tokens'

/** 对象 id → 中文（与 commands.yaml 的 display_name 逐 id 一致，测试对账） */
export const KEY_LABEL: Record<string, string> = {
  // ── commands.yaml 对象清单（display_name 原文）──
  seat: '座椅',
  window: '车窗',
  sunroof: '天窗',
  sunshade: '遮阳帘',
  aircon: '空调',
  ambient_light: '氛围灯',
  low_beam: '近光灯',
  headlight: '大灯',
  warning_light: '双闪',
  trunk: '后备箱',
  door_lock: '车门锁',
  fuel_tank_cover: '油箱盖',
  charging_port: '充电口盖',
  rear_view_mirror: '后视镜',
  steering_wheel: '方向盘',
  wiper: '雨刮',
  fragrance: '香氛',
  tire_pressure_monitoring: '胎压监测',
  dashcam: '行车记录仪',
  air_purifier: '空气净化',
  navi_broadcast: '导航播报',
  key_tone: '按键音',
  scene_mode: '情景模式',
  driving_mode: '驾驶模式',
  power_mode: '动力模式',
  energy_recovery: '能量回收',
  lane_departure_assistance: '车道偏离预警',
  lane_assistance: '车道保持辅助',
  front_defogger: '前挡除雾',
  rear_defogger: '后挡除雾',
  accompany_home: '伴我回家灯光',
  volume: '音量',
  page: '界面页面',
  screen: '屏幕',
  app: '应用',
  weather: '天气',
  bluetooth: '蓝牙',
  wifi: 'WiFi',
  hotspot: '个人热点',
  phone: '电话',
  contacts: '通讯录',
  call_log: '通话记录',
  radio: '收音机',
  online_radio: '网络电台',
  music: '音乐',
  audiobook: '有声书',
  opera: '戏曲',
  news: '新闻',
  video: '视频',
  TV: '电视',
  equalizer: '均衡器',
  voice_assistant: '语音助手',
  system: '系统',
  surround_view: '360环视',
  dashboard: '仪表',
  auto_hold: '自动驻车',
  epb: '电子手刹',
  frunk: '前备箱',
  interaction: '交互',
  navigation: '导航',
  map: '地图',
  food: '美食',
  hotel: '酒店',
  flight: '航班',
  train: '火车票',
  stock: '股票',
  media: '媒体',
  battery: '电量',
  // ── 声明源之外、VAL 模拟车态真的会推的键（真栈帧样本见 test/vehiclePanel.test.ts）──
  soc: '电量',
  range_km: '续航',
  gear: '挡位',
  speed: '车速',
  cabin_temp: '车内温度',
  child_lock: '儿童锁',
  hvac_on: '空调开关',
  hvac_temp: '空调温度',
  hvac: '空调',
  ac: '空调',
  temperature: '温度',
  temp: '温度',
  fan_speed: '风量',
  seat_heating: '座椅加热',
  seat_ventilation: '座椅通风',
  speed_kmh: '车速',
  seat_heat: '座椅加热',
  steering_wheel_heat: '方向盘加热',
  defrost: '除雾',
  windows: '车窗',
  door: '车门',
  doors: '车门',
  lock: '车锁',
  charge_port: '充电口盖',
  fuel_cap: '油箱盖',
  light: '灯光',
  lights: '灯光',
  mirror: '后视镜',
  location: '位置',
}

/** 值枚举 → 中文（评审 P20：locked/unlocked/open/closed/folded/unfolded/playing/paused/stopped） */
export const VALUE_LABEL: Record<string, string> = {
  on: '开',
  off: '关',
  true: '开',
  false: '关',
  open: '已打开',
  closed: '已关闭',
  locked: '已上锁',
  unlocked: '已解锁',
  folded: '已折叠',
  unfolded: '展开',
  playing: '播放中',
  paused: '已暂停',
  stopped: '已停止',
}

export function labelOf(key: string): string | undefined {
  return KEY_LABEL[key]
}

export function displayValue(v: unknown): string {
  if (typeof v === 'boolean') return v ? '开' : '关'
  if (typeof v === 'string' && VALUE_LABEL[v]) return VALUE_LABEL[v]
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

function Row({ p, label, value, last }: { p: Palette; label: string; value: string; last: boolean }) {
  return (
    <View
      style={{
        flexDirection: 'row',
        justifyContent: 'space-between',
        paddingVertical: 7,
        borderBottomWidth: last ? 0 : 1,
        borderColor: p.line,
      }}
    >
      <Text style={{ color: p.fg2, fontSize: p.font(13) }}>{label}</Text>
      <Text style={{ color: p.fg1, fontSize: p.font(13), flex: 1, textAlign: 'right' }} numberOfLines={1}>
        {value}
      </Text>
    </View>
  )
}

/** 明细镜像列表（三格指标已单列，这里不再重复电量/续航/挡位）。
 *  已知键中文直出；`null` / `undefined` 不渲染；未知键折叠进「其他」（默认收起）。 */
export function VehicleDetails({ p, vehState }: { p: Palette; vehState: Record<string, unknown> }) {
  const [othersOpen, setOthersOpen] = useState(false)
  const rest = Object.entries(vehState).filter(([k, v]) => !/^(battery|soc|range_km|gear)$/i.test(k) && v !== null && v !== undefined)
  const known = rest.filter(([k]) => !!KEY_LABEL[k])
  const others = rest.filter(([k]) => !KEY_LABEL[k])
  if (!known.length && !others.length) return null
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
      {known.map(([k, v], i) => (
        <Row key={k} p={p} label={KEY_LABEL[k]} value={displayValue(v)} last={i === known.length - 1 && !others.length} />
      ))}
      {others.length ? (
        <>
          <Pressable
            testID="vehicle-others-toggle"
            accessibilityRole="button"
            accessibilityState={{ expanded: othersOpen }}
            onPress={() => setOthersOpen((o) => !o)}
            style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center' }}
          >
            <Text style={{ color: p.fg3, fontSize: p.font(12) }}>
              其他 {others.length} 项 {othersOpen ? '▾' : '▸'}
            </Text>
          </Pressable>
          {othersOpen
            ? others.map(([k, v], i) => <Row key={k} p={p} label={k} value={displayValue(v)} last={i === others.length - 1} />)
            : null}
        </>
      ) : null}
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
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>还没收到车况</Text>
      ) : null}
    </View>
  )
}

/** 手机「车辆」整页 */
export function VehiclePanel({ p, vehState }: { p: Palette; vehState: Record<string, unknown> }) {
  const empty = !Object.keys(vehState).length
  return (
    <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 14 + PRESENCE_LANE_DP, gap: 10 }}>
      <VehicleMetrics p={p} vehState={vehState} />
      {/* 打磨批 B（评审 P21）：页脚与空态是用户话术，不再是开发者话术 */}
      {empty ? (
        <Text style={{ color: p.fg3, fontSize: p.font(13) }}>
          还没收到车况，连上座舱后会自动显示
        </Text>
      ) : null}
      <VehicleDetails p={p} vehState={vehState} />
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>与座舱实时同步</Text>
    </ScrollView>
  )
}
