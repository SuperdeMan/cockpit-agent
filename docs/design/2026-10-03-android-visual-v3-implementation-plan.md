# 小舟随行 Android 视觉 v3 · 实施计划（草案）

- **状态**：草案。本文是 [Brief](2026-10-02-android-visual-redesign-brief.md) §14 M6 要求的「实施计划草案」。
  - 设计阶段 M0–M6 已于 2026-10-03 完成。
  - §5 的五项同日按推荐拍板。
  - 同日按泓舟意见，卡片设计从 v1 十二张扩到注册表全部 35 个卡型键，并新增车控结果卡（D17–D20）。
  - 2026-10-03 起实施：P0–P3 已提交，进度与真机证据见[实施记录](2026-10-03-android-visual-v3-execution.md)。
- **设计真相源**：Figma「小舟随行 · Android Visual v3」（`1jdZ6Cwp8pEtQJJUwg6NHS`），按页取用：
  - 10 Handoff：token ↔ 代码、组件 ↔ 代码文件、实现差异清单 D1–D16 与补充差异 D17–D20；
  - 02 Foundations：变量与对比度；03 Components：组件板；
  - 04–08：页面、语音层与在场、卡片、自适应、行车；09 Motion：动效规格。
- **声明源**：落地后代码 token（`mobile/src/ui/theme.ts`、`tokens.ts`）仍是唯一权威。Figma 变量按代码回写校准，两边不能同时宣称权威（Brief §9.1）。
- **交付对象**：按稿落地的实现者（Claude Code 或人类协作者）。
- **红线**：
  - 只改视觉与版式，交互语义不重开（Brief §3.2）；
  - 不改 `hmi/src/types.ts` 字段，不碰 proto 与后端；
  - 车控仍只经 VAL；确认只投影 VAL；
  - 文案改动和标「提议」的项先过 §5，再动手。

---

## 0. 一页速读

**做法**：自底向上分 8 批，顺序是「token → 共享原语 → 对话主屏 → 语音层 → 卡片 → 支持页 → 自适应与行车 → 系统面」。

- 每批单独提交、单独真机验证，可以单独回滚。
- 旧 Palette 键保留到最后一批才删，中途任何一批都不会让别的页面失色。

**规模**：

- 改动集中在 `mobile/src/ui/**`、`features/chat/**`、`features/cards/**`、`features/settings/**` 与少量 `app/**`；
- 新增 4 个共享原语：Button、Segmented、ListItem、Sheet；
- 没有新原生依赖。字体仍用系统字体 + 表格数字，模糊仍用已有的 expo-blur。

**验证**：

- 每批都跑 `npm run typecheck` / `npm run lint` / `npm test`（mobile）；
- 每批都在 OPPO（test 角色）上用 dev 画廊、状态画廊和相关 Maestro 流截图，与 Figma 同名画板并排核对；
- 每批都覆盖深浅两套主题，再加大字档。

---

## 1. 原则

1. **token 先行**：页面里不再出现颜色、圆角、字号字面量。新值先进 `theme.ts` / `tokens.ts` 并有测试，再在组件里使用。
2. **对照画板实现**：每个改动都对得上 Figma 里的一个组件变体或画板。画板里没有的状态，先补画板再写代码。
3. **语义不变要能证明**：每批列出受影响的 testID 与 Maestro 流，实现前后都跑一遍。testID 一律沿用，`target_probe` 读数不降。
4. **一屏一球、极光四处**：光球只在 Composer / 语音层 / 欢迎页 / 支持页浮动组之一出现。极光只给光球、流式光标、语音层顶缘和 AI 角标。
5. **最小字号 12、触控 ≥ target**：这两条在 token 层兜底（`TYPE` 不提供 11；可点件的外框绑 `TARGET`）。

---

## 2. 批次总表

| 批 | 内容 | 主要文件 | 门禁 / 证据 |
|---|---|---|---|
| **P0 Token** | Palette 扩键（D1）：色调层级、on 色、soft / line、`sheetTint`、数据色。字阶（D2）、圆角刻度（D3）、`MOTION` 加提议值（只在 §5 批准后加） | `ui/theme.ts`、`ui/tokens.ts` | `theme.test.ts` 覆盖新增键的对比度（与 02 页「对比度」「数据色对比度」两表同口径）；`tokens.test.ts` |
| **P1 原语** | Button、SegmentedButton、ListItem、BottomSheet / Dialog、TextField、Switch 着色；Pill 改成 Plain / Accent / Amber / Floating，`selected` 不再兼 `disabled`；回写补画的 15 个图标 | `ui/Button.tsx` 等（新）、`ui/Pill.tsx`、`ui/icons.local.ts` | `pill.test.ts` 改断言；新原语单测只测分支（禁用、按下、目标高） |
| **P2 对话主屏** | 主栏去光球（D4）；发送键改交互色实色，尺寸 target（D5）；助手去气泡（D6）；回执和结果折叠热区 48、字号 12；时间分隔、回到最新；胶囊改 Floating；Dock 改 G0 | `ChatScreen.tsx`、`Composer.tsx`、`MessageBubble.tsx`、`ExecutionReceipt.tsx`、`ResultDetailsFold.tsx`、`PresenceCapsule.tsx`、`FocusDock.tsx` | Maestro 01 / 03 / 04 / 06 / 08 / 10；`chatHierarchy`、`composerHint`、`focusDockInteraction` 等单测；对照 04 页 W / R / D 组 |
| **P3 语音层** | 实色改为 surface/high，真模糊配 `sheetTint`（D7）；顶角 28；内容区上下渐隐；头区与内容区按 05 页；S2S 告知条换 token | `VoiceSheet.tsx`、`ui/layout/sheetHeight.ts` | Maestro 05；`sheetHeight` / `voiceSheetFollow` / `sheetGesture` 单测；05 页 12 态逐张核对，再加行车 DR-2–DR-5 |
| **P4 卡片** | 注册表全部 35 个键（D10、D19）：卡头图标、来源中文名、时间相对化与本地时区、时长换算小时、置信人话、AQI 色阶、涨跌色变量化、选中规格不再 50%、原始枚举转人话、兜底卡；按卡型出行车摘要模板（D11）；赛事队伍标识（D18）；车控结果卡与回执中文对象名（D17） | `features/cards/*`、`core/cards/cardFields.ts`、`core/session/receipt.ts`、新增 `ControlResult.tsx` | `cards` / `cardParts` / `cardFields` / `cardGroup` / `merchantCards` / `manualCard` 单测；新增 command → 中文名映射与 `commands.yaml` 的对账测试；卡片画廊样本按 06 页逐张核对；行车摘要逐型核对；Maestro 06（确认后的车控轮） |
| **P5 支持页** | 设置（D8，含文案确认项）、次级顶栏（D9）、车况（D13）、地图（D12）、隐私栏（D14）、引导页 | `SettingsScreen.tsx`、`app/_layout.tsx`、`VehiclePanel.tsx`、`map.tsx`、`MapLayers.tsx`、`PrivacyRail.tsx`、`app/onboarding.tsx` | Maestro 09 / 11；对照 04 页 S / V / M / O / P 组 |
| **P6 自适应与行车** | 舞台去内部名；双栏下设置改列表–详情、车况改网格、地图加侧边信息栏；行车档 Size 一致性 | `features/stage/*`、`ui/layout/sizeClass.ts` 及上述支持页 | Maestro 07；`foldPosture` / `drivingMode` 单测；对照 07 / 08 页；OPPO 内屏抽屉与桌面姿态实拍 |
| **P7 系统面与收尾** | 主题图标单色字形；动效提议项（若批准）；删旧 Palette 键与 glass 系列；Figma 按代码回写 | `assets/images/*`、`ui/theme.ts`、Figma 10 页 | 全量 mobile 门禁 + 01–11 Maestro；release 包真机冒烟 |

---

## 3. 各批要点

### P0 Token

- **新增色键**（深浅两档，取值见 10 页 token 表；「新增键」那一列就是清单）：
  - `surfaceLow`、`surface`、`surfaceHigh`、`surfaceHighest`
  - `lineStrong`、`onAccent`、`onAmber`、`amberLine`、`redSoft`
  - `scrim`：统一取一个值，修掉 0.45 / 0.6 两处不一致
  - `sheetTint`
  - `dataUp`、`dataDown`、`aqi[6]`、`series[5]`
- **改值键**：
  - 浅色的 `accent` / `amber` / `red` / `green` 沿用已过测试的值（Brief §9.1）；
  - 深色 `dataUp` 用 `#F26464`（现状 `#EF4444` 压在 surface/high 上只有 4.46）。
- **字阶**：`TYPE` 按角色给 display … numeric 和 `voice/answer`；`scale()` 规则不变（大字 ×1.15）。数字统一加 `fontVariant: ['tabular-nums']`。
- **圆角**：`RADIUS` 加 `3xl: 28`。现有的 14 并入 `md`（12），24 并入 `3xl`（28）。圆角 14 的位置逐处列出，在 P2–P5 随页面一起改。

### P1 原语

- **Button**：Filled / Tonal / Outlined / Text / Destructive；高 = `TARGET`；按下用 12% 叠层，禁用 38%。用它替换各页手写的按钮。
- **SegmentedButton**：视觉高 `PILL`，热区 `TARGET`。设置页所有单选改用它（语义仍是单选）。
- **ListItem**：Switch / Segmented / Value / Link / Danger；最小高 `TARGET`；分组容器用 surface + 圆角 16。
- **BottomSheet / Dialog**：G0 实色，顶角 28；遮罩用统一的 `scrim`。
- **Pill**：`glass` / `solid` 合并为 `Floating`（surface/high + line + elevation/2）。`selected` 只表达选中，不再禁用（现状的商户规格选中项被画成 50%）。

### P2 对话主屏

- **主栏**：字标 + 健康点 + 采集点 + 车况 / 设置。光球移除，「一屏一球」由 Composer 或欢迎页大球承担。
- **欢迎态**：大球可点（与 Composer 光球同语义）；Composer 用无球布局。
- **发送 / 打断 / 停播合一键**：语义与 testID `composer-send` 不变，只改配色与尺寸。
- **助手回答**：去掉气泡容器、全宽。顺序固定为：过程区 → 正文 → 卡片 → 追问 chips → 回执。

### P3 语音层

- 只改材质、圆角、渐隐和头区排版。
- 档位比例与 `sheetHeight.ts` 的下限公式不动；10 页的 `sheet/*` 变量是这个公式在 360×800 画板上的算例。
- 行车档光球 120 由 `SHEET_ORB` 驱动，不新增常量。

### P4 卡片

- **来源中文名**：新增一张 vendor → 中文名映射表（qweather → 和风、amap → 高德、exa → Exa、luckin → 瑞幸）。放在 `core/cards/`，未知 vendor 原样显示。
- **时间**：搜索来源与数据源时间转本地时区并相对化，复用 `FreshChip` 的规则。
- **行车摘要**：按卡型取「标题 + 主数值 + ≤2 字段 + 1 主按钮」。主按钮取渲染器合成的那一个（路线的「开始导航」、商户的「去支付」）。扫码支付在行车时不出二维码。
- **车控结果卡（D17）**：
  - 数据只读已有字段：`Msg.actions` 与 `resultBundles[].results[].evidence`，不改契约；卡内不放任何按钮，确认仍走全局 Dock。
  - 状态判据：
    - 没有 evidence → 已执行（默认）；
    - `verified` → 已核实；
    - `satisfied` 且 `unchanged` → 本来就是；
    - `acknowledged` 且 `state=unknown` → 未核实（证据行用服务端原话）；
    - `unsatisfied` 或气泡出错 → 没生效；
    - `pending_edge` → 执行中。
  - command → 中文对象名的映射表放 `core/cards/`，数据取 `commands.yaml` 的 `display_name`。客户端不复制判据，用测试与 `commands.yaml` 对账。回执「执行」行也改用这张表。
- **赛事（D18）**：
  - 队伍用缩写圆加主客色环，不再取名字前两个字。
  - 有 `home_logo` / `away_logo` 时画 32px 队徽（RN Image，加载失败回落缩写圆）。
  - 进球用足球图标，不用 emoji。

### P5 支持页

- **设置**：
  - 长选项的缩短（「实时 / 整句」「手持 / 支架 / 车载平板」）和说明行已于 2026-10-03 拍板（§5）；
  - 音色改成图标 + 名字的格子，数据源不变。
- **车况**：未识别键用「未识别字段 · 原键」兜底，并补一张常见键的中文名表。
- **地图**：
  - 标注字改用 on 色；
  - 信息条用 G0；
  - 浮动在场组放在信息条之上；
  - 「不可用」改用户语言，排障细节进开发者页。

### P6 自适应与行车

- 双栏下设置的列表–详情是新版式。交互上只是把分区变成导航列表，没有新功能。
- 舞台去掉「舞台 · 双栏」内部名。

### P7 收尾

- 删旧键之前，全仓 grep 不再引用 `glass*` 与旧 `card` / `panel` / `fill*`。
- 按代码最终值回写 Figma 变量，并更新 10 页 token 表的「状态」列。

---

## 4. 验证与证据

- **每批**：
  - `cd mobile && npm run typecheck && npm run lint && npm test`；
  - 改了 `hmi/src` 共享文件就两端都跑（见[落稿规则](../guides/figma-design-system-rules.md) §9）。
- **真机**：
  - 只用 OPPO（test 角色），按 [Android 构建与设备验证指南](../guides/android-build-and-device-validation.md) 构建与取证。
  - 截图与 Figma 同名画板并排。深浅两套主题都要；大字档抽查 3 屏；行车档用手动行车开关。
  - 证据放 scratchpad，绑定精确 SHA，不进仓库。
- **Maestro**：每批跑表中列出的流。合一键、Dock、语音层的 testID 不变；读数（目标高、行车 56）不降。

---

## 5. 拍板结果

> **2026-10-03 裁决**：泓舟回复「需要我定的几项都按你的建议来」，五项全部按推荐执行，实现时不再逐项确认。

| 项 | 内容 | 裁决 |
|---|---|---|
| 设置长选项缩短 | 「实时（边说边上屏）/ 整句（松手后出字）」改为「实时 / 整句」+ 说明行；设备角色改为「手持 / 支架 / 车载平板」+ 说明行 | 采用。分段按钮放不下长标签，说明行保留了原信息 |
| 地图「不可用」文案 | 不再给用户显示高德 key / 原生模块状态，改为「地图暂时打不开 · 更新 App 后再试」，细节进开发者页 | 采用 |
| 空态改写建议 | 周边无结果时给两条改写 chips；提醒空态给一句「怎么加」 | 采用。点 chip = 普通发送，语义不变 |
| 动效提议 | Dock 进出 180 / 120ms、光标 1Hz、顶缘光随音量呼吸、提醒卡溢出渐隐、股票迷你走势 | 先做前三项；迷你走势要读 `candles`，排到 v1.1 |
| 主题图标字形 | 单色环 + 一笔涡线 | 采用；正式出图再细化 |

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 去气泡后助手与用户的层级变弱 | 用户气泡保留实色。助手正文左对齐全宽，过程区与回执弱色；R 组画板里层级清楚，P2 用真机截图再确认 |
| 真模糊在 OPPO 60Hz 上掉帧 | `sheetTint` 保证无模糊回落时观感一致；保留低电量、减少透明度、行车回落实色的现有判据 |
| 新增 4 个原语与现有页面并存期间风格不一 | 按批次整页切换，不在同一页混用新旧按钮 |
| Figma 与代码数值漂移 | P7 统一回写；中途任何新值都先进代码 token，再改 Figma |
| 卡片人话化掩盖原始数据，排障变难 | 原始值在回执的「数据源」行与开发者卡片画廊里仍可见 |

---

## 7. 不在范围

- 交互语义、在场模型、手势契约、确认与隐私制度（Brief §3.2）。
- `types.ts` 字段、后端与 proto。
- 打包品牌字体（Brief §15-6 选的是系统字体 + 表格数字）。
- 数据侧缺口（D20，另立后端卡）：
  - 俱乐部 / 球员名只有英文；
  - 场景步骤缺模板时回落成「{command}（k=v）」；
  - `news_digest` / `news_list` / `search_answer` / `search_list` 当前没有生产方；
  - 本地直连车控没有执行证据。

  这些按现有数据设计，不靠客户端补。

---

## 附录 A：Figma 页面索引

| 页 | 内容 |
|---|---|
| 00 Cover `0:1` | 目标、方向、状态、页面导航 |
| 01 Audit `1:2` | 现状 9 屏 + D1–D10 标注 |
| 02 Foundations `1:3` | 色板深浅、对比度表、数据色对比度、字阶标准对大字、间距、圆角、尺寸四档、材质、层级 |
| 03 Components `1:4` | 组件板 `7:2`（§7 全部组件与变体；图标 68 + 补画 15） |
| M1 `1:5` | 方向样张（历史） |
| 04 Phone `1:6` | 欢迎 3、记录 6（含车控一轮）、Dock 8、设置 6、车况 + 地图 7、引导 5、浮层 3，各配深浅 |
| 05 Voice & Presence `1:7` | 语音层 12 态 × 深浅、在场 8 态矩阵、原型流「语音一轮」 |
| 06 Cards `1:8` | 注册表全部 35 个卡型键 + 车控结果，每型含状态与行车摘要，深浅两份；赛事 / 下跌股票等示例数据已标注 |
| 07 Adaptive `1:9` | 内屏双栏 / 抽屉 / 桌面、横屏、支持页大屏版式、系统面 |
| 08 Driving `1:10` | 身份 A / B / C、播报 + 摘要卡、答后回落、行车确认、横屏 40:60 |
| 09 Motion `1:11` | 光球节律、过场与手势、减少动效规则 |
| 10 Handoff `1:12` | token ↔ 代码（92 条）、组件 ↔ 代码文件、实现差异 D1–D16、补充差异 D17–D20 |
