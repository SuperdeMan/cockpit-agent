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

## 回填（2026-09-10 执行轮，Goal 0 一次性推进）

证据目录（仓库外）：`%LOCALAPPDATA%\car-agent\artifacts\POLISH-A-20260910\`（批 A 固定包 12 态 + 同帧 dump + 每态 JSON 说明 + 构建日志 / result.json / apk 哈希）与 `POLISH-F-20260910\`（最终包）。设备均为 OPPO PEUM00（`919fd6f9`，test）。变异反向验证由 `mutate.py` 逐条施加 → 跑对应用例 → 按字节恢复并核 sha256（`mutation_report.json`）。

### 批 A（含批 D 与 J2）

```text
批次：A（+D app.config 两处颜色、J2 首页示例顺序）
代码 SHA / APK 构建行 / 设备：7525784b6（追加修正 4720429 见下）/ xiaozhou-companion-prod-release-7525784b6-20260910-2104.apk，
  SHA-256 b52e2801…0c7b（本地 Get-FileHash 与设备 sha256sum 逐字相同）、签名 5e8f1606…、包内 variant=prod build=7525784b6、
  设置页底行「v0.1.0 · prod · 7525784b6 · 2026-09-10 20:46」/ OPPO lastUpdateTime 2026-09-10 21:07:06、flags 无 DEBUGGABLE；
  构建 -CompileJobs 1 + 128m/2048m，17m25s，1222 tasks（737 executed / 485 from cache）
截图清单完成度（12 张 / Xiaomi 4 格）：12/12 张（命名 <状态>-7525784b6.png）+ 补充 chat-3turns-card / exp-dock-*；Xiaomi 0/4（未接，见集中处理表）。
  逐态：welcome ✅（新文案、三条跨能力推荐、Composer 无 chips 行）/ welcome-keyboard ⚠（球缩 56、可滚动，但第三条推荐仍被 Composer 切半
  ⇒ 追加修正 4720429：键盘下去掉次要说明、收窄留白；在最终包复核）/ chat-3turns ✅（天气卡 + 笑话 + 「what day of the week」被服务端答成
  「unsupported datetime format」，属后端 info 域英文日期解析问题，记录不处理）/ chat-confirm-dock ⚠（Dock 钉 1 条 + 「open the charging port」
  用原话作标题（P10 客户端兜底生效，机器名未出现）+ 无「等你确认」胶囊（P28 ✅）；**三条并存未复现**：连发三条时前两条在第三条到达前已被
  服务端关闭或过期，Dock 只钉一条，`dock-others` 未出现）/ sheet-listening-empty ⚠（第一次 0.8s 已有 ASR partial「假」，0.35s 复取到
  「在听…」胶囊 + 上一轮转写；「草稿为空 ⇒ 在听… 占位」这一格在 adb 下不可复现——partial 与草稿同帧到达；dump 因收音动画失败）/
  sheet-speaking-long ✅（播报「总是」+ 长英文问题，点「播报中」胶囊升层：层内停止键、底缘渐隐、层外无胶囊；⚠ 用 am start 深链升层会经一次
  前后台切换把播报停掉，两次误取已改名 sheet-open-not-speaking-*）/ sheet-driving-resident ✅（可信车载平板 + 手动行车档：层常驻、实色壳、
  无发送键无输入框）/ settings-top ✅（V8：深链进入有返回箭头）/ settings-bottom ✅（本包仍是批 B 之前的分区；构建行与包一致）/
  vehicle-mirror ✅（真栈车态；本包仍直出英文键，批 C 后在最终包复核）/ map-two-points ✅（深链两点；高德 logo 在信息条下方完整可见 ⇒ P22 不改）/
  onboarding-dark ✅ 截图、dump 失败（引导页光球动画不受减少动效约束，uiautomator 拿不到 idle）。
  设置指纹：取证期间 App 内「减少动效」开（对话页 dump 必需）、uxV2Dock 原本即开、播报/角色/行车档按态切换，结束时全部回读恢复
  （drivingManual 第一次回读不符 after=True，重试一次 OK）。
jest / tsc / lint / Maestro 读数：批 A 提交时 jest 84 suites / 907 tests、tsc 0、lint 0；Maestro release 轨**未在本包跑**（合并到最终包一趟，见批 F）。
反向验证：12 条变异（capsuleVisible 两分支 / 无消息给 chips / 头像回来 / driving solid 关 / Dock 机器名兜底关 / 隐藏键渲染 / J2 迁移关 /
  层内占位关 / commitmentTitle 机器名判定关 / 键盘缩球关 / chip minHeight 回退）各自只红对应用例，全部按字节恢复；追加修正另一条变异（P03）。
未达项与归属：① 三条待办并存的截图（服务端窗口内并存条件未复现，归 AR05 真栈复验）；② 「草稿为空」占位格（adb 不可造，归 AR10 真人轮）；
  ③ 键盘弹起期间两次取证 Dock 缺席、胶囊顶替，键盘收起后 Dock 在场——机制未钉死（对照实验里 adb 点输入框未能稳定拉起 IME），最终包复核。
```

### 批 E

```text
批次：E（裁决 J1，删除 v1 回滚路径）
代码 SHA / APK 构建行 / 设备：3d04c77（纯 JS，随最终包进包）
截图清单完成度：不适用（无新界面）；Maestro 06 / 09 在最终包跑
jest / tsc / lint / Maestro 读数：jest 84 / 909、tsc 0、lint 0；`rg uxV2Presence|uxV2Dock|inlineConfirm|HF_LABEL|linkWarn|set-dock|confirm-accept`
  只剩 store.ts 的旧存量容忍（读到即丢）、其注释与三条容忍用例
反向验证：3 条（容忍不丢键 / 气泡内确认回来 / Dock 开关回来）各自只红对应用例，按字节恢复
未达项与归属：无；02 与 set-dock 已删，e2e/README、mobile/README、06 头注同步，06 加了「Dock 在场时无胶囊」阴性断言（P28）
```

### 批 B

```text
批次：B（设置页分级 + 开发者选项）
代码 SHA / APK 构建行 / 设备：0cb136c（纯 JS，随最终包进包）
截图清单完成度：settings-top / settings-bottom（未解锁）与解锁后两张在最终包取；本包（7525784b6）上的两张只证明批 A 状态
jest / tsc / lint / Maestro 读数：jest 85 / 918、tsc 0、lint 0；Maestro 06 / 11 在最终包跑
反向验证：4 条（解锁位失效 / 一律显示 / 解锁点数改 6 / 「（强制）」文案回来）各自只红对应用例，按字节恢复
未达项与归属：`set-switch` 子流**没有**加解锁步骤——批 E 之后实验开关只剩「减少动效」「减少透明度」两枚，按文档它们住在「通用 · 无障碍」，
  不在开发者区，自动化不需要解锁；开发者区七条链接（含 JSON 版采集状态）prod 默认隐藏、构建行连点 7 次解锁、可再隐藏。
  「首页示例」以移除 / 添加 / 恢复默认的小编辑器实现（此前 mobile 侧没有编辑入口）。
```

### 批 C

```text
批次：C（车辆页 + 视觉系统）
代码 SHA / APK 构建行 / 设备：10e93ca（纯 JS，随最终包进包）
截图清单完成度：vehicle-mirror（真栈车态）、卡片画廊深 / 浅、chat-confirm-dock 在最终包取
jest / tsc / lint / Maestro 读数：jest 87 / 934、tsc 0、lint 0
反向验证：7 条（删一条 KEY_LABEL / 删一个值枚举 / null 行渲染 / 浅色 fg3 回 0.60 / 浅色 accent 回旧值 / micro 回 11 / 本地图标撞共享名）各自只红，按字节恢复
未达项与归属：① P22 地图信息条**不改**——7525784b6 截图里高德 logo 在信息条下方完整可见；② 评审点名的八枚图标里 warning / chat / clock / pin
  共享台账已有，本地只补 refresh / camera / bolt / check / square（本地同名会被合并顺序覆盖，已有用例守）；③ 对比度判据把浅色 accent / amber
  一并压深（#0369A1 / #92400E）：旧值压在各自 soft 底上只有 2.9:1 / 3.7:1，这是文档三对判据实测出来的、超出「只改 fg3」的范围。
```

### 批 F

```text
批次：F（聊天基线，裁决 J3）
代码 SHA / APK 构建行 / 设备：7fc8d98 / 最终包 xiaozhou-companion-prod-release-7fc8d9894-20260910-2345.apk（A+D+E+B+C+F 全部进包），
  SHA-256 ce72d484…70bec（本地 Get-FileHash 与设备 pm path + sha256sum 逐字相同）、包内 variant=prod build=7fc8d9894、
  设置页底行「v0.1.0 · prod · 7fc8d9894 · 2026-09-10 23:20」/ OPPO lastUpdateTime 2026-09-10 23:49:19、flags 无 DEBUGGABLE；
  构建 -CompileJobs 1 + 128m/2048m，result.json 起止 25m50s（23:19:40 → 23:45:30）、exitCode 0
截图清单完成度（12 张 / Xiaomi 4 格）：F 自己的四件——冷启动前后 chat-3turns-before-cold / chat-3turns-after-cold（force-stop 后冷启动，列表底部：
  三条危险动作的确认与过期提示原样回来）+ chat-3turns-after-cold-top（同一进程滚到顶：分隔线「昨天 23:50」+ 首轮天气卡 + 笑话；跨过零点后
  分隔线由「今天」变「昨天」）✅；时间分隔 chat-3turns「今天 23:50」✅；「↓ 最新」胶囊 chat-confirm-dock（离底一屏且有新消息到达时出现、
  与 Dock 不重叠；手动滚到顶而没有新消息时不出现，这是 awayCount 判据的本意）✅；「清除对话记录」入口在设置页「隐私」组 dump 里在场，
  动作没在真机按（会抹掉冷启动证据）；「重发」按钮真机未截——要一次失败请求（msg.error / uncertainIds），本轮没有自然发生，
  人为制造要开飞行模式（系统级网络设置，集中处理表 #5），jest 覆盖。Xiaomi 0/4。
jest / tsc / lint / Maestro 读数：jest 89 suites / 949 tests、tsc 0、lint 0（7fc8d98；批 G 不碰 mobile）；Maestro release 轨见下一节
反向验证：9 条（键忽略 token / 保留 pending 占位 / 不截 50 条 / restore 覆盖非空 / 分隔阈值改 1 分钟 / 半屏就出「最新」/ 重发键不渲染 /
  messageAt 不打戳 / 清除入口缺失）各自只红 history.test / messageBubble.test / diagnosticRoutes.test，按字节恢复（mutation_report.json 9 条 restored=true）
未达项与归属：① 「重发」真机截图（集中处理表 #5，或 AR10 真人轮）；② 第三轮「recommend a song for a rainy evening」被服务端答成
  「规划没有产出可执行的步骤，本轮没有执行任何操作。」+「换个说法再试」（后端 Planner 对英文点歌没有可执行步骤；UI 按 AR05 issue 行渲染正确，记录不处理）。
```

### 最终包（7fc8d9894）12 态复核 + Maestro

```text
批次：最终包复核（A+D+E+B+C+F 全部进包；G 在服务端，未 deploy）
代码 SHA / APK 构建行 / 设备：同批 F；OPPO PEUM00 919fd6f9（test）；证据 POLISH-F-20260910\<状态>-7fc8d9894.png + 同帧 dump + JSON 说明
截图清单完成度（12 张 / Xiaomi 4 格）：12/12 张 + 补充（chat-3turns-card / chat-confirm-dock-keyboard / settings-dev-unlocked(-bottom) /
  card-gallery 深浅各 4 张 / welcome-or-chat-light / settings-role-readback / settings-theme-readback）；Xiaomi 0/4。
  逐态：welcome ✅ / welcome-keyboard ✅（4720429 修正生效：三条推荐全在键盘上方、无次要说明）/ chat-3turns ✅（分隔线、天气卡、笑话；第三轮见批 F ②）/
  chat-confirm-dock ✅（Dock 钉「open the trunk」原话 + 「另有 1 个待处理 ›」——dock-others 首次在真机出现、两条并存；「↓ 最新」胶囊在场；无「等你确认」胶囊）
  + chat-confirm-dock-keyboard ✅（键盘弹起 Dock 仍在场 ⇒ 批 A 未达项 ③ 在本包不复现；机制仍未钉死，只记录本包读数）/
  sheet-listening-empty ⚠（同批 A：0.35s 层已开、转写区仍是上一轮原话「open the fuel tank cover」+ 状态字「在听…」，partial 先于占位窗口到达，
  adb 造不出「转写为空」）/ sheet-speaking-long ✅（「停止播报」键、底缘渐隐、层外无胶囊、Composer 停止方键）/
  sheet-driving-resident ✅（可信车载平板 + 手动行车档：实色常驻层、无输入框无发送键）/
  settings-top ✅（冷启动直落 /settings：**无返回箭头**——deepLink.ts 冷启动分支交回 router、栈里只有设置页；温深链 settings-role-readback /
  settings-theme-readback 有「←」，V8 结论不变，批 A 那张也是温深链）/ settings-bottom ✅（prod 锁定：无开发者区，底行 v0.1.0 · prod · 7fc8d9894 · 2026-09-10 23:20）
  + settings-dev-unlocked ✅（构建行连点 7 次 ⇒ 开发者选项 7 条链接 + 「隐藏开发者选项」；再点隐藏 ⇒ 回读消失）/
  vehicle-mirror ✅（真栈车态，键与值枚举全部中文，仅单位 km 为拉丁字母）/ map-two-points ✅ / onboarding-dark ✅（dump 失败：引导页光球不受减少动效约束，同批 A）/
  card-gallery-dark / -light ✅（浅色下 accent #0369A1 与 amber #92400E 压 soft 底可辨）/ welcome-or-chat-light ✅。
  设置指纹：取证期间「减少动效」开、播报「总是」→「自动」、角色「可信车载平板」→「手持」、行车档开→关、主题「浅色」→「跟随系统」，
  结束时全部回读恢复（行车档第一次点击未生效、第二次回读 checked=false；角色 ChoiceRow 不暴露 selected，靠截图回读）。
jest / tsc / lint / Maestro 读数：Maestro release 轨（-e APP_LAUNCH_MODE=release --no-reinstall-driver）在 7fc8d9894 上两趟：
  第一趟 04 ✅ 65.7s / 09 ✅ 270s / 10 ✅ 190.8s / 11 ✅ 15.7s / **01 ❌** 230s（用户气泡在、「天气 · .*」45s 内不可见——截图里回答文字已到、
  气泡下沿被 Composer 切掉、卡片在折叠线下）/ **03 ⛔ 挂起** 464.7s（开飞行模式后 inputText 打到第 4 个字停住，driver 的 hierarchy 取数不回；
  flow 没走到「关飞行模式」那一步 ⇒ 手机留在飞行模式）/ 06 ❌ 338s、08 ❌ 361s（都是在 03 留下的飞行模式里跑的，tap 挂起，不算读数）。
  关飞行模式后 tailnet **20 分钟没恢复**（VPN 显示 CONNECTED、MagicDNS 与对端全不通，App 停在「正在重连…」），
  `am force-stop com.tailscale.ipn` + 重启 Tailscale 才通；第二趟（网络恢复后）06 ✅ 286s（含「Dock 在场时无胶囊」阴性断言）/
  **08 ❌** 230.6s（键盘弹着点发送后，用户气泡「现在几点」不可见——同 01 一族：长列表 + 视口变小，新内容落在折叠线下）。
  根因与修正见「批 F 追加」；03 在追加包上再跑一次。
反向验证：不适用（本节只复核最终包）
未达项与归属：① 行车档 + 可信车载平板下冷启动直落 /settings，常驻语音层压住设置页下 40%（dump：voice-sheet [0,1183][988,1972]），
  set_switch.py 的滑动起点落在层里滚不动、行车档开关够不到；稍后从对话页温深链进设置页时层不在——机制未钉死，归 AR04「支持页在场（行车档）」复验；
  ② 角色 ChoiceRow 没有 accessibilityState.selected，自动化只能截图回读（无障碍缺口，归下一轮打磨）；③ 三条并存仍未复现（本包两条并存，dock-others 已在场）。
```

### 批 F 追加（e95d077：晚到的布局增高也贴底）

```text
批次：F 追加（Maestro 01 / 08 在最终包上红出来的同一族缺陷）
代码 SHA / APK 构建行 / 设备：e95d077（history.ts / ChatScreen.tsx / history.test.ts）/ 追加包 xiaozhou-companion-prod-release-e95d07725-20260911-0159.apk，
  SHA-256 069a45a4…052c（本地 Get-FileHash 与设备 pm path + sha256sum 逐字相同）、包内 variant=prod build=e95d07725、签名 5e8f1606…（同前两包）、
  OPPO lastUpdateTime 2026-09-11 02:00:14（前值 23:49:19）、flags 无 DEBUGGABLE；构建 -CompileJobs 1 + 128m/2048m，result.json 起止 15m35s
  （01:44:01 → 01:59:36，Gradle 报 14m47s，缓存热）、exitCode 0；证据目录 POLISH-F2-20260911\
根因：FlashList v2 的 `maintainVisibleContentPosition.autoscrollToBottomThreshold` 只在 `data` 变化那一刻检查「此前是否贴底」再 scrollToEnd
  （useBoundDetection.js：checkBounds 在 scroll 事件里记 pendingAutoscrollToBottom，effect 依赖 [data]）。回答的卡片是在文字之后才量出高度的
  **布局增高**、键盘弹起是**视口变小**——两者 `data` 都没变，它不跟。批 F 之前 force-stop 后列表是空的、一屏装得下所以看不出；
  批 F 恢复了 50 条历史，列表一长就稳定露出（01：天气卡压在 Composer 下；08：新用户气泡在折叠线下）。
修正：`history.ts` 新增 `STICK_TO_BOTTOM_THRESHOLD = 0.2` 与 `stickToBottom(offsetFromBottom, viewportH)`（视口未量到不贴）；
  ChatScreen 把同一个阈值给 FlashList，并在 `onContentSizeChange` 上按增高**之前**的离底距离（ref，不等 state）再贴一次；离底更远的人不被拽回。
  与 `showJumpToLatest`（离底超过一屏才出「最新」）互斥，用例钉了这条。
截图清单完成度：追加包上 F 证据重取（POLISH-F2-20260911\<状态>-e95d07725.png + 同帧 dump）：chat-long-reply-card ✅（50 条历史之上发天气问题、
  不滚动：整条回答含卡片与「数据来源 · 展开回执」行都在 Composer 之上——回执行下沿 y=1603 < Composer 上沿 1717，正是 01 红的那个形状）/
  chat-keyboard-send ⚠（adb `input text` 不拉 IME，键盘形状留给 Maestro 08 判）/ chat-before-cold → chat-after-cold ✅（force-stop 后冷启动直接落在列表底部，
  最后一轮原样在；issue 行与「播报中」胶囊按设计不持久化）+ chat-after-cold-top ✅（分隔线「昨天 23:50」+ 首轮）/ settings-bottom ✅（底行
  v0.1.0 · prod · e95d07725 · 2026-09-11 01:44）。A–E 的 12 态不重取（代码在两包一致，7fc8d9894 的证据继续有效）。
jest / tsc / lint / Maestro 读数：jest 89 suites / 951 tests、tsc 0、lint 0（e95d077）；Maestro 8 条在追加包 e95d07725 上整跑一趟（03 放最后）：
  04 ✅ 52s / **09 ❌** 111s（`scrollUntilVisible "另有 1 个待处理.*"` 30s 超时——Maestro 自己的失败截图里那一行已完整在屏上，是 driver 在带光球动画的画廊页上
  没检出，不是渲染回归；同一条在 7fc8d9894 上 ✅ 270s；复跑读数见下）/ 10 ✅ 232.3s / 11 ✅ 16.6s / **01 ✅ 189.9s**（7fc8d9894 上 ❌ ⇒ 修正生效）/
  06 ✅ 230.6s / **08 ✅ 192.7s**（7fc8d9894 上 ❌ ⇒ 修正生效）/ **03 ⛔ 挂起** 355.9s（这次走到「Tap on composer-send」停住，仍在「关飞行模式」之前
  ⇒ 手机再次留在飞行模式；关掉后 Tailscale 又是「CONNECTED 但 MagicDNS / 对端不通」，重启 Tailscale 才通）。
  09 复跑：**❌** 111.9s，同一步同一形状（失败截图里「另有 1 个待处理 ›」完整在屏、y≈1452），2/2 红；e95d077 相对 7fc8d98 只改了
  ChatScreen / history（画廊页不经过它们），判为 driver 在动画页上的检出问题、不动 flow；**09 在追加包上没有绿读数**，写成未达。
  汇总：追加包 8 条 = 6 ✅（04 / 10 / 11 / 01 / 06 / 08）+ 09 ❌（工具检出）+ 03 ⛔（飞行模式段挂起）。
反向验证：2 条（阈值放宽到整屏 / 不看视口是否量到）各自只红 stickToBottom 用例，按字节恢复
未达项与归属：① 取证第一趟全军覆没的原因是上一趟 Maestro 的 driver（dev.mobile.maestro）还活着 ⇒ uiautomator「could not get idle state」，
  `am force-stop dev.mobile.maestro` 后恢复（AR 余项那条判据再次命中）；② 追加包上没有重取 A–E 的 12 态（见上）；
  ③ **Maestro 03（断网入队恢复补达）两包三趟全部在飞行模式段挂起**（inputText 第 4 字 / tap composer-send），driver 的 hierarchy 取数不回、
  flow 走不到自己的「关飞行模式」。机制**未钉死**，最像的一条：`usePresence.needsTick` 在 `connStatus==='connecting'` 后 3s 内每秒 tick，
  断网后重连循环每次尝试都把 connChangedAt 归零 ⇒ 对话页几乎一直在每秒重渲，driver 永远等不到静止（在线时同一页 01/06/08/10 都能点）。
  批 E 删掉 v1 之后离线态只剩 v2 Dock + 胶囊这一条路，回不去了；要钉死得在飞行模式下用 uiautomator（开减少动效）看树是否每秒变——
  **2026-09-11 用户授权飞行模式探针后两条 App 侧假设都被推翻**：① 减少动效开着、飞行模式下对话页 uiautomator 30/30 次 idle、树签名只在
  胶囊切换时变一次（airplane-probe-2.log）——不是树每秒在跳；② 减少动效开着再跑一次 03（光球静止）仍在「Tap on composer-input」挂 322s
  （maestro03-reducemotion.log）——不是光球动画。剩下的变量在 Maestro driver 自己：飞行模式下它的 hierarchy 取数不回（同一时刻 uiautomator 能取）。
  下一步不在 App：拿 driver 的 logcat / `--debug-output` 看 gRPC 超时，或把 flow 的 `setAirplaneMode` 换成不经 driver 的断网方式。
  每趟都把手机留在飞行模式，靠 `cmd connectivity airplane-mode disable` + 重启 Tailscale 拉回（本轮三次）。**补达语义本身**本轮有一次真机旁证：01 趟留下的飞行模式期间发的
  「现在几点what is the weather in Shenzhen today」在 Tailscale 恢复后自动补发并出了天气卡（POLISH-F-20260910\probe-after-tailscale-restart-7fc8d9894.png），
  但那不是 flow 03 的读数，不算通过。
```

### 批 G

```text
批次：G（承诺卡人话摘要，服务端为主 + 客户端兜底）
代码 SHA / APK 构建行 / 设备：d532c6d（orchestrator/cloud：contracts.py / engine.py / tests/test_ar05_contracts.py；客户端兜底在批 A 7525784b6）；
  **已 push（2026-09-11，b7d24cf..bd71f89）、已 deploy**：dry-run 无 blocking_changes、基础设施摘要 499fc97c… 已批准、CI/CD 摘要未变；
  `deploy --apply` submitted 后 `status` 读到 release_sha = running_release_sha = d532c6d816e662fa60740565d6230774c02ff200、5/5 healthy、无 warnings；
  `verify` = verified（.artifacts/dev-stack-verifications/20260911T111910Z-d532c6d.json）；日志在 POLISH-F2-20260911\deploy-apply-d532c6d.log / verify-d532c6d.log。
  服务端代码相对 main 顶 bd71f89 无差异（e95d077 / bd71f89 只动 mobile 与 docs）。
截图清单完成度：真栈复验 ✅ confirm-title-after-deploy-e95d07725.png（OPPO 上 e95d07725 包对新发布发「open the trunk」：Dock 标题「打开后备箱」+
  「危险动作 · 需二次确认」+ 倒计时，不再是原话兜底、更不是 trunk.open；dump 因 Dock 倒计时拿不到，截图为准）
jest / tsc / lint / Maestro 读数：四道门禁 eval_skills / eval_exemplars / check_intent_gate（85+25 units）/ eval_capability_integrity 全 PASS，
  smoke_edge 13/13；orchestrator/cloud 目录 1311 passed / 1 skipped；全量固定口径（TZ=UTC0，-n 8 --dist worksteal）**8233 passed / 32 skipped / 0 failed**
  （8m14s，绑 d532c6d；上一基线 8231 / 32，新增两条为本批契约用例）——POLISH-F-20260910\pytest-full-d532c6d.log
反向验证：2 条（允许机器名当描述 / 直出 intent）各自红在 test_confirm_policy_summary… 与 test_action_summary_never_emits_a_machine_intent_name，按字节恢复
未达项与归属：① 文档写的「对象名来自 commands.yaml 的 display_name、用现有 loader 读」在云侧**不可行**——云侧镜像只 COPY cloud/security/observability/
  runtime/skills，没有 commands.yaml（runtime/intent_effect.py 头注同一事实）。改走 Registry 目录：端侧注册能力时的 description 就是
  capabilities.py::_describe 从 commands.yaml + 动词表机械生成的「打开后备箱」，engine 挂起时 list_agents() 现查（best-effort，取不到回退原话）
  ⇒ 云侧零新词表；② 旧 Agent 把 intent 当 description（本仓 test 夹具即如此）时回退原话并标 summary_source=user_utterance，
  客户端再用 isMachineIntentName 兜第二道。
```

### Xiaomi 对照四格（2026-09-11，用户接机后授权）

```text
批次：Xiaomi 对照（评审 §7 收口四格）
代码 SHA / APK 构建行 / 设备：追加包 e95d07725（同 OPPO 那份 APK，SHA-256 069a45a4…052c 端本逐字相同）/ Xiaomi MIX Fold 4 24072PX77C（5d432b6d，compare，
  HyperOS 3 / Android 16，折叠态外屏 1080×2520@480dpi = 360×840dp，横屏 840×360dp）；lastUpdateTime 2026-09-08 10:13:00 → 2026-09-11 20:02:57、
  flags 无 DEBUGGABLE、设置页底行「v0.1.0 · prod · e95d07725 · 2026-09-11 01:44」；证据 POLISH-XIAOMI-20260911\<状态>-e95d07725.png + 同帧 dump
截图清单完成度（Xiaomi 4 格）：4/4。settings-top ✅（冷启动直落 /settings，用户自己的主题「深色」未动）/ chat-3turns ✅（欢迎态起三轮：天气卡、笑话、
  点歌——这台上第三轮有正常回答，OPPO 上是 Planner 无可执行步骤，两机同一服务端 ⇒ 是模型侧方差不是客户端）+ chat-3turns-card /
  landscape-driving-sheet ✅（可信车载平板 + 手动行车档，`user_rotation=1` 横屏冷启动：实色常驻层、光球居左、无输入框无发送键）/
  landscape-driving-dock ✅（C 身份没有文本入口，这一格用「手持 + 手动行车档」发「open the trunk」：Dock「打开后备箱」+「危险动作 · 需二次确认」+ 倒计时，
  横屏行车档下 Dock 落在 Composer **之下**——与竖屏相反，属 B4 横屏可达性布局，记录不改）。
jest / tsc / lint / Maestro 读数：不适用（对照机只截图）
反向验证：不适用
未达项与归属：① HyperOS 上 `input tap` 在对话页对 Pressable 时灵时不灵（横屏发送键两次点不动，Enter 才发出；竖屏 Dock「取消」input tap 不动、
  150ms swipe-tap 才动；设置页 ChoiceRow / 开关 input tap 都正常）——取证判据照旧是回读，不是「点过了」；② 这台上「免唤醒」开关根本不渲染
  （handsFreeAvailability 判原生缺席），所以没有开麦风险也没有要还原的；③ 行车档下冷启动进设置页，常驻语音层同样压住下半段（与 OPPO 同形），
  改设置只能在层上方滚——已写进 xiaomi_cells.py。设备状态：user_rotation 0→1→0、accelerometer_rotation 0 未动、drivingManual 开→关、角色 可信车载平板→手持、
  减少动效 开→关（原值关），结束时 App force-stop（接机时它就不在前台）。
```

### 集中处理表（交用户单独授权）

| # | 事项 | 现状 | 需要的动作 |
|---|---|---|---|
| 1 | `git push` | **已做**（用户 2026-09-11 授权）：b7d24cf..bd71f89 共 10 个提交推到 origin/main | 无 |
| 2 | cloud deploy（批 G 服务端） | **已做**（同上授权）：dry-run 干净 → `--apply` → status 5/5 healthy、running_release_sha = d532c6d → verify = verified → 真栈复验 Dock 标题「打开后备箱」 | 无 |
| 3 | Xiaomi 对照四格（landscape-driving-sheet / landscape-driving-dock / chat-3turns / settings-top） | **已做**（用户 2026-09-11 接机授权）：装 e95d07725、4/4 截到，见上一节；改过的系统旋转与 App 内设置逐项回读还原 | 无 |
| 4 | 系统级设置（字号 / 网络 / 权限 / 省电）与 `.env` | 本轮自己的探针没碰；Maestro 03 按 Goal 点名的回归清单跑，它自带「飞行模式 10s → 恢复」 | 无需动作（记录用） |
| 5 | 「重发」按钮真机截图（批 F） | **探针做了（2026-09-11 授权），截不到，且发现前提不可达**：在线发一句、1.7s 后开飞行模式让它在飞 ⇒ 34s 后胶囊「已断开 · 消息会排队」、气泡保持 pending；关飞行模式重连后气泡标「发送状态未知（网络刚断过；连上后若无回音请再说一次）」但仍是「正在思考…」——`MessageBubble` 只在 `(error ∨ uncertain) ∧ !pending` 时渲染「重发」，而队列语义让在飞的那条**永远不离开 pending**（要么补达出回答、要么一直思考），所以断网这条路到不了「重发」；能到的只有服务端 error 事件那条路（`出错了：…` 气泡）。证据 POLISH-F2-20260911\chat-after-reconnect-e95d07725.png + airplane-probe-2.log | 这是批 F「失败的请求能重发」的一个设计缺口：uncertain ∧ pending 超过 N 秒没有回音时应转成可重发的终态（判据一处，配 jest）。本轮不改，归下一批裁决 |
| 6 | Maestro 03 在飞行模式段挂起（两包三趟）+ 关飞行模式后 Tailscale 不自愈 | **探针做了**：减少动效开着时，飞行模式下对话页 uiautomator **每次都能 idle**（60s 内 30 次 dump 全 ok、树签名只在胶囊切换那一刻变一次）、离线闲置 ≈2.3 帧/s（在线闲置 0 帧/s）⇒ 「树每秒在变」这条假设**被推翻**；driver 挂起不是树在跳。再把减少动效开着跑一次 03：仍在「Tap on composer-input」挂 322s ⇒ 光球动画也**不是**变量。两条 App 侧假设都排除，剩 driver 自身在飞行模式下的 hierarchy 取数（同一时刻 uiautomator 能取） | 归 Maestro 工具侧：看 driver logcat / `--debug-output` 的 gRPC 超时，或把 flow 的 `setAirplaneMode` 换成不经 driver 的断网方式；Tailscale 不自愈是手机侧现象，每次都靠 `cmd connectivity airplane-mode disable` + 重启 Tailscale |

