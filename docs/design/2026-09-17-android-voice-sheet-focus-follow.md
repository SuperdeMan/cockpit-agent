# Android 语音层三项体验修正：光球固定锚、内容焦点跟随、输入框「按住说话」提示

> 状态：**设计定稿 → 同批实施**（2026-09-17 用户口述三条问题 → 本文定判据与代码位置 → 同批实施；实施记录见 §9）
> 交付对象：mobile（React Native，纯 JS，零原生重建、零后端）
> 关联：[四项体验修正 2026-09-11](2026-09-11-android-sheet-controls-map-tabletop.md)（上一次「上升幅度要按手机尺寸适配」）、
> [打磨批 A](2026-09-10-android-ui-polish-batches.md)（P06 底缘渐隐 / P07 空转写占位）、方案 §5.1.1 / §5.2、
> [剩余待办总表](2026-09-14-android-remaining-todos.md)（N-02 层内 error 文案被裁一行，本批一并处理）

## 0. 三条问题（用户原话摘要）与裁决

| # | 用户看到的 | 根因（代码定位） | 裁决 |
|---|---|---|---|
| 1 | 层升起后光球有时被**上面**或**下面**挡住；识别文字上屏、思考中、内容生成中各种状态下都出现；要按分辨率处理升起高度或展示细节 | `VoiceSheet.tsx` 里大光球**住在 ScrollView 的内容流里**，排在转写之后、回答之前。① 转写多行时把球推到层底之下（0.4 档下限 `parkedSheetMinDp` 只算「把手 + 球列」，**一行转写都没预留**，思考三点更在预留之外）——这是「被下面挡住」；② 用户往下滚去看回答，球随内容滚到把手带之下——这是「被上面挡住」。09-11 补的泊车下限只解决了「空层装不下球」，没解决「有内容时球在流里」 | **球出流**：把手带 + 大球 + 胶囊成为层的**固定头区**，转写 / 思考 / 回答 / chips / 卡片进**可滚内容区**。头区不随滚动动，任何状态、任何分辨率下球都不会被裁；下限按「头区 + 该档该看见的内容」重算（§2） |
| 2 | 内容生成时要自己手动滑才看得到；竞品都是焦点自动跟随最新内容，我们默认停在顶部的光球上 | 层内 ScrollView 没有任何跟底逻辑；球在流顶，回答在流底，流式增长全在视口之外 | 内容区**跟底**：复用记录列表那份判据（`core/session/history.ts::followOnContentChange` / `stickToBottom`，阈值同一个数）——开层、新一轮、回答开始时无条件贴底；之后离底不超过阈值就跟、用户上滚就不拽；滚回底部自动恢复。球在头区不动，所以「跟随内容」和「球不被挡」不再冲突 |
| 3 | 输入框的文字要体现「按住对话框也可以输入（说话）」，给用户好的交互 | 占位符只有「和小舟说点什么…」；空输入框长按 = PTT 这条路（B3-3 的透明触摸层）**没有任何可见提示**；按住期间输入框本身也没变化，只有光球底色变了 | 占位符三态由纯函数 `features/chat/composerHint.ts` 给：闲时「输入文字，或按住说话…」（有语音配置才这么写）；按住中「松开发送 · 上滑取消」（行车档「松开发送」，上滑取消本来就禁用）；轻点 / 免唤醒收音「正在听…」；识别中「识别中…」。按住时输入框描边与底色转 accent（与光球同款），占位符同色 |

一句话：**光球是层的锚，不是内容；内容才滚，锚不滚**。

## 1. 语音层结构

### 1.1 竖屏（single / drawer / two-pane / tabletop）

```
┌─ voice-sheet ──────────────────────────┐
│ ━━━ 把手带（48/56，轻点或下拖收起）  停止播报 │ 固定
│ [端到端语音 · 原始音频将在本轮上传]        │ 固定（S2S 时才有）
│                ◉ 88 / 120                │ 固定头区 voice-sheet-header
│              在听… / 正在思考…            │
├─ voice-sheet-scroll ───────────────────┤
│   「明天深圳天气怎么样」  ← 转写 20pt      │ 可滚，跟底
│   ● ● ●                  ← 思考三点       │
│   明天深圳多云，24–29℃…▍ ← 回答 + 光标     │
│   [换一批] [后天呢]      ← chips           │
│   ┌ 卡片 ┐                                │
│ ░░░░░░░░ 底缘渐隐 24 ░░░░░░░░░░░░░░░░░░░░ │ 固定
└────────────────────────────────────────┘
```

- 头区 = `voice-sheet-header`（新 testID）：大球 + 胶囊，居中；**不进 ScrollView**。
- 内容区 = 既有 `voice-sheet-scroll`：顺序 转写 → 思考三点 → 回答（+ 光标）→「已打断」→ chips → 卡片。转写从球上方移到球下方——读序变成「球 → 你说的 → 回答」，与记录列表同向；转写是本轮最早的内容，回答长了它先滚出视口，符合「焦点跟随最新」。
- 行车档回落（terse：答完 +3s、不忙、不收音）：内容区整个不渲染，层只剩头区（与今天一致，只是原来是「内容清空」）。

### 1.2 横屏车载（driving-landscape，split）

左 40% = 头区（球 + 胶囊，固定），右 60% = 内容区（同一顺序，跟底）。与竖屏**同一份内容顺序**，只是头区挪到左列；原来左列里的转写并入右列内容区，左列高度因此恒 ≤ 球列（球 120/88 + 胶囊），不再有「左列装不下」的情形。

### 1.3 不做的两种形态（记下来免得下次再议）

- **球放层底（小艺形态）**：层底正上方就是 Composer 的 44dp 光球，两颗球叠在 60dp 内；且要接管「轻点始终能说」得把 onOrbTap 路由到层内球（今天只 split 这么做）。改动面大、收益是一个视觉惯例，本批不做。
- **层高随内容长（内容高 + chrome，封顶 0.78）**：流式时每来一段字层顶边就动一次，视觉比 detent 三档更躁；detent 模型保留，只修下限。

## 2. 高度判据（`ui/layout/sheetHeight.ts`）

式子不变：`min(容器, max(比例 × 容器, 该档下限))`。**变的是下限的构成**：

| 项 | 旧 | 新 | 为什么 |
|---|---|---|---|
| chrome | 把手带 + 内容区 padding 32 | 把手带 + padding 32 + **底缘渐隐 24** | 打磨批 A 加渐隐时刻意不改下限（「chrome 读数一个不动」）——结果是下限高度下最后一行永远压在渐隐里。内容区跟底后这一行就是「最新内容」，必须在渐隐之上 |
| 0.4 档主体 | 无（只有球列） | **转写 2 行（20pt / 28）+ gap + 思考三点行 14** | 用户点名的三种状态里两种（识别文字上屏、思考中）都落在 0.4 档；原来一个字都没预留 |
| 0.62 档主体 | 回答 2 行 | **转写 1 行 + gap + 回答 3 行** | 跟底后视口里是回答的末尾，3 行是能读的最小窗；转写留 1 行让「你问的什么」还在 |
| 0.78 档主体 | 泊车：回答 2 行 + 卡头；行车：压缩卡 | 泊车：**0.62 主体 + gap + 卡头**；行车：压缩卡（不变，195 ≥ 0.62 主体） | 下限必须随档位**单调不减**——否则矮容器上「回答到了、层反而缩」 |
| terse（行车回落） | 走 0.4 档下限 | **主体 0**，只剩 chrome + 球列 | 回落时内容区不渲染，再预留转写空间就是一块空白 |
| 球降级阈值 `sheetOrbDp` | 0.4 档下限（split） | 同一条式子，随 chrome 变 | 判据只有一份，阈值跟着下限走 |

主力机（OPPO 外屏竖，记录区实测 578.67dp，normal 字号）三档读数：

| 档 | 比例 | 泊车下限 旧 → 新 | 泊车层高 旧 → 新 | 行车下限 旧 → 新 | 行车层高 旧 → 新 |
|---|---|---|---|---|---|
| 0.4 | 231 | 200 → **318** | 231 → **318** | 240 → **358** | 240 → **358** |
| 0.62 | 359 | 260 → 348 | 359 → 359 | 308 → **400** | 359 → **400** |
| 0.78 | 451 | 314 → 402 | 451 → 451 | 447 → **471** | 451 → **471** |

泊车只有 0.4 档变（+87dp）：这一档正是「识别 / 思考」态，多出来的就是转写两行 + 思考行 + 渐隐；0.62 / 0.78 泊车逐 dp 不变。行车档三档都由下限托住（120 球 + 18pt 回答本来就更高）。矮容器仍 clamp 到容器，不许顶出屏外。

## 3. 焦点跟随（内容区）

判据**不新写**，复用 `core/session/history.ts`：

- `followOnContentChange(offsetFromBottom, viewportH, pendingFollow)`：`pendingFollow` 为真无条件贴底；否则 `stickToBottom`（离底 ≤ 0.2 × 视口）才贴。
- `pendingFollow` 挂旗的三个时刻（都是「用户等着看的东西来了」）：层升起、当前轮的用户气泡 id 变了（新一轮）、当前轮的助手气泡 id 变了（回答开始）。旗在 `onContentSizeChange` 里消费——新内容要等 ScrollView 量完才滚得到（与记录列表 `ownSendRef` 同一个坑）。
- 离底读数在 `onScroll` 上记 ref（增高之前的值），`onContentSizeChange` 用它判——RN 不会因内容长了就发 scroll 事件，所以 ref 里就是「增高前」。
- 用户上滚离底超过阈值 ⇒ 不拽；滚回底部 ⇒ 下一次增高自动跟。不做「↓ 最新」胶囊——层内容只有一轮，滚回去就是。

## 4. 下滑收起与滚动区

09-11 定的「只在滚动区处于顶部时接管下拉」原来靠 `scrollY ≤ 1` 判——跟底之后流式期间滚动区常在**底部**，按旧判据整层下拉会变成滚动区上滚，收起手势失效。修法是把「手指落在哪」也算进去：

`sheetGesture.ts::sheetPanAtTop({ y, scrollTop, scrollOffset })` = 手指落在滚动区上方（把手带 / 头区）**或** 滚动区偏移 ≤ 1dp。头区是固定的、面积大（球 + 胶囊 ≥ 120dp 高），从它上面下拉永远收起；从内容区下拉仍遵守「在顶部才接管」（否则和阅读上滚打架）。把手带轻点、暗区、返回键三条出口不变。

## 5. 层内胶囊去重

识别中 `derivePresence` 给的胶囊文案是 partial 本身（层外胶囊要靠它显示识别到了什么）。层内转写区已经用 20pt 显示同一段 partial，头区胶囊再复读一遍等于两份（且 partial 长了胶囊会换行、把头区撑高）。新增纯函数 `presence.ts::sheetCapsuleText(snapshot)`：`capture === 'recognizing'` ⇒「识别中…」，其余与层外一字不差。层外 `PresenceCapsule` 不动。

## 6. 输入框（`features/chat/composerHint.ts`）

| 状态 | 占位符 | 输入框外观 |
|---|---|---|
| 无语音配置（没有 audioUrl） | 和小舟说点什么… | 不变 |
| 有语音、闲时 | **输入文字，或按住说话…** | 不变 |
| 按住中（`ptt.mode === 'hold'`，光球或空输入框都算） | **松开发送 · 上滑取消**；行车档 **松开发送** | 描边 + 底色 accent（与光球 `accentSoft` 同款），占位符 accent 色 |
| 轻点 / 免唤醒收音中 | 正在听… | 不变 |
| 识别中（finalizing） | 识别中… | 不变（原来这段显示的是闲时占位，像什么都没发生） |

占位符只在输入框为空时可见，与「有字时长按走原生选择」的边界一致。C 身份（输入框隐藏）与 B 身份折叠态无占位符可言，不涉及。

## 7. 验收

- jest：`sheetHeight`（下限构成逐项、三档单调、terse、主力机读数表、球降级阈值随 chrome）/ `sheetGesture`（`sheetPanAtTop` 三分支）/ `voiceSheetFollow`（新：内容增高时在底部 ⇒ `scrollToEnd`；离底 ⇒ 不滚；新一轮 / 回答开始 ⇒ 无条件滚；球与胶囊不在 ScrollView 子树里）/ `presence`（`sheetCapsuleText`）/ `composerHint`（占位符矩阵 + 按住态外观）；既有 `stopPlaybackUi` / `chatHierarchy` / `landscapeDockReach` / `assistantPresence` 不放宽。
- tsc / eslint 0。
- 反向验证（各改一处只红对应用例，按字节恢复）：下限去掉转写两行；terse 仍走 0.4 主体；`sheetPanAtTop` 忽略 y；跟底旗不消费；`sheetCapsuleText` 直出 partial；占位符按住态不区分行车。
- 真机（OPPO test 机，prod 常驻包）：`xiaozhou://voice` 升层 → 长按发一句 → 三张：识别中（转写在球下、球完整、胶囊「识别中…」）/ 思考中（三点可见）/ 流式中（视口停在回答末尾，球在头区不动）；从头区下拉收起；输入框闲时占位符「输入文字，或按住说话…」、按住空输入框时「松开发送 · 上滑取消」。

## 8. 风险与边界

- 泊车 0.4 档层高 231 → 318（+87dp）：识别 / 思考期间层更高，记录露出更少。取舍是用户点名的那两个状态要能看见转写与思考行。
- 跟底与「整层任意位置下滑收起」天然冲突，只在内容区且不在顶部时让给滚动；头区永远能收。真机手感待验。
- S2S 告知条（8 + 约 26dp）不进下限，与今天一致（S2S 挡位少见，且 clamp 保证不出屏）。
- 折叠机与 Xiaomi 对照机本批不取证。

## 9. 实施记录（2026-09-17）

### 9.1 改了什么（基线 `cc0ccef6`）

| 面 | 文件 | 内容 |
|---|---|---|
| 层结构 | `mobile/src/features/chat/VoiceSheet.tsx` | 固定头区 `voice-sheet-header`（球 + 胶囊 `voice-sheet-capsule`）+ 可滚内容区 `voice-sheet-content` / `voice-sheet-scroll`（转写 → 思考 → 回答 → 已打断 → chips → 卡）；跟底（`onScroll` 记离底、`onContentSizeChange` 消费旗）；整层 Pan 的 `atTop` 改读 `sheetPanAtTop`（内容区矩形由 `measureLayout` 量）；split 头区在左、内容在右；terse 内容区不渲染；收音中无字不再画灰字「在听…」 |
| 层高下限 | `mobile/src/ui/layout/sheetHeight.ts` | chrome 含渐隐 24（`SHEET_BOTTOM_FADE_DP` 从 VoiceSheet 搬来）；0.4 / 0.62 / 0.78 主体按 §2；`terse` 入参；`sheetOrbDp` 阈值随之 |
| 下滑接管 | `mobile/src/ui/layout/sheetGesture.ts` | 新 `sheetPanAtTop({ x, y, scrollRect, scrollOffset })` |
| 层内胶囊 | `mobile/src/core/presence/presence.ts` | 新 `sheetCapsuleText(snapshot)`：识别中固定「识别中…」，其余同层外 |
| 输入框 | `mobile/src/features/chat/composerHint.ts`（新）、`Composer.tsx` | 占位符三态 + 按住中描边 / 底色 / 占位符色 accent |
| 测试 | `test/sheetHeight.test.ts`（重写清单，24 条）、`test/sheetGesture.test.ts`（+4）、`test/presence.test.ts`（+4）、`test/composerHint.test.ts`（新，7）、`test/voiceSheetFollow.test.ts`（新，8）、`test/stopPlaybackUi.test.ts`（P07 改断言） | |
| 文档 | 本文、`mobile/README.md` 模块图、总表 §8、`AGENTS.md` Android 入口行 | |

### 9.2 本地验证（工作树 = `cc0ccef6` + 本批；⚠ 同一工作树里另有一条会话未提交的定位 / 导航改动与新原生模块 `mobile/modules/platformlocation/`，全量 jest 是连它们一起跑的）

| 口径 | 结果 |
|---|---|
| mobile `npx jest` 全量 | 第一趟 **1074 / 1076、2 红**（`landscapeDockReach` 等两条在并行负载下超时，单跑三套结构用例 29/29 绿）；**复跑全量 103 suites / 1076 passed / 0 failed**（50s）。基线 1042 → 1076 |
| mobile `tsc --noEmit` / `eslint --max-warnings 0 .` | 0 / 0 |
| 本批相关六套单跑 | `sheetHeight` 24 / `sheetGesture` 10 / `presence` 77 / `composerHint` 7 / `voiceSheetFollow` 8 / `stopPlaybackUi` 15 全绿 |

反向验证（各改一处、只跑对应文件、看红、按字节恢复；六处恢复后与原文逐字节相同）：

| 变异 | 红 |
|---|---|
| A 0.4 档下限去掉转写第二行 | `sheetHeight` 8/24（读数表、构成、主力机三条…） |
| B `drivingSheetMinDp` 忽略 terse | `sheetHeight` 1/24（恰 terse 那条） |
| C `sheetPanAtTop` 忽略落点 | `sheetGesture` 2/10（头区接管、横屏左列） |
| D 跟底旗不消费 | `voiceSheetFollow` 4/8（贴底一次 / 上滚不拽 / 新一轮 / 同轮重渲） |
| E `sheetCapsuleText` 直出 partial | `presence` 1 + `voiceSheetFollow` 1 |
| F 按住态不区分行车 | `composerHint` 2/7 |

### 9.3 真机

**未做。** 原因：同一工作树 / 同一构建镜像此刻被另一条会话占用——它未提交的改动里有新原生模块 `mobile/modules/platformlocation/`，
且 `D:\Android\builds\xiaozhou-mobile` 镜像 10:48 刚被刷新、同一分钟起了一个 Gradle JVM（原生构建必须串行、别人的 Gradle 不停）。
从这棵脏树出包会把对方半成品的原生模块一起打进去，包身份也不是本批的 SHA。

下一步（等对方构建结束、树干净或用本批 SHA 的 worktree 出清洁包）：`scripts\build_mobile.ps1 -Release -Variant prod`（内存紧加 `-CompileJobs 3`）→
`scripts\mobile_device.ps1 -Role test -Install <apk>` → §7 的真机清单：`xiaozhou://voice` 升层后按住说一句，截识别中 / 思考中 / 流式中三态
（球在头区不动、视口停在回答末尾）；从头区下拉收起；输入框闲时与按住时的占位符。真机层高读数对照 §2 表：泊车 0.4 档应为 **318**（09-11 是 231）。
