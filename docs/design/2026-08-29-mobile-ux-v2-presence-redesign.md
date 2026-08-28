# Android 陪伴端交互设计升级方案（UX v2：以光球为锚的三层在场）

> 状态：**草案（待泓舟评审；评审通过后按 §11 拆实施计划）**
> 交付对象：`mobile/` 后续执行者（人或 Agent）；评审对象：泓舟
> 关联：`2026-08-23-hmi-android-app-plan.md`（选型与形态判断）、
> `2026-08-24-mobile-app-implementation-plan.md`（执行真相源；§M3-V 光球复刻批、§M4、坑账 §9）、
> `docs/conventions.md` §9.33（多端契约）、`docs/architecture/cockpit-agent-architecture.md` §2.4（两端一脑）、
> `CLAUDE.md` §5（S2S / 视觉 / 声纹三条红线）、Figma 源 `oGlfQSUhriAEs4uH8sJnVe`（A-1 设计系统 / A-6 对话态）
> 调研日期：2026-08-29（对标读数有时效，§2 每条都带来源与版本）

---

## 0. 结论先行

**一句话：现在的 App 是「贴了光球的聊天软件」，而它的产品是「语音优先的随身座舱助手」。**
升级不是换皮——Aurora 玻璃语言、光球形象、34 型卡片、共享判据、三条红线全部保留；
补的是五件 App 现在**没有**的东西：

| # | 升级点 | 一句话 | 现状证据（§1） |
|---|---|---|---|
| U1 | **在场模型 + 光球作为唯一状态锚** | 把散在 4 条窄条里的语音/连接/通知状态收成一个派生态，由光球 + 一枚「状态胶囊」承载；免唤醒的 `armed/listening/followup` 终于上光球 | `hf.orb` 算出来从没渲染过；状态文案用的还不是 Aurora 色板 |
| U2 | **语音层（Voice Sheet）** | 按住/唤醒/端到端三种说话方式进同一张从底部升起的语音层：大光球 + 实时转写 + 流式回答 + 主卡；收起后**逐字沉淀进对话记录**（S2S 轮从此有记录） | S2S 自答轮在对话里**零痕迹**；PTT 与免唤醒的转写各放一处 |
| U3 | **承诺面（Focus Dock）** | 危险动作确认 / 待补槽 / 长任务进度 / 离线队列这类「系统正欠用户一个动作或一个结果」的状态，钉在 Composer 上方**不随列表滚走**，带倒计时与消失理由 | 确认条是普通气泡，30s 静默剪枝后按钮**无解释地消失** |
| U4 | **形态：尺寸类 × 姿态 × 行车档** | 手机/平板/折叠屏按窗口尺寸类（compact / medium / expanded）+ 折叠姿态（book / tabletop）布局；`driving` 标志真正驱动行车档（56dp 目标、单行过程区、无文本输入） | 响应式只有一个 `min(w,h)>=600` 布尔；`driving` 存了没人读；Android 键盘避让是空操作 |
| U5 | **出 App 在场（跟着 M5）** | 长任务用 Android 16 Live Updates、快捷入口用 QS Tile / 快捷方式 / 小组件、默认助理角色按真机验证结果决定；**全部挂在 M5 前台服务/推送之后**，本轮只定形态不排期 | 主动消息只在 App 前台存在；没有通知、没有小组件 |

**对标的核心结论**（§2 详述）：小艺与超级小爱在 2025–2026 走到同一处——助手是**叠在当前情境上的一层**而不是一个要跳进去的页；状态由**一个视觉锚**承载（小艺的流光导航条 / 小爱的悬浮态与超级岛）；长任务有**常驻可见的进度面**（小艺：流光导航条 + 提示胶囊；小爱：超级岛，完成自动展开结果）；语音与文字同一面；大屏即「左对话右内容」双栏。**可搬的是这些机制，不可搬的是系统特权**（系统级浮层、实况窗/超级岛 API、电源键）——用 Android 公开机制替代，并且只在 M5 之后才有意义。

**两条边界事实，先说清免得方案被误读**：
1. **HarmonyOS 6.x（NEXT 系）不能运行 APK**（§14 [S-H1]）——小艺**只是设计参照**，不是目标设备。
2. **HyperOS 是 Android 系，也是我们唯一真机**（MIX Fold 4 / Android 16）——超级小爱**既是参照也是同台的系统级竞争者**：它能做的系统面我们做不到，所以方案在「App 内体验」上必须比它更专（座舱/车况/记忆/确认链），而不是在「系统面」上追它。

**「不负优化」的保障**（§11.4）：每条改动都带可量的判据；新增「状态画廊」调试屏（同 `card-gallery` 哲学）让每个在场状态可截图回归；Maestro 扩 5 条流；§10 列出**刻意不动**的清单（光球十条不变量、对话记录不被临时层取代、共享判据不分叉、红线逐条对等）。

---

## 1. 现状与证据（2026-08-29 盘点读数）

> 盘点按源码逐行核过；本节只列**与交互直接相关**的读数。`M/`=`mobile/src/`，`H/`=`hmi/src/`。

### 1.1 已经做对的（本方案不动）

- **光球复刻到位**：七层五态（`M/ui/aurora/AuroraOrb.tsx:23,84-87`）逐值照 Figma A-1 §10；四个落位（欢迎 88 / 顶栏 30 / Composer 40-in-52 / 气泡 28）与 HMI 同构；`animated=false` 零帧回调的性能纪律（`:74,94-95`）。
- **对话语义层共享**：请求归属 / 确认台账 / 序数消费 / 位置闸 / 主动幂等全走 `@shared/*`（`M/core/session/store.ts`、`sendRouter.ts`）。
- **卡片铁则**：34 型注册表从 `types.ts::UiCard` 派生、兜底卡绝不 null、`CardBoundary` 隔离、`_prov` 四态角标（`M/features/cards/CardRenderer.tsx:61-160`，`parts.tsx:148-161`）。
- **弱网提示延迟 3s**（`M/features/chat/ChatScreen.tsx:255-265`）——「重连是常态，每次都弹会让真断网没人看」这条判断是对的，U1 沿用。
- **设置页三条红线文案在屏上**（`M/features/settings/SettingsScreen.tsx:394-418`）。

### 1.2 问题清单（按「用户会撞到」排序）

| # | 面 | 现状 | 证据 | 定性 |
|---|---|---|---|---|
| P1 | 光球不承载状态 | 免唤醒六态只以 12pt 文字条 + 7dp 色点显示；`useHandsFree` 算出的 `orb` 值**没有任何消费方**；色点用 `#64748B/#A78BFA/#22D3EE…`，不是 Aurora token | `M/features/chat/useHandsFree.ts:38,87` vs `ChatScreen.tsx:225-233`；色表 `ChatScreen.tsx:35-50` | **设计资产闲置**：为「一眼读出麦开没开」设计的 `armed/listening` 两态在生产里从未出现 |
| P2 | 状态散在四条窄条 | 连接 pill（顶栏）+ 弱网横幅 + 免唤醒条 + 通知条 + Composer 提示行，各自一套颜色语言，最多同时叠 4 条 | `ChatScreen.tsx:267-272,308-341,378-413`；`Composer.tsx:41-43,75-88` | 用户要学四种语言才能知道「它现在在干嘛」 |
| P3 | S2S 轮无记录 | 端到端挡位自答的轮，`onS2sUserUtterance / onS2sAnswerDelta` 有事件、`ChatScreen` 不接；只有 escalate 的轮进列表 | `M/core/voice/handsFree.ts:164,301`；`useHandsFree.ts:103-105`；`ChatScreen.tsx:225-233` | 对话记录与「实际说了什么/听到什么」**静默分叉**；「系统持有的事实」在这个端不可见 |
| P4 | 转写落点不一致 | PTT 的 partial 在 Composer 提示行；免唤醒的 partial 在状态条；都不进列表；HMI 用的是列表尾部虚线「幽灵气泡」 | `Composer.tsx:42`；`ChatScreen.tsx:330-334`；对照 `H/components/ChatView.tsx:202-216` | 同一个动作（在说话）两种呈现 |
| P5 | 确认是气泡不是承诺面 | 确认行内嵌在助手气泡里，随列表滚走；台账每 30s 剪枝，到期后按钮**无解释地消失**；无倒计时 | `M/features/chat/MessageBubble.tsx:166-201`；`M/core/session/store.ts:27,518-532` | 危险动作确认是安全链的一环，它的可见性不该受滚动位置支配 |
| P6 | 键盘避让在 Android 上是空操作 | 三处 `KeyboardAvoidingView` 都传 `behavior = ios ? 'padding' : undefined`；e2e 不得不手动 `hideKeyboard` | `ChatScreen.tsx:358-360`；`onboarding.tsx:97-100`；`debug.tsx:96-99`；`M/e2e/01-text-weather.yaml:10-13` | 唯一交付平台上键盘会盖住 Composer |
| P7 | 形态只有一个布尔 | `tablet = min(w,h) >= 600`；无横屏布局、无姿态、无分屏/多窗、无尺寸类；只有地图页在旋转/展开时重 fit | `ChatScreen.tsx:58-59,414-442`；`M/app/map.tsx:107-117` | 折叠屏外屏→内屏、平板横竖、车载横向支架三种场景没有形态答案 |
| P8 | 行车档缺席 | Edge 下发的 `driving` 标志存进 store，**没有任何组件读它**；有 keep-awake「车载支架」设置却没有行车布局 | `store.ts:286,295,303`；`SettingsScreen.tsx:155-184` | Figma A-6 的行车态（过程区单行锁定 / 56dp 目标 / 车速门禁）在 App 端没有落点 |
| P9 | 卡片无优先级、一轮一张 | `display_priority` 全仓 mobile 零命中；`Msg.uiCard` 单数；`card_group` 只是竖排 `gap:8` | `CardRenderer.tsx:62-68`；`H/types.ts:26`；对照 `orchestrator/cloud/aggregator.py:155-158` | 聚合器精心算的主卡/候选卡序，在手机上被丢掉 |
| P10 | 视觉抓帧零反馈 | 命中触发词→挂相机→拍→卸载，全程无「正在看」提示；用户自己的气泡要等相机冷启动才出现 | `M/features/vision/VisionCapture.tsx:71-85`；`ChatScreen.tsx:195-210` | 隐私敏感动作**看不见**，与「采集面就是隐私面」相悖 |
| P11 | 主动消息无出 App 路径 | 无通知、无前台服务（刻意，PoC 前台档）、无小组件；根屏返回=退 Activity | `M/app.config.ts:74-83`；实施计划 §M3-W「根屏返回」定案 | 前台档是承诺，但**形态**要提前定，否则 M5 到了又是一轮重做 |
| P12 | Onboarding 脱离品牌 | 自带浅色硬编码色板、无光球、无安全区 | `M/app/onboarding.tsx:209-250` | 第一屏就不是「小舟」 |
| P13 | 无 token 层 | 无圆角/间距/字阶文件；~40 处内联数值；「大字」只放大文字不放大容器与热区 | `theme.ts:51,116`；`MessageBubble.tsx:71-73` 等 | 可访问性与一致性都没有抓手 |
| P14 | 无障碍与触感近乎缺席 | 全 App 4 个 `accessibilityLabel`、无 role、无 live region、无 haptics、无 reduce-motion | grep 读数 | 语音助手对视障用户本应最友好 |
| P15 | e2e 只覆盖文本路径 | 4 条 Maestro 流没有语音/设置/平板/主题/地图/视觉 | `M/e2e/*.yaml` | 本方案改的正是这些没被守的面 |

### 1.3 设计侧已有、App 端未落的（是「捡」不是「创」）

- Figma A-6 六个对话态齐全（思考 / 流式 / 过程区三形态 / 确认 / 主动两色 / 错误），**含行车态开关**；A-8「行车态 + 浅色」**未定稿**（`2026-06-29-figma-hmi-implementation-plan.md:155-161`）。
- A-1 §10 光球动效表与「行车 ×0.5 频率 ×0.6 透明度」「`prefers-reduced-motion` → 0.01ms」两条降级规则；触控目标「泊车 48 / 行车 56」；确认「车速 >0 禁用、>5km/h 升级全屏拦截」（Guidelines `:325-327`、A-6 spec `:712-718`）。
- HMI 里设计了、从未挂载的 `.au-edge-glow`（听/想时屏幕边缘 2px 极光呼吸，`H/aurora.css:193-201`）——手机上正好可用作**语音层的边缘信号**。

---

## 2. 对标调研：小艺（HarmonyOS 6 / 6.1）与超级小爱 2.0（HyperOS 4）

### 2.1 方法与边界

- **版本事实（2026-08-29）**：HarmonyOS 6 GA 2025-10-22、**6.1 GA 2026-04-20**（国内），7 处于 Beta（首发机 Pura X View 在售）[S-H2]；**HyperOS 4 于 2026-08-13 官宣、08-14 起推 Beta**（小米 17 / K90 / 平板 8 三批 29 机型），超级小爱 2.0 分批放量——「码号上岛 / 小爱备车」08-28 已到，**「灵感球」承诺 9 月内 Beta**，正式版日期未公布 [S-X1][S-X2]。所以「HarmonyOS 6.1 的小艺」是已发布事实，「HyperOS 4 + 超级小爱 2.0」是 Beta 期读数。
- **两家都没有公开像素级状态设计**（听/想/说的具体形态），公开资料描述的是**行为**；下面每条都带来源，凡是描述性推断都标「推断」。
- **HarmonyOS NEXT 系（5.x/6.x）不运行 APK** [S-H1]——小艺只做设计参照。HyperOS 是 Android 系（HyperOS 4 = Android 16），是我们唯一真机的系统。

### 2.2 小艺（HarmonyOS 6 / 6.1）

| 面 | 事实 | 版本 · 来源 |
|---|---|---|
| **入口** | 底部导航条**就是助手把手**：长按=唤醒、把文字/图片/文件**拖给小艺**；电源键 0.5s；「小艺小艺」；耳机双击；多设备协同唤醒只应一台；**小艺私语**（抬到嘴边 5cm 内轻声说、回应同样轻）；指关节圈选、识屏对话 | 5.0+ · [S-H3][S-H4][S-H5] |
| **6.1 新形态** | **伴随式 AI**：双击导航条从屏侧拉出小窗，**按握持手自动换边**，三档「侧边常驻 / 展开 / 后台静默」，极简态收成**「一个小小的彩条」**点按再展开 | 6.1（Pura X Max 独占）· [S-H6][S-H7] |
| **状态视觉** | 5.0 唤醒动效是「多彩灵动的圆形立体」从导航条升起，**不是全屏接管**；6.0「智慧光感」——轻按底部「像水面涟漪般晕开」、呼叫小艺时「如同流光一般在界面中温暖流淌」（推断：听的状态 = 底缘多色光晕）；**执行中导航条变成「流光彩条」**，点按弹「提示胶囊」带「停止」，上滑导航条把任务送后台；决策点（选商品/航班）也从提示胶囊冒出；付款前弹窗确认 | 5.0 / 6.0 · [S-H8][S-H9][S-H10][S-H11] |
| **容器** | **两层**：唤醒 → 紧凑面板（Beta 上手称「半屏」）；**上滑进全屏对话页**（菜单里记忆/历史）；按住说话 + 键盘并列；6.0 「上划界面后小艺在后台完成一系列操作」——对话收起、工作继续 | 5.0+ / 6.0 · [S-H12][S-H13][S-H14] |
| **播报策略** | 三档：总是播报 / 静音 / **自动（语音提问才播报）** | 4.x 起沿用 · [S-H15] |
| **长任务** | 系统级智能体框架 HMAF；**实况窗**（状态栏胶囊 / 锁屏卡 / 悬浮卡，模板制，6.0 胶囊环绕前摄）——但**没有证据显示小艺把自己的任务投进实况窗**，助手任务用的是流光导航条 + 提示胶囊，实况窗归 App 自己的进度（外卖/打车/红绿灯） | 6.0 · [S-H16][S-H17][S-H18] |
| **大屏** | Mate X7：长按导航条 → **自动分屏，左边保持聊天、右边打开内容页**；「分屏问小艺」选中文字问另一半屏；一多断点 **SM<600 / MD 600–840 / LG ≥840 vp**，折叠屏「避免关键内容压折痕、支持悬停（半开）」 | 6.0 / 官方设计指南 · [S-H19][S-H20][S-H21] |
| **行车** | 手机侧小艺**没有**行车皮肤；HiCar 7.0（2026-07）给手机加了**横屏驾驶模式**（导航/音乐/联系人同屏）；6.1「智感畅行」情景：红绿灯倒计时实况窗、**自动外放、音量最大、清晰播报、息屏延迟 10 分钟** | 6.1 · [S-H22][S-H23] |
| **主动** | 桌面「小艺建议」卡片栈（可逐张移除/不再推荐）；6.1 伴随式 AI「**不打扰，但始终在**」；关键字触发的优先通知（航班/快递） | 5.0+ / 6.1 · [S-H24][S-H7][S-H25] |
| **设计词汇** | 智慧光感（6.0）→ 沉浸光感（6.1，可设高/均衡/弱）→ 全局沉浸空间（7）；助手自身的签名是**多色流光导航条**（没有「小艺之光 / 蓝色光带」这种官方命名） | [S-H9][S-H2] |

### 2.3 超级小爱 2.0（HyperOS 4）

| 面 | 事实 | 版本 · 来源 |
|---|---|---|
| **入口** | OS4 主入口：**「长按屏幕底部小白条，按住就能说，抬手即走，不打断当前操作」**；电源键 ≈1s；双击手势线=文字输入；长按手势线=圈搜；三指上滑=小爱记忆（截屏进记忆，气泡上岛）；**灵感球**：「指哪里、看哪里」，替代「唤醒识屏→圈选→输入」三步（9 月 Beta） | OS3 / OS4 · [S-X1][S-X3][S-X4][S-X5] |
| **状态视觉** | OS2 唤醒是**全屏水波纹**；OS4 改为**「全新悬浮态界面」**，「即使切到其他应用，小爱的思考和执行状态仍在超级岛上实时显示，任务完成后自动展开结果」；上手：「界面更轻盈，支持悬浮交互，长任务可以直接上到超级岛显示进度」 | OS2 → OS4 · [S-X6][S-X7][S-X8] |
| **超级岛规格**（开发者文档） | 大岛按前摄分 A/B 区，**文字建议 ≤4 个中文字**，溢出先缩后裁不跑马灯；小岛 = 图标 / 图标+进度环 / 图标+短文；展开态 22 套模板、≤3 个按钮、进度条单色或渐变、**展开 5s 自动收起**，岛默认 1h 消失、进程 ≤12h；手势：点=展开、下拉=小窗、左右滑切岛、最多 3 岛并存；专用窄高字体 | OS3 · [S-X9][S-X10][S-X11] |
| **容器** | 多模态（语音/文字/识屏/拍照）；问路直接出地图卡；「陪伴模式」常驻实时语音；OS4「对话接力」（PC↔手机）、锁屏下**不回答敏感数据问题**、记忆可管理来源与清除；**收起 = 抬手即走**。对话历史页结构无公开资料 | OS2 / OS4 · [S-X12][S-X1] |
| **智能体与确认** | 一步直达（100 应用 / 3000+ 能力）；OS3 现实是「只能跳到对应 App，没法做后续动作」；2.0 **专家模式**「自思考、自规划、自调用、自执行」+ 积分计费；官方确认策略：「**应用控制与敏感行为二次确认**」「修改、删除或外发数据前会向你确认」；运行中任务的暂停/取消**无公开资料** | OS3 / OS4 · [S-X13][S-X14][S-X1] |
| **大屏** | Flip 外屏「推荐上下单列布局」、折叠展开接续；超级岛「一次适配多端展示，支持背屏、外屏」；Pad 工作台；**没有任何来源描述平板/折叠屏专用的小爱布局** | OS3 · [S-X15][S-X16] |
| **车** | SU7 小爱免唤醒高频指令；OS4 **「备车」**一句话预加载导航 + 调温、**导航一键流转中控**；CarWith 驾驶模式自动开启、摇一摇把地址送车机 | OS3 / OS4 · [S-X17][S-X18][S-X19] |
| **设计词汇** | 「生命感美学」；OS4 **柔光玻璃**「点按有光，操作有回应 / 拖拽有光，流转更自然」（旗舰芯片限定）；MiSans | OS1 / OS4 · [S-X1][S-X20] |

### 2.4 共性机制（可搬）→ 落到本方案哪里

| 机制 | 小艺 | 超级小爱 2.0 | 本方案落点 |
|---|---|---|---|
| ① **按住即说、抬手即走**，不换页 | 长按导航条 | 长按小白条「按住就能说，抬手即走」 | §5.1 光球按住 = PTT；§5.2 语音层从底部升起不换页 |
| ② **两层容器**：紧凑层 → 上滑全屏 | 紧凑面板 → 上滑对话页 | 悬浮态界面 | §5.2 语音层 62% + 对话记录变暗仍可见；下拉收起、上滑展开为全屏（Q1） |
| ③ **一个视觉锚承载状态** | 导航条流光 = 执行中 | 超级岛 = 思考/执行 | §4 Presence + 光球三新态 + 状态胶囊 |
| ④ **决策点从锚上冒出，不弹模态** | 提示胶囊（选项 / 停止 / 付款前确认） | 岛展开态 ≤3 按钮、5s 自动收起 | §5.3 Focus Dock（确认 flex2 / 取消 flex1 / 倒计时）；行车全屏拦截是唯一模态 |
| ⑤ **长任务可后台、完成自动展开结果** | 上滑导航条送后台 | 长任务上岛、完成自动展开 | §5.3 `task` Dock 项 + §9 Live Updates（同一份数据两个出口） |
| ⑥ **语音与文字同一面** | 按住说话 + 键盘并列 | 双击=文字、长按=语音 | §5.1 Composer 保留输入框，文字不升层 |
| ⑦ **大屏自动分屏：左对话右内容** | Mate X7 自动分屏、分屏问小艺 | （无资料） | §7.2 双栏 + 舞台 = 卡的大视图 |
| ⑧ **侧边伴随窗，极简态收成一条彩条** | 6.1 伴随式 AI | — | §7.2 medium 宽度的「舞台抽屉」48dp 把手 |
| ⑨ **播报策略三档「自动=语音提问才播报」** | 4.x 起 | — | §5.2 规则 8（替换现在 `ttsEnabled && autoplay` 两个近义开关） |
| ⑩ **行车：外放/音量/息屏延迟/极简卡** | 智感畅行、HiCar 横屏驾驶模式 | CarWith 驾驶模式 | §6 行车档（TTS 强制、keep-awake、单卡、56dp） |
| ⑪ **确认分类公开化** | 付款前弹窗 | 「修改/删除/外发 + 敏感行为二次确认」 | 我们已有 `require_confirm` + 支付不执行；§5.3 把「为什么要确认」写在 Dock 上（`require_confirm` 的对象名） |
| ⑫ **锁屏下不给敏感内容** | 卡证记忆需解锁 | 锁屏不答敏感问题 | §9 Live Updates 锁屏面**不带**记忆/支付内容 |
| ⑬ **上车前一句话备车 / 导航流转** | 高德近车机一键切换 | 「备车」、导航一键流转 | **挂账**（§13 Q12）：需要产品定手机档是否给 `vehicle.control` 子集 + 后端焦点跨会话流转，本方案只留 UI 落点（Dock `task`） |

### 2.5 不可搬 + Android 侧替代

| 系统特权 | 谁有 | Android 三方能做的替代 | 本方案位置 |
|---|---|---|---|
| 系统导航条 / 小白条手势、电源键、耳机唤醒 | 两家 | **默认数字助理角色**（`VoiceInteractionService`，设置 → 默认应用 → 默认数字助理）[S-A2]；HyperOS 上是否可选、电源键是否只绑小爱 **待真机验**；兜底 = QS Tile / 长按图标快捷方式 | §9、Q5 |
| 实况窗 / 超级岛 | 两家 | **Android 16 Live Updates**（`ProgressStyle`，三方公开，需 `POST_PROMOTED_NOTIFICATIONS` + ongoing）[S-A1]；超级岛三方接入是**白名单审核制**（注册→上架→证书→预审→联调→白名单→灰度），且只能投**通知**不能自渲染 [S-X21] | §9 |
| 系统级悬浮层（提示胶囊 / 悬浮态） | 两家 | `SYSTEM_ALERT_WINDOW` 可用但 HyperOS 权限阻力大、后台 socket 仍会被杀 ⇒ **不做** | P7 |
| 识屏 / 圈选 / 灵感球 / 拖给小艺 | 两家 | 只在自己 App 内可做（Share Sheet 入口、App 内圈选） | 不在本轮 |
| 跨 App 执行（帮帮忙 / 一步直达 / 专家模式） | 两家 | 无（无障碍服务是灰色地带，不做） | — |
| 对话接力 / 车机流转 / 双 NFC | 两家 | 同账号后端能力（我们已有「一脑两端」的记忆共享，会话级流转是后端工作） | Q12 挂账 |

### 2.6 我们已经领先或刻意不同的地方（别在对标里丢掉）

- **卡片真实性**：34 型卡 + `_prov` 四态（模拟/降级/缓存/vendor·时间）——两家公开资料里**没有**「这条数据是不是真的」的 UI 表达；这是座舱助手的信任基础，语音层与舞台**必须**保留角标（P0）。
- **确认台账服务端权威 + 多确认并存**（`pendingOps`）：小爱的「敏感行为二次确认」是政策，我们是**机制**。Dock 只是给这台机制一个不会滚走的面。
- **过程区**（理解→规划→执行→整理，行车单行锁定）：小艺的「深度思考」是独立智能体、无思考轨迹 UI；小爱把思考态放到岛上但只有状态没有步骤。**我们的过程区更透明，保留并在语音层里复用**。
- **S2S 红线**：小爱「陪伴模式」是常驻实时语音；我们**默认三段式、唤醒后才采**，这是刻意的，不因对标而放宽。
- **一脑两端的记忆共享**已在后端成立（`AGENTS.md` §4.1、architecture §2.4）；两家的「记忆」都是手机侧功能。我们缺的是**出处 UI**（Q5 记忆出处那条已在云端落地，App 端沿用文本）。
- **不做积分计费 UI、不做智能体广场**：与产品无关，且对标里可见的代价（计费 UI 喧宾夺主）[S-X14]。

### 2.7 对标里的反例（明确不学）

- 超级岛给外卖 App 的岛「面积过大，浪费了起码 40% 的空间」（36kr 实测批评）[S-X22] ⇒ Dock 高度按内容，`confirm` 项一行摘要 + 一行按钮，不做大卡。
- OS3 一步直达「只能跳到对应 App」却以「执行」宣传 [S-X13] ⇒ 我们的「已执行 xxx」行只在 `action` 帧到达后出现（现状已如此，保持）；语音层不许出现「正在为您办理」这类未执行先说的话术（与 C11 防编造同方向）。
- OS2 全屏水波纹唤醒被 OS4 自己改成悬浮态 [S-X6][S-X7] ⇒ 语音层不全屏（Q1 默认 62%）。
- 小艺伴随式 AI 只在一款阔折叠机型上 [S-H6] ⇒ 侧边舞台抽屉按尺寸类给，不绑机型。

---

## 3. 设计原则（七条 + 一条不变）

| # | 原则 | 含义 | 反例（它防的是什么） |
|---|---|---|---|
| P0 | **红线与真实性不变** | S2S 默认三段式 / 唤醒后才采 / 视觉默认关 / 声纹不作鉴权；`_prov` 角标不许被任何「更好看」的卡壳吞掉 | 语音层为了「沉浸」把设置里的挡位选择做成一键切换 |
| P1 | **光球是唯一状态锚** | 用户只看一个东西就知道助手在干嘛：光球态（环、辉光、节律）+ 一枚状态胶囊文案；其它面**不再各自表达状态** | 顶栏 pill、免唤醒条、通知条各说各的 |
| P2 | **助手是层，不是页** | 说话时助手从底部升起覆盖当前内容；说完落回去；对话记录是沉淀层不是主舞台 | 每次说话都得先「进聊天页」 |
| P3 | **承诺面不许消失** | 系统欠用户的动作/结果（确认、补槽、长任务、离线队列）钉在固定位置，有倒计时，消失必有理由 | 确认按钮 30s 后静默蒸发 |
| P4 | **一种输入，一份记录** | 按住 / 唤醒 / 端到端 / 打字进同一条转写与回答通道，记录逐字相同 | S2S 轮无记录；PTT 与免唤醒 partial 两处 |
| P5 | **形态按窗口尺寸类与姿态，不按设备** | compact / medium / expanded × book / tabletop / flat；手机横屏、平板竖屏、折叠内屏各得其所 | `min(w,h)>=600` 一个布尔 |
| P6 | **行车是产品档位，不是主题** | `driving` 或用户档位开关 ⇒ 目标 56dp、过程区单行、无文本输入、TTS 自动、确认按 A-6 车速门禁 | 只改一下字号叫「车载模式」 |
| P7 | **出 App 在场只用公开机制，跟着 M5** | Live Updates / QS Tile / 快捷方式 / 小组件 / 默认助理角色；不做系统浮层，不做后台保活 | 用 `SYSTEM_ALERT_WINDOW` 造一个「小舟胶囊」 |

---

## 4. 在场模型（Presence）：一个派生态，四个消费面

### 4.1 为什么是派生态而不是新状态机

App 里已经有三台状态机：轮态（`Msg` 的 `pending/streaming/processActive/needConfirm/error`）、免唤醒 FSM（`@shared/voiceLoop.mjs` 六态，**判据只许一份**，§9.33）、PTT（`usePtt.ts` 三态），外加连接态、S2S 挡位、视觉抓帧、主动消息、`driving`。**它们各自都对，错的是没有一个地方把它们合成「此刻该让用户看到什么」。**
所以 U1 加的不是第四台状态机，是一个纯函数：

```ts
// M/core/presence/presence.ts —— 纯函数、零副作用、node 可测（同 sendRouter 形态）
type Presence = {
  mode: 'offline' | 'reconnecting' | 'idle' | 'armed' | 'listening' | 'recognizing'
      | 'thinking' | 'processing' | 'speaking' | 'followup' | 'attention' | 'looking' | 'error'
  orb: OrbState            // 'idle'|'armed'|'listening'|'thinking'|'speaking' + 新增 'attention'|'looking'|'muted'
  capsule?: { text: string; tone: 'neutral'|'accent'|'amber'|'red'; live?: boolean }
  dock?: DockItem          // 见 §5.3；attention 态必有
  driving: boolean
  input: 'voice-sheet' | 'composer' | 'none'
}
function derivePresence(i: PresenceInput): Presence
```

输入 = `connStatus`、`hf.fsm`、`ptt.state`、最新在飞 `Msg` 的四个布尔、`pendingOps`、`voicePipeline`、`visionCapturing`、`proactiveUnread`、`driving`。**优先级固定且写进测试**：
`offline/reconnecting` > `attention`（有活的确认/补槽）> `looking` > `listening/recognizing` > `speaking` > `processing/thinking` > `followup` > `armed` > `idle`；`error` 只在无在飞轮时短显 4s。

这条纯函数是 **U1 的唯一新真相**；光球、状态胶囊、Focus Dock、无障碍播报四个消费面都只读它。反向验证：把任一输入维度置空，对应消费面必须回到上一优先级（测试逐维断言）。

### 4.2 状态矩阵（每个状态用户看到 / 听到 / 摸到什么）

| Presence | 触发 | 光球态 | 环 / 辉光 | 状态胶囊 | Focus Dock | 声音 · 触感 | TalkBack 播报 |
|---|---|---|---|---|---|---|---|
| `idle` | 无任务、免唤醒关 | `idle` 呼吸 4s | 无 | （隐藏） | — | — | — |
| `armed` | 免唤醒开、待唤醒 | `armed` 微光 5s ×0.8 | 青环 0.18α | 「说「小舟小舟」」淡灰，3s 后隐藏 | — | 开启时 1 次轻触感 | 「免唤醒已开」 |
| `listening` | 唤醒命中 / PTT 按下 / FOLLOWUP 听到人声 | `listening` 1.15s ×1.15 | 青环 0.4α + **屏幕边缘 2px 极光呼吸**（§5.2） | 「在听…」 | — | 唤醒：两音上行提示音 + 轻触感；PTT：按下触感 | 「在听」 |
| `recognizing` | 有 partial / 松手定稿中 | `listening` | 同上 | partial 原文（大字，语音层里） | — | — | 逐句更新 live region |
| `thinking` | 请求已发、无 process | `thinking` 1.4s | 无 | 「正在思考…」 | — | — | 「正在思考」 |
| `processing` | 收到 `process` 帧 | `thinking` | 无 | 「第 N 步 · 标签」（行车：单行锁定） | 长任务 >8s 时进 Dock（进度条 + 取消） | — | 阶段变更播报 |
| `speaking` | TTS 在放 | `speaking` 0.72s ×1.35 | 三层青波纹 | 「播报中 · 说话可打断」 | — | 首音不加提示音（避免与 TTS 叠） | — |
| `followup` | TTS 完、8s 追问窗 | `listening`（邀请式） | 青环 0.4α，**环按剩余时间递减** | 「可以接着说」+ 环倒计时 | — | 窗关闭：无声（刻意） | 「可以接着说」 |
| `attention` | 台账有活的确认 / `NEED_SLOT` / 位置授权 | **新 `attention`**：`idle` 节律 + **琥珀环** | 琥珀环 0.35α 呼吸 3s | 「等你确认」/「还差一个信息」 | **必有**：确认卡 / 补槽卡（§5.3） | 进入：双触感（重-轻） | 「需要你确认：{摘要}」 |
| `looking` | 视觉触发词命中、相机在拍 | **新 `looking`**：体缩 0.96 一次「快门」 | 一圈白环 300ms 扩散一次 | 「看一眼…」 | — | 快门轻触感 | 「正在拍摄一帧」 |
| `reconnecting` | `connStatus=connecting` >3s | `idle` ×0.6 亮度 | 灰环 | 「正在重连…」琥珀 | 离线队列 ≥1 条时：「N 条消息排队中」 | — | 「连接中」 |
| `offline` | 探活判死 | **新 `muted`**：去饱和、停旋转 | 灰环 | 「已断开 · 消息会排队」红 | 同上 | 判死 1 次触感 | 「已断开」 |
| `error` | error / cancelled / 超时（无在飞） | `idle` | 红环闪 1 次 | 「出错了」4s | — | 1 次触感 | 播报错误文案 |

三个新光球态的实现全在既有语言内（**只加环与节律，不改七层、不改色板**）：`attention` = 琥珀 `#F59E0B` 单环（琥珀本就是确认态语义色，A-6.4）；`looking` = 一次性白环扩散（与 speaking 的三青环区分：一次 vs 连续）；`muted` = 停旋转 + 饱和度 0.4 + 灰环（对应 HMI `driving ? 0.6` 那条降级思路）。**光球十条不变量逐条核过**（§10.1）：形状、七层、四色、极光只在 AI 时刻、波纹用青、动效签名、五态含义、落位、最前层、脚本化降级——三个新态没有碰其中任何一条。

### 4.3 状态胶囊（Capsule）

一枚 Composer 上方居中的 28dp 高胶囊（r=999，玻璃底，Aurora 纪律：**文字不用极光**），替代 P2 那四条窄条：
- 只显示 `Presence.capsule`，一次一条；连接态与语音态**合成一条**（优先级见 §4.1）。
- 含 `live` 的胶囊（在听 / 追问窗）左侧带 6dp 青点；琥珀/红胶囊不带点。
- 3s 延迟规则沿用弱网横幅那条（`ChatScreen.tsx:255-265`）：`reconnecting` 3s 内不显示。
- 点按胶囊 = 打开语音层（`listening/recognizing/followup`）或滚到 Dock（`attention`）或重连（`offline`）。
- 顶栏连接 pill **保留但降级**：只在 `offline/reconnecting` 变色，其余时间纯灰点——它是「系统健康」不是「助手状态」。

---

## 5. 手机形态：三层在场

### 5.1 Tier 0 —— 光球锚 + Composer 重排

```
┌──────────────────────────────┐
│ ◐ 小舟   · 在线        ☰  ⚙ │  顶栏：30dp 品牌球（只 idle/thinking）、连接灰点、车辆/设置
│                              │
│   （对话记录 / 欢迎态）      │
│                              │
├──────────────────────────────┤
│  ┌ Focus Dock（有承诺时才出现）┐│  §5.3
│  │ ⚠ 打开后备箱  确认 ▮▮▮▮ 42s││
│  └────────────────────────────┘│
│        ( 在听… )               │  状态胶囊 §4.3（一次一条）
│ [今天天气] [附近充电站] [讲个笑话] │  快捷 chips（行车档 ≤3 条）
│  ◉ 56   ┌──────────────┐  ➤   │  光球 = 麦克风（44dp 球 in 56dp 热区）+ 输入框 + 发送
└──────────────────────────────┘
```

Composer 改动（`M/features/chat/Composer.tsx`）：
- 光球热区 52 → **56dp**（Figma 行车档目标；泊车档也用 56——手机是手持设备，Material 最小 48，56 不冒犯任何场景），球体 40 → 44dp。
- **按住 = PTT，轻点 = 打开语音层并进入 `listening`（免唤醒开着时）或提示「按住说话」（关着时）**；长按松手前上滑 = 取消本次录音（微信惯例，行车档禁用此手势）。
- 提示行（`:75-88`）**删除**，其职责由状态胶囊与语音层接管；`■ 打断` 按钮保留但移入语音层（§5.2）；发送键极光填充不变（虹彩纪律三处之一）。
- 输入框 `returnKeyType=send` 不变；**键盘避让改为 Android 有效实现**（§7.6）。

### 5.2 Tier 1 —— 语音层（Voice Sheet）

**这是本方案最大的一处形态变化，也是小艺/小爱共同的形态**（§2.4 机制 ①②）。触发即从底部升起，覆盖对话记录约 62% 高度（手机竖屏），对话记录在其后**变暗 40%、仍可见**——用户知道自己没「离开」对话。

```
┌──────────────────────────────┐
│  ░░░ 对话记录（变暗，仍可见）░░░ │
│ ┌────────────────────────────┐ │  ← 顶缘：极光 2px 呼吸（listening/thinking）= .au-edge-glow 的移植
│ │  「附近有什么好吃的」        │ │  转写区：ASR partial 大字 20pt，定稿后转为用户气泡样式
│ │                             │ │
│ │           ◉ 88dp            │ │  大光球：Presence.orb 驱动（listening→thinking→speaking→followup）
│ │       在听… ● 可打断         │ │  胶囊文案（同 §4.3，此处放大）
│ │  为你找到 5 家……（流式）     │ │  回答区：speech_delta 逐字 + StreamCursor；行车档 18pt
│ │  ┌ 主卡（display_priority 0）┐│ │  主卡只放一张；其余卡收起成「还有 2 张卡片 ›」
│ │  └────────────────────────┘│ │
│ │  [换一批] [导航去第一个]       │ │  follow-up chips（来自 final.follow_up 与候选集）
│ │      ⌄ 收起      ■ 打断      │ │
│ └────────────────────────────┘ │
└──────────────────────────────┘
```

规则：
1. **三种说话方式一张层**：PTT 按住（`usePtt`）、唤醒词（`handsFree` FSM）、S2S（`s2sClient`）都通过 `Presence.input === 'voice-sheet'` 升起同一张层；文字输入**不升层**（沿用 voiceLoop 头注「文本不进 FSM」）。
2. **沉淀规则（P4）**：语音层里出现的每一句转写与回答，在层收起时**逐字**写入对话记录（`SessionCore` 已有的 user/assistant 气泡通道）。S2S 自答轮走 `onS2sUserUtterance/onS2sAnswerDelta` → 新增 `store.appendS2sTurn()`（**只写记录，不进 `requestRouting`**——它没有 `request_id`，按「无在飞轮的续流 adopt 新气泡」语义单独开一条 `source:'s2s'` 气泡，气泡带「端到端」小角标，让用户分得清哪些轮是没过 planner 的）。
3. **收起时机**：`followup` 窗关闭（8s）或用户下拉/点「收起」或点了主卡的按钮；`attention` 态下**不自动收起**（等确认）；行车档下 TTS 结束后 +3s 自动收起。
4. **打断**：`speaking` 时开口（barge-in）或点 `■ 打断`——层不收，光球从 `speaking` 直接转 `listening`，回答区文字定格并标「已打断」（不再改成红色错误样式；打断不是错误，A-6 也没把它归错误态）。
5. **回声提示**：voiceLoop 的 `_overlapsTts → _echoSuspected` 命中时（`H/voiceLoop.mjs`，按符号找——这个文件 2026-08-29 正被另一条线改，行号在变），胶囊短显「像是我自己的声音，没算数」2s——把「吞掉的那句」变成可见的，否则用户以为没听见。
6. **边缘极光**：层顶缘 2px `AURORA` 呼吸 1.6s，只在 `listening/thinking`（虹彩纪律允许的「听/想时屏幕边缘」那一处，Guidelines `:113-119`）。RN 实现 = 一条 2dp 高的 `experimental_backgroundImage` 线性渐变 View + opacity 呼吸，零新依赖。
7. **主卡规则**：`final.ui_card` 为 `card_group` 时按 `display_priority` 升序取首张为主卡，其余折叠；`display_priority` 缺省按 §CLAUDE.md 卡片优先级默认 2。**这是 P9 的修法**，聚合器的排序终于有消费方。
8. **播报策略三档**（小艺同款，[S-H15]）：`总是 / 静音 / 自动`，**自动 = 语音提问才播报、打字提问只显示文字**，默认「自动」。替换现在 `ttsEnabled && autoplay` 两个近义开关（`M/core/voice/speech.ts:58`、`SettingsScreen.tsx:271-290`）——两个开关同时为真才出声，用户分不清哪个是哪个。迁移：旧值 `ttsEnabled=false` → 静音；`autoplay=false` → 静音；否则 → 自动（旧行为里打字也播报，若泓舟要保留那个行为则默认取「总是」，Q11）。

### 5.3 Tier 2 —— 对话记录 + Focus Dock（承诺面）

对话记录保持现状（FlashList、气泡、卡片内嵌、过程区折叠条、trace 长按）——**它是沉淀层，不是被替换的对象**（§10.2）。加的是 Focus Dock：Composer 上方、状态胶囊之上的一块固定区，**只在 `Presence.dock` 存在时渲染**，最多 1 项（多确认并存时按台账顺序轮显，头部标「1/2」）。四种 DockItem：

| DockItem | 内容 | 关闭方式 | 关闭时对话记录里留什么 |
|---|---|---|---|
| `confirm` | ⚠ 图标 + 动作摘要（「打开后备箱」）+ **取消 flex1 / 确认 flex2**（A-6.4 比例与琥珀色）+ **剩余时间环** | 点按 / 语音「确认/取消」/ 到期 / `closed_operation_ids` | 到期：气泡追加一行「确认已过期，需要的话再说一次」；被服务端关：「已由座舱端处理」 |
| `slot` | 「还差一个信息：{missing_slots 中文名}」+ 建议 chips | 用户补槽 / 换题（`slot_shapes` 判换题时 Dock 自动撤） | 无（补槽本身进记录） |
| `task` | 长任务名 + 阶段（复用 `process` 帧）+ 取消 | 终态帧 | 过程区折叠条（现状） |
| `queue` | 「N 条消息排队，连上自动补发」 | 队列清空 | 无 |

`confirm` 项的**剩余时间来自台账**：`pendingOps` 的 `prunePendings` 用的 TTL（共享模块内）就是倒计时的上界，UI 不另存一份时间；到期由 store 的 30s 剪枝改成**按项到期精确调度**（`syncPruneTimer` 从固定 30s 改为 `min(nextExpiry, 30s)`，判据不变，只改触发粒度）。**行车档**：Dock 的确认按钮遵守 A-6 `:712-718`——`vehicle_state.speed > 0` 时禁用点按（只允许语音「确认」）；`> 5 km/h` 时 Dock 展开成全屏拦截层。手机档默认没有 `vehicle.control` scope，这条主要作用于平板车载档，但**判据与 UI 一起做**，不让「手机上永远碰不到」变成漏做的理由。

### 5.4 卡片与序数候选

- 一轮多卡：`card_group` 在对话记录里改为「主卡全展 + 其余卡 `还有 N 张 ›` 折叠」，展开是竖排（不做轮播——轮播在语音场景里等于藏卡）。
- 候选列表（`poi_list/place_list/intent_choice`）的序号 20dp 列不变；**行高提到 ≥48dp**（现状部分行 ~40）；行车档 ≥56。
- 卡内按钮仍走「合成一句话→普通 send」（`parts.tsx:82-113`），不加直接 API——这是架构约束（LLM/UI 都不直连执行）。

### 5.5 视觉抓帧反馈（P10）

`looking` 态：光球一次「快门」+ 胶囊「看一眼…」+ 用户气泡**立刻**出现并带 📷 角标（不等相机冷启动——把 `ChatScreen.tsx:195-210` 的顺序倒过来：先落气泡，`vision_frame_id` 迟到再补进 meta；SessionCore 加 `__bubbled` 同款入口，HMI 已有先例）。拍完 0.5s 内不出预览（红线：图像不落端、不落记忆——**预览也是一份落端**，刻意不做）。

### 5.6 主动消息呈现

- 前台：沿用 💡 气泡 + 类别头；**新增**：`priority` 高（scene_verify / 告警词表命中，HMI `ALERT_RE`）的主动消息进 **Dock `task` 位短驻 6s**（琥珀），并触发光球 `speaking`（若治理器决定播报）。
- 后台/锁屏：属 U5（§9）。

### 5.7 离线与弱网

- 判死→`offline`：光球 `muted` + 胶囊红 + Dock `queue`；恢复→补达时 Dock 逐条消失。
- 探活残留窗（M3-W 记录：判死前 ~25s 发出的第一条可能丢）**在 UI 上如实说**：`reconnecting` 期间发送后气泡带「发送状态未知」灰字，补达/超时后更新——不许因为 UI 好看就把不确定写成确定。

### 5.8 Onboarding 上品牌（P12）

用 `AuroraBackground + Glass + AuroraOrb(88)` 重排，三步：服务器 → token → 麦克风与定位权限（**权限用途文案**在此屏，`app.config.ts:99-101` 注释点名的合规落点）；保留 dev/staging/prod 入口差异。

### 5.9 Token 层（P13）

`M/ui/tokens.ts`（新）：`space = [4,8,12,16,24,32,48]`、`radius = {sm:8, md:12, lg:16, xl:20, '2xl':24, '3xl':28, full:999}`、`type = {display:32, h1:24, h2:18, body:15, caption:12, mono:'monospace'}`、`motion = {fast:120, base:180, slow:260, orbIdle:4000…}`、`target = {parked:48, driving:56}`。逐值照 A-1 §「间距/圆角/字阶」；`Palette.font()` 扩成 `scale(size, kind)`，**「大字」同时放大 target 与行高**。迁移只做**新组件必用 + 触碰到的旧组件顺手换**，不做全仓扫荡（34 个卡渲染器逐处改是独立批，与 M3-V「等宽数字铁律」同一处置）。

---

## 6. 行车档（Driving Mode）

**触发**（任一）：Edge 帧 `driving=true`（`store.ts:286`，已存在）；用户在语音层/设置里手动切「行车」；平板车载档 token（全 scope）+ 横屏 + keep-awake 同时成立时**建议**开启（弹一次胶囊「切到行车档？」，不自动切——自动切会在副驾用平板时误伤）。

**规则**（照 Figma A-1/A-6 行车条款 + NHTSA 视线原则）：
- 语音层常驻（不自动收起到 idle，收起到 `armed`），光球 120dp 居中；转写/回答字号 18/20pt；一屏只有一张卡、只显示标题 + ≤2 个字段 + 1 个主按钮。
- 过程区单行锁定（A-6.3 行车形态）；快捷 chips ≤3；**文本输入框隐藏**（点光球右侧「键盘」图标才出）。
- 目标 ≥56dp；光球动效 ×0.5 频率、×0.6 透明度（A-1 §10）；TTS 自动播报强制开；barge-in 开。
- 确认：§5.3 车速门禁；手机档无车控 scope 时此条不触发但 UI 判据在场。
- 横屏（车载支架）：光球与转写在左 40%，卡/回答在右 60%（§7.2 medium-height 规则的行车变体）。
- 退出：Edge `driving=false` 持续 30s 或用户手动；退出时不清对话记录。

---

## 7. 平板与折叠屏

### 7.1 尺寸类（替代 `min(w,h)>=600`）

按 Material 3 窗口尺寸类，**宽高各算**（`useWindowDimensions`，旋转/展开即时重算）：

| 宽度类 | 范围 | 高度类 | 范围 |
|---|---|---|---|
| compact | < 600dp | compact | < 480dp |
| medium | 600–839dp | medium | 480–899dp |
| expanded | ≥ 840dp | expanded | ≥ 900dp |

我们的真机（换算按 420dpi 估，**实施时用 `adb shell wm size`/`wm density` 读实**，别信换算）：MIX Fold 4 外屏 1080×2520 ≈ 411×960dp → 竖屏 compact×expanded，横屏 expanded×compact；内屏 2224×2488 ≈ 847×948dp → **expanded×expanded**（若系统密度取 440 则 809dp = medium，所以双栏阈值**不要卡 840**，见 7.2）。真平板（E3 一直没到位，实施计划 R8）按同表处理。

### 7.2 布局

| 形态 | 判据 | 布局 |
|---|---|---|
| 单栏 | width compact，或 height compact 且非行车 | §5 手机形态原样 |
| 单栏 + 舞台抽屉 | width medium 且 height ≥ medium | 对话全宽；右缘一个 48dp「舞台」把手，拉出 320dp 玻璃舞台（车况/提醒/焦点卡），半开时对话区随之压缩 |
| **双栏** | **width ≥ 720dp** 且 height ≥ medium | 左对话 + 右舞台；舞台宽 `clamp(320, 42%, 440)`（现状 `min(400, 42%)` 放宽上限）；语音层只覆盖**左栏**，右栏舞台同步显示主卡（舞台=卡的大视图，同 HMI `ContextualStage` 场景判定：地图族→地图、天气→天气、提醒→日程、其余→焦点卡） |
| 横屏车载 | width expanded × height compact + 行车档 | §6 横屏行车布局 |

双栏阈值取 720 而不是 840：折叠内屏密度不确定（7.1），而**「内屏展开一定双栏」是用户对折叠屏的基本预期**，宁可 medium 高段也给双栏。

### 7.3 折叠姿态（book / tabletop）

需要 Jetpack WindowManager 的 `FoldingFeature`——RN 无内置，选项：`@logicwind/react-native-fold-detection`（现成 hook：`isTableTop/isBook/isFlat`，§14 [S-F1]）或自写 30 行 Expo 模块。**任一都是新原生依赖 ⇒ 归入 B3 的那一次重建**，且按坑账 §9.43 验 `PackageList.java`。
- **tabletop**（半开、铰链水平，手机横放桌上）：上半屏 = 舞台 + 大光球，下半屏 = 转写 + Composer——语音层天然分到上下两半，铰链线不压内容。
- **book**（半开、铰链垂直）：强制双栏，铰链落在两栏 gap 中（gap 取 `foldBounds.width + 16`）。
- flat：按 7.2。

### 7.4 外屏 ↔ 内屏接续

展开/折叠是一次 configuration change；现状「旋转中消息流不丢位置」已验，扩到：语音层状态、Dock、Presence 全在 store 不在组件（U1 本来就这么做）；语音层在形态切换时**不收起**，只重排；正在录音的 PTT 在外屏→内屏切换瞬间**按松手处理**（手指物理上一定离开了外屏）。

### 7.5 多窗 / 分屏 / 返回手势

不做专门布局，但**必须不崩不遮**：分屏下 width 可能落到 compact 而 height medium——按 7.2 单栏；键盘避让在分屏尤其重要（7.6）。
返回手势：RN 0.81 起对 targetSdk 36 **默认开启预测性返回**（[S-A3]），Android 16 真机上二级页（设置/地图/画廊）回退会带系统预览动画——语音层与 Dock 都不是路由页，**返回手势先收语音层、再退页面**（`BackHandler` 顺序写进 `presence` 的消费面），「根屏返回=退 Activity」的 M3-W 定案不变。

### 7.6 键盘避让（P6，两步）

1. 零依赖修法：`behavior='padding'` 对 Android 同样启用 + `keyboardVerticalOffset` 取顶栏高度；Expo `android.softwareKeyboardLayoutMode` 保持 `resize`；edge-to-edge（RN 0.86 默认）下用 `useSafeAreaInsets().bottom` 补底部。Maestro 断言：`inputText` 后**不 `hideKeyboard`** 直接 `assertVisible composer-send`。
2. 若 1 在 HyperOS 输入法（独立窗口顶层）下仍不稳 ⇒ `react-native-keyboard-controller`（原生，归 B3 重建）。

---

## 8. 声音、触感与无障碍

- **提示音**：唤醒确认音是 M4 挂账（`handsFree.ts:17-20`，mp3 解码因 FFmpeg 关闭不可用）。**不用 mp3**：`react-native-audio-api` 是 Web Audio 实现，用 `OscillatorNode + GainNode` 合成两音上行（C5→G5，各 60ms，gain 0.15）——零资源、零重建、与 `pcmPlayer` 同一 `AudioContext`。只在 `listening`（唤醒）与 `attention` 进入时响；`speaking` 首音**不**响（与 TTS 叠）；设置项「提示音」默认开、行车档强制开。
- **触感**：`expo-haptics`（Expo SDK 内，需 prebuild ⇒ B3）：唤醒轻、确认双、判死一、快门轻；设置项默认开。
- **减少动效**：读 `AccessibilityInfo.isReduceMotionEnabled()` → 光球所有循环降到静帧 + 单次过渡（对应 `aurora.css:266-272` 那条，App 端目前缺）。
- **TalkBack**：光球 `accessibilityRole="button"` + label 随 Presence 变（「小舟，在听」）；转写区与回答区 `accessibilityLiveRegion="polite"`；Dock 进入时 `AccessibilityInfo.announceForAccessibility`；卡片按钮补 role/label。
- **对比与字号**：token 层落地后「大字」= 1.15× 文字 + 1.1× 目标 + 行高；浅色主题下胶囊/Dock 用不透明底（坑账 §9.36：压在不可控内容上的浮层一律不透明——语音层压在变暗的对话上算可控，用玻璃；Dock 压在列表上，用不透明）。

---

## 9. 出 App 在场（U5，跟着 M5；本轮只定形态）

| 机制 | 平台事实 | 我们怎么用 | 前提 |
|---|---|---|---|
| **Live Updates**（`Notification.ProgressStyle`） | Android 16（API 36）起对三方开放：`POST_PROMOTED_NOTIFICATIONS` + `setRequestPromotedOngoing` + 必须 ongoing、有 `contentTitle`、不用 RemoteViews（§14 [S-A1]）；真机 Android 16 ✓ | 长任务（沿途充电规划 / 商户订单 / 提醒倒计时 / 导航接续）作为进度式通知常驻状态栏与锁屏——**它就是我们能用的「实况窗 / 超级岛」** | 前台服务（M5）；Android <16 回落普通 ongoing 通知 |
| **默认数字助理角色**（`VoiceInteractionService` + `ROLE_ASSISTANT`） | AOSP 公开机制，三方可申请；**设置路径**「设置 → 应用 → 默认应用 → 默认数字助理」；触发靠系统手势/电源键设置（§14 [S-A2]）。**HyperOS 上电源键是否只绑小爱 = 未验**（§13 Q5） | 长按/手势直接升起语音层（跳过冷启动进对话页）；实现是一个原生 Service 壳 + deeplink `xiaozhou://voice` | 原生模块（B3 重建）；真机验证角色是否可选 |
| **QS Tile**（`TileService`） | 三方可用 | 下拉一键「小舟」→ 语音层 | 原生 Service |
| **App Shortcuts** | 三方可用（`expo-quick-actions` 或 config plugin） | 长按图标：「说话」「车况」「今日提醒」 | 无需重建（config plugin 需 prebuild） |
| **小组件** | 三方可用（`react-native-android-widget`） | 光球 + 一句今日摘要 + 车况三格 | B5 再评估 |
| 系统浮层（`SYSTEM_ALERT_WINDOW`） | 可用但 HyperOS 权限阻力大、后台 socket 仍会被杀 | **刻意不做**（P7） | — |

这一节的每一项都以「M5 前台服务 + 推送」为前提，**本轮只保证 B1–B4 的形态给它们留好落点**：Dock 的 `task` 项与 Live Updates 是同一份数据的两个出口；语音层可由 deeplink 直接升起。

---

## 10. 保留项（刻意不动）

### 10.1 光球十条不变量（源自 Figma A-1 §10 与 HMI/App 两份实现，逐条对照见盘点）
① 正圆，不变形 ② 七层及其顺序 ③ 四色极光固定序 + 高光核 ④ 极光只在 AI 时刻 ⑤ 波纹用青 `#46D6E0` 不用极光 ⑥ 呼吸 + 反向双漩涡、速度编码状态 ⑦ 五态含义（含 `armed/listening`）⑧ 它同时是麦克风、头像、品牌标 ⑨ 最前层 ⑩ 降级脚本化（行车 ×0.5/×0.6、reduce-motion、静态实例不动画）。
**本方案新增的三态（attention / looking / muted）只加环与节律**，不违反 ①–⑩ 中任何一条。

### 10.2 其它
- 对话记录不被临时层取代（P2 的「层」永远落回记录）；`SessionCore` / `requestRouting` / `pendingOps` 语义不改。
- 共享判据不分叉：Presence 是**派生视图**，voiceLoop / sileroEndpoint / visionFrame 一字不动。
- 红线逐条对等：语音层不改挡位、不改采集窗；`looking` 不出预览；声纹不进 App。
- 前台交互档承诺（M3-W 定案）不变；U5 全部跟随 M5。
- 三处虹彩纪律（光球 / 发送键 / 流式光标）+ 新增第四处「语音层顶缘」——**这是 Guidelines `:113-119` 明文允许的那一处**，不是扩面。
- `hmi/` 零改动；共享白名单只在 B2 可能新增 `nav.mjs` 之外的读侧模块时走台账流程。

---

## 11. 分阶段落地与验收

### 11.1 批次

> 批次编号 **B1–B5** 与 §0 的升级点 **U1–U5 不是一回事**：一个批次可以装多个升级点（B1 装 U1 + U3 的 Dock；B4 装 U4）。评审时按 U 谈价值、按 B 谈排期。

| 批 | 装的升级点 | 范围 | 重建? | 预估 | 依赖 |
|---|---|---|---|---|---|
| **B1 在场与锚** | U1 + U3 | `presence.ts` + 测试；光球三新态；状态胶囊；Focus Dock 四项；确认倒计时精确调度；Onboarding 上品牌；token 层骨架；键盘避让第一步；`/state-gallery` 调试屏 | 否（全 JS） | 3–4d | — |
| **B2 语音层** | U2 | Voice Sheet（PTT / 唤醒 / S2S 三入口）；S2S 轮沉淀（`appendS2sTurn` + 角标）；边缘极光；主卡/折叠卡；follow-up chips；视觉抓帧反馈 + 先落气泡；回声提示；播报三档 | 否 | 4–5d | B1 |
| **B3 原生一次重建** | （U4/U5 的原生前提） | `expo-haptics`；折叠姿态模块；（若需）`react-native-keyboard-controller`；默认助理角色的 Service 壳（**只注册不启用**，等真机验证） | **是（一次）** | 2d + 一趟构建 | B1；坑账 §9.43 验 `PackageList.java` |
| **B4 形态与行车档** | U4 | 尺寸类 × 姿态布局；舞台抽屉 / 双栏 / tabletop；行车档全套（触发、布局、门禁、动效降级）；无障碍与 reduce-motion；提示音合成 | 否（依赖 B3 的姿态模块，缺席时按 flat 降级） | 4–5d | B1–B3 |
| **B5 出 App 在场** | U5 | Live Updates / QS Tile / Shortcuts / 小组件 / 角色启用 | 是 | 随 M5 | **M5 前台服务 + 推送** |

**顺序理由**：B1 是所有后续消费面的唯一真相，必须先；B2 用到 B1 的 orb/capsule；B3 把新原生依赖压成一次 22–38 分钟的构建（实施计划 M3-B 的教训：分两次不值当）；B4 的姿态在 B3 缺席时按 flat 降级，所以 B4 不被 B3 阻塞，只是少一种形态。

### 11.2 每批验收（含反向验证，沿用实施计划 §0 纪律）

- **B1**：`presence.test.ts` 逐维断言优先级（反向：交换任意两级优先级 → 对应用例红）；真机：免唤醒开→光球 `armed` 青环可见（截图，`screencap -d`）；唤醒→`listening` 环 + 胶囊「在听…」；危险动作→Dock 出现 + 倒计时递减 + 到期后记录里有「确认已过期」一行（**这条是 P5 的判据**）；断网→`muted` + 「已断开」；`/state-gallery` 13 态全部有样本且深浅主题各一套截图；键盘：Maestro 三条 online 流去掉 `hideKeyboard` 仍通过。
- **B2**：真人说一句（R3 同类，需泓舟）→ 语音层升起 → 转写大字 → 回答流式 → 8s 追问窗环递减 → 收起后对话记录里两条气泡逐字等于层里显示的；S2S 挡位走一轮（M4 挂账「端到端未验」在此一并）→ 记录里出现带「端到端」角标的两条；`card_group` 两卡 → 主卡在上、「还有 1 张 ›」可展开；「这是什么」→ 用户气泡**先于**相机出现（时间戳比对）；打字提问在「自动」档不出声、语音提问出声。
- **B3**：`PackageList.java` 含新 Package；haptics 四种触感在真机各触发一次；姿态 hook 在 Fold 4 半开时报 `isTableTop=true`（`cmd device_state` 无法模拟半开，**要真机手折**）。
- **B4**：形态矩阵截图（外屏竖 / 外屏横 / 内屏 / 内屏 book / tabletop）；行车档：`driving=true` 帧（云栈 debug 注入）→ 目标 ≥56dp（无障碍扫描器读实）、输入框隐藏、过程区单行；`speed>5` → 确认全屏拦截；reduce-motion 开 → 光球静帧。
- **B5**：随 M5 计划另立。

### 11.3 Maestro 扩流（进 `mobile/e2e/`）

`05-voice-sheet-ptt`（按住 testID `composer-orb` 2s → 断言 `voice-sheet` 可见 → 松手 → 断言收起后新气泡）—需真人/直灌音频，标 `manual`；`06-confirm-dock-expire`（危险动作 → `dock-confirm` 可见 → 等到期 → 断言「确认已过期」文本）；`07-tablet-two-pane`（`cmd device_state state 3` → 断言 `stage-pane`）；`08-keyboard-no-hide`（去掉 hideKeyboard 的 01 流）；`09-state-gallery`（离线，CI 跑：`xiaozhou://state-gallery` 13 态各 assertVisible）。新增 testID：`composer-orb / voice-sheet / presence-capsule / dock-confirm / dock-slot / stage-pane`。

### 11.4 「不负优化」判据（每条可量、上线前后各测一次）

| 判据 | 度量 | 目标 |
|---|---|---|
| 首反馈时延 | 唤醒命中 → 光球进入 `listening` 的帧时间（Reanimated 回调打点） | ≤ 100ms（现状：文字条更新 ~ 同级；不许变慢） |
| 状态可读性 | 5 名外部用户各看 6 张状态截图说出「它在干嘛」 | ≥ 5/6 正确（现状基线先测一次） |
| 记录完整性 | 一次会话中语音轮（含 S2S）在记录里的条数 / 实际轮数 | 100%（现状 S2S 为 0） |
| 承诺不丢 | 确认到期/关闭无解释消失次数 | 0 |
| 键盘遮挡 | Maestro 08 流 | 通过 |
| 性能 | 同屏循环动画实例数；语音层升起时 JS 帧率 | 常态 1 个（语音层内大球接管、Composer 球转静态）；≥ 55fps（Fold 4） |
| 无障碍 | Android Accessibility Scanner 严重项 | 0（现状未测，先取基线） |
| 回归 | mobile jest / hmi node:test / 4 条既有 Maestro 流 | 全绿，条数只增不减 |

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 语音层把「打字用户」逼进语音心智 | 文字输入不升层；层只由说话动作触发；设置里可关「语音层」回落到胶囊 + 记录（老形态保留为降级路线） |
| Presence 优先级与 voiceLoop 语义打架（如 FOLLOWUP 时来了确认） | `attention` 高于 `followup` 是刻意的：追问窗里出现确认 = 先确认再追问；voiceLoop 那侧 `needConfirm` 镜像已保证「确认/取消」不被本地吞（`ChatScreen.tsx:229` 链） |
| 常驻动画让 `uiautomator dump` 拿不到树（坑账 §9.40/48） | 语音层升起时 Composer 球转静态、层内只一个动画实例；取证一律截图（既有纪律） |
| 折叠姿态库质量未知 | 先 spike（半天）；不可用则自写 Expo 模块包 `WindowInfoTracker`；两条路都在 B3 那一次重建里 |
| 行车档在手机上「验不到」（无车控 scope） | 门禁与 UI 判据用 `driving/speed` 帧驱动，云栈 debug 注入即可验；scope 那半留给平板车载档 |
| 新状态让光球「更花」 | 三新态只加环；同一时刻最多一环一辉光；reduce-motion 全降；`/state-gallery` 里并排对照 Figma A-6 截图 |
| U5 形态定早了 M5 变卦 | U5 只定接口（Dock `task` 数据 → 通知），不写实现 |

---

## 13. 假设与可调点（本轮无法当面确认，按最合理默认执行；泓舟一句话即可改）

| # | 问题 | 默认取值 | 改了会动哪里 |
|---|---|---|---|
| Q1 | 语音层覆盖高度 | 手机竖屏 62%，对话记录变暗仍可见 | §5.2；若要全屏沉浸（小爱式）改 100% + 顶部留记录入口 |
| Q2 | PTT 轻点行为 | 免唤醒开=进 `listening`；关=提示「按住说话」 | §5.1；也可轻点=切换免唤醒（有误触开麦风险，默认不选） |
| Q3 | 双栏阈值 | 720dp | §7.2 |
| Q4 | 行车档是否自动进入 | 只由 `driving` 帧或手动；平板三条件成立时只**建议** | §6 |
| Q5 | 是否申请默认助理角色 | B3 注册 Service 壳、**不启用**，先在 HyperOS 真机验「设置里能否选到 + 电源键/手势是否响应」 | §9 |
| Q6 | 提示音是否默认开 | 开（行车强制开） | §8 |
| Q7 | S2S 轮角标文案 | 「端到端」 | §5.2 规则 2 |
| Q8 | 确认到期文案 | 「确认已过期，需要的话再说一次」 | §5.3 |
| Q9 | Onboarding 是否保留「局域网/自定义」入口 | 保留（dev/staging），prod 隐藏（现状） | §5.8 |
| Q10 | 是否把 `relativeTime` 等第二份实现顺手收敛 | 不动（独立共享面批） | — |
| Q11 | 播报三档的默认值 | 「自动」（语音提问才播报）；旧行为等价于「总是」 | §5.2 规则 8 |
| Q12 | 「备车 / 导航流转到车」要不要立卡 | **本轮不做**，只在 Dock `task` 留落点；前提有两个都不在 App 侧：手机档是否给 `vehicle.control` 子集（产品 + 安全评审，主设计文档 §4.4 明写不由客户端夹带）、焦点跨会话流转（后端） | §2.4 ⑬、§9 |

---

## 14. 参考来源

> 调研日 2026-08-29；公开资料，读数有时效。华为官方支持页 / 开发者文档优先，媒体报道只用于官方页没写的行为描述。

**华为 / 小艺**
- [S-H1] HarmonyOS NEXT 不兼容 APK：<https://cloud.tencent.com/developer/article/2486636>；<https://www.zhihu.com/question/616067090/answer/3156290969>
- [S-H2] 版本事实：6.1 更新说明 <https://consumer.huawei.com/cn/support/content/zh-cn16054599/>；HarmonyOS 7 HDC <https://www.huawei.com/cn/news/2026/6/harmonyos7-hdc>；Pura X View <https://k.sina.com.cn/article_7879849859_1d5acf78306801lzcw.html>
- [S-H3] 小艺导航条（长按 / 拖给小艺）：<https://consumer.huawei.com/cn/support/content/zh-cn16010331/>
- [S-H4] 唤醒方式（电源键 0.5s / 语音 / 耳机 / 协同唤醒）：<https://consumer.huawei.com/cn/support/content/zh-cn16013467/>
- [S-H5] 小艺私语 <https://consumer.huawei.com/cn/support/content/zh-cn16010150/>；圈选/识屏 <https://news.qq.com/rain/a/20241022A0939H00>；6.0 入口清单 <https://www.pingwest.com/a/308454>
- [S-H6] 6.1 伴随式 AI（Pura X Max）：<https://consumer.huawei.com/cn/support/content/zh-cn16093961/>
- [S-H7] 伴随窗三档 / 极简彩条 / 握持换边：<https://hea.china.com/articles/20260514/202605141868369.html>；<https://www.leikeji.com/article/76327>；<https://harmonyosdev.csdn.net/6a1fdcfd10ee7a33f2772bc7.html>
- [S-H8] 5.0 唤醒动效：<https://news.qq.com/rain/a/20241022A095CS00>
- [S-H9] 6.0 智慧光感：<https://consumer.huawei.com/cn/harmonyos-6/>；<https://hmos.ithome.com/archiver/0/891/428.htm>
- [S-H10] 流光彩条 + 提示胶囊 + 停止 / 送后台：<https://consumer.huawei.com/cn/support/content/zh-cn16073021/>
- [S-H11] 付款前弹窗确认：<https://m.leiphone.com/category/industrynews/bvimfeEkEOybfHS1.html>
- [S-H12] 两层容器（上滑进对话页 / 记忆）：<https://consumer.huawei.com/cn/support/content/zh-cn16010214/>
- [S-H13] Beta 半屏面板上手：<https://zhuanlan.zhihu.com/p/704832322>
- [S-H14] 6.0 上划后后台继续：<https://m.c114.com.cn/w241-1299209.html>
- [S-H15] 播报三档（总是 / 静音 / 自动）：<https://consumer.huawei.com/cn/support/content/zh-cn16063214/>
- [S-H16] HMAF：<https://news.qq.com/rain/a/20250620A09YQW00>；白皮书 <https://developer.huawei.com/consumer/cn/doc/guidebook/ai-agent-0000002355199797>
- [S-H17] 实况窗 Live View Kit：<https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/live-view-kit-guide>
- [S-H18] 6.0 实况窗胶囊 subText：<https://harmonyosdev.csdn.net/6a1b7a05662f9a54cb786e24.html>
- [S-H19] Mate X7 自动分屏 / 分屏问小艺：<https://www.163.com/dy/article/KF7VHADS05118774.html>；<https://consumer.huawei.com/cn/phones/mate-x7/>
- [S-H20] 一多布局断点（SM/MD/LG、折痕、悬停）：<https://developer.huawei.com/consumer/cn/doc/design-guides/design-layout-basics-0000001795579413>
- [S-H21] Pura X 折叠布局指南：<https://www.cnblogs.com/HarmonyOS5/p/18953790>
- [S-H22] HiCar 7.0 横屏驾驶模式：<https://www.163.com/dy/article/L3RFMOIQ0511B8LM.html>；HiCar <https://consumer.huawei.com/cn/phones/hicar/>
- [S-H23] 6.1 智感畅行：<https://m.sohu.com/a/1021360022_122004016>
- [S-H24] 小艺建议：<https://consumer.huawei.com/cn/support/content/zh-cn16073900/>
- [S-H25] 6.1 优先通知：<https://m.tech.china.com/redian/2026/0424/042026_1854939.html>

**小米 / 超级小爱**
- [S-X1] HyperOS 4 官网（悬浮态 / 小白条按住说 / 超级岛 130+ / 专家模式 / 对话接力 / 柔光玻璃）：<https://hyperos.mi.com/>
- [S-X2] 08-13 官宣与 Beta 批次 <https://finance.sina.com.cn/tech/roll/2026-08-13/doc-ininctay0148814.shtml>；码号上岛 / 备车 08-28 <https://finance.sina.com.cn/tech/roll/2026-08-28/doc-inipwtfp5785193.shtml>；灵感球 9 月 Beta <https://www.163.com/dy/article/L4BVU6M70511B8LM.html>
- [S-X3] OS2 八种唤醒入口：<https://zhongce.sina.com.cn/iframe/article/view/187217/>
- [S-X4] OS3 手势线按模态分工：<https://news.qq.com/rain/a/20250828A06Q3700>
- [S-X5] 三指上滑小爱记忆 / 气泡上岛：<https://news.qq.com/rain/a/20250828A08C0000>
- [S-X6] OS2 全屏水波纹唤醒：<https://www.pingwest.com/a/301471>
- [S-X7] OS4 悬浮态 + 岛上思考/执行 + 完成自动展开：<https://www.163.com/dy/article/L474PCUC0511CPVM.html>
- [S-X8] OS4 Beta 上手：<https://www.163.com/dy/article/L4MHPHBL0511B8LM.html>
- [S-X9] 超级岛大岛 A/B 区与 ≤4 字规格：<https://dev.mi.com/xiaomihyperos/documentation/detail?pId=2143>
- [S-X10] 展开态模板 / 5s 收起 / 1h·12h：<https://dev.mi.com/xiaomihyperos/documentation/detail?pId=2142>；<https://dev.mi.com/xiaomihyperos/documentation/detail?pId=2140>
- [S-X11] 岛手势 <https://news.qq.com/rain/a/20250828A06LTD00>；专用字体 <https://www.ifanr.com/1635881>
- [S-X12] OS2 多模态 / 地图卡 <https://finance.sina.com.cn/tech/roll/2025-05-13/doc-inewkruh2219868.shtml>；陪伴模式 <https://app.mi.com/details?id=com.miui.voiceassist>
- [S-X13] OS3 一步直达只跳转 <https://eu.36kr.com/zh/p/3443194625365637>；3000+ 能力 <https://t.cj.sina.com.cn/articles/view/1826017320/6cd6d02802001ijay>
- [S-X14] 专家模式 <https://finance.sina.com.cn/tech/mobile/n/n/2026-08-19/doc-ininuvaw8423761.shtml>；积分计费 <https://finance.sina.com.cn/tech/roll/2026-08-13/doc-ininecsn4741129.shtml>
- [S-X15] Flip 外屏适配指南：<https://dev.mi.com/xiaomihyperos/documentation/detail?pId=2026>
- [S-X16] Pad 工作台：<https://finance.sina.com.cn/tech/digi/2025-09-30/doc-infshhpq8059084.shtml>
- [S-X17] SU7 车机小爱免唤醒：<https://k.sina.com.cn/article_7857141524_1d4527714019020xk0.html>
- [S-X18] 备车 / 导航流转 <https://k.sina.com.cn/article_7879776882_1d5abda7206801mk92.html>；<https://www.163.com/dy/article/KR22A6420531THX3.html>
- [S-X19] CarWith 驾驶模式：<https://news.mydrivers.com/1/942/942322.htm>
- [S-X20] 生命感美学 <https://www.ithome.com/0/737/760.htm>；MiSans <https://hyperos.mi.com/font/zh/>
- [S-X21] 超级岛三方接入流程（白名单审核）：<https://finance.sina.com.cn/tech/digi/2025-10-23/doc-infuwfyp7043252.shtml>
- [S-X22] 36kr 对外卖岛面积的批评：<https://www.36kr.com/p/3444790738196866>

**Android / React Native**
- [S-A1] Android 16 Live Updates（`ProgressStyle`）：<https://developer.android.com/about/versions/16/features/progress-centric-notifications>；<https://developer.android.com/develop/ui/views/notifications/progress-centric>
- [S-A2] 默认数字助理角色：<https://developer.android.com/reference/android/app/role/RoleManager>；<https://developer.android.com/reference/android/service/voice/VoiceInteractionService>；设置路径示例 <https://blog.csdn.net/Slaven230101/article/details/146213036>
- [S-A3] RN 0.81 预测性返回默认开启：<https://reactnative.dev/blog/2025/08/12/react-native-0.81>
- [S-F1] 折叠姿态：<https://github.com/logicwind/react-native-fold-detection>；Jetpack WindowManager <https://developer.android.com/develop/adaptive-apps/guides/foldables/make-your-app-fold-aware>

**项目内**
- 盘点读数：`mobile/src/**`（2026-08-29 逐行核对，file:line 见 §1）；HMI 光球 `hmi/src/components/aurora/AuroraOrb.tsx`、`hmi/src/aurora.css`；Figma Make 导出 `docs/design/【新】座舱Agent-HMI-A-1 Design System.zip`（`guidelines/Guidelines.md` §10 光球、§触控目标）与 `A-6 Conversation States.zip`（六态 + 行车开关）。
