**AR03 停播与横屏操作实施记录｜2026-09-08**

> 状态：客户端与共享 FSM 修复、本地回归与变异验证完成；OPPO prod release 上取得 R06 主证据（同题 A/B）；**R09 横屏那格在本机不可达、层内停止键与 S2S/多段/主动消息未验，AR03 未整批签收**（末节列格）。
> 范围只有 R06 与 R09 的横屏部分；R09 的跨页部分属 AR04，本批不碰。
> 启动基线 `005e5951fd76af3887030ec2eceac5312134b412`（本地 = `origin/main`，工作树 clean；期间另一会话推了 AR02 缓存清理的纯文档提交，不改本批证据锚）。
> 输入：[完整评审 R06/R09](../reviews/2026-09-07-android-ux-full-review.md)、[分批建议 AR03](2026-09-07-android-review-remediation-batches.md#ar03)、[AR01 实施记录](2026-09-07-ar01-confirmation-cancellation-implementation.md)、[AR02 实施记录](2026-09-07-ar02-capture-privacy-implementation.md)。
> 前置说明：AR02 客户端修复已在 main，其完整设备矩阵仍未齐。AR03 的改动面（播放事实、停播命令、横屏层级）与那些未验格不重叠；AR02 的整批签收仍留在 AR02。

---

**一、现状与证据（实施前现读，逐条给文件:行）**

| # | 事实 | 证据 |
|---|---|---|
| E1 | `busy` 只看 `pending / streaming / processActive`，不含音频 | `mobile/src/features/chat/ChatScreen.tsx:397` |
| E2 | `final` 帧把这三个标记一次清零 | `mobile/src/core/session/store.ts:806-809` |
| E3 | 合一键仅按 `busy` 在「发送 / 打断」间切换 | `mobile/src/features/chat/Composer.tsx:263-265` |
| E4 | 真实播报收尾是独立事件（段链 + 250ms 宽限），与 `final` 无关 | `mobile/src/core/voice/speech.ts:296-333` |
| E5 | B5 之后语音层内没有任何停止键，只剩把手带（收起） | `VoiceSheet.tsx:199-210` 全文无 stop |
| E6 | 播报中轻点光球 = `cancelCurrentTurn()` + `startListening()`，**隐式开麦** | `ChatScreen.tsx:376-385` |
| E7 | `speech.stop()` → `finishTurn()` → `onSpeechEnded()` → `ctl.ttsEnd()` → FSM `_gotoFollowup()`，开 8s 免唤醒续问窗 | `speech.ts:378-395` → `handsFree.ts:301-304` → `hmi/src/voiceLoop.mjs:465-470,210-216` |
| E8 | `finishTurn()` 无条件 `flushDeferred()`：**用户按停之后，攒着的主动消息立刻开播** | `speech.ts:322-333` |
| E9 | S2S 自答走 `S2SClient` 自己的 player，不经 `SpeechController` | `handsFree.ts:366-376`、`hmi/src/s2sClient.mjs:285-301` |
| E10 | `derivePresence` 的 `agent` 只读 `i.speaking`（= `SpeechController.speaking`），无 `hfFsm==='SPEAKING'` 分支 ⇒ **S2S 自答期间 `agent` 恒 `idle`** | `presence.ts:193-201`、`usePresence.ts:62-66` |
| E11 | driving-landscape 下语音层挂在整列上，暗区 `absolute inset 0` 覆盖记录区 + Dock + Composer | `ChatScreen.tsx:660-663`、`VoiceSheet.tsx:155-160` |
| E12 | Dock 的回声降级行提示「点『停止播报』后再说」，而该控件不存在 | `FocusDock.tsx:217` |

**二、问题**

1. **停止键跟错了对象**（R06 主症状）。E1+E2+E4：整段答案一次 `final` 返回时三个忙态同帧清零，而 TTS 才刚起播——按钮此刻已变回「发送」，没有一步可达的停播入口。
2. **停止会隐式开采集**。E6 是「停止后开始说话」，而它是**唯一**的一条；E7 让纯停播也把 FSM 推进 FOLLOWUP（下一句不用唤醒词即上行 ASR）；E8 再叠一层，「队列清空」不成立。
3. **S2S 自答不在播报事实面内**。E9+E10：端到端挡位自答时 `agent=idle`，没有播报态、没有胶囊、没有任何停止入口。
4. **横屏关键操作被自己的层挡住**。E11 违反 Dock「永远不被别的轴覆盖」这条既有原则；E12 是同一件事的文案面。

**三、最终实现**

| 问题 | 行为与责任边界 |
|---|---|
| 播放事实 | 新增 `core/voice/playbackFacts.ts`（与 AR02 `captureFacts.ts` 同形态：owner 集合 + `useSyncExternalStore`），**两个轴**：`playing`=已经出过声（在场模型的播报轴，光球/胶囊读它），`live`=还可能出声（会话开着/播放器建好，首片可能没到）。产出方是**真实播放器**：`SpeechController.setSpeaking` 覆盖主链流式段链与批处理兜底；S2S 那一路把 `playerFactory` 包一层（起点＝首片真的推进去、终点＝`stop()`），`S2SClient` 的每条收尾路径都经 `_stopPlayback` ⇒ 一个包装盖全，不在六七个调用点各记一次。`PresenceInput.speaking` 改喂这份事实 ⇒ `snapshot.agent === 'speaking'` ⟺ 真的在出声（S2S 自答从此在事实面内） |
| 三条命令分开 | **停止播报**＝停当前所有出声，不发 cancel 帧、不开麦、不开续问窗、不放 DEFER；**取消在飞请求**＝既有 `cancelCurrentTurn()`（一字未改）；**停止后开始说话**＝既有 `interruptAndListen()`（一字未改） |
| 合一键三态 | `canStopPlayback → ▣停止播报`；`busy 且无音频 → ■打断`；否则 `⬆发送`。可用面的判据在 `core/voice/stopPlayback.ts::canStopPlayback` = `playing ∨ (live ∧ ¬busy)`。**audio-first**：声音已经在放时按这枚键的意图压倒性是「别说了」，那一刻取消在飞请求换不来任何东西。`testID="composer-send"` 保留（Maestro 01/02/03/06/08 依赖），三态各有独立 `accessibilityLabel`，停播态另给 hint「只停止声音，不会开始录音」 |
| 停播不开续问窗 | 共享 `hmi/src/voiceLoop.mjs` 追加 `stopSpeaking()`：SPEAKING/THINKING → **ARMED**。不复用 `recycle()`（那条走 `handsFreeOff→On`，会顺带复位会话级 `_bargeInDisabled` 与自触发计数，是「重新开启插话」的语义）；也不在 mobile 侧另写一份迁移——FSM 的迁移表只许有一份。`HandsFreeController.stopSpeaking()` 置「本次是用户停播」标志、`bargeIn()` 停 S2S 本地播放并上行、调既有停播出口，标志期内 `ttsEnd()/turnEnded()` 短路 |
| DEFER | `finishTurn(natural)`：**只有自然收尾（宽限到点）才补播**。攒着的话不丢——`setProactiveCtx` 的 s2s 转空闲与下一次自然收尾照旧补播，队列本来就有界去重 |
| 层内停止键 | `voice-sheet-stop` 绝对定位在把手带那一行右侧，**只在 `playing` 时挂载**。该行 `minHeight` 已是目标高 ⇒ `ui/layout/sheetHeight.ts` 的 chrome 与三个真机容器读数一个不动。与 B5-12「撤掉底栏两枚常驻键」不冲突：撤的是常驻键，这是条件出现的单一停播键 |
| 横屏层级 | 给层一个**有边界的覆盖域**（`testID="voice-sheet-scope"`：横屏是新的列容器，竖屏是原记录区容器），Dock 落在覆盖域之外。非 split 路径逐字节不变（层仍住记录区容器、Dock 仍在原位置、`listHeight` 量点不动） |
| 文案 | `FocusDock` 回声降级行从悬空引用改成真引用，并跟着停播语义说实话：「点『停止播报』后**重新唤醒**再问」——停播回 ARMED，不是接着说就行 |
| 只读诊断 | `/capture-status` 加一行 `capture-playback`（播放事实 + 起停计数）。只读，不播放、不采集；停播的真机取证读它，不看屏上的键 |

**明确不做**：KWS 模型/阈值、ASR/TTS 选型、声学通路（AR07/AR08 的 A/B 输入）；网关取消协议、proto、VAL、CI/CD、`.env`；跨页语音宿主 / 前后台 / 提醒 ACK（AR04）。没有云端部署、真实车控、商户写或付款。

**四、精确版本与检查**

| 项 | 读数 |
|---|---|
| 启动基线 | `005e5951fd76af3887030ec2eceac5312134b412` |
| 本批代码 | `b5c471832061e4744dc42128e346462be6f41bc8`（前两版见 §五末「两个被自己的用例放过的缺陷」） |
| mobile 全量 | 70 suites / 695 tests，exit 0（基线 65 / 656，本批 +6 文件 / +39 tests） |
| mobile TypeScript | `tsc --noEmit` exit 0 |
| HMI node:test | 308 / 308，exit 0（基线 304；本批 +4，全部针对 `voiceLoop.stopSpeaking`） |
| HMI 构建 | Vite build 成功，8.28s；保留既有 >500kB chunk 提示，未改阈值 |
| 共享面守卫 | `test/sharedAllowlist.test.ts` 随 mobile 全量通过；`voiceLoop.mjs` 是既有白名单条目，未新增共享入口 |

命令为 `node node_modules/jest/bin/jest.js --runInBand` 与 `node node_modules/typescript/bin/tsc --noEmit`（cwd=mobile）、`npm test` 与 `npm run build`（cwd=hmi）。没有放宽断言、删除用例或跳过失败。

**新增用例**（5 个 mobile 文件 + 4 条 HMI）：`playbackFacts`（owner 集合、并存不互清、重复置值空操作、迟到置假不伪造收尾）、`speechStopPlayback`（首片起播/`final` 已到仍在播/按停不补播 DEFER/自然收尾照常补播）、`handsFreeStopSpeaking`（停播出口一次、FSM 回 ARMED、不开续问窗、麦租约不变、迟到回调不推回续问窗、与唤醒词打断分得开、免唤醒关时 no-op、S2S 自答起播与停播）、`stopPlaybackUi`（合一键三态路由与读屏标签、边流边播 audio-first、C 身份可点性、层内停止键出现条件、横屏可达）、`landscapeDockReach`（横屏 Dock 不在覆盖域内、Composer 仍在覆盖域内、竖屏逐字节不变）。

**五、反向验证（AGENTS §4.3）**

逐条注入目标缺陷、跑对应用例、再恢复实现；15 条全部被抓到，无漏网：

| 注入 | 结果 |
|---|---|
| 合一键退回只看 `busy` | RED（3 条） |
| 层内停止键改成常驻 | RED（1 条） |
| Dock 退回覆盖域内 | RED（1 条） |
| 停播改成进续问窗（mobile 侧） | RED（4 条） |
| 停播也补播 DEFER | RED（1 条） |
| 播放事实挂在 `begin` 而非首片音频 | RED（2 条） |
| S2S 播放不报事实 | RED（1 条） |
| `voiceLoop.stopSpeaking` 改成 `_gotoFollowup` | RED（3 条） |
| `voiceLoop.stopSpeaking` 改成走 `_gotoIdle`（复位会话级护栏） | RED（1 条） |
| `voiceLoop.stopSpeaking` 连 ARMED/IDLE 也改态 | RED（4 条） |
| 停播两条腿的顺序颠倒（先主链后免唤醒） | RED（1 条） |
| 停播键退回只看 `playing`（丢掉缓冲段） | RED（1 条） |
| 停播键不看 `busy`（在飞轮也给停播） | RED（1 条） |
| `live` 不随收尾落（停播键永远亮） | RED（3 条） |
| 不播报的轮也留下 `live` | RED（2 条） |

第一版的「不复位会话级护栏」用例曾对着 `_vadBargeInDisabled` 断言，而 `_gotoIdle` 复位的是 `_bargeInDisabled` ⇒ 变异注入时**没红**。改成走 D6 那条自触发路径把 `bargeInDisabled` 真置起来之后才抓得到。**判据自己也要被变异验一遍**，写得像那么回事不算数。

**两个被自己的用例放过的缺陷（`8bb13ab` → `fe798cc` → `b5c4718`）**

两条都是在**构建出包之前**发现的，都不是评审提的，是这一批自己造/自己漏的。两轮构建因此主动中止，没有产物，不计任何 SHA 的构建尝试；日志留在证据目录的 `aborted-1-*`、`aborted-2-*`。

**① 停播两条腿的顺序（`8bb13ab` → `fe798cc`）**

首版把两条腿写在组件里：`speechController().stop()` 然后 `hf.stopSpeaking()`。这个顺序**是错的，而且错得很安静**——`SpeechController.stop()` 同步收尾，回调链 `onSpeechEnded → HandsFreeController.ttsEnd() → voiceLoop.ttsEnd()` 先把 FSM 从 SPEAKING 推进 **FOLLOWUP**；随后的 `stopSpeaking()` 看到的既不是 SPEAKING 也不是 THINKING，于是变成空操作。净效果：声音停了，续问窗照样开着——正是 R06 要修的那件事，被这一批自己又造了一遍。

上面那八条 `handsFreeStopSpeaking` 用例**全是绿的**，因为它们直接调 `ctl.stopSpeaking()`——那等于替被测系统注入了正确顺序（CLAUDE.md「测试替被测系统注入的前提不再被验证」）。

修法不是把两行换个位置就算完：顺序本身是判据，所以它有名字、有理由、有测试——`core/voice/stopPlayback.ts`。新增两条回归用真实控制器 + 模拟真实回调链，一条钉正确顺序落 ARMED，一条钉颠倒顺序落 FOLLOWUP（反例也进库，否则下次改回去没人拦）。

**② 合成缓冲段没有停播入口（`fe798cc` → `b5c4718`）**

R06 点名要验的五个场景里，「播放器缓冲阶段」是唯一一个**两个显而易见的量都不成立**的：全文一次 `final` 到达时三个忙态同帧清零，而 TtsSession 还在合成、一个字节音频都没出来。那几百毫秒里 `busy=false ∧ playing=false` ⇒ 合一键显示的是「发送」，用户对着马上就要出声的播报**没有任何停止入口**。

首版把这一格漏了，因为判据只有「已经出声」这一个量。修法是把播放事实拆成两个轴：`playing` 仍然只表示「已经出过声」（它是在场模型的播报轴，没出声就说「播报中」是假话），新增 `live` 表示「这条通道还可能出声」；停播键读 `canStopPlayback = playing ∨ (live ∧ ¬busy)`——在飞轮且无音频时那一格仍然归「打断」。

两条的共同形态值得单记：**用例把被测系统的前提替它注入了，就再也验不到那个前提**（CLAUDE.md 那条）。①的八条用例直接调 `ctl.stopSpeaking()`，等于替生产路径注入了正确顺序；②的用例全都手工给定 `playing`，等于替生产路径注入了「此刻该不该给停播键」这个判断本身。两次都是把判据搬进一个有名字的纯函数、再让用例对着**那个函数**说话，才把前提暴露到能被变异打红的位置。

**六、取证环境的两处限制（记录，不当作产品结论）**

- reanimated 4 官方 `mock.js` 自己 `import` 真包，在 jest 里照样走 worklets 原生初始化 ⇒ 新增手写 `test/support/reanimatedMock.ts`（只覆盖 `src/ui/aurora/*` 与 `VoiceSheet` 真正用到的符号，缺了会 undefined 立刻炸，不会静默过）。「有一份官方 mock」不等于「这份 mock 在这套环境里能用」。
- `react-test-renderer` 不跑布局 ⇒ 语音层的 `containerHeight > 0` 永远不成立、层根本不挂载。横屏用例里**先手动放一次 `onLayout` 再断言**，并显式先证「层真的升起来了」——层没升起来时「Dock 不被盖」是句空话。

**七、设备验证（OPPO test，prod release `b5c471832`）**

| 项 | 读数 |
|---|---|
| APK | `D:\Android\builds\apk\xiaozhou-companion-prod-release-b5c471832-20260908-0825.apk`，210,675,814 bytes |
| SHA-256 | `0583e297d344c0a8e2f12d4e2a1eb31106fffa54179fedd73a17b7785e10d875`；`pm path` 取到的安装文件同哈希 |
| 构建 | `BUILD SUCCESSFUL in 15m 20s`，exit 0；`-CompileJobs 1`、堆 `128m/2048m`（AR02 那次 1GB 在 D8 报 OOM，本轮直接用 2GB）；1222 tasks / 491 from cache |
| 设备 | OPPO PEUM00 / Android 14，外屏 988×1972、density 440；`install -r` 后 lastUpdateTime 20:51:42 → 2026-09-08 08:26:38；非 DEBUGGABLE |
| 设置页构建行 | `v0.1.0 · prod · b5c471832 · 2026-09-08 08:10` |
| 构建期工作树 | 构建起于 08:09:56，`mobile/` 与 `hmi/` 最后一次改动是 08:09:48 的 `b5c4718`；构建期间只提交过 `docs/` |

**R06 主证据：同一问题、同一答案的 A/B。** 用快捷指令「讲个笑话」，合一键的态按 ~2.5s 采样：

| 轮次 | `停止播报` 采样 | 回到「发送」 |
|---|---|---|
| 自然播完 | 11.2s → 26.9s，连续 7 个采样 | 29.5s |
| 按下停止（7.4s 按） | 7.4s，单个采样 | 10.3s |
| 对照（同题不干预，另一轮） | 7.1s → 15.0s，连续 4 个采样 | 17.6s |

按下之后播报窗口从 ≥15.7s / 8–10s 塌成一个采样。**修前这一格根本不存在**：`final` 一到键就变回「发送」。

**停止不开麦（设备侧）。** 全程 8 次播报、2 次用户按停之后，`/capture-status` 的采集计数仍是
`{"micStarts":0,"micStops":0,"asrSegments":0,"s2sSegments":0}`、`micActive:false`——麦克风一次都没开过。
播放计数 `{"playing":false,"live":false,"starts":8,"stops":8}` 起停配平，没有泄漏。

**缓冲段（间接证据）。** 五轮里合一键的序列都是 `打断 → 停止播报 → 发送`，**没有任何一个采样落在「发送」上**。
采样密度 2.5–2.7s，所以这只能说「没抓到」，不能声称连续无缺口；判据本身由 `canStopPlayback` 的用例与两条变异钉住。

**取证环境的三条硬限制（ColorOS 侧，记录为设备边界，不是产品结论）：**

- `uiautomator dump` 在对话页拿不到 idle（光球常驻动画）。用的是仓库里本来就有的取证开关「减少动效（强制）」，读完已关回。不是靠关动效掩盖问题，动效与合一键的判据无关。
- `WRITE_SECURE_SETTINGS` / `WRITE_SETTINGS` 对 adb 全部拒绝 ⇒ **动画缩放、`wm size`、`wm density`、`user_rotation` 一个都改不了**（读数确认三项原值未变：`1.0 / 1.0 / null`，`988x1972`，`440`）。
- 没有声学输入通道（同 AR02 的限制），语音发起的轮取不到。

**R09 横屏那格在这台机器上不可达（不是没做，是做不了）。** `driving-landscape` 要 `宽 expanded(≥840dp) ∧ 高 compact(<480dp)`：
外屏横 = 1972×988px ÷ 2.75 = **717×359dp**（宽只到 medium）；内屏 = 1920×1792px ÷ 2.75 = **698×652dp**（高不是 compact）。
两块屏都落不进那个分支，而密度/尺寸/旋转覆盖又被系统拒了。Xiaomi 对照机外屏（1080×2520 @480dpi ⇒ 横 840×360dp）理论上命中，
但那是泓舟主用机，README 明令不在其上改设备状态。**这条要带进 AR10 的支持范围裁决**：当前两台真机里，
方案承诺的横屏车载形态只有对照机的外屏够得到。

**八、未完成项（AR03 未整批签收）**

仍未验的格：

- **层内停止键**（`voice-sheet-stop`）：需要层在播报期间开着。语音发起的轮取不到（无声学输入）；改用播报中点状态胶囊升层的路子，两次都在点下后 ~1–2s 内播报结束（同题不干预的对照跑了 8–10s）。样本 n=2 对 n=1，且 TTS 时长本轮方差很大（同一问题另一轮 16s），**分不清是升层停了播报还是 TTS 自己结束**，所以既不记该格通过、也不记为缺陷——留作 AR04 接手跨页/层宿主时的第一条待查。层内键的挂载条件与按下去向由组件用例 + 2 条变异钉住。
- **多段回答**（divergent / mixed 段链）中途停播：本轮没有构造出多段轮。
- **S2S 自答**真机停播：需免唤醒 + 端到端挡位 + 说话，无声学输入。
- **主动消息**播报中停播：不能按需触发。
- **横屏行车态**确认与停止可达、200% 字号与长摘要：本机形态不可达（见上节）。
- 与停播/接段直接相关的一次**混合意图盲听**回归——需泓舟参与。

云端未部署，未做真实车控/商户写/付款。本轮只用了快捷指令「讲个笑话 / 我今天有点不开心 / 今天天气怎么样」三条，
没有触发任何 `require_confirm` 动作。设备设置改过四项（播报、减少动效强制、设备角色、行车档），**已逐项还原并回读**；
系统设置一项都没改（也改不了）。结束时 App 退到 Launcher，crash buffer 的 FATAL 计数为 0。
AR01/AR02 的 APK 读数**不转借**给本批代码。AR04（跨页宿主 / 前后台 / 提醒 ACK）与其余 AR 批次未启动。

证据目录：`%LOCALAPPDATA%\car-agent\artifacts\AR03-20260908`——构建 stdout/result.json、两次主动中止的日志、
逐项 XML/PNG、三条 A/B 时间线（`cellA3-natural.txt` / `cellA4-stopped.txt` / `cellB-control.txt`）、取证脚本 `probe.py`；
不含原始音频、图像、token 或密钥。

**九、提交**

| SHA | 内容 |
|---|---|
| `8bb13ab80a1479e5e90c56f51228e20590717778` | 首版代码 + 测试（mobile 5 个新文件 + 共享 voiceLoop 追加）。**该版本的停播顺序有缺陷**，不作为验收锚 |
| `4544058f10bd380870601268e2585e1bb6d2234f` | 首版文档 |
| `fe798ccb2de476e6a933b6ab06f0b79e27ef60f2` | 停播顺序修复 + `core/voice/stopPlayback.ts` + 两条顺序回归。**该版本的缓冲段仍无停播入口**，不作为验收锚 |
| `4014a572a21286ec841a9d9197fa77d21e5cd25b` | 顺序缺陷的记录 |
| `b5c471832061e4744dc42128e346462be6f41bc8` | 播放事实拆两轴 + `canStopPlayback` + 十条相关用例。**上表全部本地读数与后续 APK 绑定这个 SHA** |
| 后续文档提交 | 只回填入口与状态，不转称上述测试是新版本的读数 |

~~~text
批次：AR03
对应 R 编号与关闭/剩余项：R06 客户端修复完成、设备验证未做；R09 横屏部分客户端修复完成、设备验证未做；R09 跨页部分属 AR04 未启动
代码 SHA：b5c471832061e4744dc42128e346462be6f41bc8（8bb13ab 的停播顺序、fe798cc 的缓冲段两处缺陷均在同批修掉）
APK 构建行与设备：`v0.1.0 · prod · b5c471832 · 2026-09-08 08:10`，SHA-256 0583e297…；OPPO PEUM00 08:26:38 装机，设备文件同哈希
云端 release SHA、provider/model：本批未涉及真栈
本轮执行的检查与原始证据：mobile 70 suites / 695 tests + tsc exit 0；hmi 308/308 + Vite build；15 条变异注入全部判红；OPPO 上同题 A/B（停播窗口 ≥15.7s → 单个采样）与 8 次播报零麦克风
失败/阻塞/污染样本：reanimated 官方 mock 在本环境不可用（已手写替代）；测试渲染器不跑布局（横屏用例手动放 onLayout 并先证层在场）；ColorOS 拒绝 adb 改动画/尺寸/密度/旋转 ⇒ driving-landscape 本机不可达；升层与播报结束同时发生，n 太小未定性
后续批次受到的影响：AR04 接手跨页宿主与 ACK 时，语音层的「覆盖域」边界（voice-sheet-scope）是既有锚点；AR07/AR08 的声学与时延基线不受本批影响
~~~
