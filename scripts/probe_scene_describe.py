"""真栈探针：场景定义问讲场景会做什么、不执行；部件模式问照旧归手册（docs/design/2026-10-05-scene-describe.md）。

只读：每句一个签名的合成 E2E 用户、车辆 v1 的新会话；只发定义问与部件模式问，不发开启 / 确认，不调车控、商户与支付。
场景定义问的回答要带「X模式会：」和该场景的动作；「运动模式是什么意思」不能被讲成场景；「空调有哪些模式」要答空调自己的模式，
不能列回家 / 露营这些场景。每句都断言零动作。前后各核一次 release SHA，写一份 JSON 证据文件。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import probe_history_window as identity  # noqa: E402
from scripts import probe_qa_long_sessions as audit  # noqa: E402
from scripts import probe_qa_regression as wire  # noqa: E402
from scripts.e2e_identity import sign_identity  # noqa: E402
from scripts.render_cloud_env import DEMO_AUTH_SCOPES  # noqa: E402

SCOPES = tuple(s for s in DEMO_AUTH_SCOPES if s not in {"merchant.write", "payment.invoke"})
# 原话 → (话术里必须出现的词组（全都要有）, 话术里不能出现的词)
CASES = {
    "露营模式是什么意思？": (("露营模式会：", "座椅放平"), ()),
    "回家模式会做哪些事": (("回家模式会：", "导航"), ()),
    "午休模式开了会做哪些事": (("午休模式会：",), ()),
    "运动模式是什么意思": ((), ("运动模式会：", "内置的")),        # 部件模式不是场景，也不该答成场景列表
    "空调有哪些模式": ((), ("回家模式", "露营模式")),               # 答空调自己的模式，不列场景
}


async def probe(expected_sha: str, repeat: int) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"release_start": snapshot, "rows": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, _collector, secret = identity._endpoints()
    run_id = "e2e-scenedesc-" + uuid.uuid4().hex[:12]
    index = 0
    for round_no in range(repeat):
        for text, (want, avoid) in CASES.items():
            user = f"{run_id}-q{index}"
            index += 1
            token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES),
                                  timeout_s=900)
            async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
                except asyncio.TimeoutError:
                    pass
                obs = await wire._one_turn(ws, f"{user}-session-1", text, trace_id=uuid.uuid4().hex)
            speech = str(obs.get("speech") or "")
            actions = obs.get("actions") or []
            ok = (all(w in speech for w in want) and not any(a in speech for a in avoid)
                  and bool(speech) and not actions)
            result["rows"].append({"round": round_no, "text": text, "ok": ok, "speech": speech[:200],
                                   "actions": actions})
            print(f"r{round_no} {text:<12} ok={int(ok)} | {speech[:80]}")
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha, args.repeat))
    rows = result.get("rows", [])
    summary = {"ok": sum(r["ok"] for r in rows), "total": len(rows),
               "actions": sum(len(r["actions"]) for r in rows)}
    result["summary"] = summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary:", summary, result.get("error") or result.get("continuity_errors") or "")
    ok = (not result.get("error") and not result.get("continuity_errors") and summary["actions"] == 0
          and summary["ok"] == summary["total"])
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
