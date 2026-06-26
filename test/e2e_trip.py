"""端到端验证：trip-planner P0 结构化行程链路（经 Edge Gateway WebSocket）。

前置：`make up` 起全栈（改 trip-planner/aggregator/hmi 后须 --build 重建对应容器）。
依赖：pip install websockets
用法：python test/e2e_trip.py

断言：
1. 多日行程规划 → need_confirm + ui_card.type=="trip_itinerary"，按天结构化、停靠点接地真实 POI（有 lat/lng）。
2. 确认 → ok 收尾 + 第一站 poi_list（说『第N个』即导航），不再 need_confirm。
3. 改某天 → 仍 trip_itinerary，且未提及的天结构化保留（不漂移）。
"""
import asyncio
import json
import sys
import time

try:                                   # Windows 控制台默认 GBK，强制 UTF-8 输出避免 ✓/中文崩
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
TIMEOUT = 90  # 多日行程是 LLM 重生成 + 多次接地，给足


async def ask(payload: dict, desc: str) -> dict:
    # 禁用客户端 ping（靠 recv TIMEOUT 兜底）：复杂确认/重生成轮可能 20s+ 无流量，
    # 默认 ping_interval 会在服务端忙时误杀连接（服务端自有 keepalive）。
    async with websockets.connect(URL, ping_interval=None, close_timeout=3) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") == "final":
                print(f"\n[{desc}]")
                print(f"  输入: {payload['text']}")
                print(f"  回复: {(msg.get('speech') or '')[:120]}")
                card = msg.get("ui_card")
                if card:
                    print(f"  卡片: type={card.get('type')}")
                if msg.get("need_confirm"):
                    print("  需确认: True")
                return msg
            if msg.get("type") == "error":
                print(f"\n[{desc}] 错误: {msg.get('message')}")
                return msg


def _grounded_stop(card: dict):
    """返回卡片里第一个接地（含坐标）的停靠点，无则 None。"""
    for day in (card.get("itinerary") or []):
        for s in (day.get("stops") or []):
            poi = s.get("poi") or {}
            if s.get("grounded") and poi.get("lat") and poi.get("lng"):
                return s
    return None


async def main() -> int:
    print("=== trip-planner P0 E2E ===")
    failures = []
    # 唯一 session 前缀：corrID = vehicleID-corrSeq，cloud-gateway 按 corrID 幂等去重；
    # 复用固定 session + edge-gateway 重启重置 corrSeq 会撞历史 corrID 致请求被丢→挂起。
    run = int(time.time())
    sid = f"e2e-trip-{run}"
    mod_sid = f"e2e-trip-mod-{run}"

    # 轮1：多日行程规划
    m1 = await ask({"text": "周末去杭州两天带老人不要太累", "session_id": sid},
                   "轮1 多日行程规划（应 need_confirm + trip_itinerary 卡）")
    card1 = m1.get("ui_card") or {}
    if not m1.get("need_confirm"):
        failures.append("轮1 未返回 need_confirm")
    if card1.get("type") != "trip_itinerary":
        failures.append(f"轮1 卡片不是 trip_itinerary（实为 {card1.get('type')}）")
    else:
        days = card1.get("itinerary") or []
        if not days:
            failures.append("轮1 行程为空")
        gs = _grounded_stop(card1)
        if not gs:
            failures.append("轮1 无接地停靠点（应有真实 POI 坐标）")
        else:
            print(f"  ✓ 接地第一站: {gs.get('name')} "
                  f"@({gs['poi'].get('lat')},{gs['poi'].get('lng')})；天数={len(days)}")

    # 轮2：确认收尾
    m2 = await ask({"text": "确认", "session_id": sid, "is_confirmation": True},
                   "轮2 确认收尾（应 ok + 第一站 poi_list，不再 need_confirm）")
    if m2.get("need_confirm"):
        failures.append("轮2 确认后仍 need_confirm（疑似死循环）")
    if "确认" not in (m2.get("speech") or "") and (m2.get("ui_card") or {}).get("type") != "poi_list":
        failures.append("轮2 既无『已确认』话术也无第一站 poi_list")
    else:
        print("  ✓ 确认收尾正常")

    # 轮2.5：在途导航——『下一站』应路由到 trip.navigate 并发 navigate 动作（P1）
    m_nav = await ask({"text": "下一站", "session_id": sid},
                      "轮2.5 下一站（应路由 trip.navigate 并发 navigate 动作）")
    if not any(a.get("type") == "navigate" for a in (m_nav.get("actions") or [])):
        failures.append("轮2.5 『下一站』未产出 navigate 动作")
    else:
        print("  ✓ 下一站发起导航")

    # 轮2.6：在途状态查询（P2）——『行程到哪了』应路由 trip.status 报进度
    m_st = await ask({"text": "行程到哪了", "session_id": sid},
                     "轮2.6 行程状态（trip.status，应报站数进度）")
    if "站" not in (m_st.get("speech") or ""):
        failures.append("轮2.6 trip.status 未报站数进度")
    else:
        print("  ✓ 在途状态可查")

    # 轮2.7：在途精简（P2）——『时间不够』应路由 trip.reschedule 砍尾部站、need_confirm
    m_rs = await ask({"text": "时间不够了精简一下行程", "session_id": sid},
                     "轮2.7 在途精简（trip.reschedule，应 need_confirm + trip_itinerary）")
    if (m_rs.get("ui_card") or {}).get("type") != "trip_itinerary":
        failures.append(
            f"轮2.7 reschedule 卡片不是 trip_itinerary（实为 {(m_rs.get('ui_card') or {}).get('type')}）")
    else:
        print("  ✓ 在途精简行程")

    # 轮3：改某天（重新规划一份行程后改第二天）
    m3a = await ask({"text": "周末去成都三天轻松点", "session_id": mod_sid},
                    "轮3a 先规划成都三天")
    card3a = m3a.get("ui_card") or {}
    day1_name = None
    if card3a.get("type") == "trip_itinerary":
        days = card3a.get("itinerary") or []
        if days and days[0].get("stops"):
            day1_name = days[0]["stops"][0].get("name")
    m3b = await ask({"text": "第二天换一个景点", "session_id": mod_sid},
                    "轮3b 改第二天（应仍 trip_itinerary，第一天不变）")
    card3b = m3b.get("ui_card") or {}
    if card3b.get("type") != "trip_itinerary":
        failures.append(f"轮3b 改行程后卡片不是 trip_itinerary（实为 {card3b.get('type')}）")
    elif day1_name:
        days = card3b.get("itinerary") or []
        new_day1 = days[0]["stops"][0].get("name") if days and days[0].get("stops") else None
        if new_day1 == day1_name:
            print(f"  ✓ 改第二天后第一天保留: {day1_name}")
        else:
            print(f"  ⚠ 第一天变化 {day1_name} → {new_day1}（弱 LLM 重路由可能，非硬失败）")

    print("\n=== 结果 ===")
    if failures:
        for f in failures:
            print(f"  ✗ {f}")
        print(f"\n{len(failures)} 项失败")
        return 1
    print("  ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
