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
    // 语音面原生依赖（M2-1 定案：录音+播放同一库，见实施计划 §5 M2 实施记录）。
    // 三项刻意偏离插件默认值：
    //  · androidPermissions 只要 RECORD_AUDIO——默认那两条是给前台服务用的，我们不起服务
    //  · androidForegroundService=false：PoC 承诺前台交互档（坑账 §9.5 省电模式会杀后台
    //    socket，验收本就在前台做）；声明 service 却不给 FOREGROUND_SERVICE 权限会崩，
    //    两者必须一起关
    //  · iosBackgroundMode=false：本项目不构建 iOS
    [
      'react-native-audio-api',
      {
        androidPermissions: ['android.permission.RECORD_AUDIO'],
        androidForegroundService: false,
        iosBackgroundMode: false,
        // FFmpeg 关掉：本 App 的音频面全是裸 PCM（TTS 下行 s16le / ASR 上行 s16le），
        // 用不到 decodeAudioData 解 mp3/aac。收益是不下 11.9MB 的 jniLibs.zip
        // （GitHub releases 本网络 30KB/s，见坑账）+ 不打包 4 个 ABI 的 FFmpeg .so。
        // ⚠ 代价明说：AudioContext.decodeAudioData 对压缩格式不可用；哪天要放 mp3
        // 提示音（M4 cue 音）得把这条改回来并重构建。
        disableFFmpeg: true,
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
