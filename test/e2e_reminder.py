"""真栈闭环：WS 创建（相对秒级）→ NATS agent.proactive 收 reminder_fired（带卡）
→ 列表（fired 未完成仍可见）→ P1a snooze 改期原条目（无尸体）→ 完成 → 清空确认续接
（自清理可重入）。

前置：make up 起全栈。依赖：pip install websockets nats-py
用法：python test/e2e_reminder.py
"""
import asyncio
import json
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
NATS_URL = "nats://localhost:4222"
SESSION = f"e2e-reminder-{int(time.time())}"
TIMEOUT = 60
_results: list[bool] = []


def record(name: str, ok: bool, detail: str = ""):
    _results.append(ok)
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


async def ask(text: str, desc: str) -> dict:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"text": text, "session_id": SESSION}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                print(f"  [{desc}] {text} → {msg.get('speech', msg.get('message', ''))[:60]}")
                return msg


async def main() -> int:
    # 1) 创建（20秒后）→ 回读确认
    r = await ask("20秒后提醒我E2E演练提醒", "创建")
    record("1.创建回读", r.get("type") == "final" and "E2E演练提醒" in r.get("speech", ""))

    # 2/3) 订 NATS 等 reminder_fired（20s 相对时间 + 5s 轮询 → 40s 内必到）
    got: list[dict] = []
    try:
        import nats
        nc = await nats.connect(NATS_URL)

        async def on_msg(m):
            try:
                p = json.loads(m.data.decode())
                if p.get("agent_id") == "reminder":
                    got.append(p)
            except Exception:
                pass

        sub = await nc.subscribe("agent.proactive", cb=on_msg)
        for _ in range(80):
            if got:
                break
            await asyncio.sleep(0.5)
        await sub.unsubscribe()
        await nc.close()
    except Exception as e:
        print(f"  NATS 订阅失败：{e}")
    ok_fire = bool(got) and got[0].get("type") == "reminder_fired" \
        and "E2E演练提醒" in got[0].get("speech", "")
    card_type = (got[0].get("card") or {}).get("type", "") if got else ""
    record("2.到点触达(NATS)", ok_fire, got[0].get("speech", "")[:40] if got else "未收到")
    record("3.触达带卡", card_type in ("reminder_card", "card_group"), card_type)

    # 4) 列表：fired 未完成仍可见（诚实呈现，设计 §4）
    r = await ask("我今天有什么安排", "列表")
    record("4.列表含该条", "E2E演练提醒" in r.get("speech", ""))

    # 4b) P1a snooze：改期原条目，列表仍 1 条（旧实现会新建第二条留 fired 尸体）
    r = await ask("10分钟后再提醒我E2E演练提醒", "snooze")
    ok_snooze = "再提醒你" in r.get("speech", "")
    r = await ask("我今天有什么安排", "snooze后列表")
    record("4b.snooze改期无尸体", ok_snooze and "共 1 条" in r.get("speech", ""),
           r.get("speech", "")[:40])

    # 5) 完成（pending/fired 均可完成）
    r = await ask("完成提醒：E2E演练提醒", "完成")
    record("5.完成", "已完成" in r.get("speech", ""))

    # 6) 清空：NEED_CONFIRM → 确认续接（engine meta.confirmed 契约）；也是自清理
    r = await ask("把提醒都清空", "清空请求")
    if r.get("need_confirm"):
        r2 = await ask("确定", "确认")
        record("6.清空确认闭环", "清空" in r2.get("speech", ""))
    else:
        record("6.清空确认闭环", "没有" in r.get("speech", ""), "已无活动项，直答")

    print(f"\n{'ALL PASS' if all(_results) else 'FAILED'} ({sum(_results)}/{len(_results)})")
    return 0 if all(_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
