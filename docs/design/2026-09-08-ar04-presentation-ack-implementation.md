# AR04：跨页语音宿主与提醒呈现 ACK

日期：2026-09-09。状态：**生产 release 与 OPPO 包代码均为 `573ad46`，本轮发布及独立 status/verify 通过；标题修复已用真实提醒验证。真实 Keyguard 下不提前 ACK，解锁后呈现并只播放一次，本地收起后再次进入不重播。既有默认/全屏折叠保留证据及设置恢复仍有效。本轮授权已完成；服务端多 operationId 实机组合及 Planner 技术失败降级策略仍未闭合，AR04 未整批签收。当前交接见第十四节。**

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

当前 Android 代码锚：`573ad46d939bf655f5a0f4ef16579e2a9b80b087`。后续文档提交不改变这个 APK / 测试锚。

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
| `065efd85ee6365160862b009dcaffc50e8b57473` | 获授权的地图 App 级兼容配置；**71 suites / 726 tests，111.162s，exit 0**；tsc exit 0；地图插件定向 lint exit 0；配置插件无 key / 属性保留 / 幂等检查通过 |

固定命令为 `node node_modules/jest/bin/jest.js --runInBand --silent` 与 `node node_modules/typescript/bin/tsc --noEmit`。
仓库尚无生效的 `eslint.config.*`，本轮通过的是显式 `--config node_modules/eslint-config-expo/flat.js` 的选定文件检查，**不是全仓 lint 门禁**；配置治理仍归 AR06。

反向证据：恢复条件式列高测量，旋转用例即红；恢复入库即 presented，回执用例即红；两条源码均按原字节恢复。
并发播报的 4 条永久回归、权限查询的 2 条永久回归，都先在未修实现上取得红灯，再修到通过。
本轮没有用 skip、宽松断言或延长超时掩盖失败。测试渲染器没有真实布局：原生测量在测试中明确注入，屏幕外测量也必须拒绝 ACK。

中途并行合入的 `5d3fd50` 是另一批 llm-gateway CI 修复；本页只报告 Android 的检查，不替它报告后端 CI 或生产发布结果。

## 9. 前三次构建与设备历史（当前候选见第十二节）

三次构建均为 prod release、单编译任务、Gradle 堆 `128m/2048m`、Kotlin in-process；签名 SHA-1 保持
`5e8f16062ea3cd2c4a0d547876baa6f38cabf625`。

| 包 | 构建与使用 |
|---|---|
| `5d1379e31` / 13:36 | 首次完整构建：Gradle 23m28s，1222 tasks（737 executed / 485 from cache），验包成功；只保留为诊断候选，未装机 |
| `61801bfe7` / 14:14 | 核对 345 个 mobile/HMI 文件与 19 个原生资产，匹配后只同步 4 个差异文件，沿原构建脚本 §5–§6 接续；7m33s，52 executed / 1170 up-to-date；OPPO 14:23:44 安装 |
| **`433fb3cf0` / 15:30** | 同口径核对并同步 6 个差异文件；5m47s，52 executed / 1170 up-to-date，exit 0、验包成功；**OPPO 15:41:31 安装，前一常驻候选** |

接续未重新 MIR / prebuild，使用新单次 daemon 注入本次身份；原生工程、依赖、模型和签名均未改变。
APK 内 `assets/app.config`、内嵌 bundle 与必需原生库经过验证，安装文件与本地产物 SHA-256 相同，非 DEBUGGABLE。

前一产物：`D:\Android\builds\apk\xiaozhou-companion-prod-release-433fb3cf0-20260908-1536.apk`，210,703,730 bytes。
SHA-256：`117746efb1139e963d21a3bf40adc8322653c36faf78c90d278b4ad160201243`。
构建行：`v0.1.0 · prod · 433fb3cf0 · 2026-09-08 15:30`。
构建日志保留 SDK XML 版本、NODE_ENV、颜色环境变量与 Gradle 弃用提示；这些提示未替代退出码和验包检查。

**OPPO PEUM00 / Android 14 的本轮读数：**

| 验证项 | 结果与证据边界 |
|---|---|
| 草稿跨页 | `61801bf` 上真实 Maestro 中文输入“AR04 待发送草稿”，设置→地图→车辆→对话后完整保留；`433fb3cf` 上另用 `AR04 final draft` 复验设置→车辆→对话，全部通过。测试草稿已清除 |
| 跨页层与操作栏 | 旧候选上地图/设置层均能展开、收起，层底 `[0,974][988,1623]`、栏 `[0,1623][988,1972]` 零重叠；`433fb3cf` 候选另取实色地图语音层截图，地图文字不再透入回答区。地图正常退出另见下节，不能因进入/呈现通过就关闭该项 |
| 跨页停播 | `433fb3cf` 用普通故事请求，点击 App 内设置入口；第 5 个采样取到跨页停止键，采集栏明确“麦克风关闭”；按停后两次采样均无停止键复现。证据是播放事实与操作结果，未做人耳盲听 |
| 前后台采集 | `433fb3cf` 设置页启动 HF→Home→回设置→停止收音→关闭 HF。最终计数 **micStarts=2 / micStops=2，ASR=0 / S2S=0**；原生录音记录前台 active、VOICE_COMMUNICATION、AEC/NS 在场，后台当前录音配置清空 |
| 权限循环 | 未修包已有权限仍反复启动 `GrantPermissionsActivity`，Native 录音未开始；系统日志留有 144 条相关 Activity 行。修复后的上述 2/2 采集流程成立，原循环已关闭 |
| 提醒呈现合同 | 假投递/组件/遮挡/断线/补投边界已通过本地回归；`433fb3cf` 阶段尚未创建真实提醒；授权后的 `065efd85e` 实测见第十二节。不能用空投递台账当作真实投递成功 |

真机取证条件与失败样本：

- 默认动画使首页 `uiautomator dump` 无法 idle；取证期间临时开 App 的“减少动效（强制）”。它不代表默认动画或性能验收。
- Maestro 2.9.0 与两个设备驱动均实存；中文输入首次成功。第二条 flow 的 `eraseText:80` 遇设备服务 DEADLINE_EXCEEDED 与宿主 heartbeat 文件锁，未发送；改用 ADB ASCII 输入并逐次回读。零时长 tap 对空输入框的 RNGH 背板不可靠，改用 120ms 手势且先验 focused。清空仅针对本轮文本，用 Ctrl+A/DEL。
- 外部 `am start` 切页的第一条故事只有文字、未取到播放窗口，未作成功样本或据此断定宿主故障；App 内设置 Link 的复验通过。
- `61801bf` 和 `433fb3cf` 分别在 15:02:50、15:42:49 发生地图退出 native 崩溃，原始栈保留，**没有归零或忽略**。
- 最后已恢复 **handsFree=false、speakPolicy=auto、reduceMotionForce=false**；没有修改系统密度、旋转、字号、音量或其他系统设置。测试 App 已 force-stop、回到 Launcher；该清理动作不是地图正常退出通过的证据。

本轮只读云栈 status 为 5/5 healthy、warnings=[]，release 为 `a09c73a5da3181708279bc1f3e90acb1519606a0`；没有部署后台、执行车控/商户写或付款。

## 10. 前轮兼容决策与验证边界（最新状态见第十四节）

**地图退出历史故障已用获授权的临时兼容配置处理。** 修复前两次 SIGABRT 的错误均为 pointer tag 被截断，栈落在高德 `GLMapEngine.destroyAMapEngine / destroySurface`；依赖实际固定为 `com.amap.api:3dmap:9.6.0`。
地图页、地图插件和 npm 依赖相对 AR03 没有源码变化；这里记录的是本轮发现的 SDK 兼容性故障，不冒称已证明旧 APK 的同一复现。

[高德官方规避说明](https://lbs.amap.com/faq/android/navi-sdk/1000108216/1061016771)提供 App Manifest 的
`android:allowNativeHeapPointerTagging="false"` 方案。[Android 官方说明](https://source.android.com/docs/security/test/tagged-pointers)
明确它会关闭该 App 的指针标签检查，未修复底层库的问题，也不是面向未来 MTE 的长期解决方案。
2026-09-08 用户对两项具体请求回复“授权”：采用 App 级临时兼容配置，并执行下述最多五条真实提醒。`with-amap-key.js` 仅对启用地图的 App 增加以下配置，保持 targetSdk 36；后续升级 SDK 时须重新验证并评估移除此项。

```javascript
app.$ = app.$ || {}
app.$['android:allowNativeHeapPointerTagging'] = 'false'
```

`065efd85e` 已经过 CNG、重新构建和最终 APK Manifest 检查；同一候选完成正常返回 3 次、快速返回 1 次、前后台后返回 1 次，PID 保持、无新增 App 崩溃。底层 SDK 的指针问题仍未修复，未来升级时须重新评估兼容项。

**真实提醒授权已执行完毕。** OPPO 当前账号共创建 5 条一次性提醒，每条创建后 120 秒到期；标识为 `AR04-0908-对话/设置/地图/后台/锁屏验收`。五条均已触发并完成呈现回执，零待触发残留；没有取消、删除、完成或延后业务记录，也没有操作其他提醒。第五条实测是无安全锁屏的熄屏/唤醒，不能替代 Keyguard 验收。实际标题解析偏差和逐条凭据见第十二节。

**设备范围未补齐。** OPPO 的 `driving-landscape` 不可达性仍按 AR03 记录留给支持范围/对照机验证；没有操作 Xiaomi，也没有做物理折叠、系统 200% 字号、真实 S2S 发声或盲听。AR02/AR03 的旧剩余格仍归原批次。

## 11. 原始证据与接续

证据目录：`%LOCALAPPDATA%\car-agent\artifacts\AR04-20260908`。

- `jest-065efd8.json`、`checks-065efd8.json`：当前代码检查；`433fb3cf` 及之前的 SHA 各有独立历史文件。
- `mutations.json`、`batch-overlap-isolated.json`、`batch-regression-before.log`、`permission-regression-before.log`：反向与修复前证据。
- `build-device-fix.log` / `.err.log` / `build-device-fix-result.json`、`mirror-device-fix-manifest.json`、`apk-433fb3c.json`：`433fb3cf` 历史构建与验包；当前包见第十二节。
- `final-hf-settings-results.json`、`hf-settings-background-native.txt`、`final-story-results.json`、`final-draft-results.json`、`final-map-opaque.png`、`preferences-restored.json`：`433fb3cf` 的定向设备证据；当前包使用第十二节 `real-*` 文件。
- `permission-activity-evidence.log`、`crash-buffer-current.log`、`amap-compat-proposal.patch`：权限循环原始证据、地图崩溃与当时的兼容草案；配置已在 `065efd85e` 实施。

上述两项授权已执行完毕。接续先看第十四节当前状态，再核 SHA / APK / 设备。若要补真实 Keyguard 或物理折叠，先取得相应输入条件；新增提醒超出本轮最多五条的授权。不要把本页已取到的定向证据写成整批通过。


## 12. 前轮地图兼容与真实提醒验收（065efd85e，2026-09-08）

### 12.1 当轮包与资源

- 代码 / 测试锚：`065efd85ee6365160862b009dcaffc50e8b57473`；后续纯文档提交不改变 APK。
- 构建：16:47:07–17:11:20，Gradle **22m30s**、1222 tasks（736 executed / 486 from cache）、exit 0。沿原脚本 CNG 重新生成，没有手改生成 Manifest；`CompileJobs=1`、堆 128m/2048m、Kotlin in-process。
- 当轮 APK：`D:\Android\builds\apk\xiaozhou-companion-prod-release-065efd85e-20260908-1711.apk`，210,703,754 bytes；SHA-256 `7c8915b45430b32dd1a77b1f15028aa99873cd8f274da0e194b3900fb4afde7f`。
- 包内身份：`v0.1.0 · prod · 065efd85e · 2026-09-08 16:48`；OPPO PEUM00 / Android 14 于 **17:12:17** 安装。非 DEBUGGABLE，设备安装文件哈希相同；两种 ABI、内嵌 bundle、KWS / ORT、原签名均保留。
- 最终 APK Manifest：`allowNativeHeapPointerTagging=false`、`targetSdkVersion=36`；只在启用地图时由插件生成。无 key 不变、原属性保留、重复执行不重复 meta-data，均已检查。
- 地图瓦片正常显示；正常返回 3 次、快速返回 1 次、前后台后返回 1 次，均返回对话页，PID 7373 保持。安装后至末端复核无新增 App / pointer-tag 崩溃；这 5 个返回场景未用 force-stop 代替退出。
- 日志另有 FileSystemFileProvider replace 无原声明、高德库无法 strip、SDK XML、NODE_ENV、Gradle 弃用提示；构建成功和验包分别核实。
- 测试后已恢复 `handsFree=false`、`speakPolicy=auto`、`reduceMotionForce=false`；未修改系统锁屏、音量、密度、字号或旋转。App 已退出至 Launcher，自有 Gradle daemon 与 Maestro 流已结束，无 Metro 占用。

构建证据：`build-amap.log` / `.err.log` / `build-amap-result.json`、`generated-amap-manifest.json`、`apk-065efd8.json`、`amap-native-results.json`、`amap-final-crash.log`、`real-preferences-restored.json`、`real-resources-released.json`。

### 12.2 五条真实提醒与回执

均从 OPPO App 正常输入发送，账号 `u1 / primary`。后端实测 release 始终为 `a09c73a5da3181708279bc1f3e90acb1519606a0`；本轮未部署后端。每条在创建后 120 秒到期，经实际 Reminder 调度、proactive 投递、Android 呈现及 ACK。

| 场景 | reminder ID | delivery ID | 客户端收到→呈现 | 服务端末态 |
|---|---|---|---|---|
| 对话 | `a699423759ec46728350344f90905fa1` | `68e89ac66e9d4954aef2fafd61b9192b` | 97 ms | presented / 1 次投递 |
| 设置 | `5d1a2b5afe5142738c515a96afab189f` | `9dd0e552cbc6407b837c72447bc20ab3` | 114 ms | presented / 1 次投递 |
| 地图 | `e3cffc8406a64a95b80bd04a33f5f8c6` | `e0e30a2c89004c9bbbd3a38e336eff67` | 126 ms | presented / 1 次投递 |
| 后台 | `f9938841401e445d9366e412b5f9a98b` | `ec2b650e812e4a5bb9ce7032b4b4610b` | 54571 ms | presented / 1 次投递 |
| 熄屏（原计划锁屏） | `3e7142f118154dcfbf4eca72f798b06b` | `215aa59185764b85bd179f42f837077d` | 46780 ms | presented / 1 次投递 |

- 三个前台页都读到了实际提醒文字 / 卡片并留有截图。前三条保持自动播报，播放计数为 0；这是可视呈现验收，不冒称播报通过。
- 后台条在桌面隐藏期间、熄屏条在 `mWakefulness=Asleep` 期间均已收到；恢复之前服务端各两次查询保持 `dispatched / presented_at=0`。恢复后才呈现并 ACK，客户端等待分别 **54.571s / 46.780s**。
- 后两条临时设为“总是播报”。创建确认的播放先独立记录基线；恢复后提醒各增加 **1 starts / 1 stops**。再次后台/熄屏再恢复后，消息凭据、呈现/ACK 时刻和播放计数完全不变。最终总计 4 starts / 4 stops（2 次创建确认 + 2 次提醒），无 live / playing 残留。
- 全程 micStarts / micStops / ASR / S2S / 视觉上传均为 0。播放计数来自播放器事实；系统媒体音量未改，未做人耳盲听，也不借此关闭 AR03 的完整音频矩阵。
- 每个 delivery ID 只对应一条本地 message，列表记录与提醒出口可以同时显示同一内容，不是新增第二条投递。收起只更新本地 handledAt，没有点击业务“完成”或“稍后10分钟”。
- 末端数据库：**5 条 fired、0 条 pending、5 条 presented delivery，attempts 均为 1**。fired 是已触发，不能称作业务 done；保留原记录，没有删除或额外创建。
- 时间差只在同一客户端时钟内相减；服务端与手机时钟未做精密校准，不据跨机时间戳计算延迟。既有 ACK 帧不保存客户端来源，本轮报告设备 ACK 与对应服务端状态，不宣称额外的客户端来源审计。

证据：`real-reminder-summary.json`、各场景 `real-*-receipts.json`、`real-*-presented.png`、`real-background-before-resume.json`、`real-lock-before-wake.json`、两场景 baseline/reentry、`real-final-after-restore-server.json`。原始 JSON 保留精确时间戳和服务端 payload；仓库内只保留必要摘要。

### 12.3 当时未签收项与新增发现（后续处置见第十三节）

| 项目 | 当前判断 / 下一步 |
|---|---|
| 安全 Keyguard | **未验**。OPPO `secure=false / showing=false / inputRestricted=false`；第五条只能证明熄屏/唤醒。需要有实际锁屏界面的设备条件，不自动更改系统配置；不能再复用这五条已触发提醒冒充新试验 |
| 物理折叠 / 旋转与草稿、指定操作组合 | **未验**；已有本地布局/旋转回归和变异证据、前批跨页草稿证据，不替代该设备组合。OPPO driving-landscape 不可达及 Xiaomi 主用机边界仍归原记录 / AR10 |
| 创建提醒错域 | **仅记录、未修**。原句“`两分钟后提醒我AR04-0908-对话验收`”落联网搜索，返回 quick-reminders 等建议；只读数据库为零条。不是创建成功。改为明确“创建一条定时提醒…”后才创建上述五条；首句只有一次样本，不推导稳定复现率，provider/model 未独立提取 |
| 提醒标题污染 | **仅记录、未修**。五条实际 title 都保留“创建一条定时提醒，提醒我，提醒内容是”前缀；本轮没有通过改库或重命名掩盖。按 reminder ID 核对本次五条范围 |
| 对话页播报胶囊文案 | **仅记录、未修**。`real-lock-presented.png` 仍显示“播报中 · 说话可打断”，同轮麦克风计数为 0；跨页采集栏已准确，但对话页这段提示仍需按真实可打断条件收敛。归入后续交互文案 / 整体验证，不把无采集写成已能语音打断 |

本轮额外的 SSH 连接诊断曾触发腾讯云扫码认证并终止，未绕过；常规具名提醒台账查询随后正常完成，不再依赖该额外诊断。所有台账查询使用只读事务；没有执行 schema / 环境 / 系统配置 / 生产部署变更。

结论为 **AR04 开发修复及本轮授权的定向验证完成，完整验收矩阵未签收**。本页的新发现保持“仅记录”，AR02/AR03 的原剩余格、AR05–AR11 的范围不因此关闭或自动启动。


## 13. 前期未签收项续接（2026-09-08；发布结果见第十四节）

用户要求继续未签收项，并提供 OPPO 物理折叠配合；对“临时把小舟随行系统显示方式改为全屏并恢复”的具体请求回复“允许”。该回复用于显示模式切换；云端 apply 与新增一条提醒的请求仍待单独明确批准。

### 13.1 修复、检查与当前 APK

| 项目 | 当前证据 |
|---|---|
| 播放提示 | `a731973` 将新胶囊与旧版回滚栏统一为“播报中”，不凭播放事实承诺可直接语音打断。OPPO 第二次定向采样已抓到新文案；播放器 starts/stops=2/2、mic/ASR/S2S/视觉计数为 0。首个样本采样时已播完，未计通过；实际播的是故事请求的回复，未把它称为故事内容验收 |
| 提醒标题 | `573ad46` 在既有标题提取路径剥离句首、有分隔符的显式创建包装及随后的标题字段；保留真实任务内容与 QA 标记。未修实现先出现 4 failed / 2 passed；独立内存重放也证实三种槽位输入都会污染。修后提醒服务 **220 passed**；未改库中五条旧标题 |
| 本地 / CI | 当前完整 SHA `573ad46d939bf655f5a0f4ef16579e2a9b80b087`：mobile **71 suites / 726 tests，86.726s**、tsc、两处源文件定向 lint 全部 exit 0；四门禁 exit 0（范例检索仍有门禁允许的 3 条错配，不冒称逐条全绿）。[CI 34214476735](https://github.com/SuperdeMan/cockpit-agent/actions/runs/34214476735) 八个任务全部 success，含 Python 3.11/3.12 |
| 构建 | 核对 345 个 mobile/HMI 文件与 19 个原生资产，4 个差异仅为 mobile README、两处提示源文件与现有测试。原生配置不变，按共享指南从 Gradle §5–§6 接续，单次 daemon；18:15:26–18:24:30，Gradle **8m57s**，52 executed / 1170 up-to-date，exit 0 |
| 当前 APK | `D:\Android\builds\apk\xiaozhou-companion-prod-release-573ad46d9-20260908-1824.apk`，210,703,738 bytes；SHA-256 `2e14feb02f23dc58dc291e1899fa64a26fd25d0956c3ed52c5fd0dab93148573`；包内 `prod · 573ad46d9 · 2026-09-08 18:15` |
| 安装 | OPPO PEUM00 / Android 14 于 **18:26:17** 原地安装，设备 APK 哈希相同、非 DEBUGGABLE；原签名、双 ABI、KWS/ORT、内嵌 bundle、targetSdk 36 与地图 pointer-tag 兼容项均保留 |

本节证据目录：`%LOCALAPPDATA%\car-agent\artifacts\AR04-followup-20260908`。主要文件：`mobile-checks.json`、`backend-checks.json`、`title-before.json`、`ci-573ad46.json`、`build-followup-result.json` / `.log`、`mirror-followup-manifest.json`、`apk-573ad46.json`、`caption-native-results.json`、`caption-verified.png`、`caption-verified-counters.json`。

### 13.2 物理折叠与窗口范围

测试内容是**未发送草稿＋本地“使用当前位置”征询卡**；没有点击允许/拒绝，没有把本地 `__location__` 说成服务端 operationId。多项服务端确认保留的合同仍由当前本地集成测试覆盖，本次物理样本不补造那条业务证据。

| 稳定保持点 | 实测与保留结果 |
|---|---|
| 默认兼容模式，完全展开 | 真实 base/committed=3，未使用 override；ColorOS `always-compat / oplus-magic-windows`，应用 bounds `[357,0][1436,1920]`，约 392dp 宽。PID 22627、原草稿与定位征询保持 |
| 默认兼容模式，半折 | base/committed=2，姿态前后复核一致；相同 PID、草稿与征询保持；默认窄窗不代表进入宽屏 UI |
| 默认兼容模式，合拢横屏 | base/committed=0、orientation=3、active viewport 1972×988，App bounds `[0,0][1972,988]`；草稿和征询/按钮都可见，PID 不变 |
| 经授权切换全屏 | 左上系统入口明确提示“切换为全屏显示需要重启”；执行应用重启，PID 22627→1200。这是系统显示设置引起的重启，旧测试内容已保存，重新建立新草稿/征询后才取下一组样本 |
| 全屏，完全展开 | App bounds `[0,0][1792,1920]`、652×698dp；原生 `flat / horizontal / isSeparating=false`，实际布局 `drawer · medium×medium`；新草稿与征询保持 |
| 全屏，半折 | base/committed=2，原生 `halfOpened / horizontal / isSeparating=true`，实际进入 **`tabletop · medium×medium`**；草稿与征询/按钮保持，PID 1200 不变 |
| 全屏，合拢返回（回连后补验） | 真实 CLOSED、外屏 988×1972；回连后确认仍为 PID 1200，原全屏测试草稿和定位征询保留。首个采样被 USB 用途弹窗遮挡，剔除后重新取稳定样本 |
| 全屏，书本式 | base/committed=2、orientation=0、窗口 1920×1792；原生 `book / halfOpened / vertical / isSeparating=true`，实际进入 **`two-pane · medium×medium`**，698×652dp；左右双栏中草稿与征询/按钮保持，PID 1200 不变 |

稳定样本使用硬件状态和 active input viewport 的**采样前后相等**检查，并保存 XML/截图。早期自动观察中旋转和展开接得很快，两张截图相同，不能按各自前置状态分别签收；正式结果取 `fold-inner-stable-0.json`、`fold-half-stable-0.json`、`fold-closed-stable-0.json`、`full-open-stable-0.json`、`full-half-stable-0.json`。原生模块证明见 `fullscreen-open-native.xml` / `fullscreen-half-native.xml`；新挂载诊断页 `events=0`，同时 `current` 缓存有有效姿态，不把零新事件计数判为模块缺失。

全屏书本式和合拢返回已在回连后补齐，证据为 `full-book-stable-0.json`、`fullscreen-book-native.json`、`full-closed-after-usb-dismiss-0.json`。本轮关闭的是上述固定包、机型与本地征询/草稿的保留格；服务端多 operationId 的实机组合仍未补造。默认系统兼容窄窗与真实宽屏布局分开记录；`driving-landscape` 尺寸门槛、Xiaomi 对照及 AR02/AR03/AR10 其余矩阵不由本轮 two-pane/tabletop 代替。

### 13.3 锁屏、后端定位与发布边界

- 本轮 18:28 实际看到了 OPPO Keyguard：`showing=true / secure=false`，有锁屏界面，可正常上滑解锁。AR04 的目标是锁屏遮挡时不提前呈现/ACK，**不要求为了测试新设 PIN**。前轮 `showing=false` 的熄屏证据仍不代替本次真正锁屏；新增一条 `AR04-0908-锁屏复验`、两分钟到期的请求已列明，尚未获准创建。Xiaomi 尚未接入/操作。
- 原错路由已取到原始 trace **`21798d30258aa5bf`**：实际 `minimax:MiniMax-M3` 首次 submit_plan 为 `steps=["[]</steps>"]`，结构校验失败；重试返回 `{"addressed":true,"steps":[]}`，随后 `toolcall_degraded → chitchat.talk → needs_realtime → info.search`。当前只完成准确归因，尚未修复 Planner 技术失败降级策略。
- 成功创建 trace **`458440c431c99c8f`** 的 Planner 原本就给了干净 title `AR04-0908-对话验收`；污染发生在 Agent `_fuller_title` 回填原话时。本次标题修复针对这个已证实的本地路径。
- 云端仍为 **`a09c73a5da3181708279bc1f3e90acb1519606a0`**，本轮**未 apply**。目标 `573ad46d939bf655f5a0f4ef16579e2a9b80b087` 的 dry-run 为 ready / blocking_changes=[]，结果在 `deploy-dry-run.json`，源包在仓库 `.artifacts/releases/<完整SHA>/`。
- 发布该目标还会包含当前 release 之后的 HMI `ws.mjs` 队列撤回/发送回执、`voiceLoop.stopSpeaking` 方法以及 MiniMax 已到齐文本合并修复。相关 **HMI 75 tests / TTS pacing 21 tests** 通过，且上述完整 CI 已绿。已向用户说明具体范围并请求单独发布及一条提醒授权；不能把显示设置的“允许”转借给生产发布。

### 13.4 当时断线交接（19:26；已恢复，见 13.5）

19:26 当时 `adb devices -l` 为空，故停止设备操作并保留恢复事项；用户后续重连后已按 13.5 完成。以下保留断线时交接原貌。

- 断线时设备仍为临时**全屏显示**，当时尚未恢复原兼容模式。系统提示给出的恢复入口：系统服务“已切换为全屏显示”通知里的“恢复”，或 **设置 → 大屏专区 → 兼容模式 → 小舟随行**；原始窗口为 16:9 兼容窄窗。只恢复该 App，系统若重启 App 则重新核包与界面。
- 断线时 App 暂存偏好：`locationEnabled=false`、`reduceMotionForce=true`、`speakPolicy=auto`、`handsFree=false`。结束后将前两项恢复为 **true / false**；后两项维持原值。不点本地征询的允许/拒绝，不发送测试草稿。
- 当时全屏样本 PID 是 1200，断线时后续存活状态未知（回连后已确认仍为 1200）；新样本草稿为 `AR04 fullscreen draft 573ad46 - retain across folding`。设备回连先核实际状态，不能沿用 PID 或假定还在原页面。
- 自有 Gradle、Maestro 均已结束；10 分钟只读折叠观察器已到期结束，无后台采集进程留占。当前进展和脚本入口同时保存在 `followup-progress.json`。

当时约定：回连后补稳定姿态与采集终态，再恢复显示及 App 偏好；该部分现已按 13.5 完成。云端发布/新增提醒仍等待对应授权，AR04 未整批签收。


### 13.5 回连补验与恢复完成（2026-09-08）

- 回连核包：设备仍安装 `573ad46d9`，安装文件哈希与当前 APK 一致。Keyguard 为 showing=true / secure=false，正常上滑解锁；出现的 USB 用途弹窗仅关闭，没有切换 USB 模式。
- 合拢返回与书本式稳定样本均复核采样前后硬件状态/active viewport 一致、无 override；原未发送草稿和本地定位征询保留，PID 1200 不变。书本式实际为 two-pane，已目视检查草稿、征询与按钮在左侧，舞台在右侧。首个被 USB 弹窗遮挡的样本不计通过。
- 全屏折叠结束时 `final-fold-counters.json`：mic/ASR/S2S/视觉上传全部为 0，播放 starts/stops=0/0；这是应用因授权的显示切换重启后新进程的本组读数，不与前一个进程的 2/2 混算。
- App 偏好已恢复并回读：locationEnabled=true、reduceMotionForce=false、handsFree=false；speakPolicy=auto 保持原值。证据 `app-preferences-restored-final.json` 及对应 XML。
- 系统通知中的显示恢复入口已不在列表，实际通过 **系统设置→大屏专区→兼容模式→小舟随行** 操作。先确认只有小舟随行显示“全屏使用”，选择原 **16:9**，系统提示“切换显示比例，此应用会被关闭”，确认后列表回读为 16:9。没有勾选“不再提醒”，未更改其他 App 比例。
- **显示比例于 22:15:57 恢复并回读成功**，证据 `system-display-restored-final.json`、`compat-restored-list.xml/.png`。恢复后只读诊断确认包仍为 573ad46d9，采集/播放/视觉均无活动、投递列表为空；最后退出 App 至 Launcher，确认无 App 进程。系统关闭应用使本次未发送草稿和本地征询结束，没有点击发送、允许或拒绝。
- 末端 cloud status 仍为 `a09c73a5da3181708279bc1f3e90acb1519606a0`、5/5 healthy、warnings=[]。本轮未 apply、未新增真实提醒。生产发布/一条真正锁屏提醒的授权仍待答复，不能以物理操作回复替代批准。

恢复总记录：`reconnect-closeout.json`；当前机器可正常使用，**不再有本轮显示或 App 偏好待恢复项**。下一步只按仍未闭合的业务/锁屏矩阵及发布权限继续，不重做已通过的普通折叠保留。


## 14. 已授权发布与真实 Keyguard 复验（2026-09-09，当前入口）

用户对发布 `573ad46`（提醒标题修复及此前 HMI/语音合并修复）和新增一条两分钟锁屏提醒明确回复“授权”。本轮只创建下述一条，没有复用前轮五条提醒的额度。

### 14.1 生产发布与独立验证

| 项目 | 精确证据 |
|---|---|
| 目标 / 发布前 | 目标 `573ad46d939bf655f5a0f4ef16579e2a9b80b087`；发布前 `a09c73a5da3181708279bc1f3e90acb1519606a0`，工作树 clean，目标 main 可达 |
| 代码验证 | [CI 34214476735](https://github.com/SuperdeMan/cockpit-agent/actions/runs/34214476735) 对同一完整 SHA 八任务 success，含 Python 3.11/3.12；上轮 mobile 726、reminder 220、HMI 75、TTS pacing 21 与四门禁的精确 SHA 不变。本轮未另跑本机全量，不借用旧 release 的 7861 条结果 |
| dry-run / apply | 重新 dry-run：原 release 与已审阅差异一致，blocking_changes=[]、release lock available；随后执行已授权 `--apply`，命令 exit 0 / submitted。submitted 仅表示提交，最终状态以下两项独立核实 |
| status | 独立读回新 release；验证前后均 5/5 endpoint healthy，warnings=[] |
| verify | `verified`，exit 0；`.artifacts/dev-stack-verifications/20260909T015134Z-573ad46.json`，release SHA 完整一致、case `e2e_remote_safe`、`minimax:MiniMax-M3` |
| 范围 | 发布包含提醒标题修复、HMI 指定队列撤回/发送回执与 stopSpeaking 接口、MiniMax 已到齐文本合并修复；未额外编辑环境/密钥/基础设施/schema，也未执行数据清理或回滚 |

本轮证据目录：`%LOCALAPPDATA%\car-agent\artifacts\AR04-release-20260909`。部署为 `dry-run.json`、`apply.json`、`apply-command-result.json`、`status-deploy-progress.json`、`status-final.json`、`verify.json`、`verify-command-result.json`；原 source/transport 包仍在仓库 `.artifacts/releases/<目标完整SHA>/`。部分 PowerShell 重定向 JSON 是 UTF-16，以 BOM 判断读取，不能把空解析结果当成功。

### 14.2 唯一真实提醒与锁屏合同

| 字段 | 当前值 |
|---|---|
| 账号 / 创建 | OPPO 当前 App 账号 `u1 / primary`；正常 App 输入发送一次 |
| 原话 / 标题 | “创建一条定时提醒，2分钟后提醒我，提醒内容是AR04-0909-锁屏复验。”；数据库 title 精确为 **`AR04-0909-锁屏复验`**，没有创建指令前缀 |
| reminder ID | `82c09ae84f36456bb42a972b3f85f263` |
| delivery ID | `3cf096f8d48f42d895feb0f2ca3d317f` |
| 创建 trace | `510339013295c9a7`，`reminder.create`、`toolcall`，实际 `minimax:MiniMax-M3` |
| 计划 / 实际触发 | created_at=1788919223、fire_at=1788919343（相差 120 秒）；fired_at=1788919346，实际调度比计划晚 3 秒；不是时延统计样本 |
| 客户端时间 | receivedAt=1788919347259；presentedAt=1788919541603；ackSentAt=1788919541604 |
| 服务端结果 | presented_at=1788919542903；state=presented，attempts=1；收起后业务记录仍是 fired，不称作 done |

步骤与结论：

1. **先预检，再消耗唯一提醒样本**。准备中文草稿并结束输入工具后，使用正常电源键事件 `POWER(26)` 锁屏、`WAKEUP(224)` 亮屏，读回 Keyguard `showing=true / enabled=true / secure=false`，解锁后草稿仍在；这一步尚未发送创建请求。
2. 创建结果核对 title 和 120 秒计划时间后，记录创建确认的播放基线 1 starts / 1 stops，再进入真实 Keyguard 锁屏。到期后第一次查账为 `dispatched / presented_at=0`。
3. **亮屏后仍不解锁**：XML 与截图证实前台是 Keyguard，App 不可见，查账仍是 dispatched；解锁前第三次查账也没有提前销账。使用的是实际锁屏界面，不再把前轮 `showing=false` 的熄屏当锁屏。
4. 正常上滑解锁后，真实提醒文字/卡片进入可见出口，客户端发 ACK，服务端才变为 presented。客户端收到→呈现 **194344 ms** 是人为保持锁屏的等待，不是 App 性能延迟；没有用手机与服务端的不同钟源相减计算时延。
5. 解锁后只新增 **1 starts / 1 stops**；本地收起后再次后台→前台，message/delivery、呈现和 ACK 时间及播放计数全部不变。最终总播放 2/2（一次创建确认、一次到期提醒）；mic、ASR、S2S、视觉上传均为 0。截图也已抓到“播报中”。播放器事实不替代扬声器盲听。
6. 只点击本地“收起”记录 handledAt，没有点业务“完成”或“稍后10分钟”。末端本轮 **1 条 fired、0 条 pending、1 条 presented delivery**，无待触发残留，无需取消；未删除记录，也未触碰前轮五条或其他提醒。

主要证据：`keyguard-preflight.json/.txt/.png`、`locked-before-due-system.txt`、`locked-audit-1.json`、`awake-keyguard-ui.xml/.png`、`awake-keyguard-audit.json`、`before-unlock.json`、`reminder-presented.png`、`before-lock-counters.json`、`after-presentation-counters.json`、`after-reentry-counters.json`、`creation-trace.json`、`lockscreen-summary.json`、`final-reminder-audit.json`。ACK 帧本身不记录客户端来源，本轮报告设备发送事实和对应服务端账本结果，不宣称新增了来源审计能力。

### 14.3 恢复与剩余范围

本轮仅临时改 App 的 speakPolicy=always 和 reduceMotionForce=true，用于验证播放/取证；已经恢复 **auto / false**，handsFree 始终 false。App 已退出至 Launcher、进程不存在；没有本轮构建或输入驱动留占。恢复文件 `real-preferences-restored.json`、`real-speak-auto-restored.png`、`device-released.json`；前轮已恢复的 16:9 系统显示比例未被本轮修改。

**本轮授权的发布与一条锁屏复验已完成；标题污染和真实 Keyguard 提醒格已关闭。** 剩余 AR04 签收缺口是服务端多 operationId 的实机组合，不能用本地定位征询替代；Planner 非法/空计划后的技术失败降级策略仍未修。AR02/AR03/AR10 的既有矩阵、长文本配额/盲听与设备支持范围保持独立，不能把本轮短提醒通过写成 QA 全绿。

## 15. 跨页宿主形态修正：常驻双栏改为浮动在场（2026-09-09，当前入口）

用户于 2026-09-09 评审第十四节的结果，判定「页面底部加了两栏破坏了整个用户页面的设计」并要求优化；本轮 commit/push 已授权。

### 15.1 评审结论

问题落点是 `AssistantSurface`：设置 / 车辆 / 地图页底部常驻两行——状态行「小舟在这里 · 麦克风关闭」+ 按钮行「展开回答 / 说话」，播报或在飞时再追加「停止播报 / 取消请求 / 停止收音」——外加顶边线，闲置约占 175dp 布局高度；地图页在自己的信息条之下再叠这两行，提醒到达时再叠第三块（`route-settings.png` / `route-map.png` / `real-map-presented.png`）。

这不是「样式不好看」，而是与项目已经写进代码和方案的三条判据相悖：

1. 胶囊「一次只说一件『此刻』的事」（方案 §4.3）与采集点「没在采集就不渲染」（ChatScreen 注释）：闲置时这两行没有任何此刻的事实——「麦克风关闭」是缺省态，「展开回答」在没有回答时是空入口，正是评审 R05 点名的「空入口」形态。
2. 「助手是层」（方案 §5.2）：助手的身份锚是光球。对话页用光球 + 胶囊 + Composer 表达全部在场状态，支持页却换成一组纯文字方块按钮，同一个助手在两类页面上是两种物种。
3. 「先保证内容空间，再分配装饰面积」（完整评审「视觉与交互本身的判断」）：两行是固定高度的 chrome，把设置滚动区、地图视口各切掉一段，换来的信息量为零。

AR04 §3/§4 真正要求的是四件事**可达**：采集事实可见、一步停播、回答 / 语音层入口、待办与提醒不被遮挡。它们没有一件要求常驻栏；第十四节已取得的呈现 / ACK / 折叠 / 锁屏证据都不依赖这两行。

### 15.2 目标形态

支持页的助手是**浮动在场**：闲置零布局占用，只在有事时长出内容；判据全部复用对话页那一份（`derivePresence` / `canStopPlayback` / `orbPolicy`），不新增状态机。

| 元素 | 何时出现 | 点按 | 复用 |
|---|---|---|---|
| 光球（右下角；泊车 48 / 行车 56 热区，实色圆底 + 玻璃边框） | 支持页、前台可采集、配置了语音、语音层未升起、键盘未弹出（有事时键盘弹出也保留） | 同 Composer 光球 `onOrbTap`：闲 / 待唤醒→说话；播报中→停后说；思考中→展开层 | `AuroraOrb`、`ORB_A11Y`、`orbTempo` |
| 状态胶囊（光球左侧） | `snapshot.capsule` 存在时（在听 / 识别中 / 播报中 / 正在思考 / 等你确认 / 已断开 / 说「小舟小舟」…） | 展开语音层；建议胶囊 = 开行车档 | `PresenceCapsule` 原组件，加 `solid` 实色底 |
| 动作键（琥珀胶囊） | `stoppable` →「停止播报」（只停声、不开麦，AR03）；否则 `busy` →「打断」 | `onStopPlayback` / `onInterrupt` | Composer 合一键前两态 |
| 采集点（8dp，琥珀 / 青） | `privacy` 物理事实任一为真（麦开 / 上传 / 摄像头 / 画面上传） | 打开隐私栏（含「结束本轮收音 / 关闭免唤醒」） | 与对话页顶栏 `capture-dot` 同一份 `captureSummary` |
| 承诺面 FocusDock | 有待确认 / 长任务 / 队列 / 降级时 | 原样 | 占真实布局空间（G0），仍在语音层覆盖域之外 |
| 提醒出口 | 有未处理的真实投递时 | 原样（呈现测量、ACK、收起） | 从通栏改为 G0 卡片，与 Dock 同住一个宿主，共用一次底部安全区 |

其余约束：地图页信息条经 `ui/layout/bottomChrome.ts` 按路由上报自身高度，浮动光球落在它上方，不压「全览 / 收起详情」；压在地图瓦片上的浮层一律实色底（map.tsx 既有判据）。语音层升起时浮动元素不渲染，层内大球 / 胶囊 / 停止键接管。对话页不再渲染任何全局条：v2 的采集点、胶囊、合一键已经覆盖同一组出口。承诺面与提醒宿主没有内容时整个不渲染，不留空安全区。

### 15.3 实现与验证

代码锚 `de2a5564caf6641ed5fc598e5d84ee7fbdeabf26`（已推送 main）。后续纯文档提交不改变这个 APK / 测试锚。

| 代码面 | 实际落点 |
|---|---|
| 浮动在场 | `AssistantSurface.tsx::AssistantPresence`，住在 `CrossPageVoiceLayer` 的覆盖域里、排在语音层之前；`position: absolute`、右下角，`bottom = 12 + 安全区 + 本路由上报的底部浮层高度`。光球 / 胶囊 / 动作键 / 采集点分别按 `cfg.audioUrl`、`snapshot.capsule`、`stoppable ?? busy`、`captureSummary` 挂载 |
| 占布局空间的宿主 | `AssistantSurface` 只在 `pickProactiveMessage` 或 `focusDockVisible` 为真时渲染一个带底部安全区的容器；提醒卡改 G0 实色卡（与承诺卡同材质）。对话页的 Dock 仍在 ChatBody |
| 唯一判据 | `presence.ts::captureSummary`（对话页顶栏采集点改为消费它）、`FocusDock.tsx::focusDockVisible`、`ProactivePresenter.tsx::pickProactiveMessage` |
| 地图避让 | `ui/layout/bottomChrome.ts` 按路由登记；`map.tsx` 信息条 `onLayout` 上报「高度 + 12」、离开路由清零 |
| 实色底 | `PresenceCapsule` 新增 `solid`，浮动态取 `p.panel`；动作键与光球圆底同色，玻璃只保留边框与投影 |
| 撤掉的东西 | 状态行、「展开回答 / 说话 / 停止收音 / 取消请求」按钮行、顶边线、对话页采集时的全局条、`_layout.tsx` 里单独挂的 `ProactivePresenter` |

本地验证（固定命令 `node node_modules/jest/bin/jest.js --runInBand --silent` 与 `node node_modules/typescript/bin/tsc --noEmit`）：

- mobile **72 suites / 737 tests，77.59s，exit 0**（改前 71 / 726；新增 `test/assistantPresence.test.ts` 11 条，`landscapeDockReach.test.ts` 改为与 `_layout.tsx` 同一棵树后 7/7 仍过）；tsc exit 0。
- 新用例锁的是「何时渲染什么」：支持页闲置只有光球；对话页 / `/debug` / `/onboarding` 无浮动在场也无宿主；待确认时宿主占空间且可直接确认、台账清空后宿主撤走；播报中动作键「停止播报」按到 `SpeechController.stop` 且不增加麦租约；在飞未出声动作键「打断」发出 cancel 帧并标记已打断；采集中采集点可开隐私栏；层升起时浮动在场让位；思考中轻点光球展开层；键盘弹出时闲置隐藏、有声音保留；地图上报 108dp 后 `bottom` 由 12 变 120 且不带到设置页；提醒出口与宿主同生同灭。
- 反向证据：三处变异各自只红对应用例——层升起不让位（`if (false)`）、对话页也渲染（`support = !!runtime`）、宿主没内容也渲染（`if (false)`）；每次都从备份按字节恢复（`cmp` 一致）。
- 显式 Expo flat 配置下的定向 lint：本批新写 / 改动的 8 个源文件 0 error；仅有的 2 个 error 是 `map.tsx` 既有的高德初始化 ref 守卫（HEAD 上第 59 行，本批未触碰），不是本批引入。测试文件沿用既有 `landscapeDockReach.test.ts` 的 `Observe` 写法，同样带 `react-hooks/globals` 提示；仓库仍无生效的 `eslint.config.*`，配置治理归 AR06。

### 15.4 OPPO 设备验证（PEUM00 / Android 14，2026-09-09）

证据目录 `%LOCALAPPDATA%\car-agent\artifacts\AR04-presence-20260909`（首轮包）与 `…-b2`（复验包）。

| 项目 | 精确证据 |
|---|---|
| 首轮包 | `de2a5564c` prod release：11:13:33–11:43:22，Gradle **27m19s**、1222 tasks（731 executed / 491 from cache）、exit 0；`CompileJobs=1`、堆 128m/2048m、Kotlin in-process；签名 SHA-1 仍为 `5e8f16062ea3cd2c4a0d547876baa6f38cabf625`；`assets/app.config` variant=prod build=de2a5564c mapEnabled=True。APK `D:\Android\builds\apk\xiaozhou-companion-prod-release-de2a5564c-20260909-1143.apk`，210,705,742 bytes，SHA-256 `fde4c0d324f4fc9fafb196be89c73b762022f27b425702e7f397368e250e949e`；11:44:17 `install -r`，非 DEBUGGABLE，设备 `base.apk` 哈希相同；设置页底行 `v0.1.0 · prod · de2a5564c · 2026-09-09 11:14` |
| 闲置形态 | `idle-settings.png` / `idle-vehicle.png` / `idle-map.png`：三页底部不再有任何栏，只剩右下角一颗光球；地图页光球落在信息条上方，「全览」按钮不被压（`bottomChrome` 上报生效） |
| 思考中 | 对话页打字发送「Tell a long story in Chinese.」（11:52:18.3）后点顶栏设置入口：`story-settings-t2.png`（+3.2s）光球 + 胶囊「正在思考…」+ 琥珀「打断」键，浮在设置内容上、零占位 |
| 播报中 | `story-settings-t8.png` / `-t16.png`（+9.7s / +18.3s）：胶囊「播报中」+「停止播报」键 + 光球 |
| 一步停播 | 点「停止播报」后 1.2s：`story-settings-stopped.png` 只剩闲置光球，同帧 dump 里「播报中 / 停止播报 / 打断」均消失；`capture-status-after-stop.png`：`playing=false, live=false, starts=1, stops=1`，mic / ASR / S2S / 视觉上传全 0——只停了声音，没有开麦 |
| 临时设置与恢复 | 为取播报态把「播报」auto→always、为让 uiautomator 能 idle 把「减少动效（强制）」off→on；结束已恢复 **auto / off**（`speak-policy-auto-restored.png`、`reduce-motion-final2.png`）。**一次误操作如实记录**：11:59 恢复减少动效时沿用「标签之后第一枚开关」配对，但标签不在当前屏上、脚本仍取了屏上第一枚开关，把「能力开关 · 车辆控制」从开切成了关（`reduce-motion-final.png`）；12:05 按标签重新定位切回开（`vehicle-control-restored.png`，13 枚能力开关全开）。规则补一条：标签没在屏上就不许配对开关 |
| 收尾 | HOME + `force-stop`，进程不存在；系统设置未动 |

首轮取证暴露两条形态问题，修在 `1c6780744be22151b04ef606eca961bbad060c26`（本地 72 suites / 739 tests、tsc 通过；`orbPolicy.test.ts` 新增两条）：

1. 设置 / 车辆页滚到底时光球压住最后一行开关 / 链接的右半边（`idle-settings.png` 的「保持屏幕常亮」开关、`settings-scroll-2.png` 的「材质 spike」链接）→ 两页滚动内容 `paddingBottom += PRESENCE_LANE_DP`（72dp），最后一行可以滚到光球上方；中途行仍会被 FAB 短暂遮住，这是 FAB 惯例，不是缺陷。
2. 闲置光球常驻呼吸动画让支持页 uiautomator 永不 idle（`ERROR: could not get idle state`，只能先开「减少动效（强制）」才能 dump）→ `orbPolicy.presenceOrbTempo`：idle / armed / muted 静帧，听 / 想 / 说 / 等确认 / 看一眼才动。FAB 不是主角，每个支持页常驻一份循环动画既没信息也吃 G5 预算。

**复验包 `1c6780744`（OPPO，2026-09-09）**：12:10:18–12:33:03，Gradle **21m9s**、1222 tasks（731 executed / 491 from cache）、exit 0，参数与签名同首轮；`assets/app.config` build=1c6780744。APK `D:\Android\builds\apk\xiaozhou-companion-prod-release-1c6780744-20260909-1233.apk`，210,706,038 bytes，SHA-256 `0ebcee33d2f1a279278982494c5c8474f58c79d54c379afb73885a2246169cfd`；12:33:45 原地安装，非 DEBUGGABLE，设备 `base.apk` 哈希相同。

- 余量：`b2-vehicle-bottom.png` 最后一行「雨刷」与页脚「车况镜像 · 与座舱实时同步」都在光球上方；`b2-settings-bottom-2.png` 滚到底后构建行 `v0.1.0 · prod · 1c6780744 · 2026-09-09 12:11` 的 bounds y=1671–1735、光球 `[823,1763][955,1895]`，零重叠（同一帧 dump 读数）。
- 静帧：闲置的设置 / 车辆页 `uiautomator dump` 直接成功，不再需要先开「减少动效（强制）」；地图页 `b2-map.png` 光球仍在信息条上方。
- 本轮没有改任何 App 设置；HOME + `force-stop`，进程不存在。未在复验包上重做播报 / 停播态——判据未变，那组证据仍绑定首轮包 `de2a556`。

**状态：支持页常驻两栏已撤；浮动在场在 OPPO 两个包上取到闲置 / 思考 / 播报 / 一步停播 / 余量 / 静帧证据。** Xiaomi 对照未做；对话页提醒出口与记录同时呈现同一条内容的形态未改（第十二节既有裁决）；AR04 其余未签收项（服务端多 operationId 实机组合、Planner 技术失败降级）不变。
