# Dashboard Visual v2 浏览器验收（2026-10-11）

结论：**3 个问题已完成本地修复与浏览器重证，尚未发布。** 初验事实保留在下文；本地修复、用户批准的两处配置映射与验证边界见 §5。不能据此宣布线上已经修复或项目 QA 全绿。

## 1. 被测版本与执行边界

- 线上 release / running：`32bd007c03a748fa7eddd8a10f409a57f4a8bf0f`。开始、结束与收尾独立 `dev_stack status` 均为同一 SHA、5/5 healthy、零 warning。
- 本地运行器基线：`c705f8c40e76436e06dd7163fa3865451b35ca5d`；本轮核对 `dashboard/`、collector、结局声明与共享图标相对被测 release 无差异。其他工作流的 HMI 改动不在本轮范围内。
- 浏览器：Windows Edge `154.0.4258.62`，独立 headless / guest profile。使用真实鼠标、键盘、滚动及文件下载事件；检查实际加载的线上页面与接口响应，并查看关键截图。
- 实际运维登录、真实轮次/日志/用量/车况与 Agent 读取；没有替换这些业务响应。标记保存、标注保存、确认重放、指令完成等写流程只在应用自己的离线夹具中验证，不算真实写接口通过。
- 两次真实天气指令尝试在 WebSocket 鉴权阶段失败，没有业务出帧。鉴权对照只连接、随后关闭，不发送业务内容；未执行真实车控、商户写或支付。
- 初验只新增验收记录与 ignored 证据，未修产品代码、未改配置或密钥、未提交推送/部署。初验浏览器的运维令牌已清除，浏览器已关闭；后续修复单列于 §5。
- [证据清单](../../.artifacts/dashboard-browser-audit-20261011/audit-manifest.json)；整个目录为本地 ignored 证据，不随 clone 分发。

## 2. 已确认问题

### DV2-B01 · P1：指令台缺少 Edge 鉴权，真实查询无法发出

**复现**：正常运维登录 → 实况 → 输入“深圳今天的天气怎么样？” → 点击“发送”。界面进入“连不上网关”，提示“无法建立 Edge Gateway 连接，请求尚未发送”，本句时间线没有链路片段。

- 两次天气查询均失败。浏览器记录的目标为 `wss://car-agent-dev.tailc936bb.ts.net:8443/ws`，错误为 `HTTP Authentication failed; no valid credentials available`，业务出帧为 0。
- 同一网关使用项目已有合法访问凭据可以握手成功；对照连接未发送业务帧。故障不是网关整体不可达。
- [CommandConsole.tsx:57](../../dashboard/src/components/live/CommandConsole.tsx#L57) 直接 `new WebSocket(EDGE_WS_URL)`，没有接入 Edge 访问身份；运维令牌只用于 collector，不能据此视为已经获得 Edge 身份。
- 重放的 [CommandBar.tsx:37](../../dashboard/src/components/CommandBar.tsx#L37) 同样直连该地址。此项为代码确认的相同接线问题，**未在生产点击确认重放，不另报一次真实重放失败**。
- 修复方向：按已有 Edge 认证契约接线，区分鉴权拒绝与网络不可达；指令台与重放统一处理。修复后用隔离、只读身份验证实际 final、观测 trace 和状态转换。
- 证据：[截图](../../.artifacts/dashboard-browser-audit-20261011/bug-command-auth.png)、[握手错误与鉴权对照](../../.artifacts/dashboard-browser-audit-20261011/bug-command-auth.json)、[首次失败](../../.artifacts/dashboard-browser-audit-20261011/real-command-weather.json)。

### DV2-B02 · P2：Agent 心跳时间显示为约 2983 万分钟前

**复现**：实况 → 查看 Agent 卡片。正常上报的 `info` 等 Agent 显示“29831896 分钟前”。

- 同步取证时，`info.last_seen=1791705489.2798107`，正确时间差约 **2.40 秒**；另一个样本约 **10.96 秒**，界面仍显示数千万分钟前。
- [registry/store.py:55](../../registry/store.py#L55) 使用 `time.time()`，`last_seen` 单位为秒；[AgentList.tsx:21](../../dashboard/src/components/AgentList.tsx#L21) 直接与毫秒 `now` 相减。
- 固定夹具使用毫秒数，原有夹具没有暴露真实接口的单位差异。
- 修复方向：统一 REST、WebSocket 与夹具的时间口径，在接收边界完成适配；同时保留缺失值的“未上报时间”。
- 证据：[截图](../../.artifacts/dashboard-browser-audit-20261011/bug-agent-heartbeat-synchronized.png)、[同次 UI 与接口读数](../../.artifacts/dashboard-browser-audit-20261011/bug-agent-heartbeat-synchronized.json)。

### DV2-B03 · P2：双闪被显示为“故障灯”，布尔值直接显示 false

**复现**：实况 → 车况 → 驾驶，显示“故障灯 false”。

- 真实接口返回 `warning_light: false`。
- [commands.yaml:259](../../orchestrator/edge/knowledge/commands.yaml#L259) 明确定义 `warning_light` 为“双闪（危险报警灯）”，`display_name: 双闪`；[val.py:604](../../orchestrator/edge/val.py#L604) 的值为开/关布尔值。
- [vehicle-config.ts:40](../../dashboard/src/components/vehicle-config.ts#L40) 却映射为 `label: '故障灯'`、`group: 'driving'`、`kind: 'mode'`，从而产生错误名称和原始布尔文本。
- 固定夹具把该字段设成 `null / unavailable`，只验证了“读不到”，没有覆盖真实开/关值。
- 修复方向：按现有双闪契约映射到灯光组的开/关展示；保留缺失或过期状态，不把它解释成车辆故障诊断。
- 证据：[局部截图](../../.artifacts/dashboard-browser-audit-20261011/bug-warning-light-detail.png)、[整页截图](../../.artifacts/dashboard-browser-audit-20261011/bug-warning-light.png)、[接口值与渲染文案](../../.artifacts/dashboard-browser-audit-20261011/bug-warning-light.json)。

以上是现版本缺陷；引入提交尚未逐项追溯。

## 3. 通过的检查与范围

| 检查 | 实际结果 |
|---|---|
| 真实主视图 | 轮次、实况、日志、LLM 用量、收藏 × 深浅 × 1920×1080 / 1440×900 / 1280×800 / 3440×1440，共 **40 组**。页面无横向溢出；另测 1280 演示尺寸五视图。内容正确性仍受 B02/B03 约束。 |
| 真实轮次与检查器 | 时间线、规划、LLM 展开、单轮日志展开、原始 JSON；↑↓ 只改焦点、Enter 打开；Ctrl+K 搜 trace、唯一结果定位、空结果清除。 |
| 分页与分组 | 真实列表由 **200 加载至 240 轮**；按会话分组得到 **112 组**，可切回。计数只属于当轮数据。 |
| 弹层与焦点 | 生产 badcase 草稿取消后恢复原值、标注搜索取消；重放初始焦点在“取消”、Tab 被限制在对话框；1280 抽屉内结局弹层完整，Esc 先关弹层再关抽屉并回焦。没有保存生产标记。 |
| 真实导出 | 下载 `turn-df37c43c6cda1063.json`，浏览器下载完成，**10,246 bytes**；导出响应/原始 JSON 对应所选合成轮。 |
| 真实全局日志 | 1440 展开完整消息，既有右侧空列问题未复现；WARNING 过滤、无结果查询、暂停/恢复正常。重复事件与暂停缓存的新日志测试属于下方夹具。 |
| 真实 LLM 用量 | 1 小时 / 24 小时 / 7 天 / 30 天切换，读取真实数字和分组；网络故障时保留旧数据并提示失败，恢复网络后点击重试成功。 |
| 真实认证边界 | 更换令牌 → 无效令牌得到 403 与明确无效提示 → 使用合法令牌按 Enter 登录。令牌只在 sessionStorage，未进入 localStorage 或页面 URL。 |
| 实况读取 | 车辆与 Agent 真实数据可读；Agent 展开至 17 项、收起至 6 项；模拟环境只展开观察，未更改车辆参数。指令业务链路因 B01 未验通。 |
| 离线视觉矩阵 | **66 个组合**，含组件、演示尺寸、状态/空/失败/缺失/旧接口/访问门，以及 3440×1440；**48 次日志展开布局检查**。减少动效、静态资源、可访问名称、嵌套按钮和页面溢出断言通过。 |
| 离线写交互 | badcase/落域标注的保存反馈、确认后重放对照、指令完成态通过；生产请求被隔离，不宣称写接口或真实重放通过。 |

主要证据：[真实走查](../../.artifacts/dashboard-browser-audit-20261011/live-audit.json)、[深入交互](../../.artifacts/dashboard-browser-audit-20261011/deep-audit.json)、[日志复测](../../.artifacts/dashboard-browser-audit-20261011/final-retakes.json)、[下载事件](../../.artifacts/dashboard-browser-audit-20261011/export-page-result.json)、[错误恢复与离线写交互](../../.artifacts/dashboard-browser-audit-20261011/boundaries.json)、[66 组矩阵](../../.artifacts/dashboard-browser-audit-20261011/fixture-matrix-complete/evidence.json)。

基础真实页面走查未捕获未处理 JS 异常、console error 或非预期 HTTP 失败。主动断网、无效令牌的失败属于预期测试输入；B01 的 WebSocket 鉴权错误属于产品缺陷。几何/控制台断言通过不覆盖 B02/B03 的语义错误。

## 4. 探针校准与未验项

- 初版 Enter 键事件缺少字符输入参数，导致表单未提交；补齐真实键盘事件后，合法/非法令牌和全局搜索的 Enter 路径通过。
- 首轮日志定位把非可见第一行滚到 sticky 表头下，导致命中失败；改为可见区域居中和显式滚到列表起点后复测通过。一次选择器引号错误也已校正。
- 夹具长日志查看/缩放会改变跟随状态；分开的“暂停/恢复”测试需先明确恢复跟随。原始失败日志保留，校准后 66 组完整重跑。
- Edge guest 的 `Browser.setDownloadBehavior` 未正确作用到目标页，使用其 browserContextId 又被拒绝；改为目标页的 `Page.setDownloadBehavior` 后收到实际完成事件与 JSON 文件。下载面板产生的新调试目标还曾被误选，现按 Dashboard origin 选择页面。这些不列为产品缺陷。
- 固定夹具、模拟网络与旧接口状态不算真实后端故障演练；真实重放、生产标记保存、模拟车参数写入、车控、商户与支付未执行。剪贴板跨原生应用读取、屏幕阅读器和真人触控也未验。
- 初验为浏览器功能/视觉验收，没有修改产品代码，因此未重跑前后端全量，也未重新逐节点比对 Figma。后续修复应对每个问题补与真实接口口径一致的回归，再做浏览器重证；实际修复验证见 §5。

## 5. 本地修复与浏览器重证（2026-10-11，待发布）

用户要求修复后完成：

- **B01**：新增 `edgeGateway.ts`，指令台与重放共用 Edge 连接入口，按 HMI 既有契约在升级请求中携带 `VITE_WS_TOKEN`；不复用 collector 运维令牌。缺失凭据时在连接前停止并显示明确原因；浏览器不暴露升级 HTTP 状态时，提示检查网络或访问令牌，不编造单一根因。请求仍保持 `is_confirmation=false`，重放使用独立会话。
- **B02**：`AgentInfo.last_seen` 明确为 Unix 秒；显示时把毫秒 `now` 转成秒后计算，REST / WS 原始值不再错当毫秒。夹具同步使用秒，缺失/非法值继续显示“未上报时间”。
- **B03**：`warning_light` 按 VAL 契约映射为灯光组的“双闪”开关；开、关和读不到分别展示，过期逻辑保留。
- **运行接线**：本地启动器读取根 `.env` 已有 `VITE_WS_TOKEN` 并保持日志脱敏。用户随后明确批准在 `deploy/docker-compose.yaml`、`deploy/cloud/compose.cloud.yaml` 的 Dashboard 环境中各增加一行该变量映射；已应用并核对两份配置除这两行外无其他改动，未更改 `.env` 或任何令牌值。

| 修复验证 | 结果与证据 |
|---|---|
| 前端回归 | **19 文件 / 170 passed**；新增覆盖编码后的 Edge 凭据、运维令牌不可代用、缺凭据零连接、重放会话/确认边界、真实秒级时间与双闪布尔值。`dashboard-tests.log` |
| 启动器回归 | **170 passed / 3 skipped**，含凭据读取、注入与日志脱敏；跳过为当前平台条件用例。`dev-stack-tests.log` |
| TypeScript / 构建 | `npm run build` 通过。配置映射另经 YAML 结构与精确差异检查：`compose-checks.json` |
| 修复前端 + 真实云后端 | 本地 Vite `127.0.0.1:5178`；云后端 `eb098eddc2b1bac00aa0ba152adfdc99013af75c`。签名只读身份，前端自身完成 Edge 鉴权；浏览器适配层仅调整该测试身份要求的会话名并关闭测试记忆，不注入凭据或业务答案。 |
| 真实指令 | “深圳今天的天气怎么样？”得到 final：“深圳当前晴，气温30℃，体感33℃，西南风2级。”；原轮 `16382efebe2cec29`，采集到 14 个 span，LLM 记录为 `minimax / MiniMax-M3.1-Flash-Preview`，actions 为 0。 |
| 真实重放 | 同一句经 UI 确认后重放，轮次 `c350b7a110e62e89`；与原轮会话不同、`is_confirmation=false`、actions 为 0，实际观测到达并显示两条对照时间线。 |
| 数据与尺寸 | 真实 Agent 显示 0–4 秒前；真实双闪显示“灯光 / 双闪 / 关”。1920、1280、3440 宽度核对通过；夹具中双闪缺失仍显示“读不到”，心跳为 2 秒前。 |

证据根目录：`.artifacts/dashboard-v2-bugfix-20261011/`。汇总与源码摘要见 [validation.json](../../.artifacts/dashboard-v2-bugfix-20261011/validation.json)；[真实指令截图](../../.artifacts/dashboard-v2-bugfix-20261011/fixed-real-command-final.png)、[真实重放截图](../../.artifacts/dashboard-v2-bugfix-20261011/fixed-real-replay.png)、[宽屏截图](../../.artifacts/dashboard-v2-bugfix-20261011/fixed-live-3440.png)。

初次新增车辆显示用例把 SVG 附带空白误算进文本，修正断言的空白处理后完整重跑通过。浏览器功能检查全部通过后，第一次收尾 `status` 命令失败；原始尝试保留于 `live-regression.json`，随后独立 `status-after-recheck.json` 确认仍为同一后端 SHA、5/5 healthy、零 warning。汇总同时保留该校准情况，不把失败记录覆盖为成功。

上述真实链路验证属于**本地修复前端连接真实后端**，不是线上 Dashboard 已更新。此批尚未提交、推送或部署；上线后应再核对 Dashboard 的实际环境映射并复验。
