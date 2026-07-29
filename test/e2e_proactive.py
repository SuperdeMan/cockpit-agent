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
import sys
import time
import urllib.request
from pathlib import Path

from support.e2e import CaseRecorder, assert_persistent_source_contract


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

COLLECTOR = "http://localhost:8092"
NATS_URL = "nats://localhost:4222"
REQUEST_SUBJECT = "agent.proactive.request"
OUTPUT_SUBJECT = "agent.proactive"
DECISION_SUBJECT = "obs.proactive.decision"
ADMIN_COUNT_SUBJECT = "e2e.proactive.namespace.count"
ADMIN_PURGE_SUBJECT = "e2e.proactive.namespace.purge"
ADMIN_MAX_RESPONSE_BYTES = 16 * 1024
_recorder: CaseRecorder | None = None
_case_index = 0
_probe_index = 0


def record(name, ok, detail: str = ""):
    global _case_index
    if _recorder is None:
        raise RuntimeError("CaseRecorder is not initialized")
    _case_index += 1
    case_id = f"proactive-{_case_index:02d}"
    if ok:
        _recorder.pass_case(case_id)
    else:
        _recorder.fail_case(case_id, "assertion_failed", detail or name)
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


def debug_vehicle(key: str, value) -> None:
    """压车辆环境：collector → NATS → 端侧 VAL 白名单键 → vehicle.state.changed 广播。"""
    req = urllib.request.Request(
        f"{COLLECTOR}/api/debug/vehicle",
        data=json.dumps({"key": key, "value": value}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def vehicle_state() -> dict:
    with urllib.request.urlopen(f"{COLLECTOR}/api/vehicle/state", timeout=10) as r:
        return json.loads(r.read().decode())


def restore_vehicle(original: dict) -> None:
    for key in ("battery", "speed_kmh"):
        if key in original:
            debug_vehicle(key, original[key])


class Bus:
    """收 agent.proactive 与裁决事件的探针。"""

    def __init__(self, nc, owner: str, agent_prefix: str):
        self.nc = nc
        self.owner = owner
        self.agent_prefix = agent_prefix
        self.out: list[dict] = []
        self.decisions: list[dict] = []

    async def start(self):
        async def on_out(m):
            try:
                payload = json.loads(m.data.decode())
                if payload.get("user_id") == self.owner:
                    self.out.append(payload)
            except Exception:
                pass

        async def on_dec(m):
            try:
                payload = json.loads(m.data.decode())
                if str(payload.get("agent_id") or "").startswith(self.agent_prefix):
                    self.decisions.append(payload)
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
    global _probe_index
    if _recorder is None:
        raise RuntimeError("CaseRecorder is not initialized")
    _probe_index += 1
    p = {
        "type": kind,
        "agent_id": kw.pop("agent_id", _recorder.user_id("probe")),
        "user_id": _recorder.user_id(),
        "speech": kw.pop("speech", "探针一句话。"),
        "ts": int(time.time() * 1000),
        "dedup_key": kw.pop(
            "dedup_key",
            f"{_recorder.run_id()}.probe.{_probe_index}",
        ),
    }
    p.update(kw)
    return p


async def admin_request(
    nc,
    subject: str,
    *,
    identity_token: str,
    user_id: str,
) -> dict:
    request = json.dumps({
        "identity_token": identity_token,
        "user_id": user_id,
    }, ensure_ascii=True, separators=(",", ":")).encode()
    message = await nc.request(subject, request, timeout=3.0)
    if len(message.data) > ADMIN_MAX_RESPONSE_BYTES:
        raise RuntimeError("proactive admin response is too large")
    response = json.loads(message.data.decode("utf-8"))
    if (
        not isinstance(response, dict)
        or set(response) != {
            "ok",
            "before",
            "deleted",
            "after",
            "rate_delivered",
            "rate_max_per_hour",
            "error",
        }
        or type(response.get("ok")) is not bool
        or any(
            type(response.get(key)) is not int
            for key in (
                "before",
                "deleted",
                "after",
                "rate_delivered",
                "rate_max_per_hour",
            )
        )
        or not isinstance(response.get("error"), str)
    ):
        raise RuntimeError("proactive admin response is invalid")
    if not response["ok"]:
        raise RuntimeError(f"proactive admin rejected request: {response['error']}")
    return response


def cleanup_namespace(identity_token: str, user_id: str) -> None:
    async def cleanup() -> None:
        import nats
        nc = await nats.connect(NATS_URL, connect_timeout=5)
        try:
            purged = await admin_request(
                nc,
                ADMIN_PURGE_SUBJECT,
                identity_token=identity_token,
                user_id=user_id,
            )
            counted = await admin_request(
                nc,
                ADMIN_COUNT_SUBJECT,
                identity_token=identity_token,
                user_id=user_id,
            )
            if purged["after"] != 0 or counted["after"] != 0:
                raise RuntimeError("proactive owner queue cleanup is not empty")
        finally:
            await nc.close()
    asyncio.run(cleanup())


async def case_single_passthrough(bus):
    bus.clear()
    payload = probe("probe_single", speech="单条直通探针。",
                    card={"type": "probe_card", "n": 1},
                    priority="user_contract",
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
           and m.get("user_id") == _recorder.user_id())


async def case_dod_merge(bus):
    """DoD：同 owner 的两条用户约定消息在同窗合成**一条**。

    原脚本靠重启 charging-planner 清进程内 30 分钟节流，这会破坏共享栈。
    这里直接对治理器真实 NATS 入口发两条 exact-owner 信封，验证目标仍是治理器
    的合并与下游投递；低电量生产方自身由 charging-planner 契约测试覆盖。
    """
    bus.clear()
    await bus.send(probe("probe_merge_energy",
                         agent_id=_recorder.user_id("merge-energy"),
                         speech="电量只剩18%了。",
                         priority="user_contract"))
    await bus.send(probe("probe_merge_scene",
                         agent_id=_recorder.user_id("merge-scene"),
                         speech="要开启省电出行模式吗？",
                         card={"type": "scene_card", "name": "省电出行"},
                         priority="user_contract"))
    out = await bus.wait_out(1, timeout=12.0)
    merged = [m for m in out if m.get("merged_from")]
    record("DoD：同窗两条 → HMI 只响一条（不是两个 Agent 各响一次）",
           len(out) == 1 and bool(merged),
           f"实际 {len(out)} 条：{[m.get('type') for m in out]}")
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
    await bus.send(probe("probe_card_a", agent_id=_recorder.user_id("card-a"), speech="卡片甲。",
                         card={"type": "probe_card", "n": 1},
                         priority="user_contract"))
    await bus.send(probe("probe_card_b", agent_id=_recorder.user_id("card-b"), speech="卡片乙。",
                         card={"type": "probe_card", "n": 2},
                         priority="user_contract"))
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
    key = f"{_recorder.run_id()}.dedup"
    await bus.send(probe("probe_dedup", agent_id=_recorder.user_id("dedup-a"), speech="第一条。",
                         priority="user_contract", dedup_key=key))
    await bus.send(probe("probe_dedup2", agent_id=_recorder.user_id("dedup-b"), speech="第二条。",
                         priority="user_contract", dedup_key=key))
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
                         priority="advisory",
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
                         priority="advisory",
                         ttl_ms=120000))
    await bus.send(probe("reminder_fired", agent_id=_recorder.user_id("contract"),
                         speech="叮，到点了：探针提醒。",
                         priority="user_contract"))
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
    token = _recorder.identity_token()
    owner = _recorder.user_id()
    await admin_request(
        bus.nc,
        ADMIN_PURGE_SUBJECT,
        identity_token=token,
        user_id=owner,
    )
    status = await admin_request(
        bus.nc,
        ADMIN_COUNT_SUBJECT,
        identity_token=token,
        user_id=owner,
    )
    cap = status["rate_max_per_hour"]
    clean = (
        status["after"] == 0
        and status["rate_delivered"] == 0
        and cap > 0
    )
    record(
        "频控：全局匿名窗口为空（有他人贡献即 fail closed）",
        clean,
        (
            f"count={status['after']} "
            f"delivered={status['rate_delivered']} cap={cap}"
        ),
    )
    if not clean:
        return

    delivered = 0
    delivered_types: list[str] = []
    for i in range(cap):
        bus.clear()
        kind = f"probe_rate_{i}"
        await bus.send(probe(
            kind,
            speech=f"第{i}条。",
            priority="advisory",
        ))
        out = await bus.wait_out(1, timeout=6.0)
        if len(out) == 1 and out[0].get("type") == kind:
            delivered += 1
            delivered_types.append(kind)
    status = await admin_request(
        bus.nc,
        ADMIN_COUNT_SUBJECT,
        identity_token=token,
        user_id=owner,
    )
    record(
        "频控：额度内每条均独立 flush 并投递",
        delivered == cap and status["rate_delivered"] == cap,
        (
            f"delivered={delivered}/{cap} "
            f"admin={status['rate_delivered']} "
            f"types={delivered_types}"
        ),
    )

    bus.clear()
    await bus.send(probe(
        "probe_rate_over_cap",
        speech="额度外建议。",
        priority="advisory",
    ))
    await asyncio.sleep(0.5)
    limited = [d for d in bus.decisions if d.get("reason") == "rate_limited"]
    record(
        "频控：下一条建议被 rate_limited",
        bool(limited) and not bus.out,
        f"limited={len(limited)} out={len(bus.out)}",
    )
    bus.clear()
    await bus.send(probe("reminder_fired", agent_id=_recorder.user_id("rate-contract"),
                         priority="user_contract"))
    out = await bus.wait_out(1, timeout=5.0)
    record("频控：user_contract 不受限（到点必响）",
           any(m.get("type") == "reminder_fired" for m in out),
           f"收到 {len(out)} 条")


async def run(recorder: CaseRecorder) -> None:
    try:
        import nats
    except ImportError:
        recorder.fail_case(
            "nats_dependency",
            "dependency_unavailable",
            "nats-py is unavailable",
        )
        return
    user = recorder.user_id()
    token = recorder.identity_token()
    original = vehicle_state()
    recorder.register_cleanup(user, lambda: restore_vehicle(original))
    recorder.register_cleanup(user, lambda: cleanup_namespace(token, user))
    nc = await nats.connect(NATS_URL, connect_timeout=5)
    bus = Bus(nc, user, user)
    await bus.start()
    try:
        before = await admin_request(
            nc,
            ADMIN_COUNT_SUBJECT,
            identity_token=token,
            user_id=user,
        )
        starts_empty = (
            before["after"] == 0
            and before["rate_delivered"] == 0
        )
        record(
            "前置：exact owner 队列为空且全局匿名频控用量为零",
            starts_empty,
            (
                f"count={before['after']} "
                f"delivered={before['rate_delivered']} "
                f"cap={before['rate_max_per_hour']}"
            ),
        )
        if not starts_empty:
            return

        # 前置：用恒不满足条件拿 ack，不消耗投递频控额度。
        alive = await bus.send(probe(
            "probe_ping",
            speech="ping。",
            priority="advisory",
            conditions=[{"key": "battery", "op": "lt", "value": -1}],
        ))
        record("前置：主动治理器在线并 ack", alive)
        if not alive:
            return
        await asyncio.sleep(0.5)

        print("\n── 1. 单条直通 ──")
        await case_single_passthrough(bus)
        print("\n── 2. DoD：同 owner 两条 → 一条 ──")
        await case_dod_merge(bus)
        print("\n── 2b. 卡片合并成 card_group ──")
        await case_card_group(bus)
        print("\n── 3. 同类去重（跨生产方）──")
        await case_dedup(bus)
        print("\n── 4. 情境断言投递期复核 ──")
        await case_conditions_recheck(bus)
        print("\n── 5. user_contract 豁免 vs advisory 延后 ──")
        await case_user_contract_exempt(bus)
        print("\n── 6. 全局频控与 user_contract 豁免 ──")
        await case_rate_limit(bus)
    finally:
        await nc.close()


def main() -> int:
    _source_contract()
    global _recorder
    _recorder = CaseRecorder()
    with _recorder:
        asyncio.run(run(_recorder))
    result = _recorder.result
    print(f"\n===== e2e_proactive: {result.counts['passed']}/{result.counts['selected']} =====")
    return _recorder.exit_code()


if __name__ == "__main__":
    sys.exit(main())
