"""Manifest loader preserves the unified capability kind field."""
from __future__ import annotations

from agents._sdk.manifest import load_manifest


def test_manifest_kind_is_loaded(tmp_path):
    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(
        "\n".join([
            "agent_id: local-tool",
            "kind: tool",
            "deployment: cloud",
            "capabilities:",
            "  - intent: math.eval",
            "    description: safe math",
        ]),
        encoding="utf-8",
    )

    manifest = load_manifest(str(manifest_file))

    assert manifest.kind == "tool"
