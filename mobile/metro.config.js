// Metro 共享接线（实施计划 M0-3）：mobile 直引 hmi/src 白名单纯逻辑模块（@shared/*），
// 不复制不搬家——白名单台账 shared-allowlist.json，守卫测试 test/sharedAllowlist.test.ts。
// @shared/* 的别名解析由 Expo 默认启用的 tsconfig paths 完成，这里只负责三件事：
//  1. watchFolders 加仓库根（越出 projectRoot 的文件才可被打包）
//  2. sourceExts 补 mjs（hmi 纯逻辑层的文件形态）
//  3. 依赖只从 mobile/node_modules 解析，防重复 React（hmi/dashboard 各有自己的 node_modules）
const { getDefaultConfig } = require('expo/metro-config')
const path = require('path')

const projectRoot = __dirname
const repoRoot = path.resolve(projectRoot, '..')

const config = getDefaultConfig(projectRoot)

config.watchFolders = [repoRoot]
config.resolver.sourceExts = [...config.resolver.sourceExts, 'mjs']
config.resolver.nodeModulesPaths = [path.join(projectRoot, 'node_modules')]
config.resolver.disableHierarchicalLookup = true

module.exports = config
