"""加载 manifest.yaml -> AgentManifest proto。"""
from __future__ import annotations
import yaml
from google.protobuf.struct_pb2 import Struct
from cockpit.agent.v1 import agent_pb2


def build_verification(raw) -> agent_pb2.Verification | None:
    """capability.verification（M2 Outcome Verifier）YAML → proto。

    缺省 / 非 dict / mode 空或 none → None（不声明=不验，零行为变化）。
    `expect` 是自由 Struct（按 mode 定形），中央求值器据此对账——领域期望留在 Agent 侧，
    编排核心零领域分支（同 route_hints 哲学）。
    """
    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("mode", "") or "").strip()
    if not mode or mode == "none":
        return None
    expect = Struct()
    if isinstance(raw.get("expect"), dict):
        expect.update(raw["expect"])
    return agent_pb2.Verification(
        mode=mode,
        timeout_ms=int(raw.get("timeout_ms", 0) or 0),
        on_fail=str(raw.get("on_fail", "") or ""),
        max_attempts=int(raw.get("max_attempts", 0) or 0),
        expect=expect,
    )


def load_manifest(path: str) -> agent_pb2.AgentManifest:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    caps = [
        agent_pb2.Capability(
            intent=c["intent"],
            description=c.get("description", ""),
            slots=c.get("slots", []),
            examples=c.get("examples", []),
            require_confirm=c.get("require_confirm", False),
            heavy=c.get("heavy", False),
            verification=build_verification(c.get("verification")),
        )
        for c in data.get("capabilities", [])
    ]
    # 确定性路由提示（R2.1）：Agent 声明式路由，编排核心 RouteHintEngine 通用消费。
    route_hints = [
        agent_pb2.RouteHint(
            pattern=h["pattern"],
            intent=h["intent"],
            policy=h.get("policy", "replace"),
            priority=int(h.get("priority", 0)),
            guard=h.get("guard", ""),
            slots={k: str(v) for k, v in (h.get("slots") or {}).items()},
        )
        for h in data.get("route_hints", [])
    ]
    return agent_pb2.AgentManifest(
        agent_id=data["agent_id"],
        version=data.get("version", "0.0.0"),
        display_name=data.get("display_name", ""),
        category=data.get("category", "ecosystem"),
        trust_level=data.get("trust_level", "third_party"),
        deployment=data.get("deployment", "cloud"),
        latency_budget_ms=int(data.get("latency_budget_ms", 2000)),
        fallback=data.get("fallback", ""),
        capabilities=caps,
        requires_permissions=data.get("requires_permissions", []),
        edge_intents=data.get("edge_intents", []),
        kind=data.get("kind", "agent"),
        context_scopes=data.get("context_scopes", []),
        route_hints=route_hints,
    )
