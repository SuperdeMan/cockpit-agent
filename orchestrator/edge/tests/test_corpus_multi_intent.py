"""数据驱动的多意图拆分边界语料回归（P2）。

系统化覆盖 fast_intent 的拆分与不拆边界（连接词 / 逗号 / "和" 的安全二次拆分），
与 test_multi_intent_split / test_he_split 的手写断言互补。语料见 corpus/multi_intent.yaml。
"""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import split_and_classify_any

_CORPUS = os.path.join(os.path.dirname(__file__), "corpus", "multi_intent.yaml")
with open(_CORPUS, encoding="utf-8") as _f:
    _CASES = yaml.safe_load(_f)


@pytest.mark.parametrize("case", _CASES["split"], ids=lambda c: c["text"])
def test_should_split(case):
    result = split_and_classify_any(case["text"])
    assert result is not None, f"{case['text']!r} 应被拆分"
    assert len(result) == case["parts"], (
        f"{case['text']!r} 拆成 {len(result)} 段，期望 {case['parts']} 段"
    )


@pytest.mark.parametrize("case", _CASES["no_split"], ids=lambda c: c["text"])
def test_should_not_split(case):
    result = split_and_classify_any(case["text"])
    assert result is None, (
        f"{case['text']!r} 不应拆分（{case['reason']}），却得到 {result!r}"
    )
