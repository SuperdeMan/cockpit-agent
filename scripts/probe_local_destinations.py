"""真栈探针：日常具名地点解析到地点本身，而不是借了它名字的近处附属地点。

只读：只问距离估算（「去X要开多久」），每条旅程用一个签名的合成 E2E 用户、车辆 v1 的新会话；不导航、不确认、不发车控、
不调商户 / 支付。案例表在冻结语料里只留一份（`test/eval_corpus/v2_runtime/core/f06_f07_f09_maps.yaml` 的 NV 旅程，冻结 P2，
2026-10-10 由本文件原来的 CASES / ASK / MAX_KM / MIN_KM 搬过去）：话术里该有的名字之一、不该再出现的附属地点、全程公里数上下限、
「鼓浪屿」候选第一项是厦门。判定用共用实现 `scripts.journey_expect`（与冻结运行器同一份）；来历见
docs/design/2026-10-04-local-destination-ranking.md §3–§17。前后各核一次 release SHA，写一份 JSON 证据文件。
`--ids` 只跑点名的几条（逗号分隔）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import probe_history_window as identity  # noqa: E402
from scripts import probe_qa_long_sessions as audit  # noqa: E402
from scripts import probe_qa_regression as wire  # noqa: E402
from scripts.e2e_identity import sign_identity  # noqa: E402
from scripts.journey_expect import check_expect  # noqa: E402
from scripts.render_cloud_env import DEMO_AUTH_SCOPES  # noqa: E402

SCOPES = tuple(s for s in DEMO_AUTH_SCOPES if s not in {"merchant.write", "payment.invoke"})
CORPUS = ROOT / "test" / "eval_corpus" / "v2_runtime" / "core" / "f06_f07_f09_maps.yaml"


def load_cases(path: Path = CORPUS, ids: set[str] | None = None) -> list[dict]:
    """F06 估算旅程（id 以 NV 开头）；`ids` 非空时只取点名的。"""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cases = [c for c in doc.get("cases") or [] if str(c.get("id", "")).startswith("NV")]
    return [c for c in cases if not ids or c["id"] in ids]


def _card(obs: dict) -> dict:
    try:
        card = json.loads(obs.get("card_text") or "{}")
    except ValueError:
        return {}
    return card if isinstance(card, dict) else {}


def judge_turn(expect: dict, obs: dict) -> list[str]:
    """一轮的判定：语料里写的判据（共用实现）+ 只读零动作。"""
    view = SimpleNamespace(final={"speech": str(obs.get("speech") or ""), "ui_card": _card(obs),
                                  "need_confirm": bool(obs.get("need_confirm")),
                                  "follow_up": str(obs.get("follow_up") or "")},
                           actions=[], process_events=[], elapsed=0.0)
    fails = check_expect(expect, view, False, ())
    if obs.get("actions"):
        fails.append(f"只读估算出了动作 {obs.get('actions')}")
    return fails


async def probe(expected_sha: str, cases: list[dict]) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"release_start": snapshot, "rows": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, _collector, secret = identity._endpoints()
    run_id = "e2e-localdest-" + uuid.uuid4().hex[:12]
    for index, case in enumerate(cases):
        user = f"{run_id}-d{index}"
        token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES), timeout_s=900)
        turns = []
        async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
            except asyncio.TimeoutError:
                pass
            for turn in case["turns"]:
                obs = await wire._one_turn(ws, f"{user}-session-1", turn["say"], trace_id=uuid.uuid4().hex)
                fails = judge_turn(turn.get("expect") or {}, obs)
                items = [str(it.get("name") or "") for it in _card(obs).get("items") or [] if isinstance(it, dict)]
                turns.append({"say": turn["say"], "fails": fails, "speech": str(obs.get("speech") or "")[:160],
                              "items": items, "actions": obs.get("actions") or []})
        ok = not any(t["fails"] for t in turns)
        result["rows"].append({"id": case["id"], "ok": ok, "turns": turns})
        last = turns[-1]
        print(f"{case['id']} ok={int(ok)} {last['say']} | {last['speech'][:70]}"
              + ("" if ok else f" | {[f for t in turns for f in t['fails']]}"))
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", default="", help="只跑这几条（逗号分隔，缺省全部 NV 旅程）")
    args = parser.parse_args()
    cases = load_cases(ids={x for x in args.ids.split(",") if x} or None)
    result = asyncio.run(probe(args.expected_sha, cases))
    rows = result.get("rows", [])
    summary = {"ok": sum(r["ok"] for r in rows), "total": len(rows),
               "actions": sum(len(t["actions"]) for r in rows for t in r["turns"])}
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
