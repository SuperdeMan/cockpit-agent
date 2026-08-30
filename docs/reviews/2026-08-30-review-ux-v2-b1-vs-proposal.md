# UX v2.1 · B1 落地评审（对照总方案）——评审入口与结果

> 状态：**待评审（新会话执行）**；评审者填写 §3–§6，写完即停
> 评审对象：`mobile/` 在 `3cc6b74^..5839e62`（B1 四批，49 个提交，2026-08-29 → 08-30）的落地
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

## 2. 对照矩阵（评审者逐行填「分类 + 证据」）

### 2.1 升级点 → B1 承诺

| 方案条目 | B1 承诺（计划 §0.1） | 代码落点（应有） | 测试（应有） | 分类 | 证据 / 缺陷 |
|---|---|---|---|---|---|
| U1 在场模型：六轴 `PresenceSnapshot` + 唯一 `primary`（§4.1） | 全部 | `core/presence/presence.ts` | `presence.test.ts`（优先级、轴独立、评审案例） | | |
| U1 状态矩阵 13 态（§4.2）逐态：光球态 / 环 / 胶囊文案 / Dock / 声音触感 / TalkBack | 光球+胶囊+Dock 全部；**声音触感属 B3/B4 ⏭**；TalkBack label 部分 | `AuroraOrb.tsx` `PresenceCapsule.tsx` `FocusDock.tsx` `usePresence.ts` | `presence.test.ts` 胶囊文案表 | | 逐态核：`idle/armed/listening/recognizing/thinking/processing/speaking/followup/attention/looking/reconnecting/offline/error` |
| U1 三新光球态只加环与节律，十条不变量不破（§4.2 末段、§10.1） | 全部 | `AuroraOrb.tsx` diff | 无 jest（真机截图） | | 逐条核十条：正圆 / 七层顺序 / 四色 / 极光只在 AI 时刻 / 波纹青 / 动效签名 / 五态含义 / 落位 / 最前层 / 降级脚本化 |
| U1 状态胶囊一次一条、3s 延迟、降级不进胶囊（§4.3） | 全部 | `PresenceCapsule.tsx` `presence.ts` | `presence.test.ts` reconnecting 3s | | 「点按胶囊=打开语音层」属 B2 ⏭；B1 刻意不接 onPress（附加项⑤） |
| U1 顶栏连接 pill 降级为健康点（§4.3 末条） | 全部 | `ChatScreen.tsx` | — | | |
| U3 Focus Dock 四项 `confirm/slot/task/queue`（§5.3） | `confirm/task/queue` 有产出方；`slot` **只类型与样本**（协议无 `missing_slots`，Q19） | `FocusDock.tsx` `commitment.ts` | `commitment.test.ts` `presence.test.ts` | | `slot` 判 ⏭（后端挂账）而非 ❌ |
| U3 Dock 不轮播、钉最高风险最早到期、稳定排序（§5.3） | 全部 | `commitment.ts::pinCommitment/sortCommitments` | `commitment.test.ts` | | |
| U3 倒计时只读共享 TTL、到期精确调度、到期留痕（§5.3） | 全部 | `store.ts::syncPruneTimer/noteExpired` `FocusDock.tsx` | `sessionStore.test.ts` B1-4 | | 核：进度条用 `PENDING_TTL_MS` 而非字面量；到期文案 |
| U3 Dock 材质 G0 实色（§5.3、§5.11） | 全部 | `FocusDock.tsx` | — | | 核 `solid` 底色不透明 |
| §5.3.1 ConfirmPolicy 只投影 VAL：UI 不据车速自定规则；VAL 拒绝 → `safety_blocked` | 第一阶段：**不据 driving 做限制** ✅ 应落；`safety_blocked` 无结构化信号 ⏭（Q16） | `FocusDock.tsx` `usePresence.ts` | — | | **核 UI 里没有任何 `speed` / 车速阈值**（`grep -rn "speed" mobile/src`） |
| §5.3.2 执行回执 | **B2** ⏭ | — | — | ⏭ | 不算缺口 |
| §5.7 离线：`muted` + 胶囊 + `queue` 项；「发送状态未知」灰字 | 全部 | `store.ts::setStatus/uncertainIds` `MessageBubble.tsx` | `sessionStore.test.ts` | | 附加项①（离线暂停看门狗）是 B1 新增，方案 §5.7 **没写**——候选 🔁 回写 |
| §5.8 Onboarding 上品牌 + 权限用途文案 | 全部 | `app/onboarding.tsx` | — | | 核逻辑函数未改（`onTest/onSave/derived`） |
| §5.9 token 层（新组件必用，旧组件不扫荡） | 骨架 + 新组件使用 | `ui/tokens.ts` | `tokens.test.ts` | | 核新组件是否真用了 `scale/TARGET/RADIUS` |
| §5.10 隐私栏（麦/摄像头/最近一次/当前用户/一键关闭/差异说明）+ 顶栏采集点 | 全部 | `PrivacyRail.tsx` `activityLog.ts` `ChatScreen.tsx` | `activityLog.test.ts` | | ⚠ 已知：`privacy.mic` 三档并了「端侧待机」与「上传服务端 ASR」（计划 §6.3 坑②、§6.4 出账②）——判分类并给裁决建议 |
| §5.10「开录即告知」+ 首次显式同意（同 §5.2.2） | **B2** ⏭（语音层） | — | — | ⏭ | 但设置页切端到端的一次性同意弹窗是否该属 B1？给判断 |
| §5.11 材质三档 token；G2 只给光球/把手/选中 chip | token ✅ 应落；G2 反应式 B2 ⏭ | `ui/tokens.ts::GLASS` | `tokens.test.ts` | | |
| §7.6 键盘避让：先读数再修 + Maestro 08 | 全部 | `ChatScreen.tsx` `onboarding.tsx` `debug.tsx` `e2e/08` | Maestro 08 | | 核三处 `behavior` 一致；读数与修法在 §6.3 |
| §8.1 无障碍：200% 重排 / 减少透明度 / partial 节流 / 轻点切换 | 200% **验收项**；其余 B4 ⏭ | — | — | | §6.4 第 9 条：Dock 标题 200% 下被挤成「这..」⇒ ⚠ |
| §11.5 开关 `uxV2Presence/uxV2Dock`，v1 代码保留 | 全部 | `settings/store.ts` `ChatScreen.tsx` `Composer.tsx` `MessageBubble.tsx` | `settingsMeta.test.ts` | | 核关掉两开关后 v1 路径完整（§6.4 第 11 条 + Maestro 02） |
| §11.5 埋点：20 条在场轨迹环形日志 + 调试屏页 | 计划把它写成 activityLog（采集激活）——**与方案「PresenceSnapshot 变化轨迹」不是一回事** | `activityLog.ts` | | | 判：⚠ 或 🔁（方案回写成「B1 只落采集激活日志，在场轨迹页留 B2」） |
| §11.6 追溯矩阵 P1–P14 | 逐行核「代码落点/测试/指标/回滚开关」是否成立 | 见方案 §11.6 | | | 每行一个分类 |
| §12.1 七种降级：产出方 / 停留 / 面 / 出口 | `permission_denied / service_degraded / audio_echo_degraded / transport_unknown` 应有产出方；`recoverable_error` 胶囊 4s；`safety_blocked / fatal` 无产出方 ⏭ | `usePresence.ts` `FocusDock.tsx::DegradationRow` | `presenceFixtures.test.ts`（含产出方守卫） | | 核 `audio_echo_degraded` 的「重新开启插话」= 关再开免唤醒（权宜）是否如实标注 |
| §12.2 威胁模型（B1 相关行）：唤醒误采回收、S2S 告知、摄像头不出预览、承诺面 G0 + 比例 + 倒计时 | 除 S2S 告知（B2）外应落 | 各处 | | | |
| §11.4「不负优化」八条判据 | 首反馈时延（B2 取数 ⏭）/ 可读性（B2 后 ⏭）/ 记录完整性（B2 ⏭）/ **承诺不丢 ✅ 应落** / **键盘 ✅** / 性能（§6.4 第 12 条只有 GPU 侧）/ 无障碍（未跑 ⬜）/ **回归（条数只增不减）** | | | | 逐条给分类 |
| §11.2 B1 验收清单（含四批附加项） | 与 §6.4 十三条表逐条对照 | | | | 对 §6.4 的 ✅ 抽三条复核证据链（不是重跑：看截图是否存在、读数是否自洽） |

### 2.2 红线与保留项（一条都不许 ⚠）

| 项 | 核法 | 结果 |
|---|---|---|
| S2S 默认 `classic`、只能设置里显式选 | `settings/store.ts` 默认值；B1 没加任何「一键切挡位」 | |
| 视觉默认关、命中才挂相机、**不出预览** | `VisionCapture.tsx` 未改语义；`looking` 态无预览 | |
| 声纹不进 App | `grep -rn "occupant\|voiceprint" mobile/src` 只应有常量 `primary` | |
| `hmi/` 零改动、共享判据零改动、白名单未扩 | `git diff --stat 3cc6b74^..5839e62 -- hmi/ mobile/shared-allowlist.json` 为空 | |
| 编排核心零改动 | 同上加 `orchestrator/`（B1 线不该碰；若有改动，先确认是别的线的提交） | |
| 光球十条不变量 | 2.1 那一行 | |
| 对话记录未被替代；`SessionCore/requestRouting/pendingOps` 语义不改 | `store.ts` diff：只加不改归属规则 | |
| 前台交互档承诺不变 | 无前台服务 / 通知代码 | |

## 3. 结论先行（评审者填）

- 一句话结论：
- 分类计数：✅ __ / ⚠ __ / ❌ __ / ⏭ __ / 🔁 __
- ❌ 与 🔁 逐条（这两类是评审的全部价值）：

## 4. 缺陷与建议（按严重度排序；每条：现象 → 证据（file:line / 测试名 / §6 条目）→ 影响 → 建议落点 B2/B3/B4/独立批）

## 5. 方案需要回写的条目（🔁）

> 实现推翻或细化了方案假设的地方，列出「方案原句 → 应改成」，由方案作者回写 v2.2；评审不直接改方案。

## 6. 给 B2 的入口建议

> B2 = 语音层（方案 §11.1）。列出 B1 遗留里 B2 **必须**先处理的（建议 ≤5 条），其余留在计划 §6.4 出账表。
