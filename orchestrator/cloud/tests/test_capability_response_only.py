from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus


class _Resp:
    def __init__(self, *, status=0, actions=None, data=None):
        self.status = status
        self.speech = ""
        self.actions = list(actions or [])
        self.data = data
        self.ui_card = None
        self.follow_up = ""
        self.missing_slots = []


def _action():
    return SimpleNamespace(
        type="external.write",
        payload={},
        require_confirm=False,
    )


def _response_step(*, require_confirm=False, verification=None):
    step = Step(
        id="s1",
        agent_id="answer",
        intent="answer.render",
        require_confirm=require_confirm,
        verification=dict(verification or {}),
    )
    step.response_only = True
    return step


def _run(step, *responses):
    queue = list(responses)

    async def call(*_args, **_kwargs):
        return queue.pop(0)

    executor = DagExecutor(call_agent_fn=call)

    async def collect():
        return [result async for result in executor.run(Plan(steps=[step]), None)]

    return asyncio.run(collect())


@pytest.mark.parametrize("status", [1, 2])
def test_pending_response_is_failed_closed(status):
    result = _run(_response_step(), _Resp(status=status))[0]
    assert result.status == StepStatus.FAILED
    assert result.actions == []
    assert result.error == "response_only_contract_violation"


def test_malicious_action_is_failed_closed():
    result = _run(_response_step(), _Resp(status=0, actions=[_action()]))[0]
    assert result.status == StepStatus.FAILED
    assert result.actions == []
    assert result.error == "response_only_contract_violation"


def test_response_only_confirm_configuration_never_becomes_need_confirm():
    calls = 0

    async def call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Resp(status=0)

    executor = DagExecutor(call_agent_fn=call)
    step = _response_step(require_confirm=True)

    async def collect():
        return [result async for result in executor.run(Plan(steps=[step]), None)]

    result = asyncio.run(collect())[0]
    assert calls == 0, "static response_only/confirm conflict must fail before dispatch"
    assert result.status == StepStatus.FAILED
    assert result.actions == []
    assert result.error == "response_only_contract_violation"


def test_zero_action_failure_and_escalate_control_are_preserved():
    failure = StepResult("s1", StepStatus.FAILED, error="provider_down")
    assert DagExecutor._enforce_response_only(_response_step(), failure) is failure

    esc = StepResult(
        "s1",
        StepStatus.OK,
        data={"_escalate": {"intent": "info.search", "slots": {}}},
    )
    assert DagExecutor._enforce_response_only(_response_step(), esc) is esc


def test_verifier_retry_reapplies_response_only_gate():
    calls = 0
    responses = [_Resp(status=0), _Resp(status=0, actions=[_action()])]

    async def call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return responses.pop(0)

    executor = DagExecutor(call_agent_fn=call)

    async def unsat(*_args, **_kwargs):
        return "unsat"

    executor._evaluate = unsat
    step = _response_step(
        verification={
            "mode": "schema",
            "on_fail": "retry",
            "max_attempts": 1,
        }
    )

    async def collect():
        return [result async for result in executor.run(Plan(steps=[step]), None)]

    result = asyncio.run(collect())[0]
    assert calls == 2
    assert result.status == StepStatus.FAILED
    assert result.actions == []
    assert result.error == "response_only_contract_violation"


def test_verifier_retry_preflight_rejects_static_conflict_without_dispatch(
    monkeypatch,
):
    from orchestrator.cloud import executor as executor_module

    calls = 0

    async def call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Resp(status=0)

    executor = DagExecutor(call_agent_fn=call)

    async def unsat(*_args, **_kwargs):
        return "unsat"

    executor._evaluate = unsat
    monkeypatch.setattr(
        executor_module._verify,
        "retry_allowed",
        lambda _verification, _confirm, attempts: attempts < 1,
    )
    step = _response_step(
        require_confirm=True,
        verification={
            "mode": "schema",
            "on_fail": "retry",
            "max_attempts": 1,
        },
    )
    initial = StepResult("s1", StepStatus.OK)
    result = asyncio.run(executor._verify_outcome(step, initial, None))

    assert calls == 0
    assert result.status == StepStatus.FAILED
    assert result.actions == []
    assert result.error == "response_only_contract_violation"
