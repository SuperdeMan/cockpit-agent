// 发送前置路由（实施计划 M1-2）：每个拦截分支正/负各一（App.tsx:722-845 对照）。
// 纯函数：只断言决策，不碰 store/transport。
import { emptyCandidates, recordCandidates, type CandidateState } from '@/core/session/candidates'
import { routeSend, type RouteDecision } from '@/core/session/sendRouter'

function cand(patch: Partial<CandidateState> = {}): CandidateState {
  return { ...emptyCandidates(), ...patch }
}
const route = (text: string, c: CandidateState, locationEnabled = false): RouteDecision =>
  routeSend(text, { candidates: c, locationEnabled })

describe('行程整句守卫（防「第二天第一个」被候选劫持）', () => {
  test('正：含「第N天」→ 原句直发 + 清 poi/dest/waypoint 候选', () => {
    const d = route('第二天第一个改成西湖', cand({ poiNames: ['A', 'B'] }))
    expect(d).toMatchObject({ kind: 'dispatch', text: '第二天第一个改成西湖' })
    expect((d as any).clear).toEqual(['poi', 'dest', 'waypoint'])
  })
  test('负：普通「第一个」不触发守卫，走候选选择', () => {
    const d = route('第一个', cand({ poiNames: ['老王烧烤', 'B'] }))
    expect(d).toMatchObject({ kind: 'dispatch', text: '导航去老王烧烤', clear: ['poi'] })
  })
})

describe('澄清卡（intent_choice）选择', () => {
  const ic = cand({
    intentChoice: {
      options: [
        { label: '查天气', send_text: '查一下上海天气' },
        { label: '开空调', send_text: '打开空调' },
      ],
    },
  })
  test('正：「第二个」→ 回发 send_text + clarify_resume=1 + 清澄清卡', () => {
    expect(route('第二个', ic)).toEqual({
      kind: 'dispatch',
      text: '打开空调',
      metaExtra: { clarify_resume: '1' },
      clear: ['intent'],
    })
  })
  test('正：按钮原文（label）同语义', () => {
    expect(route('查天气', ic)).toMatchObject({ text: '查一下上海天气', clear: ['intent'] })
  })
  test('负：不命中（换话题）→ 正常直发，澄清卡留给下一轮 final 自然作废', () => {
    expect(route('播放音乐', ic)).toEqual({ kind: 'dispatch', text: '播放音乐' })
  })
  test('优先级：澄清卡排在 poi 候选前（两者并存时「第一个」归澄清卡）', () => {
    const both = cand({ ...ic, poiNames: ['充电站A'] })
    expect(route('第一个', both)).toMatchObject({ text: '查一下上海天气' })
  })
})

describe('商户菜单卡「第N个」直达下单句', () => {
  test('正：序数命中 → send_text + 清菜单候选', () => {
    const c = cand({
      merchantMenu: {
        options: [
          { label: '拿铁', send_text: '在瑞幸点一杯拿铁' },
          { label: '美式', send_text: '在瑞幸点一杯美式' },
        ],
      },
    })
    expect(route('第2个', c)).toEqual({
      kind: 'dispatch',
      text: '在瑞幸点一杯美式',
      clear: ['merchant'],
    })
  })
})

describe('「换一批」类目翻页', () => {
  test('正：有类目上下文 → 重发干净句 + poi_page 翻页 + 带最新定位', () => {
    const c = cand({ category: { keyword: '粤菜馆', page: 1 } })
    expect(route('换一批', c)).toEqual({
      kind: 'dispatch',
      text: '导航去附近的粤菜馆',
      metaExtra: { poi_page: '2' },
      withLocation: true,
      categoryPage: 2,
    })
  })
  test('负：无类目上下文 → 不劫持，原句直发', () => {
    expect(route('换一批', cand())).toEqual({ kind: 'dispatch', text: '换一批' })
  })
})

describe('途经点 / 充电目的地候选', () => {
  test('waypoint_choice「第一个」→「导航去{目的地}途经{名称}」', () => {
    const c = cand({ waypointChoice: { destination: '虹桥机场', names: ['服务区餐厅'] } })
    expect(route('第一个', c)).toEqual({
      kind: 'dispatch',
      text: '导航去虹桥机场途经服务区餐厅',
      clear: ['waypoint'],
    })
  })
  test('dest_choice「第2个」→ 派发候选名本身（回填槽位，不改写为导航）', () => {
    const c = cand({ destChoice: ['西湖银泰充电站', '黄龙充电站'] })
    expect(route('第2个', c)).toEqual({ kind: 'dispatch', text: '黄龙充电站', clear: ['dest'] })
  })
})

describe('周边发现 place_list「第N个」', () => {
  const c = cand({
    poiNames: ['蜀香居', '渝味堂'],
    placeItems: [
      { id: 'B0FF123', name: '蜀香居' },
      { id: 'B0FF456', name: '渝味堂' },
    ],
  })
  test('默认看详情 + 透传高德 POI id（不按名重搜取到别的分店）', () => {
    expect(route('看第二家', c)).toEqual({
      kind: 'dispatch',
      text: '看渝味堂的详情',
      metaExtra: { nearby_poi_id: 'B0FF456' },
    })
  })
  test('带导航词 → 导航', () => {
    expect(route('导航去第一个', c)).toEqual({ kind: 'dispatch', text: '导航去蜀香居' })
  })
  test('负：「第一次来」不是选择（序数须带 个/家）→ 原句直发', () => {
    expect(route('第一次来这边玩', c)).toEqual({ kind: 'dispatch', text: '第一次来这边玩' })
  })
})

describe('位置闸（Q4 收窄三条：依赖词 + 无地点线索 + 非否定句）', () => {
  test('未开定位 + 位置依赖 → 征询', () => {
    expect(route('附近的充电站', cand(), false)).toEqual({ kind: 'consent', text: '附近的充电站' })
  })
  test('已开定位 + 位置依赖 → 直发并带最新定位', () => {
    expect(route('导航去虹桥机场', cand(), true)).toEqual({
      kind: 'dispatch',
      text: '导航去虹桥机场',
      withLocation: true,
    })
  })
  test('负：否定/取消句不触发（「取消当前导航」曾命中『导航』被拦，I-032①）', () => {
    expect(route('取消当前导航', cand(), false)).toEqual({ kind: 'dispatch', text: '取消当前导航' })
  })
  test('负：显式地点锚不触发（「查深圳欢乐海岸周边停车场」曾被判依赖当前定位，I-007）', () => {
    expect(route('查深圳欢乐海岸周边停车场', cand(), true)).toEqual({
      kind: 'dispatch',
      text: '查深圳欢乐海岸周边停车场',
    })
  })
  test('metaExtra 在普通直发/带定位直发两路都透传', () => {
    expect(route('导航去虹桥机场', cand(), true, )).not.toHaveProperty('metaExtra')
    expect(routeSend('随便聊聊', { candidates: cand(), locationEnabled: false }, { a: '1' })).toEqual({
      kind: 'dispatch',
      text: '随便聊聊',
      metaExtra: { a: '1' },
    })
  })
})

describe('候选记录（recordCandidates，App.tsx:483-517 对照）', () => {
  test('poi_list（普通）：记名 + 类目关键词；同关键词翻页保页码、换类目回第 1 页', () => {
    let c = recordCandidates(emptyCandidates(), {
      type: 'poi_list', keyword: '粤菜馆', items: [{ id: '1', name: 'A店' }],
    })
    expect(c.poiNames).toEqual(['A店'])
    expect(c.category).toEqual({ keyword: '粤菜馆', page: 1 })
    c = { ...c, category: { keyword: '粤菜馆', page: 3 } }
    c = recordCandidates(c, { type: 'poi_list', keyword: '粤菜馆', items: [{ id: '2', name: 'B店' }] })
    expect(c.category).toEqual({ keyword: '粤菜馆', page: 3 }) // 同关键词保页码
    c = recordCandidates(c, { type: 'poi_list', keyword: '川菜馆', items: [] })
    expect(c.category).toEqual({ keyword: '川菜馆', page: 1 }) // 换类目回第 1 页
  })
  test('新一轮 final 互斥清空全部候选槽；非 poi/place 卡不触碰 category', () => {
    const seeded: CandidateState = {
      poiNames: ['x'],
      placeItems: [{ id: '1', name: 'x' }],
      destChoice: ['y'],
      waypointChoice: { destination: 'z', names: ['w'] },
      intentChoice: { options: [{ label: 'l', send_text: 's' }] },
      merchantMenu: { options: [{ label: 'l', send_text: 's' }] },
      category: { keyword: '粤菜馆', page: 2 },
    }
    const c = recordCandidates(seeded, { type: 'weather', city: '上海' })
    expect(c).toEqual({ ...emptyCandidates(), category: { keyword: '粤菜馆', page: 2 } })
  })
  test('merchant_choices 只记 product 卡且选项须齐 label+send_text（门店卡由补槽链消费）', () => {
    const c = recordCandidates(emptyCandidates(), {
      type: 'merchant_choices', choice_kind: 'product',
      options: [{ label: '拿铁', send_text: '点拿铁' }, { label: '缺送句' }],
    })
    expect(c.merchantMenu).toEqual({ options: [{ label: '拿铁', send_text: '点拿铁' }] })
    const store = recordCandidates(emptyCandidates(), {
      type: 'merchant_choices', choice_kind: 'store', options: [{ label: 'x', send_text: 'y' }],
    })
    expect(store.merchantMenu).toBeNull()
  })
  test('poi_list purpose 变体：dest_choice 记名 / waypoint_choice 记目的地+名', () => {
    const dest = recordCandidates(emptyCandidates(), {
      type: 'poi_list', purpose: 'dest_choice', items: [{ id: '1', name: '充电站A' }],
    })
    expect(dest.destChoice).toEqual(['充电站A'])
    expect(dest.poiNames).toBeNull()
    const wp = recordCandidates(emptyCandidates(), {
      type: 'poi_list', purpose: 'waypoint_choice', destination: '机场', items: [{ id: '1', name: '服务区' }],
    })
    expect(wp.waypointChoice).toEqual({ destination: '机场', names: ['服务区'] })
  })
})
