/**
 * react-native-amap3d 补丁的回归锁（2026-09-11 地图路线）。
 *
 * 真机（OPPO，prod 包 d03c9e675-dirty）实证：给 Marker 传自定义 children 后，库在 onLayout 里调
 * `invoke("update")` → `UIManager.dispatchViewManagerCommand(handle, command, undefined)`；Fabric 互操作层把
 * 缺席的 args 当 null 交给 legacy ViewManager ⇒ `UnexpectedNativeTypeException: expected Array, got a null`，
 * 整个 App 退到桌面。补丁只做一件事：args 永远是数组。原生侧 Marker.kt 自己挂了 layout 监听更新图标，
 * 这条命令在 Android 上本就是冗余的，所以补丁不改任何可见行为。
 * 与 cameraNativePatch.test 同类：验 npm ci 后实际安装的源码，不替代 APK 编译与真机取证。
 */
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(__dirname, '..')

test('patches/ 里有 amap3d 3.2.4 的补丁，且只碰 lib/src/component.ts', () => {
  const patch = fs.readFileSync(path.join(root, 'patches/react-native-amap3d+3.2.4.patch'), 'utf8')
  const files = [...patch.matchAll(/^diff --git a\/(\S+)/gm)].map((m) => m[1])
  expect(files).toEqual(['node_modules/react-native-amap3d/lib/src/component.ts'])
  expect(patch).toContain('params ?? []')
})

test('安装后的 component.ts 已打上补丁：dispatchViewManagerCommand 永远收到数组', () => {
  const src = fs.readFileSync(path.join(root, 'node_modules/react-native-amap3d/lib/src/component.ts'), 'utf8')
  expect(src).toContain('UIManager.dispatchViewManagerCommand(handle, command, params ?? []);')
  expect(src).not.toMatch(/dispatchViewManagerCommand\(handle, command, params\);/)
})
