"""真栈闭环：统一主动引擎（M3 P0）。

五个场景：
1. **单条直通** —— 治理键被剥掉、其余字节不变（下游网关/HMI 零改动的保证）。
2. **DoD 合并** —— 电量跌破阈值（真实 charging-planner 生产方经车况广播触发）与另一条
   同窗建议 → HMI **只响一条**，而不是两个 Agent 各响一次。
3. **同类去重跨生产方** —— 同 `dedup_key` 的第二条被丢，裁决事件说明理由。
4. **情境断言投递期复核** —— 声称「电量 < 20」的建议，在电量已恢复后**不说出口**。
5. **`user_contract` 豁免** —— 提醒档不受频控/负荷抑制（到点必响契约）。

fail-open（治理器停掉 → 生产方直发老主题）**不在本脚本**：它要停容器，
属真栈验收步骤（记录在 M3 子 RFC 落地记录），契约由
`proactive/tests/test_client_contract.py` 锁死。

前置：make up 起全栈（改过源码要 --build，无卷挂载）；容器重建后等 ≥40s
（edge 车况全量快照周期 OBS_SNAPSHOT_INTERVAL=30s，charging-planner 的电量镜像靠它填满）。
用法：python test/e2e_proactive.py
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

COLLECTOR = "http://localhost:8092"
NATS_URL = "nats://localhost:4222"
REQUEST_SUBJECT = "agent.proactive.request"
OUTPUT_SUBJECT = "agent.proactive"
DECISION_SUBJECT = "obs.proactive.decision"
_results: list[bool] = []


def record(name, ok, detail: str = ""):
    _results.append(bool(ok))
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


def restart(service: str, wait_s: float) -> None:
    subprocess.run(["docker", "compose", "restart", service],
                   capture_output=True, text=True, timeout=180)
    time.sleep(wait_s)


def reset_governor() -> None:
    """重启治理器 = 净初态。

    它**刻意没有持久化**（待发/延后队列与频控计数的生命周期以秒/小时计，落库不值当，
    子 RFC §7 明确不做）——所以重启就是最干净的重置，顺带证明了这条设计。
    不重置的话，全局频控（默认 6 条/小时）会让重复跑的第二遍全被 rate_limited 掐掉。
    """
    restart("proactive", 4)


def reset_low_battery_producer() -> None:
    """重启 charging-planner = 清掉它的**生产侧节流**（默认 30 分钟）与电量边沿状态。

    这层节流是产品行为（防读数在阈值附近抖动重复播报），不该为测试调小；
    它是进程内的，所以重启即净初态。等 15s 覆盖 registry 重注册，避免影响后续 e2e 步骤。
    """
    restart("charging-planner-agent", 15)


def debug_vehicle(key: str, value) -> None:
    """压车辆环境：collector → NATS → 端侧 VAL 白名单键 → vehicle.state.changed 广播。"""
    req = urllib.request.Request(
        f"{COLLECTOR}/api/debug/vehicle",
        data=json.dumps({"key": key, "value": value}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


class Bus:
    """收 agent.proactive 与裁决事件的探针。"""

    def __init__(self, nc):
        self.nc = nc
        self.out: list[dict] = []
        self.decisions: list[dict] = []

    async def start(self):
        async def on_out(m):
            try:
                self.out.append(json.loads(m.data.decode()))
            except Exception:
                pass

        async def on_dec(m):
            try:
                self.decisions.append(json.loads(m.data.decode()))
            except Exception:
                pass
        await self.nc.subscribe(OUTPUT_SUBJECT, cb=on_out)
        await self.nc.subscribe(DECISION_SUBJECT, cb=on_dec)

    def clear(self):
        self.out.clear()
        self.decisions.clear()

    async def send(self, payload: dict, *, expect_ack=True) -> bool:
        data = json.dumps(payload, ensure_ascii=False).encode()
        try:
            await self.nc.request(REQUEST_SUBJECT, data, timeout=1.0)
            return True
        except Exception:
            if expect_ack:
                print("   ⚠️ 治理器未 ack（未启动？）")
            return False

    async def wait_out(self, n=1, timeout=6.0) -> list:
        deadline = time.time() + timeout
        while time.time() < deadline and len(self.out) < n:
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)          # 收敛：确认没有第二条尾随
        return list(self.out)

    def decisions_for(self, agent_id: str) -> list:
        return [d for d in self.decisions if d.get("agent_id") == agent_id]


def probe(kind, **kw):
    p = {"type": kind, "agent_id": kw.pop("agent_id", "e2e-probe"),
         "speech": kw.pop("speech", "探针一句话。"), "ts": int(time.time() * 1000)}
    p.update(kw)
    return p


async def case_single_passthrough(bus):
    bus.clear()
    payload = probe("probe_single", speech="单条直通探针。", user_id="u1",
                    card={"type": "probe_card", "n": 1},
                    priority="advisory", dedup_key=f"e2e.single.{time.time()}",
                    ttl_ms=60000, conditions=[])
    await bus.send(payload)
    out = await bus.wait_out(1)
    got = [m for m in out if m.get("type") == "probe_single"]
    record("单条直通：投递到 agent.proactive", len(got) == 1, f"收到 {len(got)} 条")
    if not got:
        return
    m = got[0]
    stripped = all(k not in m for k in ("priority", "conditions", "dedup_key", "ttl_ms"))
    record("单条直通：治理键被剥掉（下游契约不变）", stripped, str(sorted(m.keys())))
    record("单条直通：其余字段逐字保留",
           m.get("speech") == "单条直通探针。" and m.get("card") == {"type": "probe_card", "n": 1}
           and m.get("user_id") == "u1")


async def case_dod_merge(bus):
    """DoD：低电量（真实生产方）+ 同窗另一条建议 → **一条**合并消息。"""
    bus.clear()
    debug_vehicle("battery", 66)                 # 先回到阈值上，制造干净的变沿
    await asyncio.sleep(2.0)
    bus.clear()
    debug_vehicle("battery", 18)                 # 跌破 20% → charging-planner 变沿触发
    # 同窗塞入第二条建议（站位真实的 scene 低电量触发：造场景需要一整条 LLM 编译链，
    # 本用例要验的是治理器的合并，不是场景编译；scene 生产方的迁移由单测覆盖）
    await asyncio.sleep(0.2)
    await bus.send(probe("scene_suggest", agent_id="scene-orchestrator",
                         speech="要开启省电出行模式吗？",
                         card={"type": "scene_card", "name": "省电出行"},
                         priority="advisory", dedup_key=f"e2e.scene.{time.time()}",
                         ttl_ms=120000))
    out = await bus.wait_out(1, timeout=12.0)
    charging = [m for m in out if m.get("type") == "charging_advice"]
    merged = [m for m in out if m.get("merged_from")]
    record("低电量：真实 charging-planner 生产方被车况变沿触发",
           bool(charging) or bool(merged), f"共收到 {len(out)} 条")
    record("DoD：同窗两条 → HMI 只响一条（不是两个 Agent 各响一次）",
           len(out) == 1, f"实际 {len(out)} 条：{[m.get('type') for m in out]}")
    if len(out) == 1 and out[0].get("merged_from"):
        m = out[0]
        record("DoD：合并后的一条里两件事都在",
               "18%" in m.get("speech", "") and "省电出行" in m.get("speech", ""),
               m.get("speech", "")[:70])
    else:
        record("DoD：合并后的一条里两件事都在", False, "未产生合并消息")


async def case_card_group(bus):
    """卡片合并单独验：DoD 用例里充电建议**可能没有卡**（拿不到位置时按铁律③只说事实、
    不编站点），卡张数不确定，不能拿它当卡片合并的判据。"""
    bus.clear()
    await bus.send(probe("probe_card_a", agent_id="e2e-a", speech="卡片甲。",
                         card={"type": "probe_card", "n": 1},
                         priority="advisory", dedup_key=f"e2e.card.a.{time.time()}"))
    await bus.send(probe("probe_card_b", agent_id="e2e-b", speech="卡片乙。",
                         card={"type": "probe_card", "n": 2},
                         priority="advisory", dedup_key=f"e2e.card.b.{time.time()}"))
    out = await bus.wait_out(1)
    mine = [m for m in out if str(m.get("type")).startswith("probe_card")]
    record("卡片合并：两条各带一张卡 → 一条消息", len(mine) == 1, f"收到 {len(mine)} 条")
    if mine:
        card = mine[0].get("card") or {}
        record("卡片合并：合成 card_group 且两张都在",
               card.get("type") == "card_group" and len(card.get("items") or []) == 2,
               str(card.get("type")))
    else:
        record("卡片合并：合成 card_group 且两张都在", False, "未产生合并消息")


async def case_dedup(bus):
    bus.clear()
    key = f"e2e.dedup.{time.time()}"
    await bus.send(probe("probe_dedup", agent_id="e2e-a", speech="第一条。",
                         priority="advisory", dedup_key=key))
    await bus.send(probe("probe_dedup2", agent_id="e2e-b", speech="第二条。",
                         priority="advisory", dedup_key=key))
    out = await bus.wait_out(1)
    mine = [m for m in out if str(m.get("type")).startswith("probe_dedup")]
    record("同类去重：跨生产方同 dedup_key 只过一条", len(mine) == 1,
           f"收到 {len(mine)} 条")
    dropped = [d for d in bus.decisions if d.get("decision") == "dropped"
               and d.get("reason") == "dedup"]
    record("同类去重：裁决事件说明理由 dedup", bool(dropped), str(dropped[:1]))


async def case_conditions_recheck(bus):
    debug_vehicle("battery", 66)                 # 电量已恢复
    await asyncio.sleep(2.0)
    bus.clear()
    await bus.send(probe("probe_cond", speech="电量低，要充电吗？",
                         priority="advisory", dedup_key=f"e2e.cond.{time.time()}",
                         conditions=[{"key": "battery", "op": "lt", "value": 20}]))
    out = await bus.wait_out(1, timeout=4.0)
    mine = [m for m in out if m.get("type") == "probe_cond"]
    record("情境断言：投递时刻前提不成立 → 不说出口", not mine,
           f"收到 {len(mine)} 条（应为 0）")
    dropped = [d for d in bus.decisions
               if d.get("reason") == "conditions_unmet"]
    record("情境断言：裁决事件记 conditions_unmet", bool(dropped), str(dropped[:1]))


async def case_user_contract_exempt(bus):
    bus.clear()
    debug_vehicle("speed_kmh", 120)              # 高驾驶负荷
    await asyncio.sleep(2.0)
    bus.clear()
    await bus.send(probe("probe_advisory", speech="高负荷下的建议。",
                         priority="advisory", dedup_key=f"e2e.adv.{time.time()}",
                         ttl_ms=120000))
    await bus.send(probe("reminder_fired", agent_id="reminder",
                         speech="叮，到点了：探针提醒。",
                         priority="user_contract", dedup_key=f"e2e.rem.{time.time()}"))
    out = await bus.wait_out(1, timeout=6.0)
    got_contract = [m for m in out if m.get("type") == "reminder_fired"]
    got_advisory = [m for m in out if m.get("type") == "probe_advisory"]
    record("user_contract：高负荷下仍照响（到点必响契约）", bool(got_contract))
    record("advisory：高负荷下被延后不投递", not got_advisory,
           f"收到 {len(got_advisory)} 条（应为 0）")
    deferred = [d for d in bus.decisions if d.get("decision") == "deferred"]
    record("advisory：裁决事件记 deferred/driving_load",
           any(d.get("reason") == "driving_load" for d in deferred), str(deferred[:1]))
    debug_vehicle("speed_kmh", 0)                # 复原


async def case_rate_limit(bus):
    """全局频控：按**投递消息数**计（合并因此天然省额度）；user_contract 豁免。"""
    bus.clear()
    cap = 6
    for i in range(cap + 2):
        await bus.send(probe(f"probe_rate_{i}", speech=f"第{i}条。",
                             priority="advisory", dedup_key=f"e2e.rate.{i}.{time.time()}"))
        await asyncio.sleep(0.05)
    await asyncio.sleep(3.0)
    limited = [d for d in bus.decisions if d.get("reason") == "rate_limited"]
    record("频控：超出每小时上限的建议被丢弃", bool(limited), f"{len(limited)} 条被限流")
    bus.clear()
    await bus.send(probe("reminder_fired", agent_id="reminder", speech="叮，到点了。",
                         priority="user_contract",
                         dedup_key=f"e2e.rate.rem.{time.time()}"))
    out = await bus.wait_out(1, timeout=5.0)
    record("频控：user_contract 不受限（到点必响）",
           any(m.get("type") == "reminder_fired" for m in out),
           f"收到 {len(out)} 条")


async def main():
    try:
        import nats
    except ImportError:
        print("请先：pip install nats-py")
        return 1
    reset_governor()                      # 净初态（见 reset_governor 注释）
    reset_low_battery_producer()          # 清低电量生产方的 30 分钟节流与边沿状态
    nc = await nats.connect(NATS_URL, connect_timeout=5)
    bus = Bus(nc)
    await bus.start()

    # 前置：治理器必须在（不在则本脚本没有验证对象——这不是 fail-open 的验收点）
    alive = await bus.send(probe("probe_ping", speech="ping。",
                                 priority="advisory",
                                 dedup_key=f"e2e.ping.{time.time()}"))
    record("前置：主动治理器在线并 ack", alive)
    if not alive:
        print("\n治理器未运行 —— 先 `docker compose up -d proactive`")
        await nc.close()
        return 1
    await asyncio.sleep(2.0)

    print("\n── 1. 单条直通 ──")
    await case_single_passthrough(bus)
    print("\n── 2. DoD：低电量 + 同窗建议 → 一条 ──")
    await case_dod_merge(bus)
    print("\n── 2b. 卡片合并成 card_group ──")
    await case_card_group(bus)
    print("\n── 3. 同类去重（跨生产方）──")
    await case_dedup(bus)
    print("\n── 4. 情境断言投递期复核 ──")
    await case_conditions_recheck(bus)
    print("\n── 5. user_contract 豁免 vs advisory 延后 ──")
    await case_user_contract_exempt(bus)

    await nc.close()
    ok = sum(_results)
    print(f"\n===== e2e_proactive: {ok}/{len(_results)} =====")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
