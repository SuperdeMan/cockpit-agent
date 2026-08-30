# UX v2.2 · B1「在场与锚」实施计划（已收口；逐任务历史）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 状态：**四批已收口；2026-08-30 落地评审完成（✅29 / ⚠8 / ❌1 / ⏭5 / 🔁5）；五条 🔁 已回写上游总方案 v2.2；下一步 = 新会话拆 B2「语音层」，第一件事 D1**
> 交付对象：`mobile/` 执行者（人或 Agent）
> 上游真相源：[`2026-08-29-mobile-ux-v2-presence-redesign.md`](2026-08-29-mobile-ux-v2-presence-redesign.md)（方案 v2.2；本计划只展开 **B1**，B2–B5 见其 §11.1）
> 历史边界：正文 Task / commit / 实施记录中的“UX v2.1”是实施时标签，作为历史证据保留，不批量改名。
> 纪律：沿用 [`2026-08-24-mobile-app-implementation-plan.md`](2026-08-24-mobile-app-implementation-plan.md) §0 接手须知 + §9 坑账；每任务「先测后码、一任务一提交」；**不动 `hmi/`、不动共享判据、不改编排核心**（唯一例外走 §10 的「共享模块有 bug」条款，本批没有）

**Goal:** 把 App 的「此刻在干嘛」收成一个多轴派生态 `PresenceSnapshot`，由光球（含三个新态）+ 一枚状态胶囊 + 一块承诺面 Focus Dock + 一张隐私栏承载；顺带落 token 层、Onboarding 上品牌、键盘避让读数、`/state-gallery` 取证屏与三条 Maestro 流。**全 JS，不重建 APK。**

**Architecture:** `core/presence/presence.ts` 是一个纯函数 `derivePresence(input) → PresenceSnapshot`（六个正交轴 + 一个视觉主态 `primary`），输入全部来自既有状态机（`SessionCore` store / 免唤醒 FSM / PTT / 设置 / 播报控制器 / 视觉抓帧），**不新增状态机、不改共享 FSM**。UI 侧 `usePresence()` 收集输入并调用它；`PresenceCapsule` 读 `capsule`、`FocusDock` 读 `commitment[] + degradation[]`、`AuroraOrb` 读 `primary`、顶栏读 `transport/privacy`。两个设置开关 `uxV2Presence / uxV2Dock`（缺省开）保留 v1 形态作回滚路径。

**Tech Stack:** React Native 0.86 / Expo 57 / expo-router / zustand / react-native-reanimated 4.5 / jest-expo（`mobile/test/**/*.test.ts`，纯逻辑测试）/ Maestro 2.9（`mobile/e2e/`）。

---

## 0. 接手须知（先读）

1. **开工前提**（不变）：`powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；Metro `cd mobile && npx expo start --dev-client`；真机 dev-client 已装（本批**不重建**，任何「要重建」的念头都说明走偏了——B1 零新原生依赖）。
2. **每个任务的顺序是固定的**：写失败测试 → 跑出红 → 最小实现 → 跑绿 → `tsc` → 提交。`mobile/` 的测试命令：`cd mobile && npx jest test/<file>.test.ts`（单文件）/ `npm test`（全量，当前 234 通过）/ `npm run typecheck`。
3. **提交只加自己的路径**：`git commit -- <paths>`（比 `git add` 再 commit 少一步暂存态）；提交后 **`git show --stat HEAD` 复核行数**。共享工作树里可能有别的会话的改动（2026-08-29 当时是 `docs/design/2026-08-24-…`、`mobile/package.json`、`mobile/patches/`），**不要 `git add -A`**。⚠ 按路径隔离得了「文件」，隔离不了**同一个文件里别人未提交的行**——`AGENTS.md` 正是所有会话都在写的那一个（2026-08-29 出过一次：55 行被扫进别人的 commit，靠 soft reset + `hash-object`/`update-index` 只暂存自己那行才拆开）。动 `AGENTS.md` 前先 `git diff --stat -- AGENTS.md` 看行数对不对得上自己的预期。另：全仓提交都是同一个 git 身份，「这个 commit 是哪条会话的」靠元数据答不出来，只能靠会话自己说——别按「紧挨着」推断归属。
4. **真机取证一律截图**（`adb exec-out screencap -p -d <displayId>`，折叠屏 id 用 `dumpsys SurfaceFlinger --display-id` 取）；`uiautomator dump` 在有常驻动画的屏上不可信（坑账 §9.40/48）。
5. **三个结构性事实，写代码前记住**：
   - `Msg` 类型在 `hmi/src/types.ts`（共享，**不能加字段**）——任何「气泡上要多显示一个态」都走 `SessionState` 的并列字段（本计划的 `uncertainIds` 就是这么做的），或追加一条新消息。
   - `pendingOps` 的 TTL 是共享模块 `pendingOps.mjs::PENDING_TTL_MS = 300_000`——倒计时**只许读它**，UI 不另存时间。
   - 协议里**没有** `missing_slots`、没有 VAL 拒绝的结构化标记（2026-08-29 grep 全空）⇒ `DockItem.slot` 与 `Degradation.safety_blocked` 在 B1 **只有类型与画廊样本、没有生产产出方**；方案 §13 Q16 / 新增 Q19 挂账后端。不许在客户端用正则猜「这句是不是 VAL 拒绝」。
6. 方案里已裁决的默认值（Q1–Q19）不在本计划重议；本计划新增的实施判断集中在 §5。

### 0.1 分批执行：一批一个会话（新会话从这里开始）

本计划 2700 行，一个会话读不完也不该读完。**分四批，每批一个新会话**，每批以「jest 全绿 + `tsc` 0 + 逐任务已提交 + §6 实施记录回填」收口；下一批冷启动只读 §0 + §0.1 + §1 + 自己那几个 `### Task N` 块（用 `grep -n "^### Task" <本文件>` 取行号，`sed -n` 只读自己的段），**不读整份计划、不读方案全文**（方案只在任务里点名的 §号处查）。

| 批 | 会话任务 | 性质 | 并行度 | 收口判据 | 真机？ |
|---|---|---|---|---|---|
| **第 1 批「纯逻辑层」** | T1 tokens → T2 commitment → T3 presence → T4 store → T5 信号 | 零 UI、零 RN 渲染，全部 jest 可跑 | T1/T2/T4/T5 四个 subagent 并行，T3 等 T2 | `npm test` 全绿（234 → ≈280）、`tsc` 0、5 个 commit、§6 第 1 批记录 | 否 |
| **第 2 批「光球与取证屏」** | T6 光球 → T7 开关 → T8 activityLog + usePresence → T9 胶囊 + Dock → **T14 状态画廊**（提前到这一批，作为组件的视觉验证台） | 组件成型但**还没接进对话屏** | T6/T7 并行，其余串行 | `tsc` 0、`presenceFixtures.test.ts` 绿、**真机画廊深浅各一套截图**、Maestro 09（offline）通过、§6 第 2 批记录 | 是（画廊 + Metro 热载） |
| **第 3 批「接线」** | T10 对话屏接线 → T11 隐私栏 → T12 键盘（先读数）→ T13 Onboarding | 用户可见的形态切换；两个回滚开关必须验 | 串行 | 真机：危险动作进 Dock / 关开关回 v1 / 隐私栏三行 / 键盘读数 + Maestro 08 通过 / Onboarding 深浅截图；§6 第 3 批记录 | 是 |
| **第 4 批「验收与记录」** | T15 Maestro 06 → T16 真机 13 条验收表（**第 2 条唤醒、第 4 条 300s 到期需泓舟在场**）→ T17 记录 + AGENTS.md | 取证与收口 | 串行 | 13 条逐条 ✅/⬜/❌ 写实、反向验证两条、遗留出账；AGENTS.md Android 行更新（先 `git diff --stat` 核行数） | 是 |

**第 2 批附加项（第 1 批 §6.1 遗留转入，做在 T6 之前或随所在任务顺手做，各自单独提交）**：
- 补 `test/presence.test.ts` 的四对共存用例：listening↔speaking、speaking↔thinking、thinking↔followup、followup↔armed（每对让两态**同时在场**，断言 `primary` 取高档）。T6 光球直接读 `primary`，改错顺序要红得出来。
- 删 `presence.ts:100` 的死变量 `hfListening`（仓库没有 eslint 配置、`noUnusedLocals` 未开，没有门禁会抓它）。
- T8 的 `usePresence` 必须消费 `isVisionCapturing()`（计划代码里 `useState(isVisionCapturing)` 就是）；若最终没消费，收口时把它删掉。
- 记住 `setStatus` 已对同值状态早退、`queued` 只由 `transport.send()` 返回 `false` 驱动（真实 `GatewaySession` 断线时是否返 false **未验**）——这两条不在第 2 批动，但 T8/T9 别假设它们成立；第 3 批 T10 与第 4 批真机验收要专门确认。
- 第 1 批没有分树（泓舟未授权），提交都在本地 `main` 未推送；第 2 批继续在主工作树，`git status` 的有效期只到别人下一次落盘为止，据它判断前先重采。

**第 3 批附加项（第 2 批 §6.2 遗留转入；①②③ 是 T10 的一部分，④⑤ 随 T10/T11 顺手，各自单独提交）**：
- ① **两个实验室开关必须在 T10 里有消费方**：`uxV2Presence` 关 ⇒ 四条 v1 状态条与旧光球推导回来；`uxV2Dock` 关 ⇒ 气泡内确认按钮回来。收口时真机各关一次截图——「设置页有开关、关了没反应」比没开关更糟。
- ② **修确认卡倒计时冻住**（§6.2 坑⑤）：`FocusDock.tsx` 的 `CommitmentCard` 删掉组件本地的 `setInterval` 时钟，`now` 从 `snapshot` 走（`usePresence` 已每秒 tick；`PresenceSnapshot` 加一个 `now: number` 字段由 `derivePresence` 原样透传输入的 `now`）；同时把 `accessibilityLiveRegion="assertive"` 从含倒计时的子树挪到只含摘要行的 `View` 上（否则 TalkBack 每秒重播整张卡）。验证：接线后触发「打开后备箱」，盯 15 秒数字必须在走。
- ③ **`usePresence` 三处已知偏差，T10 按既有判据接、不在收集器里发明判据**：`driving` 实质恒 false（`Msg.driving` 只由 `process` 帧写；「哪个 vehState 键算行车」的判据不存在，写进收集器就是第二份 VAL）——B1 接受恒 false，B4 再接；`lastError.at` 是「hook 第一次看见」不是发生时刻——T10 只对**挂载之后**出现的 error 气泡显示 4s 红胶囊（挂载时已存在的不算）；`connChangedAt` 挂载时重置——接受最晚迟 3s。
- ④ 秒级 `setInterval` 无条件常开：T10 接进 `ChatScreen` 后看一眼是否带着 FlashList 与光球一起重渲（React DevTools 或 `console.count`）；若明显，只在「有 pendingOps / processActive / reconnecting / 4s 内 error」任一成立时才 tick，否则停表。
- ⑤ `PresenceCapsule` 在 B1 **不接 `onPress`**（它是 `accessibilityRole="text"`、26dp，接了点按就同时违反读屏与热区两条）；`DegradationRow` 的 `key` 改成 `${d.kind}:${'what' in d ? d.what : 'reason' in d ? d.reason : ''}`。
- ⑥ **`queued` 的真实前提本批要验**：飞行模式下发一条 → 探活判死（≤30s）→ Dock 出现「N 条消息排队中」；若不出现，先查 `GatewaySession.sendRaw`/`ws.mjs.send` 断线时是否返 `false`（第 1 批遗留③，单测里是替身写死的）。**没出现不许读成「没有排队」。**
- ⑦ 取证纪律（§6.2 坑⑥⑩）：取证前 `adb shell am force-stop com.xiaozhou.companion` 再进（有时间基准的屏不许用深链「刷新」）；Metro 若已有别的会话起着的 dev server，先核它的命令行服务的是本仓 `mobile/`，**复用、别再起一个**（撞 8081）；新建文件 `git add -- <路径>` 与 `git commit` 必须在同一条命令链里。

**第 4 批附加项（第 3 批 §6.3 遗留转入 + 验收表按实情校准）**：
- ① **T16 前置修复（先做，单独提交，有 jest）——离线期间暂停看门狗、重连后重新计时**。§6.3 遗留③的现象有确定的机制：飞行模式下发出的帧入队，恢复网络后退避重连约 2 分钟，而那一轮的 95s 看门狗早已把气泡收成「响应超时」并从 `registry` 注销 ⇒ 重连后帧确实补发了（文案没说谎），但回来的 `final` 带着一个已注销的 `request_id`，按「对不上=丢帧」被丢——**用户永远拿不到答案**。修法只在 `SessionCore`（共享路由一字不动）：`setStatus('closed')` 时对所有在飞 id `clearTimeout` 并记入 `pausedWatchdogs: Set<string>`；`setStatus('open')` 时对每个 paused id 重新 `armWatchdog`（整 95s）；`uncertainIds` 语义不变。测试（`sessionStore.test.ts` 追加，假时钟）：send → `setStatus('closed')` → 推进 150s → 气泡**仍 pending** → `setStatus('open')` → 推进 94s 仍 pending → 96s 转超时；再一条：closed → open → 在 30s 时收到 final → 正常收尾、无「响应超时」。真机复验：飞行模式发一句 → 等 2 分钟以上 → 关飞行模式 → **答复气泡出现**（这就是 T16 第 5 条的另一半）。
- ② **T16 验收表按实情改三处**：第 6 条隐私栏「端到端录音中」那一行要真人说话——与第 2 条（唤醒）合并成泓舟在场的同一段，顺手用 `adb shell screenrecord` 录 20s（`looking` 快门白环、`cloudAudio` 行都靠这一段录屏，§6.2 遗留⑥ / §6.3 遗留①）；第 7 条 VAL 拒绝**预期写「未接、待 Q16」**，不是绿；第 4 条 300s 到期不需要人，但要 5 分钟不碰手机（keep-awake 开着）。
- ③ **不改 `PENDING_TTL_MS` 去缩短验收等待**——那是共享判据（`pendingOps.mjs`），改了 hmi 也跟着变。等 5 分钟。
- ④ 第 3 批坑②留下的判据层问题（`privacy.mic='edge'` 并了「端侧待机」与「音频上传给服务端 ASR」两件事）**B1 不动**，写进遗留出账给 B2/B4 裁；文案层已分开。
- ⑤ T17 除记录外还要：`mobile/e2e/README.md` 把 06/08/09 三条流的状态与实跑时长补齐（08/09 已过、06 本批跑）；`AGENTS.md` Android 行改成「B1 四批收口 + 下一步 B2」——**动前 `git diff --stat -- AGENTS.md` 核行数、提交后 `git show --stat` 复核**；前三批 §6 的遗留汇总成一张出账表（含未推送清单：`git log origin/main..HEAD --oneline`）。
- ⑥ **推送需泓舟单独授权**：收口时把待推送提交数报给泓舟，不擅自 `git push`。

**每批开工的固定五步**（写进新会话的第一条提示词）：
1. `powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0（第 1 批可跳过——纯 jest）；
2. `cd mobile && npm test && npm run typecheck` 取**开工基线**（条数与 0 error），写进 §6 该批记录的第一行——读数有效期只到下一次改动；
3. 只读 §0 / §0.1 / §1 + 自己批次的 `### Task N` 块；
4. 按任务顺序：写失败测试 → 跑红 → 实现 → 跑绿 → `tsc` → `git commit -- <只加自己的路径>` → `git show --stat HEAD` 复核；
5. 收口：全量 `npm test` + `tsc`，把读数、遗留、撞到的坑写进 §6，**然后停下**——下一批是另一个会话的事。

**worktree**：三条线共用一个工作树今天已出过一次事故（§0 第 3 条）。若泓舟同意分树，第 1 批开工前执行一次并写进 §6：`git worktree add ../car-agent-ux-b1 -b ux-v2-b1`，四批都在该 worktree 里做，最后由泓舟决定合回 `main` 的方式（`git push` 仍需单独授权）。没有分树就在主工作树做，纪律同 §0 第 3 条。

**批与批之间的状态只靠两处传递**：git 提交（代码）与本文件 §6（读数与遗留）。新会话不要去翻上一批会话的对话——那些不在仓库里。

---

## 1. 文件结构（先定边界，再拆任务）

### 新建

| 文件 | 职责 | 依赖 |
|---|---|---|
| `mobile/src/ui/tokens.ts` | 间距 / 圆角 / 字阶 / 动效 / 触控目标 / 材质三档 token + `scale()` | 无 RN import |
| `mobile/src/core/presence/presence.ts` | `PresenceSnapshot` 类型 + `derivePresence()` 纯函数 + 胶囊文案表 | `pendingOps.mjs`（只读 TTL） |
| `mobile/src/core/presence/commitment.ts` | `DockItem` 类型、稳定排序、钉住项选择、确认剩余时间 | 无 |
| `mobile/src/core/presence/fixtures.ts` | `presenceFixtures()`：每个 `primary` 与每种 `degradation` 至少一条样本 | presence.ts |
| `mobile/src/features/chat/usePresence.ts` | 收集输入（store / hf / ptt / settings / 播报 / 视觉 / 时钟）→ `derivePresence` | 上述 + 既有 hooks |
| `mobile/src/features/chat/PresenceCapsule.tsx` | 状态胶囊（读 `snapshot.capsule`） | tokens |
| `mobile/src/features/chat/FocusDock.tsx` | 承诺面（读 `commitment[]` / `degradation[]`），G0 实色 | tokens、commitment |
| `mobile/src/features/chat/PrivacyRail.tsx` | 隐私栏（读 `snapshot.privacy` + 最近激活日志），G0 | tokens |
| `mobile/src/core/presence/activityLog.ts` | 采集激活环形日志（20 条，内存，不上传） | 无 |
| `mobile/src/app/state-gallery.tsx` | 取证屏：逐样本渲光球 + 胶囊 + Dock + 隐私栏摘要；`?only=` 直达 | fixtures |
| `mobile/test/tokens.test.ts` `presence.test.ts` `commitment.test.ts` `presenceFixtures.test.ts` `activityLog.test.ts` | 纯逻辑守卫 | jest |
| `mobile/e2e/06-confirm-dock.yaml` `08-keyboard-no-hide.yaml` `09-state-gallery.yaml` | Maestro 扩流 | — |

### 修改

| 文件 | 改什么 | 为什么 |
|---|---|---|
| `mobile/src/ui/aurora/AuroraOrb.tsx` | `OrbState` 加 `attention / looking / muted`；琥珀环 / 一次白环 / 去饱和停旋 + 灰环；`dim` prop | 方案 §4.2 三新态，十条不变量内 |
| `mobile/src/core/session/store.ts` | `syncPruneTimer` 精确调度；剪枝到期时追加「确认已过期」消息；`queued` 计数；`uncertainIds` | 方案 §5.3 / §5.7 |
| `mobile/src/core/voice/speech.ts` | `speaking` 可订阅（`subscribeSpeaking`） | Presence 的 `agent.speaking` 需要一个不靠链式覆盖回调的来源 |
| `mobile/src/core/vision/frame.ts` | `capturing` 可订阅 | `looking` 态 |
| `mobile/src/core/voice/handsFree.ts` | 新增 deps `onBargeInDisabled(reason)` / `onPipelineDegraded(kind, msg)` | 结构化降级信号（§12.1），不再只靠 notice 字符串 |
| `mobile/src/features/chat/useHandsFree.ts` | 透出 `bargeInDisabled` / `pipelineDegraded` | 同上 |
| `mobile/src/features/chat/usePtt.ts` | `errorKind: 'permission' \| 'start' \| 'asr' \| ''` | `permission_denied` 降级项 |
| `mobile/src/core/settings/store.ts` | `uxV2Presence` / `uxV2Dock` / `deviceRole` 字段 | 回滚开关（§11.5）；身份轴占位 |
| `mobile/src/features/settings/SettingsScreen.tsx` | 「实验室」分区两开关 + `/state-gallery` 链接 | 同上 |
| `mobile/src/features/chat/ChatScreen.tsx` | 四条窄条 → 胶囊 + Dock；顶栏健康点 + 采集点 + 隐私栏入口；键盘避让 | 方案 §4.3 / §5.1 / §5.10 / §7.6 |
| `mobile/src/features/chat/Composer.tsx` | 光球读 `primary`；热区 56 / 球 44；删提示行 | §5.1 |
| `mobile/src/features/chat/MessageBubble.tsx` | Dock 开时气泡内不渲染确认按钮；`uncertain` 灰字 | §5.3 / §5.7 |
| `mobile/src/app/onboarding.tsx` | 上品牌（AuroraBackground + Glass + 光球 + 色板 + 安全区 + 权限用途文案） | §5.8 |
| `mobile/src/app/_layout.tsx` | 注册 `state-gallery` 路由 | — |
| `mobile/e2e/README.md`、`docs/design/2026-08-24-mobile-app-implementation-plan.md`（只加 §B1 实施记录指针）、`AGENTS.md` §4.1 | 记录 | 收口任务 |

---

## 2. 任务清单

### Task 1: token 层骨架（`ui/tokens.ts`）

**Files:**
- Create: `mobile/src/ui/tokens.ts`
- Test: `mobile/test/tokens.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/tokens.test.ts
// token 层（UX v2.1 §5.9）：数值逐值照 Figma A-1 设计系统；`scale()` 是「大字」档同时放大
// 文字 / 目标 / 行高的唯一入口——此前 Palette.font() 只放大文字，容器与热区不跟着长（P13）。
import { GLASS, MOTION, RADIUS, SPACE, TARGET, TYPE, scale } from '@/ui/tokens'

describe('tokens 数值照 A-1 设计系统', () => {
  test('4px 栅格与圆角阶', () => {
    expect(SPACE).toEqual([4, 8, 12, 16, 24, 32, 48])
    expect(RADIUS).toEqual({ sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 })
  })
  test('触控目标：泊车 48 / 行车 56（Guidelines :325-327）', () => {
    expect(TARGET).toEqual({ parked: 48, driving: 56 })
  })
  test('材质三档：G0 不透明不模糊；G2 只给光球与把手（§5.11）', () => {
    expect(GLASS.solid.blur).toBe(0)
    expect(GLASS.solid.opacity).toBeGreaterThanOrEqual(0.96)
    expect(GLASS.frosted.blur).toBeGreaterThan(0)
    expect(GLASS.reactive.blur).toBeGreaterThanOrEqual(GLASS.frosted.blur)
  })
  test('MOTION 含光球四态基准时长（毫秒）', () => {
    expect(MOTION.orbIdle).toBe(4000)
    expect(MOTION.fast).toBeLessThan(MOTION.base)
    expect(MOTION.base).toBeLessThan(MOTION.slow)
  })
})

describe('scale()：大字档同时放大文字、目标与行高', () => {
  test('normal 档原样', () => {
    expect(scale(15, 'text', 'normal')).toBe(15)
    expect(scale(48, 'target', 'normal')).toBe(48)
  })
  test('large 档：文字 ×1.15、目标 ×1.1、行高 ×1.15', () => {
    expect(scale(15, 'text', 'large')).toBe(17)
    expect(scale(48, 'target', 'large')).toBe(53)
    expect(scale(23, 'line', 'large')).toBe(26)
  })
  test('缺省档参数 = normal', () => {
    expect(scale(20, 'text')).toBe(20)
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/tokens.test.ts`
Expected: FAIL — `Cannot find module '@/ui/tokens'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/ui/tokens.ts
// 设计 token（UX v2.1 §5.9 / §5.11）。数值逐值照 Figma A-1 设计系统
// （docs/design/【新】座舱Agent-HMI-A-1 Design System.zip → guidelines/Guidelines.md
//  §间距 4px 栅格 / §圆角 / §字阶 / §触控目标 / §10 光球动效）。
// 色板仍在 theme.ts（Palette）；这里只放**尺寸、节律、材质档**，与色板分文件是因为
// 色板随深浅主题变、尺寸不随主题变。
// 使用纪律：**新组件必用；旧组件只在被触碰时顺手换**，不做全仓扫荡（34 个卡渲染器逐处改
// 是独立批，与 M3-V「等宽数字铁律」同一处置）。
import type { FontScalePref } from '../core/settings/store'

/** 4px 栅格 */
export const SPACE = [4, 8, 12, 16, 24, 32, 48] as const

export const RADIUS = { sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 } as const

/** 字阶（pt）。mono 用系统等宽——JetBrains Mono 未随 App 打包（M3-V 刻意不做） */
export const TYPE = { display: 32, h1: 24, h2: 18, body: 15, caption: 12, micro: 11, mono: 'monospace' } as const

/** 过渡与光球基准节律（ms）。光球各态的旋转/呼吸时长仍在 AuroraOrb.tsx（照 A-1 §10），
 *  这里只登记 idle 呼吸基准，供状态画廊与减少动效判断引用 */
export const MOTION = { fast: 120, base: 180, slow: 260, orbIdle: 4000 } as const

/** 触控目标（dp）：泊车 48 / 行车 56（Guidelines :325-327） */
export const TARGET = { parked: 48, driving: 56 } as const

/** 材质三档（方案 §5.11）。frosted/reactive 的 blur 在 B3 spike 前**不真的用**——
 *  RN 无 backdrop-filter，B1 的 G1 就是 theme.ts 的 glass（tint 版）。 */
export const GLASS = {
  /** G0 Solid/Safety：确认、错误、隐私说明、行车限制、压在不可控内容上的浮层 */
  solid: { blur: 0, opacity: 0.96 },
  /** G1 Frosted：顶栏、语音层外壳、舞台抽屉、Onboarding 容器、卡壳 */
  frosted: { blur: 28, tint: 0.58, border: 0.16 },
  /** G2 Reactive：只给光球、语音层把手、选中态 chip */
  reactive: { blur: 34, tint: 0.42, specular: 0.32 },
} as const

export type ScaleKind = 'text' | 'target' | 'line'

/** 「大字」档的唯一放大入口：文字 ×1.15、触控目标 ×1.1、行高 ×1.15；normal 原样。
 *  四舍五入到整 dp（RN 的 lineHeight/height 取小数会在 Android 上出现半像素缝）。 */
export function scale(size: number, kind: ScaleKind, pref: FontScalePref = 'normal'): number {
  if (pref !== 'large') return size
  const k = kind === 'target' ? 1.1 : 1.15
  return Math.round(size * k)
}
```

- [ ] **Step 4: 跑测试确认绿 + 类型检查**

Run: `cd mobile && npx jest test/tokens.test.ts && npm run typecheck`
Expected: PASS（7 tests）；`tsc` 0 error

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/ui/tokens.ts mobile/test/tokens.test.ts
git commit -m "feat(mobile): UX v2.1 B1-1 token 层骨架——间距/圆角/字阶/目标/材质三档 + scale()"
```

---

### Task 2: 承诺项与降级项的纯逻辑（`core/presence/commitment.ts`）

**Files:**
- Create: `mobile/src/core/presence/commitment.ts`
- Test: `mobile/test/commitment.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/commitment.test.ts
// Focus Dock 的承诺项（方案 §5.3 / §4.1 commitment 轴）：
//  · 排序稳定：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列；同类按最早到期在前
//  · 钉住项 = 排序后的第一项；其余只报个数（**不轮播**，评审 P1-2 采纳）
//  · 确认剩余时间**只读共享 TTL**（pendingOps.mjs::PENDING_TTL_MS），UI 不另存时间
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import {
  confirmRemainingMs,
  pinCommitment,
  sortCommitments,
  type DockItem,
} from '@/core/presence/commitment'

const NOW = 1_000_000

const confirm = (id: string, ts: number, risk: 'low' | 'high' = 'high'): DockItem => ({
  kind: 'confirm',
  id,
  summary: `动作 ${id}`,
  risk,
  expiresAt: ts + PENDING_TTL_MS,
})

describe('sortCommitments', () => {
  test('类别顺序：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列', () => {
    const items: DockItem[] = [
      { kind: 'queue', id: 'q', count: 2 },
      { kind: 'task', id: 't', label: '规划路线', startedAt: NOW - 9000 },
      { kind: 'slot', id: 's', missing: '你的位置' },
      confirm('c-low', NOW, 'low'),
      confirm('c-high', NOW, 'high'),
    ]
    expect(sortCommitments(items).map((i) => i.id)).toEqual(['c-high', 'c-low', 's', 't', 'q'])
  })
  test('同类按最早到期在前', () => {
    const items = [confirm('later', NOW - 1000), confirm('sooner', NOW - 60_000)]
    expect(sortCommitments(items).map((i) => i.id)).toEqual(['sooner', 'later'])
  })
  test('排序不改原数组（纯函数）', () => {
    const items = [confirm('b', NOW - 1000), confirm('a', NOW - 5000)]
    const copy = items.slice()
    sortCommitments(items)
    expect(items).toEqual(copy)
  })
})

describe('pinCommitment', () => {
  test('空 → null', () => {
    expect(pinCommitment([])).toBeNull()
  })
  test('钉住排序后的第一项，others = 其余个数', () => {
    const items: DockItem[] = [{ kind: 'queue', id: 'q', count: 1 }, confirm('c', NOW)]
    const pinned = pinCommitment(items)
    expect(pinned?.item.id).toBe('c')
    expect(pinned?.others).toBe(1)
  })
})

describe('confirmRemainingMs', () => {
  test('剩余 = expiresAt - now，下限 0', () => {
    const c = confirm('c', NOW - 10_000)
    expect(confirmRemainingMs(c, NOW)).toBe(PENDING_TTL_MS - 10_000)
    expect(confirmRemainingMs(c, NOW + PENDING_TTL_MS + 5)).toBe(0)
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/commitment.test.ts`
Expected: FAIL — `Cannot find module '@/core/presence/commitment'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/core/presence/commitment.ts
// Focus Dock 的承诺项（方案 §5.3）——「系统欠用户一个动作或一个结果」的那些东西。
// 纯类型 + 纯函数，零 RN import（jest 直接跑）。
//
// 四种 + 两种「B1 没有生产产出方」的：
//  · confirm：来自 pendingOps（危险动作二次确认）与位置授权征询（subkind='location'）
//  · slot：**协议里没有 missing_slots**（2026-08-29 grep 全空）⇒ B1 只有类型与画廊样本，
//    产出方等后端（方案 Q19）。不许在客户端猜「这句是不是在补槽」。
//  · task：长任务（process 帧持续 >8s）
//  · queue：离线队列（SessionState.queued）
// 排序稳定（评审 P1-2 采纳）：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列；
// 同类最早到期在前；Dock 只钉第一项，其余报个数、**不轮播**。

export type DockItem =
  | {
      kind: 'confirm'
      id: string
      /** 用户看得懂的动作摘要（气泡原话截断） */
      summary: string
      risk: 'low' | 'high'
      /** = 台账 ts + PENDING_TTL_MS；**只读共享 TTL**，UI 不另存时间 */
      expiresAt: number
      /** 位置授权征询：按钮文案换成「允许 / 拒绝」，不上行 operation_id */
      subkind?: 'location'
    }
  | { kind: 'slot'; id: string; missing: string }
  | { kind: 'task'; id: string; label: string; startedAt: number }
  | { kind: 'queue'; id: string; count: number }

const KIND_ORDER: Record<DockItem['kind'], number> = { confirm: 0, slot: 2, task: 3, queue: 4 }

function rank(item: DockItem): number {
  if (item.kind === 'confirm') return item.risk === 'high' ? 0 : 1
  return KIND_ORDER[item.kind]
}

function dueAt(item: DockItem): number {
  if (item.kind === 'confirm') return item.expiresAt
  if (item.kind === 'task') return item.startedAt
  return Number.MAX_SAFE_INTEGER
}

/** 稳定排序：类别 → 到期时间。不改原数组。 */
export function sortCommitments(items: readonly DockItem[]): DockItem[] {
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => rank(a.item) - rank(b.item) || dueAt(a.item) - dueAt(b.item) || a.i - b.i)
    .map((x) => x.item)
}

/** Dock 只钉一项：排序后的第一项 + 其余个数。空台账返回 null（Dock 不渲染）。 */
export function pinCommitment(items: readonly DockItem[]): { item: DockItem; others: number } | null {
  const sorted = sortCommitments(items)
  if (!sorted.length) return null
  return { item: sorted[0], others: sorted.length - 1 }
}

export function confirmRemainingMs(item: Extract<DockItem, { kind: 'confirm' }>, now: number): number {
  return Math.max(0, item.expiresAt - now)
}
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd mobile && npx jest test/commitment.test.ts && npm run typecheck`
Expected: PASS（6 tests）；`tsc` 0

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/presence/commitment.ts mobile/test/commitment.test.ts
git commit -m "feat(mobile): UX v2.1 B1-2 承诺项类型与稳定排序（Dock 钉一项不轮播）"
```

---

### Task 3: 在场派生（`core/presence/presence.ts`）

**Files:**
- Create: `mobile/src/core/presence/presence.ts`
- Test: `mobile/test/presence.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/presence.test.ts
// derivePresence（方案 §4.1）——多轴事实 + 单一视觉主态。
// 两组主张：
//  A. primary 的优先级固定：offline > attention > looking > listening/recognizing > speaking
//     > thinking/processing > followup > armed > idle（逐对交换即红）
//  B. **轴独立**：改 transport 不许动 commitment；这是多轴与单枚举的可测差别——
//     评审那个案例（待确认时断网，确认被盖掉）在这里有一条专门的断言
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { derivePresence, type PresenceInput } from '@/core/presence/presence'

const NOW = 5_000_000

function base(over: Partial<PresenceInput> = {}): PresenceInput {
  return {
    now: NOW,
    connStatus: 'open',
    connChangedAt: NOW - 60_000,
    hfEnabled: false,
    hfUsable: false,
    hfFsm: 'IDLE',
    ptt: 'idle',
    partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false,
    pendingOps: [],
    pendingLocation: false,
    voicePipeline: 'classic',
    visionCapturing: false,
    queued: 0,
    lastError: null,
    degradations: [],
    driving: false,
    identity: 'handheld',
    user: 'u1',
    ...over,
  }
}

describe('A. primary 优先级', () => {
  const cases: Array<[string, Partial<PresenceInput>, string]> = [
    ['idle', {}, 'idle'],
    ['armed', { hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' }, 'armed'],
    ['followup', { hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' }, 'listening'],
    ['thinking', { turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 } }, 'thinking'],
    ['processing', { turn: { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW - 1000 } }, 'thinking'],
    ['speaking', { speaking: true }, 'speaking'],
    ['listening(PTT)', { ptt: 'recording' }, 'listening'],
    ['listening(hf)', { hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING' }, 'listening'],
    ['looking', { visionCapturing: true }, 'looking'],
    ['attention', { pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }] }, 'attention'],
    ['offline', { connStatus: 'closed', connChangedAt: NOW - 30_000 }, 'muted'],
  ]
  test.each(cases)('%s → primary=%s', (_name, over, expected) => {
    expect(derivePresence(base(over)).primary).toBe(expected)
  })

  test('逐对交换：更高档在场时低档不得胜出', () => {
    // attention 与 listening 同时在场 → attention
    const both = base({
      pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }],
      ptt: 'recording',
    })
    expect(derivePresence(both).primary).toBe('attention')
    // looking 与 speaking 同时在场 → looking
    expect(derivePresence(base({ visionCapturing: true, speaking: true })).primary).toBe('looking')
    // offline 压过一切
    expect(
      derivePresence(base({ connStatus: 'closed', connChangedAt: NOW - 30_000, ptt: 'recording' })).primary,
    ).toBe('muted')
  })

  test('error 只在无在飞轮时短显 4s，之后回 idle', () => {
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 1000 } })).primary).toBe('idle')
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 1000 } })).capsule?.text).toBe('出错了')
    expect(derivePresence(base({ lastError: { text: '出错了', at: NOW - 4001 } })).capsule).toBeUndefined()
  })
})

describe('B. 轴独立', () => {
  test('评审案例：待确认时断网——Dock 仍钉着确认，胶囊说断开，光球 muted', () => {
    const snap = derivePresence(
      base({
        connStatus: 'closed',
        connChangedAt: NOW - 30_000,
        pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }],
        queued: 1,
      }),
    )
    expect(snap.primary).toBe('muted')
    expect(snap.capsule?.text).toContain('已断开')
    expect(snap.commitment.map((c) => c.kind)).toEqual(['confirm', 'queue'])
    expect(snap.transport).toBe('offline')
  })
  test('transport 变化不动 commitment（逐轴断言）', () => {
    const ops = [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }]
    const online = derivePresence(base({ pendingOps: ops }))
    const offline = derivePresence(base({ pendingOps: ops, connStatus: 'closed', connChangedAt: NOW - 30_000 }))
    expect(offline.commitment).toEqual(online.commitment)
  })
  test('reconnecting 3s 内不显示（沿用弱网横幅 3s 延迟）', () => {
    expect(derivePresence(base({ connStatus: 'connecting', connChangedAt: NOW - 1000 })).capsule).toBeUndefined()
    expect(derivePresence(base({ connStatus: 'connecting', connChangedAt: NOW - 3500 })).capsule?.text).toBe('正在重连…')
  })
  test('privacy 轴：唤醒词待机=edge；端到端录音中=cloudAudio；PTT=edge（三段式只上传文字）', () => {
    expect(derivePresence(base({ hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' })).privacy.mic).toBe('edge')
    expect(
      derivePresence(base({ hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING', voicePipeline: 's2s' })).privacy.mic,
    ).toBe('cloudAudio')
    expect(derivePresence(base({ ptt: 'recording' })).privacy.mic).toBe('edge')
    expect(derivePresence(base()).privacy.mic).toBe('off')
    expect(derivePresence(base({ visionCapturing: true })).privacy.camera).toBe('singleFrame')
  })
})

describe('commitment 构造', () => {
  test('pendingOps → confirm 项：expiresAt = ts + PENDING_TTL_MS，摘要来自输入', () => {
    const snap = derivePresence(base({ pendingOps: [{ id: 'op1', ts: NOW - 1000, summary: '打开后备箱' }] }))
    expect(snap.commitment[0]).toEqual({
      kind: 'confirm',
      id: 'op1',
      summary: '打开后备箱',
      risk: 'high',
      expiresAt: NOW - 1000 + PENDING_TTL_MS,
    })
  })
  test('位置授权征询 → confirm(subkind=location)，risk=low', () => {
    const snap = derivePresence(base({ pendingLocation: true }))
    expect(snap.commitment[0]).toMatchObject({ kind: 'confirm', subkind: 'location', risk: 'low' })
    expect(snap.primary).toBe('attention')
  })
  test('process 持续 >8s 才进 task 项', () => {
    const turn = (since: number) => ({ pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: since })
    expect(derivePresence(base({ turn: turn(NOW - 5000) })).commitment).toEqual([])
    expect(derivePresence(base({ turn: turn(NOW - 9000) })).commitment[0]).toMatchObject({ kind: 'task', label: '规划路线' })
  })
  test('queued>0 → queue 项', () => {
    expect(derivePresence(base({ queued: 3 })).commitment[0]).toMatchObject({ kind: 'queue', count: 3 })
  })
})

describe('capsule 文案', () => {
  test.each([
    [{ hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' } as Partial<PresenceInput>, '说「小舟小舟」'],
    [{ ptt: 'recording' } as Partial<PresenceInput>, '在听…'],
    [{ ptt: 'finalizing' } as Partial<PresenceInput>, '识别中…'],
    [{ speaking: true } as Partial<PresenceInput>, '播报中 · 说话可打断'],
    [{ hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' } as Partial<PresenceInput>, '可以接着说'],
    [{ visionCapturing: true } as Partial<PresenceInput>, '看一眼…'],
    [{ pendingOps: [{ id: 'op1', ts: NOW, summary: 'x' }] } as Partial<PresenceInput>, '等你确认'],
  ])('%o → %s', (over, text) => {
    expect(derivePresence(base(over)).capsule?.text).toBe(text)
  })
  test('partial 在 recognizing 时进胶囊', () => {
    expect(derivePresence(base({ ptt: 'recording', partial: '附近有什么' })).capsule?.text).toBe('附近有什么')
  })
  test('processing 显示当前步骤', () => {
    const turn = { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW }
    expect(derivePresence(base({ turn })).capsule?.text).toBe('规划路线…')
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/presence.test.ts`
Expected: FAIL — `Cannot find module '@/core/presence/presence'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/core/presence/presence.ts
// 在场模型（UX v2.1 §4）：`derivePresence()` 是一个**纯函数**——输入是既有状态机各自的事实
// （SessionCore store / 免唤醒 FSM / PTT / 设置 / 播报 / 视觉 / 时钟），输出是
// `PresenceSnapshot`：六个正交轴（transport / capture / agent / commitment / privacy / degradation）
// + **唯一的视觉主态** `primary`。
//
// 不是第四台状态机：voiceLoop / PTT / 轮态一字不改，这里只回答「此刻该让用户看到什么」。
// 输出是多轴不是单枚举（外部评审 P0-1，采纳）：待确认时断网，`offline` 不许盖掉那条确认——
// Dock 读 `commitment[]`，永远不被别的轴覆盖；光球与胶囊只读 `primary` / `capsule`。
//
// 零 RN import；jest 直接跑（test/presence.test.ts）。
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { sortCommitments, type DockItem } from './commitment'

export type OrbState =
  | 'idle'
  | 'thinking'
  | 'speaking'
  | 'armed'
  | 'listening'
  | 'attention'
  | 'looking'
  | 'muted'

export type Degradation =
  | { kind: 'recoverable_error'; text: string; at: number }
  | { kind: 'transport_unknown'; messageIds: string[] }
  | { kind: 'permission_denied'; what: 'mic' | 'camera' | 'location'; text: string }
  | { kind: 'service_degraded'; text: string }
  | { kind: 'safety_blocked'; text: string }
  | { kind: 'audio_echo_degraded'; reason: string }
  | { kind: 'fatal'; text: string }

export type Identity = 'handheld' | 'mount' | 'trusted-tablet'

export interface PresenceInput {
  now: number
  connStatus: 'connecting' | 'open' | 'closed'
  /** 上次 connStatus 变化的时刻（reconnecting 3s 延迟用） */
  connChangedAt: number
  hfEnabled: boolean
  hfUsable: boolean
  hfFsm: 'IDLE' | 'ARMED' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'FOLLOWUP' | string
  ptt: 'idle' | 'recording' | 'finalizing'
  partial: string
  turn: {
    pending: boolean
    streaming: boolean
    processActive: boolean
    processLabel: string
    /** process 首帧到达时刻；0=无 */
    processSince: number
  }
  /** 播报控制器：首片音频已起播且未结束 */
  speaking: boolean
  pendingOps: Array<{ id: string; ts: number; summary: string }>
  pendingLocation: boolean
  voicePipeline: 'classic' | 's2s'
  visionCapturing: boolean
  queued: number
  lastError: { text: string; at: number } | null
  degradations: Degradation[]
  driving: boolean
  identity: Identity
  user: string
}

export interface PresenceSnapshot {
  transport: 'online' | 'reconnecting' | 'offline'
  capture: 'off' | 'armed' | 'listening' | 'recognizing' | 'looking'
  agent: 'idle' | 'thinking' | 'processing' | 'speaking' | 'followup'
  commitment: DockItem[]
  privacy: { mic: 'off' | 'edge' | 'cloudAudio'; camera: 'off' | 'singleFrame'; user: string }
  degradation: Degradation[]
  identity: Identity
  driving: boolean
  primary: OrbState
  /** reconnecting 期间光球 ×0.6 亮度（不是新态） */
  dim: boolean
  capsule?: { text: string; tone: 'neutral' | 'accent' | 'amber' | 'red'; live?: boolean }
  input: 'voice-sheet' | 'composer' | 'none'
}

/** reconnecting 胶囊延迟（沿用 ChatScreen 弱网横幅那条 3s：重连是常态，每次都弹会让真断网没人看） */
export const RECONNECTING_GRACE_MS = 3000
/** error 胶囊短显 */
export const ERROR_SHOW_MS = 4000
/** process 持续多久才算「长任务」进 Dock */
export const LONG_TASK_MS = 8000

export function derivePresence(i: PresenceInput): PresenceSnapshot {
  // ── transport ──
  const transport: PresenceSnapshot['transport'] =
    i.connStatus === 'open' ? 'online' : i.connStatus === 'connecting' ? 'reconnecting' : 'offline'
  const reconnectingShown = transport === 'reconnecting' && i.now - i.connChangedAt >= RECONNECTING_GRACE_MS

  // ── capture ──
  const hfOn = i.hfEnabled && i.hfUsable
  const hfListening = hfOn && (i.hfFsm === 'LISTENING' || i.hfFsm === 'FOLLOWUP')
  const capture: PresenceSnapshot['capture'] = i.visionCapturing
    ? 'looking'
    : i.ptt === 'recording' || (hfOn && i.hfFsm === 'LISTENING')
      ? i.partial
        ? 'recognizing'
        : 'listening'
      : i.ptt === 'finalizing'
        ? 'recognizing'
        : hfOn && (i.hfFsm === 'ARMED' || i.hfFsm === 'FOLLOWUP')
          ? 'armed'
          : 'off'

  // ── agent ──
  const agent: PresenceSnapshot['agent'] = i.speaking
    ? 'speaking'
    : i.turn.processActive
      ? 'processing'
      : i.turn.pending || i.turn.streaming || (hfOn && i.hfFsm === 'THINKING')
        ? 'thinking'
        : hfOn && i.hfFsm === 'FOLLOWUP'
          ? 'followup'
          : 'idle'

  // ── commitment ──
  const items: DockItem[] = i.pendingOps.map((op) => ({
    kind: 'confirm',
    id: op.id,
    summary: op.summary,
    risk: 'high',
    expiresAt: op.ts + PENDING_TTL_MS,
  }))
  if (i.pendingLocation) {
    items.push({
      kind: 'confirm',
      id: '__location__',
      summary: '使用当前位置',
      risk: 'low',
      expiresAt: Number.MAX_SAFE_INTEGER,
      subkind: 'location',
    })
  }
  if (i.turn.processActive && i.turn.processSince > 0 && i.now - i.turn.processSince > LONG_TASK_MS) {
    items.push({ kind: 'task', id: '__task__', label: i.turn.processLabel || '处理中', startedAt: i.turn.processSince })
  }
  if (i.queued > 0) items.push({ kind: 'queue', id: '__queue__', count: i.queued })
  const commitment = sortCommitments(items)
  const hasAttention = commitment.some((c) => c.kind === 'confirm' || c.kind === 'slot')

  // ── privacy ──
  const micActive = capture === 'listening' || capture === 'recognizing'
  const privacy: PresenceSnapshot['privacy'] = {
    mic: micActive ? (i.voicePipeline === 's2s' && hfOn ? 'cloudAudio' : 'edge') : capture === 'armed' ? 'edge' : 'off',
    camera: i.visionCapturing ? 'singleFrame' : 'off',
    user: i.user,
  }

  // ── primary（固定顺序，写进测试）──
  const errorLive = !!i.lastError && i.now - i.lastError.at < ERROR_SHOW_MS && agent === 'idle'
  let primary: OrbState
  if (transport === 'offline') primary = 'muted'
  else if (hasAttention) primary = 'attention'
  else if (capture === 'looking') primary = 'looking'
  else if (capture === 'listening' || capture === 'recognizing') primary = 'listening'
  else if (agent === 'speaking') primary = 'speaking'
  else if (agent === 'thinking' || agent === 'processing') primary = 'thinking'
  else if (agent === 'followup') primary = 'listening'
  else if (capture === 'armed') primary = 'armed'
  else primary = 'idle'

  // ── capsule（一次一条；胶囊说「此刻」，Dock 说「欠着」）──
  let capsule: PresenceSnapshot['capsule']
  if (transport === 'offline') capsule = { text: '已断开 · 消息会排队', tone: 'red' }
  else if (reconnectingShown) capsule = { text: '正在重连…', tone: 'amber' }
  else if (hasAttention) {
    const first = commitment.find((c) => c.kind === 'confirm' || c.kind === 'slot')
    capsule = { text: first?.kind === 'slot' ? '还差一个信息' : '等你确认', tone: 'amber' }
  } else if (capture === 'looking') capsule = { text: '看一眼…', tone: 'accent' }
  else if (capture === 'recognizing') capsule = { text: i.partial || '识别中…', tone: 'accent', live: true }
  else if (capture === 'listening') capsule = { text: '在听…', tone: 'accent', live: true }
  else if (agent === 'speaking') capsule = { text: '播报中 · 说话可打断', tone: 'accent' }
  else if (agent === 'processing') capsule = { text: `${i.turn.processLabel || '处理中'}…`, tone: 'neutral' }
  else if (agent === 'thinking') capsule = { text: '正在思考…', tone: 'neutral' }
  else if (agent === 'followup') capsule = { text: '可以接着说', tone: 'accent', live: true }
  else if (capture === 'armed') capsule = { text: '说「小舟小舟」', tone: 'neutral' }
  else if (errorLive) capsule = { text: i.lastError!.text, tone: 'red' }

  const input: PresenceSnapshot['input'] =
    capture === 'listening' || capture === 'recognizing' ? 'voice-sheet' : 'composer'

  return {
    transport,
    capture,
    agent,
    commitment,
    privacy,
    degradation: i.degradations,
    identity: i.identity,
    driving: i.driving,
    primary,
    dim: transport === 'reconnecting',
    ...(capsule ? { capsule } : {}),
    input,
  }
}
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd mobile && npx jest test/presence.test.ts && npm run typecheck`
Expected: PASS（≈24 tests）；`tsc` 0

- [ ] **Step 5: 反向验证（不提交这一步的改动）**

把 `primary` 判断里的 `else if (hasAttention)` 与 `else if (capture === 'looking')` 两行**互换顺序**，跑 `npx jest test/presence.test.ts` —— 期望「逐对交换」那条红（attention 与 listening 同时在场应为 attention）。还原后再跑一次绿。这一步是方案 §4.1「反向验证」的落实，做完写进提交信息。

- [ ] **Step 6: 提交**

```bash
git add -- mobile/src/core/presence/presence.ts mobile/test/presence.test.ts
git commit -m "feat(mobile): UX v2.1 B1-3 derivePresence——六轴事实 + 唯一视觉主态（反向验证：交换 attention/looking 顺序即红）"
```

---

### Task 4: SessionCore——精确剪枝、到期留痕、离线队列计数、发送状态未知

**Files:**
- Modify: `mobile/src/core/session/store.ts`（`SessionState`、`dispatch`、`setStatus`、`syncPruneTimer`、`handleFrame` final 分支）
- Test: `mobile/test/sessionStore.test.ts`（追加一个 describe）

- [ ] **Step 1: 写失败测试（追加到 `mobile/test/sessionStore.test.ts` 末尾）**

```ts
describe('UX v2.1 B1-4：承诺面的账本侧', () => {
  test('剪枝按项到期精确调度：到期那一秒出账，并在记录里留「确认已过期」', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op1' })
    expect(core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op1'])
    // 共享 TTL 300s：到 299s 还在
    jest.advanceTimersByTime(299_000)
    expect(core.store.getState().pendingOps).toHaveLength(1)
    // 300s 整出账（v1 是固定 30s 轮询，最坏晚 30s；现在按项到期调度）
    jest.advanceTimersByTime(1_100)
    expect(core.store.getState().pendingOps).toHaveLength(0)
    const last = msgs(core)[msgs(core).length - 1]
    expect(last.role).toBe('assistant')
    expect(last.text).toContain('确认已过期')
    expect(last.text).toContain('打开后备箱') // 摘要来自原气泡
    core.dispose()
  })

  test('服务端 closed 出账不留「过期」痕（那是被处理了，不是过期）', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op1' })
    core.send('算了')
    const rid2 = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid2, speech: '好的', closed_operation_ids: ['op1'] })
    expect(msgs(core).some((m) => m.text.includes('确认已过期'))).toBe(false)
    core.dispose()
  })

  test('离线入队计数：transport.send 返回 false 累加，连上归零', () => {
    const transport = new FakeTransport()
    transport.send = (frame: object) => {
      transport.sent.push(frame)
      return false // 断线：入队
    }
    const core = new SessionCore({
      transport,
      sessionId: 'app-test01',
      getMeta: () => ({}),
      location: fakeLocation(false),
    })
    core.setStatus('closed')
    core.send('现在几点')
    core.send('讲个笑话')
    expect(core.store.getState().queued).toBe(2)
    core.setStatus('open') // ws.mjs onopen 时 flush 队列 → 计数归零
    expect(core.store.getState().queued).toBe(0)
    core.dispose()
  })

  test('探活判死时在飞轮标「发送状态未知」，收到终态帧即清', () => {
    const { transport, core } = newCore()
    core.setStatus('open')
    core.send('现在几点')
    const rid = transport.lastUserFrame().request_id
    const pendingId = assistants(core)[0].id
    core.setStatus('closed') // liveness.onDead → reconnectNow → onStatus('closed')
    expect(core.store.getState().uncertainIds).toEqual([pendingId])
    core.setStatus('open')
    core.handleFrame({ type: 'final', request_id: rid, speech: '八点' })
    expect(core.store.getState().uncertainIds).toEqual([])
    core.dispose()
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/sessionStore.test.ts -t "B1-4"`
Expected: FAIL — 第 1 条在 300s 后仍有 1 条（旧 30s 轮询恰好在 300s 时也会剪……**注意**：旧实现 `setInterval(30s)` 在 300s 整那一跳也会剪掉，所以第 1 条的「到期」断言可能碰巧绿——红的是「确认已过期」文案断言）；第 3/4 条 `queued` / `uncertainIds` 为 `undefined`。

- [ ] **Step 3: 改实现**

`mobile/src/core/session/store.ts`：

(a) `SessionState` 加两个字段（在 `lastEmotion` 之后）：

```ts
  /** 断线期间入队（transport.send 返回 false）的上行帧数；连上即归零（ws.mjs onopen 会 flush） */
  queued: number
  /** 探活判死那一刻仍在飞的助手气泡 id——它们的请求可能写进了死 socket（M3-W 残留窗），
   *  UI 标「发送状态未知」；终态帧到达或超时即清。**不自动重发**（重发同一 request_id
   *  意味着车控可能执行两次，M3-W 定案） */
  uncertainIds: string[]
```

初始值：`queued: 0, uncertainIds: []`。

(b) `PRUNE_INTERVAL_MS` 注释改为「上界」并加导入：

```ts
import { PENDING_TTL_MS, closePendings, openPending, prunePendings } from '@shared/pendingOps.mjs'
// 剪枝调度上界：按项到期精确调度，min(下一条到期, 30s)。30s 是没有任何挂起时的兜底轮询上界，
// 不再是「最坏晚 30s 才出账」——那正是 P5「按钮无解释消失」的一半成因
const PRUNE_INTERVAL_MS = 30_000
```

(c) `setStatus` 改为：

```ts
  setStatus(status: GatewayStatus): void {
    const prev = this.store.getState().connStatus
    if (status === prev) return
    if (status === 'closed' && prev === 'open') {
      // 探活判死 / onclose：此刻在飞的轮可能写进了死 socket（M3-W 残留窗）——标未知，不重发
      const inFlight = this.registry.bubbleIds()
      this.store.setState({ connStatus: status, uncertainIds: inFlight })
      return
    }
    if (status === 'open') {
      // onopen 时 ws.mjs 已 flush 队列
      this.store.setState({ connStatus: status, queued: 0 })
      return
    }
    this.store.setState({ connStatus: status })
  }
```

⚠ `RequestRegistry` 是共享模块（`hmi/src/requestRouting.mjs`），**不能加方法**。在 SessionCore 里自己维护在飞 id 集合：`private readonly inFlight = new Set<string>()`，`dispatch` 里 `open` 后 `add(pendingId)`，`clearWatchdog`（所有终态路径都经它）里 `delete`。于是上面的 `this.registry.bubbleIds()` 改成 `[...this.inFlight]`。

(d) `dispatch` 里 `this.deps.transport.send(frame)` 改为：

```ts
    const sentNow = this.deps.transport.send(frame)
    if (!sentNow) this.store.setState((s) => ({ queued: s.queued + 1 }))
```

`cancelCurrentTurn` / `proactive_ack` 的 `send` 不计数（它们不是用户消息）。

(e) `clearWatchdog` 末尾追加：

```ts
    this.inFlight.delete(bubbleId)
    const s = this.store.getState()
    if (s.uncertainIds.includes(bubbleId)) {
      this.store.setState({ uncertainIds: s.uncertainIds.filter((x) => x !== bubbleId) })
    }
```

`armWatchdog` 的超时回调里在 `this.registry.dropBubble(id)` 之后同样执行 `this.inFlight.delete(id)` 与 `uncertainIds` 过滤（超时也是终态）。

(f) `syncPruneTimer` 整段替换：

```ts
  /** 本地限龄（App.tsx:630-640 的语义 + B1 的精确调度）：后端挂起 TTL 到点就没了，前端跟着老化。
   *  v1 固定 30s 轮询，最坏晚 30s 出账、且**静默消失**；现在按「下一条到期时刻」调度，
   *  到期出账时在记录里留一行「确认已过期」（P5：承诺消失必有理由）。 */
  private syncPruneTimer(): void {
    if (this.pruneTimer) {
      clearTimeout(this.pruneTimer)
      this.pruneTimer = null
    }
    const ops = this.store.getState().pendingOps
    if (!ops.length) return
    const now = Date.now()
    const nextExpiry = Math.min(...ops.map((o) => o.ts + PENDING_TTL_MS))
    const delay = Math.max(0, Math.min(nextExpiry - now, PRUNE_INTERVAL_MS))
    this.pruneTimer = setTimeout(() => {
      this.pruneTimer = null
      const before = this.store.getState().pendingOps
      const after = prunePendings(before)
      if (after.length !== before.length) {
        const expired = before.filter((o) => !after.some((a) => a.id === o.id))
        this.store.setState({ pendingOps: after })
        for (const op of expired) this.noteExpired(op.id)
      }
      this.syncPruneTimer()
    }, delay)
  }

  /** 到期留痕：找到带这条 operationId 的助手气泡，取其原话做摘要，追加一条说明 */
  private noteExpired(operationId: string): void {
    const src = this.store.getState().messages.find((m) => m.operationId === operationId)
    const summary = (src?.text || '').replace(/\s+/g, ' ').slice(0, 24)
    this.appendMessage({
      id: uid(),
      role: 'assistant',
      text: summary ? `⏱ 「${summary}」的确认已过期，需要的话再说一次` : '⏱ 刚才那条确认已过期，需要的话再说一次',
    })
  }
```

`pruneTimer` 的类型从 `ReturnType<typeof setInterval>` 改为 `ReturnType<typeof setTimeout>`；`dispose()` 里 `clearInterval` 改 `clearTimeout`。`handleFrame` 的 final 分支里已有 `closePendings(prunePendings(...))` 后调 `syncPruneTimer()`，不用改；`confirmReply` 同。

(g) 摘要来源：`noteExpired` 用 `msg.operationId` 找气泡——`final` 分支已把 `operationId` 写进气泡（`store.ts:366`），无需改。

- [ ] **Step 4: 跑测试确认绿 + 全量**

Run: `cd mobile && npx jest test/sessionStore.test.ts && npm test && npm run typecheck`
Expected: 新增 4 条 PASS；全量 234 + 4 + 前面任务新增（tokens 7 / commitment 6 / presence ≈24）全绿；`tsc` 0

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts
git commit -m "feat(mobile): UX v2.1 B1-4 账本侧——剪枝按项到期精确调度 + 到期留痕 + 离线入队计数 + 发送状态未知"
```

---

### Task 5: 四路结构化信号（播报 / 视觉 / 回声 / 权限）

**Files:**
- Modify: `mobile/src/core/voice/speech.ts`（`subscribeSpeaking`）
- Modify: `mobile/src/core/vision/frame.ts`（`subscribeVisionCapturing`）
- Modify: `mobile/src/core/voice/handsFree.ts`（deps `onBargeInDisabled` / `onPipelineDegraded`）
- Modify: `mobile/src/features/chat/useHandsFree.ts`（透出 `bargeInDisabled` / `pipelineDegraded`）
- Modify: `mobile/src/features/chat/usePtt.ts`（`errorKind`）
- Test: `mobile/test/presenceSignals.test.ts`（新建）

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/presenceSignals.test.ts
// B1-5：Presence 的四路输入信号必须是**可订阅的结构化事实**，不是回调链上的字符串。
// 此前 `speechController` 的 onSpeechBegan/Ended 只能被一个消费方链式覆盖（useHandsFree 就是
// 这么接的），第二个消费方要么覆盖掉第一个、要么复制那套「存 prev 再链回去」的脆弱写法。
import { registerVisionCapturer, subscribeVisionCapturing, captureVisionFrame } from '@/core/vision/frame'
import { SpeechController } from '@/core/voice/speech'

// 播放器与 TTS 会话在 jest 里不存在：只订阅状态，不真播
jest.mock('@/core/voice/audioCtx', () => ({ newPcmPlayer: jest.fn(() => ({ push() {}, remainingSec: () => 0, stop() {} })) }))

describe('speech.subscribeSpeaking', () => {
  test('首片音频 → true；stop → false；退订后不再收', () => {
    const sc = new SpeechController('https://x')
    const seen: boolean[] = []
    const off = sc.subscribeSpeaking((v) => seen.push(v))
    ;(sc as unknown as { setSpeaking(v: boolean): void }).setSpeaking(true)
    sc.stop()
    off()
    ;(sc as unknown as { setSpeaking(v: boolean): void }).setSpeaking(true)
    expect(seen).toEqual([true, false])
    expect(sc.speaking).toBe(true)
  })
})

describe('vision.subscribeVisionCapturing', () => {
  test('抓帧期间 true，结束（含失败）后 false', async () => {
    const seen: boolean[] = []
    const off = subscribeVisionCapturing((v) => seen.push(v))
    let release: () => void = () => {}
    registerVisionCapturer(() => new Promise<string>((r) => (release = () => r(''))))
    const p = captureVisionFrame('https://audio')
    await Promise.resolve()
    expect(seen).toEqual([true])
    release()
    await p
    expect(seen).toEqual([true, false])
    off()
    registerVisionCapturer(null)
  })
})

```

回声回调那一条**不放在这个文件**——`HandsFreeController` 构造时会 `new VadEngine/KwsEngine/micLease`，只有 `test/handsFree.test.ts` 那套 `jest.doMock` + `jest.resetModules` + `require` 的接线替身能构造它。追加到 `test/handsFree.test.ts` 末尾（复用其 `makeCtl`）：

```ts
test('⑤ FSM 关闭 barge-in → onBargeInDisabled(reason) 与 onNotice 同时收到（UX v2.1 B1-5）', () => {
  const bargeIn: string[] = []
  const notices: string[] = []
  const { ctl } = makeCtl({
    onNotice: (m: string) => notices.push(m),
    onBargeInDisabled: (r: string) => bargeIn.push(r),
  })
  // 直接触发 FSM 的回调（VoiceLoop 构造时由控制器注入 onDisableBargeIn），不跑引擎
  ctl.vl.onDisableBargeIn('repeated-self-trigger')
  expect(bargeIn).toEqual(['repeated-self-trigger'])
  expect(notices[0]).toContain('语音打断')
})
```

（`makeCtl` 返回的 `ctl` 在那个文件里是 `any`，可直接取 `vl`。）

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/presenceSignals.test.ts`
Expected: FAIL — `subscribeSpeaking is not a function` / `subscribeVisionCapturing` 未导出 / `onBargeInDisabled` 未调用

- [ ] **Step 3: 改实现**

(a) `mobile/src/core/voice/speech.ts`——在 `SpeechController` 类里加：

```ts
  /** 播报中（首片音频起播 → 播完/停）。Presence 的 agent 轴读它；**可多订阅**，
   *  不再要求消费方链式覆盖 onSpeechBegan/Ended（那套写法第二个消费方就会把第一个顶掉）。 */
  speaking = false
  private readonly speakingSubs = new Set<(v: boolean) => void>()

  subscribeSpeaking(fn: (v: boolean) => void): () => void {
    this.speakingSubs.add(fn)
    return () => {
      this.speakingSubs.delete(fn)
    }
  }

  private setSpeaking(v: boolean): void {
    if (this.speaking === v) return
    this.speaking = v
    for (const fn of this.speakingSubs) fn(v)
  }
```

挂点：`begin()` 里 `onFirstAudio` 回调内 `this.setSpeaking(true)`（放在 `this.onSpeechBegan?.(...)` 之前）；`onEnd` 回调内 `this.setSpeaking(false)`；`stop()` 开头 `this.setSpeaking(false)`；`speakBatch()` 在 `this.extra = { player }` 之后 `this.setSpeaking(true)`、`await done` 之后 `this.setSpeaking(false)`；`preview()` 的 `onFirstAudio` 里 `this.setSpeaking(true)`、`await session.completion` 后 `this.setSpeaking(false)`。

(b) `mobile/src/core/vision/frame.ts`——加：

```ts
let capturing = false
const capturingSubs = new Set<(v: boolean) => void>()

/** 抓帧进行中（挂载相机 → 拍完卸载）。Presence 的 `looking` 态与隐私栏读它。 */
export function subscribeVisionCapturing(fn: (v: boolean) => void): () => void {
  capturingSubs.add(fn)
  return () => {
    capturingSubs.delete(fn)
  }
}

function setCapturing(v: boolean): void {
  if (capturing === v) return
  capturing = v
  for (const fn of capturingSubs) fn(v)
}

export function isVisionCapturing(): boolean {
  return capturing
}
```

`captureVisionFrame` 改为：

```ts
export async function captureVisionFrame(audioUrl: string): Promise<string> {
  const fn = capturer
  if (!fn || !audioUrl) return ''
  setCapturing(true)
  try {
    const uri = await fn()
    if (!uri) return ''
    const blob = await (await fetch(uri)).blob()
    const r = await fetch(`${audioUrl}/api/vision/frame?mime=image/jpeg`, {
      method: 'POST',
      body: blob,
      headers: { 'Content-Type': 'image/jpeg' },
    })
    if (!r.ok) return ''
    const j = (await r.json()) as { frame_id?: string }
    return j.frame_id || ''
  } catch {
    return ''
  } finally {
    setCapturing(false)
  }
}
```

(c) `mobile/src/core/voice/handsFree.ts`——`HandsFreeDeps` 加两条（在 `onNotice` 之后）：

```ts
  /** 结构化降级信号（UX v2.1 §12.1）。与 onNotice 并行：notice 是人话，这两条是给 Presence 的事实 */
  onBargeInDisabled?(reason: string): void
  onPipelineDegraded?(kind: 'degraded' | 'unsupported', message: string): void
```

`onDisableBargeIn` 改为：

```ts
      onDisableBargeIn: (reason: string) => {
        this.deps.onNotice?.('已关闭本次会话的语音打断（' + reason + '）')
        this.deps.onBargeInDisabled?.(reason)
      },
```

`startS2s` 里 `onSessionState` 与 `onUnsupported` 各加一行：`this.deps.onPipelineDegraded?.('degraded', '语音链路降级，本轮回落三段式')` / `this.deps.onPipelineDegraded?.('unsupported', msg)`。

(d) `mobile/src/features/chat/useHandsFree.ts`——`HandsFreeUi` 加：

```ts
  /** 会话级关闭了语音打断（回声连续自触发）；空串=未关。会话级不可恢复（voiceLoop 语义） */
  bargeInDisabled: string
  /** 本轮语音链路降级说明；下一轮 FSM 进 LISTENING 时清 */
  pipelineDegraded: string
```

hook 内加 `const [bargeInDisabled, setBargeInDisabled] = useState('')`、`const [pipelineDegraded, setPipelineDegraded] = useState('')`；deps 里加 `onBargeInDisabled: (r) => setBargeInDisabled(r)`、`onPipelineDegraded: (_k, m) => setPipelineDegraded(m)`；`onOrbState` 里 `if (f === 'LISTENING') setPipelineDegraded('')`；effect 清理里两者置空；返回对象加两字段。

(e) `mobile/src/features/chat/usePtt.ts`——`PttHandle` 加 `errorKind: 'permission' | 'start' | 'asr' | ''`；`setError` 的三处分别配 `setErrorKind('asr')`（`onError` / 没听清）、`'permission'`（`PermissionDeniedError`）、`'start'`（其它启动失败）；`pressDown` 开头 `setErrorKind('')`；返回对象加 `errorKind`。

- [ ] **Step 4: 跑测试确认绿 + 全量**

Run: `cd mobile && npx jest test/presenceSignals.test.ts test/handsFree.test.ts && npm test && npm run typecheck`
Expected: `presenceSignals` 2 PASS + `handsFree` 新增第 ⑤ 条 PASS；全量绿（既有用例不受影响）；`tsc` 0

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/voice/speech.ts mobile/src/core/vision/frame.ts mobile/src/core/voice/handsFree.ts mobile/src/features/chat/useHandsFree.ts mobile/src/features/chat/usePtt.ts mobile/test/presenceSignals.test.ts mobile/test/handsFree.test.ts
git commit -m "feat(mobile): UX v2.1 B1-5 四路结构化信号——播报/抓帧可订阅、回声与链路降级回调、PTT 错误分类"
```

---

### Task 6: 光球三新态（`AuroraOrb.tsx`）

**Files:**
- Modify: `mobile/src/ui/aurora/AuroraOrb.tsx`
- Test: 类型层由 `tsc` 守；视觉由 Task 13 的状态画廊真机截图守（本任务无 jest——光球是 Reanimated 组件，仓库里没有组件渲染测试，不为这一个开先例）

- [ ] **Step 1: 改类型与状态判定**

**`OrbState` 只许有一份定义**：Task 3 已在 `core/presence/presence.ts` 导出它（含三个新态）。把 `AuroraOrb.tsx:23` 的本地 `export type OrbState = …` **删掉**，改为：

```ts
import type { OrbState } from '../../core/presence/presence'
export type { OrbState }
```

（`ui/aurora/index.ts` 的 `export { AuroraOrb, type OrbState } from './AuroraOrb'` 不用改。依赖方向 UI → core，presence.ts 零 RN import，不会把 reanimated 拖进 jest。）⇒ **Task 6 依赖 Task 3**（§3 依赖图已改）。

`AuroraOrb` 的 props 加 `dim?: boolean`（reconnecting 期 ×0.6 亮度）。函数体开头在 `const armed = ...` 之后加：

```ts
  const attention = state === 'attention'
  const looking = state === 'looking'
  const muted = state === 'muted'
  // 三个新态只加环与节律，不碰七层、四色、波纹色（方案 §10.1 十条不变量）
  const spins = animated && !muted
```

`glow` 行改为 `const glow = speaking ? 1.35 : listening ? 1.15 : armed ? 0.8 : muted ? 0.6 : 1`。
`useEffect` 里 `if (!animated) return` 改为 `if (!spins) return`（muted 停旋转但呼吸也停：muted 时整段 return 即可）；依赖数组把 `animated` 换成 `spins`。

- [ ] **Step 2: 外层 View 加去饱和与调暗**

最外层 `<View style={{ width: size, height: size }} accessibilityLabel="小舟">` 改为：

```tsx
    <View
      style={{
        width: size,
        height: size,
        opacity: dim ? 0.6 : 1,
        // RN 0.86 Android 支持 filter.saturate；iOS 不支持时静默忽略（本 App 只交付 Android）
        ...(muted ? { filter: [{ saturate: 0.4 }] } : {}),
      }}
      accessibilityLabel={ORB_A11Y[state]}
      accessibilityRole="image"
    >
```

文件顶部加：

```ts
/** TalkBack 读法随状态变（§8.1）；Composer 上的可点光球再包一层 button role */
export const ORB_A11Y: Record<OrbState, string> = {
  idle: '小舟',
  thinking: '小舟，正在思考',
  speaking: '小舟，播报中',
  armed: '小舟，等待唤醒',
  listening: '小舟，在听',
  attention: '小舟，等你确认',
  looking: '小舟，正在看一眼',
  muted: '小舟，已断开',
}
```

- [ ] **Step 3: 加三种环**

把「聆听/待机态：单圈交互蓝聆听环」那段 `{(listening || armed) && (...)}` 之后追加：

```tsx
      {/* 等你确认：琥珀环（琥珀本就是确认态语义色，A-6.4），呼吸 3s；不改球体 */}
      {attention && (
        <Animated.View
          pointerEvents="none"
          style={[
            layer,
            {
              top: -size * 0.14, left: -size * 0.14, right: -size * 0.14, bottom: -size * 0.14,
              borderWidth: 2,
              borderColor: 'rgba(245,158,11,0.35)',
            },
            animated ? bodyStyle : null,
          ]}
        />
      )}
      {/* 看一眼：一次性白环扩散（与 speaking 的三青环区分：一次 vs 连续） */}
      {animated && looking && <Shutter size={size} />}
      {/* 离线：灰环，静止 */}
      {muted && (
        <View
          pointerEvents="none"
          style={[
            layer,
            {
              top: -size * 0.1, left: -size * 0.1, right: -size * 0.1, bottom: -size * 0.1,
              borderWidth: 1,
              borderColor: 'rgba(255,255,255,0.22)',
            },
          ]}
        />
      )}
```

文件末尾加 `Shutter`：

```tsx
/** looking 快门：白环 0.9→1.35 扩散并淡出，**只放一次**（300ms），之后停在不可见 */
function Shutter({ size }: { size: number }) {
  const t = useSharedValue(0)
  useEffect(() => {
    t.value = 0
    t.value = withTiming(1, { duration: 300, easing: Easing.out(Easing.ease) })
    return () => cancelAnimation(t)
  }, [t])
  const style = useAnimatedStyle(() => ({
    transform: [{ scale: 0.9 + 0.45 * t.value }],
    opacity: 0.9 * (1 - t.value),
  }))
  return (
    <Animated.View
      pointerEvents="none"
      style={[
        {
          position: 'absolute',
          top: -size * 0.1, left: -size * 0.1, right: -size * 0.1, bottom: -size * 0.1,
          borderRadius: 9999,
          borderWidth: 1.5,
          borderColor: 'rgba(255,255,255,0.8)',
        },
        style,
      ]}
    />
  )
}
```

`attention` 态下 `bodyDur` 取 idle 的 4s（`bodyDur` 表达式不用改：attention 不命中 thinking/speaking/listening/armed，落到 4）。

- [ ] **Step 4: 类型检查 + 既有测试**

Run: `cd mobile && npm run typecheck && npm test`
Expected: `tsc` 0（`OrbState` 扩展是纯增量，`Composer.tsx:39` 等既有用法不受影响）；全量绿

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/ui/aurora/AuroraOrb.tsx
git commit -m "feat(mobile): UX v2.1 B1-6 光球三新态 attention/looking/muted——只加环与节律，七层四色不动"
```

---

### Task 7: 设置开关（回滚路径）与身份占位

**Files:**
- Modify: `mobile/src/core/settings/store.ts`
- Modify: `mobile/src/features/settings/SettingsScreen.tsx`（新「实验室」分区，放在「调试」之前）
- Test: `mobile/test/settingsMeta.test.ts`（追加）

- [ ] **Step 1: 写失败测试（追加到 `settingsMeta.test.ts` 末尾）**

```ts
describe('UX v2.1 开关与身份（B1-7）', () => {
  test('缺省：两个 v2 开关开、身份=手持', () => {
    expect(DEFAULT_APP_SETTINGS.uxV2Presence).toBe(true)
    expect(DEFAULT_APP_SETTINGS.uxV2Dock).toBe(true)
    expect(DEFAULT_APP_SETTINGS.deviceRole).toBe('handheld')
  })
  test('存量设置没有这三个键 → 合并后取缺省（向前兼容）', () => {
    const merged = mergeStoredSettings(JSON.stringify({ theme: 'dark' }))
    expect(merged.uxV2Presence).toBe(true)
    expect(merged.deviceRole).toBe('handheld')
  })
  test('三个键**不上行**（buildMeta 键集不变）', () => {
    expect(Object.keys(buildMeta(DEFAULT_APP_SETTINGS)).sort()).toEqual([...HMI_META_KEYS].sort())
  })
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/settingsMeta.test.ts -t "B1-7"`
Expected: FAIL — `uxV2Presence` 为 `undefined`

- [ ] **Step 3: 改实现**

`mobile/src/core/settings/store.ts`：`AppSettings` 末尾加

```ts
  // ── UX v2.1（B1）──
  /** 在场模型 + 光球主态 + 状态胶囊。关掉即回 v1 的四条状态条（回滚路径，§11.5） */
  uxV2Presence: boolean
  /** Focus Dock 承诺面。关掉即回气泡内确认按钮 */
  uxV2Dock: boolean
  /** 产品身份（方案 §6.0）。B1 只占位，B4 才有 UI 与行为差异；**窗口尺寸不决定它** */
  deviceRole: 'handheld' | 'mount' | 'trusted-tablet'
```

`DEFAULT_APP_SETTINGS` 末尾加 `uxV2Presence: true, uxV2Dock: true, deviceRole: 'handheld',`。

`mobile/src/features/settings/SettingsScreen.tsx`：在 `<Section p={p} title="调试">` 之前插入：

```tsx
      {/* UX v2.1 实验室：两个开关是**回滚路径**（§11.5）——纯 JS 批次出问题关开关，不重装。
          v1 的状态条与气泡内确认在 B1/B2 期间不删。 */}
      <Section p={p} title="实验室（UX v2.1）">
        <SwitchRow
          p={p}
          label="光球状态锚 + 状态胶囊"
          desc="关闭后回到 v1：免唤醒状态条、弱网横幅、通知条分开显示"
          value={settings.uxV2Presence}
          onChange={(uxV2Presence) => set({ uxV2Presence })}
        />
        <SwitchRow
          p={p}
          label="承诺面 Focus Dock"
          desc="确认 / 长任务 / 离线队列钉在输入区上方；关闭后回到气泡内确认按钮"
          value={settings.uxV2Dock}
          onChange={(uxV2Dock) => set({ uxV2Dock })}
        />
        <Link href="/state-gallery" style={{ color: p.accent, fontSize: p.font(14) }}>
          状态画廊（在场 13 态 + 7 种降级 / 主题过检）
        </Link>
      </Section>
```

- [ ] **Step 4: 跑测试确认绿**

Run: `cd mobile && npx jest test/settingsMeta.test.ts && npm run typecheck`
Expected: PASS；`tsc` 0（`/state-gallery` 路由在 Task 14 注册前 `Link` 的 href 类型由 expo-router typed routes 生成——若 `tsc` 报 href 不合法，先在 `app/_layout.tsx` 注册 `<Stack.Screen name="state-gallery" options={{ title: '调试 · 状态画廊' }} />` 并建一个空的 `app/state-gallery.tsx` 占位：`export default function StateGallery() { return null }`，Task 14 再填。）

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/core/settings/store.ts mobile/src/features/settings/SettingsScreen.tsx mobile/test/settingsMeta.test.ts mobile/src/app/_layout.tsx mobile/src/app/state-gallery.tsx
git commit -m "feat(mobile): UX v2.1 B1-7 实验室开关 uxV2Presence/uxV2Dock + deviceRole 占位"
```

---

### Task 8: 采集激活日志 + `usePresence` 收集器

**Files:**
- Create: `mobile/src/core/presence/activityLog.ts`
- Create: `mobile/src/features/chat/usePresence.ts`
- Test: `mobile/test/activityLog.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/activityLog.test.ts
// 隐私栏「最近一次激活」的数据源（方案 §5.10）：20 条内存环形日志，**不上传、不持久化**。
import { ActivityLog } from '@/core/presence/activityLog'

test('环形 20 条，最新在前', () => {
  const log = new ActivityLog(20, () => 1000)
  for (let i = 0; i < 25; i += 1) log.push('mic', `唤醒词命中 ${i}`)
  const items = log.list()
  expect(items).toHaveLength(20)
  expect(items[0].note).toBe('唤醒词命中 24')
  expect(items[19].note).toBe('唤醒词命中 5')
})

test('lastOf 取该来源最近一条；没有返回 null', () => {
  let t = 0
  const log = new ActivityLog(20, () => (t += 1000))
  log.push('mic', '按住说话')
  log.push('camera', '触发词「这是什么」')
  log.push('mic', '唤醒词命中')
  expect(log.lastOf('mic')).toMatchObject({ note: '唤醒词命中', at: 3000 })
  expect(log.lastOf('camera')).toMatchObject({ note: '触发词「这是什么」' })
  expect(log.lastOf('location')).toBeNull()
})

test('订阅：每次 push 通知一次；退订后不再收', () => {
  const log = new ActivityLog(5, () => 0)
  let n = 0
  const off = log.subscribe(() => (n += 1))
  log.push('mic', 'a')
  log.push('mic', 'b')
  off()
  log.push('mic', 'c')
  expect(n).toBe(2)
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/activityLog.test.ts`
Expected: FAIL — `Cannot find module '@/core/presence/activityLog'`

- [ ] **Step 3: 写实现**

```ts
// mobile/src/core/presence/activityLog.ts
// 采集激活环形日志（方案 §5.10 隐私栏「最近一次激活」）。**内存、20 条、不上传、不持久化**：
// 它回答的是「刚才麦克风/摄像头为什么开了」，不是审计——审计在服务端账本。
export type ActivitySource = 'mic' | 'camera' | 'location'

export interface ActivityEntry {
  source: ActivitySource
  note: string
  at: number
}

export class ActivityLog {
  private items: ActivityEntry[] = []
  private readonly subs = new Set<() => void>()

  constructor(
    private readonly capacity = 20,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  push(source: ActivitySource, note: string): void {
    this.items = [{ source, note, at: this.clock() }, ...this.items].slice(0, this.capacity)
    for (const fn of this.subs) fn()
  }

  /** 最新在前 */
  list(): ActivityEntry[] {
    return this.items.slice()
  }

  lastOf(source: ActivitySource): ActivityEntry | null {
    return this.items.find((e) => e.source === source) ?? null
  }

  subscribe(fn: () => void): () => void {
    this.subs.add(fn)
    return () => {
      this.subs.delete(fn)
    }
  }
}

/** App 级单例（隐私栏读；PTT / 免唤醒 / 视觉在激活处写） */
export const activityLog = new ActivityLog()
```

- [ ] **Step 4: 写 `usePresence`（无单测：它只是把既有 hook 的输出喂给纯函数；纯函数在 Task 3 已守）**

```ts
// mobile/src/features/chat/usePresence.ts
// 在场收集器：把既有状态机的事实收齐 → derivePresence（纯函数，Task 3）。
// 这里**不做任何判断**——所有「谁压过谁」都在 presence.ts 里且有测试；这里只负责订阅与计时。
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from 'zustand'

import type { SessionCore } from '@/core/session/store'
import { settingsStore } from '@/core/settings/store'
import { speechController } from '@/core/voice/speech'
import { isVisionCapturing, subscribeVisionCapturing } from '@/core/vision/frame'
import { derivePresence, type Degradation, type PresenceSnapshot } from '@/core/presence/presence'

import type { HandsFreeUi } from './useHandsFree'
import type { PttHandle } from './usePtt'

export interface UsePresenceOpts {
  core: SessionCore
  hf: HandsFreeUi
  ptt: PttHandle | null
  /** token 对应的 user_id（隐私栏「当前：xx」）；ServerConfig 里没有就显示 token 尾 4 位 */
  user: string
}

/** 只在这些秒级量变化时才需要重算：倒计时 / 3s 延迟 / 4s error / 8s 长任务 */
const TICK_MS = 1000

export function usePresence({ core, hf, ptt, user }: UsePresenceOpts): PresenceSnapshot {
  const { messages, pendingOps, connStatus, pendingLocationText, queued, uncertainIds } = useStore(core.store)
  const { settings } = useStore(settingsStore)

  // 播报中 / 抓帧中：订阅式信号（Task 5）
  const [speaking, setSpeaking] = useState(() => speechController().speaking)
  const [visionCapturing, setVisionCapturing] = useState(isVisionCapturing)
  useEffect(() => speechController().subscribeSpeaking(setSpeaking), [])
  useEffect(() => subscribeVisionCapturing(setVisionCapturing), [])

  // connStatus 变化时刻（reconnecting 3s 延迟的基准）
  const connChangedAt = useRef(Date.now())
  const prevConn = useRef(connStatus)
  if (prevConn.current !== connStatus) {
    prevConn.current = connStatus
    connChangedAt.current = Date.now()
  }

  // 最近一条错误（4s 短显）：取最后一条 error 气泡的出现时刻
  const lastError = useMemo(() => {
    const m = [...messages].reverse().find((x) => x.role === 'assistant' && x.error)
    return m ? { text: m.text.startsWith('出错了') ? '出错了' : m.text.slice(0, 20), at: errorSeenAt(m.id) } : null
  }, [messages])

  // 秒级时钟：只驱动依赖 now 的分支
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(t)
  }, [])

  // 在飞轮 + 过程区
  const active = [...messages].reverse().find((m) => m.role === 'assistant' && (m.pending || m.streaming || m.processActive))
  const processLabel = active?.process?.length ? active.process[active.process.length - 1].label : ''
  const processSince = active?.processActive ? processSeenAt(active.id) : 0

  // 降级轴（§12.1）：B1 只接今天就有信号的四种
  const degradations: Degradation[] = []
  if (ptt?.errorKind === 'permission') degradations.push({ kind: 'permission_denied', what: 'mic', text: ptt.error })
  if (hf.bargeInDisabled) degradations.push({ kind: 'audio_echo_degraded', reason: hf.bargeInDisabled })
  if (hf.pipelineDegraded) degradations.push({ kind: 'service_degraded', text: hf.pipelineDegraded })
  if (uncertainIds.length) degradations.push({ kind: 'transport_unknown', messageIds: uncertainIds })

  // pendingOps 的摘要：带该 operationId 的助手气泡原话
  const ops = pendingOps.map((op) => ({
    id: op.id,
    ts: op.ts,
    summary: (messages.find((m) => m.operationId === op.id)?.text || '待确认的操作').replace(/\s+/g, ' ').slice(0, 24),
  }))

  return derivePresence({
    now,
    connStatus,
    connChangedAt: connChangedAt.current,
    hfEnabled: settings.handsFree,
    hfUsable: hf.availability.usable,
    hfFsm: hf.fsm,
    ptt: ptt?.state ?? 'idle',
    partial: ptt?.partial || hf.partial || '',
    turn: {
      pending: !!active?.pending,
      streaming: !!active?.streaming,
      processActive: !!active?.processActive,
      processLabel,
      processSince,
    },
    speaking,
    pendingOps: ops,
    pendingLocation: pendingLocationText !== null,
    voicePipeline: settings.voicePipeline,
    visionCapturing,
    queued,
    lastError,
    degradations,
    driving: !!active?.driving,
    identity: settings.deviceRole,
    user,
  })
}

// ── 时刻登记：Msg 类型是共享的、不能加字段，所以「这条错误/过程是什么时候出现的」在这里记 ──
const seenAt = new Map<string, number>()
function firstSeen(key: string): number {
  const t = seenAt.get(key)
  if (t) return t
  const n = Date.now()
  seenAt.set(key, n)
  if (seenAt.size > 200) seenAt.delete(seenAt.keys().next().value as string)
  return n
}
function errorSeenAt(id: string): number {
  return firstSeen('err:' + id)
}
function processSeenAt(id: string): number {
  return firstSeen('proc:' + id)
}
```

- [ ] **Step 5: 跑测试 + 类型检查**

Run: `cd mobile && npx jest test/activityLog.test.ts && npm run typecheck`
Expected: PASS（3）；`tsc` 0（`HandsFreeUi.bargeInDisabled/pipelineDegraded`、`PttHandle.errorKind`、`SessionState.queued/uncertainIds` 都在 Task 4/5 已加）

- [ ] **Step 6: 提交**

```bash
git add -- mobile/src/core/presence/activityLog.ts mobile/src/features/chat/usePresence.ts mobile/test/activityLog.test.ts
git commit -m "feat(mobile): UX v2.1 B1-8 采集激活日志 + usePresence 收集器（只订阅与计时，不判断）"
```

---

### Task 9: 状态胶囊与 Focus Dock 组件

**Files:**
- Create: `mobile/src/features/chat/PresenceCapsule.tsx`
- Create: `mobile/src/features/chat/FocusDock.tsx`

- [ ] **Step 1: 写 `PresenceCapsule`**

```tsx
// mobile/src/features/chat/PresenceCapsule.tsx
// 状态胶囊（方案 §4.3）：替代 v1 的四条窄条——一次只说一件「此刻」的事；「欠着」的归 Dock。
// 材质 G1-tint（叠在深空底上）；文字不用极光（虹彩纪律）。
import { Pressable, Text, View } from 'react-native'

import type { PresenceSnapshot } from '@/core/presence/presence'
import { RADIUS, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'
import type { FontScalePref } from '@/core/settings/store'

export function PresenceCapsule({
  p,
  fontScale,
  snapshot,
  onPress,
}: {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onPress?(): void
}) {
  const c = snapshot.capsule
  if (!c) return null
  const fg = c.tone === 'amber' ? p.amber : c.tone === 'red' ? p.red : c.tone === 'accent' ? p.accent : p.fg2
  return (
    <View style={{ alignItems: 'center', paddingVertical: 4 }}>
      <Pressable
        testID="presence-capsule"
        onPress={onPress}
        accessibilityRole="text"
        accessibilityLiveRegion="polite"
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: 6,
          minHeight: scale(26, 'target', fontScale),
          paddingHorizontal: 12,
          borderRadius: RADIUS.full,
          backgroundColor: p.glassBg,
          borderWidth: 1,
          borderColor: p.glassBdRight,
          borderTopColor: p.glassBdTop,
          boxShadow: p.glassShadow,
        }}
      >
        {c.live ? (
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: p.accent, boxShadow: `0 0 8px ${p.accent}` }} />
        ) : null}
        <Text numberOfLines={1} style={{ color: fg, fontSize: scale(TYPE.micro + 1, 'text', fontScale), maxWidth: 260 }}>
          {c.text}
        </Text>
      </Pressable>
    </View>
  )
}
```

- [ ] **Step 2: 写 `FocusDock`**

```tsx
// mobile/src/features/chat/FocusDock.tsx
// 承诺面（方案 §5.3）：读 `commitment[]`（钉一项 + 其余个数）与 `degradation[]`（有出口的降级）。
// 材质 **G0 实色**（§5.11：确认/错误/隐私说明不许半透明；坑账 §9.36 同判据）。
// 确认按钮比例照 A-6.4：取消 flex1 / 确认 flex2；剩余时间**只读共享 TTL**（commitment.ts）。
import { useEffect, useState } from 'react'
import { Linking, Pressable, Text, View } from 'react-native'

import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { confirmRemainingMs, pinCommitment, type DockItem } from '@/core/presence/commitment'
import type { Degradation, PresenceSnapshot } from '@/core/presence/presence'
import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export interface FocusDockProps {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onConfirm(reply: '确认' | '取消', operationId?: string): void
  onCancelTurn(): void
  /** 「另有 N 个待处理 ›」点开 → 调用方决定怎么展示（B1：滚到列表底部即可） */
  onOthers?(): void
  onReenableBargeIn?(): void
}

function fmt(ms: number): string {
  const s = Math.ceil(ms / 1000)
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s}s`
}

export function FocusDock(props: FocusDockProps) {
  const { p, fontScale, snapshot } = props
  const pinned = pinCommitment(snapshot.commitment)
  const degradations = snapshot.degradation.filter((d) => d.kind !== 'transport_unknown' && d.kind !== 'recoverable_error')
  if (!pinned && !degradations.length) return null
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  return (
    <View testID="focus-dock" style={{ paddingHorizontal: 12, paddingBottom: 6, gap: 6 }}>
      {pinned ? <CommitmentCard {...props} item={pinned.item} others={pinned.others} solid={solid} /> : null}
      {degradations.map((d) => (
        <DegradationRow key={d.kind} p={p} fontScale={fontScale} d={d} solid={solid} onReenableBargeIn={props.onReenableBargeIn} />
      ))}
    </View>
  )
}

function CommitmentCard({
  p,
  fontScale,
  item,
  others,
  solid,
  onConfirm,
  onCancelTurn,
  onOthers,
}: FocusDockProps & { item: DockItem; others: number; solid: string }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (item.kind !== 'confirm' || item.subkind === 'location') return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [item])
  const h = scale(TARGET.parked, 'target', fontScale)
  const border = item.kind === 'confirm' ? 'rgba(245,158,11,0.38)' : p.line
  return (
    <View
      testID={item.kind === 'confirm' ? 'dock-confirm' : `dock-${item.kind}`}
      accessibilityLiveRegion="assertive"
      style={{
        backgroundColor: solid,
        borderRadius: RADIUS.lg,
        borderWidth: 1,
        borderColor: border,
        padding: 10,
        gap: 8,
        boxShadow: item.kind === 'confirm' ? '0 0 16px rgba(245,158,11,0.12), 0 8px 24px rgba(0,0,0,0.3)' : '0 8px 24px rgba(0,0,0,0.25)',
      }}
    >
      {item.kind === 'confirm' ? (
        <>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body, 'text', fontScale) }}>⚠</Text>
            <Text numberOfLines={1} style={{ color: p.fg1, fontSize: scale(TYPE.body, 'text', fontScale), fontWeight: '600', flex: 1 }}>
              {item.summary}
            </Text>
            <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>
              {item.subkind === 'location' ? '位置授权' : '危险动作 · 需二次确认'}
            </Text>
          </View>
          {item.subkind !== 'location' ? (
            <View style={{ gap: 4 }}>
              <View style={{ height: 2, borderRadius: 1, backgroundColor: p.fill2, overflow: 'hidden' }}>
                <View
                  style={{
                    height: 2,
                    width: `${Math.round((confirmRemainingMs(item, now) / PENDING_TTL_MS) * 100)}%`,
                    backgroundColor: p.amber,
                  }}
                />
              </View>
              <Text testID="dock-countdown" style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>
                {fmt(confirmRemainingMs(item, now))} 后过期
              </Text>
            </View>
          ) : null}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              testID="dock-cancel"
              accessibilityRole="button"
              onPress={() => onConfirm('取消', item.subkind === 'location' ? undefined : item.id)}
              style={{ flex: 1, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: p.fill2, backgroundColor: p.fill, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>{item.subkind === 'location' ? '拒绝' : '取消'}</Text>
            </Pressable>
            <Pressable
              testID="dock-accept"
              accessibilityRole="button"
              onPress={() => onConfirm('确认', item.subkind === 'location' ? undefined : item.id)}
              style={{ flex: 2, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: 'rgba(245,158,11,0.38)', backgroundColor: p.amberSoft, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={{ color: p.amber, fontSize: scale(TYPE.body - 1, 'text', fontScale), fontWeight: '600' }}>{item.subkind === 'location' ? '允许' : '确认'}</Text>
            </Pressable>
          </View>
        </>
      ) : item.kind === 'task' ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: p.teal, fontSize: scale(TYPE.body, 'text', fontScale) }}>⟳</Text>
          <Text numberOfLines={1} style={{ color: p.fg1, fontSize: scale(TYPE.body - 1, 'text', fontScale), flex: 1 }}>{item.label}…</Text>
          <Pressable accessibilityRole="button" onPress={onCancelTurn} style={{ minHeight: h, paddingHorizontal: 12, justifyContent: 'center' }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>取消</Text>
          </Pressable>
        </View>
      ) : item.kind === 'queue' ? (
        <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>
          {item.count} 条消息排队中，连上后自动补发
        </Text>
      ) : (
        <Text style={{ color: p.fg1, fontSize: scale(TYPE.body - 1, 'text', fontScale) }}>还差一个信息：{item.missing}</Text>
      )}
      {others > 0 ? (
        <Pressable onPress={onOthers} accessibilityRole="button">
          <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale) }}>另有 {others} 个待处理 ›</Text>
        </Pressable>
      ) : null}
    </View>
  )
}

const DEGRADATION_TEXT: Record<Exclude<Degradation['kind'], 'transport_unknown' | 'recoverable_error'>, (d: Degradation) => string> = {
  permission_denied: (d) => (d.kind === 'permission_denied' ? d.text : ''),
  service_degraded: (d) => (d.kind === 'service_degraded' ? d.text : ''),
  safety_blocked: (d) => (d.kind === 'safety_blocked' ? d.text : ''),
  audio_echo_degraded: () => '环境回声较强，本轮已关闭插话；点「停止播报」后再说',
  fatal: (d) => (d.kind === 'fatal' ? d.text : ''),
}

function DegradationRow({
  p,
  fontScale,
  d,
  solid,
  onReenableBargeIn,
}: {
  p: Palette
  fontScale: FontScalePref
  d: Degradation
  solid: string
  onReenableBargeIn?(): void
}) {
  if (d.kind === 'transport_unknown' || d.kind === 'recoverable_error') return null
  const text = DEGRADATION_TEXT[d.kind](d)
  const action =
    d.kind === 'permission_denied'
      ? { label: '去系统设置', run: () => void Linking.openSettings() }
      : d.kind === 'audio_echo_degraded' && onReenableBargeIn
        ? { label: '重新开启插话', run: onReenableBargeIn }
        : null
  return (
    <View
      testID={`dock-${d.kind}`}
      style={{ backgroundColor: solid, borderRadius: RADIUS.md, borderWidth: 1, borderColor: d.kind === 'safety_blocked' || d.kind === 'fatal' ? 'rgba(239,68,68,0.35)' : p.line, padding: 10, flexDirection: 'row', alignItems: 'center', gap: 8 }}
    >
      <Text style={{ color: p.fg2, fontSize: scale(TYPE.caption, 'text', fontScale), flex: 1 }}>{text}</Text>
      {action ? (
        <Pressable accessibilityRole="button" onPress={action.run} style={{ minHeight: scale(TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 8 }}>
          <Text style={{ color: p.accent, fontSize: scale(TYPE.caption, 'text', fontScale) }}>{action.label}</Text>
        </Pressable>
      ) : null}
    </View>
  )
}
```

⚠ `audio_echo_degraded` 的「重新开启插话」：voiceLoop 的 `_bargeInDisabled` 是**会话级不可恢复**（`voiceLoop.mjs:129` 注释）——B1 的做法是关掉再打开免唤醒开关（等于重建控制器）。`onReenableBargeIn` 在 ChatScreen 里实现为 `update({ handsFree: false })` 后下一帧 `update({ handsFree: true })`。不改共享 FSM。

- [ ] **Step 3: 类型检查**

Run: `cd mobile && npm run typecheck`
Expected: 0 error

- [ ] **Step 4: 提交**

```bash
git add -- mobile/src/features/chat/PresenceCapsule.tsx mobile/src/features/chat/FocusDock.tsx
git commit -m "feat(mobile): UX v2.1 B1-9 状态胶囊 + Focus Dock（G0 实色、钉一项、倒计时读共享 TTL、降级出口）"
```

---

### Task 10: 对话屏接线——四条窄条 → 胶囊 + Dock；光球读 primary

**Files:**
- Modify: `mobile/src/features/chat/ChatScreen.tsx`
- Modify: `mobile/src/features/chat/Composer.tsx`
- Modify: `mobile/src/features/chat/MessageBubble.tsx`

- [ ] **Step 1: `Composer` 改为读 `primary`、热区 56、删提示行**

`Composer` props 加 `orbState: OrbState`、`orbDim?: boolean`、`fontScale: FontScalePref`；**删除** `orbState` 的本地推导（`Composer.tsx:39`）与提示行（`:40-43`、`:75-88`）；PTT 按钮改：

```tsx
        {ptt ? (
          <Pressable
            testID="composer-orb"
            onPressIn={ptt.pressDown}
            onPressOut={ptt.pressUp}
            disabled={finalizing}
            accessibilityRole="button"
            accessibilityLabel={recording ? '松开发送' : '按住说话'}
            style={{
              width: scale(TARGET.driving, 'target', fontScale),
              height: scale(TARGET.driving, 'target', fontScale),
              borderRadius: RADIUS.full,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: recording ? p.accentSoft : 'transparent',
            }}
          >
            <AuroraOrb size={44} state={orbState} dim={orbDim} animated />
          </Pressable>
        ) : null}
```

（导入 `RADIUS, TARGET, scale` from `'../../ui/tokens'`，`FontScalePref` from settings store。）`placeholder` 保留 `recording ? '正在听…' : '和小舟说点什么…'`。

⚠ 回滚路径：`uxV2Presence=false` 时 ChatScreen 传入的 `orbState` 仍是旧推导值（见 Step 3），Composer 自己不再推导。

- [ ] **Step 2: `MessageBubble` 气泡内确认按钮加开关；「发送状态未知」灰字**

props 加 `inlineConfirm: boolean`（Dock 开时为 false）、`uncertain?: boolean`。`{msg.needConfirm && confirmActive ? (...)}` 改为 `{msg.needConfirm && confirmActive && inlineConfirm ? (...)}`；在 `{msg.pending ? ...}` 之后加：

```tsx
        {uncertain ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>发送状态未知（网络刚断过；连上后若无回音请再说一次）</Text>
        ) : null}
```

- [ ] **Step 3: `ChatScreen` 接线**

(a) 导入：`usePresence`、`PresenceCapsule`、`FocusDock`、`activityLog`、`type OrbState`。删除 `HF_LABEL` / `HF_DOT` 常量**不要删**——`uxV2Presence=false` 的回滚分支仍用它们（B4 稳定后再删，§11.5）。

(b) `ChatBody` 里在 `hf` 之后加：

```tsx
  const snapshot = usePresence({ core, hf, ptt: cfg.audioUrl ? ptt : null, user: cfg.token.slice(-4) })
  const v2 = settings.uxV2Presence
  const dock = settings.uxV2Dock
  // 回滚分支的光球态（v1 推导，逐字照 Composer 旧代码）
  const legacyOrb: OrbState = ptt.state === 'recording' ? 'speaking' : ptt.state === 'finalizing' ? 'thinking' : 'idle'
  const { uncertainIds } = useStore(core.store)
  const reenableBargeIn = useCallback(() => {
    settingsStore.getState().update({ handsFree: false })
    setTimeout(() => settingsStore.getState().update({ handsFree: true }), 50)
  }, [])
```

(c) `chatColumn` 里，把「免唤醒状态条」与「hf.error || notice」两段包进 `{!v2 ? (...) : null}`；在 `<Composer>` 之前插入：

```tsx
      {v2 && dock ? (
        <FocusDock
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          onConfirm={onConfirm}
          onCancelTurn={onInterrupt}
          onReenableBargeIn={reenableBargeIn}
        />
      ) : null}
      {v2 ? <PresenceCapsule p={p} fontScale={settings.fontScale} snapshot={snapshot} /> : null}
```

`<Composer>` 传 `orbState={v2 ? snapshot.primary : legacyOrb}`、`orbDim={v2 && snapshot.dim}`、`fontScale={settings.fontScale}`。`MessageBubble` 传 `inlineConfirm={!(v2 && dock)}`、`uncertain={uncertainIds.includes(item.id)}`；FlashList 的 `extraData` 加 `uncertainIds, v2, dock`。

(d) 弱网横幅 `{linkWarn ? (...)}` 包进 `{!v2 && linkWarn ? ... : null}`（v2 下胶囊已表达）。顶栏连接 pill 改为**健康点**：v2 时只渲染 7dp 点（`transport==='online'` 灰 `p.fg3`、`reconnecting` 琥珀、`offline` 红），不带文字；非 v2 保持旧 pill。顶栏光球 `state={busy ? 'thinking' : 'idle'}` 保持（品牌球只 idle/thinking，方案 §5.1）。

(e) PTT 与免唤醒的激活写日志：`usePtt` 的 `onFinal` 不变；在 `ptt` 创建后加

```tsx
  useEffect(() => {
    if (ptt.state === 'recording') activityLog.push('mic', '按住说话')
  }, [ptt.state])
  useEffect(() => {
    if (hf.fsm === 'LISTENING') activityLog.push('mic', settings.voicePipeline === 's2s' ? '唤醒词命中 · 端到端（原始音频上传）' : '唤醒词命中')
  }, [hf.fsm, settings.voicePipeline])
```

视觉：`onSend` 里 `captureVisionFrame` 之前 `activityLog.push('camera', `触发词「${text.slice(0, 12)}」`)`。

- [ ] **Step 4: 类型检查 + 全量测试 + 真机冒烟**

Run: `cd mobile && npm run typecheck && npm test`
Expected: `tsc` 0；全量绿。
真机（Metro 热载）：① 发「打开后备箱」→ Dock 出现（`dock-confirm`）、倒计时递减、点「取消」→ Dock 消失；② 关闭「承诺面」开关 → 确认回到气泡内；③ 关闭「光球状态锚」开关 → 旧四条状态条回来。截图三张存 `mobile/e2e/artifacts/b1-10-*.png`（目录 gitignore，见 Task 17）。

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/Composer.tsx mobile/src/features/chat/MessageBubble.tsx
git commit -m "feat(mobile): UX v2.1 B1-10 对话屏接线——四条窄条收成胶囊 + Dock，光球读 primary，两开关保留 v1 回滚路径"
```

---

### Task 11: 隐私栏（Privacy Rail）

**Files:**
- Create: `mobile/src/features/chat/PrivacyRail.tsx`
- Modify: `mobile/src/features/chat/ChatScreen.tsx`（顶栏采集点 + 打开隐私栏）

- [ ] **Step 1: 写 `PrivacyRail`**

```tsx
// mobile/src/features/chat/PrivacyRail.tsx
// 隐私栏（方案 §5.10）：红线三条件的**实时**表达。G0 实色；读 snapshot.privacy + 激活日志。
// 「当前用户」= token 身份（App 端没有声纹，§2.3 信道约束），不做「未确认说话人 / 访客模式」。
import { useEffect, useState } from 'react'
import { Modal, Pressable, ScrollView, Text, View } from 'react-native'

import { activityLog, type ActivityEntry } from '@/core/presence/activityLog'
import type { PresenceSnapshot } from '@/core/presence/presence'
import { settingsStore, type FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

const MIC_TEXT = {
  off: '关',
  edge: '本机处理（唤醒词待机 / 转文字后只上传文字）',
  cloudAudio: '原始音频上传中（端到端）',
} as const

function when(e: ActivityEntry | null): string {
  if (!e) return '—'
  const d = new Date(e.at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')} ${e.note}`
}

export function PrivacyRail({
  p,
  fontScale,
  snapshot,
  visible,
  onClose,
  onStopMic,
}: {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  visible: boolean
  onClose(): void
  /** 一键关闭本轮麦克风（停 PTT / 免唤醒 / ASR 流） */
  onStopMic(): void
}) {
  const [, force] = useState(0)
  useEffect(() => activityLog.subscribe(() => force((n) => n + 1)), [])
  const { settings, update } = settingsStore.getState()
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  const row = (k: string, v: string, tone: string = p.fg1) => (
    <View style={{ flexDirection: 'row', gap: 12, paddingVertical: 6, borderBottomWidth: 1, borderColor: p.line }}>
      <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fontScale), width: 88 }}>{k}</Text>
      <Text style={{ color: tone, fontSize: scale(TYPE.caption, 'text', fontScale), flex: 1 }}>{v}</Text>
    </View>
  )
  const btn = (label: string, run: () => void) => (
    <Pressable accessibilityRole="button" onPress={run} style={{ minHeight: scale(TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 12, borderRadius: RADIUS.md, borderWidth: 1, borderColor: p.fill2, backgroundColor: p.fill }}>
      <Text style={{ color: p.fg1, fontSize: scale(TYPE.caption, 'text', fontScale) }}>{label}</Text>
    </Pressable>
  )
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={onClose} accessibilityLabel="关闭隐私栏" />
      <View testID="privacy-rail" style={{ backgroundColor: solid, borderTopLeftRadius: RADIUS['2xl'], borderTopRightRadius: RADIUS['2xl'], padding: 16, gap: 10, maxHeight: '70%' }}>
        <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fontScale), fontWeight: '600' }}>隐私 · 现在在采集什么</Text>
        <ScrollView>
          {row('麦克风', MIC_TEXT[snapshot.privacy.mic], snapshot.privacy.mic === 'cloudAudio' ? p.amber : p.fg1)}
          {row('摄像头', snapshot.privacy.camera === 'singleFrame' ? '正在抓一帧（触发词命中）' : '关')}
          {row('最近一次', `麦 ${when(activityLog.lastOf('mic'))}`)}
          {row('', `摄像头 ${when(activityLog.lastOf('camera'))}`)}
          {row('当前用户', `token ····${snapshot.privacy.user}（App 端身份 = token，不做声纹）`)}
          <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale), lineHeight: scale(17, 'line', fontScale), paddingTop: 8 }}>
            三段式：语音在本机转成文字后只上传文字。端到端：上传原始音频，仅在唤醒后的对话窗内采集。
            拍到的画面只用于回答当前这一句，服务器最多保留两分钟，不落盘、不进记忆。
          </Text>
        </ScrollView>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          {btn('关闭本轮麦克风', () => { onStopMic(); onClose() })}
          {settings.handsFree ? btn('关闭免唤醒', () => { update({ handsFree: false }); onClose() }) : null}
          {settings.visionEnabled ? btn('关闭看图问答', () => { update({ visionEnabled: false }); onClose() }) : null}
        </View>
      </View>
    </Modal>
  )
}
```

- [ ] **Step 2: 顶栏采集点 + 入口**

`ChatScreen` 顶栏健康点旁加 8dp 采集点：`snapshot.privacy.mic === 'cloudAudio'` 琥珀 / `mic !== 'off'` 青 / `camera === 'singleFrame'` 白 / 其余不渲染；点按（整个健康点 + 采集点区域是一个 `Pressable`，热区 40dp）→ `setPrivacyOpen(true)`。挂 `<PrivacyRail visible={privacyOpen} onClose={() => setPrivacyOpen(false)} onStopMic={() => { ptt.pressUp(); if (settings.handsFree) reenableBargeIn() }} … />`（`onStopMic` 的免唤醒半边 = 关再开，等于打断当前收音窗；B2 的语音层会给它更准确的实现）。

- [ ] **Step 3: 类型检查 + 真机冒烟**

Run: `cd mobile && npm run typecheck`；真机：免唤醒开 → 顶栏青点 → 点开 → 「本机处理（唤醒词待机…）」；切端到端并唤醒 → 琥珀点 + 「原始音频上传中」；截图存档。

- [ ] **Step 4: 提交**

```bash
git add -- mobile/src/features/chat/PrivacyRail.tsx mobile/src/features/chat/ChatScreen.tsx
git commit -m "feat(mobile): UX v2.1 B1-11 隐私栏——麦/摄像头/最近激活/当前用户实时指示 + 一键关闭"
```

---

### Task 12: 键盘避让——先取读数，再修

**Files:**
- Modify: `mobile/src/features/chat/ChatScreen.tsx:358-360`、`mobile/src/app/onboarding.tsx:97-100`、`mobile/src/app/debug.tsx:96-99`
- Create: `mobile/e2e/08-keyboard-no-hide.yaml`

- [ ] **Step 1: 取读数（真机，HyperOS 输入法）**

对话页点输入框弹键盘 → `adb exec-out screencap -p -d <displayId> > mobile/e2e/artifacts/b1-12-chat-kbd.png`；Onboarding 页同样一张。判据：**发送键与输入框是否被键盘盖住**。把两张图的结论写进本任务提交信息（「遮 / 不遮」各一）。

- [ ] **Step 2: 写 Maestro 流（无论读数如何都要有——它是以后这条不回退的探针）**

```yaml
# mobile/e2e/08-keyboard-no-hide.yaml
# 流 ⑧：键盘弹出时发送键仍可点（UX v2.1 P6）。**刻意不 hideKeyboard**——
# 01/02/03 里的 hideKeyboard 是 Maestro 树里只剩输入法的问题（坑账），不是遮挡证据；
# 这条才是遮挡的直接判据：树里拿不到发送键就红。
appId: com.xiaozhou.companion
name: 08 键盘弹出不遮发送键
tags:
  - online
---
- runFlow: subflows/open-app.yaml
- tapOn:
    id: "composer-input"
- inputText: "现在几点"
- assertVisible:
    id: "composer-send"
- tapOn:
    id: "composer-send"
- assertVisible: "现在几点"
```

- [ ] **Step 3: 按读数二选一改代码**

**A. 读数=遮挡**（三处 `KeyboardAvoidingView`）：

```tsx
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior="padding"
          keyboardVerticalOffset={Platform.OS === 'android' ? 0 : 0}
        >
```

并在 `mobile/app.config.ts` 的 `android` 段确认 `softwareKeyboardLayoutMode: 'resize'`（缺省即是；若 edge-to-edge 下 `resize` 失效，`behavior="padding"` 由 RN 用键盘高度补底）。

**B. 读数=不遮**：三处改成不再传 `behavior`（删掉那行死代码），注释写明「Android `adjustResize` 已由系统重排，KeyboardAvoidingView 只为 iOS 后路保留」。

- [ ] **Step 4: 跑流**

Run: `maestro test --no-reinstall-driver mobile/e2e/08-keyboard-no-hide.yaml`
Expected: `1/1 Flows Passed`（A 与 B 两种读数下都必须通过——这条流就是「修没修对」的判据）

- [ ] **Step 5: 提交**

```bash
git add -- mobile/src/features/chat/ChatScreen.tsx mobile/src/app/onboarding.tsx mobile/src/app/debug.tsx mobile/e2e/08-keyboard-no-hide.yaml
git commit -m "fix(mobile): UX v2.1 B1-12 键盘避让——真机读数=<遮/不遮>，<A/B> 修法 + Maestro 08 流钉住"
```

---

### Task 13: Onboarding 上品牌

**Files:**
- Modify: `mobile/src/app/onboarding.tsx`（整文件重写视觉；**逻辑函数 `onTest/onSave/derived/presets` 一行不改**）

- [ ] **Step 1: 重写渲染部分**

保留文件顶部的 import 与 `Onboarding()` 内所有 state/逻辑；把 `return (...)` 与 `styles` 替换为：

```tsx
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const fs = settings.fontScale
  const label = (t: string) => (
    <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fs), marginTop: 6 }}>{t}</Text>
  )
  const input = (props: React.ComponentProps<typeof TextInput>) => (
    <TextInput
      placeholderTextColor={p.fg3}
      {...props}
      style={{
        backgroundColor: p.fill,
        borderWidth: 1,
        borderColor: p.fill2,
        borderRadius: RADIUS.md,
        paddingHorizontal: 12,
        minHeight: scale(TARGET.parked, 'target', fs),
        color: p.fg1,
        fontSize: scale(TYPE.body, 'text', fs),
      }}
    />
  )
  const button = (text: string, run: () => void, primary: boolean, disabled: boolean) => (
    <Pressable
      onPress={run}
      disabled={disabled}
      accessibilityRole="button"
      style={{
        minHeight: scale(TARGET.parked, 'target', fs),
        borderRadius: RADIUS.lg,
        alignItems: 'center',
        justifyContent: 'center',
        opacity: disabled ? 0.4 : 1,
        ...(primary
          ? { experimental_backgroundImage: AURORA.gradient, boxShadow: '0 4px 22px rgba(91,140,255,0.45)' }
          : { backgroundColor: p.fill, borderWidth: 1, borderColor: p.fill2 }),
      }}
    >
      <Text style={{ color: primary ? '#fff' : p.fg1, fontSize: scale(TYPE.body, 'text', fs), fontWeight: '600' }}>{text}</Text>
    </Pressable>
  )

  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <SafeAreaView style={{ flex: 1 }} edges={['top', 'bottom']}>
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView contentContainerStyle={{ padding: 20, gap: 14 }} keyboardShouldPersistTaps="handled">
            <View style={{ alignItems: 'center', gap: 8, paddingVertical: 12 }}>
              <AuroraOrb size={88} state="idle" animated />
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.display - 6, 'text', fs), fontWeight: '600', marginTop: 8 }}>我是小舟</Text>
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fs) }}>先连上你的座舱服务器</Text>
            </View>

            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>服务器</Text>
              <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
                {presets.map((it) => (
                  <Pressable key={it.key} onPress={() => { setPreset(it.key); setTest({ kind: 'idle' }) }} style={{ paddingVertical: 8, paddingHorizontal: 14, borderRadius: RADIUS.full, backgroundColor: preset === it.key ? p.accentSoft : p.fill, borderWidth: 1, borderColor: preset === it.key ? p.accent : p.fill2 }}>
                    <Text style={{ color: preset === it.key ? p.accent : p.fg2, fontSize: scale(TYPE.caption, 'text', fs) }}>{it.label}</Text>
                  </Pressable>
                ))}
              </View>
              {preset === 'cloud' ? (
                <View>
                  {label('Tailnet FQDN')}
                  {input({ value: fqdn, onChangeText: (v) => { setFqdn(v); setTest({ kind: 'idle' }) }, placeholder: 'your-machine.tailxxxx.ts.net', autoCapitalize: 'none', autoCorrect: false })}
                  {fqdn.trim().length > 0 && !derived && <Text style={{ color: p.red, fontSize: scale(TYPE.caption, 'text', fs), marginTop: 6 }}>FQDN 不合法（须形如 xxx.ts.net，全小写）</Text>}
                  {derived && <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fs), marginTop: 6 }}>主链 {derived.edgeUrl} ｜ 音频 {derived.audioUrl}</Text>}
                </View>
              ) : (
                <View>
                  {label('主链入口（edge）')}
                  {input({ value: edgeUrl, onChangeText: (v) => { setEdgeUrl(v); setTest({ kind: 'idle' }) }, placeholder: 'http://192.168.1.10:18000', autoCapitalize: 'none', autoCorrect: false })}
                  {label('音频入口（audio）')}
                  {input({ value: audioUrl, onChangeText: setAudioUrl, placeholder: 'http://192.168.1.10:50059', autoCapitalize: 'none', autoCorrect: false })}
                </View>
              )}
            </Glass>

            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>访问 token</Text>
              {label(savedTokenTail ? `当前 ····${savedTokenTail}；只存本机安全存储，不进日志` : '只存本机安全存储，不进日志')}
              {input({ value: token, onChangeText: (v) => { setToken(v); setTest({ kind: 'idle' }) }, placeholder: 'AUTH_TOKENS 条目的 token 段', autoCapitalize: 'none', autoCorrect: false, secureTextEntry: true })}
            </Glass>

            {/* 权限用途文案的合规落点（app.config.ts:99-101 注释点名：manifest 里写了字不算合规） */}
            <Glass p={p} r={RADIUS['2xl']} style={{ padding: 14, gap: 6 }}>
              <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fs), fontWeight: '600' }}>之后会用到的权限</Text>
              <Text style={{ color: p.fg2, fontSize: scale(TYPE.caption, 'text', fs), lineHeight: scale(18, 'line', fs) }}>
                麦克风：按住光球说话时才开；免唤醒与端到端默认关，要你在设置里显式打开。{'\n'}
                定位：只在位置相关请求时取一次坐标，坐标不持久化。{'\n'}
                摄像头：只有开了「看图问答」且说出看图的话时才拍一张，不落盘不进记忆。
              </Text>
            </Glass>

            {button(test.kind === 'testing' ? '连接中…' : '连接测试', onTest, false, !canTest || test.kind === 'testing')}
            {test.kind === 'ok' && <Text style={{ color: p.green, fontSize: scale(TYPE.caption, 'text', fs) }}>✓ 连接成功（握手通过）</Text>}
            {test.kind === 'fail' && <Text style={{ color: p.red, fontSize: scale(TYPE.caption, 'text', fs) }}>{test.message}</Text>}
            {button('保存并进入', onSave, true, !canSave)}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  )
```

新增 import：`SafeAreaView` from `react-native-safe-area-context`；`useStore` from `zustand`；`settingsStore` from `@/core/settings/store`；`AuroraBackground, AuroraOrb, Glass` from `@/ui/aurora`；`AURORA, usePalette` from `@/ui/theme`；`RADIUS, TARGET, TYPE, scale` from `@/ui/tokens`。删除 `StyleSheet` import 与整个 `styles`。Task 12 若选 B 路径，这里的 `KeyboardAvoidingView` 也同样处理。

- [ ] **Step 2: 类型检查 + 真机截图（深浅各一）**

Run: `cd mobile && npm run typecheck`；设置页「重新配置」进入 → 截图；`cmd uimode night no` 切浅色再截一张。

- [ ] **Step 3: 提交**

```bash
git add -- mobile/src/app/onboarding.tsx
git commit -m "feat(mobile): UX v2.1 B1-13 Onboarding 上品牌——Aurora 底 + 玻璃 + 光球 + 色板 + 安全区 + 权限用途文案"
```

---

### Task 14: 状态画廊（`/state-gallery`）+ 样本覆盖守卫

**Files:**
- Create: `mobile/src/core/presence/fixtures.ts`
- Create/Replace: `mobile/src/app/state-gallery.tsx`
- Modify: `mobile/src/app/_layout.tsx`（若 Task 7 未注册）
- Test: `mobile/test/presenceFixtures.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
// mobile/test/presenceFixtures.test.ts
// 状态画廊的覆盖度守卫（同 card-gallery 的「注册表卡型必须都有样本」）：
// 每个 primary（8 个光球态）与每种 degradation（7 种）都要有样本，缺一即红——
// 「少了谁」得当场看得见，不能等到事后数截图。
import { presenceFixtures } from '@/core/presence/fixtures'
import type { Degradation, OrbState } from '@/core/presence/presence'

const PRIMARIES: OrbState[] = ['idle', 'armed', 'listening', 'thinking', 'speaking', 'attention', 'looking', 'muted']
const DEGRADATIONS: Degradation['kind'][] = [
  'recoverable_error', 'transport_unknown', 'permission_denied', 'service_degraded', 'safety_blocked', 'audio_echo_degraded', 'fatal',
]

test('每个 primary 至少一条样本', () => {
  const covered = new Set(presenceFixtures().map((f) => f.snapshot.primary))
  expect(PRIMARIES.filter((s) => !covered.has(s))).toEqual([])
})

test('每种 degradation 至少一条样本', () => {
  const covered = new Set(presenceFixtures().flatMap((f) => f.snapshot.degradation.map((d) => d.kind)))
  expect(DEGRADATIONS.filter((k) => !covered.has(k))).toEqual([])
})

test('样本标签唯一（?only= 直达靠它）', () => {
  const labels = presenceFixtures().map((f) => f.label)
  expect(new Set(labels).size).toBe(labels.length)
})
```

- [ ] **Step 2: 跑测试确认红**

Run: `cd mobile && npx jest test/presenceFixtures.test.ts`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 写样本**

```ts
// mobile/src/core/presence/fixtures.ts
// 状态画廊语料：**每次调用重新取时间基准**（倒计时是相对量，同 cards/fixtures.ts 的教训）。
// 样本走的是 derivePresence 本尊——画廊里看到的就是生产代码算出来的，不是手摆的快照；
// `slot` 与 `safety_blocked` 两种 B1 没有生产产出方，这里只证明「渲得出来」，不证明「会出现」。
import { derivePresence, type PresenceInput, type PresenceSnapshot } from './presence'

export interface PresenceFixture {
  label: string
  /** 真栈能否产出（false = 只有画廊样本，方案 §0 接手须知 5） */
  producible: boolean
  snapshot: PresenceSnapshot
}

export function presenceFixtures(): PresenceFixture[] {
  const NOW = Date.now()
  const base: PresenceInput = {
    now: NOW, connStatus: 'open', connChangedAt: NOW - 60_000,
    hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', ptt: 'idle', partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false, pendingOps: [], pendingLocation: false, voicePipeline: 'classic',
    visionCapturing: false, queued: 0, lastError: null, degradations: [], driving: false,
    identity: 'handheld', user: 'ab12',
  }
  const mk = (label: string, over: Partial<PresenceInput>, producible = true): PresenceFixture => ({
    label, producible, snapshot: derivePresence({ ...base, ...over }),
  })
  const hf = (fsm: PresenceInput['hfFsm'], extra: Partial<PresenceInput> = {}) => ({ hfEnabled: true, hfUsable: true, hfFsm: fsm, ...extra })
  return [
    mk('idle', {}),
    mk('armed', hf('ARMED')),
    mk('listening-ptt', { ptt: 'recording' }),
    mk('recognizing-partial', { ptt: 'recording', partial: '附近有什么好吃的' }),
    mk('listening-s2s', hf('LISTENING', { voicePipeline: 's2s' })),
    mk('thinking', { turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 } }),
    mk('processing-long', { turn: { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW - 12_000 } }),
    mk('speaking', { speaking: true }),
    mk('followup', hf('FOLLOWUP')),
    mk('attention-confirm', { pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }] }),
    mk('attention-two', { pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }, { id: 'op2', ts: NOW - 5_000, summary: '要解锁车门吗？' }], queued: 1 }),
    mk('attention-location', { pendingLocation: true }),
    mk('looking', { visionCapturing: true }),
    mk('reconnecting', { connStatus: 'connecting', connChangedAt: NOW - 5_000 }),
    mk('offline-with-confirm', { connStatus: 'closed', connChangedAt: NOW - 30_000, pendingOps: [{ id: 'op1', ts: NOW - 20_000, summary: '要打开后备箱吗？' }], queued: 2 }),
    mk('error', { lastError: { text: '出错了', at: NOW - 500 } }),
    mk('deg-permission', { degradations: [{ kind: 'permission_denied', what: 'mic', text: '需要麦克风权限，请在系统设置里允许' }] }),
    mk('deg-service', { degradations: [{ kind: 'service_degraded', text: '语音链路降级，本轮回落三段式' }] }),
    mk('deg-echo', { degradations: [{ kind: 'audio_echo_degraded', reason: 'repeated-self-trigger' }] }),
    mk('deg-transport-unknown', { degradations: [{ kind: 'transport_unknown', messageIds: ['m1'] }] }),
    mk('deg-recoverable', { degradations: [{ kind: 'recoverable_error', text: '响应超时了', at: NOW }] }),
    mk('deg-safety-blocked', { degradations: [{ kind: 'safety_blocked', text: '高速行驶中请勿打开车窗/天窗' }] }, false),
    mk('deg-fatal', { degradations: [{ kind: 'fatal', text: 'token 握手失败，请重新配置服务器' }] }),
  ]
}
```

- [ ] **Step 4: 写画廊屏（替换 Task 7 的占位）**

```tsx
// mobile/src/app/state-gallery.tsx
// 状态画廊（UX v2.1 §11.2 B1 验收 + §11.4 可读性判据的取数屏）。与 card-gallery 同类：
// dev 取证入口，不进主链路；`?only=` 直达；每条标「真栈可产 / 仅样本」。
import { useLocalSearchParams } from 'expo-router'
import { useMemo } from 'react'
import { ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { presenceFixtures } from '@/core/presence/fixtures'
import { settingsStore } from '@/core/settings/store'
import { Chip } from '@/features/cards/parts'
import { FocusDock } from '@/features/chat/FocusDock'
import { PresenceCapsule } from '@/features/chat/PresenceCapsule'
import { AuroraBackground, AuroraOrb } from '@/ui/aurora'
import { usePalette } from '@/ui/theme'

export default function StateGallery() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const { only } = useLocalSearchParams<{ only?: string }>()
  const all = useMemo(() => presenceFixtures(), [])
  const fixtures = useMemo(() => {
    const keys = (only || '').split(',').map((k) => k.trim()).filter(Boolean)
    return keys.length ? all.filter((f) => keys.some((k) => f.label.includes(k))) : all
  }, [all, only])
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <ScrollView contentContainerStyle={{ padding: 12, gap: 18 }}>
        <Text style={{ color: p.fg2, fontSize: p.font(12) }}>
          样本 {fixtures.length}{fixtures.length === all.length ? '' : `/${all.length}`} 条{only ? `（only=${only}）` : ''} · 主题跟随设置页 · 按钮在这里只记录不上行
        </Text>
        {fixtures.map((f) => (
          <View key={f.label} testID={`state-${f.label}`} style={{ gap: 6, borderTopWidth: 1, borderColor: p.line, paddingTop: 10 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Text style={{ color: p.fg3, fontSize: p.font(11), flex: 1 }}>{f.label} · primary={f.snapshot.primary}</Text>
              <Chip p={p} tone={f.producible ? 'accent' : 'amber'} text={f.producible ? '真栈可产' : '仅样本'} />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <AuroraOrb size={44} state={f.snapshot.primary} dim={f.snapshot.dim} animated />
              <View style={{ flex: 1 }}>
                <PresenceCapsule p={p} fontScale={settings.fontScale} snapshot={f.snapshot} />
              </View>
            </View>
            <FocusDock
              p={p}
              fontScale={settings.fontScale}
              snapshot={f.snapshot}
              onConfirm={() => {}}
              onCancelTurn={() => {}}
              onReenableBargeIn={() => {}}
            />
            <Text style={{ color: p.fg3, fontSize: p.font(10) }}>
              transport={f.snapshot.transport} capture={f.snapshot.capture} agent={f.snapshot.agent} mic={f.snapshot.privacy.mic} camera={f.snapshot.privacy.camera}
            </Text>
          </View>
        ))}
      </ScrollView>
    </View>
  )
}
```

`_layout.tsx` 注册：`<Stack.Screen name="state-gallery" options={{ title: '调试 · 状态画廊' }} />`。

- [ ] **Step 5: 跑测试 + 真机截图归档（深浅各一套）**

Run: `cd mobile && npx jest test/presenceFixtures.test.ts && npm run typecheck`
Expected: 3 PASS；`tsc` 0。真机 `xiaozhou://state-gallery` 深色一套、`cmd uimode night no` 浅色一套，存 `mobile/e2e/artifacts/b1-14-*.png`。⚠ 同屏 23 个光球都在动——画廊里 **`animated` 只给可见区**在 B1 不做（取证屏，帧率不是判据）；但要记一条读数：画廊滚动是否掉帧，进实施记录。

- [ ] **Step 6: 提交**

```bash
git add -- mobile/src/core/presence/fixtures.ts mobile/src/app/state-gallery.tsx mobile/src/app/_layout.tsx mobile/test/presenceFixtures.test.ts
git commit -m "feat(mobile): UX v2.1 B1-14 状态画廊 /state-gallery——8 主态 + 7 降级样本走生产 derivePresence，覆盖度守卫"
```

---

### Task 15: Maestro 扩流（06 / 09）+ e2e README

**Files:**
- Create: `mobile/e2e/06-confirm-dock.yaml`、`mobile/e2e/09-state-gallery.yaml`
- Modify: `mobile/e2e/README.md`

- [ ] **Step 1: 写两条流**

```yaml
# mobile/e2e/06-confirm-dock.yaml
# 流 ⑥：危险动作 → Focus Dock 钉住确认（倒计时在走）→ 取消 → Dock 消失（UX v2.1 P5）。
# 与 02 的区别：02 验的是气泡内确认（v1 路径，uxV2Dock 关时仍有效）；这条验承诺面。
# 「到期留痕」（300s）不在这里等——那条由 sessionStore.test.ts 用假时钟守；
# 这里只验「钉住 + 倒计时可见 + 取消后消失」。
appId: com.xiaozhou.companion
name: 06 危险动作进 Focus Dock
tags:
  - online
---
- runFlow: subflows/open-app.yaml
- tapOn:
    id: "composer-input"
- inputText: "打开后备箱"
- hideKeyboard
- tapOn:
    id: "composer-send"
- extendedWaitUntil:
    visible:
      id: "dock-confirm"
    timeout: 45000
- assertVisible:
    id: "dock-countdown"
- assertVisible:
    id: "presence-capsule"
- tapOn:
    id: "dock-cancel"
- extendedWaitUntil:
    notVisible:
      id: "dock-confirm"
    timeout: 20000
```

```yaml
# mobile/e2e/09-state-gallery.yaml
# 流 ⑨：状态画廊离线冒烟（CI 跑）：三个新光球态 + 承诺面 + 降级行渲得出来、不崩。
appId: com.xiaozhou.companion
name: 09 状态画廊离线冒烟
tags:
  - offline
---
- runFlow: subflows/open-app.yaml
- openLink: "xiaozhou://state-gallery?only=attention-two,looking,offline-with-confirm,deg-echo"
- extendedWaitUntil:
    visible:
      id: "state-attention-two"
    timeout: 20000
- assertVisible: "另有 1 个待处理"
- assertVisible: "条消息排队中"
- assertVisible: "环境回声较强"
```

- [ ] **Step 2: 跑**

Run: `maestro test --no-reinstall-driver mobile/e2e/09-state-gallery.yaml && maestro test --no-reinstall-driver mobile/e2e/06-confirm-dock.yaml`
Expected: 各 `1/1 Flows Passed`。⚠ 06 的 `hideKeyboard` 保留——它防的是 Maestro 树问题（坑账），与 08 验的遮挡是两件事。

- [ ] **Step 3: README 加三条流与状态**

在 `mobile/e2e/README.md` 「状态」段下追加：`06 危险动作进 Focus Dock（online）/ 08 键盘不遮发送键（online）/ 09 状态画廊离线冒烟（offline，CI）`，并写实跑读数（时长）。`.github/workflows/mobile-apk.yml` 若按 tag 跑 offline，09 会自动进 CI——**不改 workflow**（CI/CD 是红线路径，改了发版要摘要批准）。

- [ ] **Step 4: 提交**

```bash
git add -- mobile/e2e/06-confirm-dock.yaml mobile/e2e/09-state-gallery.yaml mobile/e2e/README.md
git commit -m "test(mobile): UX v2.1 B1-15 Maestro 06 承诺面 / 09 状态画廊离线冒烟"
```

---

### Task 16: B1 真机验收轮（方案 §11.2 B1 清单逐条打钩）

**Files:**
- 无代码；读数写进 Task 17 的实施记录

- [ ] **Step 1: 逐条取证（MIX Fold 4，`target=cloud`）**

| # | 项 | 做法 | 判据 |
|---|---|---|---|
| 1 | 免唤醒开 → 光球 `armed` 青环 | 设置开免唤醒 → 回对话页截图 | 青环 0.18α 可见；胶囊「说「小舟小舟」」3s 内出现 |
| 2 | 唤醒 → `listening` | 真人说「小舟小舟」（**需泓舟**） | 光球青环 0.4α + 胶囊「在听…」带青点 |
| 3 | 危险动作 → Dock | 「打开后备箱」 | `dock-confirm` + 倒计时递减（两次截图相差 ≥5s 数字变小） |
| 4 | 到期留痕 | 等 300s 不点 | Dock 消失**同时**记录里出现「⏱ 「要打开后备箱吗？」的确认已过期」 |
| 5 | 待确认 + 断网 | 「打开后备箱」后开飞行模式 ≥30s | 光球 `muted`、胶囊「已断开 · 消息会排队」、**Dock 仍钉着确认** |
| 6 | 隐私栏三行 | 免唤醒待机 / PTT 中 / 端到端录音中各截一张 | 麦克风行分别为「本机处理」「本机处理」「原始音频上传中（琥珀）」 |
| 7 | VAL 拒绝 → Dock | 云栈 debug 注入 `speed_kmh=90` 后说「打开天窗」 | **B1 预期：只在气泡里看到 VAL 原话，Dock 无 `safety_blocked`**（协议无结构化标记，接手须知 5）——把这条写成「未接、待 Q16」而不是绿 |
| 8 | 状态画廊 | 深浅各一套截图 | 23 条样本全渲，`presenceFixtures.test.ts` 绿 |
| 9 | 200% 字号 | 系统「显示大小/字体」拉到最大 → 对话页 / Dock / 胶囊 / Onboarding 截图 | 不裁字；Dock 按钮仍 ≥48dp |
| 10 | 键盘 | Maestro 08 | 通过 |
| 11 | 回滚 | 关两个实验室开关 | v1 四条状态条与气泡内确认回来；Maestro 02 仍通过 |
| 12 | 性能 | 语音层未做（B2），本批只看：Dock 出现时对话列表不卡顿；画廊滚动帧率 | 主观不掉帧；记读数 |
| 13 | 无障碍 | Accessibility Scanner 扫对话页 | 严重项 0（先取基线，超出记进遗留） |

- [ ] **Step 2: 反向验证两条**

- 把 `presence.ts` 里 `if (transport === 'offline') primary = 'muted'` 临时移到 `hasAttention` 之后 → `npx jest test/presence.test.ts` 应红两条（优先级 + 评审案例）；还原。
- 把 `FocusDock.tsx` 的 `pinCommitment` 换成 `items[0]` → `attention-two` 样本里钉住的变成先到的 op1 而不是最早到期（本例相同）……这条反向验证**无区分度**，改用 `commitment.test.ts` 的排序用例（已有）。写进记录：Dock 的排序判据由纯函数测试守，组件层不再重复验。

---

### Task 17: 记录收口

**Files:**
- Modify: `docs/design/2026-08-24-mobile-app-implementation-plan.md`（§7 末尾加一行指针：「UX v2.1 B1 实施记录见 `2026-08-29-mobile-ux-v2-b1-implementation-plan.md` §11」）
- Modify: 本文件 §11 实施记录（读数、遗留、坑）
- Modify: `AGENTS.md` §4.1 Android 行（B1 状态 + 下一步 B2）
- Modify: `mobile/.gitignore`（加 `e2e/artifacts/`）

- [ ] **Step 1: 写实施记录（§6 第 4 批）**：每个任务的读数（jest 计数 234 → N、`tsc` 0、Maestro 06/08/09 时长、真机 13 条打钩表、反向验证结果、画廊帧率主观读数）；遗留（`slot` / `safety_blocked` 无产出方待 Q16/Q19；`reenableBargeIn` 的关再开是权宜、B2 语音层接管；键盘读数与选的路径）；并把前三批 §6 记录里的「遗留」汇总成一张出账表。
- [ ] **Step 2: 提交**

```bash
git add -- docs/design/2026-08-29-mobile-ux-v2-b1-implementation-plan.md docs/design/2026-08-24-mobile-app-implementation-plan.md AGENTS.md mobile/.gitignore
git commit -m "docs(mobile): UX v2.1 B1 实施记录 + 验收读数 + 遗留出账"
```

---

## 3. 任务依赖与并行度

```
T1 tokens ─┐
T2 commitment ─┬─ T3 presence ─┬─ T6 光球（OrbState 单一来源）─┐
T4 store ──────┘               ├─ T8 usePresence ─ T9 组件 ─ T10 接线 ─ T11 隐私栏 ─ T12 键盘 ─ T13 Onboarding ─ T14 画廊 ─ T15 e2e ─ T16 验收 ─ T17 记录
T5 信号 ───────────────────────┘
T7 开关（独立，T10 之前）
```

T1/T2/T4/T5/T7 互不依赖，可由不同 subagent 并行（各自只加自己的路径提交）；T3 依赖 T2；**T6 依赖 T3**（`OrbState` 从 presence.ts 引）；T8 依赖 T3/T4/T5/T7；之后串行。

## 4. 「不负优化」判据在 B1 的取数点（方案 §11.4）

| 判据 | B1 取数 |
|---|---|
| 首反馈时延 | PTT 按下 → 光球 `listening`：`usePtt` 的 `setState('recording')` 到 Composer 重渲，本批不加打点，用 Reanimated 的 `useAnimatedReaction` 打点留到 B2 语音层（那里才有唤醒→听的真实链） |
| 状态可读性 | 画廊 6 张截图（armed / listening / thinking / speaking / attention / muted）给 5 名外部用户——**B2 后统一做**（评审建议的闸在 B2→B3 之间） |
| 承诺不丢 | Task 16 第 4/5 条 |
| 键盘 | Task 12 |
| 性能 | Task 16 第 12 条（主观）；同屏循环动画实例数：对话页常态 = Composer 主球 1 个（顶栏球只在 busy 时动） |
| 无障碍 | Task 16 第 13 条 |
| 回归 | `npm test` 条数只增不减；hmi node:test 288 不变（本批不碰 hmi） |

## 5. 实施判断（写在开工前，做的时候撞到再补）

1. **`Msg` 不能加字段**（共享类型）⇒ 「发送状态未知」「过程首帧时刻」「错误出现时刻」全部用并列结构（`uncertainIds` / `seenAt` map）；到期留痕用**追加一条消息**而不是改原气泡。
2. **剪枝调度改 `setTimeout` 链**而不是继续 `setInterval`：判据（`prunePendings`）一字不改，只改触发粒度；测试用假时钟量「300s 整出账」。
3. **`slot` / `safety_blocked` 只有类型与样本**：协议里没有 `missing_slots`、没有 VAL 拒绝标记，客户端不许猜。方案 Q16 已挂 `confirm_policy`；本计划**新增 Q19：`final.missing_slots`**（NEED_SLOT 的结构化字段）——写进方案 §13 由泓舟排期。
4. **回声「重新开启插话」= 关再开免唤醒**：voiceLoop 的 `_bargeInDisabled` 会话级不可恢复，B1 不改共享 FSM；B2 语音层给它正式出口。
5. **`audio_echo_degraded` 的可见性依赖 M4-R1 那条「回声防线拿不到播报文本」的修复已提交但真机未复验**（AGENTS.md）——B1 验收若 Dock 从不出现这条，先证明 `onDisableBargeIn` 有没有被调到（Task 5 的单测证明接线，真机证明触发），别把「没出现」读成「修好了」。⚠ 同一条上还有一个变量：`96a6830` 用 patch-package 给 `react-native-audio-api` 的 Oboe 输入流改了 `VoiceCommunication` 预设（**开平台 AEC**）——那是 C++ 侧改动，`postinstall` 会把 patch 打进 `node_modules`，但**只在下一次原生构建后才进 APK**。B1 不重建，所以真机上的回声读数取决于装的是哪次构建：写读数时带上 APK 的构建时间（`adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime`），并标明「无 AEC」还是「有 AEC」——两台状态不同的机器上「回声 Dock 出没出现」不能互相比。
6. **顶栏品牌球不读 `primary`**：方案 §5.1 写明它只 idle/thinking；状态锚是 Composer 主球，两个球同时表达状态会打架。
7. **画廊 23 个光球同屏全动**：取证屏不管帧率；但如果真机滚动明显掉帧，记读数、不改（B4 的 reduce-motion 会给画廊一个静帧开关）。
8. **Task 12 的两条路径都要留 Maestro 08**：读数是「今天这台机、这个输入法」的，流才是以后不回退的判据。

---

## 6. 实施记录（分批回填；每批一个会话，写完即停）

> 格式照 `2026-08-24-mobile-app-implementation-plan.md` 的 M 段实施记录：先读数、再做了什么、再「本批踩的坑」、最后「遗留 / 给下一批的话」。读数只写自己跑出来的数，不复述计划里的预期。

### 6.1 第 1 批「纯逻辑层」（T1–T5）

- 开工基线：`npm test` **22 suites / 235 tests 全通过**（50.9s；计划里写的 234 是当时读数，开工当日实为 235）/ `tsc` **0 error**（2026-08-29，worktree：**主工作树**——泓舟未授权分树，按 §0.1 「没有分树就在主工作树做」）
- 完成任务与提交（并行度：T1/T2/T4/T5 四个 subagent 同时开跑，T3 等 T2 落地后起；每个只提交自己的路径）：

| 任务 | commit | 文件 / 行 | 新增用例 |
|---|---|---|---|
| T1 tokens | `3cc6b74` | 2 文件 +85 | 7 |
| T2 commitment | `1471f34` | 2 文件 +130 | 6 |
| T4 store 账本侧 | `64b2bc6` | 2 文件 +144 −18 | 4（该文件 25 → 29） |
| T5 四路信号 | `1538bc0` | 7 文件 +143 −12 | 3（presenceSignals 2 + handsFree ⑤） |
| T3 derivePresence | `152b738` | 2 文件 +373 | 30 |
| （基线记录） | `9f3a133` | 本文件 §6.1 1 行 | — |

- 读数：收口 `npm test` = **26 suites / 285 tests 全通过**（20.8s），`tsc` **0 error**。相对基线 **+4 suites / +50 tests**，逐条对得上：7+6+30+4+2+1=50。`npm test` 条数只增不减（§4 回归判据）满足；本批未碰 `hmi/`。
- 本批踩的坑：
  1. **计划自己指定的那次反向验证是空转的**（T3，本批最值钱的一条）。按 Step 5 原样交换 `primary` 里 `hasAttention` 与 `capture === 'looking'` 两行，`30 passed, 30 total`——**一条都不红**。查证：「逐对交换」那条实测覆盖的是 attention↔listening（`ptt:'recording'` 的 capture 是 `listening` 不是 `looking`）、looking↔speaking、offline↔全部，**唯独没有一处让 attention 与 looking 同时在场**，而那正是被交换的相邻对。补一条共存断言后变异红在 `test/presence.test.ts:72`（Expected `attention` / Received `looking`），还原即绿。判据：**变异测试要盖的是「被改的那一对」，不是名字相近的那一对**——测试名写着「逐对交换」不等于它逐对覆盖了。若不做这步，commit message 里那句「交换 attention/looking 顺序即红」就是一句假话。
  2. **T4 第 1 条用例的时序断言是搭便车的**。计划的预判成立：旧 `setInterval(30s)` 在 300s 整那一跳恰好命中，299s / 300s 两条断言**碰巧全绿**，真正驱动这次改动的红只有「确认已过期」那条文案断言。⇒ **「按项到期精确调度」本身在单测里没有被独立证伪过**。要真守它，得挑一个不是 30s 整数倍的到期时刻（例如让 `ts` 偏移 7s）。这条**留着没补**，出账在此。
  3. **四个 subagent 共享一个工作树**：没撞 `index.lock`（各自 `git commit -- <pathspec>` + 重试兜底），但 **`tsc` 是全项目扫描，会看到别人半途的文件**——T1 看到 T2 的、T2 看到 T4 的、T4 看到 T5 的，各报了一轮对方的过渡态错误。约定「错误落在自己文件里才算自己的红」有效且必要。另：全量 `npm test` 只由协调者在收口时跑一次，四路各跑会抢 CPU 且读数互相污染。
  4. **另一条会话（car-agent-08）的云端发版闸要求工作树 `--porcelain --untracked-files=normal` 全空**，被本批在飞的 TDD 循环挡住。处置：立刻把自己那 1 行文档提交掉（`9f3a133`）解开一个，其余等收口——**不为了让树干净去打断跑到一半的 TDD 循环**（半截提交比等 15 分钟贵）。同时**拒绝了它「顺带把你几个提交一起 push」的提议**：它手上那份 push 授权是给它那批的，覆盖不到本条线。
  5. **照抄计划前先核真实结构**：计划 (e) 说 `usePtt` 有「三处 `setError`」，真实代码只有**一处**（permission / start 是它内部的三元）。落法改成提一个 `const denied` 给 `setError` 与 `setErrorKind` 共用——`instanceof` 只求值一次，**同一判据不写第二份**。
  6. 基线本身与计划记的不一致：计划 §0.1 写「当前 234 通过」，开工实测 **235**。读数有效期只到下一次改动为止。
- 遗留 / 给第 2 批的话：
  1. **`presence.ts` 里 `hfListening` 是死变量**（声明后无消费方，计划代码块的残留）。计划说「若 `tsc` 报红就删」——实测**没报**：`expo/tsconfig.base` 的 `noUnusedLocals` 是 `undefined`，且仓库**没有 eslint 配置**（`npx eslint` 报找不到 `eslint.config.js`）⇒ 当前**没有任何门禁能抓到它**。按「条件未成立不许删」保留原样。第 2 批碰这个文件时顺手删掉，或让它有真实消费方。
  2. **「逐对交换」这条测试的名字仍强于它的覆盖**：本批只补齐了被变异触及的 attention↔looking，**listening↔speaking / speaking↔thinking / thinking↔followup / followup↔armed 四对仍无共存用例**。T6 光球直接读 `primary`，接线前值得补齐——否则改错优先级顺序时红不出来。
  3. **`queued` 计数依赖 `transport.send()` 返回 `false`**。单测里是替身写死返回 false；**真实 `GatewaySession.send` 断线时到底返不返 `false`，本批没有验证过**。T10 接线或第 4 批真机验收要单独确认——否则 Dock 的离线队列项永远不出现，而「没出现」会被读成「没有排队」（同 §5 第 5 条那个形态：别把「没触发」读成「修好了」）。
  4. **`isVisionCapturing()` 目前零消费方**（`subscribeVisionCapturing` 有测试覆盖，它没有）。T8 `usePresence` 若不接它，收口时该删。
  5. **`useHandsFree` / `usePtt` 两个 hook 本批无单测覆盖**（`mobile/test/` 原本就没有它们的套件）：新增的 `bargeInDisabled` / `pipelineDegraded` / `errorKind` 目前只有 `tsc` 与下游 `ChatScreen`/`Composer` 的类型面在守，真正验到它们的是 T8 / T10。
  6. `uncertainIds` **只在 `open → closed` 那一跳写入**；`connecting → closed` 不标。这是设计语义（残留窗只在真写过 socket 之后才存在），**不是漏标**，记一笔防后续误读。
  7. `setStatus` 现在**对同值状态早退**。`wiring.ts` 的 `onStatus` 若重复推同一状态不再触发订阅；当前无消费方依赖那个副作用，接线批留意。
  8. `Jest did not exit one second after the test run has completed` 是 `handsFree.test.ts` 的**既有**噪声（在 Step 2 那次红跑里就已经在了，那时实现一行未改），不是本批引入。
  9. **未推送**：本批 5 个代码提交 + 1 个文档提交，连同批前的 `141d60f`，都还在本地 `main`。`git push` 需泓舟单独授权。
  10. 本批期间 `AGENTS.md` 一度带着 100 行未提交改动，**我据此在初稿里判了「树里有第三条线」——错的**：那是 car-agent-08 那条线的，已随 `e5e85b8` 提交。它同一时间也把我的 `git status` 采成了旧的（漏了 T3/T4/T5 三个已落提交）。**一小时里我们互相采到对方的中间态各两次** ⇒ **共享树里 `git status` 的有效期只到下一次别人落盘为止**，据它下任何判断前先重采一次。§0 第 3 条对 T17 的要求不变：动 `AGENTS.md` 前先 `git diff --stat -- AGENTS.md` 核行数。

### 6.2 第 2 批「光球与取证屏」（T6 / T7 / T8 / T9 / T14）

- 开工基线：`npm test` **26 suites / 285 tests 全通过**（20.4s）/ `tsc` **0 error**；
  `scripts\check_android_env.ps1` **18 pass / 0 warn / 0 fail**、退出码 0
  （2026-08-29 夜～08-30 凌晨，worktree：**主工作树**——泓舟仍未授权分树）
- 完成任务与提交（并行度：T6/T7 两个 subagent 同时开跑，T8 → T9 → T14 串行；
  §0.1 的两条附加项在 T6 之前先做，各自单独提交）：

| 任务 | commit | 文件 / 行 | 新增用例 |
|---|---|---|---|
| 附加项① 四对共存用例 | `74f907e` | 1 文件 +42 | 4 |
| 附加项② 删死变量 `hfListening` | `b673750` | 1 文件 −1 | — |
| T6 光球三新态 | `71f8ed7` | 1 文件 +99 −5 | 0（任务规格即无 jest） |
| T7 实验室开关 + `deviceRole` | `4fe6714` | 5 文件 +54 | 3 |
| T8 activityLog + usePresence | `131ac13` | 3 文件 +199 | 3 |
| T9 胶囊 + Focus Dock | `9787cf4` | 2 文件 +247 | 0（同 T6） |
| T14 状态画廊 | `cd77cbb` | 3 文件 +137 −3 | 3 |
| T14 补 Dock 分支守卫 + `offline-queue-only` 样本 | `dd1a6c9` | 2 文件 +28 −1 | 1 |
| T15（**提前**）Maestro 09 | `e0a74cc` | 1 文件 +40 | — |
| T14 修两条假「真栈可产」+ 产出方守卫 | `4bf7a16` | 2 文件 +26 −3 | 1 |
| 截图目录不入库 | `0400867` | 1 文件 +4 | — |

- 读数：收口 `npm test` = **28 suites / 300 tests 全通过**（42.6s），`tsc` **0 error**。
  相对基线 **+2 suites / +15 tests**，逐条对得上：4+3+3+3+1+1=15。`npm test` 条数只增不减
  （§4 回归判据）满足；本批未碰 `hmi/`、未改编排核心。
- 真机读数（Mix Fold 4 `5d432b6d`，显示屏 id `4630947090644569220`；**Metro 用的是工作树里
  别的会话已经起着的那一个 dev server**——先核过它的命令行确认服务的就是本仓 `mobile/`，
  自己再起会撞 8081）：
  - 截图 `mobile/e2e/artifacts/b1-14-gallery-{dark,light}-{1..7}.png` 共 14 张，深浅各一套、
    各自从表头滚到表尾，24 条样本全覆盖。**不入库**（`0400867` 已 gitignore 该目录：
    全仓从来没有提交过验收截图，且**截图是取证不是读数**）。
  - 三个新光球态真机可见：`attention` 琥珀环（`dark-4`）、`muted` 去饱和 + 灰环（`light-5`，
    与同屏其它彩色球对比明显 ⇒ `filter: [{ saturate: 0.4 }]` 在 Android 上确实生效）；
    **`looking` 快门白环是 300ms 一次性动画，截图抓不到**，本批**没有**它的静态取证（出账遗留⑥）。
  - 评审 P0-1「待确认时断网」在 `light-5` 上成立：`primary=muted`、胶囊「已断开 · 消息会排队」，
    而琥珀确认卡**照样钉着**（`4:15 后过期` + 「另有 1 个待处理 ›」）。
  - Maestro 09：**通过**，**退出码 0**（专门单跑一趟只为拿退出码——管道里 `tail` 的退出码是老账），
    实跑 5 分 10 秒，其中约 40s 是 dev-client 起 + bundle。
  - 画廊滚动主观：24 个动画光球同屏，滚动跟手、无明显掉帧。`adb shell input swipe` **300ms 以内
    的快扫会被列表当成 fling 且实际位移极小**（换 400ms 才稳）——这是取证脚本的事、不是渲染卡；
    我头一套深色截图就是这么只拍到了前 10 条，重拍才补齐。
  - 截图右上角那个半透明齿轮浮标**不是 App 的**（`_layout.tsx` / `state-gallery.tsx` grep 无 FAB），
    是设备上的系统/开发浮窗；它会盖住第一条样本的 chip，看图时别读成 UI。
- 本批踩的坑：
  1. **我自己的第一次变异验证是空转的，而且它「全绿」**（本批最值钱的一条）。四对共存用例写完跑变异，
     脚本里写的是 `/tmp/presence.orig.ts`——MSYS 会把**命令行参数**里的 `/tmp/mut.py` 转成 Windows 路径，
     但**脚本源码里的字符串字面量不转**，Python 按 cwd 解析成 `D:\tmp\...`，四次「变异」一次都没落盘，
     四轮读数全是 `34 passed`。**只看 PASS 数就会把「一次都没变异」读成「测试很稳」**（第 1 批那条
     「逐对交换」是同族老账：绿在了没被交换的那一对上）。⇒ **变异测试的第一条断言是「变异真的发生了」**：
     改完先 `git diff --stat` 核一眼，再读测试读数。
  2. **四对里有两对根本不能在 `primary` 链上验**。`primary` 的 `speaking` / `thinking` / `followup`
     三条分支都读**单值**的 `agent`，两两永不同真 ⇒ 交换它们是**空操作**，写在那儿的断言无论如何都绿。
     真正的优先级住在 `agent` 轴自己的三元链里。⇒ 这两对同时钉 `agent` 与 `primary`，逐对变异确认
     各只红自己那条（第四对 followup↔armed 连带红了既有参数化例，符合预期）。
     **「给每一对都写了断言」不等于每一对都被验到——先问这一对的裁决点在哪一层。**
  3. **计划里 Maestro 09 那四条断言实跑红了三条，三个成因互不相同**，而三条红看起来都像
     「东西没渲出来」：① `"另有 1 个待处理"`——Maestro 的文本选择器是**整串正则不是包含匹配**，
     屏上是「另有 1 个待处理 ›」，**hierarchy 里那行明明在**（去 dump `screen-hierarchy/*.json`
     才分得清「断言红」与「没渲出来」）；② `"条消息排队中"`——在原 `only=` 集合下**永远渲不出来**
     （见坑⑧），是样本集的洞不是断言的错；③ `"环境回声较强"`——在屏幕外，`assertVisible` 不会自己滚。
  4. **`scrollUntilVisible` 在这块屏上判不出可见，而 `scroll` + `assertVisible` 同一目标当场绿**。
     它确实滚到了（失败瞬间的 hierarchy 里目标文本就在），换 `id` 选择器、把 `visibilityPercentage`
     从 100 降到 50 都照红。⇒ **两条命令的「可见」判定不是同一个**。最终改成「滚到触底
     （`repeat: 6`，滚过头无害）再断言」，三条判据全写进流的头注释——防的是下一个人把它改回去。
  5. **画廊里的倒计时会走，但那恰恰不能当成「倒计时是对的」的证据——方向是反的。**
     `CommitmentCard` 的秒表 `useEffect` 依赖 `[item]` **对象引用**；画廊喂的是 `useMemo` 只算一次的
     静态 snapshot，引用稳定 ⇒ 秒表正常跑（真机实测 `4:28 → 4:15` 在走）。而生产路径上
     `usePresence` 每秒 tick、`derivePresence` 每次 `.map()` 现造新的 `DockItem` ⇒ **每秒 cleanup +
     重建 interval，本地 `now` 会冻在挂载那一刻**。**取证屏与生产路径在这一点上的输入形态相反，
     画廊绿证明不了生产绿**（修法与验证入口见遗留②）。
  6. **同一块屏上「0s 后过期」有两种成因**。头一轮截图里两张确认卡都写着 `0s 后过期`、进度条空——
     不是倒计时坏了，是 **expo-router 复用了已挂载的画廊屏**：`useMemo(() => presenceFixtures(), [])`
     只在挂载时取一次时间基准，而我隔了 18 分钟用 `openLink` 再进来，样本早过期。`force-stop` 后
     重进就是 `4:28`。⇒ **取证前先 force-stop**，别用深链去「刷新」一个有时间基准的屏。
  7. **取证屏自己写的标记也是断言，也会漂**。`deg-recoverable` 与 `deg-fatal` 两条挂着「真栈可产」，
     可全仓 grep，`usePresence` 只 push 四种降级、这两种一次都不产——那个 chip 就是**截图上的一句假话**，
     而它恰恰是这块屏用来分「读数」与「样本」的唯一标记（同 card-gallery 头注「样本截图不是读数」）。
     ⇒ 改成 `producible: false` + 一条**从产出方源码盘点**的守卫（`4bf7a16`），守卫里带观测通道自检
     （正则被改坏 ⇒ 空集 ⇒ 把每条样本都判成说谎，那时红指向测试自己不指向样本）。
  8. **覆盖度守卫守的面比它的名字窄**：只守 `primary`（8）与 `degradation`（7），**不守 Dock 的四个分支**，
     而 Maestro 09 的断言全落在 Dock 分支文案上。实测（对样本逐条跑 `pinCommitment`）：
     confirm 4 条 / task 1 条 / **queue 0 条** / slot 0 条。queue 是 0 不是漏写样本，是排序：
     带 `queued` 的两条样本都同时带 `pendingOps`，confirm rank 0、queue rank 4 ⇒ queue 只进
     「另有 N 个」的计数。补第四条守卫（`dd1a6c9`）之后这条缝才有人看着。**`slot` 刻意不进守卫**
     ——`derivePresence` 没有产出它的代码路径，进了守卫只会逼人手搓假 snapshot 来喂绿灯。
  9. **expo-router 的 typed routes 闸依赖「有没有 dev server 在跑」**（T7 实测）：
     `.expo/types/router.d.ts` 是 gitignore 的本地生成物、由 dev server watch 生成，`tsc` 自己不生成它。
     推论两条：干净 checkout（CI）里这道 href 闸**根本不存在**，**CI 的 tsc 绿证明不了路由注册了**；
     反过来本地没起 dev server 时新建路由会**假红**——那时别去改 `Link`。
  10. **新建文件的提交要多一步**：`git commit -- <未跟踪路径>` 会报 `pathspec did not match`，
      得先 `git add -- <路径>` 再**紧接着同一条命令链**里 commit（中间不许停下来做别的事——
      已暂存未提交比未暂存更有害，会污染别的会话的发版闸）。另：`git commit` 的 `-m` 必须写在 `--` 之前。
      共享树里全程有别的会话在写 `AGENTS.md` / `scripts/probe_qa_regression.py` /
      `docs/design/2026-08-27-…`，pathspec 隔离全程有效，11 个提交无一夹带。
- 遗留 / 给第 3 批的话：
  1. **`uxV2Presence` / `uxV2Dock` 两个开关目前零消费方**——「设置页有开关、关了没反应」比没开关更糟。
     T10 接线时**必须让 v2 组件读这两个开关**，否则回滚路径（§11.5）只是一句话。
  2. **确认卡倒计时在生产路径上大概率冻住**（坑⑤）。两条一行修法：① `useEffect` 依赖换成
     `[item.kind, item.id]`；② 更好——删掉组件里那份本地时钟，`now` 从 snapshot 走
     （`usePresence` 已经在每秒 tick，本地这份是第二个不同步的 1s 时钟）。**本批没改**：组件还没有
     消费方，改了也无法证伪。验证入口：T10 接进 ChatScreen 后触发一次危险动作确认，盯 15 秒看数字动不动。
     ⚠ 连带：`dock-confirm` 挂着 `accessibilityLiveRegion="assertive"` 且倒计时是它的后代——秒表一旦
     真跑起来，TalkBack 会**每秒重播整张卡**；修①的同时要把 live region 挪出倒计时子树或降成 `polite`。
  3. **`usePresence` 有三个字段喂不出正确的值**（T8 按计划原样落，未擅自改）：
     - `driving` **实质恒为 false**：`Msg.driving` 全仓只由 `process` 帧写，空闲/简单轮/纯流式全是 false。
       真信号该来自 `vehState`，但「哪个键算行车」的判据**目前不存在**，写进收集器就是第二份判据
       （撞方案自纠的那条「车速阈值不许进 UI、VAL 才是安全裁决点」）。⇒ T10 按已有判据接，别在收集器里判。
     - `lastError.at` 是「hook 第一次看见它」不是「它发生的时刻」（`Msg` 无时间戳，靠本地 `seenAt` 登记）。
       后果：挂载时若历史最后一条是 error 气泡，会闪一次 4s 红胶囊。
     - `connChangedAt` 挂载时被重置成 now：挂载那刻已经是 `connecting` 的话，「正在重连…」最晚迟 3s。
  4. **秒级 `setInterval` 无条件常开**：全空闲时也每秒 setState。`derivePresence` 本身不贵，
     但 T10 接进 ChatScreen 后要看它会不会带着 FlashList 与光球动画一起重渲。
  5. **两条未验前提原样搁置**（第 1 批遗留③⑦；本批既没动它们，也没写任何依赖它们成立的代码）：
     `queued` 只由 `transport.send()` 返回 `false` 驱动，真实 `GatewaySession` 断线时到底返不返 `false`
     **仍未验**——`offline-queue-only` 那条样本标着「真栈可产」正是压在这个未验前提上；
     `setStatus` 对同值状态早退。T10 或第 4 批真机验收要单独确认。
  6. **`looking` 快门环没有静态取证**（300ms 一次性动画，截图抓不到）。要么第 4 批录一段屏，
     要么接受「只有类型与代码路径被守着」——别把「画廊里有这条样本」读成「白环验过了」。
  7. **B1 不可达的那几种，不是本批的洞**：`slot` 分支（`derivePresence` 无产出路径 + 协议无
     `missing_slots`）在 B1 完全不可达；`recoverable_error` / `safety_blocked` / `fatal` 三种降级
     同样无产出方（样本上已标「仅样本」）。这些挂账方案 §13 Q16 / Q19 的后端。
  8. **`PresenceCapsule` 是 `Pressable` 但 `accessibilityRole="text"`、`minHeight` 只有 26dp**
     （远低于 `TARGET.parked=48`）。作为状态读出这是对的；T10 一旦给 `onPress` 接真实动作，
     它就成了一个读屏不播报、热区又不达标的可点区域——接线时再判一次。
  9. **`DegradationRow` 用 `key={d.kind}`**：今天安全（每种 kind 上游最多 push 一次），
     将来出现同 kind 两条（mic + camera 两个 `permission_denied`）就是 React key 冲突。
  10. **计划自身一处不一致，本批按收口判据处置**：§0.1 第 2 批收口判据要求「Maestro 09 通过」，
      而 Task 15（06 + 09 + README）整体排在第 4 批。我把 **09 提前落了**（`e0a74cc`），
      **06 流与 `e2e/README.md` 仍留给第 4 批 T15**（README 里那三条流的状态与读数也一并留给它）。
  11. **光球在浅色主题下对比度明显偏低**（`light-1` / `light-4`：极光球压在磨砂白底上糊成一团淡紫）。
      这是 B1 之前就有的形态、不是本批引入，但状态画廊第一次把 24 个球并排放在浅色底上，一眼可见。
      记一笔给 B2/B3 的视觉批。
  12. **未推送**：本批 11 个提交，连同第 1 批那 6 个，都还在本地 `main`。`git push` 需泓舟单独授权。

### 6.3 第 3 批「接线」（T10–T13）

- 开工基线：`npm test` **28 suites / 300 tests 全通过**（89.7s；第 2 批收口读的是 42.6s，同一套用例——
  跑批耗时跨趟不可比，只有条数与红绿可比）/ `tsc` **0 error**；
  `scripts\check_android_env.ps1` **18 pass / 0 warn / 0 fail**、退出码 0（设备 `5d432b6d` 在线）
  （2026-08-30，worktree：**主工作树**——泓舟仍未授权分树）。
  ⚠ 与第 2 批遗留⑲不同的一处事实：第 1/2 批那 17 个提交**已经在 `origin/main` 上了**
  （开工时 `git log origin/main..HEAD` = 0，不是我推的；共享 main 上 push 的粒度是分支不是提交）。
- 完成任务与提交（串行；共享树里中间夹着别的会话的两个提交 `5c21e21` / `b16cfb7`，pathspec 隔离全程有效）：

| 任务 | commit | 文件 / 行 | 新增用例 |
|---|---|---|---|
| （开工基线） | `98963fc` | 本文件 +6 −1 | — |
| T10 对话屏接线（含附加项②③⑤） | `fa9b4f9` | 8 文件 +367 −83 | 12 |
| T10 附加项④ 秒级时钟停表 | `fffee7b` | 1 文件 +27 −8 | 0 |
| T11 隐私栏 + 顶栏采集点 | `5833a39` | 3 文件 +203 −9 | 0 |
| T12 键盘避让 + Maestro 08 | `561a37a` | 4 文件 +43 −14 | 0 |
| T13 Onboarding 上品牌 | `542cb00` | 1 文件 +201 −156 | 0 |

- 读数：收口 `npm test` = **29 suites / 312 tests 全通过**（62.9s），`tsc` **0 error**。
  相对基线 **+1 suite / +12 tests**，逐条对得上：`presenceSeen` 10 + `presence` 的 now 透传 2。
  条数只增不减满足；本批未碰 `hmi/`、未改编排核心。
- 真机读数（Mix Fold 4 `5d432b6d`，显示屏 id `4630947090644569220`；**Metro 复用别的会话已起着的
  dev server**——先核过它的命令行确认服务的就是本仓 `mobile/`。截图存 `mobile/e2e/artifacts/`，不入库）：
  - **a) 危险动作进 Dock**：「打开后备箱」→ `dock-confirm` 出现（琥珀实色卡 + 「危险动作 · 需二次确认」
    + 进度条 + 取消/确认 1:2），胶囊「等你确认」，光球转 `attention` 琥珀环，**气泡内确认按钮消失**；
    倒计时 **`4:57` → `4:22`**（35s 间隔，真的在走 ⇒ 附加项②修掉了生产路径上的冻表）；
    点「取消」→ Dock 消失 + 上行「取消」+ 「好的，已为您取消。」
  - **b) 两开关回滚**（`b1-10-dock-off-inline-confirm.png` / `b1-10-v1-rollback.png` / `b1-10-v1-ptt-hint.png`）：
    关 `uxV2Dock` → Dock 不再出现、**确认按钮回到气泡内**，胶囊仍在（两个开关各管各的轴）；
    关 `uxV2Presence` → 顶栏回 v1 连接 pill（绿点+「在线」）、底部回「待唤醒 · 说「小舟小舟」」、
    胶囊消失、光球回 v1 三态；长按光球验第四条窄条 → 「识别中…」也回来了。
  - **c) 隐私栏**：待机 `b1-11-privacy-armed.png`（「唤醒词待机（端侧监听，不上传）」+
    「最近一次 麦 09:10 按住说话」← activityLog 端到端通了 + 「token ····j2_Z」）；
    PTT 中 `b1-11-privacy-ptt-top.png`（胶囊「● 在听…」+ 采集点青）；
    s2s 收音 `b1-11-capture-dot-s2s.png`（采集点**琥珀**）。采集点三态真机全见：不渲染 / 青 / 琥珀。
    ⚠ **`cloudAudio` 那一行的隐私栏截图没取到**（见遗留①）。
  - **d) 飞行模式（附加项⑥，第 1/2 批那条未验前提到此为止）**：发出后 **3 秒**就出
    「1 条消息排队中，连上后自动补发」——**不需要等 30s 判死**；同屏健康点红、胶囊「已断开 · 消息会排队」、
    光球 `muted`。⇒ **`transport.send()` 在真实断线时确实返 `false`，队列语义成立**。
    关飞行模式后约 2 分钟自动重连（指数退避，不是立刻）：健康点转灰、Dock 消失、胶囊回 armed。
  - **e) 键盘读数 = 遮（两处都遮），选 A 修法**：对话页 `b1-12-chat-kbd.png` 输入框/光球/发送键
    **整个被盖住**、页面不上移；Onboarding `b1-12-onboarding-kbd.png`「保存并进入」被完全盖住。
    修后 `b1-12-chat-kbd-after.png` Composer 整体在键盘上方。**Maestro 08 通过、退出码 0**
    （专门单跑一趟取退出码），反向验证：`behavior` 改回 `undefined` → 08 在
    `assertVisible composer-send` 当场 FAILED，还原即绿。
  - **f) Onboarding 深浅**：`b1-13-onboarding-{dark,dark-2,light,light-top}.png`，改造前留档
    `b1-13-onboarding-before.png`（两张并排看差别最直观）。
  - **APK 构建时间：本批零构建**（B1 全 JS、走 Metro 热载；`check_android_env.ps1` 18 pass / 0 fail）。
    **AEC 档位**：`mobile/patches/react-native-audio-api+0.13.3.patch` 的
    `setInputPreset(oboe::InputPreset::VoiceCommunication)`（平台 AEC 那条路），本批未动。
- 本批踩的坑：
  1. **「抓不到 cloudAudio」我先归因成时序，真因是那个开关根本没切中**（本批最值钱的一条）。
     点了「端到端」没当场核选中态就往下走，之后用「多点触控注入不生效 / 松手时序太紧」解释了三轮，
     每一条都自洽、都能独立成立。回到设置页才看见它还停在「三段式（默认）」。
     ⇒ **改完一个设置，当场截图确认它真的变了再往下走**；一个自洽的错误解释比没有解释更难被推翻，
     而它最爱出现在「我刚操作过这里」的时候（同族老账第 N 次）。
  2. **隐私面板上有一句假话，而「说的是真的」正是这块屏存在的全部理由**。原文案
     「本机处理（唤醒词待机 / 转文字后只上传文字）」把两件事并成一句——App 的 PTT 走的是
     **服务端** ASR（`core/voice/asr.ts` 连 `ws://…/api/asr/stream`），录音那一刻音频是上传的。
     更糟的是 `test/presence.test.ts` 那条用例名把这个错误理由**焊死成了预期行为**
     （「PTT=edge（三段式只上传文字）」）。⇒ 判据层 `edge` 并了「端侧待机」与「音频上传给 ASR」两件事，
     B1 先在 `PrivacyRail.micText` 用文案分开、断言不动、把正确的理由写进注释留给下一个人。
  3. **`secureTextEntry` 输入框聚焦时 `screencap` 全黑**（HyperOS 的截屏保护），稳定复现两次。
     我差点把它读成「Onboarding 在键盘弹起时崩了/白屏」。换非密码框当场截图正常 ⇒ 证伪。
     **任何「没看到 X」的结论，先证明观测通道还开着**。
  4. **停表的读数必须两头都取**。只证明「空闲不再 tick」（10s 内 ChatBody 11→**0** 次、
     可见气泡 88→**0** 次）不够——那也可能是把表停死了；还要证明「该走时真的走」
     （有待确认时 11 次/10s、倒计时 4:35→4:20）与「渲染没被冻住」（一次真实重挂载仍 1/8）。
  5. **Maestro 08 第一趟红在最后一条断言上**，而那趟是热载中途起的 app；`force-stop` 后连跑两趟都绿，
     加上反向验证红，才定性成「工具时序」而不是 flaky。
  6. **PowerShell 的 `>` 会损坏二进制**：`adb exec-out screencap > x.png` 在 PS 里会写出带 BOM 的坏 PNG
     （`ef bb bf ef bf bd 50 4e`）。截图一律走 bash。
  7. **反引号在 bash 的 commit message 里会被当命令替换执行**——T12 那条 message 里三处
     `` `artifacts/xxx.png` `` 被替换成空，报 `No such file or directory` 才发现。
     ⇒ 多行 message 用 `git commit -F -` + heredoc（已 amend 修回）。
  8. **飞行模式验不到探活**。飞行模式下 socket 立刻失效、`send()` 当场返 `false`，
     `ws.mjs` 头注说的那个「RN 上 onclose 不来、帧被写进死 socket」的窗口**根本不出现**
     ——所以这次验的是队列语义，**不是**探活。要验探活得换断网形态（关 AP / 丢包）。
  9. `input motionevent DOWN/UP` 在这台设备上不驱动 RN 的 `Pressable`（想「按住光球的同时点顶栏」
     取 cloudAudio 隐私栏截图，三种注入法全部无效）。
- 遗留 / 给第 4 批的话：
  1. **`cloudAudio` 的隐私栏那一行没有截图**（adb 的多点触控注入不驱动 RN Pressable，按住光球时点不动顶栏）。
     该态由三条独立证据成立：真机采集点变琥珀、`presence.test.ts` 的 `cloudAudio` 断言、`micText` 源码分支。
     第 4 批若录屏可补一张。
  2. **`privacy.mic` 判据层三档并了两件事**（坑②）。要不要加第四档 `cloudAsr` 是 B2/B4 的裁决——
     它会牵动 `PresenceSnapshot` 类型、画廊样本与覆盖度守卫，B1 刻意不动。
  3. **排队消息补发之后没有第二次答复上屏**：那一轮已被 95s 看门狗以「响应超时了，请稍后重试。」收尾，
     重连后 Dock 消失、`queued` 清零，但没有新气泡。**这是现象记录，未定性**——后端到底收没收到、
     回没回，要查 trace 才知道。UI 文案承诺的是「连上后自动补发」，值得第 4 批查一次。
  4. 恢复网络后**不是立刻重连**（指数退避，实测约 2 分钟）。这期间胶囊一直说「已断开」，是对的。
  5. 第 2 批遗留⑪（浅色下光球对比度偏低）在 Onboarding 顶部第二次出现（`b1-13-onboarding-light-top.png`）。
     不是本批引入，继续记给 B2/B3 的视觉批。
  6. `usePresence` 的 `driving` 恒 false 与 `connChangedAt` 挂载重置**按既有判据原样接**（附加项③要求），
     未在收集器里发明判据；`lastError.at` 那条已修（`SeenRegistry.seed`）。
  7. **Maestro 06 与 `e2e/README.md` 仍留给第 4 批 T15**（第 2 批已把 09 提前落了）。
  8. **未推送**：本批 6 个提交仍在本地 `main`。⚠ 与第 2 批遗留⑫不同的是：**第 1/2 批那 17 个提交
     开工时已经在 `origin/main` 上了**（不是我推的）。`git push` 仍需泓舟单独授权。

### 6.4 第 4 批「验收与记录」（T15–T17）

- **开工基线**（2026-08-30）：`check_android_env.ps1` 退出码 **0**（18 pass / 0 warn / 0 fail）；
  `npm test` **29 suites / 312 tests 全绿**（39.7s）；`npm run typecheck` **0 error**；
  工作树干净、`origin/main..HEAD` 只有 1 个提交（`95c1d02`，本批开工前的计划文档）。
  与第 3 批收口读数逐字一致。**收口读数**：jest **29 suites / 315 tests**（+3，见附加项①）、`tsc` 0。

- **附加项①（T16 前置）：离线期间暂停看门狗、重连后整 95s 重新起表** —— `59b742b`
  - 机制（第 3 批遗留③的定性）：飞行模式下 RN 的 `onclose` 不来，靠 HTTP 探活 `reconnectNow()`
    判死；退避重连实测约 2 分钟。旧行为里那一轮的 95s 表在断网期间就跑完了——气泡被收成
    「响应超时」且已从 `registry` 注销 ⇒ 重连 flush 后回来的 `final` 带着一个已注销的
    `request_id`、按「对不上＝丢帧」被丢，**用户永远拿不到答案**。
  - ⚠ **计划写的修法只覆盖一半**。计划说「`setStatus('closed')` 时对在飞 id 摘表」，
    但第 3 批观测到的现象是**帧入队**——`ws.mjs::send()` 只在 `readyState !== OPEN` 时返 false，
    也就是**发送那一刻链路已知非 open**（退避重连期间状态在 `closed`↔`connecting` 之间摆动、
    不会回 `open`）。只按跳变摘表，这条最常见的路径照样漏。⇒ 实现取**并集**：
    ① `setStatus` 非 open ⇒ 摘表记入 `pausedWatchdogs`，open ⇒ 各自重新起整 95s；
    ② 表跟着**链路**走、不受 `connStatus` 同值早退影响；
    ③ `armWatchdog` 在链路已知断开（`linkDown`）时**不起表**，把 id 直接寄存。
    `linkDown` 初值 `false`＝「还没人告诉过我链路状态」——既有测试从不驱动 `setStatus`，
    行为逐字不变（3 条既有看门狗用例零改动）。`uncertainIds` / `registry` / `inFlight` 语义不变。
  - 测试（`sessionStore.test.ts` +3，假时钟）：在飞轮 150s 仍 pending → 重连后 94s 仍 pending、
    96s 才超时／closed 150s → open → 30s 收到 final 正常收尾且无「响应超时」／**断开期间发出**
    的轮同样不起表。先跑红三条再实现。
  - **变异验证两条，各红在自己那半个修法上、互不覆盖**：注射「不摘表」⇒ 红前两条（第 3 条仍绿）；
    注射「离线期间照样起表」⇒ 只红第 3 条。⇒ 第 3 条断言确实由「超出计划字面」的那半决定。
  - **真机复验见验收表第 5 条**（整条链走通：断网 164s 不超时 → 重连 → 补发答复上屏）。

- **T15 Maestro 06 + `e2e/README.md`** —— `183e44e`。三条流各单跑、退出码全 0：
  **06 危险动作进 Focus Dock 193.7s** / **08 键盘不遮发送键 126.0s** / **09 状态画廊离线冒烟
  326.4s 与 323.9s（两趟，差 0.8%）**。墙钟含 JVM 启动与 dev bundle 首载；09 那 5 分半几乎
  全在 `repeat 6 × scroll`。README 补了三条流的表、口径说明与「06 与 02 验的不是同一件事」。
  CI 按 `--include-tags offline` 自动带上 09，**未改 workflow**；⚠ 但该 job 挂在
  `workflow_dispatch` 的 `run_e2e` 开关下，**不是每次 push 都跑**——别把「进了 CI」读成「每次都跑」。

- **真机 13 条验收表**（MIX Fold 4 折叠外屏 `display 4630947090644569220`，1080×2520 / density 480，
  `target=cloud`；截图在 `mobile/e2e/artifacts/b1-16-*.png`，该目录 gitignore）：

| # | 项 | 结论 | 取证与读数 |
|---|---|---|---|
| 1 | 免唤醒 → `armed` 青环 | ✅ | 青环**量化**：环上 x=36 处 G−R=**+17**（(51,68,106)），左右 ±2px 仅 +2~4。胶囊「说「小舟小舟」」在回到对话屏后 **≤3.0s**（11:33:29.5 → 11:33:32.5 已在屏）。`b1-16-01-armed-capsule.png` |
| 2 | 唤醒 → `listening` | ✅ | 泓舟真人说「小舟小舟」。连拍 w04 胶囊＝「**● 在听…**」带青点，w07 变实时部分识别「● 今天天气怎么样」；青环 G−R **37–42**（armed 档只有 18–22）⇒ 0.18α→0.4α 的差别是**量出来的**。`b1-16-02-listening*.png` |
| 3 | 危险动作 → Dock | ✅ | 「打开后备箱」→ `dock-confirm` + 进度条 + 取消/确认；倒计时 **4:37 → 4:27**（墙钟 10:47:59→10:48:09）⇒ 同时复验了第 3 批附加项②「倒计时冻住」的修法。`b1-16-03-dock-t0/t9.png` |
| 4 | 到期留痕（300s） | ✅ | 5 分钟不碰手机（keep-awake 设置**是关的**，dev build 无条件持 tag——R4 那条结论顺带再证一次）。Dock 消失 + 记录里出现「⏱ 「…」的确认已过期，需要的话再说一次」。`b1-16-04-expired.png` |
| 5 | 待确认 + 断网（含附加项①复验） | ✅ | 飞行模式 → 探活判死 ≤30s → 胶囊「已断开 · 消息会排队」、**Dock 仍钉着确认（3:43 后过期）**、光球 muted（**量化**：armed 的青环峰整条消失，x=28–46 全是背景 (12,13,25)；球体由偏蓝紫 (101,99,146) 变近灰 (108,103,123)）。断网时再发一句 → **「另有 1 个待处理」出现**（＝`queued>0`，第 1 批遗留③那条「`send()` 断线返不返 false」的**真机确认：返 false**）→ 确认过期后 Dock 钉住「1 条消息排队中，连上后自动补发」。**发出后 164s（远超 95s）气泡仍「正在思考…」、无「响应超时」**；关飞行模式 11:05:16 → 11:07:03 前答复「现在是上午11点5分。」已上屏、Dock 清空。`b1-16-05-*.png` |
| 6 | 隐私栏三行 | ⚠ 二缺一 | **第一行 ✅ 实拍**「唤醒词待机（端侧监听，不上传）」+ 摄像头「关」（⚠ 计划表里写的期望文案「本机处理」是**旧文案**，第 3 批坑②已把两件事分开写，实际文案更准）。**第三行 ⚠ 部分**：对话屏采集点在唤醒瞬间实拍为**琥珀** (233,162,59) —— 判据与那一行**同源**（`privacy.mic==='cloudAudio'`）；激活日志实拍「麦 12:06/12:07 唤醒词命中 · **端到端（原始音频上传）**」。**但隐私栏那一行文字的活证四轮都没抓到**（详见遗留⑤）。**第二行 ⬜ 未取**（PTT 中／三段式）——adb 单点注入做不到「按住光球 + 点顶栏」，本轮未安排真人做这一下。`b1-16-06-*.png` |
| 7 | VAL 拒绝 → Dock | ⬜ **未接、待 Q16**（预期内，非红） | 源码盘点：`safety_blocked` 全仓只有**类型 + 渲染分支 + 画廊样本**（样本已标「仅样本」），`usePresence` 零产出方。⚠ 顺带校准接手须知 5 的措辞：`missing_slots` 在 **agent gRPC 契约里是有的**（`proto/cockpit/agent/v1/agent.proto:119`），但 `gateway/edge/main.go` 构造的**下行帧没有**——那条说的是客户端侧，别按 `proto/` 一 grep 就以为它被推翻了 |
| 8 | 状态画廊深浅各一套 | ✅ | 两套均已取，页面自报「**样本 24 条**」（⚠ 计划表写的 23 是拆计划时的数，第 2 批补了 `offline-queue-only`）；`presenceFixtures.test.ts` 5 条全绿。`b1-16-08-gallery-{light,dark}.png` |
| 9 | 200% 字号 | ⚠ 一半 | `settings put system font_scale 2.0`。**按钮 ✅**：取消键上下外边框 1684→1828 = **144px = 48.0dp**（density 480），正好压线达标。**「不裁字」❌**：Dock 标题被压成「**这..**」——右侧「危险动作 · 需二次确认」是固定文案、随字号一起放大，把标题挤没了 ⇒ 承诺卡在 200% 下**不再说明自己要确认什么**。Onboarding 200% 全部换行不裁。已还原 1.0。`b1-16-09-*.png` |
| 10 | 键盘 | ✅ | Maestro 08 退出码 0（126.0s），见 T15 |
| 11 | 回滚 | ✅ | 两个实验室开关各关一次并**当场截图确认开关真的变了**：`uxV2Dock` 关 ⇒ **气泡内确认按钮回来**；`uxV2Presence` 关 ⇒ **v1 的「● 待唤醒 · 说「小舟小舟」」状态条回来、v2 胶囊消失**。开关关闭状态下 **Maestro 02 仍绿**（退出码 0，202.1s）。验完已恢复开启并再截图确认。`b1-16-11-rollback-v1.png` |
| 12 | 性能 | ⚠ 读数只取一半 | `gfxinfo` 的 **CPU 侧直方图不可信**：530 帧全部落在 150ms/200ms 两桶、133ms 以下为 0，却同时报 `Janky frames: 0 (0.00%)` / `Missed Vsync: 0` / `Frame deadline missed: 0`，而 legacy 口径又报 100%——**自相矛盾的仪器不作数**。可用的是 GPU 侧：**50th 11ms / 90th 14ms / 95th 15ms / 99th 18ms**，`Slow UI thread 0`、`Slow bitmap uploads 0`。⚠ 这是 **dev build**（JS 不压缩、dev 标志开着）的读数，不能当 release 读数；`Number High input latency: 1060` 是 adb 注入事件的时间戳伪影，不是用户侧读数 |
| 13 | 无障碍 Scanner | ⬜ 未跑 | 设备上**没装** Accessibility Scanner（`pm list packages` 只有 `switchaccess` / `miui.accessibility` / systemui 的 a11y menu，无 `com.google.android.apps.accessibility.auditor`）。装 APK 到泓舟的设备不在我的授权范围 ⇒ 记未跑并转入出账，不写成绿 |

- **反向验证两条**
  1. 把 `presence.ts` 的 `if (transport === 'offline') primary = 'muted'` 与 `hasAttention` 那行对调
     （变异落盘已核 `grep -c`）⇒ `presence.test.ts` **红 1 条**（「评审案例：待确认时断网…」），
     **不是计划预期的 2 条**。查因：`A. primary 优先级 › 逐对交换` 的最后一条钉的是
     `offline↔listening`（`connStatus closed + ptt recording`），**不含 `offline↔attention`** ⇒
     不变量仍被守着（靠评审案例那条），但**「逐对交换」这个名字第三次强于它的覆盖**
     （第 1 批遗留②、第 2 批变异验证、这次）。已还原。
  2. 计划自己判定第二条「无区分度」，照其结论执行：**Dock 的排序判据由 `commitment.test.ts`
     的纯函数用例守，组件层不再重复验**。

- **本批踩的坑**
  1. **`adb shell` 里的 `/sdcard/...` 会被 MSYS 翻译成 Windows 路径**（`screenrecord` 与 `adb pull`
     双双落空，报 `D:/Program Files/Git/sdcard/...`）。要 `MSYS_NO_PATHCONV=1` 或写 `//sdcard/`。
     ⇒ 计划里「用 `screenrecord` 录 20s」这条**本轮没成**，改用连拍取证。
  2. **PowerShell `Start-Job` 起的 maestro 没真跑**（日志文件根本没生成），而我的 30 张连拍
     全部「读数一致」——差点被读成「视觉态没出现」。**「没看到 X」先证明观测通道开着**：
     这次是**演员没上场**。前台重跑立刻拿到结果。
  3. **固定坐标探针会随内容长度失准**：隐私栏第三轮 20 帧「逐字节相同」，是因为激活日志那行
     换成两行、把整个弹层撑高，我固定的 y=1580 落进了行间空白。**采样行必须跟着布局重新对准**，
     否则「没变化」是假的。
  4. **`maestro test` 的相对路径依赖 cwd**，而 PowerShell 的 cwd 在本轮漂到了 `mobile/`
     ⇒ `mobile/e2e/02-…yaml` 报 `Flow path does not exist`。跑既有流一律用绝对路径。
  5. `keyevent 4` 连按会退出应用落到 **Expo dev-launcher** 屏，此时所有坐标点击都打在别的窗口上
     （我据此误以为「设置页深链失效」）。判屏一律先截图。
  6. **`captureDot` 是一颗点、按优先级取一个**（`cloudAudio` > `mic≠off` > `camera`）。
     免唤醒开着时 `mic≠off` 恒真 ⇒ 「正在抓一帧画面」这一档**在免唤醒开启下永远显示不出来**。
     这是设计（源码注释写着「顺序即优先级」），但**取证时别把它当成「视觉没触发」**。

- **遗留出账（含前三批汇总）**

  本批新增：

| # | 事项 | 处置 |
|---|---|---|
| ① | **200% 字号下 Dock 标题被挤成「这..」**（验收表第 9 条）。承诺卡在最大字号下不再说明要确认什么；右侧「危险动作 · 需二次确认」是固定文案、同比例放大 | **给 B2**：标题与右侧标签的让位规则（标签在窄宽下折行/缩写/隐藏），不是 B1 范围 |
| ② | **隐私栏「麦克风」行的颜色判据与文案判据不同源**：`tone` 取 `mic==='cloudAudio'` **或** `capture!=='armed'` 即琥珀，于是 `mic='off'`（文字「关」）且 `capture!=='armed'` 时，**「关」被涂成琥珀**（实拍）。一块以「说的是真的」为全部价值的屏，颜色不该和文字说两件事 | **给 B2**：与遗留⑤、第 3 批遗留②（`privacy.mic` 三档并了两件事）一起裁 |
| ③ | **`gfxinfo` 在本机对本应用的 CPU 侧直方图不可信**（验收表第 12 条）。性能读数目前只有 GPU 侧可用，且是 dev build | **给 B2 性能批**：要么换 `framestats` 逐帧口径，要么在 release 包上量 |
| ④ | **`missing_slots` 的措辞需按层区分**（验收表第 7 条）：agent gRPC 契约有、客户端下行帧没有 | 已在本记录写明；接手须知 5 不改（它说的就是客户端侧） |
| ⑤ | **隐私栏第三行（端到端「原始音频上传中」）的活证没抓到**，四轮：两轮弹层开着时窗口内没发生唤醒；一轮唤醒发生在弹层渲出前 1.5s、渲出时 `capture` 已回落；一轮端上 45 帧尺寸只有 3 种（实质全同）。⚠ **「弹层挡住 KWS」这个解释已被证伪**——第三轮日志新增了「麦 12:07 唤醒词命中」，说明弹层开着时唤醒照常发生 | **给 B2**：那一行由采集点琥珀（同源判据）+ 激活日志 + `presence.test.ts` + `micText` 源码分支四条独立证据支撑，缺的只是那一行文字的静态照片 |
| ⑥ | **隐私栏第二行（PTT 中／三段式录音）未取证** | 同上，需要「按住光球 + 点顶栏」的双指操作或真人一次 |
| ⑦ | **Accessibility Scanner 未装、第 13 条未跑** | 需泓舟授权装 APK；B2 前补一次基线 |
| ⑧ | **`looking` 白环仍无静态取证**（第 2 批遗留⑥原样带过来）。本轮打字触发视觉词命中了（回答里点名「再说一次『那是什么』」、激活日志留了「摄像头 11:49/12:07 触发词…」），但**那两次都没抓到画面**（手机平放桌上），`looking` 态因此没产生 | **给 B2**：`screenrecord` 路径坑已定位（坑①），下次可录屏逐帧 |

  前三批遗留的现状（逐条核过，不是复述）：

| 来源 | 事项 | 现状 |
|---|---|---|
| 1批① | `hfListening` 死变量 | **已删**（`grep` 全空） |
| 1批② | 「逐对交换」名字强于覆盖 | 四对已补；**但本批反向验证发现 `offline↔attention` 这一对仍不在其中**（见反向验证 1）——同族第三次 |
| 1批③ | `queued` 依赖 `send()` 返 false（未验） | **本批真机确认：返 false**（验收表第 5 条，「另有 1 个待处理」出现） |
| 1批④ | `isVisionCapturing()` 零消费方 | **已被 `usePresence` 消费**（`useState(isVisionCapturing)`） |
| 1批⑤ | `useHandsFree`/`usePtt` 无单测 | 仍无；本批由真机第 2/6 条间接验到 |
| 1批⑥⑦ | `uncertainIds` 只在 open→closed 写入；`setStatus` 同值早退 | 设计语义不变。⚠ 本批**在同值早退之前**插了摘/起表（否则退避期 `closed↔connecting` 摆动会漏摘） |
| 1批⑧ | `handsFree.test.ts` 的 `Jest did not exit` 噪声 | 仍在（全量跑仍出现），非本批引入 |
| 2批① | 两个实验室开关零消费方 | **已接线并真机验过关/开两个方向**（验收表第 11 条） |
| 2批② | 确认卡倒计时冻住 | **已修并真机复验**（4:37→4:27，验收表第 3 条） |
| 2批③ | `usePresence` 三字段偏差 | `lastError.at` 已修（`SeenRegistry.seed`）；`driving` 恒 false 与 `connChangedAt` 挂载重置**按既有判据原样接**，转 B4 |
| 2批④ | 秒级 `setInterval` 常开 | 第 3 批已改成条件停表 |
| 2批⑥ | `looking` 白环无静态取证 | **仍开**（本批新增⑧） |
| 2批⑦ | `slot`/`safety_blocked`/`fatal`/`recoverable_error` 无产出方 | **仍开**，挂方案 §13 Q16/Q19（验收表第 7 条已复核） |
| 2批⑧ | `PresenceCapsule` 26dp + `role=text` | 仍是只读状态，B1 未接 `onPress`（第 3 批附加项⑤照办） |
| 2批⑨ | `DegradationRow` key 冲突 | **已改**成 `${kind}:${what/reason}` |
| 2批⑩ | 计划自身不一致（09 提前落） | **已收口**：06 与 README 本批补齐 |
| 2批⑪/3批⑤ | 浅色下光球对比度偏低 | **仍开**，本批画廊浅色套第三次可见，给 B2/B3 视觉批 |
| 3批① | `cloudAudio` 隐私栏行没截图 | **部分收口**（采集点琥珀 + 激活日志实拍），那一行文字仍缺 ⇒ 本批⑤ |
| 3批② | `privacy.mic` 三档并了两件事 | **B1 不动**（附加项④），转 B2/B4，与本批② 一起裁 |
| 3批③ | 排队补发后没有第二次答复 | **本批定性 + 修复 + 真机复验，收口**（附加项①） |
| 3批④ | 恢复网络非立刻重连（约 2 分钟） | 本批实测**更快**：关飞行 11:05:16 → 11:07:03 前已补发上屏（`reconnectNow` 把退避档位归零） |
| 3批⑥ | `driving`/`connChangedAt` 原样接 | 同 2批③ |
| 3批⑦ | Maestro 06 与 README 留第 4 批 | **已收口**（T15） |

- **`mobile/.gitignore`**：Task 17 要求加的 `e2e/artifacts/` **第 2/3 批已经加过**（本批核实存在、
  `git ls-files` 零跟踪），不重复改。

- **未推送清单**（`git log origin/main..HEAD --oneline`，T17 提交前）：
  `95c1d02`（开工前就在）／`59b742b` 附加项①／`183e44e` T15；
  ⚠ 另有 **3 个不是本线的提交**混在同一支 `main` 上：`723ba0f`／`7a9fd7f`／`15ff116`
  （另一条会话的「QA 安全闸设计」文档）。**`git push` 的粒度是分支不是提交**——推 `main`
  会连它们一起带走 ⇒ 本批只报数、不推送，等泓舟单独授权（附加项⑥）。
