# 座舱 HMI（React + TypeScript + Vite）

智能座舱演示前端，横屏 1920×1080：全幅情境舞台叠加单一对话面板。通过 WebSocket 连 Edge Gateway 收发指令（文字或语音），展示完整回答、结果证据与多轮确认；通过 HTTP 代理（llm-gateway:50059）做 ASR/TTS 与记忆读取。

> HMI Visual v2（2026-10-09）I1–I6 已实现：深浅与四档字阶、只读行车投影、固定确认条、34 类业务卡片、情境舞台、12 节设置及开发者区。设计依据与逐批证据见 [实施记录](../docs/design/2026-10-08-hmi-visual-v2-implementation-plan.md)。本轮为本地 Vite/隔离浏览器验证，**未部署生产**；不转借旧版真栈验收。地图当前为明确标注的底图不可用示意，车型轮廓为占位；Dashboard 不在本轮范围。

预览：`?tokens` 字阶、`?icons` 图标、`?demo[=map|cards|info|states|charge|trip|route|results]`、`?card-gallery=N`（44 份样本）、`?settings[=tts|asr|wake|pipeline|occupants|vision|display|location|assistant|agents|memory|developer]`。加 `&theme=light` 切浅色；大字/大触控/手动行车在「显示」中切换。诊断默认隐藏，`?dev` 或「开发者模式」开启。

## v2 完整结果阅读（CA2-04）

final 可带 ResultBundle v1；`resultBundle.mjs` 与 Android 共用字段选择、旧协议回退和 revision 规则。
完整答案用于消息正文，原 speech 仍用于短 TTS。隐藏卡片/多项正文通过“查看各项结果”阅读，
主卡不重复渲染，行车态隐藏详情，确认仍由原 operation_id/confirm_policy 控件处理。
本地夹具：`?demo=results`；协议和证据见 [结果契约](../docs/design/2026-09-27-v2-result-bundle.md)。

## 本地运行
```bash
cd hmi
npm install
npm run dev      # http://localhost:5173
```

环境变量（`.env` 或构建时注入）：
- `VITE_EDGE_GATEWAY_URL` — Edge Gateway 地址（默认 `http://localhost:8090`），WS 走 `/ws`。
- `VITE_AUDIO_API_URL` — 音频/记忆 HTTP 代理（默认 `http://localhost:50059`），用于 `/api/asr`(批处理)、`/api/asr/stream`(WS 流式识别上屏)、`/api/tts`(批处理)、`/api/tts/stream`(WS 服务端流式 TTS)、`/api/tts/stream/info`(引擎+音色探测)、`/api/voices`、`/api/memory/*`。

> 麦克风需安全上下文：经 `localhost` 或 HTTPS 访问才可录音（浏览器限制）。

## 功能
- **对话**：文字输入 / **按住下方小舟光球说话**（ASR）；语音支持**流式实时上屏**——边说边在输入框逐字显示、松手定稿自动发送（任一环失败无感回退批处理识别）；助手回复**流式逐字**渲染 + “思考中”即时反馈；危险动作多轮确认（确认/取消按钮）。
- **信息类 UI 卡片**：天气/股票/搜索/新闻/深度调研/POI/路线/充电/行程/赛事等结构化卡片（Aurora Glass 液态玻璃风格，按 Figma 设计稿逐张重建），从 Agent 返回的 `ui_card` 经 Gateway→Cloud→Edge 全链路透传到 HMI 渲染。
- **语音播报（TTS）**：回复可自动朗读，**服务端流式合成**（文本增量进、PCM 分片无缝拼播、首音 <1s，`pcmPlayer.mjs` 调度）；音色**两级选择**——先选引擎（CosyVoice 流式 / Qwen 流式方言 / MiMo 流式 / MiniMax 流式）再选该引擎音色，逐个可试听；无凭据/失败无感回退句级批处理。
- **免唤醒连续对话 / 唤醒词**（R4.3，opt-in 默认关）：本地 KWS 唤醒（sherpa-onnx WASM）+ silero VAD 端点 + 续问窗免唤醒接话 + 播报中打断（barge-in）+ 「退下吧」本地退场不上云。状态机在 `voiceLoop.mjs`（六态，纯逻辑全注入），外设接线在 `handsFreeController.ts`。
- **端到端语音直连（S2S，M4，可选挡位，默认关）**：闲聊与常识由语音大模型直接听直接答（首音频 ~609ms、多轮更连贯）；**需要执行或查实时信息的请求由模型自动交回确定性主链**——车控不绕权限校验与二次确认。断线自动重连并重注入上下文，连不上整条回落三段式。**voiceLoop 状态机零改动**，S2S 只是换了一组效果回调（详见 `docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`）。⚠️ 开启后唤醒窗内的**原始语音**会上云（三段式只上传识别后的文字），故须用户显式开启。
- **设置页**（右上 ⚙）：
  - 语音播报：音色选择/试听、播报与自动播放开关
  - 语音输入：**识别方式（实时=边说边上屏 / 整句=松手后出字）→ 引擎（实时：Qwen3-ASR / Fun-ASR；整句：MiniMax asr-1.0 / MiMo v2.5）**两级，目录来自网关 `/api/asr/stream/info`、与 Android 设置页同一份契约（`types.ts::ASR_*`）；识别语言、麦克风模式（按住/点按）、最长聆听时长。以前的「分块 / 关闭」并进整句（2026-09-14）
  - 唤醒与连续对话：免唤醒开关、唤醒词选择、续问聆听窗；静音断句参数在开发者区
  - **语音链路**：端到端语音直连开关（默认关）+ 直连音色
  - 乘员与声纹、看一看：独立分区，保留既有注册/识别与摄像头接线
  - 显示：深/浅色、字号、大触控、手动行车、快捷指令编辑
  - 位置与常用地点：权限、定位观测与地点列表；常用地点仍通过语音设置
  - 助手：昵称、回答长度、对话模型（快速/深度/自动）、AI 大脑；沿用 `/api/llm/providers` 和 `/api/llm/provider`，切换全局生效
  - 能力开关：各 Agent 开关
  - 记忆：开关、会话与画像；云端删除前确认。偏好/地点按 item ID 删除，经历明确按整个类别清除；清除全部覆盖当前账号所有乘员及关联身份数据
  - 开发者：模型 ID、采样率、健康/延迟、VAD 参数、资源指引、trace 与原始错误开关；不改变 WS 元数据或共享 Settings 契约
- 会话级偏好经 WS `meta` 透传后端（`model_pref`/`answer_length`/`assistant_name`/`memory_enabled`）。

## 结构
```
src/
  App.tsx            外壳：WS 连接(重连) + 视图路由 + 原消息状态机
  settings.tsx       设置仓库（localStorage 持久化 + Context）+ buildMeta()
  audio.ts           录音控制器(消除收音竞态) + StreamingRecognizer(流式识别 WS) + StreamingTtsSession(流式 TTS WS+回退) + 批处理 TTS 队列 + 音色/记忆读取
  pcmPlayer.mjs      流式 TTS PCM 分片调度(jitter 起播/无缝拼接/underrun 重建/barge-in 停,Web Audio 注入)
  ── 语音回路（R4.3 / M4；全部纯逻辑+注入，可 node:test 无 DOM 单测）──
  voiceLoop.mjs      语音 FSM 六态(IDLE/ARMED/LISTENING/THINKING/SPEAKING/FOLLOWUP)：唤醒/聆听/
                     打断/退出词/续问窗/端点宽限。效果全注入=换 DSP 本文件一字不改；143 例保护
  handsFreeController.ts  把 FSM 接到真实外设：VAD/KWS/流式 ASR/S2S 会话 + App 效果(send/stopTTS/orb)
  s2sClient.mjs      M4 端到端语音会话客户端(/api/s2s)：协议翻译+收音门控+播放+打断；**不含状态机**
                     ——用户可感知状态的唯一权威是 voiceLoop
  vadEngine.ts / kwsEngine.ts / sileroEndpoint.mjs   VAD 端点 / 唤醒词(WASM) / 端点判据
  pcmRing.mjs        VAD 帧前滚缓冲(pre-roll 补首字) + Float32↔Int16 转换
  utteranceHeuristics.mjs  退出词/语气词/完整度判据（去尾语气词后精确匹配，防吞「退出导航」）
  voiceMetrics.mjs   语音语义事件计数(localStorage，供真麦验收)
  types.ts           共享类型 + 能力目录 + 默认值（数据契约，重构不改字段）
  aurora.css         Aurora Glass 设计系统 token 层（--au-*，深空/玻璃/极光/语义色/keyframes）
  shell.css          全幅舞台与单一面板、状态栏、输入区与行车回答条
  conversation.css   回答/过程/固定确认条与历史滚动
  stage.css          待机、地图示意、天气、车况、日程、阅读/付款、媒体
  settings.css       设置覆盖层、ListItem/Select/VoiceTile 与无障碍尺寸
  cards.css          卡片皮（覆盖既有语义类）+ AQI/SoC 等
  styles.css         旧「深空座舱 HUD」token（过渡期与 --au-* 并存，逐步退役）
  demo.ts            本地视觉验证夹具（不进正式主链）
  components/
    aurora/          设计系统 primitives：AuroraOrb(小舟光球三态)/Glass/AuroraBorder/ConfBadge/CatChip/AQISection + 预览沙盒
    ContextualStage  情境舞台（独立只读选择；行车不展示阅读/付款码）
    StatusBar / ChatView / Composer / Cards / SettingsPanel / controls
```

当前设计见 [Visual v2 brief](../docs/design/2026-10-08-hmi-visual-redesign-brief.md) 与 [落地规则](../docs/guides/figma-design-system-rules.md)。旧 Make 稿与 2026-06-29 计划只作历史证据。
本地预览参数：`?aurora`（设计系统沙盒）、`?demo` / `?demo=map` / `?demo=cards`（场景与卡片夹具）。

## 自检

`src/ws.mjs` 也是 Android 的共享传输层。`send(frame, hooks?)` 的可选本地 hooks
（`canSend/onSent/onDropped`）、`discardQueued(requestId)` 与 `sendIfOpen(frame)`
用于 Android 的请求撤回；hooks 不进线上 JSON，HMI 现有单参数 send 保持兼容。
`onSent` 只证明写入 socket，不证明业务执行；详细边界见 `docs/conventions.md` §9.33。
```bash
npx tsc --noEmit -p tsconfig.json   # 类型检查
npx vite build                      # 生产构建
```


## 声纹与视觉（M4 P4）

- `voiceprintIdentifier.mjs`：唤醒后首句**边说边识别**（累计 1.5s **有效语音**即发请求，
  此刻用户还在说）。**取值是同步的，没有「等一下结果」的接口**——曾有过 150ms 软等待，
  它把 FSM 的 `onSend` 变成异步、破坏「用户气泡由 send 同步接管」的不变量，已整个删除并有
  回归护栏挡它被加回来（**绝不为了认人拖慢首字**）。
  **只收 VAD 语音段的帧**（controller 转发 `onSpeechStart/End`）：喂进来的 `vad.onFrame` 是
  原始帧旁路**不做门控**，按墙钟累计的话唤醒后那一秒的提示音+静音会把探针稀释到谁都认不出。
  VAD 端点处**补发一次**（短问句约 1.2s 攒不够 1.5s，否则从「认错」退化成「永不识别」）。
  **一次唤醒锁一次**：续问窗内不重识，回 ARMED/IDLE 才解锁。识别不到恒 `primary`，
  且**认不出就不给称呼**（`displayName` 只在 `accept` 时非空——称呼是断言，没认出来不该断言）。
  纯逻辑 + 依赖注入，20 个 node 测试；**voiceLoop 一字未改**（用 `onState` 既有的第二参
  拿 FSM 态，不给它加回调——同 S2S 期的纪律）。
- `pcmRecorder.mjs`：设置页注册与「试一试」的录音器。**必须与主链路识别同一条音频通路**
  （AudioContext 16k + 同一个 `vad-capture-worklet` + 同一组 EC/NS/AGC 约束 + 原始 s16le PCM）。
  **不要改回 MediaRecorder/webm**：opus 有损压缩会把模板挪到另一个信道上，真机实测同人余弦
  0.73→0.48、探针会塌向别人的模板（两个人认成同一个）。录完**切头尾静音**（只切头尾不逐帧筛，
  中间挖洞会把话切碎）并如实回报「只听到 X 秒人声」。11 个 node 测试含源码级契约
  （约束与 `vadEngine` 逐字一致 / 两个端点默认必须 `pcm16le` / 设置页不得再用 MediaRecorder）。
- `visionFrame.mjs`：`needsFrame(text)` 端侧触发词判定（与 `agents/vision/manifest.yaml`
  的 route_hints **同口径，两侧同步改**）+ `captureFrame()` 抓一帧上传换 `frame_id`。
  **默认一帧都不采**，命中触发词才抓；抓完立刻关摄像头；任何失败返回空串
  （由 vision Agent 诚实说「没拿到画面」，不在这里弹错打断对话）。
- 设置页新增「乘员与声纹」（注册 3 段 / **改名** / **重录** / 删除）与「看一看」两组，
  **均默认关**。**「重录」与「添加乘员」是两个动作**：前者带原 `occupant_id` 更新模板、
  身份与记忆都保留，后者会分配新的 `occ-N`——走错了这个人的记忆当场分家成两半。
  称呼**必填**（空名不再静默兜底成「乘客」，那会把上次填对的名字冲掉）。

## 车辆投影（CA2-06）

`vehicleObservation.mjs` 与 mobile 共用：绑定 session_identity，拒绝其它车辆、旧 revision/epoch，
只展示未过期的 good 信号，断连清空读数并标明模拟来源。旧帧只属于明确 v1 的未升级兼容档。
[服务端协议与混部边界](../docs/design/2026-09-27-v2-vehicle-state-and-simulation.md)。
