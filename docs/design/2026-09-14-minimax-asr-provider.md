# MiniMax ASR（`speech_to_text`）接入：整句引擎进流式插槽 + 批处理面

- **状态**：已实施并发布（release `40ccb9b6`，§6 首批 + §7 合并选择）；真栈验证 §8（网关 / 云端 HMI / OPPO 真机三面取到，`verify` verified）；真人按住说话待泓舟
- **交付对象**：llm-gateway / HMI / mobile 的后续开发者
- **关联代码**：`llm-gateway/providers.py`（`MiniMaxASRProvider` / `WholeUtteranceASRProvider` / 两个工厂）、
  `llm-gateway/http_server.py`（`/api/asr/stream/info`）、`hmi/src/types.ts` + `components/SettingsPanel.tsx`、
  `mobile/src/core/voice/catalog.ts`
- **关联文档**：`2026-06-30-asr-streaming-design.md`（WS 传输层与引擎抽象）、
  `2026-07-07-llm-asr-tts-multiprovider-and-sports-flags.md` §3（MiMo `stream:true` 的同款裁决）
- **官方文档**：https://platform.minimax.cn/docs/api-reference/speech-to-text（2026-09-14 读取）
- **红线**：不改 WS `/api/asr/stream` 协议与客户端状态机；不改 `.env`（key 复用已配置的 `MINIMAX_API_KEY`）；
  密钥不进代码/日志/文档。

---

## 1. 接口能力核对（先回答「和 fun-asr / qwen-asr 差不多吗」）

| 维度 | MiniMax `POST /v1/speech_to_text` | DashScope fun-asr-realtime / qwen3-asr-realtime |
|---|---|---|
| 传输 | HTTPS `multipart/form-data`，**整段音频文件一次上传** | WebSocket，**边说边推 PCM 帧** |
| 音频输入 | wav/aiff/flac/alac(m4a)/mp3/aac/opus/ogg；**不收裸 PCM**；≤500s、≤50MB | 16k mono s16le 裸 PCM 帧 |
| 中间结果 | `stream=true` 时 SSE 推 `{index, delta, finish, duration}`——**只是输出文本流式**，音频仍须先整段到齐 | 说话期间就有 partial（`.text` / `result-generated`） |
| 端点判定 | 无（文件即一句） | qwen3 有 server_vad；fun-asr 靠客户端 stop |
| 语言 | 请求头 `language`（BCP-47 主子标签：zh/yue/en/ja/…，空=混合语言） | `language` 参数 |
| 附加能力 | `verbose_json/srt/vtt` 说话人分离 + 时间戳（与 `stream=true` 互斥） | 无 |
| 模型 | 只有 `asr-1.0` | 两个模型 |
| 鉴权 | `Authorization: Bearer <MINIMAX_API_KEY>`（与 LLM / TTS 同一把） | 百炼 key |

**结论：形态不同，不是「差不多」。** MiniMax 是文件转写 API，和 MiMo ASR（`2026-07-07` §3 裁决过：
「`stream:true` 只是输出文本流式、音频仍须整段一次性传入，不构成真正的实时 ASR」）是同一类；
fun-asr / qwen-asr 是实时流式识别。对用户可感知的差别只有一条：**说话过程中不会边说边上屏**，
文本在松手（或端侧 VAD 判定说完）之后才开始出现。识别质量、语种覆盖（20 种）、说话人分离是它的长处，
但本项目的座舱语音链路只消费「一句话 → 文本」。

## 2. 目标与不改的东西

目标：MiniMax 作为**第三个可选识别引擎**出现在 HMI 设置页和 Android 设置页，用户切过去即生效，
批处理面（`/api/asr` + gRPC `Transcribe`）也可钉住它；一切走既有 provider 工厂 + env，**零业务逻辑改动**。

不改：
- WS `/api/asr/stream` 帧协议（start/stop/partial/final/done/error/unsupported）与两端客户端状态机
  （单 final 守卫、7s 无定稿兜底、批处理回退、换模型重试）。
- 默认引擎（HMI `dashscope/qwen3`、mobile `dashscope/fun-asr`）与 `.env`。

## 3. 方案

```
WS /api/asr/stream ──PCM 帧──▶ WholeUtteranceASRProvider ──(流末攒齐)──▶ MiniMaxASRProvider.transcribe_stream
                                    │                                        multipart + stream=true (SSE)
                                    ◀── partial(每个 delta 累积) … final ────┘
/api/asr、gRPC Transcribe ─────────────────────────────────────────────▶ MiniMaxASRProvider.transcribe
                                                                         multipart + stream=false (json)
```

### 3.1 `providers.py`

- `MiniMaxASRProvider(BaseASRProvider)`：`transcribe()` 走 `response_format=json`；`transcribe_stream()` 走
  `stream=true` 逐 SSE 事件 yield `(delta, finish, duration_s)`。共用一份表单装配：
  - 裸 PCM（`pcm/pcm16/s16le/pcm16le`）套 44 字节 WAV 头（16k mono，与流式面同一假设）；
  - ffmpeg pipe 产的 WAV RIFF/data size 是占位（0 / 0xFFFFFFFF），上传前按实际长度回填（`_wav_fix_sizes`）
    ——MiniMax 按容器解析，占位值不能赌它容忍；
  - `language`：`auto`/空 → 不发请求头（=混合语言识别）；否则取 BCP-47 主子标签小写（`zh-CN`→`zh`）；
  - `model`：只认 `asr-*`，否则回落常量 `asr-1.0`（目前唯一的模型）。**必须归一**：批处理面每次都传
    `ASR_MODEL`（默认 `mimo-v2.5-asr`），mobile 换模型重试会带 dashscope 备用模型 id。
  - 非 2xx → `ProviderHTTPError(status, body 片段)`（与 chat 4xx 可诊断同口径）。
- `WholeUtteranceASRProvider(BaseStreamingASRProvider)`：攒完整段 PCM 才打一次；<0.1s 直接 `final ""`
  （同 MiMo 分块阈值，不为一次误触付费）；引擎有 `transcribe_stream` 就按 delta 累积 yield partial 再 final，
  没有就一次 `transcribe` 出 final。**不做**「每 1.2s 重传整段产伪 partial」（MiMo 分块那套）：
  按时长计费下 6s 话要付 ~18s，且 MiniMax 侧 RPM 已经被 TTS 撞过（`MINIMAX_TTS_RPM` 的来历）。
- `build_streaming_asr_provider("minimax")`：有 `MINIMAX_API_KEY` → 上面两层组合；无 → `None`
  （客户端收 `unsupported` 无感回退批处理，与其它引擎同）。
- `build_asr_provider()`：`ASR_PROVIDER=minimax` 显式钉住；`auto` 下新增一条**只在
  `ASR_STREAM_PROVIDER=minimax` 且有 key 时**生效的规则（放在 MiMo 现状之后、dashscope 桥接之前），
  其余 auto 路径逐字不变。

### 3.2 `http_server.py`

`/api/asr/stream/info` 增加 `{"id":"minimax","label":"MiniMax 整句","available":bool(MINIMAX_API_KEY),"models":["asr-1.0"]}`；
`streaming` 标志把它算进去。WS 端点零改动（工厂已经按 provider 分派）。

### 3.3 客户端

- HMI：`AsrProvider` 联合类型加 `'minimax'`；设置页「识别服务商」加第四档「整句」，副标题写明
  「松手后才出字」；模型行对 minimax 显示 `asr-1.0`（不可选）。`Composer` / `App` 的 model 只对 dashscope 传，
  minimax 传空 → 网关回落默认，无需改。
- mobile：`ASR_PROVIDER_FALLBACK` 静态兜底表加一行（与网关 id/label 同步）；设置页本来就按目录渲染，
  模型只有一个时不出模型行。`usePtt` / `useHandsFree` 的 `fallbackModel` 保持原样：带着 dashscope 备用 id
  重试时网关把 model 归一成 `asr-1.0`，等价于同引擎再试一次（mimo 引擎上这条重试今天就是同样的形态）。

### 3.4 配置与文档

**不动 `.env.example` 与 `deploy/docker-compose.yaml`**：前者在发布闸里是 `runtime_config_contract`
（硬阻断、无放行通道，`docs/dev-guide.md`「cloud 档需要的两个键」），后者是 `infrastructure`（要重走摘要批准）；
而本能力需要的两个开关 `ASR_PROVIDER` / `ASR_STREAM_PROVIDER` 本来就已透传，`minimax` 直接可填。模型与端点
在代码里是常量（模型只有 `asr-1.0`、端点与 MiniMax LLM / TTS 同集群），不开 env 旋钮——半接线的旋钮
（写在模板里、compose 不列名注入不进容器）是本仓库踩过的坑。`docs/conventions.md` env 表、
`llm-gateway/README.md` 流式 ASR 一节、`hmi/README.md` 设置项一行同步。

## 4. 验收

离线（本轮必过）：
- `llm-gateway/tests/test_minimax_asr.py`：表单装配（Bearer / language 头有无 / 字段 / 文件名与类型 /
  占位 WAV 回填 / 裸 PCM 套头）、json 响应解析、SSE 解析（空 delta 终止事件、非 2xx、finish 后不再读）、
  整句适配（<0.1s 零调用、partial→final、断流两种结局、错误上抛）、模型归一；真 httpx 编码 multipart 核对线上形状；
  **WS 全链 e2e**（只桩出站 HTTP，真 `handle_asr_stream` + 工厂 + 适配器）：start→PCM→stop→partial/final/done、
  无 key→`unsupported`、上游 422→`error`。
- `test_batch_audio_providers.py`：工厂三档（显式 minimax / 无 key mock / auto+`ASR_STREAM_PROVIDER=minimax`）、
  流式工厂 minimax 有 key/无 key；既有 auto 用例全绿证明现状不变。
- HMI `tsc` + 单测；mobile `tsc`/`eslint`/`jest`。

真栈（需 `MINIMAX_API_KEY` 与部署，本轮不做）：`python scripts/dev_stack.py target show` 后
HMI 设置页切「整句」按住说话 → 松手 → 输入框出字自动发送；Android 设置页切 MiniMax → PTT 同样；
`/api/asr/stream` 的 `asr.stream` span 里 `provider=minimax`；`ASR_PROVIDER=minimax` 时 `/api/asr` 返回
`model=asr-1.0`。

## 5. 风险与已知边界

- **没有边说边上屏**——形态使然，设置页文案已明说；免唤醒 / 轻点即说的收尾都在端侧 VAD，不受影响。
- 松手后一整段上传 + 转写要落在客户端 7s 兜底窗内；≤15s 的话 16k mono WAV ≈480KB，云主机到
  `api.minimaxi.com` 通常够；超窗时客户端会回退批处理再算一次（两次计费）。
- 422（内容风控）/ 429（限流）直接当 error 上抛，客户端回退批处理引擎——若批处理也是 minimax 会再撞一次。
- `verbose_json` 说话人分离本轮不接：座舱链路没有消费方，且与 `stream=true` 互斥。

## 6. 实施记录（2026-09-14）

- 代码：`providers.py` 新增 `MiniMaxASRProvider`（+`_wav_fix_sizes` / `_minimax_asr_language` / `_minimax_asr_model` /
  `_asr_upload_file`）与 `WholeUtteranceASRProvider`，两个工厂各加 `minimax` 分支；`http_server.py` 目录加第三行；
  `.env.example` / compose 一度加了 `MINIMAX_ASR_MODEL` / `MINIMAX_ASR_URL`，第二批因发布闸撤回（§3.4）；
  HMI `types.ts` / `SettingsPanel.tsx`；mobile `catalog.ts` + `test/asrCatalog.test.ts`（读网关源码字面量对账）。
- 读数：llm-gateway `test_minimax_asr.py` 29 例 + 目录 515 passed / 1 skipped；mobile tsc 0 / eslint 0 /
  jest 98 suites / 1026；HMI `tsc` 25→25（全是既有 `.mjs` 声明缺失，零新错）。
- 真 ffmpeg 6.1 pipe WAV 实测：RIFF / data size 均为 `0xFFFFFFFF` 且 `data` 块前有 `LIST` 块（偏移 70）；
  回填后 `wave` 模块解析出 16000 帧、PCM 逐字节与回填前一致——`_wav_fix_sizes` 的前提成立。
- 八处变异各自只红自己的用例、按字节恢复：占位 size 不回填 / 模型不归一 / 极短按也付费 / auto 不跟随 /
  断流不抛错 / 网关目录 label 漂移 / mobile 表 label 漂移 / 适配器不出 partial。
- 未做：真栈（要带 `MINIMAX_API_KEY` 的部署 + 真机按住说话），§4 的核对点原样保留；未 commit。

## 7. 合并选择：「方式 → 引擎」两级，两端同一份契约（2026-09-14 第二批）

泓舟追问「MiniMax 和 MiMo 是不是一类，是的话能不能把两端的选择合并」，并裁决：**关闭档不留**（和整句一致就没必要留）、
MiMo key 不可用已知不管、做完上真栈。

### 7.1 判断

是一类：两家都是「整段音频到齐后才能识别」的转写 API（MiMo `chat/completions` 塞 base64、MiniMax multipart；
两家 `stream:true` 都只是输出文本流式）。此前设置页里的「分块 / 整句」两档差的不是引擎、是**网关适配器**
（`MiMoChunkedASRProvider` 每 1.2s 重传整段伪造 partial vs `WholeUtteranceASRProvider` 松手后打一次），
用户面在给适配器起名字。「关闭」（不开 WS、录完 POST `/api/asr`）的体验也是整句，只是引擎换成服务端默认。

### 7.2 模型

```
识别方式   [ 实时 · 边说边上屏 ]   [ 整句 · 松手后出字 ]
引擎       实时 → Qwen3-ASR | Fun-ASR（百炼）
           整句 → MiniMax asr-1.0 | MiMo v2.5
```

- **声明源只留网关一份**：`/api/asr/stream/info` 每引擎带 `mode: realtime | utterance`、全小写 `models`、
  `model_labels`；另给 `modes` 表。旧字段（`id/label/available/models`）原样保留，已装机的常驻包读它照常。
- **两端同一份契约**：`hmi/src/types.ts`（mobile 经 `@shared/types.ts` 引用）放 `AsrProviderInfo` / `ASR_MODES` /
  `ASR_PROVIDER_FALLBACK`（离线兜底）/ `asrEngineOptions` / `asrModeOf` / `pickAsrEngine` / `normalizeAsrProviders`。
  HMI 从静态四档改成读目录（与它的 TTS 一节同做法），mobile 删掉自己那份 `AsrProviderInfo` + 兜底表。
  `mobile/test/asrCatalog.test.ts` 读网关源码字面量对账 id 顺序 / label / mode / 模型集合 / 展示名。
- **存储不变**：仍是 `asrProvider + asrModel`，方式由目录派生、不单独存。两端各自的默认不变
  （HMI qwen3、mobile fun-asr）；换方式时优先本端默认那一对，其次该方式下首个可用。
- **`off` 退役**：类型里去掉；存量读到按整句迁移（`settings.load` / `mergeStoredSettings` → `migrateAsrEngine`），
  同时自愈失配的 (provider, model) 对（老存量切到 mimo 时 model 还留着 qwen3 的 id，在 start 帧上只表现为「连不上」）。
  mobile `AsrSession` 仍认 `provider='off'` 作**内部**批处理路径（jest 用它正向实证兜底链），只是用户选不到了；
  `usePtt` / `AssistantProvider` 的 warmSocket 不再按 off 门控。
- **MiMo 改走整句适配**：`build_streaming_asr_provider("mimo")` → `WholeUtteranceASRProvider(MiMoASRProvider)`；
  `mimo-chunked` 只作 env 别名保留旧伪 partial 形态，目录里不再出现。
- **备用模型只给百炼**：`usePtt` / `useHandsFree` 的 `fallbackModel`（dashscope 内的第二个模型）只在
  `asrProvider === 'dashscope'` 时带——整句引擎没有第二个模型，带了只是白等一个来回再回批处理。

### 7.3 验收（离线，本批必过）

- `test_minimax_asr.py` 目录契约：三条 `(id, mode)`、`modes` 两项、模型 id 全小写、`model_labels` 键集 = `models`、
  MiMo label「MiMo 整句」；工厂：`mimo` → WholeUtterance、`mimo-chunked` → chunked、无 key → None。
- `mobile/test/asrCatalog.test.ts`：对账 + 两级派生 + 换方式选引擎 + 存量迁移 + 入口归一 + fetch 回退。
- HMI tsc 零新错、node 333；mobile tsc / eslint / jest 全绿；`diagnosticRoutes` 那种把 catalog 桩成空的用例
  也不能把设置页渲染崩掉（兜底表从共享契约直引，不经 catalog 模块）。

## 8. 真栈验证（2026-09-14，release `40ccb9b6`，基线 `9ced633b`）

发布链（逐步单独授权）：push `9ced633b..40ccb9b6`（2 条：上一轮 release 记录 + 本轮）→ `deploy --sha 40ccb9b6`
dry-run `status=dry_run`、`blocking_changes=[]`、基础设施摘要与已批准一致 → `--apply` submitted → 独立 `status`：
`release_sha` = `running_release_sha` = `40ccb9b6`、5/5 端点 healthy。

### 8.1 网关：目录 + 同一段音频喂四个引擎（`scratchpad/probe_asr_minimax.py`，pcm16le 直传、100ms/帧实时节奏）

探针音频：真栈 MiniMax TTS 合成「帮我看看明天杭州的天气怎么样」→ 16k mono PCM 2.67s。

| 引擎 | 松手前 partial | 定稿 | 松手→定稿 |
|---|---|---|---|
| **minimax / asr-1.0（整句）** | 0（形态使然）；松手后 1 个 partial「帮我」 | 「帮我看看明天杭州的天气怎么样。」（与原句逐字一致） | **1391ms** |
| dashscope / fun-asr-realtime | 4 个（首个 406ms） | 同上 | 188ms |
| dashscope / qwen3-asr-flash-realtime | 10 个（首个 719ms） | 同上 | 203ms |
| mimo / mimo-v2.5-asr（整句） | 0 | — | `error`：MiMo 401（key 已失效，已知） |

`/api/asr/stream/info`：`modes` 两项；三条引擎各带 `mode`、全小写 `models`、`model_labels`；`minimax.available=true`、
label「MiMo 整句」。对照发布前同一探针（`9ced633b`）：`minimax` → `unsupported`、目录无 `modes`、MiMo 分块把 401
**吞成空定稿 + done**——新适配器如实报 `error`，客户端据此回退批处理。

### 8.2 HMI（云端 `https://<fqdn>/?settings=asr`，headless Edge + CDP）

「识别方式 [实时 | 整句]」→ 实时引擎「Qwen3-ASR / Fun-ASR」；点「整句」→ 整句引擎「MiniMax asr-1.0 / MiMo v2.5」，
`localStorage.cockpit.settings.v1` 回读 `{minimax, asr-1.0}` → 点 MiMo `{mimo, mimo-v2.5-asr}` → 回「实时」
`{dashscope, qwen3-asr-flash-realtime-2026-02-10}`（HMI 默认对）。截图 `hmi-settings-asr.png` / `hmi-settings-asr-utterance.png`
（scratchpad，未入库）。

### 8.3 Android（OPPO test，prod release 包 `40ccb9b6`）

prod release 包 `xiaozhou-companion-prod-release-40ccb9b6e-20260914-2204.apk`（`build_mobile.ps1 -Release -Variant prod -CompileJobs 3`，
11m6s，签名 SHA-1 `5e8f1606…f625`）装 OPPO test（`install -r`，`lastUpdateTime` 16:01:41 → 22:05:54，非 DEBUGGABLE），
APK SHA-256 本地 = 设备 `pm path` 回读 `4aed0592e23cbb5003858ca8089d4817fc2aef75e57422b5d739d76f4174f733`；设置页底行
`v0.1.0 · prod · 40ccb9b6e · 2026-09-14 21:52`。`scratchpad/device_asr_settings.py`（深链 `/settings`、uiautomator 按
`choice-<value>` 的 `selected` 回读，判据是回读不是「点过了」）：

| 步骤 | 回读 |
|---|---|
| 初始 | 识别方式「实时（边说边上屏）」selected；实时引擎 Qwen3-ASR / **Fun-ASR selected**（mobile 默认对，存量保留） |
| 点「整句（松手后出字）」 | utterance selected；整句引擎 **MiniMax asr-1.0 selected** / MiMo v2.5 |
| 点「MiMo v2.5」 | MiMo selected、MiniMax 取消 |
| 点「实时」 | 回到 Fun-ASR selected（`pickAsrEngine` 优先本端默认对）——设备状态还原到基线 |

行文案：「实时=边说边上屏；整句=松手后整段上传再出字（MiniMax / MiMo 这类转写接口）。以前的「不用流式」并进整句」；
「不用流式」选项已不在树里。截图 `device-40ccb9b6-realtime.png` / `device-40ccb9b6-utterance.png`（scratchpad，未入库）。
遇到的杂音：装机后 ColorOS 弹「USB 用于」系统对话框盖住整屏、dump 只剩它的六行文字，`keyevent 4` 关掉再跑。

### 8.4 未闭合

- ~~`dev_stack.py verify`~~：远端 e2e 事务锁曾被一个 **12:41Z 起、`sshd: ubuntu@notty` 下的 `remote-e2e-lock.sh hold --run-id e2e-d374ba04…`**
  占着（早于本轮任何真栈动作、本会话零残留进程），第一次 verify 拿不到锁 ⇒ `failed`（artifact `20260914T133608Z-unknown.json`
  全空、不说原因）。泓舟授权后按 run-id 核对再 kill（236919/236920），`flock` 探测 AVAILABLE，重跑 **`verified`**
  （`20260914T144801Z-40ccb9b.json`，`minimax:MiniMax-M3`），status 回到 `ok` 零 warning。
- 真人按住说话（HMI 麦克风 / Android PTT）：泓舟自己验，步骤与观察点见 §8.5。

### 8.5 真人验收步骤（泓舟）

- HMI：设置 › 语音输入 › 识别方式「整句」› 整句引擎「MiniMax asr-1.0」，关掉设置面板，按住光球说一句 → 松手。
  预期：说话期间输入框**不出字**；松手后 ~1–2s 出字并自动发送。切回「实时 / Fun-ASR」对照：边说边出字。
  若出现「实时识别暂不可用，已切换经典模式」说明 WS 路径失败回退了批处理（批处理引擎是服务端 `ASR_PROVIDER=auto` 的
  dashscope 桥接，不是 MiniMax）。
- Android（OPPO 上已是 `40ccb9b6e` prod 包）：设置 › 语音 › 识别方式「整句」› 整句引擎「MiniMax asr-1.0」，回对话页
  PTT 说一句 → 松手，预期同上；再开免唤醒说一句，端侧 VAD 判定说完后出定稿。超过 7s 没定稿会走批处理兜底（同上）。
- 事后可在可观测台看 `asr.stream` span：`provider=minimax`、`model=asr-1.0`、时延与定稿长度。
