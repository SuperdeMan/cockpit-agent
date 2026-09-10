# Android 页面打磨分批计划（批 A–G）

> 状态：**已批准，一次性推进**（用户 2026-09-10「goal 都给我，我一次性完整推进」）；J1–J5 按 §「裁决定案」执行；Goal 文本见文末总表。本文件落盘时零产品代码改动。
> 交付对象：mobile（批 A–F 纯 JS，D 的配置改动随第一次构建）；批 G 含 `orchestrator/cloud` 一处服务端改动。
> 关联：[页面展示层评审](../reviews/2026-09-10-android-ui-polish-review.md)（P/D/V 编号出处）；判据源 `mobile/src/core/presence/presence.ts`、`orbPolicy.ts`、`ui/tokens.ts`、`ui/theme.ts`；AR01–AR11 状态见 [分批处理建议](2026-09-07-android-review-remediation-batches.md)。
> 评审基线 `b7d24cf`；固定包取证按评审 §7。

## 0. 边界（各批共用）

- 不改 `hmi/`、不改 proto；后端只有 P10 的服务端半（单列、走正常 deploy 授权）。
- 批 A–C 零原生重建，改完直接 `-Release -Variant prod` 出常驻包；批 D 并入下一次必需的原生重建。
- 先测后码；每条新判据只放一处（`presence.ts` / `tokens.ts` / 一个组件），并配一条会红的变异。
- 每批一个可验收结果：OPPO 固定包截图清单 + jest/tsc/lint + Maestro 分轨；Xiaomi 只在批收口时截四格（评审 §5）。
- 不做：v1 路径删除（J1 裁后另开）、会话持久化（J3 裁后另开）、引导页换账号登录（AM5-01）。
- 已有的 AR 未签收项不因本文变化；本文不重排 AR07/08/10 的优先级。

## 批 A｜对话页信息层级（建议第一批，纯 JS，一天内闭合）

**结果：** 对话页每一屏只出现一份状态、一份推荐、两颗可操作光球；键盘、语音层、行车档三种遮挡下内容不被切半。

范围：P01 P02 P03 P04 P05 P06 P07 P08 P09 P12 P14 P28 P29；顺带复核 V8（深链进设置页的返回箭头）。

| 判据 / 改动 | 落点 | 说明 |
|---|---|---|
| `capsuleVisible(snapshot, dockVisible)` | `core/presence/presence.ts` 新增纯函数 | `input === 'voice-sheet'` ⇒ false（层内已有一份）；`dockVisible` 且胶囊为 attention 类（等你确认 / 还差一个信息）⇒ false（Dock 已在说）。对话页 `ChatScreen` 与支持页 `AssistantSurface` 都改为只读它，删掉支持页里那条内联的 `input` 判断 |
| chips 来源 | `Composer` 的 `quickCommands` 改为 `chips: FollowUpChip[]` 由宿主算；`ChatScreen`：无消息 ⇒ `[]`（欢迎态自己有三条推荐）；有消息 ⇒ `followUpChips(最近助手回答.followUp, core.candidates, driving ? 3 : MAX_CHIPS)` | 复用 `core/session/followUps.ts`，不新造判据；空数组时整行不渲染 |
| chip 触控目标 | `Composer` chip `minHeight: scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)` | 泊车 48 / 行车 56，与 `FollowUpChips` 同一表达式 |
| 欢迎态在键盘下的形态 | `ChatScreen::Welcome` 外层改 `ScrollView`（`keyboardShouldPersistTaps="handled"`），`facts.keyboardVisible` 时大球 88→56、推荐块保留 | 事实来自 `InteractionScope.keyboardVisible`（已有），不加新监听 |
| 助手气泡去头像 | `MessageBubble` 删除左侧 `AuroraOrb`；气泡 `maxWidth` 92% ⇒ 100%，左对齐 | 活跃态（思考 / 流式）的动效由气泡内 `ThinkDots` / `StreamCursor` 表达，顶栏与 Composer 光球照旧 |
| 长按 = 复制正文 | `MessageBubble` 两种气泡 `onLongPress` ⇒ `Clipboard.setStringAsync(msg.text)`，提示「已复制」；`trace_id` 复制移到 `/turn-timeline`（那一页已有复制 JSON） | 观测台排障通道不丢，只换位置 |
| 三处小目标 | `ProcessFold` / `receipt-toggle` / follow-up 链接 `minHeight: 44` + `hitSlop 2` | |
| 语音层底缘 | `VoiceSheet` 的 `ScrollView` `contentContainerStyle.paddingBottom += 24`；底缘 24dp 渐变遮罩（`experimental_backgroundImage: linear-gradient(transparent, 壳底色)`），`pointerEvents="none"` | 零依赖；壳底色与 `shellTint` 同源 |
| 空转写占位 | `VoiceSheet`：`user.text` 为空且 `capturing` ⇒ 灰字「在听…」，不渲染光标 | |
| 行车档实色层 | `ChatScreen` 给 `VoiceSheet` 传 `solid={snapshot.driving}`（支持页已是 `solid`） | 泊车真模糊路径逐字节不变；`sheetHeight.ts` 不受影响 |
| 隐藏点不动的键 | `Composer`：`inputMode === 'hidden' && !keyActive` ⇒ 不渲染发送键 | 忙 / 出声时照旧可点 |
| 机器意图名兜底 | `FocusDock::CommitmentCard`：`summary` 命中 `^[a-z_]+(\.[a-z_]+)+$` ⇒ 标题「待确认的车辆操作」，原文降为说明行 | 只是兜底；服务端出中文 summary 归 J4 |
| 文案 | 欢迎态「点一下光球说话，或点指令试试」；长按作次要说明 | |

反向验证（每条改回去必须只红对应用例）：`capsuleVisible` 的两个分支各去掉一条；`Composer` 无消息时仍渲染 chips；`MessageBubble` 恢复头像；行车档 `solid` 改回 false；`FocusDock` 机器名兜底关掉。

验收物：评审 §7 的 12 张 OPPO 截图（重点 `welcome-keyboard`、`sheet-speaking-long`、`sheet-driving-resident`、`chat-confirm-dock`）；jest / tsc / lint 全绿；Maestro release 轨 04 / 09 / 10 / 11 + online 01 / 06 / 08（02 与 06 前提互斥、分两趟）；`landscapeDockReach.test.ts`、`assistantPresence.test.ts`、`presence.test.ts` 按新判据更新，不放宽既有断言。

批 A 追加两项（裁决 J2 / J5）：

- 首页示例顺序改跨能力：mobile 侧新增 `MOBILE_QUICK_COMMAND_ORDER`（同一集合、只换顺序：今天天气怎么样 / 附近的充电站 / 讲个笑话 / 打开空调26度 / 播放音乐 / 打开主驾座椅加热 / 导航去首都机场 / 我今天有点不开心），共享 `DEFAULT_QUICK_COMMANDS` 不动；`mergeStoredSettings` 只在存量列表与旧默认逐项相同时换成新顺序，用户自定义过的列表原样保留。
- 批 D 的 `app.config.ts` 两处颜色改动随批 A 的第一次构建一起进包。

Goal 文本见文末「Goal 文本总表」。

## 批 B｜设置页分级与开发者选项（纯 JS）

**结果：** 普通用户在设置页看不到任何工程入口与内部代号；工程入口一个不少，只是搬进「开发者选项」。

范围：P17 P18（隐藏）P19 D1 D2 D5 P21。

结构：

| 分组 | 内容 |
|---|---|
| 通用 | 主题 / 字号 / 保持常亮 / 无障碍（减少动效、减少透明度） |
| 助手 | 昵称 / 回答长度 / 模型偏好 / 首页示例 |
| 语音 | 语音输入（引擎 / 模型 / 语言）/ 播报（策略 / 触感 / 提示音 / 引擎 / 音色 / 试听）/ 免唤醒与唤醒词 / 语音链路（端到端同意流程不变） |
| 隐私 | 记忆 / 定位 / 看图问答 / 隐私记录（用户版采集状态：隐私栏的行 + 最近激活日志）|
| 账号与连接 | 身份摘要（服务端）/ 能力摘要 / 能力开关 / 设备角色与行车档 / 重新配置连接 |
| 开发者选项（默认隐藏） | 实验室四开关 / 状态画廊 / 卡片画廊 / 在场轨迹 / 轮次时间线 / 原生状态 / 材质 spike / JSON 版采集状态 / dev 变体的操作诊断 |

显隐判据只放一处：`core/diagnostics.ts` 新增 `developerOptionsVisible(settings, buildInfo)` = `variant !== 'prod' || settings.developerUnlocked`；解锁 = 构建行连点 7 次（写 `developerUnlocked: true` 到 settings，`buildMeta` 键集不变）。

文案改写表（before → after）：

| 位置 | 现文案 | 改后 |
|---|---|---|
| 分区标题 | 进阶语音（M4） | 免唤醒与端到端 |
| 分区标题 | 实验室（UX v2.1） | 开发者选项 · 实验开关 |
| 开关 | 光球状态锚 + 状态胶囊 | 新版状态显示（关闭回旧版） |
| 开关 | 承诺面 Focus Dock | 待办卡（关闭回气泡内确认） |
| 开关 | 减少动效（强制） | 减少动效 |
| 链接 | 材质 spike（B3：真模糊 vs G1-tint 对照） | 材质对照 |
| 服务器区 | `https://…:8443` + `token ····j2_Z` | 「已连接 · car-agent-dev」+「访问令牌已保存」；完整地址进「重新配置」页 |
| 授权来源 | PoC 默认放行（不是真实授权）/ 无授权（fail-closed） | 演示模式（未做真实授权）/ 未授权 |
| 能力摘要 | 当前服务端还不支持能力摘要（旧版本） | 服务端暂不提供能力列表 |
| 试听失败 | 没有出声：minimax 这个引擎没返回音频（后端没配它的 key…） | 这个音色暂时不可用，换一个试试 |
| 车辆页脚 | 车况镜像 · 与座舱实时同步（只读） | 与座舱实时同步 |
| 车辆空态 | 等待车况镜像…（连上网关后 vehicle_state 帧会推全量） | 还没收到车况，连上座舱后会自动显示 |

自动化影响：`e2e/subflows/set-switch.yaml` 按 testID 找开关，实验开关搬进开发者区后子流先解锁再滚动（加一步「连点构建行 ×7」）；`diagnosticRoutes.test.ts` 改为断言「prod 未解锁 ⇒ 六条链接不在树里；解锁 ⇒ 全在」；`settingsMeta.test.ts` 键集不变。

验收物：`settings-top` / `settings-bottom` 两张（prod 未解锁应无开发者区），解锁后再两张；Maestro release 轨 06 / 11。

## 批 C｜车辆页与视觉系统（纯 JS）

**结果：** 车辆页没有一个英文键名或 `null`；全 App 不再用 emoji 当图标；最小字号与对比度有判据守。

范围：P20 P15 P16 P22。

| 改动 | 落点 | 判据 |
|---|---|---|
| 车态键表与值枚举 | `VehiclePanel.tsx` `KEY_LABEL` / `displayValue` | 键集真源是 `orchestrator/edge/knowledge/commands.yaml` 的对象清单；值枚举表覆盖 locked/unlocked/open/closed/folded/unfolded/playing/paused/stopped；`null`/`undefined` 行不渲染；未知键收进折叠「其他」。jest 读一份入库的脱敏 `vehicle_state` 样本（取自真栈帧，AR04 取证目录已有），断言样本出现的每个键都有中文标签——**跨进程消费用测试对账声明源**，不复制第二份表 |
| 线性图标八枚 | `ui/icons.local.ts` 追加 warning / refresh / camera / chat / bolt / check / clock / pin（24×24、1.8 stroke，同现有三枚格式） | 第一步换 Dock / 胶囊 / 气泡 / 提醒段；第二步换卡片族；能力开关不渲染共享 `AGENT_CATALOG.icon` |
| 字号与对比度 | `tokens.ts` `micro: 12`；六处 `p.font(10)` 提到 11；`theme.ts` 浅色 `fg3` 0.60 → 0.66 | 对比度 jest 新增 `fg3/bg`、`amber/amberSoft`、`accent/accentSoft` 三对，深浅各一 |
| 地图 logo 避让 | `map.tsx` 信息条 `left` 留出高德 logo 宽度，或信息条上移一档 | 真机截图核 logo 完整 |

验收物：`vehicle-mirror`（真栈车态）、卡片画廊深 / 浅各一屏、`chat-confirm-dock`；jest 对账用例与三对对比度用例；变异：删一条 `KEY_LABEL` 即红。

## 批 D｜启动图与图标（随批 A 的第一次构建）

范围：P25 P26。`app.config.ts`：`expo-splash-screen` `backgroundColor` / `dark.backgroundColor` 改 `#06080F`，`imageWidth` 120；`adaptiveIcon.backgroundColor` 改 `#06080F`。CNG 构建每次都跑 prebuild，不需要额外的原生重建。验收：冷启动首帧底色（录屏或连续截图）、应用信息页图标底色；随批 A 的验包一起做。

## 批 E｜删除 v1 回滚路径（裁决 J1，先于批 B）

**结果：** 代码里只剩 v2 一条呈现路径；设置页不再有「光球状态锚 + 状态胶囊」「承诺面 Focus Dock」两个开关。

| 改动 | 落点 |
|---|---|
| 设置项 | `core/settings/store.ts` 删 `uxV2Presence` / `uxV2Dock`（`mergeStoredSettings` 容忍旧存量里的这两个键，读到就丢弃）；`SettingsScreen` 删两枚开关 |
| 对话页 | `ChatScreen.tsx` 删 `HF_LABEL` / `HF_DOT`、v1 免唤醒状态条、v1 通知条、v1 PTT 提示行、v1 连接 pill、`linkWarn` 弱网横幅与其定时器、`legacyOrb` / `legacyHint`；`MessageBubble` 删 `inlineConfirm` 与气泡内确认按钮（Dock 是唯一确认入口） |
| 自动化 | 删 `e2e/02-danger-confirm-cancel.yaml` 与 `subflows/set-dock.yaml`（06 覆盖同一业务）；`set-switch` 子流与 `tools/set_switch.py` 去掉对这两个开关的引用；`e2e/README.md` 与 `config.yaml` 同步；`assistantPresence.test.ts` / `landscapeDockReach.test.ts` / `settingsMeta.test.ts` / `diagnosticRoutes.test.ts` 按新键集更新，不放宽既有断言 |

反向验证：删掉后用 `rg -n "uxV2Presence|uxV2Dock|inlineConfirm|HF_LABEL|linkWarn" mobile/` 必须零命中（测试里的旧存量容忍用例除外）。验收：jest / tsc / lint；Maestro release 轨 06 / 09。

## 批 F｜聊天基线（裁决 J3）

**结果：** 冷启动能看到上次的对话；长对话能一键回到最新；失败的请求能重发；记录里有时间分隔。

| 改动 | 落点 | 判据 |
|---|---|---|
| 持久化 | `core/session/history.ts` 新增：只读快照 = 最近 50 条 `messages`（去掉 `pending` / `streaming` / `processActive` 标志与草稿）、对应 id 的 `turnMeta` / `confirmLog` / `interruptedIds` / `s2sIds` / `visionIds`；键 `xiaozhou.history.v1:<sha256(edgeUrl|token)>`，换账号或换服务器绝不串；`wiring.ensureWired` 建好 core 后 `core.restore(snapshot)`；每次 `final` / 终态后节流写入 | **不持久化** `pendingOps` / `pendingLocationText` / `queued` / `uncertainIds` / `proactiveDeliveries` / `issues` / `drivingEdge`（各有 TTL 或服务端台账）；恢复不触发播报、不发 ACK、不重发；`restore` 只在 `messages` 为空时生效 |
| 清除入口 | 设置页「隐私」组「清除对话记录」（需二次确认） | 清除后当前会话与存量同时清空 |
| 时间分隔 | store 侧 `messageAt: Record<id, number>`（不改共享 `Msg`），`ChatScreen` 按相邻消息间隔 ≥ 5 分钟插分隔行：今天 `HH:mm` / 昨天 `HH:mm` / `M月D日 HH:mm` | 纯函数 `timeDividers(messages, messageAt, now)` 进 jest |
| 回到最新 | `FlashList` 离底超过一屏且有新内容时，Composer 上方浮出「↓ 最新」胶囊（实色底，与 Dock 不重叠） | 判据 `showJumpToLatest(offsetFromBottom, viewportH)` |
| 重发 | `msg.error` 或 `uncertainIds` 命中的助手气泡下给「重发」：取紧邻上一条用户原话，**新 request_id** 走 `core.send`，不复用旧 id（M3-W 的「不自动重发」不变，这是用户手动发起） | 只对这两类气泡渲染；发出后旧气泡标「已重发」 |

验收：`chat-3turns` 冷启动前后各一张；jest 覆盖 restore 不越账号、不触发 speech；Maestro release 轨 03（断网补达）不回归。

## 批 G｜承诺卡人话摘要（裁决 J4，服务端 + 客户端兜底）

**结果：** 承诺卡标题永远是人话，不再出现 `trunk.open` 这类机器意图名。

- 服务端：`orchestrator/cloud/contracts.py::action_summary` 现在直接返回 `step.intent`（有槽值时 `intent（k=v）`），并标 `summary_source=capability`。改为：按声明源把 `<object>.<operate>` 解析成「操作动词 + 对象 `display_name`」（对象名来自 `orchestrator/edge/knowledge/commands.yaml` 的 `display_name`，动词用现有 knowledge 里已经有的中文表，**不在 cloud 新造第二份词表**；用现有 loader 读，不复制 yaml）；解析不到就走既有的 `user_text` 回退并标 `summary_source=utterance`。用例：`trunk.open` → 「打开后备箱」；未知 intent → 用户原话；断言输出永不匹配 `^[a-z_]+(\.[a-z_]+)+`。跑 `orchestrator/cloud` 单测与四道门禁；deploy 走正常 dry-run → apply 授权。
- 客户端：批 A 的机器名兜底保留作第二道防线；`commitmentTitle` 加同一条正则判定，命中时不用服务端摘要而回落原话。

验收：离线契约用例；发布后真栈复验一次「open the trunk」的确认卡标题（绑 release SHA）。

## 执行顺序与授权边界

顺序：**A（含 D）→ E → B → C → F → G**。A 之后构建一次取第一轮截图；F 之后构建一次取最终包；中间批以 jest / tsc / lint 与最新包上的 Maestro release 轨为准。每批回填的截图必须来自含该批代码的包。

| 已由本轮 Goal 覆盖（不再逐项问） | 仍需到点单独授权 |
|---|---|
| `mobile/` 与 `orchestrator/cloud` 的源码、测试、文档；本地 `git commit`（每批一次，只含本批文件） | `git push`（先列完整 `origin/main..HEAD`） |
| `build_mobile.ps1` 的镜像同步（`robocopy /MIR` → `D:\Android\builds\xiaozhou-mobile`、`D:\Android\builds\hmi\src`；首次构建前打印解析后的绝对路径与排除项核对一次） | `cloud deploy --apply`（先 dry-run） |
| OPPO 测试机：装 prod 包、正常 UI 操作、截图、`uiautomator dump`、App 内设置切换（回读 + 恢复） | `.env` / 密钥 / CI/CD / 数据库 schema |
| 云栈只读 `status` 与正常会话请求 | Xiaomi 对照机的任何装包与操作 |
| | 系统级设置（字号 / 网络 / 权限 / 省电）、删除非本批生成的文件 |

## 裁决定案（2026-09-10）

| # | 定案 | 落点 |
|---|---|---|
| J1 | 删除 v1 回滚路径 | 批 E |
| J2 | 首页示例改跨能力顺序，只动 mobile 侧 | 批 A |
| J3 | 持久化最近 50 条只读记录，不持久化挂起 / 草稿 / 队列 | 批 F |
| J4 | 服务端出中文摘要为主，客户端兜底为辅 | 批 G + 批 A |
| J5 | 不单独重建，随批 A 的第一次构建 | 批 D |

## Goal 文本总表

### Goal 0｜连续推进（推荐直接用这一条）

```text
按 docs/design/2026-09-10-android-ui-polish-batches.md（评审出处 docs/reviews/2026-09-10-android-ui-polish-review.md）一次性推进批 A→E→B→C→F→G，批 D 的 app.config.ts 改动并入批 A 的第一次构建；J1–J5 按文档「裁决定案」执行，不再询问。

执行方式：先测后码；每条新判据只放一处（presence.ts / tokens.ts / 单个组件）并配一次会红的变异，按字节恢复；每批本地 git commit 一次（只含本批文件，提交信息带批号）；jest / tsc / lint 全绿才进下一批，既有断言不放宽；不动 hmi/、不改 proto、不改 sheetHeight.ts。构建按 docs/guides/android-build-and-device-validation.md：先核 dev-stack.local 与 target、镜像目录与 Gradle 无他人占用、内存余量；用仓库外目录的 run-build.ps1 后台构建并留 result.json；至少构建两次（批 A 后、批 F 后），每批回填的截图必须来自含该批代码的 prod 包。装机与验包按指南 §5（lastUpdateTime 变、非 DEBUGGABLE、设置页构建行、SHA-256 端本一致）。取证按评审 §7：OPPO 12 张固定状态 + 同帧 uiautomator dump，命名 <状态>-<包短SHA>.png；改过的 App 内设置回读并恢复。Maestro release 轨用 -e APP_LAUNCH_MODE=release --no-reinstall-driver；批 E 删除 02 之后回归清单为 04 / 09 / 10 / 11 + 01 / 03 / 06 / 08。

授权范围（用户 2026-09-10 裁定，不再逐项问）：mobile/ 与 orchestrator/cloud 的源码、测试、文档；本地 git commit；构建脚本的镜像同步（robocopy /MIR 到 D:\Android\builds\xiaozhou-mobile 与 D:\Android\builds\hmi\src，首次构建前打印解析后的绝对路径与排除项核对一次即可）；OPPO 测试机装包、正常 UI 操作、截图、dump、App 内设置切换（回读 + 恢复）；云栈只读 status 与正常会话请求。仍需到点单独授权：git push（先列完整 origin/main..HEAD）、cloud deploy --apply（先 dry-run）、.env / 密钥 / CI/CD / schema、Xiaomi 对照机的装包与操作、系统级设置（字号 / 网络 / 权限 / 省电）、删除非本批生成的文件。等待授权时先完成其他独立工作，不空转。

完成判据：批 A–F 的代码、测试、变异证据、最终 prod 包与 12 张截图；批 G 的服务端改动本地 pytest 与四道门禁全绿、离线契约用例证明不再下发机器意图名，并备好 push / dry-run 材料；每批按文档「回填格式」回填状态、包 SHA 与未达项；Xiaomi 四格对照与 push / deploy 列成集中处理表交给用户。不把未截图的状态写成已验，不把「等待授权」写成「完成」。
```

### Goal A｜对话页信息层级（含批 D 与 J2）

```text
按 docs/design/2026-09-10-android-ui-polish-batches.md 批 A 实施（含批 D 的 app.config.ts 两处颜色与 J2 的首页示例顺序）：先写 capsuleVisible、chips 来源、timeDividers 之外的本批判据测试，再改 ChatScreen / Composer / MessageBubble / VoiceSheet / FocusDock；每条判据配一次变异反向验证并按字节恢复；不动 hmi/、不动后端、不改 sheetHeight.ts。改完本地 commit，出 prod 常驻包装 OPPO（按构建指南 §4–§5），按评审 §7 截 12 张固定状态并同帧 dump，重点核 welcome-keyboard、sheet-speaking-long、sheet-driving-resident、chat-confirm-dock，顺带核深链进设置页有返回箭头；Maestro release 轨 04 / 09 / 10 / 11 + 01 / 06 / 08。Xiaomi 本批不接。结束时回填批 A 状态、包 SHA 与未达项。
```

### Goal E｜删除 v1 回滚路径

```text
按 docs/design/2026-09-10-android-ui-polish-batches.md 批 E 实施：删除 uxV2Presence / uxV2Dock 设置项与 SettingsScreen 两枚开关（mergeStoredSettings 容忍旧存量键）、ChatScreen 全部 v1 分支（HF_LABEL / HF_DOT / v1 状态条 / 通知条 / PTT 提示行 / 连接 pill / linkWarn / legacyOrb / legacyHint）、MessageBubble 的 inlineConfirm 与气泡内确认按钮；删 e2e 02 与 set-dock 子流，更新 set-switch / set_switch.py / e2e README / config.yaml 与四个相关测试，不放宽既有断言。完成判据：rg 对这些标识零命中（旧存量容忍用例除外）、jest / tsc / lint 全绿、Maestro release 轨 06 / 09 通过；本地 commit 并回填。
```

### Goal B｜设置页分级与开发者选项

```text
按 docs/design/2026-09-10-android-ui-polish-batches.md 批 B 实施：设置页重排为「通用 / 助手 / 语音 / 隐私 / 账号与连接」五组 + 默认隐藏的「开发者选项」；显隐判据只放 core/diagnostics.ts::developerOptionsVisible（variant !== 'prod' || settings.developerUnlocked），解锁 = 构建行连点 7 次；按文档 §批 B 的文案表逐条改写，采集状态拆成用户版「隐私记录」与开发者版 JSON；set-switch 子流加解锁步骤；diagnosticRoutes.test 改为断言 prod 未解锁六条链接不在树里、解锁后全在；settingsMeta 键集不变。完成判据：jest / tsc / lint 全绿；Maestro release 轨 06 / 11；prod 包上 settings-top / settings-bottom（未解锁）与解锁后各两张截图；本地 commit 并回填。
```

### Goal C｜车辆页与视觉系统

```text
按 docs/design/2026-09-10-android-ui-polish-batches.md 批 C 实施：VehiclePanel 补齐 KEY_LABEL 与值枚举表（locked/unlocked/open/closed/folded/unfolded/playing/paused/stopped），null / undefined 行不渲染，未知键收进折叠「其他」；入库一份脱敏的真栈 vehicle_state 样本，jest 断言样本里每个键都有中文标签（键集真源仍是 commands.yaml，不复制第二份表）；icons.local.ts 追加 warning / refresh / camera / chat / bolt / check / clock / pin 八枚并替换 Dock / 胶囊 / 气泡 / 提醒段与卡片族的 emoji，能力开关不渲染共享 AGENT_CATALOG.icon；tokens.ts micro 提到 12、六处 10pt 提到 11、浅色 fg3 提到 0.66，对比度 jest 新增 fg3/bg、amber/amberSoft、accent/accentSoft 深浅各一；地图页信息条为高德 logo 留位。变异：删一条 KEY_LABEL 即红。完成判据：jest / tsc / lint 全绿；prod 包上 vehicle-mirror（真栈车态）、卡片画廊深浅各一屏、chat-confirm-dock；本地 commit 并回填。
```

### Goal F｜聊天基线

```text
按 docs/design/2026-09-10-android-ui-polish-batches.md 批 F 实施：新增 core/session/history.ts 持久化最近 50 条只读记录（键 xiaozhou.history.v1:<sha256(edgeUrl|token)>，换账号绝不串；不持久化 pendingOps / pendingLocationText / queued / uncertainIds / proactiveDeliveries / issues / drivingEdge；restore 只在 messages 为空时生效，不触发播报、不发 ACK、不重发），设置页「隐私」组加「清除对话记录」二次确认；store 侧 messageAt 与纯函数 timeDividers 做 5 分钟时间分隔；showJumpToLatest 判据驱动「↓ 最新」胶囊；error / uncertain 气泡给「重发」（新 request_id，旧气泡标已重发）。完成判据：jest 覆盖 restore 不越账号与不触发 speech；jest / tsc / lint 全绿；出最终 prod 包装 OPPO，补齐评审 §7 全部 12 张截图并核 chat-3turns 冷启动前后一致；Maestro release 轨全清单；本地 commit 并回填。
```

### Goal G｜承诺卡人话摘要（服务端 + 客户端兜底）

```text
按 docs/design/2026-09-10-android-ui-polish-batches.md 批 G 实施：orchestrator/cloud/contracts.py::action_summary 改为按声明源把 <object>.<operate> 解析成「操作动词 + 对象 display_name」（对象名读 orchestrator/edge/knowledge/commands.yaml，动词用现有 knowledge 里已有的中文表并经现有 loader 读取，不在 cloud 新造词表、不复制 yaml），解析不到走既有 user_text 回退并标 summary_source=utterance；用例 trunk.open →「打开后备箱」、未知 intent → 用户原话、输出永不匹配 ^[a-z_]+(\.[a-z_]+)+；客户端 commitmentTitle 加同一条正则兜底。跑 orchestrator/cloud 单测、四道门禁与全量固定口径；不 push、不 deploy，备好 origin/main..HEAD 清单与 dry-run 材料交用户授权；发布后在真栈复验一次「open the trunk」的确认卡标题并绑 release SHA。本地 commit 并回填。
```

## 回填格式（每批结束）

```text
批次：
代码 SHA / APK 构建行 / 设备：
截图清单完成度（12 张 / Xiaomi 4 格）：
jest / tsc / lint / Maestro 读数：
反向验证：
未达项与归属：
```

