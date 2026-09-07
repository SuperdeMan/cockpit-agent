**Android 规划与 UX 升级完整评审｜2026-09-07**

> 状态：评审已归档；R01–R15 均未因本次文档提交而修复。评审代码基线为 f140f979654d1727406c0da4c9d72d42ada970b7。
> 后续处理统一看 [分批处理建议与状态表](../design/2026-09-07-android-review-remediation-batches.md)，保留本文原始发现与证据，不在本文滚动改写历史通过数。

**评审结论：技术路线通过；UX v2 的完整交付验收暂不通过；Android 对外生产化仍需独立的 M5 交付计划。** 当前适合继续内部验证。下一步应先修确认、取消、采集和播放控制的完整流程，再补体验证据。继续增加视觉效果或系统入口，不能解决这些已确认的问题。

本轮审查了 Android 选型方案、M0–M4 任务与验收记录、UX v2.2 总方案、B1–B5 分批计划及收口记录、B1 既有评审、09-05 语音批的最终修复记录，以及对应客户端代码、共享 WS/确认/路由模块、多端契约、构建与 CI 入口。打开检查了 B5 小样本的 s1–s7 共七张截图。重点检查用户完整流程及组合状态，未重新执行商户操作、真实车控或手机采集。

| 证据面 | 本轮确认的状态 |
|---|---|
| 代码评审基线 | HEAD = 本地 origin/main = f140f979654d1727406c0da4c9d72d42ada970b7，只读评审阶段开工及收尾工作树干净；归档前另行 git fetch，HEAD 与 origin/main 仍相同 |
| Android 常驻包对应代码 | 文档记录的构建为 ce123957843d242f4b1c4366d85fa3ba754c5dab；本轮 git diff 确认 ce12395..HEAD 的 mobile 变化只有 README |
| 设备安装证据 | 09-07 README 记录 OPPO 与 Xiaomi 已装 ce1239578；本轮未重新读设备设置页，安装状态按文档引用 |
| 云端状态 | 本轮 status：a09c73a5da3181708279bc1f3e90acb1519606a0，5/5 endpoints healthy，warnings=[] |
| 本地 Jest | 55 suites / 550 tests 全通过。首轮 45.075s 有 worker 未正常退出告警；针对该告警串行加 detectOpenHandles 复查，550/550，64.318s，exit 0，没有报告具体未释放句柄 |
| TypeScript | npm run typecheck 无错误 |
| Lint | 仓库缺少 ESLint 配置。npm run lint 自动生成 Expo 默认配置后失败：src 中 63 errors / 43 warnings。不能将其等同于 63 个运行时故障，但既定 lint 门禁确实未闭合 |
| 独立缺陷复现 | 调用真实 SessionCore、ResilientWebSocket、derivePresence，以内存通道验证了五个反例；零业务网络请求、零设备调用 |
| 现场边界 | 没有重新构建/装机，没有本轮 Maestro、盲听、外部用户测试或 Android 全机型验收；status 健康不代表完整业务 QA 通过 |

本次只将评审报告与分批建议等 Markdown 文档入库。截图、复现脚本、日志、JSON 与 Expo 自动生成的诊断配置均保存在仓库外；自动生成的配置不是 mobile 的生效配置。本次没有修改产品实现。

**方案中应保留的选择有明确依据。**

独立 mobile 客户端与 HMI 共存、共享会话语义而各自呈现 UI，适合现有 TS 工程。Presence 做派生视图、确认策略由 VAL 决定、卡片按钮走普通发送链、S2S 副作用回主链，这些方向无需推翻。CNG 配置、原生件显式失败、APK 构建身份，以及两台机器区分测试与对照，也已经提供了可用的工程基础。见 [两端一脑的架构约束](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/architecture/cockpit-agent-architecture.md#L136)、[Android 架构方案](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-08-23-hmi-android-app-plan.md#L140)、[常驻包流程](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/README.md#L84)。

问题集中在这些正确原则之间的接线：UI 的“已停止”未必阻止传输，“显示了计数”未必提供选择入口，“派生的采集态”未必等于真实麦克风状态，“收到消息”也未必等于用户看见。

| 规划范围 | 评审判断 | 主要理由 |
|---|---|---|
| M0 地基、构建、多环境 | 主干可用，发布验证待补 | 已有常驻包身份；lint 未闭合，现有自动化入口仍主要面向 dev-client |
| M1 对话、确认、弱网、多端 | 实现具备基础，但存在行为缺陷 | 指定 operation 的确认被位置征询截走；取消没有贯穿队列和异步准备阶段 |
| M2–M4 PTT、轻点、免唤醒、S2S | 主要链路已实现，体验目标未全部闭合 | 播报中断入口有空档；KWS 最新已记录真人基线不达既定目标 |
| U1 光球、胶囊、隐私栏 | 结构成立，采集事实错误 | THINKING/SPEAKING 时仍有麦租约，但隐私栏派生为 off |
| U2 Voice Sheet、统一记录、播报 | 大部分落地，跨页面及停止语义不足 | final 与音频 completion 分离，UI 没有完整消费；层仍局限在 ChatBody |
| U3 Focus Dock、确认与承诺 | 完整验收不通过 | 其它待处理入口无回调，slot/safety_blocked 等仍无完整生产输入 |
| U4 尺寸、姿态、行车、无障碍 | 多形态已落，组合状态待补 | 横屏整列覆盖碰到 Dock/停止键；现有两个折叠机不能覆盖真实平板及全部支持系统 |
| U5 出 App 在场 | 仅 Shortcuts 已落，其他按计划延期 | 默认助理角色按实测不启用合理；QS Tile/Live Updates/小组件仍应按具体场景排期 |
| 后续语音修复 | 已记录的卡顿根因与修复应认可 | 队列播放器与账号级 RPM 合并有独立证据；不能将其扩大为首音、KWS 和 UI 性能全部闭合 |
| M5 生产化 | 方向清单存在，尚非可执行交付计划 | 账号、权限摘要、推送投递、更新签名、观测、隐私与支持矩阵需要明确依赖及完成条件 |

下面的问题以 R 编号便于后续立项。P1 表示应在扩大验证范围或宣布 UX 完整交付前优先解决；P2 表示明确的体验、工程或规划缺口。历史未闭合项与本轮新确认问题分别标注。

**R01 · P1 · 调试深链可在常驻包中自动启动录音。证据：代码确认。**

voice-spike 在 useEffect 中消费 auto 参数并直接启动 probeStutter；该方法在已有麦克风授权时会启动 AudioRecord 或 micLease，并发起 TTS 请求。路由在根 Stack 无条件注册，没有 prod/开发构建门控，也没有本次交互手势校验。OS 曾授予麦克风权限不能代替本次采集授权。普通 voice 深链“只升层不开麦”的验收没有覆盖这个入口。

证据：[自动执行入口](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/app/voice-spike.tsx#L864)、[录音探针](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/app/voice-spike.tsx#L600)、[无条件注册的调试路由](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/app/_layout.tsx#L53)。

责任：mobile 路由、诊断工具与构建边界。建议在 prod 从入口和处理函数两层禁止自动采集/自动改播放器的诊断参数；保留必要的只读诊断页。开发探针要独立标明授权入口。

验收：对 prod APK 枚举深链及冷启动入口，在“麦克风已经授权、免唤醒关闭”条件下，外部打开诊断链接不得启动录音；不能只验证新装未授权状态。

**R02 · P1 · 视觉抓帧确实落了本地缓存，没有清理路径。证据：应用代码、安装版本原生源码和官方文档一致。**

VisionCapture 使用 takePictureAsync，取 pic.uri 后上传；frame.ts 的 finally 只恢复 capturing。Expo SDK 57 的该接口会将图片写入 App 缓存；本地安装源码中 skipProcessing 分支也明确调用 writeStreamToFile。因此“不显示预览”“卸载 CameraView”均不能证明“不落盘”。目前没有成功、上传失败、取消或下一次启动的文件清理机制。

证据：[拍照返回文件 URI](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/vision/VisionCapture.tsx#L53)、[上传及 finally](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/vision/frame.ts#L57)、本地已安装 expo-camera 57.0.4 的 ResolveTakenPicture.kt（185–196 行调用 writeStreamToFile，290–297 行使用 FileOutputStream 写入缓存）。平台行为见 [Expo SDK 57 Camera 文档](https://docs.expo.dev/versions/v57.0.0/sdk/camera/)。

责任：mobile 视觉链路。应实现并验证满足现有零落盘约束的内存传输路径。仅添加 base64 选项不能自动消除原有写文件行为。若暂时采用“短暂缓存后清理”，那是对零落盘承诺的设计变更，不能仍按原约束签收。

验收：正常、超时、上传失败、关闭视觉开关、进程中断分别检查实际文件和上传状态。关闭能力还应作废已经进行中的 capture/upload；当前注销 capturer 只影响后续调用，不会中止持有旧函数的请求。

**R03 · P1 · 位置征询能截走针对指定 operation 的确认或取消。证据：本轮独立复现。**

当 pendingLocationText 非空时，confirmReply 不先检查 operationId 就进入位置分支。于是危险动作与位置征询并存时，用户点击危险动作的“确认”，实际调用 location.enable，并重发位置问题；原危险操作仍在台账内。

本轮真实代码复现结果：permissionCalls=1，发出帧 operation_id=null、is_confirmation=false，原 operation 仍 pending。该路径也会把针对危险动作的“取消”解释为拒绝位置征询。

证据：[confirmReply 分支顺序](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/session/store.ts#L279)、[按钮确实传递 operationId](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/FocusDock.tsx#L130)。

责任：mobile SessionCore。应明确区分本地授权回复与服务端 operation 回复，指定 operationId 的操作不得被其他待办消费；校验操作仍有效，再更新对应台账。

验收：危险操作 A + 危险操作 B + 位置征询同时存在，分别点确认/取消，逐项核对授权调用、operation_id、关闭对象和剩余待办。

**R04 · P1 · “已打断”没有贯穿未发送队列和异步准备阶段。证据：两个独立反例复现。**

第一种：离线发送一条消息，再调用 cancelCurrentTurn。界面 queued 变成 0，气泡标“已打断”，但 ResilientWebSocket 的原始帧还在队列里。恢复连接时实际发送顺序为“原请求 → cancel”。服务端是否来得及取消取决于时序，不能把这表示成“请求没发出去”。

第二种：定位刷新尚未完成时打断，refreshMeta 的 Promise 结束后仍执行 dispatch。本轮观测到发送顺序为“cancel → 新 user 请求”，即取消发生后又产生了请求。

证据：[定位刷新后无条件派发](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/session/store.ts#L267)、[取消实现](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/session/store.ts#L317)、[展示层队列记账](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/session/store.ts#L447)、[传输队列](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/hmi/src/ws.mjs#L74)、[恢复后 flush](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/hmi/src/ws.mjs#L153)。

责任：mobile 会话装配与共享 WS 传输边界。需要让请求 ID 贯穿“等待定位/视觉 → 排队 → 已发送 → 终态”；取消未发送项应移除对应真实队列项，并使在途异步回调失效。已发送项应按服务端取消结果展示，不能等同于业务回滚。

验收：分别在定位等待、视觉等待、离线排队、传输已发送、部分结果返回五个时点取消；检查恢复后实际帧及动作账，而非只看 queued 数字。

**R05 · P1 · 多项承诺无法按需选择，“另有 N 个待处理”是空入口。证据：代码确认。**

FocusDock 只渲染 pinCommitment 的第一项。“另有 N 个待处理”按钮使用可选 onOthers，但 ChatScreen 没有传入它；同时 v2 下所有气泡的 inlineConfirm 被关闭。第二、第三个确认仍在台账，却没有对应的触控入口，必须先处理第一项才露出来。

证据：[其它待处理按钮](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/FocusDock.tsx#L166)、[缺少 onOthers 接线](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/ChatScreen.tsx#L617)、[气泡确认被隐藏](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/ChatScreen.tsx#L539)。

责任：mobile Dock 与对话页。实现承诺列表或明确可达的展开方式，保留 operationId；不使用轮播，也不要求用户处理无关操作才能取消另一项。

验收：至少三项并存，直接取消最后一项，前两项保持不变；新增、到期、离线期间排序变化时，正在操作的对象不能错位。

**R06 · P1 · 长回答 final 到达后，停止播放按钮提前消失。证据：确定性代码路径。**

ChatBody 的 busy 只检查 pending/streaming/processActive。final 到达会清除这些标记，而真实 TTS 可能刚开始或还要播几分钟。Composer 仅根据 busy 把按钮在“发送/打断”之间切换，所以它会在仍播报时恢复发送。B5 又撤掉了 Voice Sheet 的独立停止键。此时轻点麦克风走的是“停止后开始录音”，不能满足“只停播、不开始采集”。

证据：[busy 的来源](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/ChatScreen.tsx#L398)、[final 清除文本忙态](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/session/store.ts#L605)、[合一键行为](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/Composer.tsx#L253)、[真实音频收尾单独发生](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/voice/speech.ts#L321)。

责任：mobile 对话与播放控制。交互应区分“停止播放”“取消在飞请求”“停止后开始说话”，合一键的可停止状态必须包含音频生命周期，且停止动作不能隐式开启麦克风。

验收：构造全文一次 final 返回、音频继续播放的长回答，以及 S2S 自答、主动消息播放；始终能一步只停播，真实播放队列清空，麦克风不新增租约。

**R07 · P1 · 隐私栏显示“麦克风关”，与实际免唤醒采集不一致。证据：纯函数反例与采集代码对应。**

HandsFreeController enable 后保持 micLease，将音频持续送入 VAD/KWS，直到 disable/teardown 才释放。但 derivePresence 对 hfFsm=THINKING 或 SPEAKING 派生 capture=off，继而 privacy.mic=off。本轮分别调用真实派生函数，两个状态都复现“关”。摄像头与麦克风同时工作时，共用单一 capture 枚举也会丢失麦克风事实。

证据：[持续麦租约](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/voice/handsFree.ts#L230)、[持续送 VAD/KWS](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/voice/handsFree.ts#L321)、[capture 枚举](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/presence/presence.ts#L174)、[隐私轴由 capture 反推](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/presence/presence.ts#L224)。

责任：mobile 采集事实与 Presence。隐私轴应直接消费“物理麦开启、ASR 正在上行、S2S 正在上行、摄像头开启/上传”等事实；助手正在思考或播报，不能推导麦克风已关闭。

验收：免唤醒六态 × 三段式/S2S × 视觉并发，对照 micBus、系统采集状态和网络帧验证隐私文案。UI 状态测试应直接对账物理事实。

**R08 · P1 · 主动消息在进入内存时就回“已呈现”，可能漏掉真正的用户触达。证据：本轮独立复现；跨页面影响需真机复核。**

SessionCore.handleFrame 收到 proactive 后 appendMessage，立即发送 proactive_ack，不检查 App 是否前台、对话页是否可见、消息是否实际呈现。本轮仅创建 SessionCore、完全没有渲染组件，仍收到 proactive_ack。用户停留在设置页/地图页时，对话 store 继续存活，这条路径就不能证明“用户看见了”。

证据：[立即 ACK 的代码](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/session/store.ts#L638)、[presented 是投递合同完成条件](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/conventions.md#L841)、[跨路由会话单例](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/session/wiring.ts#L1)。页面跳转不等于组件卸载，亦可参照 [React Navigation 生命周期文档](https://reactnavigation.org/docs/navigation-lifecycle/)。

责任：mobile 呈现层与主动投递契约。区分收到、可见呈现、用户处理；由有效的呈现出口提交 ACK，后台或不可见页面先保留待呈现状态。

验收：对话页、设置页、地图页、后台、锁屏分别到达提醒，检查 ACK 的时点；随后回前台，确认既不漏呈现也不重复播报。本轮未向生产投递任何测试提醒。

**R09 · P2 · “助手是层”的实现范围和横屏关键控制可达性未完整闭合。证据：代码推导，待目标设备交互验证。**

Voice Sheet、Privacy Rail、Focus Dock 和语音控制接线都在 ChatBody 内。地图页没有方案所写的 40% 语音层；已挂载的 ChatBody/免唤醒控制器与导航焦点又没有明确的统一开关。因此看地图时如何继续说话、在哪里看采集提示、如何停止，需要产品与生命周期契约。

另一个直接问题是 driving-landscape 时，语音层被挂到整个 chatColumn 最后，包含整列 scrim；它覆盖 Composer，也会覆盖同列 Focus Dock。新增了大球点击入口，却没有在上层提供确认和独立停止出口，必须先收层再操作。

证据：[语音层的挂载范围](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/ChatScreen.tsx#L486)、[横屏覆盖整列](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/ChatScreen.tsx#L660)、[整列触摸遮罩](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/VoiceSheet.tsx#L146)、[地图页](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/app/map.tsx#L45)、[控制器生命周期仅随 wantOn](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/useHandsFree.ts#L82)。

建议：先明确支持哪些页面内的语音，而不是直接扩大后台能力。若要应用内跨页使用，把可见语音壳与隐私提示放到应用级宿主；页面只提供上下文。横屏布局单独为承诺和停止控制预留空间，不能用覆盖全部内容解决高度不足。

**R10 · P2 · U3 与降级矩阵的后端依赖仍然影响完整交付。性质：已登记但未闭合。**

Q16 confirm_policy、Q19 missing_slots 仍缺完整下行契约。slot 没有真实产出路径；safety_blocked/fatal 在画廊有样本，usePresence 却没有生产产出。permission_denied 也主要接麦克风一路；TTS 服务降级没有在这里与 hf 降级同样接齐。因此“七种降级都在画廊里”和“所有降级用户均可恢复”不是同一结论。

证据：[slot 的缺口](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/presence/commitment.ts#L7)、[实际降级输入](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/usePresence.ts#L110)、[样本与生产的区分](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/presence/fixtures.ts#L4)、[Q16–Q19](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md#L701)。

建议把结构化拒绝原因、补槽上下文、服务不可用/鉴权失效的恢复出口纳入一张跨端合同卡，分别指定后端生产、网关透传、mobile 消费与验收。可以分期，不应继续只移动 UI 计划中的“待后端”标签。

**R11 · P1（体验收口）· 语音首要指标还没有达成闭合，优先级应高于继续增加视觉能力。性质：已知未闭合，不冒充本轮新实测。**

最新找到的可比真人 KWS 读数是 09-01：仪器 5/10、用户自数 6/10，低于计划 N≥8/10；B5 已批准独立 A/B 批，但未找到后续达到该门槛的正式结果。该旧样本不能推算成当前 ce12395 包的实际成功率，同样不能宣称问题已解决。

语音批最后的实测记录仍有首音 12.7s 的长回答，修前同类轮约 30s；规划和信息合成是主要等待。光球 3ms 状态反馈与 TTS 自身首片 0.63s，均不等于用户说完到听见答案的端到端延迟。见 [KWS 真人读数](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-09-01-mobile-ux-v2-b3-implementation-plan.md#L2037)、[文本、合成和播放分段定因](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-09-05-mobile-voice-broadcast-stutter-plan.md#L417)、[最终复验与仍开项](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-09-05-mobile-voice-broadcast-stutter-plan.md#L459)。

09-06 已修的队列节点“嗡嗡”与 RPM 周期空白，应保持已修状态，不重新列为现存缺陷。需要另行完成 KWS A/B、首音时延、对话页持续 UI 负载/Reanimated 和混合意图盲听的出账。

建议统一记录六个时点：入口反馈、采集开始、ASR final、首段有效文本、真实首音、实际播完/停止。按 provider/model、设备、APK SHA、云端 SHA 记录 p50/p95 和失败样本。不要再用一个“首响”承载几种不同指标。

**R12 · P2 · UX 可用性和无障碍验收仍缺独立用户与目标设备证据。性质：明确的验收缺口。**

B5 七张材料已经齐备，本轮也逐张查看；五名外部用户尚未测试，armed 未纳入这批材料。B2 的 1 人自评、当时允许继续开发的裁决，不能替代外部状态可读性基线。静态截图也不能独立证明“找到录音入口、录完、取消、停止播报”这些操作成功。

证据：[最新外部样本状态](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-09-04-mobile-ux-v2-b5-implementation-plan.md#L2894)、[1 人自评的真实边界](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-08-30-mobile-ux-v2-b2-implementation-plan.md#L4661)、[无障碍未闭合范围](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-09-04-mobile-ux-v2-b5-implementation-plan.md#L2743)。

现有 OPPO Android 14 与 Xiaomi Android 16 两台都属于折叠设备；它们不能替代 Android 10+ 的支持矩阵、普通直屏手机和真实平板。之前由设备/工具链阻塞的 TalkBack、Scanner、saver=true、release 下 keep-awake，现在应按实际新设备和常驻包重新安排，不能永久继承“验不了”。

建议保留状态截图识别，再增加少量真实任务：首次成功发问、误录取消、只停播、选择第二项确认、拒绝位置、断网恢复。统计各项失败而不是只统计均值。200% 字号、长中文、横屏确认和读屏焦点应覆盖主操作。

**R13 · P2 · 自动化验证还没有与常驻 release 交付方式对齐。证据：本轮运行与脚本确认。**

当前 CI mobile job 运行 tsc 与 Jest，没有 lint。现有 open-app 子流程固定走 expo-development-client 加 localhost:8081；APK workflow 主要产 assembleDebug，Maestro 为手动可选。09-07 开始常态使用内嵌 bundle 的 prod release 后，旧的 dev-client 通过记录不能直接代表交付包的启动、权限和生命周期已验证。

证据：[实际 CI 门禁](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/.github/workflows/ci.yml#L307)、[dev-client 前提](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/e2e/subflows/open-app.yaml#L1)、[Debug 构建](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/.github/workflows/mobile-apk.yml#L60)、[项目检查入口](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/package.json#L65)。

建议先固化与 React Compiler/Reanimated 使用方式相容的 ESLint 配置，再逐项区分真实缺陷与需要有依据解释的规则冲突；不要为“变绿”全局关闭规则。测试分清 dev-client 与 prod release 入口，02/06 的 Dock 开关前提自动自洽。补测试应针对本轮暴露的跨模块状态组合，已有单模块/样本覆盖继续保留。

本轮 lint 证据：Expo 默认 src 范围 63 errors / 43 warnings；全目录诊断另有 64 errors / 74 warnings，其中额外条目来自配置/测试等文件；原始诊断与 src 范围的 63/43 摘要只留在仓库外。两个范围不能混用。串行 detectOpenHandles 复查未报告句柄，不将首轮 worker 告警直接定性为内存泄漏。

**R14 · P2 · M5 需要拆成交付依赖；用户端身份也需要真实能力摘要。性质：规划评审建议。**

目前“手持/支架/可信车载平板”是用户可选的布局配置，App 无法查询 token scope。设置页前两档却写“不控车”，首页前几个默认示例又直接是空调、座椅加热等车控指令。对于计划中的无车控 token 用户，这是“首页推荐自己做不了的事”；对有车控 token 的实验用户，“不控车”又不是由真实权限得出的事实。

证据：[角色说明与真实限制](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/settings/SettingsScreen.tsx#L182)、[复用 HMI 快捷指令](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/core/settings/store.ts#L90)、[车控优先的默认指令](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/hmi/src/types.ts#L946)、[首屏入口](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/mobile/src/features/chat/ChatScreen.tsx#L154)。建议把布局角色、服务端能力摘要、用户身份、绑定车辆分开建模；示例按已验证能力呈现。首次引导在内部验证期手填 FQDN/token 可接受，外部交付必须有实际的登录与恢复流程。

M5 建议拆成五个可独立验收的结果：

| 工作包 | 核心产物 | 依赖与完成条件 |
|---|---|---|
| 账号与设备身份 | 正式登录、token 更新/吊销、能力摘要、设备绑定 | mobile 与后端共用可信身份；更换账号或服务器时，旧会话/音频/异步请求失效 |
| 连接与投递 | 公网入口、前后台重连、设备注册、推送、呈现 ACK | 先明确可靠触达语义，再接厂商推送；不依赖用户手动重启 Tailscale |
| 发布与更新 | 正式签名、版本递增、回退与配置升级、原生/JS 版本兼容 | 当前模板 debug 签名属于已声明的内部包边界；转换正式签名会影响升级和高德指纹，须有用户数据迁移安排 |
| 隐私与系统能力 | 实际采集同意、撤销、缓存与 SDK 初始化策略、锁屏展示 | 修复 R01/R02/R07；高德 init 当前硬编码同意的挂账也纳入此包 |
| 体验与观测 | 崩溃/ANR、首屏和内存、语音全链路指标、设备/网络矩阵 | release 包独立验证，带精确构建及 provider 证据 |

Live Updates 应用于有明确结束条件、用户发起且可跟踪的任务，不宜作为通用“助手常驻存在感”容器。QS Tile、静态 Shortcuts 与推送也不应无差别绑定为同一个前置。这个范围收紧依据 [Android Live Updates 设计指南](https://developer.android.com/design/ui/mobile/guides/home-screen/live-updates)，并不要求现在马上实现这些系统能力。

**R15 · P2 · 当前入口、总方案与最新记录存在冲突，会影响下一轮实施和验收。证据：本轮现读。**

UX 总方案顶部仍写 B5 第一批进行中；后面一些规范性段落仍写“三段式本机转文字后只上传文字”，虽然当前实现与隐私栏已修为云端 ASR。主实施验收表仍标 S2S 一轮未验，但 B2 已有真机证据。根 AGENTS 发布快照仍指向 9a3b6f2，本轮 live status 已是 a09c73a。入口还保留“本地领先若干提交未推送”的旧句，本轮 HEAD 与本地 origin/main 相同。

证据：[总方案状态](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md#L3)、[旧隐私规范段](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-08-29-mobile-ux-v2-presence-redesign.md#L425)、[旧 S2S 验收项](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-08-24-mobile-app-implementation-plan.md#L1737)、[较新 S2S 证据](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/docs/design/2026-08-30-mobile-ux-v2-b2-implementation-plan.md#L4634)、[发布快照入口](https://github.com/SuperdeMan/cockpit-agent/blob/f140f979654d1727406c0da4c9d72d42ada970b7/AGENTS.md#L83)。

建议完成修复后更新一个带日期的 Android 交接入口，将“规划承诺 / 实现状态 / 设备证据 / 当前阻塞 / 下一步”分开。历史事实保留在原计划，不反复复制进多个入口。评审阶段只记录冲突；本次归档已连接报告与分批入口，规范性段落和旧验收表的事实冲突仍需各批核实同步，R15 不因此全部销账。

**视觉与交互本身的判断。**

七张现有材料已经能看出统一的 Aurora 语言，确认 Dock 在竖屏比旧气泡确认更突出，正文和来源卡的阅读层次也已形成。但截图并不支持“所有状态一眼可读”的结论。顶部品牌球、气泡头像、Composer 球和语音层大球同时存在时，必须让用户清楚哪一个能操作、哪个只表示身份。当前大球只有横屏接点击，竖屏仍依赖下方较小入口；建议统一可操作性表达，并用首次使用任务验证，而不是继续增加光环种类。

大球、胶囊、把手带、快捷 chips、输入栏和 Dock 都占固定高度；后续布局应先保证“当前问题/结果、确认/取消、停止”这些内容的空间，再分配装饰面积。横屏一屏一卡和 200% 字号不能只在短标题/无确认时取一张通过图。截图 s2 的透叠与 s5 的瞬态裁切属于历史材料可见现象，本轮不据它们直接宣判当前 release 渲染回归；需要带相同状态与尺寸复核。

本轮查看的历史材料：s1 泊车记录、s2 行车 C 常驻层、s3 首页、s4 待确认、s5 收音、s6 思考、s7 播报。七张图是 2026-09-05 的本地历史材料，本次不将图片入库，也不冒充 ce12395 APK 新截图。

建议按下面顺序推进，避免把已登记的缺口继续带入下一批：

1. 先处理 R01–R08 的具体行为问题，以本轮五条反例及停止键/多项 Dock 的交互反例作为验收输入。
2. 将 Q16/Q19、跨页可见性及横屏关键控制放在同一轮合同与接线评审中；不改变 VAL 的安全裁决权。
3. 独立完成已批准的 KWS A/B 与首音时延工作，保留已修音频卡顿的既有证据；不要混调输入和输出链路。
4. 在固定的 prod release APK 上补真实任务、无障碍、前后台、网络、折叠/直屏/平板矩阵；OPPO 为测试落点，Xiaomi 做同包对照。
5. 修复和验收稳定后再形成 Android 当前交接页，并按依赖启动 M5。是否上 QS Tile、Live Updates、Widgets，应由具体用户任务决定。

**建议下一轮的签收条件是：用户确认的就是指定对象、取消后未发送请求不会复活、采集指示与实际一致、任何播报都能一步停止、所有待办可达，以及核心语音和无障碍指标有真实结果。** 这几条成立后，UX v2 才具备完整收口的依据。

原始复现结果、离线脚本、Jest 串行诊断日志、ESLint 明细和生成配置留存于仓库外，未随文档提交。五个反例的输入、观察结果与判定见 R03、R04、R07、R08；本页已保留全部分项检查结论。
