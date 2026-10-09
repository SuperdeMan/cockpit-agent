// 车况镜像（M1-7 立，M3-2 完整化；打磨批 C 补齐键表与值枚举）：vehicle_state 帧的镜像展示。
// 三格指标（电量/续航/挡位）复用 `@shared/vehicleStage.mjs::stageMetrics`——
// **投影住在共享模块**，两端不各算一份：续航只读 range_km，没有就显示「读不到」，
// 不从电量估算续航，也不替缺失读数补数值或单位。
//
// 键表（评审 P20 / V6）：**声明源是 `orchestrator/edge/knowledge/commands.yaml` 的对象清单**——这里不是第二份表，
// 是那份表的中文展示映射，由 `test/vehiclePanel.test.ts` 逐 id 对账 display_name（漂了当场红）；
// 声明源之外的键只收 VAL 模拟车态真的会推的那几个（真栈帧样本同一测试入库）。
// `null` / `undefined` 行不渲染；认不出的键收进折叠的「其他」，不再以英文键名直出。
// v3 P5b（Figma 04 页 V 组）：指标块 = surface 圆角 16、数值左对齐；明细 = 「明细」分区 + 分组卡（行高 44）；
// 「其他」展开后每行写「未识别字段 · 原键」——原键留给排障，前缀说清这不是我们认得的东西。
import { useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'

import { stageMetrics } from '@shared/vehicleStage.mjs'

import { Icon, iconRuntimeAvailable } from '../../ui/Icon'
import { PRESENCE_LANE_DP } from '../../ui/layout/bottomChrome'
import { ListGroup, ListSectionHeader } from '../../ui/ListItem'
import type { Palette } from '../../ui/theme'
import { RADIUS, SPACE, TARGET, textStyle } from '../../ui/tokens'

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
  // ── VAL 模拟车态 `self.state[...]` 会写的子功能键（v3 P5b「常见键的中文名表」；test/vehiclePanel.test.ts 读 val.py 对账）──
  ambient_light_brightness: '氛围灯亮度',
  ambient_light_color: '氛围灯颜色',
  fragrance_level: '香氛浓度',
  hvac_wind_speed: '空调风量',
  rear_view_mirror_heating: '后视镜加热',
  screen_brightness: '屏幕亮度',
  seat_recline: '座椅靠背',
  steering_wheel_heating: '方向盘加热',
  steering_wheel_height: '方向盘高度',
  volume_muted: '静音',
  wiper_speed: '雨刮速度',
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

/** 三格指标条（电量 / 续航 / 挡位；Figma Vehicle/MetricTile：surface 圆角 16、数值 numeric/l 左对齐 + 单位、标签 caption）。
 *  `compact` 供平板右面板用（不占整屏） */
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
            backgroundColor: p.surface,
            borderRadius: RADIUS.lg,
            paddingVertical: compact ? 10 : 12,
            paddingHorizontal: 14,
            gap: 4,
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 2 }}>
            <Text style={[textStyle(compact ? 'numericM' : 'numericL', p.fontScale), { color: p.fg1 }]}>{m.value}</Text>
            {m.unit ? <Text style={[textStyle('caption', p.fontScale), { color: p.fg2 }]}>{m.unit}</Text> : null}
          </View>
          <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>{m.label}</Text>
        </View>
      ))}
    </View>
  )
}

/** 明细行（Figma Vehicle/DetailRow：高 44，键 body/m 次级色、值 body/m 主色右对齐） */
function Row({ p, label, value }: { p: Palette; label: string; value: string }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 44, paddingHorizontal: SPACE[3] }}>
      <Text style={[textStyle('bodyM', p.fontScale), { color: p.fg2 }]}>{label}</Text>
      <Text style={[textStyle('bodyM', p.fontScale), { color: p.fg1, flex: 1, textAlign: 'right' }]} numberOfLines={1}>
        {value}
      </Text>
    </View>
  )
}

/** 认不出的键怎么叫（计划 P5b）：前缀说清「这不是我们认得的字段」，原键留给排障 */
export function unknownKeyLabel(key: string): string {
  return `未识别字段 · ${key}`
}

/** 明细镜像列表（三格指标已单列，这里不再重复电量/续航/挡位）。
 *  已知键中文直出；`null` / `undefined` 不渲染；未知键折叠进「其他」（默认收起），展开后写「未识别字段 · 原键」。
 *  `columns=2`（大屏，Figma 07 SP-2）：已知行对半分进左右两组，「其他」跟在右组 */
export function VehicleDetails({ p, vehState, columns = 1 }: { p: Palette; vehState: Record<string, unknown>; columns?: 1 | 2 }) {
  const [othersOpen, setOthersOpen] = useState(false)
  const rest = Object.entries(vehState).filter(([k, v]) => !/^(battery|soc|range_km|gear)$/i.test(k) && v !== null && v !== undefined)
  const known = rest.filter(([k]) => !!KEY_LABEL[k])
  const others = rest.filter(([k]) => !KEY_LABEL[k])
  if (!known.length && !others.length) return null
  const half = columns === 2 ? Math.ceil(known.length / 2) : known.length
  const rows = (list: [string, unknown][]) => list.map(([k, v]) => <Row key={k} p={p} label={KEY_LABEL[k]} value={displayValue(v)} />)
  const otherRows = [
    others.length ? (
      <Pressable
        key="others"
        testID="vehicle-others-toggle"
        accessibilityRole="button"
        accessibilityState={{ expanded: othersOpen }}
        onPress={() => setOthersOpen((o) => !o)}
        style={{ minHeight: p.target(TARGET.parked), flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: SPACE[3] }}
      >
        <Text style={[textStyle('bodyM', p.fontScale), { color: p.fg2, flex: 1 }]}>其他 {others.length} 项</Text>
        {iconRuntimeAvailable() ? (
          <Icon name={othersOpen ? 'chevron-up' : 'chevron-down'} size={18} color={p.fg3} />
        ) : (
          <Text style={[textStyle('bodyM', p.fontScale), { color: p.fg3 }]}>{othersOpen ? '▾' : '▸'}</Text>
        )}
      </Pressable>
    ) : null,
    ...(othersOpen ? others.map(([k, v]) => <Row key={'other:' + k} p={p} label={unknownKeyLabel(k)} value={displayValue(v)} />) : []),
  ]
  if (columns === 2) {
    return (
      <View testID="vehicle-details-wide" style={{ flexDirection: 'row', gap: 12, alignItems: 'flex-start' }}>
        <ListGroup p={p} style={{ flex: 1 }}>{rows(known.slice(0, half))}</ListGroup>
        <ListGroup p={p} style={{ flex: 1 }}>
          {rows(known.slice(half))}
          {otherRows}
        </ListGroup>
      </View>
    )
  }
  return (
    <View>
      <ListSectionHeader p={p} title="明细" fontScale={p.fontScale} />
      <ListGroup p={p}>
        {rows(known)}
        {otherRows}
      </ListGroup>
    </View>
  )
}

/** 平板右面板的车况段（无滚动容器，由外层 ScrollView 承载） */
export function VehicleSection({ p, vehState }: { p: Palette; vehState: Record<string, unknown> }) {
  return (
    <View style={{ gap: 8 }}>
      <Text style={[textStyle('labelM', p.fontScale), { color: p.fg3 }]}>车况</Text>
      <VehicleMetrics p={p} vehState={vehState} compact />
      {!Object.keys(vehState).length ? (
        <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>还没收到车况</Text>
      ) : null}
    </View>
  )
}

/** 手机「车辆」整页；`wide`（sizeClass.supportWide，大屏非行车）= 明细两列（Figma 07 SP-2） */
export function VehiclePanel({
  p,
  vehState,
  stateLabel,
  wide = false,
}: {
  p: Palette
  vehState: Record<string, unknown>
  stateLabel?: string
  wide?: boolean
}) {
  const empty = !Object.keys(vehState).length
  return (
    <ScrollView contentContainerStyle={{ paddingHorizontal: SPACE[3], paddingTop: SPACE[1], paddingBottom: SPACE[3] + PRESENCE_LANE_DP, gap: 12 }}>
      <VehicleMetrics p={p} vehState={vehState} />
      {/* 打磨批 B（评审 P21）：页脚与空态是用户话术，不再是开发者话术 */}
      {empty ? (
        <Text style={[textStyle('bodyM', p.fontScale), { color: p.fg2 }]}>还没收到车况，连上座舱后会自动显示</Text>
      ) : null}
      <VehicleDetails p={p} vehState={vehState} columns={wide ? 2 : 1} />
      {/* 页脚就是共享投影给的来源口径（模拟车况 / 包含模拟数据 / 部分状态待更新 / 更新时效未知），原样显示；
          画板上的「· 12:51 更新」没加：投影里没有帧时刻，客户端收到的时刻不是数据时刻 */}
      <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>{stateLabel || '车辆状态'}</Text>
    </ScrollView>
  )
}
