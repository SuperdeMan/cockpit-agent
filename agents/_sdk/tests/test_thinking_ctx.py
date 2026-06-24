"""SDK 自动覆盖：LLMClient 从当前请求 meta（contextvar）判定是否开思考，
无需改各 Agent 业务码。复杂任务由编排层下发 meta["thinking"]="on"。"""
from agents._sdk._ctx import set_current_meta
from agents._sdk.clients import _resolve_thinking


def teardown_function():
    set_current_meta(None)   # 防止泄漏到后续用例


def test_no_meta_defaults_off():
    set_current_meta(None)
    assert _resolve_thinking(None) is False


def test_meta_thinking_on_enables():
    set_current_meta({"thinking": "on"})
    assert _resolve_thinking(None) is True


def test_explicit_arg_overrides_meta():
    set_current_meta({"thinking": "on"})
    assert _resolve_thinking(False) is False   # 显式传参优先于 meta
    assert _resolve_thinking(True) is True


def test_meta_thinking_off_or_absent():
    set_current_meta({"thinking": "off"})
    assert _resolve_thinking(None) is False
    set_current_meta({"other": "x"})
    assert _resolve_thinking(None) is False
