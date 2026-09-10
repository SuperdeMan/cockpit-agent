# AR06～AR09 工程交付实施记录（2026-09-10）

> 状态：**工程交付批已实施**；AR06 的本地门禁与 release 验证入口闭合，AR07/AR08/AR09 交付的是
> **仪器与判据**，三批的产品结论都还没有数据支撑，**一格都不算签收**。
> 采用范围：[工程交付与集中验收安排](2026-09-10-android-goal-delivery-and-acceptance-plan.md) §4 的「工程交付必须尽量闭合」一列。
> 对应方案：[AR06](2026-09-09-ar06-release-validation-lint-plan.md) / [AR07](2026-09-09-ar07-kws-ab-validation-plan.md) /
> [AR08](2026-09-09-ar08-first-audio-latency-plan.md) / [AR09](2026-09-09-ar09-ui-performance-observability-plan.md)。

## 0. 一句话

**做完的是尺子，不是读数。** lint 门禁从「每次可能不一样」变成「钉在仓库里、0 error 0 warning、反向验证过会红」；
release 与 dev-client 从「同一条入口」拆成两条判据不同的轨；中文输入这条卡了 AR05 的阻塞在 prod 固定包上闭合；
AR07/08/09 各自补了**可回读、可复算、有界只读**的观测装置。唤醒率、首音时延、UI 性能三个数字，**一个都还没有**。

## 1. 代码与证据身份

| 项 | 值 |
|---|---|
| 工程批提交 | `7481cb5`（AR06 代码 + e2e 分轨）→ 本记录所属提交（AR07/08/09 装置） |
| 候选 APK | `xiaozhou-companion-prod-release-7481cb5ee-dirty-20260910-0751.apk` |
| APK SHA-256 | `f3825e282c3bdb8fb432f743fd2b4f5146911150e93e5f3cbce7b47bd9ca7ca9`（210,732,082 B）|
| 设备侧回读 | `/data/app/~~ei5tZ5aX…/base.apk` 的 `sha256sum` **与本地逐字节相同** |
| 包身份 | `variant=prod`、`build=7481cb5ee-dirty`、签名 SHA-1 `5e8f1606…`（模板 debug.keystore）、`flags` 不含 `DEBUGGABLE` |
| 设备 | test / OPPO PEUM00 / Android 14 / `919fd6f9`，`lastUpdateTime=2026-09-10 07:53:34` |
| 本地门禁 | `tsc` 0 / `eslint . --max-warnings 0` 0 / `jest` **854 passed, 80 suites**（批前 795 / 76）|
| 云端 | 本轮**未 deploy、未 status/verify**；任何真栈读数都不在本记录里 |

⚠ `-dirty` 的唯一来源是工作树里**另一个会话**未提交的 `mobile/README.md`（文档，不进 APK）。
构建脚本按 `git status --porcelain -- mobile hmi/src scripts/build_mobile.ps1` 判脏，逐条核过就这一个文件。

## 2. AR06：lint 口径、e2e 分轨、中文输入、验包

### 2.1 lint 从「不存在」到「钉住」

`npm run lint` 原来是 `expo lint`，而 **expo lint 在没有配置时会自己生成一份** ⇒ 本地一次、CI 一次可能落在不同规则集上，
「63 errors / 43 warnings」这种历史数字不可比。

落 `mobile/eslint.config.js`（基座 = 仓库锁定的 `eslint-config-expo@57.0.2` flat 导出，eslint 9.39.x），
按**运行环境**分三档补 globals（RN 源码 / Node 脚本 / Jest），`npm run lint` 改成 `eslint . --max-warnings 0`。

基线 **218 problems（70 errors / 148 warnings）→ 0 / 0**。70 个 error 全部是 React Compiler 关心的
Rules-of-React 偏离（`app.config.ts` 里 `reactCompiler: true`），逐类处置：

| 类别 | 处置 | 为什么 |
|---|---|---|
| `static-components` ×20（`voice-spike`）| 把 `Btn` 提到模块级 | 它定义在组件体内 ⇒ 每次渲染都是**新组件类型**，React 把 20 枚探针键全部卸载重挂；而 `busy` 每跑一次探针就变 |
| `refs` / `set-state-in-effect`（`map`）| SDK 初始化守卫从组件 ref 搬到模块级 | 初始化的作用域是**进程**，ref 的作用域是组件实例。**仍然是渲染期调用**——挪进 effect 会晚于 MapView 原生视图创建，正是文件头写的「白屏且零日志」 |
| `refs` + `set-state-in-effect`（`ChatScreen` / `blur-spike`）| 「模糊目标挂上没有」改成 `onLayout` 置位的真状态 | ref 回调过不了 tsc（`BlurTargetView` 把 ref 声明成 `RefObject<View｜null>`）；布局必定晚于 ref 挂载，语义同样成立 |
| `set-state-in-effect` ×6（`AssistantProvider` / `SettingsScreen` / `Composer` / `VoiceSheet` / `usePtt` / `ChatScreen`）| 改成 React 官方的**渲染期调整派生状态** | 顺带修掉一个真缺陷：换服务器后**有整整一帧**屏上还挂着上一个账号的身份摘要——AR05 明写不许发生 |
| `set-state-in-effect`（`native-spike` / `SettingsScreen`）| 「读一次 + 订阅」改成 `useSyncExternalStore`，新增 `core/session/wiredStore.ts` | 顺带去掉两处重复接线，并消灭「挂载与订阅之间的缝」 |
| `immutability`（`StageDrawer`）| `w.value = …` → `w.set(…)` | Reanimated 4.5.1 的官方入口，同一语义 |
| `purity` ×4（`usePresence` / `native-spike` / `SettingsScreen` / `ReminderSection`）| 逐行 disable + 就地写理由 | 这四处都是**时钟驱动**的读数，`now` 必须是这一帧的墙钟；存 state 会在停表期留陈旧读数，`useMemo` 会冻住 |
| `refs` ×3（`Composer`）| 逐行 disable + 理由 | RNGH 手势回调只在触摸时触发，规则无法证明「传进去的闭包不会在渲染期被调用」所以保守判红 |

`usePresence` 里两块重复的「上一次它变成现在这个值是什么时候」合并成 `useChangedAt`，
偏离的理由写在函数头：本 hook 在同一次渲染末尾调 `presenceTrail.record`，
用 setState 调整派生状态时被丢弃的那趟渲染**已经把一条用旧时刻算出来的轨迹记进去了**。

`test/**` 关掉四条规则并写清立论前提为何不成立（`jest.mock` 被 babel 提升 ⇒ import 顺序无语义；
`no-require-imports` 的立论是「align with Metro behavior」而 test 不过 Metro）。

**反向验证**：在隔离输入里注入违例，`array-type` / `no-require-imports` / `import/first` /
`exhaustive-deps` / `purity` / `refs` / `set-state-in-effect` / `static-components` **逐条判红**；
同时确认这四条在 `src/` 仍然生效（域放宽没有漏出去）。验完删除，不提交。

### 2.2 e2e：dev-client 与 release 拆成两条轨

`subflows/open-app.yaml` 变成按 `APP_LAUNCH_MODE` 分发的入口，新增 `open-app-dev.yaml` / `open-app-release.yaml`。
release 轨的判据与 dev 完全不同：按 App 自己的 scheme 冷启动、**不发任何开发深链**、
断言 dev-launcher 文案不出现。

**三个被实测逆推出来的装置事实**（都写进了对应文件头）：

1. **Maestro 2.9 里 flow 自身的 `env:` 会压过 CLI `--env`**。三条对照：顶层声明 `env:X=hello` + `-e X=override` ⇒ 假；
   子流不声明 X ⇒ 真；子流声明默认值 ⇒ 假。第一版分发器就是因为写了默认值，`-e APP_LAUNCH_MODE=release`
   被静默吞掉、照走 dev 分支，而 flow **全绿**——「通过」什么也证明不了。默认值改由 `evalScript` 在没传时才补。
2. **`launchApp` 会动权限矩阵**：不写 `permissions` 时逐条 `pm grant`（定位/相机/录音/悬浮窗…）；
   写 `permissions: all: unchanged` 反而逐条 `pm revoke`（`unchanged` 不是它认的值）。
   本机 OPPO 两边都被 `SecurityException` 拦掉，所以「没出事」是**设备拦的，不是流程对**——
   换一台能授的机器，这一步会静默改掉 AR02 的拒绝/撤销用例前提而流照样全绿。
   ⇒ release 入口改用 `openLink: "xiaozhou:///"` 冷启动，Maestro 完全不碰权限。
3. **App 已在前台时再发一条深链**，`am start` 只回「intent delivered to top-most instance」，
   而 Maestro 随后的 `waitForAppToSettle` 会卡在 `viewHierarchy` 的 gRPC 上直到 **DEADLINE_EXCEEDED 120s**。
   ⇒ 需要进设置页的流改成**冷启动直接落到 `/settings`**。

`subflows/set-dock.yaml` 建立并**回读** `uxV2Dock`（02 与 06 前提互斥，此前两条流都不建立自己的前提、靠上一次残留）。
为此给 `SettingsScreen` 的每个 `SwitchRow` 加必填 `settingKey` → `settings-switch-<key>`：
自动化此前按「标签之后第一枚开关」配对，而多行说明会把开关挤出文字带 ⇒ 点中下一行那枚，
**回读读到的也是那枚错开关的新值**，看起来完全像生效了。`diagnosticRoutes` 补了唯一性单测。

### 2.3 中文输入（A06-V04）：AR05 的阻塞解除

新增 `10-zh-input.yaml`：在 **prod 固定包**的真实 Composer 里填一条无副作用中文、**逐字锚定断言**、擦除、不发送。
在 `7481cb5ee-dirty` 上 **RC=0**。反向验证：把断言换成另一句中文即判红；`APP_LAUNCH_MODE` 传未知值时
**前提失败**而不是回落。

> 这条推翻了「Maestro CLI 未装 ⇒ 中文通道不可用」的判断：CLI 一直在本机
> `D:\Android\tools\maestro-dist\maestro\bin\maestro.bat`（2.9.0），设备 driver 也在
> （判据是 `maestro hierarchy --no-reinstall-driver` 的退出码，不是 `pm list packages` 数条数）。

### 2.4 验包与流

- `11-release-identity.yaml`：冷启动进设置页、滚到构建行、断言 `v… · prod · …` 且**不含 Metro**。
- `09-state-gallery` 首跑红在「另有 1 个待处理」。查证结论是**判据对、位置不对**：
  同一份包上 `pinCommitment(offline-with-confirm).others === 1`（单测），屏上也确实只有一个
  `dock-others` 在首屏内。⇒ 「该是几」搬进 `test/presenceFixtures.test.ts`，flow 改 `scrollUntilVisible`，
  只负责证明「真机上渲得出来」。
  ⚠ 中途一次误判值得记：我先按控制台输出判定「文案全不见了」，那是 **PowerShell 用控制台代码页
  解码了 maestro 的输出**造成的乱码，加上 `Select-Object -First 25` 截断与 `-Unique` 去重——
  三个仪器问题叠在一起，看着像产品缺陷。

### 2.5 CI（A06-5）

`.github/workflows/ci.yml` 的 mobile job 在 typecheck 与 jest 之间加一步 `npm run lint`。
零网络（eslint 与 config 都在 devDependencies，`npm ci` 已装）。
**本记录落盘时该 workflow 尚未在 CI 上实际跑过**，不得写成「CI lint 门禁已闭合」。

## 3. AR07：单变量实验装置（无任何唤醒数据）

- `core/voice/kwsProfile.ts`：`KwsProfile{id,threshold,score}`、`PRODUCTION_PROFILE`（= 生产默认，就是 A 组）、
  `EXPLORATION_PROFILES`（只动 threshold，score 恒定——单变量的那个「单」）、`validateKwsProfile`（**越界抛错，不静默夹**）。
- `KwsEngine.start(cb, keywords, profile)` + `appliedProfile()`：回读**原生实际收到**的那一组值；
  `stop()` 清空回读（上一次实验的档不许留在屏上冒充当前值）；越界档**根本不会走到原生**。
- `HandsFreeController` 的 config 接 `kwsProfile`，默认仍是生产默认；实验值只活在本次会话里。
- `core/voice/kwsExperiment.ts`：把「原生命中 / 真的进了可交互态 / 命中但 FSM 没接 / 无命中的进入」
  **四种结果分栏**，观察窗 2000ms 采样前冻结，明细有界、丢弃数可见，摘要**刻意不报唤醒率**
  （真分母是「说了几次」，机器数不出来）。没开实验时全部空转。

单测抓到两个真缺陷：① `kwsProfile` ↔ `kws` **循环 import** 让 `PRODUCTION_PROFILE.threshold` 成了
`undefined`，而 `undefined` 传到原生 `load()` 不报错、只是唤醒忽然不灵；
② 第一版按「一次原生回调一条记录」计数，结果 LISTENING 被挂到**重复触发**那条上，
原始那次尝试永远判「没进入」⇒ 成功数恒为 0 而重复数虚高。

**仍然没有**：真人 A/B、误唤醒暴露、泛化集、prod 复验。生产默认一个字没改。

## 4. AR08：端到端计时契约（无声学校准）

`core/obs/turnTimeline.ts`：一轮交互的时间线。要解决的是
`SpeechController.firstAudioMs` 两头都不是用户体感那两个时刻——它从「请求发出之后」到「首片**排定**起播」，
把 ASR 定稿、发送准备与真实出声全排除在外，所以它总是好看得多。

钉住的口径：

- 事件走**单调时钟**，墙钟只用于检索与排序；
- 每个事件带**时钟域**，跨域两点 `durationOf` 返回 `null`（不是一个看着正常的数）；
- `play_scheduled` 与 `audible_onset` 是**两个事件**，前者永远不许被写成后者；
  `attachMeasuredOnset` 是唯一能把 `firstAudioSource` 抬离 `scheduled_proxy` 的入口，且**只接受外部时基**；
- 测不到就是**没有那条 mark**，不是 0。

真实打点（不是空通道）：`usePtt`（按下/采集开始/ASR 定稿/失败）、`useHandsFree`（唤醒进 LISTENING）、
`SessionCore`（认领语音轮、挂 request/trace/bubble/operation、发送、首段有效文本、取消、失败）、
`SpeechController`（送合成、首片 PCM、排定起播、自然收尾/主动停播）。
`TtsSession` 补 `onFirstChunk`（首片 PCM 到达，与「排定起播」分开——合在一起就分不清「网关慢」和「排队慢」）。

`core/obs/latencyStats.ts`：nearest-rank 分位数（定义随报告输出），`summarize` 的入参是**尝试**不是读数，
失败/超时/没测到都留在分母里，输出带 `measured/unmeasured/nonOk/coverage`——想只报 p95 而不报覆盖率在类型上就做不到。

`timelineWiring.test.ts` 用**真的** `SessionCore` + **真的** `SpeechController` 驱动，
断言四个事件真的产生、`play_scheduled` 不会变成 `audible_onset`、停播记 `stopped` 而不是 `play_ended`。
写这条时抓到一个真缺陷：`stop()` 先把 `this.bubble` 清空再调收尾，导致「用户按了停」永远落不到任何一轮上。

**仍然没有**：声学校准装置、任何一桶的 A 基线、任何瓶颈归因。

## 5. AR09：只读有界诊断（无性能矩阵）

新增 `/turn-timeline` 只读页：显示最近 20 轮的关联 id、逐项指标（测不到打 `NOT_MEASURED`，
**不打 0ms**）、事件相对时刻；手动复制 JSON 有 256KiB 上限且截断可见。
`diagnosticRoutes` 补了一条：prod 下挂载这一页**不碰音频、不发业务**。

AR06 的渲染/生命周期修复（§2.1）同时属于 AR09 的「只修确定热点」那一档：
selector/订阅面（`wiredStore`）、渲染期 ref 读写、动画共享值写法、失效视图更新，都已经从判据层消除。

**仍然没有**：P0–P5 场景的任何读数、帧/内存/耗电基线、诊断开关的开销对照。

## 6. 后置清单（每条都能直接执行）

| item_id | 卡在哪 | 需要什么 | 已备好的产物 | 直接执行步骤 | 成功判据 |
|---|---|---|---|---|---|
| E-01 | CI lint 未实跑 | push 到 main（已授权）| workflow diff 已就绪 | push 后看 mobile job | lint 步骤绿；红了先看是不是 Linux 路径差异 |
| E-02 | 02/06 Dock 前提未真栈验 | `target=cloud` 可达 + 设备在 tailnet | `subflows/set-dock.yaml` | 分两趟：`-e APP_LAUNCH_MODE=release` 跑 02（DOCK=false）与 06（DOCK=true）| 各自 RC=0，且流末开关回读为设定值 |
| E-03 | AR07 无唤醒数据 | **真人说话** + 安静环境 | 参数入口、回读、四分栏计数器、协议 | 开实验会话 → A/B/C 各 10 次近场 → 读 `formatKwsTrial` | 每臂原始四栏齐；分母是「说了几次」由人记 |
| E-04 | AR08 无声学首音 | 外部时基取证装置 | `attachMeasuredOnset` 入口、分桶统计 | 同一时基录「说完」与「扬声器首音」→ 回填 | `firstAudioSource=acoustic` 的样本 ≥ 每桶每臂 |
| E-05 | AR09 无性能读数 | 设备时段 | 只读诊断页、指标定义 | P0–P5 按协议跑，prod 计分 | 逐场景有固定包证据；两档（诊断开/关）分列 |
| E-06 | 受限 token 负例 | 已获授权，未执行 | AUTH_TOKENS 四段格式已核 | 追加一条只带 `location.read` 的条目 → 重启云端 → 跑越权负例 | 服务端拒绝；端侧 T0 也拒绝 |
| E-07 | 旧服务端 / Redis 恢复 | 一个旧 release 环境 + 一次重启 | — | 见 AR05 §9.5 V06/V10 | 挂起恢复保真；新客户端对旧服务端不误报 |
| E-08 | 五人 UX | 参与者与授权 | [AR10 准备材料](2026-09-10-ar10-acceptance-preparation.md) | 按 §4 脚本执行 | 逐格原始记录，Agent 不代填 |

## 7. 明确没做的事

- 没有取得任何唤醒率、首音时延、UI 性能读数；三批的产品结论全部待定；
- 没有 deploy、没有 status/verify，本记录里没有任何真栈读数；
- 没有改 KWS 生产默认、没有改架构里「HMI 与 mobile 同模型同阈值」的承诺；
- 没有招募参与者、没有向任何人发送邀请；
- 没有正式签名、没有上架、没有 OTA；
- 没有把 AR02～AR05 的任何未签收项标成已签收。
