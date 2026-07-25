"""偏好加权与衰减单测（M2 记忆图谱 P0）。

对症的真缺陷：现状 confidence 抽取时定死，重复不加强、久了不衰减——「上个月说过一次
想吃辣」和「每周三次点川菜」在召回里同权。本文件逐条钉住新的强度语义。

**存量兼容是硬要求**：weight<=0 的条目（M2 前写入的全部 + 非 semantic）必须逐字回到
`confidence`，否则会扰动已绿的旅程（B3-3 记忆族）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import weighting as W  # noqa: E402

DAY = 86400


# ── base / reinforce ──────────────────────────────────────────────────────

def test_base_favours_user_stated_over_inferred():
    """用户明说的起点就该高于 Agent 猜的。"""
    assert W.base_strength("user_stated") > W.base_strength("agent_inferred")
    assert W.base_strength("") == W.BASE_DEFAULT      # 未知来源按保守档


def test_first_evidence_gives_no_bonus():
    assert W.reinforcement(1) == 0
    assert W.reinforcement(0) == 0
    assert W.reinforcement(-3) == 0                   # 垃圾值不炸也不加成


def test_reinforcement_accumulates_and_caps():
    assert W.reinforcement(2) == pytest.approx(0.1)
    assert W.reinforcement(5) == pytest.approx(0.4)
    assert W.reinforcement(50) == pytest.approx(0.4)  # 封顶：刷不出无限强度


def test_reinforcement_tolerates_garbage():
    assert W.reinforcement("abc") == 0
    assert W.reinforcement(None) == 0


# ── decay ────────────────────────────────────────────────────────────────

def test_no_decay_when_half_life_zero():
    """显式偏好不衰减——用户明说的凭什么因为久了就不算数。"""
    assert W.decay(365 * DAY, 0) == 1.0
    assert W.decay(365 * DAY, -1) == 1.0


def test_decay_halves_每_half_life():
    assert W.decay(90 * DAY, 90) == pytest.approx(0.5)
    assert W.decay(180 * DAY, 90) == pytest.approx(0.25)


def test_future_timestamp_does_not_amplify():
    """时钟漂移/未来 valid_from 不该把强度放大到 >1。"""
    assert W.decay(-1000 * DAY, 90) == 1.0


def test_default_half_life_by_provenance():
    assert W.default_half_life("user_stated") == 0                    # 不衰减
    assert W.default_half_life("agent_inferred") == W.HALF_LIFE_INFERRED
    # 用户确认过的推断同样升格为不衰减（review_status 是二次确认信号）
    assert W.default_half_life("agent_inferred", "user_confirmed") == 0


# ── compute_weight：子 RFC §3.2 的三条关键语义 ────────────────────────────

def test_said_once_explicitly():
    """「说过一次爱吃辣」= 0.6。"""
    assert W.compute_weight(provenance="user_stated", evidence_count=1) == pytest.approx(0.6)


def test_repeated_inference_overtakes_single_statement():
    """**本卡的核心语义**：「每周三次点川菜」(推断×8) 要超过「说过一次爱吃辣」(显式×1)。"""
    repeated = W.compute_weight(provenance="agent_inferred", evidence_count=8)
    once = W.compute_weight(provenance="user_stated", evidence_count=1)
    assert repeated == pytest.approx(0.7)
    assert repeated > once


def test_stale_single_inference_sinks():
    """半年前的一次性推断自然沉底（0.3 × 0.5^2 = 0.075）。"""
    w = W.compute_weight(provenance="agent_inferred", evidence_count=1,
                         age_seconds=180 * DAY)
    assert w == pytest.approx(0.075, abs=1e-3)


def test_explicit_preference_survives_time():
    """同样半年前，显式偏好不掉——这是刻意的不对称。"""
    w = W.compute_weight(provenance="user_stated", evidence_count=1,
                         age_seconds=180 * DAY)
    assert w == pytest.approx(0.6)


def test_weight_clamped_to_unit_interval():
    w = W.compute_weight(provenance="user_stated", evidence_count=999)
    assert 0.0 <= w <= 1.0


# ── effective_confidence：存量兼容（红线）────────────────────────────────

def test_legacy_item_falls_back_to_confidence():
    """M2 之前写入的条目没有 weight → 打分逐字回到 confidence。"""
    assert W.effective_confidence({"confidence": 0.83}) == pytest.approx(0.83)
    assert W.effective_confidence({"confidence": 0.83, "weight": 0}) == pytest.approx(0.83)


def test_missing_both_is_zero_not_crash():
    assert W.effective_confidence({}) == 0.0
    assert W.effective_confidence({"confidence": None, "weight": None}) == 0.0


def test_weighted_item_uses_weight():
    assert W.effective_confidence({"confidence": 0.4, "weight": 0.7}) == pytest.approx(0.7)


def test_weighted_item_decays_at_read_time():
    """存的是巩固那一刻的 weight，读的时候可能又老了——实时再衰减一次。"""
    now = 1_000_000_000
    item = {"weight": 0.8, "half_life_days": 90,
            "valid_from": now - 90 * DAY}
    assert W.effective_confidence(item, now=now) == pytest.approx(0.4, abs=1e-3)


def test_non_decaying_weighted_item_stable():
    now = 1_000_000_000
    item = {"weight": 0.6, "half_life_days": 0, "valid_from": now - 999 * DAY}
    assert W.effective_confidence(item, now=now) == pytest.approx(0.6)


# ── 证据链合并（子 RFC §5 溯源强制项）──────────────────────────────────

def test_merge_evidence_dedups_and_keeps_order():
    assert W.merge_evidence("t1,t2", "t2,t3") == "t1,t2,t3"


def test_merge_evidence_handles_empties():
    assert W.merge_evidence("", "t1") == "t1"
    assert W.merge_evidence("t1", "") == "t1"
    assert W.merge_evidence("", "") == ""


def test_merge_evidence_caps_and_drops_oldest():
    merged = W.merge_evidence(",".join(f"t{i}" for i in range(40)), "tnew", cap=5)
    parts = merged.split(",")
    assert len(parts) == 5 and parts[-1] == "tnew"     # 保最新，丢最旧


def test_evidence_count_from_ids():
    assert W.evidence_count("t1,t2,t3") == 3
    assert W.evidence_count("") == 1                   # 存量无证据串 → 至少算 1 次
    assert W.evidence_count("", fallback=4) == 4
