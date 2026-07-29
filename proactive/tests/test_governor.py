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
