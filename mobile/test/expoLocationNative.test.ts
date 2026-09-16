/**
 * expo-location Android 实现的两条前提（core/location/appLocation.ts 依赖它们，库升级即红）：
 *
 * ① `getCurrentPositionAsync` 的 `timeInterval` 在原生侧同时喂给 `CurrentLocationRequest.maxUpdateAgeMillis`
 *    ——这是「GMS 手里有 5 分钟内的定位就立刻给、不点 GPS」的依据。默认（Highest）只有 1000ms，
 *    正是 2026-09-16 真机每轮在发送前等满 20s 的成因之一。
 * ② `getLastKnownPositionAsync` 读的是 fused provider 的 `lastLocation`（全设备共享的缓存），
 *    不是 LocationManager 按 provider 的 GPS 优先 last-known——后者在这台测试机上是 6 天前的 GPS 点。
 * 与 amapPatch.test 同类：验 npm ci 后实际安装的源码，不替代真机取证。
 */
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(__dirname, '..')
const base = path.join(root, 'node_modules/expo-location/android/src/main/java/expo/modules/location')

test('timeInterval → CurrentLocationRequest.maxUpdateAgeMillis 的映射仍在', () => {
  const src = fs.readFileSync(path.join(base, 'LocationHelpers.kt'), 'utf8')
  expect(src).toMatch(/options\.timeInterval\?\.let\s*\{\s*locationParams\.interval = it\s*\}/)
  expect(src).toContain('setMaxUpdateAgeMillis(locationParams.interval)')
  // 缺省档位的 maxUpdateAge 只有 1s：不显式传 timeInterval 就等于「只认 1s 内的缓存」
  expect(src).toMatch(/ACCURACY_HIGHEST -> LocationParams\(accuracy = LocationAccuracy\.HIGH, distance = 25f, interval = 1000\)/)
})

test('getLastKnownPositionAsync 读的是 fused lastLocation，且按 maxAge 过滤', () => {
  const mod = fs.readFileSync(path.join(base, 'LocationModule.kt'), 'utf8')
  expect(mod).toMatch(/private suspend fun getLastKnownLocation\(\): Location\? \{[\s\S]*?mLocationProvider\.lastLocation/)
  const helpers = fs.readFileSync(path.join(base, 'LocationHelpers.kt'), 'utf8')
  expect(helpers).toMatch(/val maxAge = options\.maxAge \?: Double\.MAX_VALUE[\s\S]*?timeDiff <= maxAge/)
})
