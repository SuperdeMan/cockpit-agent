"""registry PgStore manifest round-trip 必须保 route_hints/heavy/context_scopes/
verification（R2.1 + M2）。

registry 重启恢复经 _manifest_to_dict→JSON→_dict_to_manifest 还原 AgentManifest；
任一字段丢失都会让声明式机制静默失效——route_hints 丢了确定性路由兜底不生效（R2.1 真栈
踩过），verification 丢了执行后对账不生效（M2 同一类坑，故一并钉死在本文件）。
单测用 MockAgent 直挂字段会漏掉这条真栈路径。
"""
from types import SimpleNamespace

from google.protobuf.struct_pb2 import Struct
from cockpit.agent.v1 import agent_pb2
from registry.store import _manifest_to_dict, _dict_to_manifest


def _expect(d: dict) -> Struct:
    s = Struct()
    s.update(d)
    return s


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


def _verified_manifest():
    return agent_pb2.AgentManifest(
        agent_id="edge-vehicle", version="1.0.0", category="core", deployment="edge",
        capabilities=[
            agent_pb2.Capability(
                intent="hvac.set",
                verification=agent_pb2.Verification(
                    mode="state_match", timeout_ms=2500, on_fail="report",
                    max_attempts=1,
                    expect=_expect({"mirror": "vehicle_state",
                                    "keys": {"hvac_on": "true"}}))),
            agent_pb2.Capability(intent="hvac.off"),      # 未声明：round-trip 后仍不该验
        ],
    )


def test_manifest_roundtrip_preserves_verification():
    """M2：registry 重启恢复后执行后对账必须还在（丢了=车控步「没生效」重新变静默）。"""
    restored = _dict_to_manifest(_manifest_to_dict(_verified_manifest()))
    v = restored.capabilities[0].verification
    assert v.mode == "state_match"
    assert v.timeout_ms == 2500
    assert v.on_fail == "report"
    assert v.max_attempts == 1
    expect = dict(v.expect)
    assert expect["mirror"] == "vehicle_state"
    assert dict(expect["keys"]) == {"hvac_on": "true"}


def test_manifest_roundtrip_keeps_undeclared_capability_unverified():
    """未声明 verification 的能力 round-trip 后仍是「不验」——不能凭空长出对账。"""
    restored = _dict_to_manifest(_manifest_to_dict(_verified_manifest()))
    assert restored.capabilities[1].verification.mode == ""
    assert restored.capabilities[1].HasField("verification") is False


def test_manifest_roundtrip_survives_json_serialization():
    """真栈路径是 dict→JSON→dict（PgStore 存的是 JSON 文本），Struct 必须能过 JSON。"""
    import json
    d = json.loads(json.dumps(_manifest_to_dict(_verified_manifest()),
                              ensure_ascii=False))
    restored = _dict_to_manifest(d)
    assert restored.capabilities[0].verification.mode == "state_match"
    assert dict(dict(restored.capabilities[0].verification.expect)["keys"]) == {
        "hvac_on": "true"}


def test_manifest_roundtrip_preserves_response_only_and_default_false():
    """Registry 重启后 response-only 权威不得丢失，未声明能力仍保持 false。"""
    manifest = agent_pb2.AgentManifest(
        agent_id="chitchat",
        capabilities=[
            agent_pb2.Capability(intent="chitchat.talk", response_only=True),
            agent_pb2.Capability(intent="chitchat.audit"),
        ],
    )

    restored = _dict_to_manifest(_manifest_to_dict(manifest))

    assert restored.capabilities[0].response_only is True
    assert restored.capabilities[1].response_only is False


def test_non_proto_manifest_roundtrip_preserves_response_only():
    """测试/内存形态同样要把 response_only 写入持久化 dict。"""
    manifest = SimpleNamespace(
        agent_id="chitchat",
        capabilities=[
            SimpleNamespace(intent="chitchat.talk", response_only=True),
        ],
    )

    serialized = _manifest_to_dict(manifest)
    assert serialized["capabilities"][0].get("response_only") is True

    restored = _dict_to_manifest(serialized)
    assert restored.capabilities[0].response_only is True
