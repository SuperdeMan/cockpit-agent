"""Edge fast capabilities are discoverable without weakening permissions."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from capabilities import _describe, _knowledge, build_edge_manifests
from edge_agents_mod.media import MEDIA_INTENTS
from edge_agents_mod.vehicle import VEHICLE_INTENTS
from edge_call import decode_intent

_FALLBACKS = ("通过车端 VAL 执行确定性车控意图", "通过车端执行器控制本地媒体")


def _all_caps():
    return [c for m in build_edge_manifests() for c in m.capabilities]


def test_edge_vehicle_and_media_capabilities_are_separate_and_routable():
    manifests = {m.agent_id: m for m in build_edge_manifests()}

    vehicle = manifests["edge-vehicle"]
    media = manifests["edge-media"]

    assert vehicle.deployment == "edge"
    assert vehicle.kind == "edge_fast"
    assert vehicle.trust_level == "system"
    assert list(vehicle.requires_permissions) == ["vehicle.control"]
    assert any(c.intent == "hvac.set" for c in vehicle.capabilities)
    assert not any(c.intent == "media.play" for c in vehicle.capabilities)

    assert media.deployment == "edge"
    assert media.kind == "edge_fast"
    assert list(media.requires_permissions) == ["media.control"]
    assert {c.intent for c in media.capabilities} >= {
        "media.play", "media.pause", "media.next", "media.prev",
    }


# ── 判别化描述（M5 P3 收尾）────────────────────────────────────────────────────
#
# 这组测试守的性质只有一条：**planner 面前不许再出现文本上无法区分的工具**。
# P3a 影子第一条观测就是这条性质失守的代价——78 个 capability 共用两句描述，
# `关闭强力前除雾` 被规划成 `accompany_home.close` 并被 VAL 照单执行。


def test_every_capability_has_a_generated_description():
    """一条都不许回落到泛化兜底句。

    `_capabilities` 里的 `or fallback` 是运行期不崩的保险，**不是可接受的稳态**：
    新增 intent 若少了 display_name 或中间段中文，本条当场红——这正是「没消费方的
    契约会潜伏」那一课的反向用法，把沉默失败换成阻断。
    """
    generic = [c.intent for c in _all_caps() if c.description in _FALLBACKS]
    assert not generic, f"这些 capability 仍是泛化描述：{generic}"


def test_descriptions_are_pairwise_distinct():
    """两两不同——否则就是把「描述逐字重叠 → planner 掷硬币」原样搬了过来。"""
    seen: dict[str, list[str]] = {}
    for c in _all_caps():
        seen.setdefault(c.description, []).append(c.intent)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dups, f"描述重复：{dups}"


def test_description_count_matches_executable_surface():
    caps = _all_caps()
    assert len(caps) == len(VEHICLE_INTENTS | MEDIA_INTENTS) == 78


@pytest.mark.parametrize("intent,expect", [
    # 此前只靠名字猜的四组（名字相近、语义完全不同），现在文本上就分得开
    ("lane_assistance.open", "打开车道保持辅助"),
    ("lane_departure_assistance.open", "打开车道偏离预警"),
    ("accompany_home.close", "关闭伴我回家灯光"),
    ("hvac.off", "关闭空调"),
    # 中间段与默认属性两条渲染路径
    ("seat.lumbar_support.on", "打开座椅腰托"),
    ("aircon.wind_speed.inc", "调高空调风速"),
    ("hvac.inc", "调高空调温度"),          # path 空 → 取 attrs[0]=temperature，与 VAL _simulate 一致
    # 模式选择器才带取值清单（aircon 有 attrs，故 hvac.set 不灌 19 个 mode）
    ("power_mode.set", "设置动力模式（标准/运动/节能）"),
    ("hvac.set", "设置空调温度"),
    # 后置动词
    ("media.next", "媒体切换到下一个"),
])
def test_description_rendering(intent, expect):
    objects, entities = _knowledge()
    assert _describe(intent, objects, entities) == expect


def test_descriptions_are_decoded_by_the_executor_decoder():
    """描述里的对象名必须来自 executor 真会用的那次解码，不是第二份映射表。

    守的是「要比对的两端必须同源」：`_describe` 与 `EdgeCallExecutor.execute` 都走
    `decode_intent`，所以描述说的对象 = VAL 待会儿校验和执行的对象。若有人为了让描述
    好看另建一张 intent→对象 表，两边就会各自漂移——而描述写错不报错，只让 planner
    悄悄选错工具。
    """
    objects, _ = _knowledge()
    known = set(objects)
    for intent in sorted(VEHICLE_INTENTS | MEDIA_INTENTS):
        decoded = decode_intent(intent, known)
        assert decoded, f"{intent} 无法被 executor 解码，却出现在能力面上"
        display = (objects[decoded["data"]["object"]] or {}).get("display_name")
        assert display, f"{decoded['data']['object']} 缺 display_name"


def test_every_val_object_declares_a_display_name():
    """display_name 是对象自己的属性，跟着对象放在 commands.yaml——**只准有一处**。"""
    objects, _ = _knowledge()
    assert len(objects) == 65
    missing = [k for k, v in objects.items() if not (v or {}).get("display_name")]
    assert not missing, f"这些对象缺 display_name：{missing}"
