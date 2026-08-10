"""VAL 二次确认闸的**结构性不变量**（B1）。

风格参照 `orchestrator/cloud/tests/test_voiceprint_not_auth.py`：不是测某条业务路径，
而是把一条红线钉在源码上——「危险动作未经确认不得执行」必须由 VAL 一处结构性保证，
而不是靠每条上游路径自觉。此前 `val._structured_execute` 第 4 步是 PoC 注释
「直接执行」，于是任何绕过确认闭环的路径（云端降级兜底、异常回流 action）都能
开后备箱 / 解锁车门。

这里的断言**数据驱动**：危险对象取自 `knowledge/commands.yaml` 的 `require_confirm`，
将来新增危险对象自动进入覆盖，不需要改这个文件。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from val import VAL


@pytest.fixture
def val():
    knowledge_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge")
    return VAL(knowledge_dir=knowledge_dir)


def _dangerous_objects() -> list[str]:
    knowledge_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge")
    objects = (VAL(knowledge_dir=knowledge_dir).commands or {}).get("objects") or {}
    return sorted(k for k, v in objects.items() if v.get("require_confirm"))


DANGEROUS = _dangerous_objects()


def test_dangerous_object_set_is_not_empty():
    """兜底：若知识库结构变了导致这个集合塌成空，下面所有参数化测试会静默零执行。"""
    assert DANGEROUS, "commands.yaml 未声明任何 require_confirm 对象——闸失去被测对象"
    assert "trunk" in DANGEROUS and "door_lock" in DANGEROUS


def _open_cmd(obj: str) -> dict:
    return {"domain": "setting", "intent": "control",
            "data": {"operate": "open", "object": obj}}


@pytest.mark.parametrize("obj", DANGEROUS)
def test_unconfirmed_dangerous_command_is_rejected(val, obj):
    """核心不变量：confirmed=False（默认）时危险动作必被拒，且**状态零变化**。"""
    before = dict(val.state)
    ok, speech = val.execute(_open_cmd(obj))
    assert ok is False, f"{obj}.open 未经确认竟被执行"
    assert val.state == before, f"{obj}.open 被拒却改了车辆状态：{val.state}"
    assert speech, "拒绝必须有话术，不能静默"


@pytest.mark.parametrize("obj", DANGEROUS)
def test_confirmed_dangerous_command_executes(val, obj):
    """反向：带确认凭据则放行——闸是闸，不是禁令（否则确认闭环整条链就断了）。"""
    ok, speech = val.execute(_open_cmd(obj), confirmed=True)
    assert ok is True, f"{obj}.open 带 confirmed=True 仍被拒：{speech}"
    assert val.state != {}, "执行成功却无任何状态"


@pytest.mark.parametrize("obj", DANGEROUS)
def test_legacy_string_path_also_gated(val, obj):
    """legacy 字符串面同样有闸——当前 `_apply` 恰好没实现危险对象，
    但「恰好没实现」不是不变量，闸才是。"""
    before = dict(val.state)
    ok, _ = val.execute(f"{obj}.open", {})
    assert ok is False
    assert val.state == before


def test_non_dangerous_object_unaffected(val):
    """回归防线：闸不许误伤普通车控（默认 confirmed=False 也照常执行）。"""
    ok, _ = val.execute({"domain": "setting", "intent": "control",
                         "data": {"operate": "open", "object": "window"}})
    assert ok is True
    assert val.state["window"] == "open"


def test_default_is_fail_closed_by_signature():
    """签名级断言：`confirmed` 的默认值必须是 False。

    这条看着像废话，但它防的是一类真实改法——某天有人为了「让某条路径跑起来」
    把默认值翻成 True，所有调用点无声地全部解闸，而业务测试照样全绿。
    """
    import inspect
    for fn in (VAL.execute, VAL._run, VAL._structured_execute, VAL._legacy_execute):
        sig = inspect.signature(fn)
        assert "confirmed" in sig.parameters, f"{fn.__qualname__} 丢了 confirmed 形参"
        assert sig.parameters["confirmed"].default is False, (
            f"{fn.__qualname__} 的 confirmed 默认值不是 False——闸默认打开了")
