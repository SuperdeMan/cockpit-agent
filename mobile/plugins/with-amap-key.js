// 高德地图 Android key 注入 config plugin（实施计划 M3-3）。
//
// 高德 Android SDK 只认 AndroidManifest 里 `<application>` 下的这条 meta-data，
// 没有运行时设置 key 的 API。而 CNG 形态下 `mobile/android/` 不入库、每次 prebuild
// 重生成 ⇒ 手改 manifest 一定会被冲掉，必须做成 plugin（同 with-unified-drive-root
// 那条判据：prebuild 检测到受管文件被外部改过会整目录 Clear 重生成）。
//
// **key 从环境变量来，不写死在仓库里**（`mobile/.env.local`，已 gitignore）。
// 高德 Android key 绑「包名 + 签名 SHA1」，泄露出去在别的 App 里也用不了，但
// 「密钥不进 commit」是本仓库的红线，按红线走不按危害程度走。
//
// **缺 key 时本插件根本不会被挂上**（见 app.config.ts）——manifest 里不出现这条
// meta-data，地图页入口也不出现（`extra.mapEnabled=false`）。M3-3 的「可降级」
// 就是这么实现的：不是运行时 try/catch，是这个能力压根没被装进去。
const { withAndroidManifest } = require('expo/config-plugins')

const META_NAME = 'com.amap.api.v2.apikey'

module.exports = function withAmapKey(config, { apiKey } = {}) {
  if (!apiKey) return config // 双保险：即便被误挂，没 key 也不写空值进 manifest
  return withAndroidManifest(config, (c) => {
    const app = c.modResults.manifest.application?.[0]
    if (!app) throw new Error('with-amap-key: AndroidManifest 里没有 <application>')
    app['meta-data'] = (app['meta-data'] || []).filter(
      (m) => m?.$?.['android:name'] !== META_NAME,
    )
    app['meta-data'].push({ $: { 'android:name': META_NAME, 'android:value': apiKey } })
    return c
  })
}
