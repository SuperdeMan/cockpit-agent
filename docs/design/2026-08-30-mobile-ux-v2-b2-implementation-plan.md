# UX v2.1 · B2「语音层」实施计划（逐任务，按方案 v2.2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 状态：**草案（2026-08-30）——待泓舟批准开工；批准前 `mobile/` 不按它动手**
> 交付对象：`mobile/` 执行者（人或 Agent）
> 上游真相源：[`2026-08-29-mobile-ux-v2-presence-redesign.md`](2026-08-29-mobile-ux-v2-presence-redesign.md)（方案 **v2.2**；本计划只展开 **B2**，读 §0 / §5.1.1 / §5.2–§5.2.2 / §5.3.2 / §5.5 / §11.1 / §11.4 / §13 Q2·Q11·Q20）；
> B1 落地评审 [`docs/reviews/2026-08-30-review-ux-v2-b1-vs-proposal.md`](../reviews/2026-08-30-review-ux-v2-b1-vs-proposal.md)（§4 D1–D9、§6 B2 入口五条）；
> B1 计划 [`2026-08-29-mobile-ux-v2-b1-implementation-plan.md`](2026-08-29-mobile-ux-v2-b1-implementation-plan.md)（§0.1 分批纪律、§6.4 遗留出账——B2 从那张表接活）
> 纪律：沿用 B1 计划 §0 + [`2026-08-24-mobile-app-implementation-plan.md`](2026-08-24-mobile-app-implementation-plan.md) §9 坑账；每任务「先测后码、一任务一提交」；**零新原生依赖、不重建 APK；共享判据（`hmi/src/*.mjs`、`pendingOps.mjs` TTL）一字不动；`hmi/` 不碰**（唯一例外走主计划 §10「共享模块有 bug」条款，本批没有——回声那条不是 bug 是缺出口，记遗留）

**Goal:** 把 PTT / 唤醒词 / S2S 三种说话方式收进**同一张从底部升起的语音层**；语音层不持有自己的状态——它是「对话记录里当前这一轮」的视图，转写与回答**增量沉淀**进记录（S2S 自答轮从此有记录、带「端到端」角标、开录即告知、首次显式同意）；轻点光球始终能说（端侧 VAD 收尾）；主卡/折叠卡、follow-up chips、视觉先落气泡、回声提示、播报三档、执行回执、在场轨迹页。**开工第一件事修 B1 评审的 D1（承诺卡摘要取错源），第二件事把 D2–D5 当一组判据修正做完**——它们不是语音层，但 B2 不做就没有别的批会做。末尾是 **B2→B3 闸**：真人语音轮五项读数 + 5 人外部小样本。**全 JS，不重建 APK。**

**Architecture:** 判断仍只住在 `core/presence/presence.ts::derivePresence`（B1 的纯函数）——B2 给它加四个事实输入（`hfFsmChangedAt` / `voice` / `notice`，以及麦克风第四档 `cloudAsr` 的派生）与两个派生输出（`input` 的语音层开合、`sheetDetent`），**不新增状态机、不改共享 FSM**。记录侧只动 `SessionCore`（`core/session/store.ts`）：草稿气泡 `draftUserId`、打断留痕 `interruptedIds`、端到端轮 `s2sIds`、视觉轮 `visionIds`、轮元数据 `turnMeta`、确认台账回复 `confirmLog`——全部是 `SessionState` 的**并列字段**（`Msg` 是共享类型不能加字段）。UI 侧新增 `VoiceSheet`（读 snapshot + 当前轮）、`EdgeGlow`、`CardGroup`、`FollowUpChips`、`ExecutionReceipt`、`S2sConsentSheet`、`/presence-trail`；`Composer` 换成手势契约（RNGH 已随 expo-router 在 APK 里注册）；`usePtt` 长出 `tap()` / `cancel()`，`HandsFreeController` 长出 `wakeManually()` / `endUtterance()` / `recycle()`（全是 FSM 的**公开**入口，FSM 一字不改）。

**Tech Stack:** React Native 0.86 / Expo 57 / expo-router / zustand / react-native-reanimated 4.5 / react-native-gesture-handler 2.32（已在场）/ jest-expo（`mobile/test/**/*.test.ts`，纯逻辑）/ Maestro 2.9（`mobile/e2e/`）。

---

## 0. 接手须知（先读）

1. **开工前提**（不变）：`powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0；Metro `cd mobile && npx expo start --dev-client`；真机 dev-client 已装（本批**不重建**——B2 零新原生依赖；任何「要重建」的念头都说明走偏了）。**B2 唯一的原生前提是 RNGH 已注册**，判据不是 `package.json`、是 `PackageList.java`（坑账 §9.43）：
   ```bash
   grep -n "RNGestureHandlerPackage" "D:/Android/builds/xiaozhou-mobile/android/app/build/generated/autolinking/src/main/java/com/facebook/react/PackageList.java"
   # 2026-08-30 读数：第 73 行 `new com.swmansion.gesturehandler.RNGestureHandlerPackage()`（Reanimated 第 75 行）
   ```
   若你换了构建目录且这一行不在 ⇒ Task 6 的手势退回 RN 核心 `PanResponder`（写进 §6 记录），**不加依赖、不重建**。
2. **每个任务的顺序是固定的**：写失败测试 → 跑出红 → 最小实现 → 跑绿 → `tsc` → 提交。`mobile/` 的测试命令：`cd mobile && npx jest test/<file>.test.ts`（单文件）/ `npm test`（全量，**开工基线 29 suites / 315 tests**，B1 收口读数）/ `npm run typecheck`。
3. **提交只加自己的路径**：新建文件 `git add -- <路径> && git commit -m '…' -- <路径>` **同一条命令链**（**`-m` 必须写在 `--` 之前**——`--` 之后的一切都是 pathspec，第 1 批实测 `pathspec '-m' did not match`）（已暂存未提交比未暂存更有害，会污染别人的发版闸）；提交后 **`git show --stat HEAD` 复核行数**。共享工作树里可能有别的会话的改动，**不要 `git add -A`**。⚠ 按路径隔离得了「文件」，隔离不了**同一个文件里别人未提交的行**——`AGENTS.md` 正是所有会话都在写的那一个：动它前先 `git diff --stat -- AGENTS.md` 看行数对不对得上自己的预期。全仓提交都是同一个 git 身份，「这个 commit 是哪条会话的」靠元数据答不出来，只能靠会话自己说。
4. **真机取证一律截图**（`adb exec-out screencap -p -d <displayId>`；折叠屏 id 用 `dumpsys SurfaceFlinger --display-id` 取）；`uiautomator dump` 在有常驻动画的屏上不可信（坑账 §9.40/48）。**录屏路径要写 `//sdcard/…` 或带 `MSYS_NO_PATHCONV=1`**（B1 第 4 批坑①：MSYS 把 `/sdcard/` 翻成 Windows 路径，`screenrecord` 与 `adb pull` 双双落空）。`adb shell` 里的 PNG **不要经 PowerShell 的 `>`**（会损坏）。
5. **六个结构性事实，写代码前记住**（每条都有 `文件:行号`，别按印象）：
   - **`Msg` 不能加字段**（`hmi/src/types.ts:9`，共享）⇒ B2 全部「气泡上要多显示一个态」都是 `SessionState` 的并列字段：`draftUserId` / `interruptedIds` / `s2sIds` / `visionIds` / `turnMeta` / `confirmLog`（Task 4/5/10/13 逐个引入；类型在 §1）。到期留痕、回执都用**追加消息 / 并列表**，不改原气泡。
   - **共享判据只读**：`voiceLoop.mjs` / `s2sClient.mjs` / `sileroEndpoint.mjs` / `pendingOps.mjs::PENDING_TTL_MS`。后果之一：回声提示（§5.2 规则 5）只能挂 FSM **已经吐出来的** `onMetric('echo_dismissed')`（`hmi/src/voiceLoop.mjs:379`，续问窗回声那一路）；barge-in 那一路的回声（`_bargeInFire` → `_countSelfTrigger`，`:476`）**没有 metric**，App 看不见 ⇒ Task 11 只做前者，后者写进遗留、由 hmi 侧另立。
   - **`vad_silence_ms` 只有 qwen3 realtime 消费**：`llm-gateway/providers.py:762`「fun-asr 走客户端 stop 端点，不受此影响」，而 App 默认 `asrModel='fun-asr-realtime'`（泓舟 2026-08-26 指示，`core/settings/store.ts:81`）⇒ 方案 §5.1.1「轻点光球 → 服务端静音尾收尾」在缺省引擎上**收不了尾**。Task 6 的收尾主判据改成**端侧 VAD**（`VadEngine` 已在这个 APK 里，M4 真机已验），`vad_silence_ms` 照传（qwen3 用户白得一层），VAD 缺席时 15s 硬上限——**方案里的参数是待证命题不是待办**（B1 计划 §5 同款教训）。
   - **PTT 在免唤醒开着时今天是坏的**（写计划时从源码读出来的，B1 验收表第 6 条第二行「PTT 中」恰好没取证）：`usePtt` 的 `AsrSession` 用 `recorder()` 单例，`AudioApiRecorder.start()` 在已录时**静默 return**（`core/voice/recorder.ts:50`）⇒ PTT 一帧都收不到；`session.stop()` → `AudioApiRecorder.stop()` 会**把免唤醒正在用的真麦停掉**（`:89`，micBus 的 `running` 还是 true，从此免唤醒是聋的）。⇒ Task 6 的前置修：`AsrSession` 的 recorder 换 `micLease()`（`core/voice/micBus.ts:105`，Lease 实现 `Recorder`、引用计数、真麦只在最后一个 lease 停下才关）。**这一条要先于手势契约落，且要有 jest 钉住**。
   - **S2S 逃逸的双气泡风险**：`onS2sUserUtterance`（`core/voice/handsFree.ts:167`）与 `onS2sEscalated`（`:325` → `useHandsFree.ts:115` → `ChatScreen.onSend`）都会产生一条用户气泡。B1 没接前者所以没撞上；Task 5 接了就必须用 `takeS2sUserBubble()` 把那条气泡**交给主链轮复用**，不许两条。
   - **协议里仍然没有** `missing_slots` / `confirm_policy` / VAL 拒绝结构化标记（方案 Q16 / Q19 挂账后端）⇒ 回执（Task 13）的「安全检查」栏**留位不渲染**；`DockItem.slot` / `safety_blocked` 仍只有类型与画廊样本。不许在客户端用正则猜。
6. **评审交给 B2 的两条「别踩」**（评审 §6 第 5 条）：① `PresenceCapsule` 是 26dp + `accessibilityRole="text"`——Task 3 接 `onPress`（§4.3「点按胶囊 = 打开语音层」）时**必须同时改 role 与热区**（`hitSlop` 补到 48dp、role 换 `button`）；② `SessionCore` 的看门狗暂停 / 恢复语义（`linkDown` / `pausedWatchdogs`，`store.ts:531-552`）修的是「重连后用户永远拿不到答案」，**重构时别当成多余的复杂度删掉**——判据在 `sessionStore.test.ts:529/545/560` 三条，第三条有变异验证钉着。Task 4 动 `store.ts` 时这三条必须仍绿。
7. **方案 §13 Q20 的落点在本计划里是**：D1 → Task 1；D2/D3/D4/D5 → Task 2（一组，不拆）；D6 → **B4**（不在本计划）；D7 → Task 6；D8 → Task 14；D9 → Task 4；S2S 首次显式同意 → Task 5（设置页那一半）；在场轨迹页（🔁-1）→ Task 14。方案已裁决的默认值（Q1–Q20）不在本计划重议；本计划新增的实施判断集中在 §5。

### 0.1 分批执行：一批一个会话（新会话从这里开始）

本计划很长，一个会话读不完也不该读完。**分四批，每批一个新会话**，每批以「jest 全绿 + `tsc` 0 + 逐任务已提交 + §6 实施记录回填」收口；下一批冷启动只读 §0 + §0.1 + §1 + 自己那几个 `### Task N` 块（用 `grep -n "^### Task" <本文件>` 取行号，`sed -n` 只读自己的段），**不读整份计划、不读方案全文**（方案只在任务里点名的 §号处查）。

| 批 | 会话任务 | 性质 | 并行度 | 收口判据 | 真机？ |
|---|---|---|---|---|---|
| **第 1 批「遗留修正与判据层」** | T1 D1 摘要源 → T2 D2–D5 胶囊与隐私栏判据修正 | 两处纯函数 + 三个组件的取值处；全部 jest 可跑 | 串行（两条都动 `usePresence.ts` / `presence.test.ts`） | `npm test` 全绿（315 → ≈340）、`tsc` 0、2 个 commit、画廊深浅各一套、真机 4 张（Dock 标题「打开后备箱」/ 200% 标题不裁 / armed 胶囊 3s 消失 / 隐私栏 PTT 行琥珀）、§6.1 | 是（画廊 + 对话屏，不需要泓舟） |
| **第 2 批「语音层骨架与记录沉淀」** | T3 Voice Sheet 三入口一张层 → T4 draft→final 增量沉淀 → T5 S2S 轮沉淀 + 角标 + 开录即告知 + 首次同意 | 新组件 + `SessionCore` 长方法 | T4 的 store 半边（`store.ts` + `sessionStore.test.ts`）可与 T3 并行；接线串行 | 全绿、`tsc` 0、3 个 commit、真机：三入口各升一次层 / 草稿气泡逐字与层同 / **S2S 走通一轮**（M4 挂账「端到端未验」在此一并）、§6.2 | 是（**S2S 一轮与「录音中途切后台」需泓舟真人**） |
| **第 3 批「手势与层内元素」** | T6 轻点即说 + 手势契约（含 PTT-lease 前置修、D7）→ T7 边缘极光 ∥ T8 card_group 主卡/折叠 ∥ T9 follow-up chips → T10 视觉先落气泡 → T11 回声提示 | 手势 + 四个小组件 | **T7/T8/T9 三个 subagent 并行**（互不相干的新文件，各自只加自己的路径提交）；T6/T10/T11 串行（都动 `ChatScreen.tsx`） | 全绿、`tsc` 0、6 个 commit、真机：轻点即说（免唤醒关）/ 上滑取消 + 文案 / 主卡「还有 N 张 ›」/ chips 可点 / 「这是什么」气泡先于相机 / 回声提示（带 APK 构建时间与有无 AEC）、§6.3 | 是 |
| **第 4 批「播报·回执·轨迹 + 闸」** | T12 播报三档 → T13 执行回执 → T14 在场轨迹页 → **T15 B2→B3 闸**（含 Maestro 05、记录收口） | 设置 + 两个组件 + 取证 | T12/T13/T14 可并行（不同文件；`MessageBubble.tsx` 只有 T13 动、`SettingsScreen.tsx` T12 与 T14 各改一个分区——**串行提交**） | 闸五项读数 + 5 人小样本 ≥5/6 + §11.4 取数 + §6.4；AGENTS.md 只改指针；未推送清单报给泓舟 | 是（**泓舟 + 5 名外部用户**） |

**B1 §6.4 出账表在 B2 的去向**（接手时逐条核，不复述）：① 200% 下 Dock 标题被挤 → T1；② 隐私栏颜色与文案不同源 → T2；③ `gfxinfo` CPU 侧不可信 → T15 改用 `framestats` 逐帧口径；④ `missing_slots` 分层措辞 → 已在 §0 第 5 条写清；⑤⑥ 隐私栏第二/三行活证 → T2 补第二行（PTT 行，单人可取）、T15 补第三行（端到端，随 S2S 轮）；⑦ Accessibility Scanner 未装 → T15（**装 APK 到泓舟设备要授权**，没授权就记 ⬜）；⑧ `looking` 白环无静态取证 → T10 随「先落气泡」用录屏取（§0 第 4 条的路径坑已定位）。B1 前三批仍开的两条：`handsFree.test.ts` 的 `Jest did not exit` 噪声（非本批引入，T6 动该文件时看一眼是否是未清的定时器）；浅色下光球对比度（给 B3 视觉批，B2 不动）。

**第 3 批附加项（第 2 批 §6.2 遗留转入；⓪ 先于 T6、①②③ 随所在任务顺手，各自单独提交）**：
- ⓪ **取证探针落进仓库**：第 1/2 批的颜色类读数一直没做 PNG 反滤波（§6.2 坑⑨，只有 filter=0 的扫描线碰巧对）。把带 defilter 的最小解码器落到 `mobile/e2e/tools/png_probe.py`（纯 stdlib：解 IDAT + 五种 filter；命令行给矩形 → 输出平均 RGB、亮像素计数、两图逐字节差异比）——上一批的脚本若只在 scratchpad 就重写；单独一个 commit。之后所有颜色 / 亮度类读数都经它，逐字节差异类读数不受影响。
- ① **T7 附加：语音层壳底不够实**（§6.2 遗留：记录里的气泡透过 `Glass` 与层内文字重叠，`b2-03-capsule-attention.png`）。方案 §5.11 给语音层外壳的是 G1 frosted，token 已有 `GLASS.frosted.tint = 0.58`——`VoiceSheet` 的壳底改成 `p.bg` 同色系实色叠 `GLASS.frosted.tint` 的不透明度（`Glass` 组件的 `glassBg` 是 5.6%，那是卡壳用的），不够读再把暗区 0.4 → 0.6；真机同一场景截图对比、取可读的那个，写进 §6.3 并注明「这是 §5.11 G1 的 tint 落地，不是新裁决」。方案 §5.2「记录变暗 40%、仍可见」的「仍可见」保留。
- ② **T11 附加：S2S 挡位下 FSM 判回声丢弃要显式取消 provider 的轮**（§6.2 遗留：真机实录 `深圳市宝安区当前音。`——主链 TTS 被麦收回、S2S 转写成用户话并自答一整轮）。`handsFree.ts` 的 `S2S_LOCAL_HANDLED` 只有 `exit_word / filler_dismissed / false_wake_dismissed`，**没有 `echo_dismissed`**——FSM 把这句判成回声进 FOLLOWUP 时，provider 不知道、照答。加进去 + `handsFree.test.ts` 一条（**App 侧一行，共享 FSM 不动**）：
  ```ts
  test('B2-11 附加：S2S 挡位下 FSM 判回声丢弃 → 显式 cancelTurn（provider 不知道我们把这句判掉了，会自答一整轮）', async () => {
    const cancelled: number[] = []
    const { ctl } = makeCtl()
    await ctl.enable()
    // 假 S2S 客户端：只看 cancelTurn 有没有被叫到（真的 S2SClient 要 WebSocket，jest 里没有）
    ctl.s2s = { cancelTurn: () => cancelled.push(1) }
    ctl.vl.onMetric('echo_dismissed')
    expect(cancelled).toEqual([1])
    ctl.vl.onMetric('endpoint_merge') // 对照：不在名单里的事件不取消
    expect(cancelled).toEqual([1])
  })
  ```
  ⚠ 这只堵「判出来的回声」；AEC 那一行没覆盖 S2S 采集路径这件事（§6.2 遗留）仍是 B3/B4 或 hmi 侧的，本批零原生变更。
- ③ **T6 真机顺手两条**：a) 第 2 批 ⬜ 的「打断留痕灰字」——busy 窗口只有 5–15s、adb 往返押不准（§6.2 坑⑩），改用「讲一个三百字的故事」把流式窗口拉长到 30s+ 再点「■ 打断」，取 `b2-04-interrupted.png`；b) 上滑取消不能用 `input swipe`（它没有按住阶段、长按判定过不了），用 `adb shell input motionevent DOWN x y` → `sleep 0.5` → `input motionevent MOVE x y-300` → `input motionevent UP x y-300` 三段式注入，或真人手指。
- ④ **开工前 `git log --oneline origin/main..HEAD` 念一遍**：第 2 批中途 `origin/main` 被另一条会话推进、把本线的 T3 一并带上去了（§6.2 遗留第一条）——共享 main 上「请不要推我的」不是可执行防线，念一遍至少知道谁的东西在里面。

**第 4 批附加项（第 3 批 §6.3 遗留转入；⓪ 是开工前提，其余随所在任务顺手）**：
- ⓪ **设备上 TalkBack 仍开着**（泓舟为 T6⑪ 手动开的，`touchExplorationEnabled=true`）。TalkBack 开着时 adb 的单击语义会变（单击=聚焦、双击=激活），**所有 `input tap` 类取证都会失真**。开工先 `adb shell dumpsys accessibility | grep -iE "touchExploration|talkback"` 核一眼；开着就停下交泓舟关（他手动开的，关也交回给他），关完复核 `Enabled services:{}` 再动真机。
- ① **层 Pan 方向约束（`e5f514c` 的 `activeOffsetY(10)`）真机未复验**——随 T15 真机段做，配方照 §6.3 遗留第一条：出一轮带 chips 的答案 → 答案出来后再点胶囊开层 → `input swipe` 横滑 chips 带 → `png_probe diff` chips 带有变化（**同帧层内大球框 diff 做通道自检**）→ 再验向下拖 500px 仍能收起。
- ② **T11 回声提示仍 ⬜**（装的包 `lastUpdateTime 2026-08-29 17:22:24` 早于 AEC 提交 `96a6830`，**没有 AEC，本应能触发**）——归 T15 G2 一并取，配方照 §6.3：断开/忽略 vivo TWS 4 蓝牙耳机确认音频走扬声器；用长播报（「讲一个三百字的故事」）；在播报结束进 FOLLOWUP 窗那几秒连拍胶囊区。仍未触发就写 ⬜ 与排查过的原因，**不写「预期」**；若期间重建过带 `96a6830` 的 APK，才能写「未触发（AEC 在场）」。
- ③ **T10「气泡早于相机」的顺序取证**——ffmpeg/ffprobe 6.1.1 已装（`C:/Users/Super/tools/ffmpeg/bin`，追加在用户 PATH 末尾；**已在跑的终端读不到新 PATH，用绝对路径**）。设备半段未验：先试通 `adb shell screenrecord --time-limit 8 //sdcard/x.mp4` → `adb exec-out cat` 拉回 → `ffmpeg -vf fps=30` 抽帧 → `png_probe`，帧号 × 1/30s 对 logcat `CameraService::connect` 墙钟；试不通走**反向法**：`pm revoke com.xiaozhou.companion android.permission.CAMERA` 后说「这是什么」——新代码气泡同步先落、抓帧失败照样在，**用完 `pm grant` 还原并写进记录**。
- ④ **T6 两格补取**：a) 15s 硬上限单独取证（**安静环境**，全程静默 ⇒ 只能是硬上限收的尾）；b) 输入框有字时长按走原生选择、只有光球仍可 PTT（`input text a` 一个 ASCII 字符就够，不需要中文）。
- ⑤ **Maestro CLI 已不在本机**（T15 的 05/06/08/09 要用）。装回需泓舟授权，两条路：`curl -Ls "https://get.maestro.mobile.dev" | bash`（Git Bash，装到 `~/.maestro/bin`）或下 release zip 用绝对路径调 `maestro.bat`；国内慢就按 §6.3 那条镜像教训先找镜像。跑时全程 `--no-reinstall-driver`；第一次装 driver 的 MIUI 弹窗坑见 `mobile/e2e/README.md`。**uiautomator 手工替代只对静止屏有效**（常驻动画屏 `could not get idle state` 且会留陈旧文件），08/09 没有 Maestro 就 ⬜。
- ⑥ **设备重连后 `adb reverse` 会消失**（§6.3 坑12，症状伪装成「应用坏了」）：每次设备重插先 `adb reverse tcp:8081 tcp:8081` 再 `adb reverse --list` 复核。
- ⑦ 「换一批」chip 真机从没出现过（`place_list` 分支显式清 `category`）——T15 真机段用「附近的充电站」这类出 `poi_list` 的语料顺手补一张。
- ⑧ T15 动 `AGENTS.md` 时注意：CLAUDE.md 已于 2026-08-31 重写，明确 AGENTS.md 不是变更日志——照计划只改 Android 行的指针段，动前 `git diff --stat -- AGENTS.md`。

**每批开工的固定五步**（写进新会话的第一条提示词）：
1. `powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1` 退出码 0（第 1 批的 T1/T2 纯 jest 可先做，真机截图在收口时补）；
2. `cd mobile && npm test && npm run typecheck` 取**开工基线**（条数与 0 error），写进 §6 该批记录的第一行——读数有效期只到下一次改动；
3. 只读 §0 / §0.1 / §1 + 自己批次的 `### Task N` 块；
4. 按任务顺序：写失败测试 → 跑红 → 实现 → 跑绿 → `tsc` → `git add -- <新文件> && git commit -m '…' -- <只加自己的路径>` → `git show --stat HEAD` 复核；
5. 收口：全量 `npm test` + `tsc`，把读数、遗留、撞到的坑写进 §6，**然后停下**——下一批是另一个会话的事。

**worktree**：B1 期间三条线共用一个工作树出过两次事故（B1 计划 §0 第 3 条；`git push` 的粒度是分支不是提交）。**若泓舟同意分树**，第 1 批开工前执行一次并写进 §6：`git worktree add ../car-agent-ux-b2 -b ux-v2-b2`，四批都在该 worktree 里做，最后由泓舟决定合回 `main` 的方式（`git push` 仍需单独授权）。没有分树就在主工作树做，纪律同 §0 第 3 条——但**这一条不再是可选项的措辞**：不分树就要在每次提交前 `git status` 重采、`git log origin/main..HEAD --oneline` 念一遍谁的提交在前面。

**批与批之间的状态只靠两处传递**：git 提交（代码）与本文件 §6（读数与遗留）。新会话不要去翻上一批会话的对话——那些不在仓库里。

---

## 1. 文件结构（先定边界，再拆任务）

### 新建

| 文件 | 职责 | 依赖 | 任务 |
|---|---|---|---|
| `mobile/src/core/session/actionSummary.ts` | `actionSummary(messages, operationId)`：承诺卡 / 到期留痕的动作摘要——**紧邻的上一条用户原话**，跳过「确认/取消」回复 | 无 RN import | T1 |
| `mobile/src/features/chat/dockLabel.ts` | `dockLabelMode(pref, windowFontScale)`：承诺卡右侧固定标签的让位规则（❌-1） | 无 RN import | T1 |
| `mobile/src/core/session/turnView.ts` | `currentTurn(messages)`：「当前这一轮」= 最后一条用户气泡 + 其后的助手气泡；`isProactive(msg)` 从 `MessageBubble` 搬来共用 | 无 RN import | T3 |
| `mobile/src/features/chat/VoiceSheet.tsx` | 语音层：读 `snapshot` + 当前轮，**零自有状态**；detent / 收起手势 / 打断 / 大光球 / 转写 / 回答 / 主卡 / chips / S2S 告知条 | reanimated、RNGH、`CardRenderer`、`FollowUpChips`、`EdgeGlow` | T3（T5/T7/T9 追加） |
| `mobile/src/core/voice/tapTalk.ts` | `TapTalkSession`：轻点即说 = `AsrSession` + 端侧 VAD 端点 + 15s 上限；`vadEndpoint()` 生产端点、测试注入假端点 | `asr.ts`、`vad.ts`、`micBus.ts` | T6 |
| `mobile/src/ui/aurora/EdgeGlow.tsx` | 语音层顶缘 2dp 极光呼吸（1.6s，只在 listening/thinking） | reanimated、`AURORA.gradient` | T7 |
| `mobile/src/core/cards/cardGroup.ts` | `cardPriority(card)` / `splitCardGroup(items)`：按 `display_priority` 升序取主卡，其余折叠（core 层：T13 的回执也读它） | 无 RN import | T8 |
| `mobile/src/features/cards/CardGroup.tsx` | `card_group` 的渲染器：主卡全展 + 「还有 N 张 ›」竖排展开 | `CardRenderer` | T8 |
| `mobile/src/core/session/followUps.ts` | `followUpChips(followUp, candidates)`：`final.follow_up` + 候选集 → ≤4 枚 chip，文本以 `sendRouter` 能消费为准 | `candidates.ts` | T9 |
| `mobile/src/features/chat/FollowUpChips.tsx` | chips 行（语音层内；点按 = 普通 `send`） | tokens | T9 |
| `mobile/src/features/settings/S2sConsentSheet.tsx` | 端到端挡位的**一次性显式同意**（G0 实色） | tokens | T5 |
| `mobile/src/core/session/receipt.ts` | `buildReceipt(...)`：执行回执（车控四行 / 信息服务 `_prov` 展开），字段全部来自已有数据 | `actionSummary.ts` | T13 |
| `mobile/src/features/chat/ExecutionReceipt.tsx` | 回执组件：默认折叠成「已执行 · 展开回执」 | tokens | T13 |
| `mobile/src/core/presence/presenceTrail.ts` | `PresenceTrail`：20 条环形，记 `PresenceSnapshot` 变化的轴 + 变化的输入摘要（不上传） | `presence.ts` | T14 |
| `mobile/src/app/presence-trail.tsx` | 调试屏「在场轨迹」：轨迹 + 采集激活日志（`activityLog.list()` 的第一个消费方，D8） | `presenceTrail.ts`、`activityLog.ts` | T14 |
| `mobile/test/actionSummary.test.ts` `dockLabel.test.ts` `turnView.test.ts` `tapTalk.test.ts` `pttLease.test.ts` `cardGroup.test.ts` `followUps.test.ts` `speakPolicy.test.ts` `receipt.test.ts` `presenceTrail.test.ts` | 纯逻辑守卫 | jest | 各任务 |
| `mobile/e2e/05-voice-sheet-ptt.yaml` | Maestro 流 ⑤（`manual`）：按住光球 → `voice-sheet` 可见 → 松手 → 收起 | — | T15 |

### 修改

| 文件 | 改什么 | 为什么 | 任务 |
|---|---|---|---|
| `mobile/src/features/chat/usePresence.ts` | 摘要改 `actionSummary`；登记 `hfFsmChangedAt`；产 `voice` 事实（轮来源 / 层开合覆盖 / 有无回答 / 有无卡）；产 `notice`（取消 / 回声）；每次派生后喂 `presenceTrail` | D1 / D2 / §5.2 规则 1·3 / §5.1.1 取消文案 / §5.2 规则 5 / 🔁-1 | T1 T2 T3 T6 T11 T14 |
| `mobile/src/features/chat/FocusDock.tsx` | 标题两行、右侧标签按 `dockLabelMode` 让位、卡片 a11y label 补分类 | ❌-1 | T1 |
| `mobile/src/core/session/store.ts` | `noteExpired` 用 `actionSummary`；`SendOpts`（`source` / `bubbleId` / `vision`）；草稿三方法；`interruptedIds`；`queued` 按 id 计数（D9）；S2S 四方法；`visionIds`；`turnMeta` / `confirmLog`；`SpeechSink.begin` 第三参 `voice` | T1 / §5.2.1 / §5.2.2 / §5.5 / §5.3.2 / §5.2 规则 8 | T1 T4 T5 T10 T12 T13 |
| `mobile/src/core/presence/presence.ts` | `MicState` 四档 + `MIC_LABEL` 表；`ARMED_CAPSULE_MS`；error 提到 armed 之前；`voice` 输入 → `input` 开合 + `sheetDetent` + `turnSource`；`notice` 输入 → 2s 胶囊 | D2 D3 D4 D5 / §5.2 规则 1·3 / §5.1.1 / §5.2 规则 5 | T2 T3 T6 |
| `mobile/src/core/presence/fixtures.ts` | base 加 `hfFsmChangedAt`；新样本 `armed-quiet` / `error-hf-on` / `sheet-*` 三条 | 画廊要能看见「3s 后没胶囊」「免唤醒下的 error」「三档 detent」 | T2 T3 |
| `mobile/test/presence.test.ts` `presenceFixtures.test.ts` | base 补字段；privacy 轴改四档；armed 3s / error 可见；`MIC_LABEL` 覆盖；`input` / `sheetDetent`；notice；画廊「四档各有样本」 | 同上 | T2 T3 T6 |
| `mobile/src/features/chat/PrivacyRail.tsx` | 删 `micText`，行文案与颜色都取 `MIC_LABEL[mic]`；「关闭本轮麦克风」改调 `ptt.cancel()` + `hf.recycle()` | D4 D5 / D7 | T2 T6 |
| `mobile/src/features/chat/ChatScreen.tsx` | 采集点 label/颜色取 `MIC_LABEL`；语音层接线（容器高度、`sheetOverride`、胶囊 `onPress`、Composer 主球在层开时静态）；草稿/提交/丢弃接线；S2S 四回调；手势契约的 `startListening` / `interruptAndListen`；视觉先落气泡；`hf.recycle()` 替代 `reenableBargeIn` | 各任务 | T2 T3 T4 T5 T6 T10 |
| `mobile/src/features/chat/PresenceCapsule.tsx` | 有 `onPress` 时 role=`button` + `hitSlop` 到 48dp；无则维持 `text` | §4.3 + 评审「别踩」① | T3 |
| `mobile/src/features/chat/Composer.tsx` | 手势契约（轻点 / 长按 ≥300ms / 上滑取消 / 松手发送；输入框有字时只有光球可 PTT）；`orbAnimated` prop；a11y 轻点切换 | §5.1.1 | T6 |
| `mobile/src/features/chat/usePtt.ts` | recorder 换 `micLease()`；`onPartial` / `onDiscard` 出口；`tap()` / `cancel()`；`cancelledAt` | §0 第 5 条 / §5.2.1 / §5.1.1 | T4 T6 |
| `mobile/src/core/voice/asr.ts` | `AsrConfig.vadSilenceMs?` → start 帧 `vad_silence_ms`（hold 不传、tap 传） | §5.1.1 | T6 |
| `mobile/src/core/voice/handsFree.ts` | `wakeManually()` / `endUtterance()` / `recycle()`；deps 加 `onEchoDismissed?` | §5.1.1 / D7 / §5.2 规则 5 | T6 T11 |
| `mobile/src/features/chat/useHandsFree.ts` | 透出 `wake` / `endUtterance` / `recycle` / `echoAt`；`onS2sEscalated?` / `onS2sTurnEnd?` 交给调用方 | 同上 / §5.2.2 | T5 T6 T11 |
| `mobile/src/features/chat/MessageBubble.tsx` | `draft` / `interrupted` / `s2s` / `vision` 四个并列态的呈现；`isProactive` 改从 `turnView` 引；「已执行 …」行换 `ExecutionReceipt` | §5.2.1 / §5.2.2 / §5.5 / §5.3.2 | T3 T4 T5 T10 T13 |
| `mobile/src/features/cards/CardRenderer.tsx` | REGISTRY `card_group` → `CardGroup` | §5.2 规则 7 / §5.4 | T8 |
| `mobile/src/core/settings/store.ts` | `s2sConsentAt`；`speakPolicy` 三档替换 `ttsEnabled && autoplay`（存量迁移） | §5.2.2 / §5.2 规则 8 · Q11 | T5 T12 |
| `mobile/src/features/settings/SettingsScreen.tsx` | 切端到端弹一次性同意；「语音播报」两开关 → 三档；调试分区加「在场轨迹」 | 同上 / 🔁-1 | T5 T12 T14 |
| `mobile/src/core/voice/speech.ts` | `enabledFor(voice)`；`begin` 第三参；`finish` 尊重 begin 时的裁决 | §5.2 规则 8 | T12 |
| `mobile/test/sessionStore.test.ts` `settingsMeta.test.ts` `handsFree.test.ts` `voiceAsr.test.ts` | 各任务追加用例（只追加不改既有断言，看门狗三条必须仍绿） | — | T1 T4 T5 T6 T11 T12 T13 |
| `mobile/src/app/_layout.tsx` | T3：根包 `GestureHandlerRootView`（Android 上 RNGH 手势的必要条件，今天不在）；T14：注册 `presence-trail` 路由 | — | T3 T14 |
| `mobile/e2e/README.md`、`2026-08-24-mobile-app-implementation-plan.md`（只加 §B2 指针）、`docs/design/README.md`、`AGENTS.md` §4.1（只改指针） | 记录 | 收口 | T15 |

### 1.1 追溯：方案 / 评审的每条要求指到哪个任务（自检用，写完计划逐行核过）

| 来源 | 要求 | 任务 |
|---|---|---|
| 评审 §6 ① / D1 / 🔁-4 | 承诺卡摘要取紧邻上一条用户原话；同时验 Dock 标题、`noteExpired` 留痕、两条并存；❌-1 右侧标签让位 | **T1** |
| 评审 §6 ② / D2 D3 D4 D5 / B1 出账② | armed 胶囊 3s 隐藏；error 不被 armed 遮蔽；判据层加 `cloudAsr`；隐私栏文案与颜色同源；读屏 label 同源；连带类型 / 画廊样本 / 覆盖度守卫 | **T2** |
| 方案 §5.2 规则 1 | 三种说话方式一张层，`snapshot.input === 'voice-sheet'` 升起，文字不升层 | T3 |
| 方案 §5.2 规则 3 | 自适应 detent 40/62/78；收起时机（追问窗关 / 下拉 / 点收起 / 点主卡按钮）；`attention` 不自动收 | T3（行车档 +3s 自动收 → B4） |
| 方案 §5.2 规则 4 | 打断：层不收、光球 speaking→listening、文字定格标「已打断」不改红 | T3（免唤醒路径）+ T4（留痕）+ T6（PTT 路径的「再听」） |
| 方案 §4.3 / 评审「别踩」① | 点按胶囊 = 打开语音层，同时改 role 与热区 | T3 |
| 方案 §11.4 性能 | 同屏循环动画常态 1 个：层开时 Composer 球转静态 | T3 |
| 方案 §5.2 规则 2 / §5.2.1 | 增量沉淀：`draft_user → final_user`、取消即删、`draft_assistant` 已是逐 delta；恢复不假装；异常退出不写 | **T4** |
| 评审 D9 | `queued` 取消一轮要减 | T4 |
| 方案 §5.2.2 / §11.2 B2 | S2S 轮记 `source:'s2s' + transcriptKind` 语义、角标「端到端」、长按提示「转写由语音模型生成」；开录即告知首行 G0；设置里切挡位弹一次性显式同意；副作用只走主链、逃逸轮按普通轮渲染 | **T5** |
| 评审 §6 ③ | S2S 首次显式同意放 B2 设置页那一半 | T5 |
| 方案 §5.1.1 表 + 配套规则 / Q2 | 轻点始终能说（与免唤醒开关无关）；录音中轻点=结束提交；播报中轻点=先停 TTS；长按 ≥300ms PTT；按住上滑取消 + 隐私文案；松手发送；12dp 移动阈值；输入框有字时只有光球可 PTT；TalkBack 轻点切换 + label 随状态；热区 ≥48dp；免唤醒开关职责收窄 | **T6** |
| 评审 D7 | `reenableBargeIn` 复用与 50ms 假窗口 → 语音层给「结束本轮收音」正式实现 | T6 |
| §0 第 5 条（本计划新发现） | PTT 在免唤醒下坏 → `micLease()` | T6（前置） |
| 方案 §5.2 规则 6 | 顶缘 2dp 极光呼吸 1.6s，只在 listening/thinking，零依赖 | **T7** |
| 方案 §5.2 规则 7 / §5.4 | `card_group` 按 `display_priority` 取主卡、其余折叠竖排展开；行高不在本批（B4 无障碍/触控批） | **T8** |
| 方案 §5.2 图 / §11.1 B2 行 | follow-up chips（`final.follow_up` + 候选集） | **T9** |
| 方案 §5.5 | `looking`：气泡**立刻**出现带 📷 角标，`vision_frame_id` 迟到补进 meta；不做预览 | **T10** |
| 方案 §5.2 规则 5 | 回声命中时胶囊短显「像是我自己的声音，没算数」2s | **T11** |
| 方案 §5.2 规则 8 / Q11 | 播报三档 总是/静音/自动，默认自动，迁移规则 | **T12** |
| 方案 §5.3.2 | 执行回执四行 + 信息服务 `_prov` 展开；默认折叠；「安全检查」留位 | **T13** |
| 🔁-1 / 评审 §6 ④ / D8 | `PresenceSnapshot` 变化轨迹 + 调试屏页；`activityLog.list()` 有消费方 | **T14** |
| 方案 §11.1「B2→B3 闸」/ §11.2 B2 / §11.3 流 05 / §11.4 | 真人语音轮五项读数 + 5 人外部小样本；B2 真机验收 7 条；Maestro 05 `manual`；八条判据取数 | **T15** |
| 方案 §0 / §11.1 | 零新原生依赖、不重建 APK | 全部（§0 第 1 条） |
| CLAUDE.md §5 S2S 红线 | 会话内唯一工具 `escalate`，不注入 capability；三条件（默认 classic / 显式选 / 文案）| T5 只让它**可见**，不改任何一条 |

---
## 2. 任务清单

> 每个任务：**Files** → 为什么/落点 → 步骤（写失败测试 → 跑红 → 实现 → 跑绿 → `tsc`）→ **反向验证**（注入缺陷要红在自己那条上、对照仍绿）→ 提交（只加自己的路径）。代码块是**完整代码**，不是示意；改既有文件的地方给「锚点 + 替换后的整段」——那些片段里的 `…` **只表示原样保留的既有代码**（旁边都写了「原样 / 其余不变」），不是待填的占位符。

### Task 1: D1——承诺卡摘要改取紧邻的上一条用户原话（+ ❌-1 标签让位）

**Files:**
- 新建 `mobile/src/core/session/actionSummary.ts`、`mobile/src/features/chat/dockLabel.ts`
- 新建 `mobile/test/actionSummary.test.ts`、`mobile/test/dockLabel.test.ts`
- 修改 `mobile/src/features/chat/usePresence.ts`（摘要取值处）、`mobile/src/core/session/store.ts`（`noteExpired`）、`mobile/src/features/chat/FocusDock.tsx`（标题两行 + 标签让位 + a11y）、`mobile/test/sessionStore.test.ts`（追加 1 条）

**为什么**：评审 D1 / 🔁-4——`usePresence.ts:97` 取的是带 `operationId` 的助手气泡正文，而那句在端侧是硬编码通用句（`orchestrator/edge/edge_call.py:272`），于是 Dock 标题恒为「这项操作可能影响车辆…」、两条并存时两张卡逐字相同、200% 下被挤成「这..」（❌-1）。客户端可得的正确源是**紧邻的上一条用户原话**。**三处同时验**：Dock 标题、`noteExpired` 留痕行、`attention-two` 两条并存——所以摘要函数只能有一份，两个出口都从它取（「同一个值有几个出口，就在入口处判一次」）。❌-1 的表象另有一半在 `FocusDock.tsx:97-99` 的右侧固定标签随字号同比放大——给它让位规则；**`numberOfLines` / `flexShrink` 单独不算修法**（评审原话），它们只在摘要源修对之后才有意义。

- [ ] **步骤 1：写失败测试**

`mobile/test/actionSummary.test.ts`：

```ts
// mobile/test/actionSummary.test.ts
// 承诺卡 / 到期留痕的摘要源（评审 D1 / 🔁-4）：紧邻的上一条用户原话，不是助手那句通用确认句。
import type { Msg } from '@shared/types.ts'

import { SUMMARY_MAX, actionSummary } from '@/core/session/actionSummary'

const u = (id: string, text: string): Msg => ({ id, role: 'user', text })
const a = (id: string, text: string, operationId?: string): Msg => ({
  id,
  role: 'assistant',
  text,
  ...(operationId ? { needConfirm: true, operationId } : {}),
})
/** 端侧硬编码的那句（edge_call.py:272）——每个危险动作都是它 */
const GENERIC = '这项操作可能影响车辆安全，请确认是否继续。'

test('Dock 标题：取紧邻的上一条用户原话，不取带 operation_id 的助手气泡正文', () => {
  const msgs = [u('u1', '打开后备箱'), a('a1', GENERIC, 'op1')]
  expect(actionSummary(msgs, 'op1')).toBe('打开后备箱')
})

test('两条并存：两张卡的摘要逐字不同（今天 attention-two 形态两张卡逐字相同）', () => {
  const msgs = [u('u1', '打开后备箱'), a('a1', GENERIC, 'op1'), u('u2', '解锁车门'), a('a2', GENERIC, 'op2')]
  expect(actionSummary(msgs, 'op1')).toBe('打开后备箱')
  expect(actionSummary(msgs, 'op2')).toBe('解锁车门')
  expect(actionSummary(msgs, 'op1')).not.toBe(actionSummary(msgs, 'op2'))
})

test('「确认」「取消」是台账回复不是原话：跳过它们往前找', () => {
  const msgs = [u('u1', '打开后备箱'), a('a1', GENERIC, 'op1'), u('u2', '确认'), a('a2', GENERIC, 'op2')]
  expect(actionSummary(msgs, 'op2')).toBe('打开后备箱')
})

test('找不到对应助手气泡 / 前面没有用户原话 → 空串（兜底文案由调用方决定）', () => {
  expect(actionSummary([], 'op1')).toBe('')
  expect(actionSummary([a('a1', GENERIC, 'op1')], 'op1')).toBe('')
  expect(actionSummary([u('u1', '打开后备箱'), a('a1', GENERIC, 'op1')], 'op9')).toBe('')
})

test('空白归一 + 截到 SUMMARY_MAX（Dock 一行 / 留痕一句）', () => {
  const long = '帮我把  后备箱\n打开一下然后再把车窗也都关上好吗谢谢你了'
  const s = actionSummary([u('u1', long), a('a1', GENERIC, 'op1')], 'op1')
  expect(s).not.toMatch(/\s{2,}|\n/)
  expect([...s].length).toBeLessThanOrEqual(SUMMARY_MAX)
  expect(s.startsWith('帮我把 后备箱 打开')).toBe(true)
})
```

`mobile/test/dockLabel.test.ts`：

```ts
// mobile/test/dockLabel.test.ts
// 承诺卡右侧固定标签的让位规则（评审 ❌-1）：标题是承诺卡的全部价值，抢一行时让的是标签。
import { LABEL_HIDE_FONT_SCALE, dockLabelMode } from '@/features/chat/dockLabel'

test.each([
  ['normal', 1.0, 'full'],
  ['normal', 1.29, 'full'],
  ['normal', LABEL_HIDE_FONT_SCALE, 'hidden'],
  ['normal', 2.0, 'hidden'], // B1 验收表第 9 条那台机（settings put system font_scale 2.0）
  ['large', 1.0, 'hidden'], // App 内「大字」档本身已 ×1.15，再加标签就抢标题
] as const)('pref=%s × 系统字号 %s → %s', (pref, sys, mode) => {
  expect(dockLabelMode(pref, sys)).toBe(mode)
})
```

`mobile/test/sessionStore.test.ts` 在 `describe('UX v2.1 B1-4：承诺面的账本侧')` 末尾追加：

```ts
  test('到期留痕的摘要是用户原话，不是那句通用确认句（评审 D1 的第二个出口）', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final',
      request_id: rid,
      speech: '这项操作可能影响车辆安全，请确认是否继续。',
      need_confirm: true,
      operation_id: 'op1',
    })
    jest.advanceTimersByTime(300_000 + 1_100)
    const last = msgs(core)[msgs(core).length - 1]
    expect(last.text).toContain('「打开后备箱」的确认已过期')
    expect(last.text).not.toContain('可能影响')
    core.dispose()
  })
```

- [ ] **步骤 2：跑红** — `cd mobile && npx jest test/actionSummary.test.ts test/dockLabel.test.ts test/sessionStore.test.ts`：前两个文件「模块不存在」红；第三个新用例红在 `not.toContain('可能影响')`（今天的留痕取的正是那句）。

- [ ] **步骤 3：实现**

`mobile/src/core/session/actionSummary.ts`：

```ts
// mobile/src/core/session/actionSummary.ts
// 承诺卡 / 到期留痕的「动作摘要」（方案 §5.3 v2.2 🔁-4、评审 D1）。
//
// **今天协议里没有动作名**：端侧车控确认的 `speech` 是硬编码通用句
// （`orchestrator/edge/edge_call.py:272`「这项操作可能影响车辆安全，请确认是否继续。」），
// `final` 里也没有任何动作名字段。B1 取的是带 operation_id 的助手气泡正文 ⇒ 每个危险动作
// 都是同一句话：两条并存时两张卡逐字相同，200% 字号下退化成「这..」（评审 ❌-1）。
// 客户端可得的正确源是**紧邻的上一条用户原话**（实拍里就是「打开后备箱」）；
// 结构化的 action / target / impact 随方案 Q16 的 `confirm_policy` 一起挂账后端。
//
// **同一个值有几个出口，就在入口处判一次**：Dock 标题（usePresence）与到期留痕
// （store.noteExpired）都从这里取，别再各抄一份 `messages.find(...)?.text`。零 RN import。
import type { Msg } from '@shared/types.ts'

/** 摘要上限（与 B1 的 24 同值：Dock 标题一行 / 留痕一句） */
export const SUMMARY_MAX = 24

/** 台账回复的字面值——`store.confirmReply` 追加的用户气泡就是这两个字，它们不是原话 */
const CONFIRM_REPLIES = new Set(['确认', '取消'])

/** 紧邻的上一条用户原话；找不到返回空串（兜底文案由调用方决定） */
export function actionSummary(messages: readonly Msg[], operationId: string): string {
  const at = messages.findIndex((m) => m.role === 'assistant' && m.operationId === operationId)
  if (at < 0) return ''
  for (let i = at - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.role !== 'user') continue
    const text = m.text.replace(/\s+/g, ' ').trim()
    if (!text || CONFIRM_REPLIES.has(text)) continue
    return text.slice(0, SUMMARY_MAX)
  }
  return ''
}
```

`mobile/src/features/chat/dockLabel.ts`：

```ts
// mobile/src/features/chat/dockLabel.ts
// 承诺卡右侧固定标签「危险动作 · 需二次确认」的让位规则（评审 ❌-1）。
// 标题（用户原话）是承诺卡的全部价值，标签只是分类；两者抢一行时**让的是标签不是标题**。
// 判据是系统字号倍数（`useWindowDimensions().fontScale`）与 App 内「大字」档——纯函数，jest 直接跑。
import type { FontScalePref } from '@/core/settings/store'

/** 系统字号放大到这个倍数起隐藏标签（Android「大」档 = 1.3；200% = 2.0） */
export const LABEL_HIDE_FONT_SCALE = 1.3

export type DockLabelMode = 'full' | 'hidden'

export function dockLabelMode(pref: FontScalePref, windowFontScale: number): DockLabelMode {
  if (pref === 'large') return 'hidden'
  return windowFontScale >= LABEL_HIDE_FONT_SCALE ? 'hidden' : 'full'
}
```

`mobile/src/features/chat/usePresence.ts`——锚点 `// pendingOps 的摘要：带该 operationId 的助手气泡原话`，整段替换：

```ts
  // pendingOps 的摘要：**紧邻的上一条用户原话**（评审 D1）。带 operationId 的那条助手气泡
  // 对每个危险动作都是同一句通用话，不是摘要。判据在 actionSummary.ts，留痕行也从它取。
  const ops = pendingOps.map((op) => ({
    id: op.id,
    ts: op.ts,
    summary: actionSummary(messages, op.id) || '待确认的操作',
  }))
```
并在 import 区加 `import { actionSummary } from '@/core/session/actionSummary'`。

`mobile/src/core/session/store.ts`——`noteExpired` 整段替换（import 加 `import { actionSummary } from './actionSummary'`）：

```ts
  /** 到期留痕：摘要取**紧邻的上一条用户原话**（actionSummary，与 Dock 标题同源），追加一条说明 */
  private noteExpired(operationId: string): void {
    const summary = actionSummary(this.store.getState().messages, operationId)
    this.appendMessage({
      id: uid(),
      role: 'assistant',
      text: summary ? `⏱ 「${summary}」的确认已过期，需要的话再说一次` : '⏱ 刚才那条确认已过期，需要的话再说一次',
    })
  }
```

`mobile/src/features/chat/FocusDock.tsx`——`CommitmentCard` 的 confirm 分支头部整段替换（import 加 `useWindowDimensions` 与 `import { dockLabelMode } from './dockLabel'`）：

```tsx
  // 右侧标签的让位（评审 ❌-1）：200% 字号下它随标题同比放大、把标题挤成「这..」。
  // 隐藏时把分类并进标题的读屏 label，信息不丢，只是不再抢那一行。
  const { fontScale: sysScale } = useWindowDimensions()
  const labelMode = dockLabelMode(fontScale, sysScale)
  const kindLabel = item.kind === 'confirm' ? (item.subkind === 'location' ? '位置授权' : '危险动作 · 需二次确认') : ''
```

```tsx
          <View accessibilityLiveRegion="assertive" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body, 'text', fontScale) }}>⚠</Text>
            <Text
              numberOfLines={2}
              accessibilityLabel={labelMode === 'hidden' ? `${kindLabel}：${item.summary}` : undefined}
              style={{ color: p.fg1, fontSize: scale(TYPE.body, 'text', fontScale), fontWeight: '600', flex: 1, flexShrink: 1 }}
            >
              {item.summary}
            </Text>
            {labelMode === 'full' ? (
              <Text style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale), flexShrink: 0 }}>{kindLabel}</Text>
            ) : null}
          </View>
```

- [ ] **步骤 4：跑绿 + `tsc`** — 三个测试文件绿；`npm run typecheck` 0。

- [ ] **反向验证**（每条注入后只红自己那条，还原后全绿）：
  1. `actionSummary` 改成 `return messages[at].text.slice(0, SUMMARY_MAX)`（B1 的取法）⇒ `actionSummary.test` 前三条 + `sessionStore` 新用例红；`dockLabel.test` 仍绿。
  2. `dockLabelMode` 的 `>=` 改 `>` ⇒ `dockLabel.test` 只红 `LABEL_HIDE_FONT_SCALE` 那一行。
  3. 真机（第 1 批收口时）：`settings put system font_scale 2.0` → 说「打开后备箱」→ Dock 标题两行显示原话、右侧标签不渲染（截图 `b2-01-font200-dock.png`）；还原 1.0 → 标签回来。再造两条并存（「打开后备箱」→「解锁车门」）→ 「另有 1 个待处理 ›」两张卡文案不同（`b2-01-attention-two.png`）。

- [ ] **提交**（同一条命令链）：
```bash
git add -- mobile/src/core/session/actionSummary.ts mobile/src/features/chat/dockLabel.ts mobile/test/actionSummary.test.ts mobile/test/dockLabel.test.ts && git commit -m "fix(mobile): UX v2 B2-1 承诺卡摘要取紧邻上一条用户原话（D1）+ 右侧标签让位（❌-1）" -- mobile/src/core/session/actionSummary.ts mobile/src/features/chat/dockLabel.ts mobile/test/actionSummary.test.ts mobile/test/dockLabel.test.ts mobile/src/features/chat/usePresence.ts mobile/src/core/session/store.ts mobile/src/features/chat/FocusDock.tsx mobile/test/sessionStore.test.ts && git show --stat HEAD
```

### Task 2: D2/D3/D4/D5——胶囊与隐私栏的判据修正（一组，不拆）

**Files:**
- 修改 `mobile/src/core/presence/presence.ts`（`MicState` 四档 + `MIC_LABEL`；`ARMED_CAPSULE_MS`；`hfFsmChangedAt` 输入；error 提到 armed 之前）
- 修改 `mobile/src/core/presence/fixtures.ts`（base 补字段 + 三条新样本）、`mobile/test/presence.test.ts`（base 补字段、privacy 轴改写、四条新用例）、`mobile/test/presenceFixtures.test.ts`（四档覆盖守卫）
- 修改 `mobile/src/features/chat/usePresence.ts`（登记 `hfFsmChangedAt` + tick 条件）、`mobile/src/features/chat/PrivacyRail.tsx`（删 `micText`，取 `MIC_LABEL`）、`mobile/src/features/chat/ChatScreen.tsx`（采集点取 `MIC_LABEL`）

**为什么**：四条缺陷在两条判据链上——D2（armed 胶囊永不隐藏）与 D3（error 被 armed 遮蔽）在 `presence.ts:187-188` 同一条 if/else 链；D4（读屏「本机处理」假话）与 D5（「关」涂琥珀）在同一个 `privacy.mic` 判据上。**在判据层加第四档 `cloudAsr` 能一次堵掉所有出口**（评审 §6 ②）；逐处改文案会再漏下一个出口——D4 就是这么活下来的（B1 第 3 批改了隐私栏文案，`ChatScreen.tsx:317` 的读屏 label 原样留着）。加档会动 `PresenceSnapshot` 类型、画廊样本与覆盖度守卫，正因如此 B1 刻意不动，现在该动了。

- [ ] **步骤 1：写失败测试**

`mobile/test/presence.test.ts`：① `base()` 加一行 `hfFsmChangedAt: NOW - 1000,`；② 把 `test('privacy 轴：唤醒词待机=edge；…PTT=edge…')` 及其上方那段「edge 并了两件事」的注释整体替换为：

```ts
  // B2 T2：`privacy.mic` 从三档改四档。B1 的 `edge` 并了「唤醒词待机（端侧 KWS，一个字节不出机）」
  // 与「正在录音（音频上传给服务端 ASR）」两件事，隐私栏因此说过假话（第 3 批坑②）。
  // 现在四档各说各的：off / edge / cloudAsr / cloudAudio。
  test('privacy 轴四档：待机=edge；PTT 与免唤醒三段式收音=cloudAsr；端到端收音=cloudAudio；空闲=off', () => {
    const hfOn = { hfEnabled: true, hfUsable: true }
    expect(derivePresence(base({ ...hfOn, hfFsm: 'ARMED' })).privacy.mic).toBe('edge')
    expect(derivePresence(base({ ptt: 'recording' })).privacy.mic).toBe('cloudAsr')
    expect(derivePresence(base({ ptt: 'finalizing' })).privacy.mic).toBe('cloudAsr') // 识别中也在传
    expect(derivePresence(base({ ...hfOn, hfFsm: 'LISTENING' })).privacy.mic).toBe('cloudAsr')
    expect(derivePresence(base({ ...hfOn, hfFsm: 'LISTENING', voicePipeline: 's2s' })).privacy.mic).toBe('cloudAudio')
    // 挡位选了 s2s 但用户按住光球：走的是服务端 ASR，不是原始音频上传——档要说真话
    expect(derivePresence(base({ ptt: 'recording', voicePipeline: 's2s' })).privacy.mic).toBe('cloudAsr')
    expect(derivePresence(base()).privacy.mic).toBe('off')
    expect(derivePresence(base({ visionCapturing: true })).privacy.camera).toBe('singleFrame')
  })

  test('MIC_LABEL：四档齐全；只有两个「上传」档是琥珀（评审 D5：「关」不许再涂琥珀）；读屏不许再说「本机处理」（评审 D4）', () => {
    expect(Object.keys(MIC_LABEL).sort()).toEqual(['cloudAsr', 'cloudAudio', 'edge', 'off'])
    expect(MIC_LABEL.off.tone).toBe('plain')
    expect(MIC_LABEL.edge.tone).toBe('plain')
    expect(MIC_LABEL.cloudAsr.tone).toBe('amber')
    expect(MIC_LABEL.cloudAudio.tone).toBe('amber')
    for (const v of Object.values(MIC_LABEL)) {
      expect(v.short).not.toContain('本机处理')
      expect(v.long).not.toContain('本机处理')
    }
  })
```

③ 在 `describe('capsule 文案')` 里追加：

```ts
  test('armed 胶囊只在进入待机 3s 内显示，之后无胶囊（方案 §4.2，评审 D2）', () => {
    const on = { hfEnabled: true, hfUsable: true, hfFsm: 'ARMED' }
    expect(derivePresence(base({ ...on, hfFsmChangedAt: NOW - 2_999 })).capsule?.text).toBe('说「小舟小舟」')
    expect(derivePresence(base({ ...on, hfFsmChangedAt: NOW - 3_000 })).capsule).toBeUndefined()
    expect(derivePresence(base({ ...on, hfFsmChangedAt: NOW - 600_000 })).capsule).toBeUndefined()
    // 胶囊隐藏了，光球仍是 armed——这是胶囊的判据，不是在场的判据
    expect(derivePresence(base({ ...on, hfFsmChangedAt: NOW - 600_000 })).primary).toBe('armed')
  })

  test('error 在免唤醒开着（armed）时也出红胶囊——显式排在 armed 之前，不靠 D2 顺手带走（评审 D3）', () => {
    // 刚进待机 100ms：armed 胶囊也在自己的 3s 窗内，两者同时成立时 error 赢
    const on = { hfEnabled: true, hfUsable: true, hfFsm: 'ARMED', hfFsmChangedAt: NOW - 100 }
    expect(derivePresence(base({ ...on, lastError: { text: '出错了', at: NOW - 500 } })).capsule).toEqual({
      text: '出错了',
      tone: 'red',
    })
  })
```

④ import 行改为 `import { MIC_LABEL, derivePresence, type PresenceInput } from '@/core/presence/presence'`。

`mobile/test/presenceFixtures.test.ts` 追加（import 加 `type MicState`）：

```ts
test('privacy.mic 四档各有样本（B2 T2 加档：画廊要能看见每一档的文案与颜色）', () => {
  const covered = new Set(presenceFixtures().map((f) => f.snapshot.privacy.mic))
  const MICS: MicState[] = ['off', 'edge', 'cloudAsr', 'cloudAudio']
  expect(MICS.filter((m) => !covered.has(m))).toEqual([])
})

test('armed 有「胶囊在窗内」与「3s 后无胶囊」两条样本（评审 D2 的画廊证据）', () => {
  const armed = presenceFixtures().filter((f) => f.snapshot.primary === 'armed')
  expect(armed.some((f) => f.snapshot.capsule?.text === '说「小舟小舟」')).toBe(true)
  expect(armed.some((f) => f.snapshot.capsule === undefined)).toBe(true)
})
```

- [ ] **步骤 2：跑红** — `npx jest test/presence.test.ts test/presenceFixtures.test.ts`：`tsc` 层面 `hfFsmChangedAt` 未知字段 / `MIC_LABEL` 不存在先红；跑得起来后 privacy 四档、armed 3s、D3、覆盖守卫各红。

- [ ] **步骤 3：实现**

`mobile/src/core/presence/presence.ts` 完整替换为：

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
// B2 T2 判据修正（B1 落地评审 D2–D5，一组）：
//  · armed 胶囊只在进入待机后 ARMED_CAPSULE_MS 内显示（方案 §4.2「3s 后隐藏」）。输入是
//    「FSM 什么时候变的」这个**事实**（收集器登记），「显示多久」这个判据只在这里；
//  · errorLive 提到 armed 之前——免唤醒开着时 error 胶囊原来永远出不来；
//  · 麦克风隐私档从三档改四档 `MicState`；`MIC_LABEL` 是所有出口（隐私栏行 / 采集点 / 读屏 label）
//    的唯一文案与颜色表——「同一个值有几个出口，就在入口处判一次」。
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

/** 麦克风隐私档（四档）。B1 的 `edge` 并了「唤醒词待机（端侧 KWS，一个字节不出机）」与
 *  「正在录音（音频上传给服务端 ASR，识别完只留文字）」两件事——App 的 PTT 与免唤醒三段式
 *  都连 `ws://…/api/asr/stream`，音频是上传的；合成一句「转文字后只上传文字」在录音那一刻
 *  就是假话，而隐私栏存在的全部理由是它说的是真的。 */
export type MicState = 'off' | 'edge' | 'cloudAsr' | 'cloudAudio'

/** 四档的文案与颜色——**唯一的一份**。short 给采集点 / 读屏 label，long 给隐私栏那一行；
 *  tone=amber 只给两个「音频离机」的档（评审 D5：「关」与「待机」不许涂琥珀，警示色贬值）。 */
export const MIC_LABEL: Record<MicState, { short: string; long: string; tone: 'plain' | 'amber' }> = {
  off: { short: '关', long: '关', tone: 'plain' },
  edge: { short: '唤醒词监听在本机，不上传', long: '唤醒词待机（端侧监听，不上传）', tone: 'plain' },
  cloudAsr: {
    short: '正在录音，音频上传做识别',
    long: '正在录音 · 音频上传到语音识别服务（识别完只留文字）',
    tone: 'amber',
  },
  cloudAudio: { short: '正在上传原始音频', long: '原始音频上传中（端到端对话）', tone: 'amber' },
}

export interface PresenceInput {
  now: number
  connStatus: 'connecting' | 'open' | 'closed'
  /** 上次 connStatus 变化的时刻（reconnecting 3s 延迟用） */
  connChangedAt: number
  hfEnabled: boolean
  hfUsable: boolean
  hfFsm: 'IDLE' | 'ARMED' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'FOLLOWUP' | string
  /** 上次 hfFsm 变化的时刻（armed 胶囊 3s 隐藏的基准，评审 D2）。收集器登记事实，判据在这里 */
  hfFsmChangedAt: number
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
  /** 输入的 `now` 原样带下来。**Dock 的倒计时只许读它**——组件自己起秒表就是第二个不同步的
   *  1s 时钟，而生产路径上 `derivePresence` 每秒现造新的 `DockItem`，那份秒表会被每秒
   *  cleanup 重建、本地 now 冻在挂载那一刻（第 2 批坑⑤，取证屏与生产路径输入形态相反）。 */
  now: number
  transport: 'online' | 'reconnecting' | 'offline'
  capture: 'off' | 'armed' | 'listening' | 'recognizing' | 'looking'
  agent: 'idle' | 'thinking' | 'processing' | 'speaking' | 'followup'
  commitment: DockItem[]
  privacy: { mic: MicState; camera: 'off' | 'singleFrame'; user: string }
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
/** armed 胶囊「说「小舟小舟」」只在进入待机后短显（方案 §4.2，评审 D2）；光球的 armed 青环不受它影响 */
export const ARMED_CAPSULE_MS = 3000

export function derivePresence(i: PresenceInput): PresenceSnapshot {
  // ── transport ──
  const transport: PresenceSnapshot['transport'] =
    i.connStatus === 'open' ? 'online' : i.connStatus === 'connecting' ? 'reconnecting' : 'offline'
  const reconnectingShown = transport === 'reconnecting' && i.now - i.connChangedAt >= RECONNECTING_GRACE_MS

  // ── capture ──
  const hfOn = i.hfEnabled && i.hfUsable
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
  // 端到端只在免唤醒的 LISTENING 期推流（s2sClient 的 collecting 门控）；PTT 即便挡位选了 s2s
  // 也走服务端 ASR——档位说的必须是此刻真发生的事
  const s2sCollecting = hfOn && i.hfFsm === 'LISTENING' && i.voicePipeline === 's2s'
  const mic: MicState = s2sCollecting ? 'cloudAudio' : micActive ? 'cloudAsr' : capture === 'armed' ? 'edge' : 'off'
  const privacy: PresenceSnapshot['privacy'] = {
    mic,
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
  const armedCapsule = capture === 'armed' && i.now - i.hfFsmChangedAt < ARMED_CAPSULE_MS
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
  // error 在 armed 之前（评审 D3）：免唤醒开着时 capture 恒 armed，排后面就永远出不来
  else if (errorLive) capsule = { text: i.lastError!.text, tone: 'red' }
  else if (armedCapsule) capsule = { text: '说「小舟小舟」', tone: 'neutral' }

  const input: PresenceSnapshot['input'] =
    capture === 'listening' || capture === 'recognizing' ? 'voice-sheet' : 'composer'

  return {
    now: i.now,
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

`mobile/src/core/presence/fixtures.ts`：`base` 里 `hfFsm: 'IDLE'` 后加 `hfFsmChangedAt: NOW - 500,`（刚进态：armed 样本的胶囊在窗内）；`return [...]` 里 `mk('armed', hf('ARMED')),` 之后插入：

```ts
    // 评审 D2：进入待机 3s 后胶囊消失、青环仍在——画廊要能看见「没有胶囊」这个态
    mk('armed-quiet', hf('ARMED', { hfFsmChangedAt: NOW - 10_000 })),
    // 评审 D3：免唤醒开着时 error 也要出得来（此前被 armed 遮蔽）
    mk('error-hf-on', hf('ARMED', { hfFsmChangedAt: NOW - 10_000, lastError: { text: '出错了', at: NOW - 500 } })),
```

`mobile/src/features/chat/usePresence.ts`——在 `connChangedAt` 那段之后插入，并把 `needsTick` 与 `derivePresence` 调用补上字段（import 加 `ARMED_CAPSULE_MS`）：

```ts
  // hf.fsm 变化时刻（armed 胶囊 3s 的基准，评审 D2）：登记的是事实，判据在 presence.ts
  const hfFsmChangedAt = useRef(Date.now())
  const prevFsm = useRef(hf.fsm)
  if (prevFsm.current !== hf.fsm) {
    prevFsm.current = hf.fsm
    hfFsmChangedAt.current = Date.now()
  }
```

```ts
  const needsTick =
    pendingOps.length > 0 || // 确认卡倒计时（每秒要变）
    !!active?.processActive || // 长任务 8s 门槛
    (connStatus === 'connecting' && now - connChangedAt.current < RECONNECTING_GRACE_MS) || // 「正在重连…」3s 门槛
    (!!lastError && now - lastError.at < ERROR_SHOW_MS) || // error 胶囊 4s 短显
    (hf.fsm === 'ARMED' && now - hfFsmChangedAt.current < ARMED_CAPSULE_MS) // armed 胶囊 3s 隐藏
```

```ts
    hfFsm: hf.fsm,
    hfFsmChangedAt: hfFsmChangedAt.current,
```

`mobile/src/features/chat/PrivacyRail.tsx`：删掉 `micText` 函数及其头注，import 改 `import { MIC_LABEL, type PresenceSnapshot } from '@/core/presence/presence'`，麦克风那一行替换为：

```tsx
          {/* 四档文案与颜色都取 MIC_LABEL（B2 T2）：颜色与文字不许说两件事——
              B1 那条 `mic==='cloudAudio' || capture!=='armed'` 让默认空闲态的「关」也涂成琥珀（评审 D5） */}
          {row('麦克风', MIC_LABEL[snapshot.privacy.mic].long, MIC_LABEL[snapshot.privacy.mic].tone === 'amber' ? p.amber : p.fg1)}
```

`mobile/src/features/chat/ChatScreen.tsx`——`captureDot` 整段替换（import 加 `import { MIC_LABEL } from '../../core/presence/presence'`）：

```ts
  // v2 采集点（隐私栏入口旁的第二颗点）：**没在采集就不渲染**——一个常驻的灰点会让
  // 「现在到底在不在采」这件事看不出来，而这正是常开麦最该让用户一眼看见的事（方案 §5.10）。
  // 文案与颜色只取 MIC_LABEL（评审 D4：读屏 label 里那句「本机处理」在 PTT 那一刻是假话）。
  // 顺序即优先级：音频离机（两档琥珀）> 正在抓一帧 > 唤醒词待机——待机是常态、抓帧是事件，
  // 事件排在常态前面（B1 第 4 批坑⑥：待机排前面时，免唤醒开着抓帧那档永远显示不出来）。
  const mic = snapshot.privacy.mic
  const captureDot =
    mic !== 'off' && MIC_LABEL[mic].tone === 'amber'
      ? { color: p.amber, label: MIC_LABEL[mic].short }
      : snapshot.privacy.camera === 'singleFrame'
        ? { color: p.fg1, label: '正在抓一帧画面' }
        : mic !== 'off'
          ? { color: p.teal, label: MIC_LABEL[mic].short }
          : null
```

- [ ] **步骤 4：跑绿 + `tsc`** — `npx jest test/presence.test.ts test/presenceFixtures.test.ts`；然后**全量** `npm test`（`presence.ts` 类型变了，`usePresence` / `PrivacyRail` / `ChatScreen` 的编译错误只有 `tsc` 抓得到）；`npm run typecheck` 0。画廊 `xiaozhou://state-gallery?only=armed,error` 深浅各一张：`armed` 有胶囊、`armed-quiet` 无胶囊青环仍在、`error-hf-on` 红胶囊。

- [ ] **反向验证**（四条各红自己那一条）：
  1. 把 `else if (errorLive)` 与 `else if (armedCapsule)` 两行对调 ⇒ 只红「D3」用例。
  2. `armedCapsule` 改成 `capture === 'armed'`（去掉时间条件）⇒ 只红「D2」用例 + 画廊「两条样本」守卫。
  3. `MIC_LABEL.cloudAsr.tone` 改 `'plain'` ⇒ 只红 `MIC_LABEL` 用例。
  4. `mic` 派生里 `micActive ? 'cloudAsr'` 改回 `'edge'` ⇒ 只红 privacy 四档用例 + 画廊四档守卫。
  5. 真机（第 1 批收口）：① 免唤醒开 → 回对话屏连拍 0/1/2/3/4s（`b2-02-armed-{0..4}s.png`），胶囊在 ≤3s 那几帧、4s 帧没有、青环全程在；② 免唤醒开 + 发一句 + 点「■ 打断」→ 红胶囊「已打断」4s（D3，今天这一帧是「说「小舟小舟」」；⚠ T4 之后「已打断」不再是 error，这条复现只在第 1 批有效）；③ 隐私栏 PTT 行：设置 `识别引擎=不用流式（整段识别）` → 按住光球说两秒松手 → 识别中（`finalizing`，最长 10s 批处理窗）立刻点顶栏健康点 → 麦克风行「正在录音 · 音频上传到语音识别服务（识别完只留文字）」**琥珀**（`b2-02-rail-ptt.png`，B1 出账⑥收口——单指就能做，不需要双指）；④ 默认空闲态开隐私栏 → 「关」是 `fg1` 不是琥珀（`b2-02-rail-idle.png`，D5）；⑤ TalkBack 开、PTT 中焦点落健康点 → 读的是「正在录音，音频上传做识别」（D4；读不到就录屏，`//sdcard/`）。

- [ ] **提交**：
```bash
git commit -m "fix(mobile): UX v2 B2-2 胶囊与隐私栏判据修正——armed 胶囊 3s 隐藏、error 不被 armed 遮蔽、麦克风四档 MIC_LABEL 一处出口（D2–D5）" -- mobile/src/core/presence/presence.ts mobile/src/core/presence/fixtures.ts mobile/test/presence.test.ts mobile/test/presenceFixtures.test.ts mobile/src/features/chat/usePresence.ts mobile/src/features/chat/PrivacyRail.tsx mobile/src/features/chat/ChatScreen.tsx && git show --stat HEAD
```
### Task 3: Voice Sheet——三入口一张层（升起 / detent / 收起 / 打断 / 胶囊点按）

**Files:**
- 新建 `mobile/src/features/chat/VoiceSheet.tsx`、`mobile/src/core/session/turnView.ts`、`mobile/test/turnView.test.ts`
- 修改 `mobile/src/core/presence/presence.ts`（`VoiceFacts` 输入 → `input` 开合 + `sheetDetent` + `turnSource`）、`mobile/src/core/presence/fixtures.ts`（三条 detent 样本）、`mobile/test/presence.test.ts`（新 describe）、`mobile/test/presenceFixtures.test.ts`（detent 守卫）
- 修改 `mobile/src/core/session/store.ts`（`TurnSource` / `TurnMeta` / `SendOpts.source`——「这一轮是谁发起的」这个事实住在记录里）、`mobile/test/sessionStore.test.ts`（追加 1 条）
- 修改 `mobile/src/features/chat/usePresence.ts`（产 `voice` 事实；`sheetOverride` 入参）、`mobile/src/features/chat/PresenceCapsule.tsx`（`onPress` 时 role=button + 48dp 热区）、`mobile/src/features/chat/Composer.tsx`（`orbAnimated`）、`mobile/src/features/chat/ChatScreen.tsx`（接线）、`mobile/src/features/chat/MessageBubble.tsx`（`isProactive` 改从 `turnView` 引）

**为什么**：方案 §5.2 规则 1「三种说话方式一张层」、规则 3「自适应 detent + 收起时机」、规则 4「打断层不收」；§4.3「点按胶囊 = 打开语音层」；§11.4「同屏循环动画常态 1 个」。**层不持有状态**（规则 2）——它渲染的是 `snapshot` + `currentTurn(messages)`，所以 T3 先于 T4 也成立：T4 把转写变成草稿气泡之后，层不用改一行就会显示草稿。开合与 detent 是**判据**，住在 `derivePresence`；层只读 `snapshot.input` / `snapshot.sheetDetent`。B1 的 `input` 只在收音时是 `'voice-sheet'`，B2 加的事实是「这一轮是语音发起的」——它住在记录里（`turnMeta[气泡].source`），收集器只做搬运。

- [ ] **步骤 1：写失败测试**

`mobile/test/turnView.test.ts`：

```ts
// mobile/test/turnView.test.ts
// 「当前这一轮」= 最后一条用户气泡 + 其后的助手气泡（语音层读它；主动播报与到期留痕不算这一轮的回答）。
import type { Msg } from '@shared/types.ts'

import { currentTurn, isAside, isProactive } from '@/core/session/turnView'

const u = (id: string, text: string): Msg => ({ id, role: 'user', text })
const a = (id: string, text: string, extra: Partial<Msg> = {}): Msg => ({ id, role: 'assistant', text, ...extra })

test('最后一条用户气泡 + 其后的助手气泡', () => {
  const t = currentTurn([u('u1', '你好'), a('a1', '你好呀'), u('u2', '天气'), a('a2', '晴')])
  expect(t.user?.id).toBe('u2')
  expect(t.assistant?.id).toBe('a2')
})

test('用户刚说完、助手还没答：assistant=null，且不许把上一轮的助手气泡当成回答', () => {
  const t = currentTurn([u('u1', '你好'), a('a1', '你好呀'), u('u2', '天气')])
  expect(t.user?.id).toBe('u2')
  expect(t.assistant).toBeNull()
})

test('主动播报与到期留痕是旁白，不是这一轮的回答', () => {
  const t = currentTurn([u('u2', '打开后备箱'), a('a2', '要打开后备箱吗？'), a('x1', '💡 前方拥堵', { proactiveKind: 'scene_suggest' }), a('x2', '⏱ 「打开后备箱」的确认已过期，需要的话再说一次')])
  expect(t.assistant?.id).toBe('a2')
  expect(isProactive(a('x1', '💡 前方拥堵'))).toBe(true)
  expect(isAside(a('x2', '⏱ 过期'))).toBe(true)
  expect(isAside(a('a2', '要打开后备箱吗？'))).toBe(false)
})

test('空记录 / 只有助手：两边都 null', () => {
  expect(currentTurn([])).toEqual({ user: null, assistant: null })
  expect(currentTurn([a('a1', '欢迎')])).toEqual({ user: null, assistant: null })
})
```

`mobile/test/presence.test.ts` 追加一个 describe（import 加 `type VoiceFacts`）：

```ts
describe('语音层开合与 detent（B2 T3，方案 §5.2 规则 1/3/4、§4.3）', () => {
  const thinking = { turn: { pending: true, streaming: false, processActive: false, processLabel: '', processSince: 0 } }
  const hfOn = { hfEnabled: true, hfUsable: true }
  const v = (over: Partial<VoiceFacts> = {}): VoiceFacts => ({ turnSource: 'ptt', override: null, answer: false, card: false, ...over })

  test('三入口收音中都升层（PTT / 免唤醒 / S2S），不需要 voice 事实', () => {
    expect(derivePresence(base({ ptt: 'recording' })).input).toBe('voice-sheet')
    expect(derivePresence(base({ ...hfOn, hfFsm: 'LISTENING' })).input).toBe('voice-sheet')
    expect(derivePresence(base({ ...hfOn, hfFsm: 'LISTENING', voicePipeline: 's2s' })).input).toBe('voice-sheet')
  })

  test('语音发起的轮在飞 / 播报 / 追问窗内层保持升起；**文字轮不升层**（voiceLoop 头注「文本不进 FSM」的 UI 版）', () => {
    expect(derivePresence(base({ ...thinking, voice: v() })).input).toBe('voice-sheet')
    expect(derivePresence(base({ speaking: true, voice: v({ turnSource: 'handsfree' }) })).input).toBe('voice-sheet')
    expect(derivePresence(base({ ...hfOn, hfFsm: 'FOLLOWUP', voice: v({ turnSource: 'handsfree' }) })).input).toBe('voice-sheet')
    expect(derivePresence(base({ ...thinking, voice: v({ turnSource: 'text' }) })).input).toBe('composer')
    expect(derivePresence(base({ ...thinking })).input).toBe('composer')
  })

  test('追问窗关闭（回 ARMED、agent idle）→ 收起', () => {
    expect(derivePresence(base({ ...hfOn, hfFsm: 'ARMED', voice: v({ turnSource: 'handsfree', answer: true }) })).input).toBe('composer')
  })

  test('attention 态下不自动收起；用户下拉才收；下拉之后再开口又升', () => {
    const att = { pendingOps: [{ id: 'op1', ts: NOW, summary: '打开后备箱' }] }
    expect(derivePresence(base({ ...att, voice: v() })).input).toBe('voice-sheet')
    expect(derivePresence(base({ ...att, voice: v({ override: 'dismissed' }) })).input).toBe('composer')
    expect(derivePresence(base({ ...att, ptt: 'recording', voice: v({ override: 'dismissed' }) })).input).toBe('voice-sheet')
  })

  test('点按胶囊 = 打开语音层（override=open），哪怕是文字轮', () => {
    expect(derivePresence(base({ ...thinking, voice: v({ turnSource: 'text', override: 'open' }) })).input).toBe('voice-sheet')
  })

  test('detent：只录音 0.4 / 有回答 0.62 / 有主卡或长任务 0.78', () => {
    expect(derivePresence(base({ ptt: 'recording' })).sheetDetent).toBe(0.4)
    expect(derivePresence(base({ ...thinking, voice: v({ answer: true }) })).sheetDetent).toBe(0.62)
    expect(derivePresence(base({ ...thinking, voice: v({ answer: true, card: true }) })).sheetDetent).toBe(0.78)
    const longTask = { turn: { pending: false, streaming: false, processActive: true, processLabel: '规划路线', processSince: NOW - 12_000 } }
    expect(derivePresence(base({ ...longTask, voice: v() })).sheetDetent).toBe(0.78)
  })

  test('turnSource 透传（S2S 告知条读它）；没有 voice 事实 = text', () => {
    expect(derivePresence(base()).turnSource).toBe('text')
    expect(derivePresence(base({ voice: v({ turnSource: 's2s' }) })).turnSource).toBe('s2s')
  })
})
```

`mobile/test/presenceFixtures.test.ts` 追加：

```ts
test('语音层三档 detent 各有一条 input=voice-sheet 的样本（B2 T3）', () => {
  const open = presenceFixtures().filter((f) => f.snapshot.input === 'voice-sheet')
  const detents = new Set(open.map((f) => f.snapshot.sheetDetent))
  expect([0.4, 0.62, 0.78].filter((d) => !detents.has(d as 0.4 | 0.62 | 0.78))).toEqual([])
})
```

`mobile/test/sessionStore.test.ts` 追加：

```ts
describe('UX v2 B2-3：轮来源（语音层开合的事实住在记录里）', () => {
  test('send 不带 opts → turnMeta[助手气泡].source=text；带 source=ptt → ptt；confirmReply 同理', () => {
    const { core } = newCore()
    core.send('天气')
    core.send('附近有什么', undefined, { source: 'ptt' })
    const [a1, a2] = assistants(core)
    const meta = core.store.getState().turnMeta
    expect(meta[a1.id].source).toBe('text')
    expect(meta[a2.id].source).toBe('ptt')
    expect(meta[a2.id].sentAt).toBeGreaterThan(0)
    core.confirmReply('确认', 'op1', { source: 'handsfree' })
    expect(meta[assistants(core)[2].id]).toBeUndefined() // 上面的 meta 是旧快照
    expect(core.store.getState().turnMeta[assistants(core)[2].id].source).toBe('handsfree')
    core.dispose()
  })
})
```

- [ ] **步骤 2：跑红** — `npx jest test/turnView.test.ts test/presence.test.ts test/presenceFixtures.test.ts test/sessionStore.test.ts`。

- [ ] **步骤 3：实现**

`mobile/src/core/session/turnView.ts`：

```ts
// mobile/src/core/session/turnView.ts
// 「当前这一轮」的判定（语音层读它，方案 §5.2 规则 2：层是记录里当前轮的视图）。
// 纯函数，零 RN import。`isProactive` 从 MessageBubble 搬到这里——同一个「这条是不是主动播报」
// 的判据两处各写一份就会漂（MessageBubble 改成从这里引）。
import type { Msg } from '@shared/types.ts'

/** 主动播报（网关 advisory 透传成 proactiveKind；老帧只有 💡 前缀） */
export function isProactive(m: Msg): boolean {
  return m.proactiveKind !== undefined || m.text.startsWith('💡 ')
}

/** 旁白：主动播报、到期留痕——它们在记录里，但不是「这一轮的回答」 */
export function isAside(m: Msg): boolean {
  return isProactive(m) || m.text.startsWith('⏱ ')
}

export interface TurnView {
  user: Msg | null
  assistant: Msg | null
}

/** 最后一条用户气泡 + 其后第一条非旁白的助手气泡（从后往前找，只看它之后的） */
export function currentTurn(messages: readonly Msg[]): TurnView {
  let ui = -1
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') {
      ui = i
      break
    }
  }
  if (ui < 0) return { user: null, assistant: null }
  let assistant: Msg | null = null
  for (let i = ui + 1; i < messages.length; i += 1) {
    const m = messages[i]
    if (m.role === 'assistant' && !isAside(m)) {
      assistant = m
      break
    }
  }
  return { user: messages[ui], assistant }
}
```

`mobile/src/core/presence/presence.ts`——四处追加：

① 文件顶部 import 之后加类型：
```ts
import type { TurnSource } from '../session/store'

/** 语音层要的三个事实（B2 T3）。全部来自记录与 UI 的既有状态，收集器只搬运：
 *  turnSource=最近一轮的发起方（turnMeta）；override=用户对这一轮的显式操作（点胶囊 / 下拉）；
 *  answer/card=当前轮助手气泡有没有字、有没有卡。没有这个入参 = 文字世界（层只在收音时升）。 */
export interface VoiceFacts {
  turnSource: TurnSource
  override: 'open' | 'dismissed' | null
  answer: boolean
  card: boolean
}

/** 自适应 detent（方案 §5.2 规则 3）：只录音 / 有回答 / 有主卡或长任务 */
export type SheetDetent = 0.4 | 0.62 | 0.78
```
② `PresenceInput` 末尾加 `voice?: VoiceFacts`；③ `PresenceSnapshot` 在 `input` 后加：
```ts
  /** 语音层高度档（input==='voice-sheet' 时有意义） */
  sheetDetent: SheetDetent
  /** 最近一轮的发起方（S2S 告知条读它；没有轮 = text） */
  turnSource: TurnSource
```
④ `const input = …` 那两行整段替换：
```ts
  // ── 语音层开合（方案 §5.2 规则 1/3/4、§4.3）──
  // 收音中一律升（三入口共用）；语音发起的轮在飞 / 播报 / 追问窗 / 等确认时保持升起；
  // 用户下拉过这一轮就不再自动升（再开口另算）；点胶囊 = 显式打开（哪怕是文字轮）。
  // PTT 轮没有追问窗：播报结束、agent 回 idle 即收（方案只给免唤醒定义了 8s 窗）。
  const voice = i.voice
  const capturing = capture === 'listening' || capture === 'recognizing'
  const voiceTurnLive = !!voice && voice.turnSource !== 'text' && (agent !== 'idle' || hasAttention)
  const sheetOpen = capturing || voice?.override === 'open' || (voice?.override !== 'dismissed' && voiceTurnLive)
  const input: PresenceSnapshot['input'] = sheetOpen ? 'voice-sheet' : 'composer'
  const sheetDetent: SheetDetent =
    commitment.some((c) => c.kind === 'task') || !!voice?.card ? 0.78 : voice?.answer ? 0.62 : 0.4
```
并在 `return` 里加 `sheetDetent,` 与 `turnSource: voice?.turnSource ?? 'text',`。

`mobile/src/core/presence/fixtures.ts` 追加三条（放在 `mk('looking', …)` 之前）：
```ts
    // B2 T3 语音层三档 detent：只录音 0.4（listening-ptt 那条就是）/ 有回答 0.62 / 有主卡 0.78；
    // 外加一条「用户下拉过」证明 dismissed 真的收得起来
    mk('sheet-answering', {
      turn: { pending: false, streaming: true, processActive: false, processLabel: '', processSince: 0 },
      voice: { turnSource: 'ptt', override: null, answer: true, card: false },
    }),
    mk('sheet-card', { speaking: true, voice: { turnSource: 'handsfree', override: null, answer: true, card: true } }),
    mk('sheet-dismissed', {
      turn: { pending: false, streaming: true, processActive: false, processLabel: '', processSince: 0 },
      voice: { turnSource: 'ptt', override: 'dismissed', answer: true, card: false },
    }),
```

`mobile/src/core/session/store.ts`——三处：

① `PendingOp` 之前加：
```ts
/** 这一轮是谁发起的。语音层只对语音发起的轮保持升起（方案 §5.2 规则 1：文字不升层）；
 *  播报三档的「自动」也读它（T12）。S2S 逃逸轮回到主链后来源仍记 s2s——它是语音发起的。 */
export type TurnSource = 'text' | 'ptt' | 'handsfree' | 's2s'

/** 轮元数据：键=助手气泡 id。`Msg` 是共享类型不能加字段，所以并列存（B1 计划 §0 第 5 条） */
export interface TurnMeta {
  sentAt: number
  source: TurnSource
}

export interface SendOpts {
  source?: TurnSource
}
```
② `SessionState` 加 `turnMeta: Record<string, TurnMeta>`，构造里初始化 `turnMeta: {}`；
③ `send` / `confirmReply` / `dispatch` 签名与调用：
```ts
  send(text: string, metaExtra?: Record<string, string>, opts: SendOpts = {}): void {
```
（函数体内四处 `this.dispatch(...)` 末尾都补上 `opts.source ?? 'text'` 这个实参；`consent` 分支不派发，不动）
```ts
  confirmReply(reply: '确认' | '取消', operationId?: string, opts: SendOpts = {}): void {
```
（体内两处 `this.dispatch(pendingText, false, loc)` / `this.dispatch(pendingText, false)` 与最后的 `this.dispatch(reply, true, undefined, undefined, operationId)` 都补 `source`）
```ts
  private dispatch(
    text: string,
    isConfirmation: boolean,
    locationMeta?: Record<string, string>,
    metaExtra?: Record<string, string>,
    operationId?: string,
    source: TurnSource = 'text',
  ): void {
```
`dispatch` 里 `this.appendMessage({ id: pendingId, … })` 之后加：
```ts
    this.store.setState((s) => ({ turnMeta: { ...s.turnMeta, [pendingId]: { sentAt: Date.now(), source } } }))
```

`mobile/src/features/chat/usePresence.ts`：
```ts
import { currentTurn } from '@/core/session/turnView'
import { derivePresence, ARMED_CAPSULE_MS, ERROR_SHOW_MS, RECONNECTING_GRACE_MS, type Degradation, type PresenceSnapshot, type VoiceFacts } from '@/core/presence/presence'

/** 用户对语音层的显式操作，钉在某一轮上（换轮即失效） */
export interface SheetOverride {
  turnId: string
  mode: 'open' | 'dismissed'
}

export interface UsePresenceOpts {
  core: SessionCore
  hf: HandsFreeUi
  ptt: PttHandle | null
  user: string
  sheetOverride: SheetOverride | null
}
```
`useStore(core.store)` 解构里加 `turnMeta`；在 `const active = …` 之前加：
```ts
  // 语音层的三个事实（判据在 derivePresence）：这一轮谁发起、用户有没有下拉过、有没有字/卡
  const turn = currentTurn(messages)
  const latestTurnId = turn.assistant?.id ?? ''
  const voice: VoiceFacts = {
    turnSource: (latestTurnId && turnMeta[latestTurnId]?.source) || 'text',
    override: sheetOverride && sheetOverride.turnId === latestTurnId ? sheetOverride.mode : null,
    answer: !!turn.assistant?.text,
    card: !!turn.assistant?.uiCard,
  }
```
`derivePresence({...})` 里加 `voice,`；函数签名解构加 `sheetOverride`。

`mobile/src/features/chat/PresenceCapsule.tsx`——`Pressable` 替换（import 加 `TARGET`）：
```tsx
      <Pressable
        testID="presence-capsule"
        onPress={onPress}
        disabled={!onPress}
        // 接了 onPress 就是按钮：role 与热区一起改（评审「别踩」①）——视觉仍 26dp，
        // hitSlop 补到 48dp（Material 触控目标是**可点区域**不是球体，方案 §8.1）
        accessibilityRole={onPress ? 'button' : 'text'}
        accessibilityHint={onPress ? '打开语音层' : undefined}
        hitSlop={onPress ? Math.ceil((TARGET.parked - 26) / 2) : undefined}
        accessibilityLiveRegion="polite"
```

`mobile/src/features/chat/Composer.tsx`：props 加 `/** 语音层开着时主球转静态（同屏循环动画常态 1 个，方案 §11.4） */ orbAnimated?: boolean`，`<AuroraOrb size={44} state={orbState} dim={orbDim} animated={orbAnimated ?? true} />`。

`mobile/src/features/chat/MessageBubble.tsx`：`const proactive = msg.proactiveKind !== undefined || msg.text.startsWith('💡 ')` 改为 `const proactive = isProactive(msg)`，import 加 `import { isProactive } from '@/core/session/turnView'`。

`mobile/src/features/chat/VoiceSheet.tsx`：

```tsx
// mobile/src/features/chat/VoiceSheet.tsx
// 语音层（方案 §5.2）：PTT / 唤醒词 / S2S 三种说话方式升起的**同一张层**。
// **它不持有任何转写 / 回答状态**——它是「对话记录里当前这一轮」的视图（§5.2 规则 2）：
// 转写读当前用户气泡、回答读当前助手气泡、卡片读 final.ui_card；收起、切后台、折叠展开都不会丢，
// 因为根本没有「等收起再写」这一步。升不升起、升多高由 derivePresence 定（`snapshot.input` /
// `snapshot.sheetDetent`），这里只渲染与转发手势。
// 材质：外壳 G1（Glass）；零新依赖——手势用随 expo-router 在场的 react-native-gesture-handler
// （PackageList.java:73 已注册），高度用 reanimated。
// 性能纪律（方案 §11.4）：层开着时它的 88dp 大球是**唯一**跑循环动画的光球，Composer 主球转静态。
import { useEffect, useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { Gesture, GestureDetector } from 'react-native-gesture-handler'
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated'

import type { PresenceSnapshot } from '@/core/presence/presence'
import type { TurnView } from '@/core/session/turnView'
import type { FontScalePref } from '@/core/settings/store'
import { CardRenderer } from '@/features/cards/CardRenderer'
import { AuroraOrb, Glass, StreamCursor, ThinkDots } from '@/ui/aurora'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export interface VoiceSheetProps {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  /** 当前这一轮（core/session/turnView.ts::currentTurn 算出来的事实） */
  turn: TurnView
  /** 层可用的高度（包裹列表的那个 View 的 onLayout 高度）；0=还没量到，不渲染 */
  containerHeight: number
  /** 下拉 / 点「收起」/ 点暗区 */
  onCollapse(): void
  /** ■ 打断：T3 只停播报与取消在飞轮；T6 接上「打断后再听」 */
  onInterrupt(): void
  onSend(text: string): void
}

/** 下拉多少算「收起」（dp） */
export const SHEET_DISMISS_DY = 80
/** 收起动画时长（ms） */
const COLLAPSE_MS = 180

export function VoiceSheet(props: VoiceSheetProps) {
  const { p, fontScale, snapshot, turn, containerHeight } = props
  const open = snapshot.input === 'voice-sheet' && containerHeight > 0
  const target = Math.round(containerHeight * snapshot.sheetDetent)
  // 挂载态比 open 晚 COLLAPSE_MS 关掉：让收起动画播完再卸载
  const [mounted, setMounted] = useState(open)
  const h = useSharedValue(0)
  useEffect(() => {
    if (open) {
      setMounted(true)
      h.value = withSpring(target, { damping: 18, stiffness: 160 })
      return
    }
    h.value = withTiming(0, { duration: COLLAPSE_MS })
    const t = setTimeout(() => setMounted(false), COLLAPSE_MS)
    return () => clearTimeout(t)
  }, [open, target, h])
  const sheetStyle = useAnimatedStyle(() => ({ height: h.value }))
  const pan = Gesture.Pan()
    .runOnJS(true)
    .onEnd((e) => {
      if (e.translationY > SHEET_DISMISS_DY) props.onCollapse()
    })
  if (!mounted) return null

  const user = turn.user
  const assistant = turn.assistant
  const busy = snapshot.agent !== 'idle'
  const body = scale(TYPE.body, 'text', fontScale)
  const target48 = scale(TARGET.parked, 'target', fontScale)
  const capsuleColor =
    snapshot.capsule?.tone === 'red' ? p.red : snapshot.capsule?.tone === 'amber' ? p.amber : snapshot.capsule?.tone === 'accent' ? p.accent : p.fg2
  return (
    <View pointerEvents="box-none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}>
      {/* 记录变暗 40%、仍可见（§5.2）：点暗区 = 收起 */}
      <Pressable
        accessibilityLabel="收起语音层"
        onPress={props.onCollapse}
        style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.4)' }}
      />
      <GestureDetector gesture={pan}>
        <Animated.View testID="voice-sheet" style={[{ position: 'absolute', left: 0, right: 0, bottom: 0 }, sheetStyle]}>
          <Glass p={p} r={RADIUS['2xl']} style={{ flex: 1, overflow: 'hidden', borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }}>
            {/* 把手（G2 只给光球与把手，§5.11） */}
            <View style={{ alignSelf: 'center', width: 36, height: 4, borderRadius: 2, backgroundColor: p.fill2, marginTop: 8 }} />
            <ScrollView contentContainerStyle={{ padding: 16, gap: 12, alignItems: 'center' }} keyboardShouldPersistTaps="handled">
              {/* 转写区：大字 20pt。T4 起它是草稿气泡（增量沉淀），定稿后仍是同一条 */}
              {user ? (
                <Text
                  testID="voice-sheet-transcript"
                  style={{ color: p.fg1, fontSize: scale(20, 'text', fontScale), lineHeight: scale(28, 'line', fontScale), textAlign: 'center' }}
                >
                  {user.text}
                  {snapshot.capture === 'recognizing' ? <StreamCursor h={scale(20, 'text', fontScale)} /> : null}
                </Text>
              ) : null}
              {/* 大光球：snapshot.primary 驱动（listening→thinking→speaking→followup）；十条不变量内 */}
              <AuroraOrb size={88} state={snapshot.primary} dim={snapshot.dim} animated />
              {/* 胶囊文案（同 §4.3，此处放大） */}
              {snapshot.capsule ? (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  {snapshot.capsule.live ? (
                    <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: p.accent, boxShadow: `0 0 10px ${p.accent}` }} />
                  ) : null}
                  <Text style={{ color: capsuleColor, fontSize: body }}>{snapshot.capsule.text}</Text>
                </View>
              ) : null}
              {/* 回答区：speech_delta 逐字 + StreamCursor；pending 时 ThinkDots */}
              {assistant?.pending ? <ThinkDots color={p.accent} /> : null}
              {assistant?.text ? (
                <Text
                  testID="voice-sheet-answer"
                  style={{ color: assistant.error ? p.red : p.fg1, fontSize: scale(TYPE.body + 1, 'text', fontScale), lineHeight: scale(24, 'line', fontScale), alignSelf: 'stretch' }}
                >
                  {assistant.text}
                  {assistant.streaming ? <StreamCursor h={scale(TYPE.body + 1, 'text', fontScale)} /> : null}
                </Text>
              ) : null}
              {/* 卡片：card_group 的主卡/折叠由 CardRenderer 的注册表决定（T8），这里不判 */}
              {assistant?.uiCard ? (
                <View style={{ alignSelf: 'stretch' }}>
                  <CardRenderer p={p} card={assistant.uiCard} onSend={props.onSend} />
                </View>
              ) : null}
            </ScrollView>
            <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 24, paddingVertical: 8, borderTopWidth: 1, borderColor: p.line }}>
              <Pressable
                testID="voice-sheet-collapse"
                accessibilityRole="button"
                onPress={props.onCollapse}
                style={{ minHeight: target48, minWidth: 96, justifyContent: 'center', alignItems: 'center' }}
              >
                <Text style={{ color: p.fg2, fontSize: body }}>⌄ 收起</Text>
              </Pressable>
              {busy ? (
                <Pressable
                  testID="voice-sheet-interrupt"
                  accessibilityRole="button"
                  onPress={props.onInterrupt}
                  style={{ minHeight: target48, minWidth: 96, justifyContent: 'center', alignItems: 'center' }}
                >
                  <Text style={{ color: p.amber, fontSize: body }}>■ 打断</Text>
                </Pressable>
              ) : null}
            </View>
          </Glass>
        </Animated.View>
      </GestureDetector>
    </View>
  )
}
```

`mobile/src/features/chat/ChatScreen.tsx` 接线（按锚点）：

① import 加：
```ts
import type { SendOpts } from '../../core/session/store'
import { currentTurn } from '../../core/session/turnView'
import { VoiceSheet } from './VoiceSheet'
import { usePresence, type SheetOverride } from './usePresence'
```
② `onSend` 签名改 `(text: string, metaExtra?: Record<string, string>, opts?: SendOpts)`，两处 `core.send(...)` 都把 `opts` 传下去；`ptt` 的 `onFinal: (text) => onSend(text, undefined, { source: 'ptt' })`；`hf` 的 `onSend: (text) => onSend(text, undefined, { source: 'handsfree' })`。
③ `usePresence` 调用之前加状态，调用补入参：
```ts
  // 语音层的显式操作（点胶囊打开 / 下拉收起），钉在当前轮上；换轮自动失效（判据在 derivePresence）
  const [sheetOverride, setSheetOverride] = useState<SheetOverride | null>(null)
  const turn = useMemo(() => currentTurn(messages), [messages])
  const latestTurnId = turn.assistant?.id ?? ''
  const [listHeight, setListHeight] = useState(0)
```
```ts
  const snapshot = usePresence({ core, hf, ptt: cfg.audioUrl ? ptt : null, user: cfg.token.slice(-4), sheetOverride })
```
④ `chatColumn` 里 `messages.length === 0 ? <Welcome…/> : <FlashList…/>` 整段包进一个量高度的容器，并把语音层放进去：
```tsx
      <View style={{ flex: 1 }} onLayout={(e) => setListHeight(Math.round(e.nativeEvent.layout.height))}>
        {messages.length === 0 ? (
          <Welcome … />  {/* 原入参不变 */}
        ) : (
          <FlashList … />  {/* 原入参不变 */}
        )}
        {v2 ? (
          <VoiceSheet
            p={p}
            fontScale={settings.fontScale}
            snapshot={snapshot}
            turn={turn}
            containerHeight={listHeight}
            onCollapse={() => setSheetOverride({ turnId: latestTurnId, mode: 'dismissed' })}
            onInterrupt={onInterrupt}
            onSend={(t) => onSend(t)}
          />
        ) : null}
      </View>
```
⑤ 胶囊接 `onPress`、Composer 主球在层开时静态：
```tsx
      {v2 ? (
        <PresenceCapsule
          p={p}
          fontScale={settings.fontScale}
          snapshot={snapshot}
          onPress={() => setSheetOverride({ turnId: latestTurnId, mode: 'open' })}
        />
      ) : null}
      <Composer
        …（其余原样）
        orbAnimated={snapshot.input !== 'voice-sheet'}
```
（v2 关闭时 `snapshot.input` 仍会算，但层不渲染、主球照旧动——回滚路径不受影响。）
⑥ `mobile/src/app/_layout.tsx`：RNGH 的 `GestureDetector` 在 Android 上必须在 `GestureHandlerRootView` 之内，而 App 今天没有任何 RNGH 手势、根上也没有它（`grep -rn GestureHandlerRootView mobile/src` 为空；expo-router 的构建产物里只有 react-navigation stack 内部用到它，**不包根**）。`return` 整段替换（JS 改动，零依赖、不重建）：
```tsx
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      {/* M4-6 视觉抓帧的采集端。挂在根布局但**平时什么都不渲染**——
          它只在真要抓帧的那一瞬挂载 CameraView，拍完立刻卸载（=关摄像头）。
          放根布局是因为抓帧可能由任何路由上的一句话触发。 */}
      <VisionCapture enabled={settings.visionEnabled} />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: p.bg },
          headerTintColor: p.fg1,
          contentStyle: { backgroundColor: p.bg },
        }}
      >
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="onboarding" options={{ title: '连接服务器' }} />
        <Stack.Screen name="settings" options={{ title: '设置' }} />
        <Stack.Screen name="vehicle" options={{ title: '车辆' }} />
        <Stack.Screen name="debug" options={{ title: '调试 · 主链帧' }} />
        <Stack.Screen name="voice-spike" options={{ title: '调试 · 语音 spike' }} />
        <Stack.Screen name="card-gallery" options={{ title: '调试 · 卡片画廊' }} />
        <Stack.Screen name="state-gallery" options={{ title: '调试 · 状态画廊' }} />
        <Stack.Screen name="map" options={{ title: '地图' }} />
      </Stack>
    </GestureHandlerRootView>
  )
```
（import 加 `import { GestureHandlerRootView } from 'react-native-gesture-handler'`；T14 再往里加 `presence-trail` 一行。）

- [ ] **步骤 4：跑绿 + `tsc`** — 四个测试文件绿、全量绿、`tsc` 0；Metro 热载：按住光球说一句 → 层升起（40%）→ 松手 → 回答流式（62%）→ 播报完收起。画廊 `?only=sheet` 三条样本的 `input` / `sheetDetent` 打印正确（画廊不渲染层本身——层要 `containerHeight`，画廊只证明判据）。

- [ ] **反向验证**：
  1. `sheetOpen` 里删掉 `voice?.override === 'open' ||` ⇒ 只红「点按胶囊」用例。
  2. `voiceTurnLive` 去掉 `voice.turnSource !== 'text'` ⇒ 只红「文字轮不升层」两行。
  3. `sheetDetent` 的 0.62 / 0.78 对调 ⇒ 只红 detent 用例 + 画廊 detent 守卫。
  4. `currentTurn` 改成「最后一条助手气泡」⇒ 只红「用户刚说完」与「旁白」两条。
  5. 真机（第 2 批收口）：三入口各一次（按住 / 说「小舟小舟」/ 端到端挡位说话）→ `voice-sheet` 可见（截图 `b2-03-sheet-{ptt,hf,s2s}.png`）；打字发送 → 层**不**升；层开着时 Composer 主球静止（连拍两帧逐字节相同）、层内大球在动；点胶囊 → 层升；下拉 80dp → 收起；有待确认时不自动收、Dock 仍在层下方可见。

- [ ] **提交**：
```bash
git add -- mobile/src/features/chat/VoiceSheet.tsx mobile/src/core/session/turnView.ts mobile/test/turnView.test.ts && git commit -m "feat(mobile): UX v2 B2-3 语音层——三入口一张层、detent 三档、收起/打断、胶囊点按（判据在 derivePresence，层零自有状态）" -- mobile/src/features/chat/VoiceSheet.tsx mobile/src/core/session/turnView.ts mobile/test/turnView.test.ts mobile/src/core/presence/presence.ts mobile/src/core/presence/fixtures.ts mobile/test/presence.test.ts mobile/test/presenceFixtures.test.ts mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts mobile/src/features/chat/usePresence.ts mobile/src/features/chat/PresenceCapsule.tsx mobile/src/features/chat/Composer.tsx mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/MessageBubble.tsx mobile/src/app/_layout.tsx && git show --stat HEAD
```

### Task 4: draft → final 增量沉淀（转写草稿气泡、打断留痕、D9）

**Files:**
- 修改 `mobile/src/core/session/store.ts`（`SendOpts.bubbleId`；`draftUserId` 三方法；`interruptedIds`；`queued` 按 id 计数）、`mobile/test/sessionStore.test.ts`（新 describe）
- 修改 `mobile/src/features/chat/usePtt.ts`（`onPartial` / `onDiscard` 出口）、`mobile/src/features/chat/ChatScreen.tsx`（草稿接线）、`mobile/src/features/chat/MessageBubble.tsx`（`draft` / `interrupted` 呈现）、`mobile/src/features/chat/VoiceSheet.tsx`（转写光标读草稿、回答区「已打断」）

**为什么**：方案 §5.2 规则 2 / §5.2.1——ASR 的每个稳定 segment **即时**写进记录（`draft_user → final_user`），取消即删、不留气泡；回答侧 `store.ts:294-298` 已是逐 delta 追加，**不是新机制**，只把转写侧对齐过来。打断（§5.2 规则 4）：文字定格并标「已打断」，**不再改成红色错误样式**——今天 `markInterrupted` 写 `error: true`，B2 起它不是错误。D9：`queued` 只增不减，断线期间取消一轮仍按旧数报——承诺型文案报错数比不报更糟。

- [ ] **步骤 1：写失败测试**（`mobile/test/sessionStore.test.ts` 追加）

```ts
describe('UX v2 B2-4：增量沉淀（方案 §5.2.1）、打断留痕（§5.2 规则 4）、D9', () => {
  test('draftUser：第一次建草稿气泡，之后只更新同一条；commit 后 send({bubbleId}) 复用不追加第二条', () => {
    const { transport, core } = newCore()
    core.draftUser('附近')
    core.draftUser('附近有什么')
    core.draftUser('附近有什么好吃的')
    expect(msgs(core).filter((m) => m.role === 'user')).toHaveLength(1)
    const id = core.store.getState().draftUserId as string
    expect(msgs(core)[0].text).toBe('附近有什么好吃的')
    expect(core.commitDraftUser()).toBe(id)
    core.send('附近有什么好吃的？', undefined, { source: 'ptt', bubbleId: id })
    const users = msgs(core).filter((m) => m.role === 'user')
    expect(users).toHaveLength(1)
    expect(users[0].id).toBe(id)
    expect(users[0].text).toBe('附近有什么好吃的？') // 定稿可能与最后一个 partial 不同，以定稿为准
    expect(core.store.getState().draftUserId).toBeNull()
    expect(transport.lastUserFrame().text).toBe('附近有什么好吃的？')
    core.dispose()
  })

  test('discardDraftUser：取消 / 误唤醒回收 / 空定稿 → 草稿删除、不留气泡；没有草稿时是 no-op', () => {
    const { core } = newCore()
    core.discardDraftUser()
    expect(msgs(core)).toHaveLength(0)
    core.draftUser('你好')
    core.discardDraftUser()
    expect(msgs(core)).toHaveLength(0)
    expect(core.store.getState().draftUserId).toBeNull()
    core.dispose()
  })

  test('commit 没有草稿返回 null；send 不带 bubbleId 仍追加（文字路径逐字不变）', () => {
    const { core } = newCore()
    expect(core.commitDraftUser()).toBeNull()
    core.send('天气')
    core.send('天气', undefined, { bubbleId: 'no-such-id' }) // 对不上的 id 当没给
    expect(msgs(core).filter((m) => m.role === 'user')).toHaveLength(2)
    core.dispose()
  })

  test('打断：已显示的回答定格、不改成错误；interruptedIds 记下它（方案 §5.2 规则 4）', () => {
    const { transport, core } = newCore()
    core.send('讲个故事')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'speech_delta', request_id: rid, delta: '从前有座山' })
    core.cancelCurrentTurn()
    const a = assistants(core)[0]
    expect(a.text).toBe('从前有座山')
    expect(a.streaming).toBe(false)
    expect(a.error).toBeFalsy()
    expect(core.store.getState().interruptedIds).toEqual([a.id])
    core.dispose()
  })

  test('打断时一个字都没出 → 文本「已打断」，同样不是 error', () => {
    const { core } = newCore()
    core.send('讲个故事')
    core.cancelCurrentTurn()
    const a = assistants(core)[0]
    expect(a.text).toBe('已打断')
    expect(a.error).toBeFalsy()
    expect(a.pending).toBe(false)
    core.dispose()
  })

  test('D9：断线期间取消一轮，「N 条消息排队中」跟着减（承诺型文案不许报错数）', () => {
    const { transport, core } = newCore()
    transport.send = (frame: object) => {
      transport.sent.push(frame)
      return false // 断线：帧入 ws.mjs 队列
    }
    core.setStatus('closed')
    core.send('a')
    core.send('b')
    expect(core.store.getState().queued).toBe(2)
    core.cancelCurrentTurn() // 结算 FIFO 头（a）
    expect(core.store.getState().queued).toBe(1)
    core.setStatus('open')
    expect(core.store.getState().queued).toBe(0)
    core.dispose()
  })
})
```
⚠ 既有用例「⑥ 本地 cancel 后网关 cancelled 幂等」若断言了被打断气泡的 `error: true`，那条断言改成 `interruptedIds` 包含——**这是判据变更（打断不是错误），不是测试让步**，写进 §6.2。

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

`mobile/src/core/session/store.ts`：

① `SendOpts` 与 `SessionState`：
```ts
export interface SendOpts {
  source?: TurnSource
  /** 这条用户气泡已经在记录里（草稿转正 / 视觉先落气泡 / S2S 逃逸）：把文本对齐成定稿，不追加第二条 */
  bubbleId?: string
}
```
```ts
  /** 转写草稿气泡（方案 §5.2.1 draft_user）：ASR partial 按稳定 segment 写进记录；定稿转正、取消删除 */
  draftUserId: string | null
  /** 被打断的助手气泡：文字定格、标「已打断」，**不是错误**（方案 §5.2 规则 4） */
  interruptedIds: string[]
```
（构造里初始化 `draftUserId: null, interruptedIds: []`。）

② 类字段加 `private readonly queuedIds = new Set<string>()`；`send` 开头替换：
```ts
  send(text: string, metaExtra?: Record<string, string>, opts: SendOpts = {}): void {
    const reuse = opts.bubbleId && this.store.getState().messages.some((m) => m.id === opts.bubbleId) ? opts.bubbleId : null
    if (reuse) this.setText(reuse, text)
    else this.appendMessage({ id: uid(), role: 'user', text })
```
③ 新增四个方法（放在 `cancelCurrentTurn` 之后）：
```ts
  // ── 转写草稿（方案 §5.2.1）：语音层不持有转写状态，草稿就是记录里的一条用户气泡 ──

  /** 有草稿就更新，没有就建。partial 按稳定 segment 来，每次都是全文不是增量 */
  draftUser(text: string): void {
    const s = this.store.getState()
    if (s.draftUserId && s.messages.some((m) => m.id === s.draftUserId)) {
      this.setText(s.draftUserId, text)
      return
    }
    const id = uid()
    this.store.setState((st) => ({ messages: [...st.messages, { id, role: 'user', text }], draftUserId: id }))
  }

  /** 取消 / 误唤醒回收 / 空定稿：草稿删除，不留气泡 */
  discardDraftUser(): void {
    const id = this.store.getState().draftUserId
    if (!id) return
    this.store.setState((s) => ({ messages: s.messages.filter((m) => m.id !== id), draftUserId: null }))
  }

  /** 定稿：草稿转正，返回它的 id 供 send({ bubbleId }) 复用；没有草稿返回 null */
  commitDraftUser(): string | null {
    const id = this.store.getState().draftUserId
    if (!id) return null
    this.store.setState({ draftUserId: null })
    return id
  }

  private setText(id: string, text: string): void {
    this.store.setState((s) => ({ messages: s.messages.map((m) => (m.id === id ? { ...m, text } : m)) }))
  }
```
④ `markInterrupted` 整段替换：
```ts
  /** 打断留痕（方案 §5.2 规则 4）：已显示的部分定格；一个字没出就写「已打断」。**不是错误**——
   *  打断是用户的动作，A-6 也没把它归错误态；红色留给 error 帧与超时 */
  private markInterrupted(id: string): void {
    this.store.setState((s) => ({
      messages: s.messages.map((msg) =>
        msg.id === id && (msg.pending || msg.streaming || msg.processActive)
          ? { ...msg, pending: false, streaming: false, processActive: false, text: msg.text || '已打断' }
          : msg,
      ),
      interruptedIds: s.interruptedIds.includes(id) ? s.interruptedIds : [...s.interruptedIds, id],
    }))
  }
```
⑤ D9——`dispatch` 里 `if (!sentNow) this.store.setState((s) => ({ queued: s.queued + 1 }))` 改为：
```ts
    const pendingId = uid()
    if (!sentNow) this.queuedIds.add(pendingId) // 按轮计数：取消 / 终态都能把它摘掉（评审 D9）
    this.syncQueued()
```
（`const pendingId = uid()` 原来在 `sentNow` 之后一行，上移即可；）`setStatus` 的 `open` 分支 `this.store.setState({ connStatus: status, queued: 0 })` 之前加 `this.queuedIds.clear()`；`clearWatchdog` 末尾与看门狗超时回调里各加 `this.queuedIds.delete(id); this.syncQueued()`（`clearWatchdog` 里用 `bubbleId`）；新增：
```ts
  private syncQueued(): void {
    const n = this.queuedIds.size
    if (this.store.getState().queued !== n) this.store.setState({ queued: n })
  }
```

`mobile/src/features/chat/usePtt.ts`：`usePtt(opts)` 的入参类型加两个可选回调，并在四处调用：
```ts
export function usePtt(opts: {
  audioUrl: string
  sessionId: string
  onFinal(text: string): void
  /** 稳定 partial（全文）→ 记录里的草稿气泡（方案 §5.2.1） */
  onPartial?(text: string): void
  /** 太短 / 出错 / 空定稿 / 取消 → 草稿不留气泡 */
  onDiscard?(): void
}): PttHandle {
```
- `finishSession` 的 `tooShort` 分支 `await session.cancel()` 之前加 `opts.onDiscard?.()`；
- `onPartial: (t) => { setPartial(t); opts.onPartial?.(t) }`；
- `onFinal` 的 `else`（空定稿）分支加 `opts.onDiscard?.()`；
- `onError` 分支加 `opts.onDiscard?.()`；
- `.catch` 分支（启动失败）加 `opts.onDiscard?.()`。

`mobile/src/features/chat/ChatScreen.tsx`：
```ts
  const ptt = usePtt({
    audioUrl: cfg.audioUrl,
    sessionId: wired.session.sessionId,
    onPartial: (t) => core.draftUser(t),
    onDiscard: () => core.discardDraftUser(),
    onFinal: (text) => onSend(text, undefined, { source: 'ptt', bubbleId: core.commitDraftUser() ?? undefined }),
  })
  const hf = useHandsFree({
    …（其余入参原样）
    onPartial: (t) => core.draftUser(t),
    onSend: (text) => onSend(text, undefined, { source: 'handsfree', bubbleId: core.commitDraftUser() ?? undefined }),
    …（其余原样）
  })
  // 免唤醒离开 LISTENING 而没有定稿（退出词 / 语气词 / 误唤醒回收 / 回声）：草稿不留气泡。
  // 定稿路径里 commit 先于 FSM 换态（onSend 同步发生在 _finalizeSend 内），到这里已是 no-op
  useEffect(() => {
    if (hf.fsm !== 'LISTENING') core.discardDraftUser()
  }, [hf.fsm, core])
```
`useStore(core.store)` 解构加 `draftUserId, interruptedIds`；FlashList `extraData` 加这两个；`MessageBubble` 传 `draft={item.id === draftUserId}` `interrupted={interruptedIds.includes(item.id)}`；`VoiceSheet` 传 `draftUserId={draftUserId} interruptedIds={interruptedIds}`。

`mobile/src/features/chat/MessageBubble.tsx`：`BubbleProps` 加
```ts
  /** 转写草稿（方案 §5.2.1）：虚线边 + 光标，定稿后由同一条气泡接管（HMI PartialUserBubble 同款形态） */
  draft?: boolean
  /** 被打断（方案 §5.2 规则 4）：文字定格 + 灰字「已打断」，不是错误样式 */
  interrupted?: boolean
```
用户气泡分支的容器 style 里边框改为 `borderWidth: 1, borderStyle: draft ? 'dashed' : 'solid', borderColor: draft ? \`${p.accent}4D\` : \`${p.accent}38\``，文本后 `{draft ? <StreamCursor h={p.font(15)} /> : null}`；助手气泡在 `{msg.text ? (…) : null}` 之后加：
```tsx
        {interrupted ? <Text style={{ color: p.fg3, fontSize: p.font(11) }}>已打断</Text> : null}
```

`mobile/src/features/chat/VoiceSheet.tsx`：props 加 `draftUserId: string | null` 与 `interruptedIds: readonly string[]`；转写光标条件改为 `user.id === props.draftUserId`；回答文本之后加 `{assistant && props.interruptedIds.includes(assistant.id) ? <Text style={{ color: p.fg3, fontSize: scale(TYPE.caption, 'text', fontScale) }}>已打断</Text> : null}`。

- [ ] **步骤 4：跑绿 + `tsc`**；全量 `npm test`（**看门狗三条 `sessionStore.test.ts:529/545/560` 必须仍绿**——D9 改了 `clearWatchdog`）。

- [ ] **反向验证**：
  1. `send` 里去掉 `reuse` 分支（总是追加）⇒ 只红「复用不追加第二条」。
  2. `markInterrupted` 恢复 `error: true` ⇒ 只红两条「打断」用例。
  3. `cancelCurrentTurn` 路径不 `queuedIds.delete` ⇒ 只红 D9。
  4. 真机（第 2 批收口，需泓舟真人一句）：说「附近有什么好吃的」——层里转写逐字变、记录里同一条气泡（虚线）逐字同变；**录音中途切后台再回来 / 折叠展开**，草稿仍在且与层里显示逐字相同（`b2-04-draft-{fg,bg,fold}.png`）；说到一半上滑（T6 才有；本批用误唤醒回收：唤醒后不说话 5s）→ 草稿消失、记录里没有空气泡；播报中点「■ 打断」→ 回答定格 + 灰字「已打断」、气泡不变红。

- [ ] **提交**：
```bash
git commit -m "feat(mobile): UX v2 B2-4 转写增量沉淀（草稿气泡 draft→final）、打断留痕不改红、queued 按轮计数（D9）" -- mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts mobile/src/features/chat/usePtt.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/MessageBubble.tsx mobile/src/features/chat/VoiceSheet.tsx && git show --stat HEAD
```

### Task 5: S2S 轮沉淀 + 「端到端」角标 + 开录即告知 + 设置页首次显式同意

**Files:**
- 修改 `mobile/src/core/session/store.ts`（`s2sIds` + 四方法）、`mobile/test/sessionStore.test.ts`
- 修改 `mobile/src/features/chat/useHandsFree.ts`（`onS2sEscalated?` / `onS2sTurnEnd?` 交给调用方）、`mobile/src/features/chat/ChatScreen.tsx`（四回调接线 + `s2sNotice`）、`mobile/src/features/chat/MessageBubble.tsx`（`s2s` 角标 + 长按提示）、`mobile/src/features/chat/VoiceSheet.tsx`（首行告知条）
- 修改 `mobile/src/core/settings/store.ts`（`s2sConsentAt` + `needsS2sConsent`）、`mobile/test/settingsMeta.test.ts`；新建 `mobile/src/features/settings/S2sConsentSheet.tsx`；修改 `mobile/src/features/settings/SettingsScreen.tsx`

**为什么**：方案 §5.2 规则 2 末段 + §5.2.2——S2S 自答轮今天在对话里**零痕迹**（方案 §0 表 U2 的现状证据）；记录语义 `{ source:'s2s', transcriptKind:'model_inferred' }` 在本 App 里的载体是并列字段 `s2sIds`（只有 S2S 轮才是模型推断的转写，所以一个集合表达两件事）；角标「端到端」（Q7）；长按可看「转写由语音模型生成」。**开录即告知**：S2S 挡位下语音层升起的第一行是 G0 实色条——这是红线三条件③在**交互时刻**的落实；设置里把挡位切到端到端时弹**一次性显式同意**（评审 §6 ③：零依赖语音层，但 B2 不做就没有别的批会做）。副作用只走主链（CLAUDE.md §5）：本任务**不改任何一条红线**，只让它看得见——逃逸轮有 `request_id`、进 `requestRouting`、按普通轮渲染。**双气泡风险**（§0 第 5 条）：`onS2sUserUtterance` 与 `onS2sEscalated` 都会产生用户气泡，用 `takeS2sUserBubble()` 交给主链复用。

- [ ] **步骤 1：写失败测试**

`mobile/test/sessionStore.test.ts` 追加：

```ts
describe('UX v2 B2-5：S2S 自答轮沉淀（方案 §5.2.2）', () => {
  test('用户话 + 回答增量 + turn.end → 记录里两条、都在 s2sIds、不进 requestRouting（没有上行帧）', () => {
    const { transport, core } = newCore()
    core.draftUser('今天天气') // S2S 的 transcript partial 也走草稿
    core.s2sUserUtterance('今天天气怎么样')
    core.s2sAnswerDelta('深圳今天')
    core.s2sAnswerDelta('多云。')
    core.s2sTurnEnd('completed')
    const all = msgs(core)
    expect(all).toHaveLength(2)
    expect(all[0]).toMatchObject({ role: 'user', text: '今天天气怎么样' })
    expect(all[1]).toMatchObject({ role: 'assistant', text: '深圳今天多云。', streaming: false })
    expect(core.store.getState().s2sIds).toEqual([all[0].id, all[1].id])
    expect(core.store.getState().draftUserId).toBeNull()
    expect(transport.sent.filter((f: any) => typeof f.text === 'string')).toHaveLength(0)
    core.dispose()
  })

  test('逃逸：takeS2sUserBubble 交给主链 send({bubbleId}) 复用——只有一条用户气泡、不再在 s2sIds、有上行帧', () => {
    const { transport, core } = newCore()
    core.s2sUserUtterance('打开后备箱')
    const id = core.takeS2sUserBubble() as string
    core.s2sTurnEnd('escalated')
    core.send('打开后备箱', undefined, { source: 's2s', bubbleId: id })
    expect(msgs(core).filter((m) => m.role === 'user')).toHaveLength(1)
    expect(core.store.getState().s2sIds).toEqual([])
    expect(transport.lastUserFrame().text).toBe('打开后备箱')
    expect(core.store.getState().turnMeta[assistants(core)[0].id].source).toBe('s2s')
    core.dispose()
  })

  test('取消且一个字没出 → 那条助手气泡删除；出了字 → 定格并进 interruptedIds', () => {
    const { core } = newCore()
    core.s2sUserUtterance('讲个笑话')
    core.s2sAnswerDelta('')
    core.s2sTurnEnd('cancelled')
    expect(assistants(core)).toHaveLength(0)
    core.s2sUserUtterance('再讲一个')
    core.s2sAnswerDelta('从前')
    core.s2sTurnEnd('cancelled')
    expect(assistants(core)[0].text).toBe('从前')
    expect(core.store.getState().interruptedIds).toEqual([assistants(core)[0].id])
    core.dispose()
  })

  test('takeS2sUserBubble 没有待归属的用户话时返回 null（逃逸帧先于 transcript 到达的兜底）', () => {
    const { core } = newCore()
    expect(core.takeS2sUserBubble()).toBeNull()
    core.dispose()
  })
})
```

`mobile/test/settingsMeta.test.ts` 追加：

```ts
describe('UX v2 B2-5：端到端首次显式同意', () => {
  test('缺省未同意（s2sConsentAt=0）；存量没有这个键 → 0；同意过 → 不再需要', () => {
    expect(DEFAULT_APP_SETTINGS.s2sConsentAt).toBe(0)
    expect(needsS2sConsent(DEFAULT_APP_SETTINGS)).toBe(true)
    expect(needsS2sConsent(mergeStoredSettings(JSON.stringify({ voicePipeline: 's2s' })))).toBe(true)
    expect(needsS2sConsent({ ...DEFAULT_APP_SETTINGS, s2sConsentAt: 1_700_000_000_000 })).toBe(false)
  })
  test('s2sConsentAt 不上行（buildMeta 键集不变）', () => {
    expect(Object.keys(buildMeta(DEFAULT_APP_SETTINGS)).sort()).toEqual([...HMI_META_KEYS].sort())
  })
})
```
（import 加 `needsS2sConsent`。）

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

`mobile/src/core/session/store.ts`：`SessionState` 加
```ts
  /** 端到端（S2S）自答轮的气泡（用户话 + 回答）：转写由语音模型生成、不是逐字 ASR（方案 §5.2.2
   *  的 `source:'s2s' + transcriptKind:'model_inferred'`——只有 S2S 轮是模型推断的，一个集合够用）。
   *  逃逸轮回到主链后从这里摘掉——它按普通轮渲染 */
  s2sIds: string[]
```
（初始化 `s2sIds: []`），类字段加 `private s2sUserId: string | null = null` / `private s2sAssistantId: string | null = null`，新增方法（放在草稿三方法之后）：

```ts
  // ── S2S 自答轮（方案 §5.2.2）：只写记录，不进 requestRouting——它没有 request_id ──

  /** 已过 FSM 本地治理的用户话：有草稿就转正为它，没有就建；进 s2sIds，等归属（自答 / 逃逸） */
  s2sUserUtterance(text: string): void {
    const draft = this.commitDraftUser()
    const id = draft ?? uid()
    if (draft) this.setText(id, text)
    else this.appendMessage({ id, role: 'user', text })
    this.s2sUserId = id
    this.store.setState((s) => ({ s2sIds: s.s2sIds.includes(id) ? s.s2sIds : [...s.s2sIds, id] }))
  }

  /** 回答增量：按「无在飞轮的续流 adopt 新气泡」语义单独开一条（§5.2 规则 2） */
  s2sAnswerDelta(delta: string): void {
    if (!delta) return
    const cur = this.s2sAssistantId
    if (cur && this.store.getState().messages.some((m) => m.id === cur)) {
      this.store.setState((s) => ({
        messages: s.messages.map((m) => (m.id === cur ? { ...m, text: m.text + delta, streaming: true } : m)),
      }))
      return
    }
    const id = uid()
    this.s2sAssistantId = id
    this.store.setState((s) => ({
      messages: [...s.messages, { id, role: 'assistant', text: delta, streaming: true }],
      s2sIds: [...s.s2sIds, id],
    }))
  }

  /** turn.end：收尾。cancelled 且没出字 → 删；出了字 → 定格 + 打断留痕；escalated 的用户话留给 takeS2sUserBubble */
  s2sTurnEnd(reason: string): void {
    const id = this.s2sAssistantId
    this.s2sAssistantId = null
    if (reason !== 'escalated') this.s2sUserId = null
    if (!id) return
    const text = this.store.getState().messages.find((m) => m.id === id)?.text ?? ''
    if (!text) {
      this.store.setState((s) => ({ messages: s.messages.filter((m) => m.id !== id), s2sIds: s.s2sIds.filter((x) => x !== id) }))
      return
    }
    this.store.setState((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, streaming: false } : m)),
      interruptedIds: reason === 'cancelled' && !s.interruptedIds.includes(id) ? [...s.interruptedIds, id] : s.interruptedIds,
    }))
  }

  /** 逃逸（红线：S2S 会话内无执行通道，原话交回主链）：这条用户气泡不再是端到端轮——
   *  交给 send({ bubbleId }) 复用，**不许出现第二条**。没有待归属的用户话返回 null */
  takeS2sUserBubble(): string | null {
    const id = this.s2sUserId
    this.s2sUserId = null
    if (!id) return null
    this.store.setState((s) => ({ s2sIds: s.s2sIds.filter((x) => x !== id) }))
    return id
  }
```

`mobile/src/features/chat/useHandsFree.ts`：`UseHandsFreeOpts` 加
```ts
  /** turn.end（reason: completed / cancelled / escalated / error…）；不传只清 partial */
  onS2sTurnEnd?(r: { turnId: string; reason: string; detail: string }): void
  /** 逃逸：不传就退回 onSend(utterance)（B1 行为） */
  onS2sEscalated?(utterance: string): void
```
deps 里两行改为：
```ts
      onS2sEscalated: (utterance) => (cbRef.current.onS2sEscalated ?? cbRef.current.onSend)(utterance),
      onS2sTurnEnd: (r) => {
        setPartial('')
        cbRef.current.onS2sTurnEnd?.(r)
      },
```

`mobile/src/features/chat/ChatScreen.tsx`：`useHandsFree` 调用加四个回调；`useStore` 解构加 `s2sIds`；`extraData` 加它；`MessageBubble` 传 `s2s={s2sIds.includes(item.id)}`；`VoiceSheet` 传 `s2sNotice`：
```ts
    onS2sUserUtterance: (t) => core.s2sUserUtterance(t),
    onS2sAnswerDelta: (t) => core.s2sAnswerDelta(t),
    onS2sTurnEnd: (r) => core.s2sTurnEnd(r.reason),
    // 逃逸走的是同一个 onSend（视觉抓帧 / 前置路由 / 位置闸一条不少）；用户气泡复用 S2S 那条
    onS2sEscalated: (utt) => onSend(utt, undefined, { source: 's2s', bubbleId: core.takeS2sUserBubble() ?? undefined }),
    …（其余入参原样）
```
```ts
  // 开录即告知（红线三条件③在交互时刻的落实）：正在上传原始音频、或这一轮就是端到端发起的
  const s2sNotice = snapshot.privacy.mic === 'cloudAudio' || snapshot.turnSource === 's2s'
```

`mobile/src/features/chat/VoiceSheet.tsx`：props 加 `s2sNotice: boolean`，把手之后、ScrollView 之前插入：
```tsx
            {props.s2sNotice ? (
              <View
                testID="s2s-notice"
                accessibilityLiveRegion="polite"
                style={{ backgroundColor: p.dark ? '#3B2A0A' : '#FFF4DB', paddingVertical: 6, paddingHorizontal: 12, marginTop: 8 }}
              >
                <Text style={{ color: p.amber, fontSize: scale(TYPE.caption, 'text', fontScale), textAlign: 'center' }}>
                  端到端语音 · 原始音频将在本轮上传
                </Text>
              </View>
            ) : null}
```
（G0 实色：不透明底，§5.11。）

`mobile/src/features/chat/MessageBubble.tsx`：`BubbleProps` 加 `/** 端到端自答轮：角标「端到端」，长按看「转写由语音模型生成」（方案 §5.2.2，Q7） */ s2s?: boolean`；用户气泡分支改成 `Pressable`（`onLongPress` 时 `s2s` 才有动作）：
```tsx
  const [hint, setHint] = useState(false)
  if (msg.role === 'user') {
    return (
      <View style={{ alignItems: 'flex-end', marginVertical: 5 }}>
        <Pressable
          onLongPress={s2s ? () => { setHint(true); setTimeout(() => setHint(false), 2500) } : undefined}
          style={{ … 原样式 … }}
        >
          {s2s ? <Text style={{ color: p.teal, fontSize: p.font(10), marginBottom: 2 }}>端到端</Text> : null}
          <Text style={{ color: p.fg1, fontSize: p.font(15), lineHeight: p.font(23) }}>{msg.text}{draft ? <StreamCursor h={p.font(15)} /> : null}</Text>
          {hint ? <Text style={{ color: p.fg3, fontSize: p.font(10), marginTop: 4 }}>转写由语音模型生成，可能与原话有出入</Text> : null}
        </Pressable>
      </View>
    )
  }
```
助手气泡：`proactive` 标题行之前加 `{s2s ? <Text style={{ color: p.teal, fontSize: p.font(11), fontWeight: '600' }}>端到端</Text> : null}`。

`mobile/src/core/settings/store.ts`：`AppSettings` 加
```ts
  /** 端到端挡位的一次性显式同意时刻（ms）；0=从未同意。红线三条件②「用户显式选择」的
   *  持久化证据——只有开关不算显式（方案 §5.2.2） */
  s2sConsentAt: number
```
默认 `s2sConsentAt: 0`；导出
```ts
/** 切到端到端前要不要弹一次性同意（判据只此一处：设置页与任何未来入口都问它） */
export function needsS2sConsent(s: AppSettings): boolean {
  return s.s2sConsentAt <= 0
}
```

`mobile/src/features/settings/S2sConsentSheet.tsx`：

```tsx
// mobile/src/features/settings/S2sConsentSheet.tsx
// 端到端挡位的一次性显式同意（方案 §5.2.2「设置里把挡位从三段式切到端到端时弹一次性显式同意（不是只有开关）」）。
// G0 实色（§5.11：隐私说明不许半透明）。文案逐条对应 CLAUDE.md §5「唯一的受控例外」三条件。
import { Modal, Pressable, ScrollView, Text, View } from 'react-native'

import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export const S2S_CONSENT_TEXT = [
  '端到端语音会把你说话的**原始音频**上传到服务器上的语音大模型，而三段式只上传识别后的文字。',
  '只在你唤醒之后的对话窗内采集；没唤醒时一帧都不传。',
  '它不能直接执行任何动作：涉及车控、支付、导航、账户或记忆的话会交回文本主链，经权限与二次确认。',
  '随时可以在这里切回三段式；切回后立即停止上传。',
]

export function S2sConsentSheet({
  p,
  fontScale,
  visible,
  onAccept,
  onDecline,
}: {
  p: Palette
  fontScale: FontScalePref
  visible: boolean
  onAccept(): void
  onDecline(): void
}) {
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  const h = scale(TARGET.parked, 'target', fontScale)
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onDecline}>
      <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={onDecline} accessibilityLabel="仍用三段式" />
      <View testID="s2s-consent" style={{ backgroundColor: solid, borderTopLeftRadius: RADIUS['2xl'], borderTopRightRadius: RADIUS['2xl'], padding: 16, gap: 12 }}>
        <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fontScale), fontWeight: '600' }}>切到端到端语音之前</Text>
        <ScrollView style={{ maxHeight: 260 }}>
          {S2S_CONSENT_TEXT.map((line, i) => (
            <Text key={i} style={{ color: p.fg2, fontSize: scale(TYPE.body - 1, 'text', fontScale), lineHeight: scale(22, 'line', fontScale), paddingVertical: 3 }}>
              {i + 1}. {line.replace(/\*\*/g, '')}
            </Text>
          ))}
        </ScrollView>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <Pressable testID="s2s-consent-decline" accessibilityRole="button" onPress={onDecline} style={{ flex: 1, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: p.fill2, backgroundColor: p.fill, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: p.fg2, fontSize: scale(TYPE.body, 'text', fontScale) }}>仍用三段式</Text>
          </Pressable>
          <Pressable testID="s2s-consent-accept" accessibilityRole="button" onPress={onAccept} style={{ flex: 2, minHeight: h, borderRadius: RADIUS.md, borderWidth: 1, borderColor: 'rgba(245,158,11,0.38)', backgroundColor: p.amberSoft, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body, 'text', fontScale), fontWeight: '600' }}>我知道了，切到端到端</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  )
}
```

`mobile/src/features/settings/SettingsScreen.tsx`：import `needsS2sConsent` 与 `S2sConsentSheet`；`const [consentOpen, setConsentOpen] = useState(false)`；「语音链路」`ChoiceRow` 的 `onPick` 改为：
```tsx
              onPick={(voicePipeline) => {
                // 首次切端到端弹一次性显式同意（方案 §5.2.2）；同意过的直接切；切回三段式永远不问
                if (voicePipeline === 's2s' && needsS2sConsent(settings)) setConsentOpen(true)
                else set({ voicePipeline })
              }}
```
分区末尾加 `{settings.s2sConsentAt > 0 ? <Text style={{ color: p.fg3, fontSize: p.font(11) }}>已于 {new Date(settings.s2sConsentAt).toLocaleString('zh-CN', { hour12: false })} 同意端到端上传原始音频</Text> : null}`；`ScrollView` 之后（同级）渲染：
```tsx
      <S2sConsentSheet
        p={p}
        fontScale={settings.fontScale}
        visible={consentOpen}
        onAccept={() => {
          set({ voicePipeline: 's2s', s2sConsentAt: Date.now() })
          setConsentOpen(false)
        }}
        onDecline={() => setConsentOpen(false)}
      />
```
（`ScrollView` 外要包一层 `View style={{flex:1}}`，Modal 与 ScrollView 并列。）

- [ ] **步骤 4：跑绿 + `tsc`**。

- [ ] **反向验证**：
  1. ChatScreen 的 `onS2sEscalated` 不调 `takeS2sUserBubble`（`bubbleId: undefined`）⇒ 单测层在 store 用例「只有一条用户气泡」红（store 用例直接调 `takeS2sUserBubble`，所以再加一条**接线**证据：真机逃逸轮记录里恰好一条用户气泡）。
  2. `s2sAnswerDelta` 总是新建气泡 ⇒ 只红第一条（`toHaveLength(2)`）。
  3. `needsS2sConsent` 改成 `s.s2sConsentAt >= 0` ⇒ 只红同意用例的第三行。
  4. 真机（第 2 批收口，**需泓舟真人 + 云端 S2S 已开通**——AGENTS.md：`/api/s2s/info available:true`、端到端一轮**从未验过**，这一批就是验它的时候）：设置切端到端 → 同意页出现一次（截图 `b2-05-consent.png`）→ 「仍用三段式」→ 挡位不变；再切 → 同意 → 挡位变、页脚出现同意时间；再切回三段式再切端到端 → **不再弹**。唤醒说一句 → 层首行「端到端语音 · 原始音频将在本轮上传」（`b2-05-notice.png`）→ 记录里出现带「端到端」角标的两条（`b2-05-turn.png`）——**§11.4「记录完整性」在此取数：S2S 轮记录条数 / 实际轮数 = 100%**（此前是 0）；说「打开后备箱」→ 逃逸 → 记录里恰好一条用户气泡、无角标、Dock 出现确认（主链闸生效，`b2-05-escalate.png`）。

- [ ] **提交**：
```bash
git add -- mobile/src/features/settings/S2sConsentSheet.tsx && git commit -m "feat(mobile): UX v2 B2-5 S2S 自答轮沉淀 + 「端到端」角标 + 开录即告知条 + 首次显式同意（逃逸轮复用用户气泡，红线只让它可见）" -- mobile/src/features/settings/S2sConsentSheet.tsx mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts mobile/src/features/chat/useHandsFree.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/MessageBubble.tsx mobile/src/features/chat/VoiceSheet.tsx mobile/src/core/settings/store.ts mobile/test/settingsMeta.test.ts mobile/src/features/settings/SettingsScreen.tsx && git show --stat HEAD
```
### Task 6: 轻点光球即说 + Composer 手势契约（含 PTT-lease 前置修、D7）

**Files:**
- 新建 `mobile/src/core/voice/tapTalk.ts`、`mobile/test/tapTalk.test.ts`、`mobile/test/pttLease.test.ts`
- 修改 `mobile/src/core/voice/asr.ts`（`AsrConfig.vadSilenceMs`）、`mobile/test/voiceAsr.test.ts`（追加 1 条）
- 修改 `mobile/src/features/chat/usePtt.ts`（recorder 换 `micLease()`；`tap()` / `cancel()` / `mode` / `cancelledAt`）
- 修改 `mobile/src/core/voice/handsFree.ts`（`wakeManually()` / `endUtterance()` / `recycle()`）、`mobile/test/handsFree.test.ts`（追加 3 条）、`mobile/src/features/chat/useHandsFree.ts`（透出三个方法）
- 修改 `mobile/src/features/chat/Composer.tsx`（手势契约）、`mobile/src/features/chat/ChatScreen.tsx`（`startListening` / `interruptAndListen` / `onOrbTap` / `stopMic`）
- 修改 `mobile/src/core/presence/presence.ts`（`notice` 输入 + `NOTICE_SHOW_MS`）、`mobile/src/features/chat/usePresence.ts`（取消提示）、`mobile/test/presence.test.ts`（追加 1 条）

**为什么**：方案 §5.1.1 手势契约 + Q2「轻点始终开始录音，与免唤醒开关无关」。三条写计划时核出来的事实决定了实现形态（§0 第 5 条）：① `vad_silence_ms` 只有 qwen3 消费、缺省引擎 fun-asr 收不了尾 ⇒ 收尾主判据是**端侧 VAD**（`core/voice/tapTalk.ts` 三层收尾：VAD 端点 / 服务端尾 / 15s 上限）；② **PTT 在免唤醒开着时今天是坏的**（recorder 单例）⇒ `AsrSession` 领 `micLease()`，先于手势落、有 jest 钉住；③ 免唤醒开着时「轻点」= FSM 的公开入口 `wake()`（VAD 收尾是它本来就有的），不另起一条会话——**哪个引擎持有麦，就由谁开始听**，这是 ChatScreen 知道的事实，不是判据。D7：`reenableBargeIn` 翻持久化开关 50ms 的假窗口 ⇒ `HandsFreeController.recycle()`（FSM 拆机再装机，引擎与麦都不动，`_bargeInDisabled` 随 `_gotoIdle` 复位）。取消的隐私文案（§5.1.1）：「已取消，这段话不会发给小舟」——**不是**「未上传」。

- [ ] **步骤 1：写失败测试**

`mobile/test/pttLease.test.ts`：

```ts
// mobile/test/pttLease.test.ts
// §0 第 5 条：PTT 在免唤醒开着时今天是坏的——AsrSession 用 recorder() 单例，已录时 start 静默 return
// （一帧收不到，recorder.ts:50）、stop 会把免唤醒的真麦停掉（:89）。修法：PTT 的 AsrSession 也领一路 micLease。
// 这里验的是**组合**：一路 lease 常开（免唤醒）时，第二路（PTT）能收到帧、停下时真麦不关。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { AsrSession } from '@/core/voice/asr'
import { micBusStats, micLease, resetMicBusForTest } from '@/core/voice/micBus'
import { setRecorderForTest, type FrameSink, type Recorder } from '@/core/voice/recorder'

class FakeRecorder implements Recorder {
  starts = 0
  stops = 0
  sink: FrameSink | null = null
  deviceRate = 16000
  get recording(): boolean {
    return !!this.sink
  }
  async start(onFrame: FrameSink): Promise<void> {
    this.starts += 1
    this.sink = onFrame
  }
  async stop(): Promise<void> {
    this.stops += 1
    this.sink = null
  }
  emit(n = 1600): void {
    this.sink?.(new Int16Array(n))
  }
}

class FakeWs {
  static last: FakeWs | null = null
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(readonly url: string) {
    FakeWs.last = this
  }
  send(d: unknown): void {
    this.sent.push(d)
  }
  close(): void {
    this.readyState = 3
  }
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }
  get binarySent(): unknown[] {
    return this.sent.filter((s) => typeof s !== 'string')
  }
}

const origWs = (global as unknown as { WebSocket: unknown }).WebSocket
let rec: FakeRecorder
beforeEach(() => {
  resetMicBusForTest()
  rec = new FakeRecorder()
  setRecorderForTest(rec)
  ;(global as unknown as { WebSocket: unknown }).WebSocket = FakeWs
})
afterEach(() => {
  setRecorderForTest(null)
  ;(global as unknown as { WebSocket: unknown }).WebSocket = origWs
})

test('免唤醒常开一路时，PTT 的 AsrSession（micLease）能收到帧并上行；PTT 停下真麦不关', async () => {
  const handsFree = micLease()
  await handsFree.start(() => {})
  expect(rec.starts).toBe(1)
  const ptt = new AsrSession(
    { audioUrl: 'https://x', language: 'zh', provider: 'dashscope', model: 'fun-asr-realtime' },
    { onFinal() {}, onError() {} },
    micLease(),
  )
  await ptt.start()
  FakeWs.last!.open()
  expect(micBusStats().active).toBe(2)
  rec.emit(1600)
  expect(FakeWs.last!.binarySent).toHaveLength(1) // PTT 这一路拿到了帧
  await ptt.cancel()
  expect(rec.stops).toBe(0) // 真麦仍开着——免唤醒那一路还在
  expect(micBusStats().active).toBe(1)
  await handsFree.stop()
  expect(rec.stops).toBe(1)
})

test('usePtt 的 AsrSession 必须领 micLease，不再用 recorder() 单例（源码级断言）', () => {
  const src = readFileSync(resolve(__dirname, '../src/features/chat/usePtt.ts'), 'utf8')
  expect(src).toContain('micLease()')
  expect(src).not.toMatch(/\brecorder\(\)/)
})
```

`mobile/test/tapTalk.test.ts`：

```ts
// mobile/test/tapTalk.test.ts
// 轻点即说的三层收尾（方案 §5.1.1 Q2）：端侧 VAD 端点 / 硬上限 / 用户再点一下——**没有一层是「不收尾」**。
// 假端点 + 假 recorder + 假 WebSocket，不碰真机也不碰网（同 voiceAsr.test.ts 的做法）。
import { TAP_MAX_MS, TAP_SILENCE_MS, TapTalkSession, type TapEndpoint } from '@/core/voice/tapTalk'
import type { FrameSink, Recorder } from '@/core/voice/recorder'

class FakeRecorder implements Recorder {
  sink: FrameSink | null = null
  recording = false
  deviceRate = 16000
  stops = 0
  async start(onFrame: FrameSink): Promise<void> {
    this.sink = onFrame
    this.recording = true
  }
  async stop(): Promise<void> {
    this.recording = false
    this.stops += 1
  }
}

class FakeEndpoint implements TapEndpoint {
  onEnd: (() => void) | null = null
  started = 0
  stopped = 0
  async start(onSpeechEnd: () => void): Promise<void> {
    this.onEnd = onSpeechEnd
    this.started += 1
  }
  async stop(): Promise<void> {
    this.stopped += 1
  }
}

class FakeWs {
  static last: FakeWs | null = null
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(readonly url: string) {
    FakeWs.last = this
  }
  send(d: unknown): void {
    this.sent.push(d)
  }
  close(): void {
    this.readyState = 3
  }
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }
  get jsonSent(): Array<Record<string, unknown>> {
    return this.sent.filter((s): s is string => typeof s === 'string').map((s) => JSON.parse(s) as Record<string, unknown>)
  }
}

const origWs = (global as unknown as { WebSocket: unknown }).WebSocket
beforeEach(() => {
  jest.useFakeTimers()
  ;(global as unknown as { WebSocket: unknown }).WebSocket = FakeWs
})
afterEach(() => {
  jest.useRealTimers()
  ;(global as unknown as { WebSocket: unknown }).WebSocket = origWs
})

const CFG = { audioUrl: 'https://x', language: 'zh', provider: 'dashscope', model: 'fun-asr-realtime' }
const CB = { onFinal() {}, onError() {} }
const flush = async (n = 6) => {
  for (let i = 0; i < n; i += 1) await Promise.resolve()
}

test('start 帧带 vad_silence_ms（qwen3 用户的服务端尾；hold 模式不带——见 voiceAsr.test）', async () => {
  const rec = new FakeRecorder()
  const s = new TapTalkSession(CFG, CB, { endpoint: null, rec })
  await s.start()
  FakeWs.last!.open()
  expect(FakeWs.last!.jsonSent.find((m) => m.type === 'start')?.vad_silence_ms).toBe(TAP_SILENCE_MS)
})

test('端侧 VAD 端点 → 自动 stop（发 stop 帧、放开 recorder、端点自己也停）', async () => {
  const rec = new FakeRecorder()
  const ep = new FakeEndpoint()
  const s = new TapTalkSession(CFG, CB, { endpoint: ep, rec })
  await s.start()
  FakeWs.last!.open()
  expect(ep.started).toBe(1)
  ep.onEnd!()
  await flush()
  expect(FakeWs.last!.jsonSent.some((m) => m.type === 'stop')).toBe(true)
  expect(rec.stops).toBe(1)
  expect(ep.stopped).toBe(1)
})

test('端点缺席 → TAP_MAX_MS 硬上限收尾；上限前一毫秒还在录', async () => {
  const rec = new FakeRecorder()
  const s = new TapTalkSession(CFG, CB, { endpoint: null, rec })
  await s.start()
  FakeWs.last!.open()
  jest.advanceTimersByTime(TAP_MAX_MS - 1)
  await flush()
  expect(rec.stops).toBe(0)
  jest.advanceTimersByTime(1)
  await flush()
  expect(rec.stops).toBe(1)
})

test('用户再点一下 = 结束并提交；stop 幂等（端点随后再到不会二次 stop）', async () => {
  const rec = new FakeRecorder()
  const ep = new FakeEndpoint()
  const s = new TapTalkSession(CFG, CB, { endpoint: ep, rec })
  await s.start()
  FakeWs.last!.open()
  await s.stop()
  ep.onEnd!()
  await flush()
  expect(rec.stops).toBe(1)
  expect(FakeWs.last!.jsonSent.filter((m) => m.type === 'stop')).toHaveLength(1)
})

test('cancel：不定稿不回调；端点与 recorder 都放开', async () => {
  const rec = new FakeRecorder()
  const ep = new FakeEndpoint()
  const onFinal = jest.fn()
  const s = new TapTalkSession(CFG, { onFinal, onError() {} }, { endpoint: ep, rec })
  await s.start()
  FakeWs.last!.open()
  await s.cancel()
  FakeWs.last!.onmessage?.({ data: JSON.stringify({ type: 'final', text: '迟到的定稿' }) })
  expect(onFinal).not.toHaveBeenCalled()
  expect(rec.stops).toBe(1)
  expect(ep.stopped).toBe(1)
})
```

`mobile/test/voiceAsr.test.ts` 追加：

```ts
test('B2-6：hold 模式（不传 vadSilenceMs）start 帧不带 vad_silence_ms；传了才带（只有 qwen3 消费它）', async () => {
  const rec = new FakeRecorder()
  const s = new AsrSession(CFG, { onFinal() {}, onError() {} }, rec)
  await s.start()
  FakeWs.last!.open()
  const start = FakeWs.last!.jsonSent.find((m) => m.type === 'start')
  expect(start).toBeDefined()
  expect('vad_silence_ms' in start!).toBe(false)
  const s2 = new AsrSession({ ...CFG, vadSilenceMs: 800 }, { onFinal() {}, onError() {} }, new FakeRecorder())
  await s2.start()
  FakeWs.last!.open()
  expect(FakeWs.last!.jsonSent.find((m) => m.type === 'start')?.vad_silence_ms).toBe(800)
})
```

`mobile/test/handsFree.test.ts` 追加：

```ts
test('B2-6 wakeManually：ARMED 下等同 KWS 命中——开一条 ASR（轻点光球 = 手动唤醒，FSM 不改）', async () => {
  const { ctl } = makeCtl()
  await ctl.enable()
  ctl.wakeManually()
  expect(asrLog).toEqual(['start'])
  expect(kws.resets).toBe(1)
})

test('B2-6 endUtterance：LISTENING 下请定稿（= onEndpoint 的效果）；非 LISTENING 时 no-op', async () => {
  const { ctl } = makeCtl()
  await ctl.enable()
  ctl.endUtterance() // ARMED：什么都不做
  expect(asrLog).toEqual([])
  ctl.wakeManually()
  ctl.endUtterance()
  expect(asrLog).toEqual(['start', 'stop'])
})

test('B2-6 recycle（评审 D7）：FSM 拆机再装机、麦不停、会话级关闭的 barge-in 被复位并以空串通知', async () => {
  const bargeIn: string[] = []
  const states: string[] = []
  const { ctl } = makeCtl({
    onBargeInDisabled: (r: string) => bargeIn.push(r),
    onOrbState: (_o: unknown, f: string) => states.push(f),
  })
  await ctl.enable()
  ctl.vl._bargeInDisabled = true // FSM 自己的会话级标志：只有 _gotoIdle 会复位它
  const stopsBefore = recStarts.stops
  ctl.recycle()
  expect(states.slice(-2)).toEqual(['IDLE', 'ARMED'])
  expect(recStarts.stops).toBe(stopsBefore) // 麦没停（不是 disable/enable）
  expect(ctl.vl.bargeInDisabled).toBe(false)
  expect(bargeIn).toEqual([''])
})
```

`mobile/test/presence.test.ts` 在 `describe('capsule 文案')` 追加：

```ts
  test('notice（取消 / 回声）2s 短显：压过 followup 与 armed，让位给收音 / 播报 / 思考（B2 T6/T11）', () => {
    const n = { text: '已取消，这段话不会发给小舟', at: NOW - 500 }
    expect(derivePresence(base({ notice: n })).capsule).toEqual({ text: n.text, tone: 'neutral' })
    expect(derivePresence(base({ notice: { ...n, at: NOW - 2_000 } })).capsule).toBeUndefined()
    expect(derivePresence(base({ notice: n, hfEnabled: true, hfUsable: true, hfFsm: 'FOLLOWUP' })).capsule?.text).toBe(n.text)
    expect(derivePresence(base({ notice: n, hfEnabled: true, hfUsable: true, hfFsm: 'ARMED', hfFsmChangedAt: NOW - 100 })).capsule?.text).toBe(n.text)
    expect(derivePresence(base({ notice: n, ptt: 'recording' })).capsule?.text).toBe('在听…')
    expect(derivePresence(base({ notice: n, speaking: true })).capsule?.text).toBe('播报中 · 说话可打断')
  })
```

- [ ] **步骤 2：跑红**（`pttLease` 源码断言红——今天 `usePtt.ts` 用的正是 `recorder()`；其余「模块 / 方法不存在」红）。

- [ ] **步骤 3：实现**

`mobile/src/core/voice/asr.ts`：`AsrConfig` 加
```ts
  /** 服务端静音尾（ms）——**只给轻点即说**：PTT 由松手定稿、免唤醒由端侧 VAD 定稿，两者不传。
   *  只有 qwen3 realtime 消费它（llm-gateway/providers.py:762），fun-asr 忽略：传了不坏，别指望它 */
  vadSilenceMs?: number
```
start 帧里那行注释 `// vad_silence_ms 刻意不传：…` 替换为
```ts
          ...(this.cfg.vadSilenceMs ? { vad_silence_ms: this.cfg.vadSilenceMs } : {}),
```

`mobile/src/core/voice/tapTalk.ts`：

```ts
// mobile/src/core/voice/tapTalk.ts
// 轻点即说（方案 §5.1.1、Q2）：轻点光球开始录音，**说完自动收尾**——与免唤醒开关无关。
//
// 方案写的收尾是「ASR 网关的 vad_silence_ms 服务端静音尾」。写计划时核了消费方：
// `llm-gateway/providers.py:762`「仅 qwen3 realtime 的 server_vad 消费；fun-asr 走客户端 stop 端点」，
// 而 App 默认 `asrModel='fun-asr-realtime'`（settings/store.ts:81）⇒ 缺省引擎上服务端尾**收不了尾**。
// 所以主判据是**端侧 VAD**（VadEngine：M4 已在 APK 里、真机已验）：语音结束事件 → asr.stop()；
// vad_silence_ms 照传（qwen3 用户白得一层）；VAD 缺席（原生不在 / 载入失败）→ TAP_MAX_MS 硬上限。
// 三层都是「收尾」，谁先到谁收；**没有任何一层是「不收尾」**——轻点录音不能变成永远开着的麦。
//
// 麦：ASR 与 VAD 各领一路 micLease（一路采集多路消费，micBus 头注）。免唤醒开着时不走这里
// （那时轻点 = HandsFreeController.wakeManually，FSM 自带 VAD 收尾），所以 VAD 引擎不会有两份。
import { micLease } from './micBus'
import type { Recorder } from './recorder'
import { AsrSession, type AsrCallbacks, type AsrConfig } from './asr'
import { VadEngine, vadNativeAvailable } from './vad'

/** 端侧 VAD 静音尾（ms）。与 voiceLoop DEFAULTS.silenceTailMs 同值——那是共享判据，
 *  这里只是把同一个数交给 VadEngine 与 ASR 网关，不另立判据 */
export const TAP_SILENCE_MS = 800
/** 无端点时的硬上限（ms；HMI listenSeconds 同值） */
export const TAP_MAX_MS = 15_000

/** 端点源：生产用 VAD，测试注入假的 */
export interface TapEndpoint {
  start(onSpeechEnd: () => void): Promise<void>
  stop(): Promise<void>
}

/** 生产端点：VadEngine + 自己的一路 micLease。原生缺席返回 null（→ 只剩硬上限 + 服务端尾） */
export function vadEndpoint(): TapEndpoint | null {
  if (!vadNativeAvailable()) return null
  const vad = new VadEngine(TAP_SILENCE_MS)
  const lease = micLease()
  return {
    async start(onSpeechEnd) {
      await vad.load()
      await vad.start({ onSpeechStart: () => {}, onSpeechEnd, onError: () => {} })
      await lease.start((f) => vad.accept(f))
    },
    async stop() {
      await lease.stop()
      vad.stop()
      await vad.dispose()
    },
  }
}

export interface TapTalkDeps {
  endpoint: TapEndpoint | null
  /** ASR 的 recorder（缺省 micLease()；测试注入假的） */
  rec?: Recorder
}

export class TapTalkSession {
  private readonly asr: AsrSession
  private cap: ReturnType<typeof setTimeout> | null = null
  private ended = false

  constructor(
    cfg: AsrConfig,
    cb: AsrCallbacks,
    private readonly deps: TapTalkDeps,
  ) {
    this.asr = new AsrSession({ ...cfg, vadSilenceMs: cfg.vadSilenceMs ?? TAP_SILENCE_MS }, cb, deps.rec ?? micLease())
  }

  get active(): boolean {
    return this.asr.active
  }

  async start(): Promise<void> {
    await this.asr.start()
    if (this.deps.endpoint) {
      try {
        await this.deps.endpoint.start(() => void this.stop())
      } catch {
        // 端点起不来（模型载入失败等）：不阻塞录音，硬上限兜底
      }
    }
    this.cap = setTimeout(() => void this.stop(), TAP_MAX_MS)
  }

  /** 收尾（端点到 / 上限到 / 用户再点一下）：幂等 */
  async stop(): Promise<void> {
    if (this.ended) return
    this.ended = true
    this.clearCap()
    await this.deps.endpoint?.stop()
    await this.asr.stop()
  }

  /** 取消：不定稿、不回调 */
  async cancel(): Promise<void> {
    if (this.ended) return
    this.ended = true
    this.clearCap()
    await this.deps.endpoint?.stop()
    await this.asr.cancel()
  }

  private clearCap(): void {
    if (this.cap !== null) {
      clearTimeout(this.cap)
      this.cap = null
    }
  }
}
```

`mobile/src/features/chat/usePtt.ts` 完整替换：

```ts
// mobile/src/features/chat/usePtt.ts
// PTT 状态机（实施计划 M2-2）+ B2 T6 的手势契约入口（方案 §5.1.1）：
//  · 按住 / 松手（hold）：三个竞态守卫照旧（快按快松 pendingStop / <320ms 误触 / 并发按下忽略）
//  · 轻点（tap）：开始录音、说完自动收尾（core/voice/tapTalk.ts 三层收尾）；录音中再点 = 结束并提交
//  · 取消（cancel）：按住时上滑 / 隐私栏「关闭本轮麦克风」——不定稿、草稿删除；胶囊说
//    「已取消，这段话不会发给小舟」（**不是**「未上传」：流式 ASR 已上传的音频不能宣称从未上传）
//
// 麦：AsrSession 领一路 micLease（§0 第 5 条：recorder() 单例在免唤醒开着时让 PTT 收不到帧、
// 且 stop 会把免唤醒的真麦停掉——pttLease.test.ts 钉住）。
// barge-in 的 App 版：按下先停播报再开麦，物理上不会自听（计划 M2-3 的 stopTTS 硬停）。
import { useCallback, useEffect, useRef, useState } from 'react'

import { AsrSession, type AsrCallbacks, type AsrConfig } from '@/core/voice/asr'
import { micLease } from '@/core/voice/micBus'
import { PermissionDeniedError } from '@/core/voice/recorder'
import { speechController } from '@/core/voice/speech'
import { TapTalkSession, vadEndpoint } from '@/core/voice/tapTalk'
import { ASR_FALLBACK_MODEL, settingsStore } from '@/core/settings/store'

const MIN_DURATION_MS = 320
// 定稿超过它仍无结果 → UI 给中间反馈（兜底链最长 ≈24s，一直只写「识别中…」会让人以为卡死）
const SLOW_HINT_MS = 8000

export type PttState = 'idle' | 'recording' | 'finalizing'
export type PttMode = 'hold' | 'tap' | ''

export interface PttHandle {
  state: PttState
  /** 这次录音是按住还是轻点（空=没在录） */
  mode: PttMode
  partial: string
  error: string
  errorKind: 'permission' | 'start' | 'asr' | ''
  slow: boolean
  /** 上一次取消的时刻（胶囊短显「已取消…」用）；0=没有 */
  cancelledAt: number
  pressDown(): void
  pressUp(): void
  /** 轻点契约：空闲 = 开始录音（自动收尾）；录音中 = 结束并提交；识别中 = 忽略 */
  tap(): void
  /** 取消本次录音：不定稿、不回调 onFinal、草稿删除 */
  cancel(): void
}

interface VoiceSession {
  start(): Promise<void>
  stop(): Promise<void>
  cancel(): Promise<void>
}

export function usePtt(opts: {
  audioUrl: string
  sessionId: string
  onFinal(text: string): void
  /** 稳定 partial（全文）→ 记录里的草稿气泡（方案 §5.2.1） */
  onPartial?(text: string): void
  /** 太短 / 出错 / 空定稿 / 取消 → 草稿不留气泡 */
  onDiscard?(): void
}): PttHandle {
  const [state, setState] = useState<PttState>('idle')
  const [mode, setMode] = useState<PttMode>('')
  const [partial, setPartial] = useState('')
  const [error, setError] = useState('')
  const [errorKind, setErrorKind] = useState<PttHandle['errorKind']>('')
  const [slow, setSlow] = useState(false)
  const [cancelledAt, setCancelledAt] = useState(0)
  const sessionRef = useRef<VoiceSession | null>(null)
  const startingRef = useRef(false)
  const pendingStopRef = useRef(false)
  const pendingCancelRef = useRef(false)
  const startedAtRef = useRef(0)

  const reset = useCallback(() => {
    sessionRef.current = null
    startingRef.current = false
    pendingStopRef.current = false
    pendingCancelRef.current = false
    setPartial('')
    setState('idle')
    setMode('')
  }, [])

  const asrConfig = useCallback((): AsrConfig => {
    const s = settingsStore.getState().settings
    return {
      audioUrl: opts.audioUrl,
      language: s.asrLanguage,
      provider: s.asrProvider,
      model: s.asrModel,
      // 选中的就是备用模型时不再指自己（否则失败后原地重试一次，白等一个来回）
      ...(s.asrModel === ASR_FALLBACK_MODEL ? {} : { fallbackModel: ASR_FALLBACK_MODEL }),
      sessionId: opts.sessionId,
    }
  }, [opts.audioUrl, opts.sessionId])

  const callbacks = useCallback(
    (): AsrCallbacks => ({
      onPartial: (t) => {
        setPartial(t)
        opts.onPartial?.(t)
      },
      onFinal: (t) => {
        reset()
        const text = t.trim()
        if (text) opts.onFinal(text)
        else {
          setError('没听清，再说一次？')
          setErrorKind('asr')
          opts.onDiscard?.()
        }
      },
      onError: (msg) => {
        reset()
        setError(msg)
        setErrorKind('asr')
        opts.onDiscard?.()
      },
    }),
    [opts, reset],
  )

  const finishSession = useCallback(async () => {
    const session = sessionRef.current
    if (!session) return
    if (Date.now() - startedAtRef.current < MIN_DURATION_MS) {
      // ② 误触：不定稿——否则每次误触都往后端发一次空识别
      reset()
      opts.onDiscard?.()
      await session.cancel()
      return
    }
    setState('finalizing')
    await session.stop()
  }, [opts, reset])

  const begin = useCallback(
    (kind: 'hold' | 'tap') => {
      if (startingRef.current || sessionRef.current) return // ③ 并发按下忽略
      startingRef.current = true
      pendingStopRef.current = false
      pendingCancelRef.current = false
      setError('')
      setErrorKind('')
      setPartial('')
      setState('recording')
      setMode(kind)
      startedAtRef.current = Date.now()
      speechController().stop() // barge-in：先停播报，再开麦
      const session: VoiceSession =
        kind === 'tap'
          ? new TapTalkSession(asrConfig(), callbacks(), { endpoint: vadEndpoint() })
          : new AsrSession(asrConfig(), callbacks(), micLease())
      sessionRef.current = session
      void session
        .start()
        .then(() => {
          startingRef.current = false
          if (pendingCancelRef.current) {
            pendingCancelRef.current = false
            void session.cancel()
            return
          }
          if (pendingStopRef.current) {
            // ① 会话就绪前就松手了：这时才真正停
            pendingStopRef.current = false
            void finishSession()
          }
        })
        .catch((e: unknown) => {
          reset()
          const denied = e instanceof PermissionDeniedError
          setError(denied ? '需要麦克风权限，请在系统设置里允许' : '录音启动失败')
          setErrorKind(denied ? 'permission' : 'start')
          opts.onDiscard?.()
        })
    },
    [asrConfig, callbacks, finishSession, opts, reset],
  )

  useEffect(() => {
    if (state !== 'finalizing') {
      setSlow(false)
      return
    }
    const t = setTimeout(() => setSlow(true), SLOW_HINT_MS)
    return () => clearTimeout(t)
  }, [state])

  const pressDown = useCallback(() => begin('hold'), [begin])

  const pressUp = useCallback(() => {
    if (startingRef.current) {
      pendingStopRef.current = true
      return
    }
    void finishSession()
  }, [finishSession])

  const tap = useCallback(() => {
    if (state === 'finalizing') return
    if (sessionRef.current || startingRef.current) {
      pressUp() // 录音中轻点 = 结束并提交
      return
    }
    begin('tap')
  }, [begin, pressUp, state])

  const cancel = useCallback(() => {
    if (!sessionRef.current && !startingRef.current) return
    if (startingRef.current) {
      pendingCancelRef.current = true // 会话还没建起来：就绪后立刻 cancel，别留一个开着的麦
      setState('idle')
      setMode('')
      setPartial('')
    } else {
      const session = sessionRef.current
      reset()
      void session?.cancel()
    }
    setCancelledAt(Date.now())
    opts.onDiscard?.()
  }, [opts, reset])

  return { state, mode, partial, error, errorKind, slow, cancelledAt, pressDown, pressUp, tap, cancel }
}
```

`mobile/src/core/voice/handsFree.ts`——`stats()` 之前加三个方法：

```ts
  /** 轻点光球（方案 §5.1.1）= 一次「手动唤醒」：FSM 的公开入口 wake()——ARMED/FOLLOWUP 进聆听、
   *  SPEAKING 先停播再听、THINKING 取消在飞轮再听。FSM 一字不改，KWS 与命中时同样 reset */
  wakeManually(): void {
    void this.kws.reset()
    this.vl.wake()
  }

  /** 录音中轻点 = 结束并提交：与 FSM 的 onEndpoint 效果逐字同构（S2S 请收尾 / classic 请定稿） */
  endUtterance(): void {
    if (this.vl.state !== 'LISTENING') return
    if (this.s2s) this.s2s.commitAudio()
    else void this.asr?.stop()
  }

  /** 「结束本轮收音」/「重新开启插话」的正式实现（评审 D7）：FSM 拆机再装机——
   *  handsFreeOff → IDLE（关 ASR、清定时器、**复位会话级 _bargeInDisabled**）→ handsFreeOn → ARMED。
   *  引擎、麦、KWS 都不动（不是 disable/enable），也不碰持久化设置（B1 那 50ms 的 false 窗口没了）。
   *  onBargeInDisabled('') = 「已复位」，Presence 据此撤掉 Dock 里那条降级 */
  recycle(): void {
    if (!this.on) return
    this.vl.handsFreeOff()
    this.vl.handsFreeOn()
    this.deps.onBargeInDisabled?.('')
  }
```

`mobile/src/features/chat/useHandsFree.ts`：`HandsFreeUi` 加
```ts
  /** 轻点光球 = 手动唤醒（免唤醒开着时由它开始听） */
  wake(): void
  /** 录音中轻点 = 结束并提交 */
  endUtterance(): void
  /** 结束本轮收音 / 重新开启插话（评审 D7） */
  recycle(): void
```
实现（放在 `return` 之前）：
```ts
  const wake = useCallback(() => ctlRef.current?.wakeManually(), [])
  const endUtterance = useCallback(() => ctlRef.current?.endUtterance(), [])
  const recycle = useCallback(() => ctlRef.current?.recycle(), [])
  return { fsm, orb, partial, availability, error, bargeInDisabled, pipelineDegraded, wake, endUtterance, recycle }
```
（import 加 `useCallback`。）

`mobile/src/features/chat/Composer.tsx` 完整替换：

```tsx
// mobile/src/features/chat/Composer.tsx
// 输入区（M1-3 + M2-2 PTT，Aurora Glass 复刻轮重皮）+ B2 T6 手势契约（方案 §5.1.1）：
//  · 轻点光球：开始录音（与免唤醒开关无关）/ 录音中 = 结束并提交 / 播报中 = 先停 TTS 再录 / 思考中 = 展开语音层
//  · 长按光球 / 空输入框 ≥300ms：按住说话；**按住时上滑 ≥60dp 取消**（微信惯例，Q13）；松手发送
//  · 长按识别前允许移动 12dp（列表纵向滚动与 chips 横滑不得误触）
//  · 输入框有字时长按走原生选择，**只有光球仍可 PTT**
//  · TalkBack：轻点切换开始 / 停止，label 随状态；热区 56dp（≥48）
// 手势用 react-native-gesture-handler（PackageList.java:73 已注册，零新依赖）。
// 「轻点到底做什么」的判据不在这里——Composer 只报告手势，ChatScreen 的 onTap 决定走免唤醒的
// 手动唤醒还是 PTT 的 tap 会话（哪个引擎持有麦是 ChatScreen 知道的事实）。
import { useRef, useState } from 'react'
import { ScrollView, Text, TextInput, View, Pressable } from 'react-native'
import { Gesture, GestureDetector } from 'react-native-gesture-handler'

import type { FontScalePref } from '../../core/settings/store'
import { AuroraOrb, type OrbState } from '../../ui/aurora'
import { ORB_A11Y } from '../../ui/aurora/AuroraOrb'
import { AURORA, type Palette } from '../../ui/theme'
import { RADIUS, TARGET, scale } from '../../ui/tokens'
import type { PttHandle } from './usePtt'

/** 长按判定（ms）：方案 §5.1.1 的 ≥300；usePtt 的 MIN_DURATION_MS=320 是「录了多久」，是另一件事 */
export const HOLD_MS = 300
/** 长按识别前允许的移动（dp）：超过就交给滚动 */
export const HOLD_MAX_DISTANCE = 12
/** 按住时上滑多少算取消（dp） */
export const CANCEL_DY = 60

export interface ComposerProps {
  p: Palette
  quickCommands: string[]
  /** 有在飞轮（pending/streaming/process 任一）→ 显示打断 */
  busy: boolean
  /** 语音输入把手；null=服务器未配置（没有 audioUrl 就没有语音） */
  ptt: PttHandle | null
  /** 光球主态由调用方给（v2=snapshot.primary，v1=ChatScreen 里的旧推导） */
  orbState: OrbState
  orbDim?: boolean
  /** 语音层开着时主球转静态（同屏循环动画常态 1 个，方案 §11.4） */
  orbAnimated?: boolean
  fontScale: FontScalePref
  onSend(text: string): void
  onInterrupt(): void
  /** 轻点光球（判据在 ChatScreen） */
  onTap(): void
}

export function Composer({ p, quickCommands, busy, ptt, orbState, orbDim, orbAnimated, fontScale, onSend, onInterrupt, onTap }: ComposerProps) {
  const [input, setInput] = useState('')
  const heldRef = useRef(false)
  const cancelledRef = useRef(false)
  const submit = () => {
    const text = input.trim()
    if (!text) return
    onSend(text)
    setInput('')
  }
  const recording = ptt?.state === 'recording'
  const finalizing = ptt?.state === 'finalizing'

  // 按住 = Pan.activateAfterLongPress：激活即按下；onUpdate 看上滑；结束即松手（取消过就不发）
  const makeHold = (enabled: boolean) =>
    Gesture.Pan()
      .runOnJS(true)
      .enabled(enabled)
      .maxPointers(1)
      .minDistance(HOLD_MAX_DISTANCE)
      .activateAfterLongPress(HOLD_MS)
      .onStart(() => {
        heldRef.current = true
        cancelledRef.current = false
        ptt?.pressDown()
      })
      .onUpdate((e) => {
        if (heldRef.current && !cancelledRef.current && e.translationY < -CANCEL_DY) {
          cancelledRef.current = true
          ptt?.cancel()
        }
      })
      .onFinalize(() => {
        if (heldRef.current && !cancelledRef.current) ptt?.pressUp()
        heldRef.current = false
      })
  const tap = Gesture.Tap()
    .runOnJS(true)
    .maxDuration(HOLD_MS - 20)
    .onEnd(() => onTap())
  const orbGesture = Gesture.Exclusive(makeHold(!!ptt && !finalizing), tap)
  // 空输入框 = 背板的一部分：有字时关掉（原生长按选择接管），轻点仍由原生聚焦
  const plateGesture = makeHold(!!ptt && !finalizing && input.length === 0)

  const a11yLabel = recording ? '小舟，结束并发送' : `${ORB_A11Y[orbState]}，开始说话`

  return (
    <View
      style={{
        borderTopWidth: 1,
        borderColor: p.line,
        backgroundColor: p.dark ? 'rgba(6,8,15,0.55)' : 'rgba(237,241,250,0.72)',
      }}
    >
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 12, paddingTop: 8 }}>
        {quickCommands.map((c) => (
          <Pressable
            key={c}
            onPress={() => onSend(c)}
            style={{ backgroundColor: p.fill, borderWidth: 1, borderColor: p.fill2, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 7 }}
          >
            <Text style={{ color: p.fg2, fontSize: p.font(12) }}>{c}</Text>
          </Pressable>
        ))}
      </ScrollView>
      <View style={{ flexDirection: 'row', gap: 10, padding: 10, alignItems: 'flex-end' }}>
        {ptt ? (
          <GestureDetector gesture={orbGesture}>
            <View
              testID="composer-orb"
              accessible
              accessibilityRole="button"
              accessibilityLabel={a11yLabel}
              accessibilityHint="轻点开始说话，说完自动发送；长按可按住说话，上滑取消"
              style={{
                width: scale(TARGET.driving, 'target', fontScale),
                height: scale(TARGET.driving, 'target', fontScale),
                borderRadius: RADIUS.full,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: recording ? p.accentSoft : 'transparent',
              }}
            >
              <AuroraOrb size={44} state={orbState} dim={orbDim} animated={orbAnimated ?? true} />
            </View>
          </GestureDetector>
        ) : null}
        <GestureDetector gesture={plateGesture}>
          <View style={{ flex: 1 }}>
            <TextInput
              testID="composer-input"
              style={{
                backgroundColor: p.fill,
                borderWidth: 1,
                borderColor: p.fill2,
                borderRadius: 14,
                paddingHorizontal: 14,
                paddingVertical: 10,
                fontSize: p.font(15),
                color: p.fg1,
                maxHeight: 120,
              }}
              value={input}
              onChangeText={setInput}
              placeholder={recording ? '正在听…' : '和小舟说点什么…'}
              placeholderTextColor={p.fg3}
              multiline
              onSubmitEditing={submit}
              submitBehavior="blurAndSubmit"
              returnKeyType="send"
            />
          </View>
        </GestureDetector>
        {busy ? (
          <Pressable
            onPress={onInterrupt}
            style={{ backgroundColor: p.amberSoft, borderWidth: 1, borderColor: 'rgba(245,158,11,0.3)', borderRadius: 14, paddingHorizontal: 14, minHeight: 44, justifyContent: 'center' }}
          >
            <Text style={{ color: p.amber, fontSize: p.font(14) }}>■ 打断</Text>
          </Pressable>
        ) : null}
        <Pressable
          testID="composer-send"
          onPress={submit}
          style={{ experimental_backgroundImage: AURORA.gradient, borderRadius: 14, paddingHorizontal: 18, minHeight: 44, justifyContent: 'center', boxShadow: '0 4px 22px rgba(91,140,255,0.45)' }}
        >
          <Text style={{ color: '#fff', fontSize: p.font(15), fontWeight: '600' }}>发送</Text>
        </Pressable>
      </View>
    </View>
  )
}
```
⚠ RNGH 的根容器 `GestureHandlerRootView` 在 T3 已包进 `_layout.tsx`；手势仍不响应时先核它在（`grep -n GestureHandlerRootView mobile/src/app/_layout.tsx`），再考虑 §0 第 1 条的 `PanResponder` 退路。

`mobile/src/features/chat/ChatScreen.tsx`：删掉 `reenableBargeIn`；`usePresence` 之后加：
```ts
  // 轻点光球：哪个引擎持有麦，就由谁开始听——免唤醒开着 = 手动唤醒（FSM 自带 VAD 收尾）；
  // 否则 = PTT 的 tap 会话（端侧 VAD / 服务端尾 / 15s 三层收尾）。这是「谁持有麦」的事实，不是判据
  const hfOn = settings.handsFree && hf.availability.usable
  const startListening = useCallback(() => {
    if (!hfOn) {
      ptt.tap()
      return
    }
    if (hf.fsm === 'LISTENING') hf.endUtterance()
    else hf.wake()
  }, [hfOn, hf, ptt])
  // ■ 打断 / 播报中轻点：先停（cancel 帧 + stop TTS），再听（方案 §5.2 规则 4：层不收、speaking→listening）
  const interruptAndListen = useCallback(() => {
    core.cancelCurrentTurn()
    startListening()
  }, [core, startListening])
  // 光球轻点契约（方案 §5.1.1 表）：播报中 = 停播再录；思考 / 执行中 = 展开语音层；其余 = 开始 / 结束录音
  const onOrbTap = useCallback(() => {
    if (snapshot.agent === 'speaking') interruptAndListen()
    else if (snapshot.agent === 'thinking' || snapshot.agent === 'processing') setSheetOverride({ turnId: latestTurnId, mode: 'open' })
    else startListening()
  }, [snapshot.agent, interruptAndListen, latestTurnId, startListening])
  // 「关闭本轮麦克风」（隐私栏）与「重新开启插话」（Dock）：评审 D7——不再翻持久化开关
  const stopMic = useCallback(() => {
    ptt.cancel()
    if (hfOn) hf.recycle()
  }, [ptt, hfOn, hf])
```
接线：`FocusDock onReenableBargeIn={hf.recycle}`、`PrivacyRail onStopMic={stopMic}`、`VoiceSheet onInterrupt={interruptAndListen}`、`Composer onTap={onOrbTap}`。

`mobile/src/core/presence/presence.ts`：`PresenceInput` 加
```ts
  /** 2s 短提示（取消 / 回声）；判据 NOTICE_SHOW_MS 在这里 */
  notice?: { text: string; at: number } | null
```
常量 `export const NOTICE_SHOW_MS = 2000`；胶囊链里 `else if (agent === 'thinking') …` 之后、`else if (agent === 'followup')` 之前插入：
```ts
  else if (!!i.notice && i.now - i.notice.at < NOTICE_SHOW_MS) capsule = { text: i.notice.text, tone: 'neutral' }
```

`mobile/src/features/chat/usePresence.ts`：`derivePresence` 调用前加（import `NOTICE_SHOW_MS`）：
```ts
  // 2s 提示：取消（§5.1.1 的隐私文案——「不会发给」而不是「未上传」）；T11 把回声并进来
  const notice = ptt?.cancelledAt ? { text: '已取消，这段话不会发给小舟', at: ptt.cancelledAt } : null
```
`needsTick` 加 `|| (!!notice && now - notice.at < NOTICE_SHOW_MS)`；`derivePresence({...})` 加 `notice,`。

- [ ] **步骤 4：跑绿 + `tsc`**；全量。

- [ ] **反向验证**：
  1. `usePtt` 改回 `new AsrSession(…, recorder())`（要 import）⇒ `pttLease` 源码断言红。
  2. `TapTalkSession.start` 不设 `cap` ⇒ 只红「硬上限」。
  3. `stop()` 里去掉 `endpoint.stop()` ⇒ 只红「VAD 端点」与「cancel」两条的 `ep.stopped`。
  4. `recycle()` 里去掉 `handsFreeOff()` ⇒ 只红 recycle 用例（`states` 与 `bargeInDisabled`）。
  5. `notice` 分支挪到 `followup` 之后 ⇒ 只红 notice 用例第三行。
  6. 真机（第 3 批收口）：**免唤醒关** → 轻点 → 层升、胶囊「在听…」→ 说一句停 1s → 自动收尾发送（VAD 在场；`b2-06-tap.png` 连拍）；再轻点一次中途 → 立刻收尾；**免唤醒开** → 轻点 → 同（FSM）；**免唤醒开 + 按住说话** → 有转写、松手发送、之后说「小舟小舟」仍能唤醒（麦没被掐——§0 第 5 条的真机确认，B1 验收表第 6 条第二行顺便收口）；长按 → 上滑 60dp → 胶囊「已取消，这段话不会发给小舟」2s、记录无气泡；输入框有字 → 长按输入框走原生选择、长按光球仍 PTT；TalkBack 开 → 光球 label「小舟，开始说话」/ 录音中「小舟，结束并发送」；播报中轻点 → 停播 + 进入收音（层不收）；隐私栏「关闭本轮麦克风」→ 免唤醒回 ARMED、设置里开关仍是开的（D7）。

- [ ] **提交**：
```bash
git add -- mobile/src/core/voice/tapTalk.ts mobile/test/tapTalk.test.ts mobile/test/pttLease.test.ts && git commit -m "feat(mobile): UX v2 B2-6 轻点光球即说（端侧 VAD 三层收尾）+ Composer 手势契约（长按/上滑取消/松手）+ PTT 领 micLease（免唤醒下 PTT 修复）+ recycle 替代翻开关（D7）" -- mobile/src/core/voice/tapTalk.ts mobile/test/tapTalk.test.ts mobile/test/pttLease.test.ts mobile/src/core/voice/asr.ts mobile/test/voiceAsr.test.ts mobile/src/features/chat/usePtt.ts mobile/src/core/voice/handsFree.ts mobile/test/handsFree.test.ts mobile/src/features/chat/useHandsFree.ts mobile/src/features/chat/Composer.tsx mobile/src/features/chat/ChatScreen.tsx mobile/src/core/presence/presence.ts mobile/src/features/chat/usePresence.ts mobile/test/presence.test.ts && git show --stat HEAD
```

### Task 7: 边缘极光（语音层顶缘 2dp 呼吸）

**Files:**
- 新建 `mobile/src/ui/aurora/EdgeGlow.tsx`；修改 `mobile/src/ui/aurora/index.ts`（导出）、`mobile/src/features/chat/VoiceSheet.tsx`（顶缘挂载）

**为什么**：方案 §5.2 规则 6——层顶缘 2px `AURORA` 呼吸 1.6s，只在 `listening/thinking`（虹彩纪律允许的「听/想时屏幕边缘」那一处）。RN 实现 = 一条 2dp 高的 `experimental_backgroundImage` 线性渐变 View + opacity 呼吸，零新依赖。纯动效、无判据 ⇒ 没有 jest；验收是真机录屏。

- [ ] **步骤 1：实现**

```tsx
// mobile/src/ui/aurora/EdgeGlow.tsx
// 语音层顶缘极光（方案 §5.2 规则 6）：2dp 线性渐变 + 1.6s 呼吸，**只在 listening / thinking**——
// 虹彩纪律允许的「听/想时屏幕边缘」那一处（Guidelines :113-119），hmi .au-edge-glow 的移植。
// 零依赖：experimental_backgroundImage 渐变 + reanimated opacity。reduce-motion 的静帧留 B4。
import { useEffect } from 'react'
import Animated, {
  Easing,
  cancelAnimation,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated'

import { AURORA } from '../theme'

/** 呼吸周期（ms）——方案原文 1.6s */
export const EDGE_GLOW_PERIOD_MS = 1600

export function EdgeGlow({ active }: { active: boolean }) {
  const t = useSharedValue(0)
  useEffect(() => {
    cancelAnimation(t)
    if (!active) {
      t.value = withTiming(0, { duration: 200 })
      return
    }
    t.value = withRepeat(
      withSequence(
        withTiming(1, { duration: EDGE_GLOW_PERIOD_MS / 2, easing: Easing.inOut(Easing.ease) }),
        withTiming(0.35, { duration: EDGE_GLOW_PERIOD_MS / 2, easing: Easing.inOut(Easing.ease) }),
      ),
      -1,
    )
    return () => cancelAnimation(t)
  }, [active, t])
  const style = useAnimatedStyle(() => ({ opacity: t.value }))
  return <Animated.View pointerEvents="none" testID="edge-glow" style={[{ height: 2, experimental_backgroundImage: AURORA.gradient }, style]} />
}
```
`mobile/src/ui/aurora/index.ts` 加 `export { EdgeGlow } from './EdgeGlow'`。`VoiceSheet.tsx` 的 `<Glass …>` 内第一个子元素（把手之前）加：
```tsx
            <EdgeGlow active={snapshot.primary === 'listening' || snapshot.primary === 'thinking'} />
```
（import 从 `@/ui/aurora` 加 `EdgeGlow`。）

- [ ] **步骤 2：`tsc` 0；真机**：轻点说话 → `screenrecord`（`//sdcard/b2-07.mp4`，8s）→ 抽 4 帧（0/0.4/0.8/1.2s）顶缘 2dp 的取样色亮度不同（同 B1 第 4 批的 stdlib PNG 取色法）；回答定稿、播报中 → 顶缘不亮（`speaking` 不在允许列表）。

- [ ] **反向验证**：`active` 恒 true ⇒ 播报中顶缘仍亮（录屏可见）——用来证明「只在 listening/thinking」不是巧合；还原。

- [ ] **提交**：
```bash
git add -- mobile/src/ui/aurora/EdgeGlow.tsx && git commit -m "feat(mobile): UX v2 B2-7 语音层顶缘极光 2dp 呼吸（只在 listening/thinking，零依赖）" -- mobile/src/ui/aurora/EdgeGlow.tsx mobile/src/ui/aurora/index.ts mobile/src/features/chat/VoiceSheet.tsx && git show --stat HEAD
```

### Task 8: `card_group` 主卡 / 折叠（`display_priority` 终于有消费方）

**Files:**
- 新建 `mobile/src/core/cards/cardGroup.ts`、`mobile/src/features/cards/CardGroup.tsx`、`mobile/test/cardGroup.test.ts`
- 修改 `mobile/src/features/cards/CardRenderer.tsx`（REGISTRY `card_group` → `CardGroup`）

**为什么**：方案 §5.2 规则 7 / §5.4——`final.ui_card` 为 `card_group` 时按 `display_priority` 升序取首张为主卡，其余「还有 N 张 ›」折叠、展开是竖排（不做轮播——语音场景里轮播等于藏卡）。聚合器（`orchestrator/cloud/aggregator.py:158`）用同一个缺省 2，**这是它的排序第一次有消费方（P9 的修法）**。判据放 `core/cards/`（零 RN）：T13 的回执要从 card_group 的主卡取 `_prov`，core 不能反向依赖 features。语音层不用改——它经 `CardRenderer` 渲染，注册表换了它就换了。

- [ ] **步骤 1：写失败测试**

```ts
// mobile/test/cardGroup.test.ts
// card_group 主卡判定（方案 §5.2 规则 7 / §5.4）：display_priority 升序，缺省 2，同级保原序。
import { cardPriority, splitCardGroup } from '@/core/cards/cardGroup'

test('display_priority 升序取主卡；缺省 2（CLAUDE.md 卡片优先级默认）；同级保原序', () => {
  const items = [
    { type: 'weather' },
    { type: 'trip_itinerary', display_priority: 0 },
    { type: 'news_digest', display_priority: 2 },
    { type: 'poi_list', display_priority: 1 },
  ]
  const { main, rest } = splitCardGroup(items)
  expect(main).toEqual({ type: 'trip_itinerary', display_priority: 0 })
  expect(rest.map((c) => c.type)).toEqual(['poi_list', 'weather', 'news_digest'])
})

test('非法 display_priority（字符串 / NaN / 缺 / null 卡）都按 2', () => {
  expect(cardPriority({ display_priority: '0' })).toBe(2)
  expect(cardPriority({ display_priority: NaN })).toBe(2)
  expect(cardPriority({})).toBe(2)
  expect(cardPriority(null)).toBe(2)
})

test('空组 → main=null（CardGroup 渲染 null——CardRenderer 铁则「没有卡才允许空」）', () => {
  expect(splitCardGroup([])).toEqual({ main: null, rest: [] })
})
```

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

```ts
// mobile/src/core/cards/cardGroup.ts
// card_group 的主卡判定（方案 §5.2 规则 7 / §5.4）：按 display_priority 升序取首张为主卡（缺省 2，
// CLAUDE.md 卡片优先级默认），其余折叠。聚合器（orchestrator/cloud/aggregator.py:158）用同一个缺省
// ——这是它的排序第一次有消费方（P9）。稳定排序：同优先级保原序。零 RN import（回执也读它）。
export function cardPriority(card: unknown): number {
  const v = (card as { display_priority?: unknown } | null)?.display_priority
  return typeof v === 'number' && Number.isFinite(v) ? v : 2
}

export interface CardSplit<T> {
  main: T | null
  rest: T[]
}

export function splitCardGroup<T>(items: readonly T[]): CardSplit<T> {
  const sorted = items
    .map((c, i) => ({ c, i }))
    .sort((a, b) => cardPriority(a.c) - cardPriority(b.c) || a.i - b.i)
    .map((x) => x.c)
  return { main: sorted[0] ?? null, rest: sorted.slice(1) }
}
```

```tsx
// mobile/src/features/cards/CardGroup.tsx
// card_group 渲染（方案 §5.2 规则 7 / §5.4）：主卡全展 + 其余「还有 N 张 ›」竖排展开
// （不做轮播——语音场景里轮播等于藏卡）。判据在 core/cards/cardGroup.ts；这里只渲染。
// 与 CardRenderer 互相 import 是**渲染期**的循环（子卡在函数体里才引用），与今天注册表的递归同形。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import { splitCardGroup } from '../../core/cards/cardGroup'
import type { Palette } from '../../ui/theme'
import { TARGET } from '../../ui/tokens'
import { CardRenderer } from './CardRenderer'
import type { SendFn } from './parts'

export function CardGroup({ p, items, onSend }: { p: Palette; items: unknown[]; onSend: SendFn }) {
  const [open, setOpen] = useState(false)
  const { main, rest } = splitCardGroup(items)
  if (!main) return null
  return (
    <View style={{ gap: 8 }}>
      <CardRenderer p={p} card={main} onSend={onSend} />
      {rest.length ? (
        <Pressable
          testID="card-group-more"
          accessibilityRole="button"
          onPress={() => setOpen((o) => !o)}
          style={{ minHeight: TARGET.parked, justifyContent: 'center' }}
        >
          <Text style={{ color: p.accent, fontSize: p.font(12) }}>{open ? '收起其余卡片 ⌃' : `还有 ${rest.length} 张 ›`}</Text>
        </Pressable>
      ) : null}
      {open ? rest.map((sub, i) => <CardRenderer key={i} p={p} card={sub} onSend={onSend} />) : null}
    </View>
  )
}
```
`CardRenderer.tsx`：import `CardGroup`，REGISTRY 的 `card_group` 项替换为
```tsx
  card_group: ({ p, card, onSend }) => <CardGroup p={p} items={card.items || []} onSend={onSend} />,
```

- [ ] **步骤 4：跑绿 + `tsc`**；`cards.test.ts` 的注册表守卫仍绿（键没变）。

- [ ] **反向验证**：排序改降序 ⇒ 只红第一条；缺省改 0 ⇒ 只红第二条。真机（第 3 批收口）：「查英伟达股价和新闻」→ 记录里与语音层里都是主卡在上、「还有 1 张 ›」可展开、展开竖排（`b2-08-group-{fold,open}.png`）；卡片画廊 `card_group` 样本同样折叠。

- [ ] **提交**：
```bash
git add -- mobile/src/core/cards/cardGroup.ts mobile/src/features/cards/CardGroup.tsx mobile/test/cardGroup.test.ts && git commit -m "feat(mobile): UX v2 B2-8 card_group 主卡/折叠——display_priority 升序取主卡，其余竖排展开（P9）" -- mobile/src/core/cards/cardGroup.ts mobile/src/features/cards/CardGroup.tsx mobile/test/cardGroup.test.ts mobile/src/features/cards/CardRenderer.tsx && git show --stat HEAD
```

### Task 9: follow-up chips（`final.follow_up` + 候选集）

**Files:**
- 新建 `mobile/src/core/session/followUps.ts`、`mobile/src/features/chat/FollowUpChips.tsx`、`mobile/test/followUps.test.ts`
- 修改 `mobile/src/features/chat/VoiceSheet.tsx`（chips 行）、`mobile/src/features/chat/ChatScreen.tsx`（传 `candidates`）

**为什么**：方案 §5.2 图「follow-up chips（来自 `final.follow_up` 与候选集）」。chip 点按 = 合成一句话走普通 `send`（架构约束：卡内动作 / chip 都不直连执行，§5.4）。**文本以 `sendRouter` 能消费为准**——测试直接拿 `routeSend` 验，而不是猜 `nav.mjs` 的正则；若某句不命中，改 chip 文本不改共享模块。

- [ ] **步骤 1：写失败测试**

```ts
// mobile/test/followUps.test.ts
// follow-up chips（方案 §5.2）：follow_up 排第一、去重、上限 4；候选集 chip 的文本 sendRouter 必须认得。
import { emptyCandidates, type CandidateState } from '@/core/session/candidates'
import { MAX_CHIPS, followUpChips } from '@/core/session/followUps'
import { routeSend } from '@/core/session/sendRouter'

const ctx = (candidates: CandidateState) => ({ candidates, locationEnabled: true })

test('follow_up 排第一；空候选 + 无 follow_up → 空数组（chips 行不渲染）', () => {
  expect(followUpChips('要不要看明天的？', emptyCandidates())).toEqual([{ label: '要不要看明天的？', text: '要不要看明天的？' }])
  expect(followUpChips(undefined, emptyCandidates())).toEqual([])
  expect(followUpChips('  ', emptyCandidates())).toEqual([])
})

test('候选集 chips 的文本 sendRouter 都认得（chip 是合成一句话，不是新通道）', () => {
  const cand: CandidateState = {
    ...emptyCandidates(),
    category: { keyword: '咖啡', page: 1 },
    poiNames: ['星巴克', '瑞幸'],
    placeItems: [
      { id: 'B1', name: '星巴克' },
      { id: 'B2', name: '瑞幸' },
    ],
  }
  const chips = followUpChips(undefined, cand)
  const refresh = routeSend(chips.find((c) => c.label === '换一批')!.text, ctx(cand))
  expect(refresh.kind).toBe('dispatch')
  expect(refresh.kind === 'dispatch' && refresh.categoryPage).toBe(2)
  const nav = routeSend(chips.find((c) => c.label === '导航去第一个')!.text, ctx(cand))
  expect(nav.kind === 'dispatch' && nav.text).toBe('导航去星巴克')
})

test('poi_list（无 placeItems）的「第一个」→ 导航去{名称}', () => {
  const cand: CandidateState = { ...emptyCandidates(), poiNames: ['加油站A', '加油站B'] }
  const chips = followUpChips(undefined, cand)
  const nav = routeSend(chips.find((c) => c.label === '导航去第一个')!.text, ctx(cand))
  expect(nav.kind === 'dispatch' && nav.text).toBe('导航去加油站A')
})

test('intent_choice 选项直接成 chip（label→send_text）；去重；上限 MAX_CHIPS', () => {
  const cand: CandidateState = {
    ...emptyCandidates(),
    intentChoice: { options: [{ label: '查天气', send_text: '查深圳天气' }, { label: '查空气', send_text: '查深圳空气质量' }, { label: '查天气', send_text: '查深圳天气' }] },
    category: { keyword: '咖啡', page: 1 },
    poiNames: ['a', 'b'],
  }
  const chips = followUpChips('要不要看明天的？', cand)
  expect(chips).toHaveLength(MAX_CHIPS)
  expect(new Set(chips.map((c) => c.text)).size).toBe(MAX_CHIPS)
  expect(chips[0].text).toBe('要不要看明天的？')
})
```

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

```ts
// mobile/src/core/session/followUps.ts
// follow-up chips（方案 §5.2 图：「来自 final.follow_up 与候选集」）。chip 点按 = 合成一句话走普通 send
// （架构约束：卡内动作 / chip 都不直连执行）。**文本以 sendRouter 能消费为准**——测试直接拿 routeSend 验，
// 不猜 nav.mjs 的正则；某句不命中就改这里的文本，不改共享模块。零 RN import。
import type { CandidateState } from './candidates'

export interface FollowUpChip {
  label: string
  text: string
}

export const MAX_CHIPS = 4

export function followUpChips(followUp: string | undefined, cand: CandidateState): FollowUpChip[] {
  const out: FollowUpChip[] = []
  const push = (label: string, text: string) => {
    const t = text.trim()
    if (!t || out.some((c) => c.text === t) || out.length >= MAX_CHIPS) return
    out.push({ label: label.trim() || t, text: t })
  }
  if (followUp) push(followUp, followUp)
  if (cand.category) push('换一批', '换一批')
  // 周边发现（place_list，带 id）：「导航去第N个」命中 sendRouter 的 PLACE_NAVIGATE_RE 分支；
  // 普通 poi_list：poiSelectionIndex 认「第一个」
  if (cand.placeItems?.length) push('导航去第一个', '导航去第一个')
  else if (cand.poiNames?.length) push('导航去第一个', '第一个')
  for (const o of cand.intentChoice?.options ?? []) push(o.label, o.send_text)
  return out
}
```

```tsx
// mobile/src/features/chat/FollowUpChips.tsx
// chips 行（语音层内）：横向、48dp 触控高度、点按 = 普通 send。零判据——chips 由 followUps.ts 算。
import { Pressable, ScrollView, Text } from 'react-native'

import type { FollowUpChip } from '@/core/session/followUps'
import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export function FollowUpChips({ p, fontScale, chips, onSend }: { p: Palette; fontScale: FontScalePref; chips: FollowUpChip[]; onSend(text: string): void }) {
  if (!chips.length) return null
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 2 }} style={{ alignSelf: 'stretch' }}>
      {chips.map((c) => (
        <Pressable
          key={c.text}
          testID="followup-chip"
          accessibilityRole="button"
          onPress={() => onSend(c.text)}
          style={{ minHeight: scale(TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 14, borderRadius: RADIUS.full, backgroundColor: p.accentSoft, borderWidth: 1, borderColor: p.accent }}
        >
          <Text style={{ color: p.accent, fontSize: scale(TYPE.caption + 1, 'text', fontScale) }}>{c.label}</Text>
        </Pressable>
      ))}
    </ScrollView>
  )
}
```
`VoiceSheet.tsx`：props 加 `candidates: CandidateState`（import type from `@/core/session/candidates`；import `followUpChips` 与 `FollowUpChips`）；卡片之后加：
```tsx
              {assistant && !assistant.streaming && !assistant.pending ? (
                <FollowUpChips p={p} fontScale={fontScale} chips={followUpChips(assistant.followUp, props.candidates)} onSend={props.onSend} />
              ) : null}
```
`ChatScreen.tsx`：`<VoiceSheet … candidates={core.candidates} />`（`core.candidates` 是普通字段不是 store——它只在 final 到达时变，而 final 同时改 `messages`，层随之重渲，够用）。

- [ ] **步骤 4：跑绿 + `tsc`**。⚠ 第二条用例若红在 `routeSend`（`ordinalSelectIn('导航去第一个')` 不命中）：改 `followUps.ts` 里那句文本到命中为止，**不改 `nav.mjs`**，把最终文本写进 §6.3。

- [ ] **反向验证**：`push` 去掉去重 ⇒ 只红第四条；`if (followUp)` 挪到最后 ⇒ 只红 `chips[0]` 断言。真机：「附近有什么咖啡」→ 层里出现「换一批」「导航去第一个」→ 点「换一批」→ 第二页卡（`b2-09-chips.png`）。

- [ ] **提交**：
```bash
git add -- mobile/src/core/session/followUps.ts mobile/src/features/chat/FollowUpChips.tsx mobile/test/followUps.test.ts && git commit -m "feat(mobile): UX v2 B2-9 语音层 follow-up chips（final.follow_up + 候选集，文本以 sendRouter 判定为准）" -- mobile/src/core/session/followUps.ts mobile/src/features/chat/FollowUpChips.tsx mobile/test/followUps.test.ts mobile/src/features/chat/VoiceSheet.tsx mobile/src/features/chat/ChatScreen.tsx && git show --stat HEAD
```

### Task 10: 视觉抓帧——先落气泡（📷 角标，`vision_frame_id` 迟到补进 meta）

**Files:**
- 修改 `mobile/src/core/session/store.ts`（`beginUserBubble` / `markVision` / `visionIds`）、`mobile/test/sessionStore.test.ts`
- 修改 `mobile/src/features/chat/ChatScreen.tsx`（`onSend` 视觉分支顺序倒过来）、`mobile/src/features/chat/MessageBubble.tsx`（📷 角标）、`mobile/src/features/chat/VoiceSheet.tsx`（转写前缀）

**为什么**：方案 §5.5——`looking` 态用户气泡**立刻**出现并带 📷（不等相机冷启动几百毫秒），`vision_frame_id` 迟到再补进 meta；HMI 的 `__bubbled` 先例（`hmi/src/App.tsx:719-726`）。SessionCore 的对应入口 = `beginUserBubble()` + `send({ bubbleId })`（T4 已有复用语义）。**拍完 0.5s 内不出预览**（红线：图像不落端——预览也是一份落端，刻意不做）。B1 出账⑧（`looking` 白环无静态取证）随本任务用录屏取。

- [ ] **步骤 1：写失败测试**（`sessionStore.test.ts` 追加）

```ts
describe('UX v2 B2-10：视觉先落气泡（方案 §5.5）', () => {
  test('beginUserBubble 立刻上屏并返回 id；markVision 记 visionIds；随后 send({bubbleId}) 复用、meta 带 vision_frame_id', () => {
    const { transport, core } = newCore()
    const id = core.beginUserBubble('这是什么')
    core.markVision(id)
    expect(msgs(core)).toHaveLength(1)
    expect(core.store.getState().visionIds).toEqual([id])
    expect(transport.sent).toHaveLength(0) // 还没发：相机在冷启动
    core.send('这是什么', { vision_frame_id: 'f1' }, { source: 'text', bubbleId: id })
    expect(msgs(core).filter((m) => m.role === 'user')).toHaveLength(1)
    expect(transport.lastUserFrame().meta.vision_frame_id).toBe('f1')
    core.dispose()
  })
})
```

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

`store.ts`：`SessionState` 加 `/** 带过视觉抓帧的用户气泡（📷 角标）；帧本身不落端、不进记录 */ visionIds: string[]`（初始化 `[]`）；方法：
```ts
  /** 先落气泡（方案 §5.5 / HMI __bubbled 同款）：用户那句话立刻上屏，请求稍后用 send({ bubbleId }) 发 */
  beginUserBubble(text: string): string {
    const id = uid()
    this.appendMessage({ id, role: 'user', text })
    return id
  }

  markVision(id: string): void {
    this.store.setState((s) => ({ visionIds: s.visionIds.includes(id) ? s.visionIds : [...s.visionIds, id] }))
  }
```
`ChatScreen.tsx` 的 `onSend` 视觉分支整段替换：
```ts
      if (settings.visionEnabled && !visionDone && needsVisionFrame(text)) {
        activityLog.push('camera', `触发词「${text.slice(0, 12)}」`)
        // 先落气泡（方案 §5.5）：用户那句话**立刻**上屏带 📷，vision_frame_id 迟到再补进 meta——
        // 相机冷启动几百毫秒，这段时间用户自己的话不该还没出现。草稿 / S2S 转正的气泡直接复用
        const bubbleId = opts?.bubbleId ?? core.beginUserBubble(text)
        core.markVision(bubbleId)
        void captureVisionFrame(cfg.audioUrl).then((fid) =>
          core.send(text, { ...(metaExtra || {}), vision_frame_id: fid }, { ...(opts || {}), bubbleId }),
        )
        return
      }
```
`useStore` 解构加 `visionIds`、`extraData` 加它、`MessageBubble` 传 `vision={visionIds.includes(item.id)}`、`VoiceSheet` 传 `visionIds={visionIds}`。

`MessageBubble.tsx`：`BubbleProps` 加 `/** 带过视觉抓帧：📷 角标（方案 §5.5）；不做预览 */ vision?: boolean`；用户气泡文本前加 `{vision ? <Text style={{ color: p.fg3, fontSize: p.font(10), marginBottom: 2 }}>📷 看图</Text> : null}`。`VoiceSheet.tsx`：props 加 `visionIds: readonly string[]`，转写文本前 `{user && props.visionIds.includes(user.id) ? '📷 ' : ''}`。

- [ ] **步骤 4：跑绿 + `tsc`**。

- [ ] **反向验证**：`onSend` 视觉分支改回「先抓再 send」⇒ 单测层无区分（这是接线），所以真机证据是这条的判据：说「这是什么」→ `screenrecord`（`//sdcard/b2-10.mp4`）逐帧：用户气泡（带 📷）出现的帧 **早于** `looking` 白环 / 采集点出现的帧；`adb logcat -s CameraService` 的 `connect` 时间戳晚于气泡上屏帧的时间（录屏帧号 × 1/30s 对墙钟）。同一段录屏顺手补 B1 出账⑧的 `looking` 白环静态取证（抽帧 `b2-10-looking.png`）。拍完不出预览（录屏里没有任何画面帧）。

- [ ] **提交**：
```bash
git commit -m "feat(mobile): UX v2 B2-10 视觉抓帧先落气泡（📷 角标，vision_frame_id 迟到补进 meta；不做预览）" -- mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/features/chat/MessageBubble.tsx mobile/src/features/chat/VoiceSheet.tsx && git show --stat HEAD
```

### Task 11: 回声提示——「像是我自己的声音，没算数」

**Files:**
- 修改 `mobile/src/core/voice/handsFree.ts`（`onEchoDismissed?`）、`mobile/test/handsFree.test.ts`（追加 1 条）、`mobile/src/features/chat/useHandsFree.ts`（`echoAt`）、`mobile/src/features/chat/usePresence.ts`（并进 `notice`）

**为什么**：方案 §5.2 规则 5——回声判据命中时胶囊短显「像是我自己的声音，没算数」2s，把「吞掉的那句」变成可见的，否则用户以为没听见。共享判据只读（§0 第 5 条）⇒ App 能观测到的只有 FSM 已经吐出来的 `onMetric('echo_dismissed')`（`hmi/src/voiceLoop.mjs:379`，续问窗回声，**Android 无 AEC 时几乎每轮都命中的那一路**）；barge-in 那一路（`:476` `_countSelfTrigger`）没有 metric，看不见——记遗留给 hmi 侧加 `onMetric('echo_suspected')`，本任务不碰共享文件。

- [ ] **步骤 1：写失败测试**（`handsFree.test.ts` 追加）

```ts
test('B2-11 续问窗回声：FSM 吐 echo_dismissed → onEchoDismissed 收到（胶囊「像是我自己的声音，没算数」的信号源）', async () => {
  const echoes: number[] = []
  const { ctl, sent } = makeCtl({ onEchoDismissed: () => echoes.push(1) })
  await ctl.enable()
  ctl.wakeManually()
  FakeAsr.last!.cb.onFinal('今天天气怎么样') // 定稿 → THINKING（走调用方 onSend）
  expect(sent).toEqual(['今天天气怎么样'])
  ctl.ttsStart('深圳市当前阴')
  ctl.setTtsText('深圳市当前阴，气温28℃，西南风3级。')
  ctl.ttsEnd() // SPEAKING → FOLLOWUP
  vad.cb.onSpeechStart() // 续问窗内开口 → LISTENING(source=followup)
  FakeAsr.last!.cb.onFinal('深圳市的') // 真机原字：与播报的公共子序列「深圳市」=3/4=0.75 ⇒ 回声
  expect(echoes).toEqual([1])
  expect(sent).toEqual(['今天天气怎么样']) // 回声那句没上云
  expect(ctl.state).toBe('FOLLOWUP') // 窗留着（voiceLoop:377 的裁决）
})
```

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

`handsFree.ts`：`HandsFreeDeps` 加 `/** 续问窗回声被 FSM 丢弃（voiceLoop echo_dismissed）→ UI 短显提示（方案 §5.2 规则 5） */ onEchoDismissed?(): void`；构造里 `onMetric` 回调替换为：
```ts
      onMetric: (name: string) => {
        // 本地消化的三类事件：provider 不知道我们把这句判掉了，要显式让它别答
        if (this.s2s && S2S_LOCAL_HANDLED.has(name)) this.s2s.cancelTurn()
        // 回声提示只有这一路信号（barge-in 那一路的 _countSelfTrigger 没有 metric——共享文件不改，记遗留）
        if (name === 'echo_dismissed') this.deps.onEchoDismissed?.()
      },
```
`useHandsFree.ts`：`HandsFreeUi` 加 `/** 上一次回声被丢弃的时刻；0=没有 */ echoAt: number`；state `const [echoAt, setEchoAt] = useState(0)`；deps 加 `onEchoDismissed: () => setEchoAt(Date.now())`；cleanup 里 `setEchoAt(0)`；返回加 `echoAt`。

`usePresence.ts`：`notice` 那行替换为
```ts
  // 2s 提示：取消（§5.1.1）与回声（§5.2 规则 5）——取更晚的那个
  const cancelNotice = ptt?.cancelledAt ? { text: '已取消，这段话不会发给小舟', at: ptt.cancelledAt } : null
  const echoNotice = hf.echoAt ? { text: '像是我自己的声音，没算数', at: hf.echoAt } : null
  const notice = !cancelNotice ? echoNotice : !echoNotice ? cancelNotice : echoNotice.at > cancelNotice.at ? echoNotice : cancelNotice
```

- [ ] **步骤 4：跑绿 + `tsc`**。

- [ ] **反向验证**：`onMetric` 里去掉 `echo_dismissed` 那行 ⇒ 只红新用例；真机（第 3 批收口）：**读数要带 APK 的构建时间与有无 AEC**（`adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime`；B1 计划 §5 第 5 条：`96a6830` 的 `VoiceCommunication` 预设只在下一次原生构建后才进 APK，B2 不重建 ⇒ 装的是哪次构建决定回声出不出现）。无 AEC 的包：天气答完 → 胶囊「像是我自己的声音，没算数」2s（`b2-11-echo.png`）；有 AEC 的包：不出现是**预期**，写「未触发（AEC 在场）」不写 ✅。

- [ ] **提交**：
```bash
git commit -m "feat(mobile): UX v2 B2-11 回声提示——FSM echo_dismissed → 胶囊「像是我自己的声音，没算数」2s（共享判据不改）" -- mobile/src/core/voice/handsFree.ts mobile/test/handsFree.test.ts mobile/src/features/chat/useHandsFree.ts mobile/src/features/chat/usePresence.ts && git show --stat HEAD
```
### Task 12: 播报三档（总是 / 静音 / 自动）——替换 `ttsEnabled && autoplay`

**Files:**
- 修改 `mobile/src/core/settings/store.ts`（`SpeakPolicy` + `speakPolicy` + `speakAllowed()` + 存量迁移）、`mobile/src/core/voice/speech.ts`（`begin` 第三参、裁决只在 `begin`）、`mobile/src/core/session/store.ts`（`SpeechSink.begin(…, voice)`；`dispatch` 传 `source !== 'text'`）、`mobile/src/features/settings/SettingsScreen.tsx`（两开关 → 三档）
- 新建 `mobile/test/speakPolicy.test.ts`；修改 `mobile/test/sessionStore.test.ts`（追加 1 条）

**为什么**：方案 §5.2 规则 8 / Q11——`总是 / 静音 / 自动`，**自动 = 语音提问才播报、打字只显示文字**，默认「自动」；替换现在 `ttsEnabled && autoplay` 两个近义开关（`speech.ts:80-83`、`SettingsScreen.tsx:271-290`，两个同时为真才出声，用户分不清哪个是哪个）。「这一轮是不是语音提问」的事实 T3 已经进了记录（`turnMeta.source`），播报端口在 `begin` 时拿到它。迁移：旧值任一为 false → 静音，否则 → 自动（旧行为「打字也播报」= 「总是」，泓舟要保留就把默认改一行）。

- [ ] **步骤 1：写失败测试**

`mobile/test/speakPolicy.test.ts`：

```ts
// mobile/test/speakPolicy.test.ts
// 播报三档（方案 §5.2 规则 8，Q11）：判据 speakAllowed 只此一处；存量迁移规则；默认「自动」。
import { DEFAULT_APP_SETTINGS, mergeStoredSettings, speakAllowed } from '@/core/settings/store'

test.each([
  ['always', true, true],
  ['always', false, true],
  ['auto', true, true],
  ['auto', false, false], // 自动：打字提问不出声
  ['silent', true, false],
  ['silent', false, false],
] as const)('policy=%s × 语音提问=%s → 播报=%s', (policy, voice, allowed) => {
  expect(speakAllowed(policy, voice)).toBe(allowed)
})

test('默认「自动」（Q11）', () => {
  expect(DEFAULT_APP_SETTINGS.speakPolicy).toBe('auto')
})

test('存量迁移：ttsEnabled=false 或 autoplay=false → silent；两者都真 → auto；已有 speakPolicy 原样；旧键不留', () => {
  expect(mergeStoredSettings(JSON.stringify({ ttsEnabled: false, autoplay: true })).speakPolicy).toBe('silent')
  expect(mergeStoredSettings(JSON.stringify({ ttsEnabled: true, autoplay: false })).speakPolicy).toBe('silent')
  expect(mergeStoredSettings(JSON.stringify({ ttsEnabled: true, autoplay: true })).speakPolicy).toBe('auto')
  expect(mergeStoredSettings(JSON.stringify({ speakPolicy: 'always', ttsEnabled: false })).speakPolicy).toBe('always')
  expect('ttsEnabled' in mergeStoredSettings(JSON.stringify({ ttsEnabled: true }))).toBe(false)
  expect('autoplay' in mergeStoredSettings(JSON.stringify({ autoplay: true }))).toBe(false)
})
```

`mobile/test/sessionStore.test.ts` 追加：

```ts
describe('UX v2 B2-12：播报端口拿到「这一轮是不是语音发起」', () => {
  test('dispatch 把 source!==text 交给 SpeechSink.begin 第三参', () => {
    const calls: string[] = []
    const speech: SpeechSink = {
      begin: (_b, e, voice) => calls.push(`begin:${e}:${String(voice)}`),
      delta() {},
      finish() {},
      stop() {},
    }
    const { core } = newCore({ speech })
    core.send('天气')
    core.send('天气', undefined, { source: 'ptt' })
    expect(calls).toEqual(['begin::false', 'begin::true'])
    core.dispose()
  })
})
```

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

`mobile/src/core/settings/store.ts`：
- 类型：`export type SpeakPolicy = 'always' | 'silent' | 'auto'`；`AppSettings` 删 `ttsEnabled` / `autoplay`，加
```ts
  /** 播报三档（方案 §5.2 规则 8，Q11）：总是 / 静音 / 自动=语音提问才播报、打字只显示文字。
   *  替换 B1 之前的 ttsEnabled && autoplay 两个近义开关（两个同时为真才出声，用户分不清哪个是哪个） */
  speakPolicy: SpeakPolicy
```
- `DEFAULT_APP_SETTINGS` 删 `ttsEnabled: DEFAULT_SETTINGS.ttsEnabled,` / `autoplay: DEFAULT_SETTINGS.autoplay,` 两行，加 `speakPolicy: 'auto',`；
- 判据函数：
```ts
/** 这一轮要不要出声——判据只此一处（SpeechController.begin 读它） */
export function speakAllowed(policy: SpeakPolicy, voice: boolean): boolean {
  return policy === 'always' || (policy === 'auto' && voice)
}
```
- `mergeStoredSettings` 整段替换：
```ts
/** 存量合并（同 hmi settings.load()）：合并默认值向前兼容新增字段；agents 深合并；
 *  播报三档迁移（方案 §5.2 规则 8）：旧值任一为 false → 静音；否则 → 自动（Q11：不保留「打字也播报」）。 */
export function mergeStoredSettings(raw: string | null): AppSettings {
  if (!raw) return DEFAULT_APP_SETTINGS
  try {
    const parsed = JSON.parse(raw) as Partial<AppSettings> & { ttsEnabled?: boolean; autoplay?: boolean }
    const { ttsEnabled, autoplay, ...rest } = parsed
    const speakPolicy: SpeakPolicy =
      rest.speakPolicy ?? (ttsEnabled === false || autoplay === false ? 'silent' : DEFAULT_APP_SETTINGS.speakPolicy)
    return {
      ...DEFAULT_APP_SETTINGS,
      ...rest,
      speakPolicy,
      agents: { ...DEFAULT_APP_SETTINGS.agents, ...(parsed.agents || {}) },
    }
  } catch {
    return DEFAULT_APP_SETTINGS
  }
}
```

`mobile/src/core/session/store.ts`：`SpeechSink.begin` 签名改 `begin(bubbleId: string, emotion: string, voice?: boolean): void`（注释加「voice=这一轮是语音发起的（播报三档的「自动」读它）」）；`dispatch` 里 `this.speech.begin(pendingId, this.store.getState().lastEmotion)` 改为 `this.speech.begin(pendingId, this.store.getState().lastEmotion, source !== 'text')`。

`mobile/src/core/voice/speech.ts`：删 `private get enabled()`；类字段加 `/** begin 时按三档裁决的结果；finish 尊重它（同一轮不许 begin 说播、finish 又不播） */ private allowed = false`；import 加 `speakAllowed`；
```ts
  begin(bubbleId: string, emotion: string, voice = false): void {
    this.allowed = speakAllowed(settingsStore.getState().settings.speakPolicy, voice)
    if (!this.allowed) {
      this.stop()
      return
    }
    …（其余不变）
```
```ts
  finish(bubbleId: string, text: string): void {
    // 三档在 begin 裁过；这里再看一眼「静音」——用户可能在这一轮中途把它关了
    if (!this.allowed || settingsStore.getState().settings.speakPolicy === 'silent' || !text) return
    …（其余不变）
```

`mobile/src/features/settings/SettingsScreen.tsx`：「语音播报」分区的两个 `SwitchRow`（播报回答 / 自动播报）整段替换为：
```tsx
        <ChoiceRow
          p={p}
          label="播报"
          value={settings.speakPolicy}
          options={[
            { v: 'auto' as const, label: '自动' },
            { v: 'always' as const, label: '总是' },
            { v: 'silent' as const, label: '静音' },
          ]}
          onPick={(speakPolicy) => {
            set({ speakPolicy })
            if (speakPolicy === 'silent') speechController().stop() // 关掉要立刻停当前这段
          }}
        />
        <Text style={{ color: p.fg3, fontSize: p.font(11), lineHeight: p.font(17) }}>
          自动：用语音问才播报，打字只显示文字（默认）。总是：打字也播报。静音：完全不出声（试听仍可用）。
        </Text>
```

- [ ] **步骤 4：跑绿 + `tsc`**——`tsc` 会把所有还在读 `ttsEnabled` / `autoplay` 的地方指出来（`speech.ts`、`SettingsScreen.tsx`，以及 `voiceTts.test.ts` 若引用了它们：改成 `speakPolicy`，这是判据变更不是测试让步，写进 §6.4）。

- [ ] **反向验证**：`speakAllowed` 的 `auto` 分支改成恒 true ⇒ 只红 `auto × false`；迁移里 `'silent'` 与默认对调 ⇒ 只红迁移用例前两行。真机（第 4 批收口）：「自动」档打字问天气 → 不出声（`speaking` 从未变 true，看轨迹页）；轻点说「天气」→ 出声；切「静音」→ 播报中立刻停；切「总是」→ 打字也出声（§11.2 B2 最后一条）。

- [ ] **提交**：
```bash
git add -- mobile/test/speakPolicy.test.ts && git commit -m "feat(mobile): UX v2 B2-12 播报三档 总是/静音/自动（自动=语音提问才播报），替换 ttsEnabled&&autoplay，存量迁移" -- mobile/test/speakPolicy.test.ts mobile/src/core/settings/store.ts mobile/src/core/voice/speech.ts mobile/src/core/session/store.ts mobile/src/features/settings/SettingsScreen.tsx mobile/test/sessionStore.test.ts && git show --stat HEAD
```

### Task 13: 执行回执（Execution Receipt）

**Files:**
- 新建 `mobile/src/core/session/receipt.ts`、`mobile/src/features/chat/ExecutionReceipt.tsx`、`mobile/test/receipt.test.ts`
- 修改 `mobile/src/core/session/actionSummary.ts`（抽出 `precedingUserUtterance`）、`mobile/src/core/session/store.ts`（`TurnMeta` 三字段 + `confirmLog`）、`mobile/test/sessionStore.test.ts`、`mobile/src/features/chat/MessageBubble.tsx`（「已执行 …」行 → 回执）、`mobile/src/features/chat/ChatScreen.tsx`（传 `receipt`）

**为什么**：方案 §5.3.2——项目最有价值的不是思考动画，是 VAL 的确定性执行，但今天用户只看到一行灰字「已执行 media.control」。回执四行**全部来自已有数据，零后端改动**：已理解（紧邻上一条用户原话——与 T1 同一份判据）/ 目标（`vehState.vehicle_id`，没有就「当前车辆」）/ 确认（本端台账：`confirmReply` 的时刻与方式）/ 执行（action 帧 + final 时刻）。信息服务 = `_prov` 展开（card_group 取主卡的，与 T8 同一份主卡判据）。「安全检查」栏**今天拿不到**（VAL 只在拒绝时说话）——留位不渲染，随 Q16 来。默认折叠成一行「已执行 · 展开回执」；行车档只播不展 → B4。

- [ ] **步骤 1：写失败测试**

`mobile/test/receipt.test.ts`：

```ts
// mobile/test/receipt.test.ts
// 执行回执（方案 §5.3.2）：字段全部来自已有数据；车控四行 / 信息服务 _prov 展开；两者都没有 → null。
import type { Msg } from '@shared/types.ts'

import { buildReceipt, provOf } from '@/core/session/receipt'

const u = (id: string, text: string): Msg => ({ id, role: 'user', text })
const a = (id: string, text: string, extra: Partial<Msg> = {}): Msg => ({ id, role: 'assistant', text, ...extra })

test('车控回执：已理解=紧邻上一条用户原话（跳过「确认」）；目标=vehicle_id；确认来自本端台账；执行=action 类型 + final 时刻', () => {
  const msgs = [
    u('u1', '打开后备箱'),
    a('a1', '这项操作可能影响车辆安全，请确认是否继续。', { needConfirm: true, operationId: 'op1' }),
    u('u2', '确认'),
    a('a2', '已打开', { actions: [{ type: 'vehicle.control' }] }),
  ]
  const r = buildReceipt({
    messages: msgs,
    assistant: msgs[3],
    turnMeta: { a2: { sentAt: 1_000, finalAt: 2_000, source: 'text', operationId: 'op1' } },
    confirmLog: { op1: { reply: '确认', at: 1_500 } },
    vehicleId: 'V-001',
  })
  expect(r).toEqual({
    kind: 'action',
    understood: '打开后备箱',
    target: 'V-001',
    confirm: { reply: '确认', at: 1_500 },
    executed: { ok: true, at: 2_000, types: ['vehicle.control'] },
  })
})

test('没有 vehicle_id → 「当前车辆」；没有确认记录 → confirm=null；error → ok=false；没有 turnMeta → at=null', () => {
  const msgs = [u('u1', '打开车窗'), a('a1', '出错了', { error: true, actions: [{ type: 'vehicle.control' }] })]
  const r = buildReceipt({ messages: msgs, assistant: msgs[1], turnMeta: {}, confirmLog: {} })
  expect(r).toEqual({
    kind: 'action',
    understood: '打开车窗',
    target: '当前车辆',
    confirm: null,
    executed: { ok: false, at: null, types: ['vehicle.control'] },
  })
})

test('信息回执 = _prov 展开；card_group 取主卡的 _prov（与 T8 同一份主卡判据）；无 _prov 无 actions → null', () => {
  const card = {
    type: 'card_group',
    items: [
      { type: 'news_digest', display_priority: 2, _prov: { mode: 'cached', vendor: 'x' } },
      { type: 'weather', display_priority: 0, _prov: { mode: 'real', vendor: '高德', fetched_at: '2026-08-30T09:34:00Z' } },
    ],
  } as unknown as Msg['uiCard']
  expect(provOf(card)?.vendor).toBe('高德')
  const r = buildReceipt({
    messages: [],
    assistant: a('a1', '晴', { uiCard: card }),
    turnMeta: { a1: { sentAt: 1, source: 'ptt', withLocation: true } },
    confirmLog: {},
  })
  expect(r).toEqual({ kind: 'info', vendor: '高德', fetchedAt: '2026-08-30T09:34:00Z', located: true, mode: 'real', note: '' })
  expect(buildReceipt({ messages: [], assistant: a('a2', '你好'), turnMeta: {}, confirmLog: {} })).toBeNull()
})
```

`mobile/test/sessionStore.test.ts` 追加：

```ts
describe('UX v2 B2-13：回执的账本侧（方案 §5.3.2）', () => {
  test('confirmReply 记 confirmLog[operationId]；该轮 turnMeta 记 operationId；final 到达记 finalAt', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '请确认', need_confirm: true, operation_id: 'op1' })
    core.confirmReply('确认', 'op1')
    expect(core.store.getState().confirmLog.op1.reply).toBe('确认')
    expect(core.store.getState().confirmLog.op1.at).toBeGreaterThan(0)
    const a2 = assistants(core)[1]
    expect(core.store.getState().turnMeta[a2.id].operationId).toBe('op1')
    expect(core.store.getState().turnMeta[a2.id].finalAt).toBeUndefined()
    const rid2 = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid2, speech: '已打开', actions: [{ type: 'vehicle.control' }] })
    expect(core.store.getState().turnMeta[a2.id].finalAt).toBeGreaterThan(0)
    core.dispose()
  })

  test('带坐标发出的轮 turnMeta.withLocation=true（回执「定位 当前位置」）', async () => {
    const { core } = newCore({ location: fakeLocation(true, { lat: '22.5', lng: '113.9' }) })
    core.send('附近有什么好吃的')
    await flush()
    expect(core.store.getState().turnMeta[assistants(core)[0].id].withLocation).toBe(true)
    core.dispose()
  })
})
```

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

`mobile/src/core/session/actionSummary.ts`——把循环抽成可复用函数（`actionSummary.test` 不变仍绿）：
```ts
/** 从 messages[before] 往前找最近一条用户原话（跳过台账回复「确认/取消」）；空串=没有 */
export function precedingUserUtterance(messages: readonly Msg[], before: number): string {
  for (let i = Math.min(before, messages.length) - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.role !== 'user') continue
    const text = m.text.replace(/\s+/g, ' ').trim()
    if (!text || CONFIRM_REPLIES.has(text)) continue
    return text.slice(0, SUMMARY_MAX)
  }
  return ''
}

/** 紧邻的上一条用户原话；找不到返回空串（兜底文案由调用方决定） */
export function actionSummary(messages: readonly Msg[], operationId: string): string {
  const at = messages.findIndex((m) => m.role === 'assistant' && m.operationId === operationId)
  return at < 0 ? '' : precedingUserUtterance(messages, at)
}
```

`mobile/src/core/session/store.ts`：
```ts
export interface TurnMeta {
  sentAt: number
  source: TurnSource
  /** final 到达时刻（回执「执行 · 00:42」）；没到过就没有 */
  finalAt?: number
  /** 这一轮是对哪条挂起的回复（confirmReply 派发的那轮；回执「确认」行据它找 confirmLog） */
  operationId?: string
  /** 发出时带了坐标（回执「定位 当前位置」） */
  withLocation?: boolean
}

/** 本端台账里的一次确认 / 取消（回执「你在手机端点了「确认」 00:41」） */
export interface ConfirmEntry {
  reply: '确认' | '取消'
  at: number
}
```
`SessionState` 加 `confirmLog: Record<string, ConfirmEntry>`（初始化 `{}`）；`confirmReply` 的 `if (operationId) { … }` 里加 `this.store.setState((s) => ({ confirmLog: { ...s.confirmLog, [operationId]: { reply, at: Date.now() } } }))`；`dispatch` 里 `turnMeta` 那行改为：
```ts
    this.store.setState((s) => ({
      turnMeta: {
        ...s.turnMeta,
        [pendingId]: {
          sentAt: Date.now(),
          source,
          ...(operationId ? { operationId } : {}),
          ...(locationMeta && Object.keys(locationMeta).length ? { withLocation: true } : {}),
        },
      },
    }))
```
`handleFrame` 的 `final` 分支在 `this.clearWatchdog(id)` 之后加：
```ts
      if (id) {
        this.store.setState((s) =>
          s.turnMeta[id] ? { turnMeta: { ...s.turnMeta, [id]: { ...s.turnMeta[id], finalAt: Date.now() } } } : {},
        )
      }
```

`mobile/src/core/session/receipt.ts`：

```ts
// mobile/src/core/session/receipt.ts
// 执行回执（方案 §5.3.2）：字段**全部来自已有数据，零后端改动**。
//  车控：已理解（紧邻上一条用户原话，与 Dock 标题同一份判据）/ 目标（vehState.vehicle_id，没有就「当前车辆」）
//       / 确认（本端台账 confirmLog[operationId]）/ 执行（action 帧 + final 时刻）。
//       「安全检查：车辆静止，允许执行」今天拿不到（VAL 只在拒绝时说话）——**留位不渲染**，随 Q16 来。
//  信息服务：_prov 展开（数据源 · 更新 · 定位 · 状态）；card_group 取主卡的 _prov（与 T8 同一份主卡判据）。
// 零 RN import。
import type { Msg, Provenance } from '@shared/types.ts'

import { splitCardGroup } from '../cards/cardGroup'
import { precedingUserUtterance } from './actionSummary'
import type { ConfirmEntry, TurnMeta } from './store'

export interface ActionReceipt {
  kind: 'action'
  understood: string
  target: string
  confirm: ConfirmEntry | null
  executed: { ok: boolean; at: number | null; types: string[] }
}

export interface InfoReceipt {
  kind: 'info'
  vendor: string
  fetchedAt: string
  located: boolean
  mode: Provenance['mode']
  note: string
}

export type Receipt = ActionReceipt | InfoReceipt

/** 卡的 _prov；card_group 取主卡的（同一份主卡判据）；没有就 null */
export function provOf(card: unknown): Provenance | null {
  const c = card as { type?: string; _prov?: Provenance; items?: unknown[] } | null | undefined
  if (!c) return null
  if (c.type === 'card_group') return provOf(splitCardGroup(c.items ?? []).main)
  return c._prov?.mode ? c._prov : null
}

export function buildReceipt(args: {
  messages: readonly Msg[]
  assistant: Msg
  turnMeta: Record<string, TurnMeta>
  confirmLog: Record<string, ConfirmEntry>
  vehicleId?: string
}): Receipt | null {
  const { messages, assistant, turnMeta, confirmLog } = args
  const meta = turnMeta[assistant.id]
  if (assistant.actions?.length) {
    const at = messages.findIndex((m) => m.id === assistant.id)
    const opId = meta?.operationId
    return {
      kind: 'action',
      understood: at >= 0 ? precedingUserUtterance(messages, at) : '',
      target: args.vehicleId || '当前车辆',
      confirm: opId && confirmLog[opId] ? confirmLog[opId] : null,
      executed: { ok: !assistant.error, at: meta?.finalAt ?? null, types: assistant.actions.map((x) => x.type) },
    }
  }
  const prov = provOf(assistant.uiCard)
  if (prov) {
    return {
      kind: 'info',
      vendor: prov.vendor ?? '',
      fetchedAt: prov.fetched_at ?? '',
      located: !!meta?.withLocation,
      mode: prov.mode,
      note: prov.note ?? '',
    }
  }
  return null
}
```

`mobile/src/features/chat/ExecutionReceipt.tsx`：

```tsx
// mobile/src/features/chat/ExecutionReceipt.tsx
// 回执组件（方案 §5.3.2）：默认折叠成一行「已执行 · 展开回执」；展开四行。判据在 core/session/receipt.ts。
// 行车档「只播不展」留 B4。「安全检查」栏留位不渲染（Q16）。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import type { InfoReceipt, Receipt } from '@/core/session/receipt'
import { KV } from '@/features/cards/parts'
import type { Palette } from '@/ui/theme'

function hhmm(ms: number | null): string {
  if (!ms) return ''
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

const MODE_LABEL: Record<InfoReceipt['mode'], string> = { real: '实时', cached: '缓存', degraded: '降级', mock: '模拟' }

export function ExecutionReceipt({ p, receipt }: { p: Palette; receipt: Receipt }) {
  const [open, setOpen] = useState(false)
  const head = receipt.kind === 'action' ? (receipt.executed.ok ? '已执行' : '执行失败') : '数据来源'
  return (
    <View style={{ gap: 4 }}>
      <Pressable testID="receipt-toggle" accessibilityRole="button" onPress={() => setOpen((o) => !o)} style={{ minHeight: 32, justifyContent: 'center' }}>
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
          {head} · {open ? '收起回执' : '展开回执'}
        </Text>
      </Pressable>
      {open ? (
        receipt.kind === 'action' ? (
          <View style={{ gap: 2 }}>
            <KV p={p} k="已理解" v={receipt.understood || receipt.executed.types.join('、')} />
            <KV p={p} k="目标" v={receipt.target} />
            <KV p={p} k="确认" v={receipt.confirm ? `你在手机端点了「${receipt.confirm.reply}」 ${hhmm(receipt.confirm.at)}` : '无需确认'} />
            <KV p={p} k="执行" v={`${receipt.executed.ok ? '成功' : '失败'}${receipt.executed.at ? ' · ' + hhmm(receipt.executed.at) : ''} · ${receipt.executed.types.join('、')}`} />
          </View>
        ) : (
          <View style={{ gap: 2 }}>
            <KV p={p} k="数据源" v={receipt.vendor || '未知'} />
            <KV p={p} k="更新" v={receipt.fetchedAt ? receipt.fetchedAt.slice(11, 16) : ''} />
            <KV p={p} k="定位" v={receipt.located ? '当前位置' : '未使用定位'} />
            <KV p={p} k="状态" v={`${MODE_LABEL[receipt.mode]}${receipt.note ? ' · ' + receipt.note : ''}`} />
          </View>
        )
      ) : null}
    </View>
  )
}
```

`mobile/src/features/chat/MessageBubble.tsx`：`BubbleProps` 加 `/** 执行回执（core/session/receipt.ts 算好传进来；null=这条没有可回执的事） */ receipt?: Receipt | null`；把 `{(msg.actions || []).length ? (<Text …>已执行 …</Text>) : null}` 整段替换为 `{receipt ? <ExecutionReceipt p={p} receipt={receipt} /> : null}`。

`mobile/src/features/chat/ChatScreen.tsx`：`useStore(core.store)` 解构加 `turnMeta, confirmLog`；`extraData` 加它们；`renderItem` 里：
```tsx
                receipt={
                  item.role === 'assistant'
                    ? buildReceipt({ messages, assistant: item, turnMeta, confirmLog, vehicleId: String(vehState.vehicle_id ?? '') })
                    : null
                }
```

- [ ] **步骤 4：跑绿 + `tsc`**。

- [ ] **反向验证**：`buildReceipt` 的 `understood` 改取 `assistant.text` ⇒ 只红第一条；`provOf` 对 card_group 取 `items[0]` ⇒ 只红第三条（主卡是第二张）；`dispatch` 不记 `operationId` ⇒ 只红账本侧第一条。真机（第 4 批收口）：「打开后备箱」→ 确认 → 回执展开四行「已理解 打开后备箱 / 目标 当前车辆 / 确认 你在手机端点了「确认」 hh:mm / 执行 成功 · hh:mm · vehicle.control」（`b2-13-receipt.png`）；「深圳天气」→ 「数据来源 · 展开回执」四行（数据源 / 更新 / 定位 / 状态 实时）。

- [ ] **提交**：
```bash
git add -- mobile/src/core/session/receipt.ts mobile/src/features/chat/ExecutionReceipt.tsx mobile/test/receipt.test.ts && git commit -m "feat(mobile): UX v2 B2-13 执行回执——车控四行 / 信息服务 _prov 展开，字段全来自已有数据，默认折叠" -- mobile/src/core/session/receipt.ts mobile/src/features/chat/ExecutionReceipt.tsx mobile/test/receipt.test.ts mobile/src/core/session/actionSummary.ts mobile/src/core/session/store.ts mobile/test/sessionStore.test.ts mobile/src/features/chat/MessageBubble.tsx mobile/src/features/chat/ChatScreen.tsx && git show --stat HEAD
```

### Task 14: 在场轨迹页（🔁-1）+ `activityLog.list()` 的第一个消费方（D8）

**Files:**
- 新建 `mobile/src/core/presence/presenceTrail.ts`、`mobile/src/app/presence-trail.tsx`、`mobile/test/presenceTrail.test.ts`
- 修改 `mobile/src/features/chat/usePresence.ts`（每次派生后 `record`）、`mobile/src/features/chat/useHandsFree.ts`（FSM 回调时刻 `mark`）、`mobile/src/features/chat/ChatScreen.tsx`（位置授权同意 → `activityLog.push('location', …)`）、`mobile/src/app/_layout.tsx`（路由）、`mobile/src/features/settings/SettingsScreen.tsx`（调试入口）

**为什么**：方案 §11.5（v2.2 🔁-1）——B1 落的是**采集激活日志**（`activityLog`：麦为什么开了）；方案要的那份 **`PresenceSnapshot` 变化轨迹 + 调试屏「在场轨迹」页**今天不在任何批次里，评审 §6 ④ 让 B2 落（语音层会大量制造在场变化，正是要看轨迹的时候）。D8：`activityLog.list()` 与 `ActivitySource='location'` 零消费方 / 零产出方——本任务给 `list()` 一个消费方（轨迹页）、给 `location` 一个产出方（位置授权同意）。轨迹还承担 §11.4「首反馈时延」的取数：`mark('fsm:LISTENING')` 是 FSM 回调时刻（≈KWS 命中），随后第一条 `primary=listening` 的快照条目是屏上变化时刻，两者之差就是读数——不加打点库，不加依赖。内存、20 条、不上传。

- [ ] **步骤 1：写失败测试**

```ts
// mobile/test/presenceTrail.test.ts
// 在场轨迹（方案 §11.5）：只在轴变化时记、每秒 tick 不刷屏；记变化的轴与变化的输入；环形 20；mark 打点。
import { derivePresence, type PresenceInput } from '@/core/presence/presence'
import { PresenceTrail } from '@/core/presence/presenceTrail'

const NOW = 5_000_000
function base(over: Partial<PresenceInput> = {}): PresenceInput {
  return {
    now: NOW, connStatus: 'open', connChangedAt: NOW - 60_000,
    hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', hfFsmChangedAt: NOW - 1000, ptt: 'idle', partial: '',
    turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
    speaking: false, pendingOps: [], pendingLocation: false, voicePipeline: 'classic',
    visionCapturing: false, queued: 0, lastError: null, degradations: [], driving: false,
    identity: 'handheld', user: 'u1',
    ...over,
  }
}

test('只在轴变化时记；同快照每秒 tick 不刷屏；记下变化的轴与变化的输入', () => {
  let t = 0
  const trail = new PresenceTrail(20, () => (t += 1))
  const i1 = base()
  trail.record(i1, derivePresence(i1))
  expect(trail.list()).toHaveLength(1)
  const tick = base({ now: NOW + 1000 })
  trail.record(tick, derivePresence(tick)) // 只有 now 变了：不记
  expect(trail.list()).toHaveLength(1)
  const i2 = base({ now: NOW + 2000, ptt: 'recording' })
  trail.record(i2, derivePresence(i2))
  const top = trail.list()[0]
  expect(top.kind).toBe('snapshot')
  if (top.kind === 'snapshot') {
    expect(top.primary).toBe('listening')
    expect(top.input).toBe('voice-sheet')
    expect(top.changedAxes).toEqual(expect.arrayContaining(['capture', 'primary', 'input', 'capsule', 'privacy.mic']))
    expect(top.changedInputs).toEqual(['ptt'])
  }
})

test('环形 20 条，最新在前', () => {
  const trail = new PresenceTrail(20, () => 1)
  for (let k = 0; k < 25; k += 1) {
    const i = base({ ptt: k % 2 ? 'recording' : 'idle' })
    trail.record(i, derivePresence(i))
  }
  expect(trail.list()).toHaveLength(20)
})

test('mark：外设时刻打点（首反馈时延 = mark 到下一条 listening 快照的时间差）', () => {
  let t = 100
  const trail = new PresenceTrail(20, () => (t += 30))
  trail.mark('fsm:LISTENING')
  const i = base({ hfEnabled: true, hfUsable: true, hfFsm: 'LISTENING' })
  trail.record(i, derivePresence(i))
  const [snap, mark] = trail.list()
  expect(mark).toEqual({ kind: 'mark', at: 130, label: 'fsm:LISTENING' })
  expect(snap.kind === 'snapshot' && snap.primary).toBe('listening')
  expect(snap.at - mark.at).toBe(30)
})

test('clear 清空并复位「上一次」：清空后第一条快照重新算作全轴变化', () => {
  const trail = new PresenceTrail(20, () => 1)
  const i = base()
  trail.record(i, derivePresence(i))
  trail.clear()
  expect(trail.list()).toHaveLength(0)
  trail.record(i, derivePresence(i))
  expect(trail.list()).toHaveLength(1)
})
```

- [ ] **步骤 2：跑红**。

- [ ] **步骤 3：实现**

```ts
// mobile/src/core/presence/presenceTrail.ts
// 在场轨迹（方案 §11.5，v2.2 🔁-1）：20 条环形，记 PresenceSnapshot **变化的轴** + 变化的输入摘要 + 时间戳。
// 内存、不上传、不持久化。与 activityLog（采集激活）是两件事：那份答「麦为什么开了」，这份答「光球为什么变了」。
// mark()：外设时刻打点（FSM 换态的回调时刻）——§11.4「首反馈时延」= mark 到相应快照条目的时间差。
// 零 RN import；jest 直接跑。
import type { PresenceInput, PresenceSnapshot } from './presence'

export type TrailEntry =
  | {
      kind: 'snapshot'
      at: number
      changedAxes: string[]
      changedInputs: string[]
      primary: PresenceSnapshot['primary']
      input: PresenceSnapshot['input']
      capsule: string
    }
  | { kind: 'mark'; at: number; label: string }

/** 轴的投影：投影相同即「没变」（每秒 tick 只改 now，不在这里） */
const AXES: Array<[string, (s: PresenceSnapshot) => string]> = [
  ['transport', (s) => s.transport],
  ['capture', (s) => s.capture],
  ['agent', (s) => s.agent],
  ['commitment', (s) => s.commitment.map((c) => `${c.kind}:${c.id}`).join(',')],
  ['privacy.mic', (s) => s.privacy.mic],
  ['privacy.camera', (s) => s.privacy.camera],
  ['degradation', (s) => s.degradation.map((d) => d.kind).join(',')],
  ['primary', (s) => s.primary],
  ['input', (s) => s.input],
  ['sheetDetent', (s) => String(s.sheetDetent)],
  ['capsule', (s) => s.capsule?.text ?? ''],
]

/** 输入的投影：答「是哪个输入变了」 */
const INPUTS: Array<[string, (i: PresenceInput) => string]> = [
  ['connStatus', (i) => i.connStatus],
  ['hfFsm', (i) => i.hfFsm],
  ['ptt', (i) => i.ptt],
  ['partial', (i) => (i.partial ? 'yes' : '')],
  ['turn', (i) => `${i.turn.pending ? 'p' : ''}${i.turn.streaming ? 's' : ''}${i.turn.processActive ? 'x' : ''}`],
  ['speaking', (i) => String(i.speaking)],
  ['pendingOps', (i) => String(i.pendingOps.length)],
  ['pendingLocation', (i) => String(i.pendingLocation)],
  ['queued', (i) => String(i.queued)],
  ['visionCapturing', (i) => String(i.visionCapturing)],
  ['lastError', (i) => (i.lastError ? String(i.lastError.at) : '')],
  ['degradations', (i) => i.degradations.map((d) => d.kind).join(',')],
  ['voice', (i) => (i.voice ? `${i.voice.turnSource}/${i.voice.override ?? '-'}/${i.voice.answer ? 'a' : ''}${i.voice.card ? 'c' : ''}` : '')],
  ['notice', (i) => (i.notice ? String(i.notice.at) : '')],
]

export class PresenceTrail {
  private items: TrailEntry[] = []
  private prevSnap: PresenceSnapshot | null = null
  private prevInput: PresenceInput | null = null
  private readonly subs = new Set<() => void>()

  constructor(
    private readonly capacity = 20,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  /** 每次派生后喂一次；轴没变就不记（渲染期调用是幂等的——同一份输入再喂一次什么都不发生） */
  record(input: PresenceInput, snap: PresenceSnapshot): void {
    const prevSnap = this.prevSnap
    const prevInput = this.prevInput
    const changedAxes = AXES.filter(([, f]) => !prevSnap || f(prevSnap) !== f(snap)).map(([k]) => k)
    const changedInputs = INPUTS.filter(([, f]) => !prevInput || f(prevInput) !== f(input)).map(([k]) => k)
    this.prevSnap = snap
    this.prevInput = input
    if (!changedAxes.length) return
    this.push({ kind: 'snapshot', at: this.clock(), changedAxes, changedInputs, primary: snap.primary, input: snap.input, capsule: snap.capsule?.text ?? '' })
  }

  mark(label: string): void {
    this.push({ kind: 'mark', at: this.clock(), label })
  }

  /** 最新在前 */
  list(): TrailEntry[] {
    return this.items.slice()
  }

  clear(): void {
    this.items = []
    this.prevSnap = null
    this.prevInput = null
    this.notify()
  }

  subscribe(fn: () => void): () => void {
    this.subs.add(fn)
    return () => {
      this.subs.delete(fn)
    }
  }

  private push(e: TrailEntry): void {
    this.items = [e, ...this.items].slice(0, this.capacity)
    this.notify()
  }

  private notify(): void {
    for (const fn of this.subs) fn()
  }
}

/** App 级单例（usePresence 写、轨迹页读、useHandsFree 打点） */
export const presenceTrail = new PresenceTrail()
```

`mobile/src/app/presence-trail.tsx`：

```tsx
// mobile/src/app/presence-trail.tsx
// 调试屏「在场轨迹」（方案 §11.5，v2.2 🔁-1）：PresenceSnapshot 变化轨迹（20 条环形）+ 采集激活日志
// （activityLog.list() 的第一个消费方，评审 D8）。dev 取证入口，不进主链路；不上传。
import * as Clipboard from 'expo-clipboard'
import { useEffect, useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { activityLog } from '@/core/presence/activityLog'
import { presenceTrail, type TrailEntry } from '@/core/presence/presenceTrail'
import { settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'

function hms(ms: number): string {
  const d = new Date(ms)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, '0')}`
}

function line(e: TrailEntry): string {
  if (e.kind === 'mark') return `${hms(e.at)} ◇ ${e.label}`
  return `${hms(e.at)} ${e.primary} · ${e.input}${e.capsule ? ` · 「${e.capsule}」` : ''}\n   轴 ${e.changedAxes.join(',')}\n   输入 ${e.changedInputs.join(',') || '—'}`
}

export default function PresenceTrailScreen() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const [, force] = useState(0)
  useEffect(() => presenceTrail.subscribe(() => force((n) => n + 1)), [])
  useEffect(() => activityLog.subscribe(() => force((n) => n + 1)), [])
  const trail = presenceTrail.list()
  const acts = activityLog.list()
  const btn = (label: string, run: () => void) => (
    <Pressable accessibilityRole="button" onPress={run} style={{ minHeight: 44, justifyContent: 'center', paddingHorizontal: 12, borderRadius: 12, borderWidth: 1, borderColor: p.fill2, backgroundColor: p.fill }}>
      <Text style={{ color: p.fg1, fontSize: p.font(13) }}>{label}</Text>
    </Pressable>
  )
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <View style={{ flexDirection: 'row', gap: 8, padding: 12 }}>
        {btn('复制 JSON', () => void Clipboard.setStringAsync(JSON.stringify({ trail, activity: acts })))}
        {btn('清空轨迹', () => presenceTrail.clear())}
      </View>
      <ScrollView contentContainerStyle={{ padding: 12, gap: 10 }}>
        <Text style={{ color: p.fg2, fontSize: p.font(12) }}>在场轨迹 {trail.length}/20（最新在前；只记轴变化，不上传）</Text>
        {trail.map((e, i) => (
          <Text key={i} testID="trail-entry" style={{ color: p.fg2, fontSize: p.font(11), fontFamily: 'monospace' }}>
            {line(e)}
          </Text>
        ))}
        <Text style={{ color: p.fg2, fontSize: p.font(12), marginTop: 12 }}>采集激活 {acts.length}/20（麦 / 摄像头 / 定位为什么开了）</Text>
        {acts.map((a, i) => (
          <Text key={i} style={{ color: p.fg2, fontSize: p.font(11), fontFamily: 'monospace' }}>
            {hms(a.at)} {a.source} · {a.note}
          </Text>
        ))}
      </ScrollView>
    </View>
  )
}
```

`mobile/src/features/chat/usePresence.ts`：末尾 `return derivePresence({...})` 改为
```ts
  const input: PresenceInput = { … 原来那个对象字面量 … }
  const snapshot = derivePresence(input)
  presenceTrail.record(input, snapshot) // 轴没变就不记：渲染期调用是幂等的
  return snapshot
```
（import `PresenceInput` 类型与 `presenceTrail`。）

`mobile/src/features/chat/useHandsFree.ts`：`onOrbState` 回调第一行加 `presenceTrail.mark('fsm:' + f)`（import `presenceTrail`）。

`mobile/src/features/chat/ChatScreen.tsx`：`onConfirm` 改为
```ts
  const onConfirm = useCallback(
    (reply: '确认' | '取消', operationId?: string) => {
      // 位置授权同意 = 定位这一档「开了」：激活日志有了第三个产出方（评审 D8 那条 location）
      if (!operationId && pendingLocationText !== null && reply === '确认') activityLog.push('location', '位置授权 · 同意')
      core.confirmReply(reply, operationId)
    },
    [core, pendingLocationText],
  )
```
`mobile/src/app/_layout.tsx`：`<Stack.Screen name="presence-trail" options={{ title: '调试 · 在场轨迹' }} />`；`SettingsScreen.tsx` 调试分区加 `<Link href="/presence-trail" style={{ color: p.accent, fontSize: p.font(14) }}>在场轨迹（B2：光球为什么变了 / 麦为什么开了）</Link>`。⚠ expo-router typed routes：`Link href` 的字面量类型由 Metro 生成（B1 第 2 批坑：**闸依赖 dev server 在跑**）——`tsc` 若报 `"/presence-trail"` 不是合法路由，先起一次 `npx expo start --dev-client` 让 `.expo/types` 更新，不是去改 href 类型。

- [ ] **步骤 4：跑绿 + `tsc`**；Metro：设置 → 在场轨迹 → 回对话屏轻点说一句 → 回轨迹页：`◇ fsm:LISTENING`（免唤醒开时）/ `listening · voice-sheet` 等条目按时间排列；「复制 JSON」能贴出。

- [ ] **反向验证**：`record` 里去掉 `if (!changedAxes.length) return` ⇒ 只红第一条（tick 也记了）；`push` 不切片 ⇒ 只红环形。真机：D8 的 `location` 产出——第一次问「附近有什么」→ 同意定位 → 轨迹页「采集激活」出现 `location · 位置授权 · 同意`。

- [ ] **提交**：
```bash
git add -- mobile/src/core/presence/presenceTrail.ts mobile/src/app/presence-trail.tsx mobile/test/presenceTrail.test.ts && git commit -m "feat(mobile): UX v2 B2-14 在场轨迹页（PresenceSnapshot 变化 20 条环形 + FSM 打点）+ activityLog.list() 首个消费方与 location 产出方（🔁-1、D8）" -- mobile/src/core/presence/presenceTrail.ts mobile/src/app/presence-trail.tsx mobile/test/presenceTrail.test.ts mobile/src/features/chat/usePresence.ts mobile/src/features/chat/useHandsFree.ts mobile/src/features/chat/ChatScreen.tsx mobile/src/app/_layout.tsx mobile/src/features/settings/SettingsScreen.tsx && git show --stat HEAD
```

### Task 15: B2→B3 闸——真机验收 7 条 + 真人语音轮五项读数 + 5 人外部小样本 + 记录收口

**Files:**
- 新建 `mobile/e2e/05-voice-sheet-ptt.yaml`
- 修改 `mobile/e2e/README.md`、`docs/design/2026-08-24-mobile-app-implementation-plan.md`（只加 §B2 指针）、`docs/design/README.md`（本计划状态）、`AGENTS.md` §4.1（只改指针）、本文件 §6.4

**为什么**：方案 §11.1「B2 → B3/B4 之间加一道闸」（评审建议，采纳）：在 MIX Fold 4 上完成**真人语音轮**（唤醒→说→答→追问）、回声降级、键盘、折叠切换、帧率五项读数，再做一轮 **5 人外部小样本**的「状态识别 + 录音手势」测试（§11.4 可读性判据在这里取数）；**闸不过，B3/B4 不开工**——避免状态系统、语音层、折叠屏、行车、材质、无障碍一次整包。§11.2 的 B2 清单 7 条也在这里逐条打钩。**打钩纪律**（主计划 §8）：✅ 只给有真机读数的条目；只在画廊 / 单测上验过的写「样本」；没跑的写 ⬜ **未验**；被外部挡住的写 ❌ 并写清挡在哪。

- [ ] **步骤 1：Maestro 流 ⑤（`manual`）**

```yaml
# mobile/e2e/05-voice-sheet-ptt.yaml
# 流 ⑤：语音层 PTT（UX v2.1 §11.3）——按住光球 → voice-sheet 可见 → 松手 → 收起。
# tag=manual：Maestro 的 longPressOn 只按不说话，ASR 会以「没听清」收尾（层在识别中仍可见，
# 收尾后收起）；要真验转写得有人在旁说话，或用 M4 的直灌探针灌音频。
# CI 的 offline / online 两档都不带 manual；`maestro test mobile/e2e/` 不带 tag 会跑到它——README 写明。
appId: com.xiaozhou.companion
name: 05 语音层 PTT（manual）
tags:
  - manual
---
- runFlow: subflows/open-app.yaml
- longPressOn:
    id: "composer-orb"
- assertVisible:
    id: "voice-sheet"
- extendedWaitUntil:
    notVisible:
      id: "voice-sheet"
    timeout: 30000
```
跑法：`maestro test --no-reinstall-driver --include-tags manual mobile/e2e/05-voice-sheet-ptt.yaml`（绝对路径，B1 第 4 批坑④）。

- [ ] **步骤 2：B2 真机验收表（方案 §11.2 B2 七条，MIX Fold 4，`target=cloud`；截图进 `mobile/e2e/artifacts/b2-15-*.png`，目录已 gitignore）**

| # | 项 | 怎么取 | 结论（回填 §6.4） |
|---|---|---|---|
| 1 | **轻点**光球（免唤醒关）→ 层升起、开始录音、停顿后由端侧 VAD 收尾并发送 | T6 已取；这里复跑一次留最终截图 | |
| 2 | 真人说一句（**需泓舟**）→ 层升起 → 转写大字 → 回答流式 → 8s 追问窗环递减 → **录音中途切后台再回来 / 折叠展开**，记录里的草稿气泡仍在且最终与层里显示逐字相同 | T4 已取一半；追问窗与折叠展开在这里补：`cmd device_state`（B1 计划 T16 第 3 批坑：折叠要真机手折） | |
| 3 | 端到端挡位下层首行「原始音频将在本轮上传」可见；首次切挡位有显式同意 | T5 已取 | |
| 4 | S2S 挡位走一轮 → 记录里出现带「端到端」角标的两条（M4 挂账「端到端未验」一并） | T5 已取；**§11.4 记录完整性 100%** 在此写读数 | |
| 5 | `card_group` 两卡 → 主卡在上、「还有 1 张 ›」可展开 | T8 已取 | |
| 6 | 「这是什么」→ 用户气泡**先于**相机出现（时间戳比对） | T10 已取（录屏帧号 + logcat） | |
| 7 | 打字提问在「自动」档不出声、语音提问出声 | T12 已取 | |

- [ ] **步骤 3：闸的五项读数（真人语音轮，需泓舟在场；读数带 APK 构建时间 `dumpsys package … lastUpdateTime` 与有无 AEC）**

| # | 项 | 度量 | 目标 | 读数 |
|---|---|---|---|---|
| G1 | 真人语音轮：唤醒 → 说 → 答 → 追问（不带唤醒词） | 轨迹页：`◇ fsm:LISTENING` 到下一条 `listening` 快照的时间差 = **首反馈时延**（§11.4 第 1 条）；追问窗内第二句进去 | ≤100ms；追问成功 | |
| G2 | 回声降级 | 天气答完后 5s 内：胶囊「像是我自己的声音，没算数」出现次数 / Dock `audio_echo_degraded` 是否出现 / 是否出现正反馈环（连着两轮同一句） | 无 AEC 包：提示出现、无环；有 AEC 包：三者都 0 | |
| G3 | 键盘 | Maestro 08 复跑退出码；层开着时键盘弹起 → 层重排、发送键仍在树里 | 0；在 | |
| G4 | 折叠切换 | 层开着 + 草稿存在时外屏→内屏、内屏→外屏各一次：层仍在、草稿逐字相同、detent 按新高度重算 | 两个方向都不丢 | |
| G5 | 帧率 | `adb shell dumpsys gfxinfo com.xiaozhou.companion framestats` 在层升起的 2s 内取一次（**用 framestats 逐帧口径，B1 出账③：直方图在本机自相矛盾**）；同屏循环动画实例数（层开时 Composer 主球静止：连拍两帧逐字节相同） | ≥55fps（dev build 读数注明不能当 release）；1 个 | |

- [ ] **步骤 4：5 人外部小样本（§11.4「状态可读性」；需泓舟组织，不录像、不留个人信息，只记编号 P1–P5）**
  1. 状态识别：画廊 `?only=armed,listening-ptt,thinking,speaking,attention-confirm,offline-with-confirm` 深色套 6 张截图（**每张只露光球 + 胶囊 + Dock**，裁掉记录），逐张问「它现在在干嘛？」，答案与预期语义一致记 1：目标 **≥5/6**，五人各一行。
  2. 录音手势：把手机交给对方（免唤醒关、对话屏空），说「用语音问它明天天气」，不给任何提示：记「找到入口用了多少秒 / 用的是轻点还是长按 / 是否成功发出」。目标：5 人全部 ≤15s 内发出。
  3. 记录表进 §6.4；**读数是分布不是结论**——低于目标的项写「未过」，闸的裁决由泓舟按表做。

- [ ] **步骤 5：§11.4 其余取数**（无障碍：Android Accessibility Scanner——**设备上未装，装 APK 到泓舟的设备要授权**（B1 出账⑦）；没授权就记 ⬜ 未跑，并把 T6 的 TalkBack 手动读数当替代证据写清「不是 Scanner 读数」）。

- [ ] **步骤 6：记录收口**
  1. `mobile/e2e/README.md`：加 05（manual，怎么跑、为什么 manual）；06/08/09 三条复跑一次写读数（层加进对话屏后 06 的 `dock-confirm` 仍要在树里——层在 Dock **上方**、不遮 Dock）。
  2. `docs/design/2026-08-24-mobile-app-implementation-plan.md`：B1 指针块下面加一段同款 B2 指针（实施记录在本文件 §6）。
  3. `docs/design/README.md`：本计划那一行状态改「四批收口，闸结论：过 / 未过（列未过项）」。
  4. `AGENTS.md` §4.1 Android 行：只改指针段（B2 四批收口 + 闸结论 + 下一步 B3/B4 或「闸未过、B3/B4 不开工」）——**动前 `git diff --stat -- AGENTS.md` 核行数、提交后 `git show --stat` 复核**。
  5. 本文件 §6.4：读数、坑、遗留出账表（含前三批汇总——逐条核过再写「现状」，不复述）、**未推送清单**（`git log origin/main..HEAD --oneline`，**只报数不推送**，`git push` 需泓舟单独授权）。
  6. 闸的结论一句话写在 §6.4 第一行：**「过 → B3/B4 开工」或「未过：G? / 小样本 ?/6 → B3/B4 不开工」**。

- [ ] **提交**：
```bash
git add -- mobile/e2e/05-voice-sheet-ptt.yaml && git commit -m "docs(mobile): UX v2 B2-15 闸——真机验收 7 条 + 五项读数 + 外部小样本 + Maestro 05（manual）+ 记录收口" -- mobile/e2e/05-voice-sheet-ptt.yaml mobile/e2e/README.md docs/design/2026-08-24-mobile-app-implementation-plan.md docs/design/README.md docs/design/2026-08-30-mobile-ux-v2-b2-implementation-plan.md && git show --stat HEAD
# AGENTS.md 单独一个 commit（它是所有会话都在写的文件）：
git diff --stat -- AGENTS.md && git commit -m "docs(agents): Android 行指向 UX v2 B2 收口与闸结论" -- AGENTS.md && git show --stat HEAD
```

---
## 3. 任务依赖与并行度

```
T1 D1 摘要源 ─► T2 D2–D5 判据修正 ─► T3 语音层骨架 ─► T4 草稿沉淀 ─► T5 S2S ─► T6 手势 + 轻点 ─┬─ T7 边缘极光 ─┐
                                                                                            ├─ T8 card_group ─┼─► T10 视觉先落气泡 ─► T11 回声提示 ─► T12 播报三档 ─► T13 回执 ─► T14 轨迹页 ─► T15 闸
                                                                                            └─ T9 chips ──────┘
```

- **T1 → T2 串行**：都动 `usePresence.ts` 与 `presence.test.ts`，且 T2 的 `hfFsmChangedAt` 会改 `base()`。
- **T3 先于 T4**：层不持状态，T4 的草稿只是记录里的一条用户气泡，层不改一行就会显示它；T3 引入 `turnMeta.source` 是因为「层为语音轮保持升起」这个事实要住在记录里。
- **T6 是第 3 批的门**：`ptt.tap()` / `hf.wake()` / `recycle()` 被 T3 的打断、T11 的回声、隐私栏都用到；`micLease` 前置修不做，真机的免唤醒 + PTT 读数全是假的。
- **T7 ∥ T8 ∥ T9**：三个互不相干的新文件（各自只碰自己的路径 + `VoiceSheet.tsx` 的一行插入——**三个 subagent 并行时 `VoiceSheet.tsx` 由协调者最后合一次**，或三条各自 `git commit -- 自己的新文件`、层的三行插入由协调者一个 commit 收）。
- **T12 / T13 / T14 可并行**（不同文件；`SettingsScreen.tsx` T12 与 T14 各改一个分区——串行提交，谁后提谁 rebase 自己那段）；T15 最后。

## 4. 「不负优化」判据在 B2 的取数点（方案 §11.4）

| 判据 | B2 取数 |
|---|---|
| 首反馈时延 | T14 的轨迹：`◇ fsm:LISTENING`（FSM 回调时刻 ≈ KWS 命中）到下一条 `primary=listening` 快照的时间差；T15 G1 取真人读数，目标 ≤100ms。B1 计划 §4 把它留给 B2「那里才有唤醒→听的真实链」，就是这里 |
| 状态可读性 | T15 步骤 4 的 5 人小样本（6 张画廊截图），目标 ≥5/6——现状基线 B1 没取，B2 这一次就是基线也是读数 |
| 记录完整性 | T5 真机：一次会话中语音轮（含 S2S）在记录里的条数 / 实际轮数 = 100%（现状 S2S 为 0） |
| 承诺不丢 | B1 已收；T1 复验「到期留痕带原话」（留痕不再说一句通用句） |
| 键盘遮挡 | T15 G3：Maestro 08 复跑 + 层开着时键盘弹起的重排 |
| 性能 | T3：层开时 Composer 主球静态（同屏循环动画常态 1 个）；T15 G5：`framestats` 逐帧口径 ≥55fps（dev build 注明） |
| 无障碍 | T6：TalkBack 光球 label 随状态、轻点切换；T15 步骤 5：Scanner 基线（需授权装 APK） |
| 回归 | `npm test` 条数只增不减（315 → 预计 ≈380：T1 +6 / T2 +5 / T3 +12 / T4 +6 / T5 +6 / T6 +12 / T8 +3 / T9 +4 / T10 +1 / T11 +1 / T12 +9 / T13 +5 / T14 +4）；hmi node:test **288 不变**（本批不碰 hmi）；4 条既有 Maestro 流 + B1 的 06/08/09 复跑全绿 |

## 5. 实施判断（写在开工前，做的时候撞到再补）

1. **`Msg` 不能加字段**（共享类型）⇒ B2 的六个并列字段：`draftUserId` / `interruptedIds` / `s2sIds` / `visionIds` / `turnMeta` / `confirmLog`，全部住 `SessionState`；层与气泡按 id 查表。到期留痕、回执用追加消息 / 并列表，不改原气泡。
2. **`vad_silence_ms` 只有 qwen3 realtime 消费**（`llm-gateway/providers.py:762`），缺省引擎 fun-asr 走客户端 stop ⇒ 轻点即说的收尾主判据是端侧 VAD，服务端尾照传、15s 硬上限兜底（T6）。方案 §5.1.1 那句「服务端静音尾收尾」在缺省引擎上是假前提——**方案里的参数是待证命题不是待办**。
3. **PTT 在免唤醒开着时今天是坏的**（recorder 单例，`recorder.ts:50/89`）⇒ `AsrSession` 领 `micLease()`，先于手势契约落、`pttLease.test.ts` 钉住（T6）。B1 验收表第 6 条第二行恰好没取证——「没取证」的那一格可能正藏着缺陷。
4. **免唤醒开着时轻点 = FSM 的 `wake()`**，不另起 TapTalk（那样会有两份 VAD 抢同一路麦）。「哪个引擎持有麦」是 ChatScreen 知道的事实，不是判据；Composer 只报告手势。
5. **语音层开合与 detent 是派生态**（`derivePresence`），层零自有状态。PTT 轮没有追问窗 ⇒ 播报结束、agent 回 idle 即收；若泓舟要「答完再停 8s」，加一个 `SHEET_LINGER_MS` 常量读 `turnMeta.finalAt`（T13 之后才有）——一行判据，不是新机制。
6. **回声提示只有 `echo_dismissed` 一路信号**（续问窗回声，`voiceLoop.mjs:379`）；barge-in 那一路（`:476`）没有 metric，App 看不见。共享文件不改 ⇒ 记遗留给 hmi 侧加 `onMetric('echo_suspected')`，本批不做。
7. **打断不是错误**（方案 §5.2 规则 4）⇒ `markInterrupted` 不再写 `error: true`，改 `interruptedIds`。既有用例若断言了 `error: true`，那是判据变更、改断言并写进 §6.2。副作用：B1 那条「打断 → 4s 红胶囊」的复现在 T4 之后失效（T2 真机取证在第 1 批做完，不受影响）。
8. **播报三档迁移**：旧值任一为 false → 静音，否则 → 自动（Q11 默认自动，不保留「打字也播报」）。泓舟若要保留旧行为，`DEFAULT_APP_SETTINGS.speakPolicy` 改 `'always'` 一行。
9. **回执「安全检查」栏留位不渲染**（VAL 只在拒绝时说话，Q16 挂账后端）；「目标」取 `vehState.vehicle_id`，没有就「当前车辆」——不猜。
10. **轨迹在渲染期记录**（`usePresence` 里 `presenceTrail.record`）：按轴投影去重，同一份输入再喂一次什么都不发生，React 严格模式的双渲染不会写两条。
11. **零新原生依赖**：RNGH 已随 expo-router 注册（`PackageList.java:73`），但根容器 `GestureHandlerRootView` 今天不在（expo-router 不包根、App 也没包）——T3 把它包进 `_layout.tsx`（Android 上 `GestureDetector` 必须在它之内）；仍不响应再退 RN 核心 `PanResponder`——两条都不重建。
12. **`CardGroup` ↔ `CardRenderer` 的循环 import 是渲染期的**（子卡在函数体里才引用），与今天注册表 `card_group` 的递归同形；主卡判据放 `core/cards/`，core 不反向依赖 features（T13 的回执要读它）。
13. **chip 文本以 `routeSend` 判定为准**（T9 测试直接跑路由）：某句不命中就改 `followUps.ts` 的文本，**不改 `nav.mjs`**。
14. **FOLLOWUP→ARMED 会让 armed 胶囊再显示 3s**（`hfFsmChangedAt` 随 FSM 换态重置）——这是想要的提示（「接话窗关了，要再说唤醒词」），不是 bug；写在这里免得被当成 D2 没修好。
15. **Composer 的「背板」= 空输入框区域**：方案 §5.1.1 写的「光球 + 空输入框/占位区 + 背板」，实现里第二个手势探测器只包输入框那一块（有字时关掉）；chips 横滑区与「打断/发送」按钮不在长按热区内——嵌套两个 `activateAfterLongPress` 探测器会互相抢，这是刻意收窄，写进 §6.3。
16. **`Link href="/presence-trail"` 的类型由 Metro 生成**（expo-router typed routes，B1 第 2 批坑）：`tsc` 报路由不合法时先起一次 dev server，不改 href 类型。
17. **S2S 一轮从未验过**（AGENTS.md：云端已开通、端到端未走通）——T5 的真机读数是这件事的第一次验证；若走不通，红的可能在网关侧，先看 `session.state` 帧再改 App。

## 6. 实施记录（分批回填；每批一个会话，写完即停）

> 格式照 B1 计划 §6：先**开工基线**（自己跑出来的数）、再逐任务的提交与读数、再**反向验证**、再「本批踩的坑」、最后「遗留 / 给下一批的话」。读数只写自己跑出来的数，不复述计划里的预期；**未跑的一律不写 ✅**。

### 6.1 第 1 批「遗留修正与判据层」（T1–T2）

- **开工基线**（2026-08-30 15:20，主工作树，**未分树**）：`check_android_env.ps1` 退出码 0（18 pass / 0 warn / 0 fail）；`npm test` = **29 suites / 315 tests 全绿**；`npm run typecheck` = **0 error**；`git status --short` 空；`git log --oneline origin/main..HEAD | wc -l` = **17**。
- **T1** —— commit `a751390`（8 文件，`--stat` 与计划 Files 逐条对上：4 新建 + 4 修改，148+/10−）。新增用例 **11 条**（`actionSummary` 5 + `dockLabel` 5 + `sessionStore` 1）。反向验证两条：① `actionSummary` 退回 B1 取法（`messages[at].text`）⇒ `actionSummary.test` **5 条全红** + `sessionStore` 新用例红，`dockLabel.test` 仍绿——计划预期写的是「前三条」，实测第 4/5 条（空串兜底、空白归一）同样只由被改的那一行决定，**不是红错地方**；② `dockLabelMode` 的 `>=` 改 `>` ⇒ 只红 `pref=normal × 1.3 → hidden` 那一行。真机：`b2-01-font100-dock.png`（默认字号，Dock 标题=「打开后备箱」**用户原话**、右侧标签渲染）／`b2-01-font200-dock.png`（200%，标题=「解锁车门」两行完整、**标签不渲染**）／`b2-01-attention-two.png`（两条并存，两条助手气泡逐字相同、Dock 钉「打开后备箱」+「另有 1 个待处理 ›」）／`b2-01-attention-card2.png`（取消第一条后第二张卡标题=「解锁车门」⇒ **两张卡逐字不同**）／`b2-01-note-expired.png`（**D1 第二个出口**：留痕行「⏱「解锁车门」的确认已过期」而非那句通用句）。标签有无是**程序化数的**（原图 x[650,1030) 亮度≥135）：默认 2112/1038 像素 vs 200% **18** 像素，通道自检=默认那两张必须>300 才读结论。
- **T2** —— commit `128896a`（8 文件，129+/43−；比计划 Files **多一个** `mobile/test/presenceSeen.test.ts`，见「本批踩的坑」①）。新增用例 **5 条**（privacy 四档改写净 +1、`MIC_LABEL` +1、armed 3s +1、D3 +1、画廊守卫 +2 中的净增）。四条反向验证**各红各自那条**（每条都先 `grep` 核对变异真落盘）：
  1. `errorLive` / `armedCapsule` 两行对调 ⇒ 只红「error 在免唤醒开着（armed）时也出红胶囊（D3）」；
  2. `armedCapsule` 去掉时间条件 ⇒ 只红「armed 胶囊只在进入待机 3s 内显示（D2）」+ 画廊「armed 两条样本」守卫；
  3. `MIC_LABEL.cloudAsr.tone` 改 `'plain'` ⇒ 只红「MIC_LABEL 四档齐全 / 只有两个上传档琥珀」；
  4. `mic` 派生 `micActive ? 'cloudAsr'` 改回 `'edge'` ⇒ 只红「privacy 轴四档」+ 画廊「四档各有样本」守卫。
  画廊深浅各一套（`b2-02-gallery-dark-1/2.png`、`b2-02-gallery-light.png`，样本 26 条）：`armed`（胶囊「说「小舟小舟」」）／`armed-quiet`（**无胶囊**、青环仍在）／`error-hf-on`（**红「出错了」**）三条并列可见；四档 `mic=off`(idle) / `edge`(armed 三条) / `cloudAsr`(listening-ptt、recognizing-partial) / `cloudAudio`(listening-s2s) 全部有样本。
  真机五项：
  - **armed 3s ✅**（`b2-02-armed-f09..f13.png`）。以 logcat `KwsModule: KWS loaded`（15:56:13.386）为零点连拍 14 帧、每帧记墙钟；胶囊区（原图 x[384,700) y[2085,2150)）亮像素：+0.36s=0（未进 armed）→ **+1.13s / +1.92s / +2.79s / +3.70s = 1352（胶囊在）** → **+4.63s = 82（消失）**。同帧青环（Composer 光球 r76–81 的 G−R）：+8.76 / +8.38 / +7.90 / +7.92 / **+8.48**，对照 idle（免唤醒关）**−0.98** ⇒ **胶囊 3s 后消失、青环全程在**。⚠ 青环半径是**按半径逐环差分找出来的**（armed−idle 在 r=79 差 +12.87），第一次凭尺寸估的 r52–68 差值只有 1.6、不足以下结论。
  - **D3 红胶囊 ✅**（`b2-02-d3-error-capsule.png` / `b2-02-d3-after-4s.png`）。免唤醒开（顶栏采集点青色）+ 发一句 + busy 期点「■ 打断」：胶囊区红像素 1135 连续 4 帧（≈3.6–4.5s，与 `ERROR_SHOW_MS=4000` 一致）显示**红「已打断」**，第 5 帧起归零且**无 armed 胶囊**（进 ARMED 已超 3s ⇒ D2/D3 两条判据同时生效）。
  - **空闲「关」非琥珀 ✅**（`b2-02-rail-idle.png`，D5）。隐私栏麦克风行：`edge` 档「唤醒词待机（端侧监听，不上传）」RGB(193,194,197) R−B=−4；关掉免唤醒后 `off` 档「关」RGB(196,196,199) R−B=**−3** ⇒ 都不是琥珀（B1 那条 `capture !== 'armed'` 会把空闲态的「关」涂琥珀）。同帧顶栏采集点随之**不渲染**。
  - **cloudAsr 档琥珀 ✅**（`b2-02-dot-edge-teal.png` / `b2-02-dot-cloudasr-amber.png`）。按住光球期间（后台跑 `input swipe` 长按、前台连拍）顶栏采集点：按住前 RGB(100,200,184) G−R=+100=**青 teal(edge)**，按住中 RGB(222,155,59) R−B=+163=**琥珀(cloudAsr)**，4 帧一致。
  - **隐私栏 PTT 行琥珀 ⬜ 未验**：需要「按住光球 + 点顶栏健康点」同时发生，**adb 单点注入做不到**（B1 第 4 批同一条坑）；非流式档松手后的 `finalizing` 窗口在无人声输入时太短，隐私栏动画放完已回 `edge`（实拍到过渡帧，其「最近一次：麦 16:37 按住说话」证明 PTT 确实录了）。判据与已验的采集点**同源**（都读 `MIC_LABEL[snapshot.privacy.mic]`，一个取 `.long` 一个取 `.short`，`tone` 同一份），但那一行本身**没有直接取到读数**。
  - **TalkBack 读屏 ⬜ 未验**：`uiautomator dump` 在对话主屏只拿到 **1 个节点**（e2e/README 已记：常驻动画屏永不 idle），content-desc 取不到；未装 TalkBack（改系统无障碍设置不在本批授权内）。
- **收口读数**：`npm test` = **31 suites / 331 tests 全绿**（315 → 331，净增 16 = T1 11 + T2 5；suites +2 = 两个新测试文件）；`npm run typecheck` = **0 error**。两个 commit，`origin/main..HEAD` = **19**（未推送）。
- **本批踩的坑**：
  1. **计划的 Files 漏了一个文件**：`mobile/test/presenceSeen.test.ts` 也构造 `PresenceInput`，加必填 `hfFsmChangedAt` 后它先在 `tsc` 红。判据是**先 grep 全部构造方**再改类型（`grep -rn "PresenceInput" src test`），不要照 Files 清单照单全收。同一轮 grep 还查出 `state-gallery.tsx` 把 `privacy.mic` 拼进字符串——那个**不受**加档影响，「看起来是消费方」和「会被类型波及」是两回事。
  2. **`git commit -- <paths> -m '…'` 在本机 git 下把 `-m` 当 pathspec**（计划两条命令都是这个写法，直接跑必报 `pathspec '-m' did not match`）。改成 **`git commit -m '…' -- <paths>`**。
  3. **一条超长 heredoc 把 `presence.ts` 写成了半份**（bash 报 `here-document ... delimited by end-of-file`，文件停在第 187 行、末尾是半句 `'cloudAudio`）。当场 `git checkout` 还原，改成**增量 patch + 每个锚点断言唯一命中**，再用 `git diff | grep '^-'` 核对「只删了预期的 3 行」。教训与「一次失败的编辑脚本会留下半份改动」同族——**整文件替换的失败模式是静默的**，增量 patch 至少会在锚点上报错。
  4. **计划锚点漂移**：`sessionStore.test.ts` 末尾的 describe 已不是计划说的 `UX v2.1 B1-4：承诺面的账本侧`（别的会话在它后面加了 `B1-16 前置：离线期间暂停看门狗`）。按「先 `grep -n "^describe("` 核结构」把新用例搬回正确的组。
  5. **判据量错了对象，读数照样返回**（本批三次）：① 判 busy 时量的是**发送按钮**（它永远在，打断按钮在它左边）⇒ 四帧都报「不 busy」而屏上明明有「■ 打断」；② 标签让位第一次取的 y 范围窄了 40px ⇒ 两张都报 0 像素，**默认字号那张本该有**，这是通道坏了不是结论；③ 并发 tap 打断了 swipe、隐私栏根本没开，判据仍从对话气泡的青色文字里算出一个「非琥珀」。三次都是**先做通道自检**（拿一张肉眼确认过的图跑同一判据）才救回来的。
  6. **发中文的路子**：`adb shell input text` 不支持中文；Maestro IME 装在设备上但 `am broadcast` 接口不对；**Maestro CLI 已不在本机**（`~/.maestro` 只剩 `tests/` 日志与 `deps/`，没有 `bin/`）。最终走 `run-as` + 拉改推 `databases/RKStorage`（AsyncStorage）把 `quickCommands` 前两条临时换成「打开后备箱」「解锁车门」，取证完**整库还原**并逐字比对（`两键逐字一致: True`）。滚设置页时误触把「回答长度」改成了「详细」，也是靠这次比对发现并改回的——**改过设备设置就要有一次逐字段 diff 收尾**。
  7. **MSYS 路径转换又踩两次**：`adb push … /data/local/tmp/…` 被翻成 `D:/Program Files/Git/data/local/tmp/…`；`/tmp/x.png` 在 bash 与 Windows python 眼里不是同一个目录。一律 `MSYS_NO_PATHCONV=1` + scratchpad 绝对路径。
  8. **Metro 已有一个在跑**（8081 被占，`npx expo start` 非交互模式直接退出）。先 `netstat` + `Get-CimInstance Win32_Process` 核对那个 PID 的 CommandLine **确实是本仓的 mobile 目录**，再复用它——不另起第二个（两个 dev server 时「App 连的是哪个」就说不清了）。
  9. `xiaozhou://state-gallery` 深链在 dev-client 里**没跳转**（`am start` 只报 "delivered to top-most instance"），改从设置页「实验室」分区的入口进。
  10. 折叠屏两块屏都要试：本轮活跃的是**外屏** `4630947090644569220`（1080×2520），内屏 `4630946481727302019` 折叠着、截出来是全黑。
- **遗留 / 给第 2 批的话**：
  - 上面两格 ⬜（隐私栏 PTT 行、TalkBack 读屏）**没有取到读数**，不要当成已验。想补的话：PTT 行需要多点触控（`sendevent` 或真人手指）；读屏 label 可以在 T3 给 `PresenceCapsule` 加 `onPress` 时顺带用 Maestro（要先把 CLI 装回来）或真人开 TalkBack 验一次。
  - `mobile/e2e/artifacts/` 是 **gitignore** 的，19 张截图只在本机；文件名已逐条记在上面。
  - T3 会动 `presence.ts` 的 `input` / 新增 `sheetDetent`：**本批新加的 `hfFsmChangedAt` 是必填字段**，再有新的 `PresenceInput` 构造方（`fixtures.ts` 的 `sheet-*` 三条）记得带上，且 `presenceSeen.test.ts` 的 `base()` 也在名单里。
  - 本批**没有动** `AGENTS.md`（T15 的事），**没有 push**；`origin/main..HEAD` 19 个提交里有别的会话的，推送前按计划 §0 第 3 条念一遍谁的东西在里面。

### 6.2 第 2 批「语音层骨架与记录沉淀」（T3–T5）

- **开工基线**（2026-08-30 17:35，主工作树，**未分树**）：`check_android_env.ps1` 退出码 0（18 pass / 0 warn / 0 fail）；`npm test` = **31 suites / 331 tests 全绿**；`npm run typecheck` = **0 error**；`git status --short` 空；`git log --oneline origin/main..HEAD | wc -l` = **21**；HEAD = `36957e8`。
- **T3** —— commit `41fae55`（15 文件，486+/26−，`--stat` 与计划 Files 逐条对上：3 新建 + 12 修改）。新增用例 **13 条**（`turnView` 4 + `presence` 7 + `presenceFixtures` 1 + `sessionStore` 1）。判据全部落在 `derivePresence`：`VoiceFacts` 三个事实 → `input` 开合 + `sheetDetent` 三档 + `turnSource` 透传；`VoiceSheet` 零自有状态（除挂载动画）；`_layout.tsx` 补 `GestureHandlerRootView`（真机下拉收起生效＝这条补对了，见真机项）。
- **T4** —— commit `e948f71`（6 文件，200+/20−）。新增用例 **6 条**。**改了既有用例⑥的断言**（`必测边界 › ⑥ 本地 cancel 后网关 cancelled 幂等`，`sessionStore.test.ts:301`）：原 `toMatchObject({ text: '已打断', error: true })` → 拆成 `toMatchObject({ text: '已打断' })` + `expect(interrupted.error).toBeFalsy()` + `expect(interruptedIds).toContain(id)`。**这是判据变更（方案 §5.2 规则 4「打断不是错误」），不是测试让步**——红色留给 error 帧与超时，留痕改由并列表 `interruptedIds` 承担。看门狗三条（`:529/545/560`）动 `clearWatchdog` 后**仍全绿**（单独 `-t` 跑过一遍取证）。
- **T5** —— commit `890c0d1`（10 文件，327+/10−）。新增用例 **6 条**（`sessionStore` 4 + `settingsMeta` 2）。
- **T5 真机取证时新发现的缺陷，另立 commit `2187ca3`**（2 文件，29+/4−）：**App 把 TTS 的 provider/音色当成 S2S 的传上去了**——`useHandsFree.getS2sConfig()` 返回 `{voice: s.voiceId, provider: s.ttsProvider}`（真机上是 `minimax` / `female-shaonv`），而网关 S2S provider 的值域只有 dashscope/mock/off（`llm-gateway/s2s/provider.py:388-419`）⇒ `build_s2s_provider('minimax')` 一路 `return None` ⇒ 每次 `session.start` 都回 `unsupported`「S2S 未配置或无凭据」。**这就是「端到端从未走通」的直接原因，红在 App 侧不在云栈**（HMI 侧 `getS2sConfig()` 只给 `{pipeline, voice: s2sVoice}`、**不给 provider**，走网关 env 缺省）。修法＝两个都不给（App 没有 `s2sVoice` 这个设置项）；**源码级断言钉住**（`handsFree.test.ts` ⑥，含通道自检：先断言真的抓到了 `getS2sConfig` 那个块）——值域住在网关 env，运行期这一侧没有任何东西会红。
- **收口读数**：`npm test` = **32 suites / 357 tests 全绿**（331 → 357，净增 **26** = T3 13 + T4 6 + T5 6 + fix 那条源码断言 1；suites +1 = `turnView.test.ts`，源码断言加在既有的 `handsFree.test.ts` 里所以 suites 不变）。⚠ 本行第一版写的 356 是**加源码断言之前**那一趟的数——读数的有效期只到下一次改动为止；`npm run typecheck` = **0 error**。
- **反向验证**（每条都先 `grep` 核对变异真落盘，跑完逐条还原并复跑全绿）：
  - **T3-1** `sheetOpen` 删掉 `voice?.override === 'open' ||` ⇒ 只红「点按胶囊 = 打开语音层」1 条 ✅
  - **T3-2** `voiceTurnLive` 去掉 `voice.turnSource !== 'text'` ⇒ 只红「文字轮不升层」1 条 ✅
  - **T3-3** `sheetDetent` 的 0.62/0.78 对调 ⇒ 只红 detent 用例 1 条；**画廊 detent 守卫没红**——计划预期它也红，实测它验的是「三档各有样本」这个**覆盖度**，对调后三档集合不变（`sheet-answering` 0.62↔`sheet-card` 0.78 互换）⇒ **不是红错地方，是那条守卫的判据面本来就不含「哪条样本是哪档」**
  - **T3-4** `currentTurn` 改成「最后一条助手气泡」⇒ 只红「用户刚说完」+「旁白」2 条 ✅（与计划预期一致）
  - **T4-1** `send` 去掉 `reuse` 分支 ⇒ 只红「复用不追加第二条」1 条 ✅
  - **T4-2** `markInterrupted` 恢复 `error: true` ⇒ 红 **3 条**（两条新用例 + 既有用例⑥）——计划写「只红两条」，第三条正是我这次改的那句判据变更断言，方向一致
  - **T4-3** `clearWatchdog` 不摘 `queuedIds` ⇒ 只红 D9 1 条 ✅
  - **T5-1** ChatScreen 的 `onS2sEscalated` 不调 `takeS2sUserBubble` ⇒ **一条都不红**（计划已预告：store 用例直接调 `takeS2sUserBubble`，**接线层零 jest 覆盖**）。补了一条**对照变异**证明用例本身有效：`takeS2sUserBubble` 改成恒返回 null ⇒ 只红「逃逸」1 条 ✅。接线证据只能来自真机（逃逸轮恰好一条用户气泡）⇒ 列在下面的 ⬜。
  - **T5-2** `s2sAnswerDelta` 总是新建气泡 ⇒ 只红第一条（`toHaveLength(2)`）✅
  - **T5-3** `needsS2sConsent` 的 `<=` 改 `>=` ⇒ 只红同意用例第 3 行（`s2sConsentAt: 1_700_000_000_000` → false）✅
  - **fix 那条** 把 `provider: ttsProvider` 注射回去 ⇒ 源码断言红 ✅；**真机对照**见下。
- **真机取证**（设备 `5d432b6d`，外屏 `4630947090644569220`，Metro 复用既有 PID 56952 = 本仓 `mobile/`；`dev_stack target show` = **cloud**；`https://…:8444/api/s2s/info` = `{"available": true, "provider": "dashscope"}`）：
  - **PTT 升层 ✅**（`b2-03-sheet-ptt.png`）：按住光球 → 层从底部升起（detent 0.4）、层内 88dp 大光球青环、层内胶囊「● 在听…」、「⌄ 收起」、记录变暗、顶栏采集点转**琥珀**（cloudAsr）、输入框占位变「正在听…」。
  - **同屏循环动画常态 1 个 ✅**（方案 §11.4 的硬读数，程序化逐字节比对，非肉眼）：层**开**着时 Composer 主球框（原图 x[44,182] y[2356,2494]）5 帧与首帧 **0 字节不同（0.00%）**，同帧层内大球框（x[400,678] y[1460,1740]）**52.8–53.3% 字节不同**；**通道自检**＝层**关**着时同一个 Composer 框 **51.2–54.4% 不同**（证明这个框里本来就有会动的东西，0.00% 不是「框里没东西」）。
  - **文字轮不升层 ✅**（`b2-03-text-turn-no-sheet.png`）：快捷指令「播放音乐」发出 → 执行完成（「好的 / 已执行 media.control」），全程层未升起。
  - **点胶囊升层 ✅ + attention 不自动收 ✅ + Dock 在层下方可见 ✅**（`b2-03-capsule-attention.png`，一张图三项）：pending 期点胶囊「正在思考…」→ 层升起（**文字轮也能开**，override=open）；转写区显示当前轮用户原话「打开后备箱」、层内光球**琥珀**（attention）、层内胶囊「等你确认」、回答区是助手原文；**Dock 确认卡完整显示在层下方**（「⚠ 打开后备箱 / 危险动作 · 需二次确认 / 3:52 后过期 / 取消·确认」），层没有自动收。detent = 0.62（有回答无卡无 task），与判据一致。
  - **下拉收起 ✅**（`b2-03-collapse-swipe.png`）：层内下滑 500px（≈182dp > `SHEET_DISMISS_DY` 80dp）→ 层收起、记录恢复、Dock 与胶囊仍在。**这一条同时证明 `GestureHandlerRootView` 补对了**——RNGH 的 `GestureDetector` 在 Android 上不在根容器内根本不响应。
  - **S2S 首次显式同意四项全 ✅**（`b2-05-consent.png` / `b2-05-consent-accepted.png` / `b2-05-consent-second-noprompt.png`，每步都当场回读 `RKStorage` 而不是只看屏）：设备存量正好是「`voicePipeline=s2s` 且**没有** `s2sConsentAt` 键」⇒ 合并后 `s2sConsentAt=0`、页脚无同意行（存量场景对了）；切回三段式（回读 `classic`）→ 切端到端 → **同意页弹出且背后挡位仍是三段式**（没有预先切）→「仍用三段式」→ 回读仍 `classic`、`s2sConsentAt` 仍 0 → 再切 → 同意 → 回读 `voicePipeline=s2s`、`s2sConsentAt=1788085468272`、页脚出现「已于 2026/8/30 18:24:28 同意端到端上传原始音频」→ 切回三段式再切端到端 → **不再弹**。
  - **S2S 会话建立 ✅（这条链的第一次真机验证，AGENTS.md 挂账「端到端未走通」的前半程到此为止）**：`startS2s()` 是 `enable()` 时就建的（会话级常驻，`handsFree.ts:224`），所以「启动后有没有那条 unsupported 通知」就是「会话建没建成」的判据。三点对照，同一像素判据（notice 区 y[2000,2140] 亮像素）：**修前 1513**（`b2-05-s2s-unsupported-before.png`，屏上是「S2S 未配置或无凭据」）→ **修后 461**（`b2-05-s2s-ok-after.png`，无 notice）→ **把 bug 注射回去 1513**（与修前逐数相同）⇒ notice 消失确由 `2187ca3` 决定，不是别的原因。
  - **免唤醒升层 ✅**（`b2-04-hf-sheet.png`，三段式挡位）：说「小舟小舟」→ FSM 进 LISTENING → 层升起（detent 0.4）、层内 88dp 大球**青环**、层内与层外胶囊都是「● 在听…」、顶栏采集点转**琥珀**（`cloudAsr`——三段式录音的音频同样上传做识别，与 D5 的四档判据一致）。**三个入口至此全部验到**（PTT / 免唤醒 / 端到端）。
  - **端到端挡位说话升层 ✅ + 开录即告知 ✅**（`b2-05-notice.png`）：一帧同时给出四项——层升起、首行琥珀实色条「端到端语音 · 原始音频将在本轮上传」、层内转写「今天天气怎么样」、记录里同一句话的用户气泡（层是记录的视图，不是第二份状态）。
  - **S2S 一轮走通 ✅（AGENTS.md 挂账「端到端未走通」到此为止）**（`b2-05-turn.png`）：记录里两个 S2S 自答轮，每轮**用户气泡与助手气泡都带「端到端」角标**（`深圳市宝安区当前音。`／`今天深圳宝安区是阴天，记得带把伞哦。`；`小周，小周。`／`我在呢，有什么需要帮忙的吗？`）；同一份记录里的**主链轮一个角标都没有**——角标判据正确区分了「谁答的」。
  - **逃逸轮 ✅，三次独立复现**（`b2-05-escalate.png`）：「今天天气怎么样啊」「今天天气怎么样」「打开后备箱」三轮各**恰好一条**用户气泡、**无角标**、走主链（前两轮出天气卡、第三轮出 Dock 确认卡「⚠ 打开后备箱 / 危险动作 · 需二次确认」，已点取消未执行）。**这正是 T5-1 反向验证在单测层零红的那条接线**（`takeS2sUserBubble` 只有 store 用例、ChatScreen 的调用没有 jest 覆盖）——真机是它唯一的证据来源，且复现了三次。
  - **§11.4 记录完整性 = 5/5 轮 = 100%**（此前 0）：2 个 S2S 自答轮各 2 条记录（4 条）+ 3 个逃逸轮各 1 条用户气泡且无重复。
  - **草稿 draft→final ✅**（`b2-04-draft-fg.png`）：三段式下说「附近有什么好吃的」，定稿后记录里是**恰好一条、实线边框**的用户气泡（草稿转正、没有出现第二条）。
  - **草稿「逐字变」⬜ 未验**：三次连拍（150/300/420 帧）都没盖住那一段——前两次窗口在他开口前就用完了，第三次 420 帧全程零变化。过程判据缺读数，但**定稿态判据（一条实线气泡）已验**，且 `draftUser`/`discardDraftUser`/`commitDraftUser` 有 3 条 store 用例 + 反向验证钉着。
  - **打断留痕（灰字「已打断」不变红）⬜ 未验**——**失败原因写清楚**：busy 窗口只有 5–15s（还因 `withLocation` 的定位刷新而到达时间不定），而我这一侧每次工具调用往返 30–90s，`adb` 单点注入无法在**同一条命令**里可靠地「等到 busy 再点」；试了 5 次，两次点在非 busy 态的输入框上（弹出键盘反而把按钮挡了）、三次 chip 没落到。判据本身有 2 条 store 用例 + 反向验证（`markInterrupted` 恢复 `error: true` 红 3 条）；缺的只有「`MessageBubble` 把 `interrupted` 渲染成灰字」这一层。第 3 批 T6 动 `Composer.tsx` 时顺手取一张。
  - **第 1 批遗留的两个 ⬜ 仍未验，但 TalkBack 那条排除范围缩小了**：隐私栏 PTT 行琥珀（要多点触控，adb 单点注入做不到，同第 1 批）；TalkBack 读屏 label——**这次装了也启用了**（`com.google.android.marvin.talkback` 在设备上，`dumpsys accessibility` 的 `Enabled services` 确认已注册），卡在两处：`settings put secure touch_exploration_enabled 1` 之后 `touchExplorationEnabled` 仍报 `false`（MIUI 要求在系统设置里人工确认），且 `uiautomator dump` 在常驻动画屏仍是 0 节点。**用完已关**：`settings delete secure enabled_accessibility_services` + `touch_exploration_enabled=0`，复核 `Enabled services:{}`。
- **本批踩的坑**：
  1. **计划给的测试语料撞上了被测系统的前置闸**：T3 那条 `sessionStore` 用例用「天气」「附近有什么」，两句都命中 `routeSend` 的位置征询（`decision.kind==='consent'` 分支**不派发**）⇒ `turnMeta` 一个键都没有，红的是测试不是判据。换成「讲个笑话 / 再讲一个」即绿，并在用例里写了原因。判据：**先打印一次真实产出**（我是靠一个临时 `zzdbg.test.ts` 打出 `messages`/`turnMeta` 才看出来的），别对着断言猜。
  2. **`adb shell cat` 拉二进制会损坏**（LF→CRLF 转换）：`run-as … cat databases/RKStorage` 经 `adb shell` 拉下来 `pragma integrity_check` 报 `database disk image is malformed`，而**第一次读还能读出几个 key**（部分页面没坏）——差点当成「库本身坏了」。一律用 **`adb exec-out`**。
  3. **点击落空会把「功能没实现」和「我没点中」混成一件事**：第一次测同意页，两次 `input tap` 都落在说明文字上（上一条 swipe 的惯性还在滚，按钮已移出坐标），屏上「没弹同意页」——差点据此定性成「Modal 没接上」。改成**先连拍两帧比对确认页面静止**（两帧字节差 0）→ 再从静止图取坐标 → 点完**立刻回读 `RKStorage`** 核对值真的变了。第 3 批那条「改完一个设置要当场核它真的变了」原样应验。
  4. **「没看到 X」要证明观测通道开着，两处都用上了**：① Composer 主球 0.00% 差异 → 拿「层关着」的同一个框做通道自检（51–54%）；② S2S notice 消失 → 把 bug 注射回真机再测一遍（1513 逐数复现）。没有这两步，两条读数都可以用别的原因解释。
  5. **计划的反向验证预期可能比实际判据面宽**（T3-3 画廊守卫、T4-2 第三条红、T5-1 零红）：三条都不是红错地方，是「这条守卫到底在验什么」与计划的措辞有出入。**红的条数与计划不符时，先问「这条守卫的判据面包不包含这个变异」，再问是不是自己写错了。**
  6. **一条只有 store 用例的判据，接线层是没被验证过的**（T5-1）：`takeS2sUserBubble` 在 store 里有 4 条用例，但「ChatScreen 有没有真的调它」零覆盖——单测能证明的最远边界就在 core/。这类判据的真机项不是锦上添花，是**唯一**的证据来源。
  7. **`getS2sConfig` 这个 bug 单测永远抓不到**：值域住在网关的 env 与 `build_s2s_provider`，App 侧传什么都「类型正确」。它是被**真机上那条 notice** 抓到的，而那条 notice 在屏上挂了不知道多久没人当回事——**屏上常驻的降级提示要当成红灯读**。
  8. **`git commit -- <paths>` 隔离得了文件、隔离不了 `origin/main` 的推进**（见下条遗留）。
  9. **本批（和第 1 批）的取证脚本一直没做 PNG 反滤波**——把 IDAT 解压后的字节直接当像素读，只有 `filter type=0` 的扫描线才碰巧对。症状很好认却一直被当成别的原因：顶栏采集点恒读 `(0,0,0)`（以为坐标错了）、「有打断按钮 vs 无按钮」读出 1316 vs 1217（以为区域选大了）。写了个带 defilter 的最小解码器之后，同一判据变成 **2158 vs 0**，采集点也读出 `(104,209,191)` 的青色。**判据面要分两类重读**：颜色/亮度类结论必须用解码器；**逐字节差异类结论不受影响**（同一 filter 作用在两帧上，内容相同则字节相同）——所以「Composer 主球 0.00% / 层内大球 52.8%」与 notice 区三点对照 1513/461/1513 仍然成立。这是第 1 批坑⑤「判据量错对象，读数照样返回」的**同族第四例**，而且这次错在**解码层**不是取样区域。
  10. **`adb shell` 拉二进制会损坏、`adb exec-out` 不会**（坑②的另一面）；本批还撞到 **busy 窗口（5–15s）与工具调用往返（30–90s）不可通约**：想「看到 busy 再点打断」在多轮工具调用里做不到，必须把等待与点击写进同一条命令，而 `withLocation` 的定位刷新让 busy 到达时间在 5–15s 之间浮动，固定延时也押不准。**要抓短窗口交互，得让被测状态先变长或换注入方式**，不能靠多试几次（试了 5 次全落空）。
  11. **连拍窗口三次都没盖住真人说话那一段**（150/300/420 帧）：前两次在他开口前就用完，第三次 420 帧全程零变化。**长时窗口 + 人工节奏的组合，取证成本远高于预期**；下次要么等对方说「开始」再挂连拍，要么直接录屏（本机无 ffmpeg，抽帧要另找工具）。
- **遗留 / 给第 3 批的话**：
  - **⚠ `origin/main` 在本批中途被另一条会话推进，把我的 T3 `41fae55` 一并推上去了**（`git reflog show origin/main`：`e9fa602` 那次 push；那条线是 QA safety guard，随后还有 `3657b62`/`5e88ae8`）。**我一次 `git push` 都没跑**。所以 `origin/main..HEAD` 现在 = **3**（T4 `e948f71` / T5 `890c0d1` / fix `2187ca3`），不是「21+4」。这与 B1 那次同形态、方向相反（那次是我的 push 带走了别人的）——**push 的粒度是分支不是提交，共享 main 上「请不要推我的」不是可执行防线**；可执行的那条仍是计划 §0.1 写着的 `git worktree` 分支隔离。第 3 批开工前先 `git log --oneline origin/main..HEAD` 念一遍谁的东西在里面。
  - **⚠ 真机上发现的渲染问题（本批不改，交视觉批）**：`VoiceSheet` 的外壳是 `Glass`（G1 半透明）+ 身后 40% 暗区，**记录里的气泡会透过层与层内文字重叠**（`b2-03-capsule-attention.png` 里「等你确认」与助手气泡的正文叠在一起，两边都难读）。欢迎屏（记录空）时看不出来，有气泡且正好落在层区域时才出现。方案 §5.2 要的是「记录变暗 40%、**仍可见**」，所以不是判据错，是**层自己的底不够实**。改它要动材质分级（§5.11），属方案层裁决——建议给 T7（边缘极光那批一起做视觉）或 B3 视觉批：要么暗区提到 60%，要么层底改 G0 实色。
  - **T6 会重写 `usePtt.ts` 与 `Composer.tsx`，我在这两个文件里动过的行**：`usePtt.ts` —— 入参类型加了 `onPartial?` / `onDiscard?` 两个可选回调，并在 **5 处**调用（`finishSession` 的 `tooShort` 分支、`onPartial`、`onFinal` 的空定稿 `else`、`onError`、`.catch` 启动失败）；T6 换 `micLease()` 时这 5 处**不要丢**，草稿气泡靠它们才不会留空气泡。`Composer.tsx` —— 只加了一个 `orbAnimated?: boolean` prop 并传给 `<AuroraOrb animated={orbAnimated ?? true} />`（默认值保证回滚路径逐字不变），T6 做手势契约时保留它，否则「层开时主球静止」那条读数当场作废。
  - `ChatScreen.tsx` 里 T4 加的那个 effect（`hf.fsm !== 'LISTENING'` 时 `discardDraftUser()`）**没有真机验过**（要真人说话）；计划 §5 第 5 条说的 FOLLOWUP→ARMED 撞上 PTT 草稿的罕见误丢路径，本批也没撞到。
  - **设备当前状态**：`voicePipeline=s2s`、`s2sConsentAt=1788085468272`（真实同意记录，**故意不还原**）、`quickCommands` 已还原（临时改过 `[0]` 为「打开后备箱」造 attention 态，用完整库还原并**逐字段 diff = 零差异**）；测试期间发过的那条「打开后备箱」确认卡已点**取消**、未执行。
  - **⚠ 真机上撞见的回声自触发（S2S 挡位，不是本批引入）**：记录里那条带「端到端」角标的用户气泡 `深圳市宝安区当前音。` 不是人说的——是**主链回答的 TTS 播报被麦克风收回、由 S2S 转写成了用户话**（「阴」→「音」），S2S 随后自答了一整轮。与 M4-R1 那条「回声环最终靠平台 AEC 治本」同形态，说明 **AEC 那一行没覆盖 S2S 这条采集路径**。本批零新原生依赖、不重建 APK ⇒ 不动，记给 B3/B4 或 hmi 侧。
  - **设备当前状态**：`voicePipeline` 现为 **`classic`**（为测三段式那一组由我切的，接手时是 `s2s`）；`s2sConsentAt=1788085468272` 保留（真实同意记录）；`quickCommands` 已还原并逐字段 diff 零差异；TalkBack 已关（`Enabled services:{}`）。**要继续测 S2S 就在设置里切回端到端**——同意过了不会再弹。
  - `mobile/e2e/artifacts/` 是 gitignore 的，本批 13 张截图只在本机，文件名已逐条记在上面。
  - 本批**没有动** `AGENTS.md`（T15 的事），**没有 push**。

### 6.3 第 3 批「手势与层内元素」（T6–T11）

- **开工基线**（2026-08-30 22:53，主工作树，**未分树**）：`check_android_env.ps1` 退出码 0（18 pass / 0 warn / 0 fail，含 `E3 devices PASS 1 attached: 5d432b6d`）；`npm test` = **32 suites / 357 tests 全绿**；`npm run typecheck` = **0 error**；`git status --short` 空；HEAD = `61097e2`；`git log --oneline origin/main..HEAD | wc -l` = **0**。
  ⚠ **不是计划预期的 7**：`git reflog show origin/main` 显示 `61097e2 … @{2026-08-30 22:47:11}: update by push`——第 2 批的 merge 落在 22:39:15，**8 分钟后另一条会话推了 main**，把本线 7 个提交（T4 `e948f71` / T5 `890c0d1` / fix `2187ca3` / 三条 docs / merge）一并带上去了。**我一次 `git push` 都没跑。** 与第 2 批同形态、连续第三次（B1 那次方向相反）——「共享 main 上『请不要推我的』不是可执行防线」再次应验；可执行的仍是 §0.1 里那条 `git worktree`。
- **附加⓪ PNG 探针入仓** —— commit `8b26989`（1 文件，378+）。`mobile/e2e/tools/png_probe.py`，纯 stdlib：解 IDAT + 五种 filter 反滤波，子命令 `info / px / region / rows / diff` + **`selftest`**。三点通道自检：
  - `selftest`（造图 → 五种 filter 各编一次 → 解回来逐像素比 + 部分解码与全解一致）= **6/6 PASS**；
  - **变异**：Paeth 分支退化成 Sub ⇒ **只有 filter 4 FAIL**（`(1,1) 解出 (14,40,242)，应为 (18,43,242)`），其余四种与部分解码仍 PASS；
  - **真实 screencap 上反滤波确实在起作用**：`b2-02-dot-*.png` 的 filter 直方图 = `{1:344, 2:669, 3:45, 4:1462}`——**filter 0 一行都没有**（比第 2 批坑⑨说的「只有 filter=0 的行碰巧对」更糟：**一行都没对**）。同一点 (230,62) 反滤波读 `(255,197,42)` 琥珀、裸读 `(0,253,248)` 青——不是偏差，是另一个颜色。
  - `diff` 顺带两端都验了：两图共有的状态栏区 `[224,58)-[244,86)` = **0.00%**，胶囊带 `[0,150)-[1080,260)` = **4.56%**。
- **T6** —— commit `0a317b9`（**14 文件，774+/200−**，与计划 Files 逐条对上：3 新建 + 11 修改）。新增用例 **12 条**（`pttLease` 2 + `tapTalk` 5 + `voiceAsr` 1 + `handsFree` 3 + `presence` 1）。
  - ⚠ **计划自相矛盾，改了一处判据写法**：计划给的 `pttLease` 源码断言是 `expect(src).not.toMatch(/\brecorder\(\)/)`，而计划给的 `usePtt.ts` **全文头注里就写着「`recorder()` 单例…」**——两边照抄必红在注释上。改成**先剥注释再扫**（只剥整行 `//` 与块注释，不动字符串字面量），并加一条通道自检 `expect(code).toContain('export function usePtt')`（否则「没匹配到」可能只是因为把整个文件剥没了）。同「零领域词断言不能裸扫源码」。
  - 计划要求核的两处，实现后 grep 过：`usePtt.ts` 的 `onPartial?`/`onDiscard?` **6 处都在**（`onPartial` 1 + `onDiscard` 5：tooShort / 空定稿 else / onError / .catch / cancel）；`Composer.tsx` 的 `orbAnimated?: boolean` 与 `animated={orbAnimated ?? true}` 保留。
  - §5 第 15 条落地：第二个长按探测器 `plateGesture` **只包 TextInput 那一块**（`input.length === 0` 时才 enabled），chips 横滑区与「打断/发送」按钮不在长按热区内——刻意收窄，避免嵌套 `activateAfterLongPress` 互相抢。
- **T7 + 附加①** —— commit `81b63e4`（3 文件，61+/2−）。`EdgeGlow`（2dp `experimental_backgroundImage` 渐变 + 1.6s 呼吸，`active = primary ∈ {listening, thinking}`），零 jest（纯动效，计划明写）。**附加①**：`VoiceSheet` 在 `Glass` 内垫一层绝对定位实色 `shellTint(p.bg, GLASS.frosted.tint)`（暗色 = `rgba(6,8,15,0.58)`），Glass 自己的白膜与光照边框仍叠在上面。**这是 §5.11 G1 的 tint 落地，不是新裁决**；身后 40% 暗区一字未动（方案 §5.2「记录变暗 40%、仍可见」保留）。选这个改法的理由：`glassBg` 暗色只有 **5.6%**（`theme.ts:68`，那是卡壳用的），浅色下本来就是 76% ⇒ 问题只在暗色，垫一层比改 `Glass` 影响面小。
- **T8** —— commit `d48ccf4`（4 文件，80+/7−）。新增用例 **3 条**。判据在 `core/cards/cardGroup.ts`（零 RN import，T13 回执要读它）；`CardRenderer` 的 `card_group` 项换成 `<CardGroup>`，注册表键没变 ⇒ `cards.test.ts` 的注册表守卫仍绿。
- **T9** —— commit `4743470`（5 文件，127+）。新增用例 **5 条**（计划给 4 条 + 我补的 1 条，见反向验证 T9-1）。**chip 文本一句都没改**——四条都被 `routeSend` 直接认下：`换一批`→`categoryPage=2`；`导航去第一个`（有 `placeItems`）→`导航去星巴克`；`第一个`（只有 `poiNames`）→`导航去加油站A`；`intent_choice` 的 `label→send_text` 原样。`nav.mjs` 一字未动。
- **T10** —— commit `a0bcb5e`（5 文件，45+/6−）。新增用例 **1 条**。`SessionState.visionIds` + `beginUserBubble()` / `markVision()`；`ChatScreen` 的视觉分支改成「先落气泡 → 抓帧 → `send({bubbleId})`」；`MessageBubble` 加 📷 角标、`VoiceSheet` 转写加 `📷 ` 前缀。**拍完不出预览**（红线：预览也是一份落端）。
- **T11 + 附加②** —— commit `84de435`（4 文件，50+/5−）。新增用例 **2 条**。`onMetric` 里两件事分开写：`S2S_LOCAL_HANDLED` 加 `echo_dismissed`（附加②，让 provider 别自答）+ `onEchoDismissed?.()`（胶囊信号源）。`useHandsFree.echoAt` → `usePresence` 与取消提示取更晚的那个。**共享 `voiceLoop.mjs` 一字未动。**
- **真机取证驱动的两处修正，另立 commit `e5f514c`**（1 文件，11+/2−）：① 附加①的暗区 `0.4 → 0.6`（计划 §0.1 预先授权的升级档，读数见下）；② `VoiceSheet` 的 `Gesture.Pan` 加 `.activeOffsetY(10)`——不加方向约束时它把层内 chips 的横滑一并吃掉（T9 真机抓到，见下）。**两条都不是新裁决**：前者是计划写好的第二档，后者是 T3 那条手势缺方向约束、被 T9 的横滑区暴露出来。
- **收口读数**：`npm test` = **36 suites / 380 tests 全绿**（357 → 380，净增 **23** = 附加⓪ 0 + T6 12 + T7 0 + T8 3 + T9 5 + T10 1 + T11 2；suites +4 = `pttLease` / `tapTalk` / `cardGroup` / `followUps`）；`npm run typecheck` = **0 error**。
- **反向验证**（每条先断言变异真的落盘再跑；跑完还原并核对文件逐字回到原样）：
  - **⓪** Paeth 退化成 Sub ⇒ selftest **只 FAIL filter 4** ✅
  - **T6-1** `usePtt` 构造处换回 `recorder()` ⇒ 只红 `pttLease` 源码断言 1 条 ✅（⚠ 第一版变异我写成 `recorderMUT()`，它匹配不到 `\brecorder\(\)`、红的其实是 `micLease()` 那句子断言——**变异要落在被验的那个字面量上**，重做后才算数）
  - **T6-2** `TapTalkSession.start` 不设 `cap` ⇒ 只红「硬上限」1 条 ✅
  - **T6-3** `stop()` 去掉 `endpoint.stop()` ⇒ **只红 1 条**（计划预期 2 条）。不是红错地方：`cancel()` 有**它自己那一处** `endpoint?.stop()`，cancel 用例的 `ep.stopped` 由 `cancel()` 决定、不由 `stop()` 决定 ⇒ 那条用例的判据面不含这个变异。
  - **T6-4** `recycle()` 去掉 `handsFreeOff()` ⇒ 只红 recycle 1 条 ✅
  - **T6-5** `notice` 分支挪到 `followup` 之后 ⇒ 只红 notice 1 条 ✅
  - **T8-1** 排序改降序 ⇒ 只红第一条 ✅
  - **T8-2** 缺省 2 改 0 ⇒ **红 2 条**（计划预期 1 条）。同样不是红错地方：第一条用例的 `{type:'weather'}` 没有 `display_priority`，**它排第几由缺省值决定**（缺省 0 时它会变成主卡）⇒ 那条用例的判据面本来就含缺省值。
  - **T9-1** `push` 去掉去重 ⇒ **零红**。查明原因**不是判据面问题、是够不到**：计划给的夹具里重复项排在第 5 位，`MAX_CHIPS=4` 先把它挡了 ⇒ **去重那个分支没有任何用例走得到**。**补了第 5 条用例**（`category` 与 `intentChoice` 各出一个 `换一批`，共 2 条 chip、离上限还远），同一变异重跑 ⇒ 只红这条新用例 ✅
  - **T9-2** `followUp` 挪到 `intentChoice` 之后（挪不是删——删掉量的是「有没有」不是「顺序」）⇒ 只红 `chips[0]` 那条 ✅
  - **T10-1** `onSend` 改回「先抓帧再 send」⇒ **零红**（计划已预告：这是接线，单测层无区分）。补**对照变异** `markVision` 改成 no-op ⇒ 只红新用例 ✅ ⇒ 用例本身有效，缺的只有接线证据，只能来自真机。
  - **T11-1** `onMetric` 去掉 `echo_dismissed → onEchoDismissed` 那行 ⇒ 只红 T11 用例 ✅
  - **T11-2（附加②）** `S2S_LOCAL_HANDLED` 拿掉 `echo_dismissed` ⇒ 只红附加②用例 ✅
- **真机取证**（设备 `5d432b6d`，外屏 `4630947090644569220`；Metro 复用既有 PID 56952 = 本仓 `mobile/`；装载方式 = `adb reverse tcp:8081 tcp:8081` + 深链 `xiaozhou://expo-development-client/?url=http%3A%2F%2Flocalhost%3A8081`——**dev-launcher 首屏不算应用在跑**，`monkey -c LAUNCHER` 只到那一屏；**APK `lastUpdateTime = 2026-08-29 17:22:24`**）。⚠ 设备在收口时掉过一次线（`adb devices` 空、`Get-PnpDevice` 在 OS 层也无、tailnet 在线但 5555 未监听），泓舟插回数据线后才取到。
  - **T6① 免唤醒开时轻点 = FSM 手动唤醒 ✅**（`b2-07-f1.png`）：层升起 detent 0.4、层内 88dp 大球青环、层内外胶囊都是「● 在听…」、顶栏采集点转**琥珀**（cloudAsr）、「⌄ 收起」在位。
  - **T6② 轻点即说（免唤醒关）✅**（`b2-06-tap.png`，本批最关键的一格）：`handsFree=false` 回读确认后单击光球 → 层升起、**输入框占位变「正在听…」**（=`ptt.state==='recording'`）、层内实时转写「啊，对了，啊，就是这个」、记录里同一句是**虚线草稿气泡**、采集点琥珀。⇒ 方案 Q2「轻点始终能说、与免唤醒开关无关」成立，走的是 `ptt.tap()` 而不是 FSM。
  - **T6③ 说完自动收尾并发送 ✅**（`b2-06-tap-cap.png`，同一次会话 +19s）：层**自行收起**、草稿转正为实线用户气泡、助手已回答——**全程没有任何松手动作**。⚠ **哪一层收的尾这一趟分不出来**：现场有持续环境人声，端侧 VAD 端点与 15s 硬上限都能解释。要分开得在安静环境各测一次（全程静默 ⇒ 只能是 15s 上限；说一句停 1s ⇒ VAD）。三层各自有单测钉着。
  - **T6④ 录音中再点 = 结束并提交 ✅**（`b2-06-a1/a2.png` → 记录里多出「好了。」/「好的，您说。」一轮）。
  - **T6⑤ 按住上滑取消 ✅**（`b2-06-cancel-k2.png`，附加③b 的三段式注入奏效）：`input motionevent DOWN → 0.8s → MOVE ×3（上移 407px ≈ 136dp > CANCEL_DY 60dp）→ UP` ⇒ 胶囊「**已取消，这段话不会发给小舟**」且**记录里没有新气泡**；连拍 k4 帧该区亮像素 8825 → 3050 ⇒ 2s 窗口到期。（`input swipe` 没有按住阶段，长按判定过不去——计划说的这条属实。）
  - **T6⑥ 播报中轻点 = 停播 + 进入收音、层不收 ✅**（`b2-06-speaking-tap.png`）：故事流式播报中点光球 → 「播报中 · 说话可打断」消失、层升起「● 在听…」、采集点琥珀，助手气泡定格并标灰字「已打断」。
  - **T6⑦ 思考 / 执行中轻点 = 展开语音层 ✅**（`b2-07-shell-06.png`：`正在思考…` 态下点光球，层升起；`b2-07-speaking-edge.png`：`处理中…` 五步过程 + Dock 任务项，层内可见）。
  - **T6⑧ D7 隐私栏「关闭本轮麦克风」✅**（`b2-06-privacy.png` / `b2-06-d7.png`）：点完**当场回读 `RKStorage`** —— `handsFree = True`、`wakeWord = True`、`voicePipeline = classic`。旧的 `reenableBargeIn` 会把持久化开关翻成 false 50ms，现在 `stopMic` 一次都不碰它。
  - **T6⑫ 轻点说一句停 1s → 端侧 VAD 自动收尾发送 ✅**（**泓舟真人验收，2026-08-31**）。⇒ 这条把 T6③ 那个「哪一层收的尾分不出来」拆开了：**VAD 那一层单独证实**，剩下 15s 硬上限仍未单独取证。
  - **T6⑬ 免唤醒开 + 按住说话 → 有转写、松手发送、之后说「小舟小舟」仍能唤醒 ✅**（**泓舟真人验收，2026-08-31**）。⇒ **§0 第 5 条「PTT 在免唤醒开着时今天是坏的」这次修复（`AsrSession` 领 `micLease()`）的真机确认**，也是 **B1 验收表第 6 条第二行**一直空着那一格的收口。判据的两半都在：按住能出转写（PTT 那一路真的收到帧了）、松手之后唤醒词仍然有效（PTT 的 stop 没把免唤醒的真麦掐掉）。
  - **T6⑨ 15s 硬上限单独取证 ⬜**（被上面 T6③ 的环境人声吞掉，见那条的说明）；**T6⑩ 输入框有字时长按走原生选择 ⬜**（`input text` 不支持中文、本批没造出「输入框有字」的场景）。
  - **T6⑪ TalkBack 光球 label ✅**（**泓舟真人验收，2026-08-31**；第 1/2/3 批连续卡在这里，到此收口）：泓舟在系统设置里开 TalkBack 并确认触摸浏览后，`dumpsys accessibility` 首次读到 **`touchExplorationEnabled=true`**；手指停在光球上读**「小舟，开始说话」**、双击激活录音后读**「小舟，结束并发送」**，与 `Composer.tsx:93` 的 `recording ? '小舟，结束并发送' : ORB_A11Y[orbState] + '，开始说话'` 逐字对上。⚠ **这条没有程序化通道，三条都堵死了，下次别再试**：① `uiautomator dump` 在这块屏上报 `could not get idle state`（光球常驻动画）；② TalkBack **不把播报文本写进 logcat**（只有 `AudioTrack start … FailoverTextToSpeech` 证明它在出声）；③ adb 注入的单击移不动无障碍焦点（焦点停在标题，截图里的绿框可证）。**只能靠人耳**。
  - **T7 顶缘极光 ✅**（`b2-07-f1..f3.png`，`png_probe rows` 先定位再取值）：层顶缘结构 = 边框行 1364–1366（luma ~70）+ **极光行 1367–1372**（6px ≈ 2dp）。同一窗口三帧 x[100,980) 的读数：**avg B 130.2 → 146.9 → 238.2、亮像素比 78.2% → 83.3% → 100%** ⇒ 在呼吸。f4–f6 归零是因为 **FSM 自己从 LISTENING 回了 ARMED、层收了**（不是极光灭了），这是 FSM 行为不是本批改动。
  - **T7 反向「只在 listening/thinking」✅（真机观测对照）**：正例（`b2-07-speaking-edge.png`，`处理中…` ⇒ primary=thinking）边框行 654–656 之后 **657–662 luma 77.3、B=119.3**；负例（`b2-07-edge-speaking1.png`，层仍开着但 agent 已非 listening/thinking）边框行 722–724 之后 **725–727 luma 33.6、B 无抬升**。同一结构位置，一亮一不亮。
  - **附加① 层壳底 ✅（取了 0.6 那一档）**：58% 壳底之后，同一帧里层外未压暗文字带幅度 **41.9**、层内透出文字带幅度 **18.8** ⇒ 记录仍以 **~45%** 强度透过来（`b2-06-speaking-tap.png` 上层内答案与记录里同一段故事叠在一起、两边都难读）。按计划授权把暗区 **0.4 → 0.6** 后同流程复拍（`b2-07-shell-06.png`）：记录已不可读。⚠ **两张图的记录内容不同**，所以 0.6 那张只有定性判断 + 合成算术（暗区 0.4→0.6 把透出乘 (1−0.6)/(1−0.4)=0.667，45% → ~30%），没有逐像素同场景对比。「仍可见」保留。
  - **T8 card_group ✅**（`b2-08-group-fold.png` / `b2-08-group-open.png`）：「查英伟达股价和新闻」→ 折叠态 = **主卡 NVDA 股价卡 +「还有 1 张 ›」**；点开 = 「收起其余卡片 ^」+ 第二张 `news_digest`（要闻·英伟达）竖排在下。语音层与记录里是同一份渲染（都过 `CardRenderer` 注册表）。
  - **T9 follow-up chips ✅**（`b2-09-chips.png`）：「附近有什么咖啡」→ 层内 chips 行两条：`final.follow_up`「说『看第 1 个详情』或『导航去第 2 个』」+「导航去第…」（`placeItems` 那条）。**「换一批」没有出现，原因查清了且不是缺陷**：chip 的来源是 `candidates.category`，而 `candidates.ts:42` 的 **`place_list`（周边发现）分支显式 `next.category = null`**，只有 `poi_list` 才记 keyword ⇒ 咖啡这一轮根本没有 category 候选。要在真机上验「换一批」得用出 `poi_list` 的语料（如「附近的充电站」），单测那条（`routeSend('换一批') → categoryPage=2`）已经绿着。
  - **T9 顺带抓到一个缺陷并当场修了**：层内 chips **横滑不动**——`b2-09-chips-scroll.png`/`b2-09-scroll2.png` 两次横滑（400ms / 900ms、800px）在 chips 带上 `diff` = **0.00%**，而**同两帧的层内大球框 diff = 99.98%**（观测通道自检：屏是活的）⇒ 第二个 chip 永远够不到。根因是 T3 那条 `Gesture.Pan()` 没有方向约束，把横向拖拽一并吃掉。修法 = `.activeOffsetY(10)`（只在向下超过 10dp 才激活；收起需要的 500px 下拖远超它）。⚠ **这条修复真机未复验**——两次复现场景都失败（一次胶囊落空、一次热更新中间态），复验配方写进遗留。
  - **T10 视觉先落气泡 ✅（气泡本身）/ ⬜（顺序）**（`b2-10-bubble.png` / `b2-10-v1.png` / `b2-10-v12.png`）：「这是什么」→ 用户气泡带 **📷 看图** 角标、答案是视觉卡（模拟车外摄像头）、**记录里没有任何预览画面**（红线：预览也是一份落端）。时间线：点击 `10:29:54.319` → logcat `CameraService::connect call (PID … com.xiaozhou.companion, camera ID 0)` **`10:29:55.059`（+740ms）**——**这就是这次改动消掉的那段窗口的实测大小**。但「气泡帧早于相机连接帧」这条**顺序没取到**：本机无 ffmpeg（`screenrecord` 抽不了帧），`screencap` 单帧往返 450–1000ms，第一帧落在 +1.06s，已经晚于 +0.74s。取证法见遗留。
  - **B1 出账⑧ `looking` 白环静态取证 ✅ 收口**（`b2-10-v1.png`，+1.06s）：胶囊「看一眼…」、顶栏采集点变**白**（与待机 teal / 收音琥珀三档可分）。
  - **T11 回声提示 ⬜ 未触发，且不能记成「预期」**：APK `lastUpdateTime = 2026-08-29 17:22:24`，而开 AEC 的 `96a6830`（Oboe `setInputPreset(VoiceCommunication)`）作者时间是 **2026-08-29 17:27:50** ⇒ **装的这个包在 AEC 之前、没有 AEC**，所以「未触发」**不是**「AEC 在场」那种预期结果，只是这一趟没测到。两条具体原因：① 最后一轮的播报只有「开了」两个字，回声判据是与播报文本求公共子序列比，这么短不可能命中；② 手机上配着 **vivo TWS 4 蓝牙耳机**（当前未连接，但一连上音频就不走扬声器、麦克风根本收不到自己的播报）。复现配方见遗留。
  - **附加③a 打断留痕灰字 ✅**（`b2-04-int-pre.png` / `b2-04-interrupted.png`，第 2 批 ⬜ 到此收口）：用「讲一个三百字的故事」把流式窗口拉长，**点之前先拍一帧**证明按钮在（rect(600,2380)-(830,2465) 平均 RGB **(65.4, 49.1, 33.9)** = 琥珀、亮像素 11.4%）；点完气泡定格在「有一盏路灯，照了小镇一百年。它见过」+ 灰字「已打断」。取色：「已打断」**(61.5, 59.2, 87.1)**、同气泡正文 **(97.6, 94.9, 119.5)** —— 两者都是 B>R≈G 的冷灰，**没有变红**。
  - **设备当前状态：与接手时逐字段 diff = 0**。取证期间为造语料改过 `quickCommands`、为测「免唤醒关」改过 `handsFree`，用完把 10:02 的备份原样灌回（**sha256 `c381c59046b655f0` 逐字节一致**），关掉应用后再回读一次做**逐字段对账：键集合一致、差异 0**（`voicePipeline=classic`、`s2sConsentAt=1788085468272`、`quickCommands` 八条原样、`serverConfig` 原样）。取证期间发出的都是无害指令（故事/股价新闻/咖啡/看图/座椅加热），没有危险动作确认。
- **本批踩的坑**：
  1. **计划的验收栏与计划自己给的实现全文自相矛盾**（`pttLease` 的 `not.toMatch(/\brecorder\(\)/)` vs 计划给的 `usePtt.ts` 头注里就写着 `recorder()`）。两边都照抄 = 一开工就红在注释上。**「计划给了全文」不等于「这份全文和它自己的断言一致」**——同族第二次（第 1 批 C1-A 是验收栏与原则栏矛盾）。
  2. **变异要落在被验的那个字面量上**（T6-1）：第一版我写 `recorderMUT()`，红是红了，但红的是另一条子断言 ⇒ `\brecorder\(\)` 那半条判据那一趟根本没被验证。**「红了」不等于「你想验的那一条红了」。**
  3. **反向验证零红时，先分清「判据面不含」与「用例够不到」**（T9-1）。这次是后者：夹具里的重复项排在第 5 位、被 `MAX_CHIPS` 先挡下 ⇒ 去重整句删掉四条用例一条不红。**判据面不含是设计如此，够不到是覆盖漏洞**——第 2 批坑⑤只写了前者，这一批补上后者：零红时要读夹具，看那条分支到底有没有机会执行。
  4. **计划的反向验证条数预期可能两头都不准**（T6-3 少 1、T8-2 多 1），原因各不相同：前者是「另一个调用点自己也管着那件事」，后者是「那条用例的输入本来就依赖被变异的缺省值」。照旧：先问判据面，再问是不是自己写错了。
  5. **前两批所有颜色类读数的错误面比记的还大**：不是「只有 filter=0 的行碰巧对」，而是真实 screencap 里**一行 filter=0 都没有**（直方图 1/2/3/4 = 344/669/45/1462）⇒ 那些读数**没有一行是对的**。第 2 批坑⑨对症状的定性对、对规模偏轻。
  6. **`git status` 干净不等于「你的提交还在本地」**：开工第一条命令读到 `origin/main..HEAD = 0`，不是我推的、也不是我漏提交，是别人的 push 把我的一并带走了。**开工先念这一句的价值就在这——它答的是「谁的东西在里面」，不是「我有没有推」。**
  7. **「我没点中」这一批出现了三次，每次都差点被读成「功能没做」**：① 点 ■ 打断落到输入框（弹出键盘）——那一刻按钮已经因为轮结束而消失；② 点胶囊落空两次（一次胶囊已过期、一次页面滚动过）。**修法就是第 2 批坑③那条，这次真的用上了：点之前在同一条 `adb shell` 里先拍一帧，把「目标在不在」变成可查的证据**（附加③a 的琥珀读数就是这么来的）。
  8. **`run-as` 读不了 `/sdcard`，但 shell 的重定向已经先把目标文件清空了**——`run-as … sh -c 'cat /sdcard/x > databases/RKStorage'` 报 `Permission denied`，而 `RKStorage` 已经是 **0 字节**。**写入类命令的失败点可能在读那一侧，破坏却已经发生在写那一侧**。可用的通道是 **base64 经 stdin**：`base64 -w0 f | adb shell "run-as PKG sh -c 'tr -d \"\r\" | base64 -d > databases/RKStorage'"`（`tr -d '\r'` 是必须的，adb shell 会做 LF→CRLF）。事后要 `rm databases/RKStorage-journal`（截断留下的热日志会在下次打开时回滚），并**用 sha256 对账**。
  9. **音量键把系统面板拉出来，之后 18 帧量的全是系统页**（`avg_rgb` 三帧一模一样 = 13.1/13.1/13.1、亮像素恒 0）。「读数完全不变」这件事本身就该触发通道自检——一看图是 vivo TWS 耳机设置页，**演员根本没上场**（B1 第 4 批同族）。
  10. **热更新的中间态不能取读数**：改完 `VoiceSheet` 之后 fast refresh 那一帧里，暗区在、极光在、层高度却是 0（`containerHeight` 没重新量到）。全量重载后同一流程立刻正常。**改了组件就重载再拍，别在 Fast Refresh 的中间态上量。**
  12. **`adb reverse` 会在设备掉线重连后消失，症状是「应用无法正常运行」**：本批设备中途掉过一次线，插回来之后 `adb reverse --list` 是空的，dev-client 连不上 `localhost:8081`、载不了 bundle。**Metro 本身一直是好的（`/status` = 200）、JS 侧 logcat 零错误**——所以「应用坏了」这个现象里没有任何一条线索指向真因。判据就一条：**`adb reverse --list`**。重连设备之后第一件事是重建隧道。
  13. **`uiautomator dump` 在常驻动画屏上的真实症状比记的更危险**：不是「dump 出 0 节点」，是 **`ERROR: could not get idle state` 然后命令失败、`/sdcard/ui.xml` 留着上一次的内容**。我据此读到 21 个 `com.android.systemui` 节点，差点定性成「应用不在前台」（那一次恰好屏幕真的在 Dozing，所以两个原因同时成立、更难分）。**dump 之后要先核 XML 里的 `package` 是不是被测应用，再看节点。**
  11. **`input swipe` 与 `input motionevent` 不是一回事，这次两边都应验**：横滑用 `input swipe` 是对的（chips 那条 0.00% 是真的没滚）；而「按住 → 上滑取消」必须 `motionevent DOWN/MOVE/UP` 三段式，`input swipe` 没有按住阶段、长按判定过不去（计划附加③b 说的属实）。
- **遗留 / 给第 4 批的话**：
  - **⚠ 层 Pan 的方向约束（`activeOffsetY(10)`）真机未复验**。复验配方：出一轮 `poi_list` 或带 `follow_up` 的答案 → **等答案出来之后**再点胶囊开层（早点会落空）→ `input swipe <x=1000> <chips 行 y> 200 <同 y> 700` → `png_probe diff` 同一 chips 带；**必须同时对层内大球框做一次 diff 做通道自检**（本批那次是 99.98%）。顺带复核向下拖 500px 仍能收起（T3 已验路径）。
  - **T11 回声复现配方**：装的包**没有 AEC**（APK `lastUpdateTime 2026-08-29 17:22:24` 早于 `96a6830` 的 17:27:50），所以它**应该**能触发。要点三条：① 断开 / 忽略 **vivo TWS 4 蓝牙耳机**，确认音频走扬声器；② 用**长播报**（「讲一个三百字的故事」这类），回声判据是与播报文本求公共子序列比，两三个字的「开了」不可能命中；③ 在**播报结束进 FOLLOWUP 窗**那几秒连拍胶囊区。若第 4 批之前重建过 APK（带上 `96a6830`），那时「未触发」才可以写成「预期（AEC 在场）」。
  - **T10「气泡早于相机」的顺序取证法**：**ffmpeg 已于 2026-08-31 装好**（泓舟当场授权）：`ffmpeg`/`ffprobe` **6.1.1**（gyan.dev 静态 essentials 构建）落在 **`C:\Users\Super\tools\ffmpeg\bin`**，追加进**用户** PATH 末尾。三条要记住的：
    ① **`winget install Gyan.FFmpeg` 装不了**——它走 Delivery Optimization 从 GitHub 拉 9.0.1 full build，实测 **0 字节 / 0 KB/s，完全不动**（不是慢，是停住），10 分钟后被我停掉；
    ② **源要用 npmmirror**：`https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.1.1/ffmpeg-win32-x64.gz`（28MB gz → 79MB exe，`ffprobe-win32-x64.gz` 同目录）实测 **3.3 MB/s**，而直连 GitHub 只有 **57–111 KB/s** ⇒ **快 30–60 倍**。与坑账里 gradle 分发源那条（34KB/s vs 腾讯镜像 3.6MB/s）**同一条教训**：国内拉二进制先找镜像，别等官方源；
    ③ **PATH 是追加到末尾的**——`HKCU:\Environment` 的 `Path` 是 `REG_SZ`、不含 `%变量%`，条目 11 → 12，**Python 三条仍排在 `WindowsApps` 之前**（那条老账没被破坏）；已广播 `WM_SETTINGCHANGE`，但**已经在跑的终端读不到新 PATH**（进程环境是启动时继承的），新开终端才有，或者直接用绝对路径。
    **已验到哪一步**：合成 H.264（与 `screenrecord` 同编码）→ `ffprobe` 读出 90 帧 / 3.0s / 30fps → `ffmpeg -vf fps=10` 抽出**正好 30 张** PNG → **直接喂进 `mobile/e2e/tools/png_probe.py`**（`info` 读出 640×480×3 通道，`diff` 第 1 帧 vs 第 15 帧 = 16.63%）⇒ **抽帧 → 判据这一段是通的**。⚠ **设备那半没验**（取证结束后设备已拔线）：`screenrecord --display-id <外屏>` → `adb exec-out cat` 拉 mp4 这一段，第 4 批第一次用时要先确认一遍，`screencap` 单帧 450–1000ms 分辨不出 740ms 的窗口。两条路：① 装 ffmpeg（`winget install Gyan.FFmpeg` 或解压 release 到 PATH），`adb shell screenrecord --time-limit 8 //sdcard/x.mp4` 后按帧号 × 1/30s 对 logcat 墙钟；② **反向法**：`adb shell pm revoke com.xiaozhou.companion android.permission.CAMERA` 之后说「这是什么」——新代码里气泡是同步先落的、抓帧失败也照样在，旧代码（先抓再 send）则整条 `.then` 拿不到帧 ⇒ 两者在屏上直接可分。用完 `pm grant` 还原。
  - **T6 三格没取到，第 4 批补**：① 15s 硬上限单独取证（要**安静环境**——本批被持续环境人声吞掉，端侧 VAD 与硬上限都能解释同一个读数）；② 输入框有字时长按走原生选择（`input text` 不支持中文，得先想办法造出「输入框有字」——ASCII 一个字符也够）；③ TalkBack 光球 label（MIUI 触摸浏览要人工在系统设置里确认，第 1/2/3 批连续三次卡在这）。
  - **真人项本批清零**：T6⑫⑬⑪ 三条均于 2026-08-31 由泓舟真机验收通过（见真机段）。**第 4 批不需要再排这三条**。
  - **⚠ 设备当前 TalkBack 仍开着**（泓舟为 T6⑪ 手动在系统设置里开的，`Enabled services` 含 `com.google.android.marvin.talkback`、`touchExplorationEnabled=true`）。第 2 批的做法是用完关掉（`settings delete secure enabled_accessibility_services` + `touch_exploration_enabled=0`，复核 `Enabled services:{}`）；**这次开关是泓舟手动做的，关也交回给他**，接手前先核一眼——TalkBack 开着时 adb 的单击语义会变（单击=聚焦、双击=激活），所有 `input tap` 类取证都会失真。
  - **`Maestro CLI` 已不在本机**（T15 要用它跑 05/06/08/09）。装回来两条路：① `curl -Ls "https://get.maestro.mobile.dev" | bash`（装到 `~/.maestro/bin`，Windows 下在 Git Bash 里跑并把该目录加进 PATH）；② 下 release zip（`https://github.com/mobile-dev-inc/maestro/releases`）解压后用绝对路径调 `maestro.bat`。**替代路径**：`mobile/e2e/*.yaml` 的断言多是「文本可见 / 可点」，可以 `adb shell uiautomator dump` + 文本 grep 手工走——但第 2 批坑已记：**常驻动画屏上 `uiautomator dump` 出 0 节点**，所以替代只对 05/06 这类静止屏有效，08（键盘）09（画廊）仍需 Maestro。装 CLI 需泓舟单独授权。
  - **barge-in 那一路的回声仍看不见**（`voiceLoop.mjs:476 _countSelfTrigger` 没有 metric）——共享文件不改，记给 hmi 侧加 `onMetric('echo_suspected')`。T11 只覆盖续问窗那一路。
  - **AEC 没覆盖 S2S 采集路径**（第 2 批遗留）仍在。附加②只堵「FSM 判出来的回声」——判不出来的那些照旧会被 S2S 转写成用户话。B3/B4 或 hmi 侧。
  - **`EdgeGlow` 零 jest**（计划明写「纯动效、无判据」）：`active` 那个表达式住在 `VoiceSheet.tsx` 的 JSX 里，没有任何用例钉它。最小改法是提成 `core/presence` 的纯函数——B4 视觉批顺手。
  - **「换一批」chip 在真机上没有出现过**：来源 `candidates.category` 只在 `poi_list` 分支被记（`candidates.ts:42` 的 `place_list` 分支显式置空）。第 4 批若要补，用「附近的充电站」这类出 `poi_list` 的语料。
  - **取证语料是靠改 `quickCommands` 造出来的**（`input text` 不支持中文、设置页也没有编辑入口）。方法与风险都写在坑⑧；下次要么沿用 base64 灌库法（记得 sha256 对账 + 逐字段 diff），要么给 e2e 加一条「测试语料」入口。
  - 本批**没有动** `AGENTS.md`，**没有 push**；`mobile/e2e/artifacts/` 是 gitignore 的，本批 30 余张截图只在本机、文件名已逐条记在上面。`git log --oneline origin/main..HEAD` = **10**（附加⓪ + T6–T11 + 真机两处修正 + 本节），只报数。

### 6.4 第 4 批「播报·回执·轨迹 + 闸」（T12–T15）

- **闸的结论（第一行）**：**未过——四格无读数：G1 的真人语音轮 / G2 回声 / G4 折叠切换 / 5 人外部小样本。**
  按本计划 §11.1 的纪律（「闸不过，B3/B4 不开工」）⇒ **B3/B4 暂不开工**。⚠ 这四格**不是失败，是没取到**：
  前三格与小样本都需要泓舟本人在场，而**取证会话末尾设备 USB 链路失效**（见坑⑦），剩余 adb 取证被迫中断。
  **裁决权在泓舟**：若认为「已取到的那几格 + 代码层全绿」足以放行，可直接判过；否则补齐这四格再判。
  已取到的闸项：**G3 前半 ✅**（Maestro 08 rc=0）、**G5 ✅**（60.0 fps 呈现节奏、零掉帧；同屏循环动画 1 个）、
  **§11.4 首反馈时延 ✅ 2ms**（两次独立读数；但走的是 `fsm:ARMED` 那一路，唤醒词那一路仍需真人）。

- **开工基线**（2026-08-31 13:40–13:55，主工作树，**未分树**）：`check_android_env.ps1` 退出码 0（18 pass / 0 warn / 0 fail）；
  `npm test` = **36 suites / 380 tests 全绿**；`npm run typecheck` = **0 error**；`git status --short` 空；
  `git log --oneline origin/main..HEAD` = **1**（只有别的会话的 `0a38fb0`——第 3 批那 10 个提交又被别人推走了，**连续第四次**）；HEAD = `0a38fb0`。
  动真机三前提：TalkBack 已关（`Enabled services:{}` / `touchExplorationEnabled=false`，泓舟已处理）；
  **`adb reverse --list` 开工时是空的**，已装回并复核 `UsbFfs tcp:8081 tcp:8081`；Metro 复用 PID 56952（命令行确认是本仓 `mobile/`）。
  `python scripts/dev_stack.py target show` = **cloud**。

- **T12 播报三档** —— commit **`78baa58`**（6 文件 92+/37−，1 新建；与计划 Files 逐条对上）。新增用例 **9 条**
  （`speakPolicy.test.ts` 8 + `sessionStore.test.ts` 1），全量 36→**37 suites / 380→389 tests**，`tsc` 0。
  **零既有断言改动**：`tsc` 跑完没有指出任何第三处旧键读者——`ttsEnabled`/`autoplay` 的全部消费方就是
  `speech.ts::enabled` 与 `SettingsScreen.tsx` 两个 `SwitchRow`。既有的两参 `FakeSpeech` 未改（`voice?` 可选保证少参可赋值）。
  - 反向验证三条（每条先 `grep` 证明变异落盘，跑完还原复跑全绿）：① `speakAllowed` 的 `auto` 分支恒 true ⇒ **恰好红
    `policy=auto × 语音提问=false`** 一条；② 迁移里 `'silent'` 与默认对调 ⇒ **恰好红迁移那一条**；
    ③（自加）`...rest` 换回 `...parsed`（旧键不剥）⇒ **`settingsMeta.test.ts` 全绿**，只有新用例最后两行红。
    **⇒ 计划的 ⚠ 指错了守卫**（见坑①）。
  - **真机（HEAD 含 T12–T14）**：设置页「语音播报」已是三档 `自动 / 总是 / 静音` + 说明文字逐字一致，旧两开关消失（`t15_speak4.png`）。
    **存量迁移端到端取证**（RKStorage，只读 dump + UI 驱动写）：迁移前 `ttsEnabled:true` / `autoplay:true` / `speakPolicy` **缺席**
    （db 20480 B，`integrity_check=ok`，sha256 `c381c59046b655f0`）→ 水合后 UI **落在「自动」** → 点「总是」写库后
    `speakPolicy='always'`、**`ttsEnabled` 与 `autoplay` 双双消失**（24 键，sha256 `0e665e300e6acfd0`）。
    **「总是」＝打字也播报 ✅**：打字问「深圳天气」→ logcat `OboeAudio: openStreamInternal() OUTPUT`（14:41:25）
    + 轨迹页同刻 `speaking · 「播报中 · 说话可打断」`，**两条独立证据**。
    **「自动」＝打字不出声 ✅**：切回「自动」（落库 `speakPolicy='auto'` 已核）后打字问「深圳今天热不热」→ 30s 内
    **零** `openStreamInternal() OUTPUT`；**通道自检**：同段 logcat 里 App 相关行 **2486 条**（日志在流，不是「日志没了」）；
    **阳性对照**＝上面「总是」档那次。⬜ 未取：「静音」切换时播报中立刻停（设备失联前没排上）。

- **T13 执行回执** —— commit **`578db3f`**（8 文件 282+/13−，3 新建）。新增用例 **5 条**（`receipt.test.ts` 3 + `sessionStore.test.ts` 2），
  37→**38 suites / 389→394 tests**，`tsc` 0。**零既有断言改动**；`actionSummary.test.ts` 一个字节没动、原样 5/5 全绿
  （`actionSummary()` 抽出 `precedingUserUtterance` 后语义等价）；`MessageBubble.tsx` 里旧的「已执行 …」灰字行是**替换**
  不是并存（`grep -c '已执行'` = 0）。
  - 反向验证三条：① `understood` 改取 `assistant.text` ⇒ **红 2 条**（计划预期 1 条，**多的那条是计划漏数了自己写的第二条守卫**——
    第二条用例的判据面里也含 `understood: '打开车窗'`；不是误伤，是同一判据有两条独立守卫）；
    ② `provOf` 对 `card_group` 取 `items[0]` ⇒ 恰好红第三条；③ `dispatch` 不记 `operationId` ⇒ 恰好红账本侧第一条。
  - **真机 ✅ 两张都取到**（`t15_receipt_info.png` / `t15_receipt_action.png`）：
    **信息回执**「深圳天气」→ 折叠头「数据来源 · 展开回执」→ 展开四行 `数据源 qweather` / `更新 06:41` / `定位 未使用定位` / `状态 实时`；
    **车控回执**（快捷指令「打开主驾座椅加热」，非危险直接执行）→ 折叠头「**已执行** · 展开回执」→ 四行
    `已理解 打开主驾座椅加热` / `目标 当前车辆` / `确认 无需确认` / `执行 成功 · 14:45 · vehicle.control`。
    `目标` 落「当前车辆」是因为这条云栈没有 `vehState.vehicle_id`——**兜底而不猜**（§5 第 9 条）。
    ⬜ **带「确认」行的那张没取到**：计划授权了「在模拟栈上对『打开后备箱』点一次确认」，排在设备失联之后。
    另一个观察：`charging_list` 卡在 mobile 端**未适配**（落到兜底卡「该卡型暂未适配，内容已收到」），且不带 `_prov`
    ⇒ `buildReceipt` 返回 null、该条不渲染回执——**行为正确**，但卡型缺口是既有的（不是本批引入），记进遗留。

- **T14 在场轨迹页** —— commit **`36352bc`**（8 文件 284+/4−，3 新建）。新增用例 **4 条**，38→**39 suites / 394→398 tests**，`tsc` 0。
  两条反向验证各红各自那条（去掉 `if (!changedAxes.length) return` ⇒ 只红「tick 不刷屏」；`push` 不切片 ⇒ 只红「环形 20」）。
  **D8 两条声明用 `git grep` 在 `HEAD~1` 上取了证**：`activityLog.list()`（单例）与 `activityLog.push('location'` 各 **0 命中**，
  HEAD 上各 **1 命中** ⇒「第一个消费方 / 第一个 `location` 产出方」成立。
  expo-router typed routes 这一关是 **Metro 自己过的**：`.expo/types/router.d.ts` 的 mtime 在路由文件落盘后更新、
  `/presence-trail` 命中 3 处，`tsc` 第一次跑就是 0 error——**没改 href 类型**（§5 第 16 条按预期生效）。
  - **真机 ✅**：设置 → 调试分区末行「在场轨迹（B2：光球为什么变了 / 麦为什么开了）」可见并可进；
    轨迹页 20 条环形、每条带 `轴`/`输入` 两行、`◇ fsm:*` 打点、「复制 JSON」「清空轨迹」两个按钮（`t15_trail2.png` / `t15_trail_fixed.png`）。
    **采集激活 7/20 有真实数据**（`mic · 唤醒词命中` ×6 + `mic · 按住说话` ×1）⇒ `activityLog.list()` 的消费方带真数据工作正常。
    ⬜ **`location` 那条没落上**：定位关掉后问「附近有什么好吃的」，位置授权征询正常弹出（Dock attention 态 +「拒绝/允许」+ 胶囊「等你确认」，
    `t15_consent.png`），但**点「允许」那一下 adb 恰好报 `no devices`（daemon 自重启）**，之后设备失联 ⇒ 这一格 ⬜，
    **产出方代码路径已由 `git grep` 证明存在，只差一次点击**。

- **T14 真机修正** —— commit **`bcc63a0`**（2 文件 45+/1−）。**真机抓到的缺陷**：轨迹屏挂载着时 logcat 报
  `Cannot update a component (PresenceTrailScreen) while rendering a different component (ChatBody)`，屏上还弹了红色警告横幅。
  根因是**计划设计的两半只盘了一半**：§5 第 10 条要求 `record()` 在**渲染期**调用（为了按轴投影去重、严格模式双渲不写两条），
  但 `push()` 里**同步 `notify()`** 就等于「渲染 A 组件时给 B 组件 `setState`」。修法＝通知推迟一个微任务并合并
  （`record()` 仍同步、`list()` 仍是同步真值，只有「通知」晚一拍）。新增 2 条用例钉住；
  反向验证＝把 `notify` 改回同步 ⇒ 全量 **恰好 2 条红 / 400 条**，就是新加的那两条。
  **真机复验带通道自检**：修复前 14:52:07 有该警告；重载后开着轨迹屏 25 秒，logcat **零命中**，
  而同期轨迹条目从 15:02 持续增长到 15:06（证明 ChatBody 的 `usePresence` 确实还在渲染并 record，**观测通道是开着的**）。

- **T15 产出**：新建 `mobile/e2e/05-voice-sheet-ptt.yaml`（manual，含实测得出的竞态说明）；
  `mobile/e2e/README.md` 加「B2 收口复跑」读数表 + 改写「装 CLI」段（本机 dist 落点）+ 新增「dev-client Tools button 会截走手势」一节；
  `2026-08-24-mobile-app-implementation-plan.md` 加 §B2 指针块；`docs/design/README.md` 本计划行状态；`AGENTS.md` Android 行指针（单独 commit）。

- **收口全量**（HEAD = `bcc63a0`）：`npm test` = **39 suites / 400 tests 全绿**；`npm run typecheck` = **0 error**。
  条数账：380 →(T12 +9)→ 389 →(T13 +5)→ 394 →(T14 +4)→ 398 →(真机修正 +2)→ **400**，**净增 20 条，只增不减**。

#### B2 真机验收表（方案 §11.2 B2 七条；MIX Fold 4，`target=cloud`，HEAD 含 T12–T14）

| # | 项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 轻点光球（免唤醒关）→ 层升起、录音、端侧 VAD 收尾并发送 | **⚠ 部分** | 本批复取时**免唤醒是开着的**，走的是 `hf.wake()` 那一路（§5 第 4 条），不是 TapTalk。层升起有实拍（`t15_ptt_hold.png`，见第 7 行）；「免唤醒关时由端侧 VAD 收尾」那一格仍引用第 3 批 T6 读数，本批**没有复取** |
| 2 | 真人说一句 → 层 → 转写大字 → 流式回答 → 8s 追问窗 → 切后台/折叠后草稿仍在 | **⬜** | 需泓舟真人；折叠还需手折。第 3 批取了前半，本批未补 |
| 3 | 端到端挡位下层首行「原始音频将在本轮上传」；首次切挡位有显式同意 | **✅（引用）** | 第 3 批已取；本批在设置页复见同意记录「**已于 2026/8/30 18:24:28 同意端到端上传原始音频**」（`t15_speak2.png`） |
| 4 | S2S 走一轮 → 记录里两条带「端到端」角标 | **✅（引用第 2 批）** | 第 2 批 T5 取得；本批设备 `voicePipeline` 仍是 `classic`（第 3 批切的），未复跑。§11.4「记录完整性 100%」引用第 2 批读数 |
| 5 | `card_group` 两卡 → 主卡在上、「还有 1 张 ›」可展开 | **✅（引用第 3 批）** | 第 3 批 T8 取得；本批未复取 |
| 6 | 「这是什么」→ 用户气泡**先于**相机出现 | **⬜** | 第 3 批用录屏帧号取过一半；本批把设备半段的**取证通路**验通了（见附加③），但顺序取证本身排在设备失联之后 |
| 7 | 打字提问在「自动」档不出声、语音提问出声 | **⚠ 一半 ✅** | **「自动」档打字不出声 ✅**（零 Oboe OUTPUT + 2486 行通道自检 + 「总是」档阳性对照）；**「语音提问出声」⬜**（需真人说话） |

#### 闸的五项读数（方案 §11.1）

| # | 项 | 目标 | 读数 | 结论 |
|---|---|---|---|---|
| G1 | 真人语音轮：唤醒 → 说 → 答 → 8s 内不带唤醒词追问；首反馈时延 | ≤100ms；追问成功 | **首反馈时延 = 2ms**，两次独立读数：`14:30:08.565 ◇ fsm:ARMED` → `14:30:08.567 armed` 快照；`15:02:52.924 → .926`。**但两次都是 `fsm:ARMED` 那一路**（`hfFsm` 输入驱动），**唤醒词 → LISTENING → 说 → 答 → 追问**这条真人链路一次都没跑 | **⬜**（时延机制 ✅，真人轮未取） |
| G2 | 回声降级：胶囊「像是我自己的声音，没算数」出现次数 / 是否成正反馈环 | 无 AEC 包：提示出现、无环 | **未取**。APK `lastUpdateTime=2026-08-29 17:22:24`，**早于 AEC 提交 `96a6830`（17:27:50）⇒ 这个包没有 AEC，本应能触发**。本批把两个前置条件补上了：① A2DP `STATE_DISCONNECTED`（vivo TWS 4 已断开，音频走扬声器）；② **发现媒体音量是 0/150 并抬到 140/150**（见坑③——这很可能就是前几批「回声一直不触发」的真因）。取证本身排在设备失联之后 | **⬜** |
| G3 | 键盘：Maestro 08 退出码；层开着时键盘弹起 → 层重排、发送键仍在树里 | 0；在 | **Maestro 08 rc=0，墙钟 133s**（B1 是 126.0s）。**层开着时的键盘重排 ⬜**（未单独取） | **⚠ 一半 ✅** |
| G4 | 折叠切换：层开 + 草稿存在时外屏↔内屏各一次 | 两个方向都不丢 | **未取**——需泓舟手折。本批把口径钉了：`cmd device_state print-states` = 0 CLOSED / 1 TENT / 2 HALF_OPENED / 3 OPENED …，本次全程 `Committed state: CLOSED`（外屏 `4630947090644569220` 1080×2520 是活屏；内屏 `4630946481727302019` 2224×2488） | **⬜** |
| G5 | 帧率 `framestats` 逐帧口径；同屏循环动画实例数 | ≥55fps（dev build）；1 个 | **呈现节奏 = 60.0 fps**（`IntendedVsync` 逐帧差中位 **16.66ms**、**p90 也是 16.66ms**，120 帧**零掉帧**；`FrameInterval` 16.657ms ⇒ 采样期间屏在 60Hz）。GPU 段中位 7.16ms / p95 7.77ms。**循环动画 = 1 个**：层开时 Composer 主球 f1 vs f2 **0.00%**（22500 px 逐字节相同）／同两帧层内大球 **28.87%**（通道自检）／层关时同一区域 **100.00%**（阳性对照） | **✅** |

> **G5 顺带推翻 B1 出账③的定性**：`Janky frames: 0 (0.00%)` 与 `Janky frames (legacy): 748 (100.00%)` **不是「直方图自相矛盾」**，
> 是**两把尺子量两件事**——现代口径按 `FrameDeadline` 判（本次 deadline 远在未来，所以 0%），legacy 按固定 16ms 判**帧耗时**
> （`FrameCompleted − IntendedVsync` 中位 37.25ms，因为渲染流水线深度 >1 帧，所以必然超，但**每个 vsync 都有一帧交付**）。
> ⇒ **「帧耗时 > 预算」≠「掉帧」**；要读实际帧率就看**呈现间隔**，不要看帧耗时直方图。
> 另：这块屏是可变刷新率（支持 15/24/30/40/60/90/120Hz），**毫秒数不能直接换算 fps**，必须先读 `FrameInterval`。
> ⚠ framestats 在本机是 **24 列新布局**（`Flags,FrameTimelineVsyncId,IntendedVsync,…,FrameCompleted(第 17 列),…`），
> **不是网上常见的 19 列**；按老索引解会得到「帧耗时 3286 万毫秒」这种明显荒谬但不会报错的数——**先读表头再解析**。

#### 5 人外部小样本（§11.4「状态可读性」）

**⬜ 未做**——需泓舟组织 5 名外部用户。两张表的跑法保持计划 §Task 15 步骤 4 原样：
① 6 张画廊截图（`?only=armed,listening-ptt,thinking,speaking,attention-confirm,offline-with-confirm` 深色套，裁到只露光球+胶囊+Dock）逐张问「它现在在干嘛」，目标 ≥5/6；
② 把手机交给对方（免唤醒关、对话屏空）说「用语音问它明天天气」，记入口用时 / 轻点还是长按 / 是否 ≤15s 发出。
**只记编号 P1–P5，不录像、不留个人信息。读数是分布不是结论。**

#### Accessibility Scanner

**⬜ 未跑**——装 APK 到泓舟设备需单独授权，本批**没有授权**。替代证据是第 3 批 T6⑪ 的 **TalkBack 人耳读数**（泓舟 2026-08-31 真机验收通过），
**那不是 Scanner 读数**，不能当作无障碍基线。

#### §11.4「不负优化」八条判据在本批的取数

| 判据 | 读数 |
|---|---|
| 首反馈时延 | **2ms**（`◇ fsm:ARMED` → `armed` 快照，两次独立读数一致）；目标 ≤100ms ⇒ 达标，但走的不是唤醒词那一路 |
| 状态可读性 | ⬜（5 人小样本未做，B2 本应是基线也是读数） |
| 记录完整性 | 引用第 2 批 T5 读数；本批 `voicePipeline=classic`，未复跑 |
| 承诺不丢 | ✅ Maestro 06 rc=0，`dock-confirm`/`dock-countdown`/`presence-capsule` 三条断言全过 |
| 键盘遮挡 | ✅ Maestro 08 rc=0 / 133s；层开着时的重排 ⬜ |
| 性能 | ✅ 60.0 fps 呈现节奏、零掉帧；同屏循环动画 1 个（三点对照） |
| 无障碍 | ⬜ Scanner 未授权；替代证据是 T6⑪ 的 TalkBack 人耳读数 |
| 回归 | ✅ `npm test` 380 → **400**（净增 20，只增不减）；`hmi/` 一字未动；Maestro 4 条中 3 条 rc=0，05 是 manual 的已知竞态 |

#### 第 4 批附加项（⓪–⑧）逐条结果

| # | 项 | 结果 |
|---|---|---|
| ⓪ | TalkBack 先关 | **✅** 开工即核，已是 `Enabled services:{}` / `touchExplorationEnabled=false`（泓舟已关）。全程未再开 |
| ① | 层 Pan 方向约束（`e5f514c` 的 `activeOffsetY(10)`）真机复验 | **✅ 两半都验到**：横滑 700px 后**层仍升着**（层壳/grabber/当前轮标题/层内大球/答案正文/「∨ 收起」/胶囊全在，`p2_after_hswipe.png`）⇒ 横向手势不误收层；随后三段式向下拖 500px ⇒ **层收起**、回到普通列表（`p3_after_drag.png`）。⚠ 配方修正：计划让「diff chips 带」，但 **diff 分不出「层还在但动画在动」和「层收起了」**（两种情况都会有大比例差异）——**这类判据必须看图或换成结构断言，不能只看 diff 百分比** |
| ② | T11 回声（G2） | **⬜**，见上表。两个前置条件已补齐（TWS 已断开 / 音量 0→140），**很可能前几批的「不触发」根本原因就是音量为 0** |
| ③ | T10 顺序取证的设备半段 | **✅ 通路验通**（顺序取证本身 ⬜）：`adb shell screenrecord --time-limit 4 --size 720x1600 /sdcard/x.mp4` rc=0 → **`adb pull`**（不是 `exec-out cat`）拉回 92311 B → `ffprobe` 读出 720×1600 / 4.44s。⚠ **screenrecord 是 VFR**：静止屏 4.4s 只出 **4 帧**，所以**帧号不能直接对墙钟**，必须先 `ffmpeg -vf fps=30` 重采样成定帧率再按 index/30 换算 |
| ④ | T6 两格 | **a) 15s 硬上限 ⬜**：本次轻点走的是 `hf.wake()`（免唤醒开着），一轮在 **6.7s < t ≤ 9.7s** 之间收尾（连拍文件大小从 825–835KB 掉回 ~712KB 为判据）——**不是硬上限**，而且轨迹页实录显示环境里有持续人声被收进来（`listening · voice-sheet ·「啊？知识库，没有…这飞书的这知识过」`）⇒ **「安静环境」这个前提本身不成立**，要验硬上限得先关免唤醒**并且**换安静环境。**b) 输入框有字长按 ⬜**（排在设备失联之后） |
| ⑤ | Maestro CLI 装回 | **✅ 不需要装**——**计划 §6.3 那条「CLI 已不在本机」是错的**：它只是不在 PATH / 不在 `~/.maestro/bin`（那个目录从来只放 `deps/`、`tests/` 等运行期状态），**2.9.0 的 dist 一直在 `D:/Android/tools/maestro-dist/maestro/bin/maestro.bat`**，`--version` rc=0；设备上 `dev.mobile.maestro` + `dev.mobile.maestro.test` 也还在 ⇒ `--no-reinstall-driver` 直接可用、**没有 MIUI 弹窗**、**零授权消耗**。判据应该是 `find` 整盘而不是 `which` |
| ⑥ | 设备重插后 `adb reverse` | **✅ 且发现新子例**：开工时就是空的，装回一次；此后**又丢了两次**，两次都不是拔线——一次是 **adb daemon 自己重启**（`* daemon not running; starting now`），一次是我 `kill-server` 之后。⇒ **判据从「重插过没有」改成「每次 adb server 生命周期变化后都重建」** |
| ⑦ | 「换一批」chip | **⬜，但缺口定位比计划更准**：`followUps.ts:21` 的 `换一批` 只在 `cand.category` 非空时出，而 `candidates.ts` 里 `category` **只有 `poi_list` 且 `c.keyword` 非空**那一支会记；`place_list` 分支**显式置空**（`:83`）。实测「附近有什么好吃的」→ `place_list`（10 条带评分/人均/营业时间）⇒ 无 category；「附近的充电站」→ **`charging_list`**，且 mobile 端**未适配该卡型**、落到兜底卡 ⇒ 也无 category。**要复现得先找到一条真出 `poi_list` 且带 keyword 的语料** |
| ⑧ | `AGENTS.md` 只改指针段 | **✅**，单独一个 commit；动前 `git diff --stat -- AGENTS.md` 为空（无别人未提交的行） |

- **反向验证（本批共 10 条，逐条先 `grep` 证明变异落盘、跑完还原并复跑全绿）**：
  T12 三条（`auto` 恒真 ⇒ 红 1／迁移默认对调 ⇒ 红 1／不剥旧键 ⇒ **`settingsMeta` 零红**、只有新用例红）；
  T13 三条（`understood` 取 `assistant.text` ⇒ **红 2**、多的那条是计划漏数的第二条守卫／`card_group` 取 `items[0]` ⇒ 红 1／
  `dispatch` 不记 `operationId` ⇒ 红 1）；T14 两条（去掉去重守卫 ⇒ 红 1／`push` 不切片 ⇒ 红 1）；
  真机修正一条（`notify` 改回同步 ⇒ **全量恰好 2 条红 / 400**）。**只有 T13-① 与计划预期不符，已定性到根因、不是缺陷。**

- **本批踩的坑**：
  1. **计划的 ⚠ 会把守卫挂在够不到的地方**（T12）。计划写「剥旧键靠 `settingsMeta.test.ts` 的 `toEqual(DEFAULT_APP_SETTINGS)`」，
     实测那条测试的两个入参是 `null` 与 `'{oops'`，一个走 `if (!raw)` 提前返回、一个走 `catch`，**两条都够不到合并体** ⇒ 它对
     「剥不剥旧键」**零敏感**。真正的守卫只有新用例最后两行。这是「**判据面不含 ≠ 用例够不到**」的第三次应验，而且这次是
     **计划自己指错了**——照它省掉那两行，剥旧键就会完全没有守卫。
  2. **计划的反向验证预期会漏数自己写的第二条守卫**（T13-①，多红一条）。写预期的人想的是「哪条用例叫这个名字」，
     没盘「哪几条用例的判据面里含这个字段」。同族第四次（「逐对交换」名字强于覆盖）。
  3. **⚠ 媒体音量是 0/150，而三条 `cmd media_session volume --set/--adj` 全部静默失败**——它照样打印
     `[V] will set volume to index=90` / `[V] Connecting to AudioService`，回读仍是 0。**成功输出不是读数**。
     有效的是 `adb shell input keyevent 24` ×15（0 → 140/150，`volume_music_speaker` 与 `--get` 双证）。
     **这条很可能就是前几批 G2 回声一直不触发的真因**——音量为 0 时 TTS 根本不出声，麦克风不可能收到回声，
     与 M4「KWS 三轮零命中真因是扬声器音量为 0」**是同一个坑的第二次**。
  4. **dev-client 的悬浮 Tools 按钮会截走手势**：Maestro 的 `scrollUntilVisible` 用屏幕中线 swipe，实测**抓到它并弹出 Expo dev 菜单**，
     表现成「flow 莫名失败」。关掉它之后一切正常。判据不靠眼睛：同一矩形 `png_probe region` 关前 `avg(46.6,48.0,52.6)` / 亮像素 **8.85%**，
     关后 `avg(6.0,8.0,14.0)` / **0.00%**。
  5. **PowerShell 的 `>` 确实损坏 PNG，当场复现**：`adb exec-out screencap -p > x.png` 得到的文件前 8 字节是
     `ef bb bf ef bf bd 50 4e`（BOM + 替换字符），`png_probe` 直接报「不是 PNG」。可用通道是
     `adb shell screencap -p -d <id> /sdcard/x.png` + **`adb pull`**。
  6. **折叠屏抓错屏＝全黑，不是息屏**：设备 `Committed state: CLOSED`，内屏 `4630946481727302019` 抓出来
     32056 B 全黑（`avg(0,0,0)`、亮像素 0.00%），外屏 `4630947090644569220` 是 727521 B 有内容。**先读 `cmd device_state state` 再选 displayId。**
  7. **⚠ 取证会话末尾 adb / USB 链路失效，剩余取证被迫中断**。演进顺序：`dumpsys bluetooth_manager | grep` 卡住 →
     连 `adb devices` 都不返回 → `taskkill` server + `start-server` 恢复过一次 → 随后 `adb pull` / `exec-out` 稳定 timeout（而 `adb shell echo` 一度还能回）
     → 最后 `adb devices` 也 timeout。机器上残留一个**杀不掉的 `adb.exe` PID 14604**（`taskkill /F` 报「拒绝访问」，
     很可能就是 `check_android_env` 报的那个 shadow 副本），怀疑它占着 USB 句柄。**修法需要物理重插数据线或提权杀进程，两件都得泓舟做。** 收口时最后探一次：`adb devices` 本身 rc=0 但**设备列表是空的** ⇒ 设备已完全不在枚举中，不是 adb 慢。
     副教训：**每一条 adb 调用都该带 `timeout`**——我前后被卡掉 5 条命令、每条 45–180s，其中 3 条还要 `TaskStop` 才停得下来。
  8. **`input text` 不支持中文**（当场复现 `NullPointerException: Attempt to get length of null array`）。
     可用替代是 **Maestro 的 `inputText` + `-e` 变量**（`maestro test -e QUERY="深圳天气" ask.yaml`），它走 driver 的 unicode 通道，
     实测中文原样上屏。**这条比第 3 批「改 quickCommands 灌库」的办法干净得多**（不写设备数据库、不用 sha256 对账）。
  9. **一次 `adb` 报 `no devices` 就足以让一整条取证链失效而不留痕**：点「允许」那一下 daemon 恰好自重启，
     tap 落空，后面的截图看起来只是「activityLog 里没有 location」——**跟「功能没做」长得一模一样**。
     这是第 2 批坑③、第 3 批坑⑦的第三次应验：**点之前/点之后都要有独立证据**。
  10. **diff 百分比分不出「还在但在动」和「已经没了」**（附加①）：层区域横滑前后 28.78%、层内大球 34.52%，
     两种解释都成立；**只有看图才分得出**。计划给的「diff chips 带 + 大球框自检」配方在这里不够用——
     自检只能证明「通道开着」，证明不了「层还在」。

- **遗留出账（前三批逐条核过再写现状）**：

  | 来源 | 项 | 现状 |
  |---|---|---|
  | 第 2 批 | `origin/main` 被别的会话推进带走本线提交 | **仍在，且第四次发生**：开工时 `origin/main..HEAD` = 1，第 3 批那 10 个提交已被推走。本批**一次 push 都没跑** |
  | 第 2 批 | `VoiceSheet` 壳底不够实、记录气泡透过层重叠 | **仍在但已缓解**（T7 附加①把 tint 落地）：`p2_after_hswipe.png` 上答案正文与身后气泡仍有重叠，但正文亮白、背景压暗，可读。**视觉批再裁** |
  | 第 2 批 | AEC 没覆盖 S2S 采集路径 | **仍在**（本批零原生变更、未重建 APK；`voicePipeline` 仍是 `classic`，没有复现机会） |
  | 第 3 批 | 层 Pan 方向约束未复验 | **✅ 本批清掉**（附加①） |
  | 第 3 批 | T11 回声未触发 | **仍 ⬜**，但**前置条件已补齐**（TWS 断开 + 音量 0→140），且**音量为 0 很可能就是真因**——下次先核音量 |
  | 第 3 批 | T10 顺序取证的设备半段未验 | **✅ 通路已验通**（附加③）；顺序取证本身仍 ⬜ |
  | 第 3 批 | T6 三格（15s 硬上限 / 输入框有字长按 / TalkBack） | TalkBack **✅ 泓舟已验收**；15s 硬上限 **⬜ 且发现「安静环境」前提不成立**；输入框有字长按 **⬜** |
  | 第 3 批 | Maestro CLI 不在本机 | **✅ 定性推翻**：一直在 `D:/Android/tools/maestro-dist`（附加⑤） |
  | 第 3 批 | barge-in 那一路回声没有 metric | **仍在**（共享 `voiceLoop.mjs:476` 不改，记给 hmi 侧加 `onMetric('echo_suspected')`） |
  | 第 3 批 | `EdgeGlow` 零 jest | **仍在**（`active` 表达式住在 `VoiceSheet.tsx` 的 JSX 里；最小改法是提成 `core/presence` 纯函数，B4 视觉批顺手） |
  | 第 3 批 | 「换一批」chip 从没出现过 | **仍 ⬜，但缺口定位更准了**（附加⑦：要 `poi_list` **且带 keyword**） |
  | 第 3 批 | 取证语料靠改 `quickCommands` 造 | **✅ 有更干净的替代**：Maestro `inputText` + `-e` 变量（坑⑧），不再需要灌库 |
  | **本批新增** | `charging_list` 卡型 mobile 端未适配（落兜底卡、无 `_prov`） | 既有缺口，不是本批引入；记给卡片批 |
  | **本批新增** | `ExecutionReceipt.tsx` 零单测 | `jest.config.js` 的 `testMatch` 是 `test/**/*.test.ts`（不含 `.tsx`），全仓无组件级渲染测试。判据全在 `core/session/receipt.ts`（3 条用例）；折叠交互 / `hhmm()` / `MODE_LABEL` 只能真机验——本批两张回执截图覆盖了后两者 |
  | **本批新增** | ⚠ **设备 USB 链路失效** | 见坑⑦，需泓舟物理重插或提权杀 `adb.exe` PID 14604 |

- **设备当前状态（逐字段对账）**：
  - **TalkBack**：全程 `Enabled services:{}`，**我没有开过、也没有关过**（泓舟在第 3 批末尾关的）；
  - **CAMERA 权限**：**从未动过**（附加③的反向法没用上，没有 `pm revoke`，也就不需要 `pm grant` 还原）；
  - **RKStorage**：只做过**只读 base64 dump**（4 次）+ **UI 驱动的写**（点设置项），**没有用灌库法写过任何字节**，无需 sha256 还原对账；
  - ⚠ **`locationEnabled` 被我改成 `false`（原值 `true`），未能还原**——设备在还原前失联。**请泓舟在 设置 → 记忆与隐私 → 使用定位 点回去**；
  - **`speakPolicy` = `'auto'`**（迁移后的默认档，留着即可；期间为取证短暂切到过 `'always'`）；
  - ⚠ **媒体音量由 0/150 抬到 140/150**（我改的，**故意不还原**——G1/G2/5 人小样本都需要能出声；要还原是 `input keyevent 25` ×N）；
  - ⚠ **expo dev-client 的 Tools button 被我关掉了**（避免它截走 Maestro 手势）；重开：`adb shell input keyevent 82` → 开 Tools button；
  - `voicePipeline` = `classic`（第 3 批切的，未动）；`s2sConsentAt=1788085468272` 保留；`handsFree`/`wakeWord`/`visionEnabled` 均为 `true`（未动）；
  - `quickCommands` **未动**（本批用 Maestro `inputText` 造语料，不再改它）；
  - 蓝牙 A2DP `STATE_DISCONNECTED`（本批未主动断开，接手时就是断的）；
  - `mobile/e2e/artifacts/` 是 gitignore 的；本批 20 余张截图只在 scratchpad，文件名逐条记在上面。

- **未推送清单**（只报数，`git push` 需泓舟单独授权）：`git log --oneline origin/main..HEAD` = **7**
  （本批 5 个：`78baa58` T12 / `578db3f` T13 / `36352bc` T14 / `bcc63a0` 真机修正 / T15 记录收口，外加 `AGENTS.md` 单独一个；
  再加上开工时就在 HEAD 上的、**别的会话的** `0a38fb0`）。**本批一次 push 都没跑。**
