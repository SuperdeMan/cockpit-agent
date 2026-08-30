# UX v2.1 · B1 落地评审（对照总方案）——评审入口与结果

> 状态：**已评审**（2026-08-30，评审者填 §3–§6）
> 评审对象：`mobile/` 在 `3cc6b74^..5839e62`（B1 四批，区间 49 个提交、其中**触及 `mobile/` 的 24 个**，2026-08-29 → 08-30）的落地
> 对照真相源：方案 [`docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md`](../design/2026-08-29-mobile-ux-v2-presence-redesign.md)（v2.1）；实施计划 [`docs/design/2026-08-29-mobile-ux-v2-b1-implementation-plan.md`](../design/2026-08-29-mobile-ux-v2-b1-implementation-plan.md)（§6.1–§6.4 四批记录）
> 纪律：**评审不修代码**（发现即记，修在 B2 或独立批）；读数只写自己跑出来的；§6 记录是被评对象的自述，**逐条复核不采信**

## 0. 评审要回答的唯一问题

**总方案里划给 B1 的东西，是否都落地了、落地得对不对；没落地的，是「属于 B2–B5」还是「B1 漏了」；方案有没有被实现推翻、需要回写。**

分类只许五种：✅ 落地且有证据 / ⚠ 部分或有缺陷 / ❌ 应在 B1 却没落 / ⏭ 不属于 B1（B2–B5，**不算缺口**）/ 🔁 方案假设被实现推翻，需回写方案。

## 1. 读法（省上下文）

1. 方案只读：§0（U1–U5 与 v2.1 变化）、§4（在场模型 + 状态矩阵 + 胶囊）、§5.1（Composer）、§5.3（Dock + 5.3.1 + 5.3.2）、§5.7–§5.11、§10（保留项 + 光球十条不变量）、§11.1–§11.6（批次 / 验收 / 判据 / 追溯矩阵）、§12.1–§12.2、§13（Q 表）。**不读 §2 对标**。
2. 计划只读：§0.1（分批与四批附加项）、§6.1–§6.4。**不读 Task 正文**——评的是代码不是计划。
3. 代码按 §2 的清单读，`git log --stat 3cc6b74^..5839e62 -- mobile/` 先看改了哪些文件。
4. 跑：`cd mobile && npm test && npm run typecheck`（记条数）；`git diff --stat 3cc6b74^..5839e62 -- hmi/ mobile/shared-allowlist.json`（应为空）。Maestro 与真机**可选**：没有设备就把 §6.4 的真机表标「未复核（无设备）」，不写成 ✅。

### 1.1 本轮评审者自己跑出来的读数（2026-08-30）

| 项 | 命令 | 读数 |
|---|---|---|
| jest | `cd mobile && npm test` | **29 suites / 315 tests 全通过**，exit 0，44.4s。与 §6.4 自述的收口读数逐字一致 |
| tsc | `cd mobile && npm run typecheck` | **0 error**，exit 0 |
| 回归（条数只增不减） | `git diff --stat 3cc6b74^..5839e62 -- mobile/test/` | 10 个文件 **+767 −0**，**零删除**；基线 235 → 315 |
| 红线 hmi | `git diff --stat … -- hmi/` | **空**（`hmi/src/` 亦空） |
| 红线 白名单 | `git diff --stat … -- mobile/shared-allowlist.json` | **空**（未新增共享模块） |
| 红线 编排核心 | `git diff --stat … -- orchestrator/` | **非空，但不是本线**：区间内 5 个提交（`343934b` `c3ec022` `61160a4` `7da0d1b` `e5e85b8`）全是 QA 线的 `fix(cloud)`；逐个核过它们的 `mobile/` 改动行数 = 0，反向核过 B1 那 24 个提交对 `orchestrator/ hmi/ shared-allowlist.json` 的改动行数 = 0 ⇒ **两条线零交叉，红线成立** |
| 取证截图 | `ls mobile/e2e/artifacts/` | 57 个文件（`b1-16-*` 18 个）在磁盘上、`git ls-files` **0 跟踪**（gitignore 生效）。本轮**打开看了三张**（见 §2.1 相应行） |

> ⚠ jest 尾部有 `A worker process has failed to exit gracefully` 告警（§6.1 遗留⑧记为 `handsFree.test.ts` 既有噪声，本轮复核仍在，非本批引入）。

---

## 2. 对照矩阵

### 2.1 升级点 → B1 承诺

| 方案条目 | B1 承诺（计划 §0.1） | 代码落点（应有） | 测试（应有） | 分类 | 证据 / 缺陷 |
|---|---|---|---|---|---|
| U1 在场模型：六轴 `PresenceSnapshot` + 唯一 `primary`（§4.1） | 全部 | `core/presence/presence.ts` | `presence.test.ts` | **✅** | 六轴齐（`presence.ts:69-87`）；`primary` 顺序 `presence.ts:163-171` **与 §4.1 逐字同序**（offline>attention>looking>listening/recognizing>speaking>thinking/processing>followup>armed>idle）。零 RN import、node 可测。`presence.test.ts:40-56` 参数化 11 态 + `:131` 评审案例 + `:145` 逐轴断言 |
| U1 状态矩阵 13 态（§4.2）逐态：光球态 / 环 / 胶囊文案 / Dock / 声音触感 / TalkBack | 光球+胶囊+Dock 全部；**声音触感属 B3/B4 ⏭**；TalkBack label 部分 | `AuroraOrb.tsx` `PresenceCapsule.tsx` `FocusDock.tsx` `usePresence.ts` | `presence.test.ts` 胶囊文案表 | **⚠** | 13 态逐格核过，**10 态全对**（idle / armed / listening / recognizing / thinking / processing / speaking / attention / looking / reconnecting=idle+dim / offline=muted）。三格不对：① **`armed` 胶囊「3s 后隐藏」没做**——探针实测 10 分钟后仍是「说「小舟小舟」」（D2）；② **`error` 胶囊在免唤醒开着时永远出不来**——`presence.ts:187` 的 armed 分支排在 `:188` 的 errorLive 之前，探针实测返「说「小舟小舟」」而不是「出错了」（D3）；③ **`followup` 的「环按剩余时间递减」未做**（`AuroraOrb.tsx` 无倒计时环，followup 只复用 listening 的静态 0.4α 环）。TalkBack：`ORB_A11Y` 八态齐（`AuroraOrb.tsx:27-36`）✅。`processing` 胶囊是「{label}…」不是「第 N 步 · 标签」（协议无步序，微偏差） |
| U1 三新光球态只加环与节律，十条不变量不破（§4.2 末段、§10.1） | 全部 | `AuroraOrb.tsx` diff | 无 jest（真机截图） | **✅** | 逐条核 `git diff … AuroraOrb.tsx`：①正圆（新增全是 `borderRadius:9999` 的环，无形变）②七层未动 ③四色/高光核未动 ④极光未动 ⑤`Ripple` 青色未动、新环是琥珀/白/灰**不是波纹** ⑥`muted` 停旋转是 §4.2 明写 ⑦五态语义未动 ⑧⑨未动 ⑩`animated=false` / `dim` 仍生效。环参数**与 §4.2 逐值对得上**：listening 0.4α / armed 0.18α（`AuroraOrb.tsx:264`）、attention 琥珀 0.35α、`Shutter` 白环 300ms 一次。两处微偏差不破不变量：`looking` **未做「体缩 0.96」**（只做白环，见 🔁-3）、attention 呼吸落到 idle 的 4s 而非 §4.2 的 3s（源码有注释） |
| U1 状态胶囊一次一条、3s 延迟、降级不进胶囊（§4.3） | 全部 | `PresenceCapsule.tsx` `presence.ts` | `presence.test.ts` reconnecting 3s | **⚠** | 一次一条 ✅（`presence.ts:174-188` 单条 if/else 链）；`reconnecting` 3s 延迟 ✅（`presence.ts:90,100`，`presence.test.ts:151`）；降级不进胶囊 ✅（`FocusDock.tsx:34` 过滤，`transport_unknown` 走气泡灰字）；`live` 青点 6dp ✅（`PresenceCapsule.tsx:47`）。缺陷同上行 D2/D3（那两条的裁决点就在这条 if/else 链里）。**「点按胶囊=打开语音层」B1 刻意不接** ✅ 按附加项⑤照办（`ChatScreen.tsx` 不传 `onPress`；组件仍是 `Pressable` 但 `role=text`——B2 接线时按遗留⑧再判一次） |
| U1 顶栏连接 pill 降级为健康点（§4.3 末条） | 全部 | `ChatScreen.tsx` | — | **✅** | `ChatScreen.tsx:305-307` `healthColor`：**在线=`p.fg3` 纯灰、无 glow**，只在 reconnecting/offline 变琥珀/红；v1 pill 完整保留在 `!v2` 分支（`:502-524`）。实拍复核 `b1-16-04-expired.png` 顶栏确为灰点 |
| U3 Focus Dock 四项 `confirm/slot/task/queue`（§5.3） | `confirm/task/queue` 有产出方；`slot` **只类型与样本**（协议无 `missing_slots`，Q19） | `FocusDock.tsx` `commitment.ts` | `commitment.test.ts` `presence.test.ts` | **⚠** | 产出方到位：confirm（`presence.ts:128-144`，含 `subkind=location`）/ task（`:145`，>8s）/ queue（`:148`）；`slot` 判 **⏭** 成立——`derivePresence` 无产出路径，`presenceFixtures.test.ts:37-39` 写明「刻意不进守卫」的理由。**但 confirm 的「动作摘要」取错了源**：`usePresence.ts:97` 取的是带该 `operationId` 的**助手气泡原话**，而端侧车控确认的话术是硬编码通用句（`orchestrator/edge/edge_call.py:272`「这项操作可能影响车辆安全，请确认是否继续。」）⇒ **Dock 从来没说过它在确认什么**。实拍复核 `b1-16-03-dock-t0.png`（100% 字号）：标题是「这项操作可能影响车辆…」不是「打开后备箱」。这是 D1，也是 🔁-4 |
| U3 Dock 不轮播、钉最高风险最早到期、稳定排序（§5.3） | 全部 | `commitment.ts::pinCommitment/sortCommitments` | `commitment.test.ts` | **✅** | `commitment.ts:30-49`：高风险确认 0 > 低风险确认 1 > slot 2 > task 3 > queue 4，同档按 `dueAt`，末位 `a.i-b.i` 保稳定；`pinCommitment` 只返回第 1 项 + `others` 计数（`:52-56`），组件侧「另有 N 个待处理 ›」（`FocusDock.tsx:153-157`），**无任何轮播代码**。`commitment.test.ts:30/40/44/56` 四条守着 |
| U3 倒计时只读共享 TTL、到期精确调度、到期留痕（§5.3） | 全部 | `store.ts::syncPruneTimer/noteExpired` `FocusDock.tsx` | `sessionStore.test.ts` B1-4 | **✅** | 进度条与文案都读 `PENDING_TTL_MS`（`FocusDock.tsx:7,107,113`），**无字面量**；`store.ts:597-616` 改成 `min(nextExpiry-now, 30s)` 的递归 `setTimeout`；`noteExpired`（`:619-628`）追加「⏱ 「…」的确认已过期，需要的话再说一次」= Q8 文案。`sessionStore.test.ts:456/475/487/508` 四条 + `:529/545/560` 三条看门狗。**实拍复核 `b1-16-04-expired.png`：Dock 消失 + 记录里确有那一行 ⇒ P5 判据成立**（但那一行的摘要同样是通用句，见 D1）。`PENDING_TTL_MS=300_000` 核过（`hmi/src/pendingOps.mjs:21`），与真机 4:37→4:27 的读数自洽 |
| U3 Dock 材质 G0 实色（§5.3、§5.11） | 全部 | `FocusDock.tsx` | — | **✅** | `FocusDock.tsx:36` `solid = p.dark ? '#0A0E1A' : '#FFFFFF'`——**不带 alpha 的实色**，确认卡 / 降级行 / 隐私栏（`PrivacyRail.tsx:55` 同款）三处共用 |
| §5.3.1 ConfirmPolicy 只投影 VAL：UI 不据车速自定规则；VAL 拒绝 → `safety_blocked` | 第一阶段：**不据 driving 做限制** ✅ 应落；`safety_blocked` 无结构化信号 ⏭（Q16） | `FocusDock.tsx` `usePresence.ts` | — | **✅** | `grep -rniE "speed\|km/?h\|车速" mobile/src` 只命中 `VehiclePanel.tsx:16,31` 两条**车况面板的中文标签**（`speed:'车速'` / `fan_speed:'风量'`），**UI 里零车速阈值、零 driving 门禁**。`snapshot.driving` 有字段但无消费方（`FocusDock` / `PresenceCapsule` 都不读它）。`safety_blocked` 无产出方 ⏭ 成立并被守卫钉住（`presenceFixtures.test.ts:56-66` 从 `usePresence` 源码盘点产出方，样本标 `producible:false`） |
| §5.3.2 执行回执 | **B2** ⏭ | — | — | **⏭** | 全仓无回执组件；不算缺口 |
| §5.7 离线：`muted` + 胶囊 + `queue` 项；「发送状态未知」灰字 | 全部 | `store.ts::setStatus/uncertainIds` `MessageBubble.tsx` | `sessionStore.test.ts` | **✅** | `store.ts:129-151`：`open→closed` 写 `uncertainIds=[...inFlight]`、`→open` 清 `queued`；`send()` 返 false 累加 `queued`（`:265-266`）；`MessageBubble.tsx:147-151` 灰字「发送状态未知（网络刚断过；连上后若无回音请再说一次）」。**附加项①（离线暂停看门狗）是 B1 新增、方案 §5.7 没写 ⇒ 🔁-2 回写**（不是缺陷：它是让 §5.7「连上自动补发」成真的前提，`store.ts:525-556` + `sessionStore.test.ts:529/545/560` 三条假时钟用例 + 变异验证两条各红各的一半） |
| §5.8 Onboarding 上品牌 + 权限用途文案 | 全部 | `app/onboarding.tsx` | — | **✅** | `AuroraBackground` + `SafeAreaView` + `AuroraOrb(88)` + `Glass(RADIUS['2xl'])`（`onboarding.tsx:148-153,167,247,265`）；权限用途文案在 `:263-278`（麦克风 / 定位各一句，「要权限之前」）。**逻辑函数零改动**——diff 里唯一的逻辑侧改动是 `import React`，`onTest/onSave/derived` 一行未动 ✅ |
| §5.9 token 层（新组件必用，旧组件不扫荡） | 骨架 + 新组件使用 | `ui/tokens.ts` | `tokens.test.ts` | **✅** | `tokens.ts` 的 SPACE/RADIUS/TYPE/MOTION/TARGET/GLASS **与 §5.9/§5.11 逐值对得上**（TYPE 多一个 `micro:11`）；`scale(size,kind,pref)` 大字档 文字×1.15 / 目标×1.1 / 行高×1.15。新组件真用了：`FocusDock` / `PresenceCapsule` / `PrivacyRail` / `Composer` / `onboarding` / `state-gallery` 六处 import `RADIUS/TARGET/TYPE/scale`；旧组件未扫荡 ✅。`tokens.test.ts:7/11/14/20/28/32/37` |
| §5.10 隐私栏（麦/摄像头/最近一次/当前用户/一键关闭/差异说明）+ 顶栏采集点 | 全部 | `PrivacyRail.tsx` `activityLog.ts` `ChatScreen.tsx` | `activityLog.test.ts` | **⚠** | 六行齐（`PrivacyRail.tsx:97-113`）+ 三个一键关按钮（`:115-132`）+ 采集点三态（`ChatScreen.tsx:313-321`，**不采集就不渲染**）。第 3 批坑②的修法**只修了一半**：`micText`（`PrivacyRail.tsx:21-26`）已把「端侧待机」与「上传服务端 ASR」分开 ✅，**但 `ChatScreen.tsx:317` 的 `captureDot.label` 还是「麦克风在本机处理」，且它被 `:470` 当成 `accessibilityLabel` 播给读屏**——PTT 录音时 `privacy.mic='edge'`（探针实测），那句话是假的（D4）。另有 tone 判据缺陷：`PrivacyRail.tsx:97` 的 `mic==='cloudAudio' \|\| capture!=='armed'` 在**默认空闲态**（免唤醒关、mic='off'、capture='off'，探针实测）就为真 ⇒ 文字「关」被涂成琥珀（D5，§6.4 出账②只写了它存在，没写它命中的是默认态）。判据层 `privacy.mic` 三档并两件事：B1 不动 ✅ 按附加项④照办 |
| §5.10「开录即告知」+ 首次显式同意（同 §5.2.2） | **B2** ⏭（语音层） | — | — | **⏭** | **裁断：设置页那颗一次性同意弹窗可以留给 B2，但要写进 B2 的必做项（§6 建议 3）。** 理由：CLAUDE.md 的红线三条件今天**已经满足**——默认 `classic`（`settings/store.ts:87`）、须在设置里显式选、差异文案在设置页（`SettingsScreen.tsx:396,401`）与隐私栏（`PrivacyRail.tsx:110-113`）两处都在 ⇒ 缺的「首次显式同意」是方案 v2.1 在红线**之上**加的一道硬化，不是红线缺口，不必抢进 B1；但它零依赖语音层、B1 已经在改设置页，B2 不做就没有别的批会做 |
| §5.11 材质三档 token；G2 只给光球/把手/选中 chip | token ✅ 应落；G2 反应式 B2 ⏭ | `ui/tokens.ts::GLASS` | `tokens.test.ts` | **✅** | `tokens.ts:28-35` 三档数值与 §5.11 逐字相同；G0 已在 Dock / 隐私栏落地，G1 = 既有 `Glass`，G2 只登记不用（源码注释写明「B3 spike 前不真的用」）✅ |
| §7.6 键盘避让：先读数再修 + Maestro 08 | 全部 | `ChatScreen.tsx` `onboarding.tsx` `debug.tsx` `e2e/08` | Maestro 08 | **✅** | 三处 `behavior` 一致改成 `"padding"`（`ChatScreen.tsx:442` / `onboarding.tsx` / `debug.tsx:97`），**未动 `app.config.ts` 的 `softwareKeyboardLayoutMode`**（B1 零原生变更，符合 §7.6 步骤 1）。读数在 §6.3 e)：两处都遮 ⇒ 选 A 修法；Maestro 08 退出码 0、反向验证（改回 `undefined`）当场红。**本轮无设备，Maestro 未复跑——标「未复核（无设备）」，只核了代码侧** |
| §8.1 无障碍：200% 重排 / 减少透明度 / partial 节流 / 轻点切换 | 200% **验收项**；其余 B4 ⏭ | — | — | **❌** | **200% 这条是 B1 的验收项且实测未达成**（§11.2 B1 明写「系统字号 200% 下 Dock / 语音层 / 胶囊不裁字」）。**评审者亲自打开 `b1-16-09-font200-dock.png` 复核：Dock 标题被压成「这..」，右侧「危险动作 · 需二次确认」占满**——承诺卡在最大字号下**完全不说明自己要确认什么**。裁字的直接成因是 `FocusDock.tsx:94-99`（标题 `numberOfLines={1}` + `flex:1`，右侧固定标签既无 `numberOfLines` 也无 `flexShrink`），**但根因是 D1**：即使给足宽度它也只会说「这项操作可能影响车辆…」。胶囊在同一张图里未裁字 ✅。「减少透明度 / partial 节流 / 轻点切换」B4 ⏭；reduce-motion 全仓零实现（`grep` 空），是 B1 之前就没有、非本批引入 |
| §11.5 开关 `uxV2Presence/uxV2Dock`，v1 代码保留 | 全部 | `settings/store.ts` `ChatScreen.tsx` `Composer.tsx` `MessageBubble.tsx` | `settingsMeta.test.ts` | **✅** | 两开关缺省 true（`settings/store.ts:88-89`）；**两个都有真消费方且各管各的轴**：`v2` 关 ⇒ 四条 v1 窄条（免唤醒条 `ChatScreen.tsx:359`、`hf.error/notice` `:388`、PTT 提示行 `:394`、`linkWarn` `:530`）+ v1 pill + `legacyOrb` 全部回来；`dock` 关 ⇒ `inlineConfirm=true`，气泡内确认按钮回来（`MessageBubble.tsx:176`）。`settingsMeta.test.ts:72/77/82` 三条，其中「三个键**不上行**」那条是好判据。真机两向各截图见 §6.4 第 11 条（本轮无设备，未复跑） |
| §11.5 埋点：20 条在场轨迹环形日志 + 调试屏页 | 计划把它写成 activityLog——**与方案「PresenceSnapshot 变化轨迹」不是一回事** | `activityLog.ts` | | **🔁** | 复核成立，且比记录写的更彻底：`activityLog` 记的是**采集激活**（mic/camera，`ChatScreen.tsx:209/262/266` 三处写入），是 §5.10「最近一次激活」那一行的数据源，**不是** `PresenceSnapshot` 变化轨迹；`grep -rniE "在场轨迹\|presenceTrace\|轨迹" mobile/src` **全空**，调试屏无该页；`activityLog.list()` **零消费方**（只用了 `lastOf`）。⇒ 🔁-1：方案 §11.5 要回写；**而且它今天无人认领**（§11.1 的 B2 范围里也没有它） |
| §11.6 追溯矩阵 P1–P14 | 逐行核「代码落点/测试/指标/回滚开关」是否成立 | 见方案 §11.6 | | 见 §2.1a | 11 行：✅7 / ⚠1 / ⏭3 |
| §12.1 七种降级：产出方 / 停留 / 面 / 出口 | `permission_denied / service_degraded / audio_echo_degraded / transport_unknown` 应有产出方；`recoverable_error` 胶囊 4s；`safety_blocked / fatal` 无产出方 ⏭ | `usePresence.ts` `FocusDock.tsx::DegradationRow` | `presenceFixtures.test.ts`（含产出方守卫） | **⚠** | 四种产出方齐（`usePresence.ts:68-71`）✅；面与出口对（`FocusDock.tsx:34` 过滤两种、`:185-190` permission→「去系统设置」、echo→「重新开启插话」）✅；`safety_blocked/fatal` ⏭ 且被守卫钉住 ✅。三处偏差：① **`recoverable_error` 不走 degradation 轴**，走的是 `lastError` 独立通路（`presence.ts:161,188`）——行为在、建模与 §12.1 不一致，且**被 armed 遮蔽**（D3）；② **`permission_denied` 只有 mic**，§12.1 写的是麦 / 摄像头 / 定位三种；③ 它的「停留」实为**到用户下次按下光球为止**（`usePtt.ts:71-74` 每次 `pressDown` 清 `errorKind`），不是 §12.1 写的「持久，直到授予」。**「重新开启插话」= 关再开免唤醒（权宜）在源码里如实标注**（`ChatScreen.tsx:252-256`）✅，且机制真的通——`useHandsFree.ts` 的 `[wantOn]` effect cleanup 会清 `bargeInDisabled`（`:163`），关再开确实重建收音窗 |
| §12.2 威胁模型（B1 相关行）：唤醒误采回收、S2S 告知、摄像头不出预览、承诺面 G0 + 比例 + 倒计时 | 除 S2S 告知（B2）外应落 | 各处 | | **✅** | 唤醒误采：隐私栏实时指示 + 三个一键关 + `audio_echo_degraded` 可见 ✅；摄像头：`looking` 快门环（`AuroraOrb.tsx::Shutter`）+ 激活日志记原因 + **不出预览**（`core/vision/frame.ts` 的 diff 只加了 `subscribeVisionCapturing`，抓帧语义与 `VisionCapture.tsx` 一字未动）✅；承诺面：G0 ✅ + 取消 `flex:1` / 确认 `flex:2`（`FocusDock.tsx:122,130`）✅ + 倒计时 ✅ + **UI 不扩大 `allowedChannels`**（全仓无该概念，就是今天的 touch+voice）✅。S2S 首次同意 ⏭ B2 |
| §11.4「不负优化」八条判据 | | | | 见 §2.1b | 8 行：✅3 / ⚠2 / ⏭3 |
| §11.2 B1 验收清单（含四批附加项） | 与 §6.4 十三条表逐条对照 | | | **⚠** | §6.4 自评 ✅8 / ⚠3 / ⬜2。**抽三条复核证据链**：**第 3 条**（倒计时 4:37→4:27）——打开 `b1-16-03-dock-t0.png`，确有 `4:37 后过期` + 进度条；`PENDING_TTL_MS=300_000` ⇒ 4:37 = 已过 23s，10s 后 4:27 **算术自洽** ✅；**第 4 条**（300s 到期留痕）——打开 `b1-16-04-expired.png`，Dock 已消失、记录里确有「⏱ …的确认已过期，需要的话再说一次」✅；**第 9 条**（200%）——打开 `b1-16-09-font200-dock.png`，「这..」属实，且「按钮 144px=48.0dp」自洽（`minHeight:48dp` × density 480 = 144px；Android 系统字号不缩 dp，所以按钮没变而文字翻倍——这正是标题被挤掉的机制）✅。**三条证据链全部成立，但第 9 条的定性只对一半**（见 D1）。第 7 条（VAL 拒绝）⬜ 是预期内 ⏭；第 6/12/13 条的 ⚠/⬜ 复核后维持 |

#### 2.1a §11.6 追溯矩阵逐行

| 行 | 分类 | 证据 |
|---|---|---|
| P1 光球不承载状态 | **✅** | `presence.ts` + `AuroraOrb.tsx` 三新态 + `Composer.tsx` 读 `orbState`；`presence.test.ts` + `/state-gallery`（24 样本）+ Maestro 09；回滚 `uxV2Presence` ✅。指标「首反馈时延 / 可读性」未取数（⏭ B2） |
| P2 状态散在四条 | **✅** | 四条窄条**逐条**收进 `!v2` 分支（行号见 §11.5 那行） |
| P3 S2S 无记录 / P4 转写两处 | **⏭** | B2（U2） |
| P5 确认随列表滚走 | **✅** | `FocusDock.tsx` + `store.ts::syncPruneTimer/noteExpired` + Maestro 06 + 真机第 4 条实拍；回滚 `uxV2Dock` ✅ |
| P6 键盘 | **✅** | `ChatScreen.tsx` KAV + Maestro 08；矩阵列的 `app.config.ts` **刻意未改**（B1 零原生），符合 §7.6 步骤 1 |
| P7 形态 / P8 行车 | **⏭** | B4 |
| P9 卡无优先级 | **⏭** | B2 |
| P10 视觉零反馈 | **✅** | B1 那一半到位：`subscribeVisionCapturing` → `looking` 态 + 快门环 + 隐私栏激活日志。「用户气泡**先于**相机」是 §11.2 B2 的验收项 ⏭（今天 `ChatScreen.tsx:209-212` 仍是先抓帧后 `core.send`） |
| P12 Onboarding | **✅** | 见 §5.8 那行 |
| P13 token | **✅** | 见 §5.9 那行 |
| P14 a11y | **⚠** | 200% ❌（见 §8.1 行）；Accessibility Scanner 未跑（设备没装，装 APK 超授权范围）⇒ 无基线 |

#### 2.1b §11.4「不负优化」八条

| 判据 | 分类 | 读数 / 理由 |
|---|---|---|
| 首反馈时延 ≤100ms | **⏭** | 未取数；方案自己把打点挂在 Reanimated 回调上，B2 取数 |
| 状态可读性 5/6 | **⏭** | 5 人外部小样本是 §11.1 的 B2→B3 闸 |
| 记录完整性 100% | **⏭** | S2S 沉淀在 B2 |
| **承诺不丢 = 0 次无解释消失** | **✅** | 到期有痕（`noteExpired` + 实拍）、服务端关有痕（`sessionStore.test.ts:475` 断言 closed **不**留「过期」痕）、离线时 Dock 仍钉（`presence.test.ts:131`）⇒ 判据满足。**注**：承诺不丢 ≠ 承诺说得清（D1） |
| **键盘遮挡** | **✅** | Maestro 08 退出码 0 + 反向验证红（§6.3 e）；本轮无设备未复跑 |
| 性能 | **⚠** | 只有 GPU 侧 dev build 读数（50th 11ms / 90th 14ms / 95th 15ms / 99th 18ms）；CPU 侧仪器自相矛盾已被 §6.4 自己否掉 ✅ 处置正确；「常态 1 个循环动画实例」**未量**——B1 里对话屏只有 Composer 一颗球在转（列表头像 `animated=false`），但没有读数 |
| 无障碍 Scanner 严重项 0 | **⚠** | 未跑、无基线（见 P14） |
| **回归：条数只增不减** | **✅** | 235 → **315**，`test/` 净 +767 −0 行、零文件删除（评审者自跑） |

### 2.2 红线与保留项（一条都不许 ⚠）

| 项 | 核法 | 结果 |
|---|---|---|
| S2S 默认 `classic`、只能设置里显式选 | `settings/store.ts` 默认值；B1 没加任何「一键切挡位」 | **✅** `settings/store.ts:87 voicePipeline:'classic'`；隐私栏三个按钮是 `handsFree=false` / `visionEnabled=false` / 停本轮麦，**没有一个动 `voicePipeline`** |
| 视觉默认关、命中才挂相机、**不出预览** | `VisionCapture.tsx` 未改语义；`looking` 态无预览 | **✅** `visionEnabled:false`（`:86`）；`core/vision/frame.ts` diff 只加订阅、`captureVisionFrame` 的抓帧-卸载语义一字未动；`VisionCapture.tsx` 零改动；`looking` 只出一圈白环 |
| 声纹不进 App | `grep -rn "occupant\|voiceprint" mobile/src` 只应有常量 `primary` | **✅** 命中 5 处：`gateway.ts:51-52` 的常量 `occupant_id:'primary'` / `occupant_name:''`（B1 前既有）+ 三处注释。零新增 |
| `hmi/` 零改动、共享判据零改动、白名单未扩 | `git diff --stat 3cc6b74^..5839e62 -- hmi/ mobile/shared-allowlist.json` 为空 | **✅** 两者皆空；`hmi/src/` 亦空（B1 只**读** `@shared/pendingOps.mjs` 的 `PENDING_TTL_MS`，这正是「只读共享 TTL」要的方向） |
| 编排核心零改动 | 同上加 `orchestrator/` | **✅** 见 §1.1：区间内的 `orchestrator/` 改动全部来自 QA 线 5 个提交，与 B1 的 24 个提交零交叉（双向核过） |
| 光球十条不变量 | 2.1 那一行 | **✅** 逐条核过，见 §2.1 第 3 行 |
| 对话记录未被替代；`SessionCore/requestRouting/pendingOps` 语义不改 | `store.ts` diff：只加不改归属规则 | **✅（附注）** 记录仍是沉淀层（Dock 是加在 Composer 之上的一块，不替代列表）；`requestRouting.mjs` / `pendingOps.mjs` **零改动**；**归属规则**（`registry.open/dropBubble`）一行未动。⚠ 附注：`SessionCore` 本身**有语义增量**——看门狗暂停/恢复（`linkDown` / `pausedWatchdogs`）、三个新 state 字段、到期时**主动追加一条 assistant 消息**。都是 B1 有意为之、有测试、且修的是真 bug（否则用户永远拿不到答案），但它超出「只加不改」的字面，B2 接手时要知道 |
| 前台交互档承诺不变 | 无前台服务 / 通知代码 | **✅** `grep -rniE "foregroundservice\|expo-notifications\|Notifications\."` 全空 |

---

## 3. 结论先行

- **一句话结论**：B1 把「在场」这件事真正做成了一份纯函数 + 一个消费面矩阵，方案划给它的东西**基本**都落了地，工程纪律（多轴不压枚举、时钟只有一个、判据不抄第二份、产出方守卫、回滚路径有真消费方）是这一批最值钱的产物；**唯一的实质缺口是承诺卡说不清它在确认什么**——摘要取的是后端的通用句而不是动作，200% 字号只是把这件事从「读着别扭」放大成「完全读不出来」。

- **分类计数**（三张表合计 **43 行**）
  - §2.1 主矩阵（24 行）：**✅14 / ⚠7 / ❌1 / ⏭2 / 🔁1**
  - §2.1a 追溯矩阵（11 行）：**✅7 / ⚠1 / ⏭3**
  - §2.2 红线（8 行）：**✅8**（0 ⚠，红线成立）
  - **合计（43 行）：✅29 / ⚠8 / ❌1 / ⏭5 / 🔁1**
  - §2.1b 八条判据（8 行，不并入上面的合计）：✅3 / ⚠2 / ⏭3
  - **🔁 另有 4 条不落在某一行上**（是横跨若干行的方案假设被推翻），逐条见 §5 ⇒ **🔁 实际共 5 条**

- **❌ 与 🔁 逐条（这两类是评审的全部价值）**

  **❌ = 1 条**
  - **❌-1｜§8.1 / §11.2「系统字号 200% 下不裁字」是 B1 验收项，实测未达成。** 承诺卡标题被压成「这..」（评审者亲自看图复核 `b1-16-09-font200-dock.png`）。§6.4 出账①把它定性成「右侧固定文案挤掉标题」——**只对了一半**，根因见 D1。

  **🔁 = 5 条**（全文见 §5）
  - 🔁-1 §11.5 埋点：落的是采集激活日志，不是 `PresenceSnapshot` 变化轨迹；在场轨迹页**今天无人认领**。
  - 🔁-2 §5.7 离线：方案没写「离线期间必须暂停看门狗」，而不写这条时「连上自动补发」是一句做不到的承诺。
  - 🔁-3 §4.2 `looking`：方案写「体缩 0.96 一次快门」，实现只做白环、**刻意不碰球体**（不变量①更安全）。
  - 🔁-4 §5.3 `confirm` 的「动作摘要」：方案假设客户端拿得到动作名，**今天的下行帧里没有这个东西**（`edge_call.py:272` 恒为通用句）。这是 D1 的方案侧成因。
  - 🔁-5 §11.3 `06-confirm-dock-expire`：方案要求流里等到期，实现把「到期留痕」判据切给假时钟单测（共享 TTL 300s 不许为验收缩短）。

---

## 4. 缺陷与建议（按严重度排序）

### D1｜承诺卡从来没说过它在确认什么（高）

- **现象**：Focus Dock 的确认卡标题恒为「这项操作可能影响车辆…」，不是「打开后备箱」。100% 字号下是「读着像系统在复述自己」，200% 下被挤成「这..」＝**完全读不出来**。到期留痕那一行同样是这句通用句。
- **证据**：`mobile/src/features/chat/usePresence.ts:97` 摘要取 `messages.find(m => m.operationId === op.id)?.text`；该气泡的文案在端侧是硬编码通用句 `orchestrator/edge/edge_call.py:272`。实拍 `mobile/e2e/artifacts/b1-16-03-dock-t0.png`（100%）、`b1-16-09-font200-dock.png`（200%）、`b1-16-04-expired.png`（留痕行）。方案 §5.3 要的是「⚠ 图标 + **动作摘要**（「打开后备箱」）」；§5.3.2 已经点名了正确的源——「用户原话 / 意图中文名（`commands.yaml` display_name）」。
- **影响**：① 承诺面的**全部价值**是「系统欠你什么」，欠什么说不出来等于只剩一个按钮；② 多条待确认时（`attention-two` 样本形态）两张卡文案**逐字相同**，「另有 1 个待处理 ›」点开也分不出谁是谁；③ 200% 下退化成 ❌-1；④ 到期留痕那行读起来像系统在自言自语。
- **建议落点**：**B2**（零后端依赖的那一半先做）。客户端可得的正确源是**紧邻的上一条 user 气泡原话**（实拍里就是「打开后备箱」）；结构化的 `action/target/impact` 随 Q16 的 `confirm_policy` 一起挂账后端。⚠ 别把 `numberOfLines` / `flexShrink` 当成修法——那只修 ❌-1 的表象，不修 D1。

### D2｜`armed` 胶囊永不隐藏（中）

- **现象**：§4.2 明写「说「小舟小舟」」**3s 后隐藏**；实现里只要 `capture==='armed'` 就一直挂着。评审者探针实测：`now+10min` 仍返 `{text:'说「小舟小舟」',tone:'neutral'}`。实拍 `b1-16-04-expired.png` 里那颗胶囊就是这么停着的。
- **证据**：`mobile/src/core/presence/presence.ts:187`（无时间条件）；`mobile/test/presence.test.ts:201` 的胶囊表只断言文案、**没有任何隐藏断言**。§6.4 验收表第 1 条量的是「≤3.0s **出现**」——方向和规格相反，所以它绿着。
- **影响**：Composer 上方常驻一条不变的提示，正是 §4.3 要消灭的「四条窄条」的心智；且直接引出 D3。
- **建议落点**：**B2**（`presence.ts` 一行：`capture==='armed' && now - armedSince < 3000`，`armedSince` 由收集器登记，同 `SeenRegistry` 形态）。补一条「3s 后无胶囊」的断言。

### D3｜免唤醒开着时，error 胶囊永远出不来（中）

- **现象**：§4.2 的 `error` 态与 §12.1 的 `recoverable_error`（4s 胶囊）在免唤醒开启下不可达。探针实测：同一条 error，免唤醒关 → `{text:'出错了',tone:'red'}`；免唤醒开 → `{text:'说「小舟小舟」',tone:'neutral'}`。
- **证据**：`mobile/src/core/presence/presence.ts:187-188`——`armed` 分支排在 `errorLive` 之前。并且 v2 下 v1 那条错误横幅已被 `ChatScreen.tsx:388` 的 `!v2` 关掉 ⇒ 这一路的错误提示只剩气泡本身。
- **影响**：`handsFree` 默认 false（`settings/store.ts:84`），所以不是所有用户都撞；但「语音优先随身助手」的主用法就是开着免唤醒，四批验收截图里它一直是开的。
- **建议落点**：**B2**，与 D2 一并修。D2 修好后 armed 胶囊 3s 就让位、这条大概率一起消失，**但仍应把 `errorLive` 显式提到 `armed` 之前**——别指望另一条修法顺手带走它。

### D4｜采集点的读屏播报里留着已经被修掉的那句假话（中）

- **现象**：第 3 批坑②把「本机处理」这个错误说法从隐私栏文案里修掉了，**但它在采集点的 `accessibilityLabel` 里原样活着**。PTT 录音时 `privacy.mic='edge'`（探针实测）⇒ 读屏播「麦克风在本机处理」，而那一刻音频正在上传给服务端 ASR。
- **证据**：`mobile/src/features/chat/ChatScreen.tsx:317`（label 定义）→ `:470`（拼进 `health-dot` 的 `accessibilityLabel`）；对照已修好的 `PrivacyRail.tsx:21-26`。
- **影响**：同一个判据两个出口，只堵了看得见的那个；对读屏用户这块屏说的仍是假话——而「说的是真的」是这块屏存在的全部理由。**「同一个值有几个出口，就在它的入口处判一次」的又一例。**
- **建议落点**：**B2**，与 §6.3 遗留②（`privacy.mic` 要不要加第四档 `cloudAsr`）**一起裁**——判据层加档能一次堵掉所有出口，文案层逐处改会再漏下一个。

### D5｜隐私栏「麦克风：关」被涂成琥珀，而且命中的是默认态（中低）

- **现象**：`mic='off'`（文字「关」）且 `capture!=='armed'` 时行色为琥珀。探针实测**默认空闲态**（免唤醒关、什么都没发生）就满足：`mic='off'`、`capture='off'` ⇒ 琥珀。
- **证据**：`mobile/src/features/chat/PrivacyRail.tsx:97` 的 `snapshot.privacy.mic === 'cloudAudio' || snapshot.capture !== 'armed'`。§6.4 出账②记了这条，但把它写成一种组合，没写它**就是默认态**。
- **影响**：颜色和文字说两件事，而且是在最常见的一屏上；隐私面板的警示色一直亮着 ⇒ 警示色贬值。
- **建议落点**：**B2**，与 D4 同一处裁决。

### D6｜`permission_denied` 只有麦克风一路，且「持久」实为「到下次按下为止」（低）

- **证据**：`mobile/src/features/chat/usePresence.ts:68` 只从 `ptt.errorKind==='permission'` 产出，摄像头 / 定位无产出方（§12.1 写的是三种）；`mobile/src/features/chat/usePtt.ts:71-74` 每次 `pressDown` 清 `errorKind` ⇒ 用户一按光球，Dock 里那条「去系统设置」就消失，与 §12.1 的「持久，直到授予」不同。
- **建议落点**：**B4**（无障碍 / 权限批）或随 B2 顺手；先把语义差异写进 §12.1 的表，别让下一个人按表读代码。

### D7｜`reenableBargeIn` 被「关闭本轮麦克风」复用，中间有 50ms 设置为假的窗口（低）

- **证据**：`mobile/src/features/chat/ChatScreen.tsx:252-256`（`handsFree=false` → `setTimeout 50ms` → `true`）被 `:580-583` 的 `onStopMic` 复用。机制本身是对的（cleanup 清 `bargeInDisabled`、重建收音窗，`useHandsFree.ts:163`），语义也说得通（「本轮」结束、免唤醒有自己的按钮）；但：① 一个名为「重新开启插话」的 helper 被用来实现「关闭本轮麦克风」，读代码要绕一圈；② 这 50ms 里持久化设置是 `false`，App 被杀就**静默关掉了免唤醒**。
- **建议落点**：**B2**，语音层会给更准的「结束本轮收音」实现（源码注释已经这么写了）。

### D8｜`activityLog.list()` 与 `ActivitySource='location'` 零消费方（低）

- **证据**：`grep -rn "activityLog"` 只用到 `push` / `lastOf` / `subscribe`；`location` 无产出方。
- **建议落点**：**B2** 落「在场轨迹页」（🔁-1）时 `list()` 自然有消费方；届时若仍没有，按第 1 批遗留④的判据删掉。

### D9｜`queued` 只增不减，取消一轮不减计数（低）

- **证据**：`mobile/src/core/session/store.ts:266` 只加，归零只发生在 `setStatus('open')`（`:145`）。断线期间用户取消 / 删除了那一轮，「N 条消息排队中」仍按旧数报。
- **建议落点**：**B2**（低优先；离线期间取消是罕见路径，但 Dock 的文案是承诺型文案，报错数字比不报更糟）。

---

## 5. 方案需要回写的条目（🔁）

> 由方案作者回写 v2.2；评审不直接改方案。

**🔁-1｜§11.5 埋点**
- 原句：「本端 20 条环形日志记录 `PresenceSnapshot` 变化（时间戳、变化的轴、触发输入），调试屏「主链帧」旁加「在场轨迹」页」
- 应改成：「**B1 落的是采集激活日志**（`core/presence/activityLog.ts`，20 条环形、mic/camera、隐私栏「最近一次激活」的数据源，不上传）。**`PresenceSnapshot` 变化轨迹与调试屏「在场轨迹」页留 B2**，与语音层一并落——B1 的两条日志需求（§5.10 的激活日志 / §11.5 的在场轨迹）是两件事，不要再合成一条。」
- 理由：`grep` 全空、无调试页、`activityLog.list()` 零消费方。**并且它今天不在任何批次的范围里，回写时要顺手给它一个批。**

**🔁-2｜§5.7 离线与弱网**
- 原句：「恢复→补达时 Dock 逐条消失。」
- 应补一条：「**离线期间必须暂停请求看门狗**：飞行模式下 RN 的 `onclose` 不来、靠 HTTP 探活判死、退避重连约 2 分钟，而 95s 看门狗会在断网期间跑完并把该轮从 `registry` 注销 ⇒ 重连后补发的 `final` 带着已注销的 `request_id`、按「对不上=丢帧」被丢，**用户永远拿不到答案**。表跟着**链路**走（`setStatus` 非 open 即摘、open 即各自重起整 95s；断开期间发出的轮**不起表**），不跟着 `connStatus` 的值变走。」
- 理由：B1 附加项①实现了它（`store.ts:525-556` + 3 条假时钟用例 + 两条各红一半的变异验证），而方案 §5.7 一个字没写——不回写，下一个人重构 `SessionCore` 时会把它当成多余的复杂度删掉。

**🔁-3｜§4.2 `looking` 光球态**
- 原句：「`looking` = **新 `looking`**：体缩 0.96 一次「快门」」
- 应改成：「`looking` = 一次性白环 0.9→1.35 扩散淡出（300ms），**不缩球体**——不变量①（正圆不变形）与⑧（它同时是麦克风 / 头像 / 品牌标）下，动球体本身的收益不抵风险。」
- 理由：`AuroraOrb.tsx::Shutter` 只做环；这是实现对方案的一次**有理由的收窄**，回写后它才是决定而不是遗漏。

**🔁-4｜§5.3 `confirm` 的「动作摘要」（最重要的一条）**
- 原句：「`confirm` | ⚠ 图标 + **动作摘要**（「打开后备箱」）+ 取消 flex1 / 确认 flex2 + 剩余时间环」
- 应改成：「动作摘要**今天协议里不存在**——端侧车控确认的 `speech` 是硬编码通用句（`orchestrator/edge/edge_call.py:272`「这项操作可能影响车辆安全，请确认是否继续。」），`final` 里没有任何动作名字段。⇒ **摘要取「紧邻的上一条用户原话」**（客户端可得，就是「打开后备箱」）；结构化的 `action / target / impact` 随 §13 Q16 的 `confirm_policy` 一起挂账后端。**不许取带 `operation_id` 的那条助手气泡的正文**——它对每个危险动作都一样。」
- 理由：这是 §5.3 与 §5.3.2 之间的一处**内部不一致**——§5.3.2 已经写明了正确的源（「用户原话 / `commands.yaml` display_name」），§5.3 却假设摘要唾手可得。B1 照 §5.3 实现，于是承诺卡说不出话（D1、❌-1）。

**🔁-5｜§11.3 Maestro 扩流**
- 原句：「`06-confirm-dock-expire`（危险动作 → `dock-confirm` 可见 → 等到期 → 断言「确认已过期」文本）」
- 应改成：「`06-confirm-dock`（危险动作 → `dock-confirm` + `dock-countdown` + `presence-capsule` 可见 → 取消 → Dock 消失）。**「到期留痕」不在 e2e 里等**——共享 TTL `pendingOps.mjs::PENDING_TTL_MS=300_000` 是 hmi 也在读的判据、**不许为缩短验收去改**，那条由 `mobile/test/sessionStore.test.ts` 用假时钟守，真机各批各跑一次。」
- 理由：实现已按这个判据落（流的头注释写得比方案清楚），方案不回写就会有人来「修好」这条流。

---

## 6. 给 B2 的入口建议

> B2 = 语音层（方案 §11.1）。以下 5 条是 B1 遗留里 B2 **必须**先处理的；其余留在计划 §6.4 出账表。

1. **先修 D1（承诺卡摘要取错源），再动语音层。** 它是 ❌-1 的根因，且改的是 `usePresence.ts` 一处取值（换成紧邻的上一条 user 气泡原话），零后端依赖、零语音层依赖。**修完要同时验三处**：Dock 标题、`noteExpired` 的留痕行、`attention-two` 那种两条并存的形态（今天两张卡逐字相同）。修完再顺手给 `FocusDock.tsx:97-99` 的右侧固定标签加让位规则，❌-1 才算真的关掉。
2. **把 D2/D3/D4/D5 作为一组「胶囊与隐私栏的判据修正」一次做完，别拆散。** D2（armed 胶囊 3s 隐藏）与 D3（error 被 armed 遮蔽）在同一条 if/else 链上；D4（读屏里的假话）与 D5（「关」被涂琥珀）在同一个 `privacy.mic` 判据上——**在判据层加第四档 `cloudAsr` 能一次堵掉所有出口**（会动 `PresenceSnapshot` 类型、画廊样本与覆盖度守卫，正因如此 B1 刻意不动，现在该动了）。逐处改文案会再漏下一个出口，D4 就是这么活下来的。
3. **S2S 首次显式同意弹窗放进 B2 的设置页那一半。** 「开录即告知」确实要语音层，但一次性同意零依赖语音层；红线三条件今天已满足（默认 classic + 显式选 + 两处差异文案），所以它不是红线缺口，**但 B2 不做就没有别的批会做**。
4. **给「在场轨迹」找一个批（🔁-1）。** B1 落的是采集激活日志，方案要的那份 `PresenceSnapshot` 变化轨迹 + 调试屏页**今天不在任何批次的范围里**。B2 是最合适的落点（语音层会大量制造在场变化，正是要看轨迹的时候）；不做就在方案里把它删掉或明确挂 B4，别让它悬着。
5. **B1 交给 B2 的两条「别踩」**：① `PresenceCapsule` 是 26dp + `accessibilityRole="text"`，B2 若接 `onPress`（§4.3 的「点按胶囊 = 打开语音层」），**必须同时改 role 与热区**，否则一次违反读屏与 Material 触控两条；② `SessionCore` 在 B1 长了看门狗暂停 / 恢复语义（`linkDown` / `pausedWatchdogs`），它修的是「重连后用户永远拿不到答案」，**重构时别当成多余的复杂度删掉**——判据在 `sessionStore.test.ts:529/545/560` 三条，其中第三条（断开期间发出的轮不起表）是「超出计划字面」的那一半，专门有变异验证钉着。

> **本轮未复核（无设备）**：§6.4 的 13 条真机验收表、Maestro 06/08/09 三条流的实跑、第 12 条性能读数。评审者只做了两件事——打开磁盘上的取证截图逐张看（第 3 / 4 / 9 条，三条证据链全部成立）、核读数的算术自洽性（`PENDING_TTL_MS=300_000` 与 4:37→4:27；`48dp × density480 = 144px`）。**未跑的一律没写成 ✅。**
