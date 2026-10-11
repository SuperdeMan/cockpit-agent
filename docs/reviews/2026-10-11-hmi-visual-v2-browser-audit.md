# HMI Visual v2 浏览器验收（2026-10-11）

当前状态：**3 个问题已按授权部署并通过线上浏览器回归。** 当前 release/status/verify 统一见 [QA 交接 §2](2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。
下文保留 `0c07bc33` 的初验事实，发布前验证与发布后复验分别列出。首屏加载慢 B04 已另批授权发布并通过线上复验，证据见文末与 [B04 修复记录](../design/2026-10-11-hmi-startup-performance.md)。

## 初验身份与边界

- 线上 release：`0c07bc3341ef74d96467dbbb4e4e8163b29fca7d`。开始、结束独立 `dev_stack status` 均为同一 SHA，5/5 healthy、无告警。
- 浏览器：Windows Edge `154.0.4258.62`，独立 guest/headless profile；1920×1080 为主，另测 1920×720、1920×1200、深浅主题、标准/大字与行车布局。通过真实浏览器输入、点击、滚动、键盘事件取证，并逐图复核关键页面。
- 在线服务配置在真实对话前后均为 `minimax / MiniMax-M3.1-Flash-Preview`。这是当轮 active 配置读数，不转借旧 MiniMax-M3 的发布证据，也不宣称逐轮 collector 模型归属审计。
- 真实对话使用现有签名 E2E 身份车道；适配层只替换认证 token 和该车道要求的 session ID，保留线上页面、真实 WebSocket、服务端回答和第三方数据。未向浏览器注入业务答案。只授予本轮查询所需 scope，未授予车控、商户写或支付权限。
- 卡片全覆盖、确认/删除弹窗、付款码、自动行车投影和故障状态另用浏览器内业务夹具。它们加载线上前端资源，但**不算真实业务端到端通过**；高德 SDK/瓦片仍为真实网络请求。
- 初验证据在 [初验目录](../../.artifacts/hmi-visual-audit-20261011/)。初验阶段未改产品源码、环境文件、密钥或部署配置，未推送/部署。检查期间共享 main 增加交接及探针记录提交 `3ee90358824b397c2dca109f0c270f1d13ac9e15`，其 `hmi/` 和 `test/hmi_cdp/` 与被测 release 无差异。

## 已复现问题

### V2-B01 · P2：未上翻历史也会停止跟随新回答

复现：保持聊天停在最底部，输入“分别查询北京和广州今天的天气。”；等待过程区和最终回答，期间不滚动、不点历史。页面自动出现“有新消息”，最终回答和卡片却在当前视口下方。点击胶囊后才能看到完整最新内容。

- 初次发生在“分别查询深圳和杭州今天的天气。”；随后两轮带事件记录的复测均复现，北京/广州、成都/重庆两组结果一致。
- 两轮复测发送前距底部均为 **0 px**，过程区变化后出现 **52 / 144 px** 的间距，最终均停在距底部 **1049 px**。事件记录没有 wheel 输入。
- 原因定位：[ChatView.tsx](../../hmi/src/components/ChatView.tsx#L46) 的 `onScroll` 仅按当前距离写 `followRef`；内容尺寸变化引起的浏览器滚动也会把它改为 false，随后 `ResizeObserver` 不再跟随。不是后端没返回答案。
- 修复方向：区分用户主动上翻与布局变化，保留“本来在底部就继续跟随”的状态；同时保留已经批准的 D12 历史阅读保护。不能用一律滚到底掩盖问题。
- 证据：[截图](../../.artifacts/hmi-visual-audit-20261011/bug-scroll-02.png)、[第一轮事件](../../.artifacts/hmi-visual-audit-20261011/bug-scroll-01.json)、[第二轮事件](../../.artifacts/hmi-visual-audit-20261011/bug-scroll-02.json)。

对照：主动上翻历史时，新真实回答到达前后 `scrollTop` 均为 **3587 px**，胶囊出现，点击后距底部 **0 px**；这条 D12 路径正常。见 [真实历史阅读证据](../../.artifacts/hmi-visual-audit-20261011/live-history-reader.json)。

### V2-B02 · P2：地图的“回到当前内容”按钮被顶部状态栏遮住

复现：询问“从杭州西湖文化广场开车到杭州东站要多久？”，地图就绪后，点击路线卡的“在舞台查看”。应能点击右上方“回到当前内容”退出选定舞台，实际只露出按钮底缘，点按钮中心没有反应。

- 按钮矩形为 `x=1714, y=24, width=182, height=64`；状态栏覆盖 `y=0…73`。
- 中心点命中的是 `.au-sb-actions`，实际点击后仍保持选定舞台。下缘未遮挡区域可以点击，所以属于位置/遮挡问题。
- 原因定位：[stage.css](../../hmi/src/stage.css#L7) 的返回按钮仍使用 `top:24px`；地图改为 [全屏 inset:0](../../hmi/src/stage.css#L35) 后，没有补偿状态栏高度。
- 修复方向：地图场景下将返回控件排到状态栏下方，并同时避让“全览”按钮和地图标题。
- 证据：[截图](../../.artifacts/hmi-visual-audit-20261011/bug-map-back-button.png)、[坐标与命中元素](../../.artifacts/hmi-visual-audit-20261011/bug-map-back-button.json)、[真实点击结果](../../.artifacts/hmi-visual-audit-20261011/bug-map-layer-evidence.json)。

### V2-B03 · P2：高德署名穿透全屏设置页

复现：真实地图显示时打开设置。深浅主题均能看到右下角高德 Logo、`© 2026 AutoNavi` 浮在设置页之上；无地图场景则不出现。

- 高德版权元素的计算 `z-index=160`，设置遮罩为 `60`；舞台未建立独立层叠上下文。版权位置的命中元素也是 `.amap-copyright`，不是设置容器。
- 在浏览器中临时给 `.au-stage` 添加 `isolation:isolate` 后，命中元素变为 `.au-settings-body`，署名不再穿透。该诊断 CSS 随即撤掉，**没有修改线上代码**。
- 原因定位：[shell.css](../../hmi/src/shell.css#L8)、[设置层级](../../hmi/src/shell.css#L111) 与 SDK 内部元素层级共同作用。
- 修复方向：隔离舞台的层叠上下文；地图可见时继续保留完整合法署名，不直接删掉 SDK 版权元素。
- 证据：[深色设置截图](../../.artifacts/hmi-visual-audit-20261011/settings-dark-display.png)、[浅色设置截图](../../.artifacts/hmi-visual-audit-20261011/settings-light-tts.png)、[前后命中读数](../../.artifacts/hmi-visual-audit-20261011/bug-map-layer-evidence.json)、[临时 CSS 诊断截图](../../.artifacts/hmi-visual-audit-20261011/diagnostic-logo-isolation-only.png)。

## 初验通过的检查

| 车道 | 当轮观察与证据 |
|---|---|
| 真实对话 | 天气、同会话“那明天呢”、路线估算、手册只读回答、两城市多结果均有真实 final；没有 action。多结果展开后两项回答可见。布局是否通过仍受 V2-B01 约束。 |
| 真实地图 | 杭州公开起终点、道路底图、路线折线、标记、出处正常；浅色/大字/720 高度可见；行车隐藏输入与地图摘要、标记禁用。地图与设置/舞台返回的交叉流程发现 B02/B03。 |
| 真实设置 | 12 分区 × 深浅 = **24 个页面**，无横向溢出、无读取回退；长页可滚到底。Select 的 End / Escape 操作及焦点返回通过。未切换全局 LLM。 |
| 真实播报 | MiniMax TTS：所取样本 **645 个二进制 PCM 帧、645 次 AudioBuffer 起播/结束，active=0**；累积约 **27.348 秒**，存在非静音音频。仅证明浏览器合成/播放生命周期，未作人耳听感验收。 |
| 中断与恢复 | 实际点击“停止”产生 cancel，界面显示“已打断”；随后天气请求正常 final。见 `live-cancel-recovery.json`。 |
| 读历史 | 真实新回答到达时保持用户主动上翻的位置，点击“有新消息”才回底部。与 B01 的“没有上翻”场景分开判定。 |
| 卡片夹具 | **44 张夹具 × 深浅 × 标准/大字 = 176 次渲染组合**；含空字段、数据来源角标、付款码和行车摘要；现有几何断言通过。 |
| 布局夹具 | **12 组**高度/主题/行车组合，另有 token 四档、减少动效与无模糊回退检查；通过。 |
| 情境舞台夹具 | **22 个深浅场景**，另测车况、媒体、缺失续航和行车隐藏付款码；通过。 |
| 地图 SDK 夹具 | **9 个布局场景**，另测缺坐标、取消、选择、加载失败与重试；真实 SDK，业务出帧 **0**。 |
| 交互夹具 | 固定确认条、多个 operation ID 切换、到期禁用、删除确认取消零请求、读取失败回退、只读 driving/speech 投影通过；没有真实写确认。 |
| 浏览器错误 | 本轮 live lane 捕获到的未处理 JS 异常、console error/warning、非取消网络失败/HTTP 4xx/5xx 均为 **0**。它不能证明没有视觉缺陷；B01–B03 均未触发这些错误。 |

复用的 7 份 `visual_*.mjs` 检查及独立地图检查均退出 0。它们原有的断言没有覆盖 B01 的实际过程区尺寸竞争、B02 的返回按钮命中、B03 的 SDK/设置跨层遮挡，因而本次“脚本通过”不能覆盖实际复现结果。有关几何扫描的原有反向验证仍保留。

Figma 本轮只读取 `34:9` 和 `54:9` 截图，用于外壳/设置结构对照；token 与四档尺寸沿用已有受控快照验证，未重新遍历整个设计文件。服务端字段、引擎选项与数据值按契约判断，不因示例不同列为缺陷。

## 排除项与未执行项

- 首轮校准的签名会话格式不满足 E2E 网关要求，发送后被断开。修正驱动为 `<signed-user>-session-<positive-number>` 后正常；不是 HMI 发布故障，不计入通过数。
- 为隔离记忆而关闭 `memoryEnabled` 的一轮追问没有继承城市。已核对该设置明确关闭焦点/历史读写；按正常设置重测“深圳天气 → 那明天呢”正确回到深圳。不把校准样本当多轮回归。
- 受限测试身份下，“小米 SU7 怎么查看胎压”“说明充电口怎么打开”两句被服务端车控授权前置检查拒绝，没有动作。它们没有验到所期望的手册路径，不计通过，也未在本轮扩充车控权限绕过；只读权限的路由粒度可另行排查。
- 真实麦克风、真人唤醒/声纹/相机、车机 GPU/触控、人耳播报、真实车控、商户下单/付款、云端记忆删除未执行。相关视觉状态只有夹具证据，不算硬件或写操作验收。
- 按用户已经裁决的契约差异保留离线文案、经历删除范围说明等，不将其重新包装成此次新增 bug。

## 修复记录（2026-10-11，发布前验证）

用户要求“修复这些问题”后，在 `3ee90358824b397c2dca109f0c270f1d13ac9e15` 基础上完成同一批修复：

- **B01**：消息提交后在布局阶段跟随底部；记录上一次滚动位置/内容高度/视口高度，区分布局引起的滚动和主动上翻。继续保留 D12，不对正在阅读历史的人强制回底部。
- **B02**：地图返回控件与“全览/重试地图”放入同一顶栏，位于状态栏下方。标题与按钮按实际宽度排列；地图视野避让读取整条顶栏尺寸，返回仍调用原有 `StageView.close`。
- **B03**：只给 SDK 画布建立独立层叠上下文，SDK 内部 `z-index` 不再穿透 HMI 设置层；地图署名未删除、未改写。
- `types.ts` 与整个 `App.tsx` 相对上述基线均无改动，无新增依赖、无服务端/配置变化。

| 修复验证 | 本批结果 |
|---|---|
| HMI 单测 / TypeScript / 构建 | **413 passed、0 failed**；`npm run typecheck`、`npm run build` 通过。原有大于 500 kB 的 bundle 提示仍在。 |
| 滚动回归 | 新增 `visual_scroll.mjs`：**8 组**底部跟随/历史阅读检查、**3 次**视口/迟到内容尺寸变化通过；旧实现在尺寸变化检查中实际失败。 |
| 地图命中/层级回归 | 新增 `visual_map_layers.mjs`：深浅 × 标准/大字 × 1080/720，共 **8 组**通过。修前 8 组均复现按钮遮挡与署名穿透（32 条失败），修后全部通过。 |
| 既有对话与地图回归 | `visual_conversation.mjs` 通过；原地图 SDK **9 场景**及缺坐标、取消清空、失败重试通过，业务出帧 0。 |
| 修复版 + 真实云后端 | 本地 Vite 的修复前端连接真实云端 `0c07bc3341ef74d96467dbbb4e4e8163b29fca7d`。三组两城市天气连续请求，完成后均距底部 **0 px**、无误判历史/新消息胶囊；真实主动上翻前后位置均为 **0 px**，点击胶囊才返回底部。 |
| 真实地图交叉流程 | 真实杭州路线估算，深浅两版返回按钮均位于 `y=104`，状态栏底为 `73`，中心命中并能退出选定舞台；设置完整遮住两项 SDK 署名。 |
| 真实中断/恢复 | 停止按钮产生 cancel，显示已打断；下一轮天气正常 final。此次真实流程 action 为 0，浏览器未捕获 JS/console/network 错误。 |

真实复测的签名身份、后端 SHA、活动模型与校准排除项边界同上；复测读取的 active 为
`minimax / MiniMax-M3.1-Flash-Preview`，未切换它。后端前后独立 status 均为同一 SHA、5/5 healthy。
这证明修复版前端接真实服务的结果，**不是线上旧前端已经修好**。

证据：

- [滚动修前](../../.artifacts/hmi-visual-v2/fix-20261011-scroll-before/scroll-evidence.json) / [修后](../../.artifacts/hmi-visual-v2/fix-20261011-scroll-after/scroll-evidence.json)。
- [地图修前](../../.artifacts/hmi-visual-v2/fix-20261011-map-before/map-layer-evidence.json) / [修后](../../.artifacts/hmi-visual-v2/fix-20261011-map-after/map-layer-evidence.json)。
- [修复版真实对话](../../.artifacts/hmi-visual-fix-20261011/live-scroll-fixed.json)、[真实历史阅读](../../.artifacts/hmi-visual-fix-20261011/live-history-reader.json)、[真实地图命中](../../.artifacts/hmi-visual-fix-20261011/live-map-fixed.json)。
- [修复版深色地图](../../.artifacts/hmi-visual-fix-20261011/fixed-live-dark-map-return.png) / [浅色设置](../../.artifacts/hmi-visual-fix-20261011/fixed-live-light-map-settings.png)。
- [源码指纹](../../.artifacts/hmi-visual-fix-20261011/candidate-fingerprint.json)、构建与单测日志在 [修复目录](../../.artifacts/hmi-visual-fix-20261011/)。提交后对账 Git blob 与指纹，不转借其他源码的读数。

上述结果属于发布前候选。用户随后明确授权“推送部署验证”，执行与线上证据如下；硬件与真实写操作的未验范围保留。

## 授权发布与线上复验（2026-10-11）

- 精确推送并部署修复提交 `64be4936cd6898a44660f768d6233d7ef8ed0aa4`；部署基线 `0c07bc3341ef74d96467dbbb4e4e8163b29fca7d`。干净隔离工作树只复制 `dev-stack.local`，没有复制 `.env`。dry-run 无阻塞，既有基础设施摘要一致；没有新增 schema、Compose、CI/CD、密钥或系统配置变更。
- [该 SHA 的 CI](https://github.com/SuperdeMan/cockpit-agent/actions/runs/38107072447) 8 项全部成功。apply 完成后独立 `status` / `verify` 通过，具体权威发布字段与 artifact 只维护在 QA 交接 §2。
- 线上页面、真实业务 WebSocket 与实际高德 SDK：3 轮两城市天气完成后距底部均为 0，无误判历史；主动上翻位置保持、点击胶囊回底部；停止后下一轮正常恢复。真实杭州路线在深浅主题下返回按钮中心均命中，`y=104` 位于状态栏底 `73` 下方，点击能够返回；设置完整覆盖两项 SDK 署名。业务 action 为 0。
- 同一已加载浏览器再隔离业务 `/ws`、跑线上资源的 8 组地图层级矩阵（深浅 × 标准/大字 × 1080/720）：全部通过。该矩阵是业务夹具，单列于真实对话证据，不冒充 8 条真实业务旅程。
- 浏览器复验前后 release 固定，active 为 `minimax / MiniMax-M3.1-Flash-Preview`，未切换。没有捕获到 JS 异常、console error/warning 或非取消网络失败；首次页面等待超时另记在 B04，不能被这条“无运行时错误”覆盖。
- 留证在 [发布目录](../../.artifacts/hmi-visual-release-20261011/)：`dry-run.stdout.log`、`apply-result.json` / `apply.stdout.log`、`verification-bound.json`、`ci.json`、`status-before.json` / `status-after.json`；浏览器 [真实复验结论](../../.artifacts/hmi-visual-release-20261011/online-result.json)、[三轮滚动](../../.artifacts/hmi-visual-release-20261011/online-tail-results.json)、[真实地图](../../.artifacts/hmi-visual-release-20261011/online-map-results.json)、[8 组层级矩阵](../../.artifacts/hmi-visual-release-20261011/map-layer-evidence.json)。

### V2-B04 · P2：首屏/页面重载偏慢（已修并通过线上复验）

- 本次 Windows Edge + 私网链路下，两次独立浏览器会话的 DOMContentLoaded 为 **127.1 s / 107.5 s**。第一次实际 UI 最终正常显示，但超过原驱动 30 s 等待；并行启动的地图矩阵也超过其 20 s 本地默认等待。两次超时均不计作通过。
- 第一趟记录了 94 个资源；页面使用 Vite 开发模块入口（`/@vite/client`、`/src/main.tsx`），多个资源请求耗时 20–37 s。后续已加载浏览器内 8 次页面导航仍在 **2.7–38.2 s** 之间波动。
- 仅将本次验收驱动的页面就绪等待扩至 180 s，保留全部交互和几何断言；等待正常渲染后完成上述三项修复复验。没有修改产品超时、服务器配置或部署方式。
- 初验时尚未区分私网传输、开发模块加载、首次编译的各自影响，未将其归因于该批 4 个 HMI 文件改动；后续拆分与候选修复见下方 B04 记录，线上性能验收继续保持开放。
- 证据：[首次资源时序](../../.artifacts/hmi-visual-release-20261011/bootstrap-attempt-1.json)、[第二次冷加载](../../.artifacts/hmi-visual-release-20261011/bootstrap-attempt-2.json)、[后续导航时序](../../.artifacts/hmi-visual-release-20261011/warm-navigation-timings.json)。

用户要求继续修复后，定位到云端开发模块分发开销，改为构建产物服务；本地受控网络三轮中位数 21.356 s → 1.833 s、95 → 6 请求。用户单独批准两处云端启动配置后，已在 `32bd007c03a748fa7eddd8a10f409a57f4a8bf0f` 完成线上复验：三次独立新浏览器就绪 1.012 / 1.013 / 1.014 s，6 请求、传输中位数 296,573 bytes；真实三轮底部跟随、历史阅读、中断恢复与深浅地图通过，另有 8 组地图矩阵、6 项声音资源通过。线上 ORT 按实际 bundle 引用和运行镜像指纹验证。浏览器启动、驱动表达式与本地 ORT 文件名的三项探针校准失败单独保留，修正后重跑，未计为通过。详见 [B04 修复记录](../design/2026-10-11-hmi-startup-performance.md)，发布权威字段在 QA 交接 §2；不替代真人麦克风/实车验收。
