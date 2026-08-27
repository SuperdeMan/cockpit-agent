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
| E4 | 两台设备装 Tailscale 官方 App、登录同一 tailnet | 泓舟 | **⏳ 部分**：PC→云栈已实测通（`:8443/healthz`=200、`:8444` 有响应）；tailnet 里已有 android 折叠屏节点（节点名见本机 `tailscale status`——公开仓库不留节点名，同本文档 §卫生约定；手机态+展开态可覆盖两形态），但当前离线 |
| E5 | Node ≥ 20（已有）、npm 可用（已有） | — | **✅** node v22.15.0 / npm 10.9.2 |
| E6 | ⚠ 路径风险预案：仓库路径含中文+空格（`D:\Personal\AI\Claude Code\产品\car-agent`） | 执行者 | **✅ 已实测，定案经两次迭代**：AGP 对中文路径硬拒绝（§1.1 ③）→ subst 在真实 RN/Expo 构建里**也不可用**（双根 relativize 必炸，§1.1 偏差 ④）→ **最终形态：原生构建在 ASCII 镜像工作区 `D:\Android\builds\xiaozhou-mobile` 进行**（M0-2 构建脚本内置 robocopy 增量镜像）。JS/Metro 开发不受影响（中文路径无碍） |

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

**偏差 ④（E6 定案二次迭代，2026-08-25 M0-2 实测）：subst 从「必需项」改判「不可用」，
最终形态 = ASCII 镜像工作区。** 上面的 A/B 只覆盖**单工程最小 AGP 构建**，subst 在那个
场景够用；真实 RN/Expo 构建（Expo 57 / RN 0.86 / 新架构 codegen / worklets C++）
六次失败收敛出三条结构性事实（逐条读数在 §9 坑账 11-12）：

1. **subst 双根必炸**：`require.resolve` 不解析 subst（产 X: 路径）、RN CLI 的
   `fs.realpathSync.native` 解析 subst（产 D: 中文真实路径）——同一构建里永远两套
   盘符根，每个做 `Path.relativize` 的子系统挨个断（expo-dev-menu projectDirectory /
   codegen schema 任务 / KGP 增量编译，三处实证）。
2. **真实中文路径单根也不可用**：`android.overridePathCheck=true` 放行后，
   CMake PCH 把中文路径以错误编码写进生成头文件，clang `cannot open file`——
   **AGP 那条检查的原判被实证坐实**（上面「不要用」维持不变）。
3. junction 与 subst 同类（native realpath 同样穿透）。

⇒ 定案：`build_mobile.ps1` 构建前 `robocopy /MIR` 把 `mobile/` 增量镜像到
**`D:\Android\builds\xiaozhou-mobile`**（全 ASCII 单根，落点跟随 SDK/缓存所在的
D:\Android 惯例；`/XD android .expo` 让原生目录在镜像侧存续、增量编译不丢）。
debug 构建不读 `src/` 与 `hmi/`（JS 不打包），镜像自洽；**-Release 打 JS bundle
时此形态需再评估**（M5 前用不上，先挂账）。E6 对 Metro/JS 开发面的原判维持
（中文路径无碍，无需 subst）。

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

唯一真相源 `hmi/src/types.ts` 的 `UiCard` 联合。**实际是 34 个 type 字符串**（`UiCard` 联合 32 个成员：31 个单行 `type:` 声明 + `card_group` 内联；再加 `merchant_checkout` 那条联合里的 `merchant_choices` / `merchant_order_preview` 两个别名）。
> ⚠ **原文写的「29 型」是错的**（2026-08-27 M3-1 实测），且 M1 的守卫测试把本节清单**逐字手抄了一份**——于是清单在仓库里有了第二份，而漂掉的那份没有任何东西会红。
> 现在 `mobile/test/cards.test.ts` **从 `types.ts::UiCard` 派生**并两个方向断言，本节清单只作导读，**加卡型不需要改测试**。

分批：

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

**M0 实施记录**（2026-08-25，M0-1..M0-8 代码面全部完成；真机面待 E3/E4，清单见末条）：

- **M0-1 ✅**：CLAUDE.md §2 技术栈行 + §3 目录条目，diff 仅两处（commit `12f8036`）。
- **M0-2 ✅（构建面）**：Expo SDK **57.0.16** / RN 0.86.2 / React 19.2.3 / TS ~6.0.3，
  一轮交付内锁定。app.config.ts=CNG 真相源（名称小舟随行、包 com.xiaozhou.companion、
  APP_VARIANT 三档 dev/staging/prod、expo-build-properties 控 cleartext、config plugin
  `with-unified-drive-root`）；模板样板裁掉（.claude/、模板 AGENTS/CLAUDE/Expo LICENSE、
  app.json、demo 屏与未引用图片）。构建脚本定案形态=**ASCII 镜像工作区**（三形态
  六连败的证据链在 §1.1 偏差 ④ 与坑账 §9.11-17）：robocopy /MIR（**绝对路径** /XD）→
  镜像内 prebuild（前置 gradlew --stop 防 EBUSY）→ 腾讯 gradle 分发镜像 + jvmargs
  强制 UTF-8 → 从 libs.versions.toml 解析并预装缺失 SDK 包（NDK 27.1.12297006 745MB
  实测 3.5MB/s 三分半，AGP 现拉同物 32KB/s 要 6.5 小时）→ CN maven init script →
  assembleDebug → 验产物。**读数：首构 BUILD SUCCESSFUL in 21m48s（473 executed /
  223 from cache），app-debug.apk 254MB（debug 含 dev-client，正常量级）；复构
  14m37s**——复构慢在每次 prebuild 都会刷新 codegen/autolinking 生成物、拖着 app 层
  4 ABI 的 C++ 重编。**日常 JS 开发不走本脚本**（dev-client 热更即可），只在原生
  配置/依赖变化时重跑；「prebuild 无变化时跳过」留作后续优化观察项，M0 不做。
  真机可装可启动待 E3。
- **M0-3 ✅（本地可验部分）**：metro.config.js（watchFolders=仓库根 / sourceExts+mjs /
  nodeModulesPaths 单指 mobile + disableHierarchicalLookup）+ tsconfig paths
  （`@shared/*`、allowJs、**types 显式 [jest,node]**——TS 6.0 不再自动纳入 @types，
  这是踩了才知道的）。`tsc --noEmit` 绿；jest 侧 `import { nextBackoff } from
  '@shared/ws.mjs'` 断言退避值通过（真机 dev-client 同款验证待 E3）。jest 接线三坑：
  预设 babel transform 键是 `\.[jt]sx?$` **不含字面 js 也不含 mjs**（按「值是 babel
  转换器」找到该条给 .mjs 复用）；@babel/runtime 需显式 moduleNameMapper（转译后的
  hmi 文件从自己目录向上找不到 mobile/node_modules）；moduleFileExtensions **别覆盖**
  （jest 默认已含 mjs，窄覆盖反而丢 jsx/cjs）。
- **M0-4 ✅**：台账 16 模块（phase/purpose/notes）+ 守卫四断言（含台账健康：file 真实
  存在防 typo）。反向验证**三头**、逐条具名红：台账外引用（`cardMath.mjs (used by
  src\core\_tmp_guard_probe.ts)`）、ws.mjs 临时塞 window.x（`ws.mjs: window.`，
  git 还原后 hmi node:test 282/282 绿）、M1 模块在 M0 引用（`pendingOps.mjs (phase M1,
  current M0)`）；还原后 jest 29/29 绿。断言②去注释后扫（pcmPlayer.mjs 注释里合法
  提及 window.AudioContext，naive 子串扫会假红）。
- **M0-5 ✅（单测面）**：端点校验三判据与 dev_stack_lib.cloud_endpoints 同构（合法 3 /
  非法 7，含「整串正则过、label 校验拦」与「label 全合法、仅总长超 253」两个边界）；
  token 存入 Expo SecureStore、其余配置存入 AsyncStorage、任一缺失按未配置 fail-closed；
  onboarding（预设 chips / 连接测试给 close code+「查 Tailscale/token」/ token 显尾
  4 位；prod 档隐藏 lan/custom 由 extra.allowCustomServer 门控）。真机持久化验收待 E3。
- **M0-6 ✅（单测面）**：GatewaySession 用 @shared/ws.mjs（注入 fake WebSocket 实证
  断线入有界队列→onopen 按序 flush；URL 逐字 `wss://…:8443/ws?token=`）；用户帧
  逐字段对照 App.tsx:691-714（meta 全 string / `__` 前缀与空值剥离 / occupant 恒
  primary·空 / trace_id 16hex 同构 / operation_id 条件键）；调试屏 8 型下行帧原样
  落屏；会话 id `app-` 与记忆跳过名单互斥由测试钉住。真栈冒烟待 E3/E4。
  ⚠ token：M0 冒烟用根 `.env` 现有 `VITE_WS_TOKEN`（只读）；App 专属 AUTH_TOKENS
  条目（手机档无 vehicle.control）=⚠ 泓舟，M1 期间完成。
- **M0-7 ✅**：conventions.md **§9.33**（多端客户端网关契约：types.ts 唯一真相源 /
  归属与台账语义多端一致 / `app-` 前缀 / 鉴权与 meta 约束）+ mobile/README.md
  （运行手册：前置自检、日常开发、镜像构建、连接后端、共享面）。
- **M0-8 ✅（push 车道实跑绿）**：ci.yml `mobile` job（npm ci→tsc→jest 含守卫；
  刻意不跑 gradle）+ mobile-apk.yml（workflow_dispatch 出 debug APK 工件；ubuntu
  路径全 ASCII，不需要本机那套镜像预案）。**读数：run 32822278722（sha `4a3c829`）
  全绿，8 job 含 `mobile` success，既有 7 job 零回归**。⚠ mobile-apk 手动档**尚未
  实跑**：本机无 gh CLI、`.env` 无 GitHub token，`workflow_dispatch` 触发需要认证
  ——由泓舟在 GitHub → Actions → Mobile APK → Run workflow 点一次（其验收「下载
  APK 装机可启」本就依赖 E3 真机，与设备批次同做）。
- **待真机清单（E3/E4 就绪后一次补验，缺一不算 M0 收口）**：① APK 装机启动（空壳）；
  ② dev-client 下 @shared 退避值 console 打印；③ 引导页配置→杀 App 重启仍在→
  错 token 连接测试给可读失败；④ 调试屏发「今天天气怎么样」→ speech_delta×N +
  final（ui_card.type='weather'）；⑤ 断 Tailscale 再发一条→重连后自动 flush 送达。

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

**M1 实施记录**（2026-08-25，M1-1..M1-7 代码面全部完成；真机面待 E3/E4，M1-8 验收轮顺延到
设备批次与 M0 五条补验同做）：

- **M1-1 ✅（⛔）**：`src/core/session/store.ts`（zustand vanilla `SessionCore`，不接 UI
  先接测试）。8 型分发逐帧对照表（帧型 → HMI 行号，App 实现均在该文件 `handleFrame`）：
  `speech_delta`→App.tsx:347-368 / `process`→:369-410 / `action`→:411-427 /
  `final`（含 rejected 特例与挂起进出账）→:428-536 / `vehicle_state`→:538-542 /
  `proactive`→:543-583 / `error`→:585-594 / `cancelled`→:595-606；看门狗:609-628
  （**每轮一只** 95s）、dispatch/占位/trace 挂气泡:680-720、确认:850-876、打断:664-678、
  挂起限龄:630-640。`requestRouting`/`pendingOps`/`proactiveSpeech` 从 `@shared`
  （台账 `currentPhase` 推进 M0→M1）。测试 `test/sessionStore.test.ts`：**正常 8 +
  必测边界 8（逐条对号①-⑧）+ ⑦b 双看门狗 + 位置征询 2**，jest 回放帧序列断言消息数组终态。
- **M1-2 ✅**：`sendRouter.ts` 纯函数（**决策与副作用分离**：clear/categoryPage/withLocation
  由 store 按决策执行，node 直接单测），分支序逐条=App.tsx:722-845（trip 守卫→intent_choice
  →商户菜单→换一批→waypoint→dest→place→poi→位置闸）；`candidates.ts` 收 final 记录
  （:483-517 对照，isLatest 才记）。单测 18 例（每分支正/负 + I-007/I-032① 两个历史误伤
  反例回归）。位置桥 `core/location/appLocation.ts`：expo-location 取坐标 + 共享纯函数拼
  meta 键、source='app'；征询语义按本计划 M1-2（**拒绝→照发不带**，与 HMI「拒绝不发」
  刻意不同，后端诚实降级）。
- **M1-3 ✅（代码面）**：`features/chat/`。⚠ 踩坑：**FlashList v2 已移除 `inverted`**，
  聊天列表改 v2 范式 `maintainVisibleContentPosition.startRenderingFromBottom`。气泡态
  全集（pending/streaming/error/rejected/超时）、过程区折叠（execute 合并态在 store 已做）、
  **确认条按台账渲染多条并存**（isPendingLive；位置征询条只激活最新一条）、followUp 点发、
  trace 长按复制（expo-clipboard）；Composer=文本+快捷 chips+在飞轮「■ 打断」（cancel 帧）。
- **M1-4 ✅（⛔，代码面）**：`features/cards/`：注册表 **17+1 型** + **兜底卡铁则**
  （type 名+主字段探取+buttons+`_prov`，绝不 null）+ 每卡 ErrorBoundary（单卡异常换渲
  兜底卡，不抛崩列表）+ ProvBadge 四态（mock 醒目琥珀）。`test/cards.test.ts` 断言注册表
  与 §2.6 首批清单**集合相等**（M3 扩表时同步该测试）。真栈逐族验收语句待设备轮。
- **M1-5 ✅**：`core/settings/store.ts`（AsyncStorage 持久化、深合并向前兼容、
  `currentMeta()` 注入会话）+ `features/settings/` 六分区（服务器/显示/助手/能力开关/
  记忆定位/调试）。buildMeta 键集=settings.tsx:79-90 逐键，`settingsMeta.test.ts` 键名
  硬拷贝钉住。⚠ 踩坑：AsyncStorage 原生模块在 jest 里是 null——moduleNameMapper 接官方
  内存 mock。
- **M1-6 ✅（代码面）**：短边 ≥600dp 平板双栏（右=车况+最近卡片聚焦），
  useWindowDimensions 旋转即时切；主题 system/dark/light + 字号两档全组件走 Palette；
  SafeAreaView+KeyboardAvoidingView。三形态截图待真机。
- **M1-7 ✅（代码面）**：proactive 帧→💡 通知气泡（标题按 advisory 种类取，HMI
  PROACTIVE_LABEL 同款）+ `deliveryIdsOf` 幂等 + **呈现即回执**；vehicle_state→store→
  平板面板+手机「车辆」页。`core/session/wiring.ts` 会话单例跨路由共享——导航切页
  **不重置对话**（会话=App 启动一次，§2.1）；仅 edgeUrl+token 变化才断开重建（M1-5
  服务器分区语义）。真栈提醒到点待设备轮。
- **读数**：jest **80/80**（8 suites：M0 4 + M1 新增 sessionStore/sendRouter/
  settingsMeta/cards）、`tsc --noEmit` 0 error、白名单守卫绿（M1 五模块准入）。
  新增依赖：zustand 5.0.15 + expo-location 57.0.13 + expo-clipboard 57.0.1 +
  @shopify/flash-list 2.0.2（后三者含原生面）⇒ 按纪律重跑镜像构建复验：
  **BUILD SUCCESSFUL in 15m15s**（696 tasks：411 executed / 285 from cache），
  app-debug.apk **243MB**（新 dev-client，含本批原生模块——真机装的是它）。
- **M1-8 ✅ 真机首轮（2026-08-25 深夜–08-26 晨，MIX Fold 4 / Android 16 / HyperOS 3；
  与 M0 五条补验同轮做完）**。环境=本批 243MB dev-client APK + Metro（USB adb reverse）+
  Tailscale 直连云栈；驱动=adb（screencap+uiautomator dump+input，泓舟穿插手测）。
  **M0 五条**：①装机启动 ✓ ②共享 ws.mjs 队列语义以整链实证替代 console 冒烟
  （断网入队→重连 flush 补达，两种断法各一次：进程后台冻结杀 VPN / 飞行模式）✓
  ③配置持久化（force-stop 冷启直进对话）+ 错 token 可读失败（close code 1006 +
  「查 Tailscale/token」，不保存退出）✓ ④天气链经位置征询/坐标两路（见下）✓
  ⑤断网补发 ✓。**§8.1 清单**：流式渲染/17 族逐族（天气征询链、**forecast/place_list/
  place_detail 按真实坐标出宝安区数据**、poi_list 选站卡点选合成句、stock/news_brief/
  search_result（exa 徽章+置信 chip）/reminder_card created·fired、**trip_itinerary 走
  兜底卡=铁则真机实证**（type 名+amap `_prov`+主字段））/危险动作双确认条并存→
  逐条确认执行·取消收尾（对象=**后备箱/前备箱**）/快速连发不串轮/断网补发+失败态之后
  再说一句（挂起台账跨离线周期存活，followUp 软提醒亲证「打开后备箱还在等你确认」）/
  双形态（折叠展开实时切双栏+右栏车况全量镜像+最近卡片）/深浅主题即时切/主动提醒到点
  33s 送达+完成按钮闭环/看门狗超时轮转错误态且迟到
  final 丢弃（socket 死亡期组织性复现）。⚠ **「重启零重投」两次观察窗均无效**
  （第一次 force-stop 后 `am start` 起的是 dev launcher、JS 未跑；第二次赶上泓舟
  在用机，前台归属不明）——**不算数，挪二轮复验**（判据：chat 前台且 WS 绿点
  持续 60s 无「主动播报」重现）。**真机首轮修了两处**（commit `5c8fad9`）：
  SafeArea 缺 top（顶栏顶进状态栏）；定位 20s 上限+last-known 兜底（下条）。
  **多端并发与整轮回归复跑待第二轮**（PIN 解锁后补 weather 卡单证）。
- **⚠ 计划语料勘误（M1-3 验收句）**：「打开车窗」**不是**危险动作——`commands.yaml`
  的 `require_confirm=true` 对象是 **trunk/door_lock/fuel_tank_cover/charging_port/frunk**
  五个；车窗/天窗直通执行是**正确行为**。确认条验收语句应为「打开后备箱/打开前备箱」。
  按计划变更纪律在此记录，M1-3 原文不改（历史文本）。
- **真机轮后端观察三条（移交后端 QA，非 App 面）**：① 同一提醒被记忆抽取**重复 offer
  三次**（已创建后仍 offer——G7 准入或需「已建提醒」去重维）；② 「我有哪些提醒」报
  「接下来没有提醒」但 8/27 09:00 开会提醒明明 pending（upcoming 读侧存疑）+ 50 条
  过期存量该清；③ 「规划三天杭州行程」speech 自述「杭州**4天**行程」（餐厅日+1?）。

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

**M2 实施记录**（2026-08-26，M2-1 定案 + M2-2/M2-3/M2-4 代码面完成，语音两条链路已在
真栈上跑通并留下机器读数；**剩交互面与四场景中断待人工验收**，清单在本节末）：

- **M2-1 ✅（采集定案：react-native-audio-api 的 AudioRecorder）**。A/B **都在真机上跑过**
  才选（计划要求），读数（MIX Fold 4 / Android 16 / HyperOS 3，spike 屏 `/voice-spike`）：

  | | A `react-native-audio-record@0.2.2` | B `react-native-audio-api@0.13.3` |
  |---|---|---|
  | 帧间隔（5s） | min 36/p50 40/p95 43/max 47ms | min 97/p50 100/p95 102/max 104ms |
  | 采样率 | 16k（init 参数） | **16k（设备直接给，无需重采样）** |
  | 数据形态 | base64 字符串（每帧一次编解码 + 字符串 GC） | Float32Array（JSI） |
  | 与播放并存 | 未测 | ✅ 不互踢（录音 p50 100/max 116ms，播放同时进行） |
  | 3 分钟长测 | — | frames=1798 p95=102/**max=128**ms，**漂移 -0.21%**（179.80s vs 墙钟 180s） |
  | 维护状态 | 最后发布 2022-05，legacy bridge，靠 RN 的 interop 层活着 | 活跃（0.13.3 当日发布），新架构原生 |
  | 构建面 | `compileSdkVersion` 默认写死 27、`compileOnly 'com.facebook.react:react-native:+'`——**现在能编译是因为 expo root project 注入了 ext 版本**，注入方式一变就断 | 正常 |

  ⚠ **A 并不是"不能用"**：`NativeModules.RNAudioRecord = present`，5 秒录到 4.88s，
  帧间隔 40ms 比 B 还细。**静态取证（停更 4 年 + legacy bridge）预测它会挂，实测它没挂**
  ——所以定案理由不是"A 坏了"，是这三条：① B 同一个库还提供播放侧的 AudioContext，
  用 A 意味着**两个音频库抢同一份设备资源**；② A 每帧多一轮 base64；③ A 的构建面挂在
  一个它自己没声明的前提上。**帧间隔判据（≤128ms）两个都过，不构成区分度。**

  ⚠ **A 仍留在 `package.json` 里**（spike 屏的 `A rec5s` 是它的可复现入口）——M2-5 验收通过后移除：留一个淘汰的 legacy bridge 模块，每次构建都编译它，而 RN 哪次升级把 interop 层拿掉它就炸。留它的唯一理由是「B 万一在人工验收里翻车还有退路」。

  **判据偏差两条（如实记）**：计划写「两台真机 30 分钟」——① 平板缺席（E3 仍未办），
  只跑了手机；② 实跑 **3 分钟**不是 30 分钟。理由：爆音只有人耳能判，而**漂移可以机器判**
  （采集样本数换算的秒 vs 墙钟秒），3 分钟已足够看出有没有累积趋势（-0.21% 全在启停开销里）。
  30 分钟那条的边际信息是「有没有极低频掉帧」，留给真人对话轮顺带观察。

- **M2-2 ✅（⛔ 代码面 + 真栈上行链路实证）**：`core/voice/`（recorder/resample/asr/base64）
  + `features/chat/usePtt.ts` + Composer PTT 按钮。三条语义从 `hmi/src/audio.ts` 照抄不简化
  （各对应一次已发生的事故）：单 final 守卫 / 7s 无定稿兜底（timer 句柄留存并在 cleanup 清，
  否则陈旧会话劫杀下一轮）/ recorder 先行（不等 ws.onopen 就采集，握手窗音频攒着补发）。
  PTT 三个竞态守卫对应 MicController 原账：快按快松（pendingStop）/ 最短时长 320ms /
  并发按下。**定稿走与文本完全相同的 `core.send`**——前置路由、位置闸、候选拦截一条都不因为
  「这句是说出来的」而绕过。

  **真栈读数（spike 屏 `asr 直灌`）**：把合成好的干净音频**当作麦克风输出**喂给
  `AsrSession`，绕开麦克风只验协议面——`fun-asr-realtime` **partial×4（首个 @476ms）→
  final「今天深圳天气怎么样？」逐字一致**；`qwen3-asr-flash-realtime` partial×10
  （首个 @764ms）同样逐字一致。⇒ start 帧 / 100ms 聚包 / 二进制上行 / partial 流 /
  单 final 守卫 / 松手定稿，**在真栈上整条成立**。
  之所以要这条「不经过麦克风」的路：声学回环一次失败会同时怀疑「协议错了」和
  「麦克风音质不够」，而这两件事的修法毫不相干。

- **M2-3 ✅（⛔ 代码面 + 真栈播报实证）**。注入契约在真机上**逐条量过**（spike `inject` 项），
  三处不一致全部由 App 侧适配层吸收，`@shared/pcmPlayer.mjs` 与 `hmi/` 一行未改：

  | 探测 | 读数 | 处置 |
  |---|---|---|
  | `getChannelData()` 是否可写视图 | **true** | 本可直接 `.set()`；但第三条要求先重采样，数据本就要过 scratch，统一走 `copyToChannel` |
  | 结束回调属性名 | `onended=false` / **`onEnded=true`** | 适配层做名字映射。**赋错的那个不报错、只是永不触发** ⇒ sources 永不回收 |
  | 是否按 ctx 采样率重采样 | **不重采样**：播 1 秒 22050 的 buffer，onEnded 复测 **461ms** ≈ 22050/48000 秒 | 适配层每片重采样到 `ctx.sampleRate`；同一段经适配层复测 **onEnded @1000ms** ✅ |
  | `AudioContext` 初始状态 | **suspended** | `sharedAudioContext()` 建完即 `resume()`。不 resume 就不出声，**而且一声不吭**——没有异常也没有回调 |

  **真栈读数（spike 屏 `tts 流式`：真实 `TtsSession` 逐字 append→finish，
  同时开麦克风量能量）**：

  | 引擎 | 首音 | 整段 | 麦克风 peak |
  |---|---|---|---|
  | cosyvoice/longxiaochun_v3 | **617ms** ✅ | 5659ms | 0.311 |
  | minimax/female-tianmei（新默认） | **1881 / 1353 / 1390ms** ⚠ | ~6.0-6.6s | 0.12-0.22 |

  `same=true`（前缀差量收尾逻辑正确）。**「我听不到」不是不能验证的理由**——底噪 peak≈0.06，
  播报时 0.12~0.31，麦克风能量就是「确实出声了」的客观证据。
  ⚠ **minimax 首音 1.35~1.39s（冷启动 1.88s），贴着甚至越过计划的 <1.5s 判据**；
  cosyvoice 617ms 有三倍余量。**这是引擎属性不是 App 缺陷**——要么调判据要么调引擎，
  由泓舟定（见下「引擎偏好」）。

- **M2-4 ✅（代码面，真机四场景待验）**：`core/voice/audioFocus.ts`，`_layout.tsx` 启动装一次。
  两条处置**刻意不对称**：中断开始→停播报，**恢复不自动续播**（被电话打断后自己接着念半句
  比不念更奇怪）；`routeChange` 只在 `OldDeviceUnavailable`（拔耳机/蓝牙断）时停——
  那是 Android 的 becoming-noisy 语义，拔了耳机继续外放等于把刚才那句广播给一车人；
  设备**接入**不停，那不是隐私事件。

- **引擎偏好（泓舟 2026-08-26 当轮指示）**：ASR 主用 **fun-asr**、其次 **qwen3-asr**；
  TTS 主用 **minimax**；LLM 主用 minimax。App 侧已落：
  - 默认 `asrModel='fun-asr-realtime'`、`ttsProvider='minimax'`、`voiceId='female-tianmei'`。
    ⚠ 这三项**刻意偏离共享契约**（`hmi/src/types.ts::DEFAULT_SETTINGS` 仍是
    qwen3/cosyvoice）——改 hmi/ 是本计划禁区（§10），两边不一致是**已知的**不是漂移。
    HMI 侧要不要跟，由泓舟另定。
  - 「其次」落成**真正的模型回退链**（`AsrConfig.fallbackModel`）：主模型 error/unsupported/
    7s 无定稿 → 换模型重连 + **全量重放已录音频** + 补 stop，只重试一次。
    之所以要做实而不是只改个默认值：**批处理那条兜底在当前云栈上是 401**（下条），
    「一次说话只有一次机会」的第二次机会就只剩它。5 条单测钉住（含「换了还失败才走批处理」
    与「不无限换」）。
  - LLM provider 是后端 `.env` 的事（红线），App 不碰——**请泓舟自行确认云栈 `LLM_PROVIDER`**。

- **⚠ 移交后端 QA（不是 App 面）**：**批处理 `/api/asr` 401**——
  `Client error '401 Unauthorized' for url 'https://token-plan-cn.xiaomimimo.com/v1/chat/completions'`。
  PC 侧直连探针实测（`scripts` 外的一次性探针，读数在本条）：`dashscope/qwen3-asr` 与
  `dashscope/fun-asr` 流式都**逐字 partial→final 全对**，`mimo` 流式 final 空，
  批处理 `/api/asr` 401。⇒ MiMo 的 key 无效/过期，而**批处理是全体客户端（含 HMI）的
  ASR 兜底路径**，现在这条路是死的。App 因此更依赖上面的模型回退链。

- **声学回环（spike `e2e 回环`）**：合成→扬声器→麦克风→ASR。读数：合成 1021ms/2.32s，
  **麦克风 peak=0.247 rms=0.0256（底噪 0.06/0.005）⇒ 声音确实传到了麦克风**，
  但识别为空。⚠ **这不是链路失败**：同一段音频经 `asr 直灌` 识别逐字正确，说明协议面没问题；
  失败在声学质量（HyperOS 的 `AudioHardening ... would be muted, level: partial` 日志、
  自机扬声器→自机麦克风的近场失真、可能的 AEC）。**真人说话的验收不能用它替代。**

- **读数**：jest **133/133**（新增 3 suites + sessionStore 追加 6 条：重采样跨帧连续 /
  base64 往返 / WAV 带 LIST chunk / ASR 单 final 守卫·7s 兜底·off 主路径·模型回退 5 条 /
  TTS 收尾三分支 / SpeechSink 六个挂点）、`tsc --noEmit` 0 error、白名单守卫绿
  （`currentPhase` 推进到 M2，新增 `ttsQueue.mjs`）。镜像构建 **BUILD SUCCESSFUL 15m20s**、
  app-debug.apk **281MB**（M1 是 243MB）。

- **共享面改了一行（§10 的正当情形）**：`hmi/src/ttsQueue.d.mts` 补 `normSpeech`/`speechCovered`
  两条声明——`.mjs` 里它们一直是导出的，`.d.mts` 漏了。hmi 走 vite（esbuild 不做类型检查）
  且 package.json **没有 typecheck 脚本**，所以这缺口从没红过；mobile 跑 tsc 才撞上。只补声明。

- **M2-5 验收（2026-08-26 首轮，机器可判的部分已跑完；纯人工项待下一轮）**：

  | § 8.2 条目 | 状态 | 读数 / 说明 |
  |---|---|---|
  | PTT partial 上屏 ≥1 / 松手自动发送 | **✅ 全过**（协议面 08-26 机器验、交互面 08-27 泓舟真人验） | `asr 直灌`（绕开麦克风只验协议）：fun-asr partial×4 首个 @476ms、qwen3 partial×10 首个 @764ms，final 均逐字一致。真人按住说话（含按钮手感 / 320ms 误触门槛 / 首次权限弹窗）由泓舟验过 |
  | 蜂窝网络下重复 | ⬜ **未验** | 08-27 那轮没跑这条 |
  | `asrProvider=off` 批处理出文字 | **❌ 阻塞（后端）** | 云栈 `/api/asr` 401（MiMo key）。**这条不是 App 能修的**，等后端 |
  | 说话中断网兜底不挂死 | **✅（抓到并修了一个真 bug）** | 首跑**挂死**：松手后永久停在「识别中…」，恢复网络也不回来。根因＝换模型重连清了兜底 timer 而新 ws 在飞行模式下停在 CONNECTING（onerror/onclose 都不来）＋批处理用裸 fetch 断网时一直挂着。修后复验：松手 29 秒出「语音流连接失败」并回 idle |
  | TTS 首音 <1.5s | **✅ 已修并验证** | 旧：cosyvoice 551/617ms、minimax 1353/1390ms（冷启 1881ms）。根因与修法见下条；**换 WS 长连接并部署后 minimax 经云栈 516~563ms**，2.7 倍余量 |
| 播报听感 | **✅** | 泓舟 08-27 真机验：minimax 语音正常，播报中按 PTT 立刻安静 |
  | 播报中按 PTT 即停 | **✅ 物理面实证** | `barge-in` 探测：播报中麦克风 peak=0.145 → stop 后 250ms=0.024 → 稳态=0.004。**不是「代码调了 stop」而是声音真的没了**（调了 stop 但已排定的分片继续播完，是这类调度器的典型失败形态） |
  | 无 key 引擎回退出声 | ⬜ **未验** | ⚠ 现在有现成的无 key 引擎可用：**mimo TTS 四次探测全 error**（与 mimo ASR 401 同源，MiMo key 失效），选它即可验回退 |
  | 来电/焦点抢占四场景 | ⬜ **未验** | M2-4 代码已落，四场景需真实事件；08-27 那轮没跑 |
  | 语音设置持久化 | **✅** | 设置页选 CosyVoice → force-stop → 冷启 → 探测报 `cosyvoice/longxiaochun_v3`（引擎切换联动 voiceId 也对）。设置页两分区渲染正确，minimax 七个音色带中文名（来自云栈目录探测） |
  | 平板形态 | ⬜ | E3 仍未办 |

- **minimax 首音慢在哪（2026-08-27，泓舟指示「保持 minimax 但先查慢在哪」）**。
  PC 侧直连网关分段量，把 App 完全排除，再把喂法拆成两种：**A 一次性发全文**（引擎地板）
  与 **B 逐字 50ms append**（speech_delta 的真实节奏）。

  | 引擎 | A 首音 | B 首音 | A→B 的差 |
  |---|---|---|---|
  | cosyvoice | 500/531ms | 625/562ms | +~60ms |
  | **minimax** | **406/407ms（四家最快）** | **1500/1453ms** | **+1070ms** |
  | qwen | 625/547ms | 797/781ms | +~200ms |
  | mimo | error ×2 | error ×2 | — |

  **决定性对照**：同一句话把第一个句末标点从第 18 字挪到第 6 字（`今天深圳晴。气温…`），
  minimax B 首音 1468→**688ms**，而 cosyvoice **纹丝不动**（563→562ms）。

  ⇒ **根因是网关两个 provider 的传输形态不同，与 minimax 引擎无关**：
  - `MiniMaxStreamingTTSProvider` 是 **HTTP-per-sentence**——`async for seg in
    _sentence_segments(...)` 每吐一整句发一次 POST，所以首音 = 第一个句末标点到达时刻
    + 引擎首帧 ~300ms。而 `_SENTENCE_END = "。！？!?…
；;"` **不含逗号**，
    「今天深圳晴，气温二十八度，适合出门。」要等到第 18 字才断句。
  - `CosyVoiceStreamingTTSProvider` 是 DashScope 的 **WS run-task 长连接**，
    文本增量直送、引擎边收边合成，所以它对句号位置不敏感。

  **修法（泓舟 2026-08-27 授权，已实现待部署）**：不是「改切分阈值」而是**换传输**——
  新写 `MiniMaxWsStreamingTTSProvider` 走 MiniMax 的 T2A **WebSocket**
  （`wss://api.minimaxi.com/ws/v1/t2a_v2`），一条长连接内分片直送。
  真栈验证（直接调 provider、同一句、同一逐字 50ms 节奏）：
  **WS 首音 954ms / HTTP 2609ms（37%，省 1655ms）**，音频 4.02s 完整。
  `MINIMAX_TTS_TRANSPORT=http` 可一键退回旧形态——换传输是首音优化，不该是单程票。

  协议**逐条实测取证**（官方文档没写认证方式，不许猜）：`Authorization: Bearer` 走
  header；`connected_success → task_start → task_started → N×task_continue →
  task_finish → task_finished`；音频是 `data.audio` 的 **hex**。两个坑写进了源码头注：
  ① **`is_final` 是段级不是任务级**（实测首段 406ms 就 IS_FINAL，其后还有 200+ 条音频帧，
  拿它收尾会把话截断一大半——首轮探针就这么读错过一次）；
  ② **逐字发 `task_continue` 是错误用法**（音频总长翻倍 7.57s vs 3.98s，且撞
  `rate limit exceeded(RPM)`），所以仍走 `_sentence_segments`，只是开 `soft_break=True`
  ——长连接下细分段不额外建连，首音因此能贴着第一个逗号。
  **已部署并验证（2026-08-27，云端 release `7ac2176`，5/5 端点 healthy）**：同一句、同一逐字 50ms 节奏、经云栈测四趟——**首音 516 / 547 / 547 / 563ms**（部署前 1453 / 1500ms），音频 3.83~4.01s 完整。**比 cosyvoice（563~594ms）还快一点**，计划里的 <1.5s 判据现在有 2.7 倍余量。
  → 原本记在 M2-5 表里的「minimax 首音贴线」那条**已消失**，不再需要泓舟定取舍。

  **这一轮最有价值的产出不是「都过了」，是那个断网挂死**——它只在「失败态之后」出现，
  happy path 与单测都证明不了会话状态是对的（CLAUDE.md §6 的那条纪律，又一次实证）。
  回归测试已按纪律反向验证：移除那行修复即红。

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

**M3 实施记录**（2026-08-27，A 批代码面完成 + 真机验证第一轮；**原生依赖重建（B 批）与地图（M3-3）未做**）：

- **⚠ 先纠一个数：§2.6 写的「29 型 + card_group」是错的，`hmi/src/types.ts` 实际 34 个 type
  字符串**（31 个单行声明 + `card_group` 内联 + `merchant_choices`/`merchant_order_preview`
  两个别名；`UiCard` 联合 32 个成员）。M1 已实现 18 个，**M3-1 补 16 个字符串 / 14 个渲染器**。
  这个数错了不止是笔误——**M1 的守卫测试把 §2.6 的清单逐字手抄了一份**，于是「清单」在仓库里
  有了第二份，而漂掉的那份**没有任何东西会红**。⇒ `test/cards.test.ts` 改成**从
  `types.ts::UiCard` 联合派生**，两个方向都断言（缺=漏实现、多=注册了后端不会发的型）。
  反向验证两头都做过：摘掉 `vision_answer` → 红；多注册一个 `bogus_card` → 红；还原即绿。

- **M3-1 ✅（代码面）**：14 个渲染器落 4 个文件——`infoCards`+3（research_report /
  sports_scores / sports_scorers）、`navCards`+2（charging_route / trip_itinerary）、
  `miscCards`+3（scene_card / scene_list / vision_answer）、**新增 `merchantCards.tsx`**+6
  （payment_qr / payment_receipt / parking_fee / mcp_order / mcp_result / merchant_checkout）。
  商户族全部复用 `@shared/merchantUi.mjs`（品牌归一 / 行项归一 / 分转元 / 按钮合成 /
  规格 chip 句式 / 图片与支付链接白名单），**App 侧只做渲染**；白名单 `currentPhase` 推进到 M3。

- **M3-2 ✅（代码面）**：`VehiclePanel` 重写为三格指标（复用 `@shared/vehicleStage.mjs::stageMetrics`）
  + 明细镜像；新增 `ReminderSection`（复用 `@shared/reminderStage.mjs::groupByDay`）；
  平板右面板改成**车况 / 提醒 / 焦点卡**三段。提醒段的数据源刻意取「消息流里最近一张
  `reminder_list` 卡」而不是另起一路查询——**App 与后端之间只有主链一条通道，右面板自己去拉
  一遍等于凭空多一个会漂的真相**。

- **M3-4 部分**：keep-awake 设置项（`expo-keep-awake` 已随 expo 传递依赖装好，**不需要重建**）
  + 弱网提示条（**延迟 3 秒**才显示：重连本就是常态，每次都弹会把「正常自愈」渲染成「出事了」，
  用户学会忽略之后真断网也没人看了）。**返回键语义与深浅主题全卡族尚未在真机过**（下方「仍未做」）。

- **新增卡片画廊调试屏** `/card-gallery`（与「主链帧调试屏」「语音 spike」同类，设置页 §调试 入口）。
  它不是锦上添花，是两条验收要求的**唯一可行落点**：① §8.3「全卡族截图归档」——一部分卡真栈
  够不着或**不该**够（`payment_receipt` 要真付款，而契约 §9.17 明说系统不执行最终付款 ⇒ 它在
  验收里**永远**只能靠样本）；② M3-4「深浅主题全卡族过一遍」。**屏内每条都标「真栈已验/样本」**
  ——样本截图只证明渲染器对这份数据的输出长这样，不证明后端会发这样的数据，两者混为一谈
  正是验收造假的常见形态。配套守卫：注册表卡型必须都有样本（**第一次跑就抓到 `merchant_checkout`
  本名没样本**——两条商户样本用的都是别名，而注册表是按字符串查的）。

- **真机读数（MIX Fold 4 / 云栈 `target=cloud`，2026-08-27 01:1x–02:0x）**。
  驱动法沿用坑账 §9.20③ 的「临时验收 chips」（`adb input text` 不支持中文），验收后
  `git checkout Composer.tsx` 已还原。

  | 卡型 | 结果 | 读数 / 说明 |
  |---|---|---|
  | `sports_scores` | ✅ 真栈 | 「英超 · 08-26」+ **空 fixtures 分支**（暂无比赛安排）+ 数据来源角标 |
  | `scene_card` | ✅ 真栈 | `confirm` 态「待确认」+ 编号步骤 + **step1「需确认」danger 角标**；点确认→`created`闭环 |
  | `scene_list` | ✅ 真栈 | 「我建的·2」含刚创建的钓鱼模式 + 「内置·3」+ 步数 |
  | `research_report` | ✅ 真栈 | 分节手风琴 + `high` 置信 + 「引用 [1][3][4][6][9][12]」+ **未覆盖 gaps**（琥珀）+ 编号信源 |
  | `trip_itinerary` | ✅ 真栈 | D2/D3/D4 分天配色 + 每日天气「小雨 26-32℃」+ 点数 + 🏛 图标 + **接地点才给「导航」按钮** + 顶部补电摘要 |
  | `mcp_result`(readonly) | ✅ 真栈 | 品牌胶囊「麦当劳」+「商户信息」+ prov `mcdonalds·17:34` + 来源 `list-nutrition-foods`；**刻意极简**（无订单号/状态/按钮）符合 I-022 |
  | `parking_fee` | ✅ 真栈 | 「当前停车费 15元」+ 订单 |
  | `place_list` 看菜单 | ✅ 真栈 | **本轮新补的能力**（下条） |
  | `charging_route` | ⬜ 未达 | 两次尝试都停在异步「正在查询沿途充电桩并规划路线」超 2 分钟未回卡（后端行为）。**渲染器只在画廊样本上验过**（含「全程无需补电」空 stops 分支） |
  | `sports_scorers` | ⬜ 未达 | 「英超射手榜」被路由到 info 检索域，出的是 `search_result`。**落域结果不是 App 缺陷**，渲染器只在样本上验过 |
  | merchant / payment 族 | ⬜ 未达 | **被营业时间挡住**：验收时刻 01:30，附近瑞幸全部 07:00-18:00 已打烊（后端如实回「门店已打烊」），附近无麦当劳。渲染器只在样本上验过 |
  | `vision_answer` | ⬜ 未达 | 需 M4 P4 视觉开关（opt-in 默认关），本轮未开 |

- **本轮修的三处（都是真机逼出来的，不是计划里写着的）**：

  1. **`place_list` 缺「看菜单」直达**。HMI 的 `PlaceListCardView` 对瑞幸/麦当劳门店经
     `placeMenuAction` 补一个直达按钮，App 的 M1 版没有 ⇒ **「发现→看单→点单三步全可点按」
     那条链在手机上是断的**。已补（句式仍由共享模块决定，App 不自己拼），真机复验每家门店
     都出按钮。

  2. **freshness 直显 ISO 原文**。三张卡上都出现（`2026-08-25T01:56:00.000Z` 这样的串当角标）。
     HMI 走 `relativeTime()`，但那函数住在 `Cards.tsx` 里、**不是共享模块**，搬出来要动 hmi
     五处调用点。⇒ 判定：**§10 的例外是「共享模块有 bug」，而这里 hmi 是对的、错的是 App**，
     所以在边界内实现（`parts.tsx`），并把「这是第二份实现」写在函数注释与
     `test/cardParts.test.ts` 的头部，阈值逐条钉死（60s/60min/24h/30d + mock/NaN 判空）。
     **要收敛得先把它提成共享模块 + node:test + 台账，那是一次独立的共享面改动，挂账给泓舟裁。**

  3. **⚠ 最重要的一条：`CardBoundary` 兜不住原生组件缺席，整屏红屏。**
     打开画廊时 dev-client 直接崩到红屏：
     `com.facebook.react.uimanager.IllegalViewOperationException` @
     `ViewManagerRegistry.get(RNSVGSvgViewAndroid)` —— 因为 JS 已经引了 `react-native-svg`
     而设备上是 M2 那版 APK（原生侧没有它）。
     **根因不是「忘了重建」，是判据边界**：那是 Fabric 在**挂载期、原生线程**抛的异常，
     不是 React 渲染异常，`getDerivedStateFromError` **根本不会触发** ⇒
     **「未知/异常卡型不许抛崩整个列表」这条铁则只覆盖 JS 侧**。
     修法＝`merchantCards.tsx` 顶部用 `TurboModuleRegistry.get('RNSVGSvgViewModule')` 探测
     原生在场（拿不到就是没链接进来，零副作用），不在场就走契约里**本来就有**的
     「无码→安全支付链接」降级分支。
     ⚠ 同时补一条**诚实措辞**：`hasQr=true` 但渲不出时，`paymentPresentation` 仍是扫码档
     （title=扫码支付 / hint=请用手机扫码），屏上却没有码——不说明就是**让用户去扫一个不存在的
     东西**。已加显式提示（「本机暂时无法显示二维码，请用上面的安全支付链接」）。
     ⚠ 这条**不是一次性的开发期补丁**：M5 规划的 expo-updates OTA 同样只推 JS 不推原生，
     那时新增任何原生支撑的卡片都会以完全一样的形态炸掉整屏。

- **移交 / 挂账（都不是 App 能修的，也都不在本批范围）**：
  1. **RN 的 `URL` 实现把路径与查询串里的 `@` 读成 userinfo**
     （`get username()` 的 `[^:@]+` 不在路径分隔符处停）⇒ `merchantUi.safeHttpsUrl` 会把
     `https://host/x?e=a@b.com` 判成「带凭据」拒掉，而浏览器上同一个 URL 正常放行。
     **方向是 fail closed（拒绝而非放行），不是安全漏洞**；代价是 `pay_url` 含 `@` 时 App 会说
     「支付入口暂不可用」——**那是一句假话**；商品图含 `@`（国内 CDN 做尺寸变体的常见惯例）则静默不显示。
     仓库里的样本 URL 都不含 `@`，**真实商户 URL 含不含我没有观测数据**。已在
     `test/merchantCards.test.ts` 把差异**钉成可见的测试**（RN 拒 / Node 放行两条都断言），
     真机一旦观测到含 `@` 的商户 URL 即升级为待修（修法＝改判 authority 段而不读 getter，两端都对）。
  2. **澄清按钮的 `send_text` 会丢掉原句的位置依赖性**。`导航去杭州东站，沿途安排充电` 命中
     `ORIGIN_TERMS` 的「导航」⇒ 带坐标；而 `intent_choice` 选项回发的
     `规划去杭州东站的长途充能策略`（导航/补电/行程规划三个词一个不沾）⇒ 闸判 false、不带坐标，
     后端于是回「需要您的当前位置」。**共享闸 `location.mjs`，HMI 同样如此，不是 App 缺陷。**
  3. `trip_itinerary` 对「规划**三天**杭州行程」产出**四天**——与 M1 交接里已记的
     「行程天数自述 4 天」同一条，仍在 QA 侧。
  4. 已确认 Expo CLI 会加载 `mobile/.env.local` 并 export `AMAP_ANDROID_KEY`（Metro 启动日志
     `env: load .env.local` / `env: export AMAP_ANDROID_KEY`），M3-3 的 key 通道成立。

- **读数**：`tsc --noEmit` **0 error**；mobile jest **174/174**（M2 末 133 → **+41**：
  卡片派生守卫 4 + 画廊覆盖 3 + `merchantCards` 14 + `cardParts` 12 + `reminderSection` 7，
  另 1 条来自 M1 守卫拆分）；
  hmi `npm test` **285/285**（改了 `merchantUi.d.mts` 后按 §10 复跑）；白名单守卫绿。

- **共享面改了一处（§10 的正当情形）**：`hmi/src/merchantUi.d.mts` 补
  `swapStoreAction`/`specChipAction`/`placeMenuAction` 三条声明——`.mjs` 一直导出它们、`.d.mts` 漏了。
  hmi 走 vite（esbuild 不做类型检查）且 package.json 没有 typecheck 脚本，所以这缺口从没红过；
  mobile 跑 tsc 才撞上。**与 M2 那次 `ttsQueue.d.mts` 同一形态，只补声明、实现一行未改。**

- **真机第二轮（2026-08-27 上午，泓舟解锁 + 关自动锁屏后跑完）**：

  | 项 | 结果 | 读数 |
  |---|---|---|
  | 画廊全卡族（暗色）| ✅ | 40 条样本 / 注册 34 型 / 「注册表卡型已全部有样本」；`charging_route` 两分支、`trip_itinerary` 未接地点不给导航按钮、`vision_answer` simulated 角标、商户选品与预览（规格 chips 选中态 + 「大杯 +3元」+ 优惠/实付 + `swapStoreAction`）、order 态三按钮、`mcp_order` 演示角标 + 降级 prov、`card_group` 递归、**兜底卡**逐张确认 |
  | `payment_qr` 三分支 | ✅ | 有码降级（**「打开安全支付链接」按钮确实在上面**，提示是真话）+ 活的倒计时「03:17 后过期」/ 已过期只说过期 / 无码走安全链接档 |
  | 深浅主题全卡族 | ✅ | `cmd uimode night no` 切浅色，**同一挂载实例实时重渲染**（倒计时 03:17→02:40 连续，不是重启）；sports 计分板/时间线、merchant 虚线与 chips、fallback 卡在浅色下对比度均可读 |
  | `relativeTime` 上屏 | ✅ | 「31分钟前」「11分钟前」，不再是 ISO 原文 |
  | 平板/展开态三段面板 | ✅ | `cmd device_state state 3` 强制展开（跑完 `reset`）：双栏 + **车况三格是真实推导**（真栈 vehicle_state 72% × `RANGE_FULL_KM` 550 = 396km，与 `vehicleStage.mjs` 逐字对上）+ 提醒段空态引导语 + 焦点卡段；手机态的「车辆」入口在平板态正确隐藏 |
  | 返回键：二级页 | ✅ | 画廊返回 = 关层回对话根屏，应用仍在前台 |
  | **返回键：根屏** | **❌ 与计划不符** | 见下条 |

- **⚠ 「根屏返回=后台」这条计划假设被实测推翻**（需泓舟拍板）。计划写「返回键语义（设置/地图页返回=关层，**根=后台**）」，那是按 Android 12+ 根 Activity 的 back-to-home 优化写的。**这台机器（HyperOS / Android 16）没有那个行为**：
  - 从桌面图标启动（App 是任务根）→ 根屏按返回 → `MainActivity` 被 finish，进程转 `cch-rec`（cached）；再打开是同一个 pid 但 **JS 上下文重建、会话消息清空、WS 重连**。
  - 判据取证做了两遍：先用 deeplink 起（那次 App 不是任务根，结论不作数），换 `monkey -c LAUNCHER` 重测才坐实。**启动方式会改变返回键语义，测这条必须走真实入口。**
  - 后果：陪伴端在用户按返回后**停止接收前台主动提醒**。⚠ 但这与 PoC 现有承诺并不矛盾——坑账 §9.5 早写了「系统省电模式会杀后台 socket，验收在前台做」，前台交互档本来就是 PoC 的档位。
  - **不擅自修**：RN 不暴露 `moveTaskToBack`，纯 JS 的做法是发一个 `intent://...category.HOME` 把桌面拉到前台（hack，且改变任务栈语义），要做实得进原生模块；而**真正的修法是 M5 的前台服务 / 厂商推送**（主设计文档 §10 已挂账）。⇒ 三个选项请泓舟选：① 维持现状并把「前台档」写进产品承诺；② M3 内加 intent hack；③ 并入 M5 一起做。

- **画廊加了 `?only=` 过滤**（`xiaozhou://card-gallery?only=payment_qr` / 逗号分隔多个 / 子串匹配）。加它是被逼的：全表 40 条时 adb 滚动定位**极不可靠**——慢拖（700ms）会被卡内 Pressable 吃掉、快扫（260ms）带惯性一次跨 3 条，同一条指令时灵时不灵，为定位一张卡耗掉十几轮。**取证屏就该能直达要取的那一条。**

- **B 批：原生重建 ✅ + M3-3 地图 spike ✅（2026-08-27 下午）**。**一次构建带上
  `react-native-svg` 与 `react-native-amap3d`**（每趟镜像构建 ~24 分钟，分两次不值当）：
  `BUILD SUCCESSFUL in 24m 6s`、APK **325MB**（M2 是 281MB）、`adb install -r` Success。

  - **`react-native-svg` ✅ 端到端通**：付款码三条分支真机复验——有码那张渲出**真二维码**
    且降级提示自动消失（`qrBlocked` 转 false）、过期那张置灰 + 「已过期」角标、无码那张走
    安全链接档。**上一轮那条「本机暂时无法显示二维码」的守卫，这一轮自动让位**，
    说明守卫判的是「原生在不在场」而不是写死的降级。

  - **`react-native-amap3d` 的定性被实测改了两次，两次都是我先判悲观**：
    1. 先判「`compileOnly 'com.facebook.react:react-native:+'` 是 RN 0.71 前的老坐标，
       必炸」——**错了**：RN 0.86 的 gradle 插件**仍然做 `react-native` → `react-android`
       的依赖替换**（`DependencyUtils.kt:135` 的注释直接点名这个场景）。**差点据此否掉一个
       其实能用的库。**
    2. 再判「`compileSdkVersion getExt('compileSdkVersion', 33)` 会回落到 33、撞 androidx 34+」
       ——这条**是真的**（Expo 57 根 build.gradle 不设这些 ext，坑账 §9.16），但修法是通用的：
       init script 里补 `rootProject.ext` 的四个 SDK 版本，值由 `build_mobile.ps1` 从
       `libs.versions.toml` 解析后 `-P` 传入。**不是给某个库开后门——任何按约定读根 ext 的
       老库都会受益，且值来自本机真实安装的那套，不是又写死一份。**
    ⇒ 结论：**库能编译、能挂载、Paper ViewManager 在 Fabric interop 下正常工作**
    （`ReactNativeFeatureFlagsDefaults.useFabricInterop()` 默认 `true` 那条预判是对的）。

  - **⚠ 地图现在停在一处只有泓舟能解的地方：`Key验证失败：[INVALID_USER_SCODE]`**
    （logcat `amapsdk` 标签）。高德这个码的含义是**包名 + 签名 SHA1 的组合与该 key 注册的不符**。
    我们的 APK 是 **debug 签名**，从 APK 本体读实（`apksigner verify --print-certs`，
    不是从 keystore 推断——本机有两个 debug.keystore，推错就给错指纹）：
    - 包名：`com.xiaozhou.companion`
    - **SHA-1：`5E:8F:16:06:2E:A3:CD:2C:4A:0D:54:78:76:BA:A6:F3:8C:AB:F6:25`**
      （即 RN/Expo 模板自带的 `android/app/debug.keystore`，**不是** `~/.android/debug.keystore`
      那把 `BA:53:6B:57:…`）
    **✅ 泓舟当日在控制台加完，地图随即打通**（无需重建、无需改代码——只重开一次应用
    让 SDK 重新鉴权）：真实高德瓦片渲染、**两个 marker 落在给定坐标上**（深圳宝安西乡）、
    「回中」实测（先拖走再点，画面回到初始构图）、底部信息条「周边 · 咖啡 / 2 个点」、
    左下角高德 logo（合规要求）、浅色主题下 header 与底栏正确跟随。
    logcat 里 `Key验证失败` 一行不再出现。
    ⚠ 顺带一条 M5 账：模板 debug keystore 是**全世界 RN 项目共用的那一把**，
    正式签名必须换（M5 生产化项），换了要在高德控制台同步加新 SHA1。

  - **另一处实测改了我自己的设计决定**：我最初在 `app.config.ts` 里**刻意只透传布尔、
    不透传 key**（想把 key 留在原生侧）。实测地图灰屏且 **logcat 零输出**，查了三轮才定位：
    库的 `initSDK` 是 `apiKey?.let { ... }`（`SdkModule.kt:19-25`），**传空则整块跳过**，
    连高德 9.x 必需的 `updatePrivacyAgree`/`updatePrivacyShow` 四个调用一起跳过。
    ⇒ 必须把 key 透传给 JS。**原来那个顾虑站不住**：key 本来就写在 APK 的 AndroidManifest
    里、解包即可读，JS 侧多一份不增加任何暴露面；真正的防线是它绑「包名 + 签名」以及不进 git。
    ⚠ 同一段还发现库**硬编码同意隐私政策**（`updatePrivacyShow(context, true, true)`）——
    PoC 可以，发布前必须有真实的隐私声明呈现（M5 合规项，挂账）。

  - **地图入口只挂在真的带坐标的卡上**。逐类型核过 `hmi/src/types.ts`：
    `poi_detail` / `place_list` / `place_detail` **有** `lat`/`lng`；
    **`poi_list` / `route_plan` / `charging_route` 没有**。
    ⇒ 计划 §M3-3 点名的三张卡里有两张（`route_plan`/`poi_list`）**根本没有坐标**，
    给它们挂地图入口只能靠客户端地理编码——那是另一件事（要 Web 服务 key + 一次外呼），
    **挂账给后端在卡里带坐标**，比在客户端猜位置正确得多。
    坐标校验落 `core/map/available.ts`：`0,0` 直接判空——**几内亚湾那个点画上去比不画更糟**，
    用户看到一个 marker 不会怀疑它是「没有数据」（8 条守卫测试钉住）。

  - **地图能力的判据是两条 AND**：构建期有 key（`extra.mapEnabled`）**且**原生模块在场
    （`NativeModules.AMapSdk != null`）。第二条就是坑账 §9.27 那条的复用——amap3d 同样是
    Paper ViewManager，原生缺席时整屏红屏，`CardBoundary` 兜不住。
    不可用时**入口根本不渲染**（这就是「可降级」），直接深链进 `/map` 才给诊断页，
    且**两个条件分开报**——「不可用」查不出是哪一半最耗时，这一轮就是靠它一次命中的。

- **仍未做（下一轮的入口）**：
  - ~~B 批原生重建~~ / ~~M3-3 地图~~ **✅ 全部完成并真机验过**（见上）。
  - **地图余项**：`route_plan`/`charging_route` 要的**折线**还等后端在卡里带坐标
    （这两类契约里根本没有 lat/lng，见上）；marker 点按弹窗、按点集自动缩放（当前是
    固定 zoom 12/15，两点相距 0.5km 时视野偏远）属打磨项。
  - ~~**真机未过项**：画廊全卡族截图归档 / 深浅主题全卡族 / 返回键语义 / 平板形态~~
    **✅ 均已在真机第二轮跑完**（见上）；其中「根=后台」被实测推翻，**待泓舟拍板**。
    ⚠ 平板形态是用 `cmd device_state state 3` 强制展开验的，**不等于 E3 那台真平板**
    ——折叠屏展开态与真平板的差别（DPI/输入法/多窗）没有覆盖到。
  - M3-5 Maestro（泓舟已授权装 CLI，flow 未写）、M3-6 §8.3 全清单。

### M3-V 实施记录（2026-08-27 晚，Aurora Glass 复刻批——泓舟指定的插批，不在原 M 序列里）

- **做了什么**：把 hmi 的 Aurora Glass 设计语言（`hmi/src/aurora.css` + `AuroraOrb.tsx`）
  复刻到 App 端，VPA 光球形象落四个位：Composer PTT 按钮本体（52dp 热区，录音=speaking
  波纹/识别中=thinking，隐喻照 hmi Composer）、欢迎态 88dp 大球（**此前空对话是纯空白**，
  本批补齐欢迎态=大球+问候+quick chips）、assistant 气泡 28dp 头像、顶栏 30dp 品牌球。
  落点：`ui/theme.ts`（token 逐值照 aurora.css，深浅两套，**旧字段名全保留** ⇒ 34 个
  卡渲染器/设置/车辆/地图零改动自动换肤）+ 新目录 `ui/aurora/`（AuroraOrb 七层五态 /
  AuroraBackground 深空渐变+三 blob / Glass 四边不等光照边框 / StreamCursor 虹彩流式光标 /
  ThinkDots）+ 四个消费面重皮（cards/parts.tsx 卡壳、MessageBubble、Composer、ChatScreen
  含平板右栏玻璃舞台化+宽度响应 `min(400, 42%)`）。
- **技术定案（RN 0.86 新架构，零新原生依赖 ⇒ 全批免重建、Metro 热载直验）**：
  渐变用 `experimental_backgroundImage`（TS 类型在 `types_generated/`，Flow 与手写 types
  目录里都搜不到——**查 RN 样式能力要看 types_generated**）；玻璃投影/inset 顶缘高光用
  `boxShadow` 字符串；**全程不用 `filter: blur`**（Android <12 RenderEffect 缺席会整层
  失效），柔光一律 radial 透明衰减自带柔边；conic-gradient 不存在 → 光球晕环/漩涡改
  「四色 blob 绕圆 + 整层旋转」，视觉等效流动彩环；RN 无 backdrop-filter → 玻璃走
  aurora.css 自己声明的 `--au-glass-fallback` 降级路线的增强版（半透明底叠深空 blob 背景）。
  虹彩纪律（§5）App 端守住 3 处：光球、发送按钮、流式光标——正文与数字零虹彩。
- **性能纪律**：光球 `animated` 开关——列表历史气泡头像**零动画帧回调**，判据取
  「这条消息此刻在动」（pending/streaming/processActive）而非「是不是最后一条」；
  同屏循环动画常态只有 Composer 主球一个。
- **真机读数（goku 24072PX77C，全部截图验证）**：手机态欢迎/对话（user 蓝玻璃+assistant
  玻璃+真执行回执）/画廊 40 样本抽查（weather/stock A股红涨/scene_card danger 角标/
  payment_qr 真码+过期置灰）/设置页；**浅色主题即时切换**全对；平板态
  `cmd device_state state 3` 强制展开=左对话+右玻璃舞台三段（车况 72%·396km·P 真实推导）；
  PTT 长按=「正在听…」+partial 实时上屏+系统麦克风指示亮。tsc 0、jest 188/188。
- **本批踩的坑（已修）**：RN radial `ellipse` 缺省 farthest-corner——衰减在 View 边界外
  才走完，真机上镜面高光渲染成**明显方块**（web 同款语法不显形是因为有 blur(2px) 且尺寸小）
  ⇒ 光球内定位式柔光层一律 `closest-side` + 色标 ≤82% 收尾。
- **刻意不做**：等宽数字 §7 铁律（34 个渲染器逐处标 mono 是独立批）、AuroraBorder
  AI 虹彩描边（App 端暂无「整卡 AI 出品」标识需求）、speaking 波纹只在真机动态可见
  （静帧抓不到峰值，真人按住肉眼验）。
- **接手注意**：画廊全卡族的样式回归入口不变（`xiaozhou://card-gallery?only=…`）；
  折叠屏截图要 `screencap -d <display-id>`（坑账 §9.28 同款）。

**第二轮（同日晚，泓舟真机四条反馈的收口）**：
- **光球形态重做（反馈①「像几个同心圆旋转」）**：根因两条——四角小 blob 不重叠（有「点」感）
  + 全程无 blur（三层裁切边界清晰=「圈层」感）。对照物先行：用 headless Edge 把 web 版
  七层逐值渲成基准图（scratchpad orb-ref.html），确认 web 形态是**无边界流体**。修法：
  瓣径 68%→132%、中心绕圆周分布相邻大幅重叠、正反两层 45° 相位错开；三层盘启用
  `filter:[{blur}]`（此前「零 filter」是全机型一致的保守决策，**光球是设计记忆点，值得为它
  破例**——API<31 无 RenderEffect 时瓣重叠仍柔和，只是少一层融合）；瓣浓度 C8→E6、
  内层 opacity 0.85。真机对照基准逐轮收敛，终态与 web 版 96px 球形态一致。
- **浅色「对话框顶边白线」（反馈②）**：根因=浅色 `hi`（顶缘高光）是 85% 白 + 气泡 inset
  1px 纯白——aurora.css 的纯白 bd-top 压在 backdrop 磨砂上才成立，RN 无磨砂时它是一条
  孤立白线。修法：浅色 `hi` 退成比 line 更淡的深色 `rgba(10,14,26,0.05)`、去浅色 inset 段；
  深色不动。
- **图标移植（反馈③，评估结论=数据共享、渲染各端自持）**：A-8 图标库两份数据文件
  （icons.gen.ts 60+ / icons.custom.ts 21）是纯 `{w,h,body}` ⇒ 进 `shared-allowlist.json`
  台账（M3 相），`mobile/src/ui/Icon.tsx` 用 SvgXml 复刻 hmi Icon.tsx 的 24×24/1.8 stroke
  契约（aiMoment 渐变态无消费点刻意不做）；svg 原生探测走 merchantCards 同款
  `TurboModuleRegistry` 判据、缺席回退文字。本轮消费点=顶栏「车辆/设置」换 40dp 图标钮
  （`.au-icon-btn` 同款）；34 个卡渲染器内部图标不动（独立批）。
  ⚠ 台账守卫①扫**全文含注释**：注释里写 `@shared/...` 不带扩展名会被当引用判红——
  守卫第一次跑就抓到写它消费面的人（正确行为，注释改措辞）。
- **应用图标=光球（反馈④）**：icon 渲染源即基准 HTML（scratchpad icon-gen.html，
  querystring 六模式），headless Edge 出六件套：icon 1024（深空底+600px 球）/
  adaptive 前景 512（**透明底必须垫不透明深空圆**——半透明球体没有深色底会整体泛白，
  辉光同步减 35%）/ 背景 512（深空渐变+双 blob）/ monochrome 432（SVG mask 白剪影：
  实心圆+高光挖空，按像素 alpha 验过形状）/ favicon 48 / splash 228×213。
  换 icon 需重 prebuild+重装（README 既有约定）。

---

### M3-W 实施记录（2026-08-27 深夜–08-28，M3 收尾批：地图打磨 / e2e / 探活缺陷）

- **M3-3 地图打磨 ✅（三项，真机逐项验过）**：
  1. **按点集自动缩放**。amap3d 的相机 API 只有 `moveCamera(CameraPosition)`，**没有
     fitBounds**（`lib/src/map-view.tsx:175`）⇒ 新建 `core/map/fit.ts` 自己解：
     zoom = log2(可视像素 / 该跨度在 zoom0 下占的像素)，经纬各解一次取小的；
     纬度必须走**墨卡托 y** 而不是度数（高纬同样度数占的像素更多，按度数算会把点顶出画面）。
     真机读数：画廊 `place_list` 两点（瑞幸/麦当劳，相距 ~0.76km）从固定 zoom 12 的
     「视野偏远」变成**比例尺 100m、两点分居对角**。
     守卫 `test/mapFit.test.ts` **12 条**，主张写成「每个点都在可视矩形内」而不是断言具体
     zoom 值（后者一改 padding 就红、红了也说明不了对错）。反向验证：把 log2 改成 log10
     → 3 条红，还原即绿。⚠ 顺带记一条：**containment 断言只能抓「装不下」，抓不到「太远」**
     ——所以另有一条「0.5km 两点 zoom>14」的回归探针专门盯老毛病。
  2. **marker 点按出详情**。`Marker.onPress` 是**无参数**回调（`marker.tsx:72`），
     谁被点只能靠调用方闭包捕获。点中 → 底部条换成「序号 + 名称 + 地址」+ 相机拉近到
     `max(当前 zoom, 16)`（**不许比当前更远**——用户已经放大到 18 再点一个点，被拉回 16 是倒退）。
  3. **详情条要有显式关闭按钮**。原设计只靠「点地图空白处收起」，真机第一次点就没关掉
     ——高德把标注点（商场/地铁站那些自带图标）的点击走 `onPressPoi`，`onPress` **根本不发**，
     而地图上标注很密。新坑账 §9.35。
  - **⚠ 本批自己制造又自己修的回归**：底部信息条一度换成 Aurora 的 `Glass`，真机上**几乎读不出来**
    ——玻璃底是半透明的、靠叠在 AuroraBackground 深空渐变上才成立（M3-V 记录里写死了这个前提），
    而它这次压在**地图瓦片**上。改回不透明面板 + 只保留边框与投影。新坑账 §9.36。
    判据：**压在不可控内容上的浮层一律用不透明底。**

- **M3-5 Maestro flow ✅ 写完（4 条）/ ⬜ 未实跑**（CLI 未装到位，见下）：
  `mobile/e2e/`：`01-text-weather`（发文字→天气卡）、`02-danger-confirm-cancel`
  （危险动作→确认条→取消→台账清）、`03-offline-resend`（飞行模式→恢复→补达）三条按计划原文；
  另加 **`04-offline-smoke`**（画廊渲染，零后端依赖）。
  - **为什么多加第四条**：前三条**全都要真栈**，而 CI runner 没有 Tailscale（给 CI 配
    auth key 是红线动作）。在 CI 里跑那三条只会稳定红、然后被人加 `continue-on-error` 忽略掉
    ——**一条永远红的检查等于没有检查**。⇒ CI 跑第四条，三条真栈流的读数只能来自真机。
    计划 §M3-5 原文写的「CI 手动档实跑一次」按此调整（计划变更纪律 §0.5）。
  - **驱动用 testID、断言用文本**：新增 `composer-input`/`composer-send`/`confirm-accept`/
    `confirm-cancel`/`msg-pending` 五个 testID。理由：文案会变（Aurora 那批就把发送键重画过），
    而断言必须验语义内容。
  - **flow ③ 的判据是「挂起态消失」不是「回答文本出现」**：断言回答文本会被**用户自己那条
    气泡**满足（同一句话同时在屏上），立刻绿而消息可能还卡在队列里。这一条写进了
    `MessageBubble` 的 testID 注释。
  - **`subflows/open-app.yaml` 不是仪式**：dev-client 的 `launchApp` 打开的是
    **DevLauncherActivity**，此时所有 `tapOn` 都找不到元素，失败信息看起来像「App 里没这个按钮」，
    真相是被测对象没在跑（同坑账 §9.20 那条）。
  - **⬜ CLI 未装成**：`maestro.zip` **315MB**，本网络实测 ~30KB/s（GitHub releases，
    与坑账 §9.7 同一形态；三个 GitHub 代理镜像本网络全不可达，4 段并行也没提速）。
    两次下载各花 40+ 分钟仍未完成，且**带 `-C -` 的 retry 把重复字节插进了文件中间**
    （截断到官方声明大小后 sha256 仍对不上、zip 仍坏）——新坑账 §9.37。
    ⇒ flow 已按 Maestro 2.9.0 语法写好，**装上 CLI 后直接 `maestro test e2e/` 即可**，
    但**本轮没有实跑读数，不许把「写完了」说成「跑通了」**。

- **⚠ 本批最有价值的产出是一个真缺陷（不在计划里，真机逼出来的）**：
  **RN 的 WebSocket 在飞行模式下 `onclose`/`onerror` 都不来**（MIX Fold 4 / Android 16 实测，
  观察 4 分钟状态仍 open、顶栏仍显示「在线」）。后果不止是状态显示不对——
  `ResilientWebSocket.send()` 看 `isOpen` 为真就把帧**直接写进死 socket，不入队**：
  复现读数（23:13:14 开飞行模式 → 23:13:15 发送 → 23:15:26 恢复网络 → **不补发**，
  95s 后看门狗给「响应超时」）。**「断线不丢消息」这条队列语义在这个窗口里是失效的。**
  - **修法（两侧）**：共享 `hmi/src/ws.mjs` 加 `reconnectNow()`——外部判死入口，
    关掉旧 socket（**先摘回调再关**，否则迟到的 onclose 会排出第二条重连链把退避算乱）、
    **保留队列**、立即走退避重连。HMI 侧不调用它，行为逐字不变；hmi node:test **288/288**
    （新增 3 条，反向验证：去掉「摘回调」那步 → 2 条红）。
    App 侧 `core/api/liveness.ts`：前台每 15s 探一次 `/healthz`（4s 超时），**连续 2 次**
    失败才判死（一次抖动就判是误杀），判死 → `reconnectNow()`。守卫 `test/liveness.test.ts` 9 条。
  - **判据的分界要说清楚**：`ws.mjs` 头注早就写了「刻意不做 JS 侧假死探测」，理由是
    **应用层静默 ≠ 断连**（长任务开思考 30s+ 期间本来就静默，拿它判死会误杀健康连接）。
    这次加的探活**不违反那条**——它判的是「HTTP 探不到 /healthz」，那是真实的网络不可达证据，
    与「应用层静默」是两回事。这层区分写进了两个文件的头注。
  - **真机复验读数**：t0=23:57:35 开飞行模式 → 23:57:45 探活失败(1/2) → 23:58:00 失败(2/2)
    **判死 → reconnectNow**（**25.7s**）→ 顶栏转「已断开」+ 提示条出现（**那句「消息会排队、
    连上后自动补发」现在是真话**）→ 断网期间发「播放音乐」→ 恢复网络 → **补达，回执
    「好的 / 已执行 media.control」**。
  - **⚠ 残留（不许说成全修好）**：探活周期内（最长约 25~30s）发出的**第一条**消息仍可能丢
    ——那个窗口里 ws 仍自称 open。彻底消除要么「发送前先探」（给每条消息加延迟），
    要么「判死后重发」（**涉及后端幂等**：同一 `request_id` 重发意味着车控可能执行两次，
    「音量+1」两次就错了）。⇒ **刻意不做自动重发**，宁可诚实降级。
    另一处已知限制：探的是 HTTP 端点不是这条 WS，「网络通但服务端单方面关了连接」探不出来。

- **「根屏返回=后台」定案（泓舟 2026-08-27 拍板）**：**维持现状，把「前台交互档」写进产品承诺**。
  不加 intent hack（改任务栈语义、且是纯 hack），真正的修法是 M5 的前台服务/厂商推送。
  与坑账 §9.5「省电模式会杀后台 socket，验收在前台做」是同一条承诺的两面。

- **§8.3 里程碑清单逐条状态见 §8.3 表**（本批把「✅/⬜/❌」标在条目上，未验的一律写未验）。

- **读数**：`tsc --noEmit` 0 error；mobile jest **209/209**（M3-V 末 188 → +21：
  mapFit 12 + liveness 9）；hmi node:test **288/288**（285 → +3）；白名单守卫绿。
  ⚠ 修了一处**测试自身的定时器泄漏**（`gateway.test.ts` 建的会话会起真探活定时器，
  jest 报「worker 无法优雅退出」）——传 `liveness: false` 关掉，探活判据在自己的单测里注入定时器验。

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

> **打钩纪律（2026-08-28 M3-6 收口时定的）**：✅ 只给**有真机读数**的条目；
> 只在画廊样本/单测上验过的写「样本」，没跑的写 ⬜ **未验**，被外部挡住的写 ❌ 并写清挡在哪。
> **「实现完成」不是打钩的理由**——那是代码面，这张表验的是真机行为。
> 逐条读数在各阶段的实施记录里（M1-8 / M2-5 / M3 / M3-V / M3-W），这里只留状态。

### 8.1 M1（两台真机 → **实际只有一台**：MIX Fold 4，E3 那台真平板始终未办）
- [x] ✅ 文字问答流式渲染（speech_delta 逐字、final 收尾）
- [x] ✅ 17 卡族逐族语句通过；未实现型出兜底卡（trip 语句实证）
- [x] ✅ 危险动作确认条：确认执行 / 取消收尾 / 两条挂起并存各自可点
- [x] ✅ 快速连发两问不串轮（M1-1 ③ 的真机版）
- [x] ✅ 断网发消息→重连自动补发；锁屏 30s 回前台流恢复
      ——⚠ **M3-W 在这条上发现了一个真缺陷并修掉**：断网瞬间（onclose 未到的窗口）发的消息
      **不入队、直接丢**。修后复验补达。**修法有残留窗口**（探活周期内第一条仍可能丢），见 M3-W
- [x] ✅ **失败态之后再说一句**：挂起台账跨离线周期存活（M1-8 亲证）
- [x] ✅ 平板/手机三形态 + 旋转；深浅主题（⚠ 平板态是折叠屏展开态 `cmd device_state state 3`
      强制验的，**不等于真平板**：DPI/输入法/多窗没覆盖）
- [x] ✅ 多端并发：座舱 HMI（浏览器，CDP headless）与 App 同 user 同时在线
      ——① **会话不串**：HMI 那轮（session `demo-…`）在 App 屏上零出现；
      ② **记忆共享**：HMI 侧说「我喜欢喝美式咖啡，记住这个偏好」→ App 侧问「我喜欢喝什么咖啡」
      答「你喜欢喝瑞幸的美式咖啡，而且偏好冰的～（这是您8月9日提过的）」，**且带出处**。
      ⚠ **诚实一句**：答案引用的是 8/9 的存量记忆，所以本轮证明的是「同一 user 的记忆库两端都能召回」，
      **没有量「刚写入的记忆多久跨端可见」**（记忆抽取是异步的）。
      ⚠ 顺带记一条事实：`AUTH_TOKENS` 目前**只有一个条目**（u1/v1，带 `vehicle.control`），
      README 里写的「App 专属条目、手机档不含 vehicle.control」**没有落地**——App 现在用的就是 HMI 那把
- [x] ✅ 主动提醒到点前台送达（33s）+ 完成按钮闭环
- [x] ✅ 重启零重投（2026-08-28 补验，M1-8 那两次观察窗无效的账在此清掉）：
      提醒到点送达 → `force-stop` + 从 dev-client 重新起 → **前台 + WS 绿点持续 65s，
      「叮，到点了」零重现**（判据就是 M1-8 当时写下的那条）。
      ⚠ 顺带验到一条**跨端**事实：这条提醒是在 **HMI** 侧创建的，到点后 **App 收到了主动播报**
      （「主动播报 · 提醒到点」+ `reminder_card` 完成/稍后两键）——主动消息是按 user 推送到
      所有在线端的，这也是「多端并发」那条的第三个证据

### 8.2 M2（追加）
- [x] ✅ PTT partial 上屏 ≥1 / 松手定稿自动发送
- [ ] ⬜ **未验** 蜂窝网络下重复
- [ ] ❌ **后端阻塞** `asrProvider=off` 批处理路径出文字（云栈 `/api/asr` 401，MiMo key 失效）
- [x] ✅ 说话中断网兜底不挂死（首跑抓到真挂死并修掉，复验 29s 出错误态回 idle）
- [x] ✅ TTS 自动播报首音 <1.5s（换 WS 传输后 516~563ms，2.7 倍余量）
- [x] ✅ 播报中按 PTT 即停（物理面实证：麦克风 peak 0.145 → 0.004）
- [ ] ⬜ **未验，且预期行为本身存疑**（2026-08-28 读码发现）：App 侧的 fallback 是
      **流式失败 → `synthesizeBatch` 批处理**，而批处理用的是**同一个 cfg、同一个引擎**
      （`core/voice/tts.ts::fallback`）。所以「选一个没 key 的引擎（mimo）还应该出声」这条
      **要先说清是谁来换引擎**：网关侧自动换？还是 App 侧换？现有实现两者都不是。
      ⇒ 先定预期再验，否则跑出来的「不出声」既可能是缺陷、也可能是设计本就如此
- [ ] ⬜ **未验** 来电/焦点抢占四场景不崩（代码已落，缺真实事件）
- [x] ✅ 语音设置（引擎/音色/试听）持久化

### 8.3 M3（追加）
- [x] ✅ **34** 卡族截图归档（画廊 40 条样本逐张，深浅两套）
      ——⚠ §2.6 原文写的「29 型」是错的，实际 34 个 type 字符串（M3 记录已纠）
- [~] **样本** 商户全流程到亮码卡（不付款）：`payment_qr` 三分支真机验过，但**走的是画廊样本**
      ——真栈那次被门店营业时间挡住（01:30 全打烊），**不算真栈读数**
- [x] ✅ 车况镜像双端同步：云栈 `POST /api/debug/vehicle {battery:55}`（M3-2 验收原文允许的手段）
      → App 车辆页**未刷新页面即时变**：72%/396km → **55%/303km**，
      且续航是**真实推导**（55%×`RANGE_FULL_KM` 550=302.5→303，与 `vehicleStage.mjs` 逐字对上）。
      验完已还原 72
- [x] ✅ 平板面板三段（车况三格是真实推导 72%×550=396km）
- [x] ✅ 地图入口（有 key）：真实瓦片 + marker 落点正确 + 回中 + **M3-W 三项打磨**
      （按点集自动缩放 / marker 点按详情 / 显式关闭）；无 key 干净降级由 `MAP_AVAILABLE` 两条件守
- [ ] ⬜ **flow 写完、未实跑** Maestro 三流 3/3（`maestro.zip` 315MB 本网络下不动，坑账 §9.37）
- [x] ✅ CI mobile job 常绿 + APK 工件手动档可用（既有，M0-8）
      ——⚠ CI 的 e2e 步骤只能跑 `04-offline-smoke`（runner 无 Tailscale，理由见 M3-W）
- [x] ✅ 返回键语义（二级页关层 / 根屏 finish——**计划原假设被推翻，泓舟已拍板维持现状**）
- [x] ✅ 弱网提示（**M3-W 修复后才是真的**：修前 onclose 不来、屏上一直显示「在线」）
- [ ] ⬜ **验不出** keep-awake（dev build + USB 常亮两个污染源叠加，坑账 §9.41）

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
11. **subst 在真实 RN/Expo 构建里不可用（E6 定案二次迭代，2026-08-25 M0-2 六连败实测）**：
    路径来源多头——`require.resolve` **不**解析 subst（产 X:）、RN CLI 的
    `fs.realpathSync.native` **解析** subst（产 D: 中文）——同一构建永远两套盘符根，
    每个做 `Path.relativize` 的子系统挨个炸 `different roots`（三处实证：expo-dev-menu
    projectDirectory / 各库 generateCodegenSchemaFromJavaScript / KGP 增量编译）。
    **`NODE_OPTIONS=--preserve-symlinks` 是反例**：拦不住 CLI 里显式的 native realpath，
    反把 expo 侧路径也变 X:，制造第二种混合。修 codegen 那半的 config plugin
    `mobile/plugins/with-unified-drive-root.js`（react{} Folders 三路径钉 native realpath）
    留作形态守卫。
12. **真实中文路径单根也不可用**：`android.overridePathCheck=true` 放行 AGP 后，
    CMake PCH 把中文路径以错误编码写进生成头文件（clang 报
    `cannot open file 'D:/.../<B2><FA>?/.../WorkletsPCH.h'`）——**AGP 那条检查的
    原判（§1.1 ③「不要用 override」）被实证坐实**。⇒ 唯一形态：**ASCII 镜像工作区**
    （`build_mobile.ps1` 用 `robocopy /MIR` 增量镜像 `mobile/` 到
    `D:\Android\builds\xiaozhou-mobile`，`/XD android .expo` 保镜像侧原生目录与增量）。
    debug 构建不读 `src/`；**-Release 打 JS bundle 需再评估此形态**（挂 M5）。
13. **Gradle `providers.exec` 的 `asText` 按 JVM 默认字符集解码**（本机 GBK）：expo
    autolinking 起的 node 子进程输出 UTF-8 JSON，中文路径在 JVM 内成乱码、报
    「projectDirectory 不存在」，而**日志里路径看着是干净中文**——GBK 解码→GBK 编码
    的字节回环假象，肉眼从日志分辨不出。修 = `gradle.properties` 的 jvmargs 加
    `-Dfile.encoding=UTF-8`（脚本自动补）。修完后现象反转：JVM 正确、GBK 控制台渲染
    UTF-8 输出变乱码——**判读构建日志先想编码链是哪一段**。
14. **`repo.maven.apache.org` 本网络 DNS 不可达**（Maven Central 拉不动
    org.jetbrains.compose 等；gradle 一次失败还会把整个 MavenRepo 禁用、连坐其余包）。
    修 = `scripts/gradle_cn_mirrors.init.gradle` 镜像**前置**（aliyun central/google/
    gradle-plugin + tencent 聚合；`-I` 注入，**前置不删原仓库**）。CI 海外 runner
    不经过它（workflow 里直接 gradlew）。
15. **prebuild「Clearing android」EBUSY 的锁主清单**：gradle/kotlin daemon 之外，
    **自己会话与子进程的 cwd** 也算——本轮两连败都是自己（bash 会话 cd 进
    `mobile/android` 查文件没退出来；Monitor 的 tail 子进程继承了那个 cwd）。
    脚本已内置 prebuild 前 `gradlew --stop`；列出 java 进程为空却仍 EBUSY ≈ 就是你自己。
16. **小坑合集**：android CLI 实体是 `android.exe`（bin 下没有 android.bat，传 .bat 报
    CommandNotFound）；SDK 57 生成的根 build.gradle **不再带 ext 版本块**，静态版本
    真相源 = `node_modules/react-native/gradle/libs.versions.toml`（NDK 27.1.12297006
    就是从这解析并预装的，AGP 现拉这个 745MB 包要 6.5 小时、android CLI 3.5MB/s 三分半）；
    `subst` 命令输出的中文经 OEM 码页必乱码、**不可字符串比对**（当时用 nonce 探针文件
    验同一性；subst 已废弃，判据留档）。
17. **robocopy `/XD` 传裸名是按目录名匹配全树**：`/XD android` 把 node_modules 里
    每个包的 `android/` 原生源码目录一并排掉（镜像后 prebuild 报
    `Cannot find module .../config-plugins/build/android/codeMod.js`，而两侧
    node_modules 顶层数量完全一致——**顶层对不代表深层对**）。要只挡顶层那一个目录，
    必须传**绝对路径**、且源侧与镜像侧都传（前者管拷贝面、后者管 /MIR 的删除面）。
18. **Metro 别用 `CI=1` 起**（M1-8 真机首轮）：CI 模式首跑的转换缓存曾被污染，真机
    红屏「Cannot read property 'EventEmitter' of undefined」（expo/src/Expo.ts 的
    re-export 下游症状，`--clear` 后干净启动）。判读：**这类 undefined 红屏先想缓存，
    再想缺原生模块**——本机 logcat 里 SoLoader/AppContext 全正常就是旁证。
19. **本机 Metro 文件监听不可靠（两次实锤丢事件）**：改完源码必须在 Metro 输出看到
    `Bundled` 行；没有就 `am force-stop` + deeplink 重拉——**新 bundle 请求会重读磁盘
    最新文件**，不依赖 watch 事件。「我改了东西」≠「被测路径变了」在构建面同样成立。
20. **adb 驱动真机的三个雷**（M1-8 首轮全踩过）：① `input text` 在焦点不在输入框时
    字符成**全局键事件**——文本里 'r','r' 正是 dev-client 重载快捷键（两次莫名 reload
    的成因；输入前必须 uiautomator 验 `focused="true"`）；② `keyevent 111`(ESC) 被译成
    Back，根屏=退出 Activity；③ `input text` 不支持中文——中文语料用「临时验收 chips」
    形态（Composer 临时加一排、验收后 git checkout 还原）。HyperOS 侧：装 APK 要
    「USB 安装」、注入要「USB 调试（安全设置）」、shell 无 MOCK_LOCATION（test provider
    注入被拒）。
21. **「有 GMS ≠ GMS 能用」——expo-location 在 GMS 网络面死的国行设备上取不到 fix**
    （结构性，**挂 M2 账**）：`getCurrentPositionAsync`（含 Highest）永不返回也不抛错，
    而 `dumpsys location` 显示**系统 network provider 有 AMap 源 fix**（expo-location
    走 GMS fused 读不到平台层）。本批修法=20s 上限 + `getLastKnownPositionAsync` 兜底
    （坐标带真实时间戳上行，实测宝安区预报/周边闭环）；根治=换 platform-provider 取
    坐标方案（M2 评估 @react-native-community/geolocation `locationProvider:'android'`
    或 M4 一并入原生模块）。
    ⚠ **2026-08-27 更正：M2 结束了，这条既没做也没评估**——M2 全程围着语音面转，
    定位根治一次都没被拿起来。**挂在某个阶段名下的账，那个阶段结束时要么做要么改期，
    不能就这么留着**（留着的下场是下一个人以为它做过了）。
    ⇒ **改期到 M3**：M3-3 本来就要动地图/坐标面，届时一并评估
    `@react-native-community/geolocation` 的 `locationProvider:'android'`。
    当前的 20s 上限 + last-known 兜底继续用，它在真机上是通的（M1-8 实证）。

22. **react-native-audio-api 的 `downloadPrebuiltBinaries` 有两个 Windows 假设**（M2-1，
    两轮实测才通）：① 它在 Windows 分支把 Git Bash 写成**绝对路径**（C 盘的
    `Program Files\Git\usr\bin\bash.exe`，见该包 `android/build.gradle:320-329`），
    本机 Git 装在 D 盘 ⇒ `A problem occurred starting process`，而 preBuild 依赖它，
    整构建停在这（BUILD FAILED in 2m18s）；② 换对 bash 还不够——Gradle 的 `Exec`
    继承的是 gradle 进程的 Windows PATH，**里面没有 Git 的 usr/bin**，脚本里的
    `rm`/`mkdir`/`unzip` 全 `command not found`（exit 127），`curl` 则撞上 System32
    那个原生版、不认 MSYS 路径、写 `-o` 目标报 `curl: (23)`。
    修在 `scripts/gradle_cn_mirrors.init.gradle`（不是改 node_modules——它是镜像产物，
    改了每次同步都要重做）：`-PaudioApiBashPath` 传真实路径 + 把 `usr/bin`、`mingw64/bin`
    前置到该任务的 PATH。bash 路径由 `build_mobile.ps1::Resolve-GitBash` 探测，
    **从 git.exe 反推优先**——PATH 上叫 bash.exe 的还有 WSL 启动器（System32）和商店存根，
    拿到那两个比拿不到更糟。CI（Linux）走库的非 Windows 分支，不经过这里。
23. **下载源仍是 GitHub releases**（`software-mansion-labs/rn-audio-libs`），本网络
    **30 KB/s**（走代理，`Connection established` 可见）。两条动作：① `app.config.ts` 的
    插件配置 `disableFFmpeg: true`——本 App 音频面全是裸 PCM，用不到 `decodeAudioData`
    解 mp3/aac，省掉 11.9MB 的 `jniLibs.zip`（~6.6 分钟）与 4 个 ABI 的 FFmpeg `.so`；
    ② 剩下那个 2.6MB 的 `android.zip`（opus/ogg/vorbis 静态库）**在源侧先下**
    （`mobile/node_modules/.../android` 里跑一次那个脚本），否则 `build_mobile.ps1` 的
    `robocopy /MIR` 每次都会把镜像侧的下载产物当"多余文件"删掉、每次构建重下。
    ⚠ 代价明说：`decodeAudioData` 对压缩格式不可用；哪天要放 mp3 提示音（M4 cue）
    得把 `disableFFmpeg` 改回来并重构建。
24. **metro 的 `disableHierarchicalLookup=true` 会掐断嵌套依赖**（M2-1）：
    react-native-audio-api 运行时要 `semver@^7`，而顶层 `node_modules/semver` 被
    `@babel/core` 的 6.3.1 占着，npm 把 7 装进了它自己的 `node_modules`——**正好是那条
    被关掉的层级查找**。症状是 bundle 直接失败 `Unable to resolve "semver/functions/gte"`。
    修 = `metro.config.js` 加一个**只对 semver 生效**的 `resolveRequest`，用 node 的
    `require.resolve(..., {paths:[发起方目录]})`（那就是"从发起方向上找"的语义），
    重复 React 的防线原样保留。**别为一个包的依赖去关全局解析策略。**
25. **react-native-audio-api 与浏览器 Web Audio 的三处差异**（M2-3 真机逐条量的，
    读数在实施计划 M2 记录）：`onEnded` 不是 `onended`（赋错不报错、只是永不触发）；
    `AudioContext` 建出来是 **suspended**，不 `resume()` 就不出声**且一声不吭**；
    **不按 ctx 采样率重采样**（22050 的 TTS 在 48k 上下文 = 2.18 倍速 + 音调升高）。
    三条全部由 `mobile/src/core/voice/audioCtx.ts` 吸收，共享模块与 hmi 一行不改。
    ⚠ 附带一条 typed array 的坑：`copyToChannel` 收到 `subarray` 视图时抛
    `Not enough space to copy to destination`——**它量的是底层 ArrayBuffer 不是 length**，
    所以重采样器返回 `slice` 而不是 `subarray`。
26. **Git Bash 里跑 `adb shell` 要关 MSYS 路径转换**：`adb shell uiautomator dump /sdcard/ui.xml`
    里的 `/sdcard/...` 会被 MSYS 当本地路径翻译成 `D:\Program Files\Git\sdcard\ui.xml`
    （报 `failed to stat remote object`）。`export MSYS_NO_PATHCONV=1` 即可，
    或者用 PowerShell 跑 adb。**这类故障看起来像"设备上没有那个文件"，其实命令根本没到设备。**

27. **`CardBoundary` 兜不住原生组件缺席——整屏红屏，不是掉兜底卡**（M3-1，2026-08-27 实测）。
    JS 引了 `react-native-svg` 而设备上的 APK 还没重建 ⇒ 打开卡片画廊直接红屏
    `IllegalViewOperationException @ ViewManagerRegistry.get(RNSVGSvgViewAndroid)`。
    **它是 Fabric 在挂载期、原生线程抛的**，不是 React 渲染异常，
    `getDerivedStateFromError` 根本不触发 ⇒ **「未知/异常卡型不许抛崩整个列表」这条铁则
    只覆盖 JS 侧异常**。判据：**引入任何原生支撑的卡片组件，都要在渲染前显式探测原生在场**
    （`TurboModuleRegistry.get('<该库的 TurboModule>')`，拿不到就是没链接进来），
    不在场时走契约里本来就有的降级分支。⚠ 这不只是开发期：M5 的 expo-updates OTA
    同样只推 JS 不推原生，形态一模一样。
    ⚠ 配套一条**措辞诚实**：降级后如果上层 presentation 仍是「扫码档」，屏上却没有码，
    必须显式说「显示不了」——否则就是让用户去扫一个不存在的东西。
28. **`adb shell input swipe` 太快会被当成点击**（M3-1）：`swipe x1 y1 x2 y2 90`（90ms）
    对 FlashList **纹丝不动**，看起来像「列表不跟新消息」；换 **320ms 慢拖**才真的滚。
    ⚠ 别把这个误读成 bug：`maintainVisibleContentPosition.autoscrollToBottomThreshold=0.2`
    表示**用户手动上滚后就不再抢滚动位置**，那是刻意的，所以驱动侧本来就该自己滚到底。
29. **验收跑到一半设备会锁屏，且 adb 解不开**（M3-1）：`stay_on_while_plugged_in` 本来就是
    `2`（USB 常亮）也不管用——外屏进 `DOZE_SUSPEND`，`KEYCODE_WAKEUP` / `POWER` /
    `wm dismiss-keyguard` 都过不了安全锁，`screencap` 一律返回纯黑（**16643 字节的全黑 PNG
    就是这个签名**，别当成截图失败去查 adb）。折叠屏还多一层：`screencap -d 0` 报
    `Display Id '0' is not valid`，真实 id 要 `dumpsys SurfaceFlinger --display-id` 取，
    内外屏各一个（内 2224x2488 / 外 1080x2520）。⇒ **长时验收要么先请人解锁并关掉锁屏，
    要么把需要人解锁的项排在一起做。**
30. **卡片渲染验收不能只靠真栈**（M3-1）：一部分卡真栈**够不着或不该够**——`payment_receipt`
    要真付款而契约 §9.17 明说系统不执行最终付款；商户菜单卡要门店营业（01:30 附近瑞幸
    全部打烊，后端如实回「门店已打烊」）；边界分支（付款码过期置灰、充电路线「全程无需补电」）
    真栈几乎不产。⇒ `/card-gallery` 画廊屏 + `fixtures.ts` 是**必需品不是装饰**。
    ⚠ 但**样本不是读数**：画廊每条都标「真栈已验/样本」，混为一谈就是验收造假。

31. **折叠屏上 `screencap` 必须显式 `-d <displayId>`**（M3-1 真机第二轮）：不带 `-d` 时
    adb 自己就警告「Defaulting to the first display found, **however this default is not
    guaranteed to be consistent across captures**」——抓到关着的那块屏就是
    **16643 字节的纯黑 PNG**。⚠ 我据此误判过一轮「设备锁屏了」，其实屏是亮的、只是抓错了。
    显示器 id 用 `dumpsys SurfaceFlinger --display-id` 取（本机内屏 2224x2488 /
    外屏 1080x2520，**折叠态活跃的是外屏**）。判据：**全黑截图先怀疑抓错屏，再怀疑息屏。**
32. **`adb shell input swipe` 的时长要卡在中间档**（M3-1）：700ms 慢拖会被卡内 `Pressable`
    当成按压吃掉（列表纹丝不动，连滚 9 次零位移）；90ms 快扫又被当成点击。
    **260ms 左右才既能滚又不误触**，且一次跨约 3 个条目（有惯性，落点不可预测）。
    ⇒ **长列表的取证不要靠滚动定位**——给取证屏一个直达参数（本轮 `?only=` 就是这么来的）。
33. **deeplink 打到「已在栈顶的同一个路由」不会重新挂载**（M3-1）：`am start -d
    xiaozhou://card-gallery` 在画廊已经是顶层时，Android 只 deliver intent
    （`Warning: Activity not started, intent has been delivered to currently running
    top-most instance`），React 组件**不重挂**，`useMemo(..., [])` 里的东西还是旧的。
    症状是「代码明明改了、屏上没变」——我据此以为热更没生效，其实是没重挂。
    判据：**要验「挂载时求值」的东西，必须 force-stop 重拉，光按返回可能也不够**
    （expo-router 的 Stack 会留着屏）。
34. **启动方式会改变返回键语义**（M3-1）：用 deeplink 从别的 App 起时，本 App 不是任务根，
    根屏返回自然回到上一个 Activity；只有用 `monkey -c android.intent.category.LAUNCHER`
    从桌面起才测得到真实语义。**第一次的结论因此不作数，换入口重测才坐实。**

35. **amap3d 的 `onPress` 在点到标注点时不发**（M3-W）：高德把商场/地铁站那些自带图标的
    点击走 `onPressPoi`，`onPress` 收不到 ⇒ **「点空白处关闭」不是可靠出口**——地图上标注很密，
    用户以为点的是空白。真机实测：第一次点没关掉，换三处真空白才关掉。
    判据：**浮层必须有显式关闭按钮，「点别处」只能当加速路径。**
36. **Aurora 的 `Glass` 压在地图上等于透明**（M3-W）：RN 无 backdrop-filter，玻璃感靠
    半透明底叠在 AuroraBackground 的深空渐变上（M3-V 定的前提）。地图页底下是**地图瓦片**
    ——亮度不可控、内容不可预测，换上 Glass 后底部信息条**几乎读不出来**。
    判据：**压在不可控内容上的浮层用不透明底，玻璃质感只留边框与投影。**
37. **`curl -C -` 配 `--retry` 会把重复字节插进文件中间**（M3-W，下 315MB 的 maestro.zip 时踩）：
    多次断点续传后文件**比声明大小还大**，截断到正确大小后 sha256 仍对不上、zip 仍坏。
    判据：**大文件下载失败就整个重来，别用 retry+续传；下完先对官方 sha256 再用**
    （release 里就有 `checksums_sha256.txt`）。顺带：GitHub releases 本网络 ~30KB/s，
    三个 GitHub 代理镜像全不可达，4 段并行也没提速（与坑账 §9.7 同一形态）。
38. **RN 的 WebSocket 在飞行模式下 `onclose`/`onerror` 都不来**（M3-W，最要紧的一条）：
    观察 4 分钟 `readyState` 仍 OPEN ⇒ `send()` 把帧写进死 socket、**不入队、消息真的丢**。
    浏览器不是这样（HMI 因此从没暴露过）。修法与判据分界见 `mobile/src/core/api/liveness.ts`
    与 `hmi/src/ws.mjs` 的头注。**验「断网不丢消息」必须让断网窗口短于看门狗（95s）**
    ——我第一次复验时网络恢复得比看门狗还晚，读到的「响应超时」两种成因都能解释，读数不作数。
39. **HyperOS 的输入法首启同意页会截胡第一次点击**（M3-W）：点 App 输入框 → 弹出系统
    「剪贴板与常用语」授权页，`input text` 打到那个页面上去了。**症状是「输入没进去」，
    像是 App 的焦点 bug。** 处理：点「不同意」退出（不给多余权限）再重试。
40. **`uiautomator dump` 会静默返回残缺树，根因是本 App 的常驻动画**（M3-W）：
    见过只有 1 个 node 的输出，grep 什么都匹配不到。**拿它当「元素不存在」的证据会误判**
    ——我据此以为「弱网提示条没出现」，截图一看提示条明明在。
    **根因（后来定位到）**：`uiautomator dump` 要等窗口 idle，而**对话主屏永远有一个循环
    动画**（Composer 的 Aurora 光球；欢迎态还有 88dp 大球与背景 blob）⇒ 主屏**稳定**拿不到树
    （连试 4 次都是 1 个 node），而设置页/地图页正常。
    判据：**dump 结果先验节点数，取证以截图为准**；在有常驻动画的屏上根本别指望 dump。
    ⚠ **对 Maestro 的影响待实测**：它的 Android driver 走 `AccessibilityNodeInfo`（不等 idle），
    大概率不受影响，但**这是推断**——第一次跑之前先 `maestro hierarchy` 验一句
    （`mobile/e2e/README.md` 写了不通过时的退路）。
41. **dev-client + USB 调试线时 keep-awake 验不出来**（M3-W）：两个污染源叠在一起——
    ① `stay_on_while_plugged_in=2`（USB 充电常亮，坑账 §9.29 那次设的）；
    ② MainActivity 一起来就带 `fl=KEEP_SCREEN_ON`（dev launcher 屏没有，来源未定位，
    项目代码里只有 `_layout.tsx` 一处调用且被开关守着）。三态对照（开关 off/on/off）
    flag 全都在 ⇒ **这条清单项在 dev build 上没有区分度**，要么关掉 ①（有锁屏风险、
    设备有 PIN 我解不开）在无人值守时段验，要么留到 release 构建。**不许拿「代码路径对」
    充当「屏幕真的不睡」。**

## 10. 与既有体系的关系（改动禁区重申）

- `hmi/`：只读。共享模块要改（真发现 bug）→ 在 hmi 侧改 + 跑 `hmi` node:test + 本计划
  白名单守卫，两边都绿才算完。
- 后端/编排/proto：零改动。App 需要后端新能力=先回主设计文档 §10 挂账，不夹带。
- `AGENTS.md`：每完成一个 M 阶段，在 §4.1 该行更新状态一句话（不复述细节，链本文档）。
