"""registry PgStore manifest round-trip 必须保 route_hints/heavy/context_scopes（R2.1）。

registry 重启恢复经 _manifest_to_dict→JSON→_dict_to_manifest 还原 AgentManifest；
任一字段丢失都会让 RouteHintEngine 拿不到提示、确定性路由兜底静默失效
（单测用 MockAgent 直挂 route_hints 会漏掉这条真栈路径）。
"""
from cockpit.agent.v1 import agent_pb2
from registry.store import _manifest_to_dict, _dict_to_manifest


def _manifest():
    return agent_pb2.AgentManifest(
        agent_id="trip-planner", version="0.1.0", category="ecosystem",
        deployment="cloud", kind="agent",
        context_scopes=["location", "vehicle_state"],
        capabilities=[
            agent_pb2.Capability(intent="trip.plan", heavy=True, slots=["destination"]),
        ],
        route_hints=[
            agent_pb2.RouteHint(pattern="去.+天", intent="trip.plan", policy="append",
                                priority=50, guard="去公司", slots={"raw": "$text"}),
        ],
    )


def test_manifest_roundtrip_preserves_route_hints_heavy_context_scopes():
    restored = _dict_to_manifest(_manifest_to_dict(_manifest()))
    assert list(restored.context_scopes) == ["location", "vehicle_state"]
    assert restored.capabilities[0].heavy is True
    assert len(restored.route_hints) == 1
    h = restored.route_hints[0]
    assert h.pattern == "去.+天"
    assert h.intent == "trip.plan"
    assert h.policy == "append"
    assert h.priority == 50
    assert h.guard == "去公司"
    assert dict(h.slots) == {"raw": "$text"}
