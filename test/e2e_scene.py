"""真栈闭环：一句话造场景 → 回读确认 → 激活（车况真变）→ 退出（车况真恢复）→ 同名遮蔽。

这条链路专门钉死重构要根治的两处硬伤：
- 硬伤 1「用户不能造场景」：LLM 编译 → 白名单校验 → 回读确认 → 落 PG → 随叫随到；
- 硬伤 2「deactivate 是嘴炮」：退出必须把车真的恢复回**激活前**的值（不是默认值）——
  故用例先把空调开到 26 度（一个非默认值），激活场景压到 22 度，退出后必须回到 26。

前置：make up 起全栈（改过源码要 --build，无卷挂载）；容器重建后等 ≥40s
（registry 重注册 10s + edge 车况全量快照周期 OBS_SNAPSHOT_INTERVAL=30s，
场景 Agent 的车况镜像要靠它填满，否则快照全空只能退反向默认表）。
用法：python test/e2e_scene.py
"""
import asyncio
import json
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

URL = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
NATS_URL = "nats://localhost:4222"
SESSION = f"e2e-scene-{int(time.time())}"      # e2e- 前缀：跳过记忆抽取（conventions §9.2）
TIMEOUT = 90
_results: list[bool] = []


def record(name: str, ok: bool, detail: str = ""):
    _results.append(ok)
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


def vehicle_state() -> dict:
    with urllib.request.urlopen(f"{COLLECTOR}/api/vehicle/state", timeout=10) as r:
        return json.loads(r.read().decode())


def debug_vehicle(key: str, value) -> None:
    """压车辆环境（行车态/电量/车内温度）：collector → NATS → 端侧 VAL 白名单键。"""
    req = urllib.request.Request(
        f"{COLLECTOR}/api/debug/vehicle",
        data=json.dumps({"key": key, "value": value}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


async def ask(text: str, desc: str) -> dict:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"text": text, "session_id": SESSION}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                print(f"  [{desc}] {text} → {msg.get('speech', msg.get('message', ''))[:80]}")
                return msg


async def settle(seconds: float = 2.5) -> dict:
    """等动作经端侧 VAL 落地 + 状态 diff 经 NATS 回到 collector 镜像。"""
    await asyncio.sleep(seconds)
    return vehicle_state()


async def main() -> int:
    print(f"session={SESSION}")

    # 0) 先把空调开到 26 度——一个**非默认**的激活前状态，用来证明退出走的是快照而非默认表
    await ask("把空调调到26度", "前置")
    st0 = await settle()
    base_temp = st0.get("hvac_temp")
    record("0.前置车况", base_temp == 26, f"hvac_temp={base_temp}")

    # 1) 创建：一句话 → 回读确认（含动作清单 + 做不到的诚实告知）
    r = await ask("帮我创建一个钓鱼模式：氛围灯调到10%，空调22度", "创建")
    sp = r.get("speech", "")
    record("1.创建回读", r.get("type") == "final" and "钓鱼模式" in sp
           and ("保存吗" in sp or "确认" in sp), sp[:50])
    card = r.get("ui_card") or {}
    record("1b.回读卡片", card.get("type") == "scene_card"
           and len(card.get("actions_preview") or []) >= 2,
           f"actions_preview={len(card.get('actions_preview') or [])}")

    # 2) 确认 → 落库
    r = await ask("确认", "确认保存")
    record("2.确认落库", "钓鱼模式" in r.get("speech", "")
           and ("开启钓鱼模式" in r.get("speech", "") or "好" in r.get("speech", "")))

    # 3) 激活 → 车况真变 + scene_mode 状态位
    await ask("开启钓鱼模式", "激活")
    st1 = await settle()
    record("3.激活生效", st1.get("hvac_temp") == 22
           and st1.get("ambient_light_brightness") == 10 and st1.get("ambient_light") is True,
           f"hvac_temp={st1.get('hvac_temp')} 灯={st1.get('ambient_light_brightness')}")
    record("3b.场景状态位", st1.get("scene_mode") == "钓鱼模式",
           f"scene_mode={st1.get('scene_mode')}")

    # 4) 退出 → 恢复到**激活前**的 26 度（不是默认 24），氛围灯关回去
    await ask("退出钓鱼模式", "退出")
    st2 = await settle()
    record("4.退出真恢复", st2.get("hvac_temp") == base_temp,
           f"hvac_temp={st2.get('hvac_temp')}（激活前 {base_temp}，默认表是 24）")
    record("4b.氛围灯还原", st2.get("ambient_light") is False,
           f"ambient_light={st2.get('ambient_light')}")
    record("4c.场景位清空", st2.get("scene_mode") == "off",
           f"scene_mode={st2.get('scene_mode')}")

    # 5) 同名遮蔽：自建「露营模式」（只开灯，无座椅）→ 激活走用户版，不再要座椅确认
    await ask("帮我创建一个露营模式：氛围灯调到20%", "遮蔽-创建")
    r = await ask("确认", "遮蔽-确认")
    record("5.同名场景已存", "露营模式" in r.get("speech", ""))
    r = await ask("开启露营模式", "遮蔽-激活")
    sp = r.get("speech", "")
    record("5b.用户版遮蔽预置", "需要您确认" not in sp and "已为您开启露营模式" in sp,
           "用户版无座椅动作 → 不该要确认" if "需要您确认" in sp else sp[:40])
    st3 = await settle()
    record("5c.遮蔽版生效", st3.get("ambient_light_brightness") == 20,
           f"灯={st3.get('ambient_light_brightness')}（预置版是 30）")

    # 6) 列表：我建的 / 内置分组
    r = await ask("有哪些场景模式", "列表")
    card = r.get("ui_card") or {}
    mine = [x["name"] for x in (card.get("mine") or [])]
    record("6.列表分组", card.get("type") == "scene_list"
           and "钓鱼模式" in mine and "露营模式" in mine
           and len(card.get("builtin") or []) == 3,      # 露营被用户版遮蔽 → 内置剩 3 个
           f"mine={mine} builtin={len(card.get('builtin') or [])}")

    await ask("退出露营模式", "遮蔽-退出")

    # 7) P1 参数覆盖：「开启午休模式，温度26」→ 场景里写的是 24，原话的 26 要赢
    await ask("开启午休模式，温度26", "参数覆盖")
    await ask("确认", "参数覆盖-确认")           # 午休含座椅放平 → 危险动作要确认
    st4 = await settle()
    record("7.custom_params 覆盖", st4.get("hvac_temp") == 26,
           f"hvac_temp={st4.get('hvac_temp')}（场景里写的是 24）")
    await ask("退出午休模式", "参数覆盖-退出")
    await ask("确认", "参数覆盖-退出确认")

    # 8) P1 会话沉淀（D11 桥）：先手动调两下车，再「把刚才这些存成加班模式」
    await ask("把空调调到28度", "沉淀-操作1")
    await ask("氛围灯调到45%", "沉淀-操作2")
    r = await ask("把刚才这些存成加班模式", "沉淀-固化")
    record("8.会话沉淀回读", r.get("type") == "final" and "加班模式" in r.get("speech", ""),
           r.get("speech", "")[:60])
    await ask("确认", "沉淀-确认")
    await ask("把空调调到20度", "沉淀-打乱现场")     # 先破坏现场，再看激活能不能还原
    await ask("开启加班模式", "沉淀-激活")
    st5 = await settle()
    record("8b.沉淀场景可复用", st5.get("hvac_temp") == 28,
           f"hvac_temp={st5.get('hvac_temp')}（沉淀时是 28，激活前被打乱成 20）")

    await ask("退出加班模式", "沉淀-退出")

    # 9) P2 Verify-Repair：VAL 安全门控拒掉一条动作 → 即时话术不再被后续成功掩埋
    #    + 后台对账诚实汇报 → 环境恢复后重新激活，幂等只补缺失项
    #    门控用**真实存在**的那条：低电量(<10%)禁高耗电功能（氛围灯）。
    #    注意 seat 在 commands.yaml 里是 drive_restricted: false——VAL 并不拦行车中的座椅，
    #    别拿"行车禁座椅"当前提（设计原文的假设，实测不成立）。
    proactive: list[dict] = []
    nc = None
    try:
        import nats
        nc = await nats.connect(NATS_URL)

        async def on_msg(m):
            try:
                p = json.loads(m.data.decode())
                if p.get("agent_id") == "scene-orchestrator":
                    proactive.append(p)
            except Exception:
                pass

        await nc.subscribe("agent.proactive", cb=on_msg)
    except Exception as e:
        print(f"  [警告] NATS 订阅失败，跳过 verify 断言：{e}")

    debug_vehicle("battery", 5)           # 低电量：VAL 禁高耗电功能（氛围灯）
    await asyncio.sleep(1.5)
    await ask("开启午休模式", "P2-低电量激活")
    r = await ask("确认", "P2-低电量确认")
    sp = r.get("speech", "")
    record("9.拒绝不被成功掩埋", "电量" in sp,
           sp[:60])                        # 5 个动作里第 3 条被拒、后两条成功——旧实现只播最后一条
    st6 = await settle(3)
    record("9b.氛围灯确实被门控", st6.get("ambient_light_brightness") != 10
           and st6.get("seat_recline") == 160,
           f"灯={st6.get('ambient_light_brightness')} 座椅={st6.get('seat_recline')}")

    if nc:
        for _ in range(30):                # verify 后台等 4s 再对账
            if proactive:
                break
            await asyncio.sleep(0.5)
        vs = proactive[0].get("speech", "") if proactive else ""
        record("9c.后台诚实汇报", bool(proactive) and "没有生效" in vs and "氛围灯" in vs,
               vs[:70] or "没收到 scene_verify proactive")

    debug_vehicle("battery", 72)           # 电量恢复
    await asyncio.sleep(1.5)
    r = await ask("开启午休模式", "P2-幂等重激活")
    sp = r.get("speech", "")
    record("9d.幂等只补缺失项", "跳过" in sp,
           sp[:70])                        # 座椅/音量/空调已达成 → 只剩氛围灯；座椅不再要确认
    st7 = await settle(3)
    record("9e.补上了氛围灯", st7.get("ambient_light_brightness") == 10,
           f"灯={st7.get('ambient_light_brightness')}")
    if nc:
        await nc.close()
    await ask("退出午休模式", "P2-收尾")
    await ask("确认", "P2-收尾确认")

    # 10) 自清理（可重入）：删掉本次建的场景，露营模式回归内置
    await ask("退出场景", "清理-退出")
    for name in ("钓鱼模式", "露营模式", "加班模式"):
        await ask(f"删掉{name}", f"清理-删{name}")
        await ask("确认", "清理-确认")
    r = await ask("有哪些场景模式", "清理-复查")
    card = r.get("ui_card") or {}
    record("10.自清理", not (card.get("mine") or []) and len(card.get("builtin") or []) == 4,
           f"mine={[x['name'] for x in (card.get('mine') or [])]} "
           f"builtin={len(card.get('builtin') or [])}")

    ok = all(_results)
    print(f"\n{'✅ 全部通过' if ok else '❌ 有失败'}："
          f"{sum(_results)}/{len(_results)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
