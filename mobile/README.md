# mobile/ — 小舟随行（Android 陪伴端 App）

React Native + **Expo SDK 57**（TypeScript strict，CNG：`android/` 不入库，
`app.config.ts` + config plugins 是原生配置唯一真相源）。与座舱 HMI **共存**的第二个
用户端、同一个后端大脑：同 user_id 共享记忆/画像，各自独立会话，经同一 WS/HTTP 契约接入。

- 需求/选型/架构判断：[`docs/design/2026-08-23-hmi-android-app-plan.md`](../docs/design/2026-08-23-hmi-android-app-plan.md)
- **逐任务执行真相源**（协议契约指认 + 坑账）：[`docs/design/2026-08-24-mobile-app-implementation-plan.md`](../docs/design/2026-08-24-mobile-app-implementation-plan.md)
- 多端网关契约：`docs/conventions.md` §9.33
- ⚠ Expo 迭代快，写代码前查**版本对应**文档：<https://docs.expo.dev/versions/v57.0.0/>
  （SDK 版本一轮交付内锁定，不升级）

## 前置（一次性，详见实施计划 §1）

E1–E6 环境（JDK 17 / Android SDK 命令行工具链 / 环境变量 / Node ≥20 / 真机 USB 调试 /
设备端 Tailscale）。**每次开工先跑**：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1   # 退出码 0 才动手
```

## 日常开发（JS/Metro，可在原路径跑）

```bash
cd mobile
npm install          # 首次
npx expo start       # Metro dev server；真机 dev-client 扫码连
npm run typecheck    # tsc --noEmit（含 @shared 引用）
npm test             # jest：白名单守卫 + 端点/gateway 契约 + 会话状态机 + 发送路由 + 设置 meta + 卡片注册表
```

首次真机调试需要安装 **dev-client APK**（见下方构建），之后 JS 热更即可；
**改了 app.config.ts / config plugins / 新增原生依赖必须重 prebuild + 重装 APK**
（「改了不生效」十有八九是这个）。

## 原生构建（在 ASCII 镜像工作区进行——仓库路径含中文，subst/中文单根两形态均实测不可用，实施计划 §1.1 偏差 ④ + 坑账 §9.11-12）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1            # debug APK
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Release   # release（M5 前 debug keystore）
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Clean     # 重生成 android/
```

脚本内置：robocopy 增量镜像 `mobile/` → `D:\Android\builds\xiaozhou-mobile`（全 ASCII
单根）→ 镜像里 `expo prebuild` → gradle wrapper 换腾讯镜像 + jvmargs 强制 UTF-8 →
缺失 SDK 包用 android CLI 预装 → CN maven 镜像 init script → `gradlew assembleDebug`
→ 验 APK 产物。装机：`adb install -r <APK 路径>`（脚本末尾打印，路径在镜像区）。

构建变体：`APP_VARIANT=dev|staging|prod`（缺省 dev）。dev 允许 cleartext + 任意服务器
入口；prod 两者皆禁（只留云栈 FQDN 预设）。包名三档同为 `com.xiaozhou.companion`。

## 连接后端

App 连**云栈**（不需要本地 Docker）：引导页选「云栈」填 Tailnet FQDN
（根 `.env` 的 `TAILNET_FQDN`），派生 `:8443`（主链）/`:8444`（音频）；token 填
`AUTH_TOKENS` 条目的 token 段——M0 冒烟可用根 `.env` 现有 `VITE_WS_TOKEN` 值（只读），
App 专属条目（手机档不含 `vehicle.control`）由泓舟在 M1 期间加。
**token 不进代码、不进 commit、不进日志**；App 内存 Android Keystore（expo-secure-store）。

设备前置：真机装 Tailscale 官方 App 并登录同一 tailnet（连不上先查它，再查代码）。

## 地图（M3-3，可降级）

高德 Android SDK。**key 不进 git**——放本机 `mobile/.env.local`（已 gitignore，Expo CLI
自动加载；Metro 启动日志会打印 `env: load .env.local`）：

```dotenv
AMAP_ANDROID_KEY=<你的高德 Android key>
```

链路：`app.config.ts` 读它 → `plugins/with-amap-key.js` 写进 AndroidManifest 的
`com.amap.api.v2.apikey` → `extra.mapEnabled/amapKey` 透传给 JS。
**缺 key 时插件根本不挂**：manifest 里没有这条 meta-data、`mapEnabled=false`、
卡片上的「地图」入口不出现——「可降级」是这个意思，不是点进去报错。

⚠ **高德 key 绑「包名 + 签名 SHA1」**，换签名（debug→release、换机器、换 keystore）
必须在高德控制台补一条，否则地图只是灰屏、`logcat` 里报
`Key验证失败：[INVALID_USER_SCODE]`。当前 debug 签名指纹（从 APK 本体
`apksigner verify --print-certs` 读实，**不要从 keystore 推断**——本机有两把
debug.keystore）：

```
包名   com.xiaozhou.companion
SHA-1  5E:8F:16:06:2E:A3:CD:2C:4A:0D:54:78:76:BA:A6:F3:8C:AB:F6:25
```

地图入口只出现在**契约里真的带 `lat`/`lng`** 的卡上（`poi_detail` / `place_list` /
`place_detail`）；`route_plan` / `poi_list` / `charging_route` 没有坐标，折线等后端补。

## 与 hmi/ 的共享面（单一真相源，不复制不搬家）

`@shared/*` = `hmi/src/*`，**只许引白名单模块**：台账 [`shared-allowlist.json`](shared-allowlist.json)
（含 phase 分阶段准入），守卫测试 `test/sharedAllowlist.test.ts`。共享模块要改
（真发现 bug）→ 在 hmi 侧改 + 跑 `hmi` node:test + 本守卫，两边都绿才算完。
`hmi/` 本身一行不改。

## 目录

```
app.config.ts          原生配置真相源（名称/包名/变体/插件/高德 key 注入）
shared-allowlist.json  共享模块台账（机器守；currentPhase 当前 M3）
src/app/               expo-router 屏：index=对话主屏 / settings / vehicle / onboarding / map
                       / debug / voice-spike（M2 语音取证屏）/ card-gallery（M3 卡片画廊，
                       支持 ?only=<type> 直达某一族；后三个不进主导航，深链接进）
src/core/config/       服务器配置：FQDN 校验派生（dev_stack_lib 同构）+ SecureStore/AsyncStorage
src/core/api/          gateway.ts（共享 ws.mjs 的会话客户端）+ connectionTest.ts
src/core/session/      M1 会话状态机：store.ts（8 型帧分发+看门狗+确认台账）/ sendRouter.ts
                       （候选拦截+位置闸）/ candidates.ts / wiring.ts（跨路由单例）
src/core/settings/     设置仓库（AsyncStorage 持久化；buildMeta 与 HMI settings.tsx 键集一致）
src/core/location/     定位桥（expo-location 取坐标；meta 键共享纯函数拼、source='app'）
src/core/obs/          trace_id（HMI 同构）+ 会话前缀 app-
src/core/voice/        M2 语音面：recorder（16k 归一）/ resample / asr（流式+模型回退+批处理兜底）
                       / tts（流式+收尾三分支）/ audioCtx（pcmPlayer 注入适配）/ speech
                       （SpeechSink 实现）/ audioFocus / catalog / wav / base64
src/features/chat/     对话 UI：ChatScreen（双形态外壳）/ MessageBubble / Composer
src/core/map/          地图能力判据：MAP_AVAILABLE（有 key ∧ 原生在场）+ 坐标校验（0,0 判空）
src/features/cards/    CardRenderer（**全量 34 型** + 兜底卡铁则 + ErrorBoundary + _prov 徽章）；
                       infoCards / navCards / miscCards / merchantCards（商户支付族，复用
                       @shared/merchantUi.mjs）/ parts（含 relativeTime）/ fixtures（画廊语料）
src/features/settings/ 设置页；src/features/vehicle/ 车况面板（三格指标复用 vehicleStage.mjs）
                       + ReminderSection（平板右面板提醒段，复用 reminderStage.mjs）
types/                 第三方类型补丁：RN 内部 URL 实现 / react-native-amap3d（见文件头注）
src/ui/                主题（深浅/跟随系统 + 字号两档）
test/                  jest（jest-expo）：守卫 + 契约单测（188 条）
```
