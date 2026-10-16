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


def test_the_first_one_reaches_the_agent_through_the_real_downlink():
    """「第一家附近」（§18.7）：真实 manifest 声明的上下文范围 → 编排下发 → 按主体权限投影，周边收到本域那一组的名字、
    能解析出「第一家」。Agent 侧直接塞 meta 的用例验不到这条链路；不声明 `candidates` 就收不到（同一道门控）。"""
    import time
    from types import SimpleNamespace

    from agents._sdk import candidate_ref
    from orchestrator.cloud.context import Focus
    from orchestrator.cloud.engine import PlannerEngine
    from orchestrator.cloud.models import Plan, Step
    from runtime.context_access import project_meta

    manifest, _caps_ = _caps()
    shop = "NOWWA挪瓦咖啡(美宜佳深圳华富洋大厦店)"
    focus = Focus(candidate_sets=[{
        "source_intent": "nearby.search", "agent_id": "nearby", "purpose": "list", "ts": time.time(),
        "is_fallback": False, "items": [{"name": shop, "lat": 22.547, "lng": 114.085}, {"name": "黄色咖啡店"}]}])
    plan = Plan(steps=[Step(id="s1", agent_id="nearby", intent="nearby.search",
                            context_scopes=list(manifest.context_scopes))])
    PlannerEngine._apply_focus_meta(plan, focus)
    subject = SimpleNamespace(granted_permissions=["profile.read", "location.read"], user_id="u1", prefs={})
    meta = project_meta(subject, plan.steps[0].meta, plan.steps[0].context_scopes)
    assert candidate_ref.resolve("第一家", meta, namespace="nearby")["name"] == shop
    assert "22.547" not in meta[candidate_ref.META_KEY]          # 只下发名字，不带坐标

