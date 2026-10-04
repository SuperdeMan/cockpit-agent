"""Jev 判别服务（JV01）：缺省零外呼、allowlist、payload 拒绝、错误码映射、请求形状、预算、整批作废、用量。"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import httpx

_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _DIR)
from cockpit.llm.v1 import llm_pb2  # noqa: E402
from google.protobuf.struct_pb2 import Struct  # noqa: E402

from decision_provider import DecisionProviderError, FakeDecisionProvider, TypeSafeDecisionProvider  # noqa: E402
from decision_service import DecisionService  # noqa: E402
from runtime import decision_contract as dc  # noqa: E402

S = llm_pb2


def _task(task_id="smoke", version="1", **payload):
    struct = Struct()
    struct.update(payload or {"text": "打开空调"})
    return S.DecisionTask(task_id=task_id, rubric_version=version, payload=struct)


def _request(*tasks, budget_ms=0):
    return S.DecideRequest(request_id="req-1", binding=S.DecisionBinding(state_fingerprint="fp-1"),
                           tasks=list(tasks) or [_task()], budget_ms=budget_ms)


def _run(service, request=None):
    return asyncio.run(service.decide(request or _request()))


def _service(provider=None, **kw):
    kw.setdefault("enabled", True)
    kw.setdefault("tasks", ("smoke",))
    return DecisionService(provider or FakeDecisionProvider(), **kw)


def test_off_by_default_makes_no_call(monkeypatch):
    for key in ("DECISION_ENABLED", "DECISION_TASKS", "TYPESAFE_API_KEY", "DECISION_MODEL"):
        monkeypatch.delenv(key, raising=False)
    service = DecisionService.from_env()
    assert service.enabled is False and service.model == "jev-1.13.0" and service.tasks == frozenset()
    fake = FakeDecisionProvider()
    res = _run(DecisionService(fake))
    assert [r.status for r in res.results] == [S.DECISION_STATUS_DISABLED] and fake.calls == []
    assert res.results[0].reason_code == "global_off" and res.state_fingerprint == "fp-1"


def test_task_gates_run_before_any_call():
    fake = FakeDecisionProvider()
    res = _run(_service(fake, tasks=()), _request(_task(), _task("nope"), _task(text="x", owner="u1")))
    assert [(r.status, r.reason_code) for r in res.results] == [
        (S.DECISION_STATUS_DISABLED, "task_off"), (S.DECISION_STATUS_INVALID_REQUEST, "unknown_task"),
        (S.DECISION_STATUS_DISABLED, "task_off")]
    res = _run(_service(fake), _request(_task(text="x", owner="u1"), _task(text=3)))
    assert [(r.status, r.reason_code) for r in res.results] == [
        (S.DECISION_STATUS_INVALID_REQUEST, "unknown_field:owner"), (S.DECISION_STATUS_INVALID_REQUEST, "bad_field:text")]
    assert fake.calls == []


def test_ok_path_returns_typed_answers_and_usage():
    fake = FakeDecisionProvider()
    res = _run(_service(fake))
    (result,) = res.results
    assert result.status == S.DECISION_STATUS_OK and result.answers[0].question_id == "asks_action"
    assert result.answers[0].WhichOneof("value") == "noul" and result.answers[0].noul.p_true == 0.5
    assert res.model_requested == res.model_used == "jev-1.13.0"
    assert res.usage.known and res.usage.input_tokens == 100
    assert fake.calls[0]["state"] == {"text": "打开空调"} and set(fake.calls[0]["questions"]) == {"asks_action"}


def test_budget_is_capped_by_the_server():
    fake = FakeDecisionProvider()
    _run(_service(fake, max_budget_ms=800), _request(budget_ms=5000))
    _run(_service(fake, max_budget_ms=800), _request(budget_ms=200))
    _run(_service(fake, max_budget_ms=800), _request(budget_ms=0))
    assert [c["timeout_s"] for c in fake.calls] == [0.8, 0.2, 0.8]


def test_a_slow_vendor_times_out_within_the_budget():
    class Slow(FakeDecisionProvider):
        async def decide(self, **kw):
            await asyncio.sleep(1.0)
            return await super().decide(**kw)
    res = _run(_service(Slow(), max_budget_ms=50))
    assert res.results[0].status == S.DECISION_STATUS_TIMEOUT and not res.results[0].answers


def test_invalid_answers_drop_the_whole_batch_and_unknown_usage_stays_unknown():
    for answers in ({}, {"asks_action": {"type": "noul", "noul": float("nan")}},
                    {"asks_action": {"type": "noul", "noul": 0.4}, "extra": {"type": "noul", "noul": 0.1}}):
        res = _run(_service(FakeDecisionProvider(answers=answers)))
        assert res.results[0].status == S.DECISION_STATUS_INVALID_RESPONSE and not res.results[0].answers
    res = _run(_service(FakeDecisionProvider(model="jev-1.14.0")))
    assert (res.results[0].status, res.results[0].reason_code) == (S.DECISION_STATUS_INVALID_RESPONSE, "model_drift")
    res = _run(_service(FakeDecisionProvider(usage={})))
    assert res.results[0].status == S.DECISION_STATUS_OK and res.usage.known is False


def test_provider_errors_map_to_statuses_without_retrying():
    for error, status in ((DecisionProviderError(dc.UNAVAILABLE, "rate_limited"), S.DECISION_STATUS_UNAVAILABLE),
                          (DecisionProviderError(dc.TIMEOUT, "timeout"), S.DECISION_STATUS_TIMEOUT),
                          (DecisionProviderError(dc.INVALID_RESPONSE, "bad_json"), S.DECISION_STATUS_INVALID_RESPONSE)):
        fake = FakeDecisionProvider(error=error)
        res = _run(_service(fake))
        assert res.results[0].status == status and len(fake.calls) == 1 and res.usage.known is False


def _typesafe(handler, key="test-key"):
    return TypeSafeDecisionProvider(key, "https://vendor.test", transport=httpx.MockTransport(handler))


def _vendor_call(provider):
    return asyncio.run(provider.decide(model="jev-1.13.0", state={"text": "x"},
                                       questions={"q": {"type": "noul", "instructions": "i"}}, timeout_s=1.0))


def test_the_vendor_request_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 1}})
    _vendor_call(_typesafe(handler))
    (req,) = seen
    assert req.method == "POST" and str(req.url) == "https://vendor.test/v1/systemone"
    assert req.headers["Authorization"] == "Bearer test-key"
    assert json.loads(req.content) == {"model": "jev-1.13.0", "state": {"text": "x"},
                                       "questions": {"q": {"type": "noul", "instructions": "i"}}}


def test_vendor_failures_are_classified():
    cases = [(401, "auth", dc.UNAVAILABLE), (403, "auth", dc.UNAVAILABLE), (422, "vendor_rejected", dc.INVALID_REQUEST),
             (429, "rate_limited", dc.UNAVAILABLE), (529, "overloaded", dc.UNAVAILABLE), (500, "http_500", dc.UNAVAILABLE)]
    for code, reason, status in cases:
        try:
            _vendor_call(_typesafe(lambda request, code=code: httpx.Response(code, json={"error": "x"})))
        except DecisionProviderError as e:
            assert (e.status, e.reason) == (status, reason), code
        else:
            raise AssertionError(code)
    for handler, reason, status in (
            (lambda request: httpx.Response(200, content=b"not json"), "bad_json", dc.INVALID_RESPONSE),
            (lambda request: httpx.Response(200, json=[1, 2]), "bad_json", dc.INVALID_RESPONSE)):
        try:
            _vendor_call(_typesafe(handler))
        except DecisionProviderError as e:
            assert (e.status, e.reason) == (status, reason)
        else:
            raise AssertionError(reason)

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def network(request):
        raise httpx.ConnectError("down", request=request)
    for handler, reason, status in ((timeout, "timeout", dc.TIMEOUT), (network, "network", dc.UNAVAILABLE)):
        try:
            _vendor_call(_typesafe(handler))
        except DecisionProviderError as e:
            assert (e.status, e.reason) == (status, reason)
        else:
            raise AssertionError(reason)


def test_no_credential_never_reaches_the_network():
    def handler(request):
        raise AssertionError("must not be called")
    try:
        _vendor_call(_typesafe(handler, key=""))
    except DecisionProviderError as e:
        assert (e.status, e.reason) == (dc.UNAVAILABLE, "no_credential")
    else:
        raise AssertionError("no_credential")


def test_the_servicer_routes_decide_to_the_service():
    from server import LLMGatewayServicer
    fake = FakeDecisionProvider()
    res = asyncio.run(LLMGatewayServicer.Decide(SimpleNamespace(decision=_service(fake)), _request(), None))
    assert res.results[0].status == S.DECISION_STATUS_OK and len(fake.calls) == 1
