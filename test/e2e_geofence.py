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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

WS = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
NATS_URL = "nats://localhost:4222"
SESSION = f"e2e-geofence-{int(time.time())}"     # e2e- 前缀：跳过记忆抽取（conventions §9.2）
# 用一个**真实可解析**的 POI：地点坐标由 Agent 经 nearby(高德) 真解析后落库，
# 脚本再把它读回来当围栏中心——这样验的是真实解析链路，而不是脚本自己编的坐标。
PLACE = "望京SOHO"
TITLE = "拿文件"
CITY_CENTER = {"lat": 39.9950, "lng": 116.4800, "city": "北京市", "name": "北京市朝阳区"}
_results: list[bool] = []


def record(name, ok, detail=""):
    _results.append(bool(ok))
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


def stored_geofence():
    """从 PG 读回刚建的位置提醒的围栏坐标（e2e_ledger 的 psql 取数同款）。"""
    q = ("SELECT extra::text FROM reminder_item WHERE kind='location' "
         "AND status='pending' ORDER BY created_at DESC LIMIT 1")
    out = subprocess.run(
        ["docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit",
         "-d", "cockpit", "-t", "-A", "-c", q],
        capture_output=True, text=True, timeout=60)
    # 注：Windows 上 psql 回显的中文可能是乱码（客户端编码），**库里是好的**——
    # 本函数只取 lat/lon，不受影响；话术断言才是中文正确性的判据。
    line = (out.stdout or "").strip().splitlines()
    if not line:
        return None
    try:
        return json.loads(line[0])
    except Exception:
        return None


def debug_vehicle(key, value):
    req = urllib.request.Request(
        f"{COLLECTOR}/api/debug/vehicle",
        data=json.dumps({"key": key, "value": value}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


async def ask(text: str) -> dict:
    """一问一答，返回 final 帧。"""
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "user_text", "text": text,
                                  "session_id": SESSION}))
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


async def main():
    try:
        import nats
    except ImportError:
        print("请先：pip install nats-py")
        return 1
    nc = await nats.connect(NATS_URL, connect_timeout=5)
    fired = []

    async def on_proactive(m):
        try:
            fired.append(json.loads(m.data.decode()))
        except Exception:
            pass
    await nc.subscribe("agent.proactive", cb=on_proactive)

    # 显式前置：车在市区。地点解析走 nearby（**邻近搜索**，不是通用地理编码）——
    # 车在 50 公里外时 PLACE 落在搜索半径外，Agent 会诚实追问而不是乱猜（这是对的行为，
    # 但会让本用例失去验证对象）。不设这个前置，脚本就依赖上一个 e2e 留下的车况。
    debug_vehicle("location", CITY_CENTER)
    await asyncio.sleep(2)

    print("── 1. 创建位置提醒 ──")
    res = await ask(f"到{PLACE}提醒我{TITLE}")
    speech = res.get("speech", "")
    ok_create = "提醒你" in speech and TITLE in speech
    record("建单：到某地提醒我某事（地点经 nearby 真解析出坐标）", ok_create, speech[:70])
    if not ok_create:
        # 地点解析不出时应当是**诚实追问**，不是假装记下了——这本身是合格行为，
        # 但后续步骤没有对象可验，如实报告后退出。
        record("（解析不出时诚实追问，不存永不触发的提醒）",
               "在哪" in speech or "地址" in speech, speech[:70])
        await nc.close()
        return 1

    geo = stored_geofence()
    record("围栏坐标已落库", bool(geo and geo.get("lat") and geo.get("lon")), str(geo))
    if not geo:
        await nc.close()
        return 1
    # city 必须是**真实地名**：road-safety 订同一条广播、按 city 查天气预警，
    # 塞个查不到的地名会打 400 直到熔断器打开，把天气域一起拖垮（2026-07-25 真栈实测）。
    OFFICE = {"lat": geo["lat"], "lng": geo["lon"], "city": "北京市", "name": PLACE}
    FAR = {"lat": geo["lat"] + 0.5, "lng": geo["lon"] + 0.5,
           "city": "北京市", "name": "围栏外"}

    print("── 2. 围栏外：只播种不触发 ──")
    fired.clear()
    debug_vehicle("location", FAR)
    await asyncio.sleep(4)
    record("围栏外不触达", not [m for m in fired if m.get("type") == "reminder_fired"],
           f"收到 {len(fired)} 条主动消息")

    print("── 3. 进围栏：触达一次 ──")
    fired.clear()
    debug_vehicle("location", OFFICE)
    for _ in range(30):
        if any(m.get("type") == "reminder_fired" for m in fired):
            break
        await asyncio.sleep(0.5)
    hits = [m for m in fired if m.get("type") == "reminder_fired"]
    record("进围栏触达一次", len(hits) == 1, f"收到 {len(hits)} 条")
    if hits:
        sp = hits[0].get("speech", "")
        record("话术含到达地点与事项", f"到{PLACE}了" in sp and TITLE in sp, sp[:70])
        record("卡片按位置语义展示", (((hits[0].get("card") or {}).get("item") or {})
                                     .get("time_display") or "").startswith("到"),
               str(((hits[0].get("card") or {}).get("item") or {}).get("time_display")))
    else:
        record("话术含到达地点与事项", False, "未触达")
        record("卡片按位置语义展示", False, "未触达")

    print("── 4. 再次进出：不重复触达 ──")
    fired.clear()
    debug_vehicle("location", FAR)
    await asyncio.sleep(3)
    debug_vehicle("location", OFFICE)
    await asyncio.sleep(6)
    record("已触发条目不重复触达",
           not [m for m in fired if m.get("type") == "reminder_fired"],
           f"收到 {len(fired)} 条")

    debug_vehicle("location", FAR)
    await nc.close()
    ok = sum(_results)
    print(f"\n===== e2e_geofence: {ok}/{len(_results)} =====")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
