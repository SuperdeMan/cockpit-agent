// 原生配置唯一真相源（CNG：android/ 不入库，prebuild 由本文件 + config plugins 生成）。
// 三档 scheme（实施计划 M0-2）：dev 允许 cleartext + 任意服务器入口，prod 两者皆禁，
// staging 居中（禁 cleartext、允许自定义服务器，供 tailnet 外实验）。
// 包名三档共用 com.xiaozhou.companion——M3-3 高德 key 绑「包名+签名」，变体不改包名。
import type { ConfigContext, ExpoConfig } from 'expo/config'

declare const process: { env: Record<string, string | undefined> }

type Variant = 'dev' | 'staging' | 'prod'

const VARIANT: Variant = (['dev', 'staging', 'prod'] as const).includes(
  process.env.APP_VARIANT as Variant,
)
  ? (process.env.APP_VARIANT as Variant)
  : 'dev'

const NAME_SUFFIX: Record<Variant, string> = { dev: ' (Dev)', staging: ' (Staging)', prod: '' }

export default ({ config }: ConfigContext): ExpoConfig => ({
  ...config,
  name: `小舟随行${NAME_SUFFIX[VARIANT]}`,
  slug: 'xiaozhou-companion',
  version: '0.1.0',
  // 平板横竖屏都要（M1-6 双形态外壳按窗口尺寸类即时切），不锁竖屏
  orientation: 'default',
  icon: './assets/images/icon.png',
  scheme: 'xiaozhou',
  userInterfaceStyle: 'automatic',
  android: {
    package: 'com.xiaozhou.companion',
    adaptiveIcon: {
      backgroundColor: '#E6F4FE',
      foregroundImage: './assets/images/android-icon-foreground.png',
      backgroundImage: './assets/images/android-icon-background.png',
      monochromeImage: './assets/images/android-icon-monochrome.png',
    },
    predictiveBackGestureEnabled: false,
  },
  web: {
    output: 'static',
    favicon: './assets/images/favicon.png',
  },
  plugins: [
    'expo-router',
    [
      'expo-splash-screen',
      {
        backgroundColor: '#208AEF',
        image: './assets/images/splash-icon.png',
        imageWidth: 76,
      },
    ],
    'expo-secure-store',
    // Windows subst 构建的盘符根统一（非 win32 原样通过），坑账见插件头注
    './plugins/with-unified-drive-root',
    [
      'expo-build-properties',
      {
        android: {
          // dev 档允许 http/ws 明文（LAN 本地栈调试）；staging/prod 只 wss/https
          usesCleartextTraffic: VARIANT === 'dev',
        },
      },
    ],
  ],
  experiments: {
    typedRoutes: true,
    reactCompiler: true,
  },
  extra: {
    variant: VARIANT,
    // 「任意服务器入口」开关：onboarding 只在非 prod 档展示 lan/custom 预设
    allowCustomServer: VARIANT !== 'prod',
  },
})
