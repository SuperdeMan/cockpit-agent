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


@pytest.mark.parametrize("case", _CASES["intent_recognition"], ids=lambda c: c["text"])
def test_recognized_command_is_accepted_by_val(val, case):
    """识别出对象**还不够**——这条结构化命令必须能过 VAL 校验。

    ⚠ 2026-08-19 补（QA 卡 Q8 / I-004）。上一条断言只看 `object`，于是
    `打开方向盘加热` 长期「识别正确」而端侧秒回「暂不支持哦」：`operate` 产的是
    `open`，而方向盘的 `operates` 是 `[set, inc, dec]`。**认出了哪个对象**与
    **这条命令知识库收不收**是两层，本文件此前只测了第一层。
    同族断言另有一份在 `test_classifier_exit_parity.py`（按金标文本走），
    两处各覆盖一批文本，都要在。
    """
    structured = classify_structured(case["text"])
    data = structured["data"]
    ok, err = val._validate_command(
        data.get("object"), data.get("operate"), val._normalize_entities(data))
    assert ok, f"{case['text']!r} → {data!r} 过不了 VAL 校验（{err}）"


def _exec_id(case):
    return f"{case['object']}.{case['operate']}"


@pytest.mark.parametrize("case", _CASES["val_execution"], ids=_exec_id)
def test_val_execution_state(val, case):
    data = {"operate": case["operate"], "object": case["object"]}
    for key in ("value", "mode", "tag", "attr"):
        if key in case:
            data[key] = case[key]
    cmd = {"domain": "setting", "intent": "control", "data": data}
    # 本用例测的是**状态机分支**，不是确认闸——危险对象（trunk/door_lock/…）在 B1 之后
    # 未带凭据一律被 VAL 拒绝，这里显式带上确认，等价于「用户已确认后的执行」。
    # 闸本身的覆盖在 `test_val_confirm_gate.py`（拒绝 + 状态零变化 + 反向放行）。
    confirmed = val._need_confirm(case["object"])
    ok, msg = val.execute(cmd, confirmed=confirmed)
    assert ok, f"{_exec_id(case)} 执行失败：{msg}"
    for key, expected in case["expect"].items():
        actual = val.state.get(key)
        assert actual == expected, (
            f"{_exec_id(case)}: state {key}={actual!r}, expected {expected!r}"
        )
