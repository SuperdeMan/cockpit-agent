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

前置：`make up` 起全栈；依赖 `websockets`。无 ASR/TTS provider 凭据（如 nightly mock）时
相关断言优雅 SKIP（退出 0），不误报失败——沿用 r3.3 e2e-ci-gate 口径。
用法：python test/e2e_voice_loop.py
"""
import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.request

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


def _post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        AUDIO_API + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _service_up() -> bool:
    """ASR/TTS 服务是否可达（这是 E2E，需要全栈在跑 make up）。"""
    try:
        urllib.request.urlopen(AUDIO_API + "/api/voices", timeout=3)
        return True
    except urllib.error.HTTPError:
        return True  # 有 HTTP 响应即服务在
    except Exception:
        return False


def _synth_wav(text: str):
    """经 /api/tts 合成 wav（供喂回流式 ASR）；失败/无凭据返回 None。"""
    try:
        data = _post_json("/api/tts", {"text": text, "voice_id": "冰糖", "format": "wav"})
        b64 = data.get("audio")
        return base64.b64decode(b64) if b64 else None
    except Exception as e:
        print(f"  TTS 合成失败：{e}")
        return None


async def _stream_asr(audio: bytes) -> dict:
    """按前端协议驱动 /api/asr/stream，收集消息。

    返回 {terminal, partials, final_text, msgs}；terminal ∈ {done,error,unsupported,timeout,None}。
    """
    out = {"terminal": None, "partials": 0, "final_text": None, "msgs": []}
    async with websockets.connect(ASR_WS, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "type": "start", "format": "wav", "language": "zh",
            "provider": PROVIDER, "model": MODEL,
        }))
        chunk = 8192  # 分帧推，模拟前端 MediaRecorder 边录边推
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


async def main() -> int:
    print("=== R4.3 语音回路 e2e（后端契约：ASR 流式 + TTS）===\n")
    if not _service_up():
        print(f"⚠ SKIP：ASR/TTS 服务不可达 {AUDIO_API}（先 make up）")
        return 0

    fails: list[str] = []

    # 1) TTS 唤醒提示音「在呢」（issue①）——顺带确认 TTS provider 可用
    cue = _synth_wav("在呢")
    if cue:
        print(f"✓ TTS 唤醒提示音「在呢」：{len(cue)} bytes wav")
    else:
        print("⚠ SKIP：TTS 不可用（无 provider 凭据？）——跳过流式 ASR round-trip（mock/CI 预期）")
        print("\n=== 完成（SKIP）===")
        return 0

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

    # provider 层（凭据/网络/ffmpeg）问题只 ⚠ 不判失败——沿用 r3.3：CI mock 下优雅降级
    if r["terminal"] == "unsupported":
        print("  ⚠ provider build 返回 None（检查 ASR_PROVIDER）——协议本身正常")
    elif r["terminal"] == "error":
        print("  ⚠ provider 运行出错（多为缺凭据/网络/ffmpeg）——协议正常，不校验转写内容")
    elif r["terminal"] == "done":
        if r["partials"] or r["final_text"]:
            print(f"  ✓ 流式识别产出文本（partial×{r['partials']}, final={r['final_text']!r}）——上屏/定稿通路通")
        else:
            print("  ⚠ provider 跑完但无 partial/final（合成音频未被识别）——协议正常")

    if fails:
        print("\n=== 失败 ===")
        for f in fails:
            print("  ✗", f)
        return 1
    print("\n=== 通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
