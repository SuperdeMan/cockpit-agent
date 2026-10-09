# 座舱 Agent 可观测台视觉 v2 · 实施计划

- **状态**：B1–B6 已提交、推送并完成授权部署及独立验证（2026-10-09）。发布记录见 §9.5，当前 release/status/verify 统一看 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。
- **依据**：
  - [设计 Brief](2026-10-09-dashboard-visual-redesign-brief.md)（已批准，§16 九项全部按推荐；检查点① 用户已确认）；
  - Figma「座舱 Agent 可观测台 · Visual v2」`wmGLdb9ZAU5AT1RtSlSDT2`。09 Handoff 页有 token ↔ CSS（H-01）、组件 ↔ 代码（H-02）、差异清单 D（H-03）、接口依赖 C（H-04）。
- **读数基线**：`main` @ `642fb9b0`（2026-10-09）。`dashboard/` 最后一次改动是 `7a3b38f9`（2026-10-03），与 brief 的基线 `a49662fa` 之间没有改动，文中 `文件:行号` 两版通用。
- **范围**：
  - 改 `dashboard/` 的视图层与样式；
  - collector 的只读查询补充（§4 的 C 项）单列一条线，可以并行做；
  - HMI 与 mobile 不动。

---

## 0. 结论

分 6 批落地，顺序固定为 **B1 token 与基础件 → B2 外壳与访问 → B3 轮次与检查器 → B4 时间线 → B5 实况 → B6 日志、用量与无障碍收口**。

- 每批都能单独合入、单独回退。
- 前一批不完成，后一批就没有可用的变量和基础件。
- `types.ts` 只补 C5 列出的、collector 已经返回的字段；collector 的写路径和表结构都不改。
- 不引入组件库、图表库、动效库。时间线用 DOM + CSS 绝对定位来画。

行为变更共 4 条：D11 单轮日志合并、D12 重放前确认、D16 去掉 armed、D21 列表键盘。D21 已在 2026-10-09 实施会话取得用户确认：↑↓ 只移动焦点，Enter 打开；鼠标点击保留。

C10（深链与离线夹具）建议在 B1 就做：没有夹具，就没法把实现和 Figma 截图逐帧对照。

---

## 1. 不变量

| 项 | 规则 |
|---|---|
| 判据单源 | 结局分类只认 `runtime/outcome.py`；色调、文字、图标的显示映射只放在 `outcomeDisplay.ts`（附录 C）；泳道映射只放在 `laneOf.ts`（附录 D）；车况键的 Kind 只放在 `vehicle-config.ts`。不在第二个文件里复制同一份判据 |
| 诚实 | ok 不画成「成功」；空、失败、未授权、断连、未采集、已过保留期六种形态都有画面；「最近 N」不写成总数；未上报写「—」；示例与模拟数据带标签 |
| 安全语义 | 指令台仍走 Edge Gateway，车控仍经 VAL 与确认；不新增确认按钮（`is_confirmation` 恒为 false，保持不变）；模拟环境只改环境量 |
| 令牌 | 运维令牌只保存在本标签页，不进 localStorage、日志或 URL |
| 依赖 | React 18 + 全局 CSS + `--obs-*` 变量；Inter 与 JetBrains Mono 自托管，复制 HMI `public/fonts/` 里的两份 latin 子集和 OFL 许可证；中文用系统字体栈 |
| 品牌原色 | 与 HMI 同名的原色，用测试对账 `hmi/design/visual-v2.tokens.json` |

---

## 2. 分批

| 批 | 内容 | 主要文件 | 完成判据 |
|---|---|---|---|
| **B1 token 与基础件** | 从 Figma 变量生成 `--obs-*`（Color 深浅 × `data-theme`，Size 工作台 / 演示 × `data-size`，Space、Radius、Motion）；字体自托管；`Icon` 读 HMI 共享图标表；`ui/*` 控件（含 FocusRing 的 CSS 写法：`outline: 2px` + `outline-offset: 2px`）；`data/*` 数据件（OutcomeBadge + `outcomeDisplay.ts`、Tag、IdChip、Duration、KVRow、CodeBlock、StatTile、ShareBar、Table、EmptyState、ErrorState、SkeletonRow、Banner）；减少动效的基础规则；C10 的前端部分（`?view=&trace=`、`?fixture=`） | `tokens.css`、`styles.css`、`main.tsx`、`components/ui/*`、`components/data/*` | token 快照与 Figma H-01 逐项一致（导出变量对账）；对比度测试按 F-01 口径，五种底加软底、按 alpha 合成后算；`npm test`、`npm run build` 通过；夹具截图对照 02、03 页 |
| **B2 外壳与访问** | 顶栏：页签改名，加全局跳转、栈标识（取自 collector 地址）、连接三态、主题、演示、菜单；令牌门四态替换 `window.prompt`（`api.ts:52`）；6 处静默 `.catch(() => set([]))` 改成错误卡；WS 重连横幅；trace 不存在 / 已过保留期 | `App.tsx`、`api.ts`、`components/shell/*` | 07 页 S-01～S-08 对照；`api.test.ts` 补三条用例：401、503、网络错误 |
| **B3 轮次与检查器** | 扁平轮次流 + 筛选行 + 按会话分组 + 日期分隔 + 诚实的列表脚注；检查器概要头、事实行、同会话条、动作条；页签（时间线 / 规划 / LLM / 日志合并 / 原始）；badcase 与标注弹层；重放确认对话框；对照（两条时间线上下叠放，共用刻度）；收藏改为预设筛选，拆掉按钮套按钮 | `views/TurnsView.tsx`（替换 `SessionsView.tsx`、`BadcasesView.tsx`）、`components/inspector/*`（替换 `TurnDetailPanel.tsx`） | 04 页 T-01～T-13 对照；D11、D12 各补一条用例 |
| **B4 时间线** | `laneOf.ts`（附录 D，唯一声明，替换 `spanMeta.ts`）；SpanBar、EventMarker、LogTick、LaneHeader、TimelineAxis、ItemDetail；LLM 调用与日志画在同一根轴上；轮次外灰底；列表视图切换；实时形态（新 span 淡入、轴向右延伸）；C6 落地之前按节点名过滤 `llm.call.meta` | `components/timeline/*`（替换 `SpanWaterfall.tsx`） | `SpanWaterfall.test.tsx` 改成 `laneOf` 与渲染用例；T-01、A-04、L-03 对照 |
| **B5 实况** | 指令台六态（done 用中性色，证据取 `val.execute` 的 `changes`）；本句链路只画当前一句，之前的收成摘要；diff 芯片与车况「刚变」联动 2.5 s；VehicleTile 九种 Kind；模拟环境去掉 armed 与 120，加滑块与挡位分段，debug disabled 时置灰；AgentTile 六态；演示档 | `components/live/*`（替换 `CommandBar.tsx`、`TracePanel.tsx`、`Dynamics.tsx`）、`VehicleState.tsx`、`vehicle-config.ts`、`AgentList.tsx` | 05 页 L-01～L-07 对照；`Dynamics.test.tsx` 改断言（不再有 armed）；`VehicleState.test.tsx` 补「未建模」「读不到」 |
| **B6 日志、用量与无障碍收口** | 日志页：跟随、暂停、「N 条新日志」、trace 列，不合并重复；用量页：数字卡、「(未归属)」置顶、未上报写「—」；`:focus-visible` 全量检查；方向键（页签、分段、芯片）；列表 ↑↓ / Enter（先确认）；减少动效全量检查；1440 / 2560 / 1280 抽屉 | `views/LogsView.tsx`、`views/LlmView.tsx`、`App.tsx` | 06 页 G-01～U-04、08 页 R-01～A-05 对照；`LlmView.test.tsx` 改断言（未上报不是 0） |

每批的验证口径：

- `npm test`（Vitest）全绿，`npm run build`（`tsc -b && vite build`）通过；
- 先运行 `python scripts/dev_stack.py target show`。`target=cloud` 时用 `python scripts/dev_stack.py dashboard` 只起 Vite，再用 headless Edge 按夹具截图，与对应 Figma 帧并排检查（方法见 brief 附录 B）；
- 只改样式的批次不新写测试；行为变更 D11、D12、D16、D21 各补一条用例。

---

## 3. 差异清单 D 对账

完整描述见 Figma H-03，这里只列到批次。

| D | 内容 | 批 |
|---|---|---|
| D1 | 字体与字号 → Inter + 系统中文 + JetBrains Mono，11 档 × 两档 | B1 |
| D2 | rgba / hex 字面量、青色一色六用 → 34 个语义色 × 深浅 | B1 |
| D3 | 扫描线、网格、角饰、光晕、常驻循环动画 → 去掉 | B1 |
| D4 | 页签改名，顶栏补全局跳转、栈标识、主题、演示、菜单 | B2 |
| D5 | `window.prompt` → 令牌门四态 | B2 |
| D6 | 静默失败 → 错误卡 + 重试；重连横幅 | B2 |
| D7 | 三级下钻 → 扁平轮次流 + 检查器页签 | B3 |
| D8 | ok 画成「成功」→ 结局徽标；无账本按 status 回退 | B3 |
| D9 | 「最近 N」写成总数 → 如实写 | B3 / B6 |
| D10 | 瀑布图 → 泳道甘特 + 列表视图 | B4 |
| D11 | 检查器日志合并重复（行为变更） | B3 |
| D12 | 重放前确认（行为变更） | B3 |
| D13 | 收藏并入为预设筛选，拆掉按钮套按钮 | B3 |
| D14 | 实况只画本句 + 摘要；diff ↔「刚变」 | B5 |
| D15 | 车况：图标 + 文字值，九种 Kind，「其他」「读不到」 | B5 |
| D16 | 去掉 armed 与 120（行为变更）；挡位分段 | B5 |
| D17 | 指令台「发送」，failed / unreachable 分开，「完成」中性色 + 证据 | B5 |
| D18 | Agent 六态 + 标签，「启动后无调用」 | B5 |
| D19 | 用量：未上报写「—」，「(未归属)」置顶 | B6 |
| D20 | 日志：跟随 / 暂停、trace 列、长消息展开 | B6 |
| D21 | 焦点、方向键、列表 ↑↓ / Enter（行为变更，先确认）、减少动效 | B6 |
| D22 | 深 / 浅 / 跟随系统；演示档 | B1 / B2 |

---

## 4. 接口依赖 C（collector 只读查询）

都不改 SQLite 表结构，也不做数据迁移。建议顺序：C10 → C6 / C8 → C3 → C1 / C2 / C4 → C7 / C9 / C5。哪一项最后不做，对应元素就按「降级形态」实现，不在前端复制服务端判据。

| C | 内容 | 依赖它的元素 | 不做时的降级形态 |
|---|---|---|---|
| C1 | 轮次带 `outcome_category` | 结局筛选按 8 类分组 | 筛选只列种类；徽标色调照附录 C 的种类表，不依赖 C1 |
| C2 | 会话、轮次带 `origin` | 来源芯片与来源字 | 不显示来源，只显示会话 ID |
| C3 | `/api/search` 的筛选、分页与 `total` | 筛选行、「共 N 轮」 | 只写「最近 200 轮」，筛选只作用于已加载的 200 轮 |
| C4 | `/api/sessions` 带首句、`origin`、总数 | 会话分组头 | 组头只写会话 ID 与轮数 |
| C5 | `types.ts` 补 `edge_nlu`、`actionability`、`pinned`、`requested_tier` | 事实行的分歧，LLM 行的档位 / 锁定 | 不显示 |
| C6 | 详情不返回 `llm.call.meta` | 时间线 | 前端按节点名过滤 |
| C7 | `content_capture`、`retention_days` | 内容未采集横幅、「已过保留期」 | 不显示横幅；trace 不存在只写「没找到」 |
| C8 | span 状态统一为 `err` | 状态映射 | 前端把 `error` 当作 `err` |
| C9 | `fallback_calls`、`zero_usage_calls` | 错误与未上报数字卡 | 未上报在前端按 tokens 为 0 的行计数 |
| C10 | `?view=&trace=` 深链、`?fixture=` 离线夹具 | 复制当前链接；视觉 QA | 无，B1 就做 |

---

## 5. 设计阶段的细化（已回写 brief）

M1–M6 期间对 brief 做了以下细化，实施按这一版：

1. **端侧本地轮没有终态账本**：collector 只从 `cloud.outcome` 回填 outcome。本地轮显示「ok · 无账本」；无账本的色调按 status 取（err / timeout / empty 用危险色）。
2. **指令台**：
   - 「完成」只表示请求拿到最终结果，用中性色；
   - 执行证据另给「车身变化 N 项」芯片（取自 `val.execute.changes`；空列表写「无变化」）；
   - error 分成 failed（网关回了 error 消息）和 unreachable（WS 连不上），brief 原写的「网关拒绝」改成「连不上网关」。
3. **实况时间轴按实测画**：
   - 本地轮回复 1.5–4 ms（另有一簇 280–330 ms），`val.execute` 0.1–0.4 ms，`route.local` 零时长，`nlu.shadow` 在轮次结束后才到，落在「轮次外」；
   - 需要确认的车控不走本地，「待确认一轮」是云端轮，经 `edge_call` 拿到 NEED_CONFIRM 后挂起；
   - span 结束后才上报，进行中的调用不画占位条。
4. **「刚变」改为名称后的内联字**，格子高度不变，实况里不跳动。
5. **颜色**：
   - 浅色警告填充改用 `#92400E`（原 `#D97706` 只有 2.7:1）；
   - `accent/line`、`data/axis`、`sim/line` 定为装饰，不单独表达状态；
   - 选中态统一为「交互蓝软底 + 交互蓝字」。
6. **焦点环**用描边组件表达（`outline` + `offset`），不用阴影：阴影扩展会从半透明控件下面透色。
7. **组件轴**：
   - SpanBar 拆成 Status × State；
   - VehicleTile 的 Kind 与 `vehicle-config.ts` 同名；
   - AgentTile 改为六态，状态标签放在指标行。
8. **三级文字**：`text/tertiary` 在浅色下只有 3.3:1，只给占位与禁用，元信息一律用二级文字。
9. **1920 宽的实况页车况放三列**：两列放不下 22 个格子；1440 时把格子收窄到 218，仍是三列。
10. **新增图标 7 枚**：`val-gate`、`eye-off`、`key`、`link`、`cloud-off`、`pause`、`sidebar`。

---

## 6. 风险与待定

| 项 | 说明 | 对策 |
|---|---|---|
| C 项排期 | 没有 C1–C4，轮次页的筛选与计数只能降级 | 按 §4 的降级形态先上；C3 优先 |
| 结局词表演进 | `runtime/outcome.py` 新增种类后，附录 C 的映射会缺项 | 缺项按分类默认色调 + 原始种类名显示；没有 C1 时按中性色 + 原名 |
| 端侧账本 | 端侧本地轮一直显示「无账本」，直到端侧开始写账 | 属于 `runtime/outcome.py` 的后续工作，不在视觉批里做 |
| 数据问题 N1–N5 | 未归属、未上报、车况配置漂移、伪 span、`err` / `error` 混写 | 视觉批只如实显示，不修数据；修复各自立项 |
| Figma 计划限制 | Professional 计划每个集合最多 4 个模式，没有 Code Connect | 用 H-02 对照表；升级计划后再补映射 |
| 字重 | Noto Sans SC 在 Figma 里没有 600，display / headline / title 用的是 Bold | 代码按 brief 用 600 |

---

## 7. Figma 索引

文件：<https://www.figma.com/design/wmGLdb9ZAU5AT1RtSlSDT2>（节点 ID 写成 `55:1376` 时，URL 里用 `node-id=55-1376`）。

| 页 | 内容 | 关键节点（深色） |
|---|---|---|
| 00 Cover | 目标、方向、状态、页索引 | `69:484` |
| 01 Audit | 18 张现状截图 + H 编号标注 | 说明 `2:2` |
| 02 Foundations | F-01 颜色与对比度 … F-06 时间线与数据编码 | `27:2` · `31:70` · `33:616` · `33:711` · `34:616` · `36:645` |
| 03 Components | 42 个组件集 + 26 个单组件 + 78 个图标 | 图标 `5:2` · 基础 `6:2` · 控件 `40:284` · 外壳与数据件 `43:325` · 轮次 / 检查器 / 时间线 / 实况 / 访问 `45:440` |
| 04 Turns | 19 帧 × 深浅（浅色在正下方 6000） | T-01 `55:1376` · T-02 `55:1944` · T-03 `55:2537` · T-04 `55:3202` · T-05 `55:3888` · T-06 `56:2043` · T-07a–e `56:2914`… · T-08 `58:2985` · T-10a `58:3522` · T-10b `58:4285` · T-11 `58:5063` · T-12 `59:5025` · T-13 `59:5975` |
| 05 Live | 7 帧 × 深浅（下方 3000） | L-01 `60:182` · L-02 `62:1154` · L-03 `60:2`（演示档）· L-04 `61:759` · L-05 `62:1506` · L-06 `62:1620` · L-07 `62:1735` |
| 06 Logs · Usage | 8 帧 × 深浅（下方 2600） | G-01 `63:68` · G-02 `63:561` · U-01 `64:836` · U-02 `64:1065` |
| 07 States · Access | 8 帧 × 深浅（下方 2400） | S-01 `65:2` … S-04 `65:181` · S-05 `65:240` · S-06 `65:552` · S-07 `65:1106` · S-08 `65:1630` |
| 08 Responsive · A11y | 宽度档与无障碍 | R-01 `66:2` · R-02 `66:243` · R-03a `66:25026` · R-03b `66:25519` · R-04 `66:25834` · A-01 `67:2153` · A-02 `68:2970` · A-03 `68:3006` · A-04 `67:2893` · A-05 `68:3186` |
| 09 Handoff | token ↔ CSS、组件 ↔ 代码、差异清单、接口依赖、实施入口 | `69:2` · `70:4` · `70:88` · `70:184` · `70:243` |
| M1 样张 | 检查点① 确认时的四张样张，只作历史记录 | — |

---

## 8. 给实施 Agent 的说明

- **读稿**：
  - 结构与绑定的变量用 Figma MCP 的 `get_design_context`（`fileKey=wmGLdb9ZAU5AT1RtSlSDT2`，`nodeId` 见 §7）；
  - token 用 `get_variable_defs`，或直接以 H-01 为准；
  - 截图只在做对照时取。读配额约每天 200 次，每批只读本批涉及的节点，不整页扫。
- **一帧多用**：
  - 深浅是同一组帧切 Color 模式，演示档切 Size 模式，不另画；
  - 实现时分别对应 `[data-theme]` 和 `[data-size]`。
- **命名**：
  - Figma 组件与代码同名，变体写成 `属性=值`；
  - 变量的 WEB code syntax 就是 CSS 变量名。
- **以契约为准**：
  - 设计稿与 `types.ts` 或服务端语义冲突时，以契约为准，并把冲突报回来；
  - 实现里不顺手改交互语义（brief §15）。需要改稿时，先在 brief 记下原因，再改 Figma。
- **数据**：
  - 帧名或格子上标了「示例数据」的内容是编造的，不照抄；
  - 实现以夹具与真栈数据为准。
- **回写 Figma**（例如按代码校准变量）：
  - `use_figma` 必须严格串行，脚本出错会整次回滚；
  - 在组件套件里克隆变体，会丢失文字属性绑定；
  - 套件级文字属性的默认值由所有变体共享；
  - 实例里改不了嵌套层的坐标，也改不了自动布局子层的尺寸；
  - 阴影扩展只在开启裁切的框上渲染。

---

## 9. 实施记录

### 9.1 2026-10-09 本地工作树交付

已逐项读取 09 Handoff 五张表，并按本批节点取得高保真结构与截图。实现保留 React 18、全局 CSS 和既有网络通道，无新增 npm 依赖；HMI/mobile 源文件未改。

| 批 / 差异 | 已实现 |
|---|---|
| B1 / D1–D3、D22 | `tokens.css` 与 Figma 冻结快照；34 个颜色 × 深浅、34 个尺寸 × 工作台/演示、间距/圆角/动效；自托管字体和许可证；ui/data 原语、37 种结局显示映射；深链与离线夹具 |
| B2 / D4–D6 | 五页签、全局 trace 跳转、栈标识、连接状态、主题/演示/菜单；令牌门四态；HTTP 失败与空态分离；新旧令牌及旧 WS 迟到回调隔离 |
| B3 / D7–D9、D11–D13 | 扁平轮次、会话分组、服务端筛选与旧接口降级、真实加载数量；检查器五页签、会话前后导航、badcase/标注弹层、自由 intent、JSON 导出；非连续重复日志合并；重放确认与独立 replay 会话 |
| B4 / D10 | `laneOf.ts` 单源、DOM/CSS 泳道图、零时长标记、LLM/日志共轴、轮次外区域、真实“现在”线、可折叠外部服务、详情与等价列表；原轮/重放轮共用相对刻度 |
| B5 / D14–D18 | 本句与历史分离；指令六态加具体失败原因；完成中性；只从当前 trace 新到 `val.execute.changes` 触发 2.5 秒变化提示；详情防串轮并补拉晚到日志/LLM；九种车况 Kind；模拟环境不自算安全阈值；Agent 六态 |
| B6 / D19–D21 | 日志远端搜索、多服务选择、按出现次数保留事件、暂停与新日志缓存、trace 跳转；用量五卡、占比、未归属置顶、未上报独立表达；页签/芯片/分段方向键、用户确认的列表 ↑↓ / Enter、焦点还原、减少动效及窄屏抽屉 |

旧 `SessionsView` / `BadcasesView` / `TurnDetailPanel` / `SpanWaterfall` / `TracePanel` / `Dynamics` / `CommandBar` 保留兼容入口，运行路径复用新实现，不维护第二套组件行为。

### 9.2 接口与契约边界

- C1–C4、C6、C7、C9 已在 collector **只读查询**实现。原接口默认继续返回数组；`paginated=1` 才返回 `items/total/limit/offset`。`/api/meta` 仍需运维令牌。
- C5 已补当前 collector 真实返回的四个字段；C10 已实现。C8 采用读态 `error → err` 兼容，**SDK 写入方未改**，历史行不重写。
- `zero_usage_calls` 只计 `status=ok` 且输入/输出 tokens 均为 0 的调用；旧接口缺 C9 时显示“未上报分组”，不把分组数冒充调用数。
- AST 对账确认 `_SCHEMA` 及 `insert_turn/insert_span/insert_llm/insert_log/set_badcase/set_gold/cleanup/_ensure_column` 与基线一致；无表结构变更或迁移。
- 2026-10-09 发布前只读核对：`/api/meta` 为 404，搜索仍返回旧数组，详情仍有 `llm.call.meta`；C5 字段已存在。前端对应降级均保留。本地实施阶段未发真实指令、未重放真实轮次、未写模拟环境；发布后检查见 §9.5。
- 未归属调用、模型用量缺报等上游数据问题仍独立，不因界面改造而关闭。

### 9.3 设计落地说明

- 1280 抽屉按实施计划与 Figma R-03；1440 车况三列按本计划 §5 的最新细化。数据量超过可视区域时滚动，未隐藏观测字段。
- Figma 缓存 token 数示例没有对应契约，只显示缓存命中布尔；不存在的 trace 只写“没找到”，当前保留天数不能证明某条记录已清理。
- 专用新增图标直接导出 Figma 原节点，保存在 dashboard 本地图标扩展；共享表只读导入，未为本台重写 HMI/mobile。
- 减少动效关闭淡入与过渡；“刚变”信息仍保持 2.5 秒，期间不循环闪动。原型中的“完成”不作为执行成功证明。
- HTTP/WS 令牌不进 URL/localStorage/日志；旧令牌响应和退订连接不能污染新访问状态；离线夹具在 REST、WS、重放、环境设置入口处截断外呼。

### 9.4 验证与复现

最终本地浏览器验证记录的基线为 `ff9bce828fb58459b8979f2ba8263f746c4bd250` **加本次未提交改动**，不是 release SHA；实施期间 main 从 `2464356a` 前进，按最终 artifact 的 `baseSha` 和输入摘要登记。
浏览器证据另绑定 119 个输入文件的 SHA-256 摘要：`b559c140f0186c3ab90b4d9758e14aad9ffa5a8f13c0e375fe786d102c030680`；运行前后摘要相同。

| 检查 | 结果 / artifact |
|---|---|
| dashboard `npm test` | **17 文件 / 153 passed**；`.artifacts/dashboard-visual-v2/dashboard-tests.log` |
| dashboard `npm run build` | TypeScript + Vite 通过；`dashboard-build.log` |
| collector `python -X utf8 -m pytest -q -rs observability/collector/tests` | **105 passed / 1 skipped / 1 warning**；skip 为未安装可选 OpenTelemetry，warning 为既有 Starlette/httpx 弃用；`collector-tests.log` |
| 浏览器主帧 | **56 个不同组合通过（58 次检查，含 2 次同组合重复）**；深浅、演示、1280/1440/1920/2560、正常/空/失败/未授权/未采集/断连/缺失/旧接口；`browser/evidence.json` |
| 浏览器交互 | span 选中详情、时间线列表、检查器各页签、重放取消初始焦点、确认后两条时间线同刻度、抽屉 Escape 回焦、暂停时缓存两条相同文本事件并逐条恢复、debug disabled 控件不可写 |
| 浏览器断言 | 零外部请求/资源、零运行异常、零页面横向溢出、零嵌套按钮、按钮有名称、减少动效下无运行中动画 |
| 品牌与视觉 | token 全模式逐项对账、HMI 同源角色对账、五种底和软底的 alpha 合成对比度、字体/许可证字节一致；已查看主要页面、详情、对照与抽屉截图 |
| 只读真接口形状 | `live-contract-shapes.json`，仅保存字段与状态，不保存原话或令牌，不作为业务 E2E |

复现入口与夹具参数见 [dashboard/README.md](../../dashboard/README.md)。最终矩阵命令：

```powershell
node dashboard/visual-qa.mjs --interactions components components:1920:presentation turns turns:1440 turns:2560 turns:1280 live live:1440 live-done:1920:presentation live-running live-pending live-disabled:1440 live-failed live-unreachable logs logs:1440 usage usage:1440 token-first token-invalid token-not-configured token-unreachable empty error content-off disconnected missing legacy
```

上述结论限本地前端与 collector 查询，不替代真实 Provider 旅程或整项目 QA。后续授权发布的独立证据见 §9.5。

### 9.5 2026-10-09 授权发布

- 用户已明确授权“提交推送并部署”。本轮只提交 dashboard、collector 查询及配套文档；不包含其他任务的 Android 实施记录改动。
- 发布前发现原 dashboard 镜像把源码平铺在 `/app`，且未包含共享图标，无法解析本批新增的跨目录导入。应用 Dockerfile 改为 `/app/dashboard`，只复制三份受控的 HMI/mobile 图标数据，保持仓库相对布局；不改 Compose 或运行配置。
- `imageLayout.test.ts` 从实际运行入口遍历相对导入，按 Docker COPY/WORKDIR 计算镜像位置。3 条通过，包括去掉共享文件复制、退回旧工作目录的两条反向验证。
- 发布代码提交 `72d30457d9bbb68e6442143b47fc8b923f6397fb` 已推送 `origin/main`；本轮部署前线上为 `ff9bce828fb58459b8979f2ba8263f746c4bd250`。干净隔离工作树只复制 `dev-stack.local`，未复制 `.env`。dry-run 无阻断，沿用已有基础设施批准锚；本轮未申请新的 Compose、CI/CD、schema 或主机配置变更。
- 精确代码提交重新验证：dashboard **18 文件 / 156 passed**、TypeScript/Vite build 通过；collector **105 passed / 1 skipped / 1 warning**，skip/warning 原因同 §9.4。日志在 `.artifacts/dashboard-visual-v2/release-72d30457-{tests,build,collector-tests}.log`，不沿用 §9.4 的未提交树结果。
- 26 个镜像完成构建，独立 status 与 verify 通过；精确运行 SHA、验收 artifact、provider/model 与容量只登记在 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)，不把 `submitted` 当发布验收。
- 线上专项只读检查前后均锁定上述 release：`/api/meta` 无令牌 401、带令牌返回采集/保留/调试和查询功能；分页 envelope 与默认数组两种响应均正确；详情无伪 span、`error` 读态兼容正常；汇总含两项 C9 计数；三份共享图标模块和渲染入口可访问。字体与标识的线上字节和实际发布 `source.tar` 一致；SVG 的 Git blob 与归档只差 CRLF/LF（已额外验证文本一致），两份字体与 Git blob 也逐字节一致。
- 经线上服务跑匿名令牌门和离线轮次的深/浅 **4 个浏览器组合**通过，零运行异常/外部数据请求，确认容器内模块依赖完整。证据：`.artifacts/dashboard-visual-v2/release/online-checks.json`、`online-browser-72d30457/`。不重放真实轮次，不设置模拟环境。
- 部署完成后的状态回写为独立纯文档提交；允许 `origin/main` 领先实际 release，后续不得将文档提交的 SHA 当成运行版本。

### 9.6 2026-10-09 日志展开空列修复

- 用户反馈全局日志点击展开后右侧出现大片空白。已在 1440px 浏览器复现：窄屏通过 CSS 隐藏 trace 列，但详情仍为 `colSpan=5`，浏览器补出第五列，消息列从 1054px 缩为 527px。展开前后的列宽断言在旧实现上失败，截图保留于 `.artifacts/dashboard-log-expand/before/`。
- 修复提交 `b2222c634362beeeb0e962ae41823a906498827e`：同一响应式状态控制 colgroup、表头、数据列和详情跨列数；窄屏 trace 入口移入详情。保持详情展开跨越 1680px 断点时也同步更新，不改变日志查询、逐条保留或跟随语义。
- 该提交的 dashboard **18 文件 / 156 tests** 与 TypeScript/Vite build 通过；浏览器 **18 个组合 / 108 次布局检查**通过，覆盖 1024/1280/1440/1679/1680/1920/2560、深浅主题、工作台/演示尺寸、长文本与 JSON 全文、跨断点缩放，以及暂停/恢复。允许长内容引入竖向滚动条，但不允许多出空列或正文横向溢出。
- 本地证据：`.artifacts/dashboard-log-expand/release-{tests,build,browser}.log` 和 `release-b2222c63/browser/evidence.json`；后者绑定精确代码提交及源码摘要。沿用本轮“提交推送并部署”授权，发布与线上验收结果登记在 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。
- dry-run 无阻断，独立 status / verify 通过；从线上服务加载离线日志夹具，1440/1679/1680/1920 深浅 **8 个组合 / 48 次布局检查**通过，前后运行版本固定为该提交，空列宽度为 0。证据：`.artifacts/dashboard-log-expand/online-checks.json` 与 `online-b2222c63/browser/`；不读取真实日志正文或执行真实业务写入。
