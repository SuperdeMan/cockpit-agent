# AR04：跨页语音宿主与提醒呈现 ACK

日期：2026-09-08。状态：**用户已确认推荐方案；客户端实现与本地验证进行中，尚未构建或安装本批候选包。**

输入：[分批建议 AR04](2026-09-07-android-review-remediation-batches.md#ar04)、
[完整评审 R08/R09](../reviews/2026-09-07-android-ux-full-review.md)、
[AR03 第八、十节](2026-09-08-ar03-stop-playback-landscape-implementation.md)。
本页是 AR04 的具体实施方案；AR01–AR03 原有设备证据仍绑定各自代码和 APK。

## 1. 本轮接手基线

| 项目 | 本轮实际核查 |
|---|---|
| 工作树 | `main`，改前 clean，`HEAD == origin/main` |
| 改前源码 | `cd5c19b5e1d67fcf07713ced9376e2612d6ec999` |
| 真栈目标 | 根 `dev-stack.local` 与 `python scripts/dev_stack.py target show` 均为 `cloud` |
| Android 环境 | `scripts/check_android_env.ps1`：18 pass / 0 warn / 0 fail，exit 0 |
| Mobile 改前测试 | `npm test`：70 suites / 695 tests，32.839s，exit 0；实际为 Jest 默认 worker 模式 |
| Mobile 改前类型 | `npm run typecheck`：exit 0 |
| APK / 云端 | 本轮尚未构建、安装或部署；不把文档中的历史设备包写成本轮设备实测 |

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
