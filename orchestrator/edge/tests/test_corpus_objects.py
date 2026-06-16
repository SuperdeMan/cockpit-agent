"""数据驱动的车控对象矩阵语料回归（P2）。

intent_recognition：自然语句 → fast_intent 识别的 object（覆盖识别广度）。
val_execution：结构化协议指令 → VAL 执行后的车辆状态（系统覆盖状态机分支，
对应仪表盘的状态变更）。语料见 corpus/vehicle_objects.yaml。
"""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify_structured
from val import VAL

_CORPUS = os.path.join(os.path.dirname(__file__), "corpus", "vehicle_objects.yaml")
with open(_CORPUS, encoding="utf-8") as _f:
    _CASES = yaml.safe_load(_f)


@pytest.fixture
def val():
    knowledge_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge")
    return VAL(knowledge_dir=knowledge_dir)


@pytest.mark.parametrize("case", _CASES["intent_recognition"], ids=lambda c: c["text"])
def test_intent_recognition(case):
    structured = classify_structured(case["text"])
    assert structured is not None, f"{case['text']!r} 未被 fast_intent 识别"
    obj = structured["data"].get("object")
    expected = case["object"]
    if isinstance(expected, list):
        assert obj in expected, f"{case['text']!r} 识别为 {obj!r}，期望 {expected!r} 之一"
    else:
        assert obj == expected, f"{case['text']!r} 识别为 {obj!r}，期望 {expected!r}"


def _exec_id(case):
    return f"{case['object']}.{case['operate']}"


@pytest.mark.parametrize("case", _CASES["val_execution"], ids=_exec_id)
def test_val_execution_state(val, case):
    data = {"operate": case["operate"], "object": case["object"]}
    for key in ("value", "mode", "tag", "attr"):
        if key in case:
            data[key] = case[key]
    cmd = {"domain": "setting", "intent": "control", "data": data}
    ok, msg = val.execute(cmd)
    assert ok, f"{_exec_id(case)} 执行失败：{msg}"
    for key, expected in case["expect"].items():
        actual = val.state.get(key)
        assert actual == expected, (
            f"{_exec_id(case)}: state {key}={actual!r}, expected {expected!r}"
        )
