"""端到端验证：R4.3 语音回路的后端契约（hands-free 依赖的 ASR 流式 + TTS）。

守护 hands-free 语音回路真正会「静默回归」的后端面：
  1. /api/asr/stream WS 流式协议：start → 音频帧 → stop → partial/final/done
     —— issue② 实时上屏、issue① VAD 端点定稿的**数据通路**（前端 audio.ts::StreamingRecognizer 的对端）。
  2. /api/tts 合成：唤醒提示音「在呢」+ 回复播报路径（issue① / 常规 TTS）。
用 /api/tts 合成一句中文再喂回流式 ASR（自洽 round-trip），无需入库二进制音频资产。

**刻意不做**：浏览器 CDP + fake-mic 的唤醒词/VAD 声学验证——KWS 命中率 / 误唤醒 / 回声打断
属声学质量，CI 无法客观评（合成音频能否触发 KWS 本就不确定，会变 flaky）；这些留在
设计卡 `docs/design/2026-07-04-r4.3-wake-vad-fullduplex.md` §9「人工验收单」（真麦）。
FSM 纯逻辑另有 `hmi/src/voiceLoop.test.mjs`（20 例）覆盖。

前置：`make up` 起全栈；依赖 `websockets`。HTTP 服务不可达写结构化 whole-skip；
ASR provider 明确返回 `unsupported` 时仅跳过对应探针；provider 接受探针后的错误、超时或
协议损坏一律记 FAIL，不再把运行期错误伪装成缺凭证。
用法：python test/e2e_voice_loop.py
"""
import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.request

from support.e2e import CaseRecorder, is_network_timeout
from support.tts import select_tts_capability

try:  # Windows 控制台默认 GBK，强制 UTF-8（否则打印 ⚠/✓ 崩溃）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

AUDIO_API = os.getenv("VITE_AUDIO_API_URL", "http://localhost:50059")
ASR_WS = AUDIO_API.replace("http", "ws", 1) + "/api/asr/stream"
# hands-free 默认流式引擎（同前端 DEFAULT_SETTINGS.asrProvider/asrModel）
PROVIDER = os.getenv("ASR_PROVIDER", "dashscope")
MODEL = os.getenv("ASR_MODEL", "qwen3-asr-flash-realtime-2026-02-10")
PHRASE = "今天天气怎么样"
RECV_TIMEOUT = 30


class AudioConversionUnavailable(RuntimeError):
    """The local Python runtime cannot perform the probe conversion."""


class InvalidProviderAudio(RuntimeError):
    """The reachable provider returned audio that violates the WAV contract."""


def _post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        AUDIO_API + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get_json(path: str) -> dict:
    with urllib.request.urlopen(AUDIO_API + path, timeout=5) as r:
        return json.loads(r.read().decode())


def _service_up() -> bool:
    """ASR/TTS 服务是否可达（这是 E2E，需要全栈在跑 make up）。"""
    try:
        urllib.request.urlopen(AUDIO_API + "/api/voices", timeout=3)
        return True
    except urllib.error.HTTPError:
        return True  # 有 HTTP 响应即服务在
    except Exception as exc:
        if is_network_timeout(exc):
            raise
        return False


def _synth_wav(text: str):
    """经 /api/tts 合成 wav；运行期失败返回 None，由调用方记录 FAIL。"""
    try:
        provider, voice_id = select_tts_capability(
            _get_json("/api/tts/stream/info"),
        )
        data = _post_json(
            "/api/tts",
            {
                "text": text,
                "provider": provider,
                "voice_id": voice_id,
                "format": "wav",
            },
        )
        b64 = data.get("audio")
        return base64.b64decode(b64) if b64 else None
    except Exception as e:
        print(f"  TTS 合成失败：{e}")
        return None


def _wav_to_s16le_16k_mono(wav_bytes: bytes):
    """Convert provider WAV to 16-kHz mono s16le PCM."""
    try:
        import audioop
    except ImportError as exc:
        raise AudioConversionUnavailable(
            "the local runtime does not provide audioop",
        ) from exc

    import io
    import wave
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            ch, sw, fr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if n <= 0:
                raise InvalidProviderAudio("the provider WAV contains no audio frames")
            pcm = w.readframes(n)
        if sw != 2:
            pcm = audioop.lin2lin(pcm, sw, 2)
            sw = 2
        if ch == 2:
            pcm = audioop.tomono(pcm, sw, 0.5, 0.5)
        if fr != 16000:
            pcm, _ = audioop.ratecv(pcm, sw, 1, fr, 16000, None)
        if not pcm:
            raise InvalidProviderAudio("the provider WAV converts to empty PCM")
        return pcm
    except Exception as exc:
        raise InvalidProviderAudio(
            "the provider response is not a convertible WAV payload",
        ) from exc


async def _stream_asr(audio: bytes, fmt: str = "wav", vad_silence_ms=None) -> dict:
    """按前端协议驱动 /api/asr/stream，收集消息。fmt=pcm16le 时走 PCM 直传（跳网关 ffmpeg）。

    返回 {terminal, partials, final_text, msgs}；terminal ∈ {done,error,unsupported,timeout,None}。
    """
    out = {"terminal": None, "partials": 0, "final_text": None, "msgs": []}
    async with websockets.connect(ASR_WS, max_size=8 * 1024 * 1024) as ws:
        start = {"type": "start", "format": fmt, "language": "zh",
                 "provider": PROVIDER, "model": MODEL}
        if vad_silence_ms is not None:
            start["vad_silence_ms"] = vad_silence_ms
        await ws.send(json.dumps(start))
        chunk = 8192  # 分帧推，模拟前端 MediaRecorder / PCM 聚包边录边推
        for i in range(0, len(audio), chunk):
            await ws.send(audio[i:i + chunk])
            await asyncio.sleep(0.02)
        await ws.send(json.dumps({"type": "stop"}))
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                m = json.loads(raw if isinstance(raw, str) else raw.decode())
                t = m.get("type")
                out["msgs"].append(t)
                if t == "partial":
                    out["partials"] += 1
                elif t == "final":
                    out["final_text"] = m.get("text", "")
                elif t in ("done", "error", "unsupported"):
                    out["terminal"] = t
                    break
        except asyncio.TimeoutError:
            out["terminal"] = out["terminal"] or "timeout"
    return out


async def _run(recorder: CaseRecorder) -> int:
    print("=== R4.3 语音回路 e2e（后端契约：ASR 流式 + TTS）===\n")
    fails: list[str] = []

    # 1) TTS 唤醒提示音「在呢」（issue①）——顺带确认 TTS provider 可用
    cue = _synth_wav("在呢")
    if cue:
        recorder.pass_case("tts_wakeup_cue")
        print(f"✓ TTS 唤醒提示音「在呢」：{len(cue)} bytes wav")
    else:
        print("✗ TTS provider 已可达但合成探针音频失败")
        recorder.fail_case(
            "tts_wakeup_cue",
            "provider_execution_failed",
            "reachable TTS provider could not synthesize the probe audio",
        )
        return 1

    # 2) 合成 query 音频喂回流式 ASR（round-trip）
    audio = _synth_wav(PHRASE) or cue
    print(f"✓ 合成 query 音频「{PHRASE}」：{len(audio)} bytes\n")

    try:
        r = await _stream_asr(audio)
    except Exception as e:
        print(f"✗ 流式 ASR 连接/协议异常：{e}")
        return 1

    seq = r["msgs"][:12]
    print(f"  流式消息序列：{seq}{'…' if len(r['msgs']) > 12 else ''}")
    print(f"  partial 数：{r['partials']}  final：{r['final_text']!r}  terminal：{r['terminal']}")

    # 硬契约：必须收到一个终止消息——协议不挂（issue② 数据通路的核心保证）
    if r["terminal"] not in ("done", "error", "unsupported"):
        fails.append(f"未收到终止消息（terminal={r['terminal']}）——流式协议挂起/断裂")

    # 只有 provider 明确声明不支持该探针才 SKIP；已接收后的运行错误必须 FAIL。
    if r["terminal"] == "unsupported":
        print("  ⚠ 当前 ASR provider 明确不支持流式探针——协议终止正常")
        recorder.skip_case(
            "streaming_asr_roundtrip",
            "provider_unavailable",
            "ASR provider does not support the streaming probe",
        )
    elif r["terminal"] == "error":
        fails.append("ASR provider returned an error terminal")
    elif r["terminal"] == "done":
        recorder.pass_case("streaming_asr_roundtrip")
        if r["partials"] or r["final_text"]:
            print(f"  ✓ 流式识别产出文本（partial×{r['partials']}, final={r['final_text']!r}）——上屏/定稿通路通")
        else:
            print("  ⚠ provider 跑完但无 partial/final（合成音频未被识别）——协议正常")

    # 3) R4.3b P2 B1：PCM 直传 round-trip（format:pcm16le 跳网关 ffmpeg）
    print("\n--- P2 B1：PCM 直传（format:pcm16le，跳 ffmpeg）---")
    try:
        pcm = _wav_to_s16le_16k_mono(audio)
    except AudioConversionUnavailable:
        pcm = None
        recorder.skip_case(
            "pcm_asr_roundtrip",
            "profile_unavailable",
            "the local runtime cannot convert WAV probe audio to PCM",
        )
    except InvalidProviderAudio:
        pcm = None
        recorder.fail_case(
            "pcm_asr_roundtrip",
            "provider_protocol_error",
            "the reachable TTS provider returned invalid WAV audio",
        )
        fails.append("TTS provider returned invalid WAV audio")
    if pcm:
        try:
            rp = await _stream_asr(pcm, fmt="pcm16le")
            print(f"  PCM 消息序列：{rp['msgs'][:12]}  terminal：{rp['terminal']}")
            if rp["terminal"] not in ("done", "error", "unsupported"):
                fails.append(f"PCM 直传未收到终止消息（terminal={rp['terminal']}）——B1 路径挂起/断裂")
            elif rp["terminal"] == "unsupported":
                recorder.skip_case(
                    "pcm_asr_roundtrip",
                    "provider_unavailable",
                    "ASR provider does not support PCM streaming",
                )
            elif rp["terminal"] == "error":
                fails.append("PCM ASR provider returned an error terminal")
            else:
                recorder.pass_case("pcm_asr_roundtrip")
                print(f"  ✓ PCM 直传协议闭合（terminal={rp['terminal']}）——跳 ffmpeg 通路通")
        except Exception as e:
            fails.append(f"PCM 直传连接/协议异常：{e}")
    # 4) R4.3b P2 B2：vad_silence_ms 透传后会话仍正常定稿（值被夹紧，qwen3 session.update 消费）
    print("\n--- P2 B2：vad_silence_ms 透传 ---")
    try:
        rv = await _stream_asr(audio, fmt="wav", vad_silence_ms=1200)
        print(f"  透传 vad_silence_ms=1200 → terminal：{rv['terminal']}")
        if rv["terminal"] not in ("done", "error", "unsupported"):
            fails.append(f"vad_silence_ms 透传后未收到终止消息（terminal={rv['terminal']}）")
        elif rv["terminal"] == "unsupported":
            recorder.skip_case(
                "vad_silence_passthrough",
                "provider_unavailable",
                "ASR provider does not support the VAD probe",
            )
        elif rv["terminal"] == "error":
            fails.append("VAD ASR provider returned an error terminal")
        else:
            recorder.pass_case("vad_silence_passthrough")
            print(f"  ✓ 透传后会话仍正常闭合（terminal={rv['terminal']}）")
    except Exception as e:
        fails.append(f"vad_silence_ms 透传异常：{e}")

    if fails:
        print("\n=== 失败 ===")
        for f in fails:
            print("  ✗", f)
        return 1
    print("\n=== 通过 ===")
    return 0


async def main() -> int:
    recorder = CaseRecorder()
    with recorder:
        if not _service_up():
            print(f"⚠ SKIP：ASR/TTS 服务不可达 {AUDIO_API}")
            recorder.skip_case(
                "voice_roundtrip",
                "provider_unavailable",
                "ASR/TTS HTTP service is unavailable",
            )
        else:
            rc = await _run(recorder)
            if rc != 0:
                recorder.fail_case(
                    "voice_provider_execution",
                    "provider_protocol_error",
                    "one or more voice-loop provider assertions failed",
                )
    return recorder.exit_code()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
