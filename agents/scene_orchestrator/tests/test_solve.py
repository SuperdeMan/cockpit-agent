"""Ground·Solve 求值器单测（纯函数，env 注入，全离线）。

要害是**三态**：条件不是真/假两态，而是 sat / unsat / unknown。unknown 绝不当成满足——
否则一对互斥分支（夏制冷/冬制热）在缺数据时会同时生效、后条覆盖前条（v2.1 修正②）。
"""
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.scene_orchestrator.src.solve import (SAT, UNKNOWN, UNSAT, check_guards,
                                                 evaluate, solve, unmet)


def _act(cmd, params=None, **kw):
    a = {"type": "vehicle.control", "command": cmd, "params": params or {},
         "require_confirm": False}
    a.update(kw)
    return a


# ── 三态求值 ────────────────────────────────────────────────────────────────

def test_evaluate_three_states():
    assert evaluate({"key": "battery", "op": "gte", "value": 20}, {"battery": 50}) == SAT
    assert evaluate({"key": "battery", "op": "gte", "value": 20}, {"battery": 10}) == UNSAT
    assert evaluate({"key": "battery", "op": "gte", "value": 20}, {}) == UNKNOWN
    assert evaluate({"key": "battery", "op": "gte", "value": 20},
                    {"battery": None}) == UNKNOWN


def test_evaluate_cross_type_equality():
    """状态镜像给的是数字 22 / 布尔 True，DSL 里可能是字符串——不能因类型不同就判不等。"""
    assert evaluate({"key": "hvac_temp", "op": "eq", "value": "22"},
                    {"hvac_temp": 22}) == SAT
    assert evaluate({"key": "ambient_light", "op": "eq", "value": True},
                    {"ambient_light": True}) == SAT
    assert evaluate({"key": "gear", "op": "eq", "value": "P"}, {"gear": "P"}) == SAT
    assert evaluate({"key": "gear", "op": "in", "value": ["P", "N"]},
                    {"gear": "N"}) == SAT


def test_evaluate_non_numeric_compare_is_unknown():
    """「P 挡 > 20」没有意义——不能瞎判成 True/False。"""
    assert evaluate({"key": "gear", "op": "gt", "value": 20}, {"gear": "P"}) == UNKNOWN


# ── guards（unknown → 降级 confirm，不 block）───────────────────────────────

def test_guard_block_only_on_hard_evidence():
    guards = [{"key": "battery", "op": "gte", "value": 20, "mode": "block",
               "message": "电量太低"}]
    blocked, notes = check_guards(guards, {"battery": 10}, "露营模式")
    assert blocked and "电量太低" in blocked                 # 确凿不满足 → 拒绝

    blocked, notes = check_guards(guards, {}, "露营模式")
    assert not blocked and notes                            # 读不到 → 降级 confirm，不拦
    assert "读不到" in notes[0]


def test_guard_confirm_mode():
    guards = [{"key": "battery", "op": "gte", "value": 20, "mode": "confirm",
               "message": "电量偏低"}]
    blocked, notes = check_guards(guards, {"battery": 10})
    assert not blocked and "电量偏低" in notes[0]


# ── when 裁剪（unknown = 跳过 + 告知）───────────────────────────────────────

def test_when_prunes_unsatisfied_action():
    actions = [_act("ambient_light.set", {"brightness": "10"}),
               _act("seat.recline", {"angle": "160"},
                    when={"key": "gear", "op": "eq", "value": "P"})]
    sol = solve(actions, [], {"gear": "D"})
    assert [a["command"] for a in sol.actions] == ["ambient_light.set"]
    assert sol.notes and "跳过" in sol.notes[0]


def test_mutually_exclusive_branches_do_not_double_fire_on_missing_data():
    """v2.1 修正②：**这是本模块存在的理由**。

    夏冷/冬热两条互斥 when。cabin_temp 读不到时，若把 unknown 当成"满足"，两条会同时生效、
    后条覆盖前条（用户在 30℃ 车里被吹了 26℃ 的热风）——实打实的 bug。三态求值下两条都跳过，
    并诚实告知"车内温度读不到"。
    """
    actions = [_act("hvac.set", {"temperature": "22"},
                    when={"key": "cabin_temp", "op": "gte", "value": 28}),
               _act("hvac.set", {"temperature": "26"},
                    when={"key": "cabin_temp", "op": "lt", "value": 15})]

    hot = solve(actions, [], {"cabin_temp": 35})
    assert [a["params"]["temperature"] for a in hot.actions] == ["22"]      # 只走制冷

    cold = solve(actions, [], {"cabin_temp": 5})
    assert [a["params"]["temperature"] for a in cold.actions] == ["26"]     # 只走制热

    blind = solve(actions, [], {})                                          # 读不到
    assert blind.actions == [], "缺数据时互斥分支绝不能双发"
    assert any("cabin_temp" in n for n in blind.notes), "消失了要透明告知"


# ── 幂等跳过（assert 已达成）───────────────────────────────────────────────

def test_idempotent_skip_when_already_satisfied():
    """重复激活 / 触发与手动撞车 / 「再试一次」——天然只补缺失项。"""
    actions = [_act("hvac.set", {"temperature": "22"}),
               _act("ambient_light.set", {"brightness": "10"})]
    sol = solve(actions, [], {"hvac_temp": 22, "ambient_light_brightness": 60})
    assert [a["command"] for a in sol.actions] == ["ambient_light.set"]
    assert sol.skipped_done == 1


def test_unknown_assert_key_does_not_skip():
    """期望态读不到 → **不跳过**（照常执行）。跳过意味着"已经是这样了"，猜不得。"""
    sol = solve([_act("hvac.set", {"temperature": "22"})], [], {})
    assert len(sol.actions) == 1 and sol.skipped_done == 0


def test_all_actions_already_satisfied_is_honest():
    sol = solve([_act("hvac.set", {"temperature": "22"})], [], {"hvac_temp": 22})
    assert sol.actions == [] and sol.skipped_done == 1


def test_explicit_assert_wins_over_derived():
    a = _act("fragrance.on", assert_={"key": "fragrance", "op": "eq", "value": True})
    a["assert"] = a.pop("assert_")
    sol = solve([a], [], {"fragrance": True})
    assert sol.actions == [] and sol.skipped_done == 1


def test_order_is_not_rearranged():
    """执行序由编译期定死（人类直觉序），运行期**只裁不排**。"""
    actions = [_act("ambient_light.set", {"brightness": "10"}),
               _act("volume.set", {"level": "0"}),
               _act("hvac.set", {"temperature": "22"})]
    sol = solve(actions, [], {})
    assert [a["command"] for a in sol.actions] == [
        "ambient_light.set", "volume.set", "hvac.set"]


def test_blocked_guard_short_circuits_actions():
    sol = solve([_act("hvac.set", {"temperature": "22"})],
                [{"key": "battery", "op": "gte", "value": 20, "mode": "block",
                  "message": "电量太低"}],
                {"battery": 5}, label="露营模式")
    assert sol.blocked and not sol.actions


# ── Verify 对账（unmet：只算确凿未达成）─────────────────────────────────────

def test_unmet_only_reports_hard_failures():
    actions = [_act("seat.recline", {"angle": "160"}),
               _act("hvac.set", {"temperature": "22"})]
    # 座椅确凿没动（90≠160），空调达成 → 只报座椅
    bad = unmet(actions, {"seat_recline": 90, "hvac_temp": 22})
    assert [a["command"] for a in bad] == ["seat.recline"]


def test_unmet_is_fail_open_on_missing_keys():
    """读不到 = 无法验证，**不是失败**。fail-open 铁律：绝不假警。"""
    assert unmet([_act("seat.recline", {"angle": "160"})], {}) == []
    assert unmet([_act("seat.recline", {"angle": "160"})], {"hvac_temp": 22}) == []
