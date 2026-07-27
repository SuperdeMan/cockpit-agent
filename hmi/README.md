# 座舱 HMI（React + TypeScript + Vite）

智能座舱演示前端：「**Aurora Glass · 极光液态座舱**」风格——横屏 1920×1080 两栏布局（左对话流 / 右「上下文舞台」随对话切换场景）。通过 WebSocket 连 Edge Gateway 收发指令（文字或语音），流式展示助手回复、车控动作卡片与多轮确认；通过 HTTP 代理（llm-gateway:50059）做 ASR/TTS 与记忆读取。

> 视觉重构（2026-06-30 已落地）：P0 设计系统 / P1 两栏外壳·舞台 / P2 ~20 卡(A-3~A-5) / P3 对话动态六态(A-6) / P4 设置横屏侧栏(A-7) / P5 浅色主题 / A-4 信息卡按源重建 / A-5 右舞台数据驱动地图 / A-8 图标库（39 设计图标 + 21 补齐，`Icon.tsx`/`icons.gen.ts`/`icons.custom.ts`，**emoji 全替 A-8 线性图标**）/ **语音按钮即小舟光球** / **ASR 流式识别上屏**。**已重建容器 + 真后端全栈 e2e 验证**（天气/POI/股票/新闻/调研/赛事/充电/行程 8 卡族真数据渲染 + 过程区/确认条 + 光球流式上屏）。待做：P5 行车态、P6 Dashboard（均待 Figma 出帧）。本地预览参数：`?aurora` 设计系统沙盒、`?icons` 图标验证台、`?demo[=map|cards|info|states|charge|trip|route]` 卡片/对话态夹具、`?settings[=<分区>]` 设置面板、`?theme=light|dark`。见 `docs/design/2026-06-29-figma-hmi-implementation-plan.md`。

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
  - 语音输入：**识别引擎（实时 DashScope / 分块 MiMo / 关闭）+ 模型（Qwen3-ASR / Fun-ASR）**、识别语言、麦克风模式（按住/点按）、最长聆听时长
  - 语音唤醒·连续对话：免唤醒开关、唤醒词选择、续问聆听窗、静音断句
  - **语音链路**：端到端语音直连开关（默认关）+ 直连音色
  - 显示主题：深/浅色、字号、大触控、快捷指令编辑
  - 助手：昵称、回答长度、对话模型（快速/深度/自动）、**AI 大脑（LLM 厂商→模型两级切换：MiMo/MiniMax/DeepSeek/阿里百炼，切即全局生效并持久化 Redis，未配 key 置灰，接 `/api/llm/providers`+`/api/llm/provider`；厂商行下带被动健康点——绿=近窗全成+EWMA 时延/黄=偶发失败/红=高失败或限流/灰=近期未使用，2026-07-17）**；信息卡带 `_prov` 数据真实性徽章（mock=琥珀「模拟数据」醒目 / degraded·cached=灰标 / real=小字来源·取数时间角标，`Cards.tsx::ProvBadge`）
  - 能力开关：各 Agent 开关
  - 记忆：开关 + **查看会话对话记忆与偏好画像**（接 `/api/memory`）
- 会话级偏好经 WS `meta` 透传后端（`model_pref`/`answer_length`/`assistant_name`/`memory_enabled`）。

## 结构
```
src/
  App.tsx            外壳：WS 连接(重连) + 视图路由 + 消息状态机 + 两栏布局
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
  shell.css          应用外壳：1920×1080 两栏栅格 + 状态栏/输入区/欢迎态/气泡
  cards.css          卡片皮（覆盖既有语义类）+ AQI/SoC 等
  styles.css         旧「深空座舱 HUD」token（过渡期与 --au-* 并存，逐步退役）
  demo.ts            本地视觉验证夹具（不进正式主链）
  components/
    aurora/          设计系统 primitives：AuroraOrb(小舟光球三态)/Glass/AuroraBorder/ConfBadge/CatChip/AQISection + 预览沙盒
    ContextualStage  右「上下文舞台」场景机（待机/天气/地图）
    StatusBar / ChatView / Composer / Cards / SettingsPanel / controls
```

设计契约见 Figma Make `guidelines/Guidelines.md`；实施计划与分阶段进度见
`docs/design/2026-06-29-figma-hmi-implementation-plan.md`。
本地预览参数：`?aurora`（设计系统沙盒）、`?demo` / `?demo=map` / `?demo=cards`（场景与卡片夹具）。

## 自检
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
