**Android 页面展示层评审：以「可交付给普通用户的 Android App」为尺子｜2026-09-10**

> 状态：评审结论已落盘；**本轮零产品代码改动、零构建、零装机**。分批打磨草案见
> [`docs/design/2026-09-10-android-ui-polish-batches.md`](../design/2026-09-10-android-ui-polish-batches.md)，待用户选批后实施。
> 与 [2026-09-07 完整评审](2026-09-07-android-ux-full-review.md)（R01–R15，行为与流程）互补：本文只看**页面展示层**——布局、层级、文案、视觉一致性、显示完整性与「测试味」，不重复 R 编号已覆盖的行为缺陷。
> 评审代码基线 `b7d24cf831ae1a6d8cbd7403a9134e061926f332`（mobile 最近改动 `fa6e87d`）；生产 release `74852a7`。

## 0. 结论先行

1. **视觉语言成立、信息层级欠打磨。** Aurora 光球 + 玻璃的品牌感已经形成，深浅主题、折叠形态、行车档、无障碍目标尺寸都有判据在守。差距不在「像不像一个好看的 App」，而在「每一屏是不是只说该说的话」：首屏同一组推荐出现两次、语音层升起时状态胶囊出现两次、承诺卡钉着时胶囊还在重复它、每条助手气泡都带一颗光球、快捷指令 chips 全程常驻，这些都是把固定高度花在零信息上。
2. **「测试味」集中在三处，而且都在 prod 常驻包里可见：** 设置页的「实验室（UX v2.1）」四个开关 + 「诊断与样本」六条链接；长按助手气泡复制的是 `trace_id` 而不是正文；车辆页把 `vehicle_state` 的原始键值直接镜像给用户（`cabin_temp` / `door_lock: locked` / `location: null`）。
3. **显示不全有 3 处实证、1 处疑似**（§4）：键盘弹起时欢迎态第三条推荐被遮半截；语音层底缘把答案切成半行且无渐隐；行车档语音层是染色玻璃而非实色，记录透出来与层内文字叠字；横屏 split 右列答案贴右缘疑似裁切（只在 Xiaomi 外屏可复核）。
4. **两台真机不必都接。** 打磨批的日常验证只用 OPPO；Xiaomi 只在每批收口时做一次同包只读对照，覆盖只有它能到的四格（§5）。
5. **建议顺序：** 先做纯 JS 的对话页层级批（一天内可闭合、零原生零后端），再做设置页分级与开发者区隐藏，再做车辆页/卡片一致性；启动图与图标并入下一次原生重建。有五项要用户裁决（§6）。

## 1. 本轮看了什么、没看什么

| 证据面 | 状态 |
|---|---|
| 源码 | `mobile/src/app/*`、`features/{chat,cards,settings,vehicle,assistant,stage}/*`、`ui/*`、`core/presence/*`、`core/settings/store.ts`、`app.config.ts`、`plugins/with-shortcuts.js` 全文；`core/session/store.ts` 只查持久化与时间戳 |
| 真机截图 | 仓库内 `docs/images/mobile-*.jpg`（`cd5c19b` 入库，2026-09-08）；仓库外 `%LOCALAPPDATA%\car-agent\artifacts\AR04-presence-20260909[-b2]`（包 `de2a556` / `1c6780744`，OPPO）、`AR05-device-20260909-b2`（包 `d425b9c2d`，OPPO）、`AR03-20260908`（包 `b5c471832`，含 Xiaomi 横屏 `x19`/`x24`）；`mobile/e2e/artifacts/b*.png` 只作形态参考（代码已多轮变更） |
| 设备 | 本轮 `adb devices` 为空，**没有新截图**；所有「实证」都标了截图来源与包号，「疑似」表示只有源码推断或截图分辨不清 |
| 未覆盖 | 语音听感、唤醒率、首音（AR07/08）；行为类缺陷（R01–R15）；真人可用性（AR10）；HMI 侧一律不动 |

## 2. 逐页发现（P 编号；★ = 建议本轮就做）

严重度：**高** = 用户会当成故障或看不懂；**中** = 明显的粗糙感；**低** = 细节。

### 2.1 对话页（`ChatScreen` / `Composer` / `MessageBubble` / `VoiceSheet` / `FocusDock` / `PresenceCapsule`）

| # | 严重度 | 现象 | 证据 | 建议 |
|---|---|---|---|---|
| ★P01 | 中 | 首屏同一组推荐出现两次：欢迎态三条 chip + Composer 的 chips 行又是同样三条 | `docs/images/mobile-welcome.jpg`；`ChatScreen.tsx:145`（`slice(0, 3)`）与 `Composer.tsx:170-188` | 无消息时 Composer 不渲染 chips 行；欢迎态保留三条推荐 |
| ★P02 | 中 | 快捷指令 chips 行在整段对话里常驻（固定 8 条，不随语境变）；文字路径没有 follow-up chips（只在语音层内有），却把 ~40dp 永久给了静态示例；泊车态 chip 高约 31dp，低于 48dp 触控目标 | `Composer.tsx:176-186`（`paddingVertical: 7`、只有行车才 `minHeight`）；`VoiceSheet.tsx:354-362` 才有 `FollowUpChips` | 有消息后 chips 行改为「最近一条助手回答的 follow-up + 候选集」（复用 `core/session/followUps.ts`），没有就收起；chip `minHeight` 泊车 48 / 行车 56 |
| ★P03 | 高 | 键盘弹起时欢迎态被压缩，第三条推荐「播放音乐」被 Composer 遮住一半 | `AR04-presence-20260909/chat-typed.png`（包 `de2a556`） | 欢迎态改可滚动，或键盘可见时隐藏推荐块并缩小大球 |
| ★P04 | 中 | 每条助手气泡都带 28dp 光球头像：360dp 宽屏上正文损失约 36dp；同屏最多 5 颗光球（顶栏 / 头像 / Composer / 层内 / 欢迎态），2026-09-07 评审已指出「让用户清楚哪一个能操作」 | `MessageBubble.tsx:158-160`；`docs/images/mobile-focus-dock.jpg` | 助手气泡去头像（或只在一段连续回答的第一条显示静态小球）；顶栏 + Composer 两个锚保留 |
| ★P05 | 高 | 语音层升起时状态胶囊出现两次：层内一份、层下面 `PresenceCapsule` 一份，两处文字逐字相同 | `AR03-20260908/r5-largefont-two-stops.png`（包 `b5c471832`，「播报中 · 说话可打断」×2）；`docs/images/mobile-voice-sheet.jpg`；`ChatScreen.tsx:458-469` 未按 `snapshot.input` 收敛，而支持页 `AssistantSurface.tsx:65` 已经这样做 | `snapshot.input === 'voice-sheet'` 时对话页不渲染胶囊，判据与支持页同一条 |
| ★P06 | 中 | 语音层内容在层底缘被硬切成半行字，没有渐隐也没有底部留白，读起来像裁切故障而不是「还能往下滚」 | 同上 `r5-largefont-two-stops.png` 底缘「哈哈再来一个…」 | `ScrollView` 底部 `paddingBottom` + 底缘 24dp 渐变遮罩（`experimental_backgroundImage` 的 `linear-gradient` 即可，零依赖） |
| P07 | 低 | 刚升层、还没识别出字时转写区只剩一根光标条，像残影 | `docs/images/mobile-voice-sheet.jpg`；`VoiceSheet.tsx:283-298`（`user.text` 为空仍渲染 `StreamCursor`） | 无文字时显示灰字「在听…」占位，不单独渲染光标 |
| ★P08 | 高 | 行车档语音层是 G1 染色玻璃（行车时不走真模糊），记录透出约 30% 与层内文字叠在一起；行车恰恰是最需要可读性的时刻 | `docs/images/mobile-driving.jpg`；`ChatScreen.tsx:280-283`（`!snapshot.driving` 才给 `blurTarget`）、`VoiceSheet.tsx:197-203` | 行车档层壳改 G0 实色（`GLASS.solid`），与「安全面 G0」规则一致；泊车保持真模糊 |
| P09 | 中 | C 身份行车档闲时发送键常驻灰态（B4 §6.3 自己写的「一枚永远点不动的键」） | `Composer.tsx:293,300`；`docs/images/mobile-driving.jpg` 右下角 | `inputMode === 'hidden' && !keyActive` 时不渲染该键 |
| ★P10 | 高 | 承诺卡标题直接显示服务端 `summary`，英文语料下是机器意图名 `trunk.open` / `fuel_tank_cover.open` / `charging_port.open`，右侧还标着「危险动作 · 需二次确认」 | `AR05-device-20260909-b2/10-confirm-card.png`、`21-dock-expanded.png`（包 `d425b9c2d`）；`FocusDock.tsx:193-199` | 主修在服务端 summary 生成（中文人话）；客户端兜底：`summary` 匹配 `^[a-z_]+\.[a-z_]+$` 时标题显示「待确认的车辆操作」、原 id 降为说明行，**不许把机器名当标题** |
| P11 | 中 | 顶栏 7dp 灰点在线时常驻，读起来像残留标点；它同时是隐私栏唯一入口，没有可见的可点提示 | `ChatScreen.tsx:534-571`；所有对话页截图 | 在线时不渲染健康点（离线/重连由胶囊表达）；采集点只在采集时出现、可点开隐私栏；隐私栏再给一个显式入口（设置页「隐私 · 现在在采集什么」） |
| ★P12 | 高 | 长按助手气泡复制的是 `trace_id`（提示「trace 已复制」）；用户气泡没有长按；两者都不能复制正文 | `MessageBubble.tsx:136-142,165,261` | 长按 = 复制正文（两种气泡都支持）；trace 复制移到「轮次时间线」诊断页或 dev 变体 |
| P13 | 中 | 作为聊天类 App 的基线缺口：无时间分隔、无「回到最新」、失败气泡无「重发」、冷启动会话清空 | `core/session/store.ts` 零 `AsyncStorage` 引用；`hmi/src/types.ts:9-37` `Msg` 无时间字段；`MessageBubble.tsx:205-216` 错误只变红 | 分三件：① store 侧记 `at`（不改共享 `Msg`），按 5 分钟分组画分隔；② 离底超一屏时显示「↓ 最新」；③ 错误/未知状态气泡加「重发」；持久化涉及挂起语义单列裁决（§6） |
| P14 | 中 | 过程区折叠条、回执切换、💬 follow-up 三个可点文字的触控高度 16–32dp | `MessageBubble.tsx:39-49,256-260`；`ExecutionReceipt.tsx:23-32`（`minHeight: 32`） | 三处 `minHeight: 44` + `hitSlop` |
| ★P28 | 中 | Dock 钉着「打开后备箱」确认卡时，紧贴其下的胶囊还说「等你确认」；竖屏下 Dock（~200dp）+ 胶囊 + chips + 输入行叠到 ~330dp，OPPO 外屏（717dp 高）留给记录的不到一半 | `docs/images/mobile-focus-dock.jpg`；`AR03-20260908/x19-xiaomi-landscape.png`；`presence.ts:303-305` | `focusDockVisible` 为真且胶囊是 attention 类时不渲染胶囊；判据写进 `presence.ts`（一处），对话页与支持页共用 |
| P29 | 低 | 欢迎态文案「按住下方光球说话」，而 B2 起主手势是轻点即说（Composer 的读屏提示已是「轻点开始说话」） | `ChatScreen.tsx:142`；`Composer.tsx:197` | 改「点一下光球说话」，长按作为次要说明 |

### 2.2 设置页（`SettingsScreen`）

| # | 严重度 | 现象 | 证据 | 建议 |
|---|---|---|---|---|
| ★P17 | 高 | 一页 12 个分区约 60 个控件无分级；用户项与工程项混排；标题带内部代号（「进阶语音（M4）」「实验室（UX v2.1）」「光球状态锚 + 状态胶囊」「承诺面 Focus Dock」「材质 spike（B3…）」）；服务器区直出 URL 与 token 尾巴；「PoC 默认放行（不是真实授权）」「无授权（fail-closed）」「当前服务端还不支持能力摘要（旧版本）」；试听失败文案「后端没配它的 key，或 key 已失效」 | `AR04-presence-20260909-b2/b2-settings-bottom-2.png`、`AR05-device-20260909/06-capabilities.png`；`SettingsScreen.tsx:65-69,326-342,588,616,739-807` | 重排为「通用（主题 / 字号 / 常亮 / 无障碍）」「助手」「语音」「隐私」「账号与连接」五组；工程项全部进「开发者选项」，prod 默认隐藏，构建行连点 7 次或 dev/staging 变体才出现；文案改写表见分批草案 §3 |
| ★P18 | 中 | `uxV2Presence` / `uxV2Dock` 两个 v1 回滚开关仍暴露给用户；B4 起所有验证只跑 v2 路径，v1 分支已是没人验证的代码 | `SettingsScreen.tsx:739-755`；`ChatScreen.tsx:403-452` 四段 v1 代码；`e2e/02-danger-confirm-cancel.yaml` 只在 v1 下绿 | 本轮先隐藏进开发者选项；是否删除 v1 路径（含 e2e 02、`set-dock` 子流、3 个测试文件）单列裁决（§6） |
| P19 | 低 | 「减少动效（强制）」「减少透明度」是无障碍设置，却住在实验室 | `SettingsScreen.tsx:756-771` | 移到「通用 · 无障碍」，去掉「（强制）」 |

### 2.3 车辆页（`VehiclePanel`）

| # | 严重度 | 现象 | 证据 | 建议 |
|---|---|---|---|---|
| ★P20 | 高 | 明细列表直出原始键名与原始值：`cabin_temp` / `child_lock` / `door_lock: locked` / `fragrance` / `hvac_on` / `location: null` / `媒体: stopped` / `rear_view_mirror: unfolded` / `seat_heating`…；中英混排 | `AR04-presence-20260909/idle-vehicle.png`（包 `de2a556`）；`VehiclePanel.tsx:12-45`（`KEY_LABEL` 缺至少 12 个真栈会推的键）、`:47-53`（枚举值不翻译，`null` 直出） | 补齐键表与值枚举表（locked/unlocked/open/closed/folded/unfolded/playing/paused/stopped）；`null`/`undefined` 行不渲染；未知键收进折叠的「其他」；用 jest 对账「VAL 模拟车态出现过的键必须有中文标签」，声明源仍是 `commands.yaml` |
| P21 | 低 | 页脚「车况镜像 · 与座舱实时同步（只读）」、空态「等待车况镜像…（连上网关后 vehicle_state 帧会推全量）」是开发者话术 | `VehiclePanel.tsx:152-157` | 「与座舱实时同步」「还没收到车况，连上座舱后会自动显示」 |

### 2.4 地图页、引导页、启动与图标

| # | 严重度 | 现象 | 证据 | 建议 |
|---|---|---|---|---|
| P22 | 低 | 底部信息条、浮动光球、高德 logo 三者挤在一起：光球压在 POI 标注上，logo 被信息条半遮（高德要求 logo 可见） | `AR04-presence-20260909/idle-map.png`；`map.tsx:205-221` | 信息条左侧留出 logo 高度，或把 logo 位置上报给 `bottomChrome` 一起避让 |
| P24 | 低 | 引导页是运维语言：「Tailnet FQDN」「AUTH_TOKENS 条目的 token 段」「主链 / 音频」 | `onboarding.tsx:200-260`；`e2e/artifacts/b1-13-onboarding-dark.png` | 内部验证期可接受；AM5-01 账号落地前只改 placeholder（「访问令牌」「由管理员提供」），派生 URL 缩成一行「已自动生成连接地址」 |
| ★P25 | 中 | 启动页背景是 Expo 模板蓝 `#208AEF`，App 是深空底 `#06080F`：每次冷启动蓝→黑闪变 | `app.config.ts` `expo-splash-screen` 配置 | `backgroundColor: '#06080F'` + `dark` 同值，`imageWidth` 76 → 120；需要原生重建，并入下一次重建 |
| P26 | 低 | 自适应图标 `backgroundColor: '#E6F4FE'`（模板淡蓝）：不支持 `backgroundImage` 的场景（部分系统对话框、应用信息页）会露淡蓝底 | `app.config.ts` `adaptiveIcon` | 改 `#06080F`，与 P25 同一次重建 |

### 2.5 跨页与视觉系统

| # | 严重度 | 现象 | 证据 | 建议 |
|---|---|---|---|---|
| P15 | 中 | emoji 当图标：⚠ ⟳ 📷 💬 ⚡ ✅ ⏰ ☐ 🏛 🍽 🏨 📍 以及能力开关的 🚘🎵🧭；ColorOS 与 HyperOS 的 emoji 字形不同，且与线性图标体系混搭 | `FocusDock.tsx:192,256`、`MessageBubble.tsx:123,258`、`navCards.tsx:312,349-355`、`miscCards.tsx:108`、`ReminderSection.tsx:45` | 线性图标库补 warning / refresh / camera / chat / bolt / check / clock / pin 八枚（`icons.local.ts` 现有 3 枚同格式）；Dock / 胶囊 / 气泡先换，卡片族第二步；能力开关的 emoji 来自共享 `AGENT_CATALOG`，mobile 只改自己的渲染（不显示 `icon` 或映射到线性图标） |
| P16 | 中 | 10pt 文字六处（`SportsScores`/`SportsScorers` 的「数据来源」、手册卡 PDF 页码、「trace 已复制」、行程卡日期徽标与地址、抽屉箭头）；11pt 大量；深色 `fg3` 48% 白、浅色 `fg3` 60% 黑做小字时对比度接近 AA 下限 | `tokens.ts:17`（`micro: 11`）；`infoCards.tsx:422,447`、`miscCards.tsx:47`、`MessageBubble.tsx:261`、`navCards.tsx:418,448`；`theme.ts:61,86` | 最小字号统一 11，`micro` 提到 12；浅色 `fg3` 提到 0.66；把 `fg3/bg`、`amber/amberSoft`、`accent/accentSoft` 三对加进已有的对比度 jest |
| P27 | 中 | driving-landscape split 下右列答案文字贴到屏幕右缘，「是否允许」之后疑似被裁 | `AR03-20260908/x24-landscape-dock-under-sheet.png`（Xiaomi 外屏，包 `b5c471832`）；`VoiceSheet.tsx:327-331` 右列无 `paddingRight` | 右列 `paddingRight: 16`；**只能在 Xiaomi 外屏复核**（OPPO 两块屏都进不了该形态，AR03 已证） |
| P30 | 低 | 光球依赖 `filter: blur`，Android < 12 全灭时退化成「四个色点在转」（组件头注自述） | `AuroraOrb.tsx:6` | 归 AR10 的 minSdk / 支持范围裁决，不在本轮改 |

## 3. 问题三：偏向测试、不符合用户交互的部分

按「用户在 prod 常驻包里点得到」为准：

| # | 位置 | 性质 | 处置 |
|---|---|---|---|
| D1 | 设置页「实验室（UX v2.1）」四开关 + 状态画廊链接 | 回滚路径与取证入口 | 进开发者选项（P17/P18/P19） |
| D2 | 设置页「诊断与样本」六条链接（采集状态 / 卡片画廊 / 在场轨迹 / 轮次时间线 / 原生状态 / 材质 spike） | 只读取证屏（AR02 已封自动采集，可达无害） | 只有「采集状态」有用户价值，但它现在是六行 `JSON.stringify` 直出（`capture-status.tsx`）——拆成用户版「隐私记录」（隐私栏的行 + 最近激活日志）留在隐私分区，JSON 版进开发者选项 |
| D3 | 长按助手气泡复制 `trace_id` | 可观测台排障通道 | 改为复制正文（P12），trace 进轮次时间线页 |
| D4 | 车辆页原始键值镜像 | 调试镜像面向了用户 | P20 |
| D5 | 设置页服务器区 URL / token 尾巴、授权来源三档文案、试听失败文案 | 运维语言 | P17 文案表 |
| D6 | 首页默认示例前三条全是车控（`打开空调26度` / `打开主驾座椅加热` / `播放音乐`） | AR05 已按能力摘要筛，但摘要未取到时照常全列；手机档用户多数没有 `vehicle.control` | mobile 侧改示例顺序为跨能力（天气 / 附近充电站 / 讲个笑话 / 打开空调），共享 `DEFAULT_QUICK_COMMANDS` 不动；属产品决定（§6） |
| D7 | 能力开关列表里的「闲聊兜底 · （系统兜底）」 | 共享 `AGENT_CATALOG` 的内部用语 | 属 hmi 共享数据，mobile 不改；记给 hmi 侧裁 |

不算测试味、保留：testID 密布（用户不可见）；`?only=` 深链参数；设置页底部构建行（报问题要抄它）。

## 4. 问题四：显示不全与视觉缺陷清单

| # | 结论 | 证据 | 归属 |
|---|---|---|---|
| V1 | **实证** 键盘弹起时欢迎态第三条推荐被遮半截 | `chat-typed.png`（`de2a556`） | P03 |
| V2 | **实证** 语音层底缘把答案切成半行、无渐隐 | `r5-largefont-two-stops.png`（`b5c471832`） | P06 |
| V3 | **实证** 行车档语音层记录透出与层内文字叠字 | `docs/images/mobile-driving.jpg` | P08 |
| V4 | **疑似** driving-landscape 右列答案贴右缘裁切 | `x24`（Xiaomi） | P27 |
| V5 | **实证** 承诺卡标题为机器意图名 | `10-confirm-card.png`（`d425b9c2d`） | P10 |
| V6 | **实证** 车辆页 `null` / 英文枚举直出 | `idle-vehicle.png`（`de2a556`） | P20 |
| V7 | **实证** 状态胶囊在层内层外各一份 | `r5-largefont-two-stops.png` | P05 |
| V8 | **已修待复核** 深链进入设置页时没有返回箭头（`idle-settings.png` 无「←」，同包从顶栏进入的 `story-settings-t8.png` 有） | 深链改为「回栈底再导航」已落 `046fb5c`，未在固定包上复核 | 批 A 取证顺带核 |
| V9 | **已闭合** 设置 / 车辆页滚到底最后一行与浮动光球零重叠 | `b2-settings-bottom-2.png`（`1c6780744`） | 无 |
| V10 | 长提醒卡 `maxHeight: 190` 内滚动、无滚动提示 | `ProactivePresenter.tsx:66` | 低，随 P06 一起加渐隐 |

## 5. 问题二：OPPO 与 Xiaomi 要不要都接

**不用都接。** 判据是「哪些格只有那台能到」，不是「多一台更稳」。

| 机器 | 角色 | 打磨轮里的用法 |
|---|---|---|
| OPPO PEUM00（test） | 日常落点 | 每批都接：装候选包、截固定状态清单（§7）、改设备状态的探针只在这台 |
| Xiaomi MIX Fold 4（compare，用户主用机） | 同包只读对照 | **每批收口时接一次**，只装 prod 常驻包、不装 dev-client、不改设备状态、不打断用户会话；只截四格：① driving-landscape 横屏（外屏 840×360dp，OPPO 两块屏都进不了）；② HyperOS 字体 / emoji / 状态栏下的对话页与设置页；③ Android 16 的键盘避让与返回手势；④ 内屏双栏 |

三点判断依据：

- 两台外屏竖屏都是约 360dp 宽（OPPO 359×717dp、Xiaomi 360×840dp），普通直屏手机的竖屏形态已被 OPPO 近似覆盖，Xiaomi 不提供新的宽度读数，只提供更高的屏和另一套 OEM 渲染。
- 视觉打磨的缺陷几乎都在竖屏对话页与设置页，OPPO 一台就能暴露；横屏行车形态是唯一的例外，AR03 已证只有 Xiaomi 外屏命中 `w840dp h360dp`。
- Xiaomi 是用户主用机，中间候选包每装一次都打断使用；截图对照放在收口那一次最省。

真平板与 Android 10–11 直屏仍不在两台覆盖内，AR10 已把它们列为支持范围决策，本轮不假装覆盖。

## 6. 需要用户裁决的五项

| # | 事项 | 我的建议 |
|---|---|---|
| J1 | v1 回滚路径（`uxV2Presence` / `uxV2Dock`）是隐藏还是删除 | 本轮隐藏；下一批删除（连同 e2e 02、`set-dock` 子流、v1 状态条代码），减少一条没人验证的分支 |
| J2 | 首页默认示例顺序是否改成跨能力 | 改；只动 mobile 侧顺序，共享 `DEFAULT_QUICK_COMMANDS` 不动 |
| J3 | 会话冷启动是否持久化 | 持久化最近 50 条只读记录，不持久化挂起 / 草稿 / 队列（那些有 TTL 与服务端台账）；需和 AR01 的取消语义对账，单列 |
| J4 | 承诺卡机器意图名（P10）主修在服务端 summary 还是客户端映射 | 服务端出中文 summary 为主，客户端只做兜底显示；服务端改动走正常 deploy 授权 |
| J5 | 启动图 / 图标（P25/P26）是否值得单独一次原生重建（约 21–27 分钟） | 并入下一次必需的原生重建（AR07 或 AM5-03 签名），不单开 |

## 7. 取证协议（供分批草案引用）

固定包在 OPPO 上截 12 个状态，命名 `<状态>-<包短 SHA>.png`，每张配同帧 `uiautomator dump`；设置指纹只记非敏感项（字号 / 主题 / 行车档 / Dock 开关 / 免唤醒）。

`welcome` / `welcome-keyboard` / `chat-3turns`（含卡片）/ `chat-confirm-dock`（三条待办）/ `sheet-listening-empty` / `sheet-speaking-long`（长回答播报中）/ `sheet-driving-resident`（C 身份行车档）/ `settings-top` / `settings-bottom`（prod 应无开发者区）/ `vehicle-mirror`（真栈车态）/ `map-two-points` / `onboarding-dark`。

Xiaomi 收口四格：`landscape-driving-sheet`、`landscape-driving-dock`、`chat-3turns`、`settings-top`。

未达项一律写现象 + 归属批 + 复验范围，不写「通过」。
