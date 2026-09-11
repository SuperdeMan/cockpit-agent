// mobile/test/mapGeometry.test.ts
// 「这张卡能不能画、画什么」的唯一判据（2026-09-11，设计 §3.3）。三个消费方（卡内入口 / 地图页 / 舞台内嵌地图）
// 都读它，所以这里钉的是**输出形状**：角色顺序、折线校验与抽样、参数回环、以及「没几何 ⇒ null」（入口不出现）。
// 坐标是后端给的、模型输出是不可信输入：0,0 与 NaN 的点必须被丢掉而不是画上去（mapAvailable.test 同族）。
import { cardGeometry, fitPointsOf, geometryParams, parsePath, parseGeometryParams } from '@/core/map/geometry'

const ROUTE = {
  type: 'route_plan', origin: '当前位置', destination: '机场', distance_km: 24.6, duration_min: 38,
  origin_loc: { lat: 22.53, lng: 113.95 },
  destination_loc: { lat: 22.64, lng: 113.81 },
  waypoints: [{ name: '瑞幸', address: 'x', lat: 22.578, lng: 113.856 }, { name: '没坐标的途经点' }],
  path: [[22.53, 113.95], [22.58, 113.86], [22.64, 113.81]],
}

describe('route_plan', () => {
  test('没有任何坐标也没有折线 ⇒ null（入口不出现，与 M3-3 可降级一致）', () => {
    expect(cardGeometry({ type: 'route_plan', destination: '机场', waypoints: [] })).toBeNull()
  })
  test('完整几何：起 → 途经（只收有坐标的）→ 终，折线原样；标题与概览', () => {
    const g = cardGeometry(ROUTE)!
    expect(g.points.map((pt) => [pt.role, pt.name])).toEqual([
      ['origin', '当前位置'], ['waypoint', '瑞幸'], ['dest', '机场'],
    ])
    expect(g.path).toEqual([{ lat: 22.53, lng: 113.95 }, { lat: 22.58, lng: 113.86 }, { lat: 22.64, lng: 113.81 }])
    expect(g.title).toBe('当前位置 → 机场')
    expect(g.subtitle).toBe('24.6km · 约38分钟 · 途经 2')
  })
  test('只有终点坐标（provider 没给几何时后端只写 destination_loc）⇒ 一个点、无折线', () => {
    const g = cardGeometry({ type: 'route_plan', destination: '机场', waypoints: [], destination_loc: { lat: 22.64, lng: 113.81 } })!
    expect(g.points).toHaveLength(1)
    expect(g.points[0].role).toBe('dest')
    expect(g.path).toEqual([])
  })
  test('只有折线也能画（终点坐标缺席）', () => {
    const g = cardGeometry({ type: 'route_plan', destination: '机场', waypoints: [], path: ROUTE.path })!
    expect(g.points).toEqual([])
    expect(g.path).toHaveLength(3)
  })
  test('时长过一小时的概览写「约1小时2分钟」', () => {
    expect(cardGeometry({ ...ROUTE, duration_min: 62 })!.subtitle).toBe('24.6km · 约1小时2分钟 · 途经 2')
  })
})

describe('charging_route / trip_itinerary / 周边族', () => {
  test('charging_route：stops 带坐标 ⇒ 补电站角色；一个都没有且无折线 ⇒ null', () => {
    const g = cardGeometry({
      type: 'charging_route', destination: '杭州', distance_km: 300, duration_min: 200,
      stops: [{ name: 'A 站', lat: 30.1, lng: 120.1, at_km: 150 }],
      destination_loc: { lat: 30.29, lng: 120.21 },
    })!
    expect(g.points.map((pt) => pt.role)).toEqual(['stop', 'dest'])
    expect(g.subtitle).toBe('300km · 约3小时20分钟 · 补电 1')
    expect(cardGeometry({ type: 'charging_route', destination: '杭州', stops: [{ name: 'A 站' }] })).toBeNull()
  })
  test('trip_itinerary：只收接地停靠点，名字带 D{day}', () => {
    const g = cardGeometry({
      type: 'trip_itinerary', destination: '杭州', days: 2,
      itinerary: [
        { day_index: 1, stops: [{ name: '西湖', grounded: true, poi: { lat: 30.25, lng: 120.14 } }, { name: '待定', grounded: false }] },
        { day_index: 2, stops: [{ name: '灵隐寺', grounded: true, poi: { lat: 30.24, lng: 120.10 } }] },
      ],
    })!
    expect(g.points.map((pt) => pt.name)).toEqual(['D1 西湖', 'D2 灵隐寺'])
    expect(g.points.every((pt) => pt.role === 'poi')).toBe(true)
    expect(cardGeometry({ type: 'trip_itinerary', destination: '杭州', days: 1, itinerary: [{ day_index: 1, stops: [{ name: '待定', grounded: false }] }] })).toBeNull()
  })
  test('place_list 只收带坐标的行；poi_detail / place_detail 一个点', () => {
    const g = cardGeometry({ type: 'place_list', category: '咖啡', items: [{ name: 'a', lat: 22.5, lng: 113.8 }, { name: 'b' }] })!
    expect(g.points).toHaveLength(1)
    expect(g.title).toBe('周边 · 咖啡')
    expect(cardGeometry({ type: 'poi_detail', name: '杭州东站', lat: 30.29, lng: 120.21 })!.points[0]).toMatchObject({ role: 'poi', name: '杭州东站' })
  })
  test('契约里不带坐标的卡型 ⇒ null', () => {
    expect(cardGeometry({ type: 'weather' })).toBeNull()
    expect(cardGeometry(null)).toBeNull()
    expect(cardGeometry('x')).toBeNull()
  })
})

describe('parsePath：逐点校验 + 抽样', () => {
  test('0,0 / NaN / 越界的点被丢掉；对象形状也收', () => {
    expect(parsePath([[0, 0], [22.5, 113.8], ['a', 1], [91, 100], { lat: 22.6, lng: 113.9 }])).toEqual([
      { lat: 22.5, lng: 113.8 }, { lat: 22.6, lng: 113.9 },
    ])
    expect(parsePath('nope')).toEqual([])
  })
  test('超过上限按等距抽样，保头尾', () => {
    const raw = Array.from({ length: 1000 }, (_, i) => [22 + i / 1000, 113 + i / 1000])
    const out = parsePath(raw, 100)
    expect(out).toHaveLength(100)
    expect(out[0]).toEqual({ lat: 22, lng: 113 })
    expect(out[99]).toEqual({ lat: 22 + 999 / 1000, lng: 113 + 999 / 1000 })
  })
})

test('路由参数回环：角色与折线都保真', () => {
  const g = cardGeometry(ROUTE)!
  const back = parseGeometryParams(geometryParams(g))
  expect(back.points).toEqual(g.points)
  expect(back.path).toEqual(g.path)
  expect(back.title).toBe(g.title)
  expect(back.subtitle).toBe(g.subtitle)
})

test('fitPointsOf 把折线点也算进相机（折线可能伸得比两端标注更远）', () => {
  const g = cardGeometry(ROUTE)!
  expect(fitPointsOf(g)).toHaveLength(g.points.length + g.path.length)
})
