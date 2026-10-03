// 桌面 Shortcut 图标（v3 P7，Figma 07 系统面 78:2616）：插件在 prebuild 期读共享图标库生成 VectorDrawable。
//
// 两条判据：
//  1. 声明的每枚字形在共享图标库里读得到、转得出笔画——图标改名或换成不支持的元素时这里先红，
//     不必等一次十几分钟的 release 构建在 prebuild 里炸；
//  2. 插件里的两色是 ui/theme.ts 深色 bg / accent 的拷贝（prebuild 读不了 TS），在这里对账。
import { readFileSync } from 'fs'
import { join } from 'path'

import { paletteOf } from '@/ui/theme'

const icons = require('../plugins/shortcut-icons') as {
  ICON_COLORS: { bg: string; fg: string }
  readIcon(dir: string, name: string): { w: number; h: number; body: string }
  glyphPaths(body: string): { d: string }[]
  shortcutIconFiles(dir: string, id: string, icon: string): Record<string, string>
}

const COMPONENTS = join(__dirname, '..', '..', 'hmi', 'src', 'components')
// 判据读 Shortcut 的声明源本身（同 deepLinkIntent 那条），不另抄一份名单
const plugin = readFileSync(join(__dirname, '..', 'plugins', 'with-shortcuts.js'), 'utf8')
const declared = [...plugin.matchAll(/id:\s*'([^']+)'[^\n]*icon:\s*'([^']+)'/g)].map((m) => ({ id: m[1], icon: m[2] }))

test('每条 Shortcut 都声明了字形，且能从共享图标库生成三份资源', () => {
  expect(declared.map((d) => d.id)).toEqual(['voice', 'vehicle']) // 读不到就等于这条守卫恒绿
  for (const { id, icon } of declared) {
    const files = icons.shortcutIconFiles(COMPONENTS, id, icon)
    expect(Object.keys(files).sort()).toEqual([
      `drawable-anydpi-v26/ic_shortcut_${id}.xml`,
      `drawable/ic_shortcut_${id}.xml`,
      `drawable/ic_shortcut_${id}_fg.xml`,
    ])
    // 图标库里有几笔，前景里就画几笔（circle 也转成了 path，没有被静默丢掉）
    const strokes = (icons.readIcon(COMPONENTS, icon).body.match(/<(path|circle)\b/g) ?? []).length
    expect(strokes).toBeGreaterThan(0)
    expect(files[`drawable/ic_shortcut_${id}_fg.xml`].match(/<path /g)?.length).toBe(strokes)
    expect(plugin).toContain('android:icon="@drawable/ic_shortcut_${s.id}"')
  }
})

test('读不到的图标名直接报错，不静默回落', () => {
  expect(() => icons.readIcon(COMPONENTS, 'no-such-icon')).toThrow(/找不到图标/)
  expect(() => icons.glyphPaths('<rect x="1" y="1" width="2" height="2"/>')).toThrow(/不支持/)
})

test('两色与 theme.ts 深色对账：深空底 + 交互色', () => {
  const p = paletteOf('dark', true, 'normal')
  expect(icons.ICON_COLORS.bg.toUpperCase()).toBe(p.bg.toUpperCase())
  expect(icons.ICON_COLORS.fg.toUpperCase()).toBe(p.accent.toUpperCase())
})
