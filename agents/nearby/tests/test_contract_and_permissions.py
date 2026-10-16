"""周边发现的权限与严格契约（2026-10-16，200×5 后续批 §18.2）。

修前整个 Agent 为一个不扣款的预留下单桩要求 `payment.invoke`：没有支付权限的主体连只读的周边搜索都看不到，依赖它的规划指南
（`weather-outing`）跟着被挡掉——核心旅程 A1-5「今天的天气适合去哪玩」5 / 5 只答天气、NB11 模拟车车道第一句就被拒。
去掉它要改能力 ABI，三条能力随之迁到严格契约：Agent 实际读的槽（含规划器偶发用的地名别名）全部声明成参数，否则带它的步会被拒。
"""
from pathlib import Path

import runtime.capability_contract as cc
from agents._sdk.manifest import load_manifest
from agents.nearby.src.agent import NearbyAgent

_MANIFEST = Path(__file__).resolve().parents[1] / "manifest.yaml"


def _caps():
    manifest = load_manifest(str(_MANIFEST))
    return manifest, {cap.intent: cap for cap in manifest.capabilities}


def test_discovery_does_not_need_the_payment_permission():
    manifest, _caps_ = _caps()
    assert "payment.invoke" not in list(manifest.requires_permissions)
    cc.validate_manifest(manifest)


def test_every_slot_the_agent_reads_is_a_declared_parameter():
    """严格契约（`reject`）下没声明的槽整步被拒：别名只进契约参数、不进 `slots`（规划器看不到它们）。"""
    _manifest, caps = _caps()
    reads = {
        "nearby.search": set(caps["nearby.search"].slots) | set(NearbyAgent._NEAR_ALIAS_SLOTS),
        "nearby.detail": {"poi_id", "id", "name", "restaurant_name"},
        "nearby.order": {"poi_id", "name", "restaurant_name"},
    }
    for intent, names in reads.items():
        contract = cc.contract_of(caps[intent])
        assert contract["additional_parameters"] == "reject", intent
        for name in names:
            assert cc.argument_error(contract, {name: "深圳湾"}) == "", (intent, name)
        assert not set(NearbyAgent._NEAR_ALIAS_SLOTS) & set(caps[intent].slots), intent
