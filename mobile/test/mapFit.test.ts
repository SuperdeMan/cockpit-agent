// 按点集自动缩放的守卫（M3-3 打磨）。
//
// 这里刻意**不断言具体 zoom 值**（那种断言一改 padding 就红，且红了也说明不了对错）。
// 主张是「所有点都在画面里」，所以断言就照着这句话写：把算出的 zoom 反推成可视跨度，
// 逐点检查它落不落在里面。反向验证做过：把 `fitCamera` 的 log2 底改成 10（等于算错），
// 下面 containment 那几条立刻红。
import { fitCamera, type Viewport } from '@/core/map/fit'
import type { MapPoint } from '@/core/map/available'

const pt = (name: string, lat: number, lng: number): MapPoint => ({ name, lat, lng })

const PHONE: Viewport = { width: 400, height: 800 }

/** zoom 下 1 个 dp 对应多少经度 */
function degPerPxLng(zoom: number): number {
  return 360 / (256 * Math.pow(2, zoom))
}

function mercatorY(lat: number): number {
  const rad = (Math.max(-85.05, Math.min(85.05, lat)) * Math.PI) / 180
  return Math.log(Math.tan(Math.PI / 4 + rad / 2))
}

/** 每个点都在可视矩形内（含 padding 的可用区）——这就是「fit」的定义 */
function expectAllVisible(points: MapPoint[], viewport: Viewport, padding: number) {
  const cam = fitCamera(points, viewport, { padding })!
  expect(cam).not.toBeNull()
  const usableW = viewport.width - padding * 2
  const usableH = viewport.height - padding * 2
  const halfLng = (usableW / 2) * degPerPxLng(cam.zoom)
  const yPerPx = (2 * Math.PI) / (256 * Math.pow(2, cam.zoom))
  const halfY = (usableH / 2) * yPerPx
  const cy = mercatorY(cam.target.latitude)
  for (const p of points) {
    expect(Math.abs(p.lng - cam.target.longitude)).toBeLessThanOrEqual(halfLng + 1e-6)
    expect(Math.abs(mercatorY(p.lat) - cy)).toBeLessThanOrEqual(halfY + 1e-6)
  }
}

describe('fitCamera', () => {
  test('零点返回 null（兜底中心由调用方给，这里不许凭空造一个点）', () => {
    expect(fitCamera([], PHONE)).toBeNull()
  })

  test('单点：目标=该点，zoom=约定的单点档', () => {
    const cam = fitCamera([pt('西湖', 30.2489, 120.1417)], PHONE, { singleZoom: 15 })!
    expect(cam.target.latitude).toBeCloseTo(30.2489, 4)
    expect(cam.target.longitude).toBeCloseTo(120.1417, 4)
    expect(cam.zoom).toBe(15)
  })

  test('所有点重合 → 与单点同档（跨度为 0 解不出 zoom）', () => {
    const same = [pt('a', 30, 120), pt('b', 30, 120), pt('c', 30, 120)]
    expect(fitCamera(same, PHONE, { singleZoom: 15 })!.zoom).toBe(15)
  })

  test('近距离两点（~0.5km）都在画面里，且比远距离更近（zoom 更大）', () => {
    const near = [pt('a', 22.5429, 113.9089), pt('b', 22.5471, 113.9112)]
    const far = [pt('杭州', 30.2489, 120.1417), pt('深圳', 22.5429, 113.9089)]
    expectAllVisible(near, PHONE, 48)
    expectAllVisible(far, PHONE, 48)
    expect(fitCamera(near, PHONE, { padding: 48 })!.zoom).toBeGreaterThan(
      fitCamera(far, PHONE, { padding: 48 })!.zoom,
    )
  })

  test('固定 zoom 12/15 的老毛病：0.5km 两点不再被顶到远景', () => {
    // 上一轮真机读数「两点相距 0.5km 时视野偏远」的回归探针
    const near = [pt('a', 22.5429, 113.9089), pt('b', 22.5471, 113.9112)]
    expect(fitCamera(near, PHONE, { padding: 48 })!.zoom).toBeGreaterThan(14)
  })

  test('多点（周边 POI 一屏）逐点可见', () => {
    const rows = [
      pt('瑞幸(西乡店)', 22.5652, 113.8571),
      pt('瑞幸(宝安店)', 22.5539, 113.8836),
      pt('麦当劳(新安店)', 22.5405, 113.9022),
      pt('星巴克(海雅店)', 22.5721, 113.8493),
    ]
    expectAllVisible(rows, PHONE, 48)
  })

  test('视口越窄，zoom 越小（同一批点要装进更小的画面）', () => {
    const rows = [pt('a', 22.54, 113.90), pt('b', 22.57, 113.94)]
    const wide = fitCamera(rows, { width: 900, height: 800 }, { padding: 48 })!
    const narrow = fitCamera(rows, { width: 320, height: 800 }, { padding: 48 })!
    expect(narrow.zoom).toBeLessThan(wide.zoom)
  })

  test('padding 越大，zoom 越小；底部信息条盖住的区域不许把点藏进去', () => {
    const rows = [pt('a', 22.54, 113.90), pt('b', 22.57, 113.94)]
    const small = fitCamera(rows, PHONE, { padding: 16 })!
    const big = fitCamera(rows, PHONE, { padding: { top: 56, right: 48, bottom: 160, left: 48 } })!
    expect(big.zoom).toBeLessThanOrEqual(small.zoom)
    // bottom 留白 160 意味着可视高度少了那一块，点必须落在剩下的区域里
    const yPerPx = (2 * Math.PI) / (256 * Math.pow(2, big.zoom))
    const halfY = ((800 - 56 - 160) / 2) * yPerPx
    const cy = mercatorY(big.target.latitude)
    for (const p of rows) expect(Math.abs(mercatorY(p.lat) - cy)).toBeLessThanOrEqual(halfY + 1e-6)
  })

  test('纬度走墨卡托而不是度数：同样的度数跨度，高纬度需要更小的 zoom', () => {
    const equator = [pt('a', 0, 0), pt('b', 1, 0)]
    const north = [pt('a', 60, 0), pt('b', 61, 0)]
    expect(fitCamera(north, PHONE, { padding: 48 })!.zoom).toBeLessThan(
      fitCamera(equator, PHONE, { padding: 48 })!.zoom,
    )
    expectAllVisible(north, PHONE, 48)
  })

  test('跨 180° 接线：走绕行跨度，与不跨线的同跨度点集算出同一个 zoom', () => {
    // 对照物是这条断言的全部力量所在：179°E↔179°W 与 1°W↔1°E 都是 2° 跨度，
    // 算对了就该**逐值相同**；按直接跨度（358°）算则会掉到 minZoom，成一张世界地图。
    const across = fitCamera([pt('西', 0, 179), pt('东', 0, -179)], PHONE, { padding: 48 })!
    const plain = fitCamera([pt('西', 0, -1), pt('东', 0, 1)], PHONE, { padding: 48 })!
    expect(across.zoom).toBe(plain.zoom)
    expect(Math.abs(across.target.longitude)).toBeGreaterThan(179) // 中心在接线附近，不是 0
    expect(across.zoom).toBeGreaterThan(5) // 反面：世界地图档（minZoom=3）是错的
  })

  test('视口还没测出来（首帧 0×0）不产 NaN/Infinity，退回单点档', () => {
    const rows = [pt('a', 22.54, 113.90), pt('b', 22.57, 113.94)]
    const cam = fitCamera(rows, { width: 0, height: 0 }, { singleZoom: 15 })!
    expect(cam.zoom).toBe(15)
    expect(Number.isFinite(cam.target.latitude)).toBe(true)
  })

  test('zoom 夹在 [minZoom, maxZoom] 内', () => {
    const worldwide = [pt('a', -60, -170), pt('b', 60, 170)]
    expect(fitCamera(worldwide, PHONE, { minZoom: 3 })!.zoom).toBeGreaterThanOrEqual(3)
    const microscopic = [pt('a', 30, 120), pt('b', 30.000001, 120.000001)]
    expect(fitCamera(microscopic, PHONE, { maxZoom: 17 })!.zoom).toBeLessThanOrEqual(17)
  })
})
