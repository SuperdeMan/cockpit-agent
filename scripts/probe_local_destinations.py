"""真栈探针：日常具名地点解析到地点本身，而不是借了它名字的近处附属地点。

只读：只问距离估算（「去X要开多久」），每个目的地用一个签名的合成 E2E 用户、车辆 v1 的新会话；不导航、不确认、不发车控、
不调商户 / 支付。每个目的地的估算话术要带上预期名字之一（docs/design/2026-10-04-local-destination-ranking.md §3 A/B），
且不出现它曾落到的附属地点名；「鼓浪屿」要给出两处且厦门在前。带类目词的通称（§7）还要在全程距离上限内（它们曾去北京）；
城市限定的外地名（§8）不能落到本地同名点；带锚词的「东莞松山湖」（§9）要落到松山湖、「小梅沙海滩」（§10）要落到沙滩；
口头叫法（§13）落到本地门店、不去北京，「北大医院」落到北京大学深圳医院，名字毫不相干的近处结果不当目的地（「301医院」说没找到）。
前后各核一次 release SHA，写一份 JSON 证据文件。
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
# 目的地 → (话术里应出现的名字之一，空 = 不限; 不应再出现的附属地点 / 城市)
CASES = {
    "大梅沙": (("大梅沙海滨",), ("推拿",)),
    "蛇口港": (("蛇口港",), ("公安局",)),
    "东门": (("东门商业步行街", "东门步行街", "东门(地名"), ("食堂",)),
    "海岸城": (("海岸城购物中心",), ("停车场",)),
    "会展中心": (("深圳会展中心",), ("国家会议中心",)),
    "山姆会员店": (("山姆会员商店",), ("北京",)),
    "厦门火车站": (("厦门站", "厦门火车站"), ()),
    # §7 带类目词的通称：不再去北京，连锁店是近处那家
    "儿童医院": (("儿童医院",), ("北京", "首都医科大学")),
    "口腔医院": (("口腔",), ("北京",)),
    "7-11便利店": (("7-ELEVEn", "7-Eleven", "7-11"), ("北京",)),
    "华润万家超市": (("华润万家",), ()),
    "医院": ((), ("北京",)),
    # §8 模型猜名去掉原话里的城市：不落到只同名后半截的本地点
    "上海外滩": (("外滩",), ("夜市",)),
    "长沙橘子洲": (("橘子洲",), ("小炒",)),
    # §9 估算与导航用同样多的候选：锚词查询不靠模型猜名
    "东莞松山湖": (("松山湖",), ("马拉松", "公交站")),
    # §10「XX海滩」剥两个字取主干：落到沙滩本体，不是同前缀的海洋世界
    "小梅沙海滩": (("沙滩",), ("海洋世界",)),
    # §13 口头叫法 + 名字毫不相干的兜底不当目的地
    "7-11": (("7-ELEVEn", "7-Eleven", "7-11"), ("北京",)),
    "KFC": (("肯德基", "KFC"), ("北京",)),
    "北大医院": (("北京大学深圳医院",), ("眼科", "角膜", "北京大学第一医院")),
    "301医院": (("没找到", "无法确定"), ("美妍", "美容", "门诊部")),
}
ASK = {"鼓浪屿": "厦门"}          # 应发问：候选第一个带这个城市
# 话术报的全程公里数上限（「全程约X公里」；话术不报距离时不判）：类目通称该在本城、连锁店该是近处那家
MAX_KM = {"儿童医院": 60, "口腔医院": 15, "7-11便利店": 15, "华润万家超市": 6, "医院": 15, "7-11": 15, "KFC": 15,
          "北大医院": 60}
# 话术报的全程公里数下限：城市限定的外地名不该落在本城
MIN_KM = {"上海外滩": 1000, "长沙橘子洲": 500, "东莞松山湖": 30}
_ROUTE_KM = re.compile(r"全程约\s*([\d.]+)\s*公里")


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
            ok = (not want or any(w in speech for w in want)) and not any(a in speech for a in avoid)
        km = _ROUTE_KM.search(speech)
        if dest in MAX_KM and km:
            ok = ok and float(km.group(1)) <= MAX_KM[dest]
        if dest in MIN_KM:
            ok = ok and bool(km) and float(km.group(1)) >= MIN_KM[dest]
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
