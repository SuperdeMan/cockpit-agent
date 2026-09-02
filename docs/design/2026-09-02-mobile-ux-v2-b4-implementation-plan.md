# UX v2.1 · B4「形态与行车档」实施计划（逐任务，按方案 v2.2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 状态：**草案待批（2026-09-02）**——四批待开工；§0 第 3 条的两个待裁项先交泓舟
> 交付对象：`mobile/` 执行者（人或 Agent）
> 上游真相源：[`2026-08-29-mobile-ux-v2-presence-redesign.md`](2026-08-29-mobile-ux-v2-presence-redesign.md)（方案 **v2.2**；本计划只展开 **B4**，读 §6 全节（含 §6.0）/ §7.1–§7.5 / §8 + §8.1 / §5.11 / §11.1 B4 行 / §11.2 B4 / §11.3（流 07）/ §11.4 / §11.5 / §13 Q3·Q4·Q6·Q15·Q16·Q18；**不读 §2**）；
> B3 计划 [`2026-09-01-mobile-ux-v2-b3-implementation-plan.md`](2026-09-01-mobile-ux-v2-b3-implementation-plan.md)（只读 §0 / §6.1–§6.4：**遗留出账是本计划的输入**，Task 正文不读；blur 裁决读数在其 §6.3「T9 步骤 3」，姿态四格读数在「T9 步骤 2」）；
> B2 计划 [`2026-08-30-mobile-ux-v2-b2-implementation-plan.md`](2026-08-30-mobile-ux-v2-b2-implementation-plan.md)（§6.4 出账④⑨⑩⑫ 是 B4 的账）；
> 主计划 [`2026-08-24-mobile-app-implementation-plan.md`](2026-08-24-mobile-app-implementation-plan.md)（§9 坑账 **50–58** 是 B3 新添的，开工前读一遍；`POST /api/debug/vehicle` 的云栈用法在其 §8.3）；
> `AGENTS.md` §4.2「Android App B4」行（活项清单——本计划 §0 第 9 条逐条给了去向）
> 纪律：沿用 B1/B2/B3 计划 §0 + 主计划 §9 坑账；每任务「先测后码、一任务一提交」；**零重建、零新原生依赖、零后端改动；`hmi/` 不碰；共享判据（`hmi/src/*.mjs`、`pendingOps.mjs` TTL、`voiceLoop.mjs`）一字不动**。

**Goal:** 把方案 U4 落成生产形态：① **尺寸类 × 折叠姿态**替代 `ChatScreen.tsx:69` 那个 `min(w,h) >= 600` 布尔——compact / medium / expanded / large / extra-large 宽高各算，单栏 / 舞台抽屉 / 双栏（内容约束 720dp）/ tabletop 上下半 / book 铰链落 gap，姿态事实是 B3 交付的 `modules/foldstate` + `ui/layout/foldPosture`（真机已验四格）；② **行车档全套**：触发（Edge `process` 帧的 `driving` 标 + 手动 + 身份 C 的「建议」胶囊）、退出（Edge 标 false 起 30s）、布局（目标 ≥56dp、过程区单行、文本输入按身份 A 常驻 / B 折叠 / C 隐藏、chips ≤3、一屏一卡、语音层常驻、横屏 40:60）、动效降级（×0.5 频率 ×0.6 透明度）、播报收紧（行车档只强制 `critical` 主动告警，主动消息语音仲裁走共享 `decideSpeech`）；③ **无障碍与 reduce-motion**：光球所有循环降静帧、转写/回答 live region、partial 节流、卡片按钮 role；④ **提示音**用 `OscillatorNode + GainNode` 在 `pcmPlayer` 同一个 `AudioContext` 上合成（零资源、零重建）；⑤ **材质**：B3 裁决「过」⇒ 语音层外壳换真模糊（`<BlurTargetView>` + `blurTarget`，首帧 ref 为 null 会静默回落 none），同屏只许一个 BlurView，减少透明度 / 行车档回落 G1-tint；⑥ 把 `AGENTS.md` §4.2 里「归 B4」的每一条落到任务（发送按钮图标 / `charging_list` 兜底 / `EdgeGlow` jest / 壳底与浅色对比度 / `shutter`≡`wake` / Reanimated 告警定位 / B2 表#6 与「换一批」两格）。验收对齐方案 §11.2 B4 四条；计划末尾给 §11.4「状态可读性」一次**真 5 人外部小样本**留位（做不做交泓舟裁）。

**Architecture:** 判据全部是纯函数、住在一处、jest 钉住，组件只消费：尺寸类与布局 `ui/layout/sizeClass.ts`（`layoutMode()`——姿态只从 `foldPosture` 读）；行车档 `core/presence/drivingMode.ts`（`drivingActive()`：手动 ∨ Edge 标 true ∨ Edge 标 false 后 30s 内；**客户端不拿 `vehicle_state` 的 `speed_kmh/gear` 再算一份**——`server.py::_is_driving` 是唯一裁决点，§5.3.1 删掉的就是 UI 自己的车速规则）；动效策略 `core/presence/orbPolicy.ts`（B3-1 立的那份，扩成 `orbTempo()` / `edgeGlowActive()` / `loopsAnimated()`——reduce-motion、行车 ×0.5、G5「层开主球静态」三个利益方一处裁）；提示音 `core/presence/soundCue.ts`（转移判据，与 `hapticCue` 同形态；执行薄壳 `core/voice/cueTone.ts`，挂 `usePresence` 的 **useEffect**，不挂渲染期）；舞台场景 `core/stage/stageScene.ts`（映射表与 hmi `ContextualStage.tsx::MAP_TYPES` 由测试从 hmi 源码读出来逐字对账）；主动消息播报仲裁 `core/voice/proactivePolicy.ts`（共享 `decideSpeech` 之上只叠一条「行车档 critical 强制」）。事实登记只加 `SessionState` 并列字段（`drivingEdge`）与 `AppSettings` 键（`drivingManual` / `cueToneEnabled` / `reduceMotionForce` / `reduceTransparency`），`Msg` 共享类型不动。`derivePresence` 只加两个输入（`drivingSuggest`、`voice.answeredAt`）与两个派生（胶囊 `action`、行车档下语音层常驻与 3s 回落的 detent），**不新增状态机**。

**Tech Stack:** React Native 0.86 / Expo 57 / react-native-reanimated 4.5 / RNGH 2.32 / expo-blur 57（已在 APK，B3 T4）/ expo-haptics 57（已在 APK）/ react-native-audio-api 0.13.3（`createOscillator` / `createGain` / `AudioParam.setValueAtTime` 均在 `lib/typescript/core/*.d.ts` 里核过）/ `modules/foldstate`（B3 自写，已在 APK）/ jest-expo（`mobile/test/**/*.test.ts`，**不含 `.tsx`**）/ Maestro 2.9（dist `D:/Android/tools/maestro-dist/maestro/bin/maestro.bat`，带 `--no-reinstall-driver`）/ 云栈 debug 注入 `POST /api/debug/vehicle`（collector `:8446`，M3-2 验收在云栈用过）。

---

## 0. 接手须知（先读）

1. **开工前提**：`powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；Metro `cd mobile && npx expo start --dev-client`；`python scripts/dev_stack.py target show` = cloud（行车 `driving` 帧与 VAL 拒绝都走云栈）；设备上是主线包 **`lastUpdateTime=2026-09-02 16:45:36`**（B3′ 验完装回的那个，APK 与 `2026-09-01 19:21:55` 同源）——开工第一步 `adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime` 核一次并写进 §6。**全批 JS + Metro 热载，不重建**。
2. **三条硬边界，任务中撞上就停、出账、不做**：① **零重建**——`app.config.ts` / plugins / `android/` / `package.json` 依赖一行不动（`predictiveBackGestureEnabled: false` 也不动，见 §5 第 8 条）；② **零新原生依赖**——本计划用到的原生面（expo-blur / expo-haptics / react-native-audio-api 的 Oscillator / foldstate / AccessibilityInfo / BackHandler）**全在当前 APK 里**，凡是「顺手装一个 expo-battery / keyboard-controller」的念头都说明走偏了；③ **零后端改动**——行车 `driving` 帧与 VAL 拒绝走既有云栈 debug 注入（`POST https://<TAILNET_FQDN>:8446/api/debug/vehicle {"key":"speed_kmh","value":30}`，主机名从根 `.env` 的 `TAILNET_FQDN` 读、Windows 侧用 PowerShell `Invoke-RestMethod`，CLAUDE.md §6.1 那条「真栈一律 PowerShell」）；后端挂账（Q16 `confirm_policy` / Q17 / Q19）不在本批。**若某任务非要突破其中一条不可 ⇒ 停下来，把「为什么非要」写进 §6 交泓舟，不自行突破。**
3. ⛔ **两个待裁项，不进任务清单，在这里单列交泓舟裁**（B3 §6.3 遗留①②、`AGENTS.md` §4.2 ①②）：
   - **① 唤醒率 5–6/10 不达标**（判据 N≥8，未触 N<5 红线；引擎直灌对照已证「引擎能认」⇒ 问题在声学链路 麦→AEC/NS/AGC→KWS）。**调阈值（0.2/2.0）是独立批，要在同一个含 AEC 的包上做 A/B**，不拍脑袋、不混进 B4——B4 的语音读数若捎带唤醒率，只记数不调参。泓舟要裁的是：是否另开「KWS 阈值 A/B 批」、以及它排在 B4 前还是后。
   - **② 播报卡顿的对照构建**（本应用麦流 HAL 阻塞 71–104ms，播报时 2.2 次/分 vs 无播报 0.7 次/分；**不能归因给 AEC**——没有同条件的无 AEC 对照包）。要坐实需要**一趟不带 `96a6830` 的对照构建**（去掉 `patches/react-native-audio-api+0.13.3.patch`、同条件复测 Xruns、再装回主线包）——这违背 B4「零重建」，所以不在本计划里。泓舟要裁的是：单开一趟对照构建（B4′），还是并进 B5 那一趟重建再取。
4. **读数口径**：语音类读数若捎带，**必须带 `lastUpdateTime` 锚**（当前主线包 `2026-09-02 16:45:36`；B3 §0 第 5 条的口径原样沿用，没记锚的语音读数视为无效）。B4 的主读数不是语音——是**截图 / `png_probe` 颜色亮度 / `maestro hierarchy` 边界 / `framestats`**，每条读数写清取法与图名（`mobile/e2e/artifacts/b4-*.png`，gitignore）。
5. **真机取证纪律（合订，B1–B3 §0 原样）**：截图 `adb shell screencap -p -d <物理displayId> /sdcard/x.png` + `adb pull`（PowerShell 的 `>` 损坏 PNG）；**折叠屏先 `cmd device_state state` 再选 displayId**（外屏 `4630947090644569220` 1080×2520 / 内屏 `4630946481727302019` 2224×2488；映射从 `dumpsys display` 的 `mViewports` 读，坑账 §9.52；全黑先查 `dumpsys power` 的 `mWakefulness`——息屏锁屏与抓错屏是两件事）；**每条 adb 命令都带 `timeout`**；`adb reverse tcp:8081 tcp:8081` 在每次 adb server 生命周期变化后重建；颜色 / 亮度类读数一律经 `mobile/e2e/tools/png_probe.py`（先 `selftest`）；`uiautomator dump` 在对话主屏拿不到树（常驻动画），**边界读数用 `maestro hierarchy`**；深链在 bundle 加载完之前发会被吞（坑账 §9.58）；改完设备/App 设置一律回读（坑账 §9.55）；`adb logcat -b all -c` 才清 system 缓冲、长观测宿主侧流式捕获（§9.53）；dev-client 的 Tools 按钮取证前关掉；Maestro 用 `D:/Android/tools/maestro-dist/maestro/bin/maestro.bat`（不在 PATH ≠ 不在本机）+ `--no-reinstall-driver`，新流**别写 `hideKeyboard`**（它发的是 BACK，B3 坑⑤）。
6. **十二个结构性事实，写代码前记住**（每条都有 `文件:行号`，别按印象）：
   - **`driving` 今天只在 `process` 帧上**：`gateway/edge/main.go:409` 把 `p.Driving` 透传进 `type:'process'`，产出方是 `orchestrator/edge/server.py::_stamp_progress`（按 `_is_driving`：`speed_kmh > 0` 或挡位 D/R/S）；`store.ts:498` 存到**那条助手气泡**上（`Msg.driving`，共享类型 `hmi/src/types.ts:28` 本来就有，语义「行车极简、不可展开」）；`usePresence.ts` 读的是 `!!active?.driving`——**轮一结束就掉回 false**。所以 B4 的行车档事实要在 `SessionState` 里并列登记（`drivingEdge`，T2），不能靠在飞轮。**简单轮（无 `process` 帧）不带这个标**——取证要用会出过程区的复杂任务语料（T13 步骤 1）。
   - **`vehicle_state` 帧里也有 `speed_kmh`**（`store.ts:606` 的镜像，车辆页 `KEY_LABEL` 有「车速」）——**它只许显示，不许拿来判行车**（第二份判据）。
   - **`tablet` 布尔在 `ChatScreen.tsx:69`**（`Math.min(width, height) >= 600`），消费在 `:92`（传 ChatBody）、`:551`（隐藏「车辆」入口）、`:575-604`（右舞台 `Glass`，`width: min(400, 42%)`）。B4 用 `layoutMode()` 整个替掉；舞台三段（`VehicleSection` / `ReminderSection` / 焦点卡）抽成 `features/stage/StagePane.tsx`。
   - **键盘避让已是 `behavior="padding"`**（`ChatScreen.tsx:462`，B1 T12 读数「遮」→ 修 → Maestro 08 过）；`react-native-keyboard-controller` B3 §5 第 1 条**裁定不装**。B4 的 §7.6 **零任务**——只在分屏取证里复跑 08 作回归（T6 步骤 6）。
   - **`deviceRole` 是 B1 的占位**（`core/settings/store.ts` 末段：`'handheld' | 'mount' | 'trusted-tablet'`，默认 `handheld`，**设置页无 UI**、`usePresence.ts` 已把它喂进 `identity` 轴）。B4 给它 UI（T10）与行为差异（T11）。
   - **blur 的接法**（B3 §6.3 步骤 3 裁决原话）：SDK 57 真模糊要求被糊的背景被 `<BlurTargetView>` 包住、`BlurView` 的 `blurTarget` 指向它的 ref，**且 ref 要先挂上再渲 BlurView**——首帧 ref 为 null 会被当成「没配」静默回落成 `none`（`BlurView.js::_maybeWarnAboutBlurMethod`）；落地参数 `blurMethod="dimezisBlurView"` + `intensity={60}` + `tint`；`experimentalBlurMethod` 已 deprecated。参照实现 `src/app/blur-spike.tsx`（③ 号块 + `ready` 状态那几行）。
   - **`predictiveBackGestureEnabled: false`**（`app.config.ts:44`）——方案 §7.5 写的「RN 0.81 起默认开」在本 App 被显式关了，那是原生配置、动它要重建。B4 的返回手势只做 JS 侧 `BackHandler` 顺序（先收语音层 / 隐私栏，再退页面，T7），不碰这个开关。
   - **`charging_list` 不在 `hmi/src/types.ts` 的 `UiCard` 联合里**（全仓只有 `charging_route`；产出方 `agents/charging_planner/src/agent.py:171` 与 `low_battery.py:100`，字段 `items[{id,name,available,total,price,distance_km,operator}]` + `soc`）。`test/cards.test.ts` 从 types.ts **两个方向**派生注册表 ⇒ mobile **不能单独注册这个卡型**（注册即红），而 `hmi/` 不碰。B4 的落点是把兜底卡做成**能渲 `items[]` 列表的通用兜底**（T5），`charging_list` 进 types.ts + HMI 渲染器是 **hmi 侧另立**的账（写进 §6.4 出账）。
   - **主动消息在 mobile 端从不出声**：`store.ts:613-636` 的 `proactive` 分支只 `appendMessage` + `proactive_ack`，共享 `@shared/proactiveSpeech.mjs::decideSpeech`（网关 `main.go:543` 已透传 `priority`）在 mobile **零消费方**（只用了 `deliveryIdsOf`）。§6「安全告警强制播报」的落点就是接上它（T12），不发明第二套仲裁。
   - **`EdgeGlow` 的 `active` 表达式住在 `VoiceSheet.tsx:150` 的 JSX 里**（`snapshot.primary === 'listening' || snapshot.primary === 'thinking'`），零 jest（B2 出账⑩）。B4 提成 `orbPolicy.ts::edgeGlowActive()`（T3）。
   - **`sharedAudioContext()`**（`core/voice/audioCtx.ts:105`）是全局唯一输出上下文，懒建、建后 `resume()`——提示音必须走它（§8「与 pcmPlayer 同一 AudioContext」），别再建一个。
   - **胶囊 `onPress` 今天恒等于「打开语音层」**（`ChatScreen.tsx:518`），且 `PresenceCapsule` 挂着 `accessibilityLiveRegion="polite"`——recognizing 态它显示的是**逐 token 的 partial**（`presence.ts` capsule 分支），TalkBack 会不断打断自己（§8.1 节流那条）。B4 给胶囊加 `action` 字段（行车档建议）并把 partial 的 live region 挪到转写区（T9/T10）。
   - **jest 的 `testMatch` 不含 `.tsx`**，全仓无组件渲染测试 ⇒ 组件层改动的「红→绿」是真机读数；能提成纯函数的判据（本计划的 T1–T5 全部）都提出来给 jest。
7. **提交纪律**（B1–B3 §0 原样沿用）：新建文件 `git add -- <路径> && git commit -m '…' -- <路径>` **同一条命令链**，**`-m` 必须写在 `--` 之前**（`--` 之后的一切都是 pathspec）；提交后 `git show --stat HEAD` 复核行数；**不要 `git add -A`**；`AGENTS.md` 是所有会话都在写的文件——动它前 `git diff --stat -- AGENTS.md` 核行数、单独一个 commit；开工先 `git log --oneline origin/main..HEAD` 念一遍谁的提交在里面（B3 收口时 mobile 线有未推提交）。多行 commit message 走 `git commit -F -` + heredoc（反引号在 bash 单行 `-m` 里会被当命令替换执行——B1 坑⑦）。
8. **方案 §13 已裁决的默认值不在本计划重议**：Q3 双栏阈值 720（内容约束）；Q4 行车档不自动进入、三条件只**建议**；Q6 提示音默认开、行车强制开；Q15 设备角色用户在设置里选、C 身份的绑定另议；Q18 行车档只强制安全告警、不再「强制开」播报。本计划新增的实施判断集中在 §5。
9. **`AGENTS.md` §4.2「Android App B4」行与 B3 出账的逐条去向**（接手时逐条核，不复述）：

   | 来源 | 条目 | 去向 |
   |---|---|---|
   | AGENTS ① / B3 §6.3 遗留① | 唤醒率 5–6/10 不达标 | **待裁项，不进任务**（§0 第 3 条①） |
   | AGENTS ② / B3 §6.3 遗留② | 播报卡顿需无 AEC 对照构建 | **待裁项，不进任务**（§0 第 3 条②） |
   | AGENTS ③ / B3 §6.3 T9 步骤 3 | blur 裁决过 ⇒ B4 换真模糊，接法 `BlurTargetView` + `blurTarget` | **T8** |
   | AGENTS ④ / B3 §6.4 遗留① | B3′ 默认助理角色：第一问过、第二问不过；spike 分支永不合并 | **不在 B4**（B5「角色启用」的前置结论，§6.4 收口时原样转出） |
   | AGENTS ⑤ / B2 §6.4 闸 | 5 人外部小样本仍是「裁定算过、无分布读数」 | **T14**（有位置；做不做交裁） |
   | AGENTS ⑥ / B2 出账④ | 发送按钮改图标 | **T9** |
   | AGENTS ⑥ / B2 出账⑨ | `charging_list` 卡型未适配（落兜底卡、无 `_prov`） | **T5**（通用列表兜底）+ hmi 侧另立（§6.4 出账） |
   | AGENTS ⑥ / B2 出账⑩ | `EdgeGlow` 零 jest | **T3** |
   | AGENTS ⑥ / B2 出账⑫ | VoiceSheet 壳底与浅色光球对比度 | **T8**（blur 落地时一并裁） |
   | AGENTS ⑥ / B3 §6.3 遗留③ | `shutter` ≡ `wake` 触感不可区分 | **T13 步骤 10**（候选 `AndroidHaptics.Virtual_Key` + 盲测） |
   | AGENTS ⑥ / B3 §6.3 遗留⑤ | `Reanimated: synchronouslyUpdateUIProps failed` 一直在发生 | **T13 步骤 9**（定位到组件；修得掉就修，修不掉出账带组件名） |
   | AGENTS ⑥ / B3 §6.3 遗留⑥ | B2 验收表#6（气泡先于相机）与「换一批」chip 两格未取 | **T13 步骤 11**（非闸门） |
   | B3 §6.1 遗留④ | `ChatScreen.tsx:511` 注释「B1 不接 onPress」过期 | **T10 顺手**（改胶囊 onPress 时一起清） |
   | B3 §6.3 遗留④ | B3 计划两条判据的前提被推翻（VAD 探针回环 / 触感只有三个波形） | **不动 B3 计划**（B3 §6.3 已记录；本计划 §5 第 10 条按新前提写） |
   | B3 §6.4 遗留⑤ | 主线 APK 备份 `D:/Android/builds/_b3-mainline-apk-backup/`（不入库） | **B4 不需要，不删**（删文件是红线，交泓舟） |
   | 方案 §11.5 | 「v1 代码 B4 稳定后再删」 | **B4 不删**（§5 第 7 条：稳定的判据是 B4 收口 + 一轮真机使用，删是之后另立的一条提交） |

### 0.1 分批执行：一批一个会话（新会话从这里开始）

本计划分四批，每批一个新会话，每批以「jest 全绿 + `tsc` 0 + 逐任务已提交 + §6 实施记录回填」收口；下一批冷启动只读 §0 + §0.1 + §1 + 自己那几个 `### Task N` 块（`grep -n "^### Task" <本文件>` 取行号，`sed -n` 只读自己的段），**不读整份计划、不读方案全文**（方案只在任务里点名的 §号处查）。

| 批 | 会话任务 | 性质 | 并行度 | 收口判据 | 真机？ |
|---|---|---|---|---|---|
| **第 1 批「判据层」** | T1 尺寸类与布局 → T2 行车档事实与判据 → T3 动效策略（含 `EdgeGlow` jest）→ T4 提示音 → T5 舞台场景 + 通用兜底卡 | 五份纯函数 + 测试 + 最薄的接线（store 一个字段、settings 四个键、usePresence 两个 effect）；**不改任何布局** | T1 ∥ T3 ∥ T5 互不相干；T2 先于 T4（T4 的「行车强制开」读 T2 的 `snapshot.driving`）；都动 `settings/store.ts` 的串行提交 | `npm test` 全绿（413 → ≈463）、`tsc` 0、5 个 commit、每个纯函数反向验证各 ≥2 条、Metro 热载冒烟（App 起得来、胶囊/光球无回归）、§6.1 | 只冒烟（不需要泓舟） |
| **第 2 批「形态落地」** | T6 舞台抽屉 / 双栏 / 布局切换 + Maestro 07 → T7 折叠姿态消费（book / tabletop / 外内屏接续 / 返回顺序）→ T8 材质（语音层真模糊 + 减少透明度 + 浅色对比裁）→ T9 发送按钮图标 + 无障碍补项 | 组件层；判据全在第 1 批 | T6 先（T7 在它之上）；T8 ∥ T9 独立文件；`VoiceSheet.tsx` 三者都动——串行提交 | 全绿（≈463 → ≈465）、`tsc` 0、4 个 commit、形态矩阵**前三张**（外屏竖 / 外屏横 / 内屏 `device_state 3`）+ 分屏不崩不遮 + Maestro 06/07/08/09 rc=0、§6.2 | 是（Metro 热载；book / tabletop 两张**需泓舟手折**，可挪到第 3 批一并） |
| **第 3 批「行车档」** | T10 身份与行车档设置（含建议胶囊）→ T11 行车档布局全套 → T12 播报收紧（主动消息仲裁）→ T13 B4 真机验收（§11.2 B4 四条 + 五截图矩阵 + Reanimated 定位 + shutter 盲测 + B2 两格 + 无障碍读数） | 组件层 + 一次真机验收轮 | T10 → T11 → T12 串行（同动 `presence.ts` / `ChatScreen.tsx`）；T13 最后 | 全绿（≈465 → ≈476）、`tsc` 0、3 个 commit + 撞出的修复、§11.2 B4 四条各有读数或显式 ⬜（第 3 条的 Dock 半按 §5 第 2 条记 ⬜ 待 Q16）、§6.3 | 是（**手折与盲测需泓舟**；云栈 debug 注入单人可造） |
| **第 4 批「小样本 + 收口」** | T14 5 人外部小样本（泓舟批准才做）→ T15 记录收口 | 取数 + 文档 | 串行 | 小样本有分布读数或显式「未做，仍无外部基线」、README / 主计划 / AGENTS.md 指针、未推送清单报给泓舟、§6.4 | 是（截图材料来自真机） |

**每批开工的固定五步**（写进新会话的第一条提示词）：
1. `powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；`python scripts/dev_stack.py target show` = cloud；
2. `cd mobile && npm test && npm run typecheck` 取**开工基线**（条数与 0 error），写进 §6 该批记录的第一行——读数有效期只到下一次改动；再取 `adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime`（应为 `2026-09-02 16:45:36`，不是就先问为什么）；
3. 只读 §0 / §0.1 / §1 + 自己批次的 `### Task N` 块；
4. 按任务顺序：写失败测试 → 跑红 → 实现 → 跑绿 → `tsc` → `git add -- <新文件> && git commit -m '…' -- <只加自己的路径>` → `git show --stat HEAD` 复核；
5. 收口：全量 `npm test` + `tsc`，把读数、遗留、撞到的坑写进 §6，**然后停下**——下一批是另一个会话的事。

**worktree**：B1–B3 都在主工作树做（泓舟未授权分树）。**若泓舟同意分树**，第 1 批开工前执行一次并写进 §6：`git worktree add ../car-agent-ux-b4 -b ux-v2-b4`，四批都在该 worktree 里做，最后由泓舟决定合回方式。没有分树就在主工作树做——**这一条不是可选项措辞**：不分树就要在每次提交前 `git status` 重采、`git log origin/main..HEAD --oneline` 念一遍谁的提交在前面。⛔ **push 的粒度是分支不是提交**（M4-R1 推 main 带走别人 33 个提交的教训）：本计划任何一批都**不推送**，推前列完整 `origin/main..HEAD` 并单独取得泓舟授权。

**批与批之间的状态只靠两处传递**：git 提交（代码）与本文件 §6（读数与遗留）。新会话不要去翻上一批会话的对话——那些不在仓库里。

---

## 1. 文件结构（先定边界，再拆任务）

### 新建

| 文件 | 职责 | 依赖 | 任务 |
|---|---|---|---|
| `mobile/src/ui/layout/sizeClass.ts` | `widthClass / heightClass / layoutMode / stageWidth / bookSplit / tabletopSplit / screenSwitch`：尺寸类 × 姿态 × 行车 → 布局模式的**唯一判据**（替 `ChatScreen.tsx:69` 的布尔）；纯函数、零 RN import | `foldPosture` 类型 | T1 |
| `mobile/test/sizeClass.test.ts` | 上者的守卫：真机 dp（外屏 411×960 / 内屏 847×948 与 809 两种密度）逐格、720 内容约束、姿态强制、分屏落单栏 | jest | T1 |
| `mobile/src/ui/layout/useLayout.ts` | hook：`useWindowDimensions` + `useFoldState` + `driving` → `LayoutSpec`（mode / 舞台宽 / 铰链 dp / 切屏事件）；**零判断**，只搬运 | `sizeClass`、`useFoldState`、`PixelRatio` | T1 |
| `mobile/src/core/presence/drivingMode.ts` | `drivingActive / recordEdgeDriving / composerInputMode / drivingSuggested / sheetResident`：行车档判据（手动 ∨ Edge 标 ∨ 30s 退出宽限；身份 → 文本输入形态；建议三条件）；纯函数 | `presence.ts` 的 `Identity` | T2 |
| `mobile/test/drivingMode.test.ts` | 30s 宽限的正反例、连续 false 不刷新起点、身份三档、建议三条件缺一不出 | jest | T2 |
| `mobile/src/core/a11y/reduceMotion.ts` | `useReduceMotion()`：系统 `AccessibilityInfo.isReduceMotionEnabled` ∨ 实验室强制开关——**事实收集**，判据在 `orbPolicy` | RN `AccessibilityInfo`、settings | T3 |
| `mobile/test/orbPolicy.test.ts`（既有，扩） | `orbTempo` 三档 / reduce-motion 压过一切 / `edgeGlowActive` 正反 / `loopsAnimated` | jest | T3 |
| `mobile/src/core/presence/soundCue.ts` | `soundCueForTransition(prev, next)`：唤醒确认音只给 ARMED→LISTENING（PTT 按下不响，§4.2）、`attention` 进入一次；`cueToneAllowed(enabled, driving)`——纯函数 | `presence.ts` 类型 | T4 |
| `mobile/src/core/voice/cueTone.ts` | `playCueTone(kind)`：`OscillatorNode + GainNode` 在 `sharedAudioContext()` 上合成两音（C5→G5 各 60ms、gain 0.15）；fire-and-forget、静默失败 | `audioCtx.sharedAudioContext` | T4 |
| `mobile/test/soundCue.test.ts` | 四条转移正反例 + 行车强制开 | jest | T4 |
| `mobile/src/core/stage/stageScene.ts` | `stageScene(messages)`：最近一张卡 → 舞台场景（agenda / weather / map / focus / idle）；`STAGE_MAP_TYPES` 与 hmi `ContextualStage.tsx::MAP_TYPES` 逐字一致 | `@shared/types.ts` | T5 |
| `mobile/test/stageScene.test.ts` | 场景四种 + `card_group` 展平 + **从 hmi 源码读 `MAP_TYPES` 字面逐字对账**（同 `cards.test.ts` 手法） | jest、fs | T5 |
| `mobile/src/core/cards/cardFields.ts` | `cardPrimaryFields / cardListRows / cardPrimaryButton`：兜底卡与行车压缩卡共用的字段探取（`FALLBACK_PRIMARY_KEYS` 从 `CardRenderer.tsx` 搬来）；纯函数 | — | T5 |
| `mobile/test/cardFields.test.ts` | `charging_list` 真实形状（`items[{name,distance_km,available,total}]` + `soc`）能拿出 5 行 + soc；无 items 的卡拿不出行 | jest | T5 |
| `mobile/src/features/stage/StagePane.tsx` | 舞台面板（testID `stage-pane`）：车况三格 / 提醒 / 场景卡（读 `stageScene`），标题行 testID `stage-mode` 写当前模式（双栏 / 舞台抽屉 / 桌面）——Maestro 07 与形态截图的判据物 | `VehicleSection`、`ReminderSection`、`CardRenderer`、`stageScene` | T6 |
| `mobile/src/features/stage/StageDrawer.tsx` | width medium 的舞台抽屉：右缘 48dp 把手（testID `stage-handle`）→ 拉出 320dp 玻璃舞台，对话区随之压缩 | reanimated、`StagePane` | T6 |
| `mobile/e2e/07-tablet-two-pane.yaml` | 流 ⑦：内屏（`cmd device_state state 3` 由外部先设）→ 断言 `stage-pane` 与 `stage-mode`=「双栏」 | Maestro | T6 |
| `mobile/e2e/tools/target_probe.py` | 从 `maestro hierarchy` 的 JSON 读指定 testID 的 bounds，按 `wm density` 换算 dp，断言 ≥56——「目标 ≥56dp 读实」的非 Scanner 读法 | Python stdlib | T11 |
| `mobile/src/ui/icons.local.ts` | mobile 专有图标数据（`send` / `keyboard`）——**不进共享台账**（`icons.custom.ts` 是 hmi 的文件，不碰），`Icon.tsx` 的 REGISTRY 合并它 | — | T9 |
| `mobile/src/features/cards/DrivingCardSummary.tsx` | 行车压缩卡（§6：标题 + ≤2 字段 + 1 主按钮 + `_prov`），读 `cardFields`；`card_group` 取主卡 | `cardFields`、`splitCardGroup`、`parts` | T11 |
| `mobile/src/core/voice/proactivePolicy.ts` | `proactiveSpeechDecision(msg, ctx)`：共享 `decideSpeech` 之上只叠「行车档 `critical` 强制」（Q18）；纯函数 | `@shared/proactiveSpeech.mjs` | T12 |
| `mobile/test/proactivePolicy.test.ts` | 行车 critical 强制（静音档也说）/ 非行车照共享判据 / `auto` 档主动消息不出声 / S2S 忙时 INTERRUPT | jest | T12 |

### 修改

| 文件 | 改什么 | 为什么 | 任务 |
|---|---|---|---|
| `mobile/src/core/session/store.ts` | `SessionState.drivingEdge: DrivingEdgeFact`（初值 `{trueAt:0,falseAt:0}`）；`process` 帧分支登记 `recordEdgeDriving(...)`；`proactive` 分支调 `speech.proactive?.(text, {priority, hasCard})` | 行车事实不能靠在飞轮；主动消息仲裁的入口 | T2 T12 |
| `mobile/test/sessionStore.test.ts` | +3 条：`driving:true` 的 process 帧登记 `trueAt`；之后 `driving:false` 登记 `falseAt` 且再来 false 不刷新；轮结束后 `drivingEdge` 仍在 | 事实登记的守卫 | T2 |
| `mobile/src/core/settings/store.ts` | `drivingManual`（默认 false）/ `cueToneEnabled`（默认 true）/ `reduceMotionForce`（默认 false）/ `reduceTransparency`（默认 false）四键；`mergeStoredSettings` 走既有 `...DEFAULT` 展开零迁移 | §6 手动切行车 / §8 提示音默认开 / §8 减少动效强制（Android 系统开关取证用）/ §8.1 减少透明度 | T2 T3 T4 T8 |
| `mobile/test/settingsMeta.test.ts` | +4 条：旧库缺四键各补默认；`drivingManual:true` 存量保持（合并不许把用户的开覆盖回关） | B2 坑①：既有两条够不到合并体 | T2 T3 T4 T8 |
| `mobile/src/core/presence/orbPolicy.ts` | 扩：`orbTempo / edgeGlowActive / loopsAnimated`；`composerOrbAnimated` 加 `env` 参数 | 动效三个利益方一处裁；`EdgeGlow` 判据出 JSX | T3 |
| `mobile/src/ui/aurora/AuroraOrb.tsx` | `driving?: boolean`：四组时长 ×2（HMI `AuroraOrb.tsx:30` 的 `dm`）、根 opacity 0.6（`:50`）；Ripple 周期同乘 | §6 光球 ×0.5 频率 ×0.6 透明度 | T3 |
| `mobile/src/ui/aurora/EdgeGlow.tsx`、`ThinkDots.tsx`、`StreamCursor.tsx` | `animated?: boolean`（默认 true）：false 时定格（EdgeGlow 常亮 0.6 / 三点 0.7 / 光标常显） | reduce-motion「循环全部降静帧」（aurora.css 同款覆盖 cursor 与 dots） | T3 |
| `mobile/src/features/chat/usePresence.ts` | `driving` 改读 `drivingActive(...)`；`needsTick` 加 30s 宽限窗与行车档 3s 回落窗；提示音 effect（与触感同形）；`drivingSuggest` 输入；opts 加 `landscape` | T2 / T4 / T10 | T2 T4 T10 |
| `mobile/src/core/presence/presence.ts` | 输入 `drivingSuggest?`、`voice.answeredAt?`；胶囊 `action?: 'open-sheet' \| 'enable-driving'`；行车档 B/C 语音层常驻 + 答后 3s 回落 0.4 的 detent | §6 触发③ / §5.2 规则 3 行车条款 / §6「语音层常驻」 | T10 T11 |
| `mobile/test/presence.test.ts` | +2（建议胶囊只在三条件下出且不压过收音/播报）+5（行车档 B/C 常驻、A 不常驻、答后 3s 回落、dismissed 仍可收、card 仍 0.78） | 判据守卫 | T10 T11 |
| `mobile/src/features/chat/ChatScreen.tsx` | `tablet` → `useLayout` 五模式渲染；舞台三段抽出；`BlurTargetView` 包住列表 + `blurReady`；`BackHandler` 顺序；折叠切屏时 PTT 松手；胶囊 `action` 分派；行车档 props 下发（Composer / VoiceSheet / Dock / 气泡）；`:511` 过期注释清掉 | §7.2 / §7.3 / §7.4 / §7.5 / §5.11 / §6 | T6 T7 T8 T10 T11 |
| `mobile/src/features/chat/VoiceSheet.tsx` | 壳底：`blurTarget` 在场且未回落时 `BlurView` + 更薄的 tint；`EdgeGlow active={edgeGlowActive(snapshot)}`；转写/回答 `accessibilityLiveRegion="polite"`；行车档：120dp 球、18/20pt、常驻 idle 形态（只球 + 胶囊）、`DrivingCardSummary`、chips ≤3、按钮 56；横屏 `split`（左 40% 球+转写 / 右 60% 回答+卡） | §5.11 / §8 / §6 | T8 T9 T11 |
| `mobile/src/features/chat/Composer.tsx` | 发送键改圆形图标（`Icon name="send"`，svg 缺席回退文字）；`inputMode`（always / folded / hidden）；chips ≤3；行车档禁用上滑取消；目标 56 | B2 出账④ / §6.0 身份 / §5.1.1 行车条款 | T9 T11 |
| `mobile/src/features/chat/FocusDock.tsx`、`PresenceCapsule.tsx`、`FollowUpChips.tsx`、`features/cards/CardGroup.tsx`、`core/session/followUps.ts` | 目标按 `snapshot.driving` 取 `TARGET.driving`；胶囊浅色实底 + `action` + recognizing 时 live region 让给转写区；chips `max` 参数 | §6 ≥56dp / §8 浅色不透明 / §8.1 partial 节流 | T9 T11 |
| `mobile/src/features/chat/MessageBubble.tsx` | `ProcessFold` 在 `msg.driving \|\| driving` 时单行锁定不可展开；头像球 `animated={active && loops}` | §6 过程区单行（A-6.3）/ reduce-motion | T3 T11 |
| `mobile/src/features/cards/parts.tsx` | `CardButtons`：`accessibilityRole="button"` + label，minHeight 44 + hitSlop 2（=48 目标） | §8 TalkBack「卡片按钮补 role/label」/ §5.4 行高 | T9 |
| `mobile/src/features/cards/CardRenderer.tsx` | `FallbackCard` 改读 `cardFields`：有 `items[]` 时渲通用列表行（最多 5）+ soc 等主字段 | `charging_list` 落点（B2 出账⑨） | T5 |
| `mobile/src/ui/Icon.tsx` | REGISTRY 合并 `LOCAL_ICONS`；`IconName` 联合加本地键 | 发送 / 键盘图标 | T9 |
| `mobile/src/ui/tokens.ts` + `test/tokens.test.ts` | `GLASS.frosted.tintOverBlur = 0.40`（真模糊下的壳底 tint，**待证参数**，T8 步骤 5 取数后可改）；`TARGET.driving` 不变 | §5.11 | T8 |
| `mobile/src/core/voice/speech.ts` | `proactive(text, msg)` + `setProactiveCtx({driving, s2sBusy})`；`PendingSpeech`（共享）作 DEFER 队列，`setSpeaking(false)` 时补播 | §6 播报收紧 / §5.6 | T12 |
| `mobile/src/core/session/store.ts::SpeechSink` | 接口加可选 `proactive?()` | 同上（FakeSpeech 不实现也过） | T12 |
| `mobile/src/features/settings/SettingsScreen.tsx` | 新分区「身份与行车」置顶（身份三选 + 身份说明行 + 行车档开关）；语音播报分区加「提示音」；实验室加「减少动效（强制）」「减少透明度」 | §6.0 / §8 / §8.1 | T4 T8 T10 |
| `mobile/src/core/presence/fixtures.ts` + `test/presenceFixtures.test.ts` | 样本 `driving-resident-C` / `driving-answer-B` / `driving-suggest`（`producible: true`——手动开关就能造） | 状态画廊要能看见行车档 | T11 |
| `mobile/src/app/native-spike.tsx` | 加「layout」四行：`mode / widthClass×heightClass / dp 尺寸 / 铰链 dp`——形态矩阵截图的机器读数 | 取证屏 | T6 |
| `mobile/src/app/debug.tsx` | 加一个按钮「本地回放 proactive critical 帧」（`core.handleFrame({type:'proactive',priority:'critical',speech:…,card:{…}})`，零后端） | T12 的真机取证装置（云栈没有 proactive 注入入口时用） | T12 |
| `mobile/e2e/README.md`、`docs/design/README.md`、`AGENTS.md` §4.2（只改指针）、主计划 §9（新坑追加） | 记录 | 收口 | T15 |

**刻意不动**：`app.config.ts`（含 `predictiveBackGestureEnabled`、`softwareKeyboardLayoutMode`）、`package.json`、`plugins/*`、`modules/*`（B3 交付物只消费）；`hmi/src/*` 全部（`stageScene` 的对账测试只**读**它）；`@shared/voiceLoop.mjs` / `pendingOps.mjs` / `proactiveSpeech.mjs`（只消费 `decideSpeech` / `PendingSpeech`）；`Msg` 共享类型；v1 回滚分支代码（`ChatScreen.tsx` 的 `HF_LABEL` / `legacyHint` 等，§5 第 7 条）；`orbAnimated` 在 G5 里的语义（reduce-motion 只是在它之上再压一层）；B3 §6 记录与 B3 计划正文（被推翻的判据已在那里出账，不回改）。

### 1.1 追溯：方案 / 出账的每条要求指到哪个任务（自检用，写完计划逐行核过）

| 来源 | 要求 | 任务 |
|---|---|---|
| 方案 §11.1 B4 行 | 尺寸类 × 姿态布局 | **T1**（判据）+ **T6**（落地）+ **T7**（姿态） |
| 方案 §11.1 B4 行 | 舞台抽屉 / 双栏 / tabletop | **T6**（抽屉 / 双栏）+ **T7**（tabletop / book） |
| 方案 §11.1 B4 行 | 行车档全套（触发、布局、门禁、动效降级） | **T2**（触发 / 退出判据）+ **T10**（手动 / 建议）+ **T11**（布局）+ **T3**（动效降级）；门禁 = §5.3.1「UI 只投影 VAL」⇒ **零任务**（§5 第 2 条） |
| 方案 §11.1 B4 行 | 无障碍与 reduce-motion | **T3**（reduce-motion）+ **T9**（live region / role / partial 节流）+ **T13 步骤 13**（Scanner 或替代读法） |
| 方案 §11.1 B4 行 | 提示音合成 | **T4** |
| 方案 §11.1 B4 行 | 「依赖 B3 的姿态模块，缺席时按 flat 降级」 | **T1**（`useLayout` 在 `FOLD_NATIVE_AVAILABLE=false` 时恒 flat；单测有格） |
| 方案 §11.2 B4 ① | 形态矩阵截图（外屏竖 / 外屏横 / 内屏 / 内屏 book / tabletop） | **T6 步骤 6**（前三张）+ **T13 步骤 8**（五张齐，手折两张需泓舟） |
| 方案 §11.2 B4 ② | `driving=true` 帧（云栈 debug 注入）→ 目标 ≥56dp（无障碍扫描器读实）、文本输入按身份、过程区单行 | **T13 步骤 1 / 3–5**（Scanner 需授权；替代读法 `target_probe.py` 明标「不是 Scanner 读数」） |
| 方案 §11.2 B4 ③ | 注入 `speed_kmh=90` 说「打开天窗」→ VAL 拒绝 → Dock `safety_blocked` 显示原话、无全屏层 | **T13 步骤 6**：「无全屏层」+「UI 不据 driving 加限制」两半可验；「Dock `safety_blocked`」半**无产出方（协议无结构化标记，Q16 后端挂账）⇒ 记 ⬜ 待 Q16**，不许客户端正则猜（§5 第 2 条） |
| 方案 §11.2 B4 ④ | reduce-motion 开 → 光球静帧 | **T3**（判据）+ **T13 步骤 7**（两帧逐字节 diff 0.00% + 反例） |
| 方案 §11.3 | Maestro `07-tablet-two-pane`（`device_state 3` → `stage-pane`）；新 testID `stage-pane` | **T6 步骤 5** |
| 方案 §11.4 | 性能（同屏循环动画 1 个；语音层升起 ≥55fps）/ 无障碍 Scanner 基线 / 可读性外部分布 / 回归条数只增不减 | **§4 取数表**；**T13 步骤 12–14**；**T14** |
| 方案 §11.5 | 回滚粒度按开关；v1 代码 B4 稳定后再删 | 行车档 = `drivingManual` 开关 + 身份 = `deviceRole`（**T10**）；**v1 不删**（§5 第 7 条） |
| 方案 §6.0 | 三身份由 token scope + 设备角色定；设置页顶部身份说明行；A/B/C 行车差异；Dock `safety_blocked` 身份 A 补「本机不控车」 | **T10**（UI + 说明行）+ **T11**（差异）；「本机不控车」那句随 `safety_blocked` 产出方（Q16）一起，B4 只在 `DEGRADATION_TEXT` 里留位 |
| 方案 §6 触发 | Edge `driving=true` / 手动 / 身份 C + 横屏 + keep-awake 只**建议** | **T2**（判据）+ **T10**（开关 + 建议胶囊） |
| 方案 §6 规则 | 语音层常驻、120dp 球、18/20pt、一屏一卡（标题 + ≤2 字段 + 1 主按钮）、过程区单行、chips ≤3、文本输入按身份、目标 ≥56、×0.5/×0.6、barge-in 开、播报收紧、确认只投影 VAL、横屏 40:60、退出 30s 不清记录 | **T11**（布局全部）+ **T3**（×0.5/×0.6）+ **T12**（播报收紧）；barge-in 已开（voiceLoop，零任务）；确认零任务（§5.3.1）；退出 **T2** |
| 方案 §5.2 规则 3 行车条款 | 行车档下 TTS 结束后 +3s 自动收起（与「常驻」的合并写法见 §5 第 15 条） | **T11**（detent 回落 0.4） |
| 方案 §5.1.1 行车条款 | 行车档只保留「按住—松开发送」，禁用上滑取消；按下给一次明确触感 | **T11**（Composer `driving` 时 `CANCEL_DY` 不生效）；触感 = B3 T5 的 wake 挂点已有 |
| 方案 §5.4 | 候选列表行高 ≥48（行车 ≥56） | **T9**（`CardButtons` 48）+ **T11**（`DrivingCardSummary` 56）；34 型卡逐处改行高**不在本批**（方案 §5.9「不做全仓扫荡」） |
| 方案 §7.1 | 五档宽度类 / 三档高度类；真机 dp 用 `wm size / wm density` 读实 | **T1**；读实在 **T6 步骤 1** |
| 方案 §7.2 | 单栏 / 抽屉 / 双栏（≥720 内容约束）/ 横屏车载；舞台宽 clamp(320, 42%, 440)；舞台场景判定同 HMI | **T1** + **T5** + **T6** |
| 方案 §7.3 布局消费半 | tabletop 上下两半、book 强制双栏且铰链落 gap | **T7** |
| 方案 §7.4 | 形态切换语音层不收起只重排；正在录音的 PTT 在外→内切换瞬间按松手处理 | **T7 步骤 3** |
| 方案 §7.5 | 分屏不崩不遮（落单栏）；返回手势先收语音层再退页面 | **T6 步骤 6**（分屏取证）+ **T7 步骤 4**（BackHandler） |
| 方案 §7.6 | 键盘避让 | **零任务**（B1 T12 修、B2 G3 验、B3 §5 第 1 条裁定不装 keyboard-controller）；Maestro 08 回归在 T6/T13 |
| 方案 §8 提示音 | `OscillatorNode + GainNode` 两音上行 C5→G5 各 60ms gain 0.15；同一 `AudioContext`；只在 listening（唤醒）与 attention 进入；speaking 首音不响；设置默认开、行车强制开 | **T4** |
| 方案 §8 触感 | 四种（B3 已落）；`shutter`≡`wake` | **T13 步骤 10** |
| 方案 §8 减少动效 | `AccessibilityInfo.isReduceMotionEnabled` → 光球循环降静帧 + 单次过渡 | **T3** |
| 方案 §8 TalkBack | 光球 role/label（B1/B2 已做）；转写区 / 回答区 live region；Dock 进入播报；卡片按钮 role/label | **T9**（live region + 卡片按钮）；Dock 进入播报 = B1 的 `accessibilityLiveRegion="assertive"` 已覆盖，**不再加 `announceForAccessibility`**（会双读，§8.1 对账） |
| 方案 §8 对比与字号 | 「大字」= token `scale()`（B1 已落）；浅色下胶囊 / Dock 不透明底 | Dock 已 G0；胶囊浅色实底 **T8 步骤 6** |
| 方案 §8.1 | 200% 重排（B1 T-Dock 让位 + B2 T1 已做）/ 减少透明度 → 材质回落 / partial 节流播报 / 轻点切换（B2 T6 已做） | **T8**（减少透明度）+ **T9**（partial 节流）；其余零任务 |
| 方案 §5.11 | frosted 真模糊只在 B3 裁决过后落；G0 不许半透明；同屏不许多个动态 Blur；reduce-transparency / 行车档 / 低电量回落 G1-tint | **T8**（只落语音层外壳；低电量回落**不做**——无 `expo-battery`，§5 第 9 条） |
| B2 §6.4 出账④ | 发送按钮改图标（泓舟要看方案） | **T9**（圆形极光底 + 纸飞机线性图标；截图交泓舟） |
| B2 §6.4 出账⑨ | `charging_list` 未适配、无 `_prov` | **T5**（通用列表兜底）；types.ts / `_prov` 是 hmi 与 agent 侧的账（§6.4 出账） |
| B2 §6.4 出账⑩ | `EdgeGlow` 零 jest | **T3** |
| B2 §6.4 出账⑫ | 壳底与浅色光球对比度 | **T8 步骤 5** |
| B3 §6.3 遗留③ | `shutter`≡`wake` | **T13 步骤 10** |
| B3 §6.3 遗留⑤ | `Reanimated: synchronouslyUpdateUIProps failed` | **T13 步骤 9** |
| B3 §6.3 遗留⑥ | B2 表#6 顺序取证 / 「换一批」chip | **T13 步骤 11** |
| B3 §6.1 遗留④ | `ChatScreen.tsx:511` 过期注释 | **T10** 顺手 |
| AGENTS ①② | 唤醒率 / 播报卡顿对照构建 | **§0 第 3 条待裁，不进任务** |
| AGENTS ④ | B3′ 结论 | **不在 B4**，§6.4 原样转出给 B5 |
| AGENTS ⑤ | 外部小样本无分布读数 | **T14** |

---

## 2. 任务清单

### Task 1: 尺寸类与布局判据——`sizeClass.ts` 纯函数 + `useLayout` 收集器（方案 §7.1 / §7.2 / §7.3 / §7.4 / §7.5）

**Files:**
- 新建 `mobile/src/ui/layout/sizeClass.ts`、`mobile/src/ui/layout/useLayout.ts`、`mobile/test/sizeClass.test.ts`

**为什么**：`ChatScreen.tsx:69` 的 `tablet = min(w,h) >= 600` 是 P7 的全部现状——无横屏、无姿态、无分屏、无尺寸类。方案 §7.1 要宽高各算五档 × 三档，§7.2 要四种布局且**双栏阈值 720 是内容约束**（对话 360 + 舞台 320 + gap 24 + 16 余量），不是设备判据；§7.3 要 book / tabletop 两种姿态压过尺寸；§7.5 要分屏落单栏不崩。这些都是「给定几个数与一个姿态 → 一种布局」的纯映射，先写成纯函数钉住，第 2 批的组件只消费。姿态事实来自 B3 的 `foldPosture`（真机四格已验，B3 §6.3 T9 步骤 2）：外屏报 `state: none`（无折叠特征）⇒ `flat`。`useLayout` 只收集（`useWindowDimensions` + `useFoldState` + `driving`），零判断；原生 foldstate 缺席时 `useFoldState` 恒 null ⇒ 恒 flat（方案 §11.1 B4 行的降级）。

- [ ] **步骤 1：写失败测试**

`mobile/test/sizeClass.test.ts`：

```ts
// mobile/test/sizeClass.test.ts
// 尺寸类 × 姿态 × 行车 → 布局模式（方案 §7.1–§7.5）。真机 dp 是估算（420dpi：外屏 411×960 / 内屏 847×948，
// 440dpi 时内屏 809）——T6 步骤 1 用 wm size/density 读实后，把这里的数改成实测值再跑一遍。
import {
  TWO_PANE_MIN_WIDTH,
  bookSplit,
  heightClass,
  layoutMode,
  screenSwitch,
  stageWidth,
  tabletopSplit,
  widthClass,
} from '@/ui/layout/sizeClass'

describe('尺寸类（Material 3 WindowSizeClass v2，宽高各算）', () => {
  test('宽度五档边界（large / extra-large 今天没设备命中，枚举要齐）', () => {
    expect(widthClass(599)).toBe('compact')
    expect(widthClass(600)).toBe('medium')
    expect(widthClass(839)).toBe('medium')
    expect(widthClass(840)).toBe('expanded')
    expect(widthClass(1199)).toBe('expanded')
    expect(widthClass(1200)).toBe('large')
    expect(widthClass(1600)).toBe('extra-large')
  })
  test('高度三档边界', () => {
    expect(heightClass(479)).toBe('compact')
    expect(heightClass(480)).toBe('medium')
    expect(heightClass(899)).toBe('medium')
    expect(heightClass(900)).toBe('expanded')
  })
})

describe('layoutMode：真机 dp 逐格', () => {
  const flat = (width: number, height: number, driving = false) => layoutMode({ width, height, posture: 'flat', driving })
  test('外屏竖 411×960 → 单栏', () => expect(flat(411, 960)).toBe('single'))
  test('外屏横 960×411 → 高度 compact 且非行车 → 单栏；行车 → 横屏车载', () => {
    expect(flat(960, 411)).toBe('single')
    expect(flat(960, 411, true)).toBe('driving-landscape')
  })
  test('内屏 847×948 → 双栏；密度 440 时 809 也双栏（阈值不卡 840，§7.1）', () => {
    expect(flat(847, 948)).toBe('two-pane')
    expect(flat(809, 905)).toBe('two-pane')
  })
  test('720 是内容约束的边：719 不双栏（medium ⇒ 舞台抽屉），720 双栏', () => {
    expect(TWO_PANE_MIN_WIDTH).toBe(720)
    expect(flat(719, 900)).toBe('drawer')
    expect(flat(720, 900)).toBe('two-pane')
  })
  test('分屏：宽 compact / 高 compact 都落单栏（§7.5 不崩不遮）', () => {
    expect(flat(411, 470)).toBe('single')
    expect(flat(600, 470)).toBe('single')
  })
  test('行车但宽度不到 expanded → 单栏（手机横屏 medium 不是车载支架）', () => expect(flat(700, 411, true)).toBe('single'))
  test('姿态压过尺寸：book 在 720 以下也强制双栏；tabletop 上下半', () => {
    expect(layoutMode({ width: 700, height: 900, posture: 'book', driving: false })).toBe('two-pane')
    expect(layoutMode({ width: 847, height: 948, posture: 'tabletop', driving: false })).toBe('tabletop')
  })
})

describe('舞台几何', () => {
  test('stageWidth = clamp(320, 42%, 440)；large 上限 520', () => {
    expect(stageWidth(847)).toBe(356)
    expect(stageWidth(720)).toBe(320)
    expect(stageWidth(1100)).toBe(440)
    expect(stageWidth(1300)).toBe(520)
  })
  test('book：铰链落 gap 正中（gap = 铰链宽 + 16）；无铰链几何退回舞台分法', () => {
    expect(bookSplit(847, { leftDp: 423, widthDp: 0 })).toEqual({ chat: 415, gap: 16 })
    expect(bookSplit(847, null)).toEqual({ chat: 847 - 356 - 24, gap: 24 })
  })
  test('tabletop：分界 = 铰链上缘 − 内容区上方已占高度；不合理时对半', () => {
    expect(tabletopSplit(800, 500, 100)).toBe(400)
    expect(tabletopSplit(800, 50, 100)).toBe(400)
    expect(tabletopSplit(800, 950, 100)).toBe(400)
  })
})

describe('外屏 ↔ 内屏切换（§7.4）', () => {
  test('none → flat/halfOpened = 外到内；反向 = 内到外；内屏内部变化与缺席都不是切屏', () => {
    expect(screenSwitch({ state: 'none' }, { state: 'flat' })).toBe('outer-to-inner')
    expect(screenSwitch({ state: 'halfOpened' }, { state: 'none' })).toBe('inner-to-outer')
    expect(screenSwitch({ state: 'flat' }, { state: 'halfOpened' })).toBeNull()
    expect(screenSwitch(null, { state: 'flat' })).toBeNull()
  })
})
```

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/sizeClass.test.ts`
Expected: FAIL（模块不存在）

- [ ] **步骤 3：实现**

`mobile/src/ui/layout/sizeClass.ts`：

```ts
// mobile/src/ui/layout/sizeClass.ts
// 尺寸类与布局判据（B4-1 / 方案 §7.1 §7.2 §7.3 §7.5）——纯函数、零 RN import（jest 直接跑）。
// 替代 ChatScreen.tsx:69 的 `tablet = min(w,h) >= 600` 单布尔。宽高各算（Material 3 WindowSizeClass v2；
// large / extra-large 今天没设备命中，但枚举要齐——桌面窗口 / 外接屏到了不该落进 expanded 分支）。
// **双栏阈值 720 是内容约束**（对话 360 + 舞台 320 + gap 24 + 16 余量，方案 Q3），不是「为了折叠屏一定双栏」；
// 折叠内屏密度不定（809 / 847dp）只说明为什么不能卡 840。姿态事实来自 foldPosture（B3），这里只消费。
import type { FoldEvent } from '../../../modules/foldstate'
import type { FoldPosture } from './foldPosture'

export type WidthClass = 'compact' | 'medium' | 'expanded' | 'large' | 'extra-large'
export type HeightClass = 'compact' | 'medium' | 'expanded'
export type LayoutMode = 'single' | 'drawer' | 'two-pane' | 'tabletop' | 'driving-landscape'

export function widthClass(w: number): WidthClass {
  if (w < 600) return 'compact'
  if (w < 840) return 'medium'
  if (w < 1200) return 'expanded'
  if (w < 1600) return 'large'
  return 'extra-large'
}

export function heightClass(h: number): HeightClass {
  if (h < 480) return 'compact'
  if (h < 900) return 'medium'
  return 'expanded'
}

/** 双栏的内容约束（§7.2）：对话 360 + 舞台 320 + gap 24 + 16 余量 = 720 */
export const CHAT_MIN_WIDTH = 360
export const STAGE_MIN_WIDTH = 320
export const PANE_GAP = 24
export const TWO_PANE_MIN_WIDTH = CHAT_MIN_WIDTH + STAGE_MIN_WIDTH + PANE_GAP + 16
export const STAGE_RATIO = 0.42
export const STAGE_MAX_WIDTH = 440
/** large / extra-large 只把舞台上限放宽到 520（§7.1），布局仍同 expanded */
export const STAGE_MAX_WIDTH_LARGE = 520
export const DRAWER_WIDTH = 320
export const DRAWER_HANDLE = 48
/** book 姿态：铰链落在两栏 gap 正中（§7.3），gap = 铰链宽 + 16 */
export const BOOK_GAP_EXTRA = 16

export interface LayoutInput {
  width: number
  height: number
  posture: FoldPosture
  driving: boolean
}

export function layoutMode(i: LayoutInput): LayoutMode {
  const wc = widthClass(i.width)
  const hc = heightClass(i.height)
  // 姿态压过尺寸（§7.3）：半开的物理形态决定内容怎么分，不看 dp
  if (i.posture === 'tabletop') return 'tabletop'
  if (i.posture === 'book') return 'two-pane'
  if (hc === 'compact') {
    // §7.2 第四行：width expanded × height compact + 行车档 = 横屏车载；
    // 其余 height compact 一律单栏（手机横屏、§7.5 分屏都落这里）
    return i.driving && wc !== 'compact' && wc !== 'medium' ? 'driving-landscape' : 'single'
  }
  if (i.width >= TWO_PANE_MIN_WIDTH) return 'two-pane'
  if (wc === 'medium') return 'drawer'
  return 'single'
}

/** 舞台宽：clamp(320, 42%, 440)；large / extra-large 上限 520 */
export function stageWidth(width: number): number {
  const wc = widthClass(width)
  const max = wc === 'large' || wc === 'extra-large' ? STAGE_MAX_WIDTH_LARGE : STAGE_MAX_WIDTH
  return Math.round(Math.min(max, Math.max(STAGE_MIN_WIDTH, width * STAGE_RATIO)))
}

/** book：左栏宽 = 铰链左缘 − gap/2（铰链在 gap 正中）；铰链几何缺席或不合理时退回舞台分法 */
export function bookSplit(
  width: number,
  hinge: { leftDp: number; widthDp: number } | null,
): { chat: number; gap: number } {
  if (!hinge || !(hinge.leftDp > 0) || hinge.leftDp >= width) {
    return { chat: width - stageWidth(width) - PANE_GAP, gap: PANE_GAP }
  }
  const gap = hinge.widthDp + BOOK_GAP_EXTRA
  return { chat: Math.round(hinge.leftDp - gap / 2), gap }
}

/** tabletop：上半 = 舞台 + 大光球，下半 = 转写 + Composer；分界 = 铰链上缘 − 内容区上方已占的高度 */
export function tabletopSplit(contentHeight: number, hingeTopDp: number, contentTopDp: number): number {
  const top = hingeTopDp - contentTopDp
  if (!(top > 0) || top >= contentHeight) return Math.round(contentHeight / 2)
  return Math.round(top)
}

/** 外屏 ↔ 内屏（§7.4）：外屏没有 FoldingFeature（B3 实测 state=none），内屏是 flat / halfOpened */
export type ScreenSwitch = 'outer-to-inner' | 'inner-to-outer'

export function screenSwitch(
  prev: Pick<FoldEvent, 'state'> | null,
  next: Pick<FoldEvent, 'state'> | null,
): ScreenSwitch | null {
  if (!prev || !next) return null
  const inner = (e: Pick<FoldEvent, 'state'>) => e.state !== 'none'
  if (!inner(prev) && inner(next)) return 'outer-to-inner'
  if (inner(prev) && !inner(next)) return 'inner-to-outer'
  return null
}
```

`mobile/src/ui/layout/useLayout.ts`：

```ts
// mobile/src/ui/layout/useLayout.ts
// 布局事实收集（B4-1）：窗口尺寸（useWindowDimensions，旋转 / 展开即时重算）+ 折叠姿态（B3 hook）
// + 行车档 → sizeClass.layoutMode()。**零判断**：所有「该用哪种布局」都在 sizeClass.ts 里且有测试。
// 原生 foldstate 缺席（旧 APK）时 useFoldState 恒 null ⇒ posture 恒 flat（方案 §11.1 B4 行「缺席按 flat 降级」）。
// 切屏事件（§7.4 的 PTT 松手）**不在这里算**——渲染期用 ref 记 prev 在 StrictMode 双渲下会把事件吞掉，
// 放消费方（ChatScreen）的 useEffect 里用 screenSwitch() 判（T7）。
import { useMemo } from 'react'
import { PixelRatio, useWindowDimensions } from 'react-native'

import type { FoldEvent } from '../../../modules/foldstate'
import { foldPosture, type FoldPosture } from './foldPosture'
import {
  bookSplit,
  heightClass,
  layoutMode,
  stageWidth,
  widthClass,
  type HeightClass,
  type LayoutMode,
  type WidthClass,
} from './sizeClass'
import { useFoldState } from './useFoldState'

export interface LayoutSpec {
  mode: LayoutMode
  width: number
  height: number
  widthClass: WidthClass
  heightClass: HeightClass
  posture: FoldPosture
  /** 舞台宽（two-pane / drawer 用） */
  stage: number
  /** book：左栏宽与 gap（铰链落 gap 正中） */
  book: { chat: number; gap: number }
  /** 铰链几何（dp，窗口坐标）；无折叠特征时 null */
  hinge: { leftDp: number; topDp: number; widthDp: number; heightDp: number } | null
  /** 原生事件原样带出（T7 的切屏判定读 state） */
  fold: FoldEvent | null
  landscape: boolean
}

export function useLayout(driving: boolean): LayoutSpec {
  const { width, height } = useWindowDimensions()
  const fold = useFoldState()
  return useMemo(() => {
    const posture = foldPosture(fold)
    const px = PixelRatio.get()
    const b = fold?.bounds ?? null
    const hinge = b
      ? { leftDp: b.left / px, topDp: b.top / px, widthDp: (b.right - b.left) / px, heightDp: (b.bottom - b.top) / px }
      : null
    return {
      mode: layoutMode({ width, height, posture, driving }),
      width,
      height,
      widthClass: widthClass(width),
      heightClass: heightClass(height),
      posture,
      stage: stageWidth(width),
      book: bookSplit(width, hinge && posture === 'book' ? { leftDp: hinge.leftDp, widthDp: hinge.widthDp } : null),
      hinge,
      fold,
      landscape: width > height,
    }
  }, [width, height, fold, driving])
}
```

- [ ] **步骤 4：跑绿 + tsc + 全量**

Run: `cd mobile && npx jest test/sizeClass.test.ts && npm run typecheck && npm test`
Expected: 新增 13 条全绿；0 error；全量只增不减（413 → 426）。本任务**不接任何组件**——`useLayout` 此时零消费方，tsc 过即可（第 2 批 T6 接）。

- [ ] **步骤 5：反向验证**（每条先 grep 证明变异落盘，跑完还原复跑全绿）

① `layoutMode` 里 `i.width >= TWO_PANE_MIN_WIDTH` 改成 `>= 840` ⇒ **恰好红**「809 也双栏」与「720 双栏」两条（这就是 §7.1「不要卡 840」的可测形态）；② 姿态两行删掉 ⇒ 恰好红「姿态压过尺寸」；③ `heightClass` compact 边界改成 `< 600` ⇒ 恰好红「外屏横 → 单栏」之外还红「分屏 600×470」的反面——看清是哪几条，别只数红的条数；④ `bookSplit` 的 `gap / 2` 改成 `gap` ⇒ 恰好红 book 那条。

- [ ] **提交**

```bash
git add -- mobile/src/ui/layout/sizeClass.ts mobile/src/ui/layout/useLayout.ts mobile/test/sizeClass.test.ts && git commit -m "feat(mobile): UX v2 B4-1 尺寸类与布局判据——widthClass/heightClass 五档三档 + layoutMode（双栏 720 内容约束、姿态压过尺寸、分屏落单栏）+ useLayout 收集器（缺席恒 flat）" -- mobile/src/ui/layout/sizeClass.ts mobile/src/ui/layout/useLayout.ts mobile/test/sizeClass.test.ts && git show --stat HEAD
```

---

### Task 2: 行车档事实与判据——Edge `driving` 标登记 + `drivingMode.ts` 纯函数 + `usePresence` 改读（方案 §6 触发 / 退出、§6.0 身份差异）

**Files:**
- 新建 `mobile/src/core/presence/drivingMode.ts`、`mobile/test/drivingMode.test.ts`
- 修改 `mobile/src/core/session/store.ts`（`drivingEdge`）、`mobile/test/sessionStore.test.ts`（+3）、`mobile/src/core/settings/store.ts`（`drivingManual`）、`mobile/test/settingsMeta.test.ts`（+2）、`mobile/src/features/chat/usePresence.ts`（`driving` 改读判据 + tick 窗）

**为什么**：P8「`driving` 存了没人读」的真相比方案写的更窄：它只在 `process` 帧上（`store.ts:498`），且 `usePresence.ts` 读的是**在飞那条气泡**的 `driving`——轮一结束就掉回 false，而行车档要跨轮存在（§6 退出条款「Edge `driving=false` 持续 30s」本身就要求记住上一次标注）。所以事实登记进 `SessionState.drivingEdge = { trueAt, falseAt }`（并列字段，`Msg` 共享类型不动），判据是纯函数 `drivingActive()`：手动 ∨ 最近一次 Edge 标 true ∨ 标 false 后 30s 内。**客户端不拿 `vehicle_state` 的 `speed_kmh / gear` 再算一份**——`server.py::_is_driving` 是唯一裁决点，§5.3.1 删的正是 UI 自己的车速规则。同文件放身份 → 文本输入形态（§6.0：A 常驻 / B 折叠 / C 隐藏）、语音层是否常驻（B/C）、建议三条件（身份 C + 横屏 + keep-awake，只建议不自动切——自动切会误伤副驾用平板）。「持续 30s」从**第一条** false 起算，连续 false 不刷新起点——否则复杂任务每帧一条 false 会把退出无限推迟。

- [ ] **步骤 1：写失败测试**

`mobile/test/drivingMode.test.ts`：

```ts
// mobile/test/drivingMode.test.ts
// 行车档判据（方案 §6 触发 / 退出、§6.0 身份差异）。事实只有 Edge 的 process 帧标注与手动开关；
// 客户端不看 speed_kmh（那是第二份判据）。
import {
  DRIVING_EXIT_GRACE_MS,
  composerInputMode,
  drivingActive,
  drivingSuggested,
  recordEdgeDriving,
  sheetResident,
} from '@/core/presence/drivingMode'

const NOW = 1_000_000

describe('drivingActive', () => {
  test('手动开 ⇒ 行车，不看 Edge', () => {
    expect(drivingActive({ manual: true, edge: { trueAt: 0, falseAt: 0 }, now: NOW })).toBe(true)
  })
  test('从未有 Edge 标 ⇒ 非行车', () => {
    expect(drivingActive({ manual: false, edge: { trueAt: 0, falseAt: 0 }, now: NOW })).toBe(false)
  })
  test('最近一次 Edge 标 true ⇒ 行车（退出只由 false 或手动触发，不按时间自动退）', () => {
    expect(drivingActive({ manual: false, edge: { trueAt: NOW - 3_600_000, falseAt: 0 }, now: NOW })).toBe(true)
  })
  test('Edge 标 false 后 30s 内仍算行车，满 30s 退出', () => {
    const edge = { trueAt: NOW - 60_000, falseAt: NOW - 10_000 }
    expect(drivingActive({ manual: false, edge, now: NOW })).toBe(true)
    expect(drivingActive({ manual: false, edge, now: edge.falseAt + DRIVING_EXIT_GRACE_MS - 1 })).toBe(true)
    expect(drivingActive({ manual: false, edge, now: edge.falseAt + DRIVING_EXIT_GRACE_MS })).toBe(false)
  })
})

describe('recordEdgeDriving：「持续 30s」从第一条 false 起算', () => {
  test('true 登记 trueAt 并清 falseAt', () => {
    expect(recordEdgeDriving({ trueAt: 1, falseAt: 5 }, true, 10)).toEqual({ trueAt: 10, falseAt: 0 })
  })
  test('从没行车过的 false 不登记', () => {
    expect(recordEdgeDriving({ trueAt: 0, falseAt: 0 }, false, 10)).toEqual({ trueAt: 0, falseAt: 0 })
  })
  test('true 后第一条 false 登记 falseAt；之后的 false 不刷新起点', () => {
    const a = recordEdgeDriving({ trueAt: 10, falseAt: 0 }, false, 20)
    expect(a).toEqual({ trueAt: 10, falseAt: 20 })
    expect(recordEdgeDriving(a, false, 35)).toEqual({ trueAt: 10, falseAt: 20 })
  })
})

describe('身份 → 文本输入形态与语音层常驻（§6.0）', () => {
  test('非行车一律常驻', () => {
    for (const id of ['handheld', 'mount', 'trusted-tablet'] as const) expect(composerInputMode(id, false)).toBe('always')
  })
  test('行车：A 常驻 / B 折叠 / C 隐藏', () => {
    expect(composerInputMode('handheld', true)).toBe('always')
    expect(composerInputMode('mount', true)).toBe('folded')
    expect(composerInputMode('trusted-tablet', true)).toBe('hidden')
  })
  test('语音层常驻只给 B / C，且只在行车', () => {
    expect(sheetResident('handheld', true)).toBe(false)
    expect(sheetResident('mount', true)).toBe(true)
    expect(sheetResident('trusted-tablet', true)).toBe(true)
    expect(sheetResident('trusted-tablet', false)).toBe(false)
  })
})

describe('建议开行车档（§6 触发③）：三条件缺一不出，已在行车不出', () => {
  const ok = { identity: 'trusted-tablet' as const, landscape: true, keepAwake: true, active: false }
  test('三条件齐 ⇒ 建议', () => expect(drivingSuggested(ok)).toBe(true))
  test.each([
    ['identity', { identity: 'mount' as const }],
    ['landscape', { landscape: false }],
    ['keepAwake', { keepAwake: false }],
    ['active', { active: true }],
  ])('缺 %s ⇒ 不建议', (_name, over) => {
    expect(drivingSuggested({ ...ok, ...over })).toBe(false)
  })
})
```

`mobile/test/sessionStore.test.ts` 追加一个 describe（用文件里既有的 `newCore()` 与 `FakeTransport.lastUserFrame()`；该文件已 `jest.useFakeTimers()`——若那句在某个 describe 内，本 describe 自己 `beforeEach(() => jest.useFakeTimers())`；`final` 帧形状照文件里既有用例）：

```ts
describe('B4-2 行车档事实登记（Edge process 帧的 driving 标）', () => {
  test('driving:true 登记 trueAt，轮结束后仍在（不靠在飞轮）', () => {
    const { transport, core } = newCore()
    core.send('帮我规划去杭州的充电路线')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'process', request_id: rid, phase: 'plan', label: '规划', status: 'running', driving: true })
    expect(core.store.getState().drivingEdge.trueAt).toBeGreaterThan(0)
    core.handleFrame({ type: 'final', request_id: rid, speech: '好的' })
    expect(core.store.getState().drivingEdge.trueAt).toBeGreaterThan(0)
  })
  test('true 之后的 false 登记 falseAt；再来一条 false 不刷新起点', () => {
    const { transport, core } = newCore()
    core.send('a')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'process', request_id: rid, phase: 'plan', label: '规划', status: 'running', driving: true })
    jest.advanceTimersByTime(5_000)
    core.handleFrame({ type: 'process', request_id: rid, phase: 'execute', label: '检索', status: 'running', step_id: 's1', driving: false })
    const first = core.store.getState().drivingEdge.falseAt
    expect(first).toBeGreaterThan(core.store.getState().drivingEdge.trueAt)
    jest.advanceTimersByTime(5_000)
    core.handleFrame({ type: 'process', request_id: rid, phase: 'execute', label: '检索', status: 'done', step_id: 's1', driving: false })
    expect(core.store.getState().drivingEdge.falseAt).toBe(first)
  })
  test('从没行车过的 false 不登记', () => {
    const { transport, core } = newCore()
    core.send('a')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'process', request_id: rid, phase: 'plan', label: '规划', status: 'running', driving: false })
    expect(core.store.getState().drivingEdge).toEqual({ trueAt: 0, falseAt: 0 })
  })
})
```

`mobile/test/settingsMeta.test.ts` 追加 2 条（追加进既有 describe，入参必须是合法 JSON——B2 坑①）：

```ts
test('B4-2：旧库没有 drivingManual → 水合补默认 false', () => {
  expect(mergeStoredSettings(JSON.stringify({ theme: 'dark' })).drivingManual).toBe(false)
})
test('B4-2：旧库显式 drivingManual:true → 保持（合并不许把用户的开覆盖回默认关）', () => {
  expect(mergeStoredSettings(JSON.stringify({ drivingManual: true })).drivingManual).toBe(true)
})
```

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/drivingMode.test.ts test/sessionStore.test.ts test/settingsMeta.test.ts`
Expected: FAIL（模块不存在 / `drivingEdge` undefined / 键缺失）

- [ ] **步骤 3：实现**

`mobile/src/core/presence/drivingMode.ts`：

```ts
// mobile/src/core/presence/drivingMode.ts
// 行车档判据（B4-2 / 方案 §6）——纯函数、零 RN import。
// 事实只有两种来源：① Edge 在 process 帧上按 VAL 标注的 `driving`（server.py::_is_driving 是唯一裁决点：
//    speed_kmh > 0 或挡位 D/R/S；客户端**不**用 vehicle_state 的 speed_kmh/gear 再算一份——那是第二份判据，
//    §5.3.1 删的就是它）；② 用户手动开关。退出：Edge 标 false 起持续 30s（§6「退出」），或手动关。
// 建议（§6 触发③）：身份 C + 横屏 + keep-awake 同时成立时**只建议**（胶囊），不自动切——自动切会误伤副驾用平板。
import type { Identity } from './presence'

export const DRIVING_EXIT_GRACE_MS = 30_000

export interface DrivingEdgeFact {
  /** 最近一次 Edge 标 driving=true 的时刻；0=从未 */
  trueAt: number
  /** 最近一次「由 true 转 false」的时刻（连续 false 不刷新——「持续 30s」从第一条 false 起算）；0=无 */
  falseAt: number
}

export const NO_EDGE_DRIVING: DrivingEdgeFact = { trueAt: 0, falseAt: 0 }

export function drivingActive(f: { manual: boolean; edge: DrivingEdgeFact; now: number }): boolean {
  if (f.manual) return true
  if (f.edge.trueAt <= 0) return false
  if (f.edge.falseAt <= f.edge.trueAt) return true
  return f.now - f.edge.falseAt < DRIVING_EXIT_GRACE_MS
}

/** process 帧到达时的事实登记（SessionCore 调用；写成 reducer 是为了可测） */
export function recordEdgeDriving(prev: DrivingEdgeFact, driving: boolean, now: number): DrivingEdgeFact {
  if (driving) return { trueAt: now, falseAt: 0 }
  if (prev.trueAt <= 0) return prev // 从没行车过，false 不需要记
  if (prev.falseAt > prev.trueAt) return prev // 已在 false 段里：不刷新起点
  return { trueAt: prev.trueAt, falseAt: now }
}

export type ComposerInputMode = 'always' | 'folded' | 'hidden'

/** 文本输入按身份（§6.0）：A 常驻 / B 折叠成键盘图标可点开 / C 隐藏；非行车一律常驻 */
export function composerInputMode(identity: Identity, driving: boolean): ComposerInputMode {
  if (!driving) return 'always'
  return identity === 'trusted-tablet' ? 'hidden' : identity === 'mount' ? 'folded' : 'always'
}

/** 语音层常驻（§6「语音层常驻」）只给 B / C：A 是手持，可能是乘客在打字 */
export function sheetResident(identity: Identity, driving: boolean): boolean {
  return driving && identity !== 'handheld'
}

export function drivingSuggested(i: { identity: Identity; landscape: boolean; keepAwake: boolean; active: boolean }): boolean {
  return !i.active && i.identity === 'trusted-tablet' && i.landscape && i.keepAwake
}
```

`mobile/src/core/session/store.ts`：
- import：`import { NO_EDGE_DRIVING, recordEdgeDriving, type DrivingEdgeFact } from '../presence/drivingMode'`（store → drivingMode 是值依赖，drivingMode → presence 只 `import type`，presence → store 只 `import type`：运行时无环）；
- `SessionState` 加：
  ```ts
    /** 行车档事实（B4-2）：Edge 在 process 帧上的 driving 标注。判据在 core/presence/drivingMode.ts，
     *  这里只登记「最近一次 true / 由 true 转 false 的时刻」——不靠在飞轮，轮结束后仍在 */
    drivingEdge: DrivingEdgeFact
  ```
- 初始 state（`vehState: {}` 所在的对象）加 `drivingEdge: NO_EDGE_DRIVING,`；
- `process` 分支 `const driving = !!data.driving` 之后加一行：
  ```ts
      this.store.setState((s) => ({ drivingEdge: recordEdgeDriving(s.drivingEdge, driving, Date.now()) }))
  ```
  （文件里时钟一律直接 `Date.now()`——`:293 / :450 / :557` 同款，jest 假时钟能推它。）

`mobile/src/core/settings/store.ts`：`AppSettings` 加 `drivingManual: boolean`（注释：`/** 手动行车档（方案 §6 触发②）。Edge 标 true 也进；两者取或（判据在 core/presence/drivingMode.ts） */`），`DEFAULT_APP_SETTINGS` 加 `drivingManual: false`。水合走既有 `...DEFAULT_APP_SETTINGS` 展开，零迁移逻辑。

`mobile/src/features/chat/usePresence.ts`：
- import：`import { DRIVING_EXIT_GRACE_MS, drivingActive } from '@/core/presence/drivingMode'`；
- `useStore(core.store)` 解构加 `drivingEdge`；
- `const now = Date.now()` **之后**加：
  ```ts
    // B4-2 行车档：手动 ∨ Edge 标 true ∨ 标 false 后 30s 内（判据在 drivingMode.ts）。
    // 不再读 active?.driving——那只在在飞轮上有值，轮一结束就掉回 false
    const drivingNow = drivingActive({ manual: settings.drivingManual, edge: drivingEdge, now })
  ```
- `needsTick` 加一项：`(drivingEdge.falseAt > drivingEdge.trueAt && now - drivingEdge.falseAt < DRIVING_EXIT_GRACE_MS) || // 行车档 30s 退出宽限（到点要重渲一次才退得出）`；
- `input` 里 `driving: !!active?.driving` 改成 `driving: drivingNow`。

- [ ] **步骤 4：跑绿 + tsc + 全量**

Run: `cd mobile && npx jest test/drivingMode.test.ts test/sessionStore.test.ts test/settingsMeta.test.ts && npm run typecheck && npm test`
Expected: 新增 18 条全绿（drivingMode 13 + sessionStore 3 + settingsMeta 2）；0 error；全量只增不减（426 → 444）。**看门狗三条（`sessionStore.test.ts:529/545/560`）必须仍绿**——本任务动了 store。

- [ ] **步骤 5：反向验证**（每条先 grep 证明变异落盘，跑完还原复跑全绿）

① `drivingActive` 最后一行 `<` 改 `<=` ⇒ **恰好红**「满 30s 退出」那条的第三个断言；② `recordEdgeDriving` 去掉 `prev.falseAt > prev.trueAt` 守卫 ⇒ 恰好红「之后的 false 不刷新起点」（drivingMode）**和** sessionStore 那条同名用例——两处都红才说明 store 真的走了这个 reducer；③ store 里那行 `setState` 注释掉 ⇒ sessionStore 三条全红、drivingMode 全绿（事实登记与判据是两层，各红各的）；④ `composerInputMode` 的 mount / trusted-tablet 对调 ⇒ 恰好红「A 常驻 / B 折叠 / C 隐藏」。

- [ ] **步骤 6：Metro 热载冒烟**（不取语音读数）：App 起得来、发一句文字、光球与胶囊无回归；`/presence-trail` 里 `driving` 轴不再随轮结束翻转（此时没有 Edge 标，应恒 false）。

- [ ] **提交**

```bash
git add -- mobile/src/core/presence/drivingMode.ts mobile/test/drivingMode.test.ts && git commit -m "feat(mobile): UX v2 B4-2 行车档事实与判据——SessionState.drivingEdge 登记 Edge process 帧的 driving 标（不靠在飞轮）+ drivingActive 手动∨Edge∨30s 宽限 + 身份→文本输入形态/语音层常驻/建议三条件（纯函数）+ usePresence 改读" -- mobile/src/core/presence/drivingMode.ts mobile/test/drivingMode.test.ts mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts mobile/src/core/settings/store.ts mobile/test/settingsMeta.test.ts mobile/src/features/chat/usePresence.ts && git show --stat HEAD
```

---

### Task 3: 动效策略——`orbPolicy` 扩成 `orbTempo / edgeGlowActive / loopsAnimated` + reduce-motion 事实源 + 光球行车 ×0.5/×0.6 + 循环小动效可定格（方案 §8 减少动效、§6 光球降级、B2 出账⑩）

**Files:**
- 新建 `mobile/src/core/a11y/reduceMotion.ts`
- 修改 `mobile/src/core/presence/orbPolicy.ts`、`mobile/test/orbPolicy.test.ts`（+4）、`mobile/src/ui/aurora/AuroraOrb.tsx`（`driving`）、`mobile/src/ui/aurora/EdgeGlow.tsx` / `ThinkDots.tsx` / `StreamCursor.tsx`（`animated`）、`mobile/src/core/settings/store.ts`（`reduceMotionForce`）、`mobile/test/settingsMeta.test.ts`（+1）、`mobile/src/features/chat/ChatScreen.tsx` / `Composer.tsx` / `VoiceSheet.tsx` / `MessageBubble.tsx` / `mobile/src/app/state-gallery.tsx`（消费）

**为什么**：B3-1 把「Composer 球动不动」抽成 `orbPolicy.ts` 时头注写明「B4 的 reduce-motion 将来落同一函数」。现在它有三个利益方：G5 性能（层开时主球让出那「1 个」循环动画名额，B3 §6.1 T1 的 0.00% 读数）、§8 reduce-motion（光球所有循环降静帧 + 单次过渡，十条不变量⑩「降级脚本化」）、§6 行车（×0.5 频率、×0.6 透明度，逐值照 HMI `AuroraOrb.tsx:30,50` 的 `dm` 与 `opacity`）。三者一处裁（`orbTempo`），组件只消费。`EdgeGlow` 的 `active` 表达式从 `VoiceSheet.tsx:150` 的 JSX 提成 `edgeGlowActive()`——B2 出账⑩「零 jest」的最小修法。reduce-motion 的事实源是系统 `AccessibilityInfo.isReduceMotionEnabled()`（Android「移除动画」= `animator_duration_scale=0`）**∨ 实验室强制开关**——后者是取证装置：改设备系统设置命中红线要泓舟动手（B3 §6.2 触感开关那次），App 内开关让单人就能取到 §11.2 B4 ④ 的读数，也给想要它的用户一个入口。ThinkDots / StreamCursor / EdgeGlow 呼吸也是循环（aurora.css 的 reduce-motion 规则同样覆盖 `.au-cursor / .au-think-dots / .au-edge-glow`），一并可定格。

- [ ] **步骤 1：写失败测试**

`mobile/test/orbPolicy.test.ts` 追加（既有 2 条不动；import 加 `edgeGlowActive, loopsAnimated, orbTempo`）：

```ts
describe('B4-3 动效策略（reduce-motion / 行车 / G5 三个利益方一处裁）', () => {
  test('reduce-motion 压过一切：层没开 composer 球也静止', () => {
    expect(composerOrbAnimated({ input: 'composer' }, { reduceMotion: true })).toBe(false)
    expect(composerOrbAnimated({ input: 'composer' }, { reduceMotion: false })).toBe(true)
  })
  test('orbTempo：静帧 > 行车 ×0.5 > 全速', () => {
    expect(orbTempo({ driving: true }, { reduceMotion: true })).toBe('static')
    expect(orbTempo({ driving: true }, { reduceMotion: false })).toBe('slow')
    expect(orbTempo({ driving: false }, { reduceMotion: false })).toBe('loop')
  })
  test('edgeGlowActive：只在 listening / thinking（B2 出账⑩：判据出 JSX）', () => {
    expect(edgeGlowActive({ primary: 'listening' })).toBe(true)
    expect(edgeGlowActive({ primary: 'thinking' })).toBe(true)
    for (const s of ['idle', 'speaking', 'armed', 'attention', 'looking', 'muted'] as const) {
      expect(edgeGlowActive({ primary: s })).toBe(false)
    }
  })
  test('loopsAnimated（EdgeGlow 呼吸 / ThinkDots / StreamCursor）只看 reduce-motion', () => {
    expect(loopsAnimated({ reduceMotion: false })).toBe(true)
    expect(loopsAnimated({ reduceMotion: true })).toBe(false)
  })
})
```

`mobile/test/settingsMeta.test.ts` 追加 1 条：`旧库没有 reduceMotionForce → false`（同 T2 的写法）。

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/orbPolicy.test.ts test/settingsMeta.test.ts`
Expected: FAIL（`orbTempo` 不存在 / 第二个参数不存在 / 键缺失）

- [ ] **步骤 3：实现**

`mobile/src/core/presence/orbPolicy.ts`（整文件替换）：

```ts
// 动效策略——唯一判据（B3-1 立「Composer 球动不动」，B4-3 扩成三档）。
// 三个利益方在这里一处裁：G5 性能（层开时主球让出那「1 个」循环动画名额）/ §8 reduce-motion
// （循环全部降静帧 + 单次过渡，十条不变量⑩「降级脚本化」）/ §6 行车（×0.5 频率、×0.6 透明度，A-1 §10）。
// AuroraOrb / EdgeGlow / ThinkDots / StreamCursor 只消费，各自不再判。
// ⚠ 它只回答「动不动、多快」；「静止时 speaking 还读不读得出」仍是 AuroraOrb 静态标记的事（B3-1），
//   两个判据刻意分开——合成一个就会回到「要么动（破 G5）要么盲（S4）」的二选一。
import type { PresenceSnapshot } from './presence'

export interface MotionEnv {
  /** 系统「减少动效」（AccessibilityInfo.isReduceMotionEnabled）∨ 实验室强制开关（settings.reduceMotionForce） */
  reduceMotion: boolean
}

export const FULL_MOTION: MotionEnv = { reduceMotion: false }

export type OrbTempo = 'loop' | 'slow' | 'static'

/** 光球节律：reduce-motion ⇒ 静帧；行车 ⇒ ×0.5 频率（HMI AuroraOrb.tsx:30 的 dm=2）；其余全速 */
export function orbTempo(s: Pick<PresenceSnapshot, 'driving'>, env: MotionEnv): OrbTempo {
  if (env.reduceMotion) return 'static'
  return s.driving ? 'slow' : 'loop'
}

export function composerOrbAnimated(s: Pick<PresenceSnapshot, 'input'>, env: MotionEnv = FULL_MOTION): boolean {
  if (env.reduceMotion) return false
  // 层开着：层内大球（VoiceSheet）接管那「1 个」循环动画
  return s.input !== 'voice-sheet'
}

/** 语音层顶缘极光只在听 / 想（方案 §5.2 规则 6）。此前内联在 VoiceSheet.tsx 的 JSX 里、零 jest（B2 出账⑩） */
export function edgeGlowActive(s: Pick<PresenceSnapshot, 'primary'>): boolean {
  return s.primary === 'listening' || s.primary === 'thinking'
}

/** 循环类小动效（EdgeGlow 呼吸 / ThinkDots / StreamCursor）要不要动：只看 reduce-motion */
export function loopsAnimated(env: MotionEnv): boolean {
  return !env.reduceMotion
}
```

`mobile/src/core/a11y/reduceMotion.ts`：

```ts
// mobile/src/core/a11y/reduceMotion.ts
// 「减少动效」事实源（B4-3 / 方案 §8）：系统开关（AccessibilityInfo；Android「移除动画」=
// animator_duration_scale=0）∨ 实验室强制开关（settings.reduceMotionForce，取证装置兼用户入口）。
// 判据在 core/presence/orbPolicy.ts；这里只收集。系统值异步到达：首帧按 false，到达后重渲一次。
import { useEffect, useState } from 'react'
import { AccessibilityInfo } from 'react-native'
import { useStore } from 'zustand'

import { settingsStore } from '../settings/store'

export function useReduceMotion(): boolean {
  const { settings } = useStore(settingsStore)
  const [system, setSystem] = useState(false)
  useEffect(() => {
    let alive = true
    AccessibilityInfo.isReduceMotionEnabled()
      .then((v) => {
        if (alive) setSystem(v)
      })
      .catch(() => {})
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setSystem)
    return () => {
      alive = false
      sub.remove()
    }
  }, [])
  return system || settings.reduceMotionForce
}
```

`mobile/src/core/settings/store.ts`：`reduceMotionForce: boolean`（默认 false；注释「实验室：强制减少动效——系统开关的 App 内等价物，判据取或（core/a11y/reduceMotion.ts）」）。设置页的开关行在 T8 一起加（实验室分区那一批 UI 改动集中一次）。

`mobile/src/ui/aurora/AuroraOrb.tsx`：
- props 加 `driving = false`（注释：`/** 行车档（§6 / A-1 §10）：四组时长 ×2（频率 ×0.5）+ 整体 0.6 透明度——逐值照 HMI AuroraOrb.tsx:30,50 */`）；
- `const dm = driving ? 2 : 1`，四个 `*Dur` 各乘 `* dm`；
- 根 View `opacity: dim || driving ? 0.6 : 1`；
- `Ripple` 加 `dm` prop：`duration: (0.9 + i * 0.28) * 1000 * dm`，调用处 `<Ripple key={i} size={size} i={i} dm={dm} />`；
- `useEffect` 依赖数组不用改（`*Dur` 已在里面）。Shutter 一次性 300ms **不乘**——它是「单次过渡」，reduce-motion 也允许。

`EdgeGlow.tsx`：props 加 `animated = true`；effect 开头：
```ts
    if (!animated) {
      t.value = withTiming(active ? 0.6 : 0, { duration: 0 }) // 定格：常亮 0.6 或熄灭，零循环
      return
    }
```
依赖数组加 `animated`。`ThinkDots.tsx`：`ThinkDots({ color, animated = true })` → `Dot` 加 `animated`，effect 里 `if (!animated) { t.value = 0.5; return }`。`StreamCursor.tsx`：`StreamCursor({ h = 16, animated = true })`，effect 里 `if (!animated) { on.value = 1; return }`（依赖数组各自补上）。

消费接线（判据一处、各处只传布尔）：
- `ChatScreen.tsx`：`import { useReduceMotion } from '../../core/a11y/reduceMotion'`、`import { composerOrbAnimated, edgeGlowActive, loopsAnimated, orbTempo } from '../../core/presence/orbPolicy'`；ChatBody 里 `const reduceMotion = useReduceMotion(); const motionEnv = { reduceMotion }`；Composer 传 `orbAnimated={composerOrbAnimated(snapshot, motionEnv)}` 与新 prop `orbDriving={orbTempo(snapshot, motionEnv) === 'slow'}`；Welcome 的 88 球 `animated={loopsAnimated(motionEnv)}`（Welcome 加 `animated` prop）；顶栏 30 球 `animated={busy && loopsAnimated(motionEnv)}`；VoiceSheet 传 `motion={{ orb: orbTempo(snapshot, motionEnv), loops: loopsAnimated(motionEnv) }}`；MessageBubble 传 `loops={loopsAnimated(motionEnv)}`，FlashList 的 `extraData` 数组加 `reduceMotion`。
- `Composer.tsx`：props 加 `orbDriving?: boolean` → `<AuroraOrb … driving={orbDriving} />`。
- `VoiceSheet.tsx`：props 加 `motion: { orb: OrbTempo; loops: boolean }`；大球 `animated={motion.orb !== 'static'} driving={motion.orb === 'slow'}`；`<EdgeGlow active={edgeGlowActive(snapshot)} animated={motion.loops} />`（原 `:150` 的内联表达式删掉）；`<ThinkDots … animated={motion.loops} />`、两处 `<StreamCursor … animated={motion.loops} />`。
- `MessageBubble.tsx`：`BubbleProps` 加 `loops: boolean`；头像球 `animated={active && loops}`。
- `state-gallery.tsx`：`const reduceMotion = useReduceMotion()`，样本球 `animated={!reduceMotion}`。

- [ ] **步骤 4：跑绿 + tsc + 全量**

Run: `cd mobile && npx jest test/orbPolicy.test.ts test/settingsMeta.test.ts && npm run typecheck && npm test`
Expected: 新增 5 条全绿；0 error；全量 444 → 449。⚠ `composerOrbAnimated` 的既有 2 条用例不改也过（第二参有默认值）。

- [ ] **步骤 5：反向验证**

① `orbTempo` 去掉 `reduceMotion` 分支 ⇒ **恰好红**「静帧 > 行车」第一个断言；② `edgeGlowActive` 把 `thinking` 换成 `speaking` ⇒ 恰好红 edgeGlow 那条（且正例 thinking 一侧红、反例 speaking 一侧也红——两侧都红才是这条判据的形状）；③ `composerOrbAnimated` 忽略 env ⇒ 恰好红「reduce-motion 压过一切」。真机侧的「静帧」读数在 T13 步骤 6（两帧逐字节 diff）。

- [ ] **步骤 6：Metro 热载冒烟**：实验室开关暂时没有 UI（T8 才加）——用 `/presence-trail` 之外的法子验：临时把 `DEFAULT_APP_SETTINGS.reduceMotionForce` 改 true 热载一次，看 Composer 球、顶栏球、欢迎球全部静止且**仍显示正确的态**（S4 的静态双青环这时也在），改回 false 再热载一次球又动了；**改回来后 `git diff -- mobile/src/core/settings/store.ts` 只剩本任务的新键那几行**。

- [ ] **提交**

```bash
git add -- mobile/src/core/a11y/reduceMotion.ts && git commit -m "feat(mobile): UX v2 B4-3 动效策略——orbPolicy 扩 orbTempo/edgeGlowActive/loopsAnimated（reduce-motion 静帧 > 行车 ×0.5 > 全速，一处裁）+ useReduceMotion 事实源（系统∨实验室强制）+ 光球 driving ×2 时长 0.6 透明 + EdgeGlow/ThinkDots/StreamCursor 可定格" -- mobile/src/core/a11y/reduceMotion.ts mobile/src/core/presence/orbPolicy.ts mobile/test/orbPolicy.test.ts mobile/src/ui/aurora/AuroraOrb.tsx mobile/src/ui/aurora/EdgeGlow.tsx mobile/src/ui/aurora/ThinkDots.tsx mobile/src/ui/aurora/StreamCursor.tsx mobile/src/core/settings/store.ts mobile/test/settingsMeta.test.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/Composer.tsx mobile/src/features/chat/VoiceSheet.tsx mobile/src/features/chat/MessageBubble.tsx mobile/src/app/state-gallery.tsx && git show --stat HEAD
```

---

### Task 4: 提示音——`soundCue` 转移判据 + `OscillatorNode` 合成薄壳 + 设置开关 + `usePresence` effect（方案 §8 提示音、§4.2 声音列）

**Files:**
- 新建 `mobile/src/core/presence/soundCue.ts`、`mobile/src/core/voice/cueTone.ts`、`mobile/test/soundCue.test.ts`
- 修改 `mobile/src/core/settings/store.ts`（`cueToneEnabled`）、`mobile/test/settingsMeta.test.ts`（+1）、`mobile/src/features/settings/SettingsScreen.tsx`（开关行）、`mobile/src/features/chat/usePresence.ts`（effect）、`mobile/src/app/native-spike.tsx`（两个试听按钮）

**为什么**：唤醒确认音是 M4 挂账（`handsFree.ts` 头注最后一句：mp3 解码因 FFmpeg 关闭不可用）。方案 §8 定的解法**不用音频文件**：`react-native-audio-api` 是 Web Audio 实现，`OscillatorNode + GainNode` 合成两音上行（C5→G5，各 60ms，gain 0.15），零资源、零重建，走 `sharedAudioContext()`（`audioCtx.ts:105`——全局唯一、懒建、建后 resume；再建一个就是第二份设备音频资源）。「什么时候响」与触感同形：`PresenceSnapshot` 转移的纯函数（`soundCueForTransition`），只在 `listening`（**唤醒**——§4.2 写明 PTT 只有按下触感，所以判据要看 `hfFsm` ARMED→LISTENING，纯 `primary` 分不出 PTT 与唤醒）与 `attention` 进入时响；`speaking` 首音**不**响（与 TTS 叠）。执行挂 `usePresence` 的 **useEffect**（B3-5 的理由原样：不幂等，渲染期双渲会双响）。设置「提示音」默认开、**行车档强制开**（Q6）——强制这一半也是纯函数 `cueToneAllowed(enabled, driving)`。attention 的音型方案没定，本计划取两音**下行** G5→E5（「问你一句」的语气，与唤醒的上行成对）——§5 第 5 条，泓舟一句话可改。

- [ ] **步骤 1：写失败测试**

`mobile/test/soundCue.test.ts`：

```ts
// mobile/test/soundCue.test.ts
// 提示音的转移判据（方案 §8 / §4.2 声音列）：唤醒两音上行只给唤醒（ARMED→LISTENING），PTT 按下不响；
// attention 进入响一次；speaking 首音不响（与 TTS 叠）。行车档强制开（Q6）。
import { cueToneAllowed, soundCueForTransition } from '@/core/presence/soundCue'
import type { OrbState } from '@/core/presence/presence'

const s = (primary: OrbState, hfFsm = 'IDLE') => ({ primary, hfFsm })

test('唤醒确认音：免唤醒 ARMED→LISTENING 响一次；持续 LISTENING 不再响', () => {
  expect(soundCueForTransition(s('armed', 'ARMED'), s('listening', 'LISTENING'))).toBe('wake')
  expect(soundCueForTransition(s('listening', 'LISTENING'), s('listening', 'LISTENING'))).toBeNull()
})

test('PTT 按下进 listening 不响（§4.2：PTT 只有按下触感）', () => {
  expect(soundCueForTransition(s('idle', 'IDLE'), s('listening', 'IDLE'))).toBeNull()
})

test('追问窗里开口（FOLLOWUP→LISTENING）不响——没有唤醒词，两音只会打断人', () => {
  expect(soundCueForTransition(s('listening', 'FOLLOWUP'), s('listening', 'LISTENING'))).toBeNull()
})

test('attention 进入响一次；持续不响', () => {
  expect(soundCueForTransition(s('idle'), s('attention'))).toBe('attention')
  expect(soundCueForTransition(s('attention'), s('attention'))).toBeNull()
})

test('speaking 首音不响；其余转移不响', () => {
  expect(soundCueForTransition(s('thinking'), s('speaking'))).toBeNull()
  expect(soundCueForTransition(s('idle'), s('thinking'))).toBeNull()
  expect(soundCueForTransition(s('idle'), s('looking'))).toBeNull()
})

test('cueToneAllowed：设置关就不响，除非行车档（§8「行车档强制开」）', () => {
  expect(cueToneAllowed(true, false)).toBe(true)
  expect(cueToneAllowed(false, false)).toBe(false)
  expect(cueToneAllowed(false, true)).toBe(true)
})
```

`mobile/test/settingsMeta.test.ts` 追加 1 条：`旧库没有 cueToneEnabled → true`。

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/soundCue.test.ts test/settingsMeta.test.ts`
Expected: FAIL（模块不存在 / 键缺失）

- [ ] **步骤 3：实现**

写 `cueTone.ts` 前先核三处 API 在装着的 0.13.3 上真的存在（都核过一次，写时再看一眼）：`lib/typescript/core/BaseAudioContext.d.ts:36,39` 的 `createOscillator() / createGain()`、`core/AudioParam.d.ts:12-13` 的 `setValueAtTime / linearRampToValueAtTime`、`OscillatorNode.d.ts:7` 的 `frequency: AudioParam`。**不设 `osc.type`**（默认 sine，规范如此；少碰一个不确定的 setter）。

`mobile/src/core/presence/soundCue.ts`：

```ts
// mobile/src/core/presence/soundCue.ts
// 提示音的转移判据（B4-4 / 方案 §8）——纯函数、零 RN/audio import。与 hapticCue 同形态，但切片多带 hfFsm：
// 唤醒确认音只给**唤醒**（免唤醒 FSM ARMED→LISTENING，含轻点光球的手动唤醒），PTT 按下进 listening 不响
// （§4.2：PTT 只有按下触感）；追问窗里开口（FOLLOWUP→LISTENING）也不响——没有唤醒词，两音只会打断人。
// attention 进入响一次；speaking 首音**不**响（与 TTS 叠）。执行层在 core/voice/cueTone.ts；接线在 usePresence 的 useEffect。
import type { OrbState } from './presence'

export type SoundCue = 'wake' | 'attention'

export interface CueSlice {
  primary: OrbState
  /** 免唤醒 FSM 态（PresenceInput.hfFsm）；PTT 世界里恒 IDLE */
  hfFsm: string
}

export function soundCueForTransition(prev: CueSlice, next: CueSlice): SoundCue | null {
  if (next.primary === 'listening' && next.hfFsm === 'LISTENING' && prev.hfFsm === 'ARMED') return 'wake'
  if (next.primary === 'attention' && prev.primary !== 'attention') return 'attention'
  return null
}

/** 设置「提示音」默认开；行车档强制开（方案 §8 / Q6） */
export function cueToneAllowed(enabled: boolean, driving: boolean): boolean {
  return enabled || driving
}
```

`mobile/src/core/voice/cueTone.ts`：

```ts
// mobile/src/core/voice/cueTone.ts
// 提示音执行层（B4-4 / 方案 §8）：OscillatorNode + GainNode 合成——零资源、零解码（FFmpeg 关着，mp3 不可用，
// handsFree.ts 头注的 M4 挂账就是这条）。走 sharedAudioContext()：与 pcmPlayer 同一个上下文，不另占音频设备。
// 判据不在这里（core/presence/soundCue.ts）；fire-and-forget + 静默失败：提示音缺席不值得打断任何主流程。
import { sharedAudioContext } from './audioCtx'

import type { SoundCue } from '../presence/soundCue'

/** 两音上行 C5→G5（唤醒确认，方案 §8 原文）；attention 两音下行 G5→E5（方案未定音型，B4 计划 §5 第 5 条） */
const TONES: Record<SoundCue, readonly number[]> = {
  wake: [523.25, 783.99],
  attention: [783.99, 659.25],
}
export const CUE_TONE_MS = 60
export const CUE_GAIN = 0.15
/** 起落沿（秒）：没有它每个音头尾都是一声咔嗒 */
const RAMP_S = 0.008

export function playCueTone(kind: SoundCue): void {
  try {
    const ctx = sharedAudioContext()
    let at = ctx.currentTime + 0.01
    for (const freq of TONES[kind]) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.frequency.value = freq
      const end = at + CUE_TONE_MS / 1000
      gain.gain.setValueAtTime(0, at)
      gain.gain.linearRampToValueAtTime(CUE_GAIN, at + RAMP_S)
      gain.gain.setValueAtTime(CUE_GAIN, end - RAMP_S)
      gain.gain.linearRampToValueAtTime(0, end)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(at)
      osc.stop(end)
      at = end
    }
  } catch {
    /* 音频上下文不可用（设备占用 / 原生缺席）：不响就不响 */
  }
}
```

`mobile/src/core/settings/store.ts`：`cueToneEnabled: boolean`（默认 **true**；注释「提示音（方案 §8 / Q6）：唤醒确认两音 + 需要确认时一次。默认开；行车档强制开——判据 core/presence/soundCue.ts::cueToneAllowed」）。

`mobile/src/features/settings/SettingsScreen.tsx`——语音播报分区「触感」那行**之后**加：

```tsx
        <SwitchRow
          p={p}
          label="提示音"
          desc="唤醒命中、需要你确认时的两音提示（合成，不用音频文件）。默认开；行车档强制开"
          value={settings.cueToneEnabled}
          onChange={(cueToneEnabled) => set({ cueToneEnabled })}
        />
```

`mobile/src/features/chat/usePresence.ts`——触感 effect 之后加（`input` 与 `snapshot` 都在作用域内）：

```tsx
  // B4-4 提示音：与触感同一形态（判据纯函数 + effect 执行，不挂渲染期）。切片多带 hfFsm——
  // 唤醒确认音只给唤醒（ARMED→LISTENING），PTT 按下进 listening 不响（§4.2）。行车档强制开（Q6）
  const cuePrev = useRef<CueSlice | null>(null)
  useEffect(() => {
    const prev = cuePrev.current
    const cur: CueSlice = { primary: snapshot.primary, hfFsm: input.hfFsm }
    cuePrev.current = cur
    if (!prev || !cueToneAllowed(settings.cueToneEnabled, snapshot.driving)) return
    const kind = soundCueForTransition(prev, cur)
    if (kind) playCueTone(kind)
  })
```

（import：`import { cueToneAllowed, soundCueForTransition, type CueSlice } from '@/core/presence/soundCue'`、`import { playCueTone } from '@/core/voice/cueTone'`。）

`mobile/src/app/native-spike.tsx`——触感四按钮之后加两个按钮（T13 盲测与单人「不崩」冒烟的装置）：

```tsx
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '600', marginTop: 16 }}>提示音两种（§8，B4）</Text>
      {(['wake', 'attention'] as const).map((kind) => (
        <Pressable
          key={kind}
          testID={`cue-${kind}`}
          accessibilityRole="button"
          onPress={() => playCueTone(kind)}
          style={{ backgroundColor: p.fill, borderRadius: 12, padding: 12, minHeight: 44, justifyContent: 'center' }}
        >
          <Text style={{ color: p.fg1, fontSize: p.font(14) }}>{kind}（{kind === 'wake' ? '唤醒 · 两音上行' : '需确认 · 两音下行'}）</Text>
        </Pressable>
      ))}
```

- [ ] **步骤 4：跑绿 + tsc + 全量**

Run: `cd mobile && npx jest test/soundCue.test.ts test/settingsMeta.test.ts && npm run typecheck && npm test`
Expected: 新增 7 条全绿（soundCue 6 + settingsMeta 1）；0 error；全量 449 → 456。

- [ ] **步骤 5：反向验证**

① `wake` 分支去掉 `prev.hfFsm === 'ARMED'` ⇒ **恰好红**「PTT 按下不响」与「追问窗不响」两条（都是同一守卫在挡）；② `attention` 分支去掉 `prev.primary !== 'attention'` ⇒ 恰好红「持续不响」那半；③ `cueToneAllowed` 改成 `enabled && …` ⇒ 恰好红最后一条的第三个断言。

- [ ] **步骤 6：Metro 热载冒烟（单人能做的那半）**：`/native-spike` 两个按钮各按一次——**不崩、logcat 本应用 pid 零 E/F 行**，`adb shell dumpsys audio` 里 `AudioPlaybackConfiguration … uid:10423 state:started` 至少出现一次（60ms×2 太短未必抓得到——抓不到只记「未观测」，不写「不出声」）。**「响不响、两种分不分得出」是人耳读数，归 T13 步骤 2（泓舟）。** 扬声器音量先按 B3 坑⑨ 读一次 `STREAM_MUSIC Current`（音量 0 是「不出声」惯犯第三次）。

- [ ] **提交**

```bash
git add -- mobile/src/core/presence/soundCue.ts mobile/src/core/voice/cueTone.ts mobile/test/soundCue.test.ts && git commit -m "feat(mobile): UX v2 B4-4 提示音——ARMED→LISTENING 两音上行 / attention 两音下行的转移判据（PTT 不响、speaking 首音不响）+ OscillatorNode 合成薄壳（同一 AudioContext，零资源）+ 设置默认开行车强制开 + usePresence effect" -- mobile/src/core/presence/soundCue.ts mobile/src/core/voice/cueTone.ts mobile/test/soundCue.test.ts mobile/src/core/settings/store.ts mobile/test/settingsMeta.test.ts mobile/src/features/settings/SettingsScreen.tsx mobile/src/features/chat/usePresence.ts mobile/src/app/native-spike.tsx && git show --stat HEAD
```

---

### Task 5: 舞台场景判据 + 通用列表兜底卡（方案 §7.2「舞台=卡的大视图」、B2 出账⑨ `charging_list`）

**Files:**
- 新建 `mobile/src/core/stage/stageScene.ts`、`mobile/test/stageScene.test.ts`、`mobile/src/core/cards/cardFields.ts`、`mobile/test/cardFields.test.ts`
- 修改 `mobile/src/features/cards/CardRenderer.tsx`（`FallbackCard` 改读 `cardFields`，渲 `items[]`）

**为什么**：§7.2 双栏的右舞台是「卡的大视图」，场景判定「同 HMI `ContextualStage`：地图族→地图、天气→天气、提醒→日程、其余→焦点卡」。HMI 的 `deriveScene` 住在 `hmi/src/components/ContextualStage.tsx`（.tsx 组件，不是共享模块，`hmi/` 不碰）⇒ mobile 这份是**视图选择的第二份实现**，明说不藏，用测试从 hmi 源码把 `MAP_TYPES = [...]` 字面读出来逐字对账（`cards.test.ts` 从 `types.ts` 派生卡型清单是同一手法）——漂了当场红，而不是「两边各一份、错得一致时毫无异常」。与 HMI 的一处刻意差异：mobile **最近一张卡永远进舞台**（非场景型 → `focus`，这是 M3-2 右舞台「焦点卡」段的既有语义），HMI 会跳过非场景卡去找更早的天气卡；`card_group` 的主卡用 `splitCardGroup`（`display_priority` 判据已在 `core/cards/cardGroup.ts`，不另判）。

`charging_list`（B2 出账⑨）**mobile 不能单独注册**：它不在 `hmi/src/types.ts::UiCard` 联合里，`cards.test.ts` 两个方向断言 ⇒ 注册即红。产出方形状核过（`agents/charging_planner/src/agent.py:162-171`、`low_battery.py:100-104`）：`items[{id,name,available,total,price,distance_km,operator}]` + `soc`。落点是把兜底卡从「四个主字段」升级成**通用列表兜底**（`items[]` 里的对象行：名字 + 距离 + 空闲），任何未登记的列表型卡都受益；`charging_list` 进 types.ts + HMI 渲染器、以及它不带 `_prov`，是 hmi 与 agent 侧的账（§6.4 出账）。字段探取提成纯函数 `cardFields.ts`，T11 的行车压缩卡也读它。

- [ ] **步骤 1：写失败测试**

`mobile/test/stageScene.test.ts`：

```ts
// mobile/test/stageScene.test.ts
// 舞台场景（方案 §7.2）：映射表与 hmi ContextualStage.tsx 逐字对账（从源码读，hmi 不碰）。
import * as fs from 'fs'
import * as path from 'path'

import { STAGE_MAP_TYPES, stageScene } from '@/core/stage/stageScene'
import type { Msg } from '@shared/types.ts'

/* eslint-disable @typescript-eslint/no-explicit-any */
const msg = (uiCard?: any, role: 'user' | 'assistant' = 'assistant'): Msg =>
  ({ id: Math.random().toString(36).slice(2), role, text: '', uiCard }) as Msg

test('对账：STAGE_MAP_TYPES 与 hmi ContextualStage.tsx::MAP_TYPES 逐字一致；日程族两型名在其判定里', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'hmi', 'src', 'components', 'ContextualStage.tsx'), 'utf8')
  const m = /const MAP_TYPES = \[([^\]]+)\]/.exec(src)
  if (!m) throw new Error('hmi ContextualStage.tsx 里找不到 MAP_TYPES——结构变了，先看它再改这条守卫')
  const hmi = m[1].split(',').map((s) => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean)
  expect(hmi.length).toBeGreaterThan(2) // 结构性自检：解析成个位数说明正则失配
  expect([...STAGE_MAP_TYPES]).toEqual(hmi)
  for (const t of ['reminder_list', 'reminder_card']) expect(src.includes(`'${t}'`)).toBe(true)
})

test('无卡 → idle（用户气泡带卡也不算）', () => {
  expect(stageScene([msg(), msg({ type: 'weather' }, 'user')])).toEqual({ kind: 'idle' })
})

test('最近一张卡决定场景：天气 / 地图族 / 日程族 / 其余焦点', () => {
  expect(stageScene([msg({ type: 'weather' })]).kind).toBe('weather')
  expect(stageScene([msg({ type: 'weather' }), msg({ type: 'route_plan' })]).kind).toBe('map')
  expect(stageScene([msg({ type: 'reminder_list' })]).kind).toBe('agenda')
  expect(stageScene([msg({ type: 'stock_quote' })]).kind).toBe('focus')
})

test('card_group：主卡（display_priority 升序首张，判据在 cardGroup.ts）决定场景', () => {
  const s = stageScene([msg({ type: 'card_group', items: [{ type: 'poi_list', display_priority: 2 }, { type: 'weather', display_priority: 0 }] })])
  expect(s.kind).toBe('weather')
})
```

`mobile/test/cardFields.test.ts`：

```ts
// mobile/test/cardFields.test.ts
// 兜底卡 / 行车压缩卡共用的字段探取。charging_list 的形状照产出方源码抄（agent.py:162-171）。
import { cardListRows, cardPrimaryButton, cardPrimaryFields } from '@/core/cards/cardFields'

const chargingList = {
  type: 'charging_list',
  soc: '62%',
  items: [
    { id: 's1', name: '特来电充电站', available: 3, total: 8, price: 1.2, distance_km: 0.8, operator: '特来电' },
    { id: 's2', name: '星星充电', available: 0, total: 4, price: 1.5, distance_km: 2.1, operator: '星星' },
  ],
  buttons: [{ label: '导航去第一个', send_text: '导航去第一个' }],
}

test('charging_list：列表行 = 名字 + 距离 + 空闲；主字段只剩 soc', () => {
  const rows = cardListRows(chargingList)
  expect(rows).toHaveLength(2)
  expect(rows[0]).toEqual({ title: '特来电充电站', sub: '0.8km · 3/8 空闲' })
  expect(rows[1].sub).toBe('2.1km · 0/4 空闲')
  expect(cardPrimaryFields(chargingList)).toEqual([['soc', '62%']])
})

test('items 最多 5 行；非对象项与无名字项跳过', () => {
  const many = { items: [...Array.from({ length: 8 }, (_, i) => ({ name: `s${i}` })), 'junk', { foo: 1 }] }
  expect(cardListRows(many)).toHaveLength(5)
})

test('无 items 的卡拿不出行；主按钮取第一个可用的', () => {
  expect(cardListRows({ type: 'weather', city: '深圳' })).toEqual([])
  expect(cardPrimaryButton(chargingList)).toEqual({ label: '导航去第一个', send_text: '导航去第一个' })
  expect(cardPrimaryButton({ buttons: [{ label: 'x' }] })).toBeNull()
})
```

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/stageScene.test.ts test/cardFields.test.ts`
Expected: FAIL（模块不存在）

- [ ] **步骤 3：实现**

`mobile/src/core/stage/stageScene.ts`：

```ts
// mobile/src/core/stage/stageScene.ts
// 舞台场景选择（B4-5 / 方案 §7.2「舞台=卡的大视图」）：最近一张助手卡决定右舞台放什么——
// 日程族→日程、天气→天气、地图族→地图（卡自带「地图」入口）、其余→焦点卡、无卡→待机（车况三格）。
// 映射表与 hmi/src/components/ContextualStage.tsx::deriveScene 同一张；它住在 .tsx 组件里、不是共享模块
// （hmi/ 不碰），所以这里是**视图选择**的第二份实现——test/stageScene.test.ts 从 hmi 源码把 MAP_TYPES
// 字面读出来逐字比对，漂了当场红。与 HMI 的一处刻意差异：最近一张卡永远进舞台（非场景型 → focus），
// HMI 会跳过非场景卡去找更早的；M3-2 右舞台「焦点卡」段本来就是这个语义。纯函数、零 RN import。
import type { Msg, UiCard } from '@shared/types.ts'

import { splitCardGroup } from '../cards/cardGroup'

export const STAGE_MAP_TYPES = ['poi_list', 'poi_detail', 'route_plan', 'charging_route', 'trip_itinerary'] as const
export const STAGE_AGENDA_TYPES = ['reminder_list', 'reminder_card'] as const

export type StageScene = { kind: 'idle' } | { kind: 'weather' | 'map' | 'agenda' | 'focus'; card: UiCard }

/* eslint-disable @typescript-eslint/no-explicit-any */
function mainCard(card: any): UiCard | null {
  if (!card || typeof card !== 'object') return null
  if (card.type === 'card_group') return (splitCardGroup(card.items || []).main as UiCard | null) ?? null
  return card as UiCard
}

export function stageScene(messages: readonly Msg[]): StageScene {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.role !== 'assistant') continue
    const card = mainCard(m.uiCard)
    if (!card) continue
    const t = card.type as string
    if ((STAGE_AGENDA_TYPES as readonly string[]).includes(t)) return { kind: 'agenda', card }
    if (t === 'weather') return { kind: 'weather', card }
    if ((STAGE_MAP_TYPES as readonly string[]).includes(t)) return { kind: 'map', card }
    return { kind: 'focus', card }
  }
  return { kind: 'idle' }
}
```

（`splitCardGroup` 的返回形状以 `core/cards/cardGroup.ts` 为准——`CardGroup.tsx:16` 用的是 `{ main, rest }`；`main` 为 null/undefined 时本函数按无卡处理。）

`mobile/src/core/cards/cardFields.ts`：

```ts
// mobile/src/core/cards/cardFields.ts
// 兜底卡与行车压缩卡共用的字段探取（B4-5）：从任意卡里拿「人能认出这是什么」的主字段、列表行、主按钮。
// 纯函数、零 RN import。PRIMARY_KEYS 从 CardRenderer.tsx 的 FALLBACK_PRIMARY_KEYS 搬来（那里只剩渲染）+ soc。
/* eslint-disable @typescript-eslint/no-explicit-any */
export const PRIMARY_KEYS = [
  'title', 'name', 'question', 'query', 'topic', 'destination', 'answer', 'merchant',
  'brand', 'store_name', 'order_id', 'amount', 'status', 'city', 'soc',
] as const

export function cardPrimaryFields(card: any, max = 4): Array<[string, string]> {
  if (!card || typeof card !== 'object') return []
  const out: Array<[string, string]> = []
  for (const k of PRIMARY_KEYS) {
    const v = card[k]
    if (typeof v === 'string' || typeof v === 'number') out.push([k, String(v)])
    if (out.length >= max) break
  }
  return out
}

export interface CardListRow {
  title: string
  sub: string
}

/** items[] 里的对象行：name/title/label 为主；distance_km / distance、available/total、address 为副 */
export function cardListRows(card: any, max = 5): CardListRow[] {
  const items: unknown[] = Array.isArray(card?.items) ? card.items : []
  const out: CardListRow[] = []
  for (const raw of items) {
    if (!raw || typeof raw !== 'object') continue
    const it = raw as Record<string, unknown>
    const title = String(it.name ?? it.title ?? it.label ?? '').trim()
    if (!title) continue
    const sub: string[] = []
    if (typeof it.distance_km === 'number') sub.push(`${it.distance_km}km`)
    else if (typeof it.distance === 'string' || typeof it.distance === 'number') sub.push(String(it.distance))
    if (typeof it.total === 'number' && it.total > 0) sub.push(`${Number(it.available ?? 0)}/${it.total} 空闲`)
    if (typeof it.address === 'string' && it.address) sub.push(it.address)
    out.push({ title, sub: sub.join(' · ') })
    if (out.length >= max) break
  }
  return out
}

export function cardPrimaryButton(card: any): { label: string; send_text: string } | null {
  const list: any[] = Array.isArray(card?.buttons) ? card.buttons : []
  const b = list.find((x) => x?.label && x?.send_text)
  return b ? { label: String(b.label), send_text: String(b.send_text) } : null
}
```

`mobile/src/features/cards/CardRenderer.tsx`：删掉 `FALLBACK_PRIMARY_KEYS`，`FallbackCard` 改成：

```tsx
/** 兜底卡（铁则）：type 名 + 可识别主字段 + **通用列表行（items[]）** + buttons + _prov，绝不 null。
 *  B4-5：charging_list（types.ts 里没有、mobile 不能注册——cards.test 两向断言）落在这里时
 *  至少要能读出「哪几个站、多远、几个空闲」，而不是一句「暂未适配」。 */
export function FallbackCard({ p, card, onSend }: CardProps) {
  const fields = cardPrimaryFields(card)
  const rows = cardListRows(card)
  return (
    <CardShell p={p} title={`卡片 · ${card?.type || '未知'}`} right={<ProvBadge p={p} prov={card?._prov} />}>
      {fields.map(([k, v]) => (
        <Text key={k} style={{ color: p.fg2, fontSize: p.font(12) }} numberOfLines={2}>
          {k}: {v}
        </Text>
      ))}
      {rows.map((r, i) => (
        <View key={i} testID="fallback-row" style={{ flexDirection: 'row', gap: 8, alignItems: 'baseline' }}>
          <Text style={{ color: p.fg3, fontSize: p.font(12), width: 20 }}>{i + 1}</Text>
          <View style={{ flex: 1 }}>
            <Text style={{ color: p.fg1, fontSize: p.font(13) }} numberOfLines={1}>{r.title}</Text>
            {r.sub ? <Text style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>{r.sub}</Text> : null}
          </View>
        </View>
      ))}
      {!fields.length && !rows.length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>该卡型暂未适配，内容已收到</Text>
      ) : null}
      <CardButtons p={p} onSend={onSend} buttons={card?.buttons} />
    </CardShell>
  )
}
```

（import：`import { cardListRows, cardPrimaryFields } from '../../core/cards/cardFields'`。**不加** `charging_list` 画廊样本——先看 `cards.test.ts` 对 `CARD_FIXTURES` 的断言方向，若它要求样本型 ⊆ 注册表，加样本就红；真栈取证在步骤 6。）

- [ ] **步骤 4：跑绿 + tsc + 全量**

Run: `cd mobile && npx jest test/stageScene.test.ts test/cardFields.test.ts test/cards.test.ts && npm run typecheck && npm test`
Expected: 新增 7 条全绿；`cards.test.ts` 既有断言（兜底卡绝不 null、注册表两向）不变仍绿；0 error；全量 456 → 463。

- [ ] **步骤 5：反向验证**

① `STAGE_MAP_TYPES` 少写 `trip_itinerary` ⇒ **恰好红**对账那条（这就是它存在的理由——hmi 加了型 mobile 没跟也会红）；② `stageScene` 里 agenda 与 weather 两行对调 ⇒ 不红——**这是预期的**（两者互斥，顺序无关），写进 §6.1 别当成测试漏洞；③ `cardListRows` 的 `total > 0` 改 `>= 0` ⇒ 恰好红 charging_list 第一条（`0/0 空闲` 会冒出来）；④ `mainCard` 不走 `splitCardGroup` 直接取 `items[0]` ⇒ 恰好红 card_group 那条。

- [ ] **步骤 6：真栈取证（B2 出账⑨的肯定式）**：Metro 热载，对话页发「附近的充电站」（B2 §6.4 附加⑦实测这句出 `charging_list`）⇒ 兜底卡渲出**站名 + 距离 + 空闲**的行（`b4-05-charging-list.png`，`testID=fallback-row` 数 ≥1）；卡头仍是「卡片 · charging_list」+ 无 `_prov` 徽章（**这两条不是缺陷是出账**：型名与 `_prov` 都是 hmi / agent 侧的账）。设备没在线就记 ⬜、转 T13。

- [ ] **提交**

```bash
git add -- mobile/src/core/stage/stageScene.ts mobile/test/stageScene.test.ts mobile/src/core/cards/cardFields.ts mobile/test/cardFields.test.ts && git commit -m "feat(mobile): UX v2 B4-5 舞台场景判据（与 hmi ContextualStage MAP_TYPES 从源码逐字对账）+ 字段探取纯函数 + 兜底卡渲通用列表行（charging_list 落点；型名与 _prov 归 hmi/agent 侧）" -- mobile/src/core/stage/stageScene.ts mobile/test/stageScene.test.ts mobile/src/core/cards/cardFields.ts mobile/test/cardFields.test.ts mobile/src/features/cards/CardRenderer.tsx && git show --stat HEAD
```

---

### Task 6: 舞台面板 / 舞台抽屉 / 布局切换 + Maestro 07（方案 §7.1 / §7.2 / §7.5、§11.3 流 07）

**Files:**
- 新建 `mobile/src/features/stage/StagePane.tsx`、`mobile/src/features/stage/StageDrawer.tsx`、`mobile/e2e/07-tablet-two-pane.yaml`
- 修改 `mobile/src/features/chat/ChatScreen.tsx`（`tablet` → `useLayout` 五模式；舞台三段抽出）、`mobile/src/app/native-spike.tsx`（layout 三行）、`mobile/test/sizeClass.test.ts`（真机 dp 读实后改数）、`mobile/e2e/README.md`（07 的跑法）

**为什么**：这是 P7 的落地。`ChatScreen.tsx:575-604` 的右舞台（`Glass` + 三段 ScrollView）与 `:551` 的「车辆」入口都挂在 `tablet` 上；B4 把三段抽成 `StagePane`（读 T5 的 `stageScene`），布局由 T1 的 `layoutMode()` 决定：`single` 原样；`drawer`（width medium）右缘 48dp 把手拉出 320dp 舞台、对话区随之压缩；`two-pane`（≥720 或 book）左对话右舞台、舞台宽 `stageWidth()`；`tabletop` 与 `driving-landscape` 两个分支本任务**先占位渲 chatColumn**（T7 / T11 填）。取证判据物是 `StagePane` 标题行 `stage-mode`（「舞台 · 双栏」），Maestro 07 断言它——不数屏数、不靠肉眼。舞台材质停 G1-tint：它压在静态深空底上，真模糊没收益，也避开 §5.11「同屏多个动态 Blur」。

- [ ] **步骤 1：真机 dp 读实（先于一切改数）**

```bash
adb shell cmd device_state print-state          # 期望 CLOSED(0)
adb shell wm size; adb shell wm density         # 外屏 px 与 dpi
adb shell cmd device_state state 3 && sleep 3 && adb shell wm size && adb shell wm density && adb shell cmd device_state state reset
```

dp = px ÷ (dpi ÷ 160)。把 `test/sizeClass.test.ts` 顶部注释与用例里的 411×960 / 847×948 / 809 改成实测值（**只改数、不改判据**；改完 `npx jest test/sizeClass.test.ts` 仍绿——若某格因实测尺寸落到另一种布局（例如内屏实测 <720），那是**判据要重新裁**，停下来写进 §6.2 交泓舟，别改阈值去迁就设备）。

- [ ] **步骤 2：实现 `StagePane` 与 `StageDrawer`**

`mobile/src/features/stage/StagePane.tsx`：

```tsx
// mobile/src/features/stage/StagePane.tsx
// 舞台面板（B4-6 / 方案 §7.2「舞台=卡的大视图」）：ChatScreen.tsx 原右舞台三段（车况 / 提醒 / 焦点卡）抽出，
// 焦点卡一段改读 stageScene（天气 / 路线 / 日程 / 焦点）。它是**已经在会话里的事实的第二个视图**，不向后端取数。
// 材质 G1-tint（Glass）：压在静态 AuroraBackground 上，真模糊没收益，也避开「同屏多个动态 Blur」（§5.11）。
// testID：stage-pane（面板）/ stage-mode（标题行，写当前布局模式——Maestro 07 与形态截图的判据物）。
import { ScrollView, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import type { Msg } from '@shared/types.ts'

import { stageScene } from '@/core/stage/stageScene'
import { CardRenderer } from '@/features/cards/CardRenderer'
import type { SendFn } from '@/features/cards/parts'
import { ReminderSection } from '@/features/vehicle/ReminderSection'
import { VehicleSection } from '@/features/vehicle/VehiclePanel'
import { Glass } from '@/ui/aurora'
import { RADIUS } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export type StageModeLabel = '双栏' | '舞台抽屉' | '桌面'

const SCENE_LABEL: Record<ReturnType<typeof stageScene>['kind'], string> = {
  idle: '焦点卡',
  weather: '天气',
  map: '路线',
  agenda: '日程',
  focus: '焦点卡',
}

export function StagePane({
  p,
  mode,
  messages,
  vehState,
  onSend,
  style,
}: {
  p: Palette
  mode: StageModeLabel
  messages: Msg[]
  vehState: Record<string, unknown>
  onSend: SendFn
  style?: StyleProp<ViewStyle>
}) {
  const scene = stageScene(messages)
  return (
    <Glass p={p} r={RADIUS['2xl']} style={[{ overflow: 'hidden' }, style]}>
      <ScrollView testID="stage-pane" contentContainerStyle={{ padding: 14, gap: 16 }}>
        <Text testID="stage-mode" style={{ color: p.fg3, fontSize: p.font(11) }}>
          舞台 · {mode}
        </Text>
        <VehicleSection p={p} vehState={vehState} />
        {scene.kind !== 'agenda' ? <ReminderSection p={p} messages={messages} /> : null}
        <View style={{ gap: 6 }}>
          <Text style={{ color: p.fg3, fontSize: p.font(12) }}>{SCENE_LABEL[scene.kind]}</Text>
          {scene.kind === 'idle' ? (
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>本轮还没有卡片</Text>
          ) : (
            <CardRenderer p={p} card={scene.card} onSend={onSend} />
          )}
        </View>
      </ScrollView>
    </Glass>
  )
}
```

`mobile/src/features/stage/StageDrawer.tsx`：

```tsx
// mobile/src/features/stage/StageDrawer.tsx
// 舞台抽屉（B4-6 / 方案 §7.2 第二行）：width medium 且 height ≥ medium。右缘 48dp 把手常驻，
// 拉出 320dp 舞台；打开时对话区随之压缩（父级 row 里本组件占宽 48 → 368）。零新依赖：宽度用 reanimated。
// 关闭时面板立即卸载（不做收起动画——抽屉里是 ScrollView，收窄过程中它会重排，不值得）。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated'

import type { Msg } from '@shared/types.ts'

import type { SendFn } from '@/features/cards/parts'
import { DRAWER_HANDLE, DRAWER_WIDTH } from '@/ui/layout/sizeClass'
import { MOTION, TARGET } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

import { StagePane } from './StagePane'

export function StageDrawer({
  p,
  messages,
  vehState,
  onSend,
}: {
  p: Palette
  messages: Msg[]
  vehState: Record<string, unknown>
  onSend: SendFn
}) {
  const [open, setOpen] = useState(false)
  const w = useSharedValue(0)
  const paneStyle = useAnimatedStyle(() => ({ width: w.value }))
  const toggle = () => {
    const next = !open
    setOpen(next)
    w.value = withTiming(next ? DRAWER_WIDTH : 0, { duration: MOTION.base })
  }
  return (
    <View testID="stage-drawer" style={{ flexDirection: 'row', alignSelf: 'stretch' }}>
      <Pressable
        testID="stage-handle"
        accessibilityRole="button"
        accessibilityLabel={open ? '收起舞台' : '打开舞台'}
        accessibilityState={{ expanded: open }}
        onPress={toggle}
        style={{ width: DRAWER_HANDLE, minHeight: TARGET.parked, alignItems: 'center', justifyContent: 'center' }}
      >
        <View style={{ width: 4, height: 36, borderRadius: 2, backgroundColor: p.fill2 }} />
        <Text style={{ color: p.fg3, fontSize: p.font(10), marginTop: 6 }}>{open ? '›' : '‹'}</Text>
      </Pressable>
      <Animated.View style={[{ overflow: 'hidden', marginVertical: 10 }, paneStyle]}>
        {open ? (
          <StagePane
            p={p}
            mode="舞台抽屉"
            messages={messages}
            vehState={vehState}
            onSend={onSend}
            style={{ width: DRAWER_WIDTH, flex: 1, marginRight: 10 }}
          />
        ) : null}
      </Animated.View>
    </View>
  )
}
```

- [ ] **步骤 3：`ChatScreen.tsx` 换判据**

1. `ChatScreen()` 里删掉 `const { width, height } = useWindowDimensions()` 与 `const tablet = …`；`<ChatBody … tablet={tablet} width={width} …/>` 去掉这两个 prop（ChatBody 的 props 类型同步删）。`useWindowDimensions` 若此后无人用则从 import 里去掉。
2. `ChatBody` 里 `usePresence(...)` 之后加：`const layout = useLayout(snapshot.driving)`（import `useLayout` from `'../../ui/layout/useLayout'`、`PANE_GAP` from `'../../ui/layout/sizeClass'`、`StagePane` / `StageDrawer` from `'../stage/…'`）。
3. 顶栏「车辆」入口：`{!tablet ? <TopIconLink …/> : null}` 改成 `{layout.mode !== 'two-pane' && layout.mode !== 'tabletop' ? <TopIconLink … /> : null}`（舞台常驻的两种形态不重复给入口；抽屉与单栏保留）。
4. 主体渲染（原 `{tablet ? (<View style={{flex:1, flexDirection:'row'}}>{chatColumn}<Glass …>…</Glass></View>) : (chatColumn)}` 整段）换成：

```tsx
          {layout.mode === 'two-pane' ? (
            <View style={{ flex: 1, flexDirection: 'row' }}>
              {/* book：左栏宽 = 铰链左缘 − gap/2，铰链落在 gap 正中（§7.3）；flat 双栏：对话 flex、舞台 stageWidth */}
              <View style={layout.posture === 'book' ? { width: layout.book.chat } : { flex: 1 }}>{chatColumn}</View>
              <View style={{ width: layout.posture === 'book' ? layout.book.gap : PANE_GAP }} />
              <StagePane
                p={p}
                mode="双栏"
                messages={messages}
                vehState={vehState}
                onSend={onSend}
                style={[{ marginVertical: 10, marginRight: 10 }, layout.posture === 'book' ? { flex: 1 } : { width: layout.stage }]}
              />
            </View>
          ) : layout.mode === 'drawer' ? (
            <View style={{ flex: 1, flexDirection: 'row' }}>
              <View style={{ flex: 1 }}>{chatColumn}</View>
              <StageDrawer p={p} messages={messages} vehState={vehState} onSend={onSend} />
            </View>
          ) : (
            // single / tabletop（T7 填）/ driving-landscape（T11 填）
            chatColumn
          )}
```

5. 删掉不再用的 `Glass` / `ScrollView` / `VehicleSection` / `ReminderSection` / `latestCard` 相关 import 与 `useMemo`（`latestCard` 只有舞台在用；grep 确认后再删）。
6. `native-spike.tsx` 在 `rows` 里加三行（import `useLayout`、`PixelRatio`、`useWindowDimensions`）：

```ts
    ['layout', `${layout.mode} · ${layout.widthClass}×${layout.heightClass}`],
    ['dp', `${Math.round(width)}×${Math.round(height)} @${PixelRatio.get()}x`],
    ['hinge(dp)', layout.hinge ? JSON.stringify(layout.hinge) : '—'],
```

- [ ] **步骤 4：tsc + 全量**

Run: `cd mobile && npm run typecheck && npm test`
Expected: 0 error；全量零增量（463）——本任务是组件层，判据在 T1/T5；`sizeClass.test.ts` 若步骤 1 改了数仍全绿。

- [ ] **步骤 5：Maestro 07**

`mobile/e2e/07-tablet-two-pane.yaml`：

```yaml
# 流 ⑦：折叠内屏（展开态）→ 双栏：舞台面板在、标题写「舞台 · 双栏」（方案 §11.3）。
# ⚠ Maestro 没有 adb 命令：`cmd device_state state 3`（OPENED）由**外部脚本**先设，跑完 `state reset`
#   （M3-2 验收就是这么强制展开验的，主计划 §8.3；它不等于真平板——R8 那台一直没到位）。
#   展开会让 App 进程落回 DevLauncher（B3 坑：切屏重启进程）——open-app 子流的深链把它拉回来。
# ⚠ 判据物是 testID 与标题文本，不是「屏上看起来有两栏」；tag=manual：CI 设不了 device_state。
appId: com.xiaozhou.companion
name: 07 内屏双栏
tags:
  - manual
---
- runFlow: subflows/open-app.yaml
- assertVisible:
    id: "stage-pane"
- assertVisible: "舞台 · 双栏"
```

跑法（写进 `mobile/e2e/README.md`「B4」一节）：

```bash
adb shell cmd device_state state 3 && adb shell cmd device_state print-state      # 期望 OPENED(3)
D:/Android/tools/maestro-dist/maestro/bin/maestro.bat test --no-reinstall-driver mobile/e2e/07-tablet-two-pane.yaml
adb shell cmd device_state state reset && adb shell cmd device_state print-state  # 回 CLOSED(0)
```

Expected: rc=0；反向验证——把 `layoutMode` 里 `TWO_PANE_MIN_WIDTH` 临时改成 9999 热载再跑 ⇒ **必红**在 `stage-pane`（还原后再跑绿；两次读数都记 §6.2）。

- [ ] **步骤 6：形态取证（前三张 + 分屏 + 回归）**

1. **外屏竖**（`CLOSED(0)`，物理 id `4630947090644569220`）：对话页截图 `b4-06-outer-portrait.png` + `/native-spike` 的 `layout` 行截图（应 `single · compact×expanded`）。
2. **外屏横**：需要转手机——**优先泓舟物理转**；单人要做得改设备系统设置（`settings put system accelerometer_rotation 0` + `user_rotation 1`，命中「修改系统配置」红线，须泓舟授权，做完还原并 `settings get` 回读）。截图 `b4-06-outer-landscape.png`，`layout` 行应 `single · expanded×compact`（非行车）。
3. **内屏**：`cmd device_state state 3` → 深链重连 Metro（坑账 §9.58：等 `Running "main"` 再发业务深链）→ 内屏物理 id `4630946481727302019` 截图 `b4-06-inner-two-pane.png`；`stage-mode` 文本「舞台 · 双栏」在图上；`layout` 行应 `two-pane · expanded×expanded`（或 medium×expanded——取决于步骤 1 的实测密度，**两者都必须仍是 two-pane**）。做完 `state reset`。
4. **抽屉**：真机没有 width medium 的形态（内屏 ≥720 直接双栏；R8 真平板未到位）⇒ 用显示覆盖造一个：内屏态 `adb shell wm size 1900x2100`（按实测密度换算到 600–719dp 宽）→ `layout` 行应 `drawer`、右缘把手可见、点把手舞台拉出 `b4-06-drawer.png`；**必做** `adb shell wm size reset` 并 `wm size` 回读无 override 行。这是显示覆盖不是持久设置，但仍属改设备状态——写进 §6.2 并还原。
5. **分屏不崩不遮（§7.5）**：需泓舟把 App 拖成 MIUI 分屏（上下与左右各一次）⇒ App 不崩、`layout` 行 `single`、Composer 发送键在树里（Maestro 08 在分屏态跑一次 rc=0）。泓舟不在则记 ⬜。
6. **回归**：Maestro 06 / 08 / 09 各一次 rc=0（09 走 `xiaozhou://state-gallery` 与布局无关，是「改了 ChatScreen 没把别的弄坏」的廉价保险）。

- [ ] **提交**

```bash
git add -- mobile/src/features/stage/StagePane.tsx mobile/src/features/stage/StageDrawer.tsx mobile/e2e/07-tablet-two-pane.yaml && git commit -m "feat(mobile): UX v2 B4-6 形态落地——tablet 布尔换 useLayout 五模式（单栏/舞台抽屉/双栏/占位）、舞台三段抽成 StagePane（场景读 stageScene，G1-tint）、抽屉把手 48dp、Maestro 07 内屏双栏、native-spike 加 layout 读数" -- mobile/src/features/stage/StagePane.tsx mobile/src/features/stage/StageDrawer.tsx mobile/e2e/07-tablet-two-pane.yaml mobile/src/features/chat/ChatScreen.tsx mobile/src/app/native-spike.tsx mobile/test/sizeClass.test.ts mobile/e2e/README.md && git show --stat HEAD
```

---

### Task 7: 折叠姿态消费——book 铰链落 gap / tabletop 上下半 / 外内屏接续（PTT 松手）/ 返回手势顺序（方案 §7.3 / §7.4 / §7.5）

**Files:**
- 修改 `mobile/src/features/chat/ChatScreen.tsx`（tabletop 分支、切屏 effect、`BackHandler`）、`mobile/src/features/stage/StagePane.tsx`（桌面模式的大光球）

**为什么**：B3 只交付了姿态**事实**（`FoldEvent` + `foldPosture`，真机四格：tabletop 的 `bounds` 是整宽零高的水平线、book 是整高零宽的垂直线、外屏 `state: none`）；布局消费是 B4 的账。book：铰链在两栏 gap 正中（T1 的 `bookSplit`，T6 的 two-pane 分支已按它取宽，本任务只验）；tabletop：上半 = 舞台 + 大光球、下半 = 转写 + Composer，分界 = 铰链上缘——`FoldingFeature.bounds` 是**窗口坐标**，内容区自己的窗口位置要用 `measureInWindow` 量，不能用 `onLayout` 的相对 y。§7.4：形态切换是 configuration change，状态全在 store（B1 已成立），语音层不收起只重排（VoiceSheet 的 `mounted` 只随 `open`，容器高度变了只是 detent 目标变）；**正在按住的 PTT 在外屏→内屏切换瞬间按松手处理**（手指物理上一定离开了外屏）——切屏判定用 T1 的 `screenSwitch`，放 effect 里（渲染期用 ref 记 prev 在 StrictMode 双渲下会把事件吞掉）。§7.5：返回手势先收语音层 / 隐私栏再退页面；行车档 B/C 的常驻语音层例外（它不是一个「可收」的层）；「根屏返回 = 退 Activity」的 M3-W 定案不变。`predictiveBackGestureEnabled: false` 是原生配置，本批不碰（§5 第 8 条）。

- [ ] **步骤 1：tabletop 分支**

`StagePane` props 加 `orb?: { state: OrbState; animated: boolean; driving: boolean }`——有它就在标题行下渲 `<AuroraOrb size={120} state={orb.state} animated={orb.animated} driving={orb.driving} />`（居中，`marginVertical: 12`）。**这颗球与 Composer 球会同屏跑两个循环动画**（§11.4「常态 1 个」）⇒ tabletop 下 Composer 球按 `composerOrbAnimated` 之外再压一层：`orbAnimated={composerOrbAnimated(snapshot, motionEnv) && layout.mode !== 'tabletop'}`——写在 ChatScreen 那一行的注释里说明是 tabletop 让位，判据仍是 orbPolicy（这条例外太小，不值得进纯函数；§6.2 记一句）。

`ChatScreen.tsx`：ChatBody 加

```tsx
  // tabletop（§7.3）：分界 = 铰链上缘（窗口坐标）− 内容区在窗口里的 y。onLayout 给的是相对父级的 y，
  // 这里要的是窗口坐标 ⇒ measureInWindow；量一次不够（旋转 / 展开会变），随 layout 重量
  const contentRef = useRef<View | null>(null)
  const [contentBox, setContentBox] = useState({ y: 0, h: 0 })
  useEffect(() => {
    if (layout.mode !== 'tabletop') return
    contentRef.current?.measureInWindow((_x, y, _w, h) => setContentBox({ y, h }))
  }, [layout.mode, layout.width, layout.height])
```

主体渲染的最后一个分支（T6 留的占位）改成三分支：

```tsx
          ) : layout.mode === 'tabletop' ? (
            <View ref={contentRef} style={{ flex: 1 }}>
              {/* 上半：舞台 + 大光球（铰链上方）；下半：转写 / 记录 + Composer。分界 = 铰链上缘（§7.3） */}
              <View style={{ height: tabletopSplit(contentBox.h, layout.hinge?.topDp ?? 0, contentBox.y) }}>
                <StagePane
                  p={p}
                  mode="桌面"
                  messages={messages}
                  vehState={vehState}
                  onSend={onSend}
                  orb={{ state: snapshot.primary, animated: loopsAnimated(motionEnv), driving: orbTempo(snapshot, motionEnv) === 'slow' }}
                  style={{ flex: 1, marginHorizontal: 10, marginTop: 10 }}
                />
              </View>
              <View style={{ height: 8 }} />
              <View style={{ flex: 1 }}>{chatColumn}</View>
            </View>
          ) : (
            // single / driving-landscape（T11 填）
            chatColumn
          )}
```

（import `tabletopSplit` from `sizeClass`、`useRef`。）

- [ ] **步骤 2：外内屏接续——PTT 按松手处理（§7.4）**

ChatBody 里、`usePtt` 之后：

```tsx
  // §7.4：外屏↔内屏切换瞬间，正在按住的 PTT 按松手处理（手指物理上一定离开了那块屏）；轻点会话按「结束并提交」。
  // 切屏判定在 effect 里（渲染期用 ref 记 prev 会被 StrictMode 双渲吞掉事件）
  const prevFoldRef = useRef(layout.fold)
  useEffect(() => {
    const sw = screenSwitch(prevFoldRef.current, layout.fold)
    prevFoldRef.current = layout.fold
    if (!sw || ptt.state !== 'recording') return
    if (ptt.mode === 'hold') ptt.pressUp()
    else if (ptt.mode === 'tap') ptt.tap()
  }, [layout.fold, ptt])
```

（import `screenSwitch` from `sizeClass`。⚠ `layout` 要在 `usePtt` 之后、`usePresence` 之后才有——它读 `snapshot.driving`；把这段放在 `const layout = useLayout(...)` 之后。）

- [ ] **步骤 3：返回手势顺序（§7.5）**

```tsx
  // §7.5 返回顺序：隐私栏 > 语音层（行车档 B/C 的常驻层除外——它不是「可收」的层）> 页面默认
  // （根屏返回 = 退 Activity，M3-W 定案不变；predictiveBackGestureEnabled 是原生配置，本批不碰）
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (privacyOpen) {
        setPrivacyOpen(false)
        return true
      }
      if (snapshot.input === 'voice-sheet' && !sheetResident(snapshot.identity, snapshot.driving)) {
        setSheetOverride({ turnId: latestTurnId, mode: 'dismissed' })
        return true
      }
      return false
    })
    return () => sub.remove()
  }, [privacyOpen, snapshot.input, snapshot.identity, snapshot.driving, latestTurnId])
```

（import `BackHandler` from `react-native`、`sheetResident` from `drivingMode`。收音中按返回：`derivePresence` 的 `capturing` 分支让层保持——返回不等于取消录音，取消只有上滑与「关闭本轮麦克风」两条路，刻意不加第三条。）

- [ ] **步骤 4：tsc + 全量 + 单人可做的真机取证**

Run: `cd mobile && npm run typecheck && npm test` ⇒ 0 error、零增量。

真机（外屏，单人）：① 语音层开着（点胶囊显式开）→ `adb shell input keyevent 4` ⇒ 层收起、`dumpsys window | grep mCurrentFocus` 仍是 `MainActivity`（`b4-07-back-sheet.png`）；再按一次 ⇒ 退到桌面（根屏语义不变，`mCurrentFocus` 不再是本应用）；② 隐私栏开着 → BACK ⇒ 栏关、应用仍前台；③ 阴性：层没开、栏没开 → BACK ⇒ 直接退（与①的第二下同）。三组读数都记 §6.2。

- [ ] **步骤 5：需泓舟手折的四格**（可与第 3 批 T13 合并做，但读数记在本任务名下）

1. **book**：内屏半开、竖持 ⇒ `layout` 行 `two-pane`、`hinge(dp)` 有值、`cmd device_state print-state` = `HALF_OPENED(2)`；截图 `b4-07-book.png`，`png_probe region` 取铰链 x ±8dp 那一竖带：**应是背景色（无文字、无卡边）**——铰链落在 gap 里的机器读数；左栏宽度 = `hinge.leftDp − 8`（截图上量 Composer 右缘）。
2. **tabletop**：半开横放桌面 ⇒ `layout` 行 `tabletop`，截图 `b4-07-tabletop.png`：上半舞台 + 120 球、下半 Composer；`png_probe rows` 在铰链 y 上下各 6dp 取两行平均亮度——分界与铰链对齐（上半的舞台玻璃底与下半的对话底是两种亮度）。
3. **PTT 松手**：外屏长按光球说一句、**按住不放**，泓舟展开手机 ⇒ 松手处理：草稿气泡转正、消息发出（记录里出现用户气泡 + 助手回复；`/presence-trail` 里 `ptt: recording → idle` 的时刻与 `cmd device_state` 变化同秒）。**反向**：轻点开始的 tap 会话在展开瞬间也提交（`ptt.mode === 'tap'` 分支）。
4. **层不收起只重排**：层开着（有转写字）外→内、内→外各一次 ⇒ 层仍在、草稿逐字不变（B2 G4 同款判据，这次多记 `sheetDetent` 目标值随容器高度变化）。

- [ ] **提交**

```bash
git commit -m "feat(mobile): UX v2 B4-7 折叠姿态消费——tabletop 上下半（分界=铰链上缘 measureInWindow）+ 桌面舞台大光球、外↔内切屏瞬间 PTT 按松手处理、返回顺序隐私栏>语音层>页面（常驻层例外）" -- mobile/src/features/chat/ChatScreen.tsx mobile/src/features/stage/StagePane.tsx && git show --stat HEAD
```

---

### Task 8: 材质——语音层外壳换真模糊 + 减少透明度 / 行车档回落 + 浅色对比裁 + 胶囊浅色实底（方案 §5.11、§8 对比、B2 出账⑫）

**Files:**
- 修改 `mobile/src/ui/tokens.ts` + `mobile/test/tokens.test.ts`（`GLASS.frosted.tintOverBlur`）、`mobile/src/core/settings/store.ts`（`reduceTransparency`）+ `mobile/test/settingsMeta.test.ts`（+1）、`mobile/src/features/settings/SettingsScreen.tsx`（实验室两行：减少动效（强制）/ 减少透明度）、`mobile/src/features/chat/ChatScreen.tsx`（`BlurTargetView` + `blurReady`）、`mobile/src/features/chat/VoiceSheet.tsx`（`BlurView` 壳）、`mobile/src/features/chat/PresenceCapsule.tsx`（浅色实底）

**为什么**：B3 §6.3 步骤 3 三判据全过（③ 块梯度方差 11.9 vs ①155.9 / ②328.7、A/B 帧率分不开、20 次挂卸零崩）⇒ Q14 落锤「过」，B4 可换真模糊。**只换语音层外壳**：它压在「变暗的对话」上（可控内容，§8 的对比条款允许玻璃），也是 B2 出账⑫「壳底与浅色光球对比度」的现场；顶栏与舞台压在静态深空底上，糊了没收益，且 §5.11 禁「同屏多个动态 Blur」——一屏只有语音层这一个 `BlurView`。接法照 B3 裁决原话：被糊的背景（对话列表）被 `<BlurTargetView>` 包住、`blurTarget` 指向它、**ref 先挂上再渲 BlurView**（首帧 null 静默回落 none——`blur-spike.tsx` 的 `ready` 那几行就是模板）。真模糊之下壳底 tint 可以更薄（B2 附加①把它抬到 .58 是因为没有模糊）——取 `tintOverBlur = 0.40`，**待证参数**：步骤 5 用 B2 附加①同款「层外 / 层内两条文字带幅度」读数裁，不达标就回 .58。§5.11 末句：reduce-transparency / 行车档 ⇒ 回落 G1-tint（低电量那条**不做**：无 `expo-battery`，§5 第 9 条）；Android 没有系统级「减少透明度」⇒ 实验室给 App 内开关（§8.1）。

- [ ] **步骤 1：写失败测试**

`mobile/test/tokens.test.ts` 材质那条追加断言：`expect(GLASS.frosted.tintOverBlur).toBeLessThan(GLASS.frosted.tint)`（真模糊之下 tint 只能更薄，不能更厚——厚了模糊就白做了）。`mobile/test/settingsMeta.test.ts` +1：`旧库没有 reduceTransparency → false`。

- [ ] **步骤 2：跑红** ⇒ FAIL（字段不存在）

- [ ] **步骤 3：实现**

`tokens.ts`：`frosted: { blur: 28, tint: 0.58, border: 0.16, tintOverBlur: 0.4 }`，注释：「`tintOverBlur`：真模糊在场时壳底的染色（B4-8）；.58 是无模糊时为可读性抬上去的（B2 附加①），糊了之后高频没了、可以更薄——**待证参数**，T8 步骤 5 取数后可改」。

`settings/store.ts`：`reduceTransparency: boolean`（默认 false；注释「实验室：减少透明度（§8.1）——Android 无系统开关，App 内给一个；开 ⇒ 全部材质回落 G1-tint（语音层不糊）」）。

`SettingsScreen.tsx` 实验室分区、两个 v2 开关之后加：

```tsx
        <SwitchRow
          p={p}
          label="减少动效（强制）"
          desc="光球、光标、思考点全部静止（系统「移除动画」开着时自动生效，这里是强制开）"
          value={settings.reduceMotionForce}
          onChange={(reduceMotionForce) => set({ reduceMotionForce })}
        />
        <SwitchRow
          p={p}
          label="减少透明度"
          desc="语音层不再对背景做模糊，回到染色玻璃"
          value={settings.reduceTransparency}
          onChange={(reduceTransparency) => set({ reduceTransparency })}
        />
```

`ChatScreen.tsx`（ChatBody）：

```tsx
  // §5.11 真模糊（B3 T9 裁决过）：被糊的背景 = 对话列表，BlurTargetView 包住它；ref 要先挂上再给 VoiceSheet
  // 渲 BlurView（首帧 null 会被 expo-blur 当成「没配」静默回落成 none——blur-spike.tsx 的 ready 模板）。
  // 回落 G1-tint 的三种情形（§5.11 末句）：减少透明度 / 行车档 / ref 还没挂上；低电量不做（无 expo-battery）
  const blurTargetRef = useRef<View | null>(null)
  const [blurReady, setBlurReady] = useState(false)
  useEffect(() => setBlurReady(true), [])
  const blurTarget = blurReady && blurTargetRef.current && !settings.reduceTransparency && !snapshot.driving ? blurTargetRef : null
```

`chatColumn` 里包住列表（`onLayout` 那个外层 View 不动，`VoiceSheet` 仍在它的直接子级）：

```tsx
      <View style={{ flex: 1 }} onLayout={(e) => setListHeight(Math.round(e.nativeEvent.layout.height))}>
        <BlurTargetView ref={blurTargetRef} style={{ flex: 1 }}>
          {messages.length === 0 ? ( <Welcome … /> ) : ( <FlashList … /> )}
        </BlurTargetView>
        {v2 ? <VoiceSheet … blurTarget={blurTarget} /> : null}
      </View>
```

（import `{ BlurTargetView } from 'expo-blur'`。）

`VoiceSheet.tsx`：props 加 `blurTarget: RefObject<View | null> | null`；壳底那个 View 换成：

```tsx
            {/* 壳底（§5.11 G1 frosted）：真模糊在场 = BlurView + 更薄的 tint；否则 = B2 附加①的 tint（.58） */}
            {props.blurTarget ? (
              <>
                <BlurView
                  pointerEvents="none"
                  blurMethod="dimezisBlurView"
                  blurTarget={props.blurTarget}
                  intensity={60}
                  tint={p.dark ? 'dark' : 'light'}
                  style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}
                />
                <View
                  pointerEvents="none"
                  testID="voice-sheet-shell"
                  style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: shellTint(p.bg, GLASS.frosted.tintOverBlur) }}
                />
              </>
            ) : (
              <View
                pointerEvents="none"
                testID="voice-sheet-shell"
                style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: shellTint(p.bg, GLASS.frosted.tint) }}
              />
            )}
```

（import `{ BlurView } from 'expo-blur'`、`type { RefObject } from 'react'`、`View` 已有。）

`PresenceCapsule.tsx`：`backgroundColor: p.glassBg` → `backgroundColor: p.dark ? p.glassBg : '#FFFFFF'`（注释：§8「浅色主题下胶囊 / Dock 用不透明底」——Dock 已 G0，胶囊补上；深色仍 G1-tint）。

- [ ] **步骤 4：跑绿 + tsc + 全量** ⇒ +2 条（463 → 465），0 error。

- [ ] **步骤 5：真机取证与裁决**（Metro 热载，单人）

1. **真模糊真的在**：点胶囊显式开层 → logcat 干净窗口里 `W ReactNativeJS.*blurTarget` **0 条**（有 1 条 = ref 时序回落了，回去查 `blurReady`）；截图 `b4-08-sheet-blur-dark.png`，用 B3 的 `hfreq.py`（`mobile/e2e/artifacts/` 里那个，复用 `png_probe.decode`）量层内一条**没有文字**的横带：最大梯度应 ≤ B3 ③ 块量级（19.2）而不是 ①② 的 100+；**反例**：实验室开「减少透明度」再截 `b4-08-sheet-tint-dark.png` ⇒ 同带最大梯度回到 100+（记录里的气泡边缘透过来）。
2. **壳底可读性（tintOverBlur 的裁）**：B2 附加①同款读数——层内答案文字带与层外记录文字带各取幅度（`png_probe rows`），层内 / 层外幅度比 **≤ 0.5** 算过（B2 无模糊 + .58 时是 18.8 / 41.9 = 0.45）；不过就把 `tintOverBlur` 抬到 .50 再量，仍不过回 .58（等于只加模糊不减 tint，也是合法落地），裁决与读数写 §6.2。
3. **浅色对比（B2 出账⑫）**：主题切浅色，层开、光球 `listening`（点光球）⇒ 截图 `b4-08-sheet-light.png`；`png_probe region` 取球体边缘 8dp 环带与紧邻壳底 8dp 环带的平均亮度，差 **≥ 40/255** 算过；不过 ⇒ 在 `VoiceSheet` 大球后面加一枚浅色专用的暗盘（`!p.dark` 时 `<View style={{ position:'absolute', width: orb*1.5, height: orb*1.5, borderRadius: 9999, backgroundColor: 'rgba(10,14,26,0.08)' }} />`），加完重量、≥40 才算过；仍不过 ⇒ 出账（换 orb 浅色专用高光参数是 A-1 §10 的事，B4 不动光球）。
4. **胶囊浅色实底**：浅色下截 `b4-08-capsule-light.png`，胶囊底 `png_probe px` 应为 `#FFFFFF`（B1 是 76% 白透出深空 blob 的色）。
5. **行车档回落**：T10 之后才有开关——本任务只验 `settings.reduceTransparency` 那一路；行车档回落在 T13（行车档开着时层壳应回 tint）顺带。
6. **稳定**：层开合 20 次（点胶囊 / 下拉）零崩、本应用 pid 零 `E/`、`F/`（B3 判据 3 同款）。

- [ ] **提交**

```bash
git commit -m "feat(mobile): UX v2 B4-8 材质——语音层外壳换真模糊（BlurTargetView 包对话列表 + blurTarget，ref 先挂再渲；同屏唯一 BlurView）、tintOverBlur 待证参数、减少透明度/行车档回落 G1-tint、实验室两开关、胶囊浅色实底" -- mobile/src/ui/tokens.ts mobile/test/tokens.test.ts mobile/src/core/settings/store.ts mobile/test/settingsMeta.test.ts mobile/src/features/settings/SettingsScreen.tsx mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/VoiceSheet.tsx mobile/src/features/chat/PresenceCapsule.tsx && git show --stat HEAD
```

---

### Task 9: 发送按钮图标 + 无障碍补项——转写 / 回答 live region、partial 节流、卡片按钮 role（B2 出账④、方案 §8 TalkBack、§8.1 partial 节流）

**Files:**
- 新建 `mobile/src/ui/icons.local.ts`
- 修改 `mobile/src/ui/Icon.tsx`（合并本地图标）、`mobile/src/features/chat/Composer.tsx`（圆形图标发送键）、`mobile/src/features/chat/VoiceSheet.tsx`（live region）、`mobile/src/features/chat/PresenceCapsule.tsx`（recognizing 时让出 live region）、`mobile/src/features/cards/parts.tsx`（`CardButtons` role / label / 48 目标）

**为什么**：发送按钮改图标是泓舟 2026-09-01 提的设计项（B2 §6.4，对标小艺 / 小爱的圆形箭头键）。共享图标库（`icons.gen.ts` / `icons.custom.ts`）里没有「发送」「键盘」，而 `hmi/` 不碰、共享台账不为 mobile 单方需求扩 ⇒ mobile 本地放两枚（格式与 `icons.custom.ts` 同：24×24 盒、1.8 stroke、`currentColor`），`Icon.tsx` 的 REGISTRY 合并；svg 原生缺席仍回退文字（`iconRuntimeAvailable()` 既有判据）。虹彩纪律三处之一（发送键极光填充）不变，只是从矩形「发送」变成圆形图标——Maestro 流都按 `id: composer-send` 驱动，改之前 `grep -n '"发送"' mobile/e2e/*.yaml` 确认没有按文本断言它。无障碍：§8 要转写区与回答区 `accessibilityLiveRegion="polite"`——今天两处都没有，而**胶囊有**且 recognizing 时显示逐 token partial（§8.1 明写要节流：TalkBack 会不断打断自己）⇒ live region 从胶囊挪到转写区（草稿气泡按稳定 segment 写，B2 T4——本来就是节流过的粒度），胶囊在 `capture === 'recognizing'` 时 `accessibilityLiveRegion="none"`；Dock 进入播报 **不加** `announceForAccessibility`（B1 的 `assertive` live region 已覆盖，再加会双读）；卡片按钮补 `role="button"` + label，目标 48（§5.4）。

- [ ] **步骤 1：本地图标与发送键**

`mobile/src/ui/icons.local.ts`：

```ts
// mobile/src/ui/icons.local.ts
// mobile 专有图标数据（B4-9）。共享图标库（hmi icons.gen / icons.custom）里没有「发送」「键盘」，而 hmi/ 不碰、
// 共享台账不为 mobile 单方需求扩——本地放两枚，格式与 icons.custom.ts 同（24×24 盒、1.8 stroke、currentColor 由 Icon.tsx 替换）。
// send：线性纸飞机（对标小艺 / 小爱的圆形发送键，B2 出账④）；keyboard：行车档 B 身份「文本输入折叠成键盘图标」（§6.0）。
export const LOCAL_ICONS = {
  send: { w: 24, h: 24, body: '<path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9L22 2Z"/>' },
  keyboard: {
    w: 24,
    h: 24,
    body: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10"/>',
  },
} as const

export type LocalIconName = keyof typeof LOCAL_ICONS
```

`Icon.tsx`：`const REGISTRY = { ...ICON_DATA, ...ICON_CUSTOM, ...LOCAL_ICONS }`；`export type IconName = GenIconName | keyof typeof ICON_CUSTOM | LocalIconName`（import `LOCAL_ICONS, type LocalIconName` from `'./icons.local'`）。

`Composer.tsx` 发送键换成（`Icon`、`iconRuntimeAvailable` 从 `'../../ui/Icon'` import；圆形尺寸本任务 44，T11 按行车档换 56）：

```tsx
        <Pressable
          testID="composer-send"
          accessibilityRole="button"
          accessibilityLabel="发送"
          onPress={submit}
          style={{
            experimental_backgroundImage: AURORA.gradient,
            width: scale(44, 'target', fontScale),
            height: scale(44, 'target', fontScale),
            borderRadius: RADIUS.full,
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 4px 22px rgba(91,140,255,0.45)',
          }}
        >
          {iconRuntimeAvailable() ? (
            <Icon name="send" size={22} color="#fff" />
          ) : (
            <Text style={{ color: '#fff', fontSize: p.font(15), fontWeight: '600' }}>发</Text>
          )}
        </Pressable>
```

- [ ] **步骤 2：无障碍补项**

- `VoiceSheet.tsx`：转写 `Text`（`testID="voice-sheet-transcript"`）与回答 `Text`（`testID="voice-sheet-answer"`）各加 `accessibilityLiveRegion="polite"`。
- `PresenceCapsule.tsx`：`accessibilityLiveRegion="polite"` 改成 `accessibilityLiveRegion={snapshot.capture === 'recognizing' ? 'none' : 'polite'}`（注释：§8.1 partial 节流——逐 token 的 partial 交给转写区按稳定 segment 播，胶囊在识别中闭嘴）。
- `parts.tsx::CardButtons`：`Pressable` 加 `accessibilityRole="button"`、`accessibilityLabel={b.label}`、`hitSlop={2}`，`minHeight: 36` → `44`（44 + 2×2 = 48 目标，§5.4 行高 ≥48 那条）。

- [ ] **步骤 3：tsc + 全量** ⇒ 0 error、零增量（465）。`grep -n '"发送"' mobile/e2e/*.yaml` 为空（若有，按 id 改那条流并记 §6.2）。

- [ ] **步骤 4：真机取证**

1. 发送键截图 `b4-09-send-icon.png`（深浅各一张）**交泓舟看图标方案**——他要的是看，不是我判；反例：svg 缺席回退文字这一路今天没法在真机造（原生在场），只 tsc 保证分支存在。
2. Maestro 01 / 06 / 08 rc=0（按 id 驱动，图标不影响）。
3. TalkBack（**需泓舟开**：改无障碍服务是系统设置）：识别中焦点在转写区 ⇒ 按 segment 播、不逐字打断；开层后回答流式 ⇒ 回答区播；卡片按钮 ⇒ 读「按钮 + 标签」。单人替代读法：`maestro hierarchy` 里 `composer-send` 的 `accessibilityText`=「发送」、卡片按钮节点有 `clickable + text`——**这只证明 label 在树里，不证明 live region 行为**，两种读数分开记。

- [ ] **提交**

```bash
git add -- mobile/src/ui/icons.local.ts && git commit -m "feat(mobile): UX v2 B4-9 发送键改圆形极光图标（本地图标数据，svg 缺席回退文字）+ 转写/回答 live region、胶囊识别中让出 live region（partial 节流）、卡片按钮 role/label/48 目标" -- mobile/src/ui/icons.local.ts mobile/src/ui/Icon.tsx mobile/src/features/chat/Composer.tsx mobile/src/features/chat/VoiceSheet.tsx mobile/src/features/chat/PresenceCapsule.tsx mobile/src/features/cards/parts.tsx && git show --stat HEAD
```

---

### Task 10: 身份与行车档设置——`deviceRole` 三选 + 身份说明行 + 行车档手动开关 + 建议胶囊（方案 §6.0、§6 触发②③、Q4、Q15）

**Files:**
- 修改 `mobile/src/features/settings/SettingsScreen.tsx`（新分区置顶）、`mobile/src/core/presence/presence.ts`（`drivingSuggest` 输入、胶囊 `action`）、`mobile/test/presence.test.ts`（+2）、`mobile/src/features/chat/usePresence.ts`（建议三条件、`landscape` opt）、`mobile/src/features/chat/ChatScreen.tsx`（胶囊 `action` 分派、过期注释清理）

**为什么**：§6.0「先定产品身份再定行车档」：身份由 token scope + 设备角色决定，**窗口尺寸不决定权限、横屏不决定你是驾驶员、平板不自动获得车控**。`deviceRole` 是 B1 的占位（settings 里有、无 UI），B4 给它 UI 与说明行。C 身份的「绑定」（全 scope token）App 端**判不了**——token 对 App 是不透明串、没有 scope 查询端点；选了 C 也**不会**多出任何权限（车控可用与否始终在服务端按 token 裁）⇒ 设置里 C 可选（否则 §11.2 B4 ② 的「C 隐藏文本输入」验不到），说明行**不承诺「可控车」**，写「车控由服务端 token 决定，这里只决定布局」（§5 第 6 条；Q15 的绑定另议）。行车档触发②手动开关 + 触发③「身份 C + 横屏 + keep-awake 同时成立 ⇒ 建议一次」——胶囊只有一个槽、今天 `onPress` 恒等于「打开语音层」（`ChatScreen.tsx:518`），所以胶囊要长出 `action`（`open-sheet` / `enable-driving`），判据仍在 `derivePresence`（建议胶囊排在 error 之后 armed 之前：它是常态提示，让位给收音 / 播报 / 思考 / 错误）。顺手清掉 `ChatScreen.tsx:511` 那句过期注释（B3 §6.1 遗留④）。

- [ ] **步骤 1：写失败测试**

`mobile/test/presence.test.ts` 追加：

```ts
describe('B4-10 行车档建议胶囊（§6 触发③：只建议不自动切）', () => {
  test('drivingSuggest=true 且无更高优先级的胶囊时 ⇒ 「切到行车档？」带 action=enable-driving', () => {
    const s = derivePresence(base({ drivingSuggest: true }))
    expect(s.capsule).toEqual({ text: '切到行车档？点一下开启', tone: 'accent', action: 'enable-driving' })
    expect(s.driving).toBe(false) // 只建议，不切
  })
  test('收音 / 播报 / 错误在场时建议让位；其它胶囊没有 action', () => {
    expect(derivePresence(base({ drivingSuggest: true, ptt: 'recording' })).capsule?.text).toBe('在听…')
    expect(derivePresence(base({ drivingSuggest: true, speaking: true })).capsule?.action).toBeUndefined()
    expect(derivePresence(base({ drivingSuggest: true, lastError: { text: '出错了', at: NOW - 500 } })).capsule?.tone).toBe('red')
  })
})
```

- [ ] **步骤 2：跑红** ⇒ FAIL（`drivingSuggest` 不在输入类型里 / capsule 无 action）

- [ ] **步骤 3：实现**

`presence.ts`：
- `PresenceInput` 加 `/** 建议开行车档（§6 触发③三条件由收集器算：drivingMode.ts::drivingSuggested）；只建议不切 */ drivingSuggest?: boolean`；
- `PresenceSnapshot.capsule` 类型加 `action?: 'open-sheet' | 'enable-driving'`（注释：胶囊点按做什么——缺省 open-sheet（ChatScreen 既有行为），建议胶囊是唯一的另一种）；
- capsule 链在 `errorLive` **之后**、`armedCapsule` **之前**插一行：
  ```ts
    else if (i.drivingSuggest) capsule = { text: '切到行车档？点一下开启', tone: 'accent', action: 'enable-driving' }
  ```

`usePresence.ts`：`UsePresenceOpts` 加 `landscape: boolean`；`input` 加：

```ts
    drivingSuggest: drivingSuggested({
      identity: settings.deviceRole,
      landscape,
      keepAwake: settings.keepAwake,
      active: drivingNow,
    }),
```

（import `drivingSuggested`。「弹一次」的语义：三条件持续成立就持续显示，用户点了或条件消失即没；不做「本会话只弹一次」的记忆——一个常态提示比一个会消失的记忆更可预期，且它让位给所有更重要的胶囊。）

`ChatScreen.tsx`：
- `usePresence({ …, landscape: layout?.landscape })` ——⚠ `layout` 在 `usePresence` 之后才算（它读 `snapshot.driving`）；`landscape` 用 `useWindowDimensions()` 直接算（`width > height`）传进去，不经 `layout`；
- 胶囊：`onPress={() => setSheetOverride({ turnId: latestTurnId, mode: 'open' })}` 改成
  ```tsx
          onPress={() =>
            snapshot.capsule?.action === 'enable-driving'
              ? settingsStore.getState().update({ drivingManual: true })
              : setSheetOverride({ turnId: latestTurnId, mode: 'open' })
          }
  ```
- 删掉 `:511` 那段「B1 不接 onPress」的注释，换成一句真的：「胶囊点按：默认打开语音层；建议胶囊 = 开行车档（action 由 derivePresence 给，判据不在这里）」。

`SettingsScreen.tsx`：「服务器」分区**之后**、「显示」之前加新分区：

```tsx
      {/* 身份与行车（方案 §6.0）：身份由 token scope + 设备角色决定——窗口尺寸不决定权限、横屏不决定你是驾驶员。
          App 端判不了 token 的 scope（不透明串、无查询端点），所以说明行只说布局、不承诺「可控车」；
          选了「可信车载平板」也不会多出任何权限——车控可用与否始终在服务端按 token 裁。 */}
      <Section p={p} title="身份与行车">
        <Text style={{ color: p.fg2, fontSize: p.font(13) }}>
          {settings.deviceRole === 'handheld'
            ? '手持陪伴端 · 不控车'
            : settings.deviceRole === 'mount'
              ? '支架 · 副驾协同 · 不控车'
              : '可信车载平板 · 车控由服务端 token 决定，这里只决定布局'}
        </Text>
        <ChoiceRow
          p={p}
          label="设备角色"
          value={settings.deviceRole}
          options={[
            { v: 'handheld' as const, label: '手持' },
            { v: 'mount' as const, label: '支架 / 副驾' },
            { v: 'trusted-tablet' as const, label: '可信车载平板' },
          ]}
          onPick={(deviceRole) => set({ deviceRole })}
        />
        <SwitchRow
          p={p}
          label="行车档"
          desc="目标放大、过程区单行、文本输入按角色收起。座舱判定行车时自动进入，停车 30 秒后自动退出"
          value={settings.drivingManual}
          onChange={(drivingManual) => set({ drivingManual })}
        />
      </Section>
```

- [ ] **步骤 4：跑绿 + tsc + 全量** ⇒ +2（465 → 467），0 error。`presence.test.ts` 既有的优先级用例全绿（建议胶囊只在链尾，动不到它们）。

- [ ] **步骤 5：反向验证**

① 建议那行挪到 `errorLive` 之前 ⇒ **恰好红**「错误在场时让位」；② `drivingSuggested` 里 `landscape` 条件删掉 ⇒ `drivingMode.test.ts` 那条 `缺 landscape` 红（判据在 T2 那份，这里只是接线——两处各红各的）。

- [ ] **步骤 6：Metro 热载取证**：设置页顶部分区截图 `b4-10-identity.png`（三种角色各切一次，说明行随之变——`png_probe` 不用，文字读图）；行车档开关开 ⇒ 对话页 `/presence-trail` 的 `driving` 轴翻 true（`b4-10-driving-manual.png`）；关 ⇒ 立刻 false（手动关不走 30s 宽限——宽限只给 Edge 标）。建议胶囊：角色选「可信车载平板」+ 常亮开 + 横屏（泓舟转手机，或 T6 步骤 6 ② 的授权路子）⇒ 胶囊「切到行车档？点一下开启」（`b4-10-suggest.png`）；点它 ⇒ 行车档开、胶囊消失；**阴性**：三条件缺一（关常亮）胶囊不出。做完角色还原 `handheld`、常亮还原关、行车档关，**回读**。

- [ ] **提交**

```bash
git commit -m "feat(mobile): UX v2 B4-10 身份与行车档设置——deviceRole 三选 + 说明行（不承诺可控车）+ 行车档手动开关；建议胶囊（身份 C+横屏+常亮只建议不切，capsule.action 由 derivePresence 给）；清 ChatScreen 过期注释" -- mobile/src/features/settings/SettingsScreen.tsx mobile/src/core/presence/presence.ts mobile/test/presence.test.ts mobile/src/features/chat/usePresence.ts mobile/src/features/chat/ChatScreen.tsx && git show --stat HEAD
```

---

### Task 11: 行车档布局全套——目标 56 / 文本输入按身份 / chips ≤3 / 过程区单行 / 语音层常驻 + 3s 回落 / 120dp 球 / 一屏一卡 / 横屏 40:60 / 上滑取消禁用（方案 §6 规则、§6.0 差异、§5.2 规则 3 行车条款、§5.1.1 行车条款、§5.4）

**Files:**
- 新建 `mobile/src/features/cards/DrivingCardSummary.tsx`、`mobile/e2e/tools/target_probe.py`
- 修改 `mobile/src/core/presence/presence.ts`（`voice.answeredAt`、行车档 input / detent）、`mobile/test/presence.test.ts`（+5）、`mobile/src/features/chat/usePresence.ts`（`answeredAt` + 3s tick 窗）、`mobile/src/features/chat/ChatScreen.tsx`（driving-landscape 分支、props 下发）、`mobile/src/features/chat/VoiceSheet.tsx`（行车形态 + `split`）、`mobile/src/features/chat/Composer.tsx`（`inputMode` / chips / 目标 / 取消手势）、`mobile/src/features/chat/FocusDock.tsx`、`PresenceCapsule.tsx`、`FollowUpChips.tsx`、`mobile/src/core/session/followUps.ts`（`max`）、`mobile/src/features/chat/MessageBubble.tsx`（`ProcessFold` 单行）、`mobile/src/core/presence/fixtures.ts` + `mobile/test/presenceFixtures.test.ts`（行车样本）

**为什么**：这是 P8 的落地——§6 的规则逐条对应一处代码，判据全在第 1 批（`drivingActive / composerInputMode / sheetResident / orbTempo`），本任务只把它们接到 UI。三处需要说清的合并：① 「语音层常驻」（§6）与「行车档下 TTS 结束后 +3s 自动收起」（§5.2 规则 3）——常驻是**层不消失**，+3s 是**内容回落**：答完 3s 后 detent 回 0.4、层里只剩 120dp 球 + 胶囊（§5 第 15 条）；只给 B/C（A 是手持、可能乘客在打字）。② 「一屏只有一张卡、只显示标题 + ≤2 字段 + 1 主按钮」——不改 34 个渲染器，行车档语音层里用 `DrivingCardSummary`（读 T5 的 `cardFields`），记录里的卡不动（记录在常驻层身后）。③ 「目标 ≥56dp」——`TARGET.driving` 已是 56，Composer 球本来就是；发送键 / Dock 按钮 / chips / 层内按钮 / 胶囊热区按 `snapshot.driving` 取 56，读实用 `maestro hierarchy` 的 bounds（`target_probe.py`）——**不是** Accessibility Scanner 读数，两者分开记（Scanner 装 APK 要授权，T13 步骤 13）。过程区单行：`Msg.driving`（Edge 标在气泡上，语义「行车极简、不可展开」）∨ 当前行车档，`ProcessFold` 单行不可展开。横屏车载（`driving-landscape`）：语音层 `split`——左 40% 球 + 转写 + 胶囊，右 60% 回答 + 卡 + chips。§5.1.1 行车条款：只保留「按住—松开发送」，上滑取消禁用。

- [ ] **步骤 1：写失败测试**

`mobile/test/presence.test.ts` 追加：

```ts
describe('B4-11 行车档下的语音层（§6 常驻 + §5.2 规则 3 行车条款）', () => {
  const drivingB = { driving: true, identity: 'mount' as const }
  test('B/C 行车：无轮也常驻（input=voice-sheet、detent 0.4）；A 行车不常驻', () => {
    expect(derivePresence(base(drivingB)).input).toBe('voice-sheet')
    expect(derivePresence(base(drivingB)).sheetDetent).toBe(0.4)
    expect(derivePresence(base({ driving: true, identity: 'trusted-tablet' })).input).toBe('voice-sheet')
    expect(derivePresence(base({ driving: true, identity: 'handheld' })).input).toBe('composer')
  })
  test('答完 3s 内 detent 仍 0.62；3s 后回 0.4（层不消失，内容回落）', () => {
    const answered = (ago: number) =>
      base({ ...drivingB, voice: { turnSource: 'handsfree', override: null, answer: true, card: false, answeredAt: NOW - ago } })
    expect(derivePresence(answered(1_000)).sheetDetent).toBe(0.62)
    expect(derivePresence(answered(3_000)).sheetDetent).toBe(0.4)
  })
  test('播报中不回落（answeredAt 早于 3s 但 speaking）', () => {
    const s = derivePresence(base({ ...drivingB, speaking: true, voice: { turnSource: 'handsfree', override: null, answer: true, card: false, answeredAt: NOW - 10_000 } }))
    expect(s.sheetDetent).toBe(0.62)
  })
  test('有卡仍 0.78；用户下拉过这一轮仍可收（常驻不是不可收）', () => {
    expect(derivePresence(base({ ...drivingB, voice: { turnSource: 'handsfree', override: null, answer: true, card: true, answeredAt: NOW - 10_000 } })).sheetDetent).toBe(0.78)
    expect(derivePresence(base({ ...drivingB, voice: { turnSource: 'handsfree', override: 'dismissed', answer: true, card: false } })).input).toBe('composer')
  })
  test('非行车：answeredAt 不影响 detent（3s 回落只是行车条款）', () => {
    expect(derivePresence(base({ voice: { turnSource: 'ptt', override: null, answer: true, card: false, answeredAt: NOW - 10_000 }, turn: { pending: false, streaming: true, processActive: false, processLabel: '', processSince: 0 } })).sheetDetent).toBe(0.62)
  })
})
```

- [ ] **步骤 2：跑红** ⇒ FAIL（`answeredAt` 不在 `VoiceFacts` / 常驻未实现）

- [ ] **步骤 3：实现（判据层）**

`presence.ts`：
- `VoiceFacts` 加 `/** 本轮 final 到达时刻（turnMeta.finalAt）；行车档 +3s 回落读它；0/缺省=还没答完 */ answeredAt?: number`；
- 新常量 `export const DRIVING_SHEET_SETTLE_MS = 3000`（注释：§5.2 规则 3 行车条款「TTS 结束后 +3s 自动收起」——收起 = 内容回落 0.4，层常驻不消失）；
- 语音层开合那段改成：

```ts
  const resident = sheetResident(i.identity, i.driving)
  const sheetOpen =
    capturing ||
    voice?.override === 'open' ||
    (voice?.override !== 'dismissed' && (voiceTurnLive || resident))
  const input: PresenceSnapshot['input'] = sheetOpen ? 'voice-sheet' : 'composer'
  // 行车档（§5.2 规则 3 行车条款）：答完且没在播报、过了 3s ⇒ 内容回落到只球 + 胶囊（0.4）；有卡 / 长任务不回落
  const settled =
    i.driving && !!voice?.answeredAt && agent !== 'speaking' && i.now - voice.answeredAt >= DRIVING_SHEET_SETTLE_MS
  const sheetDetent: SheetDetent =
    commitment.some((c) => c.kind === 'task') || !!voice?.card ? 0.78 : voice?.answer && !settled ? 0.62 : 0.4
```

（import `sheetResident` from `'./drivingMode'`——presence → drivingMode 值依赖、drivingMode → presence 只 `import type`，无运行时环。）

`usePresence.ts`：`voice` 加 `answeredAt: (latestTurnId && turnMeta[latestTurnId]?.finalAt) || 0`；`needsTick` 加 `(drivingNow && !!voice.answeredAt && now - voice.answeredAt < DRIVING_SHEET_SETTLE_MS) || // 行车档答后 3s 回落`（import 常量）。

- [ ] **步骤 4：实现（组件层）**

**`DrivingCardSummary.tsx`**：

```tsx
// mobile/src/features/cards/DrivingCardSummary.tsx
// 行车压缩卡（B4-11 / 方案 §6「一屏只有一张卡、只显示标题 + ≤2 个字段 + 1 个主按钮」）。
// 不改 34 个渲染器：从任意卡里探取（core/cards/cardFields.ts），card_group 取主卡（display_priority 判据在 cardGroup.ts）。
// 只在行车档的语音层里用；记录里的卡照旧全量渲（记录在常驻层身后）。目标 56dp（TARGET.driving）。
import { Pressable, Text, View } from 'react-native'

import { splitCardGroup } from '@/core/cards/cardGroup'
import { cardListRows, cardPrimaryButton, cardPrimaryFields } from '@/core/cards/cardFields'
import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

import { CardShell, ProvBadge, type SendFn } from './parts'

/* eslint-disable @typescript-eslint/no-explicit-any */
export function DrivingCardSummary({ p, fontScale, card, onSend }: { p: Palette; fontScale: FontScalePref; card: any; onSend: SendFn }) {
  const main = card?.type === 'card_group' ? splitCardGroup(card.items || []).main : card
  if (!main || typeof main !== 'object') return null
  const fields = cardPrimaryFields(main, 3) // 首个当标题，其余最多两个字段
  const rows = cardListRows(main, 1)
  const title = fields[0]?.[1] ?? rows[0]?.title ?? String(main.type || '')
  const kv = fields.slice(1, 3)
  const button = cardPrimaryButton(main)
  const h = scale(TARGET.driving, 'target', fontScale)
  return (
    <CardShell p={p} title={String(main.type || '')} right={<ProvBadge p={p} prov={main._prov} />}>
      <Text testID="driving-card-title" style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fontScale), fontWeight: '600' }} numberOfLines={1}>
        {title}
      </Text>
      {rows[0]?.sub ? <Text style={{ color: p.fg2, fontSize: scale(TYPE.body, 'text', fontScale) }} numberOfLines={1}>{rows[0].sub}</Text> : null}
      {kv.map(([k, v]) => (
        <Text key={k} style={{ color: p.fg2, fontSize: scale(TYPE.body, 'text', fontScale) }} numberOfLines={1}>
          {k}: {v}
        </Text>
      ))}
      {button ? (
        <Pressable
          testID="driving-card-button"
          accessibilityRole="button"
          accessibilityLabel={button.label}
          onPress={() => onSend(button.send_text)}
          style={{ minHeight: h, borderRadius: RADIUS.md, backgroundColor: p.accentSoft, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 16 }}
        >
          <Text style={{ color: p.accent, fontSize: scale(TYPE.h2, 'text', fontScale) }}>{button.label}</Text>
        </Pressable>
      ) : null}
      <View />
    </CardShell>
  )
}
```

**`Composer.tsx`**：props 加 `driving: boolean`、`inputMode: ComposerInputMode`；
- `const target = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)`；光球热区保持 `TARGET.driving`（本来就 56）；发送键宽高 `driving ? target : scale(44, …)`；快捷 chips `minHeight: driving ? target : undefined`、`quickCommands.slice(0, driving ? 3 : quickCommands.length)`；
- `makeHold` 的 `onUpdate` 加守卫：`if (!driving && heldRef.current && …)`（注释：§5.1.1 行车条款——只保留按住—松开发送，空间手势禁用）；
- 输入框三态（本地 `const [inputOpen, setInputOpen] = useState(false)`；`inputMode` 变化时 `useEffect(() => setInputOpen(false), [inputMode])`）：
  - `'always'`：现状；
  - `'folded'`：不渲 TextInput 与透明层，渲一枚 `target` 大小的键盘按钮（`testID="composer-keyboard"`，`Icon name="keyboard"`，缺席回退「⌨」），点它 `setInputOpen(true)` 后按 `'always'` 渲（再点键盘按钮收回）；
  - `'hidden'`：不渲输入框、不渲键盘按钮；`flex:1` 由一个空 View 占位，光球与发送键仍在（发送键在无输入时 `disabled`）。

**`VoiceSheet.tsx`**：props 加 `driving: boolean`、`split: boolean`、`identity`（读 `snapshot.identity`）：
- 大球 `size={driving ? 120 : 88}`；转写 `fontSize: scale(driving ? 20 : 20 …)` 保持 20、回答 `scale(driving ? TYPE.h2 : TYPE.body + 1 …)`（18pt）；按钮 `minHeight: scale(driving ? TARGET.driving : TARGET.parked …)`；
- **回落形态**：`driving && snapshot.sheetDetent === 0.4 && snapshot.agent === 'idle' && !capturing` ⇒ 只渲球 + 胶囊（转写 / 回答 / chips / 卡都不渲）；
- 卡：`driving ? <DrivingCardSummary …/> : <CardRenderer …/>`；chips：`followUpChips(assistant.followUp, props.candidates, driving ? 3 : MAX_CHIPS)`（`followUps.ts` 的 `followUpChips` 加第三个参数 `max = MAX_CHIPS`）；`FollowUpChips` 加 `target` prop（`minHeight` 取它）；
- `split`（横屏车载）：把 ScrollView 内容分成两栏 `flexDirection:'row'`：左 `width:'40%'`（转写 + 球 + 胶囊）、右 `flex:1`（回答 + chips + 卡）。

**`FocusDock.tsx`**：`const h = scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', fontScale)`；`DegradationRow` 的按钮同款。**`PresenceCapsule.tsx`**：`hitSlop` 按 `snapshot.driving ? TARGET.driving : TARGET.parked`。**`FollowUpChips.tsx`**：`target` prop。

**`MessageBubble.tsx`**：`BubbleProps` 加 `driving: boolean`；`ProcessFold` 加 `driving` 参数：`if (msg.driving || driving)` ⇒ 只渲一行 `⟳ {最后一步 label}…`（进行中）/ `过程 N 步`（结束，**不可展开**，`Pressable disabled`），注释引 `types.ts:28` 的语义原话「行车极简、不可展开」。

**`ChatScreen.tsx`**：
- `composerInputMode(snapshot.identity, snapshot.driving)` 算一次传 Composer；`driving={snapshot.driving}` 传 Composer / MessageBubble（`extraData` 加 `snapshot.driving`）/ VoiceSheet；VoiceSheet `split={layout.mode === 'driving-landscape'}`；
- `driving-landscape` 分支：与 `single` 同（`chatColumn`），差异全在 VoiceSheet 的 `split`——不另写布局；
- 快捷指令行在 `Welcome` 里也 `slice(0, 3)`（本来就是 3）。

**`fixtures.ts`**：加三条样本（都 `producible: true`——手动开关就能造）：
```ts
    mk('driving-resident-C', { driving: true, identity: 'trusted-tablet' }),
    mk('driving-answer-B', { driving: true, identity: 'mount', speaking: true, voice: { turnSource: 'handsfree', override: null, answer: true, card: false } }),
    mk('driving-suggest', { drivingSuggest: true, identity: 'trusted-tablet' }),
```
（`presenceFixtures.test.ts` 从产出方源码盘点 `producible`——`driving` / `drivingSuggest` 都是 `usePresence` 真会给的输入，按它的规则该标 true；跑红了就照它的判据改。）

**`mobile/e2e/tools/target_probe.py`**：

```python
"""目标尺寸读实（B4-11 / 方案 §6「目标 ≥56dp」）——从 `maestro hierarchy` 的 JSON 里读指定 testID 的 bounds，
按 density 换算成 dp，逐个断言 ≥ 阈值。**不是 Accessibility Scanner 的读数**（那个装 APK 要授权），两者分开记。

用法：
  D:/Android/tools/maestro-dist/maestro/bin/maestro.bat hierarchy > h.json
  python target_probe.py h.json --density $(adb shell wm density | grep -oE '[0-9]+' | head -1) \
      --min 56 composer-orb composer-send dock-accept dock-cancel followup-chip voice-sheet-collapse driving-card-button
退出码：0 全过 / 1 有不达标 / 2 找不到某个 id（演员没上场——先核那一屏真的在）。
"""
import json
import re
import sys


def walk(node, out):
    if isinstance(node, dict):
        attrs = node.get('attributes', node)
        rid = attrs.get('resource-id') or attrs.get('resourceId') or ''
        b = attrs.get('bounds')
        if rid and b:
            out.setdefault(rid.split('/')[-1], []).append(b)
        for v in node.values():
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)


def dp_of(bounds, density):
    m = re.findall(r'-?\d+', bounds)
    x1, y1, x2, y2 = (int(v) for v in m[:4])
    k = density / 160.0
    return (x2 - x1) / k, (y2 - y1) / k


def main(argv):
    if len(argv) < 5 or '--density' not in argv:
        print(__doc__)
        return 2
    path = argv[1]
    density = int(argv[argv.index('--density') + 1])
    min_dp = float(argv[argv.index('--min') + 1]) if '--min' in argv else 56.0
    ids = [a for a in argv[2:] if not a.startswith('--') and not a.isdigit()]
    with open(path, encoding='utf-8') as f:
        tree = json.load(f)
    found = {}
    walk(tree, found)
    rc = 0
    for tid in ids:
        if tid not in found:
            print(f'{tid}: NOT FOUND（演员没上场）')
            rc = 2 if rc == 0 else rc
            continue
        for b in found[tid]:
            w, h = dp_of(b, density)
            ok = min(w, h) >= min_dp
            print(f'{tid}: {w:.1f}×{h:.1f}dp {"PASS" if ok else "FAIL"} ({b})')
            if not ok:
                rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv))
```

（⚠ `maestro hierarchy` 的 JSON 结构以实跑输出为准——`walk` 对 `attributes` 嵌套与扁平两种都试了；第一次跑先 `head -40 h.json` 看一眼键名，不对就改 `walk`，别改判据。）

- [ ] **步骤 5：跑绿 + tsc + 全量** ⇒ +5（467 → 472；`presenceFixtures.test.ts` 若因新样本增减条数照实记），0 error；`presence.test.ts` 既有 `sheet-*` 用例全绿（非行车路径一字未动）。

- [ ] **步骤 6：反向验证**

① `settled` 里去掉 `agent !== 'speaking'` ⇒ **恰好红**「播报中不回落」；② `resident` 从 `sheetOpen` 里删掉 ⇒ 恰好红「B/C 常驻」（A 那条仍绿——它本来就不常驻）；③ `DRIVING_SHEET_SETTLE_MS` 改 5000 ⇒ 恰好红「3s 后回 0.4」；④ 组件层无 jest——判据是 T13 的真机读数（≥56 读实 / 文本输入三张 / 过程区单行 / 横屏）。

- [ ] **步骤 7：Metro 热载冒烟（手动行车档，单人）**：设置开「行车档」、角色 C ⇒ 回对话页语音层常驻（120 球 + 胶囊）、无输入框、发送键 56、快捷 chips 3 条；角色 B ⇒ 键盘按钮，点它输入框出来；角色 A ⇒ 输入框常驻、层不常驻；`maestro hierarchy > h.json` + `target_probe.py … --min 56 composer-orb composer-send` PASS（完整清单在 T13）。关行车档 ⇒ 全部回原样。三张截图 `b4-11-input-{A,B,C}.png`。

- [ ] **提交**

```bash
git add -- mobile/src/features/cards/DrivingCardSummary.tsx mobile/e2e/tools/target_probe.py && git commit -m "feat(mobile): UX v2 B4-11 行车档布局全套——B/C 语音层常驻 + 答后 3s 回落 0.4、120dp 球、18/20pt、一屏一卡 DrivingCardSummary、chips ≤3、文本输入 A 常驻/B 折叠/C 隐藏、目标 56、过程区单行不可展开、横屏 40:60、上滑取消禁用；target_probe 读实" -- mobile/src/features/cards/DrivingCardSummary.tsx mobile/e2e/tools/target_probe.py mobile/src/core/presence/presence.ts mobile/test/presence.test.ts mobile/src/features/chat/usePresence.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/VoiceSheet.tsx mobile/src/features/chat/Composer.tsx mobile/src/features/chat/FocusDock.tsx mobile/src/features/chat/PresenceCapsule.tsx mobile/src/features/chat/FollowUpChips.tsx mobile/src/core/session/followUps.ts mobile/src/features/chat/MessageBubble.tsx mobile/src/core/presence/fixtures.ts mobile/test/presenceFixtures.test.ts && git show --stat HEAD
```

---

### Task 12: 播报收紧——主动消息语音仲裁接共享 `decideSpeech` + 行车档 `critical` 强制（方案 §6「播报收紧」、§5.6、Q18）

**Files:**
- 新建 `mobile/src/core/voice/proactivePolicy.ts`、`mobile/test/proactivePolicy.test.ts`
- 修改 `mobile/src/core/voice/speech.ts`（`proactive()` + `setProactiveCtx()` + DEFER 队列）、`mobile/src/core/session/store.ts`（`SpeechSink.proactive?` + `proactive` 分支调用）、`mobile/src/features/chat/ChatScreen.tsx`（ctx 下发 effect）、`mobile/src/app/debug.tsx`（本地回放按钮）

**为什么**：§6「播报收紧」：安全告警强制播报；普通问答遵循播报三档；非紧急主动消息在高驾驶负荷时静默（**治理器已按 >80 km/h 判，App 不重复判**）。事实核过（§0 第 6 条）：mobile 的主动消息**从不出声**——`store.ts:613-636` 只落气泡 + `proactive_ack`；网关 `main.go:543` 已透传 `priority`，共享 `@shared/proactiveSpeech.mjs::decideSpeech`（critical 抢话 / user_contract 排队 / 其余只气泡，`PendingSpeech` 有界去重队列）在 mobile 零消费方，而它**已在共享白名单**（`shared-allowlist.json:58`）。落点就是接上它，而不是发明第二套仲裁：mobile 的 ctx 从播报三档映射（`ttsEnabled = policy !== 'silent'`、`autoplay = policy === 'always'`——「自动」档主动消息**不**出声，与 §5.2 规则 8「自动 = 语音提问才播报」一致），之上只叠一条「行车档 + `critical` ⇒ 不看三档」（Q18：行车档只强制安全告警）。**普通轮的 VAL 拒绝 speech 不在这条里**——它是普通 `final`，走三档；协议没有「这句是安全告警」的结构化标记（Q16），客户端不猜。SessionCore 不读设置（头注约定），所以判定住 `SpeechController`（它本来就读设置），SessionCore 只调 sink 的可选 `proactive()`；`driving / s2sBusy` 两个事实由 ChatScreen 用 setter 喂给控制器（同 `setAudioUrl` 形态）。§5.6 的「Dock `task` 位短驻 6s + 为什么收到」是 U1 的账，**不在本任务**（§6.4 出账给主动消息批）。真机取证需要一条真的 `critical` 主动消息——云栈没有 proactive 注入入口（`grep -rn proactive scripts/ observability/collector/server.py` 先核一遍）就在 `/debug` 屏加「本地回放 proactive critical 帧」按钮走 `core.handleFrame`（零后端的取证装置，同 `/native-spike` 哲学）。

- [ ] **步骤 1：写失败测试**

`mobile/test/proactivePolicy.test.ts`：

```ts
// mobile/test/proactivePolicy.test.ts
// 主动消息播报仲裁（方案 §6 播报收紧 / §5.6 / Q18）：共享 decideSpeech 之上只叠「行车档 critical 强制」。
import { proactiveSpeechDecision } from '@/core/voice/proactivePolicy'

const alert = { priority: 'critical', hasText: true, hasCard: true }
const tip = { priority: '', hasText: true, hasCard: true }

test('行车档 + critical：静音档也说；S2S 忙 ⇒ 抢话', () => {
  expect(proactiveSpeechDecision(alert, { policy: 'silent', driving: true, s2sBusy: false })).toBe('speak')
  expect(proactiveSpeechDecision(alert, { policy: 'auto', driving: true, s2sBusy: true })).toBe('interrupt')
})

test('行车档 + critical 但没有文字 ⇒ 没东西可播，只气泡', () => {
  expect(proactiveSpeechDecision({ priority: 'critical', hasText: false, hasCard: true }, { policy: 'always', driving: true, s2sBusy: false })).toBe('bubble')
})

test('非行车：照共享判据——「自动」档主动消息不出声（§5.2 规则 8：自动=语音提问才播报），「总是」才说', () => {
  expect(proactiveSpeechDecision(tip, { policy: 'auto', driving: false, s2sBusy: false })).toBe('bubble')
  expect(proactiveSpeechDecision(tip, { policy: 'always', driving: false, s2sBusy: false })).toBe('speak')
  expect(proactiveSpeechDecision(tip, { policy: 'silent', driving: false, s2sBusy: false })).toBe('bubble')
})

test('非行车 + 总是 + S2S 忙：critical 抢话 / user_contract 排队 / 其余气泡（共享判据原样）', () => {
  const busy = { policy: 'always' as const, driving: false, s2sBusy: true }
  expect(proactiveSpeechDecision(alert, busy)).toBe('interrupt')
  expect(proactiveSpeechDecision({ priority: 'user_contract', hasText: true, hasCard: true }, busy)).toBe('defer')
  expect(proactiveSpeechDecision(tip, busy)).toBe('bubble')
})
```

- [ ] **步骤 2：跑红** ⇒ FAIL（模块不存在）

- [ ] **步骤 3：实现**

`mobile/src/core/voice/proactivePolicy.ts`：

```ts
// mobile/src/core/voice/proactivePolicy.ts
// 主动消息播报仲裁（B4-12 / 方案 §6 播报收紧、§5.6、Q18）——纯函数。
// 判据是共享的 @shared/proactiveSpeech.mjs::decideSpeech（critical 抢话 / user_contract 排队 / 其余气泡；
// 朗读判据 text && card 是验收更正过的口径）；这里只做两件事：① 把 mobile 的播报三档映射成它要的
// ttsEnabled / autoplay（「自动」档主动消息不出声——§5.2 规则 8「自动 = 语音提问才播报」）；
// ② 之上叠**唯一**一条 mobile 规则：行车档 + critical ⇒ 不看三档（Q18：行车档只强制安全告警）。
// 高驾驶负荷静默、免打扰、频控都在 proactive/governor.py，App 不重复判。
import { BUBBLE, DEFER, INTERRUPT, SPEAK, decideSpeech } from '@shared/proactiveSpeech.mjs'

import type { SpeakPolicy } from '../settings/store'

export type ProactiveDecision = 'speak' | 'interrupt' | 'defer' | 'bubble'

export interface ProactiveMsg {
  priority?: string
  hasText: boolean
  hasCard: boolean
}

export interface ProactiveCtx {
  policy: SpeakPolicy
  driving: boolean
  /** S2S 交互进行中（免唤醒 FSM 在 LISTENING/THINKING/SPEAKING 且挡位 s2s）：主动 TTS 与模型音频是两个播放器 */
  s2sBusy: boolean
}

export function proactiveSpeechDecision(msg: ProactiveMsg, ctx: ProactiveCtx): ProactiveDecision {
  if (ctx.driving && msg.priority === 'critical' && msg.hasText) return ctx.s2sBusy ? INTERRUPT : SPEAK
  const d = decideSpeech(msg, { ttsEnabled: ctx.policy !== 'silent', autoplay: ctx.policy === 'always', s2sBusy: ctx.s2sBusy })
  return d === SPEAK || d === INTERRUPT || d === DEFER ? d : BUBBLE
}
```

`mobile/src/core/session/store.ts`：`SpeechSink` 加
```ts
  /** 主动消息（B4-12）：仲裁在实现里（读设置 + 行车事实），SessionCore 只报事实；可选——M2 起的四方法实现照旧可用 */
  proactive?(text: string, msg: { priority?: string; hasCard: boolean }): void
```
`proactive` 分支 `appendMessage` 之后加：`this.speech.proactive?.(text, { priority: typeof data.priority === 'string' ? data.priority : undefined, hasCard: !!card })`（`FakeSpeech` 不实现也过——可选方法）。

`mobile/src/core/voice/speech.ts`：
- import `PendingSpeech` from `'@shared/proactiveSpeech.mjs'`、`proactiveSpeechDecision`；
- 字段：`private proactiveCtx = { driving: false, s2sBusy: false }`、`private readonly deferred = new PendingSpeech()`（构造参数以 `proactiveSpeech.mjs:46` 为准）；
- `setProactiveCtx(ctx: { driving: boolean; s2sBusy: boolean }): void { this.proactiveCtx = ctx }`；
- `proactive(text, msg)`：
  ```ts
  proactive(text: string, msg: { priority?: string; hasCard: boolean }): void {
    const policy = settingsStore.getState().settings.speakPolicy
    const d = proactiveSpeechDecision({ priority: msg.priority, hasText: !!text, hasCard: msg.hasCard }, { policy, ...this.proactiveCtx })
    if (d === 'bubble') return
    if (d === 'defer') { this.deferred.push(text /* 按 PendingSpeech 的入参形状 */); return }
    if (d === 'interrupt') this.stop()
    void this.speakBatch(text)
  }
  ```
- `setSpeaking(false)` 之后（`onEnd` 与 `stop` 都会到）补播：`const next = this.deferred.shift(); if (next) void this.speakBatch(next)`（`PendingSpeech` 的出队方法名照源码）。

`ChatScreen.tsx`：
```tsx
  // B4-12 主动消息仲裁的两个事实喂给播报控制器（它读设置，但不认识行车与 S2S）
  useEffect(() => {
    const s2sBusy = settings.voicePipeline === 's2s' && (hf.fsm === 'LISTENING' || hf.fsm === 'THINKING' || hf.fsm === 'SPEAKING')
    speechController().setProactiveCtx({ driving: snapshot.driving, s2sBusy })
  }, [snapshot.driving, hf.fsm, settings.voicePipeline])
```

`mobile/src/app/debug.tsx`：加一个按钮「本地回放 proactive critical 帧」→ `getWired()?.core.handleFrame({ type: 'proactive', priority: 'critical', speech: '后备箱没有关好', card: { type: 'scene_card', title: '后备箱未关' }, delivery_ids: ['local-' + Date.now()] })`（帧形状照 `store.ts:613` 分支读的键；`delivery_ids` 唯一才不被幂等吞掉；卡型取注册表里有的一个）。

- [ ] **步骤 4：跑绿 + tsc + 全量** ⇒ +4（472 → 476），0 error。

- [ ] **步骤 5：反向验证**

① 行车强制那行删掉 ⇒ **恰好红**第一条（静音档也说）；② `autoplay: ctx.policy === 'always'` 改成 `!== 'silent'` ⇒ 恰好红「自动档不出声」；③ 共享 `decideSpeech` 的三条（抢话 / 排队 / 气泡）——mobile 只是透传，红了说明共享判据变了、先看 `proactiveSpeech.mjs` 再说。

- [ ] **步骤 6：真机取证**（三档 × 行车两态，每格记决策与 `dumpsys audio` 的 `AudioPlaybackConfiguration … state:started`）

| 档 | 行车 | 回放 critical ⇒ 预期 |
|---|---|---|
| 自动 | 关 | 只气泡（`💡` 气泡在、无播放） |
| 自动 | 开（手动） | **出声**（行车强制） |
| 静音 | 开 | **出声**（行车强制压过静音） |
| 总是 | 关 | 出声（共享判据） |

反例：回放一条 `priority` 为空的（改 debug 按钮参数）⇒ 行车档下也只气泡。S2S 忙的 INTERRUPT / DEFER 两路真机不造（要泓舟 S2S 真人轮同时来主动消息，条件太苛刻）——jest 钉住、记 ⬜ 真机未验。

- [ ] **提交**

```bash
git add -- mobile/src/core/voice/proactivePolicy.ts mobile/test/proactivePolicy.test.ts && git commit -m "feat(mobile): UX v2 B4-12 主动消息语音仲裁接共享 decideSpeech（三档映射：自动档不出声）+ 行车档 critical 强制（Q18）+ DEFER 队列补播 + SessionCore 可选 sink.proactive + debug 屏本地回放装置" -- mobile/src/core/voice/proactivePolicy.ts mobile/test/proactivePolicy.test.ts mobile/src/core/voice/speech.ts mobile/src/core/session/store.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/app/debug.tsx && git show --stat HEAD
```

---

### Task 13: B4 真机验收——§11.2 B4 四条 + 形态矩阵五截图 + Reanimated 告警定位 + shutter 盲测 + B2 两格 + 无障碍与性能读数（方案 §11.2 B4、§11.4）

**Files:**（取证与裁决；撞上缺陷就修并记 §6.3。本任务里唯一预期的代码改动是步骤 9 的触感映射一行 + 步骤 8 若定位到可修的组件）

**为什么**：§11.2 B4 四条里三条只有真机给得出（≥56dp 读实、VAL 拒绝的「无全屏层」、reduce-motion 静帧），第一条（五截图矩阵）有两张要泓舟手折。行车 `driving` 帧走既有云栈 debug 注入（零后端）。每条读数都写清「取法 + 图名 + 阴性对照」——B1–B3 的教训是只验正例的那半会漏缺陷。**语音类读数（步骤 2 的提示音、步骤 11 的语音轮）带包锚 `lastUpdateTime`**。

- [ ] **步骤 0：前提**：`lastUpdateTime` = `2026-09-02 16:45:36`（不是就先问为什么）；`dev_stack target show` = cloud；`adb reverse --list` 非空；Tools 按钮已关；扬声器音量 `dumpsys audio` 的 `STREAM_MUSIC Current` 非 0（B3 坑⑨）；设置基线记一行（角色 / 行车档 / 提示音 / 触感 / 播报 / 免唤醒 / 主题 / 实验室三开关）。

- [ ] **步骤 1：行车 `driving` 帧——云栈 debug 注入（§11.2 B4 ② 的前提）**

```powershell
$fqdn = (Select-String -Path .env -Pattern '^TAILNET_FQDN=' | ForEach-Object { $_.Line.Split('=')[1].Trim() })
Invoke-RestMethod -Method Post -Uri "https://$fqdn`:8446/api/debug/vehicle" -ContentType 'application/json' -Body '{"key":"speed_kmh","value":30}'
Invoke-RestMethod -Uri "https://$fqdn`:8446/api/vehicle/state"      # 回读 speed_kmh=30（M3-2 在云栈用过同一端点）
```

（`apply_debug` 的白名单是 `speed_kmh / battery / gear / location / cabin_temp`，`server.py:169`；云栈若返回 403/404 = `DEBUG_VEHICLE_CONTROL` 关着——那是云配置，不在 B4 改：记 ⬜，其余步骤用手动行车档。）
设备：角色 C、行车档**关**（要看 Edge 触发）、免唤醒开 → 说一句会出过程区的复杂任务（「帮我规划从深圳到广州的充电路线」；`/debug` 屏看到 `type:process … driving:true` 才算——简单轮没有这个标，§0 第 6 条）⇒ `/presence-trail` 的 `driving` 轴翻 true、语音层常驻、目标放大（`b4-13-driving-edge.png`）。**退出**：`speed_kmh=0` + `gear=P` 回读 → 再说一句复杂任务（frames 带 `driving:false`）→ 轨迹里 30s 后 `driving` 翻 false（时刻差记下来，判据 `DRIVING_EXIT_GRACE_MS`）。做完 `speed_kmh` 还原 0 并回读。

- [ ] **步骤 2：提示音（T4 的人耳半，需泓舟）**：`/native-spike` 两个按钮由我按泓舟不知道的顺序各 3 次（B3 T9 的盲测协议：注入前 Maestro 断言按钮可见、注入后核 `dumpsys audio` 有 `state:started`）⇒ 泓舟分组 ≥5/6 算「两种可辨」；挂点面：免唤醒开、说唤醒词 ⇒ **先**两音上行**再**「在听…」；「打开后备箱」⇒ Dock 出现同时两音下行；阴性：PTT 长按进 listening **无**提示音；设置关「提示音」⇒ 两挂点都无声、行车档开 ⇒ 关着也响。包锚记 `lastUpdateTime`。

- [ ] **步骤 3：目标 ≥56dp 读实（§11.2 B4 ②）**：行车档开（手动，角色 C）、造齐演员——「打开后备箱」（Dock 两钮）+ 一轮带 chips 的语音轮（层开着）⇒ `maestro hierarchy > mobile/e2e/artifacts/b4-13-h.json` → `python mobile/e2e/tools/target_probe.py b4-13-h.json --density <wm density> --min 56 composer-orb composer-send dock-accept dock-cancel followup-chip voice-sheet-collapse driving-card-button presence-capsule` ⇒ 全 PASS（`presence-capsule` 视觉 26dp 靠 hitSlop——hierarchy 给的是视觉 bounds，这一个 FAIL 是**读法的限制**不是缺陷，单列说明）；**阴性**：行车档关 ⇒ `composer-send` 44dp FAIL（探针量得出差别才算探针活着）。Scanner 读数见步骤 13。

- [ ] **步骤 4：文本输入按身份（②）**：行车档开，角色 A / B / C 各一张（`b4-13-input-{A,B,C}.png`）：A 输入框常驻；B 键盘按钮 56dp、点开输入框；C 无输入框。回读角色设置。

- [ ] **步骤 5：过程区单行（②）**：行车档开 + 复杂任务 ⇒ 过程中截图 `b4-13-process-driving.png`：过程区一行「⟳ {label}…」；点它**不展开**；结束后「过程 N 步」不可展开。阴性：行车档关同一语料 ⇒ `▸ 过程 N 步` 可展开。

- [ ] **步骤 6：VAL 拒绝（③，两半分开记）**：`speed_kmh=90` 注入并回读 → 行车档开 → 说「打开天窗」⇒
  - **可验的半**：① **无全屏层**（`b4-13-val-reject.png`：对话记录 / Composer 仍可见，语音层 ≤78% detent）；② UI **没有**据 driving 加任何限制（若有确认卡，两钮都在、都能点）；③ 回复原话逐字记进 §6.3——它要么是 VAL 的 `_safety_gate` 话术（「高速行驶中请勿打开车窗/天窗」类），要么是手机档 token 无 `vehicle.control` 的权限拒绝话术；**后者说明手机档根本到不了 VAL 门控**，这条验收在手机档就只剩「无全屏层」可验，如实写；
  - **不可验的半**：Dock `safety_blocked` 无产出方（协议无结构化标记，B1 验收表第 7 条同一状态）⇒ **⬜ 待 Q16**，不许客户端正则猜（§5 第 2 条）。
  做完 `speed_kmh=0` 回读。

- [ ] **步骤 7：reduce-motion → 光球静帧（④）**：实验室开「减少动效（强制）」⇒ 对话页 Composer 球区两帧（间隔 1s）`png_probe` diff **0.00%**、层开后层内大球两帧 0.00%、`EdgeGlow` 常亮（两帧顶缘同色）、思考点静止；**S4 的静态双青环仍在**（speaking 时）；阴性：关开关 ⇒ 同法 diff >0（通道活着）。系统路径（`settings put global animator_duration_scale 0`）**需泓舟授权**——做了就再取一组、做完还原 1 并回读。

- [ ] **步骤 8：形态矩阵五截图（①）**：外屏竖 / 外屏横 / 内屏（`device_state 3`）/ 内屏 book / tabletop——每张配 `cmd device_state print-state`、物理 displayId、`/native-spike` 的 `layout` 行截图（T6 / T7 已取且 SHA 未变的可引用，变了重取）。

- [ ] **步骤 9：`Reanimated: synchronouslyUpdateUIProps failed` 定位（B3 §6.3 遗留⑤；上限 90 分钟，到点出账）**：宿主侧 `adb logcat -b all > b4-13-rea.log` 常驻，分段计数（每段 3 分钟）：① `/state-gallery?only=idle`（1 颗动球）；② `/state-gallery?only=zzz`（0 颗球、0 样本）；③ 对话页层开（Composer 静、层内 1 颗）；④ 实验室减少动效开（全静）。四段的 条/分 摆在一起先定「跟不跟动球走」。跟 ⇒ 在 `AuroraOrb.tsx` 上做**不提交的**逐层排除（各热载一次、各 3 分钟）：a. 去掉三层 `filter: [{ blur }]`；b. 去掉玻璃球体层的 `boxShadow`；c. 把 `experimental_backgroundImage` 层从 `Animated.View` 换成静态 `View` 包一层 `Animated.View` 只做 transform。命中哪一步就是元凶；修法若能保住 B3 §6.1 的 S4 / S6 读数（复跑那两组 `png_probe`）与光球十条不变量，就修并提交；否则**出账带组件与 prop 名**。不跟动球走 ⇒ 记四段读数出账，不再往前推。

- [ ] **步骤 10：`shutter` ≡ `wake`（B3 §6.3 遗留③）**：`core/haptics.ts` 的 shutter 换 `Haptics.performAndroidHapticsAsync(Haptics.AndroidHaptics.Virtual_Key)`（B3 T9 实测本机支持 VIRTUAL_KEY、不支持 REJECT/CLOCK_TICK）；HAL 层核波形（`vib.py`）确实与 wake 的 `[50]ms@30` 不同；盲测 wake / shutter 各 3 次打乱（B3 协议）⇒ ≥5/6 保留并提交（`fix(mobile): B4-13 shutter 触感换 VIRTUAL_KEY…`），否则**还原**并出账 B5（自写波形要原生）。

- [ ] **步骤 11：B2 两格（非闸门）**：① 表#6「这是什么」⇒ 用户气泡**先于**相机：`screenrecord //sdcard/b4-13-vision.mp4` + `ffmpeg -vf fps=30` 重采样 → 气泡帧号 < `logcat -s CameraService` 的 `connect` 时刻对应帧号（B2 T10 方法）；② 「换一批」chip：免唤醒开 → 说「附近的川菜馆」（B3 §6.3 步骤 8 实测出正牌 `poi_list` 且带 keyword）→ 答完、追问窗内层仍开 ⇒ `followup-chip` 里有「换一批」（截图 + `maestro hierarchy` 找 `followup-chip` 文本；**别用宽松正则**——B3 坑：`.*换一批.*` 会匹配到卡里的提示文字）。包锚记 `lastUpdateTime`。

- [ ] **步骤 12：Maestro 回归**：06 / 07 / 08 / 09 各 rc=0；05 是 manual、无人说话必红（B3 §6.1 遗留③）——不跑或记明原因。

- [ ] **步骤 13：无障碍读数（§11.4）**：Android Accessibility Scanner **装 APK 到泓舟设备要授权**（B1 出账⑦ 至今 ⬜）——授权则跑对话页（泊车 / 行车各一次）记严重项数与清单；不授权记 ⬜，`target_probe.py`（步骤 3）与 `maestro hierarchy` 的 label 检查作替代读数，**明标不是 Scanner**。TalkBack 四条（T9 步骤 4）需泓舟开。

- [ ] **步骤 14：性能（§11.4）**：语音层开（真模糊在）+ 滚动记录 ⇒ `framestats`（本机 120Hz，先读 24 列表头；B3 §6.3 T9 步骤 3 的 A/B 口径：同配置两趟的噪声底 5ms / 0.65pp）呈现间隔中位 / p90 ≥55fps 等价（≤18.2ms）；同屏循环动画实例：层开 Composer 球两帧 diff 0.00%（B3 T1 口径）；tabletop 是刻意的例外（舞台大球 + Composer 球——T7 让 Composer 静止，仍 1 个）。

- [ ] **步骤 15：设备与云栈还原表**（每项「改成 / 还原为 / 回读方式」，B3 §6.3 遗留⑧ 格式）：`speed_kmh` / `gear`（云栈）；角色 / 行车档 / 实验室三开关 / 提示音 / 播报 / 免唤醒 / 主题（App）；`device_state reset`、`wm size reset`、系统动画缩放与旋转（若动过，需泓舟授权那两项）。

---

### Task 14: 5 人外部小样本——§11.4「状态可读性」第一份分布读数（做不做交泓舟裁；本任务只保证有位置、有协议）

**Files:**（材料 `mobile/e2e/artifacts/b4-sample/`，gitignore；读数进本文件 §6.4）

**为什么**：§11.4「状态可读性：5 名外部用户各看 6 张状态截图说出它在干嘛，≥5/6」至今**没有一次外部分布读数**——B2 闸的那格是泓舟自评 6/6（1 人、非外部），B3 原样转出。B4 是视觉大批（形态 / 行车 / 材质 / 图标），是取这份基线最该取的时候。它要泓舟找 5 个**不在项目里**的人，所以做不做由他裁；计划的责任是让它可以立刻执行：材料、协议、计分、记录格式都写死，不留「到时候再想」。

- [ ] **步骤 1：材料（单人可备）**：真机截图 8 张（深色主题，外屏竖）：`idle / armed / listening / thinking / speaking / attention`（六态，来自对话页真实状态，不是画廊——画廊没有胶囊位置关系）+ 行车档两张（`行车 C 常驻层` vs `泊车 同一轮`）。每张裁掉状态栏与顶栏以外的无关信息，**不裁胶囊、不裁 Dock**。文件名只编号（`s1..s8.png`），不带态名（标签会泄题）。
- [ ] **步骤 2：协议**：每人独立、不看别人答案；逐张问同一句「它现在在干嘛？」自由作答（不给选项——给选项量的是识别不是理解）；行车两张追问「哪张是开车时用的？为什么？」；记录**原话**。
- [ ] **步骤 3：计分**：六态每张判「对 / 错」——对 = 说出该态的语义（空闲 / 等唤醒·待命 / 在听 / 在想·处理 / 在说·播报 / 等我确认），同义即对；每人得分 x/6，五人分布 `[x1..x5]`，均值 ≥5/6 算达标；**错的原话逐条列出**——它们是 B5 视觉批的输入，比分数值钱。行车两张：5 人里几人选对 + 理由分类（目标大 / 没有输入框 / 单行 / 球大）。
- [ ] **步骤 4：记录**：§6.4 一张表（人 × 图）+ 错答原话 + 达标判定；不做则写「泓舟裁定不做，§11.4 状态可读性仍无外部基线」，**别写「裁定算过」**（B2 那句就是这么被误读成有读数的）。

---

### Task 15: 记录收口

- [ ] **步骤 1：文档五处**
  1. `mobile/e2e/README.md`：追加「B4」一节——流 07 的跑法（`device_state 3` / `reset` 包着跑、tag manual）、`target_probe.py` 用法、行车 `driving` 帧的云栈注入命令、四个实验室开关在取证里的用途；
  2. 主计划 `2026-08-24-mobile-app-implementation-plan.md`：B3 指针块下加同款 B4 指针段（实施记录在本文件 §6）；**§9 坑账追加本批新坑**（撞几条记几条，编号接 58 往后排）；
  3. `docs/design/README.md`：本计划行状态改「四批收口 + §11.2 B4 四条一句话 + 小样本一句话 + 两个待裁项的裁决结果」；
  4. `AGENTS.md` §4.2 Android 行：**只改指针段**（B4 收口 + 仍开的活项 + 下一步 B5 / 待裁项）——动前 `git diff --stat -- AGENTS.md` 核行数、单独 commit；
  5. 本文件 §6.4：读数、坑、遗留出账表（含 B3 出账与 AGENTS ⑥ 逐条的去向核销：`charging_list` 型名与 `_prov` → hmi / agent 侧；`safety_blocked` → Q16；§5.6 Dock task 位 → 主动消息批；B3′ → B5；低电量回落 → 有 expo-battery 的重建趟）、**未推送清单**（只列构成不写死条数）。
- [ ] **步骤 2：全量收口**：`cd mobile && npm test && npm run typecheck` 终值写 §6.4（条数账逐任务对得上、只增不减）；`python scripts/run_e2e.py --target cloud` 跑一次冒烟（零后端改动，廉价保险、不当 canonical）。
- [ ] **提交**：

```bash
git commit -m "docs(mobile): UX v2 B4 收口——四批读数与遗留出账（§6）+ README/主计划指针 + 坑账追加" -- docs/design/2026-09-02-mobile-ux-v2-b4-implementation-plan.md docs/design/README.md docs/design/2026-08-24-mobile-app-implementation-plan.md mobile/e2e/README.md && git show --stat HEAD
# AGENTS.md 单独一个 commit（所有会话都在写的文件）：
git diff --stat -- AGENTS.md && git commit -m "docs(agents): Android 行指向 UX v2 B4 收口" -- AGENTS.md && git show --stat HEAD
```

---

## 3. 任务依赖与并行度

```
第 1 批：T1 尺寸类 ∥ T3 动效 ∥ T5 舞台场景        （互不相干的新文件；settings/store.ts 各加键——串行提交）
         T2 行车判据 ─► T4 提示音                    （T4 的「行车强制开」读 T2 的 snapshot.driving）
第 2 批：T6 布局落地 ─► T7 姿态消费 ─► T8 材质 ∥ T9 图标+a11y   （T8/T9 都动 VoiceSheet.tsx——串行提交）
第 3 批：T10 设置+建议 ─► T11 行车布局 ─► T12 播报收紧 ─► T13 真机验收
第 4 批：T14 小样本（泓舟裁）─► T15 收口
```

- **第 1 批只写判据不动布局**：五份纯函数落盘后 Metro 热载只做冒烟——第 1 批结束时 App 的样子应与 B3 收口时**一模一样**（`usePresence` 的 `driving` 改读判据在没有 Edge 标、手动关着时恒 false；提示音 effect 在免唤醒关着时永远不触发）。
- **T6 是第 2 批的门**：`tablet` 布尔换掉、`StagePane` 抽出后 T7 才有地方放 tabletop；T8 的 `BlurTargetView` 包的是 T6 重排后的列表容器。
- **T10 → T11 → T12 串行**：三者都动 `presence.ts` / `ChatScreen.tsx`。
- **T13 在第 3 批最后**：真机读数不许跨改动转借（证据绑 SHA）；步骤 9 / 10 若产生提交，涉及的读数（S4 / S6 / 触感）当场复取。
- **T7 步骤 5 与 T13 步骤 2 / 8 / 10 需泓舟在场**：可以合并成一次真机会——但读数按任务名记，不合并成一段。

## 4. 「不负优化」判据在 B4 的取数点（方案 §11.4）

| 判据 | B4 取数 |
|---|---|
| 首反馈时延 | 不复取（B2 3ms、B3 同口径；B4 没动唤醒→listening 那条链）。提示音若让人感觉「慢了」——它挂在 effect、与触感同帧，T13 步骤 2 顺带记两音起点与「在听…」胶囊的先后 |
| 状态可读性 | **T14**：第一份外部分布读数（做不做交裁；不做就写「仍无外部基线」，不写「裁定算过」） |
| 记录完整性 | 不复取（B4 没动记录侧；T12 的主动消息仍走 `appendMessage`，条数不变） |
| 承诺不丢 | Maestro 06 回归（T6 / T13）；行车档下 Dock 按钮 56 仍两钮都在（T13 步骤 6 ②） |
| 键盘遮挡 | Maestro 08 回归（T6 步骤 6 分屏态 + T13 步骤 12） |
| 性能 | **T13 步骤 14**：真模糊在场的 `framestats`（120Hz 表头、A/B 噪声底口径）≥55fps 等价；同屏循环动画常态 1 个（层开 Composer 静 0.00%；tabletop 例外已让位） |
| 无障碍 | **T13 步骤 13**：Scanner 需授权（不授权 ⬜ + `target_probe.py` 替代读数明标）；TalkBack 四条需泓舟；`maestro hierarchy` 的 label 检查单人可做 |
| 回归 | `npm test` 条数账：413 →(T1 +13)→ 426 →(T2 +18)→ 444 →(T3 +5)→ 449 →(T4 +7)→ 456 →(T5 +7)→ 463 →(T8 +2)→ 465 →(T10 +2)→ 467 →(T11 +5)→ 472 →(T12 +4)→ **≈476，只增不减**；`hmi/` 零改动（`git diff --stat -- hmi/` 每批收口核一次）；Maestro 06/07/08/09 rc=0 |

## 5. 实施判断（写在开工前，做的时候撞到再补）

1. **行车档的事实只有 Edge 的 `driving` 标与手动开关**。`vehicle_state` 帧里的 `speed_kmh / gear` 只许显示（车辆页、舞台三格），不许判行车——`server.py::_is_driving` 是唯一裁决点（§5.3.1 删掉的正是 UI 自己的车速规则）。代价是简单轮不带标、行车档只在复杂任务后进入；这是协议的形状，不是客户端该补的洞（要更早的标注是 Edge 在 `final` 上也打 `driving`，后端挂账，不在 B4）。
2. **§11.2 B4 ③ 拆成两半**：「无全屏层 + UI 不据 driving 加限制」可验；「Dock `safety_blocked` 显示 VAL 原话」在零后端改动下**没有产出方**（协议无结构化标记，B1 验收表第 7 条同一状态）——记 ⬜ 待 Q16，不许客户端正则猜「这句是不是 VAL 拒绝」。另外手机档 token 没有 `vehicle.control`，「打开天窗」可能在权限层就被拒、根本到不了 VAL 门控——T13 步骤 6 把回复原话记下来，两种情形分开写。
3. **真模糊只落语音层外壳**：它压在可控内容（变暗的对话）上；顶栏与舞台压在静态深空底上糊了没收益，且 §5.11 禁同屏多个动态 Blur。`tintOverBlur = 0.40` 是待证参数（T8 步骤 5 的两条文字带读数裁，不过就 .50 → .58）。
4. **`charging_list` mobile 不注册**：它不在 `types.ts` 的 `UiCard` 联合里，`cards.test.ts` 两向断言、注册即红，而 `hmi/` 不碰。落点是通用列表兜底（任何未登记的列表卡都受益）；型名进 types.ts、HMI 渲染器、agent 补 `_prov` 是 hmi / agent 侧的账。
5. **提示音的两个自决**：① 唤醒确认音只给 ARMED→LISTENING（含轻点光球的手动唤醒），PTT 按下与追问窗开口都不响——§4.2 只给「唤醒」配了提示音；② `attention` 的音型方案没定，取两音下行 G5→E5（与唤醒的上行成对，「问你一句」的语气）。泓舟一句话可改，改的是 `cueTone.ts` 的 `TONES` 表。
6. **身份 C 可选但不承诺可控车**：App 判不了 token scope（不透明串、无查询端点），选 C 也不会多出权限（服务端按 token 裁）；说明行写「车控由服务端 token 决定，这里只决定布局」。Q15 的绑定另议。
7. **v1 回滚代码 B4 不删**：方案 §11.5「B4 稳定后再删」——稳定的判据是 B4 收口 + 至少一轮真机使用没回滚，删是之后另立的一条提交（含 `HF_LABEL / HF_DOT / legacyHint / legacyOrb` 与气泡内确认那一路）。
8. **`predictiveBackGestureEnabled: false` 不动**：原生配置、改了要重建。返回顺序只做 JS 侧 `BackHandler`；预测性返回的系统预览动画不在本批。
9. **低电量回落不做**：§5.11 末句的三种回落只落两种（减少透明度 / 行车档）——第三种要 `expo-battery`，新原生依赖，等有重建趟再议。
10. **B3 被推翻的两条判据按新前提写**：VAD 探针是回环（不再当「不经 AEC 的直灌」引用）；触感在 expo-haptics 上只给得出三个波形（T13 步骤 10 的 shutter 候选是 `AndroidHaptics.Virtual_Key`，不是再调 impact 参数）。
11. **`stageScene` 是视图选择的第二份实现，明说**：与 HMI 的差异只有一处（最近一张卡永远进舞台，非场景型 → focus），映射表由测试从 hmi 源码逐字对账。
12. **tabletop 的分界用 `measureInWindow`**：`FoldingFeature.bounds` 是窗口坐标，`onLayout` 给的是相对父级的 y，两者混用分界会偏一个顶栏高度。
13. **双栏阈值 720 按实测 dp 验**：T6 步骤 1 先 `wm size / wm density` 读实再改测试里的数；若内屏实测落到 720 以下，那是判据要重新裁（交泓舟），不是把阈值改到设备上。
14. **≥56dp 的两种读法分开记**：Scanner（装 APK 要授权）与 `maestro hierarchy` bounds（`target_probe.py`）——后者明标「不是 Scanner 读数」；胶囊靠 hitSlop 达标，hierarchy 量不到，单列说明。
15. **「语音层常驻」与「答后 +3s 收起」的合并**：常驻 = 层不消失（B/C）；收起 = 内容回落到 0.4（只球 + 胶囊）。A 身份不常驻（可能乘客在打字）。用户下拉仍可收（常驻不是不可收，下一轮再升）。
16. **主动消息仲裁只叠一条规则**：共享 `decideSpeech` 原样（critical 抢话 / user_contract 排队 / 其余气泡；text && card 才朗读），mobile 三档映射「自动」为不出声，行车档 + critical 不看三档。普通轮的 VAL 拒绝 speech 走三档——「这句是不是安全告警」协议里没有，客户端不猜。§5.6 的 Dock task 位与「为什么收到」不在 B4。
17. **两个待裁项不进任务**（§0 第 3 条）：唤醒率调阈值要独立 A/B 批；播报卡顿要一趟无 AEC 对照构建（违背零重建）。B4 的语音读数若捎带唤醒率只记数不调参。
18. **组件层没有 jest 面**：T6–T12 的组件改动判据全是真机读数（截图 / `png_probe` / `maestro hierarchy` / `framestats`），每条配阴性对照；能提成纯函数的（T1–T5、T10/T11 的 presence 分支、T12 的仲裁）都提了。

## 6. 实施记录（分批回填；每批一个会话，写完即停）

> 格式照 B1–B3 计划 §6：先**开工基线**（自己跑出来的数——jest 条数、tsc、`git log origin/main..HEAD` 计数、设备在线、`lastUpdateTime`、`dev_stack target show`、设备 App 设置基线）、再逐任务的提交与读数、再**反向验证**逐条、再「本批踩的坑」、最后「遗留 / 给下一批的话」。读数只写自己跑出来的数，不复述计划预期；**未跑的一律不写 ✅**；语音类读数必须带 `lastUpdateTime` 锚；改过的设备 / 云栈状态要有还原表。

### 6.1 第 1 批「判据层」（T1–T5）

**本批开工授权**：泓舟 **2026-09-02** 当轮批准第 1 批开工——计划头部「草案待批（2026-09-02）」对本批以本条为准（头部状态行本批不改，四批走完再由收口批统一改）。

#### 开工基线（2026-09-02，本会话自跑；有效期只到下一次改动）

| 项 | 读数 |
|---|---|
| `powershell -File scripts\check_android_env.ps1` | 退出码 **0**（17 pass / 1 warn / 0 fail；唯一 WARN = E3 `no device attached`） |
| `python scripts/dev_stack.py target show` | `{"source":"file","status":"target","target":"cloud"}` |
| `cd mobile && npm test` | **42 suites / 413 tests 全绿**（72.7s） |
| `npm run typecheck` | **0 error** |
| `git log --oneline origin/main..HEAD` | **0 条**；`HEAD` = `origin/main` = `e248deb`；`git status` 干净 |
| `adb shell dumpsys package com.xiaozhou.companion \| grep lastUpdateTime` | ⬜ **未取——设备不在线**（下一条） |

**设备缺席（开工第一件没做成的事）**：`adb devices` 是空列表；按 B3 §0 的补救做了 `adb kill-server && adb start-server` 重新枚举 USB，**仍空**。tailnet 上手机在线（`tailscale status`：`100.78.231.58  superdemanxiaomi-mix-fold-4  android`），但 `adb connect 100.78.231.58:5555` 被拒（`10061 目标计算机积极拒绝`——无线调试没开、`adb tcpip` 也没设过）⇒ **USB 物理没插，adb 这条路今天没有**。后果只有一种：**本批全部真机动作未做**（批次级步骤 4 的 Metro 热载冒烟 + T2/T3/T4/T5 各自的步骤 6），逐条记在「遗留」里。代码侧判据不依赖设备（T1–T5 全是纯函数 + jest），**但「没有回归」这一半证据本批没有**。

#### 逐任务

| 任务 | 提交 | jest 条数 | tsc |
|---|---|---|---|
| T1 尺寸类与布局判据 | `db97a33` | 413 → **426**（+13，`sizeClass.test.ts`） | 0 |
| T2 行车档事实与判据 | `156b2c6` | 426 → **446**（+20：drivingMode **15** + sessionStore 3 + settingsMeta 2） | 0 |
| T3 动效策略 | `b791414` | 446 → **451**（+5） | 0 |
| T4 提示音 | `b3c65d4` | 451 → **458**（+7） | 0 |
| T5 舞台场景 + 兜底卡 | `d7029f7` | 458 → **466**（+8，含反向验证补的 1 格） | 0 |
| **收口全量** | — | **47 suites / 466 tests 全绿** | **0 error** |

计划预期是 413 → ≈463；实到 **466**，差在 T2 的 `test.each` 四格（计划按 1 条估）与 T5 反向验证补的 1 格。只增不减达标。

#### 反向验证（每条先 `grep` 证明变异落盘，跑完 `cp` 还原并复跑全绿）

| 任务 | 变异 | 落盘证据 | 实际红的是哪几条 | 与计划预期 |
|---|---|---|---|---|
| T1① | `i.width >= TWO_PANE_MIN_WIDTH` → `>= 840` | `sizeClass.ts:60` | 「内屏 847×948 → 双栏；密度 440 时 809 也双栏」+「720 是内容约束的边」 | ✅ 一致 |
| T1② | 删掉 `posture` 两行 | `grep -c` = 0 | 「姿态压过尺寸」 | ✅ |
| T1③ | `heightClass` 边界 480 → 600 | `sizeClass.ts:23` | **只有「高度三档边界」1 条** | ⚠ **不一致**：计划以为 layoutMode 那边也会红。实测 layoutMode 的六个 height 用例，compact 侧全部 `< 480`、expanded 侧全部 `≥ 900`——**480 这条边只有 heightClass 单测钉着，layoutMode 层零敏感** |
| T1④ | `bookSplit` 的 `gap / 2` → `gap` | `sizeClass.ts:81` | 「book：铰链落 gap 正中」 | ✅ |
| T2① | `drivingActive` 末行 `<` → `<=` | `drivingMode.ts:24` | 「Edge 标 false 后 30s 内仍算行车，满 30s 退出」 | ✅ |
| T2② | 删 `prev.falseAt > prev.trueAt` 守卫 | `grep -c` = 0 | **两层同时红**：`drivingMode`「之后的 false 不刷新起点」+ `sessionStore` 同名用例 | ✅ 这正是「store 真的走了这个 reducer」要的形状 |
| T2③ | store 的 `setState` 登记注释掉 | `store.ts:506` | sessionStore **2 条**（trueAt / falseAt），drivingMode 全绿 | ⚠ 计划写「三条全红」。第三条「从没行车过的 false 不登记」**仍绿**——它的期望 `{trueAt:0,falseAt:0}` 恰好等于「没人登记」的初值，**对「登记接没接上」零敏感**，只钉 reducer 语义 |
| T2④ | `composerInputMode` 的 mount / trusted-tablet 对调 | `drivingMode.ts:40` | 「行车：A 常驻 / B 折叠 / C 隐藏」 | ✅ |
| T3① | 删 `orbTempo` 的 reduceMotion 分支 | `grep -c` = 0 | 「orbTempo：静帧 > 行车 ×0.5 > 全速」 | ✅ |
| T3② | `edgeGlowActive` 的 `thinking` → `speaking` | `orbPolicy.ts:32` | 「edgeGlowActive：只在 listening / thinking」 | ⚠ 计划要的「两侧都红」**看不出来**：正例与反例写在同一个 `test()` 里，jest 在第一个失败断言（`thinking` 正例）就停，`speaking` 反例那半根本没执行 |
| T3③ | `composerOrbAnimated` 忽略 `env` | `orbPolicy.ts:25` | 「reduce-motion 压过一切」 | ✅ |
| T4① | `wake` 分支去掉 `prev.hfFsm === 'ARMED'` | `soundCue.ts:17` | 「唤醒确认音…持续 LISTENING 不再响」+「追问窗里开口不响」 | ⚠ **点名错了**：计划说会红「PTT 按下不响」，实测它**仍绿**——PTT 那条 `next.hfFsm` 是 `IDLE`，挡住它的是 `next.hfFsm === 'LISTENING'` 那一半，不是 ARMED 守卫。ARMED 守卫真正钉住的是「持续 LISTENING 不重复响」 |
| T4② | `attention` 去掉 `prev.primary !== 'attention'` | `soundCue.ts:18` | 「attention 进入响一次；持续不响」 | ✅ |
| T4③ | `cueToneAllowed` 的 `\|\|` → `&&` | `soundCue.ts:24` | 「cueToneAllowed：设置关就不响，除非行车档」 | ✅（挂在第一个断言 `(true,false)`，不是计划说的第三个） |
| T5① | `STAGE_MAP_TYPES` 少写 `trip_itinerary` | `stageScene.ts:12` | 「对账：与 hmi `MAP_TYPES` 逐字一致」 | ✅ 这就是它存在的理由 |
| T5② | agenda 与 weather 两行对调 | 源码逐行核过 | **不红** | ✅ 计划预写的预期（两者互斥、顺序无关），不是测试漏洞 |
| T5③ | `cardListRows` 的 `total > 0` → `>= 0` | `cardFields.ts:38` | **不红** → **补格后红** | ⚠ **计划预期落空**：fixture 两个站 `total` 是 8 / 4，全仓没有任何用例走到 `total === 0`，这条守卫**是裸的**。已补一格（`total===0 的站不写「0/0 空闲」`，`cardFields.test.ts`），复验：变异落盘 → 恰好红这一条 → 还原 → 4 条全绿 |
| T5④ | `mainCard` 不走 `splitCardGroup`、直接取 `items[0]` | `stageScene.ts:20` | 「card_group：主卡决定场景」 | ✅ |

#### 本批踩的坑

1. **计划给的 fixture 常量撞上判据自己的哨兵**（`drivingMode.test.ts`）：`NOW = 1_000_000`（≈16 分钟）减一小时是**负数**，正好落进 `trueAt <= 0 = 从未标注过`（`NO_EDGE_DRIVING` 的定义）⇒「最近一次 Edge 标 true ⇒ 行车」红了。红的是 fixture 不是判据——真实时钟不会为负。改成 `1_700_000_000_000` 并把原因写进测试注释。
2. **计划给的 `test.each` 过不了 tsc**：`ok` 里写 `identity: 'trusted-tablet' as const` 会把 `Partial<typeof ok>` 的 `identity` 窄成那个字面量，`{ identity: 'mount' }` 不可赋值。显式标注 `identity: Identity` 才过。
3. **「存量显式 true → 保持」这一类 settings 用例，在实现之前就是绿的**：`mergeStoredSettings` 的 `...rest` 展开会把任何未知键原样带出（运行时不受 `AppSettings` 类型约束）⇒ 这半条断言对「键有没有加进 `DEFAULT_APP_SETTINGS`」**零敏感**。真正先红的只有「旧库没有 X → 补默认」那半（T2 / T3 / T4 各一条）。B2 坑① 说的是「入参得是合法 JSON 才够得到合并体」，这是它的第二层。
4. **多 suite 一起跑时 jest 不打逐条 `×` 行**，只在总结区打 `●  <describe> › <test>`；单 suite 才有 `×`。反向验证要读「红的是哪几条」就得取 `●` 行（或一次只跑一个 suite）——否则只能数到「几条红」，正是计划反复警告的那种读法。

#### 遗留 / 给第 2 批的话

1. ⬜ **本批所有真机动作未做（USB 没插）**，逐条列清，谁先插上谁做（T13 一并收也行）：
   - **批次级步骤 4**：Metro 热载冒烟——App 起得来、发一句文字、光球与胶囊无回归（判据：第 1 批结束时 App 的样子应与 B3 收口时一模一样）；
   - **T2 步骤 6**：`/presence-trail` 里 `driving` 轴不再随轮结束翻转（此时没有 Edge 标，应恒 false）；
   - **T3 步骤 6**：临时把 `DEFAULT_APP_SETTINGS.reduceMotionForce` 改 `true` 热载一次，看 Composer 球 / 顶栏球 / 欢迎球全部静止且**仍显示正确的态**，改回再热载一次球又动了。⚠ **本批没做这一步，所以 `settings/store.ts` 的 diff 里只有四个新键，没有临时改动残留**；
   - **T4 步骤 6**：`/native-spike` 的 `cue-wake` / `cue-attention` 两按钮各按一次——不崩、logcat 本应用 pid 零 E/F、`dumpsys audio` 抓 `AudioPlaybackConfiguration`（60ms×2 太短，抓不到只记「未观测」不写「不出声」）；按前先按 B3 坑⑨ 读一次 `STREAM_MUSIC Current`（音量 0 是「不出声」惯犯第三次）。**「响不响、两种分不分得出」是 T13 泓舟的人耳读数，本批不产出任何语音读数**；
   - **T5 步骤 6**：真栈发「附近的充电站」，兜底卡应渲出站名 + 距离 + 空闲（`testID=fallback-row` 数 ≥1，`b4-05-charging-list.png`）；卡头仍是「卡片 · charging_list」且无 `_prov` 徽章——**这两条是出账不是缺陷**。
2. **对计划接线清单的一处扩写**：`MessageBubble` 内的 `ThinkDots` 与两处 `StreamCursor`，计划的接线清单只写了「头像球 `animated={active && loops}`」没写它们，但同任务的「为什么」段写明「ThinkDots / StreamCursor / EdgeGlow 呼吸也是循环……一并可定格」。`loops` 这个 prop 本来就要传进 `MessageBubble`，所以按语义一并接上了（三处 `animated={loops}`）。**不是新判据**，判据仍只有 `orbPolicy.loopsAnimated` 一份。
3. **五份判据里有四份此刻零生产消费方**（`useLayout` / `stageScene` / `cardFields.cardPrimaryButton` / `drivingMode` 的 `composerInputMode`·`sheetResident`·`drivingSuggested`）——分批就是这么切的（T6/T10/T11 才接）。但要记住：**它们现在只有单测一个消费方**，「两个消费方才算真收敛」这条到第 2/3 批才兑现。
4. `charging_list` 仍不在 `hmi/src/types.ts::UiCard`、仍无 `_prov`：B2 出账⑨ 的 **hmi / agent 侧那一半原样转出**（本批只做了 mobile 兜底卡这一半）。`cards.test.ts:79` 断言 `CARD_FIXTURES ⊆ KNOWN_CARD_TYPES` 已复核——**加画廊样本会红，计划的警告是对的，没加**。
5. **两个待裁项（§0 第 3 条①②）本批一字未碰**，仍等泓舟裁。
6. **硬边界全程未破**：零重建、零新原生依赖、零后端改动、`hmi/` 只读不写（`stageScene.test.ts` 只 `readFileSync` 对账）、共享判据一字未动、无任何布局改动。
7. **未推送**：本批 5 个提交全部在本地 `main`（`origin/main..HEAD` = `db97a33` / `156b2c6` / `b791414` / `b3c65d4` / `d7029f7`），**没推**，等泓舟单独授权。

### 6.2 第 2 批「形态落地」（T6–T9）

> （待开工。含 T6 步骤 1 的真机 dp 读实、形态前三张、Maestro 07 正反两跑、T8 的 tintOverBlur 裁决与浅色对比裁决、T7 需泓舟的四格若挪到第 3 批要在此注明）

### 6.3 第 3 批「行车档」（T10–T13）

> （待开工。§11.2 B4 四条逐条读数或显式 ⬜；T13 步骤 9 / 10 的定位与裁决；设备与云栈还原表）

### 6.4 第 4 批「小样本 + 收口」（T14–T15）

> （待开工。小样本人 × 图表与错答原话，或「裁定不做、仍无外部基线」；B3 出账 / AGENTS ⑥ 逐条去向核销；未推送清单只列构成）

