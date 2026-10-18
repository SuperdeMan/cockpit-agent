"""route hint 的 `complexity`（200×5 后续批 §21.5）：SDK 从 manifest 读进 proto，缺省空串 = 不改计划复杂度。"""
from pathlib import Path

from agents._sdk.manifest import load_manifest

_INFO = Path(__file__).resolve().parents[2] / "info" / "manifest.yaml"


def test_the_weather_outing_hint_declares_adaptive_and_the_others_default_to_unchanged():
    hints = list(load_manifest(str(_INFO)).route_hints)
    adaptive = [h for h in hints if h.complexity]
    assert [(h.intent, h.complexity, h.policy) for h in adaptive] == [("info.weather", "adaptive", "replace")]
    assert len(hints) > len(adaptive) and all(h.complexity == "" for h in hints if h not in adaptive)
