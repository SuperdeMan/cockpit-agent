"""真栈闭环：位置提醒（M3 P1）。

四步：
1. 「到<真实POI>提醒我拿文件」→ 建单成功（地点经 nearby/高德**真解析**出坐标才建；
   解析不出必须诚实追问，绝不存一条永远不会触发的提醒）。
2. 注入车况 location 到**围栏外** → 播种，不触发（首次观测只播种是刻意设计：
   人已经在目的地时创建提醒，立刻响一声不是用户要的，用户要的是「下次到」）。
3. 注入车况 location 到**围栏内** → 触达一次，话术含「到X了」+ 事项。
4. 再次进出 → **不重复触达**（条目已 fired，claim_location 原子领取保证）。

前置：make up 全栈 + AMAP_KEY（地点解析走真高德）。位置来源：debug 通道
（PoC 没有真实 GPS 流；road-safety 的「进入新区域」播报走的是同一条路）。
用法：python test/e2e_geofence.py
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from support.e2e import (
    CaseRecorder,
    assert_persistent_source_contract,
    postgres_psql_argv,
)


def _source_contract() -> None:
    assert_persistent_source_contract(Path(__file__).read_text(encoding="utf-8"))


if "--source-contract" in sys.argv:
    _source_contract()
    print("source contract: PASS")
    raise SystemExit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    websockets = None

WS = ""
COLLECTOR = "http://localhost:8092"
NATS_URL = "nats://localhost:4222"
# 用一个**真实可解析**的 POI：地点坐标由 Agent 经 nearby(高德) 真解析后落库，
# 脚本再把它读回来当围栏中心——这样验的是真实解析链路，而不是脚本自己编的坐标。
PLACE = "望京SOHO"
TITLE = "拿文件"
CITY_CENTER = {"lat": 39.9950, "lng": 116.4800, "city": "北京市", "name": "北京市朝阳区"}
PG = postgres_psql_argv()
_recorder: CaseRecorder | None = None


def creation_text() -> str:
    """隔离放在 user/session 命名空间，不把运行标识泄漏进用户自然话术。"""
    return f"到{PLACE}提醒我{TITLE}"


def record(case_id, name, ok, detail=""):
    if _recorder is None:
        raise RuntimeError("CaseRecorder is not initialized")
    if ok:
        _recorder.pass_case(case_id)
    else:
        _recorder.fail_case(case_id, "assertion_failed", detail or name)
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


def sql(query: str) -> str:
    try:
        out = subprocess.run(
            PG + [query],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("geofence SQL command timed out") from exc
    if out.returncode != 0:
        raise RuntimeError("geofence SQL command failed")
    return (out.stdout or "").strip()


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_count(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("geofence namespace count is invalid") from exc
    if value < 0 or str(value) != raw:
        raise RuntimeError("geofence namespace count is invalid")
    return value


def namespace_count(user: str) -> int:
    return parse_count(sql(
        f"SELECT count(*) FROM reminder_item WHERE user_id={quoted(user)}",
    ))


def stored_geofence(user: str, title: str):
    q = (
        "SELECT json_build_object('id',id,'extra',extra)::text "
        "FROM reminder_item WHERE kind='location' AND status='pending' "
        f"AND user_id={quoted(user)} AND title={quoted(title)}"
    )
    raw = sql(q)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def vehicle_state() -> dict:
    with urllib.request.urlopen(f"{COLLECTOR}/api/vehicle/state", timeout=10) as response:
        return json.loads(response.read().decode())


def debug_vehicle(key, value):
    req = urllib.request.Request(
        f"{COLLECTOR}/api/debug/vehicle",
        data=json.dumps({"key": key, "value": value}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def cleanup_namespace(user: str, original_location) -> None:
    debug_vehicle("location", original_location)
    sql(f"DELETE FROM reminder_item WHERE user_id={quoted(user)}")
    remaining = namespace_count(user)
    if remaining:
        raise RuntimeError(f"geofence cleanup left {remaining} rows")


async def ask(text: str, session: str) -> dict:
    """一问一答，返回 final 帧。"""
    if websockets is None:
        raise RuntimeError("websockets is unavailable")
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "user_text", "text": text,
                                  "session_id": session}))
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("type") == "final":
                return msg
    return {}


async def run(recorder: CaseRecorder) -> None:
    global WS
    WS = recorder.ws_url()
    user = recorder.user_id()
    session = recorder.session_id(1)
    title = TITLE
    original_state = vehicle_state()
    if "location" not in original_state:
        recorder.fail_case(
            "location_snapshot",
            "isolation_precondition",
            "vehicle location snapshot is missing",
        )
        return
    original_location = original_state["location"]
    recorder.register_cleanup(
        user,
        lambda: cleanup_namespace(user, original_location),
    )
    if namespace_count(user) != 0:
        recorder.fail_case(
            "isolation_precondition",
            "isolation_precondition",
            "namespace was not empty before setup",
        )
        return
    try:
        import nats
    except ImportError:
        recorder.fail_case(
            "nats_available",
            "environment_unavailable",
            "nats-py is unavailable",
        )
        return
    nc = await nats.connect(NATS_URL, connect_timeout=5)
    recorder.pass_case("nats_available")
    fired = []

    async def on_proactive(m):
        try:
            payload = json.loads(m.data.decode())
            if (
                payload.get("user_id") == user
                and title in payload.get("speech", "")
            ):
                fired.append(payload)
        except Exception:
            pass
    await nc.subscribe("agent.proactive", cb=on_proactive)

    # 显式前置：车在市区。地点解析走 nearby（**邻近搜索**，不是通用地理编码）——
    # 车在 50 公里外时 PLACE 落在搜索半径外，Agent 会诚实追问而不是乱猜（这是对的行为，
    # 但会让本用例失去验证对象）。不设这个前置，脚本就依赖上一个 e2e 留下的车况。
    debug_vehicle("location", CITY_CENTER)
    await asyncio.sleep(2)

    print("── 1. 创建位置提醒 ──")
    res = await ask(creation_text(), session)
    speech = res.get("speech", "")
    ok_create = "提醒你" in speech and title in speech
    record("create_geofence", "建单：到某地提醒我某事（地点经 nearby 真解析出坐标）",
           ok_create, speech[:70])
    if not ok_create:
        # 地点解析不出时应当是**诚实追问**，不是假装记下了——这本身是合格行为，
        # 但后续步骤没有对象可验，如实报告后退出。
        record("honest_clarification", "（解析不出时诚实追问，不存永不触发的提醒）",
               "在哪" in speech or "地址" in speech, speech[:70])
        await nc.close()
        return

    stored = stored_geofence(user, title)
    geo = (stored or {}).get("extra") or {}
    created_id = (stored or {}).get("id") or ""
    record("geofence_persisted", "围栏坐标已落库",
           bool(created_id and geo.get("lat") and geo.get("lon")), str(geo))
    if not created_id:
        await nc.close()
        return
    # city 必须是**真实地名**：road-safety 订同一条广播、按 city 查天气预警，
    # 塞个查不到的地名会打 400 直到熔断器打开，把天气域一起拖垮（2026-07-25 真栈实测）。
    OFFICE = {"lat": geo["lat"], "lng": geo["lon"], "city": "北京市", "name": PLACE}
    FAR = {"lat": geo["lat"] + 0.5, "lng": geo["lon"] + 0.5,
           "city": "北京市", "name": "围栏外"}

    print("── 2. 围栏外：只播种不触发 ──")
    fired.clear()
    debug_vehicle("location", FAR)
    await asyncio.sleep(4)
    record("outside_no_fire", "围栏外不触达",
           not [m for m in fired if m.get("type") == "reminder_fired"],
           f"收到 {len(fired)} 条主动消息")

    print("── 3. 进围栏：触达一次 ──")
    fired.clear()
    debug_vehicle("location", OFFICE)
    for _ in range(30):
        if any(m.get("type") == "reminder_fired" for m in fired):
            break
        await asyncio.sleep(0.5)
    hits = [m for m in fired if m.get("type") == "reminder_fired"]
    record("inside_fires_once", "进围栏触达一次", len(hits) == 1,
           f"收到 {len(hits)} 条")
    if hits:
        sp = hits[0].get("speech", "")
        record("arrival_speech", "话术含到达地点与事项",
               f"到{PLACE}了" in sp and title in sp, sp[:70])
        record("location_card", "卡片按位置语义展示",
               (((hits[0].get("card") or {}).get("item") or {})
                                     .get("time_display") or "").startswith("到"),
               str(((hits[0].get("card") or {}).get("item") or {}).get("time_display")))
    else:
        record("arrival_speech", "话术含到达地点与事项", False, "未触达")
        record("location_card", "卡片按位置语义展示", False, "未触达")

    print("── 4. 再次进出：不重复触达 ──")
    fired.clear()
    debug_vehicle("location", FAR)
    await asyncio.sleep(3)
    debug_vehicle("location", OFFICE)
    await asyncio.sleep(6)
    record("no_repeat_fire", "已触发条目不重复触达",
           not [m for m in fired if m.get("type") == "reminder_fired"],
           f"收到 {len(fired)} 条")

    await nc.close()


def main() -> int:
    _source_contract()
    global _recorder
    _recorder = CaseRecorder()
    with _recorder:
        asyncio.run(run(_recorder))
    result = _recorder.result
    print(f"\n===== e2e_geofence: {result.counts['passed']}/{result.counts['selected']} =====")
    return _recorder.exit_code()


if __name__ == "__main__":
    sys.exit(main())
