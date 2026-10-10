"""续问窗受话判定评测语料的尺子自检（零网络）：结构、计数下限、开发集与留出集不重叠、留出集不进提示词。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "eval_followup_admission", Path(__file__).resolve().parent / "eval_followup_admission.py")
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_the_followup_admission_corpora_are_sound():
    assert _MOD.check_offline() == []


def test_the_leak_guard_catches_a_prompt_example(monkeypatch):
    """反向验证：把提示词里的一个示例塞进留出集，自检必须红。"""
    original = _MOD.load

    def load(split):
        data = original(split)
        if split == "heldout":
            data = {**data, "cases": [*data["cases"], {"ctx": next(iter(data["contexts"])), "text": "那明天呢",
                                                        "expect": "accept"}]}
        return data

    monkeypatch.setattr(_MOD, "load", load)
    assert any("提示词" in e for e in _MOD.check_offline())
