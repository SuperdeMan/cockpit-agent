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

// 高德 Android key（M3-3）：来自 `mobile/.env.local`（gitignore；Expo CLI 自动加载 .env*，
// Metro 启动日志会打印 `env: load .env.local` / `env: export AMAP_ANDROID_KEY`）。
// **缺 key 时插件根本不挂**——manifest 里不写这条 meta-data、`extra.mapEnabled=false`、
// 卡片上的「地图」入口不出现。M3-3 要的「可降级」是这个意思：不是运行时 try/catch，
// 是这个能力压根没被装进 APK。⚠ key 绑「包名 + 签名 SHA1」，换签名要在高德控制台另加一条。
const AMAP_KEY = (process.env.AMAP_ANDROID_KEY || '').trim()

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
    // M4 端侧语音（VAD/KWS/视觉）。三条各自的理由：
    //  · onnxruntime-react-native：VAD 引擎，跑 hmi 那份 silero_vad.onnx。插件本身只往
    //    app/build.gradle 加一行 `implementation project(':onnxruntime-react-native')`。
    //  · expo-camera：M4 视觉抓帧。两个选项都是关的：`recordAudioAndroid` 关掉是因为
    //    RECORD_AUDIO 已由 react-native-audio-api 声明（同一条权限声明两次没意义，
    //    而「谁声明的」影响后来人找它）；`barcodeScannerEnabled` 关掉省体积——本 App
    //    不扫码。⚠ 插件的 `cameraPermission` 是 **iOS 专用**（写 Info.plist），
    //    Android 的权限用途文案只能落在 App 自己的 UI 上（设置页 + 首次申请引导），
    //    见 §5.5「权限用途文案齐」。别以为在这里写了字就合规了。
    //  · ./plugins/with-native-voice：abiFilters + noCompress，理由见该文件头注。
    //    ⚠ 它必须在 expo-build-properties **之后**——两者都改 app/build.gradle，
    //    而 mergeContents 靠锚点定位，锚点被前一个插件挪走过就找不到了。
    'onnxruntime-react-native',
    ['expo-camera', { recordAudioAndroid: false, barcodeScannerEnabled: false }],
    './plugins/with-native-voice',
    // 有 key 才挂（见上方 AMAP_KEY 注释）。`...(cond ? [x] : [])` 而不是塞个 false 进去：
    // Expo 的 plugins 数组不接受假值项，会直接报配置错。
    ...(AMAP_KEY ? [['./plugins/with-amap-key', { apiKey: AMAP_KEY }] as [string, unknown]] : []),
  ],
  experiments: {
    typedRoutes: true,
    reactCompiler: true,
  },
  extra: {
    variant: VARIANT,
    // 「任意服务器入口」开关：onboarding 只在非 prod 档展示 lan/custom 预设
    allowCustomServer: VARIANT !== 'prod',
    // 地图入口的**唯一运行时判据**（M3-3「可降级」）：为 false 时卡片不出「地图」按钮。
    mapEnabled: Boolean(AMAP_KEY),
    // ⚠ key 也要透传给 JS——这不是疏忽，是被库的 API 逼出来的（2026-08-27 实测）：
    // `react-native-amap3d` 的 `initSDK(apiKey)` 实现是 `apiKey?.let { ... }`
    // （SdkModule.kt:19-25），**传空则整块跳过**，连高德 9.x 必需的
    // updatePrivacyAgree/updatePrivacyShow 四个调用一起跳过 ⇒ 地图白屏且不报任何错。
    // 我最初刻意只透传布尔、想把 key 留在原生侧，那个顾虑站不住：**key 本来就写在
    // APK 的 AndroidManifest 里，解包即可读**，JS 侧多一份不增加任何暴露面；
    // 真正的防线是它绑「包名 + 签名 SHA1」，以及它不进 git（来自 .env.local）。
    amapKey: AMAP_KEY,
  },
})
