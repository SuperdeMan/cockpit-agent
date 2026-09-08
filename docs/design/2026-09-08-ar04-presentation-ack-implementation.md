# AR04：跨页语音宿主与提醒呈现 ACK

日期：2026-09-08。状态：**客户端核心修复已合入并推送；`433fb3cf` 上 726 条本地测试通过，OPPO 当前候选包为 `433fb3cf0`。真机已验证草稿、跨页层、停播和前后台麦克风撤回；地图退出发生 native 崩溃、真实提醒投递尚待授权，AR04 未整批签收。**

输入：[分批建议 AR04](2026-09-07-android-review-remediation-batches.md#ar04)、
[完整评审 R08/R09](../reviews/2026-09-07-android-ux-full-review.md)、
[AR03 第八、十节](2026-09-08-ar03-stop-playback-landscape-implementation.md)。
本页是 AR04 的具体实施方案；AR01–AR03 原有设备证据仍绑定各自代码和 APK。

## 1. 改前接手基线

| 项目 | 本轮实际核查 |
|---|---|
| 工作树 | `main`，改前 clean，`HEAD == origin/main` |
| 改前源码 | `cd5c19b5e1d67fcf07713ced9376e2612d6ec999` |
| 真栈目标 | 根 `dev-stack.local` 与 `python scripts/dev_stack.py target show` 均为 `cloud` |
| Android 环境 | `scripts/check_android_env.ps1`：18 pass / 0 warn / 0 fail，exit 0 |
| Mobile 改前测试 | `npm test`：70 suites / 695 tests，32.839s，exit 0；实际为 Jest 默认 worker 模式 |
| Mobile 改前类型 | `npm run typecheck`：exit 0 |
| APK / 云端 | 改前阶段尚未构建或装机；本批后续构建见第九节，后台未由本批部署 |

已读根 AGENTS/CLAUDE、架构总体结构与 §2.4、多端契约 §9.33、主动投递契约 §9.8、
mobile README、Android 构建指南、完整评审、分批建议与 AR03 交接；源码沿
`RootLayout → ChatScreen/ChatBody → usePtt/useHandsFree/usePresence → SessionCore → GatewaySession`
及 SpeechController、VisionCapture、Composer、VoiceSheet、FocusDock 核查。

项目仍是两端一脑：HMI 与 Android 共享会话/确认/语音判据，UI 和原生引擎按平台实现。
AR04 处理客户端呈现与生命周期，不变更 VAL、能力授权、Provider、模型或后台部署。

## 2. 已核实的问题

| 问题 | 当前代码与后果 | 本批修复落点 |
|---|---|---|
| R08：入库即 ACK | `SessionCore.handleFrame(proactive)` 在 appendMessage 后立即 `speech.proactive` 和 `transport.send(proactive_ack)`；尚未有任何可见性证据，deliveryId 已进 `presented` | 收到、呈现、用户处理分别登记；呈现出口驱动 ACK 与一次播报仲裁 |
| R08：丢失 ACK 无补偿 | 同 deliveryId 已在 `presented` 就直接 return；如果前次 ACK 在链路上丢失，服务端再次补投时客户端也不重新回执 | 已呈现补投只重回 ACK，不重新出卡或播报；离线 ACK 留待重连 |
| R09：不可见页仍有控制器 | `ChatBody` 持有 `usePtt/useHandsFree/usePresence`；导航压栈不代表卸载。设置、地图页没有这些控制器对应的提示和停止出口 | 会话/语音控制器移入应用级宿主，活跃页面只呈现同一份事实 |
| 前后台缺口 | handsFree 与 speech 没有统一 AppState 门控；只把提示栏挪到根布局不能阻断迟到的 ASR/S2S/TTS | 同步撤回采集和音频；回调代际失效；文字终态继续归入原请求 |
| AR03 旋转疑点 | 外层覆盖域的 `onLayout` 只在 `splitLandscape` 为真时挂上；首次竖屏布局未缓存列高，切横屏时若边界未变，新增回调不保证触发布局事件 | 高度测量始终挂载，按布局事实选择覆盖域；做“先竖后横”的用例，不能只验横屏冷启动 |
| 草稿与待办视图生命周期 | Composer 文本草稿存组件内部；FocusDock 展开态也在局部组件，布局换父节点会重挂载 | 草稿/列表展开态放到会话级 UI 宿主；确认仍以稳定 operationId 操作 |

最后两项中，“回调条件挂载”和“局部 state”是代码事实；旋转故障的因果仍需回归及设备验证。
AR03 已证伪的“升层自动停播”怀疑不重新列为缺陷。

## 3. 已确认的产品范围

用户于 2026-09-08 明确“确认方案”，采用下表的应用内跨页语音范围。

| 场景 | 语音 / 采集 | 呈现与待办 |
|---|---|---|
| 对话、地图、车况、设置页面，应用前台 | 沿用同一个 PTT/免唤醒/S2S 控制器；只延续用户已开启的能力；页间跳转本身不开麦 | 活跃页始终有采集事实、只停播、关闭本轮收音与展开回答入口；待办保留并按 ID 操作 |
| Onboarding、配置失效、开发诊断或未列入的路由 | 主会话采集与播报暂停；诊断原有的显式操作仍受 AR02 限制 | 主动消息保留待呈现；不因进入任意深链而启动主会话语音 |
| 后台、锁屏 | 停 PTT/ASR、免唤醒麦租约、S2S 上行/播放、TTS 与视觉准备；作废未完成采集回调 | 不新增呈现 ACK；已发主链请求的文字/卡片可继续落账，不把切后台称作业务取消或回滚 |
| 回前台 | 不恢复被打断的录音、不自动续播旧回答；免唤醒只在设置已开启且支持页可见时重新进入待唤醒 | 恢复未呈现提醒；保持 deliveryId 去重；文字草稿、操作对象和已接收记录保留 |
| Android 窗口失焦（如通知抽屉） | 与真正后台分别记录；权限弹窗、失焦和锁屏在设备上各自取证，不能假定只收到 AppState change | 失焦期间不认定有效呈现；迟到的布局/可见性回调必须复查窗口事实 |

“只允许对话页语音”的备选未采用。

通知 `presented` 定义为内容进入有效可见出口，并不声称用户已经阅读或处理。
“用户处理”只在明确的查看/收起等用户手势后记本地事实，不发送虚构的服务端 handled 状态，
也不自动执行提醒卡携带的业务动作。

## 4. 宿主边界与迁移图（推荐范围）

```text
当前
RootLayout
  ├─ VisionCapture
  └─ Stack
       ├─ ChatBody：配置装配 + PTT/HF + presence + 草稿 + 语音层 + Dock
       └─ map / vehicle / settings：看不到上述控制器的状态和出口

迁移后
RootLayout
  └─ AssistantRuntimeProvider（一个会话、原有控制器；不增加语音 FSM）
       ├─ 前台/路由事实与配置切换 → 同步撤回、重连与晚回调闸
       ├─ VisionCapture（沿用 AR02 的物理事实与取消信号）
       ├─ Stack
       │    └─ ChatBody：记录、页面布局、舞台，以及当前页的宿主视图插槽
       ├─ AssistantSurface：非对话支持页上的语音壳与操作入口
       └─ ProactivePresenter：有效呈现 → SessionCore 回执方法

所有呈现出口 ──读──> 同一份 SessionCore / presence / playbackFacts / captureFacts
所有用户操作 ──调──> 同一份 onSend / onConfirm / stopPlayback / stopMic
```

- Provider 负责配置与 wired 会话、PTT/HF 接线、草稿、sheetOverride、notice、隐私栏和待办列表展开态。
  不复制 `voiceLoop.mjs`、`derivePresence`、请求注册表或确认台账。
- ChatBody 保留消息列表、欢迎态、舞台与尺寸类布局；从 Provider 取动作和快照。
  地图仍负责坐标、选择点、相机和自己的内容，设置仍负责设置；它们不创建新语音控制器。
- 对话页和跨页壳互斥呈现同一语音组件。跨页底栏占布局空间，不能压住地图详情或设置开关；
  语音层的覆盖域有实际测量，FocusDock 和必要停止出口在覆盖域外。
- 保留 v1 回滚路径可用；关闭 UX v2 外观开关不能绕过采集指示或恢复“入库即 ACK”。
- 切换服务器/身份时撤回旧控制器和待发回执，旧配置异步加载结果不得覆盖新配置。
  配置读取和重连不输出 token，也不更改根 `.env` 或 mobile 环境文件。
- 跨页不会改变用户发出的主链请求身份；折叠/旋转沿用原来的 PTT 松手规则。
  新增验证“文本草稿不丢”与“展开列表仍指向原 operationId”。

## 5. 提醒合同

### 5.1 收到与去重

SessionCore 的 proactive 分支只校验内容、提取共享 `deliveryIdsOf()` 凭据、登记气泡及投递元数据。
`Msg` 是共享类型，本端元数据并列存放，沿用 turnMeta 的形式，不为了本端 UI 修改共享类型。
空 speech 且无 card 时不消费 deliveryId，后续有效补投仍可进入。

同凭据的未呈现补投复用待呈现对象；已呈现补投只补回 ACK。
单 ID、合并 delivery_ids、组内重复、部分重叠和无 deliveryId 的旧协议都要有明确用例。
无凭据消息可呈现，但不伪造 ACK 或用话术相同冒充可靠去重键。

### 5.2 有效呈现出口

呈现方法按本会话 messageId 查真实元数据，不能让调用者任意指定 delivery_ids 销账。
只有应用前台、窗口可交互、当前支持页的实际内容出口已经布局/可见，且未被语音层或 Modal
完全遮挡时才调用。FlashList 的测量 render、store 更新、路由仍 mounted、TTS 请求已发都不算。

记录列表用稳定的 viewability 回调；跨页提醒出口展示真实文字/卡片，只有“有新消息”的占位条
不销账。进入后台、切路由或更换会话后，已排定的呈现回调再次复查有效性。
呈现/播报是否成功要分开记：ACK 不依赖 TTS Provider 正常；同一提醒只进入一次播报仲裁。

### 5.3 ACK 与重试

使用既有帧 `{type:'proactive_ack', session_id, delivery_ids}`，无需 proto、schema 或云端部署。
立即发送使用 `sendIfOpen`；断线留待发回执，连上再发，不能把 ACK 混入用户请求 queued 数量。
发送失败保留待发项；已发后服务端再次补投，允许重 ACK，保持用户侧不重复播报。
`dispose` 后全部回调失效，旧会话 ACK 不进入新配置的连接。

### 5.4 后台音频

后台不仅是“不调用 proactive”：同一主链请求的迟到 delta/final、批处理兜底和 S2S 也可能出声。
SpeechController 的生成/接段/兜底入口需要共同遵守前台门控；停播时清 DEFER，避免
S2S 变 idle 的同步回调把队列重新放出来。沿用 AR03 的停播顺序与两轴播放事实。
后台期间到达的提醒留在未呈现队列，回前台实际呈现后再进行一次播报仲裁；旧回答不补播。

## 6. 实施顺序与验证

| 步骤 | 修改面 | 完成判据 |
|---|---|---|
| 1 | SessionCore 收到/呈现/处理及回执重试 | 可控假投递：收到零 ACK 零播报，呈现一次，重投只重 ACK；断线/重连/换会话不会丢账或串账 |
| 2 | 应用级配置和语音宿主；ChatBody 消费接线 | 应用内切页不重复创建控制器；草稿/指定操作保留；非支持路由暂停 |
| 3 | 前后台撤回与 SpeechController 门控 | PTT 权限等待、ASR finalizing、HF 启动、S2S、TTS 缓冲/兜底、视觉准备的迟到回调均不能复活 |
| 4 | 跨页提示、停止键、提醒呈现出口与覆盖域 | 支持页都能直接停声音/查看采集；隐藏列表和被遮挡内容零提前 ACK；先竖后横层仍可开 |
| 5 | 本地验收、自审、精确提交 | 新增针对性反例回归 + mobile 全量 Jest / tsc；若共享源改动才加 HMI 检查；扫描断言需变异判红 |
| 6 | 冻结 clean SHA 后构建 prod release | 沿共享构建指南串行使用镜像；验包、内嵌 bundle、build SHA、原生模块和安装文件哈希一致 |
| 7 | OPPO 定向设备验证 | 对话/设置/地图/后台/锁屏各自投递，逐条核呈现与 ACK；回前台不重复；跨页停播与零残留采集；路由/折叠/旋转不丢草稿与操作 |

先用不接生产的假投递通道覆盖确定性边界。真实提醒另以获授权的具名测试提醒验证，
记录创建、到期投递、设备呈现、ACK 与剩余台账；不以真实商户操作替代提醒测试。
若线上证据需新增提醒、清理数据、生产部署或使用 Xiaomi 主用对照机，先给出该动作的具体范围再取授权。
本轮用户已授权必要的 commit/push，推送前仍逐条展示 `origin/main..HEAD`。

## 7. 签收与剩余项

AR04 必须分别记录：合同单测、组件交互、APK、设备可见性和真实提醒回执。
任一设备格或真实投递未取到，状态保留“本地修复待设备或发布验证”，不得用 695 条改前测试签收。
AR02 的完整采集矩阵、AR03 的多段/S2S/盲听、AR07 唤醒率及 AR08 时延仍归原批次。

实现时参考已核对的官方接口：
[Expo SDK 57 Router](https://docs.expo.dev/versions/v57.0.0/sdk/router/)、
[React Native AppState](https://reactnative.dev/docs/appstate)、
[FlashList viewability](https://shopify.github.io/flash-list/docs/usage/)。
依赖版本维持仓库 lockfile；不因文档推荐的小版本变化升级 SDK。

## 8. 实现与本地验证（2026-09-08）

当前 Android 代码锚：`433fb3cf0c62e51493dcc2b05e9eb87f92921beb`。后续文档提交不改变这个 APK / 测试锚。

| 代码面 | 实际落点 |
|---|---|
| 单一宿主 | `features/assistant/AssistantProvider.tsx` 持有原有会话、PTT/HF、presence、草稿、隐私栏和稳定 ID 的待办展开态；ChatBody 只消费它。没有新增语音 FSM，HMI 共享源码未修改 |
| 跨页呈现 | `AssistantSurface` / `CrossPageVoiceLayer` / `AssistantFrame`；操作栏占真实布局空间，语音层有边界，非对话页避让键盘。跨页语音层用实色底，采集栏只显示真实采集状态 |
| 回执 | SessionCore 分开 `receivedAt`、`presentedAt`、`handledAt`；`handledAt` 是用户收起提醒的本地事实，不是业务完成。`ProactivePresenter` 复查前台、窗口、遮挡与原生窗口测量；列表消费 viewability，测量 render 不算呈现 |
| 去重与补偿 | 凭据从本会话消息取；重复收到不重复气泡，已呈现补投只补 ACK；`sendIfOpen` 失败留待发回执，重连再试，不计入用户 queued。`ackSentAt` 仅表示写入 socket，不能冒充服务端收妥 |
| 生命周期 | `InteractionScope` 同步撤回 PTT、HF、S2S、视觉与播报；旧权限/合成/识别回调不能复活；已发主链请求仍按原 requestId 落文字终态。键盘、窗口失焦和 Modal 阻断提前呈现 |
| 播报所有权 | 主链与批处理分别持有 playbackFacts；批处理自然结束不能清主链事实。主链/批处理占用与 S2S 一起交给现有共享优先级判据，critical 抢话、user_contract 排队，显式停止清等待项 |
| 录音权限 | 先 `checkRecordingPermissions()`，仅未授权时申请；两次 await 都检查代际有效性。已授予权限时不会反复启动权限 Activity |
| 只读取证 | `capture-status` 增加最近 20 条投递时间/凭据，不输出提醒内容或 token。进入诊断路由会暂停主会话；dev 显式播报探针使用同实现的独立实例并守页面/前台门控 |

| 提交 | 验证与边界 |
|---|---|
| `5d1379e31e2de51f51a7289fe57c1f510b54b250` | 首版宿主/ACK；71 suites / 717 tests、tsc、新文件定向 lint。随后独立并发探针发现两个播放器只停了最后一个，本版本不作为最终验收锚 |
| `61801bfe75334ebbacdbf3bf488a221bc6eb4ff1` | 播报排队与所有权修复；71 suites / 724 tests、tsc、定向 lint。OPPO 取到中文草稿与跨页结构/停播证据，又发现录音权限 Activity 循环 |
| `433fb3cf0c62e51493dcc2b05e9eb87f92921beb` | 权限查询与跨页呈现修正；**71 suites / 726 tests，70.116s，exit 0**；tsc exit 0；显式 Expo flat 配置下选定文件 lint exit 0 |

固定命令为 `node node_modules/jest/bin/jest.js --runInBand --silent` 与 `node node_modules/typescript/bin/tsc --noEmit`。
仓库尚无生效的 `eslint.config.*`，本轮通过的是显式 `--config node_modules/eslint-config-expo/flat.js` 的选定文件检查，**不是全仓 lint 门禁**；配置治理仍归 AR06。

反向证据：恢复条件式列高测量，旋转用例即红；恢复入库即 presented，回执用例即红；两条源码均按原字节恢复。
并发播报的 4 条永久回归、权限查询的 2 条永久回归，都先在未修实现上取得红灯，再修到通过。
本轮没有用 skip、宽松断言或延长超时掩盖失败。测试渲染器没有真实布局：原生测量在测试中明确注入，屏幕外测量也必须拒绝 ACK。

中途并行合入的 `5d3fd50` 是另一批 llm-gateway CI 修复；本页只报告 Android 的检查，不替它报告后端 CI 或生产发布结果。

## 9. 构建、安装与真机结果

三次构建均为 prod release、单编译任务、Gradle 堆 `128m/2048m`、Kotlin in-process；签名 SHA-1 保持
`5e8f16062ea3cd2c4a0d547876baa6f38cabf625`。

| 包 | 构建与使用 |
|---|---|
| `5d1379e31` / 13:36 | 首次完整构建：Gradle 23m28s，1222 tasks（737 executed / 485 from cache），验包成功；只保留为诊断候选，未装机 |
| `61801bfe7` / 14:14 | 核对 345 个 mobile/HMI 文件与 19 个原生资产，匹配后只同步 4 个差异文件，沿原构建脚本 §5–§6 接续；7m33s，52 executed / 1170 up-to-date；OPPO 14:23:44 安装 |
| **`433fb3cf0` / 15:30** | 同口径核对并同步 6 个差异文件；5m47s，52 executed / 1170 up-to-date，exit 0、验包成功；**OPPO 15:41:31 安装，当前常驻候选** |

接续未重新 MIR / prebuild，使用新单次 daemon 注入本次身份；原生工程、依赖、模型和签名均未改变。
APK 内 `assets/app.config`、内嵌 bundle 与必需原生库经过验证，安装文件与本地产物 SHA-256 相同，非 DEBUGGABLE。

当前产物：`D:\Android\builds\apk\xiaozhou-companion-prod-release-433fb3cf0-20260908-1536.apk`，210,703,730 bytes。
SHA-256：`117746efb1139e963d21a3bf40adc8322653c36faf78c90d278b4ad160201243`。
构建行：`v0.1.0 · prod · 433fb3cf0 · 2026-09-08 15:30`。
构建日志保留 SDK XML 版本、NODE_ENV、颜色环境变量与 Gradle 弃用提示；这些提示未替代退出码和验包检查。

**OPPO PEUM00 / Android 14 的本轮读数：**

| 验证项 | 结果与证据边界 |
|---|---|
| 草稿跨页 | `61801bf` 上真实 Maestro 中文输入“AR04 待发送草稿”，设置→地图→车辆→对话后完整保留；`433fb3cf` 上另用 `AR04 final draft` 复验设置→车辆→对话，全部通过。测试草稿已清除 |
| 跨页层与操作栏 | 旧候选上地图/设置层均能展开、收起，层底 `[0,974][988,1623]`、栏 `[0,1623][988,1972]` 零重叠；最终包另取实色地图语音层截图，地图文字不再透入回答区。地图正常退出另见下节，不能因进入/呈现通过就关闭该项 |
| 跨页停播 | `433fb3cf` 用普通故事请求，点击 App 内设置入口；第 5 个采样取到跨页停止键，采集栏明确“麦克风关闭”；按停后两次采样均无停止键复现。证据是播放事实与操作结果，未做人耳盲听 |
| 前后台采集 | `433fb3cf` 设置页启动 HF→Home→回设置→停止收音→关闭 HF。最终计数 **micStarts=2 / micStops=2，ASR=0 / S2S=0**；原生录音记录前台 active、VOICE_COMMUNICATION、AEC/NS 在场，后台当前录音配置清空 |
| 权限循环 | 未修包已有权限仍反复启动 `GrantPermissionsActivity`，Native 录音未开始；系统日志留有 144 条相关 Activity 行。修复后的上述 2/2 采集流程成立，原循环已关闭 |
| 提醒呈现合同 | 假投递/组件/遮挡/断线/补投边界已通过本地回归；**五场景真实提醒未创建，等待本轮明确授权**。不能用空投递台账当作真实投递成功 |

真机取证条件与失败样本：

- 默认动画使首页 `uiautomator dump` 无法 idle；取证期间临时开 App 的“减少动效（强制）”。它不代表默认动画或性能验收。
- Maestro 2.9.0 与两个设备驱动均实存；中文输入首次成功。第二条 flow 的 `eraseText:80` 遇设备服务 DEADLINE_EXCEEDED 与宿主 heartbeat 文件锁，未发送；改用 ADB ASCII 输入并逐次回读。零时长 tap 对空输入框的 RNGH 背板不可靠，改用 120ms 手势且先验 focused。清空仅针对本轮文本，用 Ctrl+A/DEL。
- 外部 `am start` 切页的第一条故事只有文字、未取到播放窗口，未作成功样本或据此断定宿主故障；App 内设置 Link 的复验通过。
- `61801bf` 和 `433fb3cf` 分别在 15:02:50、15:42:49 发生地图退出 native 崩溃，原始栈保留，**没有归零或忽略**。
- 最后已恢复 **handsFree=false、speakPolicy=auto、reduceMotionForce=false**；没有修改系统密度、旋转、字号、音量或其他系统设置。测试 App 已 force-stop、回到 Launcher；该清理动作不是地图正常退出通过的证据。

本轮只读云栈 status 为 5/5 healthy、warnings=[]，release 为 `a09c73a5da3181708279bc1f3e90acb1519606a0`；没有部署后台、执行车控/商户写或付款。

## 10. 剩余项与需要的决定

**地图退出未通过。** 两次 SIGABRT 的错误均为 pointer tag 被截断，栈落在高德 `GLMapEngine.destroyAMapEngine / destroySurface`；依赖实际固定为 `com.amap.api:3dmap:9.6.0`。
地图页、地图插件和 npm 依赖相对 AR03 没有源码变化；这里记录的是本轮发现的 SDK 兼容性故障，不冒称已证明旧 APK 的同一复现。

[高德官方规避说明](https://lbs.amap.com/faq/android/navi-sdk/1000108216/1061016771)提供 App Manifest 的
`android:allowNativeHeapPointerTagging="false"` 方案。[Android 官方说明](https://source.android.com/docs/security/test/tagged-pointers)
明确它会关闭该 App 的指针标签检查，未修复底层库的问题，也不是面向未来 MTE 的长期解决方案。
2026-09-08 用户对两项具体请求回复“授权”：采用 App 级临时兼容配置，并执行下述最多五条真实提醒。`with-amap-key.js` 仅对启用地图的 App 增加以下配置，保持 targetSdk 36；后续升级 SDK 时须重新验证并评估移除此项。

```javascript
app.$ = app.$ || {}
app.$['android:allowNativeHeapPointerTagging'] = 'false'
```

采用后必须经过 CNG 生成、重新构建/验 Manifest，再在同一候选上验证地图正常返回、快速返回、前后台及重复进出；不能只靠属性存在销账。

**真实提醒已授权、尚未执行。** 范围是 OPPO 当前 App 账号下最多 5 条一次性提醒，分别用于对话、设置、地图、后台、锁屏，每条约两分钟到期；只取消本轮尚未触发的残留，不删除记录、不操作其他提醒。标题为 `AR04-0908-对话/设置/地图/后台/锁屏验收`（每条取对应场景），逐条核对服务端与客户端时点。

**设备范围未补齐。** OPPO 的 `driving-landscape` 不可达性仍按 AR03 记录留给支持范围/对照机验证；没有操作 Xiaomi，也没有做物理折叠、系统 200% 字号、真实 S2S 发声或盲听。AR02/AR03 的旧剩余格仍归原批次。

## 11. 原始证据与接续

证据目录：`%LOCALAPPDATA%\car-agent\artifacts\AR04-20260908`。

- `jest-433fb3c.json`、`jest-device-fix-result.json`、`static-device-fix-result.json`：最终代码检查；旧 SHA 各有独立文件。
- `mutations.json`、`batch-overlap-isolated.json`、`batch-regression-before.log`、`permission-regression-before.log`：反向与修复前证据。
- `build-device-fix.log` / `.err.log` / `build-device-fix-result.json`、`mirror-device-fix-manifest.json`、`apk-433fb3c.json`：当前构建与验包；原两包证据分别保留。
- `final-hf-settings-results.json`、`hf-settings-background-native.txt`、`final-story-results.json`、`final-draft-results.json`、`final-map-opaque.png`、`preferences-restored.json`：当前包的定向设备证据。
- `permission-activity-evidence.log`、`crash-buffer-current.log`、`amap-compat-proposal.patch`：权限循环原始证据、地图崩溃与未应用的兼容草案。

接续时先确认这两项授权/取舍，再核当前 SHA、设备包和资源。当前没有本轮构建、Metro 或 Maestro 流占用资源；不要从旧 session ID 猜仍在运行，也不要把本页“已修的部分”写成 AR04 整批通过。
