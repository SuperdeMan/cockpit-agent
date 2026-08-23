# Android 陪伴端 App（`mobile/`）：技术选型与实施计划

> 状态：**已批准（方向）**——2026-08-23 泓舟定三条需求（共存客户端 / 生产标准 / 细节授权代决），
> 五项拍板经授权由执行方代决，记录在 §9；开工从 §6 M0 开始
> 交付对象：后续执行者（人或 Agent）
> 关联：`hmi/`（**共存的座舱端，本计划对它零改动**）、`scripts/dev_stack_lib.py`（云端点契约）、
> `CLAUDE.md` §2/§3（M0 先改文档再动手）、`memory/server.py:48`（合成会话前缀契约）

## 0. 结论先行

**新建独立客户端 `mobile/`：React Native + Expo（TypeScript），不是把 HMI 包壳。**

- **产品形态**：座舱 HMI（1920×1080 车机屏）与手机/平板 App 是**两个用户端、一个后端大脑**
  ——同一 user_id 共享记忆/画像，各自独立会话，经同一 WS/HTTP 契约接入。HMI 一行不改。
- **选型逻辑**：需求变为「独立客户端 + 生产标准」后，决策维度从「复用率」变成
  「生产可演进 + 与仓库 TS 生态协同」。React Native 两头都占：原生渲染/原生模块/OTA/
  iOS 后路满足生产；TypeScript + 直接复用 `hmi/src` 那层**纯逻辑注入式模块**
  （ws 重连、请求归属、确认台账、PCM 播放调度……全部零 DOM、带 node:test）满足协同——
  这层恰好是两端**必须一致**的会话语义，天然的单一真相源。
- **分阶段**：M0 地基（1d）→ M1 对话 MVP（3–4d）→ M2 语音（2–3d）→ M3 卡片全量/车况/地图
  （3–5d）；免唤醒/S2S 条件触发（M4）；生产化清单（推送/账号/公网接入/崩溃监控）单列 M5
  挂账，**其中三项是后端工作，先立卡不排期**（§10）。

### 0.1 版本记录（v1 → v2 为什么换了结论）

v1（同日早些时候）在「把现有 HMI 做成 App」的理解下建议 **Capacitor 包壳**，决定性论据是
「~95% 复用 + 语音链路全建在 Web API 上」。泓舟随后给出三条需求：**① HMI 与 App 共存、
是不同用户端不是替换；② App 要满足后续支持生产的标准；③ 细节授权代决。**
①拿掉了「整体复用 HMI UI」这个前提（App 的 UI 本来就要移动优先重新设计），
②把 WebView 形态的天花板（后台执行、厂商推送、原生地图 SDK、音频延迟档位、商店/合规姿态）
从「PoC 可接受」变成「选型必须回答」。两条一起把结论从 Capacitor 翻到 React Native。
v1 的调研结论**大部分仍然成立且被 v2 继承**（通信面开放性、云端点契约、纯逻辑层可移植性、
声纹信道敏感性），作废的只有「包壳 + 给 hmi/ 做响应式改造」这条路线本身。

## 1. 需求（泓舟 2026-08-23）

1. HMI 和 Android App **共存**，是不同的用户端，不是替换。
2. App 要做到**后续可以支持生产**的标准，架构和选型据此定。
3. 五项拍板（v1 §9）授权按执行方建议代决。

由 1 推出的硬约束：**本计划对 `hmi/` 零改动**（共存意味着座舱端继续按它自己的节奏演进；
v1 里「给 hmi 做三形态响应式」出计划）。由 2 推出的硬约束：选型必须给出**原生能力、
发布/更新、观测、多环境**四条生产路径，不能只回答「能跑」。

## 2. 现状与证据（v2 视角重述，v1 调研的存活部分）

### 2.1 后端接入面——App 是「第二个已被支持的客户端」

- 主链 WS：edge-gateway `/ws?token=`（`gateway/edge/main.go:33` CheckOrigin 恒真）；
  音频/记忆面 HTTP+WS：llm-gateway `/api/*`（`llm-gateway/http_server.py:1220` ACAO `*`）
  ⇒ **两个网关本来就不关心客户端是谁**，App 不需要后端开新口。
- 鉴权：`AUTH_TOKENS` 每条 `token:user_id:vehicle_id:scope-csv`（`.env.example:271-275`），
  scope fail-closed ⇒ 多端多 token、同人共记忆、按端裁能力，**零后端代码**。
- 会话语义：焦点态/候选集/挂起确认全部按 session 隔离（context 系统），多端同时在线
  互不串——QA 轮验过的「响应按 request_id 归属、确认按 operation_id 台账」契约
  （`requestRouting.mjs` / `pendingOps.mjs`）就是为并发/交错场景硬化出来的。
- 云端点：`https://{TAILNET_FQDN}:8443`（edge）/ `:8444`（audio），LE 证书
  （`scripts/dev_stack_lib.py:346-365`）⇒ 开发期设备装 Tailscale 即直连；生产公网接入是
  后端另一条工作线（§10）。
- 会话前缀契约：合成会话跳过记忆抽取的名单是 `eval-,e2e-,ctxe2e-,central-,review-,
  nightly-,replay-,probe-,smoke-,memtest-`（`memory/server.py:48`）。HMI 用 `demo-`；
  **App 用 `app-` 前缀**——不在跳过名单（记忆正常抽取），且观测面可分端。

### 2.2 可复用资产——不是 UI，是「会话语义层」

`hmi/src` 里一层刻意做成「纯逻辑 + 依赖注入 + node:test 无 DOM」的模块，恰好是任何
客户端都必须实现一致的部分：

| 模块 | 职责 | App 端用法 |
|---|---|---|
| `ws.mjs` | 指数退避重连 + 有界发送队列（断线不丢消息） | 原样（注入 RN 全局 WebSocket） |
| `requestRouting.mjs` | 响应按 `request_id` 归属，对不上即丢帧 | 原样 |
| `pendingOps.mjs` | 确认台账（≤3、按 `operation_id`） | 原样 |
| `types.ts` | WS 消息/卡片数据契约 + 能力目录 + 默认值 | 原样（契约单一真相源） |
| `pcmPlayer.mjs` | 流式 TTS PCM 分片调度（jitter/无缝拼接/underrun/barge-in），**ctx 注入** | 注入 `react-native-audio-api` 的 AudioContext |
| `proactiveSpeech.mjs` | 主动语音 SPEAK/DEFER/INTERRUPT 决策 + 投递凭据幂等 | M3 |
| `voiceLoop.mjs`（143 例）、`s2sClient.mjs`、`sileroEndpoint.mjs` | 免唤醒 FSM / S2S 客户端（wsFactory+playerFactory 全注入） | M4 条件触发 |

共享机制见 §5.3：**不复制、不搬家**——Metro monorepo 直引 + 白名单守卫。

### 2.3 v1 里作废/降级的结论

- ~~给 `hmi/` 做三形态响应式~~ → 出计划（座舱端保持 1920×1080 形态）。
- ~~Capacitor 包壳 + COOP/COEP 响应头注入解锁 KWS WASM~~ → 不再相关；App 端如做免唤醒，
  VAD 走 `onnxruntime-react-native`（官方包）跑同一个 silero 模型，KWS 走 sherpa-onnx
  Android AAR 原生模块（M4 再议，手机形态是否要免唤醒先过产品判断）。
- 声纹结论**升级为约束**：模板对采集信道敏感（`pcmRecorder.mjs` 真机实测同人余弦
  0.73→0.48 那条）⇒ **手机麦注册的模板不保证在车内可用，声纹注册留在座舱端**，
  App 不做声纹注册入口（识别是后端行为，不受影响）。

## 3. 目标与非目标

**目标**：一个生产架构的 Android App（Expo/RN，TypeScript），手机竖屏 + 平板双形态，
连同一个后端：对话流/卡片/多轮确认/流式 ASR·TTS 为第一轮交付；车况镜像、主动消息
（前台）、地图卡为第二轮；免唤醒/S2S 条件触发。iOS 不在本计划验收内但选型已留路。

**非目标**：
- 不改 `hmi/`、不改后端契约、不改编排核心（引用共享模块不算改）。
- 不做后台常驻语音、不做声纹注册入口（§2.3）、不做把平板当替代车机屏的车规承诺。
- 商店上架、厂商推送、公网接入**本轮不做**，但架构按 §10 预留（生产标准=路径清晰，
  不等于第一轮全做）。
- Dashboard 不进 App。

## 4. 技术选型

### 4.1 决策维度（需求变更后）

D1 独立客户端（UI 移动优先新写）／D2 生产可演进（原生能力·发布·观测·iOS 后路）／
D3 仓库生态协同（TS、契约共享、单一真相源纪律）／D4 语音核心交互（流式 PCM 上下行）／
D5 中国生产环境（无 GMS、厂商推送、高德原生 SDK、资产本地化）。

### 4.2 候选对比

| 维度 | **React Native + Expo（选定）** | Capacitor（v1 方案） | Flutter | Kotlin/Compose 原生 |
|---|---|---|---|---|
| D1 移动优先 UI | 原生渲染，移动组件生态成熟 | WebView 里新写 web UI，可行但天花板低 | 强 | 最强（Android） |
| D2 生产演进 | 大厂量产案例充分；OTA=expo-updates 可自托管；原生模块生态；同码出 iOS | 可生产但后台/推送/地图/音频档位全要跨 WebView 桥 | 强，但见 D3 | Android 最强；iOS 全重写 |
| D3 生态协同 | **TypeScript；§2.2 会话语义层直接复用；契约同源** | 强 | **零复用 + 新语言（Dart）**，一人 + Agent 维护双语言栈 | 零复用 + 新语言 |
| D4 语音 | 原生采集模块小而稳；`react-native-audio-api` 提供 Web Audio 兼容 AudioContext ⇒ `pcmPlayer` 注入即用 | Web Audio 可用但受 WebView 版本碎片支配 | 原生插件 | 原生 API 直用 |
| D5 中国生产 | AMap RN 桥/厂商推送桥都是常规工程 | 弱-中 | 常规 | 最顺 |
| 到 MVP 工作量 | 6–8d（M0+M1+M2） | 4–5d | 3–4 周 | 4 周+ |

### 4.3 结论

**React Native + Expo（dev-client 工作流，开工时最新稳定 SDK）。**
淘汰逻辑：Kotlin 与 Flutter 输在 D3——这个仓库的客户端语义层（消息契约、确认台账、
请求归属、PCM 调度）已经以 TS/JS 存在且被测试固化，换语言等于再养一份实现，
和「同一件事只许一份实现」的仓库纪律正面冲突；且团队形态（一人 + Agent）养不起双语言栈。
Capacitor 输在 D2——需求 ② 把它 v1 时代「PoC 可接受」的天花板变成了不可接受。
RN 的已知代价（原生桥调试、升级噪声）用 Expo 管理的工程结构 + 尽量少的自研原生模块
（只有音频采集一个）来控制。

### 4.4 刻意不做

- **不做 Kotlin Multiplatform / 自研双端框架评估**：D3 同样不过，评估本身就是开销。
- **不预付 iOS**：目录与选型留路（RN 同码），但不写进任何验收。
- **不把 App 做成「远程车控遥控器」来立项**：车控 scope 默认不发给手机档（§5.6），
  远程车控是产品决策 + 安全评审级动作，不由客户端计划夹带。

## 5. 架构方案

### 5.1 目录与规范先行

新顶层目录 `mobile/`（Expo/RN 工程，TypeScript strict）。M0 第一步改文档：
CLAUDE.md §2 技术栈表加行「移动端 mobile/ ｜ React Native + Expo (TypeScript)」、
§3 目录约定加 `mobile/` 条目；`mobile/README.md` 写运行/构建/真机调试指引。

```
mobile/
  app.config.ts        # 环境 scheme（dev/staging/prod）；dev 才开「任意服务器」入口
  src/
    core/
      api/             # WS 会话客户端（共享 ws.mjs + types 契约）、audio-api HTTP/WS
      session/         # 消息流状态机：请求归属/看门狗/确认台账（共享 requestRouting/pendingOps）
      voice/           # 录音器接口(native spike) + 流式 ASR 上行 + TTS 播放(共享 pcmPlayer)
      config/          # 服务器配置 + token（expo-secure-store → Android Keystore）
      obs/             # trace_id（与 HMI genTraceId 同构）+ 会话前缀 `app-`
    ui/                # 设计 token 的 RN 版（--au-* 色板/字阶/玻璃卡片视觉的移动转译）
    features/
      onboarding/      # 首启：服务器预设(cloud FQDN 派生 8443/8444，与 dev_stack_lib 同构)+token+连接测试+权限
      chat/            # 对话流（speech_delta 流式/思考占位/确认条/错误态）
      cards/           # 卡片族 RN 渲染（按 types.ts 数据契约，分批移植）
      vehicle/         # 车况镜像页（vehicle_state 广播）
      settings/
  e2e/                 # Maestro 流（对话/语音/重连/确认）
```

### 5.2 平板 + 手机自适应（App 内，一套代码）

- 手机竖屏：单栏对话流，卡片全宽，确认条置底，PTT 按钮拇指热区。
- 平板（≥ ~700dp 短边）：**master-detail**——左对话右「上下文面板」（车况/地图/当前卡片
  聚焦），即 HMI「上下文舞台」概念的移动转译；横竖屏都按窗口尺寸类（WindowSizeClass 思路）
  切形态，不写死分辨率。
- 全程用 RN 布局原语（Flexbox + useWindowDimensions），不做像素级车机复刻——
  两端视觉同源（Aurora token 色板/字阶），形态各自为战。

### 5.3 与 HMI 的共享机制（单一真相源，不复制不搬家）

- Metro `watchFolders` 加仓库根，`resolver.sourceExts` 补 `mjs`；mobile 经别名
  `@shared/*` 只允许引 `hmi/src` 里的**白名单模块**（§2.2 表）。
- **白名单是台账 + 机器守**（仓库惯例）：`mobile/shared-allowlist.json` 列明每个共享模块
  + 准入判据（零 DOM/零 import.meta.env/依赖闭包封闭），配一条测试扫描 mobile 源码里
  `@shared` 的实际引用集合与台账逐项比对，**引了台账外的模块即红**；同一条测试对台账内
  模块断言「不含 `window.`/`document.`/`import.meta`」——防止座舱端日后无意把 DOM 依赖
  写进共享模块（hmi 自己的 node:test 本来就在守行为，这条守的是**边界**）。
- 刻意不抽 `shared/` 顶层包：hmi 的 Docker 构建上下文是 `hmi/` 目录（`hmi/Dockerfile:1`），
  抽包会逼着动 compose/云端 release 链——共存原则下不值得为共享机制去碰部署面。
  若日后共享面扩大再立独立卡迁移（届时才动构建上下文）。

### 5.4 语音链路（App 端形态）

- **M2 PTT 流式 ASR**：原生采集模块（16k mono s16le 分帧）→ WS `/api/asr/stream`
  PCM 直传协议（与 HMI 同一条服务端协议，聚包 ~100ms）。采集库 spike 二选一：
  `react-native-audio-record`（AudioRecord 直出 PCM，成熟）vs `react-native-audio-api`
  的 recorder；包一层 `core/voice/recorder.ts` 接口（帧回调形态与 HMI `vad.onFrame` 同构），
  选错可换。
- **M2 流式 TTS**：WS `/api/tts/stream` → **`pcmPlayer.mjs` 原样**，ctx 注入
  `react-native-audio-api` 的 AudioContext（它实现 createBuffer/source.start(when)/
  currentTime 这组契约；spike 首日验证，缺口则写薄的 AudioTrack 原生模块顶上，
  pcmPlayer 不改）。
- **M4（条件触发）免唤醒/S2S**：`voiceLoop.mjs` FSM 与 `s2sClient.mjs` 全注入、直接可用；
  引擎侧 VAD=`onnxruntime-react-native` 跑同一个 `silero_vad.onnx`，KWS=sherpa-onnx
  Android AAR。触发条件：M2 真机验收通过 + 产品确认手机形态需要免唤醒（手机不是车机，
  常开麦有它自己的产品/合规问题）。
- 声纹：不做注册入口（§2.3 信道约束）；视觉抓帧：M4 随免唤醒一并评估。

### 5.5 Android 系统面（生产标准清单）

| 事项 | 做法 |
|---|---|
| 权限 | RECORD_AUDIO 首启引导申请；CAMERA/定位按需延迟；权限用途文案齐（合规） |
| 网络 | dev scheme 允许 cleartext（LAN 本地栈调试）；staging/prod 只 wss/https；证书=系统信任链（Tailscale LE 可直用） |
| 凭证 | token 存 expo-secure-store（Keystore），不落 AsyncStorage/日志；输入后只显尾 4 位 |
| 前后台 | 前台恢复即重连（共享 ws.mjs 退避 + 队列）；不做后台保活；息屏断流按「断线不丢消息」语义兜底 |
| 常亮 | 设置开关（桌面支架/车载场景），expo-keep-awake |
| 字体/资产 | 全本地打包（Inter/Noto Sans SC/JetBrains Mono 均 OFL），零 CDN |
| 观测 | trace_id 上行同 HMI；崩溃监控 M5 接 Sentry（可自托管）；构建号/版本随 obs meta |
| 构建/发布 | Expo CNG（`android/` 不入库，prebuild 生成；config plugins 为原生配置真相源）；本地 gradle 出 APK/AAB；GitHub Actions 加 mobile job（tsc+lint+单测+debug APK 产物）；签名 keystore 走 `.env`/CI secret，不进仓库 |
| 支持底线 | minSdk 跟 RN 默认（API 24）；**官方支持承诺 Android 10+**（测试矩阵只覆盖 10+） |

### 5.6 多端语义与鉴权

- **App 发独立 `AUTH_TOKENS` 条目**：同 `user_id`（记忆/画像跨端共享——「一个大脑」的
  产品含义），独立 token（可单独吊销），**手机默认档 scope 不含 `vehicle.control`**；
  另备一条全 scope 条目供平板车载档/受控实验（fail-closed 模型零代码支持）。
- 会话前缀 `app-`（§2.1）；多端同时在线：会话隔离已由后端保证，主动消息两端都收、
  按投递凭据幂等去重（`proactiveSpeech.mjs` 的 deliveryIds 机制原样用）。
  「不在线端经厂商推送补投」是 §10 的后端工作，App 端 MVP 只做前台接收。

## 6. 分阶段落地

> **逐任务级展开（接手者照做的执行真相源）见
> [`2026-08-24-mobile-app-implementation-plan.md`](2026-08-24-mobile-app-implementation-plan.md)**
> ——含协议契约指认（附录 §2）、每任务产出物/验收/反向验证、一次性环境准备（E1–E6，
> 多数须泓舟执行）、里程碑 checklist 与坑账。本节只保留阶段口径。

### M0 地基（1d）
文档先行（CLAUDE.md/README）；`mobile/` 脚手架（Expo TS strict + dev-client）；
Metro 共享接线 + 白名单守卫测试；core/api 用共享 ws.mjs 连云栈；onboarding
（服务器预设/token/连接测试/麦克风权限占位）。
**验收**：真机（手机）发文字收到流式回复原文打印；白名单测试红绿各验一次（反向验证：
故意引台账外模块须红）。

### M1 对话 MVP（3–4d）
消息流状态机接共享 requestRouting/pendingOps（思考占位/看门狗/取消/多确认条）；
chat UI + 高频 6 卡族（天气/POI 列表/路线/新闻/股票/提醒）RN 渲染；设置基础
（主题/字号/服务器）；手机 + 平板两形态外壳。
**验收**：真机双设备过——文字/卡片/危险动作二次确认/断网重连队列 flush/锁屏恢复；
**失败态之后再说一句**（QA 轮纪律）；`tsc`+lint+单测绿。

### M2 语音（2–3d）
采集 spike 定库 → PTT 流式上屏（partial/final）；流式 TTS（pcmPlayer 注入）+ 自动播报
开关；播报中来新请求的打断语义（stopTTS）。
**验收**：真机 PTT 边说边上屏、松手定稿自动发送、TTS 首音体感 <1.5s；弱网（蜂窝）下
ASR 回退批处理路径走通；语音设置持久化。

### M3 卡片全量 + 车况 + 主动 + 地图（3–5d）
其余卡族补齐（按 `?demo` 夹具语料逐族对数据契约）；vehicle_state 车况页；前台主动消息
（横幅 + deliveryIds 幂等）；地图卡接 AMap RN 桥（react-native-amap3d 或届时官方桥；
key 绑包名签名）。
**验收**：Maestro e2e 流（对话/确认/重连/语音冒烟）进 CI 可跑档；两形态全卡族清单过。

### M4 进阶语音（条件触发，不排期）
免唤醒（voiceLoop+RN 引擎）/ S2S / 视觉抓帧。触发条件见 §5.4。

### M5 生产化清单（发布前，依赖 §10 后端）
厂商推送接入、账号体系升级（静态 token → 正式鉴权）、Sentry、OTA 通道（expo-updates
自托管）、商店物料与隐私合规（权限用途/个保法文案）、性能回归（首屏/内存/弱网矩阵）。

## 7. 验收标准（全局）

- 既有一切零回归：hmi node:test / 全量 pytest / CI 不受影响（唯一交集=共享模块**只读**）。
- mobile 自身门禁：`tsc --noEmit` strict、eslint、单测（会话语义层直接跑共享模块的
  node:test + mobile 侧 jest）、白名单守卫；CI job 出 debug APK 产物。
- 真机矩阵：手机（竖屏主力）+ 平板（两朝向）各一台，Android 10+；网络三态
  （WiFi/蜂窝+Tailscale/断网）。
- 纪律条款：不许只跑 happy path（锁屏中收流/后台 30s 回前台/多确认条并存/多端同 user
  并发互不串）；红线自查（S2S 三条件、声纹不作鉴权、车控 scope 手机档不发）。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| `react-native-audio-api` 与 pcmPlayer 契约有缺口 | M2 首日 spike 验证 createBuffer/start(when)/currentTime 三点；缺口=写薄 AudioTrack 原生模块，pcmPlayer 不动（注入设计就是为这个） |
| 采集库质量参差 | recorder.ts 接口先行，spike 二选一，帧形态与服务端协议由 HMI 已验的 PCM 直传契约钉死 |
| RN/Expo 升级噪声 | 锁 SDK 大版本一轮交付内不升；原生自研面积压到最小（音频一个模块） |
| 共享模块被座舱端演化破坏 | 白名单守卫断言边界（零 DOM/零 env）；hmi 自身 node:test 守行为；两边同仓同 PR 可见 |
| Metro monorepo 接线坑（mjs 解析/重复 React） | M0 一次性趟平（sourceExts+nodeModulesPaths），进 mobile/README 记操作账 |
| Tailscale 依赖（开发期） | 连接状态如实呈现；生产公网接入挂 §10，App 端只认 URL 不认网络形态 |
| 多端主动消息重复打扰 | deliveryIds 幂等已有；治理器侧「同类合并/频控」本就全局；实测列进 M3 验收 |
| AMap RN 桥维护风险 | 地图只在 M3 卡片档引入，核心链路（对话/语音）不依赖它；桥失效降级为静态卡 |

## 9. 决策记录（v1 五项拍板，经授权代决）

1. **选型**：React Native + Expo（§4.3；v1 的 Capacitor 结论随需求变更作废，§0.1 留痕）。
2. **原生工程**：Expo CNG，`android/` 不入库，config plugins 为真相源；某项原生需求
   超出 plugin 能力时再评估入库（届时单独立卡）。
3. **token 策略**：同 user_id + App 独立条目 + 手机默认档不含 `vehicle.control`；
   平板车载档/实验另发全 scope 条目（§5.6）。
4. **支持底线**：minSdk 跟 RN 默认（24），官方支持承诺 Android 10+（§5.5）。
5. **排期**：M2 语音随第一轮交付；免唤醒/S2S（M4）条件触发不排期（§5.4 触发条件）。

## 10. 生产前置的后端工作（不在本计划内，先挂账）

1. **公网接入面**：Tailscale 是开发形态；生产要正式入口（TLS 终结/限流/WAF/网关鉴权
   加固）。App 端已按「只认 URL」解耦。
2. **账号与鉴权升级**：静态 `AUTH_TOKENS` → 正式用户体系（注册/登录/token 轮转）；
   App 的 `core/config` 模块边界已为此留好。
3. **离线投递**：主动引擎补「设备注册表 + 厂商推送通道」投递适配器（当前只有 WS 在线投递）；
   涉及 proactive/ 与新推送服务，独立设计。

以上三项到 M5 前由泓舟决定排期；App 侧不被它们阻塞。
