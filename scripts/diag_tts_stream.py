"""P0 前置探针（R4.2 流式 TTS）——真实探测 DashScope（百炼）流式 TTS 协议。

设计文档 §3.1 硬 gate：先验证供应商能力，再动集成代码。对标 ASR 落地时的 diag 先例。

探测两个模型：
  1. cosyvoice-v3-flash —— 猜测走 run-task 协议（/api-ws/v1/inference，与 fun-asr 同壳）
  2. qwen3-tts-flash-realtime —— 猜测走 OpenAI-realtime 协议（/api-ws/v1/realtime，与 qwen3-asr 同壳）

对每个模型验证四件事（全过才进 P1）：
  a) run-task/session 能否 200 开任务；
  b) 增量喂文本 → 服务端边收边回二进制音频帧（不是攒到 finish 才回）；
  c) 输出格式协商：优先 pcm，记录实际采样率；
  d) 测量首帧延迟（开任务→首二进制帧）与整句延迟。

用法：python scripts/diag_tts_stream.py [cosyvoice|qwen|both]
无参数默认 both。从根 .env 读 LLM_EMBED_API_KEY / DASHSCOPE_ASR_KEY。
"""
from __future__ import annotations
import asyncio
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path

import aiohttp

# Windows 控制台默认 GBK，中文/emoji 会 UnicodeEncodeError 崩脚本——强制 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]


def load_key() -> str:
    # 优先环境变量，其次根 .env
    key = os.getenv("DASHSCOPE_ASR_KEY") or os.getenv("LLM_EMBED_API_KEY") or ""
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.split("#")[0].strip()
            if k in ("DASHSCOPE_ASR_KEY", "LLM_EMBED_API_KEY") and v:
                return v
    return ""


TEXT_CHUNKS = ["杭州今天", "多云转晴，", "气温 18 到 26 度，", "适合出门。"]
INFERENCE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
REALTIME_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


def _preview(obj, n=600) -> str:
    s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[:n] + f"…(+{len(s)-n} chars)"


async def probe_cosyvoice(key: str, model: str, voice: str) -> dict:
    """run-task 协议（/api-ws/v1/inference）。"""
    print(f"\n{'='*70}\n[cosyvoice] model={model} voice={voice} — run-task 协议 {INFERENCE_URL}\n{'='*70}")
    task_id = uuid.uuid4().hex
    audio_bytes = bytearray()
    first_frame_t = None
    t0 = None
    events_seen = []
    sample_rate = 22050
    result = {"model": model, "protocol": "run-task", "ok": False}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                INFERENCE_URL, headers={"Authorization": f"bearer {key}"}, heartbeat=20.0,
            ) as ws:
                run_task = {
                    "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                    "payload": {
                        "task_group": "audio", "task": "tts", "function": "SpeechSynthesizer",
                        "model": model,
                        "parameters": {"text_type": "PlainText", "voice": voice,
                                       "format": "pcm", "sample_rate": sample_rate},
                        "input": {},
                    },
                }
                print(f"[send run-task] {_preview(run_task)}")
                t0 = time.monotonic()
                await ws.send_json(run_task)

                started = asyncio.Event()

                async def pump():
                    await started.wait()
                    for i, chunk in enumerate(TEXT_CHUNKS):
                        cont = {"header": {"action": "continue-task", "task_id": task_id, "streaming": "duplex"},
                                "payload": {"input": {"text": chunk}}}
                        print(f"[send continue-task #{i}] text={chunk!r}")
                        await ws.send_json(cont)
                        await asyncio.sleep(0.15)  # 模拟 LLM 增量到达节奏
                    fin = {"header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                           "payload": {"input": {}}}
                    print("[send finish-task]")
                    await ws.send_json(fin)

                pump_task = asyncio.create_task(pump())
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        msg = await ws.receive(timeout=15)
                    except asyncio.TimeoutError:
                        print("[timeout] 15s 无消息")
                        break
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        if first_frame_t is None:
                            first_frame_t = time.monotonic()
                            print(f"  >>  首二进制帧 @ {(first_frame_t - t0)*1000:.0f}ms, {len(msg.data)} bytes")
                        audio_bytes.extend(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        m = json.loads(msg.data)
                        evt = m.get("header", {}).get("event", "")
                        events_seen.append(evt)
                        if evt == "task-started":
                            print(f"[recv task-started] {_preview(m, 300)}")
                            started.set()
                        elif evt == "result-generated":
                            pass  # 增量结果元数据，音频走二进制
                        elif evt == "task-finished":
                            print(f"[recv task-finished] {_preview(m, 400)}")
                            break
                        elif evt == "task-failed":
                            print(f"[recv task-failed] {_preview(m, 600)}")
                            result["error"] = m.get("header", {}).get("error_message", "task-failed")
                            break
                        else:
                            print(f"[recv {evt or '?'}] {_preview(m, 300)}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                        print(f"[ws closed] {msg.type} code={ws.close_code}")
                        break
                pump_task.cancel()
        total_t = time.monotonic() - t0 if t0 else 0
        result.update({
            "ok": len(audio_bytes) > 0,
            "first_frame_ms": round((first_frame_t - t0) * 1000) if first_frame_t else None,
            "total_ms": round(total_t * 1000),
            "audio_bytes": len(audio_bytes),
            "sample_rate": sample_rate,
            "events": events_seen,
        })
        if audio_bytes:
            out = ROOT / "scratchpad_tts_cosyvoice.pcm"
            out.write_bytes(bytes(audio_bytes))
            dur_ms = len(audio_bytes) / (sample_rate * 2) * 1000
            print(f"  [OK] 收到 {len(audio_bytes)} bytes PCM (~{dur_ms:.0f}ms @ {sample_rate}Hz s16le) → {out.name}")
    except Exception as e:
        print(f"  [FAIL] 异常: {type(e).__name__}: {e}")
        result["error"] = f"{type(e).__name__}: {e}"
    return result


async def probe_qwen_realtime(key: str, model: str, voice: str) -> dict:
    """OpenAI-realtime 协议（/api-ws/v1/realtime）——事件名先按猜测发，未知全量打印以读真实协议。"""
    url = f"{REALTIME_URL}?model={model}"
    print(f"\n{'='*70}\n[qwen-tts] model={model} voice={voice} — realtime 协议 {url}\n{'='*70}")
    audio_bytes = bytearray()
    first_frame_t = None
    t0 = None
    events_seen = []
    sample_rate = 24000
    result = {"model": model, "protocol": "realtime", "ok": False}
    eid = [0]

    def ev(o):
        eid[0] += 1
        return {"event_id": f"ev{eid[0]}", **o}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url, headers={"Authorization": f"Bearer {key}"}, heartbeat=20.0,
            ) as ws:
                # 等 session.created
                created = False
                for _ in range(5):
                    msg = await ws.receive(timeout=10)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        m = json.loads(msg.data)
                        print(f"[recv {m.get('type')}] {_preview(m, 400)}")
                        if m.get("type") == "session.created":
                            created = True
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print(f"[ws closed before session.created] code={ws.close_code}")
                        result["error"] = f"closed code={ws.close_code}"
                        return result
                if not created:
                    print("[warn] 未收到 session.created，继续尝试")

                # session.update：协商 voice + pcm 输出。字段名按 Qwen-TTS-Realtime 文档猜测。
                upd = ev({"type": "session.update", "session": {
                    "voice": voice, "response_format": "pcm", "sample_rate": sample_rate,
                    "mode": "server_commit",
                }})
                print(f"[send session.update] {_preview(upd)}")
                t0 = time.monotonic()
                await ws.send_json(upd)

                async def pump():
                    await asyncio.sleep(0.3)
                    for i, chunk in enumerate(TEXT_CHUNKS):
                        a = ev({"type": "input_text_buffer.append", "text": chunk})
                        print(f"[send input_text_buffer.append #{i}] text={chunk!r}")
                        await ws.send_json(a)
                        await asyncio.sleep(0.15)
                    print("[send input_text_buffer.commit + session.finish]")
                    await ws.send_json(ev({"type": "input_text_buffer.commit"}))
                    await ws.send_json(ev({"type": "session.finish"}))

                pump_task = asyncio.create_task(pump())
                deadline = time.monotonic() + 30
                done = False
                while time.monotonic() < deadline and not done:
                    try:
                        msg = await ws.receive(timeout=15)
                    except asyncio.TimeoutError:
                        print("[timeout] 15s 无消息")
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        m = json.loads(msg.data)
                        t = m.get("type", "")
                        events_seen.append(t)
                        # 音频增量：可能在 delta/audio 字段（base64）
                        b64 = m.get("delta") or m.get("audio") or ""
                        if b64 and ("audio" in t or "delta" in t):
                            if first_frame_t is None:
                                first_frame_t = time.monotonic()
                                print(f"  >>  首音频事件 [{t}] @ {(first_frame_t - t0)*1000:.0f}ms")
                            try:
                                audio_bytes.extend(base64.b64decode(b64))
                            except Exception:
                                pass
                        else:
                            print(f"[recv {t}] {_preview(m, 300)}")
                        if t in ("session.finished", "response.done", "session.done", "response.audio.done"):
                            # 收到明确结束标志之一
                            if t in ("session.finished", "response.done", "session.done"):
                                done = True
                        if t == "error":
                            result["error"] = _preview(m.get("error", m), 400)
                            done = True
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        if first_frame_t is None:
                            first_frame_t = time.monotonic()
                            print(f"  >>  首二进制帧 @ {(first_frame_t - t0)*1000:.0f}ms")
                        audio_bytes.extend(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                        print(f"[ws closed] {msg.type} code={ws.close_code}")
                        break
                pump_task.cancel()
        total_t = time.monotonic() - t0 if t0 else 0
        result.update({
            "ok": len(audio_bytes) > 0,
            "first_frame_ms": round((first_frame_t - t0) * 1000) if first_frame_t else None,
            "total_ms": round(total_t * 1000),
            "audio_bytes": len(audio_bytes),
            "sample_rate": sample_rate,
            "events": events_seen,
        })
        if audio_bytes:
            out = ROOT / "scratchpad_tts_qwen.pcm"
            out.write_bytes(bytes(audio_bytes))
            dur_ms = len(audio_bytes) / (sample_rate * 2) * 1000
            print(f"  [OK] 收到 {len(audio_bytes)} bytes PCM (~{dur_ms:.0f}ms @ {sample_rate}Hz s16le) → {out.name}")
    except Exception as e:
        print(f"  [FAIL] 异常: {type(e).__name__}: {e}")
        result["error"] = f"{type(e).__name__}: {e}"
    return result


async def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "both").lower()
    key = load_key()
    if not key:
        print("[FAIL] 无 DashScope key（DASHSCOPE_ASR_KEY / LLM_EMBED_API_KEY）")
        return
    print(f"key: {key[:6]}…{key[-4:]}  ({len(key)} chars)")
    results = []
    if which in ("both", "cosyvoice", "cosy"):
        # cosyvoice-v3-flash 用 v3 专属音色（官方音色表；v2 名会 418）：longxiaochun_v3=龙小淳·女·语音助手
        for voice in ("longxiaochun_v3", "longanyang"):
            r = await probe_cosyvoice(key, "cosyvoice-v3-flash", voice)
            results.append(r)
            if r.get("ok"):
                break
    if which in ("both", "qwen"):
        r = await probe_qwen_realtime(key, "qwen3-tts-flash-realtime", "Cherry")
        results.append(r)

    print(f"\n\n{'#'*70}\n# 探针基线汇总（回填设计文档 §7）\n{'#'*70}")
    for r in results:
        status = "[OK] 可用" if r.get("ok") else "[FAIL] 不可用"
        print(f"\n{status}  {r['model']} ({r['protocol']})")
        print(f"    首帧延迟   : {r.get('first_frame_ms')} ms")
        print(f"    整句延迟   : {r.get('total_ms')} ms")
        print(f"    音频字节   : {r.get('audio_bytes')} @ {r.get('sample_rate')}Hz")
        print(f"    事件序列   : {r.get('events')}")
        if r.get("error"):
            print(f"    错误       : {r['error']}")


if __name__ == "__main__":
    asyncio.run(main())
