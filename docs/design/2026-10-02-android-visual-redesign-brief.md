# 小舟随行 Android 视觉重构 · 设计 Brief（Figma）

- **状态**：已批准（2026-10-02，方向 B；§15 其余决定按推荐执行，在 M1 检查点①复核），Figma 设计阶段进行中（§14）；
  代码实现另立实施计划。
- **进度**：M1 方向样张已出，等待检查点①——Figma 文件「小舟随行 · Android Visual v3」
  （`1jdZ6Cwp8pEtQJJUwg6NHS`，页「M1 · 方向样张」）：5 屏 × 深浅；变量 5 个集合、文字样式 14 个、阴影样式 3 个、
  图标 24/68、光球 16 个变体、核心组件 10 个。浅色光球边缘与紧邻底色的亮度差在 Figma 渲染上从 30.4 升到 47.9
  （判据 ≥40）。M0 的现状截图尚未取。
- **交付对象**：Figma 设计阶段的执行者（Claude Code 经 Figma MCP `use_figma` 写画布）与后续实现者。
- **关联**：
  - 代码：`mobile/src/**`（主战场）；`hmi/src/types.ts`（卡片 / 消息契约，不改字段）；
    `hmi/src/components/icons.gen.ts`、`icons.custom.ts`（共享图标数据）。
  - 交互制度（本次不重开）：[UX v2.2 在场方案](2026-08-29-mobile-ux-v2-presence-redesign.md)，以及后续 B1–B5、打磨批 A–G、
    [语音层跟随](2026-09-17-android-voice-sheet-focus-follow.md)、[控件两档制](2026-09-11-android-sheet-controls-map-tabletop.md)。
  - 品牌源：Aurora Glass 设计契约 `guidelines/Guidelines.md` v1.0（`docs/design/【新】座舱Agent-HMI-A-*.zip`），
    [HMI 设计交接简报](2026-06-28-figma-hmi-dashboard-redesign-brief.md)。
  - Figma 落稿规则：[`docs/guides/figma-design-system-rules.md`](../guides/figma-design-system-rules.md)。
- **读数基线**：`main` @ `21b6e4b3`（2026-10-02）。文中 `文件:行号` 指这一版。文档代号见附录 A。
- **截图说明**：`docs/images/mobile-*.jpg` 早于打磨批 A–G（还带助手头像、欢迎页双排推荐），**不能作为现状证据**。
  设计阶段第一步按 §14 M0 重新取截图。

---

## 0. 一页速读

**问题**：App 的视觉是把 1920×1080 车机大屏的 Aurora Glass「逐值」搬到手机上（`mobile/src/ui/theme.ts:2`；MI:995-1005）。
有三处不成立：

- 材质的前提——实时背景模糊，加上背后有一层活的场景——在 RN 上不存在；
- 光球和极光在小屏上用得过量；
- 排版、形状、颜色一直靠打磨批 A–G 逐处打补丁维持。

而立项时的结论本来就是「App 的 UI 本来就要移动优先重新设计」（H:29）。

**目标**：保留小舟的品牌内核（光球、极光只留给 AI 时刻、深空色基因、交互蓝），结构层改用 Android 原生的组织方式：

- 色彩按角色分层，用色调区分层级；
- 字阶按用途定义；
- 形状有统一刻度；
- 组件遵循平台惯例；
- 动效用弹簧。

重建后的设计系统要做到三点：**手机优先、深浅两套主题同等完成、每个视觉决定都能映射到代码 token**。Figma 里要覆盖全部页面、状态、尺寸类和行车档。

**推荐方向**：B「Aurora × Material 3」（§4）——品牌层不变，结构层换成 Android 原生。另有两个备选：A 延续 Aurora 只修问题；C 全新视觉。

**不变的东西**（§3.2）：

- 交互语义：在场模型、手势契约、承诺面、「确认只投影 VAL」、隐私在场、语音层结构；
- 数据契约 `types.ts`；
- 安全红线；
- 光球造型的十条不变量。

这次改的是**视觉与版式**。

**Figma 侧前提已具备**：2026-10-02 跑 `whoami`，账号是 Professional + Full 座席。可以写画布，读调用每天 200 次，每个变量集合最多 10 个模式。

**需要你拍板的**：§15 列了 8 个决定，每个都附了推荐项。

---

## 1. 产品与使用情境

### 1.1 它是什么

- 定位是「语音优先的随身座舱助手」，而不是「贴了光球的聊天软件」（P:16）。
- 与 HMI 是两个用户端，共用一个后端大脑：同一 user_id 共享记忆，会话各自独立。两端共享的是判据，不是 UI（`AGENTS.md:13-15`）。
- 交互主线：
  1. 轻点光球开始说话（P:268）；
  2. 语音层升起，依次出现转写 → 思考 → 回答 → 主卡；
  3. 收起后，这一轮沉淀进对话记录。
- 系统欠用户一个动作或一个结果时（确认、补槽、长任务、离线队列），这件事钉在 Focus Dock 上，直到解决。

### 1.2 谁在用、在哪用、用什么设备

| 身份 | 场景 | 文字输入 | 控车 |
|---|---|---|---|
| A 手持陪伴端（默认） | 手持、步行、副驾 | 常驻 | 不控车 |
| B 支架 / 副驾 | 车载支架 | 行车时折叠成键盘键 | 看 token |
| C 可信车载平板 | 车内固定 | 行车时隐藏 | 全 scope，经 VAL |

身份由 token scope 加设备角色决定。**窗口尺寸不决定权限，横屏也不代表用户是驾驶员**（P:452-458；`drivingMode.ts:52-55`；默认值 `handheld`，见 `store.ts:117`）。

| 设备 | 系统 | 实测 dp | 角色 |
|---|---|---|---|
| OPPO PEUM00（折叠） | Android 14 / ColorOS 14，60Hz | 外屏 359×717；内屏 698×652；内屏默认是兼容窄窗，约 392dp 宽 | test，可供 agent 取证 |
| Xiaomi MIX Fold 4（折叠） | HyperOS 3 / Android 16，120Hz | 外屏 360×840；内屏 741×829 | compare，你的主力机，只读对照 |

（A3:151；A4:343；B4:2710-2718；UB:426）

- 设备覆盖：没有普通直板机，没有平板，Android 10–11 未覆盖（FR:163；UR:129）。
- 使用者：目前唯一真实使用者兼验收人是你（UR:121）。五人外部测试还没开始（A10P:16）。

### 1.3 与 HMI 的关系

- **品牌同源**：光球「小舟」、极光四色、交互蓝 `#46D6E0`、深空底 `#06080F`、24 格 1.8 描边线性图标、A 股红涨绿跌、置信度三色。
- **形态不同**：
  - HMI 是 1920 横屏两栏，玻璃面板背后有活的场景（地图、氛围光）可透；
  - 手机是单手竖屏，常在日光下用，背后没有可透的东西，还要照顾折叠屏和车载支架。
- **这次不动 HMI**：两端共享的只有图标数据和纯逻辑模块（`mobile/shared-allowlist.json`）。

---

## 2. 现状诊断

### 2.1 已经做对、必须保留的

- **光球是唯一状态锚**：主态由 `derivePresence` 统一派生，按 offline > attention > looking > listening > speaking > thinking > followup > armed > idle 取第一个（P:209；`presence.ts:326-334`）。胶囊一次只说一件事。
- **语音层**：固定头区（光球 + 胶囊）加可滚内容区，焦点跟随最新内容；三档高度带内容下限（FF:17-82）。
- **Focus Dock**：G0 实色，不轮播，只钉一项，其余收进「另有 N 个待处理 ›」；倒计时只读服务端窗口（`FocusDock.tsx:1-4,223-233`）。
- **控件两档制**：按钮 = 触控目标 48/56；胶囊的视觉高 36/44，外框撑满触控目标（`tokens.ts:24-31`；`Pill.tsx`）。
- **对比度进了 jest**：WCAG ≥4.5:1，alpha 先按底色合成再算（`mobile/test/theme.test.ts`）。
- **动效预算**：同屏常态只有一个循环动画；30s 无交互后静置；减少动效时一律静帧（`orbPolicy.ts`；`orbIdle.ts:10`）。
- **卡片框架**：兜底卡绝不返回 null；每张卡包 ErrorBoundary；`_prov` 四态角标不许被任何壳吞掉；card_group 主卡全展、其余折叠（CR:114-160；`cardGroup.ts`）。
- **布局判据集中在纯函数**：`sizeClass.ts`（尺寸类 × 姿态 × 行车）。
- **Composer 手势契约**：轻点始终能说；长按 PTT，上滑取消；行车档禁用空间手势（P:278-287）。

### 2.2 问题（按影响排序，附证据）

**D1 材质前提不成立**

- Aurora 的玻璃要靠 `backdrop-filter` 折射背后活的场景才成立（契约 §2、§6）。RN 没有这个能力：`Glass` 实际只是 5.6% 白底加四边不等光边框（`Glass.tsx:1-3`；`theme.ts:3-4`）。
- 后果：
  - 压在地图和文字上的浮层只能改实色（`Pill.tsx:27-28`；`map.tsx:166-170`）；
  - 浅色主题的白色顶缘变成一条孤立白线，只好改成深色 0.05（`theme.ts:91-95`）；
  - 语音层要额外垫 0.58 的 tint 才能读（`VoiceSheet.tsx:96-104`）；
  - 真模糊全仓只有一处，必须包 `BlurTargetView` 才不会静默回落（`ChatScreen.tsx:273-288`）。
- 手机上的「玻璃」实际看起来就是灰色半透明框。

**D2 光球过量，焦点互相竞争**

- 欢迎态同屏有三颗光球：顶栏 30、欢迎 88、Composer 44（`ChatScreen.tsx:135,536`；`Composer.tsx:223`）。
- 欢迎大球和 Composer 球同时在跑循环，超出「同屏一个循环」的预算（`ChatScreen.tsx:375,494`）。
- 顶栏球不能点，只剩装饰作用。光球的三重身份（麦克风 / 头像 / 品牌标，P:563⑧）在这里被拆散了。
- 极光同时用在发送键上（`Composer.tsx:313,322`）。在手机上它成了屏幕上最饱和的常驻元素，「AI 时刻」的稀缺性被冲淡。

**D3 底部交互带过厚，动作难以分辨**

- 有待确认事项时，从上到下叠了四层：Dock（标题行 + 倒计时条 + 48dp 按钮行）、状态胶囊（48 热区）、chips 行（48）、Composer（56 光球热区 + 输入框 + 发送键）（`ChatScreen.tsx:460-501`）。在 359×717 外屏上实际占多少，留到 M0 截图实测。
- 泊车态发送键只有 44dp，低于 48（`Composer.tsx:317`；RT:115）。
- 外部评审建议先验证「用户能否区分『停止播报』『取消任务』『结束收音』」（GR:290）。这三个动作分在合一键三态、层内停止键、隐私栏按钮三处，视觉上都是琥珀色或普通文字按钮。

**D4 没有手机字阶**

- 字号字面量 297 处，共 12 种取值；其中 11pt 有 116 处。`TYPE` token 只被引用 56 次。
- 「数字等宽铁律」没有落地（`tokens.ts:7-8`；MI:1025）。
- 字体没有打包：正文用 OEM 系统字体，等宽用系统 monospace（`tokens.ts:16`），与原计划「Inter / Noto 本地打包」不一致（H:224）。

**D5 颜色靠字面量维持，浅色主题是二等公民**

- 20 个文件里有 65 处 hex、92 处 rgba 字面量。
- 实色底 `#0A0E1A/#FFFFFF` 和琥珀边 `rgba(245,158,11,.38)` 散在 4 个组件里，浅色主题下仍用深色主题的琥珀（`FocusDock.tsx:64`；`PrivacyRail.tsx:41`；`ProactivePresenter.tsx:64,67`；`S2sConsentSheet.tsx:34`）。
- 卡片里主客队色、行程天色板是硬编码 hex，不跟主题走（`infoCards.tsx:305`；`navCards.tsx:340`）。
- **光球不读色板。浅色主题下，光球边缘与壳底的亮度差只有 12.4/255，判据是 ≥40**（B4:2824-2846）。「垫暗盘」的补丁实测方向相反：光球是半透明的，暗盘会把球一起压暗。补丁已还原，问题转给「动光球浅色高光参数」，此后一直没人接。

**D6 卡片：层层嵌套、结构各写各的、行车态失真**

- 卡片放在助手气泡里，形成二级嵌套：气泡内边距 14/11，卡片再内边距 12（`MessageBubble.tsx:189-198`；`parts.tsx:28-36`）。
- 35 个卡型 key 由 33 个渲染器实现：头部写法有 3 种，主数字字号有 7 种，圆角有 6 种，按钮有 4 种样式，**所有可点区域都没有按下反馈**（代理清单 §4）。
- 卡内的 Pill 不传 `fontScale`，大字档下不放大。
- 时间与置信度直接显示 ISO 原文和英文枚举（`infoCards.tsx:198,205`）；路线时长不换算成小时（`navCards.tsx:238`）。
- 行车压缩卡（`DrivingCardSummary.tsx:28-75`）的问题：
  - reminder_card、scene_list、manual、parking_fee 的标题显示原始 type 名；
  - 天气卡没有温度，股票卡没有价格，intent_choice 的选项全部丢失；
  - 各渲染器自己合成的按钮在行车态全部丢失。

**D7 形状没有刻度**：`borderRadius` 字面量有 14 种取值，其中 10、14、18 等落在 `RADIUS` 刻度之外（`SettingsScreen.tsx:51`；`VehiclePanel.tsx:171`；`navCards.tsx:297`）。

**D8 支持页没做手机设计**

- 设置、车况、地图、引导四个页面都不读尺寸类，在折叠屏内屏上是满宽单列（代理清单 §2）。
- 舞台面板把内部名「舞台 · 双栏」显示给了用户（`StagePane.tsx:83-85`）。
- 车况「其他」展开后直接显示英文键名（`VehiclePanel.tsx:246`）。
- 地图不可用时显示的是开发者诊断（`map.tsx:120-134`）；引导页用的是运维语言（UR:70）。

**D9 动效成本高**

- 光球持续动画时，空闲 CPU 约 150%，主要花在 RenderThread 和阴影栅格化上；静置后降到约 9.5%（PR:39-43,254-255；`orbIdle.ts:3-5`）。
- 光球七层里有三层是实时模糊。目前靠 30s 静置兜底。

**D10 平台惯例缺位**

- 对话页是自绘顶栏，支持页是原生 Stack 标题栏，两套并存（`ChatScreen.tsx:525-585`；`app/_layout.tsx:40-44`）。
- 没有按下反馈（ripple）；预测式返回已关闭（`mobile/app.config.ts`）。
- 语音层暗区：方案写 40%，代码是 60%（`VoiceSheet.tsx:452`）。
- 「减少透明度」的说明写的是「改用不透明底」，实际回落到 58% 的 tint（`SettingsScreen.tsx:514`；`VoiceSheet.tsx:269`）。

---

## 3. 目标、不变量与非目标

### 3.1 目标

- **G1 手机优先**：以 360dp 宽的外屏为基准，单手可达，日光下可读。
- **G2 深浅同权**：浅色主题的完成度与深色相同，包括光球在浅色底上的可见度。
- **G3 品牌内核保留，而且更稀缺**：光球与极光只在 AI 时刻出现，一屏只有一颗光球。
- **G4 一套可映射的 token**：每个视觉值都绑定到 Figma Variables，变量名与代码 token 一一对应（§9）。
- **G5 状态全覆盖**：在场 8 态 × 页面 × 承诺面 × 降级态，每种组合在 Figma 里都有对应的帧或组件变体。
- **G6 自适应**：compact / medium / expanded × book / tabletop × 行车档 × 横屏。
- **G7 可实现**：只用 RN 0.86 能稳定画出来的东西（§12）。
- **G8 性能预算**：同屏一个循环动画；材质尽量不依赖实时模糊。

### 3.2 不变量（本次不重开）

- **交互语义**：
  - 在场多轴模型与主态优先级；
  - Composer 手势契约；
  - 承诺面：只钉一项、不轮播、G0 实色；
  - **确认只投影 VAL 的结论**，UI 不自定车速门禁或全屏拦截（P:356-374）；
  - 隐私在场的内容与入口；
  - S2S 开录即告知；
  - 语音层是对话记录的视图，不是独立状态。
- **数据契约**：`hmi/src/types.ts` 不增删字段。设计稿要新字段，单独走契约流程。
- **安全红线**：`CLAUDE.md` §5。
- **光球十条不变量**（P:563）：正圆不变形；七层及其顺序；四色固定序 + 高光核；只在 AI 时刻；波纹用青；呼吸 + 反向双漩涡；五态含义；同时是麦克风 / 头像 / 品牌标；最前层；降级脚本化。
- **已明确拒绝、本次不复活的方案**：全屏语音层、球放在层底、Dock 轮播、卡片轮播、系统悬浮胶囊、相机预览、全卡玻璃化、同屏多个动态模糊、引入 Skia、用弹簧动画改高度、支持页底部常驻两栏（代理摘要 §5）。

### 3.3 非目标

- 不写代码。实现另立实施计划，排在设计定稿之后。
- 不改 HMI 和 Dashboard。
- 不做出 App 在场（Live Updates、小组件、QS Tile），这部分跟 M5。
- 不做 iOS。
- 不引入新的原生依赖（字体打包算新决定，见 §15-6）。

---

## 4. 设计方向

| | A 延续 Aurora，只修问题 | **B Aurora × Material 3（推荐）** | C 全新视觉 |
|---|---|---|---|
| 品牌连续性（与车机同一个小舟） | 最高 | 高：光球、极光、配色基因都保留 | 低 |
| 手机原生感 | 低：桌面玻璃语汇仍在 | 高：色调分层、角色字阶、平台组件 | 取决于新方向 |
| RN 可实现性 | 中：玻璃仍要靠 tint 补丁维持 | 高：主体是实色色调面，模糊只做增强 | 取决于新方向 |
| 浅色主题 | 继续当补丁 | 一开始就当一等主题设计 | — |
| 工作量 / 风险 | 小 / 小 | 中 / 中 | 大 / 大 |

**推荐 B 的理由**：

- 问题出在**结构层**（材质、层级、排版、形状），不在品牌层；A 修不到根上，C 会丢掉「车上车下是同一个小舟」这个产品资产。
- 外部评审（GR:286,407）不建议现在大改整体视觉、重画光球。B 不动光球造型，也不重开交互语义，把改动收在结构与系统层，这条建议照样成立。

**B 的具体主张**：

1. **用色调分层代替假玻璃**。
   - 深色主题直接沿用契约里的深空色阶，作为容器层级（Space-950 → 600 对应底 → 最高容器）。
   - 浅色主题用「晨雾」色阶，对应同样的层级。
   - G1 一律改为**不透明的色调面**，可选一条 1px 顶缘细光，保留玻璃的记忆点。
   - 真模糊只留在语音层，作为增强；不可用时回落到同色实色面。
2. **一屏一颗光球**。光球的身份收回为「麦克风 + 头像 + 品牌标」合一的那一颗。顶栏改为字标加状态点；欢迎态与 Composer 不同时出球。具体方案见 §15-2。
3. **极光更稀缺**。只留给四处：光球、流式光标、语音层顶缘、AI 生成内容的角标。发送键改用交互蓝（§15-4）。
4. **助手回答去掉气泡**（§15-3）。用户消息保留右侧色调气泡；助手回答改成全宽正文，卡片作为一等内容块，不再嵌套在气泡里。确认、错误、打断改用左侧色条加标签表达。
5. **底部只保留一条交互带**。Dock 和胶囊的语义不变，但重新确定它们与 Composer 的视觉层级和间距。停播、打断、结束收音三个动作用不同的形状和标签区分（解决 D3）。
6. **字阶按角色定义，数字用表格数字**（§9.2）。
7. **形状按角色取刻度**：0 / 4 / 8 / 12 / 16 / 20 / 28 / full。刻度以外的值一律消失。
8. **支持页用 Android 原生版式**：设置用列表项 + 开关 / 分段按钮；车况用指标块 + 分组；地图用底部信息条。大屏下设置改为「列表–详情」两栏。
9. **动效**：
   - 光球的节律参数不变；
   - 位移、缩放、展开用短弹簧或减速曲线；
   - 不对高度做弹簧（FF:211-215 已证明慢机上会掉帧）；
   - 点按有 ripple 或按下态。

---

## 5. 设计原则（手机版）

| # | 原则 | 判据 |
|---|---|---|
| M1 | 一屏一颗光球 | 任何帧里只有一个光球实例在跑循环；静止实例不超过一个 |
| M2 | 极光只留给 AI 时刻 | 只能出现在光球、流式光标、语音层顶缘、AI 角标四处；正文、数字、语义色、普通图标、按钮底一律不用 |
| M3 | 色调分层，安全面用实色 | G0（确认 / 错误 / 隐私 / 行车限制 / 地图浮层）不透明度 ≥0.96；其余容器是不透明色调面 |
| M4 | 结论先行 | 每屏最大字号给结论或主数值；元信息不超过 caption 字号 |
| M5 | 一条底部交互带 | Composer、chips、胶囊、Dock 的层级关系固定，同一时刻同一类信息只出现一份 |
| M6 | 深浅同权 | 每个组件、每屏都有深浅两版；对比度判据在两版上都通过 |
| M7 | 动效有预算 | 同屏一个循环；减少动效时有静态版；不动画布局属性 |
| M8 | 每个值都有名字 | Figma 里的颜色、字号、间距、圆角、尺寸都绑定变量或样式，没有裸值 |

---

## 6. 信息架构与页面清单（Figma 需要出的帧）

| 页面 / 浮层 | 必须覆盖的状态 | 布局模式 | 代码 |
|---|---|---|---|
| 对话主屏 · 欢迎 | 有语音 / 无语音；键盘弹起；能力摘要筛过的推荐 | 全部 | `ChatScreen.tsx:106-163` |
| 对话主屏 · 记录 | 思考、流式（含光标）、过程区（进行中 / 完成折叠 / 展开 / 行车单行）、完成、错误、断网未收完、已重发、已打断、拒识提示、主动消息、S2S 角标、看图角标、时间分隔、「↓ 最新」、执行回执（折叠 / 展开）、查看各项结果 | 全部 | `MessageBubble.tsx`、`ExecutionReceipt.tsx`、`ResultDetailsFold.tsx` |
| Focus Dock | 高风险确认、低风险确认、位置授权、策略未取到、补槽（追问中 / 已搁置，有无建议值）、长任务、离线队列、服务端问题行（error / warn + 恢复动作）、降级行（权限 / 服务 / 安全拦截 / 回声 / fatal）、「另有 N 个」+ 列表弹层、200% 字号让位 | single / 横屏 | `FocusDock.tsx` |
| 状态胶囊 | 8 个主态的文案与色调、live 点、建议胶囊「切到行车档？」 | 全部 | `PresenceCapsule.tsx`；`presence.ts:337-358` |
| 语音层 | 三档高度 × {收音无字、识别中、思考、播报、追问窗、已打断、S2S 告知条、行车常驻、settled、停止播报键、跟底 / 上滚、上下渐隐} × {竖屏、横屏 40:60} × {模糊、实色} | single / 横屏 / 双栏左栏 | `VoiceSheet.tsx` |
| 支持页浮动在场 | 静止、有胶囊、停播 / 打断键、采集点、语音层升起时让位、地图信息条之上 | 设置 / 车况 / 地图 | `AssistantSurface.tsx:58-106` |
| 设置 | 五个分区 + 开发者 + 构建行；能力摘要的加载 / 失败；免唤醒引擎缺席；行车退出行；清除记录确认 | single；双栏改列表–详情 | `SettingsScreen.tsx` |
| S2S 同意页 | 首次切换 | 弹层 | `S2sConsentSheet.tsx` |
| 车况 | 三格指标 + 明细 + 其他（改成中文标签）；空态、断线 | single；大屏改网格 | `VehiclePanel.tsx` |
| 地图 | 未选点、已选点、全览 / 回中；无坐标；不可用（改用户语言） | 全屏；双栏下用侧边信息栏 | `app/map.tsx`、`MapLayers.tsx` |
| 引导 | 服务器预设（prod 只有云栈）、FQDN 校验、token、权限说明；测试中 / 成功 / 失败 | single；大屏居中限宽 | `app/onboarding.tsx` |
| 隐私栏 | 采集状态各档；三个关闭按钮按条件出现 | 弹层 | `PrivacyRail.tsx` |
| 主动提醒卡 | 提醒、收起、超长时的滚动提示 | 全部 | `ProactivePresenter.tsx` |
| 舞台（双栏 / 抽屉 / 桌面） | 车况段、提醒段（两种空态）、场景段（地图 / 天气 / 日程 / 焦点）；去掉内部名 | drawer / two-pane / tabletop | `features/stage/*` |
| 系统面 | 启动页、自适应图标三层、App Shortcuts「说话 / 车况」图标 | — | `app.config.ts` |

不在范围内：dev 取证屏（画廊、探针），只需保持可用。

---

## 7. 组件清单（Figma 组件与变体）

| 组件 | 变体轴（≤30 组合，超了就拆子组件） | 备注 / 代码 |
|---|---|---|
| `AuroraOrb` | State（idle / armed / listening / thinking / speaking / attention / looking / muted）× Motion（loop / slow / static） | 尺寸用实例缩放；深浅各一套高光参数（§15-2）；节律以标注加原型表达。`ui/aurora/AuroraOrb.tsx` |
| `Composer` | Layout（text-first / voice-first）× Input（always / folded / hidden）× Holding（是 / 否） | 手势契约不变。`features/chat/Composer.tsx` |
| `PrimaryKey`（发送 / 打断 / 停播合一键） | Mode（send / interrupt / stop-playback）× Density | 泊车态要补到 ≥48 |
| `UserBubble` / `AnswerBlock` | 见 §6 记录区的状态 | 助手去气泡后改为 AnswerBlock（§15-3） |
| `ProcessFold` | active / done-collapsed / expanded / terse | |
| `Pill` | Tone（plain / accent / amber / glass）× Selected × Solid × Density × Disabled | 两档制不变。`ui/Pill.tsx` |
| `PresenceCapsule` | Tone × Live | |
| `DockItem` | Confirm / Slot / Task / Queue / Issue / Degradation，各自带子状态 | G0 实色 |
| `VoiceSheet` | Detent × Split × Material（blur / solid） | 头区和内容区各自组件化 |
| `EdgeGlow` / `StreamCursor` / `ThinkDots` | Animated / static | 极光的四处之一 / 之二 |
| `TopAppBar` | Main（字标 + 健康点 + 采集点 + 两个操作）/ Secondary（返回 + 标题）× Scrolled | 两套顶栏统一 |
| `ListItem` | 开关 / 分段 / 值 / 箭头 / 危险 | 设置页 |
| `SegmentedButton`、`Switch`、`TextField` | 标准态、禁用、错误 | |
| `Button` | Filled / Tonal / Outlined / Text / Destructive × Density | 按钮档 = TARGET |
| `IconButton` | Standard / Tonal × Density | 顶栏、卡内 |
| `BottomSheet` / `Dialog` | 隐私栏、S2S 同意、Dock 列表、清除确认 | G0 |
| 卡片件 | `CardShell`（头：图标 + 标题 + 右槽）、`MetricBlock`、`KVRow`、`ListRow`（可点时 ≥48）、`Timeline`、`ProvBadge`、`FreshChip`、`ConfBadge`（人话标签）、`CardActions`、`MapEntry`、`DrivingSummary` | §8 |
| 地图 | 标注 5 种角色（起 / 经 / 终 / 电 / 序号）+ 信息条 | 高德 logo 必须可见 |
| 车况 | `MetricTile`、分组行 | |
| `ProactiveCard`、`TimeDivider`、`JumpToLatest` | | |
| `Icon` | 共享 60 个 + 本地 8 个；补缺（§13.3） | 24 格，1.8 描边 |

---

## 8. 卡片系统

**现状数字**：

- 35 个卡型 key，由 33 个组件实现。按家族分：信息 12、出行 7、澄清 / 提醒 / 场景 / 手册 7、商户支付 8、组合 1。另有兜底卡。
- 画廊有 42 条样本，其中 13 条是真栈已验证的（`features/cards/fixtures.ts`）。

**统一的卡片结构**：

| 区域 | 内容 | 规则 |
|---|---|---|
| 头 | 图标 + 标题（「类别 · 实体」）+ 右槽（`_prov` / 时效 / 置信） | 每种卡都有图标；不显示 type 名或英文键 |
| 主指标 | 温度、价格、里程、金额、比分这类主数值 | 使用 numeric 字阶和表格数字 |
| 内容行 | 列表行、KV 行、时间线 | 可点的行高 ≥48，有按下态 |
| 尾 | 动作按钮（合成一句话后普通发送）、地图入口 | 危险动作走全局 Dock，卡内不放确认按钮 |

**每种卡都要出的状态**：正常、空、缺字段、低置信、`_prov` 三档（mock / degraded / cached）、行车摘要、浅色主题。

**行车摘要按家族重做模板**：标题 + 主数值 + 最多 2 个字段 + 1 个主按钮。主按钮取渲染器合成的那一个，不只读 `card.buttons`。

**展示成人话**：

- 置信度显示「高 / 中 / 未充分核实」（HMI 的做法）；
- 时间一律相对化，或显示为本地时刻；
- 时长换算成小时；
- type 名不出现在屏上。

**数据色板改成 token**：主客队、行程天、AQI 七档、涨跌都随主题切换。

**v1 先做的 12 张（按高频）**：weather、route_plan、place_list / poi_list、charging_route、trip_itinerary、search_result、research_report、news_brief、reminder_list、stock_quote、payment_qr、merchant_checkout。另加 card_group 和兜底卡。

其余卡型用同一套卡片件拼装，排在 v1.1。

---

## 9. Foundations：Figma Variables 结构与代码映射

### 9.1 集合与模式

Professional 计划每个集合最多 10 个模式。

| 集合 | 模式 | 内容 | 代码对应 |
|---|---|---|---|
| `Primitives` | Value | 深空色阶（Space-950…500）、晨雾色阶（浅色底）、teal、极光四色、amber、red、green、中性灰、数据色 | `theme.ts` 常量 |
| `Color` | Dark / Light | 语义角色：`bg`、`surface/{low,base,high,highest}`、`text/{primary,secondary,tertiary,on-accent}`、`line/{default,strong}`、`accent/{default,soft,on}`、`warning/*`、`danger/*`、`success/*`、`process`、`aurora/*`、`scrim`、`data/1…8` | `Palette`（实现时扩键，旧键保留到迁移完） |
| `Size` | 泊车·标准 / 泊车·大字 / 行车·标准 / 行车·大字 | 触控目标、胶囊高、各字阶的字号与行高 | `TARGET`、`PILL`、`TYPE`、`scale()` |
| `Space` | Value | 4 / 8 / 12 / 16 / 24 / 32 / 48 | `SPACE` |
| `Radius` | Value | 0 / 4 / 8 / 12 / 16 / 20 / 28 / full | `RADIUS` |

- **代码语法**：每个变量的 ANDROID code syntax 填代码表达式，例如 `p.bg`、`RADIUS.lg`、`SPACE[3]`、`TARGET.parked`。这样从 Dev Mode 或 MCP 读出来就是代码名。
- **权威归属**：设计阶段，Figma 是目标态；落地后，代码 token 文件仍是唯一声明源，Figma 变量按代码回写校准，不能两边都宣称自己是权威。

**起点值**（M2 定稿）：

- 深色：
  - 底 `#06080F`；
  - 容器色取契约深空色阶 `#0A0E1A / #0F1525 / #151D30 / #1C2540`；
  - accent `#46D6E0`，amber `#F59E0B`，red `#EF4444`，green `#34D399`。
- 浅色：
  - 底 `#EDF1FA`；
  - accent `#0369A1`，amber `#92400E`，red `#C62828`，green `#1A7F37`。这组是现有过了对比度测试的值，见 `theme.ts:119-123`。

### 9.2 文字样式（按角色；数值是起点，M2 定稿）

| 样式 | 字号 / 行高 | 字重 | 用途 |
|---|---|---|---|
| display | 28 / 36 | 600 | 欢迎问候 |
| headline | 22 / 30 | 600 | 引导页标题 |
| title-l | 18 / 26 | 600 | 弹层标题、Dock 标题 |
| title-m | 16 / 24 | 600 | 顶栏、卡片主标题 |
| body-l | 17 / 26 | 400 | 语音层回答（行车时用 20 / 30） |
| body-m | 15 / 24 | 400 | 对话正文 |
| transcript | 20 / 28 | 400 | 语音层转写 |
| label-l | 15 / 20 | 500 | 按钮 |
| label-m | 13 / 18 | 500 | chips、胶囊 |
| caption | 12 / 16 | 400 | 元信息、角标（**最小字号 12**） |
| numeric-xl / l / m | 34 / 40、24 / 30、15 / 20 | 600 / 600 / 500 | 主数值，表格数字 |

- **Figma 里的字体**：用 Noto Sans SC（中文）+ Roboto（西文、数字）代替系统字体。`use_figma` 不支持自定义字体，这两款都是 Google Fonts，可以直接用。
- **代码里的字体**：系统字体 + `fontVariant: ['tabular-nums']`。M1 之前用最小样例在两台机器上验证 OEM 字体是否支持 tnum。字体决定见 §15-6。

### 9.3 效果样式

- `elevation/1–3`：深色主题用色调差 + 极淡阴影 + 可选 1px 顶缘细光；浅色主题用柔和阴影。
- `orb/glow`：只给光球用。
- `scrim`：遮罩，统一取一个值，修掉 40% / 60% 的文档与代码不一致。

### 9.4 对比度判据（与 `mobile/test/theme.test.ts` 同口径）

- 正文、次要、弱文字压在 bg 和每一级 surface 上，都要 ≥4.5:1。
- 状态色文字压在各自的 soft 底上 ≥4.5:1。
- 图形与边界 ≥3:1。
- **光球与浅色底的亮度差 ≥40/255**，沿用 B4 的判据。

---

## 10. 自适应、折叠与行车

**画板**（按两台设备的实测 dp）：

| 画板 | 尺寸 | 模式 | 对应设备 |
|---|---|---|---|
| Phone | 360×800（基准） | single | MIX Fold 4 外屏 360×840 / OPPO 外屏 359×717 |
| Phone 横屏 | 840×360 | single，或行车时 driving-landscape | 车载支架 |
| Fold 内屏 · 抽屉 | 698×652 | drawer | OPPO 内屏全屏 |
| Fold 内屏 · 双栏 | 741×829 | two-pane（book 时铰链落在 gap 里） | MIX Fold 4 内屏 |
| Tabletop | 内屏横向半开，上半约 698×246 / 847×330 | tabletop | 两台都有 |

- **支持页也要按尺寸类出版式**：设置在双栏下改为列表–详情；车况用网格；地图在双栏下用侧边信息栏。
- **行车档**（规则沿用 P:465-466）：
  - 竖屏分 B / C 两种身份；横屏 40:60 分栏；
  - 一屏一卡，摘要卡按 §8 的模板；
  - 触控目标 56；
  - 光球 120，动效频率 ×0.5、透明度 ×0.6。

---

## 11. 无障碍

- **对比度**：§9.4。
- **触控目标**：泊车 48，行车 56；最小字号 12。
- **大字档**：文字 ×1.15，触控目标 ×1.1。
- **系统 200% 字号**：可以重排；固定高的容器用 `maxFontSizeMultiplier` 1.3 封顶（P:538）。
- **减少动效**：每个动效都要有静帧版本。
- **减少透明度**：真正改成不透明（修掉 D10 的说明与行为不符）。
- **纯图标按钮**：设计稿要标注读屏名称。
- 不以颜色作为唯一的信息载体。
- 所有可点区域都要有按下态。

---

## 12. 技术边界（设计必须能落地）

- **技术栈**：RN 0.86.2、Expo 57、Reanimated 4.5、react-native-svg 15.15、expo-blur（`mobile/package.json`）。
- **模糊**：没有 `backdrop-filter`。真模糊只能用 expo-blur 的 dimezis：被糊的背景要包在 `BlurTargetView` 里，而且 ref 要先挂上，否则会静默回落。一屏只能有一个。
- **渐变**：用 `experimental_backgroundImage`，支持 linear 和 radial。radial 要写 `closest-side`，色标 ≤82%，否则会渲出方块（MI:1007,1022-1024）。**没有 conic 渐变**。
- **阴影与滤镜**：`boxShadow` 字符串可用，含 inset。`filter` 要 API 31 才有；父容器挂了 `filter` 会把子元素裁到盒内。
- **动画**：只动 transform 和 opacity，不动布局属性。屏幕刷新率 OPPO 是 60Hz、小米是 120Hz。
- **字体**：没有打包，只能用系统字体。表格数字可行性待验。
- **图标**：用 SvgXml 渲染，原生模块缺席时回退文字。
- **图片**：远程图用 RN `Image`，没有图片处理管线。
- **地图**：高德原生视图，logo 必须可见，浮层要用实色。
- **Figma 端**：`use_figma` 不能导入图片，不支持自定义字体，单次返回上限 20KB，调用必须串行。

---

## 13. Figma 交付物与组织

### 13.1 文件

新建一个设计文件「小舟随行 · Android Visual v3」，放在现有团队里。HMI 文件 `oGlfQSUhriAEs4uH8sJnVe` 不改。

### 13.2 页面

| 页 | 内容 |
|---|---|
| 00 Cover | 目标、方向、状态、相关链接 |
| 01 Audit | 现状截图与问题标注（D1–D10） |
| 02 Foundations | 变量预览、深浅色板、字阶、间距、形状、材质、动效规格、图标 |
| 03 Components | §7 全部组件 |
| 04 Phone | compact 竖屏全部页面 × 状态 × 深浅 |
| 05 Voice & Presence | 语音层矩阵、在场 8 态矩阵 |
| 06 Cards | 家族模板、v1 的 12 张 × 状态、行车摘要 |
| 07 Adaptive | 抽屉、双栏、book、tabletop、横屏 |
| 08 Driving | 行车档：竖屏 B/C、横屏分栏 |
| 09 Motion | 光球节律、语音层升降、Dock 出入、原型连线 |
| 10 Handoff | token 与代码对照、组件与代码文件对照、Code Connect 计划、实现差异清单 |

**命名**：组件名与代码同名（`AuroraOrb`、`Composer`、`FocusDock/Confirm`……）；变体写成 `Property=Value`；变量用斜杠分组，code syntax 填代码名。

### 13.3 图标

- **以代码数据为准生成**：`icons.gen.ts`、`icons.custom.ts`、`icons.local.ts` 的 SVG 正文，通过 `createNodeFromSvg` 一次性生成 68 个组件。
- **Figma 现状**：HMI 文件的 A-8 页有 39 个母版（`32:198`）和回推的 16 个补充图标；`icons.custom.ts` 最后 5 个（search / newspaper / clock / check-circle / settings）和 mobile 本地的 8 个，Figma 里都没有。
- **要补画的**（同一规格）：星级、展开 / 收起、右箭头、外链、复制、电话、地图、关闭、返回、更多、足球（替掉 emoji）等。补画后再回写到代码数据。

### 13.4 设计阶段完成的判据（DoD）

- Foundations 全部做成变量，并且带模式（深浅 × 尺寸四档）；文字样式、效果样式齐全。
- 组件库覆盖 §7，状态全部用变体表达；每个变体的颜色、间距、圆角都绑定到变量。
- 页面覆盖 §6 × 深浅；在场 8 态矩阵完整；v1 的 12 张卡加行车摘要完整。
- 自适应五种画板、行车档都完整。
- 对比度核算表全部通过，光球在浅色底上达标。
- Handoff 页完成，含实现差异清单和实施计划草案入口。
- 每个检查点都有截图证据，并且经过你确认。

---

## 14. 执行计划（确认之后）

| 阶段 | 内容 | 检查点 |
|---|---|---|
| **M0 准备** | 建文件；一次写入冒烟；状态台账 JSON 放 scratchpad（技能要求落盘，中断后可续跑）。现状截图只在 OPPO（test 角色）上按 `scripts/mobile_device.ps1` 规程取：欢迎、对话、语音层、Dock、设置、车况、地图、卡片画廊、状态画廊。**不碰小米，除非你同意**。截图放进 01 页：先试 `upload_assets`，不行就请你拖进去 | — |
| **M1 方向样张** | 三个关键屏 × 深浅：欢迎、对话（含卡与 Dock）、语音层（听 / 播）。Composer 的 text-first 和 voice-first 两种版式都出。同时解决光球在浅色主题下的可见度 | ① 你确认方向 |
| **M2 Foundations** | 变量（Color 深浅、Size 四档、Space、Radius）、文字样式、效果样式、68 个图标；对比度核算表 | ② |
| **M3 Components** | §7 组件库与变体 | ③ |
| **M4 Screens** | compact 全部页面 × 状态 × 深浅；语音与在场矩阵；v1 的 12 张卡加行车摘要 | ④ |
| **M5 Adaptive & Driving** | 抽屉、双栏、book、tabletop、横屏；行车档 | ⑤ |
| **M6 Handoff** | token 与代码映射、实现差异清单；在 `docs/design/` 起草实施计划 | 结束 |

- 每个检查点出 1–3 张 `get_screenshot`，等你看过回复后再进下一阶段。**M1 方向没确认，不做 M2 及以后**。
- `use_figma` 严格串行执行（技能硬规则）。读调用的预算按每天 200 次控制，实际远用不到。
- 设计过程中如果发现交互语义必须改，先停下来问你，不在设计稿里顺手改。

---

## 15. 需要你拍板的决定

> **2026-10-02 裁决**：泓舟确认方向 B。其余 7 项没有逐条回复，按推荐执行，在 M1 检查点①看样张复核。

1. **方向**：B「Aurora × Material 3」（推荐）/ A 延续 Aurora 只修问题 / C 全新视觉。
2. **光球的角色与浅色版**：
   - 推荐一屏一颗：常驻的是 Composer 里那颗光球（它就是麦克风）；顶栏去掉光球，改成字标 + 状态点；欢迎态的大球本身就可以点，此时 Composer 不出球，第一条消息之后再出现。
   - 造型的十条不变量不动，但**浅色主题需要一套光球高光参数**，否则 D5 修不掉。
   - 9 月那一批写过「不动光球视觉」（GB:68）。浅色参数需要你改判这一条。
3. **助手回答去气泡**：推荐采用。用户消息保留气泡；助手回答全宽，卡片成为一等内容块。
4. **发送键配色**：推荐从极光改成交互蓝实色，极光收回到光球、流式光标、语音层顶缘、AI 角标四处。这一条会改动 UX v2 §10.2 的「三处虹彩纪律」。
5. **Composer 版式**：推荐在 M1 把 text-first（现有结构重排）和 voice-first（居中大光球 + 键盘键）都出样张，由你选。身份 B / C 在行车时默认用 voice-first。
6. **字体**：
   - 系统字体 + 表格数字（推荐，不增加包体）；
   - 或打包品牌字体。这算新的原生依赖，要考虑许可和包体，是新决定。
7. **范围与顺序**：推荐先把手机竖屏（compact）全量做完，再做折叠屏、横屏、行车档；也可以一次全做。
8. **浅色主题地位**：推荐与深色同权，默认仍然「跟随系统」。

---

## 16. 风险与对策

| 风险 | 对策 |
|---|---|
| 视觉改动波及已经验收过的交互 | 不重开交互；每处结构改动标注「语义不变」，实施计划逐条映射到现有测试和 Maestro 流 |
| 浅色主题下的光球问题 | 放在 M1 解决；不解决，G2 不成立 |
| Figma 与 RN 的实现差距 | 每个材质和效果都标注实现方式；不确定的项（表格数字、radial 渐变、模糊回落）在 M1 前用最小样例上真机验证 |
| 组合数爆炸（35 卡 × 状态 × 深浅 × 尺寸） | 用组件和变量模式来覆盖，不逐帧手画；卡片 v1 只做 12 张 |
| MCP 单次返回上限 20KB、长流程中断 | 按页拆分调用；状态台账落盘；按技能的续跑协议恢复 |
| 外部评审建议不要大改整体视觉（GR:286,407） | 以你的新决定为准，但范围收在结构与系统层；不动光球造型，不重开交互 |

---

## 附录 A：文档代号

| 代号 | 文件 |
|---|---|
| P | `docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md` |
| H | `docs/design/2026-08-23-hmi-android-app-plan.md` |
| MI | `docs/design/2026-08-24-mobile-app-implementation-plan.md` |
| B4 | `docs/design/2026-09-02-mobile-ux-v2-b4-implementation-plan.md` |
| UB / UR | `docs/design/2026-09-10-android-ui-polish-batches.md` / `docs/reviews/2026-09-10-android-ui-polish-review.md` |
| FF | `docs/design/2026-09-17-android-voice-sheet-focus-follow.md` |
| RT | `docs/design/2026-09-14-android-remaining-todos.md` |
| PR | `docs/reviews/2026-09-12-android-performance-latency-review.md` |
| FR | `docs/reviews/2026-09-07-android-ux-full-review.md` |
| A3 / A4 | `docs/design/2026-09-08-ar03-stop-playback-landscape-implementation.md` / `2026-09-08-ar04-presentation-ack-implementation.md` |
| A10P | `docs/design/2026-09-10-ar10-acceptance-preparation.md` |
| GR / GB | `docs/reviews/2026-09-19-android-gpt6-pro-review.md` / `docs/design/2026-09-19-android-gpt6-review-remediation-batches.md` |
| CR | `mobile/src/features/cards/CardRenderer.tsx` |

## 附录 B：现状数字（2026-10-02 读数）

| 项 | 数 |
|---|---|
| 卡型 key / 渲染组件 | 35 / 33（另有兜底卡、行车摘要） |
| 字号字面量 / 取值种数 / 其中 11pt | 297 / 12 / 116 |
| `TYPE` token 引用次数 | 56 |
| 颜色字面量（hex / rgba）/ 涉及文件 | 65 / 92 / 20 |
| `borderRadius` 字面量取值种数 | 14 |
| 光球同屏实例（欢迎态） | 3（其中 2 个在跑循环） |
| 共享图标 / 本地图标 / Figma 已有 | 60 / 8 / 55 |
