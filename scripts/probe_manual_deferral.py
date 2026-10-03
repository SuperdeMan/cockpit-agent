"""Real-stack probe: when the planner falls back to chitchat on a vehicle-manual question, chitchat defers to the manual.

Read-only: question utterances only, each repeat in a fresh session of a synthetic signed E2E user on vehicle v1;
no confirmation, vehicle command or merchant/payment call. Routing to chitchat is the planner's choice and cannot be
forced, so the probe repeats each question and reports, per turn, the planned intents, whether chitchat ran and
handed off (`_escalate` reason `manual_confident`), whether the manual answered, and the first words the user heard.
The release SHA is checked before and after; a JSON evidence file is written.
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
QUESTIONS = ("空调温度怎么调？", "空调有哪些模式？", "说明书里“打开后备箱”这一节讲的是什么？", "空调有哪些模式？风量分几个档位？")


def _nodes(detail: dict) -> list[str]:
    return [str(s.get("node") or "") for s in sorted(detail.get("spans") or [], key=lambda s: s.get("ts", 0))]


def classify(obs: dict, detail: dict) -> dict:
    nodes = _nodes(detail)
    chitchat = any(n == "step.agent:chitchat" for n in nodes)
    manual = any(n == "step.agent:manual-rag" for n in nodes)
    logs = " ".join(str(lg.get("msg") or lg.get("message") or "") for lg in detail.get("logs") or [])
    intents = str((detail.get("turn") or {}).get("intents") or "")
    # 改派判定按结构：规划只有闲聊、手册却跑了；闲聊改派时还会记一行 manual_confident 日志（两者都要）
    return {"intents": intents,
            "chitchat_ran": chitchat, "manual_ran": manual,
            "deferred": chitchat and manual and "manual.query" not in intents and "manual_confident" in logs,
            "manual_card": obs.get("card_type") == "manual",
            "clarify": "clarify" in nodes,
            "speech": str(obs.get("speech") or "")[:120],
            "actions": obs.get("actions") or []}


async def probe(expected_sha: str, repeats: int) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"release_start": snapshot, "turns": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, collector, secret = identity._endpoints()
    run_id = "e2e-defer-" + uuid.uuid4().hex[:12]
    for q_index, question in enumerate(QUESTIONS):
        for repeat in range(1, repeats + 1):
            user = f"{run_id}-q{q_index}-r{repeat}"
            token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1",
                                  scopes=list(SCOPES), timeout_s=900)
            async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
                except asyncio.TimeoutError:
                    pass
                trace = uuid.uuid4().hex
                obs = await wire._one_turn(ws, f"{user}-session-1", question, trace_id=trace)
                detail = await audit._fetch_detail(collector, trace)
                row = {"say": question, "repeat": repeat, "trace_id": trace, **classify(obs, detail)}
                result["turns"].append(row)
                print(f"{question[:14]:<14} r{repeat} intents={row['intents']:<24} chitchat={int(row['chitchat_ran'])} "
                      f"deferred={int(row['deferred'])} manual={int(row['manual_ran'])} card={int(row['manual_card'])} "
                      f"clarify={int(row['clarify'])} | {row['speech'][:60]}")
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha, args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    turns = result.get("turns", [])
    chitchat = [t for t in turns if t["chitchat_ran"]]
    summary = {"turns": len(turns), "manual_answered": sum(t["manual_ran"] for t in turns),
               "chitchat_ran": len(chitchat), "chitchat_deferred": sum(t["deferred"] for t in chitchat),
               "clarify": sum(t["clarify"] for t in turns), "actions": sum(len(t["actions"]) for t in turns)}
    print("summary:", summary, result.get("error") or result.get("continuity_errors") or "")
    ok = not result.get("error") and not result.get("continuity_errors") and summary["actions"] == 0 \
        and all(t["deferred"] for t in chitchat)
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
