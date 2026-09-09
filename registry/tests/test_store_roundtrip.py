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


# ─────────────────────────────────────────────────────────────────────────────
# AR05 F08（2026-09-09）：逐字段对账，而不是每发现一个丢字段就补一条断言。
#
# 复现记录：`slot_shapes` / `whole_utterance` / `RouteHint.scope` 三个字段在
# `_manifest_to_dict` 里存进了 JSON，`_dict_to_manifest` 却**根本没读**——
# registry 重启恢复后，wait_slot 的槽值形状判据、「同一份计划最多一步」的整句约束、
# 接送 hint 的分句锚定同时静默失效。这已经是这个适配器第三次丢字段
# （route_hints → verification → 本批），说明缺的不是断言而是**判据**。
#
# 下面两条把「新加的 proto 字段有没有过 round-trip」变成机器判据：
# ① 夹具必须把每个字段都填成非默认值（新字段不填 → 夹具那条先红）；
# ② round-trip 后整条 proto 必须逐字节等价（适配器漏读 → 这条红）。
# ─────────────────────────────────────────────────────────────────────────────

def _fully_populated_manifest():
    """每个字段都取非默认值的 manifest。新增 proto 字段时**必须**在这里补一笔。"""
    return agent_pb2.AgentManifest(
        agent_id="full-fixture",
        version="9.9.9",
        display_name="全字段夹具",
        category="ecosystem",
        trust_level="third_party",
        deployment="edge",
        latency_budget_ms=1234,
        fallback="chitchat",
        requires_permissions=["merchant.read"],
        edge_intents=["window.open"],
        kind="edge_fast",
        context_scopes=["location"],
        capabilities=[
            agent_pb2.Capability(
                intent="fixture.do",
                description="夹具能力",
                slots=["when"],
                examples=["例句"],
                require_confirm=True,
                heavy=True,
                slot_shapes={"when": "time_phrase"},
                whole_utterance=True,
                response_only=True,
                verification=agent_pb2.Verification(
                    mode="schema", timeout_ms=1500, on_fail="retry", max_attempts=2,
                    expect=_expect({"data_keys": ["items"]})),
            ),
        ],
        route_hints=[
            agent_pb2.RouteHint(
                pattern="接.+", intent="fixture.do", policy="append", priority=7,
                guard="不接", slots={"raw": "$text"}, scope="clause"),
        ],
    )


def _unset_fields(msg, path=""):
    """返回夹具里仍是默认值的字段路径（递归子 message，Struct 只看非空）。"""
    from google.protobuf.struct_pb2 import Struct

    missing = []
    for field in msg.DESCRIPTOR.fields:
        name = f"{path}{field.name}"
        value = getattr(msg, field.name)
        if field.is_repeated:
            if not value:
                missing.append(name)
                continue
            # map<> 与 repeated message 都在这里；逐元素递归只对 message 元素有意义
            if field.message_type is not None and not field.message_type.GetOptions().map_entry:
                for i, item in enumerate(value):
                    missing.extend(_unset_fields(item, f"{name}[{i}]."))
            continue
        if field.message_type is not None:
            if not msg.HasField(field.name):
                missing.append(name)
                continue
            if isinstance(value, Struct):
                if not value.fields:
                    missing.append(name)
                continue
            missing.extend(_unset_fields(value, f"{name}."))
            continue
        if value in ("", 0, False):
            missing.append(name)
    return missing


def test_roundtrip_fixture_covers_every_declared_field():
    """夹具本身必须是满的——否则下一条的「等价」是拿空字段换来的假绿。"""
    missing = _unset_fields(_fully_populated_manifest())
    assert missing == [], (
        f"这些字段在 round-trip 夹具里还是默认值，新增 proto 字段后请补齐：{missing}")


def test_manifest_roundtrip_is_lossless_for_every_field():
    """PgStore 真栈路径 proto→dict→JSON→dict→proto 必须逐字段无损。"""
    import json

    original = _fully_populated_manifest()
    stored = json.loads(json.dumps(_manifest_to_dict(original), ensure_ascii=False))
    restored = _dict_to_manifest(stored)

    assert restored == original, (
        "registry 重启恢复丢字段——声明式机制会在重启后静默失效。\n"
        f"stored={stored}")


def test_non_proto_manifest_roundtrip_is_lossless_for_declared_fields():
    """内存/测试形态（dataclass、SimpleNamespace）走的是手写序列化那一支。"""
    original = _fully_populated_manifest()
    ns = SimpleNamespace(
        **{f.name: getattr(original, f.name)
           for f in original.DESCRIPTOR.fields})

    restored = _dict_to_manifest(_manifest_to_dict(ns))

    assert restored.capabilities[0].slot_shapes == original.capabilities[0].slot_shapes
    assert restored.capabilities[0].whole_utterance is True
    assert list(restored.context_scopes) == ["location"]
    assert list(restored.edge_intents) == ["window.open"]
    assert restored.kind == "edge_fast"
    assert restored.route_hints[0].scope == "clause"
