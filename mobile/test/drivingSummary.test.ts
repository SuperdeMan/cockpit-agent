// 行车摘要模板（v3 P4d，差异 D11；Figma 06 各卡型「行车摘要」格）。
//  ① 卡片注册表里每个卡型都有模板（多一个型漏模板 = 行车时退到兜底「其他结果」）；
//  ② 画廊里每条样本都出得来：标题不是机器名、副行 ≤2 个字段（card_group 另补一个「另有 N 张卡」）；
//  ③ 主按钮只取全量卡已有的动作，句子与全量卡同一个函数（cardActions）；扫码支付不出码不出键；
//  ④ 几张卡对照画板逐项钉住。
import {
  DRIVING_TEMPLATE_TYPES, drivingSummary, type DrivingSummary,
} from '@/core/cards/drivingSummary'
import { navToText, poiPickText, sceneOpenText, tripStopText } from '@/core/cards/cardActions'
import { KNOWN_CARD_TYPES } from '@/features/cards/CardRenderer'
import { CARD_FIXTURES } from '@/features/cards/fixtures'

const NOW = new Date(2026, 9, 3, 13, 53).getTime() // 2026-10-03（周六）13:53，本地
const sum = (card: unknown): DrivingSummary => {
  const s = drivingSummary(card, NOW)
  if (!s) throw new Error('summary is null')
  return s
}

test('注册表里每个卡型都有行车模板（两个方向）', () => {
  expect(KNOWN_CARD_TYPES.filter((t) => !DRIVING_TEMPLATE_TYPES.includes(t))).toEqual([])
  expect(DRIVING_TEMPLATE_TYPES.filter((t) => !KNOWN_CARD_TYPES.includes(t))).toEqual([])
})

describe('画廊每条样本', () => {
  test.each(CARD_FIXTURES.map((f) => [f.label, f.card] as const))('%s', (_label, card) => {
    const s = sum(card)
    expect(s.title).toBeTruthy()
    // 修前标题就是 `main.type`：机器名不许上屏
    expect(s.title).not.toMatch(/^[a-z_]+$/)
    expect(`${s.title} ${s.main} ${s.sub}`).not.toMatch(/\b(weather|route_plan|card_group|vehicle\.control|undefined|null|NaN)\b/)
    const fields = s.sub ? s.sub.split(' · ').length : 0
    expect(fields).toBeLessThanOrEqual((card as { type?: string }).type === 'card_group' ? 3 : 2)
    if (s.button) expect(s.button.label && s.button.send_text).toBeTruthy()
  })
})

describe('对照画板', () => {
  test('天气：今天给「温度 天况」，副行预警优先、再给 AQI，预警时副行琥珀', () => {
    const s = sum({
      type: 'weather', city: '深圳', temp: '28', text: '多云', feels_like: '31', humidity: '78', wind_dir: '东南风', wind_scale: '2',
      update_time: '', air_quality: { aqi: '42', category: '优', pm2p5: '', primary_pollutant: '' },
      alerts: [{ title: '雷电黄色预警', level: '', type: '', text: '', pub_time: '' }],
    })
    expect(s).toMatchObject({ icon: 'weather-thunder-alert', title: '天气 · 深圳', main: '28° 多云', sub: '雷电黄色预警 · AQI 42 优', subWarn: true, button: null })
  })

  test('预报：主数值给下一个白天，副行给今天 + 有雷 / 雪的那天', () => {
    const day = (date: string, text: string, lo: string, hi: string) => ({ date, text_day: text, text_night: text, temp_low: lo, temp_high: hi, wind_dir: '', wind_scale: '' })
    const s = sum({ type: 'forecast', city: '杭州', days: [day('2026-10-03', '小雨', '26', '32'), day('2026-10-04', '多云', '27', '34'), day('2026-10-08', '雷阵雨', '25', '31')] })
    expect(s).toMatchObject({ title: '未来预报 · 杭州', main: '明天 多云 27–34°', sub: '今天 小雨 26–32° · 周四有雷阵雨' })
  })

  test('路线：只算不导才给「开始导航」，句子与全量卡同一个函数；已在导航不给；结束了标题说结束', () => {
    const route = { type: 'route_plan', destination: '深圳宝安国际机场', waypoints: [], distance_km: 24.6, duration_min: 38 }
    expect(sum({ ...route, estimate: true })).toMatchObject({
      title: '路线测算 · 深圳宝安国际机场', main: '38分钟', sub: '24.6 km', button: { label: '开始导航', send_text: navToText('深圳宝安国际机场') },
    })
    expect(sum(route).button).toBeNull()
    expect(sum({ ...route, cancelled: true })).toMatchObject({ title: '导航已结束', button: null })
  })

  test('充电路线：全量卡没有「开始导航」，行车也不造；空 stops 是「全程无需补电」', () => {
    const s = sum({ type: 'charging_route', destination: '杭州东站', stops: [{ name: '凯能中泰充电站', at_km: 320 }, { name: 'B', at_km: 600 }] })
    expect(s).toMatchObject({ main: '补电 2 次', sub: '下一站 约 320 km · 凯能中泰充电站', button: null })
    expect(sum({ type: 'charging_route', destination: '杭州东站', stops: [] }).main).toBe('全程无需补电')
  })

  test('行程：第一站（不说「下一站」——系统不知道走到哪了）；按钮只给已接地的站，句子同全量卡', () => {
    const s = sum({
      type: 'trip_itinerary', destination: '杭州', days: 3,
      itinerary: [{ day_index: 1, stops: [{ stop_id: 'a', type: 'attraction', name: '西湖', grounded: true }, { stop_id: 'b', type: 'meal', name: '楼外楼', grounded: false }], legs: [], weather: { text: '小雨', temp_low: '26', temp_high: '32' } }],
    })
    expect(s).toMatchObject({ title: '行程 · 杭州 3 日', main: '第一站 西湖', sub: 'D1 2 个点 · 小雨 26–32°', button: { label: '导航去西湖', send_text: tripStopText(1, '西湖') } })
  })

  test('候选 / 周边 / 详情：按钮句子与全量卡一致（回填目的地是原名，周边与详情是导航去）', () => {
    const poi = { type: 'poi_list', purpose: 'dest_choice', items: [{ id: '1', name: '杭州东站', address: '' }] }
    expect(sum(poi).button).toEqual({ label: '选第 1 个', send_text: poiPickText(poi as never, '杭州东站') })
    expect(sum(poi).button?.send_text).toBe('杭州东站')
    const place = { type: 'place_list', category: '咖啡', items: [{ id: '1', name: '瑞幸咖啡', distance_km: 1.3, rating: 4.2, address: '', open_today: '07:00-18:00' }] }
    expect(sum(place)).toMatchObject({ title: '周边 · 咖啡', main: '瑞幸咖啡', sub: '1.3 km · ★4.2', button: { label: '导航去第 1 个', send_text: navToText('瑞幸咖啡') } })
    const detail = { type: 'place_detail', id: '1', name: '瑞幸咖啡', category: '咖啡', address: '富通城', lat: 0, lng: 0, rating: 4.2, cost: '13' }
    expect(sum(detail)).toMatchObject({ icon: 'dining', title: '瑞幸咖啡', main: '富通城', sub: '★4.2 · ¥13/人', button: { label: '导航去这里', send_text: navToText('瑞幸咖啡') } })
  })

  test('场景列表：开启第一个（我建的优先），句子同全量卡；场景卡不出键', () => {
    const s = sum({ type: 'scene_list', mine: [{ id: 'a', name: '钓鱼模式' }, { id: 'b', name: '午休' }], builtin: [{ id: 'c', name: '小憩' }, { id: 'd', name: '观影' }] })
    expect(s).toMatchObject({ title: '场景 · 4 个', main: '钓鱼模式', sub: '我建的 2 · 内置 2', button: { label: '开启钓鱼模式', send_text: sceneOpenText('钓鱼模式') } })
    expect(sum({ type: 'scene_card', context: 'confirm', name: '钓鱼模式', actions_preview: [{ label: '座椅放平' }, { label: '氛围灯 20%' }], buttons: [{ label: '确认', send_text: '确认' }] }))
      .toMatchObject({ title: '场景 · 钓鱼模式', main: '2 步 · 待确认', sub: '座椅放平 · 氛围灯 20%', button: null })
  })

  test('扫码支付：行车不出码、不出键，只给金额与到期时刻；过期了副行琥珀', () => {
    const qr = { type: 'payment_qr', payment_id: 'p', amount: '15元', merchant: '停车场', qr_svg: 'data:image/svg+xml;base64,AAAA', expires_at_ms: new Date(2026, 9, 3, 14, 30).getTime() }
    expect(sum(qr)).toMatchObject({ title: '扫码支付 · 停车场', main: '15元', sub: '14:30 过期 · 停车后再付', button: null })
    expect(sum({ ...qr, expires_at_ms: NOW - 1 })).toMatchObject({ sub: '已过期 · 停车后再付', subWarn: true })
  })

  test('行情：主数值带涨跌幅，涨跌给数据色，平盘不着色', () => {
    const q = { type: 'stock_quote', name: '宁德时代', symbol: '300750', market: '深市', price: '246.80', change: '+5.20', change_pct: '+2.15%', market_time: '' }
    expect(sum(q)).toMatchObject({ title: '宁德时代 · 深市', main: '246.80  +2.15%', sub: '+5.20', tone: 'up' })
    expect(sum({ ...q, change: '-1.00' }).tone).toBe('down')
    expect(sum({ ...q, change: '0.00' }).tone).toBeUndefined()
  })

  test('比分 / 射手榜：主数值一场比分，副行状态 + 另 N 场；进行中副行琥珀', () => {
    const f = (home: string, away: string, status: string) => ({ league: '', round: '', home, away, score: '', home_goals: '2', away_goals: '1', status, status_text: status === 'live' ? '下半场' : '已结束' })
    expect(sum({ type: 'sports_scores', title: '欧冠联赛 · 10-02', fixtures: [f('Real Madrid', 'Man City', 'finished'), f('A', 'B', 'finished'), f('C', 'D', 'finished')] }))
      .toMatchObject({ main: 'Real Madrid 2 : 1 Man City', sub: '已结束 · 另 2 场' })
    expect(sum({ type: 'sports_scores', title: '英超', fixtures: [f('LIV', 'CHE', 'live')] }).subWarn).toBe(true)
    expect(sum({ type: 'sports_scorers', title: '英超 射手榜', season: '', scorers: [{ rank: 1, player: 'E. Haaland', team: '', goals: 27 }, { rank: 2, player: 'M. Salah', team: '', goals: 22 }] }))
      .toMatchObject({ main: 'E. Haaland 27 球', sub: '第 2 · M. Salah 22 球' })
  })

  test('卡组取主卡，副行补「另有 N 张卡」；未知卡型走兜底「其他结果」（中文字段名）', () => {
    const group = { type: 'card_group', items: [{ type: 'parking_fee', amount: '15元', plate: '粤B·D12345' }, { type: 'payment_receipt', receipt_id: 'r' }] }
    expect(sum(group)).toMatchObject({ title: '停车费 · 当前', main: '15元', sub: '粤B·D12345 · 说「交停车费」去付 · 另有 1 张卡' })
    expect(sum({ type: 'some_future_card', title: '未来卡型', merchant: '某商户', buttons: [{ label: '看看', send_text: '看看' }] }))
      .toMatchObject({ title: '其他结果', main: '未来卡型', sub: '商户 · 某商户', button: { label: '看看', send_text: '看看' } })
    expect(drivingSummary(null)).toBeNull()
  })

  test('看一看：PoC 画面来自手机摄像头，行车摘要也要说「模拟画面」', () => {
    expect(sum({ type: 'vision_answer', answer: '前方是双向四车道，右侧有行道树，路面干燥。', simulated: true }))
      .toMatchObject({ title: '看一看', main: '前方是双向四车道', sub: '右侧有行道树 · 模拟画面' })
  })
})
