"""Offline CA2-05 inventory using the production manifest builders; no providers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "gen/python", ROOT / "orchestrator/edge"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agents._sdk.manifest import load_manifest
from runtime.capability_contract import abi_digest, capability_digest, contract_of, validate_manifest


def collect_manifests():
    from agents.mcp_bridge.src.agent import McpBridgeAgent
    from agents.mcp_bridge.src.admission import load_servers, load_local_capabilities
    from orchestrator.edge.capabilities import build_edge_manifests
    from orchestrator.cloud.tools.registry import ToolRegistry

    result = []
    for path in sorted((ROOT / "agents").glob("*/manifest.yaml")):
        if path.parent.name != "mcp_bridge":
            result.append(load_manifest(str(path)))
    bridge = McpBridgeAgent.__new__(McpBridgeAgent)
    bridge.manifest = load_manifest(str(ROOT / "agents/mcp_bridge/manifest.yaml"))
    config = str(ROOT / "agents/mcp_bridge/servers.yaml")
    servers = load_servers(config)
    bridge._bindings = {t.intent: SimpleNamespace(tool=t, server=s)
                        for s in servers for t in s.tools}
    bridge._workflow_bindings = {w.intent: SimpleNamespace(spec=w, server=s)
                                 for s in servers for w in s.workflows}
    bridge._local_capabilities = load_local_capabilities(config)
    bridge._sync_capabilities()
    result.append(bridge.manifest)
    result.extend(build_edge_manifests())
    result.append(ToolRegistry().manifest)
    return result


def inventory(*, validate=False):
    rows = {}
    for manifest in collect_manifests():
        if validate:
            validate_manifest(manifest)
        for cap in manifest.capabilities:
            if validate and not contract_of(cap):
                raise ValueError("producer_missing_contract: " + manifest.agent_id + "/" + cap.intent)
            key = f"{manifest.agent_id}/{cap.intent}"
            if key in rows:
                raise ValueError("duplicate capability identity: " + key)
            rows[key] = {"abi_sha256": abi_digest(manifest, cap),
                         "contract_sha256": capability_digest(manifest, cap),
                         "effect": contract_of(cap).get("effect", "legacy_unspecified")}
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.out and args.out.resolve() == (ROOT / "runtime/capability_migration.json").resolve():
        parser.error("the migration inventory is frozen; do not overwrite it from current declarations")
    rows = inventory(validate=args.check)
    payload = {"schema_version": 1, "capabilities": rows}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"capabilities": len(rows), "validated": args.check,
                      "effects": {e: sum(r["effect"] == e for r in rows.values())
                                  for e in sorted({r["effect"] for r in rows.values()})}}))


if __name__ == "__main__":
    main()
