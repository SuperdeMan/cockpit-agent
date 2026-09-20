"""`Capability.effect`（评审 W11，2026-09-20）：声明在 Agent、loader 校验值域、中央只消费。"""
from pathlib import Path

import pytest
import yaml

from agents._sdk.manifest import EFFECTS, capability_effect, load_manifest

_ROOT = Path(__file__).resolve().parents[3]


def _cap(path: str, intent: str):
    manifest = load_manifest(str(_ROOT / path))
    return next(c for c in manifest.capabilities if c.intent == intent)


@pytest.mark.parametrize("raw, expect", [
    (None, ""), ("", ""), ("read", "read"), ("write", "write"), (" Write ", "write")])
def test_capability_effect_normalizes(raw, expect):
    assert capability_effect(raw) == expect


@pytest.mark.parametrize("raw", ["wirte", "control", "yes", 1])
def test_capability_effect_rejects_unknown_values_loudly(raw):
    with pytest.raises(ValueError):
        capability_effect(raw)


def test_effect_values_are_a_closed_vocabulary():
    assert EFFECTS == ("", "read", "write")


def test_real_write_capabilities_declare_write():
    assert _cap("agents/reminder/manifest.yaml", "reminder.create").effect == "write"
    assert _cap("agents/navigation/manifest.yaml", "navigation.navigate_to").effect == "write"
    assert _cap("agents/scene_orchestrator/manifest.yaml", "scene.activate").effect == "write"


def test_real_read_capabilities_declare_read_or_nothing():
    assert _cap("agents/reminder/manifest.yaml", "reminder.list").effect in ("", "read")
    assert _cap("agents/info/manifest.yaml", "info.weather").effect in ("", "read")
    assert _cap("agents/chitchat/manifest.yaml", "chitchat.talk").effect in ("", "read")


def test_every_cloud_manifest_effect_is_in_vocabulary():
    """manifest.yaml 里写的每个 effect 都过 loader 的值域——写错的字母不许静默变成「未声明」。"""
    for path in sorted(_ROOT.glob("agents/*/manifest.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for cap in data.get("capabilities") or []:
            assert capability_effect(cap.get("effect")) in EFFECTS, path
