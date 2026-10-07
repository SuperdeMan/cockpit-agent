"""CA2-19 真栈探针：「换一个充电站」与导航续航提醒——会对模拟车 v1 发导航动作（用户 2026-10-07 授权）。

合成的已签名 E2E 用户、车辆 v1；每个套件一个会话，最后一轮「取消导航」收尾，不留活动路线。不发车控、商户写或支付。
- `swap`：导航去深圳北站并在附近找充电桩 → 「换一个充电站」→ 取消导航。
  判据看动作载荷：第二轮的导航动作目的地不变、原充电站不在途经点里、换上的站离原站不远、话术报出新站名。
- `range`：导航去厦门火车站（模拟车电量不够跑完全程）→ 话术带电量读数的续航提醒 → 取消导航。
逐轮打印判定并写 JSON 证据；前后各核一次云端发布 SHA。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
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
#: 换上的站离原站多远以内算「同一处补电」（目的地附近选站，原站与新站都在目的地周边）
SWAP_MAX_KM = 6.0

SUITES = {
    "swap": ("导航去深圳北站，在附近找个充电桩", "换一个充电站", "取消导航"),
    "range": ("导航去厦门火车站", "取消导航"),
}


def _km(a: dict, b: dict) -> float:
    """两点球面距离（公里）；坐标缺失返回无穷大。"""
    try:
        lat1, lng1, lat2, lng2 = (math.radians(float(v)) for v in (a["lat"], a["lng"], b["lat"], b["lng"]))
    except (KeyError, TypeError, ValueError):
        return math.inf
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _plain(text: str) -> str:
    """比较站名前统一括号与空格：聚合模型改写话术时会把半角括号写成全角，用户听到的是同一个名字。"""
    return str(text or "").replace("（", "(").replace("）", ")").replace(" ", "")


def _navigates(actions: list) -> list[dict]:
    """本轮 navigate 动作的载荷（动作名逐字相等，`navigate_cancel` 不算）。"""
    return [dict(a.get("payload") or {}) for a in actions
            if isinstance(a, dict) and a.get("type") == "navigate"]


async def _turn(ws, session: str, text: str) -> dict:
    """一轮：发出后收到 final 再等一个短空闲窗；同轮多个 final 合并动作与话术（同 `probe_qa_regression._one_turn`）。"""
    frame = {"text": text, "session_id": session, "meta": dict(wire.PROBE_META),
             "request_id": f"probe-{uuid.uuid4().hex[:16]}"}
    await ws.send(json.dumps(frame))
    out: dict | None = None
    timeout, deadline = wire.TIMEOUT, 0.0
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            if out is not None:
                return out
            raise
        msg = json.loads(raw)
        kind = msg.get("type")
        if kind == "final":
            card = msg.get("ui_card") or {}
            if out is None:
                out = {"speech": str(msg.get("speech") or ""), "actions": list(msg.get("actions") or []),
                       "card": card if isinstance(card, dict) else {}}
            else:
                out["speech"] = "\n".join(s for s in (out["speech"], str(msg.get("speech") or "")) if s)
                out["actions"] += list(msg.get("actions") or [])
                out["card"] = out["card"] or (card if isinstance(card, dict) else {})
            timeout = wire._TAIL_IDLE_S
            if not deadline:
                deadline = time.monotonic() + wire._TAIL_BUDGET_S
        elif kind == "error":
            return {"speech": f"[error] {msg.get('message')}", "actions": [], "card": {}, "error": True}
        elif out is not None:
            timeout = max(0.1, min(wire.TIMEOUT, deadline - time.monotonic()))


def _judge_swap(turns: list[dict]) -> list[str]:
    """`swap` 套件的判定；返回不满足的判据（空 = 通过）。"""
    problems = []
    first, second, cancel = turns
    nav1, nav2 = _navigates(first["actions"]), _navigates(second["actions"])
    if len(nav1) != 1 or not (nav1[0].get("waypoints") or []):
        return ["第一轮没有带途经充电站的导航动作"]
    original = nav1[0]["waypoints"][0]
    first["original_station"] = original
    # 只记录不判定：充电步的站被并进导航时聚合器确定性说这一句；规划成导航单步（途经点是导航自己的）时本来就没有
    first["waypoint_line"] = f"已把{original.get('name')}加入导航途经点" in first["speech"]
    if len(nav2) != 1:
        return problems + ["第二轮没有导航动作（路线没换）"]
    payload = nav2[0]
    if payload.get("destination") != nav1[0].get("destination"):
        problems.append(f"目的地变了：{nav1[0].get('destination')} → {payload.get('destination')}")
    names = [str(w.get("name") or "") for w in payload.get("waypoints") or []]
    if original.get("name") in names:
        problems.append(f"原充电站「{original.get('name')}」还在途经点里")
    swapped = [w for w in payload.get("waypoints") or [] if w.get("name") != original.get("name")]
    if len(swapped) != 1:
        problems.append(f"换站后途经点不是恰好一个新站：{names}")
    else:
        station = swapped[0]
        second["swapped_station"] = station
        distance = _km(original, station)
        second["swap_distance_km"] = round(distance, 2)
        if distance > SWAP_MAX_KM:
            problems.append(f"新站离原站 {distance:.1f} km，不是同一处补电")
        if _plain(station.get("name")) not in _plain(second["speech"]):
            problems.append("话术没报出新站名")
    if not any(a.get("type") == "navigate_cancel" for a in cancel["actions"] if isinstance(a, dict)):
        problems.append("收尾没有取消导航")
    return problems


def _judge_range(turns: list[dict]) -> list[str]:
    """`range` 套件的判定：导航动作照发，话术带电量读数的续航提醒。"""
    problems = []
    first, cancel = turns
    if len(_navigates(first["actions"])) != 1:
        problems.append("没有导航动作")
    speech = first["speech"]
    if not ("提醒一下：当前电量约" in speech and "模拟车读数" in speech and "估算续航" in speech):
        problems.append("话术没有带电量读数的续航提醒")
    if not any(a.get("type") == "navigate_cancel" for a in cancel["actions"] if isinstance(a, dict)):
        problems.append("收尾没有取消导航")
    return problems


async def probe(expected_sha: str, suite: str) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"suite": suite, "release_start": snapshot, "turns": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, _collector, secret = identity._endpoints()
    run_id = "e2e-ca219swap-" + uuid.uuid4().hex[:12]
    user = run_id + "-u1"
    token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES), timeout_s=900)
    async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        session = f"{user}-session-1"
        for text in SUITES[suite]:
            obs = await _turn(ws, session, text)
            result["turns"].append({"say": text, **obs})
    result["problems"] = (_judge_swap if suite == "swap" else _judge_range)(result["turns"])
    result["release_end"] = audit.cloud_release_snapshot(expected_sha)
    result["continuity_errors"] = audit.validate_release_continuity(
        result["release_start"], result["release_end"], expected_sha)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--suite", choices=sorted(SUITES), default="swap")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args.expected_sha, args.suite))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for turn in result.get("turns", []):
        navs = [{"destination": p.get("destination"), "waypoints": [w.get("name") for w in p.get("waypoints") or []]}
                for p in _navigates(turn["actions"])]
        kinds = [a.get("type") for a in turn["actions"] if isinstance(a, dict)]
        print(f"{turn['say']} -> {turn['speech'][:200]}\n    actions={kinds} navigate={navs}"
              + (f" waypoint_line={turn['waypoint_line']}" if "waypoint_line" in turn else ""))
    problems = result.get("problems") or []
    for problem in problems:
        print("  ✗", problem)
    ok = not result.get("error") and not result.get("continuity_errors") and not problems
    print("verdict:", "PASS" if ok else "FAIL", result.get("error") or result.get("continuity_errors") or "")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
