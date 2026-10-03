# Android 视觉 v3 · 实施记录

> 日期：2026-10-03 起。授权：用户「开工」，按[实施计划](2026-10-03-android-visual-v3-implementation-plan.md) P0 → P7 推进，
> 允许提交、推送与 OPPO（test 角色）真机验证。本页只记实现与证据；批次划分、拍板项与红线仍以计划为准。
> 设计真相源：Figma「小舟随行 · Android Visual v3」（`1jdZ6Cwp8pEtQJJUwg6NHS`）。
> 状态：落地中（P0–P3 已提交）。交付对象：Android 实现者（Claude Code 或人类协作者）。
> 关联：[Brief](2026-10-02-android-visual-redesign-brief.md)、`mobile/src/ui/**`、`mobile/src/features/chat/**`、`mobile/src/ui/layout/sheetHeight.ts`。

## 1. 当前进度

| 批 | 提交 | 门禁 | 真机 |
|---|---|---|---|
| P0 token | `fc432388` | typecheck / lint / jest；对比度按表面逐层算，变异 7 红 | 随 P2 包 |
| P1 原语 | `308b5159`，lint 补 `d59889e0` | 同上；Button / Segmented / ListItem / TextField 单测 | 随 P2 包 |
| P2a 主屏视觉 | `9a0699ad` | 113 文件 / 1350 条 | ✅ `86a7c89e` 包，见 §2 |
| P2b 去气泡 + 追问进回答 | `f472b840` | 113 / 1351；两处守卫变异各 1 红 | ✅ 同上 |
| P2c 欢迎态一屏一球 | `86a7c89e` | 113 / 1354；三条宿主规则变异各 1 红 | ⏳ 欢迎态手势见 §5 |
| P2 真机修正 | `6cfb0d22` | 113 / 1354 | ✅ `b6288305` 包复核，见 §3 |
| P3 语音层 + 层开时一屏一球 | `b6288305` | 113 / 1356；锁存与宿主规则变异各 1 红 | ✅ 视觉与层开合（不开麦部分）；⏳ 层内大球手势见 §5 |
| P3 真机修正 | `48188ba8` | 113 / 1356 | ⏳ 随 P4 包复核 |
| P4–P7 | — | — | — |

## 2. P2 真机轮（`86a7c89e`）

**包与装机**

- 构建：`scripts/build_mobile.ps1 -Release -Variant prod -CompileJobs 3`，堆 128m / 2048m（与上一包同参，复用原生缓存）。
  12:34–12:45，`BUILD SUCCESSFUL in 10m 12s`，包内 `variant=prod build=86a7c89e3`。
- APK `xiaozhou-companion-prod-release-86a7c89e3-20261003-1245.apk`，SHA-256 `39d9fe7e…c143`，与设备 `base.apk` 一致；
  `lastUpdateTime` 07:26:35 → 12:46:12，非 DEBUGGABLE。
- 设备：OPPO PEUM00（919fd6f9）合屏，外屏 988×1972（约 360dp），系统深色。证据目录 `%LOCALAPPDATA%\car-agent\artifacts\V3-P2-20261003-123402`。

**Maestro（release 档）**

| 流 | 结果 |
|---|---|
| 04 离线画廊 / 10 中文输入 / 08 键盘不遮发送 | 通过 |
| 01 文字问天气 | 首跑失败：手机 Wi-Fi 丢包，回答停在「发送状态未知」；网络恢复后重跑通过 |
| 06 危险动作进 Dock | 用仓库外的逐条副本跑（取消前加一张截图），通过；只建确认后取消，未执行 |
| 03 断网入队 | 未完成：开飞行模式后 Maestro 卡在 45 s 可选等待、无失败步骤退出；已用 adb 关飞行模式、刷新 Tailscale |

`Failed to record heartbeat` 在通过的流里同样出现（06 里 35 次），是 Maestro 自身噪声。

**画板对照**：对话主屏深 / 浅 / 大字档，状态画廊 Dock 深 / 浅，真栈 Dock（06 截图）。顶栏、时间分隔、用户气泡、
无气泡回答、追问 chip、回执、Composer、Dock 卡与按钮都对上 R-2 / R-4 / D-1。主题与字号改完已恢复为「跟随系统 / 标准」。

**发现与处置**

| 现象 | 处置 |
|---|---|
| 出错状态行在 360dp 上用 flexWrap：图标独占一行、键再掉一行 | `6cfb0d22` 去掉折行，文字在中间一栏收缩换行 |
| App 浅色 + 系统深色时状态栏白字压浅底 | `6cfb0d22` 根布局按 Palette 设 `StatusBar` |
| 「另有 N 个待处理」在 Dock 卡内，D-1 在卡外 | `6cfb0d22` 移到卡下右对齐，testID 不变 |
| 大字档 Composer 占位折两行 | 旧尺寸同样折行，不是本批引入；记入 P6 大字档 |
| 装机后首启列表停在末尾之后的空白（当时「USB 用于」弹窗盖着） | 强停重开复现不了；P3 包装机后首启正常；继续观察 |
| 确认轮服务端 follow_up「说"确认"后我再执行。」显示成追问 chip | 旧版是追问链接，同一文本；服务端文案问题，不在本计划范围 |

## 3. P3 真机轮（`b6288305`）

**包与装机**：同参构建 13:40–13:52，`BUILD SUCCESSFUL in 10m 23s`，包内 `build=b62883052`；
APK `xiaozhou-companion-prod-release-b62883052-20261003-1352.apk`，SHA-256 `01fd54d7…a6b0` 与设备一致；
`lastUpdateTime` → 13:53:38，非 DEBUGGABLE。证据目录 `%LOCALAPPDATA%\car-agent\artifacts\V3-P3-20261003-134046`。

**核对**（主题 / 字号改完已恢复）：

- P2 修正三项都对：出错行图标 + 两行红字 + 重发同一行；浅色状态栏深色图标；「另有 2 个待处理」在卡下右对齐。
- 语音层用 `xiaozhou://voice` 打开（只升层、不开麦；`dumpsys audio` 无录音会话）：层开着时 Composer 无球、层内大球在；
  点把手带收起后 Composer 光球回来。顶角 28、模糊壳、把手、88 球对上 VS 画板。
- 发文字轮时「思考中」用 `ThinkDots` + bodyM 次级字，合一键转「打断」中性底，对上 R-1。

**发现与处置**

| 现象 | 处置 |
|---|---|
| 头区与内容区交界一道硬边：空闲轮状态行不渲染、内容区顶到球底切掉球环与光晕；渐隐用半透明 sheetTint，在模糊壳上叠出色带（深浅两色都有） | `48188ba8` 状态行没字也占 labelL 行高（与 sheetHeight 预留一致）；渐隐改用壳的视觉合成色 |
| 仓库外 Maestro 流 `p3-sheet`：`hideKeyboard` 后立刻 `tapOn composer-send` 没发出去，Maestro 等界面变化 2 分钟后退出 | 同一枚键 adb 直点正常发送；判为 Maestro 用了收键盘前的坐标，不是 App 缺陷。改用深链开层核对 |

## 4. 与画板的有意偏离

- 待确认时回答不描琥珀边：D-1 画板如此，确认只在 Dock。
- 主动播报类别头的「为什么收到」入口未做：代码里没有对应功能，只画了图标与类别文案。
- 行车常驻层的状态行仍是「说「小舟小舟」」：画板写「…，或轻点光球」，属文案改动，按计划先过 §5。

## 5. 待用户

- **欢迎态与光球手势的真机验证需要本人操作**：轻点 / 按住欢迎态大球与层内大球都会开麦并上云识别，
  按安全红线不由脚本代按。另外 OPPO 上有历史对话，看到欢迎态要先在设置里清除对话记录（数据删除，需授权）。
