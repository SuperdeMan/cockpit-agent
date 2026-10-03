// 静态 App Shortcuts（方案 §9「App Shortcuts」，B5-8）：长按图标 →「说话」「车况」。
// 静态 shortcuts = res/xml/shortcuts.xml + <meta-data android:name="android.app.shortcuts">，都住在
// prebuild 重生成的 android/ 里 ⇒ 必须做成 config plugin（同 with-amap-key 的判据）。
// 不引 expo-quick-actions：两条静态入口不需要运行时 API，少一个 autolinking 缝候选（B3 foldstate 同一判据）。
// 标签必须是 @string 资源引用（aapt 拒字面量），所以同时写 strings.xml。
// 「说话」落 xiaozhou://voice：只回对话页并升层、不开麦（§12.2）；「车况」落既有的 /vehicle 路由。
// 图标（v3 P7，Figma 07 系统面）：每条一枚自己的字形（话筒 / 车），不再两条都挂 App 图标；
// 字形从共享图标库生成 VectorDrawable，见 shortcut-icons.js。
const fs = require('fs')
const path = require('path')
const { AndroidConfig, withAndroidManifest, withDangerousMod, withStringsXml } = require('expo/config-plugins')

const { shortcutBgFile, shortcutIconFiles } = require('./shortcut-icons')

const SHORTCUTS = [
  { id: 'voice', short: '说话', long: '和小舟说话', data: 'xiaozhou://voice', icon: 'voice-input' },
  { id: 'vehicle', short: '车况', long: '查看车况', data: 'xiaozhou://vehicle', icon: 'vehicle' },
]

function shortcutsXml(pkg) {
  const items = SHORTCUTS.map(
    (s) =>
      `  <shortcut android:shortcutId="${s.id}" android:enabled="true" android:icon="@drawable/ic_shortcut_${s.id}"\n` +
      `    android:shortcutShortLabel="@string/shortcut_${s.id}_short" android:shortcutLongLabel="@string/shortcut_${s.id}_long">\n` +
      `    <intent android:action="android.intent.action.VIEW" android:data="${s.data}"\n` +
      `      android:targetPackage="${pkg}" android:targetClass="${pkg}.MainActivity" />\n` +
      `  </shortcut>`,
  )
  return (
    '<?xml version="1.0" encoding="utf-8"?>\n' +
    '<shortcuts xmlns:android="http://schemas.android.com/apk/res/android">\n' +
    items.join('\n') +
    '\n</shortcuts>\n'
  )
}

module.exports = function withShortcuts(config) {
  config = withStringsXml(config, (c) => {
    const strings = SHORTCUTS.flatMap((s) => [
      { $: { name: `shortcut_${s.id}_short` }, _: s.short },
      { $: { name: `shortcut_${s.id}_long` }, _: s.long },
    ])
    c.modResults = AndroidConfig.Strings.setStringItem(strings, c.modResults)
    return c
  })
  config = withDangerousMod(config, [
    'android',
    async (c) => {
      // 包名缺席就停：写成 targetPackage="undefined" 的话 shortcut 会静默失效（点了没反应），
      // 那种失败比构建失败难查得多
      const pkg = c.android && c.android.package
      if (!pkg) throw new Error('with-shortcuts: config.android.package 缺席，写不出 targetPackage')
      const dir = path.join(c.modRequest.platformProjectRoot, 'app', 'src', 'main', 'res', 'xml')
      fs.mkdirSync(dir, { recursive: true })
      fs.writeFileSync(path.join(dir, 'shortcuts.xml'), shortcutsXml(pkg), 'utf8')
      // 图标资源：字形读 ../hmi/src/components（构建镜像第 1b 步与 mobile 并排镜像了 hmi/src）
      const res = path.join(c.modRequest.platformProjectRoot, 'app', 'src', 'main', 'res')
      const components = path.join(c.modRequest.projectRoot, '..', 'hmi', 'src', 'components')
      const files = Object.assign(shortcutBgFile(), ...SHORTCUTS.map((s) => shortcutIconFiles(components, s.id, s.icon)))
      for (const [rel, xml] of Object.entries(files)) {
        const file = path.join(res, rel)
        fs.mkdirSync(path.dirname(file), { recursive: true })
        fs.writeFileSync(file, xml, 'utf8')
      }
      return c
    },
  ])
  return withAndroidManifest(config, (c) => {
    const app = c.modResults.manifest.application?.[0]
    const main = (app?.activity || []).find((a) => a.$?.['android:name'] === '.MainActivity')
    if (!main) throw new Error('with-shortcuts: AndroidManifest 里找不到 .MainActivity')
    main['meta-data'] = (main['meta-data'] || []).filter((m) => m?.$?.['android:name'] !== 'android.app.shortcuts')
    main['meta-data'].push({ $: { 'android:name': 'android.app.shortcuts', 'android:resource': '@xml/shortcuts' } })
    return c
  })
}
