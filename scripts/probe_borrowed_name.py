"""真栈探针：远处地标不被借了它名字的近处地点悄悄顶替。

只读：只问距离估算（「去X要开多久」），每个目的地用一个签名的合成 E2E 用户、车辆 v1 的新会话；不导航、不确认、不发车控、
不调商户 / 支付。曾被解析到本地借名点的地标（docs/design/2026-10-04-destination-borrowed-name.md §1）：第一轮要给出两处
（dest_choice 卡，远处在前）且零动作；下一轮回答「第一个」或远处候选的名字（HMI 与手机点选发的就是它），要估算到远处那个
（本地半径之外）。对照目的地要直接作答。前后各核一次 release SHA，写一份 JSON 证据文件。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
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
BORROWED = ("南京南站", "东方明珠", "外滩", "黄鹤楼", "故宫")
CONTROLS = ("上海虹桥站", "深圳北站", "南山书城")
LOCAL_RADIUS_KM = 150
_KM = re.compile(r"(?:全程约|约)\s*([\d.]+)\s*公里")


def _card(obs: dict) -> dict:
    try:
        card = json.loads(obs.get("card_text") or "{}")
    except ValueError:
        return {}
    return card if isinstance(card, dict) else {}


def _km(speech: str) -> float | None:
    m = _KM.search(speech or "")
    return float(m.group(1)) if m else None


async def _turn(ws, collector, session: str, text: str) -> dict:
    trace = uuid.uuid4().hex
    obs = await wire._one_turn(ws, session, text, trace_id=trace)
    detail = await audit._fetch_detail(collector, trace)
    card = _card(obs)
    return {"say": text, "trace_id": trace,
            "intents": str((detail.get("turn") or {}).get("intents") or ""),
            "speech": str(obs.get("speech") or "")[:160],
            "km": _km(str(obs.get("speech") or "")),
            "card_type": card.get("type") or "", "purpose": card.get("purpose") or "",
            "items": [str(it.get("name") or "") for it in card.get("items") or [] if isinstance(it, dict)],
            "actions": obs.get("actions") or []}


async def probe(expected_sha: str) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"release_start": snapshot, "rows": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, collector, secret = identity._endpoints()
    run_id = "e2e-namesake-" + uuid.uuid4().hex[:12]
    for index, dest in enumerate(BORROWED + CONTROLS):
        user = f"{run_id}-d{index}"
        token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1",
                              scopes=list(SCOPES), timeout_s=900)
        async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
            except asyncio.TimeoutError:
                pass
            session = f"{user}-session-1"
            first = await _turn(ws, collector, session, f"去{dest}要开多久")
            row = {"dest": dest, "borrowed": dest in BORROWED, "first": first,
                   "asked": first["purpose"] == "dest_choice" and len(first["items"]) == 2}
            if row["asked"]:
                # 一半说序号、一半回发候选名（两端点选时发的就是名字）
                answer = "第一个" if index % 2 == 0 else first["items"][0]
                row["second"] = await _turn(ws, collector, session, answer)
            result["rows"].append(row)
            second = row.get("second") or {}
            print(f"{dest:<6} asked={int(row['asked'])} items={first['items']} first_km={first['km']} "
                  f"| second({second.get('say', '-')}) km={second.get('km')} | {first['speech'][:50]}")
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def verdict(rows: list[dict]) -> dict:
    out = {"borrowed_asked": 0, "borrowed_resumed_far": 0, "controls_direct": 0, "actions": 0}
    for row in rows:
        out["actions"] += len(row["first"]["actions"]) + len((row.get("second") or {}).get("actions") or [])
        if row["borrowed"]:
            out["borrowed_asked"] += int(row["asked"])
            km = (row.get("second") or {}).get("km")
            out["borrowed_resumed_far"] += int(km is not None and km > LOCAL_RADIUS_KM)
        else:
            out["controls_direct"] += int(not row["asked"] and row["first"]["km"] is not None)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary = verdict(result.get("rows", []))
    result["summary"] = summary
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary:", summary, result.get("error") or result.get("continuity_errors") or "")
    ok = (not result.get("error") and not result.get("continuity_errors") and summary["actions"] == 0
          and summary["borrowed_asked"] == len(BORROWED) and summary["borrowed_resumed_far"] == len(BORROWED)
          and summary["controls_direct"] == len(CONTROLS))
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
