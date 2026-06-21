"""PgStore 持久化辅助函数单测（不依赖真实 PostgreSQL）。

重点回归：从 PG 还原的 manifest 必须是 AgentManifest proto，能塞进
ResolvedAgent.manifest（proto 字段）；若回退成 SimpleNamespace，重启恢复 /
语义路由会在 server 构造 ResolveResponse 时抛 TypeError。
"""
from cockpit.registry.v1 import registry_pb2

from registry.store import _dict_to_manifest, _manifest_to_dict


_SAMPLE = {
    "agent_id": "charging-planner",
    "version": "0.1.0",
    "display_name": "充能助手",
    "category": "core",
    "trust_level": "first_party",
    "deployment": "cloud",
    "latency_budget_ms": 2000,
    "requires_permissions": ["location.read", "navigation.control"],
    "capabilities": [
        {"intent": "charging.find", "description": "找充电站",
         "slots": ["destination"], "examples": ["找个充电站"],
         "require_confirm": False},
    ],
}


def test_dict_to_manifest_is_proto_serializable_into_response():
    manifest = _dict_to_manifest(_SAMPLE)
    # 关键：必须能赋值进 proto 字段（SimpleNamespace 会抛 TypeError）。
    agent = registry_pb2.ResolvedAgent(
        manifest=manifest, endpoint="localhost:50068", score=0.9)
    assert agent.manifest.agent_id == "charging-planner"
    assert list(agent.manifest.requires_permissions) == [
        "location.read", "navigation.control"]
    assert agent.manifest.capabilities[0].intent == "charging.find"
    assert list(agent.manifest.capabilities[0].examples) == ["找个充电站"]


def test_manifest_dict_roundtrip_preserves_routing_fields():
    # proto → dict → proto 往返后，打分/过滤依赖的字段不丢。
    manifest = _dict_to_manifest(_SAMPLE)
    d = _manifest_to_dict(manifest)
    again = _dict_to_manifest(d)
    assert again.agent_id == "charging-planner"
    assert again.capabilities[0].intent == "charging.find"
    assert list(again.requires_permissions) == [
        "location.read", "navigation.control"]
