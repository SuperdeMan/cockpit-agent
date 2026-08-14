"""主动治理器契约测试（M3 P0）。

覆盖：六道闸各自的判据、单条字节级兼容、合并、延后队列、fail-open 语义，
以及**零领域字面量源码断言**（防治理器长成第二个 fast_intent.py）。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from scripts.e2e_contract import assert_architecture_guard
from proactive.evaluate import SAT, UNKNOWN, UNSAT, evaluate_all
from proactive.governor import (ACCEPTED, DEFERRED, DELIVERED, DROPPED, MERGED,
                                Governor, in_quiet_hours, merge_cards, merge_speech,
                                parse_quiet_hours)


class Sink:
    """收 publish 与裁决事件的假下游。"""

    def __init__(self):
        self.out: list[dict] = []
        self.decisions: list[dict] = []

    async def publish(self, payload):
        self.out.append(payload)

    async def emit(self, event):
        self.decisions.append(event)


def make_gov(sink, *, state=None, clock=None, **kw):
    now_fn = clock or (lambda: 1000.0)
    return Governor(sink.publish, state_fn=lambda: dict(state or {}),
                    emit=sink.emit, now_fn=now_fn,
                    merge_window_ms=kw.pop("merge_window_ms", 0), **kw)


def msg(**kw):
    base = {"type": "t1", "speech": "话术一。", "agent_id": "a1", "ts": 1}
    base.update(kw)
    return base


def decisions_of(sink, decision):
    return [d for d in sink.decisions if d["decision"] == decision]


# ── E2E exact-owner namespace admin（纯通用队列操作）──────────────────────

@pytest.mark.asyncio
async def test_namespace_admin_counts_and_purges_only_exact_owner():
    state = {"speed_kmh": 120}
    sink = Sink()
    gov = Governor(
        sink.publish,
        state_fn=lambda: dict(state),
        emit=sink.emit,
        now_fn=lambda: 1000.0,
        merge_window_ms=60_000,
        high_load_speed=80,
    )
    await gov.submit(msg(type="a-pending", user_id="owner-a",
                         priority="user_contract", dedup_key="a-pending"))
    await gov.submit(msg(type="a-deferred", user_id="owner-a",
                         priority="advisory", ttl_ms=300_000,
                         dedup_key="a-deferred"))
    await gov.submit(msg(type="b-pending", user_id="owner-b",
                         priority="user_contract", dedup_key="b-pending"))
    await gov.submit(msg(type="b-deferred", user_id="owner-b",
                         priority="advisory", ttl_ms=300_000,
                         dedup_key="b-deferred"))

    assert gov.count_owner("owner-a") == 2
    assert gov.count_owner("owner-b") == 2
    assert gov.purge_owner("owner-a") == {
        "before": 2,
        "deleted": 2,
        "after": 0,
    }
    assert gov.count_owner("owner-a") == 0
    assert gov.count_owner("owner-b") == 2
    await gov.stop()


def test_namespace_admin_rejects_empty_owner():
    sink = Sink()
    gov = make_gov(sink)
    with pytest.raises(ValueError):
        gov.count_owner("")
    with pytest.raises(ValueError):
        gov.purge_owner("")


@pytest.mark.asyncio
async def test_namespace_admin_purges_owner_dedup_and_rate_residue():
    sink = Sink()
    gov = Governor(
        sink.publish,
        state_fn=dict,
        emit=sink.emit,
        now_fn=lambda: 1000.0,
        merge_window_ms=0,
        max_per_hour=2,
    )
    await gov.submit(msg(
        type="a-delivered",
        user_id="owner-a",
        priority="user_contract",
        dedup_key="a-delivered",
    ))
    await gov.submit(msg(
        type="b-delivered",
        user_id="owner-b",
        priority="user_contract",
        dedup_key="b-delivered",
    ))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert gov.count_owner("owner-a") == 1
    assert gov.count_owner("owner-b") == 1

    assert gov.purge_owner("owner-a") == {
        "before": 1,
        "deleted": 1,
        "after": 0,
    }
    assert gov.rate_status()["rate_delivered"] == 1
    assert gov.count_owner("owner-b") == 1
    assert await gov.submit(msg(
        type="b-next",
        user_id="owner-b",
        priority="advisory",
        dedup_key="b-next",
    )) == ACCEPTED
    await gov.stop()


# ── 闸1 情境断言（三态）────────────────────────────────────────────────────

def test_evaluate_all_three_states():
    assert evaluate_all([], {}) == SAT
    assert evaluate_all([{"key": "battery", "op": "lt", "value": 20}], {"battery": 18}) == SAT
    assert evaluate_all([{"key": "battery", "op": "lt", "value": 20}], {"battery": 55}) == UNSAT
    # 读不到 → UNKNOWN，绝不当成满足
    assert evaluate_all([{"key": "battery", "op": "lt", "value": 20}], {}) == UNKNOWN
    # 有 UNSAT 时 UNSAT 优先（合取里一条否定就够）
    assert evaluate_all([{"key": "a", "op": "eq", "value": 1},
                         {"key": "b", "op": "eq", "value": 2}], {"a": 9}) == UNSAT


@pytest.mark.asyncio
async def test_conditions_unmet_dropped_and_unknown_also_dropped():
    sink = Sink()
    gov = make_gov(sink, state={"battery": 55})
    assert await gov.submit(msg(conditions=[{"key": "battery", "op": "lt", "value": 20}])) == DROPPED

    sink2 = Sink()
    gov2 = make_gov(sink2, state={})          # 车况读不到
    assert await gov2.submit(
        msg(conditions=[{"key": "battery", "op": "lt", "value": 20}])) == DROPPED
    assert decisions_of(sink2, DROPPED)[0]["reason"] == "conditions_unmet"
    assert sink.out == [] and sink2.out == []


@pytest.mark.asyncio
async def test_first_pass_condition_with_ttl_waits_for_mirror_convergence():
    state = {"battery": 80}
    sink = Sink()
    gov = make_gov(sink, state=state)
    payload = msg(
        conditions=[{"key": "battery", "op": "lt", "value": 20}],
        ttl_ms=300_000,
    )

    assert await gov.submit(payload) == DEFERRED
    state["battery"] = 15
    await gov.tick()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert sink.out and sink.out[0]["speech"] == "话术一。"
    assert decisions_of(sink, DEFERRED)[0]["reason"] == "conditions_pending"


# ── 单条路径：字节级兼容 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_item_forwarded_verbatim_minus_governance_keys():
    sink = Sink()
    gov = make_gov(sink, state={})
    payload = msg(card={"type": "x"}, user_id="u1", priority="advisory",
                  conditions=[], dedup_key="k", ttl_ms=5000)
    assert await gov.submit(payload) == ACCEPTED
    await asyncio.sleep(0)                     # 让 window=0 的 flush 任务跑完
    await asyncio.sleep(0)
    assert len(sink.out) == 1
    out = sink.out[0]
    # 治理键被剥掉，其余逐字保留
    assert out == {"type": "t1", "speech": "话术一。", "agent_id": "a1", "ts": 1,
                   "card": {"type": "x"}, "user_id": "u1"}
    assert decisions_of(sink, DELIVERED)


# ── 闸2 同类去重（跨生产方）────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dedup_is_cross_producer():
    sink = Sink()
    gov = make_gov(sink, state={}, dedup_window_s=600)
    assert await gov.submit(msg(dedup_key="charging.low-battery")) == ACCEPTED
    # 另一个 agent、另一个 type，但同一件事 → 窗口内不重复说
    assert await gov.submit(
        msg(agent_id="a2", type="t2", dedup_key="charging.low-battery")) == DROPPED
    assert decisions_of(sink, DROPPED)[0]["reason"] == "dedup"


@pytest.mark.asyncio
async def test_default_dedup_key_is_agent_and_type():
    sink = Sink()
    gov = make_gov(sink, state={})
    assert await gov.submit(msg()) == ACCEPTED
    assert await gov.submit(msg()) == DROPPED                  # 同 agent 同 type
    assert await gov.submit(msg(type="t2")) == ACCEPTED         # 换 type 就不是同一件事


# ── 闸3 免打扰 ────────────────────────────────────────────────────────────

def test_parse_quiet_hours():
    assert parse_quiet_hours("23:00-07:00") == (1380, 420)
    assert parse_quiet_hours("") is None                        # 默认空 = 该闸不启用
    assert parse_quiet_hours("garbage") is None
    assert parse_quiet_hours("25:00-07:00") is None


def test_in_quiet_hours_wraps_midnight():
    w = parse_quiet_hours("23:00-07:00")
    assert in_quiet_hours(w, 23 * 60 + 30) and in_quiet_hours(w, 3 * 60)
    assert not in_quiet_hours(w, 12 * 60)
    assert not in_quiet_hours(None, 3 * 60)                     # 未启用 → 永不抑制


@pytest.mark.asyncio
async def test_quiet_hours_suppresses_advisory_but_not_user_contract():
    night = time.struct_time((2026, 7, 25, 2, 0, 0, 4, 206, 0))
    sink = Sink()
    gov = Governor(sink.publish, state_fn=dict, emit=sink.emit, now_fn=lambda: 1000.0,
                   localtime_fn=lambda _t: night, merge_window_ms=0,
                   quiet_hours="23:00-07:00")
    assert await gov.submit(msg(priority="advisory")) == DROPPED     # ttl=0 → 攒不了就丢
    assert decisions_of(sink, DROPPED)[0]["reason"] == "quiet_hours"
    assert await gov.submit(msg(type="t2", priority="user_contract")) == ACCEPTED
    assert await gov.submit(msg(type="t3", priority="critical")) == ACCEPTED


# ── 闸4 驾驶负荷 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_high_speed_defers_advisory_with_ttl():
    sink = Sink()
    gov = make_gov(sink, state={"speed_kmh": 110}, high_load_speed=80)
    assert await gov.submit(msg(ttl_ms=300000)) == DEFERRED
    assert decisions_of(sink, DEFERRED)[0]["reason"] == "driving_load"
    assert sink.out == []


@pytest.mark.asyncio
async def test_unknown_speed_passes_not_suppressed():
    """镜像冷启动读不到车速时**放行**——「读不到车速」不是「用户在忙」的证据。"""
    sink = Sink()
    gov = make_gov(sink, state={}, high_load_speed=80)
    assert await gov.submit(msg(ttl_ms=300000)) == ACCEPTED


@pytest.mark.asyncio
async def test_critical_and_user_contract_ignore_driving_load():
    sink = Sink()
    gov = make_gov(sink, state={"speed_kmh": 120}, high_load_speed=80)
    assert await gov.submit(msg(priority="critical")) == ACCEPTED
    assert await gov.submit(msg(type="t2", priority="user_contract")) == ACCEPTED


# ── 闸5 频控 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_counts_delivered_messages_and_exempts_contract():
    sink = Sink()
    gov = make_gov(sink, state={}, max_per_hour=2)
    for i in range(2):
        assert await gov.submit(msg(type=f"k{i}")) == ACCEPTED
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert len(sink.out) == 2
    assert await gov.submit(msg(type="k9")) == DROPPED
    assert decisions_of(sink, DROPPED)[0]["reason"] == "rate_limited"
    # 用户显式约定的不受频控（到点必响）
    assert await gov.submit(msg(type="k10", priority="user_contract")) == ACCEPTED


@pytest.mark.asyncio
async def test_rate_limit_and_report_are_global_while_purge_is_exact_owner():
    sink = Sink()
    gov = make_gov(sink, state={}, max_per_hour=2)
    for i in range(2):
        assert await gov.submit(
            msg(type=f"owner-a-{i}", user_id="owner-a"),
        ) == ACCEPTED
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert gov.rate_status() == {
        "rate_delivered": 2,
        "rate_max_per_hour": 2,
    }
    assert await gov.submit(
        msg(type="owner-b-0", user_id="owner-b"),
    ) == DROPPED
    assert await gov.submit(msg(
        type="owner-a-contract",
        user_id="owner-a",
        priority="user_contract",
    )) == ACCEPTED
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert gov.rate_status()["rate_delivered"] == 3

    gov.purge_owner("owner-a")
    assert gov.rate_status()["rate_delivered"] == 0
    assert await gov.submit(
        msg(type="owner-b-after-purge", user_id="owner-b"),
    ) == ACCEPTED


# ── 闸6 合并窗口 ──────────────────────────────────────────────────────────

def test_merge_speech_is_deterministic_concat():
    class I:
        def __init__(self, s):
            self.payload = {"speech": s}
    assert merge_speech([I("电量只剩18%了"), I("回家路上有充电桩。")]) == \
        "电量只剩18%了。另外，回家路上有充电桩。"
    assert merge_speech([I("只有一条。")]) == "只有一条。"


def test_merge_cards_flattens_nested_group():
    class I:
        def __init__(self, c):
            self.payload = {"card": c}
    out = merge_cards([I({"type": "a"}),
                       I({"type": "card_group", "items": [{"type": "b"}, {"type": "c"}]})])
    assert out == {"type": "card_group",
                   "items": [{"type": "a"}, {"type": "b"}, {"type": "c"}]}
    assert merge_cards([I({"type": "a"})]) == {"type": "a"}
    assert merge_cards([I(None)]) is None


@pytest.mark.asyncio
async def test_same_window_merges_into_one_message():
    """DoD 的机制核心：同窗到达的多条 → HMI 只响一条。"""
    sink = Sink()
    gov = Governor(sink.publish, state_fn=dict, emit=sink.emit, now_fn=lambda: 1000.0,
                   merge_window_ms=30)
    await gov.submit(msg(agent_id="charging-planner", type="charging_advice",
                         speech="电量只剩18%了，回家路上有充电桩。",
                         card={"type": "charging_route"}))
    await gov.submit(msg(agent_id="scene-orchestrator", type="scene_suggestion",
                         speech="要开启省电出行模式吗？", card={"type": "scene_suggest"}))
    await asyncio.sleep(0.1)
    assert len(sink.out) == 1, "同窗两条必须合并成一条"
    out = sink.out[0]
    assert "电量只剩18%" in out["speech"] and "省电出行模式" in out["speech"]
    assert out["card"]["type"] == "card_group" and len(out["card"]["items"]) == 2
    assert [m["agent_id"] for m in out["merged_from"]] == \
        ["charging-planner", "scene-orchestrator"]
    assert len(decisions_of(sink, MERGED)) == 2


@pytest.mark.asyncio
async def test_same_window_never_merges_across_owners():
    """同窗不同乘员的消息不得合并成一条。

    合并组只带 top 的 user_id——跨 owner 合并会把 B 的提醒记在 A 名下、只对 A 播报
    （2026-08-14 EVA 二轮批 A③：reminder 侧刻意做的 owner 分组不该在治理器这跳被抵消）。
    """
    sink = Sink()
    gov = Governor(sink.publish, state_fn=dict, emit=sink.emit, now_fn=lambda: 1000.0,
                   merge_window_ms=30)
    await gov.submit(msg(type="reminder_fired", user_id="owner-a",
                         priority="user_contract", speech="A 的提醒。", dedup_key="ra"))
    await gov.submit(msg(type="reminder_fired", user_id="owner-b",
                         priority="user_contract", speech="B 的提醒。", dedup_key="rb"))
    await asyncio.sleep(0.1)
    assert len(sink.out) == 2, "跨 owner 必须各自成条"
    assert sorted(o.get("user_id") for o in sink.out) == ["owner-a", "owner-b"]
    assert not any("merged_from" in o for o in sink.out)
    # 同 owner 的仍照常合并（分组不改变既有合并语义）
    await gov.submit(msg(type="t-a1", user_id="owner-a", speech="话一。", dedup_key="a1"))
    await gov.submit(msg(type="t-a2", user_id="owner-a", speech="话二。", dedup_key="a2"))
    await asyncio.sleep(0.1)
    assert len(sink.out) == 3 and "merged_from" in sink.out[2]


@pytest.mark.asyncio
async def test_critical_flushes_immediately_and_takes_pending_along():
    sink = Sink()
    gov = Governor(sink.publish, state_fn=dict, emit=sink.emit, now_fn=lambda: 1000.0,
                   merge_window_ms=5000)
    await gov.submit(msg(type="advice", priority="advisory", speech="建议一句。"))
    await gov.submit(msg(type="hazard", priority="critical", speech="前方大雾，注意减速。"))
    await asyncio.sleep(0.05)
    assert len(sink.out) == 1
    # critical 排最前（rank 序），建议跟在后面——一次打扰说完两件事
    assert sink.out[0]["speech"].startswith("前方大雾")
    assert sink.out[0]["type"] == "hazard"


# ── 延后队列 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deferred_item_delivered_when_load_drops():
    state = {"speed_kmh": 110}
    sink = Sink()
    gov = Governor(sink.publish, state_fn=lambda: dict(state), emit=sink.emit,
                   now_fn=lambda: 1000.0, merge_window_ms=0, high_load_speed=80)
    assert await gov.submit(msg(ttl_ms=300000)) == DEFERRED
    state["speed_kmh"] = 20
    await gov.tick()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(sink.out) == 1


@pytest.mark.asyncio
async def test_deferred_item_dropped_at_ttl_not_stockpiled():
    clock = {"t": 1000.0}
    sink = Sink()
    gov = Governor(sink.publish, state_fn=lambda: {"speed_kmh": 110}, emit=sink.emit,
                   now_fn=lambda: clock["t"], merge_window_ms=0, high_load_speed=80)
    assert await gov.submit(msg(ttl_ms=1000)) == DEFERRED
    clock["t"] = 1002.0
    await gov.tick()
    assert sink.out == []
    assert decisions_of(sink, DROPPED)[0]["reason"] == "ttl_expired"


@pytest.mark.asyncio
async def test_deferred_item_rechecks_conditions_on_replay():
    """攒着说的建议在投递时刻必须重新证实前提——「产出时成立、5 分钟后已不成立」不该说出口。"""
    state = {"speed_kmh": 110, "battery": 18}
    sink = Sink()
    gov = Governor(sink.publish, state_fn=lambda: dict(state), emit=sink.emit,
                   now_fn=lambda: 1000.0, merge_window_ms=0, high_load_speed=80)
    cond = [{"key": "battery", "op": "lt", "value": 20}]
    assert await gov.submit(msg(conditions=cond, ttl_ms=300000)) == DEFERRED
    state.update({"speed_kmh": 10, "battery": 66})     # 已经充上电了
    await gov.tick()
    assert sink.out == []
    assert decisions_of(sink, DROPPED)[-1]["reason"] == "conditions_unmet"


# ── 默认档位与未知档位 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_or_unknown_priority_is_governed_not_exempt():
    sink = Sink()
    gov = make_gov(sink, state={"speed_kmh": 120}, high_load_speed=80)
    assert await gov.submit(msg()) == DROPPED                       # 缺省 = advisory
    assert await gov.submit(msg(type="t2", priority="vip")) == DROPPED   # 不认识 ≠ 豁免


# ── 零领域字面量（防长成第二个 fast_intent.py）──────────────────────────────

def test_governor_source_has_zero_dynamic_domain_literals():
    """生产方调用图新增 type 后，治理器无需同步维护固定黑名单也会自动受检。"""
    assert_architecture_guard(Path(__file__).resolve().parents[2])


def test_evaluator_matches_scene_three_state_semantics():
    """与 scene 的三态求值器行为逐条对齐（刻意的重复实现，靠测试锁语义）。"""
    from agents.scene_orchestrator.src.solve import SAT as S_SAT
    from agents.scene_orchestrator.src.solve import UNKNOWN as S_UNKNOWN
    from agents.scene_orchestrator.src.solve import UNSAT as S_UNSAT
    from agents.scene_orchestrator.src.solve import _cmp

    from proactive.evaluate import compare

    cases = [
        (18, "lt", 20), (55, "lt", 20), (None, "lt", 20),
        ("P", "gt", 20), (True, "eq", "true"), (22, "eq", "22"),
        ("D", "ne", "P"), ("cool", "in", ["cool", "heat"]),
        (72, "gte", 72), (5, "lte", 4),
    ]
    mapping = {S_SAT: SAT, S_UNSAT: UNSAT, S_UNKNOWN: UNKNOWN}
    for actual, op, expect in cases:
        assert compare(actual, op, expect) == mapping[_cmp(actual, op, expect)], \
            f"三态语义与 scene 不一致：{actual!r} {op} {expect!r}"


# ── 可靠投递（M-C）────────────────────────────────────────
async def _settled():
    """让 _accept 派生的 flush 任务跑完（沿用本文件既有惯例）。"""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _durable_gov(sink, **kw):
    from proactive.delivery_store import DeliveryStore
    store = DeliveryStore(dsn="")
    await store.init()
    return make_gov(sink, store=store, **kw), store


def _contract(text="到点了：吃药", user="u1"):
    return {"type": "reminder_fired", "speech": text, "agent_id": "reminder",
            "user_id": user, "priority": "user_contract",
            "dedup_key": f"k|{text}"}


@pytest.mark.asyncio
async def test_contract_message_is_persisted_before_ownership_is_taken():
    """所有权移交的时机就是本期的核心：ack 之后生产方不再重发，
    所以 durable 档必须**先有账**才认领。"""
    sink = Sink()
    gov, store = await _durable_gov(sink)

    item, durable = await gov.take_ownership(_contract())
    assert durable is True and item.delivery_id
    assert [r["delivery_id"] for r in await store.undelivered()] == [item.delivery_id]


@pytest.mark.asyncio
async def test_advisory_is_not_persisted():
    """advisory 本来就可以不说——为它付持久化代价不划算。"""
    sink = Sink()
    gov, store = await _durable_gov(sink)
    _item, durable = await gov.take_ownership(
        {"type": "t", "speech": "路况还行", "agent_id": "a1", "priority": "advisory"})
    assert durable is False
    assert await store.undelivered() == []


@pytest.mark.asyncio
async def test_delivery_id_travels_to_the_consumer_and_ack_settles_the_ledger():
    sink = Sink()
    gov, store = await _durable_gov(sink)
    await gov.submit(_contract())
    await _settled()

    assert sink.out and sink.out[0]["delivery_id"]
    did = sink.out[0]["delivery_id"]
    # **发出去仍然算没送到**：网关 write 成功不是用户看见了
    assert [r["delivery_id"] for r in await store.undelivered()] == [did]

    assert await gov.acknowledge(did) == 1
    assert await store.undelivered() == []


@pytest.mark.asyncio
async def test_publish_failure_keeps_the_message_on_the_ledger():
    """「broadcast n==0 即丢」的另一半：投递失败不销账，等 HMI 上线补投。"""
    sink = Sink()

    async def boom(_payload):
        raise RuntimeError("nats down")

    from proactive.delivery_store import DeliveryStore
    store = DeliveryStore(dsn="")
    await store.init()
    gov = Governor(boom, emit=sink.emit, now_fn=lambda: 1000.0,
                   merge_window_ms=0, store=store)
    await gov.submit(_contract())
    await _settled()

    assert len(await store.undelivered()) == 1
    assert [d["decision"] for d in sink.decisions] == [DROPPED]   # 观测面仍如实记


@pytest.mark.asyncio
async def test_replay_on_reconnect_resends_undelivered_only():
    sink = Sink()
    gov, store = await _durable_gov(sink)
    await gov.submit(_contract("到点了：吃药"))
    await _settled()
    await gov.submit(_contract("到点了：开会"))
    await _settled()
    await gov.acknowledge(sink.out[0]["delivery_id"])      # 第一条用户已看见

    sink.out.clear()
    assert await gov.replay_undelivered() == 1
    assert "开会" in sink.out[0]["speech"] and "吃药" not in sink.out[0]["speech"]


@pytest.mark.asyncio
async def test_restart_recovers_undelivered_messages():
    """只落库不恢复，等于把消息存进了一个没人读的表。"""
    sink1 = Sink()
    gov1, store = await _durable_gov(sink1)
    await gov1.submit(_contract())
    await _settled()
    did = sink1.out[0]["delivery_id"]

    sink2 = Sink()                                    # 新进程，共用同一份账
    gov2 = make_gov(sink2, store=store)
    assert await gov2.restore() == 1
    await gov2._flush()
    assert sink2.out and sink2.out[0]["delivery_id"] == did


@pytest.mark.asyncio
async def test_restart_does_not_replay_expired_content():
    """停机久到消息过期的，恢复时判掉——半小时后突然播「到点了」比不播更糟。"""
    sink = Sink()
    gov, store = await _durable_gov(sink)
    await gov.take_ownership(dict(_contract(), ttl_ms=1))
    for row in store._mem.values():
        row["expires_at"] = row["created_at"] - 1

    sink2 = Sink()
    gov2 = make_gov(sink2, store=store)
    assert await gov2.restore() == 0
    assert sink2.out == []


@pytest.mark.asyncio
async def test_merged_group_carries_every_credential_and_one_ack_settles_all():
    """合并组把整组凭据一起带走，HMI 原样回传，一次 ACK 销掉整组——
    让凭据随消息走而不是留在治理器内存里，重启后 ACK 仍然对得上账。"""
    sink = Sink()
    gov, store = await _durable_gov(sink, merge_window_ms=1)
    await gov.submit(_contract("到点了：吃药"))
    await _settled()
    await gov.submit(_contract("到点了：开会"))
    await _settled()
    await asyncio.sleep(0.05)

    assert len(sink.out) == 1
    dids = sink.out[0]["delivery_ids"]
    assert len(dids) == 2
    assert await gov.acknowledge(dids) == 2
    assert await store.undelivered() == []


@pytest.mark.asyncio
async def test_dropped_message_leaves_the_ledger_so_reconnect_does_not_replay_it():
    """账本不能只进不出：判丢的消息还留在账上，HMI 一连上就会被重投一堆废内容。"""
    sink = Sink()
    gov, store = await _durable_gov(sink)
    await gov.submit(dict(_contract(),
                          conditions=[{"key": "speed_kmh", "op": "lt", "value": 10}]))
    await _settled()
    # 读不到车速 → UNKNOWN → 无 ttl 的请求 fail-closed 判丢
    assert await store.undelivered() == []


@pytest.mark.asyncio
async def test_governor_without_store_keeps_the_old_in_memory_behaviour():
    """账本可空：不接 store 时逐字回落旧行为，且信封里不多一个键。"""
    sink = Sink()
    gov = make_gov(sink)
    assert await gov.submit(_contract()) in (DELIVERED, ACCEPTED)
    await _settled()
    assert "delivery_id" not in sink.out[0]
    assert await gov.acknowledge("whatever") == 0
