"""语义打断：真栈探针（docs/design/2026-10-11-semantic-barge-in.md §6）。

只读，两部分：
  A) 判定接口 `POST <audio>/api/voice/admission`：续问窗那份语料（人人对话 / 电话 / 广播为负例，追问 / 问句 / 闲聊为正例），
     每例带一段「正在播的回答」作 previous（每轮换一段，免得逐字相同的重复命中网关 300 s 缓存），看判定与耗时；
     另发一次不带身份的请求，必须 401。
  B) 已判过的一轮走签名车道：唤醒那一轮先答一句，再按插话来源发一句带 `voice_admitted=1` 的问句——
     trace 里只能有 exit=bargein_preadmitted、不能有续问窗判定，且不被拒；对照：同来源不带标记的负例照旧被续问窗判定拒掉。
全程断言零动作；前后各核一次 release SHA。
⚠ 两次运行要相隔 5 分钟以上：消息逐字相同的判定会命中网关 300 s 缓存，第二次就不是独立判定了（2026-10-11 复跑实测中位 3 ms）。

用法：
  python scripts/probe_bargein_admission.py --expected-sha <40 位 release> --output <json> [--repeat 3]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import probe_followup_rejection as followup  # noqa: E402
from scripts import probe_history_window as identity  # noqa: E402
from scripts import probe_qa_long_sessions as audit  # noqa: E402
from scripts import probe_qa_regression as wire  # noqa: E402
from scripts.e2e_identity import sign_identity  # noqa: E402

#: 正在播的那段（插话发生时助手在说的话）——每轮换一段
SPOKEN = (
    "深圳明天多云转晴，最高气温二十七度，东南风二级，适合出行。",
    "好的，为您播放周杰伦的《晴天》。",
    "从前有座山，山里有座庙，庙里有个老和尚在给小和尚讲故事。",
    "已为您规划去公司的路线，全程约二十五分钟，前方三公里有拥堵。",
    "杭州明天小雨，最高气温二十二度，出门记得带伞。",
)
#: 天气类追问（「那后天呢」「上海呢」「要带伞吗」）只在助手正在播天气时才是接着说——配天气回答（每轮换城市，避开缓存）。
#: 2026-10-11 首跑把它们与放歌 / 讲笑话的回答随机配对：放歌时问「要带伞吗」也可能是在问同车的人，判否说得通，那不是该测的场景。
WEATHER_SPOKEN = (
    "深圳明天多云转晴，最高气温二十七度，东南风二级，适合出行。",
    "广州明天阵雨，最高气温二十九度，湿度较大。",
    "成都明天阴，最高气温二十度，早晚偏凉。",
)
WEATHER_FOLLOWUPS = frozenset({"那后天呢", "上海呢", "要带伞吗"})


def _spoken_for(text: str, rep: int, index: int) -> str:
    if text in WEATHER_FOLLOWUPS:
        return WEATHER_SPOKEN[(rep + index) % len(WEATHER_SPOKEN)]
    return SPOKEN[(rep + index) % len(SPOKEN)]


def _audio_base() -> str:
    env = dict(__import__("os").environ)
    env.update(identity.read_root_env(identity._ROOT, {"TAILNET_FQDN", "VITE_WS_TOKEN", "E2E_IDENTITY_SECRET"}))
    target = identity.resolve_e2e_target(identity._ROOT, explicit=None, environ=env)
    return identity.endpoint_environment(target)["AUDIO_API_URL"].rstrip("/")


def _post(url: str, body: dict, token: str, timeout: float = 15.0) -> tuple[int, dict, float]:
    """回 (HTTP 状态, 回包, 墙钟 ms)。连接级失败（TLS 握手超时、断连）重试一次，仍失败记状态 0——链路问题
    如实记一行、不中断整轮，也不算判定对错。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in (1, 2):
        started = time.monotonic()
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                return resp.status, payload, round((time.monotonic() - started) * 1000)
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
            except ValueError:
                payload = {}
            return exc.code, payload, round((time.monotonic() - started) * 1000)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 2:
                return 0, {"error": type(exc).__name__}, round((time.monotonic() - started) * 1000)
    return 0, {}, 0


def judge_endpoint(audio: str, secret: bytes, run_id: str, repeat: int) -> dict:
    url = audio + "/api/voice/admission"
    out: dict = {"rows": []}
    status, payload, _ms = _post(url, {"utterance": "妈你到哪了", "previous": SPOKEN[0]}, token="")
    out["no_token"] = {"status": status, "error": payload.get("error")}
    for rep in range(repeat):
        user = f"{run_id}-judge-r{rep}"
        token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1",
                              scopes=list(followup.SCOPES), timeout_s=900)
        for index, (text, addressed) in enumerate(followup.CASES):
            previous = _spoken_for(text, rep, index)
            status, payload, wall_ms = _post(url, {"utterance": text, "previous": previous,
                                                    "session_id": f"{user}-session-1"}, token)
            row = {"rep": rep, "text": text, "gold_addressed": addressed, "previous": previous[:16],
                   "status": status, "addressed": payload.get("addressed"), "votes": payload.get("votes"),
                   "server_ms": payload.get("ms"), "wall_ms": wall_ms}
            out["rows"].append(row)
            ok = status == 200 and payload.get("addressed") is addressed
            print(f"A r{rep} {'OK ' if ok else 'BAD'} gold={int(addressed)} got={payload.get('addressed')} "
                  f"votes={payload.get('votes')} {payload.get('ms')}ms/{wall_ms}ms  {text}")
    return out


async def _spans(collector: str, trace: str, attempts: int = 8) -> dict:
    detail: dict = {}
    for _ in range(attempts):
        detail = await audit._fetch_detail(collector, trace)
        if (detail.get("turn") or {}).get("status"):
            return detail
        await asyncio.sleep(1.5)
    return detail


async def preadmitted_turns(ws_url: str, collector: str, secret: bytes, run_id: str) -> list[dict]:
    import websockets
    cases = (
        ("帮我查下明天杭州的天气", {"input_source": "voice_bargein", "voice_admitted": "1"}, "bargein_preadmitted", False),
        ("你今天几点下班", {"input_source": "voice_bargein"}, "continuation", True),
    )
    rows = []
    for index, (text, meta, expected_exit, expected_rejected) in enumerate(cases):
        user = f"{run_id}-turn-s{index}"
        token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1",
                              scopes=list(followup.SCOPES), timeout_s=900)
        session = f"{user}-session-1"
        trace = uuid.uuid4().hex
        try:
            async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
                except asyncio.TimeoutError:
                    pass
                opening = await wire._one_turn(ws, session, followup.OPENINGS[index % len(followup.OPENINGS)],
                                               meta_overrides={"input_source": "voice_wake"})
                started = time.monotonic()
                obs = await wire._one_turn(ws, session, text, trace_id=trace, meta_overrides=meta)
                turn_s = round(time.monotonic() - started, 2)
        except (asyncio.TimeoutError, TimeoutError, OSError, websockets.ConnectionClosed) as exc:
            rows.append({"text": text, "meta": meta, "trace_id": trace, "error": type(exc).__name__, "actions": []})
            print(f"B ERR {type(exc).__name__}  {text}")
            continue
        detail = await _spans(collector, trace)
        exits = [(s.get("attrs") or {}).get("exit") for s in detail.get("spans") or []
                 if s.get("node") == "cloud.voice_admission"]
        rejected = obs.get("card_type") == "rejected"
        row = {"text": text, "meta": meta, "trace_id": trace, "rejected": rejected, "exits": exits,
               "expected_exit": expected_exit, "expected_rejected": expected_rejected,
               "speech": str(obs.get("speech") or "")[:60], "turn_s": turn_s,
               "actions": (obs.get("actions") or []) + (opening.get("actions") or [])}
        ok = rejected is expected_rejected and exits == [expected_exit]
        print(f"B {'OK ' if ok else 'BAD'} rejected={int(rejected)} exits={exits} turn={turn_s}s  {text} {meta}")
        rows.append(row)
    return rows


async def probe(expected_sha: str, repeat: int) -> dict:
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result: dict = {"release_start": snapshot}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, collector, secret = identity._endpoints()
    run_id = "e2e-bargein-" + uuid.uuid4().hex[:10]
    result["judge"] = await asyncio.to_thread(judge_endpoint, _audio_base(), secret, run_id, repeat)
    result["turns"] = await preadmitted_turns(ws_url, collector, secret, run_id)
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def summarize(result: dict) -> dict:
    rows = (result.get("judge") or {}).get("rows") or []
    ok_rows = [r for r in rows if r["status"] == 200]
    negatives = [r for r in ok_rows if not r["gold_addressed"]]
    positives = [r for r in ok_rows if r["gold_addressed"]]
    ms = sorted(r["server_ms"] for r in ok_rows if isinstance(r.get("server_ms"), int))
    rej_ms = sorted(r["server_ms"] for r in negatives if r.get("addressed") is False and isinstance(r.get("server_ms"), int))
    acc_ms = sorted(r["server_ms"] for r in positives if r.get("addressed") is True and isinstance(r.get("server_ms"), int))
    turns = result.get("turns") or []
    summary = {
        "judged": len(ok_rows), "http_errors": len(rows) - len(ok_rows),
        "negatives_rejected": f"{sum(r.get('addressed') is False for r in negatives)}/{len(negatives)}",
        "positives_admitted": f"{sum(r.get('addressed') is True for r in positives)}/{len(positives)}",
        "unjudged": sum(r.get("addressed") is None for r in ok_rows),
        "server_ms_range": [ms[0], ms[-1]] if ms else None,
        "rejected_ms_range": [rej_ms[0], rej_ms[-1]] if rej_ms else None,
        "admitted_ms_range": [acc_ms[0], acc_ms[-1]] if acc_ms else None,
        "no_token_status": (result.get("judge") or {}).get("no_token", {}).get("status"),
        "turns_ok": sum(1 for t in turns if not t.get("error") and t["rejected"] is t["expected_rejected"]
                        and t["exits"] == [t["expected_exit"]]),
        "turns": len(turns),
        "actions": sum(len(t.get("actions") or []) for t in turns),
        "continuity_errors": result.get("continuity_errors") or [],
    }
    summary["pass"] = (not result.get("error") and summary["http_errors"] == 0 and summary["unjudged"] == 0
                       and summary["no_token_status"] == 401
                       and summary["negatives_rejected"].split("/")[0] == summary["negatives_rejected"].split("/")[1]
                       and summary["positives_admitted"].split("/")[0] == summary["positives_admitted"].split("/")[1]
                       and summary["turns_ok"] == summary["turns"] == 2 and summary["actions"] == 0
                       and not summary["continuity_errors"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha, args.repeat))
    result["summary"] = summarize(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
