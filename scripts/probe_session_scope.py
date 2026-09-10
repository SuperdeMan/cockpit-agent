"""受限 scope 身份的**只读**端到端探针（AR05 V05/V07 · AR10 X08）。

发一条普通用户帧，收全下行到 `final`，把四件事**分开**报：
执行了几个动作（`actions`）、播报了多少字、有没有卡片、final 里的 issue code 是什么。

为什么要有它：`GET /api/session` 只能告诉你「摘要面怎么说」，
而「请求面到底怎么处置」是另一回事——本仓 2026-09-10 实测到两者不一致：
摘要面写着 `navigation → unauthorized / scope_missing`，请求面却回
「已为你规划路线前往广州塔」而 `actions: []`（说的和做的矛盾），
或者把内部错误串 `unsupported datetime format` 原样吐给用户。
判据只能来自请求面，所以要这条探针。

⚠ 语料**必须只读**。不要拿它发车控/商户写/支付语料：
真的没被闸住时它会改动真实状态，而那需要精确到用例的单独授权。

⚠ token 只从文件读，不接受命令行传值——命令行会进 shell 历史与 `ps`。

用法：
    python scripts/probe_session_scope.py <token文件> <fqdn> [语料]
"""
from __future__ import annotations

import asyncio
import json
import ssl
import sys
import time
import uuid
from pathlib import Path

import websockets

UTTERANCE_DEFAULT = "深圳今天天气怎么样"
TIMEOUT_S = 60


async def main() -> int:
    token = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    fqdn = sys.argv[2]
    utterance = sys.argv[3] if len(sys.argv) > 3 else UTTERANCE_DEFAULT
    url = f"wss://{fqdn}:8443/ws?token={token}"
    session_id = "app-neg" + uuid.uuid4().hex[:6]
    frame = {
        "text": utterance,
        "session_id": session_id,
        "request_id": str(uuid.uuid4()),
        "is_confirmation": False,
        "meta": {
            "assistant_name": "小舟",
            "memory_enabled": "false",
            "occupant_id": "primary",
            "occupant_name": "",
            "trace_id": uuid.uuid4().hex[:16],
        },
    }
    ctx = ssl.create_default_context()
    received: list[dict] = []
    started = time.monotonic()
    async with websockets.connect(url, ssl=ctx, open_timeout=30) as ws:
        await ws.send(json.dumps(frame, ensure_ascii=False))
        while time.monotonic() - started < TIMEOUT_S:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT_S)
            except asyncio.TimeoutError:
                break
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            received.append(msg)
            if msg.get("type") in {"final", "error"}:
                break

    print(f"utterance={utterance}")
    print(f"session_id={session_id}")
    print(f"frames={len(received)}")
    kinds: dict[str, int] = {}
    for m in received:
        kinds[str(m.get("type"))] = kinds.get(str(m.get("type")), 0) + 1
    print("frame_types=" + json.dumps(kinds, ensure_ascii=False))

    # 三条判据分开报，不合成一个「通过」
    actions = [m for m in received if m.get("actions") or m.get("type") == "action"]
    print(f"actions={len(actions)}")
    finals = [m for m in received if m.get("type") == "final"]
    for m in finals:
        text = (m.get("text") or "")[:200]
        print("final_text=" + text.replace("\n", " "))
        for key in ("issue", "issue_code", "reason", "reason_code", "error", "degraded"):
            if key in m:
                print(f"final_{key}=" + json.dumps(m[key], ensure_ascii=False)[:200])
    errs = [m for m in received if m.get("type") == "error"]
    for m in errs:
        print("error=" + json.dumps(m, ensure_ascii=False)[:300])
    speech = "".join(m.get("delta", "") for m in received if m.get("type") == "speech_delta")
    print(f"speech_delta_len={len(speech)}")
    if speech:
        print("speech_head=" + speech[:160].replace("\n", " "))
    for m in received:
        if m.get("type") in {"process", "final", "error"}:
            print("RAW " + json.dumps(m, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
