# UX v2.1 · B5「缺陷 C + 重建趟 + B4 开项」实施计划（逐任务，按方案 v2.2 与泓舟 2026-09-04 裁决）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 状态：**已批准（泓舟 2026-09-04，拆完当轮批准，裁决表未改）；逐批新会话推进，第 1 批「缺陷 C」待开工**。§0 第 2 条是本计划的核心产出——B4 收口时归到 B5 的 19 项开项逐条给了默认裁决与落点，泓舟一句话可改任一条。
> 交付对象：`mobile/` 执行者（人或 Agent）；第 1 批同时是 `orchestrator/edge` + `gateway/edge` + `proto/` 的执行者（本计划是 B 系列里**第一次**允许后端改动的批）
> 上游真相源：[`2026-08-29-mobile-ux-v2-presence-redesign.md`](2026-08-29-mobile-ux-v2-presence-redesign.md)（方案 **v2.2**；本计划读 §6 退出条款 / §7.3 / §5.11 末句 / §8 / §9 全表 / §11.1 B5 行 / §11.4 / §12.2 / §13 Q5；**不读 §2**）；
> B4 计划 [`2026-09-02-mobile-ux-v2-b4-implementation-plan.md`](2026-09-02-mobile-ux-v2-b4-implementation-plan.md)（**§6.4 是本计划的输入**：「泓舟裁决」「对账一 / 二 / 三」「缺陷 C 两轮取证」「泓舟当轮提的两条界面优化」「Scanner 三条出账」「T14 材料」；§6.1–§6.3 各批「遗留」小节逐条核过，去向在本计划 §0 第 2 条；Task 正文不读）；
> B3 计划 [`2026-09-01-mobile-ux-v2-b3-implementation-plan.md`](2026-09-01-mobile-ux-v2-b3-implementation-plan.md)（只读 §6.2「T8 一趟重建」的读数与坑、§6.4「B3′ 结论」——重建趟与角色裁决的输入）；
> 主计划 [`2026-08-24-mobile-app-implementation-plan.md`](2026-08-24-mobile-app-implementation-plan.md)（§2 协议契约第 145 行 `final` 帧键表**本批要改**；§9 坑账 **69–74** 是 B4 新添的，开工前读一遍；下一个坑号从 **75** 起）；
> `AGENTS.md` §4.2「Android App」行（本计划 §0 第 2 条逐条给了去向）；`docs/dev-guide.md`「日常三条路径」C（cloud deploy 先 dry-run 再单独授权 apply）。
> 纪律：沿用 B1–B4 计划 §0 + 主计划 §9 坑账；每任务「先测后码、一任务一提交」；**`hmi/` 不碰；共享判据（`hmi/src/*.mjs`、`pendingOps.mjs` TTL、`voiceLoop.mjs`）一字不动**。B4 的三条硬边界在本计划**按批放开**：第 1 批放开「零后端」（只放开 §0 第 4 条列的三处），第 2 批放开「零重建 / 零新原生」（只放开 §1 列的件），第 3 / 4 批三条全部收回。

**Goal:** ① **缺陷 C「车停了行车档退不出来」**（B4 真机实测 3h12m）按泓舟裁决走后端：Edge 在 `final` 帧上也标 `driving`（裁决点仍只有 `server.py::_is_driving` 一处、标注函数与 `_stamp_progress` 同款、挪到 `Handle` 出口一处盖全部路径），网关 `eventToMap` 的 final 分支透传，客户端 `recordEdgeDriving` 同吃 final（**只认布尔**——旧网关无此键时不登记 false），加 UI 手动退出口（设置页行车档开关在自动进入时显示「自动行车中 · 退出」，退出只压**本段**、不改判据）；带后端单测 + Go 单测 + jest + 云栈注入 `speed_kmh=0` 后**一句简单轮即退出**的真机读数。② **一趟重建**：`foldstate` 补「查当前值」（B4 定性：任何新挂载实例都停在 flat 直到铰链再动）、`expo-battery` 低电量材质回落（§5.11 第三种回落）、静态 App Shortcuts + `xiaozhou://voice` 深链（方案 §9，§12.2 红线：只升层不开麦）、播报卡顿的**无 AEC 对照构建**并进同一趟当第一次构建取 Xruns 对照。③ **B4 开项全部落地或显式出账**：泓舟提的两条界面优化（语音层去底栏改顶缘把手下拉收起 / 发送与打断合一 ⬆·■）、Scanner 三条（顶栏 40dp 钮与 `health-dot` 触控目标 / 灰色副文本对比度 / 气泡与 chip 同文案的重复说明）、缺陷 A 横屏半（先量后改：底栏撤掉 → driving-landscape 隐藏 chips 且层覆盖整列 → 球降级判据兜底）、`Msg.driving` 单独验、B2 表#6 顺序取证、Reanimated tag→view 定位（非闸门）。④ **T14 五人小样本**跑五态版（speaking 用 Maestro 同步补齐、armed 明记缺），KWS 阈值 A/B 批排 B5 之后另拆。**B3′ 默认助理角色不启用**（B3′ 实证 HyperOS 三个手势一个都拿不到）。

**Architecture:** 判据仍全部是纯函数、住在一处、jest 钉住，组件只消费。后端：`server.py::_stamp_driving(event)` 在 `Handle` 出口对 `progress` 与 `final` 两种事件盖 `_is_driving()`（删掉 `_handle_impl` 里两处只盖 progress 的调用——盖在出口才能覆盖本地快路径 / 混合 / 降级兜底这些**不经云端**的 final，缺陷 C 的形态正是这些简单轮）；网关只加一行透传（恒带键，不 omitempty）。客户端：`drivingMode.ts` 的 `DrivingEdgeFact.trueAt` 语义收紧为「当前行车段的起点」（段内连续 true 不刷新），新增 `dismissedAt`——`drivingActive` 多一条「用户退出压住本段」；`SessionState.drivingDismissedAt` 只在内存（与 `drivingEdge` 同命，重启即清）。重建趟：`FoldStateModule` 缓存最近一次 `WindowLayoutInfo` 投影 + `Function("current")`，hook 初值读它；`core/power/lowPower.ts` 纯函数 + `usePowerFacts` 事实收集（原生缺席不崩、按 §9.27 铁则用 `requireOptionalNativeModule` 探）；Shortcuts 用自写 config plugin（静态 `shortcuts.xml` + `@string`，不引 `expo-quick-actions`——两条静态入口不需要运行时 API，少一个 autolinking 缝候选，同 B3 自写 foldstate 的判据）。界面：语音层的 Pan **只挂在顶缘把手带**（B4 实测层内 ScrollView 与整层 Pan 会打架），把手带接替 `voice-sheet-collapse` 的 testID 与 ≥56dp 演员身份；Composer 的 `composer-send` 忙时 ■ 闲时 ⬆，testID 不变（Maestro 五条流都在闲时点它）。`sheetHeight.ts` 的最小高构成随底栏撤掉重算，并加 `sheetOrbDp` 球降级判据兜底。

**Tech Stack:** Python 3.12 / grpcio（`orchestrator/edge`，pytest `--import-mode=importlib` 由根 `pytest.ini` 给）/ Go 1.24（`gateway/edge`，`go test ./gateway/edge/`）/ buf（`scripts/gen-proto.ps1`，`gen/` 不入库）/ `scripts/dev_stack.py deploy`（PowerShell，dry-run → 单独授权 `--apply`）/ React Native 0.86 / Expo 57 / expo-modules-core 57.0.13（`OnActivityEntersForeground` 在 `ModuleDefinitionBuilder.kt:129`、无参 `Function` 在 `ObjectDefinitionBuilder.kt:106`——写 Kotlin 前都核过）/ expo-battery ~57（本批新装）/ react-native-reanimated 4.5 / RNGH 2.32 / jest-expo（`mobile/test/**/*.test.ts`，**不含 `.tsx`**）/ Maestro 2.9（`D:/Android/tools/maestro-dist/maestro/bin/maestro.bat --no-reinstall-driver`，退出码由 Python `subprocess.run().returncode` 直读）/ `scripts/build_mobile.ps1`（ASCII 镜像区 `D:/Android/builds/xiaozhou-mobile`，`-Clean` 重生成 `android/`）/ patch-package（`npx patch-package --reverse` 卸 AEC 补丁做对照包）/ 云栈 debug 注入 `POST /api/debug/vehicle`（collector `:8446`）。

---

## 0. 接手须知（先读）

1. **开工前提**：`powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；`python scripts/dev_stack.py target show` = cloud；Metro `cd mobile && npx expo start --dev-client`；设备上是主线包 **`lastUpdateTime=2026-09-02 16:45:36`**（B4 全程用的那个；第 2 批装机后换新锚）——每批开工第一步 `adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime` 核一次并写进 §6。`adb` 一律用绝对路径 `%ANDROID_HOME%\platform-tools\adb.exe`（B4 §6.4 真机轮：本机两份 adb 版本不同，PATH 第一个不是 `ANDROID_HOME` 指的那个；开工先 `netstat -ano | findstr :5037` 核 server 是哪一份、`Get-Process adb` 核有没有孤儿）。**每次取证前后核 `mCurrentFocus` 的包名**（B4 抓到过别人的聊天窗口）。

2. ⛔ **B4 归到 B5 的开项逐条裁决（默认按此执行；泓舟一句话可改任一条，改了只动对应任务）**

   | # | 事项 | 来源 | 裁决（默认执行） | 理由 | 落点 |
   |---|---|---|---|---|---|
   | 1 | 缺陷 C「车停了行车档退不出来」 | 泓舟 2026-09-04 裁决 | **已裁**：后端 final 标 `driving` + 网关透传 + 客户端 `recordEdgeDriving` 同吃 final + UI 手动退出口；不走客户端拿 `vehicle_state` 自算（第二份判据，§5.3.1） | — | **第 1 批 T1–T5** |
   | 2 | 播报卡顿的无 AEC 对照构建归哪一趟 | 对账一 #2（泓舟曾同时选中互斥的两条） | **并进 B5 重建趟，作为该趟的第一次构建（对照包）**；不单开 B4′ | 对照要「同条件」——同一天、同设备、同工具链、同 App 设置；重建趟本来就要装两次包（对照 → 主线），Xruns 在两个包上各取一次即成对。单开一趟要把设备与工具链再暖一遍，且对照包没有第二个用途 | **T9（对照）→ T10（主线）**，读数表在 T11 |
   | 3 | T14 五人小样本：四态降级版还是等 armed / speaking 补齐 | 对账一 #8 | **五态版**：speaking 用 Maestro `extendedWaitUntil` 同步截（不依赖 KWS），armed 缺、记录明写「缺 armed」；armed 那张等 KWS A/B 批产出后**追加一次单图追问**，不重跑五人六图。判据按分母 5 记「均值 ≥4/5」并明标口径变了 | armed 拿不到的根因在 KWS 链路（#4），把五人小样本卡在它后面等于再拖一批；四态版丢掉 speaking 是没必要的损失——B4 T14 只是没用对同步法（adb 连拍） | **T17** |
   | 4 | KWS 阈值 A/B 批排 B5 前还是后 | 对账一 #1（已裁另开） | **B5 之后**、独立计划，在 B5 主线包（新包锚）上做 | 它是声学批，协议与人（泓舟说 10 句 × 档位）都独立；排在前面会让 A/B 读数绑在一个马上要被重建替换的包上（证据不跨包锚）；B5 重建不动音频链路（对照包只取数，最终装回带 patch 的主线） | B5 收口后另拆；T14 的 armed 追问挂在它后面 |
   | 5 | speaker 音量（0 → 120）还不还原 | 对账一 #6 | **不还原**，从还原表里划掉，音量归泓舟自管 | 它是设备用户自己的量，不是本批改的 App / 云栈状态；还原成 0 会让下一轮所有语音读数失效（B4 §6.1 因它卡了两批）；B4 真机轮已见泓舟自己按过音量键（120 → 90） | §5 第 12 条（还原表口径） |
   | 6a | `foldstate`「订阅即查询」不成立（任何新挂载实例都停在 flat 直到铰链再动） | 对账一 #3（已裁并进重建） | **做**：原生缓存最近一次 `WindowLayoutInfo` 投影 + `Function("current")`，hook 初值读它；顺带补「`currentActivity` 为 null 时延后到进前台再订阅」 | 根因：Expo 的 `OnStartObserving` 只在 0→1 个订阅者时触发一次，WindowManager 的「注册即回推」只回给第一个订阅者；第二个实例（对话页已挂着时再进取证屏）拿不到回推 | **T6** |
   | 6b | B3′ 默认助理角色启用 | B3 §6.4 遗留① / AGENTS ④ | **不启用、不进主线**；spike 分支 `spike/b3p-assistant-role` 原样保留不合并 | B3′ 实证：角色能拿到（五条断言）但 HyperOS 三个触发手势一个都不响应——`com.miui.home` 与 `system_server` 都用显式 `ComponentName` 拉小爱、不做 `ROLE_ASSISTANT` 解析。启用只换来 manifest 面（`BIND_VOICE_INTERACTION` 两个 service）与「读取正在使用的应用信息」这条平台授权，零用户可达入口；换机型 / ROM 再验时重开。B3 §6.4 遗留②（锁屏凭据复验）随之不做 | 不进任务；§5 第 1 条 |
   | 6c | `expo-battery` 低电量材质回落 | 对账二 / 方案 §5.11 末句 | **做**：省电模式 ∨ 电量 <20% ⇒ 材质回落 G1-tint（第三种回落） | §5.11 三种回落 B4 只落了两种（减少透明度 / 行车档），这是唯一因缺原生件没做的 | **T7** |
   | 6d | 缺陷 A 横屏半（外屏横记录区实测 98.67dp，0.4 档内容需求 269dp） | 对账一 #4 / 对账二 | **做，但它是 JS 批的事、不依赖重建**：先量后改，lever 按序——① 底栏撤掉（T12 顺带 −73dp）② driving-landscape 隐藏 chips 且层覆盖整列（层脱离记录区容器）③ `sheetOrbDp` 球降级判据兜底 | 修法在 `ChatScreen` / `VoiceSheet` 布局与 `sheetHeight.ts` 判据，不是原生；B4 把它写进「重建趟内容」只是归类 | **T15** |
   | 6e | 方案 §9 其余四项：Shortcuts / QS Tile / Live Updates / 小组件 | 方案 §9、§11.1 B5 行 | **Shortcuts 随重建趟做**（自写 config plugin、静态两条：「说话」→ `xiaozhou://voice`、「车况」→ `xiaozhou://vehicle`；「今日提醒」无路由不做）；**QS Tile 不做**（同一 deeplink 的第二入口，泓舟要就加进同趟 ≈60 行 Kotlin）；**Live Updates / 小组件等 M5**（前台服务 + 推送；M5 未启动，主计划 §7「触发 = 泓舟决定发布」） | 方案 §9 明写 Shortcuts 的前提只是 prebuild、不需要 M5；B3′ 之后 HyperOS 上唯一确定可达的出 App 入口就是长按图标。深链落点按 §12.2：只到对话页并升层、**不开麦** | **T8**（泓舟否决则整任务删除，不影响其余） |
   | 7 | Scanner 三条出账：顶栏「车辆 / 设置」40dp 钮与 `health-dot` 40dp 触控目标 / 灰色副文本对比度 / 气泡与 chip 同文案「重复说明」 | B4 §6.4 步骤 13 | **做**（全 JS）：目标按 `TARGET.parked` 48（行车 56）；`fg3`（两主题）与浅色 `fg2` 抬到 ≥4.5:1，判据写成 jest 里的 WCAG 对比度函数；chips 的 `accessibilityLabel` 加「快捷指令：」前缀 | 三条都是一处一改的账；对比度那条**要有可量的判据**（B4 Scanner 独有的维度） | **T14** |
   | 8 | 泓舟提的两条界面优化 | B4 §6.4 泓舟原话 | **做**（全 JS）：① 语音层去掉「收起 / 打断」底栏，收起 = 顶缘把手带向下拖（或点暗区 / 返回键），打断合并到 Composer；② 发送键与「■ 打断」合一：闲时 ⬆ 发、忙时 ■ 停 | 泓舟提的；B4 §6.4 记的三条实测约束（Pan 只认向下、底栏是 ≥56dp 演员、横屏底栏占 73dp）全部带进任务 | **T12 / T13** |
   | 9 | `Reanimated: synchronouslyUpdateUIProps failed` 钉到组件 | B4 §6.4（根因串已读到：`Unable to find SurfaceMountingManager for tag`，跟 surface 拆建走） | 第 3 批真机轮 **90 分钟预算、非闸门**：一次带 tag → view 映射的 pass | 差的只是组件名 | T16 步骤 8 |
   | 10 | B2 表#6「气泡先于相机」顺序取证 | B4 §6.4 T13 余格（一直没进任何 ⬜ 表） | 第 3 批真机轮顺手 | 便宜，只差没做（需相机权限 + `screenrecord`） | T16 步骤 7 |
   | 11 | `Msg.driving` 单独验 | B4 §6.3 遗留⑤ | 第 1 批真机轮顺手 | final 标 driving 之后，「退出后回看老气泡」用一句简单轮就能造 | T5 步骤 6 |
   | 12 | reduce-motion 的「系统开关那一路」 | §11.2 B4 ④ 具名 ⬜ | 第 3 批真机轮**可选**（改 `animator_duration_scale` 是系统设置，需泓舟授权；做完还原 1 并回读） | 第二入口的确认，不是闸门 | T16 步骤 9 |
   | 13 | Dock `safety_blocked` 显示 VAL 原话（Q16 `confirm_policy`） | 对账二 | **不进 B5**，后端另立 | Q16 是 VAL 策略投影的契约设计（risk / allowed_channels / reason_code / expires_at），不是一个字段；本批放开的后端面只有 §0 第 4 条那三处 | 出账（AGENTS §4.2 保留） |
   | 14 | `charging_list` 的 hmi `UiCard` 型名 + `_prov` 半 | 对账二 | **不进 B5**（hmi / agent 侧） | `hmi/` 不碰是 B 系列硬边界 | 出账 |
   | 15 | `poi_list` 的 follow_up 不含「换一批」 | B4 §6.4 步骤 11② | **不进 B5**（内容由 `agents/nearby` 产出） | 客户端只渲 `assistant.followUp` | 出账给 agents/nearby |
   | 16 | v1 回滚代码删除（方案 §11.5「B4 稳定后再删」） | B4 §5 第 7 条 | **B5 不删** | B5 又动了语音层底栏与发送键；「收口 + 一轮真机使用没回滚」这个条件要在 B5 之后重新计 | 出账 |
   | 17 | 主线 APK 备份 `D:/Android/builds/_b3-mainline-apk-backup/` | B3 §6.4 遗留⑤ | 第 2 批装了新主线包之后它就过时；**删除是红线 ⇒ 收口时列给泓舟裁**；重建趟自己的备份放 `_b5-mainline-apk-backup/` | — | T10 步骤 0 / T18 |
   | 18 | 本地与远端分叉怎么合 | 对账一 #7 | **已解决**（B4 已 merge 并推送，`origin/main = HEAD = faf8b31`）；每批开工重核 | — | §0.1 固定五步 |
   | 19 | `test_operation_hold_cancels_owner_when_lease_is_lost` 只在全仓并行时红 | B4 §6.4 合并后全量 | **不在 B5**（`agents/mcp_bridge/` 负责人；欠一次根因定性） | 不在改动面内 | 出账 |

   **对方案 §11.1 B5 行的回写**：方案里 B5 = 「出 App 在场，随 M5」。泓舟 2026-09-04 裁决把 B5 的实际范围改成本计划四批；U5 只随重建趟带 Shortcuts（6e），Live Updates / 小组件 / QS Tile 仍随 M5。**这一句在第 1 批 T1 步骤 0 回写进方案 §11.1 B5 行**（先改文档再改实践；方案状态行同步，版本号不 bump——是范围记录不是设计变更）。

3. **三条硬边界按批放开，撞上没放开的一条就停、出账、不做**：
   - **第 1 批**放开「零后端」，且**只放开三处**：`proto/cockpit/orchestrator/v1/orchestrator.proto`（`FinalResult` 加一个字段）、`orchestrator/edge/server.py`（`_stamp_progress` → `_stamp_driving` + 出口一处调用）、`gateway/edge/main.go`（final 分支一行）。云端 Planner / VAL / 其它服务一行不动。零重建、零新原生仍在。
   - **第 2 批**放开「零重建 / 零新原生」，且只放开 §1 列的件：`modules/foldstate` 原生半、`expo-battery`、`plugins/with-shortcuts.js` + `app.config.ts` 挂它、`mobile/patches/` 的**临时**卸载（对照包）。`predictiveBackGestureEnabled: false`、`softwareKeyboardLayoutMode`、ABI、FFmpeg 开关一律不动。零后端。
   - **第 3 / 4 批**三条全收回：零重建、零新原生、零后端。
   - `hmi/` 全程不碰（网关加的 `driving` 键 HMI 收到即忽略——`hmi/src/App.tsx:420` 的 final 分支不读它；`cd hmi && npm test` 在第 1 批收口跑一次作回归）；共享判据一字不动。

4. **第 1 批的两道泓舟闸（红线，计划里写死位置）**：① cloud deploy 只接受干净、已提交、main 可达的 SHA——`dev_stack deploy --sha HEAD` dry-run 若报 SHA 不可达 / 未推送 ⇒ **停下**，列完整 `origin/main..HEAD` 逐条念归属，向泓舟要**推送授权**；② dry-run 通过后 `--apply` 是**部署到云栈**，**单独授权**。两道都过了才有 T5 的真机读数；过不了就把 T1–T4 的宿主侧读数（pytest / go test / jest / tsc）收口，T5 记 ⬜ 等授权。deploy 不自动 commit / merge / push；不改 `.env`、安全组、CI/CD。

5. **读数口径**：语音类读数（本批只有 T9 / T11 的 Xruns 与 T17 的 speaking 截图算语音类）**必须带 `lastUpdateTime` 锚**；对照包与主线包各自的锚分开记，Xruns 表两行两锚。其余读数写清取法与图名（`mobile/e2e/artifacts/b5-*.png`，gitignore）。目标 dp 读实用 `uiautomator dump`（开「减少动效（强制）」才吐完整树，B4 §6.3）+ `mobile/e2e/tools/target_probe.py --density 480`；Scanner 读数与 `target_probe` 读数**带各自阈值**（48 / 56）分开记。

6. **真机取证纪律（合订，B1–B4 §0 原样 + B4 §6.4 新添四条）**：截图 `adb shell screencap -p -d <物理displayId> /sdcard/x.png` + `adb pull`（PowerShell 的 `>` 损坏 PNG）；**判「哪块屏是活的」看 `dumpsys display` 的 `state ON/OFF`，不信 `cmd device_state`**（B4 §6.4：device_state 滞后，连截三张纯黑）；外屏 `4630947090644569220` 1080×2520 / 内屏 `4630946481727302019` 2224×2488；**每条 adb 命令都带 `timeout`**、daemon 自发重启后 `adb reverse` 要重建（判别式：两份 `uiautomator dump` hash 相同 = 中间那条命令没送到）；颜色 / 亮度类读数一律经 `mobile/e2e/tools/png_probe.py`（先 `selftest`）；深链在 bundle 加载完之前发会被吞（§9.58）；改完设备 / App 设置一律回读（§9.55）；Maestro 新流**别写 `hideKeyboard` 也别 `pressKey: Back`**（两者都发 BACK，键盘不在时退到桌面——B3 坑⑤ + B4 §6.4），收键盘用 `tapOn: point: "50%,30%"`；抓瞬态状态用 Maestro `extendedWaitUntil` 按状态自己的 testID 同步再 `takeScreenshot`，**不要 adb 连拍**（B4 T14 三次落空）；MIUI「USB 安装」开关约 10 分钟 / 重启后自动关回，开完立刻跑；Metro 约 61 分钟 / 4GB 堆会 OOM 自死而退出码 0，症状是 App 落到 `DevLauncherErrorActivity`（坑 74）。

7. **十个结构性事实，写代码前记住**（每条带 `文件:行号`，别按印象）：
   - **`driving` 今天只在 `process` 帧上**：产出方 `orchestrator/edge/server.py:1021` `_stamp_progress`（按 `:1010` `_is_driving`：`speed_kmh > 0` 或挡位 D/R/S），**只在两处云端路径上被调用**（`:810` 混合路径、`:947` 上云路径）；本地快路径的 final（`:680` / `:765` / `:831` / `:905` / `:1008` 等）**从不经过它**。`Handle`（`:513`）是所有事件的唯一出口（`:535` `async for event in self._handle_impl(...)` → `:545` `yield event`），出口盖一次就盖全。
   - **`FinalResult` 现有 8 个字段**（`proto/cockpit/orchestrator/v1/orchestrator.proto:57-72`，最后是 `closed_operation_ids = 8`），`ProcessUpdate.driving = 6`（`:54`）。新字段编号 **9**。`gen/` 是 gitignore 的 codegen 产物，`scripts/gen-proto.ps1` = `buf generate proto`（buf 在 `%APPDATA%\npm\buf`）。
   - **网关 `eventToMap`**（`gateway/edge/main.go:397-435`）：process 分支 `:409` 恒带 `"driving": p.Driving`（无 omitempty）；final 分支 `:417-420` 的 `result` 字面量 + 四个条件键（emotion / operation_id / closed_operation_ids / ui_card）。`gateway/edge/*_test.go` 都是 `package main`（`vehstate_test.go` 是模板），根 `go.mod` 模块名 `github.com/cockpit/car-agent`，proto 生成包 `orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"`。
   - **客户端登记点**：`mobile/src/core/session/store.ts:470` `handleFrame(data: any)`；`:507-509` process 分支无条件 `recordEdgeDriving(s.drivingEdge, !!data.driving, Date.now())`（**这一路不动**）；final 分支从 `:562` 起，先处理 `emotion`、再 `rejected` 特例提前 return（`:566-580`）——final 的登记要放在 **`rejected` 之前**（拒识轮也是一轮，行车事实与拒识无关）。`SessionState.drivingEdge` 声明在 `:72`、初值 `:180`。
   - **判据**：`mobile/src/core/presence/drivingMode.ts` `drivingActive`（手动 ∨ trueAt>0 ∧（falseAt≤trueAt ∨ 30s 宽限））、`recordEdgeDriving`（true ⇒ `{trueAt: now, falseAt: 0}`——**每条 true 都刷新 trueAt**，这是「退出只压本段」要收紧的那一处）；消费在 `usePresence.ts:131`（`drivingActive({ manual, edge, now })`）与 `native-spike.tsx:38`；`needsTick` 的 30s 宽限窗在 `usePresence.ts:135`。
   - **设置页行车档开关**：`SettingsScreen.tsx:184-189` 的 `SwitchRow`（`value={settings.drivingManual}`）；页面拿不到会话 store——取证屏的做法是 `getWired()?.core.store` + `subscribe`（`native-spike.tsx:30-35`），照抄。
   - **`foldstate` 三件**：`modules/foldstate/android/src/main/java/com/xiaozhou/foldstate/FoldStateModule.kt`（`OnStartObserving` 里 `appContext.currentActivity ?: return@OnStartObserving` **静默返回**；`consumer` 把 `WindowLayoutInfo` 投影成 map 直接 `sendEvent`，**不缓存**）、`modules/foldstate/index.ts`（接口只有 `addListener`）、`src/ui/layout/useFoldState.ts`（`useState<FoldEvent | null>(null)` 初值 null）。两处注释「订阅就是查询」**都是错的**，改代码时一并改注释。
   - **材质回落三种情形的接线**：`ChatScreen.tsx:432-433` `blurTarget = blurReady && ref && !reduceTransparency && !snapshot.driving ? ref : null`，注释明写「低电量不做（无 expo-battery）」——第三个 `&&` 就加在这里。
   - **语音层底栏与 Pan**：`VoiceSheet.tsx:298-323` 底栏（`voice-sheet-collapse` 96×56 + busy 时 `voice-sheet-interrupt`）；Pan `:111-116`（`.activeOffsetY(10)`，`onEnd` 位移 > `SHEET_DISMISS_DY`=80 收起）挂在 `:150` 的整个 `Animated.View testID="voice-sheet"` 上；把手是 `:184` 那个 36×4 的 View；暗区 Pressable `:144-147`（`accessibilityLabel="收起语音层"`）。`sheetHeight.ts` 的常量 `HANDLE_DP 12 / SCROLL_PAD_DP 32 / FOOTER_PAD_DP 17` + 一枚 `TARGET.driving` 键构成 chrome 117。
   - **Composer 的两枚键**：`Composer.tsx:236-243` busy 时的「■ 打断」pill（`onInterrupt`）；`:250-270` `composer-send`（`Icon name="send"`，C 身份 `disabled` + 0.45 透明）。图标数据在 `ui/icons.local.ts`（`send` / `keyboard`，24 盒 1.8 stroke，`Icon.tsx` 的 REGISTRY 合并）。ChatScreen 侧 `onInterrupt = core.cancelCurrentTurn()`（`:240`），`interruptAndListen`（`:349`）是「停播再听」——**两者语义不同**，合一键取前者，后者仍归光球轻点（`:355`）。
   - **顶栏两枚 40dp 钮**：`ChatScreen.tsx:124-140` 的 `TopLink`（`width: 40, height: 40`）；`health-dot` `:654-664`（`minWidth: 40, height: 40`）。色板 `ui/theme.ts:61-62`（深色 `fg2 .58 / fg3 .34`）、`:84-85`（浅色 `fg2 .55 / fg3 .32`）。chips `Composer.tsx:135-144` 无 `accessibilityLabel`。

8. **提交纪律**（B1–B4 §0 原样沿用）：新建文件 `git add -- <路径> && git commit -m '…' -- <路径>` **同一条命令链**，`-m` 写在 `--` 之前；提交后 `git show --stat HEAD` 复核；**不要 `git add -A`**；`AGENTS.md` 单独一个 commit、动它前 `git diff --stat -- AGENTS.md`；多行 message 走 `git commit -F -` + heredoc。**变异测试的还原用副本回写，不用 `git checkout --`**（B4 §6.3：checkout 会抹掉同文件未提交的实现）；`grep` 证明变异落盘之后，「零红」是关于判据的读数不是关于变异的读数（B4 坑 69）。

9. **方案 §13 已裁决的默认值不在本计划重议**：Q4 行车档不自动进入、三条件只建议；Q5 角色启用（本计划裁「不启用」，见第 2 条 6b）；Q6 提示音；Q15 设备角色；Q18 行车档只强制安全告警。本计划新增的实施判断集中在 §5。

### 0.1 分批执行：一批一个会话（新会话从这里开始）

本计划分四批，每批一个新会话，每批以「jest 全绿 + `tsc` 0 + 逐任务已提交 + §6 回填」收口；下一批冷启动只读 §0 + §0.1 + §1 + 自己那几个 `### Task N` 块（`grep -n "^### Task" <本文件>` 取行号，`sed -n` 只读自己的段），**不读整份计划、不读方案全文**。

| 批 | 会话任务 | 性质 | 并行度 | 收口判据 | 真机？ |
|---|---|---|---|---|---|
| **第 1 批「缺陷 C」** | T1 proto + Edge 出口标注 + pytest → T2 网关透传 + Go 测 + 契约文档 + e2e 断言 → T3 客户端：final 同吃 + 段语义 + `dismissedAt` + 取证屏 → T4 设置页退出口 + `usePresence` 接线 → T5 deploy（两道闸）+ 真机读数 | 后端三处 + 客户端判据与接线 | T1 → T2 串行（T2 的 Go 测要 T1 生成的 `gen/go`）；T3 ∥ T1/T2（客户端只认布尔，不等后端）；T4 在 T3 之后；T5 最后 | `python -m pytest orchestrator/edge/tests -q` 全绿（+5）、`go test ./gateway/edge/` 全绿（+2）、`npm test` 全绿（499 → ≈508）、`tsc` 0、`cd hmi && npm test` 全绿、4 个代码提交 + 1 个方案回写提交、T5 有读数或显式 ⬜ 等授权、§6.1 | 是（云栈注入单人可造；不需要泓舟在场） |
| **第 2 批「重建趟」** | T6 foldstate `current()` → T7 expo-battery + `lowPower` → T8 Shortcuts + `xiaozhou://voice` → T9 对照构建（卸 AEC 补丁）+ Xruns 对照读数 → T10 主线 `-Clean` 重建 + 注册验证 + 装机重核 → T11 第 2 批真机验收 | 原生三件 + 两趟构建 + 语音类读数 | T6 ∥ T7 ∥ T8 互不相干（T8 动 `app.config.ts`，T7 动 `package.json`——串行提交）；T9 → T10 串行（对照包先、主线包后，收尾时设备上留主线包） | `npm test` 全绿（≈508 → ≈513）、`tsc` 0、两趟构建各 BUILD SUCCESSFUL、注册验证五件 + shortcuts meta-data、新包锚、Xruns 2×2 表、§6.2 | 是（手折与省电模式需泓舟；Xruns 单人可取——播报用「总是」档文字轮触发） |
| **第 3 批「界面优化 + Scanner + 缺陷 A 横屏半」** | T12 语音层去底栏 + 顶缘把手带下拉收起 → T13 发送 / 打断合一 ⬆·■ → T14 Scanner 三条 → T15 缺陷 A 横屏半（先量后改）→ T16 第 3 批真机轮 | 组件层 + 两份判据更新 + 真机轮 | T12 → T15 串行（都动 `VoiceSheet.tsx` / `sheetHeight.ts`）；T13 ∥ T14（不同文件；T14 的 chips label 在 `Composer.tsx` 与 T13 同文件——串行提交） | 全绿（≈513 → ≈521）、`tsc` 0、Maestro 01/02/03/06/08/09 rc=0、`target_probe` 新演员 PASS + 阴性、Scanner 复扫两态、§6.3 | 是（横屏行车档、Scanner、TalkBack、表#6 需泓舟） |
| **第 4 批「小样本 + 收口」** | T17 五态小样本（泓舟批准才做）→ T18 记录收口 | 取数 + 文档 | 串行 | 小样本有分布读数或显式「未做，仍无外部基线」；README / 主计划 §2 与 §9 / `docs/design/README.md` / `AGENTS.md` 指针；APK 备份目录交裁；未推送清单当场重列；§6.4 | 是（截图材料来自真机） |

**每批开工的固定五步**（写进新会话的第一条提示词）：
1. `powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；`python scripts/dev_stack.py target show` = cloud；`netstat -ano | findstr :5037` 核 adb server 是 `ANDROID_HOME` 那份；
2. `cd mobile && npm test && npm run typecheck` 取**开工基线**（条数与 0 error）写进 §6 该批第一行；第 1 批另取 `python -m pytest orchestrator/edge/tests -q` 与 `go test ./gateway/edge/` 的基线；`adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime`（第 1 批应为 `2026-09-02 16:45:36`，第 3 批起应为 T10 记的新锚，不是就先问为什么）；`git log --oneline origin/main..HEAD` 与 `HEAD..origin/main` 各念一遍归属；
3. 只读 §0 / §0.1 / §1 + 自己批次的 `### Task N` 块；
4. 按任务顺序：写失败测试 → 跑红 → 实现 → 跑绿 → `tsc` → `git add -- <新文件> && git commit -m '…' -- <只加自己的路径>` → `git show --stat HEAD` 复核；
5. 收口：全量 `npm test` + `tsc`（第 1 批加 pytest / go test / hmi npm test），把读数、遗留、撞到的坑写进 §6，**然后停下**——下一批是另一个会话的事。

**worktree**：B1–B4 都在主工作树做（泓舟未授权分树）。若泓舟同意分树，第 1 批开工前 `git worktree add ../car-agent-ux-b5 -b ux-v2-b5` 并写进 §6；没有分树就在主工作树做，每次提交前 `git status` 重采。⛔ **push 的粒度是分支不是提交**（M4-R1）：本计划任何一批都**不主动推送**——唯一例外是第 1 批 T5 的 deploy 前置（§0 第 4 条那道闸），推前列完整 `origin/main..HEAD` 并单独取得授权；**「我不推」保证不了「我的东西没上去」**（B4 §6.3 遗留⑨：别人推 main 会带走同一分支上的提交）。

**批与批之间的状态只靠两处传递**：git 提交（代码）与本文件 §6（读数与遗留）。新会话不要去翻上一批会话的对话。

---
## 1. 文件结构（先定边界，再拆任务）

### 新建

| 文件 | 职责 | 依赖 | 任务 |
|---|---|---|---|
| `orchestrator/edge/tests/test_driving_stamp.py` | 缺陷 C 的后端守卫：本地快路径 final 带 `driving`（true / false 两向）、上云路径 progress 与 final 都带、挡位 D 零速仍算行车、云端自带的 `driving` 被 Edge 覆盖 | fake cloud（`test_cloud_degraded_fallback.py` 同款 `_servicer`） | T1 |
| `gateway/edge/frames_test.go` | `eventToMap`：final 恒带 `driving` 键（true / false）；process 仍带（回归） | `orchpb`（T1 重新生成） | T2 |
| `mobile/src/core/power/lowPower.ts` | `lowPower({level, saver})`：省电模式 ∨ 电量 <0.2 ⇒ true；原生缺席（null）/ 未知（-1）⇒ false；纯函数 | — | T7 |
| `mobile/src/core/power/usePowerFacts.ts` | 事实收集：`requireOptionalNativeModule('ExpoBattery')` 探原生，在场才 `require('expo-battery')`（它的顶层 `requireNativeModule` 在旧 APK 上会抛——§9.27 铁则） | expo-battery | T7 |
| `mobile/test/lowPower.test.ts` | 五条正反例 | jest | T7 |
| `mobile/plugins/with-shortcuts.js` | 静态 App Shortcuts：写 `res/xml/shortcuts.xml` + `strings.xml` 两组标签 + `.MainActivity` 的 `android.app.shortcuts` meta-data；两条：说话 / 车况 | `expo/config-plugins`（`withAndroidManifest` / `withStringsXml` / `withDangerousMod`） | T8 |
| `mobile/src/app/voice.tsx` | `xiaozhou://voice` 落点：`Redirect` 到 `/?voice=1`（只回对话页，升层不开麦由 ChatScreen 消费参数） | expo-router | T8 |
| `mobile/test/theme.test.ts` | WCAG 对比度判据：深 / 浅两主题的 `fg2` / `fg3` 压在 `bg` 上 ≥4.5:1（alpha 先合成再算相对亮度） | `ui/theme.ts` 的两份 Palette | T14 |

### 修改

| 文件 | 改什么 | 为什么 | 任务 |
|---|---|---|---|
| `proto/cockpit/orchestrator/v1/orchestrator.proto` | `FinalResult` 加 `bool driving = 9;`（注释写清与 `ProcessUpdate.driving` 同裁决点） | 缺陷 C 后端半 | T1 |
| `orchestrator/edge/server.py` | `_stamp_progress` → `_stamp_driving`（progress + final 两分支）；调用从 `_handle_impl` 的 `:810` / `:947` 两处**挪到** `Handle` 的 `:535` 出口一处 | 本地快路径的 final 不经云端路径；出口盖一次盖全 | T1 |
| `gateway/edge/main.go` | final 分支 `result` 字面量加 `"driving": f.Driving`（恒带键） | 客户端要能分辨 false 与「没有这个键」 | T2 |
| `docs/design/2026-08-24-mobile-app-implementation-plan.md` | §2 第 145 行 `final` 帧键表加 `driving`；§9 坑账追加 75 起 | 协议契约文档与坑账 | T2 / T18 |
| `test/e2e_process_region.py` | 简单车控轮与复杂轮的 final 各加一条 `driving is False`（默认泊车态、键在场） | 本地栈 e2e 守卫（cloud 缺省不跑它——云上的证据是 T5 真机读数） | T2 |
| `mobile/src/core/presence/drivingMode.ts` | `trueAt` 语义 = 当前行车段起点（段内 true 不刷新）；`drivingActive` 加 `dismissedAt?`（≥ trueAt 压住本段）；头注改写（final 也是事实来源） | 缺陷 C 客户端半 + 退出口判据 | T3 |
| `mobile/test/drivingMode.test.ts` | +5：段内 true 不刷新 / false 后再 true 开新段 / dismissed 压住本段 / 新段解除 dismissed / 手动压过 dismissed | 判据守卫 | T3 |
| `mobile/src/core/session/store.ts` | `SessionState.drivingDismissedAt`（初值 0）；final 分支顶部（`rejected` 之前）`typeof data.driving === 'boolean'` 才登记；`dismissDriving()` | 只认布尔 = 旧网关兼容 | T3 |
| `mobile/test/sessionStore.test.ts` | +4：final `driving:true` 登记 / true 段后 final `false` 登记 falseAt / final 无键不动 / `dismissDriving` 写时刻 | 登记守卫 | T3 |
| `mobile/src/app/native-spike.tsx` | `driving` 行加 `dismissedAt`，`drivingActive` 传它；第 2 批加 `current` / `power` 两行 | 取证屏接真实事实（B4 缺陷 B 的教训） | T3 / T6 / T7 |
| `mobile/src/features/chat/usePresence.ts` | 解构 `drivingDismissedAt`，喂进 `drivingActive` | 接线 | T4 |
| `mobile/src/features/settings/SettingsScreen.tsx` | 行车档 `SwitchRow` 下：自动进入且未手动时渲「自动行车中 · 退出」行（testID `driving-auto-exit`），点 = `core.dismissDriving()`；desc 改写 | 缺陷 C 的 UI 出口 | T4 |
| `mobile/modules/foldstate/android/.../FoldStateModule.kt` | 缓存最近投影 `last` + `Function("current")`；`start()` 抽出，`OnActivityEntersForeground` 补订阅；注释改写 | 新挂载实例拿得到当前值 | T6 |
| `mobile/modules/foldstate/index.ts`、`mobile/src/ui/layout/useFoldState.ts` | 接口加 `current(): FoldEvent \| null`；hook 初值 `FoldNative?.current() ?? null`；注释改写 | 同上 | T6 |
| `mobile/package.json` | `expo-battery`（`npx expo install`，版本以装出来的为准记进 §6.2） | 第三种回落 | T7 |
| `mobile/src/features/chat/ChatScreen.tsx` | `blurTarget` 加 `!lowPower(power)`；消费 `voice=1` 参数升层（一次性）；driving-landscape 下 chips 隐藏 + 语音层容器改整列 + 层内大球接 `onTap`；顶栏钮 / `health-dot` 48（行车 56） | T7 / T8 / T15 / T14 | T7 T8 T14 T15 |
| `mobile/app.config.ts` | `plugins` 加 `'./plugins/with-shortcuts'` | prebuild 才写得进 `android/` | T8 |
| `mobile/patches/react-native-audio-api+0.13.3.patch` | **T9 期间临时** `npx patch-package --reverse` 卸载（文件不动、只反打进 `node_modules`），T10 前 `npx patch-package` 重新打上并核证进包 | 对照包 | T9 |
| `mobile/src/features/chat/VoiceSheet.tsx` | 底栏整段删除；Pan 从整层挪到顶缘把手带（`Pressable` + `GestureDetector`，testID `voice-sheet-collapse` 沿用，`minHeight` = 目标）；`onInterrupt` prop 删除；split 时大球接 `onOrbTap` | 界面优化① + 缺陷 A 横屏半 | T12 T15 |
| `mobile/src/ui/layout/sheetHeight.ts` + `mobile/test/sheetHeight.test.ts` | chrome 重算（把手带 = 目标高 + `SCROLL_PAD_DP`；`HANDLE_DP` / `FOOTER_PAD_DP` 删）；`drivingSheetMinDp` 加 `orb` 参数；新增 `sheetOrbDp`；测试的「内容清单」逐项对齐 | 判据跟着排版走（改那边要同步改这里——文件头注原话） | T12 T15 |
| `mobile/src/features/chat/Composer.tsx` | 「■ 打断」pill 删除；`composer-send` 忙时 `onInterrupt` + ■ + 琥珀，闲时 `submit` + ⬆ + 极光；C 身份闲时仍 disabled、忙时可点；chips `accessibilityLabel="快捷指令：…"` | 界面优化② + Scanner ③ | T13 T14 |
| `mobile/src/ui/icons.local.ts` | `send` 换成 `arrowUp` + `stop`（先 `grep -rn 'name="send"'` 确认只有 Composer 用） | 图标 | T13 |
| `mobile/src/ui/theme.ts` | 深色 `fg3` .34 → .48；浅色 `fg3` .32 → .60、`fg2` .55 → .62（数值由 T14 的测试反推，以跑出来的为准） | Scanner ② | T14 |
| `mobile/e2e/README.md`、`docs/design/README.md`、`AGENTS.md` §4.2（只改指针）、方案 §11.1 B5 行（T1 步骤 0） | 记录 | 收口 | T1 T18 |

**刻意不动**：`hmi/src/*` 全部；`@shared/*.mjs` 共享判据；`Msg` 共享类型（`hmi/src/types.ts:28` 的 `driving?` 语义仍是「气泡的显示语义」）；`orchestrator/cloud/*`、`val.py::_is_driving`（裁决点本身不动，只是多一处消费）；`app.config.ts` 除 plugins 数组外的一切；`modules/kws`；`predictiveBackGestureEnabled`；v1 回滚分支代码；B3 / B4 计划正文与 §6 记录（被推翻的判据已在那里出账）；`process` 帧那一路的客户端登记（`store.ts:507-509`）；`interruptAndListen`（光球轻点「停播再听」不变）。

### 1.1 追溯：输入的每条要求指到哪个任务（自检用，写完计划逐行核过）

| 来源 | 要求 | 任务 |
|---|---|---|
| 泓舟裁决「缺陷 C」 | Edge 在 final 帧也标 driving，裁决点仍只 VAL 一处、与 `_stamp_progress` 同款一行 | T1 |
| 同上 | 网关 `eventToMap` final 分支透传 | T2 |
| 同上 | 客户端 `recordEdgeDriving` 同吃 final，process 那一路不动 | T3 |
| 同上 | UI 手动退出口：行车档开关在自动进入时显示「自动行车中 · 退出」，退出只压本次、不改判据 | T3（判据）/ T4（UI） |
| 同上 | 后端改动带单测 + 云栈注入 `speed_kmh=0` 后一句简单轮即退出的真机读数 | T1 / T2（单测）/ T5（读数） |
| 对账一 #2 | 播报卡顿对照构建归哪一趟 | §0 第 2 条 #2 → T9 / T10 / T11 |
| 对账一 #6 | speaker 音量还不还原 | §0 第 2 条 #5 → §5 第 12 条 |
| 对账一 #8 / T14 材料 | 四态降级版还是等补齐 | §0 第 2 条 #3 → T17 |
| 对账一 #1 | KWS 阈值 A/B 批排期 | §0 第 2 条 #4（B5 后另拆） |
| 对账一 #3 / §6.2 遗留① / §6.3 遗留③ / 坑 72 | `foldstate` 查当前值 | T6 |
| 对账二 / B3 §6.4 遗留①② / AGENTS ④ | B3′ 角色启用 | §0 第 2 条 #6b（不启用）/ §5 第 1 条 |
| 对账二 / 方案 §5.11 末句 / B4 §5 第 9 条 | `expo-battery` 低电量回落 | T7 |
| 对账一 #4 / 对账二 / §6.4 缺陷 A ③ | 缺陷 A 横屏半 | T15 |
| 方案 §9 表 / §11.1 B5 行 / §12.2 | Shortcuts / QS Tile / Live Updates / 小组件 / 深链红线 | §0 第 2 条 #6e → T8；回写方案 §11.1（T1 步骤 0） |
| §6.4 步骤 13 Scanner 三条 | 顶栏 40dp 钮 + `health-dot` / 副文本对比度 / 重复说明 | T14 |
| §6.4 泓舟原话① | 语音层去底栏、顶缘滑动收起、打断合并 | T12 |
| §6.4 泓舟原话② | 发送键与打断合并、⬆ 图标 | T13 |
| §6.4「带过去的实测约束」三条 | Pan 只认向下 / 底栏是 ≥56 演员 / 横屏底栏 73dp | T12 步骤 3 注释 / T12 步骤 5 / T15 步骤 1 |
| §6.4 Reanimated 根因串 | tag → view 定位 | T16 步骤 8 |
| §6.4 T13 余格「表#6」 | 气泡先于相机 | T16 步骤 7 |
| §6.3 遗留⑤ | `Msg.driving` 单独验 | T5 步骤 6 |
| §11.2 B4 ④ 具名 ⬜ | 系统动画缩放那一路 | T16 步骤 9（可选） |
| 对账二 Q16 / `charging_list` hmi 半 / 步骤 11②「换一批」/ v1 删除 / APK 备份 / 全量并行红 | 出账项 | §0 第 2 条 #13–#17、#19 |
| B4 §6.1 遗留③ | 判据要有两个消费方才算真收敛 | T12 / T15 的 `sheetHeight` 现在有 VoiceSheet + ChatScreen 两个消费方 |
| B4 §6.2 遗留②（`device_state 3→2` 能驱动事件但没证明摊平时也能造铰链） | 仪器前提 | T11 步骤 1 顺带补对照（摊平时强制 `state 2` 看有没有事件） |
| B4 §6.2 遗留⑤ / §6.3 遗留⑥ | 临时取证流 `drawer-tap.yaml` / `back-order.yaml` 不入库 | T16 步骤 10：把手带正反结构写进 `e2e/README.md`，流仍不入库 |
| 方案 §11.4 八条 | 取数点 | §4 |
| 方案 §11.5 | 开关 / 回滚 | §5 第 9 条（B5 的组件改动仍受 `uxV2.voiceSheet` / `uxV2.presence` 开关保护，v1 不删） |

---
## 2. 任务清单

### Task 1: 缺陷 C 后端半——proto `FinalResult.driving` + Edge 出口标注 `_stamp_driving` + pytest（泓舟裁决；方案 §6 退出条款的事件源）

**Files:**
- 修改 `proto/cockpit/orchestrator/v1/orchestrator.proto:57-72`（`FinalResult`）、`orchestrator/edge/server.py:535`（`Handle` 出口）、`:810` / `:947`（删两处旧调用）、`:1021-1025`（`_stamp_progress` → `_stamp_driving`）
- 新建 `orchestrator/edge/tests/test_driving_stamp.py`
- 文档：方案 §11.1 B5 行回写（步骤 0）

**为什么**：B4 真机实测：车 16:07 停下，行车档到 19:12 仍开着（3h12m），期间三轮对话只有第一轮出了过程区。机制是 `driving` 只在 `process` 帧上（`_stamp_progress` 只在两处云端路径上被调用），而日常轮大多不走 Planner 过程区 ⇒ 客户端「Edge 标 false 起 30s 退出」那条 false **可能永远不到**。两条修法里泓舟裁了后端这条：裁决点仍只有 `_is_driving` 一处（VAL 状态），final 每轮都有。**盖在 `Handle` 出口而不是照抄两处云端路径再加一处**，是因为本地快路径（`:680` / `:765` / `:831` / `:905` / `:1008`）、混合路径的本地 final、云端降级兜底的 final 都不经那两处——缺陷 C 的形态正是这些简单轮；出口是唯一漏斗（`Handle` 头注原话「端侧是所有请求的漏斗」）。云端自带的 `driving`（Planner 不产它，但字段一旦在 proto 里就可能被填）在出口被 Edge **覆盖**：Edge 是车辆状态真相源。

- [ ] **步骤 0：方案 §11.1 B5 行回写（先改文档再改实践；单独一个 docs 提交）**

`docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md` §11.1 表的 B5 行末尾追加一句：「**（2026-09-04 泓舟裁决回写）**B5 实际范围按 [`2026-09-04-mobile-ux-v2-b5-implementation-plan.md`](2026-09-04-mobile-ux-v2-b5-implementation-plan.md) §0 第 2 条：缺陷 C（后端 final 标 `driving`）+ 一趟重建（foldstate / expo-battery / Shortcuts / 无 AEC 对照）+ B4 开项；U5 只带 Shortcuts，Live Updates / 小组件 / QS Tile 仍随 M5；默认助理角色不启用（B3′ 实证）」；文件顶部状态行把「下一步」改成「B5 计划已拆（草案待批）」。版本号不 bump（范围记录，不是设计变更）。

```bash
git commit -m "docs(design): 方案 §11.1 B5 行回写——B5 实际范围按 2026-09-04 裁决（缺陷 C + 重建趟 + B4 开项；U5 只带 Shortcuts，其余随 M5）" -- docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md && git show --stat HEAD
```

- [ ] **步骤 1：写失败测试** `orchestrator/edge/tests/test_driving_stamp.py`

```python
"""B5 缺陷 C：Edge 在 final 帧上也标 driving（裁决点仍只有 server.py::_is_driving 一处）。

process 帧只有复杂轮才有；简单轮从不带标 ⇒ 客户端「Edge 标 false 起 30s 退出」那条 false
可能永远不到（B4 真机实测行车档 3h12m 退不出）。final 每轮都有，在 Handle 出口统一盖。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.orchestrator.v1 import orchestrator_pb2

from server import EdgeOrchestratorServicer

# 端侧快路径（hvac.on 在 LOCAL_INTENTS，无 process 帧）——正是缺陷 C 的形态。
# 跑红前先核一眼它真走本地：`_finals` 为空就换 LOCAL_INTENTS 表里任一句。
_LOCAL = "打开空调"
# 端侧认得出、不在 LOCAL_INTENTS、VAL 执行得了（test_cloud_degraded_fallback 同一句，理由见那边）
_CLOUD_ROUTED = "开启露营模式"


def _drive(srv, text: str):
    req = orchestrator_pb2.HandleRequest(
        text=text, session_id="s-driving-stamp", meta={"memory_enabled": "false"})

    async def run():
        return [ev async for ev in srv.Handle(req, None)]

    return asyncio.run(run())


def _servicer(cloud_events):
    srv = EdgeOrchestratorServicer()

    async def fake_cloud_handle(req):
        for ev in cloud_events:
            yield ev

    srv.cloud.handle = fake_cloud_handle
    return srv


def _finals(events):
    return [ev.final for ev in events if ev.WhichOneof("event") == "final"]


def _progress(events):
    return [ev.progress for ev in events if ev.WhichOneof("event") == "progress"]


def _cloud_events():
    return [
        orchestrator_pb2.HandleEvent(progress=orchestrator_pb2.ProcessUpdate(
            phase="analyze", label="规划", status="done")),
        orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech="好的")),
    ]


def _moving(srv):
    srv.val.state["speed_kmh"] = 30
    srv.val.state["gear"] = "D"


def _parked(srv):
    srv.val.state["speed_kmh"] = 0
    srv.val.state["gear"] = "P"


def test_local_fast_path_final_is_stamped_true_when_moving():
    srv = _servicer([])
    _moving(srv)
    finals = _finals(_drive(srv, _LOCAL))
    assert finals, "这句没走本地快路径——换 LOCAL_INTENTS 里的一句"
    assert all(f.driving is True for f in finals)


def test_local_fast_path_final_is_stamped_false_when_parked():
    srv = _servicer([])
    _parked(srv)
    finals = _finals(_drive(srv, _LOCAL))
    assert finals
    assert all(f.driving is False for f in finals)


def test_cloud_path_stamps_both_progress_and_final():
    srv = _servicer(_cloud_events())
    _moving(srv)
    events = _drive(srv, _CLOUD_ROUTED)
    assert [p.driving for p in _progress(events)] == [True]
    assert [f.driving for f in _finals(events)] == [True]


def test_gear_d_at_zero_speed_counts_as_driving():
    """与 _is_driving 同口径：红灯停车挡位 D 仍算行车（退出只在 P/N 且零速）。"""
    srv = _servicer([])
    srv.val.state["speed_kmh"] = 0
    srv.val.state["gear"] = "D"
    assert all(f.driving for f in _finals(_drive(srv, _LOCAL)))


def test_edge_overrides_cloud_supplied_driving():
    """Edge 是车辆状态真相源：云端 final 自带 driving=True 也按 VAL 盖成 False。"""
    srv = _servicer([orchestrator_pb2.HandleEvent(
        final=orchestrator_pb2.FinalResult(speech="x", driving=True))])
    _parked(srv)
    assert [f.driving for f in _finals(_drive(srv, _CLOUD_ROUTED))] == [False]
```

- [ ] **步骤 2：跑红**

```powershell
python -m pytest orchestrator/edge/tests/test_driving_stamp.py -q
```

Expected: 5 条红，且**红法要对**——前四条 `AttributeError`/`ValueError: Protocol message FinalResult has no "driving" field`（proto 还没改），不是 assertion。红法不对（比如 `_finals` 为空）先修 fixture 不动实现。

- [ ] **步骤 3：改 proto + 重新生成**

`orchestrator.proto` 的 `FinalResult` 末尾（`closed_operation_ids = 8;` 之后）：

```proto
  // B5 缺陷 C：终态也标行车态（与 ProcessUpdate.driving 同一裁决点 server.py::_is_driving）。
  // process 帧只有复杂轮才有，简单轮从不带标 ⇒ 客户端「Edge 标 false 起 30s 退出」那条 false
  // 可能永远不到（B4 真机实测行车档 3h12m 退不出）。final 每轮都有，所以也标；Edge 在
  // Handle 出口盖，云端填的值无权威。缺省 false = 泊车。
  bool driving = 9;
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\gen-proto.ps1
Select-String -Path gen\python\cockpit\orchestrator\v1\orchestrator_pb2.py -Pattern 'driving' | Measure-Object   # 计数 ≥ 2（Process + Final）
Select-String -Path gen\go\cockpit\orchestrator\v1\orchestrator.pb.go -Pattern 'func \(x \*FinalResult\) GetDriving'   # 恰 1 条
git status --short -- gen/    # 必须为空：gen/ 是 gitignore 的，不要 force-add
```

- [ ] **步骤 4：实现（`server.py`，三处）**

① `:1021-1025` 整个函数替换：

```python
    def _stamp_driving(self, event):
        """给过程区与终态事件标注行车态（Edge 是车辆状态真相源；裁决点只有 _is_driving 一处）。

        B5 缺陷 C：process 帧只有复杂轮才有，简单轮从不带标 ⇒ 客户端「Edge 标 false 起 30s
        退出」的那条 false 可能永远不到（B4 真机实测 3h12m 退不出）。final 每轮都有，所以也标；
        在 Handle 出口盖一次——本地快路径 / 混合 / 降级兜底的 final 都不经云端路径。
        其它事件原样返回。"""
        which = event.WhichOneof("event")
        if which == "progress":
            event.progress.driving = self._is_driving()
        elif which == "final":
            event.final.driving = self._is_driving()
        return event
```

② `Handle`（`:535-537`）：

```python
            async for event in self._handle_impl(request, context, turn):
                event = self._stamp_driving(event)  # B5 缺陷 C：过程区 + 终态在唯一出口标行车态
                which = event.WhichOneof("event")
```

③ 删掉 `:810` 与 `:947` 两行 `event = self._stamp_progress(event)  # 过程区事件标注行车态`（`grep -n _stamp_progress orchestrator/ gateway/ test/` 之后应为 0 命中）。

- [ ] **步骤 5：跑绿 + 相关目录**

```powershell
python -m pytest orchestrator/edge/tests/test_driving_stamp.py -q     # 5 passed
python -m pytest orchestrator/edge/tests -q                              # 全绿（基线条数 +5）
python -m pytest test/test_remaining_e2e_protocol.py -q                  # 协议契约测试不受影响
```

- [ ] **步骤 6：反向验证（每条先 `grep` 证明变异落盘；用副本还原；预期是待证命题，实红写进 §6.1）**

| # | 变异 | 预期红 |
|---|---|---|
| M1 | `_stamp_driving` 的 `elif which == "final"` 分支删掉 | 4 条（cloud 那条 progress 半仍绿） |
| M2 | 出口那行调用删掉、把两处旧调用加回去 | 本地两条 + 覆盖那条红；cloud 那条绿（**这就是「为什么要盖在出口」的证据**） |
| M3 | `_is_driving` 的 `gear in ("D","R","S")` 改成只看 `speed > 0` | 恰红「挡位 D 零速」1 条 |

- [ ] **步骤 7：提交**

```bash
git add -- orchestrator/edge/tests/test_driving_stamp.py && git commit -m "feat(edge): B5-1 缺陷 C——FinalResult.driving（proto 字段 9）+ Edge 在 Handle 出口统一标 progress/final 行车态（_stamp_driving，裁决点仍只 _is_driving 一处）；简单轮从此也带标，客户端 30s 退出的 false 不再可能永远不到" -- proto/cockpit/orchestrator/v1/orchestrator.proto orchestrator/edge/server.py orchestrator/edge/tests/test_driving_stamp.py && git show --stat HEAD
```

---

### Task 2: 缺陷 C 网关半——`eventToMap` final 分支透传 + Go 单测 + 协议契约文档 + 本地 e2e 断言

**Files:**
- 修改 `gateway/edge/main.go:417-420`、`docs/design/2026-08-24-mobile-app-implementation-plan.md:145`、`test/e2e_process_region.py:108-113` 与 `:133-135`
- 新建 `gateway/edge/frames_test.go`

**为什么**：网关是 gRPC → WS 的唯一翻译点，字段不透传客户端永远看不到。**恒带键、不 omitempty**（与 process 分支 `:409` 同款）：客户端要能分辨「false」与「旧网关没有这个键」——后者不许登记（T3）。契约文档第 145 行是 mobile 主计划的协议真相源（「指认源码行号不转述默写」），加键要同步。本地栈 e2e `e2e_process_region.py` 已有「默认泊车态 process driving=false」断言，final 补一条同款——它不在 cloud 缺省集（`remote_safe`）里，云上的证据是 T5。

- [ ] **步骤 1：写失败测试** `gateway/edge/frames_test.go`

```go
package main

import (
	"testing"

	orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"
)

// B5 缺陷 C：final 帧也透传 driving（与 process 分支同款、恒带键不 omitempty——
// 客户端要能分辨「false」与「旧网关没有这个键」）。
func TestEventToMapFinalCarriesDriving(t *testing.T) {
	for _, want := range []bool{true, false} {
		ev := &orchpb.HandleEvent{Event: &orchpb.HandleEvent_Final{
			Final: &orchpb.FinalResult{Speech: "好的", Driving: want}}}
		m := eventToMap(ev)
		got, ok := m["driving"]
		if !ok {
			t.Fatalf("final frame lacks driving key (want %v): %v", want, m)
		}
		if got != want {
			t.Fatalf("driving=%v want %v", got, want)
		}
	}
}

func TestEventToMapProcessStillCarriesDriving(t *testing.T) {
	ev := &orchpb.HandleEvent{Event: &orchpb.HandleEvent_Progress{
		Progress: &orchpb.ProcessUpdate{Phase: "analyze", Driving: true}}}
	if got := eventToMap(ev)["driving"]; got != true {
		t.Fatalf("process driving=%v", got)
	}
}
```

- [ ] **步骤 2：跑红**

```powershell
go test ./gateway/edge/ -run 'TestEventToMap' -v
```

Expected: `TestEventToMapFinalCarriesDriving` FAIL（`final frame lacks driving key`）；process 那条 PASS。编译错 `Driving undefined` ⇒ T1 步骤 3 的 `gen/go` 没生成，回去。

- [ ] **步骤 3：实现（`main.go:417-420` 的 `result` 字面量）**

```go
		result := map[string]any{
			"type": "final", "speech": f.Speech, "follow_up": f.FollowUp,
			"need_confirm": f.NeedConfirm, "actions": actions,
			// B5 缺陷 C：终态也透传行车态（与 process 分支同款、恒带键不 omitempty——
			// 客户端要能分辨「false」与「旧网关没有这个键」；HMI 不读它）
			"driving": f.Driving,
		}
```

- [ ] **步骤 4：跑绿 + 全包**

```powershell
go test ./gateway/edge/ -v -run 'TestEventToMap'     # 2 PASS
go test ./gateway/...                                  # 全绿
go vet ./gateway/edge/
```

- [ ] **步骤 5：契约文档 + 本地 e2e 断言**

`docs/design/2026-08-24-mobile-app-implementation-plan.md:145` 的 `final` 行键列表加 `driving`，并在说明里加一句「`driving`（B5 缺陷 C）：Edge 在出口按 VAL 标的行车态，**恒带键**；客户端只在键为布尔时登记（旧网关兼容）」。

`test/e2e_process_region.py`：步骤 2（普通车控）之后追加

```python
    final_simple = next((e for e in ev if e.get("type") == "final"), None)
    _assert(final_simple is not None and final_simple.get("driving") is False,
            "简单轮 final 带 driving=false（B5 缺陷 C：final 也标，键在场）")
```

步骤 3 的 `driving=false（可展开）` 断言之后追加

```python
    _assert(final is not None and final.get("driving") is False,
            "复杂轮 final 带 driving=false（默认泊车态）")
```

`python -m pytest test/test_remaining_e2e_protocol.py -q` 仍绿（它 mock 的 final 没有 driving，不受影响——它测的是别的契约）。本地栈 e2e 本批不跑（`target=cloud` 禁本地 Compose），记「改了断言、未在本地栈复跑」。

- [ ] **步骤 6：反向验证**：M1 删掉 `"driving": f.Driving,` ⇒ 恰红 final 那条；M2 改成 `if f.Driving { result["driving"] = true }`（omitempty 形态）⇒ 红在 `want false` 那半——**这一红就是「恒带键」的理由**。

- [ ] **步骤 7：提交**

```bash
git add -- gateway/edge/frames_test.go && git commit -m "feat(gateway): B5-2 缺陷 C——eventToMap final 分支透传 driving（恒带键，与 process 同款）+ Go 测；协议契约文档 final 行加键；e2e_process_region 简单轮/复杂轮 final 各加 driving=false 断言" -- gateway/edge/main.go gateway/edge/frames_test.go docs/design/2026-08-24-mobile-app-implementation-plan.md test/e2e_process_region.py && git show --stat HEAD
```

---
### Task 3: 缺陷 C 客户端半——final 同吃 `driving`（只认布尔）+ 「本段」语义 + `dismissedAt` 判据 + 取证屏（泓舟裁决「客户端 `recordEdgeDriving` 同吃 final，process 那一路不动」「退出只压本次、不改判据」）

**Files:**
- 修改 `mobile/src/core/presence/drivingMode.ts`、`mobile/test/drivingMode.test.ts`（+5）、`mobile/src/core/session/store.ts:72` / `:180` / `:562`、`mobile/test/sessionStore.test.ts`（+4）、`mobile/src/app/native-spike.tsx:30-39`

**为什么**：三件事一份判据。① **final 同吃**：网关从此每轮都带键，但设备上可能连着**旧网关**（云栈没部署、或将来回滚）——旧 final 没这个键，`!!undefined` 会把每个简单轮都当成「Edge 标 false」，行车中 30s 后就退出，那是比缺陷 C 反向的缺陷 ⇒ **只在 `typeof data.driving === 'boolean'` 时登记**；process 那一路本来就恒带键，一字不动。② **「本次」= 本段**：泓舟裁「退出只压本次、不改判据」——现在 `recordEdgeDriving` 每条 true 都刷新 `trueAt`，final 也标之后每轮都会刷新，「本次」就没有稳定的边界；把 `trueAt` 收紧成「当前行车段的起点」（从非行车转行车那一刻，段内连续 true 不刷新），`dismissedAt ≥ trueAt` 即压住本段，下一次非行车 → 行车（新段）照常自动进入。既有用例（`{trueAt:1, falseAt:5}` 来 true ⇒ `{10, 0}`）仍成立——那是 false 段后的新段。③ `dismissedAt` 与 `drivingEdge` 同命：只在内存 `SessionState`，重启即清（B4 实测「App 重启是缺陷 C 的唯一出路」——现在它只是清掉一次退出）。取证屏接真实事实（B4 缺陷 B 的教训：取证装置写死参数会说谎）。

- [ ] **步骤 1：写失败测试**

`mobile/test/drivingMode.test.ts` 追加：

```ts
describe('B5-3 「本次」= 本段：trueAt 是段起点', () => {
  test('段内再来 true 不刷新起点（final 每轮都标之后尤其重要）', () => {
    expect(recordEdgeDriving({ trueAt: 10, falseAt: 0 }, true, 20)).toEqual({ trueAt: 10, falseAt: 0 })
  })
  test('false 段之后再来 true 开新段（起点刷新、falseAt 清零）', () => {
    expect(recordEdgeDriving({ trueAt: 10, falseAt: 20 }, true, 25)).toEqual({ trueAt: 25, falseAt: 0 })
  })
})

describe('B5-3 用户退出只压本段（dismissedAt），不改判据', () => {
  const edge = { trueAt: NOW - 60_000, falseAt: 0 }
  test('退出时刻 ≥ 段起点 ⇒ 本段不算行车，哪怕 Edge 仍标 true', () => {
    expect(drivingActive({ manual: false, edge, now: NOW, dismissedAt: NOW - 30_000 })).toBe(false)
  })
  test('新段起点晚于退出时刻 ⇒ 自动进入照常', () => {
    const fresh = { trueAt: NOW - 10_000, falseAt: 0 }
    expect(drivingActive({ manual: false, edge: fresh, now: NOW, dismissedAt: NOW - 30_000 })).toBe(true)
  })
  test('手动开压过退出（退出只针对自动进入）', () => {
    expect(drivingActive({ manual: true, edge, now: NOW, dismissedAt: NOW })).toBe(true)
  })
})
```

`mobile/test/sessionStore.test.ts` 在 `process：execute 步按 step_id 合并…` 那条之后追加：

```ts
  test('B5-3 final 带 driving:true ⇒ 登记 trueAt（简单轮也能进行车档）', () => {
    const { transport, core } = newCore()
    core.send('打开空调')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '好的', driving: true })
    expect(core.store.getState().drivingEdge.trueAt).toBeGreaterThan(0)
    expect(core.store.getState().drivingEdge.falseAt).toBe(0)
    core.dispose()
  })

  test('B5-3 true 段后 final 带 driving:false ⇒ 登记 falseAt（缺陷 C 的退出事件从此每轮都有）', () => {
    const { transport, core } = newCore()
    core.send('打开空调')
    core.handleFrame({ type: 'final', request_id: transport.lastUserFrame().request_id, speech: '好的', driving: true })
    core.send('关闭空调')
    core.handleFrame({ type: 'final', request_id: transport.lastUserFrame().request_id, speech: '好的', driving: false })
    const e = core.store.getState().drivingEdge
    expect(e.falseAt).toBeGreaterThan(0)
    expect(e.falseAt).toBeGreaterThanOrEqual(e.trueAt)
    core.dispose()
  })

  test('B5-3 final 无 driving 键（旧网关）⇒ drivingEdge 一字不动', () => {
    const { transport, core } = newCore()
    core.store.setState({ drivingEdge: { trueAt: 5, falseAt: 0 } })
    core.send('打开空调')
    core.handleFrame({ type: 'final', request_id: transport.lastUserFrame().request_id, speech: '好的' })
    expect(core.store.getState().drivingEdge).toEqual({ trueAt: 5, falseAt: 0 })
    core.dispose()
  })

  test('B5-3 dismissDriving 写 drivingDismissedAt（内存态，初值 0）', () => {
    const { core } = newCore()
    expect(core.store.getState().drivingDismissedAt).toBe(0)
    core.dismissDriving()
    expect(core.store.getState().drivingDismissedAt).toBeGreaterThan(0)
    core.dispose()
  })
```

- [ ] **步骤 2：跑红** `cd mobile && npx jest test/drivingMode.test.ts test/sessionStore.test.ts` ⇒ 段语义 1 条红（`{20,0}` vs `{10,0}`）、dismissed 2 条红（tsc 层是 `dismissedAt` 不在参数类型里）、store 侧 final 两条红 + `dismissDriving` 不存在。⚠ 「false 段后开新段」与「手动压过」这两条在实现之前就是绿的——它们钉的是不变量，红只在反向验证里看得见（B4 §6.3 那条纪律）。

- [ ] **步骤 3：实现**

`drivingMode.ts`（头注与两个函数整体替换；`composerInputMode / sheetResident / drivingSuggested` 不动）：

```ts
// mobile/src/core/presence/drivingMode.ts
// 行车档判据（B4-2 / 方案 §6；B5-3 缺陷 C）——纯函数、零 RN import。
// 事实只有两种来源：① Edge 按 VAL 标注的 `driving`（server.py::_is_driving 是唯一裁决点：speed_kmh > 0 或
//    挡位 D/R/S）——B4 只在 process 帧上、B5 起 final 帧也带（缺陷 C：简单轮从不带标 ⇒ 退出事件可能永远不到）；
//    客户端**不**用 vehicle_state 的 speed_kmh/gear 再算一份——那是第二份判据，§5.3.1 删的就是它；② 用户手动开关。
// 退出：Edge 标 false 起持续 30s（§6「退出」），或手动关，或**用户退出本段**（dismissedAt：只压住当前行车段，
//    下一次由非行车转行车的新段照常自动进入——「退出只压本次、不改判据」，泓舟 2026-09-04）。
// 「段」：trueAt 是段起点（从非行车转行车那一刻），段内连续 true 不刷新——final 每轮都标之后，
//    没有这条就没有稳定的「本次」边界。
import type { Identity } from './presence'

export const DRIVING_EXIT_GRACE_MS = 30_000

export interface DrivingEdgeFact {
  /** 当前行车段的起点：由非行车转为 true 的那一刻；段内连续 true 不刷新；0=从未 */
  trueAt: number
  /** 最近一次「由 true 转 false」的时刻（连续 false 不刷新——「持续 30s」从第一条 false 起算）；0=无 */
  falseAt: number
}

export const NO_EDGE_DRIVING: DrivingEdgeFact = { trueAt: 0, falseAt: 0 }

export function drivingActive(f: {
  manual: boolean
  edge: DrivingEdgeFact
  now: number
  /** 用户在设置页「自动行车中 · 退出」的时刻；0/缺省=没退过。只压住 trueAt ≤ dismissedAt 的那一段 */
  dismissedAt?: number
}): boolean {
  if (f.manual) return true
  if (f.edge.trueAt <= 0) return false
  if ((f.dismissedAt ?? 0) >= f.edge.trueAt) return false
  if (f.edge.falseAt <= f.edge.trueAt) return true
  return f.now - f.edge.falseAt < DRIVING_EXIT_GRACE_MS
}

/** process / final 帧到达时的事实登记（SessionCore 调用；写成 reducer 是为了可测） */
export function recordEdgeDriving(prev: DrivingEdgeFact, driving: boolean, now: number): DrivingEdgeFact {
  if (driving) {
    const inSegment = prev.trueAt > 0 && prev.falseAt <= prev.trueAt
    return inSegment ? prev : { trueAt: now, falseAt: 0 }
  }
  if (prev.trueAt <= 0) return prev // 从没行车过，false 不需要记
  if (prev.falseAt > prev.trueAt) return prev // 已在 false 段里：不刷新起点
  return { trueAt: prev.trueAt, falseAt: now }
}
```

`store.ts`：`SessionState` 在 `drivingEdge` 之后加

```ts
  /** 用户在设置页退出**自动进入**的行车档的时刻（B5-3 缺陷 C）：只压住当前行车段，判据在 drivingMode.ts；
   *  与 drivingEdge 同命——只在内存，重启即清 */
  drivingDismissedAt: number
```

初值（`:180` 之后）`drivingDismissedAt: 0,`。`handleFrame` 的 final 分支**顶部**（`if (data.type === 'final') {` 之后、`emotion` 之前）：

```ts
      // B5-3 缺陷 C：final 帧也带 driving（网关 eventToMap final 分支透传；产出方 server.py::_stamp_driving）。
      // **只认布尔**——旧网关的 final 没有这个键，`!!undefined` 会把每个简单轮都当成「Edge 标 false」，
      // 行车中 30s 后就退出：那是比缺陷 C 反向的缺陷。process 那一路（上面）不动。放在 rejected 之前：
      // 拒识轮也是一轮，行车事实与拒识无关。
      if (typeof data.driving === 'boolean') {
        const drivingNow = data.driving
        this.store.setState((s) => ({ drivingEdge: recordEdgeDriving(s.drivingEdge, drivingNow, Date.now()) }))
      }
```

类里加方法（挨着 `cancelCurrentTurn`）：

```ts
  /** 行车档手动退出（B5-3 缺陷 C 的 UI 出口）：只压住**本段**，不改判据；下一段照常自动进入 */
  dismissDriving(): void {
    this.store.setState({ drivingDismissedAt: Date.now() })
  }
```

`native-spike.tsx:30-39`：`edge` 旁边同款订阅一个 `dismissedAt`（`core.store.getState().drivingDismissedAt`），`drivingActive({ manual: settings.drivingManual, edge, now: Date.now(), dismissedAt })`，`driving` 行改成 `` `${driving}（manual=${settings.drivingManual} edge.trueAt=${edge.trueAt} edge.falseAt=${edge.falseAt} dismissedAt=${dismissedAt}）` ``。

- [ ] **步骤 4：跑绿 + tsc + 全量** `npm test`（499 → ≈508）、`npm run typecheck` 0。

- [ ] **步骤 5：反向验证**（副本还原；预期待证）

| # | 变异 | 预期红 |
|---|---|---|
| M1 | `recordEdgeDriving` 的 `inSegment` 恒 false（退回旧语义） | 恰红「段内不刷新」1 条 |
| M2 | `drivingActive` 删 `dismissedAt` 那行 | 红「压住本段」1 条；「新段」「手动」仍绿（这两条不是它的） |
| M3 | store 的 `typeof === 'boolean'` 改成 `!!data.driving` 无条件登记 | 恰红「旧网关不动」1 条——**这一红就是兼容那条的理由** |
| M4 | `dismissedAt ≥ trueAt` 改成 `>` | 红？——按 fixture 是 `NOW-30_000 ≥ NOW-60_000` 仍成立，**预期零红**；补一条 `dismissedAt === trueAt` 的边界用例让它红（同一毫秒退出算退出） |

- [ ] **步骤 6：提交**

```bash
git commit -m "feat(mobile): UX v2 B5-3 缺陷 C 客户端半——final 帧同吃 driving（只认布尔，旧网关兼容）；trueAt 收紧为段起点；drivingActive 加 dismissedAt（退出只压本段不改判据）；SessionCore.dismissDriving；取证屏接 dismissedAt" -- mobile/src/core/presence/drivingMode.ts mobile/test/drivingMode.test.ts mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts mobile/src/app/native-spike.tsx && git show --stat HEAD
```

---

### Task 4: 缺陷 C 的 UI 出口——设置页「自动行车中 · 退出」+ `usePresence` 接线（泓舟裁决「行车档开关在自动进入时显示『自动行车中 · 退出』」）

**Files:**
- 修改 `mobile/src/features/settings/SettingsScreen.tsx:184-189`、`mobile/src/features/chat/usePresence.ts:56` / `:131`

**为什么**：B4 真机轮的视觉证据 `b4-14-t3.png`：开关关着而 App 在行车档里，用户在 UI 上找不到任何可以退出的地方。裁决把出口放在行车档开关那一行——不在胶囊：胶囊只有一个槽、按优先级让位（收音 / 播报 / 错误都压过它），一个常驻的「自动行车中」会跟 armed / listening 抢位；设置页是用户找「为什么它长这样」时会去的地方（B4 T10 的身份说明行也在这）。**判据不复制**：页面只搬事实（`drivingEdge` / `drivingDismissedAt` / `drivingManual`）给 `drivingActive`。设置页没有秒级 tick：30s 宽限到点那一跳这一行不会自己消失（下一次 store 变化才重渲）——记在注释里，不加 ticker（取证屏同一理由，B4 §6.2 坑⑨）。

- [ ] **步骤 1：接线（无 jest 面——`.tsx`；判据在 T3 已钉）**

`usePresence.ts:56` 解构加 `drivingDismissedAt`；`:131` 改成
`const drivingNow = drivingActive({ manual: settings.drivingManual, edge: drivingEdge, now, dismissedAt: drivingDismissedAt })`。

`SettingsScreen.tsx`：import 加 `import { drivingActive, NO_EDGE_DRIVING } from '../../core/presence/drivingMode'` 与 `import { getWired } from '../../core/session/wiring'`；组件顶部（`settings` 解构之后）：

```tsx
  // B5-4 缺陷 C：自动进入的行车档要有可发现的退出口（B4 真机：开关灰着、App 在行车档里、3h12m 退不出）。
  // 只搬事实不复制判据（drivingActive 是唯一一份）；订阅法照 native-spike。无 ticker：30s 宽限到点
  // 这一行不会自己消失，下一次 store 变化才重渲（与取证屏同一取舍）。
  const core = getWired()?.core ?? null
  const readDriving = () => {
    const st = core?.store.getState()
    return { edge: st?.drivingEdge ?? NO_EDGE_DRIVING, dismissedAt: st?.drivingDismissedAt ?? 0 }
  }
  const [drivingFact, setDrivingFact] = useState(readDriving)
  useEffect(() => {
    if (!core) return
    setDrivingFact(readDriving()) // 挂载与订阅之间的缝
    return core.store.subscribe(() => setDrivingFact(readDriving()))
  }, [core])
  const autoDriving =
    !settings.drivingManual &&
    drivingActive({ manual: false, edge: drivingFact.edge, now: Date.now(), dismissedAt: drivingFact.dismissedAt })
```

`:184-189` 的 `SwitchRow` desc 改为 `"目标放大、过程区单行、文本输入按角色收起。座舱判定行车时自动进入（每轮回答后判定），停车 30 秒后自动退出"`，其后追加：

```tsx
        {autoDriving ? (
          <Pressable
            testID="driving-auto-exit"
            accessibilityRole="button"
            accessibilityLabel="自动行车中，退出行车档"
            onPress={() => core?.dismissDriving()}
            style={{
              minHeight: 48,
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingHorizontal: 12,
              borderRadius: 10,
              backgroundColor: p.accentSoft,
            }}
          >
            <Text style={{ color: p.fg2, fontSize: p.font(13) }}>自动行车中 · 座舱判定为行驶</Text>
            <Text style={{ color: p.accent, fontSize: p.font(13), fontWeight: '600' }}>退出</Text>
          </Pressable>
        ) : null}
```

- [ ] **步骤 2：tsc + 全量** `npm run typecheck` 0；`npm test` 条数不变。

- [ ] **步骤 3：Metro 热载冒烟（不需要云栈）**：设置页行车档手动开 ⇒ **不**出现退出行（手动的退出口就是开关）；关 ⇒ 无行。`/native-spike` 的 `driving` 行有 `dismissedAt=0`。真正的正例要 Edge 标（T5 步骤 4）。

- [ ] **步骤 4：提交**

```bash
git commit -m "feat(mobile): UX v2 B5-4 缺陷 C 的 UI 出口——设置页行车档自动进入时显示「自动行车中 · 退出」（testID driving-auto-exit，退出只压本段）；usePresence 喂 dismissedAt" -- mobile/src/features/settings/SettingsScreen.tsx mobile/src/features/chat/usePresence.ts && git show --stat HEAD
```

---

### Task 5: 缺陷 C 真机读数——cloud deploy（两道泓舟闸）+ 「注入 `speed_kmh=0` 后一句简单轮即退出」+ 手动退出口 + `Msg.driving` 单独验（泓舟裁决要求的读数；§0 第 4 条）

**Files:**（取证；`mobile/e2e/artifacts/b5-5-*.png`，gitignore）

**为什么**：后端改动的证据只能在云栈上取（`target=cloud`，Edge 与网关都在云上）。deploy 是 CLAUDE.md §6.1 的受控动作：先 dry-run、再单独授权 `--apply`；SHA 要干净、已提交、main 可达——前置可能还要一次推送授权。两道闸过不了就把 T1–T4 的宿主侧读数收口，本任务记 ⬜。读数要有**阴性**：同一句简单轮在泊车态不进行车档（B4 之前它就是这样，现在也要还是这样）。

- [ ] **步骤 0：前提**：T1–T4 已提交、`git status` 干净；`python scripts/dev_stack.py target show` = cloud；`lastUpdateTime=2026-09-02 16:45:36`；`adb reverse --list` 非空；云栈 `speed_kmh=0 / gear=P` 基线回读（`GET /api/vehicle/state`）；App 设置基线记一行（角色 / 行车档 / 免唤醒 / 播报）。

- [ ] **步骤 1：deploy dry-run（PowerShell）**

```powershell
python scripts/dev_stack.py deploy --sha HEAD
```

读 `status`：`plan_rejected` 且 `blocking_changes` 含 `.env.example` / CI 路径 ⇒ 本批没动这些，先查是不是别人的提交混进来了；报 SHA 不可达 / 未推送 ⇒ **停**，`git log --oneline origin/main..HEAD` 逐条念归属，向泓舟要**推送授权**（红线），推完重跑 dry-run。dry-run 通过 ⇒ 记 `deployed_sha` 基线。

- [ ] **步骤 2：`--apply`（**单独授权**）** `python scripts/dev_stack.py deploy --sha HEAD --apply` → `python scripts/dev_stack.py verify` → `python scripts/run_e2e.py --target cloud` exit 0（廉价保险，不当 canonical）；`/debug` 屏发一句「讲个笑话」⇒ final 帧里**逐字**出现 `"driving":false`（键在场——这是网关半的云上证据）。

- [ ] **步骤 3：核心读数——注入 → 简单轮进入 → 注入 0 → 简单轮退出**

```powershell
$fqdn = (Select-String -Path .env -Pattern '^TAILNET_FQDN=' | ForEach-Object { $_.Line.Split('=')[1].Trim() })
Invoke-RestMethod -Method Post -Uri "https://$fqdn`:8446/api/debug/vehicle" -ContentType 'application/json' -Body '{"key":"speed_kmh","value":30}'
Invoke-RestMethod -Uri "https://$fqdn`:8446/api/vehicle/state"      # 回读 speed_kmh=30 / gear=D
```

角色 C、行车档关、免唤醒关 → 说 / 输一句**简单轮**「打开空调」（B4 时它**不会**进行车档——这正是缺陷 C；`/debug` 屏里这一轮**没有** process 帧、final 带 `driving:true`）⇒ `/native-spike`：`driving: true（manual=false edge.trueAt=<刚才> …）`，输入框消失、`composer-send` 56.0dp（`uiautomator dump` + `target_probe.py --density 480 --min 56 composer-send`），截 `b5-5-simple-enter.png`。
然后 `speed_kmh=0` + `gear=P` 注入并回读 → 再说一句简单轮「关闭空调」⇒ `edge.falseAt` 从 0 变成刚才的时刻（**这就是 B4 3h12m 里从没发生过的那件事**）→ 30s 后（记时刻差，判据 `DRIVING_EXIT_GRACE_MS`）`driving: false`、输入框回来、`composer-send` 44.0dp FAIL（探针量得出差别）。截 `b5-5-simple-exit.png`。
**阴性**：泊车态（0 / P）再说一句简单轮 ⇒ `trueAt` 仍 0、不进行车档。

- [ ] **步骤 4：手动退出口**：注入 30 → 简单轮 → 行车档开 → 设置页出现「自动行车中 · 座舱判定为行驶 / 退出」（`b5-5-exit-row.png`；`uiautomator dump` 里 `driving-auto-exit` 在）→ 点「退出」⇒ 立即：行已消失、`/native-spike` `driving: false … dismissedAt=<刚才>`、输入框回来（**不等 30s**——退出不走宽限）。→ 仍在 30 时再说一句简单轮 ⇒ **仍 false**（同段，`trueAt` 没刷新——这一格证明「段」语义）→ 注入 0/P → 简单轮 → 注入 30 → 简单轮 ⇒ **重新自动进入**（新段起点 > dismissedAt）。回读角色与开关。

- [ ] **步骤 5：手动开关不受影响**：行车档手动开 ⇒ 无退出行、行车档开；关 ⇒ 立刻退（B4 T10 读数复核一次）。

- [ ] **步骤 6：`Msg.driving` 单独验（B4 §6.3 遗留⑤）**：注入 30 → 说一句会出过程区的复杂任务「帮我规划从深圳到广州的充电路线」（Maestro `inputText` 能输中文，B4 坑 60 已推翻；或泓舟手输）⇒ 气泡带「过程 N 步」且 `Msg.driving=true` → 注入 0/P → 一句简单轮 → 30s 退出 ⇒ **回看那条老气泡：过程区仍不可展开**（`Msg.driving` 那一支单独起作用）；退出后新一轮复杂任务的过程区**可展开**（阴性对照）。截 `b5-5-msg-driving.png`。

- [ ] **步骤 7：HMI 回归（网关多了一个键）**：`cd hmi && npm test` 全绿；`python scripts/dev_stack.py hmi` 起 Vite，浏览器一轮「讲个笑话」正常渲染、devtools 里 final 帧带 `driving`。

- [ ] **步骤 8：设备与云栈还原表**：`speed_kmh` 0 / `gear` P（回读）；App 角色 / 行车档 / 免唤醒 / 播报（回读）；**音量不在表里**（§0 第 2 条 #5）。

- [ ] **步骤 9：记录提交**（读数写 §6.1）

```bash
git commit -m "docs(mobile): UX v2 B5 §6.1 第 1 批记录——缺陷 C 云栈读数（简单轮进入/退出、手动退出口、Msg.driving 单独验、HMI 回归）" -- docs/design/2026-09-04-mobile-ux-v2-b5-implementation-plan.md && git show --stat HEAD
```

---
### Task 6: `foldstate` 查当前值——原生缓存 + `current()` + hook 初值 + 延后订阅（对账一 #3、坑 72、方案 §7.3）

**Files:**
- 修改 `mobile/modules/foldstate/android/src/main/java/com/xiaozhou/foldstate/FoldStateModule.kt`（整文件）、`mobile/modules/foldstate/index.ts`（接口 + 注释）、`mobile/src/ui/layout/useFoldState.ts`（初值 + 注释）、`mobile/src/app/native-spike.tsx`（加 `current` 行）

**为什么**：B4 两轮实证「任何新挂载的 `useFoldState` 实例都停在 flat 直到铰链再动」（`/native-spike` 在机身已半开时读 `flat / events: 0`，泓舟动一下铰链立刻 `book / events: 4`）。根因不是 WindowManager 不回推，是 Expo 的 `OnStartObserving` **只在 0→1 个订阅者时触发一次**：对话页一直挂着 `useLayout → useFoldState`，它拿到了注册时的回推；之后再挂的实例（取证屏、将来任何新页）只是第二个 JS listener，原生侧什么都不发生。B3 写的「订阅就是查询」只对第一个订阅者成立。修法：原生缓存最近一次投影，暴露同步 `current()`，hook 用它当初值——**判据（`foldPosture`）不动**，改的是事实通道。顺带堵第二个候选根因：`OnStartObserving` 时 `currentActivity` 为 null 会静默 `return`，从此永远没有监听——改成记下「想订阅」，`OnActivityEntersForeground` 时补（API 在 `ModuleDefinitionBuilder.kt:129`，核过）。

- [ ] **步骤 1：Kotlin（整文件替换；构建一趟十几分钟，先核 API 存在）**

```powershell
Select-String -Path mobile\node_modules\expo-modules-core\android\src\main\java\expo\modules\kotlin\modules\ModuleDefinitionBuilder.kt -Pattern 'fun OnActivityEntersForeground'   # 1 条
Select-String -Path mobile\node_modules\expo-modules-core\android\src\main\java\expo\modules\kotlin\objects\ObjectDefinitionBuilder.kt -Pattern 'inline fun Function\('       # 无参重载在
```

```kotlin
package com.xiaozhou.foldstate

import androidx.core.content.ContextCompat
import androidx.core.util.Consumer
import androidx.window.java.layout.WindowInfoTrackerCallbackAdapter
import androidx.window.layout.FoldingFeature
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowLayoutInfo
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

// 折叠姿态事实透传（B3 / 方案 §7.3；B5-6 补「查当前值」）。
// ⚠ 「注册监听即回推当前值 ⇒ 订阅就是查询」只对**第一个** JS 订阅者成立：Expo 的 OnStartObserving 只在
//    0→1 个订阅者时触发一次，之后再挂载的 useFoldState 实例什么都收不到，直到铰链下一次物理动作
//    （B4 两轮实证：新屏读 flat/events 0，动一下铰链立刻 book/events 4）。所以这里缓存最近一次投影，
//    JS 侧新实例用 current() 拿初值。
class FoldStateModule : Module() {
  private var adapter: WindowInfoTrackerCallbackAdapter? = null
  @Volatile private var last: Map<String, Any?>? = null
  private var wantObserving = false

  private fun project(info: WindowLayoutInfo): Map<String, Any?> {
    val fold = info.displayFeatures.filterIsInstance<FoldingFeature>().firstOrNull()
    return mapOf(
      "present" to (fold != null),
      "state" to when (fold?.state) {
        FoldingFeature.State.HALF_OPENED -> "halfOpened"
        FoldingFeature.State.FLAT -> "flat"
        else -> "none"
      },
      "orientation" to when (fold?.orientation) {
        FoldingFeature.Orientation.HORIZONTAL -> "horizontal"
        FoldingFeature.Orientation.VERTICAL -> "vertical"
        else -> "none"
      },
      "isSeparating" to (fold?.isSeparating ?: false),
      "bounds" to fold?.bounds?.let {
        mapOf("left" to it.left, "top" to it.top, "right" to it.right, "bottom" to it.bottom)
      },
    )
  }

  private val consumer = Consumer<WindowLayoutInfo> { info ->
    val m = project(info)
    last = m
    sendEvent("onFoldChange", m)
  }

  // currentActivity 为 null（冷启动早期）时原来是静默 return ⇒ 永远没有监听；现在记下「想订阅」，进前台再补
  private fun start() {
    if (adapter != null) return
    val activity = appContext.currentActivity ?: return
    val a = WindowInfoTrackerCallbackAdapter(WindowInfoTracker.getOrCreate(activity))
    adapter = a
    a.addWindowLayoutInfoListener(activity, ContextCompat.getMainExecutor(activity), consumer)
  }

  override fun definition() = ModuleDefinition {
    Name("FoldState")
    Events("onFoldChange")

    /** 最近一次投影；从未收到过事件时 null（JS 侧按 flat 降级，与原生缺席同一条路） */
    Function("current") { last }

    OnStartObserving {
      wantObserving = true
      start()
    }
    OnStopObserving {
      wantObserving = false
      adapter?.removeWindowLayoutInfoListener(consumer)
      adapter = null
    }
    OnActivityEntersForeground {
      if (wantObserving) start()
    }
  }
}
```

- [ ] **步骤 2：JS 面**

`modules/foldstate/index.ts` 的接口：

```ts
interface FoldStateNativeModule {
  addListener(event: 'onFoldChange', cb: (e: FoldEvent) => void): { remove(): void }
  /** 原生缓存的最近一次事件；从未收到过时 null（B5-6：新挂载实例的初值，见 useFoldState） */
  current(): FoldEvent | null
}
```

头注里「订阅就是查询」那句改成「订阅只对第一个订阅者是查询——其余实例用 `current()`（B5-6）」。

`src/ui/layout/useFoldState.ts`：

```ts
// 折叠姿态 hook（B3-6；B5-6 补初值）：订阅原生事件流，返回最新 FoldEvent（null=尚无事件或原生缺席）。
// ⚠ 「WindowManager 注册即回推 ⇒ 订阅即查询」只对第一个 JS 订阅者成立（Expo OnStartObserving 只触发一次）；
//    新挂载的实例要用原生缓存的 current() 当初值，否则停在 flat 直到铰链再动（B4 两轮实证，坑 72）。
//    旧 APK 没有 current（B5 之前的原生）⇒ 可选链回落 null，不崩（§9.27 铁则）。
import { useEffect, useState } from 'react'

import FoldNative, { type FoldEvent } from '../../../modules/foldstate'

export function useFoldState(): FoldEvent | null {
  const [fold, setFold] = useState<FoldEvent | null>(() => FoldNative?.current?.() ?? null)
  useEffect(() => {
    if (!FoldNative) return
    const sub = FoldNative.addListener('onFoldChange', setFold)
    return () => sub.remove()
  }, [])
  return fold
}
```

`native-spike.tsx` 的 `rows` 加一行 `['current', FoldNative?.current?.() ? JSON.stringify(FoldNative.current()) : '—（旧 APK 无 current / 从未收到事件）']`——放在 `events` 行之后。

- [ ] **步骤 3：tsc + 全量**（`npm run typecheck` 0；`npm test` 条数不变——hook 无 jest 面，判据 `foldPosture` 没动）。⚠ 热载后旧 APK 上 `current` 缺席 ⇒ 取证屏该行显示「—」、App 不崩（这是 §9.27 铁则的当场实证，记进 §6.2）。

- [ ] **步骤 4：提交**（构建在 T10；真机验收在 T11 步骤 1）

```bash
git commit -m "feat(mobile): UX v2 B5-6 foldstate 查当前值——原生缓存最近投影 + Function(current)，hook 初值读它（新挂载实例不再停在 flat 直到铰链再动，坑 72）；currentActivity 为 null 时延后到进前台再订阅" -- mobile/modules/foldstate/android/src/main/java/com/xiaozhou/foldstate/FoldStateModule.kt mobile/modules/foldstate/index.ts mobile/src/ui/layout/useFoldState.ts mobile/src/app/native-spike.tsx && git show --stat HEAD
```

---

### Task 7: `expo-battery` 低电量材质回落——`lowPower` 判据 + `usePowerFacts` 事实收集 + `blurTarget` 第三个条件（方案 §5.11 末句、对账二）

**Files:**
- 新建 `mobile/src/core/power/lowPower.ts`、`mobile/src/core/power/usePowerFacts.ts`、`mobile/test/lowPower.test.ts`
- 修改 `mobile/package.json`（`expo-battery`）、`mobile/src/features/chat/ChatScreen.tsx:432-433`、`mobile/src/app/native-spike.tsx`（`power` 行）

**为什么**：§5.11 末句「reduce-transparency / 行车档 / 低电量 ⇒ 全部回落 G0/G1-tint」，B4 只落了前两种（`ChatScreen.tsx:433` 注释明写「低电量不做（无 expo-battery）」）。真模糊是每帧的 GPU 工作（B3 T9 实测帧率 A/B 分不开，但那是满电）；省电模式下它是最该先关的东西。判据一份纯函数；事实收集按 §9.27 铁则：**先 `requireOptionalNativeModule('ExpoBattery')` 探原生，在场才 `require('expo-battery')`**——它的 JS 顶层 `requireNativeModule` 在旧 APK（第 2 批装机前的热载期）上会抛，直接 import 就是一次「崩在 import」。

- [ ] **步骤 1：装依赖 + 核原生模块名**

```powershell
cd mobile; npx expo install expo-battery
git diff -- package.json        # 恰一行新增；版本记进 §6.2
Select-String -Path node_modules\expo-battery\android\src\main\java\expo\modules\battery\BatteryModule.kt -Pattern 'Name\("'   # 应为 Name("ExpoBattery")——不是就把下面探针的名字改成它
Select-String -Path node_modules\expo-battery\build\index.d.ts -Pattern 'useBatteryLevel|useLowPowerMode'                       # 两个 hook 都在
```

- [ ] **步骤 2：写失败测试** `mobile/test/lowPower.test.ts`

```ts
// 低电量材质回落判据（方案 §5.11 末句第三种回落；B5-7）。事实来自 expo-battery，原生缺席时两项都是 null。
import { LOW_BATTERY_LEVEL, lowPower } from '@/core/power/lowPower'

describe('lowPower', () => {
  test('省电模式 ⇒ 回落，不看电量', () => {
    expect(lowPower({ level: 0.9, saver: true })).toBe(true)
  })
  test('电量低于阈值 ⇒ 回落；等于阈值不回落', () => {
    expect(lowPower({ level: LOW_BATTERY_LEVEL - 0.01, saver: false })).toBe(true)
    expect(lowPower({ level: LOW_BATTERY_LEVEL, saver: false })).toBe(false)
  })
  test('原生缺席（null / null）⇒ 不回落——旧 APK 上材质照旧', () => {
    expect(lowPower({ level: null, saver: null })).toBe(false)
  })
  test('电量未知（expo-battery 用 -1 表示）⇒ 不回落', () => {
    expect(lowPower({ level: -1, saver: false })).toBe(false)
  })
  test('阈值是 20%（§5.11 没给数，取 Android 省电提示的默认档）', () => {
    expect(LOW_BATTERY_LEVEL).toBe(0.2)
  })
})
```

- [ ] **步骤 3：跑红** ⇒ 模块不存在。

- [ ] **步骤 4：实现**

`core/power/lowPower.ts`：

```ts
// mobile/src/core/power/lowPower.ts
// 低电量材质回落判据（方案 §5.11 末句：reduce-transparency / 行车档 / 低电量 ⇒ 全部回落 G1-tint；B5-7 补第三种）。
// 纯函数、零 RN import。事实由 core/power/usePowerFacts 收集；原生缺席时两项为 null ⇒ 不回落（旧 APK 材质照旧）。
export const LOW_BATTERY_LEVEL = 0.2

export interface PowerFacts {
  /** 0–1；expo-battery 未知时 -1；原生缺席 null */
  level: number | null
  /** 系统省电模式；原生缺席 null */
  saver: boolean | null
}

export function lowPower(p: PowerFacts): boolean {
  if (p.saver) return true
  return p.level != null && p.level >= 0 && p.level < LOW_BATTERY_LEVEL
}
```

`core/power/usePowerFacts.ts`：

```ts
// 电量事实收集（B5-7）。§9.27 铁则：expo-battery 的 JS 顶层 requireNativeModule 在没带它的 APK 上会抛，
// 所以先用 requireOptionalNativeModule 探原生，在场才 require 包——旧 APK（重建前的热载期）上不崩、按 null 降级。
// `api` 是模块级常量 ⇒ 下面 hook 的调用分支在进程内恒定，不违反 rules of hooks。
import { requireOptionalNativeModule } from 'expo'

import type { PowerFacts } from './lowPower'

type BatteryApi = typeof import('expo-battery')

export const BATTERY_NATIVE_AVAILABLE = requireOptionalNativeModule('ExpoBattery') != null
// eslint-disable-next-line @typescript-eslint/no-require-imports
const api: BatteryApi | null = BATTERY_NATIVE_AVAILABLE ? (require('expo-battery') as BatteryApi) : null

export function usePowerFacts(): PowerFacts {
  if (!api) return { level: null, saver: null }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const level = api.useBatteryLevel()
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const saver = api.useLowPowerMode()
  return { level, saver }
}
```

`ChatScreen.tsx:426-433`：import `lowPower` / `usePowerFacts`；`const power = usePowerFacts()`；`blurTarget` 加第四个 `&&`：`!lowPower(power)`；注释里「低电量不做（无 expo-battery）」改成「低电量 = `lowPower(power)`（B5-7，expo-battery）」。

`native-spike.tsx` `rows` 加 `['power', \`native=${BATTERY_NATIVE_AVAILABLE} level=${power.level} saver=${power.saver} lowPower=${lowPower(power)}\`]`（组件里 `const power = usePowerFacts()`）。

- [ ] **步骤 5：跑绿 + tsc + 全量**（≈508 → ≈513）。热载到旧 APK：`/native-spike` `power: native=false level=null saver=null lowPower=false`、对话页语音层仍真模糊（`blurTarget` 告警 0 条）——**旧包上零行为变化**是这一步的判据。

- [ ] **步骤 6：反向验证**：M1 `lowPower` 的 `p.level >= 0` 删掉 ⇒ 恰红「未知 -1」1 条；M2 `if (p.saver)` 改 `if (p.saver === false)` ⇒ 红「省电模式」1 条 + 「原生缺席」仍绿（null 不等于 false——这条阴性跟着红就说明测试写反了）。

- [ ] **步骤 7：提交**

```bash
git add -- mobile/src/core/power/lowPower.ts mobile/src/core/power/usePowerFacts.ts mobile/test/lowPower.test.ts && git commit -m "feat(mobile): UX v2 B5-7 低电量材质回落——expo-battery 进依赖；lowPower 判据（省电 ∨ <20%）+ usePowerFacts 事实收集（原生缺席按 null 降级）；blurTarget 第三个回落条件；取证屏 power 行" -- mobile/package.json mobile/package-lock.json mobile/src/core/power/lowPower.ts mobile/src/core/power/usePowerFacts.ts mobile/test/lowPower.test.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/app/native-spike.tsx && git show --stat HEAD
```

---

### Task 8: 静态 App Shortcuts + `xiaozhou://voice` 深链——自写 config plugin + 落点路由 + ChatScreen 升层不开麦（方案 §9「App Shortcuts」、§12.2 红线；§0 第 2 条 #6e，泓舟可整任务否决）

**Files:**
- 新建 `mobile/plugins/with-shortcuts.js`、`mobile/src/app/voice.tsx`
- 修改 `mobile/app.config.ts:50`（plugins 数组）、`mobile/src/app/_layout.tsx`（`Stack.Screen name="voice"`）、`mobile/src/features/chat/ChatScreen.tsx`（消费 `voice=1`）

**为什么**：B3′ 之后，HyperOS 上唯一确定可达的出 App 入口就是长按图标（AOSP 静态 shortcuts，MIUI 桌面支持）。方案 §9 把它的前提写成「config plugin 需 prebuild」——正好搭重建趟。**不引 `expo-quick-actions`**：两条静态入口不需要运行时 API，少一个 autolinking 缝候选（B3 自写 foldstate 同一判据）；静态 shortcuts 就是 `res/xml/shortcuts.xml` + MainActivity 的 `android.app.shortcuts` meta-data 两件事，都住在 prebuild 重生成的 `android/` 里 ⇒ 必须是 plugin（`with-amap-key.js` 同一判据）。标签**必须**是 `@string` 资源引用（aapt 拒字面量），所以同时写 `strings.xml`。深链落点按 §12.2：只到对话页并升起语音层，**不开麦**——进入后仍需一次轻点 / 长按；锁屏下 launcher 本来就到不了 shortcuts（不需要我们判 keyguard）。「今日提醒」无独立路由，不做。

- [ ] **步骤 1：plugin** `mobile/plugins/with-shortcuts.js`

```js
// 静态 App Shortcuts（方案 §9「App Shortcuts」，B5-8）：长按图标 →「说话」「车况」。
// 静态 shortcuts = res/xml/shortcuts.xml + <meta-data android:name="android.app.shortcuts">，都住在
// prebuild 重生成的 android/ 里 ⇒ 必须做成 config plugin（同 with-amap-key 的判据）。
// 不引 expo-quick-actions：两条静态入口不需要运行时 API，少一个 autolinking 缝候选（B3 foldstate 同一判据）。
// 标签必须是 @string 资源引用（aapt 拒字面量），所以同时写 strings.xml。
// 「说话」落 xiaozhou://voice：只回对话页并升层、不开麦（§12.2）；「车况」落既有的 /vehicle 路由。
const fs = require('fs')
const path = require('path')
const { AndroidConfig, withAndroidManifest, withDangerousMod, withStringsXml } = require('expo/config-plugins')

const SHORTCUTS = [
  { id: 'voice', short: '说话', long: '和小舟说话', data: 'xiaozhou://voice' },
  { id: 'vehicle', short: '车况', long: '查看车况', data: 'xiaozhou://vehicle' },
]

function shortcutsXml(pkg) {
  const items = SHORTCUTS.map(
    (s) =>
      `  <shortcut android:shortcutId="${s.id}" android:enabled="true" android:icon="@mipmap/ic_launcher"\n` +
      `    android:shortcutShortLabel="@string/shortcut_${s.id}_short" android:shortcutLongLabel="@string/shortcut_${s.id}_long">\n` +
      `    <intent android:action="android.intent.action.VIEW" android:data="${s.data}"\n` +
      `      android:targetPackage="${pkg}" android:targetClass="${pkg}.MainActivity" />\n` +
      `  </shortcut>`,
  )
  return (
    '<?xml version="1.0" encoding="utf-8"?>\n' +
    '<shortcuts xmlns:android="http://schemas.android.com/apk/res/android">\n' +
    items.join('\n') +
    '\n</shortcuts>\n'
  )
}

module.exports = function withShortcuts(config) {
  config = withStringsXml(config, (c) => {
    const strings = SHORTCUTS.flatMap((s) => [
      { $: { name: `shortcut_${s.id}_short` }, _: s.short },
      { $: { name: `shortcut_${s.id}_long` }, _: s.long },
    ])
    c.modResults = AndroidConfig.Strings.setStringItem(strings, c.modResults)
    return c
  })
  config = withDangerousMod(config, [
    'android',
    async (c) => {
      const dir = path.join(c.modRequest.platformProjectRoot, 'app', 'src', 'main', 'res', 'xml')
      fs.mkdirSync(dir, { recursive: true })
      fs.writeFileSync(path.join(dir, 'shortcuts.xml'), shortcutsXml(c.android.package), 'utf8')
      return c
    },
  ])
  return withAndroidManifest(config, (c) => {
    const app = c.modResults.manifest.application?.[0]
    const main = (app?.activity || []).find((a) => a.$?.['android:name'] === '.MainActivity')
    if (!main) throw new Error('with-shortcuts: AndroidManifest 里找不到 .MainActivity')
    main['meta-data'] = (main['meta-data'] || []).filter((m) => m?.$?.['android:name'] !== 'android.app.shortcuts')
    main['meta-data'].push({ $: { 'android:name': 'android.app.shortcuts', 'android:resource': '@xml/shortcuts' } })
    return c
  })
}
```

`app.config.ts:50` 的 `plugins` 数组加 `'./plugins/with-shortcuts',`（挨着 `./plugins/with-unified-drive-root`）。

- [ ] **步骤 2：落点与消费**

`src/app/voice.tsx`：

```tsx
// xiaozhou://voice 的落点（方案 §9「语音层可由 deeplink 直接升起」+ §12.2 红线；B5-8）：
// 只回对话页并升起语音层，**不开麦**——进入后仍需一次轻点 / 长按才录音。升层由 ChatScreen 消费 voice=1。
import { Redirect } from 'expo-router'

export default function VoiceDeeplink() {
  return <Redirect href={{ pathname: '/', params: { voice: '1' } }} />
}
```

`_layout.tsx` 的 `Stack` 里加 `<Stack.Screen name="voice" options={{ headerShown: false }} />`（既有 typed routes 会因 `.expo/types/router.d.ts` 更新而认识 `/voice`——tsc 0 是判据，B3 T6 同款）。

`ChatScreen.tsx`（`sheetOverride` 声明附近）：

```tsx
  // B5-8 深链 xiaozhou://voice（Shortcuts「说话」）：升层**不开麦**（§12.2：进入后仍需一次手势才录音）。
  // 一次性消费：同一次进入只升一次，用户收起后不再被参数顶回去。
  const { voice: voiceParam } = useLocalSearchParams<{ voice?: string }>()
  const voiceParamConsumed = useRef(false)
  useEffect(() => {
    if (voiceParam !== '1' || voiceParamConsumed.current) return
    voiceParamConsumed.current = true
    setSheetOverride({ turnId: latestTurnId, mode: 'open' })
  }, [voiceParam, latestTurnId])
```

（`useLocalSearchParams` 从 `expo-router` import；`useRef` 已在文件里。⚠ 无消息时 `latestTurnId` 与 `sheetOverride.turnId` 是否匹配到「open」——`derivePresence` 的 `voice.override` 判等那一段要现读，热载时先在欢迎态验一次，不匹配就把消费改成 `turnId: null` 那一支。）

- [ ] **步骤 3：tsc + 全量**（0 error；条数不变）。热载冒烟（旧 APK 也能验深链——路由是 JS）：`adb shell am start -a android.intent.action.VIEW -d "xiaozhou://voice" com.xiaozhou.companion` ⇒ 对话页 + 语音层升起（`voice-sheet` 在）+ **无采集**：`dumpsys audio` 全机 0 条 `active? true` riid、隐私栏无采集点、胶囊不是「在听…」；收起后再发一次同深链 ⇒ 再升一次（新一次进入）。Shortcuts 本身要等 T10 装机（T11 步骤 3）。

- [ ] **步骤 4：提交**

```bash
git add -- mobile/plugins/with-shortcuts.js mobile/src/app/voice.tsx && git commit -m "feat(mobile): UX v2 B5-8 静态 App Shortcuts（自写 config plugin：说话/车况）+ xiaozhou://voice 落点——只回对话页并升层不开麦（§12.2）" -- mobile/plugins/with-shortcuts.js mobile/app.config.ts mobile/src/app/voice.tsx mobile/src/app/_layout.tsx mobile/src/features/chat/ChatScreen.tsx && git show --stat HEAD
```

---
### Task 9: 对照构建（无 AEC）+ Xruns 对照读数——播报卡顿定因的那一半（§0 第 2 条 #2；B3 §6.3「本批撞出的新缺陷」）

**Files:**（零源码改动；`mobile/patches/react-native-audio-api+0.13.3.patch` 文件**不动**，只临时反打进 `node_modules`）

**为什么**：B3 定性到此为止：本应用麦流 `AudioIn_92E`（tid ∈ 本应用 pid）在「麦常开 + 播报」段 2.2 次/分 Xruns，「麦常开无播报」0.7 次/分，HAL 阻塞 71–104ms；**不能归因给 AEC**，因为没有同条件的无 AEC 对照包（`96a6830` 是那一趟第一次进包）。裁决把对照并进本趟当**第一次**构建：卸掉 patch → 构建 → 装 → 同条件取 Xruns → T10 打回 patch 构建主线 → 装 → **同一天同条件再取一次**，两行两锚成对。播报用文字轮 + 播报档「总是」触发，不需要泓舟说话；免唤醒开着让麦流存在。**对照包上不取任何语音质量读数**（它没有 AEC，唤醒率 / 回声都不可比），只取 Xruns。

- [ ] **步骤 0：前提**：T6–T8 已提交、`git status` 干净；设备主线包 `2026-09-02 16:45:36`；`D:/Android/builds/_b3-mainline-apk-backup/mainline-app-debug.apk`（289317943 B）仍在——它就是当前主线包，本趟不需要再备份旧包；`dumpsys audio` 的 `Devices:` 是 `speaker(2)`（不是蓝牙，B4 §6.3 那条惯犯）、`STREAM_MUSIC` 非 0。

- [ ] **步骤 1：卸 AEC 补丁（只在 node_modules，patch 文件不动）**

```powershell
cd mobile
npx patch-package --reverse
Select-String -Path node_modules\react-native-audio-api\android\src\main\cpp\audioapi\android\core\AndroidAudioRecorder.cpp -Pattern 'VoiceCommunication'   # 必须 0 命中
git status --short -- patches/   # 必须为空（文件没动）
```

- [ ] **步骤 2：对照构建（带 `-Clean`，理由见下）**

⚠ 这里有个陷阱：`build_mobile.ps1` 每次都 robocopy 镜像 `mobile/`（含 `node_modules`），而 prebuild 判定受管文件变化时会整目录重生成 `android/`——T8 改了 `app.config.ts`，prebuild 可能会重生成并把 shortcuts / battery 带进来。对照包要的是「除 AEC 外与主线包一致」，主线包是**带**新原生的那个 ⇒ 对照包也带上它们更接近「只差 AEC」。所以：**对照包也用 `-Clean`**，变量仍只有 AEC 一个（两个包都有 foldstate current / battery / shortcuts）。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1
powershell -ExecutionPolicy Bypass -File scripts\fetch_mobile_voice_assets.ps1
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Clean
```

Expected: BUILD SUCCESSFUL（B3 读数 12–13 分钟）；构建侧核证 **补丁不在**：镜像区 `D:/Android/builds/xiaozhou-mobile/node_modules/react-native-audio-api/.../AndroidAudioRecorder.cpp` grep `VoiceCommunication` 0 命中。APK 另存为 `D:/Android/builds/_b5-contrast-apk/contrast-app-debug.apk`（构建产物路径会被 T10 覆盖）。

- [ ] **步骤 3：装对照包 + 锚**

```powershell
& "$env:ANDROID_HOME\platform-tools\adb.exe" install -r D:\Android\builds\_b5-contrast-apk\contrast-app-debug.apk
& "$env:ANDROID_HOME\platform-tools\adb.exe" shell dumpsys package com.xiaozhou.companion | Select-String lastUpdateTime   # 对照包锚，记进 §6.2
& "$env:ANDROID_HOME\platform-tools\adb.exe" reverse tcp:8081 tcp:8081
```

App 内：`/voice-spike` 探针 `avail: vad=true kws=true usable=true`（老模块没丢）；`/native-spike` `native: available`（foldstate 在）。

- [ ] **步骤 4：Xruns 读数（B3 T10 同一口径）**

设置：免唤醒**开**（麦流常开）、播报档「**总是**」、角色手持、行车档关。每段前 `adb shell dumpsys media.audio_flinger` 取基线，找**本应用 pid** 下的 `AudioIn_*` 流（`tid` 属于 `pidof com.xiaozhou.companion`；`src:HOTWORD` 的 `com.miui.voicetrigger` 那条**不是我们的**）。

| 段 | 做法 | 时长 |
|---|---|---|
| A 麦常开 + 播报 | 每 40s 手输一句会出长回答的问题（「介绍一下广州的历史」类），TTS 全程播完 | 5 min |
| B 麦常开、无播报 | 静置，不发消息 | 5 min |

每段结束 `dumpsys media.audio_flinger` 读该流 `Xruns` 增量；`adb logcat -d | findstr "HAL write blocked"` 计数（`-v brief`，B4 §6.3 那条格式坑）。记成表：`包 | 段 | Xruns | 次/分 | HAL blocked 条`。同一段里 `Reanimated` 告警条数也记一列（B3 已证它与播报无关，作对照）。

- [ ] **步骤 5：打回补丁（为 T10 做准备）**

```powershell
cd mobile
npx patch-package
Select-String -Path node_modules\react-native-audio-api\android\src\main\cpp\audioapi\android\core\AndroidAudioRecorder.cpp -Pattern 'VoiceCommunication'   # 恰 1 命中（miniaudio.h 的同名符号不算，路径不同）
```

- [ ] **步骤 6：记录**（读数写 §6.2「对照包」段；本任务零代码提交）

---

### Task 10: 主线一趟 `-Clean` 重建 + 注册验证 + 装机重核 + 冒烟（B3 T8 同款；方案 §11.1「是」）

**Files:**（零源码改动；产物与读数）

**为什么**：T6（foldstate 原生）、T7（expo-battery）、T8（config plugin）都只有 prebuild 后才进包。**构建成功 ≠ 注册上了**（§9.43）：Expo 件看 `ExpoModulesPackageList.kt`（在 `node_modules/expo/android/build/generated/expo/src/main/java/expo/modules/`，B3 §6.2 核出的真路径，**不是** `ExpoModulesProvider`），RN 社区件看 `PackageList.java`，plugin 写的东西看 merged manifest 与 `res/xml`。B3 T11 步骤 0 的教训：构建输出到与上一个包同一路径，**先备份**。

- [ ] **步骤 0：备份当前构建产物路径上的对照包**（步骤 2 已另存；再核一次 `_b5-contrast-apk/` 里那份大小 = `adb shell dumpsys package` 里装着的 `versionName`/大小对得上）。旧主线备份 `_b3-mainline-apk-backup/` **不动、不删**（删是红线，T18 交裁）。

- [ ] **步骤 1：前置 + 构建**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1        # 退出码 0
powershell -ExecutionPolicy Bypass -File scripts\fetch_mobile_voice_assets.ps1  # 缺模型/原生件显式失败
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Clean
```

Expected: BUILD SUCCESSFUL（12–38 分钟）；APK 体积与 275.9MB 对比，涨幅 >20MB 停下来查（battery / shortcuts 都极小）。第 1 趟若因镜像仓库 HEAD/GET 不一致失败（B3 §6.2 Dimezis 那条），看 `scripts/gradle_cn_mirrors.init.gradle` 的 `excludeGroup` 是否要加坐标——**只排实测在镜像上坏了的 group**。APK 复制到 `D:/Android/builds/_b5-mainline-apk-backup/mainline-app-debug.apk`。

- [ ] **步骤 2：注册验证（按通道分流）**

```bash
P=D:/Android/builds/xiaozhou-mobile
grep -n "BatteryModule\|FoldStateModule\|KwsModule\|BlurModule\|HapticsModule" "$P/node_modules/expo/android/build/generated/expo/src/main/java/expo/modules/ExpoModulesPackageList.kt"   # 五件全在
grep -n "RNGestureHandlerPackage\|OnnxruntimePackage\|ReanimatedPackage" "$P/android/app/build/generated/autolinking/src/main/java/com/facebook/react/PackageList.java"   # 既有三件不丢
grep -rn "android.app.shortcuts" "$P/android/app/build/intermediates/merged_manifests/debug/"   # meta-data 在 MainActivity 上
cat "$P/android/app/src/main/res/xml/shortcuts.xml"       # 两条 shortcut、@string 引用
grep -n "shortcut_voice_short\|shortcut_vehicle_short" "$P/android/app/src/main/res/values/strings.xml"
grep -n "VoiceCommunication" "$P/node_modules/react-native-audio-api/android/src/main/cpp/audioapi/android/core/AndroidAudioRecorder.cpp"   # AEC 补丁在（T9 步骤 5 打回的）
grep -c "fun current\|Function(\"current\")" "$P/modules/foldstate/android/src/main/java/com/xiaozhou/foldstate/FoldStateModule.kt"   # 1
```

任何一件缺席 ⇒ **先修注册再装机**。

- [ ] **步骤 3：装机 + 全套重核**

```powershell
& "$env:ANDROID_HOME\platform-tools\adb.exe" install -r D:\Android\builds\_b5-mainline-apk-backup\mainline-app-debug.apk
& "$env:ANDROID_HOME\platform-tools\adb.exe" shell dumpsys package com.xiaozhou.companion | Select-String 'lastUpdateTime|versionName'   # 新主线包锚——第 3 批起所有读数用它
& "$env:ANDROID_HOME\platform-tools\adb.exe" reverse tcp:8081 tcp:8081; & "$env:ANDROID_HOME\platform-tools\adb.exe" reverse --list
curl.exe -s http://localhost:8081/status
& "$env:ANDROID_HOME\platform-tools\adb.exe" shell pm list packages | Select-String maestro   # driver 还在
& "$env:ANDROID_HOME\platform-tools\adb.exe" shell dumpsys shortcut | Select-String -Pattern 'xiaozhou' -Context 0,4   # 两条静态 shortcut 已登记
```

- [ ] **步骤 4：冒烟（老能力一件不许丢）**：App 起、一轮文字问答；`/native-spike`：`native: available`、`current` 行有值（平放 = flat 的投影，**不再是「—」**）、`power` 行 `native=true level=<0–1> saver=false`；`/voice-spike` `avail: vad=true kws=true usable=true`；`/blur-spike` 四块无崩；触感四按钮有振感；Maestro 09 rc=0（Python 直读退出码）。

- [ ] **步骤 5：Xruns 主线复取**——**同一天、同设置、同段法**再跑一遍 T9 步骤 4 的 A / B 两段，填进同一张表（主线包锚另记）。

- [ ] **步骤 6：记录提交**（读数写 §6.2；本任务预期零源码提交，步骤 2 逼出的修复各自归位）

```bash
git commit -m "docs(mobile): UX v2 B5 §6.2 重建趟读数——对照包/主线包两锚、注册验证六件、Xruns 2×2 表、冒烟" -- docs/design/2026-09-04-mobile-ux-v2-b5-implementation-plan.md && git show --stat HEAD
```

---

### Task 11: 第 2 批真机验收——foldstate 新挂载实例 / 省电回落 / Shortcuts 与深链红线 / 触感与 blur 回归 / 播报卡顿定因（方案 §7.3、§5.11、§9、§12.2）

**Files:**（取证；`mobile/e2e/artifacts/b5-11-*.png`）

**为什么**：三件原生各要一个「装了之后才验得到」的读数，且每条配阴性对照；Xruns 表两行齐了才能对「播报卡顿是不是 AEC」下结论——**结论只许两种写法**：「对照包同条件 Xruns 相当 ⇒ 不是 AEC」或「主线包明显更高 ⇒ AEC 参与」，都要带两锚与次/分。

- [ ] **步骤 1：foldstate 新挂载实例（需泓舟手折）**：机身**半开**（book 或 tabletop）且对话页已挂着 → 进 `/native-spike` ⇒ `posture` 行**立刻**是 `book`/`tabletop`（不是 flat）、`current` 行 = 同一投影、`events: 0`（**0 才对**：初值来自 `current()`，不是新事件）。**阴性**（B4 的读数，已记录）：旧包同一操作读 flat / events 0——引用 B4 §6.3 遗留③，不复取。顺带补 B4 §6.2 遗留② 的仪器对照：机身**摊平**时 `cmd device_state state 2` ⇒ `events` 涨不涨（涨 = 强制值能造铰链事件；不涨 = 只有物理半开才行）；做完 `state reset` 回读。

- [ ] **步骤 2：省电回落（需泓舟开系统省电模式）**：省电开 ⇒ `/native-spike` `power: saver=true lowPower=true`；对话页语音层升起 ⇒ **无 BlurView**（`uiautomator dump` 里没有 `BlurView` 类节点；`voice-sheet-shell` tint 走 `.58` 那支——`png_probe` 壳底幅度对照 B4 T8 读数：真模糊 8 / 无模糊 211 那一族）。关省电 ⇒ 回到真模糊（`blurTarget` 告警 0 条）。回读省电开关。

- [ ] **步骤 3：Shortcuts + 深链红线**：桌面长按图标 ⇒ 菜单里「说话」「车况」（泓舟截图 `b5-11-shortcuts.png`）；点「说话」⇒ 对话页 + 语音层升起 + **零采集**（`dumpsys audio` 0 条 `active? true`、隐私栏无点、胶囊非「在听…」）；点「车况」⇒ 车辆页。**锁屏态**：launcher 到不了 shortcuts，记「不适用」而不是 ✅。

- [ ] **步骤 4：回归**：触感四种各触发一次（B3 T9 映射，`shutter` 是 B4 换的 VIRTUAL_KEY）；`/blur-spike` ③ 真糊；KWS/VAD 探针；Maestro 06/07/08/09 rc=0。

- [ ] **步骤 5：播报卡顿定因（读 T9/T10 的 2×2 表）**：写结论（两种之一）+ 出账：若不是 AEC，下一个嫌疑按 B3 的读数是「TTS 播放与麦流同 AudioContext 的 HAL 写阻塞」，归 KWS A/B 批之后的语音批；若是 AEC，选项（`VoiceCommunication` 换 `VoicePerformance` / 关 NS 只留 AEC）另立，不在 B5 改。

- [ ] **步骤 6：设备与云栈还原表**（省电模式 / `device_state` / App 设置；**音量不在表里**）。记录提交同 T10 步骤 6 格式（§6.2）。

---
### Task 12: 语音层去底栏 + 顶缘把手带下拉收起（泓舟原话①；B4 §6.4「带过去的实测约束」三条）

**Files:**
- 修改 `mobile/src/features/chat/VoiceSheet.tsx:111-116`（Pan）、`:150`（外层 GestureDetector 去掉）、`:184`（把手 → 把手带）、`:298-323`（底栏删除）、props（`onInterrupt` 删）；`mobile/src/features/chat/ChatScreen.tsx:519`（不再传 `onInterrupt`）；`mobile/src/ui/layout/sheetHeight.ts`（chrome 重算）+ `mobile/test/sheetHeight.test.ts`

**为什么**：泓舟原话「语音层升起的时候，下面收起、打断按钮应该不需要，收起可以通过滑动语音层顶部来收起，打断可以和下面的打断合并」。三条实测约束逐条落：① **Pan 只认向下、且要限定在顶缘**——B4 实测向上拖走的是层内 ScrollView（`driving-card-title` −11 → +24dp，层高纹丝不动），而 ScrollView 滚到顶之后再向下拖会与整层 Pan 打架 ⇒ Pan 从整层挪到**把手带**（`GestureDetector` 只包那条带）；② **底栏是 §6「目标 ≥56dp」在层内的唯一演员**（`voice-sheet-collapse` 96×56 PASS / 48 FAIL 阴性）⇒ 把手带接替：`minHeight` = 目标（行车 56 / 泊车 48）、**testID 沿用 `voice-sheet-collapse`**（`target_probe` 与 B4 的读数表都不用改），`Pressable` 轻点也收起、TalkBack 双击也收起（`accessibilityRole="button"`）；③ **横屏底栏占 73dp**（底栏 17 + 键 56）⇒ 撤掉是缺陷 A 横屏半的第一个 lever（T15）。打断合并进 Composer（T13）——语义取 `cancelCurrentTurn`（停），「停播再听」仍归光球轻点（`ChatScreen.tsx:355`）。暗区点击收起、返回键收起（非常驻）两条路不动。

- [ ] **步骤 1：先改判据与测试（`sheetHeight.ts` 头注原话「改那边要同步改这里」）**

`sheetHeight.ts`：删 `HANDLE_DP` / `FOOTER_PAD_DP`；`drivingSheetMinDp` 的 chrome 改为 **把手带（= `scale(TARGET.driving,'target',fontScale)`）+ `SCROLL_PAD_DP`**；头注加一段「B5-12：底栏撤掉、把手带 = 目标高（它接替 `voice-sheet-collapse` 的演员身份），chrome 从 117 降到 88（行车）」。

`sheetHeight.test.ts` 的「最小高的构成：三档 × split × 两个字号档，逐项与内容清单对齐」把清单里的 `把手 12 + 底栏 17 + 键 56` 换成 `把手带 56（大字 62）`，其余项不动；「缺陷 A 症状一」那条标题改成「层高够一条 56dp 把手带 + padding」并按新构成改期望值（**先算再填**：0.4 档行车最小高 = 56 + 32 + 120 + 12 + 20 = **240**，大字档 = 62 + 32 + 120 + 12 + 23 = 249）。

跑 `npx jest test/sheetHeight.test.ts` ⇒ 改期望的那几条先红（实现还没改）。

- [ ] **步骤 2：实现判据** ⇒ 跑绿。反向验证：M1 chrome 里把手带写死 56 不过 `scale` ⇒ 恰红「大字档」那条（B4 M5 那次是零敏感，这次清单逐项等值了才红——**再验一次**）；M2 把 `SCROLL_PAD_DP` 删掉 ⇒ 全部构成用例红。

- [ ] **步骤 3：组件**

`VoiceSheet.tsx`：
- props 删 `onInterrupt`；
- `:150` 外层 `<GestureDetector gesture={pan}>` 去掉，`Animated.View testID="voice-sheet"` 直接挂；
- `:184` 把手那行替换成把手带：

```tsx
            {/* 顶缘把手带（B5-12，泓舟 B4 真机轮原话①）：底栏「收起 / 打断」撤掉——收起 = 从这条带向下拖
                （或轻点它 / 点暗区 / 返回键），打断 = Composer 的 ⬆/■ 合一键（T13）。Pan **只挂在这条带上**：
                B4 实测层内 ScrollView 向上拖能滚，滚到顶后向下拖会与整层 Pan 打架；限定在把手带就不打架。
                它接替 voice-sheet-collapse 的 §6「目标 ≥56dp」演员身份（testID 沿用，探针脚本不改）。 */}
            <GestureDetector gesture={pan}>
              <Pressable
                testID="voice-sheet-collapse"
                accessibilityRole="button"
                accessibilityLabel="收起语音层"
                accessibilityHint="向下拖或轻点收起"
                onPress={props.onCollapse}
                style={{ minHeight: targetBtn, alignItems: 'center', justifyContent: 'center' }}
              >
                <View style={{ width: 36, height: 4, borderRadius: 2, backgroundColor: p.fill2 }} />
              </Pressable>
            </GestureDetector>
```

- `:298-323` 底栏 `View`（含 `voice-sheet-collapse` 与 `voice-sheet-interrupt` 两个 `Pressable`）整段删除；`ScrollView` 之后直接 `</Glass>`。
- Pan 的注释（`:106-110`）改写：「只认向下（`activeOffsetY(10)`），且只挂把手带（B5-12）——横滑与层内滚动都够不到它」。

`ChatScreen.tsx:519` 删 `onInterrupt={interruptAndListen}`（`interruptAndListen` 仍被 `:355` 光球轻点用，不删函数）。

- [ ] **步骤 4：tsc + 全量**（≈513 → 条数按步骤 1 改期望后不变或 +1）。

- [ ] **步骤 5：Metro 热载读数（新主线包锚）**：层开 → `uiautomator dump`（减少动效强制开）⇒ **无** `voice-sheet-interrupt`、`voice-sheet-collapse` 存在且 `target_probe --min 56`（行车）PASS / 泊车 48 PASS；从把手带向下 `input swipe`（**单进程慢速**，B3 坑：分进程 `motionevent` 各带 downTime）⇒ 收起；从层内答案区向下拖 ⇒ **不收起**（ScrollView 滚或不动，层高不变——这是 Pan 挪位的判据）；轻点把手带 ⇒ 收起；点暗区 ⇒ 收起；行车档 B/C 常驻层：把手带下拖 ⇒ 收起、下一轮再升（B4 §5 第 15 条不变）。截 `b5-12-handle.png`。

- [ ] **步骤 6：提交**

```bash
git commit -m "feat(mobile): UX v2 B5-12 语音层去底栏——收起改为顶缘把手带下拉/轻点（Pan 只挂把手带，不与层内滚动打架；把手带接替 voice-sheet-collapse 的 ≥56dp 演员），打断并入 Composer；sheetHeight chrome 重算（117→88）" -- mobile/src/features/chat/VoiceSheet.tsx mobile/src/features/chat/ChatScreen.tsx mobile/src/ui/layout/sheetHeight.ts mobile/test/sheetHeight.test.ts && git show --stat HEAD
```

---

### Task 13: 发送键与打断合一——闲时 ⬆ 发、忙时 ■ 停（泓舟原话②；B2 出账④ 的第二次改设计）

**Files:**
- 修改 `mobile/src/features/chat/Composer.tsx:236-270`、`mobile/src/ui/icons.local.ts`

**为什么**：泓舟原话「输入框旁边的发送按钮可以和打断合并，并且发送按钮可以简化为一个 ⬆️ 箭头图标，目前市面上很多 AI 助手都是这么设计的」。合一键的语义：**忙时（在飞轮：pending / streaming / process）= ■ 停**（`onInterrupt` = `core.cancelCurrentTurn()`，与原 pill 同一回调），**闲时 = ⬆ 发**；忙时要发新话先停再发（市面惯例），回车键 `returnKeyType="send"` 不动。testID **`composer-send` 不变**——Maestro 01/02/03/06/08 都按它点，且都在闲时点；§6「目标 ≥56dp」的演员不变（行车 56 / 泊车 44 那组读数照旧）。**C 身份的顺带收益**：闲时仍 `disabled`（无字可发），但忙时可点 ⇒ B4 §6.3「一枚永远点不动的键」这条设计代价只剩一半。颜色沿用既有语义：发 = 极光渐变（虹彩三处之一），停 = 琥珀（`p.amber` / `p.amberSoft`，与原 pill 同色）。

- [ ] **步骤 1：图标**

```powershell
Select-String -Path mobile\src -Pattern 'name="send"' -Recurse   # 只应命中 Composer.tsx 一处；多于一处就保留 send 条目
```

`icons.local.ts` 的 `send` 条目替换为两条（格式同 `keyboard`：24 盒、1.8 stroke，`Icon.tsx` 替 currentColor）：

```ts
  /** 发送（B5-13：⬆ 箭头，市面 AI 助手通行做法；替掉 B4-9 的纸飞机） */
  arrowUp: { w: 24, h: 24, body: '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>' },
  /** 打断 / 停（B5-13：与发送合一键，忙时显示） */
  stop: { w: 24, h: 24, body: '<rect x="6" y="6" width="12" height="12" rx="2"/>' },
```

头注的「send：线性纸飞机」那句改成「arrowUp / stop：发送与打断合一键的两态（B5-13）」。

- [ ] **步骤 2：Composer**

`:236-243` 的 `{busy ? (<Pressable onPress={onInterrupt} …>■ 打断</Pressable>) : null}` **整段删除**；`:244-270` 的发送键替换：

```tsx
        {/* 发送 / 打断合一（B5-13，泓舟 B4 真机轮原话②）：忙时 ■ 停（onInterrupt = cancelCurrentTurn，与原 pill 同一回调），
            闲时 ⬆ 发。testID 仍 composer-send（Maestro 01/02/03/06/08 都在闲时按它点）；§6「目标 ≥56dp」的演员不变。
            C 身份行车档：闲时仍 disabled（无字可发），**忙时可点**——B4 §6.3「一枚永远点不动的键」从此只剩一半。
            svg 原生缺席仍回退文字（iconRuntimeAvailable 是既有判据，坑账 §9.27） */}
        <Pressable
          testID="composer-send"
          accessibilityRole="button"
          accessibilityLabel={busy ? '打断' : '发送'}
          disabled={!busy && inputMode === 'hidden'}
          onPress={busy ? onInterrupt : submit}
          style={{
            experimental_backgroundImage: busy ? undefined : AURORA.gradient,
            backgroundColor: busy ? p.amberSoft : undefined,
            borderWidth: busy ? 1 : 0,
            borderColor: busy ? 'rgba(245,158,11,0.3)' : 'transparent',
            opacity: !busy && inputMode === 'hidden' ? 0.45 : 1,
            width: driving ? target : scale(44, 'target', fontScale),
            height: driving ? target : scale(44, 'target', fontScale),
            borderRadius: RADIUS.full,
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: busy ? undefined : '0 4px 22px rgba(91,140,255,0.45)',
          }}
        >
          {iconRuntimeAvailable() ? (
            <Icon name={busy ? 'stop' : 'arrowUp'} size={22} color={busy ? p.amber : '#fff'} />
          ) : (
            <Text style={{ color: busy ? p.amber : '#fff', fontSize: p.font(15), fontWeight: '600' }}>{busy ? '停' : '发'}</Text>
          )}
        </Pressable>
```

- [ ] **步骤 3：tsc + 全量**（0；条数不变——组件层）。

- [ ] **步骤 4：Metro 热载读数**：闲时截 `b5-13-idle.png`（⬆ 极光）；发一句慢轮（「介绍一下广州的历史」）→ 在飞时 Maestro `extendedWaitUntil: visible: presence-capsule` 后 `takeScreenshot`（**不要 adb 连拍**）⇒ ■ 琥珀、`uiautomator dump` 里 `composer-send` 的 `content-desc` = 「打断」、**无**旧 pill；点它 ⇒ 气泡定格「已打断」（B2 规则 4，不改红）、键回 ⬆；C 身份行车档：闲时 `enabled=false`，忙时 `enabled=true`（dump 读 `enabled` 属性）；`target_probe` 行车 56 / 泊车 44 阴性照旧。Maestro 01 / 02 / 03 / 06 / 08 rc=0（它们都点 `composer-send`）。

- [ ] **步骤 5：提交**

```bash
git commit -m "feat(mobile): UX v2 B5-13 发送与打断合一键——闲时 ⬆ 发（极光）、忙时 ■ 停（琥珀，cancelCurrentTurn）；testID composer-send 不变；C 身份忙时可点；icons.local send → arrowUp/stop" -- mobile/src/features/chat/Composer.tsx mobile/src/ui/icons.local.ts && git show --stat HEAD
```

---
### Task 14: Scanner 三条——顶栏钮与 `health-dot` 触控目标 / 副文本对比度（WCAG 判据进 jest）/ chip 与气泡的重复说明（B4 §6.4 步骤 13 出账；方案 §8「对比与字号」「TalkBack」）

**Files:**
- 新建 `mobile/test/theme.test.ts`
- 修改 `mobile/src/ui/theme.ts:61-62` / `:84-85`、`mobile/src/features/chat/ChatScreen.tsx:124-140`（`TopLink`）与 `:654-664`（`health-dot`）、`mobile/src/features/chat/Composer.tsx:135-144`（chips）

**为什么**：B4 用 Scanner 扫出两态都在的 9 条里，三类是 `target_probe` 完全覆盖不到的维度：① 顶栏「车辆 / 设置」40×40 与 `health-dot` 40 高——**两档都不达 48**（行车档没管顶栏）；② 灰色副文本（「已打断」「已执行 · 展开回执」那类 `fg3`）文本对比度；③ 「多个项目具有相同的说明」：用户气泡「打开空调26度」与同文案的 chip 都以文本作说明，读屏念两遍。①③ 是一处一改；② **要有可量的判据**——把 WCAG 相对亮度 / 对比度公式写进 jest（alpha 色先按 `bg` 合成再算），钉「小字（<18pt）≥4.5:1」，数值由测试反推：按 sRGB 公式手算，深色 `fg3` .34 ≈ 3.0:1（不达）→ **.48 ≈ 5.0:1**；浅色 `fg3` .32 ≈ 2.1:1 → **.60 ≈ 4.9:1**；浅色 `fg2` .55 ≈ 4.1:1（也不达，顺手）→ **.62 ≈ 5.2:1**；深色 `fg2` .58 ≈ 6.9:1 已达。**以跑出来的数为准**，手算只是起点。

- [ ] **步骤 1：写失败测试** `mobile/test/theme.test.ts`

```ts
// 色板对比度判据（B5-14，B4 Scanner 出账②「灰色副文本对比度」）。WCAG 2.x：小字 ≥4.5:1；alpha 色先按 bg 合成。
import { DARK, LIGHT } from '@/ui/theme'

function channel(c: number): number {
  const s = c / 255
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
}
function parse(color: string): { r: number; g: number; b: number; a: number } {
  const hex = color.match(/^#([0-9a-f]{6})$/i)
  if (hex) {
    const n = parseInt(hex[1], 16)
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 }
  }
  const rgba = color.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i)
  if (!rgba) throw new Error('unsupported color: ' + color)
  return { r: +rgba[1], g: +rgba[2], b: +rgba[3], a: rgba[4] === undefined ? 1 : +rgba[4] }
}
function composite(fg: string, bg: string): { r: number; g: number; b: number } {
  const f = parse(fg)
  const b = parse(bg)
  return { r: f.a * f.r + (1 - f.a) * b.r, g: f.a * f.g + (1 - f.a) * b.g, b: f.a * f.b + (1 - f.a) * b.b }
}
function luminance(c: { r: number; g: number; b: number }): number {
  return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
}
export function contrast(fg: string, bg: string): number {
  const l1 = luminance(composite(fg, bg))
  const l2 = luminance(parse(bg))
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1]
  return (hi + 0.05) / (lo + 0.05)
}

describe('WCAG 小字对比度 ≥4.5:1（fg2 / fg3 压在 bg 上，深浅两主题）', () => {
  test.each([
    ['dark fg2', DARK.fg2, DARK.bg],
    ['dark fg3', DARK.fg3, DARK.bg],
    ['light fg2', LIGHT.fg2, LIGHT.bg],
    ['light fg3', LIGHT.fg3, LIGHT.bg],
  ])('%s', (_name, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
  test('公式自检：黑白 21:1、同色 1:1', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 0)
    expect(contrast('#06080F', '#06080F')).toBeCloseTo(1, 3)
  })
})
```

（`theme.ts` 若没有导出 `DARK` / `LIGHT` 两个 Palette 常量，把两份字面量提成具名导出——先 `grep -n "const .*Palette" mobile/src/ui/theme.ts` 看现有名字，照现有名字改 import，不另起名。）

- [ ] **步骤 2：跑红** ⇒ 预期 dark fg3 / light fg3 / light fg2 三条红（实红条数是本任务第一条读数——写进 §6.3）；自检两条绿。

- [ ] **步骤 3：实现（三处）**

`theme.ts`：深色 `fg3: 'rgba(255,255,255,0.48)'`；浅色 `fg2: 'rgba(10,14,26,0.62)'`、`fg3: 'rgba(10,14,26,0.60)'`——跑绿后**逐条记下实际对比度**（`console.log` 一次或在测试里打印，记完删掉）；若某条仍红，按 0.02 步进抬到过线为止，数值以测试为准。

`ChatScreen.tsx` `TopLink`（`:124-140`）：`width: 40, height: 40` → `width: scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale), height: 同`（`TopLink` 要多收 `driving` / `fontScale` 两个 prop，调用处传 `snapshot.driving` / `settings.fontScale`；`Icon size` 不变）；`health-dot`（`:654-664`）：`minWidth: 40, height: 40` → 同一表达式。

`Composer.tsx` chips（`:135-144`）的 `Pressable` 加 `accessibilityRole="button"` 与 `accessibilityLabel={\`快捷指令：${c}\`}`——与用户气泡（纯文本说明「打开空调26度」）从此说明不同。

- [ ] **步骤 4：跑绿 + tsc + 全量**（≈513 → ≈518）。反向验证：M1 `fg3` 深色改回 .34 ⇒ 恰红 dark fg3；M2 `composite` 忽略 alpha（当 a=1）⇒ 四条全绿——**这是「alpha 先合成」那半的反证：不合成会把不达标的色判成达标**，写进 §6.3。

- [ ] **步骤 5：真机读数（需泓舟：Scanner 已装、已启用）**：Scanner 概况扫对话页泊车 / 行车各一次（结果页约 10s 后才成前台，B4 §6.4 那条）⇒ 与 B4 的 16 / 11 对比，**顶栏两钮 + `health-dot` + 两处副文本 + 两处重复说明这 7 条应消失**，剩下的逐条列（`receipt-toggle` / `composer-input` 是既知未管项）；`uiautomator dump` 核顶栏两钮 bounds ≥ 48dp（行车 56）；TalkBack 读 chip 念「快捷指令：打开空调26度」。截 `b5-14-scanner-{parked,driving}.png`。

- [ ] **步骤 6：提交**

```bash
git add -- mobile/test/theme.test.ts && git commit -m "feat(mobile): UX v2 B5-14 Scanner 三条——顶栏钮/health-dot 目标 48（行车 56）；fg3/浅色 fg2 抬到 ≥4.5:1（WCAG 对比度判据进 jest，alpha 先合成）；chips 说明加「快捷指令：」前缀去重复说明" -- mobile/test/theme.test.ts mobile/src/ui/theme.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/Composer.tsx && git show --stat HEAD
```

---

### Task 15: 缺陷 A 横屏半——先量后改：底栏已撤（T12）→ driving-landscape 隐藏 chips 且层覆盖整列 → `sheetOrbDp` 球降级兜底（对账一 #4、B4 §6.4 缺陷 A ③）

**Files:**
- 修改 `mobile/src/ui/layout/sheetHeight.ts`（`drivingSheetMinDp` 加 `orb` 参数、新增 `sheetOrbDp`）、`mobile/test/sheetHeight.test.ts`（+3）、`mobile/src/features/chat/ChatScreen.tsx`（driving-landscape：chips 隐藏、语音层容器改整列、`onOrbTap`）、`mobile/src/features/chat/VoiceSheet.tsx`（split 时大球接 `onOrbTap`；球径读 `sheetOrbDp`）、`mobile/src/features/chat/Composer.tsx`（`hideChips` prop）

**为什么**：B4 修好了缺陷 A 的「装不下 56dp 键」那半，修不掉「120dp 球与层内文字」那半：外屏横记录区**实测 98.67dp**（不是推的 192），0.4 档内容需求 269dp；成因是横屏下 App 自己的 chrome 吃掉 360dp 里的 261（顶栏 104 + chips/输入区 157）。B4 结论「要让层脱离记录区容器，或极矮容器下把球降级」。本任务按 lever 顺序做，**每一步先量再决定要不要下一步**（B4 §6.4 坑：估算要按 tokens 逐项算，别口算）：① T12 撤底栏 ⇒ 最小高 269 → **240**（仍 > 98.67）；② driving-landscape 隐藏 chips（§6「chips ≤3」不是「必须显示」；语音能到达一切）+ 语音层容器从记录区改成**整列（记录区 + Composer）**——B/C 身份常驻层里 120dp 大球接 `onTap`（同 Composer 光球的轻点即说，§5.1.1「轻点始终能说」），被盖住的 Composer 光球不再是唯一麦；③ 都做完仍装不下（大字档、或更矮的窗）⇒ `sheetOrbDp` 判据把球降到泊车的 88——**判据兜底，不是主修法**。

- [ ] **步骤 1：先量（新主线包锚；需泓舟横屏 + 角色 C + 行车档）**：`driving-landscape` 下 `uiautomator dump` ⇒ 记录区高、Composer 高、顶栏高、`voice-sheet` 高、`voice-sheet-transcript` / `voice-sheet-answer` 的 bounds（B4 读数 −6.0dp 是基线）。**T12 之后**再量一次（底栏撤掉后的层高与文字区）。两组数写进 §6.3 后才决定 lever ②③ 的期望值。

- [ ] **步骤 2：写失败测试**（`sheetHeight.test.ts`）

```ts
describe('B5-15 缺陷 A 横屏半：球降级判据只在「全部 lever 之后仍装不下」时起作用', () => {
  test('容器装得下 0.4 档最小高 ⇒ 120', () => {
    expect(sheetOrbDp({ containerH: 578, driving: true, split: false, fontScale: 'normal' })).toBe(SHEET_ORB.driving)
  })
  test('外屏横实测记录区 98.67 ⇒ 装不下 ⇒ 88；泊车永远 88', () => {
    expect(sheetOrbDp({ containerH: 98.67, driving: true, split: true, fontScale: 'normal' })).toBe(SHEET_ORB.parked)
    expect(sheetOrbDp({ containerH: 98.67, driving: false, split: true, fontScale: 'normal' })).toBe(SHEET_ORB.parked)
  })
  test('层覆盖整列后（记录区 + Composer，按步骤 1 实测填）⇒ 120 且最小高 ≤ 容器', () => {
    const columnH = 98.67 + 157 // ⚠ 157 是 B4 读的 chips+输入区高，步骤 1 实测后改成真值
    expect(sheetOrbDp({ containerH: columnH, driving: true, split: true, fontScale: 'normal' })).toBe(SHEET_ORB.driving)
    expect(drivingSheetMinDp(0.4, true, 'normal', SHEET_ORB.driving)).toBeLessThanOrEqual(columnH)
  })
})
```

- [ ] **步骤 3：判据**

`sheetHeight.ts`：`drivingSheetMinDp(detent, split, fontScale, orb: number = SHEET_ORB.driving)`（球列用参数）；新增

```ts
/** 层内大球直径（dp）：行车 120，但容器连 0.4 档最小高都装不下时降到泊车的 88（缺陷 A 横屏半的最后一道保险）。
 *  在它之前的 lever：底栏撤掉（B5-12）、driving-landscape 隐藏 chips 且层覆盖整列（B5-15）——都做完仍装不下才降球。 */
export function sheetOrbDp(i: { containerH: number; driving: boolean; split: boolean; fontScale: FontScalePref }): number {
  if (!i.driving) return SHEET_ORB.parked
  return drivingSheetMinDp(0.4, i.split, i.fontScale, SHEET_ORB.driving) <= i.containerH ? SHEET_ORB.driving : SHEET_ORB.parked
}
```

`sheetHeightDp` 里行车分支先 `const orb = sheetOrbDp(i)` 再 `drivingSheetMinDp(i.detent, i.split, i.fontScale, orb)`——球降了最小高也跟着降，clamp 逻辑不变。跑绿；反向验证：M1 `sheetOrbDp` 恒 120 ⇒ 恰红「98.67 ⇒ 88」；M2 `sheetHeightDp` 不把 `orb` 传下去 ⇒ 红「整列」那条的 `≤ 容器` 半（若步骤 1 实测数让它本来就装得下则零红——**零红是关于 fixture 的读数**，换成大字档再验）。

- [ ] **步骤 4：布局（lever ②）**

`ChatScreen.tsx`：`chatColumn` 里当 `layout.mode === 'driving-landscape'` 时：Composer 传 `hideChips`；`VoiceSheet` 挪到**整列容器**（记录区 View 与 Composer 的共同父级）里以 absolute 盖住两者，`containerHeight` 改用整列 `onLayout` 的高（新 `columnHeight` state）；传 `onOrbTap={onOrbTap}`（ChatScreen 既有的光球轻点回调——`grep -n "onTap=" ChatScreen.tsx` 取真名）。非 driving-landscape 一切照旧（**逐字节不变**——这是 B4 T11 split 分支的纪律）。
`VoiceSheet.tsx`：球径 `SHEET_ORB.driving` 字面量处改读 `sheetOrbDp({...})`；`split && props.onOrbTap` 时大球包 `Pressable`（`accessibilityLabel` 同 Composer 光球「…，开始说话」；行车条款：只轻点、无长按上滑）。
`Composer.tsx`：`hideChips?: boolean`，为 true 时不渲 chips 行。

- [ ] **步骤 5：tsc + 全量**（≈518 → ≈521）。

- [ ] **步骤 6：真机读数（需泓舟横屏）**：driving-landscape 下 `voice-sheet-transcript` / `voice-sheet-answer` **y2 > y1**（不再负高）、球径（bounds 边长 / 3）= 120 或 88 并记下**为什么**（`sheetOrbDp` 的输入值）、`voice-sheet-collapse` ≥56 PASS、chips 缺席、轻点层内大球 ⇒ 进 listening（隐私栏出采集点）。**阴性**：竖屏同角色 ⇒ 层仍在记录区容器内、chips 在、球 120（`/native-spike` 的 `layout` 行 `single`）。截 `b5-15-landscape.png`。§6「一屏一卡」在 0.78 档：`driving-card-title` 初始可见与否记实（B4 内屏是 −11 → 滚一次可见）。

- [ ] **步骤 7：提交**

```bash
git commit -m "feat(mobile): UX v2 B5-15 缺陷 A 横屏半——driving-landscape 隐藏 chips、语音层覆盖整列（记录区+Composer）且层内大球可轻点即说；sheetOrbDp 球降级判据兜底（drivingSheetMinDp 带 orb 参数）" -- mobile/src/ui/layout/sheetHeight.ts mobile/test/sheetHeight.test.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/VoiceSheet.tsx mobile/src/features/chat/Composer.tsx && git show --stat HEAD
```

---

### Task 16: 第 3 批真机轮——新演员读数 / Scanner 复扫 / Maestro 回归 / 表#6 / Reanimated 定位 / 系统动画缩放（可选）/ 缺陷 A 横屏读数

**Files:**（取证；撞上缺陷就修并记 §6.3）

**为什么**：T12–T15 的组件改动全无 jest 面，判据是真机读数，每条带阴性。三条非闸门项（表#6、Reanimated、系统开关）放在最后、各有时限，做不完记 ⬜ 带场景。

- [ ] **步骤 0：前提**：新主线包锚（T10）、`dev_stack target show` = cloud、`adb reverse` 非空、Tools 按钮关、Tailscale 在线（B4 §6.4：手机 Tailscale 掉线 3h 表现成功能红）、设置基线一行。
- [ ] **步骤 1：目标 ≥56dp 新演员**：行车档开（角色 C）+ 层开 + 一轮在飞 ⇒ `target_probe --min 56 composer-orb composer-send voice-sheet-collapse dock-accept dock-cancel followup-chip` 全 PASS；**阴性**行车档关 ⇒ `composer-send` 44 FAIL、`voice-sheet-collapse` 48（泊车目标）。
- [ ] **步骤 2：Scanner 复扫**（T14 步骤 5，需泓舟）。
- [ ] **步骤 3：TalkBack**（需泓舟开）：把手带念「收起语音层」双击收起；合一键闲时念「发送」忙时念「打断」；chip 念「快捷指令：…」。
- [ ] **步骤 4：Maestro 回归**：01 / 02 / 03 / 06 / 08 / 09 各 rc=0（Python 直读；07 需 `device_state 3` 外部先设）。
- [ ] **步骤 5：缺陷 A 横屏**（T15 步骤 6）。
- [ ] **步骤 6：把手带手势正反**（T12 步骤 5，在新包上复取一次）。
- [ ] **步骤 7：B2 表#6「气泡先于相机」**（B4 T13 步骤 11① 原文）：相机权限已授 → `screenrecord /sdcard/b5-16-vision.mp4` + 说「这是什么」→ `ffmpeg -vf fps=30` 重采样 → 用户气泡出现帧号 < `logcat -s CameraService` 的 `connect` 时刻对应帧号。需泓舟说话或 Maestro 输入。
- [ ] **步骤 8：Reanimated tag → view（90 分钟预算，非闸门）**：宿主侧 `adb logcat -v brief` 常驻；复现场景是 B4 §6.4 那个（Maestro 反复 force-stop / 切屏 / 导航）；拿到 `synchronouslyUpdateUIProps failed for tag N` 后立刻 `adb shell dumpsys activity top` / `uiautomator dump` 找不到 tag ⇒ 改用 React DevTools（Metro `j`）的 Components 树按 `tag` 找；钉到组件名就出账带名字，钉不到按时到点出账带「已排除的组件」。
- [ ] **步骤 9：系统动画缩放那一路（可选，需泓舟授权 `settings put global animator_duration_scale 0`）**：光球两帧 diff 0.00%；做完 `settings put global animator_duration_scale 1` 并回读。
- [ ] **步骤 10：`e2e/README.md`**：把手带正反两条手势的取证写法（`input swipe` 单进程慢速）写进「B5」一节；临时流仍不入库。
- [ ] **步骤 11：还原表 + 记录提交**（§6.3）。

---
### Task 17: 五人外部小样本——五态版（§11.4「状态可读性」；§0 第 2 条 #3；泓舟批准才做）

**Files:**（材料 `mobile/e2e/artifacts/b5-sample/s1..s7.png`，gitignore；协议与计分表沿用 B4 T14 那份）

**为什么**：§11.4「状态可读性」至今**没有外部分布读数**（B2 闸是泓舟自评 6/6，1 人非外部；B3/B4 原样转出；B4 T14 材料 8 张拿到 6 张，缺 armed / speaking）。裁决走五态版：speaking 用 Maestro 同步截（不依赖 KWS），armed 缺、明写，KWS A/B 批产出后追加单图追问。**材料要在第 3 批之后截**——界面变了（底栏没了、发送键换了），旧图作废。

- [ ] **步骤 1：材料（新主线包、第 3 批之后、深色主题、外屏竖）**：idle / listening（同一条 `adb shell` 里 DOWN → sleep → screencap → UP）/ thinking（Maestro `extendedWaitUntil: visible: presence-capsule`）/ attention（`extendedWaitUntil: visible: dock-confirm`）/ **speaking**（播报档「总是」+ 长回答；Maestro `extendedWaitUntil: visible: { text: "播报中 · 说话可打断" }` 后 `takeScreenshot`——胶囊文案是 `presence.ts:260` 的字面量，改了这里要改）/ 行车 C 常驻层 / 泊车同一轮。**集齐再编号 `s1..s7` 并打乱**（提前编号会藏住缺口，B4 T14 那条）。armed：**不补**，记「缺 armed（KWS 链路，§0 #4）」。
- [ ] **步骤 2：协议与计分**：照 B4 T14 表——每人独立、逐张问「它现在在干嘛？」自由作答、记原话；状态五张 x/5、五人分布 `[x1..x5]`，**均值 ≥4/5 算达标（分母从 6 改 5，明标）**；行车两张：几人选对 + 理由分类。错答原话逐条列（B5 之后视觉批的输入）。
- [ ] **步骤 3：记录**：「人 × 图」表 + 错答原话 + 达标判定。**不做就写「泓舟裁定不做，§11.4 状态可读性仍无外部基线」——不许写「裁定算过」。**

---

### Task 18: 记录收口

- [ ] `docs/design/README.md` 本计划行状态改成收口读数（四批 ✅ / ⬜ 各几格、缺陷 C 云上读数、Xruns 结论、新包锚）；
- [ ] 主计划 `2026-08-24-mobile-app-implementation-plan.md` §9 坑账追加（从 **75** 起，只记本批新踩的）；§2 第 145 行已在 T2 改；
- [ ] `mobile/e2e/README.md`「B5」一节（把手带手势取证、`composer-send` 两态、深链 `xiaozhou://voice` 冒烟法）；
- [ ] `AGENTS.md` §4.2「Android App」行**只改指针**（B5 四批已收口 → 仍开项 → 下一步 KWS 阈值 A/B 批另拆），**单独一个 commit**，动前 `git diff --stat -- AGENTS.md`；**不写批次长读数**（B4 §6.4 合并冲突那条自我更正：别人的收敛动作先当规则读）；
- [ ] 交泓舟裁的清单：`_b3-mainline-apk-backup/` 与 `_b5-contrast-apk/` 删不删（红线）；本地 / 远端分叉；T17 做没做；
- [ ] **未推送清单只列构成、推前当场重列** `origin/main..HEAD` 逐条念归属（B4 §6.3：一天变四次、且别人推 main 会带走同分支上的提交）。

```bash
# AGENTS.md 单独一个 commit（所有会话都在写的文件）：
git diff --stat -- AGENTS.md && git commit -m "docs(agents): Android 行指向 B5 收口——仍开项与下一步（KWS 阈值 A/B 批另拆）" -- AGENTS.md && git show --stat HEAD
```

---

## 3. 任务依赖与并行度

```
第 1 批：T1 proto+Edge ─► T2 网关+契约         （T2 的 Go 测要 T1 生成的 gen/go）
         T3 客户端判据 ─► T4 设置页出口          （T3 ∥ T1/T2：客户端只认布尔，不等后端）
         T1..T4 ─► T5 deploy（两道闸）+ 读数
第 2 批：T6 foldstate ∥ T7 battery ∥ T8 shortcuts   （T7 动 package.json、T8 动 app.config.ts——串行提交）
         T6..T8 ─► T9 对照构建（-Clean，卸补丁）─► T10 主线构建（打回补丁）─► T11 真机验收
第 3 批：T12 去底栏 ─► T15 缺陷 A 横屏半           （同动 VoiceSheet / sheetHeight）
         T13 合一键 ∥ T14 Scanner 三条             （T14 的 chips label 与 T13 同文件——串行提交）
         T12..T15 ─► T16 真机轮
第 4 批：T17 小样本（泓舟裁）─► T18 收口
```

- **第 1 批后端三处是本计划唯一的后端面**：T1 → T2 串行是因为 Go 测编译要新字段；T3 可以先做（判据只认布尔）。
- **T9 必须在 T10 之前**：对照包先装先取数，主线包后装——收尾时设备上留主线包，不用装第三次。
- **T12 是第 3 批的门**：底栏撤掉之后 `sheetHeight` 的 chrome 才是新值，T15 的「先量」要量撤完之后的。
- **T17 材料必须在第 3 批之后截**（界面变了）。
- **需泓舟在场的格子**：T11 步骤 1/2/3（手折 / 省电 / 长按图标）、T14 步骤 5 与 T16 步骤 2/3（Scanner / TalkBack）、T15 步骤 1/6（横屏行车档）、T16 步骤 7（说「这是什么」）、T17（五个外部人）。可以合并成两次真机会，读数按任务名记。

## 4. 「不负优化」判据在 B5 的取数点（方案 §11.4）

| 判据 | B5 取数 |
|---|---|
| 首反馈时延 | 不复取（本批没动唤醒 → listening 那条链；对照包上不取语音质量读数） |
| 状态可读性 | **T17**：第一份外部分布读数（五态版，明标分母 5；不做就写「仍无外部基线」） |
| 记录完整性 | 不复取（没动记录侧） |
| 承诺不丢 | Maestro 06 回归（T11 步骤 4 / T16 步骤 4）；合一键忙时 ■ 不误伤 Dock 两钮（T16 步骤 1 `dock-accept / dock-cancel` 仍 56） |
| 键盘遮挡 | Maestro 08 回归（T11 / T16） |
| 性能 | 对照包 / 主线包 Xruns 2×2（T9 / T10）；省电回落后语音层升起帧率不复取（真模糊关掉只会更好，B3 T9 A/B 本来就分不开） |
| 无障碍 | **T14 步骤 5 / T16 步骤 2**：Scanner 复扫两态，与 B4 的 16 / 11 逐条对；WCAG 对比度进 jest（第一次有可量判据） |
| 回归 | `npm test` 条数账：499 →(T3 +9)→ ≈508 →(T7 +5)→ ≈513 →(T12 ±0)→ ≈513 →(T14 +5)→ ≈518 →(T15 +3)→ **≈521，只增不减**；pytest edge +5、go test +2、`hmi/` 零改动（`git diff --stat -- hmi/` 每批收口核一次）；Maestro 01/02/03/06/07/08/09 rc=0 |

## 5. 实施判断（写在开工前，做的时候撞到再补）

1. **B3′ 角色不启用**（§0 第 2 条 #6b）。判据：一台真机上角色能拿到但零手势可达；启用换来的只有 manifest 面与平台授权。换机型 / ROM 再验时重开，届时先答 B3 §6.4 遗留①那个问题「拿到角色而拿不到手势，值不值得做」。
2. **缺陷 C 的后端标注盖在 `Handle` 出口，不是照抄两处云端调用再加一处**：出口是唯一漏斗，本地快路径 / 混合 / 降级兜底的 final 都不经那两处——缺陷 C 的形态正是这些简单轮。云端填的 `driving` 无权威（T1 有用例）。
3. **客户端 final 只认布尔**：旧网关的 final 无此键，`!!undefined` 会造出反向缺陷（行车中 30s 退出）。process 那一路恒带键，不动。
4. **「本次」= 本段**：`trueAt` 是段起点、段内 true 不刷新；`dismissedAt ≥ trueAt` 压住本段。代价：用户退出后车一直没停，这一段里 Edge 再怎么标 true 都不进——这是「退出只压本次」的字面含义；要重新进就手动开或等下一段。
5. **退出口在设置页、不在胶囊**：胶囊单槽按优先级让位，常驻提示会跟 armed / listening 抢位；设置页是用户找「为什么它长这样」的地方。无 ticker，30s 宽限到点那一跳这一行不自己消失。
6. **对照构建也带 `-Clean` 与新原生三件**：变量只剩 AEC 一个；对照包上只取 Xruns，不取任何语音质量读数。结论只许两种写法（见 T11 步骤 5）。
7. **Shortcuts 不引 `expo-quick-actions`**：静态 xml 足够，少一个 autolinking 缝候选。标签必须 `@string`。「今日提醒」无路由不做。深链只升层不开麦（§12.2），锁屏态记「不适用」。
8. **语音层 Pan 只挂把手带**：与层内 ScrollView 不打架；把手带接替 `voice-sheet-collapse` 的 testID 与 ≥56 演员身份。代价：从层身向下拖不再收起（B2 T3 那条验收路径的形态变了，收起路径改成把手带 / 暗区 / 返回键三条）。
9. **发送 / 打断合一键的语义取 `cancelCurrentTurn`**：「停播再听」仍归光球轻点。忙时要发新话先停再发（市面惯例）。testID 与演员身份不变；C 身份忙时可点。**v1 回滚代码不删**（§0 第 2 条 #16），`uxV2.voiceSheet` / `uxV2.presence` 开关仍是回滚路径。
10. **对比度的数值以 jest 跑出来的为准**：手算只给起点；alpha 先合成再算亮度（T14 反向验证 M2 证明不合成会判错）。深色 `fg2` 已达标不动。
11. **缺陷 A 横屏半按 lever 顺序、先量后改**：撤底栏 → 隐 chips + 层覆盖整列（大球接轻点）→ 球降级判据兜底。每一步的期望值从实测填，不从推算填（B4 把 192 推错成 98.67 那条）。
12. **还原表口径**：云栈 `speed_kmh / gear`、App 设置、系统省电模式、`device_state`、`animator_duration_scale`（若动）逐项「改成 / 还原为 / 回读」；**speaker 音量不在表里**（§0 第 2 条 #5）。
13. **语音类读数只有 Xruns 与 speaking 截图**，都带包锚；对照包与主线包两锚分开。
14. **组件层没有 jest 面**：T4 / T8 / T12 / T13 / T15 的判据全是真机读数，每条配阴性；能提成纯函数的（T3 段语义与 dismissed、T7 `lowPower`、T14 对比度、T15 `sheetOrbDp`）都提了。
15. **两道泓舟闸的位置写死在 T5 步骤 1 / 2**：dry-run 报不可达就停下要推送授权，`--apply` 单独授权；过不了就 T1–T4 宿主侧收口、T5 记 ⬜。

## 6. 实施记录（分批回填；每批一个会话，写完即停）

> 格式照 B1–B4 计划 §6：先**开工基线**（自己跑出来的数——jest 条数、tsc、pytest / go test（第 1 批）、`git log origin/main..HEAD` 与 `HEAD..origin/main` 计数与归属、设备在线、`lastUpdateTime`、`dev_stack target show`、adb server 是哪一份）、再逐任务的提交与读数、再**反向验证**逐条（预期 vs 实红）、再「本批踩的坑」、最后「遗留 / 给下一批的话」。读数只写自己跑出来的数，不复述计划预期；**未跑的一律不写 ✅**；语音类读数必须带 `lastUpdateTime` 锚；改过的设备 / 云栈状态要有还原表；引用别批结论要**现读**不凭印象（B4 §6.3 遗留②）。

### 6.1 第 1 批「缺陷 C」（T1–T5）

**开工基线（2026-09-04，本会话自己跑出来的数）**

| 项 | 读数 |
|---|---|
| `check_android_env.ps1` | 退出码 0（18 pass / 0 warn / 0 fail） |
| `dev_stack target show` | `{"source":"file","status":"target","target":"cloud"}` |
| adb server（`netstat -ano \| findstr :5037`） | LISTENING PID 60816 = `D:\Android\Sdk\platform-tools\adb.exe`（= `ANDROID_HOME` 那份 ✅，坑 73 的纪律成立）。`Get-Process adb` 另有 PID 14604 **取不到 Path / StartTime**——就是坑 73 里那个 2026-08-27 的孤儿 adb（普通权限查不到属性），本轮没碰它 |
| `mobile: npm test` | **50 suites / 499 tests 全绿**（58.6s） |
| `mobile: npm run typecheck` | 0 error |
| `python -m pytest orchestrator/edge/tests -q` | **843 passed**（206.97s） |
| `go test ./gateway/edge` | ⬜ 见「本批踩的坑」①——本机**没有 Go 工具链**，唯一途径是 `scripts/run_go_tests.ps1`（Docker 容器），而 Docker daemon 当时是停的 |
| `cd hmi && npm test` | 298 pass / 0 fail（12.8s；开工期跑的，作网关加键前的对照） |
| 设备 `lastUpdateTime` | **`2026-09-02 16:45:36`** ✅ 与 §0 第 1 条的锚一致（`versionName=0.1.0`，`firstInstallTime=2026-08-25 23:43:56`） |
| `git log --oneline origin/main..HEAD` | **空** |
| `git log --oneline HEAD..origin/main` | **空** |
| `git status --short` | 干净 |
| 起点 SHA | `ab9680e`（= `origin/main` = HEAD，无分叉、无别人的在途提交） |
| 云栈 `dev_stack status` | 5/5 endpoint healthy；**`release_sha=434a0461`**（云上跑的比 main 老，本批 deploy 前的基线） |
| Metro | 8081 `packager-status:running`，但**是别人 2026-09-03 22:26 起的**（PID 41312/75696），按纪律不停、不重启；本批未用它做热载 |
| worktree | 未分树（泓舟未授权），在主工作树做 |

**逐任务**

**T1 步骤 0（方案 §11.1 B5 行回写）** — 提交 `54b5763`。落点说明：计划原话是「B5 行**末尾**追加一句」，但 §11.1 是 markdown 表格，追到最后一格（「依赖」列）语义不对——那句讲的是**范围**，所以追加进「范围」列末尾，表格列数不变。状态行按事实写「已批准（泓舟 2026-09-04）… 第 1 批进行中」，不是计划里那句「草案待批」（计划是拆计划当轮写的，批准发生在其后）。

**T1（proto 字段 9 + `_stamp_driving` + pytest）** — 提交 `05efce6`（3 files，+126/−5）
- 跑红：5 条全红，**红法对**——4 条 `AttributeError: driving`、1 条 `ValueError: Protocol message FinalResult has no "driving" field`，没有一条是 assertion。第一条报的是 `AttributeError` 而不是 `assert finals`，**顺带证明了「打开空调」确实走本地快路径**（`_finals` 非空）。
- `gen-proto.ps1` 退出 0；`git status --short -- gen/` 空（gen/ 仍 gitignore，未 force-add）。
- 跑绿：新文件 5 passed；`orchestrator/edge/tests` **843 → 848（+5）**；`test/test_remaining_e2e_protocol.py` 187 passed 不受影响。

**T3（客户端半）** — 提交 `0455e38`（5 files，+113/−11）
- 跑红 6 条：drivingMode 3（段内不刷新 / 压住本段 / 同一毫秒边界）+ sessionStore 3（final true 登记 / final false 登记 / `dismissDriving` 不存在）。「false 段后开新段」「手动压过退出」「旧网关不动」三条在实现前就是绿的——它们钉的是不变量，红只在反向验证里看得见（与计划步骤 2 的预告一致）。
- 比计划多一条用例：计划步骤 5 的 M4 要求「补一条 `dismissedAt === trueAt` 的边界用例」，我在步骤 1 就补了 ⇒ drivingMode 是 **+6** 不是 +5，全量 **499 → 509**（计划估 ≈508）。
- `tsc` 0。

**T4（设置页退出口 + `usePresence` 接线）** — 提交 `4d4c15d`（2 files，+42/−3）
- `tsc` 0；`npm test` **509 条不变**（`.tsx` 无 jest 面，判据在 T3 已钉）。
- 步骤 3 的 Metro 热载冒烟 **⬜ 未做**：8081 上的 Metro 是别人 2026-09-03 22:26 起的（PID 41312/75696），纪律是「不停别人的 Metro」；用它热载会把别人的会话状态搅进来。这条冒烟的正例本来也要 Edge 标（T5 步骤 4），一并挂 T5。

**反向验证（每条先 `grep` 证明变异落盘；副本还原，未用 `git checkout --`）**

T1（副本 `server.py.orig`）：

| # | 变异 | 计划预期红 | **实红** | 差在哪 |
|---|---|---|---|---|
| M1 | 删 `_stamp_driving` 的 `elif which == "final"` | 4 条 | **4 条** | 条数对，**但红的不是计划说的那四条**：计划说「cloud 那条 progress 半仍绿」，实际 cloud 那条**红了**（它同时断言 final），绿的是**泊车 false 那条** |
| M2 | 删出口调用、把两处旧调用搬回云端路径 | 3 条（本地两条 + 覆盖那条） | **2 条**（本地 moving + 挡位 D） | ① 泊车那条同上假绿；② 「覆盖」那条走的是 `_CLOUD_ROUTED`，搬回云端路径后覆盖**仍然成立**，本就该绿——计划这条预期是错的。**「本地快路径的 final 不带标」仍被抓到（2 条红），盖在出口的理由成立** |
| M3 | `_is_driving` 只看 `speed > 0` | 恰 1 条 | **恰 1 条** ✅ | — |

⚠ **本批最值钱的一条**：`test_local_fast_path_final_is_stamped_false_when_parked`（泊车 ⇒ final `driving is False`）**对「final 到底有没有被标」零敏感**——proto 的 bool 缺省就是 `False`，把整个 final 分支删掉它照样绿。这正是坑 69 的形态（判据对被改的那一项零敏感），而且是**用例自身的形状**造成的，不是变异没落盘。它仍留着（键在场 / 值正确的正向断言有意义），但**不能拿它当「final 被标了」的证据**——那个证据是 M1/M2 里红的那几条。

T3（副本 `drivingMode.ts.orig` / `store.ts.orig`）：

| # | 变异 | 计划预期红 | **实红** | 差在哪 |
|---|---|---|---|---|
| M1 | `inSegment` 恒 false（退回旧语义） | 恰 1 条 | **恰 1 条**（段内不刷新）✅ | — |
| M2 | 删 `drivingActive` 的 `dismissedAt` 判据行 | 1 条 | **2 条**（压住本段 + 同一毫秒边界） | 我按 M4 补的边界用例依赖同一行 ⇒ 覆盖更严，不是缺陷 |
| M3 | `typeof === 'boolean'` 改成 `!!data.driving` | 恰 1 条 | **恰 1 条**（旧网关不动）✅ | 这一红就是「只认布尔」那条兼容的理由 |
| M4 | `dismissedAt >= trueAt` 改成 `>` | 计划预测**零红**，要求补边界用例 | **恰 1 条**（同一毫秒边界） | 用例已在步骤 1 补上 ⇒ 计划那句「预期零红」在补了之后不再成立，**这正是计划自己要的结果** |

**T2（网关半）** — 提交 `3a70029`（4 files，+41/-1）
- 跑红：**只有** `TestEventToMapFinalCarriesDriving` FAIL——`frames_test.go:18: final frame lacks driving key (want true): map[actions:[] follow_up: need_confirm:false speech:好的 type:final]`；`TestEventToMapProcessStillCarriesDriving` PASS（回归面在）。与计划预期一字不差。
- 跑绿：`./gateway/...` 四个包全 `ok`（cloud / deployprofile / edge / tlscfg）。
- `go vet` **⬜ 未单独跑**：`run_go_tests.ps1` 只接受包名、不接受 `-` 开头参数；`go test` 本身已跑 vet 的默认子集（printf / atomic / bool / ifaceassert 等）。
- 契约文档：主计划 §2 `final` 行键表加 `driving` + 一句说明。`test/e2e_process_region.py` 简单轮 / 复杂轮各加一条 `final.get("driving") is False`；**本地栈未复跑**（`target=cloud` 禁本地 Compose），云上的证据是 T5。

T2 反向验证（副本 `main.go.orig`）：

| # | 变异 | 计划预期红 | **实红** |
|---|---|---|---|
| M1 | 删 `"driving": f.Driving,` | 恰红 final 那条 | **恰红 final 那条** ✅ |
| M2 | 改成 `if f.Driving { result["driving"] = true }`（omitempty 形态） | 红在 `want false` 那半 | **`final frame lacks driving key (want false)`** ✅——**这一红就是「恒带键」的理由**，一字不差 |

**T5（真机读数）——两道闸都过了，核心读数全部取到**

- **步骤 0–1（前提 + dry-run）**：`git status` 干净；`target=cloud`；设备在线、`lastUpdateTime` 仍 `2026-09-02 16:45:36`；云栈基线 `speed_kmh=0 / gear=P`（`GET /api/vehicle/state` 回读）、`release_sha=434a0461`。
  `python scripts/dev_stack.py deploy --sha HEAD` ⇒ **`status: dry_run`，`blocking_changes: []`，exit 0**。
  ⚠ **计划这一步的预期是错的**：计划写「dry-run 若报 SHA 不可达 / 未推送 ⇒ 停下要推送授权」——**工具根本不校验「main 可达」**，5 条未推送的提交照样 dry-run 通过。CLAUDE.md §6.1 那条「只接受 main 可达的 SHA」是**给人的纪律，不是工具里的闸**。⇒ 按纪律停下、列完整 `origin/main..HEAD`（5 条全是本轮我的，`HEAD..origin/main` 空、无别人在途）、**单独取得泓舟推送授权**后才推：`ab9680e..3a70029`。

- **步骤 2（`--apply`，泓舟单独授权）——第一次失败、第二次成功**
  - **第一次 ❌**：`{"error_category":"runtime","status":"failed"}`，exit 1。⚠ **CLI 把失败原因整个丢掉**（`cloud_release.py:220` 只 `_emit({"status":"error","error_category": exc.category})`，stderr 只有一句 `cloud-release: operation failed`）⇒ 光看输出无法定位。用 scratchpad 诊断包装（import `cloud_release_lib` 跑同一条 `execute_deploy`、catch `ReleaseError` 打全文；**不改任何仓库文件**）拿到真话：**`command failed (255): ssh: Connection closed by <host> port 22`**。
  - **定性（做了对照实验，没停在第一个自洽解释上）**：

    | # | 命令 | 结果 |
    |---|---|---|
    | a | `ssh -v ... "echo PING"` | **成功**，`Authenticated ... using "publickey"` |
    | b | `ssh ... "sudo -n true && echo SUDO-OK"` | Connection closed |
    | c | `ssh ... "ls -l /opt/car-agent/shared/bin/remote-release.sh"` | **成功**（还打了登录 banner） |
    | d | `ssh ... "sudo -n true"` | 空输出（未见 closed） |
    | e | `ssh ... "true && echo AND-OK"` | **成功**（`AND-OK`） |
    | f | `ssh ... "sudo /opt/.../remote-release.sh"` | Connection closed |
    | g | `ssh ... "id -un"`（**不带 sudo、不带 `&&`**） | **Connection closed** |

    第一个自洽解释是「带 `sudo` 就被拒」（b/f 支持）——**被 g 推翻**：一条最普通的 `id -un` 同样被关。真正的模式是**「短时间内连接数超阈值后开始拒绝」**（几分钟里开了 dry-run×2、apply×2、诊断×2、探测×4 共 10+ 条 SSH/SCP），典型是服务端 fail2ban / sshd 连接频率保护；`--apply` 自己就要连开 4 条（prepare-upload → scp 51MB → chmod → deploy），落在阈值之后。
    **两条支持它的证据**：① 停手后挂每分钟探测，**第 4 分钟 `ssh "echo SSH-BACK"` 自行恢复**（1/2/3 分钟仍被拒）；② **冷却后原样重跑 `--apply` 一次就过**，中间没改任何配置或代码。
    ⚠ **仍未验证到底**：没有登上服务器看 `fail2ban-client status sshd` / `journalctl -u ssh` 拿封禁记录，所以它是**有两条正向证据的最合理解释，仍不是已证的根因**（TCP 22 全程可达，`Test-NetConnection` TcpTestSucceeded=True）。**不把没验证的解释当根因写死**（坑账 73 同一形态）。
  - **第二次 ✅**：`STATUS: submitted`，exit 0。**云上 `release_sha = 7b594f379c1fbfb5156c55c4e0dc573957b49d28` = 当时的 HEAD**，`dev_stack status` 5/5 endpoint healthy。
    ⚠ **`dev_stack verify` 自己失败了**（`{"status":"failed"}`，产物 `.artifacts/dev-stack-verifications/20260904T091630Z-unknown.json` 里 `release_sha: null`、`case_ids: []`），但**部署是成功的**——判据用 `status` 的 `release_sha`，不是 `verify` 的退出码。没重跑 verify（控 SSH 节奏）。**verify 失败 ≠ 部署失败，这两件事本轮第一次被分开。**

- **设备跑的是不是本轮的 JS——先证明仪器**：取证屏 `/native-spike` 的 driving 行出现 `dismissedAt=0`（**T3 新加的字段**）⇒ dev-client 从 Metro 拿到的是本轮的 bundle，不是 09-02 内嵌的那份。**没有这一步，后面所有读数都可能是在验旧代码**（B4 缺陷 B 的教训）。

- **步骤 3 核心读数：一句简单轮进入 / 一句简单轮 + 30s 退出**（chip「打开空调26度」＝ `hvac.set`，端侧快路径、**无 process 帧**——正是缺陷 C 的形态）

  | 时刻 | 动作 | 取证屏读数 |
  |---|---|---|
  | 17:23 | 注入 `speed_kmh=30 / gear=D`（回读确认）→ 一句简单轮 | `driving: **true**（manual=false **edge.trueAt=1788513798008** edge.falseAt=0 dismissedAt=0）` |
  | — | 注入 `0 / P`（回读确认），墙钟 `1788513881766` | — |
  | 17:25 | 泊车后一句简单轮 | `driving: true（… **edge.falseAt=1788513915533** …）`——**这就是 B4 那 3h12m 里从没发生过的那件事**；距注入 33.8s，距截图 14.7s **< 30s 宽限 ⇒ 仍 true 是对的** |
  | 17:27 | 过宽限后再一轮触发重渲 | `driving: **false**（… edge.falseAt=**1788513915533** …）`——**falseAt 没被第三轮刷新**（「已在 false 段不刷新起点」真机成立），距 falseAt **130.6s > 30s** |

  ⇒ **缺陷 C 的完整闭环在云栈上跑通了**：简单轮既能带 `driving=true` 进入，也能带 `driving=false` 触发退出。截图 `b5-5-simple-enter.png` / `b5-5-false-logged.png` / `b5-5-simple-exit.png`。

- **步骤 4 手动退出口 + 「段」语义**（截图 `b5-5-exit-row.png` / `b5-5-after-exit.png` / `b5-5-dismissed.png` / `b5-5-same-segment.png` / `b5-5-new-segment.png`）

  | # | 动作 | 读数 |
  |---|---|---|
  | 1 | 注入 30/D → 简单轮 → 进设置页 | 行车档开关**关着**（灰），其下出现 **「自动行车中 · 座舱判定为行驶 ┊ 退出」**——**这正是 B4 `b4-14-t3.png` 那个「开关灰着、App 却在行车档里、找不到出口」的缺口**；desc 也已是新的「（每轮回答后判定）」 |
  | 2 | 点「退出」（墙钟 1788514241089） | 那一行**立刻消失**（**不等 30s**）；取证屏 `driving: false（… edge.trueAt=1788514128758 **edge.falseAt=0** **dismissedAt=1788514244165**）`——**Edge 仍在标 true（falseAt=0，车还在"行驶"），`driving` 却已是 false**：退出压住了本段而**没有改判据**，Edge 事实原样保留。点击→写入 3.1s |
  | 3 | 同段内（车仍 30/D）再一句简单轮 | `edge.trueAt` **仍是 1788514128758**（**没刷新**）、`driving` 仍 **false** ⇒ **「段内连续 true 不刷新起点」在真机成立**。没有这条收紧，退出按钮下一轮就失效 |
  | 4 | 注入 0/P → 一轮 → 注入 30/D → 一轮（开新段） | `edge.trueAt=**1788514525751**`（新段起点，比旧的晚 397s）、`dismissedAt` **仍 1788514244165 未被清**、`driving: **true**` ⇒ **新段照常自动进入** |

  ⇒ **「退出只压本次、不改判据」四格全部成立**（泓舟裁决的原话）。

- **步骤 5 手动开关不受影响**：行车档手动**开** ⇒ 退出行**不出现**（判据 `autoDriving = !drivingManual && drivingActive(...)`，手动的出口就是开关本身）。截图 `b5-5-manual-on.png`。

- **步骤 6 `Msg.driving` 单独验 ⬜ 未做**（B4 §6.3 遗留⑤ 仍开）：它要一句会出过程区的复杂轮 + 退出后回看老气泡，本轮时间用在缺陷 C 主链上了。

- **步骤 7 HMI 回归**：`cd hmi && npm test` **298 pass / 0 fail**（网关加键前后各跑一次，都是 298）。**现读**核过 `hmi/src/App.tsx`：只有 `:383`（process 分支）读 `data.driving`，`:420` 起的 final 分支**不读它** ⇒ 多出来的键 HMI 收到即忽略。真栈 Vite + 浏览器那半 **⬜ 未做**。

- **步骤 8 还原表**：见下方还原表。收尾时又发了一轮让 App 自己退出行车档（`edge.falseAt=1788514870426` → `driving: false`），设备与云栈都回到泊车基线，截图 `b5-5-final-parked.png`。

**本批踩的坑（主计划 §9 从 75 起）**

① ⛔ **本机没有 Go 工具链**，`go test` 的唯一途径是 `scripts/run_go_tests.ps1`（Docker 容器），而**开工时 Docker daemon 是停的**（`Get-Service com.docker.service` = Stopped）。`where go` 空、`C:\Program Files\Go` 不存在、`GOROOT`/`GOPATH` 未设、全盘 `go.exe` 搜索无果。⇒ **计划里所有 `go test ./gateway/edge/ ...` 的命令在本机都不能直接跑**。本轮启动了 Docker Desktop（`D:\Program\Docker Desktop.exe`，daemon 5s 起来，server 29.6.1）——**这一步已向泓舟报备，不是本批边界里的东西**。

② ⛔ **`run_go_tests.ps1` 一趟 ≈35 分钟以上，成本全在 `cp -a /src/. /work/`**：它把**整个仓库**拷进容器（本机实测 **8.03 GB / 84,602 文件**），且没有挂载 module cache（每趟重跑 `go mod tidy`）。实测拷贝速度约 **0.2 GB/分钟**（Docker Desktop 的 npipe + 只读 bind mount）。
   **8GB 里 5.83 GB / 11,530 文件是 `.artifacts/`**（`.gitignore:108` 忽略它，但 docker bind mount 照拷），其中 `.artifacts/release-clones/` 是**若干份完整仓库克隆**（每份自带 `go.mod`，所以是独立模块、主模块 tidy 会跳过——只是白拷）。⇒ 这个 wrapper 的成本与 Go 代码量无关，**与本机攒了多少 artifacts 有关**。计划把 `go test` 当成便宜动作（跑红 / 跑绿 / 全包 / M1 / M2 五趟）⇒ 按原样执行是 **3 小时**。
   ⚠ 另一面：**这个 wrapper 拒绝任何以 `-` 开头的参数**（`Test-GoPackagePattern` + `if ($item.StartsWith("-"))` throw）⇒ 计划步骤 2 的 `go test ./gateway/edge/ -run 'TestEventToMap' -v` **在本机语法上就跑不了**；而且包名**结尾不能带 `/`**（`./gateway/edge/` 会被 `suffix.Split("/")` 判出空段而 throw），要写 `./gateway/edge`。
   ⇒ 本轮做法：**权威读数仍走 `scripts/run_go_tests.ps1`**；反向验证与迭代用一个 scratchpad 里的快速回路（同样只读挂载源、同样跑完校验 `go.mod`/`go.sum` 哈希、同样校验包名，区别只是 `/work` 用 named volume 复用，每趟只重拷 `gateway/` + `gen/`）。**这个脚本不入库**。

③ **新版 protobuf 生成的 `*_pb2.py` 里没有明文字段名**（描述符是二进制 `serialized_pb`）⇒ 计划步骤 3 的判据 `Select-String -Path gen\python\...\orchestrator_pb2.py -Pattern 'driving' | Measure-Object` **计数是 0，不是 ≥2**——它不是「生成失败」的信号。**行为判据才作数**：`FinalResult(driving=True).driving is True`、`FinalResult.DESCRIPTOR.fields_by_name['driving'].number == 9`、`ProcessUpdate` 仍是 6。Go 侧那条（`func (x *FinalResult) GetDriving`，恰 1 条）照计划成立。**又一例「门禁读的是形式不是内容」。**

④ **scratchpad 里临时 `.ps1` 连撞两次编码坑**（记忆里那条「带中文注释的 .ps1 必须 UTF-8 with BOM」的两个面）：第一次注释里的中文让 PowerShell 5.1 按 ANSI 读、`param()` 块直接 `Unexpected token ')'`；改成纯英文注释后第二次仍炸——**脚本里写死的仓库路径含中文「产品」，被读成 `浜у搧`**，`Resolve-Path` 找不到 `go.mod`。⇒ 临时 PowerShell 脚本一律**全 ASCII**，仓库路径用 `(Get-Location).Path` 由调用方给，不写死。

⑤ **`git` 对本仓的行尾**：工作区与索引里 `server.py` / `*.ts` 都是 **LF**，但 `core.autocrlf=true` ⇒ 每次 `git add`/`diff` 都刷一片 `LF will be replaced by CRLF` 警告。用 Python `io.open(..., newline="")` 回写保持 LF 是对的（实测 diff 只有真正改动的行，没有整文件行尾漂移）。

⑥ ⛔ **`dev_stack deploy` 的 dry-run 不校验「SHA 是否 main 可达」**（本轮实测：5 条未推送的本地提交，dry-run 照样 `status: dry_run` / `blocking_changes: []` / exit 0）。CLAUDE.md §6.1 与 AGENTS §3.2 那句「cloud deploy 只接受 clean、已提交、**main 可达**的 SHA」**是给人的纪律，不是工具里的闸**。⇒ 计划 §0 第 4 条写的「dry-run 若报 SHA 不可达 / 未推送 ⇒ 停下要授权」**永远不会触发**；这道闸只能靠人在 dry-run **通过之后**自己停下来核 `origin/main..HEAD`。**门禁不存在的时候，纪律得自己长出判据。**

⑦ ⛔ **`cloud_release.py` 把失败原因整个丢掉**（`:220` 只 `_emit({"status":"error","error_category": exc.category})`，stderr 只有一句 `cloud-release: operation failed`）⇒ 部署失败时**本地拿不到任何可行动的信息**。本轮靠一个 scratchpad 诊断包装（import `cloud_release_lib`、跑同一条 `execute_deploy`、catch `ReleaseError` 打全文，不改仓库文件）才拿到 `command failed (255): ssh: Connection closed by <host> port 22`。⇒ **部署脚本的「信息最小化」会把可诊断性一起最小化掉**；下次直接上诊断包装，别在 `error_category` 上猜。

⑧ **短时间内密集 SSH 会把自己锁在外面**：本轮几分钟内开了 10+ 条 SSH/SCP（dry-run×2、apply×2、诊断×2、探测×4），之后连最普通的 `ssh ... "id -un"` 都 `Connection closed by ... port 22`，而 TCP 22 仍可达。⇒ **真栈调试要控连接节奏**（每条命令都是一次新 SSH，`--apply` 自己就要 4 条）；撞上之后**先等，别继续重试**——重试只会把窗口继续推后。本轮实测**停手后第 4 分钟自行恢复**（1/2/3 分钟仍被拒）。
   ⚠ 这条**没有验证到底**（登不上去看 `fail2ban-client status sshd` / `journalctl -u ssh`），它是**目前最合理的解释而非已证根因**。

⑨ ⛔ **判「哪块屏是活的」不能只 `grep mState=`**：本轮第一张截图 27641 字节**纯黑**——不是坑 74 那个 `DevLauncherErrorActivity`（前台明明是 `MainActivity`），而是**截了物理关着的那块屏**。`dumpsys display | grep mState=` 吐出的两行**没有标明各属于哪个 display**，按出现顺序猜会猜反。正确读法是**三行对齐读**：`grep -E 'Display Id|mUniqueId|mState='`，实测本机 `local:4630946481727302019`（内屏 2224×2488）= OFF、`local:4630947090644569220`（外屏 1080×2520）= ON（机身折叠态）。⇒ **纯黑截图有三种成因**（Metro 死 / 截了关着的屏 / 真息屏），字节数相近，**必须先定位是哪一种**。

⑩ **`dev_stack verify` 失败 ≠ 部署失败**：本轮 `--apply` 返回 `STATUS: submitted`、`dev_stack status` 的 `release_sha` 已是新 SHA 且 5/5 healthy，但紧接着的 `dev_stack verify` 返回 `{"status":"failed"}`（产物里 `release_sha: null` / `case_ids: []`）。⇒ **判部署成没成，看 `status` 的 `release_sha`**；`verify` 是另一件事（它自己还要开 SSH，本轮大概率又撞了连接节奏），它红不构成回滚理由。

⑪ **真机验「修好了」之前，先证明设备跑的是本轮的代码**：本轮先在 `/native-spike` 上看到 T3 新加的 `dismissedAt=0` 字段，才开始取缺陷 C 的读数。设备上的 APK 是 09-02 的包，**如果它用的是内嵌 bundle 而不是 Metro 的，后面每一条读数都是在验旧代码而看不出来**（B4 缺陷 B 同族）。⇒ **仪器先自证，再用仪器测被测物。**

**收口读数（本会话自己跑出来的）**

| 项 | 开工基线 | 收口 | Δ |
|---|---|---|---|
| `mobile: npm test` | 50 suites / **499** | 50 suites / **509** | **+10**（drivingMode +6、sessionStore +4；计划估 ≈508，多的那条是 M4 要求补的边界用例） |
| `mobile: npm run typecheck` | 0 error | **0 error** | — |
| `pytest orchestrator/edge/tests` | **843** | **848** | **+5** |
| `pytest test/test_remaining_e2e_protocol.py` | — | **187 passed** | 不受影响 |
| `go test ./gateway/...`（快速回路） | ⬜（本机无 Go / Docker 停） | **4 包全 ok**（cloud / deployprofile / edge / tlscfg），其中 edge **+2** | +2 |
| `cd hmi && npm test` | 298 pass / 0 fail | **298 pass / 0 fail** | 0（网关多一个键，HMI final 分支现读确认不读它——`hmi/src/App.tsx:383` 只有 process 分支读 `data.driving`，`:420` 起的 final 分支没有） |
| `dev_stack status` | 5/5 healthy, `release_sha=434a0461` | 5/5 healthy, **`release_sha=7b594f37`** | **已上云**（第二次 `--apply` 成功；`verify` 子命令自己失败但部署是成的） |

**提交（6 个，均已推送；`origin/main = HEAD = 3a70029`）**

| SHA | 任务 | 面 |
|---|---|---|
| `54b5763` | T1 步骤 0 | 方案 §11.1 B5 行回写（1 file，+2/−2） |
| `05efce6` | T1 | proto 字段 9 + `_stamp_driving` + pytest（3 files，+126/−5） |
| `0455e38` | T3 | 客户端半（5 files，+113/−11） |
| `4d4c15d` | T4 | 设置页退出口 + 接线（2 files，+42/−3） |
| `3a70029` | T2 | 网关半 + 契约文档 + e2e 断言（4 files，+41/−1） |

**还原表**

| 项 | 动过？ | 回读 |
|---|---|---|
| 云栈 `speed_kmh` / `gear` | **动过**（30/D ↔ 0/P 共 4 轮注入） | **已还原 `0` / `P`**，`GET /api/vehicle/state` 回读确认 |
| App 行车档开关 | **动过**（手动开 → 关，验步骤 5） | **已关回**，截图 `b5-5-restored.png` 回读 |
| App 行车档状态 | 取证期间进过 3 段行车档 | **已退出**（收尾一轮登记 `falseAt=1788514870426` → `driving: false`，`b5-5-final-parked.png`） |
| App 角色 / 免唤醒 / 播报 | **没动**（角色仍「手持」，截图可见） | — |
| 设备端 App | **没装新包**（`lastUpdateTime` 仍 `2026-09-02 16:45:36`），只是重连 Metro 取了本轮 bundle | — |
| `adb reverse tcp:8081` | **本轮建的**（开工时 `adb reverse --list` 为空） | 留着（第 2 批还要用） |
| 云栈 release | **动过**：`434a0461` → **`7b594f37`** | 这是本批的交付，不还原 |
| 本机 Docker daemon | **启动了**（此前 Stopped；泓舟已批） | 仍在运行；两个卷 `b5-gowork` / `b5-gomodcache` 是本轮建的临时卷 |
| speaker 音量 | 不在表里（§0 第 2 条 #5） | — |

**遗留 / 给下一批的话**

① **缺陷 C 已在云栈上闭环**（T5 步骤 3–5 全部取到，云上 `release_sha=7b594f37`）。仍开的两格：**步骤 6 `Msg.driving` 单独验 ⬜**（B4 §6.3 遗留⑤，要一句复杂轮 + 退出后回看老气泡）、**步骤 7 的真栈 HMI（Vite + 浏览器）那半 ⬜**（单测 298 已绿、`App.tsx` final 分支不读该键已现读核过）。两条都便宜，随第 2 批真机轮顺手做。
   ⚠ 一条**没验的**：本轮所有轮次都用 chip「打开空调26度」（`hvac.set`，端侧快路径）。**「上云路径的 final 也带标」在真机上没单独验过**——它在 pytest 里有 `test_cloud_path_stamps_both_progress_and_final`，但那是单测。要在真机上确认，得发一句会上云的复杂轮再看 final。

② **T4 步骤 3 的 Metro 热载冒烟——正例与阴性都在 T5 里做掉了**：正例＝设置页出现「自动行车中 · 退出」并点得动（T5 步骤 4），阴性＝手动开 ⇒ 退出行不出现（T5 步骤 5）。**没有动别人的 Metro**（8081 上那个是 2026-09-03 22:26 起的），只是让设备重连它取新 bundle；`adb reverse tcp:8081` 是本轮建的。

③ **`go vet ./gateway/edge` ⬜**：`run_go_tests.ps1` 不接受 `-` 开头参数、快速回路也只跑 `go test`。`go test` 已含 vet 默认子集，缺的是完整 vet。要补的话得再写一条 docker 命令。

④ **官方 `scripts/run_go_tests.ps1` 本轮没有跑出过一次读数**：起了一趟（本轮全程在跑），**35+ 分钟只完成了 8GB 里的 6.8GB 拷贝**，且它用默认 `GOPROXY=https://proxy.golang.org` ——快速回路第一次跑正是因为这个默认值在 `go test` 阶段拿 `.zip` 时 **TLS handshake timeout**（`go mod tidy` 只取 `.mod` 所以看不出来）。⇒ **官方脚本在本机大概率也会撞同一堵墙**，但**这一条本轮没有实证到底**（拷贝阶段还没结束）。要么给它加 `-e GOPROXY`（改仓库文件，本批边界外），要么把 `.artifacts/` 从挂载里排除——**两条都该交泓舟裁**，不要下一批自己动手改。

⑤ **本批的两个 Docker 卷 `b5-gowork` / `b5-gomodcache` 留着没删**（删除是红线）：下一批要复用就直接 `gotest_fast.ps1 run`，不用再 init；不要了就交泓舟裁 `docker volume rm`。快速回路脚本在 scratchpad，**不入库**。

⑥ **`test/e2e_process_region.py` 的两条新断言只改了没跑**（`target=cloud` 禁本地 Compose）。它不在 cloud 缺省集里，所以云上也不会自动跑到。⇒ 下一次有人开本地栈时顺手验一趟，或者在 T5 补做时手工核 final 帧里逐字有 `"driving":false`。

⑦ **`b5-gowork` 卷里的 `/work` 是 T2 那一刻的源码快照**：下一批跑 `gotest_fast.ps1 run` 会自动重拷 `gateway/` + `gen/` + `test/fixtures/`，但**不会重拷别的目录**——如果下一批改了 gateway 以外的 Go 代码，记得扩 `run` 分支的拷贝清单，否则会拿着旧文件跑出假绿。

⑧ ⛔ **本批 11 条坑只落在 §6.1，主计划 §9 的 75+ 还没搬**：计划 §1 把「§9 坑账追加 75 起」归在 **T2 / T18**，T2 那半（协议契约表）已做，**搬运那半归第 4 批 T18**。⇒ T18 记得把 §6.1 的 ①–⑪ 逐条评估后搬进主计划 §9（不是全搬——有几条是本机环境特有的，值不值得进坑账要当场判）。**记在这里是因为它极易漏：坑写在批记录里、下一个人读的是主计划。**

⑨ **第 2 批开工时的实况与计划 §0 第 1 条有四处不同，别照抄计划**：
   - **云上 `release_sha` 已是 `7b594f37`**（本批 deploy 的），不再是计划写的 `434a0461`；
   - **Docker Desktop 已停**（本批收尾时关的，连 `docker-desktop` WSL 发行版一起，释放 ~2.2GB）。第 2 批是 Gradle 原生构建，用不到它；要跑 go test 得先起；
   - **`adb reverse tcp:8081` 已失效**（adb server 中途自发重启掉的），要用 Metro 就得重建；开工先 `adb devices` 核设备在不在（本轮末尾出现过一次瞬时 `no devices/emulators found`，是 server 刚重启还没枚举完，几秒后自愈——**别把它当设备掉线**）；
   - **设备锚仍是 `2026-09-02 16:45:36`**（本批没装新包，只是让 dev-client 重连 Metro 取了本轮 bundle）；**第 2 批 T10 装完新包后要换锚**，此后所有语音类读数绑新锚。

### 6.2 第 2 批「重建趟」（T6–T11）

（待回填）

### 6.3 第 3 批「界面优化 + Scanner + 缺陷 A 横屏半」（T12–T16）

（待回填）

### 6.4 第 4 批「小样本 + 收口」（T17–T18）

（待回填）
