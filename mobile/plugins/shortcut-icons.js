// 桌面 Shortcut 图标（v3 P7，Figma 07 系统面 78:2616「App Shortcuts」）：40dp 深空圆底 + 22dp 交互色线性字形。
// 纯函数、零 expo 依赖——with-shortcuts.js 在 prebuild 期调用，test/shortcutIcons.test.ts 直接调用对账。
// 字形读共享图标库 hmi/src/components/icons.{gen,custom}.ts（与 App 内 Icon.tsx 同一份数据、同一份
// 「紧致 viewBox 居中进 24×24 · 1.8 stroke · round cap/join」契约），不另抄路径。
// 两色是 ui/theme.ts 深色 bg / accent 的拷贝（prebuild 读不了 TS），由上面那份测试对账。
const fs = require('fs')
const path = require('path')

const ICON_COLORS = { bg: '#06080F', fg: '#46D6E0' }
const STROKE = 1.8
/** Figma：40dp 圆里 22dp 字形（0.55）；自适应图标的可见区是 108dp 画布中间 72dp ⇒ 24 格字形框画成 39.6dp */
const ADAPTIVE_SCALE = (0.55 * 72) / 24
/** API 25 的旧式图标：48dp 整圆底，同一比例 ⇒ 字形框 26.4dp */
const LEGACY_SCALE = (0.55 * 48) / 24

// 图标库一项一行：`"voice-input": { w: 13.8, h: 18.8, body: "<g> … </g>" },`（生成文件，双引号体内 \" 转义）
// 或 `vehicle: { w: 24, h: 24, body: '<path …/>' },`（手写补充，单引号体）
const ENTRY = /^\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$]*))\s*:\s*\{\s*w:\s*([\d.]+),\s*h:\s*([\d.]+),\s*body:\s*(["'])(.*)\6\s*\},?\s*$/

/** 从共享图标库读一枚图标；读不到就停（静默回落成 App 图标的话，改了名的图标会悄悄从桌面消失） */
function readIcon(componentsDir, name) {
  for (const file of ['icons.gen.ts', 'icons.custom.ts']) {
    for (const line of fs.readFileSync(path.join(componentsDir, file), 'utf8').split('\n')) {
      const m = ENTRY.exec(line)
      if (!m || (m[1] ?? m[2] ?? m[3]) !== name) continue
      const body = m[6] === '"' ? m[7].replace(/\\"/g, '"') : m[7].replace(/\\'/g, "'")
      return { w: Number(m[4]), h: Number(m[5]), body }
    }
  }
  throw new Error(`shortcut-icons: 共享图标库里找不到图标 ${name}`)
}

function attrsOf(s) {
  const out = {}
  for (const a of s.matchAll(/([\w-]+)="([^"]*)"/g)) out[a[1]] = a[2]
  return out
}

/** 线性图标 → VectorDrawable 笔画。只认 path / circle——别的元素直接报错，不静默丢笔画 */
function glyphPaths(body) {
  const bad = body.match(/<(rect|line|polyline|polygon|ellipse|text|use|image)\b/)
  if (bad) throw new Error(`shortcut-icons: 不支持的 SVG 元素 <${bad[1]}>`)
  const out = []
  for (const m of body.matchAll(/<(path|circle)\b([^>]*?)\/?>/g)) {
    const a = attrsOf(m[2])
    const d =
      m[1] === 'path'
        ? a.d
        : `M${Number(a.cx) - Number(a.r)},${a.cy}a${a.r},${a.r} 0 1,0 ${2 * Number(a.r)},0a${a.r},${a.r} 0 1,0 ${-2 * Number(a.r)},0`
    if (!d) throw new Error('shortcut-icons: path 缺 d')
    out.push({
      d,
      width: a['stroke-width'] ? Number(a['stroke-width']) : STROKE,
      stroke: a.stroke !== 'none',
      fill: a.fill === 'currentColor',
    })
  }
  if (!out.length) throw new Error('shortcut-icons: 图标没有可画的笔画')
  return out
}

/** 字形组：紧致 viewBox 先居中进 24 格（同 Icon.tsx），24 格再按 scale 居中进 size 画布 */
function glyphGroup(icon, scale, size) {
  const tx = size / 2 - 12 * scale + ((24 - icon.w) / 2) * scale
  const ty = size / 2 - 12 * scale + ((24 - icon.h) / 2) * scale
  const paths = glyphPaths(icon.body).map(
    (p) =>
      `    <path android:pathData="${p.d}"` +
      (p.stroke
        ? ` android:strokeColor="${ICON_COLORS.fg}" android:strokeWidth="${p.width}" android:strokeLineCap="round" android:strokeLineJoin="round"`
        : '') +
      ` android:fillColor="${p.fill ? ICON_COLORS.fg : '#00000000'}" />`,
  )
  return (
    `  <group android:translateX="${tx.toFixed(3)}" android:translateY="${ty.toFixed(3)}" ` +
    `android:scaleX="${scale.toFixed(4)}" android:scaleY="${scale.toFixed(4)}">\n${paths.join('\n')}\n  </group>`
  )
}

function vector(size, inner) {
  return (
    '<?xml version="1.0" encoding="utf-8"?>\n' +
    `<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="${size}dp" android:height="${size}dp" ` +
    `android:viewportWidth="${size}" android:viewportHeight="${size}">\n${inner}\n</vector>\n`
  )
}

/** 一条 Shortcut 的三份资源（相对 res/）：自适应前景（108dp）、自适应图标（v26+）、API 25 的旧式整图（48dp 圆底） */
function shortcutIconFiles(componentsDir, id, iconName) {
  const icon = readIcon(componentsDir, iconName)
  return {
    [`drawable/ic_shortcut_${id}_fg.xml`]: vector(108, glyphGroup(icon, ADAPTIVE_SCALE, 108)),
    [`drawable-anydpi-v26/ic_shortcut_${id}.xml`]:
      '<?xml version="1.0" encoding="utf-8"?>\n' +
      '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n' +
      '  <background android:drawable="@drawable/ic_shortcut_bg" />\n' +
      `  <foreground android:drawable="@drawable/ic_shortcut_${id}_fg" />\n` +
      '</adaptive-icon>\n',
    [`drawable/ic_shortcut_${id}.xml`]: vector(
      48,
      `  <path android:pathData="M0,24a24,24 0 1,0 48,0a24,24 0 1,0 -48,0" android:fillColor="${ICON_COLORS.bg}" />\n` +
        glyphGroup(icon, LEGACY_SCALE, 48),
    ),
  }
}

/** 自适应图标的底：深空实色（各条 Shortcut 共用） */
function shortcutBgFile() {
  return {
    'drawable/ic_shortcut_bg.xml':
      '<?xml version="1.0" encoding="utf-8"?>\n' +
      '<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">\n' +
      `  <solid android:color="${ICON_COLORS.bg}" />\n` +
      '</shape>\n',
  }
}

module.exports = { ICON_COLORS, readIcon, glyphPaths, shortcutIconFiles, shortcutBgFile }
