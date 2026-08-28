// Metro 共享接线（实施计划 M0-3）：mobile 直引 hmi/src 白名单纯逻辑模块（@shared/*），
// 不复制不搬家——白名单台账 shared-allowlist.json，守卫测试 test/sharedAllowlist.test.ts。
// @shared/* 的别名解析由 Expo 默认启用的 tsconfig paths 完成，这里只负责三件事：
//  1. watchFolders 加 hmi/（越出 projectRoot 的文件才可被打包；**刻意不 watch 仓库根**
//     ——M1-8 真机轮实测：根下别的进程建删临时目录会把 watcher 直接 ENOENT 崩掉，
//     且 watch 面过大疑似丢事件的成因。@shared 只来自 hmi/，够了）
//  2. sourceExts 补 mjs（hmi 纯逻辑层的文件形态）
//  3. 依赖只从 mobile/node_modules 解析，防重复 React（hmi/dashboard 各有自己的 node_modules）
const { getDefaultConfig } = require('expo/metro-config')
const path = require('path')

const projectRoot = __dirname
const hmiRoot = path.resolve(projectRoot, '..', 'hmi')

const config = getDefaultConfig(projectRoot)

config.watchFolders = [hmiRoot]
config.resolver.sourceExts = [...config.resolver.sourceExts, 'mjs']
// 5. M4：.onnx 当**资产**而不是源码（默认 assetExts 里没有它，metro 会当模块去解析然后报错）。
//    VAD 模型经 expo-asset 拿 localUri 再交给 onnxruntime-react-native；
//    KWS 的三个模型不走这里——它们是 Android library assets，由原生侧 AssetManager 读。
config.resolver.assetExts = [...config.resolver.assetExts, 'onnx']
config.resolver.nodeModulesPaths = [path.join(projectRoot, 'node_modules')]
config.resolver.disableHierarchicalLookup = true

// 4. 嵌套依赖的窄口子（M2-1 实测）：react-native-audio-api 运行时要 semver@^7，
//    而顶层 node_modules/semver 被 @babel/core 的 6.3.1 占着，npm 把 7 装进了
//    audio-api 自己的 node_modules——正好是上面第 3 条关掉的那种层级查找。
//    症状是 bundle 直接失败：Unable to resolve "semver/functions/gte"。
//    只对 semver 这一个包恢复「从发起方向上找」的语义（node 的 require.resolve 就是
//    这个语义），不改全局解析，重复 React 的防线原样保留。
const defaultResolve = config.resolver.resolveRequest
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (moduleName === 'semver' || moduleName.startsWith('semver/')) {
    try {
      return {
        type: 'sourceFile',
        filePath: require.resolve(moduleName, {
          paths: [path.dirname(context.originModulePath)],
        }),
      }
    } catch {
      // 找不到就落回默认解析，让 metro 报它自己的错（别把错误信息也吃掉）
    }
  }
  return (defaultResolve ?? context.resolveRequest)(context, moduleName, platform)
}

module.exports = config
