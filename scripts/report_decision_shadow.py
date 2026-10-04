"""Read-only report of the Jev "addressed" shadow (docs/design/2026-10-04-jev-decide-integration.md §9–§10).

Lists turns from the collector over a time window, reads each turn's `decision.shadow` span (which never carries the
utterance) and reports, split by voice / typed input: how many turns were shadowed, status counts (ok / timeout / …),
how often Jev and the planner agree, the distribution of Jev's probability, and latency percentiles. With
`--disagreements` it also lists the disagreeing turns with their text from the collector's own turn record (internal
debugging data — keep the output out of the repo).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import probe_history_window as identity  # noqa: E402
from scripts import probe_qa_long_sessions as audit  # noqa: E402


def _search(collector: str, since: int, limit: int) -> list[dict]:
    url = f"{collector}/api/search?{urllib.parse.urlencode({'since': since, 'limit': limit})}"
    rows = audit._http_json(url, headers=audit.collector_headers())
    return rows if isinstance(rows, list) else rows.get("turns", []) if isinstance(rows, dict) else []


def _pct(values: list[float], q: float):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))])


async def report(hours: float, limit: int, disagreements: bool) -> dict:
    _ws, collector, _secret = identity._endpoints()
    since = int((time.time() - hours * 3600) * 1000)
    turns = await asyncio.to_thread(_search, collector, since, limit)
    out = {"window_hours": hours, "turns_listed": len(turns), "by_input": {}, "disagreements": []}
    buckets: dict[str, dict] = {}
    for turn in turns:
        trace = str(turn.get("trace_id") or "")
        if not trace:
            continue
        detail = await audit._fetch_detail(collector, trace, attempts=1)
        span = next((s for s in detail.get("spans") or [] if s.get("node") == "decision.shadow"), None)
        if span is None:
            continue
        attrs = span.get("attrs") or {}
        key = "voice" if attrs.get("voice") else "typed"
        b = buckets.setdefault(key, {"shadowed": 0, "status": Counter(), "agree": 0, "scored": 0,
                                     "p_true": [], "latency_ms": []})
        b["shadowed"] += 1
        b["status"][str(attrs.get("status"))] += 1
        if attrs.get("p_true") is not None:
            b["scored"] += 1
            b["agree"] += bool(attrs.get("agree"))
            b["p_true"].append(float(attrs["p_true"]))
        if span.get("duration_ms") is not None:
            b["latency_ms"].append(float(span["duration_ms"]))
        if disagreements and attrs.get("agree") is False:
            out["disagreements"].append({"trace_id": trace, "voice": bool(attrs.get("voice")),
                                         "planner_addressed": attrs.get("planner_addressed"),
                                         "p_true": attrs.get("p_true"),
                                         "text": str((detail.get("turn") or {}).get("text") or "")[:80]})
    for key, b in buckets.items():
        p = b["p_true"]
        out["by_input"][key] = {
            "shadowed": b["shadowed"], "status": dict(b["status"]), "scored": b["scored"],
            "agree_rate": round(b["agree"] / b["scored"], 3) if b["scored"] else None,
            "p_true_bands": {"<0.2": sum(x < 0.2 for x in p), "0.2-0.8": sum(0.2 <= x <= 0.8 for x in p),
                             ">0.8": sum(x > 0.8 for x in p)},
            "latency_ms_p50_p95": [_pct(b["latency_ms"], 0.5), _pct(b["latency_ms"], 0.95)],
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--disagreements", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = asyncio.run(report(args.hours, args.limit, args.disagreements))
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "disagreements"}, ensure_ascii=False, indent=1))
    if args.disagreements:
        print("disagreements:", len(result["disagreements"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
