// 卡片画廊语料（M3-1/M3-4）：每个卡型一条代表性样本 + 几条**边界分支**。
//
// 为什么要这个文件（不是「为了好看」）：
//  ① §8.3 要求「全卡族截图归档」，而其中一部分卡在真栈上**够不着或不该够**——
//     商户菜单要门店营业（凌晨全打烊）、付款码要走到真实下单、payment_receipt 要真付款
//     （契约 §9.17：系统不执行最终付款，所以它在验收里**永远**只能靠样本）。
//  ② 边界分支真栈几乎不产：付款码过期置灰、充电路线「全程无需补电」、
//     merchant 选品带图/带规格 chips。这些恰恰是最容易写错的那几行。
//  ③ M3-4 要求「深浅主题全卡族过一遍」——一屏切主题比逐张跑真栈现实得多。
//
// ⚠ **样本不是读数**：画廊截图只证明「渲染器对这份数据的输出长这样」，
// 不证明后端会发这样的数据。哪些卡是真栈验的、哪些是样本验的，实施记录里逐条标注。
// 字段一律照 `hmi/src/types.ts` 填，不臆造字段名（猜字段名正是 Q2 那个洞的成因）。

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface Fixture {
  label: string
  /** 真栈是否已验过同型（画廊标注用，避免把样本读成读数） */
  realStack?: boolean
  card: any
}

/**
 * 每次调用**重新取时间基准**。
 *
 * 刻意做成函数而不是模块级常量：`payment_qr` 的倒计时、`reminder_list` 的「今天/明天」
 * 都是相对量，而模块级常量只在 **bundle 加载那一刻**求值一次。2026-08-27 真机实测踩到：
 * bundle 是凌晨 1 点加载的，早上 7 点打开画廊时「倒计时中」那条已经过期了，
 * **本该验倒计时的样本变成了在验过期态**——而两条都有独立样本，于是过期态被验了两遍、
 * 倒计时一遍没验。**相对时间的样本必须在打开的那一刻算。**
 */
export function cardFixtures(): Fixture[] {
  const NOW = Date.now()
  const iso = (offsetMs: number) => new Date(NOW + offsetMs).toISOString()
  return [
  // ── 信息族 ──
  {
    label: 'weather',
    realStack: true,
    card: {
      type: 'weather', city: '深圳', temp: '28', text: '多云', feels_like: '31',
      humidity: '78', wind_dir: '东南风', wind_scale: '2', update_time: '01:00',
      air_quality: { aqi: '42', category: '优' },
      alerts: [{ title: '雷电黄色预警' }],
      forecast: [
        { date: '2026-08-27', text_day: '雷阵雨', temp_low: '26', temp_high: '32' },
        { date: '2026-08-28', text_day: '多云', temp_low: '27', temp_high: '33' },
        { date: '2026-08-29', text_day: '晴', temp_low: '27', temp_high: '34' },
      ],
      _prov: { mode: 'real', vendor: 'qweather', fetched_at: iso(-3600_000) },
    },
  },
  {
    label: 'forecast',
    card: {
      type: 'forecast', city: '杭州',
      days: [
        { date: '2026-08-27', text_day: '小雨', text_night: '阴', temp_low: '26', temp_high: '32' },
        { date: '2026-08-28', text_day: '多云', text_night: '晴', temp_low: '27', temp_high: '34' },
      ],
    },
  },
  {
    label: 'stock_quote',
    card: {
      type: 'stock_quote', name: '宁德时代', symbol: '300750', market: 'SZ',
      price: '246.80', change: '+5.20', change_pct: '+2.15%', market_time: '15:00',
    },
  },
  {
    label: 'news_list',
    card: {
      type: 'news_list', topic: '新能源',
      summary: '固态电池产业化提速。',
      items: [{ title: '首条全固态电池中试线投产', source: '财新', publish_time: '01:10' }],
    },
  },
  {
    label: 'news_digest',
    card: {
      type: 'news_digest', topic: '今日要闻', summary: '三条值得注意的消息。',
      headlines: [{ title: '固态电池国际标准立项', source: 'OFweek' }],
    },
  },
  {
    label: 'news_brief',
    card: {
      type: 'news_brief', topic: '汽车', freshness: iso(-7200_000),
      items: [{ title: '车企集体下调续航标称口径', source: '第一财经', publish_time: '00:30', summary: '统一按 CLTC 标注。' }],
    },
  },
  {
    label: 'search_answer',
    card: {
      type: 'search_answer', query: '固态电池什么时候量产',
      answer: '主流厂商给出的全固态量产时间集中在 2027–2030 年。',
      sources: [{ title: '全固态锂电池产业化进展', source: 'hgjournal' }],
    },
  },
  {
    label: 'search_result',
    realStack: true,
    card: {
      type: 'search_result', query: '英超射手榜', freshness: iso(-5400_000), confidence: 'high',
      sources: [{ title: '射手榜 - 英格兰超级足球联赛', url: 'https://nowe.com', source: 'nowe.com', published: iso(-86400_000) }],
      _prov: { mode: 'real', vendor: 'exa', fetched_at: iso(-5400_000) },
    },
  },
  {
    label: 'search_list',
    card: {
      type: 'search_list', query: '钓鱼模式怎么设', summary: '两个可行做法。',
      items: [{ title: '一句话创建场景', snippet: '说「创建钓鱼模式：座椅放平」即可。', source: '用车手册' }],
    },
  },
  {
    label: 'research_report',
    realStack: true,
    card: {
      type: 'research_report', question: '固态电池', overall_confidence: 'high',
      summary: '固态电池以固态电解质替代液态电解液，能量密度与安全性更优，2027 起进入小批量装车。',
      freshness: iso(-172800_000),
      sections: [
        { heading: '工作原理与材料体系', body: '硫化物/氧化物/聚合物三条路线各有取舍。', citations: [1, 3], confidence: 'high' },
        { heading: '产业化挑战', body: '界面阻抗与量产良率是当前主要瓶颈。', citations: [4], confidence: 'medium' },
      ],
      sources: [
        { idx: 1, title: '固态电池', source: 'zh.wikipedia.org' },
        { idx: 3, title: '全固态锂电池的产业化和技术研究进展', source: 'hgjournal' },
        { idx: 4, title: '液态、半固态……一文了解锂电池', source: 'xinhuanet' },
      ],
      gaps: ['缺少明确量产成本数据（元/Wh）', '未涉及三大路线的横向量化对比'],
    },
  },
  {
    label: 'sports_scores（有比分 + 进球时间线）',
    card: {
      type: 'sports_scores', title: '英超 · 第1轮', freshness: iso(-1800_000), source: 'api-football',
      fixtures: [{
        league: '英超', round: '第1轮', home: '曼城', away: '切尔西',
        home_flag: '', away_flag: '', score: '2-1', home_goals: '2', away_goals: '1',
        status: 'finished', status_text: '完场', kickoff: '2026-08-26T19:30:00Z',
        goals: [
          { minute: '23', team: 'home', player: '哈兰德', detail: '进球' },
          { minute: '58', team: 'away', player: '恩佐', detail: '点球' },
          { minute: '81', team: 'home', player: '福登', detail: '进球' },
        ],
      }],
    },
  },
  {
    label: 'sports_scores（空态·真栈已验）',
    realStack: true,
    card: { type: 'sports_scores', title: '英超 · 08-26', freshness: iso(-600_000), source: 'api-football', fixtures: [] },
  },
  {
    label: 'sports_scorers',
    card: {
      type: 'sports_scorers', title: '英超射手榜', season: '2025/26', source: 'api-football',
      freshness: iso(-3600_000),
      scorers: [
        { rank: 1, player: '哈兰德', team: '曼城', goals: 27 },
        { rank: 2, player: '伊戈尔', team: '维尔贝克', goals: 22 },
        { rank: 3, player: '克鲁皮', team: '伯恩茅斯', goals: 13 },
      ],
    },
  },
  // ── 出行族 ──
  {
    label: 'route_plan（estimate 变体）',
    card: { type: 'route_plan', estimate: true, origin: '当前位置', destination: '杭州东站', waypoints: [], distance_km: 1284, duration_min: 862 },
  },
  {
    label: 'route_plan（cancelled 变体）',
    card: { type: 'route_plan', cancelled: true, destination: '杭州东站', waypoints: [] },
  },
  {
    label: 'poi_list（dest_choice·真栈已验）',
    realStack: true,
    card: {
      type: 'poi_list', purpose: 'dest_choice', keyword: '杭州', title: '杭州 · 选择目的地',
      items: [{ id: 'p1', name: '杭州站', address: '小营街道环城东路1号' }, { id: 'p2', name: '杭州东站', address: '天城路1号' }],
    },
  },
  { label: 'poi_detail', card: { type: 'poi_detail', name: '杭州东站', address: '天城路1号', category: '交通枢纽', rating: '4.5', lat: 30.2907, lng: 120.2129 } },
  {
    label: 'place_list（含「看菜单」直达）',
    realStack: true,
    card: {
      type: 'place_list', category: '咖啡', keyword: '瑞幸',
      items: [
        { id: 's1', name: '瑞幸咖啡(富通城三期店)', address: '兴业路富通城三期一层103室', rating: '4.2', cost: '13.00', distance_km: 1.3, open_today: '07:00-18:00', lat: 22.5781, lng: 113.8563 },
        { id: 's2', name: '麦当劳(西乡大道店)', address: '西乡大道100号', rating: '4.0', cost: '25.00', distance_km: 0.8, open_today: '24小时营业', lat: 22.5726, lng: 113.8608 },
      ],
      _prov: { mode: 'real', vendor: 'amap', fetched_at: iso(-900_000) },
    },
  },
  {
    label: 'place_detail',
    card: { type: 'place_detail', name: '瑞幸咖啡(富通城三期店)', address: '兴业路富通城三期一层103室', rating: '4.2', cost: '13.00', tel: '400-000-0000', open_today: '07:00-18:00', tags: '咖啡,外带,自提', lat: 22.5781, lng: 113.8563 },
  },
  {
    label: 'charging_route（有补电点）',
    card: {
      type: 'charging_route', destination: '杭州东站', distance_km: 1284, duration_min: 862, soc: '62%',
      stops: [
        { name: '中国石化凯能中泰充电站', address: 'S14 沿线 K320', at_km: 320 },
        { name: '特来电赣州服务区站', address: 'G60 赣州服务区', at_km: 780 },
      ],
    },
  },
  {
    label: 'charging_route（全程无需补电·空 stops 是结论不是空态）',
    card: { type: 'charging_route', destination: '深圳北站', distance_km: 18, duration_min: 26, soc: '92%', stops: [] },
  },
  {
    label: 'trip_itinerary',
    realStack: true,
    card: {
      type: 'trip_itinerary', destination: '杭州', days: 3, status: 'confirm',
      itinerary: [
        {
          day_index: 1, city: '杭州', theme: '抵达与西湖夜色',
          weather: { text: '小雨', temp_low: '26', temp_high: '32' },
          stops: [
            { stop_id: 's1', type: 'attraction', name: '西湖', grounded: true, poi: { address: '龙井路1号' } },
            { stop_id: 's2', type: 'meal', name: '楼外楼', grounded: true, poi: { address: '孤山路30号' } },
            { stop_id: 's3', type: 'hotel', name: '待定酒店', grounded: false },
          ],
          legs: [{ from_stop_id: 's1', to_stop_id: 's2', distance_km: 3, drive_min: 9, charging_stops: [{ name: '中国石化凯能中泰充电站' }] }],
        },
      ],
    },
  },
  // ── 澄清 / 提醒 / 场景 ──
  { label: 'intent_choice', realStack: true, card: { type: 'intent_choice', question: '沿途充电你希望怎么处理?', options: [{ label: '规划沿途充能', send_text: '规划去杭州东站的长途充能策略' }, { label: '导航+顺路找桩', send_text: '导航去杭州东站，沿途找充电桩' }] } },
  {
    label: 'reminder_list',
    realStack: true,
    card: {
      type: 'reminder_list', view: 'multi', date_label: '近期',
      items: [
        { id: 'r1', kind: 'reminder', title: '接孩子放学', status: 'pending', time_display: '今天 17:30', fire_at_ms: NOW + 3600_000 },
        { id: 'r2', kind: 'reminder', title: '车辆年检', status: 'done', time_display: '昨天 10:00', fire_at_ms: NOW - 86400_000 },
      ],
      todos: [{ id: 't1', kind: 'todo', title: '买机油', status: 'pending' }],
    },
  },
  { label: 'reminder_card（fired）', card: { type: 'reminder_card', context: 'fired', item: { id: 'r1', kind: 'reminder', title: '接孩子放学', status: 'fired', time_display: '今天 17:30', fire_at_ms: NOW }, actions: [{ label: '完成', send_text: '完成接孩子放学' }, { label: '推迟10分钟', send_text: '推迟10分钟' }] } },
  {
    label: 'scene_card（confirm + danger）',
    realStack: true,
    card: { type: 'scene_card', context: 'confirm', name: '钓鱼模式', description: '座椅放平、氛围灯调暗', actions_preview: [{ label: '座椅放平到170度', danger: true }, { label: '氛围灯20%' }], buttons: [] },
  },
  {
    label: 'scene_list',
    realStack: true,
    card: {
      type: 'scene_list',
      mine: [{ id: 'm1', name: '钓鱼模式', description: '座椅放平、氛围灯调暗', action_count: 2 }],
      builtin: [{ id: 'b1', name: '回家模式', description: '自动导航回家 + 舒适车内', action_count: 3 }],
    },
  },
  { label: 'vision_answer（simulated 必须显式）', card: { type: 'vision_answer', question: '看看外面是什么', answer: '前方是一条双向四车道的城市道路，右侧有一排行道树，路面干燥。', simulated: true } },
  // ── 商户 / 支付 ──
  {
    label: 'merchant_checkout（choices·带图与分类）',
    card: {
      type: 'merchant_choices', stage: 'choices', brand: '瑞幸', choice_kind: 'product', total: 42,
      categories: [{ label: '生椰系列', send_text: '看看生椰系列' }, { label: '拿铁', send_text: '看看拿铁' }],
      options: [
        { label: '生椰拿铁', subtitle: '¥16.00 起', send_text: '点一杯生椰拿铁', image_url: 'https://img04.luckincoffeecdn.com/pic/1262.png' },
        { label: '厚乳拿铁', subtitle: '¥18.00 起', send_text: '点一杯厚乳拿铁' },
      ],
      _prov: { mode: 'real', vendor: 'luckin', fetched_at: iso(-120_000) },
    },
  },
  {
    label: 'merchant_checkout（preview·规格 chips + 优惠）',
    card: {
      type: 'merchant_order_preview', stage: 'preview', brand: '瑞幸',
      confirmation_context: 'merchant_order_create',
      store_name: '瑞幸咖啡(富通城三期店)', fulfillment: '到店自提',
      items: [{ name: '生椰拿铁', quantity: 1, specs: '大杯 / 少冰' }],
      spec_options: [
        { name: '温度', selected: '少冰', options: [{ label: '常温' }, { label: '少冰' }, { label: '多冰', price_delta_cents: 0 }] },
        { name: '规格', selected: '大杯', options: [{ label: '中杯' }, { label: '大杯', price_delta_cents: 300 }] },
      ],
      discount_cents: 500, amount_cents: 1600,
      buttons: [{ label: '确认下单', send_text: '确认下单' }],
    },
  },
  {
    // 覆盖守卫第一次跑就抓到它：上面两条商户样本用的是 `merchant_choices` /
    // `merchant_order_preview` 两个**别名**，本名 `merchant_checkout` 一条都没有。
    // 三个 type 字符串共用一个渲染器不等于「测了一个就等于测了三个」——
    // 注册表是按字符串查的，漏注册哪一个都会掉进兜底卡。
    label: 'merchant_checkout（order 态 · 已创建未支付）',
    card: {
      type: 'merchant_checkout', stage: 'order', brand: '瑞幸',
      store_name: '瑞幸咖啡(富通城三期店)', order_id: 'LK20260827000456',
      status: 'unpaid', fulfillment: '到店自提',
      items: [{ name: '生椰拿铁', quantity: 1, specs: '大杯 / 少冰' }],
      amount_cents: 1600,
      buttons: [{ label: '去支付', send_text: '去支付瑞幸订单 LK20260827000456' }],
    },
  },
  {
    label: 'mcp_order（含演示角标与幂等命中）',
    card: {
      type: 'mcp_order', brand: '麦当劳', server: 'mcdonalds', order_id: 'MCD20260827000123',
      store_name: '麦当劳(西乡大道店)', fulfillment: '到店取餐', status: 'UNPAID',
      items: [{ name: '巨无霸套餐', quantity: 1, specs: '中可乐' }],
      amount_cents: 3900, duplicate: true, demo: true, demo_label: '演示商户',
      _prov: { mode: 'degraded', note: '演示环境' },
    },
  },
  {
    label: 'mcp_result（readonly·刻意极简）',
    realStack: true,
    card: { type: 'mcp_result', readonly: true, brand: '麦当劳', server: 'mcdonalds', tool: 'list-nutrition-foods', _prov: { mode: 'real', vendor: 'mcdonalds', fetched_at: iso(-600_000) } },
  },
  {
    label: 'payment_qr（有码 · 倒计时中）',
    card: {
      type: 'payment_qr', payment_id: 'pay_001', amount: '15元', merchant: '停车场', order_id: 'PK20260827',
      // 真·可扫的最小 SVG（qrcode SvgPathImage 同形态：XML 声明 + path），验的是解码与渲染
      qr_svg: 'data:image/svg+xml;base64,' +
        'PD94bWwgdmVyc2lvbj0nMS4wJyBlbmNvZGluZz0nVVRGLTgnPz48c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgdmlld0JveD0iMCAwIDIxIDIxIj48cmVjdCB3aWR0aD0iMjEiIGhlaWdodD0iMjEiIGZpbGw9IiNmZmYiLz48cGF0aCBkPSJNMCAwaDd2N2gtN3pNMiAyaDN2M2gtM3pNMTQgMGg3djdoLTd6TTE2IDJoM3YzaC0zek0wIDE0aDd2N2gtN3pNMiAxNmgzdjNoLTN6TTkgMGgxdjFoLTF6TTkgMmgxdjFoLTF6TTkgNGgxdjFoLTF6TTExIDloMXYxaC0xek0xMyAxMWgxdjFoLTF6TTE1IDlIMTZ2MWgtMXpNOSA5aDF2MWgtMXpNMTcgMTNoMXYxaC0xek05IDE3aDF2MWgtMXpNMTEgMTloMXYxaC0xek0xOSAxN2gxdjFoLTF6IiBmaWxsPSIjMDAwIi8+PC9zdmc+',
      expires_at_ms: NOW + 240_000, pay_url: 'https://qr.alipay.com/demo',
      merchant_note: '订单状态以商家为准',
      _prov: { mode: 'mock' },
    },
  },
  {
    label: 'payment_qr（已过期 · 置灰）',
    card: { type: 'payment_qr', payment_id: 'pay_002', amount: '15元', qr_svg: 'data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0nMS4wJz8+PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyMSAyMSI+PHJlY3Qgd2lkdGg9IjIxIiBoZWlnaHQ9IjIxIiBmaWxsPSIjZmZmIi8+PHBhdGggZD0iTTAgMGg3djdoLTd6IiBmaWxsPSIjMDAwIi8+PC9zdmc+', expires_at_ms: NOW - 60_000 },
  },
  {
    label: 'payment_qr（无码 · 降级到安全链接）',
    card: { type: 'payment_qr', payment_id: 'pay_003', amount: '15元', pay_url: 'https://qr.alipay.com/demo', expires_at_ms: NOW + 600_000 },
  },
  { label: 'payment_receipt', card: { type: 'payment_receipt', receipt_id: 'RC20260827001', order_id: 'PK20260827', amount: '15元', scene: 'parking', _prov: { mode: 'mock' } } },
  { label: 'parking_fee', realStack: true, card: { type: 'parking_fee', amount: '15元', plate: '粤B·D12345', order_id: 'current' } },
  // ── 组合 ──
  {
    label: 'card_group（递归）',
    card: {
      type: 'card_group',
      items: [
        { type: 'parking_fee', amount: '15元', plate: '粤B·D12345' },
        { type: 'payment_receipt', receipt_id: 'RC001', amount: '15元' },
      ],
    },
  },
  { label: '未知卡型 → 兜底卡（铁则：绝不 null）', card: { type: 'some_future_card', title: '未来卡型', merchant: '某商户', buttons: [{ label: '看看', send_text: '看看' }], _prov: { mode: 'mock' } } },
  ]
}

/** 覆盖度守卫用的快照（时间敏感字段与它无关，取一次即可） */
export const CARD_FIXTURES: Fixture[] = cardFixtures()
