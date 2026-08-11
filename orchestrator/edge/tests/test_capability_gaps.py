"""B4 能力完整性门禁首跑抓到的存量缺口——修完之后这些断言从记录变成回归探针。

判据（AGENTS.md §4.3）：**记录一个缺陷不等于修它**。反过来，修好之后那道断言还要留着。

首跑（2026-08-11）抓到 61 条，其中真缺陷 22 条，按维度分四族：

1. **崩溃**：`steering_wheel.height.set` 不带值 → `KeyError`。同款坑 aircon 风速那处早就修过
   （`setdefault`），方向盘高度这个孪生分支漏了；而 `edge_call._missing_required_value`
   因为 `attr` 在场提前返回、压根不会拦它。
2. **自相矛盾的状态**：`_simulate` 兜底一律写 `state[f"{obj}_{operate}"] = True`，于是
   `lane_assistance_open` 与 `lane_assistance_close` 能**同时为 True**。这种键恒为 True、
   永远无法被证否，Outcome Verifier 对账面上是个恒真的空洞。
3. **通用话术**：`window.set` / `sunroof.set` / `sunshade.set` / `power_mode.set` 与两个
   车道辅助开关落 `generic_success`（「好的」）——「开到 50%」和「全开」回执一模一样。
4. **只实现了一半的档位对象**：`energy_recovery` / `wiper.speed` 有 `set` 没有 `inc/dec`，
   `fragrance` 有开关没有档位，`sunshade` 有开关没有开度。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from val import VAL  # noqa: E402


def _run(val: VAL, obj: str, operate: str, **data):
    return val.execute({"domain": "car_control", "intent": f"{obj}.{operate}",
                        "data": {"object": obj, "operate": operate, **data}})


# ── 1. 崩溃 ─────────────────────────────────────────────────────────────────

def test_steering_wheel_height_set_without_value_no_keyerror():
    """`steering_wheel.height.set` 不带值不许抛异常（回退到当前高度）。

    与 `test_val_onchange.py::test_aircon_wind_speed_set_without_value_no_keyerror`
    是**同一个坑的两处**——那处修过，这处没有，直到 B4 门禁逐对象跑 `_simulate` 才暴露。
    """
    val = VAL()
    ok, _ = _run(val, "steering_wheel", "set", attr="height")
    assert ok is True
    assert val.state["steering_wheel_height"] == 0


def test_steering_wheel_height_set_with_value_still_works():
    """对照：带值那条路径没被改坏。"""
    val = VAL()
    _run(val, "steering_wheel", "set", attr="height", value=3)
    assert val.state["steering_wheel_height"] == 3


# ── 2. 自相矛盾的状态 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("obj", ["lane_assistance", "lane_departure_assistance"])
def test_switch_object_uses_one_key_with_two_values(obj):
    """开关型对象：open/close 落**同一个键**，取值相反。

    修前是 `{obj}_open=True` 与 `{obj}_close=True` 同时存在——关掉之后「开着」那个标记
    还在，任何按状态对账的逻辑都会读到矛盾。
    """
    val = VAL()
    _run(val, obj, "open")
    assert val.state[obj] is True
    _run(val, obj, "close")
    assert val.state[obj] is False
    # 旧的两个恒真标记键不许再出现
    assert f"{obj}_open" not in val.state
    assert f"{obj}_close" not in val.state


# ── 3. 通用话术 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("obj,expect", [
    ("window", "车窗"), ("sunroof", "天窗"), ("sunshade", "遮阳帘"),
])
def test_opening_set_speaks_the_degree(obj, expect):
    """带程度的开合动作要回显程度，不能一律「好的」。"""
    val = VAL()
    ok, speech = val.execute(
        {"domain": "car_control", "intent": f"{obj}.set",
         "data": {"object": obj, "operate": "set", "value": 50}},
        answer_length="detailed")
    assert ok is True
    assert expect in speech and "50" in speech


def test_power_mode_set_speaks_the_mode():
    val = VAL()
    ok, speech = val.execute(
        {"domain": "car_control", "intent": "power_mode.set",
         "data": {"object": "power_mode", "operate": "set", "mode": "sport"}},
        answer_length="detailed")
    assert ok is True and "sport" in speech
    assert val.state["power_mode"] == "sport"


@pytest.mark.parametrize("obj,noun,verb", [
    ("lane_assistance", "车道保持辅助", "开"),
    ("lane_assistance", "车道保持辅助", "关"),
    ("lane_departure_assistance", "车道偏离预警", "开"),
    ("lane_departure_assistance", "车道偏离预警", "关"),
])
def test_lane_assist_switches_have_their_own_speech(obj, noun, verb):
    # 断言「说出了对象名与动作方向」而不是逐字相等——`speech_full` 是候选列表，
    # `_pick_response` 单意图下随机选一条，钉死某一条会做出一个随机红的用例。
    operate = "open" if verb == "开" else "close"
    val = VAL()
    ok, speech = val.execute(
        {"domain": "car_control", "intent": f"{obj}.{operate}",
         "data": {"object": obj, "operate": operate}}, answer_length="detailed")
    assert ok is True
    assert noun in speech and verb in speech


# ── 4. 只实现了一半的档位对象 ───────────────────────────────────────────────

def test_energy_recovery_relative_steps():
    val = VAL()
    _run(val, "energy_recovery", "set", value=2)
    assert val.state["energy_recovery"] == 2
    _run(val, "energy_recovery", "inc")
    assert val.state["energy_recovery"] == 3
    _run(val, "energy_recovery", "inc")          # 夹在上限
    assert val.state["energy_recovery"] == 3
    for _ in range(5):
        _run(val, "energy_recovery", "dec")
    assert val.state["energy_recovery"] == 0     # 夹在下限
    assert "energy_recovery_inc" not in val.state


def test_wiper_speed_relative_steps():
    val = VAL()
    _run(val, "wiper", "set", attr="speed", value=2)
    assert val.state["wiper_speed"] == 2
    _run(val, "wiper", "inc", attr="speed")
    assert val.state["wiper_speed"] == 3
    _run(val, "wiper", "dec", attr="speed")
    assert val.state["wiper_speed"] == 2
    assert "wiper_inc" not in val.state


def test_wiper_on_off_still_independent_of_speed():
    """对照：改了速度分支别把开关分支弄坏（两者是不同的状态键）。"""
    val = VAL()
    _run(val, "wiper", "open")
    assert val.state["wiper"] is True
    _run(val, "wiper", "set", attr="speed", value=4)
    assert val.state["wiper"] is True and val.state["wiper_speed"] == 4


def test_fragrance_level_implies_on():
    val = VAL()
    _run(val, "fragrance", "set", value=2)
    assert val.state["fragrance"] is True
    assert val.state["fragrance_level"] == 2


def test_sunshade_open_degree():
    val = VAL()
    _run(val, "sunshade", "set", value=40)
    assert val.state["sunshade"] == "40%"
    _run(val, "sunshade", "inc", value=60)
    assert val.state["sunshade"] == "open"
    _run(val, "sunshade", "dec", value=100)
    assert val.state["sunshade"] == "closed"


def test_sunshade_open_close_unchanged():
    """对照：加了开度分支，原来的全开/全关行为逐字不变。"""
    val = VAL()
    _run(val, "sunshade", "open")
    assert val.state["sunshade"] == "open"
    _run(val, "sunshade", "close")
    assert val.state["sunshade"] == "closed"
