"""运行时硬化 D2：请求级 LLM pin 的透传链路（prefs 白名单 → step meta → SDK stamp）。"""
from types import SimpleNamespace

from orchestrator.cloud.clients import Clients
from orchestrator.cloud.engine import PlannerEngine


def _request(meta):
    return SimpleNamespace(
        request_id="r1", session_id="s1", is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
        meta=meta,
    )


def test_build_context_allowlists_llm_pin_keys():
    engine = object.__new__(PlannerEngine)
    ctx = engine._build_context(_request({
        "granted_scopes": "navigation.control",
        "llm_provider": "mimo", "llm_model": "mimo-v2.5",
    }))
    assert ctx.prefs["llm_provider"] == "mimo"
    assert ctx.prefs["llm_model"] == "mimo-v2.5"


def test_merge_meta_forwards_pin_to_agent_meta():
    """prefs 作底 → 每个 step 的 ExecuteRequest.meta 自动携带 pin（Agent 路径覆盖）。"""
    ctx = SimpleNamespace(prefs={"llm_provider": "mimo"})
    merged = Clients._merge_meta(ctx, {"confirmed": "1"})
    assert merged["llm_provider"] == "mimo"
    assert merged["confirmed"] == "1"


def test_sdk_stamp_forwards_pin_to_llm_request():
    """Agent SDK：pin 随父请求 meta 经 contextvar 自动进 LLM 请求（同 thinking 模式）。"""
    from cockpit.llm.v1 import llm_pb2

    from agents._sdk._ctx import set_current_meta
    from agents._sdk.clients import _stamp_obs_meta

    set_current_meta({"llm_provider": "mimo", "llm_model": "mimo-v2.5"})
    try:
        req = llm_pb2.CompleteRequest()
        _stamp_obs_meta(req)
        assert req.meta["llm_provider"] == "mimo"
        assert req.meta["llm_model"] == "mimo-v2.5"
    finally:
        set_current_meta(None)


def test_sdk_stamp_no_pin_adds_nothing():
    from cockpit.llm.v1 import llm_pb2

    from agents._sdk._ctx import set_current_meta
    from agents._sdk.clients import _stamp_obs_meta

    set_current_meta({})
    try:
        req = llm_pb2.CompleteRequest()
        _stamp_obs_meta(req)
        assert "llm_provider" not in req.meta
    finally:
        set_current_meta(None)
