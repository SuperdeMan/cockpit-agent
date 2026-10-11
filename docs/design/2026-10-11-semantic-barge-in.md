# 语义打断：播报中有人说话先压低、判定后再决定停不停

- 状态：云端与 HMI 已部署 `eb098edd`（2026-10-11，用户：「可以，开始做语义打断」；提交、推送、部署与真栈验证沿用上一项的授权）；Android 常驻包 `eb098eddc` 已装 OPPO（test），Xiaomi 仍是上一包 `b79daa46b`；真车声学由用户验；两处遗留缺陷（网关温度 0 被当成没传、Android 深链在根导航器就绪前送达即闪退）已修，发布 `65ddeb85`、Android `1778e22eb` 装 OPPO（§8）
- 交付对象：共享语音状态机（`hmi/src/voiceLoop.mjs`）、HMI、Android、llm-gateway、云端编排
- 前置：[唤醒后连续对话的拒识](2026-10-10-handsfree-followup-rejection.md) §4.4「语义打断」；R4.2 流式 TTS + barge-in

## 1. 问题

免唤醒开着时，助手播报中任何人连续出声 300 ms（且不像回声）就立刻停播（`voiceLoop._bargeInFire` → `onStopTts`），
随后把这句按 `voice_bargein` 上云。乘客聊天时：

1. 乘客一句话就把用户要听的回答掐断；
2. 这句上云后被续问窗判据判「不是对我说的」⇒ 按 2026-10-10 的规则结束这次连续对话。回答没了，还得重新喊唤醒词。

上一项修完之后，多人车厢里这是最显眼的缺口。

## 2. 主流做法

车机和手机助手的「全双工打断」普遍是：播报中检测到人声先压低（ducking）、继续识别，确认是对助手说的（或是「停」这类明确指令）才停，
否则恢复音量接着播。Alexa / Google 的设备端打断以唤醒词为主；带语义判断的打断都是「先压低、后决定」。

## 3. 约束（代码事实）

- 网关每个 WebSocket 连接同一时刻只有一个在飞请求，新请求进来会取消旧的（`gateway/edge/main.go` 抢占并回 `cancelled`）。
- HMI 每发一轮都先 `startTTSReply()` ⇒ `stopTTS()`；Android `requestSent()` ⇒ `speech.begin()` ⇒ 先停上一轮。
- 所以「先把插话那句当正式一轮发出去、云端判完再决定」做不到：发出去的那一刻旧回答就没了（旧回答若还在流式出字，连文字也被取消）。

## 4. 方案

插话那句**先单独问一次「是不是对我说的」**，这个问题不走对话通道；旧回答压低音量继续播，答案回来再决定。

```
SPEAKING ─有人出声 ≥300ms（不像回声）→ 压低旧回答（不停）+ 开 ASR（LISTENING，source=bargein）
  定稿：
    空 / 回声 / 语气词 / 短噪声 ─→ 恢复音量，回 SPEAKING（旧回答若已播完 → 续问窗）
    「停 / 别说了 / 够了…」整句 ─→ 本地立刻停，回 ARMED（不上云、不回应答）
    退出词（再见 / 退下…）     ─→ 停旧回答 + 退场应答，回 ARMED（同既有）
    确认条可见时               ─→ 停旧回答，照旧直接上云（D5-2 红线：确认条可见时一切定稿上主链，不判）
    其余                       ─→ 判定（THINKING，旧回答仍压低在播）
      判受话      → 停旧回答，按正式一轮发出（meta.voice_admitted=1，云端不再判第二遍）
      判非受话    → 恢复音量，回 SPEAKING，对话不结束（旧回答已播完 → 同续问窗拒识：回 ARMED）
      判不出/超时 → 停旧回答，按今天的路径发出（云端照旧并行判定）——失灵不拦人
```

### 4.1 判定放在哪里

- **llm-gateway 新开 `POST /api/voice/admission`**。两端本来就直连它（HMI `:8444`、Android audioUrl）做 ASR / TTS / 声纹，
  走 Tailscale Serve 整口转发，不改基础设施；它不占对话通道，旧那一轮不受影响。
- **判据只有一份**：续问窗判据（系统提示、复核、解析、「记住…」恒受话）从 `orchestrator/cloud/admission.py` 挪到
  `runtime/continuation_admission.py`，云端编排与 llm-gateway 都从这里取。复核差一个结尾换行避开网关缓存的处理一并带过去。
- 模型调用走 llm-gateway 自己的 `Complete`（本机回环 gRPC）：缓存、档位（`@fast`）、降级、限流、`obs.llm` 与云端那一路逐字相同。
- 鉴权：Bearer token（同记忆 / 声纹接口）；没有身份 401。每一问上限沿用 `CONTINUATION_ADMISSION_TIMEOUT_S`（缺省 5 s）。
- 「助手上一句」由客户端给：就是正在播的那段文本（状态机手里的回声参照）。拿来判受话足够，它不授予任何权限。

### 4.2 已判过的那一轮

判受话后发出的正式一轮带 `meta.voice_admitted=1`（进 prefs 白名单）。云端见到「续问窗来源 + 已判过」就不再起续问窗判定，
并按「判定为受话」处理焦点省略那道复问；规划器自己的 `addressed` 照旧（它更宽松，几乎不会再拒）。
客户端谎报只等于跳过一道体验过滤，与 `ptt` 同级，不涉及权限、VAL 或确认。

### 4.3 状态机（共享，两端同一份）

- 新注入：`onJudgeBargeIn(text, previous, done)`、`onDuckTts(on)`、`holdBargeIn()`。不传 `onJudgeBargeIn` ⇒ 逐字是旧行为（测试与旧消费方不变）。
- 「压着旧回答」期间（LISTENING / THINKING）：`ttsEnd` 只记「旧回答播完了」、不迁移；`ttsStart` 不迁移。
  停旧回答一律**先停、后放手**：Android 停播会同步回调 `ttsEnd`，放手前到达的那一下归旧回答，不会把状态机推进续问窗。
- 唤醒词、停播键、系统抢占、拆机都会作废在等的判定；离开打断判定时音量一定恢复（`_gotoArmed` / `_gotoIdle` 兜底）。
- 判定等待上限 6 s（`bargeInJudgeMs`），到点按「判不出」处理。
- 本地停词（`bargeInStopWords`）：停 / 停下 / 停一下 / 别说了 / 别念了 / 闭嘴 / 安静 / 够了 / 好了 / 可以了 / 行了 / 知道了 等，整句匹配（同退出词的「占据整句 + 1 字宽容」）。「暂停」刻意不在表里：它也可能是「把音乐暂停」，交给判定 + 云端。
- 打断进来的这一句，语音时长从 VAD 判到开口那一刻算起（含 300 ms 确认窗）：此前从确认那一刻重算，「暂停」「换台」这类两字指令只剩确认之后那一截，会被短语音规则当噪声丢掉。同时把「没有语音段」的哨兵从 0 换成 null（假时钟与时间原点下 0 是合法时刻，旧写法靠真假判断会漏记时长）。
- S2S 挡位不压低（`holdBargeIn` 返回 false）：自答轮不经主链，保持今天的立即停播。

### 4.4 压低

两端各在共享 AudioContext 上挂一条「播报总线」GainNode，主链播报的播放器都接到它（共享 `PcmPlayer` 增加可选 `destination`，
Android `QueuePcmPlayer` 同），压低 / 恢复只调总线增益：压到 0.3（约 −10 dB），80 ms 斜坡防爆音。
HMI 批处理那条（`HTMLAudioElement`）改 `volume`。S2S 播放器与提示音不经总线。

### 4.5 提示

判非受话、接着播时给一条会自己消失的提示「刚才那句不像是对我说的，我接着说」（与续问窗拒识同一个通道）。
判受话不提示——旧回答停下、新回答开始就是反馈。
Android 拒识提示（续问窗拒识与插话判非受话两条）的显示时长由 2 s 改为 4 s（与 HMI 一致）：句子十几到二十几个字，2 s 读不完；取消 / 回声那两条短提示仍是 2 s。

### 4.6 两端的连带修正

- HMI 播报文本随流式增量喂给状态机（此前只在 final 给一次）：final 之前插话，判定拿到的「助手上一句」与回声参照都是上一轮的话。Android 早就是逐增量喂。
- HMI 停播按钮通知状态机（`stopSpeaking()`，与 Android 同语义：回 ARMED、不开续问窗）。此前只停声音，`stopTTS()` 又不回放 `onEnd`，状态机会停在一个没有声音的 SPEAKING 里；判定期间按停也靠这一条作废判定。

## 5. 刻意不做

- 判定期间用户继续说的话不并进这一句（THINKING 期不收音，同今天）。
- 不改云端续问窗判据本身（系统提示、复核规则）；插话与续问窗共用这份判据，2026-10-10 的离线 / 真栈读数仍然适用。
- llm-gateway `Complete` 把 `temperature=0` 当成未传（`request.temperature or 0.7`），所以云端判定实际跑在 0.7。
  影响所有请求 0 温度的调用方，单独立项，不在本项。**已修**（`e5d581f9`，§8.1）。

## 6. 验证

- 单测：状态机（压低 / 停词 / 判受话 / 判非受话 / 判不出 / 期间播完 / 唤醒词与停播键作废判定 / 确认条可见 / S2S 不压低 / 旧行为不变）；
  播放器总线与增益；llm-gateway 接口（鉴权、空句、判定映射、超时）；云端「已判过」不再判；两端控制器接线。
- 真栈：接口探针（续问窗语料的负例 / 正例，带「上一句」）；已判过的一轮走签名车道，trace 里没有续问窗判定、答复正常。
- 设备：重打 Android 包装机、启动与链路冒烟；真车声学由用户验。

## 7. 读数

### 7.1 本地（`eb098edd`）

- HMI node 443 / 443、typecheck、生产构建通过；Android jest 1509 / 1509（全量并发下 3 个界面套件 7 例超时，单独重跑 34 / 34 通过，
  与 2026-10-10 同一类满载抖动）、tsc、eslint 零告警；Python 全量 11591 passed / 37 skipped（开发工作树，`models/` 只链接了子目录），
  四道门禁与端侧 smoke 通过。
- 变异 11 条全部被抓：旧回答播完不再被吸收、先放手后停、确认条可见仍判、去掉本地停词、判非受话也停播、判受话不带已判过、
  拆机不恢复音量、打断计时不含确认窗、云端不认已判过、网关换档位、网关不要求身份。

### 7.2 真栈（`eb098edd`，2026-10-11）

- 发布：status release = running = `eb098eddc2b1bac00aa0ba152adfdc99013af75c`、ok、5/5 healthy、无告警、可用 51,193,044,992 字节；
  verify `verified`（`.artifacts/dev-stack-verifications/20261011T083355Z-eb098ed.json`，全局生效模型 `minimax:MiniMax-M3.1-Flash-Preview`，
  用户的测试设置；判定走 `@fast` = M3）；CI 8 / 8。云上 HMI 下发的前端包含 `/api/voice/admission` 与「我接着说」提示。
  部署用干净隔离克隆：共享仓库的本地 `main` 当时被另一个会话的工作树检出、停在旧提交，发布闸只认 `refs/heads/main` 可达。
- `scripts/probe_bargein_admission.py`（签名合成用户，只读）两轮：
  - 首跑（插话那句与「正在播的回答」随机配对）：48 次判定、HTTP 错误 0，负例 30 / 30 判非受话，正例 16 / 18——
    「要带伞吗」配的是「为您播放周杰伦的《晴天》」，两问都判否（放歌时问「要带伞吗」也可能是在问同车的人，判否说得通），
    另一例第一问超过 5 s 判不出（客户端按旧路径发出，不丢话）。判非受话中位 2.0 s / p90 2.4 s，判受话中位 1.0 s / p90 1.6 s。
  - 复跑（天气类追问配天气回答）：负例 30 / 30、正例 16 / 18，没判对的两例都是第一问超过 5 s 判不出，误拒 0。
    ⚠ 复跑紧接首跑，负例与非天气正例的消息与首跑逐字相同、命中网关 300 s 缓存（中位 3 ms），只有新配的 9 次是独立判定；
    探针两次运行要相隔 5 分钟以上。超时的那两对随后单独各问一次都在 0.8–1.0 s 判受话，是模型偶发慢，不是输入问题（96 次里 3 次）。
  - 签名车道：带 `voice_admitted=1` 的插话轮只有 exit=bargein_preadmitted、正常作答（3.1 / 4.4 s）；不带标记的负例照旧被续问窗判定拒掉；
    不带身份请求 401；零动作，前后 release 一致。证据 `.artifacts/semantic-bargein/probe-eb098edd-r3-run1-mixed-context.json`、
    `probe-eb098edd-r3-run2.json`。
- 设备：OPPO PEUM00（test，Android 14）装 `xiaozhou-companion-prod-release-eb098eddc-20261011-1643.apk`（SHA-256 `d58b74c9…4e7f`，
  ORT 1.30.0，签名同登记值），设备与本地 SHA-256 一致、非 DEBUGGABLE、设置页底部 `v0.1.0 · prod · eb098eddc · 2026-10-11 16:30`，
  包内含判定接口路径与新提示；上一包 `b13e13fd4`。
- 设备上另记一条**既有**缺陷（与本项无关）：App 被 monkey 启动时系统 USB 弹窗占着焦点、Activity 未进前台，12 s 后深链 `xiaozhou://settings`
  送达即崩（expo-router「Attempted to navigate before mounting the Root Layout component」，`+native-intent` 在根布局挂载前直接导航）；
  再次打开正常。导航代码本项未改，待用户决定是否修。**已修**（`1778e22e`，§8.2）：触发条件不是 USB 弹窗本身，而是 App 在锁屏 / 被挡着时拉起、窗口从未布局。

## 8. 两处遗留缺陷的修复（2026-10-11）

用户：「问题要修」（§5 记的网关温度、§7.2 记的 Android 深链闪退），提交、推送、部署与真栈验证沿用同一授权。
代码 `e5d581f9`（网关）、`1778e22e`（Android），合入当时的 main 后发布 `65ddeb85`。

### 8.1 网关把温度 0 当成没传（`e5d581f9`）

- 根因：`CompleteRequest.temperature` 是 proto3 普通 `float`，0 是缺省值、不上线路；网关 `request.temperature or 0.7`
  分不清「传了 0」与「没传」。所有请求 0 的调用方实际都跑在 0.7：唤醒轮与续问窗的受话判定（云端编排）、插话判定
  （llm-gateway 接口）、手册目录路由、提醒时间抽取、场景编译首轮、地标解析。离线评测直连厂商、温度 0，与线上口径不一致。
- 修：proto 改 `optional float temperature = 3`（同号同类型、线路兼容；从不设它的老调用方照旧拿缺省）；
  网关 `Complete` 与 `CompleteStream` 共用一个取值：传了就用（含 0），没传取 0.7。响应缓存键用取值之后的温度，0 与缺省不互相命中。
- 改之前先直连厂商核过：MiniMax-M3、MiniMax-M3.1-Flash-Preview 与备份档 DeepSeek（`deepseek-v4-flash`）温度 0 都正常返回（HTTP 200、业务码 0）。
- 本地：新增 3 条单测（非流式 / 流式 / 缓存分开）；Python 全量 11594 passed / 37 skipped（`1778e22e`）。

### 8.2 Android 深链在根导航器就绪前送达即闪退（`1778e22e`）

- 根因链：
  1. expo-router 在原生端不给 `SafeAreaProvider` 初始尺寸，子树要等窗口第一次报 insets 才渲染——在那之前根布局与 Stack 都不挂，根导航器不就绪；
  2. 锁屏或被系统弹窗挡着时拉起 App：Activity 被短暂 resume、JS 照跑，但窗口从未布局，根导航器一直不就绪；
  3. 这时送来的运行期深链（`initial: false`，singleTask 走 onNewIntent）进 `+native-intent`，`router.dismissTo / navigate`
     入 expo-router 的 routingQueue；它自己的 effect 排空队列时 `assertIsReady()` 抛「Attempted to navigate before mounting the
     Root Layout component」——抛在 expo-router 的 effect 里，release 包是致命异常，我们这边的 try/catch 包不住。
- 复现（OPPO，旧包 `eb098eddc`）：锁屏下 monkey 拉起、等 10 s（JS 已启动）、锁屏中送 `xiaozhou://settings` ⇒ **2 / 2 闪退**；
  解锁与深链同时到 0 / 2。冷启动途中第二次 `am start`（0.3–4 s 扫 12 档）0 / 12；拉起后立刻被设置页盖住时 JS 根本不启动，也不触发。
  §7.2 那一次是同一条路径：拉起时焦点在锁屏 / 通知栏窗口。
- 修：`core/nav/navReady` 只认导航容器自己的 `isReady()`（与那条断言同一判据），由根布局用官方 `useNavigationContainerRef` 登记，
  没登记一律当未就绪；`+native-intent` 就绪时行为逐字不变（当场按序两步、返回 null），没就绪就等（50 ms 轮询、最多 10 s）再发，
  等不到把规范化后的 URL 交回 router（它自己的 listener 带 try/catch，最坏是这一跳没生效，而不是闪退）。
- 本地：jest 7 条（就绪 / 根布局未登记 / 登记未就绪 / 超时交回 / 判据抛错 / 冷启动与外部 URL / 只撤自己的登记）；
  去掉就绪判断（即修前行为）4 / 7 变红；tsc、eslint 零告警。
- 设备：OPPO PEUM00（test）装 `xiaozhou-companion-prod-release-1778e22eb-20261011-1819.apk`（SHA-256 `4978ca0a…ae74c`，
  设备与本地一致、非 DEBUGGABLE）。同一复现路径 **3 / 3 不崩**：锁屏期间进程在、解锁后停在设置页（等到就绪才导航）；
  解锁同时送 1 / 1；运行中 voice / settings / vehicle 连送 9 次再逐个各送 1 次都落对页、同一进程、任务栈 1 个 Activity；冷启动深链落设置页。
  证据在本机 `%LOCALAPPDATA%/car-agent/artifacts/deeplink-ready-apk-20261011-180436/`（界面导出含天气卡街道地址，不入库）。
  Xiaomi（compare）仍是 `b79daa46b`。

### 8.3 真栈（`65ddeb85`，2026-10-11）

- 发布：干净隔离克隆 dry-run 无阻塞后 apply（共享仓库的本地 `main` 仍被另一会话检出在 `eb65859a`）；status release = running =
  `65ddeb85b2b3a9c68676ee8238ceee3e5c035aa2`、ok、5/5 healthy、零告警。切换后约 1 分钟的首次 verify 失败、工件未记原因
  （`20261011T103323Z-unknown.json`），单跑发布核对一步通过后整次重跑 `verified`（`.artifacts/dev-stack-verifications/20261011T103538Z-65ddeb8.json`，
  `e2e_remote_safe`，全局生效模型仍是用户的测试设置 `minimax:MiniMax-M3.1-Flash-Preview`，判定走 `@fast` = M3）；CI 8 / 8（run 38131941601）。
- 插话判定（`scripts/probe_bargein_admission.py` ×2，两次相隔 10 分钟以上、零缓存命中，96 次都是独立判定）：负例 **59 / 60** 判非受话，
  正例 **36 / 36** 判受话，判不出 **0**（0.7 时 96 次里 3 次第一问超 5 s）。漏的那 1 次是「你今天几点下班」：第一问判否、复核判受话。
  复核共翻转 3 次（2 次救回正例、1 次放过负例）——温度 0 下复核仍有作用。判非受话中位 2.0 s / p90 2.9 s，判受话中位 0.86 s / p90 2.6 s
  （0.7 首跑：2.0 s / 2.4 s，0.96 s / 2.1 s）。签名车道：带 `voice_admitted=1` 只有 `bargein_preadmitted`、正常作答，不带标记的负例被续问窗判定拒掉，
  不带身份 401，零动作。证据 `.artifacts/semantic-bargein/probe-65ddeb85-t0-run1.json`、`probe-65ddeb85-t0-run2.json`。
- 续问窗（`scripts/probe_followup_rejection.py` ×3）：48 轮、链路错误 0、续问窗判定 48 / 48、判不出 0、零动作；负例 **30 / 30** 拒、
  正例 **0 / 18** 误拒，PASS——与 0.7 下（`0c07bc33`，[续问窗拒识](2026-10-10-handsfree-followup-rejection.md) §6.3）同数。拒识轮判定 1.5–2.9 s、整轮 2.3–3.7 s 落地。
  证据 `.artifacts/followup-rejection/probe-65ddeb85-t0-r3.json`。
- 其他改成真 0 的调用方：核心旅程受影响的家族各 1 遍（规划钉 MiniMax-M3，同基线）。只读车道 71 条 70 过：F01 手册 29 / 30、F06 导航 27 / 27、
  F09 周边 6 / 6、F12 受话 8 / 8；唯一红 M09 第二轮「怎么调」被规划落到闲聊、没派手册（与目录路由无关；`5afd98fd` ×5 基线 2 / 5，已知方差）。
  F12 的 R03 / R05 / R06 在 `5afd98fd` ×5 基线是 0 / 5、1 / 5、0 / 5，这遍都过；中间隔着续问窗拒识（`0c07bc33`），单遍读数不归因于温度。
  合成写车道 F10 提醒 N01 / N02 2 / 2、收尾清理核对通过。证据错误、残留挂起、发布连续性错误都是 0。
  证据 `.artifacts/v2-runtime/temp0-65ddeb85-readonly-r1.json`、`temp0-65ddeb85-f10-r1.json`。
- 读法：温度 0 下判定接口不再超时判不出，负例多漏 1 / 60，在这个样本量上与 0.7 分不开；续问窗两档同数；其余调用方没有看到回退。
  按设计的 0 运行，不回退。
