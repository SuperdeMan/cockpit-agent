# ASR 流式识别上屏 · 设计与落地

- **状态**：**已落地（commit 184d48f，2026-06-30）**。MiMo 分块流式上屏真栈验证可用（默认引擎，fake-mic e2e：输入框实时上屏「今天杭州天气怎么?」→ 松手定稿自动发送）；DashScope 实时（qwen3/fun）连通+鉴权+协议均按官方文档实现，但实测**百炼账号 realtime 推理一送音频即服务端关连接 1007/1011 InternalError**（连 paraformer inference 端点同 InternalError）——判定为**账号侧 realtime ASR 未开通/未激活**，非客户端问题；HMI 选 dashscope 时 provider 抛错→网关回 error→无感回退批处理。**待泓舟在百炼控制台确认 Qwen3-ASR-Flash-Realtime 的 realtime 推理权限后，设置页一键切「实时」即用**。
- **交付对象**：落地的 Claude Code / 后续开发者。
- **目标**：说话过程中识别文本**实时增量上屏**（输入框 interim → 松手定稿发送），替换现有"录完整段才出文本"的批处理体验。
- **关联代码**：`hmi/src/audio.ts`、`hmi/src/components/Composer.tsx`；`llm-gateway/http_server.py`、`llm-gateway/providers.py`。
- **关联现状**：`docs/design/2026-06-13-asr-pipeline-analysis.md`（批处理链路根因与修复）。
- **红线**：不破坏现有批处理 `/api/asr`（保留作回退与非流式路径）；密钥不进代码/日志；车控链路无关。

---

## 1. 现状与问题

当前 ASR 全批处理：Composer 录完整段（MediaRecorder webm/opus）→ 松手 → `recognize()` → `POST /api/asr`（整段 base64）→ ffmpeg 转 wav → 单次 gRPC `Transcribe` → MiMo（`chat/completions input_audio`，**非流式**）→ 整段文本回填输入框。说话期间**无任何反馈**，长句体感差。

**关键约束**：MiMo ASR 是批处理，provider 层拿不到流式 partial。要"流式上屏"必须引入流式识别引擎，或在批引擎上伪造 partial。

## 2. 决策（泓舟已拍板）

- 识别引擎走 **DashScope（阿里云百炼）实时流式 ASR**，接入 **`qwen3-asr-flash-realtime`** 与 **`fun-asr-flash`/`fun-asr-realtime`** 两个模型，**同一把百炼 key**（现有 `LLM_EMBED_API_KEY=sk-ws-…`，embedding 已在用）。
- **保留 MiMo 批处理 ASR**，做可切换回退（无百炼 ASR 权限/断网时仍可用）。
- 引擎经 **provider 工厂 + env** 选择，沿用本项目一贯做法（改 env 即切，不改业务码）。

## 2.1 探针实测结论（2026-06-30，已用百炼 key 验证）

逐端点/模型探针（脚本见交付时附，key 不入库/不打印）得到**确定结论**——纠正了最初按 paraformer 风格的设想：

| 端点 | 结果 |
|---|---|
| `wss://dashscope.aliyuncs.com/api-ws/v1/inference`（paraformer task-group 协议） | ❌ `paraformer-realtime-v2`/`fun-asr-realtime` 一律 `InternalError`；`qwen3-asr-flash-realtime` `ModelNotFound`。**此端点/协议不适用本任务**。 |
| **`wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<id>`（OpenAI 兼容 Realtime 协议）** | ✅ **鉴权 `Authorization: Bearer <key>` 通过**；`Qwen3-ASR-Flash-Realtime-2026-02-10`、`qwen3-asr-flash-realtime`、`fun-asr-realtime` **均连通**（收到 `session.created`）。 |
| `dashscope-intl…/realtime` | ❌ 非 101（区域不对，cn key 走主站） |

- **正确协议=OpenAI Realtime 事件流**：连上收 `session.created` → 发 `session.update`（`input_audio_format: pcm16`、`input_audio_transcription`）→ `input_audio_buffer.append`（base64 PCM16 分帧）→ `input_audio_buffer.commit` → 收 `conversation.item.input_audio_transcription.delta`（**partial 上屏**）/`.completed`（定稿）。
- **qwen3 正确 id（泓舟提供并验证）**：`Qwen3-ASR-Flash-Realtime-2026-02-10`（短名 `qwen3-asr-flash-realtime` 亦连通）。**默认引擎改用 qwen3**（既然连通），`fun-asr-realtime` 作备选。
- **待 e2e 实测**：探针用正弦波（非语音）未触发 transcription 事件——属预期（服务端 VAD 对非语音不出字）；**真实语音的 transcription 事件流在前端 e2e（fake mic 喂真音频）确认**。

## 3. 架构（一次建好传输层，引擎可换）

```
HMI Composer ──(WebSocket: audio 帧 + start/stop)──▶ llm-gateway /api/asr/stream (aiohttp WS)
   ▲  interim/final JSON                                   │
   └───────────────────────────────────────────────────────┘
                                              ┌── DashScope 实时 ASR（WS，qwen3/fun）  ← 默认
   StreamingASRProvider.stream(audio_iter) ───┤── MiMo 分块伪流式（批引擎累积重转）    ← 回退
                                              └── off（降级走批处理 /api/asr）
```

- **传输层（一次建好，三引擎通用）**：HMI 与网关之间新增 WS `/api/asr/stream`；网关与引擎之间按引擎实现。HMI 永远只跟网关说话，引擎切换对 HMI 透明。
- **音频路径**：浏览器 `MediaRecorder` 产 webm/opus 帧 → WS 二进制帧推给网关 → 网关 **流式 ffmpeg**（webm/opus stdin → PCM 16k mono stdout，复用现有转码经验）→ 喂 DashScope 实时 WS。**HMI 不改采集方式**（不引入 AudioWorklet/PCM，省事且兼容好）；代价=服务端转码一跳延迟（可接受）。
- **回退 `mimo-chunked`**：网关累积 webm 帧，每 ~1.2s 转 wav 整段打一次 MiMo 批 `transcribe`，把最新整段文本作为 interim 推回；松手做最终一次。代价：每句多次调用、partial 跳变。

## 4. 后端落地（`llm-gateway`）

### 4.1 `providers.py` 新增流式 provider（与现有批处理并存）
```python
class BaseStreamingASRProvider:
    async def stream(self, audio_chunks: AsyncIterator[bytes], *, src_format: str,
                     language: str) -> AsyncIterator[dict]:
        """yield {'text': str, 'final': bool}；text 为当前累积识别文本。"""
        raise NotImplementedError

class DashScopeRealtimeASRProvider(BaseStreamingASRProvider):
    # wss DashScope 实时 ASR；run-task(model, format=pcm, sample_rate=16000)→
    # 流式 audio 二进制帧→result-generated(partial/final 句)→task-finished。
    # model 经 env（qwen3-asr-flash-realtime / fun-asr-realtime）。key=DASHSCOPE_ASR_KEY。
    # 内部把 src webm 经 ffmpeg 流式转 PCM 再推（或在网关层转，见 §4.2）。

class MiMoChunkedASRProvider(BaseStreamingASRProvider):
    # 包现有 MiMoASRProvider，累积+节流重转，伪 partial。

def build_streaming_asr_provider() -> BaseStreamingASRProvider | None:
    # env ASR_STREAM_PROVIDER: dashscope-qwen3 | dashscope-fun | mimo-chunked | off
    # 无 key / off → None（HMI 探测到则回退批处理）
```

### 4.2 `http_server.py` 新增 WS 端点
```python
@routes.get("/api/asr/stream")
async def handle_asr_stream(request):
    ws = web.WebSocketResponse(); await ws.prepare(request)
    # 收：二进制=音频帧；文本 JSON {type:'start',format,language} / {type:'stop'}
    # 用 asyncio.Queue 把音频帧喂 provider.stream()，把 yield 的 {text,final} 发回 ws
    # ffmpeg 流式转码子进程（webm→pcm）在此管理；stop/断开时清理
```
- 端口同 50059（已 expose、CORS 已开、HMI `VITE_AUDIO_API_URL` 已指向）。
- **批处理 `/api/asr` 一字不动**（回退路径）。

### 4.3 env（`.env.example` + compose 注入 llm-gateway）
| key | 默认 | 说明 |
|---|---|---|
| `ASR_STREAM_PROVIDER` | `dashscope` | `dashscope｜mimo-chunked｜off`（dashscope 走 OpenAI Realtime 协议） |
| `DASHSCOPE_ASR_KEY` | `${LLM_EMBED_API_KEY}` | 百炼 key（复用 embedding 的；已验证鉴权通过） |
| `DASHSCOPE_ASR_WS_URL` | `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` | **OpenAI 兼容 Realtime WS**（model 走 query 参数） |
| `ASR_STREAM_MODEL` | `Qwen3-ASR-Flash-Realtime-2026-02-10` | 备选 `fun-asr-realtime`（同端点连通） |

## 5. 前端落地（`hmi`）

- `audio.ts` 新增 `class StreamingRecognizer`：开 WS→`{type:'start'}`→`MediaRecorder` 以 `timeslice=250ms` 产帧、`ondataavailable` 把 blob 推 WS→收 `{text,final}` 回调→`stop()` 发 `{type:'stop'}` 等 final。`recognizeStreamSupported()` 探测（WS 可达 + secureContext）。
- `Composer.tsx`：录音态把 WS 回调的 interim 写进**输入框**（`setInput`，带"识别中"弱样式光标），`final` 定稿；松手后用定稿文本走既有 `send()`。**光球态不变**（录音 speaking / 识别 thinking）。不支持流式或 WS 失败 → 回退现有批处理 `recognize()`（无感降级）。
- 可选：设置页"语音输入"分区加 **ASR 引擎** 选择（实时/经典），写 `meta` 或本地（非必须，P2）。

## 6. 分期

- **P0 后端**：流式 provider（DashScope qwen3/fun + mimo-chunked 回退）+ 网关 WS 端点 + env + **DashScope 连通性探针**（先验证实时 ASR 协议/模型可用，再据实调整）。重建 llm-gateway。
- **P1 前端**：`StreamingRecognizer` + Composer interim 上屏 + 批处理无感回退。重建 hmi。
- **P2 打磨**：设置引擎切换、partial 去抖/标点、断流重连、车机 HTTPS 验收。

## 7. 风险与降级

- **DashScope 实时协议**：`qwen3-asr-flash-realtime`/`fun-asr-realtime` 为较新模型，WS 协议/参数以**探针实测**为准（先 `paraformer-realtime-v2` 验证 run-task/result-generated/task-finished 三段握手，再换模型名）。
- **webm→PCM 流式转码**：ffmpeg 读连续 webm/opus stdin 出 PCM；若个别浏览器容器不可流式增量解码 → 回退 `mimo-chunked` 或要求 PCM 采集（P2）。
- **成本/抖动**：DashScope 实时按音频时长计费（比 mimo-chunked 省）；partial 抖动靠"只增不大改"渲染缓解。
- **车机安全上下文**：`getUserMedia` 仍需 HTTPS/localhost（同批处理，既有提示）。
- **回退保证**：任一环失败 → 无感回退批处理 `/api/asr`，不影响可用性。

## 8. 验证

1. **连通性探针**：脚本用百炼 key 连 DashScope 实时 WS，推一段 PCM（静音/正弦或真音频），确认 `task-started/result-generated/task-finished` 与文本返回；逐个验 qwen3/fun/paraformer。
2. **后端 e2e**：起栈→WS `/api/asr/stream` 推 webm 帧→收 interim/final。
3. **前端**：localhost 录音→输入框增量上屏→松手定稿发送；断 WS 回退批处理。
4. 自检：`llm-gateway/tests` 加流式 provider 单测（mock WS）；HMI `npm test`+`build`。
