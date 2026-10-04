"""Real-stack probe: everyday named places resolve to the place itself, not a nearby attachment that borrows the name.

Read-only: distance-estimate questions only ("去X要开多久"), each in a fresh session of a synthetic signed E2E user on
vehicle v1; no navigation, confirmation, vehicle command or merchant/payment call. For each destination the estimate
must name a place containing one of the expected names (docs/design/2026-10-04-local-destination-ranking.md §3 A/B)
and none of the attachment names it used to land on; 「鼓浪屿」 must offer both places with Xiamen first. The release
SHA is checked before and after; a JSON evidence file is written.
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
# 目的地 → (话术里应出现的名字之一, 不应再出现的附属地点)
CASES = {
    "大梅沙": (("大梅沙海滨",), ("推拿",)),
    "蛇口港": (("蛇口港",), ("公安局",)),
    "东门": (("东门商业步行街", "东门步行街", "东门(地名"), ("食堂",)),
    "海岸城": (("海岸城购物中心",), ("停车场",)),
    "会展中心": (("深圳会展中心",), ("国家会议中心",)),
    "山姆会员店": (("山姆会员商店",), ("北京",)),
    "厦门火车站": (("厦门站", "厦门火车站"), ()),
}
ASK = {"鼓浪屿": "厦门"}          # 应发问：候选第一个带这个城市


def _card(obs: dict) -> dict:
    try:
        card = json.loads(obs.get("card_text") or "{}")
    except ValueError:
        return {}
    return card if isinstance(card, dict) else {}


async def probe(expected_sha: str) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"release_start": snapshot, "rows": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, _collector, secret = identity._endpoints()
    run_id = "e2e-localdest-" + uuid.uuid4().hex[:12]
    for index, dest in enumerate(list(CASES) + list(ASK)):
        user = f"{run_id}-d{index}"
        token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES), timeout_s=900)
        async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
            except asyncio.TimeoutError:
                pass
            obs = await wire._one_turn(ws, f"{user}-session-1", f"去{dest}要开多久", trace_id=uuid.uuid4().hex)
        speech = str(obs.get("speech") or "")
        card = _card(obs)
        items = [str(it.get("name") or "") for it in card.get("items") or [] if isinstance(it, dict)]
        if dest in ASK:
            ok = card.get("purpose") == "dest_choice" and bool(items) and ASK[dest] in items[0]
        else:
            want, avoid = CASES[dest]
            ok = any(w in speech for w in want) and not any(a in speech for a in avoid)
        row = {"dest": dest, "ok": ok, "speech": speech[:160], "items": items, "actions": obs.get("actions") or []}
        result["rows"].append(row)
        print(f"{dest:<6} ok={int(ok)} items={items} | {speech[:70]}")
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha))
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
