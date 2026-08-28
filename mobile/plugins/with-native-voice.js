// M4 端侧语音的两处 gradle 补丁（实施计划 M4-1/M4-2）。expo-build-properties 两条都不覆盖，
// 而手改 android/ 在 CNG 下每次 prebuild 会被冲掉 ⇒ 只能做成 plugin（同 with-amap-key 的判据）。
//
// ① **abiFilters 只留两个 arm**。理由不是省体积（虽然也省了很多）：
//    sherpa-onnx 的 static-link AAR 在 **x86 这一个 ABI 下仍然带 `libonnxruntime.so`**
//    （arm64-v8a / armeabi-v7a 都不带，2026-08-28 解包实证），而 onnxruntime-react-native
//    也带一份同名的。两份同名 .so 进同一个 APK 是 gradle 的 duplicate 冲突，
//    靠 `pickFirst` 压下去等于在 x86 上随机赌一个 ORT 版本给两个消费方用。
//    ⇒ 真机（arm64）与 CI 产物都不需要 x86，直接不打包，冲突从源头消失。
//    ⚠ 代价明说：**x86/x86_64 模拟器装不上这个 APK**。本项目的验收本来就全在真机做
//    （实施计划 §0.4「每阶段出口必须真机」），但换人接手时别以为是构建坏了。
//
// ② **noCompress 'onnx'**。KWS 模型走 AssetManager 直读；不加这条 aapt 会压缩它们，
//    sherpa 每次 load 都要先解压 12MB 到内存。加了之后是 mmap，冷启动明显快。
//    （不加也能跑——这一条是性能不是正确性，别把它读成必需项。）
const { withAppBuildGradle } = require('expo/config-plugins')
const { mergeContents } = require('@expo/config-plugins/build/utils/generateCode')

const ABI_TAG = 'xiaozhou-abi-filters'
const NOCOMPRESS_TAG = 'xiaozhou-nocompress-onnx'

module.exports = function withNativeVoice(config) {
  return withAppBuildGradle(config, (c) => {
    if (c.modResults.language !== 'groovy') {
      throw new Error('with-native-voice: 只支持 groovy 的 app/build.gradle')
    }
    // defaultConfig 里加 ndk { abiFilters ... }
    c.modResults.contents = mergeContents({
      src: c.modResults.contents,
      newSrc: '        ndk { abiFilters "arm64-v8a", "armeabi-v7a" }',
      tag: ABI_TAG,
      anchor: /^\s*defaultConfig\s*\{/m,
      offset: 1,
      comment: '        // M4：见 plugins/with-native-voice.js 头注①',
    }).contents
    // android { } 里加 androidResources { noCompress ... }
    c.modResults.contents = mergeContents({
      src: c.modResults.contents,
      newSrc: '    androidResources { noCompress += ["onnx"] }',
      tag: NOCOMPRESS_TAG,
      anchor: /^android\s*\{/m,
      offset: 1,
      comment: '    // M4：见 plugins/with-native-voice.js 头注②',
    }).contents
    return c
  })
}
