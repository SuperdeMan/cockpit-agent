# Android 端到端响应时延复盘：同一句「今天天气怎么样」为什么 App 比 HMI 慢（2026-09-16）

> 起因：用户实测同一指令 HMI 快于 Android，「查天气预计要 10s 才返回」，与同类产品相比太慢。
> 证据绑定：设备 = OPPO PEUM00 / Android 14（test 机）；设备上的包 = prod 常驻包 `40ccb9b6e`（2026-09-14 22:05 装机，
> 设置页底行 `v0.1.0 · prod · 40ccb9b6e · 2026-09-14 21:52`）；云端 = `target=cloud`、生产 release `40ccb9b6`；
> 后端读数取自 collector（`/api/sessions` / `/api/turns/{trace}`），App 侧读数取自对话页只读诊断屏 `/turn-timeline`。
> 手机 → 云主机路径本轮为直连（ICMP 31–59ms，avg 40ms），不再是 09-12 那条美西 DERP 中继。

## 0. 一句话

**「10s」拆开是两段，两段都实测到了**：① **Android 独有的 20s**——位置相关的问题（天气 / 附近 / 我在哪 / 导航出发地）
在发送前要等一次「新」定位，`Accuracy.Highest` + 20s 上限，室内 GPS 没有天空、GMS 网络定位在大陆等不到，
于是**每一轮都等满 20s 才把帧发出去**（两次实测 +20094ms / +20060ms `request_sent`）；② **端云共有的 3–4.5s**——云侧规划
LLM（MiniMax-M3，每轮 ~10.6k prompt token、`cache_hit` 0）p50 2.2s、p90 4.0s、尾部 8–16s，加装配 0.6s 与天气 Agent 0.5s。
HMI 没有第①段（浏览器定位 `maximumAge: 30s`，桌面机秒回），所以看起来「HMI 快得多」；
第②段两端一样，是和同类产品拉开差距的真正来源，要另立项（§5）。

## 1. 读数

### 1.1 服务端：同一句话、两端、同一份后端

| 时刻 | 会话 | 输入 | 服务端单轮 | 规划 LLM | 规划前装配 | 天气 Agent | trace |
|---|---|---|---|---|---|---|---|
| 09-14 23:21:07 | HMI `demo-9s8nce` | 今天天气怎么样啊（文字） | **3061ms** | 1 次 1697ms | 835ms | 523ms | `4ae1d99cadcaff07` |
| 09-14 23:18:08 | App `app-6z9vwa` | 今天天气怎么样啊（PTT） | **3650ms** | 1 次 2415ms | 731ms | 498ms | `8100871f704ef2b9` |
| 09-16 16:39:27 | App `app-w6va4w` | 今天天气怎么样（文字，用户自己发的那轮） | **9328ms** | 1 次 **8258ms** | 521ms | 543ms | `540598c995e0acf8` |
| 09-16 17:16:20 | App `app-w6va4w` | 今天天气怎么样（文字，本轮受控复现） | **3801ms** | 1 次 | — | — | `3d0830c064ae2c20` |

天气 Agent 的 0.5s = `city_lookup` 240ms（串行）→ 六个 qweather 调用并行 ~250ms → 高德逆地理 47ms；没有余量。

近 5 天 collector 里全部 App 轮（83 次规划调用）与 HMI 轮（5 次）的规划 LLM 时延：

| 端 | n | p50 | p90 | max | 规划前装配 p50 |
|---|---|---|---|---|---|
| App | 83 | 2245ms | 3987ms | 16419ms | 627ms |
| HMI | 5 | 2274ms | 2500ms | 3066ms | 506ms |

⇒ **服务端没有「App 比 HMI 慢」这件事**：同一个 planner、同样的 prompt 体积、同一家模型；16:39 那轮的 9.3s 是 MiniMax-M3 的尾部
（单次 8.3s），近 168h 该 caller 485 次调用平均 2.63s、累计 4.82M prompt token（≈ 每轮 9.9k）。两次规划的轮次约占三分之一
（`salvage_wire_accepted` / `no_action_unconfirmed`，09-12 评审 §3.2 已分析、09-13 离线 A/B 裁决保留）。

### 1.2 App 侧：发送前那 20s

`/turn-timeline`（prod 包自带的只读诊断屏，`xiaozhou://turn-timeline` 深链可开）：

| 轮 | `request_sent` | `tts_text_sent` | `first_pcm_received` | `play_scheduled` | `play_ended` |
|---|---|---|---|---|---|
| 16:39:05 用户自己发的（trace `540598c9…`） | **+20094** | +20245 | +29752 | +29753 | +37619 |
| 17:15:58 本轮受控复现（Maestro `inputText` + adb 点发送；trace `3d0830c0…`） | **+20060** | +20061 | +24182 | +24183 | +31864 |

两轮的 `request_sent` 都落在 **20.06–20.09s**——正是 `currentFix(timeoutMs = 20_000)` 的上限加一次 `getLastKnownPositionAsync`。
收到首片 PCM 的时刻减去 `request_sent` 就是服务端那段（9.5s / 4.1s），与 collector 的 9.3s / 3.8s 对得上。
用户看到的「发出 → 听到答案」分别是 **29.8s** 与 **24.2s**；其中 20s 在手机上什么都没发生（状态栏只有一个定位图标）。

### 1.3 为什么 20s 一次都省不掉（成因链）

1. `sendRouter` 判「今天天气怎么样」位置相关（`@shared/location.mjs::isLocationDependent`：天气词 + 没有显式地点）；
   定位开关打开时 `SessionCore.send` 先 `prepareRequest` → `appLocationBridge.refreshMeta()` → 拿到再 `transmitRequest`。
2. 旧 `currentFix`：`Location.getCurrentPositionAsync({ accuracy: Highest })` 与 20s 定时器赛跑，输了才 `getLastKnownPositionAsync()`。
3. expo-location 57.0.13 的 Android 实现把它翻译成 GMS `FusedLocationProviderClient.getCurrentLocation(CurrentLocationRequest{
   priority = HIGH_ACCURACY, maxUpdateAgeMillis = 1000 })`——**系统缓存里不是 1s 内的定位一律不认**（`LocationHelpers.kt::
   prepareCurrentLocationRequest` 用 `locationParams.interval` 喂 `setMaxUpdateAgeMillis`，Highest 档 interval = 1000）。
4. 这台机 `dumpsys location`：GPS provider 最近一次定位是 **6 天前**（09-12 户外，贵州兴义），室内没有天空；GMS 的
   `network_location_provider` 需要 Google 服务，请求窗口（16:39:05–16:39:25）内没有产出（下一次网络定位出现在 16:42:58，
   来自别的请求）。⇒ 20s 内不可能有满足条件的定位，**每一次都等满**。
5. 20s 后回落到 fused `lastLocation`（深圳南山，年龄未知）——答案本来就是用这个坐标给的。也就是说这 20s 换来的坐标，
   和 0s 就能拿到的那一个**是同一个**。
6. HMI 对照：`requestCurrentLocation()` 用浏览器 `getCurrentPosition({ maximumAge: 30_000, timeout: 10_000 })`，PC 上
   是 WiFi/IP 定位、且 30s 内缓存直接复用，几百毫秒内返回。

「深圳今天天气怎么样」（带显式地点）不走这条闸，所以 09-14 D-03 那格量到的「发送→排定 3061ms」是对的，也是它把这个问题
藏了两天——**同一个功能，有没有城市名差 20s**。

## 2. 修法（mobile，本轮已落地）

判据只有一份：`mobile/src/core/location/fixPolicy.ts::acquireFix`（纯函数、注入依赖、jest 直测）：

| 步 | 做法 | 等待 |
|---|---|---|
| ① 缓存即用 | `getLastKnownPositionAsync({ maxAge: 5min })`（fused `lastLocation`，全设备共享的缓存：地图 / 微信刚定过位就在里面）有就**立刻发** | 0 |
| ② 只等预算 | 没有 ⇒ 现取一次（`Highest` + `timeInterval: 5min` ⇒ GMS `maxUpdateAge` 5min），**最多等 3s**（`FIX_BUDGET_MS`）；那条原生 promise 不取消，晚到的结果只喂缓存 | ≤3s |
| ③ 陈旧回落 | 预算到点 ⇒ 任何年龄的 `lastLocation`（同旧行为的回落）；再没有 ⇒ 不带坐标照发（Q4 判据：闸认不准就放行） | 0 |
| 退避 | 一次现取刚在预算内失败过（`FRESH_FAILURE_BACKOFF_MS` 10min 内；候选包上是 2min，§6）⇒ 下一次发送**一毫秒都不等**、直接③，后台继续试 | 0 |
| 预热 | 会话建立 / 回前台 / 打开定位开关时后台取一次（`warmLocation`，20s 上限、同一时刻一条、不弹权限申请），把缓存喂热，让①命中 | 不阻塞 |
| 征询路径 | 用户点了「同意」是显式动作：预算放宽到 8s（`CONSENT_FIX_BUDGET_MS`），仍走同一份判据 | ≤8s |

`current_location_at` 照旧如实上行，坐标新鲜度仍由下游判（context.py 那条）。发送路径的最坏情况从 20s 变成 3s，
室内连续提问时从第二问起 0s。

单测：`mobile/test/locationFix.test.ts` 11 条（缓存即用 / 偏旧要求补取 / 无时间戳 / 预算内新定位 / 预算到点回落陈旧 /
全失败为 null / lastKnown 抛异常不影响回落 / 预算可注入 / withinBudget 超时不取消 / skipWait 两条）；
`mobile/test/expoLocationNative.test.ts` 2 条钉住 expo-location 原生实现的两条前提（`timeInterval → maxUpdateAge` 映射、
`getLastKnownPositionAsync` 读 fused `lastLocation`），库升级即红。

## 3. 本轮没改的事

- HMI 那条 `timeout: 10_000` 同样会在没缓存时最多等 10s，只是桌面机从没触发过；本轮不动 `hmi/`。
- 规划重试策略、catalog 体积、模型选型都没动（§5 另立项）。
- 天气 Agent 的 `city_lookup` 串行 240ms 可以并到六个调用前面省 ~0.2s，收益小于噪声，本轮不动。

## 4. 验证

本地：mobile `tsc --noEmit` 0、`eslint . --max-warnings 0` 0/0、jest 100 suites / **1042 passed**（原 1029 + 本轮 13）。
第一次全量并行跑时 `assistantPresence` / `chatHierarchy` 各有一条首用例在负载下超过 5s 单测上限（同时跑着 eslint 与其他会话的
node 进程），单独复跑 15s 内通过、随后整套复跑 1042 全绿；不是逻辑失败。
服务端（§7）：`runtime/tests/test_city_slot_placeholder.py` + `agents/info/tests/test_agent.py` + `orchestrator/cloud/tests/test_context.py`
相关选择 **68 passed**；全量固定口径见 §4.1。

真机 A/B 见 §6。

### 4.1 全量固定口径

工作树 = 本页全部改动（未 commit）：`TZ=UTC0 python -X utf8 -m pytest -q -n 8 --dist worksteal` **8369 passed / 32 skipped / 13 warnings**
（319s；上一基线 `40ccb9b6` 8340 / 32，新增即本页 §7 的用例）。四道门禁与端侧 smoke 本轮未跑——改动不碰 skills / exemplars /
FastIntent / capability 声明。

## 8. 发布与清洁包（2026-09-16，用户授权「2 和 3 都做」）

- 提交：`4f00596e`（mobile 定位取值策略）、`8fc7638a`（runtime 占位城市归空）、`97825faa`（本页 + 总表 + 指针 + 历史）；
  push `bcb10eb0..97825faa`（三条，origin/main 之前与本地 HEAD 一致、无陌生提交）。
- 部署：`dev_stack.py deploy --sha 97825faa` dry-run `status=dry_run`、零阻断、零 warning（24s）→ `--apply` `submitted`（147s）→
  独立 `status`：`ok`、5/5 healthy、`release_sha` = `running_release_sha` = `97825faa`、零 warning → `verify` **`verified`**
  （artifact `20260916T104219Z-97825fa.json`，provider/model `minimax:MiniMax-M3`，75s）。生产基线从 `40ccb9b6` 推进到 `97825faa`。
- 清洁包：`xiaozhou-companion-prod-release-97825faa6-20260916-1849.apk`（clean 树、`-Release -Variant prod -CompileJobs 3`，12m43s；
  APK SHA-256 `86999608…0cf7`，本地 = 设备 `pm path` 逐字节相同；签名同模板 `5e8f1606…`）已装为 OPPO 常驻包
  （`lastUpdateTime 2026-09-16 18:51:05`，设置页底行 `prod · 97825faa6 · 2026-09-16 18:36`）。装机后同题一轮（新会话，
  trace `f9ba1a8716ab8f00`）：`request_sent` **+99ms**、服务端 6426ms（两次规划 2.65s + 2.65s，`no_action` 重试）、
  首片 PCM +6928ms，答「深圳市南山区当前多云…」✅——发出 → 听到 **6.9s**，其中手机侧 0.1s。
- 搁置的旧 CMake 配置目录 `D:\Android\builds\cxx\onnxruntime-react-native.stale-1.29-20260916` 已删。

## 9. 明确没做的事

- 没有改 HMI 的定位等待（`timeout: 10_000`）；没有改规划重试 / catalog / 模型（§5 待裁决）；
- 没有取得真人语音轮的读数（本页全部是文字轮；语音轮的 ASR 段另计，见 09-12 评审）；
- 没有动 Xiaomi 对照机（离线）；候选包与清洁包的 ORT 原生库随 `latest.integration` 漂到 1.30.0（常驻包 `40ccb9b6e` 是 1.29.0），
  与本页读数无关，但 KWS / VAD 那条路径在新包上没有单独复测。

## 5. 端云共有的那 3–4.5s：三个可选项（未实施，需要裁决）

服务端天气一轮的下限 = 装配 0.6s + 规划 LLM 2.2s（p50）+ Agent 0.5s ≈ **3.3s**，尾部由 MiniMax-M3 决定（p90 4s、max 16s）。
同类产品（车机语音助手）天气类问题通常 1–2s，差距全在「每一句话都要过一次 10k token 的规划 LLM」。三条路，收益从大到小：

| # | 做法 | 预期 | 代价 / 前提 |
|---|---|---|---|
| A | **单步只读意图的确定性快车道**：句子被端侧 NLU / route_hints / exemplars **唯一且高置信**地落到一个 `response_only` 的单步能力（天气 / 时间 / 电量 / 我在哪…）时直接装配计划，不调规划 LLM；其余照旧 | 这类轮 3.3s → **~0.8s**（装配可省记忆召回，只剩 Agent） | 改的是 `planning.py` 的出口顺序而非领域分支（判据来自 manifest 声明，同 route_hints 的哲学）；必须过四道门禁 + `eval_intent_adversarial --suite gate` 双臂对比，看误接管率；`EDGE_NLU_MODE` 现在只有 off / shadow，要加一档「use」并按域放量 |
| B | **规划 prompt 减负**：`PLANNER_CATALOG_TOP_K` 20 → 8 让预筛真正生效（14 个 Agent 全进 catalog 是 no-op）；把每轮不变的 13.6k 字符挪到 prompt 前缀试厂商前缀缓存 | 每次规划预填 −0.5–1s；两次规划的轮次收益翻倍 | 09-12 评审 §3.3 / 总表 E-04；要过落域门禁；MiniMax 是否有前缀缓存未证实（`cache_hit` 是网关自己的整句缓存） |
| C | **规划模型换档**：单域短句用更快的模型（`model_pref=fast` 已有通道），复杂多域再走 M3 | 视厂商 1–2s | 落域准确率要重新跑对抗集；MiMo 当前无可用 key |

建议顺序 A → B；C 视 A 的覆盖面再定。A 是产品层决策（它改变「LLM 只产计划」这条链的前置条件），不在本轮擅动。

## 6. 候选包真机 A/B

候选包 `xiaozhou-companion-prod-release-bcb10eb08-dirty-20260916-1736.apk`（APK SHA-256 `4999d56a…1fc9`，本地 = 设备 `pm path`
逐字节相同，非 DEBUGGABLE；`-Release -Variant prod -CompileJobs 3`，11m28s；`-dirty` = 本页 §2 的 mobile 改动，只用于 A/B）。
同一装置、同一句话、同一台 OPPO、同一室内位置，`/turn-timeline` 读数：

| 轮 | 包 | 走的路径 | `request_sent` | 服务端 | 结果 |
|---|---|---|---|---|---|
| 16:39 用户自己发的 | 常驻 `40ccb9b6e` | 等新定位 20s 上限 | **+20094** | 9328ms（LLM 尾部 8.3s） | 深圳南山天气 ✅ |
| 17:15 受控复现 | 常驻 `40ccb9b6e` | 同上 | **+20060** | 3801ms | 深圳南山天气 ✅ |
| 17:41 候选 #1（冷启动 +105s） | 候选 | 预热在 +20s 失败 ⇒ 2min 退避内 **不等** ⇒ 陈旧缓存 | **+93** | 4309ms | ⚠ 「没查到「当前位置」的天气」（见 §7，服务端问题） |
| 17:43 候选 #2 | 候选 | 同上 | **+90** | 2159ms | ⚠ 同上 |
| 17:54 候选 #3「我现在在哪里」 | 候选 | 退避已过 ⇒ 等预算 3s ⇒ 陈旧缓存 | **+3109** | 2166ms | 「您当前位于广东省深圳市南山区粤海街道深南大道9821号深铁金融科技大厦」✅（坐标确实带上了） |
| 18:03 候选 #4（重启进程、新会话） | 候选 | 预热失败 → 退避已过 ⇒ 等预算 3s ⇒ 陈旧缓存 | **+3097** | 6890ms（两次规划 2.8s + 3.0s） | 深圳南山天气 ✅ |

⇒ 发送前那段从 **20.1s** 变成 **0.09s（退避内）/ 3.1s（退避外）**；用户体感「发出 → 听到」从 24–30s 变成 4.8s / 2.6s / 5.7s / 10.5s，
剩下的全是服务端（§1.1、§5）。这台机室内 GMS **从未**在预算内给过定位，每次到期都是白等，所以退避从 2 分钟调到 10 分钟
（`FRESH_FAILURE_BACKOFF_MS`，改在候选包之后、只改常量，机制与设备证据同一份）。「缓存 ≤5min 直接用」那一档在这台室内机上没有机会露面
（GMS 缓存里没有 5 分钟内的定位），它的正确性只有单测证据。
A/B 结束已把 OPPO 常驻包换回 `40ccb9b6e`（APK SHA-256 `4aed0592…4733`，设备侧回读逐字节相同）。

## 7. 顺带抓到的服务端缺陷：占位城市「当前位置」被当城市名（已修，待部署）

候选 #1 / #2 两轮规划模型都把城市槽写成 `"city": "当前位置"`（prompt 明令「绝不编造占位值（如『当前位置』『未知』）」，
MiniMax-M3 照写；同一句在常驻包两轮给的是空槽 + `no_action` 重试）。`agents/info/src/agent.py::_resolve_city` **优先信城市槽**，
于是拿「当前位置」去查和风 GeoAPI → 400 No Such Location → 「没查到「当前位置」的天气」——而 meta 里带着正确的坐标
（#3 证明）。collector 近 7 天只有这两条命中，是模型偶发；但一旦命中，用户听到的是「城市名不准确」，且 `focus.last_city`
也会被「当前位置」污染、传给下一轮「明天的呢？」。

修法在唯一的归一入口 `runtime/slots.py::normalize_city_slot`：指代当前位置 / 明示未知的占位词（当前位置 / 我的位置 / 这里 /
本地 / 当地 / 附近 / 未知 / here / current location / unknown / none …）归空 ⇒ 有坐标用坐标、没坐标走既有 NEED_SLOT 追问；
真实地名与坐标串原样通过（「本溪」「无锡」是整词比较不是前缀）。单测：`runtime/tests/test_city_slot_placeholder.py` 4 组、
`agents/info/tests/test_agent.py::test_placeholder_city_slot_uses_gps_meta_instead_of_failing`（有坐标 → ok、无坐标 → need_slot）、
`orchestrator/cloud/tests/test_context.py` focus 归一 1 条。**生效要 deploy**（cloud-planner 与 info Agent 都读它）。
