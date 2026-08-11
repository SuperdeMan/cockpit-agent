"""B4-3 迁移探针：`VEHICLE_INTENTS` 从手工集合改为知识库派生，**逐字不变**。

方案 §5 的风险处置要求：「派生结果与现手工集合做一次性 diff 断言（迁移测试），
差集必须人工逐条裁定后才切换」。这份就是那次 diff——差集为空，故本次切换零行为变化。

## 这份清单要不要一直留着

留。它现在的作用变了：从「迁移当次的对照」变成**能力面变更的显式签收点**。
端侧能力面是云侧 planner 看得见的全部车控工具，它增减一条是产品决定，不该只体现在
一个 YAML diff 里悄悄过去。改了 `commands.yaml` 的 `edge_intents` 就要同步改这里，
review 时两处一起看——这是**故意的**一处手工同步，不是漏网的那种。

⚠ 与被退役的那个集合的区别：那个集合是**运行时依赖**（漏写=能力不可达且不报错），
这个是**测试基线**（漏改=测试红，红了就看得见）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capability_meta import derive_edge_intents  # noqa: E402
from edge_agents_mod.vehicle import VEHICLE_INTENTS  # noqa: E402

#: 迁移前（2026-08-11，提交 7b68047）`vehicle.py` 里手写的那 76 条，逐字抄录。
_BEFORE_MIGRATION = {
    "accompany_home.close",
    "accompany_home.open",
    "aircon.wind_speed.dec",
    "aircon.wind_speed.inc",
    "aircon.wind_speed.set",
    "ambient_light.off",
    "ambient_light.on",
    "charging_port.close",
    "charging_port.open",
    "dashcam.close",
    "dashcam.open",
    "door_lock.close",
    "door_lock.open",
    "energy_recovery.dec",
    "energy_recovery.inc",
    "energy_recovery.set",
    "fragrance.off",
    "fragrance.on",
    "fragrance.set",
    "front_defogger.close",
    "front_defogger.open",
    "fuel_tank_cover.close",
    "fuel_tank_cover.open",
    "headlight.off",
    "headlight.on",
    "hvac.dec",
    "hvac.inc",
    "hvac.off",
    "hvac.on",
    "hvac.set",
    "lane_assistance.close",
    "lane_assistance.open",
    "lane_departure_assistance.close",
    "lane_departure_assistance.open",
    "power_mode.set",
    "rear_defogger.close",
    "rear_defogger.open",
    "rear_view_mirror.fold",
    "rear_view_mirror.unfold",
    "scene_mode.set",
    "screen.brightness.dec",
    "screen.brightness.inc",
    "screen.brightness.set",
    "seat.heating.off",
    "seat.heating.on",
    "seat.lumbar_support.off",
    "seat.lumbar_support.on",
    "seat.massage.off",
    "seat.massage.on",
    "seat.ventilation.off",
    "seat.ventilation.on",
    "steering_wheel.heating.close",
    "steering_wheel.heating.open",
    "steering_wheel.height.dec",
    "steering_wheel.height.inc",
    "steering_wheel.height.set",
    "sunroof.close",
    "sunroof.open",
    "sunroof.set",
    "sunshade.close",
    "sunshade.open",
    "sunshade.set",
    "tire_pressure.query",
    "trunk.close",
    "trunk.open",
    "volume.dec",
    "volume.inc",
    "volume.set",
    "window.close",
    "window.open",
    "window.set",
    "wiper.off",
    "wiper.on",
    "wiper.speed.dec",
    "wiper.speed.inc",
    "wiper.speed.set",
}


def test_derived_set_matches_the_pre_migration_literal():
    derived = derive_edge_intents()
    assert derived == _BEFORE_MIGRATION, (
        "派生集合与迁移基线不一致；"
        f"多出来 {sorted(derived - _BEFORE_MIGRATION)}；"
        f"少掉了 {sorted(_BEFORE_MIGRATION - derived)}。"
        "能力面增减是产品决定：确认无误后同步改本文件的 _BEFORE_MIGRATION。")


def test_module_level_constant_is_the_derived_set():
    """`VEHICLE_INTENTS` 就是派生结果本身，没有第二份拷贝在别处漂移。"""
    assert set(VEHICLE_INTENTS) == derive_edge_intents()


def test_derivation_fails_closed_on_empty_knowledge(tmp_path):
    """知识库读不出意图时**拒绝返回空集**——空能力面会让 planner 看不到任何车控工具，
    而那是个静默失败：不报错、只是什么都规划不出来。"""
    import pytest

    from capability_meta import CapabilityKnowledgeError

    (tmp_path / "commands.yaml").write_text("objects: {}", encoding="utf-8")
    with pytest.raises(CapabilityKnowledgeError):
        derive_edge_intents(knowledge_dir=str(tmp_path))
