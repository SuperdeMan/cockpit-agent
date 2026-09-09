# mobile/ — 小舟随行（Android 陪伴端 App）

React Native + **Expo SDK 57**（TypeScript strict，CNG：`android/` 不入库，
`app.config.ts` + config plugins 是原生配置唯一真相源）。与座舱 HMI **共存**的第二个
用户端、同一个后端大脑：同 user_id 共享记忆/画像，各自独立会话，经同一 WS/HTTP 契约接入。

- 需求/选型/架构判断：[`docs/design/2026-08-23-hmi-android-app-plan.md`](../docs/design/2026-08-23-hmi-android-app-plan.md)
- **逐任务执行真相源**（协议契约指认 + 坑账）：[`docs/design/2026-08-24-mobile-app-implementation-plan.md`](../docs/design/2026-08-24-mobile-app-implementation-plan.md)
- **完整评审**（2026-09-07；R01–R15 原始发现）：[2026-09-07-android-ux-full-review.md](../docs/reviews/2026-09-07-android-ux-full-review.md)
- **后续分批处理入口**（AR01–AR11，按页内状态选择批次）：[2026-09-07-android-review-remediation-batches.md](../docs/design/2026-09-07-android-review-remediation-batches.md)
- **AR05 进行中**：[2026-09-09-ar05-structured-contracts-implementation-plan.md](../docs/design/2026-09-09-ar05-structured-contracts-implementation-plan.md)。覆盖结构化确认/补槽、拒绝与降级恢复、真实身份和能力摘要。**步骤 0–5 已实施**：端侧 T0 授权闸（缺 `vehicle.control` 的 token 再也执行不了本地车控）、Registry 声明逐字段往返、四份契约端云贯通、F09 规划技术失败窄修复、`GET /api/session` 会话与能力摘要、Android 消费面（真实风险档与截止时刻、显式补槽回复、结构化问题与恢复出口、服务端身份与能力摘要、按能力筛的首页推荐、播报无声按成因分档）。网关 `go build`/`vet`/`go test ./gateway/...` 已跑绿。**步骤 6 已完成**：生产发布 `d425b9c`（5/5 healthy、verify verified）、真栈契约证据（`/api/session` 200 与 401 两档、confirm_policy / slot_request / held / closed 全部真实产出、零车控动作零挂起残留）、OPPO 固定包 `d425b9c2d`（哈希两端一致、非 DEBUGGABLE、设置页底行 `v0.1.0 · prod · d425b9c2d · 2026-09-09 17:09`）与设置页取证。**未签收**——逐条见方案 §9.4 与 §9.5 的 V01–V12 逐格表（真栈闭合 3 格 / 部分 6 格 / 仅离线 5 格）。三个阻塞项：① V05 负例需要一个**无 `vehicle.control` 的受限 token**（改 `.env` 属红线，要单独授权）；② 对话页确认卡/补槽卡的真机渲染缺中文输入注入通道（Maestro CLI 未装、设备 IME 的 `INPUT_TEXT` broadcast 无效）；③ 旧服务端兼容与 Redis 重启后挂起恢复的真栈反例。另：设置页文案修复在 APK 之后，本次验包不含它。
- **构建与协作操作指南**（Claude Code / Codex 共用）：[Android 构建、取证与跨工具交接](../docs/guides/android-build-and-device-validation.md)
- **AR04 实现与设备基线**：[实施记录第十五节](../docs/design/2026-09-08-ar04-presentation-ack-implementation.md)（支持页形态修正）+ 第十四节（发布与真锁屏）。生产仍 `573ad46`；Android 代码 `1c67807`——2026-09-09 用户判定设置 / 车辆 / 地图页底部常驻两栏破坏页面设计，已改为浮动在场：闲置只剩右下角光球（静帧），有事才长出状态胶囊 / 停播·打断键 / 采集点，承诺面与提醒出口只在有内容时占布局空间；地图信息条按路由上报高度、光球浮在其上。OPPO 首轮包 `de2a556` 取到闲置 / 思考 / 播报 / 一步停播证据，`1c67807` 复验读数见第十五节；临时偏好均恢复、App 已退出。第十四节的发布、真实 Keyguard ACK 与标题修复证据不变。**服务端多 operationId 实机组合及 Planner 技术失败降级策略仍未闭合，未整批签收。**
- AR01 确认与取消：客户端修复、本地回归与 OPPO 样本验证完成，历史证据见[AR01 实施记录](../docs/design/2026-09-07-ar01-confirmation-cancellation-implementation.md)。
- **AR02 历史证据**：[采集与隐私实施记录](../docs/design/2026-09-07-ar02-capture-privacy-implementation.md)。代码已推送；OPPO 当批验证包 `70365389e`（2026-09-07 20:44）。客户端修复与定向验证完成，旧缓存已于 2026-09-08 授权清除；完整设备矩阵尚未完成，AR02 未整批签收。
- **AR03 历史证据与剩余项**：[停播与横屏操作实施记录](../docs/design/2026-09-08-ar03-stop-playback-landscape-implementation.md)。播放事实（`core/voice/playbackFacts.ts`，两轴 `playing`/`live`）是停止键的唯一判据；停播回 ARMED 不开续问窗；横屏 Dock 落在语音层覆盖域（`voice-sheet-scope`）之外。**当批两台真机验证包均为 `b5c471832`（2026-09-08 08:10）**，R06 主证据与层内停止键在 OPPO、R09 横屏结构与功能证据在 Xiaomi（`driving-landscape` 只有对照机外屏够得到）；多段 / S2S / 主动消息 / 系统 200% 字号 / 盲听未验。
- 多端网关契约：`docs/conventions.md` §9.33
- ⚠ Expo 迭代快，写代码前查**版本对应**文档：<https://docs.expo.dev/versions/v57.0.0/>
  （SDK 版本一轮交付内锁定，不升级）

## 前置（一次性，详见实施计划 §1）

E1–E6 环境（JDK 17 / Android SDK 命令行工具链 / 环境变量 / Node ≥20 / 真机 USB 调试 /
设备端 Tailscale）。**每次开工先跑**：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1   # 退出码 0 才动手
```

## 验证设备（两台真机，角色固定；2026-09-07 泓舟定）

| 角色 | 机器 | 系统 | 用途 |
|---|---|---|---|
| **test 测试机** | OPPO PEUM00 | ColorOS 14 / Android 14 | 日常验证默认落点：adb 驱动的探针、Maestro / e2e、装新包、改设备状态的取证（`device_state` / `battery set` / 设置开关）都在这台 |
| **compare 对照机** | Xiaomi MIX Fold 4（24072PX77C） | HyperOS 3 / Android 16 | 泓舟主用手机。只做对比验证（同一包、同一语料在第二台上再跑一遍）与折叠形态覆盖；**不跑改设备状态的探针、不装 dev-client、不 force-stop 他正在用的会话** |

- 角色由 `scripts\mobile_device.ps1` 按厂商解析（`-List` 看在线设备；`-Role test` 打印序列号供
  `adb -s`）；序列号是机器状态，文档里不维护第二份。两台同时插着时 **adb 命令一律带 `-s`**。
- 对照验收时两台装同一份 prod release **常驻包**（见下文）；开发候选先落 test，本轮 OPPO 候选身份看 AR04 实施记录，compare 本批未操作。dev-client 只在测试机上、只在需要 Metro 热重载的时段临时装。
- 一台机器上的读数不代表另一台（B3′ 助理角色、Xruns、AEC 通路都是单机读数）：结论要标机型；
  「Xiaomi 复验」是对照，不是第二个验收分母。
- Tailscale 连接、折叠屏截图、UIAutomator 与 PowerShell 取证排查统一看[操作指南 §6](../docs/guides/android-build-and-device-validation.md#6-设备取证的几个实测边界)。
- tailnet 节点名不进仓库（同实施计划 §1 E4 卫生约定）。

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

## 原生构建

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1                          # debug dev-client APK（JS 靠 Metro）
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Release -Variant prod   # 常驻包（内嵌 bundle，见下节）
```

脚本使用 ASCII 镜像工作区，最终 APK 落在 `D:\Android\builds\apk\`，并打印装机命令。
**不同 worktree 仍共享构建目录，原生构建必须串行**。低内存参数、JVM 1455、缓存边界、后台构建与验包步骤统一看[操作指南](../docs/guides/android-build-and-device-validation.md)。

构建变体：`-Variant dev|staging|prod`（缺省 dev；脚本据此设 `APP_VARIANT`）。dev 允许
cleartext + 任意服务器入口；prod 两者皆禁（只留云栈 FQDN 预设）。包名三档同为
`com.xiaozhou.companion`。

## 常驻包（脱离 USB / Metro，随时可用；2026-09-07 起两台真机的常态）

debug dev-client **不带 JS bundle**：没有 Metro（= 没有 USB `adb reverse` 或同网 Metro）
它就是一个打不开的壳。要「随时随地用、发现更多问题」，装的必须是 **prod 变体的 release 包**：
bundle 内嵌（Hermes）、禁 cleartext、只留云栈 FQDN 预设、`__DEV__=false`（dev-only 日志与
dev launcher 都不在）。

| 项 | 值 |
|---|---|
| 构建 | `scripts\build_mobile.ps1 -Release -Variant prod` |
| 落点 | `D:\Android\builds\apk\xiaozhou-companion-prod-release-<sha>-<时刻>.apk`（文件名即身份，不靠目录名记） |
| 装机 | `scripts\mobile_device.ps1 -Role test -Install <apk>`（对照机 `-Role compare`）；脚本回读 `lastUpdateTime` 必须变、release 不得 `DEBUGGABLE` |
| 无 adb 装机权限时 | `adb -s <序列号> push <apk> /sdcard/Download/`，手机文件管理器里点装（同签名 ⇒ 原地升级） |
| 签名 | 与 debug 同一把模板 `debug.keystore`（指纹见「地图」节）——**刻意不换**：高德 key 绑指纹；同签名才能 `install -r` 原地升级、保住 AsyncStorage / SecureStore 里的服务器配置与 token |
| 版本 | `versionCode` 固定 1；同版本覆盖装合法（只有降级要 `-d`）；谁新谁旧看设置页底部的构建行 |
| 哪份包 | 设置页最底一行 `v0.1.0 · prod · <sha> · <时刻>`（Metro 开发态显示 `Metro`）——**报问题先抄它**，它是「设备跑的是哪份代码」的唯一读数 |
| 依赖 | 手机 Tailscale 登录同一 tailnet 且连着；已存的服务器配置沿用（全新安装要走引导页填 FQDN + token） |

三条边界：

- **同一包名两种包不能共存**。要热重载时把 dev-client `install -r` 盖上去、收工再把常驻包盖回来
  （同签名，配置不丢）；只在测试机上这么做，对照机永远是常驻包。
- **release 上 `console.log` 全部剥掉**（`__DEV__` 分支被 tree-shake）：取证走设置页 → 在场轨迹 /
  采集状态（只读）。主链操作调试与 voice-spike 仅明确 dev 变体开放，prod/staging 深链不能调用；
  dev 的链接也只预填，采集/播放/切播放器必须按键发起。
- 正式签名（M5）落地那天：高德控制台补新 SHA1、两台手机**卸载重装**（签名变了 `install -r`
  会拒、本地配置随之清空）、本 README 指纹更新。

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

## 端侧语音模型与原生件（M4，**不入 git，构建前必须先取件**）

免唤醒要两样原生东西：`onnxruntime-react-native`（跑 silero VAD）与 sherpa-onnx
（跑 KWS 唤醒词）。前者是 npm 依赖，后者的原生件与三个 KWS 模型**不进仓库**（体积 + 许可，
同 `hmi/public/models` 的处置）：

```powershell
powershell -File scripts\fetch-voice-models.ps1              # 先拿 hmi 那份（KWS 模型 + silero VAD）
powershell -File scripts\fetch_mobile_voice_assets.ps1       # 再拆成 mobile 要的形态
```

缺任何一件，`:kws` 模块会在构建期**明确失败**并指向取件脚本——刻意不静默跳过，
一个「悄悄没有唤醒词」的 APK 比一次红灯危险得多。CI 的 APK job 也跑这两步（`.sh` 版）。

⚠ 三条只有踩过才知道的（详见实施计划坑账 §9.43–47）：
- `onnxruntime-react-native` 需要 **`mobile/react-native.config.js` 显式补登**，
  否则它掉进 Expo/RN 两套 autolinking 之间的缝：**构建成功、装上去原生却没注册**。
  取证看 `android/app/build/generated/autolinking/.../PackageList.java` 有没有
  `OnnxruntimePackage`，别看 gradle 日志。
- ABI 由 **`reactNativeArchitectures`**（编不编）+ `abiFilters`（打不打包）**两个杠杆**共同决定，
  只配后者无效（`abiFilters` 是 Set，RN 插件随后会把四个 ABI 加回来取并集）。
  `build_mobile.ps1` 已自动写前者：**x86 模拟器因此装不上也编不出**，验收本来就全在真机。
- 改了 `metro.config.js` 必须**重启 Metro**，否则真机报「文件不存在」而那个文件明明在。

## 平台回声消除（AEC）——一个 node_modules 补丁（2026-08-29）

`react-native-audio-api` 建 Oboe 输入流时不设 inputPreset，落 Oboe 默认
`VoiceRecognition`——**那个源按定义就是「尽量少加工」，不施加 AEC**，于是免唤醒回路会把
自己的 TTS 播报收进麦、当成用户的下一句，形成正反馈环（真机实证）。

修法是一行 `setInputPreset(oboe::InputPreset::VoiceCommunication)`，落在
**`mobile/patches/react-native-audio-api+0.13.3.patch`**，由 `postinstall` 的
`patch-package` 自动应用 —— `npm ci` / 新克隆 / CI 都会自动打上，**不需要手动做什么**。

⚠ 两条：
- **要改 node_modules 一律走 `patch-package`**，不要直接改（它是镜像产物，
  `robocopy /MIR` 每次同步都会覆盖，且换台机器就没了）。
- 生成补丁时**必须 `--include` 限定到真正改的那个文件**：
  `npx patch-package <pkg> --include "<文件名正则>"`。不限定的话它会把包目录里**所有**
  与发布版的差异都收进去——本项目第一次生成得到 12.5KB / 30 个文件，绝大多数是
  `downloadPrebuiltBinaries` 下下来的 `.a` 静态库（见上文「预先下载」那条）与 `.DS_Store`。
  **「包目录里的东西」不等于「这个包的源码」。**

⚠ **改了这个预设，唤醒率与 VAD 端点的旧读数一律作废**：`VoiceCommunication` 连带上
NS/AGC，改变了送进 VAD/KWS 的音频，而唤醒阈值（0.2/2.0）是在旧路径上定的。
换预设之后**必须重新量**（2026-08-29 已重量一次：仍能唤醒）。

## e2e（Maestro，M3-5 立、UX v2 扩到 9 条）

9 条 flow，tag 三档：`offline`（04 离线冒烟、09 状态画廊；零后端依赖，CI 的 `mobile-apk.yml` 跑这档）/
`online`（01 天气、02 气泡内确认、03 断网补达、06 承诺面确认、08 键盘不遮发送；需真栈）/
`manual`（05 语音层 PTT、07 平板双栏；要人手或特定形态）。

```bash
maestro test --no-reinstall-driver --include-tags offline mobile/e2e/   # 零后端依赖
maestro test --no-reinstall-driver --include-tags online  mobile/e2e/   # 需 target=cloud + 真机在 tailnet
```

⚠ 02（气泡内确认，v1 路径）与 06（承诺面）的前提 `uxV2Dock` 互斥，一趟跑不可能都绿——回归清单必须带前提。

⚠ **`--no-reinstall-driver` 不是可选项**：Maestro 每个 session 都会重装它自己的 driver APK，
而 MIUI 每次都弹安装确认（只给 5 秒、默认「拒绝」）。前置、判据取舍、以及实跑当场抓到的
三个坑，**全部只写在** [`e2e/README.md`](e2e/README.md) —— 这里不复制第二份。

## 与 hmi/ 的共享面（单一真相源，不复制不搬家）

`@shared/*` = `hmi/src/*`，**只许引白名单模块**：台账 [`shared-allowlist.json`](shared-allowlist.json)
（含 phase 分阶段准入），守卫测试 `test/sharedAllowlist.test.ts`。共享模块要改
（真发现 bug）→ 在 hmi 侧改 + 跑 `hmi` node:test + 本守卫，两边都绿才算完。
HMI 的 UI 与应用装配不随 mobile 批次重构；共享模块修复遵循上述双端验证流程。

## 目录

```
app.config.ts          原生配置真相源（名称/包名/变体/插件/高德 key 注入/构建身份注入）
shared-allowlist.json  共享模块台账（机器守；currentPhase 当前 M4）
react-native.config.js RN 社区 autolinking 的显式补登（M4：onnxruntime-react-native）
patches/               patch-package 补丁——改 node_modules 的唯一通道（react-native-audio-api 输入预设
                       VoiceCommunication = 平台 AEC；expo-camera 视觉单帧内存路径，AR02）；生成时 --include 限定到真正改的文件
plugins/               config plugins：with-native-voice（abiFilters/noCompress，必须排在 expo-build-properties 之后）
                       / with-amap-key（有 key 才挂）/ with-shortcuts（长按图标「说话」「车况」）
                       / with-unified-drive-root（Windows subst 构建的盘符根统一）
modules/kws/           Expo 本地原生模块：sherpa-onnx KeywordSpotter 的极窄桥（M4-2）
                       android/libs + android/src/main/jniLibs + assets/kws 均 gitignore
modules/foldstate/     Expo 本地原生模块：折叠姿态事实**只透传**（B3）；tabletop/book 的派生在 src/ui/layout/foldPosture.ts
src/app/               expo-router 屏：index=对话主屏 / settings / vehicle / onboarding / map / voice（深链落点：只升层不开麦）
                       dev 取证屏（不进主导航，深链接进）：debug（下行 8 型帧落屏）/ card-gallery（?only=<type> 直达某族）
                       / state-gallery（在场态画廊）/ presence-trail（在场轨迹 + 采集激活日志）/ capture-status（只读采集事实）
                       / voice-spike（语音探针，仅 dev 变体可操作）/ native-spike（折叠姿态 + 四种触感）/ blur-spike（材质）
src/core/config/       服务器配置：FQDN 校验派生（dev_stack_lib 同构）+ SecureStore/AsyncStorage
src/core/api/          gateway.ts（共享 ws.mjs 的会话客户端）+ connectionTest.ts
                       + liveness.ts（前台探活：RN 的 WS 在飞行模式下 onclose 不来，
                       send 会把帧写进死 socket——判据取「HTTP 探不通」不取「应用层静默」）
src/core/session/      M1 会话状态机：store.ts（8 型帧分发+看门狗+确认台账）/ sendRouter.ts
                       （候选拦截+位置闸）/ candidates.ts / wiring.ts（跨路由单例）
                       UX v2 追加：turnView（「当前这一轮」判定）/ actionSummary（承诺卡与到期留痕的动作摘要）
                       / receipt（执行回执，字段全部来自已有数据）/ followUps（follow-up chip = 合成一句话走普通 send）
src/core/presence/     在场模型（UX v2.2）：presence.ts 纯函数 derivePresence()（六轴事实 + 唯一视觉主态）
                       / commitment（Focus Dock 承诺项）/ drivingMode（行车档判据：Edge 标注 + 手动，不用车速再算一份）
                       / orbPolicy（动效三档唯一判据）/ hapticCue、soundCue（触感与提示音的转移判据）
                       / presenceTrail、activityLog（20 条内存环形日志，不上传不持久化）
src/core/settings/     设置仓库（AsyncStorage 持久化；buildMeta 与 HMI settings.tsx 键集一致）
src/core/location/     定位桥（expo-location 取坐标；meta 键共享纯函数拼、source='app'）
src/core/obs/          trace_id（HMI 同构）+ 会话前缀 app-
src/core/voice/        M2 语音面：recorder（16k 归一）/ resample / asr（流式+模型回退+批处理兜底）
                       / tts（流式+收尾三分支）/ audioCtx（pcmPlayer 注入适配）/ queuePlayer（单个队列节点顺序吃片）
                       / speech（SpeechSink 实现）/ audioFocus（+ M4 有界事件日志）/ cueTone / catalog / wav / base64
                       M4 追加：micBus（一路麦多路消费，免唤醒的地基）/ vad（ORT+silero，
                       端点判据共用 @shared/sileroEndpoint.mjs）/ kws（sherpa 原生桥的 JS 面）
                       / handsFree（voiceLoop.mjs FSM 接 RN 引擎 + S2S）/ tapTalk（轻点即说，说完自动收尾）
                       事实与命令（AR02/AR03）：captureFacts（采集事实：读设备与上行出口）/ playbackFacts（播放事实：
                       读真实播放器起止）/ stopPlayback（「只停播」合成出口，顺序即判据）/ proactivePolicy（主动播报仲裁）
src/core/vision/       M4-6 视觉单帧：触发判据共用 @shared/visionFrame.mjs::needsFrame
                       （采集面即隐私面，判据只许一份）；采集端在 features/vision/
src/core/cards/        卡片判据：cardGroup（display_priority 取主卡）/ cardFields（兜底卡与行车压缩卡的字段探取）
src/core/map/          地图能力判据：MAP_AVAILABLE（有 key ∧ 原生在场）+ 坐标校验（0,0 判空）
src/core/stage/        舞台场景选择（最近一张助手卡决定右舞台放什么；与 HMI deriveScene 同一张表，测试逐字对账）
src/core/power/        低电量材质回落判据 + 事实收集；src/core/a11y/ 「减少动效」事实源
src/features/chat/     对话 UI：ChatScreen（外壳）/ MessageBubble / Composer / VoiceSheet（语音层）/ FocusDock（承诺面）
                       / PresenceCapsule（状态胶囊）/ PrivacyRail（隐私栏）/ ExecutionReceipt / FollowUpChips
                       / usePresence / useHandsFree / usePtt
src/features/cards/    CardRenderer（全量卡型从 types.ts 派生、双向守卫 + 兜底卡铁则 + ErrorBoundary + _prov 徽章）；
                       CardGroup（主卡全展 + 「还有 N 张」）/ DrivingCardSummary（行车压缩卡）/ infoCards / navCards
                       / miscCards / merchantCards（商户支付族，复用 @shared/merchantUi.mjs）/ parts / fixtures（画廊语料）
src/features/stage/    StagePane（平板/横屏右舞台，会话已有事实的第二视图）/ StageDrawer（medium 宽度的抽屉舞台）
src/features/settings/ 设置页 + S2sConsentSheet（S2S 挡位隐私同意）；src/features/vehicle/ 车况面板
                       （三格指标复用 vehicleStage.mjs）+ ReminderSection（复用 reminderStage.mjs）
src/features/vision/   VisionCapture（命中才挂 CameraView、拍完立刻卸载；内存上传零落盘，AR02）
src/ui/                主题（深浅/跟随系统 + 字号两档）/ tokens / aurora（AuroraOrb 光球、AuroraBackground、EdgeGlow、
                       Glass、StreamCursor、ThinkDots）/ layout（sizeClass 尺寸类、foldPosture 折叠姿态、sheetHeight 语音层高度）
types/                 第三方类型补丁：RN 内部 URL 实现 / react-native-amap3d（见文件头注）
test/                  jest（jest-expo）：守卫 + 契约单测 + 变异反向验证；计数以 `npm test` 本次输出为准
e2e/                   Maestro flow：9 条（tag offline / online / manual），前提与坑账只在 e2e/README.md
```
