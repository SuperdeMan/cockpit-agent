from pathlib import Path

from agents._sdk.manifest import load_manifest


_ROOT = Path(__file__).resolve().parents[3]


def _cap(path: str, intent: str):
    manifest = load_manifest(str(_ROOT / path))
    return next(c for c in manifest.capabilities if c.intent == intent)


def test_real_chitchat_declares_response_only():
    assert getattr(
        _cap("agents/chitchat/manifest.yaml", "chitchat.talk"),
        "response_only",
        False,
    ) is True


def test_ordinary_capability_defaults_false():
    assert getattr(
        _cap("agents/info/manifest.yaml", "info.search"),
        "response_only",
        False,
    ) is False
