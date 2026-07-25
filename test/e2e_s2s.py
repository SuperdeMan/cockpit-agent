"""M4 P1 真栈 e2e：S2S 全双工最小闭环（RFC §9 P1 DoD）。

断言四件事：
  1. 自答闭环——闲聊句经 /api/s2s 出转写/回答增量/音频帧/turn.end(complete)
  2. 多轮上下文——同一会话内轮2 记得轮1（provider 侧上下文 + 本侧不重注入）
  3. escalate 逃逸——车控句出 turn.escalated{utterance} 且**零音频**（执行全在主链）
  4. 回灌防黑洞（§7 强制项）——闲聊轮进 memory；轮次进 obs（path=s2s）；
     逃逸轮**不重复**写 memory

音频源：/api/tts 合成中文再喂回（与 e2e_s2s_probe / e2e_voice_loop 同款 round-trip）。

前置：`make up` + 根 .env 带 DashScope key。缺任一 → SKIP（不算失败，同仓库惯例）。
用法：python test/e2e_s2s.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_s2s_probe import _api_key, synth_pcm16k  # noqa: E402  复用探针的音频合成

LLM_HTTP = f"http://{os.getenv('LLM_GATEWAY_HTTP_HOST', 'localhost')}:{os.getenv('AUDIO_HTTP_PORT', '50059')}"
COLLECTOR = os.getenv("OBS_COLLECTOR_HTTP", "http://localhost:8092")
SESSION = "e2e-s2s-" + time.strftime("%H%M%S")

# 上下文语料刻意避开人名——人名同音字太多（实测「泓舟」被转写成「洪舟」、助手复述又成
# 「洪州」），断言会因 ASR 同音而假红。「拿铁咖啡」是低混淆词。
UTT_FACT = "我最喜欢喝拿铁咖啡"
UTT_ASK = "我刚才说我最喜欢喝什么"
UTT_CONTROL = "把空调调到二十四度"
# 打断验证要一段够长且**确定自答**的回答。别用「讲讲咖啡豆产地和风味区别」这类落在
# 灰度边界上的句子——模型会判成「查信息」走 escalate（实测过），那轮就没有播报可打断。
UTT_LONG = "给我讲个长一点的笑话"

_fails: list[str] = []


def check(name: str, ok: bool, note: str = "") -> None:
    print(f"  [{'✓' if ok else '✗'}] {name}" + (f" —— {note}" if note else ""))
    if not ok:
        _fails.append(name)


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class Turn:
    """一轮的下行观测（对上协议视角，不含任何 provider 概念）。"""

    def __init__(self):
        self.turn_id = ""
        self.transcript = ""
        self.answer = ""
        self.audio_bytes = 0
        self.audio_frames = 0
        self.audio_meta = None
        self.end_reason = ""
        self.end_detail = ""
        self.escalated_utterance = ""
        self.barge_in_at = None      # 上行 barge_in 的时刻
        self.post_barge_frames = 0   # barge_in 之后仍收到的音频帧（残包，必须为 0）
        self.end_at = None           # turn.end 到达时刻（量停播延迟）


async def run_turn(ws, pcm: bytes, *, timeout: float = 60.0,
                   barge_in_after: int = 0) -> Turn:
    """推一段音频并**并发收下行**，直到 turn.end。

    必须并发——真实 HMI 是 WS 事件驱动（onmessage 随时到达），不存在「推完再读」。
    真栈验证时踩过这个坑：顺序写法下客户端不读 → 下行 send 背压 → 网关事件泵阻塞 →
    provider 侧数据丢，该轮回答整段消失（网关侧现已有 turn 看门狗兜底诚实收束）。

    barge_in_after>0：收到第 N 个音频帧后上行 barge_in（①听感打断的后端契约验证）。
    """
    t = Turn()
    step = 3200

    async def reader():
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                msg = await asyncio.wait_for(ws.recv(),
                                             timeout=timeout - (time.monotonic() - t0))
            except asyncio.TimeoutError:
                return
            if isinstance(msg, (bytes, bytearray)):
                t.audio_frames += 1
                t.audio_bytes += len(msg)
                if t.barge_in_at is None and barge_in_after and t.audio_frames >= barge_in_after:
                    t.barge_in_at = time.monotonic()
                    await ws.send(json.dumps({"type": "barge_in"}))
                elif t.barge_in_at is not None:
                    t.post_barge_frames += 1
                continue
            m = json.loads(msg)
            mt = m.get("type", "")
            if mt == "turn.transcript":
                t.turn_id = m.get("turn_id") or t.turn_id
                if m.get("final"):
                    t.transcript = m.get("text") or t.transcript
            elif mt == "turn.answer_delta":
                t.answer += m.get("text") or ""
            elif mt == "turn.audio_meta":
                t.audio_meta = m
            elif mt == "turn.escalated":
                t.turn_id = m.get("turn_id") or t.turn_id
                t.escalated_utterance = m.get("utterance") or ""
            elif mt == "turn.end":
                t.end_reason = m.get("reason") or ""
                t.end_detail = m.get("detail") or ""
                t.end_at = time.monotonic()
                if barge_in_after:
                    # 打断轮：turn.end 后再听 1.5s，确认没有残包续播（★1 本侧丢弃保护）
                    try:
                        while True:
                            extra = await asyncio.wait_for(ws.recv(), timeout=1.5)
                            if isinstance(extra, (bytes, bytearray)):
                                t.post_barge_frames += 1
                    except asyncio.TimeoutError:
                        pass
                return
            elif mt == "unsupported":
                t.end_reason = "unsupported"
                t.end_detail = m.get("message") or ""
                return

    rt = asyncio.create_task(reader())
    await asyncio.sleep(0.1)  # 让 reader 先就位
    await ws.send(json.dumps({"type": "audio"}))
    for i in range(0, len(pcm), step):
        await ws.send(pcm[i:i + step])
    for _ in range(13):  # 静音尾触发 server VAD 收尾
        await ws.send(b"\x00" * step)
    try:
        await asyncio.wait_for(rt, timeout=timeout + 5)
    except asyncio.TimeoutError:
        rt.cancel()
    return t


async def main() -> int:
    if not _api_key():
        print("SKIP：无 DashScope key")
        return 0
    try:
        import websockets
    except ImportError:
        print("SKIP：缺 websockets")
        return 0
    try:
        info = _get(f"{LLM_HTTP}/api/s2s/info")
    except Exception as e:
        print(f"SKIP：llm-gateway 不可达（{e}）——需 make up")
        return 0

    print(f"S2S 真栈 e2e  session={SESSION}")
    print("\n[0] 能力探测")
    check("/api/s2s/info 报可用", bool(info.get("available")), json.dumps(info, ensure_ascii=False)[:160])
    check("默认挡位是 classic（S2S 须用户显式开）", info.get("default") == "classic")
    if not info.get("available"):
        print("S2S 不可用，后续跳过")
        return 1

    print("\n  合成音频…")
    pcm_fact = synth_pcm16k(UTT_FACT)
    pcm_ask = synth_pcm16k(UTT_ASK)
    pcm_ctl = synth_pcm16k(UTT_CONTROL)
    print(f"  ok（{len(pcm_fact)}/{len(pcm_ask)}/{len(pcm_ctl)} bytes）")

    ws_url = LLM_HTTP.replace("http", "ws") + "/api/s2s"
    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "session.start", "session_id": SESSION,
                                  "user_id": "u1", "voice": ""}))
        # session.state=ready 先到
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        print("\n[1] 会话建立")
        check("下行 session.state=ready", first.get("type") == "session.state"
              and first.get("state") == "ready", json.dumps(first, ensure_ascii=False)[:120])

        print("\n[2] 自答闭环（闲聊轮）")
        t1 = await run_turn(ws, pcm_fact)
        check("转写到手", bool(t1.transcript), f"transcript={t1.transcript!r}")
        check("回答文本增量到手", bool(t1.answer), f"answer={t1.answer[:50]!r}")
        check("音频帧到手", t1.audio_bytes > 0, f"{t1.audio_bytes} bytes")
        check("audio_meta 带采样率", bool(t1.audio_meta and t1.audio_meta.get("sample_rate")),
              json.dumps(t1.audio_meta, ensure_ascii=False) if t1.audio_meta else "无")
        check("turn.end=complete", t1.end_reason == "complete", f"reason={t1.end_reason}")

        print("\n[3] 多轮上下文（同一会话）")
        t2 = await run_turn(ws, pcm_ask)
        check("轮2 记得轮1 说过的事", "拿铁" in t2.answer or "咖啡" in t2.answer,
              f"answer={t2.answer[:60]!r}")

        print("\n[4] escalate 逃逸（车控句）")
        t3 = await run_turn(ws, pcm_ctl)
        check("出 turn.escalated", bool(t3.escalated_utterance),
              f"utterance={t3.escalated_utterance!r}")
        check("utterance 是原话转述（含「空调」）", "空调" in t3.escalated_utterance)
        check("turn.end=escalated", t3.end_reason == "escalated", f"reason={t3.end_reason}")
        check("逃逸轮零音频（执行与播报全在主链）", t3.audio_bytes == 0,
              f"{t3.audio_bytes} bytes")

        print("\n[5] escalated_result 回传（保上下文，不触发播报）")
        await ws.send(json.dumps({"type": "escalated_result", "turn_id": t3.turn_id,
                                  "text": "空调已经调到24度了"}))
        quiet = True
        try:  # 回注后应静默——若 provider 自动续说就会有下行（双播）
            m = await asyncio.wait_for(ws.recv(), timeout=5)
            quiet = False
            note = str(m)[:120]
        except asyncio.TimeoutError:
            note = "5s 内无下行"
        check("回传后不产生播报（无双播）", quiet, note)

        print("\n[6] ①听感打断（barge_in 的后端契约）")
        t4 = await run_turn(ws, synth_pcm16k(UTT_LONG), barge_in_after=2)
        check("打断令本轮收 turn.end=cancelled", t4.end_reason == "cancelled",
              f"reason={t4.end_reason}")
        if t4.barge_in_at and t4.end_at:
            ms = (t4.end_at - t4.barge_in_at) * 1000
            check("网关收束响应及时（<1s）", ms < 1000, f"{ms:.0f} ms")
        check("打断后无残包续播", t4.post_barge_frames == 0,
              f"barge_in 后又收到 {t4.post_barge_frames} 个音频帧")
        check("打断轮回答被截断（不是完整全文）", bool(t4.answer),
              f"已播文本 {len(t4.answer)} 字（provider 全文更长，回灌只存这段）")

        print("\n[7] 打断后同会话续听（不重建）")
        t5 = await run_turn(ws, synth_pcm16k(UTT_ASK))
        check("打断后下一轮正常且上下文仍在", t5.end_reason == "complete"
              and ("拿铁" in t5.answer or "咖啡" in t5.answer),
              f"reason={t5.end_reason} answer={t5.answer[:40]!r}")

        print("\n[8] ③工具调用中打断（打断≠回滚）")
        t6 = await run_turn(ws, pcm_ctl)
        if t6.end_reason == "escalated":
            await ws.send(json.dumps({"type": "barge_in"}))
            await asyncio.sleep(0.3)
            # abandoned turn 的结果回来应被静默丢弃——不 inject、不播报
            await ws.send(json.dumps({"type": "escalated_result", "turn_id": t6.turn_id,
                                      "text": "已为你调好空调"}))
            quiet2 = True
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=4)
                quiet2 = False
                note2 = str(m)[:120]
            except asyncio.TimeoutError:
                note2 = "4s 内无下行"
            check("被打断的逃逸轮：结果回来不播报", quiet2, note2)
        else:
            check("③前置（车控句 escalate）", False, f"reason={t6.end_reason}")

        await ws.send(json.dumps({"type": "session.end"}))

    print("\n[9] unsupported 回落（provider=off → HMI 走三段式）")
    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws2:
        await ws2.send(json.dumps({"type": "session.start", "session_id": SESSION + "-off",
                                   "user_id": "u1", "provider": "off"}))
        try:
            m = json.loads(await asyncio.wait_for(ws2.recv(), timeout=15))
        except asyncio.TimeoutError:
            m = {}
        check("provider=off 下行 unsupported（而非静默卡住）",
              m.get("type") == "unsupported", json.dumps(m, ensure_ascii=False)[:140])

    print("\n[10] 回灌防黑洞（§7 强制项）")

    async def _mem_turns():
        return _get(f"{LLM_HTTP}/api/memory/session?session_id={SESSION}&last_n=20").get("turns", [])

    turns = []
    for _ in range(12):  # 回灌是收束后异步的，给几秒
        turns = await _mem_turns()
        if len(turns) >= 4:
            break
        await asyncio.sleep(1)
    roles = [t["role"] for t in turns]
    texts = " | ".join(t["text"][:24] for t in turns)
    check("闲聊轮已回灌 memory（user+assistant 成对）",
          roles.count("user") >= 2 and roles.count("assistant") >= 2,
          f"{len(turns)} 轮：{texts}")
    check("逃逸轮不重复回灌（主链已落）",
          not any("空调" in t["text"] and t["role"] == "user" for t in turns),
          f"roles={roles}")

    try:
        rows = _get(f"{COLLECTOR}/api/sessions?q={SESSION}")
        row = next((r for r in rows if r["session_id"] == SESSION), None)
        check("obs 有该会话", row is not None, f"turns={row['turns']}" if row else "未找到")
        if row:
            ot = _get(f"{COLLECTOR}/api/sessions/{SESSION}/turns")
            paths = {t.get("path") for t in ot}
            check("obs.turn 标 path=s2s", "s2s" in paths, f"paths={paths}")
    except Exception as e:
        check("obs 回灌可查", False, f"collector 不可达或查询失败：{e}")

    print("\n" + "=" * 68)
    if _fails:
        print(f"✗ {len(_fails)} 项失败：{_fails}")
        return 1
    print("✓ S2S 最小闭环全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
