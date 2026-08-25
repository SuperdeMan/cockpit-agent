// 统一盘符根 config plugin（仅 Windows；实施计划坑账 §9 追加项，2026-08-25 实测）。
//
// 背景：本仓库路径含中文 ⇒ 原生构建必须经 subst 盘符（X:）跑；但同一次构建里
// 两类工具对 subst 的态度不同——
//   - app/build.gradle react{} 块用 `node --print require.resolve(...)` 求路径：
//     require.resolve 走 JS 版 realpath，**不解析 subst**，产出 X: 路径；
//   - RN CLI config 给各库工程 projectDir 用 fs.realpathSync.native，**解析 subst**，
//     产出 D:\...\产品\... 真实路径。
// 两套盘符根进 Path.relativize 直接炸「this and base files have different roots」
// （codegen 任务）。修法：把 react{} 的 Folders 三路径统一钉到真实路径（native realpath）。
// X: 与 D: 指向同一份文件，功能等价；-Dfile.encoding=UTF-8（build_mobile.ps1 patch）
// 保证 JVM 正确处理中文路径。
//
// 做成 config plugin 而不是构建脚本事后改文件：prebuild 检测到受管文件被外部修改会
// 整目录 Clear 重生成（EBUSY + 丢增量），CNG 的正道是让 prebuild 自己产出这段内容。
const { withAppBuildGradle } = require('expo/config-plugins')
const fs = require('fs')
const path = require('path')

const MARKER = '// with-unified-drive-root'

module.exports = function withUnifiedDriveRoot(config) {
  return withAppBuildGradle(config, (c) => {
    if (process.platform !== 'win32') return c
    if (c.modResults.contents.includes(MARKER)) return c
    const mobileReal = fs.realpathSync
      .native(path.resolve(__dirname, '..'))
      .replace(/\\/g, '/')
    c.modResults.contents += `

${MARKER}
react {
    root = file('${mobileReal}')
    reactNativeDir = file('${mobileReal}/node_modules/react-native')
    codegenDir = file('${mobileReal}/node_modules/@react-native/codegen')
}
`
    return c
  })
}
