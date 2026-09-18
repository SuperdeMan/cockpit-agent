"""云栈流式下发节奏探针（只读；2026-09-18，设计 docs/design/2026-09-18-android-stream-text-pacing.md §2）。

用 App / HMI 同一条 WS 契约发一句话，逐帧记录 speech_delta 的到达时刻与字数，最后对账 final.speech 与
流式拼接是否逐字一致（判「是不是客户端再截了一刀」）。读根 `.env` 的 TAILNET_FQDN 与 VITE_WS_TOKEN，
token 不打印；`memory_enabled=false`，不污染用户记忆。会话 id 前缀 `probe-stream-`，collector 里可按它过滤。

    python scripts/probe_stream_cadence.py "给我讲一个很长的故事。" [standard|short|detailed]
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import dev_stack_lib as lib  # noqa: E402

_SENTENCE_END = "。！？!?」”）)…～~"


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


async def run(text: str, answer_length: str) -> None:
    import websockets

    env = _env()
    ep = lib.cloud_endpoints(env["TAILNET_FQDN"])
    token = env.get("VITE_WS_TOKEN", "")
    if not token:
        raise SystemExit("VITE_WS_TOKEN missing in .env")
    session = "probe-stream-" + uuid.uuid4().hex[:6]
    frame = {
        "text": text, "session_id": session, "request_id": uuid.uuid4().hex[:12], "is_confirmation": False,
        "meta": {"assistant_name": "小舟", "memory_enabled": "false", "answer_length": answer_length,
                 "occupant_id": "primary", "occupant_name": "", "trace_id": uuid.uuid4().hex[:16], "input_source": "text"},
    }
    async with websockets.connect(f"{ep.edge_ws}?token={token}", max_size=8 * 1024 * 1024, open_timeout=20) as ws:
        t0 = time.perf_counter()
        await ws.send(json.dumps(frame, ensure_ascii=False))
        print(f"session={session} text={text!r} answer_length={answer_length}")
        deltas: list[str] = []
        gaps: list[float] = []
        first = last = None
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            now = time.perf_counter()
            kind = msg.get("type")
            if kind == "speech_delta":
                d = msg.get("delta") or ""
                deltas.append(d)
                if first is None:
                    first = now
                else:
                    gaps.append((now - last) * 1000)
                print(f"+{(now - t0) * 1000:7.0f}ms  delta len={len(d):3d} gap={(now - last) * 1000 if last else 0:6.1f}ms  {d!r}")
                last = now
            elif kind == "final":
                streamed = "".join(deltas)
                speech = msg.get("speech") or ""
                print(f"+{(now - t0) * 1000:7.0f}ms  FINAL speech_len={len(speech)} streamed_len={len(streamed)} deltas={len(deltas)} "
                      f"first_delta=+{((first or now) - t0) * 1000:.0f}ms")
                if gaps:
                    g = sorted(gaps)
                    span = (last - first) if first and last else 0.0
                    print(f"  gaps ms: p50={g[len(g) // 2]:.0f} p90={g[int(len(g) * 0.9)]:.0f} max={g[-1]:.0f}  "
                          f"delta chars mean={len(streamed) / len(deltas):.1f}  stream span={span * 1000:.0f}ms  "
                          f"rate={len(streamed) / max(span, 0.001):.1f} chars/s")
                print("  final == streamed:", speech == streamed)
                print("  ends with sentence punctuation:", bool(speech.strip()) and speech.strip()[-1] in _SENTENCE_END)
                print("  speech tail:", repr(speech[-60:]))
                return
            elif kind == "error":
                print(f"+{(now - t0) * 1000:7.0f}ms  ERROR {msg}")
                return
            elif kind in ("process", "action"):
                print(f"+{(now - t0) * 1000:7.0f}ms  {kind}: {json.dumps(msg, ensure_ascii=False)[:160]}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else "给我讲一个很长的故事。",
                    sys.argv[2] if len(sys.argv) > 2 else "standard"))
