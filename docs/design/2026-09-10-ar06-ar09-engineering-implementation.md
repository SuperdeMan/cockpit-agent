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
| 本地门禁 | `tsc` 0 / `eslint . --max-warnings 0` 0 / `jest` **855 passed, 80 suites**（批前 795 / 76）|
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

**已在 CI 上实跑并通过**（run `34429460752`，head `49db9cc`）：mobile job 的步骤序列为
`Install → Typecheck → Lint (eslint, 0 error / 0 warning) → Unit tests`，四步全 success。
这一条是在**干净 checkout**（没有本地那份 gitignore 的 `.expo/types/router.d.ts`、全新 `npm ci`）
上跑出来的，所以它同时证明了「lint 口径不依赖本地生成物」。⇒ A06-V10 闭合。

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
| ~~E-01~~ | ~~CI lint 未实跑~~ | — | — | — | **已闭合**：run `34429460752` 的 mobile job lint 步骤 success |
| E-02 | 02/06 Dock 前提未真栈验 | `target=cloud` 可达 + 设备在 tailnet | `subflows/set-dock.yaml` | 分两趟：`-e APP_LAUNCH_MODE=release` 跑 02（DOCK=false）与 06（DOCK=true）| 各自 RC=0，且流末开关回读为设定值 |
| E-03 | AR07 无唤醒数据 | **真人说话** + 安静环境 | 参数入口、回读、四分栏计数器、协议 | 开实验会话 → A/B/C 各 10 次近场 → 读 `formatKwsTrial` | 每臂原始四栏齐；分母是「说了几次」由人记 |
| E-04 | AR08 无声学首音 | 外部时基取证装置 | `attachMeasuredOnset` 入口、分桶统计 | 同一时基录「说完」与「扬声器首音」→ 回填 | `firstAudioSource=acoustic` 的样本 ≥ 每桶每臂 |
| E-05 | AR09 无性能读数 | 设备时段 | 只读诊断页、指标定义 | P0–P5 按协议跑，prod 计分 | 逐场景有固定包证据；两档（诊断开/关）分列 |
| E-06 | 受限 token 负例 | **服务端半边已闭合**（见附录 A）；端到端那半仍缺 | 一台装了候选包、配上受限 token 的测试机 | 在设备上把服务器 token 换成受限那条 → 发一句车控 → 断言被拒且零动作 → 换回原 token | 端侧 T0 拒绝、云侧 dispatch 拒绝、零 VAL 调用、零挂起残留 |
| E-07 | 旧服务端 / Redis 恢复 | 一个旧 release 环境 + 一次重启 | — | 见 AR05 §9.5 V06/V10 | 挂起恢复保真；新客户端对旧服务端不误报 |
| E-08 | 五人 UX | 参与者与授权 | [AR10 准备材料](2026-09-10-ar10-acceptance-preparation.md) | 按 §4 脚本执行 | 逐格原始记录，Agent 不代填 |

## 7. 明确没做的事

- 没有取得任何唤醒率、首音时延、UI 性能读数；三批的产品结论全部待定；
- 没有 deploy、没有 status/verify，本记录里没有任何真栈读数；
- 没有改 KWS 生产默认、没有改架构里「HMI 与 mobile 同模型同阈值」的承诺；
- 没有招募参与者、没有向任何人发送邀请；
- 没有正式签名、没有上架、没有 OTA；
- 没有把 AR02～AR05 的任何未签收项标成已签收。

---

## 附录 A：受限 token 负例（E-06，已闭合服务端半边）与一次由我造成的生产回退

> 本节记录的是**真栈动作**，与上文的工程交付分开读。用户在本轮单独授权了「在 `AUTH_TOKENS`
> 追加一条只带 `location.read` 的测试身份」。

### A.1 结果

云端 `AUTH_TOKENS` 追加第二条：`<64 位 token>:uar10r:v1:location.read`（原条目一字未动）。
edge-gateway 重启后日志 `auth_required=true, tokens=2`。用该 token 查 `GET /api/session`：

| 字段 | 值 |
|---|---|
| `user_id` / `vehicle_id` | `uar10r` / `v1` |
| `authorization_source` | **`token`**（不是 `poc_default`——scope 确实来自这条 token）|
| `granted_scopes` | **`location.read`**（仅此一项）|
| `summary_status` | `complete` |
| 能力摘要 | **3 available**（`builtin-tools` / `chitchat` / `manual-rag`）/ **14 `unauthorized`，`reason_code=scope_missing`**，其中包含 **`edge-vehicle`（车端快控·车控）** |

阴性对照：不带 token 请求 `/api/session` → **401**（不是 404，路由在且鉴权生效）。

这一格闭合的是 AR05 §9.5 的 **V07 `permission.scope_missing`** 与 V02 的「新身份不继承」前提
（`uar10r` 是一个全新的 user_id）。**V05 的端到端负例仍未做**——那需要用这条 token 在真机上
发一句车控并证明它被拒，属于设备侧的活。

凭据落点：`%LOCALAPPDATA%\car-agent\artifacts\ar10-restricted-token\token.txt`（仓库外、未进 git、未打印）。
云端 `.env` 备份：`/opt/car-agent/shared/.env.bak-ar10-20260910T023653Z`（0600 root:root，**未删除**，含凭据）。

### A.2 我造成的生产回退：`RELEASE_SHA` 没设，compose 从 `.env` 取到了一个月前的值

**发生了什么**（时间为服务器本地时间）：

| 时刻 | 动作 | 后果 |
|---|---|---|
| 10:37:25 | 按 `deploy/cloud/README.md`「唯一运维命令形态」那段跑 `docker compose -f /opt/car-agent/current/compose.yaml … up -d edge-gateway` | 命令里**没有 `--project-name`**，compose 从符号链接推导出项目名 `current`（真实项目名是 `4c1f479`）⇒ 起了一套**并行栈**，10 个容器，撞 `127.0.0.1:50059` 端口失败 |
| 10:38 | 发现并逐个核对 project label 与创建时间后删除那 10 个容器与 `current_default` 网络 | 生产 30 个容器全程未受影响；无遗留卷 |
| 10:40:28 | 补上 `--project-name 4c1f479 --no-deps` 重跑 | edge-gateway 被**重建成 `car-agent-release/edge-gateway:4c1f479`——2026-08-11 构建的镜像**。原因：镜像 tag 是 `${RELEASE_SHA:?}`，我没在 shell 里设它，compose 于是从 `--env-file` 里读到了 `.env` 中陈旧的 `RELEASE_SHA=4c1f479` |
| 10:40–10:5x | 该窗口内 `/api/session` 返回 **404**（那份 8 月镜像里根本没有这个路由）| 生产能力回退约一个月 |
| 10:5x | 按 `activate-release.sh::compose_up_release` 的真实形态重跑：`env RELEASE_SHA=$(basename $(readlink -f /opt/car-agent/current)) docker compose --project-name 4c1f479 --project-directory /opt/car-agent/current … up -d --no-build --pull never --no-deps edge-gateway` | 镜像回到 `…:d425b9c2d…`；`/api/session` 恢复 401/200；`status` 5/5 |

**三条要记住的判据**：

1. **`dev_stack.py status` 没有发现这件事。** 整个回退窗口里它一直是 `5/5 healthy` +
   `release_sha=d425b9c2d…`——因为 release_sha 读的是**符号链接**，健康检查读的是 `/healthz`，
   而 `/healthz` 在 8 月那份镜像里也在。⇒ **「5/5 healthy」不能证明跑的是哪一份代码**。
   验证面里缺一条「运行中容器的镜像 tag == current 指向的 SHA」的对账，值得单独补。
2. **`deploy/cloud/README.md` §「唯一运维命令形态」的命令块会复现这个坑**：它 `cd /opt/car-agent/current`
   之后直接 `docker compose -f /opt/car-agent/current/compose.yaml …`，既没有 `--project-name`
   也没有 `RELEASE_SHA`。compose 自己会警告 “project has been loaded without an explicit name
   from a symlink”，但那行警告混在正常输出里。⇒ 该文档段应当补上这两项，或者干脆改成指向
   `activate-release.sh::compose_up_release` 的同一形态。
3. **`.env` 里的 `RELEASE_SHA=4c1f479` 是一颗留着的地雷**：它比当前 release 落后一个月，
   任何不显式设 `RELEASE_SHA` 的手动 compose 命令都会把服务换成旧镜像。本轮**没有改它**
   （授权范围只有追加 token 条目），但下一个人应当处理。

**没有发生的事**（逐条核过）：生产 30 个容器数未变、无卷被删除、`.env` 原有条目未改、
权限位仍是 `600 root:root`、其余四个端点全程 200。

---

## 附录 B：受限身份的**端到端** scope 负例——安全面成立，解释面不成立

> 装置：[`scripts/probe_session_scope.py`](../../scripts/probe_session_scope.py) —— `websockets` 直连
> `wss://<fqdn>:8443/ws?token=<受限>`，发一条普通用户帧，收全下行到 `final`，把
> 「执行了几个动作 / 播报了多少字 / 有没有卡片 / issue code」**分开**报。
> token 只从文件读（命令行会进 shell 历史与 `ps`）。
> 语料**刻意避开车控**（本轮授权只覆盖追加 token 条目），全部是只读/查询类。

### B.1 单变量 A/B：同一句话、同一分钟、只换 token

| | 受限 token（`location.read`）| 全权限 token |
|---|---|---|
| 「深圳今天天气怎么样」| **actions=0、speech_delta=0、无卡片**，final 带 `issues[0].code = planner.technical_failure`，话术「换个说法再说一次就行」| 真实天气卡（`_prov.mode=real`、`vendor=qweather`、AQI/预报齐）+ 正常播报 |

差异只可能来自 token 的 scope —— 同一栈、同一 release、相隔几十秒。**scope 闸在结果面是成立的**：
受限身份既没拿到数据，也没有编造数据。

### B.2 六次重复：`actions: []` 是稳的，**给用户的解释不是**

同一句「导航去广州塔」（该身份没有 `navigation` / `navigation.control`），连跑 6 次：

| # | actions | 话术 |
|---|---|---|
| 1 | 0 | 「好的，**已为你规划路线前往广州塔**。请查看中控屏或手机上的导航详情，按提示出发即可。」|
| 2 | 0 | 「好的，我来帮你导航去广州塔。…因为是跨城行程、路况随时变化，我没办法直接帮你算路线」|
| 3 | 0 | 「抱歉，处理失败。」|
| 4 | 0 | 「**unsupported datetime format**」|
| 5 | 0 | 「抱歉，处理失败。」|
| 6 | 0 | 「『导航去广州塔』是常见的导航指令，但我手头没有车载导航能力，无法帮你规划路线。」|

**两条结论必须分开写**：

1. ✅ **安全不变量 6/6 成立**：`actions` 恒为空数组，没有任何执行、没有挂起、没有卡片。
   缺 scope 的身份**确实控不了任何东西**。
2. ❌ **解释层不可用**：6 次 6 种说法，其中
   - **#1 谎称已经执行**（「已为你规划路线」而实际 `actions: []`）——这是本仓最不能接受的那一类，
     「说的」和「做的」直接矛盾；
   - **#4 把内部错误串 `unsupported datetime format` 原样吐给用户**；
   - **0/6 提到真实原因**。而真实原因**服务端自己是知道的**：同一 token 查 `GET /api/session`
     明明白白写着 `navigation` → `status=unauthorized, reason_code=scope_missing`（附录 A）。

⇒ **`permission.scope_missing` 这条理由，摘要面有、请求面没有。** 用户在请求面得到的是
「换个说法再试」这种会让人无限重试的引导，或者一句谎称成功的话术。

### B.3 附带取得的一个确定复现口

AR05 记录里 `planner.technical_failure` 的触发率是「2/7 不可稳定复现」。
本轮发现一个**确定的**触发形态：**受限 scope 的身份请求它没有权限的能力**。
「深圳今天天气怎么样」在受限身份下稳定落到无步骤规划。修 F09 那条降级时可以拿它当固定输入。

### B.4 归属与后续

- 归 **AR05**（结构化契约/降级理由）而不是 AR06～AR09：AR05 已经把 `permission.scope_missing`
  定义好并在 `/api/session` 上落地了，缺的是**请求路径上的同一条理由**。
- 归 **AR10** 的入场条件④：受限 token 现在真的可用了，B.2 这张表就是 X08「鉴权失效/部分成功」
  那一格的第一批原始证据。
- **未做**：车控语料的端到端负例（AR05 V05 的原题）。它要动模拟车态，需要精确到用例的单独授权。
