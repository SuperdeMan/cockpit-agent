# 小舟座舱 HMI 视觉 v2 · 实施计划

- **状态**：实施中（2026-10-09）。I1–I3 已实现并验证；用户已批准 D12 合入，离线文案按用户裁决采用设计值；I4–I6 待实施。未部署生产。
- **依据**：[设计 Brief](2026-10-08-hmi-visual-redesign-brief.md)（已批准，§15 全部按推荐）；Figma「小舟座舱 HMI · Visual v2」`QNXzATLf4WOKLD1rV1dilp`，第 11 页 Handoff 里有 token ↔ CSS、组件 ↔ 代码和差异清单 D。
- **读数基线**：`main` @ `b64fa70a`（2026-10-08）。它与 brief 的基线 `c5171045` 之间 `hmi/` 没有改动，文中 `文件:行号` 两版通用。
- **范围**：只改 `hmi/` 的视图层与样式。Dashboard 不做；1920×720 只写规则，不出稿。

---

## 0. 结论

分 6 批落地，顺序固定为 **token → 外壳 → 对话件 → 卡片 → 舞台 → 设置与无障碍**。每批单独可合入、可回退。

- 前一批不完成，后一批没有可绑定的变量或容器。
- 数据契约 `hmi/src/types.ts` 和 `App.tsx` 的 `handleEvent` 状态机全程不改。
- 不引入 Tailwind、shadcn、lucide、MUI、motion 等新 UI 依赖。

行为变更共 5 条（差异清单 D4、D9、D12、D16、D17），都已在 brief §15 拍板，实施时按原决定执行。其中 D12「读历史时不强制滚到底」brief 写的是「实施时确认」，做到 I3 时先问一句再合入。

---

## 1. 不变量

| 项 | 规则 |
|---|---|
| 契约 | `types.ts` 字段不增不减；卡片动作仍只合成一句话走普通上行；写确认只在全局确认条产生 |
| 状态机 | `App.tsx` `handleEvent` 不改；行车态通过 WebSocket 回调旁的独立只读投影读取既有 process/final.driving（2026-10-08 用户批准，见 §8）；隐私胶囊、连接态从已有状态派生 |
| 安全语义 | 待确认条只由服务端 `need_confirm` 加仍然有效的 `operation_id` 驱动；倒计时只在服务端给期限时显示；付款码行车不显示 |
| 判据单源 | 车控证据状态复用 `resultBundle.mjs` 的投影，与 `mobile/src/core/cards/controlResult.ts` 同口径，不写第二份判据 |
| 真实性 | `_prov` 的缓存、降级、模拟角标任何外壳都不得吞掉；mock 渠道必带「模拟数据」，演示商户必带「演示商户」 |
| 依赖 | 只用 React + 全局 CSS + `--au-*` 变量；字体：中文用系统字体栈，Inter 和 JetBrains Mono 自托管（brief §15-7） |

---

## 2. 分批

| 批 | 内容 | 主要文件 | 完成判据 |
|---|---|---|---|
| **I1 token** | 按 Figma 变量生成 `--au-*`：Color（深 / 浅）、Size（`data-drive` × `data-font` 四档）、Space、Radius、Motion（`--au-dur-*`、`--au-ease-*`）；旧键先映射到新键，迁完再删；全局行车状态写到 `data-drive`（Edge `driving` + 手动切换，退出宽限 30 s）；字体自托管 | `aurora.css`、`styles.css`、`App.tsx`（只加属性，不改状态机）、`settings.tsx` | token 快照测试与 Figma 导出一致；对比度测试：深浅两套，正文 ≥4.5、三级文字与图标 ≥3.0，按「面板背后最亮内容」合成后算 |
| **I2 外壳** | 全幅舞台 + 单一玻璃面板（800）+ scrim；StatusBar（连接三态、隐私胶囊、「行车中」可点退出）；Composer 七态、一屏一颗光球；EdgeGlow；行车布局（答案条，隐藏输入与建议）；无模糊回退与减少动效 | `shell.css`、`components/StatusBar.tsx`、`components/Composer.tsx`、`components/aurora/AuroraOrb.tsx`、`components/ChatView.tsx` | 夹具 `/`、`/?demo`、`/?demo=map` 截图与 P-04 / P-08 / S-02 对照；`prefers-reduced-motion` 与 `@supports not (backdrop-filter)` 两条回退可见 |
| **I3 对话件** | 回答去气泡、用户气泡、思考行、过程区（四态）、错误 / 超时 / 打断、拒识、主动播报（五种）、输入区提示、「有新消息」胶囊；待确认条改为面板底部固定条（一次钉一条 + 另有 N 个）；结果折叠；车控结果卡 | `components/ChatView.tsx`、`pendingOps.mjs`、`merchantUi.mjs`、`components/ResultDetails.tsx`、`resultBundle.mjs` | `confirmationPresentation` 不再返回「已泊车」，同步改 `merchantComponents.test.mjs:142-146`、`merchantUi.test.mjs:255-261`；`/?demo=states` 对照 P-05～P-16 |
| **I4 卡片** | 卡片件进 `cards.css` 并补上 `.au-card`；34 个卡型按 Figma 六块看板的家族顺序逐个换皮；置信度改只读样式；AI 角标标签按内容；等宽数字里的单位用正文字阶 + 次要色；二维码固定黑白 | `components/Cards.tsx`、`cards.css`、`components/aurora/ConfBadge.tsx`、`components/aurora/AQISection.tsx` | `/?demo=cards`、`/?demo=info`、`/?demo=results` 对照 06 页；字段缺失时整行隐藏（补用例） |
| **I5 舞台** | 待机三态（续航读不到就写「读不到」，不再按电量 × 550 估算）；地图（路线、充电规划、地点详情、行程、地图不可用示意）；天气；车况俯视图；日程（当天 / 多天）；阅读；付款码（只在停车时）；媒体极简；删掉舞台边缘极光 bug 和写死的深色信息条 | `components/ContextualStage.tsx`、`vehicleStage.mjs`、`reminderStage.mjs`、`nav.mjs` | `vehicleStage.test.mjs` 改断言（读不到 ⇒ 不估算）；`/?demo=charge`、`/?demo=trip`、`/?demo=route` 对照 05 页 |
| **I6 设置与无障碍** | 设置重排为 12 节（含开发者区，收纳模型 id、延迟、原始错误、README 指引）；补 Select / ListItem / VoiceTile；内容限宽 1120、行高 ≥80；大字档由 Size token 驱动（文字 ×1.15、触控 ×1.1） | `components/SettingsPanel.tsx`、`components/controls.tsx`、`settings.tsx` | `/?settings` 对照 08 页；大字档、无模糊回退对照 09 页 |

每批的验证口径：

- `npm test`（`node --test src/*.test.mjs`）全绿；
- `npx vite build` 通过；
- 本机 Vite + headless Edge 取夹具截图，与对应 Figma 帧并排检查（方法见 brief 附录 B）。`target=cloud` 时只起 Vite，不起本地 Compose。
- 只改样式的批次不新写测试；行为变更（D4、D9、D12、D16、D17）各补一条用例。

---

## 3. 差异清单 D 对账

| D | 内容 | 批 |
|---|---|---|
| D1 | 两栏网格 → 全幅舞台 + 单一玻璃面板 | I2 |
| D2 | 每条消息一颗光球 → 一屏一颗 | I2 |
| D3 | 发送键极光 → 交互蓝（同步改设计契约 §5） | I2 |
| D4 | 写死「泊车模式 · 已停车」「已泊车」→ 读真实行车状态 | I1 / I3 |
| D5 | 384 处字号字面量 → 12 档字阶 × 4 档 | I1 起逐批 |
| D6 | hex / rgba 字面量；浅色语义色未覆盖 → 语义 token | I1 起逐批 |
| D7 | 舞台边缘极光 bug、写死深色信息条 | I5 |
| D8 | `.au-card` 未定义 | I4 |
| D9 | 续航按电量 × 550 估算 → 读不到就说读不到 | I5 |
| D10 | 车控结果显示 action 原文 → ControlResult 证据态 | I3 |
| D11 | trace 角标、字数常显 → 开发者模式 / 删除 | I3 |
| D12 | 读历史时强制滚到底 → 「有新消息」胶囊（合入前确认） | I3 |
| D13 | 置信度胶囊 → 只读元信息 | I4 |
| D14 | emoji → 注册表图标（新画 10 枚回写 `icons.custom.ts`） | I2 / I4 |
| D15 | 设置 8 节 + 开发者语言外露 → 12 节 + 开发者区 | I6 |
| D16 | `data-drive` 从未设置 → 全局行车状态驱动 | I1 |
| D17 | 「大字」「大触控」开关无效 → Size token 驱动 | I1 / I6 |
| D18 | 付款码随主题 → 固定黑白；行车只给金额与期限 | I4 / I5 |
| D19 | 消息区溢出硬切 → 顶部 56px 渐隐 | I2 |

---

## 4. 设计阶段的细化（已回写 brief）

M2–M7 期间对 brief 做了以下细化，实施按这一版：

1. **置信度**：去掉胶囊底，只给圆点上色，文字用中性色，和卡头右槽的来源、时效同级。原因：交互蓝与「置信度 高」同为 `#46D6E0`，胶囊形状会被当成可点的 chip。
2. **AI 角标**：标签按内容写，分别是「AI · 深度调研」「AI 摘要」「AI 回答」「AI 建议」；检索原文不加角标。
3. **等宽数字里的单位**：「2 档」「24 分钟」「约 150 km」这类，单位一段用正文字阶 + 次要色，避免中文退回系统字体、空格变宽。
4. **消息区顶部渐隐**：贴底滚动时顶部 56px 渐隐，读历史时上下都渐隐。
5. **行车摘要**：一个主数值 + 最多 2 个字段。放不下时先删字段，不缩字号。付款与行情只给单值。
6. **行车里的选项**：一律用绑定 target 的按钮（≥76），不用 chip（行车档 chip 只有 56）。
7. **样例数据一致性**：行程样例改成「杭州 2 日」，和西湖底图一致。凡是编造的内容，都在格子标签上标「示例数据」。
8. **Motion 变量集合**：新增，时长用 FLOAT、缓动用 STRING，code syntax 是 `--au-dur-*` / `--au-ease-*`。
9. **新增图标 10 枚**：天气雪 / 雾 / 霾 / 沙尘、隐私三枚、行车中、开发者、行情。另外 Android 本地的 18 枚通用图标提升到共享表。

---

## 5. 风险与待定

| 项 | 说明 | 对策 |
|---|---|---|
| 车况俯视图 | Figma 里是矩形拼的占位示意 | 量产换车型线稿资产；实施时先用占位，资产到位再替换 |
| 大字档力度 | ×1.15 与 Android 一致，但只是把泊车档放到行车档的字号 | 先按批准值做；要 ×1.3 需把 Size 改成「基准 × 倍率」（Professional 计划每个集合最多 4 个模式，已用满） |
| Code Connect | Professional 计划用不了 | 用 Handoff 页的组件 ↔ 代码表对照；升级计划后再补映射 |
| 地图相机动画 | 舞台切换只做淡化与位移 | 取景动画交给地图 SDK，不在 CSS 里模拟 |
| 测试锁定的旧文案 | 「已泊车」被两条测试锁住 | I3 与实现同一提交修改，不先删测试 |

---

## 6. Figma 索引

文件：<https://www.figma.com/design/QNXzATLf4WOKLD1rV1dilp>（节点 ID 写成 `34:924` 时，URL 里用 `node-id=34-924`）。

| 页 | 内容 | 关键节点（深色） |
|---|---|---|
| 02 Foundations | 色板与对比度、字阶四档、尺寸、材质、间距圆角阴影 | Color 深 `20:9` · 浅 `20:311` · 字阶 `21:9` |
| 03 Components | 156 个组件 / 套件 | Composer `30:522` · PendingBar `27:566` · ControlResult `27:419` · 卡片件看板 `28:329` · 设置控件 `31:495` · 行车答案条 `52:70` |
| 04 Parked | 13 屏 × 深浅 | P-04 `34:9` … P-08 `34:924` … P-16 `35:1907`；浅色在下一行 |
| 05 Stage | 15 场景 × 深浅 | S-01a `37:9` · S-02 `37:154` · S-07 `39:897` · S-09 `40:862` · S-10 `40:1229` |
| 06 Cards | 六块看板 × 深浅 | 信息与搜索 `42:15` · 体育与出行 `45:409` · 状态与行车摘要 `48:1066` |
| 07 Driving | 7 屏 × 深浅 | D-02 `52:71` … D-08 `52:834` |
| 08 Settings | 5 屏 × 深浅 | SET-01 `54:9` … SET-05 `54:1111` |
| 09 Accessibility | 大字（泊车 / 行车）、减少动效、无模糊回退 | A-01 `55:902` … A-04 `55:1040` |
| 10 Motion | 光球节奏、过渡规格；泊车与行车两条可点原型 | — |
| 11 Handoff | token ↔ CSS、组件 ↔ 代码、差异清单 D | `59:9` · `59:609` · `59:673` |

---

## 7. 给实施 Agent 的说明

- **读稿**：
  - 结构与绑定的变量用 Figma MCP 的 `get_design_context`（`fileKey=QNXzATLf4WOKLD1rV1dilp`，`nodeId` 见 §6）；
  - token 用 `get_variable_defs`；
  - 截图只在做对照时取。
  - 账号是 Professional 计划，读配额约每天 200 次、每分钟 15 次。每批只读这一批涉及的节点，不整页扫。
- **一帧多用**：深浅、行车、大字是同一组帧切变量模式，不另画。
  - 实现时对应 `[data-theme]` / `[data-drive]` / `[data-font]`。
  - 04 / 05 / 07 / 08 页的浅色帧在深色帧正下方，06 页的浅色看板在右侧。
- **命名**：
  - Figma 组件与代码同名，变体写成 `属性=值`。
  - 变量的 WEB code syntax 就是 CSS 变量名，完整对照在 11 Handoff。
- **以契约为准**：
  - 设计稿与 `types.ts` 或服务端语义冲突时，以契约为准，并把冲突报回来；实现里不顺手改交互语义（brief §14）。
  - 需要改稿时，先在 brief 记下原因，再改 Figma。
- **数据**：
  - 标「示例数据」的内容是编造的，实现以 `demo.ts` 夹具和真实卡为准。
  - 车况俯视图是占位，不照着矩形去实现插画。
- **回写 Figma**（例如按代码校准变量）：
  - `use_figma` 必须严格串行，脚本出错会整体回滚。
  - 在组件套件里克隆变体会丢失文字属性绑定，要重新绑定。
  - 设完实例属性后，自动布局可能不重排，重新挂一次子节点即可。

---

## 8. 实施记录

### I1 · token 与只读行车投影（2026-10-09）

- 提交/推送：`2f8258bc5c4282adeaf31acb8709d768ef5ca1c9`。只推该精确 SHA；不代表生产发布。
- Figma 只读 `20:9`、`20:311`、`21:9`、`59:9`，变量快照留在 `hmi/design/visual-v2.tokens.json`。`hmi/scripts/generate-visual-tokens.mjs --check` 对账五个集合和四档 Size；运行时声明仍在 `aurora.css`，旧键暂作别名。
- 深浅主题的正文、次要文字、弱文字、图标按「舞台 → scrim → 面板 → surface 1/2/3」逐层合成后检查，包含面板背后的纯白和路线色；确认/错误语义色在 M0 实色面上的软底检查。
- Inter / JetBrains Mono 的 Latin 可变字体自托管，保留 OFL 与来源、SHA256；中文沿用系统字体栈。没有新增 npm 依赖。
- **接线裁决**：原 HMI 只保存 process.driving，简单 final 的字段被丢弃，仅从消息状态派生会漏报进入/退出。用户批准在 WebSocket 回调旁增加独立只读投影，`types.ts` 和 `handleEvent` 原文保持不变。行车段、退出本段和 30 秒宽限从 Android 提取到共享 `drivingMode.mjs`；Android 仅改为重导出和登记共享模块，显示与业务语义不变。
- 本地验证树基于 `92e1332b1073b8b8deb7df18371092181e9079d5`：HMI `npm test` **375 passed**；Vite build 通过（保留 >500 kB bundle 提示）；Android `drivingMode` / `sharedAllowlist` **27 passed**。完整 TypeScript 检查前后均为相同的 **25 个既有错误**，不宣称类型检查全绿；AST 核对 `handleEvent`、逐字核对 `types.ts` 未变。
- Headless Edge 读实际 CSS：深浅 × 行车/泊车 × 标准/大字，共 **8 组**，每组 **27 个 Size token** 与快照一致，两份字体均从本地加载。`?tokens` 字阶夹具对照 Figma `21:9`；中文字体按已批准的系统字体方案有字形差异。
- 真实 App 配合浏览器内隔离 WebSocket 验证：只有 final 的行车进入 → 手动退出本段 → 同段 true 不重入 → 下一段重入 → false 连续 30 秒退出。没有连接服务端、执行车控或产生业务写入。
- 证据目录：`.artifacts/hmi-visual-v2/i1/`（截图、浏览器读数、TypeScript 前后对账）；复现：`node test/hmi_cdp/visual_tokens.mjs`，先在 HMI 启动 `Vite --port 5188`。旧外壳/卡片的字号仍在 I2–I6 逐批迁移，不把 token 落地算作整屏完成。

### I2 · 外壳与输入区（2026-10-09）

- 提交/推送：`d1f5078fb3c0e16dada9f302f5b78419c8266d9f`。只推该精确 SHA。
- 本批只读 Figma `34:9`、`34:924`、`30:522`、`37:154`、`52:71`。已接全幅舞台、800px 单层面板、顶部渐隐、单一光球、连接三态、隐私胶囊、行车回答条与输入/建议隐藏。卡片/回答字号与舞台内容仍由 I3–I5 迁移，不宣称整帧已一致。
- Composer 实时转写放入原 PartialUserBubble；发送/停止走已有 App 路径，输入新文字时发送优先于停止。按住/点按模式保留；`types.ts` 和 `handleEvent` 未变。隐私相机提示只在成功取得帧 id 后显示。
- 三枚隐私图标从 Figma SVG 回写共享注册表，保留原 SVG；arrowUp / stop 从 mobile 本地图标提升到共享表，删除重复声明。没有增加 UI 依赖。
- **批次调整**：D7 中两处铺满舞台的错误极光遮罩提前移除。原因是在 I2 减少动效降级时，动画被停掉后遮罩变为完全不透明、覆盖天气/日程内容；其余舞台修改仍在 I5。
- 验证：HMI `npm test` **377 passed**，Vite build 通过；mobile 图标/共享准入 **11 passed**；TypeScript 与 I1 前基线仍是相同 **25 个错误**。`check_visual_boundaries.mjs i2` 核对受保护源码并带反向校验。
- 浏览器：八组深浅夹具的 800px 面板、全幅舞台、一颗光球、最多四条建议与减少动效；另验行车无输入/建议、真实 App 的发送/取消路径。无模糊回退通过显式激活原 `@supports not` 规则验证，非旧 WebView 实机证明。证据在 `.artifacts/hmi-visual-v2/i2/`，复现 `node test/hmi_cdp/visual_shell.mjs`。
- **文本来源裁决**：Figma DrivingAnswerBar 要显示「朗读的那句话」，但 `Msg.text` 经 `projectResultFinal` 优先取 ResultBundle 完整答案。2026-10-09 用户批准只读 `speech` 视图：`DrivingSpeechView` 按已有 RequestRegistry 归属缓存最多 64 条服务端短句，不结算请求，不改消息、类型/handleEvent/播报链路；缺少对应短句时明确回退为答案文本。旧帧/跨轮/过期与错误、流式回退均有用例；浏览器核对「短 speech + 长 ResultBundle」实际显示短句。D12 尚未实施或取得合入批准。

### I3 读稿时发现的契约冲突与裁决（2026-10-09）

- 在读取 `34:114` / `35:1286` 的组件描述时，Figma `StatusBar`（`7:67`）要求离线显示「云端未连接 · 车控仍可用」，并将其解释为端侧快系统仍可车控。
- 现有 HMI 的 `ResilientWebSocket.onStatus` 只表示浏览器与 Edge Gateway 的连接状态；它既不区分云端链路故障，也不证明失联后仍有可用的车控通道。客户端不能据此作出「车控仍可用」的承诺。
- I2 已采用保守且与观测一致的「连接已断开」，没有新增能力承诺。建议保持该文案并将 Figma 说明校准；若要分别显示云端/端侧能力，须另立能力状态契约工作，不混进视觉批。
- 首次按任务第 5 条停下报告；用户随后明确要求「按设计方案的来，然后继续」。据此采用设计的离线文案与红点，Figma 不变。此呈现裁决不代表新增了链路观测或证明断连后的车控能力。

### I3 · 对话与固定确认条（2026-10-09，D12 已批准合入）

- 读取范围：I3 的 `27:566`、`27:419`、`34:114`、`35:1286`、`35:1907`，其余已读节点复用上下文。回答去气泡/头像，保留完整 ResultBundle 正文；用户气泡、过程四态、错误/超时/打断、拒识、五类主动播报、结果折叠统一字阶与色调面。trace 只在 `?dev` 或本地开发者开关打开时显示。
- PendingBar 固定在输入区上方，一次显示一条，按有效 `operation_id` 切换；车控确认文案改为「车辆操作」，不再宣称「已泊车」。位置授权绑定客户端自己创建的消息 ID，后到的无 ID 服务端消息不能冒充该授权。示例确认没有服务端权威，按钮禁用并标明示例。
- 期限/摘要通过独立只读 `PendingPolicyView` 读取既有 `confirm_policy`，复用 `contracts.mjs` 的解析、钟差、过期与渠道判据；无服务端期限不造倒计时，未知/不匹配策略不开放确认。既有业务台账、`types.ts` 和 `handleEvent` 不变。
- 车控结果从 Android 提取为共享 `controlNames.mjs` / `controlResult.mjs`，继续经 `readResultBundles` 判证；模拟来源保留，挂确认的动作不显示「已执行」。手机仅转为共享导出；词表仍由现有测试对账 commands.yaml。
- 验证树基于 I2：HMI **381 passed**、Vite build 通过；Android 受影响的控制结果/命名/图标/共享准入 **38 passed**；完整 TypeScript 与基线仍为相同 **25 个错误**。扫描的反向校验、受保护源码比对通过。
- 浏览器证据 `.artifacts/hmi-visual-v2/i3/`：深浅对话态、历史位置不变与跳到最新、两条确认切换后回传正确 ID、服务端期限到期隐藏、未改变状态不升级为已核实、位置授权不被无 ID 服务端消息顶替、设计离线文案。浏览器内隔离 WS，未调用真实业务能力。
- **D12 已批准**：草稿已经验证「在底部继续跟随；读历史不跳走，出现有新消息；点击才回底部」。2026-10-09 用户回复「合入」，批准随 I3 提交和推送。
