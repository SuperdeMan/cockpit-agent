// jest 配置（实施计划 M0-4/M0-8）：jest-expo 预设 + 四处扩展——
//  1. @shared / @ 别名映射（jest 不走 Metro resolver，须自行映射，与 tsconfig paths 对齐）
//  2. .mjs 纳入 babel 转译（共享模块的文件形态；预设 babel 键是 `\.[jt]sx?$` 不含 mjs，
//     按「值是 babel 转换器」找到该条、给 .mjs 复用同一配置；扩展名解析 jest 默认已含 mjs）
//  3. @babel/runtime 显式映射——转译后的 hmi/src 文件从自己的目录向上找不到
//     mobile/node_modules（monorepo 直引的 jest 侧对应物，Metro 侧是 nodeModulesPaths）
//  4. testMatch 收敛到 test/（守卫 + 契约单测）
const preset = require('jest-expo/jest-preset')

const transform = { ...preset.transform }
const babelEntry = Object.entries(transform).find(([, v]) =>
  String(Array.isArray(v) ? v[0] : v).includes('babel'),
)
if (!babelEntry) throw new Error('jest-expo preset 里找不到 babel transform 条目（预设结构变了？）')
transform['^.+\\.mjs$'] = babelEntry[1]

module.exports = {
  ...preset,
  transform,
  testMatch: ['<rootDir>/test/**/*.test.ts'],
  moduleNameMapper: {
    ...(preset.moduleNameMapper ?? {}),
    '^@babel/runtime/(.*)$': '<rootDir>/node_modules/@babel/runtime/$1',
    '^@shared/(.*)$': '<rootDir>/../hmi/src/$1',
    '^@/(.*)$': '<rootDir>/src/$1',
  },
}
