# Android 陪伴端 App 实施计划（逐任务，M0–M3 展开 / M4–M5 触发条件）

> 状态：已批准（随主设计文档）；**本文档是执行真相源**——每个任务完成后在对应「实施记录」处
> 补一行（做了什么/读数/偏差），同 B 系列方案 §6 的惯例
> 交付对象：后续执行者（人或 Agent），假设不了解本仓库前端
> 关联：主设计文档 [`2026-08-23-hmi-android-app-plan.md`](2026-08-23-hmi-android-app-plan.md)
> （需求/选型/架构判断都在那边，**本文档不复述理由只给动作**）；协议真相源=本文档 §2 指认的源码位置

---

## 0. 接手须知（先读这节再动手）

1. **固定动作**：`python scripts/dev_stack.py target show` 确认档位（当前 `target=cloud`，
   起本地 Compose 是红线）。App 开发全程不需要本地 Docker：连的是云栈
   （`https://{TAILNET_FQDN}:8443/:8444`，FQDN 在根 `.env` 的 `TAILNET_FQDN`）。
2. **改动边界**：只许动 `mobile/`、`scripts/`（新增构建脚本）、CI 加 job、以及各任务点名的
   文档。**`hmi/` 一行不改**（共享=只读引用）；`.env`/云端环境/`AUTH_TOKENS` 的改动是
   红线动作，任务里标了「⚠ 泓舟」的步骤必须由泓舟执行或当轮授权。
3. **协议不许猜**：所有帧格式以 §2 指认的源码为准。实现时对照源码逐字段抄，
   不要按本附录的转述默写——附录可能过时，源码不会（附录每处都带 `文件:行号`）。
4. **每阶段出口必须真机**：模拟器只用于开发迭代；验收清单（各阶段末尾）在真实手机 +
   真实平板上过，且**不许只跑 happy path**（每张清单里失败态条目与正常态同权重）。
5. **计划变更纪律**：发现计划与现实冲突（库不可用/契约对不上/工作量严重失准），
   先改本文档（记录冲突证据与新决定），再改代码。
6. 任务编号 `M<阶段>-<序>`；标 ⛔ 的是**门禁任务**——它红着不许进下一阶段。

## 1. 一次性环境准备（开工前，多数须泓舟执行）

> **状态（2026-08-25）：E1/E2/E5/E6 已完成并实测通过，E3/E4 待泓舟。**
> 逐条自检入口 **`powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1`**
> ——"装过了"和"现在还好使"是两件事（PATH 被别的安装器改、JDK 被升级、Tailscale 掉线、
> 设备没插、licenses 没接受），**每次开工前先跑它**，退出码 0 才动手。
> 落地细节与三处与本表原文的偏差记在 §1.1。

| # | 事项 | 谁 | 状态与说明 |
|---|---|---|---|
| E1 | ~~装 Android Studio~~ → **只装命令行工具链**（cmdline-tools + platform-tools + SDK Platform + Build-Tools）+ JDK 17 | ⚠ 泓舟（全局安装是红线） | **✅ 2026-08-25 完成**（授权换成不装 IDE，理由见 §1.1）。装完 `adb --version`、`java -version` 可用即可 |
| E2 | 环境变量 `ANDROID_HOME`、`JAVA_HOME` 并入 PATH | ⚠ 泓舟 | **✅ 2026-08-25 完成**，用户级（同 [[windows-env-path-and-utf8]] 那次的形态），只加不删，改前原值备份 |
| E3 | 真机两台开 USB 调试：手机（Android 10+）、平板（Android 10+） | 泓舟 | **⏳ 待办**：`adb devices` 当前为空。平板没有可先只用手机，平板形态验收顺延 |
| E4 | 两台设备装 Tailscale 官方 App、登录同一 tailnet | 泓舟 | **⏳ 部分**：PC→云栈已实测通（`:8443/healthz`=200、`:8444` 有响应）；tailnet 里已有 android 节点 `superdeman-xiaomi-mix-fold-4`（折叠屏，手机态+展开态可覆盖两形态），但当前离线 |
| E5 | Node ≥ 20（已有）、npm 可用（已有） | — | **✅** node v22.15.0 / npm 10.9.2 |
| E6 | ⚠ 路径风险预案：仓库路径含中文+空格（`D:\Personal\AI\Claude Code\产品\car-agent`） | 执行者 | **✅ 已实测，且结论比本行原文更硬**：不是"不稳"，是 **AGP 硬拒绝**（§1.1 ③）。**原生构建一律经 `subst` 映射盘符跑**（M0-2 构建脚本内置：`subst X: <repo>` 后从 `X:\mobile` 起 gradle）。JS/Metro 开发不受影响 |

### 1.1 E1–E6 落地记录与三处偏差（2026-08-25）

**装了什么、装在哪**（自检脚本每条都验）：

| 组件 | 版本 | 落点 |
|---|---|---|
| JDK | Microsoft OpenJDK **17.0.20.1** LTS | `C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot` |
| SDK cmdline-tools | **23.0**（`sdkmanager --version` 报 Android CLI 1.0.15985488） | `D:\Android\Sdk\cmdline-tools\latest` |
| platform-tools | r**37.0.1** | `D:\Android\Sdk\platform-tools` |
| SDK Platform | **android-35 + android-36** | `D:\Android\Sdk\platforms` |
| Build-Tools | **35.0.0 + 36.0.0** | `D:\Android\Sdk\build-tools` |
| licenses | `android-sdk-license` 已接受 | `D:\Android\Sdk\licenses` |

用户级环境变量：`JAVA_HOME` / `ANDROID_HOME` / `ANDROID_SDK_ROOT` = 上表路径，
`GRADLE_USER_HOME=D:\Android\.gradle`（缓存不落 C 盘），PATH 追加
`jdk\bin`、`Sdk\platform-tools`、`Sdk\cmdline-tools\latest\bin` 三条。
另设 `git config --global core.longpaths true`。

**偏差 ①（E1）：不装 Android Studio，只装命令行工具链。** 本表原文写"装 Android Studio"，
但同一行自己也写了"不必开 IDE"——IDE 不在 M0–M3 关键路径上（真机为主，模拟器只是迭代用），
省 ~4GB 且少一个维护面。**代价明说**：没有 AVD 模拟器、logcat GUI、Profiler；M4 真要写原生
音频模块时可再补装，不返工。经泓舟当轮授权。

**偏差 ②（E1）：SDK Platform 装的是 35+36，不是本表写的 35。** 写计划时（08-24）
Google 已发到 **API 37**（`platforms;android-37.1` / `build-tools;37.0.0` 均已 GA）。
compileSdk 由开工时 Expo SDK 决定，不由本表决定；装 35+36 覆盖当下两种可能，
真需要 37 时一条 `android sdk install "platforms;android-37.1"` 补上即可。

**偏差 ③（E6）：非 ASCII 路径不是"不稳"，是硬拒绝。** 2026-08-25 用一个最小 AGP 工程
做了 A/B（同一个项目、只换访问路径）：

- **A 中文原路径**（`D:\Personal\AI\Claude Code\产品\...`）→ **BUILD FAILED**，
  AGP 在 apply plugin 阶段直接拒：
  `Your project path contains non-ASCII characters. This will most likely cause the build
  to fail on Windows.`（唯一逃生门是 `android.overridePathCheck=true`，**不要用**——
  它只是关掉检查，底下 NDK/CMake 的真实风险没消失）
- **B 同一项目经 `subst`** → **BUILD SUCCESSFUL in 23s**，产出 `app-debug.apk`

⇒ **subst 是必需项不是保险丝。** 顺带证实的三件事：licenses 文件对 AGP 有效
（构建期自动补装 build-tools 时打印了 `License for package ... accepted`）、
aapt2/d8/zipalign/apksigner 四个二进制都能跑（没有缺 MSVC 运行时那类故障）、
JDK 17 ↔ Gradle 8.13 ↔ AGP 8.7.3 ↔ SDK 36 这条链是通的。

## 2. 协议契约（真相源指认 + 导读）

> 本节是**导读**不是权威。权威=右列源码。App 端实现完一个面，回到源码对一遍字段。

### 2.1 端点与鉴权

| 项 | 内容 | 真相源 |
|---|---|---|
| 云端点派生 | `edge=https://{fqdn}:8443`、`audio=https://{fqdn}:8444`；fqdn 必须匹配 `^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.ts\.net$` | `scripts/dev_stack_lib.py:346-365` |
| 主链 WS | `wss://{edge_host}/ws?token={urlencode(token)}`；edge 在 upgrade 前校验 token，坏 token 直接握手失败 | `hmi/src/ws.mjs:25-29`、R3.1 |
| token 契约 | `AUTH_TOKENS` 条目 `token:user_id:vehicle_id:scope-csv`，scope 注入 `granted_scopes` fail-closed | `.env.example:271-275` |
| 记忆等敏感 HTTP | 同一 token 作 Bearer 头调 `/api/memory/*` 破坏性端点 | `hmi/src/audio.ts:8` 及 `.env.example:287-291` |
| 会话 id | App 用 `app-` + 随机 6 位，**每次启动新会话**（同 HMI 每次刷新语义）。禁用前缀名单（那些会跳过记忆抽取）：`eval-,e2e-,ctxe2e-,central-,review-,nightly-,replay-,probe-,smoke-,memtest-` | `memory/server.py:42-48` |

### 2.2 主链 WS 上行帧（App → edge-gateway）

| 帧 | 形状 | 真相源 |
|---|---|---|
| 用户请求 | `{ text, session_id, request_id, is_confirmation, operation_id?, meta: {...} }`——`request_id` 每轮新生成（uuid），网关会盖回该轮每一帧 | `hmi/src/App.tsx:691-714` |
| meta 基础键 | `answer_length / model_pref / assistant_name / memory_enabled("true"/"false") / disabled_agents(逗号csv)` | `hmi/src/settings.tsx:79-90` |
| meta 位置键 | `current_lat(toFixed 6) / current_lng / current_accuracy_m / current_location_at / current_location_source`——source 是信息性透传（编排只按键归 scope），App 填 `'app'` | `hmi/src/location.mjs:4-19`、`orchestrator/cloud/context.py:53` |
| meta 其他 | `occupant_id`（App 恒 `'primary'`，声纹是座舱端能力）、`occupant_name`（恒空）、`trace_id`（16 hex，随轮生成）、`vision_frame_id`（App 不发）；`__` 前缀键**不得上行** | `hmi/src/App.tsx:44-47,706-712` |
| 确认/取消 | `text` 就是字面 `'确认'` / `'取消'`，`is_confirmation: true`，带 `operation_id`（来自该气泡 final 的 `operation_id`） | `hmi/src/components/ChatView.tsx:498-531`、`App.tsx:695-698` |
| 打断 | `{ type:'cancel', session_id }` | `hmi/src/App.tsx:666` |
| 主动回执 | `{ type:'proactive_ack', session_id, delivery_ids: string[] }`——**呈现后才发**，这是通知合同唯一完成条件 | `hmi/src/App.tsx:562-566` |

### 2.3 主链 WS 下行帧（8 种 type，全部 JSON 文本帧）

归属规则先行（**这是 QA 轮硬化出来的语义，照抄不要改**，`hmi/src/requestRouting.mjs` 顶部注释）：
帧带 `request_id` → 按 id 归属，**对不上=丢帧**（不回落）；不带 → FIFO 头；无在飞轮的续流
→ `adopt` 新气泡。终态帧（final/error/cancelled）归属并注销该轮。

| type | 关键字段与处理 | 真相源（`hmi/src/App.tsx`） |
|---|---|---|
| `speech_delta` | `delta` 追加到目标气泡；**仅最新轮**喂 TTS | :347-368 |
| `process` | `{phase,label,summary,status,step_id,driving}` 过程区步骤；execute 步按 `step_id` 合并 running→done | :369-410 |
| `action` | `action` 追加到气泡 actions | :411-427 |
| `final` | `speech / actions / need_confirm / operation_id / closed_operation_ids[] / follow_up / ui_card / emotion / request_id`。**挂起台账服务端权威**：`closed_operation_ids` 出账、`need_confirm&&operation_id` 进账；`ui_card.type==='rejected'` 是拒识特例（气泡标灰不播报）；`emotion` 存起来供**下一轮** TTS start | :428-536 |
| `vehicle_state` | `state` 对象整体替换车况镜像，不进消息流 | :538-542 |
| `proactive` | `speech / card / advisory / priority / delivery_ids`（经 `proactiveSpeech.mjs::deliveryIdsOf` 抽取）；幂等呈现 + `proactive_ack` 回执 + 按 priority 决定播报（SPEAK/DEFER/INTERRUPT，`decideSpeech`） | :543-583 |
| `error` | `message`；硬终止：清**所有**在飞轮，**不清挂起台账**（传输错误≠挂起作废） | :585-594 |
| `cancelled` | 网关确认取消；本地刚主动 cancel 则幂等忽略，否则按 request_id 标「已打断」 | :595-606 |

看门狗：每轮一只，95s（`REQUEST_TIMEOUT_MS`，`App.tsx:52`），触发即 `dropBubble` + 气泡转超时。

### 2.4 流式 ASR（audio 网关 `/api/asr/stream`，App 用 PCM 模式）

URL：`audio_base.replace(/^http/,'ws') + '/api/asr/stream'`（`audio.ts:788-790`）。

上行：先 JSON `{type:'start', format:'pcm16le', sample_rate:16000, language, provider, model,
vad_silence_ms?, session_id?}`，随后**二进制帧**=s16le PCM（~100ms 聚包 ≈ 3200 字节），
结束发 `{type:'stop'}`（`audio.ts:941-954` + `s2sClient.mjs:41` 的聚包粒度注释）。
下行：`{type:'partial',text}` / `{type:'final',text}`（单 final 契约，迟到 final 要挡）/
`{type:'done'}` / `{type:'unsupported'|'error',message}`（`audio.ts:956-963`）。
兜底：final 前断流或 7s 无定稿 → 批处理 `POST /api/asr`，body
`{audio: base64, format, language}`，WAV 从累积 PCM 构造（`audio.ts:765-780`、
`pcmRing.mjs::int16ToWav`）。provider/model 默认值抄 `types.ts:929-931`
（`dashscope` + `qwen3-asr-flash-realtime-2026-02-10`，**模型 id 必须全小写**）。

### 2.5 流式 TTS（audio 网关 `/api/tts/stream`）

上行：`{type:'start', provider, voice, emotion?}` → N×`{type:'text', delta}` →
`{type:'finish'}`（`audio.ts:349-357,387-421`）。
下行：`{type:'meta', sample_rate}`（据此建播放器）→ 二进制 s16le PCM 分片 →
`{type:'done'}` / `{type:'unsupported'|'error'}`（`audio.ts:363-384`）。
final 收尾语义（**照抄，别简化错**，`audio.ts:405-422`）：final 全文若以已流式 accum 为前缀
→ 只补差量尾；accum 空（纯卡片回复无 delta）→ 发全文；**两段话对不上（divergent）**→
本会话按已流内容收尾，final 文本 App 端 M2 走批处理回退（HMI 的段链轮转 M2 不背，见 M2-3）。
批处理回退：`POST /api/tts`（句级 wav），引擎/音色目录探测 `GET /api/tts/stream/info`，
失败用 `types.ts::TTS_PROVIDER_FALLBACK` 渲染。

### 2.6 卡片契约与分批清单

唯一真相源 `hmi/src/types.ts:70-102`（29 型 + `card_group` 递归）。分批：

- **M1 首批 17 型**：`card_group`、`weather`、`forecast`、`poi_list`（含 `purpose:
  dest_choice/waypoint_choice` 语义）、`poi_detail`、`place_list`、`place_detail`、
  `route_plan`（含 estimate/cancelled 变体）、`intent_choice`、`reminder_list`、
  `reminder_card`（四 context）、`stock_quote`、`news_digest`、`news_brief`、`news_list`、
  `search_answer`、`search_result`（+`search_list` 顺手）。
- **M3 其余**：`research_report`、`sports_scores`、`sports_scorers`、`charging_route`、
  `trip_itinerary`、`scene_card`、`scene_list`、`vision_answer`、`payment_qr`、
  `payment_receipt`、`parking_fee`、`mcp_order`、`mcp_result`、`merchant_checkout` 族。
- **铁则：未知/未实现卡型渲染兜底卡**（type 名 + 可识别主字段 + buttons + `_prov`），
  **绝不 null**——`types.ts:152-153` 记录的「桥从 M3 就在发、HMI 渲染 null 两个月」欠账
  不许在 App 重演。`_prov` 徽章四态语义 `types.ts:60-68`。

### 2.7 共享白名单（初版台账；M0-4 落成 JSON + 守卫测试）

准入判据：零 DOM/BOM 全局（`window/document/localStorage/navigator/import.meta`）、依赖闭包
封闭于白名单内、hmi 侧已有 node:test 覆盖。

| 模块（`hmi/src/`） | App 用途 | 阶段 | 备注 |
|---|---|---|---|
| `ws.mjs` | 主链 WS 重连/队列 | M0 | 注入 RN 全局 WebSocket |
| `types.ts` | 全部数据契约 | M0 | |
| `requestRouting.mjs` | 响应归属 | M1 | |
| `pendingOps.mjs` | 确认台账 | M1 | |
| `nav.mjs` | 「第N个/换一批」序数与刷新判定 | M1 | |
| `location.mjs` | 位置 meta + 位置依赖闸 | M1 | **仅纯导出**；`requestCurrentLocation` 用 navigator，禁引（守卫测试点名断言） |
| `proactiveSpeech.mjs` | 主动消息投递凭据/播报仲裁 | M1 | |
| `pcmPlayer.mjs` | 流式 TTS 播放调度 | M2 | ctx 注入 react-native-audio-api |
| `pcmRing.mjs` | Float32↔Int16、int16ToWav | M2 | 兜底 WAV 构造 |
| `merchantUi.mjs`、`vehicleStage.mjs`、`reminderStage.mjs` | 商户卡逻辑/右面板推导 | M3 | |
| `voiceLoop.mjs`、`sileroEndpoint.mjs`、`utteranceHeuristics.mjs`、`s2sClient.mjs` | 免唤醒/S2S | M4 | 预登记，未触发不引 |

---

## 3. M0 地基（预估 1 天）

### M0-1 ⛔ 文档先行
- **做**：CLAUDE.md §2 技术栈表加行「移动端 mobile/ ｜ React Native + Expo (TypeScript)」；
  §3 目录树加 `mobile/` 条目（一句定位 + 「共享台账 mobile/shared-allowlist.json，改共享面
  两边一起看」）。
- **验收**：diff 仅这两处；不动其他节。

### M0-2 脚手架 + 构建脚本
- **做**：`npx create-expo-app@latest mobile`（TS 模板），锁开工时最新稳定 Expo SDK
  （SDK 版本写进本任务实施记录，一轮交付内不升级）；`tsconfig` strict；装 `expo-dev-client`；
  `app.config.ts`：`name: 小舟随行`、`android.package: com.xiaozhou.companion`、
  三 scheme（dev/staging/prod：dev 允许 cleartext + 任意服务器入口，prod 两者皆禁）；
  gitignore `mobile/android`、`mobile/ios`、`.expo`（CNG：原生目录不入库，
  `app.config.ts` + config plugins 是原生配置真相源）。
  新建 `scripts/build_mobile.ps1`：`subst X:`（E6 路径预案）→ `npx expo prebuild -p android`
  → `gradlew assembleDebug`，打印 APK 路径；`-Release` 参数走 assembleRelease（签名 M5 前
  用 debug keystore）。
- **验收**：`build_mobile.ps1` 出 APK 且真机可装可启动（空壳）；从原路径直接 gradle 若失败
  属预期（记录现象即可），经 subst 必须成功。

### M0-3 Metro 共享接线
- **做**：`metro.config.js`：`watchFolders=[仓库根]`、`resolver.sourceExts += 'mjs'`、
  防重复 React（`resolver.nodeModulesPaths` 只指 mobile/node_modules）；tsconfig
  `paths: {"@shared/*": ["../hmi/src/*"]}`（Expo 默认启用 tsconfig paths）。
- **验收**：`import { nextBackoff } from '@shared/ws.mjs'` 在真机 dev-client 跑通
  （console 打印一个退避值）；`tsc --noEmit` 绿（`.d.mts` 声明与 allowJs 生效）。

### M0-4 ⛔ 白名单守卫
- **做**：`mobile/shared-allowlist.json` 按 §2.7 落台账（含 phase 与 notes 字段）；
  `mobile/test/sharedAllowlist.test.ts`（jest-expo）三条断言：
  ① 扫描 `mobile/src` 全部 `@shared/` 引用 ⊆ 台账；② 台账内每个文件不含
  `window.` `document.` `localStorage` `import.meta`（`location.mjs` 例外条款：允许文件含
  `navigator`，但 mobile 源码不得出现 `requestCurrentLocation`）；③ 台账里 `phase` 晚于
  当前阶段的模块不得被引用（阶段值放 json 顶层手动推进）。
- **验收**：**反向验证两头做**——临时引一个台账外模块 → 红；临时在台账内文件塞一行
  `window.x`（改完还原）→ 红；还原后绿。实施记录里写明两次红的输出摘要。

### M0-5 core/config + onboarding 最小版
- **做**：`src/core/config/`：类型 `{preset:'cloud'|'lan'|'custom', fqdn?, edgeUrl,
  audioUrl, token}`；cloud 预设=输入 FQDN 校验 §2.1 正则后派生两 URL（**校验与派生逻辑
  写成纯函数 + 单测，测例含合法/非法 fqdn 各 3——判据与 `dev_stack_lib.py:346-365` 同构**）；
  token 存 `expo-secure-store`，其余 AsyncStorage；首启引导页（选预设→填 FQDN/URL→填
  token（显尾 4 位）→「连接测试」：开 WS `{edge}/ws?token=`，onopen 即成功随后主动关，
  握手失败展示 close code 与「检查 Tailscale/token」提示）。
- **验收**：单测绿；真机引导页完成配置后重启 App 配置仍在；错 token 连接测试给出可读失败。

### M0-6 ⛔ 主链冒烟（会话客户端最小版）
- **做**：`src/core/api/gateway.ts`：用 `@shared/ws.mjs::ResilientWebSocket`（注入全局
  WebSocket）连 config 的 edge；实现 §2.2 用户请求帧（meta 最小集：assistant_name=小舟、
  memory_enabled=true、trace_id、其余缺省）+ §2.3 帧原样落一个 dev 调试屏（滚动 JSON 列表）。
  会话 id `app-`+随机 6 位。
- **验收**：真机（Tailscale 就绪）发「今天天气怎么样」→ 调试屏先后出现
  `speech_delta`×N 与 `final`（`ui_card.type==='weather'`）；断 Tailscale 再发一条 →
  重连后该条自动 flush 送达（ws.mjs 队列语义生效的实证）。
- ⚠ token：M0 冒烟可读根 `.env` 现有 `VITE_WS_TOKEN` 值手动填入（只读不改）；
  **App 专属条目**（同 user_id、独立 token、手机档 scope 不含 `vehicle.control`）是
  ⚠ 泓舟动作：改云端 `AUTH_TOKENS` 并重启网关，安排在 M1 期间完成即可。

### M0-7 契约登记
- **做**：`docs/conventions.md` §9 追加一条「多端客户端网关契约」（编号顺延当前最大值+1）：
  声明 types.ts 为卡片/消息契约唯一真相源、归属/台账语义（§2.3 前言）为多端一致性要求、
  会话前缀 `app-` 登记；`mobile/README.md`（运行手册：E1-E6 前置、`npx expo start`、
  dev-client 安装、build_mobile.ps1、坑账见本计划 §9）。
- **验收**：两文档落地；conventions 条目末尾链回本计划。

### M0-8 CI
- **做**：`ci.yml` 加 `mobile` job（ubuntu）：`npm ci` → `tsc --noEmit` → jest（含白名单
  守卫）。**不做** gradle（时长/密钥不值得每 push）；另加 `mobile-apk.yml`
  `workflow_dispatch` 手动出 debug APK 工件。
- **验收**：Actions 两条都实跑过一次绿（`workflow_dispatch` 那条下载 APK 装机可启）。

**M0 实施记录**：（完成后逐任务补）

---

## 4. M1 对话 MVP（预估 3–4 天）

### M1-1 ⛔ 会话状态机（本阶段最重，先做）
- **做**：`src/core/session/store.ts`（zustand）：移植 `App.tsx:330-607` 的 8 型分发，
  **逐帧对照源码**；`requestRouting`/`pendingOps` 从 `@shared`；看门狗每轮一只 95s；
  dispatch 按 §2.2（含「思考中」占位、trace_id 挂气泡）。**不接 UI 先接测试**：
  jest 回放帧序列驱动 store，断言消息数组终态。
- **必测边界（每条对应一次真实事故，缺一不收）**：
  ① 带 request_id 对不上 → 丢帧（响应错挂）；② 无占位续流 → adopt 新气泡；
  ③ 两轮在飞交错（A 发→B 发→B final→A final）各归各；④ `closed_operation_ids` 出账 +
  多确认条并存；⑤ `error` 清在飞**不清**挂起台账；⑥ 本地 cancel 后网关 `cancelled` 幂等；
  ⑦ 看门狗触发后迟到 final 丢弃；⑧ `ui_card.type='rejected'` 特例。
- **验收**：8+8 用例绿；对照表（帧型→HMI 行号→App 实现处）贴进实施记录。

### M1-2 发送前置路由与位置闸
- **做**：`src/core/session/sendRouter.ts`：移植 `App.tsx:722-880` 的候选拦截
  （`intent_choice` 选项/`第N个` 序数/dest_choice/waypoint_choice/商户菜单/`换一批`翻页/
  行程句禁劫持正则），`nav.mjs` 从 `@shared`；候选记录随 final 更新（`App.tsx:480-517`
  对照）。位置闸：`@shared/location.mjs` 纯导出 + `expo-location` 实现取坐标
  （meta 键 §2.2，source='app'）；未开启定位且命中位置依赖 → 本地征询条（纯前端，
  同 HMI `pendingLocationText` 语义：同意→带坐标重发，拒绝→照发不带）。
- **验收**：sendRouter 纯函数单测 ≥12 例（每个拦截分支正/负各一）；真机「附近的粤菜馆」→
  首次触发定位征询 → 同意后出 `place_list`，随后「第二个」→ 详情或导航链路正确。

### M1-3 chat UI
- **做**：`features/chat/`：`@shopify/flash-list` 消息流；气泡态全集：user/assistant/
  pending（思考动画）/streaming（光标）/error/rejected（灰）/超时；过程区折叠条
  （process steps，execute 合并态）；**确认条按台账渲染多条并存**，点按发字面
  `'确认'/'取消'` + `is_confirmation:true` + `operation_id`（§2.2）；followUp 提示；
  trace_id 长按复制。Composer：文本输入 + 发送 + 快捷指令 chips（settings 的
  quickCommands）。
- **验收**：真机「打开车窗」（危险动作需 token 含 vehicle.control——用平板全 scope 档验，
  手机档验被拒话术）→ 确认条出现 → 「确认」执行/「取消」收尾；连续两问快发不串轮。

### M1-4 ⛔ 卡片框架 + 首批 17 型
- **做**：`features/cards/`：`CardRenderer`（switch type）+ **兜底卡**（§2.6 铁则）+
  `ProvBadge`（四态）；首批 17 型逐张实现（字段以 types.ts 为准；`card_group` 递归；
  `poi_list.purpose` 两变体按钮语义；`route_plan.estimate/cancelled` 变体文案）。
  卡内 `CardButton.send_text` 一律走普通 send（`types.ts:52-58` 语义）。
- **真栈逐族验收语句**（每族一条，全部真机跑）：天气「今天天气怎么样」/预报「未来三天天气」/
  POI「附近的充电站」/周边「附近的粤菜馆」+「看第一个详情」/路线「导航去虹桥机场」/
  澄清（说一句已知歧义句，如「打开窗」按当前 gate 语料）/提醒「提醒我明天9点开会」+
  「我有哪些提醒」/股票「茅台股价」/新闻「今天有什么新闻」/搜索「搜索固态电池最新进展」。
  未实现类型（如问出 trip 卡）必须显示兜底卡而非空白——**故意问一句「规划三天杭州行程」
  验证兜底卡出现**。

### M1-5 设置页基础
- **做**：`features/settings/`：分区=服务器（M0-5 复用，改配置→断开重连）、显示（深浅随系统/
  手动、字号）、助手（昵称/answerLength/model_pref）、能力开关（AGENT_CATALOG 全列，
  disabled_agents 生效）、记忆开关。持久化 AsyncStorage；`buildMeta` 键对照 §2.2
  实现 + 单测断言键集合与 `settings.tsx:79-90` 一致（键名硬拷贝进测试，漂移即红）。
- **验收**：改昵称后下一轮回复称呼变化；关某 Agent 后对应指令被婉拒。

### M1-6 双形态外壳
- **做**：断点=窗口短边 ≥600dp 平板 else 手机（`useWindowDimensions`，旋转即时切）；
  手机：单栏 + 底部 Composer + 顶栏（连接点/设置/车辆入口）；平板：左 chat 右上下文面板
  （M1 版：车况摘要 + 最近一张卡聚焦），横竖屏均按尺寸类；安全区 `SafeAreaView` +
  键盘 `KeyboardAvoidingView` 全屏过一遍。
- **验收**：手机竖屏/平板横屏/平板竖屏三形态截图存档；旋转中消息流不丢位置。

### M1-7 主动消息（前台）+ 车况
- **做**：proactive 帧→通知样式气泡（💡 前缀 + proactiveKind 标签 + card）；
  `deliveryIdsOf` 幂等（内存 Set，同 HMI）+ `proactive_ack` 回执；vehicle_state→store→
  平板面板 + 手机「车辆」页（M1 简版：电量/几个开关态直显）。
- **验收**：真栈造一条提醒到点（「一分钟后提醒我喝水」）→ App 前台收到 fired 卡 +
  回执后云端不再补投（重启 App 不重收）。

### M1-8 ⛔ M1 真机验收轮
§8.1 清单全过（两台设备），问题清零或立卡挂账后进 M2。

**M1 实施记录**：

---

## 5. M2 语音（预估 2–3 天）

### M2-1 采集 spike（半天定案）
- **做**：候选 A `react-native-audio-record`、B `react-native-audio-api` 的录音能力。
  判据：16k mono s16le 稳定分帧（帧间隔 ≤128ms）、两台真机 30 分钟无爆音/漂移、
  与播放并存不互踢。选定后 `core/voice/recorder.ts` 定接口
  `{ start(onFrame:(Int16Array)=>void), stop():Promise<void> }`。
- **验收**：决策 + 读数写进实施记录（**A/B 都跑过才许选**，不许按 README 星数选）。

### M2-2 ⛔ PTT 流式 ASR
- **做**：`core/voice/asr.ts` 按 §2.4：start 帧 → recorder 帧聚包 ~3200B 二进制上行 →
  松手 `{type:'stop'}` → partial 上屏输入框 / final 定稿自动 send；单 final 守卫、
  7s 兜底、断流/error → 批处理回退（累积 PCM → `@shared/pcmRing.mjs::int16ToWav` →
  `POST /api/asr` base64）。Composer 加 PTT 主按钮（hold 模式：按下开始/松开定稿；
  权限未授时按下先走申请）。设置页语音输入分区（asrProvider/asrModel/语言，默认值
  `types.ts:929-931`）。
- **验收**：真机边说边上屏（partial ≥1 次）→ 松手自动发送；蜂窝网络下重复；
  设置 `asrProvider='off'` 强制走批处理路径出文字（回退路径的正向实证）；
  说话中途杀网 → 兜底触发不挂死。

### M2-3 ⛔ 流式 TTS
- **做**：先 spike：`react-native-audio-api` AudioContext 对 `pcmPlayer.mjs` 的注入契约
  四点（`createBuffer`/`source.start(when)`/`currentTime`/`destination`）——**首日出结论**，
  缺一则改走薄 AudioTrack 原生模块（接口同 pcmPlayer 消费面，pcmPlayer 不改）。
  `core/voice/tts.ts` 按 §2.5：会话 start（带上一轮 emotion）→ speech_delta append →
  final finish（前缀差量补发；**divergent → 本会话收尾 + final 文本走批处理回退**，
  段链轮转显式不做，记 M4 观察项）；`done` 后排空播完；新请求/PTT 按下 → stopTTS
  硬停（barge-in 的 App 版：先停播再录，物理上不会自听）。设置页语音播报分区
  （开关/autoplay/引擎→音色两级 + 试听，目录探测 `/api/tts/stream/info` 失败用
  `TTS_PROVIDER_FALLBACK`）。
- **验收**：真机问天气自动播报、首音体感 <1.5s；播报中按 PTT 即停；`unsupported` 引擎
  （故意选无 key 引擎）无感回退批处理出声；关 autoplay 全静默。

### M2-4 音频焦点与中断
- **做**：来电/闹钟/其他 App 抢焦点 → 停播不崩、恢复不自动续播（重新说话即可）；
  蓝牙耳机连接/断开各一轮冒烟。
- **验收**：上述四场景真机各过一遍，现象记实施记录（PoC 承诺档=扬声器/有线为主，
  蓝牙记录现状不强修）。

### M2-5 ⛔ M2 真机验收轮
§8.2 清单全过。

**M2 实施记录**：

---

## 6. M3 卡片全量 + 车况 + 地图（预估 3–5 天）

### M3-1 其余 12+ 卡族
- **做**：§2.6 M3 清单逐张（merchant 族用 `@shared/merchantUi.mjs` 的既有逻辑：图片
  白名单、行项归一；`payment_qr` 倒计时/过期置灰/`qr_svg` data-URI 渲染——RN 用
  react-native-svg 的 SvgXml 或 WebView 单图，spike 半小时定）；每族真栈语句：
  调研「帮我调研一下固态电池」（分钟级异步→proactive 报告卡一并验）/赛事「昨天英超比分」/
  充电「导航去杭州沿途安排充电」/行程「规划三天杭州行程」+「第二天改成西湖」/
  场景「创建一个钓鱼模式…」+「我的场景」/商户「看看附近瑞幸的菜单」→选品→预览
  （**不执行支付**，到亮码卡为止，契约 §9.17）。
- **验收**：全 29 型有真实渲染截图归档（`mobile/docs/cards/` 或实施记录贴图链接）；
  兜底卡此时应只剩真正未知型才出现。

### M3-2 车况页与平板面板
- **做**：`@shared/vehicleStage.mjs`/`reminderStage.mjs` 推导逻辑复用；平板右面板=
  车况 + 今日提醒 + 焦点卡三段；手机「车辆」页完整化。
- **验收**：座舱 HMI 改车况（云栈 debug 或车控指令）→ App 镜像秒级同步。

### M3-3 地图（可降级）
- **做**：⚠ 泓舟：高德开放平台申请 Android key（绑 `com.xiaozhou.companion` + debug/release
  两签名 SHA1）。spike `react-native-amap3d`；`route_plan`/`place_detail`/`poi_list`
  卡加「地图」入口 → 地图页（marker/折线/回中）；**桥不可用或 key 未就绪 → 入口隐藏，
  卡片信息面零损失**（核心链路不依赖地图）。
- **验收**：有 key：三卡入口真机可开地图；无 key：入口不出现且无报错。

### M3-4 系统面收尾
- **做**：返回键语义（设置/地图页返回=关层，根=后台）；keep-awake 设置项；
  深浅主题全卡族过一遍（跟随系统切换即时生效）；弱网 toast 与连接状态点。
- **验收**：§8.3 对应条目。

### M3-5 Maestro e2e
- **做**：`mobile/e2e/` 三条流：①发文字→断言天气卡可见；②危险动作→确认条→取消→
  台账清；③飞行模式 10s→恢复→消息补达。本地跑通 + `mobile-apk.yml` 附带可选 e2e 步骤
  （模拟器）。
- **验收**：三条流本地稳定 3/3；CI 手动档实跑一次留 run 链接。

### M3-6 ⛔ 里程碑验收
§8.3 全清单 + 遗留问题出账（立卡进 AGENTS.md §4.2 或关闭）。

**M3 实施记录**：

---

## 7. M4 / M5（触发条件与首任务，不展开）

- **M4 进阶语音**：触发=M2 验收通过 **且** 泓舟确认手机形态需要免唤醒/S2S。首任务=
  `onnxruntime-react-native` 跑 `hmi/public/models/silero_vad.onnx` 的可行性 spike
  （复用 `sileroEndpoint.mjs` 判据），KWS 走 sherpa-onnx Android AAR。`voiceLoop.mjs`
  FSM 零改动接 RN 引擎（引擎接口对照 `handsFreeController.ts` 的注入面）。
  观察项：TTS divergent 段链（M2-3 简化处）是否在真实混合意图中造成可感知丢播。
- **M5 生产化**：触发=泓舟决定发布。范围=主设计文档 §10 三条后端线 + 厂商推送 +
  正式签名/加固 + Sentry + expo-updates 自托管 + 商店合规物料。**开工前先立独立实施计划**
  （后端三条线不属于 mobile/）。

## 8. 里程碑验收清单（复制到实施记录逐条打钩）

### 8.1 M1（两台真机）
- [ ] 文字问答流式渲染（speech_delta 逐字、final 收尾）
- [ ] 17 卡族逐族语句通过；未实现型出兜底卡（trip 语句实证）
- [ ] 危险动作确认条：确认执行 / 取消收尾 / 两条挂起并存各自可点
- [ ] 快速连发两问不串轮（M1-1 ③ 的真机版）
- [ ] 断网发消息→重连自动补发；锁屏 30s 回前台流恢复
- [ ] **失败态之后再说一句**：造一轮 error（错 token 或杀云容器不可行时用超时轮），下一轮正常问答不受影响、挂起台账未被误清
- [ ] 平板/手机三形态 + 旋转；深浅主题
- [ ] 多端并发：座舱 HMI（浏览器）与 App 同 user 同时在线，各自会话不串、记忆共享（HMI 存偏好 App 可召回）
- [ ] 主动提醒到点前台送达 + 回执不重投

### 8.2 M2（追加）
- [ ] PTT partial 上屏 ≥1 / 松手定稿自动发送 / 蜂窝网络重复
- [ ] asrProvider=off 批处理路径出文字；说话中断网兜底不挂死
- [ ] TTS 自动播报首音 <1.5s 体感；播报中按 PTT 即停；无 key 引擎回退出声
- [ ] 来电/焦点抢占四场景不崩
- [ ] 语音设置（引擎/音色/试听）持久化

### 8.3 M3（追加）
- [ ] 29 卡族截图归档；商户全流程到亮码卡（不付款）
- [ ] 车况镜像双端同步；平板面板三段
- [ ] 地图入口（有 key）或干净降级（无 key）
- [ ] Maestro 三流 3/3；CI mobile job 常绿 + APK 工件手动档可用
- [ ] 返回键/keep-awake/弱网提示

## 9. 已知坑账（开工前读一遍，踩新坑追加到这里）

1. **路径**：原生构建必经 subst（E6/M0-2），**已实测：不走 subst 就是 BUILD FAILED，
   不是"可能不稳"**（§1.1 ③ 有 A/B 读数）。Metro/JS 在原路径可用，出现诡异 watch 失败时
   同样切 subst 跑。
2. **Windows 终端**：PowerShell 5.1 无 `&&`；`PYTHONUTF8=1` 已全局（读数口径统一）；
   npm 脚本经 `npm.cmd` 解析（`Popen` 不套 PATHEXT 的老账 RC15——脚本里用绝对路径或
   `shutil.which` 形态）。
   **⚠ 写 `.ps1` 带中文注释必须存成 UTF-8 with BOM。** PS 5.1 对无 BOM 的 `.ps1`
   按 ANSI(GBK) 解码，而 GBK 是**有状态双字节**——中文注释里的高位字节一旦错位配对，
   就会把行尾的 `\r\n` 当 trail byte 吃掉，**下一行代码被并进注释、静默不执行、不报任何错**。
   2026-08-25 实测：`scripts/check_android_env.ps1` 因此吞掉 9 行，症状是
   `Get-Command adb` 那行"没跑"，读数变成"adb 不在 PATH"——一个完全误导的结论。
   判据（可机器验）：`UTF8 解码行数 - GBK 解码行数 > 0` 即中招；仓库现有 6 个 `.ps1`
   碰巧都是 0，**这是运气不是设计**。
   **镜像坑**：`.gradle` / `.properties` 反过来**不许有 BOM**——Groovy 解析器会报
   `Unexpected character: '?' @ line 1, column 1`。而 PS 5.1 的
   `Set-Content -Encoding utf8` **总是加 BOM**（PS 7 才有 `utf8` 无 BOM / `utf8BOM` 之分）。
   写文件前先想清楚**消费方是谁**。
3. **RN/Expo**：dev-client 首装后 JS 热更即可，改原生配置（app.config/插件/新原生依赖）
   必须重 prebuild+重装（对照 [[docker-rebuild-after-source-change]] 同款「改了不生效」形态）；
   `.mjs` 需 sourceExts 显式加；重复 React 报错查 nodeModulesPaths。
4. **协议**：ASR 模型 id 全小写（`types.ts:931` 注释，大写会 1011）；单 final 守卫必须做
   （qwen3 异常路径补发 final，`audio.ts:885`）；`meta` 值全 string（网关是
   `map[string]string`，塞布尔=整帧静默丢弃——切云 RC13 原账）。
5. **设备**：Tailscale 断开=全断，先查它再查代码；平板 WebView 无关（我们是 RN）但
   系统省电模式会杀后台 socket——验收在前台做。
6. **卡片**：渲染缺字段用可选链默认值，**不许因单卡异常抛崩整个列表**（ErrorBoundary
   包 CardRenderer）；`_prov.mode==='mock'` 必须醒目（数据真实性纪律）。
7. **下载源（本机实测 2026-08-25，直接决定 M0 是 1 分钟还是 1 小时）**：三条通道速度差两个
   数量级，**别用默认的那条**。
   | 通道 | 实测 | 备注 |
   |---|---|---|
   | `services.gradle.org`（gradle wrapper **默认**分发源） | **34 KB/s** | 130MB 要 ~65 分钟 |
   | `mirrors.cloud.tencent.com/gradle/` | **3.6 MB/s** | 同一个 zip 37.5 秒 |
   | AGP 构建期内置的 SDK 自动下载器 | **32 KB/s** | 缺包时它会自己去拉，慢到像卡死 |
   | `android sdk install`（cmdline-tools） | **~1 MB/s** | 同样是 dl.google.com，快 30 倍 |
   ⇒ 两条动作：① `expo prebuild` 之后**先把 `android/gradle/wrapper/gradle-wrapper.properties`
   的 `distributionUrl` 换成腾讯镜像**再跑 gradlew（M0-2 的构建脚本应内置这步）；
   ② 从 `android/build.gradle` 读出 `compileSdkVersion`/`buildToolsVersion`，
   **用 `android sdk install` 预装**，别让 AGP 在构建期现拉。
   代理帮不上忙：v2rayN 那条反而更慢（services.gradle.org 直连 10.4s vs 代理 29.7s），
   所以 `D:\Android\.gradle\gradle.properties` 里**刻意没配代理**（配了等于把构建硬绑在
   v2rayN 上，它没开就是 `Connection refused`，而这类故障看起来像"网络问题"很难归因）。
8. **cmdline-tools 23 起 `sdkmanager` 已弃用**，转 `android` CLI（`android sdk install|list|remove`）。
   两个坑：① `sdkmanager.bat` 是包装器，`"platforms;android-35"` 这种带分号的参数**会被 cmd
   拆成两个**（报 `Package platforms not found` + `Package android-35 not found`）——用
   `android.exe sdk install` 直传参数；② 新 CLI 对**已安装的包再 install 会崩**
   （`0xC0000409` STATUS_STACK_BUFFER_OVERRUN）。**它的退出码不可信，验产物不验退出码。**
9. **Windows 长路径**：gradle 中间产物路径很长
   （`app/build/intermediates/.../dexBuilderDebug/out/...`），2026-08-25 曾实测触发
   `PathTooLongException`。**当日已由泓舟以管理员改
   `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled=1` 并实证
   （379 字符路径可建可写）**，`git config --global core.longpaths true` 同日已设。
   ⚠ 这条是**本机状态不是仓库状态**——换机器/重装要重设，自检脚本 E2 有一行守它。
   注意它**不替代 subst**：LongPaths 治的是"路径太长"，subst 治的是"路径有中文"，
   两个是不同的病（§1.1 ③）。
10. **两份 adb**：机器级 PATH 上有独立的 `D:\platform-tools`（r36.0.0），SDK 里另有一份
    （r37.0.1）。**当下无害**——协议版本都是 `1.0.41`，不会互相 kill server；Expo/gradle 走
    ANDROID_HOME 那份，人手敲 `adb` 走机器 PATH 那份。自检脚本比的是**协议版本**不是包版本
    （包版本不同不要紧，协议版本分叉才会出"设备时有时无"）。哪天它报 WARN 了再处理。

## 10. 与既有体系的关系（改动禁区重申）

- `hmi/`：只读。共享模块要改（真发现 bug）→ 在 hmi 侧改 + 跑 `hmi` node:test + 本计划
  白名单守卫，两边都绿才算完。
- 后端/编排/proto：零改动。App 需要后端新能力=先回主设计文档 §10 挂账，不夹带。
- `AGENTS.md`：每完成一个 M 阶段，在 §4.1 该行更新状态一句话（不复述细节，链本文档）。
