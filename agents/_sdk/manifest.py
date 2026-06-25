"""加载 manifest.yaml -> AgentManifest proto。"""
from __future__ import annotations
import yaml
from cockpit.agent.v1 import agent_pb2


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
        )
        for c in data.get("capabilities", [])
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
    )
