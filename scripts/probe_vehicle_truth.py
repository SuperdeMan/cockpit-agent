"""CA2-19 real-stack probe: vehicle readings are never invented, alert explanations never declare a fault resolved.

Read-only: query utterances from a synthetic signed E2E user on vehicle v1. No confirmation, vehicle command
or merchant/payment call is issued (edge queries answer with a read-only `*.query` action). Suites:
- `vehicle` (S1): battery, tire pressure, a charging plan — each in its own session.
- `alerts` (S2): one session — a tire-warning question, an oil lamp report, then "it went off, can I keep driving".
- `routes` (S3): a charging plan states its arrival estimate; a destination charger search claims no route change.
- `navigation` (S4): a long trip estimate from here mentions the battery reading; a short one does not.
Prints a verdict per turn and writes a JSON evidence file; the release SHA is checked before and after.
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

#: 套件名 → (同一会话?, ((说法, 判据), ...))；判据只看用户听到的话
SUITES = {
    "vehicle": (False, (
        ("还剩多少电", lambda s: "模拟车读数" in s and "%" in s),
        ("胎压正常吗", lambda s: "读不到胎压" in s and "胎压正常" not in s),
        ("去厦门火车站路上要不要充电", lambda s: ("模拟车读数" in s and "估算" in s) or "没读到当前电量" in s),
    )),
    "alerts": (True, (
        ("胎压报警灯亮了是什么意思", lambda s: "不能据此判断故障已排除" in s),
        ("机油灯亮了", lambda s: "停车" in s or "不能据此判断故障已排除" in s),
        ("机油灯灭了，现在还能继续开吗",
         lambda s: "适合出行" not in s and ("排除" in s or "检查" in s)),
    )),
    "routes": (False, (
        ("去厦门火车站路上要不要充电", lambda s: "到达时估算剩余约" in s or "没读到当前电量" in s),
        ("厦门火车站附近有充电站吗", lambda s: "充电站" in s and "已为" not in s and "加入途经" not in s),
    )),
    "navigation": (False, (
        ("去厦门火车站要开多久", lambda s: "提醒一下：当前电量约" in s and "模拟车读数" in s and "估算续航" in s),
        ("去深圳北站多远", lambda s: "公里" in s and "电量" not in s),
    )),
}


async def probe(expected_sha: str, suite: str) -> dict:
    import websockets
    same_session, turns = SUITES[suite]
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"suite": suite, "release_start": snapshot, "turns": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, _collector, secret = identity._endpoints()
    run_id = "e2e-ca219-" + uuid.uuid4().hex[:12]
    user = run_id + "-u1"
    token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES), timeout_s=900)
    async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        for n, (text, judge) in enumerate(turns, 1):
            session = f"{user}-session-1" if same_session else f"{user}-session-{n}"
            obs = await wire._one_turn(ws, session, text)
            speech = str(obs.get("speech") or "")
            result["turns"].append({"say": text, "speech": speech, "actions": obs.get("actions") or [],
                                    "card_type": obs.get("card_type") or "", "pass": bool(judge(speech))})
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--suite", choices=sorted(SUITES), default="vehicle")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha, args.suite))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for turn in result.get("turns", []):
        print(("PASS " if turn["pass"] else "FAIL ") + turn["say"] + " -> " + turn["speech"][:160]
              + (f" actions={turn['actions']}" if turn["actions"] else ""))
    # 端侧查询本身会回一个只读动作（battery.query / tire_pressure.query）；除此之外的动作都算失败
    ok = (not result.get("error") and not result.get("continuity_errors")
          and all(t["pass"] and all(str(a).endswith(".query") for a in t["actions"])
                  for t in result.get("turns", [])))
    print("verdict:", "PASS" if ok else "FAIL", result.get("error") or result.get("continuity_errors") or "")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
