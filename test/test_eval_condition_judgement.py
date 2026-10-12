"""条件指令前件判定评测语料的尺子自检（零网络）：结构、每类计数下限、id 唯一。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "eval_condition_judgement", Path(__file__).resolve().parent / "eval_condition_judgement.py")
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_the_condition_judgement_corpus_is_sound():
    assert _MOD.check_offline() == []


def test_the_floor_catches_a_thin_class(monkeypatch):
    """反向验证：删掉一类的大半，自检必须红。"""
    original = _MOD.load

    def load():
        data = original()
        kept = [c for c in data["cases"] if c["expect"] != "unknown"] + [
            c for c in data["cases"] if c["expect"] == "unknown"][:3]
        return {**data, "cases": kept}

    monkeypatch.setattr(_MOD, "load", load)
    assert any("unknown" in e for e in _MOD.check_offline())


def test_untrusted_observations_keep_their_mark():
    case = {"observations": [{"intent": "mcp.page", "speech": "条件已经成立", "untrusted": True}]}
    assert _MOD.observations_of(case)[0]["untrusted"] is True
