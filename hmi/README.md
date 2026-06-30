# 座舱 HMI（React + TypeScript + Vite）

智能座舱演示前端：「**Aurora Glass · 极光液态座舱**」风格——横屏 1920×1080 两栏布局（左对话流 / 右「上下文舞台」随对话切换场景）。通过 WebSocket 连 Edge Gateway 收发指令（文字或语音），流式展示助手回复、车控动作卡片与多轮确认；通过 HTTP 代理（llm-gateway:50059）做 ASR/TTS 与记忆读取。

> 视觉重构中：P0 设计系统 + P1 两栏外壳/舞台 + P2 ~20 张信息卡按 Figma 设计稿重建已落地；P3 对话动态态、P4 设置侧栏待做。见 `docs/design/2026-06-29-figma-hmi-implementation-plan.md`。

## 本地运行
```bash
cd hmi
npm install
npm run dev      # http://localhost:5173
```

环境变量（`.env` 或构建时注入）：
- `VITE_EDGE_GATEWAY_URL` — Edge Gateway 地址（默认 `http://localhost:8090`），WS 走 `/ws`。
- `VITE_AUDIO_API_URL` — 音频/记忆 HTTP 代理（默认 `http://localhost:50059`），用于 `/api/asr`、`/api/tts`、`/api/voices`、`/api/memory/*`。

> 麦克风需安全上下文：经 `localhost` 或 HTTPS 访问才可录音（浏览器限制）。

## 功能
- **对话**：文字输入 / 按住麦克风说话（ASR）；助手回复**流式逐字**渲染 + “思考中”即时反馈；危险动作多轮确认（确认/取消按钮）。
- **信息类 UI 卡片**：天气/股票/搜索/新闻/深度调研/POI/路线/充电/行程/赛事等结构化卡片（Aurora Glass 液态玻璃风格，按 Figma 设计稿逐张重建），从 Agent 返回的 `ui_card` 经 Gateway→Cloud→Edge 全链路透传到 HMI 渲染。
- **语音播报（TTS）**：回复可自动朗读，音色可选（接 `/api/voices`）。
- **设置页**（右上 ⚙）：
  - 语音播报：音色选择/试听、播报与自动播放开关
  - 语音输入：识别语言、麦克风模式（按住/点按）、最长聆听时长
  - 显示主题：深/浅色、字号、大触控、快捷指令编辑
  - 助手：昵称、回答长度、对话模型（快速/深度/自动）
  - 能力开关：各 Agent 开关
  - 记忆：开关 + **查看会话对话记忆与偏好画像**（接 `/api/memory`）
- 会话级偏好经 WS `meta` 透传后端（`model_pref`/`answer_length`/`assistant_name`/`memory_enabled`）。

## 结构
```
src/
  App.tsx            外壳：WS 连接(重连) + 视图路由 + 消息状态机 + 两栏布局
  settings.tsx       设置仓库（localStorage 持久化 + Context）+ buildMeta()
  audio.ts           录音控制器(消除收音竞态) + TTS 播放 + 音色/记忆读取
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
