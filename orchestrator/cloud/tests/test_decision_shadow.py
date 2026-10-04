"""Jev 受话 shadow（JV03）：缺省关闭；只对合成会话（数据策略确认前）；只观测、不改结果；span 不带原话。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cockpit.llm.v1 import llm_pb2

from orchestrator.cloud.decision_support import ShadowRunner, synthetic_session
from orchestrator.cloud.models import Plan

from .test_engine_confirm import _make_engine, _req, _run

SYNTH = SimpleNamespace(trace_id="t1", session_id="s1", request_id="r1", user_id="e2e-run-1", e2e_memory_capability="")
REAL = SimpleNamespace(trace_id="t2", session_id="s2", request_id="r2", user_id="u1", e2e_memory_capability="")


class _Emitter:
    def __init__(self):
        self.spans = []

    async def emit_span(self, trace_id, node, status="ok", duration_ms=0, attrs=None, **kw):
        self.spans.append({"trace_id": trace_id, "node": node, "status": status, "attrs": dict(attrs or {})})


class _Clients:
    def __init__(self, p_true=0.92, error=None, delay=0.0):
        self.p_true, self.error, self.delay, self.requests = p_true, error, delay, []

    async def decide(self, request, timeout):
        self.requests.append((request, timeout))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        response = llm_pb2.DecideResponse(request_id=request.request_id, model_used="jev-1.13.0")
        result = response.results.add(task_id="addressed", rubric_version="1", status=llm_pb2.DECISION_STATUS_OK)
        answer = result.answers.add(question_id="addressed")
        answer.noul.p_true = self.p_true
        response.usage.known = True
        response.usage.input_tokens = 360
        return response


def _shadow(monkeypatch, clients, *, mode="shadow", scope="", cases=((SYNTH, "打开空调", True),)):
    monkeypatch.setenv("DECISION_ADDRESSED_MODE", mode)
    if scope:
        monkeypatch.setenv("DECISION_SHADOW_SCOPE", scope)
    else:
        monkeypatch.delenv("DECISION_SHADOW_SCOPE", raising=False)
    emitter = _Emitter()
    runner = ShadowRunner(clients, emitter=emitter)

    async def go():
        verdicts = [runner.schedule_addressed(ctx, text, planner_addressed=addressed, voice=True)
                    for ctx, text, addressed in cases]
        while runner._inflight:
            await asyncio.gather(*list(runner._inflight))
        return verdicts
    return asyncio.run(go()), emitter.spans


def test_off_by_default_and_out_of_scope_sessions_make_no_call(monkeypatch):
    monkeypatch.delenv("DECISION_ADDRESSED_MODE", raising=False)
    clients = _Clients()
    runner = ShadowRunner(clients, emitter=_Emitter())
    assert runner.schedule_addressed(SYNTH, "打开空调", planner_addressed=True, voice=True) == "skipped:off"
    verdicts, spans = _shadow(monkeypatch, clients, cases=((REAL, "打开空调", True),))
    assert verdicts == ["skipped:scope"] and clients.requests == [] and spans == []
    assert synthetic_session(SYNTH) and not synthetic_session(REAL)
    assert synthetic_session(SimpleNamespace(user_id="u1", e2e_memory_capability="cap"))


def test_a_synthetic_turn_is_compared_with_the_planner_and_no_text_is_recorded(monkeypatch):
    clients = _Clients(p_true=0.12)
    verdicts, spans = _shadow(monkeypatch, clients, cases=((SYNTH, "我跟你说过多少次了", True),))
    assert verdicts == ["scheduled"]
    (request, timeout), = clients.requests
    assert request.tasks[0].task_id == "addressed" and request.tasks[0].rubric_version == "1"
    assert dict(request.tasks[0].payload) == {"utterance": "我跟你说过多少次了"}
    assert request.budget_ms == 1500 and timeout == 2.0 and request.binding.exchange_id == "r1"
    (span,) = spans
    assert span["node"] == "decision.shadow" and span["trace_id"] == "t1"
    attrs = span["attrs"]
    assert attrs["status"] == "DECISION_STATUS_OK" and attrs["p_true"] == 0.12
    assert attrs["planner_addressed"] is True and attrs["agree"] is False and attrs["input_tokens"] == 360
    assert "我跟你说过" not in repr(attrs)


def test_scope_all_includes_real_sessions(monkeypatch):
    verdicts, spans = _shadow(monkeypatch, _Clients(), scope="all", cases=((REAL, "打开空调", True),))
    assert verdicts == ["scheduled"] and spans[0]["attrs"]["agree"] is True


def test_failures_are_recorded_not_raised(monkeypatch):
    verdicts, spans = _shadow(monkeypatch, _Clients(error=RuntimeError("down")))
    assert verdicts == ["scheduled"] and spans[0]["status"] == "err"
    assert spans[0]["attrs"]["status"] == "RPC_ERROR" and spans[0]["attrs"]["reason"] == "RuntimeError"


def test_the_inflight_cap_drops_instead_of_queueing(monkeypatch):
    monkeypatch.setenv("DECISION_SHADOW_MAX_INFLIGHT", "1")
    clients = _Clients(delay=0.05)
    verdicts, spans = _shadow(monkeypatch, clients, cases=((SYNTH, "打开空调", True), (SYNTH, "关闭空调", True)))
    assert verdicts == ["scheduled", "dropped"] and len(clients.requests) == 1
    assert sorted(s["attrs"]["status"] for s in spans) == ["DECISION_STATUS_OK", "DROPPED"]


def test_long_or_empty_text_is_skipped(monkeypatch):
    verdicts, _ = _shadow(monkeypatch, _Clients(), cases=((SYNTH, "", True), (SYNTH, "长" * 501, True)))
    assert verdicts == ["skipped:text", "skipped:text"]


def test_the_engine_schedules_the_shadow_with_the_planners_verdict(monkeypatch):
    """接线：规划之后调度一次，带规划器自己的 addressed 与是否语音来源；结果不影响这一轮。"""
    engine, _spy, _session = _make_engine()
    calls = []

    class _Recorder:
        def schedule_addressed(self, ctx, text, *, planner_addressed, voice):
            calls.append((text, planner_addressed, voice))
            return "scheduled"
    engine._decision_shadow = _Recorder()

    async def build(text, working_set, ctx, **kwargs):
        return Plan(addressed=False, steps=[])
    engine.planner.build = build
    req = _req("他们刚才说什么来着")
    req.meta = {"input_source": "voice_wake"}
    final = _run(engine, req)[-1]
    assert calls == [("他们刚才说什么来着", False, True)]
    assert final.get("ui_card", {}).get("type") == "rejected"          # 结果照旧由规划器决定
