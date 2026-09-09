// mobile ESLint 固定口径（AR06 / A06-1）。
//
// 为什么要有这个文件：`npm run lint` 走的是 `expo lint`，而 **expo lint 在没有配置时会
// 自己生成一份**——那意味着「本地跑一次」和「CI 跑一次」可能落在不同的规则集上，
// 数字不可比。这里把配置钉进仓库，让 `eslint .` 与 `expo lint` 读同一份，
// 且 clean checkout 直接可复现（AR06 §3.1）。
//
// 基座取仓库锁定版本 eslint-config-expo@57.0.2 的 flat 导出（eslint 9.39.x）。
// 本文件只做三件事：① 定 ignores；② 按**运行环境**补 globals（RN 运行时 / Node 脚本 /
// Jest 测试 三档，配置本身不改规则严厉度）；③ 少量按文件域的规则适配，每条写清理由。
//
// 门槛：`npx eslint . --max-warnings 0`（package.json 的 `lint:strict`）。
// 不做全仓格式化，不引入 prettier —— 那是另一件事，且会把 diff 淹掉。

const expoFlat = require('eslint-config-expo/flat')
const globals = require('globals')

module.exports = [
  {
    // 生成物、依赖、二进制资产与取证目录。
    // ⚠ 不许为了压低数字把 test/ 或 modules/ 这类**真源码**排除掉（AR06 §3.2）。
    ignores: [
      'node_modules/**',
      // CNG 生成的原生工程（.gitignore 里就是 /android）；里面的 JS 是构建中间物
      'android/**',
      'ios/**',
      '.expo/**',
      'dist/**',
      'web-build/**',
      // 真机取证截图/产物，非源码
      'e2e/artifacts/**',
      'assets/models/**',
      'coverage/**',
      // 本地 JVM 崩溃日志等构建残留
      '*.log',
    ],
  },

  ...expoFlat,

  {
    // ① RN 运行时（App 源码、原生模块 JS 侧、共享层消费面）
    files: ['src/**/*.{ts,tsx}', 'modules/**/*.{ts,tsx}'],
    languageOptions: {
      globals: {
        ...globals.browser,
        __DEV__: 'readonly',
      },
    },
  },

  {
    // ② Node 脚本档：Expo 配置、Metro/Jest/RN 配置、config plugins。
    // 这些文件在 **Node 里被 require**，不是被 Metro 打包进 App 的。
    files: [
      'app.config.ts',
      'jest.config.js',
      'metro.config.js',
      'react-native.config.js',
      'eslint.config.js',
      'plugins/**/*.js',
    ],
    languageOptions: {
      globals: {
        ...globals.node,
      },
      sourceType: 'commonjs',
    },
    rules: {
      // 这一档就是 CJS：require 是唯一的加载方式，不是「和 Metro 不一致」。
      '@typescript-eslint/no-require-imports': 'off',
    },
  },

  {
    // app.config.ts 是 TS 但由 Node 侧 ts 加载器读，仍用 ESM 语法
    files: ['app.config.ts'],
    languageOptions: {
      sourceType: 'module',
    },
  },

  {
    // ③ Jest 测试档（testMatch 收敛在 test/，见 jest.config.js）
    files: ['test/**/*.{ts,tsx}'],
    languageOptions: {
      globals: {
        ...globals.node,
        ...globals.jest,
      },
    },
    rules: {
      // 下面四条**不是**「把红降成绿」，是这些规则的立论前提在 test/ 这一档不成立：
      //
      // · import/first —— babel-jest 把 `jest.mock(...)` 提升到所有 import 之上，
      //   源码里的先后顺序对运行时**没有语义**。强制 import 置顶只会把「这个 mock 为什么
      //   存在」的注释和它守的那次 mock 拆散，换不来任何一次真实缺陷的拦截。
      // · @typescript-eslint/no-require-imports —— 该规则的立论写在 eslint-config-expo
      //   源码注释里：「align with Metro behavior」。test/ 不过 Metro（jest.config.js 的
      //   transform 直接走 babel），而 `jest.mock(m, () => require(x))` 是官方 mock 工厂
      //   的唯一写法（工厂在 require 时才执行，import 会被提升到工厂之前）。
      // · react/display-name —— 用例里的探针组件（`() => null`）是断言装置，不进产品树。
      // · react-hooks/globals —— `function Observe() { runtime = useHook() }` 是
      //   react-test-renderer 下观测 Hook 返回值的标准形态；产品代码里这条仍是 error。
      'import/first': 'off',
      '@typescript-eslint/no-require-imports': 'off',
      'react/display-name': 'off',
      'react-hooks/globals': 'off',
    },
  },
]
