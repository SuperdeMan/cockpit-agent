# ASR 收音失败：根因分析与修复链

- **状态**：代码链路已落地（2026-06-14）：前端竞态 + 后端转码 + ffmpeg + 自动化
  E2E；真实 API key、多容器格式矩阵和车机 HTTPS 仍需环境验收
- **交付对象**：后续开发者 / Agent（后端转码 + 部署）
- **关联代码**：`hmi/src/audio.ts`、`hmi/src/components/Composer.tsx`（已重构）；`llm-gateway/http_server.py`、`llm-gateway/providers.py`（`MiMoASRProvider`）
- **现象**：前端 mic 按住后无法收音；"ASR 还没打通"。

---

## 1. 根因链（端到端逐环排查）

### ① 前端录音竞态（主因）✅ 2026-06-13 已修
- 旧 `App.tsx`：`startRecording` 是 `async`（`await getUserMedia`）。用户快按快松时，`onMouseUp` 触发 `stopRecording` 时 `mediaRecorderRef.current` 仍是 `null`/旧值，`?.stop()` **空操作** → recorder 还没 `start()` 就被"停"，**整段无音频**。
- 旧实现也无**最短录音时长**保护，误触产生空 blob。
- **已修**：`hmi/src/audio.ts` 的 `MicController` 用 `starting`/`pendingStop` 状态机——松手发生在初始化期间时，待 recorder 就绪**立即 stop**；并加 320ms 最短时长门槛过滤误触。

### ② 安全上下文限制 ✅ 2026-06-13 已加检测/提示，⚠️ 部署待办
- 浏览器**仅在安全上下文**（HTTPS 或 `localhost`/`127.0.0.1`）暴露 `getUserMedia`。经局域网 IP + http 访问（如 `http://192.168.x.x:5173`）时 `navigator.mediaDevices` 直接 `undefined`，旧实现只 `alert("无法访问麦克风")` 不解释。
- **已修（前端）**：`audio.ts: secureContextOk()` 检测 + `Composer` 明确提示"麦克风需在 localhost 或 HTTPS 下使用"。
- **待办（部署）**：车机 webview / 演示环境需 **HTTPS** 或经 `localhost` 访问。

### ③ 音频格式链路 ⚠️ 后端待确认/转码（最可能的"打不通"点）
- 前端 `MediaRecorder` 产出 **webm/opus**（或 mp4/ogg，取决于浏览器），`audio.ts` 据实际 mime 推容器名传给后端 `format`。
- 后端 `MiMoASRProvider.transcribe`（`providers.py:175`）把音频拼成 `data:audio/<format>;base64,…` 走 `/v1/chat/completions` 的 `input_audio`。
- **风险**：MiMo ASR 官方示例多为 **wav/pcm**；若服务端不接受 **webm/opus 容器**，识别会失败或报错——这正是"看起来录上了却没结果"的典型症状。
- **建议（二选一）**：
  - **后端转码**：llm-gateway 收到 webm 后用 `ffmpeg`（或 `pydub`）转 wav 16k mono 再送 ASR。改动集中在 `http_server.py:handle_asr`，对前端透明。**推荐**（前端最省、兼容性最好）。
  - **前端采 PCM**：用 `AudioWorklet`/`ScriptProcessor` 直接采 16k PCM 并封 wav 头。前端改动大，且耗设备性能。
- 落地前先**实测**：`curl` 分别用 wav 和 webm base64 打 `/api/asr`，确认 MiMo 实际接受的容器集。

### ④ Provider 兜底"假通" ⚠️ 排查项
- 未配 `LLM_API_KEY` 时 `build_asr_provider()` 返回 `MockASRProvider`，固定回 `"[mock ASR] …"`（`providers.py:160`）。会让人误以为"通了"，实则没接真实 ASR。**排查时先确认配了 key**。

### ⑤ CORS / 端口 ✅ 当前正常
- `/api/asr` 在 `:50059`，CORS 已放开 `*`（`http_server.py:108`）；HMI `VITE_AUDIO_API_URL` 默认 `localhost:50059`，compose 已 expose 50059。Docker 部署时确认**浏览器**（非容器内）可达该端口。

---

## 2. 修复清单

| 环节 | 状态 | 动作 |
|---|---|---|
| ① 前端录音竞态 | ✅ 已修 | `MicController` 状态机 + 最短时长门槛（`hmi/src/audio.ts`） |
| ② 安全上下文 | ✅ 检测/提示已加 | 部署侧改 HTTPS 或 localhost 访问 |
| ③ 格式链路 | ✅ 已修 | 后端 webm→wav 转码（ffmpeg，`http_server.py:_transcode_to_wav`），Dockerfile 已装 ffmpeg |
| ④ Provider 兜底 | ⚠️ 配置坑已修 | key 在仓库根 `.env`，但 compose 从 `deploy/` 找 `.env` → 未加载 → `MockASRProvider`。已给 Makefile 加条件式 `--env-file .env`（2026-06-14）；裸 `docker compose` 仍需自带该参数 |
| ⑤ CORS/端口 | ✅ 正常 | 部署时确认浏览器可达 50059 |

---

## 3. 验证步骤（联调用例）

1. **后端单点**：`curl -X POST :50059/api/asr -d '{"audio":"<wav base64>","format":"wav","language":"zh"}'` → 应返回真实文本（非 mock）。再用 webm base64 复测 → 若失败即坐实环节③。
2. **前端录音**：浏览器 Console 观察 `MediaRecorder.state` 流转（recording→inactive），确认有非空 blob。
3. **安全上下文**：`window.isSecureContext` 为 true（localhost/HTTPS）。
4. **全链路 e2e**：录音 → `/api/asr` → 文本 → WS 送编排 → 回复。建议补一条 `test/` 用例覆盖"音频→识别→意图"。

---

## 4. 结论

"按住无法收音"的**主因是前端录音竞态**（2026-06-13 已修）；"ASR 没打通"的**最可能后端原因是 webm/opus 容器不被 MiMo ASR 接受**（建议后端转码）。叠加安全上下文与 mock 兜底两个易踩坑点。按上表逐环验证即可闭环。

---

## 5. 详细待办（后端打通 ASR，按此执行）

> 前端侧（竞态、安全上下文检测）已于 2026-06-13 修复，无需再动。剩余全在后端/部署。

### A1. 先确认走的是真实 ASR（10 分钟）
- [x] 起栈时 key 加载：`docker compose --env-file .env up -d`；`docker logs car-agent-llm-gateway-1` 确认无 `MockProvider`。
- [ ] 实测：录一段真音频转 wav，`curl -X POST localhost:50059/api/asr` → 返回真实文本（需真实 API key + 麦克风环境）。

### A2. 实测 MiMo ASR 接受的容器（坐实环节③，半小时）
- [ ] 同一段音频分别转 **wav / webm(opus) / mp4 / ogg**，逐个打 `/api/asr`（`format` 对应改），记录哪些成功、哪些报错或空。
- [x] 若 webm/opus 失败而 wav 成功 → 已走 A3 后端转码方案。

### A3. 后端转码 webm→wav ✅（2026-06-14 已落地）
- [x] `llm-gateway/http_server.py`：新增 `_transcode_to_wav()` 异步函数，用 ffmpeg 子进程（stdin→stdout pipe）转 16kHz mono wav；wav/pcm/pcm16 直接透传；ffmpeg 缺失或失败时回退原始字节。
- [x] `llm-gateway/Dockerfile`：加 `apt-get install -y ffmpeg`。
- [x] `llm-gateway/tests/test_transcode.py`：8 个测试全绿（透传/转码/回退）。

### A4. 部署安全上下文
- [ ] 车机 webview / 演示环境用 **HTTPS** 或经 `localhost` 访问（否则浏览器禁用 `getUserMedia`，前端已检测并提示）。

### A5. 端到端用例 ✅（2026-06-14 已落地）
- [x] `test/test_asr_e2e.py`：4 个用例（wav 返回文本、webm 转码、空音频、voices 端点），无 key 自动 skip。

### 验收
- 真实 MiMo ASR 对前端实际产出的容器（webm/opus）能稳定返回文本；A5 e2e 通过；浏览器在 localhost/HTTPS 下录音→识别全链路可用。
