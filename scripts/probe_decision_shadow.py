"""Jev 受话 shadow 的真栈探针（docs/design/2026-10-04-jev-decide-integration.md §9）。

只读：只发问句 / 闲聊 / 乘客间对话 / 播报腔句子（不含车控），每句用一个签名的合成 E2E 用户、车辆 v1 的新会话；一半按免唤醒
语音发送。每轮之后读 collector trace，等到 `decision.shadow` span；报告 shadow 状态、Jev 的概率、规划器自己的判定与两者是否一致，
并断言这一轮没有执行任何动作。原话从不出现在 span 里（探针也核这一点）。前后各核一次 release SHA。
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
# (句子, 是否对助手说的) —— 取自拒识语料里的只读句（不含车控）
SENTENCES = (
    ("帮我查下明天杭州的天气", True),
    ("附近有什么好吃的川菜馆", True),
    ("好烦啊今天", True),
    ("讲个笑话听听", True),
    ("唉，又堵车了真烦人", True),
    ("今天股市怎么样", True),
    ("妈，我晚上不回家吃饭了", False),
    ("本台记者深入调研发现，固态电池量产仍需时日", False),
    ("老王你那边信号不好，我待会儿再打给你", False),
    ("宝贝你作业写完了没有", False),
    ("各位乘客，前方到站是人民广场", False),
    # 「你昨天看的那部电影叫什么来着」不能用：端侧规则按「电影」本地开播（2026-10-04 实测，已登记）
    ("我跟你说，那个项目下周才开始", False),
)


async def _shadow_span(collector: str, trace: str, attempts: int = 8) -> tuple[dict | None, dict]:
    detail = {}
    for _ in range(attempts):
        detail = await audit._fetch_detail(collector, trace)
        for span in detail.get("spans") or []:
            if span.get("node") == "decision.shadow":
                return span, detail
        await asyncio.sleep(1.5)
    return None, detail


async def probe(expected_sha: str) -> dict:
    import websockets
    snapshot = audit.cloud_release_snapshot(expected_sha)
    result = {"release_start": snapshot, "rows": []}
    if snapshot["failures"]:
        result["error"] = "release_mismatch"
        return result
    ws_url, collector, secret = identity._endpoints()
    run_id = "e2e-shadow-" + uuid.uuid4().hex[:12]
    for index, (text, addressed) in enumerate(SENTENCES):
        user = f"{run_id}-s{index}"
        token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES), timeout_s=900)
        voice = index % 2 == 0
        trace = uuid.uuid4().hex
        async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
            except asyncio.TimeoutError:
                pass
            obs = await wire._one_turn(ws, f"{user}-session-1", text, trace_id=trace,
                                       meta_overrides={"input_source": "voice_wake"} if voice else None)
        span, detail = await _shadow_span(collector, trace)
        attrs = (span or {}).get("attrs") or {}
        row = {"text": text, "gold_addressed": addressed, "voice": voice, "trace_id": trace,
               "intents": str((detail.get("turn") or {}).get("intents") or ""),
               "shadow": bool(span), "status": attrs.get("status"), "p_true": attrs.get("p_true"),
               "planner_addressed": attrs.get("planner_addressed"), "agree": attrs.get("agree"),
               "latency_ms": (span or {}).get("duration_ms"), "text_leaked": text in json.dumps(attrs, ensure_ascii=False),
               "actions": obs.get("actions") or []}
        result["rows"].append(row)
        print(f"{text[:16]:<17} gold={int(addressed)} voice={int(voice)} shadow={int(row['shadow'])} "
              f"status={row['status']} p={row['p_true']} planner={row['planner_addressed']} agree={row['agree']} "
              f"ms={row['latency_ms']}")
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
    scored = [r for r in rows if r["p_true"] is not None]
    summary = {
        "turns": len(rows), "with_shadow": sum(r["shadow"] for r in rows),
        "ok": sum(r["status"] == "DECISION_STATUS_OK" for r in rows),
        "timeout": sum(r["status"] == "DECISION_STATUS_TIMEOUT" for r in rows),
        "jev_correct": sum((r["p_true"] >= 0.5) == r["gold_addressed"] for r in scored),
        "planner_correct": sum(r["planner_addressed"] == r["gold_addressed"] for r in rows if r["planner_addressed"] is not None),
        "agree": sum(bool(r["agree"]) for r in rows), "text_leaked": sum(r["text_leaked"] for r in rows),
        "actions": sum(len(r["actions"]) for r in rows),
    }
    result["summary"] = summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary:", summary, result.get("error") or result.get("continuity_errors") or "")
    # shadow 有预算，偶发超时如实记为 TIMEOUT、不算失败；其他非 OK 状态（无凭证、校验失败…）算失败
    ok = (not result.get("error") and not result.get("continuity_errors") and summary["actions"] == 0
          and summary["text_leaked"] == 0 and summary["with_shadow"] == summary["turns"]
          and summary["ok"] + summary["timeout"] == summary["turns"] and summary["ok"] > 0)
    print("verdict:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
