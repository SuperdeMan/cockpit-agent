// 车辆状态面板的「显示配置」——分组、标签、渲染类型集中在此，
// VehicleState.tsx 只按配置渲染，不写死任何业务键。
//
// 覆盖范围：VAL `_simulate` 已正经建模的离散状态键（聚焦优化口径）。
// 未建模、只落兜底标记的键（bluetooth_open 之类）走 fallback「其他」分组，
// 原样展示，提示其尚未建模——不在本次范围内美化。

export type Kind = 'toggle' | 'openclose' | 'percent' | 'color' | 'level' | 'mode'

export type GroupId = 'climate' | 'body' | 'lighting' | 'media' | 'driving' | 'other'

export const GROUPS: { id: GroupId; label: string; en: string }[] = [
  { id: 'climate', label: '空调', en: 'Climate' },
  { id: 'body', label: '门窗车身', en: 'Body' },
  { id: 'lighting', label: '灯光', en: 'Lighting' },
  { id: 'media', label: '影音', en: 'Media' },
  { id: 'driving', label: '驾驶', en: 'Driving' },
  { id: 'other', label: '其他', en: 'Other' },
]

export type KeyMeta = {
  label: string
  icon: string
  group: GroupId
  kind: Kind
  max?: number // level 类的满量程，用于画小条（缺省不画条）
}

// 原子键（非聚合）。顺序即同组内的渲染顺序。
export const META: Record<string, KeyMeta> = {
  // ── 空调 ──
  fragrance: { label: '香氛', icon: '🌸', group: 'climate', kind: 'toggle' },
  steering_wheel_heating: { label: '方向盘加热', icon: '🔥', group: 'climate', kind: 'toggle' },
  steering_wheel_height: { label: '方向盘高度', icon: '🎚️', group: 'climate', kind: 'level' },
  seat_heating: { label: '座椅加热', icon: '💺', group: 'climate', kind: 'toggle' },
  seat_ventilation: { label: '座椅通风', icon: '💺', group: 'climate', kind: 'toggle' },
  seat_massage: { label: '座椅按摩', icon: '💺', group: 'climate', kind: 'toggle' },
  seat_lumbar_support: { label: '座椅腰托', icon: '💺', group: 'climate', kind: 'toggle' },
  // ── 门窗车身 ──
  window: { label: '车窗', icon: '🪟', group: 'body', kind: 'percent' },
  sunroof: { label: '天窗', icon: '☀️', group: 'body', kind: 'percent' },
  sunshade: { label: '遮阳帘', icon: '🌥️', group: 'body', kind: 'percent' },
  door_lock: { label: '车门锁', icon: '🔒', group: 'body', kind: 'openclose' },
  trunk: { label: '后备箱', icon: '🧳', group: 'body', kind: 'openclose' },
  fuel_tank_cover: { label: '油箱盖', icon: '⛽', group: 'body', kind: 'openclose' },
  charging_port: { label: '充电口', icon: '🔌', group: 'body', kind: 'openclose' },
  rear_view_mirror: { label: '后视镜', icon: '🪞', group: 'body', kind: 'openclose' },
  wiper: { label: '雨刷', icon: '🌧️', group: 'body', kind: 'toggle' },
  wiper_speed: { label: '雨刷档', icon: '🌧️', group: 'body', kind: 'level', max: 5 },
  // ── 灯光 ──
  headlight: { label: '大灯', icon: '🔦', group: 'lighting', kind: 'toggle' },
  accompany_home: { label: '伴我回家', icon: '🏠', group: 'lighting', kind: 'toggle' },
  // ── 影音 ──
  screen_brightness: { label: '屏幕亮度', icon: '📱', group: 'media', kind: 'level', max: 100 },
  // ── 驾驶 ──
  driving_mode: { label: '驾驶模式', icon: '🏁', group: 'driving', kind: 'mode' },
  scene_mode: { label: '场景模式', icon: '🎭', group: 'driving', kind: 'mode' },
  energy_recovery: { label: '能量回收', icon: '🔋', group: 'driving', kind: 'level', max: 3 },
  tire_pressure_monitoring: { label: '胎压监测', icon: '🛞', group: 'driving', kind: 'toggle' },
  dashcam: { label: '行车记录仪', icon: '📹', group: 'driving', kind: 'toggle' },
}

// 聚合卡：把多个底层键合成一张卡，成员键不再单独成卡。
export type CompositeId = 'hvac' | 'ambient' | 'media'

export type Composite = {
  id: CompositeId
  label: string
  icon: string
  group: GroupId
  members: string[] // 被「吃掉」的底层键；任一存在即渲染，任一变化即整卡高亮
}

export const COMPOSITES: Composite[] = [
  { id: 'hvac', label: '空调', icon: '❄️', group: 'climate', members: ['hvac_on', 'hvac_temp', 'hvac_wind_speed'] },
  { id: 'ambient', label: '氛围灯', icon: '💡', group: 'lighting', members: ['ambient_light', 'ambient_light_color', 'ambient_light_brightness'] },
  { id: 'media', label: '媒体', icon: '🎵', group: 'media', members: ['media', 'volume'] },
]

// 动态量在「车辆动态」面板呈现，车辆状态面板不重复显示。
export const DYNAMIC = new Set(['speed_kmh', 'battery', 'gear', 'location'])

// 被聚合卡消费的底层键集合（不再单独成卡）。
export const CONSUMED = new Set(COMPOSITES.flatMap((c) => c.members))

// ── 颜色：协议值优先，中文名兜底（VAL 归一化后存的是 red/blue…） ──
const COLOR_HEX: Record<string, string> = {
  red: '#f87171', 蓝色: '#60a5fa', blue: '#60a5fa', 红色: '#f87171',
  green: '#34d399', 绿色: '#34d399',
  white: '#f1f5f9', 白色: '#f1f5f9',
  purple: '#a78bfa', 紫色: '#a78bfa',
  yellow: '#fbbf24', 黄色: '#fbbf24',
  orange: '#fb923c', 橙色: '#fb923c',
  pink: '#f472b6', 粉色: '#f472b6',
  cyan: '#2fe0c8', 青色: '#2fe0c8',
  warm_white: '#fde68a', 暖白: '#fde68a',
  cool_white: '#e0f2fe', 冷白: '#e0f2fe',
  ice_blue: '#7dd3fc', 冰蓝: '#7dd3fc',
  starry: '#818cf8', 星空: '#818cf8',
}

const COLOR_LABEL: Record<string, string> = {
  red: '红色', blue: '蓝色', green: '绿色', white: '白色', purple: '紫色',
  yellow: '黄色', orange: '橙色', pink: '粉色', cyan: '青色',
  warm_white: '暖白', cool_white: '冷白', ice_blue: '冰蓝', starry: '星空',
}

const MODE_LABEL: Record<string, string> = {
  eco: '节能', sport: '运动', comfort: '舒适', snow: '雪地', offroad: '越野', normal: '标准',
  nap: '小憩', camping: '露营', movie: '观影', romantic: '浪漫', meditation: '冥想',
}

const OC_LABEL: Record<string, { text: string; active: boolean }> = {
  open: { text: '打开', active: true },
  closed: { text: '关闭', active: false },
  close: { text: '关闭', active: false },
  locked: { text: '已锁', active: false },
  unlocked: { text: '已解锁', active: true },
  unfolded: { text: '展开', active: true },
  folded: { text: '折叠', active: false },
}

const MEDIA_LABEL: Record<string, string> = {
  playing: '播放中',
  paused: '已暂停',
  stopped: '已停止',
}

// ── 纯函数：值 → 显示 ──

/** 把 open/closed/true/false/"70%"/数字统一归一成 0–100。 */
export function toPercent(value: unknown): number {
  if (value === 'open' || value === true) return 100
  if (value === 'closed' || value === 'close' || value === false || value == null) return 0
  if (typeof value === 'number') return clamp(value)
  if (typeof value === 'string') {
    const match = value.match(/(-?\d+(?:\.\d+)?)/)
    if (match) return clamp(Number(match[1]))
  }
  return 0
}

function clamp(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n)))
}

export function colorHex(value: unknown): string {
  if (typeof value === 'string' && COLOR_HEX[value]) return COLOR_HEX[value]
  return '#9db0d4' // 未知色：中性灰蓝
}

export function colorLabel(value: unknown): string {
  if (typeof value === 'string') return COLOR_LABEL[value] || value
  return String(value)
}

export function modeLabel(value: unknown): string {
  if (typeof value === 'string') return MODE_LABEL[value] || value
  return String(value)
}

export function ocLabel(value: unknown): { text: string; active: boolean } {
  if (typeof value === 'string' && OC_LABEL[value]) return OC_LABEL[value]
  return { text: String(value ?? '—'), active: false }
}

export function mediaLabel(value: unknown): string {
  if (typeof value === 'string') return MEDIA_LABEL[value] || value
  return '—'
}
