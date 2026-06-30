// 仅用于本地视觉验证（main.tsx 经 ?demo / ?demo=map 注入）——不进入正式应用主链。
import type { Msg, WeatherCard, PoiListCard } from './types'

const weatherCard: WeatherCard = {
  type: 'weather',
  city: '杭州',
  temp: '28',
  text: '多云转阵雨',
  feels_like: '30',
  humidity: '65',
  wind_dir: '东南风',
  wind_scale: '3',
  precip: '0.2',
  pressure: '1008',
  visibility: '24',
  update_time: '17:30',
  forecast: [
    { date: '今天', text_day: '多云', text_night: '阵雨', temp_high: '30', temp_low: '24', wind_dir: '东南风', wind_scale: '3', humidity: '65', precip: '0.6', uv_index: '中等', sunrise: '05:10', sunset: '18:50' },
    { date: '明天', text_day: '阵雨', text_night: '阴', temp_high: '27', temp_low: '23', wind_dir: '东风', wind_scale: '2', humidity: '78', precip: '8', uv_index: '弱', sunrise: '05:11', sunset: '18:50' },
    { date: '后天', text_day: '晴', text_night: '晴', temp_high: '32', temp_low: '25', wind_dir: '南风', wind_scale: '3', humidity: '55', precip: '0', uv_index: '强', sunrise: '05:11', sunset: '18:49' },
  ],
  air_quality: { aqi: '68', category: '良', pm2p5: '40', primary_pollutant: 'PM2.5' },
  indices: [
    { name: '洗车', level: '适宜', text: '适宜洗车' },
    { name: '紫外线', level: '中等', text: '注意防晒' },
  ],
}

const poiCard: PoiListCard = {
  type: 'poi_list',
  keyword: '充电站',
  title: '附近充电站',
  items: [
    { id: 'p1', name: '特来电·西湖文化广场站', rating: 4.6, distance_km: 1.2, address: '西湖区文化广场 B3 层' },
    { id: 'p2', name: '星星充电·黄龙站', rating: 4.5, distance_km: 1.8, address: '黄龙路 88 号地下停车场' },
    { id: 'p3', name: '特来电·湖滨店', rating: 4.3, distance_km: 2.4, address: '湖滨路 55 号停车场 P2 层' },
    { id: 'p4', name: '国家电网·钱江新城', rating: 4.7, distance_km: 3.1, address: '钱江路 145 号地下停车室' },
  ],
}

export const DEMO_WEATHER: Msg[] = [
  { id: 'd0', role: 'user', text: '今天杭州天气怎么样' },
  { id: 'd1', role: 'assistant', text: '杭州今天多云转阵雨 28℃，体感偏闷，傍晚有阵雨，出门记得带把伞。', uiCard: weatherCard },
]

export const DEMO_MAP: Msg[] = [
  { id: 'm0', role: 'user', text: '附近的充电站' },
  { id: 'm1', role: 'assistant', text: '为你找到 4 个附近的充电站，最近的是特来电·西湖文化广场站，1.2 公里。说“导航去第 2 个”即可。', uiCard: poiCard },
]

// ── 卡片族验证（?demo=cards）：股票/搜索/新闻/深调研/赛事/充电路线 ──
const stockCard: import('./types').StockCard = {
  type: 'stock_quote', name: '贵州茅台', symbol: '600519', price: '1689.00',
  change: '+12.50', change_pct: '+0.75%', market_time: '已收盘 · 15:00',
  candles: [
    { date: '06-23', open: '1662', high: '1678', low: '1655', close: '1671', volume: '21000' },
    { date: '06-24', open: '1671', high: '1675', low: '1648', close: '1652', volume: '23400' },
    { date: '06-25', open: '1652', high: '1690', low: '1650', close: '1685', volume: '28100' },
    { date: '06-26', open: '1685', high: '1695', low: '1676', close: '1676', volume: '19800' },
    { date: '06-27', open: '1676', high: '1692', low: '1662', close: '1689', volume: '24500' },
  ],
}

const researchCard: import('./types').ResearchReportCard = {
  type: 'research_report',
  question: '固态电池 2027 年量产可行性',
  summary: '技术路线已基本确立，但量产良率与成本控制仍是决定性障碍，规模化大概率推迟到 2030 年后。',
  overall_confidence: 'medium',
  sections: [
    { heading: '① 技术现状', body: '固态电解质路线收敛为三大方向：氧化物（LLZO）、硫化物与聚合物。硫化物离子电导率最高但对水汽敏感。', citations: [1, 2], confidence: 'high' },
    { heading: '② 量产产能', body: '宁德时代规划 2027 年实现小批量生产，初期约 2GWh，2030 年前进入规模化。', citations: [3], confidence: 'medium' },
    { heading: '③ 成本曲线', body: '当前成本仍高于液态电池约 3–5 倍，量产临界点集中在 2026–2030 年。', citations: [5], confidence: 'medium' },
    { heading: '④ 风险与变数', body: '界面稳定性与制造良率仍是主要工程瓶颈。', citations: [4], confidence: 'low' },
  ],
  sources: [
    { idx: 1, title: '宁德时代全固态电池战略解析', source: 'gasgoo.com', published: '2024-12' },
    { idx: 2, title: 'Solid-state batteries: energy density limits', source: 'nature.com', published: '2024-09' },
    { idx: 3, title: '全球固态电池产能调查报告', source: 'yicai.com', published: '2024-11' },
    { idx: 4, title: 'Toyota solid-state EV timeline update', source: 'reuters.com', published: '2024-12' },
    { idx: 5, title: 'Solid-State Battery Cost Outlook 2030', source: 'bnef.com', published: '2024-10' },
  ],
  gaps: ['固态电解质量产良率数据缺乏公开来源', '超 10 万公里全固态电池循环寿命实测数据无公开报告'],
  freshness: new Date(Date.now() - 12 * 60000).toISOString(),
}

const newsCard: import('./types').NewsBriefCard = {
  type: 'news_brief', topic: '今日要闻',
  freshness: new Date(Date.now() - 2 * 3600000).toISOString(),
  items: [
    { title: '华为发布鸿蒙 Next 正式版，原生应用生态突破 120 万个', summary: '彻底切割安卓底层，原生 AI 加持下流畅度与续航同步提升。', source: '36氪', publish_time: new Date(Date.now() - 2 * 3600000).toISOString() },
    { title: '蔚来 Q3 交付 61,855 辆同比增 37%，现金流首次转正', summary: 'ET9 超豪华车型贡献显著，公司表示 Q2 将实现盈亏平衡。', source: '第一财经', publish_time: new Date(Date.now() - 5 * 3600000).toISOString() },
    { title: '国家统计局：11 月 CPI 同比 -0.5%，PPI 降幅收窄至 -2.1%', summary: '食品价格拖累 CPI，机构预期后续宽松政策仍有空间。', source: '财联社', publish_time: new Date(Date.now() - 6 * 3600000).toISOString() },
  ],
}

const sportsCard: import('./types').SportsScoresCard = {
  type: 'sports_scores', title: '英超 · 第 18 轮', source: 'api-football',
  freshness: new Date(Date.now() - 30 * 60000).toISOString(),
  fixtures: [
    {
      league: '英超', round: '第 18 轮', home: '曼城', away: '阿森纳',
      score: '2-1', home_goals: '2', away_goals: '1', status: 'finished', status_text: '已完赛',
      goals: [
        { minute: '23', team: 'home', player: '哈兰德', detail: '进球' },
        { minute: '54', team: 'away', player: '萨卡', detail: '点球' },
        { minute: '67', team: 'home', player: '福登', detail: '进球' },
      ],
    },
  ],
}

const chargeCard: import('./types').ChargingRouteCard = {
  type: 'charging_route', destination: '都江堰', distance_km: 248, duration_min: 195, soc: '62%',
  stops: [{ name: '青城山服务区充电站', address: '成灌高速青城山段', at_km: 48 }],
}

const tripCard: import('./types').TripItineraryCard = {
  type: 'trip_itinerary', destination: '成都', days: 2, status: 'confirmed',
  itinerary: [
    {
      day_index: 1, theme: '市区人文', legs: [],
      stops: [
        { stop_id: 's1', type: 'attraction', name: '宽窄巷子', poi: { address: '青羊区同仁路' }, grounded: true },
        { stop_id: 's2', type: 'meal', name: '陈麻婆豆腐(骡马市)', poi: { address: '青羊区西玉龙街' }, grounded: true },
        { stop_id: 's3', type: 'hotel', name: '成都太古里酒店', poi: { address: '锦江区中纱帽街' }, grounded: true },
      ],
    },
    {
      day_index: 2, theme: '都江堰一日',
      stops: [
        { stop_id: 's4', type: 'attraction', name: '都江堰景区', poi: { address: '都江堰市公园路' }, grounded: true },
        { stop_id: 's5', type: 'charging', name: '青城山服务区充电站', poi: { address: '成灌高速' }, grounded: true },
      ],
      legs: [{ from_stop_id: 's3', to_stop_id: 's4', distance_km: 60, drive_min: 70, charging_stops: [{ name: '青城山服务区' }] }],
    },
  ],
}

export const DEMO_CARDS: Msg[] = [
  { id: 'c0', role: 'user', text: '查一下茅台股价' },
  { id: 'c1', role: 'assistant', text: '贵州茅台收报 1689.00 元，今日上涨 0.75%。', uiCard: stockCard },
  { id: 'c2', role: 'user', text: '帮我深入调研固态电池 2027 年量产可行性' },
  { id: 'c3', role: 'assistant', text: '已为你完成固态电池量产可行性的分节调研，整体置信度中。', uiCard: researchCard },
  { id: 'c4', role: 'user', text: '今天有什么要闻' },
  { id: 'c5', role: 'assistant', text: '为你速览今日要闻。', uiCard: newsCard },
  { id: 'c6', role: 'user', text: '昨晚曼城对阿森纳比分' },
  { id: 'c7', role: 'assistant', text: '曼城主场 2-1 战胜阿森纳。', uiCard: sportsCard },
  { id: 'c8', role: 'user', text: '导航去都江堰，沿途要充电' },
  { id: 'c9', role: 'assistant', text: '已为你规划到都江堰的充电路线，途中在青城山服务区补电一次。', uiCard: chargeCard },
  { id: 'c10', role: 'user', text: '帮我规划成都 2 日自驾' },
  { id: 'c11', role: 'assistant', text: '已为你规划成都 2 日自驾行程，含都江堰一日。', uiCard: tripCard },
]
