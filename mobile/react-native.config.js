// RN 社区 autolinking 的显式补登（M4-1，2026-08-28 实测逼出来的）。
//
// **问题**：`onnxruntime-react-native` 掉进了两套 autolinking 之间的缝——
//  · 它带一个 `unimodule.json`（Expo 旧式 unimodules 的标记），于是 **expo-modules-autolinking
//    把它认成自己人**，并从交给 RN 社区 autolinking 的那份清单里排除掉；
//  · 但它没有 `expo-module.config.json`，Expo 的 `ExpoModulesPackage` 又注册不了它。
// ⇒ 两边都没注册。症状极具误导性：gradle **构建完全成功**（`:onnxruntime-react-native:
//    assembleDebug` 跑过、.so 也进了 APK），JS 侧却是
//    `TypeError: Cannot read property 'install' of null`——因为
//    `NativeModules.OnnxruntimeJSIHelper` 是 null。
//    取证判据是 `android/app/build/generated/autolinking/src/main/java/com/facebook/react/
//    PackageList.java`：**那里面没有 `OnnxruntimePackage` 就是没注册**，别看 gradle 日志。
//
// **修法**：项目级 `react-native.config.js` 显式补登。放在仓库里而不是改 node_modules——
// node_modules 是镜像产物，改了每次同步都要重做（同 gradle_cn_mirrors.init.gradle 的判据）。
//
// ⚠ 这个文件里的三个字段是**与库内部结构的契约**，升级 onnxruntime-react-native 时要核：
//    包名 `ai.onnxruntime.reactnative`、类名 `OnnxruntimePackage`（android/src/main/java/
//    ai/onnxruntime/reactnative/OnnxruntimePackage.java）。写错了症状与现在一模一样。
module.exports = {
  dependencies: {
    'onnxruntime-react-native': {
      platforms: {
        android: {
          // ⚠ 相对包根，不是绝对路径：解析器做的是 `path.join(packageRoot, sourceDir)`，
          // 给绝对路径会拼出垃圾路径 → 找不到 gradle → 静默 return null，症状与
          // 「压根没配」一模一样（我第一版就是这么写的，2026-08-28）。
          sourceDir: 'android',
          packageImportPath: 'import ai.onnxruntime.reactnative.OnnxruntimePackage;',
          packageInstance: 'new OnnxruntimePackage()',
        },
      },
    },
  },
}
