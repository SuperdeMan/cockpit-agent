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
  precip: '65%',
  pressure: '1008',
  visibility: '24km',
  update_time: '17:30',
  forecast: [
    { date: '今天', text_day: '多云', text_night: '阵雨', temp_high: '30', temp_low: '24', wind_dir: '东南风', wind_scale: '3', humidity: '65', precip: '65%', uv_index: '中等', sunrise: '05:10', sunset: '18:50' },
    { date: '明天', text_day: '阵雨', text_night: '阴', temp_high: '27', temp_low: '23', wind_dir: '东风', wind_scale: '2', humidity: '78', precip: '80%', uv_index: '弱', sunrise: '05:11', sunset: '18:50' },
    { date: '后天', text_day: '晴', text_night: '晴', temp_high: '32', temp_low: '25', wind_dir: '南风', wind_scale: '3', humidity: '55', precip: '10%', uv_index: '强', sunrise: '05:11', sunset: '18:49' },
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
