"""M4 P3 灰度门槛评测：S2S 移交判定准确率（RFC §5.3-3 / §6.2）。

量的是灰度扩域的**硬指标**——漏移交率（该交给确定性链的却口头答应了）。安全上 S2S 面
没有执行通道所以不会出事故，但「说了没做」是确定的体验事故（§5.3 三道背板的第三道）。

配置**生产同源**：直接用 `s2s.protocol.escalate_tool()` 与 `persona()`，不写评测专用
prompt——M1a 探针的教训是「探的必须是生产形态」，否则评测数字与线上无关。

默认走**文本注入**路径（快、可重复、聚焦判定质量本身）；`--audio` 走真实音频路径
（经 TTS 合成再喂回，含转写误差，与线上完全一致但慢 ~6 倍）。两者判定逻辑同源——
工具选择基于语义，文本路径的数字是判定质量的上界。

前置：根 .env 带 DashScope key；`--audio` 另需 `make up`（要 /api/tts）。
用法：python test/eval_s2s_escalation.py [--audio] [--limit N] [--model M] [--desc "..."]
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT), str(_ROOT / "llm-gateway"), str(_ROOT / "test")):
    if p not in sys.path:
        sys.path.insert(0, p)

from e2e_s2s_probe import _api_key, synth_pcm16k  # noqa: E402
from s2s import protocol as P  # noqa: E402  ← 生产同源的工具定义与 persona

CORPUS = _ROOT / "test" / "eval_corpus" / "s2s_escalation_cases.yaml"
WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
# 灰度门槛（RFC §9 P3）：漏移交率达标才扩下一域
RECALL_GATE = 0.95


def load_cases() -> list[dict]:
    import yaml
    rows = yaml.safe_load(CORPUS.read_text(encoding="utf-8")) or []
    out = []
    for r in rows:
        if not isinstance(r, dict) or "text" not in r:
            continue
        out.append({"text": r["text"], "expect": r.get("expect", "self_answer"),
                    "tag": r.get("tag", "")})
    return out


class Judge:
    """一条 realtime 会话，逐句判「是否调 escalate」。每句独立上下文（不互相污染）。"""

    def __init__(self, key: str, model: str, desc_override: str = ""):
        self.key = key
        self.model = model
        self.desc_override = desc_override
        self._eid = 0

    def _tool(self) -> dict:
        t = P.escalate_tool()
        if self.desc_override:
            t["description"] = self.desc_override  # §6.2 灰度调参：收放边界即换描述
        return t

    async def _send(self, ws, obj):
        self._eid += 1
        await ws.send_json({"event_id": f"ev{self._eid}", **obj})

    async def _open(self, ws, *, audio: bool):
        import aiohttp
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            msg = await ws.receive(timeout=15)
            if msg.type == aiohttp.WSMsgType.TEXT and \
                    json.loads(msg.data).get("type") == "session.created":
                break
        sess = {
            "modalities": ["text", "audio"] if audio else ["text"],
            "instructions": P.persona(),
            "tools": [self._tool()],
            "tool_choice": "auto",
        }
        if audio:
            sess.update({"input_audio_format": "pcm16", "sample_rate": 16000,
                         "turn_detection": {"type": "server_vad", "threshold": 0.2,
                                            "silence_duration_ms": 800}})
        await self._send(ws, {"type": "session.update", "session": sess})
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            msg = await ws.receive(timeout=15)
            if msg.type == aiohttp.WSMsgType.TEXT:
                m = json.loads(msg.data)
                if m.get("type") == "session.updated":
                    echoed = (m.get("session") or {}).get("tools")
                    names = [x.get("name") for x in echoed] if isinstance(echoed, list) else []
                    if "escalate" not in names:
                        raise RuntimeError(f"model {self.model} 丢弃 tools（{names}）")
                    return
                if m.get("type") == "error":
                    raise RuntimeError(str(m.get("error"))[:200])
        raise RuntimeError("未收到 session.updated")

    async def judge(self, text: str, *, audio: bool) -> dict:
        """返回 {escalated, utterance, answer, transcript, ms}。"""
        import aiohttp
        r = {"escalated": False, "utterance": "", "answer": "", "transcript": "", "ms": 0.0}
        t_start = time.monotonic()
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{WS_URL}?model={self.model}",
                                    headers={"Authorization": f"Bearer {self.key}"},
                                    heartbeat=20.0, max_msg_size=16 * 1024 * 1024) as ws:
                await self._open(ws, audio=audio)
                if audio:
                    pcm = synth_pcm16k(text)
                    import base64
                    step = 3200
                    for i in range(0, len(pcm), step):
                        await self._send(ws, {"type": "input_audio_buffer.append",
                                              "audio": base64.b64encode(pcm[i:i + step]).decode()})
                    sil = base64.b64encode(b"\x00" * step).decode()
                    for _ in range(13):
                        await self._send(ws, {"type": "input_audio_buffer.append", "audio": sil})
                else:
                    await self._send(ws, {"type": "conversation.item.create", "item": {
                        "type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": text}]}})
                    await self._send(ws, {"type": "response.create"})
                t0 = time.monotonic()
                while time.monotonic() - t0 < 60:
                    try:
                        msg = await ws.receive(timeout=60 - (time.monotonic() - t0))
                    except asyncio.TimeoutError:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    m = json.loads(msg.data)
                    t = m.get("type", "")
                    if "input_audio_transcription" in t and t.endswith(".completed"):
                        r["transcript"] = m.get("transcript") or ""
                    elif t.endswith("audio_transcript.delta") or t == "response.text.delta":
                        r["answer"] += m.get("delta") or ""
                    elif t in ("response.audio_transcript.done", "response.text.done"):
                        r["answer"] = m.get("transcript") or m.get("text") or r["answer"]
                    elif t == "response.function_call_arguments.done":
                        r["escalated"] = m.get("name") == "escalate"
                        try:
                            r["utterance"] = (json.loads(m.get("arguments") or "{}")
                                              or {}).get("utterance", "")
                        except Exception:
                            pass
                    elif t == "response.done":
                        break
                    elif t == "error":
                        r["error"] = str(m.get("error"))[:160]
                        break
        r["ms"] = (time.monotonic() - t_start) * 1000
        return r


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", action="store_true", help="走真实音频路径（含转写误差）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="qwen3.5-omni-flash-realtime")
    ap.add_argument("--desc", default="", help="覆盖 escalate 描述（§6.2 灰度调参对照）")
    args = ap.parse_args()

    key = _api_key()
    if not key:
        print("SKIP：无 DashScope key")
        return 0
    try:
        import aiohttp, yaml  # noqa: F401
    except ImportError as e:
        print(f"SKIP：缺依赖（{e}）")
        return 0
    cases = load_cases()
    if args.limit:
        cases = cases[:args.limit]
    if args.audio:
        try:
            synth_pcm16k("测试")
        except Exception as e:
            print(f"SKIP：--audio 需 make up 起 /api/tts（{e}）")
            return 0

    print(f"S2S 移交判定评测  model={args.model}  path={'audio' if args.audio else 'text'}  "
          f"{len(cases)} 条")
    if args.desc:
        print(f"  （escalate 描述已覆盖：{args.desc[:60]}…）")

    judge = Judge(key, args.model, args.desc)
    rows = []
    for i, c in enumerate(cases, 1):
        r = {}
        # provider 侧偶发错误（实测音频路径连续跑会撞 "algorithm server connection
        # closed"）重试一次；仍失败则标 error 并**排除出指标分母**——把调用失败算成
        # 「模型选择自答」会让指标彻底失真：错误越多，自答准确率越高。
        for attempt in (1, 2):
            try:
                r = await judge.judge(c["text"], audio=args.audio)
            except Exception as e:
                r = {"escalated": False, "answer": "", "utterance": "", "transcript": "",
                     "ms": 0.0, "error": f"{type(e).__name__}: {e}"[:120]}
            if not r.get("error"):
                break
            if attempt == 1:
                await asyncio.sleep(2.0)
        if r.get("error"):
            rows.append({**c, "got": "error", "ok": None, **r})
            print(f"  [!] {i:2}/{len(cases)} {c['text'][:26]:<28} → error        "
                  f"{r['error'][:70]}（不计入指标）")
            continue
        got = "escalate" if r["escalated"] else "self_answer"
        ok = got == c["expect"]
        rows.append({**c, "got": got, "ok": ok, **r})
        mark = "✓" if ok else "✗"
        extra = r["utterance"] if r["escalated"] else r["answer"][:36]
        print(f"  [{mark}] {i:2}/{len(cases)} {c['text'][:26]:<28} → {got:<12} {extra}")

    # 指标（error 条目不进分母）
    errs = [r for r in rows if r["got"] == "error"]
    valid = [r for r in rows if r["got"] != "error"]
    esc = [r for r in valid if r["expect"] == "escalate"]
    slf = [r for r in valid if r["expect"] == "self_answer"]
    esc_hit = sum(1 for r in esc if r["ok"])
    slf_hit = sum(1 for r in slf if r["ok"])
    recall = esc_hit / len(esc) if esc else 0.0
    miss = [r for r in esc if not r["ok"]]
    false_esc = [r for r in slf if not r["ok"]]

    print("\n" + "=" * 72)
    print(f"移交率（应交且交了）      {esc_hit}/{len(esc)} = {recall:.1%}   "
          f"← 灰度门槛 ≥{RECALL_GATE:.0%}（硬指标）")
    print(f"自答正确率（应答且答了）  {slf_hit}/{len(slf)} = "
          f"{(slf_hit/len(slf) if slf else 0):.1%}   ← 只入基线，不设硬指标")
    print(f"总体（有效样本）          {esc_hit + slf_hit}/{len(valid)} = "
          f"{((esc_hit + slf_hit)/len(valid) if valid else 0):.1%}")
    if errs:
        print(f"provider 错误             {len(errs)}/{len(rows)} 条（重试后仍失败，"
              f"已排除出分母）：{[r['tag'] for r in errs]}")
    if valid:
        avg = sum(r["ms"] for r in valid) / len(valid)
        print(f"平均单轮耗时              {avg:.0f} ms")
    # 错误太多则数据不足以判断——不拿残缺样本下结论（同 journeys「漂移即报告作废」口径）
    err_rate = len(errs) / len(rows) if rows else 0
    if err_rate > 0.10:
        print(f"\n✗ provider 错误率 {err_rate:.0%} > 10% ——**本次报告作废**，"
              "有效样本不足以判断移交率；排查 provider 可用性/限流后重跑")
        return 2
    if miss:
        print(f"\n漏移交 {len(miss)} 条（口头答应没办事——体验事故）：")
        for r in miss:
            print(f"  · [{r['tag']}] {r['text']}\n      答：{r['answer'][:70]}")
    if false_esc:
        print(f"\n误移交 {len(false_esc)} 条（多一次主链往返，可接受）：")
        for r in false_esc:
            print(f"  · [{r['tag']}] {r['text']} → utterance={r['utterance'][:40]!r}")

    if recall < RECALL_GATE:
        print(f"\n✗ 漏移交率未达门槛（{recall:.1%} < {RECALL_GATE:.0%}）"
              "——不得扩下一档灰度域；先调 escalate 描述（§6.2）再复评")
        return 1
    print(f"\n✓ 移交率达门槛（{recall:.1%}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
