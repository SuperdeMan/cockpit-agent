# 座舱 HMI（React + TypeScript + Vite）

智能座舱演示前端：「深空座舱 HUD」风格。通过 WebSocket 连 Edge Gateway 收发指令（文字或语音），流式展示助手回复、车控动作卡片与多轮确认；通过 HTTP 代理（llm-gateway:50059）做 ASR/TTS 与记忆读取。

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
- **信息类 UI 卡片**：天气/预报/股票/新闻/搜索/POI 结构化卡片（深空座舱 HUD 玻璃态风格），从 Agent 返回的 `ui_card` 经 Gateway→Cloud→Edge 全链路透传到 HMI 渲染。
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
  App.tsx            外壳：WS 连接(重连) + 视图路由 + 消息状态机
  settings.tsx       设置仓库（localStorage 持久化 + Context）+ buildMeta()
  audio.ts           录音控制器(消除收音竞态) + TTS 播放 + 音色/记忆读取
  types.ts           共享类型 + 能力目录 + 默认值
  styles.css         深空座舱 HUD 设计系统（主题/字号/大触控由 [data-*] 驱动）
  components/         StatusBar / ChatView / Composer / SettingsPanel / controls
```

## 自检
```bash
npx tsc --noEmit -p tsconfig.json   # 类型检查
npx vite build                      # 生产构建
```
