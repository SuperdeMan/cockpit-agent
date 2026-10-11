"""唤醒后连续对话的拒识：真栈探针（docs/design/2026-10-10-handsfree-followup-rejection.md §5）。

只读：每例一个签名的合成 E2E 用户、车辆 v1 的新会话，两轮——
  1) 按唤醒词那一轮发一句只读问句（`input_source=voice_wake`），让助手先答一句；
  2) 按续问窗来源发这一例（`input_source=voice_followup`），看云端拒没拒。
第二轮读 collector trace：`cloud.voice_admission`（exit=continuation，两次判定与耗时）与 `rejected`（judge）。
正例只有问句 / 闲聊（不含车控），负例是人人对话 / 电话 / 广播 / 自言自语；全程断言零动作。前后各核一次 release SHA。

用法：
  python scripts/probe_followup_rejection.py --expected-sha <40 位 release> --output <json> [--repeat 3]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
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
OPENING = "深圳明天天气怎么样"
#: (续问窗里收到的这句, 是否对助手说的)。前 8 句是核心旅程 F12 的原句（R01–R08）。
CASES = (
    ("妈你到哪了，我们在停车场等你", False),
    ("他昨天跟我说那个项目黄了", False),
    ("喂，听得到吗，我在开车呢", False),
    ("据央视新闻报道，今日多地迎来强降雨天气", False),
    ("下一站人民广场，请从后门下车", False),
    ("接下来为您播放一首经典老歌", False),
    ("现在几点了", True),
    ("帮我查下明天杭州的天气", True),
    ("你今天几点下班", False),
    ("张姐，这个月报表弄好了吗", False),
    ("我这边在开车，晚点给你回过去", False),
    ("完了，忘带充电器了", False),
    ("那后天呢", True),
    ("上海呢", True),
    ("要带伞吗", True),
    ("给我讲个笑话", True),
)


async def _admission(collector: str, trace: str, attempts: int = 8) -> tuple[dict | None, dict | None, dict]:
    detail: dict = {}
    for _ in range(attempts):
        detail = await audit._fetch_detail(collector, trace)
        spans = detail.get("spans") or []
        admission = next((s for s in spans if s.get("node") == "cloud.voice_admission"
                          and (s.get("attrs") or {}).get("exit") == "continuation"), None)
        rejected = next((s for s in spans if s.get("node") == "rejected"), None)
        if admission is not None or rejected is not None:
            return admission, rejected, detail
        if (detail.get("turn") or {}).get("status"):
            return None, None, detail
        await asyncio.sleep(1.5)
    return None, None, detail


async def probe(expected_sha: str, repeat: int) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"release_start": snapshot, "rows": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, collector, secret = identity._endpoints()
    run_id = "e2e-followup-" + uuid.uuid4().hex[:10]
    for rep in range(repeat):
        for index, (text, addressed) in enumerate(CASES):
            user = f"{run_id}-r{rep}-s{index}"
            token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES),
                                  timeout_s=900)
            session = f"{user}-session-1"     # 签名车道只认「<用户>-session-<正整数>」（gateway/edge/e2e_identity.go）
            trace = uuid.uuid4().hex
            try:
                async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
                    except asyncio.TimeoutError:
                        pass
                    opening = await wire._one_turn(ws, session, OPENING, meta_overrides={"input_source": "voice_wake"})
                    started = time.monotonic()
                    obs = await wire._one_turn(ws, session, text, trace_id=trace,
                                               meta_overrides={"input_source": "voice_followup"})
                    turn_s = round(time.monotonic() - started, 2)
            except (asyncio.TimeoutError, TimeoutError, OSError, websockets.ConnectionClosed) as exc:
                # 单例链路失败（云端偶发慢、断连）如实记一行、继续下一例；不算判定对错
                result["rows"].append({"rep": rep, "text": text, "gold_addressed": addressed, "trace_id": trace,
                                       "error": type(exc).__name__, "actions": []})
                print(f"r{rep} ERR {type(exc).__name__}  {text}")
                continue
            admission, rejected_span, detail = await _admission(collector, trace)
            attrs = (admission or {}).get("attrs") or {}
            rejected = obs.get("card_type") == "rejected"
            row = {"rep": rep, "text": text, "gold_addressed": addressed, "trace_id": trace,
                   "rejected": rejected, "speech": str(obs.get("speech") or "")[:60],
                   "votes": attrs.get("votes"), "verdict": attrs.get("verdict"),
                   "admission_ms": attrs.get("admission_ms"), "judge": ((rejected_span or {}).get("attrs") or {}).get("judge"),
                   "turn_s": turn_s, "opening_ok": bool(opening.get("speech")),
                   "actions": (obs.get("actions") or []) + (opening.get("actions") or []),
                   "intents": str((detail.get("turn") or {}).get("intents") or "")}
            result["rows"].append(row)
            ok = rejected != addressed
            print(f"r{rep} {'OK ' if ok else 'BAD'} gold={int(addressed)} rejected={int(rejected)} votes={row['votes']} "
                  f"judge={row['judge']} adm={row['admission_ms']}ms turn={turn_s}s  {text}")
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha, args.repeat))
    errors = [r for r in result.get("rows", []) if r.get("error")]
    rows = [r for r in result.get("rows", []) if not r.get("error")]
    negatives = [r for r in rows if not r["gold_addressed"]]
    positives = [r for r in rows if r["gold_addressed"]]
    summary = {
        "turns": len(rows),
        "link_errors": len(errors),
        "negatives_rejected": f"{sum(r['rejected'] for r in negatives)}/{len(negatives)}",
        "positives_rejected": f"{sum(r['rejected'] for r in positives)}/{len(positives)}",
        "continuation_judged": sum(r["votes"] is not None for r in rows),
        "judge_unavailable": sum(r.get("verdict") == "unavailable" for r in rows),
        "opening_answered": sum(r["opening_ok"] for r in rows),
        "actions": sum(len(r["actions"]) for r in rows),
    }
    result["summary"] = summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary:", summary, result.get("error") or result.get("continuity_errors") or "")
    # 读数工具：判错的句子如实列出，不算探针失败；探针失败只有 release 漂移、执行了动作、续问窗判定没接上
    ok = (not result.get("error") and not result.get("continuity_errors") and summary["actions"] == 0
          and summary["continuation_judged"] == len(rows))
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
