// 地图坐标判据守卫（M3-3）。
//
// 为什么这几行值得钉：卡片上的坐标是**后端给的**，而模型输出是不可信输入
// （CLAUDE.md §6）。这里最危险的不是崩溃，是**把一个不存在的位置画得像真的**——
// `0,0` 是几内亚湾，缺失坐标被当成一个点画上去，比不画更糟：
// 用户看到一个 marker，不会怀疑它是「没有数据」。
import { mapPointsOf, toMapPoint } from '@/core/map/available'

describe('toMapPoint', () => {
  test('正常点通过', () => {
    expect(toMapPoint({ name: '西湖', lat: 30.2489, lng: 120.1417, address: '龙井路1号' })).toEqual({
      name: '西湖',
      lat: 30.2489,
      lng: 120.1417,
      address: '龙井路1号',
    })
  })

  test('字符串数字也收（后端 JSON 里数值有时是字符串）', () => {
    expect(toMapPoint({ name: 'x', lat: '30.2', lng: '120.1' })?.lat).toBe(30.2)
  })

  test('缺名字给占位而不是丢点（有坐标就画得出来）', () => {
    expect(toMapPoint({ lat: 30, lng: 120 })?.name).toBe('未命名地点')
  })

  test.each([
    ['0,0（几内亚湾——缺失坐标的典型表现，画上去比不画更糟）', { lat: 0, lng: 0 }],
    ['缺 lat', { lng: 120 }],
    ['缺 lng', { lat: 30 }],
    ['NaN', { lat: 'abc', lng: 120 }],
    ['纬度越界', { lat: 91, lng: 120 }],
    ['经度越界', { lat: 30, lng: 181 }],
    ['null', null],
    ['字符串', 'x'],
  ])('%s → null', (_label, input) => {
    expect(toMapPoint(input)).toBeNull()
  })
})

describe('mapPointsOf', () => {
  test('只留挑得出坐标的行', () => {
    const rows = [
      { name: 'a', lat: 30, lng: 120 },
      { name: 'b' }, // 没坐标
      { name: 'c', lat: 0, lng: 0 }, // 假点
      { name: 'd', lat: 31, lng: 121 },
    ]
    expect(mapPointsOf(rows).map((x) => x.name)).toEqual(['a', 'd'])
  })

  test('非数组 → 空（入口据此不渲染）', () => {
    expect(mapPointsOf(undefined)).toEqual([])
    expect(mapPointsOf({} as unknown)).toEqual([])
  })

  test('一行都挑不出时返回空数组——入口不出现，而不是给个空地图', () => {
    expect(mapPointsOf([{ name: 'a' }, { name: 'b' }])).toEqual([])
  })
})
