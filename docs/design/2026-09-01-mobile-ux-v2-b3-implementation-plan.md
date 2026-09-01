# UX v2.1 · B3「原生一次重建」实施计划（逐任务，按方案 v2.2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 状态：**第 1 批（T1–T3）泓舟 2026-09-01 批准开工，已完成**——三条缺陷各一提交、逐格读数见 §6.1；第 2–4 批待开工
> 交付对象：`mobile/` 执行者（人或 Agent）
> 上游真相源：[`2026-08-29-mobile-ux-v2-presence-redesign.md`](2026-08-29-mobile-ux-v2-presence-redesign.md)（方案 **v2.2**；本计划只展开 **B3**，读 §5.11 / §7.3 / §7.4 / §7.6 / §8 / §9 表中 B3′ 那一行 / §11.1 B3 行 / §11.2 B3 / §12.2 / §13 Q5·Q14）；
> B2 计划 [`2026-08-30-mobile-ux-v2-b2-implementation-plan.md`](2026-08-30-mobile-ux-v2-b2-implementation-plan.md)（**§6.4 第一行是闸结论：过，泓舟 2026-09-01 裁定放行**；其遗留出账表是本计划的输入——S4 / S6 / `plateGesture` 三条缺陷泓舟裁定记给本批）；
> B1 计划 [`2026-08-29-mobile-ux-v2-b1-implementation-plan.md`](2026-08-29-mobile-ux-v2-b1-implementation-plan.md)（§0.1 分批纪律、T12 键盘读数与 §6.3 记录——`react-native-keyboard-controller` 的裁决依据）；
> 主计划 [`2026-08-24-mobile-app-implementation-plan.md`](2026-08-24-mobile-app-implementation-plan.md)（§9 坑账 §9.43–47、M4 实施记录的构建读数、「上真 AEC」段——`96a6830` 的来历与「旧读数作废」那条）
> 纪律：沿用 B1/B2 计划 §0 + 主计划 §9 坑账；每任务「先测后码、一任务一提交」；**零后端改动；`hmi/` 不碰；共享判据（`hmi/src/*.mjs`、`pendingOps.mjs` TTL）一字不动**；新原生依赖**只有本计划 §1 清单那三件**（`expo-haptics`、`expo-blur`、`modules/foldstate`），清单之外零新原生依赖——任何「顺手再装一个」的念头都说明走偏了。

**Goal:** 把 B3 该带的原生件压成**一趟**重建：`expo-haptics`（四种触感）+ 自写折叠姿态模块 `modules/foldstate`（B4 的 tabletop/book 布局前提）+ `expo-blur`（§5.11 材质 **spike**，不通过就停在 G1-tint）；`react-native-keyboard-controller` 按 B1 T12 读数**裁定不装**（§5 第 1 条）。重建之前先修掉 B2 验收轮出账的三条 JS 缺陷（S4 / S6 / `plateGesture`），各自独立提交、**每条配肯定式验收项**——B2 的教训：只有否定式验收项的能力等于没验过。重建会把 `96a6830`（patch-package 的 Oboe `VoiceCommunication`，平台 AEC）**第一次带进 APK** ⇒ 重建后先 `dumpsys` 核 `src client=VOICE_COMMUNICATION`，唤醒率 / VAD 端点 / 回声的旧读数**一律作废重取**，B2 G2「回声 0 次」的口径整个换掉（§5 第 8 条是裁决）。默认助理角色是**独立 spike 分支构建（B3′，不进主线、不发版）**，排在最后。

**Architecture:** 触感与姿态都不发明新判据：触感的「什么时候振」是 `PresenceSnapshot` 转移的纯函数（`core/presence/hapticCue.ts`，jest 钉住），执行层薄壳（`core/haptics.ts`）挂在 `usePresence` 的 **useEffect** 里——**不挂渲染期**（B2 T14 的红条教训：渲染期同步副作用 = 渲染 A 时动 B）。折叠姿态是**事实透传**：原生模块只报 `FoldingFeature` 的 state/orientation/bounds，`isTableTop/isBook` 的派生是 `ui/layout/foldPosture.ts` 纯函数（jest），布局消费归 B4。`expo-blur` 不碰任何主线组件——spike 代码全部住在取证屏（`/blur-spike`），裁决通过才在 B4 落进材质，不通过则消费代码不进主线（依赖留在 APK 里等下一趟重建拆，§5 第 3 条）。三条缺陷修复全是既有文件的窄改：S4 在 `AuroraOrb` 加**静态 speaking 标记**（不回退 `orbAnimated`，G5「循环动画 1 个」口径不破）；S6 把 `filter` 从根盒挪进 **oversize wrapper**（RN Android 的 filter 裁子元素到容器盒，盒外的灰环/辉光是被裁的原因）；`plateGesture` 先 spike RNGH 竞争配置、不成就换**透明触摸层**（TextInput 自己消费触摸，RNGH 抢不到——绕开竞争比调竞争参数确定）。

**Tech Stack:** React Native 0.86 / Expo 57（expo-modules 本地模块，`modules/kws` 是现成模板）/ react-native-reanimated 4.5 / RNGH 2.32 / jest-expo（`mobile/test/**/*.test.ts`，纯逻辑）/ Maestro 2.9（dist 在 `D:/Android/tools/maestro-dist/maestro/bin/maestro.bat`）/ 构建走 `scripts/build_mobile.ps1`（ASCII 镜像工作区）。

---

## 0. 接手须知（先读）

1. **开工前提**：`powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；Metro `cd mobile && npx expo start --dev-client`；`python scripts/dev_stack.py target show` = cloud（真机语音轮走云栈）。第 1 批（T1–T3）在**旧 APK**（无 AEC 的那个）+ Metro 热载上做——三条修复全是 JS，不等重建；但第 1 批**不许产出任何语音类读数**（唤醒率/回声/端点），那些读数只在 AEC 进包后才算数（第 3 批 T10）。
2. **构建三前提**（第 2 批 T8 用）：
   - 构建在 **ASCII 镜像工作区 `D:\Android\builds\xiaozhou-mobile`**（仓库路径含中文，subst 与真实路径两个形态均实测不可用——主计划 §9.1 定案）；镜像同步、gradle 镜像源、SDK 预装全由 `scripts/build_mobile.ps1` 负责，**不要手工在镜像区改文件**（镜像产物，下次同步就没了——要改的都改在真仓库 `mobile/` 下）。
   - 构建前**必跑** `powershell -ExecutionPolicy Bypass -File scripts\fetch_mobile_voice_assets.ps1`——缺模型/原生件必须显式失败（CLAUDE.md §6.2）。
   - 本批新增了 Expo 本地模块与新依赖 ⇒ 构建**必须带 `-Clean`**（prebuild --clean 重生成 `android/`，否则新模块不进 settings.gradle）。预期时长参照 M4 读数：全量 22–38 分钟。
3. **原生注册的判据按注册通道分流**（⚠ 方案 §11.2 B3 写的「`PackageList.java` 含新 Package」对本批**三件新原生都不适用**——它们全走 Expo 通道，这不是方案错了是方案写那行时默认新库是 RN 社区库；照字面在 PackageList 里找 `HapticsPackage` 会白找一场然后误判失败）：
   - **RN 社区库**（本批没有新增；既有的 RNGH/Reanimated/Onnxruntime 不许丢）→ `D:/Android/builds/xiaozhou-mobile/android/app/build/generated/autolinking/src/main/java/com/facebook/react/PackageList.java`（坑账 §9.43，2026-08-30 读数：RNGH 第 73 行 / Reanimated 第 75 行）；
   - **Expo 模块**（`expo-haptics` / `expo-blur` / `modules/foldstate`）→ `android/app/build/generated/**/ExpoModulesProvider*`（expo-modules-autolinking 生成；路径在构建目录里 `grep -rn` 找，T8 有精确命令）+ **运行时探针**（`requireOptionalNativeModule` 非 null，照 `modules/kws/index.ts` 的模式）；
   - **带 `unimodule.json` 的老式库要登 `mobile/react-native.config.js`**（坑账 §9.43 的修法）。本批三件按现代规范都不该带它，但装完必须验：`ls node_modules/expo-haptics/unimodule.json node_modules/expo-blur/unimodule.json`（应 No such file）+ `ls node_modules/expo-haptics/expo-module.config.json`（应存在）。若哪件真带了 `unimodule.json` ⇒ 按 §9.43 补登再构建，**别等一趟 30 分钟构建之后用运行时报错来发现**。
4. **重建后 dev-client 是重装的 ⇒ 全套重核**（B2 §6.4 附加⑤⑥与坑⑦⑪ 的合订）：
   - `adb reverse tcp:8081 tcp:8081` + `adb reverse --list` 复核——且判据是「**每次 adb server 生命周期变化后都重建**」（daemon 自重启 / kill-server / 拔插都算），不是「重插过没有」；
   - 设备重插后**物理重插本身不够**：`adb kill-server && adb start-server` 让 server 重新枚举 USB，第一次 `adb devices` 可能仍是空列表；
   - **每条 adb 命令都带 `timeout`**（B2 被卡掉 5 条、每条 45–180s）；
   - 「应用加载失败」有两个长得一样的真因：`adb reverse --list` 验 reverse、宿主侧 `curl http://localhost:8081/status` 验 Metro 活着——**分别验，别猜**；
   - Maestro 用 `D:/Android/tools/maestro-dist/maestro/bin/maestro.bat`（**「CLI 不在本机」是 B2 已推翻的定性**——它只是不在 PATH；判据是 `find` 整盘不是 `which`）+ `--no-reinstall-driver`；⚠ 重建重装 APK 后设备上的 driver（`dev.mobile.maestro*`）还在不在要先 `adb shell pm list packages | grep maestro` 核一眼——不在就要重装 driver，MIUI 的 ADB 安装确认弹窗坑见 `mobile/e2e/README.md`；
   - dev-client 悬浮 Tools 按钮会截走手势（B2 坑④）——真机取证前先关掉。
5. **`lastUpdateTime` 是所有语音读数口径的一部分**：AEC 补丁 `96a6830` 的提交时刻是 2026-08-29 17:27:50，只有 `lastUpdateTime` 晚于它的 APK 才含 AEC（B2 整个 B 批用的包是 17:22:24 的——差 5 分钟，就没有）。**每条语音类读数旁边都要记当时的 `adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime`**；没记的语音读数视为无效。重建装机后第一件事就是取一次并写进 §6.2。
6. **六个结构性事实，写代码前记住**（每条都有 `文件:行号`，别按印象）：
   - **S4 的两面**：`ChatScreen.tsx:530` 的 `orbAnimated={snapshot.input !== 'voice-sheet'}` 是为 §11.4「同屏循环动画常态 1 个」写的，G5 的 `0.00%` 达标读数与「语音轮里 Composer 球看不出在播报」是**同一行代码的两面**——所以 T1 是权衡（加静态标记）不是回退（恢复动画会把 G5 的达标一起回退掉）。层内大球 `VoiceSheet.tsx:168` 是 `animated` 恒 true 的那「1 个」。
   - **S6 的机制**：`AuroraOrb.tsx:162` 给根 View（size×size 盒）挂 `filter: [{ saturate: 0.4 }]`，RN Android 会把子元素**裁到容器盒内**；而 `:291` 的离线灰环画在盒外 −10%、`:168` 环境辉光在 −52% ⇒ 只有 muted（唯一同时用了这两样的态）被裁成方框。
   - **`plateGesture` 的机制**：`Composer.tsx:91` 的 `makeHold(...)` 挂在 `:136-138` 包着 TextInput 的父 View 上，Android 的 TextInput **自己消费触摸**（长按=光标/选择），RNGH 抢不到 ⇒「空输入框长按录音」**从来不工作**（B2 验收轮实测：长按 4s 层不升、屏上出现文本光标水滴柄）。光球上同款手势（`:89`）工作正常——差异就在 native child。**单测和 tsc 都看不见这条，修完必须真机复验。**
   - **S2S 采集与 AEC 的覆盖面**：`handsFree.ts:128` 的 `mic: Recorder = micLease()` ⇒ 免唤醒/S2S 的帧都来自 micBus 的真麦（`AudioApiRecorder`，也就是打了 `96a6830` 补丁的那条 Oboe 输入流）。B2 出账「AEC 没覆盖 S2S 采集路径」的静态证据**指向相反**（同一条流）——真机 `dumpsys` 是终裁（T10），核完销账或转修。
   - **jest 的 `testMatch` 是 `test/**/*.test.ts` 不含 `.tsx`**（B2 §6.4 出账原话），全仓无组件级渲染测试 ⇒ T1/T2 这类渲染层修复的「红→绿」是真机 png_probe 读数（修前读数就是「红」，B2 验收轮已经取好了）；能提成纯函数的判据（T1 的 `composerOrbAnimated`）就提出来给 jest。
   - **触感不挂渲染期**：`usePresence.ts:172` 的 `presenceTrail.record` 能在渲染期调用是因为它**幂等**（按轴投影去重）；触感是一次性副作用，不幂等，StrictMode 双渲会双振 ⇒ 挂 `useEffect`（T6 的实现就是这么写的，别「顺手」挪进渲染期求简洁）。
7. **提交纪律**（B1/B2 §0 原样沿用）：新建文件 `git add -- <路径> && git commit -m '…' -- <路径>` **同一条命令链**，**`-m` 必须写在 `--` 之前**（`--` 之后的一切都是 pathspec）；提交后 `git show --stat HEAD` 复核行数；**不要 `git add -A`**；`AGENTS.md` 是所有会话都在写的文件——动它前 `git diff --stat -- AGENTS.md` 核行数、单独一个 commit；开工先 `git log --oneline origin/main..HEAD` 念一遍谁的提交在里面（B2 期间 origin/main 被别的会话推进过**四次**）。多行 commit message 走 `git commit -F -` + heredoc（反引号在 bash 单行 `-m` 里会被当命令替换执行——B1 坑⑦）。
8. **真机取证纪律**（合订）：截图 `adb shell screencap -p -d <displayId> /sdcard/x.png` + `adb pull`（PowerShell 的 `>` 损坏 PNG，B2 当场复现过）；折叠屏先 `cmd device_state state` 再选 displayId（抓错屏=全黑，不是息屏）；录屏路径写 `//sdcard/…` 或带 `MSYS_NO_PATHCONV=1`；`screenrecord` 是 VFR，帧号对墙钟前必须 `ffmpeg -vf fps=30` 重采样（ffmpeg 在 `C:/Users/Super/tools/ffmpeg/bin`，用绝对路径）；颜色/亮度类读数一律经 `mobile/e2e/tools/png_probe.py`；**diff 百分比分不出「还在但在动」和「没了」**——这类判据看图或换结构断言；`input text` 不支持中文，中文语料用 Maestro `inputText` + `-e` 变量；`uiautomator dump` 只能读坐标不能判「哪一屏」。
9. **方案 §13 已裁决的默认值不在本计划重议**（Q5：B3 只做 spike 分支验证、主线不注册不启用；Q14：blur 先 spike，不通过停 G1-tint）。本计划新增的实施判断集中在 §5。

### 0.1 分批执行：一批一个会话（新会话从这里开始）

本计划分四批，每批一个新会话，每批以「jest 全绿 + `tsc` 0 + 逐任务已提交 + §6 实施记录回填」收口；下一批冷启动只读 §0 + §0.1 + §1 + 自己那几个 `### Task N` 块（`grep -n "^### Task" <本文件>` 取行号，`sed -n` 只读自己的段），**不读整份计划、不读方案全文**（方案只在任务里点名的 §号处查）。

| 批 | 会话任务 | 性质 | 并行度 | 收口判据 | 真机？ |
|---|---|---|---|---|---|
| **第 1 批「重建前的三条 JS 缺陷」** | T1 S4 播报可读性 → T2 S6 muted 裁剪 → T3 `plateGesture` | 三个既有文件的窄改；旧 APK + Metro 热载可验 | 串行（T1/T2 都动 `AuroraOrb.tsx`；T3 独立但真机取证同一台设备） | `npm test` 全绿（400 → ≈402）、`tsc` 0、3 个 commit、每条缺陷**肯定式**真机取证各一张（+反例仍过）、§6.1 | 是（Metro 热载；不需要泓舟） |
| **第 2 批「依赖引入 + 一趟重建」** | T4 依赖与模块落盘 → T5 haptics JS 面 → T6 foldstate JS 面 ∥ T7 blur spike 屏 → T8 重建 + 注册验证 + 重装重核 | 2 个 npm 依赖 + 1 个本地 Expo 模块 + 三块 JS；一趟 22–38 分钟构建 | T4 先；**T5 先于 T6**（取证屏引 `performHaptic`）；T7 可与 T5/T6 并行（新文件互不相干；`SettingsScreen.tsx` 三者各加一行——串行提交，后提的 rebase 自己那行）；T8 最后 | 全绿（≈402 → ≈412）、`tsc` 0、5 个 commit、`ExpoModulesProvider` 三件都在 + PackageList 既有件不丢、装机 `lastUpdateTime` 已记、§6.2 | 是（装机 + 冒烟；不需要泓舟） |
| **第 3 批「真机验收 + AEC 语音读数重取」** | T9 B3 验收三条（触感四种 / 姿态手折 / blur 裁决）→ T10 AEC 首次进包的读数重取段 | 纯取证与裁决，代码预期零改动（撞上缺陷就修并记 §6.3） | 串行（同一台设备） | §11.2 B3 三条全有读数、blur spike 裁决写进 §6.3、`dumpsys VOICE_COMMUNICATION` 核过、唤醒率/VAD/回声按新口径各有读数或显式 ⬜、§6.3 | 是（**姿态手折与真人语音轮需泓舟**） |
| **第 4 批「B3′ spike + 收口」** | T11 默认助理角色 spike（独立分支构建，不合主线）→ T12 记录收口 | spike 分支 + 文档 | 串行 | spike 两问有读数（角色可选？手势响应？）、分支保留未合并、README/AGENTS.md 指针、未推送清单报给泓舟、§6.4 | 是（spike APK 装机验证） |

**B2 §6.4 出账表在 B3 的去向**（接手时逐条核，不复述）：① S4 Composer 球语音轮不可读 → **T1**；② S6 muted 被 `filter` 裁 → **T2**；③ `plateGesture` 空输入框长按不工作 → **T3**（顺带补 B2 附加④的两格：15s 硬上限、有字长按正反例）；④ 发送按钮改图标 → **归 B4**（纯视觉且要泓舟看图标方案，不混进原生批）；⑤ T11 回声「提示路径从未被观测到触发过」 → **T10 裁口径**（§5 第 8 条）；⑥ AEC 没覆盖 S2S 采集路径 → **T10 真机终裁**（静态证据见 §0 第 6 条）；⑦ T10 顺序取证（气泡先于相机）→ **T10 附加**（通路 B2 已验通）；⑧ 「换一批」chip 要 `poi_list` 且带 keyword 的语料 → **T10 附加**（非闸门）；⑨ `charging_list` 卡型未适配 → **归 B4/卡片批**（既有缺口，与原生无关）；⑩ `EdgeGlow` 零 jest → **归 B4 视觉批**（B2 出账原话「B4 视觉批顺手」）；⑪ barge-in 那一路回声没有 metric → **hmi 侧另立**（共享 `voiceLoop.mjs:476` 不改，不在本批）；⑫ VoiceSheet 壳底与浅色光球对比度 → **归 B4 视觉批**（blur spike 若过，B4 材质落地时一并裁）；⑬ 设备 USB 链路失效 / adb reverse / Maestro dist 三条 → 已收进 §0 第 4 条当操作前提。B1 §6.3 仍开的一条：排队消息补发后无第二次答复上屏（未定性，后端 trace 的事）——**不在本批**，留在 B1 记录里。

**每批开工的固定五步**（写进新会话的第一条提示词）：
1. `powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；
2. `cd mobile && npm test && npm run typecheck` 取**开工基线**（条数与 0 error），写进 §6 该批记录的第一行——读数有效期只到下一次改动；第 3/4 批还要取 `lastUpdateTime` 基线（§0 第 5 条）；
3. 只读 §0 / §0.1 / §1 + 自己批次的 `### Task N` 块；
4. 按任务顺序：写失败测试 → 跑红 → 实现 → 跑绿 → `tsc` → `git add -- <新文件> && git commit -m '…' -- <只加自己的路径>` → `git show --stat HEAD` 复核；
5. 收口：全量 `npm test` + `tsc`，把读数、遗留、撞到的坑写进 §6，**然后停下**——下一批是另一个会话的事。

**worktree**：B1/B2 期间共享 main 上「请不要推我的」四次失灵（push 的粒度是分支不是提交）。**若泓舟同意分树**，第 1 批开工前执行一次并写进 §6：`git worktree add ../car-agent-ux-b3 -b ux-v2-b3`，四批都在该 worktree 里做，最后由泓舟决定合回方式。没有分树就在主工作树做——**这一条不是可选项措辞**：不分树就要在每次提交前 `git status` 重采、`git log origin/main..HEAD --oneline` 念一遍谁的提交在前面。⚠ B3 有一件 B1/B2 都没有的事：**T11 要开 spike 分支**——spike 分支从本批收口的 HEAD 拉出，它本身就是隔离的，但**切分支前先确认工作树干净**（别把别的会话的未提交行带过去）。

**批与批之间的状态只靠两处传递**：git 提交（代码）与本文件 §6（读数与遗留）。新会话不要去翻上一批会话的对话——那些不在仓库里。

---

## 1. 文件结构（先定边界，再拆任务）

### 新建

| 文件 | 职责 | 依赖 | 任务 |
|---|---|---|---|
| `mobile/src/core/presence/orbPolicy.ts` | `composerOrbAnimated(snapshot)`：Composer 主球要不要跑循环动画的**唯一判据**（今天内联在 `ChatScreen.tsx:530`；B4 的 reduce-motion 将来落同一函数） | `presence.ts` 类型 | T1 |
| `mobile/test/orbPolicy.test.ts` | 上者的守卫 | jest | T1 |
| `mobile/src/core/presence/hapticCue.ts` | `hapticCueForTransition(prev, next)`：四种触感（唤醒轻/确认双/判死一/快门轻）对应哪些 Snapshot 转移——**纯函数、零 RN/expo import** | `presence.ts` 类型 | T5 |
| `mobile/src/core/haptics.ts` | `performHaptic(kind)`：执行层薄壳（import expo-haptics；jest 不碰它） | expo-haptics | T5 |
| `mobile/test/hapticCue.test.ts` | 四种转移各自「进入才振」+ 持续不重复 + 无转移 → null（设置开关的判据在 usePresence 的 effect，不在纯函数） | jest | T5 |
| `mobile/modules/foldstate/expo-module.config.json` | Expo 本地模块声明（照 `modules/kws` 模板） | — | T4 |
| `mobile/modules/foldstate/android/build.gradle` | 原生构建（androidx.window 依赖；⚠ §9.45 的两处门槛：versionCode/versionName 必须有） | androidx.window | T4 |
| `mobile/modules/foldstate/android/src/main/java/com/xiaozhou/foldstate/FoldStateModule.kt` | `FoldingFeature` 事实透传（state/orientation/isSeparating/bounds），callback-adapter 版（零 coroutine 依赖） | Jetpack WindowManager | T4 |
| `mobile/modules/foldstate/index.ts` | JS 面：`requireOptionalNativeModule`，缺席降级（照 `modules/kws/index.ts` 铁则：旧 APK 上不许崩） | expo | T4 |
| `mobile/src/ui/layout/foldPosture.ts` | `foldPosture(fact)`：`tabletop / book / flat` 的派生（**纯函数**；isTableTop = halfOpened × horizontal） | `modules/foldstate` 类型 | T6 |
| `mobile/src/ui/layout/useFoldState.ts` | hook：订阅原生事件 → `FoldFact`；模块缺席恒 flat | `modules/foldstate` | T6 |
| `mobile/test/foldPosture.test.ts` | 姿态四象限 + 缺席 → flat | jest | T6 |
| `mobile/src/app/native-spike.tsx` | **B3 取证屏**（照 M2 `/voice-spike` 先例）：折叠姿态实时读数（posture/state/orientation/bounds/事件计数）+ 触感四按钮（§11.2 B3「四种各触发一次」的装置） | `useFoldState`、`haptics` | T6 |
| `mobile/src/app/blur-spike.tsx` | 材质 spike 屏（§5.11 / Q14）：G1-tint 现状 vs `BlurView` 默认 vs `experimentalBlurMethod` 三块对照 + 高对比滚动内容；**主线组件零依赖它** | expo-blur、`GLASS` | T7 |
| `mobile/plugins/with-assist-role.js` | **只在 T11 的 spike 分支上创建**：AndroidManifest 注入 `VoiceInteractionService` 壳（主线不含此文件） | — | T11 |

### 修改

| 文件 | 改什么 | 为什么 | 任务 |
|---|---|---|---|
| `mobile/src/ui/aurora/AuroraOrb.tsx` | ① `!animated && speaking` 时渲染静态双青环（波纹的「定格」，零动画帧回调）；② `filter` 从根盒挪进 oversize wrapper（`pointerEvents="none"`） | S4 / S6 | T1 T2 |
| `mobile/src/features/chat/ChatScreen.tsx` | `:530` 的内联判据换 `composerOrbAnimated(snapshot)`（行为不变，判据归一） | S4 | T1 |
| `mobile/src/features/chat/Composer.tsx` | `plateGesture` 从「包 TextInput 的父 View」挪到**空输入框时的透明触摸层**（Tap→focus / Hold→PTT，`Gesture.Exclusive` 照 orbGesture 模式）；若 T3 的 A-spike 成功则只加竞争配置 | `plateGesture` | T3 |
| `mobile/src/features/chat/usePresence.ts` | `useEffect` 挂 `hapticCueForTransition`（prev 用 ref；**不在渲染期**） | §8 触感 | T5 |
| `mobile/src/core/settings/store.ts` | `hapticsEnabled: boolean` 默认 true（水合走 `...DEFAULT` 展开，零迁移逻辑） | §8「设置项默认开」 | T5 |
| `mobile/src/features/settings/SettingsScreen.tsx` | 语音分区加「触感」SwitchRow（T5）；调试分区加 `/native-spike`、`/blur-spike` 两行 Link（T6/T7 各自那行） | 同上 / 取证入口 | T5 T6 T7 |
| `mobile/test/settingsMeta.test.ts` | 追加 1 条：水合无 `hapticsEnabled` 的旧 JSON → true（⚠ B2 坑①：既有的 `toEqual(DEFAULT_APP_SETTINGS)` 两条入参是 `null` 与坏 JSON，**够不到合并体**，对新键零敏感——守卫只能是新用例自己） | T5 | T5 |
| `mobile/package.json` | `expo-haptics`、`expo-blur` 两条依赖（`npx expo install` 对齐 SDK 版本，不手写版本号） | T4 | T4 |
| `mobile/e2e/README.md`、`docs/design/README.md`、`AGENTS.md` §4.1（只改指针）、主计划 §9（新坑追加） | 记录 | 收口 | T12 |

**刻意不动**：`mobile/react-native.config.js`（本批三件都走 Expo 通道，不需要补登——除非 §0 第 3 条的 `unimodule.json` 检查撞上意外）；`app.config.ts` 的 plugins（expo-haptics/expo-blur 无 config plugin 需求，VIBRATE 权限由库 manifest 自动 merge；`modules/foldstate` 由 `package.json` 里已有的 `expo.autolinking.nativeModulesDir: "./modules"` 扫到）；`hmi/src/*` 全部；`orbAnimated` 在 G5 里的语义（T1 是加标记不是改判据）。

### 1.1 追溯：方案 / 出账的每条要求指到哪个任务（自检用，写完计划逐行核过）

| 来源 | 要求 | 任务 |
|---|---|---|
| B2 §6.4 出账（泓舟裁定记本批） | S4：语音轮里 Composer 球看不出在播报——与 G5「循环动画 1 个」是同一读数两面，要权衡不是回退 | **T1** |
| B2 §6.4 出账（泓舟裁定记本批） | S6：muted 光球被 `filter` 裁成方框——三种修法任选其一 | **T2** |
| B2 §6.4 出账（泓舟裁定记本批） | `plateGesture`：空输入框长按录音从来不工作——修法在 RNGH 手势竞争配置一类，必须真机复验 | **T3** |
| B2 §6.4 附加④ | 15s 硬上限（安静环境 + 免唤醒关）与「有字长按走原生选择」正反例取证 | T3（步骤 5） |
| 方案 §8 / §11.1 B3 行 | `expo-haptics`（需 prebuild）：唤醒轻、确认双、判死一、快门轻；设置项默认开 | **T4**（装）+ **T5**（判据与接线）+ **T9**（验收） |
| 方案 §7.3 / §11.1 B3 行 | 折叠姿态模块（`FoldingFeature`）——新原生依赖归 B3 一次重建；§9.43 验注册 | **T4**（原生）+ **T6**（JS 面）+ **T9**（手折验收） |
| 方案 §7.6 / §11.1 B3 行 | 「（若需）`react-native-keyboard-controller`」——待证不是待办 | **裁定不装**（§5 第 1 条，依据 B1 T12 + B2 G3 读数） |
| 方案 §5.11 / §13 Q14 / §11.1 B3 行 | `expo-blur` 材质 spike，不通过就停在 G1-tint、不进主线 | **T7**（spike 屏）+ **T9**(裁决) |
| 方案 §11.1 B3 行 / §9 表 B3′ 行 / §13 Q5 / §12.2 | 默认助理角色改独立 spike 分支构建（B3′，不进主线不发版）；只在前台已解锁响应、进语音层后仍需一次手势才开麦 | **T11** |
| 方案 §11.2 B3 ① | 注册验证（原文「`PackageList.java` 含新 Package」；本批按 §0 第 3 条分流成 ExpoModulesProvider + PackageList 既有件不丢） | **T8** |
| 方案 §11.2 B3 ② | haptics 四种触感真机各触发一次 | **T9** |
| 方案 §11.2 B3 ③ | 姿态 hook 在 Fold 4 半开报 `isTableTop=true`（`cmd device_state` 无法模拟半开，要真机手折） | **T9** |
| AGENTS.md M4-R1 / 主计划「上真 AEC」段 | `96a6830` 首次进包 ⇒ `dumpsys` 核 `src client=VOICE_COMMUNICATION`；唤醒率/VAD 端点旧读数一律作废重取 | **T10** |
| B2 §6.4 G2 / 出账⑤ | 回声口径整个换掉：造直灌回声装置，或显式承认不可观测 | **T10**（§5 第 8 条是裁决） |
| B2 §6.4 出账⑥ | AEC 是否覆盖 S2S 采集路径 | T10（dumpsys 终裁） |
| B2 §6.4 验收表 #1/#2/#6/#7 | 免唤醒关的 TapTalk VAD 收尾 / 真人轮 / 顺序取证 / 语音提问出声——APK 换了，随重取段一并 | T10 |
| 方案 §7.4 | 外屏↔内屏接续（正在录音的 PTT 切换瞬间按松手处理） | **归 B4**（布局消费批；B3 只交付姿态事实。§7.4 的「状态全在 store」B1 已成立） |
| 方案 §8 提示音 / §8.1 | 提示音合成（OscillatorNode，零重建）、reduce-motion、TalkBack 播报节流 | **归 B4**（方案 §11.1 就排在 B4） |
| B2 §6.4 出账④⑨⑩⑫ | 发送按钮图标 / `charging_list` 卡型 / `EdgeGlow` jest / 壳底与浅色对比度 | **归 B4**（§0.1 出账去向表有逐条理由） |
| B2 §6.4 出账⑪ | barge-in 路回声无 metric | **hmi 侧另立**（共享文件，不在 mobile 批） |

---
## 2. 任务清单

### Task 1: S4——语音轮里 Composer 球的「播报中」要可读（权衡：静态标记，不回退动画）

**Files:**
- 新建 `mobile/src/core/presence/orbPolicy.ts`、`mobile/test/orbPolicy.test.ts`
- 修改 `mobile/src/ui/aurora/AuroraOrb.tsx`（静态 speaking 标记）、`mobile/src/features/chat/ChatScreen.tsx:530`（内联判据换函数，行为不变）

**为什么**：B2 验收轮泓舟当场指出（编号 S4）：语音轮里输入框旁的光球是「静止版的 speaking」，看不出在播报。根因是 `ChatScreen.tsx:530` 的 `orbAnimated={snapshot.input !== 'voice-sheet'}`——它是为 §11.4「同屏循环动画常态 1 个」写的（层内大球 `VoiceSheet.tsx:168` 接管那「1 个」），G5 的 `0.00%`（层开时 Composer 球两帧逐字节相同）**就是这行代码的达标读数**。⇒ 恢复动画 = 回退 G5；正确的权衡是：**动画仍停，但给静止的球补一个「在播报」的静态视觉标记**——speaking 的识别特征是三青环波纹（`AuroraOrb.tsx:255`，注释原话「与 speaking 的三青环区分：一次 vs 连续」），把波纹「定格」成两圈固定透明度的静态青环（`#46D6E0`，§10.1 ⑤ 波纹用青不用极光），零动画帧回调 ⇒ G5 的逐字节口径不破（静止的环两帧仍逐字节相同）。与 listening 单圈青环的区分：**一圈 vs 两圈**。顺手把 `:530` 的内联判据提成 `core/presence/orbPolicy.ts::composerOrbAnimated`——B4 的 reduce-motion（方案 §8「循环降静帧」）将来落同一函数，「同一个值有几个出口就在入口处判一次」。

- [ ] **步骤 1：写失败测试**

`mobile/test/orbPolicy.test.ts`：

```ts
// mobile/test/orbPolicy.test.ts
// Composer 主球循环动画的唯一判据（B3-1，抽自 ChatScreen.tsx:530 的内联表达式）。
// 层开 = 层内大球接管那「1 个」循环动画（§11.4 性能行），Composer 球静止；
// 静止时 speaking 的可读性由 AuroraOrb 的静态标记负责（本任务另一半，jest 够不到渲染层）。
import { composerOrbAnimated } from '@/core/presence/orbPolicy'

test('语音层开着 → Composer 主球不跑循环动画（G5「同屏循环动画 1 个」的判据）', () => {
  expect(composerOrbAnimated({ input: 'voice-sheet' })).toBe(false)
})

test('层没开 → 主球正常动画（composer / none 两个非层态都动）', () => {
  expect(composerOrbAnimated({ input: 'composer' })).toBe(true)
  expect(composerOrbAnimated({ input: 'none' })).toBe(true)
})
```

（`composerOrbAnimated` 的参数类型写 `Pick<PresenceSnapshot, 'input'>`——测试传最小对象即可；`input` 轴的取值是 `'voice-sheet' | 'composer' | 'none'`，`presence.ts:133`，写计划时已核。）

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/orbPolicy.test.ts`
Expected: FAIL（模块不存在）

- [ ] **步骤 3：实现**

`mobile/src/core/presence/orbPolicy.ts`：

```ts
// Composer 主球要不要跑循环动画——唯一判据（B3-1）。
// 曾内联在 ChatScreen.tsx:530；抽出来是因为它已经有两个利益方（G5 性能 / S4 可读性），
// B4 的 reduce-motion（方案 §8：循环全部降静帧）将来也落这里，不再各写一份。
// ⚠ 它只回答「动不动画」；「静止时 speaking 还读不读得出」是 AuroraOrb 静态标记的事，
//   两个判据刻意分开——合成一个就会回到「要么动（破 G5）要么盲（S4）」的二选一。
import type { PresenceSnapshot } from './presence'

export function composerOrbAnimated(s: Pick<PresenceSnapshot, 'input'>): boolean {
  // 层开着：层内大球（VoiceSheet.tsx:168，animated 恒 true）接管那「1 个」循环动画
  return s.input !== 'voice-sheet'
}
```

`mobile/src/features/chat/ChatScreen.tsx`（`:530` 一行替换，行为逐字节等价；import 加 `composerOrbAnimated`）：

```tsx
        orbAnimated={composerOrbAnimated(snapshot)}
```

`mobile/src/ui/aurora/AuroraOrb.tsx`——在 `:255` 的动画波纹行**之后**插入静态标记（只在 `animated=false` 时渲染，与动画波纹互斥）：

```tsx
      {/* 说话态·静态标记（B3-1 / B2 出账 S4）：animated=false（语音层开着，Composer 球让出
          那「1 个」循环动画名额）时，「在播报」仍要可读——画两圈固定透明度的青环（波纹的
          「定格」）。零动画帧回调 ⇒ G5「层开时主球两帧逐字节相同」的读数不受影响；
          与 listening 单圈青环的区分：一圈 vs 两圈。青 #46D6E0（§10.1 ⑤ 波纹用青不用极光）。 */}
      {!animated && speaking && [1, 2].map((i) => {
        const d = size * (0.66 + i * 0.34)
        return (
          <View
            key={i}
            pointerEvents="none"
            style={{
              position: 'absolute',
              top: (size - d) / 2, left: (size - d) / 2,
              width: d, height: d, borderRadius: 9999,
              borderWidth: 1,
              borderColor: `rgba(70,214,224,${0.55 - i * 0.18})`,
            }}
          />
        )
      })}
```

（d 值取动画波纹同一族：`size*(0.66+i*0.34)` ⇒ i=1 直径 1.0×size、i=2 直径 1.34×size——外圈溢出 44px 球所在的 56dp 容器约 1.5dp/边，与动画波纹 i=3 的溢出同族，真机 overflow visible 一直成立（B1/B2 无被裁记录）。**speaking 态无 filter，不会撞 T2 那个裁剪机制。**）

- [ ] **步骤 4：跑绿 + tsc**

Run: `cd mobile && npx jest test/orbPolicy.test.ts && npm run typecheck`
Expected: 2 passed；0 error。再跑全量 `npm test`：**既有断言零改动**（`orbAnimated` 的传值语义不变，只是判据搬家）。

- [ ] **步骤 5：真机取证（肯定式验收项——B2 的教训：只有否定式验收项的能力等于没验过）**

1. **肯定式主证**：语音轮播报中（轻点光球问一句有语音回答的，层开着、`speaking` 进行时）截图 `b3-01-orb-speaking-static.png` ⇒ **Composer 球带两圈静态青环**。判据不靠眼睛：`png_probe` 采样外圈环带（球心半径 0.5×size 与 0.67×size 处各一点）**G−R 差 > +15**（B1 第 4 批取色法：listening 青环 G−R=37–42，同族信号）。
2. **阴性对照**：同一位置在层收起、idle 态下采样 ⇒ 无青信号（排除「环带常驻」）。
3. **G5 口径复核**：层开播报中隔 ≥1s 连截两帧，Composer 球区域 `png_probe diff` **仍 0.00%**（静态标记没把动画偷偷带回来）——这条是「修 S4 不许回退 G5」的直接判据。
4. 层内大球同两帧 diff > 0%（通道自检：证明 0.00% 不是截图工具冻住了）。

- [ ] **步骤 6：反向验证**（每条先 grep 证明变异落盘，跑完还原复跑全绿）

① `composerOrbAnimated` 恒 return true ⇒ **恰好红**第一条（voice-sheet）；② 恒 return false ⇒ **恰好红**第二条。两条各红各的，两个分支都被独立守住。

- [ ] **提交**

```bash
git add -- mobile/src/core/presence/orbPolicy.ts mobile/test/orbPolicy.test.ts && git commit -m "fix(mobile): UX v2 B3-1 S4 静态 speaking 双青环（动画仍停、G5 口径不破）+ orbAnimated 判据抽成 orbPolicy" -- mobile/src/core/presence/orbPolicy.ts mobile/test/orbPolicy.test.ts mobile/src/ui/aurora/AuroraOrb.tsx mobile/src/features/chat/ChatScreen.tsx && git show --stat HEAD
```

---

### Task 2: S6——muted 光球被 `filter` 裁成方框（修法：oversize filter wrapper）

**Files:**
- 修改 `mobile/src/ui/aurora/AuroraOrb.tsx`（filter 从根盒挪进 oversize wrapper）

**为什么**：B2 验收轮泓舟指出（编号 S6）：离线态光球边缘被截断成方框。机制（B2 §6.4 出账已定位）：`AuroraOrb.tsx:162` 给根 View（size×size 盒）挂 `filter: [{ saturate: 0.4 }]`，RN 在 Android 上会把**子元素裁到容器盒内**；而 `:291` 的离线灰环刻意画在盒外 −10%、`:168` 环境辉光在 −52%——**只有 muted 同时用了「filter + 盒外子元素」两样，所以只有它被裁**。出账给了三种修法：容器留 padding／filter 下移到只包球体的内层／灰环改画盒内。**选第一种的变体（oversize wrapper）**：根 View 内加一层覆盖到 −52% 的大盒承接 filter、内部再造一个 size×size 相对系，**七层子元素代码零改动、非 muted 态零成本**（两层无样式 View）。不选「filter 只包球体」：muted 时环境辉光（−52%）与晕环（−6%）也在渲染、也该去饱和，只包球体会让球灰而辉光仍带蓝紫——修一半；不选「灰环画盒内」：只救灰环救不了辉光，且动了 §10.1 逐条对照过的层几何。

- [ ] **步骤 1：取「红」读数（渲染层缺陷，jest 够不到——`testMatch` 不含 `.tsx`、全仓无组件渲染测试，B2 §6.4 出账原话；本任务的红→绿是真机 png_probe 对照）**

修前基线：`/state-gallery` 的 muted 样本截图 `b3-02-muted-before.png`（B2 验收轮的 S6 图就是这个形态）；`png_probe` 采样光球盒外 −10% 灰环应在的位置 ⇒ **亮像素 ≈ 0（被裁掉了）**——这就是「红」。

- [ ] **步骤 2：实现**

`mobile/src/ui/aurora/AuroraOrb.tsx` 的 return 块改成（根 View 上**删掉** `...(muted ? { filter: … } : {})` 那行；`{/* …原七层与各环原样搬入，一个字符不改… */}` 处为既有全部子元素）：

```tsx
  return (
    <View
      style={{
        width: size,
        height: size,
        opacity: dim ? 0.6 : 1,
      }}
      accessibilityLabel={ORB_A11Y[state]}
      accessibilityRole="image"
    >
      {/* B3-2（B2 出账 S6）：filter 挂在覆盖到最远子层（环境辉光 −52%）的 oversize 盒上，
          不挂根盒——RN Android 的 filter 会把子元素裁到容器盒内，而灰环（−10%）与辉光
          （−52%）刻意画在盒外，挂根盒会把 muted 裁成方框。内层再造一个 size×size 相对系，
          七层子元素零改动。非 muted 时这两层是无样式 View，零成本。pointerEvents=none：
          纯视觉层，球自己从不接触摸（手势在 Composer 的容器上）。 */}
      <View
        pointerEvents="none"
        style={{
          position: 'absolute',
          top: -size * 0.52, left: -size * 0.52,
          width: size * 2.04, height: size * 2.04,
          ...(muted ? { filter: [{ saturate: 0.4 }] } : {}),
        }}
      >
        <View style={{ position: 'absolute', top: size * 0.52, left: size * 0.52, width: size, height: size }}>
          {/* …原七层与各环原样搬入，一个字符不改… */}
        </View>
      </View>
    </View>
  )
```

⚠ 两个必须核的边界：① 环境辉光 `top: -size*0.52` 恰好顶到 wrapper 边缘 0——它的 radial 在 70% 处已透明，贴边无可见裁切；若真机放大后四角仍见硬边，把 wrapper 再放大到 −0.6（同步改内层偏移），**别回去动七层**。② `pointerEvents="none"` 挂 wrapper 足够（none 级联子树）；根 View 不加——a11y label 还在根上。

- [ ] **步骤 3：全量回归**

Run: `cd mobile && npm test && npm run typecheck`
Expected: 全绿 0 error（本任务零 jest 增量——理由见步骤 1；**条数不许减**）。

- [ ] **步骤 4：真机取证（肯定式）**

1. **肯定式主证**：`/state-gallery` muted 样本 `b3-02-muted-after.png` ⇒ 灰环完整一圈可见。`png_probe` 同一盒外位置（步骤 1 的采样点）**亮像素 > 0**，与修前 ≈0 成对照——红→绿闭环。
2. **四角圆润**：光球四角（盒角内 2px）无饱和色硬边（修前是内容被硬裁出的直角）。
3. **回归面**：画廊 13 态深浅各一套截图与 B1 T14 基线肉眼比对——**wrapper 对非 muted 态必须零可见变化**（重点看 speaking 波纹、listening 环、attention 琥珀环这些盒外元素还在不在原位）；Maestro 09 流复跑 rc=0（离线冒烟：画廊渲得出、不崩）。
4. 对话屏真机开飞行模式 ⇒ muted 球同样完整——**取证屏绿证明不了生产绿**（B1 第 2 批的账），生产消费方也要看一眼。

- [ ] **步骤 5：反向验证**

把 filter 临时挪回根 View ⇒ muted 画廊**复现方框**（盒外灰环亮像素回到 ≈0）；还原后复跑步骤 4.1 恢复。——证明「修好」确实是 wrapper 的功劳。

- [ ] **提交**

```bash
git commit -m "fix(mobile): UX v2 B3-2 S6 muted 光球 filter 挪进 oversize wrapper，盒外灰环/辉光不再被裁成方框" -- mobile/src/ui/aurora/AuroraOrb.tsx && git show --stat HEAD
```

---

### Task 3: `plateGesture`——空输入框长按录音从来不工作（A-spike RNGH 竞争配置 → B 透明触摸层）

**Files:**
- 修改 `mobile/src/features/chat/Composer.tsx`

**为什么**：B2 验收轮实测（泓舟裁定记本批）：空输入框长按 4s，层不升、PTT 不启动，屏上出现文本光标水滴柄——**「空输入框可长按录音」这条计划声称已有的能力从来不工作**。机制：`Composer.tsx:91` 的 `plateGesture` 挂在 `:136-138` 包着 TextInput 的父 View 上，Android 的 TextInput 自己消费触摸（长按=光标/选择），RNGH 抢不到；同款 `makeHold` 在光球（普通 View）上一直好用，差异就在 native child。B2 出账指的修法方向是「RNGH 手势竞争配置（`blocksExternalGesture` 一类）」——先按这个方向 spike（A）；A 是在 RNGH 与 EditText 的原生竞争里调参数，行为只活在原生层、**每个候选都必须真机验**；30 分钟盒内不成就走 B：**空输入框时铺透明触摸层**（轻点→`focus()` 弹键盘、长按→PTT），把「与原生 TextInput 抢触摸」这个问题整个绕开——覆盖层是普通 View，与光球已验证可用的手势形态完全同款，确定性最高。有字时覆盖层卸载、原生长按选择完整回归。⚠ 本任务 jest 增量 0：手势竞争「单测和 tsc 都看不见」（B2 §6.4 出账原话），红→绿是真机操作对照（红 = B2 验收轮那条实录，已在案）。

- [ ] **步骤 1：A-spike（30 分钟盒，Metro 热载逐个真机试；每个候选试完记结果，成了就停）**

| # | 候选配置 | 原理假设 |
|---|---|---|
| A1 | `plateGesture` 加 `.blocksExternalGesture()` 指向包 TextInput 的 native view ref | hold 判定期间阻断 EditText 接手 |
| A2 | TextInput 外包 `GestureDetector` 用 `Gesture.Native()`，与 hold 组成 `Gesture.Exclusive(hold, native)` | Exclusive 让 native 等 hold 失败才生效：快松（<300ms）hold 失败→native 聚焦；按满 300ms hold 激活→EditText 收 CANCEL |
| A3 | hold 加 `.disallowInterruption(true)`（Android 专属：不许 native child 打断 handler） | 阻止 EditText 把 hold 掐掉 |

每个候选的判据同一条：**空输入框长按 4s → PTT 启动**（层升 + 转写区出现）**且轻点仍能聚焦弹键盘**——两半都过才算过（B2 坑⑫的镜像：只验长按不验轻点，回归就躲在轻点里）。任一候选两半都过 ⇒ 用它（改动一两行），步骤 2 跳过；全败 ⇒ 步骤 2。**spike 结果无论走哪条都写进 §6.1**（哪个配置、两半的读数）。

- [ ] **步骤 2：B 方案实现（A 全败时）**

`mobile/src/features/chat/Composer.tsx`——`plateGesture` 那两处改成：

```tsx
  // 空输入框的「背板即录音键」（B3-3 / B2 出账 plateGesture）：不再把手势挂在包 TextInput 的
  // 父 View 上——Android 的 TextInput 自己消费触摸（长按=光标/选择），RNGH 抢不到（B2 真机
  // 实录：长按 4s 出的是光标水滴柄）。改为空输入框时铺一层透明触摸层：轻点→聚焦弹键盘
  // （原生 focus 语义由我们转发），长按→PTT（与光球同款、已验证可用的手势形态）。
  // 有字时该层卸载，原生长按选择完整回归。a11y：触摸层不进无障碍树（TalkBack 的双击激活
  // 走 a11y action 直达 TextInput，不经普通触摸；长按录音对 TalkBack 本来就不是唯一入口，§8.1）。
  const inputRef = useRef<TextInput>(null)
  const plateTap = Gesture.Tap()
    .runOnJS(true)
    .maxDuration(HOLD_MS - 20)
    .onEnd(() => inputRef.current?.focus())
  const plateGesture = Gesture.Exclusive(makeHold(!!ptt && !finalizing), plateTap)
  const plateOverlayOn = !!ptt && !finalizing && input.length === 0
```

JSX（原来包 TextInput 的 `<GestureDetector gesture={plateGesture}>` 拆掉，TextInput 加 `ref={inputRef}`，其余 props 原样）：

```tsx
        <View style={{ flex: 1 }}>
          <TextInput
            ref={inputRef}
            testID="composer-input"
            … 其余 props 一字不改（style/value/onChangeText/placeholder/multiline/…）…
          />
          {plateOverlayOn ? (
            <GestureDetector gesture={plateGesture}>
              <View
                testID="composer-plate-overlay"
                accessible={false}
                importantForAccessibility="no"
                style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
              />
            </GestureDetector>
          ) : null}
        </View>
```

（`makeHold` / `HOLD_MS` / 上滑取消逻辑一字不动——hold 的 onUpdate 取消路径在覆盖层上同样生效。）

- [ ] **步骤 3：全量回归**

Run: `cd mobile && npm test && npm run typecheck`
Expected: 全绿 0 error（零 jest 增量，条数不许减）。

- [ ] **步骤 4：真机取证（肯定式为主、反例为辅——这条缺陷的教训就是「只有否定式验收项的能力等于没验过」）**

1. **肯定式主证（这一格 B2 从来没绿过）**：空输入框长按 4s ⇒ **层升起、录音中**（截图 `b3-03-plate-hold.png`：层壳 + 实时转写大字 + 胶囊「在听…」；轨迹页同刻有 `listening` 条目为第二证据）。
2. **轻点回归**：轻点空输入框 ⇒ 键盘弹起、可打字（`b3-03-plate-tap-kbd.png`）——修好长按不许弄坏聚焦。
3. **反例仍过**：`adb shell input text a` 后长按输入框区域 ⇒ 不触发 PTT（光球无青环）；长按光球 ⇒ PTT 仍可（B2 附加④b 上半的复验）。
4. **上滑取消**：空输入框长按 1s 后上滑 ≥60dp 松手 ⇒ 不发送、层收起（三段式 motionevent 注入或真人手指，B2 附加③b 配方）。
5. **B2 附加④a 补账——15s 硬上限**：免唤醒**关**（走 TapTalk 路径）+ **安静环境**（B2 撞过的前提：环境人声会让 VAD 提前收尾，读数两可）：轻点光球不说话 ⇒ **15s 自动收尾**（连拍或录屏，收尾时刻−开始时刻 ≈15s）。这格 B2 两批都没取到，取到才销账；环境不安静就写 ⬜ 与原因，**不写「预期」**。
6. Maestro 05 流复跑 rc=0（PTT 主路径回归；dist 路径见 §0 第 4 条）。

- [ ] **步骤 5：反向验证**

B 方案：`plateOverlayOn` 临时恒 false ⇒ 步骤 4.1 复现 B2 实录形态（长按出光标水滴柄、层不升）；还原复验恢复。A 方案：去掉那个竞争配置 ⇒ 同样复现。——真机对照就是本任务的变异测试。

- [ ] **提交**

```bash
git commit -m "fix(mobile): UX v2 B3-3 plateGesture 空输入框长按录音真机首次可用（A-spike 或透明触摸层，正反例+上滑取消+15s 上限四格取证）" -- mobile/src/features/chat/Composer.tsx && git show --stat HEAD
```

---
### Task 4: 新原生依赖一次落盘（expo-haptics + expo-blur + modules/foldstate 原生半）

**Files:**
- 修改 `mobile/package.json`（`npx expo install` 产出的两行）
- 新建 `mobile/modules/foldstate/expo-module.config.json`、`mobile/modules/foldstate/android/build.gradle`、`mobile/modules/foldstate/android/src/main/java/com/xiaozhou/foldstate/FoldStateModule.kt`、`mobile/modules/foldstate/index.ts`

**为什么**：方案 §11.1 的排期理由——「B3 把新原生依赖压成一次 22–38 分钟的构建（M3-B 教训：分两次不值当）」。本任务把三件原生**全部落盘**（npm 两件 + 本地模块一件），T5–T7 在其上写 JS（jest 在重建前就能绿——npm 包的 JS/类型装完即在），T8 才构建。折叠模块**自写**而不用 `@logicwind/react-native-fold-detection`（方案 §7.3 给的两个选项之一）：理由三条——① B4 的 book 布局要 `foldBounds` 精确值（§7.3 `gap 取 foldBounds.width + 16`），三方 hook 给不给 bounds 未知，自家模块透传全量事实；② `modules/kws` 是本仓验证过的模板，§9.45 的两处门槛（`nativeModulesDir` 已配、versionCode/versionName）都踩过了；③ 少一个可能掉进 autolinking 缝的三方件（§9.43 一趟 38 分钟的学费）。模块**只透传不判定**（照 `modules/kws/index.ts` 的头注纪律）：`isTableTop/isBook` 的派生住 `ui/layout/foldPosture.ts`（T6，纯函数 jest）。

- [ ] **步骤 1：装 npm 两件（版本由 expo 对齐 SDK，不手写）**

```bash
cd mobile && npx expo install expo-haptics expo-blur
git diff -- package.json   # 应只有两行新增；多了任何东西都停下来看
```

- [ ] **步骤 2：§0 第 3 条的注册通道检查（30 秒，省一趟 30 分钟构建）**

```bash
ls node_modules/expo-haptics/expo-module.config.json node_modules/expo-blur/expo-module.config.json   # 应存在（Expo 通道）
ls node_modules/expo-haptics/unimodule.json node_modules/expo-blur/unimodule.json 2>/dev/null          # 应 No such file
```

若哪件真带了 `unimodule.json` ⇒ 按坑账 §9.43 在 `mobile/react-native.config.js` 补登再进 T8，并把这个意外写进 §6.2。

- [ ] **步骤 3：foldstate 模块四件**

`mobile/modules/foldstate/expo-module.config.json`：

```json
{
  "platforms": ["android"],
  "android": {
    "modules": ["com.xiaozhou.foldstate.FoldStateModule"]
  }
}
```

`mobile/modules/foldstate/android/build.gradle`（结构照 `modules/kws/android/build.gradle`——本仓唯一验证过的模板；无资产断言，依赖换 androidx.window）：

```gradle
// 折叠姿态原生桥（B3 / 方案 §7.3）。只做一件事：把 Jetpack WindowManager 的
// FoldingFeature 事实（state/orientation/isSeparating/bounds）透传成 Expo 事件。
// 不判定 tabletop/book——派生住 src/ui/layout/foldPosture.ts（纯函数，jest 钉住）。
plugins {
  id 'com.android.library'
  id 'expo-module-gradle-plugin'
}

group = 'com.xiaozhou.foldstate'
version = '0.1.0'

android {
  namespace "com.xiaozhou.foldstate"
  // ⚠ 这两行不是样板（坑账 §9.45②）：缺 versionName 时报错在 :expo 头上，
  //    看起来像 expo 自己坏了。kws 首次构建就撞过。
  defaultConfig {
    versionCode 1
    versionName '0.1.0'
  }
}

dependencies {
  // window-java 提供 WindowInfoTrackerCallbackAdapter（回调式适配层）——
  // 刻意不走 Kotlin Flow：collect 要协程作用域，回调式零额外依赖、生命周期一目了然
  implementation "androidx.window:window:1.3.0"
  implementation "androidx.window:window-java:1.3.0"
}
```

`mobile/modules/foldstate/android/src/main/java/com/xiaozhou/foldstate/FoldStateModule.kt`：

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

// 折叠姿态事实透传（B3 / 方案 §7.3）。注册监听即回推当前值（WindowManager 的行为），
// 所以 JS 侧不需要「查询一次当前值」的方法——订阅就是查询。
class FoldStateModule : Module() {
  private var adapter: WindowInfoTrackerCallbackAdapter? = null

  private val consumer = Consumer<WindowLayoutInfo> { info ->
    val fold = info.displayFeatures.filterIsInstance<FoldingFeature>().firstOrNull()
    sendEvent(
      "onFoldChange",
      mapOf(
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
      ),
    )
  }

  override fun definition() = ModuleDefinition {
    Name("FoldState")
    Events("onFoldChange")

    OnStartObserving {
      val activity = appContext.currentActivity ?: return@OnStartObserving
      val a = WindowInfoTrackerCallbackAdapter(WindowInfoTracker.getOrCreate(activity))
      adapter = a
      a.addWindowLayoutInfoListener(activity, ContextCompat.getMainExecutor(activity), consumer)
    }
    OnStopObserving {
      adapter?.removeWindowLayoutInfoListener(consumer)
      adapter = null
    }
  }
}
```

`mobile/modules/foldstate/index.ts`（照 `modules/kws/index.ts` 的铁则：旧 APK 上原生缺席**不许崩**，`requireOptionalNativeModule` 返 null、消费方降级 flat）：

```ts
// 折叠姿态原生模块的 JS 面（B3）。**只透传，不判定**——tabletop/book 的派生在
// src/ui/layout/foldPosture.ts（纯函数）。
//
// ⚠ 原生缺席时 requireOptionalNativeModule 返回 null 而不是抛（坑账 §9.27 铁则）：
//    JS 已经引了、设备上是旧 APK（B3 重建之前的）时不许崩，降级成「永远 flat」。
import { requireOptionalNativeModule } from 'expo'

export interface FoldBounds {
  left: number
  top: number
  right: number
  bottom: number
}

export interface FoldEvent {
  present: boolean
  state: 'halfOpened' | 'flat' | 'none'
  orientation: 'horizontal' | 'vertical' | 'none'
  isSeparating: boolean
  bounds: FoldBounds | null
}

interface FoldStateNativeModule {
  addListener(event: 'onFoldChange', cb: (e: FoldEvent) => void): { remove(): void }
}

const native = requireOptionalNativeModule<FoldStateNativeModule>('FoldState')

/** 原生模块在不在场。false = 这个 APK 没带折叠姿态，消费方按 flat 降级（方案 §11.1 B4 行）。 */
export const FOLD_NATIVE_AVAILABLE = native != null

export default native
```

- [ ] **步骤 4：tsc（jest 无新用例——本任务是落盘，判据在 T6 的纯函数与 T8 的注册验证）**

Run: `cd mobile && npm run typecheck && npm test`
Expected: 0 error、全量仍绿（`modules/foldstate/index.ts` 没有消费方，tsc 只核它自身合法）。

- [ ] **提交**

```bash
git add -- mobile/package.json mobile/package-lock.json mobile/modules/foldstate && git commit -m "feat(mobile): UX v2 B3-4 原生依赖一次落盘——expo-haptics + expo-blur + 自写 modules/foldstate（FoldingFeature 事实透传，判定留给纯函数）" -- mobile/package.json mobile/package-lock.json mobile/modules/foldstate && git show --stat HEAD
```

---

### Task 5: 触感 JS 面——判据纯函数 + 执行薄壳 + 设置开关 + usePresence 接线

**Files:**
- 新建 `mobile/src/core/presence/hapticCue.ts`、`mobile/src/core/haptics.ts`、`mobile/test/hapticCue.test.ts`
- 修改 `mobile/src/core/settings/store.ts`（`hapticsEnabled`）、`mobile/src/features/settings/SettingsScreen.tsx`（开关行）、`mobile/src/features/chat/usePresence.ts`（useEffect 接线）、`mobile/test/settingsMeta.test.ts`（+1 条）

**为什么**：方案 §8——四种触感：**唤醒轻、确认双、判死一、快门轻**；设置项默认开。四个时机全是 `PresenceSnapshot` 的转移（listening / attention / looking 进入 + 判死降级出现）⇒ 判据是一份纯函数（`hapticCueForTransition`），不在四个产生处各挂一枪——「同一个值有几个出口就在入口处判一次」。执行层（expo-haptics 调用）单独薄壳：`hapticCue.ts` **零 RN/expo import**（jest 直接跑，类型 `HapticKind` 定义在判据层、执行层反向 `import type`——依赖方向是执行层依赖判据层）。接线挂 `usePresence` 的 **useEffect**：`presenceTrail.record`（`:172`）能住渲染期是因为它幂等；触感不幂等，StrictMode 双渲会双振，B2 T14 的红条（渲染期同步副作用）就是同一族——**不挂渲染期**。

- [ ] **步骤 1：写失败测试**

`mobile/test/hapticCue.test.ts`：

```ts
// mobile/test/hapticCue.test.ts
// 四种触感对应哪些 Snapshot 转移（方案 §8：唤醒轻/确认双/判死一/快门轻）——B3-5。
// 「进入」才振（持续态不振、离开不振）；判死看 degradation 出现 kind=recoverable_error|fatal。
import type { PresenceSnapshot } from '@/core/presence/presence'
import { hapticCueForTransition } from '@/core/presence/hapticCue'

type Slice = Pick<PresenceSnapshot, 'primary' | 'degradation'>
const s = (primary: PresenceSnapshot['primary'], degradation: PresenceSnapshot['degradation'] = []): Slice => ({
  primary,
  degradation,
})

test('唤醒轻：进入 listening 振一次；持续 listening 不再振', () => {
  expect(hapticCueForTransition(s('armed'), s('listening'))).toBe('wake')
  expect(hapticCueForTransition(s('listening'), s('listening'))).toBeNull()
})

test('确认双：进入 attention（危险动作等你确认）', () => {
  expect(hapticCueForTransition(s('idle'), s('attention'))).toBe('confirm')
})

test('快门轻：进入 looking（视觉抓帧那一下）', () => {
  expect(hapticCueForTransition(s('idle'), s('looking'))).toBe('shutter')
})

test('判死一：recoverable_error / fatal 降级从无到有；已有时再渲染不重复振', () => {
  const dead = s('idle', [{ kind: 'recoverable_error', text: '响应超时了', at: 1 }])
  expect(hapticCueForTransition(s('idle'), dead)).toBe('dead')
  expect(hapticCueForTransition(dead, dead)).toBeNull()
})

test('无转移 → null（armed 呼吸、thinking 进行中都不振）', () => {
  expect(hapticCueForTransition(s('armed'), s('armed'))).toBeNull()
  expect(hapticCueForTransition(s('armed'), s('thinking'))).toBeNull()
})
```

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/hapticCue.test.ts`
Expected: FAIL（模块不存在）

- [ ] **步骤 3：实现**

`mobile/src/core/presence/hapticCue.ts`：

```ts
// 四种触感的转移判据（B3-5 / 方案 §8）——纯函数、零 RN/expo import。
// 执行层在 core/haptics.ts（它 import type 本文件的 HapticKind——执行层依赖判据层，不反过来）；
// 接线在 usePresence 的 useEffect（不挂渲染期：触感不幂等，StrictMode 双渲会双振，
// 与 B2 T14「渲染期同步 notify」同族）。
import type { PresenceSnapshot } from './presence'

export type HapticKind = 'wake' | 'confirm' | 'dead' | 'shutter'

type Slice = Pick<PresenceSnapshot, 'primary' | 'degradation'>

const isDead = (x: Slice) =>
  x.degradation.some((d) => d.kind === 'recoverable_error' || d.kind === 'fatal')

export function hapticCueForTransition(prev: Slice, next: Slice): HapticKind | null {
  // primary 的三个「进入」优先于判死（同帧撞上时，态的转移比降级的出现更「此刻」）
  if (next.primary === 'listening' && prev.primary !== 'listening') return 'wake'
  if (next.primary === 'attention' && prev.primary !== 'attention') return 'confirm'
  if (next.primary === 'looking' && prev.primary !== 'looking') return 'shutter'
  if (isDead(next) && !isDead(prev)) return 'dead'
  return null
}
```

`mobile/src/core/haptics.ts`：

```ts
// 触感执行层（B3-5）：只把 HapticKind 翻成 expo-haptics 调用。判据不在这里
// （core/presence/hapticCue.ts）。fire-and-forget + 静默失败：触感缺席不值得打断任何主流程；
// 原生缺席（旧 APK）时 expo-haptics 的调用会 reject，同样吞掉——坑账 §9.27 铁则的触感版。
import * as Haptics from 'expo-haptics'

import type { HapticKind } from './presence/hapticCue'

export const HAPTIC_KINDS: readonly HapticKind[] = ['wake', 'confirm', 'dead', 'shutter'] as const

/** 方案 §8 的四张脸：唤醒轻 / 确认双 / 判死一 / 快门轻 */
export function performHaptic(kind: HapticKind): void {
  const p =
    kind === 'wake'
      ? Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
      : kind === 'confirm'
        ? Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)
        : kind === 'dead'
          ? Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy)
          : Haptics.selectionAsync()
  p.catch(() => {})
}
```

（⚠ 「确认双」用 `notificationAsync(Warning)`——Android 上是模式振动、有双段感；**T9 真机若感知不出「双」**，改成两次 `impactAsync(Medium)` 间隔 120ms，以真机手感为准、裁决写进 §6.3。）

`mobile/src/core/settings/store.ts`：`AppSettings` 加 `hapticsEnabled: boolean`；`DEFAULT_APP_SETTINGS` 加 `hapticsEnabled: true`（§8「设置项默认开」）。水合走既有 `...DEFAULT_APP_SETTINGS` 展开，旧库缺键自动补 true，**零迁移逻辑**。

`mobile/test/settingsMeta.test.ts` 追加 1 条（⚠ B2 坑①：既有 `toEqual(DEFAULT_APP_SETTINGS)` 两条的入参是 `null` 与坏 JSON，走提前返回与 catch，**够不到合并体**、对新键零敏感——守卫只能是新用例自己传合法 JSON）：

```ts
test('B3-5：旧库没有 hapticsEnabled → 水合补默认 true（触感默认开，§8）', () => {
  expect(hydrate(JSON.stringify({ ttsEnabled: true })).hapticsEnabled).toBe(true)
})
```

（`hydrate` 为该测试文件里既有的水合入口名——**以文件里实际的函数名为准**，追加进同一 describe，不新开装置。）

`mobile/src/features/settings/SettingsScreen.tsx`——语音分区（播报三档那段之后）加：

```tsx
        <SwitchRow
          p={p}
          label="触感"
          desc="唤醒、需要确认、出错、拍一张时轻微振动。默认开"
          value={settings.hapticsEnabled}
          onChange={(hapticsEnabled) => set({ hapticsEnabled })}
        />
```

`mobile/src/features/chat/usePresence.ts`——`derivePresence` 调用之后、return 之前**不动**（渲染期只有幂等的 `presenceTrail.record`）；在 hook 体里加 effect（import `useEffect`/`useRef` 已在或补上；`settings` 在作用域内，grep 过 `settings.voicePipeline` 同函数可用）：

```tsx
  // B3-5 触感：判据纯函数、执行挂 effect——不挂渲染期（触感不幂等，StrictMode 双渲会双振；
  // B2 T14 的红条是同一族）。prev 用 ref，首帧不振（prev=null）。
  const hapticPrev = useRef<PresenceSnapshot | null>(null)
  useEffect(() => {
    const prev = hapticPrev.current
    hapticPrev.current = snapshot
    if (!prev || !settings.hapticsEnabled) return
    const kind = hapticCueForTransition(prev, snapshot)
    if (kind) performHaptic(kind)
  })
```

（⚠ `usePresence` 的实际结构以文件为准：若 snapshot 是在函数末 return 的局部值，effect 就放 return 之前、闭包引用它——effect 无依赖数组=每次渲染后跑，转移判据自身保证「没变就 null」。）

- [ ] **步骤 4：跑绿 + tsc + 全量**

Run: `cd mobile && npx jest test/hapticCue.test.ts test/settingsMeta.test.ts && npm run typecheck && npm test`
Expected: 新增 6 条全绿（hapticCue 5 + settingsMeta 1）；0 error；全量只增不减。**看门狗三条（`sessionStore.test.ts:529/545/560`）必须仍绿**——本任务没动 store，但全量是收口判据。

- [ ] **步骤 5：反向验证**（每条先 grep 证明变异落盘，跑完还原复跑全绿）

① `hapticCueForTransition` 里 `wake` 分支去掉 `prev.primary !== 'listening'` 守卫（恒进入）⇒ **恰好红**「持续 listening 不再振」那半条；② `isDead` 改成只认 `fatal` ⇒ **恰好红**判死那条的前半（`recoverable_error` 进不来）；③ 四个 return 全换 null ⇒ 红 4 条、「无转移」那条仍绿（阴性用例不许跟着红——它红了说明测试写反）。

- [ ] **提交**

```bash
git add -- mobile/src/core/presence/hapticCue.ts mobile/src/core/haptics.ts mobile/test/hapticCue.test.ts && git commit -m "feat(mobile): UX v2 B3-5 触感四种——转移判据纯函数 + expo-haptics 薄壳 + 设置开关默认开 + usePresence effect 接线（不挂渲染期）" -- mobile/src/core/presence/hapticCue.ts mobile/src/core/haptics.ts mobile/test/hapticCue.test.ts mobile/src/core/settings/store.ts mobile/src/features/settings/SettingsScreen.tsx mobile/src/features/chat/usePresence.ts mobile/test/settingsMeta.test.ts && git show --stat HEAD
```

---

### Task 6: 折叠姿态 JS 面——posture 纯函数 + hook + B3 取证屏

**Files:**
- 新建 `mobile/src/ui/layout/foldPosture.ts`、`mobile/src/ui/layout/useFoldState.ts`、`mobile/test/foldPosture.test.ts`、`mobile/src/app/native-spike.tsx`
- 修改 `mobile/src/features/settings/SettingsScreen.tsx`（调试分区 +1 行 Link）

**为什么**：方案 §7.3——tabletop（半开、铰链水平）/ book（半开、铰链垂直）/ flat 三姿态；B4 拿它做布局，B3 只交付**事实与派生**。§11.2 B3 的验收「姿态 hook 在 Fold 4 半开时报 `isTableTop=true`（`cmd device_state` 无法模拟半开，要真机手折）」需要一块能实时读 hook 值的屏——**B3 取证屏 `/native-spike`**（照 M2 `/voice-spike` 先例），同屏放触感四按钮（§11.2「四种各触发一次」的装置——四种的自然转移里「判死」要断网等 watchdog、成本高，按钮直接调 `performHaptic` 逐种验振感；自然挂点另验两条代表，见 T9）。

- [ ] **步骤 1：写失败测试**

`mobile/test/foldPosture.test.ts`：

```ts
// mobile/test/foldPosture.test.ts
// 折叠姿态派生（方案 §7.3）：tabletop=半开×铰链水平（上下两半），book=半开×铰链垂直（左右双栏），
// 其余一律 flat（含原生缺席、全开、无折叠特征——B4 的布局降级路径就吃这个 flat）。
import { foldPosture } from '@/ui/layout/foldPosture'

const e = (state: 'halfOpened' | 'flat' | 'none', orientation: 'horizontal' | 'vertical' | 'none') => ({
  present: state !== 'none',
  state,
  orientation,
})

test('tabletop：半开 + 铰链水平', () => {
  expect(foldPosture(e('halfOpened', 'horizontal'))).toBe('tabletop')
})

test('book：半开 + 铰链垂直', () => {
  expect(foldPosture(e('halfOpened', 'vertical'))).toBe('book')
})

test('全开（FLAT）→ flat；无折叠特征 → flat', () => {
  expect(foldPosture(e('flat', 'horizontal'))).toBe('flat')
  expect(foldPosture(e('none', 'none'))).toBe('flat')
})

test('原生缺席（旧 APK，事件永远不来）→ flat', () => {
  expect(foldPosture(null)).toBe('flat')
})
```

- [ ] **步骤 2：跑红**

Run: `cd mobile && npx jest test/foldPosture.test.ts`
Expected: FAIL（模块不存在）

- [ ] **步骤 3：实现**

`mobile/src/ui/layout/foldPosture.ts`：

```ts
// 折叠姿态派生（B3-6 / 方案 §7.3）——纯函数。模块只透传事实（modules/foldstate），
// 「哪种姿态」在这里判一次；B4 的布局（tabletop 上下两半 / book 双栏 gap）只读它。
import type { FoldEvent } from '../../../modules/foldstate'

export type FoldPosture = 'tabletop' | 'book' | 'flat'

export function foldPosture(e: Pick<FoldEvent, 'present' | 'state' | 'orientation'> | null): FoldPosture {
  if (!e || !e.present || e.state !== 'halfOpened') return 'flat'
  return e.orientation === 'horizontal' ? 'tabletop' : 'book'
}
```

`mobile/src/ui/layout/useFoldState.ts`：

```ts
// 折叠姿态 hook（B3-6）：订阅原生事件流，返回最新 FoldEvent（null=尚无事件或原生缺席）。
// WindowManager 在注册监听时会立即回推当前值，所以「订阅即查询」，不需要 get 方法。
import { useEffect, useState } from 'react'

import FoldNative, { type FoldEvent } from '../../../modules/foldstate'

export function useFoldState(): FoldEvent | null {
  const [fold, setFold] = useState<FoldEvent | null>(null)
  useEffect(() => {
    if (!FoldNative) return
    const sub = FoldNative.addListener('onFoldChange', setFold)
    return () => sub.remove()
  }, [])
  return fold
}
```

`mobile/src/app/native-spike.tsx`（expo-router 文件路由；深链 `xiaozhou://native-spike`）：

```tsx
// B3 原生件取证屏（照 M2 /voice-spike 先例）。§11.2 B3 的两条验收在这里读：
//  ① 折叠姿态：Fold 4 真机手折半开，「posture」行要变 tabletop（铰链横）——
//     cmd device_state 模拟不了半开（B2 §6.4 的 device_state 口径只有 0/1/2/3 档），要人手。
//  ② 触感四种：四个按钮各触发一次（§8：唤醒轻/确认双/判死一/快门轻）；
//     自然挂点的代表性验证另有两条（计划 T9——按钮验的是「振感对不对」，挂点验的是「时机对不对」）。
import { useEffect, useState } from 'react'
import { Pressable, ScrollView, Text } from 'react-native'

import FoldNative, { FOLD_NATIVE_AVAILABLE } from '../../modules/foldstate'
import { HAPTIC_KINDS, performHaptic } from '@/core/haptics'
import { foldPosture } from '@/ui/layout/foldPosture'
import { useFoldState } from '@/ui/layout/useFoldState'
import { usePalette } from '@/ui/theme'

export default function NativeSpikeScreen() {
  const p = usePalette()
  const fold = useFoldState()
  const [events, setEvents] = useState(0)
  useEffect(() => {
    if (!FoldNative) return
    const sub = FoldNative.addListener('onFoldChange', () => setEvents((n) => n + 1))
    return () => sub.remove()
  }, [])

  const rows: Array<[string, string]> = [
    ['native', FOLD_NATIVE_AVAILABLE ? 'available' : 'MISSING（旧 APK？重建没带上？）'],
    ['posture', foldPosture(fold)],
    ['state', fold?.state ?? '—'],
    ['orientation', fold?.orientation ?? '—'],
    ['isSeparating', String(fold?.isSeparating ?? '—')],
    ['bounds', fold?.bounds ? JSON.stringify(fold.bounds) : '—'],
    ['events', String(events)],
  ]

  return (
    <ScrollView style={{ flex: 1, backgroundColor: p.bg }} contentContainerStyle={{ padding: 16, gap: 8 }}>
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '600' }}>B3 原生件取证</Text>
      {rows.map(([k, v]) => (
        <Text key={k} testID={`fold-${k}`} style={{ color: p.fg2, fontSize: p.font(14) }}>
          {k}: {v}
        </Text>
      ))}
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '600', marginTop: 16 }}>触感四种（§8）</Text>
      {HAPTIC_KINDS.map((kind) => (
        <Pressable
          key={kind}
          testID={`haptic-${kind}`}
          onPress={() => performHaptic(kind)}
          style={{ backgroundColor: p.fill, borderRadius: 12, padding: 12 }}
        >
          <Text style={{ color: p.fg1, fontSize: p.font(14) }}>
            {kind}（{kind === 'wake' ? '唤醒·轻' : kind === 'confirm' ? '确认·双' : kind === 'dead' ? '判死·一记重' : '快门·轻'}）
          </Text>
        </Pressable>
      ))}
    </ScrollView>
  )
}
```

（⚠ `usePalette` 的引法照 `presence-trail.tsx` / `state-gallery` 既有调试屏抄——主题接法以仓库现状为准，别发明第二种。）

`mobile/src/features/settings/SettingsScreen.tsx`——「调试」分区（`:484` Section）追加：

```tsx
        <Link href="/native-spike" style={{ color: p.accent, fontSize: p.font(14) }}>
          B3 原生件取证（折叠姿态 / 触感四种）
        </Link>
```

- [ ] **步骤 4：跑绿 + tsc**

Run: `cd mobile && npx jest test/foldPosture.test.ts && npm run typecheck && npm test`
Expected: 新增 5 条全绿；0 error；全量只增不减。⚠ expo-router typed routes：`/native-spike` 的 href 类型由 **Metro 起着时**生成（B1 第 2 批坑）——`tsc` 报路由不合法就先 `npx expo start --dev-client` 等 `.expo/types/router.d.ts` 更新，**不改 href 类型**。

- [ ] **步骤 5：反向验证**

① `foldPosture` 的 horizontal/vertical 两个分支对调 ⇒ **恰好红** tabletop 与 book 两条（各红各的——这就是「逐对交换」要能分辨哪半修法决定哪条断言）；② `state !== 'halfOpened'` 守卫去掉 ⇒ **恰好红**「全开 → flat」那条。

- [ ] **提交**

```bash
git add -- mobile/src/ui/layout/foldPosture.ts mobile/src/ui/layout/useFoldState.ts mobile/test/foldPosture.test.ts mobile/src/app/native-spike.tsx && git commit -m "feat(mobile): UX v2 B3-6 折叠姿态 JS 面——posture 纯函数 + hook + /native-spike 取证屏（含触感四按钮）" -- mobile/src/ui/layout/foldPosture.ts mobile/src/ui/layout/useFoldState.ts mobile/test/foldPosture.test.ts mobile/src/app/native-spike.tsx mobile/src/features/settings/SettingsScreen.tsx && git show --stat HEAD
```

---

### Task 7: expo-blur 材质 spike 屏（§5.11 / Q14——主线组件零依赖）

**Files:**
- 新建 `mobile/src/app/blur-spike.tsx`
- 修改 `mobile/src/features/settings/SettingsScreen.tsx`（调试分区 +1 行 Link）

**为什么**：方案 §5.11——G1 Frosted 的「真模糊是 B3 的 spike」；Q14 默认「先 spike，不通过就停在 G1-tint」。spike 的消费代码**全部住取证屏**，主线组件（`Glass` / `VoiceSheet`）零依赖它——通过了才在 B4 把 frosted 换真模糊，不通过则屏留档、库闲置（§5 第 3 条：依赖已进 APK，下一趟重建 B5 再裁去留）。**先验事实写在屏里**：expo-blur 在 Android 上默认是**半透明回退（无真模糊）**，只有 `experimentalBlurMethod` 打开才走真模糊路径，且官方明说有性能与动画代价——这正是要 spike 的原因，不是缺陷。裁决判据三条（T9 执行）：**视觉**（真模糊可辨：糊层下的高对比文字边缘不可读）、**性能**（`framestats` 呈现间隔 60fps 口径，B2 G5 的读法）、**稳定**（挂载/卸载 20 次不崩）。

- [ ] **步骤 1：实现（spike 屏是取证装置，无 jest；「红→绿」= 三块对照在真机上的可辨差异）**

`mobile/src/app/blur-spike.tsx`：

```tsx
// 材质 spike 屏（B3-7 / 方案 §5.11、Q14）：真模糊要不要上，在这里裁——不通过就停在
// G1-tint，主线组件零依赖本屏。三块对照压在同一份高对比滚动内容上：
//   ① G1-tint 现状（染色 + 边框，Glass 的等效参数）
//   ② BlurView 默认（Android 无 experimentalBlurMethod = 半透明回退——预期与①肉眼接近，
//      这块是「对照组」：它证明②③之间的差异来自 blurMethod，不是 BlurView 本身）
//   ③ BlurView + experimentalBlurMethod（真模糊路径）
// 裁决三判据（计划 T9）：③ 糊层下文字边缘不可读（视觉）；framestats 60fps（性能，B2 G5 口径
// ——先读 24 列表头再解析）；③ 挂载/卸载 20 次不崩（稳定）。
import { useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { BlurView } from 'expo-blur'

import { GLASS } from '@/ui/tokens'
import { usePalette } from '@/ui/theme'

/** 高对比背景条（糊不糊一眼可辨的判据物：细字 + 强色块交替） */
function Stripes({ fg }: { fg: string }) {
  return (
    <View>
      {Array.from({ length: 40 }, (_, i) => (
        <View key={i} style={{ flexDirection: 'row', alignItems: 'center', height: 28, backgroundColor: i % 2 ? '#0a84ff' : '#111' }}>
          <Text style={{ color: fg, fontSize: 11 }} numberOfLines={1}>
            {i} 高对比判据行——真模糊下这行小字应不可读 abcdefg 0123456789
          </Text>
        </View>
      ))}
    </View>
  )
}

export default function BlurSpikeScreen() {
  const p = usePalette()
  const [mountCount, setMountCount] = useState(0)
  const [blurOn, setBlurOn] = useState(true)
  return (
    <ScrollView style={{ flex: 1, backgroundColor: p.bg }} contentContainerStyle={{ padding: 12, gap: 16 }}>
      <Text style={{ color: p.fg1, fontSize: p.font(15) }}>
        ① G1-tint 现状 / ② BlurView 默认（Android 回退=对照组）/ ③ experimentalBlurMethod（真模糊）
      </Text>
      {/* ① 现状染色 */}
      <View style={{ height: 160, borderRadius: 16, overflow: 'hidden' }} testID="blur-tint">
        <Stripes fg="#fff" />
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: `rgba(10,14,24,${GLASS.frosted.tint})`, borderWidth: 1, borderColor: 'rgba(255,255,255,0.16)' }} />
      </View>
      {/* ② BlurView 默认（Android 上=半透明回退） */}
      <View style={{ height: 160, borderRadius: 16, overflow: 'hidden' }} testID="blur-default">
        <Stripes fg="#fff" />
        <BlurView intensity={60} tint="dark" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} />
      </View>
      {/* ③ 真模糊路径 */}
      <View style={{ height: 160, borderRadius: 16, overflow: 'hidden' }} testID="blur-real">
        <Stripes fg="#fff" />
        {blurOn ? (
          <BlurView
            intensity={60}
            tint="dark"
            experimentalBlurMethod="dimezisBlurView"
            style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
          />
        ) : null}
      </View>
      <Pressable
        testID="blur-remount"
        onPress={() => {
          setBlurOn((v) => !v)
          setMountCount((n) => n + 1)
        }}
        style={{ backgroundColor: p.fill, borderRadius: 12, padding: 12 }}
      >
        <Text style={{ color: p.fg1 }}>挂载/卸载 ③（稳定性判据：20 次不崩）· 已切 {mountCount} 次</Text>
      </Pressable>
    </ScrollView>
  )
}
```

（⚠ 两处以装出来的包为准：`experimentalBlurMethod` 的 prop 名与取值查 `node_modules/expo-blur/build/*.d.ts`——SDK 57 若改名/移除，spike 的目的不变、prop 跟着 d.ts 走，差异写进 §6.2；`GLASS.frosted.tint` 字段在 `ui/tokens.ts:28`（B1 T1 落的，B2 附加①用过 0.58 这个值），字段名对不上就 grep 核。）

`SettingsScreen.tsx` 调试分区追加：

```tsx
        <Link href="/blur-spike" style={{ color: p.accent, fontSize: p.font(14) }}>
          材质 spike（B3：真模糊 vs G1-tint 对照）
        </Link>
```

- [ ] **步骤 2：tsc + 全量**

Run: `cd mobile && npm run typecheck && npm test`
Expected: 0 error、全量不减（本任务零 jest——取证装置）。typed routes 同 T6 的 ⚠。

- [ ] **提交**

```bash
git add -- mobile/src/app/blur-spike.tsx && git commit -m "feat(mobile): UX v2 B3-7 expo-blur 材质 spike 屏——三块对照 + 挂卸稳定性装置（主线组件零依赖，Q14 裁决装置）" -- mobile/src/app/blur-spike.tsx mobile/src/features/settings/SettingsScreen.tsx && git show --stat HEAD
```

---

### Task 8: 一趟重建 + 注册验证 + 重装重核

**Files:**（零源码改动；产物与读数）

**为什么**：方案 §11.1 B3 行「是（一次）」——B3 全部原生变更压成这一趟。这也是 **`96a6830`（Oboe `VoiceCommunication` patch）第一次进 APK** 的时刻：patch 自 2026-08-29 17:27 就躺在 `mobile/patches/`（`postinstall` 走 patch-package），但 B1/B2 零重建、真机包一直是 17:22:24 的 ⇒ 装机后语音链路第一次有平台 AEC。**本任务只负责「构建成功 + 注册齐 + 链路复核」**；语音读数重取是 T10（第 3 批）——构建会话别顺手取语音读数，`lastUpdateTime` 记了就停。

- [ ] **步骤 1：构建前置**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1     # 退出码 0
powershell -ExecutionPolicy Bypass -File scripts\fetch_mobile_voice_assets.ps1   # 缺模型/原生件显式失败（CLAUDE.md §6.2）
```

- [ ] **步骤 2：构建（带 -Clean——本批有新 Expo 本地模块与新依赖，必须重生成 android/）**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Clean
```

Expected: BUILD SUCCESSFUL；预期时长 22–38 分钟（M4 读数：全量 38m27s、砍 ABI 后 22m42s）。**APK 体积记下来**与 M4 的 275.9MB 对比——新增三件都很小（haptics/blur 是薄桥、foldstate 零资产），涨幅 >20MB 就停下来查（多半是 ABI 或 FFmpeg 开关被动了）。

- [ ] **步骤 3：注册验证（构建成功 ≠ 注册上了——坑账 §9.43 一趟 38 分钟的学费；判据按 §0 第 3 条分流）**

```bash
# Expo 通道（本批三件新原生全在这边）——ExpoModulesProvider 由 expo-modules-autolinking 生成：
grep -rn "foldstate\|FoldState" "D:/Android/builds/xiaozhou-mobile/android/app/build/generated/" --include=*.java --include=*.kt | head
grep -rln "ExpoModulesProvider" "D:/Android/builds/xiaozhou-mobile/android/app/build/generated/"   # 找到文件后：
grep -n "haptics\|Haptics\|blur\|Blur\|FoldState" <上一步的 ExpoModulesProvider 路径>
# RN 社区通道（既有三件不许丢——-Clean 重生成过 android/，丢了就是 prebuild 配置被破坏）：
grep -n "RNGestureHandlerPackage\|OnnxruntimePackage" "D:/Android/builds/xiaozhou-mobile/android/app/build/generated/autolinking/src/main/java/com/facebook/react/PackageList.java"
```

Expected: FoldStateModule / expo-haptics / expo-blur 三件在 Expo 侧命中；RNGH 与 Onnxruntime 仍在 PackageList（2026-08-30 的行号是 73/75 级别，行号漂了没关系、**在**才是判据）。任何一件缺席 ⇒ **先修注册再装机**（§9.43：`.so` 在包里、gradle 绿都证明不了注册；Expo 模块的缺席多半是 `expo-module.config.json` 拼错类名或 `-Clean` 没带）。

- [ ] **步骤 4：AEC patch 进包核证（构建侧）**

```bash
# patch-package 在装依赖时打的（postinstall）；构建日志或镜像区核一眼补丁真的落在源上：
grep -n "VoiceCommunication" "D:/Android/builds/xiaozhou-mobile/node_modules/react-native-audio-api/common/cpp/audioapi/android/core/AudioRecorder"* 2>/dev/null || grep -rn "setInputPreset" "D:/Android/builds/xiaozhou-mobile/node_modules/react-native-audio-api/" --include=*.cpp --include=*.h | head -5
```

Expected: `setInputPreset(oboe::InputPreset::VoiceCommunication)` 在（patch 文件是 `mobile/patches/react-native-audio-api+0.13.3.patch`，1.6KB / 1 文件）。**不在 ⇒ 停**：镜像同步没带 patches/ 或 postinstall 没跑，装出来的包又是无 AEC 的——B2 那 5 分钟之差的翻版。

- [ ] **步骤 5：装机 + 全套重核（§0 第 4 条）**

```bash
adb install -r <APK 路径>    # dev-client 重装
adb shell dumpsys package com.xiaozhou.companion | grep -E "lastUpdateTime|versionName"   # 记进 §6.2——所有语音读数的口径锚
adb reverse tcp:8081 tcp:8081 && adb reverse --list
curl -s http://localhost:8081/status    # Metro 活着
adb shell pm list packages | grep maestro    # driver 还在不在（重装 APK 不动 driver，但要核）
```

- [ ] **步骤 6：冒烟（老能力一件不许丢）**

1. App 起、对话一轮文字问答正常（主链没断）；
2. `/native-spike`：`native: available`、posture 行有值（平放 = flat）；四个触感按钮**有振感**（详细验收在 T9，这里只验「原生活着」）；
3. `/blur-spike`：三块渲染、无崩溃（③ 有没有真糊留给 T9 裁决）；
4. **KWS/VAD 仍在**：免唤醒开一次，`avail: vad=true kws=true usable=true`（M4 的探针行）——`-Clean` 重生成过 android/，老模块丢没丢要有读数；
5. Maestro 09 流 rc=0（画廊离线冒烟，用 §0 第 4 条的 dist 路径 + `--no-reinstall-driver`）。

- [ ] **提交**（本任务预期零源码改动；若步骤 3/4 逼出了修复，修复各自归位提交。把读数写进 §6.2 后提交记录）

```bash
git commit -m "docs(mobile): UX v2 B3-8 一趟重建读数——注册验证三件 + AEC patch 进包核证 + lastUpdateTime 锚（§6.2）" -- docs/design/2026-09-01-mobile-ux-v2-b3-implementation-plan.md && git show --stat HEAD
```

---
### Task 9: B3 真机验收——触感四种 / 姿态手折 / blur spike 裁决（方案 §11.2 B3）

**Files:**（取证与裁决；撞上缺陷就修并记 §6.3）

**为什么**：§11.2 B3 的三条验收里两条只有真机给得出（触感的「振感」、折叠的「半开」——`cmd device_state` 只有 0 CLOSED / 1 TENT / 2 HALF_OPENED / 3 OPENED 的档位切换，**模拟不了物理半开的 FoldingFeature**，要人手折）；blur spike 的裁决（Q14）也在这里落锤。**手折与「确认双」的手感判定需要泓舟**（设备在他手里）；其余单人可造。

- [ ] **步骤 1：触感四种各触发一次（§11.2 B3 ②）**

1. **按钮面（振感对不对）**：`/native-spike` 四个按钮逐个按，逐个记「有振动 + 感受描述」：wake=轻一下；**confirm=双段感**（感不出「双」⇒ 按 T5 的备胎改两次 `impactAsync(Medium)` 间隔 120ms，改完重验、裁决写 §6.3）；dead=重一下；shutter=极轻。⚠ 振动强度是厂商映射，「四种可区分」比「绝对强度」重要——泓舟盲按能分出 wake/confirm/dead 三档就算过，分不出就调映射再验。
2. **挂点面（时机对不对，验两条代表——按钮验不了挂点）**：
   - 轻点光球开始说话 ⇒ 进 `listening` 那一刻**手上有轻振**（wake 挂点活着）；
   - 「打开后备箱」⇒ Dock 出现、球转 `attention` 那一刻**双振**（confirm 挂点活着）。
3. **开关**：设置页关「触感」⇒ 重复 2 的两条，**零振动**；开回来。
4. **双振探针（阴性）**：挂点面每次转移**只振一次**就是「effect 写法没被挪进渲染期」的活证（双振 = T5 的接线被人改过，回去查——同一转移双振没有正常成因）。

- [ ] **步骤 2：折叠姿态（§11.2 B3 ③，需泓舟手折）**

1. **肯定式主证**：内屏开 `/native-spike`，手折到半开、**横放桌面（铰链水平）** ⇒ `posture: tabletop`、`state: halfOpened`、`orientation: horizontal`、`bounds` 有值（截图 `b3-09-tabletop.png`）——这就是「姿态 hook 在 Fold 4 半开报 isTableTop=true」的读数。
2. **book 方向**：半开、竖持（铰链垂直）⇒ `posture: book`（截图）。
3. **全开回 flat**：展平 ⇒ `posture: flat`、`events` 计数在整个过程持续递增（事件流活着，不是初值糊弄）。
4. **外屏对照**：合上用外屏开同屏 ⇒ `present: false` 或 `state: flat`（外屏无折叠特征；读数照实记，这格是 B4 外屏布局的先验）。
5. ⚠ 折叠取证的截图一律先 `cmd device_state state` 再选 displayId（§0 第 8 条：抓错屏=全黑）。

- [ ] **步骤 3：blur spike 裁决（Q14 落锤，三判据全过才算过）**

1. **视觉**：`/blur-spike` 截图——③ 真模糊块下的判据行小字**不可读**、① tint 块下**可读**（两块对照同图）；② 默认块与 ① 肉眼接近（对照组成立——②③ 差异来自 blurMethod）。`png_probe` 佐证：③ 区域相邻像素方差显著低于 ①（糊=低频）。
2. **性能**：③ 在屏 + 手指滚动背景条，`framestats` 呈现间隔口径（B2 G5 的读法：**先读 24 列表头**、`IntendedVsync` 逐帧差中位 ≈16.7ms、p90 无跳档）。掉到 <55fps ⇒ 性能判据不过。
3. **稳定**：挂载/卸载按钮连按 20 次 + 屏内滚动 ⇒ 无崩溃、无 logcat 红条（`ReactNativeJS.*Error|FATAL` 零命中，配通道自检：同段 logcat App 行数 >0）。
4. **裁决写进 §6.3（三选一）**：**过** ⇒ B4 材质批把 G1 frosted 换真模糊（写明用的 blurMethod 与 intensity）；**不过** ⇒ 停 G1-tint（Q14 默认），`blur-spike.tsx` 留档、`expo-blur` 依赖闲置至下一趟重建再裁去留；**部分过**（如视觉过、性能悬）⇒ 记读数交泓舟，B4 前不落材质。

- [ ] **步骤 4：§6.3 回填三块读数**（触感表 / 姿态表 / blur 三判据 + 裁决一句话）。

---

### Task 10: AEC 首次进包——语音读数重取段（旧读数一律作废）

**Files:**（取证与裁决；`quickCommands`、RKStorage 等设备状态动过必须还原并记录）

**为什么**：T8 的重建把 `96a6830` 第一次带进 APK。主计划「上真 AEC」段与 AGENTS.md 那条判据：**`VoiceCommunication` 连带 NS/AGC 改变了送进 VAD/KWS 的音频——唤醒率、VAD 端点的旧读数一律作废，不许沿用**（M4-R1 真机验过「仍能唤醒」，但那是 08-29 当晚的包和阈值，B3 的包是重新构建的、要重新取）。B2 G2 的「回声 0 次」口径也整个换掉：那是「无 AEC 包上的观测」，AEC 在场后「0 次」的含义完全不同（§5 第 8 条是口径裁决）。**每条读数旁记 `lastUpdateTime`**（§0 第 5 条）——这一段的读数将来被引用时，第一句就是「取自含 AEC 的包」。

- [ ] **步骤 1：AEC 生效核证（OS 侧，这条是整段的前提）**

免唤醒开（麦常开）⇒ `adb shell dumpsys audio | grep -E "src client|VOICE_"` ⇒ **`src client=VOICE_COMMUNICATION`**（M4-R1 的判据形态：`rec update riid:xxxx src:VOICE_COMMUNICATION`；旧包是 `VOICE_RECOGNITION`）。**不是 ⇒ 停**：AEC 没生效，回 T8 步骤 4 查 patch 是否真进了构建——本段全部读数都以这条为前提。

- [ ] **步骤 2：S2S 采集路径的 AEC 覆盖面（B2 出账⑥终裁）**

静态证据（§0 第 6 条）：`handsFree.ts:128` 的 `micLease()` ⇒ S2S 帧来自同一条打过 patch 的 Oboe 输入流，「没覆盖」的定性**预期被推翻**。真机终裁：设置切 `voicePipeline=s2s` ⇒ 说一轮 ⇒ 同步骤 1 的 dumpsys ⇒ `VOICE_COMMUNICATION` 则**销账**（B2 出账⑥写「已核，同一采集流」）；真是 `VOICE_RECOGNITION` ⇒ 查是谁在 S2S 挡位另开了录音流（`react-native-audio-record` 还在依赖里——它暴露 `audioSource` 参数，JS 侧传 7=VOICE_COMMUNICATION 就能修，**不用再 patch**），修法与读数记 §6.3。测完把 `voicePipeline` 切回 `classic` 并回读确认（B2 的设备状态对账纪律）。

- [ ] **步骤 3：唤醒率重取（需真人；阈值 0.2/2.0 是旧音频路径定的）**

真人对手机说「小舟小舟」**10 次**（正常音量、正常距离，每次等 ARMED 态再说下一次），记命中数 N/10 与每次的间隔场景。直灌探针（debug 屏，M4 装置）跑一遍作**引擎对照**（直灌绕过麦，不经 AEC/NS/AGC——它只证「引擎没坏」，与唤醒率是两个读数，别混）。**N≥8 记达标；N<5 = NS/AGC 显著劣化了 KWS 输入，当场标红交泓舟裁**（阈值重调是另一批的活，B3 只取数定性）。

- [ ] **步骤 4：VAD 端点重取（两条观察判据，不设新仪器）**

1. 免唤醒**关** + 安静环境，轻点光球（TapTalk 路径）说一句 5 秒左右的话 ⇒ 说完后**≤2s 内自动收尾发送**（轨迹页 `listening→thinking` 时刻 − 实际住口时刻；录屏配轨迹页取）。**提前截断**（说话中收尾）或**收不了尾**（15s 硬上限兜底触发）都是 NS/AGC 影响 VAD 的反常签名——出现就记读数交裁。
2. 直灌探针复跑一遍：事件 start/end 时刻应与 M4 基线（start@647ms end@2449ms）同量级（直灌不经 AEC，这条是「引擎与模型没被重建弄坏」的对照）。

- [ ] **步骤 5：G2 回声口径的收口（§5 第 8 条的裁决落地）**

1. **AEC 在场的回声观测**：复刻 B2 G2 装置（媒体音量 ≥140/150——**先 `input keyevent 24` 抬并回读**，B2 坑③：`cmd media_session` 会静默失败、成功输出不是读数；蓝牙断开走扬声器；长播报「讲一个三百字的故事」）⇒ 观察整轮：回声提示次数、有无 barge-in 自触发、有无环。**预期 0 次——但这次 0 次的读法变了**：AEC 在场，「没回声」是设计在工作，不再是「提示路径可能死着」的嫌疑现场。
2. **口径裁决写进 §6.3（照 §5 第 8 条）**：「回声提示在 AEC 健康的设备上**不可自然触发**」正式入账——提示是 AEC 失效时的兜底，FSM 判定层已有 jest 钉住（B2 T11 的 `echo_dismissed` 用例），真机触发验证从验收清单**除名**，不再逐批抬着「⬜ 未触发」走。**可选装置**（泓舟想要真机触发一次才做，默认不做）：第二台设备播放一段本机 TTS 音色的录音、贴近麦、与本机播报同步——外部声源不在 AEC 参考信号里，文本又与正在播的重叠，FSM 会判回声；配方记档即可。
3. **连带的松紧待裁定挂账**（主计划「⛔ 待裁定」）：`ECHO_OVERLAP_RATIO 0.75` 是为「没有 AEC」的世界调的松度，AEC 上来后代价（吞真续问）原样、收益缩水。**共享判据 `voiceLoop.mjs` 本批一字不动**——本步骤只取数：步骤 1–5 期间若出现「真续问被当回声吞掉」记次数，连同本段读数一起交泓舟裁阈值（hmi 侧另立）。

- [ ] **步骤 6：G5 帧率复测（重建加了三件原生，性能不许回退）**

`framestats` 同 B2 口径（**先读 24 列表头**；呈现间隔中位 / p90；这块屏可变刷新率，先读 `FrameInterval` 再换算 fps）：层开播报中采一段 ⇒ 中位 ≈16.66ms、零掉帧同 B2；同屏循环动画仍 1 个（T1 的静态标记不是动画，`png_probe` 两帧 0.00% 在 T1 步骤 5 已取，这里引用）。

- [ ] **步骤 7：B2 验收表遗留复验（APK 换了，旧 ⚠/⬜ 四格在新包上补）**

| B2 表# | 项 | 配方 |
|---|---|---|
| 1 | 轻点光球（免唤醒关）→ 层升、录音、端侧 VAD 收尾并发送 | 与步骤 4.1 同一趟取（T3 修完 plateGesture 后这条还有第二入口：空输入框长按） |
| 2 | 真人语音轮全链（唤醒→说→答→8s 追问→切后台/折叠草稿不丢） | 需泓舟；折叠半程与 T9 步骤 2 同一次手折捎带 |
| 6 | 「这是什么」→ 用户气泡先于相机（时间戳比对） | 通路 B2 已验通：`screenrecord` → `adb pull` → `ffmpeg -vf fps=30` 重采样 → 帧号×1/30 对 logcat `CameraService::connect` 墙钟 |
| 7 | 「语音提问出声」半格（自动档） | 真人语音问一句 ⇒ Oboe OUTPUT 行 + 播报可闻（阳性对照 B2 已有打字侧） |

- [ ] **步骤 8：附加（非闸门，顺手则做）**：「换一批」chip——找一条真出 `poi_list` 且带 keyword 的语料（B2 附加⑦的定位：`candidates.ts` 只有 `poi_list` 且 `c.keyword` 非空才记 category；用 Maestro `inputText -e` 灌中文语料试「附近的川菜馆」一类**带品类词**的说法）；出了就截图销账，没出就把试过的语料记 §6.3。

- [ ] **步骤 9：§6.3 回填**：每条读数带 `lastUpdateTime`；B2 §6.4 出账表逐条写去向结果（销账/转修/仍开）。

---

### Task 11: B3′——默认助理角色 spike（独立分支构建，不进主线、不发版）

**Files:**（全部在 spike 分支 `spike/b3p-assistant-role` 上；主线零改动）
- 新建 `mobile/modules/assistrole/`（manifest + service 壳 + 极小查询 Module）、`mobile/src/app/voice.tsx`（deeplink 落点）

**为什么**：方案 §11.1 B3 行的显式拆分——「默认助理角色不再捆在这一趟：它改 manifest 与 Service，未验证就不该进正式构建，改成独立的兼容性 spike 分支构建（B3′，不发版）」。要验的是 Q5 的两个平台事实：**HyperOS 的默认数字助理列表里能不能选到我们**（`ROLE_ASSISTANT` 三方可申请是 AOSP 事实，HyperOS 没验过）、**电源键/手势触发是否响应还是被小爱独占**。红线照 §12.2：**只在前台且已解锁时响应；进入 App 后仍需一次手势才开麦（deeplink 只到对话页，不升层、不开麦——升层开麦都不是 spike 的判据，Q5 原文只验「选得到 + 响应」两问）**。

- [ ] **步骤 1：开分支（先确认工作树干净——别把别的会话的未提交行带过去）**

```bash
git status --short   # 必须干净；不干净就停，共享树的行不属于 spike
git checkout -b spike/b3p-assistant-role
```

- [ ] **步骤 2：`modules/assistrole/`（走 modules/ 机制——library 的 AndroidManifest 会 merge 进 APK，零 config plugin）**

`expo-module.config.json`：`{ "platforms": ["android"], "android": { "modules": ["com.xiaozhou.assistrole.AssistRoleModule"] } }`
`android/build.gradle`：照 `modules/foldstate` 模板（namespace `com.xiaozhou.assistrole`、versionCode/versionName、零外部依赖）。
`android/src/main/AndroidManifest.xml`（service 声明住 library、merge 进 app）：

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application>
    <!-- B3′ spike：VoiceInteractionService 壳。只为验证两个平台事实（Q5）：
         角色列表可见性 + 触发手势响应。onShow 里只做「已解锁 → deeplink 到对话页」，
         不开麦、不出会话 UI（§12.2 红线）。 -->
    <service
      android:name="com.xiaozhou.assistrole.XiaozhouVoiceInteractionService"
      android:exported="true"
      android:permission="android.permission.BIND_VOICE_INTERACTION">
      <intent-filter>
        <action android:name="android.service.voice.VoiceInteractionService" />
      </intent-filter>
      <meta-data android:name="android.voice_interaction" android:resource="@xml/xiaozhou_voice_interaction" />
    </service>
    <!-- 类名一律全限定：library manifest 的相对名（`.X`）在 AGP8 的 merger 里按 namespace
         解析，规则没错但多一层解析就多一处「症状与压根没配一样」的静默失败面（§9.43 族） -->
    <service
      android:name="com.xiaozhou.assistrole.XiaozhouVoiceSessionService"
      android:exported="false"
      android:permission="android.permission.BIND_VOICE_INTERACTION" />
  </application>
</manifest>
```

`android/src/main/res/xml/xiaozhou_voice_interaction.xml`：

```xml
<voice-interaction-service xmlns:android="http://schemas.android.com/apk/res/android"
  android:sessionService="com.xiaozhou.assistrole.XiaozhouVoiceSessionService"
  android:recognitionService="com.xiaozhou.assistrole.XiaozhouVoiceInteractionService"
  android:supportsAssist="true" />
```

`android/src/main/java/com/xiaozhou/assistrole/AssistRole.kt`（三个类合一个文件——壳就是壳）：

```kotlin
package com.xiaozhou.assistrole

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService

/** 壳：不持有任何会话逻辑（S2S 红线不在这里破——本服务没有执行通道，连麦都不碰） */
class XiaozhouVoiceInteractionService : VoiceInteractionService()

class XiaozhouVoiceSessionService : VoiceInteractionSessionService() {
  override fun onNewSession(args: Bundle?): VoiceInteractionSession = XiaozhouVoiceSession(this)
}

/** 触发（电源键长按/手势）→ onShow：已解锁才 deeplink 到对话页；锁屏直接收起（§12.2）。
 *  不开麦、不升语音层——进 App 后仍需一次手势才开麦，deeplink 只到 xiaozhou://voice。 */
class XiaozhouVoiceSession(context: Context) : VoiceInteractionSession(context) {
  override fun onShow(args: Bundle?, showFlags: Int) {
    super.onShow(args, showFlags)
    val kg = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
    if (!kg.isKeyguardLocked) {
      val i = Intent(Intent.ACTION_VIEW, Uri.parse("xiaozhou://voice"))
      i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      context.startActivity(i)
    }
    hide()
  }
}

/** 极小查询面：spike 屏读「角色现在是不是我们的」用 */
class AssistRoleModule : expo.modules.kotlin.modules.Module() {
  override fun definition() = expo.modules.kotlin.modules.ModuleDefinition {
    Name("AssistRole")
    Function("isAssistRoleHeld") {
      val rm = appContext.reactContext?.getSystemService(android.app.role.RoleManager::class.java)
      rm?.isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT) ?: false
    }
  }
}
```

`mobile/src/app/voice.tsx`（deeplink 落点，spike 分支上）：

```tsx
// B3′ spike：xiaozhou://voice 的落点——只回对话页（§12.2：进 App 后仍需一次手势才开麦；
// 升层/开麦都不在 spike 判据里，Q5 只验「角色可选 + 手势响应」两问）。
import { Redirect } from 'expo-router'

export default function VoiceDeeplink() {
  return <Redirect href="/" />
}
```

- [ ] **步骤 3：spike 构建 + 装机**（`fetch_mobile_voice_assets.ps1` → `build_mobile.ps1 -Clean`；**spike APK 覆盖装机会顶掉 T8 的主线包**——验完要重装主线包并复核 `lastUpdateTime`，写进 §6.4 的设备状态对账）。

- [ ] **步骤 4：真机两问（Q5）**

1. **角色可选吗**：HyperOS 设置 →「默认数字助理」路径（原生路径「设置 → 应用 → 默认应用 → 数字助理」；HyperOS 可能藏「更多应用设置」下，找不到就系统设置搜「助理」）⇒ 列表里**「小舟随行 (Dev)」出现吗**（截图）。出现且可选 ⇒ 选上，`/native-spike` 若接了 `isAssistRoleHeld` 读 true（接线可选，spike 屏加一行即可）。
2. **手势响应吗**：电源键长按（HyperOS 上可能绑小爱——这正是 Q5 要验的）/ 底部角滑手势 ⇒ **已解锁**：App 被拉到前台对话页（不开麦——屏上无录音态，隐私栏无采集点）；**锁屏**：按下手势 ⇒ **什么都不发生**（§12.2 红线的活证，配 logcat `onShow` 有到达 + 无 startActivity 佐证「响应了但拒了」）。
3. 读数三态照实记：可选+响应 / 可选+不响应（电源键被小爱独占，记哪个手势试过）/ 列表里根本不出现。**任何一态都是 spike 的合法产出**——B5 的「角色启用」排期靠它定。

- [ ] **步骤 5：收口（分支不合并）**

spike 分支上提交（`git add -- mobile/modules/assistrole mobile/src/app/voice.tsx && git commit -m "spike(mobile): UX v2 B3' 默认助理角色壳——角色可见性与手势响应验证（不合主线）" -- mobile/modules/assistrole mobile/src/app/voice.tsx`）；`git checkout main`（或本批工作分支）；**主线重装 T8 的包**并复核；读数写 §6.4（在主线上提交记录）。分支保留不删、不合并、不发版——它是 B5 的输入。

---

### Task 12: 记录收口

- [ ] **步骤 1：文档五处**
  1. `mobile/e2e/README.md`：追加「B3 重建后」一节——driver 复核命令、`lastUpdateTime` 口径锚、`/native-spike` 与 `/blur-spike` 两个取证屏的深链；
  2. 主计划 `2026-08-24-mobile-app-implementation-plan.md`：B2 指针块下加同款 B3 指针段（实施记录在本文件 §6）；**§9 坑账追加本批新坑**（撞几条记几条，编号接着最后一条排）；
  3. `docs/design/README.md`：本计划行状态改「四批收口 + blur spike 裁决一句话 + B3′ 两问读数一句话」；
  4. `AGENTS.md` §4.1 Android 行：**只改指针段**（B3 收口 + AEC 已进包旧读数作废线 + 下一步 B4）——动前 `git diff --stat -- AGENTS.md` 核行数、单独 commit；
  5. 本文件 §6.4：读数、坑、遗留出账表（含 B2 出账逐条的去向核销）、**未推送清单**（`git log origin/main..HEAD --oneline` 只报数不推送——写死条数必然过期，列构成）。
- [ ] **步骤 2：全量收口**：`cd mobile && npm test && npm run typecheck` 终值写 §6.4（条数账逐任务对得上、只增不减）；`python scripts/run_e2e.py --target cloud` 若本批动过与云栈交互的面（预期没有——零后端改动，跑一次冒烟是廉价保险）。
- [ ] **提交**：

```bash
git commit -m "docs(mobile): UX v2 B3 收口——四批读数与遗留出账（§6）+ README/主计划指针" -- docs/design/2026-09-01-mobile-ux-v2-b3-implementation-plan.md docs/design/README.md docs/design/2026-08-24-mobile-app-implementation-plan.md mobile/e2e/README.md && git show --stat HEAD
# AGENTS.md 单独一个 commit（所有会话都在写的文件）：
git diff --stat -- AGENTS.md && git commit -m "docs(agents): Android 行指向 UX v2 B3 收口（AEC 进包，语音旧读数作废线）" -- AGENTS.md && git show --stat HEAD
```

---
## 3. 任务依赖与并行度

```
第 1 批：T1 S4 ─► T2 S6 ─► T3 plateGesture          （T1→T2 同文件 AuroraOrb.tsx 串行；
                                                      T3 文件独立，但同一台真机、串行省纠纷）
第 2 批：T4 依赖落盘 ─► T5 触感 JS ─► T6 姿态 JS ─► T7 blur 屏 ─► T8 一趟重建
第 3 批：T9 B3 验收三条 ─► T10 AEC 读数重取
第 4 批：T11 B3′ spike 分支 ─► T12 收口
```

- **T4 必须最先**：npm 包不落盘，T5/T7 的 import 连 tsc 都过不了；`modules/foldstate` 的 `index.ts` 是 T6 的类型源。
- **T5 先于 T6**：`/native-spike` 的触感四按钮引 `performHaptic`（T5 的产物）。
- **T7 可与 T5/T6 并行**（互不相干的新文件）；三者都在 `SettingsScreen.tsx` 加一行（T5 语音分区、T6/T7 调试分区各一行）——**串行提交**，后提的 rebase 自己那行（B2 第 4 批同款约定）。
- **T8 是第 2 批的门**：注册验证不过就不进第 3 批——「构建成功 ≠ 注册上了」是本仓花过 38 分钟学费的判据。
- **T9 先于 T10**：T9 撞出的修复（触感映射调整、blur 崩溃）要在语音读数重取前落定——重取段的读数不许跨改动转借（证据绑 SHA）。
- **T11 在最后**：spike 构建会顶掉主线包，放在所有主线真机取证之后，验完重装主线包。

## 4. 「不负优化」判据在 B3 的取数点（方案 §11.4）

| 判据 | B3 取数 |
|---|---|
| 首反馈时延 | T10 步骤 7 #2 真人轮顺带（轨迹 `◇ fsm:LISTENING` → `listening` 快照对，B2 口径）——**新包上的复测**，与 B2 的 3ms 同口径可比 |
| 状态可读性 | 无新分布读数（外部基线仍缺——B2 §6.4 的裁定遗留，别把泓舟自评 6/6 当分布）；S4/S6 修复后请泓舟**复看那两张**（S4 speaking 静态标记 / S6 muted 完整圆）——修的就是他验收指出的两格 |
| 记录完整性 | 不复取（B2 第 2 批读数仍引用；`voicePipeline` 在 T10 步骤 2 切到 s2s 走一轮，顺带看记录条数不掉） |
| 承诺不丢 | 不动（B1/B2 已收；重建后 Maestro 06 复跑一次作回归） |
| 键盘遮挡 | 不动（G3 已过；重建后 Maestro 08 复跑一次作回归——**重装过 APK 的包上 rc=0 才算数**） |
| 性能 | T10 步骤 6：`framestats` 呈现间隔口径复测（重建加了三件原生，不许回退）；同屏循环动画仍 1 个（T1 步骤 5.3 的 0.00% 读数） |
| 无障碍 | Scanner 仍 ⬜（装 APK 需泓舟单独授权，本批未列）；触感是无障碍的加分面（TalkBack 用户的状态转移多一条通道），T9 步骤 1 的读数顺带记 |
| 回归 | `npm test` 条数账：400 →(T1 +2)→ 402 →(T5 +6)→ 408 →(T6 +4)→ **≈412，只增不减**；`hmi/` 零改动；Maestro 06/08/09 在新包上各复跑一次 rc=0（06/08 归 T10 步骤 7 附带，09 在 T8 步骤 6） |

## 5. 实施判断（写在开工前，做的时候撞到再补）

1. **`react-native-keyboard-controller` 裁定不装**。方案 §7.6 第 2 条的措辞是「若 1 在 HyperOS 输入法下仍不稳 ⇒ keyboard-controller（归 B3 重建）」——**待证条件从未成立**：B1 T12 取了读数（真机 HyperOS，遮 → `behavior='padding'` A 修法 → Maestro 08 过 + 反向验证红，B1 §6.3 e 条）；B2 G3 两半都过（08 复跑 rc=0/133s + 层开着弹键盘发送键仍在树的专门探针）。两批读数里「不稳」一次没出现 ⇒ 不装。将来若 HyperOS 输入法升级撞出新形态，B5 的重建趟再议——**证据在案，这不是拖延是裁决**。
2. **折叠模块自写、不引 `@logicwind/react-native-fold-detection`**（理由三条见 T4「为什么」：bounds 全量事实 / kws 模板与 §9.45 门槛已踩 / 少一个 autolinking 缝候选）。方案 §7.3 说「任一都是新原生依赖」——自写也是原生件，归这一趟重建，没绕过任何纪律。
3. **`expo-blur` 依赖进主线、消费代码 spike 化**。§5.11「不通过就不进主线」指的是**材质用法**；依赖本身若不装，spike 就得单独构建一趟（违背「一次重建」的成本模型）。装进主线的代价是 spike 不过时 APK 里躺一个闲置薄桥——下一趟重建（B5 随 M5）再裁去留。泓舟若不接受这个折衷，改法是把 T7/T9.3 挪进 B3′ 分支一起构建（两问变三问），主线不装 blur——**改一行 T4 的 install 清单即可，批准计划时说一声**。
4. **S4 的立场**：`orbAnimated` 的停动画**不回退**（G5 的达标读数就是它），可读性靠静态标记补。若泓舟真机看了觉得静态双环不够醒目，升级路径是加静态「▶ 播报中」微标或胶囊常显——都在「零循环动画」约束内迭代，不回到二选一。
5. **方案 §11.2 B3 ① 的判据字面要修正**：「`PackageList.java` 含新 Package」只对 RN 社区库成立；本批三件全走 Expo 通道，判据是 `ExpoModulesProvider` + 运行时探针（§0 第 3 条、T8 步骤 3）。**这不是放松验收，是把尺子对准注册机制**——PackageList 那半仍要验（既有三件不许丢）。
6. **触感挂 `useEffect` 不挂渲染期**（B2 T14 红条同族：触感不幂等，StrictMode 双渲会双振）；判死轴的字段是 `degradation: Degradation[]`、判别 `d.kind`（`presence.ts:48-55`，写计划时已核）。
7. **`expo-glass-effect` 不是 blur 的替身**：它已在依赖里（`package.json`，iOS liquid glass 专用库，Android 无效）——别在 spike 里误用它得出「真模糊不可用」的假结论；Android 的真模糊路径只有 expo-blur 的 `experimentalBlurMethod`。
8. **G2 回声口径的裁决**（T10 步骤 5 落地）：AEC 在场后，「回声提示真机自然触发」正式定性为**不可观测**——提示是 AEC 失效兜底，FSM 判定层有 jest（B2 T11），真机触发从验收清单除名、不再逐批抬「⬜」。可选的双设备装置配方记档，默认不做。连带把主计划「⛔ 待裁定：回声判据松紧」的取数做了（吞真续问计数），阈值裁决交泓舟/hmi 侧——**共享 `voiceLoop.mjs` 本批一字不动**。
9. **`lastUpdateTime` 锚**：本批起语音类读数不带包锚不算数（§0 第 5 条）。B2 那笔「5 分钟之差装了旧包」的学费直接变成口径。
10. **B3′ 永不合并**：spike 分支是证据保存形态，B5 做「角色启用」时**重新按主线写**（manifest/Service 届时走正式评审），不 cherry-pick spike 代码——spike 的价值是两问的读数，不是代码。
11. **唤醒率的红线**：真人 10 次 N<5 当场标红交泓舟（NS/AGC 劣化 KWS 输入的信号），**不自行调阈值**——阈值 0.2/2.0 的重定是独立批（要 A/B，不是拍脑袋），B3 只取数定性。
12. **第 1 批在旧包上做**：三条修复是 JS，Metro 热载可验；但**语音类读数一条都不许在第 1 批产出**（旧包无 AEC，读了也是要作废的数）——T3 步骤 4.5 的 15s 硬上限是「计时行为」不是「音频质量」读数，可以取；拿不准的一律留 T10。

## 6. 实施记录（分批回填；每批一个会话，写完即停）

> 格式照 B1/B2 计划 §6：先**开工基线**（自己跑出来的数——jest 条数、tsc、`git log origin/main..HEAD` 计数、设备在线、第 2 批起加 `lastUpdateTime`）、再逐任务的提交与读数、再**反向验证**逐条、再「本批踩的坑」、最后「遗留 / 给下一批的话」。读数只写自己跑出来的数，不复述计划预期；**未跑的一律不写 ✅**；语音类读数必须带 `lastUpdateTime` 锚。

### 6.1 第 1 批「重建前的三条 JS 缺陷」（T1–T3）

> 泓舟 2026-09-01 批准本批开工（计划头部「草案待批」以该条为准）。全批在旧 APK
> （`lastUpdateTime=2026-08-29 17:22:24`，**AEC 补丁 17:27:50 之前**，§0 第 5 条口径核过）
> + Metro 热载上做，**未产出任何语音类读数**（唤醒率 / 回声 / 端点归第 3 批）。

**开工基线（自己跑出来的数）**

| 项 | 读数 |
|---|---|
| `scripts\check_android_env.ps1` | 退出码 **0**；18 pass / 0 warn / 0 fail；设备 `5d432b6d` 在线 |
| `npm test` | **39 suites / 400 tests 全绿** |
| `npm run typecheck` | **0 error** |
| `git log origin/main..HEAD` | **2 个提交**，都是计划撰写会话的：`3ca90e2` / `80782c3`；`git status` 干净 |
| Metro | 已在跑，PID 18496，命令行核过服务的是**本仓** `mobile/`（`/status` = `packager-status:running`） |
| `dev_stack target show` | `cloud` |
| APK | `versionName=0.1.0`、`lastUpdateTime=2026-08-29 17:22:24` |
| worktree | 泓舟未授权分树 ⇒ **在主工作树做**，每次提交前重采 `git status`（三次都只有自己的文件） |

**T1（S4 静态 speaking 标记）——提交 `124d152`，4 文件 +47/−1**

`npm test` 400 → **402**（新 `test/orbPolicy.test.ts` 2 条），tsc 0，既有断言零改动。

真机取证走**生产路径**（不是取证屏）。到达目标态 `input=voice-sheet + primary=speaking` 的路子：
播报设「总是」→ 发文字 → 等 speaking → **点状态胶囊**显式开层。⚠ 计划里没写这条：
speaking 时点光球会走 `onOrbTap` 的 `interruptAndListen()`（有竞态），而 `PresenceCapsule`
接了 `onPress = setSheetOverride(open)`（`ChatScreen.tsx:518`；`presence.ts:258` 注释原话
「点胶囊 = 显式打开，哪怕是文字轮」）⇒ **点胶囊无竞态**。⚠ `ChatScreen.tsx:511` 那条
「B1 不接 onPress」的注释是**过期的**，代码里已经接了。

| 格 | 判据 | 读数 | |
|---|---|---|---|
| 肯定式主证 | 环带 G−R > +15 | 内环 r=64：**+39 / +39**；外环 r=86：**+16 / +20** | ✅ |
| 几何自洽 | 两环比值 | 实测 86/64 = **1.34** = 实现的 (0.66+2×0.34)/(0.66+0.34) | ✅ |
| 阴性对照 A（**只变 speaking**） | 层仍开、TTS 已结束、同四点 | **−2 / −1 / −3 / 0** | ✅ 无青 |
| 阴性对照 B（计划字面：层收起 + idle） | 同四点 | **+4 / +5 / 0 / −3** | ✅ 无青 |
| **G5 口径复核** | 层开播报中两帧 Composer 球区 diff | **0.00%**（0 / 36100 px，不同字节 0） | ✅ 不破 |
| 通道自检 | 同两帧层内大球区 diff | **99.99%**（14399 / 14400，单通道最大偏差 57） | ✅ 通道活着 |

图：`b3-01-orb-speaking-static.png` / `b3-01-frame2.png` / `b3-01-orb-idle-negative.png` /
`b3-01-orb-idle-collapsed.png`（`mobile/e2e/artifacts/`，gitignore）。

**T2（S6 oversize filter wrapper）——提交 `1e86d4a`，1 文件 +20/−2**

`npm test` **402（零增量、条数不减）**，tsc 0。七层子元素含缩进一字未动（刻意不重排，让 diff 只剩 wrapper 那几行）。

| 格 | 判据 | 修前 | 修后 | |
|---|---|---|---|---|
| 盒外灰环带 · 右 | 亮像素(≥60) / 195 | **0（0.00%）** | **195（100.00%）** | ✅ |
| 同 · 左 | | **0（0.00%）** | **104（53.33%）** | ✅ |
| 同 · 上 | | **0（0.00%）** | **168（86.15%）** | ✅ |
| 同 · 下 | | **0（0.00%）** | **168（86.15%）** | ✅ |
| 盒边硬悬崖 | 水平 / 垂直剖面 | ±66 处 **101→20 悬崖**（= 方框） | 无悬崖；环峰在 **±78**（设计 79.2），盒外平滑衰减 | ✅ |
| 非 muted 回归 | 盒外环仍在设计半径 | — | armed **r=80**（设计 79.2）；listening **r=83/84**（84.5）；attention **r=80/82** 且 R−B 由 −40 翻正到 **+25/+33**（琥珀签名） | ✅ |
| **生产路径** | 对话屏开飞行模式 | — | 红健康点 +「已断开 · 消息会排队」，Composer 球去饱和且**灰环完整成圆**，环峰 r=76–78 | ✅ |
| Maestro 09 离线冒烟 | rc | — | **rc=0** | ✅ |

⚠ 非 muted 回归**不能用逐字节 diff**：画廊的球是 `animated`，两帧必然不同，diff 百分比分不出
「还在但在动」和「没了」⇒ 换成**结构判据**（环在不在设计半径上、琥珀环的 R−B 符号）。

图：`b3-02-muted-before.png` / `b3-02-muted-after.png` / `b3-02-nonmuted-after.png` /
`b3-02-muted-production.png`。

**T3（plateGesture）——提交 `9e54407`，1 文件 +51/−28**

`npm test` **402（零增量）**，tsc 0。

先自己把红重新证一遍（没照抄 B2 的定性）：空输入框长按 4s ⇒ 层不升、屏上出现**文本光标水滴柄**
+ 输入法选择工具条 ⇒ 原生 EditText 吃掉触摸，机制与 B2 出账一致（`b3-03-plate-hold-red.png`）。

**A-spike 逐候选**（每个都真机验；证据自带出处——`placeholder` 里带 `[A1]`/`[A2]` 标记，
否则「候选不工作」可能只是新代码没到设备）

| # | 配置 | 半 1 长按起 PTT | 半 2 轻点聚焦 | 结论 |
|---|---|---|---|---|
| A1 | `makeHold(...).blocksExternalGesture(ref)` + TextInput 外包 `Gesture.Native().withRef()` | ❌ 层不升、水滴柄仍在（截图 placeholder 显示 `[A1]`，出处自证） | 未验（两半都要过才算过） | **败** |
| A2 | `Gesture.Exclusive(makeHold(...), Gesture.Native())` | ❌ 同上（placeholder 显示 `[A2]`） | 未验 | **败** |
| A3 | `makeHold(...).disallowInterruption(true)` | ❌ **Render Error: undefined is not a function**；`tsc` 同时红：`TS2339: Property 'disallowInterruption' does not exist on type 'PanGesture'` | — | **败（API 在 RNGH 2.32 上根本不存在）** |

⇒ 三候选全败，走 **B 方案：空输入框时铺透明触摸层**（`composer-plate-overlay`；
`Gesture.Exclusive(hold, tap→inputRef.focus())`；`plateOverlayOn = !!ptt && !finalizing && input.length === 0`）。

| 格 | 判据 | 读数 | |
|---|---|---|---|
| **肯定式主证（B2 从来没绿过的那格）** | 空输入框长按 4s ⇒ 层升 + 录音中 | 层升起 + 层内大球 +「在听…」胶囊 + Composer placeholder 变「正在听…」 | ✅ |
| 轻点回归 | 轻点 ⇒ 键盘弹起可打字 | 键盘弹起（且是在轮次在飞时点的，更强） | ✅ |
| 反例 a | 有字时长按输入框 ⇒ 不触发 PTT | 框里有「a」，长按 4s：层不升、走原生选择（水滴柄） | ✅ |
| 反例 b | 长按光球 ⇒ PTT 仍可 | 框里有字时长按光球：层升 +「在听…」 | ✅ |
| 上滑取消 | 长按后上滑 ≥60dp 松手 ⇒ 不发送、层收起 | 中途帧「**已取消，这段话不会发给小舟**」（证明 PTT 真起来过，不是「压根没触发」）；松手后层收起、**零消息发出** | ✅ |
| 15s 硬上限 | 见遗留① | 设备时钟：轻点 `15:48:16.253` → 收尾 `15:48:32.176` = **15.92s**（`TAP_MAX_MS=15s` + `asr.start()` 建链 ≈0.9s） | ⚠ 遗留① |
| Maestro 05 | rc | **rc=0** | ⚠ 遗留③ |

图：`b3-03-plate-hold-red.png` / `b3-03-a1c-hold.png` / `b3-03-a2-hold.png` /
`b3-03-plateB-hold.png` / `b3-03-plateB-tap.png` / `b3-03-neg-typed-hold.png` /
`b3-03-orb-hold.png` / `b3-03-cancel2-plate-mid.png` / `b3-03-cancel2-plate-after.png` /
`b3-03-tapcap-trail2.png`。

**反向验证**（每条先 grep 证明变异真的落盘，跑完还原复跑全绿）

| 任务 | 变异 | 落盘证据 | 结果 |
|---|---|---|---|
| T1 ① | `composerOrbAnimated` 恒 `return true` | grep 到第 10 行 `return true` | **恰好红第 1 条**（voice-sheet），第 2 条绿 |
| T1 ② | 恒 `return false` | grep 到第 10 行 `return false` | **恰好红第 2 条**，第 1 条绿 |
| T1 还原 | | grep 回 `return s.input !== 'voice-sheet'` | 2 passed |
| T2 | filter 挪回根 View | grep 到 `saturate` 紧跟 `opacity` 行（第 161 行） | 四个环带**全部回到 0.00%**、方框复现；还原后 `git diff --stat` 回到 +20/−2 |
| T3 | 真机对照就是本任务的变异测试 | A1/A2/A3 各自的失败态 | B 的正反两侧都有读数 |

**本批踩的坑**（每条都是一次红换来的）

1. **MSYS 路径转换两头都有代价**：`adb pull /sdcard/x.png` 被改写成 `D:/Program Files/Git/sdcard/…`；
   不加引号的 `adb shell screencap -p -d <id> /sdcard/x.png` 同样被改写、报 usage。修法
   `export MSYS_NO_PATHCONV=1`——但**设了之后** `python /tmp/scan.py` 又会被当成 `D:\tmp\scan.py`
   （同一个开关，两个方向各坏一次）。
2. **Maestro `scrollUntilVisible` 在设置页判定恒否**（它确实滚了，但自己判「不可见」）——与
   `e2e/09-state-gallery.yaml` 文件头记的**同一条坑，本轮第二次撞**。可用组合是 `scroll` + 可见性断言；
   写成 `repeat + runFlow(when: notVisible) + scroll` 的「条件下滚」一次过。
3. **设置页 `openLink` 不重挂、保留上次滚动位置** ⇒ 固定次数滚动第一跑就滚过头（4 次直接到底部「调试」段），
   第二跑又因为停在底部而找不到顶部的元素。修法：先向上滚过头回顶，再条件下滚。
4. **连按两次 back 把应用退到桌面** ⇒ 那一轮截图拍的是桌面壁纸编辑页，读数作废
   （「观察对象缺席时零事件 = 零证据」）。脚本里加了前台核对，不通过直接 `exit 2`。
5. **候选测试必须让证据自带出处**：给每个 A 候选在 `placeholder` 里塞 `[A1]`/`[A2]` 标记，
   截图里看得见才算「跑的是这个候选」——否则「A 不工作」和「A 的代码没到设备」长得一模一样。
6. **分进程 `input motionevent MOVE` 形不成连贯手势流**：上滑取消首测失败，看起来像 B 方案的缺陷。
   **对照实验**（同一套注入打在 T3 一字未动的**光球**路径上）同样取消不了 ⇒ 定性成**仪器问题**
   而非被测系统问题。换单进程慢速 `input swipe`（3s 走 230px：t=300ms 时才移动 7.7dp <
   `minDistance` 12dp ⇒ 由 `activateAfterLongPress` 激活，随后位移长到 −76dp < −60dp）后当场取消成功。
7. **裸 `adb shell input swipe` 滚不动本 App 的 ScrollView**（轨迹页、设置页都试过），而 Maestro 的 `swipe` 可以。
8. **Maestro 的文本选择器命中同名的第一个**：还原设置时 `tapOn: "自动"` 点到了「模型偏好」的自动、
   `tapOn: "标准"` 点到了「字号」的标准。修法：相对选择器 `tapOn: {text: "标准", below: {text: "回答长度"}}`。
9. **计划的候选表里可以有一个不可能的选项**：A3 的 `disallowInterruption` 在 RNGH 2.32 上不存在，
   `tsc` 一跑就红 ⇒ spike 的候选**先过 tsc 再上真机**能省一趟 20s 重载。

**遗留 / 给下一批的话**

1. ⚠ **计划的「15s 硬上限」格与 `tapTalk.ts` 的三层设计自相矛盾，出账**：
   `tapTalk.ts:8` 写明收尾有三层——端侧 VAD（`TAP_SILENCE_MS=800`）/ 服务端 `vad_silence_ms`
   （只有 qwen3 消费，App 默认 `fun-asr-realtime` ⇒ 收不了尾）/ `TAP_MAX_MS=15_000`，**谁先到谁收**。
   ⇒ **安静环境下应由 VAD 800ms 尾收尾（≈1–2s），根本走不到 15s**；真走到 15s 反而说明 VAD 没载上。
   计划步骤 4.5 写的「安静环境 ⇒ 15s 自动收尾」把两层弄反了。本轮实测环境**有持续人声**
   （转写到旁人整段对话），VAD 静音尾从未触发——这恰恰是**单独暴露硬上限那一层的条件**，量到 **15.92s**。
   **「安静环境下轻点不说话」这一格仍是 ⬜**（环境不满足，不写「预期」）；下一批要么在安静环境补量
   VAD 那一层（判据应是 ≈1–2s 而不是 15s），要么把计划这条判据改掉。
2. ⬜ 轨迹页的「采集激活」段这次没读到（ScrollView 吃不到裸 adb swipe，坑 ⑦）；不影响 T3 结论
   （收尾时刻用设备时钟 + 轨迹主表读的）。
3. ⚠ **Maestro 流 05 本轮 rc=0，但这条流的绿红都不能单独当读数**：文件头记着「没人说话时稳定红在
   `assertVisible: voice-sheet` 上，而功能是好的」（2026-08-31 实测）——本轮是**环境有持续人声**、
   层保持到断言才绿的。它是 `tags: manual`，无人说话时拿不到确定的 rc。
4. `ChatScreen.tsx:511` 的注释「**B1 不接 onPress**」已过期——`PresenceCapsule` 现在接了
   （`:518` 传 onPress；组件里 `testID="presence-capsule"`，`accessibilityRole` 随 onPress 变）。
   纯注释漂移，**没改**（不在本批范围）；出账给 B4 视觉批顺手清。
5. 设备状态已还原：回答长度 标准 / 播报 自动 / 模型偏好 自动 / 免唤醒 关 / 飞行模式 关；
   `adb reverse tcp:8081` 已建。取证图全在 `mobile/e2e/artifacts/`（gitignore，不入库）。
6. **推送**：泓舟 2026-09-01 在看过完整 `origin/main..HEAD` 列表后单独授权。
   ⚠ **未推送清单只列构成、不写死条数**——本条初稿写的「共 5 个」在它自己落盘成提交的那一刻
   就变成 6 个了（B1/B2 记录里「列构成不写死条数」这条纪律，本批又踩了一次）。构成：
   本批代码 3 条（`124d152` / `1e86d4a` / `9e54407`）+ 本条记录 + 计划撰写会话 2 条
   （`3ca90e2` / `80782c3`）。
   ⛔ **push 的粒度是分支不是提交**（M4-R1 那次推 main 带走别人 33 个提交的教训）：推 main
   会一起带走计划撰写会话那两条。推前 `git fetch` 核过：`origin/main` 仍停在 `ca7a82c`
   （本轮无人推进）、ahead 里没有别的会话的提交、工作树干净。

### 6.2 第 2 批「依赖引入 + 一趟重建」（T4–T8）

（回填：开工基线 / T4 落盘与 unimodule 检查 / T5–T7 各自 jest 与提交 / T8 构建时长 + APK 体积 + 注册验证三件 + AEC patch 核证 + `lastUpdateTime` + 冒烟五格 / 反向验证 / 坑 / 遗留）

### 6.3 第 3 批「真机验收 + AEC 读数重取」（T9–T10）

（回填：`lastUpdateTime` 基线 / T9 触感四种表 + 姿态表 + **blur spike 裁决一句话** / T10 的 dumpsys 核证、S2S 覆盖面终裁、唤醒率 N/10、VAD 两判据、G2 口径收口、G5 复测、B2 遗留四格、B2 §6.4 出账表逐条核销 / 坑 / 遗留）

### 6.4 第 4 批「B3′ spike + 收口」（T11–T12）

（回填：T11 两问读数（角色可选？手势响应？）+ 分支名与提交 + 主线包重装对账 / T12 文档五处 + 全量终值 + 条数账 / **未推送清单**（列构成不写死条数）/ 设备状态逐字段对账 / 坑 / 遗留出账给 B4）
