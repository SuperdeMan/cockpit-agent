// 共享类型与能力目录。HMI 的"单一事实源"：消息结构、设置模型、Agent 清单、默认值。

export type Action = {
  type: string
  payload?: Record<string, unknown>
  require_confirm?: boolean
}

export type Msg = {
  id: string
  role: 'user' | 'assistant'
  text: string
  actions?: Action[]
  needConfirm?: boolean
  followUp?: string
  pending?: boolean // 助手"思考中"占位（开放域慢响应时立刻给反馈）
  streaming?: boolean // 正在流式接收 speech_delta
  error?: boolean
  uiCard?: UiCard
  // 复杂任务过程区（脱敏「步骤+思考摘要」）：进行中默认简短摘要，完成后默认折叠可展开。
  process?: ProcessStep[]
  processActive?: boolean // 过程进行中（未出最终答案）
  driving?: boolean // 行车态（由 Edge 按 VAL 标注）：行车极简、不可展开
}

// 过程区单步：phase=understand|plan|execute|synthesize；summary 为后端按步骤结果合成的脱敏摘要。
export type ProcessStep = {
  phase: string
  label: string
  summary?: string
  status?: string // running | done | start
  step_id?: string // execute 步骤：按它合并 running→done
}

// ─── UI 卡片类型 ───

export type CardGroup = { type: 'card_group'; items: UiCard[] }

export type UiCard =
  | CardGroup
  | WeatherCard
  | ForecastCard
  | StockCard
  | NewsCard
  | SearchCard
  | SearchAnswerCard
  | NewsDigestCard
  | SearchResultCard
  | NewsBriefCard
  | ResearchReportCard
  | SportsScoresCard
  | SportsScorersCard
  | RoutePlanCard
  | ChargingRouteCard
  | TripItineraryCard
  | PoiListCard
  | PoiDetailCard
  | PlaceListCard
  | PlaceDetailCard

// 路线规划卡：出发地 → 途经点（餐厅等）→ 目的地（导航确认途经点后）
export type RoutePlanCard = {
  type: 'route_plan'
  origin?: string
  destination: string
  waypoints: Array<{ name: string; address?: string }>
  distance_km?: number
  duration_min?: number
}

// 充能路线卡：出发地 → 沿途途经充电点 → 目的地
export type ChargingRouteCard = {
  type: 'charging_route'
  destination: string
  distance_km?: number
  duration_min?: number
  stops: Array<{ name: string; address?: string; at_km?: number }>
  soc?: string
}

// 行程卡（P0 重构）：结构化多日行程——按天列停靠点（接地真实 POI）+ 段间驾驶/充电
export type TripStop = {
  stop_id: string
  type: string                 // attraction|meal|hotel|charging|custom
  name: string
  poi?: { name?: string; address?: string; lat?: number; lng?: number; rating?: number } | null
  dwell_min?: number
  grounded: boolean
}

export type TripLeg = {
  from_stop_id: string
  to_stop_id: string
  distance_km: number
  drive_min: number
  charging_stops: Array<{ name: string; address?: string; at_km?: number }>
  soc_before?: number
  soc_after?: number
}

export type TripDay = {
  day_index: number
  theme?: string
  stops: TripStop[]
  legs: TripLeg[]
}

export type TripItineraryCard = {
  type: 'trip_itinerary'
  destination: string
  days: number
  preferences?: string[]
  status?: string
  itinerary: TripDay[]
}

// ── 2026-06-22 信息卡重设计：卡片只给证据（来源/要点/时效/置信度），气泡给结论，不复读 ──
export type Confidence = 'high' | 'medium' | 'low'

export type SearchResultCard = {
  type: 'search_result'
  query: string
  sources: Array<{ title: string; url: string; source: string; published?: string }>
  freshness?: string
  confidence?: Confidence
}

export type NewsBriefCard = {
  type: 'news_brief'
  topic: string
  items: Array<{ title: string; url?: string; source: string; publish_time?: string; summary?: string }>
  freshness?: string
}

// 深度调研报告卡（独立 deep-research Agent 产出）：分节可读报告——气泡给一段式语音简报，
// 卡片给分节结论 + 引用 + 置信度 + 未覆盖 gaps（泊车/手机可读）。
export type ResearchReportCard = {
  type: 'research_report'
  question: string
  summary?: string
  sections: Array<{ heading: string; body: string; citations?: number[]; confidence?: Confidence }>
  sources: Array<{ idx?: number; title: string; url?: string; source?: string; published?: string }>
  overall_confidence?: Confidence
  gaps?: string[]
  freshness?: string
}

export type SportsFixture = {
  league: string
  round: string
  home: string
  away: string
  home_logo?: string
  away_logo?: string
  score: string
  home_goals: string
  away_goals: string
  status: 'finished' | 'live' | 'scheduled' | 'other'
  status_text: string
  elapsed?: string
  kickoff?: string
  // 进球时间线（仅"某场详情"追问时带）：射手 + 分钟 + 主客侧 + 进球/点球/乌龙球
  goals?: Array<{ minute: string; team: 'home' | 'away' | ''; player: string; detail: string }>
}

export type SportsScoresCard = {
  type: 'sports_scores'
  title: string
  fixtures: SportsFixture[]
  freshness?: string
  source?: string
}

export type SportsScorersCard = {
  type: 'sports_scorers'
  title: string
  season: string
  scorers: Array<{ rank: number; player: string; team: string; goals: number }>
  freshness?: string
  source?: string
}

export type WeatherCard = {
  type: 'weather'
  city: string
  temp: string
  text: string
  feels_like: string
  humidity: string
  wind_dir: string
  wind_scale: string
  precip?: string
  pressure?: string
  visibility?: string
  cloud?: string
  dew_point?: string
  update_time: string
  forecast?: Array<{
    date: string
    text_day: string
    text_night: string
    temp_high: string
    temp_low: string
    wind_dir: string
    wind_scale: string
    humidity: string
    precip: string
    uv_index: string
    sunrise: string
    sunset: string
  }>
  air_quality?: {
    aqi: string
    category: string
    pm2p5: string
    primary_pollutant: string
  }
  indices?: Array<{ name: string; level: string; text: string }>
  alerts?: Array<{ title: string; level: string; type: string; text: string; pub_time: string }>
  alerts_available?: boolean
}

export type ForecastCard = {
  type: 'forecast'
  city: string
  days: Array<{
    date: string
    text_day: string
    text_night: string
    temp_high: string
    temp_low: string
    wind_dir: string
    wind_scale: string
  }>
}

export type StockCard = {
  type: 'stock_quote'
  name: string
  symbol: string
  price: string
  change: string
  change_pct: string
  market_time: string
  candles?: StockCandle[]
}

export type StockCandle = {
  date: string
  open: string
  high: string
  low: string
  close: string
  volume: string
}

export type NewsCard = {
  type: 'news_list'
  topic: string
  summary?: string
  items: Array<{
    title: string
    summary: string
    source: string
    publish_time: string
  }>
}

export type SearchCard = {
  type: 'search_list'
  query: string
  summary?: string
  items: Array<{
    title: string
    url: string
    snippet: string
    source: string
  }>
}

// ws2 search-news-redesign：结论式搜索卡片
export type SearchAnswerCard = {
  type: 'search_answer'
  query: string
  answer: string
  sources: Array<{ title: string; url: string; source: string }>
  items?: SearchCard['items']  // 向后兼容
}

// ws2 search-news-redesign：摘要式新闻卡片
export type NewsDigestCard = {
  type: 'news_digest'
  topic: string
  summary: string
  headlines: Array<{ title: string; source: string }>
  items?: NewsCard['items']  // 向后兼容
}

export type PoiListCard = {
  type: 'poi_list'
  keyword?: string
  // 'dest_choice' = 充电目的地候选（回填目的地槽位）；'waypoint_choice' = 顺路停靠候选（落途经点）
  purpose?: string
  title?: string
  destination?: string   // waypoint_choice：导航目的地，供「第N个」拼「导航去{destination}途经{name}」
  items: Array<{
    id: string
    name: string
    rating?: number
    distance_km?: number
    address: string
  }>
}

export type PoiDetailCard = {
  type: 'poi_detail'
  id: string
  name: string
  address: string
  lat: number
  lng: number
  rating: number
  category: string
}

// 周边发现列表卡（nearby.search）：多类目富数据——评分/人均/距离/营业/特色芯片
export type PlaceListCard = {
  type: 'place_list'
  category?: string            // 餐饮/酒店/景点/影院…（卡头与文案用）
  keyword?: string
  items: Array<{
    id: string
    name: string
    category?: string
    rating?: number
    cost?: string              // 人均（字符串，可能空）
    distance_km?: number
    address: string
    tags?: string              // 特色标签（逗号分隔）
    open_today?: string
    lat?: number               // 供「导航去第 N 个」handoff
    lng?: number
  }>
}

// 周边发现详情卡（nearby.detail）：评分/人均/电话/营业时间/特色/图片 + 导航·拨打
export type PlaceDetailCard = {
  type: 'place_detail'
  id: string
  name: string
  category?: string
  address: string
  lat: number
  lng: number
  rating?: number
  cost?: string
  tel?: string
  open_today?: string
  open_week?: string
  tags?: string
  photos?: string[]
}

export type Voice = {
  voice_id: string
  name: string
  language: string
  gender: string
  description?: string
  tags?: string[]
}

// ─── 设置模型 ───
// 端到端已接通的：voiceId / ttsEnabled / autoplay / asrLanguage / micMode /
//   listenSeconds / theme / fontScale / largeTouch / quickCommands / assistantName。
// 预留（UI+持久化已就绪，经 WS meta 透传，待后端 honor）：
//   answerLength / model / agents / memoryEnabled。详见 docs/design 任务文档。
// R4.3 语音回路（UI+持久化就绪，驱动 voiceLoop.mjs FSM；真麦/Worker 集成待 P0 后接线）：
//   handsFree / wakeWordEnabled / followupWindowS / silenceTailMs（全 opt-in 默认关）。

export type Theme = 'dark' | 'light'
export type FontScale = 'normal' | 'large'
export type AsrLanguage = 'zh' | 'en' | 'auto'
export type AsrProvider = 'dashscope' | 'mimo' | 'off' // 流式识别引擎（off=走批处理）
export type MicMode = 'hold' | 'toggle'
export type AnswerLength = 'short' | 'standard' | 'detailed'
export type ModelPref = 'fast' | 'deep' | 'auto'
export type ListenSeconds = 10 | 15 | 30 | 60
export type FollowupWindowS = 5 | 8 | 15 // R4.3 免唤醒续问聆听窗（秒）
export type SilenceTailMs = 500 | 800 | 1200 // R4.3 VAD 静音尾（端点判据，毫秒）

export type Settings = {
  // 语音播报 TTS
  ttsEnabled: boolean
  autoplay: boolean
  voiceId: string
  // 语音输入 ASR
  asrLanguage: AsrLanguage
  asrProvider: AsrProvider // 流式识别引擎（dashscope 实时 / mimo 分块 / off 批处理）
  asrModel: string // 引擎模型（dashscope: Qwen3-…/fun-asr-realtime）
  micMode: MicMode
  listenSeconds: ListenSeconds
  // 免唤醒连续对话 / 唤醒词（R4.3；全部 opt-in 默认关，唤醒前音频不离开浏览器）
  handsFree: boolean            // L1 免唤醒连续对话：一轮回复后保持聆听窗，VAD 断句自动发送
  wakeWordEnabled: boolean      // L2 唤醒词：待机说唤醒词进入聆听
  wakeWord: string              // 选定的唤醒词（display 值，映射到 KWS pinyin token；见 WAKE_WORD_PRESETS）
  followupWindowS: FollowupWindowS // 续问聆听窗时长（秒）
  silenceTailMs: SilenceTailMs  // VAD 静音尾（端点判据，毫秒）
  // 显示与主题
  theme: Theme
  fontScale: FontScale
  largeTouch: boolean
  quickCommands: string[]
  // 定位：仅记住是否允许本应用使用；精确坐标不持久化
  locationEnabled: boolean
  // 助手
  assistantName: string
  answerLength: AnswerLength
  model: ModelPref
  // Agent 开关
  agents: Record<string, boolean>
  // 记忆
  memoryEnabled: boolean
}

// 用户可见的能力开关（对应 agents/ 与端侧快/慢系统）
export type AgentMeta = { id: string; label: string; desc: string; icon: string; core?: boolean }

export const AGENT_CATALOG: AgentMeta[] = [
  { id: 'vehicle', label: '车辆控制', desc: '空调、车窗、座椅、灯光等车身控制（端侧秒回）', icon: '🚘', core: true },
  { id: 'media', label: '媒体音乐', desc: '播放、暂停、切歌（端侧秒回）', icon: '🎵', core: true },
  { id: 'navigation', label: '导航出行', desc: '搜索 POI、导航、充电站、逆地理编码', icon: '🧭' },
  { id: 'info', label: '信息助手', desc: '天气、预报、预警、空气质量、联网搜索、新闻、股票', icon: 'ℹ️' },
  { id: 'trip-planner', label: '行程规划', desc: '多日自驾行程编排', icon: '🗺️' },
  { id: 'deep-research', label: '深度调研', desc: '多视角联网深调研，出带引用的分节报告', icon: '🔬' },
  { id: 'nearby', label: '周边发现', desc: '找餐厅/酒店/景点/影院/停车/充电，看评分·人均·营业·电话', icon: '📍' },
  { id: 'parking-payment', label: '停车缴费', desc: '找车位、停车缴费', icon: '🅿️' },
  { id: 'manual-rag', label: '用车手册', desc: '车辆说明书问答（RAG）', icon: '📖' },
  { id: 'chitchat', label: '闲聊兜底', desc: '开放域对话与情绪陪伴（系统兜底）', icon: '💬', core: true },
]

export const VOICE_FALLBACK: Voice[] = [
  { voice_id: '冰糖', name: '冰糖', language: 'zh', gender: 'female', description: '中文女声', tags: ['中文', '女声'] },
  { voice_id: '茉莉', name: '茉莉', language: 'zh', gender: 'female', description: '中文女声', tags: ['中文', '女声'] },
  { voice_id: '苏打', name: '苏打', language: 'zh', gender: 'male', description: '中文男声', tags: ['中文', '男声'] },
  { voice_id: '白桦', name: '白桦', language: 'zh', gender: 'male', description: '中文男声', tags: ['中文', '男声'] },
  { voice_id: 'Mia', name: 'Mia', language: 'en', gender: 'female', description: '英文女声', tags: ['英文', '女声'] },
  { voice_id: 'Chloe', name: 'Chloe', language: 'en', gender: 'female', description: '英文女声', tags: ['英文', '女声'] },
  { voice_id: 'Milo', name: 'Milo', language: 'en', gender: 'male', description: '英文男声', tags: ['英文', '男声'] },
  { voice_id: 'Dean', name: 'Dean', language: 'en', gender: 'male', description: '英文男声', tags: ['英文', '男声'] },
  { voice_id: 'mimo_default', name: 'MiMo 默认', language: 'zh', gender: 'neutral', description: '中国集群默认', tags: ['默认'] },
]

export const DEFAULT_QUICK_COMMANDS = [
  '打开空调26度',
  '打开主驾座椅加热',
  '播放音乐',
  '附近的充电站',
  '导航去首都机场',
  '今天天气怎么样',
  '讲个笑话',
  '我今天有点不开心',
]

// R4.3 唤醒词预设（issue③）：keywords 为 sherpa-onnx KWS 运行时 pinyin token 串——声母 + 带声调韵母，
// 逐一对 wenetspeech tokens.txt 核对（换词无需重训模型，仅换本串）。刻意不开放自由输入：
// 中文→带声调 token 的浏览器端转换不可靠（ü/零声母/y-w 边界易错→唤醒词静默失效，用户难自查）。
// 真机命中率以泓舟验收为准（同「小舟小舟」的验收口径）。
export const WAKE_WORD_PRESETS: Array<{ word: string; keywords: string }> = [
  { word: '小舟小舟', keywords: 'x iǎo zh ōu x iǎo zh ōu @小舟小舟' }, // 小=x iǎo 舟=zh ōu
  { word: '你好小舟', keywords: 'n ǐ h ǎo x iǎo zh ōu @你好小舟' },   // 你=n ǐ 好=h ǎo
  { word: '小舟你好', keywords: 'x iǎo zh ōu n ǐ h ǎo @小舟你好' },
  { word: '你好阿段', keywords: 'n ǐ h ǎo ā d uàn @你好阿段' },        // 阿=ā(零声母) 段=d uàn
]

/** 唤醒词 display 值 → KWS pinyin token 串；未命中预设回落默认「小舟小舟」。 */
export function wakeKeywordsFor(word: string): string {
  return WAKE_WORD_PRESETS.find((p) => p.word === word)?.keywords ?? WAKE_WORD_PRESETS[0].keywords
}

export const DEFAULT_SETTINGS: Settings = {
  ttsEnabled: true,
  autoplay: true,
  voiceId: '冰糖',
  asrLanguage: 'zh',
  asrProvider: 'dashscope', // DashScope 实时 qwen3 真栈验证可用（边说边上屏）；mimo 分块为回退
  asrModel: 'qwen3-asr-flash-realtime-2026-02-10', // 注意全小写 id（CamelCase 会 1011）
  micMode: 'hold',
  listenSeconds: 15,
  handsFree: false,       // R4.3 opt-in：默认关，行为与今天逐字一致
  wakeWordEnabled: false, // R4.3 opt-in：默认关
  wakeWord: '小舟小舟',    // 默认唤醒词（真麦已验证命中）
  followupWindowS: 8,
  silenceTailMs: 800,
  theme: 'dark',
  fontScale: 'normal',
  largeTouch: false,
  quickCommands: DEFAULT_QUICK_COMMANDS,
  locationEnabled: false,
  assistantName: '小舟',
  answerLength: 'standard',
  model: 'auto',
  agents: Object.fromEntries(AGENT_CATALOG.map((a) => [a.id, true])),
  memoryEnabled: true,
}
