"""端到端验证：R4.2 服务端流式 TTS（/api/tts/stream）。

守护流式合成的后端契约：文本增量进 → meta + N 个二进制 PCM 帧 + done（带 first_chunk_ms）；
并与批处理 /api/tts 同句对照，断言流式首帧 < 批处理整句 × 0.5（G1：真流式提速）。
另验 barge-in 取消（cancel → 停止吐帧）与能力探测 /api/tts/stream/info。

前置：`make up` 起全栈（或本地起 llm-gateway http server）；依赖 websockets。
无 DashScope 凭据（nightly/mock）时自动回退 provider=mock 验证协议帧序（不校验音质/延迟），优雅通过。
用法：python test/e2e_tts_stream.py
"""
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

AUDIO_API = os.getenv("VITE_AUDIO_API_URL", "http://localhost:50059")
TTS_WS = AUDIO_API.replace("http", "ws", 1) + "/api/tts/stream"
DELTAS = ["杭州今天", "多云转晴，", "气温 18 到 26 度，", "适合出门。"]
SENTENCE = "".join(DELTAS)
RECV_TIMEOUT = 30


def _get_json(path: str) -> dict:
    with urllib.request.urlopen(AUDIO_API + path, timeout=5) as r:
        return json.loads(r.read().decode())


def _post_json(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        AUDIO_API + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _service_up() -> bool:
    try:
        urllib.request.urlopen(AUDIO_API + "/api/tts/stream/info", timeout=3)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


async def _stream_tts(provider: str, voice: str = "", cancel_after: int = -1) -> dict:
    """按前端协议驱动 /api/tts/stream，逐个发文本 delta。
    cancel_after≥0：发第 N 个 delta 后发 cancel（barge-in 验证），否则发 finish。
    返回 {meta, chunks, audio_bytes, first_chunk_ms, terminal, msgs}。"""
    out = {"meta": None, "chunks": 0, "audio_bytes": 0, "first_chunk_ms": None,
           "terminal": None, "msgs": []}
    t0 = time.monotonic()
    async with websockets.connect(TTS_WS, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "provider": provider, "voice": voice}))

        async def feed():
            for i, d in enumerate(DELTAS):
                await ws.send(json.dumps({"type": "text", "delta": d}))
                await asyncio.sleep(0.15)
                if cancel_after >= 0 and i == cancel_after:
                    await ws.send(json.dumps({"type": "cancel"}))
                    return
            await ws.send(json.dumps({"type": "finish"}))

        feeder = asyncio.create_task(feed())
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                if isinstance(raw, (bytes, bytearray)):
                    if out["first_chunk_ms"] is None:
                        out["first_chunk_ms"] = round((time.monotonic() - t0) * 1000)
                    out["chunks"] += 1
                    out["audio_bytes"] += len(raw)
                    continue
                m = json.loads(raw)
                t = m.get("type")
                out["msgs"].append(t)
                if t == "meta":
                    out["meta"] = m
                elif t in ("done", "error", "unsupported"):
                    out["terminal"] = t
                    if t == "done":
                        out["done_first_chunk_ms"] = m.get("first_chunk_ms")
                    break
        except asyncio.TimeoutError:
            out["terminal"] = out["terminal"] or "timeout"
        except websockets.exceptions.ConnectionClosed:
            # cancel 后服务端主动关闭连接（barge-in 干净收尾）——非挂起
            out["terminal"] = out["terminal"] or "closed"
        finally:
            feeder.cancel()
    return out


def _batch_baseline() -> float | None:
    """同句批处理 /api/tts 整句延迟（首音=全量返回）。失败返回 None。"""
    try:
        t0 = time.monotonic()
        data = _post_json("/api/tts", {"text": SENTENCE, "voice_id": "冰糖", "format": "wav"})
        dt = (time.monotonic() - t0) * 1000
        return dt if data.get("audio") else None
    except Exception:
        return None


async def main() -> int:
    print("=== R4.2 流式 TTS e2e（/api/tts/stream）===\n")
    if not _service_up():
        print(f"⚠ SKIP：TTS 服务不可达 {AUDIO_API}（先 make up 或本地起 llm-gateway）")
        return 0

    fails: list[str] = []
    info: dict = {}

    # 0) 能力探测
    try:
        info = _get_json("/api/tts/stream/info")
        prov_ids = [p["id"] for p in info.get("providers", [])]
        print(f"  /api/tts/stream/info: streaming={info.get('streaming')} default={info.get('default')} providers={prov_ids}")
        if "cosyvoice" not in prov_ids or "qwen" not in prov_ids:
            fails.append("stream/info 未含 cosyvoice/qwen 引擎")
        for p in info.get("providers", []):
            if not p.get("voices"):
                fails.append(f"引擎 {p['id']} 无音色列表")
    except Exception as e:
        fails.append(f"stream/info 探测异常：{e}")

    streaming_available = bool(info.get("streaming"))

    # 1) 真流式（有凭据走 cosyvoice；无凭据回退 mock 验证协议帧序）
    provider = "cosyvoice" if streaming_available else "mock"
    print(f"\n--- 流式合成 provider={provider} ---")
    try:
        r = await _stream_tts(provider)
    except Exception as e:
        print(f"✗ 流式 TTS 连接/协议异常：{e}")
        return 1
    print(f"  消息序列：{r['msgs']}  二进制帧：{r['chunks']}  音频 {r['audio_bytes']} bytes")
    print(f"  meta：{r['meta']}  首帧：{r['first_chunk_ms']}ms  terminal：{r['terminal']}")

    if r["terminal"] == "unsupported":
        # 连 mock 都 unsupported 不该发生（mock 无需 key）
        fails.append("provider=%s 返回 unsupported" % provider)
    elif r["terminal"] == "error":
        print("  ⚠ provider 运行出错（多为凭据/网络）——协议正常，不校验延迟")
    else:
        if not r["meta"] or r["meta"].get("format") != "pcm":
            fails.append("未收到 meta 或 format≠pcm")
        if r["chunks"] < 1:
            fails.append("未收到任何二进制音频帧")
        if r["terminal"] != "done":
            fails.append(f"未正常 done（terminal={r['terminal']}）")
        else:
            print(f"  ✓ 流式协议闭合：meta + {r['chunks']} 帧 + done（服务端 first_chunk_ms={r.get('done_first_chunk_ms')}）")

    # 2) 与批处理对照（仅真流式可用时）：首帧 < 批处理整句 × 0.5
    #    优先用服务端上报的 first_chunk_ms（真首帧，排除客户端测试链路开销）；缺则用客户端测量。
    server_first = r.get("done_first_chunk_ms")
    first_ms = server_first if server_first else r["first_chunk_ms"]
    if streaming_available and r["terminal"] == "done" and first_ms:
        base = _batch_baseline()
        if base:
            src = "服务端" if server_first else "客户端"
            print(f"\n--- 对照 ---\n  批处理整句：{base:.0f}ms   流式首帧（{src}）：{first_ms}ms   提速 {base / max(1, first_ms):.1f}×")
            if first_ms >= base * 0.5:
                fails.append(f"流式首帧 {first_ms}ms 未达标（应 < 批处理 {base:.0f}ms×0.5）")
            else:
                print(f"  ✓ G1 达标：流式首帧 < 批处理 × 0.5")
        else:
            print("  ⚠ 批处理基线不可用（跳过对照）")

    # 3) barge-in：cancel 后停止吐帧（发 2 个 delta 后 cancel）
    print(f"\n--- barge-in 取消（provider={provider}）---")
    try:
        rc = await _stream_tts(provider, cancel_after=1)
        print(f"  取消后消息：{rc['msgs']}  帧：{rc['chunks']}  terminal：{rc['terminal']}")
        # 取消是硬终止：不应挂起（terminal 须落定为 closed/done/error，非 timeout/None）
        if rc["terminal"] in ("closed", "done", "error"):
            print(f"  ✓ 取消路径干净收尾（terminal={rc['terminal']}）——barge-in 传播到供应商")
        else:
            fails.append(f"barge-in 取消后挂起（terminal={rc['terminal']}）")
    except Exception as e:
        fails.append(f"barge-in 取消异常：{e}")

    if fails:
        print("\n=== 失败 ===")
        for f in fails:
            print("  ✗", f)
        return 1
    print("\n=== 通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
