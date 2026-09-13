# Android 陪伴端性能与链路时延评审（2026-09-12）

> 范围：`mobile/` 的 CPU / 内存 / 稳定性，以及「用户发出 → 首段有效文本 → 首片音频 → 终态」整条链路的分段耗时。
> 证据绑定：设备 = test / OPPO PEUM00 / Android 14（活跃屏外屏 988×1972 @60Hz）；设备上的包 = 2026-09-12 00:20 安装的 prod 常驻包（`e38cd75` 发布批的 OPPO 候选）；云端 = `target=cloud`、生产 release `e38cd75`；后端轮次数据取自 collector 最近 12 个 `app-*` 会话（47 轮，2026-09-11/12）与本轮 18 条只读探针轮（`perf-probe-*` 会话）。
> 本页只记本轮测到的读数与判断；本轮落地的代码改动见 §7，**未推送、未部署、未换包**。

## 0. 一句话

**稳定性没有活着的崩溃；CPU 的大头不在业务而在空闲动效和 VAD 线程池；链路的大头在云侧规划（每轮 10.5k token 的 prompt、约三分之二的轮次调两次 LLM），其次是这套测试环境的网络路径（Tailscale 走美西 DERP 中继，RTT 0.5–3s，每条新 WSS 握手 1.4s+）。**

| 项 | 读数 | 判断 |
|---|---|---|
| 崩溃 | dropbox 里 2 条 `data_app_crash`（09-11 23:48 / 23:52，`UnexpectedNativeTypeException: expected Array, got a null`，amap3d Marker）；当前包 09-12 00:20 装机后 **0 条**；无 ANR、无 native tombstone | 已由 `e38cd75` 的 patch-package 修掉，本轮未复现 |
| 对话页空闲 CPU（已连接、光球在动） | **155–160%**（1.6 核常驻）：RenderThread 62–66%、hwuiTask0/1 各 ~28%、主线程 24–27%、AudioTrack 4.6–6.8% | **最大的空闲成本**，全部来自极光光球的每帧模糊/阴影渲染；断连（光球静止）时只剩 7–10% |
| 免唤醒 ARMED 空闲 CPU | **219–239%**：在光球之上再加 ORT 三条线程各 ~25%（自旋等待）、kws-decode 7%、JS 线程 5–7% | VAD 线程池缺省起多线程，**已改成单线程**（§7） |
| 内存 | 冷启动对话页 PSS 213MB（Native 55MB）；跑了 6 小时的进程 264–302MB（Native 101–125MB）；ARMED 331MB（Native 133MB：ORT + sherpa 模型） | 无泄漏形状（Views 182–236 恒定、Activities 1）；AR09 §8.2 的结论仍成立 |
| 帧 | 空闲 55.6fps、jank 0%、p50 21ms p95 28ms；键盘弹起那一帧 `InsetsAnimation` 线程 90% | 健康进程在预算内 |
| 云侧单轮（cloud 路径） | p50 **4.3–4.5s**、p95 **7.9s**、max 32.9s（行程规划） | 规划前装配 0.63s + 规划 LLM 2.0s/次 × 平均 1.66 次 + 执行 1.3s |
| 规划 LLM（MiniMax-M3） | 每次 p50 1.95s / p95 5.6s；prompt **~10.5k token**（catalog 13,631 字符每轮原样进 prompt，`cache_hit` 0/73） | **29/44 轮调了两次**，原因见 §3.2 |
| TTS 首片（网关→云厂商） | MiniMax 首片 0.75–1.56s（首段在第一个逗号 150ms 就送出）；CosyVoice meta 0.63s、首片 1.2–2.5s | 服务侧首片正常；慢在客户端路径 |
| ASR 定稿 | fun-asr `stop`→`final` 0.67–1.81s；qwen3 0.94s；首个 partial 1.0–1.6s | 正常；每次 PTT 新建 WSS 握手另计 |
| 端到端（PC 探针，中继路径） | 电量 0.45–0.72s（端侧本地）／几点 3.0–5.3s／天气 3.8–9.4s／路线 6.0–9.9s／介绍公园 5.7–17.5s | 同一句天气 3.8s 与 9.4s 的差就是「规划调了一次还是两次」 |

## 1. 取数装置与边界

- 设备：`top -b -n N -d 5 -p <pid>`（进程）与 `top -H`（线程）、`dumpsys gfxinfo` reset 后读、`dumpsys meminfo`、`dumpsys dropbox`、`dumpsys batterystats --charged`；开关走 `mobile/e2e/tools/set_switch.py`（按 resource-id 回读）；App 内事实走 `/presence-trail`。
- 后端：collector `GET /api/sessions` / `/api/sessions/{id}/turns` / `/api/turns/{trace_id}`（spans + llm_calls）；探针脚本从 PC 直连 `wss://…:8443/ws`（主链）、`:8444/api/tts/stream`、`:8444/api/asr/stream`，语料**只读**（时间 / 电量 / 天气 / 路线 / 介绍），token 只从文件读。
- ⚠ 路径边界：PC 与手机到云主机都**只有 DERP 中继**（`tailscale ping` 报 `via DERP(lax)`，PC 最近 DERP = San Francisco；手机 ICMP RTT 1.6–3.0s，`/healthz` 一次 1.44s 其中 TLS 0.91s）。所有「客户端看到的」时延都含这段路径；服务内部分段（spans / llm_calls）不含。**不能把探针的端到端数当成产品在正常网络上的数**，能当的是分段与相对比较。
- 未测：真人语音的「说完 → 听见」（AR08 E-04 仍缺声学时基）；本轮两次在真机上发文字轮取 `/turn-timeline`，都撞上手机隧道抖动（见 §5），没拿到有效 timeline 读数。

## 2. 客户端：CPU、内存、稳定性

### 2.1 光球动效 = 1.5 核常驻（最大单项）

同一台设备、同一个包、同一屏，只差「光球动不动」：

| 状态 | 进程 CPU | 帧/60s | 线程分布 |
|---|---|---|---|
| 已连接，光球 idle 呼吸 + 三层旋转 | **153–164%** | 3339（55.6fps） | RenderThread 62–66%、hwuiTask0/1 各 27–31%、主线程 24–30% |
| 断连（`muted`，光球静止） | **7–10%** | 0 | 主线程 6.8–9.4% |
| AR09 §8.4 的 `reduceMotionForce=on` | （上一轮：0 帧） | 0 | — |

归因（`ui/aurora/AuroraOrb.tsx`）：三层 `AuroraDisk` 各挂 `filter: [{ blur }]`（RenderEffect，内容在转 ⇒ 每帧重新模糊）；球体一层 4 段 `boxShadow`（含两段 inset）随 `breathe` 缩放；四个 `withRepeat` 共享值每帧驱动 6 个 `useAnimatedStyle`。`hwuiTask` 两条线程是 HWUI 的 CPU 侧任务（阴影/路径栅格化），RenderThread 是 GPU 模糊与绘制。**这是「常态 1 个循环动画」的真实代价**：一台放在支架上的手机，只要在对话页停着就烧 1.5 核。

可选修法（都要用户裁决视觉，本轮没动）：

1. **去掉内层两圈的 `filter: blur`**（内层漩涡 / 逆向漩涡的 blur 半径只有 `size*0.05` / `0.04`，瓣本身已是 radial 软边），只留晕环那一层；预期 RenderThread 减半。
2. **阴影静态化**：`boxShadow` 不随 `breathe` 缩放（把 scale 挂在不带阴影的内层），或用一张预渲染的辉光图替代 4 段阴影；预期 hwuiTask 归零。
3. **空闲降频**：`primary === 'idle'` 且 N 秒无交互后转 `animated=false`（现有 `orbPolicy` 已是三档判据，加一档「静置」），任何交互立即恢复；这是零视觉改动之外最省的一条。
4. 验收尺子就是本页的 `top -H` 读数：目标空闲 ≤ 40%。

### 2.2 免唤醒：VAD 线程池自旋（已修）

ARMED 空闲 219–239%，减去光球那份后多出的 ~75% 全在三条继承了 `mqt_v_js` 名字的线程上（各 23–25%）——是 `onnxruntime-react-native` 缺省 session 的 intra-op 线程池，推理间隙自旋等待。silero 每 32ms 一窗、单线程不到 1ms，多线程只多出自旋的账；HMI 侧同一模型早就是 `numThreads = 1`。

修法：`InferenceSession.create(path, { intraOpNumThreads: 1, interOpNumThreads: 1 })`（RN 绑定 1.24.3 的 `cpp/SessionUtils.cpp` 明确支持这两个键）。预期 ARMED 空闲回到 ~150%（只剩光球）；**待换包在 OPPO 上复测同一协议**。

其余 ARMED 成本合理：`kws-decode`（sherpa，numThreads=1）7%、JS 线程（帧拷贝 + 512 窗重切）5–7%、AudioRecord 1.6%。

### 2.3 输出上下文常驻 running（已修）

一轮播报之后 `AudioTrack` 线程仍常驻 4.6–6.8% CPU，输出流不停、音频 HAL 不睡（`audioCtx.ts` 的 `sharedAudioContext()` 只在新建时 `resume()`，从没人 `suspend()`）。修法：收尾后空闲 15s 挂起，任何一路再取用即 resume；resume 落在 `begin()`（请求刚发出、离首片音频还有规划那 2s+），不落在首片到达那一刻；到点时播放事实仍 `live` 就再等一轮。S2S 挡位的播放器不经 `SpeechController`，本轮不给它排空闲表（S2S 是显式开启的挡位，常驻可接受）。**待换包验证：挂起后下一轮首音不变差、提示音不丢。**

### 2.4 渲染放大（低优先级，随记录变长会长）

- `AssistantProvider` 用 `useStore(core.store)` 订阅整份 store：每一片 `speech_delta` 都让整棵树重渲，`ChatBody` 里 `FlashList` 的 `extraData` 是每次新建的数组字面量 ⇒ 所有可见行重渲，每行还各算一次 `buildReceipt`；`usePresence` 每次渲染 `[...messages].reverse().find`、`currentTurn(messages)` 不带 memo。
- 有挂起确认时 `usePresence` 每秒 `bumpTick` ⇒ 整棵树每秒重渲一次（倒计时只需要 Dock 那一格动）。
- 本轮真机文字轮期间 JS 线程 ≤2.5%，记录 50 条时这不是瓶颈；持久化上限 50 条把它按住了。真要修：Provider 按 selector 拆订阅、`extraData` 用稳定引用、倒计时下沉到叶子组件。

### 2.5 其他

- 冷启动：AR09 记的 `am start -W` TotalTime p50 590ms（本轮进程已在跑，未重测）。
- 电量（自上次充满）：App CPU 13m49s，其中前台 1m18s、后台 48s、cached 1m10s；audio 15m12s；partial wakelock 10m33s（多为 AudioMix / 采集）。与 §2.1/§2.2 的读数一致——**这台机整晚的耗电大头是空闲时的光球与免唤醒**。
- 探活/重连（`core/api/liveness.ts`）：单次探活超时 4s，而这条中继路径一次 `/healthz` 就要 1.44s、RTT 抖到 3s——判死会把活着的 WS 踢掉再握手一遍。已把超时抬到 8s（§7）；本轮取证里手机端的断连主因是隧道本身（§5），这条是顺手把误判面收窄。

## 3. 后端链路：一轮 4.5s 花在哪

### 3.1 分段（cloud 路径，App 47 轮 + 探针 18 轮）

```
route.cloud ──0.63s（p50，memory on；memory off 0.20s）──▶ 规划 LLM 第 1 次 ~2.0s ──(66% 的轮次)──▶ 规划 LLM 第 2 次 1.1–5.6s ──▶ 执行 1.27s（p50）──▶ aggregate ──▶ final
                 history / recall / focus / catalog                    MiniMax-M3, ~10.5k prompt token                 chitchat 1.5s（LLM 1.4s）
                 四次串行往返（已改并发，§7）                            catalog 13,631 字符每轮原样进                  weather 0.46–0.64s（6 个 qweather 并行）
                                                                        cache_hit 0/73                                 search 3.3–8.6s（exa 0.6–2.5s + 合成 LLM 2.7–6.1s，不流式）
                                                                                                                       navigation 0.43–2.9s / vision 1.2–2.4s / trip 29s
```

| 段 | n | p50 | p95 | 备注 |
|---|---|---|---|---|
| 规划前装配（route.cloud → 第 1 次 LLM 起） | 37 | 632ms | 813ms | memory_enabled=true 的 App 轮；探针（memory off）~200ms |
| 规划 LLM 单次 | 73 | 1951ms | 5590ms | max 14.9s（澄清生成 677 token） |
| 执行（cloud.planning → aggregate） | 30 | 1268ms | 2863ms | |
| 单轮总时长 | 42 | 4304ms | 7879ms | mixed 路径 5 轮 p50 6.0s |

### 3.2 规划调两次：29/44 轮

`cloud.planning` span 的 `retry_policies`（35 个有规划的 App 轮）：

| 触发策略 | 轮数 | 第 1 次调用给了什么 | 第 2 次的代价 |
|---|---|---|---|
| `salvage_wire_accepted` | 8 | **一份合法计划，只是以正文 JSON 而不是 tool_call 送回**（如 `{"addressed":true,"steps":[{"capability_ref":"cap_0104","slots":{"city":"深圳","date":"今天"}}]}`，45 token） | 再走一遍工具通道，2.4–5.6s，**探针 3 例第 2 次给出的是同一份计划** |
| `no_action_unconfirmed` ×2 | 7 | `{"addressed":true,"steps":[]}`（10 token，但要 1.1–5.1s——时延全在 10.5k prompt 的预填） | 再抽样确认，1.1–5.6s |
| `no_action_unconfirmed` ×1 | 2 | 同上，第 2 次给出了步 | |
| `schema_validation_failed` / `explicit_input_not_addressed` | 各 1 | | |
| 一次成功 | 16 | | |

两条判断：

- `salvage_wire_accepted` 的重试是 2026-08-10 A/B（toolcall 91% vs salvage 50% 落域正确率）留下的保险；**本轮真栈里它的收益是 0、代价是每次 2.4–5.6s**。它有现成的消融开关：`PLANNER_RETRY_DISABLE=salvage_wire_accepted`（`retry_policy.disabled_policies`），建议先在云端开一轮 A/B（改 `.env` 属红线，需授权），用落域评测判它现在还需不需要。
- `no_action` 二次抽样保住的是「打开空调偶尔空手而归」那种抽风；ASR 噪声句（`你。`/`Yeah.`/`1600。`）占了 7 例中的 5 例——它们本该在拒识层就落地，不该跑两次 10.5k token 的规划。

### 3.3 prompt 体积与缓存

- 每次规划 prompt ~10.5k token，其中 catalog 渲染 13,631 字符**每轮相同**（`PLANNER_CATALOG_TOP_K=20` > 14 个 Agent ⇒ 语义预筛是 no-op，`catalog_dropped` 恒空）。MiniMax-M3 对 10.5k prompt 的预填就是那 1–2s 的底：**空计划 10 token 也要 1.1–5.1s**。
- `llm_calls.cache_hit` 73 次全 0。prompt 顺序刻意把 catalog 放在历史/记忆之后（弱模型别把旧范例当能力），代价是前缀里先出现每轮都变的日期与历史——即便厂商有前缀缓存也命不中。要吃到缓存得把「每轮不变的 13.6k 字符」挪到前缀（system 段），这与 M1a 的顺序契约冲突，需要重新做 A/B，本轮只记不改。
- 可试的减负：`PLANNER_CATALOG_TOP_K` 降到 8（预筛真正生效，edge 核心与 always-include 仍保留）；这是 R2.1 P5 / M5 讨论过的裁剪面，要过落域门禁。

### 3.4 执行段

- info 深调研：`exa.web_search` 0.6–2.5s + 合成 LLM 2.7–6.1s（574 token）**不流式**——`agents/info` 没实现 `handle_stream`，SDK 缺省把 `handle` 包成单个 final，700 字的回答一次到齐。让它像 chitchat 一样 `llm.stream` 逐句 yield，首段有效文本能从 5.7–17.5s 提前到 ~3s。
- weather：6 个 qweather 调用已并行（465ms），没有余量。
- chitchat：`现在几点了` 3.0–5.3s，其中规划占 2.6–4.8s，agent 本身 3–4ms。
- trip-planner 27–29s（LLM ct=2048 到顶）：范围外，只记。
- MiniMax TTS：网关侧首片 0.75–1.56s 正常；`_tts_send_now` 的首片门控 + RPM 桶已把请求数压到 2/句；本轮从 PC 看到「6.5s 音频用 15–34s 才到齐」，CosyVoice 同样（7.8s 音频 9–18s），**是中继路径的事，不是厂商的事**——但在同样的路径上手机端会表现为 underrun。

## 4. 客户端链路：每轮多付的握手

| 时刻 | 现在的做法 | 中继路径上的代价 |
|---|---|---|
| PTT 按下 / 唤醒进 LISTENING | `AsrSession.start()` 新建一条 `wss://…/api/asr/stream` | PC 连一次 2.0–3.0s，手机 TLS 0.9s + upgrade ≈ 1.4s；音频先攒在 `preOpen`，不丢字，但**定稿至少晚一个握手**（说得越短越明显） |
| 请求发出（`speech.begin`） | 每段新建一条 `…/api/tts/stream` | 与规划 2s+ 重叠，首段藏得住；mixed / divergent 的第二段再握手一次 |
| 探活 | 每 15s 一次 `GET /healthz`（新 TLS） | 每分钟 4 次完整握手；判死阈值 4s（已抬到 8s） |

建议：ARMED 期预连一条 ASR WSS（网关 `heartbeat=20`，闲置成本一条连接），唤醒时直接 `start`；TTS 会话在 `finishTurn` 后保留一条热连接供下一轮复用（网关的 `started` 守卫要允许同一连接第二次 `start`，需小改 `http_server.handle_tts_stream`）。两条合起来能把语音轮省下 1–2 个握手（这条路径上就是 1.4–3s）。

## 5. 网络路径：这台测试机看到的是什么

- 手机隧道会**静默失联**（`tailscale` 进程在、VPN 显示已连接，但到云主机 100% 丢包、`curl /healthz` 8s 超时），把 Tailscale App 拉到前台 6s 后恢复——与 `docs/guides/android-build-and-device-validation.md` §6 记的形态一致。
- 恢复后也只有中继：ICMP RTT 1.6–3.0s，`/healthz` 1.44s。App 侧 `/presence-trail` 记到每 ~40s 一轮 `已断开 · 消息会排队 → 正在重连…`（19:21:13 / 19:21:56 / 19:22:41），连接期最长 37s 未 open——退避与看门狗行为正确，但用户看到的就是「一直在重连」，且光球在 `muted` 态静止（§2.1 的 7–10% 读数就是这个态）。
- PC 侧同样只有 DERP：`tailscale status` 对云主机 `relay "lax"`、`CurAddr` 空；PC 最近 DERP 是 San Francisco。**云主机没有可直连的 UDP 路径**（安全组/NAT），所有客户端都绕美西中继。这不是 App 能修的，但它决定了这套环境里所有端到端读数的底：至少 +0.5s/往返，握手 +1.4s/条。要改是基础设施（安全组放行 Tailscale UDP 41641 / 换近端 DERP），属红线，需授权。

## 6. 优先级与预期收益

| # | 项 | 层 | 预期 | 状态 |
|---|---|---|---|---|
| 1 | 光球动效降本（去内层 blur / 阴影静态化 / 空闲静置） | 客户端 | 空闲 CPU 155% → ≤40%，整晚耗电大头 | 待用户裁视觉，§2.1 三选一 |
| 2 | VAD ORT 单线程 | 客户端 | ARMED 空闲 −75% CPU | **已改**，待换包复测 |
| 3 | 关掉 `salvage_wire_accepted` 强制重试（先 A/B） | 云端 env | 23% 的轮次省 2.4–5.6s | 建议，`.env` 需授权 |
| 4 | 规划前装配并发 | 云端 | 每轮 −0.3–0.5s | **已改**，66 条 context 测试绿；待 deploy |
| 5 | info 合成流式化 | 云端 agent | 深调研首段文本 5.7–17.5s → ~3s | 建议 |
| 6 | 输出上下文空闲挂起 | 客户端 | 空闲 −5–7% CPU、HAL 可睡 | **已改**，待换包验证首音/提示音 |
| 7 | ASR 预连 / TTS 热连接 | 端 + 网关 | 语音轮 −1–2 个握手 | 建议 |
| 8 | 云主机直连路径 / 近端 DERP | 基础设施 | 所有端到端 −0.5s/往返以上，音频不再欠速 | 建议，红线 |
| 9 | catalog 预筛 / 前缀缓存 | 云端 | 规划 LLM 预填 −0.5–1s/次 | 需过落域门禁 |
| 10 | 渲染放大（selector 拆分、倒计时下沉） | 客户端 | 长记录时 JS 线程 | 低优先级 |

## 7. 本轮落地的代码改动（未提交、未推送、未部署）

| 文件 | 改动 | 验证 |
|---|---|---|
| `mobile/src/core/voice/vad.ts` | ORT session `intraOpNumThreads: 1, interOpNumThreads: 1` | 待换包复测 §2.2 协议 |
| `mobile/src/core/voice/audioCtx.ts`、`playbackFacts.ts`、`speech.ts`、`cueTone.ts` | 空闲 15s 挂起共享输出上下文；取用即 resume；到点仍有声音就再等；`openSession` 提前取上下文 | 新增 `mobile/test/audioIdle.test.ts` 三条（到点挂起 / 有声不挂 / 取用取消）；既有 speechChain / speechStopPlayback / timelineWiring 的 audioCtx mock 补三个空实现 |
| `mobile/src/core/api/liveness.ts` | 探活超时 4s → 8s（导出常量） | liveness 用例注入阈值，不受影响 |
| `orchestrator/cloud/context.py` | `assemble` 四个子项 `asyncio.gather` 并发；降级语义不变 | `orchestrator/cloud/tests` 全目录 |

本地门禁结果回填在 §7.1。

### 7.1 验证记录

| 面 | 结果 |
|---|---|
| mobile | `tsc --noEmit` 0 错；`eslint . --max-warnings 0` 0/0；`jest` 95 套件 **1004 passed**（改前基线 1001 + `audioIdle.test.ts` 3 条） |
| orchestrator/cloud | `pytest -n 8 orchestrator/cloud/tests` **1312 passed / 1 skipped**（含 `test_context*.py` 66 条） |
| 四道门禁 + 端侧 smoke | `smoke_edge` 13 passed；skills ✅；exemplars ✅（hit 64 / miss 3，域错配 1.8%）；L0 strict 2/2（units 25/25，cases 139）；capability integrity ✅ |
| 真机复测 | 见 §7.2（换包 = `-dirty` 候选包，只用于 A/B，不冒充 release） |

### 7.2 换包复测

候选包：`xiaozhou-companion-prod-release-482bf4d6b-dirty-20260912-1952.apk`（APK SHA-256 `b5fec3c2…fa35`，201MB，`-dirty` = 本页 §7 的 10 个改动文件，构建 12m40s / `-CompileJobs 3`），OPPO `lastUpdateTime=2026-09-12 19:52:53`，签名同模板 `5e8f1606…`。同一装置、同一协议（`top -b -n 8 -d 5` + `top -H` + `gfxinfo reset`）：

| 场景 | 改前（`e38cd75c8` 包） | 改后（候选包） | 差 |
|---|---|---|---|
| 免唤醒 ARMED 空闲 | **219–239%**（avg 219）：三条 ORT 线程各 23–25% + kws-decode 7% + JS 5–7% | **158–168%**（avg 162）：`mqt_v_js` 6.8–12.2%、`kws-decode` 6.8–10%、AudioRecord 1.6–3.4%，**三条 25% 的线程消失** | **−57 个点**；剩下的 55%（RenderThread）+ 2×20%（hwuiTask）+ 31%（主线程）全是光球 |
| ARMED 帧 / 内存 | 1922 帧/60s、jank 0.26%、PSS 331MB | 2124 帧/60s、jank 0%、PSS 332MB | 不变 |

空闲挂起（§2.3）在同一晚**没有拿到真机证据**，三次尝试都卡在手机隧道：① 文字轮（speakPolicy=always）18s 内没有音频到达，`AudioTrack` 线程自始至终没出现，`media.audio_flinger` 报 `0 active`；② ③ 改走本地提示音（免唤醒 ARMED 下轻点光球 ⇒ 唤醒两音，不依赖网络），但两次点击时 App 都处在断连的 `muted` 态（`/presence-trail` 仍每 ~40s 一轮断连/重连），唤醒音只在 ARMED→LISTENING 转移上响，没有触发。结论：**§2.3 只有单测证据（`audioIdle.test.ts` 三条），挂起后的下一轮首音与提示音要在隧道稳定时补一次同协议复测**；候选包复测结束后已把常驻包换回 `e38cd75c8`（`install -r`，配置保留），免唤醒 / 播报三档都已回读为改动前的值。

## 9. 第二轮（2026-09-13）：用户裁决与落地

用户裁决：§6 第 1 项取「空闲 N 秒静置」（视觉一帧不改），第 2–5 项按本页推荐推进。本节记每一项的最终处置；
代码仍**未 commit / push / deploy**，云端两项要过发布流程。

### 9.1 光球空闲静置（§6-1）

- 判据：`core/presence/orbPolicy.ts::orbStill` —— `idleStill ∧ primary ∈ {idle, armed}`；听 / 想 / 说 / 等确认 / 看一眼永远不静置，`muted` 本来就静止。`orbTempo` / `composerOrbAnimated` 读它；欢迎态大球与桌面姿态舞台球改读 `orbTempo`（此前只看 reduce-motion）。
- 表：`core/presence/orbIdle.ts::IdleClock`，`ORB_IDLE_STILL_MS = 30s`。`AssistantProvider` 喂事件：根容器 `onTouchStart`（触摸旁听，不抢手势）、键盘、光球主态变化、换页、回到前台；静置中的任何一次 touch **同步**恢复。
- 单测 `orbIdle.test.ts` 7 条（到点翻 still / touch 立即恢复并重起表 / 只保留最后一只表 / dispose / 只静置 idle·armed / 缺省不变 / 行车档下静置 > ×0.5）。真机复测见 §9.6。

### 9.2 规划强制重试（§6-3）：离线 A/B 说它不是空转，**不关**

用 collector 里最近 50 个会话（98 轮）做了离线 A/B：每一轮 `salvage_wire_accepted` 的第 1 次调用（模型以正文 JSON 送回的计划）与第 2 次（工具通道）逐槽比对（`scratchpad/salvage_ab_offline.py`，只读）：

| 结果 | 轮数 | 含义 |
|---|---|---|
| 两次计划逐槽相同 | 5 | 纯代价 |
| 第 2 次给出**空计划**，最终仍用第 1 次的（`toolcall_salvage_kept`） | 3 | 纯代价 |
| 第 2 次只是等价改写（`today`→`今天`、加 `date: 今天`、加 `route_pref`） | 3 | 基本无益 |
| 第 2 次补上了**要紧的槽**（搜索 `question`、赛事 `date`） | 3 | 有益 |

14 轮里 8 轮纯代价、3 轮有益；第 2 次调用 p50 2.4s / p95 3.1s / 合计 32.8s。manifest 的 slots 只是名字列表（`slots: [query, limit]`），没有「必填」声明，所以「只在必填槽缺失时才重试」这条更好的规则**表达不出来**（加一份必填声明要过 SDK loader → Registry round-trip → Step 装配整条链，是另一件事）。结论：**不改策略**——本项目的规则是门禁先 A/B 证伪（`retry_policy.py` 头注：这条有 +34.2pp 的已知读数）。可用的 A/B 开关早已接线：`PLANNER_TOOLCALL_SALVAGE_RETRY=off`（`deploy/docker-compose.yaml:350`，cloud-planner 环境）+ `test/eval_intent_adversarial.py --suite gate --layer all --live` 两臂对比，改云端 `.env` 与重建 cloud-planner 都要单独授权。

**09-13 用户授权后的处置：没有跑。** 复核 `test/eval_intent_adversarial.py --live` 的接线后发现这条 A/B 的前提写错了：门禁轨 L1/L2 是**本进程内**装配 `PlanBuilder`（`test/eval_live.py::make_builder`，静态 manifests + 本机 `LLM_GATEWAY_ADDR` gRPC），`PLANNER_TOOLCALL_SALVAGE_RETRY` 由跑批进程自己的环境变量决定，云端 `.env` 与 cloud-planner 容器根本不在这条路上（只有 L3 那 1 条 journey 走真栈）。要跑就得 `target=local` 起本地栈（cloud 档禁止），或者往生产 docker 网络里打一条 gRPC 隧道，再对生产 LLM 网关打 ~1,400 次规划（两臂 × 117 条 × 3 次重复 × 2 个独立进程），且这条开关在门禁轨上的读数本来就是已知的（`retry_policy.py` 头注 +34.2pp）。结论不变：保留重试；「只在必填槽缺失时才重试」要先给 manifest 加必填声明。云端 `.env` 一个字没动。

### 9.3 深调研合成流式化（§6-5）

- `agents/_sdk/grounding.py`：prompt 抽成唯一声明源 `synthesis_messages()`；新增 `AnswerFieldStreamer`（从逐片到达的合成 JSON 里增量抽出 `answer` 字段，转义按 JSON 解码，裸英文引号与 `extract_json_str_field` 同判据：只有「引号 + 下一个已知字段 / 收尾括号」才是边界）与 `grounded_synthesis_stream()`（("delta", str)… + ("result", dict|None)，内容风控拒收在首 token 前 → 收窄 top-2 重试；中途出错 → 按已到文本收口）。为什么可以流：弃权是 prompt 内的约束，`confidence` 只影响 follow_up，不存在事后丢弃答案的闸。
- `agents/info/src/handlers/search.py`：`_search` 拆成 `_search_prepare`（检索 / 提前收尾）+ `_search_finish`（结论 + 证据卡）；`_search_stream` 与 `_search` 共用这两段。`agents/info/src/agent.py::handle_stream` 只对 `info.search` 走流式，其余意图 SDK 缺省。
- `orchestrator/cloud/engine.py`：D0 流式直通不再排除**单步重任务**（此前 `not complex_task` 把 `heavy` 单步挡在 executor 路径外）；过程区的 `execute running → done` 在这条路上同样发。多步 / adaptive / require_confirm 仍走 executor 与 T2。
- 单测：`agents/_sdk/tests/test_grounding_stream.py` 13 条（任意片长与 `parse_synth` 逐字一致、`\uXXXX` 跨片、裸引号、截断只出前缀不漏 JSON 外壳、拒收重试、中途失败收口、全败为 None、流式与一次性版 prompt 同源）；`agents/info/tests/test_search_stream.py` 3 条；`orchestrator/cloud/tests/test_engine_stream.py` 新增 1 条（重任务单步：understand / plan → execute running → 增量 → execute done → final，`thinking=on` 仍随 meta）。
- 预期：首段有效文本从「等整段合成 2.7–6.1s」提前到合成首 token（~1s）；**真栈读数待部署后用同一探针复测**。

### 9.4 ASR / TTS 连接预热（§6-7）

- `mobile/src/core/voice/warmSocket.ts`：一 URL 一条、OPEN 才能取、还在握手的留在池里、超龄（4 分钟）不取、取走即接管；换服务器 / 拆装配 / 退后台清池。URL 规则抽到 `audioUrls.ts`（asr.ts / tts.ts 再导出）。
- 接线：`AsrSession.openSocket` / `TtsSession.start` 先取预热连接，取到就直接发 `start`；`AssistantProvider` 前台即各预热一条；PTT 每轮结束、免唤醒 ARMED 与每次 `closeAsr`（classic 挡位）再补 ASR；每轮播报收尾再补 TTS。S2S 挡位不建 ASR 流，不预热。
- 单测 `warmSocket.test.ts` 4 条；既有 ASR / TTS / 免唤醒 / PTT 套件不变（池空时行为逐字同今天）。
- 预期：中继路径上语音轮省 1–2 个握手（1.4–3s）；网关代价 = 每客户端两条等 `start` 的空闲连接。

### 9.5 云主机直连路径（§6-8）：诊断结论与要你做的一步

云主机侧（SSH 只读）：广州腾讯云（公网 / 内网地址见 gitignore 的 `dev-stack.local`，仓库公开，不写这里），`tailscaled` 监听 UDP 41641，主机防火墙 `ts-input` 已放行 41641，`ufw` 关闭，`YJ-FIREWALL-INPUT` 只封几个扫描源；`tailscale netcheck`：UDP 可用、MappingVariesByDestIP=false，但对外映射端口是 **33039 而不是 41641**，最近 DERP 仍是 San Francisco（160ms）。PC 侧 `MappingVariesByDestIP: true`（对称 NAT）。⇒ 两端都打不出直连，卡在**腾讯云安全组没放行入站 UDP 41641**（主机内什么都不缺）。

要做的一步（云控制台，本页不代做）：给该实例的安全组加一条入站规则 `UDP 41641，源 0.0.0.0/0`（WireGuard 加密，放开无风险）。验收：PC / 手机上 `tailscale ping car-agent-dev` 从 `via DERP(lax)` 变成 `direct`。做完后本页 §3–§4 的所有端到端读数都要重取。补充：Tailscale 没有大陆 DERP，若还想给打不出直连的客户端兜底，可在这台广州机上自建 `derper` 并写进 tailnet 的 `derpMap`——另立项，不在本轮。

### 9.6 验证记录（第二轮）

本地（工作树含第一、二轮全部改动，未 commit）：

| 套件 | 结果 |
|---|---|
| mobile `npx jest` | 97 suites / **1015 passed**（第一轮 1004 + `orbIdle` 7 + `warmSocket` 4） |
| mobile `npm run typecheck` / `npm run lint --max-warnings 0` | 0 / 0（asr.ts / tts.ts 的 `import/first` 已归位） |
| `agents/info/tests` + `agents/_sdk/tests` | **331 passed**（345s；含 `test_grounding_stream` 13 + `test_search_stream` 3） |
| `orchestrator/cloud/tests` | **1313 passed / 1 skipped**（61s；第一轮 1312 + 重任务单步流式 1） |
| `test/smoke_edge.py` | 13 passed / 0 failed |
| 四道门禁 | `eval_skills` PASS；`eval_exemplars` hit 64 / miss 3 / silent 100（域错配率 1.8%，上限 20%）；`check_intent_gate` 2/2 strict；`eval_capability_integrity` PASS |

顺手修的两处装置问题：① `IdleClock` 与 `scheduleAudioIdle` 的真定时器在 node 下 `unref()`（RN 里 id 是数字，可选链跳过）；② 全量 jest 曾报「worker 未能正常退出」，`--detectOpenHandles` 钉到一只 `TCPWRAP`：三个 speech 套件（`speechChain` / `speechStopPlayback` / `timelineWiring`）在 `finishTurn` 收尾时经 `warmSocket` 真开了一条到假 audioUrl 的 WebSocket——单测不该碰网络，三个套件补 `@/core/voice/warmSocket` mock，警告消失（1015 passed，无 open handle）。

真机「空闲静置」复测（OPPO PEUM00，候选包 `482bf4d6b-dirty` 09-13 08:16，APK SHA-256 `72c85aaa…6c0d78`，同协议 `top -b -d 5` / `gfxinfo reset`，`/presence-trail` 回读整段处于 `idle · composer` + transport 已连接）：

| 段 | 进程 CPU（5s 样本，去首样本均值） | 帧（gfxinfo） | 线程 |
|---|---|---|---|
| A：启动后 t+8–23s，光球在动 | 151 / 151 / 152 / 147 → **150%** | 933 帧 / 15s（≈62fps），jank 0% | RenderThread 62% + hwuiTask×2 各 27.5% + 主线程 34% |
| B：t+45–70s，30s 无人理 ⇒ 静置 | 13.7 / 10 / 8.8 / 9 / 10.2 / 9.4 → **9.5%** | **0 帧 / 25s** | 只剩主线程 10% |
| C：轻点空白处后 15s | 144 / 144 / 149 → **146.5%** | 631 帧 / 10s，jank 0.16% | 与 A 同形 |

⇒ 手机放在支架上停在对话页时，30s 后从 1.5 核降到 0.1 核（**−140 个点**），一次触摸即恢复；恢复后的形态与改前逐字相同（这就是「视觉一帧不改」的含义）。第一趟同协议全 0 帧被判无效——PC 休眠后重新枚举 USB，系统「USB 用于」弹窗盖住了 App（`mCurrentFocus=null` 在这台机上不足以判别，截图才看得出）；按 BACK 收掉后重跑得到上表。复测结束常驻包已换回 `e38cd75c8`（配置保留）。

### 9.7 发布与部署后复测（2026-09-13）

- 提交：`43436398`（两轮全部改动，main）。deploy dry-run `blocking_changes: []` → apply 08:48–08:50 `submitted` → 独立 `status`：5/5 endpoint healthy、零 warning、`release_sha` = `running_release_sha` = `43436398` → verify `verified`（artifact `20260913T005437Z-4343639.json`）。
- 直连路径：用户加了安全组入站 UDP 41641 后，PC `tailscale ping car-agent-dev` 从 `via DERP(lax)` 160ms 变成 **direct 33ms**；手机到云主机 ICMP RTT 从 1.6–3.0s 变成 **32–59ms（avg 44ms）**，4/4 无丢包。
- PC 探针（同 §1/§4 协议，`link_probe.py`，直连后、新 release 上，`memory_enabled=false`）：

| 读数 | 改前（09-12，DERP 中继 + `e38cd75`） | 改后（09-13，直连 + `43436398`） |
|---|---|---|
| 每条新 WSS 握手（8443 / 8444） | 1.4–3.0s | **0.59–0.78s** |
| MiniMax TTS：文本送完 → 首片 PCM | 0.75–1.56s | **0.39–0.48s**；6.9s 音频 1.4–1.7s 到齐（此前 6.5s 音频要 15–34s，欠速） |
| fun-asr `stop` → `final` | 0.67–1.81s | **0.14–0.17s**；首个 partial 0.42–0.55s（此前 1.0–1.6s） |
| cloud 路径单轮总时长 | p50 4.3–4.5s（app 轮，memory on） | 探针 10 轮 p50 3.56s（规划 LLM p50 2.29s 未变；13 次规划里 3 轮仍是两次调用） |
| 深调研「帮我介绍一下深圳湾公园」 | 首段有效文本 = 终态，一次到齐 10–13s | **首段文本 5.9s**（规划 3.4s + Exa 1.6s + 合成首 token 0.9s），28 个增量片，终态 13.4s；过程区 `execute running` 3.6s 先到 |

规划本身没变快（prompt 仍 10.4k token、`cache_hit` 仍 0），少下来的全是路径与握手；同题第二遍被规划器路由到 navigation（POI 查询），不可比。
- 手机：清洁包 `434363986`（`xiaozhou-companion-prod-release-434363986-20260913-0909.apk`，APK SHA-256 `4541ad7b…1115c`，09:09 装为 OPPO 常驻包，`/turn-timeline` 底行回读 `prod · 434363986 · 2026-09-13 08:47`）。**手机侧文字轮的分段时延没取到**：adb 在这台 OPPO 上没有中文输入通道——英文句与拼音字母都被规划器判成「没有可执行的步骤」（受话判定把它们当噪声），把 IME 切到中文后是九宫格布局、`input text` 仍按原字母落字；只拿到错误话术那几轮的 `首片PCM→排定 1–2ms`（说明播放排定本身不慢）。要补这一格得靠真人说一轮或对话页加中文输入通道，评审 §4 的预热收益目前只有单测 + PC 侧握手读数。

## 8. 明确没做的事

- 没有改光球视觉、没有改规划重试策略、没有改 `.env` / 安全组 / Tailscale；
- 没有 commit、push、deploy、换常驻包；
- 没有取得真人语音的声学首音（AR08 E-04 照旧）；两次真机文字轮的 `/turn-timeline` 因隧道抖动没拿到读数；
- 探针语料只读，未发任何车控 / 商户 / 支付语料。
